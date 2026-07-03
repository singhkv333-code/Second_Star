"""Runtime yfinance price-reliability guard.

Honesty contract (CLAUDE.md): a wrong price with a "(yfinance, EOD)" tag is
still a wrong price. Yahoo's NSE feed occasionally serves a mispriced series
for a symbol (unadjusted corporate action, wrong listing). Rather than a
hand-curated deny-list — a 2026-07-04 live sweep found yfinance agreeing
with Kite within 5% on 74/76 universe names, so a static list would rot —
this guard measures divergence AT RUNTIME:

  - While Kite is live, every screener metrics refresh compares the yfinance
    price against the broker price per symbol. |yf − kite|/kite above
    ``_THRESHOLD_PCT`` flags the symbol (Redis hash, self-expiring); back
    within threshold clears it.
  - While Kite is DOWN (the ~7:30 IST token death), yfinance-sourced values
    for flagged symbols are suppressed to null — the FE renders "—" instead
    of a confidently wrong number.

Never raises; Redis failures degrade to "nothing flagged" (fail-open on the
guard, since the base data is usually right — the flag is the exception).
"""
from __future__ import annotations

import logging
import time

from backend.cache import redis_client

logger = logging.getLogger(__name__)

_HASH_KEY = "yf:price_unreliable:v1"
_THRESHOLD_PCT = 7.5   # generous vs EOD-vs-live drift; catches scale errors
_ENTRY_TTL_S = 7 * 24 * 3600  # re-earn trust after a week unrefreshed


def check_and_flag(symbol: str, yf_price: float, kite_price: float) -> bool:
    """Compare one symbol's yfinance price against the live broker price.
    Flags (returns True) on divergence beyond threshold; clears any existing
    flag when back in line."""
    sym = (symbol or "").strip().upper()
    if not sym or not yf_price or not kite_price or kite_price <= 0:
        return False
    dev_pct = abs(yf_price - kite_price) / kite_price * 100
    try:
        if dev_pct > _THRESHOLD_PCT:
            redis_client.hset(
                _HASH_KEY, sym,
                f"{time.time():.0f}|kite={kite_price:.2f}|yf={yf_price:.2f}"
                f"|dev={dev_pct:.1f}%",
            )
            logger.warning(
                "[price_guard] %s flagged unreliable on yfinance "
                "(kite=%.2f yf=%.2f dev=%.1f%%)",
                sym, kite_price, yf_price, dev_pct,
            )
            return True
        redis_client.hdel(_HASH_KEY, sym)
    except Exception:  # noqa: BLE001 — guard is best-effort
        pass
    return False


def unreliable_symbols() -> set[str]:
    """Symbols currently flagged (lazily expiring stale entries)."""
    try:
        raw = redis_client.hgetall(_HASH_KEY) or {}
    except Exception:  # noqa: BLE001
        return set()
    now = time.time()
    out: set[str] = set()
    for k, v in raw.items():
        sym = k.decode() if isinstance(k, (bytes, bytearray)) else str(k)
        val = v.decode() if isinstance(v, (bytes, bytearray)) else str(v)
        try:
            ts = float(val.split("|", 1)[0])
        except (ValueError, IndexError):
            ts = 0.0
        if now - ts > _ENTRY_TTL_S:
            try:
                redis_client.hdel(_HASH_KEY, sym)
            except Exception:  # noqa: BLE001
                pass
            continue
        out.add(sym.upper())
    return out


__all__ = ["check_and_flag", "unreliable_symbols"]
