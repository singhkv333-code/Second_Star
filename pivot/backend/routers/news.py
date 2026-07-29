"""News endpoint — back the Phase 3 stock detail side panel (#53).

Pulls news articles for a symbol via yfinance.Ticker(sym).news.
Trimmed to the top N items, with a normalized response shape so the FE
doesn't have to deal with yfinance's loose schema.

Endpoint:
  GET /api/news?symbol=RELIANCE&limit=10

yfinance's news payload has changed shape over time (sometimes flat,
sometimes nested under `content`). We tolerate both, falling through
to whichever fields exist.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import yfinance as yf  # type: ignore[import-untyped]
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from backend.routers._deps import require_user
from backend.routers._errors import http_error


router = APIRouter(prefix="/api/news", tags=["News"])
logger = logging.getLogger(__name__)


class NewsItem(BaseModel):
    title: str
    publisher: str | None
    url: str | None
    published_at: datetime | None
    thumbnail: str | None
    summary: str | None


class NewsResponse(BaseModel):
    symbol: str
    items: list[NewsItem]


def _normalize(item: dict[str, Any]) -> NewsItem | None:
    """yfinance News item normaliser. Two shapes seen:

    - Old (flat):  {title, publisher, link, providerPublishTime,
                    thumbnail: {resolutions: [{url}]}, ...}
    - New (nested under `content`):
                   {content: {title, summary, pubDate, thumbnail: {originalUrl},
                              clickThroughUrl: {url}, provider: {displayName}}}
    """
    if not isinstance(item, dict):
        return None
    content = item.get("content")
    if isinstance(content, dict):
        # New shape.
        title = str(content.get("title") or "").strip()
        if not title:
            return None
        publisher = None
        prov = content.get("provider")
        if isinstance(prov, dict):
            publisher = prov.get("displayName")
        click = content.get("clickThroughUrl")
        url = None
        if isinstance(click, dict):
            url = click.get("url")
        if not url:
            url = content.get("canonicalUrl") or None
            if isinstance(url, dict):
                url = url.get("url")
        thumbnail = None
        thumb = content.get("thumbnail")
        if isinstance(thumb, dict):
            thumbnail = thumb.get("originalUrl") or thumb.get("url")
        published_at: datetime | None = None
        pub = content.get("pubDate")
        if isinstance(pub, str):
            try:
                published_at = datetime.fromisoformat(pub.replace("Z", "+00:00"))
            except ValueError:
                published_at = None
        summary = content.get("summary") or None
        return NewsItem(
            title=title,
            publisher=str(publisher) if publisher else None,
            url=str(url) if url else None,
            published_at=published_at,
            thumbnail=str(thumbnail) if thumbnail else None,
            summary=str(summary) if summary else None,
        )

    # Old / flat shape.
    title = str(item.get("title") or "").strip()
    if not title:
        return None
    ts = item.get("providerPublishTime")
    published_at = (
        datetime.fromtimestamp(int(ts), tz=timezone.utc)
        if isinstance(ts, (int, float)) else None
    )
    thumbnail = None
    thumb = item.get("thumbnail")
    if isinstance(thumb, dict):
        resolutions = thumb.get("resolutions")
        if isinstance(resolutions, list) and resolutions:
            thumbnail = resolutions[0].get("url")
    return NewsItem(
        title=title,
        publisher=item.get("publisher") or None,
        url=item.get("link") or None,
        published_at=published_at,
        thumbnail=thumbnail,
        summary=None,  # flat shape doesn't carry summary
    )


@router.get(
    "",
    response_model=NewsResponse,
    summary="Get recent news articles for a symbol",
)
def get_news(
    symbol: str = Query(..., min_length=1, max_length=24),
    limit: int = Query(10, ge=1, le=50),
    exchange: str = Query("NSE", pattern="^(NSE|BSE)$"),
    _user_id: int = Depends(require_user),
) -> NewsResponse:
    sym = symbol.upper().strip()
    suffix = ".NS" if exchange == "NSE" else ".BO"
    yf_sym = sym if sym.endswith((".NS", ".BO")) else f"{sym}{suffix}"

    # yfinance is the only per-symbol news source (no Kite equivalent). `.news`
    # has no timeout arg and hangs on a cloud IP, so bound it — fail fast with a
    # clean 503 instead of a gateway timeout / browser "Failed to fetch".
    from backend.market.net_timeout import call_bounded
    raw = call_bounded(lambda: yf.Ticker(yf_sym).news or [],
                       timeout=6, default=None, label=f"yf.news {sym}")
    if raw is None:
        raise http_error(
            503, "not_yet_available",
            f"news for {sym} is temporarily unavailable (source timed out)",
        )

    items: list[NewsItem] = []
    for r in raw:
        item = _normalize(r)
        if item is not None:
            items.append(item)
        if len(items) >= limit:
            break

    return NewsResponse(symbol=sym, items=items)
