"""Parlay combination (Phase 8): reference implementation for web/src/lib/parlay.ts.

P(all legs win) under a Gaussian copula: leg i wins when a standard normal Z_i <= Phi^-1(p_i),
with corr(Z_i, Z_j) = the estimated correlation, oriented so that it is positive when the two
legs tend to win together. The orthant probability is computed with Genz's separation-of-variables
method on a fixed Richtmyer lattice, so it is deterministic. The browser runs the same arithmetic
(same normal CDF, same inverse, same lattice) and is tested against vectors generated here
(fixtures/parlay_vectors.json).

A correlation matrix that isn't positive definite is shrunk toward independence (off-diagonals
x 0.9 until the Cholesky factorization succeeds), and the shrink factor is reported.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

N_POINTS = 4096
PRIMES = (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37)  # lattice generators: frac(j * sqrt(prime))
MAX_LEGS = len(PRIMES) + 1


_ERFC = (
    -1.26551223,
    1.00002368,
    0.37409196,
    0.09678418,
    -0.18628806,
    0.27886807,
    -1.13520398,
    1.48851587,
    -0.82215223,
    0.17087277,
)


def erfc(x: float) -> float:
    """Complementary error function, Chebyshev fit (Numerical Recipes erfcc), |error| < 1.2e-7."""
    z = abs(x)
    t = 1 / (1 + 0.5 * z)
    poly = _ERFC[-1]
    for coef in reversed(_ERFC[:-1]):
        poly = coef + t * poly
    r = t * math.exp(-z * z + poly)
    return r if x >= 0 else 2 - r


def cdf(x: float) -> float:
    return 0.5 * erfc(-x / math.sqrt(2))


_A = (
    -3.969683028665376e01,
    2.209460984245205e02,
    -2.759285104469687e02,
    1.383577518672690e02,
    -3.066479806614716e01,
    2.506628277459239e00,
)
_B = (-5.447609879822406e01, 1.615858368580409e02, -1.556989798598866e02, 6.680131188771972e01, -1.328068155288572e01)
_C = (
    -7.784894002430293e-03,
    -3.223964580411365e-01,
    -2.400758277161838e00,
    -2.549732539343734e00,
    4.374664141464968e00,
    2.938163982698783e00,
)
_D = (7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e00, 3.754408661907416e00)


def ppf(p: float) -> float:
    """Inverse normal CDF (Acklam's rational approximation, relative error < 1.2e-9)."""
    if p <= 0:
        return -math.inf
    if p >= 1:
        return math.inf
    lo = 0.02425
    if p < lo:
        q = math.sqrt(-2 * math.log(p))
        return (((((_C[0] * q + _C[1]) * q + _C[2]) * q + _C[3]) * q + _C[4]) * q + _C[5]) / (
            (((_D[0] * q + _D[1]) * q + _D[2]) * q + _D[3]) * q + 1
        )
    if p > 1 - lo:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((_C[0] * q + _C[1]) * q + _C[2]) * q + _C[3]) * q + _C[4]) * q + _C[5]) / (
            (((_D[0] * q + _D[1]) * q + _D[2]) * q + _D[3]) * q + 1
        )
    q = p - 0.5
    r = q * q
    return (
        (((((_A[0] * r + _A[1]) * r + _A[2]) * r + _A[3]) * r + _A[4]) * r + _A[5])
        * q
        / (((((_B[0] * r + _B[1]) * r + _B[2]) * r + _B[3]) * r + _B[4]) * r + 1)
    )


def cholesky(m: list[list[float]]) -> list[list[float]] | None:
    n = len(m)
    c = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1):
            s = m[i][j] - sum(c[i][k] * c[j][k] for k in range(j))
            if i == j:
                if s <= 1e-10:
                    return None
                c[i][i] = math.sqrt(s)
            else:
                c[i][j] = s / c[j][j]
    return c


def make_pd(corr: list[list[float]]) -> tuple[list[list[float]], list[list[float]], float]:
    """(matrix used, its Cholesky factor, shrink factor applied to the off-diagonals)."""
    f = 1.0
    for _ in range(200):
        m = [[1.0 if i == j else corr[i][j] * f for j in range(len(corr))] for i in range(len(corr))]
        c = cholesky(m)
        if c is not None:
            return m, c, f
        f *= 0.9
    raise ValueError("could not make the correlation matrix positive definite")


def _frac(x: float) -> float:
    return x - math.floor(x)


def combine(probs: list[float], corr: list[list[float]]) -> tuple[float, float]:
    """(P(every leg wins), shrink factor). Independent legs give exactly the product."""
    k = len(probs)
    if k == 0:
        return 1.0, 1.0
    if k > MAX_LEGS:
        raise ValueError(f"at most {MAX_LEGS} legs")
    if any(p <= 0 for p in probs):
        return 0.0, 1.0
    if all(corr[i][j] == 0 for i in range(k) for j in range(k) if i != j):
        out = 1.0
        for p in probs:
            out *= p
        return out, 1.0
    _, c, shrink = make_pd(corr)
    b = [ppf(min(p, 1 - 1e-12)) for p in probs]
    alphas = [math.sqrt(PRIMES[i]) for i in range(k - 1)]
    total = 0.0
    for j in range(1, N_POINTS + 1):
        e = cdf(b[0] / c[0][0])
        f = e
        y: list[float] = []
        for i in range(1, k):
            w = _frac(j * alphas[i - 1])
            y.append(ppf(min(max(w * e, 1e-15), 1 - 1e-15)))
            s = sum(c[i][m] * y[m] for m in range(i))
            e = cdf((b[i] - s) / c[i][i])
            f *= e
        total += f
    return total / N_POINTS, shrink


VECTOR_CASES: list[tuple[list[float], list[list[float]]]] = [
    ([0.55], [[1.0]]),
    ([0.55, 0.6], [[1, 0], [0, 1]]),
    ([0.55, 0.6], [[1, 0.3], [0.3, 1]]),
    ([0.5, 0.5], [[1, -0.4], [-0.4, 1]]),
    ([0.3, 0.7], [[1, 0.8], [0.8, 1]]),
    ([0.52, 0.48, 0.6], [[1, 0.25, 0.1], [0.25, 1, 0.05], [0.1, 0.05, 1]]),
    ([0.2, 0.9, 0.55], [[1, 0.5, -0.2], [0.5, 1, 0.3], [-0.2, 0.3, 1]]),
    ([0.6, 0.6, 0.6], [[1, 0.9, -0.9], [0.9, 1, 0.9], [-0.9, 0.9, 1]]),  # not PD: shrunk
    ([0.55, 0.5, 0.45, 0.6, 0.52], [[1 if i == j else 0.12 for j in range(5)] for i in range(5)]),
    ([0.03, 0.97], [[1, 0.2], [0.2, 1]]),
]


def vectors() -> dict[str, object]:
    out = []
    for probs, corr in VECTOR_CASES:
        p, shrink = combine(probs, corr)
        out.append({"probs": probs, "corr": corr, "p": p, "shrink": shrink})
    return {
        "about": "Generated by pipeline/rinkx/correlation/parlay.py; web/src/lib/parlay.ts must match.",
        "n_points": N_POINTS,
        "cases": out,
    }


def write_vectors(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(vectors(), indent=1) + "\n")


if __name__ == "__main__":  # python -m rinkx.correlation.parlay  (regenerates the shared vectors)
    from rinkx.config import REPO_ROOT

    write_vectors(REPO_ROOT / "fixtures/parlay_vectors.json")
