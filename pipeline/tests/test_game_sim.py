"""Game model (v1.1): closed-form outcomes vs. brute-force simulation, the walk-forward gate,
and live game projections. Synthetic data only (tests/synth.py)."""

from __future__ import annotations

import copy
import json
import math
from datetime import UTC, date, datetime, timedelta

import numpy as np
import pytest
import synth

from rinkx.models import fit, game_sim, history, project
from rinkx.models.game_sim import GameChoice
from rinkx.models.state import State, walk

START = date(2025, 10, 7)
DAYS = 200
NOW = datetime(2026, 4, 25, 15, 0, tzinfo=UTC)


def _cols(lam_h: float, lam_a: float, *, playoff: bool = False, q: float = 0.6, so_home: float = 0.52) -> dict:
    one = lambda v: np.array([v], dtype=float)  # noqa: E731
    c = {"gL": one(1.0), "ot_q": one(q), "so_home": one(so_home), "playoff": one(float(playoff))}
    for side, lam in (("h", lam_h), ("a", lam_a)):
        c[f"{side}.S"] = one(lam)
    return c


def _simulate(lam_h: float, lam_a: float, q: float, so_home: float, playoff: bool, n: int = 400_000):
    rng = np.random.default_rng(0)
    h, a = rng.poisson(lam_h, n), rng.poisson(lam_a, n)
    tie = h == a
    ot_goal = tie & ((rng.random(n) < q) | playoff)
    ot_home = rng.random(n) < lam_h / (lam_h + lam_a)
    so_home_win = rng.random(n) < so_home
    home_win = (h > a) | (tie & ot_goal & ot_home) | (tie & ~ot_goal & so_home_win)
    h_tot = h + (ot_goal & ot_home)
    a_tot = a + (ot_goal & ~ot_home)
    # first goal: with both processes Poisson, the first goal is home with prob lam_h / (lam_h + lam_a)
    any_reg = (h + a) > 0
    first_home = (any_reg & (rng.random(n) < lam_h / (lam_h + lam_a))) | (~any_reg & ot_goal & ot_home)
    return {
        "home_win": home_win.mean(),
        "tie": tie.mean(),
        "so_home": (a_tot == 0).mean(),
        "so_away": (h_tot == 0).mean(),
        "first_home": first_home.mean(),
        "game_total_mean": (h_tot + a_tot).mean(),
        "home_total_3plus": (h_tot >= 3).mean(),
    }


@pytest.mark.parametrize("playoff", [False, True])
def test_closed_form_matches_simulation(playoff):
    ch = GameChoice(factors=(), size=math.inf)
    o = game_sim.outcomes(_cols(3.3, 2.7, playoff=playoff), ch)
    sim = _simulate(3.3, 2.7, 0.6, 0.52, playoff)
    assert o.p_home_win[0] == pytest.approx(sim["home_win"], abs=0.003)
    assert o.p_tied_reg[0] == pytest.approx(sim["tie"], abs=0.003)
    assert o.so_home[0] == pytest.approx(sim["so_home"], abs=0.002)
    assert o.so_away[0] == pytest.approx(sim["so_away"], abs=0.002)
    assert o.p_home_first[0] == pytest.approx(sim["first_home"], abs=0.003)
    ks = np.arange(o.game_total.shape[1])
    assert (o.game_total[0] * ks).sum() == pytest.approx(sim["game_total_mean"], abs=0.01)
    assert o.home_total[0][3:].sum() == pytest.approx(sim["home_total_3plus"], abs=0.003)
    for m in (o.home_total, o.away_total, o.game_total):
        assert m[0].sum() == pytest.approx(1, abs=1e-6)


def test_hosmer_lemeshow_flags_miscalibration():
    rng = np.random.default_rng(1)
    p = rng.uniform(0.1, 0.9, 5000)
    y = (rng.random(5000) < p).astype(float)
    assert game_sim.hosmer_lemeshow(p, y) > 0.05
    assert game_sim.hosmer_lemeshow(np.clip(p * 1.3, 0, 0.99), y) < 0.01


@pytest.fixture(scope="module")
def league():
    return synth.build(teams=20, days=DAYS, seed=22)


@pytest.fixture(scope="module")
def report(league) -> dict:
    conn, _ = league
    return fit.evaluate(fit.collect(walk(history.load(conn))))


def test_game_gate(report):
    stats = report["stats"]
    for s in ("team_goals", "first_goal", "shutout"):
        assert stats[s]["passed"], (s, stats[s].get("reason"))
        for b in stats[s]["baselines"].values():
            assert b["model_minus_baseline"]["lo"] > 0
    assert stats["team_goals"]["mean_pred"] == pytest.approx(stats["team_goals"]["mean_actual"], rel=0.05)
    # Win probability is reported either way; it is published only if it passes.
    assert set(stats["win"]["baselines"]) == {"home_rate", "log5_record"}
    assert "game" in report["choices"]


def test_shootout_goal_is_not_a_goal(league):
    conn, _ = league
    so = conn.execute("SELECT id, home_score, away_score FROM games WHERE ended_in = 'SO' LIMIT 1").fetchone()
    rec = next(g for g in history.load(conn) if g.game_id == so[0])
    assert rec.home_goals == rec.away_goals  # tied after OT; the +1 on the final score is removed
    assert rec.home_won == (so[1] > so[2])


def _passing(report: dict) -> dict:
    """The live code path for every game market, regardless of which passed on this small league."""
    r = copy.deepcopy(report)
    for s in game_sim.GAME_STATS:
        r["stats"][s]["passed"] = True
    return r


def test_live_game_projections(tmp_path):
    conn, truth = synth.build(str(tmp_path / "g.db"), teams=20, days=DAYS, seed=22)
    game_id = synth.add_upcoming(conn, START + timedelta(days=DAYS))
    st = State()
    rep = fit.evaluate(fit.collect(walk(history.load(conn), st)))
    g = conn.execute("SELECT * FROM games WHERE id = ?", (game_id,)).fetchone()
    players, games = project.project_game(conn, st, _passing(rep), g)

    by = {(p.market, p.player): p for p in players}
    ml = next(x for x in games if x.market == "game_moneyline")
    assert 0.05 < ml.mean < 0.95 and ml.pmf[0] + ml.pmf[1] == pytest.approx(1, abs=1e-4)
    totals = {(x.market, x.side): x for x in games if x.market != "game_moneyline"}
    assert set(totals) == {("team_total", "home"), ("team_total", "away"), ("game_total", "game")}
    for x in totals.values():
        assert sum(x.pmf) == pytest.approx(1, abs=1e-3)
    home_total = totals[("team_total", "home")]
    prod = math.prod(1 + f["effect"] for f in home_total.factors)
    assert home_total.inputs["reference_mean"] * prod == pytest.approx(ml.inputs["expected_goals_home"], rel=0.01)

    # Goalie win/shutout only for each team's projected starter; win probabilities are complementary.
    wins = [p for p in players if p.market == "goalie_win"]
    assert len(wins) == 2 and sum(w.mean for w in wins) == pytest.approx(1, abs=1e-6)
    assert len([p for p in players if p.market == "goalie_shutout"]) == 2

    # First goal: each player gets P(team scores first) x his share of team goals.
    fg = [p for p in players if p.market == "skater_first_goal"]
    assert fg
    home_team = g["home_team_id"]
    home_fg = sum(p.mean for p in fg if truth.team_of[p.player] == home_team)
    assert home_fg > 0
    assert all(0 < p.mean < 0.25 for p in fg)

    # Stored: a re-run writes nothing new; a confirmed goalie supersedes with a reason.
    as_of = "2026-04-24T23:00:00Z"
    for gp in games:
        assert project.save_game(conn, game_id, gp, as_of, "scheduled", NOW)
    for gp in games:
        assert not project.save_game(conn, game_id, gp, as_of, "scheduled", NOW)
    rows = conn.execute("SELECT count(*) FROM game_projections WHERE game_id = ? AND is_current = 1", (game_id,))
    assert rows.fetchone()[0] == 4
    assert (
        json.loads(conn.execute("SELECT pmf FROM game_projections WHERE side = 'game'").fetchone()[0])["p"]
        == totals[("game_total", "game")].pmf
    )
    assert ("goalie_win", wins[0].player) in by


def test_stored_report_keeps_game_settings_and_projects(tmp_path):
    conn, _ = synth.build(str(tmp_path / "s.db"), teams=20, days=DAYS, seed=22)
    game_id = synth.add_upcoming(conn, START + timedelta(days=DAYS))
    project.run_models(conn, NOW, START + timedelta(days=DAYS))
    stored, _ = project.latest_report(conn)
    assert stored is not None and "game" in stored["choices"]
    markets = {
        r[0]
        for r in conn.execute(
            "SELECT m.code FROM game_projections p JOIN markets m ON m.id = p.market_id WHERE p.game_id = ? "
            "AND p.is_current = 1",
            (game_id,),
        )
    }
    expected = set()
    if stored["stats"]["team_goals"]["passed"]:
        expected |= {"team_total", "game_total"}
    if stored["stats"]["win"]["passed"]:
        expected.add("game_moneyline")
    assert markets == expected and expected
