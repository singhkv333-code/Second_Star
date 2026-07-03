"""NewsAPI.org client for the triggers subsystem.

GET https://newsapi.org/v2/everything

Free-tier limits: 100 requests/day. The monitor polls every 30s but
cancels on trigger fire or user-stop, so a single workflow burns
~2880 req/day worst case — well over the budget. The demo assumes
small N concurrent workflows; this client doesn't enforce a global
rate limit, just degrades gracefully on 429.

When ``NEWSAPI_KEY`` is empty we return ``[]`` and log once per call;
this lets the rest of the pipeline (monitor task, classifier, fire
rule) run end-to-end against the ``simulate`` endpoint without an
external dependency.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from backend.config import settings
from backend.triggers.credibility import score_source
from backend.triggers.models import NewsArticle


logger = logging.getLogger(__name__)


_NEWSAPI_URL = "https://newsapi.org/v2/everything"
_REQUEST_TIMEOUT_SECONDS = 10.0
_WINDOW_HOURS = 48
_PAGE_SIZE = 20


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_published(raw: Any) -> datetime:
    """NewsAPI returns ``"2025-05-13T08:42:01Z"`` — coerce to aware UTC."""
    if isinstance(raw, datetime):
        if raw.tzinfo is None:
            return raw.replace(tzinfo=timezone.utc)
        return raw.astimezone(timezone.utc)
    if not isinstance(raw, str) or not raw:
        return _now_utc()
    try:
        # Python's fromisoformat doesn't accept trailing 'Z' until 3.11+
        s = raw.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return _now_utc()


def _build_query(keywords: list[str]) -> str:
    """OR-join keywords with quotes around multi-word terms."""
    parts: list[str] = []
    seen: set[str] = set()
    for kw in keywords:
        if not isinstance(kw, str):
            continue
        s = kw.strip()
        if not s or s.lower() in seen:
            continue
        seen.add(s.lower())
        if " " in s:
            parts.append(f"\"{s}\"")
        else:
            parts.append(s)
    return " OR ".join(parts)


def _article_from_payload(item: dict[str, Any]) -> NewsArticle | None:
    """Convert one NewsAPI article dict to our model. Returns None for
    obviously-broken rows (missing title)."""
    title = item.get("title")
    if not isinstance(title, str) or not title.strip():
        return None
    src = item.get("source") or {}
    src_id_raw = src.get("id") if isinstance(src, dict) else None
    src_name_raw = src.get("name") if isinstance(src, dict) else None
    src_id = str(src_id_raw) if isinstance(src_id_raw, str) else ""
    src_name = str(src_name_raw) if isinstance(src_name_raw, str) else ""
    # Prefer the slug id for scoring; fall back to display name.
    cred = score_source(src_id or src_name)
    url_raw = item.get("url")
    url = str(url_raw) if isinstance(url_raw, str) else ""
    desc_raw = item.get("description")
    description = str(desc_raw) if isinstance(desc_raw, str) else ""
    published = _parse_published(item.get("publishedAt"))
    # NewsAPI doesn't return a stable id — synthesise one from the url
    # (falls back to a uuid if url is missing).
    article_id = url or f"art_{uuid.uuid4().hex[:12]}"
    return NewsArticle(
        id=article_id,
        title=title.strip(),
        description=description,
        source=src_name or src_id,
        source_id=src_id,
        url=url,
        published_at=published,
        credibility_score=cred,
    )


async def fetch_news(
    keywords: list[str],
    *,
    hours_back: int | None = None,
) -> list[NewsArticle]:
    """Fetch up to 20 articles from NewsAPI for the OR-joined keywords.

    ``hours_back`` overrides the default 48h look-back window when the
    caller wants tighter or looser recency. None falls through to the
    module-level default — keeps existing callers untouched.

    Mock-mode tolerance: if ``settings.newsapi_key`` is empty, log a
    warning and return an empty list. Same for HTTP / parse errors —
    the caller (workflow engine, classifier loop) must keep running.
    """
    api_key = (settings.newsapi_key or "").strip()
    if not api_key:
        logger.warning(
            "NEWSAPI_KEY is empty; fetch_news returning []. "
            "Set NEWSAPI_KEY in .env to enable real polling."
        )
        return []

    q = _build_query(keywords)
    if not q:
        return []

    window = int(hours_back) if hours_back and hours_back > 0 else _WINDOW_HOURS
    from_ts = (_now_utc() - timedelta(hours=window)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    params = {
        "q": q,
        "from": from_ts,
        "sortBy": "publishedAt",
        "language": "en",
        "pageSize": str(_PAGE_SIZE),
        "apiKey": api_key,
    }

    try:
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_SECONDS) as client:
            resp = await client.get(_NEWSAPI_URL, params=params)
    except httpx.HTTPError as e:
        logger.warning("NewsAPI request failed: %s", e)
        return []

    if resp.status_code == 429:
        logger.warning("NewsAPI rate-limited (429); returning []")
        return []
    if resp.status_code >= 400:
        logger.warning(
            "NewsAPI returned %s: %s", resp.status_code, resp.text[:200]
        )
        return []

    try:
        body = resp.json()
    except ValueError:
        logger.warning("NewsAPI returned non-JSON body")
        return []

    raw_articles = body.get("articles") if isinstance(body, dict) else None
    if not isinstance(raw_articles, list):
        return []

    out: list[NewsArticle] = []
    for item in raw_articles:
        if not isinstance(item, dict):
            continue
        article = _article_from_payload(item)
        if article is None:
            continue
        out.append(article)
    return out
