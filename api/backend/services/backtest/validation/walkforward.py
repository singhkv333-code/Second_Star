"""Walk-forward + no-skill permutation test (Phase 1.4) — the rigor "middle".

Both RE-RUN the strategy many times, so they share a **warmup-aware engine-rerun
adapter**: every evaluation window is padded with ``warmup`` bars of history
BEFORE the window starts, so indicators (RSI, SMA200, …) are warm at the fold
boundary. Clipping a fold to its raw start would leave the indicators cold and
silently corrupt the out-of-sample stats — the failure mode the plan flags.

  * ``permutation_test`` — shuffle the bar-to-bar returns (same return
    distribution, random serial order), rebuild the price path, re-run the
    strategy, and compare the real result to that null. A strategy with a true
    edge (it exploits mean-reversion / momentum) beats the shuffled null; a
    curve-fit one does not. The p-value is the honest "is this better than
    random?".
  * ``walk_forward`` — split the window into sequential out-of-sample folds, each
    re-run with its own warmup, stitched into one OOS curve + a consistency call.

The cores are engine-agnostic (callables); ``deep_validate_engine2b`` wires them
to the single-symbol tree engine. These are EXPENSIVE (N re-runs) → opt-in, not
part of the per-backtest battery.
"""
from __future__ import annotations

from typing import Callable, Optional

import numpy as np


def permutation_test(
    metric_fn: Callable[[np.ndarray], Optional[float]],
    close: "np.ndarray | list[float]",
    *,
    observed: float,
    n_perm: int = 200,
    seed: int = 12_345,
    greater_is_better: bool = True,
) -> Optional[dict]:
    """No-skill Monte-Carlo permutation test.

    ``metric_fn(perm_close) -> float`` re-runs the strategy on a permuted close
    series and returns its performance metric (e.g. total return). Returns the
    p-value ``P(null >= observed)`` + a verdict, or ``None`` if no permutation
    produced a finite metric.
    """
    close = np.asarray(close, dtype=float)
    if close.size < 8:
        return None
    rets = close[1:] / close[:-1] - 1.0
    rng = np.random.default_rng(seed)
    null: list[float] = []
    for _ in range(n_perm):
        perm = rng.permutation(rets)
        perm_close = np.empty(close.size)
        perm_close[0] = close[0]
        perm_close[1:] = close[0] * np.cumprod(1.0 + perm)
        m = metric_fn(perm_close)
        if m is not None and np.isfinite(m):
            null.append(float(m))
    if not null:
        return None
    arr = np.array(null)
    if greater_is_better:
        at_least_as_good = int(np.sum(arr >= observed))
        tail = float(np.percentile(arr, 95))
    else:
        at_least_as_good = int(np.sum(arr <= observed))
        tail = float(np.percentile(arr, 5))
    # +1 in num & denom → unbiased small-sample p-value (never exactly 0).
    p = (at_least_as_good + 1) / (len(arr) + 1)
    skill = p < 0.05
    return {
        "p_value": round(p, 4),
        "observed": round(float(observed), 4),
        "null_mean": round(float(arr.mean()), 4),
        "null_tail_p95": round(tail, 4),
        "n_perm": len(arr),
        "skill": skill,
        "verdict": "beats_random" if skill else "no_skill",
    }


def walk_forward(
    run_window: Callable[[int, int, int], "np.ndarray | list[float]"],
    n_bars: int,
    *,
    n_folds: int = 4,
    warmup: int = 200,
) -> Optional[dict]:
    """Sequential out-of-sample folds over ``[warmup, n_bars)``.

    ``run_window(test_start, test_end, warmup) -> per-bar fractional returns`` of
    the test fold (the adapter pads ``warmup`` bars before ``test_start`` so the
    strategy is warm, then keeps only in-fold bars). Returns per-fold returns, the
    stitched OOS total, the fraction of positive folds, and a consistency verdict.
    """
    if n_bars - warmup < 2 * n_folds:
        return None
    eval_start = warmup
    span = n_bars - eval_start
    bounds = [eval_start + round(i * span / n_folds) for i in range(n_folds + 1)]
    fold_rets: list[np.ndarray] = []
    per_fold: list[dict] = []
    for k in range(n_folds):
        ts, te = bounds[k], bounds[k + 1]
        if te - ts < 2:
            continue
        r = np.asarray(run_window(ts, te, warmup), dtype=float)
        total = float(np.prod(1.0 + r) - 1.0) if r.size else 0.0
        per_fold.append({"fold": k + 1, "return_pct": round(total * 100, 3),
                         "n_bars": int(r.size)})
        fold_rets.append(r)
    fold_rets = [r for r in fold_rets if r.size]
    if not fold_rets:
        return None
    oos = np.concatenate(fold_rets)
    oos_total = float(np.prod(1.0 + oos) - 1.0) * 100.0
    n_pos = sum(1 for f in per_fold if f["return_pct"] > 0)
    frac_pos = n_pos / len(per_fold) if per_fold else 0.0
    consistent = frac_pos >= 0.6 and oos_total > 0
    return {
        "n_folds": len(per_fold),
        "per_fold": per_fold,
        "oos_total_return_pct": round(oos_total, 3),
        "frac_folds_positive": round(frac_pos, 2),
        "consistent": consistent,
        "verdict": "consistent_oos" if consistent else "inconsistent_oos",
    }


# ── Engine-2b adapter (single-symbol tree engine) ────────────────────

def deep_validate_engine2b(
    *,
    tree: dict,
    primary_symbol: str,
    bars,                       # pandas DataFrame (DatetimeIndex, open/high/low/close/volume)
    exit_policy,
    starting_capital: float = 100_000.0,
    quantity: int = 10,
    n_perm: int = 200,
    n_folds: int = 4,
    warmup: int = 200,
    seed: int = 12_345,
) -> dict:
    """Run the permutation test + walk-forward for a single-symbol tree strategy
    over the given bars. Re-runs Engine 2b with an injected fetcher each time."""
    import pandas as pd

    from backend.workflows.dsl.backtest.engine import run_backtest
    from backend.workflows.dsl.backtest.schema import BacktestRequest

    idx = bars.index

    def _run(df, start, end):
        def _fetch(symbol, s, e):
            m = (df.index >= pd.Timestamp(s)) & (df.index <= pd.Timestamp(e))
            return df.loc[m].copy()
        req = BacktestRequest(
            tree=tree, primary_symbol=primary_symbol,
            start_date=start, end_date=end,
            starting_capital=starting_capital, quantity=quantity,
            exit_policy=exit_policy, save=False,
        )
        return run_backtest(request=req, user_id=1, fetcher=_fetch)

    def _bars_from_close(close_arr):
        return pd.DataFrame({
            "open": close_arr, "high": close_arr * 1.001,
            "low": close_arr * 0.999, "close": close_arr,
            "volume": np.full(close_arr.size, 1e6),
        }, index=idx)

    observed = _run(bars, idx[0].date(), idx[-1].date()).metrics.total_return_pct

    def _metric(perm_close):
        try:
            return _run(_bars_from_close(perm_close), idx[0].date(), idx[-1].date()).metrics.total_return_pct
        except Exception:
            return None

    perm = permutation_test(_metric, bars["close"].to_numpy(), observed=observed,
                            n_perm=n_perm, seed=seed)

    def _run_window(ts, te, wu):
        s = max(0, ts - wu)
        sub = bars.iloc[s:te]
        if len(sub) < 3:
            return []
        res = _run(sub, sub.index[0].date(), sub.index[-1].date())
        fold_start = idx[ts].date()
        eq = np.array([p.equity for p in res.equity_curve if p.date >= fold_start], dtype=float)
        return eq[1:] / eq[:-1] - 1.0 if eq.size >= 2 else []

    wf = walk_forward(_run_window, len(bars), n_folds=n_folds, warmup=warmup)
    return {"observed_return_pct": round(float(observed), 3),
            "permutation": perm, "walk_forward": wf}
