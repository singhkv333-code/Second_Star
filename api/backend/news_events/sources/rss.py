"""Generic RSS 2.0 / Atom 1.0 adapter.

Deliberately dependency-light: we use the stdlib ``xml.etree`` parser
instead of pulling in ``feedparser``. The feeds we ship in Phase 1
(RBI, BBC, Google News) are all well-formed, and any future weirdness
can be handled by a small per-source override in the registry without
a new dep.

Both RSS 2.0 and Atom 1.0 are supported. We try RSS first (most of our
feeds) and fall back to Atom when the root tag indicates so.

Polite-citizen rules baked in:

  - All requests carry ``settings.news_events_user_agent`` so the
    publisher can identify us and rate-limit cleanly.
  - HTTP 4xx is treated as fatal for that fetch (no retries with the
    same UA — re-config the registry instead).
  - HTTP 5xx and network errors raise ``SourceFetchError`` so the
    poller's exponential-backoff bookkeeping can engage.
  - We do NOT scrape article bodies here — that's Stage 3 (Phase 3+).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Optional
from xml.etree import ElementTree as ET

import httpx

from backend.config import settings
from backend.news_events.sources.base import (
    FetchedItem,
    SourceAdapter,
    SourceFetchError,
)

logger = logging.getLogger(__name__)


# Atom namespace. RSS 2.0 elements live in the default namespace.
_ATOM_NS = "{http://www.w3.org/2005/Atom}"

# Per-fetch timeout. RBI's feeds are slow during business hours; 15s is
# generous enough to ride through a slow TLS handshake without making
# the poll job miss its next tick.
_DEFAULT_TIMEOUT_SECONDS = 15.0


def _parse_rfc822(value: Optional[str]) -> Optional[datetime]:
    """Tolerant pubDate parser. RSS 2.0 spec is RFC 822 (the email
    format), but real feeds wander — some emit ``YYYY-MM-DDTHH:MM:SSZ``
    instead. Try RFC 822 first, then ISO 8601. Returns UTC-aware
    datetime or None on parse failure (caller leaves the column NULL)."""
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (TypeError, ValueError):
        pass
    # Atom feeds (and a few RSS feeds in the wild) use ISO 8601.
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def parse_feed(
    source_id: str,
    feed_xml: str | bytes,
) -> list[FetchedItem]:
    """Parse a feed body into ``FetchedItem`` records.

    Pulled out as a free function so unit tests can exercise the
    parser against recorded XML fixtures without touching httpx.
    """
    try:
        root = ET.fromstring(feed_xml)
    except ET.ParseError as exc:
        raise SourceFetchError(f"malformed XML: {exc}") from exc

    # Strip any namespace from the root tag for branching.
    root_tag = root.tag.split("}", 1)[-1].lower()

    if root_tag == "rss":
        return _parse_rss_2(source_id, root)
    if root_tag == "feed":
        return _parse_atom(source_id, root)
    raise SourceFetchError(f"unrecognised feed root <{root_tag}>")


def _parse_rss_2(source_id: str, root: ET.Element) -> list[FetchedItem]:
    """RSS 2.0 — items live at ``rss/channel/item``."""
    channel = root.find("channel")
    if channel is None:
        return []

    items: list[FetchedItem] = []
    for item in channel.findall("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        if not title or not link:
            # An item with neither a title nor a link is useless to
            # the funnel — skip it rather than persist a junk row.
            continue
        summary = (item.findtext("description") or "").strip() or None
        pub = _parse_rfc822(item.findtext("pubDate"))
        guid_el = item.find("guid")
        meta: dict[str, object] = {}
        if guid_el is not None and guid_el.text:
            meta["guid"] = guid_el.text.strip()
        categories = [
            c.text.strip()
            for c in item.findall("category")
            if c.text and c.text.strip()
        ]
        if categories:
            meta["categories"] = categories
        items.append(
            FetchedItem(
                source_id=source_id,
                url=link,
                title=title,
                summary=summary,
                published_at=pub,
                raw_metadata=meta,
            )
        )
    return items


def _atom_text(el: Optional[ET.Element]) -> Optional[str]:
    if el is None or el.text is None:
        return None
    text = el.text.strip()
    return text or None


def _parse_atom(source_id: str, root: ET.Element) -> list[FetchedItem]:
    """Atom 1.0 — entries live at ``feed/entry``."""
    items: list[FetchedItem] = []
    for entry in root.findall(f"{_ATOM_NS}entry"):
        title = _atom_text(entry.find(f"{_ATOM_NS}title")) or ""
        link_el = entry.find(f"{_ATOM_NS}link")
        link = link_el.get("href", "").strip() if link_el is not None else ""
        if not title or not link:
            continue
        summary = (
            _atom_text(entry.find(f"{_ATOM_NS}summary"))
            or _atom_text(entry.find(f"{_ATOM_NS}content"))
        )
        pub = _parse_rfc822(
            _atom_text(entry.find(f"{_ATOM_NS}published"))
            or _atom_text(entry.find(f"{_ATOM_NS}updated"))
        )
        meta: dict[str, object] = {}
        id_el = entry.find(f"{_ATOM_NS}id")
        if id_el is not None and id_el.text:
            meta["id"] = id_el.text.strip()
        items.append(
            FetchedItem(
                source_id=source_id,
                url=link,
                title=title,
                summary=summary,
                published_at=pub,
                raw_metadata=meta,
            )
        )
    return items


class RSSAdapter(SourceAdapter):
    """Polled RSS / Atom adapter.

    Instantiated once per source by the poller. Holds no per-call
    state; the same instance is safe to reuse across ticks.
    """

    def __init__(
        self,
        *,
        source_id: str,
        feed_url: str,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._source_id = source_id
        self._feed_url = feed_url
        self._timeout = timeout_seconds

    @property
    def source_id(self) -> str:
        return self._source_id

    @property
    def feed_url(self) -> str:
        return self._feed_url

    async def fetch(self) -> list[FetchedItem]:
        headers = {
            "User-Agent": settings.news_events_user_agent,
            "Accept": "application/rss+xml, application/atom+xml, "
            "application/xml;q=0.9, text/xml;q=0.8, */*;q=0.5",
        }
        try:
            async with httpx.AsyncClient(timeout=self._timeout, headers=headers) as client:
                resp = await client.get(self._feed_url, follow_redirects=True)
        except (httpx.HTTPError, httpx.InvalidURL) as exc:
            raise SourceFetchError(f"network error: {exc}") from exc

        if resp.status_code >= 500:
            raise SourceFetchError(
                f"upstream {resp.status_code} from {self._feed_url}"
            )
        if resp.status_code >= 400:
            # 4xx is treated as fatal for this source; the registry
            # should mark it disabled until a human re-checks.
            raise SourceFetchError(
                f"client {resp.status_code} from {self._feed_url}"
            )

        items = parse_feed(self._source_id, resp.content)
        logger.info(
            "[news_events.rss] fetched source=%s items=%d url=%s",
            self._source_id,
            len(items),
            self._feed_url,
        )
        return items
