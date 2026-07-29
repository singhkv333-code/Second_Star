"""Per-asset-class market hours.

A single INR paper book now holds Indian equities/ETFs, US equities/ETFs, and
crypto — each on a DIFFERENT clock:
  - Indian : NSE 09:15–15:30 IST, Mon–Fri.
  - US     : NYSE/Nasdaq regular session 09:30–16:00 ET, Mon–Fri (DST-correct,
             which is ~19:00–01:30 IST depending on US daylight saving).
  - Crypto : 24/7.

`is_market_open_for(asset_class)` gates paper fills so a US order fills during
the US session (not NSE hours) and a crypto order fills any time. US hours are
computed in America/New_York directly, so daylight saving is handled without
any manual IST offset math.
"""
from __future__ import annotations

import logging
from datetime import datetime, time
from typing import Optional

logger = logging.getLogger(__name__)

_US_OPEN = time(9, 30)   # 09:30 ET
_US_CLOSE = time(16, 0)  # 16:00 ET


def _us_market_open(now: Optional[datetime] = None) -> bool:
    try:
        from zoneinfo import ZoneInfo
        et = now.astimezone(ZoneInfo("America/New_York")) if now else datetime.now(ZoneInfo("America/New_York"))
        if et.weekday() >= 5:  # Sat/Sun
            return False
        return _US_OPEN <= et.time() <= _US_CLOSE
    except Exception:  # noqa: BLE001 — never let a clock error break a fill path
        return False


def is_market_open_for(asset_class: Optional[str], now: Optional[datetime] = None) -> bool:
    """True when the venue for ``asset_class`` is currently open.

    asset_class ∈ {in_equity, in_etf, us_equity, us_etf, crypto}. Unknown /
    None defaults to the Indian (NSE) calendar — the conservative default for
    this India-first book."""
    ac = (asset_class or "").lower()
    if ac == "crypto":
        return True  # 24/7
    if ac in ("us_equity", "us_etf"):
        return _us_market_open(now)
    # Indian equity/ETF (and unknown) → NSE.
    try:
        from backend.utils.time_utils import is_market_open
        return bool(is_market_open())
    except Exception:  # noqa: BLE001
        return False


def asset_class_for_symbol(symbol: str) -> str:
    """Resolve a symbol's asset class (cheap classify). Falls back to
    'in_equity' on any error so gating stays on the safe NSE calendar."""
    try:
        from backend.view_markets.security_meta import classify
        return str(classify(symbol).get("asset_class") or "in_equity")
    except Exception:  # noqa: BLE001
        return "in_equity"


def is_market_open_for_symbol(symbol: str, now: Optional[datetime] = None) -> bool:
    """Convenience: market-open check keyed by a raw symbol."""
    return is_market_open_for(asset_class_for_symbol(symbol), now)


def any_equity_market_open(now: Optional[datetime] = None) -> bool:
    """True when EITHER the NSE (Indian) or the US session is open. Used to
    decide whether the paper resting-order / mark jobs should run at all; the
    jobs then gate per-symbol so an Indian order doesn't fill during US hours
    and vice-versa. (Crypto is 24/7 — its synchronous MARKET fills don't depend
    on this; a resting crypto order in the NSE+US dead window is a known edge.)"""
    try:
        from backend.utils.time_utils import is_market_open
        if is_market_open():
            return True
    except Exception:  # noqa: BLE001
        pass
    return _us_market_open(now)
