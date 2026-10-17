"""Multi-source company news aggregation for the stock detail page.

Sources are queried on demand, then passed through a shared entity gate,
cross-source headline deduplication, freshness ranking and source balancing.
Only feed/API metadata is used; Pivot does not scrape or republish articles.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from html import unescape
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from email.utils import parsedate_to_datetime
from typing import Any, Callable
from urllib.parse import urlencode, urljoin, urlparse
from xml.etree import ElementTree as ET

import httpx
import yfinance as yf  # type: ignore[import-untyped]
from sqlalchemy import text

from backend.database import SessionLocal

logger = logging.getLogger(__name__)

_POOL = ThreadPoolExecutor(max_workers=8, thread_name_prefix="company-news")
_CACHE_TTL_SECONDS = 300
_CACHE: dict[str, tuple[float, list["NewsCandidate"]]] = {}
_CACHE_LOCK = threading.Lock()
_PUBLIC_USER_AGENT = "PivotCompanyNews/0.1"
_NSE_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 Chrome/128 Safari/537.36"
)
_CORPORATE_SUFFIXES = {
    "limited", "ltd", "inc", "incorporated", "corporation", "corp", "plc",
}
_MARKET_CONTEXT = {
    "share", "shares", "stock", "stocks", "nse", "bse", "market", "markets",
    "earnings", "profit", "revenue", "results", "dividend", "investor",
    "company", "ceo", "deal", "bank", "group", "limited", "ltd",
}
_PUBLISHER_FEEDS = (
    (
        "economic_times",
        "The Economic Times",
        "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
    ),
    (
        "business_standard",
        "Business Standard",
        "https://www.business-standard.com/rss/markets-106.rss",
    ),
    ("mint", "Mint", "https://www.livemint.com/rss/markets"),
    (
        "businessline",
        "BusinessLine",
        "https://www.thehindubusinessline.com/markets/feeder/default.rss",
    ),
)


@dataclass(frozen=True)
class NewsCandidate:
    title: str
    publisher: str | None
    url: str | None
    published_at: datetime | None
    thumbnail: str | None
    summary: str | None
    provider: str
    kind: str = "article"
    thumbnail_kind: str | None = None


class NewsSourcesUnavailable(RuntimeError):
    """Every configured upstream failed or exceeded the time budget."""


def aggregate_company_news(
    *, symbol: str, exchange: str, company_name: str | None, limit: int,
) -> list[NewsCandidate]:
    """Return a company-specific, deduplicated and source-balanced feed."""
    sym = symbol.upper().strip()
    resolved_name = _resolve_company_name(sym) or (company_name or "").strip() or sym
    cache_key = f"{exchange}:{sym}:{resolved_name.casefold()}"
    now = time.monotonic()
    with _CACHE_LOCK:
        cached = _CACHE.get(cache_key)
        if cached and now - cached[0] < _CACHE_TTL_SECONDS:
            return cached[1][:limit]

    providers: list[tuple[str, Callable[[], list[NewsCandidate]]]] = [
        ("nse", lambda: _fetch_nse_announcements(sym)),
        ("gdelt", lambda: _fetch_gdelt(sym, resolved_name)),
        ("yahoo_finance", lambda: _fetch_yahoo(sym, exchange)),
    ]
    providers.extend((
        provider,
        lambda p=provider, n=publisher, u=url: _fetch_publisher_feed(p, n, u),
    ) for provider, publisher, url in _PUBLISHER_FEEDS)
    futures = {_POOL.submit(fetch): name for name, fetch in providers}
    done, pending = wait(futures, timeout=7.0)
    for future in pending:
        future.cancel()
        logger.info("company news provider timed out: %s", futures[future])

    available = 0
    candidates: list[NewsCandidate] = []
    for future in done:
        provider = futures[future]
        try:
            candidates.extend(future.result())
            available += 1
        except Exception as exc:  # noqa: BLE001 — one source must not sink the feed
            logger.info("company news provider failed (%s): %s", provider, str(exc)[:180])
    if available == 0:
        raise NewsSourcesUnavailable(f"all company-news sources failed for {sym}")

    relevant = [
        item for item in candidates
        if item.kind == "filing"
        or (item.thumbnail_kind == "article" and _is_relevant(item, sym, resolved_name))
    ]
    merged = _deduplicate(relevant)
    merged.sort(key=_sort_key, reverse=True)

    # First-party filings are useful but must not crowd reporting off the page.
    balanced: list[NewsCandidate] = []
    filings = 0
    for item in merged:
        if item.kind == "filing":
            if filings >= 2:
                continue
            filings += 1
        balanced.append(item)

    with _CACHE_LOCK:
        _CACHE[cache_key] = (now, balanced)
        if len(_CACHE) > 256:
            oldest = min(_CACHE, key=lambda key: _CACHE[key][0])
            _CACHE.pop(oldest, None)
    return balanced[:limit]


def _resolve_company_name(symbol: str) -> str | None:
    """Resolve via the authoritative identity table, with a safe fallback."""
    try:
        with SessionLocal() as db:
            row = db.execute(text("""
                SELECT verified_name
                  FROM company_identity
                 WHERE verified_symbol = :symbol
              ORDER BY mc_is_primary DESC, mc_metric_count DESC NULLS LAST
                 LIMIT 1
            """), {"symbol": symbol}).first()
        return str(row[0]).strip() if row and row[0] else None
    except Exception as exc:  # noqa: BLE001 — identity enrichment is optional here
        logger.debug("company-news identity lookup unavailable: %s", exc)
        return None


def _fetch_publisher_feed(
    provider: str, publisher: str, feed_url: str,
) -> list[NewsCandidate]:
    """Read an official publisher feed, retaining only image-backed stories."""
    headers = {"User-Agent": _NSE_USER_AGENT, "Accept": "application/rss+xml,application/xml,text/xml"}
    with httpx.Client(timeout=5.5, headers=headers, follow_redirects=True) as client:
        response = client.get(feed_url)
        response.raise_for_status()
    root = ET.fromstring(response.content)
    items: list[NewsCandidate] = []
    for node in root.findall(".//item")[:60]:
        title = unescape(_child_text(node, "title")).strip()
        url = _child_text(node, "link").strip()
        description = _child_text(node, "description")
        thumbnail = _feed_image(node, description, url)
        if not title or not url.startswith("http") or not thumbnail:
            continue
        summary = _clean_feed_description(description)
        items.append(NewsCandidate(
            title=title,
            publisher=publisher,
            url=url,
            published_at=_parse_rfc822(_child_text(node, "pubDate")),
            thumbnail=thumbnail,
            thumbnail_kind="article",
            summary=summary,
            provider=provider,
        ))
    return items


def _child_text(node: ET.Element, local_name: str) -> str:
    for child in node:
        if child.tag.rsplit("}", 1)[-1] == local_name:
            return child.text or ""
    return ""


def _feed_image(node: ET.Element, description: str, article_url: str) -> str | None:
    for child in node:
        if child.tag.rsplit("}", 1)[-1] not in {"content", "thumbnail", "enclosure"}:
            continue
        candidate = str(child.get("url") or "").strip()
        media_type = str(child.get("type") or "").casefold()
        if candidate and (not media_type or media_type.startswith("image/")):
            return urljoin(article_url, candidate)
    match = re.search(r"<img[^>]+src=[\"']([^\"']+)", description, flags=re.I)
    return urljoin(article_url, unescape(match.group(1))) if match else None


def _clean_feed_description(value: str) -> str | None:
    clean = unescape(re.sub(r"<[^>]+>", " ", value))
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean[:360] or None


def _fetch_nse_announcements(symbol: str) -> list[NewsCandidate]:
    today = datetime.now(timezone.utc).date()
    params = {
        "index": "equities",
        "from_date": (today - timedelta(days=31)).strftime("%d-%m-%Y"),
        "to_date": today.strftime("%d-%m-%Y"),
        "symbol": symbol,
    }
    headers = {
        "User-Agent": _NSE_USER_AGENT,
        "Accept": "application/json,text/plain,*/*",
        "Referer": "https://www.nseindia.com/companies-listing/corporate-filings-announcements",
    }
    with httpx.Client(timeout=5.5, headers=headers, follow_redirects=True) as client:
        response = client.get("https://www.nseindia.com/api/corporate-announcements", params=params)
        response.raise_for_status()
        payload = response.json()
    if not isinstance(payload, list):
        raise ValueError("unexpected NSE announcements response")

    items: list[NewsCandidate] = []
    for raw in payload[:12]:
        if not isinstance(raw, dict) or str(raw.get("symbol") or "").upper() != symbol:
            continue
        title = str(raw.get("attchmntText") or raw.get("desc") or "").strip()
        url = str(raw.get("attchmntFile") or "").strip()
        if not title or not url.startswith("http"):
            continue
        items.append(NewsCandidate(
            title=title,
            publisher="NSE Corporate Filings",
            url=url,
            published_at=_parse_datetime(raw.get("sort_date") or raw.get("an_dt")),
            thumbnail=_publisher_logo("https://www.nseindia.com"),
            thumbnail_kind="publisher",
            summary=str(raw.get("desc") or "").strip() or None,
            provider="nse",
            kind="filing",
        ))
    return items


def _fetch_gdelt(symbol: str, company_name: str) -> list[NewsCandidate]:
    phrase = _company_stem(company_name) or company_name
    terms = [f'"{phrase}"']
    if len(symbol) >= 4 and symbol.casefold() not in phrase.casefold():
        terms.append(symbol)
    params = {
        "query": f"({' OR '.join(terms)}) sourcelang:english",
        "mode": "artlist",
        "maxrecords": "30",
        "timespan": "1month",
        "sort": "datedesc",
        "format": "json",
    }
    headers = {"User-Agent": _PUBLIC_USER_AGENT, "Accept": "application/json"}
    with httpx.Client(timeout=5.5, headers=headers, follow_redirects=True) as client:
        response = client.get("https://api.gdeltproject.org/api/v2/doc/doc", params=params)
        response.raise_for_status()
        payload = response.json()
    articles = payload.get("articles") if isinstance(payload, dict) else None
    if not isinstance(articles, list):
        raise ValueError("unexpected GDELT article response")

    items: list[NewsCandidate] = []
    for raw in articles:
        if not isinstance(raw, dict):
            continue
        title = str(raw.get("title") or "").strip()
        url = str(raw.get("url") or "").strip()
        if not title or not url.startswith("http"):
            continue
        items.append(NewsCandidate(
            title=title,
            publisher=_publisher_from_domain(str(raw.get("domain") or "")),
            url=url,
            published_at=_parse_datetime(raw.get("seendate")),
            thumbnail=str(raw.get("socialimage") or "").strip() or None,
            thumbnail_kind="article" if raw.get("socialimage") else None,
            summary=None,
            provider="gdelt",
        ))
    return items


def _fetch_yahoo(symbol: str, exchange: str) -> list[NewsCandidate]:
    suffix = ".NS" if exchange == "NSE" else ".BO"
    ticker = symbol if symbol.endswith((".NS", ".BO")) else f"{symbol}{suffix}"
    return [item for raw in (yf.Ticker(ticker).news or [])
            if (item := _normalize_yahoo(raw)) is not None]


def _normalize_yahoo(item: Any) -> NewsCandidate | None:
    if not isinstance(item, dict):
        return None
    content = item.get("content")
    if isinstance(content, dict):
        title = str(content.get("title") or "").strip()
        if not title:
            return None
        provider = content.get("provider")
        publisher = provider.get("displayName") if isinstance(provider, dict) else None
        click = content.get("clickThroughUrl")
        url = click.get("url") if isinstance(click, dict) else None
        canonical = content.get("canonicalUrl")
        if not url:
            url = canonical.get("url") if isinstance(canonical, dict) else canonical
        thumb = content.get("thumbnail")
        thumbnail = (thumb.get("originalUrl") or thumb.get("url")) if isinstance(thumb, dict) else None
        return NewsCandidate(
            title=title, publisher=str(publisher) if publisher else None,
            url=str(url) if url else None,
            published_at=_parse_datetime(content.get("pubDate")),
            thumbnail=str(thumbnail) if thumbnail else None,
            thumbnail_kind="article" if thumbnail else None,
            summary=str(content.get("summary")) if content.get("summary") else None,
            provider="yahoo_finance",
        )

    title = str(item.get("title") or "").strip()
    if not title:
        return None
    thumbnail = None
    thumb = item.get("thumbnail")
    if isinstance(thumb, dict):
        resolutions = thumb.get("resolutions")
        if isinstance(resolutions, list) and resolutions and isinstance(resolutions[0], dict):
            thumbnail = resolutions[0].get("url")
    timestamp = item.get("providerPublishTime")
    published_at = (datetime.fromtimestamp(float(timestamp), tz=timezone.utc)
                    if isinstance(timestamp, (int, float)) else None)
    return NewsCandidate(
        title=title,
        publisher=str(item.get("publisher")) if item.get("publisher") else None,
        url=str(item.get("link")) if item.get("link") else None,
        published_at=published_at,
        thumbnail=str(thumbnail) if thumbnail else None,
        thumbnail_kind="article" if thumbnail else None,
        summary=None,
        provider="yahoo_finance",
    )


def _is_relevant(item: NewsCandidate, symbol: str, company_name: str) -> bool:
    value = _normalise_text(f"{item.title} {item.summary or ''}")
    stem = _normalise_text(_company_stem(company_name))
    if stem and stem in value:
        return True
    symbol_norm = _normalise_text(symbol)
    words = set(value.split())
    if symbol_norm in words:
        return len(symbol_norm) >= 5 or bool(words & _MARKET_CONTEXT)
    first = stem.split()[0] if stem else ""
    return len(first) >= 5 and first in words


def _deduplicate(items: list[NewsCandidate]) -> list[NewsCandidate]:
    kept: list[NewsCandidate] = []
    keys: list[str] = []
    for item in sorted(items, key=_sort_key, reverse=True):
        key = _canonical_title(item.title, item.publisher)
        if not key:
            continue
        duplicate_index = next((
            index for index, seen in enumerate(keys)
            if key == seen or SequenceMatcher(None, key, seen).ratio() >= 0.88
        ), None)
        if duplicate_index is not None:
            current = kept[duplicate_index]
            # Prefer genuine article media when a duplicate source exposes it.
            if item.thumbnail and (
                not current.thumbnail
                or (current.thumbnail_kind == "publisher" and item.thumbnail_kind == "article")
            ):
                kept[duplicate_index] = replace(
                    current,
                    thumbnail=item.thumbnail,
                    thumbnail_kind=item.thumbnail_kind,
                )
            if not current.summary and item.summary:
                kept[duplicate_index] = replace(kept[duplicate_index], summary=item.summary)
            continue
        kept.append(item)
        keys.append(key)
    return kept


def _publisher_logo(publisher_url: str) -> str | None:
    """Return an honest publisher-identity fallback, never a guessed article image."""
    parsed = urlparse(publisher_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    query = urlencode({"domain_url": publisher_url, "sz": 256})
    return f"https://www.google.com/s2/favicons?{query}"


def _strip_publisher_suffix(title: str, publisher: str | None) -> str:
    if not publisher:
        return title
    return re.sub(rf"\s*[-–—|]\s*{re.escape(publisher)}\s*$", "", title, flags=re.I).strip()


def _canonical_title(title: str, publisher: str | None) -> str:
    return _normalise_text(_strip_publisher_suffix(title, publisher))


def _company_stem(name: str) -> str:
    words = re.findall(r"[A-Za-z0-9]+", name)
    while words and words[-1].casefold() in _CORPORATE_SUFFIXES:
        words.pop()
    return " ".join(words)


def _normalise_text(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.casefold()))


def _publisher_from_domain(domain: str) -> str | None:
    clean = domain.casefold().removeprefix("www.")
    known = {
        "reuters.com": "Reuters", "moneycontrol.com": "Moneycontrol",
        "livemint.com": "Mint", "economictimes.indiatimes.com": "The Economic Times",
        "business-standard.com": "Business Standard", "cnbctv18.com": "CNBC-TV18",
        "thehindubusinessline.com": "BusinessLine", "ndtvprofit.com": "NDTV Profit",
    }
    if clean in known:
        return known[clean]
    root = clean.split(".")[0].replace("-", " ").strip()
    return root.title() if root else None


def _parse_rfc822(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    raw = str(value).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%d-%b-%Y %H:%M:%S", "%Y%m%dT%H%M%SZ"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _sort_key(item: NewsCandidate) -> tuple[datetime, int]:
    published = item.published_at or datetime.min.replace(tzinfo=timezone.utc)
    priority = {
        "nse": 7,
        "economic_times": 6,
        "business_standard": 5,
        "mint": 4,
        "businessline": 3,
        "gdelt": 2,
        "yahoo_finance": 1,
    }
    return published, priority.get(item.provider, 0)
