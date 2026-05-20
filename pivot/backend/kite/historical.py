"""Kite-backed historical OHLCV fetcher.

Wraps ``KiteConnect.historical_data`` for users with a live Kite session
(authenticated access_token in DB). Mirrors the dict-of-records shape of
``backend.kite.market_data.get_historical_ohlcv`` (yfinance) so callers
can swap without changes.

When called without a valid access_token (mock mode, no session, expired
token), returns ``None`` — callers are expected to fall back to yfinance.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from backend.cache import get_redis
from backend.database import SessionLocal
from backend.kite.auth import (
    KITE_MOCK_MODE,
    get_authenticated_kite,
    read_kite_access_token,
)
from backend.kite.ticker import get_ticker_manager
from backend.models import KiteSession

logger = logging.getLogger(__name__)

# Map our public period strings → (timedelta, kite_interval). Kite
# accepts: minute, 3minute, 5minute, 10minute, 15minute, 30minute,
# 60minute, day. We pick an interval that yields ≤500 candles per
# period (Kite's hard limit per request for intraday is 60 days).
_PERIOD_MAP: dict[str, tuple[timedelta, str]] = {
    "1d":  (timedelta(days=1),    "5minute"),
    "5d":  (timedelta(days=5),    "15minute"),
    "1mo": (timedelta(days=30),   "day"),
    "3mo": (timedelta(days=90),   "day"),
    "6mo": (timedelta(days=180),  "day"),
    "1y":  (timedelta(days=365),  "day"),
    "2y":  (timedelta(days=730),  "day"),
    "5y":  (timedelta(days=1825), "day"),
}

# Redis cache for historical responses. Keyed by (symbol, period,
# interval). 30-minute TTL — historical data rarely changes within
# a session.
_CACHE_TTL_SECONDS = 1800


@dataclass
class HistoricalBar:
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: int


def _resolve_instrument_token(symbol: str, exchange: str = "NSE") -> Optional[int]:
    """Look up the Kite instrument_token for a tradingsymbol.

    Uses the ticker manager's in-memory instrument map when it's
    populated (fast path). Otherwise returns None — caller will need
    to fall back.
    """
    mgr = get_ticker_manager()
    state = getattr(mgr, "_state", None)
    token_map = getattr(state, "instrument_tokens", None) if state else None
    if not token_map:
        return None
    # Ticker stores tokens under the normalised key (upper + underscores).
    from backend.kite.ticker import normalize_symbol
    return token_map.get(normalize_symbol(symbol))


def get_kite_historical(
    symbol: str,
    period: str = "1y",
    exchange: str = "NSE",
    interval: Optional[str] = None,
) -> Optional[list[dict]]:
    """Fetch OHLCV via Kite's historical API.

    Returns ``None`` if (a) we're in mock mode, (b) no active session
    exists, (c) the instrument map isn't populated, or (d) Kite returns
    an error. The caller should fall back to yfinance on ``None``.

    Args:
        symbol: NSE tradingsymbol, e.g. ``"RELIANCE"``.
        period: One of the ``_PERIOD_MAP`` keys. Default 1y.
        exchange: Exchange the symbol belongs to. Default ``"NSE"``.
        interval: Override the auto-picked interval (e.g. ``"minute"``
            for 1-min candles). Optional.

    Returns:
        List of records ``[{"date": "...", "open": ..., "high": ...,
        "low": ..., "close": ..., "volume": ...}, ...]``.
    """
    if KITE_MOCK_MODE:
        return None

    if period not in _PERIOD_MAP:
        logger.warning("kite_historical: unknown period %r — using 1y", period)
        period = "1y"
    span, default_interval = _PERIOD_MAP[period]
    use_interval = interval or default_interval

    # Cache hit?
    cache_key_str = (
        f"kite_hist:{symbol.upper()}:{exchange}:{period}:{use_interval}"
    )
    try:
        rc = get_redis()
        cached = rc.get(cache_key_str)
        if cached:
            import json
            if isinstance(cached, (bytes, bytearray)):
                cached = cached.decode()
            return json.loads(cached)
    except Exception as exc:
        logger.debug("kite_historical cache read failed: %s", exc)

    # Pull the active session
    db = SessionLocal()
    try:
        session = (
            db.query(KiteSession)
            .filter(KiteSession.access_token.isnot(None))
            .order_by(KiteSession.updated_at.desc().nullslast())
            .first()
        )
    finally:
        db.close()

    if session is None:
        return None

    access_token = read_kite_access_token(session)
    if not access_token:
        return None

    instrument_token = _resolve_instrument_token(symbol, exchange)
    if instrument_token is None:
        logger.info(
            "kite_historical: instrument_token unknown for %s (ticker map empty?)",
            symbol,
        )
        return None

    try:
        kite = get_authenticated_kite(access_token)
        if kite is None:
            return None
        to_dt = datetime.now(timezone.utc)
        from_dt = to_dt - span
        candles = kite.historical_data(
            instrument_token=instrument_token,
            from_date=from_dt,
            to_date=to_dt,
            interval=use_interval,
            continuous=False,
            oi=False,
        )
    except Exception as exc:
        logger.warning("kite_historical fetch failed for %s: %s", symbol, exc)
        return None

    records = []
    for c in candles or []:
        ts = c.get("date")
        if ts is None:
            continue
        if isinstance(ts, datetime):
            iso = ts.strftime("%Y-%m-%d %H:%M:%S")
        else:
            iso = str(ts)
        records.append(
            {
                "date": iso,
                "open":  round(float(c.get("open", 0)), 2),
                "high":  round(float(c.get("high", 0)), 2),
                "low":   round(float(c.get("low",  0)), 2),
                "close": round(float(c.get("close", 0)), 2),
                "volume": int(c.get("volume", 0) or 0),
            }
        )

    # Cache for 30 minutes
    try:
        import json
        rc = get_redis()
        rc.setex(cache_key_str, _CACHE_TTL_SECONDS, json.dumps(records))
    except Exception as exc:
        logger.debug("kite_historical cache write failed: %s", exc)

    return records


def get_historical_with_fallback(
    symbol: str,
    period: str = "1y",
    exchange: str = "NSE",
) -> list[dict]:
    """Convenience wrapper: try Kite first, fall back to yfinance.

    Always returns a list (possibly empty). Suitable as a drop-in for
    callers that currently use ``backend.kite.market_data.get_historical_ohlcv``.
    """
    rows = get_kite_historical(symbol, period=period, exchange=exchange)
    if rows is not None and rows:
        return rows
    from backend.kite.market_data import get_historical_ohlcv
    return get_historical_ohlcv(symbol, period=period)
