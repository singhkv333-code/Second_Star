"""Calendar SIP backtester.

Strategy: fixed-rupee buy on a recurring cadence (daily / weekly on a
given weekday / monthly on a given day-of-month). Each contribution
buys whole-rupee fractional shares at the bar's close, net of friction.
Equity is the cash that has been deployed plus the live mark-to-market
on accumulated shares.

Distinct from the indicator and open/close backtesters in this folder —
those are signal- and intraday-driven; this one is purely calendar-
driven dollar-cost averaging. It exists so prompts like
"backtest SIP into HDFCBANK monthly for 1 year" run deterministically
without going through the LLM.

Benchmark: lump-sum buy-and-hold of the same TOTAL contribution at the
window's first close. That's the real-world SIP-vs-lump-sum question,
not "vs cash" — surfacing it lets the chat answer the underlying ask.

Result shape matches the chat surface's FinancialBacktestPayload (see
pivot-next/components/chat/FinancialBacktestCard.tsx) so the FE renders
it without a new component.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal

import pandas as pd
import yfinance as yf  # type: ignore[import-untyped]

logger = logging.getLogger(__name__)


# 10 bps slippage + brokerage on every buy. SIPs only buy, so there is
# no symmetric exit-side drag like in the open/close backtester.
_FRICTION = 0.001
_DEFAULT_INSTALLMENT = 10_000.0


_FrequencyLiteral = Literal["daily", "weekly", "monthly"]


@dataclass
class CalendarSIPBacktestResult:
    symbol: str
    frequency: str
    day_of_week: int | None
    day_of_month: int | None
    installment: float
    period_label: str
    start_iso: str
    end_iso: str
    equity_curve: list[dict]
    benchmark_curve: list[dict]
    metrics: dict[str, Any]
    n_trades: int
    summary_text: str


def run_calendar_sip_backtest(
    *,
    symbol: str,
    frequency: _FrequencyLiteral = "monthly",
    day_of_week: int | None = None,
    day_of_month: int | None = None,
    installment: float = _DEFAULT_INSTALLMENT,
    period: str = "1y",
    exchange: str = "NSE",
) -> CalendarSIPBacktestResult:
    """Run a calendar-driven SIP backtest.

    ``day_of_week``: 0=Monday … 6=Sunday. Used for ``frequency='weekly'``;
    defaults to Monday.
    ``day_of_month``: 1..28. Used for ``frequency='monthly'``; defaults
    to 1. We cap at 28 because some months don't have a 29th–31st and
    the contribution would silently slip.

    Raises ValueError on bad inputs / no data — the chat layer catches
    this and surfaces a friendly message.
    """
    sym = symbol.upper().strip()
    suffix = ".NS" if exchange.upper() == "NSE" else ".BO"
    yf_sym = sym if sym.endswith((".NS", ".BO")) else f"{sym}{suffix}"

    hist = yf.Ticker(yf_sym).history(period=period, interval="1d")
    if hist.empty or len(hist) < 5:
        raise ValueError(
            f"insufficient data for {sym} over {period} (got {len(hist)} bars)"
        )

    closes = hist["Close"].astype(float)

    contribution_mask = _contribution_mask(
        closes.index, frequency, day_of_week, day_of_month,
    )
    if not contribution_mask.any():
        raise ValueError(
            f"no contribution dates fell inside the {period} window — "
            f"try a longer window"
        )

    # Walk each bar. On contribution bars, deploy ``installment``
    # rupees (net of friction) into shares at the bar's close. Equity
    # at every bar = cumulative shares × close.
    shares = 0.0
    cash_invested = 0.0
    n_trades = 0
    equity_values: list[float] = []
    invested_values: list[float] = []

    for ts, close in closes.items():
        if contribution_mask.loc[ts]:
            shares += (installment * (1 - _FRICTION)) / float(close)
            cash_invested += installment
            n_trades += 1
        equity_values.append(shares * float(close))
        invested_values.append(cash_invested)

    equity_series = pd.Series(equity_values, index=closes.index)

    # Benchmark: lump-sum the TOTAL invested at the first close,
    # ride buy-and-hold. Same total rupees deployed, so the chart
    # answers "did averaging-in beat going all-in?"
    total_invested = cash_invested
    if total_invested <= 0:
        raise ValueError("no contributions made — check frequency/period")
    bench_shares = (total_invested * (1 - _FRICTION)) / float(closes.iloc[0])
    bench_values = (closes * bench_shares).round(2)

    equity_curve = [
        {"date": ts.date().isoformat(), "value": round(float(v), 2)}
        for ts, v in equity_series.items()
    ]
    benchmark_curve = [
        {"date": ts.date().isoformat(), "value": float(v)}
        for ts, v in bench_values.items()
    ]

    metrics = _compute_metrics(
        equity_series=equity_series,
        invested_values=invested_values,
        bench_values=bench_values,
        total_invested=total_invested,
    )

    start_iso = equity_curve[0]["date"]
    end_iso = equity_curve[-1]["date"]
    summary_text = _format_summary(
        sym, frequency, day_of_week, day_of_month, installment,
        period, metrics, n_trades, start_iso, end_iso, total_invested,
    )

    return CalendarSIPBacktestResult(
        symbol=sym,
        frequency=frequency,
        day_of_week=day_of_week,
        day_of_month=day_of_month,
        installment=installment,
        period_label=period,
        start_iso=start_iso,
        end_iso=end_iso,
        equity_curve=equity_curve,
        benchmark_curve=benchmark_curve,
        metrics=metrics,
        n_trades=n_trades,
        summary_text=summary_text,
    )


def _contribution_mask(
    index: pd.DatetimeIndex,
    frequency: str,
    day_of_week: int | None,
    day_of_month: int | None,
) -> pd.Series:
    """For each trading day in ``index``, return True if that bar is a
    SIP contribution day.

    Weekly: first trading day on/after the requested weekday in each
    ISO week. Monthly: first trading day on/after the requested
    day-of-month within each calendar month. This handles weekends and
    market holidays — the contribution simply slips to the next open
    bar, mirroring how brokers actually execute SIPs.
    """
    if frequency == "daily":
        return pd.Series(True, index=index)

    if frequency == "weekly":
        target_dow = 0 if day_of_week is None else int(day_of_week)
        if not 0 <= target_dow <= 6:
            raise ValueError(f"day_of_week must be 0..6, got {target_dow}")
        # First trading day per ISO week whose weekday >= target.
        dows = index.dayofweek
        weeks = index.isocalendar().week.astype(int) + index.isocalendar().year.astype(int) * 100
        mask = pd.Series(False, index=index)
        seen: set[int] = set()
        for ts, w, dow in zip(index, weeks, dows):
            if w in seen:
                continue
            if dow >= target_dow:
                mask.loc[ts] = True
                seen.add(int(w))
        return mask

    if frequency == "monthly":
        target_dom = 1 if day_of_month is None else int(day_of_month)
        if not 1 <= target_dom <= 28:
            raise ValueError(f"day_of_month must be 1..28, got {target_dom}")
        # First trading day per (year, month) whose day >= target.
        ym = index.year.astype(int) * 100 + index.month.astype(int)
        days = index.day.astype(int)
        mask = pd.Series(False, index=index)
        seen: set[int] = set()
        for ts, key, dom in zip(index, ym, days):
            if key in seen:
                continue
            if dom >= target_dom:
                mask.loc[ts] = True
                seen.add(int(key))
        return mask

    raise ValueError(f"unsupported frequency: {frequency}")


def _compute_metrics(
    *,
    equity_series: pd.Series,
    invested_values: list[float],
    bench_values: pd.Series,
    total_invested: float,
) -> dict[str, Any]:
    """Annualised metrics on the SIP equity curve.

    Total return is on TOTAL contributions, not on a starting capital
    of one — averaging-in deploys cash over time, so the natural
    denominator is the rupees actually committed by the end. CAGR uses
    a money-weighted approximation: time-weighted years of the average
    contribution against the final equity.
    """
    n_days = len(equity_series)
    years = n_days / 252.0 if n_days > 0 else 1e-9

    ending_equity = float(equity_series.iloc[-1])
    total_return_pct = (ending_equity / total_invested - 1) * 100

    # Money-weighted CAGR: dollar-weighted by the per-bar invested
    # capital. Keeps a SIP that put most cash in late from looking
    # like it had years to compound.
    invested_arr = pd.Series(invested_values, index=equity_series.index)
    avg_invested = invested_arr.mean()
    money_weighted_years = (
        years * avg_invested / total_invested if total_invested > 0 else years
    )
    money_weighted_years = max(money_weighted_years, 1 / 252)
    cagr_pct = (
        (ending_equity / total_invested) ** (1 / money_weighted_years) - 1
    ) * 100

    # Max drawdown — peak-to-trough on the equity curve. For a SIP
    # this slightly overstates pain because new contributions push
    # equity up irrespective of price; but it matches what the
    # user actually sees in their statement.
    rolling_peak = equity_series.cummax()
    drawdowns = (equity_series - rolling_peak) / rolling_peak.replace(0, pd.NA)
    max_dd_pct = float(drawdowns.min(skipna=True)) * 100 if drawdowns.notna().any() else 0.0

    bench_total_pct = (float(bench_values.iloc[-1]) / total_invested - 1) * 100

    return {
        "total_return_pct": round(float(total_return_pct), 2),
        "cagr_pct": round(float(cagr_pct), 2),
        "max_drawdown_pct": round(max_dd_pct, 2),
        "sharpe": None,  # noisy on monthly cadences; intentionally skip
        "hit_rate_pct": None,
        "calmar": (
            round(float(cagr_pct) / abs(float(max_dd_pct)), 2)
            if max_dd_pct < 0 else None
        ),
        "turnover_pct": None,
        "n_unique_companies": 1,
        "bench_total_return_pct": round(float(bench_total_pct), 2),
        "total_invested": round(float(total_invested), 2),
        "ending_value": round(float(ending_equity), 2),
    }


def _format_summary(
    symbol: str,
    frequency: str,
    day_of_week: int | None,
    day_of_month: int | None,
    installment: float,
    period: str,
    metrics: dict,
    n_trades: int,
    start_iso: str,
    end_iso: str,
    total_invested: float,
) -> str:
    cadence = _format_cadence(frequency, day_of_week, day_of_month)
    bench = metrics["bench_total_return_pct"]
    delta = metrics["total_return_pct"] - bench
    lump_vs_sip = (
        f"Lump-sum buy & hold (same ₹{total_invested:,.0f} deployed at the start) "
        f"would have returned **{bench:+.2f}%** "
        f"(SIP vs lump-sum: {delta:+.2f} pp)."
    )
    return (
        f"Backtested **₹{installment:,.0f} {cadence} SIP** into `{symbol}` "
        f"from {start_iso} to {end_iso} ({n_trades} contributions, "
        f"₹{total_invested:,.0f} total deployed).\n\n"
        f"- SIP total return: **{metrics['total_return_pct']:+.2f}%** "
        f"(money-weighted CAGR {metrics['cagr_pct']:+.2f}%)\n"
        f"- {lump_vs_sip}\n"
        f"- Max drawdown on the SIP equity: {metrics['max_drawdown_pct']:.2f}%\n\n"
        "Past performance does not guarantee future results. SIPs smooth "
        "entry timing but inherit the underlying's downside."
    )


_WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _format_cadence(
    frequency: str, day_of_week: int | None, day_of_month: int | None,
) -> str:
    if frequency == "daily":
        return "daily"
    if frequency == "weekly":
        dow = 0 if day_of_week is None else int(day_of_week)
        return f"weekly (every {_WEEKDAYS[dow]})"
    if frequency == "monthly":
        dom = 1 if day_of_month is None else int(day_of_month)
        return f"monthly (on day {dom})"
    return frequency
