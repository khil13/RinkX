"""Confidence score 0-100 (docs/05-models.md §8): five capped parts, each with notes saying
why it scored what it did. Confidence is not "how much the model likes it": edge counts only
relative to its uncertainty, and an implausibly large edge lowers the score."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

MAX = {"edge_strength": 30, "role_certainty": 20, "data_quality": 20, "market_agreement": 15, "availability": 15}
SIGMA_FLOOR = 0.015


@dataclass
class Inputs:
    edge: float  # edge of the side being scored (probability, may be negative)
    p_model: float
    games_in_history: int
    data_quality: float
    book_novigs: list[float]  # no-vig probability of this side at each book (same line)
    one_sided: bool
    moved_against_pts: float | None  # how far our side's implied probability fell recently (pts)
    is_goalie_prop: bool
    is_game_market: bool
    start_probability: float | None  # goalie props
    start_confirmed: bool
    opp_goalie_confirmed: bool
    lineup_confirmed: bool = False
    toi_cv: float | None = None  # coefficient of variation of the last 5 games' TOI
    role_change: float | None = None  # relative change: last-5 TOI vs the 15 before
    injury_feed: bool = False
    implausible_edge: float = 0.15
    track_record: float | None = None  # realized / expected ROI of graded leans in this market (grading)


@dataclass
class Confidence:
    score: int
    parts: dict[str, int]
    notes: dict[str, list[str]] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {"score": self.score, "parts": self.parts, "max": MAX, "notes": self.notes}


def _clip(x: float, hi: int) -> int:
    return round(max(0.0, min(float(hi), x)))


def score(c: Inputs) -> Confidence:
    notes: dict[str, list[str]] = {k: [] for k in MAX}

    # 1. Edge strength: edge relative to its uncertainty.
    n_eff = max(5, min(c.games_in_history, 82))
    s_model = math.sqrt(max(c.p_model * (1 - c.p_model), 0.0) / n_eff)
    s_books = (max(c.book_novigs) - min(c.book_novigs)) / 2 if len(c.book_novigs) > 1 else 0.0
    sigma = math.sqrt(s_model**2 + s_books**2 + SIGMA_FLOOR**2)
    z = c.edge / sigma
    mult = 1.0 if c.track_record is None else max(0.5, min(1.2, c.track_record))
    edge_pts = 30 * max(0.0, min(z / 2.5, 1.0)) * mult
    notes["edge_strength"].append(f"Edge {c.edge * 100:+.1f} pts is {z:.1f}x its uncertainty (±{sigma * 100:.1f} pts).")
    notes["edge_strength"].append(
        "Not enough graded bets in this market yet, so no track-record adjustment."
        if c.track_record is None
        else f"Track record: graded leans in this market returned {c.track_record:.2f}x their expected value, "
        f"so this part is multiplied by {mult:.2f}."
    )

    # 2. Role certainty.
    role = 20.0
    if c.is_goalie_prop:
        p = c.start_probability if c.start_probability is not None else 0.0
        if c.start_confirmed:
            notes["role_certainty"].append("Starter confirmed (Quick Entry).")
        else:
            role = 20 * p - 4
            notes["role_certainty"].append(f"Starter projected, {p:.0%} start chance, not confirmed.")
    elif c.is_game_market:
        role -= 6
        notes["role_certainty"].append("Lineups not confirmed.")
    else:
        if not c.lineup_confirmed:
            role -= 6
            notes["role_certainty"].append("Lineup not confirmed: assumes he dresses in his usual role.")
        if c.toi_cv is not None:
            pen = min(6.0, c.toi_cv * 30)
            role -= pen
            if pen >= 1:
                notes["role_certainty"].append(f"Ice time varies game to game (CV {c.toi_cv:.0%}).")
        if c.role_change is not None and abs(c.role_change) > 0.2:
            role -= 4
            notes["role_certainty"].append(f"Recent ice-time change of {c.role_change:+.0%}: role may be shifting.")

    # 3. Data quality.
    dq = c.data_quality * 20
    notes["data_quality"].append(f"Projection data quality {c.data_quality:.0%}.")
    if not c.is_game_market and c.games_in_history < 10:
        dq -= 4
        notes["data_quality"].append(f"Only {c.games_in_history} games of history.")
    elif not c.is_game_market and c.games_in_history < 30:
        dq -= 2
        notes["data_quality"].append(f"{c.games_in_history} games of history.")

    # 4. Market agreement.
    mkt = 15.0
    if c.one_sided:
        mkt -= 3
        notes["market_agreement"].append("Only one side offered: the margin can't be removed exactly.")
    if len(c.book_novigs) < 2:
        mkt -= 3
        notes["market_agreement"].append("One book at this line: no cross-check.")
    elif s_books * 2 > 0.03:
        mkt -= 4
        notes["market_agreement"].append(f"Books disagree by {s_books * 200:.1f} pts.")
    if c.moved_against_pts is not None and c.moved_against_pts > 2:
        mkt -= 4
        notes["market_agreement"].append(f"Price moved {c.moved_against_pts:.1f} pts against this side recently.")
    if c.edge > c.implausible_edge:
        mkt -= 8
        notes["market_agreement"].append("Edge is implausibly large: usually missing information (injury, lineup).")
    elif c.edge > 0.10:
        mkt -= 3
        notes["market_agreement"].append("Large edge: check for news the model doesn't know.")

    # 5. Availability.
    av = 15.0
    if not c.injury_feed:
        av -= 3
        notes["availability"].append("No injury feed: player status not verified.")
    if c.is_goalie_prop and not c.start_confirmed:
        p = c.start_probability if c.start_probability is not None else 0.0
        av -= 12 * (1 - p)
        notes["availability"].append("Goalie props usually void if he doesn't start.")
    if not c.is_goalie_prop and not c.is_game_market and not c.opp_goalie_confirmed:
        av -= 3
        notes["availability"].append("Opposing goalie not confirmed.")

    defaults = {
        "role_certainty": "Role looks stable.",
        "market_agreement": "Books agree and the price is steady.",
        "availability": "No availability concerns known.",
    }
    for k, text in defaults.items():
        if not notes[k]:
            notes[k].append(text)
    parts = {
        "edge_strength": _clip(edge_pts, 30),
        "role_certainty": _clip(role, 20),
        "data_quality": _clip(dq, 20),
        "market_agreement": _clip(mkt, 15),
        "availability": _clip(av, 15),
    }
    return Confidence(sum(parts.values()), parts, notes)
