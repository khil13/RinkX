"""The last steps for each priced prop: the range of its projection, the risks against it ("Why
not?") and a decision (Bettable value / Lean / Pass / Avoid).

Nothing here adds a new estimate. The range is read off the projection's own probability
distribution; every risk is a number the row already carries (price movement, projection vs line,
hit rate and its sample, data quality, missing inputs, line age, the size of the edge); the
decision combines the calibrated probability, the price, confidence and those risks. A missing
input is skipped, never guessed. Confidence (how much the inputs support the number) and value
(EV at the price) stay separate fields; the decision reason states both.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

import numpy as np

from rinkx.timeutil import parse_iso

QUANTILES = (("floor", 0.10), ("p25", 0.25), ("median", 0.50), ("p75", 0.75), ("ceiling", 0.90))
# Spread of the distribution (sd / mean). Below LOW the stat lands close to its mean most nights.
SPREAD_LOW, SPREAD_HIGH = 0.5, 0.9
WIDE_MIN_MEAN = 1.5  # below this (goals, points) every prop is a 0-or-1 outcome: spread isn't a risk

THIN_MARGIN = 0.3  # projection this close to the line (in the lean's direction)
MOVED_PTS = 2.0  # implied probability moved this far against the side in 24 h
MOVED_SERIOUS_PTS = 4.0
COLD_RATE, COLD_MIN = 0.4, 5  # hit this side under 40% of at least 5 recent games
SMALL_SAMPLE = 10  # games behind a hit rate
DQ_WARN = 0.8
DQ_FLOOR = 0.6  # config/pricing.yml data_quality_floor
LONG_PRICE = 300
STALE_HOURS = 3.0
IMPLAUSIBLE_PTS = 15.0  # config/pricing.yml implausible_edge_pts
CAL_SHIFT_PTS = 3.0

VALUE_EV = 0.05
VALUE_CONFIDENCE = 60
MAX_RISKS_FOR_VALUE = 1
AVOID_RISKS = 3

MISSING = {
    "goalie_unconfirmed": "Starting goalie not confirmed",
    "lineup_unconfirmed": "Lineup not confirmed",
    "injuries_not_connected": "Injury feed unavailable for this run",
}

_CAL = re.compile(r"^Calibrated from live results: P\(\w+\) ([0-9.]+) → ([0-9.]+)")


def projection_range(pmf: list[float] | None) -> dict[str, Any] | None:
    """Floor (10th percentile), p25, median, p75 and ceiling (90th) of the projection, with its
    spread. None without a distribution."""
    if not pmf:
        return None
    p = np.asarray(pmf, dtype=float)
    total = float(p.sum())
    if total <= 0:
        return None
    p = p / total
    c = np.cumsum(p)
    ks = np.arange(len(p), dtype=float)
    mean = float((ks * p).sum())
    sd = float(np.sqrt(max(((ks - mean) ** 2 * p).sum(), 0.0)))
    out: dict[str, Any] = {k: int(np.searchsorted(c, q - 1e-9)) for k, q in QUANTILES}
    cv = sd / mean if mean > 0 else None
    out |= {
        "mean": round(mean, 2),
        "sd": round(sd, 2),
        "spread": None if cv is None else ("low" if cv < SPREAD_LOW else "medium" if cv < SPREAD_HIGH else "high"),
    }
    return out


def calibration_shift(calculation: list[str], over_side: bool) -> dict[str, float] | None:
    """Raw and calibrated P(scored side | no push), when a live calibrator changed the number."""
    for line in calculation:
        m = _CAL.match(line)
        if m:
            raw, cal = float(m.group(1)), float(m.group(2))
            if not over_side:
                raw, cal = 1 - raw, 1 - cal
            return {"raw": round(raw, 4), "calibrated": round(cal, 4)}
    return None


def _risk(code: str, text: str, serious: bool = False) -> dict[str, Any]:
    return {"code": code, "text": text, "serious": serious}


def risks(
    row: dict[str, Any], facts: dict[str, Any] | None, rng: dict[str, Any] | None, now: datetime
) -> list[dict[str, Any]]:
    """What argues against the scored side, from the row's own numbers."""
    out: list[dict[str, Any]] = []
    f = facts or {}
    side = row["side_scored"]
    over = side in ("over", "yes", "home")
    edge, ev, price = row["edge"], row["ev"], row["price"]

    moved = f.get("moved_against_pts")
    if moved is not None and moved >= MOVED_PTS:
        out.append(
            _risk("moved", f"Price moved {moved:.1f} pts against this side in 24 h", serious=moved >= MOVED_SERIOUS_PTS)
        )
    diff = f.get("projection_diff")
    if diff is not None and row["kind"] == "over_under":
        toward = diff if over else -diff
        if toward <= 0:
            out.append(_risk("wrong_side", f"Projection is on the other side of the line ({diff:+.2f})", serious=True))
        elif toward < THIN_MARGIN:
            out.append(_risk("thin", f"Projection only {toward:.2f} past the line"))
    l10 = f.get("l10_hit")
    if l10 and l10[1] >= COLD_MIN and l10[0] / l10[1] < COLD_RATE:
        out.append(_risk("cold", f"Went {side} in only {l10[0]} of the last {l10[1]} games"))
    if row["subject"]["type"] == "player" and row["kind"] != "moneyline":
        n = max((h[1] for h in (f.get("l10_hit"), f.get("season_hit")) if h), default=0)
        if n < SMALL_SAMPLE:
            text = f"Small sample: {n} games at this line" if n else "No hit-rate history at this line"
            out.append(_risk("sample", text))
    dq = row["data_quality"]
    if dq is not None and dq < DQ_WARN:
        out.append(_risk("data", f"Data quality {dq:.0%}", serious=dq < DQ_FLOOR))
    for m in row["missing_inputs"]:
        if m in MISSING:
            out.append(_risk(m, MISSING[m]))
    if price is not None and price >= LONG_PRICE:
        out.append(_risk("long", f"Long price: the model gives it {row['p_model']:.0%}, so it loses most of the time"))
    if edge is not None and edge * 100 >= IMPLAUSIBLE_PTS:
        out.append(
            _risk(
                "big_edge",
                f"Edge of {edge * 100:.0f} pts: gaps this large usually mean the market knows something",
                serious=True,
            )
        )
    if ev is not None and ev < 0:
        out.append(_risk("neg_ev", f"Negative expected value at {price:+d}" if price is not None else "Negative EV"))
    cal = row.get("calibration")
    if cal and (cal["raw"] - cal["calibrated"]) * 100 >= CAL_SHIFT_PTS:
        out.append(
            _risk("calibrated_down", f"Live results pull the model down: {cal['raw']:.1%} → {cal['calibrated']:.1%}")
        )
    seen = row.get("line_seen_at")
    if seen:
        age = (now - parse_iso(seen)).total_seconds() / 3600
        if age >= STALE_HOURS:
            out.append(_risk("stale", f"Line last seen {age:.0f} h ago"))
    if rng and rng.get("spread") == "high" and rng["mean"] >= WIDE_MIN_MEAN and row["kind"] == "over_under":
        out.append(_risk("wide", f"Wide range: {rng['floor']}-{rng['ceiling']} covers 80% of outcomes"))
    return out


def decision(row: dict[str, Any], rsk: list[dict[str, Any]]) -> dict[str, str]:
    """Bettable value / Lean / Pass / Avoid, with the reason in one sentence."""
    edge, ev, conf = row["edge"], row["ev"], row["confidence"]
    serious = [r for r in rsk if r["serious"]]
    nums = []
    if edge is not None:
        nums.append(f"edge {edge * 100:+.1f} pts")
    if ev is not None:
        nums.append(f"EV {ev * 100:+.1f}%")
    nums.append(f"confidence {conf}" if conf is not None else "no confidence score")
    said = ", ".join(nums)
    if row["p_market"] is None:
        return {"code": "pass", "label": "Pass", "reason": "No market price to compare with."}
    if not row["lean"]:
        if serious or (ev is not None and ev < 0 and edge is not None and edge < 0):
            return {"code": "pass", "label": "Pass", "reason": f"No edge at this price ({said})."}
        return {"code": "pass", "label": "Pass", "reason": f"Below the lean threshold ({said})."}
    if serious or len(rsk) >= AVOID_RISKS:
        why = serious[0]["text"] if serious else f"{len(rsk)} risks against it"
        return {"code": "avoid", "label": "Avoid", "reason": f"{why} ({said})."}
    if (
        ev is not None
        and ev >= VALUE_EV
        and conf is not None
        and conf >= VALUE_CONFIDENCE
        and len(rsk) <= MAX_RISKS_FOR_VALUE
    ):
        return {"code": "value", "label": "Bettable value", "reason": f"Priced above the market ({said})."}
    short = []
    if ev is None or ev < VALUE_EV:
        short.append(f"EV under {VALUE_EV:.0%}")
    if conf is None or conf < VALUE_CONFIDENCE:
        short.append(f"confidence under {VALUE_CONFIDENCE}")
    if len(rsk) > MAX_RISKS_FOR_VALUE:
        short.append(f"{len(rsk)} risks")
    return {"code": "lean", "label": "Lean", "reason": f"An edge, but {', '.join(short)} ({said})."}
