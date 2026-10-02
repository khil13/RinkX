"""Phase 7: settlement rules, the grader (closing line, CLV, voids, regrading) and performance metrics."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
import synth

from rinkx.grading import grade
from rinkx.grading import settle as st
from rinkx.grading.performance import performance, reliability
from rinkx.pricing.odds import decimal, devig
from rinkx.timeutil import iso

# ---- settlement (pure) -------------------------------------------------------------------------


def test_settle_over_under_and_push():
    assert st.settle("over_under", 2.5, 3).result == "over"
    assert st.settle("over_under", 2.5, 2).result == "under"
    assert st.settle("over_under", 3.0, 3).result == "push"
    assert st.settle("yes_no", None, 1).result == "yes"
    assert st.settle("yes_no", None, 0).result == "no"
    assert st.settle("moneyline", None, 1).result == "home"
    assert st.settle("moneyline", None, 0).result == "away"
    with pytest.raises(ValueError):
        st.settle("over_under", None, 3)


def test_grade_lean_profit():
    win = st.Settled("over", 4)
    assert st.grade_lean("over", win, -120) == st.Graded("win", pytest.approx(100 / 120))
    assert st.grade_lean("under", win, +150) == st.Graded("loss", -1.0)
    assert st.grade_lean("over", st.Settled("push", 3), -110) == st.Graded("push", 0.0)
    assert st.grade_lean("over", st.void("did_not_play"), -110) == st.Graded("void", 0.0)
    assert st.grade_lean("none", win, None) == st.Graded("no_bet", None)
    assert st.grade_lean("yes", st.Settled("yes", 1), +250).profit == pytest.approx(2.5)


def test_clv_sign():
    # Took +120 (implied 45.5%); the close says 50% fair: that price beat the close.
    assert st.clv(0.50, +120) == pytest.approx(0.10)
    assert st.clv(0.40, +120) < 0
    assert st.clv(None, +120) is None


def _game(**kw):
    base = {
        "game_status": "final",
        "start_time_utc": "2026-01-10T00:00:00Z",
        "home_score": 3,
        "away_score": 2,
        "ended_in": "REG",
        "game_id": 1,
        "player_id": None,
        "line": None,
    }
    return base | kw


def test_game_markets_follow_book_rules():
    now = datetime(2026, 1, 11, tzinfo=UTC)
    # Shootout: the NHL score credits the winner one goal; books count it in the total and the moneyline.
    so = _game(home_score=3, away_score=2, ended_in="SO")
    assert grade.actual(None, so | {"market": "game_moneyline", "kind": "moneyline"}, now).result == "home"
    tot = grade.actual(None, so | {"market": "game_total", "kind": "over_under", "line": 4.5}, now)
    assert (tot.result, tot.actual) == ("over", 5.0)
    assert grade.actual(None, _game(game_status="cancelled", market="game_total", kind="over_under"), now).result == (
        "void"
    )
    late = _game(game_status="postponed", market="game_total", kind="over_under", line=5.5)
    assert grade.actual(None, late, now + timedelta(days=2)).void_reason == "game_postponed"
    assert grade.actual(None, late, datetime(2026, 1, 10, 12, tzinfo=UTC)) is None  # may still be played
    assert grade.actual(None, _game(home_score=None, market="game_total", kind="over_under"), now) is None


# ---- grader and metrics on a synthetic priced history -----------------------------------------


@pytest.fixture(scope="module")
def league():
    conn, truth = synth.build(teams=8, days=90, seed=4)
    n = synth.add_priced_history(conn, truth, days=30)
    assert n > 200
    last = conn.execute("SELECT max(game_date) FROM games").fetchone()[0]
    today = date.fromisoformat(last) + timedelta(days=1)
    now = datetime(today.year, today.month, today.day, 12, tzinfo=UTC)
    return conn, today, now, n


def test_every_prediction_graded_against_the_box_score(league):
    conn, today, now, n = league
    assert grade.run_grading(conn, now, today) == n
    rows = conn.execute(
        "SELECT p.*, r.*, s.shots FROM predictions p JOIN model_results r ON r.prediction_id = p.id "
        "JOIN player_game_stats s ON s.game_id = p.game_id AND s.player_id = p.player_id"
    ).fetchall()
    assert len(rows) == n
    for r in rows:
        assert r["actual_value"] == r["shots"]
        assert r["result"] == ("over" if r["shots"] > 2.5 else "under")
        if r["side"] == "none":
            assert (r["outcome"], r["profit_units"]) == ("no_bet", None)
            assert r["clv"] is None
            continue
        price = r["over_price"] if r["side"] == "over" else r["under_price"]
        won = r["side"] == r["result"]
        assert r["outcome"] == ("win" if won else "loss")
        assert r["profit_units"] == pytest.approx(decimal(price) - 1 if won else -1)
        # Closing line = the last open price before the start; CLV from its no-vig probability.
        close = conn.execute(
            "SELECT over_price, under_price FROM line_movements WHERE prop_line_id = ? ORDER BY observed_at DESC",
            (r["prop_line_id"],),
        ).fetchone()
        assert (r["closing_over_price"], r["closing_under_price"]) == tuple(close)
        nv = devig(close[0], close[1])
        p_side = nv.over if r["side"] == "over" else nv.under
        assert r["clv"] == pytest.approx(p_side * decimal(price) - 1)


def test_did_not_play_is_void_and_regrading_window(league):
    conn, today, now, _ = league
    recent = conn.execute(
        "SELECT p.game_id, p.player_id FROM predictions p JOIN games g ON g.id = p.game_id "
        "WHERE p.side <> 'none' ORDER BY g.start_time_utc DESC LIMIT 1"
    ).fetchone()
    old = conn.execute(
        "SELECT p.game_id, p.player_id FROM predictions p JOIN games g ON g.id = p.game_id "
        "WHERE p.side <> 'none' ORDER BY g.start_time_utc LIMIT 1"
    ).fetchone()
    for gid, pid in (recent, old):
        conn.execute("DELETE FROM player_game_stats WHERE game_id = ? AND player_id = ?", (gid, pid))
    grade.run_grading(conn, now, today)

    def results(gid, pid):
        return conn.execute(
            "SELECT r.result, r.void_reason, r.outcome, r.profit_units FROM model_results r "
            "JOIN predictions p ON p.id = r.prediction_id WHERE p.game_id = ? AND p.player_id = ? AND p.side <> 'none'",
            (gid, pid),
        ).fetchall()

    # A recent game is graded again (stat corrections flow through): now void, stake returned.
    assert {tuple(r) for r in results(*recent)} == {("void", "did_not_play", "void", 0.0)}
    # Older results are left as they were.
    assert all(r["result"] != "void" for r in results(*old))


def test_performance_metrics(league):
    conn, today, now, _ = league
    grade.run_grading(conn, now, today)
    perf = performance(conn, now)
    assert perf["synthetic"] is True and perf["lineup_mode"] == "live_tracked"
    # One row per (game, player, market): the same prop at two books counts once.
    subjects = conn.execute("SELECT count(DISTINCT game_id || '-' || player_id) FROM predictions").fetchone()[0]
    assert perf["graded"] == subjects
    cal = perf["calibration"]
    assert sum(b["n"] for b in cal["reliability"]) == cal["n"]
    assert 0 <= cal["ece"] < 0.2
    assert {"brier", "log_loss"} <= set(cal["model"]) and {"brier", "log_loss"} <= set(cal["market"])
    # The market was priced from the true rates, so it should score at least as well as a season average.
    assert cal["market"]["brier"] <= cal["model_same_rows"]["brier"] + 0.01

    bets = perf["bets"]
    assert bets["n"] == bets["wins"] + bets["losses"] + bets["pushes"]
    assert sum(d["profit"] for d in perf["series"]) == pytest.approx(bets["profit"], abs=0.02)
    assert perf["series"][-1]["cumulative"] == pytest.approx(bets["profit"], abs=0.02)
    assert perf["max_drawdown"] <= 0
    assert bets["roi_ci"][0] <= bets["roi"] <= bets["roi_ci"][1]
    assert sum(b["n"] for b in perf["by_month"]) == bets["n"]
    assert sum(b["n"] for b in perf["by_confidence"]) == bets["n"]
    assert [b["key"] for b in perf["by_version"]] == ["synthetic"]
    assert perf["voids"] == {"Player did not play": 1}  # from the previous test
    assert len(perf["recent"]) == 50
    assert perf["recent"][0]["date"] >= perf["recent"][-1]["date"]


def test_reliability_ece():
    p = [0.1] * 10 + [0.9] * 10
    y = [0] * 9 + [1] + [1] * 9 + [0]
    table, ece = reliability(p, y)
    assert [b["n"] for b in table] == [10, 10]
    assert ece == pytest.approx(0.0)
    _, bad = reliability([0.9] * 10, [0] * 10)
    assert bad == pytest.approx(0.9)


def test_closing_ignores_moves_after_the_start(league):
    conn, *_ = league
    line_id, start = conn.execute(
        "SELECT p.prop_line_id, g.start_time_utc FROM predictions p JOIN games g ON g.id = p.game_id LIMIT 1"
    ).fetchone()
    src = conn.execute("SELECT id FROM data_sources LIMIT 1").fetchone()[0]
    after = iso(datetime.fromisoformat(start.replace("Z", "+00:00")) + timedelta(minutes=5))
    conn.execute(
        "INSERT INTO line_movements (prop_line_id, observed_at, line, over_price, under_price, status, source_id) "
        "VALUES (?, ?, 2.5, 500, -900, 'open', ?)",
        (line_id, after, src),
    )
    c = grade.closing(conn, line_id, start)
    assert c is not None and c.over != 500


def test_goalie_and_first_goal_settlement(league):
    conn, _, now, _ = league
    g = conn.execute(
        "SELECT * FROM games WHERE status = 'final' AND ended_in = 'REG' AND home_score > 0 LIMIT 1"
    ).fetchone()
    row = {k: g[k] for k in ("start_time_utc", "home_score", "away_score", "ended_in")} | {
        "game_status": "final",
        "game_id": g["id"],
    }
    starter = conn.execute("SELECT * FROM goalie_game_stats WHERE game_id = ? AND started = 1", (g["id"],)).fetchone()
    saves = grade.actual(
        conn,
        row | {"market": "goalie_saves", "kind": "over_under", "line": 24.5, "player_id": starter["player_id"]},
        now,
    )
    assert saves.actual == starter["saves"]
    backup = conn.execute(
        "SELECT id FROM players WHERE position = 'G' AND current_team_id = ? AND id <> ?",
        (starter["team_id"], starter["player_id"]),
    ).fetchone()[0]
    benched = grade.actual(
        conn, row | {"market": "goalie_saves", "kind": "over_under", "line": 24.5, "player_id": backup}, now
    )
    assert benched.void_reason == "goalie_did_not_start"

    first = conn.execute(
        "SELECT shooter_id FROM pbp_shot_events WHERE game_id = ? AND event_type = 'goal' "
        "ORDER BY period, period_seconds, event_idx LIMIT 1",
        (g["id"],),
    ).fetchone()[0]
    other = conn.execute(
        "SELECT player_id FROM player_game_stats WHERE game_id = ? AND player_id <> ? LIMIT 1", (g["id"], first)
    ).fetchone()[0]
    fg = {"market": "skater_first_goal", "kind": "yes_no", "line": None}
    assert grade.actual(conn, row | fg | {"player_id": first}, now).result == "yes"
    assert grade.actual(conn, row | fg | {"player_id": other}, now).result == "no"


def test_track_record_feeds_confidence(league, monkeypatch):
    from rinkx.grading import performance as perf_mod
    from rinkx.pricing import confidence as conf

    conn, today, now, _ = league
    grade.run_grading(conn, now, today)
    perf = performance(conn, now)
    shots = conn.execute("SELECT id FROM markets WHERE code = 'skater_shots_on_goal'").fetchone()[0]
    monkeypatch.setattr(perf_mod, "TRACK_MIN", 10)
    tr = perf_mod.track_records(conn)
    b = perf["bets"]
    assert tr[shots] == pytest.approx(b["roi"] / b["expected_roi"], rel=1e-3)
    monkeypatch.setattr(perf_mod, "TRACK_MIN", b["n"] + 1)
    assert perf_mod.track_records(conn) == {}  # too few graded bets: no adjustment

    base = dict(
        edge=0.06,
        p_model=0.6,
        games_in_history=60,
        data_quality=0.9,
        book_novigs=[0.54, 0.54],
        one_sided=False,
        moved_against_pts=None,
        is_goalie_prop=False,
        is_game_market=False,
        start_probability=None,
        start_confirmed=False,
        opp_goalie_confirmed=True,
    )
    plain = conf.score(conf.Inputs(**base))
    weak = conf.score(conf.Inputs(**base, track_record=0.4))
    assert weak.parts["edge_strength"] == round(plain.parts["edge_strength"] * 0.5)
    assert any("0.40x their expected value" in n for n in weak.notes["edge_strength"])


def test_confidence_check_uses_roi_and_needs_enough_bets():
    from rinkx.grading.performance import _monotonic

    def b(key, n, roi, wins=0):
        return {"key": key, "n": n, "roi": roi, "wins": wins, "losses": n - wins}

    # Higher buckets win less often (longer prices) but return more: that's monotonic.
    assert _monotonic([b("50-59", 40, -0.02, wins=25), b("60-69", 40, 0.01, wins=15)]) is True
    assert _monotonic([b("50-59", 40, 0.05), b("60-69", 40, -0.03)]) is False
    assert _monotonic([b("50-59", 40, 0.05), b("60-69", 5, -0.03)]) is None  # one bucket too small
