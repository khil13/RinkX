"""Model stage: test the models (daily), then project upcoming games (every run).

* The walk-forward test (fit.evaluate) runs when the last one is older than EVAL_MAX_AGE or
  the model version changed. Its report is stored in backtest_runs / model_versions.
* Only stats that passed their test are projected. Others are listed on the Models page
  with the reason, and nothing is shown for them.
* Projections are immutable rows: a change inserts a new row that supersedes the old one,
  tagged with why (scheduled refresh, goalie confirmed, player out).
"""

from __future__ import annotations

import json
import logging
import math
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

import numpy as np

from rinkx.ingestion.injuries import espn as injuries
from rinkx.ingestion.runs import SourceSpec, ingestion_run, register_source
from rinkx.models import dist, fit, game_sim, history
from rinkx.models.fit import FAMILY, LABELS, MARKETS, MODEL_VERSION, Choice
from rinkx.models.game_sim import GameChoice
from rinkx.models.state import HALF_LIVES, K_SV, State, basis_of, walk
from rinkx.timeutil import iso, parse_iso

log = logging.getLogger("rinkx.models")

MODELS_SOURCE = SourceSpec(
    "rinkx_models",
    "RinkX models (derived from NHL data)",
    "models",
    "free",
    "",
    "Derived in-house from official NHL data; no third-party content.",
    1.0,
)
EVAL_MAX_AGE = timedelta(hours=20)
PROJECT_DAYS_AHEAD = 2  # today and the next two days
KEEP_DAYS = 14  # projections for older games are pruned
GOALIE_LOOKBACK = 10  # team games used to project the starter
ALGORITHMS = {
    "skater_shots": "Empirical-Bayes shot rate x expected ice time; Poisson/NB",
    "skater_scoring": "Empirical-Bayes scoring rates (goals: shots x shrunk finishing) x ice time; Poisson/NB",
    "skater_blocks": "Empirical-Bayes block rate x ice time; Poisson/NB",
    "skater_hits": "Empirical-Bayes hit rate x ice time x arena factor; Poisson/NB",
    "goalie": "Shots against NB x shrunk save % (binomial mixture)",
    "game_sim": "Team expected goals (shots x finishing x goalie) with OT/shootout rules; closed form",
}


# ---- registry -------------------------------------------------------------------------------


def _model_version_id(conn: sqlite3.Connection, family: str) -> int:
    row = conn.execute(
        "SELECT id FROM model_versions WHERE model_family = ? AND version = ?", (family, MODEL_VERSION)
    ).fetchone()
    if row:
        return int(row[0])
    cur = conn.execute(
        "INSERT INTO model_versions (model_family, version, algorithm, feature_list) VALUES (?, ?, ?, ?)",
        (family, MODEL_VERSION, ALGORITHMS[family], json.dumps(sorted(set(sum(fit.SKATER_FACTORS.values(), ()))))),
    )
    return int(cur.lastrowid or 0)


def record_evaluation(conn: sqlite3.Connection, report: dict[str, Any], now: datetime) -> None:
    for family in sorted(set(FAMILY.values())):
        mv = _model_version_id(conn, family)
        stats = {s: e for s, e in report["stats"].items() if e["family"] == family}
        # The game model's shared settings are stored under "game" with the game_sim family.
        choices = {s: c for s, c in report["choices"].items() if s in stats or (s == "game" and family == "game_sim")}
        passed = any(e.get("passed") for e in stats.values())
        test = report.get("test") or {}
        tune = report.get("tune") or {}
        conn.execute(
            "INSERT INTO backtest_runs (model_version_id, scheme, lineup_mode, period_start, period_end, config, "
            "metrics, created_at) VALUES (?, 'walk_forward', 'lineup_oracle', ?, ?, ?, ?, ?)",
            (
                mv,
                test.get("from") or "",
                test.get("to") or "",
                json.dumps(
                    {
                        "tune": tune,
                        "history": report.get("history"),
                        "half_lives": HALF_LIVES,
                        "k_sv": K_SV,
                        "m_grid": fit.M_GRID,
                        "burn_in_days": fit.BURN_IN_DAYS,
                        "n_games": report.get("n_games"),
                    }
                ),
                json.dumps({"status": report["status"], "stats": stats, "choices": choices}),
                iso(now),
            ),
        )
        if passed:
            conn.execute(
                "UPDATE model_versions SET status = 'retired' WHERE model_family = ? AND status = 'champion' "
                "AND id <> ?",
                (family, mv),
            )
        conn.execute(
            "UPDATE model_versions SET status = ?, hyperparams = ?, oos_metrics = ?, train_start = ?, train_end = ?, "
            "promoted_at = CASE WHEN ? AND status <> 'champion' THEN ? ELSE promoted_at END WHERE id = ?",
            (
                "champion" if passed else "candidate",
                json.dumps(choices),
                json.dumps(stats),
                tune.get("from"),
                tune.get("to_before"),
                passed,
                iso(now),
                mv,
            ),
        )


def latest_report(conn: sqlite3.Connection) -> tuple[dict[str, Any] | None, str | None]:
    """The most recent stored test of the current model version, merged across families."""
    rows = conn.execute(
        "SELECT b.metrics, b.config, b.period_start, b.period_end, b.created_at FROM backtest_runs b "
        "JOIN model_versions m ON m.id = b.model_version_id WHERE m.version = ? AND b.created_at = ("
        "SELECT max(b2.created_at) FROM backtest_runs b2 JOIN model_versions m2 ON m2.id = b2.model_version_id "
        "WHERE m2.version = ?)",
        (MODEL_VERSION, MODEL_VERSION),
    ).fetchall()
    if not rows:
        return None, None
    report: dict[str, Any] = {"version": MODEL_VERSION, "stats": {}, "choices": {}}
    for r in rows:
        m, cfg = json.loads(r[0]), json.loads(r[1])
        report["status"] = m["status"]
        report["stats"].update(m["stats"])
        report["choices"].update(m["choices"])
        report["tune"] = cfg.get("tune")
        report["history"] = cfg.get("history")
        report["n_games"] = cfg.get("n_games")
        report["test"] = {"from": r[2], "to": r[3]} if r[2] else None
    return report, rows[0][4]


# ---- availability ---------------------------------------------------------------------------


@dataclass
class GoalieMix:
    mix: list[tuple[int, float]]  # (players.id, start probability)
    status: str  # 'confirmed' | 'projected' | 'unknown'
    source_ref: str | None = None
    reported_at: str | None = None


def goalie_mix(conn: sqlite3.Connection, game_id: int, team_id: int) -> GoalieMix:
    conf = conn.execute(
        "SELECT player_id, status, source_ref, reported_at FROM goalie_starts WHERE game_id = ? AND team_id = ? "
        "AND status IN ('confirmed','likely') ORDER BY reported_at DESC, id DESC LIMIT 1",
        (game_id, team_id),
    ).fetchone()
    if conf:
        return GoalieMix([(conf[0], 1.0)], "confirmed", conf[2], conf[3])
    hurt = {pid for pid, r in injured(conn, game_id).items() if r["status"] in injuries.OUT_STATUSES}
    roster = [
        r[0]
        for r in conn.execute(
            "SELECT id FROM players WHERE current_team_id = ? AND position = 'G' AND is_active = 1", (team_id,)
        )
        if r[0] not in hurt  # a goalie on the injury report can't be the projected starter
    ]
    if not roster:
        return GoalieMix([], "unknown")
    recent = conn.execute(
        "SELECT s.player_id FROM goalie_game_stats s JOIN games g ON g.id = s.game_id WHERE s.team_id = ? "
        "AND s.started = 1 AND g.status = 'final' ORDER BY g.start_time_utc DESC LIMIT ?",
        (team_id, GOALIE_LOOKBACK),
    ).fetchall()
    counts = {g: 0 for g in roster}
    for (g,) in recent:
        if g in counts:
            counts[g] += 1
    total = sum(counts.values())
    if total == 0:
        return GoalieMix([(g, 1 / len(roster)) for g in roster], "projected")
    mix = sorted(((g, c / total) for g, c in counts.items() if c), key=lambda t: -t[1])
    return GoalieMix(mix, "projected")


INJURY_FRESH = timedelta(hours=48)  # an injury report checked longer before the game than this is ignored


def injury_report_fresh(conn: sqlite3.Connection, game_id: int) -> bool:
    """Whether the injury report was checked recently enough (relative to the game) to rely on."""
    start = conn.execute("SELECT start_time_utc FROM games WHERE id = ?", (game_id,)).fetchone()[0]
    last = conn.execute(injuries.LAST_CHECK_SQL, (injuries.SOURCE.code,)).fetchone()
    return last is not None and last[0] is not None and parse_iso(start) - parse_iso(last[0]) <= INJURY_FRESH


def injured(conn: sqlite3.Connection, game_id: int) -> dict[int, sqlite3.Row]:
    """Active injury-report entries for players on either team, if the report is fresh."""
    if not injury_report_fresh(conn, game_id):
        return {}
    rows = conn.execute(
        "SELECT i.player_id, i.status, i.description, i.source_ref, i.reported_at FROM injuries i "
        "JOIN games g ON g.id = ? WHERE i.is_active = 1 AND i.team_id IN (g.home_team_id, g.away_team_id)",
        (game_id,),
    ).fetchall()
    return {r["player_id"]: r for r in rows}


def players_out(conn: sqlite3.Connection, game_id: int) -> dict[int, dict[str, Any]]:
    """Players who won't play: ruled out by Quick Entry, or out / on IR / suspended on the injury
    report. A Quick Entry "back in" for this game overrides the report."""
    out: dict[int, dict[str, Any]] = {}
    manual: dict[int, str] = {}
    for r in conn.execute(
        "SELECT a.player_id, a.status, a.reason, a.source_ref, a.reported_at FROM game_availability a "
        "WHERE a.game_id = ? AND a.id = (SELECT a2.id FROM game_availability a2 WHERE a2.game_id = a.game_id "
        "AND a2.player_id = a.player_id ORDER BY a2.reported_at DESC, a2.id DESC LIMIT 1)",
        (game_id,),
    ):
        manual[r[0]] = r[1]
        if r[1] == "out":
            out[r[0]] = {"reason": r[2], "source": r[3], "reported_at": r[4]}
    for pid, r in injured(conn, game_id).items():
        if r["status"] in injuries.OUT_STATUSES and manual.get(pid) != "available" and pid not in out:
            out[pid] = {
                "reason": f"injury report: {r['status'].replace('_', ' ')}",
                "source": r["source_ref"],
                "reported_at": r["reported_at"],
            }
    return out


# ---- explain --------------------------------------------------------------------------------


def _mmss(hours: float) -> str:
    s = round(hours * 3600)
    return f"{s // 60}:{s % 60:02d}"


@dataclass
class Explained:
    factors: list[dict[str, Any]]
    inputs: dict[str, Any]


def _one(f: dict[str, float]) -> fit.Cols:
    return {k: np.array([v], dtype=float) for k, v in f.items()}


def explain_skater(f: dict[str, float], ch: Choice, shots: Choice | None, ctx: dict[str, Any]) -> Explained:
    """Decompose the projected mean into reference x factors (each shown as a % effect)."""
    stat, pos = ch.stat, ctx["pos"]
    c = _one(f)
    factors: list[dict[str, Any]] = []
    i = ch.hl
    label = LABELS[stat].lower()
    if ch.kind == "finish":
        assert shots is not None
        sog = float(fit.skater_mean(c, shots, None)[0])
        ref_sog = f["prior.shots"] * f["pos_toi"]
        fin = float(fit.finish(c, i, ch.m)[0])
        own = f[f"x.shots.{i}"] / (f[f"x.shots.{i}"] + ch.m)
        factors.append(
            {
                "name": "Shot volume",
                "effect": sog / ref_sog - 1 if ref_sog else 0.0,
                "detail": f"Projects {sog:.2f} shots vs {ref_sog:.2f} for an average {pos}.",
            }
        )
        factors.append(
            {
                "name": "Finishing",
                "effect": fin / f["prior_finish"] - 1 if f["prior_finish"] else 0.0,
                "detail": f"Scores on {fin:.1%} of shots (his record weighted {own:.0%}, league average "
                f"{f['prior_finish']:.1%} the rest; shooting % is mostly noise in small samples).",
            }
        )
        reference = ref_sog * f["prior_finish"]
    else:
        basis = basis_of(stat)
        eb = f[f"eb.{basis}.{i}"]
        pos_b = f["pos_pp"] if basis == "pp" else f["pos_toi"]
        r = float(fit.rate(c, stat, i, ch.m)[0])
        prior = f[f"prior.{stat}"]
        own = f[f"b.{stat}.{i}"] / (f[f"b.{stat}.{i}"] + ch.m)
        what = "power-play time" if basis == "pp" else "ice time"
        factors.append(
            {
                "name": "Power-play time" if basis == "pp" else "Ice time",
                "effect": eb / pos_b - 1 if pos_b else 0.0,
                "detail": f"Expected {_mmss(eb)} of {what} per game vs {_mmss(pos_b)} for an average {pos}.",
            }
        )
        factors.append(
            {
                "name": "His rate",
                "effect": r / prior - 1 if prior else 0.0,
                "detail": f"{r:.2f} {label} per 60 min{' of PP' if basis == 'pp' else ''} vs {prior:.2f} "
                f"for an average {pos}: his record weighted {own:.0%}, league average the rest.",
            }
        )
        reference = prior * pos_b
    for name in ch.factors:
        v = float(fit.factor(c, stat, name, ch)[0])
        factors.append(_context_factor(name, v, stat, ctx))
    inputs = {
        "games_in_history": int(f["games"]),
        "half_life_games": HALF_LIVES[ch.hl],
        "prior_strength": ch.m,
        "prior_strength_unit": "shots" if ch.kind == "finish" else ("PP hours" if basis_of(stat) == "pp" else "hours"),
        "distribution": "Poisson" if math.isinf(ch.size) else f"Negative binomial (size {ch.size:g})",
        "reference_mean": round(reference, 5),
        "expected_toi_s": round(f[f"eb.toi.{i}"] * 3600),
        "expected_pp_toi_s": round(f[f"eb.pp.{i}"] * 3600),
    }
    return Explained(factors, inputs)


def _pct(v: float) -> str:
    pct = round((v - 1) * 100)
    return "0%" if pct == 0 else f"{pct:+d}%"


def _context_factor(name: str, v: float, stat: str, ctx: dict[str, Any]) -> dict[str, Any]:
    label = LABELS[stat].lower()
    if name == "opp":
        return {
            "name": "Opponent",
            "effect": v - 1,
            "detail": f"{ctx['opp']} allows {_pct(v)} {label} vs the league average (recent games, shrunk).",
        }
    if name == "opp_shots":
        return {
            "name": "Opponent",
            "effect": v - 1,
            "detail": f"{ctx['opp']} allows {_pct(v)} shots vs the league average (recent games, shrunk).",
        }
    if name == "goalie":
        return {"name": "Opposing goalie", "effect": v - 1, "detail": ctx["goalie_text"]}
    if name == "home":
        return {
            "name": "Home ice" if ctx["home"] else "Road game",
            "effect": v - 1,
            "detail": f"League-wide, {label} run {_pct(v)} {'at home' if ctx['home'] else 'on the road'}.",
        }
    if name == "playoff":
        return {
            "name": "Playoff game",
            "effect": v - 1,
            "detail": f"League-wide, {label} run {_pct(v)} in playoff games vs the regular season (shrunk).",
        }
    if name == "venue":
        return {
            "name": "Arena scorekeeping",
            "effect": v - 1,
            "detail": f"{ctx['arena'] or 'This arena'} records {_pct(v)} {label} vs the home team's road games "
            "(scorekeepers differ; shrunk).",
        }
    raise KeyError(name)


def explain_goalie(f: dict[str, float], ch: Choice, ctx: dict[str, Any]) -> Explained:
    c = _one(f)
    sv = f[f"sv.{ch.k_sv}"]
    lsv = f["league_sv"]
    mu = float(fit.goalie_sa_mean(c, ch)[0])
    factors: list[dict[str, Any]] = []
    for name in ch.factors:
        v = f[name]
        if name == "fd":
            factors.append(
                {
                    "name": "His team's defence",
                    "effect": v - 1,
                    "detail": f"{ctx['team']} allows {_pct(v)} shots vs the league average.",
                }
            )
        elif name == "po":
            factors.append(
                {
                    "name": "Playoff game",
                    "effect": v - 1,
                    "detail": f"League-wide, teams take {_pct(v)} shots in playoff games vs the regular season.",
                }
            )
        else:
            factors.append(
                {
                    "name": "Opponent's offence",
                    "effect": v - 1,
                    "detail": f"{ctx['opp']} takes {_pct(v)} shots vs the league average.",
                }
            )
    talent = sv / lsv if ch.stat == "saves" else (1 - sv) / (1 - lsv)
    factors.append(
        {
            "name": "Save percentage",
            "effect": talent - 1,
            "detail": f"Expected save % {sv:.3f} vs league {lsv:.3f} (his record shrunk toward the league "
            f"with {K_SV[ch.k_sv]:.0f} shots of prior weight).",
        }
    )
    p = sv if ch.stat == "saves" else 1 - sv
    inputs = {
        "starts_in_history": int(f["starts"]),
        "expected_shots_against": round(mu, 2),
        "expected_save_pct": round(sv, 4),
        "league_save_pct": round(lsv, 4),
        "distribution": "Shots against "
        + ("Poisson" if math.isinf(ch.sa_size) else f"negative binomial (size {ch.sa_size:g})")
        + " x binomial saves",
        "reference_mean": round(f["mu_league"] * (lsv if ch.stat == "saves" else 1 - lsv), 3),
        "projected_mean_check": round(mu * p, 3),
    }
    return Explained(factors, inputs)


def env_factors(gf: dict[str, float], ch: GameChoice, side: str, team: str, opp: str, home: bool) -> Explained:
    """Team expected goals = league goals per game x the factors below (exact product)."""
    reference = gf[f"{side}.S"] / (gf[f"{side}.ff"] * gf[f"{side}.fa"]) * gf["gL"]
    f = [
        {
            "name": "Shot volume",
            "effect": gf[f"{side}.ff"] - 1,
            "detail": f"{team} takes {_pct(gf[f'{side}.ff'])} shots vs the league average (recent games, shrunk).",
        },
        {
            "name": "Opponent defence",
            "effect": gf[f"{side}.fa"] - 1,
            "detail": f"{opp} allows {_pct(gf[f'{side}.fa'])} shots vs the league average.",
        },
    ]
    if "fin" in ch.factors:
        v = gf[f"{side}.fin.{ch.fin}"]
        f.append(
            {
                "name": "Finishing",
                "effect": v - 1,
                "detail": f"{team} scores on {_pct(v)} of shots vs the league (shrunk; mostly noise short-term).",
            }
        )
    if "goalie" in ch.factors:
        v = gf[f"{side}.gf.{ch.k_sv}"]
        f.append(
            {
                "name": "Opposing goalie",
                "effect": v - 1,
                "detail": f"{opp}'s expected starter allows {_pct(v)} goals per shot vs the league.",
            }
        )
    if "home" in ch.factors:
        v = gf[f"{side}.home"]
        f.append(
            {
                "name": "Home ice" if home else "Road game",
                "effect": v - 1,
                "detail": f"League-wide, scoring runs {_pct(v)} {'at home' if home else 'on the road'}.",
            }
        )
    return Explained(f, {"reference_mean": round(reference, 5)})


@dataclass
class GameProjection:
    market: str
    side: str
    mean: float
    pmf: list[float]
    inputs: dict[str, Any]
    factors: list[dict[str, Any]]


# ---- projecting -----------------------------------------------------------------------------


@dataclass
class Projection:
    player: int
    market: str
    stat: str
    pmf: list[float]
    mean: float
    median: float
    sd: float
    factors: list[dict[str, Any]]
    inputs: dict[str, Any]
    data_quality: float
    missing: list[str] = field(default_factory=list)
    expected_toi_s: int | None = None
    expected_pp_toi_s: int | None = None


def _pmf_json(p: np.ndarray) -> list[float]:
    return [round(float(x), 5) for x in p]


def _markets_for(stat: str) -> list[str]:
    return [m for m, s in MARKETS.items() if s == stat]


def _quality(games: float, missing: list[str]) -> float:
    q = 1.0
    if games < 10:
        q -= 0.25
    elif games < 30:
        q -= 0.1
    q -= 0.1 * sum(m in ("goalie_unconfirmed", "lineup_unconfirmed", "injury_day_to_day") for m in missing)
    return round(max(q, 0.1), 2)


def _yes_no(p: float) -> tuple[list[float], dict[str, float]]:
    p = min(max(p, 0.0), 1.0)
    return [round(1 - p, 5), round(p, 5)], {"mean": p, "median": float(p >= 0.5), "sd": math.sqrt(p * (1 - p))}


def project_game(
    conn: sqlite3.Connection, st: State, report: dict[str, Any], g: sqlite3.Row
) -> tuple[list[Projection], list[GameProjection]]:
    """Projections for one upcoming game (only stats whose model passed its test)."""
    choices = {
        s: Choice.from_json(c)
        for s, c in report["choices"].items()
        if s in report["stats"] and s not in game_sim.GAME_STATS and report["stats"][s].get("passed")
    }
    game_passed = {s for s in game_sim.GAME_STATS if report["stats"].get(s, {}).get("passed")}
    goals_json = report["choices"].get("goals")
    goals_ch = Choice.from_json(goals_json) if goals_json else None
    shots_all = report["choices"].get("shots")
    shots = Choice.from_json(shots_all) if shots_all else None
    out = players_out(conn, g["id"])
    report_fresh = injury_report_fresh(conn, g["id"])
    day_to_day = {pid for pid, r in injured(conn, g["id"]).items() if r["status"] in ("day_to_day", "questionable")}
    teams = {g["home_team_id"]: True, g["away_team_id"]: False}
    abbrev = dict(conn.execute("SELECT id, abbrev FROM teams WHERE id IN (?, ?)", tuple(teams)).fetchall())
    names = {}
    mixes = {t: goalie_mix(conn, g["id"], t) for t in teams}
    for mx in mixes.values():
        for pid, _ in mx.mix:
            names[pid] = conn.execute("SELECT full_name FROM players WHERE id = ?", (pid,)).fetchone()[0]
    arena = None if g["is_neutral_site"] else g["home_team_id"]
    projections: list[Projection] = []
    game_projs: list[GameProjection] = []
    env: game_sim.Outcomes | None = None
    gf: dict[str, float] = {}
    gch: GameChoice | None = None
    if game_passed and "game" in report["choices"]:
        gch = GameChoice.from_json(report["choices"]["game"])
        gf = st.game_features(
            g["home_team_id"], g["away_team_id"], {t: m.mix for t, m in mixes.items()}, g["season_id"]
        ) | {"playoff": float(g["game_type"] == "O")}
        env = game_sim.outcomes(_one(gf), gch)
    for team, home in teams.items():
        opp = g["away_team_id"] if home else g["home_team_id"]
        opp_mix = mixes[opp]
        lsv = st.league_sv()
        sv_bits = ", ".join(
            f"{names[pid]} {p:.0%} start, save % {st.goalie_sv(pid, K_SV[1]):.3f}" for pid, p in opp_mix.mix
        )
        goalie_text = (
            f"{'Confirmed' if opp_mix.status == 'confirmed' else 'Projected'} {abbrev[opp]} goalie: "
            f"{sv_bits or 'unknown'} vs league {lsv:.3f}."
        )
        ctx_base = {
            "team": abbrev[team],
            "opp": abbrev[opp],
            "home": home,
            "arena": g["venue_name"],
            "goalie_text": goalie_text,
        }
        missing_common = ["lineup_unconfirmed", "odds_not_connected"]
        if not report_fresh:
            missing_common.append("injuries_not_connected")
        if opp_mix.status != "confirmed":
            missing_common.append("goalie_unconfirmed")
        skaters = conn.execute(
            "SELECT id, position FROM players WHERE current_team_id = ? AND is_active = 1 AND position <> 'G'",
            (team,),
        ).fetchall()
        for pid, position in skaters:
            if pid in out or pid not in st.skaters:
                continue  # ruled out, or no NHL history to project from
            pos = history.pos_group(position)
            player_missing = missing_common + (["injury_day_to_day"] if pid in day_to_day else [])
            f = st.skater_features(pid, pos, team, opp, home, arena, opp_mix.mix, g["season_id"], g["game_type"] == "O")
            ctx = ctx_base | {"pos": "defenceman" if pos == "D" else "forward"}
            if env is not None and gch is not None and "first_goal" in game_passed and goals_ch is not None:
                side = "h" if home else "a"
                lam_t = float((env.lam_h if home else env.lam_a)[0])
                first_t = float((env.p_home_first if home else env.p_away_first)[0])
                lam_i = float(fit.skater_mean(_one(f), goals_ch, shots)[0])
                share = lam_i / lam_t
                pmf_fg, sm_fg = _yes_no(min(first_t, first_t * share))
                fg_factors = [
                    {
                        "name": "His share of team goals",
                        "effect": 0.0,
                        "detail": f"Projects {lam_i:.2f} goals of {abbrev[team]}'s {lam_t:.2f} ({share:.0%}).",
                    },
                    {
                        "name": "Team scores first",
                        "effect": 0.0,
                        "detail": f"{abbrev[team]} scores the first goal {first_t:.0%} of the time.",
                    },
                ]
                fg_factors += env_factors(gf, gch, side, abbrev[team], abbrev[opp], home).factors
                miss = list(player_missing)
                projections.append(
                    Projection(
                        pid,
                        "skater_first_goal",
                        "first_goal",
                        pmf_fg,
                        sm_fg["mean"],
                        sm_fg["median"],
                        sm_fg["sd"],
                        fg_factors,
                        {
                            "team_scores_first": round(first_t, 4),
                            "share_of_team_goals": round(share, 4),
                            "expected_team_goals": round(lam_t, 3),
                            "games_in_history": int(f["games"]),
                        },
                        _quality(f["games"], miss),
                        miss,
                    )
                )
            for stat, ch in choices.items():
                if ch.kind == "goalie":
                    continue
                p = fit.pmf(_one(f), ch, shots)
                ex = explain_skater(f, ch, shots, ctx)
                sm = dist.summary(p)
                miss = [m for m in player_missing if m != "goalie_unconfirmed" or "goalie" in ch.factors]
                for market in _markets_for(stat):
                    projections.append(
                        Projection(
                            pid,
                            market,
                            stat,
                            _pmf_json(p),
                            sm["mean"],
                            sm["median"],
                            sm["sd"],
                            ex.factors,
                            ex.inputs,
                            _quality(f["games"], miss),
                            miss,
                            ex.inputs["expected_toi_s"],
                            ex.inputs["expected_pp_toi_s"],
                        )
                    )
        # Goalie props are priced only for the most likely starter, with his start probability.
        mine = mixes[team]
        if mine.mix:
            gid, prob = mine.mix[0]
            if gid not in out:
                f = st.goalie_features(gid, team, opp, g["season_id"], g["game_type"] == "O")
                for stat, ch in choices.items():
                    if ch.kind != "goalie":
                        continue
                    p = fit.pmf(_one(f), ch)
                    ex = explain_goalie(f, ch, ctx_base)
                    ex.inputs["start_probability"] = round(prob, 3)
                    ex.inputs["start_status"] = mine.status
                    sm = dist.summary(p)
                    miss = [] if mine.status == "confirmed" else ["goalie_unconfirmed"]
                    miss.append("odds_not_connected")
                    for market in _markets_for(stat):
                        projections.append(
                            Projection(
                                gid,
                                market,
                                stat,
                                _pmf_json(p),
                                sm["mean"],
                                sm["median"],
                                sm["sd"],
                                ex.factors,
                                ex.inputs,
                                _quality(f["starts"], miss),
                                miss,
                            )
                        )
                if env is not None and gch is not None:
                    side = "h" if home else "a"
                    win = float(env.p_home_win[0]) if home else 1 - float(env.p_home_win[0])
                    so = float((env.so_home if home else env.so_away)[0])
                    miss = ([] if mine.status == "confirmed" else ["goalie_unconfirmed"]) + ["odds_not_connected"]
                    own = env_factors(gf, gch, side, abbrev[team], abbrev[opp], home).factors
                    other = env_factors(gf, gch, "a" if home else "h", abbrev[opp], abbrev[team], not home).factors
                    base_inputs = {
                        "start_probability": round(prob, 3),
                        "start_status": mine.status,
                        "expected_goals_for": round(float((env.lam_h if home else env.lam_a)[0]), 3),
                        "expected_goals_against": round(float((env.lam_a if home else env.lam_h)[0]), 3),
                    }
                    for stat, market, pval in (("win", "goalie_win", win), ("shutout", "goalie_shutout", so)):
                        if stat not in game_passed:
                            continue
                        pm, sm = _yes_no(pval)
                        facts = own if stat == "win" else [{**x, "name": f"{abbrev[opp]}: {x['name']}"} for x in other]
                        projections.append(
                            Projection(
                                gid,
                                market,
                                stat,
                                pm,
                                sm["mean"],
                                sm["median"],
                                sm["sd"],
                                facts,
                                base_inputs | {"tied_after_regulation": round(float(env.p_tied_reg[0]), 4)},
                                _quality(f["starts"], miss),
                                miss,
                            )
                        )
                    saves_choice = report["choices"].get("saves")
                    if "saves_win" in game_passed and saves_choice is not None:
                        sa_size = math.inf if saves_choice.get("sa_size") is None else float(saves_choice["sa_size"])
                        joint = game_sim.saves_win_joint(_one(gf), gch, side, sa_size, np.array([win]))[0]
                        by_line = {
                            f"{ln:g}": round(float(joint[math.floor(ln) + 1 :].sum()), 4)
                            for ln in game_sim.SAVES_WIN_LINES
                        }
                        main = by_line[f"{game_sim.SAVES_WIN_LINE:g}"]
                        _, sm = _yes_no(main)
                        keep = int(np.searchsorted(np.cumsum(joint), joint.sum() - 1e-6)) + 1
                        projections.append(
                            Projection(
                                gid,
                                "goalie_saves_and_win",
                                "saves_win",
                                _pmf_json(joint[:keep]),  # P(k saves AND a win); sums to P(win)
                                sm["mean"],
                                sm["median"],
                                sm["sd"],
                                own,
                                base_inputs
                                | {
                                    "p_win": round(float(joint.sum()), 4),
                                    "p_by_line": by_line,
                                    "main_line": game_sim.SAVES_WIN_LINE,
                                    "expected_shots_against": round(float(gf["a.S" if home else "h.S"]), 2),
                                },
                                _quality(f["starts"], miss),
                                miss,
                            )
                        )
    if env is not None and gch is not None:
        abbr_h, abbr_a = abbrev[g["home_team_id"]], abbrev[g["away_team_id"]]
        ex_h = env_factors(gf, gch, "h", abbr_h, abbr_a, True)
        ex_a = env_factors(gf, gch, "a", abbr_a, abbr_h, False)
        common = {
            "expected_goals_home": round(float(env.lam_h[0]), 3),
            "expected_goals_away": round(float(env.lam_a[0]), 3),
            "tied_after_regulation": round(float(env.p_tied_reg[0]), 4),
            "ot_goal_rate": round(gf["ot_q"], 4),
            "home_shootout_win_rate": round(gf["so_home"], 4),
            "goalies": {("home" if teams[t] else "away"): mixes[t].status for t in teams},
        }
        if "win" in game_passed:
            p_home = float(env.p_home_win[0])
            game_projs.append(
                GameProjection(
                    "game_moneyline",
                    "home",
                    p_home,
                    [round(1 - p_home, 5), round(p_home, 5)],
                    common,
                    ex_h.factors + [{**x, "name": f"{abbr_a}: {x['name']}"} for x in ex_a.factors],
                )
            )
        if "team_goals" in game_passed:
            for side, ex, mat in (("home", ex_h, env.home_total_book), ("away", ex_a, env.away_total_book)):
                pm = mat[0]
                game_projs.append(
                    GameProjection(
                        "team_total",
                        side,
                        float((pm * np.arange(len(pm))).sum()),
                        _pmf_json(pm),
                        common | ex.inputs,
                        ex.factors,
                    )
                )
            pm = env.game_total[0]
            game_projs.append(
                GameProjection(
                    "game_total",
                    "game",
                    float((pm * np.arange(len(pm))).sum()),
                    _pmf_json(pm),
                    common,
                    ex_h.factors + ex_a.factors,
                )
            )
    return projections, game_projs


def save(conn: sqlite3.Connection, game_id: int, proj: Projection, as_of: str, reason: str, now: datetime) -> bool:
    """Insert if new or changed (superseding the current row). Returns True if a row was written."""
    market_id = conn.execute("SELECT id FROM markets WHERE code = ?", (proj.market,)).fetchone()[0]
    mv = _model_version_id(conn, FAMILY[proj.stat])
    pmf_text = json.dumps({"min": 0, "p": proj.pmf}, separators=(",", ":"))
    cur = conn.execute(
        "SELECT id, pmf, inputs FROM player_projections WHERE game_id = ? AND player_id = ? AND market_id = ? "
        "AND is_current = 1",
        (game_id, proj.player, market_id),
    ).fetchone()
    inputs_text = json.dumps(proj.inputs, separators=(",", ":"))
    if cur and cur[1] == pmf_text and cur[2] == inputs_text:
        return False
    if cur:
        conn.execute("UPDATE player_projections SET is_current = 0 WHERE id = ?", (cur[0],))
    conn.execute(
        "INSERT INTO player_projections (game_id, player_id, market_id, model_version_id, computed_at, as_of, mean, "
        "median, std_dev, pmf, expected_toi_s, expected_pp_toi_s, inputs, factors_for, factors_against, "
        "data_quality, missing_inputs, trigger_reason, supersedes) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            game_id,
            proj.player,
            market_id,
            mv,
            iso(now),
            as_of,
            round(proj.mean, 4),
            proj.median,
            round(proj.sd, 4),
            pmf_text,
            proj.expected_toi_s,
            proj.expected_pp_toi_s,
            inputs_text,
            json.dumps([x | {"effect": round(x["effect"], 4)} for x in proj.factors if x["effect"] >= 0]),
            json.dumps([x | {"effect": round(x["effect"], 4)} for x in proj.factors if x["effect"] < 0]),
            proj.data_quality,
            json.dumps(proj.missing),
            reason,
            cur[0] if cur else None,
        ),
    )
    return True


def save_game(
    conn: sqlite3.Connection, game_id: int, gp: GameProjection, as_of: str, reason: str, now: datetime
) -> bool:
    market_id = conn.execute("SELECT id FROM markets WHERE code = ?", (gp.market,)).fetchone()[0]
    pmf_text = json.dumps({"min": 0, "p": gp.pmf}, separators=(",", ":"))
    inputs_text = json.dumps(gp.inputs, separators=(",", ":"))
    cur = conn.execute(
        "SELECT id, pmf, inputs FROM game_projections WHERE game_id = ? AND market_id = ? AND side = ? "
        "AND is_current = 1",
        (game_id, market_id, gp.side),
    ).fetchone()
    if cur and cur[1] == pmf_text and cur[2] == inputs_text:
        return False
    if cur:
        conn.execute("UPDATE game_projections SET is_current = 0 WHERE id = ?", (cur[0],))
    conn.execute(
        "INSERT INTO game_projections (game_id, market_id, side, model_version_id, computed_at, as_of, mean, pmf, "
        "inputs, factors, trigger_reason, supersedes) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            game_id,
            market_id,
            gp.side,
            _model_version_id(conn, "game_sim"),
            iso(now),
            as_of,
            round(gp.mean, 4),
            pmf_text,
            inputs_text,
            json.dumps([x | {"effect": round(x["effect"], 4)} for x in gp.factors]),
            reason,
            cur[0] if cur else None,
        ),
    )
    return True


def _upcoming(conn: sqlite3.Connection, now: datetime, today: date) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM games WHERE game_type IN ('R','O') AND status IN ('scheduled','pregame') "
        "AND game_date BETWEEN ? AND ? AND start_time_utc > ? ORDER BY start_time_utc",
        (today.isoformat(), (today + timedelta(days=PROJECT_DAYS_AHEAD)).isoformat(), iso(now)),
    ).fetchall()


def project_upcoming(
    conn: sqlite3.Connection,
    st: State,
    report: dict[str, Any],
    now: datetime,
    today: date,
    as_of: str,
    reasons: dict[int, str],
) -> tuple[int, int]:
    """Returns (projections written, games projected)."""
    written = games = 0
    for g in _upcoming(conn, now, today):
        projs, game_projs = project_game(conn, st, report, g)
        games += 1
        keep_game = {(gp.market, gp.side) for gp in game_projs}
        for mcode, side in conn.execute(
            "SELECT m.code, p.side FROM game_projections p JOIN markets m ON m.id = p.market_id "
            "WHERE p.game_id = ? AND p.is_current = 1",
            (g["id"],),
        ).fetchall():
            if (mcode, side) not in keep_game:
                conn.execute(
                    "UPDATE game_projections SET is_current = 0 WHERE game_id = ? AND side = ? AND is_current = 1 "
                    "AND market_id = (SELECT id FROM markets WHERE code = ?)",
                    (g["id"], side, mcode),
                )
        for gp in game_projs:
            written += save_game(conn, g["id"], gp, as_of, reasons.get(g["id"], "scheduled"), now)
        keep = {(p.player, p.market) for p in projs}
        # A player ruled out (or a market that stopped passing its test) loses its current projection.
        for pid, mcode in conn.execute(
            "SELECT p.player_id, m.code FROM player_projections p JOIN markets m ON m.id = p.market_id "
            "WHERE p.game_id = ? AND p.is_current = 1",
            (g["id"],),
        ).fetchall():
            if (pid, mcode) not in keep:
                conn.execute(
                    "UPDATE player_projections SET is_current = 0 WHERE game_id = ? AND player_id = ? "
                    "AND is_current = 1 AND market_id = (SELECT id FROM markets WHERE code = ?)",
                    (g["id"], pid, mcode),
                )
        for p in projs:
            written += save(conn, g["id"], p, as_of, reasons.get(g["id"], "scheduled"), now)
    return written, games


def prune(conn: sqlite3.Connection, today: date) -> int:
    """Delete projections for games older than KEEP_DAYS, except ones a prediction points to
    (those are graded later). A kept row's link to a deleted predecessor is cleared first."""
    cutoff = (today - timedelta(days=KEEP_DAYS)).isoformat()
    n = 0
    for table, col in (("player_projections", "projection_id"), ("game_projections", "game_projection_id")):
        doomed = (
            f"SELECT id FROM {table} WHERE game_id IN (SELECT id FROM games WHERE game_date < ?) "
            f"AND id NOT IN (SELECT {col} FROM predictions WHERE {col} IS NOT NULL)"
        )
        conn.execute(
            f"UPDATE {table} SET supersedes = NULL WHERE supersedes IN ({doomed}) AND id NOT IN ({doomed})",
            (cutoff, cutoff),
        )
        n += conn.execute(f"DELETE FROM {table} WHERE id IN ({doomed})", (cutoff,)).rowcount
    return n


EVAL_GROWTH = 1.10  # re-test early once completed games grow by 10% (e.g. during a backfill)


def _needs_evaluation(conn: sqlite3.Connection, now: datetime, n_games: int) -> bool:
    report, created = latest_report(conn)
    if report is None or created is None or now - parse_iso(created) >= EVAL_MAX_AGE:
        return True
    tested = int(report.get("n_games") or 0)
    if report.get("status") != "ok" and n_games > tested:
        return True  # it couldn't test before; more history has arrived since
    return n_games >= tested * EVAL_GROWTH


def run_models(
    conn: sqlite3.Connection,
    now: datetime,
    today: date,
    reasons: dict[int, str] | None = None,
    *,
    force_eval: bool = False,
) -> None:
    src = register_source(conn, MODELS_SOURCE)
    conn.commit()
    games = history.load(conn)
    st = State()
    learned = False
    if force_eval or _needs_evaluation(conn, now, len(games)):
        with ingestion_run(conn, src, "models.evaluate") as run:
            tables = fit.collect(walk(games, st))
            learned = True
            report = fit.evaluate(tables)
            report["n_games"] = len(games)
            record_evaluation(conn, report, now)
            run.rows_read = sum(len(t) for t in tables.values())
            run.meta["passed"] = sorted(s for s, e in report["stats"].items() if e.get("passed"))
            run.meta["status"] = report["status"]
    if not learned:  # no test this run, or it failed part-way: learn from all games now
        st = State()
        for _ in walk(games, st, emit=False):
            pass
    stored, _ = latest_report(conn)
    with ingestion_run(conn, src, "models.project") as run:
        if stored is None or stored.get("status") != "ok":
            run.meta["skipped"] = "no tested model yet (not enough completed games)"
            return
        as_of = max((g.start for g in games), default=iso(now))
        written, n_games = project_upcoming(conn, st, stored, now, today, as_of, reasons or {})
        run.rows_upserted = written
        run.meta["games"] = n_games
        run.meta["pruned"] = prune(conn, today)
