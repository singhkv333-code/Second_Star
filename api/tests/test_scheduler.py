"""
Tests for scheduler time utilities.
All assertions check IST format including the literal string "IST".
Env setup is handled by conftest.py.
"""

from datetime import datetime
import pytz

from backend.utils.time_utils import (
    now_ist, to_ist, format_ist, format_ist_short, format_ist_date,
    is_market_open, is_trading_day, next_monthly_execution,
    next_weekly_execution, next_daily_execution, IST,
)


def test_format_ist_always_ends_with_IST():
    """Every formatted time must end with the literal string 'IST'."""
    dt = datetime(2026, 5, 1, 9, 15, 0, tzinfo=pytz.utc)
    result = format_ist(dt)
    assert result.endswith("IST"), f"Expected 'IST' suffix, got: {result}"


def test_format_ist_short_always_ends_with_IST():
    dt = datetime(2026, 5, 1, 9, 15, 0, tzinfo=pytz.utc)
    result = format_ist_short(dt)
    assert result.endswith("IST"), f"Expected 'IST' suffix, got: {result}"


def test_format_ist_date_always_ends_with_IST():
    dt = datetime(2026, 5, 1, 9, 15, 0, tzinfo=pytz.utc)
    result = format_ist_date(dt)
    assert result.endswith("IST"), f"Expected 'IST' suffix, got: {result}"


def test_utc_to_ist_conversion():
    """9:15 AM IST = 3:45 AM UTC."""
    utc_dt = datetime(2026, 5, 1, 3, 45, 0, tzinfo=pytz.utc)
    ist_dt = to_ist(utc_dt)
    assert ist_dt.hour == 9
    assert ist_dt.minute == 15
    assert "IST" in format_ist(ist_dt)


def test_naive_datetime_assumed_utc():
    """Naive datetimes (from DB) should be treated as UTC and converted to IST."""
    naive = datetime(2026, 5, 1, 3, 45, 0)
    ist_dt = to_ist(naive)
    assert ist_dt.hour == 9
    assert ist_dt.tzinfo is not None


def test_now_ist_is_ist_aware():
    now = now_ist()
    assert now.tzinfo is not None
    offset = now.utcoffset().total_seconds() / 3600
    assert offset == 5.5, f"Expected UTC+5:30, got UTC+{offset}"


def test_saturday_is_not_trading_day():
    saturday = IST.localize(datetime(2026, 5, 2, 10, 0, 0))
    assert is_trading_day(saturday) is False


def test_monday_is_trading_day():
    monday = IST.localize(datetime(2026, 5, 4, 10, 0, 0))
    assert is_trading_day(monday) is True


def test_monthly_execution_returns_ist_aware():
    result = next_monthly_execution(1)
    assert result.tzinfo is not None
    assert result.hour == 9
    assert result.minute == 15
    assert result.weekday() < 5


def test_weekly_execution_returns_correct_weekday():
    result = next_weekly_execution(0)
    assert result.weekday() == 0
    assert result.hour == 9
    assert result.minute == 15


def test_daily_execution_is_weekday():
    result = next_daily_execution()
    assert result.weekday() < 5
    assert result.hour == 9
    assert result.minute == 15


def test_format_ist_shows_correct_time():
    """The formatted string must show IST time, not UTC."""
    utc_dt = datetime(2026, 5, 1, 3, 45, 0, tzinfo=pytz.utc)
    result = format_ist(utc_dt)
    assert "09:15" in result
    assert "IST" in result
    assert "03:45" not in result


def test_format_none_returns_dash():
    assert format_ist(None) == "—"
    assert format_ist_short(None) == "—"
    assert format_ist_date(None) == "—"


def test_is_market_open_returns_bool():
    """Smoke test: function should always return a bool, never raise."""
    assert isinstance(is_market_open(), bool)
