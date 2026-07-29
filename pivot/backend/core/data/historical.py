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
import re
from datetime import timedelta
from typing import TYPE_CHECKING

import pandas as pd

from backend.core.data.intervals import (
    CANONICAL_INTERVALS,
    default_period_for,
    is_intraday,
    normalize_interval,
)
from backend.market.yfinance_service import fetch_price_history

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Supported period values (maps to yfinance periods)
VALID_PERIODS = {"5d", "1mo", "3mo", "6mo", "1y", "2y", "5y", "max", "ytd"}
# Supported interval values — full canonical set (minute…daily…monthly) plus
# the legacy daily/weekly/monthly strings kept for explicit back-compat.
VALID_INTERVALS = set(CANONICAL_INTERVALS) | {"1d", "1wk", "1mo"}

# Arbitrary-window support. yfinance only accepts the fixed VALID_PERIODS
# set, but users ask for 3y / 4y / 18mo / 9mo. We fetch the SMALLEST valid
# yfinance period that fully covers the request, then slice to the exact
# requested calendar span by date (in get_ohlcv). Canonical periods
# (6mo/1y/2y/5y) get slice_days=None -> no slice, no behaviour change.
_PERIOD_LADDER: list[tuple[str, int]] = [
    ("5d", 5), ("1mo", 31), ("3mo", 93), ("6mo", 186),
    ("1y", 366), ("2y", 731), ("5y", 1827), ("max", 10 ** 9),
]
_ARBITRARY_PERIOD_RE = re.compile(
    r"(\d+)\s*(d|day|days|w|wk|week|weeks|mo|month|months|y|yr|yrs|year|years)"
)


def _resolve_fetch_period(period: str) -> tuple[str, int | None]:
    """Map a user period to (yfinance_period, slice_days).

    - Canonical periods (in VALID_PERIODS) pass through, slice_days=None.
    - Arbitrary 'N<unit>' spans (3y, 18mo, 9mo, 30w, 45d) return the
      smallest valid yfinance period covering the span + the exact
      calendar-day count to slice to afterwards.
    - Empty/None -> ('1y', None). Garbage raises ValueError (preserves
      the invalid-period contract).
    """
    if not period:
        return "1y", None
    s = str(period).strip().lower().replace(" ", "")
    if s in VALID_PERIODS:
        return s, None
    m = re.fullmatch(_ARBITRARY_PERIOD_RE, s)
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        if unit.startswith("d"):
            days = n
        elif unit.startswith("w"):
            days = n * 7
        elif unit.startswith("mo"):
            days = n * 30
        else:
            days = n * 365
        if days <= 0:
            raise ValueError(f"Invalid period {period!r}; span must be positive")
        for name, cap in _PERIOD_LADDER:
            if days <= cap:
                return name, days
        return "max", days
    raise ValueError(
        f"Invalid period {period!r}; must be one of {sorted(VALID_PERIODS)} "
        f"or an N-day/N-week/N-month/N-year span like '3y' or '18mo'"
    )


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
    if interval not in VALID_INTERVALS:
        raise ValueError(
            f"Invalid interval {interval!r}; must be one of {sorted(VALID_INTERVALS)}"
        )

    # Normalise the interval up front (handles 'daily'/'weekly'/'60m'/etc).
    canonical = normalize_interval(interval)
    intraday = is_intraday(canonical)

    if intraday:
        # Intraday: skip the daily _resolve_fetch_period ladder (it produces
        # yfinance period strings like '1y'/'max' that are invalid alongside
        # an intraday interval). Use default_period_for() when the caller
        # didn't pin an explicit window; pass the period straight through to
        # fetch_price_history, which now handles intraday clamping internally.
        if not period or str(period).strip().lower() in {"1y", "default"}:
            fetch_period = default_period_for(canonical)
        else:
            fetch_period = period
        slice_days = None
        records = fetch_price_history(symbol, fetch_period, canonical)
    else:
        # Resolve arbitrary windows (3y / 18mo / 9mo) to the smallest valid
        # yfinance period that covers them; slice to the exact span after
        # fetch. (Raises ValueError on a genuinely invalid period, preserving
        # the prior contract.)
        fetch_period, slice_days = _resolve_fetch_period(period)

        # fetch_price_history returns list[dict] with keys: date, open, high,
        # low, close, volume. It handles .NS suffix resolution, caching, and
        # fallback internally.
        records = fetch_price_history(symbol, fetch_period, canonical)

    if not records:
        raise DataUnavailableError(symbol, reason="no data returned from yfinance")

    # Convert to DataFrame
    df = pd.DataFrame(records)

    # Parse date column to datetime and set as index. Use no explicit format
    # so we accept BOTH 'YYYY-MM-DD' (daily/weekly/monthly) and
    # 'YYYY-MM-DD HH:MM:SS' (intraday) stamps.
    df["date"] = pd.to_datetime(df["date"])
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

    # Slice to the exact requested calendar span for arbitrary windows
    # (3y -> last 1095 days). Canonical periods have slice_days=None -> no-op.
    if slice_days is not None and not df.empty:
        cutoff = df.index[-1] - timedelta(days=slice_days)
        df = df[df.index >= cutoff]
        if df.empty:
            raise DataUnavailableError(
                symbol, reason=f"no data within last {slice_days} days"
            )

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
