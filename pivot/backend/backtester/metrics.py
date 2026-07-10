"""
Performance metrics for the backtester.

Pure pandas / numpy. Takes the equity curve and trades produced by
PortfolioSimulator and returns a comprehensive metrics dict that the
frontend MetricsDashboard renders.
"""
from __future__ import annotations

import math
from typing import Iterable, Optional

import numpy as np
import pandas as pd

from backend.backtester.portfolio import PortfolioSnapshot, Trade

TRADING_DAYS_PER_YEAR = 252


def _safe(x: float, default: float = 0.0) -> float:
    if x is None:
        return default
    if isinstance(x, float) and (math.isnan(x) or math.isinf(x)):
        return default
    return float(x)


def _to_value_series(curve: Iterable[PortfolioSnapshot]) -> pd.Series:
    if not curve:
        return pd.Series(dtype=float)
    idx = pd.DatetimeIndex([pd.Timestamp(s.date) for s in curve])
    return pd.Series([s.total_value for s in curve], index=idx, dtype=float)


def _drawdown_series(values: pd.Series) -> pd.Series:
    if values.empty:
        return values
    peak = values.cummax()
    dd = (values - peak) / peak * 100.0
    return dd


def _max_drawdown_duration(values: pd.Series) -> int:
    if values.empty:
        return 0
    peak = values.cummax()
    dd = (values - peak) / peak
    if dd.empty:
        return 0
    trough_idx = int(dd.values.argmin())
    if trough_idx == 0:
        return 0
    # Peak value to compare against = peak at trough position
    peak_value_at_trough = peak.iloc[trough_idx]
    # Find the most recent peak that equals peak_value_at_trough at or before trough
    peak_idx = trough_idx
    while peak_idx > 0 and values.iloc[peak_idx] < peak_value_at_trough:
        peak_idx -= 1
    # Find recovery: first index after trough where value >= peak_value_at_trough
    recovery_idx: Optional[int] = None
    for j in range(trough_idx + 1, len(values)):
        if values.iloc[j] >= peak_value_at_trough:
            recovery_idx = j
            break
    end_idx = recovery_idx if recovery_idx is not None else len(values) - 1
    return int(end_idx - peak_idx)


def calculate_metrics(
    equity_curve: list[PortfolioSnapshot],
    trades: list[Trade],
    benchmark_curve: list[PortfolioSnapshot],
    starting_capital: float,
    risk_free_rate: float = 0.065,
) -> dict:
    """Build the full metrics dict consumed by the frontend dashboard."""
    if not equity_curve:
        return _empty_metrics(starting_capital)

    values = _to_value_series(equity_curve)
    bench = _to_value_series(benchmark_curve)
    starting_capital = float(starting_capital)

    final_value = float(values.iloc[-1])
    total_return_pct = (final_value - starting_capital) / starting_capital * 100.0

    start_dt = values.index[0]
    end_dt = values.index[-1]
    n_days = (end_dt - start_dt).days
    n_years = max(n_days / 365.25, 1.0 / 365.25)

    cagr_pct = (((final_value / starting_capital) ** (1.0 / n_years)) - 1.0) * 100.0 \
        if starting_capital > 0 and final_value > 0 else 0.0

    daily_returns = values.pct_change().dropna()
    if len(daily_returns) >= 2:
        ann_vol = float(daily_returns.std() * math.sqrt(TRADING_DAYS_PER_YEAR) * 100.0)
        excess_daily = daily_returns - (risk_free_rate / TRADING_DAYS_PER_YEAR)
        denom = float(excess_daily.std())
        sharpe = (float(excess_daily.mean()) / denom * math.sqrt(TRADING_DAYS_PER_YEAR)
                  if denom > 0 else 0.0)
        downside = daily_returns[daily_returns < 0]
        downside_dev = float(downside.std() * math.sqrt(TRADING_DAYS_PER_YEAR)) if not downside.empty else 0.0
        sortino = ((cagr_pct / 100.0 - risk_free_rate) / downside_dev
                   if downside_dev > 0 else 0.0)
        var_95 = float(np.percentile(daily_returns.values, 5) * 100.0)
    else:
        ann_vol = 0.0
        sharpe = 0.0
        sortino = 0.0
        var_95 = 0.0

    dd = _drawdown_series(values)
    max_dd_pct = float(dd.min()) if not dd.empty else 0.0
    max_dd_duration = _max_drawdown_duration(values)
    calmar = (cagr_pct / abs(max_dd_pct)) if abs(max_dd_pct) > 1e-9 else 0.0

    # Trade stats — only count completed (non-skipped) trades
    completed = [t for t in trades if not t.skipped and t.exit_date is not None
                 and t.gross_pnl is not None]
    skipped = [t for t in trades if t.skipped]
    open_trades = [t for t in trades if not t.skipped and t.exit_date is None]

    winning = [t for t in completed if (t.net_pnl or 0) > 0]
    losing = [t for t in completed if (t.net_pnl or 0) <= 0]

    total_trades = len(completed)
    win_rate = (len(winning) / total_trades * 100.0) if total_trades else 0.0

    avg_win = (sum((t.return_pct or 0) for t in winning) / len(winning)) if winning else 0.0
    avg_loss = (sum((t.return_pct or 0) for t in losing) / len(losing)) if losing else 0.0

    gross_profit = sum((t.gross_pnl or 0) for t in completed if (t.gross_pnl or 0) > 0)
    gross_loss = abs(sum((t.gross_pnl or 0) for t in completed if (t.gross_pnl or 0) < 0))
    if gross_loss > 0:
        profit_factor = gross_profit / gross_loss
    elif gross_profit > 0:
        profit_factor = float("inf")
    else:
        profit_factor = 0.0

    holding_days = [t.holding_days for t in completed if t.holding_days is not None]
    avg_hold = (sum(holding_days) / len(holding_days)) if holding_days else 0.0
    max_hold = max(holding_days) if holding_days else 0
    min_hold = min(holding_days) if holding_days else 0

    largest_win_pct = max(((t.return_pct or 0) for t in completed), default=0.0)
    largest_loss_pct = min(((t.return_pct or 0) for t in completed), default=0.0)
    avg_trade_return = (sum((t.return_pct or 0) for t in completed) / len(completed)) \
        if completed else 0.0

    total_brokerage = sum((t.brokerage or 0) + (t.brokerage_sell or 0)
                          for t in completed)
    total_stt = sum((t.stt_buy or 0) + (t.stt_sell or 0) for t in completed)

    # Benchmark
    if not bench.empty:
        bench_start = float(bench.iloc[0])
        bench_end = float(bench.iloc[-1])
        benchmark_return_pct = ((bench_end - bench_start) / bench_start * 100.0
                                if bench_start > 0 else 0.0)
        bench_n_years = max((bench.index[-1] - bench.index[0]).days / 365.25,
                             1.0 / 365.25)
        benchmark_cagr_pct = (((bench_end / bench_start) ** (1.0 / bench_n_years)) - 1.0) * 100.0 \
            if bench_start > 0 and bench_end > 0 else 0.0
        bench_dd = _drawdown_series(bench)
        bench_max_dd = float(bench_dd.min()) if not bench_dd.empty else 0.0
        bench_daily = bench.pct_change().dropna()
        if len(bench_daily) >= 2:
            bench_excess = bench_daily - (risk_free_rate / TRADING_DAYS_PER_YEAR)
            bench_denom = float(bench_excess.std())
            bench_sharpe = (float(bench_excess.mean()) / bench_denom * math.sqrt(TRADING_DAYS_PER_YEAR)
                            if bench_denom > 0 else 0.0)
        else:
            bench_sharpe = 0.0
    else:
        benchmark_return_pct = 0.0
        benchmark_cagr_pct = 0.0
        bench_max_dd = 0.0
        bench_sharpe = 0.0

    alpha = total_return_pct - benchmark_return_pct
    annualised_alpha = cagr_pct - benchmark_cagr_pct

    drawdown_records = [
        {"date": ts.strftime("%Y-%m-%d"), "drawdown_pct": round(_safe(v), 4)}
        for ts, v in zip(dd.index, dd.values)
    ]

    return {
        # Returns
        "total_return_pct": round(_safe(total_return_pct), 4),
        "cagr_pct": round(_safe(cagr_pct), 4),
        "benchmark_return_pct": round(_safe(benchmark_return_pct), 4),
        "alpha_pct": round(_safe(alpha), 4),
        "annualised_alpha_pct": round(_safe(annualised_alpha), 4),

        # Risk
        "max_drawdown_pct": round(_safe(max_dd_pct), 4),
        "max_drawdown_duration_days": int(max_dd_duration),
        "annualised_volatility_pct": round(_safe(ann_vol), 4),
        "sharpe_ratio": round(_safe(sharpe), 4),
        "sortino_ratio": round(_safe(sortino), 4),
        "calmar_ratio": round(_safe(calmar), 4),
        "value_at_risk_95": round(_safe(var_95), 4),

        # Trades
        "total_trades": int(total_trades),
        "winning_trades": int(len(winning)),
        "losing_trades": int(len(losing)),
        "win_rate_pct": round(_safe(win_rate), 4),
        "avg_winning_return_pct": round(_safe(avg_win), 4),
        "avg_losing_return_pct": round(_safe(avg_loss), 4),
        "profit_factor": (round(_safe(profit_factor), 4)
                           if not math.isinf(profit_factor) else 999.99),
        "avg_holding_days": round(_safe(avg_hold), 2),
        "max_holding_days": int(max_hold),
        "min_holding_days": int(min_hold),
        "largest_win_pct": round(_safe(largest_win_pct), 4),
        "largest_loss_pct": round(_safe(largest_loss_pct), 4),
        "avg_trade_return_pct": round(_safe(avg_trade_return), 4),
        "skipped_trades": int(len(skipped)),
        "open_trades": int(len(open_trades)),
        "total_brokerage_paid": round(_safe(total_brokerage), 2),
        "total_stt_paid": round(_safe(total_stt), 2),

        # Drawdown detail
        "drawdown_series": drawdown_records,

        # Period
        "test_period_days": int(n_days),
        "test_period_years": round(_safe(n_years), 2),
        "start_date": start_dt.strftime("%Y-%m-%d"),
        "end_date": end_dt.strftime("%Y-%m-%d"),

        # Benchmark comparison
        "outperformed_benchmark": bool(total_return_pct > benchmark_return_pct),
        "benchmark_max_drawdown_pct": round(_safe(bench_max_dd), 4),
        "benchmark_sharpe_ratio": round(_safe(bench_sharpe), 4),
        "benchmark_cagr_pct": round(_safe(benchmark_cagr_pct), 4),
    }


def _empty_metrics(starting_capital: float) -> dict:
    return {
        "total_return_pct": 0.0,
        "cagr_pct": 0.0,
        "benchmark_return_pct": 0.0,
        "alpha_pct": 0.0,
        "annualised_alpha_pct": 0.0,
        "max_drawdown_pct": 0.0,
        "max_drawdown_duration_days": 0,
        "annualised_volatility_pct": 0.0,
        "sharpe_ratio": 0.0,
        "sortino_ratio": 0.0,
        "calmar_ratio": 0.0,
        "value_at_risk_95": 0.0,
        "total_trades": 0,
        "winning_trades": 0,
        "losing_trades": 0,
        "win_rate_pct": 0.0,
        "avg_winning_return_pct": 0.0,
        "avg_losing_return_pct": 0.0,
        "profit_factor": 0.0,
        "avg_holding_days": 0.0,
        "max_holding_days": 0,
        "min_holding_days": 0,
        "largest_win_pct": 0.0,
        "largest_loss_pct": 0.0,
        "avg_trade_return_pct": 0.0,
        "skipped_trades": 0,
        "open_trades": 0,
        "total_brokerage_paid": 0.0,
        "total_stt_paid": 0.0,
        "drawdown_series": [],
        "test_period_days": 0,
        "test_period_years": 0.0,
        "start_date": "",
        "end_date": "",
        "outperformed_benchmark": False,
        "benchmark_max_drawdown_pct": 0.0,
        "benchmark_sharpe_ratio": 0.0,
        "benchmark_cagr_pct": 0.0,
    }
