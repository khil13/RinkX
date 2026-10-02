"""Grade frozen predictions once their game is over (Phase 7).

Every prediction for a finished game gets a `model_results` row: the actual stat, which side won
at that line, the outcome and profit of its lean (if it had one), the closing price at the same
line, and closing-line value. Games finished in the last REGRADE_DAYS are graded again on each
run, so late NHL stat corrections flow through; older results are left alone.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from rinkx.grading import settle as st
from rinkx.ingestion.runs import ingestion_run, register_source
from rinkx.models.project import MODELS_SOURCE
from rinkx.pricing.odds import devig
from rinkx.timeutil import iso, parse_iso

REGRADE_DAYS = 3

SQL = """
SELECT p.id, p.prop_line_id, p.game_id, p.player_id, p.line, p.over_price, p.under_price, p.side,
       m.code AS market, m.kind,
       g.status AS game_status, g.start_time_utc, g.game_date, g.home_score, g.away_score, g.ended_in
FROM predictions p
JOIN markets m ON m.id = p.market_id
JOIN games g ON g.id = p.game_id
LEFT JOIN model_results r ON r.prediction_id = p.id
WHERE g.status IN ('final','cancelled','postponed')
  AND (r.prediction_id IS NULL OR g.game_date >= ?)
ORDER BY p.id
"""


@dataclass(frozen=True)
class Closing:
    line: float | None
    over: int | None
    under: int | None
    at: str
    novig_over: float | None


def closing(conn: sqlite3.Connection, prop_line_id: int | None, start: str) -> Closing | None:
    """The last open price at this line before the game started."""
    if prop_line_id is None:
        return None
    r = conn.execute(
        "SELECT observed_at, line, over_price, under_price FROM line_movements WHERE prop_line_id = ? "
        "AND observed_at <= ? AND status = 'open' ORDER BY observed_at DESC LIMIT 1",
        (prop_line_id, start),
    ).fetchone()
    if r is None:
        return None
    nv = devig(r[2], r[3]).over if r[2] is not None and r[3] is not None else None
    return Closing(r[1], r[2], r[3], r[0], nv)


def _stats_loaded(conn: sqlite3.Connection, game_id: int, table: str) -> bool:
    return conn.execute(f"SELECT 1 FROM {table} WHERE game_id = ? LIMIT 1", (game_id,)).fetchone() is not None


def actual(conn: sqlite3.Connection, r: sqlite3.Row, now: datetime) -> st.Settled | None:
    """Settle one prediction; None while it can't be settled yet (stats not loaded)."""
    status, market, kind, line = r["game_status"], r["market"], r["kind"], r["line"]
    if status == "cancelled":
        return st.void("game_cancelled")
    if status == "postponed":
        late = now - parse_iso(r["start_time_utc"]) >= timedelta(hours=st.POSTPONED_VOID_HOURS)
        return st.void("game_postponed") if late else None
    if r["home_score"] is None or r["away_score"] is None:
        return None
    gid, pid = r["game_id"], r["player_id"]

    if market == "game_moneyline":
        return st.settle(kind, line, 1.0 if r["home_score"] > r["away_score"] else 0.0)
    if market == "game_total":
        if line is None:
            return None
        return st.settle(kind, line, float(r["home_score"] + r["away_score"]))  # includes the shootout goal

    if market in st.SKATER_STAT or market in st.SKATER_YES or market == "skater_first_goal":
        if not _stats_loaded(conn, gid, "player_game_stats"):
            return None
        row = conn.execute(
            "SELECT toi_s, shots, goals, assists, points, pp_points, pp_goals, pp_assists, blocked_shots, hits "
            "FROM player_game_stats WHERE game_id = ? AND player_id = ?",
            (gid, pid),
        ).fetchone()
        if row is None or (row["toi_s"] is not None and row["toi_s"] <= 0):
            return st.void("did_not_play")
        if market == "skater_first_goal":
            goals = r["home_score"] + r["away_score"] - (1 if r["ended_in"] == "SO" else 0)
            first = conn.execute(
                "SELECT shooter_id FROM pbp_shot_events WHERE game_id = ? AND event_type = 'goal' "
                "ORDER BY period, period_seconds, event_idx LIMIT 1",
                (gid,),
            ).fetchone()
            if first is None and goals > 0:
                return None  # play-by-play not loaded yet
            return st.settle(kind, None, 1.0 if first is not None and first[0] == pid else 0.0)
        if market in st.SKATER_YES:
            v = row[st.SKATER_YES[market]]
            return None if v is None else st.settle(kind, None, 1.0 if v >= 1 else 0.0)
        v = row[st.SKATER_STAT[market]]
        return None if v is None or line is None else st.settle(kind, line, float(v))

    if market in st.GOALIE_STAT or market in st.GOALIE_YES:
        if not _stats_loaded(conn, gid, "goalie_game_stats"):
            return None
        row = conn.execute(
            "SELECT started, saves, goals_against, decision, shutout FROM goalie_game_stats "
            "WHERE game_id = ? AND player_id = ?",
            (gid, pid),
        ).fetchone()
        if row is None or not row["started"]:
            return st.void("goalie_did_not_start")
        if market == "goalie_win":
            return st.settle(kind, None, 1.0 if row["decision"] == "W" else 0.0)
        if market == "goalie_shutout":
            return None if row["shutout"] is None else st.settle(kind, None, float(row["shutout"]))
        v = row[st.GOALIE_STAT[market]]
        return None if v is None or line is None else st.settle(kind, line, float(v))
    return None  # not a market we grade (e.g. saves + win)


def _price_for(side: str, over: int | None, under: int | None) -> int | None:
    return over if side in ("over", "yes", "home") else under


def run_grading(conn: sqlite3.Connection, now: datetime, today: date) -> int:
    """Grade predictions for finished games; returns rows written."""
    src = register_source(conn, MODELS_SOURCE)
    written = 0
    with ingestion_run(conn, src, "grading") as run:
        rows = conn.execute(SQL, ((today - timedelta(days=REGRADE_DAYS)).isoformat(),)).fetchall()
        pending = 0
        for r in rows:
            s = actual(conn, r, now)
            if s is None:
                pending += 1
                continue
            side = r["side"]
            price = _price_for(side, r["over_price"], r["under_price"]) if side != "none" else None
            g = st.grade_lean(side, s, price)
            c = closing(conn, r["prop_line_id"], r["start_time_utc"])
            value = None
            if c is not None and side != "none" and c.novig_over is not None:
                value = st.clv(c.novig_over if side in ("over", "yes", "home") else 1 - c.novig_over, price)
            conn.execute(
                "INSERT OR REPLACE INTO model_results (prediction_id, actual_value, result, void_reason, outcome, "
                "profit_units, closing_line, closing_over_price, closing_under_price, closing_at, closing_novig_p, "
                "clv, graded_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    r["id"],
                    s.actual,
                    s.result,
                    s.void_reason,
                    g.outcome,
                    g.profit,
                    c.line if c else None,
                    c.over if c else None,
                    c.under if c else None,
                    c.at if c else None,
                    c.novig_over if c else None,
                    value,
                    iso(now),
                ),
            )
            written += 1
        run.rows_read = len(rows)
        run.rows_upserted = written
        run.meta["waiting_for_stats"] = pending
    return written
