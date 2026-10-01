"""Upserts of parsed NHL records into the store."""

from __future__ import annotations

import sqlite3

from rinkx.ingestion.nhl.parse import Boxscore, GameRec, PlayerRec, SeasonRec, StandingRec, TeamRef

PROVENANCE = "official"
QUALITY = 0.95  # official box score; some fields (TOI splits, assist types) are absent, not wrong


def upsert_season(conn: sqlite3.Connection, s: SeasonRec) -> None:
    conn.execute(
        "INSERT INTO seasons (id, start_date, end_date) VALUES (?,?,?) "
        "ON CONFLICT(id) DO UPDATE SET start_date = excluded.start_date, "
        "end_date = max(seasons.end_date, excluded.end_date)",
        (s.id, s.start_date, s.end_date),
    )


def extend_season_end(conn: sqlite3.Connection, season_id: int, end_date: str | None) -> None:
    if end_date:
        conn.execute("UPDATE seasons SET end_date = max(end_date, ?) WHERE id = ?", (end_date, season_id))


def upsert_team(conn: sqlite3.Connection, t: TeamRef, source_id: int, fetched_at: str) -> int:
    # An abbreviation can move to a new franchise id (e.g. UTA). Retire the old holder first so
    # the UNIQUE(abbrev) constraint never makes us overwrite the wrong team.
    conn.execute(
        "UPDATE teams SET abbrev = abbrev || '-' || nhl_team_id, is_active = 0 WHERE abbrev = ? AND nhl_team_id <> ?",
        (t.abbrev, t.nhl_team_id),
    )
    conn.execute(
        "INSERT INTO teams (nhl_team_id, abbrev, name, location, is_active, source_id, fetched_at) "
        "VALUES (?,?,?,?,1,?,?) ON CONFLICT(nhl_team_id) DO UPDATE SET abbrev = excluded.abbrev, "
        "name = excluded.name, location = excluded.location, is_active = 1, fetched_at = excluded.fetched_at",
        (t.nhl_team_id, t.abbrev, t.name, t.location, source_id, fetched_at),
    )
    return team_id(conn, t.nhl_team_id)


def team_id(conn: sqlite3.Connection, nhl_team_id: int) -> int:
    row = conn.execute("SELECT id FROM teams WHERE nhl_team_id = ?", (nhl_team_id,)).fetchone()
    if row is None:
        raise KeyError(f"unknown NHL team id {nhl_team_id}")
    return int(row[0])


def apply_venues(conn: sqlite3.Connection, venues: dict[str, dict[str, object]]) -> None:
    for abbrev, v in venues.items():
        conn.execute(
            "UPDATE teams SET venue_lat = ?, venue_lon = ?, timezone = ? WHERE abbrev = ?",
            (v["lat"], v["lon"], v["tz"], abbrev),
        )


def upsert_game(conn: sqlite3.Connection, g: GameRec, source_id: int, fetched_at: str) -> int:
    home = upsert_team(conn, g.home, source_id, fetched_at)
    away = upsert_team(conn, g.away, source_id, fetched_at)
    conn.execute(
        "INSERT OR IGNORE INTO seasons (id, start_date, end_date) VALUES (?, ?, ?)",
        (g.season_id, g.game_date, g.game_date),
    )
    conn.execute(
        """INSERT INTO games (nhl_game_id, season_id, game_type, game_date, start_time_utc, home_team_id,
             away_team_id, venue_name, is_neutral_site, status, period, home_score, away_score, ended_in,
             source_id, fetched_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(nhl_game_id) DO UPDATE SET game_date = excluded.game_date,
             start_time_utc = excluded.start_time_utc, venue_name = excluded.venue_name,
             is_neutral_site = excluded.is_neutral_site, status = excluded.status, period = excluded.period,
             home_score = excluded.home_score, away_score = excluded.away_score, ended_in = excluded.ended_in,
             fetched_at = excluded.fetched_at""",
        (
            g.nhl_game_id,
            g.season_id,
            g.game_type,
            g.game_date,
            g.start_time_utc,
            home,
            away,
            g.venue_name,
            int(g.neutral_site),
            g.status,
            g.period,
            g.home_score,
            g.away_score,
            g.ended_in,
            source_id,
            fetched_at,
        ),
    )
    return int(conn.execute("SELECT id FROM games WHERE nhl_game_id = ?", (g.nhl_game_id,)).fetchone()[0])


def upsert_standing(conn: sqlite3.Connection, s: StandingRec, source_id: int, fetched_at: str) -> bool:
    row = conn.execute("SELECT id FROM teams WHERE abbrev = ?", (s.abbrev,)).fetchone()
    if row is None:
        return False
    conn.execute("UPDATE teams SET conference = ?, division = ? WHERE id = ?", (s.conference, s.division, row[0]))
    conn.execute(
        "INSERT OR IGNORE INTO seasons (id, start_date, end_date) VALUES (?, ?, ?)",
        (s.season_id, s.as_of_date, s.as_of_date),
    )
    conn.execute(
        """INSERT OR REPLACE INTO team_standings (team_id, season_id, as_of_date, games_played, wins, losses,
             ot_losses, points, goals_for, goals_against, home_wins, home_losses, home_ot_losses, road_wins,
             road_losses, road_ot_losses, l10_wins, l10_losses, l10_ot_losses, streak_code, streak_count,
             conference, division, provenance, source_id, fetched_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            row[0],
            s.season_id,
            s.as_of_date,
            s.games_played,
            s.wins,
            s.losses,
            s.ot_losses,
            s.points,
            s.goals_for,
            s.goals_against,
            s.home_wins,
            s.home_losses,
            s.home_ot_losses,
            s.road_wins,
            s.road_losses,
            s.road_ot_losses,
            s.l10_wins,
            s.l10_losses,
            s.l10_ot_losses,
            s.streak_code,
            s.streak_count,
            s.conference,
            s.division,
            PROVENANCE,
            source_id,
            fetched_at,
        ),
    )
    return True


def player_id(conn: sqlite3.Connection, nhl_player_id: int) -> int | None:
    row = conn.execute("SELECT id FROM players WHERE nhl_player_id = ?", (nhl_player_id,)).fetchone()
    return int(row[0]) if row else None


def upsert_player(conn: sqlite3.Connection, p: PlayerRec, source_id: int, fetched_at: str, today: str) -> int:
    team = None
    if p.team_abbrev:
        row = conn.execute("SELECT id FROM teams WHERE abbrev = ?", (p.team_abbrev,)).fetchone()
        team = int(row[0]) if row else None
    conn.execute(
        """INSERT INTO players (nhl_player_id, first_name, last_name, position, shoots_catches, birth_date,
             height_cm, weight_kg, current_team_id, sweater_number, is_active, source_id, fetched_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,1,?,?)
           ON CONFLICT(nhl_player_id) DO UPDATE SET first_name = excluded.first_name,
             last_name = excluded.last_name, position = excluded.position,
             shoots_catches = excluded.shoots_catches, birth_date = excluded.birth_date,
             height_cm = excluded.height_cm, weight_kg = excluded.weight_kg,
             current_team_id = coalesce(excluded.current_team_id, players.current_team_id),
             sweater_number = excluded.sweater_number, is_active = 1, fetched_at = excluded.fetched_at""",
        (
            p.nhl_player_id,
            p.first_name,
            p.last_name,
            p.position,
            p.shoots_catches,
            p.birth_date,
            p.height_cm,
            p.weight_kg,
            team,
            p.sweater_number,
            source_id,
            fetched_at,
        ),
    )
    pid = player_id(conn, p.nhl_player_id)
    assert pid is not None
    if team is not None:
        _record_stint(conn, pid, team, source_id, today)
    return pid


def _record_stint(conn: sqlite3.Connection, pid: int, team: int, source_id: int, today: str) -> None:
    """Stints record when RinkX first observed a player on a team (not necessarily the
    transaction date). A team change closes the open stint and opens a new one."""
    open_stint = conn.execute(
        "SELECT team_id, start_date FROM player_team_stints WHERE player_id = ? AND end_date IS NULL", (pid,)
    ).fetchone()
    if open_stint and open_stint[0] == team:
        return
    if open_stint:
        conn.execute(
            "UPDATE player_team_stints SET end_date = ? WHERE player_id = ? AND start_date = ?",
            (today, pid, open_stint[1]),
        )
    conn.execute(
        "INSERT OR IGNORE INTO player_team_stints (player_id, team_id, start_date, source_id) VALUES (?,?,?,?)",
        (pid, team, today, source_id),
    )


def mark_roster(conn: sqlite3.Connection, team: int, nhl_player_ids: set[int]) -> int:
    """Players listed for a team who are no longer on its roster lose current_team_id."""
    placeholders = ",".join("?" * len(nhl_player_ids)) or "NULL"
    cur = conn.execute(
        f"UPDATE players SET current_team_id = NULL WHERE current_team_id = ? "
        f"AND nhl_player_id NOT IN ({placeholders})",
        (team, *nhl_player_ids),
    )
    return cur.rowcount


def write_boxscore(conn: sqlite3.Connection, game_id: int, box: Boxscore, source_id: int, fetched_at: str) -> int:
    """Replace this game's box-score rows. Returns rows written. Players must already exist."""
    teams = {t.nhl_team_id: team_id(conn, t.nhl_team_id) for t in box.teams}
    home = next(teams[t.nhl_team_id] for t in box.teams if t.is_home)
    away = next(teams[t.nhl_team_id] for t in box.teams if not t.is_home)
    opp = {home: away, away: home}
    conn.execute("DELETE FROM player_game_stats WHERE game_id = ?", (game_id,))
    conn.execute("DELETE FROM goalie_game_stats WHERE game_id = ?", (game_id,))
    conn.execute("DELETE FROM team_game_stats WHERE game_id = ?", (game_id,))
    n = 0
    for t in box.teams:
        tid = teams[t.nhl_team_id]
        conn.execute(
            "INSERT INTO team_game_stats (team_id, game_id, opponent_team_id, is_home, goals_for, shots_for, "
            "provenance, source_id, fetched_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (tid, game_id, opp[tid], int(t.is_home), t.goals, t.shots, PROVENANCE, source_id, fetched_at),
        )
        n += 1
    for s in box.skaters:
        pid = player_id(conn, s.nhl_player_id)
        if pid is None:
            raise KeyError(f"player {s.nhl_player_id} must be loaded before the box score")
        tid = teams[s.nhl_team_id]
        extra = "{}" if s.faceoff_pct is None or s.position != "C" else f'{{"faceoff_pct": {s.faceoff_pct}}}'
        conn.execute(
            """INSERT INTO player_game_stats (player_id, game_id, team_id, opponent_team_id, is_home, toi_s,
                 shifts, goals, assists, pp_goals, shots, hits, blocked_shots, takeaways, giveaways, pim,
                 plus_minus, extra, provenance, quality, source_id, fetched_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                pid,
                game_id,
                tid,
                opp[tid],
                int(s.is_home),
                s.toi_s,
                s.shifts,
                s.goals,
                s.assists,
                s.pp_goals,
                s.shots,
                s.hits,
                s.blocked_shots,
                s.takeaways,
                s.giveaways,
                s.pim,
                s.plus_minus,
                extra,
                PROVENANCE,
                QUALITY,
                source_id,
                fetched_at,
            ),
        )
        n += 1
    for g in box.goalies:
        pid = player_id(conn, g.nhl_player_id)
        if pid is None:
            raise KeyError(f"goalie {g.nhl_player_id} must be loaded before the box score")
        tid = teams[g.nhl_team_id]
        played = (g.toi_s or 0) > 0
        if not played:
            continue  # dressed backup who never entered: not a goalie appearance
        teammates = [o for o in box.goalies if o.nhl_team_id == g.nhl_team_id and o is not g]
        relieved = any((o.toi_s or 0) > 0 for o in teammates)
        # Box scores are written only for final games, so a shutout is decidable here.
        shutout = int(g.decision == "W" and g.goals_against == 0 and not relieved)
        conn.execute(
            """INSERT INTO goalie_game_stats (player_id, game_id, team_id, opponent_team_id, is_home, started,
                 pulled, toi_s, shots_against, saves, goals_against, ev_shots_against, ev_saves,
                 pp_shots_against, pp_saves, sh_shots_against, sh_saves, decision, shutout,
                 provenance, quality, source_id, fetched_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                pid,
                game_id,
                tid,
                opp[tid],
                int(g.is_home),
                int(g.started),
                int(g.started and relieved),
                g.toi_s,
                g.shots_against,
                g.saves,
                g.goals_against,
                g.ev_shots_against,
                g.ev_saves,
                g.pp_shots_against,
                g.pp_saves,
                g.sh_shots_against,
                g.sh_saves,
                g.decision,
                shutout,
                PROVENANCE,
                QUALITY,
                source_id,
                fetched_at,
            ),
        )
        n += 1
    return n
