"""Web-search-brief tool — DuckDuckGo Instant Answer API.

Why this exists
---------------
Several user prompts need information the LLM lacks at training time:

- "What was last week's CPI print?"
- "What's the current RBI repo rate?"
- "Recent news on RELIANCE earnings"
- "Why did INFY drop last Tuesday?"
- "What's the latest yield on 10-year G-sec?"

Today the model invents plausible-sounding numbers. This tool gives the
chat layer a thin grounding hop into the public web — title + 1-2 line
snippet + URL — that the LLM cites in its response.

DuckDuckGo IA was chosen because:
- No API key required (no auth flow, no secret rotation).
- Returns structured `AbstractText` + `RelatedTopics` cheaply.
- Tolerates malformed queries without 4xx-ing.

The handler enforces:
- 1-hour Redis cache per query hash (saves redundant calls when the
  same Nifty / RBI / earnings query repeats).
- 5-second HTTP timeout so a slow web fetch can't blow the chat budget.
- Maximum 3 results per call (default 1).
- Maximum 1 call per turn (enforced at the chat layer via tool surface
  narrowing AND the orchestrator dispatcher).
- Plain-text snippet — never HTML or scripts.

The system.md gates when the model is allowed to call this:
"current affairs" / "recent news on X" / "today's macro" / "earnings
preview" / "yield right now". Routine queries that already have a
local tool (get_live_price, get_indicator, fetch.fundamental) MUST NOT
trigger a web search.
"""
from __future__ import annotations

import hashlib
import logging
import json
import re

import httpx

from backend.cache import redis_client


logger = logging.getLogger(__name__)


_DDG_IA_URL = "https://api.duckduckgo.com/"
_WIKI_SEARCH_URL = "https://en.wikipedia.org/w/api.php"
_WIKI_SUMMARY_URL = "https://en.wikipedia.org/api/rest_v1/page/summary/"
_CACHE_PREFIX = "web_search_brief:"
_CACHE_TTL_S = 60 * 60        # 1 hour
_TIMEOUT_S = 5.0


def _cache_key(query: str, n: int) -> str:
    h = hashlib.md5(f"{query}|{n}".encode("utf-8")).hexdigest()
    return f"{_CACHE_PREFIX}{h}"


def _read_cache(key: str) -> dict | None:
    try:
        raw = redis_client.get(key)
    except Exception:  # noqa: BLE001
        return None
    if raw is None:
        return None
    try:
        return json.loads(raw if isinstance(raw, str) else raw.decode())
    except (TypeError, json.JSONDecodeError):
        return None


def _write_cache(key: str, value: dict) -> None:
    try:
        redis_client.set(key, json.dumps(value), ex=_CACHE_TTL_S)
    except Exception as e:  # noqa: BLE001
        logger.debug("web_search_brief cache write failed: %s", e)


def _ddg_call(query: str) -> dict:
    """Single DuckDuckGo IA request. Returns the JSON dict (possibly
    empty)."""
    params = {
        "q": query,
        "format": "json",
        "no_redirect": "1",
        "no_html": "1",
        "skip_disambig": "0",
    }
    with httpx.Client(timeout=_TIMEOUT_S, follow_redirects=False) as cli:
        r = cli.get(_DDG_IA_URL, params=params, headers={
            "User-Agent": "PivotChat/1.0",
        })
    r.raise_for_status()
    return r.json() if r.content else {}


def _clean_snippet(text: str) -> str:
    """Strip residual HTML-ish bits and collapse whitespace."""
    if not text:
        return ""
    s = re.sub(r"<[^>]+>", "", text)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _to_results(payload: dict, max_results: int) -> list[dict]:
    """Distill the IA payload into a small list of {title, snippet, url}
    dicts. Combines AbstractText (the lead summary) + the first
    RelatedTopics entries that carry text and URLs.
    """
    out: list[dict] = []
    abstract = _clean_snippet(payload.get("AbstractText") or "")
    abstract_url = payload.get("AbstractURL") or ""
    heading = payload.get("Heading") or ""
    if abstract:
        out.append({
            "title": heading or "Summary",
            "snippet": abstract[:320],
            "url": abstract_url,
            "source": payload.get("AbstractSource") or "duckduckgo",
        })
    for rt in (payload.get("RelatedTopics") or [])[: max_results * 2]:
        if len(out) >= max_results:
            break
        if not isinstance(rt, dict):
            continue
        # RelatedTopics nests grouped topics under {Topics: [...]}.
        if isinstance(rt.get("Topics"), list):
            for sub in rt["Topics"][:2]:
                if len(out) >= max_results:
                    break
                if isinstance(sub, dict):
                    txt = _clean_snippet(sub.get("Text") or "")
                    url = sub.get("FirstURL") or ""
                    if txt and url:
                        out.append({
                            "title": txt.split(" - ")[0][:80],
                            "snippet": txt[:320],
                            "url": url,
                            "source": "duckduckgo",
                        })
            continue
        txt = _clean_snippet(rt.get("Text") or "")
        url = rt.get("FirstURL") or ""
        if txt and url:
            out.append({
                "title": txt.split(" - ")[0][:80],
                "snippet": txt[:320],
                "url": url,
                "source": "duckduckgo",
            })
    return out[:max_results]


def _wiki_search(query: str, max_results: int) -> list[dict]:
    """Wikipedia search → summary fallback. Returns up to `max_results`
    {title, snippet, url, source} dicts. Wikipedia's REST summary API
    gives a tight 2-3 line description ideal for grounding."""
    out: list[dict] = []
    try:
        with httpx.Client(timeout=_TIMEOUT_S) as cli:
            # 1. Search for matching pages.
            sr = cli.get(_WIKI_SEARCH_URL, params={
                "action": "query", "list": "search",
                "srsearch": query, "format": "json",
                "srlimit": max_results,
            }, headers={"User-Agent": "PivotChat/1.0"})
            sr.raise_for_status()
            hits = ((sr.json() or {}).get("query") or {}).get("search") or []
            # 2. For each hit, fetch a clean summary via the REST API.
            for hit in hits[:max_results]:
                title = (hit.get("title") or "").strip()
                if not title:
                    continue
                try:
                    sm = cli.get(
                        _WIKI_SUMMARY_URL + title.replace(" ", "_"),
                        headers={"User-Agent": "PivotChat/1.0"},
                    )
                    if sm.status_code != 200:
                        continue
                    sm_data = sm.json() or {}
                    snippet = _clean_snippet(sm_data.get("extract") or "")
                    if not snippet:
                        # Fall back to the search snippet (HTML-ish).
                        snippet = _clean_snippet(hit.get("snippet") or "")
                    url = (
                        (sm_data.get("content_urls") or {})
                        .get("desktop", {})
                        .get("page", "")
                    ) or f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}"
                    if snippet:
                        out.append({
                            "title": title,
                            "snippet": snippet[:480],
                            "url": url,
                            "source": "wikipedia",
                        })
                except (httpx.HTTPError, ValueError, KeyError):
                    continue
    except (httpx.HTTPError, ValueError):
        return out
    return out


async def web_search_brief(args: dict) -> dict:
    """Public handler. Returns `{query, results: [{title, snippet, url, source}], cached}`.

    Strategy: try DuckDuckGo Instant Answer first (cheap, fast,
    structured). If that returns nothing useful, fall back to a
    Wikipedia search + summary. Wikipedia covers entity definitions
    reliably (RBI, NIFTY 50, Reliance Industries, gold ETFs) and is
    free with no auth.
    """
    query = (args.get("query") or "").strip()
    try:
        max_results = int(args.get("max_results") or 3)
    except (TypeError, ValueError):
        max_results = 3
    max_results = max(1, min(max_results, 5))
    if not query:
        raise ValueError("web_search_brief needs a 'query' string.")
    if len(query) > 240:
        raise ValueError("web_search_brief query is too long (max 240 chars).")

    key = _cache_key(query, max_results)
    cached = _read_cache(key)
    if cached is not None:
        return {**cached, "cached": True}

    results: list[dict] = []
    try:
        payload = _ddg_call(query)
        results = _to_results(payload, max_results)
    except httpx.TimeoutException:
        pass
    except httpx.HTTPError as e:
        logger.debug("DDG IA failed: %s", e)

    # Fallback: Wikipedia if DDG produced nothing.
    if not results:
        results = _wiki_search(query, max_results)

    if not results:
        return {
            "query": query, "results": [], "cached": False,
            "note": (
                "No web summary matched this query. Answer from your own "
                "knowledge instead — do NOT tell the user a feed is "
                "missing or refuse; give the substantive answer you can."
            ),
        }

    body = {"query": query, "results": results, "cached": False}
    _write_cache(key, {"query": query, "results": results})
    return body
