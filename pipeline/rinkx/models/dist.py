"""Count distributions used by every model: Poisson, negative binomial, and the shots-against x
save-probability mixture for goalies. Everything is vectorized over rows (numpy arrays).

Negative binomial is parameterized by mean `mu` and size `r` (variance = mu + mu^2 / r);
`r = inf` is Poisson. Which one fits is decided from data (see fit.py), never assumed.
"""

from __future__ import annotations

import math

import numpy as np
from numpy.typing import NDArray
from scipy import stats

F = NDArray[np.float64]

INF = math.inf
# Candidate NB sizes; inf = Poisson. The best is chosen by log score on the tuning window.
SIZE_GRID: tuple[float, ...] = (1.0, 2.0, 3.0, 5.0, 8.0, 13.0, 20.0, 35.0, 60.0, 100.0, 250.0, INF)
MIN_MU = 1e-4
SA_MAX = 90  # shots-against support for the goalie mixture


def _mu(mu: F | float) -> F:
    return np.asarray(np.maximum(np.asarray(mu, dtype=float), MIN_MU), dtype=float)


def logpmf(y: F, mu: F | float, r: float) -> F:
    m = _mu(mu)
    if math.isinf(r):
        return np.asarray(stats.poisson.logpmf(y, m), dtype=float)
    return np.asarray(stats.nbinom.logpmf(y, r, r / (r + m)), dtype=float)


def cdf(y: F, mu: F | float, r: float) -> F:
    m = _mu(mu)
    if math.isinf(r):
        return np.asarray(stats.poisson.cdf(y, m), dtype=float)
    return np.asarray(stats.nbinom.cdf(y, r, r / (r + m)), dtype=float)


def pmf_vector(mu: float, r: float, tail: float = 1e-5, kmax: int = 200) -> F:
    """P(X = k) for k = 0..K, where K is the first k with P(X > k) < tail."""
    ks = np.arange(kmax + 1, dtype=float)
    p = np.exp(logpmf(ks, mu, r))
    c = np.cumsum(p)
    k_end = int(np.searchsorted(c, 1 - tail)) + 1
    return np.asarray(p[: max(k_end, 1)], dtype=float)


def best_size(y: F, mu: F) -> tuple[float, float]:
    """(size, mean log score) maximizing the log score of y under NB(mu, size)."""
    best = (INF, -INF)
    for r in SIZE_GRID:
        s = float(np.mean(logpmf(y, mu, r)))
        if s > best[1]:
            best = (r, s)
    return best


# --- Goalie mixture: SA ~ NB(mu_sa, r_sa); X | SA ~ Binomial(SA, p) ---------------------------------


def _sa_weights(mu_sa: F, r_sa: float) -> F:
    sa = np.arange(SA_MAX + 1, dtype=float)
    w = np.exp(logpmf(sa[None, :], mu_sa[:, None], r_sa))
    return np.asarray(w / w.sum(axis=1, keepdims=True), dtype=float)


def mixture_pmf_cdf(y: F, mu_sa: F, r_sa: float, p: F) -> tuple[F, F]:
    """P(X = y) and P(X <= y) per row, X = Binomial(SA, p) mixed over SA."""
    w = _sa_weights(_mu(mu_sa), r_sa)
    sa = np.arange(SA_MAX + 1, dtype=float)[None, :]
    yy, pp = y[:, None], p[:, None]
    pm = (w * stats.binom.pmf(yy, sa, pp)).sum(axis=1)
    cd = (w * stats.binom.cdf(yy, sa, pp)).sum(axis=1)
    return np.asarray(pm, dtype=float), np.asarray(cd, dtype=float)


def mixture_vector(mu_sa: float, r_sa: float, p: float, tail: float = 1e-5) -> F:
    w = _sa_weights(np.array([max(mu_sa, MIN_MU)]), r_sa)[0]
    ks = np.arange(SA_MAX + 1, dtype=float)
    out = (w[:, None] * stats.binom.pmf(ks[None, :], ks[:, None], p)).sum(axis=0)
    c = np.cumsum(out)
    k_end = int(np.searchsorted(c, 1 - tail)) + 1
    return np.asarray(out[: max(k_end, 1)], dtype=float)


# --- Scoring -------------------------------------------------------------------------------------


def randomized_pit(cdf_y: F, pmf_y: F, seed: int = 7) -> F:
    """PIT for discrete forecasts: u = F(y-1) + V * P(y), V ~ U(0,1). Uniform iff calibrated."""
    v = np.random.default_rng(seed).random(len(cdf_y))
    return np.clip(cdf_y - pmf_y + v * pmf_y, 0.0, 1.0)


def pit_histogram(u: F, bins: int = 10) -> list[float]:
    h, _ = np.histogram(u, bins=bins, range=(0.0, 1.0))
    return [round(float(x) / max(len(u), 1), 4) for x in h]


def summary(p: F) -> dict[str, float]:
    ks = np.arange(len(p), dtype=float)
    total = float(p.sum())
    mean = float((ks * p).sum() / total)
    var = float(((ks - mean) ** 2 * p).sum() / total)
    median = int(np.searchsorted(np.cumsum(p) / total, 0.5))
    return {"mean": mean, "median": float(median), "sd": math.sqrt(max(var, 0.0))}


def p_ge(p: F) -> list[float]:
    """P(X >= k) for k = 0..len(p)-1 (tail beyond the vector is < 1e-5)."""
    tail = np.cumsum(p[::-1])[::-1]
    return [round(float(min(x, 1.0)), 5) for x in tail]
