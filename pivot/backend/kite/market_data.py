"""
Market data fetching — live quotes, historical OHLCV.
Uses yfinance for historical (free, no auth needed).
Uses Kite for live quotes when connected.
"""
import logging
from datetime import datetime, timedelta
from typing import Optional
import yfinance as yf
from backend.kite.auth import KITE_MOCK_MODE, get_authenticated_kite
from backend.kite.mock_data import MOCK_QUOTE

logger = logging.getLogger(__name__)


def get_live_quote(access_token: str, instruments: list) -> dict:
    """
    Get live quote for list of instruments.
    instruments format: ["NSE:INFY", "NSE:TCS"]
    """
    if KITE_MOCK_MODE:
        return {inst: MOCK_QUOTE.get("NSE:NIFTY 50", {"last_price": 100.0}) for inst in instruments}
    kite = get_authenticated_kite(access_token)
    return kite.quote(instruments)


def get_historical_ohlcv(
    symbol: str,
    period: str = "1y",
    interval: str = "1d",
) -> list:
    """
    Fetch historical OHLCV using yfinance.
    Works without any API key — public data.
    symbol: NSE symbol like "INFY" → yfinance uses "INFY.NS"
    Returns list of {date, open, high, low, close, volume}
    """
    try:
        ticker_symbol = f"{symbol}.NS" if not symbol.endswith(".NS") else symbol
        ticker = yf.Ticker(ticker_symbol)
        df = ticker.history(period=period, interval=interval)
        if df.empty:
            return []
        records = []
        for idx, row in df.iterrows():
            records.append({
                "date": idx.strftime("%Y-%m-%d"),
                "open": round(float(row["Open"]), 2),
                "high": round(float(row["High"]), 2),
                "low": round(float(row["Low"]), 2),
                "close": round(float(row["Close"]), 2),
                "volume": int(row["Volume"]),
            })
        return records
    except Exception as e:
        logger.error(f"yfinance error for {symbol}: {e}")
        return []


# Approximate NSE trading-day counts per yfinance period string (smallest →
# largest). Used to size a historical fetch so a period-N indicator clears the
# live `len(bars) >= N + buffer` guard. The live sites used to hardcode "6mo"
# (~126 bars), which silently starved any indicator with period > ~120 (e.g. a
# 200-day EMA needs ≥205 bars) — it returned None and the agent never fired.
_PERIOD_BARS: list[tuple[str, int]] = [
    ("3mo", 63), ("6mo", 126), ("1y", 252), ("2y", 504), ("3y", 756),
]
_PERIOD_BARS_MAP = dict(_PERIOD_BARS)


def period_for_bars(min_bars: int, *, cap: str = "3y") -> str:
    """Smallest yfinance period string whose ~trading-day count covers
    ``min_bars``, clamped to ``cap``. Pure (no I/O)."""
    cap_bars = _PERIOD_BARS_MAP.get(cap, 756)
    target = min(max(int(min_bars), 1), cap_bars)
    for label, bars in _PERIOD_BARS:
        if bars >= target:
            return label
    return cap


def period_for_indicator(
    period: int,
    *,
    offset: int = 0,
    warmup_buffer: int = 5,
    floor: int = 20,
    cap: str = "3y",
) -> str:
    """Window string sized so a period-``period`` indicator (plus warm-up and
    any bar ``offset``) clears the live min-history guard
    ``len(bars) >= max(period + warmup_buffer, floor) + offset`` — mirrors the
    guards in ``scheduler._compute_indicator_sync`` / ``dsl.data_accessor``.

    Floored at ~6mo (126 bars) so small-period fetches never shrink below the
    previous hardcoded default (no regression); only long periods extend.
    """
    min_bars = max(int(period or 0) + int(warmup_buffer), int(floor)) + int(offset)
    return period_for_bars(max(min_bars, 126), cap=cap)


def get_nifty_level() -> float:
    """Get current Nifty 50 level via yfinance (15-min delayed, no auth needed)."""
    try:
        nifty = yf.Ticker("^NSEI")
        hist = nifty.history(period="1d", interval="5m")
        if not hist.empty:
            return round(float(hist["Close"].iloc[-1]), 2)
    except Exception as e:
        logger.warning(f"Could not fetch Nifty level: {e}")
    return 23500.0  # fallback
