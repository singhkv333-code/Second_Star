"""Monte-Carlo robustness on a backtest's realised return path.

A single equity curve is one draw from a noisy process. Resampling the
per-period returns answers what that one curve can't: *how lucky was this
drawdown, and what's the realistic spread of outcomes?*

Method — **circular block bootstrap** (Politis-Romano family). We resample
BLOCKS of consecutive returns (not individual returns) so volatility
clustering and serial correlation survive the shuffle; plain IID resampling
would systematically *understate* drawdowns. Each of ``n_sims`` synthetic
paths is compounded into an equity curve, and we read off the distribution of
max-drawdown and terminal wealth:

  * ``dd_p95_severity_pct`` — the drawdown you'd breach only ~5% of the time
    (the 5th percentile of the max-drawdown distribution; a deep negative).
  * ``prob_loss`` — fraction of paths that end below water.
  * ``prob_dd_worse_than_tol`` — P(max drawdown worse than your tolerance).

Why bootstrap and not a permutation: terminal wealth of a *permuted* return
set is identical (the product commutes), so only resampling-with-replacement
gives a terminal-wealth distribution. (The separate no-skill *permutation*
significance test — permute the PRICE path and re-run the strategy — needs
engine re-runs and lands with walk-forward.)

Pure numpy; deterministic given ``seed`` (reproducible cards + tests).
"""
from __future__ import annotations

from typing import Optional, Sequence

import numpy as np

# Below this many return observations a 1000-path bootstrap is noise, not
# signal — callers get ``None`` and simply omit the block.
_MIN_OBS = 10


def _clean(returns: Sequence[float]) -> np.ndarray:
    out: list[float] = []
    for r in returns:
        if r is None:
            continue
        try:
            v = float(r)
        except (TypeError, ValueError):
            continue
        if np.isfinite(v):
            out.append(v)
    return np.asarray(out, dtype=float)


def _block_bootstrap_paths(
    returns: np.ndarray, n_sims: int, block_size: int,
    rng: np.random.Generator,
) -> np.ndarray:
    """``(n_sims, T)`` matrix of resampled return paths via the *circular*
    block bootstrap — block start indices are random and wrap around the end
    of the series (mod T), so every block keeps its internal autocorrelation."""
    t = len(returns)
    n_blocks = int(np.ceil(t / block_size))
    starts = rng.integers(0, t, size=(n_sims, n_blocks))
    offsets = np.arange(block_size)
    idx = (starts[:, :, None] + offsets[None, None, :]) % t
    idx = idx.reshape(n_sims, n_blocks * block_size)[:, :t]
    return returns[idx]


def monte_carlo_robustness(
    period_returns: Sequence[float],
    *,
    n_sims: int = 1000,
    block_size: Optional[int] = None,
    drawdown_tolerance_pct: float = -20.0,
    seed: int = 1_234_567,
) -> Optional[dict]:
    """Bootstrap distribution of max-drawdown + terminal wealth from a backtest's
    per-period (fractional) returns.

    Returns ``None`` for fewer than ``_MIN_OBS`` finite returns. All percentages
    are signed (drawdowns negative). Deterministic for a given ``seed``.

    ``block_size`` defaults to ~T**(1/3) (the standard rule of thumb), floored
    at 2 so blocks always preserve some adjacency. ``drawdown_tolerance_pct`` is
    the user's pain threshold for the ``prob_dd_worse_than_tol`` probability."""
    r = _clean(period_returns)
    n = len(r)
    if n < _MIN_OBS:
        return None
    if block_size is None:
        block_size = max(2, int(round(n ** (1.0 / 3.0))))
    block_size = max(1, min(block_size, n))

    rng = np.random.default_rng(seed)
    paths = _block_bootstrap_paths(r, n_sims, block_size, rng)  # (n_sims, n)

    # Compound to equity, prepending a 1.0 start so the first bar can draw down.
    equity = np.cumprod(1.0 + paths, axis=1)
    equity = np.concatenate([np.ones((n_sims, 1)), equity], axis=1)
    running_peak = np.maximum.accumulate(equity, axis=1)
    drawdown = equity / running_peak - 1.0          # <= 0
    max_dd = drawdown.min(axis=1)                    # (n_sims,) most-negative per path
    terminal = equity[:, -1] - 1.0                   # terminal return, fractional

    tol = drawdown_tolerance_pct / 100.0
    return {
        "n_sims": int(n_sims),
        "block_size": int(block_size),
        # 5th percentile of the (negative) max-dd distribution = the severity
        # you breach ~5% of the time. min() is the single worst sampled path.
        "dd_median_pct": round(float(np.median(max_dd)) * 100.0, 2),
        "dd_p95_severity_pct": round(float(np.percentile(max_dd, 5)) * 100.0, 2),
        "dd_worst_pct": round(float(max_dd.min()) * 100.0, 2),
        "terminal_median_pct": round(float(np.median(terminal)) * 100.0, 2),
        "terminal_p05_pct": round(float(np.percentile(terminal, 5)) * 100.0, 2),
        "prob_loss": round(float(np.mean(terminal < 0.0)), 4),
        "prob_dd_worse_than_tol": round(float(np.mean(max_dd < tol)), 4),
        "drawdown_tolerance_pct": drawdown_tolerance_pct,
    }


def monte_carlo_terminal_distribution(
    period_returns: Sequence[float],
    *,
    n_sims: int = 2000,
    block_size: Optional[int] = None,
    n_points: int = 120,
    seed: int = 1_234_567,
) -> Optional[dict]:
    """Block-bootstrap distribution of TERMINAL return % (for a "thousands of
    simulations" spread visual).

    Reuses the same circular block bootstrap as :func:`monte_carlo_robustness`,
    but returns the full terminal-wealth spread: a downsampled, SORTED list of
    simulated terminal returns (``n_points`` evenly-spaced quantiles) plus the
    p05/p25/median/p75/p95 markers and ``prob_loss``. All percentages are
    signed. ``None`` for fewer than ``_MIN_OBS`` finite returns. Deterministic
    for a given ``seed``."""
    r = _clean(period_returns)
    n = len(r)
    if n < _MIN_OBS:
        return None
    if block_size is None:
        block_size = max(2, int(round(n ** (1.0 / 3.0))))
    block_size = max(1, min(block_size, n))

    rng = np.random.default_rng(seed)
    paths = _block_bootstrap_paths(r, n_sims, block_size, rng)  # (n_sims, n)
    equity = np.cumprod(1.0 + paths, axis=1)
    terminal = (equity[:, -1] - 1.0) * 100.0                    # terminal return %

    sorted_t = np.sort(terminal)
    p05, p25, median, p75, p95 = (
        float(np.percentile(terminal, q)) for q in (5, 25, 50, 75, 95)
    )
    # Downsample the sorted distribution to ~n_points evenly-spaced quantile
    # samples (keeps the shape; small enough to ship in a JSON payload).
    if len(sorted_t) > n_points:
        idx = np.linspace(0, len(sorted_t) - 1, n_points).round().astype(int)
        ds = sorted_t[idx]
    else:
        ds = sorted_t
    return {
        "n_sims": int(n_sims),
        "block_size": int(block_size),
        "terminal_pct": [round(float(x), 2) for x in ds.tolist()],
        "p05": round(p05, 2),
        "p25": round(p25, 2),
        "median": round(median, 2),
        "p75": round(p75, 2),
        "p95": round(p95, 2),
        "prob_loss": round(float(np.mean(terminal < 0.0)), 4),
    }


__all__ = ["monte_carlo_robustness", "monte_carlo_terminal_distribution"]
