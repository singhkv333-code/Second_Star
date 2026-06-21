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


@router.get("/search", response_model=CompanySearchResponse)
def search_companies(
    q: str = Query(..., min_length=1, max_length=100),
    limit: int = Query(10, ge=1, le=50),
    authorization: Optional[str] = Header(None),
) -> CompanySearchResponse:
    """Autosuggest matching companies by name or trading symbol."""
    _auth(authorization)
    hits = fdb.search_companies(q, limit=limit)

    # The search SQL pulls the precomputed mc.companies.logo_url. For rows
    # where it's null, fall back to the shared resolver (which also derives
    # a logo from the enrichment-DB website) so popular tickers without a
    # precomputed value still get a logo — same coverage as the detail page.
    # get_logo_url is Redis-cached and fail-safe; the result set is small
    # (<= limit), so this stays cheap on the autosuggest hot path.
    def _logo(h: "fdb.CompanyHit") -> Optional[str]:
        if h.logo_url:
            return h.logo_url
        try:
            from backend.market.company_logos import get_logo_url
            return get_logo_url(h.symbol)
        except Exception:
            return None

    return CompanySearchResponse(
        results=[
            CompanySearchResult(
                symbol=h.symbol,
                name=h.name,
                sector=h.sector,
                has_fundamentals=h.has_fundamentals,
                logo_url=_logo(h),
            )
            for h in hits
        ]
    )
