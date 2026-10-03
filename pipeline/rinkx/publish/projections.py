"""Projection payloads: per game, per player, and the model test report (models.json).

Only current projections are published. A projection that changed because of a Quick Entry
(goalie confirmed, player out) carries `previous`, so the app can show before -> after.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from rinkx.models import project, promotion
from rinkx.models.fit import FAMILY, GAME_MARKETS, LABELS, MARKETS, MODEL_VERSION, VERSIONS
from rinkx.publish import profile

P_GE_MAX = 40  # P(X >= k) published for k up to this (saves need ~40)
MARKET_ORDER = list(MARKETS)
REASON_TEXT = {
    "goalie_confirmed": "goalie confirmed",
    "player_out": "player ruled out",
    "lineup_change": "lineup change",
}


def _market_rows(conn: sqlite3.Connection, where: str, args: tuple[Any, ...]) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT p.*, m.code AS market, m.kind AS market_kind, m.name AS market_name, pl.nhl_player_id, "
        "pl.full_name, pl.position, pl.current_team_id AS team_id, old.mean AS prev_mean, "
        "old.computed_at AS prev_at FROM player_projections p "
        "JOIN markets m ON m.id = p.market_id JOIN players pl ON pl.id = p.player_id "
        "LEFT JOIN player_projections old ON old.id = p.supersedes "
        f"WHERE p.is_current = 1 AND {where}",
        args,
    ).fetchall()


def _market(r: sqlite3.Row, full: bool) -> dict[str, Any]:
    p = json.loads(r["pmf"])["p"]
    tail = _p_ge(p)
    out: dict[str, Any] = {
        "market": r["market"],
        "label": r["market_name"],
        "kind": r["market_kind"],
        "stat": MARKETS[r["market"]],
        "mean": round(r["mean"], 3),
        "median": r["median"],
        "sd": round(r["std_dev"], 3) if r["std_dev"] is not None else None,
        "p_ge": tail,  # P(X >= k), k = 0..
        "data_quality": r["data_quality"],
        "missing_inputs": json.loads(r["missing_inputs"]),
        "computed_at": r["computed_at"],
        "trigger_reason": r["trigger_reason"],
        "previous": (
            {"mean": round(r["prev_mean"], 3), "computed_at": r["prev_at"], "reason": REASON_TEXT[r["trigger_reason"]]}
            if r["prev_mean"] is not None and r["trigger_reason"] in REASON_TEXT
            else None
        ),
    }
    if full:
        inputs = json.loads(r["inputs"])
        out |= {
            "pmf": [round(x, 4) for x in p],
            "factors_for": json.loads(r["factors_for"]),
            "factors_against": json.loads(r["factors_against"]),
            "inputs": inputs,
            "as_of": r["as_of"],
        }
    return out


def _order(code: str) -> int:
    return MARKET_ORDER.index(code) if code in MARKET_ORDER else 99


def _p_ge(p: list[float]) -> list[float]:
    return [round(max(0.0, 1 - sum(p[:k])), 4) for k in range(min(len(p), P_GE_MAX) + 1)]


def game_environment(conn: sqlite3.Connection, game_pk: int) -> dict[str, Any] | None:
    """Model game environment: win probability and goal totals (no odds involved)."""
    rows = conn.execute(
        "SELECT gp.*, m.code AS market, old.mean AS prev_mean FROM game_projections gp "
        "JOIN markets m ON m.id = gp.market_id LEFT JOIN game_projections old ON old.id = gp.supersedes "
        "WHERE gp.game_id = ? AND gp.is_current = 1",
        (game_pk,),
    ).fetchall()
    if not rows:
        return None
    out: dict[str, Any] = {"win": None, "totals": {}, "computed_at": max(r["computed_at"] for r in rows)}
    for r in rows:
        inputs = json.loads(r["inputs"])
        if r["market"] == "game_moneyline":
            out["win"] = {
                "home": round(r["mean"], 4),
                "away": round(1 - r["mean"], 4),
                "tied_after_regulation": inputs.get("tied_after_regulation"),
                "expected_goals": {
                    "home": inputs.get("expected_goals_home"),
                    "away": inputs.get("expected_goals_away"),
                },
                "goalies": inputs.get("goalies"),
                "factors": json.loads(r["factors"]),
                "previous": (
                    {"home": round(r["prev_mean"], 4), "reason": REASON_TEXT[r["trigger_reason"]]}
                    if r["prev_mean"] is not None and r["trigger_reason"] in REASON_TEXT
                    else None
                ),
            }
        else:
            p = json.loads(r["pmf"])["p"]
            out["totals"][r["side"]] = {
                "mean": round(r["mean"], 3),
                "p_ge": _p_ge(p),
                "factors": json.loads(r["factors"]),
                "reference_mean": inputs.get("reference_mean"),
            }
    return out


def model_summary(conn: sqlite3.Connection, game_pk: int) -> dict[str, Any] | None:
    """Compact version for slate cards."""
    env = game_environment(conn, game_pk)
    if env is None:
        return None
    t = env["totals"]
    return {
        "p_home_win": env["win"]["home"] if env["win"] else None,
        "goals_home": t["home"]["mean"] if "home" in t else None,
        "goals_away": t["away"]["mean"] if "away" in t else None,
    }


def model_status(conn: sqlite3.Connection) -> dict[str, Any]:
    report, created = project.published_report(conn)
    if report is None:
        return {"status": "not_run", "tested_at": None, "passed": []}
    return {
        "status": report.get("status"),
        "tested_at": created,
        "passed": sorted(s for s, e in report["stats"].items() if e.get("passed")),
    }


def game_projections(conn: sqlite3.Connection, g: sqlite3.Row) -> dict[str, Any]:
    """Projection block for a game page; projections exist only for upcoming games."""
    status = model_status(conn)
    rows = _market_rows(conn, "p.game_id = ?", (g["id"],))
    out_players = project.players_out(conn, g["id"])
    sides: dict[str, Any] = {}
    for side, team in (("home", g["home_team_id"]), ("away", g["away_team_id"])):
        mix = project.goalie_mix(conn, g["id"], team) if g["status"] in ("scheduled", "pregame") else None
        players: dict[int, dict[str, Any]] = {}
        for r in rows:
            if r["team_id"] != team:
                continue
            pl = players.setdefault(
                r["player_id"],
                {
                    "id": r["nhl_player_id"],
                    "name": r["full_name"],
                    "position": r["position"],
                    "toi_s": r["expected_toi_s"],
                    "pp_toi_s": r["expected_pp_toi_s"],
                    "markets": {},
                },
            )
            pl["markets"][r["market"]] = _market(r, full=False)
        ordered = sorted(players.values(), key=lambda p: (p["position"] == "G", -(p["toi_s"] or 0)))
        sides[side] = {
            "goalie": _goalie_block(conn, mix, g),
            "players": ordered,
            "out": _out_list(conn, out_players, team),
        }
    if rows:
        reason = None
    elif g["status"] not in ("scheduled", "pregame"):
        reason = "game_started"
    elif status["status"] != "ok":
        reason = "models_not_ready"
    elif not status["passed"]:
        reason = "no_model_passed"
    else:
        reason = "outside_window"
    return {"models": status, "reason": reason, "environment": game_environment(conn, g["id"]), **sides}


def _out_list(conn: sqlite3.Connection, out_players: dict[int, dict[str, Any]], team: int) -> list[dict[str, Any]]:
    rows = []
    for pid, o in out_players.items():
        nhl_id, name, team_id = conn.execute(
            "SELECT nhl_player_id, full_name, current_team_id FROM players WHERE id = ?", (pid,)
        ).fetchone()
        if team_id == team:
            rows.append({"id": nhl_id, "name": name, "reason": o["reason"], "source": o["source"]})
    return rows


def goalie_start(conn: sqlite3.Connection, g: sqlite3.Row, team: int) -> dict[str, Any] | None:
    """Expected starter for an upcoming game; once played, the starter from the box score."""
    if g["status"] in ("scheduled", "pregame"):
        return _goalie_block(conn, project.goalie_mix(conn, g["id"], team), g)
    r = conn.execute(
        "SELECT p.nhl_player_id, p.full_name FROM goalie_game_stats s JOIN players p ON p.id = s.player_id "
        "WHERE s.game_id = ? AND s.team_id = ? AND s.started = 1",
        (g["id"], team),
    ).fetchone()
    if r is None:
        return None
    return {"id": r[0], "name": r[1], "status": "actual", "probability": 1.0, "source": None, "reported_at": None}


def _goalie_block(
    conn: sqlite3.Connection, mix: project.GoalieMix | None, g: sqlite3.Row | None = None
) -> dict[str, Any] | None:
    if mix is None or not mix.mix:
        return None
    first, prob = mix.mix[0]
    nhl_id, name = conn.execute("SELECT nhl_player_id, full_name FROM players WHERE id = ?", (first,)).fetchone()
    return {
        "id": nhl_id,
        "name": name,
        "status": mix.status,
        "probability": round(prob, 3),
        "source": mix.source_ref,
        "reported_at": mix.reported_at,
        "impact": profile.goalie_impact(conn, first, g["game_date"], g["season_id"]) if g is not None else None,
    }


def player_projection(conn: sqlite3.Connection, player_pk: int) -> dict[str, Any]:
    """The player's next projected game (if any), with the full Explain detail."""
    nxt = conn.execute(
        "SELECT g.*, h.abbrev AS h_abbrev, a.abbrev AS a_abbrev FROM player_projections p "
        "JOIN games g ON g.id = p.game_id JOIN teams h ON h.id = g.home_team_id JOIN teams a ON a.id = g.away_team_id "
        "WHERE p.player_id = ? AND p.is_current = 1 AND g.status IN ('scheduled','pregame') "
        "ORDER BY g.start_time_utc LIMIT 1",
        (player_pk,),
    ).fetchone()
    status = model_status(conn)
    if nxt is None:
        return {"models": status, "game": None, "markets": [], "reason": _player_reason(conn, player_pk, status)}
    rows = _market_rows(conn, "p.game_id = ? AND p.player_id = ?", (nxt["id"], player_pk))
    home = rows[0]["team_id"] == nxt["home_team_id"] if rows else True
    return {
        "models": status,
        "game": {
            "id": nxt["nhl_game_id"],
            "date": nxt["game_date"],
            "start_time_utc": nxt["start_time_utc"],
            "home": home,
            "opponent": nxt["a_abbrev"] if home else nxt["h_abbrev"],
        },
        "markets": sorted((_market(r, full=True) for r in rows), key=lambda m: _order(m["market"])),
        "reason": None,
    }


def _player_reason(conn: sqlite3.Connection, player_pk: int, status: dict[str, Any]) -> str:
    if status["status"] != "ok":
        return "models_not_ready"
    if not status["passed"]:
        return "no_model_passed"
    out = conn.execute(
        "SELECT 1 FROM game_availability a JOIN games g ON g.id = a.game_id WHERE a.player_id = ? "
        "AND a.status = 'out' AND g.status IN ('scheduled','pregame') AND a.id = (SELECT a2.id FROM "
        "game_availability a2 WHERE a2.game_id = a.game_id AND a2.player_id = a.player_id "
        "ORDER BY a2.reported_at DESC, a2.id DESC LIMIT 1)",
        (player_pk,),
    ).fetchone()
    return "ruled_out" if out else "no_upcoming_projection"


VERSION_ADDS = {
    "1.5": "back-to-back terms; line and PP-unit ice time; linemate quality",
    "1.6": "shots: shot attempts, recent form, opponent vs his position, team pace, PP-time change",
}


def _challengers(conn: sqlite3.Connection) -> dict[str, Any]:
    """family -> the challenger's walk-forward results, beside the champion's."""
    out: dict[str, Any] = {}
    reports: dict[str, dict[str, Any] | None] = {}
    for family, v in project.challengers(conn).items():
        if v not in reports:
            reports[v] = project.latest_report(conn, v)[0]
        rep = reports[v]
        if rep is None:
            continue
        out[family] = {
            "version": v,
            "adds": VERSION_ADDS.get(v),
            "stats": {
                s: {k: e.get(k) for k in ("passed", "reason", "log_score", "n_test")}
                | {"choice": rep["choices"].get(s)}
                for s, e in rep["stats"].items()
                if FAMILY.get(s) == family
            },
        }
    return out


def models_report(conn: sqlite3.Connection) -> dict[str, Any]:
    """Everything the Models page shows: how each stat did in the walk-forward test, which version
    is published for each model family, and how the challenger is doing on live results."""
    report, created = project.published_report(conn)
    markets = {
        m: {"stat": s, "label": conn.execute("SELECT name FROM markets WHERE code = ?", (m,)).fetchone()[0]}
        for m, s in (MARKETS | GAME_MARKETS).items()
    }
    not_modeled = [
        {"market": code, "label": name, "reason": "joint_model_not_built"}
        for code, name in conn.execute("SELECT code, name FROM markets ORDER BY id")
        if code not in MARKETS and code not in GAME_MARKETS
    ]
    if report is None:
        return {
            "version": MODEL_VERSION,
            "status": "not_run",
            "tested_at": None,
            "stats": {},
            "markets": markets,
            "not_modeled": not_modeled,
        }
    stats = {}
    for s, e in report["stats"].items():
        stats[s] = e | {"label": LABELS[s], "choice": report["choices"].get(s)}
    return {
        "version": report.get("version", MODEL_VERSION),
        "versions": report.get("versions", {}),
        "all_versions": list(VERSIONS),
        "challengers": _challengers(conn),
        "promotion": promotion.published(conn),
        "status": report.get("status"),
        "tested_at": created,
        "history": report.get("history"),
        "tune": report.get("tune"),
        "test": report.get("test"),
        "lineup_mode": "lineup_oracle",  # starting goalies; lines are each player's previous game's
        "stats": stats,
        "markets": markets,
        "not_modeled": not_modeled,
    }
