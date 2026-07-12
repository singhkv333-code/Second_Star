"""Foreign-exchange rates for multi-asset valuation.

The portfolio/book is INR-denominated; US equities and crypto are priced in
USD. Any USD figure surfaced to the user (a mark, an entry/exit level, a
holding value) MUST be converted to INR at the current rate — otherwise a
$400 MSTR share would be summed into an INR NAV as ₹400 (≈83× wrong).

This module is the single source of truth for that conversion. Rates come
from Frankfurter (ECB reference rates, no API key), Redis-cached for a few
hours (FX moves slowly relative to a chat session), with a conservative
static fallback so valuation never hard-fails when the feed is down.

Usage:
    from backend.market.fx import usd_to_inr, convert_to_inr
    inr = usd_to_inr(400.0)                    # 400 USD -> INR
    inr = convert_to_inr(400.0, "USD")         # generic: currency -> INR
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from backend.config import settings

logger = logging.getLogger(__name__)

# Cache: FX is slow-moving; a few hours is plenty and keeps us off the feed.
_FX_CACHE_PREFIX = "fx:v1:"
_FX_TTL_S = 6 * 60 * 60  # 6h
# Conservative static fallback (only used when Frankfurter AND cache both
# miss). Deliberately a round, clearly-approximate number so a stale value is
# recognisable; real rate is refreshed on the next successful fetch.
_USDINR_FALLBACK = 86.0


def _http_get(url: str, params: dict) -> dict:
    """Bounded GET → JSON dict; never hangs the caller (datacenter-safe)."""
    from backend.market.net_timeout import call_bounded
    import httpx

    def _do() -> dict:
        with httpx.Client(timeout=5.0) as c:
            r = c.get(url, params=params)
            r.raise_for_status()
            return r.json()

    return call_bounded(_do, timeout=6.0) or {}


def _redis():
    try:
        from backend.cache import get_redis
        return get_redis()
    except Exception:  # noqa: BLE001
        return None


def fx_rate(base: str, quote: str = "INR") -> Optional[float]:
    """Current ``base``→``quote`` rate (e.g. USD→INR), Redis-cached. Returns
    None only when the feed AND cache both miss for a NON-USD base (USD falls
    back to a static rate so INR valuation never breaks)."""
    base = (base or "").upper().strip()
    quote = (quote or "INR").upper().strip()
    if not base or base == quote:
        return 1.0
    key = f"{_FX_CACHE_PREFIX}{base}{quote}"
    rc = _redis()
    if rc is not None:
        try:
            raw = rc.get(key)
            if raw:
                return float(raw if isinstance(raw, str) else raw.decode())
        except Exception:  # noqa: BLE001
            pass
    rate: Optional[float] = None
    try:
        url = f"{settings.frankfurter_api_base_url.rstrip('/')}/latest"
        data = _http_get(url, {"from": base, "to": quote})
        rates = data.get("rates") if isinstance(data, dict) else None
        if isinstance(rates, dict) and rates.get(quote) is not None:
            rate = float(rates[quote])
    except Exception:  # noqa: BLE001
        rate = None
    if rate is None and base == "USD" and quote == "INR":
        rate = _USDINR_FALLBACK  # keep valuation alive; refreshed next fetch
    if rate is not None and rc is not None:
        try:
            rc.setex(key, _FX_TTL_S, json.dumps(rate))
        except Exception:  # noqa: BLE001
            pass
    return rate


def usd_to_inr(amount_usd: float) -> Optional[float]:
    """Convert a USD amount to INR at the current rate. None on total failure
    (never for USD given the static fallback)."""
    r = fx_rate("USD", "INR")
    return None if r is None else float(amount_usd) * r


def convert_to_inr(amount: float, currency: Optional[str]) -> Optional[float]:
    """Convert ``amount`` in ``currency`` to INR. INR/None passes through
    unchanged; USD and other currencies convert via fx_rate."""
    cur = (currency or "INR").upper().strip()
    if cur in ("INR", "", None):
        return float(amount)
    r = fx_rate(cur, "INR")
    return None if r is None else float(amount) * r
