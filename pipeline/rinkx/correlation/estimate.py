"""Empirical prop correlations (Phase 8), for the parlay builder.

For each player appearance, a stat's *standardized residual* is (actual - his average so far) /
sqrt(average so far). Only earlier games are used, so the player's typical level is taken out and
only the game-to-game co-movement remains. Pearson correlations of those residuals are pooled for:

  same_player   two stats of the same skater in the same game (shots & points ...)
  same_team     a stat of one skater and a stat of a teammate
  opponent      a stat of one skater and a stat of a skater on the other team
  opp_goalie    a skater stat and the opposing starting goalie's saves / goals against

Teammate and opponent pairs are summed in closed form per team-game, so a full season takes
seconds. `n_obs` is the number of independent units behind each estimate: player-games for
same_player, team-games for the others (pairs within a game are not independent). The 95%
interval is Fisher's z with that n. Correlations are never assumed: the parlay builder treats a
pair with too little data as independent and says so.
"""

from __future__ import annotations

import math
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from itertools import combinations_with_replacement

from rinkx.ingestion.runs import ingestion_run, register_source
from rinkx.models import history
from rinkx.models.project import MODELS_SOURCE
from rinkx.timeutil import iso, parse_iso

SKATER = {
    "skater_shots_on_goal": "shots",
    "skater_goals": "goals",
    "skater_assists": "assists",
    "skater_points": "points",
    "skater_pp_points": "pp_points",
    "skater_blocked_shots": "blocks",
    "skater_hits": "hits",
}
GOALIE = {"goalie_saves": "saves", "goalie_goals_against": "ga"}
MIN_PRIOR = 5  # earlier games a player needs before his residuals count
FLOOR = 0.05  # keeps rare stats from blowing up when standardized
MAX_AGE = timedelta(hours=20)
MIN_N = 300  # below this the parlay builder treats a pair as independent


@dataclass
class Acc:
    """Running sums for a pooled Pearson correlation."""

    n: float = 0.0
    sx: float = 0.0
    sy: float = 0.0
    sxx: float = 0.0
    syy: float = 0.0
    sxy: float = 0.0
    units: int = 0

    def add(self, n: float, sx: float, sy: float, sxx: float, syy: float, sxy: float) -> None:
        self.n += n
        self.sx += sx
        self.sy += sy
        self.sxx += sxx
        self.syy += syy
        self.sxy += sxy

    def rho(self) -> float | None:
        if self.n < 2:
            return None
        vx = self.sxx / self.n - (self.sx / self.n) ** 2
        vy = self.syy / self.n - (self.sy / self.n) ** 2
        if vx <= 0 or vy <= 0:
            return None
        cov = self.sxy / self.n - (self.sx / self.n) * (self.sy / self.n)
        return max(-1.0, min(1.0, cov / math.sqrt(vx * vy)))


@dataclass
class _Running:
    n: int = 0
    sums: dict[str, float] = field(default_factory=lambda: defaultdict(float))


def _resid(run: _Running, stats: dict[str, float | None], names: dict[str, str]) -> dict[str, float] | None:
    """Standardized residuals vs the player's average so far (None while he has too few games)."""
    if run.n < MIN_PRIOR:
        return None
    out = {}
    for market, stat in names.items():
        y = stats.get(stat)
        if y is None:
            continue
        m = run.sums[stat] / run.n
        out[market] = (y - m) / math.sqrt(max(m, FLOOR))
    return out


@dataclass
class Estimate:
    a: str
    b: str
    relation: str
    rho: float
    lo: float
    hi: float
    n: int


def _fisher(rho: float, n: int) -> tuple[float, float]:
    if n <= 3:
        return -1.0, 1.0
    z = math.atanh(max(-0.999999, min(0.999999, rho)))
    h = 1.96 / math.sqrt(n - 3)
    return math.tanh(z - h), math.tanh(z + h)


def estimate(games: list[history.GameRecord]) -> list[Estimate]:
    skater_runs: dict[int, _Running] = defaultdict(_Running)
    goalie_runs: dict[int, _Running] = defaultdict(_Running)
    acc: dict[tuple[str, str, str], Acc] = defaultdict(Acc)
    markets = list(SKATER)
    pairs = list(combinations_with_replacement(markets, 2))

    for g in sorted(games, key=lambda x: x.start):
        if g.home_goals is None:
            continue
        by_team: dict[int, list[dict[str, float]]] = defaultdict(list)
        for s in g.skaters:
            r = _resid(skater_runs[s.player], s.stats, SKATER)
            if r is not None and len(r) == len(SKATER):
                by_team[s.team].append(r)
                for a, b in pairs:
                    if a != b:
                        x, y = r[a], r[b]
                        acc[(a, b, "same_player")].add(1, x, y, x * x, y * y, x * y)
                        acc[(a, b, "same_player")].units += 1
        goalie_r: dict[int, dict[str, float]] = {}
        for gl in g.goalies:
            if gl.started:
                r = _resid(goalie_runs[gl.player], {"saves": gl.saves, "ga": gl.ga}, GOALIE)
                if r is not None and len(r) == len(GOALIE):
                    goalie_r[gl.team] = r

        # Teammates: sum over ordered pairs i != j of x_i * y_j = (sum x)(sum y) - sum x_i y_i.
        for team, rows in by_team.items():
            k = len(rows)
            if k >= 2:
                tot = {m: sum(r[m] for r in rows) for m in markets}
                sq = {m: sum(r[m] ** 2 for r in rows) for m in markets}
                for a, b in pairs:
                    cross = tot[a] * tot[b] - sum(r[a] * r[b] for r in rows)
                    e = acc[(a, b, "same_team")]
                    e.add(k * (k - 1), (k - 1) * tot[a], (k - 1) * tot[b], (k - 1) * sq[a], (k - 1) * sq[b], cross)
                    e.units += 1
            opp_team = g.away_team if team == g.home_team else g.home_team
            others = by_team.get(opp_team, [])
            if others and team == g.home_team:  # each game once; both directions added below
                ka, kb = len(rows), len(others)
                ta = {m: sum(r[m] for r in rows) for m in markets}
                tb = {m: sum(r[m] for r in others) for m in markets}
                qa = {m: sum(r[m] ** 2 for r in rows) for m in markets}
                qb = {m: sum(r[m] ** 2 for r in others) for m in markets}
                for a, b in pairs:
                    e = acc[(a, b, "opponent")]
                    # home a with away b, and away a with home b: symmetric in the pair
                    e.add(ka * kb, kb * ta[a], ka * tb[b], kb * qa[a], ka * qb[b], ta[a] * tb[b])
                    e.add(ka * kb, ka * tb[a], kb * ta[b], ka * qb[a], kb * qa[b], tb[a] * ta[b])
                    e.units += 2
            gr = goalie_r.get(opp_team)
            if gr is not None:
                for a in markets:
                    for gm in GOALIE:
                        e = acc[(a, gm, "opp_goalie")]
                        xs = [r[a] for r in rows]
                        gy = gr[gm]
                        e.add(k, sum(xs), k * gy, sum(v * v for v in xs), k * gy * gy, gy * sum(xs))
                        e.units += 1

        # Only now update the running averages: this game never informs its own residuals.
        for s in g.skaters:
            run = skater_runs[s.player]
            if all(s.stats.get(st) is not None for st in SKATER.values()):
                run.n += 1
                for st in SKATER.values():
                    run.sums[st] += float(s.stats[st] or 0)
        for gl in g.goalies:
            if gl.started and gl.saves is not None and gl.ga is not None:
                run = goalie_runs[gl.player]
                run.n += 1
                run.sums["saves"] += gl.saves
                run.sums["ga"] += gl.ga

    out = []
    for (a, b, rel), e in sorted(acc.items()):
        rho = e.rho()
        if rho is None:
            continue
        lo, hi = _fisher(rho, e.units)
        out.append(Estimate(a, b, rel, round(rho, 4), round(lo, 4), round(hi, 4), e.units))
    return out


def run_correlations(conn: sqlite3.Connection, now: datetime, *, force: bool = False) -> int:
    """Re-estimate when the stored estimates are older than MAX_AGE. Returns rows written."""
    last = conn.execute("SELECT max(computed_at) FROM prop_correlations").fetchone()[0]
    if not force and last is not None and now - parse_iso(last) < MAX_AGE:
        return 0
    src = register_source(conn, MODELS_SOURCE)
    ests: list[Estimate] = []  # stays empty if the stage fails (recorded; the run goes on)
    with ingestion_run(conn, src, "correlations") as run:
        games = [g for g in history.load(conn) if g.home_goals is not None]
        ests = estimate(games)
        ids = {r[0]: r[1] for r in conn.execute("SELECT code, id FROM markets")}
        conn.execute("DELETE FROM prop_correlations")
        start = min((g.date for g in games), default=None)
        end = max((g.date for g in games), default=None)
        for e in ests:
            conn.execute(
                "INSERT INTO prop_correlations (market_a_id, market_b_id, relation, coefficient, ci_low, ci_high, "
                "n_obs, method, window_start, window_end, computed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'residual_pearson', ?, ?, ?)",
                (ids[e.a], ids[e.b], e.relation, e.rho, e.lo, e.hi, e.n, start, end, iso(now)),
            )
        run.rows_read = len(games)
        run.rows_upserted = len(ests)
    return len(ests)


def published(conn: sqlite3.Connection, now: datetime) -> dict[str, object]:
    rows = conn.execute(
        "SELECT a.code AS a, b.code AS b, c.relation, c.coefficient, c.ci_low, c.ci_high, c.n_obs, c.method, "
        "c.window_start, c.window_end, c.computed_at FROM prop_correlations c "
        "JOIN markets a ON a.id = c.market_a_id JOIN markets b ON b.id = c.market_b_id "
        "ORDER BY c.relation, a.code, b.code"
    ).fetchall()
    return {
        "generated_at": iso(now),
        "min_n": MIN_N,
        "aliases": {"skater_anytime_goal": "skater_goals"},  # a "Yes" on anytime goal behaves like goals over 0.5
        "pairs": [
            {
                "a": r["a"],
                "b": r["b"],
                "relation": r["relation"],
                "rho": r["coefficient"],
                "ci": [r["ci_low"], r["ci_high"]],
                "n": r["n_obs"],
                "method": r["method"],
            }
            for r in rows
        ],
        "window": {"from": rows[0]["window_start"], "to": rows[0]["window_end"]} if rows else None,
        "computed_at": rows[0]["computed_at"] if rows else None,
    }
