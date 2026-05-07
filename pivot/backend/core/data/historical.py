"""Historical OHLCV access for /core/ modules.

Thin wrapper over backend.market.yfinance_service so the new
indicator / calculations / strategy layers can get a pandas
DataFrame by ticker without touching the chart-cache plumbing
themselves. The underlying yfinance call is already Redis-cached
(~1 hour TTL) so repeated calls within the same chat turn return
in microseconds.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING

import pandas as pd

from backend.market.yfinance_service import fetch_price_history, resolve_symbol

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Supported period values (maps to yfinance periods)
VALID_PERIODS = {"5d", "1mo", "3mo", "6mo", "1y", "2y", "5y", "max", "ytd"}
# Supported interval values
VALID_INTERVALS = {"1d", "1wk", "1mo"}


class DataUnavailableError(Exception):
    """Raised when historical data cannot be fetched for a symbol."""

    def __init__(self, symbol: str, reason: str = "no data returned"):
        self.symbol = symbol
        self.reason = reason
        super().__init__(f"Data unavailable for {symbol}: {reason}")


def get_ohlcv(
    symbol: str,
    period: str = "1y",
    interval: str = "1d",
) -> pd.DataFrame:
    """
    Fetch OHLCV history for a single symbol as a pandas DataFrame.

    Args:
        symbol: NSE ticker (e.g. "RELIANCE", "INFY"). The .NS suffix is
                appended automatically by the underlying service.
        period: One of "5d", "1mo", "3mo", "6mo", "1y", "2y", "5y", "max".
        interval: One of "1d" (daily), "1wk" (weekly), "1mo" (monthly).

    Returns:
        DataFrame with columns [Open, High, Low, Close, Volume], indexed
        by datetime, sorted ascending, NaN-free.

    Raises:
        DataUnavailableError: If no data is returned (rate-limited, unknown
                              symbol, or network failure).
        ValueError: If period or interval is invalid.
    """
    if period not in VALID_PERIODS:
        raise ValueError(
            f"Invalid period {period!r}; must be one of {sorted(VALID_PERIODS)}"
        )
    if interval not in VALID_INTERVALS:
        raise ValueError(
            f"Invalid interval {interval!r}; must be one of {sorted(VALID_INTERVALS)}"
        )

    # fetch_price_history returns list[dict] with keys: date, open, high, low, close, volume
    # It handles .NS suffix resolution, caching, and fallback internally.
    records = fetch_price_history(symbol, period, interval)

    if not records:
        raise DataUnavailableError(symbol, reason="no data returned from yfinance")

    # Convert to DataFrame
    df = pd.DataFrame(records)

    # Parse date column to datetime and set as index
    df["date"] = pd.to_datetime(df["date"], format="%Y-%m-%d")
    df.set_index("date", inplace=True)

    # Rename columns to standard capitalized form
    df.rename(
        columns={
            "open": "Open",
            "high": "High",
            "low": "Low",
            "close": "Close",
            "volume": "Volume",
        },
        inplace=True,
    )

    # Ensure correct column order
    df = df[["Open", "High", "Low", "Close", "Volume"]]

    # Sort by date ascending (should already be, but ensure)
    df.sort_index(inplace=True)

    # Drop any rows with NaN in Close (the most critical column)
    df.dropna(subset=["Close"], inplace=True)

    # Fill any remaining NaN in other columns with forward-fill then backward-fill
    df.ffill(inplace=True)
    df.bfill(inplace=True)

    # Final safety: drop any rows that still have NaN (shouldn't happen)
    df.dropna(inplace=True)

    if df.empty:
        raise DataUnavailableError(symbol, reason="all data was NaN after cleaning")

    return df


def get_close_series(
    symbol: str,
    period: str = "1y",
) -> pd.Series:
    """
    Fetch just the Close price series for a symbol.

    Args:
        symbol: NSE ticker (e.g. "RELIANCE").
        period: One of the supported period values.

    Returns:
        Series of Close prices indexed by datetime.

    Raises:
        DataUnavailableError: If no data is returned.
    """
    df = get_ohlcv(symbol, period=period, interval="1d")
    return df["Close"]


def get_multiple_ohlcv(
    symbols: list[str],
    period: str = "1y",
    interval: str = "1d",
) -> dict[str, pd.DataFrame]:
    """
    Fetch OHLCV for multiple symbols.

    Args:
        symbols: List of NSE tickers.
        period: Period string (default "1y").
        interval: Interval string (default "1d").

    Returns:
        Dict mapping symbol -> DataFrame. Symbols that fail to fetch are
        excluded (with a warning logged), not included with empty DataFrames.
    """
    result: dict[str, pd.DataFrame] = {}

    for sym in symbols:
        try:
            result[sym] = get_ohlcv(sym, period=period, interval=interval)
        except DataUnavailableError as e:
            logger.warning(f"Skipping {sym}: {e}")
        except Exception as e:
            logger.warning(f"Unexpected error fetching {sym}: {e}")

    return result


def get_close_dict(
    symbols: list[str],
    period: str = "1y",
) -> dict[str, pd.Series]:
    """
    Fetch Close prices for multiple symbols.

    Convenience function for correlation_matrix and comparison consumers.

    Args:
        symbols: List of NSE tickers.
        period: Period string.

    Returns:
        Dict mapping symbol -> Close Series. Failed symbols are excluded.
    """
    result: dict[str, pd.Series] = {}

    for sym in symbols:
        try:
            result[sym] = get_close_series(sym, period=period)
        except DataUnavailableError as e:
            logger.warning(f"Skipping {sym}: {e}")
        except Exception as e:
            logger.warning(f"Unexpected error fetching {sym}: {e}")

    return result
