"""Settlement rules: what a bet at a line paid, the way FanDuel and BetMGM settle NHL markets.

* Stats count regulation and overtime, never the shootout (NHL box scores already exclude it).
* Game and team totals count the shootout winner as one goal (the NHL final score already does).
* The moneyline includes overtime and the shootout.
* A skater prop is void if the player doesn't play. A goalie prop is void unless the goalie starts.
* First goal scorer: void if the player doesn't play. If no goal is scored before a shootout,
  every "Yes" loses.
* A cancelled game voids everything. So does a postponed game that hasn't been played within
  POSTPONED_VOID_HOURS of its original start.

House rules vary and change; these are the common ones, applied the same way to every book.
Pure functions here; database reads live in grade.py.
"""

from __future__ import annotations

from dataclasses import dataclass

from rinkx.pricing.odds import decimal

SIDES = {"over_under": ("over", "under"), "yes_no": ("yes", "no"), "moneyline": ("home", "away")}
POSTPONED_VOID_HOURS = 48

SKATER_STAT = {
    "skater_shots_on_goal": "shots",
    "skater_goals": "goals",
    "skater_assists": "assists",
    "skater_points": "points",
    "skater_pp_points": "pp_points",
    "skater_blocked_shots": "blocked_shots",
    "skater_hits": "hits",
}
SKATER_YES = {"skater_anytime_goal": "goals", "skater_pp_goal": "pp_goals", "skater_pp_assist": "pp_assists"}
GOALIE_STAT = {"goalie_saves": "saves", "goalie_goals_against": "goals_against"}
GOALIE_YES = ("goalie_shutout", "goalie_win", "goalie_saves_and_win")
GAME_MARKETS = ("game_moneyline", "game_total")
GRADABLE = (*SKATER_STAT, *SKATER_YES, "skater_first_goal", *GOALIE_STAT, *GOALIE_YES, *GAME_MARKETS)

VOID_REASONS = {
    "did_not_play": "Player did not play",
    "goalie_did_not_start": "Goalie did not start",
    "game_cancelled": "Game cancelled",
    "game_postponed": "Game postponed and not played in time",
}


@dataclass(frozen=True)
class Settled:
    result: str  # the winning side, 'push' or 'void'
    actual: float | None
    void_reason: str | None = None


def void(reason: str) -> Settled:
    return Settled("void", None, reason)


def settle(kind: str, line: float | None, actual: float) -> Settled:
    """The side that won. `actual` is the stat for over/under, 1/0 for yes/no, 1 = home won for the moneyline."""
    first, second = SIDES[kind]
    if kind == "over_under":
        if line is None:
            raise ValueError("an over/under bet needs a line")
        if actual == line:
            return Settled("push", actual)
        return Settled(first if actual > line else second, actual)
    return Settled(first if actual >= 1 else second, actual)


@dataclass(frozen=True)
class Graded:
    outcome: str  # win / loss / push / void / no_bet (for the prediction's lean)
    profit: float | None  # per 1 unit staked on the lean


def grade_lean(lean: str, settled: Settled, price: int | None) -> Graded:
    if lean == "none":
        return Graded("no_bet", None)
    if settled.result in ("void", "push"):
        return Graded(settled.result, 0.0)
    if price is None:
        raise ValueError("a lean needs the price it was taken at")
    return Graded("win", decimal(price) - 1) if settled.result == lean else Graded("loss", -1.0)


def clv(p_close_side: float | None, price: int | None) -> float | None:
    """Closing-line value: what the bet was worth at the closing no-vig probability, per unit staked.
    Positive means the price taken beat the close."""
    if p_close_side is None or price is None:
        return None
    return p_close_side * decimal(price) - 1
