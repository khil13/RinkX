"""Pure parsers: NHL API JSON -> typed records. No I/O, no database.

Field meanings were checked against recorded responses in tests/fixtures/nhl/. Fields the
API does not provide are left as None (missing is never zero).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

GAME_TYPES = {1: "P", 2: "R", 3: "O"}  # preseason, regular season, playoffs; others are skipped
FINAL_STATES = {"OFF", "FINAL"}


class ParseError(ValueError):
    pass


def _default(obj: Any) -> str | None:
    """Localized NHL strings look like {"default": "Boston", "fr": "..."}."""
    if obj is None:
        return None
    if isinstance(obj, dict):
        v = obj.get("default")
        return str(v) if v is not None else None
    return str(obj)


def toi_seconds(text: str | None) -> int | None:
    """'17:04' -> 1024. Minutes can exceed 59 ('60:18')."""
    if not text:
        return None
    try:
        m, s = text.split(":")
        return int(m) * 60 + int(s)
    except ValueError as exc:
        raise ParseError(f"bad TOI {text!r}") from exc


def saves_of(text: str | None) -> tuple[int | None, int | None]:
    """Goalie split strings are 'saves/shots', e.g. '18/20' -> (18, 20)."""
    if not text:
        return None, None
    try:
        saves, shots = text.split("/")
        return int(saves), int(shots)
    except ValueError as exc:
        raise ParseError(f"bad saves/shots {text!r}") from exc


def game_status(game_state: str, schedule_state: str | None) -> str:
    if schedule_state == "PPD":
        return "postponed"
    if schedule_state == "CNCL":
        return "cancelled"
    return {
        "FUT": "scheduled",
        "PRE": "pregame",
        "LIVE": "live",
        "CRIT": "live",
        "OFF": "final",
        "FINAL": "final",
    }.get(game_state, "scheduled")


# --------------------------------------------------------------------------- schedule


@dataclass(frozen=True)
class TeamRef:
    nhl_team_id: int
    abbrev: str
    location: str
    name: str


@dataclass(frozen=True)
class GameRec:
    nhl_game_id: int
    season_id: int
    game_type: str
    game_date: str
    start_time_utc: str
    home: TeamRef
    away: TeamRef
    venue_name: str | None
    neutral_site: bool
    status: str
    period: int | None
    home_score: int | None
    away_score: int | None
    ended_in: str | None


@dataclass(frozen=True)
class ScheduleWeek:
    days: list[str]  # every date the response covers, including days with no games
    games: list[GameRec]
    regular_season_start: str | None
    regular_season_end: str | None
    playoff_end: str | None
    next_start_date: str | None


def _team_ref(t: dict[str, Any]) -> TeamRef:
    return TeamRef(
        nhl_team_id=int(t["id"]),
        abbrev=str(t["abbrev"]),
        location=_default(t.get("placeName")) or str(t["abbrev"]),
        name=_default(t.get("commonName")) or str(t["abbrev"]),
    )


def parse_schedule(payload: dict[str, Any]) -> ScheduleWeek:
    games: list[GameRec] = []
    for day in payload.get("gameWeek", []):
        for g in day.get("games", []):
            gtype = GAME_TYPES.get(int(g["gameType"]))
            if gtype is None:
                continue  # all-star, exhibition vs non-NHL clubs, etc.
            status = game_status(g["gameState"], g.get("gameScheduleState"))
            started = status in ("live", "final")
            period = (g.get("periodDescriptor") or {}).get("number") if started else None
            ended_in = None
            if status == "final":
                ended_in = (g.get("gameOutcome") or {}).get("lastPeriodType") or (g.get("periodDescriptor") or {}).get(
                    "periodType"
                )
                if ended_in not in ("REG", "OT", "SO"):
                    ended_in = None
            games.append(
                GameRec(
                    nhl_game_id=int(g["id"]),
                    season_id=int(g["season"]),
                    game_type=gtype,
                    game_date=str(day["date"]),
                    start_time_utc=str(g["startTimeUTC"]),
                    home=_team_ref(g["homeTeam"]),
                    away=_team_ref(g["awayTeam"]),
                    venue_name=_default(g.get("venue")),
                    neutral_site=bool(g.get("neutralSite", False)),
                    status=status,
                    period=int(period) if period is not None else None,
                    home_score=g["homeTeam"].get("score") if started else None,
                    away_score=g["awayTeam"].get("score") if started else None,
                    ended_in=ended_in,
                )
            )
    return ScheduleWeek(
        days=[str(d["date"]) for d in payload.get("gameWeek", [])],
        games=games,
        regular_season_start=payload.get("regularSeasonStartDate"),
        regular_season_end=payload.get("regularSeasonEndDate"),
        playoff_end=payload.get("playoffEndDate"),
        next_start_date=payload.get("nextStartDate"),
    )


# --------------------------------------------------------------------------- seasons / standings


@dataclass(frozen=True)
class SeasonRec:
    id: int
    start_date: str
    end_date: str


def parse_standings_seasons(payload: dict[str, Any]) -> list[SeasonRec]:
    return [
        SeasonRec(id=int(s["id"]), start_date=str(s["standingsStart"]), end_date=str(s["standingsEnd"]))
        for s in payload.get("seasons", [])
    ]


@dataclass(frozen=True)
class StandingRec:
    abbrev: str
    season_id: int
    as_of_date: str
    games_played: int
    wins: int
    losses: int
    ot_losses: int
    points: int
    goals_for: int
    goals_against: int
    home_wins: int | None
    home_losses: int | None
    home_ot_losses: int | None
    road_wins: int | None
    road_losses: int | None
    road_ot_losses: int | None
    l10_wins: int | None
    l10_losses: int | None
    l10_ot_losses: int | None
    streak_code: str | None
    streak_count: int | None
    conference: str | None
    division: str | None


def parse_standings(payload: dict[str, Any]) -> list[StandingRec]:
    out = []
    for s in payload.get("standings", []):
        out.append(
            StandingRec(
                abbrev=str(_default(s["teamAbbrev"])),
                season_id=int(s["seasonId"]),
                as_of_date=str(s["date"]),
                games_played=int(s["gamesPlayed"]),
                wins=int(s["wins"]),
                losses=int(s["losses"]),
                ot_losses=int(s["otLosses"]),
                points=int(s["points"]),
                goals_for=int(s["goalFor"]),
                goals_against=int(s["goalAgainst"]),
                home_wins=s.get("homeWins"),
                home_losses=s.get("homeLosses"),
                home_ot_losses=s.get("homeOtLosses"),
                road_wins=s.get("roadWins"),
                road_losses=s.get("roadLosses"),
                road_ot_losses=s.get("roadOtLosses"),
                l10_wins=s.get("l10Wins"),
                l10_losses=s.get("l10Losses"),
                l10_ot_losses=s.get("l10OtLosses"),
                streak_code=s.get("streakCode"),
                streak_count=s.get("streakCount"),
                conference=s.get("conferenceName"),
                division=s.get("divisionName"),
            )
        )
    return out


# --------------------------------------------------------------------------- players


@dataclass(frozen=True)
class PlayerRec:
    nhl_player_id: int
    first_name: str
    last_name: str
    position: str
    shoots_catches: str | None
    birth_date: str | None
    height_cm: int | None
    weight_kg: int | None
    sweater_number: int | None
    team_abbrev: str | None


def _position(code: str) -> str:
    code = code.upper()
    if code in ("C", "L", "R", "D", "G"):
        return code
    raise ParseError(f"unknown position {code!r}")


def parse_roster(payload: dict[str, Any], team_abbrev: str) -> list[PlayerRec]:
    out = []
    for group in ("forwards", "defensemen", "goalies"):
        for p in payload.get(group, []):
            out.append(
                PlayerRec(
                    nhl_player_id=int(p["id"]),
                    first_name=_default(p["firstName"]) or "",
                    last_name=_default(p["lastName"]) or "",
                    position=_position(p["positionCode"]),
                    shoots_catches=p.get("shootsCatches"),
                    birth_date=p.get("birthDate"),
                    height_cm=p.get("heightInCentimeters"),
                    weight_kg=p.get("weightInKilograms"),
                    sweater_number=p.get("sweaterNumber"),
                    team_abbrev=team_abbrev,
                )
            )
    return out


def parse_player_landing(payload: dict[str, Any]) -> PlayerRec:
    return PlayerRec(
        nhl_player_id=int(payload["playerId"]),
        first_name=_default(payload["firstName"]) or "",
        last_name=_default(payload["lastName"]) or "",
        position=_position(payload["position"]),
        shoots_catches=payload.get("shootsCatches"),
        birth_date=payload.get("birthDate"),
        height_cm=payload.get("heightInCentimeters"),
        weight_kg=payload.get("weightInKilograms"),
        sweater_number=payload.get("sweaterNumber"),
        team_abbrev=payload.get("currentTeamAbbrev") if payload.get("isActive") else None,
    )


# --------------------------------------------------------------------------- boxscore


@dataclass(frozen=True)
class SkaterLine:
    nhl_player_id: int
    nhl_team_id: int
    is_home: bool
    position: str
    goals: int | None
    assists: int | None
    pp_goals: int | None
    shots: int | None
    hits: int | None
    blocked_shots: int | None
    pim: int | None
    plus_minus: int | None
    toi_s: int | None
    shifts: int | None
    giveaways: int | None
    takeaways: int | None
    faceoff_pct: float | None


@dataclass(frozen=True)
class GoalieLine:
    nhl_player_id: int
    nhl_team_id: int
    is_home: bool
    started: bool
    toi_s: int | None
    shots_against: int | None
    saves: int | None
    goals_against: int | None
    ev_saves: int | None
    ev_shots_against: int | None
    # Checked against penalties in a recorded game: "powerPlay" counts shots faced while the
    # OPPONENT had the power play; "shorthanded" while the opponent was shorthanded.
    pp_saves: int | None
    pp_shots_against: int | None
    sh_saves: int | None
    sh_shots_against: int | None
    decision: str | None  # 'W', 'L', 'O' (OT/SO loss), as reported


@dataclass(frozen=True)
class TeamLine:
    nhl_team_id: int
    is_home: bool
    goals: int | None
    shots: int | None


@dataclass
class Boxscore:
    nhl_game_id: int
    game_state: str
    skaters: list[SkaterLine] = field(default_factory=list)
    goalies: list[GoalieLine] = field(default_factory=list)
    teams: list[TeamLine] = field(default_factory=list)


def parse_boxscore(payload: dict[str, Any]) -> Boxscore:
    box = Boxscore(nhl_game_id=int(payload["id"]), game_state=str(payload["gameState"]))
    side_ids = {"homeTeam": int(payload["homeTeam"]["id"]), "awayTeam": int(payload["awayTeam"]["id"])}
    for side, team_id in side_ids.items():
        is_home = side == "homeTeam"
        t = payload[side]
        box.teams.append(TeamLine(team_id, is_home, t.get("score"), t.get("sog")))
        stats = payload.get("playerByGameStats", {}).get(side, {})
        for p in stats.get("forwards", []) + stats.get("defense", []):
            box.skaters.append(
                SkaterLine(
                    nhl_player_id=int(p["playerId"]),
                    nhl_team_id=team_id,
                    is_home=is_home,
                    position=_position(p["position"]),
                    goals=p.get("goals"),
                    assists=p.get("assists"),
                    pp_goals=p.get("powerPlayGoals"),
                    shots=p.get("sog"),
                    hits=p.get("hits"),
                    blocked_shots=p.get("blockedShots"),
                    pim=p.get("pim"),
                    plus_minus=p.get("plusMinus"),
                    toi_s=toi_seconds(p.get("toi")),
                    shifts=p.get("shifts"),
                    giveaways=p.get("giveaways"),
                    takeaways=p.get("takeaways"),
                    faceoff_pct=p.get("faceoffWinningPctg"),
                )
            )
        for g in stats.get("goalies", []):
            ev_sv, ev_sa = saves_of(g.get("evenStrengthShotsAgainst"))
            pp_sv, pp_sa = saves_of(g.get("powerPlayShotsAgainst"))
            sh_sv, sh_sa = saves_of(g.get("shorthandedShotsAgainst"))
            box.goalies.append(
                GoalieLine(
                    nhl_player_id=int(g["playerId"]),
                    nhl_team_id=team_id,
                    is_home=is_home,
                    started=bool(g.get("starter", False)),
                    toi_s=toi_seconds(g.get("toi")),
                    shots_against=g.get("shotsAgainst"),
                    saves=g.get("saves"),
                    goals_against=g.get("goalsAgainst"),
                    ev_saves=ev_sv,
                    ev_shots_against=ev_sa,
                    pp_saves=pp_sv,
                    pp_shots_against=pp_sa,
                    sh_saves=sh_sv,
                    sh_shots_against=sh_sa,
                    decision=g.get("decision"),
                )
            )
    return box


# --------------------------------------------------------------------------- play-by-play

SHOT_TYPES = {"shot-on-goal": "shot", "missed-shot": "miss", "blocked-shot": "block", "goal": "goal"}


@dataclass(frozen=True)
class ShotEvent:
    event_idx: int
    period: int
    period_seconds: int
    event_type: str  # shot | miss | block | goal
    shooter_id: int | None
    goalie_id: int | None  # None for empty-net attempts and blocks
    # Matches official stats: a shot blocked by the shooter's own teammate counts as the
    # shooter's blocked attempt, but is NOT a blocked shot for the teammate.
    blocker_id: int | None  # opponent who blocked it; None for teammate blocks
    teammate_block: bool
    shooter_team_id: int
    strength_state: str  # from the shooting team's view, e.g. "5v4"; "5v5" at even strength
    empty_net: bool
    x: int | None  # normalized so the shooting team attacks toward +x
    y: int | None
    shot_type: str | None
    assist1_id: int | None = None  # goals only
    assist2_id: int | None = None


@dataclass(frozen=True)
class GoalCredit:
    scorer_id: int
    assist1_id: int | None
    assist2_id: int | None
    team_id: int
    strength_state: str


@dataclass
class PlayByPlay:
    nhl_game_id: int
    game_state: str
    home_team_id: int
    away_team_id: int
    shots: list[ShotEvent] = field(default_factory=list)
    goals: list[GoalCredit] = field(default_factory=list)


def _strength(situation: str, team_is_home: bool) -> tuple[str, bool]:
    """situationCode digits: away goalie, away skaters, home skaters, home goalie."""
    if len(situation) != 4 or not situation.isdigit():
        raise ParseError(f"bad situationCode {situation!r}")
    away_g, away_s, home_s, home_g = (int(c) for c in situation)
    own, opp = (home_s, away_s) if team_is_home else (away_s, home_s)
    opp_goalie = away_g if team_is_home else home_g
    return f"{own}v{opp}", opp_goalie == 0


def parse_play_by_play(payload: dict[str, Any]) -> PlayByPlay:
    home, away = int(payload["homeTeam"]["id"]), int(payload["awayTeam"]["id"])
    pbp = PlayByPlay(int(payload["id"]), str(payload["gameState"]), home, away)
    # The roster is the source of truth for a player's team; eventOwnerTeamId is not
    # consistent across event types.
    team_of = {int(r["playerId"]): int(r["teamId"]) for r in payload.get("rosterSpots", [])}
    for play in payload.get("plays", []):
        kind = SHOT_TYPES.get(play.get("typeDescKey", ""))
        period = play.get("periodDescriptor") or {}
        if kind is None or period.get("periodType") == "SO":
            continue  # shootout attempts are not shots or goals in any stat or prop
        d = play.get("details") or {}
        shooter = d.get("scoringPlayerId") if kind == "goal" else d.get("shootingPlayerId")
        team = team_of.get(int(shooter)) if shooter is not None else d.get("eventOwnerTeamId")
        if team is None:
            raise ParseError(f"cannot determine shooting team for event {play.get('eventId')}")
        is_home = team == home
        state, empty_net = _strength(str(play["situationCode"]), is_home)
        x, y = d.get("xCoord"), d.get("yCoord")
        if x is not None and y is not None:
            # Home defends `homeTeamDefendingSide` (left/right); flip so every shot attacks +x.
            home_attacks_right = play.get("homeTeamDefendingSide") == "left"
            if is_home != home_attacks_right:
                x, y = -x, -y
        pbp.shots.append(
            ShotEvent(
                event_idx=int(play["sortOrder"]),
                period=int(period["number"]),
                period_seconds=toi_seconds(play.get("timeInPeriod")) or 0,
                event_type=kind,
                shooter_id=int(shooter) if shooter is not None else None,
                goalie_id=d.get("goalieInNetId"),
                blocker_id=None if d.get("reason") == "teammate-blocked" else d.get("blockingPlayerId"),
                teammate_block=d.get("reason") == "teammate-blocked",
                shooter_team_id=int(team),
                strength_state=state,
                empty_net=empty_net,
                x=x,
                y=y,
                shot_type=d.get("shotType"),
                assist1_id=d.get("assist1PlayerId") if kind == "goal" else None,
                assist2_id=d.get("assist2PlayerId") if kind == "goal" else None,
            )
        )
        if kind == "goal":
            if shooter is None:
                raise ParseError(f"goal event {play.get('eventId')} has no scorer")
            pbp.goals.append(
                GoalCredit(int(shooter), d.get("assist1PlayerId"), d.get("assist2PlayerId"), int(team), state)
            )
    return pbp


# --------------------------------------------------------------------------- stats API game reports


def parse_game_report(payload: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
    """Rows of a stats-API report page, plus the total row count across pages."""
    rows = payload.get("data")
    if not isinstance(rows, list):
        raise ParseError("stats report has no data list")
    for r in rows:
        if "playerId" not in r or "gameId" not in r:
            raise ParseError("stats report row without playerId/gameId")
    return rows, int(payload.get("total", len(rows)))
