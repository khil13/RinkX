"""Phase 8: empirical correlations (closed form == brute force, no leakage, n_obs/CI) and the
parlay copula (accuracy, independence, PD repair, shared vectors with the browser)."""

from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest
import synth
from scipy.stats import multivariate_normal, norm

from rinkx.config import REPO_ROOT
from rinkx.correlation import estimate as est
from rinkx.correlation import parlay
from rinkx.models.history import GameRecord, GoalieLine, SkaterLine

STATS = ("shots", "goals", "assists", "points", "pp_points", "blocks", "hits")


def _league(n_games: int = 30, seed: int = 3) -> list[GameRecord]:
    rng = np.random.default_rng(seed)
    games = []
    for g in range(n_games):
        rec = GameRecord(g, 9000 + g, f"2025-10-{1 + g // 2:02d}", f"2025-10-01T{g:02d}:00:00Z", 2025, 1, 2, False)
        rec.home_goals, rec.away_goals, rec.ended_in = 3, 2, "REG"
        for team, opp in ((1, 2), (2, 1)):
            for p in range(4):
                pid = team * 10 + p
                pace = rng.gamma(4, 0.25)  # shared within the player-game: makes his stats co-move
                stats = {s: float(rng.poisson(1.5 * pace)) for s in STATS}
                stats["points"] = stats["goals"] + stats["assists"]
                rec.skaters.append(SkaterLine(pid, team, opp, team == 1, "F", 900.0, 60.0, stats))
            rec.goalies.append(
                GoalieLine(
                    100 + team, team, opp, team == 1, True, 3600.0, 30.0, float(rng.poisson(27)), float(rng.poisson(3))
                )
            )
        games.append(rec)
    return games


def _brute(games: list[GameRecord]) -> dict[tuple[str, str, str], tuple[float, int]]:
    """The same estimate, written the slow and obvious way."""
    prior: dict[int, list[dict[str, float]]] = defaultdict(list)
    gprior: dict[int, list[tuple[float, float]]] = defaultdict(list)
    pairs: dict[tuple[str, str, str], list[tuple[float, float]]] = defaultdict(list)
    names = list(est.SKATER.items())
    for g in games:
        res = {}
        for s in g.skaters:
            hist = prior[s.player]
            if len(hist) >= est.MIN_PRIOR:
                res[s.player] = (
                    s.team,
                    {
                        m: (s.stats[st] - np.mean([h[st] for h in hist]))
                        / math.sqrt(max(np.mean([h[st] for h in hist]), est.FLOOR))
                        for m, st in names
                    },
                )
        gres = {}
        for gl in g.goalies:
            h = gprior[gl.player]
            if len(h) >= est.MIN_PRIOR:
                ms, mg = np.mean([x[0] for x in h]), np.mean([x[1] for x in h])
                gres[gl.team] = {
                    "goalie_saves": (gl.saves - ms) / math.sqrt(ms),
                    "goalie_goals_against": (gl.ga - mg) / math.sqrt(max(mg, est.FLOOR)),
                }
        mk = list(est.SKATER)
        for pi, (ti, ri) in res.items():
            for ai, a in enumerate(mk):
                for b in mk[ai + 1 :]:
                    pairs[(a, b, "same_player")].append((ri[a], ri[b]))
            for pj, (tj, rj) in res.items():
                if pi == pj:
                    continue
                rel = "same_team" if ti == tj else "opponent"
                for ai, a in enumerate(mk):
                    for b in mk[ai:]:
                        pairs[(a, b, rel)].append((ri[a], rj[b]))
            opp = g.away_team if ti == g.home_team else g.home_team
            if opp in gres:
                for a in mk:
                    for gm, gv in gres[opp].items():
                        pairs[(a, gm, "opp_goalie")].append((ri[a], gv))
        for s in g.skaters:
            prior[s.player].append(s.stats)
        for gl in g.goalies:
            gprior[gl.player].append((gl.saves, gl.ga))
    out = {}
    for k, v in pairs.items():
        x, y = np.array(v).T
        out[k] = (float(np.corrcoef(x, y)[0, 1]), len(v))
    return out


def test_closed_form_matches_brute_force():
    games = _league()
    fast = {(e.a, e.b, e.relation): e for e in est.estimate(games)}
    slow = _brute(games)
    assert set(fast) == set(slow)
    for k, (rho, _) in slow.items():
        assert fast[k].rho == pytest.approx(rho, abs=1e-3), k
        assert fast[k].lo <= fast[k].rho <= fast[k].hi
    # Units are independent observations, not pairs: player-games / team-games.
    eligible_player_games = sum(1 for g in games[est.MIN_PRIOR :] for _ in g.skaters)
    assert fast[("skater_shots_on_goal", "skater_points", "same_player")].n == eligible_player_games
    assert fast[("skater_shots_on_goal", "skater_hits", "same_team")].n == 2 * (len(games) - est.MIN_PRIOR)
    # The shared pace makes a player's own stats co-move; teammates were drawn independently.
    assert fast[("skater_shots_on_goal", "skater_hits", "same_player")].rho > 0.2


def test_no_leakage_from_the_game_itself():
    games = _league()
    base = {(e.a, e.b, e.relation): e.rho for e in est.estimate(games)}
    # Changing a LATER game's stats can't change residuals of earlier games; changing the last game
    # changes only what that game contributes. So estimates from the first 20 games are unchanged
    # when game 25 is altered.
    early = {(e.a, e.b, e.relation): e.rho for e in est.estimate(games[:20])}
    altered = list(games)
    altered[25].skaters[0].stats["shots"] = 99.0
    early2 = {(e.a, e.b, e.relation): e.rho for e in est.estimate(altered[:20])}
    assert early == early2 and base


def test_run_correlations_on_synthetic_league(tmp_path):
    conn, _ = synth.build(str(tmp_path / "c.db"), teams=8, days=60, seed=5)
    now = datetime(2026, 1, 1, tzinfo=UTC)
    n = est.run_correlations(conn, now)
    assert n == conn.execute("SELECT count(*) FROM prop_correlations").fetchone()[0] > 50
    assert est.run_correlations(conn, now + timedelta(hours=1)) == 0  # fresh: not recomputed
    pub = est.published(conn, now)
    rels = {p["relation"] for p in pub["pairs"]}
    assert rels == {"same_player", "same_team", "opponent", "opp_goalie"}
    sp = next(
        p
        for p in pub["pairs"]
        if (p["a"], p["b"], p["relation"]) == ("skater_shots_on_goal", "skater_goals", "same_player")
    )
    assert sp["rho"] > 0 and sp["ci"][0] < sp["rho"] < sp["ci"][1] and sp["n"] > 1000
    og = next(
        p
        for p in pub["pairs"]
        if (p["a"], p["b"], p["relation"]) == ("skater_shots_on_goal", "goalie_saves", "opp_goalie")
    )
    assert og["rho"] > 0  # more shots by a skater, more saves by the goalie facing him
    assert pub["min_n"] == est.MIN_N and pub["window"]["from"] < pub["window"]["to"]


# ---- copula -----------------------------------------------------------------------------------


def test_normal_functions():
    for x in np.linspace(-6, 6, 121):
        assert parlay.erfc(x) == pytest.approx(math.erfc(x), abs=2e-7)
    for p in np.linspace(0.001, 0.999, 200):
        assert parlay.ppf(p) == pytest.approx(norm.ppf(p), abs=1e-8)


@pytest.mark.parametrize("probs, corr", parlay.VECTOR_CASES[2:7] + parlay.VECTOR_CASES[8:])
def test_combine_matches_scipy(probs, corr):
    p, shrink = parlay.combine(probs, corr)
    assert shrink == 1.0
    want = multivariate_normal(mean=[0] * len(probs), cov=corr).cdf([norm.ppf(x) for x in probs])
    assert p == pytest.approx(want, abs=1e-3)


def test_independent_legs_are_exact_and_correlation_moves_the_right_way():
    assert parlay.combine([0.55, 0.6, 0.5], [[1, 0, 0], [0, 1, 0], [0, 0, 1]]) == (0.55 * 0.6 * 0.5, 1.0)
    indep = 0.5 * 0.5
    assert parlay.combine([0.5, 0.5], [[1, 0.5], [0.5, 1]])[0] > indep
    assert parlay.combine([0.5, 0.5], [[1, -0.5], [-0.5, 1]])[0] < indep


def test_non_positive_definite_is_shrunk_toward_independence():
    p, shrink = parlay.combine([0.6, 0.6, 0.6], [[1, 0.9, -0.9], [0.9, 1, 0.9], [-0.9, 0.9, 1]])
    assert shrink < 1.0 and 0 < p < 0.6


def test_shared_vectors_are_current():
    """fixtures/parlay_vectors.json is what the browser is tested against; regenerate with
    `python -m rinkx.correlation.parlay` if this fails."""
    stored = json.loads((REPO_ROOT / "fixtures/parlay_vectors.json").read_text())
    assert stored == json.loads(json.dumps(parlay.vectors()))
