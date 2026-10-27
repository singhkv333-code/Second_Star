"""US-equity / ETF market DATA via Alpaca (data-only).

Register-not-execute: Pivot never places a live US order — US positions fill
into the simulated paper book. This module ONLY reads prices. Alpaca's data
API works with the paper keys. yfinance (raw US ticker, NO .NS suffix) is the
fallback so a missing/limited Alpaca feed doesn't blank a US mark.

Returns prices in **USD** — the caller converts to INR via market.fx for
INR-denominated valuation. Redis-cached (short TTL: US marks move fast during
the US session; a stale-by-30s mark is fine for a paper book).
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from backend.config import settings

logger = logging.getLogger(__name__)

_CACHE_PREFIX = "us_price:v1:"
_TTL_S = 45


def _redis():
    try:
        from backend.cache import get_redis
        return get_redis()
    except Exception:  # noqa: BLE001
        return None


def _alpaca_latest_usd(symbol: str) -> Optional[float]:
    """Latest trade price (USD) from Alpaca's data API. None on any failure /
    missing keys — caller falls back to yfinance."""
    key = (settings.alpaca_api_key or "").strip()
    secret = (settings.alpaca_api_secret or "").strip()
    if not key or not secret:
        return None
    try:
        from backend.market.net_timeout import call_bounded
        import httpx

        base = settings.alpaca_data_base_url.rstrip("/")
        url = f"{base}/stocks/{symbol}/trades/latest"
        headers = {
            "APCA-API-KEY-ID": key,
            "APCA-API-SECRET-KEY": secret,
        }

        def _do() -> dict:
            with httpx.Client(timeout=5.0) as c:
                r = c.get(url, headers=headers, params={"feed": "iex"})
                r.raise_for_status()
                return r.json()

        data = call_bounded(_do, timeout=6.0) or {}
        trade = data.get("trade") if isinstance(data, dict) else None
        px = (trade or {}).get("p")
        if px is not None and float(px) > 0:
            return float(px)
    except Exception as e:  # noqa: BLE001 — never fatal; fall back
        logger.debug("alpaca latest failed for %s: %s", symbol, e)
    return None


def _yfinance_us_usd(symbol: str) -> Optional[float]:
    """Fallback: yfinance last close for a RAW US ticker (no .NS). Bounded so a
    Yahoo rate-limit can't hang the request."""
    try:
        from backend.market.net_timeout import call_bounded
        import yfinance as yf  # type: ignore[import-untyped]

        def _do():
            return yf.Ticker(symbol).history(period="5d", auto_adjust=False)

        hist = call_bounded(_do, timeout=6.0)
        if hist is not None and not hist.empty:
            c = float(hist["Close"].iloc[-1])
            if c > 0:
                return c
    except Exception as e:  # noqa: BLE001
        logger.debug("yfinance US fallback failed for %s: %s", symbol, e)
    return None


def get_us_price_usd(symbol: str) -> Optional[float]:
    """Latest US-equity/ETF price in USD (Alpaca → yfinance), Redis-cached.
    None when both providers miss."""
    sym = str(symbol or "").upper().strip()
    if not sym:
        return None
    rc = _redis()
    key = f"{_CACHE_PREFIX}{sym}"
    if rc is not None:
        try:
            raw = rc.get(key)
            if raw:
                v = json.loads(raw if isinstance(raw, str) else raw.decode())
                return float(v) if v is not None else None
        except Exception:  # noqa: BLE001
            pass
    px = _alpaca_latest_usd(sym) or _yfinance_us_usd(sym)
    if px is not None and rc is not None:
        try:
            rc.setex(key, _TTL_S, json.dumps(px))
        except Exception:  # noqa: BLE001
            pass
    return px
