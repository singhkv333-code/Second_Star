"""v2 tool handlers — the ones that replaced ``_generic_confirm`` stubs."""
from __future__ import annotations

import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import yaml

from backend.kite.market_data import get_historical_ohlcv


logger = logging.getLogger(__name__)


_PRODUCTS_PATH = Path(__file__).resolve().parents[1] / "config" / "products.yaml"


def _load_products() -> dict:
    return yaml.safe_load(_PRODUCTS_PATH.read_text(encoding="utf-8")) or {}


# ---- get_price_history --------------------------------------------------


_PERIOD_DAYS = {
    "1mo": 30, "3mo": 90, "6mo": 180,
    "1y": 365, "2y": 730, "5y": 1825,
}


async def get_price_history(args: dict) -> dict:
    symbol = (args.get("symbol") or "").strip().upper()
    period = (args.get("period") or "1y").lower()
    if not symbol:
        raise ValueError("symbol is required")
    if period not in _PERIOD_DAYS:
        period = "1y"

    try:
        ohlcv = get_historical_ohlcv(symbol, period=period)
    except Exception as e:
        # Real error path — surface it. Never fake data.
        raise RuntimeError(f"could not fetch price history for {symbol}: {e}") from None

    if not ohlcv:
        return {"symbol": symbol, "period": period, "n": 0,
                "summary": "no data available"}

    first = ohlcv[0]
    last = ohlcv[-1]
    high = max(row.get("high", 0) for row in ohlcv)
    low = min(row.get("low", 1e9) for row in ohlcv if row.get("low") is not None)
    pct = ((last.get("close") or 0) / first.get("close") - 1) * 100 if first.get("close") else 0

    return {
        "symbol": symbol,
        "period": period,
        "n_candles": len(ohlcv),
        "first": {"date": first.get("date"), "close": first.get("close")},
        "last": {"date": last.get("date"), "close": last.get("close")},
        "high": high,
        "low": low,
        "period_return_pct": round(pct, 2),
        "summary":
            f"{symbol} traded between ₹{low:,.2f} and ₹{high:,.2f} over the past "
            f"{period}; {pct:+.2f}% net.",
    }


# ---- get_52wk_range -----------------------------------------------------


async def get_52wk_range(args: dict) -> dict:
    symbol = (args.get("symbol") or "").strip().upper()
    if not symbol:
        raise ValueError("symbol is required")
    try:
        ohlcv = get_historical_ohlcv(symbol, period="1y")
    except Exception as e:
        raise RuntimeError(f"could not fetch 52w range for {symbol}: {e}") from None

    if not ohlcv:
        return {"symbol": symbol, "available": False,
                "summary": "no data available"}

    high = max(r.get("high", 0) for r in ohlcv)
    low = min(r.get("low", 1e9) for r in ohlcv if r.get("low") is not None)
    last = ohlcv[-1].get("close")
    pct_from_high = ((last - high) / high * 100) if high else None
    pct_from_low = ((last - low) / low * 100) if low else None
    return {
        "symbol": symbol,
        "available": True,
        "high_52w": round(high, 2),
        "low_52w": round(low, 2),
        "last_close": round(last, 2) if last is not None else None,
        "pct_from_high": round(pct_from_high, 2) if pct_from_high is not None else None,
        "pct_from_low": round(pct_from_low, 2) if pct_from_low is not None else None,
    }


# ---- get_product_spec ---------------------------------------------------


async def get_product_spec(args: dict) -> dict:
    product = (args.get("product") or "").strip().lower()
    products = _load_products()
    if product not in products:
        return {"available": False,
                "products_known": list(products.keys()),
                "error": f"unknown product '{product}'"}
    spec = products[product]
    return {"available": True, "product": product, "spec": spec}


# ---- build_product ------------------------------------------------------


async def build_product(args: dict) -> dict:
    """Constructs a fully-sized synthetic security via the structured builders.

    Routes to the matching builder in `agents.structured_builder` based on
    the `product` arg. SafeGrow and StormShield take a horizon; Barbell
    ignores it. Errors are returned as a structured payload rather than
    raised so the chat hop can narrate them.
    """
    from backend.agents.structured_builder import PRODUCT_BUILDERS

    product = (args.get("product") or "").strip().lower()
    capital = args.get("capital")
    horizon_months = args.get("horizon_months") or 12

    if product not in PRODUCT_BUILDERS:
        return {"success": False,
                "error": f"unknown product '{product}'",
                "products_known": sorted(PRODUCT_BUILDERS.keys())}
    if capital is None:
        return {"success": False, "error": "capital is required (in INR)"}
    try:
        capital = float(capital)
    except (TypeError, ValueError):
        return {"success": False, "error": "capital must be a number"}
    if capital <= 0:
        return {"success": False, "error": "capital must be positive"}

    try:
        horizon_months = int(horizon_months)
    except (TypeError, ValueError):
        horizon_months = 12

    builder = PRODUCT_BUILDERS[product]
    try:
        result = await builder(capital=capital, horizon_months=horizon_months)
    except ValueError as e:
        return {"success": False, "error": str(e)}
    except Exception as e:
        logger.exception("build_product %s failed: %s", product, e)
        return {"success": False, "error": f"could not build {product}: {e}"}

    # Tag the result so the chat router lifts it to top-level raw_data
    # and the frontend dispatcher renders the SyntheticSecurityCard
    # instead of falling through to plain prose.
    return {"success": True, "_render_hint": "synthetic_security_card", **result}
