"""Third-party web sources for the workspace widgets: news feeds, and a check
of whether a page can be shown inside another app.

News. The News widget reads public RSS feeds from Indian market desks. Only
the feeds named in FEEDS are ever fetched — the client picks by id, never by
URL — so this cannot be turned into a way to make the server fetch anything
else. Every headline keeps its source and its link; nothing is rewritten,
summarised or ranked by opinion. Feeds are cached for five minutes, and
concurrent asks for the same feed share one upstream call.

Frames. Most sites forbid being framed (X-Frame-Options, or CSP
frame-ancestors), and a browser shows a forbidden frame as a blank box with
no reason. The Browser widget asks here first, so it can say "this site does
not allow it — open it in a tab" instead. This one does take a URL, so it is
guarded: http(s) only, standard ports only, every hop of a redirect chain
resolved and refused if it lands on a private, loopback, link-local or
reserved address, only headers are used, and the body is never returned.
"""
from __future__ import annotations

import email.utils
import html
import ipaddress
import re
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

_UA = "Mozilla/5.0 (compatible; PivotCharto/1.0; +https://pivot)"

FEEDS = {
    "et-markets":   ("Economic Times", "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms"),
    "et-stocks":    ("ET Stocks", "https://economictimes.indiatimes.com/markets/stocks/rssfeeds/2146842.cms"),
    "mint-markets": ("Mint", "https://www.livemint.com/rss/markets"),
    "bl-markets":   ("BusinessLine", "https://www.thehindubusinessline.com/markets/feeder/default.rss"),
    "cnbc-market":  ("CNBC-TV18", "https://www.cnbctv18.com/commonfeeds/v1/cne/rss/market.xml"),
}
# Business Standard and Moneycontrol publish feeds too, but refuse a client
# that says it is not a browser; we do not pretend to be one, so they are out.
DEFAULT_FEEDS = ("et-markets", "mint-markets", "cnbc-market", "bl-markets")

_FEED_TTL = 300.0
_feed_cache: dict[str, tuple[float, list[dict]]] = {}
_feed_lock = threading.Lock()
_feed_inflight: dict[str, threading.Event] = {}


def _ssl_ctx():
    import ssl
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


_TAG = re.compile(r"<[^>]+>")


def _text(s: str | None, limit: int = 0) -> str:
    t = html.unescape(_TAG.sub(" ", s or "")).replace("\xa0", " ")
    t = re.sub(r"\s+", " ", t).strip()
    return (t[:limit].rsplit(" ", 1)[0] + "…") if limit and len(t) > limit else t


def _when(s: str | None) -> float | None:
    if not s:
        return None
    try:
        return email.utils.parsedate_to_datetime(s.strip()).timestamp()
    except (TypeError, ValueError, IndexError):
        pass
    try:   # ISO 8601, which a few feeds use
        from datetime import datetime
        return datetime.fromisoformat(s.strip().replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


_ITEM = re.compile(r"<item\b[^>]*>(.*?)</item>", re.S | re.I)
_CDATA = re.compile(r"<!\[CDATA\[(.*?)\]\]>", re.S)


def _field(block: str, tag: str) -> str | None:
    m = re.search(rf"<{tag}\b[^>]*>(.*?)</{tag}>", block, re.S | re.I)
    if not m:
        return None
    v = m.group(1)
    c = _CDATA.search(v)
    return c.group(1) if c else v


def _parse(xml: bytes, source: str) -> list[dict]:
    """RSS 2.0 items by pattern rather than by an XML parser: feeds in the
    wild are often not well-formed, and this needs no native library."""
    doc = xml.decode("utf-8", "replace")
    out = []
    for m in _ITEM.finditer(doc):
        block = m.group(1)
        title = _text(_field(block, "title"))
        link = html.unescape(_text(_field(block, "link")))
        if not link:
            g = _field(block, "guid") or ""
            link = _text(g) if _text(g).startswith("http") else ""
        if not title or not link.startswith(("http://", "https://")):
            continue
        im = re.search(r"<(?:media:content|media:thumbnail|enclosure)\b[^>]*\burl=[\"']([^\"']+)", block, re.I)
        out.append({"title": title, "link": link, "source": source,
                    "summary": _text(_field(block, "description"), 220),
                    "ts": _when(_text(_field(block, "pubDate") or _field(block, "dc:date"))),
                    "image": html.unescape(im.group(1)) if im and im.group(1).startswith("http") else None})
    return out


def _feed(fid: str) -> list[dict]:
    name, url = FEEDS[fid]
    while True:
        with _feed_lock:
            hit = _feed_cache.get(fid)
            if hit and time.time() - hit[0] < _FEED_TTL:
                return hit[1]
            ev = _feed_inflight.get(fid)
            owner = ev is None
            if owner:
                ev = _feed_inflight[fid] = threading.Event()
        if not owner:
            ev.wait(12)
            continue
        try:
            req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "application/rss+xml, application/xml, text/xml"})
            with urllib.request.urlopen(req, timeout=8, context=_ssl_ctx()) as r:
                items = _parse(r.read(4_000_000), name)
            with _feed_lock:
                _feed_cache[fid] = (time.time(), items)
            return items
        except Exception:                      # noqa: BLE001 — one dead feed must not sink the rest
            with _feed_lock:
                stale = _feed_cache.get(fid)
            return stale[1] if stale else []
        finally:
            with _feed_lock:
                _feed_inflight.pop(fid, None)
            ev.set()


def news(sources: list[str] | None = None, q: str = "", limit: int = 60) -> dict:
    """Headlines from the chosen feeds, newest first, de-duplicated by title.
    `q` keeps items whose title or summary contains every word of it."""
    ids = [s for s in (sources or DEFAULT_FEEDS) if s in FEEDS] or list(DEFAULT_FEEDS)
    threads, results = [], {}

    def run(fid):
        results[fid] = _feed(fid)

    for fid in ids:
        t = threading.Thread(target=run, args=(fid,), daemon=True)
        t.start()
        threads.append(t)
    for t in threads:
        t.join(10)
    seen, items = set(), []
    words = [w for w in re.split(r"\s+", q.lower().strip()) if w]
    for fid in ids:
        for it in results.get(fid) or []:
            key = re.sub(r"\W+", "", it["title"].lower())[:80]
            if key in seen:
                continue
            hay = (it["title"] + " " + it["summary"]).lower()
            if words and not all(w in hay for w in words):
                continue
            seen.add(key)
            items.append(it)
    items.sort(key=lambda x: x["ts"] or 0, reverse=True)
    return {"items": items[:max(1, min(limit, 200))],
            "sources": [{"id": f, "name": FEEDS[f][0], "ok": bool(results.get(f))} for f in ids],
            "catalog": [{"id": k, "name": v[0]} for k, v in FEEDS.items()],
            "fetched": time.time()}


# ── can this page be framed? ─────────────────────────────────────────────

_FRAME_TTL = 3600.0
_frame_cache: dict[str, tuple[float, dict]] = {}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):  # noqa: D401 — we follow hops ourselves
        return None


def _public(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
                or ip.is_multicast or ip.is_unspecified):
            return False
    return True


def _check_url(url: str) -> str | None:
    u = urllib.parse.urlsplit(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        return "Only web addresses (http or https) can be opened."
    if u.port not in (None, 80, 443):
        return "Only standard web ports can be opened."
    if not _public(u.hostname):
        return "That address is not a public website."
    return None


def frame_check(url: str) -> dict:
    url = url.strip()
    if not re.match(r"^[a-z]+://", url, re.I):
        url = "https://" + url
    hit = _frame_cache.get(url)
    if hit and time.time() - hit[0] < _FRAME_TTL:
        return hit[1]
    opener = urllib.request.build_opener(_NoRedirect, urllib.request.HTTPSHandler(context=_ssl_ctx()))
    cur, out = url, None
    for _ in range(5):
        bad = _check_url(cur)
        if bad:
            out = {"url": url, "final": cur, "embeddable": False, "reason": bad, "blocked": True}
            break
        req = urllib.request.Request(cur, headers={"User-Agent": _UA, "Accept": "text/html"})
        try:
            resp = opener.open(req, timeout=8)
            code, headers = resp.status, resp.headers
            resp.close()
        except urllib.error.HTTPError as e:
            code, headers = e.code, e.headers
        except Exception as e:                     # noqa: BLE001
            out = {"url": url, "final": cur, "embeddable": False,
                   "reason": "The site did not answer.", "detail": str(e)[:120]}
            break
        if code in (301, 302, 303, 307, 308) and headers.get("Location"):
            cur = urllib.parse.urljoin(cur, headers["Location"])
            continue
        xfo = (headers.get("X-Frame-Options") or "").strip().lower()
        csp = headers.get("Content-Security-Policy") or ""
        fa = re.search(r"frame-ancestors([^;]*)", csp, re.I)
        reason = None
        if xfo in ("deny", "sameorigin") or xfo.startswith("allow-from"):
            reason = "This site does not allow itself to be shown inside other apps."
        elif fa and "*" not in fa.group(1).split():
            reason = "This site does not allow itself to be shown inside other apps."
        out = {"url": url, "final": cur, "status": code, "embeddable": reason is None,
               **({"reason": reason} if reason else {})}
        break
    else:
        out = {"url": url, "final": cur, "embeddable": False, "reason": "Too many redirects."}
    _frame_cache[url] = (time.time(), out)
    return out



# ── live TV: a channel's current broadcast ──────────────────────────────────
# YouTube's "embed whatever this channel is streaming" address
# (embed/live_stream?channel=) stopped working: every channel answers it with
# "video unavailable". The channel's own /live page still names the broadcast
# it is showing, so the TV widget asks here for that video id and embeds the
# video itself. Only a channel id is accepted — the host is fixed.
_CHANNEL = re.compile(r"^UC[\w-]{22}$")
_CANON = re.compile(r'<link rel="canonical" href="https://www\.youtube\.com/watch\?v=([\w-]{11})"')
_LIVE_TTL = 600.0
_live_cache: dict[str, tuple[float, dict]] = {}


def live_video(channel: str) -> dict:
    """{"video": id|None, "live": bool} for a YouTube channel id."""
    if not _CHANNEL.match(channel or ""):
        return {"video": None, "live": False, "error": "not a channel id"}
    hit = _live_cache.get(channel)
    if hit and time.time() - hit[0] < _LIVE_TTL:
        return hit[1]
    try:
        req = urllib.request.Request(f"https://www.youtube.com/channel/{channel}/live",
                                     headers={"User-Agent": _UA, "Accept-Language": "en"})
        with urllib.request.urlopen(req, timeout=8, context=_ssl_ctx()) as r:
            page = r.read(3_000_000).decode("utf-8", "replace")
    except Exception as e:                     # noqa: BLE001 — answer, don't raise
        return {"video": None, "live": False, "error": f"YouTube did not answer ({type(e).__name__})"}
    m = _CANON.search(page)
    # a channel that is off air redirects /live to its channel page: no watch link
    out = {"video": m.group(1) if m else None, "live": bool(m) and '"isLive":true' in page}
    _live_cache[channel] = (time.time(), out)
    return out

if __name__ == "__main__":
    import json
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "frame":
        print(json.dumps(frame_check(sys.argv[2]), indent=1))
    else:
        d = news()
        print(json.dumps({"n": len(d["items"]), "sources": d["sources"], "first": d["items"][:2]}, indent=1))
