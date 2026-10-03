"""Best Props (Phase 6): every priced line for upcoming games, flattened for the browser,
which filters and sorts. Only the latest frozen prediction per line, only open lines, only
current projections; every row carries its source (book, vendor) and timestamps."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any

from rinkx.publish import why
from rinkx.timeutil import iso

SQL = """
WITH latest AS (
  SELECT p.* FROM predictions p
  WHERE p.id = (SELECT max(p2.id) FROM predictions p2 WHERE p2.prop_line_id = p.prop_line_id)
)
SELECT pr.*, m.code AS market, m.name AS market_name, m.kind, b.code AS book, b.name AS book_name,
       l.last_seen_at, l.last_changed_at, l.status AS line_status,
       g.nhl_game_id, g.game_date, g.start_time_utc, h.abbrev AS home, a.abbrev AS away,
       pl.nhl_player_id, pl.full_name, pl.position, t.abbrev AS team,
       pp.is_current AS proj_current, pp.data_quality, pp.missing_inputs, pp.mean AS proj_mean,
       pp.std_dev AS proj_sd, gp.is_current AS gproj_current,
       sc.intelligence, sc.intelligence_parts, sc.shot_environment, sc.shot_env_parts, sc.value AS value_score,
       sc.facts, sc.config_version
FROM latest pr
JOIN prop_lines l ON l.id = pr.prop_line_id
JOIN markets m ON m.id = pr.market_id
JOIN sportsbooks b ON b.id = pr.sportsbook_id AND b.is_enabled = 1
JOIN games g ON g.id = pr.game_id
JOIN teams h ON h.id = g.home_team_id JOIN teams a ON a.id = g.away_team_id
LEFT JOIN players pl ON pl.id = pr.player_id
LEFT JOIN teams t ON t.id = pl.current_team_id
LEFT JOIN player_projections pp ON pp.id = pr.projection_id
LEFT JOIN game_projections gp ON gp.id = pr.game_projection_id
LEFT JOIN prediction_scores sc ON sc.prediction_id = pr.id
WHERE l.status = 'open' AND g.status IN ('scheduled','pregame') AND g.start_time_utc > ?
"""

OVER_SIDES = ("over", "yes", "home")


def _row(r: sqlite3.Row) -> dict[str, Any]:
    side = r["side"]
    lean = side != "none"
    over = side in OVER_SIDES if lean else (r["edge_over"] or -1) >= (r["edge_under"] or -1)
    edge = r["edge_over"] if over else r["edge_under"]
    ev = r["ev_over"] if over else r["ev_under"]
    p_market = (
        (r["p_novig_over"] if over else r["p_novig_under"])
        if r["p_novig_over"] is not None
        else (r["p_implied_over"] if over else r["p_implied_under"])
    )
    subject = (
        {
            "type": "player",
            "id": r["nhl_player_id"],
            "name": r["full_name"],
            "team": r["team"],
            "position": r["position"],
        }
        if r["nhl_player_id"]
        else {"type": "game", "name": f"{r['away']} @ {r['home']}", "team": None}
    )
    return {
        "prediction_id": r["id"],
        "subject": subject,
        "game": {
            "id": r["nhl_game_id"],
            "date": r["game_date"],
            "start_time_utc": r["start_time_utc"],
            "home": r["home"],
            "away": r["away"],
        },
        "market": r["market"],
        "market_label": r["market_name"],
        "kind": r["kind"],
        "book": r["book"],
        "book_name": r["book_name"],
        "source": "The Odds API",
        "line": r["line"],
        "over_price": r["over_price"],
        "under_price": r["under_price"],
        "lean": side if lean else None,
        "side_scored": ("over" if over else "under") if r["kind"] != "moneyline" else ("home" if over else "away"),
        "price": r["over_price"] if over else r["under_price"],
        "p_model": r["p_model_over"] if over else r["p_model_under"],
        "p_market": p_market,
        "market_is_novig": r["p_novig_over"] is not None,
        "edge": edge,
        "ev": ev,
        "confidence": r["confidence"],
        "confidence_parts": json.loads(r["confidence_parts"]) or None,
        "calculation": json.loads(r["calculation"]),
        "data_quality": r["data_quality"],
        "missing_inputs": json.loads(r["missing_inputs"]) if r["missing_inputs"] else [],
        "line_seen_at": r["last_seen_at"],
        "line_changed_at": r["last_changed_at"],
        "priced_at": r["created_at"],
        "projection": round(r["proj_mean"], 3) if r["proj_mean"] is not None else None,
        "scores": _scores(r, p_model=r["p_model_over"] if over else r["p_model_under"], p_market=p_market, edge=edge),
    }


def _scores(r: sqlite3.Row, *, p_model: float, p_market: float | None, edge: float | None) -> dict[str, Any] | None:
    if r["facts"] is None:
        return None
    facts = json.loads(r["facts"])
    label = r["market_name"]
    return {
        "intelligence": r["intelligence"],
        "intelligence_parts": json.loads(r["intelligence_parts"]),
        "shot_environment": r["shot_environment"],
        "shot_env_parts": json.loads(r["shot_env_parts"]),
        "value": r["value_score"],
        "config_version": r["config_version"],
        "facts": facts,
        "why": why.bullets(facts, label=label, p_model=p_model, p_market=p_market, edge=edge),
        "summary": why.summary(facts, label=label, edge=edge, p_model=p_model, p_market=p_market),
    }


def best_props(conn: sqlite3.Connection, now: datetime) -> dict[str, Any]:
    rows = [
        _row(r)
        for r in conn.execute(SQL, (iso(now),)).fetchall()
        if (r["projection_id"] and r["proj_current"]) or (r["game_projection_id"] and r["gproj_current"])
    ]
    books = [r[0] for r in conn.execute("SELECT name FROM sportsbooks WHERE is_enabled = 1 ORDER BY name")]
    open_lines = conn.execute(
        "SELECT count(*) FROM prop_lines l JOIN games g ON g.id = l.game_id WHERE l.status = 'open' "
        "AND g.status IN ('scheduled','pregame') AND g.start_time_utc > ?",
        (iso(now),),
    ).fetchone()[0]
    return {
        "generated_at": iso(now),
        "books": books,
        "open_lines": open_lines,
        "rows": sorted(rows, key=lambda x: (x["game"]["start_time_utc"], -(x["ev"] or -9))),
        "reason": None if rows else ("not_connected" if not books else ("not_priced" if open_lines else "no_lines")),
    }
