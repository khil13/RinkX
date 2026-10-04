from datetime import UTC, datetime

import numpy as np
from scipy import stats

from rinkx.grading.performance import calibration_by_market
from rinkx.publish import assess

NOW = datetime(2026, 10, 4, 18, tzinfo=UTC)


def _row(**kw):
    base = {
        "subject": {"type": "player", "name": "A Skater"},
        "kind": "over_under",
        "side_scored": "over",
        "lean": "over",
        "line": 2.5,
        "price": -110,
        "p_model": 0.58,
        "p_market": 0.5,
        "edge": 0.08,
        "ev": 0.107,
        "confidence": 70,
        "data_quality": 0.95,
        "missing_inputs": [],
        "calculation": [],
        "calibration": None,
        "line_seen_at": "2026-10-04T17:30:00Z",
    }
    return base | kw


GOOD_FACTS = {"projection_diff": 0.6, "l10_hit": [7, 10], "season_hit": [20, 30]}


def test_range_is_read_off_the_distribution():
    pmf = stats.poisson.pmf(np.arange(30), 3.0).tolist()
    r = assess.projection_range(pmf)
    assert r is not None
    # Poisson(3): P(X<=1)=0.199, P(X<=2)=0.423, P(X<=3)=0.647, P(X<=4)=0.815, P(X<=5)=0.916
    assert (r["floor"], r["p25"], r["median"], r["p75"], r["ceiling"]) == (1, 2, 3, 4, 5)
    assert r["mean"] == 3.0 and r["spread"] == "medium"  # sd/mean = 0.58
    saves = stats.nbinom.pmf(np.arange(80), 60, 60 / 87).tolist()  # mean 27
    assert assess.projection_range(saves)["spread"] == "low"
    goals = stats.poisson.pmf(np.arange(10), 0.3).tolist()
    assert assess.projection_range(goals)["spread"] == "high"
    assert assess.projection_range(None) is None and assess.projection_range([]) is None


def test_calibration_shift_reads_the_note_for_either_side():
    calc = ["Model P(over) = 0.6000", "Calibrated from live results: P(over) 0.6000 → 0.5500 (isotonic fit on 400)"]
    assert assess.calibration_shift(calc, True) == {"raw": 0.6, "calibrated": 0.55}
    assert assess.calibration_shift(calc, False) == {"raw": 0.4, "calibrated": 0.45}
    assert assess.calibration_shift(["Model P(over) = 0.6"], True) is None


def test_clean_lean_is_bettable_value():
    row = _row()
    rsk = assess.risks(row, GOOD_FACTS, None, NOW)
    assert rsk == []
    d = assess.decision(row, rsk)
    assert d["code"] == "value" and "confidence 70" in d["reason"] and "EV +10.7%" in d["reason"]


def test_low_confidence_or_ev_is_a_lean_and_says_why():
    row = _row(confidence=45, ev=0.03)
    d = assess.decision(row, assess.risks(row, GOOD_FACTS, None, NOW))
    assert d["code"] == "lean"
    assert "EV under 5%" in d["reason"] and "confidence under 60" in d["reason"]


def test_risks_come_from_the_row_numbers():
    facts = {"projection_diff": 0.1, "l10_hit": [2, 6], "moved_against_pts": 2.5}
    row = _row(
        data_quality=0.7,
        missing_inputs=["goalie_unconfirmed", "odds_not_connected"],
        price=350,
        line_seen_at="2026-10-04T10:00:00Z",
        calibration={"raw": 0.6, "calibrated": 0.55},
    )
    codes = {r["code"] for r in assess.risks(row, facts, None, NOW)}
    expected = {"moved", "thin", "cold", "sample", "data", "goalie_unconfirmed", "long", "calibrated_down", "stale"}
    assert codes == expected
    assert assess.decision(row, assess.risks(row, facts, None, NOW))["code"] == "avoid"


def test_serious_risk_is_avoid_even_alone():
    rsk = assess.risks(_row(), GOOD_FACTS | {"moved_against_pts": 5.0}, None, NOW)
    assert [r["code"] for r in rsk] == ["moved"]
    d = assess.decision(_row(), rsk)
    assert d["code"] == "avoid" and d["reason"].startswith("Price moved 5.0 pts against")


def test_big_edge_blocks_value_but_alone_is_a_lean():
    row = _row(edge=0.2)
    rsk = assess.risks(row, GOOD_FACTS, None, NOW)
    assert [r["code"] for r in rsk] == ["big_edge"] and not rsk[0]["serious"]
    assert assess.decision(row, [*rsk, assess._risk("x", "y")])["code"] == "lean"


def test_missing_inputs_are_listed_but_not_counted():
    row = _row(missing_inputs=["lineup_unconfirmed", "injuries_not_connected"])
    rsk = assess.risks(row, GOOD_FACTS, None, NOW)
    assert {r["code"] for r in rsk} == {"lineup_unconfirmed", "injuries_not_connected"}
    assert assess.decision(row, rsk)["code"] == "value"


def test_no_lean_is_pass_and_no_market_is_pass():
    assert assess.decision(_row(lean=None, edge=0.01, ev=0.02), [])["code"] == "pass"
    assert assess.decision(_row(p_market=None), [])["reason"] == "No market price to compare with."


def test_wide_range_only_where_the_line_has_room():
    sog = assess.projection_range(stats.nbinom.pmf(np.arange(40), 1.0, 1 / 3.5).tolist())  # mean 2.5, wide
    assert sog["spread"] == "high"
    assert "wide" in {r["code"] for r in assess.risks(_row(), GOOD_FACTS, sog, NOW)}
    goals = assess.projection_range(stats.poisson.pmf(np.arange(10), 0.3).tolist())
    assert "wide" not in {r["code"] for r in assess.risks(_row(), GOOD_FACTS, goals, NOW)}


def _graded(market, p_over, result):
    return {"market_name": market, "p_model_over": p_over, "result": result}


def test_calibration_verdict_per_market():
    rng = np.random.default_rng(1)
    rows = []
    for _ in range(400):  # says 70% for the favourite, it wins 55%: overconfident
        rows.append(_graded("Shots", 0.7, "over" if rng.random() < 0.55 else "under"))
    for _ in range(400):  # says 60%, wins 60%
        rows.append(_graded("Points", 0.4, "under" if rng.random() < 0.6 else "over"))
    for _ in range(400):  # says 55%, wins 70%: underconfident
        rows.append(_graded("Hits", 0.55, "over" if rng.random() < 0.7 else "under"))
    rows += [_graded("Saves", 0.6, "over")] * 10 + [_graded("Saves", 0.6, "push")]
    out = {b["market"]: b for b in calibration_by_market(rows)}
    assert out["Shots"]["verdict"] == "over"
    assert out["Points"]["verdict"] == "well" and abs(out["Points"]["mean_p"] - 0.6) < 1e-9
    assert out["Hits"]["verdict"] == "under"
    assert out["Saves"]["verdict"] == "insufficient" and out["Saves"]["n"] == 10
