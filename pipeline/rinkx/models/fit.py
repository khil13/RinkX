"""Model formulas, hyperparameter tuning, and the walk-forward test that gates publication.

Skater stats (docs/05-models.md §1-4), per game:

    rate:    lambda = shrunk_rate_per_hour x expected_hours x factors
    finish:  lambda = lambda_shots x shrunk_goals_per_shot x factors        (goals only)

    shrunk_rate = (weighted stat + m x league_rate) / (weighted hours + m)

Goalie saves / goals against (§5):

    SA ~ NB(league_SA_per_start x own_defence x opp_offence, size)
    saves | SA ~ Binomial(SA, sv),  GA | SA ~ Binomial(SA, 1 - sv)
    sv = (weighted saves + k x league_sv) / (weighted shots against + k)

Counts are Poisson or negative binomial; the data picks. Every choice (half-life, prior
strength m or k, which factors to keep, dispersion) is tuned on the earlier part of the
history and then scored on the later part it never saw. A stat is published only if it beats
both simple baselines there by more than the noise, and its PIT histogram is close to flat.
"""

from __future__ import annotations

import math
from array import array
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import asdict, dataclass, replace
from typing import Any

import numpy as np
from numpy.typing import NDArray

from rinkx.models import dist, game_sim
from rinkx.models.dist import INF, F
from rinkx.models.history import GOALIE_STATS, SKATER_STATS
from rinkx.models.state import HALF_LIVES, K_SV, H, Row, basis_of

MODEL_VERSION = "1.1"
M_GRID: tuple[float, ...] = (0.25, 0.5, 1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 64.0)  # hours of ice time
FINISH_GRID: tuple[float, ...] = (25.0, 50.0, 100.0, 200.0, 400.0, 800.0)  # shots
BURN_IN_DAYS = 21  # the first weeks of history only build state; they are never scored
TUNE_SHARE = 0.6  # earliest 60% of scored dates tune; the latest 40% are the test
MIN_TEST = {"skater": 3000, "goalie": 150}
BASELINE_FLOOR = 0.02

# Factors each stat may use. The tuner drops any that don't improve the tuning-window score.
SKATER_FACTORS: dict[str, tuple[str, ...]] = {
    "shots": ("opp", "home"),
    "goals": ("opp_shots", "goalie", "home"),
    "assists": ("opp_shots", "goalie", "home"),
    "points": ("opp_shots", "goalie", "home"),
    "pp_points": ("opp", "home"),
    "pp_goals": ("opp", "home"),
    "pp_assists": ("opp", "home"),
    "blocks": ("opp", "home", "venue"),
    "hits": ("opp", "home", "venue"),
}
GOALIE_FACTORS = ("fd", "fo")

FAMILY = {
    "shots": "skater_shots",
    "goals": "skater_scoring",
    "assists": "skater_scoring",
    "points": "skater_scoring",
    "pp_points": "skater_scoring",
    "pp_goals": "skater_scoring",
    "pp_assists": "skater_scoring",
    "blocks": "skater_blocks",
    "hits": "skater_hits",
    "saves": "goalie",
    "goals_against": "goalie",
    "team_goals": "game_sim",
    "win": "game_sim",
    "shutout": "game_sim",
    "first_goal": "game_sim",
}
# Published markets and the stat each is read from (yes/no markets read P(stat >= 1)).
MARKETS: dict[str, str] = {
    "skater_shots_on_goal": "shots",
    "skater_goals": "goals",
    "skater_anytime_goal": "goals",
    "skater_assists": "assists",
    "skater_points": "points",
    "skater_pp_points": "pp_points",
    "skater_pp_goal": "pp_goals",
    "skater_pp_assist": "pp_assists",
    "skater_blocked_shots": "blocks",
    "skater_hits": "hits",
    "goalie_saves": "saves",
    "goalie_goals_against": "goals_against",
    "goalie_win": "win",
    "goalie_shutout": "shutout",
    "skater_first_goal": "first_goal",
}
# Game- and team-level markets (stored in game_projections, not player_projections).
GAME_MARKETS: dict[str, str] = {"game_moneyline": "win", "game_total": "team_goals", "team_total": "team_goals"}
LABELS = {
    "shots": "Shots on goal",
    "goals": "Goals",
    "assists": "Assists",
    "points": "Points",
    "pp_points": "Power-play points",
    "pp_goals": "Power-play goals",
    "pp_assists": "Power-play assists",
    "blocks": "Blocked shots",
    "hits": "Hits",
    "saves": "Saves",
    "goals_against": "Goals against",
    **game_sim.LABELS,
}


@dataclass(frozen=True)
class Choice:
    stat: str
    kind: str  # 'rate' | 'finish' | 'goalie'
    hl: int = 1  # index into HALF_LIVES
    m: float = 4.0  # prior strength (hours, or shots for 'finish')
    factors: tuple[str, ...] = ()
    k_sv: int = 1  # index into K_SV
    size: float = INF  # NB size; inf = Poisson
    sa_size: float = INF  # goalie: NB size of shots against

    def to_json(self) -> dict[str, Any]:
        d = asdict(self)
        d["factors"] = list(self.factors)
        for k in ("size", "sa_size"):
            d[k] = None if math.isinf(d[k]) else d[k]
        d["half_life_games"] = HALF_LIVES[self.hl]
        d["k_sv_shots"] = K_SV[self.k_sv]
        return d

    @staticmethod
    def from_json(d: dict[str, Any]) -> Choice:
        return Choice(
            stat=d["stat"],
            kind=d["kind"],
            hl=int(d["hl"]),
            m=float(d["m"]),
            factors=tuple(d["factors"]),
            k_sv=int(d["k_sv"]),
            size=INF if d["size"] is None else float(d["size"]),
            sa_size=INF if d["sa_size"] is None else float(d["sa_size"]),
        )


Cols = dict[str, F]


@dataclass
class Table:
    """Backtest rows of one kind as columns: features, plus `y.<stat>` actuals (NaN = unknown)."""

    dates: NDArray[np.str_]
    cols: Cols

    def __len__(self) -> int:
        return len(self.dates)


def collect(rows: Iterable[Row]) -> dict[str, Table]:
    """Stream rows into compact float arrays (a full feature dict per row would not fit in memory)."""
    data: dict[str, dict[str, array[float]]] = {}
    dates: dict[str, list[str]] = {}
    for r in rows:
        cols = data.get(r.kind)
        if cols is None:
            cols = data[r.kind] = defaultdict(lambda: array("d"))
            dates[r.kind] = []
        dates[r.kind].append(r.date)
        for k, v in r.features.items():
            cols[k].append(v)
        for k, a in r.actual.items():
            cols[f"y.{k}"].append(math.nan if a is None else a)
    return {
        kind: Table(np.array(dates[kind]), {k: np.frombuffer(v, dtype=float) for k, v in cols.items()})
        for kind, cols in data.items()
    }


# ---- formulas (shared by the backtest and live projections) -----------------------------------


def factor(c: Cols, stat: str, name: str, ch: Choice) -> F:
    key = {
        "opp": f"opp.{stat}",
        "opp_shots": "opp.shots",
        "goalie": f"gf.{ch.k_sv}",
        "home": f"home.{stat}",
        "venue": f"venue.{stat}",
        "fd": "fd",
        "fo": "fo",
    }[name]
    return c[key]


def rate(c: Cols, stat: str, hl: int, m: float) -> F:
    return (c[f"x.{stat}.{hl}"] + m * c[f"prior.{stat}"]) / (c[f"b.{stat}.{hl}"] + m)


def finish(c: Cols, hl: int, m: float) -> F:
    return (c[f"x.goals.{hl}"] + m * c["prior_finish"]) / (c[f"x.shots.{hl}"] + m)


def skater_mean(c: Cols, ch: Choice, shots: Choice | None) -> F:
    if ch.kind == "finish":
        assert shots is not None
        lam = skater_mean(c, shots, None) * finish(c, ch.hl, ch.m)
    else:
        lam = rate(c, ch.stat, ch.hl, ch.m) * c[f"eb.{basis_of(ch.stat)}.{ch.hl}"]
    for name in ch.factors:
        lam = lam * factor(c, ch.stat, name, ch)
    return np.asarray(lam, dtype=float)


def goalie_sa_mean(c: Cols, ch: Choice) -> F:
    mu = c["mu_league"].copy()
    for name in ch.factors:
        mu = mu * factor(c, ch.stat, name, ch)
    return mu


def goalie_p(c: Cols, ch: Choice) -> F:
    sv = c[f"sv.{ch.k_sv}"]
    return np.asarray(sv if ch.stat == "saves" else 1 - sv, dtype=float)


def pmf(c: Cols, ch: Choice, shots: Choice | None = None) -> F:
    """Full PMF for a single feature row (columns of length 1)."""
    if ch.kind == "goalie":
        return dist.mixture_vector(float(goalie_sa_mean(c, ch)[0]), ch.sa_size, float(goalie_p(c, ch)[0]))
    return dist.pmf_vector(float(skater_mean(c, ch, shots)[0]), ch.size)


# ---- scoring helpers ------------------------------------------------------------------------


def _score(y: F, c: Cols, ch: Choice, shots: Choice | None) -> tuple[F, F]:
    """(log P(y), cdf(y)) per row."""
    if ch.kind == "goalie":
        pm, cd = dist.mixture_pmf_cdf(y, goalie_sa_mean(c, ch), ch.sa_size, goalie_p(c, ch))
        return np.log(np.maximum(pm, 1e-300)), cd
    mu = skater_mean(c, ch, shots)
    return dist.logpmf(y, mu, ch.size), dist.cdf(y, mu, ch.size)


def _baseline(c: Cols, stat: str, which: str) -> F:
    lam = c[f"base_{which}.{stat}"]
    return np.asarray(np.maximum(lam, np.maximum(0.1 * c[f"fallback.{stat}"], BASELINE_FLOOR)), dtype=float)


def _subset(c: Cols, mask: NDArray[np.bool_]) -> Cols:
    return {k: v[mask] for k, v in c.items()}


# ---- tuning ---------------------------------------------------------------------------------


def _tune_skater(c: Cols, y: F, stat: str, shots: Choice | None) -> tuple[Choice, float]:
    candidates: list[tuple[Choice, float]] = []
    kinds = ["rate", "finish"] if stat == "goals" and shots is not None else ["rate"]
    allf = SKATER_FACTORS[stat]
    for kind in kinds:
        grid = FINISH_GRID if kind == "finish" else M_GRID
        factors = tuple(f for f in allf if not (kind == "finish" and f == "opp_shots"))  # already in shots
        best: tuple[Choice, float] | None = None
        for hl in range(H):
            for m in grid:
                ch = Choice(stat, kind, hl, m, factors)
                size, s = dist.best_size(y, skater_mean(c, ch, shots))
                if best is None or s > best[1]:
                    best = (replace(ch, size=size), s)
        assert best is not None
        candidates.append(_ablate(c, y, best, shots))
    return max(candidates, key=lambda t: t[1])


def _ablate(c: Cols, y: F, best: tuple[Choice, float], shots: Choice | None) -> tuple[Choice, float]:
    """Drop each factor that doesn't help on the tuning window; then pick the save-% prior."""
    ch, score = best
    for name in list(ch.factors):
        trial = replace(ch, factors=tuple(f for f in ch.factors if f != name))
        size, s = dist.best_size(y, skater_mean(c, trial, shots))
        if s > score:
            ch, score = replace(trial, size=size), s
    if "goalie" in ch.factors:
        for j in range(len(K_SV)):
            trial = replace(ch, k_sv=j)
            size, s = dist.best_size(y, skater_mean(c, trial, shots))
            if s > score:
                ch, score = replace(trial, size=size), s
    return ch, score


def _tune_goalie(c: Cols, y: F, sa: F, stat: str) -> tuple[Choice, float]:
    ch = Choice(stat, "goalie", factors=GOALIE_FACTORS)
    sa_size, sa_score = dist.best_size(sa, goalie_sa_mean(c, ch))
    ch = replace(ch, sa_size=sa_size)
    for name in GOALIE_FACTORS:
        trial = replace(ch, factors=tuple(f for f in ch.factors if f != name))
        size, s = dist.best_size(sa, goalie_sa_mean(c, trial))
        if s > sa_score:
            ch, sa_score = replace(trial, sa_size=size), s
    best: tuple[Choice, float] | None = None
    for j in range(len(K_SV)):
        trial = replace(ch, k_sv=j)
        s = float(np.mean(_score(y, c, trial, None)[0]))
        if best is None or s > best[1]:
            best = (trial, s)
    assert best is not None
    return best


# ---- the walk-forward test ------------------------------------------------------------------


def _paired(diff: F) -> dict[str, float]:
    n = len(diff)
    mean = float(np.mean(diff))
    se = float(np.std(diff, ddof=1) / math.sqrt(n)) if n > 1 else math.inf
    return {"mean": round(mean, 5), "se": round(se, 5), "lo": round(mean - 1.96 * se, 5)}


def split_dates(dates: Iterable[str]) -> tuple[str, str, str, str] | None:
    """(first scored date, test start, last date, first date) or None if too little history."""
    ds = sorted(set(dates))
    if len(ds) < 10:
        return None
    first = np.datetime64(ds[0]) + np.timedelta64(BURN_IN_DAYS, "D")
    scored = [d for d in ds if np.datetime64(d) >= first]
    if len(scored) < 10:
        return None
    test_start = scored[int(len(scored) * TUNE_SHARE)]
    return scored[0], test_start, ds[-1], ds[0]


def evaluate(tables: dict[str, Table]) -> dict[str, Any]:
    """Tune every stat on the early window, test on the late window, and decide what ships."""
    report: dict[str, Any] = {"version": MODEL_VERSION, "stats": {}, "choices": {}}
    sk = tables.get("skater")
    split = split_dates(sk.dates.tolist()) if sk is not None else None
    if split is None:
        report["status"] = "insufficient_history"
        for stat in (*SKATER_STATS, *GOALIE_STATS, *game_sim.GAME_STATS):
            report["stats"][stat] = {
                "label": LABELS[stat],
                "family": FAMILY[stat],
                "n_tune": 0,
                "n_test": 0,
                "passed": False,
                "reason": "insufficient_history",
            }
        return report
    scored_from, test_start, last, first = split
    report.update(
        status="ok",
        history={"from": first, "to": last},
        tune={"from": scored_from, "to_before": test_start},
        test={"from": test_start, "to": last},
        lineup_mode="lineup_oracle",
    )
    shots_choice: Choice | None = None
    goals_choice: Choice | None = None
    for kind, stats in (("skater", SKATER_STATS), ("goalie", GOALIE_STATS)):
        table = tables.get(kind)
        for stat in stats:
            entry: dict[str, Any] = {"label": LABELS[stat], "family": FAMILY[stat], "n_tune": 0, "n_test": 0}
            report["stats"][stat] = entry
            if table is None or len(table) == 0:
                entry.update(passed=False, reason="insufficient_history")
                continue
            c_all = table.cols
            y_all = c_all[f"y.{stat}"]
            known = ~np.isnan(y_all) & (table.dates >= scored_from)
            is_test = table.dates >= test_start
            tune_m, test_m = known & ~is_test, known & is_test
            entry.update(n_tune=int(tune_m.sum()), n_test=int(test_m.sum()))
            if entry["n_tune"] < MIN_TEST[kind] or entry["n_test"] < MIN_TEST[kind]:
                entry.update(passed=False, reason="insufficient_history")
                continue
            ct, cv = _subset(c_all, tune_m), _subset(c_all, test_m)
            yt, yv = y_all[tune_m], y_all[test_m]
            if kind == "goalie":
                ch, _ = _tune_goalie(ct, yt, c_all["y.sa"][tune_m], stat)
            else:
                ch, _ = _tune_skater(ct, yt, stat, shots_choice)
                if stat == "shots":
                    shots_choice = ch
            report["choices"][stat] = ch.to_json()
            entry.update(_test(cv, yv, stat, ch, shots_choice))
            if stat == "goals":
                goals_choice = ch
    goals_fn = None
    if goals_choice is not None:
        gc, sc = goals_choice, shots_choice

        def goals_fn(c: Cols) -> F:
            return skater_mean(c, gc, sc)

    game_sim.evaluate(report, tables.get("game"), tables.get("skater"), goals_fn, scored_from, test_start)
    return report


def _test(c: Cols, y: F, stat: str, ch: Choice, shots: Choice | None) -> dict[str, Any]:
    ls, cd = _score(y, c, ch, shots)
    pm = np.exp(ls)
    u = dist.randomized_pit(cd, pm)
    hist = dist.pit_histogram(u)
    n = len(y)
    pit_dev = max(abs(h - 0.1) for h in hist)
    pit_tol = 0.02 + 3 * math.sqrt(0.09 / n)
    base: dict[str, Any] = {}
    beats = True
    for which in ("season", "l10"):
        bl = dist.logpmf(y, _baseline(c, stat, which), INF)
        d = _paired(ls - bl)
        base[which] = {"log_score": round(float(np.mean(bl)), 5), "model_minus_baseline": d}
        beats = beats and d["lo"] > 0
    if ch.kind == "goalie":
        mean_pred = float(np.mean(goalie_sa_mean(c, ch) * goalie_p(c, ch)))
    else:
        mean_pred = float(np.mean(skater_mean(c, ch, shots)))
    passed = beats and pit_dev <= pit_tol
    reason = None
    if not beats:
        reason = "did_not_beat_baselines"
    elif not passed:
        reason = "miscalibrated"
    return {
        "log_score": round(float(np.mean(ls)), 5),
        "baselines": base,
        "pit": hist,
        "pit_max_dev": round(pit_dev, 4),
        "pit_tolerance": round(pit_tol, 4),
        "mean_pred": round(mean_pred, 4),
        "mean_actual": round(float(np.mean(y)), 4),
        "passed": passed,
        "reason": reason,
    }
