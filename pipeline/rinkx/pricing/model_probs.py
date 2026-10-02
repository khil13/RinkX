"""The model's probabilities at a sportsbook line, read straight off the projection's PMF."""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class AtLine:
    p_over: float  # unconditional
    p_under: float
    p_push: float

    @property
    def over_given_no_push(self) -> float:
        return self.p_over / (1 - self.p_push) if self.p_push < 1 else 0.0


def at_line(pmf: list[float], line: float) -> AtLine:
    """Over = X > line, Under = X < line, Push = X == line (whole-number lines only).
    The PMF tail beyond its last entry is < 1e-5 and counts as over."""
    total = sum(pmf)
    tail = max(0.0, 1.0 - total)
    if line == math.floor(line):
        k = int(line)
        push = pmf[k] if 0 <= k < len(pmf) else 0.0
        under = sum(pmf[: max(k, 0)])
    else:
        push = 0.0
        under = sum(pmf[: math.floor(line) + 1])
    over = max(0.0, total + tail - under - push)
    return AtLine(over, under, push)


def yes_no(pmf: list[float]) -> AtLine:
    """'Yes' = at least one (anytime goal, PP goal...), or the second entry of a [no, yes] PMF."""
    p_yes = max(0.0, 1.0 - pmf[0]) if pmf else 0.0
    return AtLine(p_yes, 1 - p_yes, 0.0)
