"""Open→close intraday roundtrip backtester.

Strategy: every trading day, buy at the day's OPEN, sell at the day's
CLOSE. Cumulative equity = compound product of intraday returns,
minus friction per side. Benchmark = buy-and-hold over the same
window.

Distinct from `indicator_backtest.py` (RSI/SMA/EMA on daily close)
and from the fundamentals backtester (universe-level expression on
financial ratios). Runs entirely off yfinance daily OHLCV data — no
DB, no broker.

Result shape mirrors the chat surface's `FinancialBacktestPayload`
(see pivot-next/components/chat/FinancialBacktestCard.tsx) so the
existing FE card renders without a new component.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import pandas as pd
import yfinance as yf  # type: ignore[import-untyped]

logger = logging.getLogger(__name__)


# 10 bps slippage + brokerage per side, applied on every leg. Two legs
# per day (buy + sell) means ~20 bps round-trip drag — meaningful for
# a strategy with daily turnover, which is the whole point of surfacing
# friction here rather than hiding it.
_FRICTION = 0.001
_STARTING_CAPITAL = 1_000_000.0


@dataclass
class OpenCloseBacktestResult:
    symbol: str
    period_label: str
    start_iso: str
    end_iso: str
    equity_curve: list[dict]
    benchmark_curve: list[dict]
    metrics: dict[str, Any]
    n_trades: int
    summary_text: str


def run_open_close_backtest(
    *,
    symbol: str,
    period: str = "5y",
    exchange: str = "NSE",
) -> OpenCloseBacktestResult:
    """Backtest "buy at open, sell at close" every trading day.

    Raises ValueError on bad inputs / no data — the chat layer
    catches this and surfaces a friendly message.
    """
    sym = symbol.upper().strip()
    suffix = ".NS" if exchange.upper() == "NSE" else ".BO"
    yf_sym = sym if sym.endswith((".NS", ".BO")) else f"{sym}{suffix}"

    hist = yf.Ticker(yf_sym).history(period=period, interval="1d")
    if hist.empty or len(hist) < 30:
        raise ValueError(
            f"insufficient data for {sym} over {period} (got {len(hist)} bars)"
        )

    opens = hist["Open"].astype(float)
    closes = hist["Close"].astype(float)

    # Daily intraday return = (close - open) / open. Net of friction
    # on both legs: (1 - f) * (close/open) * (1 - f) - 1.
    daily_gross = closes / opens
    daily_net = daily_gross * (1 - _FRICTION) ** 2

    # Equity curve: compound the per-day net multiplier.
    equity_multipliers = daily_net.cumprod()
    equity_values = (equity_multipliers * _STARTING_CAPITAL).round(2)

    # Benchmark: buy-and-hold from first close. Same dollar starting
    # capital so the chart compares apples-to-apples.
    bench_multipliers = closes / closes.iloc[0]
    bench_values = (bench_multipliers * _STARTING_CAPITAL).round(2)

    equity_curve = [
        {"date": ts.date().isoformat(), "value": float(v)}
        for ts, v in equity_values.items()
    ]
    benchmark_curve = [
        {"date": ts.date().isoformat(), "value": float(v)}
        for ts, v in bench_values.items()
    ]

    metrics = _compute_metrics(equity_values, daily_net, bench_values)

    n_trades = int(len(daily_net))
    start_iso = equity_curve[0]["date"]
    end_iso = equity_curve[-1]["date"]
    summary_text = _format_summary(sym, period, metrics, n_trades, start_iso, end_iso)

    return OpenCloseBacktestResult(
        symbol=sym,
        period_label=period,
        start_iso=start_iso,
        end_iso=end_iso,
        equity_curve=equity_curve,
        benchmark_curve=benchmark_curve,
        metrics=metrics,
        n_trades=n_trades,
        summary_text=summary_text,
    )


def _compute_metrics(
    equity_values: pd.Series,
    daily_net: pd.Series,
    bench_values: pd.Series,
) -> dict[str, Any]:
    """Annualised metrics on the equity curve. Sharpe uses daily
    excess returns (rf=0) annualised √252. Hit-rate is the % of days
    where the intraday roundtrip was profitable AFTER friction."""
    n_days = len(daily_net)
    years = n_days / 252.0 if n_days > 0 else 1e-9

    total_return_pct = (equity_values.iloc[-1] / _STARTING_CAPITAL - 1) * 100
    cagr_pct = ((equity_values.iloc[-1] / _STARTING_CAPITAL) ** (1 / years) - 1) * 100

    daily_returns = daily_net - 1
    if daily_returns.std() > 0:
        sharpe = (daily_returns.mean() / daily_returns.std()) * (252 ** 0.5)
        sharpe_val: float | None = round(float(sharpe), 2)
    else:
        sharpe_val = None

    # Max drawdown — peak-to-trough on the equity curve.
    rolling_peak = equity_values.cummax()
    drawdowns = (equity_values - rolling_peak) / rolling_peak
    max_dd_pct = float(drawdowns.min()) * 100

    hit_rate_pct = float((daily_returns > 0).sum()) / max(n_days, 1) * 100

    bench_total = (bench_values.iloc[-1] / _STARTING_CAPITAL - 1) * 100

    return {
        "total_return_pct": round(float(total_return_pct), 2),
        "cagr_pct": round(float(cagr_pct), 2),
        "max_drawdown_pct": round(max_dd_pct, 2),
        "sharpe": sharpe_val,
        "hit_rate_pct": round(hit_rate_pct, 1),
        "calmar": (
            round(float(cagr_pct) / abs(float(max_dd_pct)), 2)
            if max_dd_pct < 0 else None
        ),
        "turnover_pct": None,
        "n_unique_companies": 1,
        "bench_total_return_pct": round(float(bench_total), 2),
    }


def _format_summary(
    symbol: str, period: str, metrics: dict, n_trades: int,
    start_iso: str, end_iso: str,
) -> str:
    bench = metrics["bench_total_return_pct"]
    delta = metrics["total_return_pct"] - bench
    return (
        f"Backtested **buy at open / sell at close** on `{symbol}` "
        f"from {start_iso} to {end_iso} ({n_trades} trading days).\n\n"
        f"- Strategy total return: **{metrics['total_return_pct']:+.2f}%** "
        f"(CAGR {metrics['cagr_pct']:+.2f}%)\n"
        f"- Buy & hold benchmark: **{bench:+.2f}%** "
        f"(strategy vs buy&hold: {delta:+.2f} pp)\n"
        f"- Hit rate: {metrics['hit_rate_pct']:.0f}% of days profitable "
        f"after friction\n"
        f"- Max drawdown: {metrics['max_drawdown_pct']:.2f}%\n"
        f"- Sharpe (annualised, rf=0): "
        f"{metrics['sharpe'] if metrics['sharpe'] is not None else '—'}\n\n"
        "Daily turnover compounds friction (~20 bps round-trip in this run). "
        "Past performance does not guarantee future results."
    )


# ── Weekly close → next-week open swing ────────────────────────────


def run_weekly_swing_backtest(
    *,
    symbol: str,
    period: str = "5y",
    exchange: str = "NSE",
) -> OpenCloseBacktestResult:
    """Backtest "buy at last trading day's CLOSE of each week, sell at
    NEXT week's OPEN" — a weekend-hold / weekend-gap strategy.

    Each trade spans Friday-close → Monday-open (or whatever the
    actual last/first trading days are when there are mid-week
    holidays). yfinance daily bars give us closes and opens; we
    resample to weekly to find the weekly anchor bars, then walk
    the per-week return chain.

    Result shape mirrors `run_open_close_backtest` so the same
    FinancialBacktestCard renders without a new component.
    """
    sym = symbol.upper().strip()
    suffix = ".NS" if exchange.upper() == "NSE" else ".BO"
    yf_sym = sym if sym.endswith((".NS", ".BO")) else f"{sym}{suffix}"

    hist = yf.Ticker(yf_sym).history(period=period, interval="1d")
    if hist.empty or len(hist) < 30:
        raise ValueError(
            f"insufficient data for {sym} over {period} (got {len(hist)} bars)"
        )

    # Build (week_close, next_week_open) pairs by walking the daily
    # bars and taking the LAST close of each ISO week and the FIRST
    # open of the FOLLOWING week. This handles mid-week holidays
    # cleanly (we just use whatever bars exist).
    hist = hist.sort_index()
    hist_idx = hist.index
    iso = hist_idx.isocalendar()
    # Pandas ≥ 2.0 returns a DataFrame; older versions returned a tuple.
    try:
        year = iso["year"]; week = iso["week"]
    except Exception:
        year = pd.Index([d.isocalendar()[0] for d in hist_idx])
        week = pd.Index([d.isocalendar()[1] for d in hist_idx])
    week_key = [f"{int(y)}-{int(w):02d}" for y, w in zip(year, week)]

    last_close_by_week: dict[str, tuple[Any, float]] = {}
    first_open_by_week: dict[str, tuple[Any, float]] = {}
    for ts, key, close, open_ in zip(
        hist_idx, week_key, hist["Close"].astype(float), hist["Open"].astype(float),
    ):
        # Last close wins (we keep overwriting as we walk forward).
        last_close_by_week[key] = (ts, float(close))
        # First open wins (only set if not already present).
        if key not in first_open_by_week:
            first_open_by_week[key] = (ts, float(open_))

    weeks_sorted = sorted(last_close_by_week.keys())
    if len(weeks_sorted) < 4:
        raise ValueError(
            f"insufficient data for {sym} over {period} "
            f"(only {len(weeks_sorted)} weeks)"
        )

    # Walk pairs of consecutive weeks: enter at week_i close, exit at
    # week_{i+1} open. Per-trade return = open_{i+1} / close_i, with
    # friction applied on both legs.
    trade_ts: list[pd.Timestamp] = []
    daily_net_list: list[float] = []
    for i in range(len(weeks_sorted) - 1):
        w_close_ts, w_close = last_close_by_week[weeks_sorted[i]]
        w_open_ts, w_open = first_open_by_week[weeks_sorted[i + 1]]
        if w_close <= 0 or w_open <= 0:
            continue
        gross = w_open / w_close
        net = gross * (1 - _FRICTION) ** 2
        trade_ts.append(w_open_ts)        # mark trade at exit timestamp
        daily_net_list.append(float(net))

    if not daily_net_list:
        raise ValueError(f"no valid weekly trades for {sym} over {period}")

    daily_net = pd.Series(daily_net_list, index=pd.to_datetime(trade_ts))
    equity_multipliers = daily_net.cumprod()
    equity_values = (equity_multipliers * _STARTING_CAPITAL).round(2)

    closes = hist["Close"].astype(float)
    bench_multipliers = closes / closes.iloc[0]
    bench_values = (bench_multipliers * _STARTING_CAPITAL).round(2)

    equity_curve = [
        {"date": ts.date().isoformat(), "value": float(v)}
        for ts, v in equity_values.items()
    ]
    benchmark_curve = [
        {"date": ts.date().isoformat(), "value": float(v)}
        for ts, v in bench_values.items()
    ]

    metrics = _compute_metrics(equity_values, daily_net, bench_values)

    n_trades = int(len(daily_net))
    start_iso = equity_curve[0]["date"]
    end_iso = equity_curve[-1]["date"]
    summary_text = (
        f"Backtested **buy at last trading day's close / sell at "
        f"next week's open** on `{sym}` from {start_iso} to {end_iso} "
        f"({n_trades} weekly trades).\n\n"
        f"- Strategy total return: **{metrics['total_return_pct']:+.2f}%** "
        f"(CAGR {metrics['cagr_pct']:+.2f}%)\n"
        f"- Buy & hold benchmark: **{metrics['bench_total_return_pct']:+.2f}%** "
        f"(strategy vs buy&hold: "
        f"{metrics['total_return_pct'] - metrics['bench_total_return_pct']:+.2f} pp)\n"
        f"- Hit rate: {metrics['hit_rate_pct']:.0f}% of weeks profitable "
        f"after friction\n"
        f"- Max drawdown: {metrics['max_drawdown_pct']:.2f}%\n"
        f"- Sharpe: "
        f"{metrics['sharpe'] if metrics['sharpe'] is not None else '—'}\n\n"
        "Each trade spans the weekend; friction (~20 bps round-trip) "
        "applies once per week instead of once per day. Past "
        "performance does not guarantee future results."
    )

    return OpenCloseBacktestResult(
        symbol=sym,
        period_label=period,
        start_iso=start_iso,
        end_iso=end_iso,
        equity_curve=equity_curve,
        benchmark_curve=benchmark_curve,
        metrics=metrics,
        n_trades=n_trades,
        summary_text=summary_text,
    )
