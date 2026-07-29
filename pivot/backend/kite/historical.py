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

from backend.brokers.sessions import get_active_kite_session
from backend.cache import get_redis
from backend.core.data.intervals import (
    kite_lookback_days,
    normalize_interval,
    to_kite,
)
from backend.database import SessionLocal
from backend.kite.auth import (
    KITE_MOCK_MODE,
    get_authenticated_kite,
    read_kite_access_token,
)
from backend.kite.ticker import get_ticker_manager

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
    "3y":  (timedelta(days=1095), "day"),
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


# Process-local cache of {exchange: {normalized_symbol: instrument_token}}
# built directly from kite.instruments(). Decouples historical from the
# streaming ticker: charts must work even when the ticker isn't running
# (which is the common case — the ticker is opt-in). Refreshed daily since
# the instrument dump changes at most once per day.
_DIRECT_TOKEN_MAPS: dict[str, dict[str, int]] = {}
_DIRECT_TOKEN_MAP_DAY: dict[str, str] = {}


def _direct_instrument_map(exchange: str, access_token: str) -> dict[str, int]:
    """Build (and cache for the day) a tradingsymbol→instrument_token map
    straight from kite.instruments(exchange). Empty dict on any failure."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if (_DIRECT_TOKEN_MAP_DAY.get(exchange) == today
            and _DIRECT_TOKEN_MAPS.get(exchange)):
        return _DIRECT_TOKEN_MAPS[exchange]
    try:
        from backend.kite.ticker import normalize_symbol
        kite = get_authenticated_kite(access_token)
        rows = kite.instruments(exchange) or []
        out: dict[str, int] = {}
        for inst in rows:
            ts = inst.get("tradingsymbol")
            tok = inst.get("instrument_token")
            if ts and tok:
                out[normalize_symbol(ts)] = int(tok)
        if out:
            _DIRECT_TOKEN_MAPS[exchange] = out
            _DIRECT_TOKEN_MAP_DAY[exchange] = today
            logger.info("kite_historical: built direct instrument map for %s "
                        "(%d symbols)", exchange, len(out))
        return out
    except Exception as exc:  # noqa: BLE001
        logger.warning("kite_historical: instruments(%s) failed: %s",
                       exchange, str(exc)[:160])
        return {}


def _resolve_instrument_token(
    symbol: str, exchange: str = "NSE", access_token: Optional[str] = None,
) -> Optional[int]:
    """Look up the Kite instrument_token for a tradingsymbol.

    Fast path: the streaming ticker's in-memory map (populated on start).
    Fallback: a day-cached map built directly from kite.instruments() —
    so historical works whether or not the ticker is running. The direct
    fallback needs an ``access_token``; without one it returns None.
    """
    from backend.kite.ticker import normalize_symbol
    norm = normalize_symbol(symbol)
    mgr = get_ticker_manager()
    state = getattr(mgr, "_state", None)
    token_map = getattr(state, "instrument_tokens", None) if state else None
    if token_map:
        hit = token_map.get(norm)
        if hit:
            return hit
    if access_token:
        hit = _direct_instrument_map(exchange, access_token).get(norm)
        if hit:
            return hit
    # Last resort: the instrument_master table (refresh_instrument_master now
    # mirrors NSE/BSE cash rows). On cloud IPs Zerodha throttles the large
    # kite.instruments() dump — quotes work, the dump comes back empty — so
    # the direct map above never builds. The DB row survives that.
    try:
        from backend.models import InstrumentMaster
        db = SessionLocal()
        try:
            row = (
                db.query(InstrumentMaster.instrument_token)
                .filter(
                    InstrumentMaster.tradingsymbol == norm,
                    InstrumentMaster.exchange == exchange,
                )
                .order_by(InstrumentMaster.last_seen.desc())
                .first()
            )
        finally:
            db.close()
        if row:
            return int(row[0])
    except Exception as exc:  # noqa: BLE001 — resolver must never raise
        logger.debug("kite_historical: DB token lookup failed for %s: %s",
                     norm, str(exc)[:120])
    return None


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

    # Tolerate yfinance-form symbols ("HINDUNILVR.NS") from callers that skip
    # the _try_kite_history normalisation — Kite tradingsymbols never carry a
    # suffix, so without this the token lookup can only miss.
    s = (symbol or "").strip().upper()
    if s.endswith(".NS") or s.endswith(".BO"):
        symbol = s[:-3]

    # Resolve the lookback span. Accept the legacy _PERIOD_MAP keys plus the
    # bare 'Nd' day-spans that default_period_for() emits for intraday
    # intervals (e.g. '60d', '200d') — those aren't _PERIOD_MAP keys but are a
    # valid lookback request, so parse them instead of warning + defaulting.
    if period in _PERIOD_MAP:
        span, default_interval = _PERIOD_MAP[period]
    else:
        _p = (period or "").strip().lower()
        if _p.endswith("d") and _p[:-1].isdigit():
            span, default_interval = timedelta(days=int(_p[:-1])), "day"
        else:
            logger.warning("kite_historical: unknown period %r — using 1y", period)
            span, default_interval = _PERIOD_MAP["1y"]

    # Resolve interval. If the caller passed an explicit canonical interval,
    # normalise and translate to Kite's string; if Kite cannot serve it
    # (e.g. '1mo'), return None so the caller falls back honestly. When no
    # interval is given, keep the legacy period-keyed default unchanged.
    if interval is not None:
        canonical = normalize_interval(interval)
        kite_interval = to_kite(canonical)
        if kite_interval is None:
            return None
        use_interval = kite_interval
        # Clamp the lookback span to Kite's OWN per-interval cap (not the
        # cross-source max) so an intraday request never asks Kite for more
        # days than it will actually serve.
        cap_days = kite_lookback_days(canonical)
        if cap_days is not None:
            span = min(span, timedelta(days=cap_days))
    else:
        use_interval = default_interval

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

    # Pull the active Kite session (app-level, any user). The helper filters
    # is_active + access_token NOT NULL and orders by id DESC / updated_at
    # NULLS LAST — robust to a freshly-connected session whose updated_at is
    # still NULL on insert (else we'd pick a dead token and Kite would 401).
    db = SessionLocal()
    try:
        session = get_active_kite_session(db)
    finally:
        db.close()

    if session is None:
        return None

    access_token = read_kite_access_token(session)
    # Skip the dev placeholder / obviously-not-a-real token so we fall back
    # to yfinance cleanly instead of 401-ing against Kite.
    if not access_token or access_token.startswith("mock_") or len(access_token) < 20:
        return None

    instrument_token = _resolve_instrument_token(symbol, exchange, access_token)
    if instrument_token is None:
        logger.info(
            "kite_historical: instrument_token unknown for %s (not in ticker "
            "map or instruments dump)",
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
