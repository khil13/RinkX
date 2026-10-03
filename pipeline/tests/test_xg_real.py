"""The xG model on real NHL shots (tests/fixtures/nhl/xg_shots_20252026.csv.gz, recorded by
scripts/record_xg_shots.py from the 2025-26 play-by-play and parsed by the pipeline's parser).

The synthetic tests prove the fitting and the gate work. These prove the model gives sensible
numbers on actual NHL shots: it passes its own held-out test, beats a distance-and-angle-only
fit, and its coefficients and example probabilities point the way hockey says they should."""

from __future__ import annotations

import csv
import gzip
import json
from pathlib import Path

import numpy as np
import pytest

from rinkx.ingestion.nhl.parse import parse_play_by_play
from rinkx.models import xg

FIXTURES = Path(__file__).parent / "fixtures/nhl"
SHOTS = FIXTURES / "xg_shots_20252026.csv.gz"


def _rows() -> list[tuple]:
    with gzip.open(SHOTS, "rt") as f:
        raw = list(csv.DictReader(f))
    key = lambda r: (int(r["game"]), int(r["period"]), int(r["period_seconds"]), int(r["event_idx"]))  # noqa: E731
    out = []
    for i, r in enumerate(sorted(raw, key=key)):
        out.append(
            (
                i,
                int(r["game"]),
                r["game_date"],
                int(r["period"]),
                int(r["period_seconds"]),
                r["event_type"],
                int(r["team"]),
                int(r["x"]) if r["x"] else None,
                int(r["y"]) if r["y"] else None,
                r["shot_type"] or None,
                r["strength_state"],
                int(r["empty_net"]),
            )
        )
    return out


@pytest.fixture(scope="module")
def real() -> tuple[xg.Shots, dict]:
    shots = xg.from_rows(r for r in _rows() if r[5] != "block")
    dates = sorted(set(shots.dates.tolist()))
    test_start = dates[int(len(dates) * 0.6)]  # same 60/40 split as the pipeline
    return shots, xg.evaluate(shots, test_start)


def _p(w: np.ndarray, x: float, y: float, kind: str = "wrist", strength: str = "5v5", rebound: bool = False) -> float:
    return float(xg.predict(np.array([xg._row(x, y, kind, strength, rebound)]), w)[0])


def test_fixture_is_real_and_large_enough(real):
    shots, res = real
    assert len(shots.ids) > 15_000  # roughly 260 games of unblocked attempts
    assert 0.05 < shots.y.mean() < 0.09  # NHL goals per unblocked attempt is about 6-7%
    assert res["n_train"] >= xg.MIN_TRAIN and res["n_test"] >= xg.MIN_TEST


def test_passes_its_held_out_test_on_real_shots(real):
    _, res = real
    assert res["passed"], res["reason"]
    m = res["metrics"]
    assert m["log_loss"] < m["baseline_log_loss"]
    assert m["vs_distance_only"]["mean"] > 0  # shot type, strength and rebounds add something
    assert m["auc"] is not None and m["auc"] > 0.68
    assert abs(m["mean_xg_test"] - m["goal_rate_test"]) < 0.01  # calibrated in the large
    assert m["ece"] <= xg.ECE_MAX


def test_coefficients_and_examples_make_hockey_sense(real):
    _, res = real
    c = res["coef"]
    w = np.array([c[k] for k in xg.FEATURES])
    assert c["pp"] > 0
    # Rebounds: on these games a quick follow-up attempt is *not* more likely to score once its
    # distance and angle are known (the coefficient comes out slightly negative, also when only
    # follow-ups to a shot on goal count). The model is left to the data; rebounds still score
    # more often overall because they come from close in.
    slot = _p(w, 80, 0)
    point = _p(w, 30, 25)
    assert 0.07 < slot < 0.35, slot  # a wrist shot from the slot
    assert point < 0.04, point  # from the point
    assert slot > 4 * point
    assert _p(w, 80, 0, strength="5v4") > slot
    assert _p(w, 95, 10) < slot  # from behind the goal line
    # farther is worse, all else equal
    assert _p(w, 85, 0) > _p(w, 70, 0) > _p(w, 50, 0) > _p(w, 30, 0)


def test_scores_every_attempt_of_a_recorded_game(real):
    """The raw recorded play-by-play, through the pipeline's parser and xG features."""
    _, res = real
    w = np.array([res["coef"][k] for k in xg.FEATURES])
    pbp = parse_play_by_play(json.loads((FIXTURES / "pbp_2025021012.json").read_text()))
    rows = [
        (i, 1, "2026-03-10", e.period, e.period_seconds, e.event_type, e.shooter_team_id, e.x, e.y, e.shot_type,
         e.strength_state, int(e.empty_net))
        for i, e in enumerate(pbp.shots)
        if e.event_type != "block"
    ]  # fmt: skip
    shots = xg.from_rows(rows)
    p = xg.predict(shots.X, w)
    assert len(p) > 60 and np.all((p > 0) & (p < 1))
    goals = int(shots.y.sum())
    assert goals >= 1
    # a team's xG in one game lands in a plausible range (about 1.5-4.5 for NHL teams)
    assert 1.0 < p.sum() / 2 < 6.0
