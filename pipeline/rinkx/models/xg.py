"""Expected goals (xG): the chance an unblocked shot attempt becomes a goal, from where and how
it was taken. RinkX's own model, fit on NHL play-by-play; no third-party xG source.

Each unblocked, non-empty-net attempt with coordinates (shots on goal, misses and goals; blocked
attempts are left out because their coordinates are where the block happened) is described by:
distance and angle to the net, shot type, strength (power play / shorthanded), whether it came
within 3 seconds of the same team's previous attempt (a rebound), and whether it was taken from
behind the goal line. A logistic regression with a light ridge penalty maps these to a probability.

The coefficients are fit only on games before the model test window (rinkx.models.fit.split_dates),
then scored on the later games they never saw. They are used (stored on every shot) only if they
beat the league-average goal rate on those later games and are calibrated there. Otherwise the
site says xG is unavailable, as it did before.
"""

from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import numpy as np
from numpy.typing import NDArray

from rinkx.ingestion.runs import ingestion_run, register_source
from rinkx.timeutil import iso, parse_iso

VERSION = "xg-1"
NET_X = 89.0
REBOUND_S = 3
RIDGE = 1.0
MIN_TRAIN = 4000  # unblocked attempts
MIN_TEST = 2000
REFIT_EVERY = timedelta(hours=20)
REFIT_GROWTH = 1.10
ECE_MAX = 0.015
TYPES = ("snap", "slap", "backhand", "tip-in", "deflected", "wrap-around")  # wrist shot is the reference
FEATURES = (
    "bias",
    "dist",
    "log_dist",
    "angle",
    *(f"type.{t}" for t in TYPES),
    "type.other",
    "pp",
    "sh",
    "rebound",
    "behind_net",
)

F = NDArray[np.float64]


@dataclass
class Shots:
    ids: NDArray[np.int64]
    dates: NDArray[np.str_]
    X: F
    y: F


def _row(x: float, y: float, shot_type: str | None, strength: str | None, rebound: bool) -> list[float]:
    dx = NET_X - x
    dist = math.hypot(dx, y)
    angle = math.degrees(math.atan2(abs(y), dx))  # 0 = straight on; over 90 = from behind the goal line
    t = (shot_type or "").lower()
    own = opp = 5
    if strength and "v" in strength:
        a, b = strength.split("v", 1)
        if a.isdigit() and b.isdigit():
            own, opp = int(a), int(b)
    return [
        1.0,
        dist / 10,
        math.log1p(dist),
        angle / 45,
        *(float(t == k) for k in TYPES),
        float(t not in TYPES and t != "wrist"),
        float(own > opp),
        float(own < opp),
        float(rebound),
        float(dx < 0),
    ]


def load(conn: sqlite3.Connection, cond: str = "g.status = 'final'") -> Shots:
    """Every unblocked, non-empty-net attempt with coordinates, in game order."""
    rows = conn.execute(
        "SELECT e.id, e.game_id, g.game_date, e.period, e.period_seconds, e.event_type, e.team_id, e.x_coord, "
        "e.y_coord, e.shot_type, e.strength_state, e.is_empty_net FROM pbp_shot_events e "
        f"JOIN games g ON g.id = e.game_id WHERE {cond} AND e.event_type IN ('shot','miss','goal') "
        "ORDER BY e.game_id, e.period, e.period_seconds, e.event_idx"
    ).fetchall()
    ids, dates, X, y = [], [], [], []
    last: dict[tuple[int, int, int], int] = {}  # (game, period, team) -> seconds of its last attempt
    for r in rows:
        key = (r[1], r[3], r[6])
        rebound = key in last and 0 <= r[4] - last[key] <= REBOUND_S
        last[key] = r[4]
        if r[7] is None or r[8] is None or r[11]:
            continue
        ids.append(r[0])
        dates.append(r[2])
        X.append(_row(float(r[7]), float(r[8]), r[9], r[10], rebound))
        y.append(1.0 if r[5] == "goal" else 0.0)
    return Shots(
        np.array(ids, dtype=np.int64),
        np.array(dates, dtype=str),
        np.array(X, dtype=float).reshape(-1, len(FEATURES)),
        np.array(y, dtype=float),
    )


def _sigmoid(z: F) -> F:
    return np.asarray(1 / (1 + np.exp(-np.clip(z, -30, 30))), dtype=float)


def fit_logistic(X: F, y: F, ridge: float = RIDGE, iters: int = 100) -> F:
    """Newton's method with step halving on the penalised log likelihood (intercept not penalised)."""
    w = np.zeros(X.shape[1])
    rate = float(np.clip(y.mean(), 1e-4, 1 - 1e-4))
    w[0] = math.log(rate / (1 - rate))
    pen = np.full(X.shape[1], ridge)
    pen[0] = 0.0

    def objective(v: F) -> float:
        return float(-_ll(predict(X, v), y).sum() + 0.5 * (pen * v * v).sum())

    cur = objective(w)
    for _ in range(iters):
        p = predict(X, w)
        g = X.T @ (p - y) + pen * w
        h = (X * (p * (1 - p))[:, None]).T @ X + np.diag(pen) + 1e-9 * np.eye(len(w))
        step = np.linalg.solve(h, g)
        t = 1.0
        while t > 1e-6:
            trial = w - t * step
            new = objective(trial)
            if new <= cur:
                break
            t /= 2
        else:
            break
        w, prev, cur = trial, cur, new
        if prev - cur < 1e-9 * max(1.0, abs(cur)):
            break
    return w


def predict(X: F, w: F) -> F:
    return _sigmoid(X @ w)


def _ll(p: F, y: F) -> F:
    p = np.clip(p, 1e-9, 1 - 1e-9)
    return np.asarray(y * np.log(p) + (1 - y) * np.log(1 - p), dtype=float)


def _paired(diff: F) -> dict[str, float]:
    n = len(diff)
    mean = float(np.mean(diff))
    se = float(np.std(diff, ddof=1) / math.sqrt(n)) if n > 1 else math.inf
    return {"mean": round(mean, 6), "se": round(se, 6), "lo": round(mean - 1.96 * se, 6)}


def calibration(p: F, y: F, bins: int = 10) -> tuple[list[dict[str, float]], float]:
    """Equal-count bins by predicted probability: (bins, expected calibration error)."""
    order = np.argsort(p)
    out, ece = [], 0.0
    for chunk in np.array_split(order, bins):
        if len(chunk) == 0:
            continue
        mp, mo = float(p[chunk].mean()), float(y[chunk].mean())
        out.append({"pred": round(mp, 4), "obs": round(mo, 4), "n": len(chunk)})
        ece += len(chunk) / len(p) * abs(mp - mo)
    return out, round(ece, 5)


def auc(p: F, y: F) -> float | None:
    pos = int(y.sum())
    neg = len(y) - pos
    if pos == 0 or neg == 0:
        return None
    ranks = np.empty(len(p))
    ranks[np.argsort(p, kind="mergesort")] = np.arange(1, len(p) + 1)
    return round(float((ranks[y == 1].sum() - pos * (pos + 1) / 2) / (pos * neg)), 4)


def evaluate(shots: Shots, test_start: str) -> dict[str, Any]:
    """Fit on attempts before `test_start`, score the rest. Returns the fit, metrics and the verdict."""
    tr, te = shots.dates < test_start, shots.dates >= test_start
    out: dict[str, Any] = {"n_train": int(tr.sum()), "n_test": int(te.sum()), "coef": {}, "metrics": {}}
    if out["n_train"] < MIN_TRAIN or out["n_test"] < MIN_TEST or shots.y[tr].sum() < 50:
        out.update(passed=False, reason="insufficient_history")
        return out
    X, y = shots.X, shots.y
    w = fit_logistic(X[tr], y[tr])
    p = predict(X[te], w)
    yt = y[te]
    ll = _ll(p, yt)
    const = _ll(np.full(len(yt), float(y[tr].mean())), yt)
    dcols = [FEATURES.index(k) for k in ("bias", "dist", "log_dist", "angle")]
    wd = fit_logistic(X[tr][:, dcols], y[tr])
    ld = _ll(predict(X[te][:, dcols], wd), yt)
    bins, ece = calibration(p, yt)
    vs_const = _paired(ll - const)
    out["coef"] = {k: round(float(v), 5) for k, v in zip(FEATURES, w, strict=True)}
    out["metrics"] = {
        "log_loss": round(float(-ll.mean()), 5),
        "baseline_log_loss": round(float(-const.mean()), 5),
        "distance_only_log_loss": round(float(-ld.mean()), 5),
        "vs_baseline": vs_const,
        "vs_distance_only": _paired(ll - ld),
        "auc": auc(p, yt),
        "calibration": bins,
        "ece": ece,
        "goal_rate_test": round(float(yt.mean()), 4),
        "mean_xg_test": round(float(p.mean()), 4),
    }
    dtr, dte = sorted(set(shots.dates[tr].tolist())), sorted(set(shots.dates[te].tolist()))
    out["train"] = (dtr[0], dtr[-1])
    out["test"] = (dte[0], dte[-1])
    if vs_const["lo"] <= 0:
        out.update(passed=False, reason="did_not_beat_baseline")
    elif ece > ECE_MAX:
        out.update(passed=False, reason="miscalibrated")
    else:
        out.update(passed=True, reason=None)
    return out


def latest(conn: sqlite3.Connection, passed_only: bool = True) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT id, version, fitted_at, train_from, train_to, test_from, test_to, n_train, n_test, coef, metrics, "
        f"passed, reason FROM xg_models {'WHERE passed = 1' if passed_only else ''} ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    keys = ("id", "version", "fitted_at", "train_from", "train_to", "test_from", "test_to", "n_train", "n_test")
    d: dict[str, Any] = dict(zip(keys, row[:9], strict=True))
    d.update(coef=json.loads(row[9]), metrics=json.loads(row[10]), passed=bool(row[11]), reason=row[12])
    return d


def _weights(model: dict[str, Any]) -> F | None:
    coef = model["coef"]
    if model["version"] != VERSION or any(k not in coef for k in FEATURES):
        return None  # fit by a different feature set: wait for the next fit
    return np.array([coef[k] for k in FEATURES], dtype=float)


def score(conn: sqlite3.Connection, model: dict[str, Any] | None, *, all_rows: bool) -> int:
    """Write xG onto stored attempts (all of them, or those not scored yet). Returns rows written."""
    if model is None or (w := _weights(model)) is None:
        return 0
    pending = (
        "SELECT game_id FROM pbp_shot_events WHERE xg IS NULL AND event_type IN ('shot','miss','goal') "
        "AND is_empty_net = 0 AND x_coord IS NOT NULL AND y_coord IS NOT NULL"
    )
    shots = load(conn, "1" if all_rows else f"e.game_id IN ({pending})")
    if len(shots.ids) == 0:
        return 0
    p = predict(shots.X, w)
    label = f"{model['version']}#{model['id']}"
    conn.executemany(
        "UPDATE pbp_shot_events SET xg = ?, xg_model_version = ? WHERE id = ?",
        [(round(float(v), 5), label, int(i)) for v, i in zip(p, shots.ids, strict=True)],
    )
    return len(shots.ids)


def _due(conn: sqlite3.Connection, now: datetime, n_events: int) -> bool:
    last = latest(conn, passed_only=False)
    if last is None:
        return True
    if now - parse_iso(last["fitted_at"]) >= REFIT_EVERY:
        return True
    return bool(n_events >= (last["n_train"] + last["n_test"]) * REFIT_GROWTH)


def run_xg(conn: sqlite3.Connection, now: datetime) -> None:
    """Refit at most every 20 h (or when shot history grows 10%); score new attempts every run."""
    from rinkx.models.fit import split_dates
    from rinkx.models.project import MODELS_SOURCE

    src = register_source(conn, MODELS_SOURCE)
    with ingestion_run(conn, src, "models.xg") as run:
        shots = load(conn)
        refit = len(shots.ids) > 0 and _due(conn, now, len(shots.ids))
        if refit:
            dates = [r[0] for r in conn.execute("SELECT DISTINCT game_date FROM games WHERE status = 'final'")]
            split = split_dates(dates)
            res: dict[str, Any] = (
                evaluate(shots, split[1])
                if split
                else {"n_train": 0, "n_test": 0, "coef": {}, "metrics": {}, "passed": False}
            )
            if split is None:
                res["reason"] = "insufficient_history"
            tr: tuple[str | None, str | None] = res.get("train", (None, None))
            te: tuple[str | None, str | None] = res.get("test", (None, None))
            conn.execute(
                "INSERT INTO xg_models (version, fitted_at, train_from, train_to, test_from, test_to, n_train, n_test, "
                "coef, metrics, passed, reason) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    VERSION,
                    iso(now),
                    tr[0],
                    tr[1],
                    te[0],
                    te[1],
                    res["n_train"],
                    res["n_test"],
                    json.dumps(res["coef"]),
                    json.dumps(res["metrics"]),
                    int(res["passed"]),
                    res["reason"],
                ),
            )
            run.meta.update(passed=res["passed"], reason=res["reason"], n_train=res["n_train"], n_test=res["n_test"])
        model, newest = latest(conn), latest(conn, passed_only=False)
        # a new fit that passed re-scores every attempt; otherwise only attempts not scored yet
        # (a fit that failed leaves the last one that passed in use)
        fresh = refit and model is not None and newest is not None and model["id"] == newest["id"]
        run.rows_upserted = score(conn, model, all_rows=fresh)
        run.rows_read = len(shots.ids)


def summary(conn: sqlite3.Connection) -> dict[str, Any] | None:
    """What the Models page shows: the newest fit (passed or not) and the one in use."""
    newest = latest(conn, passed_only=False)
    if newest is None:
        return None
    used = latest(conn)
    return {
        "version": newest["version"],
        "fitted_at": newest["fitted_at"],
        "train": [newest["train_from"], newest["train_to"]],
        "test": [newest["test_from"], newest["test_to"]],
        "n_train": newest["n_train"],
        "n_test": newest["n_test"],
        "passed": newest["passed"],
        "reason": newest["reason"],
        "metrics": newest["metrics"],
        "coef": newest["coef"],
        "in_use": None if used is None else {"id": used["id"], "fitted_at": used["fitted_at"]},
    }
