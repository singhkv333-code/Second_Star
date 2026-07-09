"""Mark-price resolution for paper fills.

A paper order needs a price to fill against. Priority:
  1. Real Kite live quote — only when a genuine session exists (not mock,
     not the placeholder token). In mock mode get_live_quote returns a
     flat ₹100 for every symbol, which is useless for a portfolio, so we
     skip it there.
  2. yfinance last close — real per-symbol price, no auth, works in the
     default mock/dev environment. (Network; tests inject a price_fn and
     never reach here.)
  3. The screener's shared, Redis-cached market-metrics price (real, up to
     ~10 min stale) — covers the window where yfinance is transiently rate
     limited (Yahoo 429s routinely outlast a single request's retry
     budget) but the symbol has been priced recently by anyone's screener
     read. Never fabricated — just a slightly older real observation.
  4. None — the broker rejects the order with reason 'price_unavailable'.

P1 marks at fill time only. The intraday/EOD mark-to-market loop that
revalues open positions + snapshots NAV is P3.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Optional

from backend.paper.money import to_money


def get_option_mark(symbol: str) -> Optional[Decimal]:
    """Mark an option tradingsymbol off the chain cache (F&O P2).

    Resolves the contract through the instrument master, fetches the
    (5s-cached) chain slice for its (underlying, expiry) and returns the
    leg's MID. Own short-lived session — get_mark_price has no db param
    and threading one through every equity call site isn't worth it.
    Returns None when the contract is unknown or unquoted (caller falls
    through / rejects honestly)."""
    try:
        from backend.database import SessionLocal
        from backend.market.option_chain import get_chain
        from backend.models import InstrumentMaster

        db = SessionLocal()
        try:
            inst = (
                db.query(InstrumentMaster)
                .filter(
                    InstrumentMaster.tradingsymbol == str(symbol).upper(),
                    InstrumentMaster.instrument_type.in_(("CE", "PE")),
                )
                .first()
            )
            if inst is None or inst.expiry is None:
                return None
            chain = get_chain(
                db, inst.underlying, inst.expiry.isoformat(), width=25,
            )
            if not chain:
                return None
            for row in chain["rows"]:
                for side in ("ce", "pe"):
                    q = row.get(side)
                    if q and q.get("tradingsymbol") == inst.tradingsymbol:
                        mid = q.get("mid") or q.get("ltp")
                        return to_money(mid) if mid and float(mid) > 0 else None
            return None
        finally:
            db.close()
    except Exception:
        return None


def _looks_like_option(symbol: str) -> bool:
    """Cheap shape test for NSE/BSE/MCX option tradingsymbols — ends in
    CE/PE with digits before it. Avoids a DB hit per equity mark."""
    s = str(symbol).upper()
    return len(s) > 6 and s[-2:] in ("CE", "PE") and any(c.isdigit() for c in s[:-2])


def user_kite_token(db, user_id: int) -> str:
    """The user's LIVE Kite access token, or ``"mock_token"`` when they have no
    active broker session. Threading this into ``get_mark_price`` is what makes
    a paper account mark against LIVE Kite LTP (during market hours) once the
    user logs into Kite / opens the websocket in the morning — otherwise marks
    fall back to the yfinance last close. Defensive: any lookup error → mock."""
    try:
        from backend.brokers.sessions import get_active_broker_session
        from backend.kite.auth import read_kite_access_token
        sess = get_active_broker_session(db, int(user_id))
        if sess is not None:
            tok = read_kite_access_token(sess)
            if tok:
                return tok
    except Exception:
        pass
    return "mock_token"


def get_mark_price(symbol: str, token: str = "mock_token") -> Optional[Decimal]:
    sym = str(symbol).upper()

    # 0. Option contracts mark through the chain (F&O P2) — the equity
    # paths below can't price them (yfinance has no NFO symbols; a Kite
    # equity quote would need the NFO:/MCX: prefix the chain service
    # already handles). On a chain miss we fall through, in case an
    # equity ticker happens to end in CE/PE.
    if _looks_like_option(sym):
        mark = get_option_mark(sym)
        if mark is not None:
            return mark

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

    # 3. Shared screener market-metrics cache (Redis, ~10 min TTL) — a REAL
    # price recently observed by the screener's own warm/top-up pipeline.
    # Covers a transient yfinance rate-limit that would otherwise reject a
    # perfectly normal paper order for a symbol that's actually been priced
    # moments ago (e.g. while the user was just browsing the Screener).
    try:
        import json

        from backend.cache import redis_client
        from backend.routers.screener import _METRICS_CACHE_KEY

        raw = redis_client.get(_METRICS_CACHE_KEY)
        if raw:
            parsed = json.loads(raw.decode() if isinstance(raw, (bytes, bytearray)) else raw)
            rec = (parsed.get("m") or {}).get(sym)
            price = rec.get("price") if rec else None
            if price and float(price) > 0:
                return to_money(price)
    except Exception:
        pass

    # 4. No price.
    return None
