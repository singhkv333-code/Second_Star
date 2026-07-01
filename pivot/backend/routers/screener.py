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

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from backend.routers._deps import require_user
from backend.services.sector_universe import (
    _UNIVERSE as _SECTOR_UNIVERSE,
    known_sectors,
    normalize_sector,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/screener", tags=["Screener"])


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
    pe: Optional[float]
    roe: Optional[float]
    # Always null on this path — see module docstring. Kept in the shape so the
    # contract is stable and the FE can render an honest em-dash.
    div_yield: Optional[float] = None
    one_year_pct: Optional[float] = None
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


def _fetch_fundamentals_map(symbols: list[str]) -> dict[str, dict]:
    """Batch-fetch roe/pe for the page's symbols in ONE round-trip.

    Wrapped so a financials-DB outage degrades to "no fundamentals" (all-null
    columns) instead of 500'ing the whole universe — the curated universe alone
    is still a useful grid.
    """
    if not symbols:
        return {}
    try:
        from backend.services.fundamentals_screen import fetch_gate_inputs

        return fetch_gate_inputs(symbols)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[screener] fundamentals batch fetch failed: %s", exc)
        return {}


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

    # ── 2. Hydrate fundamentals for the (already sector/cap-filtered) set ──
    symbols = [r.symbol for r in rows]
    fmap = _fetch_fundamentals_map(symbols)

    def _metric(sym: str, key: str) -> Optional[float]:
        rec = fmap.get(sym.upper())
        if not rec:
            return None
        v = rec.get(key)
        return round(float(v), 2) if v is not None else None

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
                pe=pe,
                roe=roe,
                div_yield=None,
                one_year_pct=None,
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
        notes.append("fundamentals source unavailable — PE/ROE shown as —")

    return ScreenerStocksResponse(
        count=len(enriched),
        results=enriched,
        null_metrics=["div_yield", "one_year_pct"],
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
