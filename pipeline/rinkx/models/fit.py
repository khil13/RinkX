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

# Model versions this code can run. Each later version only *adds* candidate inputs, so the
# earlier ones are reproduced exactly by switching those inputs off. The champion of each model
# family (what the site publishes) is decided by live results, not by which version is newest:
# a new version runs alongside as the challenger until it beats the champion on graded props
# (rinkx.models.promotion).
VERSIONS: dict[str, frozenset[str]] = {
    "1.4": frozenset(),
    "1.5": frozenset({"rest", "lines"}),  # back-to-back terms; line and PP-unit deployment, linemates
    # shots: shot attempts, recent form, opponent's allowance to his position, team pace, PP-time change
    "1.6": frozenset({"rest", "lines", "shot_inputs"}),
}
BASE_VERSION = "1.4"  # champion of a family that has none yet
MODEL_VERSION = "1.6"  # the newest version
SLOT_W_GRID: tuple[float, ...] = (0.25, 0.5, 0.75)  # weight on the line slot's ice time
MATES_G_GRID: tuple[float, ...] = (0.25, 0.5, 1.0)  # strength of the linemate-quality factor
MATES_STATS = ("goals", "assists", "points")
ATT_W_GRID: tuple[float, ...] = (0.25, 0.5, 0.75)  # weight on the shot-attempts route to the shot rate
PP_G_GRID: tuple[float, ...] = (0.1, 0.25, 0.5)  # strength of the PP-time-change factor
M_GRID: tuple[float, ...] = (0.25, 0.5, 1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 64.0)  # hours of ice time
FINISH_GRID: tuple[float, ...] = (25.0, 50.0, 100.0, 200.0, 400.0, 800.0)  # shots
BURN_IN_DAYS = 21  # the first weeks of history only build state; they are never scored
TUNE_SHARE = 0.6  # earliest 60% of scored dates tune; the latest 40% are the test
MIN_TEST = {"skater": 3000, "goalie": 150}
BASELINE_FLOOR = 0.02

# Factors each stat may use. The tuner drops any that don't improve the tuning-window score.
# "playoff" is 1.0 in regular-season games; the tuner can only drop it if a tuning window
# contains playoff games, so it is kept by default and judged by the test like everything else.
SKATER_FACTORS: dict[str, tuple[str, ...]] = {
    "shots": ("opp", "home", "playoff", "rest", "form", "opp_pos", "team_pace", "pp_change"),
    "goals": ("opp_shots", "goalie", "home", "playoff", "rest", "mates"),
    "assists": ("opp_shots", "goalie", "home", "playoff", "rest", "mates"),
    "points": ("opp_shots", "goalie", "home", "playoff", "rest", "mates"),
    "pp_points": ("opp", "home", "playoff", "rest"),
    "pp_goals": ("opp", "home", "playoff", "rest"),
    "pp_assists": ("opp", "home", "playoff", "rest"),
    "blocks": ("opp", "home", "venue", "playoff", "rest"),
    "hits": ("opp", "home", "venue", "playoff", "rest"),
}
GOALIE_FACTORS = ("fd", "fo", "po", "rest")
# Which version input each factor belongs to (factors not listed are in every version).
FACTOR_INPUT = {
    "rest": "rest",
    "mates": "lines",
    "form": "shot_inputs",
    "opp_pos": "shot_inputs",
    "team_pace": "shot_inputs",
    "pp_change": "shot_inputs",
}


def allowed(factors: tuple[str, ...], version: str) -> tuple[str, ...]:
    extra = VERSIONS[version]
    return tuple(f for f in factors if FACTOR_INPUT.get(f) is None or FACTOR_INPUT[f] in extra)


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
    "saves_win": "game_sim",
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
    "goalie_saves_and_win": "saves_win",
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
    slot_w: float = 0.0  # weight on the ice time of the player's line slot / PP unit (0 = his own record only)
    mates_g: float = 0.5  # linemate factor = (current linemates' quality / his usual linemates') ** mates_g
    att_w: float = 0.0  # shots: weight on the rate implied by his shot attempts (x league shots per attempt)
    pp_g: float = 0.25  # shots: PP-time-change factor = (recent PP time / long-run PP time) ** pp_g

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
            slot_w=float(d.get("slot_w", 0.0)),
            mates_g=float(d.get("mates_g", 0.5)),
            att_w=float(d.get("att_w", 0.0)),
            pp_g=float(d.get("pp_g", 0.25)),
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
    if name == "mates":
        return np.asarray(c[f"mates.{ch.hl}"] ** ch.mates_g, dtype=float)
    if name == "rest" and ch.kind == "goalie":
        return c["rest_sa"]
    if name == "pp_change":
        return np.asarray(c["pp_change"] ** ch.pp_g, dtype=float)
    if name in ("form", "opp_pos"):
        return c[f"{name}.{stat}"]
    if name == "team_pace":
        return c["team_pace"]
    key = {
        "opp": f"opp.{stat}",
        "opp_shots": "opp.shots",
        "goalie": f"gf.{ch.k_sv}",
        "home": f"home.{stat}",
        "venue": f"venue.{stat}",
        "playoff": f"po.{stat}",
        "rest": f"rest.{stat}",
        "fd": "fd",
        "fo": "fo",
        "po": "po",
    }[name]
    return c[key]


def rate(c: Cols, stat: str, hl: int, m: float, att_w: float = 0.0) -> F:
    own = (c[f"x.{stat}.{hl}"] + m * c[f"prior.{stat}"]) / (c[f"b.{stat}.{hl}"] + m)
    if att_w <= 0 or stat != "shots" or "x.attempts.0" not in c:
        return own
    # shot attempts are steadier than shots on goal: his attempt rate x the league's shots per attempt
    conv = np.where(c["prior.attempts"] > 0, c["prior.shots"] / np.maximum(c["prior.attempts"], 1e-9), 0.0)
    via_att = (c[f"x.attempts.{hl}"] * conv + m * c["prior.shots"]) / (c[f"b.attempts.{hl}"] + m)
    has = c[f"b.attempts.{hl}"] > 0
    return np.asarray(np.where(has, (1 - att_w) * own + att_w * via_att, own), dtype=float)


def finish(c: Cols, hl: int, m: float) -> F:
    return (c[f"x.goals.{hl}"] + m * c["prior_finish"]) / (c[f"x.shots.{hl}"] + m)


def expected_basis(c: Cols, stat: str, ch: Choice) -> F:
    """Expected ice time (or PP time), in hours: his own shrunk record, blended with the usual
    ice time of the line slot / PP unit he is now in when slot_w > 0 and the slot is known."""
    basis = basis_of(stat)
    eb = c[f"eb.{basis}.{ch.hl}"]
    if ch.slot_w <= 0 or "slot.has" not in c:
        return eb
    has, slot = (c["slot.pphas"], c["slot.pp"]) if basis == "pp" else (c["slot.has"], c["slot.toi"])
    return np.asarray(eb + ch.slot_w * has * (slot - eb), dtype=float)


def skater_mean(c: Cols, ch: Choice, shots: Choice | None) -> F:
    if ch.kind == "finish":
        assert shots is not None
        lam = skater_mean(c, shots, None) * finish(c, ch.hl, ch.m)
    else:
        lam = rate(c, ch.stat, ch.hl, ch.m, ch.att_w) * expected_basis(c, ch.stat, ch)
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


def _tune_skater(c: Cols, y: F, stat: str, shots: Choice | None, version: str) -> tuple[Choice, float]:
    candidates: list[tuple[Choice, float]] = []
    kinds = ["rate", "finish"] if stat == "goals" and shots is not None else ["rate"]
    allf = allowed(SKATER_FACTORS[stat], version)
    lines = "lines" in VERSIONS[version]
    shot_inputs = "shot_inputs" in VERSIONS[version] and stat == "shots"
    for kind in kinds:
        grid = FINISH_GRID if kind == "finish" else M_GRID
        # opponent, playoff and rest effects on shot volume are already in the shots model
        factors = tuple(f for f in allf if not (kind == "finish" and f in ("opp_shots", "playoff", "rest")))
        best: tuple[Choice, float] | None = None
        for hl in range(H):
            for m in grid:
                ch = Choice(stat, kind, hl, m, factors)
                size, s = dist.best_size(y, skater_mean(c, ch, shots))
                if best is None or s > best[1]:
                    best = (replace(ch, size=size), s)
        assert best is not None
        if lines and kind == "rate":  # the slot's ice time: kept only if it helps on the tuning window
            ch, score = best
            for w in SLOT_W_GRID:
                trial = replace(ch, slot_w=w)
                size, s = dist.best_size(y, skater_mean(c, trial, shots))
                if s > score:
                    best = (replace(trial, size=size), s)
                    score = s
        if shot_inputs and kind == "rate":  # the shot-attempt route, kept only if it helps
            ch, score = best
            for w in ATT_W_GRID:
                trial = replace(ch, att_w=w)
                size, s = dist.best_size(y, skater_mean(c, trial, shots))
                if s > score:
                    best = (replace(trial, size=size), s)
                    score = s
        candidates.append(_ablate(c, y, best, shots))
    return max(candidates, key=lambda t: t[1])


def _ablate(c: Cols, y: F, best: tuple[Choice, float], shots: Choice | None) -> tuple[Choice, float]:
    """Drop each factor that doesn't help on the tuning window; then pick the save-% prior."""
    ch, score = best
    if "pp_change" in ch.factors:
        for g in PP_G_GRID:
            trial = replace(ch, pp_g=g)
            size, s = dist.best_size(y, skater_mean(c, trial, shots))
            if s > score:
                ch, score = replace(trial, size=size), s
    if "mates" in ch.factors:
        for g in MATES_G_GRID:
            trial = replace(ch, mates_g=g)
            size, s = dist.best_size(y, skater_mean(c, trial, shots))
            if s > score:
                ch, score = replace(trial, size=size), s
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


def _tune_goalie(c: Cols, y: F, sa: F, stat: str, version: str) -> tuple[Choice, float]:
    factors = allowed(GOALIE_FACTORS, version)
    ch = Choice(stat, "goalie", factors=factors)
    sa_size, sa_score = dist.best_size(sa, goalie_sa_mean(c, ch))
    ch = replace(ch, sa_size=sa_size)
    for name in factors:
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


def evaluate(tables: dict[str, Table], version: str = MODEL_VERSION) -> dict[str, Any]:
    """Tune every stat on the early window, test on the late window, and decide what could ship.
    `version` limits the inputs the tuner may use (VERSIONS)."""
    report: dict[str, Any] = {"version": version, "stats": {}, "choices": {}}
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
            entry: dict[str, Any] = {
                "label": LABELS[stat],
                "family": FAMILY[stat],
                "unit": "player-games" if kind == "skater" else "goalie starts",
                "n_tune": 0,
                "n_test": 0,
            }
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
                ch, _ = _tune_goalie(ct, yt, c_all["y.sa"][tune_m], stat, version)
            else:
                ch, _ = _tune_skater(ct, yt, stat, shots_choice, version)
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

    game_sim.evaluate(
        report,
        tables.get("game"),
        tables.get("skater"),
        goals_fn,
        scored_from,
        test_start,
        allowed(game_sim.GAME_FACTORS, version),
    )
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
