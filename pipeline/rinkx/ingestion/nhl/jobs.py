"""NHL ingestion jobs. Each job is logged in ingestion_runs; one failing job or item never
stops the others, and failures show up as failed/partial feeds on the site.

Per-run budget (hourly): ~6 schedule/standings calls + up to BOXSCORE_LIMIT box scores, plus
once a day 32 roster calls, plus once per season a ~30-call schedule backfill.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from rinkx.ingestion import context
from rinkx.ingestion.http import Fetcher, FetchError
from rinkx.ingestion.nhl import parse, store
from rinkx.ingestion.runs import SourceSpec, ingestion_run, last_success, register_source, source_enabled
from rinkx.timeutil import iso, parse_iso, slate_date, utcnow

log = logging.getLogger("rinkx.nhl")

WEB = "https://api-web.nhle.com/v1"
LICENSE = (
    "NHL public API (undocumented, no SLA). Used for personal, non-commercial research; "
    "polite request rate; nothing republished in readable form."
)
SCHEDULE_SOURCE = SourceSpec(
    "nhl_web_schedule", "NHL API: schedule, standings, rosters", "schedule", "free", WEB, LICENSE
)
STATS_SOURCE = SourceSpec("nhl_web_boxscore", "NHL API: box scores", "stats", "free", WEB, LICENSE)

BOXSCORE_LIMIT = 40
ROSTER_MAX_AGE = timedelta(hours=20)


@dataclass
class NhlOptions:
    today: date | None = None
    boxscore_limit: int = BOXSCORE_LIMIT
    roster_max_age: timedelta = ROSTER_MAX_AGE


def current_season(conn: sqlite3.Connection, today: date) -> int | None:
    row = conn.execute(
        "SELECT id FROM seasons WHERE start_date <= ? AND end_date >= ? ORDER BY id DESC LIMIT 1",
        (today.isoformat(), today.isoformat()),
    ).fetchone()
    if row:
        return int(row[0])
    row = conn.execute(
        "SELECT season_id FROM games WHERE game_date <= ? ORDER BY game_date DESC LIMIT 1", (today.isoformat(),)
    ).fetchone()
    return int(row[0]) if row else None


def _fetch_week(
    conn: sqlite3.Connection, fetcher: Fetcher, start: date, source_id: int
) -> tuple[int, parse.ScheduleWeek]:
    week = parse.parse_schedule(fetcher.get_json(f"{WEB}/schedule/{start.isoformat()}"))
    fetched = iso(utcnow())
    for g in week.games:
        store.upsert_game(conn, g, source_id, fetched)
        store.extend_season_end(conn, g.season_id, week.playoff_end or week.regular_season_end)
    store.mark_covered(conn, week.days, source_id, fetched)
    return len(week.games), week


def run_nhl(conn: sqlite3.Connection, fetcher: Fetcher, now: datetime, opts: NhlOptions | None = None) -> None:
    opts = opts or NhlOptions()
    today = opts.today or slate_date(now)
    sched = register_source(conn, SCHEDULE_SOURCE)
    stats_src = register_source(conn, STATS_SOURCE)
    conn.commit()
    if not source_enabled(conn, SCHEDULE_SOURCE.code):
        log.info("NHL schedule source disabled; skipping NHL jobs")
        return
    calls0 = fetcher.calls

    with ingestion_run(conn, sched, "nhl.seasons") as st:
        for s in parse.parse_standings_seasons(fetcher.get_json(f"{WEB}/standings-season")):
            store.upsert_season(conn, s)
            st.rows_upserted += 1
        st.http_calls = fetcher.calls - calls0

    calls0 = fetcher.calls
    with ingestion_run(conn, sched, "nhl.schedule") as st:
        weeks = (today - timedelta(days=7), today, today + timedelta(days=7))
        for start in weeks:
            try:
                n, _ = _fetch_week(conn, fetcher, start, sched)
                st.rows_upserted += n
            except (FetchError, parse.ParseError) as exc:
                st.errors.append(str(exc))
        if len(st.errors) == len(weeks):
            raise FetchError(f"{WEB}/schedule", "every schedule request failed")
        st.http_calls = fetcher.calls - calls0
        st.meta["window"] = [str(today - timedelta(days=7)), str(today + timedelta(days=13))]

    season = current_season(conn, today)
    if season is not None:
        _backfill_season(conn, fetcher, sched, season, today)

    calls0 = fetcher.calls
    with ingestion_run(conn, sched, "nhl.standings") as st:
        fetched = iso(utcnow())
        for row in parse.parse_standings(fetcher.get_json(f"{WEB}/standings/now")):
            if store.upsert_standing(conn, row, sched, fetched):
                st.rows_upserted += 1
            else:
                st.errors.append(f"standings for unknown team {row.abbrev}")
        st.http_calls = fetcher.calls - calls0

    store.apply_venues(conn, context.load_venues())
    conn.commit()

    last = last_success(conn, "nhl.rosters")
    if last is None or now - parse_iso(last) >= opts.roster_max_age:
        _sync_rosters(conn, fetcher, sched, today)

    if season is not None:
        _sync_boxscores(conn, fetcher, stats_src, season, opts.boxscore_limit, today)
        with ingestion_run(conn, sched, "nhl.context") as st:
            st.rows_upserted = context.recompute_context(conn, season)


def _backfill_season(conn: sqlite3.Connection, fetcher: Fetcher, source_id: int, season: int, today: date) -> None:
    """Once per season: walk the whole schedule so rest/back-to-back context is complete."""
    job = f"nhl.schedule_backfill:{season}"
    done = conn.execute(
        "SELECT 1 FROM ingestion_runs WHERE job_name = ? AND status = 'succeeded' LIMIT 1", (job,)
    ).fetchone()
    if done:  # only a fully successful backfill counts; a partial one is retried next run
        return
    row = conn.execute("SELECT start_date, end_date FROM seasons WHERE id = ?", (season,)).fetchone()
    start, end = date.fromisoformat(row[0]), date.fromisoformat(row[1])
    end = max(end, today + timedelta(days=14))
    calls0 = fetcher.calls
    with ingestion_run(conn, source_id, job) as st:
        d = start
        while d <= end:
            try:
                n, week = _fetch_week(conn, fetcher, d, source_id)
                st.rows_upserted += n
                if week.regular_season_end:
                    end = max(end, date.fromisoformat(week.regular_season_end))
            except (FetchError, parse.ParseError) as exc:
                st.errors.append(str(exc))
            d += timedelta(days=7)
        st.http_calls = fetcher.calls - calls0


def _sync_rosters(conn: sqlite3.Connection, fetcher: Fetcher, source_id: int, today: date) -> None:
    teams = conn.execute(
        "SELECT id, abbrev FROM teams WHERE is_active = 1 AND id IN "
        "(SELECT home_team_id FROM games UNION SELECT away_team_id FROM games) ORDER BY abbrev"
    ).fetchall()
    calls0 = fetcher.calls
    with ingestion_run(conn, source_id, "nhl.rosters") as st:
        for team_id, abbrev in teams:
            try:
                players = parse.parse_roster(fetcher.get_json(f"{WEB}/roster/{abbrev}/current"), abbrev)
            except (FetchError, parse.ParseError) as exc:
                st.errors.append(str(exc))
                continue
            fetched = iso(utcnow())
            for p in players:
                store.upsert_player(conn, p, source_id, fetched, today.isoformat())
                st.rows_upserted += 1
            store.mark_roster(conn, team_id, {p.nhl_player_id for p in players})
            conn.commit()
        st.http_calls = fetcher.calls - calls0


def _sync_boxscores(
    conn: sqlite3.Connection, fetcher: Fetcher, source_id: int, season: int, limit: int, today: date
) -> None:
    pending = conn.execute(
        """SELECT g.id, g.nhl_game_id FROM games g
           WHERE g.season_id = ? AND g.status = 'final'
             AND NOT EXISTS (SELECT 1 FROM team_game_stats t WHERE t.game_id = g.id)
           ORDER BY g.start_time_utc DESC LIMIT ?""",
        (season, limit),
    ).fetchall()
    calls0 = fetcher.calls
    with ingestion_run(conn, source_id, "nhl.boxscores") as st:
        st.meta["pending"] = len(pending)
        for game_id, nhl_game_id in pending:
            try:
                box = parse.parse_boxscore(fetcher.get_json(f"{WEB}/gamecenter/{nhl_game_id}/boxscore"))
                if box.game_state not in parse.FINAL_STATES:
                    continue
                _ensure_players(conn, fetcher, box, source_id, today)
                st.rows_upserted += store.write_boxscore(conn, game_id, box, source_id, iso(utcnow()))
                st.rows_read += 1
                conn.commit()
            except (FetchError, parse.ParseError, KeyError) as exc:
                conn.rollback()
                st.errors.append(f"game {nhl_game_id}: {exc}")
        st.http_calls = fetcher.calls - calls0


def _ensure_players(
    conn: sqlite3.Connection, fetcher: Fetcher, box: parse.Boxscore, source_id: int, today: date
) -> None:
    """Players can appear in a box score before RinkX has seen them on a roster (call-ups,
    trades). Load them from their player page rather than guessing from the box score."""
    ids = {s.nhl_player_id for s in box.skaters} | {g.nhl_player_id for g in box.goalies}
    for pid in sorted(ids):
        if store.player_id(conn, pid) is None:
            rec = parse.parse_player_landing(fetcher.get_json(f"{WEB}/player/{pid}/landing"))
            store.upsert_player(conn, rec, source_id, iso(utcnow()), today.isoformat())
