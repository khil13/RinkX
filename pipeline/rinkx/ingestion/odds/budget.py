"""Credit budgeter for the odds API (config/budget.yml).

Each run gets an allowance: the credits left above the reserve, spread evenly over the days
left in the month, minus what today has already used. Game lines (all games, one call) come
first, then props for the soonest games that are due a refresh, taking markets in priority
order until the allowance is used. The plan never exceeds the allowance.
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
        )


@dataclass(frozen=True)
class Candidate:
    game_id: int
    event_id: str
    start: datetime
    last_fetch: datetime | None


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
    for c in sorted(candidates, key=lambda c: c.start):
        if not refresh_due(cfg, c, now):
            continue
        k = min(len(cfg.prop_markets), left // unit)
        if k <= 0:
            break
        p.props.append((c, cfg.prop_markets[:k]))
        left -= k * unit
    return p
