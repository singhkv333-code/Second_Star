"""Section M — Fundamental Primitives."""

from __future__ import annotations

import time
import warnings
from datetime import date, datetime
from typing import Any, Optional, Union

import pandas as pd

try:
    import yfinance as yf  # type: ignore
    _YF_AVAILABLE = True
except Exception:
    yf = None  # type: ignore
    _YF_AVAILABLE = False


_CACHE_TTL_SECONDS = 24 * 60 * 60
_CACHE: dict[tuple[str, str], tuple[float, Any]] = {}

DateLike = Union[date, datetime, pd.Timestamp, str]


def _resolve_symbol(symbol: str) -> str:
    if not symbol:
        return symbol
    s = symbol.strip().upper()
    if "." in s:
        return s
    return f"{s}.NS"


def _cache_get(symbol: str, field: str) -> tuple[bool, Any]:
    key = (symbol, field)
    entry = _CACHE.get(key)
    if entry is None:
        return False, None
    ts, value = entry
    if (time.time() - ts) > _CACHE_TTL_SECONDS:
        _CACHE.pop(key, None)
        return False, None
    return True, value


def _cache_set(symbol: str, field: str, value: Any) -> None:
    _CACHE[(symbol, field)] = (time.time(), value)


def _fetch_info_field(symbol: str, field: str) -> Optional[float]:
    if not _YF_AVAILABLE:
        return None
    resolved = _resolve_symbol(symbol)
    hit, cached = _cache_get(resolved, field)
    if hit:
        return cached
    try:
        ticker = yf.Ticker(resolved)
        info = getattr(ticker, "info", None) or {}
        raw = info.get(field)
        if raw is None:
            _cache_set(resolved, field, None)
            return None
        value = float(raw)
        _cache_set(resolved, field, value)
        return value
    except Exception:
        return None


def get_stock_pe(symbol: str) -> Optional[float]:
    return _fetch_info_field(symbol, "trailingPE")


def get_stock_pb(symbol: str) -> Optional[float]:
    return _fetch_info_field(symbol, "priceToBook")


def get_stock_dividend_yield(symbol: str) -> Optional[float]:
    return _fetch_info_field(symbol, "dividendYield")


def get_stock_roe(symbol: str) -> Optional[float]:
    return _fetch_info_field(symbol, "returnOnEquity")


def get_stock_market_cap(symbol: str) -> Optional[float]:
    return _fetch_info_field(symbol, "marketCap")


def filter_pe_below(symbol: str, threshold: float) -> bool:
    pe = get_stock_pe(symbol)
    if pe is None:
        return False
    return pe < float(threshold)


def filter_pb_below(symbol: str, threshold: float) -> bool:
    pb = get_stock_pb(symbol)
    if pb is None:
        return False
    return pb < float(threshold)


def filter_dividend_yield_above(symbol: str, threshold: float) -> bool:
    dy = get_stock_dividend_yield(symbol)
    if dy is None:
        return False
    return dy > float(threshold)


def filter_market_cap_category(symbol: str, category: str) -> bool:
    mc = get_stock_market_cap(symbol)
    if mc is None:
        return False
    cat = (category or "").strip().lower()
    if cat == "large":
        return mc > 2e12
    if cat == "mid":
        return 5e11 <= mc <= 2e12
    if cat == "small":
        return mc < 5e11
    return False


def calc_nifty_pe(start_date: DateLike, end_date: DateLike) -> pd.Series:
    warnings.warn(
        "calc_nifty_pe: Nifty PE time series is not available via yfinance; "
        "returning empty Series. Provide a static data source for production use.",
        RuntimeWarning,
        stacklevel=2,
    )
    return pd.Series(dtype="float64", name="nifty_pe")


def sig_nifty_pe_below(nifty_pe_series: pd.Series, threshold: float = 20.0) -> pd.Series:
    if nifty_pe_series is None or len(nifty_pe_series) == 0:
        return pd.Series(dtype="bool")
    try:
        return (nifty_pe_series < float(threshold)).astype(bool)
    except Exception:
        return pd.Series(dtype="bool", index=getattr(nifty_pe_series, "index", None))


def sig_nifty_pe_cross_below(nifty_pe_series: pd.Series, threshold: float) -> pd.Series:
    if nifty_pe_series is None or len(nifty_pe_series) == 0:
        return pd.Series(dtype="bool")
    try:
        thr = float(threshold)
        below = nifty_pe_series < thr
        prev_at_or_above = nifty_pe_series.shift(1) >= thr
        cross = (below & prev_at_or_above).fillna(False).astype(bool)
        return cross
    except Exception:
        return pd.Series(dtype="bool", index=getattr(nifty_pe_series, "index", None))
