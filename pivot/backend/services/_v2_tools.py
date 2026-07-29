"""v2 tool handlers — the ones that replaced ``_generic_confirm`` stubs."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

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


def _sma(closes: list[float], n: int) -> Optional[float]:
    if len(closes) < n:
        return None
    return round(sum(closes[-n:]) / n, 2)


def _rsi(closes: list[float], n: int = 14) -> Optional[float]:
    """Wilder's RSI over the last n periods."""
    if len(closes) < n + 1:
        return None
    gains, losses = [], []
    for i in range(-n, 0):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    avg_gain = sum(gains) / n
    avg_loss = sum(losses) / n
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - 100 / (1 + rs), 1)


def _ret_pct(closes: list[float], bars: int) -> Optional[float]:
    if len(closes) <= bars or closes[-bars - 1] == 0:
        return None
    return round((closes[-1] / closes[-bars - 1] - 1) * 100, 2)


async def get_price_history(args: dict) -> dict:
    """Rich, interpretable price history + technicals for a symbol, sourced
    from Kite (live, correctly-dated) with a yfinance fallback. Returns
    derived metrics AND a recent OHLCV tail so the LLM can read the chart
    itself and form its own technical view — it is NOT a fixed verdict."""
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
        return {"symbol": symbol, "period": period, "n_candles": 0,
                "summary": "no price history available"}

    closes = [float(r["close"]) for r in ohlcv if r.get("close") is not None]
    first, last = ohlcv[0], ohlcv[-1]
    last_close = closes[-1]
    high = round(max(r.get("high", 0) for r in ohlcv), 2)
    low = round(min(r.get("low", 1e9) for r in ohlcv if r.get("low") is not None), 2)
    pct = round((last_close / closes[0] - 1) * 100, 2) if closes[0] else 0.0

    sma20, sma50, sma200 = _sma(closes, 20), _sma(closes, 50), _sma(closes, 200)
    rsi14 = _rsi(closes, 14)

    # Position vs moving averages (plain facts; LLM interprets the meaning).
    above = [f"SMA{n}" for n, v in (("20", sma20), ("50", sma50), ("200", sma200))
             if v is not None and last_close > v]
    below = [f"SMA{n}" for n, v in (("20", sma20), ("50", sma50), ("200", sma200))
             if v is not None and last_close < v]

    return {
        "symbol": symbol,
        "period": period,
        "source": "kite/yfinance",
        "n_candles": len(ohlcv),
        "last_close": round(last_close, 2),
        "as_of": last.get("date"),
        "first": {"date": first.get("date"), "close": round(closes[0], 2)},
        "period_high": high,
        "period_low": low,
        "period_return_pct": pct,
        "returns_pct": {
            "1w": _ret_pct(closes, 5), "1m": _ret_pct(closes, 21),
            "3m": _ret_pct(closes, 63), "6m": _ret_pct(closes, 126),
            "1y": _ret_pct(closes, 252),
        },
        "sma": {"20": sma20, "50": sma50, "200": sma200},
        "rsi14": rsi14,
        "vs_moving_avgs": {"above": above, "below": below},
        "pct_from_period_high": round((last_close - high) / high * 100, 2) if high else None,
        "pct_from_period_low": round((last_close - low) / low * 100, 2) if low else None,
        # Recent tail so the model can eyeball the actual trajectory.
        "recent": [
            {"date": r.get("date"), "close": r.get("close"), "volume": r.get("volume")}
            for r in ohlcv[-20:]
        ],
        "summary":
            f"{symbol} ₹{last_close:,.2f} as of {last.get('date')}; "
            f"{pct:+.2f}% over {period} (range ₹{low:,.2f}–₹{high:,.2f}); "
            f"RSI14 {rsi14}; "
            + (f"above {', '.join(above)}" if above else "")
            + (f"; below {', '.join(below)}" if below else "")
            + ". Interpret these numbers yourself — not a fixed signal.",
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
