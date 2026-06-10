"""Market-data REST endpoints — back the redesigned dashboard + stock-snapshot
surfaces in pivot-next/.

Three endpoints:
  - GET /api/markets/indices               → 4 NSE/BSE benchmarks for the dashboard cards
  - GET /api/markets/quote/{symbol}        → full snapshot (OHLC, 52w, mcap, P/E)
  - GET /api/markets/sparkline/{symbol}    → historical close series for the price chart

All three use yfinance under the hood (already in requirements.txt). yfinance is
keyless and works for NSE symbols via the `.NS` suffix; we cache nothing for v1
because the dashboard refreshes on tab change rather than polling, and the
stock-snapshot card is a per-click fetch.

When yfinance returns nothing (network blip, unknown symbol, rate-limit), the
endpoints raise the canonical `not_yet_available` error envelope so the
frontend can surface the message verbatim — never fake data
(ARCHITECTURE.md §5.2 footnote).
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Literal

import yfinance as yf  # type: ignore[import-untyped]
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.cache import redis_client
from backend.database import get_db
from backend.kite.ticker import cache_key as ticker_cache_key
from backend.routers._deps import require_user
from backend.routers._errors import http_error

router = APIRouter(prefix="/api/markets", tags=["Markets"])
logger = logging.getLogger(__name__)


# WHY a 10-second TTL: index levels move tick-by-tick during market
# hours but the dashboard / chat doesn't need sub-second freshness.
# 10 s collapses bursts of repeat queries (e.g. "what's NIFTY",
# "and SENSEX", "BANKNIFTY") from 4 yfinance round-trips into 1.
# Outside market hours the value barely moves, so even a stale
# 10 s read is harmless. Keep it short enough that the dashboard
# never feels stuck.
_INDEX_LEVEL_TTL_S = 10
_INDEX_LEVEL_PREFIX = "index:level:"


# ── Response models ──────────────────────────────────────────────────


class IndexQuote(BaseModel):
    name: str               # "NIFTY 50"
    symbol: str             # "^NSEI"
    value: float            # last close
    change: float           # absolute (signed)
    change_pct: float       # percent (signed)
    last_updated: datetime


class IndicesResponse(BaseModel):
    items: list[IndexQuote]


class StockQuote(BaseModel):
    symbol: str
    name: str
    exchange: str
    sector: str | None
    industry: str | None
    ltp: float
    change: float
    change_pct: float
    open: float
    high: float
    low: float
    prev_close: float
    volume: float
    w52_high: float | None
    w52_low: float | None
    market_cap: float | None
    pe_ratio: float | None
    last_updated: datetime
    # Phase 2: surface whether the quote came from the Kite live feed
    # (WS) or the REST/yfinance fallback. UIs can grey-out delayed
    # quotes; old callers ignore the fields entirely (defaults preserve
    # existing behaviour).
    live: bool = False
    source: Literal["kite_ws", "kite_rest", "yfinance"] = "yfinance"


class SparklinePoint(BaseModel):
    t: datetime    # timestamp
    v: float       # close price


class SparklineResponse(BaseModel):
    symbol: str
    range: str
    interval: str
    points: list[SparklinePoint]


class OhlcBar(BaseModel):
    t: datetime    # bar timestamp
    o: float       # open
    h: float       # high
    l: float       # low
    c: float       # close
    v: int = 0     # volume


class OhlcResponse(BaseModel):
    symbol: str
    range: str
    interval: str
    source: str    # "kite" | "yfinance"
    bars: list[OhlcBar]


# ── Indices endpoint (dashboard) ─────────────────────────────────────


# Map display name → yfinance ticker. NSEMDCP100 isn't always exposed via
# yfinance's `^` shortcut; we fall back to the .NS form. If neither works
# at request time we'll surface NotYetAvailable for that single index
# without failing the whole endpoint.
_INDEX_TICKERS: list[tuple[str, str]] = [
    ("NIFTY 50", "^NSEI"),
    ("SENSEX", "^BSESN"),
    ("BANK NIFTY", "^NSEBANK"),
    ("NIFTY MIDCAP 100", "^NSEMDCP50"),
]


def _fetch_index(name: str, ticker_symbol: str) -> IndexQuote | None:
    """Best-effort fetch via yfinance with a 10s Redis cache.

    Returns None on any failure so the caller can omit the failed
    index without 500'ing the whole list.

    WHY caching here: the chat path (`get_index_level`) and the
    dashboard both ask for these four indices repeatedly within
    seconds. Without the cache, every query was a fresh yfinance
    round-trip (~500-1500ms each) and risked rate-limiting on the
    keyless yfinance endpoint. The Redis hit collapses the burst.
    """
    cache_key = f"{_INDEX_LEVEL_PREFIX}{ticker_symbol}"
    try:
        raw = redis_client.get(cache_key)
        if raw:
            data = json.loads(raw if isinstance(raw, str) else raw.decode())
            return IndexQuote(
                name=data["name"],
                symbol=data["symbol"],
                value=data["value"],
                change=data["change"],
                change_pct=data["change_pct"],
                last_updated=datetime.fromisoformat(data["last_updated"]),
            )
    except Exception as e:
        logger.debug("[markets] cache read miss for %s: %s", cache_key, e)

    try:
        ticker = yf.Ticker(ticker_symbol)
        hist = ticker.history(period="2d", interval="1d")
        if hist.empty or len(hist) < 1:
            return None
        latest_close = float(hist["Close"].iloc[-1])
        prev_close = float(hist["Close"].iloc[-2]) if len(hist) >= 2 else latest_close
        change = latest_close - prev_close
        change_pct = (change / prev_close * 100) if prev_close > 0 else 0.0
        quote = IndexQuote(
            name=name,
            symbol=ticker_symbol,
            value=round(latest_close, 2),
            change=round(change, 2),
            change_pct=round(change_pct, 2),
            last_updated=datetime.now(timezone.utc),
        )
        try:
            redis_client.set(
                cache_key,
                json.dumps({
                    "name": quote.name,
                    "symbol": quote.symbol,
                    "value": quote.value,
                    "change": quote.change,
                    "change_pct": quote.change_pct,
                    "last_updated": quote.last_updated.isoformat(),
                }),
                ex=_INDEX_LEVEL_TTL_S,
            )
        except Exception as e:
            logger.debug("[markets] cache write failed for %s: %s", cache_key, e)
        return quote
    except Exception as e:
        logger.warning("[markets] index fetch failed for %s (%s): %s",
                       name, ticker_symbol, e)
        return None


@router.get(
    "/indices",
    response_model=IndicesResponse,
    summary="Get current NSE/BSE benchmark indices",
)
def get_indices(
    _user_id: int = Depends(require_user),
) -> IndicesResponse:
    items: list[IndexQuote] = []
    for name, sym in _INDEX_TICKERS:
        q = _fetch_index(name, sym)
        if q is not None:
            items.append(q)
    if not items:
        # Network completely down or yfinance rate-limited → surface to FE
        # via canonical envelope rather than returning an empty list (which
        # the dashboard would render as a blank strip).
        raise http_error(
            status_code=503,
            code="not_yet_available",
            message="market indices unavailable — yfinance source is unreachable",
        )
    return IndicesResponse(items=items)


# ── Stock snapshot ───────────────────────────────────────────────────


@router.get(
    "/quote/{symbol}",
    response_model=StockQuote,
    summary="Get a full stock snapshot (OHLC + 52w + market cap + P/E)",
)
def get_quote(
    symbol: str,
    exchange: str = Query("NSE", pattern="^(NSE|BSE)$"),
    _user_id: int = Depends(require_user),
) -> StockQuote:
    sym = symbol.upper().strip()
    if not sym:
        raise http_error(400, "validation_error", "symbol is required")

    # ─ Phase 2: prefer the Kite tick cache if the entry is fresh
    # (within 5 s). When the ticker isn't running or this symbol isn't
    # in its universe, fall through to the existing yfinance path.
    cached = _read_cached_kite_tick(sym)
    if cached is not None:
        return cached

    # [C4] resolve_symbol maps index aliases (NIFTY→^NSEI, SENSEX→^BSESN,
    # BANKNIFTY→^NSEBANK) and shorthand (RIL→RELIANCE) to real yfinance
    # tickers. The old naive ".NS" suffix produced dead tickers
    # (SENSEX.NS / RIL.NS) → "no quote available". Keep explicit BSE
    # lookups for plain (non-index, non-alias) symbols.
    from backend.market.yfinance_service import (
        resolve_symbol, INDEX_TICKERS, NAME_TO_TICKER,
    )
    if sym.endswith((".NS", ".BO")) or sym.startswith("^"):
        yf_symbol = sym
    elif (exchange == "BSE"
          and sym.upper() not in INDEX_TICKERS
          and sym.lower() not in NAME_TO_TICKER):
        yf_symbol = f"{sym}.BO"
    else:
        yf_symbol = resolve_symbol(sym)

    try:
        ticker = yf.Ticker(yf_symbol)
        info = ticker.info or {}
        hist = ticker.history(period="5d", interval="1d")
    except Exception as e:
        raise http_error(
            503, "not_yet_available",
            f"yfinance lookup failed for {sym}: {str(e)[:160]}",
        )

    if hist.empty:
        raise http_error(
            404, "not_found",
            f"no quote available for {sym}.{exchange}",
        )

    latest = hist.iloc[-1]
    prev_close = float(hist["Close"].iloc[-2]) if len(hist) >= 2 else float(latest["Open"])
    ltp = float(latest["Close"])
    change = ltp - prev_close
    change_pct = (change / prev_close * 100) if prev_close > 0 else 0.0

    return StockQuote(
        symbol=sym,
        name=str(info.get("longName") or info.get("shortName") or sym),
        exchange=exchange,
        sector=info.get("sector"),
        industry=info.get("industry"),
        ltp=round(ltp, 2),
        change=round(change, 2),
        change_pct=round(change_pct, 2),
        open=round(float(latest["Open"]), 2),
        high=round(float(latest["High"]), 2),
        low=round(float(latest["Low"]), 2),
        prev_close=round(prev_close, 2),
        volume=float(latest.get("Volume", 0) or 0),
        w52_high=_safe_float(info.get("fiftyTwoWeekHigh")),
        w52_low=_safe_float(info.get("fiftyTwoWeekLow")),
        market_cap=_safe_float(info.get("marketCap")),
        pe_ratio=_safe_float(info.get("trailingPE")),
        last_updated=datetime.now(timezone.utc),
        live=False,
        source="yfinance",
    )


# Max age of a Redis-cached tick we'll treat as "live". The ticker
# pushes ~1 msg/sec/symbol on a busy stock, so 5s is generous; the
# scheduler/quoting fallback handles older entries.
_KITE_TICK_FRESH_SECONDS = 5


def _read_cached_kite_tick(symbol: str) -> StockQuote | None:
    """Return a StockQuote built from the Kite tick cache if fresh."""
    try:
        raw = redis_client.get(ticker_cache_key(symbol))
        if raw is None:
            return None
        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode()
        data = json.loads(raw)
    except Exception:
        return None
    ts = data.get("ts")
    if not isinstance(ts, (int, float)):
        return None
    if (int(time.time()) - int(ts)) > _KITE_TICK_FRESH_SECONDS:
        return None
    ltp = data.get("ltp")
    if not isinstance(ltp, (int, float)):
        return None
    prev_close = data.get("prev_close")
    try:
        prev_close_f = float(prev_close) if prev_close is not None else 0.0
    except (TypeError, ValueError):
        prev_close_f = 0.0
    change_pct = data.get("change_pct") or 0.0
    try:
        change_pct_f = float(change_pct)
    except (TypeError, ValueError):
        change_pct_f = 0.0
    change = float(ltp) - prev_close_f if prev_close_f else 0.0
    source = data.get("src") if isinstance(data.get("src"), str) else "kite_ws"
    source_typed: Literal["kite_ws", "kite_rest"] = (
        "kite_rest" if source == "kite_rest" else "kite_ws"
    )
    last_updated = datetime.fromtimestamp(int(ts), tz=timezone.utc)
    return StockQuote(
        symbol=symbol,
        name=symbol,
        exchange="NSE",
        sector=None,
        industry=None,
        ltp=round(float(ltp), 2),
        change=round(change, 2),
        change_pct=round(change_pct_f, 2),
        open=_or_zero(data.get("open")),
        high=_or_zero(data.get("high")),
        low=_or_zero(data.get("low")),
        prev_close=round(prev_close_f, 2),
        volume=_or_zero(data.get("volume")),
        w52_high=None,
        w52_low=None,
        market_cap=None,
        pe_ratio=None,
        last_updated=last_updated,
        live=True,
        source=source_typed,
    )


def _or_zero(value: object) -> float:
    if value is None:
        return 0.0
    try:
        return round(float(value), 2)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


# ── Sparkline (historical close series) ──────────────────────────────


_RangeLiteral = Literal["1D", "1W", "1M", "6M", "1Y", "5Y"]

# Map our public range strings → (yfinance period, yfinance interval).
# Intervals chosen so each range comfortably fits inside ~80 points
# (good for a smooth sparkline without overdraw).
_RANGE_MAP: dict[str, tuple[str, str]] = {
    "1D": ("1d", "5m"),
    "1W": ("5d", "30m"),
    "1M": ("1mo", "1d"),
    "6M": ("6mo", "1d"),
    "1Y": ("1y", "1wk"),
    "5Y": ("5y", "1mo"),
}


@router.get(
    "/sparkline/{symbol}",
    response_model=SparklineResponse,
    summary="Get a historical close-price series for charting",
)
def get_sparkline(
    symbol: str,
    range: _RangeLiteral = Query("1Y"),
    exchange: str = Query("NSE", pattern="^(NSE|BSE)$"),
    _user_id: int = Depends(require_user),
) -> SparklineResponse:
    sym = symbol.upper().strip()
    period, interval = _RANGE_MAP[range]

    # Prefer Kite historical when an authenticated session exists and
    # the ticker has mapped this symbol's instrument_token. Falls
    # through to yfinance silently when (a) mock mode, (b) no session,
    # (c) unknown instrument, or (d) Kite errors out.
    if not sym.startswith("^"):
        try:
            from backend.kite.historical import get_kite_historical
            kite_period = {
                "1D": "1d", "1W": "5d", "1M": "1mo",
                "6M": "6mo", "1Y": "1y", "5Y": "5y",
            }[range]
            kite_rows = get_kite_historical(
                sym, period=kite_period, exchange=exchange,
            )
            if kite_rows:
                points = [
                    SparklinePoint(
                        t=datetime.fromisoformat(r["date"].replace(" ", "T"))
                          if isinstance(r["date"], str) else r["date"],
                        v=round(float(r["close"]), 2),
                    )
                    for r in kite_rows
                    if r.get("close") is not None
                ]
                if points:
                    return SparklineResponse(
                        symbol=sym, range=range, interval=interval,
                        points=points,
                    )
        except Exception as e:  # noqa: BLE001 — fall through to yfinance
            pass

    # [C4] route through the shared resolver so index aliases / shorthand
    # map to real yfinance tickers (was: naive ".NS" suffix → dead
    # NIFTY.NS / SENSEX.NS → "no historical data"). Keep explicit BSE
    # for plain non-index symbols.
    from backend.market.yfinance_service import (
        resolve_symbol, INDEX_TICKERS, NAME_TO_TICKER,
    )
    if sym.endswith((".NS", ".BO")) or sym.startswith("^"):
        yf_symbol = sym
    elif (exchange == "BSE"
          and sym.upper() not in INDEX_TICKERS
          and sym.lower() not in NAME_TO_TICKER):
        yf_symbol = f"{sym}.BO"
    else:
        yf_symbol = resolve_symbol(sym)

    try:
        hist = yf.Ticker(yf_symbol).history(period=period, interval=interval)
    except Exception as e:
        raise http_error(
            503, "not_yet_available",
            f"yfinance lookup failed for {sym}: {str(e)[:160]}",
        )

    if hist.empty:
        raise http_error(
            404, "not_found",
            f"no historical data for {sym}.{exchange} (range={range})",
        )

    points = [
        SparklinePoint(t=ts.to_pydatetime(), v=round(float(close), 2))
        for ts, close in hist["Close"].items()
        if close is not None and not _is_nan(close)
    ]
    return SparklineResponse(
        symbol=sym, range=range, interval=interval, points=points,
    )


@router.get(
    "/ohlc/{symbol}",
    response_model=OhlcResponse,
    summary="Get a historical OHLCV bar series for candlestick charting",
)
def get_ohlc(
    symbol: str,
    range: _RangeLiteral = Query("6M"),
    exchange: str = Query("NSE", pattern="^(NSE|BSE)$"),
    _user_id: int = Depends(require_user),
) -> OhlcResponse:
    """OHLCV bars for a TradingView candlestick chart. Kite-primary
    (real OHLCV when an authenticated session has mapped the symbol),
    yfinance fallback otherwise — mirrors get_sparkline's source order
    so the candlestick chart never goes blank when Kite is unavailable."""
    sym = symbol.upper().strip()
    period, interval = _RANGE_MAP[range]

    # Kite-primary: full OHLCV when a live session knows this instrument.
    if not sym.startswith("^"):
        try:
            from backend.kite.historical import get_kite_historical
            kite_period = {
                "1D": "1d", "1W": "5d", "1M": "1mo",
                "6M": "6mo", "1Y": "1y", "5Y": "5y",
            }[range]
            kite_rows = get_kite_historical(
                sym, period=kite_period, exchange=exchange,
            )
            if kite_rows:
                bars = [
                    OhlcBar(
                        t=datetime.fromisoformat(r["date"].replace(" ", "T"))
                          if isinstance(r["date"], str) else r["date"],
                        o=float(r["open"]), h=float(r["high"]),
                        l=float(r["low"]), c=float(r["close"]),
                        v=int(r.get("volume", 0) or 0),
                    )
                    for r in kite_rows
                    if r.get("close") is not None and r.get("open") is not None
                ]
                if bars:
                    return OhlcResponse(
                        symbol=sym, range=range, interval=interval,
                        source="kite", bars=bars,
                    )
        except Exception:  # noqa: BLE001 — fall through to yfinance
            pass

    # yfinance fallback — same symbol resolution as the sparkline path.
    from backend.market.yfinance_service import (
        resolve_symbol, INDEX_TICKERS, NAME_TO_TICKER,
    )
    if sym.endswith((".NS", ".BO")) or sym.startswith("^"):
        yf_symbol = sym
    elif (exchange == "BSE"
          and sym.upper() not in INDEX_TICKERS
          and sym.lower() not in NAME_TO_TICKER):
        yf_symbol = f"{sym}.BO"
    else:
        yf_symbol = resolve_symbol(sym)

    try:
        hist = yf.Ticker(yf_symbol).history(period=period, interval=interval)
    except Exception as e:  # noqa: BLE001
        raise http_error(
            503, "not_yet_available",
            f"yfinance lookup failed for {sym}: {str(e)[:160]}",
        )
    if hist.empty:
        raise http_error(
            404, "not_found",
            f"no historical data for {sym}.{exchange} (range={range})",
        )

    bars = []
    for ts, row in hist.iterrows():
        o = _safe_float(row.get("Open")); h = _safe_float(row.get("High"))
        lo = _safe_float(row.get("Low")); c = _safe_float(row.get("Close"))
        if None in (o, h, lo, c):
            continue
        vol = _safe_float(row.get("Volume")) or 0.0
        bars.append(OhlcBar(
            t=ts.to_pydatetime(), o=round(o, 2), h=round(h, 2),
            l=round(lo, 2), c=round(c, 2), v=int(vol),
        ))
    if not bars:
        raise http_error(
            404, "not_found",
            f"no usable OHLC bars for {sym}.{exchange} (range={range})",
        )
    return OhlcResponse(
        symbol=sym, range=range, interval=interval,
        source="yfinance", bars=bars,
    )


# ── Helpers ──────────────────────────────────────────────────────────


def _safe_float(v: object) -> float | None:
    """yfinance returns None / NaN / 'Infinity' for missing values."""
    if v is None:
        return None
    try:
        f = float(v)
        if _is_nan(f) or f == float("inf") or f == float("-inf"):
            return None
        return f
    except (TypeError, ValueError):
        return None


def _is_nan(v: float) -> bool:
    return v != v  # NaN is the only float that isn't equal to itself


# Suppress unused-import warning for `Session` — required by FastAPI's DI
# even when the endpoint doesn't read the DB, so future-me doesn't strip it.
_ = Session, get_db
