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

from rinkx.ingestion.runs import SourceSpec, ingestion_run, register_source
from rinkx.models import dist, fit, history
from rinkx.models.fit import FAMILY, LABELS, MARKETS, MODEL_VERSION, Choice
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
        choices = {s: c for s, c in report["choices"].items() if s in stats}
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
    roster = [
        r[0]
        for r in conn.execute(
            "SELECT id FROM players WHERE current_team_id = ? AND position = 'G' AND is_active = 1", (team_id,)
        )
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


def players_out(conn: sqlite3.Connection, game_id: int) -> dict[int, dict[str, Any]]:
    out: dict[int, dict[str, Any]] = {}
    for r in conn.execute(
        "SELECT a.player_id, a.status, a.reason, a.source_ref, a.reported_at FROM game_availability a "
        "WHERE a.game_id = ? AND a.id = (SELECT a2.id FROM game_availability a2 WHERE a2.game_id = a.game_id "
        "AND a2.player_id = a.player_id ORDER BY a2.reported_at DESC, a2.id DESC LIMIT 1)",
        (game_id,),
    ):
        if r[1] == "out":
            out[r[0]] = {"reason": r[2], "source": r[3], "reported_at": r[4]}
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
    return f"{(v - 1) * 100:+.0f}%"


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
    q -= 0.1 * sum(m in ("goalie_unconfirmed", "lineup_unconfirmed") for m in missing)
    return round(max(q, 0.1), 2)


def project_game(conn: sqlite3.Connection, st: State, report: dict[str, Any], g: sqlite3.Row) -> list[Projection]:
    """Projections for one upcoming game (only stats whose model passed its test)."""
    choices = {s: Choice.from_json(c) for s, c in report["choices"].items() if report["stats"][s].get("passed")}
    shots_all = report["choices"].get("shots")
    shots = Choice.from_json(shots_all) if shots_all else None
    out = players_out(conn, g["id"])
    teams = {g["home_team_id"]: True, g["away_team_id"]: False}
    abbrev = dict(conn.execute("SELECT id, abbrev FROM teams WHERE id IN (?, ?)", tuple(teams)).fetchall())
    names = {}
    mixes = {t: goalie_mix(conn, g["id"], t) for t in teams}
    for mx in mixes.values():
        for pid, _ in mx.mix:
            names[pid] = conn.execute("SELECT full_name FROM players WHERE id = ?", (pid,)).fetchone()[0]
    arena = None if g["is_neutral_site"] else g["home_team_id"]
    projections: list[Projection] = []
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
        missing_common = ["lineup_unconfirmed", "injuries_not_connected", "odds_not_connected"]
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
            f = st.skater_features(pid, pos, team, opp, home, arena, opp_mix.mix, g["season_id"])
            ctx = ctx_base | {"pos": "defenceman" if pos == "D" else "forward"}
            for stat, ch in choices.items():
                if ch.kind == "goalie":
                    continue
                p = fit.pmf(_one(f), ch, shots)
                ex = explain_skater(f, ch, shots, ctx)
                sm = dist.summary(p)
                miss = [m for m in missing_common if m != "goalie_unconfirmed" or "goalie" in ch.factors]
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
                f = st.goalie_features(gid, team, opp, g["season_id"])
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
    return projections


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
        projs = project_game(conn, st, report, g)
        games += 1
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
    cutoff = (today - timedelta(days=KEEP_DAYS)).isoformat()
    cur = conn.execute(
        "DELETE FROM player_projections WHERE game_id IN (SELECT id FROM games WHERE game_date < ?) "
        "AND id NOT IN (SELECT projection_id FROM predictions)",
        (cutoff,),
    )
    return cur.rowcount


def _needs_evaluation(conn: sqlite3.Connection, now: datetime) -> bool:
    _, created = latest_report(conn)
    return created is None or now - parse_iso(created) >= EVAL_MAX_AGE


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
    if force_eval or _needs_evaluation(conn, now):
        with ingestion_run(conn, src, "models.evaluate") as run:
            tables = fit.collect(walk(games, st))
            learned = True
            report = fit.evaluate(tables)
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
