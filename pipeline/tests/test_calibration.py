"""Live isotonic calibration: PAV, the held-out decision, and its use in pricing."""

from __future__ import annotations

import random
from datetime import UTC, date, datetime, timedelta
from itertools import pairwise

import pytest
import synth

from rinkx.grading import calibration as cal
from rinkx.grading import grade
from rinkx.grading.performance import performance
from rinkx.pricing.model_probs import AtLine
from rinkx.pricing.price import PricingConfig, _with_note, calibrate, price_line


def test_pav_is_monotone_and_pools_violators():
    xs, ys = cal.pav([0.1, 0.2, 0.3, 0.4], [1, 0, 1, 1])
    assert xs == pytest.approx([0.15, 0.35]) and ys == pytest.approx([0.5, 0.99])  # 1.0 clipped
    rng = random.Random(1)
    p = [rng.random() for _ in range(500)]
    y = [1 if rng.random() < q else 0 for q in p]
    xs, ys = cal.pav(p, y)
    assert all(a < b for a, b in pairwise(xs)) and all(a < b for a, b in pairwise(ys))
    assert all(cal.CLIP[0] <= v <= cal.CLIP[1] for v in ys)


def test_apply_interpolates_and_is_flat_beyond_the_ends():
    k = {"x": [0.2, 0.6], "y": [0.3, 0.5]}
    assert cal.apply(k, 0.4) == pytest.approx(0.4)
    assert cal.apply(k, 0.1) == 0.3 and cal.apply(k, 0.9) == 0.5
    assert cal.apply({"x": [], "y": []}, 0.42) == 0.42


def _rows(n, truth, seed=2):
    rng = random.Random(seed)
    out = []
    for _ in range(n):
        p = rng.uniform(0.2, 0.8)
        out.append((p, 1 if rng.random() < truth(p) else 0))
    return out


def test_applied_only_when_it_improves_held_out_brier():
    overconfident = _rows(3000, lambda p: 0.5 + 0.4 * (p - 0.5))  # the model is too sure of itself
    f = cal.fit_market(overconfident)
    assert f["applied"] and f["brier_cal"] < f["brier_raw"] - cal.MIN_GAIN
    assert f["n_fit"] == 3000 and f["n_holdout"] == 900
    assert cal.apply(f["knots"], 0.75) == pytest.approx(0.6, abs=0.05)  # pulled toward the truth (0.6)

    honest = _rows(3000, lambda p: p, seed=3)
    g = cal.fit_market(honest)
    assert not g["applied"] and g["reason"] == "no_improvement"
    assert cal.fit_market(honest[:100])["reason"] == "too_few"


def test_calibration_in_pricing_keeps_pushes_and_explains_itself():
    c = {"knots": {"x": [0.3, 0.7], "y": [0.35, 0.55]}, "n": 812, "brier_raw": 0.2431, "brier_cal": 0.2398}
    at = AtLine(0.6, 0.3, 0.1)  # P(over | no push) = 2/3
    new, note = calibrate(at, c)
    assert new.p_push == 0.1 and new.p_over + new.p_under == pytest.approx(0.9)
    assert new.over_given_no_push == pytest.approx(cal.apply(c["knots"], 2 / 3))
    pr = price_line("over_under", new, 2.0, -110, -110, 0.9, PricingConfig(0.03, 0.6, 0.15), "shots on goal")
    pr = _with_note(pr, note, "over")
    i = next(k for k, s in enumerate(pr.calculation) if s.startswith("Model P("))
    assert pr.calculation[i + 1].startswith("Calibrated from live results: P(over) 0.6667 → 0.5333")
    assert "812 graded props" in pr.calculation[i + 1]
    assert calibrate(at, None) == (at, None)


def test_fit_from_graded_history_and_publish(tmp_path):
    conn, truth = synth.build(str(tmp_path / "c.db"), teams=10, days=120, seed=6)
    synth.add_priced_history(conn, truth, days=60)
    last = conn.execute("SELECT max(game_date) FROM games").fetchone()[0]
    today = date.fromisoformat(last) + timedelta(days=1)
    now = datetime(today.year, today.month, today.day, 12, tzinfo=UTC)
    grade.run_grading(conn, now, today)
    rows = cal.graded_rows(conn)
    shots = conn.execute("SELECT id FROM markets WHERE code = 'skater_shots_on_goal'").fetchone()[0]
    assert len(rows[shots]) >= cal.MIN_ROWS
    assert cal.run_calibration(conn, now) == 1
    assert cal.run_calibration(conn, now + timedelta(hours=1)) == 0  # not refit within a day
    pub = performance(conn, now)["calibrators"]
    assert len(pub) == 1 and pub[0]["market"] == "Shots on Goal" and pub[0]["n_holdout"] > 0
    assert pub[0]["reason"] in ("applied", "no_improvement")
    assert (shots in cal.current(conn)) == pub[0]["applied"]
    # A refit replaces the current calibrator; only one is current per market.
    cal.run_calibration(conn, now + timedelta(days=1))
    assert conn.execute("SELECT count(*) FROM calibrators WHERE is_current = 1").fetchone()[0] == 1
