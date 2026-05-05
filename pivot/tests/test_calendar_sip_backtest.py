"""Tests for the calendar SIP backtester.

Two layers:
  1. NL parser tests against ``_parse_calendar_sip_backtest`` — guard
     the chat router's deterministic short-circuit for the
     "backtest SIP into <SYMBOL>" shape.
  2. Service tests against ``run_calendar_sip_backtest`` with a
     synthetic OHLCV frame patched into yfinance — verify cadence
     selection, accumulation arithmetic, and the FE-shaped result.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from backend.routers.chat import _parse_calendar_sip_backtest
from backend.services.calendar_sip_backtest import (
    _contribution_mask,
    run_calendar_sip_backtest,
)


# ── NL parser ──────────────────────────────────────────────────────


def test_parser_basic_monthly_into_symbol() -> None:
    parsed = _parse_calendar_sip_backtest(
        "backtest SIP into HDFCBANK monthly for 1 year",
    )
    assert parsed is not None
    assert parsed["symbol"] == "HDFCBANK"
    assert parsed["frequency"] == "monthly"
    assert parsed["years"] == 1


def test_parser_weekly_with_weekday() -> None:
    parsed = _parse_calendar_sip_backtest(
        "backtest 5000 SIP into RELIANCE every Monday for 3 years",
    )
    assert parsed is not None
    assert parsed["symbol"] == "RELIANCE"
    assert parsed["frequency"] == "weekly"
    assert parsed["day_of_week"] == 0
    assert parsed["installment"] == 5000.0
    assert parsed["years"] == 3


def test_parser_explicit_day_of_month() -> None:
    parsed = _parse_calendar_sip_backtest(
        "backtest SIP into INFY on the 5th for 2 years",
    )
    assert parsed is not None
    assert parsed["frequency"] == "monthly"
    assert parsed["day_of_month"] == 5


def test_parser_default_frequency_is_monthly() -> None:
    parsed = _parse_calendar_sip_backtest(
        "backtest SIP into ICICIBANK for 1 year",
    )
    assert parsed is not None
    assert parsed["frequency"] == "monthly"
    # No weekday or DOM hinted → fallback values stay None.
    assert parsed["day_of_week"] is None
    assert parsed["day_of_month"] is None


def test_parser_ignores_non_sip_prompts() -> None:
    assert _parse_calendar_sip_backtest(
        "backtest pe_ratio < 15 from 2020 to 2024",
    ) is None
    assert _parse_calendar_sip_backtest(
        "backtest RELIANCE when its RSI drops below 30",
    ) is None


def test_parser_caps_day_of_month_at_28() -> None:
    parsed = _parse_calendar_sip_backtest(
        "backtest SIP into TCS on the 31st for 1 year",
    )
    assert parsed is not None
    # Capped — months without a 31st would otherwise silently slip.
    assert parsed["day_of_month"] == 28


# ── Contribution mask ──────────────────────────────────────────────


def _trading_index(start: str, periods: int) -> pd.DatetimeIndex:
    """Mon–Fri only — yfinance's daily history skips weekends."""
    return pd.bdate_range(start=start, periods=periods, freq="C")


def test_contribution_mask_monthly_picks_one_per_month() -> None:
    idx = _trading_index("2024-01-01", 90)  # ~4.3 months of weekdays
    mask = _contribution_mask(idx, "monthly", None, day_of_month=1)
    months_selected = sorted({(ts.year, ts.month) for ts in idx[mask]})
    months_present = sorted({(ts.year, ts.month) for ts in idx})
    assert months_selected == months_present


def test_contribution_mask_weekly_picks_one_per_week() -> None:
    idx = _trading_index("2024-01-01", 30)
    mask = _contribution_mask(idx, "weekly", day_of_week=0, day_of_month=None)
    # Each (year, iso-week) should yield exactly one contribution.
    weeks = [(ts.isocalendar().year, ts.isocalendar().week) for ts in idx[mask]]
    assert len(weeks) == len(set(weeks))


def test_contribution_mask_daily_is_every_bar() -> None:
    idx = _trading_index("2024-01-01", 10)
    mask = _contribution_mask(idx, "daily", None, None)
    assert mask.all()


# ── Full service run ───────────────────────────────────────────────


def _fake_history(n_bars: int = 252) -> pd.DataFrame:
    """Linear-ish price ramp from 100 → 200 over `n_bars` weekdays.
    A monotonic ramp gives a positive SIP return AND a positive
    lump-sum benchmark, so we can sanity-check ordering."""
    idx = _trading_index("2024-01-01", n_bars)
    closes = pd.Series(
        [100.0 + (100.0 * i / max(n_bars - 1, 1)) for i in range(n_bars)],
        index=idx,
    )
    return pd.DataFrame({
        "Open": closes,
        "High": closes,
        "Low": closes,
        "Close": closes,
    })


def test_run_monthly_sip_returns_fe_shaped_payload() -> None:
    fake_df = _fake_history(252)
    fake_ticker = MagicMock()
    fake_ticker.history.return_value = fake_df

    with patch(
        "backend.services.calendar_sip_backtest.yf.Ticker",
        return_value=fake_ticker,
    ):
        result = run_calendar_sip_backtest(
            symbol="HDFCBANK",
            frequency="monthly",
            day_of_month=1,
            installment=10_000.0,
            period="1y",
        )

    assert result.symbol == "HDFCBANK"
    assert result.frequency == "monthly"
    # ~12 monthly contributions in a 252-bar (1y) window.
    assert 10 <= result.n_trades <= 13
    # FE shape sanity.
    assert result.equity_curve and isinstance(result.equity_curve[0]["value"], float)
    assert result.benchmark_curve and isinstance(result.benchmark_curve[0]["value"], float)
    # On a monotonic up ramp, SIP underperforms lump-sum (lump-sum
    # buys all shares at 100, SIP averages in higher). Verify the
    # ordering, not absolute numbers.
    assert (
        result.metrics["bench_total_return_pct"]
        > result.metrics["total_return_pct"]
    )
    assert "SIP" in result.summary_text
    assert "HDFCBANK" in result.summary_text


def test_run_raises_when_history_too_short() -> None:
    fake_ticker = MagicMock()
    fake_ticker.history.return_value = pd.DataFrame()  # empty

    with patch(
        "backend.services.calendar_sip_backtest.yf.Ticker",
        return_value=fake_ticker,
    ):
        with pytest.raises(ValueError, match="insufficient data"):
            run_calendar_sip_backtest(symbol="ZZZ", period="1y")
