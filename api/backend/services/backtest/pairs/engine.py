"""Pairs / stat-arb backtest on a mean-reverting spread.

The tradable signal is strictly causal: at each day ``t`` the hedge ratio and the
spread's mean/stdev come from the *strictly past* window ``[t-lookback, t-1]``; the
z-score uses today's prices; and the resulting position is applied to the *next*
day's spread return (one-bar lag). The full-sample Engle-Granger result is also
reported, but only as an in-sample diagnostic — it never feeds the signal.

Returns are dollar-neutral spread returns ``(r_a - β·r_b)/(1+|β|)`` per unit of
gross capital, net of round-trip costs on each position change, and the equity
curve is run through the same Phase-1 rigor battery as every other engine.
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

from .cointegration import engle_granger, hedge_ratio, johansen


class PairsError(ValueError):
    """User-facing pairs-backtest error (bad symbol, too little data, etc.)."""


def _max_drawdown_pct(equity: np.ndarray) -> float:
    peak = np.maximum.accumulate(equity)
    dd = (equity - peak) / peak
    return float(dd.min() * 100.0)


def _thin(values, max_points: int = 200):
    """Down-sample a list to <= max_points for charting (keeps endpoints)."""
    n = len(values)
    if n <= max_points:
        return list(values)
    step = n / max_points
    idx = sorted({int(i * step) for i in range(max_points)} | {n - 1})
    return [values[i] for i in idx]


def simulate_pairs(
    pa: np.ndarray,
    pb: np.ndarray,
    *,
    lookback: int,
    entry_z: float,
    exit_z: float,
    stop_z: float,
    hedge: str = "rolling",
    starting_capital: float = 1.0,
) -> dict:
    """Pure, network-free simulation core — the causal signal + return engine.

    All estimates at day ``t`` use the strictly-past window ``[t-lookback, t-1]``;
    the z-score uses today's prices; the position is applied to the *next* bar's
    spread return (one-bar lag). No value at index ``t`` reads any price after
    ``t`` — the property the no-look-ahead test pins down. Returns the per-bar
    arrays ``pos``, ``beta_t``, ``z``, ``net_ret`` and the ``equity`` curve.
    """
    pa = np.asarray(pa, dtype=float)
    pb = np.asarray(pb, dtype=float)
    n = pa.size

    r_a = np.zeros(n)
    r_b = np.zeros(n)
    r_a[1:] = pa[1:] / pa[:-1] - 1.0
    r_b[1:] = pb[1:] / pb[:-1] - 1.0

    static_beta: Optional[float] = None
    if hedge == "static":
        static_beta = hedge_ratio(pa[:lookback], pb[:lookback])[1]

    z = np.full(n, np.nan)
    beta_t = np.full(n, np.nan)
    for t in range(lookback, n):
        a_win, b_win = pa[t - lookback:t], pb[t - lookback:t]   # strictly past
        beta = static_beta if static_beta is not None else hedge_ratio(a_win, b_win)[1]
        sp_win = a_win - beta * b_win
        mu = sp_win.mean()
        sd = sp_win.std(ddof=1)
        if sd <= 0:
            continue
        sp_t = pa[t] - beta * pb[t]
        z[t] = (sp_t - mu) / sd
        beta_t[t] = beta

    # Position state machine over z (entry / mean-revert exit / stop).
    pos = np.zeros(n)
    state = 0
    for t in range(lookback, n):
        zt = z[t]
        if not np.isfinite(zt):
            pos[t] = state
            continue
        if state == 0:
            if zt > entry_z:
                state = -1            # short spread (short A, long β·B)
            elif zt < -entry_z:
                state = 1             # long spread (long A, short β·B)
        elif abs(zt) < exit_z or abs(zt) > stop_z:
            state = 0
        pos[t] = state

    # Dollar-neutral spread return per unit gross capital; signal lagged one bar.
    bt = np.nan_to_num(beta_t, nan=0.0)
    gross = 1.0 + np.abs(bt)
    spread_ret = (r_a - bt * r_b) / gross
    pos_lag = np.zeros(n)
    pos_lag[1:] = pos[:-1]
    strat_ret = pos_lag * spread_ret

    # Round-trip cost on each position change (both legs).
    rt = round_trip_bps() / 1e4
    dpos = np.zeros(n)
    dpos[1:] = np.abs(pos[1:] - pos[:-1])
    net_ret = strat_ret - dpos * rt

    equity = starting_capital * np.cumprod(1.0 + net_ret)
    return {"pos": pos, "beta_t": beta_t, "z": z, "net_ret": net_ret, "equity": equity}


def run_johansen(symbols: list[str], *, period: str = "2y", k_ar_diff: int = 1) -> dict:
    """Johansen trace test on a BASKET of ≥2 symbols (yfinance daily closes,
    aligned on common dates). Returns the cointegration rank, eigenvalues / trace
    statistics, and — when rank ≥ 1 — the cointegrating weights mapped to symbols
    (the basket whose weighted combination is stationary)."""
    canon = list(dict.fromkeys(canonical_symbol(s) for s in symbols))
    if len(canon) < 2:
        raise PairsError("Johansen needs at least 2 distinct symbols.")
    data = fetch_multi_symbol(symbols, period, "1d")
    names, series = [], []
    for c in canon:
        recs = data.get(c, [])
        if recs:
            names.append(c)
            series.append(np.array([r["close"] for r in recs], dtype=float))
    if len(series) < 2:
        raise PairsError("fewer than 2 symbols returned aligned data.")
    L = min(s.size for s in series)        # fetch_multi_symbol aligns; guard anyway
    series = [s[:L] for s in series]
    if L < len(series) + k_ar_diff + 30:
        raise PairsError(
            f"insufficient aligned data ({L} bars) for {len(series)} symbols over {period}."
        )

    res = johansen(series, k_ar_diff=k_ar_diff)
    weights = None
    if res.cointegrating_vector:
        weights = {names[i]: res.cointegrating_vector[i] for i in range(len(names))}
    return {
        "symbols": names,
        "period": period,
        "n_obs": res.n_obs,
        "rank": res.rank,
        "is_cointegrated": res.is_cointegrated,
        "eigenvalues": res.eigenvalues,
        "trace_stats": res.trace_stats,
        "crit_95": res.crit_95,
        "cointegrating_weights": weights,
        "note": "Johansen trace test, unrestricted-constant model "
                "(Osterwald-Lenum critical values). rank ≥ 1 ⇒ at least one "
                "stationary combination (a tradable basket spread).",
    }


def run_pairs_backtest(
    symbol_a: str,
    symbol_b: str,
    *,
    period: str = "2y",
    lookback: int = 60,
    entry_z: float = 2.0,
    exit_z: float = 0.5,
    stop_z: float = 4.0,
    hedge: str = "rolling",
    starting_capital: float = 1_000_000.0,
    num_trials: int = 1,
    interval: str = "1d",
) -> dict:
    """Backtest a long/short pairs strategy on ``symbol_a`` vs ``symbol_b``.

    ``hedge``: ``"rolling"`` re-estimates β each day from the trailing window;
    ``"static"`` fixes β from the first ``lookback`` days (then trades the rest).
    ``interval`` is the bar interval used for the spread series — default '1d'
    (daily) preserves prior behaviour; aliases ('daily'/'60m'/...) are
    normalized.
    Raises :class:`PairsError` on bad input / insufficient data.
    """
    if entry_z <= exit_z:
        raise PairsError("entry_z must be greater than exit_z.")
    if lookback < 20:
        raise PairsError("lookback must be at least 20 days.")

    ca, cb = canonical_symbol(symbol_a), canonical_symbol(symbol_b)
    if ca == cb:
        raise PairsError("A pair needs two different symbols.")

    from backend.core.data.intervals import (
        normalize_interval as _normalize_interval,
        to_yfinance as _to_yfinance,
    )
    _norm = _normalize_interval(interval)
    _yf_iv = _to_yfinance(_norm)
    if _yf_iv is None:
        raise PairsError(
            f"yfinance cannot serve interval {_norm!r} for {ca}/{cb}"
        )
    data = fetch_multi_symbol([symbol_a, symbol_b], period, _yf_iv)
    rec_a, rec_b = data.get(ca, []), data.get(cb, [])
    if len(rec_a) != len(rec_b) or len(rec_a) < lookback + 30:
        raise PairsError(
            f"insufficient aligned data for {ca}/{cb} over {period} "
            f"(got {min(len(rec_a), len(rec_b))} common bars; need ≥ {lookback + 30})."
        )

    dates = [r["date"] for r in rec_a]
    pa = np.array([r["close"] for r in rec_a], dtype=float)
    pb = np.array([r["close"] for r in rec_b], dtype=float)
    n = pa.size

    # ---- In-sample cointegration diagnostic (NOT part of the signal) ----
    eg = engle_granger(pa, pb)

    # ---- Causal simulation (signal + dollar-neutral returns + costs) ----
    sim = simulate_pairs(
        pa, pb, lookback=lookback, entry_z=entry_z, exit_z=exit_z,
        stop_z=stop_z, hedge=hedge, starting_capital=starting_capital,
    )
    pos, z, net_ret, equity = sim["pos"], sim["z"], sim["net_ret"], sim["equity"]

    # ---- Trades (round trips) + win rate ----
    trades = []
    entry_i = None
    for t in range(1, n):
        if pos[t - 1] == 0 and pos[t] != 0:
            entry_i = t
        elif entry_i is not None and pos[t] == 0 and pos[t - 1] != 0:
            tr_ret = float(equity[t] / equity[entry_i] - 1.0)
            trades.append({
                "entry_date": dates[entry_i],
                "exit_date": dates[t],
                "direction": "long_spread" if pos[entry_i] > 0 else "short_spread",
                "return_pct": round(tr_ret * 100.0, 3),
            })
            entry_i = None
    n_trades = len(trades)
    wins = sum(1 for tr in trades if tr["return_pct"] > 0)
    win_rate = round(100.0 * wins / n_trades, 1) if n_trades else 0.0

    total_return_pct = float(equity[-1] / equity[0] - 1.0) * 100.0

    # ---- Rigor battery (identical math to every other engine) ----
    fs = forward_stats_block(equity.tolist(), num_trials=num_trials)
    mc = monte_carlo_robustness(net_ret.tolist())
    sp = sub_period_robustness(equity.tolist())
    verdict = trust_verdict(
        forward_stats=fs, monte_carlo=mc, sub_periods=sp,
        total_return_pct=total_return_pct, n_trades=n_trades,
    )

    z_thin_idx = _thin(list(range(n)))
    return {
        "pair": {"a": ca, "b": cb},
        "period": period,
        "params": {
            "lookback": lookback, "entry_z": entry_z, "exit_z": exit_z,
            "stop_z": stop_z, "hedge": hedge,
        },
        "cointegration": {
            "alpha": round(eg.alpha, 6),
            "beta": round(eg.beta, 6),
            "adf_tstat": round(eg.adf_tstat, 4) if np.isfinite(eg.adf_tstat) else None,
            "crit_values": eg.crit_values,
            "cointegrated_at": eg.cointegrated_at,
            "is_cointegrated": eg.is_cointegrated,
            "half_life_days": round(eg.half_life, 2) if eg.half_life else None,
            "note": "Engle-Granger over the full sample — an in-sample diagnostic; "
                    "the backtest signal uses only trailing-window estimates.",
        },
        "metrics": {
            "total_return_pct": round(total_return_pct, 3),
            "n_trades": n_trades,
            "win_rate_pct": win_rate,
            "max_drawdown_pct": round(_max_drawdown_pct(equity), 3),
            "n_bars": n,
            "forward_stats": fs,
            "monte_carlo": mc,
            "sub_periods": sp,
            "trust_verdict": verdict,
        },
        "trades": trades[:50],
        "series": {
            "dates": [dates[i] for i in z_thin_idx],
            "zscore": [round(float(z[i]), 4) if np.isfinite(z[i]) else None for i in z_thin_idx],
            "equity": [round(float(equity[i]), 2) for i in z_thin_idx],
        },
    }
