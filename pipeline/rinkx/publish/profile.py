"""Player prop profiles (shooting, usage, matchup), goalie impact numbers, and shot-map events.

Everything is counted from stored box scores, shift-chart lines and play-by-play; a number that
can't be computed is None ("insufficient data" in the app), never filled in. Shot locations
come from NHL play-by-play coordinates (feet, normalized so every attempt goes toward +x, net
at x = 89). "High danger" is RinkX's simple definition, stated in the app: within 25 ft of the
net and inside the faceoff dots (|y| <= 22 ft).
"""

from __future__ import annotations

import math
import sqlite3
from datetime import date, timedelta
from typing import Any

from rinkx.pricing.scores import opponent_by_position

NET_X = 89
HD_DIST = 25.0
HD_WIDTH = 22.0


def _avg(xs: list[float]) -> float | None:
    return round(sum(xs) / len(xs), 2) if xs else None


def skater_profile(conn: sqlite3.Connection, player: int, season: int | None, today: str) -> dict[str, Any]:
    rows = conn.execute(
        "SELECT g.id, g.game_date, s.team_id, s.opponent_team_id, s.is_home, s.toi_s, s.pp_toi_s, s.shots, "
        "s.shot_attempts FROM player_game_stats s JOIN games g ON g.id = s.game_id WHERE s.player_id = ? "
        "AND g.season_id = ? AND g.status = 'final' AND coalesce(s.toi_s, 1) > 0 ORDER BY g.start_time_utc DESC",
        (player, season),
    ).fetchall()
    shots = [float(r["shots"]) for r in rows if r["shots"] is not None]
    toi_h = sum((r["toi_s"] or 0) for r in rows if r["shots"] is not None) / 3600
    attempts = [float(r["shot_attempts"]) for r in rows if r["shot_attempts"] is not None]
    team_shots = 0
    for r in rows:
        ts = conn.execute(
            "SELECT shots_for FROM team_game_stats WHERE game_id = ? AND team_id = ?", (r["id"], r["team_id"])
        ).fetchone()
        team_shots += (ts[0] or 0) if ts else 0
    home = [float(r["shots"]) for r in rows if r["is_home"] and r["shots"] is not None]
    away = [float(r["shots"]) for r in rows if not r["is_home"] and r["shots"] is not None]
    shooting = {
        "games": len(shots),
        "sog_per_game": _avg(shots),
        "sog_per_60": round(sum(shots) / toi_h, 2) if toi_h > 0 else None,
        "attempts_per_game": _avg(attempts),
        "shot_share": round(sum(shots) / team_shots, 4) if team_shots else None,
        "recent_sog": [int(x) for x in shots[:10]],
        "l5_avg": _avg(shots[:5]),
        "l10_avg": _avg(shots[:10]),
        "home_avg": _avg(home),
        "away_avg": _avg(away),
    }
    tois = [r["toi_s"] for r in rows if r["toi_s"]]
    deploy = conn.execute(
        "SELECT g.game_date, c.unit, (SELECT u.unit FROM powerplay_units u WHERE u.snapshot_id = s.id "
        "AND u.player_id = c.player_id AND u.unit IN ('PP1','PP2')) AS pp FROM line_combinations c "
        "JOIN lineup_snapshots s ON s.id = c.snapshot_id JOIN games g ON g.id = s.game_id WHERE c.player_id = ? "
        "AND s.status = 'actual' ORDER BY g.start_time_utc DESC LIMIT 5",
        (player,),
    ).fetchall()
    usage = {
        "toi_avg_s": round(sum(tois) / len(tois)) if tois else None,
        "toi_l5_s": round(sum(tois[:5]) / len(tois[:5])) if tois else None,
        "pp_toi_avg_s": _avg([float(r["pp_toi_s"]) for r in rows if r["pp_toi_s"] is not None]),
        "recent": [{"date": d["game_date"], "line": d["unit"], "pp": d["pp"]} for d in deploy],
    }
    nxt = conn.execute(
        "SELECT g.*, CASE WHEN g.home_team_id = p.current_team_id THEN g.away_team_id ELSE g.home_team_id END AS opp, "
        "g.home_team_id = p.current_team_id AS home FROM games g JOIN players p ON p.id = ? WHERE g.game_date >= ? "
        "AND g.status IN ('scheduled','pregame') AND p.current_team_id IN (g.home_team_id, g.away_team_id) "
        "ORDER BY g.start_time_utc LIMIT 1",
        (player, today),
    ).fetchone()
    matchup = None
    if nxt is not None:
        opp = nxt["opp"]
        recent = conn.execute(
            "SELECT t.shots_for FROM team_game_stats t JOIN games g ON g.id = t.game_id WHERE t.opponent_team_id = ? "
            "AND g.status = 'final' ORDER BY g.start_time_utc DESC LIMIT 20",
            (opp,),
        ).fetchall()
        lg = conn.execute(
            "SELECT avg(t.shots_for) FROM team_game_stats t JOIN games g ON g.id = t.game_id WHERE g.season_id = ? "
            "AND g.status = 'final'",
            (season,),
        ).fetchone()[0]
        pos = conn.execute("SELECT position FROM players WHERE id = ?", (player,)).fetchone()[0]
        vs_pos = opponent_by_position(conn, opp, "D" if pos == "D" else "F", nxt["game_date"])
        allowed_avg = _avg([float(r[0]) for r in recent if r[0] is not None])
        matchup = {
            "opponent": conn.execute("SELECT abbrev FROM teams WHERE id = ?", (opp,)).fetchone()[0],
            "home": bool(nxt["home"]),
            "date": nxt["game_date"],
            "opp_sog_allowed": allowed_avg,
            "league_sog": round(lg, 2) if lg else None,
            "opp_vs_position": round(vs_pos, 3) if vs_pos else None,
            "position_group": "defence" if pos == "D" else "forwards",
        }
    shooting |= expected_goals(conn, player, season)
    return {"shooting": shooting, "usage": usage, "matchup": matchup, "shot_map": shot_events(conn, player, season)}


def expected_goals(conn: sqlite3.Connection, player: int, season: int | None) -> dict[str, Any]:
    """His season xG (RinkX model, unblocked attempts) next to his goals in the same games.
    None when no game of his has scored attempts (the model hasn't passed its test, or no data)."""
    row = conn.execute(
        "SELECT count(DISTINCT e.game_id), sum(e.xg), sum(e.event_type = 'goal') FROM pbp_shot_events e "
        "JOIN games g ON g.id = e.game_id WHERE e.shooter_id = ? AND g.season_id = ? AND e.xg IS NOT NULL",
        (player, season),
    ).fetchone()
    games = conn.execute(
        "SELECT count(*) FROM player_game_stats s JOIN games g ON g.id = s.game_id WHERE s.player_id = ? "
        "AND g.season_id = ? AND g.status = 'final' AND EXISTS (SELECT 1 FROM pbp_shot_events e "
        "WHERE e.game_id = g.id AND e.xg IS NOT NULL)",
        (player, season),
    ).fetchone()[0]
    if not games or row[1] is None:
        return {"xg": None, "xg_goals": None, "xg_games": games or 0, "ixg_per_game": None}
    return {
        "xg": round(row[1], 2),
        "xg_goals": int(row[2] or 0),
        "xg_games": int(games),
        "ixg_per_game": round(row[1] / games, 3),
    }


def shot_events(conn: sqlite3.Connection, player: int, season: int | None) -> dict[str, Any]:
    rows = conn.execute(
        "SELECT g.game_date, e.event_type, e.x_coord, e.y_coord, e.strength_state, e.xg, t.abbrev AS opp "
        "FROM pbp_shot_events e JOIN games g ON g.id = e.game_id JOIN teams t ON t.id = "
        "CASE WHEN g.home_team_id = e.team_id THEN g.away_team_id ELSE g.home_team_id END "
        "WHERE e.shooter_id = ? AND g.season_id = ? ORDER BY g.start_time_utc DESC",
        (player, season),
    ).fetchall()
    events = []
    dates: list[str] = []
    for r in rows:
        if r["game_date"] not in dates:
            dates.append(r["game_date"])
        if r["x_coord"] is None or r["y_coord"] is None:
            continue
        dist = math.hypot(NET_X - r["x_coord"], r["y_coord"])
        events.append(
            {
                "g": dates.index(r["game_date"]),  # 0 = most recent game
                "t": r["event_type"],
                "x": r["x_coord"],
                "y": r["y_coord"],
                "hd": dist <= HD_DIST and abs(r["y_coord"]) <= HD_WIDTH,
                "xg": None if r["xg"] is None else round(r["xg"], 3),
                "opp": r["opp"],
            }
        )
    return {
        "games": dates,
        "events": events,
        "with_coordinates": len(events),
        "without_coordinates": len(rows) - len(events),
        "definition": f"High danger: within {HD_DIST:g} ft of the net and |y| <= {HD_WIDTH:g} ft (RinkX definition).",
    }


def goalie_impact(conn: sqlite3.Connection, goalie: int, before: str, season: int | None) -> dict[str, Any] | None:
    """Starts this season before `before`: save %, shots and goals against per start, recent workload."""
    rows = conn.execute(
        "SELECT g.game_date, s.shots_against, s.saves, s.goals_against FROM goalie_game_stats s JOIN games g "
        "ON g.id = s.game_id WHERE s.player_id = ? AND s.started = 1 AND g.season_id = ? AND g.game_date < ? "
        "AND g.status = 'final' ORDER BY g.start_time_utc DESC",
        (goalie, season, before),
    ).fetchall()
    rows = [r for r in rows if r["shots_against"] is not None and r["saves"] is not None]
    if not rows:
        return None
    sa = sum(r["shots_against"] for r in rows)
    sv = sum(r["saves"] for r in rows)
    last5 = rows[:5]
    sa5, sv5 = sum(r["shots_against"] for r in last5), sum(r["saves"] for r in last5)
    lg = conn.execute(
        "SELECT sum(s.saves) * 1.0 / sum(s.shots_against) FROM goalie_game_stats s JOIN games g ON g.id = s.game_id "
        "WHERE g.season_id = ? AND g.game_date < ? AND s.shots_against > 0",
        (season, before),
    ).fetchone()[0]
    week = (date.fromisoformat(before) - timedelta(days=7)).isoformat()
    return {
        "starts": len(rows),
        "save_pct": round(sv / sa, 4) if sa else None,
        "league_save_pct": round(lg, 4) if lg else None,
        "sa_per_start": round(sa / len(rows), 1),
        "ga_per_start": round((sa - sv) / len(rows), 2),
        "last5_save_pct": round(sv5 / sa5, 4) if sa5 else None,
        "starts_last_7_days": sum(r["game_date"] >= week for r in rows),
        "last_start": rows[0]["game_date"],
        "advanced": goalie_xg(conn, goalie, before, season),
    }


MIN_XG_STARTS = 3


def goalie_xg(conn: sqlite3.Connection, goalie: int, before: str, season: int | None) -> dict[str, Any] | None:
    """Goals saved above expected this season: RinkX xG of the unblocked, non-empty-net attempts he
    faced minus the goals they produced. None (unavailable) until enough of his starts have xG."""
    row = conn.execute(
        "SELECT count(DISTINCT e.game_id), sum(e.xg), sum(e.event_type = 'goal') FROM pbp_shot_events e "
        "JOIN games g ON g.id = e.game_id JOIN goalie_game_stats s ON s.game_id = g.id AND s.player_id = e.goalie_id "
        "WHERE e.goalie_id = ? AND s.started = 1 AND g.season_id = ? AND g.game_date < ? AND g.status = 'final' "
        "AND e.xg IS NOT NULL",
        (goalie, season, before),
    ).fetchone()
    starts, xga, ga = int(row[0] or 0), row[1], int(row[2] or 0)
    if starts < MIN_XG_STARTS or xga is None:
        return None
    return {
        "starts": starts,
        "xg_against": round(xga, 2),
        "goals_against": ga,
        "gsax": round(xga - ga, 2),
        "gsax_per_start": round((xga - ga) / starts, 3),
        "source": "RinkX expected goals (unblocked attempts, empty net excluded)",
    }
