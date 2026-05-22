"""Stage 3 — full-article body fetch + extraction.

Inputs:  one ``NewsArticle`` row that survived Stage 1+2.
Output:  the row's ``body_text`` / ``body_fetched_at`` /
         ``body_fetch_status`` columns populated.

Polite-citizen rules baked in:

  - Every HTTP request carries ``settings.news_events_user_agent``.
  - ``robots.txt`` is fetched once per host (cached for a session),
    parsed by ``urllib.robotparser``, and consulted before each
    article fetch. A Disallow → ``body_fetch_status='robots_disallowed'``,
    no body.
  - Exponential backoff retry on transient 5xx (3 attempts max).
  - 4xx is fatal — written as ``http_error`` with the status in the
    log; no retry. This avoids retry-storming a publisher that's
    intentionally blocking us.

Body extraction uses ``trafilatura.extract`` with conservative
settings — title kept, comments stripped, no boilerplate. Plain
text only; HTML / Markdown variants are added in Phase 3+ if a
downstream stage demands them.
"""
from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal, Optional
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx
import trafilatura  # type: ignore[import-untyped]

from backend.config import settings

logger = logging.getLogger(__name__)


BodyFetchStatus = Literal[
    "ok", "robots_disallowed", "http_error", "extract_failed"
]


@dataclass
class BodyFetchResult:
    """Per-article output of Stage 3."""

    status: BodyFetchStatus
    body_text: Optional[str] = None
    http_status: Optional[int] = None
    error: Optional[str] = None
    fetched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# Per-process robots.txt cache. The map is host → RobotFileParser.
# Process-lifetime cache is fine — a typical publisher's robots.txt
# changes maybe once a year; if we miss a change we re-read on
# process restart. Not invalidated on cadence to keep this simple.
_robots_cache: dict[str, RobotFileParser] = {}

_DEFAULT_TIMEOUT_SECONDS = 20.0
_MAX_BODY_BYTES = 2_500_000  # ~2.5 MB — anything larger is junk / a PDF.
_MAX_BODY_TEXT_CHARS = 50_000  # ~50 KB of extracted text caps cost downstream
_RETRY_ATTEMPTS = 3
_RETRY_BASE_DELAY = 0.6  # seconds


def _host_of(url: str) -> Optional[str]:
    try:
        return urlparse(url).netloc.lower() or None
    except (TypeError, ValueError):  # pragma: no cover — defensive
        return None


async def _load_robots(host: str) -> Optional[RobotFileParser]:
    """Return a cached RobotFileParser for the host, fetching once if
    needed. None means "robots.txt was unreachable" — caller treats
    this as 'allowed' (the default for ill-defined robots is to
    proceed)."""
    cached = _robots_cache.get(host)
    if cached is not None:
        return cached
    url = f"https://{host}/robots.txt"
    headers = {"User-Agent": settings.news_events_user_agent}
    try:
        async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT_SECONDS, headers=headers) as client:
            resp = await client.get(url, follow_redirects=True)
    except httpx.HTTPError as exc:
        logger.info(
            "[news_events.fetch_body] robots.txt unreachable host=%s err=%s",
            host,
            exc,
        )
        return None
    rp = RobotFileParser()
    if resp.status_code == 200:
        rp.parse(resp.text.splitlines())
    else:
        # 404 / 403 / 5xx — treat as no robots rules in effect.
        rp.parse([])
    _robots_cache[host] = rp
    return rp


async def _is_allowed_by_robots(url: str) -> bool:
    """True if robots.txt permits Pivot's UA to fetch the URL. A
    failure to determine (DNS error, network failure) returns True —
    fail-open is acceptable for low-volume, identified scraping."""
    host = _host_of(url)
    if not host:
        return False
    rp = await _load_robots(host)
    if rp is None:
        return True
    try:
        return rp.can_fetch(settings.news_events_user_agent, url)
    except Exception:  # noqa: BLE001 — robotparser is finicky
        return True


def _extract_body(html: str, url: str) -> Optional[str]:
    """trafilatura.extract — returns the article text or None.

    Conservative settings: include the title in the body, drop
    comments and boilerplate, no tables / images. Plain text. Caps
    output at ``_MAX_BODY_TEXT_CHARS`` to bound downstream LLM cost.
    """
    if not html:
        return None
    try:
        out = trafilatura.extract(
            html,
            url=url,
            include_comments=False,
            include_tables=False,
            include_images=False,
            include_links=False,
            output_format="txt",
            with_metadata=False,
            no_fallback=False,
        )
    except Exception:  # noqa: BLE001 — trafilatura is "best effort"
        return None
    if not out:
        return None
    text = out.strip()
    if not text:
        return None
    return text[:_MAX_BODY_TEXT_CHARS]


async def fetch_article_body(url: str) -> BodyFetchResult:
    """Top-level Stage-3 helper. Wraps robots + fetch + extract +
    retries into one call. Never raises — every failure path maps to
    a ``BodyFetchResult`` so the worker can persist the status.
    """
    if not url or not url.startswith(("http://", "https://")):
        return BodyFetchResult(
            status="http_error",
            error=f"invalid url scheme: {url[:80] if url else ''}",
        )

    if not await _is_allowed_by_robots(url):
        return BodyFetchResult(status="robots_disallowed")

    headers = {
        "User-Agent": settings.news_events_user_agent,
        "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5",
        "Accept-Language": "en-IN,en;q=0.9",
    }
    last_error: Optional[str] = None
    last_status: Optional[int] = None
    async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT_SECONDS, headers=headers) as client:
        for attempt in range(1, _RETRY_ATTEMPTS + 1):
            try:
                resp = await client.get(url, follow_redirects=True)
            except httpx.HTTPError as exc:
                last_error = str(exc)
                # Transient: backoff and retry.
                if attempt < _RETRY_ATTEMPTS:
                    await asyncio.sleep(
                        _RETRY_BASE_DELAY * (2 ** (attempt - 1))
                        + random.random() * 0.2
                    )
                    continue
                return BodyFetchResult(
                    status="http_error", error=last_error
                )

            last_status = resp.status_code
            if 200 <= resp.status_code < 300:
                content = resp.content[:_MAX_BODY_BYTES]
                try:
                    html = content.decode(resp.encoding or "utf-8", errors="replace")
                except (LookupError, ValueError):
                    html = content.decode("utf-8", errors="replace")
                body = _extract_body(html, url)
                if body is None:
                    return BodyFetchResult(
                        status="extract_failed",
                        http_status=resp.status_code,
                    )
                return BodyFetchResult(
                    status="ok",
                    body_text=body,
                    http_status=resp.status_code,
                )

            # 4xx is fatal; 5xx retries.
            if 400 <= resp.status_code < 500:
                return BodyFetchResult(
                    status="http_error",
                    http_status=resp.status_code,
                    error=f"client {resp.status_code}",
                )
            last_error = f"upstream {resp.status_code}"
            if attempt < _RETRY_ATTEMPTS:
                await asyncio.sleep(
                    _RETRY_BASE_DELAY * (2 ** (attempt - 1))
                    + random.random() * 0.2
                )
                continue
    return BodyFetchResult(
        status="http_error",
        http_status=last_status,
        error=last_error,
    )
