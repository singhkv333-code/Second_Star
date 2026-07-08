"""Tests for backend.core.data.historical module."""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import patch

import pandas as pd
import pytest

# Check if we can reach yfinance (for live test)
_NETWORK_AVAILABLE = False
try:
    import yfinance as yf

    # Quick ping: try to get info for a well-known ticker
    _ticker = yf.Ticker("RELIANCE.NS")
    _hist = _ticker.history(period="5d")
    _NETWORK_AVAILABLE = not _hist.empty
except Exception:
    pass

from backend.core.data.historical import (
    DataUnavailableError,
    get_close_series,
    get_ohlcv,
)


@pytest.mark.skipif(
    not _NETWORK_AVAILABLE,
    reason="Network unavailable or yfinance rate-limited",
)
def test_get_ohlcv_reliance_live():
    """Live test: fetch RELIANCE 1mo daily data."""
    df = get_ohlcv("RELIANCE", period="1mo", interval="1d")

    # DataFrame should not be empty
    assert not df.empty, "DataFrame should not be empty"

    # Should have the correct columns
    expected_cols = ["Open", "High", "Low", "Close", "Volume"]
    assert list(df.columns) == expected_cols, f"Expected columns {expected_cols}"

    # No NaNs in Close
    assert df["Close"].isna().sum() == 0, "Close column should have no NaNs"

    # Index should be datetime
    assert isinstance(df.index, pd.DatetimeIndex), "Index should be DatetimeIndex"

    # Last date should be within 14 days of today (markets may be closed)
    today = datetime.now()
    last_date = df.index[-1].to_pydatetime()
    days_diff = (today - last_date).days
    assert days_diff <= 14, f"Last date {last_date} is {days_diff} days ago (max 14)"

    # Data should be sorted ascending
    assert df.index.is_monotonic_increasing, "Index should be sorted ascending"


def test_get_ohlcv_unknown_symbol_raises():
    """Offline test: unknown symbol should raise DataUnavailableError."""
    with patch(
        "backend.core.data.historical.fetch_price_history", return_value=[]
    ) as mock_fetch:
        with pytest.raises(DataUnavailableError) as exc_info:
            get_ohlcv("TOTALLYFAKESYMBOL123XYZ", period="1mo", interval="1d")

        assert exc_info.value.symbol == "TOTALLYFAKESYMBOL123XYZ"
        assert "no data" in str(exc_info.value).lower()
        mock_fetch.assert_called_once()


def test_get_close_series_extracts_close():
    """Offline test: get_close_series should return the Close column as a Series."""
    mock_records = [
        {
            "date": "2024-01-02",
            "open": 100.0,
            "high": 105.0,
            "low": 99.0,
            "close": 103.0,
            "volume": 1000000,
        },
        {
            "date": "2024-01-03",
            "open": 103.0,
            "high": 108.0,
            "low": 102.0,
            "close": 107.0,
            "volume": 1200000,
        },
        {
            "date": "2024-01-04",
            "open": 107.0,
            "high": 110.0,
            "low": 106.0,
            "close": 109.0,
            "volume": 900000,
        },
    ]

    with patch(
        "backend.core.data.historical.fetch_price_history", return_value=mock_records
    ):
        series = get_close_series("TESTSTOCK", period="1mo")

        # Should be a Series
        assert isinstance(series, pd.Series), "Should return a pandas Series"

        # Should have correct values
        expected_values = [103.0, 107.0, 109.0]
        assert list(series.values) == expected_values, "Close values should match"

        # Index should be datetime
        assert isinstance(series.index, pd.DatetimeIndex), "Index should be DatetimeIndex"

        # Should have correct dtype (float)
        assert series.dtype == float, f"Dtype should be float, got {series.dtype}"


def test_get_ohlcv_invalid_period_raises():
    """Invalid period should raise ValueError."""
    with pytest.raises(ValueError) as exc_info:
        get_ohlcv("RELIANCE", period="invalid_period", interval="1d")

    assert "invalid period" in str(exc_info.value).lower()


def test_get_ohlcv_invalid_interval_raises():
    """Invalid interval should raise ValueError."""
    with pytest.raises(ValueError) as exc_info:
        get_ohlcv("RELIANCE", period="1mo", interval="5m")

    assert "invalid interval" in str(exc_info.value).lower()
