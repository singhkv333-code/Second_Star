"""HTTP surface for company autosuggest.

GET /api/companies/search?q=&limit=
  Fuzzy lookup over the Moneycontrol `mc.companies` universe — powers the
  global search bar and the chart "Compare to…" box on the FE. Read-only.
  Auth follows the same dev-mode auto-fallback as the financials router so
  the search works without a login flow in development.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel

from backend.config import settings
from backend.auth.jwt_handler import get_user_id_from_token
from backend.market import financials_db as fdb


router = APIRouter(prefix="/api/companies", tags=["Companies"])


def _auth(authorization: Optional[str]) -> int:
    if not authorization:
        if getattr(settings, "app_env", "development") == "development":
            return 1
        raise HTTPException(status_code=401, detail="Missing token")
    uid = get_user_id_from_token(authorization.replace("Bearer ", ""))
    if not uid:
        raise HTTPException(status_code=401, detail="Invalid token")
    return uid


class CompanySearchResult(BaseModel):
    symbol: str
    name: str
    sector: Optional[str] = None
    has_fundamentals: bool = False
    # Company logo URL (img.logo.dev), or null → FE renders a monogram.
    # Pulled from the precomputed mc.companies.logo_url column in the same
    # search query (no extra round-trip on the autosuggest hot path).
    logo_url: Optional[str] = None


class CompanySearchResponse(BaseModel):
    results: list[CompanySearchResult]


class CompanyLogosResponse(BaseModel):
    # symbol (UPPER) → img.logo.dev URL, or null when none is known. Callers
    # render a first-letter monogram for null entries.
    logos: dict[str, Optional[str]]


# Cap the batch so a crafted query can't fan out into an unbounded number of
# logo lookups. A single screener/portfolio table never shows this many rows.
_MAX_LOGO_SYMBOLS = 200


@router.get("/search", response_model=CompanySearchResponse)
def search_companies(
    q: str = Query(..., min_length=1, max_length=100),
    limit: int = Query(10, ge=1, le=50),
    authorization: Optional[str] = Header(None),
) -> CompanySearchResponse:
    """Autosuggest across the FULL instrument palette: companies (by name or
    trading symbol) plus ETFs, MCX commodities and indices, so the search bar
    surfaces everything Pivot offers, not just equities."""
    _auth(authorization)
    hits = fdb.search_companies(q, limit=limit)

    # Non-equity instruments (ETF / commodity / index). Strong hits (exact or
    # prefix symbol, exact keyword like "gold etf") outrank the fuzzy company
    # tail; weak substring hits append after companies. `sector` carries the
    # type label so the FE dropdown needs no changes.
    from backend.market.instrument_search import search_instruments

    instruments = search_instruments(q, limit=limit)
    strong = [i for i in instruments if i["_score"] <= 2]
    weak = [i for i in instruments if i["_score"] > 2]

    # Resolve every row through the shared resolver, which keys off the
    # company's REAL website domain (NOT the precomputed mc.companies.logo_url
    # column — those were guessed from the name and frequently pointed at a
    # different company's domain, e.g. Britannia -> bi.com). Redis-cached and
    # fail-safe; the result set is small (<= limit) so the autosuggest hot
    # path stays cheap after warm-up. Null -> FE renders a clean monogram.
    def _logo(h: "fdb.CompanyHit") -> Optional[str]:
        try:
            from backend.market.company_logos import get_logo_url
            return get_logo_url(h.symbol)
        except Exception:
            return None

    company_rows = [
        CompanySearchResult(
            symbol=h.symbol,
            name=h.name,
            sector=h.sector,
            has_fundamentals=h.has_fundamentals,
            logo_url=_logo(h),
        )
        for h in hits
    ]
    instrument_rows = {
        tier: [
            CompanySearchResult(
                symbol=i["symbol"], name=i["name"], sector=i["sector"],
                has_fundamentals=False, logo_url=None,
            )
            for i in group
        ]
        for tier, group in (("strong", strong), ("weak", weak))
    }
    merged: list[CompanySearchResult] = []
    seen: set[str] = set()
    for row in instrument_rows["strong"] + company_rows + instrument_rows["weak"]:
        if row.symbol not in seen:
            seen.add(row.symbol)
            merged.append(row)
    return CompanySearchResponse(results=merged[:limit])


@router.get("/logos", response_model=CompanyLogosResponse)
def company_logos(
    symbols: str = Query(
        ...,
        max_length=4000,
        description="Comma-separated symbols/tickers (e.g. RELIANCE,TCS,INFY)",
    ),
    authorization: Optional[str] = Header(None),
) -> CompanyLogosResponse:
    """Batch logo lookup for list/table surfaces (screener, portfolio).

    Reuses the shared, Redis-cached, fail-safe ``get_logo_url`` resolver so the
    screener and holdings tables get the same logos as the stock detail page in
    one round-trip. Unknown symbols map to ``null`` → FE monogram fallback.
    """
    _auth(authorization)
    from backend.market.company_logos import get_logo_url

    # De-dupe (preserving order) and cap the batch.
    seen: dict[str, None] = {}
    for raw in symbols.split(","):
        sym = raw.strip().upper()
        if sym and sym not in seen:
            seen[sym] = None
        if len(seen) >= _MAX_LOGO_SYMBOLS:
            break

    out: dict[str, Optional[str]] = {}
    for sym in seen:
        try:
            out[sym] = get_logo_url(sym)
        except Exception:  # noqa: BLE001 — never let one bad symbol 500 the batch
            out[sym] = None

    return CompanyLogosResponse(logos=out)
