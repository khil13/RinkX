"""Injury report from ESPN's public NHL injuries endpoint.

There is no official NHL injury feed. ESPN's public (undocumented) endpoint is the most complete
free source: every team's list of players who are out, on injured reserve, day-to-day or
suspended, with a body part, an expected return date and a link to the player card (which is
stored as each row's source). It is unofficial, so rows are stored with provenance 'reported'
and the app says where they came from. Player names are matched to NHL ids with the same
resolver as sportsbook names. Unmatched names go to the Admin review list and are never guessed.

The parser is strict: a response that doesn't have the shape recorded in
tests/fixtures/injuries/espn_injuries.json fails the run, and the previous report stays in place.
Each successful fetch is the complete current report: a player who is no longer listed is marked
resolved, and a change of status starts a new row (the history is kept).
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from rinkx.ingestion.http import Fetcher
from rinkx.ingestion.odds.resolve import PlayerResolver, load_config_aliases
from rinkx.ingestion.runs import SourceSpec, ingestion_run, register_source
from rinkx.timeutil import iso, parse_iso

URL = "https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/injuries"
SOURCE = SourceSpec(
    "espn_injuries",
    "ESPN NHL injury report (public, unofficial)",
    "injuries",
    "free",
    URL,
    "Public ESPN endpoint, personal use. Unofficial: statuses are reported, not league-confirmed.",
    0.8,
)
# ESPN abbreviations that differ from the NHL's.
TEAM_ALIASES = {"LA": "LAK", "NJ": "NJD", "SJ": "SJS", "TB": "TBL"}
# Status by ESPN type, refined by the "fantasy" abbreviation (IR-LT = long-term injured reserve).
STATUS = {
    "INJURY_STATUS_OUT": "out",
    "INJURY_STATUS_IR": "injured_reserve",
    "INJURY_STATUS_DAYTODAY": "day_to_day",
    "INJURY_STATUS_SUSPENSION": "suspended",
}
# Statuses that keep a player out of projections (unless a Quick Entry says he's back in).
OUT_STATUSES = ("out", "injured_reserve", "long_term_ir", "suspended")
FRESH_FOR = timedelta(hours=26)  # older than this, the app treats injuries as not connected


class InjuryFeedError(ValueError):
    pass


@dataclass(frozen=True)
class Entry:
    espn_id: str
    name: str
    team: str  # NHL abbreviation
    status: str
    body_part: str | None
    description: str | None
    expected_return: str | None
    reported_at: str
    source_ref: str


def _req(d: dict[str, Any], key: str, kind: type) -> Any:
    v = d.get(key)
    if not isinstance(v, kind):
        raise InjuryFeedError(f"expected {key!r} to be {kind.__name__}, got {type(v).__name__}")
    return v


def _ts(s: str) -> str:
    """ESPN times come as 2026-09-30T15:57Z (no seconds)."""
    for fmt in ("%Y-%m-%dT%H:%MZ", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return iso(datetime.strptime(s, fmt).replace(tzinfo=UTC))
        except ValueError:
            continue
    try:
        return iso(parse_iso(s))
    except ValueError as exc:
        raise InjuryFeedError(f"unreadable date {s!r}") from exc


def parse(payload: Any) -> list[Entry]:
    if not isinstance(payload, dict):
        raise InjuryFeedError("response is not an object")
    teams = _req(payload, "injuries", list)
    out: list[Entry] = []
    for t in teams:
        for e in _req(t, "injuries", list):
            athlete = _req(e, "athlete", dict)
            team = _req(_req(athlete, "team", dict), "abbreviation", str)
            kind = _req(_req(e, "type", dict), "name", str)
            details = e.get("details") or {}
            fantasy = ((details.get("fantasyStatus") or {}).get("abbreviation") or "").upper()
            status = "long_term_ir" if fantasy == "IR-LT" else STATUS.get(kind, "unknown")
            link = next(
                (lk["href"] for lk in athlete.get("links") or [] if "playercard" in (lk.get("rel") or [])), None
            )
            comment = (e.get("shortComment") or "").strip()
            body = details.get("type")
            detail = details.get("detail")
            desc = comment if len(comment) > 3 else None  # ESPN often has just "ir"
            if desc is None and body:
                desc = body + (f" ({detail})" if detail and detail != "Not Specified" else "")
            out.append(
                Entry(
                    espn_id=str(_req(e, "id", str)),
                    name=_req(athlete, "displayName", str),
                    team=TEAM_ALIASES.get(team, team),
                    status=status,
                    body_part=body if body and body != "Suspension" else None,
                    description=desc,
                    expected_return=details.get("returnDate"),
                    reported_at=_ts(_req(e, "date", str)),
                    source_ref=link or URL,
                )
            )
    return out


def store(conn: sqlite3.Connection, entries: list[Entry], source_id: int, now: datetime) -> tuple[int, int, int]:
    """Apply a complete report. Returns (new or changed, resolved, unmatched)."""
    teams = {r[0]: r[1] for r in conn.execute("SELECT abbrev, id FROM teams")}
    resolver = PlayerResolver(conn, source_id, now, load_config_aliases(), entity_type="injury_player_name")
    seen: set[int] = set()
    changed = unmatched = 0
    for e in entries:
        team_id = teams.get(e.team)
        if team_id is None:
            unmatched += 1
            continue
        pid = resolver.resolve(e.name, (team_id, team_id), 0)
        if pid is None:
            unmatched += 1
            continue
        seen.add(pid)
        cur = conn.execute(
            "SELECT id, status, description, expected_return FROM injuries WHERE player_id = ? AND is_active = 1 "
            "AND source_id = ?",
            (pid, source_id),
        ).fetchone()
        if cur is not None and (cur[1], cur[2], cur[3]) == (e.status, e.description, e.expected_return):
            conn.execute("UPDATE injuries SET fetched_at = ? WHERE id = ?", (iso(now), cur[0]))
            continue
        if cur is not None:
            conn.execute("UPDATE injuries SET is_active = 0, resolved_at = ? WHERE id = ?", (iso(now), cur[0]))
        conn.execute(
            "INSERT INTO injuries (player_id, team_id, status, body_part, description, expected_return, "
            "reported_at, is_active, provenance, source_id, source_ref, fetched_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 1, 'reported', ?, ?, ?)",
            (
                pid,
                team_id,
                e.status,
                e.body_part,
                e.description,
                e.expected_return,
                e.reported_at,
                source_id,
                e.source_ref,
                iso(now),
            ),
        )
        changed += 1
    resolved = 0
    for row_id, pid in conn.execute(
        "SELECT id, player_id FROM injuries WHERE is_active = 1 AND source_id = ?", (source_id,)
    ).fetchall():
        if pid not in seen:
            conn.execute("UPDATE injuries SET is_active = 0, resolved_at = ? WHERE id = ?", (iso(now), row_id))
            resolved += 1
    return changed, resolved, unmatched


def run_injuries(conn: sqlite3.Connection, fetcher: Fetcher, now: datetime) -> None:
    src = register_source(conn, SOURCE)
    with ingestion_run(conn, src, "injuries") as run:
        entries = parse(fetcher.get_json(URL))
        changed, resolved, unmatched = store(conn, entries, src, now)
        run.rows_read = len(entries)
        run.rows_upserted = changed
        run.meta.update(
            checked_at=iso(now), changed=changed, resolved=resolved, unmatched=unmatched, listed=len(entries)
        )
        if unmatched:
            run.errors.append(f"{unmatched} names not matched to an NHL player (see Admin)")


LAST_CHECK_SQL = """
SELECT json_extract(r.meta, '$.checked_at') FROM ingestion_runs r JOIN data_sources s ON s.id = r.source_id
WHERE s.code = ? AND r.job_name = 'injuries' AND r.status IN ('succeeded','partial') ORDER BY r.id DESC LIMIT 1
"""


def feed_fresh(conn: sqlite3.Connection, now: datetime) -> bool:
    """Whether a recent injury fetch succeeded (otherwise the app says injuries aren't connected)."""
    last = conn.execute(LAST_CHECK_SQL, (SOURCE.code,)).fetchone()
    return last is not None and last[0] is not None and abs(now - parse_iso(last[0])) <= FRESH_FOR


def active(conn: sqlite3.Connection, team_id: int | None = None, player_id: int | None = None) -> list[sqlite3.Row]:
    where, args = ["i.is_active = 1"], []
    if team_id is not None:
        where.append("i.team_id = ?")
        args.append(team_id)
    if player_id is not None:
        where.append("i.player_id = ?")
        args.append(player_id)
    return conn.execute(
        "SELECT i.*, p.nhl_player_id, p.full_name, p.position FROM injuries i JOIN players p ON p.id = i.player_id "
        f"WHERE {' AND '.join(where)} ORDER BY p.full_name",
        args,
    ).fetchall()


def admin(conn: sqlite3.Connection) -> dict[str, Any] | None:
    """Admin view: the last check and the names that didn't match an NHL player."""
    run = conn.execute(
        "SELECT r.status, r.meta, r.finished_at, r.error FROM ingestion_runs r JOIN data_sources s "
        "ON s.id = r.source_id WHERE s.code = ? AND r.job_name = 'injuries' ORDER BY r.id DESC LIMIT 1",
        (SOURCE.code,),
    ).fetchone()
    if run is None:
        return None
    unmatched = [
        json.loads(r[0])
        for r in conn.execute(
            "SELECT detail FROM data_quality_issues WHERE entity_type = 'injury_player_name' AND resolved_at IS NULL "
            "ORDER BY detected_at DESC LIMIT 200"
        )
    ]
    active_n = conn.execute("SELECT count(*) FROM injuries WHERE is_active = 1").fetchone()[0]
    return {
        "status": run["status"],
        "at": run["finished_at"],
        "detail": json.loads(run["meta"] or "{}"),
        "error": run["error"],
        "active": active_n,
        "unmatched": [{"name": u["name"], "suggestion": u.get("suggestion")} for u in unmatched],
    }
