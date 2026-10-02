"""News (Phase 8): items entered through Quick Entry, each with its source link."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from typing import Any

from rinkx.timeutil import iso

FEED_DAYS = 14

SQL = """
SELECT n.id, n.headline, n.summary, n.url, n.category, n.reliability, n.published_at, n.fetched_at,
       p.nhl_player_id, p.full_name, coalesce(t.abbrev, pt.abbrev) AS team
FROM news n
LEFT JOIN news_entities e ON e.news_id = n.id
LEFT JOIN players p ON p.id = e.player_id
LEFT JOIN teams t ON t.id = e.team_id
LEFT JOIN teams pt ON pt.id = p.current_team_id
WHERE n.published_at >= ? {extra}
ORDER BY n.published_at DESC, n.id DESC
"""


def _item(r: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": r["id"],
        "headline": r["headline"],
        "summary": r["summary"],
        "url": r["url"],
        "category": r["category"],
        "reliability": r["reliability"],
        "published_at": r["published_at"],
        "player": {"id": r["nhl_player_id"], "name": r["full_name"]} if r["nhl_player_id"] else None,
        "team": r["team"],
    }


def feed(conn: sqlite3.Connection, now: datetime) -> dict[str, Any]:
    since = iso(now - timedelta(days=FEED_DAYS))
    items = [_item(r) for r in conn.execute(SQL.format(extra=""), (since,))]
    return {"generated_at": iso(now), "days": FEED_DAYS, "items": items}


def for_player(conn: sqlite3.Connection, player_pk: int, now: datetime) -> list[dict[str, Any]]:
    since = iso(now - timedelta(days=FEED_DAYS))
    rows = conn.execute(SQL.format(extra="AND e.player_id = ?"), (since, player_pk))
    return [_item(r) for r in rows]
