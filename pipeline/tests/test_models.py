"""Phase 3 model tests: distributions, leakage, the walk-forward gate, live projections, and
Quick Entry recalculation. All data here is synthetic (tests/synth.py)."""

from __future__ import annotations

import itertools
import json
import math
import sqlite3
from datetime import UTC, date, datetime, timedelta

import numpy as np
import pytest
import synth

from rinkx.models import dist, fit, history, project
from rinkx.models.state import State, walk
from rinkx.quick_entry import Issue, finish, parse_form, run_quick_entry

START = date(2025, 10, 7)
DAYS = 140
NOW = datetime(2026, 2, 24, 15, 0, tzinfo=UTC)  # START + DAYS, before the upcoming game


@pytest.fixture(scope="module")
def league() -> tuple[sqlite3.Connection, synth.Truth]:
    return synth.build(days=DAYS, seed=3)


@pytest.fixture(scope="module")
def report(league: tuple[sqlite3.Connection, synth.Truth]) -> dict:
    conn, _ = league
    return fit.evaluate(fit.collect(walk(history.load(conn))))


# ---- distributions ----------------------------------------------------------------------


def test_pmf_vectors_sum_to_one_and_match_moments():
    p = dist.pmf_vector(2.4, 6.0)
    s = dist.summary(p)
    assert p.sum() == pytest.approx(1, abs=1e-4)
    assert s["mean"] == pytest.approx(2.4, abs=0.01)
    assert s["sd"] ** 2 == pytest.approx(2.4 + 2.4**2 / 6, rel=0.02)  # NB variance
    pois = dist.summary(dist.pmf_vector(2.4, math.inf))
    assert pois["sd"] ** 2 == pytest.approx(2.4, rel=0.01)
    ge = dist.p_ge(p)
    assert ge[0] == pytest.approx(1, abs=1e-4) and all(a >= b for a, b in itertools.pairwise(ge))


def test_goalie_mixture_matches_simulation():
    rng = np.random.default_rng(0)
    sa = rng.negative_binomial(30, 30 / (30 + 29.0), 200_000)
    saves = rng.binomial(sa, 0.905)
    p = dist.mixture_vector(29.0, 30.0, 0.905)
    assert dist.summary(p)["mean"] == pytest.approx(saves.mean(), abs=0.05)
    assert sum(p[25:]) == pytest.approx((saves >= 25).mean(), abs=0.005)


def test_randomized_pit_is_uniform_when_calibrated():
    rng = np.random.default_rng(1)
    mu = rng.gamma(2.0, 1.0, 50_000)
    y = rng.poisson(mu).astype(float)
    u = dist.randomized_pit(dist.cdf(y, mu, math.inf), np.exp(dist.logpmf(y, mu, math.inf)))
    assert max(abs(h - 0.1) for h in dist.pit_histogram(u)) < 0.01


# ---- leakage ----------------------------------------------------------------------------


def _features_by_key(rows) -> dict:
    return {(r.kind, r.game_id, r.player): r.features for r in rows}


def test_features_never_see_the_future(league):
    conn, _ = league
    full = _features_by_key(walk(history.load(conn)))
    cutoff = (START + timedelta(days=60)).isoformat()
    early = _features_by_key(walk(history.load(conn, before=cutoff)))
    assert early and len(early) < len(full)
    for key, feats in early.items():
        assert full[key] == feats  # later games change nothing that was predicted before them


def test_a_games_own_result_does_not_change_its_features(tmp_path):
    conn, _ = synth.build(str(tmp_path / "a.db"), days=40, seed=5)
    target = conn.execute(
        "SELECT id, game_date FROM games ORDER BY game_date DESC, id DESC LIMIT 1 OFFSET 3"
    ).fetchone()
    before = _features_by_key(walk(history.load(conn)))
    conn.execute("UPDATE player_game_stats SET shots = shots + 9, hits = hits + 5 WHERE game_id = ?", (target[0],))
    conn.execute(
        "UPDATE goalie_game_stats SET saves = saves + 9, shots_against = shots_against + 9 WHERE game_id = ?",
        (target[0],),
    )
    after = _features_by_key(walk(history.load(conn)))
    rows = [k for k in before if k[1] == target[0]]
    assert rows and all(before[k] == after[k] for k in rows)
    later = [
        k for k in before if conn.execute("SELECT game_date FROM games WHERE id = ?", (k[1],)).fetchone()[0] > target[1]
    ]
    assert any(before[k] != after[k] for k in later)  # ...but games after it do learn from it


# ---- learning and the gate --------------------------------------------------------------


def test_shrunk_rates_recover_true_talent(league):
    conn, truth = league
    st = State()
    for _ in walk(history.load(conn), st, emit=False):
        pass
    est, true = [], []
    for pid, rate in truth.shot_rate.items():
        s = st.skaters[pid]
        est.append((s.x["shots"][2] + 2.0 * st.prior_rate("F", "shots")) / (s.b["shots"][2] + 2.0))
        true.append(rate)
    assert np.corrcoef(est, true)[0, 1] > 0.85


def test_walk_forward_gate(report):
    assert report["status"] == "ok"
    assert report["test"]["from"] > report["tune"]["from"]
    stats = report["stats"]
    for stat in ("shots", "hits", "blocks", "saves"):
        e = stats[stat]
        assert e["passed"], (stat, e.get("reason"))
        for b in ("season", "l10"):
            assert e["baselines"][b]["model_minus_baseline"]["lo"] > 0
        assert e["pit_max_dev"] <= e["pit_tolerance"]
        assert e["mean_pred"] == pytest.approx(e["mean_actual"], rel=0.06)
    # Every stat is reported either way; failures say why and are never projected.
    for e in stats.values():
        assert e["passed"] or e["reason"] in ("did_not_beat_baselines", "miscalibrated", "insufficient_history")
    assert "venue" in report["choices"]["hits"]["factors"]  # the synthetic arenas really are biased


def test_too_little_history_publishes_nothing(tmp_path):
    conn, _ = synth.build(str(tmp_path / "s.db"), days=15, seed=2)
    r = fit.evaluate(fit.collect(walk(history.load(conn))))
    assert r["status"] == "insufficient_history"
    assert not any(e["passed"] for e in r["stats"].values())


# ---- live projections and Quick Entry ---------------------------------------------------


def _add_upcoming(conn: sqlite3.Connection) -> int:
    return synth.add_upcoming(conn, START + timedelta(days=DAYS))


class FakeTracker:
    def __init__(self, issues: list[Issue]) -> None:
        self.issues = issues
        self.comments: list[tuple[int, str]] = []
        self.closed: list[tuple[int, bool]] = []

    def open_issues(self) -> list[Issue]:
        return [i for i in self.issues if i.number not in {n for n, _ in self.closed}]

    def comment(self, number: int, body: str) -> None:
        self.comments.append((number, body))

    def close(self, number: int, completed: bool) -> None:
        self.closed.append((number, completed))


def _goalie_issue(number: int, goalie_nhl_id: int, team: str, author: str = "owner", source: str = "https://x.test/a"):
    body = (
        f"### Game\n\n2025029999\n\n### Team\n\n{team}\n\n### Goalie\n\n{goalie_nhl_id}\n\n"
        f"### Status\n\nConfirmed\n\n### Source URL\n\n{source}\n"
    )
    return Issue(number, "Quick Entry: goalie", body, author, "2026-02-24T14:00:00Z")


def test_live_projections_explain_and_goalie_change(tmp_path):
    conn, truth = synth.build(str(tmp_path / "live.db"), days=DAYS, seed=3)
    game_id = _add_upcoming(conn)
    today = START + timedelta(days=DAYS)
    project.run_models(conn, NOW, today)
    stored, _ = project.latest_report(conn)
    assert stored is not None
    passed = {s for s, e in stored["stats"].items() if e.get("passed")}
    rows = conn.execute(
        "SELECT m.code, p.player_id, p.mean, p.pmf, p.factors_for, p.factors_against, p.inputs, p.trigger_reason "
        "FROM player_projections p JOIN markets m ON m.id = p.market_id WHERE p.game_id = ? AND p.is_current = 1",
        (game_id,),
    ).fetchall()
    assert rows
    assert {fit.MARKETS[r[0]] for r in rows} == passed  # only tested-and-passed stats are projected
    for code, _, mean, pmf_text, f_for, f_against, inputs, reason in rows:
        assert reason == "scheduled"
        p = json.loads(pmf_text)["p"]
        assert sum(p) == pytest.approx(1, abs=1e-3)
        assert sum(k * x for k, x in enumerate(p)) == pytest.approx(mean, rel=0.01, abs=1e-3)
        ins = json.loads(inputs)
        if fit.MARKETS[code] not in ("saves", "goals_against"):
            # Explain is exact: reference x product of factor effects = projected mean.
            prod = math.prod(1 + f["effect"] for f in json.loads(f_for) + json.loads(f_against))
            assert ins["reference_mean"] * prod == pytest.approx(mean, rel=0.02)

    # A re-run with nothing new writes nothing new.
    n_before = conn.execute("SELECT count(*) FROM player_projections").fetchone()[0]
    project.run_models(conn, NOW + timedelta(minutes=5), today)
    assert conn.execute("SELECT count(*) FROM player_projections").fetchone()[0] == n_before

    # Confirm the *backup* goalie for the away team through Quick Entry.
    away = conn.execute("SELECT away_team_id FROM games WHERE id = ?", (game_id,)).fetchone()[0]
    backup = truth.goalies[away][1]
    nhl_id, team = conn.execute(
        "SELECT p.nhl_player_id, t.abbrev FROM players p JOIN teams t ON t.id = p.current_team_id WHERE p.id = ?",
        (backup,),
    ).fetchone()
    tracker = FakeTracker([_goalie_issue(7, nhl_id, team), _goalie_issue(8, nhl_id, team, author="someone-else")])
    later = NOW + timedelta(hours=1)
    qe = run_quick_entry(conn, tracker, "owner", later)
    assert [o.applied for o in qe.outcomes] == [True]  # the stranger's issue is ignored entirely
    project.run_models(conn, later, today, qe.reasons)
    changed = conn.execute(
        "SELECT p.player_id, m.code, p.mean, old.mean FROM player_projections p JOIN player_projections old "
        "ON old.id = p.supersedes JOIN markets m ON m.id = p.market_id WHERE p.game_id = ? AND p.is_current = 1 "
        "AND p.trigger_reason = 'goalie_confirmed'",
        (game_id,),
    ).fetchall()
    assert changed, "goalie confirmation should produce before/after projections"
    home_scorers = [c for c in changed if c[1] in ("skater_goals", "skater_points", "skater_assists")]
    if "goals" in passed or "points" in passed or "assists" in passed:
        assert home_scorers and all(c[2] != c[3] for c in home_scorers)
    goalie_rows = conn.execute(
        "SELECT p.inputs FROM player_projections p JOIN markets m ON m.id = p.market_id WHERE p.game_id = ? "
        "AND p.player_id = ? AND p.is_current = 1 AND m.code = 'goalie_saves'",
        (game_id, backup),
    ).fetchall()
    if "saves" in passed:
        assert goalie_rows and json.loads(goalie_rows[0][0])["start_status"] == "confirmed"

    # After the store is saved: the issue gets a comment with no projection values, and is closed.
    for action in finish(conn, tracker, qe, "2026-02-24T15:00:00Z"):
        action()
    assert tracker.closed == [(7, True)]
    body = tracker.comments[0][1]
    assert "Applied:" in body and "recalculated" in body
    assert not any(f"{c[2]:.2f}" in body for c in changed)

    # The same issue is never applied twice.
    qe2 = run_quick_entry(conn, FakeTracker([_goalie_issue(7, nhl_id, team)]), "owner", later)
    assert qe2.outcomes == [] and qe2.retry_close == [(7, True)]


def test_quick_entry_rejections(tmp_path):
    conn, truth = synth.build(str(tmp_path / "qe.db"), days=20, seed=4)
    _add_upcoming(conn)
    gk = next(iter(truth.sv))
    nhl_id = conn.execute("SELECT nhl_player_id FROM players WHERE id = ?", (gk,)).fetchone()[0]
    past = conn.execute("SELECT nhl_game_id FROM games WHERE status = 'final' LIMIT 1").fetchone()[0]
    issues = [
        _goalie_issue(1, nhl_id, "T00", source="not a url"),
        Issue(
            2,
            "Quick Entry: goalie",
            _goalie_issue(0, nhl_id, "T00").body.replace("2025029999", str(past)),
            "owner",
            "2026-02-24T14:00:00Z",
        ),
        _goalie_issue(3, 123, "T00"),
        Issue(
            4,
            "Quick Entry: player out",
            "### Game\n\n2025029999\n\n### Player\n\nNobody Here\n\n### Source URL\n\nhttps://x.test\n",
            "owner",
            "2026-02-24T14:00:00Z",
        ),
    ]
    qe = run_quick_entry(conn, FakeTracker(issues), "OWNER", NOW)
    assert [o.applied for o in qe.outcomes] == [False, False, False, False]
    msgs = [o.message for o in qe.outcomes]
    assert "source URL" in msgs[0]
    assert "already started" in msgs[1]
    assert "No goalie" in msgs[2]
    assert "Could not identify" in msgs[3]
    assert conn.execute("SELECT count(*) FROM goalie_starts").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM quick_entries WHERE status = 'rejected'").fetchone()[0] == 4


def test_player_out_removes_projection(tmp_path):
    conn, _ = synth.build(str(tmp_path / "out.db"), days=DAYS, seed=3)
    game_id = _add_upcoming(conn)
    today = START + timedelta(days=DAYS)
    project.run_models(conn, NOW, today)
    pid, nhl_id = conn.execute(
        "SELECT p.player_id, pl.nhl_player_id FROM player_projections p JOIN players pl ON pl.id = p.player_id "
        "WHERE p.game_id = ? AND p.is_current = 1 AND pl.position <> 'G' LIMIT 1",
        (game_id,),
    ).fetchone()
    issue = Issue(
        9,
        "Quick Entry: player out",
        f"### Game\n\n2025029999\n\n### Player\n\n{nhl_id}\n\n### Status\n\n"
        "Out\n\n### Reason\n\nInjury\n\n### Source URL\n\nhttps://x.test/out\n",
        "owner",
        "2026-02-24T14:30:00Z",
    )
    qe = run_quick_entry(conn, FakeTracker([issue]), "owner", NOW)
    assert qe.outcomes[0].applied, qe.outcomes[0].message
    project.run_models(conn, NOW + timedelta(hours=1), today, qe.reasons)
    assert (
        conn.execute(
            "SELECT count(*) FROM player_projections WHERE game_id = ? AND player_id = ? AND is_current = 1",
            (game_id, pid),
        ).fetchone()[0]
        == 0
    )
    assert project.players_out(conn, game_id)[pid]["reason"] == "injury"


def test_parse_issue_form():
    body = "### Game\n\n2025020001\n\n### Team\n\n_No response_\n\n### Source URL\n\nhttps://a.b/c\n"
    assert parse_form(body) == {"game": "2025020001", "team": "", "source url": "https://a.b/c"}
