"""Model vs market: price every open line that has a current projection, and freeze the result.

For each (projection, line at a book): the model's probability at that line (pushes handled),
the no-vig market probability, edge, expected value at the offered price, a side only when
the edge clears the configured minimum *and* EV > 0 *and* data quality is good enough, a
confidence score with its breakdown, and the step-by-step calculation. Rows are written to
`predictions` as published (immutable) and only when the projection or the price changed.
"""

from __future__ import annotations

import json
import sqlite3
import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from rinkx.config import REPO_ROOT
from rinkx.grading.performance import track_records
from rinkx.ingestion.runs import ingestion_run, register_source
from rinkx.models.project import MODELS_SOURCE
from rinkx.pricing import confidence as conf
from rinkx.pricing.model_probs import AtLine, at_line, yes_no
from rinkx.pricing.odds import NoVig, devig, ev_per_unit, implied
from rinkx.timeutil import iso

PRICING_FILE = REPO_ROOT / "config/pricing.yml"
SIDES = {"over_under": ("over", "under"), "yes_no": ("yes", "no"), "moneyline": ("home", "away")}


@dataclass(frozen=True)
class PricingConfig:
    min_edge: float
    dq_floor: float
    implausible_edge: float

    @staticmethod
    def load(path: Path = PRICING_FILE) -> PricingConfig:
        d: dict[str, Any] = yaml.safe_load(path.read_text())
        return PricingConfig(
            float(d["min_edge_pts"]) / 100, float(d["data_quality_floor"]), float(d["implausible_edge_pts"]) / 100
        )


@dataclass
class Priced:
    p_over: float  # model, conditional on no push
    p_under: float
    p_push: float
    novig: NoVig | None
    implied_over: float | None
    implied_under: float | None
    edge_over: float | None
    edge_under: float | None
    ev_over: float | None
    ev_under: float | None
    side: str
    calculation: list[str]


def _fmt_implied(price: int) -> str:
    return f"{-price}/{-price + 100}" if price < 0 else f"100/{price + 100}"


def price_line(
    kind: str,
    at: AtLine,
    line: float | None,
    over: int | None,
    under: int | None,
    data_quality: float,
    cfg: PricingConfig,
    label: str,
) -> Priced:
    s_over, s_under = SIDES[kind]
    p_o = at.over_given_no_push
    p_u = 1 - p_o
    calc: list[str] = []
    io = implied(over) if over is not None else None
    iu = implied(under) if under is not None else None
    nv = devig(over, under) if over is not None and under is not None else None
    if io is not None and over is not None:
        calc.append(f"Implied({over:+d}) = {_fmt_implied(over)} = {io:.4f}")
    if iu is not None and under is not None:
        calc.append(f"Implied({under:+d}) = {_fmt_implied(under)} = {iu:.4f}")
    if nv is not None:
        calc.append(f"Overround = {nv.overround:.4f}")
        calc.append(f"No-vig {s_over} ({nv.method}) = {nv.over:.4f}; Shin method would give {nv.shin_over:.4f}")
    elif io is not None:
        calc.append(
            f"Only the {s_over} side is offered: margin can't be removed; comparing to the raw implied "
            f"{io:.4f} (includes the margin, so the edge is understated)"
        )
    where = f" {line:g}" if line is not None else ""
    calc.append(
        f"Model P({s_over}{where} {label}) = {at.p_over:.4f}"
        + (f"; P(push) = {at.p_push:.4f}, so {p_o:.4f} without pushes" if at.p_push > 0 else "")
    )
    m_over = nv.over if nv else io
    m_under = nv.under if nv else iu
    e_o = p_o - m_over if m_over is not None else None
    e_u = p_u - m_under if m_under is not None else None
    ev_o = ev_per_unit(at.p_over, at.p_under, over) if over is not None else None
    ev_u = ev_per_unit(at.p_under, at.p_over, under) if under is not None else None
    if e_o is not None:
        calc.append(
            f"Edge {s_over} = {p_o:.4f} - {m_over:.4f} = {e_o * 100:+.1f} pts; EV at {over:+d} = "
            f"{ev_o:+.3f} units per unit staked"
        )
    if e_u is not None:
        calc.append(
            f"Edge {s_under} = {p_u:.4f} - {m_under:.4f} = {e_u * 100:+.1f} pts; EV at {under:+d} = "
            f"{ev_u:+.3f} units per unit staked"
        )
    cands = [
        (ev, side)
        for side, e, ev in ((s_over, e_o, ev_o), (s_under, e_u, ev_u))
        if e is not None and ev is not None and e >= cfg.min_edge and ev > 0
    ]
    side = "none"
    if cands and data_quality >= cfg.dq_floor:
        side = max(cands)[1]
        calc.append(f"Lean: {side} (edge ≥ {cfg.min_edge * 100:g} pts, EV > 0, data quality ≥ {cfg.dq_floor:.0%})")
    else:
        why = "data quality below the floor" if cands else f"no side has edge ≥ {cfg.min_edge * 100:g} pts with EV > 0"
        calc.append(f"No lean: {why}")
    return Priced(p_o, p_u, at.p_push, nv, io, iu, e_o, e_u, ev_o, ev_u, side, calc)


# ---- database ---------------------------------------------------------------------------------


def _toi_stats(conn: sqlite3.Connection, player_id: int, before: str) -> tuple[float | None, float | None]:
    tois = [
        r[0]
        for r in conn.execute(
            "SELECT s.toi_s FROM player_game_stats s JOIN games g ON g.id = s.game_id WHERE s.player_id = ? "
            "AND g.game_date < ? AND s.toi_s IS NOT NULL ORDER BY g.start_time_utc DESC LIMIT 20",
            (player_id, before),
        )
    ]
    if len(tois) < 5:
        return None, None
    last5 = tois[:5]
    mean5 = statistics.fmean(last5)
    cv = statistics.pstdev(last5) / mean5 if mean5 else None
    prior = tois[5:]
    change = (mean5 / statistics.fmean(prior) - 1) if len(prior) >= 5 and statistics.fmean(prior) else None
    return cv, change


def _moved_against(conn: sqlite3.Connection, line_id: int, side_over: bool, now: datetime) -> float | None:
    """How many points our side's implied probability fell over the last 24 h (positive = against us)."""
    rows = conn.execute(
        "SELECT over_price, under_price FROM line_movements WHERE prop_line_id = ? AND observed_at >= ? "
        "AND status = 'open' ORDER BY observed_at",
        (line_id, iso(now - timedelta(hours=24))),
    ).fetchall()
    prices = [r[0] if side_over else r[1] for r in rows]
    prices = [p for p in prices if p is not None]
    if len(prices) < 2:
        return None
    return (implied(prices[0]) - implied(prices[-1])) * 100


def _book_novigs(rows: list[sqlite3.Row], line: float | None, over_side: bool) -> list[float]:
    out = []
    for r in rows:
        if r["line"] == line and r["over_price"] is not None and r["under_price"] is not None:
            nv = devig(r["over_price"], r["under_price"])
            out.append(nv.over if over_side else nv.under)
    return out


def _latest_same(conn: sqlite3.Connection, line_id: int, col: str, proj_id: int, r: sqlite3.Row) -> bool:
    last = conn.execute(
        f"SELECT {col}, line, over_price, under_price FROM predictions WHERE prop_line_id = ? ORDER BY id DESC LIMIT 1",
        (line_id,),
    ).fetchone()
    return last is not None and tuple(last) == (proj_id, r["line"], r["over_price"], r["under_price"])


def _insert(
    conn: sqlite3.Connection,
    *,
    proj_col: str,
    proj_id: int,
    r: sqlite3.Row,
    game_id: int,
    market_id: int,
    player_id: int | None,
    pr: Priced,
    c: conf.Confidence | None,
    now: datetime,
) -> None:
    conn.execute(
        f"INSERT INTO predictions ({proj_col}, prop_line_id, game_id, market_id, sportsbook_id, player_id, line, "
        "over_price, under_price, p_model_over, p_model_under, p_push, p_implied_over, p_implied_under, "
        "p_novig_over, p_novig_under, devig_method, edge_over, edge_under, ev_over, ev_under, side, confidence, "
        "confidence_parts, calculation, created_at, is_published) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)",
        (
            proj_id,
            r["line_id"],
            game_id,
            market_id,
            r["sportsbook_id"],
            player_id,
            r["line"],
            r["over_price"],
            r["under_price"],
            round(pr.p_over, 6),
            round(1 - round(pr.p_over, 6), 6),
            round(pr.p_push, 6),
            pr.implied_over,
            pr.implied_under,
            pr.novig.over if pr.novig else None,
            pr.novig.under if pr.novig else None,
            pr.novig.method if pr.novig else None,
            pr.edge_over,
            pr.edge_under,
            pr.ev_over,
            pr.ev_under,
            pr.side,
            c.score if c else None,
            json.dumps(c.to_json() if c else {}),
            json.dumps(pr.calculation),
            iso(now),
        ),
    )


def _score(
    conn: sqlite3.Connection,
    pr: Priced,
    kind: str,
    rows: list[sqlite3.Row],
    r: sqlite3.Row,
    cfg: PricingConfig,
    now: datetime,
    *,
    inputs: dict[str, Any],
    missing: list[str],
    dq: float,
    games: int,
    player_id: int | None,
    game_date: str,
    is_goalie: bool,
    is_game: bool,
    track_record: float | None = None,
) -> conf.Confidence:
    s_over = SIDES[kind][0]
    if pr.side != "none":
        over_side = pr.side == s_over
    else:  # no lean: score the side with the larger edge
        e_o = pr.edge_over if pr.edge_over is not None else -1.0
        e_u = pr.edge_under if pr.edge_under is not None else -1.0
        over_side = e_o >= e_u
    side_edge = pr.edge_over if over_side else pr.edge_under
    edge = side_edge if side_edge is not None else 0.0
    p_model = pr.p_over if over_side else pr.p_under
    cv = change = None
    if player_id is not None and not is_goalie:
        cv, change = _toi_stats(conn, player_id, game_date)
    return conf.score(
        conf.Inputs(
            edge=edge,
            p_model=p_model,
            games_in_history=games,
            data_quality=dq,
            book_novigs=_book_novigs(rows, r["line"], over_side),
            one_sided=r["under_price"] is None,
            moved_against_pts=_moved_against(conn, r["line_id"], over_side, now),
            is_goalie_prop=is_goalie,
            is_game_market=is_game,
            start_probability=inputs.get("start_probability"),
            start_confirmed=inputs.get("start_status") == "confirmed",
            opp_goalie_confirmed="goalie_unconfirmed" not in missing,
            toi_cv=cv,
            role_change=change,
            implausible_edge=cfg.implausible_edge,
            track_record=track_record,
        )
    )


PLAYER_SQL = """
SELECT pp.id AS proj_id, pp.game_id, pp.player_id, pp.market_id, pp.pmf, pp.data_quality, pp.inputs,
       pp.missing_inputs, m.code AS market, m.kind, m.name AS market_name, pl.position, g.game_date,
       l.id AS line_id, l.sportsbook_id, l.line, l.over_price, l.under_price
FROM player_projections pp
JOIN markets m ON m.id = pp.market_id JOIN players pl ON pl.id = pp.player_id JOIN games g ON g.id = pp.game_id
JOIN prop_lines l ON l.game_id = pp.game_id AND l.player_id = pp.player_id AND l.market_id = pp.market_id
JOIN sportsbooks b ON b.id = l.sportsbook_id AND b.is_enabled = 1
WHERE pp.is_current = 1 AND l.status = 'open' AND l.is_main_line = 1
  AND g.status IN ('scheduled','pregame') AND g.start_time_utc > ?
"""

GAME_SQL = """
SELECT gp.id AS proj_id, gp.game_id, gp.market_id, gp.pmf, gp.inputs, m.code AS market, m.kind, m.name AS market_name,
       g.game_date, l.id AS line_id, l.sportsbook_id, l.line, l.over_price, l.under_price
FROM game_projections gp
JOIN markets m ON m.id = gp.market_id JOIN games g ON g.id = gp.game_id
JOIN prop_lines l ON l.game_id = gp.game_id AND l.market_id = gp.market_id AND l.player_id IS NULL
JOIN sportsbooks b ON b.id = l.sportsbook_id AND b.is_enabled = 1
WHERE gp.is_current = 1 AND l.status = 'open' AND l.is_main_line = 1
  AND ((m.code = 'game_moneyline' AND gp.side = 'home') OR (m.code = 'game_total' AND gp.side = 'game'))
  AND g.status IN ('scheduled','pregame') AND g.start_time_utc > ?
"""


def run_pricing(conn: sqlite3.Connection, now: datetime, cfg: PricingConfig | None = None) -> int:
    """Price every open line with a current projection; returns predictions written."""
    cfg = cfg or PricingConfig.load()
    src = register_source(conn, MODELS_SOURCE)
    written = 0
    tracks = track_records(conn)
    with ingestion_run(conn, src, "pricing") as run:
        prows = conn.execute(PLAYER_SQL, (iso(now),)).fetchall()
        groups: dict[tuple[int, int, int], list[sqlite3.Row]] = {}
        for r in prows:
            groups.setdefault((r["game_id"], r["player_id"], r["market_id"]), []).append(r)
        for rows in groups.values():
            for r in rows:
                if _latest_same(conn, r["line_id"], "projection_id", r["proj_id"], r):
                    continue
                pmf = json.loads(r["pmf"])["p"]
                kind = r["kind"]
                if kind == "over_under" and r["line"] is None:
                    continue
                at = at_line(pmf, r["line"]) if kind == "over_under" else yes_no(pmf)
                pr = price_line(
                    kind,
                    at,
                    r["line"],
                    r["over_price"],
                    r["under_price"],
                    r["data_quality"],
                    cfg,
                    r["market_name"].lower(),
                )
                inputs = json.loads(r["inputs"])
                games = int(inputs.get("games_in_history") or inputs.get("starts_in_history") or 0)
                c = _score(
                    conn,
                    pr,
                    kind,
                    rows,
                    r,
                    cfg,
                    now,
                    inputs=inputs,
                    missing=json.loads(r["missing_inputs"]),
                    dq=r["data_quality"],
                    games=games,
                    player_id=r["player_id"],
                    game_date=r["game_date"],
                    is_goalie=r["position"] == "G",
                    is_game=False,
                    track_record=tracks.get(r["market_id"]),
                )
                _insert(
                    conn,
                    proj_col="projection_id",
                    proj_id=r["proj_id"],
                    r=r,
                    game_id=r["game_id"],
                    market_id=r["market_id"],
                    player_id=r["player_id"],
                    pr=pr,
                    c=c,
                    now=now,
                )
                written += 1
        grows = conn.execute(GAME_SQL, (iso(now),)).fetchall()
        ggroups: dict[tuple[int, int], list[sqlite3.Row]] = {}
        for r in grows:
            ggroups.setdefault((r["game_id"], r["market_id"]), []).append(r)
        for rows in ggroups.values():
            for r in rows:
                if _latest_same(conn, r["line_id"], "game_projection_id", r["proj_id"], r):
                    continue
                pmf = json.loads(r["pmf"])["p"]
                if r["market"] == "game_moneyline":
                    p_home = pmf[1]
                    at = AtLine(p_home, 1 - p_home, 0.0)
                    kind = "moneyline"
                else:
                    if r["line"] is None:
                        continue
                    at = at_line(pmf, r["line"])
                    kind = "over_under"
                pr = price_line(
                    kind,
                    at,
                    r["line"],
                    r["over_price"],
                    r["under_price"],
                    1.0,
                    cfg,
                    "home win" if kind == "moneyline" else "total goals",
                )
                c = _score(
                    conn,
                    pr,
                    kind,
                    rows,
                    r,
                    cfg,
                    now,
                    inputs=json.loads(r["inputs"]),
                    missing=[],
                    dq=1.0,
                    games=82,
                    player_id=None,
                    game_date=r["game_date"],
                    is_goalie=False,
                    is_game=True,
                    track_record=tracks.get(r["market_id"]),
                )
                _insert(
                    conn,
                    proj_col="game_projection_id",
                    proj_id=r["proj_id"],
                    r=r,
                    game_id=r["game_id"],
                    market_id=r["market_id"],
                    player_id=None,
                    pr=pr,
                    c=c,
                    now=now,
                )
                written += 1
        run.rows_read = len(prows) + len(grows)
        run.rows_upserted = written
    return written
