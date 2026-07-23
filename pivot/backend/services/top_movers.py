"""Today's top gainers / losers from NIFTY 50.

Backs both the chat tool `get_top_gainers` and the workflow step
`fetch.top_movers`. yfinance is the live source (keyless, works for
NSE via the `.NS` suffix); a small Redis cache absorbs bursts and a
hardcoded seed list is the fallback when yfinance is unreachable.

Why a single shared service: chat-tool callers and workflow-step
callers both need the same data, and the cache TTL should be the same
across both. One module = one cache key prefix.

Cache TTL = 60 s. Top gainers move minute-to-minute during market
hours; 60 s is short enough that the dashboard / draft preview never
feels stale, long enough to absorb a chat-burst (a user clicking
through "today's gainers → backtest the top one → build an agent for
it") without hitting yfinance four times.

When yfinance fails (network down, rate-limited, `.NS` suffix
unrecognised), `_SEED_GAINERS` / `_SEED_LOSERS` are returned so the
agentic loop can still proceed — the user reviews the draft, sees the
seeded values labelled as such, and can edit before activating.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Literal

from backend.cache import redis_client

logger = logging.getLogger(__name__)


# NIFTY 50 constituents. Hand-curated to avoid a network dependency on
# the index registry for v1. Refresh manually when index reconstitution
# happens (NSE reviews semi-annually).
_NIFTY_50: tuple[str, ...] = (
    "RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK", "HINDUNILVR",
    "ITC", "SBIN", "BHARTIARTL", "KOTAKBANK", "BAJFINANCE", "LT",
    "AXISBANK", "MARUTI", "ASIANPAINT", "SUNPHARMA", "M&M",
    "ULTRACEMCO", "WIPRO", "NESTLEIND", "HCLTECH", "TITAN",
    "TATAMOTORS", "POWERGRID", "NTPC", "TATASTEEL", "BAJAJFINSV",
    "ONGC", "JSWSTEEL", "ADANIPORTS", "COALINDIA", "INDUSINDBK",
    "BAJAJ-AUTO", "TECHM", "GRASIM", "HINDALCO",
    "EICHERMOT", "DRREDDY", "BRITANNIA", "CIPLA", "APOLLOHOSP",
    "HEROMOTOCO", "BPCL", "TATACONSUM", "SBILIFE",
    "HDFCLIFE", "ADANIENT", "LTIM", "SHRIRAMFIN", "TRENT",
)

_UNIVERSES: dict[str, tuple[str, ...]] = {
    "nifty50": _NIFTY_50,
}

_CACHE_PREFIX = "top_movers:"
_CACHE_TTL_S = 60

# After BOTH live sources (Kite batch + yfinance) come back empty, skip
# re-trying them for this long and serve the seed immediately. Without
# it, every movers call inside the cool-down re-paid the full dual-source
# stall (~11s measured) just to fail identically.
_EMPTY_COOLDOWN_S = 45.0
_last_empty_at: float = 0.0


# Seed fallback. Marked with `seed=True` so the UI / model can disclose.
# Values are illustrative — refresh occasionally.
_SEED_GAINERS: list[dict[str, Any]] = [
    {"symbol": "BAJAJ-AUTO", "ltp": 9540.00, "change_pct": 3.2, "name": "Bajaj Auto", "seed": True},
    {"symbol": "HEROMOTOCO", "ltp": 4720.00, "change_pct": 2.8, "name": "Hero MotoCorp", "seed": True},
    {"symbol": "ADANIPORTS", "ltp": 1380.00, "change_pct": 2.4, "name": "Adani Ports", "seed": True},
    {"symbol": "TATASTEEL",  "ltp":  158.50, "change_pct": 2.0, "name": "Tata Steel", "seed": True},
    {"symbol": "ONGC",       "ltp":  255.30, "change_pct": 1.7, "name": "ONGC", "seed": True},
    {"symbol": "JSWSTEEL",   "ltp":  945.00, "change_pct": 1.5, "name": "JSW Steel", "seed": True},
    {"symbol": "M&M",        "ltp": 2960.00, "change_pct": 1.3, "name": "Mahindra & Mahindra", "seed": True},
    {"symbol": "POWERGRID",  "ltp":  315.00, "change_pct": 1.1, "name": "Power Grid Corp", "seed": True},
    {"symbol": "MARUTI",     "ltp": 12480.0, "change_pct": 1.0, "name": "Maruti Suzuki", "seed": True},
    {"symbol": "EICHERMOT",  "ltp": 5180.00, "change_pct": 0.9, "name": "Eicher Motors", "seed": True},
]


_SEED_LOSERS: list[dict[str, Any]] = [
    {"symbol": "INDUSINDBK", "ltp":  720.00, "change_pct": -3.4, "name": "IndusInd Bank", "seed": True},
    {"symbol": "BAJFINANCE", "ltp": 8290.00, "change_pct": -2.2, "name": "Bajaj Finance", "seed": True},
    {"symbol": "HDFCLIFE",   "ltp":  690.00, "change_pct": -1.8, "name": "HDFC Life", "seed": True},
    {"symbol": "DIVISLAB",   "ltp": 5760.00, "change_pct": -1.5, "name": "Divi's Labs", "seed": True},
    {"symbol": "CIPLA",      "ltp": 1480.00, "change_pct": -1.4, "name": "Cipla", "seed": True},
    {"symbol": "SUNPHARMA",  "ltp": 1740.00, "change_pct": -1.3, "name": "Sun Pharma", "seed": True},
    {"symbol": "ASIANPAINT", "ltp": 2280.00, "change_pct": -1.1, "name": "Asian Paints", "seed": True},
    {"symbol": "DRREDDY",    "ltp": 1290.00, "change_pct": -1.0, "name": "Dr Reddy's", "seed": True},
    {"symbol": "BRITANNIA",  "ltp": 5410.00, "change_pct": -0.9, "name": "Britannia", "seed": True},
    {"symbol": "WIPRO",      "ltp":  198.00, "change_pct": -0.7, "name": "Wipro", "seed": True},
]


def _cache_get(key: str) -> Any:
    try:
        raw = redis_client.get(key)
        if raw is None:
            return None
        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode()
        return json.loads(raw)
    except Exception as e:
        logger.debug("top_movers cache read failed %s: %s", key, e)
        return None


def _cache_set(key: str, value: Any) -> None:
    try:
        redis_client.set(key, json.dumps(value, default=str), ex=_CACHE_TTL_S)
    except Exception as e:
        logger.debug("top_movers cache write failed %s: %s", key, e)


def _fetch_live_movers(symbols: tuple[str, ...]) -> list[dict[str, Any]]:
    """Live price + day-change % for `symbols`. Kite-primary (one batch quote,
    broker-grade and working on cloud IPs), with a yfinance batch download as
    the backup. Returns rows with absolute values; the caller sorts / picks
    direction.
    """
    # ── Primary: a single Kite batch quote over the universe. last_price +
    #    previous close gives the day change directly. ──
    try:
        from backend.kite.live_quote import get_kite_quotes
        q = get_kite_quotes([f"NSE:{s}" for s in symbols])
        krows: list[dict[str, Any]] = []
        for s in symbols:
            d = q.get(f"NSE:{s}")
            if not d or not d.get("last_price"):
                continue
            ltp = float(d["last_price"])
            prev = d.get("prev_close") or ltp
            if not prev:
                continue
            krows.append({
                "symbol": s,
                "ltp": round(ltp, 2),
                "change_pct": round((ltp - prev) / prev * 100.0, 2),
                "seed": False,
            })
        if krows:
            return krows
    except Exception as e:  # noqa: BLE001 — fall through to yfinance
        logger.debug("top_movers kite batch failed: %s", e)

    # ── Backup: yfinance batch download (only when no live Kite session). ──
    try:
        import yfinance as yf  # type: ignore[import-untyped]
    except ImportError:
        logger.warning("yfinance not installed; top_movers using seed")
        return []

    tickers_str = " ".join(f"{s}.NS" for s in symbols)
    rows: list[dict[str, Any]] = []
    try:
        df = yf.download(
            tickers_str,
            period="2d",
            interval="1d",
            progress=False,
            group_by="ticker",
            auto_adjust=False,
            threads=True,
            # Bound the per-request wait — the unbounded default let a
            # rate-limited batch stall the chat tool for ~11s before
            # returning nothing (measured live, eval20 2026-07-22).
            timeout=8,
        )
    except Exception as e:
        logger.warning("yfinance.download failed for top_movers: %s", e)
        return []

    if df is None or df.empty:
        return []

    for sym in symbols:
        yf_sym = f"{sym}.NS"
        try:
            sub = df[yf_sym] if yf_sym in df.columns.get_level_values(0) else None
            if sub is None or sub.empty:
                continue
            closes = sub["Close"].dropna()
            if len(closes) < 1:
                continue
            ltp = float(closes.iloc[-1])
            prev = float(closes.iloc[-2]) if len(closes) >= 2 else ltp
            if prev == 0:
                continue
            change_pct = (ltp - prev) / prev * 100.0
            rows.append({
                "symbol": sym,
                "ltp": round(ltp, 2),
                "change_pct": round(change_pct, 2),
                "seed": False,
            })
        except Exception as e:
            logger.debug("top_movers parse failed for %s: %s", sym, e)
            continue

    return rows


def get_top_movers(
    direction: Literal["gainers", "losers"] = "gainers",
    universe: str = "nifty50",
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Return the top N movers in `direction` from `universe`.

    Cache key includes universe + direction + limit. Falls back to
    seed data when yfinance is unavailable; seeded rows are tagged
    `"seed": True` so callers can disclose to the user.
    """
    direction = "gainers" if direction not in ("gainers", "losers") else direction
    universe = universe if universe in _UNIVERSES else "nifty50"
    limit = max(1, min(int(limit or 1), 50))

    cache_key = f"{_CACHE_PREFIX}{universe}:{direction}:{limit}"
    cached = _cache_get(cache_key)
    if cached:
        return cached

    global _last_empty_at
    import time as _time
    if _time.monotonic() - _last_empty_at < _EMPTY_COOLDOWN_S:
        # Both live sources just failed — serve the seed instantly
        # instead of re-paying the dual-source stall.
        seed = _SEED_GAINERS if direction == "gainers" else _SEED_LOSERS
        return list(seed[:limit])

    symbols = _UNIVERSES[universe]
    rows = _fetch_live_movers(symbols)

    if not rows:
        _last_empty_at = _time.monotonic()
        seed = _SEED_GAINERS if direction == "gainers" else _SEED_LOSERS
        result = list(seed[:limit])
        # Don't cache seed in Redis — the cool-down above already
        # bounds the retry rate; a fresh attempt resumes in <=45s.
        return result

    rows.sort(
        key=lambda r: r["change_pct"],
        reverse=(direction == "gainers"),
    )
    result = rows[:limit]
    _cache_set(cache_key, result)
    return result
