"""Multi-source, company-specific news for the stock detail page.

Endpoint:
  GET /api/news?symbol=RELIANCE&limit=10

yfinance's news payload has changed shape over time (sometimes flat,
sometimes nested under `content`). We tolerate both, falling through
to whichever fields exist.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from backend.routers._deps import require_user
from backend.routers._errors import http_error
from backend.services.company_news import (
    NewsSourcesUnavailable,
    aggregate_company_news,
)


router = APIRouter(prefix="/api/news", tags=["News"])


class NewsItem(BaseModel):
    title: str
    publisher: str | None
    url: str | None
    published_at: datetime | None
    thumbnail: str | None
    thumbnail_kind: str | None = None
    summary: str | None
    provider: str = "yahoo_finance"
    kind: str = "article"


class NewsResponse(BaseModel):
    symbol: str
    items: list[NewsItem]


@router.get(
    "",
    response_model=NewsResponse,
    summary="Get recent news articles for a symbol",
)
def get_news(
    symbol: str = Query(..., min_length=1, max_length=24),
    limit: int = Query(10, ge=1, le=50),
    exchange: str = Query("NSE", pattern="^(NSE|BSE)$"),
    company_name: str | None = Query(None, max_length=120),
    _user_id: int = Depends(require_user),
) -> NewsResponse:
    sym = symbol.upper().strip()
    try:
        candidates = aggregate_company_news(
            symbol=sym,
            exchange=exchange,
            company_name=company_name,
            limit=limit,
        )
    except NewsSourcesUnavailable:
        raise http_error(
            503, "not_yet_available",
            f"news for {sym} is temporarily unavailable (all sources timed out)",
        )
    items = [NewsItem(**candidate.__dict__) for candidate in candidates]
    return NewsResponse(symbol=sym, items=items)
