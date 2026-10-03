"""Lines -> prop_lines (current state) + line_movements (append-only, written only on change)."""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from dataclasses import dataclass

from rinkx.ingestion.odds.parse import Quote


@dataclass(frozen=True)
class Line:
    book: str
    market: str  # vendor key
    subject: str | None  # player name (props) or None (game markets)
    line: float | None
    over: int | None  # over / yes / home
    under: int | None  # under / no / away
    updated: str | None


def _implied(a: int) -> float:
    return 100 / (a + 100) if a > 0 else -a / (-a + 100)


def assemble(quotes: list[Quote]) -> list[Line]:
    """Pair sides into lines. When a book lists several over/under points for one player in the
    main market, the most balanced pair is the main line. A ladder market (`*_alternate`) gives
    its lowest rung."""
    groups: dict[tuple[str, str, str | None], list[Quote]] = defaultdict(list)
    for q in quotes:
        groups[(q.book, q.market, q.subject)].append(q)
    out: list[Line] = []
    for (book, market, subject), qs in groups.items():
        upd = max((q.last_update for q in qs if q.last_update), default=None)
        if market == "h2h":
            home = next((q.price for q in qs if q.side == "home"), None)
            away = next((q.price for q in qs if q.side == "away"), None)
            out.append(Line(book, market, None, None, home, away, upd))
            continue
        if any(q.side in ("yes", "no") for q in qs):
            yes = next((q.price for q in qs if q.side == "yes"), None)
            no = next((q.price for q in qs if q.side == "no"), None)
            out.append(Line(book, market, subject, None, yes, no, upd))
            continue
        by_point: dict[float | None, dict[str, int]] = defaultdict(dict)
        for q in qs:
            by_point[q.point][q.side] = q.price

        def balance(item: tuple[float | None, dict[str, int]]) -> float:
            sides = item[1]
            if "over" in sides and "under" in sides:
                return abs(_implied(sides["over"]) - _implied(sides["under"]))
            return 9.0  # one-sided points are main only if nothing better exists

        if market.endswith("_alternate"):
            # A ladder (1+, 2+, 3+ ...): keep the lowest rung, the usual "1+ points" prop. The
            # rungs are one-sided, so there is no balanced pair to find.
            point = min((p for p in by_point if p is not None), default=None)
            sides = by_point[point]
            out.append(Line(book, market, subject, point, sides.get("over"), sides.get("under"), upd))
            continue
        point, sides = min(by_point.items(), key=balance)
        out.append(Line(book, market, subject, point, sides.get("over"), sides.get("under"), upd))
    return out


def upsert_line(
    conn: sqlite3.Connection,
    *,
    game_id: int,
    market_id: int,
    book_id: int,
    player_id: int | None,
    team_id: int | None,
    line: float | None,
    over: int | None,
    under: int | None,
    at: str,
    source_id: int,
    source_ref: str | None,
) -> tuple[int, str]:
    """Returns (prop_line id, 'new' | 'changed' | 'same')."""
    row = conn.execute(
        "SELECT id, line, over_price, under_price, status FROM prop_lines WHERE game_id = ? AND market_id = ? "
        "AND sportsbook_id = ? AND ifnull(player_id, 0) = ifnull(?, 0) AND ifnull(team_id, 0) = ifnull(?, 0) "
        "AND is_main_line = 1",
        (game_id, market_id, book_id, player_id, team_id),
    ).fetchone()
    if row is None:
        cur = conn.execute(
            "INSERT INTO prop_lines (game_id, market_id, sportsbook_id, player_id, team_id, line, over_price, "
            "under_price, is_main_line, status, first_seen_at, last_seen_at, last_changed_at, provenance, source_id, "
            "source_ref) VALUES (?,?,?,?,?,?,?,?,1,'open',?,?,?,'licensed',?,?)",
            (game_id, market_id, book_id, player_id, team_id, line, over, under, at, at, at, source_id, source_ref),
        )
        pid = int(cur.lastrowid or 0)
        _move(conn, pid, at, line, over, under, "open", source_id)
        return pid, "new"
    pid = int(row[0])
    if (row[1], row[2], row[3], row[4]) == (line, over, under, "open"):
        conn.execute("UPDATE prop_lines SET last_seen_at = ? WHERE id = ?", (at, pid))
        return pid, "same"
    conn.execute(
        "UPDATE prop_lines SET line = ?, over_price = ?, under_price = ?, status = 'open', last_seen_at = ?, "
        "last_changed_at = ?, source_ref = ? WHERE id = ?",
        (line, over, under, at, at, source_ref, pid),
    )
    _move(conn, pid, at, line, over, under, "open", source_id)
    return pid, "changed"


def _move(
    conn: sqlite3.Connection,
    pid: int,
    at: str,
    line: float | None,
    over: int | None,
    under: int | None,
    status: str,
    source_id: int,
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO line_movements (prop_line_id, observed_at, line, over_price, under_price, status, "
        "source_id) VALUES (?,?,?,?,?,?,?)",
        (pid, at, line, over, under, status, source_id),
    )


def mark_removed(
    conn: sqlite3.Connection,
    game_id: int,
    market_ids: list[int],
    book_ids: list[int],
    seen: set[int],
    at: str,
    source_id: int,
) -> int:
    """Lines a book no longer offers (in a fetch that covered that market) become 'removed'."""
    if not market_ids or not book_ids:
        return 0
    rows = conn.execute(
        f"SELECT id FROM prop_lines WHERE game_id = ? AND status = 'open' AND market_id IN "
        f"({','.join('?' * len(market_ids))}) AND sportsbook_id IN ({','.join('?' * len(book_ids))})",
        (game_id, *market_ids, *book_ids),
    ).fetchall()
    n = 0
    for (pid,) in rows:
        if pid in seen:
            continue
        conn.execute("UPDATE prop_lines SET status = 'removed', last_changed_at = ? WHERE id = ?", (at, pid))
        _move(conn, pid, at, None, None, None, "removed", source_id)
        n += 1
    return n
