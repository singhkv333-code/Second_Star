"""Mark-price resolution for paper fills.

A paper order needs a price to fill against. Priority:
  1. Real Kite live quote — only when a genuine session exists (not mock,
     not the placeholder token). In mock mode get_live_quote returns a
     flat ₹100 for every symbol, which is useless for a portfolio, so we
     skip it there.
  2. yfinance last close — real per-symbol price, no auth, works in the
     default mock/dev environment. (Network; tests inject a price_fn and
     never reach here.)
  3. None — the broker rejects the order with reason 'price_unavailable'.

P1 marks at fill time only. The intraday/EOD mark-to-market loop that
revalues open positions + snapshots NAV is P3.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Optional

from backend.paper.money import to_money


def get_mark_price(symbol: str, token: str = "mock_token") -> Optional[Decimal]:
    sym = str(symbol).upper()

    # 1. Real Kite live quote (only trust a genuine session).
    from backend.kite.auth import KITE_MOCK_MODE
    if not KITE_MOCK_MODE and token and token != "mock_token":
        try:
            from backend.kite.market_data import get_live_quote
            inst = f"NSE:{sym}"
            quotes = get_live_quote(token, [inst]) or {}
            lp = (quotes.get(inst) or {}).get("last_price")
            if lp and float(lp) > 0:
                return to_money(lp)
        except Exception:
            pass

    # 2. yfinance last close — real per-symbol price for mock/dev.
    try:
        from backend.kite.market_data import get_historical_ohlcv
        bars = get_historical_ohlcv(sym, period="5d")
        if bars:
            close = bars[-1].get("close")
            if close and float(close) > 0:
                return to_money(close)
    except Exception:
        pass

    # 3. No price.
    return None
