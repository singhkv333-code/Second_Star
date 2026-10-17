"""The Browser widget's search: a search engine API, not a model.

Two providers, tried in this order, each used only when configured:

  brave    Brave Search API (its own index), BRAVE_SEARCH_API_KEY.
  searxng  our own SearXNG instance (open-source metasearch, run in the
           browser container group beside this service), SEARXNG_URL.

Results are the engine's own titles and snippets — nothing here is written by
a model. Each answer is cached (webcache, 15 min; news 5 min) and an expired
copy is served if every provider fails.

search() returns None when no provider is configured, so the caller can say so.
"""
from __future__ import annotations

import html
import json
import os
import re
import ssl
import urllib.parse
import urllib.request

import webcache

_FRESH = {"day", "week", "month", "year"}
_BRAVE_FRESH = {"day": "pd", "week": "pw", "month": "pm", "year": "py"}
_TAG = re.compile(r"<[^>]+>")


def _ssl():
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:  # noqa: BLE001
        return ssl.create_default_context()


def _clean(s) -> str:
    return re.sub(r"\s+", " ", html.unescape(_TAG.sub("", str(s or "")))).strip()


def norm(u: str) -> str:
    sp = urllib.parse.urlsplit(u)
    qs = [(k, v) for k, v in urllib.parse.parse_qsl(sp.query, keep_blank_values=True)
          if not k.lower().startswith("utm_")]
    return urllib.parse.urlunsplit((sp.scheme, sp.netloc.lower(), sp.path or "/",
                                    urllib.parse.urlencode(qs), ""))


def providers() -> list[str]:
    out = []
    if os.environ.get("BRAVE_SEARCH_API_KEY"):
        out.append("brave")
    if os.environ.get("SEARXNG_URL"):
        out.append("searxng")
    return out


def _get(url: str, headers: dict, timeout: float = 8) -> dict:
    req = urllib.request.Request(url, headers={"Accept": "application/json", **headers})
    ctx = _ssl() if url.startswith("https:") else None
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
        return json.loads(r.read(4_000_000))


def _brave(q: str, page: int, kind: str, fresh: str) -> list[dict]:
    path = "news/search" if kind == "news" else "web/search"
    p = {"q": q, "count": 20 if kind == "news" else 10, "offset": page - 1,
         "country": "IN", "search_lang": "en", "safesearch": "moderate"}
    if fresh in _BRAVE_FRESH:
        p["freshness"] = _BRAVE_FRESH[fresh]
    d = _get(f"https://api.search.brave.com/res/v1/{path}?" + urllib.parse.urlencode(p),
             {"X-Subscription-Token": os.environ["BRAVE_SEARCH_API_KEY"]})
    rows = (d.get("results") if kind == "news" else (d.get("web") or {}).get("results")) or []
    out = []
    for r in rows:
        out.append({"title": _clean(r.get("title")), "url": r.get("url") or "",
                    "snippet": _clean(r.get("description")),
                    "published": r.get("page_age") or r.get("age") or "",
                    "image": ((r.get("thumbnail") or {}).get("src") or "")})
    return out


def _searxng(q: str, page: int, kind: str, fresh: str) -> list[dict]:
    p = {"q": q, "format": "json", "pageno": page, "language": "en-IN", "safesearch": 1,
         "categories": "news" if kind == "news" else "general"}
    if fresh in _FRESH:
        p["time_range"] = fresh
    base = os.environ["SEARXNG_URL"].rstrip("/")
    d = _get(f"{base}/search?" + urllib.parse.urlencode(p), {"User-Agent": "PivotCharto/1.0"}, timeout=10)
    out = []
    for r in d.get("results") or []:
        out.append({"title": _clean(r.get("title")), "url": r.get("url") or "",
                    "snippet": _clean(r.get("content")),
                    "published": r.get("publishedDate") or "",
                    "image": r.get("thumbnail") or r.get("img_src") or "",
                    "engines": r.get("engines") or []})
    if not out and d.get("unresponsive_engines"):
        raise RuntimeError("engines unresponsive: " + ", ".join(e[0] for e in d["unresponsive_engines"][:4]))
    return out


_CALL = {"brave": _brave, "searxng": _searxng}


def search(q: str, page: int = 1, kind: str = "web", fresh: str = "") -> dict | None:
    q = (q or "").strip()[:300]
    page = max(1, min(int(page or 1), 10))
    kind = "news" if kind == "news" else "web"
    fresh = fresh if fresh in _FRESH else ""
    names = providers()
    if not names:
        return None
    base = {"q": q, "page": page, "kind": kind, "fresh": fresh}
    if not q:
        return {**base, "results": []}
    key = json.dumps([q.lower(), page, kind, fresh])
    hit = webcache.get_json("search", key)
    if hit:
        return {**hit, "cached": True}
    errors = []
    for name in names:
        try:
            rows = _CALL[name](q, page, kind, fresh)
        except Exception as e:  # noqa: BLE001 — try the next provider
            errors.append(f"{name}: {type(e).__name__}")
            continue
        out, seen = [], set()
        for r in rows:
            u = r["url"]
            if not u.startswith(("http://", "https://")):
                continue
            n = norm(u)
            if n in seen:
                continue
            seen.add(n)
            r["url"] = u
            r["site"] = urllib.parse.urlsplit(u).hostname or ""
            r["pdf"] = urllib.parse.urlsplit(u).path.lower().endswith(".pdf")
            if not r["title"]:
                r["title"] = r["site"]
            out.append(r)
        res = {**base, "results": out, "source": name, "more": len(rows) >= 8}
        if out:
            webcache.put_json("search", key, res, 300 if kind == "news" else 900)
        else:
            res["error"] = "No pages found for that."
        return res
    old = webcache.get_json("search", key, stale=True)
    if old:
        return {**old, "cached": True, "stale": True}
    return {**base, "results": [], "error": "Search did not answer; try again shortly.", "detail": errors}
