"""Market-data REST endpoints — back the redesigned dashboard + stock-snapshot
surfaces in pivot-next/.

Three endpoints:
  - GET /api/markets/indices               → 4 NSE/BSE benchmarks for the dashboard cards
  - GET /api/markets/quote/{symbol}        → full snapshot (OHLC, 52w, mcap, P/E)
  - GET /api/markets/sparkline/{symbol}    → historical close series for the price chart

All three prefer Kite (when a session + instrument mapping exist) and fall
back to yfinance otherwise. Indices are Kite-primary too: they are identified
by their Kite instrument name (see `_INDICES`) and normalised onto it, so the
Kite tier is reachable; a legacy `^`-prefixed symbol still resolves, and only
the yfinance FALLBACK uses the `^` ticker. yfinance is keyless and works for
NSE symbols via the
`.NS` suffix. Every yfinance fallback path is Redis-cached (short TTLs — see
`_INDEX_LEVEL_TTL_S`, `_QUOTE_CACHE_TTL_SECONDS`, `_YF_SERIES_TTL_S`) so a
burst of repeat requests doesn't pay a live `.history()`/`.info` round-trip
each time.

When yfinance returns nothing (network blip, unknown symbol, rate-limit), the
endpoints raise the canonical `not_yet_available` error envelope so the
frontend can surface the message verbatim — never fake data
(ARCHITECTURE.md §5.2 footnote).
"""
from __future__ import annotations

import json
import logging
import math
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
_INDEX_LEVEL_PREFIX = "index:level:v2:"


# ── Response models ──────────────────────────────────────────────────


class IndexQuote(BaseModel):
    name: str               # "NIFTY 50"
    symbol: str             # "NIFTY 50" — the Kite instrument name (see _INDICES)
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
    # Company logo URL (img.logo.dev), or null when none is known — the FE
    # falls back to a first-letter monogram. See backend.market.company_logos.
    logo_url: str | None = None
    # Phase 2: surface whether the quote came from the Kite live feed
    # (WS) or the REST/yfinance fallback. UIs can grey-out delayed
    # quotes; old callers ignore the fields entirely (defaults preserve
    # existing behaviour).
    live: bool = False
    source: Literal["kite_ws", "kite_rest", "yfinance"] = "yfinance"
    # True for a benchmark index (NIFTY 50, SENSEX, …). Indices are not
    # tradeable instruments — they have no cash-equity order path — so the FE
    # renders price + chart only and suppresses every order affordance.
    # Defaults false, so every existing equity caller is unaffected.
    is_index: bool = False


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


class MetricPoint(BaseModel):
    t: datetime    # timestamp
    v: float       # metric value (PE multiple, market cap in ₹ Cr, revenue in ₹ Cr, …)
    margin: float | None = None  # optional secondary series (used by sales_margin for net-profit-margin %)


class MetricSeriesResponse(BaseModel):
    symbol: str
    metric: str            # "pe" | "market_cap" | "sales_margin"
    range: str
    available: bool        # false → caller shows an honest empty state
    points: list[MetricPoint]
    source: str            # "moneycontrol" | "yfinance" | "none"


# ── Indices endpoint (dashboard) ─────────────────────────────────────


# Canonical index registry, keyed by the PUBLIC symbol.
#
# Kite is PRIMARY for indices — `kite.quote()` serves the index instruments
# directly, so the strip works on a cloud IP where yfinance is throttled — and
# the public symbol is therefore the KITE instrument name. The yfinance ticker
# is an implementation detail of the fallback path only.
#
# It used to be the public identity, and that leaked: a Kite-sourced quote was
# still labelled "^NSEI", the FE built /stock/^NSEI from it, and the caret
# percent-encoded into a URL the stock page then failed to decode
# ("no quote available for %5ENSEI.NSE"). Keeping the Yahoo ticker private
# fixes the label and the URL at once.
#
# `exchange` matters: SENSEX is a BSE instrument, so a request defaulting to
# NSE would miss Kite and silently fall back to yfinance. The quote/sparkline/
# ohlc handlers read it from here so the Kite tier is actually reachable.
#
# NOTE: Kite's "NIFTY MIDCAP 100" is the true Midcap-100; the yfinance fallback
# ticker ^NSEMDCP50 is only Midcap-50 (a legacy approximation).
_INDICES: dict[str, dict[str, str]] = {
    "NIFTY 50":         {"display": "NIFTY 50",         "exchange": "NSE", "yf": "^NSEI"},
    "SENSEX":           {"display": "SENSEX",           "exchange": "BSE", "yf": "^BSESN"},
    "NIFTY BANK":       {"display": "BANK NIFTY",       "exchange": "NSE", "yf": "^NSEBANK"},
    "NIFTY MIDCAP 100": {"display": "NIFTY MIDCAP 100", "exchange": "NSE", "yf": "^NSEMDCP50"},
}


def index_meta(symbol: str) -> dict[str, str] | None:
    """Registry entry for `symbol`, or None when it isn't a tracked index.

    Accepts the public (Kite) name and the legacy yfinance `^` ticker, so old
    links and cached payloads keep resolving.
    """
    sym = (symbol or "").upper().strip()
    if sym in _INDICES:
        return _INDICES[sym]
    for meta in _INDICES.values():
        if meta["yf"].upper() == sym:
            return meta
    return None


def is_index_symbol(symbol: str) -> bool:
    """True for ANY index, not just the four dashboard benchmarks.

    `_INDICES` only covers the benchmarks we can normalise onto a Kite
    instrument. But an index reaches the stock page under many spellings
    ("NIFTY", "BANKNIFTY", "FINNIFTY", "INDIAVIX", "^CNXIT"), and every one of
    them is untradeable as cash equity. Gating the FE's order path on the
    narrow registry would leave those aliases showing Buy/Sell, so the flag
    consults the broad alias table too.
    """
    sym = (symbol or "").upper().strip()
    if index_meta(sym) is not None:
        return True
    from backend.market.yfinance_service import INDEX_TICKERS
    return sym in INDEX_TICKERS or sym in {
        t.upper() for t in INDEX_TICKERS.values()
    }


def _cache_index_quote(cache_key: str, quote: IndexQuote) -> None:
    """Best-effort Redis write of an index quote (shared by the Kite + yf paths)."""
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
    except Exception as e:  # noqa: BLE001
        logger.debug("[markets] cache write failed for %s: %s", cache_key, e)


def _fetch_index(public_symbol: str) -> IndexQuote | None:
    """Kite-primary index quote with a 10s Redis cache, yfinance fallback.

    `public_symbol` is the Kite instrument name and the identity we emit; the
    yfinance ticker is read from the registry for the fallback path only.

    Returns None on any failure so the caller can omit the failed index without
    500'ing the whole list.

    WHY caching here: the chat path (`get_index_level`) and the dashboard both
    ask for these four indices repeatedly within seconds. The Redis hit
    collapses the burst so we don't pay a round-trip per query.
    """
    meta = _INDICES[public_symbol]
    name = meta["display"]
    ticker_symbol = meta["yf"]
    kite_key = f"{meta['exchange']}:{public_symbol}"
    cache_key = f"{_INDEX_LEVEL_PREFIX}{public_symbol}"
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

    # ── Primary: Kite REST index quote (last_price + previous close). ──
    if kite_key:
        try:
            from backend.kite.live_quote import get_kite_quotes
            kq = get_kite_quotes([kite_key]).get(kite_key)
            if kq and kq.get("last_price"):
                value = float(kq["last_price"])
                prev_close = kq.get("prev_close") or value
                change = value - prev_close
                change_pct = (change / prev_close * 100) if prev_close > 0 else 0.0
                quote = IndexQuote(
                    name=name,
                    symbol=public_symbol,
                    value=round(value, 2),
                    change=round(change, 2),
                    change_pct=round(change_pct, 2),
                    last_updated=datetime.now(timezone.utc),
                )
                _cache_index_quote(cache_key, quote)
                return quote
        except Exception as e:  # noqa: BLE001 — fall through to yfinance
            logger.info("[markets] kite index fetch failed for %s: %s", name, str(e)[:160])

    # ── Fallback: yfinance (bounded so it can't hang on a cloud IP). ──
    try:
        ticker = yf.Ticker(ticker_symbol)
        hist = ticker.history(period="2d", interval="1d", timeout=6)
        if hist.empty or len(hist) < 1:
            return None
        latest_close = float(hist["Close"].iloc[-1])
        prev_close = float(hist["Close"].iloc[-2]) if len(hist) >= 2 else latest_close
        change = latest_close - prev_close
        change_pct = (change / prev_close * 100) if prev_close > 0 else 0.0
        quote = IndexQuote(
            name=name,
            symbol=public_symbol,
            value=round(latest_close, 2),
            change=round(change, 2),
            change_pct=round(change_pct, 2),
            last_updated=datetime.now(timezone.utc),
        )
        _cache_index_quote(cache_key, quote)
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
    for public_symbol in _INDICES:
        q = _fetch_index(public_symbol)
        if q is not None:
            items.append(q)
    if not items:
        # Both Kite and yfinance unreachable → surface to FE via canonical
        # envelope rather than returning an empty list (which the dashboard
        # would render as a blank strip).
        raise http_error(
            status_code=503,
            code="not_yet_available",
            message="market indices unavailable — data source is unreachable",
        )
    return IndicesResponse(items=items)


# ── Stock snapshot ───────────────────────────────────────────────────


_DB_META_TTL_S = 43_200  # 12h — name/sector/mcap/PE move slowly


def _db_meta(symbol: str) -> dict:
    """Name / sector / industry / market-cap from the enrich DB, plus P/E from
    the Financials (Moneycontrol) DB. All best-effort — a missing field is
    ``None`` (never fabricated), and neither call touches the network, so this
    never hangs on a cloud IP the way yfinance's ``.info`` does. Redis-cached
    12h so the hot quote path doesn't pay two remote-DB queries per request."""
    key = f"qmeta:{symbol.upper()}"
    try:
        raw = redis_client.get(key)
        if raw is not None:
            return json.loads(raw if isinstance(raw, str) else raw.decode())
    except Exception:  # noqa: BLE001
        pass

    out: dict[str, object] = {
        "name": None, "sector": None, "industry": None,
        "market_cap": None, "pe_ratio": None,
    }
    try:
        from backend.market import enrich_db
        e = enrich_db.get_by_ticker(symbol)
        if e is not None:
            out["name"] = e.long_name or e.company_name
            out["sector"] = e.sector
            out["industry"] = e.industry
            out["market_cap"] = _safe_float(e.market_cap)
    except Exception:  # noqa: BLE001 — DB best-effort
        pass
    try:
        from backend.services.fundamentals_screen import fetch_gate_inputs
        g = fetch_gate_inputs([symbol]).get(symbol.upper())
        if g and g.get("pe") is not None:
            out["pe_ratio"] = _safe_float(g["pe"])
    except Exception:  # noqa: BLE001
        pass

    # Only cache once we actually resolved a name — avoids pinning an all-null
    # miss (e.g. a transient DB blip) for 12h.
    if out["name"]:
        try:
            redis_client.setex(key, _DB_META_TTL_S, json.dumps(out))
        except Exception:  # noqa: BLE001
            pass
    return out


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

    # Indices: normalise a legacy "^NSEI"-style link onto the Kite instrument
    # name and pin the instrument's own exchange, so the Kite tier below is
    # actually reachable (SENSEX is BSE; defaulting to NSE would miss it and
    # fall through to yfinance).
    _idx = index_meta(sym)
    if _idx is not None:
        sym = next(k for k, v in _INDICES.items() if v is _idx)
        exchange = _idx["exchange"]
    _is_index = is_index_symbol(sym)

    # ─ Phase 2: prefer the Kite tick cache if the entry is fresh
    # (within 5 s). When the ticker isn't running or this symbol isn't
    # in its universe, fall through to the existing yfinance path.
    cached = _read_cached_kite_tick(sym)
    # A tick with no prev_close yields change/open/high/low = 0.0 — the WS feed
    # carries only last_price for index instruments. Returning that renders as
    # "Open ₹0.00 · Prev Close ₹0.00 · +0.00%", which reads as real data rather
    # than missing data. Fall through to the REST tier, which fetches genuine
    # OHLC, instead of publishing zeros.
    if cached is not None and cached.prev_close > 0:
        # The tick cache builds its own StockQuote and knows nothing about the
        # index registry, so stamp the flag here — otherwise a live-ticking
        # index (NIFTY 50, BANK NIFTY) returns is_index=False and the FE shows
        # it an order path.
        return cached.model_copy(update={"is_index": _is_index})

    # Kite tick miss → the yfinance path below makes a slow `.info` call (~1-2s).
    # Cache the resulting (already delayed, live=False) snapshot briefly so repeat
    # loads and duplicate concurrent fetches don't each pay it. Live Kite ticks
    # bypass this entirely (handled above), so real-time freshness is unaffected.
    _q_key = f"quote:yf:v1:{exchange}:{sym}"
    try:
        _raw = redis_client.get(_q_key)
        if _raw:
            # Entries cached before is_index existed decode to the False
            # default; re-stamp rather than serve a stale flag.
            return StockQuote.model_validate_json(_raw).model_copy(
                update={"is_index": _is_index}
            )
    except Exception:  # noqa: BLE001 — cache is best-effort, never fatal
        pass

    # ── Kite REST live-quote tier — the production path. Live price + OHLC come
    #    from Kite (works on cloud IPs where yfinance's `.info` hangs); the
    #    name / sector / P-E / market-cap come from the enrich + financials DBs;
    #    the 52-week range is computed from Kite's own 1-year bars. yfinance
    #    below is only reached when there is no live Kite session at all.
    from backend.kite.live_quote import get_kite_quote
    kq = get_kite_quote(sym, exchange)
    if kq and kq.get("last_price"):
        meta = _db_meta(sym)
        w52_high, w52_low = _cached_52w(sym, exchange)
        ltp = float(kq["last_price"])
        prev_close = kq.get("prev_close") or kq.get("open") or ltp
        change = ltp - prev_close
        change_pct = (change / prev_close * 100) if prev_close > 0 else 0.0
        quote = StockQuote(
            symbol=sym,
            name=str(meta["name"] or sym),
            exchange=exchange,
            sector=meta["sector"],  # type: ignore[arg-type]
            industry=meta["industry"],  # type: ignore[arg-type]
            ltp=round(ltp, 2),
            change=round(change, 2),
            change_pct=round(change_pct, 2),
            open=round(float(kq.get("open") or ltp), 2),
            high=round(float(kq.get("high") or ltp), 2),
            low=round(float(kq.get("low") or ltp), 2),
            prev_close=round(float(prev_close), 2),
            volume=float(kq.get("volume") or 0),
            w52_high=w52_high,
            w52_low=w52_low,
            market_cap=meta["market_cap"],  # type: ignore[arg-type]
            pe_ratio=meta["pe_ratio"],  # type: ignore[arg-type]
            last_updated=datetime.now(timezone.utc),
            logo_url=_lookup_logo(sym),
            live=True,
            source="kite_rest",
            is_index=_is_index,
        )
        try:
            redis_client.setex(_q_key, _QUOTE_CACHE_TTL_SECONDS, quote.model_dump_json())
        except Exception:  # noqa: BLE001 — cache write is best-effort
            pass
        return quote

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

    # Last-resort yfinance path (no live Kite session). Both calls are bounded
    # so they fail fast on a datacenter IP instead of hanging into a gateway
    # timeout / "Failed to fetch": `.info` has no timeout arg → run it under a
    # hard wall-clock via call_bounded; `.history` takes a native `timeout=`.
    from backend.market.net_timeout import call_bounded
    try:
        ticker = yf.Ticker(yf_symbol)
        info = call_bounded(lambda: ticker.info, timeout=6, default={},
                            label=f"yf.info {sym}") or {}
        hist = ticker.history(period="5d", interval="1d", timeout=6)
    except Exception as e:
        raise http_error(
            503, "not_yet_available",
            f"yfinance lookup failed for {sym}: {str(e)[:160]}",
        )

    # Yahoo's most-recent daily bar is sometimes Volume-only (OHLC all NaN)
    # before today's candle settles — building a quote from that produced an
    # unhandled 500 (Starlette's JSON encoder rejects NaN) for EVERY symbol,
    # since Kite has no live session on Azure and every quote falls through
    # to this path. Drop unsettled rows so we always use the last real close.
    if not hist.empty:
        hist = hist.dropna(subset=["Close"])

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

    quote = StockQuote(
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
        logo_url=_lookup_logo(sym),
        live=False,
        source="yfinance",
        is_index=_is_index,
    )
    try:
        redis_client.setex(_q_key, _QUOTE_CACHE_TTL_SECONDS, quote.model_dump_json())
    except Exception:  # noqa: BLE001 — cache write is best-effort
        pass
    return quote


# Max age of a Redis-cached tick we'll treat as "live". The ticker
# pushes ~1 msg/sec/symbol on a busy stock, so 5s is generous; the
# scheduler/quoting fallback handles older entries.
_KITE_TICK_FRESH_SECONDS = 5

# TTL for the cached yfinance (delayed) quote snapshot — short enough that the
# detail page stays fresh, long enough to absorb repeat loads + duplicate fetches.
_QUOTE_CACHE_TTL_SECONDS = 20


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
    # The Kite tick feed doesn't carry a 52-week range, so the live path used
    # to drop it (→ empty 52w bar on the stock page). Backfill from a 12h
    # cache computed off yfinance.
    w52_high, w52_low = _cached_52w(symbol)
    # Company name / sector / market-cap / P-E from the DB (cached) — the tick
    # feed carries none of these, so without this the live path showed a bare
    # ticker and empty fundamentals on the stock-page header.
    meta = _db_meta(symbol)
    last_updated = datetime.fromtimestamp(int(ts), tz=timezone.utc)
    return StockQuote(
        symbol=symbol,
        name=str(meta["name"] or symbol),
        exchange="NSE",
        sector=meta["sector"],  # type: ignore[arg-type]
        industry=meta["industry"],  # type: ignore[arg-type]
        ltp=round(float(ltp), 2),
        change=round(change, 2),
        change_pct=round(change_pct_f, 2),
        open=_or_zero(data.get("open")),
        high=_or_zero(data.get("high")),
        low=_or_zero(data.get("low")),
        prev_close=round(prev_close_f, 2),
        volume=_or_zero(data.get("volume")),
        w52_high=w52_high,
        w52_low=w52_low,
        market_cap=meta["market_cap"],  # type: ignore[arg-type]
        pe_ratio=meta["pe_ratio"],  # type: ignore[arg-type]
        last_updated=last_updated,
        logo_url=_lookup_logo(symbol),
        live=True,
        source=source_typed,
    )


def _lookup_logo(symbol: str) -> str | None:
    """Resolve a company logo URL (Redis-cached, fail-safe None). Kept
    thin so the hot quote path never raises on a logo miss."""
    try:
        from backend.market.company_logos import get_logo_url

        return get_logo_url(symbol)
    except Exception:  # noqa: BLE001
        return None


def _or_zero(value: object) -> float:
    if value is None:
        return 0.0
    try:
        return round(float(value), 2)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


# ── Sparkline (historical close series) ──────────────────────────────


_RangeLiteral = Literal["1D", "1W", "1M", "3M", "6M", "1Y", "5Y"]

# Map our public range strings → (yfinance period, yfinance interval).
# Intervals chosen so each range comfortably fits inside ~80 points
# (good for a smooth sparkline without overdraw).
_RANGE_MAP: dict[str, tuple[str, str]] = {
    "1D": ("1d", "5m"),
    "1W": ("5d", "30m"),
    "1M": ("1mo", "1d"),
    "3M": ("3mo", "1d"),
    "6M": ("6mo", "1d"),
    "1Y": ("1y", "1wk"),
    "5Y": ("5y", "1mo"),
}

# Redis TTL for the yfinance-fallback series cache (sparkline + OHLC),
# keyed by range. This fallback fires for any symbol with no active Kite
# session and ALWAYS for indices (which skip Kite entirely — see the
# `sym.startswith("^")` guards below), so an uncached hit was taking
# 3.3-3.7s per request. Short TTLs for the intraday ranges (5m/30m bars
# that move during market hours); longer TTLs for the daily/weekly/monthly
# bar ranges, which don't change intraday. Mirrors the Kite-primary cache
# style in `backend/kite/historical.py` (get_kite_historical's 30-min TTL).
_YF_SERIES_TTL_S: dict[str, int] = {
    "1D": 60,     # 5m bars — refresh roughly as often as a new bar forms
    "1W": 120,    # 30m bars
    "1M": 900,    # daily bars — 15 min is plenty
    "6M": 1800,   # daily bars
    "1Y": 1800,   # weekly bars
    "5Y": 1800,   # monthly bars
}
_SPARKLINE_YF_CACHE_PREFIX = "sparkline:yf:v1:"
_OHLC_YF_CACHE_PREFIX = "ohlc:yf:v1:"

# Kite historical only serves up-to-daily candles (no native week/month
# interval), so a 5Y request comes back as ~1,250 daily points — far too
# dense for an ~800px sparkline, which overdraws into a fuzzy, jagged mess.
# The yfinance path already downsamples (1Y→weekly, 5Y→monthly, ~80 pts);
# mirror that on the Kite path so both sources render at the same clean
# density. We keep every Nth real close (never interpolate/fabricate) and
# always preserve the first and last point so the endpoints stay exact.
_SPARKLINE_MAX_POINTS = 80


def _downsample_points(points: list[SparklinePoint]) -> list[SparklinePoint]:
    n = len(points)
    if n <= _SPARKLINE_MAX_POINTS:
        return points
    stride = math.ceil(n / _SPARKLINE_MAX_POINTS)
    kept = points[::stride]
    if kept[-1] is not points[-1]:
        kept.append(points[-1])
    return kept


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
    # Indices: normalise legacy "^"-style links onto the Kite instrument name
    # and pin the instrument's exchange, so the Kite tier below is reachable
    # instead of being skipped straight to yfinance.
    _idx = index_meta(sym)
    if _idx is not None:
        sym = next(k for k, v in _INDICES.items() if v is _idx)
        exchange = _idx["exchange"]
    period, interval = _RANGE_MAP[range]

    # Prefer Kite historical when an authenticated session exists and
    # the ticker has mapped this symbol's instrument_token. Falls
    # through to yfinance silently when (a) mock mode, (b) no session,
    # (c) unknown instrument, or (d) Kite errors out.
    if not sym.startswith("^"):
        try:
            from backend.kite.historical import get_kite_historical
            kite_period = {
                "1D": "1d", "1W": "5d", "1M": "1mo", "3M": "3mo",
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
                        points=_downsample_points(points),
                    )
        except Exception:  # noqa: BLE001 — fall through to yfinance
            pass

    # yfinance fallback (no Kite session, unmapped instrument, or an
    # index — indices skip Kite entirely). This path is otherwise an
    # uncached live `.history()` call (~3.3-3.7s cold); cache the
    # assembled response so repeat requests for the same symbol/range
    # are near-instant. Keyed on symbol+exchange+range+interval so
    # different ranges never collide.
    _sp_key = f"{_SPARKLINE_YF_CACHE_PREFIX}{exchange}:{sym}:{range}:{interval}"
    try:
        _sp_raw = redis_client.get(_sp_key)
        if _sp_raw:
            return SparklineResponse.model_validate_json(_sp_raw)
    except Exception as e:  # noqa: BLE001 — cache is best-effort, never fatal
        logger.debug("[markets] sparkline cache read failed for %s: %s", _sp_key, e)

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
    response = SparklineResponse(
        symbol=sym, range=range, interval=interval, points=points,
    )
    try:
        redis_client.setex(
            _sp_key, _YF_SERIES_TTL_S.get(range, 300), response.model_dump_json(),
        )
    except Exception as e:  # noqa: BLE001 — cache write is best-effort
        logger.debug("[markets] sparkline cache write failed for %s: %s", _sp_key, e)
    return response


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
    # Indices: normalise legacy "^"-style links onto the Kite instrument name
    # and pin the instrument's exchange, so the Kite tier below is reachable
    # instead of being skipped straight to yfinance.
    _idx = index_meta(sym)
    if _idx is not None:
        sym = next(k for k, v in _INDICES.items() if v is _idx)
        exchange = _idx["exchange"]
    period, interval = _RANGE_MAP[range]

    # Kite-primary: full OHLCV when a live session knows this instrument.
    if not sym.startswith("^"):
        try:
            from backend.kite.historical import get_kite_historical
            kite_period = {
                "1D": "1d", "1W": "5d", "1M": "1mo", "3M": "3mo",
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
    # Also uncached upstream (~3.3-3.7s cold); cache the assembled
    # response so repeat requests for the same symbol/range are fast.
    _oh_key = f"{_OHLC_YF_CACHE_PREFIX}{exchange}:{sym}:{range}:{interval}"
    try:
        _oh_raw = redis_client.get(_oh_key)
        if _oh_raw:
            return OhlcResponse.model_validate_json(_oh_raw)
    except Exception as e:  # noqa: BLE001 — cache is best-effort, never fatal
        logger.debug("[markets] ohlc cache read failed for %s: %s", _oh_key, e)

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
    response = OhlcResponse(
        symbol=sym, range=range, interval=interval,
        source="yfinance", bars=bars,
    )
    try:
        redis_client.setex(
            _oh_key, _YF_SERIES_TTL_S.get(range, 300), response.model_dump_json(),
        )
    except Exception as e:  # noqa: BLE001 — cache write is best-effort
        logger.debug("[markets] ohlc cache write failed for %s: %s", _oh_key, e)
    return response


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


# ── 52-week range backfill (for the live Kite-tick path) ─────────────


_52W_TTL_S = 43_200  # 12h — the range barely moves intraday


def _cached_52w(
    symbol: str, exchange: str = "NSE",
) -> tuple[float | None, float | None]:
    """Return (high, low) 52-week range, cached in Redis for 12h.

    Computed from KITE 1-year daily bars (max high / min low) — the same Kite
    historical tier the price chart uses, so it works in production where a
    yfinance `.info` call hangs on a datacenter IP. yfinance is a last-resort
    fallback only. Returns (None, None) on any failure — the caller renders "—".
    """
    key = f"q52:{symbol.upper()}"
    try:
        raw = redis_client.get(key)
        if raw is not None:
            if isinstance(raw, (bytes, bytearray)):
                raw = raw.decode()
            d = json.loads(raw)
            return d.get("high"), d.get("low")
    except Exception:
        pass

    hi: float | None = None
    lo: float | None = None

    # Primary: compute the range from Kite's 1-year daily bars.
    try:
        from backend.kite.historical import get_kite_historical
        rows = get_kite_historical(symbol, period="1y", exchange=exchange)
        if rows:
            highs = [float(r["high"]) for r in rows if r.get("high") is not None]
            lows = [float(r["low"]) for r in rows if r.get("low") is not None]
            if highs and lows:
                hi = round(max(highs), 2)
                lo = round(min(lows), 2)
    except Exception:  # noqa: BLE001 — fall through to yfinance
        pass

    # Last-resort fallback: yfinance (only reached when Kite has no session /
    # can't resolve the instrument). `timeout=` bounds the requests round-trip
    # so it fails fast on a datacenter IP instead of hanging the request.
    if hi is None or lo is None:
        try:
            from backend.market.yfinance_service import resolve_symbol
            h = yf.Ticker(resolve_symbol(symbol)).history(period="1y", timeout=6)
            if not h.empty:
                hi = round(float(h["High"].max()), 2)
                lo = round(float(h["Low"].min()), 2)
        except Exception:
            pass

    if hi is not None and lo is not None:
        try:
            redis_client.setex(key, _52W_TTL_S, json.dumps({"high": hi, "low": lo}))
        except Exception:
            pass
    return hi, lo


# ── Metric series (PE / Market Cap / Sales & Margin over time, for chart toggle) ────


from datetime import date as _date  # noqa: E402


def _to_date(v: object) -> _date | None:
    if isinstance(v, _date) and not isinstance(v, datetime):
        return v
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, str):
        try:
            return datetime.fromisoformat(v[:10]).date()
        except ValueError:
            return None
    return None


def _value_asof(steps: list[tuple[_date, float]], d: _date) -> float | None:
    """`steps` sorted ascending by date. Return the value effective on/just
    before `d`; for dates before the first step, flat-extrapolate the earliest
    value backward so the chart stays continuous."""
    val: float | None = None
    for sd, v in steps:
        if sd <= d:
            val = v
        else:
            break
    if val is None and steps:
        val = steps[0][1]
    return val


def _eps_steps(sym: str) -> tuple[list[tuple[_date, float]], str]:
    """Annual basic-EPS steps (ascending), MC-primary then yfinance."""
    from backend.market import financials_db as fdb
    try:
        rows = fdb.get_fundamental_history(sym, "eps_basic", limit=12)
    except Exception:
        rows = []
    steps: list[tuple[_date, float]] = []
    for r in rows:
        if r.value_numeric is None:
            continue
        d = _to_date(r.availability_date) or _to_date(r.period_end)
        if d is not None:
            steps.append((d, float(r.value_numeric)))
    if steps:
        steps.sort(key=lambda s: s[0])
        return steps, "moneycontrol"

    # yfinance fallback
    from backend.market import yfinance_fundamentals as yff
    f = yff.fetch_fundamentals(sym)
    for p in (f.get("history", {}).get("eps_basic") or []):
        d = _to_date(p.get("period_end"))
        v = p.get("value")
        if d is not None and v is not None:
            steps.append((d, float(v)))
    steps.sort(key=lambda s: s[0])
    return steps, ("yfinance" if steps else "none")


def _pe_series(sym: str, prices: list[tuple[datetime, float]]) -> tuple[list[MetricPoint], str]:
    steps, source = _eps_steps(sym)
    if not steps:
        return [], "none"
    pts: list[MetricPoint] = []
    for ts, close in prices:
        d = ts.date() if isinstance(ts, datetime) else _to_date(ts)
        if d is None:
            continue
        eps = _value_asof(steps, d)
        if eps and eps > 0:
            pts.append(MetricPoint(t=ts, v=round(close / eps, 2)))
    return pts, (source if pts else "none")


def _market_cap_series(sym: str, prices: list[tuple[datetime, float]]) -> tuple[list[MetricPoint], str]:
    """Market cap over time (₹ Cr) = close_price × shares_outstanding / 1e7.

    Shares outstanding is a slow-moving figure sourced from yfinance's per-ticker
    fundamentals — we treat it as constant across the chart's window (good enough
    for a visual trend; buy-backs / splits are rare enough that the small residual
    error is preferable to fabricating a share-count history yfinance doesn't ship).
    """
    from backend.market import yfinance_fundamentals as yff
    f = yff.fetch_fundamentals(sym)
    shares = f.get("shares")
    if not shares:
        return [], "none"
    pts: list[MetricPoint] = [
        MetricPoint(t=ts, v=round(close * shares / 1e7, 2))
        for ts, close in prices
    ]
    return pts, ("yfinance" if pts else "none")


def _revenue_steps(sym: str) -> tuple[list[tuple[_date, float]], str]:
    """Annual revenue (₹ Cr) steps ascending, MC-primary then yfinance."""
    from backend.market import financials_db as fdb
    try:
        rows = fdb.get_fundamental_history(sym, "revenue", limit=12)
    except Exception:
        rows = []
    steps: list[tuple[_date, float]] = []
    for r in rows:
        if r.value_numeric is None:
            continue
        d = _to_date(r.availability_date) or _to_date(r.period_end)
        if d is not None:
            steps.append((d, float(r.value_numeric)))
    if steps:
        steps.sort(key=lambda s: s[0])
        return steps, "moneycontrol"

    # yfinance fallback — values in ₹ Cr per yfinance_fundamentals._CR_FIELDS.
    from backend.market import yfinance_fundamentals as yff
    f = yff.fetch_fundamentals(sym)
    for p in (f.get("history", {}).get("revenue") or []):
        d = _to_date(p.get("period_end"))
        v = p.get("value")
        if d is not None and v is not None:
            steps.append((d, float(v)))
    steps.sort(key=lambda s: s[0])
    return steps, ("yfinance" if steps else "none")


def _margin_steps(sym: str) -> list[tuple[_date, float]]:
    """Net-profit-margin (%) steps ascending, MC-primary then yfinance;
    falls back to ebitda_margin when net-profit-margin has no rows."""
    from backend.market import financials_db as fdb
    steps: list[tuple[_date, float]] = []
    for metric_key in ("net_profit_margin", "ebitda_margin"):
        try:
            rows = fdb.get_fundamental_history(sym, metric_key, limit=12)
        except Exception:
            rows = []
        for r in rows:
            if r.value_numeric is None:
                continue
            d = _to_date(r.availability_date) or _to_date(r.period_end)
            if d is not None:
                steps.append((d, float(r.value_numeric)))
        if steps:
            steps.sort(key=lambda s: s[0])
            return steps

    # yfinance fallback
    from backend.market import yfinance_fundamentals as yff
    f = yff.fetch_fundamentals(sym)
    for key in ("net_profit_margin", "ebitda_margin"):
        for p in (f.get("history", {}).get(key) or []):
            d = _to_date(p.get("period_end"))
            v = p.get("value")
            if d is not None and v is not None:
                steps.append((d, float(v)))
        if steps:
            steps.sort(key=lambda s: s[0])
            return steps
    return steps


def _sales_margin_series(sym: str, prices: list[tuple[datetime, float]]) -> tuple[list[MetricPoint], str]:
    """Revenue (₹ Cr, primary series) + net-profit-margin (%, secondary) as-of
    each price timestamp. Margin may legitimately be None on a given point —
    we leave it None rather than fabricate."""
    revenue_steps, source = _revenue_steps(sym)
    if not revenue_steps:
        return [], "none"
    margin_steps = _margin_steps(sym)
    pts: list[MetricPoint] = []
    for ts, _close in prices:
        d = ts.date() if isinstance(ts, datetime) else _to_date(ts)
        if d is None:
            continue
        rev = _value_asof(revenue_steps, d)
        if rev is None:
            continue
        margin = _value_asof(margin_steps, d) if margin_steps else None
        pts.append(MetricPoint(
            t=ts,
            v=round(rev, 2),
            margin=(round(margin, 2) if margin is not None else None),
        ))
    return pts, (source if pts else "none")


@router.get(
    "/metric-series/{symbol}",
    response_model=MetricSeriesResponse,
    summary="Time series of a valuation metric (PE / Market Cap / Sales & Margin) for the chart toggle",
)
def get_metric_series(
    symbol: str,
    metric: Literal["pe", "market_cap", "sales_margin"] = Query("pe"),
    range: _RangeLiteral = Query("5Y"),
    exchange: str = Query("NSE", pattern="^(NSE|BSE)$"),
    _user_id: int = Depends(require_user),
) -> MetricSeriesResponse:
    sym = symbol.upper().strip()
    try:
        spark = get_sparkline(sym, range=range, exchange=exchange, _user_id=_user_id)
        prices = [(p.t, p.v) for p in spark.points]
    except Exception:
        prices = []
    if not prices:
        return MetricSeriesResponse(
            symbol=sym, metric=metric, range=range,
            available=False, points=[], source="none",
        )

    if metric == "pe":
        pts, source = _pe_series(sym, prices)
    elif metric == "market_cap":
        pts, source = _market_cap_series(sym, prices)
    else:
        pts, source = _sales_margin_series(sym, prices)

    return MetricSeriesResponse(
        symbol=sym, metric=metric, range=range,
        available=bool(pts), points=pts, source=source,
    )


# Suppress unused-import warning for `Session` — required by FastAPI's DI
# even when the endpoint doesn't read the DB, so future-me doesn't strip it.
_ = Session, get_db
