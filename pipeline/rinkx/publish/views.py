"""Store -> published payloads (the data contract in docs/03-api.md).

Rules: values come straight from the store; anything RinkX can't know yet is null with a
`*_reason`. Nothing here estimates, interpolates or fills gaps.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from rinkx.analytics import hitrates
from rinkx.publish import projections

NOT_CONNECTED = "not_connected"  # the feed that would provide this isn't built yet
INSUFFICIENT = "insufficient_sample"
MIN_TEAM_GAMES = 5


def _team(row: sqlite3.Row, prefix: str = "") -> dict[str, Any]:
    return {
        "abbrev": row[f"{prefix}abbrev"],
        "name": row[f"{prefix}name"],
        "location": row[f"{prefix}location"],
        "conference": row[f"{prefix}conference"],
        "division": row[f"{prefix}division"],
    }


def latest_standing(conn: sqlite3.Connection, team_id: int, season_id: int, on_or_before: str) -> dict[str, Any] | None:
    r = conn.execute(
        "SELECT * FROM team_standings WHERE team_id = ? AND season_id = ? AND as_of_date <= ? "
        "ORDER BY as_of_date DESC LIMIT 1",
        (team_id, season_id, on_or_before),
    ).fetchone()
    if r is None:
        return None
    l10 = (
        f"{r['l10_wins']}-{r['l10_losses']}-{r['l10_ot_losses']}"
        if r["l10_wins"] is not None and r["l10_losses"] is not None and r["l10_ot_losses"] is not None
        else None
    )
    return {
        "as_of": r["as_of_date"],
        "gp": r["games_played"],
        "w": r["wins"],
        "l": r["losses"],
        "otl": r["ot_losses"],
        "pts": r["points"],
        "gf": r["goals_for"],
        "ga": r["goals_against"],
        "l10": l10,
        "streak": f"{r['streak_code']}{r['streak_count']}" if r["streak_code"] and r["streak_count"] else None,
    }


def _context(conn: sqlite3.Connection, game_id: int, team_id: int) -> dict[str, Any] | None:
    r = conn.execute(
        "SELECT rest_days, is_back_to_back, games_last_7d, travel_km, tz_shift_hours FROM game_team_context "
        "WHERE game_id = ? AND team_id = ?",
        (game_id, team_id),
    ).fetchone()
    if r is None:
        return None
    # rest_days is NULL when no earlier game is stored. That only means "first game of the
    # season" if the whole season schedule was loaded; otherwise rest is simply unknown.
    season_complete = conn.execute(
        "SELECT 1 FROM ingestion_runs WHERE status = 'succeeded' AND job_name = "
        "'nhl.schedule_backfill:' || (SELECT season_id FROM games WHERE id = ?) LIMIT 1",
        (game_id,),
    ).fetchone()
    return {
        "first_game_of_season": (r["rest_days"] is None) if season_complete else None,
        "rest_days": r["rest_days"],
        "back_to_back": bool(r["is_back_to_back"]),
        "games_last_7d": r["games_last_7d"],
        "travel_km": r["travel_km"],
        "tz_shift_hours": r["tz_shift_hours"],
    }


GAME_SQL = """
SELECT g.*, h.abbrev AS h_abbrev, h.name AS h_name, h.location AS h_location, h.conference AS h_conference,
       h.division AS h_division, a.abbrev AS a_abbrev, a.name AS a_name, a.location AS a_location,
       a.conference AS a_conference, a.division AS a_division
FROM games g JOIN teams h ON h.id = g.home_team_id JOIN teams a ON a.id = g.away_team_id
"""


def _side(conn: sqlite3.Connection, g: sqlite3.Row, home: bool) -> dict[str, Any]:
    team_id = g["home_team_id"] if home else g["away_team_id"]
    record = latest_standing(conn, team_id, g["season_id"], g["game_date"])
    ctx = _context(conn, g["id"], team_id)
    goalie = projections.goalie_start(conn, g, team_id)
    return {
        "team": _team(g, "h_" if home else "a_"),
        "score": g["home_score"] if home else g["away_score"],
        "record": record,
        "record_reason": None if record else "data_unavailable",
        "context": ctx,
        "context_reason": None if ctx else "data_unavailable",
        "goalie": goalie,  # projected from recent starts, or confirmed via Quick Entry
        "goalie_reason": None if goalie else "data_unavailable",
        "injuries": None,
        "injuries_reason": NOT_CONNECTED,
    }


def game_summary(conn: sqlite3.Connection, g: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": g["nhl_game_id"],
        "season": g["season_id"],
        "game_type": g["game_type"],
        "date": g["game_date"],
        "start_time_utc": g["start_time_utc"],
        "status": g["status"],
        "period": g["period"],
        "ended_in": g["ended_in"],
        "venue": g["venue_name"],
        "neutral_site": bool(g["is_neutral_site"]),
        "home": _side(conn, g, True),
        "away": _side(conn, g, False),
        "environment": None,
        "environment_reason": NOT_CONNECTED,
        "model": projections.model_summary(conn, g["id"]),  # model win % and goals; not odds
        "fetched_at": g["fetched_at"],
    }


def slate(conn: sqlite3.Connection, date: str) -> dict[str, Any]:
    games = conn.execute(GAME_SQL + " WHERE g.game_date = ? ORDER BY g.start_time_utc, g.id", (date,)).fetchall()
    return {"date": date, "games": [game_summary(conn, g) for g in games]}


def team_metrics(conn: sqlite3.Connection, team_id: int, season_id: int, before: str) -> dict[str, Any] | None:
    """Per-game averages from box scores of completed games before `before`. Needs a minimum
    sample; early-season numbers are noise and are withheld rather than shown."""
    r = conn.execute(
        """SELECT count(*) AS gp, avg(t.goals_for) AS gf, avg(o.goals_for) AS ga,
                  avg(t.shots_for) AS sf, avg(o.shots_for) AS sa
           FROM team_game_stats t JOIN team_game_stats o ON o.game_id = t.game_id AND o.team_id <> t.team_id
           JOIN games g ON g.id = t.game_id
           WHERE t.team_id = ? AND g.season_id = ? AND g.game_date < ? AND g.status = 'final'""",
        (team_id, season_id, before),
    ).fetchone()
    if r["gp"] < MIN_TEAM_GAMES:
        return None
    return {
        "games": r["gp"],
        "goals_for_pg": round(r["gf"], 2),
        "goals_against_pg": round(r["ga"], 2),
        "shots_for_pg": round(r["sf"], 1),
        "shots_against_pg": round(r["sa"], 1),
    }


def _roster(conn: sqlite3.Connection, team_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT nhl_player_id, full_name, position, sweater_number FROM players "
        "WHERE current_team_id = ? AND is_active = 1 ORDER BY CASE position WHEN 'G' THEN 2 WHEN 'D' THEN 1 "
        "ELSE 0 END, last_name",
        (team_id,),
    ).fetchall()
    return [{"id": r[0], "name": r[1], "position": r[2], "number": r[3]} for r in rows]


def _boxscore(conn: sqlite3.Connection, game_id: int) -> dict[str, Any] | None:
    if not conn.execute("SELECT 1 FROM team_game_stats WHERE game_id = ?", (game_id,)).fetchone():
        return None
    skaters = conn.execute(
        """SELECT p.nhl_player_id, p.full_name, p.position, t.abbrev, s.toi_s, s.goals, s.assists, s.points,
                  s.shots, s.hits, s.blocked_shots, s.pim, s.plus_minus, s.pp_goals
           FROM player_game_stats s JOIN players p ON p.id = s.player_id JOIN teams t ON t.id = s.team_id
           WHERE s.game_id = ? ORDER BY t.abbrev, s.toi_s DESC""",
        (game_id,),
    ).fetchall()
    goalies = conn.execute(
        """SELECT p.nhl_player_id, p.full_name, t.abbrev, s.started, s.toi_s, s.shots_against, s.saves,
                  s.goals_against, s.decision
           FROM goalie_game_stats s JOIN players p ON p.id = s.player_id JOIN teams t ON t.id = s.team_id
           WHERE s.game_id = ? ORDER BY t.abbrev, s.started DESC""",
        (game_id,),
    ).fetchall()
    return {
        "skaters": [
            {
                "id": r[0],
                "name": r[1],
                "position": r[2],
                "team": r[3],
                "toi_s": r[4],
                "g": r[5],
                "a": r[6],
                "p": r[7],
                "sog": r[8],
                "hits": r[9],
                "blk": r[10],
                "pim": r[11],
                "pm": r[12],
                "ppg": r[13],
            }
            for r in skaters
        ],
        "goalies": [
            {
                "id": r[0],
                "name": r[1],
                "team": r[2],
                "started": bool(r[3]),
                "toi_s": r[4],
                "sa": r[5],
                "sv": r[6],
                "ga": r[7],
                "decision": r[8],
            }
            for r in goalies
        ],
    }


def game_detail(conn: sqlite3.Connection, nhl_game_id: int) -> dict[str, Any]:
    g = conn.execute(GAME_SQL + " WHERE g.nhl_game_id = ?", (nhl_game_id,)).fetchone()
    out = game_summary(conn, g)
    for side, team_id in (("home", g["home_team_id"]), ("away", g["away_team_id"])):
        metrics = team_metrics(conn, team_id, g["season_id"], g["game_date"])
        out[side]["metrics"] = metrics
        out[side]["metrics_reason"] = None if metrics else INSUFFICIENT
        out[side]["roster"] = _roster(conn, team_id)
        out[side]["lines"] = None
        out[side]["lines_reason"] = NOT_CONNECTED
    out["projections"] = projections.game_projections(conn, g)
    box = _boxscore(conn, g["id"])
    out["boxscore"] = box
    out["boxscore_reason"] = None if box else ("not_final" if g["status"] != "final" else "data_unavailable")
    return out


def teams(conn: sqlite3.Connection, season_id: int | None, today: str) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM teams WHERE is_active = 1 ORDER BY abbrev").fetchall()
    out = []
    for r in rows:
        out.append(
            {
                **_team(r),
                "standing": latest_standing(conn, r["id"], season_id, today) if season_id else None,
            }
        )
    return out


def players_index(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT p.nhl_player_id, p.full_name, p.position, p.sweater_number, t.abbrev FROM players p "
        "LEFT JOIN teams t ON t.id = p.current_team_id WHERE p.is_active = 1 ORDER BY p.last_name, p.first_name"
    ).fetchall()
    return [{"id": r[0], "name": r[1], "position": r[2], "number": r[3], "team": r[4]} for r in rows]


SKATER_LOG_SQL = """
SELECT g.nhl_game_id, g.game_date, g.season_id, g.game_type, o.abbrev, s.is_home, s.toi_s, s.ev_toi_s, s.pp_toi_s,
       s.sh_toi_s, s.goals, s.assists, s.points, s.primary_assists, s.secondary_assists, s.shots, s.shot_attempts,
       s.hits, s.blocked_shots, s.pim, s.plus_minus, s.pp_goals, s.pp_assists, s.pp_points, s.faceoff_wins,
       s.faceoff_losses
FROM player_game_stats s JOIN games g ON g.id = s.game_id JOIN teams o ON o.id = s.opponent_team_id
WHERE s.player_id = ? AND g.season_id IN (?, ?) ORDER BY g.start_time_utc DESC"""

GOALIE_LOG_SQL = """
SELECT g.nhl_game_id, g.game_date, g.season_id, g.game_type, o.abbrev, s.is_home, s.started, s.toi_s,
       s.shots_against, s.saves, s.goals_against, s.decision, s.shutout
FROM goalie_game_stats s JOIN games g ON g.id = s.game_id JOIN teams o ON o.id = s.opponent_team_id
WHERE s.player_id = ? AND g.season_id IN (?, ?) ORDER BY g.start_time_utc DESC"""


def _skater_game(r: sqlite3.Row) -> dict[str, Any]:
    return {
        "game_id": r[0],
        "date": r[1],
        "season": r[2],
        "playoffs": r[3] == "O",
        "opponent": r[4],
        "home": bool(r[5]),
        "toi_s": r[6],
        "ev_toi_s": r[7],
        "pp_toi_s": r[8],
        "sh_toi_s": r[9],
        "g": r[10],
        "a": r[11],
        "p": r[12],
        "a1": r[13],
        "a2": r[14],
        "sog": r[15],
        "icf": r[16],
        "hits": r[17],
        "blk": r[18],
        "pim": r[19],
        "pm": r[20],
        "ppg": r[21],
        "ppa": r[22],
        "ppp": r[23],
        "fow": r[24],
        "fol": r[25],
    }


def _goalie_game(r: sqlite3.Row) -> dict[str, Any]:
    return {
        "game_id": r[0],
        "date": r[1],
        "season": r[2],
        "playoffs": r[3] == "O",
        "opponent": r[4],
        "home": bool(r[5]),
        "started": bool(r[6]),
        "toi_s": r[7],
        "sa": r[8],
        "sv": r[9],
        "ga": r[10],
        "decision": r[11],
        "shutout": bool(r[12]) if r[12] is not None else None,
    }


def _dnp_games(conn: sqlite3.Connection, player_pk: int, team_id: int | None, season_id: int) -> list[dict[str, Any]]:
    """Completed games of the player's current team, since their first game for that team this season,
    that he did not play. Listed in the log, never counted in hit-rate denominators."""
    if team_id is None:
        return []
    rows = conn.execute(
        """SELECT g.nhl_game_id, g.game_date, g.season_id,
                  CASE WHEN g.home_team_id = ? THEN a.abbrev ELSE h.abbrev END, g.home_team_id = ?
           FROM games g JOIN teams h ON h.id = g.home_team_id JOIN teams a ON a.id = g.away_team_id
           WHERE g.season_id = ? AND g.status = 'final' AND ? IN (g.home_team_id, g.away_team_id)
             AND EXISTS (SELECT 1 FROM team_game_stats t WHERE t.game_id = g.id)
             AND g.game_date >= (SELECT min(g2.game_date) FROM player_game_stats s2 JOIN games g2 ON g2.id = s2.game_id
                                 WHERE s2.player_id = ? AND s2.team_id = ? AND g2.season_id = ?)
             AND NOT EXISTS (SELECT 1 FROM player_game_stats s WHERE s.game_id = g.id AND s.player_id = ?)
             AND NOT EXISTS (SELECT 1 FROM goalie_game_stats s WHERE s.game_id = g.id AND s.player_id = ?)""",
        (team_id, team_id, season_id, team_id, player_pk, team_id, season_id, player_pk, player_pk),
    ).fetchall()
    return [
        {"game_id": r[0], "date": r[1], "season": r[2], "opponent": r[3], "home": bool(r[4]), "dnp": True} for r in rows
    ]


def _skater_totals(games: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not games:
        return None

    def total(k: str) -> int | None:
        vals = [x[k] for x in games]
        return None if any(v is None for v in vals) else sum(vals)  # incomplete -> unknown, not partial

    toi = total("toi_s")
    return {
        "gp": len(games),
        "g": total("g"),
        "a": total("a"),
        "p": total("p"),
        "sog": total("sog"),
        "icf": total("icf"),
        "hits": total("hits"),
        "blk": total("blk"),
        "ppg": total("ppg"),
        "ppp": total("ppp"),
        "toi_avg_s": round(toi / len(games)) if toi is not None else None,
        "pp_toi_avg_s": round(t / len(games)) if (t := total("pp_toi_s")) is not None else None,
    }


def _goalie_totals(games: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not games:
        return None
    sa = sum(x["sa"] or 0 for x in games)
    toi = sum(x["toi_s"] or 0 for x in games)
    return {
        "gp": len(games),
        "starts": sum(x["started"] for x in games),
        "w": sum(x["decision"] == "W" for x in games),
        "l": sum(x["decision"] == "L" for x in games),
        "otl": sum(x["decision"] == "O" for x in games),
        "sv_pct": round(sum(x["sv"] or 0 for x in games) / sa, 3) if sa else None,
        "gaa": round(sum(x["ga"] or 0 for x in games) * 3600 / toi, 2) if toi else None,
        "so": sum(bool(x["shutout"]) for x in games),
    }


def player(conn: sqlite3.Connection, nhl_player_id: int, season_id: int | None) -> dict[str, Any]:
    p = conn.execute(
        "SELECT p.*, t.abbrev AS team_abbrev FROM players p LEFT JOIN teams t ON t.id = p.current_team_id "
        "WHERE p.nhl_player_id = ?",
        (nhl_player_id,),
    ).fetchone()
    bio = {
        "id": p["nhl_player_id"],
        "name": p["full_name"],
        "position": p["position"],
        "number": p["sweater_number"],
        "team": p["team_abbrev"],
        "shoots_catches": p["shoots_catches"],
        "birth_date": p["birth_date"],
        "height_cm": p["height_cm"],
        "weight_kg": p["weight_kg"],
    }
    last_season = season_id - 10001 if season_id else None
    goalie = p["position"] == "G"
    rows = conn.execute(GOALIE_LOG_SQL if goalie else SKATER_LOG_SQL, (p["id"], season_id, last_season)).fetchall()
    played = [(_goalie_game if goalie else _skater_game)(r) for r in rows]
    this_season = [g for g in played if g["season"] == season_id]
    prior = [g for g in played if g["season"] == last_season]

    if goalie:
        totals = _goalie_totals(this_season)
        # Saves/GA props are about starts; relief appearances would distort the rates.
        rates = hitrates.hit_rates(
            [g for g in played if g["started"]], hitrates.GOALIE_STATS, season=season_id, last_season=last_season
        )
        basis = "starts"
    else:
        totals = _skater_totals(this_season)
        rates = hitrates.hit_rates(played, hitrates.SKATER_STATS, season=season_id, last_season=last_season)
        basis = "games_played"

    dnp = _dnp_games(conn, p["id"], p["current_team_id"], season_id) if season_id else []
    log = sorted(this_season + dnp, key=lambda g: g["date"], reverse=True)
    return {
        "player": bio,
        "season": season_id,
        "last_season": last_season,
        "totals": totals,
        "totals_reason": None if totals else "no_games_this_season",
        "games": log,
        "last_season_games": prior,
        "hit_rates": rates,
        "hit_rates_basis": basis,
        "projection": projections.player_projection(conn, p["id"]),
    }
