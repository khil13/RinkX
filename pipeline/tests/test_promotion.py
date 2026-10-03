"""Model versions 1.4 / 1.5, champion and challenger, the shadow pricing of every line, and
promotion or rejection on graded props (rinkx.models.promotion)."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta

import pytest
import synth

from rinkx.grading import grade
from rinkx.ingestion.odds.store import upsert_line
from rinkx.models import fit, history, project, promotion
from rinkx.models.state import State, walk
from rinkx.pricing.price import PricingConfig, run_pricing
from rinkx.publish.projections import models_report

CFG = PricingConfig(min_edge=0.03, dq_floor=0.6, implausible_edge=0.15)


@pytest.fixture(scope="module")
def deployed():
    """A league where lines change every 10 days and back-to-backs cost shots."""
    conn, truth = synth.build(teams=20, days=200, seed=22, lines=True, line_changes=True, back_to_backs=True)
    return conn, truth


def test_back_to_back_and_line_inputs_are_point_in_time(deployed):
    conn, _ = deployed
    games = history.load(conn)
    assert all(g.has_lines for g in games)
    st = State()
    rows = [r for r in walk(games, st) if r.kind == "skater"]
    # A team that played yesterday: its skaters' shot factor is below 1, learned from earlier games only.
    assert any(r.features["rest.shots"] < 0.97 for r in rows[2000:]), "back-to-backs should lower expected shots"
    x_b, n_b, x_r, n_r = st.league.b2b["shots"]
    assert (x_b / n_b) / (x_r / n_r) == pytest.approx(synth.B2B_SHOTS, abs=0.04)  # per hour, vs rested nights
    # Deployment comes from the player's previous game: the first game of a player has none.
    first = {}
    for r in rows:
        first.setdefault(r.player, r)
    assert all(r.features["slot.has"] == 0 for r in first.values())
    assert sum(r.features["slot.has"] for r in rows) > 0.9 * len(rows)


def test_version_15_adds_inputs_and_wins_the_walk_forward_test_here(deployed):
    conn, _ = deployed
    tables = fit.collect(walk(history.load(conn)))
    base, new = fit.evaluate(tables, "1.4"), fit.evaluate(tables, "1.5")
    for s, c in base["choices"].items():
        assert c.get("slot_w", 0) == 0 and "rest" not in c["factors"] and "mates" not in c["factors"], s
    shots = new["choices"]["shots"]
    assert "rest" in shots["factors"] and shots["slot_w"] > 0
    assert "rest" in new["choices"]["saves"]["factors"]
    for s in ("shots", "saves"):
        assert new["stats"][s]["log_score"] > base["stats"][s]["log_score"]


def test_champion_publishes_and_challenger_is_priced_in_the_shadow(deployed, tmp_path):
    conn, _ = deployed
    today = date(2025, 10, 7) + timedelta(days=200)
    now = datetime(today.year, today.month, today.day, 15, tzinfo=UTC)
    gid = synth.add_upcoming(conn, today)
    project.run_models(conn, now, today)

    status = {
        (f, v): s for f, v, s in conn.execute("SELECT model_family, version, status FROM model_versions").fetchall()
    }
    assert status[("skater_shots", "1.4")] == "champion"  # the first version to pass takes an empty family
    assert status[("skater_shots", "1.5")] == "challenger"
    assert project.champions(conn)["skater_shots"] == "1.4"
    # Published projections are the champion's; the challenger's sit beside them, unpublished.
    versions = {
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT v.version FROM player_projections p JOIN model_versions v ON v.id = p.model_version_id "
            "WHERE p.game_id = ? AND p.is_current = 1",
            (gid,),
        )
    }
    assert versions == {"1.4"}
    sog = conn.execute("SELECT id FROM markets WHERE code = 'skater_shots_on_goal'").fetchone()[0]
    pid, champ_mean = conn.execute(
        "SELECT player_id, mean FROM player_projections WHERE game_id = ? AND market_id = ? AND is_current = 1 "
        "ORDER BY mean DESC LIMIT 1",
        (gid, sog),
    ).fetchone()
    chal = conn.execute(
        "SELECT c.mean, v.version FROM challenger_projections c JOIN model_versions v ON v.id = c.model_version_id "
        "WHERE c.game_id = ? AND c.player_id = ? AND c.market_id = ?",
        (gid, pid, sog),
    ).fetchone()
    assert chal is not None and chal[1] == "1.5" and chal[0] != pytest.approx(champ_mean, abs=1e-6)
    # Explain carries his line and whether either team is on a back-to-back.
    inputs = json.loads(
        conn.execute(
            "SELECT inputs FROM player_projections WHERE game_id = ? AND player_id = ? AND market_id = ? "
            "AND is_current = 1",
            (gid, pid, sog),
        ).fetchone()[0]
    )
    assert inputs["line"].startswith("F") or inputs["line"].startswith("D")
    assert "back_to_back" in inputs

    # Every line priced also freezes the challenger's probability for that same line.
    src = conn.execute("SELECT id FROM data_sources WHERE code = 'synthetic'").fetchone()[0]
    conn.execute("INSERT INTO sportsbooks (code, name) VALUES ('fanduel', 'FanDuel')")
    book = conn.execute("SELECT id FROM sportsbooks WHERE code = 'fanduel'").fetchone()[0]
    upsert_line(
        conn, game_id=gid, market_id=sog, book_id=book, player_id=pid, team_id=None, line=2.5,
        over=-115, under=-105, at="2026-04-25T14:00:00Z", source_id=src, source_ref="test",
    )  # fmt: skip
    assert run_pricing(conn, now, CFG) == 1
    sh = conn.execute(
        "SELECT s.*, p.p_model_over FROM shadow_predictions s JOIN predictions p ON p.id = s.prediction_id"
    ).fetchall()
    assert len(sh) == 1 and sh[0]["p_champion"] == pytest.approx(sh[0]["p_model_over"], abs=1e-6)
    assert sh[0]["p_challenger"] != pytest.approx(sh[0]["p_champion"], abs=1e-6)

    rep = models_report(conn)
    assert rep["versions"]["skater_shots"] == "1.4"
    assert rep["challengers"]["skater_shots"]["version"] == "1.5"
    fam = next(f for f in rep["promotion"]["families"] if f["family"] == "skater_shots")
    assert fam == {"family": "skater_shots", "champion": "1.4", "challenger": "1.5", "live": None}


class Tracker:
    def __init__(self, issues):
        self.issues = issues

    def open_issues(self):
        return self.issues

    def comment(self, number, body):
        pass

    def close(self, number, completed):
        pass


def test_quick_entry_line_change_moves_a_player_up(deployed):
    """A bottom-six forward promoted to the first line and PP1: the challenger, which learned that
    a line slot's ice time predicts his, raises his shots; Explain shows the new line."""
    from rinkx.quick_entry import Issue, run_quick_entry

    conn, _ = deployed
    today = date(2025, 10, 7) + timedelta(days=200)
    now = datetime(today.year, today.month, today.day, 16, tzinfo=UTC)
    gid = conn.execute("SELECT id FROM games WHERE nhl_game_id = ?", (synth.UPCOMING_NHL_ID,)).fetchone()[0]
    home = conn.execute("SELECT home_team_id FROM games WHERE id = ?", (gid,)).fetchone()[0]
    sog = conn.execute("SELECT id FROM markets WHERE code = 'skater_shots_on_goal'").fetchone()[0]
    pid, nhl_id, inputs = conn.execute(
        "SELECT p.player_id, pl.nhl_player_id, p.inputs FROM player_projections p "
        "JOIN players pl ON pl.id = p.player_id WHERE p.game_id = ? AND p.market_id = ? AND p.is_current = 1 "
        "AND pl.current_team_id = ? AND json_extract(p.inputs, '$.line') = 'F4' LIMIT 1",
        (gid, sog, home),
    ).fetchone()
    assert json.loads(inputs).get("pp_unit") is None  # bottom six: no PP unit

    def chal_mean() -> float:
        return conn.execute(
            "SELECT mean FROM challenger_projections WHERE game_id = ? AND player_id = ? AND market_id = ?",
            (gid, pid, sog),
        ).fetchone()[0]

    before = chal_mean()
    body = (
        f"### Game\n\n{synth.UPCOMING_NHL_ID}\n\n### Player\n\n{nhl_id}\n\n### Line\n\nF1 (first line)\n\n"
        "### Linemates\n\n_No response_\n\n### Power play\n\nPP1\n\n### Source URL\n\nhttps://x.test/lines\n"
    )
    qe = run_quick_entry(
        conn, Tracker([Issue(31, "Quick Entry: line", body, "owner", "2026-04-25T15:30:00Z")]), "owner", now
    )
    assert qe.outcomes[0].applied, qe.outcomes[0].message
    assert "to F1" in qe.outcomes[0].message and "on PP1" in qe.outcomes[0].message
    project.run_models(conn, now, today, qe.reasons)
    row = conn.execute(
        "SELECT inputs, trigger_reason FROM player_projections WHERE game_id = ? AND player_id = ? AND market_id = ? "
        "AND is_current = 1",
        (gid, pid, sog),
    ).fetchone()
    ins = json.loads(row[0])
    assert (ins["line"], ins["pp_unit"], row[1]) == ("F1", 1, "lineup_change")
    assert chal_mean() > before * 1.05

    # Rejections: a line change needs a line or a PP change; linemates need a line.
    bad = body.replace("F1 (first line)", "Unchanged").replace("PP1", "Unchanged")
    qe = run_quick_entry(
        conn, Tracker([Issue(32, "Quick Entry: line", bad, "owner", "2026-04-25T15:40:00Z")]), "owner", now
    )
    assert not qe.outcomes[0].applied and "Give a new line" in qe.outcomes[0].message


def test_deployment_tracker_and_alerts_after_a_line_change(deployed, tmp_path):
    """After the Quick Entry above (F4 -> F1 and PP1): the tracker shows the change against his last
    game, a deployment alert fires once for the game, and profiles are filled from stored data."""
    from rinkx.alerts.evaluate import evaluate as eval_alerts
    from rinkx.alerts.evaluate import load_specs, sync
    from rinkx.models import deployment
    from rinkx.publish import profile

    conn, _ = deployed
    today = date(2025, 10, 7) + timedelta(days=200)
    now = datetime(today.year, today.month, today.day, 17, tzinfo=UTC)
    g = conn.execute("SELECT * FROM games WHERE nhl_game_id = ?", (synth.UPCOMING_NHL_ID,)).fetchone()
    rows = deployment.game_deployment(conn, g)
    moved = [r for r in rows if r["status"] == "quick_entry"]
    assert len(moved) == 1
    m = moved[0]
    assert (m["line"], m["pp_unit"]) == ("F1", 1) and m["previous"]["line"] == "F4"
    assert {"pp1_promotion", "top_line_promotion"} <= set(m["changes"]) and m["source"] == "https://x.test/lines"
    # Players without a Quick Entry: their last game, compared with the one before.
    assert all(r["status"] == "last_game" and r["line"] for r in rows if r is not m)
    pub = deployment.published(conn, now, today.isoformat())
    assert pub["games"][0]["players"] and "player_pk" not in pub["games"][0]["players"][0]

    cfg = tmp_path / "alerts.yml"
    cfg.write_text("alerts:\n  - key: dep\n    type: deployment\n    changes: [pp1_promotion]\n")
    specs, errors = load_specs(cfg)
    assert errors == [] and sync(conn, specs) == []
    assert eval_alerts(conn, now, None) == 1
    payload = json.loads(conn.execute("SELECT payload FROM alert_events ORDER BY id DESC LIMIT 1").fetchone()[0])
    assert payload["message"].startswith("🔥 MOVED TO PP1: ") and "(Quick Entry)" in payload["message"]
    assert eval_alerts(conn, now, None) == 0  # once per game
    bad = tmp_path / "bad.yml"
    bad.write_text("alerts:\n  - key: x\n    type: deployment\n    changes: [moon]\n")
    assert "changes must be a list" in load_specs(bad)[1][0]

    prof = profile.skater_profile(conn, m["player_pk"], 20252026, today.isoformat())
    assert prof["shooting"]["games"] > 50 and prof["shooting"]["sog_per_60"] > 0
    assert 0 < prof["shooting"]["shot_share"] < 0.3 and len(prof["usage"]["recent"]) == 5
    assert prof["matchup"]["opp_sog_allowed"] > 0 and prof["matchup"]["position_group"] == "forwards"
    gk = conn.execute("SELECT player_id FROM goalie_game_stats WHERE started = 1 LIMIT 1").fetchone()[0]
    imp = profile.goalie_impact(conn, gk, today.isoformat(), 20252026)
    assert imp["starts"] > 10 and 0.85 < imp["save_pct"] < 0.95 and imp["advanced"] is None


def _graded_league(better: str):
    """Graded shots props with a shadow row each. One version is the season-average model; the other
    is deliberately sharper (moved 30% of the way toward the outcome: test data only, to make
    "better" unambiguous). better='challenger' or 'champion' says which is which."""
    conn, truth = synth.build(teams=8, days=90, seed=4)
    n = synth.add_priced_history(conn, truth, days=30)
    last = conn.execute("SELECT max(game_date) FROM games").fetchone()[0]
    today = date.fromisoformat(last) + timedelta(days=1)
    now = datetime(today.year, today.month, today.day, 12, tzinfo=UTC)
    grade.run_grading(conn, now, today)
    ids = {}
    for v, s in (("1.4", "champion"), ("1.5", "challenger")):
        ids[v] = conn.execute(
            "INSERT INTO model_versions (model_family, version, algorithm, feature_list, status) "
            "VALUES ('skater_shots', ?, 'test', '[]', ?)",
            (v, s),
        ).lastrowid
    market = conn.execute("SELECT id FROM markets WHERE code = 'skater_shots_on_goal'").fetchone()[0]
    conn.execute(
        "INSERT INTO calibrators (market_id, fitted_at, n_fit, n_holdout, applied, reason, knots) "
        "VALUES (?, '2026-01-01T00:00:00Z', 500, 150, 1, 'applied', '{\"x\":[0,1],\"y\":[0,1]}')",
        (market,),
    )
    for pid, p_model, result in conn.execute(
        "SELECT p.id, p.p_model_over, r.result FROM predictions p JOIN model_results r ON r.prediction_id = p.id"
    ).fetchall():
        poor = float(p_model)
        good = 0.7 * poor + 0.3 * (result == "over")
        champ, chal = (poor, good) if better == "challenger" else (good, poor)
        conn.execute(
            "INSERT INTO shadow_predictions VALUES (?, ?, ?, ?, ?, '2026-01-01T00:00:00Z')",
            (pid, ids["1.5"], ids["1.4"], champ, chal),
        )
    return conn, now, n


def test_a_challenger_that_beats_the_champion_live_is_promoted():
    conn, now, _ = _graded_league("challenger")
    c = promotion.compare(conn)[("skater_shots", "1.4", "1.5")]
    # Two books price each prop: they count once.
    assert c["n"] == conn.execute("SELECT count(DISTINCT game_id || '-' || player_id) FROM predictions").fetchone()[0]
    assert c["n"] >= promotion.MIN_PROPS and c["lo"] > 0
    made = promotion.run_promotion(conn, now)
    assert [(d["family"], d["decision"]) for d in made] == [("skater_shots", "promoted")]
    status = dict(conn.execute("SELECT version, status FROM model_versions WHERE model_family = 'skater_shots'"))
    assert status["1.5"] == "champion" and status["1.4"] == "retired"
    # The shots calibrator was fit to the old champion: it is retired with it.
    assert conn.execute("SELECT count(*) FROM calibrators WHERE is_current = 1").fetchone()[0] == 0
    row = conn.execute("SELECT * FROM model_promotions").fetchone()
    assert (row["champion"], row["challenger"], row["decision"]) == ("1.4", "1.5", "promoted")
    assert promotion.run_promotion(conn, now) == []  # decided once
    hist = promotion.published(conn)["history"]
    assert hist[0]["decision"] == "promoted" and hist[0]["n_props"] == c["n"]


def test_a_challenger_that_loses_live_is_rejected_and_the_champion_stays():
    conn, now, _ = _graded_league("champion")
    made = promotion.run_promotion(conn, now)
    assert [(d["family"], d["decision"]) for d in made] == [("skater_shots", "rejected")]
    status = dict(conn.execute("SELECT version, status FROM model_versions WHERE model_family = 'skater_shots'"))
    assert status == {"1.4": "champion", "1.5": "retired", "synthetic": "candidate"}


def test_too_few_graded_props_means_wait():
    c = {"n": promotion.MIN_PROPS - 1, "mean_diff": 0.5, "se": 0.01, "lo": 0.48, "hi": 0.52}
    assert promotion.decide(c) == "wait"
    assert promotion.decide(c | {"n": promotion.MIN_PROPS}) == "promoted"
    assert promotion.decide(c | {"n": 10_000, "lo": -0.01, "hi": 0.02}) == "wait"


def test_retired_version_is_not_made_challenger_again(deployed):
    conn, _ = deployed
    report = {
        "version": "1.5",
        "status": "ok",
        "stats": {"shots": {"family": "skater_shots", "passed": True}},
        "choices": {},
    }
    conn.execute("UPDATE model_versions SET status = 'retired' WHERE model_family = 'skater_shots' AND version = '1.5'")
    project.record_evaluation(conn, report, datetime(2026, 5, 1, tzinfo=UTC))
    st = conn.execute(
        "SELECT status FROM model_versions WHERE model_family = 'skater_shots' AND version = '1.5'"
    ).fetchone()[0]
    assert st == "retired"
