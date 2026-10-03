"""Prop scores, computed when a line is priced and frozen with the prediction (prediction_scores).

* **Prop Intelligence Score (0-100):** how well the evidence lines up behind the side RinkX scored
  (its lean, or the better side): model edge, projection vs the line, recent and season hit rates
  at this line, matchup, deployment, power-play time and recent price movement.
* **Shot Environment Score (0-100, shots-on-goal props):** how favourable the setting is for shots:
  his volume, the opponent's allowance (overall and to his position), expected ice time and power
  play, the game's pace and his share of his team's shots. It describes the setting, not a side.
* **Prop Value (0-100):** expected value at the offered price. Model Confidence (confidence.py)
  is separate: it says how far to trust the model's number; value says how good the price is.

Every part is a transparent transform of one published input (config/scoring.yml); a part without
data is left out and the others rescaled. Only data from before the game is used: hit rates count
games played before the game's date, and everything else is what the projection and the market
showed at pricing time.
"""

from __future__ import annotations

import json
import math
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

import yaml

from rinkx.config import REPO_ROOT

SCORING_FILE = REPO_ROOT / "config/scoring.yml"

# stat column(s) per market for hit rates: (table, expression)
STAT_SQL = {
    "skater_shots_on_goal": ("player_game_stats", "shots"),
    "skater_goals": ("player_game_stats", "goals"),
    "skater_anytime_goal": ("player_game_stats", "goals"),
    "skater_assists": ("player_game_stats", "assists"),
    "skater_points": ("player_game_stats", "goals + assists"),
    "skater_pp_points": ("player_game_stats", "pp_goals + pp_assists"),
    "skater_pp_goal": ("player_game_stats", "pp_goals"),
    "skater_pp_assist": ("player_game_stats", "pp_assists"),
    "skater_blocked_shots": ("player_game_stats", "blocked_shots"),
    "skater_hits": ("player_game_stats", "hits"),
    "goalie_saves": ("goalie_game_stats", "saves"),
    "goalie_goals_against": ("goalie_game_stats", "goals_against"),
}
OVER = ("over", "yes", "home")

LABELS = {
    "model_edge": "Model edge",
    "projection_vs_line": "Projection vs line",
    "recent_volume": "Last-10 hit rate",
    "season_volume": "Season hit rate",
    "matchup": "Matchup",
    "deployment": "Ice time / deployment",
    "power_play": "Power-play time",
    "market_movement": "Market movement",
    "shooting_volume": "Shooting volume",
    "opponent_allowance": "Opponent shots allowed",
    "expected_toi": "Expected ice time",
    "game_pace": "Game pace",
    "pp_opportunity": "Power-play opportunity",
    "shot_share": "Shot share",
    "opponent_by_position": "Opponent vs his position",
}


@dataclass(frozen=True)
class ScoringConfig:
    version: str
    intelligence: dict[str, float]
    shot_environment: dict[str, float]
    ratio_k: float
    edge_scale: float
    ev_scale: float
    z_k: float
    min_games: int

    @staticmethod
    def load(path: Path = SCORING_FILE) -> ScoringConfig:
        d: dict[str, Any] = yaml.safe_load(path.read_text())
        return ScoringConfig(
            str(d["version"]),
            {k: float(v) for k, v in d["intelligence"].items()},
            {k: float(v) for k, v in d["shot_environment"].items()},
            float(d["ratio_k"]),
            float(d["edge_scale"]),
            float(d["ev_scale"]),
            float(d["z_k"]),
            int(d["min_games_for_hit_rate"]),
        )


@cache
def config() -> ScoringConfig:
    return ScoringConfig.load()


def _clip(x: float) -> float:
    return max(0.0, min(100.0, x))


def from_ratio(r: float | None, cfg: ScoringConfig) -> float | None:
    if r is None or r <= 0 or not math.isfinite(r):
        return None
    return _clip(50 + 50 * math.tanh(cfg.ratio_k * math.log(r)))


def from_edge(e: float | None, scale: float) -> float | None:
    return None if e is None else _clip(50 + 50 * math.tanh(e / scale))


def from_z(z: float | None, cfg: ScoringConfig) -> float | None:
    return None if z is None else _clip(50 + 50 * math.tanh(z * cfg.z_k))


def combine(
    parts: dict[str, tuple[float | None, str]], weights: dict[str, float]
) -> tuple[int | None, list[dict[str, Any]]]:
    """Weighted average of the parts that have data. Returns (score, parts as published)."""
    have = {k: v for k, v in parts.items() if v[0] is not None and weights.get(k, 0) > 0}
    total_w = sum(weights[k] for k in have)
    out = []
    for k, w in weights.items():
        s, detail = parts.get(k, (None, "no data"))
        out.append(
            {
                "part": k,
                "label": LABELS[k],
                "weight": w,
                "score": None if s is None else round(s),
                "points": None if s is None or not total_w else round(s * w / total_w, 1),
                "detail": detail,
            }
        )
    if not have or total_w < 50:  # less than half the weight has data: too little to score
        return None, out
    return round(sum(float(have[k][0] or 0.0) * weights[k] for k in have) / total_w), out


def hit_rate(
    conn: sqlite3.Connection,
    market: str,
    player: int,
    before: str,
    line: float,
    over: bool,
    season: int,
    last: int | None,
) -> tuple[int, int] | None:
    """(hits, games) at this line on this side, over the last `last` games (or this season)."""
    spec = STAT_SQL.get(market)
    if spec is None:
        return None
    table, expr = spec
    started = " AND s.started = 1" if table == "goalie_game_stats" else " AND coalesce(s.toi_s, 1) > 0"
    cond = "" if last else " AND g.season_id = ?"
    args: tuple[Any, ...] = (player, before) + (() if last else (season,))
    rows = conn.execute(
        f"SELECT {expr} AS v FROM {table} s JOIN games g ON g.id = s.game_id WHERE s.player_id = ? "
        f"AND g.game_date < ? AND g.status = 'final'{started}{cond} ORDER BY g.start_time_utc DESC"
        + (f" LIMIT {int(last)}" if last else ""),
        args,
    ).fetchall()
    vals = [r[0] for r in rows if r[0] is not None]
    if not vals:
        return None
    hits = sum((v > line) if over else (v < line) for v in vals)
    return hits, len(vals)


def opponent_by_position(conn: sqlite3.Connection, opp: int, pos: str, before: str, games: int = 20) -> float | None:
    """Shots per game the opponent allowed to forwards (or defence) over its last `games`, relative to
    the league over the same dates."""
    pos_cond = "p.position = 'D'" if pos == "D" else "p.position IN ('C','L','R')"
    recent = conn.execute(
        "SELECT g.id, g.game_date FROM games g WHERE g.status = 'final' AND g.game_date < ? AND "
        "(g.home_team_id = ? OR g.away_team_id = ?) ORDER BY g.start_time_utc DESC LIMIT ?",
        (before, opp, opp, games),
    ).fetchall()
    if len(recent) < 5:
        return None
    ids = [r[0] for r in recent]
    marks = ",".join("?" * len(ids))
    allowed = conn.execute(
        f"SELECT sum(s.shots) FROM player_game_stats s JOIN players p ON p.id = s.player_id WHERE s.game_id IN "
        f"({marks}) AND s.team_id <> ? AND {pos_cond}",
        (*ids, opp),
    ).fetchone()[0]
    since = recent[-1][1]
    lg = conn.execute(
        f"SELECT sum(s.shots) * 1.0 / count(DISTINCT s.game_id || '-' || s.team_id) FROM player_game_stats s "
        f"JOIN players p ON p.id = s.player_id JOIN games g ON g.id = s.game_id WHERE g.game_date >= ? "
        f"AND g.game_date < ? AND g.status = 'final' AND {pos_cond}",
        (since, before),
    ).fetchone()[0]
    if not allowed or not lg:
        return None
    return float(allowed / len(ids)) / float(lg)


def _effect(factors: list[dict[str, Any]], *names: str) -> float | None:
    vals = [f["effect"] for f in factors if any(f["name"].startswith(n) for n in names)]
    return math.prod(1 + v for v in vals) if vals else None


def score_prop(
    conn: sqlite3.Connection,
    *,
    market: str,
    kind: str,
    side: str,
    line: float | None,
    price: int | None,
    edge: float | None,
    ev: float | None,
    moved_against_pts: float | None,
    projection: Mapping[str, Any] | None,
    player: int | None,
    position: str | None,
    game: sqlite3.Row,
    opp: int | None,
    cfg: ScoringConfig | None = None,
) -> dict[str, Any]:
    """Scores and the facts behind them for the side being scored."""
    cfg = cfg or config()
    over = side in OVER
    sign = 1.0 if over else -1.0
    facts: dict[str, Any] = {"side": side, "line": line, "price": price}
    parts: dict[str, tuple[float | None, str]] = {}
    parts["model_edge"] = (
        from_edge(edge, cfg.edge_scale),
        f"{edge * 100:+.1f} pts" if edge is not None else "no market",
    )
    inputs: dict[str, Any] = json.loads(projection["inputs"]) if projection is not None else {}
    factors: list[dict[str, Any]] = (
        json.loads(projection["factors_for"]) + json.loads(projection["factors_against"])
        if projection is not None
        else []
    )
    mean = projection["mean"] if projection is not None else None
    sd = projection["std_dev"] if projection is not None else None
    at = line if line is not None else (0.5 if kind == "yes_no" else None)
    if mean is not None and at is not None and sd:
        z = sign * (mean - at) / sd
        parts["projection_vs_line"] = (from_z(z, cfg), f"projection {mean:.2f} vs {at:g} ({sign * (mean - at):+.2f})")
        facts |= {"projection": round(mean, 2), "projection_diff": round(mean - at, 2)}
    else:
        parts["projection_vs_line"] = (None, "no projection at a line")
    if player is not None and at is not None:
        for key, last in (("recent_volume", 10), ("season_volume", None)):
            hr = hit_rate(conn, market, player, game["game_date"], at, over, game["season_id"], last)
            if hr is None or hr[1] < cfg.min_games:
                parts[key] = (None, f"fewer than {cfg.min_games} games")
                continue
            parts[key] = (100 * hr[0] / hr[1], f"{hr[0]}/{hr[1]} {'over' if over else 'under'} {at:g}")
            facts["l10_hit" if last else "season_hit"] = list(hr)
    matchup = _effect(factors, "Opponent", "Opposing goalie", "His team's defence")
    parts["matchup"] = (
        from_ratio(matchup**sign, cfg) if matchup else None,
        f"{(matchup - 1) * 100:+.0f}% from opponent factors" if matchup else "no matchup factor",
    )
    toi, pos_toi = inputs.get("expected_toi_s"), inputs.get("pos_toi_s")
    pp, pos_pp = inputs.get("expected_pp_toi_s"), inputs.get("pos_pp_toi_s")
    is_skater = position is not None and position != "G"
    if is_skater and toi and pos_toi:
        parts["deployment"] = (from_ratio((toi / pos_toi) ** sign, cfg), f"{_mmss(toi)} expected vs {_mmss(pos_toi)}")
        facts["expected_toi_s"] = toi
    if is_skater and pp is not None and pos_pp:
        parts["power_play"] = (from_ratio(((pp + 1) / (pos_pp + 1)) ** sign, cfg), f"{_mmss(pp)} PP vs {_mmss(pos_pp)}")
        facts["expected_pp_toi_s"] = pp
    if inputs.get("line"):
        facts["line_slot"] = inputs["line"]
    if inputs.get("pp_unit"):
        facts["pp_unit"] = inputs["pp_unit"]
    if moved_against_pts is not None:
        parts["market_movement"] = (
            _clip(50 - 50 * math.tanh(moved_against_pts / 4)),
            f"{-moved_against_pts:+.1f} pts toward this side (24 h)",
        )
    intelligence, i_parts = combine(parts, cfg.intelligence)

    env: int | None = None
    e_parts: list[dict[str, Any]] = []
    if market == "skater_shots_on_goal" and projection is not None:
        ep: dict[str, tuple[float | None, str]] = {}
        rate = _effect(factors, "His rate")
        ep["shooting_volume"] = (from_ratio(rate, cfg), f"{(rate - 1) * 100:+.0f}% vs his position" if rate else "")
        opp_r = inputs.get("opp_allowed_ratio")
        ep["opponent_allowance"] = (
            from_ratio(opp_r, cfg),
            f"{inputs.get('opp_shots_allowed', 0):.1f} shots/game allowed",
        )
        ep["expected_toi"] = (from_ratio(toi / pos_toi, cfg) if toi and pos_toi else None, "")
        ts, lg = inputs.get("team_shots_expected"), inputs.get("league_team_shots")
        ep["game_pace"] = (
            from_ratio(ts / lg, cfg) if ts and lg else None,
            f"team expected {ts:.1f} shots" if ts else "",
        )
        ep["pp_opportunity"] = (from_ratio((pp + 1) / (pos_pp + 1), cfg) if pp is not None and pos_pp else None, "")
        ref = inputs.get("reference_mean")
        share = (mean / ts) / (ref / lg) if mean and ts and lg and ref else None
        ep["shot_share"] = (from_ratio(share, cfg), f"{mean / ts:.1%} of team shots" if mean and ts else "")
        obp = opponent_by_position(conn, opp, "D" if position == "D" else "F", game["game_date"]) if opp else None
        ep["opponent_by_position"] = (
            from_ratio(obp, cfg),
            f"{(obp - 1) * 100:+.0f}% to {'defence' if position == 'D' else 'forwards'} (last 20 games)" if obp else "",
        )
        for k, (s, d) in ep.items():
            if not d and s is not None:
                ep[k] = (s, {"expected_toi": f"{_mmss(toi)} vs {_mmss(pos_toi)}",
                             "pp_opportunity": f"{_mmss(pp)} PP vs {_mmss(pos_pp)}"}.get(k, ""))  # fmt: skip
        env, e_parts = combine(ep, cfg.shot_environment)
        facts |= {
            "opp_shots_allowed": inputs.get("opp_shots_allowed"),
            "opp_vs_position": round(obp, 3) if obp else None,
            "team_shots_expected": ts,
        }
        facts["shot_environment"] = env
    value = round(from_edge(ev, cfg.ev_scale)) if ev is not None else None  # type: ignore[arg-type]
    return {
        "intelligence": intelligence,
        "intelligence_parts": i_parts,
        "shot_environment": env,
        "shot_env_parts": e_parts,
        "value": value,
        "facts": facts,
        "config_version": cfg.version,
    }


def _mmss(s: float | None) -> str:
    if s is None:
        return "—"
    s = round(s)
    return f"{s // 60}:{s % 60:02d}"


def save(conn: sqlite3.Connection, prediction_id: int, sc: dict[str, Any], created_at: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO prediction_scores (prediction_id, intelligence, intelligence_parts, shot_environment, "
        "shot_env_parts, value, facts, config_version, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (
            prediction_id,
            sc["intelligence"],
            json.dumps(sc["intelligence_parts"]),
            sc["shot_environment"],
            json.dumps(sc["shot_env_parts"]),
            sc["value"],
            json.dumps(sc["facts"]),
            sc["config_version"],
            created_at,
        ),
    )
