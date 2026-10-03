import math
from datetime import UTC, datetime

import numpy as np

from rinkx.models import fit, xg
from rinkx.publish import profile
from tests import synth

NOW = datetime(2026, 5, 1, 12, tzinfo=UTC)


def _true_p(x: float, y: float, kind: str) -> float:
    d = math.hypot(xg.NET_X - x, y)
    z = -0.6 - 0.09 * d + (0.5 if kind == "tip-in" else 0.0) - (0.4 if kind == "slap" else 0.0)
    return 1 / (1 + math.exp(-z))


def _add_attempts(conn, seed: int = 3, per_team: int = 70, signal: bool = True) -> None:
    """Unblocked attempts with locations for every synthetic game; goals drawn from a known xG curve
    (or, with signal=False, at a flat rate that ignores location)."""
    rng = np.random.default_rng(seed)
    src = conn.execute("SELECT id FROM data_sources WHERE code = 'synthetic'").fetchone()[0]
    games = conn.execute("SELECT id, home_team_id, away_team_id FROM games").fetchall()
    for gid, home, away in games:
        goalie = {}
        for team in (home, away):
            goalie[team] = conn.execute(
                "SELECT s.player_id FROM goalie_game_stats s WHERE s.game_id = ? AND s.team_id = ? AND s.started = 1",
                (gid, team),
            ).fetchone()
        idx = 1000
        for team, opp in ((home, away), (away, home)):
            shooters = [
                r[0]
                for r in conn.execute("SELECT id FROM players WHERE current_team_id = ? AND position != 'G'", (team,))
            ]
            for _ in range(per_team):
                idx += 1
                x, y = float(rng.uniform(20, 95)), float(rng.uniform(-40, 40))
                kind = str(rng.choice(["wrist", "snap", "slap", "tip-in", "backhand"]))
                p = _true_p(x, y, kind) if signal else 0.07
                goal = rng.random() < p
                ev = "goal" if goal else str(rng.choice(["shot", "miss"]))
                conn.execute(
                    "INSERT INTO pbp_shot_events (game_id, event_idx, period, period_seconds, event_type, shooter_id, "
                    "goalie_id, team_id, strength_state, x_coord, y_coord, shot_type, source_id, fetched_at) "
                    "VALUES (?,?,?,?,?,?,?,?,'5v5',?,?,?,?,?)",
                    (
                        gid,
                        idx,
                        1 + idx % 3,
                        int(rng.uniform(0, 1200)),
                        ev,
                        int(rng.choice(shooters)),
                        goalie[opp][0] if goalie[opp] else None,
                        team,
                        round(x),
                        round(y),
                        kind,
                        src,
                        synth.FETCHED,
                    ),
                )
    conn.commit()


def test_logistic_recovers_known_coefficients() -> None:
    rng = np.random.default_rng(0)
    X = np.column_stack([np.ones(40_000), rng.normal(size=40_000), rng.normal(size=40_000)])
    w_true = np.array([-2.0, 0.8, -0.5])
    y = (rng.random(40_000) < 1 / (1 + np.exp(-(X @ w_true)))).astype(float)
    w = xg.fit_logistic(X, y, ridge=0.0)
    assert np.allclose(w, w_true, atol=0.08)


def test_xg_fits_on_earlier_games_passes_and_scores_every_attempt() -> None:
    conn, _ = synth.build(teams=8, days=90)
    _add_attempts(conn)
    xg.run_xg(conn, NOW)
    m = xg.latest(conn)
    assert m is not None and m["passed"], xg.latest(conn, passed_only=False)
    assert m["coef"]["dist"] < 0  # farther shots score less
    assert m["coef"]["type.tip-in"] > m["coef"]["type.slap"]
    assert m["train_to"] < m["test_from"]  # fit only on games before the test window
    met = m["metrics"]
    assert met["log_loss"] < met["baseline_log_loss"] and met["ece"] <= xg.ECE_MAX
    unscored = conn.execute(
        "SELECT count(*) FROM pbp_shot_events WHERE xg IS NULL AND x_coord IS NOT NULL AND event_type != 'block'"
    ).fetchone()[0]
    assert unscored == 0
    # goal events from the synthetic box scores have no location: they stay unscored
    assert (
        conn.execute("SELECT count(*) FROM pbp_shot_events WHERE xg IS NOT NULL AND x_coord IS NULL").fetchone()[0] == 0
    )

    # a second run within 20 h doesn't refit
    xg.run_xg(conn, NOW)
    assert conn.execute("SELECT count(*) FROM xg_models").fetchone()[0] == 1

    summary = xg.summary(conn)
    assert summary is not None and summary["passed"] and summary["in_use"] is not None


def test_xg_without_signal_is_not_used() -> None:
    conn, _ = synth.build(teams=8, days=90)
    _add_attempts(conn, signal=False)
    xg.run_xg(conn, NOW)
    newest = xg.latest(conn, passed_only=False)
    assert newest is not None and not newest["passed"]
    assert newest["reason"] in ("did_not_beat_baseline", "miscalibrated")
    assert xg.latest(conn) is None
    assert conn.execute("SELECT count(*) FROM pbp_shot_events WHERE xg IS NOT NULL").fetchone()[0] == 0


def test_too_little_history_is_said_not_guessed() -> None:
    conn, _ = synth.build(teams=4, days=12)
    _add_attempts(conn, per_team=10)
    xg.run_xg(conn, NOW)
    newest = xg.latest(conn, passed_only=False)
    assert newest is not None and newest["reason"] == "insufficient_history"
    assert profile.goalie_xg(conn, 1, "2026-01-01", 20252026) is None


def test_player_xg_and_goals_saved_above_expected() -> None:
    conn, _ = synth.build(teams=8, days=90)
    _add_attempts(conn)
    xg.run_xg(conn, NOW)
    shooter = conn.execute(
        "SELECT shooter_id FROM pbp_shot_events WHERE xg IS NOT NULL GROUP BY shooter_id ORDER BY count(*) DESC LIMIT 1"
    ).fetchone()[0]
    e = profile.expected_goals(conn, shooter, 20252026)
    assert e["xg"] is not None and e["xg"] > 0 and e["xg_games"] > 0 and e["ixg_per_game"] > 0
    goalie = conn.execute(
        "SELECT goalie_id FROM pbp_shot_events WHERE xg IS NOT NULL GROUP BY goalie_id ORDER BY count(*) DESC LIMIT 1"
    ).fetchone()[0]
    a = profile.goalie_xg(conn, goalie, "2026-12-31", 20252026)
    assert a is not None
    assert math.isclose(a["gsax"], a["xg_against"] - a["goals_against"], abs_tol=0.02)


def test_shot_quality_falls_back_to_the_league_rate_without_xg() -> None:
    c = {
        "prior_finish": np.array([0.10, 0.10]),
        "prior.xq": np.array([0.0, 0.08]),
        "x.xg.0": np.array([0.0, 4.0]),
        "x.xg_sog.0": np.array([0.0, 20.0]),
        "x.goals.0": np.array([3.0, 3.0]),
        "x.shots.0": np.array([20.0, 20.0]),
    }
    q = fit.shot_quality(c, 0, 50.0)
    assert q[0] == 0.10  # no xG anywhere: the league's goals per shot
    # 0.2 xG per shot over 20 shots, shrunk by 50 shots toward 0.08, scaled to the league's 0.10
    assert math.isclose(q[1], (4.0 + 50 * 0.08) / 70 * (0.10 / 0.08))
    assert np.allclose(fit.finish(c, 0, 25.0), (3.0 + 25 * 0.10) / 45)
    assert fit.finish(c, 0, 25.0, 50.0)[1] > fit.finish(c, 0, 25.0)[1]
