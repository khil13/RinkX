"""Model Performance (Phase 7): how the frozen, pre-game predictions actually did.

One row per (game, market, player or game, book): the last prediction made before the game, which
is what you would have seen. For each (game, market, subject) one of those is chosen:
the lean with the highest EV, otherwise the first book. So the same prop at two books counts once.

* Probability quality, on every graded row, lean or not: Brier score and log loss of the model's
  P(over / yes / home), next to the same scores for the no-vig market on the same rows, plus a
  reliability table and ECE.
* Betting results, on leans only, 1 unit per bet at the price taken: record, profit, ROI with a 95%
  interval, closing-line value, and cumulative profit with the worst drawdown. Split by market,
  confidence bucket, month and model version. Losing months stay in.

Everything here is "live-tracked": priced before the game with what was known then. The
walk-forward model tests (Model Tests page) are the other view.
"""

from __future__ import annotations

import math
import sqlite3
import statistics
from collections import defaultdict
from datetime import datetime
from itertools import pairwise
from typing import Any

from rinkx.grading.settle import VOID_REASONS
from rinkx.timeutil import iso

MIN_BETS = 100  # below this, the page says the sample is too small to judge
TRACK_MIN = 200  # graded leans a market needs before its track record adjusts confidence
MIN_BUCKET = 30  # a bucket needs this many bets before it counts in the monotonic check
CONF_BUCKETS = ((0, 49, "under 50"), (50, 59, "50-59"), (60, 69, "60-69"), (70, 79, "70-79"), (80, 100, "80+"))
OVER = ("over", "yes", "home")
EPS = 1e-6

SQL = """
WITH last AS (
  SELECT p.* FROM predictions p JOIN games g ON g.id = p.game_id
  WHERE p.created_at <= g.start_time_utc
    AND p.id = (SELECT max(p2.id) FROM predictions p2 WHERE p2.game_id = p.game_id AND p2.market_id = p.market_id
                AND coalesce(p2.player_id, 0) = coalesce(p.player_id, 0)
                AND coalesce(p2.sportsbook_id, 0) = coalesce(p.sportsbook_id, 0)
                AND p2.created_at <= g.start_time_utc)
)
SELECT l.*, r.actual_value, r.result, r.void_reason, r.outcome, r.profit_units, r.clv, r.closing_novig_p,
       m.code AS market, m.name AS market_name, m.kind, b.code AS book, b.name AS book_name,
       g.nhl_game_id, g.game_date, h.abbrev AS home, a.abbrev AS away,
       pl.nhl_player_id, pl.full_name, coalesce(mv.version, gmv.version) AS model_version,
       coalesce(pp.provenance, gp.provenance) AS provenance
FROM last l
JOIN model_results r ON r.prediction_id = l.id
JOIN markets m ON m.id = l.market_id
JOIN games g ON g.id = l.game_id
JOIN teams h ON h.id = g.home_team_id JOIN teams a ON a.id = g.away_team_id
LEFT JOIN sportsbooks b ON b.id = l.sportsbook_id
LEFT JOIN players pl ON pl.id = l.player_id
LEFT JOIN player_projections pp ON pp.id = l.projection_id
LEFT JOIN model_versions mv ON mv.id = pp.model_version_id
LEFT JOIN game_projections gp ON gp.id = l.game_projection_id
LEFT JOIN model_versions gmv ON gmv.id = gp.model_version_id
ORDER BY g.start_time_utc, l.id
"""


def _choose(rows: list[sqlite3.Row]) -> list[sqlite3.Row]:
    groups: dict[tuple[int, int, int], list[sqlite3.Row]] = defaultdict(list)
    for r in rows:
        groups[(r["game_id"], r["market_id"], r["player_id"] or 0)].append(r)
    out = []
    for g in groups.values():
        leans = [r for r in g if r["side"] != "none"]
        out.append(max(leans, key=lambda r: _side_ev(r) or -9) if leans else g[0])
    return out


def _side_ev(r: sqlite3.Row) -> float | None:
    v = r["ev_over"] if r["side"] in OVER else r["ev_under"]
    return float(v) if v is not None else None


def _scores(p: list[float], y: list[int]) -> dict[str, float]:
    brier = statistics.fmean((pi - yi) ** 2 for pi, yi in zip(p, y, strict=True))
    ll = -statistics.fmean(
        math.log(min(max(pi, EPS), 1 - EPS)) if yi else math.log(1 - min(max(pi, EPS), 1 - EPS))
        for pi, yi in zip(p, y, strict=True)
    )
    return {"brier": round(brier, 5), "log_loss": round(ll, 5)}


def reliability(p: list[float], y: list[int], bins: int = 10) -> tuple[list[dict[str, Any]], float | None]:
    if not p:
        return [], None
    table = []
    ece = 0.0
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        idx = [i for i, pi in enumerate(p) if lo <= pi < hi or (b == bins - 1 and pi == 1.0)]
        if not idx:
            continue
        mp = statistics.fmean(p[i] for i in idx)
        hr = statistics.fmean(y[i] for i in idx)
        ece += len(idx) / len(p) * abs(mp - hr)
        table.append({"lo": lo, "hi": hi, "n": len(idx), "mean_p": round(mp, 4), "hit_rate": round(hr, 4)})
    return table, round(ece, 4)


def calibration(rows: list[sqlite3.Row]) -> dict[str, Any]:
    scored = [r for r in rows if r["result"] not in ("push", "void")]
    p = [float(r["p_model_over"]) for r in scored]
    y = [1 if r["result"] in OVER else 0 for r in scored]
    out: dict[str, Any] = {"n": len(scored)}
    if not scored:
        return out
    out["model"] = _scores(p, y)
    both = [(float(r["p_novig_over"]), pi, yi) for r, pi, yi in zip(scored, p, y, strict=True) if r["p_novig_over"]]
    out["n_vs_market"] = len(both)
    if both:
        out["model_same_rows"] = _scores([b[1] for b in both], [b[2] for b in both])
        out["market"] = _scores([b[0] for b in both], [b[2] for b in both])
    out["reliability"], out["ece"] = reliability(p, y)
    out["mean_p"] = round(statistics.fmean(p), 4)
    out["hit_rate"] = round(statistics.fmean(y), 4)
    return out


def record(bets: list[sqlite3.Row]) -> dict[str, Any]:
    wins = sum(r["outcome"] == "win" for r in bets)
    losses = sum(r["outcome"] == "loss" for r in bets)
    pushes = sum(r["outcome"] == "push" for r in bets)
    settled = [r for r in bets if r["outcome"] in ("win", "loss", "push")]
    profit = sum(float(r["profit_units"]) for r in settled)
    out: dict[str, Any] = {"n": len(settled), "wins": wins, "losses": losses, "pushes": pushes}
    out["profit"] = round(profit, 3)
    out["roi"] = round(profit / len(settled), 4) if settled else None
    if len(settled) >= 2:
        sd = statistics.stdev(float(r["profit_units"]) for r in settled)
        half = 1.96 * sd / math.sqrt(len(settled))
        out["roi_ci"] = [round(profit / len(settled) - half, 4), round(profit / len(settled) + half, 4)]
    clvs = [float(r["clv"]) for r in settled if r["clv"] is not None]
    out["clv_n"] = len(clvs)
    out["avg_clv"] = round(statistics.fmean(clvs), 4) if clvs else None
    out["beat_close"] = round(sum(c > 0 for c in clvs) / len(clvs), 4) if clvs else None
    evs = [e for e in (_side_ev(r) for r in settled) if e is not None]
    out["expected_roi"] = round(statistics.fmean(evs), 4) if evs else None
    return out


def _series(bets: list[sqlite3.Row]) -> tuple[list[dict[str, Any]], float]:
    by_day: dict[str, float] = defaultdict(float)
    n_day: dict[str, int] = defaultdict(int)
    for r in bets:
        if r["outcome"] in ("win", "loss", "push"):
            by_day[r["game_date"]] += float(r["profit_units"])
            n_day[r["game_date"]] += 1
    cum = peak = 0.0
    worst = 0.0
    out = []
    for d in sorted(by_day):
        cum += by_day[d]
        peak = max(peak, cum)
        worst = min(worst, cum - peak)
        out.append({"date": d, "profit": round(by_day[d], 3), "cumulative": round(cum, 3), "bets": n_day[d]})
    return out, round(worst, 3)


def _bucket(conf: int | None) -> str:
    for lo, hi, label in CONF_BUCKETS:
        if conf is not None and lo <= conf <= hi:
            return label
    return "none"


def _split(bets: list[sqlite3.Row], key: Any) -> list[dict[str, Any]]:
    groups: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for r in bets:
        groups[key(r)].append(r)
    return [{"key": k} | record(v) for k, v in groups.items()]


def _monotonic(buckets: list[dict[str, Any]]) -> bool | None:
    order = [label for *_, label in CONF_BUCKETS]
    rows = sorted((b for b in buckets if b["key"] in order), key=lambda b: order.index(b["key"]))
    big = [b for b in rows if b["n"] >= MIN_BUCKET]
    if len(big) < 2:
        return None
    # ROI, not win rate: bets in different buckets are at very different prices.
    rois = [b["roi"] for b in big]
    return all(a <= b + 1e-9 for a, b in pairwise(rois))


def _recent(r: sqlite3.Row) -> dict[str, Any]:
    side = r["side"]
    return {
        "prediction_id": r["id"],
        "date": r["game_date"],
        "game": f"{r['away']} @ {r['home']}",
        "subject": r["full_name"] or f"{r['away']} @ {r['home']}",
        "player_id": r["nhl_player_id"],
        "market_label": r["market_name"],
        "line": r["line"],
        "side": side,
        "price": r["over_price"] if side in OVER else r["under_price"],
        "book_name": r["book_name"],
        "confidence": r["confidence"],
        "p_model": round(float(r["p_model_over"] if side in OVER else r["p_model_under"]), 4),
        "actual": r["actual_value"],
        "outcome": r["outcome"],
        "profit": r["profit_units"],
        "clv": r["clv"],
        "void_reason": VOID_REASONS.get(r["void_reason"] or "", r["void_reason"]),
    }


def performance(conn: sqlite3.Connection, now: datetime) -> dict[str, Any]:
    rows = _choose(conn.execute(SQL).fetchall())
    bets = [r for r in rows if r["side"] != "none"]
    settled = [r for r in bets if r["outcome"] != "void"]
    series, drawdown = _series(settled)
    order = [label for *_, label in CONF_BUCKETS] + ["none"]
    by_conf = sorted(_split(settled, lambda r: _bucket(r["confidence"])), key=lambda b: order.index(b["key"]))
    voids: dict[str, int] = defaultdict(int)
    for r in rows:
        if r["result"] == "void":
            voids[VOID_REASONS.get(r["void_reason"] or "", r["void_reason"] or "void")] += 1
    first = rows[0]["game_date"] if rows else None
    last = rows[-1]["game_date"] if rows else None
    return {
        "generated_at": iso(now),
        "lineup_mode": "live_tracked",
        "period": {"from": first, "to": last} if rows else None,
        "graded": len(rows),
        "synthetic": any(r["provenance"] == "synthetic" for r in rows),
        "min_bets": MIN_BETS,
        "calibration": calibration(rows),
        "bets": record(settled),
        "series": series,
        "max_drawdown": drawdown,
        "by_market": sorted(_split(settled, lambda r: r["market_name"]), key=lambda b: -b["n"]),
        "by_confidence": by_conf,
        "confidence_monotonic": _monotonic(by_conf),
        "by_month": sorted(_split(settled, lambda r: r["game_date"][:7]), key=lambda b: b["key"]),
        "by_version": sorted(_split(settled, lambda r: r["model_version"] or "?"), key=lambda b: b["key"]),
        "voids": dict(voids),
        "recent": [_recent(r) for r in reversed(bets[-50:])],
    }


def track_records(conn: sqlite3.Connection) -> dict[int, float]:
    """Per market id: realized ROI / expected ROI of its graded leans, once it has TRACK_MIN of them.
    Confidence multiplies its edge-strength part by this (clamped to 0.5-1.2)."""
    by: dict[int, list[sqlite3.Row]] = defaultdict(list)
    for r in _choose(conn.execute(SQL).fetchall()):
        if r["side"] != "none" and r["outcome"] in ("win", "loss", "push") and _side_ev(r) is not None:
            by[r["market_id"]].append(r)
    out = {}
    for market, rows in by.items():
        if len(rows) < TRACK_MIN:
            continue
        expected = statistics.fmean(_side_ev(r) or 0.0 for r in rows)
        if expected > 0:
            out[market] = statistics.fmean(float(r["profit_units"]) for r in rows) / expected
    return out
