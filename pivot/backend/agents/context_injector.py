"""
backend/agents/context_injector.py

Builds compact market + portfolio context block from Redis.
Injected into every Sarvam call. Always < 250 tokens. Always < 5ms.
"""

import json
import logging

from backend.cache import get_redis
from backend.utils.time_utils import now_ist, format_ist_short
from backend.safety import is_market_open

logger = logging.getLogger(__name__)


def _cached_price(symbol: str) -> dict | None:
    try:
        rc = get_redis()
        raw = rc.get(f"price:{symbol.replace(' ', '_')}")
        if raw is None:
            return None
        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode()
        return json.loads(raw)
    except Exception as e:
        logger.debug(f"price cache miss for {symbol}: {e}")
        return None


def build_context_block(holdings: list = None) -> str:
    """Returns context string to prepend to system prompt."""
    now = now_ist()
    status = "OPEN" if is_market_open() else "CLOSED"
    lines = [f"Market: {status} | {format_ist_short(now)}"]

    parts = []
    for sym, label in [("NIFTY 50", "Nifty"), ("NIFTY_BANK", "BankNifty")]:
        d = _cached_price(sym)
        if d:
            pct = d.get("change_pct", 0)
            try:
                ltp = float(d.get("ltp", 0))
            except (TypeError, ValueError):
                ltp = 0
            parts.append(f"{label}: {ltp:,.0f} ({'+' if pct >= 0 else ''}{pct:.1f}%)")
    if parts:
        lines.append(" | ".join(parts))

    if holdings:
        lines.append("Your holdings (top 5):")
        for h in sorted(
            holdings,
            key=lambda x: x.get("last_price", 0) * x.get("quantity", 0),
            reverse=True,
        )[:5]:
            sym = h.get("tradingsymbol", "")
            d = _cached_price(sym)
            ltp = d["ltp"] if d else h.get("last_price", 0)
            pnl = h.get("pnl", 0)
            lines.append(
                f"  {sym}: ₹{ltp:,.2f} x{h.get('quantity', 0)} "
                f"P&L:{'+' if pnl >= 0 else ''}₹{abs(pnl):,.0f}"
            )
    return "\n".join(lines)
