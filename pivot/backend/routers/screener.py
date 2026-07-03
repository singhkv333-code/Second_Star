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
    # Total rows matching the filters ACROSS the whole universe — the FE's
    # "Showing N of M" + infinite-scroll cutoff. count stays the page size
    # for back-compat.
    total: int = 0
    offset: int = 0
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
    """Whole-universe roe/pe map, Redis-cached (see ``_FUND_CACHE_KEY``).

    Read-only view of whatever is warm: the endpoint synchronously tops up
    just the PAGE it is serving (bounded ~1-2s worst case) and kicks a
    background chunked warm for the rest — with a ~2,500-name universe a
    synchronous full fetch here would block a request for the better part of
    a minute. Keyed by UPPER(symbol). Fails open to an empty map.
    """
    try:
        raw = redis_client.get(_FUND_CACHE_KEY)
        if raw:
            data = raw.decode() if isinstance(raw, (bytes, bytearray)) else raw
            return json.loads(data)
    except Exception as exc:  # noqa: BLE001 — cache read is best-effort
        logger.debug("[screener] fundamentals cache read failed: %s", exc)
    return {}


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


# ── Full market universe ──────────────────────────────────────────────
# The grid used to serve ONLY the ~80-name curated sector_universe, so any
# filter shrank an already tiny list. The universe is now every company with
# a verified NSE symbol (mc.companies.nse_symbol — backfilled 2026-07-02),
# enriched with full name / sector / market-cap from the pivot_enrich DB.
# Cached in Redis for a day (listings/mcap drift slowly); served paginated.

_UNIVERSE_CACHE_KEY = "screener:universe:v2"
_UNIVERSE_TTL_SECONDS = 24 * 60 * 60
_universe_lock = threading.Lock()

# Background full-universe fundamentals warm (chunked) — one at a time.
_fund_warm_lock = threading.Lock()
_fund_warm_running = False
_FUND_WARM_CHUNK = 250

# Background page-metrics warm — small on-demand batches, merged into the
# shared metrics map.
_page_metrics_lock = threading.Lock()
_page_metrics_running = False


def _sector_slug(label: str) -> str:
    return "_".join((label or "other").strip().lower().split()) or "other"


def _load_full_universe() -> list[dict]:
    """Build the whole-market universe by merging the two source DBs.

    financials (mc.companies): the verified NSE symbol per sc_id.
    pivot_enrich: full company name, yfinance sector, market cap (₹ absolute).

    Cross-DB join happens here in Python (different physical DBs). Rows
    without enrichment still appear (sector "Other", mcap null) — honest
    presence beats silent omission. Sorted by market cap DESC, nulls last,
    so page 1 is the large caps."""
    from sqlalchemy import text as _text

    from backend.database import EnrichSessionLocal, FinancialsSessionLocal

    fin = FinancialsSessionLocal()
    try:
        sym_rows = fin.execute(
            _text(
                "SELECT sc_id, upper(nse_symbol) AS sym, company_name "
                "FROM mc.companies "
                "WHERE nse_symbol IS NOT NULL AND nse_symbol <> ''"
            )
        ).fetchall()
    finally:
        fin.close()

    enrich: dict[str, dict] = {}
    if EnrichSessionLocal is not None:
        edb = EnrichSessionLocal()
        try:
            for r in edb.execute(
                _text(
                    "SELECT sc_id, long_name, sector, market_cap "
                    "FROM enrich.v_company_enriched"
                )
            ).fetchall():
                m = r._mapping
                enrich[m["sc_id"]] = {
                    "name": m["long_name"],
                    "sector": m["sector"],
                    "mcap": float(m["market_cap"]) if m["market_cap"] else None,
                }
        finally:
            edb.close()

    seen: set[str] = set()
    out: list[dict] = []
    for row in sym_rows:
        sc_id, sym, mc_name = row[0], row[1], row[2]
        if not sym or sym in seen:
            continue
        seen.add(sym)
        e = enrich.get(sc_id) or {}
        label = (e.get("sector") or "Other").strip() or "Other"
        mcap = e.get("mcap")
        out.append({
            "symbol": sym,
            "name": (e.get("name") or mc_name or sym).strip(),
            "sector": _sector_slug(label),
            "sector_label": label,
            # yfinance market cap is absolute ₹; 1 crore = 1e7.
            "mcap_cr": int(mcap / 1e7) if mcap else None,
        })
    out.sort(key=lambda r: (r["mcap_cr"] is None, -(r["mcap_cr"] or 0)))
    return out


def _full_universe() -> list[dict]:
    """Redis-cached whole-market universe. On a cold cache the build costs
    two cross-DB queries (~1-2s); everyone after reads the warm entry."""
    try:
        raw = redis_client.get(_UNIVERSE_CACHE_KEY)
        if raw:
            data = raw.decode() if isinstance(raw, (bytes, bytearray)) else raw
            parsed = json.loads(data)
            if isinstance(parsed, list) and parsed:
                return parsed
    except Exception as exc:  # noqa: BLE001
        logger.debug("[screener] universe cache read failed: %s", exc)

    with _universe_lock:
        # Re-check under the lock — another request may have just warmed it.
        try:
            raw = redis_client.get(_UNIVERSE_CACHE_KEY)
            if raw:
                data = raw.decode() if isinstance(raw, (bytes, bytearray)) else raw
                parsed = json.loads(data)
                if isinstance(parsed, list) and parsed:
                    return parsed
        except Exception:  # noqa: BLE001
            pass
        try:
            uni = _load_full_universe()
        except Exception as exc:  # noqa: BLE001
            logger.warning("[screener] full universe build failed: %s", exc)
            # Degrade to the curated universe rather than an empty grid.
            uni = [
                {
                    "symbol": r.symbol,
                    "name": r.name or r.symbol,
                    "sector": r.sector,
                    "sector_label": _label_for_sector(r.sector),
                    "mcap_cr": r.mcap_cr,
                }
                for r in _SECTOR_UNIVERSE
            ]
        if uni:
            try:
                redis_client.set(
                    _UNIVERSE_CACHE_KEY, json.dumps(uni), ex=_UNIVERSE_TTL_SECONDS
                )
            except Exception as exc:  # noqa: BLE001
                logger.debug("[screener] universe cache write failed: %s", exc)
        return uni


def _merge_fundamentals_cache(new_entries: dict[str, dict]) -> None:
    """Read-merge-write the shared fundamentals map (best-effort)."""
    if not new_entries:
        return
    try:
        raw = redis_client.get(_FUND_CACHE_KEY)
        cur = json.loads(raw.decode() if isinstance(raw, (bytes, bytearray)) else raw) if raw else {}
    except Exception:  # noqa: BLE001
        cur = {}
    cur.update(new_entries)
    try:
        redis_client.set(_FUND_CACHE_KEY, json.dumps(cur), ex=_FUND_CACHE_TTL_SECONDS)
    except Exception as exc:  # noqa: BLE001
        logger.debug("[screener] fundamentals cache merge failed: %s", exc)


def _kick_full_fundamentals_warm(symbols: list[str]) -> None:
    """Warm PE/ROE for the WHOLE universe in background chunks, merging into
    the shared map after each chunk so results improve while the user browses.
    Single-flight; never raises."""
    global _fund_warm_running
    with _fund_warm_lock:
        if _fund_warm_running:
            return
        _fund_warm_running = True

    def _run() -> None:
        global _fund_warm_running
        try:
            for i in range(0, len(symbols), _FUND_WARM_CHUNK):
                chunk = symbols[i : i + _FUND_WARM_CHUNK]
                fmap = _fetch_fundamentals_map(chunk)
                _merge_fundamentals_cache(fmap)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[screener] fundamentals warm failed: %s", exc)
        finally:
            with _fund_warm_lock:
                _fund_warm_running = False

    threading.Thread(target=_run, name="screener-fund-warm", daemon=True).start()


def _kick_page_metrics_warm(symbols: list[str]) -> None:
    """Warm price/1y metrics for a page's symbols in the background and merge
    them into the shared metrics map. Small batches (≤ a page) so a deep-scroll
    session progressively fills without ever blocking a request."""
    global _page_metrics_running
    syms = [s for s in symbols if s]
    if not syms:
        return
    with _page_metrics_lock:
        if _page_metrics_running:
            return
        _page_metrics_running = True

    def _run() -> None:
        global _page_metrics_running
        try:
            metrics, source = _compute_market_metrics(syms)
            if metrics:
                try:
                    raw = redis_client.get(_METRICS_CACHE_KEY)
                    parsed = (
                        json.loads(raw.decode() if isinstance(raw, (bytes, bytearray)) else raw)
                        if raw
                        else {"m": {}, "src": source}
                    )
                    parsed.setdefault("m", {}).update(metrics)
                    # Keep the strongest source label we've seen.
                    if source == "kite" or parsed.get("src") == "warming":
                        parsed["src"] = source
                    redis_client.set(
                        _METRICS_CACHE_KEY, json.dumps(parsed), ex=_METRICS_TTL_SECONDS
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.debug("[screener] page metrics merge failed: %s", exc)
        except Exception as exc:  # noqa: BLE001
            logger.debug("[screener] page metrics warm failed: %s", exc)
        finally:
            with _page_metrics_lock:
                _page_metrics_running = False

    threading.Thread(target=_run, name="screener-page-metrics", daemon=True).start()


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
    sort_dir: Optional[str] = Query(
        None, pattern="^(asc|desc)$",
        description="override the default direction for the sort field",
    ),
    limit: int = Query(60, ge=1, le=300),
    offset: int = Query(0, ge=0),
    _user_id: int = Depends(require_user),
) -> ScreenerStocksResponse:
    notes: list[str] = []

    # ── 1. Start from the WHOLE market universe (every verified NSE name),
    # apply the cheap filters first. Paginated below — the FE loads pages
    # incrementally, but filters/sorts always run over the full universe.
    universe = _full_universe()
    rows = universe

    if sector:
        want = _sector_slug(sector)
        # Back-compat: canonical curated keys (e.g. "private_bank") won't
        # match yfinance sector slugs — fall back to a substring match so
        # old links degrade gracefully instead of to zero rows.
        matched = [r for r in rows if r["sector"] == want]
        if not matched:
            matched = [r for r in rows if want in r["sector"]]
        if matched:
            rows = matched
        else:
            notes.append(f"unknown sector {sector!r} — sector filter ignored")

    if mcap_tier:
        tier = _MCAP_TIER_ALIASES.get(mcap_tier.strip().lower())
        if tier is None:
            notes.append(f"unknown mcap_tier {mcap_tier!r} — tier filter ignored")
        else:
            lo, hi = _MCAP_TIERS[tier]
            if lo is not None:
                rows = [r for r in rows if (r["mcap_cr"] or 0) >= lo]
            if hi is not None:
                rows = [r for r in rows if (r["mcap_cr"] or 0) < hi]

    # ── 2. Hydrate fundamentals + market metrics from the Redis-cached maps ──
    # Fundamentals for a ~2,500-name universe warm in background chunks; the
    # page being served is topped up synchronously below so the visible rows
    # always carry real PE/ROE.
    fmap = _fundamentals_map_cached()
    mmap, msource = _market_metrics_cached()  # price/change/1y map + its source

    # Coverage-driven warm: if a meaningful slice of the universe has no
    # fundamentals yet, kick the chunked background warm (single-flight).
    if len(fmap) < len(universe) * 0.9:
        _kick_full_fundamentals_warm([r["symbol"] for r in universe])
        if pe_max is not None or roe_min is not None or sort_by in ("pe", "roe"):
            notes.append(
                "fundamentals are still warming for the full market — more rows "
                "will match this filter shortly"
            )

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
        pe = _metric(r["symbol"], "pe")
        roe = _metric(r["symbol"], "roe")

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
                symbol=r["symbol"],
                name=r["name"] or r["symbol"],
                sector=r["sector"],
                market_cap_cr=r["mcap_cr"],
                price=_mkt(r["symbol"], "price"),
                change_pct=_mkt(r["symbol"], "change_pct"),
                pe=pe,
                roe=roe,
                one_year_pct=_mkt(r["symbol"], "one_year_pct"),
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
        enriched.sort(
            key=lambda s: (getattr(s, sf) or "").lower(),
            reverse=sort_dir == "desc",
        )
    else:
        # Numeric metrics: descending (top first) by default, nulls last
        # regardless of direction so a missing PE/ROE never floats to the top.
        desc = (sort_dir == "desc") if sort_dir else (sf != "pe")
        enriched.sort(
            key=lambda s: (
                getattr(s, sf) is None,
                -(getattr(s, sf) or 0) if desc else (getattr(s, sf) or 0),
            )
        )

    total = len(enriched)
    enriched = enriched[offset : offset + limit]

    # ── 4b. Page top-up: synchronously fetch PE/ROE for JUST the visible
    # page's symbols that the warm map doesn't cover yet (bounded ≤ limit —
    # the same latency class the old 80-name fetch had), and merge the
    # result back so the next pages/callers benefit.
    missing = [s.symbol for s in enriched if s.symbol.upper() not in fmap]
    if missing:
        topup = _fetch_fundamentals_map(missing)
        if topup:
            _merge_fundamentals_cache(topup)
            for s in enriched:
                rec = topup.get(s.symbol.upper())
                if rec:
                    if s.pe is None and rec.get("pe") is not None:
                        s.pe = round(float(rec["pe"]), 2)
                    if s.roe is None and rec.get("roe") is not None:
                        s.roe = round(float(rec["roe"]), 2)

    # Prices for the page fill in from a small background batch (never
    # blocks the request) merged into the shared metrics map.
    page_missing_mkt = [
        s.symbol for s in enriched if s.symbol.upper() not in mmap
    ]
    if page_missing_mkt:
        _kick_page_metrics_warm(page_missing_mkt)

    # ── 4c. Hydrate logos for the FINAL page in ONE batch (cold-start fix) ──
    # Per-row logo resolution was a cold-call N+1 (~2 remote DB queries × every
    # row). Resolve only the symbols that survived the sort+slice, all at once.
    logo_map = _logo_map([s.symbol for s in enriched])
    for s in enriched:
        s.logo_url = logo_map.get(s.symbol.upper())

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
        total=total,
        offset=offset,
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
    """Sector rail derived from the FULL market universe (enrich sectors),
    so the filter counts agree with what /stocks actually serves."""
    counts: dict[str, tuple[str, int]] = {}
    for r in _full_universe():
        key = r["sector"]
        label = r.get("sector_label") or _label_for_sector(key)
        prev = counts.get(key)
        counts[key] = (label, (prev[1] if prev else 0) + 1)

    sectors = [
        ScreenerSector(sector=key, label=label, count=n)
        for key, (label, n) in sorted(
            counts.items(), key=lambda kv: -kv[1][1]
        )
    ]
    return ScreenerSectorsResponse(sectors=sectors)
