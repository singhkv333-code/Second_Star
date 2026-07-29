"""Live Kite REST quote tier — the missing middle layer between the WebSocket
tick cache and the yfinance fallback.

Both the stock-page snapshot (`/markets/quote`), the index strip
(`/markets/indices`) and the chat `get_live_price` tool were, in effect,
*yfinance-primary*: their only Kite tier was the in-memory WS tick cache
(populated only while the streaming ticker is running and subscribed to that
symbol). On a miss they fell straight to yfinance's `.info`/`.fast_info`, which
**hangs** on a datacenter IP (Yahoo silently drops cloud egress) → the request
exceeds the gateway timeout → the browser throws "Failed to fetch".

This module adds a real Kite REST tier using ``kite.quote()`` over the
app-level active Kite session (the SAME token source as ``kite/historical.py``,
which already works in production — that's why charts load while the quote
does not). It works for equities *and* indices (``kite.quote`` serves
``NSE:NIFTY 50`` / ``BSE:SENSEX`` etc.), returns ``{}`` on any auth/network
failure so callers fall back cleanly, and caches per key in Redis for a few
seconds so a burst of loads doesn't pay N round-trips.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from backend.brokers.sessions import get_active_kite_session
from backend.cache import get_redis
from backend.database import SessionLocal
from backend.kite.auth import (
    KITE_MOCK_MODE,
    get_authenticated_kite,
    read_kite_access_token,
)

logger = logging.getLogger(__name__)

# Match the WS tick cache's freshness window — a REST quote is "live enough"
# for a few seconds, and this collapses the dashboard's repeated bursts.
_QUOTE_TTL_S = 5
_CACHE_PREFIX = "kite_rest_q:"
_MAX_PER_CALL = 250  # Kite allows ~500 instruments/quote() call; stay well under


def _f(v: object) -> Optional[float]:
    """Parse a Kite numeric field to a rounded float; None when absent/bad."""
    try:
        f = float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN
        return None
    return round(f, 2)


def kite_session_available() -> bool:
    """True when a usable app-level Kite session token exists (not mock/stub)."""
    return _active_token() is not None


# The active-token row changes at most once a day (the ~6 AM IST expiry /
# re-login), but resolving it costs a full Azure-PG round-trip (~1.5s
# measured) and it ran on EVERY batch-quote call. Cache it in-process for
# a short window; a fresh login is picked up within a minute, and an
# expired token just makes kite.quote fail → callers already fall back.
_TOKEN_CACHE_TTL_S = 60.0
_token_cache: dict = {"token": None, "ts": 0.0}


def _active_token() -> Optional[str]:
    """The app-level active Kite access token, or None. Mirrors the resolution
    in ``kite/historical.py`` so the two tiers agree on whether Kite is live."""
    if KITE_MOCK_MODE:
        return None
    import time as _time
    now = _time.monotonic()
    if now - float(_token_cache["ts"]) < _TOKEN_CACHE_TTL_S:
        return _token_cache["token"]
    db = SessionLocal()
    try:
        session = get_active_kite_session(db)
    finally:
        db.close()
    token: Optional[str] = None
    if session is not None:
        token = read_kite_access_token(session)
        # Skip the dev placeholder / obviously-not-a-real token so callers
        # fall back cleanly instead of 401-ing against Kite.
        if not token or token.startswith("mock_") or len(token) < 20:
            token = None
    _token_cache["token"] = token
    _token_cache["ts"] = now
    return token


def get_kite_quotes(keys: list[str]) -> dict[str, dict]:
    """Batch live quote for Kite instrument keys.

    ``keys`` are full ``EXCHANGE:TRADINGSYMBOL`` strings, e.g.
    ``["NSE:HDFCBANK", "BSE:SENSEX", "NSE:NIFTY 50"]``.

    Returns ``{key: {"last_price", "open", "high", "low", "prev_close",
    "volume"}}`` for the keys Kite could serve. Returns whatever was cached
    (possibly empty) on any auth/network failure — never raises — so callers
    fall back to yfinance cleanly instead of hanging.
    """
    if not keys:
        return {}

    try:
        rc = get_redis()
    except Exception:  # noqa: BLE001 — cache is best-effort
        rc = None

    out: dict[str, dict] = {}
    missing: list[str] = []
    # ONE round-trip for the whole batch. Per-key GETs cost an RTT each —
    # a 50-symbol movers scan spent ~3.4s (measured) on cache reads alone.
    cached_raws: list = [None] * len(keys)
    if rc is not None:
        try:
            cached_raws = rc.mget([f"{_CACHE_PREFIX}{k}" for k in keys])
        except Exception:  # noqa: BLE001
            cached_raws = [None] * len(keys)
    for k, raw in zip(keys, cached_raws):
        cached = None
        if raw:
            try:
                cached = json.loads(raw if isinstance(raw, str) else raw.decode())
            except Exception:  # noqa: BLE001
                cached = None
        if cached is not None:
            out[k] = cached
        else:
            missing.append(k)
    if not missing:
        return out

    token = _active_token()
    if token is None:
        return out  # no live session → serve whatever was cached; caller falls back

    try:
        kite = get_authenticated_kite(token)
        if kite is None:
            return out
        # Kite caps ~500 instruments per quote() call; chunk so a large
        # universe (e.g. a movers scan) doesn't trip the limit.
        data: dict = {}
        for i in range(0, len(missing), _MAX_PER_CALL):
            chunk = missing[i:i + _MAX_PER_CALL]
            data.update(kite.quote(chunk) or {})
    except Exception as exc:  # noqa: BLE001 — never fatal; caller falls back
        logger.info("kite REST quote failed for %s: %s", missing[:5], str(exc)[:160])
        return out

    for key, q in data.items():
        if not isinstance(q, dict):
            continue
        ohlc = q.get("ohlc") or {}
        last = _f(q.get("last_price"))
        if last is None:
            continue
        norm = {
            "last_price": last,
            "open": _f(ohlc.get("open")),
            "high": _f(ohlc.get("high")),
            "low": _f(ohlc.get("low")),
            "prev_close": _f(ohlc.get("close")),  # Kite's ohlc.close == previous close
            "volume": _f(q.get("volume")) or 0.0,
        }
        out[key] = norm
    # ONE pipelined round-trip for the cache writes (was a SETEX per key).
    fresh = {k: out[k] for k in missing if k in out}
    if rc is not None and fresh:
        try:
            pipe = rc.pipeline(transaction=False)
            for key, norm in fresh.items():
                pipe.setex(f"{_CACHE_PREFIX}{key}", _QUOTE_TTL_S, json.dumps(norm))
            pipe.execute()
        except Exception:  # noqa: BLE001
            pass
    return out


def get_kite_quote(symbol: str, exchange: str = "NSE") -> Optional[dict]:
    """Single-symbol convenience over :func:`get_kite_quotes`. None on miss."""
    key = f"{exchange}:{symbol.strip().upper()}"
    return get_kite_quotes([key]).get(key)
