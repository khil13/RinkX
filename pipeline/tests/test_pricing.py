"""Phase 5 pricing: odds math properties, hand-checked examples, push handling, the side
rule, confidence parts, prediction immutability, and pricing real projections against lines
in the synthetic league (tests/synth.py)."""

from __future__ import annotations

import json
import random
import sqlite3
from datetime import UTC, date, datetime, timedelta

import pytest
import synth

from rinkx.ingestion.odds.store import upsert_line
from rinkx.models import project
from rinkx.pricing import confidence as conf
from rinkx.pricing.model_probs import at_line, yes_no
from rinkx.pricing.odds import decimal, devig, ev_per_unit, implied, multiplicative, power, shin
from rinkx.pricing.price import PricingConfig, price_line, run_pricing

CFG = PricingConfig(min_edge=0.03, dq_floor=0.6, implausible_edge=0.15)


def american(rng: random.Random) -> int:
    v = rng.randint(100, 600)
    return v if rng.random() < 0.5 else -v


def test_no_vig_probabilities_sum_to_one_for_every_method():
    rng = random.Random(4)
    for _ in range(2000):
        o, u = american(rng), american(rng)
        io, iu = implied(o), implied(u)
        if io + iu <= 1:  # not a real two-way market (no margin)
            continue
        for f in (multiplicative, power, shin):
            po, pu = f(io, iu)
            assert 0 < po < 1 and po + pu == pytest.approx(1, abs=1e-9)
        assert devig(o, u).over + devig(o, u).under == pytest.approx(1, abs=1e-9)


def test_symmetric_markets_are_even_for_every_method():
    for p in (-105, -110, -120, -150):
        for f in (multiplicative, power, shin):
            assert f(implied(p), implied(p))[0] == pytest.approx(0.5, abs=1e-9)


def test_hand_checked_example_from_the_design_doc():
    nv = devig(-115, -105)
    assert implied(-115) == pytest.approx(115 / 215) and implied(-105) == pytest.approx(105 / 205)
    assert nv.overround == pytest.approx(1.0471, abs=1e-4)
    assert nv.over == pytest.approx(0.5108, abs=1e-4) and nv.method == "multiplicative"
    # Lopsided markets use the power method, which takes more margin off the longshot.
    lop = devig(-250, 190)
    assert lop.method == "power" and lop.over > multiplicative(implied(-250), implied(190))[0]
    with pytest.raises(ValueError):
        implied(-50)


def test_expected_value():
    assert decimal(-110) == pytest.approx(1.9091, abs=1e-4) and decimal(150) == 2.5
    assert ev_per_unit(0.5, 0.5, 100) == 0
    assert ev_per_unit(110 / 210, 100 / 210, -110) == pytest.approx(0, abs=1e-12)  # break-even at -110
    assert ev_per_unit(0.4, 0.4, 100) == pytest.approx(0.0)  # a 20% push returns the stake


def test_model_probabilities_at_a_line_with_pushes():
    pmf = [0.1, 0.2, 0.3, 0.25, 0.15]
    half = at_line(pmf, 2.5)
    assert (half.p_over, half.p_under, half.p_push) == pytest.approx((0.4, 0.6, 0.0))
    whole = at_line(pmf, 2.0)
    assert (whole.p_over, whole.p_under, whole.p_push) == pytest.approx((0.4, 0.3, 0.3))
    assert whole.over_given_no_push == pytest.approx(0.4 / 0.7)
    assert yes_no([0.7, 0.2, 0.1]).p_over == pytest.approx(0.3)


def test_price_line_example_and_calculation_trail():
    pr = price_line("over_under", at_line([0.388, 0.0, 0.0, 0.0, 0.0, 0.612], 4.5), 4.5, -115, -105, 0.9, CFG, "sog")
    assert pr.edge_over == pytest.approx(0.612 - 0.5108, abs=1e-3)
    assert pr.side == "over"
    assert pr.ev_over == pytest.approx(0.612 * (100 / 115) - 0.388, abs=1e-6)
    assert "Implied(-115) = 115/215 = 0.5349" in pr.calculation
    assert any(line.startswith("Lean: over") for line in pr.calculation)


def test_side_rule():
    # Edge under the minimum: no lean.
    small = price_line("over_under", at_line([0.48, 0.52], 0.5), 0.5, -110, -110, 0.9, CFG, "x")
    assert small.side == "none" and small.edge_over == pytest.approx(0.02)
    # Low data quality: no lean, even with a big edge.
    big = at_line([0.3, 0.7], 0.5)
    assert price_line("over_under", big, 0.5, -110, -110, 0.5, CFG, "x").side == "none"
    assert price_line("over_under", big, 0.5, -110, -110, 0.9, CFG, "x").side == "over"
    # A heavy margin can make EV negative despite a no-vig edge: no lean.
    pr = price_line("over_under", at_line([0.42, 0.58], 0.5), 0.5, -160, -160, 0.9, CFG, "x")
    assert pr.edge_over == pytest.approx(0.08) and pr.ev_over < 0 and pr.side == "none"
    # One-sided (anytime goal "Yes" only): compared to the raw implied probability.
    yes = price_line("yes_no", yes_no([0.6, 0.4]), None, 200, None, 0.9, CFG, "goal")
    assert yes.novig is None and yes.edge_over == pytest.approx(0.4 - 1 / 3) and yes.side == "yes"


def _inputs(**kw: object) -> conf.Inputs:
    base = dict(
        edge=0.06,
        p_model=0.56,
        games_in_history=60,
        data_quality=0.9,
        book_novigs=[0.5, 0.505],
        one_sided=False,
        moved_against_pts=None,
        is_goalie_prop=False,
        is_game_market=False,
        start_probability=None,
        start_confirmed=False,
        opp_goalie_confirmed=True,
    )
    return conf.Inputs(**(base | kw))  # type: ignore[arg-type]


def test_confidence_parts():
    c = conf.score(_inputs())
    assert sum(c.parts.values()) == c.score <= 100
    assert all(0 <= c.parts[k] <= conf.MAX[k] for k in conf.MAX) and all(c.notes[k] for k in conf.MAX)
    # A bigger edge relative to its uncertainty scores higher on edge strength...
    assert conf.score(_inputs(edge=0.09)).parts["edge_strength"] > c.parts["edge_strength"]
    # ...but an implausibly large edge lowers market agreement.
    assert conf.score(_inputs(edge=0.2)).parts["market_agreement"] < c.parts["market_agreement"]
    # Price moving against the side, books disagreeing, one book only: each lowers agreement.
    assert conf.score(_inputs(moved_against_pts=4)).parts["market_agreement"] < c.parts["market_agreement"]
    assert conf.score(_inputs(book_novigs=[0.45, 0.53])).parts["market_agreement"] < c.parts["market_agreement"]
    # An unconfirmed goalie start lowers role certainty and availability for goalie props.
    gk = conf.score(_inputs(is_goalie_prop=True, start_probability=0.6, start_confirmed=False))
    gk_ok = conf.score(_inputs(is_goalie_prop=True, start_probability=1.0, start_confirmed=True))
    assert gk.parts["role_certainty"] < gk_ok.parts["role_certainty"]
    assert gk.parts["availability"] < gk_ok.parts["availability"]
    # Thin history lowers data quality.
    assert conf.score(_inputs(games_in_history=5)).parts["data_quality"] < c.parts["data_quality"]


def test_published_predictions_are_immutable(tmp_path):
    conn, _ = synth.build(str(tmp_path / "imm.db"), days=40, seed=1)
    gid = synth.add_upcoming(conn, date(2025, 10, 7) + timedelta(days=40))
    mid = conn.execute("SELECT id FROM markets WHERE code = 'game_total'").fetchone()[0]
    mv = conn.execute(
        "INSERT INTO model_versions (model_family, version, algorithm, feature_list) VALUES ('t','t','t','[]')"
    ).lastrowid
    gp = conn.execute(
        "INSERT INTO game_projections (game_id, market_id, side, model_version_id, computed_at, as_of, mean, pmf, "
        "trigger_reason) VALUES (?, ?, 'game', ?, 'x', 'x', 6.0, ?, 'scheduled')",
        (gid, mid, mv, json.dumps({"p": [1.0]})),
    ).lastrowid
    pid = conn.execute(
        "INSERT INTO predictions (game_projection_id, game_id, market_id, p_model_over, p_model_under, side, "
        "is_published) VALUES (?, ?, ?, 0.5, 0.5, 'none', 1)",
        (gp, gid, mid),
    ).lastrowid
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        conn.execute("UPDATE predictions SET side = 'over' WHERE id = ?", (pid,))
    with pytest.raises(sqlite3.IntegrityError, match="cannot be deleted"):
        conn.execute("DELETE FROM predictions WHERE id = ?", (pid,))
    with pytest.raises(sqlite3.IntegrityError):  # exactly one of projection / game projection
        conn.execute(
            "INSERT INTO predictions (game_id, market_id, p_model_over, p_model_under, side) "
            "VALUES (?, ?, 0.5, 0.5, 'none')",
            (gid, mid),
        )


def test_pricing_real_projections_against_lines(tmp_path):
    days = 200
    conn, _ = synth.build(str(tmp_path / "p.db"), teams=20, days=days, seed=22)
    today = date(2025, 10, 7) + timedelta(days=days)
    now = datetime(today.year, today.month, today.day, 15, tzinfo=UTC)
    gid = synth.add_upcoming(conn, today)
    project.run_models(conn, now, today)
    src = conn.execute("SELECT id FROM data_sources WHERE code = 'synthetic'").fetchone()[0]
    for code in ("fanduel", "betmgm"):
        conn.execute("INSERT INTO sportsbooks (code, name) VALUES (?, ?)", (code, code))
    books = {r[0]: r[1] for r in conn.execute("SELECT code, id FROM sportsbooks")}
    sog = conn.execute("SELECT id FROM markets WHERE code = 'skater_shots_on_goal'").fetchone()[0]
    total = conn.execute("SELECT id FROM markets WHERE code = 'game_total'").fetchone()[0]
    pid, mean = conn.execute(
        "SELECT p.player_id, p.mean FROM player_projections p WHERE p.game_id = ? AND p.market_id = ? "
        "AND p.is_current = 1 ORDER BY p.mean DESC LIMIT 1",
        (gid, sog),
    ).fetchone()
    at = "2026-04-25T14:00:00Z"
    line_ids = {}
    for code, (o, u) in (("fanduel", (-115, -105)), ("betmgm", (-120, 100))):
        line_ids[code], _ = upsert_line(
            conn,
            game_id=gid,
            market_id=sog,
            book_id=books[code],
            player_id=pid,
            team_id=None,
            line=2.5,
            over=o,
            under=u,
            at=at,
            source_id=src,
            source_ref="test",
        )
    upsert_line(
        conn,
        game_id=gid,
        market_id=total,
        book_id=books["fanduel"],
        player_id=None,
        team_id=None,
        line=6.5,
        over=-110,
        under=-110,
        at=at,
        source_id=src,
        source_ref="test",
    )

    assert run_pricing(conn, now, CFG) == 3  # two player lines + the game total
    rows = conn.execute(
        "SELECT p.*, m.code FROM predictions p JOIN markets m ON m.id = p.market_id ORDER BY p.id"
    ).fetchall()
    for r in rows:
        assert r["is_published"] == 1 and r["side"] in ("over", "under", "none")
        parts = json.loads(r["confidence_parts"])
        assert sum(parts["parts"].values()) == r["confidence"]
        calc = json.loads(r["calculation"])
        assert any(c.startswith("Edge over") for c in calc)
    fd = next(r for r in rows if r["prop_line_id"] == line_ids["fanduel"])
    assert fd["p_novig_over"] == pytest.approx(0.5108, abs=1e-4)
    assert fd["edge_over"] == pytest.approx(fd["p_model_over"] - fd["p_novig_over"], abs=1e-6)
    assert next(r for r in rows if r["code"] == "game_total")["game_projection_id"] is not None

    # Scores are frozen with each prediction; parts add up to the score; the SOG prop has a Shot Environment.
    sc = {r["prediction_id"]: r for r in conn.execute("SELECT * FROM prediction_scores").fetchall()}
    assert set(sc) == {r["id"] for r in rows}
    for r in rows:
        s_ = sc[r["id"]]
        parts = json.loads(s_["intelligence_parts"])
        assert {p["part"] for p in parts} >= {"model_edge", "projection_vs_line", "recent_volume", "market_movement"}
        if s_["intelligence"] is not None:
            assert sum(p["points"] or 0 for p in parts) == pytest.approx(s_["intelligence"], abs=1.0)
        facts = json.loads(s_["facts"])
        assert facts["side"] in ("over", "under") and "projection" in facts
        if r["code"] == "skater_shots_on_goal":
            assert 0 <= s_["shot_environment"] <= 100 and s_["intelligence"] is not None
            assert len(facts["l10_hit"]) == 2 and facts["l10_hit"][1] == 10
            env = json.loads(s_["shot_env_parts"])
            assert sum(p["points"] or 0 for p in env) == pytest.approx(s_["shot_environment"], abs=1.0)
        else:
            assert s_["shot_environment"] is None
    from rinkx.publish.best import best_props

    pub = best_props(conn, now)["rows"]
    sog_row = next(x for x in pub if x["market"] == "skater_shots_on_goal")
    labels = [b["label"] for b in sog_row["scores"]["why"]]
    assert labels[:3] == ["Projected Shots on Goal", "Market line", "Projection vs line"]
    assert "Shot Environment" in labels and "Last 10 at this line" in labels
    assert "These are estimates" in sog_row["scores"]["summary"]

    # Nothing changed: nothing new. A price move: a new frozen prediction for that line only.
    assert run_pricing(conn, now + timedelta(hours=1), CFG) == 0
    upsert_line(
        conn,
        game_id=gid,
        market_id=sog,
        book_id=books["fanduel"],
        player_id=pid,
        team_id=None,
        line=2.5,
        over=-130,
        under=110,
        at="2026-04-25T16:00:00Z",
        source_id=src,
        source_ref="test",
    )
    assert run_pricing(conn, now + timedelta(hours=2), CFG) == 1
    assert mean > 0

    # Phase 10: the Line Movement board shows first vs current price for every open line.
    from rinkx.publish.lines import movement_board

    board = movement_board(conn, now + timedelta(hours=2))["rows"]
    assert len(board) == 3 and board[0]["book_name"] == "fanduel"  # the line that moved sorts first
    assert board[0]["first"]["over"] == -115 and board[0]["now"]["over"] == -130 and board[0]["moves"] == 2
    assert board[0]["change_pts"] == pytest.approx((130 / 230 - 115 / 215) * 100, abs=0.01)
    assert all(r["change_pts"] == 0 for r in board[1:])

    # Phase 6: Best Props rows carry source + timestamps, and only open lines with current projections appear.
    from rinkx.publish.best import best_props

    later = now + timedelta(hours=2)
    data = best_props(conn, later)
    assert data["reason"] is None and len(data["rows"]) == 3
    for row in data["rows"]:
        assert row["book_name"] and row["source"] == "The Odds API"
        assert row["line_seen_at"] and row["priced_at"]
        assert row["lean"] in (None, "over", "under")
        assert row["edge"] == pytest.approx(row["p_model"] - row["p_market"], abs=1e-6)
    fd_row = next(r for r in data["rows"] if r["book"] == "fanduel" and r["market"] == "skater_shots_on_goal")
    assert fd_row["over_price"] == -130  # the latest prediction for the line, after the price move
    conn.execute("UPDATE prop_lines SET status = 'removed' WHERE id = ?", (line_ids["betmgm"],))
    conn.execute("UPDATE player_projections SET is_current = 0 WHERE game_id = ? AND market_id = ?", (gid, sog))
    after = best_props(conn, later)["rows"]
    assert [r["market"] for r in after] == ["game_total"]  # pulled line and stale projection drop out
