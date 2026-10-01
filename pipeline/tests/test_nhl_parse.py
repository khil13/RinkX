"""Contract tests: parsers against real, recorded NHL API responses (tests/fixtures/nhl)."""

import json
from pathlib import Path

import pytest

from rinkx.ingestion.nhl import parse

FIX = Path(__file__).parent / "fixtures/nhl"


def load(name: str) -> dict:
    return json.loads((FIX / name).read_text())


def test_helpers():
    assert parse.toi_seconds("17:04") == 1024
    assert parse.toi_seconds("60:18") == 3618
    assert parse.toi_seconds(None) is None
    assert parse.saves_of("18/20") == (18, 20)
    assert parse.saves_of("0/0") == (0, 0)
    with pytest.raises(parse.ParseError):
        parse.toi_seconds("17.04")
    assert parse.game_status("OFF", "OK") == "final"
    assert parse.game_status("FUT", "PPD") == "postponed"
    assert parse.game_status("CRIT", "OK") == "live"


def test_completed_week_schedule():
    week = parse.parse_schedule(load("schedule_2026-03-10.json"))
    assert len(week.games) == 56
    assert all(g.status == "final" and g.game_type == "R" for g in week.games)
    assert all(g.home_score is not None and g.away_score is not None for g in week.games)
    assert {g.ended_in for g in week.games} <= {"REG", "OT", "SO"}

    g = next(g for g in week.games if g.nhl_game_id == 2025021012)
    assert (g.away.abbrev, g.home.abbrev) == ("LAK", "BOS")
    assert (g.away_score, g.home_score, g.ended_in) == (1, 2, "OT")
    assert g.game_date == "2026-03-10" and g.start_time_utc == "2026-03-10T23:00:00Z"
    assert g.venue_name == "TD Garden" and g.neutral_site is False
    assert g.home == parse.TeamRef(6, "BOS", "Boston", "Bruins")


def test_upcoming_week_schedule_has_no_scores_and_all_teams():
    week = parse.parse_schedule(load("schedule_now.json"))
    assert week.games and all(g.status == "scheduled" for g in week.games)
    assert all(g.home_score is None and g.ended_in is None and g.period is None for g in week.games)
    teams = {t.abbrev: t.nhl_team_id for g in week.games for t in (g.home, g.away)}
    assert len(teams) == 32
    assert teams["UTA"] == 68  # the schedule disambiguates UTA (stats/team lists two ids)
    assert week.regular_season_start == "2026-09-29"


def test_seasons_and_standings():
    seasons = {s.id: s for s in parse.parse_standings_seasons(load("standings_season.json"))}
    assert seasons[20252026].start_date == "2025-10-07"
    assert seasons[20252026].end_date == "2026-04-17"

    rows = parse.parse_standings(load("standings_2026-03-10.json"))
    assert len(rows) == 32
    for r in rows:
        assert r.points == 2 * r.wins + r.ot_losses
        assert r.games_played == r.wins + r.losses + r.ot_losses
        assert r.conference in ("Eastern", "Western")
    assert len({r.division for r in rows}) == 4


def test_roster_and_player_landing():
    roster = parse.parse_roster(load("roster_TOR_current.json"), "TOR")
    assert len(roster) >= 20
    assert {p.position for p in roster} <= {"C", "L", "R", "D", "G"}
    assert sum(p.position == "G" for p in roster) >= 2
    assert len({p.nhl_player_id for p in roster}) == len(roster)

    domi = parse.parse_player_landing(load("player_8477503.json"))
    assert (domi.first_name, domi.last_name, domi.position, domi.team_abbrev) == ("Max", "Domi", "C", "TOR")
    goalie = parse.parse_player_landing(load("player_8476932.json"))
    assert goalie.position == "G"


def test_boxscore():
    box = parse.parse_boxscore(load("boxscore_2025021012.json"))
    home = next(t for t in box.teams if t.is_home)
    away = next(t for t in box.teams if not t.is_home)
    assert (home.nhl_team_id, home.goals, home.shots) == (6, 2, 23)
    assert (away.nhl_team_id, away.goals, away.shots) == (26, 1, 16)

    # Box score internally consistent: skater goals sum to team goals (no shootout here).
    for t in box.teams:
        assert sum(s.goals or 0 for s in box.skaters if s.nhl_team_id == t.nhl_team_id) == t.goals
        assert sum(s.shots or 0 for s in box.skaters if s.nhl_team_id == t.nhl_team_id) == t.shots

    starters = [g for g in box.goalies if g.started]
    assert len(starters) == 2
    bos = next(g for g in starters if g.is_home)
    assert (bos.decision, bos.saves, bos.shots_against, bos.goals_against) == ("W", 15, 16, 1)
    assert (bos.ev_saves, bos.ev_shots_against, bos.sh_shots_against) == (14, 15, 1)
    lak = next(g for g in starters if not g.is_home)
    assert (lak.decision, lak.pp_shots_against) == ("O", 3)
    for g in starters:
        assert g.shots_against == home.shots if not g.is_home else g.shots_against == away.shots
        assert g.saves is not None and g.ev_saves is not None
        assert (g.ev_saves or 0) + (g.pp_saves or 0) + (g.sh_saves or 0) == g.saves


def test_play_by_play_reconciles_with_box_score():
    pbp = parse.parse_play_by_play(load("pbp_2025021012.json"))
    box = parse.parse_boxscore(load("boxscore_2025021012.json"))
    assert (pbp.home_team_id, pbp.away_team_id) == (6, 26)

    # Goals and SOG per player from play-by-play match the official box score exactly.
    for s in box.skaters:
        mine = [e for e in pbp.shots if e.shooter_id == s.nhl_player_id]
        assert sum(e.event_type == "goal" for e in mine) == s.goals, s
        assert sum(e.event_type in ("shot", "goal") for e in mine) == s.shots, s
    # Assists split into primary/secondary still sum to the box-score assists.
    a1 = {g.assist1_id for g in pbp.goals} - {None}
    for s in box.skaters:
        n = sum(g.assist1_id == s.nhl_player_id for g in pbp.goals) + sum(
            g.assist2_id == s.nhl_player_id for g in pbp.goals
        )
        assert n == s.assists, s
    assert len(a1) == 3


def test_play_by_play_strength_coords_and_ot():
    pbp = parse.parse_play_by_play(load("pbp_2025021012.json"))
    ot = [g for g in pbp.goals if g.strength_state == "3v3"]
    assert len(ot) == 1 and ot[0].scorer_id == 8479325  # McAvoy, 3-on-3 overtime winner
    assert {e.event_type for e in pbp.shots} == {"shot", "miss", "block", "goal"}
    # Normalized coordinates: shots on goal come from the attacking half almost always.
    on_goal = [e for e in pbp.shots if e.event_type in ("shot", "goal") and e.x is not None]
    assert sum(e.x > 0 for e in on_goal) / len(on_goal) > 0.9
    # Opponent blocks name a blocker on the other team; teammate blocks (2 in this game)
    # credit nobody, matching the official box score.
    team_of = {r["playerId"]: r["teamId"] for r in load("pbp_2025021012.json")["rosterSpots"]}
    blocks = [e for e in pbp.shots if e.event_type == "block"]
    assert sum(e.teammate_block for e in blocks) == 2
    for e in blocks:
        if e.teammate_block:
            assert e.blocker_id is None
        else:
            assert e.blocker_id is not None and team_of[e.blocker_id] != e.shooter_team_id
    box = parse.parse_boxscore(load("boxscore_2025021012.json"))
    for s in box.skaters:
        assert sum(e.blocker_id == s.nhl_player_id for e in blocks) == s.blocked_shots, s
    with pytest.raises(parse.ParseError):
        parse._strength("15x1", True)
    assert parse._strength("1560", False) == ("5v6", True)  # home pulled its goalie


def test_stats_reports_cover_every_skater_and_add_up():
    def rows(report):
        out = []
        start = 0
        while True:
            page, total = parse.parse_game_report(load(f"stats_skater_{report}_2026-03-10_{start}.json"))
            out += page
            start += 100
            if start >= total:
                return {(r["playerId"], r["gameId"]): r for r in out}

    toi, summary, realtime = rows("timeonice"), rows("summary"), rows("realtime")
    assert len(toi) == len(summary) == len(realtime) == 468
    box = parse.parse_boxscore(load("boxscore_2025021012.json"))
    for s in box.skaters:
        key = (s.nhl_player_id, 2025021012)
        t, m, r = toi[key], summary[key], realtime[key]
        assert t["evTimeOnIce"] + t["ppTimeOnIce"] + t["shTimeOnIce"] == t["timeOnIce"] == s.toi_s
        assert (m["goals"], m["assists"], m["shots"], m["ppGoals"]) == (s.goals, s.assists, s.shots, s.pp_goals)
        assert r["totalShotAttempts"] == s.shots + r["missedShots"] + r["shotAttemptsBlocked"]
        assert r["hits"] == s.hits and r["blockedShots"] == s.blocked_shots
