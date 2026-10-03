"""Same-game simulation: inputs built from published projections, and the joint behaviour it
should have (rinkx.correlation.sim; the browser runs the same code, see fixtures/sim_vectors.json)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
import synth

from rinkx.correlation import sim
from rinkx.models import project


@pytest.fixture(scope="module")
def inputs():
    conn, _ = synth.build(teams=20, days=200, seed=22, lines=True)
    today = date(2025, 10, 7) + timedelta(days=200)
    now = datetime(today.year, today.month, today.day, 15, tzinfo=UTC)
    gid = synth.add_upcoming(conn, today)
    project.run_models(conn, now, today)
    game = conn.execute("SELECT * FROM games WHERE id = ?", (gid,)).fetchone()
    model = sim.assist_model(conn)
    x = sim.game_inputs(conn, game, model)
    assert x is not None
    p_home = conn.execute(
        "SELECT p.mean FROM game_projections p JOIN markets m ON m.id = p.market_id WHERE p.game_id = ? "
        "AND m.code = 'game_total' AND p.is_current = 1",
        (gid,),
    ).fetchone()[0]
    return x, model, p_home


def test_inputs_come_from_projections_and_play_by_play(inputs):
    x, model, _ = inputs
    assert {k["side"] for k in x["skaters"]} == {"home", "away"}
    assert len(x["skaters"]) == 36 and x["goalies"]["home"] and x["goalies"]["away"]
    # No per-goal assists in the synthetic play-by-play: shares from season totals, no linemate boost.
    assert model["assists_per_goal_source"] == "season_totals" and model["boost"] == 1.0
    assert model["boost_source"] == "not_enough_data"
    assert sum(x["goals"]["home"]) == pytest.approx(1, abs=1e-4)
    assert len(x["mates"][str(x["skaters"][0]["id"])]) == 2  # linemates from the projections' lines


def test_simulation_agrees_with_the_game_model_and_moves_legs_together(inputs):
    x, _, total = inputs
    home = [k for k in x["skaters"] if k["side"] == "home"]
    top = max(home, key=lambda k: k["g"])
    legs = [
        {"market": "game_moneyline", "side": "home"},
        {"market": "goalie_win", "player": x["goalies"]["home"], "side": "yes"},
        {"market": "skater_shots_on_goal", "player": top["id"], "line": 2.5, "side": "over"},
        {"market": "goalie_saves", "player": x["goalies"]["away"], "line": 25.5, "side": "over"},
    ]
    out = sim.simulate(x, legs, n=4000, seed=3)
    p = [e / out["n"] for e in out["each"]]
    # Same goal model, OT and shootout rules: the average simulated total (as books count it) matches.
    s, rng = sim.Sim(x), sim.Rng(9)
    games = [s.one(rng) for _ in range(4000)]
    mean = sum(g.team_goals["home"] + g.team_goals["away"] + g.shootout for g in games) / len(games)
    assert mean == pytest.approx(total, abs=0.1)
    assert 0.3 < p[0] < 0.7
    assert out["each"][0] == out["each"][1]  # the home starter wins exactly when home wins
    both = sim.simulate(x, legs[2:], n=4000, seed=3)
    pa, pb = (e / both["n"] for e in both["each"])
    assert both["all"] / both["n"] > pa * pb  # his shots are the other goalie's saves
    # First goal: every goal has one scorer, so the yes-probabilities add up to P(any goal).
    fg = sim.simulate(
        x, [{"market": "skater_first_goal", "player": k["id"], "side": "yes"} for k in x["skaters"]], 2000, 4
    )
    assert sum(fg["each"]) / fg["n"] == pytest.approx(1, abs=0.01)
