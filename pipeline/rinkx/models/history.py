"""Completed games from the store, in the order they happened, as compact records for models.

Only official, completed regular-season and playoff games with a loaded box score are used.
Preseason is excluded (split squads, unrepresentative ice time).
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field

# Skater counting stats the models project. pp_toi_s is the basis for the pp_* stats.
SKATER_STATS = ("shots", "goals", "assists", "points", "pp_points", "pp_goals", "pp_assists", "blocks", "hits")
GOALIE_STATS = ("saves", "goals_against")


@dataclass(frozen=True)
class SkaterLine:
    player: int  # players.id
    team: int
    opp: int
    home: bool
    pos: str  # 'F' or 'D'
    toi_s: float | None
    pp_toi_s: float | None
    stats: dict[str, float | None]


@dataclass(frozen=True)
class GoalieLine:
    player: int
    team: int
    opp: int
    home: bool
    started: bool
    toi_s: float | None
    sa: float | None
    saves: float | None
    ga: float | None


@dataclass
class GameRecord:
    game_id: int  # games.id
    nhl_game_id: int
    date: str
    start: str
    season: int
    home_team: int
    away_team: int
    neutral: bool
    skaters: list[SkaterLine] = field(default_factory=list)
    goalies: list[GoalieLine] = field(default_factory=list)


def pos_group(position: str) -> str:
    return "D" if position == "D" else "F"


def _f(v: object) -> float | None:
    return None if v is None else float(v)  # type: ignore[arg-type]


def load(conn: sqlite3.Connection, before: str | None = None) -> list[GameRecord]:
    """Completed games (optionally only those dated before `before`), oldest first."""
    cond = (
        "g.status = 'final' AND g.game_type IN ('R','O') "
        "AND EXISTS (SELECT 1 FROM team_game_stats t WHERE t.game_id = g.id)"
    )
    args: tuple[object, ...] = ()
    if before is not None:
        cond += " AND g.game_date < ?"
        args = (before,)
    games: dict[int, GameRecord] = {}
    for r in conn.execute(
        "SELECT g.id, g.nhl_game_id, g.game_date, g.start_time_utc, g.season_id, g.home_team_id, g.away_team_id, "
        f"g.is_neutral_site FROM games g WHERE {cond} ORDER BY g.game_date, g.start_time_utc, g.id",
        args,
    ):
        games[r[0]] = GameRecord(r[0], r[1], r[2], r[3], r[4], r[5], r[6], bool(r[7]))

    skaters: dict[int, list[SkaterLine]] = defaultdict(list)
    for r in conn.execute(
        "SELECT s.game_id, s.player_id, s.team_id, s.opponent_team_id, s.is_home, p.position, s.toi_s, s.pp_toi_s, "
        "s.shots, s.goals, s.assists, s.pp_goals, s.pp_assists, s.blocked_shots, s.hits "
        f"FROM player_game_stats s JOIN players p ON p.id = s.player_id JOIN games g ON g.id = s.game_id WHERE {cond} "
        "ORDER BY s.game_id, s.player_id",
        args,
    ):
        g, a, ppg, ppa = _f(r[9]), _f(r[10]), _f(r[11]), _f(r[12])
        stats = {
            "shots": _f(r[8]),
            "goals": g,
            "assists": a,
            "points": None if g is None or a is None else g + a,
            "pp_points": None if ppg is None or ppa is None else ppg + ppa,
            "pp_goals": ppg,
            "pp_assists": ppa,
            "blocks": _f(r[13]),
            "hits": _f(r[14]),
        }
        skaters[r[0]].append(SkaterLine(r[1], r[2], r[3], bool(r[4]), pos_group(r[5]), _f(r[6]), _f(r[7]), stats))

    goalies: dict[int, list[GoalieLine]] = defaultdict(list)
    for r in conn.execute(
        "SELECT s.game_id, s.player_id, s.team_id, s.opponent_team_id, s.is_home, s.started, s.toi_s, "
        "s.shots_against, s.saves, s.goals_against FROM goalie_game_stats s JOIN games g ON g.id = s.game_id "
        f"WHERE {cond} ORDER BY s.game_id, s.started DESC, s.player_id",
        args,
    ):
        goalies[r[0]].append(
            GoalieLine(r[1], r[2], r[3], bool(r[4]), bool(r[5]), _f(r[6]), _f(r[7]), _f(r[8]), _f(r[9]))
        )

    out = []
    for gid, rec in games.items():
        rec.skaters = skaters.get(gid, [])
        rec.goalies = goalies.get(gid, [])
        if rec.skaters:
            out.append(rec)
    return out


def by_date(games: list[GameRecord]) -> list[tuple[str, list[GameRecord]]]:
    """Group games by date: games on the same date are predicted before any of them is learned from."""
    out: list[tuple[str, list[GameRecord]]] = []
    for g in games:
        if out and out[-1][0] == g.date:
            out[-1][1].append(g)
        else:
            out.append((g.date, [g]))
    return out
