"""Credit budgeter for the odds API (config/budget.yml).

Each run gets an allowance: the credits left above the reserve, spread evenly over the days
left in the month, minus what today has already used. In each run, in this order:

1. Game lines (all games, one call), up to `times_per_day`.
2. Closing fetches: shortly before puck drop (`closing.hours_before`), one more look at the games
   where the model found leans, for the markets with leans first, so the closing price (and so
   closing-line value) is measured on the bets that matter.
3. First looks: a game not fetched yet gets its first `first_look_markets` markets, soonest first,
   so every game is checked cheaply before any game gets everything.
4. Refreshes and deeper looks for games that are due, the games with the biggest model edges
   first, taking markets in priority order.

Credits for the closing fetches still to come today are held back from steps 3 and 4. The plan
never exceeds the allowance.
"""

from __future__ import annotations

import calendar
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from rinkx.config import REPO_ROOT
from rinkx.ingestion.odds.client import credits_for
from rinkx.timeutil import EASTERN

BUDGET_FILE = REPO_ROOT / "config/budget.yml"


@dataclass(frozen=True)
class BudgetConfig:
    monthly_credits: int
    reserve: int
    game_markets: list[str]
    game_times_per_day: int
    window_hours: float
    refresh_hours: dict[str, float]
    prop_markets: list[str]
    first_look_markets: int = 2
    closing_hours: float = 1.5
    closing_markets: int = 2

    @staticmethod
    def load(path: Path = BUDGET_FILE) -> BudgetConfig:
        d: dict[str, Any] = yaml.safe_load(path.read_text())
        gl, pr = d["game_lines"], d["props"]
        return BudgetConfig(
            int(d["monthly_credits"]),
            int(d["reserve"]),
            list(gl["markets"]),
            int(gl["times_per_day"]),
            float(pr["window_hours"]),
            {k: float(v) for k, v in pr["refresh_hours"].items()},
            list(pr["markets"]),
            int(pr.get("first_look_markets", 2)),
            float((pr.get("closing") or {}).get("hours_before", 1.5)),
            int((pr.get("closing") or {}).get("markets", 2)),
        )


@dataclass(frozen=True)
class Candidate:
    game_id: int
    event_id: str
    start: datetime
    last_fetch: datetime | None
    priority: float = 0.0  # the model's biggest edge on this game's lines so far (0: none, or not fetched)
    lean_markets: tuple[str, ...] = ()  # vendor market keys where the model has a lean
    markets_fetched: int = 0  # prop markets in its last fetch


@dataclass
class Plan:
    allowance: int
    game_lines: bool = False
    props: list[tuple[Candidate, list[str]]] = field(default_factory=list)

    def cost(self, cfg: BudgetConfig, books: list[str]) -> int:
        c = credits_for(cfg.game_markets, books) if self.game_lines else 0
        return c + sum(credits_for(m, books) for _, m in self.props)


def days_left_in_month(now: datetime) -> int:
    local = now.astimezone(EASTERN)
    return calendar.monthrange(local.year, local.month)[1] - local.day + 1


def refresh_due(cfg: BudgetConfig, c: Candidate, now: datetime) -> bool:
    hours_to = (c.start - now).total_seconds() / 3600
    if hours_to <= 0 or hours_to > cfg.window_hours:
        return False
    if c.last_fetch is None:
        return True
    every = (
        cfg.refresh_hours["far"]
        if hours_to > 12
        else cfg.refresh_hours["near"]
        if hours_to > 3
        else (cfg.refresh_hours["close"])
    )
    return now - c.last_fetch >= timedelta(hours=every)


def plan(
    cfg: BudgetConfig,
    now: datetime,
    *,
    remaining: int,
    spent_today: int,
    game_lines_today: int,
    last_game_lines: datetime | None,
    candidates: list[Candidate],
    books: list[str],
) -> Plan:
    start_of_day = remaining + spent_today
    daily = (start_of_day - cfg.reserve) / days_left_in_month(now)
    allowance = max(0, min(math.floor(daily - spent_today), remaining - cfg.reserve))
    p = Plan(allowance)
    left = allowance
    gl_cost = credits_for(cfg.game_markets, books)
    gap = timedelta(hours=24 / max(cfg.game_times_per_day, 1))
    if (
        game_lines_today < cfg.game_times_per_day
        and (last_game_lines is None or now - last_game_lines >= gap)
        and gl_cost <= left
    ):
        p.game_lines = True
        left -= gl_cost
    unit = credits_for(["x"], books)
    taken: set[int] = set()

    def markets_for(c: Candidate, k: int) -> list[str]:
        ordered = [m for m in c.lean_markets if m in cfg.prop_markets]
        ordered += [m for m in cfg.prop_markets if m not in ordered]
        return ordered[:k]

    def hours_to(c: Candidate) -> float:
        return (c.start - now).total_seconds() / 3600

    # 2. closing fetches for games with leans
    for c in sorted(candidates, key=lambda c: -c.priority):
        if left < unit:
            break
        if closing_due(cfg, c, now):
            k = min(cfg.closing_markets, left // unit)
            p.props.append((c, markets_for(c, k)))
            taken.add(c.game_id)
            left -= k * unit
    # credits held for closing fetches still to come today
    later = [
        c for c in candidates if c.game_id not in taken and c.priority > 0 and cfg.closing_hours < hours_to(c) <= 24
    ]
    hold = min(len(later) * cfg.closing_markets * unit, left // 2)
    free = left - hold
    # 3. first looks, soonest first
    for c in sorted(candidates, key=lambda c: c.start):
        if c.last_fetch is not None or c.game_id in taken or not refresh_due(cfg, c, now):
            continue
        k = min(cfg.first_look_markets, len(cfg.prop_markets), free // unit)
        if k <= 0:
            break
        p.props.append((c, cfg.prop_markets[:k]))
        taken.add(c.game_id)
        free -= k * unit
    # 4. refreshes and deeper looks, biggest edges first
    for c in sorted(candidates, key=lambda c: (-c.priority, c.start)):
        deeper = c.priority > 0 and c.markets_fetched < len(cfg.prop_markets)  # leans on a first look
        if c.game_id in taken or c.last_fetch is None or not (refresh_due(cfg, c, now) or deeper):
            continue
        if hours_to(c) <= 0 or hours_to(c) > cfg.window_hours:
            continue
        if hours_to(c) <= cfg.closing_hours:
            continue  # inside the closing window: step 2 decides
        k = min(len(cfg.prop_markets), free // unit)
        if k <= 0:
            break
        p.props.append((c, markets_for(c, k)))
        taken.add(c.game_id)
        free -= k * unit
    return p


def closing_due(cfg: BudgetConfig, c: Candidate, now: datetime) -> bool:
    """A game with leans, starting within the closing window, not fetched inside it yet."""
    hours_to = (c.start - now).total_seconds() / 3600
    if c.priority <= 0 or c.last_fetch is None or not 0 < hours_to <= cfg.closing_hours:
        return False
    return c.last_fetch < c.start - timedelta(hours=cfg.closing_hours)
