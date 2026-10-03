""" "Why this prop?": bullet points and a short summary built only from the frozen facts of a priced
prop (prediction_scores.facts) and its pricing. Nothing here is generated freely: each sentence is
a template filled with a number that is shown in the bullets above it, and a missing input is left
out rather than guessed."""

from __future__ import annotations

from typing import Any

SIDE_WORD = {"over": "over", "under": "under", "yes": "yes", "no": "no", "home": "home", "away": "away"}


def _mmss(s: float | None) -> str | None:
    if s is None:
        return None
    s = round(s)
    return f"{s // 60}:{s % 60:02d}"


def _rate(h: list[int] | None) -> str | None:
    return f"{h[0]}/{h[1]} ({h[0] / h[1]:.0%})" if h and h[1] else None


def bullets(
    facts: dict[str, Any], *, label: str, p_model: float, p_market: float | None, edge: float | None
) -> list[dict[str, str]]:
    side = facts.get("side")
    line = facts.get("line")
    out: list[dict[str, str]] = []

    def add(k: str, v: str | None) -> None:
        if v is not None:
            out.append({"label": k, "value": v})

    add(f"Projected {label}", f"{facts['projection']:.2f}" if "projection" in facts else None)
    add("Market line", f"{side} {line:g}" if line is not None else side)
    add("Projection vs line", f"{facts['projection_diff']:+.2f}" if "projection_diff" in facts else None)
    add("Model probability", f"{p_model:.1%}")
    add("Market probability", f"{p_market:.1%}" if p_market is not None else None)
    add("Model edge", f"{edge * 100:+.1f} pts" if edge is not None else None)
    add("Last 10 at this line", _rate(facts.get("l10_hit")))
    add("Season at this line", _rate(facts.get("season_hit")))
    add("Expected TOI", _mmss(facts.get("expected_toi_s")))
    add("Line", facts.get("line_slot"))
    add(
        "Power play",
        f"PP{facts['pp_unit']}" if facts.get("pp_unit") else ("not on a PP unit" if "line_slot" in facts else None),
    )
    add("Opponent shots allowed", f"{facts['opp_shots_allowed']:.1f}/game" if facts.get("opp_shots_allowed") else None)
    add("Shot Environment", f"{facts['shot_environment']}/100" if facts.get("shot_environment") is not None else None)
    return out


def summary(facts: dict[str, Any], *, label: str, edge: float | None, p_model: float, p_market: float | None) -> str:
    """Two or three plain sentences, each restating a number from the bullets."""
    side, line = facts.get("side"), facts.get("line")
    bits: list[str] = []
    what = (
        f"{SIDE_WORD.get(side or '', side)} {line:g} {label.lower()}"
        if line is not None
        else f"{side} ({label.lower()})"
    )
    if "projection" in facts and line is not None:
        bits.append(
            f"The model projects {facts['projection']:.2f} against a line of {line:g} "
            f"({facts['projection_diff']:+.2f})."
        )
    if p_market is not None and edge is not None:
        bits.append(
            f"It gives the {what} {p_model:.1%} against the market's {p_market:.1%}, "
            f"{'an edge' if edge > 0 else 'a gap'} of {edge * 100:+.1f} points."
        )
    support = []
    if facts.get("l10_hit"):
        h = facts["l10_hit"]
        support.append(f"it went {SIDE_WORD.get(side or '', side)} in {h[0]} of his last {h[1]} games")
    if facts.get("pp_unit"):
        support.append(f"he is on PP{facts['pp_unit']}")
    if facts.get("shot_environment") is not None:
        support.append(f"the shot environment scores {facts['shot_environment']}/100")
    if support:
        bits.append("For context, " + ", ".join(support) + ".")
    bits.append("These are estimates, and props like this lose often.")
    return " ".join(bits)
