"""Prop score arithmetic (rinkx.pricing.scores): transforms, weights, missing parts."""

import pytest

from rinkx.pricing import scores


def test_transforms_are_centred_and_bounded():
    cfg = scores.config()
    assert scores.from_ratio(1.0, cfg) == 50
    assert scores.from_ratio(1.2, cfg) > 50 > scores.from_ratio(0.8, cfg)
    assert scores.from_ratio(100.0, cfg) == pytest.approx(100, abs=1e-6)
    assert scores.from_edge(0.0, cfg.edge_scale) == 50 and scores.from_edge(None, 0.06) is None
    assert scores.from_z(-5, cfg) < 0.01


def test_weights_from_config_and_missing_parts_are_rescaled():
    cfg = scores.config()
    assert sum(cfg.intelligence.values()) == 100 and cfg.intelligence["model_edge"] == 25
    s, parts = scores.combine({"model_edge": (80.0, "x"), "projection_vs_line": (60.0, "y"), "matchup": (50.0, "z")},
                              {"model_edge": 25, "projection_vs_line": 20, "matchup": 10, "power_play": 5})  # fmt: skip
    assert s == round((80 * 25 + 60 * 20 + 50 * 10) / 55)
    pp = next(p for p in parts if p["part"] == "power_play")
    assert pp["score"] is None and pp["points"] is None and pp["detail"] == "no data"
    # Under half the weight with data: no score rather than a guess.
    assert scores.combine({"model_edge": (90.0, "")}, cfg.intelligence)[0] is None
