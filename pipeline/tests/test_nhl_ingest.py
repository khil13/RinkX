"""Full NHL ingestion against recorded responses, as of 2026-03-10."""

import json
import math
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

from rinkx.config import REPO_ROOT
from rinkx.ingestion import context
from rinkx.ingestion.http import FetchError, FixtureFetcher, ReplayFetcher
from rinkx.ingestion.nhl.jobs import NhlOptions, run_nhl
from rinkx.publish.build import feed_statuses
from rinkx.store.db import connect, migrate
from rinkx.timeutil import slate_date

FIX = Path(__file__).parent / "fixtures/nhl"
NOW = datetime(2026, 3, 10, 18, 0, tzinfo=UTC)
TODAY = date(2026, 3, 10)


class DownFetcher(FixtureFetcher):
    def get_json(self, url: str) -> Any:
        self.calls += 1
        raise FetchError(url, "connection refused")


@pytest.fixture
def conn(tmp_path: Path):
    c = connect(tmp_path / "s.db")
    migrate(c, REPO_ROOT / "db")
    return c


def ingest(conn, fetcher=None) -> None:
    run_nhl(conn, fetcher or ReplayFetcher(FIX), NOW, NhlOptions(today=TODAY, boxscore_limit=100))


def one(conn, sql: str, *args):
    return conn.execute(sql, args).fetchone()[0]


def test_slate_date_uses_eastern_time_with_late_night_grace():
    assert slate_date(datetime(2026, 3, 10, 23, 0, tzinfo=UTC)) == date(2026, 3, 10)  # 7 pm ET
    assert slate_date(datetime(2026, 3, 11, 5, 30, tzinfo=UTC)) == date(2026, 3, 10)  # 1:30 am ET
    assert slate_date(datetime(2026, 3, 11, 12, 0, tzinfo=UTC)) == date(2026, 3, 11)  # 8 am ET


def test_haversine():
    # New York City Hall to Los Angeles City Hall is about 3,936 km.
    assert math.isclose(context.haversine_km(40.7128, -74.0060, 34.0522, -118.2437), 3936, rel_tol=0.01)
    assert context.haversine_km(45.0, -75.0, 45.0, -75.0) == 0


def test_ingests_schedule_teams_standings(conn):
    ingest(conn)
    assert one(conn, "SELECT count(*) FROM games WHERE game_date BETWEEN '2026-03-10' AND '2026-03-16'") == 56
    assert one(conn, "SELECT count(*) FROM teams WHERE is_active = 1") == 32
    assert one(conn, "SELECT count(*) FROM teams WHERE venue_lat IS NULL OR timezone IS NULL") == 0
    assert one(conn, "SELECT count(*) FROM team_standings") == 32
    assert one(conn, "SELECT count(DISTINCT division) FROM teams WHERE is_active = 1") == 4
    assert one(conn, "SELECT end_date FROM seasons WHERE id = 20252026") >= "2026-04-16"

    g = conn.execute(
        "SELECT g.status, g.home_score, g.away_score, g.ended_in, g.venue_name, h.abbrev, a.abbrev "
        "FROM games g JOIN teams h ON h.id = g.home_team_id JOIN teams a ON a.id = g.away_team_id "
        "WHERE g.nhl_game_id = 2025021012"
    ).fetchone()
    assert tuple(g) == ("final", 2, 1, "OT", "TD Garden", "BOS", "LAK")


def test_ingests_rosters_and_one_boxscore(conn):
    ingest(conn)
    bos = json.loads((FIX / "roster_BOS_20252026.json").read_text())
    n_bos = sum(len(bos[k]) for k in ("forwards", "defensemen", "goalies"))
    assert one(
        conn, "SELECT count(*) FROM players p JOIN teams t ON t.id = p.current_team_id WHERE t.abbrev='BOS'"
    ) == (n_bos)

    gid = one(conn, "SELECT id FROM games WHERE nhl_game_id = 2025021012")
    assert one(conn, "SELECT count(*) FROM team_game_stats WHERE game_id = ?", gid) == 2
    assert one(conn, "SELECT sum(goals) FROM player_game_stats WHERE game_id = ?", gid) == 3
    assert one(conn, "SELECT sum(shots) FROM player_game_stats WHERE game_id = ?", gid) == 39
    # Fields the box score doesn't provide stay NULL, never 0.
    assert one(conn, "SELECT count(*) FROM player_game_stats WHERE game_id = ? AND pp_points IS NOT NULL", gid) == 0
    assert one(conn, "SELECT count(*) FROM player_game_stats WHERE game_id = ? AND shot_attempts IS NOT NULL", gid) == 0
    # Only goalies who actually played get a row; the winner's shutout flag is decided.
    rows = conn.execute(
        "SELECT p.last_name, s.started, s.decision, s.saves, s.shutout, s.pulled FROM goalie_game_stats s "
        "JOIN players p ON p.id = s.player_id WHERE s.game_id = ? ORDER BY s.is_home",
        (gid,),
    ).fetchall()
    assert [tuple(r) for r in rows] == [("Kuemper", 1, "O", 21, 0, 0), ("Swayman", 1, "W", 15, 0, 0)]


def test_unrecorded_boxscores_make_a_partial_run_not_a_failure(conn):
    ingest(conn)
    status, meta = conn.execute(
        "SELECT status, meta FROM ingestion_runs WHERE job_name = 'nhl.boxscores' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert status == "partial"
    assert json.loads(meta)["error_count"] == 55
    feeds = {f.code: f for f in feed_statuses(conn, NOW)}
    assert feeds["schedule"].state == "ok" and feeds["stats"].state == "ok"
    assert feeds["odds"].state == "unavailable"


def test_context_matches_schedule(conn):
    ingest(conn)
    sched = json.loads((FIX / "schedule_2026-03-10.json").read_text())
    games = [(d["date"], g) for d in sched["gameWeek"] for g in d["games"]]
    # Pick a team that plays at least twice this week and check rest/B2B/travel by hand.
    from collections import defaultdict

    by_team = defaultdict(list)
    for day, g in games:
        for side in ("homeTeam", "awayTeam"):
            by_team[g[side]["abbrev"]].append((day, g))
    abbrev, played = next((a, p) for a, p in sorted(by_team.items()) if len(p) >= 2)
    (d1, g1), (d2, g2) = played[0], played[1]
    rest_expected = (date.fromisoformat(d2) - date.fromisoformat(d1)).days - 1
    venues = context.load_venues()
    loc = lambda g: venues[g["homeTeam"]["abbrev"]]  # noqa: E731
    km = context.haversine_km(loc(g1)["lat"], loc(g1)["lon"], loc(g2)["lat"], loc(g2)["lon"])

    row = conn.execute(
        "SELECT c.rest_days, c.is_back_to_back, c.travel_km, c.games_last_7d FROM game_team_context c "
        "JOIN games g ON g.id = c.game_id JOIN teams t ON t.id = c.team_id "
        "WHERE g.nhl_game_id = ? AND t.abbrev = ?",
        (g2["id"], abbrev),
    ).fetchone()
    assert row["rest_days"] == rest_expected
    assert row["is_back_to_back"] == int(rest_expected == 0)
    assert math.isclose(row["travel_km"], km, abs_tol=0.1)
    assert row["games_last_7d"] == 1

    first = conn.execute(
        "SELECT c.rest_days, c.travel_km FROM game_team_context c JOIN games g ON g.id = c.game_id "
        "JOIN teams t ON t.id = c.team_id WHERE g.nhl_game_id = ? AND t.abbrev = ?",
        (g1["id"], abbrev),
    ).fetchone()
    assert tuple(first) == (None, None)  # first known game: unknown, not zero


def test_idempotent(conn):
    ingest(conn)
    counts = [one(conn, f"SELECT count(*) FROM {t}") for t in ("games", "players", "player_game_stats", "teams")]
    ingest(conn)
    assert counts == [
        one(conn, f"SELECT count(*) FROM {t}") for t in ("games", "players", "player_game_stats", "teams")
    ]


def test_outage_is_reported_not_hidden(conn):
    ingest(conn, DownFetcher(FIX))
    assert one(conn, "SELECT count(*) FROM games") == 0
    assert one(conn, "SELECT count(*) FROM ingestion_runs WHERE status = 'failed'") >= 2
    feeds = {f.code: f for f in feed_statuses(conn, NOW)}
    assert feeds["schedule"].state == "failed"


def test_failed_schedule_weeks_are_partial_and_leave_dates_uncovered(conn):
    ingest(conn)
    status, meta = conn.execute(
        "SELECT status, meta FROM ingestion_runs WHERE job_name = 'nhl.schedule' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert status == "partial" and json.loads(meta)["error_count"] == 2  # weeks of 03-03 and 03-17
    covered = [r[0] for r in conn.execute("SELECT game_date FROM schedule_coverage ORDER BY game_date")]
    assert covered == [f"2026-03-{d}" for d in range(10, 17)]
