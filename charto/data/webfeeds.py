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
import json
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
    # what is on, and the channel's own picture, for the widget's guide
    t = re.search(r'"videoDetails":\{"videoId":"[^"]+","title":"((?:[^"\\]|\\.)*)"', page)
    if t and out["live"]:
        try:
            out["title"] = json.loads(f'"{t.group(1)}"')[:160]
        except ValueError:
            pass
    av = re.search(r"https://yt3\.ggpht\.com/[\w\-=/]+", page)
    if av:
        out["avatar"] = re.sub(r"=s\d+-", "=s88-", av.group(0))
    _live_cache[channel] = (time.time(), out)
    return out

# ── the Browser widget: Wikipedia search, and a reading view ───────────────
#
# The Browser is a research tool, not a general browser: it searches
# Wikipedia, shows an article as clean text with its own links kept inside,
# and reads any other page as text (title, headings, paragraphs). Nothing a
# page sends is run; Wikipedia's HTML is rebuilt from an allow-list here, and
# every other page comes back as plain strings the widget escapes.

_WIKI_LANGS = {"en", "hi"}
_wiki_cache: dict[str, tuple[float, dict]] = {}
_reader_cache: dict[str, tuple[float, dict]] = {}
_WIKI_UA = "PivotCharto/1.0 (research browser; https://pivot)"


def _get_json(url: str, timeout: float = 8) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": _WIKI_UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout, context=_ssl_ctx()) as r:
        return json.loads(r.read(6_000_000))


def _strip_tags(s: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", s or "")).strip()


def wiki_search(q: str, lang: str = "en") -> dict:
    q = (q or "").strip()[:200]
    lang = lang if lang in _WIKI_LANGS else "en"
    if not q:
        return {"q": q, "results": []}
    key = f"s:{lang}:{q.lower()}"
    hit = _wiki_cache.get(key)
    if hit and time.time() - hit[0] < 600:
        return hit[1]
    try:
        d = _get_json(f"https://{lang}.wikipedia.org/w/rest.php/v1/search/page?"
                      + urllib.parse.urlencode({"q": q, "limit": 12}))
    except Exception as e:                     # noqa: BLE001 — answer, don't raise
        return {"q": q, "results": [], "error": f"Wikipedia did not answer ({type(e).__name__})"}
    res = []
    for p in d.get("pages") or []:
        th = (p.get("thumbnail") or {}).get("url")
        res.append({
            "title": p.get("title"), "key": p.get("key"),
            "description": p.get("description") or "",
            # the excerpt marks matches with <span class="searchmatch">; keep
            # only that, as [[…]], and escape the rest in the widget
            "excerpt": html.unescape(re.sub(r"<(?!/?span)[^>]+>", "", re.sub(
                r'<span class="searchmatch">(.*?)</span>', r"[[\1]]", p.get("excerpt") or ""))),
            "thumb": ("https:" + th) if th and th.startswith("//") else th,
        })
    out = {"q": q, "lang": lang, "results": res}
    _wiki_cache[key] = (time.time(), out)
    return out


from html.parser import HTMLParser  # noqa: E402 — used only below


class _WikiClean(HTMLParser):
    """Rebuilds an article from an allow-list: text, structure, tables,
    Wikimedia images, and links — /wiki/ links become in-reader links."""
    KEEP = {"p", "h2", "h3", "h4", "h5", "ul", "ol", "li", "b", "strong", "i", "em", "a", "table", "caption",
            "thead", "tbody", "tfoot", "tr", "th", "td", "blockquote", "figure", "figcaption", "img", "dl",
            "dt", "dd", "br", "sub", "sup", "code", "pre", "abbr", "small"}
    VOID = {"img", "br"}
    DROP = {"script", "style", "link", "meta", "noscript", "math", "svg", "map", "audio", "video", "button", "input", "form"}
    BAD = re.compile(r"\b(reference|mw-editsection|navbox|vertical-navbox|metadata|ambox|mbox-small|reflist|references|"
                     r"noprint|mw-empty-elt|sistersitebox|side-box|mw-cite-backlink|shortdescription|toc|portalbox|"
                     r"catlinks|authority-control|mw-references-wrap|sidebar|navigation-not-searchable|mw-jump-link|"
                     r"printfooter|hatnote-ignore|plainlinks metadata)\b")
    END = {"references", "notes", "external links", "further reading", "bibliography", "sources", "citations",
           "footnotes", "notes and references", "references and notes"}

    def __init__(self, lang: str):
        super().__init__(convert_charrefs=True)
        self.lang, self.out, self.skip, self.stack, self.done = lang, [], 0, [], False
        self.h2 = None                         # collecting an h2's text to test END
        self.toc: list[dict] = []

    def handle_starttag(self, tag, attrs):
        if self.done:
            return
        a = dict(attrs)
        if self.skip:
            if tag not in self.VOID:
                self.skip += 1
            return
        if tag in self.DROP or self.BAD.search(a.get("class") or "") or a.get("role") == "navigation" \
                or "display:none" in (a.get("style") or "").replace(" ", ""):
            if tag not in self.VOID:
                self.skip = 1
            return
        if tag not in self.KEEP:
            self.stack.append(None)            # a wrapper: its content stays, the tag goes
            return
        attr = ""
        if tag == "a":
            href = a.get("href") or ""
            m = re.match(r"^(?:\./|/wiki/)([^#?]+)(#.*)?$", href)
            if m and ":" not in urllib.parse.unquote(m.group(1)):
                title = urllib.parse.unquote(m.group(1)).replace("_", " ")
                attr = f' href="#" data-wiki="{html.escape(title, quote=True)}"'
            elif href.startswith("#"):
                attr = f' href="{html.escape(href, quote=True)}" data-anchor="1"'
            elif re.match(r"^https?://", href):
                attr = f' href="{html.escape(href, quote=True)}" target="_blank" rel="noopener noreferrer"'
            else:
                self.stack.append(None)        # an edit, file or special-page link: keep the words only
                return
        elif tag == "img":
            src = a.get("src") or ""
            if src.startswith("//"):
                src = "https:" + src
            if not re.match(r"^https://(upload|thumb)\.wikimedia\.org/", src):
                return
            w, h = a.get("width") or "", a.get("height") or ""
            attr = (f' src="{html.escape(src, quote=True)}" alt="{html.escape(a.get("alt") or "", quote=True)}" loading="lazy"'
                    + (f' width="{w}"' if w.isdigit() else "") + (f' height="{h}"' if h.isdigit() else ""))
        elif tag in ("th", "td"):
            for k in ("colspan", "rowspan"):
                if (a.get(k) or "").isdigit():
                    attr += f' {k}="{a[k]}"'
        elif tag == "table" and "infobox" in (a.get("class") or ""):
            attr = ' class="infobox"'
        elif tag in ("h2", "h3") :
            hid = a.get("id") or ""
            if hid:
                attr = f' id="w-{html.escape(hid, quote=True)}"'
            if tag == "h2":
                self.h2 = {"id": "w-" + hid if hid else "", "start": len(self.out), "text": ""}
        self.out.append(f"<{tag}{attr}>")
        if tag not in self.VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if self.done or tag in self.VOID:
            return
        if self.skip:
            self.skip -= 1
            return
        if not self.stack:
            return
        t = self.stack.pop()
        if t:
            self.out.append(f"</{t}>")
            if t == "h2" and self.h2 is not None:
                name = self.h2["text"].strip()
                if name.lower() in self.END:
                    del self.out[self.h2["start"]:]
                    self.done = True
                else:
                    self.toc.append({"id": self.h2["id"], "title": name})
                self.h2 = None

    def handle_data(self, data):
        if self.done or self.skip:
            return
        if self.h2 is not None:
            self.h2["text"] += data
        self.out.append(html.escape(data, quote=False))


def wiki_page(title: str, lang: str = "en") -> dict:
    title = (title or "").strip()[:300]
    lang = lang if lang in _WIKI_LANGS else "en"
    if not title:
        return {"error": "No article named."}
    key = f"p:{lang}:{title}"
    hit = _wiki_cache.get(key)
    if hit and time.time() - hit[0] < 3600:
        return hit[1]
    try:
        d = _get_json(f"https://{lang}.wikipedia.org/w/api.php?" + urllib.parse.urlencode({
            "action": "parse", "page": title, "prop": "text|displaytitle|description", "redirects": 1,
            "format": "json", "formatversion": 2, "disableeditsection": 1, "disabletoc": 1}), timeout=12)
    except Exception as e:                     # noqa: BLE001
        return {"error": f"Wikipedia did not answer ({type(e).__name__})"}
    if "error" in d:
        return {"error": (d["error"].get("info") or "No such article.")}
    p = d.get("parse") or {}
    c = _WikiClean(lang)
    try:
        c.feed(p.get("text") or "")
        c.close()
    except Exception:                          # noqa: BLE001 — a page we cannot parse is said so
        return {"error": "This article could not be read."}
    body = "".join(c.out)
    out = {"title": _strip_tags(p.get("displaytitle") or p.get("title") or title), "key": p.get("title"),
           "description": p.get("description") or "", "html": body, "toc": c.toc[:40], "lang": lang,
           "url": f"https://{lang}.wikipedia.org/wiki/{urllib.parse.quote((p.get('title') or title).replace(' ', '_'))}",
           "license": "Text from Wikipedia, CC BY-SA 4.0"}
    _wiki_cache[key] = (time.time(), out)
    return out


class _Reader(HTMLParser):
    """A page as text: its title, its headings and paragraphs, nothing else."""
    SKIP = {"script", "style", "noscript", "svg", "nav", "header", "footer", "aside", "form", "button",
            "select", "template", "iframe", "figure"}
    BLOCK = {"h1": "h", "h2": "h", "h3": "h", "p": "p", "li": "li", "blockquote": "q"}

    def __init__(self, strict: bool = True):
        super().__init__(convert_charrefs=True)
        self.strict = strict
        self.skip, self.cur, self.blocks, self.meta, self.title = 0, None, [], {}, ""
        self.in_title, self.article, self.art_blocks = False, 0, []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "meta":
            k = (a.get("property") or a.get("name") or "").lower()
            if k in ("og:title", "og:site_name", "og:image", "og:description", "article:published_time", "description"):
                self.meta.setdefault(k, a.get("content") or "")
            return
        if tag == "title":
            self.in_title = True
            return
        if self.skip:
            if tag not in ("img", "br", "input", "meta", "link"):
                self.skip += 1
            return
        if tag in self.SKIP or self.strict and re.search(r"\b(share|social|newsletter|related|advert|promo|comment|cookie|subscribe)\b",
                                                         (a.get("class") or "") + " " + (a.get("id") or ""), re.I):
            self.skip = 1
            return
        if tag == "article" or (a.get("itemprop") == "articleBody"):
            self.article += 1
        if tag in self.BLOCK:
            self.cur = {"t": self.BLOCK[tag], "text": ""}

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False
            return
        if self.skip:
            if tag not in ("img", "br", "input", "meta", "link"):
                self.skip -= 1
            return
        if tag in self.BLOCK and self.cur is not None:
            t = re.sub(r"\s+", " ", self.cur["text"]).strip()
            if t and (self.cur["t"] != "p" or len(t) >= 40) and (self.cur["t"] != "li" or len(t) >= 30):
                (self.art_blocks if self.article else self.blocks).append({"t": self.cur["t"], "text": t[:4000]})
            self.cur = None
        if tag == "article" and self.article:
            self.article -= 1

    def handle_data(self, data):
        if self.in_title:
            self.title += data
        elif not self.skip and self.cur is not None:
            self.cur["text"] += data


def reader(url: str) -> dict:
    url = (url or "").strip()
    if not re.match(r"^[a-z]+://", url, re.I):
        url = "https://" + url
    hit = _reader_cache.get(url)
    if hit and time.time() - hit[0] < 900:
        return hit[1]
    opener = urllib.request.build_opener(_NoRedirect, urllib.request.HTTPSHandler(context=_ssl_ctx()))
    cur = url
    for _ in range(5):
        bad = _check_url(cur)
        if bad:
            return {"url": url, "error": bad}
        req = urllib.request.Request(cur, headers={"User-Agent": _UA, "Accept": "text/html", "Accept-Language": "en-IN,en"})
        try:
            resp = opener.open(req, timeout=10)
        except urllib.error.HTTPError as e:
            if e.code in (301, 302, 303, 307, 308) and e.headers.get("Location"):
                cur = urllib.parse.urljoin(cur, e.headers["Location"])
                continue
            return {"url": url, "error": f"The site answered {e.code}."}
        except Exception as e:                 # noqa: BLE001
            return {"url": url, "error": f"The site did not answer ({type(e).__name__})."}
        if resp.status in (301, 302, 303, 307, 308) and resp.headers.get("Location"):
            cur = urllib.parse.urljoin(cur, resp.headers["Location"])
            resp.close()
            continue
        ctype = resp.headers.get("Content-Type") or ""
        if "html" not in ctype:
            resp.close()
            return {"url": url, "final": cur, "error": "That address is not a web page."}
        raw = resp.read(3_000_000)
        resp.close()
        cs = resp.headers.get_content_charset() or "utf-8"
        break
    else:
        return {"url": url, "error": "Too many redirects."}
    doc = raw.decode(cs, "replace")

    def read(strict: bool) -> tuple[_Reader, list]:
        r_ = _Reader(strict)
        try:
            r_.feed(doc)
            r_.close()
        except Exception:                      # noqa: BLE001
            pass
        b_ = r_.art_blocks if sum(len(b["text"]) for b in r_.art_blocks) > 400 else r_.blocks + r_.art_blocks
        return r_, b_

    # first pass drops share bars, promos and comments by their class names;
    # a site that wraps its whole page in such a class gets a second pass
    r, blocks = read(True)
    if sum(len(b["text"]) for b in blocks) < 500:
        r2, b2 = read(False)
        if sum(len(b["text"]) for b in b2) > sum(len(b["text"]) for b in blocks):
            r, blocks = r2, b2
    seen: set[str] = set()
    blocks = [b for b in blocks if not (b["text"] in seen or seen.add(b["text"]))]
    img = r.meta.get("og:image") or ""
    out = {"url": url, "final": cur, "title": html.unescape((r.meta.get("og:title") or r.title).strip())[:300],
           "site": r.meta.get("og:site_name") or urllib.parse.urlsplit(cur).hostname,
           "published": r.meta.get("article:published_time") or "",
           "image": img if img.startswith("https://") else "",
           "description": r.meta.get("og:description") or r.meta.get("description") or "",
           "blocks": blocks[:400]}
    if sum(len(b["text"]) for b in blocks) < 500:
        # the site writes its text with scripts after the page loads
        out["thin"] = True
    if not blocks and not out["description"]:
        out["error"] = "No readable text on that page; it may need a full browser."
    _reader_cache[url] = (time.time(), out)
    return out


if __name__ == "__main__":
    import json
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "frame":
        print(json.dumps(frame_check(sys.argv[2]), indent=1))
    else:
        d = news()
        print(json.dumps({"n": len(d["items"]), "sources": d["sources"], "first": d["items"][:2]}, indent=1))
