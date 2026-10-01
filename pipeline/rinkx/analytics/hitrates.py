"""Hit-rate engine: how often a player reached each threshold, over recent windows.

Line-independent on purpose: it reports counts for every threshold (e.g. 1+, 2+, ... shots),
so no sportsbook line is needed. When odds arrive (Phase 4) the same counts are read at the
offered line.

Rules (docs/05-models.md §9):
* Windows count games PLAYED, most recent first. Games the player missed (DNP) are listed in
  the game log but never enter a denominator.
* A game where a stat is unknown (None) is excluded for that stat and reported as `missing`;
  it is never counted as 0.
* Nothing is filtered: every played game in the window counts, good or bad.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

WINDOWS: list[tuple[str, int | None]] = [("L5", 5), ("L10", 10), ("L15", 15), ("L20", 20)]


@dataclass(frozen=True)
class StatSpec:
    key: str  # field name in the game dicts
    label: str
    thresholds: tuple[int, ...]


SKATER_STATS = [
    StatSpec("sog", "Shots on goal", (1, 2, 3, 4, 5, 6, 7)),
    StatSpec("p", "Points", (1, 2, 3)),
    StatSpec("g", "Goals", (1, 2)),
    StatSpec("a", "Assists", (1, 2)),
    StatSpec("ppp", "Power-play points", (1, 2)),
    StatSpec("hits", "Hits", (1, 2, 3, 4, 5, 6)),
    StatSpec("blk", "Blocked shots", (1, 2, 3, 4)),
    StatSpec("icf", "Shot attempts", (2, 4, 6, 8, 10)),
]
GOALIE_STATS = [
    StatSpec("sv", "Saves", (15, 20, 22, 24, 26, 28, 30, 32, 35)),
    StatSpec("ga", "Goals against", (1, 2, 3, 4, 5)),
]


def summarize(values: Sequence[float | None], thresholds: Sequence[int]) -> dict[str, Any]:
    """Counts of value >= k for each threshold, plus mean/median/sd over known values."""
    known = [float(v) for v in values if v is not None]
    n = len(known)
    return {
        "games": len(values),
        "known": n,
        "missing": len(values) - n,
        "counts": [sum(v >= k for v in known) for k in thresholds],
        "mean": round(statistics.fmean(known), 2) if n else None,
        "median": statistics.median(known) if n else None,
        "sd": round(statistics.stdev(known), 2) if n >= 2 else None,
    }


def hit_rates(
    played: Sequence[dict[str, Any]],
    specs: Sequence[StatSpec],
    *,
    season: int | None,
    last_season: int | None,
) -> dict[str, Any]:
    """`played`: games the player appeared in, most recent first, each with `date`, `season`
    and the stat keys. Rolling windows (L5..L20) cross season boundaries, which the payload
    states via each window's date range; `season` and `last_season` do not."""
    out: dict[str, Any] = {}
    for spec in specs:
        windows: dict[str, Any] = {}
        for name, size in WINDOWS:
            games = list(played[:size]) if size else list(played)
            windows[name] = summarize([g.get(spec.key) for g in games], spec.thresholds) | _span(games)
        for name, s_id in (("season", season), ("last_season", last_season)):
            games = [g for g in played if s_id is not None and g.get("season") == s_id]
            windows[name] = summarize([g.get(spec.key) for g in games], spec.thresholds) | _span(games)
        out[spec.key] = {"label": spec.label, "thresholds": list(spec.thresholds), "windows": windows}
    return out


def _span(games: Sequence[dict[str, Any]]) -> dict[str, str | None]:
    return {"from": games[-1]["date"] if games else None, "to": games[0]["date"] if games else None}


def wilson_interval(hits: int, n: int, z: float = 1.96) -> tuple[float, float] | None:
    """95% Wilson score interval for a hit rate; honest about small samples (e.g. 4/5)."""
    if n == 0:
        return None
    p = hits / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return round(max(0.0, centre - half), 3), round(min(1.0, centre + half), 3)
