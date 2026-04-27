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
