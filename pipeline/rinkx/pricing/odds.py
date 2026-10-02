"""Odds math (docs/05-models.md §7): implied probability, removing the bookmaker margin
("devig"), and expected value. Pure functions, property-tested in tests/test_pricing.py."""

from __future__ import annotations

import math
from dataclasses import dataclass


def implied(american: int) -> float:
    """Implied probability of an American price, margin included."""
    if -100 < american < 100:
        raise ValueError(f"{american} is not an American price")
    return 100 / (american + 100) if american > 0 else -american / (-american + 100)


def decimal(american: int) -> float:
    return 1 + (american / 100 if american > 0 else 100 / -american)


def multiplicative(io: float, iu: float) -> tuple[float, float]:
    s = io + iu
    return io / s, iu / s


def power(io: float, iu: float) -> tuple[float, float]:
    """Solve p_o^k + p_u^k = 1 for k, with p = implied. Removes more margin from the longshot."""
    lo, hi = 0.5, 2.0
    for _ in range(80):
        k = (lo + hi) / 2
        if io**k + iu**k > 1:
            lo = k
        else:
            hi = k
    k = (lo + hi) / 2
    po = io**k
    return po, 1 - po


def shin(io: float, iu: float) -> tuple[float, float]:
    """Shin's model (insider-trading share z) for a two-way market."""
    s = io + iu

    def probs(z: float) -> tuple[float, float]:
        def f(i: float) -> float:
            return (math.sqrt(z * z + 4 * (1 - z) * i * i / s) - z) / (2 * (1 - z))

        return f(io), f(iu)

    lo, hi = 0.0, 0.5
    for _ in range(80):
        z = (lo + hi) / 2
        po, pu = probs(z)
        if po + pu > 1:
            lo = z
        else:
            hi = z
    po, _ = probs((lo + hi) / 2)
    return po, 1 - po


@dataclass(frozen=True)
class NoVig:
    over: float
    under: float
    method: str
    implied_over: float
    implied_under: float
    overround: float
    shin_over: float  # reported alongside (docs: shown if it differs materially)


LOPSIDED = 0.65  # an implied probability above this on either side -> power method


def devig(over_price: int, under_price: int) -> NoVig:
    io, iu = implied(over_price), implied(under_price)
    method = "power" if max(io, iu) > LOPSIDED else "multiplicative"
    po, pu = power(io, iu) if method == "power" else multiplicative(io, iu)
    return NoVig(po, pu, method, io, iu, io + iu, shin(io, iu)[0])


def ev_per_unit(p_win: float, p_loss: float, american: int) -> float:
    """Expected profit per 1 unit staked; a push (1 - p_win - p_loss) returns the stake."""
    return p_win * (decimal(american) - 1) - p_loss
