"""Sportsbook lines for the app: per game and per player, with best price, a no-vig consensus
and movement history. Only lines whose player was resolved are stored, so only those appear."""

from __future__ import annotations

import json
import sqlite3
import statistics
from collections import defaultdict
from datetime import datetime
from typing import Any

from rinkx.timeutil import iso


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
    return {
        "game": {"id": g[1], "date": g[2]},
        "markets": _group(conn, rows, with_movement=True),
        "events": line_events(conn, g[0], player_pk),
    }


def line_events(conn: sqlite3.Connection, game_pk: int, player_pk: int) -> list[dict[str, Any]]:
    """What happened while the lines were moving, for markers on the movement chart: starting
    goalies confirmed for this game, players ruled out or back in, news about this player or
    either team, and injury-report changes for this player. Each carries its time and source."""
    out: list[dict[str, Any]] = []
    for r in conn.execute(
        "SELECT s.reported_at, s.status, p.full_name, t.abbrev, s.source_ref FROM goalie_starts s "
        "JOIN players p ON p.id = s.player_id JOIN teams t ON t.id = s.team_id "
        "WHERE s.game_id = ? AND s.status = 'confirmed' AND s.provenance <> 'derived'",
        (game_pk,),
    ):
        out.append({"at": r[0], "kind": "goalie", "label": f"{r[3]} goalie confirmed: {r[2]}", "source": r[4]})
    for r in conn.execute(
        "SELECT a.reported_at, a.status, p.full_name, a.source_ref FROM game_availability a "
        "JOIN players p ON p.id = a.player_id WHERE a.game_id = ?",
        (game_pk,),
    ):
        verb = "ruled out" if r[1] == "out" else "back in"
        out.append({"at": r[0], "kind": "availability", "label": f"{r[2]} {verb}", "source": r[3]})
    teams = conn.execute("SELECT home_team_id, away_team_id FROM games WHERE id = ?", (game_pk,)).fetchone()
    for r in conn.execute(
        "SELECT DISTINCT n.published_at, n.category, n.headline, n.url FROM news n "
        "JOIN news_entities e ON e.news_id = n.id WHERE e.player_id = ? OR e.team_id IN (?, ?)",
        (player_pk, teams[0], teams[1]),
    ):
        out.append({"at": r[0], "kind": "news", "label": f"{r[1].title()}: {r[2]}", "source": r[3]})
    for r in conn.execute(
        "SELECT reported_at, status, description, source_ref FROM injuries WHERE player_id = ?", (player_pk,)
    ):
        label = f"Injury report: {r[1].replace('_', ' ')}" + (f" ({r[2]})" if r[2] else "")
        out.append({"at": r[0], "kind": "injury", "label": label, "source": r[3]})
    return sorted(out, key=lambda e: e["at"])


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


MOVEMENT_SQL = """
SELECT l.id, l.line, l.over_price, l.under_price, l.first_seen_at, l.last_changed_at, l.last_seen_at,
       m.code AS market, m.name AS market_name, m.kind, b.code AS book, b.name AS book_name,
       g.nhl_game_id, g.start_time_utc, h.abbrev AS home, a.abbrev AS away,
       p.nhl_player_id, p.full_name, t.abbrev AS team,
       (SELECT count(*) FROM line_movements x WHERE x.prop_line_id = l.id) AS moves,
       (SELECT x.over_price FROM line_movements x WHERE x.prop_line_id = l.id AND x.over_price IS NOT NULL
        ORDER BY x.observed_at LIMIT 1) AS first_over,
       (SELECT x.under_price FROM line_movements x WHERE x.prop_line_id = l.id AND x.under_price IS NOT NULL
        ORDER BY x.observed_at LIMIT 1) AS first_under
FROM prop_lines l
JOIN markets m ON m.id = l.market_id
JOIN sportsbooks b ON b.id = l.sportsbook_id AND b.is_enabled = 1
JOIN games g ON g.id = l.game_id
JOIN teams h ON h.id = g.home_team_id JOIN teams a ON a.id = g.away_team_id
LEFT JOIN players p ON p.id = l.player_id
LEFT JOIN teams t ON t.id = p.current_team_id
WHERE l.status = 'open' AND l.is_main_line = 1 AND g.status IN ('scheduled','pregame') AND g.start_time_utc > ?
"""


def movement_board(conn: sqlite3.Connection, now: datetime) -> dict[str, Any]:
    """Line Movement page (Phase 10): every open main line for upcoming games with its first and
    current price and the change in implied probability of the over / yes / home side."""
    rows = []
    for r in conn.execute(MOVEMENT_SQL, (iso(now),)):
        change = None
        if r["first_over"] is not None and r["over_price"] is not None:
            change = round((implied(r["over_price"]) - implied(r["first_over"])) * 100, 2)
        rows.append(
            {
                "subject": r["full_name"] or f"{r['away']} @ {r['home']}",
                "player_id": r["nhl_player_id"],
                "team": r["team"],
                "game": {
                    "id": r["nhl_game_id"],
                    "home": r["home"],
                    "away": r["away"],
                    "start_time_utc": r["start_time_utc"],
                },
                "market": r["market"],
                "market_label": r["market_name"],
                "kind": r["kind"],
                "book_name": r["book_name"],
                "line": r["line"],
                "first": {"over": r["first_over"], "under": r["first_under"], "at": r["first_seen_at"]},
                "now": {"over": r["over_price"], "under": r["under_price"], "at": r["last_seen_at"]},
                "changed_at": r["last_changed_at"],
                "moves": r["moves"],
                "change_pts": change,
            }
        )
    rows.sort(key=lambda x: -abs(x["change_pts"] or 0))
    return {"generated_at": iso(now), "source": "The Odds API", "rows": rows}
