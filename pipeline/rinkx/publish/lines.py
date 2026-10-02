"""Sportsbook lines for the app: per game and per player, with best price, a no-vig consensus
and movement history. Only lines whose player was resolved are stored, so only those appear."""

from __future__ import annotations

import json
import sqlite3
import statistics
from collections import defaultdict
from typing import Any


def implied(a: int) -> float:
    return 100 / (a + 100) if a > 0 else -a / (-a + 100)


def novig(over: int | None, under: int | None) -> tuple[float, float] | None:
    """Multiplicative devig of a two-way market; None if a side is missing (vig not removable)."""
    if over is None or under is None:
        return None
    io, iu = implied(over), implied(under)
    return io / (io + iu), iu / (io + iu)


def _best(prices: list[tuple[str, int]]) -> dict[str, Any] | None:
    if not prices:
        return None
    book, price = max(prices, key=lambda t: t[1])  # higher American price pays more on either sign
    return {"book": book, "price": price}


def _movement(conn: sqlite3.Connection, prop_line_id: int) -> list[dict[str, Any]]:
    return [
        {"at": r[0], "line": r[1], "over": r[2], "under": r[3], "status": r[4]}
        for r in conn.execute(
            "SELECT observed_at, line, over_price, under_price, status FROM line_movements WHERE prop_line_id = ? "
            "ORDER BY observed_at",
            (prop_line_id,),
        )
    ]


def _pricing(conn: sqlite3.Connection, prop_line_id: int, full: bool) -> dict[str, Any] | None:
    """Latest frozen prediction for this line (Phase 5), if any."""
    r = conn.execute(
        "SELECT * FROM predictions WHERE prop_line_id = ? ORDER BY id DESC LIMIT 1", (prop_line_id,)
    ).fetchone()
    if r is None:
        return None
    parts = json.loads(r["confidence_parts"])
    out: dict[str, Any] = {
        "prediction_id": r["id"],
        "p_model_over": r["p_model_over"],
        "p_model_under": r["p_model_under"],
        "p_push": r["p_push"],
        "p_novig_over": r["p_novig_over"],
        "p_implied_over": r["p_implied_over"],
        "edge_over": r["edge_over"],
        "edge_under": r["edge_under"],
        "ev_over": r["ev_over"],
        "ev_under": r["ev_under"],
        "side": r["side"],
        "confidence": r["confidence"],
        "devig_method": r["devig_method"],
        "priced_at": r["created_at"],
    }
    if full:
        out["confidence_parts"] = parts
        out["calculation"] = json.loads(r["calculation"])
    return out


def _lean(books: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The best priced side across books (highest EV among sides that cleared the lean rules)."""
    best: dict[str, Any] | None = None
    for b in books:
        pr = b.get("pricing")
        if not pr or pr["side"] == "none":
            continue
        over = pr["side"] in ("over", "yes", "home")
        ev = pr["ev_over"] if over else pr["ev_under"]
        if best is None or (ev or 0) > best["ev"]:
            best = {
                "book": b["book"],
                "book_name": b["book_name"],
                "side": pr["side"],
                "line": b["line"],
                "price": b["over"] if over else b["under"],
                "edge": pr["edge_over"] if over else pr["edge_under"],
                "ev": ev,
                "confidence": pr["confidence"],
                "p_model": pr["p_model_over"] if over else pr["p_model_under"],
            }
    return best


LINE_SQL = """
SELECT l.id, l.line, l.over_price, l.under_price, l.status, l.last_seen_at, l.last_changed_at,
       m.code AS market, m.name AS market_name, m.kind, b.code AS book, b.name AS book_name,
       p.nhl_player_id, p.full_name, t.abbrev AS team
FROM prop_lines l JOIN markets m ON m.id = l.market_id JOIN sportsbooks b ON b.id = l.sportsbook_id
LEFT JOIN players p ON p.id = l.player_id LEFT JOIN teams t ON t.id = l.team_id
WHERE b.is_enabled = 1 AND l.status = 'open' AND {where}
ORDER BY m.id, p.full_name, b.code
"""


def _group(conn: sqlite3.Connection, rows: list[sqlite3.Row], with_movement: bool) -> list[dict[str, Any]]:
    markets: dict[str, dict[str, Any]] = {}
    subjects: dict[tuple[str, Any], list[sqlite3.Row]] = defaultdict(list)
    for r in rows:
        markets.setdefault(
            r["market"], {"market": r["market"], "label": r["market_name"], "kind": r["kind"], "rows": []}
        )
        subjects[(r["market"], r["nhl_player_id"] or r["team"])].append(r)
    for (market, _), rs in subjects.items():
        first = rs[0]
        books = []
        for r in rs:
            entry: dict[str, Any] = {
                "book": r["book"],
                "book_name": r["book_name"],
                "line": r["line"],
                "over": r["over_price"],
                "under": r["under_price"],
                "last_seen_at": r["last_seen_at"],
                "last_changed_at": r["last_changed_at"],
            }
            if with_movement:
                entry["movement"] = _movement(conn, r["id"])
            entry["pricing"] = _pricing(conn, r["id"], full=with_movement)
            books.append(entry)
        # Compare prices only at the most common line (a 2.5 price isn't comparable to a 3.5 one).
        lines = [b["line"] for b in books]
        common = max(set(lines), key=lambda x: (lines.count(x), x is not None and -abs(x))) if lines else None
        at_line = [b for b in books if b["line"] == common]
        nv = [n for b in at_line if (n := novig(b["over"], b["under"])) is not None]
        markets[market]["rows"].append(
            {
                "player": {"id": first["nhl_player_id"], "name": first["full_name"]}
                if first["nhl_player_id"]
                else None,
                "team": first["team"],
                "books": books,
                "line": common,
                "best_over": _best([(b["book"], b["over"]) for b in at_line if b["over"] is not None]),
                "best_under": _best([(b["book"], b["under"]) for b in at_line if b["under"] is not None]),
                "consensus": (
                    {"p_over": round(statistics.median(x[0] for x in nv), 4), "books": len(nv)} if nv else None
                ),
                "consensus_reason": None if nv else "vig_not_removable",
                "lines_differ": len(set(lines)) > 1,
            }
        )
    return list(markets.values())


def game_lines(conn: sqlite3.Connection, game_pk: int) -> dict[str, Any]:
    rows = conn.execute(LINE_SQL.format(where="l.game_id = ?"), (game_pk,)).fetchall()
    fetched = conn.execute("SELECT max(l.last_seen_at) FROM prop_lines l WHERE l.game_id = ?", (game_pk,)).fetchone()[0]
    books = [r[0] for r in conn.execute("SELECT name FROM sportsbooks WHERE is_enabled = 1 ORDER BY code")]
    return {
        "books": books,
        "markets": _group(conn, rows, with_movement=False),
        "fetched_at": fetched,
        "reason": None if rows else ("not_connected" if not books else "no_lines_yet"),
    }


def player_lines(conn: sqlite3.Connection, player_pk: int) -> dict[str, Any] | None:
    """Open lines for the player's next game with lines, including movement history."""
    g = conn.execute(
        "SELECT g.id, g.nhl_game_id, g.game_date FROM prop_lines l JOIN games g ON g.id = l.game_id "
        "WHERE l.player_id = ? AND l.status = 'open' AND g.status IN ('scheduled','pregame') "
        "ORDER BY g.start_time_utc LIMIT 1",
        (player_pk,),
    ).fetchone()
    if g is None:
        return None
    rows = conn.execute(LINE_SQL.format(where="l.game_id = ? AND l.player_id = ?"), (g[0], player_pk)).fetchall()
    return {"game": {"id": g[1], "date": g[2]}, "markets": _group(conn, rows, with_movement=True)}


def odds_admin(conn: sqlite3.Connection) -> dict[str, Any]:
    from rinkx.ingestion.odds.budget import BudgetConfig

    last = conn.execute(
        "SELECT credits_remaining, credits_used, at FROM odds_usage WHERE credits_remaining IS NOT NULL "
        "ORDER BY at DESC, id DESC LIMIT 1"
    ).fetchone()
    monthly: int | None
    reserve: int | None
    try:
        cfg = BudgetConfig.load()
        monthly, reserve = cfg.monthly_credits, cfg.reserve
    except (OSError, KeyError):
        monthly = reserve = None
    issues = [
        json.loads(r[0]) | {"detected_at": r[1]}
        for r in conn.execute(
            "SELECT detail, detected_at FROM data_quality_issues WHERE entity_type = 'odds_player_name' "
            "AND resolved_at IS NULL ORDER BY detected_at DESC LIMIT 50"
        )
    ]
    plan = conn.execute(
        "SELECT meta, finished_at FROM ingestion_runs WHERE job_name = 'odds.plan' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return {
        "credits": {"remaining": last[0], "used": last[1], "as_of": last[2]} if last else None,
        "monthly_credits": monthly,
        "reserve": reserve,
        "last_plan": (json.loads(plan[0]) | {"at": plan[1]}) if plan else None,
        "unresolved_players": issues,
    }
