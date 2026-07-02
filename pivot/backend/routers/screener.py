"""Screener tab REST surface — a real stock universe with fundamentals + search.

Three endpoints back the FE screener tab:
  - GET /api/screener/stocks   → the filterable/sortable stock grid
  - GET /api/screener/search   → symbol/name autosuggest over the universe
  - GET /api/screener/sectors  → the sector filter rail

Design: we do NOT rebuild any data layer. The universe (symbol, name, sector,
approximate ₹-crore market cap) comes from the curated
`services.sector_universe`; the fundamentals (PE / ROE) come from the
Moneycontrol financials DB via `services.fundamentals_screen.fetch_gate_inputs`
(one batched round-trip for the whole page, not per-symbol); logos come from
`market.company_logos.get_logo_url`; and search reuses
`market.financials_db.search_companies` (the same service the /api/companies
autosuggest uses).

Honest-data contract (CLAUDE.md): a metric the financials DB genuinely can't
serve for a symbol is returned as `null`, never fabricated. Two metrics have no
underlying source on this path and are always null with a documented reason:
  - `div_yield` — the financials DB exposes dividend *payout* %, not dividend
    *yield* (yield needs a live price the screen path doesn't fetch per-row).
  - `one_year_pct` — no historical price series is fetched here (a per-symbol
    yfinance/Kite call across ~130 names would make the grid slow + flaky);
    the FE can hydrate it lazily from /api/markets/sparkline if needed.

Both are exposed in the row shape (so the contract is stable) and in the
response `meta.null_metrics` flag so the FE renders an honest "—" rather than a
guess. Market cap comes from sector_universe (the financials DB market_cap is
100% NULL — see fundamentals_screen).

Auth follows the markets/_deps pattern: `require_user` (dev-mode falls back to
user_id=1). Read-only; nothing here writes.
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from backend.cache import redis_client
from backend.routers._deps import require_user
from backend.services.sector_universe import (
    _UNIVERSE as _SECTOR_UNIVERSE,
    known_sectors,
    normalize_sector,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/screener", tags=["Screener"])

# Full-universe fundamentals (roe/pe) are the screener's whole latency cost:
# ``fetch_gate_inputs`` is a multi-CTE query against Azure Postgres (Central
# India) that measures ~1.3-2.5s for the 80-name universe. The values change at
# most quarterly, so we cache the WHOLE-universe map in Redis and let every
# /stocks request (any sector/cap/valuation filter, any sort) read that one warm
# entry and filter it in memory — turning a per-request 1.3-2.5s DB round-trip
# into a ~single-ms Redis GET after the first caller warms it.
_FUND_CACHE_KEY = "screener:fundamentals:v1"
_FUND_CACHE_TTL_SECONDS = 30 * 60  # 30 min — generous for quarterly data

# The fundamentals recency floor: only accept statements newer than this. The
# strategy-builder gate uses a strict 2-year floor (rejects a name that stopped
# reporting), but for a DISPLAY grid that drops ~84% of the universe — many
# large caps' *earnings-yield* snapshot lags their balance-sheet by a year even
# though the company reports every quarter. A 3-year floor lands on the latest
# valid P/E for 75/80 names (vs 12/80 at 2 years) while still excluding truly
# dead data. Showing the latest available P/E beats a grid of em-dashes.
_FUND_RECENCY_YEARS = 3

# Live market metrics (last price, day change %, 1-year price return) for the
# whole universe, sourced Kite-primary (batch quote) with a yfinance batch as
# the resilient fallback + the 1-year source. Cached in Redis and refreshed on a
# background thread so the /stocks endpoint NEVER blocks on the ~10-15s yfinance
# batch — a cold request returns nulls (FE renders "—") and the values fill in
# on the next poll once the warm completes. Keyed by UPPER(symbol).
_METRICS_CACHE_KEY = "screener:market_metrics:v1"
_METRICS_TTL_SECONDS = 10 * 60  # price freshness vs recompute cost (delayed grid)
_metrics_lock = threading.Lock()
_metrics_refreshing = False


# ── Market-cap tiers ──────────────────────────────────────────────────
# Mirrors the curated tiers used by fundamentals_screen so the screener and the
# fundamental screen agree on what "large"/"mid" cap means (₹ crore).
_MCAP_TIERS: dict[str, tuple[Optional[int], Optional[int]]] = {
    # tier -> (min_cr inclusive, max_cr exclusive)
    "large": (50_000, None),
    "mid": (20_000, 50_000),
    "small": (None, 20_000),
}
_MCAP_TIER_ALIASES: dict[str, str] = {
    "largecap": "large", "large-cap": "large", "large": "large",
    "bluechip": "large", "blue chip": "large", "blue-chip": "large", "big": "large",
    "midcap": "mid", "mid-cap": "mid", "mid": "mid",
    "smallcap": "small", "small-cap": "small", "small": "small",
}

_SORT_FIELDS = {"market_cap_cr", "pe", "roe", "symbol", "name"}


# ── Response models ───────────────────────────────────────────────────


class ScreenerStock(BaseModel):
    symbol: str
    name: str
    sector: str
    market_cap_cr: Optional[int]
    # Live-ish market metrics, sourced Kite-primary / yfinance-fallback and
    # cached (see _market_metrics_cached). Null when no source can serve the
    # symbol yet → the FE renders an honest em-dash.
    price: Optional[float] = None
    change_pct: Optional[float] = None
    pe: Optional[float] = None
    roe: Optional[float] = None
    # 1-year PRICE return (%), computed from the cached market-metrics map.
    one_year_pct: Optional[float] = None
    # div_yield has no source on this path; kept null in the shape for contract
    # stability (the FE no longer renders it).
    div_yield: Optional[float] = None
    logo_url: Optional[str] = None


class ScreenerStocksResponse(BaseModel):
    count: int
    results: list[ScreenerStock]
    # FE hint: which row metrics are not served by this endpoint at all, so the
    # grid renders "—" rather than treating a null as "screened out".
    null_metrics: list[str]
    note: str


class ScreenerSearchResult(BaseModel):
    symbol: str
    name: str
    sector: Optional[str] = None
    logo_url: Optional[str] = None
    has_fundamentals: bool = False


class ScreenerSearchResponse(BaseModel):
    results: list[ScreenerSearchResult]


class ScreenerSector(BaseModel):
    sector: str       # canonical key, e.g. "private_bank"
    label: str        # display label, e.g. "Private Bank"
    count: int        # number of universe names in this sector


class ScreenerSectorsResponse(BaseModel):
    sectors: list[ScreenerSector]


# ── Helpers ───────────────────────────────────────────────────────────


def _logo_map(symbols: list[str]) -> dict[str, Optional[str]]:
    """Batch logo resolution for a page in ≤2 round-trips (one Redis MGET + one
    enrich query for cold misses), instead of ~2 remote DB queries PER row.

    This is the screener cold-start fix: per-row ``get_logo_url`` fired an N+1
    storm (financials ``get_company`` + ``enrich`` lookup per symbol) the first
    time a page was loaded with an empty Redis cache. A failure degrades to no
    logos (FE renders a monogram), never 500s the grid. Keyed by UPPER(symbol).
    """
    if not symbols:
        return {}
    try:
        from backend.market.company_logos import get_logo_urls

        return get_logo_urls(symbols)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[screener] logo batch failed: %s", exc)
        return {}


def _screener_recency_floor() -> date:
    """Latest-statement floor for the screener grid — laxer than the gate's."""
    return date(date.today().year - _FUND_RECENCY_YEARS, 1, 1)


def _fetch_fundamentals_map(symbols: list[str]) -> dict[str, dict]:
    """Batch-fetch roe/pe for the page's symbols in ONE round-trip.

    Wrapped so a financials-DB outage degrades to "no fundamentals" (all-null
    columns) instead of 500'ing the whole universe — the curated universe alone
    is still a useful grid. Uses the screener's laxer recency floor so a name
    whose earnings-yield snapshot lags by a year still shows its latest P/E.
    """
    if not symbols:
        return {}
    try:
        from backend.services.fundamentals_screen import fetch_gate_inputs

        return fetch_gate_inputs(symbols, min_period_end=_screener_recency_floor())
    except Exception as exc:  # noqa: BLE001
        logger.warning("[screener] fundamentals batch fetch failed: %s", exc)
        return {}


def _fundamentals_map_cached() -> dict[str, dict]:
    """Full-universe roe/pe map, Redis-cached (see ``_FUND_CACHE_KEY``).

    Reads the warm cache first (one Redis GET); on a miss, runs the expensive
    ``fetch_gate_inputs`` ONCE for the *entire* universe and warms the cache so
    the next caller — no matter which filter/sort they use — is served from
    Redis. Fails open at every step: a cache/DB error degrades to a direct
    fetch or an empty map (all-null fundamentals), never a 500. The map is keyed
    by UPPER(symbol), so callers pick out just the symbols they need.
    """
    try:
        raw = redis_client.get(_FUND_CACHE_KEY)
        if raw:
            data = raw.decode() if isinstance(raw, (bytes, bytearray)) else raw
            return json.loads(data)
    except Exception as exc:  # noqa: BLE001 — cache read is best-effort
        logger.debug("[screener] fundamentals cache read failed: %s", exc)

    fmap = _fetch_fundamentals_map([r.symbol for r in _SECTOR_UNIVERSE])

    # Only warm the cache on a real result — never cache an empty map from a
    # transient DB outage, or we'd serve "no fundamentals" for the next 30 min.
    if fmap:
        try:
            redis_client.set(
                _FUND_CACHE_KEY, json.dumps(fmap), ex=_FUND_CACHE_TTL_SECONDS
            )
        except Exception as exc:  # noqa: BLE001 — cache write is best-effort
            logger.debug("[screener] fundamentals cache write failed: %s", exc)
    return fmap


# ── Live market metrics (price / day change / 1-year return) ──────────


def _market_token() -> Optional[str]:
    """A usable Kite access token for market quotes, or None. Market quotes are
    not user-specific, so ANY active KiteSession token works (mirrors the
    scheduler's _resolve_market_token). None → no live session; caller falls
    back to the yfinance batch. Never raises."""
    try:
        from backend.kite.auth import KITE_MOCK_MODE, read_kite_access_token

        if KITE_MOCK_MODE:
            return None  # mock quotes aren't real prices — skip the overlay
        from backend.brokers.sessions import get_active_kite_session
        from backend.database import SessionLocal

        db = SessionLocal()
        try:
            tok = read_kite_access_token(get_active_kite_session(db))
        finally:
            db.close()
        if tok and not tok.startswith("mock_") and len(tok) >= 20:
            return tok
    except Exception as exc:  # noqa: BLE001
        logger.debug("[screener] market token lookup failed: %s", exc)
    return None


# A handful of universe symbols carry a different ticker on Yahoo than the
# naive "<sym>.NS" (a demerger rename, a short code, etc.). Only these need an
# explicit map; everything else uses "<sym>.NS". Names with no working Yahoo
# ticker are simply left to Kite / a monogram-dash.
_YAHOO_TICKER_OVERRIDES: dict[str, str] = {
    "VODAFONEIDEA": "IDEA.NS",
    "TATAMOTORS": "TMPV.NS",  # post-demerger passenger-vehicle listing
}


def _yahoo_ticker(sym: str) -> str:
    return _YAHOO_TICKER_OVERRIDES.get(sym.upper(), f"{sym.upper()}.NS")


def _compute_market_metrics(symbols: list[str]) -> tuple[dict[str, dict], str]:
    """Compute ({UPPER_SYM: {price, change_pct, one_year_pct}}, source) for the
    universe. ``source`` is ``"kite"`` when a live broker session supplied the
    prices, else ``"yfinance"`` (delayed / best-effort), else ``"none"``.

    Baseline: ONE yfinance batch download (1y daily) gives all three for every
    resolvable name — always available, no broker session needed. Overlay: when
    a live Kite session exists, REPLACE price + day-change with the broker's live
    values (correct, real-time) since yfinance's NSE feed lags and, for a few
    symbols, carries wrong absolute prices. 1-year return stays the yfinance
    ratio. Best-effort throughout — a symbol no source can serve is simply
    absent (→ null → FE em-dash)."""
    out: dict[str, dict] = {}
    syms = [str(s).strip().upper() for s in symbols if str(s).strip()]
    if not syms:
        return {}, "none"

    # 1. yfinance batch baseline (price, day change, 1y return).
    try:
        import warnings

        import yfinance as yf

        yt = {s: _yahoo_ticker(s) for s in syms}
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            df = yf.download(
                list(yt.values()), period="1y", interval="1d", group_by="ticker",
                auto_adjust=False, progress=False, threads=True,
            )
        for s in syms:
            try:
                col = df[yt[s]]["Close"].dropna()
                if len(col) < 2:
                    continue
                price = float(col.iloc[-1])
                prev = float(col.iloc[-2])
                yr = float(col.iloc[0])
                out[s] = {
                    "price": round(price, 2),
                    "change_pct": round((price - prev) / prev * 100, 2)
                    if prev > 0 else None,
                    "one_year_pct": round((price - yr) / yr * 100, 2)
                    if yr > 0 else None,
                }
            except Exception:  # noqa: BLE001 — per-symbol, keep going
                continue
    except Exception as exc:  # noqa: BLE001
        logger.warning("[screener] yfinance batch metrics failed: %s", exc)

    # 2. Kite live overlay for price + day change (broker-grade, real-time).
    source = "yfinance" if out else "none"
    token = _market_token()
    if token:
        try:
            from backend.kite.market_data import get_live_quote

            raw = get_live_quote(token, [f"NSE:{s}" for s in syms]) or {}
            applied = False
            for inst, payload in raw.items():
                if not isinstance(payload, dict):
                    continue
                sym = str(inst).split(":")[-1].upper()
                lp = payload.get("last_price")
                if not isinstance(lp, (int, float)) or lp <= 0:
                    continue
                entry = out.setdefault(
                    sym, {"price": None, "change_pct": None, "one_year_pct": None}
                )
                entry["price"] = round(float(lp), 2)
                prev = (payload.get("ohlc") or {}).get("close")
                if isinstance(prev, (int, float)) and prev > 0:
                    entry["change_pct"] = round(
                        (float(lp) - float(prev)) / float(prev) * 100, 2
                    )
                applied = True
            if applied:
                source = "kite"
        except Exception as exc:  # noqa: BLE001
            logger.debug("[screener] kite quote overlay failed: %s", exc)

    return out, source


def _refresh_market_metrics() -> None:
    """Recompute the universe metrics + source and warm Redis. Guarded so only
    one background refresh runs at a time; never raises."""
    global _metrics_refreshing
    try:
        metrics, source = _compute_market_metrics(
            [r.symbol for r in _SECTOR_UNIVERSE]
        )
        if metrics:
            try:
                redis_client.set(
                    _METRICS_CACHE_KEY,
                    json.dumps({"m": metrics, "src": source}),
                    ex=_METRICS_TTL_SECONDS,
                )
            except Exception as exc:  # noqa: BLE001
                logger.debug("[screener] metrics cache write failed: %s", exc)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[screener] market metrics refresh failed: %s", exc)
    finally:
        with _metrics_lock:
            _metrics_refreshing = False


def _kick_metrics_refresh() -> None:
    """Start a background metrics refresh unless one is already running."""
    global _metrics_refreshing
    with _metrics_lock:
        if _metrics_refreshing:
            return
        _metrics_refreshing = True
    threading.Thread(
        target=_refresh_market_metrics, name="screener-metrics", daemon=True
    ).start()


def _market_metrics_cached() -> tuple[dict[str, dict], str]:
    """(metrics_map, source) from Redis. On a miss, kick a background refresh and
    return ({}, "warming") so the request never blocks on the ~10-15s yfinance
    batch — the FE fills the columns in on its next poll once the warm lands.
    ``source`` ∈ {"kite","yfinance","warming"}. Keyed by UPPER(symbol)."""
    try:
        raw = redis_client.get(_METRICS_CACHE_KEY)
        if raw:
            data = raw.decode() if isinstance(raw, (bytes, bytearray)) else raw
            parsed = json.loads(data)
            return parsed.get("m", {}), parsed.get("src", "yfinance")
    except Exception as exc:  # noqa: BLE001
        logger.debug("[screener] metrics cache read failed: %s", exc)
    _kick_metrics_refresh()
    return {}, "warming"


def warm_screener_metrics() -> None:
    """Public warm hook (called from cache_warm on login) — populate the metrics
    cache in the background if it's cold so the grid is ready when opened."""
    try:
        if redis_client.get(_METRICS_CACHE_KEY):
            return
    except Exception:  # noqa: BLE001
        pass
    _kick_metrics_refresh()


def _label_for_sector(canonical: str) -> str:
    return canonical.replace("_", " ").title()


# ── Stocks endpoint ───────────────────────────────────────────────────


@router.get(
    "/stocks",
    response_model=ScreenerStocksResponse,
    summary="Filterable/sortable stock universe with fundamentals + logos",
)
def get_screener_stocks(
    sector: Optional[str] = Query(None, description="canonical sector or alias"),
    mcap_tier: Optional[str] = Query(None, description="large | mid | small"),
    pe_max: Optional[float] = Query(None, description="max P/E (inclusive)"),
    roe_min: Optional[float] = Query(None, description="min ROE %% (inclusive)"),
    dy_min: Optional[float] = Query(
        None, description="min dividend yield %% — not served (always filters to none)"
    ),
    ret_min: Optional[float] = Query(
        None, description="min 1y return %% — not served (always filters to none)"
    ),
    sort_by: str = Query("market_cap_cr"),
    limit: int = Query(100, ge=1, le=300),
    _user_id: int = Depends(require_user),
) -> ScreenerStocksResponse:
    notes: list[str] = []

    # ── 1. Start from the curated universe, apply the cheap filters first ──
    rows = list(_SECTOR_UNIVERSE)

    if sector:
        normalized = normalize_sector(sector)
        if normalized is None:
            notes.append(f"unknown sector {sector!r} — sector filter ignored")
        elif normalized == "metals":
            # "metals" promotes steel (steel IS a metal in the user's model),
            # matching services.sector_universe.query_screener semantics.
            rows = [r for r in rows if r.sector in ("metals", "steel")]
        else:
            rows = [r for r in rows if r.sector == normalized]

    if mcap_tier:
        tier = _MCAP_TIER_ALIASES.get(mcap_tier.strip().lower())
        if tier is None:
            notes.append(f"unknown mcap_tier {mcap_tier!r} — tier filter ignored")
        else:
            lo, hi = _MCAP_TIERS[tier]
            if lo is not None:
                rows = [r for r in rows if r.mcap_cr >= lo]
            if hi is not None:
                rows = [r for r in rows if r.mcap_cr < hi]

    # ── 2. Hydrate fundamentals + market metrics from the Redis-cached maps ──
    # (Filtering happens in memory below; one warm cache entry serves every
    # sector/cap/valuation/sort combination — see _fundamentals_map_cached.)
    fmap = _fundamentals_map_cached()
    mmap, msource = _market_metrics_cached()  # price/change/1y map + its source

    def _metric(sym: str, key: str) -> Optional[float]:
        rec = fmap.get(sym.upper())
        if not rec:
            return None
        v = rec.get(key)
        return round(float(v), 2) if v is not None else None

    def _mkt(sym: str, key: str) -> Optional[float]:
        rec = mmap.get(sym.upper())
        if not rec:
            return None
        v = rec.get(key)
        return round(float(v), 2) if isinstance(v, (int, float)) else None

    enriched: list[ScreenerStock] = []
    for r in rows:
        pe = _metric(r.symbol, "pe")
        roe = _metric(r.symbol, "roe")

        # Fundamental filters: a row whose metric is null is EXCLUDED when a
        # threshold on that metric is set (we can't assert it passes), but kept
        # when no threshold targets it. Never fabricate a value to pass.
        if pe_max is not None:
            if pe is None or pe > pe_max:
                continue
        if roe_min is not None:
            if roe is None or roe < roe_min:
                continue

        enriched.append(
            ScreenerStock(
                symbol=r.symbol,
                name=r.name or r.symbol,
                sector=r.sector,
                market_cap_cr=r.mcap_cr,
                price=_mkt(r.symbol, "price"),
                change_pct=_mkt(r.symbol, "change_pct"),
                pe=pe,
                roe=roe,
                one_year_pct=_mkt(r.symbol, "one_year_pct"),
                div_yield=None,
                logo_url=None,  # hydrated in ONE batch after sort+slice (below)
            )
        )

    # ── 3. Honest handling of the unserved filters ────────────────────────
    # We have no dividend-yield or 1y-return source on this path, so a filter on
    # either would silently drop EVERY row (all are null). Rather than return an
    # empty grid that looks broken, we IGNORE these filters and disclose it.
    if dy_min is not None:
        notes.append(
            "dividend-yield filter ignored — yield is not served by the "
            "screener (DB exposes payout %, not yield)"
        )
    if ret_min is not None:
        notes.append(
            "1-year-return filter ignored — price-return is not served by the "
            "screener grid (hydrate via /api/markets/sparkline)"
        )

    # ── 4. Sort server-side ───────────────────────────────────────────────
    sf = sort_by.strip().lower()
    if sf not in _SORT_FIELDS:
        notes.append(f"unknown sort_by {sort_by!r} — sorted by market_cap_cr")
        sf = "market_cap_cr"

    if sf in ("symbol", "name"):
        enriched.sort(key=lambda s: (getattr(s, sf) or "").lower())
    else:
        # Numeric metrics: descending (top first), nulls last regardless of
        # direction so a missing PE/ROE never floats to the top of the grid.
        desc = sf != "pe"  # cheaper P/E first is the natural "cheap" sort
        enriched.sort(
            key=lambda s: (
                getattr(s, sf) is None,
                -(getattr(s, sf) or 0) if desc else (getattr(s, sf) or 0),
            )
        )

    enriched = enriched[:limit]

    # ── 4b. Hydrate logos for the FINAL page in ONE batch (cold-start fix) ──
    # Per-row logo resolution was a cold-call N+1 (~2 remote DB queries × every
    # row). Resolve only the symbols that survived the sort+slice, all at once.
    logo_map = _logo_map([s.symbol for s in enriched])
    for s in enriched:
        s.logo_url = logo_map.get(s.symbol.upper())

    if not fmap:
        notes.append("fundamentals source unavailable — PE shown as —")
    if msource == "warming":
        notes.append(
            "live prices warming up — price / day change / 1-year return fill in "
            "shortly"
        )
    elif msource == "yfinance":
        # Honest relay tag (CLAUDE.md): these aren't live broker prices. A few
        # NSE names carry a wrong absolute price on Yahoo's feed.
        notes.append(
            "price / day change / 1-year return are delayed (yfinance) — connect "
            "Kite for live broker prices"
        )

    return ScreenerStocksResponse(
        count=len(enriched),
        results=enriched,
        null_metrics=["div_yield"],
        note="; ".join(notes),
    )


# ── Search endpoint ───────────────────────────────────────────────────


@router.get(
    "/search",
    response_model=ScreenerSearchResponse,
    summary="Symbol/name search across the company universe",
)
def search_screener(
    q: str = Query(..., min_length=1, max_length=100),
    limit: int = Query(15, ge=1, le=50),
    _user_id: int = Depends(require_user),
) -> ScreenerSearchResponse:
    """Reuse the shared financials_db autosuggest (same service the
    /api/companies search uses) so the screener search never drifts from the
    global search universe."""
    try:
        from backend.market import financials_db as fdb

        hits = fdb.search_companies(q, limit=limit)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[screener] search failed q=%r: %s", q, exc)
        hits = []

    logo_map = _logo_map([h.symbol for h in hits])
    return ScreenerSearchResponse(
        results=[
            ScreenerSearchResult(
                symbol=h.symbol,
                name=h.name,
                sector=h.sector,
                logo_url=logo_map.get(h.symbol.upper()),
                has_fundamentals=h.has_fundamentals,
            )
            for h in hits
        ]
    )


# ── Sectors endpoint ──────────────────────────────────────────────────


@router.get(
    "/sectors",
    response_model=ScreenerSectorsResponse,
    summary="Available sectors for the filter rail",
)
def get_screener_sectors(
    _user_id: int = Depends(require_user),
) -> ScreenerSectorsResponse:
    counts: dict[str, int] = {}
    for r in _SECTOR_UNIVERSE:
        counts[r.sector] = counts.get(r.sector, 0) + 1

    sectors = [
        ScreenerSector(
            sector=name,
            label=_label_for_sector(name),
            count=counts.get(name, 0),
        )
        for name in known_sectors()
    ]
    return ScreenerSectorsResponse(sectors=sectors)
