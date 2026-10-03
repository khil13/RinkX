"""backtest.json: every graded pre-game price, for the browser's backtest, CLV and "my bets" pages.

One row per (game, market, subject, book): the last prediction frozen before puck drop (the same
rows Model Performance uses, before it picks one book per prop). The side is the one RinkX scored:
its lean, or the side with the larger edge when it had none. Every value is from before the game,
except the result, profit and closing price, which are what the backtest measures. Columnar, last
BACKTEST_DAYS days, to keep the file small.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from typing import Any

from rinkx.grading.performance import SQL
from rinkx.grading.settle import clv as clv_of
from rinkx.pricing.odds import decimal
from rinkx.timeutil import iso

BACKTEST_DAYS = 365
OVER = ("over", "yes", "home")
UNDER = {"over": "under", "yes": "no", "home": "away"}
FIELDS = (
    "id", "date", "game", "market", "label", "player", "player_id", "book", "line", "side", "lean", "price",
    "p", "p_mkt", "edge", "ev", "conf", "pi", "result", "profit", "close", "clv", "version", "synthetic",
)  # fmt: skip


def _r4(x: float | None) -> float | None:
    return None if x is None else round(x, 4)


def _row(r: sqlite3.Row, pi: int | None, close: sqlite3.Row | None) -> list[Any]:
    lean = r["side"] != "none"
    if lean:
        over = r["side"] in OVER
    else:
        over = (r["edge_over"] if r["edge_over"] is not None else -1) >= (
            r["edge_under"] if r["edge_under"] is not None else -1
        )
    first = {"over_under": "over", "yes_no": "yes", "moneyline": "home"}[r["kind"]]
    side = first if over else UNDER[first]
    price = r["over_price"] if over else r["under_price"]
    p = r["p_model_over"] if over else r["p_model_under"]
    p_mkt = (r["p_novig_over"] if over else r["p_novig_under"]) if r["p_novig_over"] is not None else None
    res = r["result"]
    if res in ("push", "void"):
        result, profit = res, 0.0
    elif price is None:
        result, profit = ("win" if res == side else "loss"), None
    else:
        result = "win" if res == side else "loss"
        profit = round(decimal(price) - 1, 4) if result == "win" else -1.0
    close_price = None if close is None else (close["closing_over_price"] if over else close["closing_under_price"])
    close_p = None
    if close is not None and close["closing_novig_p"] is not None:
        close_p = close["closing_novig_p"] if over else 1 - close["closing_novig_p"]
    value = clv_of(close_p, price)
    return [
        r["id"], r["game_date"], r["nhl_game_id"], r["market"], r["market_name"], r["full_name"], r["nhl_player_id"],
        r["book_name"], r["line"], side, int(lean), price, round(p, 4), None if p_mkt is None else round(p_mkt, 4),
        _r4(r["edge_over"] if over else r["edge_under"]),
        _r4(r["ev_over"] if over else r["ev_under"]),
        r["confidence"], pi, result, profit, close_price, None if value is None else round(value, 4),
        r["model_version"], int(r["provenance"] == "synthetic"),
    ]  # fmt: skip


def published(conn: sqlite3.Connection, now: datetime) -> dict[str, Any]:
    since = (now - timedelta(days=BACKTEST_DAYS)).date().isoformat()
    pis = dict(conn.execute("SELECT prediction_id, intelligence FROM prediction_scores").fetchall())
    rows = []
    for r in conn.execute(SQL).fetchall():
        if r["game_date"] < since:
            continue
        close = conn.execute(
            "SELECT closing_over_price, closing_under_price, closing_novig_p FROM model_results "
            "WHERE prediction_id = ?",
            (r["id"],),
        ).fetchone()
        rows.append(_row(r, pis.get(r["id"]), close))
    return {
        "generated_at": iso(now),
        "since": since,
        "fields": list(FIELDS),
        "rows": rows,
        "about": "Last price frozen before puck drop at each book; side = lean, else the larger edge.",
    }
