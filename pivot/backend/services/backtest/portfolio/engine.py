"""Cross-sectional portfolio backtest — multi-symbol, constrained, causal.

Pure-numpy core (``momentum_scores`` / ``target_weights`` / ``simulate_portfolio``)
+ a yfinance-backed ``run_portfolio_backtest``. Look-ahead-free: at each rebalance
the signal uses only data up to that day, and the resulting target weights are
applied to the *next* day's returns (one-bar lag). Between rebalances the holding
weights drift with prices (buy-and-hold), so turnover/costs are incurred only when
the book is reset.
"""
from __future__ import annotations

from typing import Optional

import numpy as np

from backend.market.yfinance_service import canonical_symbol, fetch_multi_symbol
from backend.services.trading_costs import round_trip_bps
from backend.services.forward_stats import forward_stats_block
from backend.services.backtest.validation.monte_carlo import monte_carlo_robustness
from backend.services.backtest.validation.sub_periods import sub_period_robustness
from backend.services.backtest.validation.verdict import trust_verdict


class PortfolioError(ValueError):
    """User-facing portfolio-backtest error."""


_FREQ_DAYS = {"W": 5, "M": 21, "Q": 63}


def momentum_scores(prices: np.ndarray, t: int, lookback: int, skip: int) -> np.ndarray:
    """Cross-sectional momentum at day ``t`` using ONLY prices ≤ ``t``:
    ``price[t-skip] / price[t-lookback] - 1`` per symbol (skip-recent, e.g.
    lookback=252, skip=21 → the classic 12-1 momentum). NaN where history is short."""
    n = prices.shape[1]
    if t - lookback < 0:
        return np.full(n, np.nan)
    recent = prices[t - skip] if skip > 0 else prices[t]
    base = prices[t - lookback]
    with np.errstate(divide="ignore", invalid="ignore"):
        score = np.where(base > 0, recent / base - 1.0, np.nan)
    return score


def target_weights(
    scores: np.ndarray,
    *,
    top_n: int,
    gross: float = 1.0,
    long_short: bool = False,
    bottom_n: Optional[int] = None,
) -> np.ndarray:
    """Constrained target weights from cross-sectional scores.

    Enforces **max names** (``top_n`` long, ``bottom_n`` short) and the **gross
    exposure** budget by construction. Long-only → fully-invested equal weight;
    long/short → dollar-neutral (each leg gets gross/2, net ≈ 0). Symbols with a
    NaN score are ineligible."""
    n = scores.size
    w = np.zeros(n)
    valid = np.where(np.isfinite(scores))[0]
    if valid.size == 0:
        return w
    order = valid[np.argsort(scores[valid])[::-1]]   # high score first
    n_long = min(top_n, order.size)
    if n_long == 0:
        return w
    if not long_short:
        w[order[:n_long]] = gross / n_long
        return w
    bn = bottom_n or top_n
    n_short = min(bn, order.size - n_long)            # don't overlap the long leg
    half = gross / 2.0
    w[order[:n_long]] = half / n_long
    if n_short > 0:
        w[order[-n_short:]] = -half / n_short
    return w


def simulate_portfolio(
    R: np.ndarray,
    rebalance_targets: dict[int, np.ndarray],
    *,
    cost_rate: float,
    starting_capital: float = 1.0,
) -> dict:
    """Simulate a drifting-weight portfolio.

    ``R`` is the (T, n) matrix of daily simple returns (``R[0]`` ignored).
    ``rebalance_targets`` maps a *decision day* d → its target weight vector; those
    weights are applied from day d+1 (one-bar lag), and turnover cost is charged on
    d+1. Between rebalances the held weights drift with prices. Returns per-bar
    ``equity`` / ``port_ret`` / ``gross`` / ``net`` arrays + total turnover."""
    T, n = R.shape
    w = np.zeros(n)                       # weights held for the upcoming day
    equity = np.empty(T)
    port_ret = np.zeros(T)
    gross = np.zeros(T)
    net = np.zeros(T)
    equity[0] = starting_capital
    turnover_total = 0.0
    for t in range(1, T):
        cost = 0.0
        if (t - 1) in rebalance_targets:             # decision made at close of t-1
            w_new = rebalance_targets[t - 1]
            turnover = float(np.abs(w_new - w).sum())
            cost = turnover * cost_rate
            turnover_total += turnover
            w = w_new.copy()
        r = R[t]
        pr = float(w @ r) - cost
        port_ret[t] = pr
        equity[t] = equity[t - 1] * (1.0 + pr)
        gross[t] = float(np.abs(w).sum())
        net[t] = float(w.sum())
        # drift weights by today's returns (buy-and-hold until next rebalance)
        denom = 1.0 + float(w @ r)
        if denom > 0:
            w = w * (1.0 + r) / denom
    return {
        "equity": equity, "port_ret": port_ret, "gross": gross, "net": net,
        "turnover_total": turnover_total,
    }


def _rebalance_days(n_days: int, first: int, freq: str) -> list[int]:
    step = _FREQ_DAYS.get(freq.upper(), 21)
    return list(range(first, n_days, step))


def _thin(values, k: int = 200):
    n = len(values)
    if n <= k:
        return list(values)
    step = n / k
    idx = sorted({int(i * step) for i in range(k)} | {n - 1})
    return [values[i] for i in idx]


def run_portfolio_backtest(
    symbols: list[str],
    *,
    period: str = "5y",
    signal: str = "momentum",
    lookback: int = 252,
    skip: int = 21,
    top_n: int = 5,
    rebalance: str = "M",
    long_short: bool = False,
    gross: float = 1.0,
    starting_capital: float = 1_000_000.0,
    num_trials: int = 1,
) -> dict:
    """Backtest a cross-sectional portfolio over ``symbols`` (yfinance daily closes,
    aligned). Today the only ``signal`` is ``"momentum"``. Raises :class:`PortfolioError`."""
    if signal != "momentum":
        raise PortfolioError(f"unsupported signal {signal!r} (only 'momentum' for now).")
    canon = list(dict.fromkeys(canonical_symbol(s) for s in symbols))
    if len(canon) < max(2, top_n):
        raise PortfolioError(
            f"need at least {max(2, top_n)} distinct symbols for top_n={top_n}."
        )
    data = fetch_multi_symbol(symbols, period, "1d")
    names, cols = [], []
    for c in canon:
        recs = data.get(c, [])
        if recs:
            names.append(c)
            cols.append([r["close"] for r in recs])
    if len(names) < max(2, top_n):
        raise PortfolioError("too few symbols returned aligned data.")
    L = min(len(c) for c in cols)
    P = np.array([c[:L] for c in cols], dtype=float).T          # (T, n)
    dates = [r["date"] for r in data[names[0]][:L]]
    T = P.shape[0]
    if T < lookback + 40:
        raise PortfolioError(
            f"insufficient history ({T} bars) for lookback {lookback}; use a longer period."
        )

    R = np.zeros_like(P)
    R[1:] = P[1:] / P[:-1] - 1.0

    first = lookback + 1
    rb_days = _rebalance_days(T, first, rebalance)
    targets: dict[int, np.ndarray] = {}
    for d in rb_days:
        sc = momentum_scores(P, d, lookback, skip)
        targets[d] = target_weights(
            sc, top_n=top_n, gross=gross, long_short=long_short, bottom_n=top_n
        )

    cost_rate = round_trip_bps() / 1e4
    sim = simulate_portfolio(R, targets, cost_rate=cost_rate, starting_capital=starting_capital)
    equity = sim["equity"]

    # trim the pre-first-rebalance flat region for the rigor stats / curve
    start_i = rb_days[0] if rb_days else 0
    eq = equity[start_i:]
    rets = sim["port_ret"][start_i:]
    total_return_pct = float(eq[-1] / eq[0] - 1.0) * 100.0 if eq.size and eq[0] > 0 else 0.0

    peak = np.maximum.accumulate(eq)
    max_dd = float(((eq - peak) / peak).min() * 100.0) if eq.size else 0.0

    fs = forward_stats_block(eq.tolist(), num_trials=num_trials)
    mc = monte_carlo_robustness(rets.tolist())
    sp = sub_period_robustness(eq.tolist())
    verdict = trust_verdict(
        forward_stats=fs, monte_carlo=mc, sub_periods=sp,
        total_return_pct=total_return_pct, n_trades=len(rb_days),
    )

    idx = _thin(list(range(start_i, T)))
    return {
        "symbols": names,
        "period": period,
        "params": {
            "signal": signal, "lookback": lookback, "skip": skip, "top_n": top_n,
            "rebalance": rebalance, "long_short": long_short, "gross": gross,
        },
        "metrics": {
            "total_return_pct": round(total_return_pct, 3),
            "n_rebalances": len(rb_days),
            "max_drawdown_pct": round(max_dd, 3),
            "avg_gross": round(float(np.mean(sim["gross"][start_i:])), 3) if eq.size else 0.0,
            "avg_net": round(float(np.mean(sim["net"][start_i:])), 3) if eq.size else 0.0,
            "turnover_total": round(sim["turnover_total"], 2),
            "n_bars": int(T),
            "forward_stats": fs,
            "monte_carlo": mc,
            "sub_periods": sp,
            "trust_verdict": verdict,
        },
        "series": {
            "dates": [dates[i] for i in idx],
            "equity": [round(float(equity[i]), 2) for i in idx],
        },
    }
