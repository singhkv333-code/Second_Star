"""Shared backtest performance metrics — one definition, used by every engine.

Before this (2026-05-29 audit) Sharpe/Sortino were hardcoded `None` in three
engines and `open_close_backtest` had its own copy; CAGR was annualized
inconsistently (calendar-year in two engines, trading-days/252 in two others).
This module is the single source for the risk-adjusted metrics + the
methodology note rendered on every backtest card.
"""
from __future__ import annotations

import math
from datetime import date, datetime
from typing import Optional, Sequence

# India risk-free proxy (10Y G-Sec ~6.5%). Daily rate = annual / 252.
DEFAULT_RF_ANNUAL = 0.065
_TRADING_DAYS = 252


def sharpe_sortino(
    daily_returns: Sequence[float],
    *,
    rf_annual: float = DEFAULT_RF_ANNUAL,
    periods_per_year: float = _TRADING_DAYS,
) -> tuple[Optional[float], Optional[float]]:
    """(sharpe, sortino) annualized from a per-period return series (fractions).

    Sharpe   = mean(excess) / std(excess)            × √P
    Sortino  = mean(excess) / downside_deviation     × √P
      where downside_deviation = sqrt(mean(min(excess, 0)²)) (target = rf),
      and P = ``periods_per_year`` — 252 for daily bars (default), 52 for
      weekly, ~1638 for 60-minute NSE bars, etc. Annualizing a weekly series
      at √252 (instead of √52) overstates Sharpe by ~2.2×, so non-daily
      callers MUST pass the right cadence.
    Returns (None, None) when there are too few points or zero dispersion."""
    rets = [float(r) for r in daily_returns if r is not None and math.isfinite(float(r))]
    n = len(rets)
    if n < 2:
        return None, None
    ppy = float(periods_per_year) if periods_per_year and periods_per_year > 0 else _TRADING_DAYS
    rf_daily = rf_annual / ppy
    excess = [r - rf_daily for r in rets]
    mean = sum(excess) / n
    var = sum((e - mean) ** 2 for e in excess) / (n - 1)
    std = math.sqrt(var)
    ann = math.sqrt(ppy)
    sharpe = (mean / std) * ann if std > 1e-12 else None
    downside_sq = sum((e if e < 0 else 0.0) ** 2 for e in excess) / n
    dd = math.sqrt(downside_sq)
    sortino = (mean / dd) * ann if dd > 1e-12 else None
    return (
        round(sharpe, 2) if sharpe is not None else None,
        round(sortino, 2) if sortino is not None else None,
    )


def daily_returns_from_equity(equity: Sequence[float]) -> list[float]:
    """Period-over-period fractional returns from an equity curve."""
    out: list[float] = []
    prev: Optional[float] = None
    for v in equity:
        try:
            cur = float(v)
        except (TypeError, ValueError):
            continue
        if prev is not None and prev > 0:
            out.append(cur / prev - 1.0)
        prev = cur
    return out


def calendar_cagr_pct(
    start_value: float, end_value: float,
    start: date | datetime | str, end: date | datetime | str,
) -> float:
    """CAGR (%) on a CALENDAR-year basis (the professional convention) —
    (end/start)^(1/years) - 1, years = calendar days / 365.25, floored at 1 day."""
    def _d(x) -> date:
        if isinstance(x, datetime):
            return x.date()
        if isinstance(x, date):
            return x
        s = str(x).replace("Z", "").split("T")[0]
        return datetime.fromisoformat(s[:10]).date()
    try:
        days = max((_d(end) - _d(start)).days, 1)
    except Exception:
        return 0.0
    years = days / 365.25
    if start_value <= 0 or years <= 0:
        return 0.0
    return (pow(end_value / start_value, 1.0 / years) - 1.0) * 100.0


def methodology_note(
    *,
    start: object = None,
    end: object = None,
    period_label: str = "",
) -> dict:
    """Standard methodology block surfaced on every backtest card so the user
    knows the window, that results are after realistic costs, the bar basis,
    and the survivorship caveat. Pulls the live round-trip bps from
    trading_costs so the number stays in sync."""
    from backend.services.trading_costs import round_trip_bps, slippage_bps
    if start and end:
        window = f"{start} → {end}"
    elif period_label:
        window = period_label
    else:
        window = "—"
    return {
        "window": window,
        "costs": (
            f"after costs (~{round_trip_bps():.0f} bps round-trip incl. "
            f"~{slippage_bps():.0f} bps slippage, STT, exchange, GST, stamp)"
        ),
        "basis": (
            "daily-bar OHLCV (Kite primary, yfinance fallback; "
            "split-adjusted, dividends not reinvested)"
        ),
        "caveat": (
            "Point-in-time for the current ticker only; no survivorship "
            "adjustment. Past performance is not indicative of future results."
        ),
    }
