"""Data layer for /core/ modules.

Provides historical OHLCV access via pandas DataFrames, wrapping the
existing yfinance_service with its Redis cache.
"""

from backend.core.data.historical import (
    DataUnavailableError,
    get_close_dict,
    get_close_series,
    get_multiple_ohlcv,
    get_ohlcv,
)

__all__ = [
    "DataUnavailableError",
    "get_ohlcv",
    "get_close_series",
    "get_multiple_ohlcv",
    "get_close_dict",
]
