"""Live calibration (isotonic regression) from graded predictions.

For each market with enough graded props, the model's P(over / yes / home) (pushes excluded) is
mapped to how often that side actually won, with a monotone, piecewise-linear isotonic fit.
To decide whether to use it, the map is fit on the earliest FIT_SHARE of props in time order
and scored on the rest, which it never saw. It is applied only if it lowers that held-out Brier
score by at least MIN_GAIN. When applied, the published map is refit on every graded prop.
Re-fit at most every REFIT_EVERY. Past predictions are never changed: only future pricing uses it.
"""

from __future__ import annotations

import json
import sqlite3
import statistics
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

from rinkx.grading.performance import SQL, _choose
from rinkx.ingestion.runs import ingestion_run, register_source
from rinkx.models.project import MODELS_SOURCE
from rinkx.timeutil import iso, parse_iso

MIN_ROWS = 400
FIT_SHARE = 0.7
MIN_GAIN = 0.0005  # Brier points; smaller differences are noise
REFIT_EVERY = timedelta(hours=20)
CLIP = (0.01, 0.99)
OVER = ("over", "yes", "home")


def pav(p: list[float], y: list[int]) -> tuple[list[float], list[float]]:
    """Pool-adjacent-violators: increasing step fit. Returns knots (block mean p, block hit rate)."""
    pairs = sorted(zip(p, y, strict=True))
    blocks: list[list[float]] = []  # [sum_p, sum_y, n]
    for pi, yi in pairs:
        blocks.append([pi, float(yi), 1.0])
        while len(blocks) > 1 and blocks[-2][1] / blocks[-2][2] >= blocks[-1][1] / blocks[-1][2]:
            b = blocks.pop()
            blocks[-1] = [blocks[-1][0] + b[0], blocks[-1][1] + b[1], blocks[-1][2] + b[2]]
    xs = [b[0] / b[2] for b in blocks]
    ys = [min(max(b[1] / b[2], CLIP[0]), CLIP[1]) for b in blocks]
    return xs, ys


def apply(knots: dict[str, list[float]], p: float) -> float:
    """Linear interpolation between knots (flat beyond the ends)."""
    xs, ys = knots["x"], knots["y"]
    if not xs:
        return p
    if p <= xs[0]:
        return ys[0]
    if p >= xs[-1]:
        return ys[-1]
    for i in range(1, len(xs)):
        if p <= xs[i]:
            x0, x1, y0, y1 = xs[i - 1], xs[i], ys[i - 1], ys[i]
            return y0 if x1 == x0 else y0 + (y1 - y0) * (p - x0) / (x1 - x0)
    return ys[-1]


def _brier(p: list[float], y: list[int]) -> float:
    return statistics.fmean((a - b) ** 2 for a, b in zip(p, y, strict=True))


def fit_market(rows: list[tuple[float, int]]) -> dict[str, Any]:
    """rows: (p_model_over, won_over) in time order."""
    n = len(rows)
    if n < MIN_ROWS:
        return {"applied": False, "reason": "too_few", "n_fit": n, "n_holdout": 0, "knots": {"x": [], "y": []}}
    cut = int(n * FIT_SHARE)
    train, test = rows[:cut], rows[cut:]
    k = dict(zip(("x", "y"), pav([r[0] for r in train], [r[1] for r in train]), strict=True))
    yt = [r[1] for r in test]
    raw = _brier([r[0] for r in test], yt)
    cal = _brier([apply(k, r[0]) for r in test], yt)
    if raw - cal < MIN_GAIN:
        return {
            "applied": False, "reason": "no_improvement", "n_fit": cut, "n_holdout": len(test),
            "brier_raw": raw, "brier_cal": cal, "knots": k,
        }  # fmt: skip
    full = dict(zip(("x", "y"), pav([r[0] for r in rows], [r[1] for r in rows]), strict=True))
    return {
        "applied": True, "reason": "applied", "n_fit": n, "n_holdout": len(test),
        "brier_raw": raw, "brier_cal": cal, "knots": full,
    }  # fmt: skip


def graded_rows(conn: sqlite3.Connection) -> dict[int, list[tuple[float, int]]]:
    """Per market id: (model P(over), over won) for each graded prop, oldest first (one per prop)."""
    by: dict[int, list[tuple[str, float, int]]] = defaultdict(list)
    for r in _choose(conn.execute(SQL).fetchall()):
        if r["result"] in ("push", "void"):
            continue
        by[r["market_id"]].append((r["game_date"], float(r["p_model_over"]), 1 if r["result"] in OVER else 0))
    return {m: [(p, y) for _, p, y in sorted(v, key=lambda t: t[0])] for m, v in by.items()}


def run_calibration(conn: sqlite3.Connection, now: datetime, *, force: bool = False) -> int:
    last = conn.execute("SELECT max(fitted_at) FROM calibrators").fetchone()[0]
    if not force and last is not None and abs(now - parse_iso(last)) < REFIT_EVERY:
        return 0
    src = register_source(conn, MODELS_SOURCE)
    n = 0
    with ingestion_run(conn, src, "calibration") as run:
        for market, rows in graded_rows(conn).items():
            f = fit_market(rows)
            conn.execute("UPDATE calibrators SET is_current = 0 WHERE market_id = ? AND is_current = 1", (market,))
            conn.execute(
                "INSERT INTO calibrators (market_id, fitted_at, n_fit, n_holdout, brier_raw, brier_cal, applied, "
                "reason, knots) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    market, iso(now), f["n_fit"], f["n_holdout"], f.get("brier_raw"), f.get("brier_cal"),
                    int(f["applied"]), f["reason"], json.dumps(f["knots"]),
                ),
            )  # fmt: skip
            n += 1
        run.rows_upserted = n
    return n


def current(conn: sqlite3.Connection) -> dict[int, dict[str, Any]]:
    """Applied calibrators by market id, for pricing."""
    return {
        r["market_id"]: {
            "knots": json.loads(r["knots"]),
            "n": r["n_fit"],
            "brier_raw": r["brier_raw"],
            "brier_cal": r["brier_cal"],
        }
        for r in conn.execute("SELECT * FROM calibrators WHERE is_current = 1 AND applied = 1")
    }


def published(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [
        {
            "market": r["name"],
            "applied": bool(r["applied"]),
            "reason": r["reason"],
            "n_fit": r["n_fit"],
            "n_holdout": r["n_holdout"],
            "brier_raw": r["brier_raw"],
            "brier_cal": r["brier_cal"],
            "fitted_at": r["fitted_at"],
            "knots": json.loads(r["knots"]),
        }
        for r in conn.execute(
            "SELECT c.*, m.name FROM calibrators c JOIN markets m ON m.id = c.market_id WHERE c.is_current = 1 "
            "ORDER BY m.name"
        )
    ]
