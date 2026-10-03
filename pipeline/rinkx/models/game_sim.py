"""Game-level model (docs/05-models.md §6, version 1.1): team goals, win, overtime, shutout,
first goal, team and game totals. All closed-form, so no simulation noise.

    team expected goals  lambda = league shots/game x shots-for x opp shots-against
                                  x league goals/shot x team finishing x opposing goalie x home
    regulation goals     H ~ NB(lambda_h), A ~ NB(lambda_a), independent
    tied after 60 min    regular season: OT goal with prob q (from past games), split by
                         lambda_h : lambda_a; otherwise a shootout (home share from past games).
                         Playoffs: play until a goal, split by lambda_h : lambda_a.

Shootout "goals" are never goals (team goals, shutouts and first goal ignore them), except in the
game and team *totals* offered for pricing, which follow book settlement: the shootout winner is
credited one goal. Everything is tested walk-forward against simple baselines, exactly like the
player models (fit.py).
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, replace
from typing import Any

import numpy as np
from scipy import stats

from rinkx.models import dist
from rinkx.models.dist import INF, F
from rinkx.models.state import FIN_C, K_SV

MAXG = 15  # goals per team in regulation, support 0..MAXG
GAME_FACTORS = ("fin", "goalie", "home", "rest")
EPS = 1e-6
MIN_GAMES = 300  # games in each of the tuning and test windows
MIN_FIRST_GOAL_ROWS = 3000

GAME_STATS = ("team_goals", "win", "shutout", "first_goal", "saves_win")
SAVES_WIN_LINE = 24.5  # the line the walk-forward test scores (a common book line)
SAVES_WIN_LINES = (19.5, 22.5, 24.5, 27.5, 29.5)  # probabilities shown on goalie pages
LABELS = {
    "team_goals": "Team goals (team & game totals)",
    "win": "Win probability (moneyline, goalie win)",
    "shutout": "Goalie shutout",
    "first_goal": "First goal scorer",
    "saves_win": "Saves + Win (goalie)",
}


@dataclass(frozen=True)
class GameChoice:
    fin: int = 1  # index into FIN_C
    k_sv: int = 1  # index into K_SV
    factors: tuple[str, ...] = GAME_FACTORS
    size: float = INF  # NB size for team goals; inf = Poisson

    def to_json(self) -> dict[str, Any]:
        d = asdict(self)
        d["factors"] = list(self.factors)
        d["size"] = None if math.isinf(self.size) else self.size
        d["fin_prior_shots"] = FIN_C[self.fin]
        d["k_sv_shots"] = K_SV[self.k_sv]
        d["kind"] = "game"
        return d

    @staticmethod
    def from_json(d: dict[str, Any]) -> GameChoice:
        return GameChoice(
            int(d["fin"]), int(d["k_sv"]), tuple(d["factors"]), INF if d["size"] is None else float(d["size"])
        )


Cols = dict[str, F]


def team_lambda(c: Cols, side: str, ch: GameChoice) -> F:
    lam = c[f"{side}.S"] * c["gL"]
    if "fin" in ch.factors:
        lam = lam * c[f"{side}.fin.{ch.fin}"]
    if "goalie" in ch.factors:
        lam = lam * c[f"{side}.gf.{ch.k_sv}"]
    if "home" in ch.factors:
        lam = lam * c[f"{side}.home"]
    if "rest" in ch.factors:
        lam = lam * c[f"{side}.rest"]
    return np.asarray(np.maximum(lam, dist.MIN_MU), dtype=float)


def _pmf_matrix(lam: F, size: float) -> F:
    ks = np.arange(MAXG + 1, dtype=float)
    p = np.exp(dist.logpmf(ks[None, :], lam[:, None], size))
    return np.asarray(p / p.sum(axis=1, keepdims=True), dtype=float)


@dataclass
class Outcomes:
    lam_h: F
    lam_a: F
    p_home_win: F
    p_tied_reg: F
    p_home_first: F
    p_away_first: F
    so_home: F  # home goalie shutout (away scores no goal)
    so_away: F
    home_total: F  # (n, MAXG + 2): real goals, including the OT goal (what the team-goals test scores)
    away_total: F
    game_total: F  # (n, 2 * MAXG + 2): as books settle totals, the shootout winner credited one goal
    home_total_book: F  # (n, MAXG + 2): team totals as books settle them (shootout winner +1)
    away_total_book: F


def outcomes(c: Cols, ch: GameChoice) -> Outcomes:
    lam_h, lam_a = team_lambda(c, "h", ch), team_lambda(c, "a", ch)
    ph, pa = _pmf_matrix(lam_h, ch.size), _pmf_matrix(lam_a, ch.size)
    n = len(lam_h)
    playoff = c["playoff"] > 0.5
    q = np.where(playoff, 1.0, c["ot_q"])  # P(a goal is scored after a regulation tie)
    s_h = lam_h / (lam_h + lam_a)
    cdf_a = np.cumsum(pa, axis=1)
    reg_home = (ph[:, 1:] * cdf_a[:, :-1]).sum(axis=1)
    tie_k = ph * pa  # P(H = A = k)
    tie = tie_k.sum(axis=1)
    so_win = np.where(playoff, 0.0, c["so_home"])
    p_home_win = reg_home + tie * (q * s_h + (1 - q) * so_win)

    first_h = np.zeros(n)
    first_a = np.zeros(n)
    # P(first goal is home) in regulation: competing Poisson processes => lambda share, given >= 1 goal.
    p00 = ph[:, 0] * pa[:, 0]
    first_h += s_h * (1 - p00) + p00 * q * s_h
    first_a += (1 - s_h) * (1 - p00) + p00 * q * (1 - s_h)

    so_home = pa[:, 0] * (1 - ph[:, 0] * q * (1 - s_h))
    so_away = ph[:, 0] * (1 - pa[:, 0] * q * s_h)

    home_total = np.zeros((n, MAXG + 2))
    away_total = np.zeros((n, MAXG + 2))
    home_total[:, : MAXG + 1] += ph - tie_k * (q * s_h)[:, None]
    home_total[:, 1:] += tie_k * (q * s_h)[:, None]
    away_total[:, : MAXG + 1] += pa - tie_k * (q * (1 - s_h))[:, None]
    away_total[:, 1:] += tie_k * (q * (1 - s_h))[:, None]
    # Books count the shootout winner as one goal, so a regulation tie always adds exactly one
    # to the game total (an OT goal or the shootout), and one to whichever team wins it.
    so_mass = tie_k * (1 - q)[:, None]
    home_book, away_book = home_total.copy(), away_total.copy()
    for book, share in ((home_book, so_win), (away_book, np.where(playoff, 0.0, 1 - c["so_home"]))):
        book[:, : MAXG + 1] -= so_mass * share[:, None]
        book[:, 1:] += so_mass * share[:, None]
    game_total = np.zeros((n, 2 * MAXG + 2))
    for i in range(n):
        conv = np.convolve(ph[i], pa[i])
        tie_i = np.zeros_like(conv)
        tie_i[::2][: MAXG + 1] = tie_k[i]
        game_total[i, : len(conv)] += conv - tie_i
        game_total[i, 1 : len(conv) + 1] += tie_i
    return Outcomes(
        lam_h,
        lam_a,
        p_home_win,
        tie,
        first_h,
        first_a,
        so_home,
        so_away,
        home_total,
        away_total,
        game_total,
        home_book,
        away_book,
    )


# ---- scoring --------------------------------------------------------------------------------


def _binary_ll(p: F, y: F) -> F:
    p = np.clip(p, EPS, 1 - EPS)
    return np.asarray(np.where(y > 0.5, np.log(p), np.log(1 - p)), dtype=float)


def _count_ll(pmf: F, y: F) -> tuple[F, F]:
    """log P(y) and P(Y <= y) from per-row PMF matrices."""
    idx = np.clip(y.astype(int), 0, pmf.shape[1] - 1)
    rows = np.arange(len(y))
    pm = pmf[rows, idx]
    cd = np.cumsum(pmf, axis=1)[rows, idx]
    return np.log(np.maximum(pm, 1e-300)), cd


def hosmer_lemeshow(p: F, y: F, groups: int = 10) -> float:
    """p-value of the Hosmer-Lemeshow calibration test (small = miscalibrated). The forecasts were
    not fitted on these outcomes (out-of-sample), so the reference distribution has `groups` df."""
    order = np.argsort(p)
    chi = 0.0
    used = 0
    for b in np.array_split(order, groups):
        if len(b) == 0:
            continue
        e = float(p[b].sum())
        o = float(y[b].sum())
        nb = len(b)
        denom = e * (1 - e / nb)
        if denom > 1e-9:
            chi += (o - e) ** 2 / denom
            used += 1
    return float(stats.chi2.sf(chi, max(used, 1)))


def _paired(diff: F) -> dict[str, float]:
    n = len(diff)
    mean = float(np.mean(diff))
    se = float(np.std(diff, ddof=1) / math.sqrt(n)) if n > 1 else math.inf
    return {"mean": round(mean, 5), "se": round(se, 5), "lo": round(mean - 1.96 * se, 5)}


def _binary_entry(p: F, y: F, baselines: dict[str, F], informational: tuple[str, ...] = ()) -> dict[str, Any]:
    """Log score vs each baseline; `informational` ones are reported but don't decide publication."""
    ll = _binary_ll(p, y)
    base: dict[str, Any] = {}
    beats = True
    for name, bp in baselines.items():
        bl = _binary_ll(bp, y)
        d = _paired(ll - bl)
        base[name] = {"log_score": round(float(np.mean(bl)), 5), "model_minus_baseline": d}
        if name in informational:
            base[name]["required"] = False
        else:
            beats = beats and d["lo"] > 0
    hl = hosmer_lemeshow(p, y)
    passed = beats and hl > 0.01
    return {
        "log_score": round(float(np.mean(ll)), 5),
        "baselines": base,
        "calibration_p": round(hl, 4),
        "mean_pred": round(float(np.mean(p)), 4),
        "mean_actual": round(float(np.mean(y)), 4),
        "passed": passed,
        "reason": None if passed else ("did_not_beat_baselines" if not beats else "miscalibrated"),
    }


def _subset(c: Cols, mask: Any) -> Cols:
    return {k: v[mask] for k, v in c.items()}


def _stack_sides(c: Cols) -> tuple[Cols, F]:
    """Team-goals rows: home and away stacked, as (features with side prefix 'h'), actual goals."""
    out: Cols = {}
    for k, v in c.items():
        if k.startswith("h."):
            out[k] = np.concatenate([v, c["a." + k[2:]]])
        elif not k.startswith("a."):
            out[k] = np.concatenate([v, v])
    y = np.concatenate([c["y.home_goals"], c["y.away_goals"]])
    return out, y


def tune(c: Cols, factors: tuple[str, ...] = GAME_FACTORS) -> GameChoice:
    """Pick finishing prior, save-% prior, factors and dispersion by team-goal log score."""
    sc, y = _stack_sides(c)

    def score(ch: GameChoice) -> tuple[float, float]:
        lam = team_lambda(sc, "h", ch)
        return dist.best_size(y, lam)

    best: tuple[GameChoice, float] | None = None
    for fin in range(len(FIN_C)):
        for k in range(len(K_SV)):
            ch = GameChoice(fin, k, factors)
            size, s = score(ch)
            if best is None or s > best[1]:
                best = (replace(ch, size=size), s)
    assert best is not None
    ch, s_best = best
    for name in factors:
        trial = replace(ch, factors=tuple(f for f in ch.factors if f != name))
        size, s = score(trial)
        if s > s_best:
            ch, s_best = replace(trial, size=size), s
    return ch


def first_goal_probs(skater: Cols, games: dict[int, tuple[float, float, float, float, int]], goals_mean: F) -> F:
    """P(player scores the game's first goal) = P(his team scores first) x his share of team goals."""
    out = np.full(len(goals_mean), np.nan)
    gid, team = skater["meta.game"].astype(int), skater["meta.team"].astype(int)
    for i in range(len(out)):
        g = games.get(int(gid[i]))
        if g is None:
            continue
        lam_h, lam_a, first_h, first_a, home_team = g
        lam_t, first_t = (lam_h, first_h) if team[i] == home_team else (lam_a, first_a)
        out[i] = min(first_t, first_t * goals_mean[i] / lam_t)
    return out


def tie_win(c: Cols, ch: GameChoice, side: str) -> F:
    """P(this side wins | tied after regulation): an OT goal, else the shootout (playoffs: OT only)."""
    lam_h, lam_a = team_lambda(c, "h", ch), team_lambda(c, "a", ch)
    playoff = c["playoff"] > 0.5
    q = np.where(playoff, 1.0, c["ot_q"])
    s_h = lam_h / (lam_h + lam_a)
    so_h = np.where(playoff, 0.0, c["so_home"])
    if side == "h":
        return np.asarray(q * s_h + (1 - q) * so_h, dtype=float)
    return np.asarray(q * (1 - s_h) + (1 - q) * np.where(playoff, 0.0, 1 - c["so_home"]), dtype=float)


def saves_win_joint(c: Cols, ch: GameChoice, side: str, sa_size: float, p_win: F | None = None) -> F:
    """joint[i, k] = P(this side's starting goalie makes k saves AND his team wins), shape (n, SA_MAX + 1).

    Shots against ~ NB(opponent's expected shots, the goalie model's dispersion); goals against |
    shots ~ Binomial(shots, opponent goals per shot), so expected goals against equal the game
    model's lambda; his team's own goals follow the game model; a regulation tie is won through
    OT or the shootout as in `outcomes`. Saves = shots against - goals against. Summing joint over
    k gives P(win); the dependence (more shots: more saves but more goals against) is kept.
    With `p_win` (the game model's win probability) the joint is rescaled to sum to it, so the
    goalie has one win probability everywhere; the shots-against dispersion otherwise moves it
    slightly.
    """
    opp = "a" if side == "h" else "h"
    lam_own, lam_opp = team_lambda(c, side, ch), team_lambda(c, opp, ch)
    shots_against = np.maximum(c[f"{opp}.S"], dist.MIN_MU)
    q_goal = np.clip(lam_opp / shots_against, 1e-4, 0.5)
    own = _pmf_matrix(lam_own, ch.size)  # (n, MAXG + 1)
    own_cdf = np.cumsum(own, axis=1)
    tw = tie_win(c, ch, side)
    w = dist._sa_weights(shots_against, sa_size)  # (n, SA_MAX + 1)
    sa = np.arange(dist.SA_MAX + 1, dtype=float)[None, :]
    joint = np.zeros_like(w)
    for g in range(MAXG + 1):
        win_g = (1 - own_cdf[:, g]) + own[:, g] * tw  # wins outright, or level and wins the tie
        contrib = w * stats.binom.pmf(g, sa, q_goal[:, None]) * win_g[:, None]
        joint[:, : dist.SA_MAX + 1 - g] += contrib[:, g:]  # saves = shots - g
    if p_win is not None:
        joint *= (p_win / np.maximum(joint.sum(axis=1), EPS))[:, None]
    return np.asarray(joint, dtype=float)


def saves_over(c: Cols, side: str, sa_size: float, line: float, ch: GameChoice) -> F:
    """P(saves > line) for this side's goalie, ignoring the result (the independence baseline)."""
    opp = "a" if side == "h" else "h"
    shots_against = np.maximum(c[f"{opp}.S"], dist.MIN_MU)
    q_goal = np.clip(team_lambda(c, opp, ch) / shots_against, 1e-4, 0.5)
    k = math.floor(line)
    _, cd = dist.mixture_pmf_cdf(np.full(len(q_goal), float(k)), shots_against, sa_size, 1 - q_goal)
    return np.asarray(1 - cd, dtype=float)


def evaluate(
    report: dict[str, Any],
    game: Any,  # fit.Table of game rows
    skater: Any,  # fit.Table of skater rows (for first goal)
    goals_mean_fn: Any,  # Cols -> expected goals per skater row (tuned goals model), or None
    scored_from: str,
    test_start: str,
    factors: tuple[str, ...] = GAME_FACTORS,
) -> None:
    """Adds team_goals / win / shutout / first_goal entries and the game choice to `report`."""
    units = {
        "team_goals": "team-games",
        "win": "games",
        "shutout": "goalie starts",
        "first_goal": "player-games",
        "saves_win": "goalie starts",
    }
    entries = {
        s: {"label": LABELS[s], "family": "game_sim", "unit": units[s], "n_tune": 0, "n_test": 0} for s in GAME_STATS
    }
    report["stats"].update(entries)
    if game is None or len(game) == 0:
        for e in entries.values():
            e.update(passed=False, reason="insufficient_history")
        return
    c = game.cols
    scored = game.dates >= scored_from
    is_test = game.dates >= test_start
    tune_m, test_m = scored & ~is_test, scored & is_test
    n_tune, n_test = int(tune_m.sum()), int(test_m.sum())
    for e in entries.values():
        e.update(n_tune=n_tune, n_test=n_test)
    if n_tune < MIN_GAMES or n_test < MIN_GAMES:
        for e in entries.values():
            e.update(passed=False, reason="insufficient_history")
        return
    ch = tune(_subset(c, tune_m), factors)
    report["choices"]["game"] = ch.to_json()
    for s in GAME_STATS:
        report["choices"][s] = ch.to_json()
    cv = _subset(c, test_m)
    out = outcomes(cv, ch)

    # Team goals: both sides, vs. each team's season-average and last-10 Poisson.
    y = np.concatenate([cv["y.home_goals"], cv["y.away_goals"]])
    pmf = np.concatenate([out.home_total, out.away_total])
    ll, cd = _count_ll(pmf, y)
    pm = np.exp(ll)
    u = dist.randomized_pit(cd, pm)
    hist = dist.pit_histogram(u)
    pit_dev = max(abs(h - 0.1) for h in hist)
    pit_tol = 0.02 + 3 * math.sqrt(0.09 / len(y))
    base = {}
    beats = True
    for which in ("season", "l10"):
        lam_b = np.maximum(np.concatenate([cv[f"h.base_{which}"], cv[f"a.base_{which}"]]), 0.5)
        bl = dist.logpmf(y, lam_b, INF)
        d = _paired(ll - bl)
        base[which] = {"log_score": round(float(np.mean(bl)), 5), "model_minus_baseline": d}
        beats = beats and d["lo"] > 0
    ks = np.arange(pmf.shape[1])
    passed = beats and pit_dev <= pit_tol
    entries["team_goals"].update(
        log_score=round(float(np.mean(ll)), 5),
        baselines=base,
        pit=hist,
        pit_max_dev=round(pit_dev, 4),
        pit_tolerance=round(pit_tol, 4),
        mean_pred=round(float((pmf * ks).sum(axis=1).mean()), 4),
        mean_actual=round(float(y.mean()), 4),
        passed=passed,
        reason=None if passed else ("did_not_beat_baselines" if not beats else "miscalibrated"),
        n_test=len(y),
    )

    # Win: vs. the league home-win rate, and log5 of the teams' win % with home advantage.
    hw = cv["lg_home_win"]
    log5 = cv["h.wpct"] * (1 - cv["a.wpct"])
    log5 = log5 / np.maximum(log5 + cv["a.wpct"] * (1 - cv["h.wpct"]), EPS)
    log5 = np.clip(log5, 0.02, 0.98)
    logit = np.log(log5 / (1 - log5)) + np.log(hw / (1 - hw))
    entries["win"].update(
        _binary_entry(out.p_home_win, cv["y.home_win"], {"home_rate": hw, "log5_record": 1 / (1 + np.exp(-logit))})
    )

    # Shutout: each starting goalie, vs. the league rate and his own season rate.
    p_so = np.concatenate([out.so_home, out.so_away])
    y_so = np.concatenate([cv["y.h_so"], cv["y.a_so"]])
    known = ~np.isnan(y_so)
    lg = np.concatenate([cv["lg_so"], cv["lg_so"]])
    own = np.concatenate([cv["h.g_so"], cv["a.g_so"]])
    entries["shutout"].update(
        _binary_entry(
            p_so[known],
            y_so[known],
            {"league_rate": np.maximum(lg[known], 0.005), "goalie_season_rate": np.maximum(own[known], 0.005)},
        )
    )
    entries["shutout"]["n_test"] = int(known.sum())

    # Saves + Win: each starting goalie, "wins with more than SAVES_WIN_LINE saves". It must beat
    # the league rate of that event (tuning window) and be calibrated. Treating saves and the win
    # as independent (same marginals) is reported too, but not required: it is a competing model
    # built from the same parts, not a simple baseline, and the dependence it ignores is small.
    sw = entries["saves_win"]
    saves_ch = report["choices"].get("saves")
    if saves_ch is None:
        sw.update(passed=False, reason="insufficient_history", n_test=0)
    else:
        sa_size = INF if saves_ch.get("sa_size") is None else float(saves_ch["sa_size"])
        k_min = math.floor(SAVES_WIN_LINE) + 1
        p_sw, p_ind, y_sw = [], [], []
        for side in ("h", "a"):
            p_win_side = out.p_home_win if side == "h" else 1 - out.p_home_win
            joint = saves_win_joint(cv, ch, side, sa_size, p_win_side)
            p_sw.append(joint[:, k_min:].sum(axis=1))
            p_ind.append(joint.sum(axis=1) * saves_over(cv, side, sa_size, SAVES_WIN_LINE, ch))
            won, saves = cv[f"y.{side}_gw"], cv[f"y.{side}_saves"]
            y_sw.append(np.where(np.isnan(won), np.nan, (saves > SAVES_WIN_LINE) * won))
        p_all, ind_all, y_all = np.concatenate(p_sw), np.concatenate(p_ind), np.concatenate(y_sw)
        ok = ~np.isnan(y_all)
        ct = _subset(c, tune_m)
        y_tune = np.concatenate([(ct[f"y.{s}_saves"] > SAVES_WIN_LINE) * ct[f"y.{s}_gw"] for s in ("h", "a")])
        lg_rate = float(np.nanmean(y_tune)) if np.isfinite(y_tune).any() else float("nan")
        if int(ok.sum()) < MIN_GAMES or not math.isfinite(lg_rate):
            sw.update(passed=False, reason="insufficient_history", n_test=int(ok.sum()))
        else:
            sw.update(
                _binary_entry(
                    np.clip(p_all[ok], EPS, 1 - EPS),
                    y_all[ok],
                    {
                        "league_rate": np.full(int(ok.sum()), max(lg_rate, 0.005)),
                        "independent": np.clip(ind_all[ok], EPS, 1 - EPS),
                    },
                    informational=("independent",),
                )
            )
            sw["n_test"] = int(ok.sum())
            sw["line"] = SAVES_WIN_LINE

    # First goal: every dressed skater in games with play-by-play.
    fg = entries["first_goal"]
    if skater is None or goals_mean_fn is None:
        fg.update(passed=False, reason="insufficient_history")
        return
    sk = skater.cols
    sk_m = (skater.dates >= test_start) & ~np.isnan(sk["y.first_goal"])
    if int(sk_m.sum()) < MIN_FIRST_GOAL_ROWS:
        fg.update(passed=False, reason="insufficient_history", n_test=int(sk_m.sum()))
        return
    sv = _subset(sk, sk_m)
    gid = cv["meta.game"].astype(int)
    games = {
        int(gid[i]): (
            float(out.lam_h[i]),
            float(out.lam_a[i]),
            float(out.p_home_first[i]),
            float(out.p_away_first[i]),
            int(cv["meta.home_team"][i]),
        )
        for i in range(len(gid))
    }
    p = first_goal_probs(sv, games, goals_mean_fn(sv))
    ok = ~np.isnan(p)
    p, yy = p[ok], sv["y.first_goal"][ok]
    g_ids = sv["meta.game"][ok].astype(int)
    _, inv, counts = np.unique(g_ids, return_inverse=True, return_counts=True)
    uniform = 1.0 / counts[inv]
    share = np.maximum(sv["base_season.goals"][ok], 0.0) + 1e-3
    totals = np.bincount(inv, weights=share)
    season_share = share / totals[inv]
    fg.update(_binary_entry(p, yy, {"equal_chance": uniform, "season_goal_share": season_share}))
    fg["n_test"] = len(p)
    fg["n_tune"] = int(((skater.dates >= scored_from) & (skater.dates < test_start)).sum())
