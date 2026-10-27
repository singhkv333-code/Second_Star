"""Social sources for plays: public posts read the way play_events reads
filings and headlines.

The model names a source by ADDRESS, never by a URL it builds: a fixed
reader turns each address into the one public endpoint that serves it, and
every post comes back in the same item shape play_events stores, matches and
judges. What can be read without a paid key is connected; what cannot is
refused with the reason and the nearest thing that can.

    reddit:r/IndianStockMarket   a subreddit's new posts
    reddit:u/<user>              one user's posts
    reddit:search:<words>        Reddit-wide search, newest first
    telegram:<channel>           a public channel (t.me/s/<channel>)
    youtube:@<handle> | youtube:<UC channel id>
    bsky:<handle>                a Bluesky account
    mastodon:<user>@<instance>   a Mastodon account
    gnews:<words>                Google News for the words (India edition)
    rss:<https url>              any RSS or Atom feed

A pasted link (t.me/..., reddit.com/r/..., youtube.com/@..., bsky.app/
profile/...) is read as its address. A post is untrusted: the judge reads it
as quoted data, and plays.py never lets a post alone place an order.
"""
from __future__ import annotations

import hashlib
import html
import ipaddress
import json
import re
import socket
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129 Safari/537.36"
KINDS = {"reddit": "Reddit", "telegram": "Telegram", "youtube": "YouTube", "bsky": "Bluesky",
         "mastodon": "Mastodon", "gnews": "Google News", "rss": "Feed"}
# what cannot be read, and the nearest thing that can
UNCONNECTED = {
    "x": "X needs a paid API key Pivot does not have",
    "twitter": "X needs a paid API key Pivot does not have",
    "stocktwits": "StockTwits blocks automated readers",
    "truth": "Truth Social blocks automated readers",
    "instagram": "Instagram has no public read access",
    "facebook": "Facebook has no public read access",
    "threads": "Threads has no public read access",
    "linkedin": "LinkedIn has no public read access",
    "whatsapp": "WhatsApp channels have no public read access",
}
NEAREST = ("the nearest real thing: the same people's Telegram, YouTube or Bluesky accounts, or "
           "gnews:<words> for the story")
# seconds between reads of one address, and between any two requests to a host
EVERY = {"reddit": 180, "telegram": 60, "youtube": 600, "bsky": 120, "mastodon": 120, "gnews": 300, "rss": 300}
HOST_GAP = {"www.reddit.com": 7.0}
MAX_BYTES = 3_000_000


class Bad(ValueError):
    pass


# ══ addresses ═══════════════════════════════════════════════════════════════

def parse(raw: str) -> dict:
    """{kind, target, address, label} for one source, or Bad saying why not."""
    s = str(raw or "").strip()
    if not s:
        raise Bad("an empty source")
    low = s.lower()
    if low.startswith(("http://", "https://", "t.me/", "www.", "reddit.com", "youtube.com", "bsky.app",
                       "x.com", "twitter.com")):
        return _from_url(s)
    kind, _, rest = s.partition(":")
    kind = kind.lower().lstrip("@").strip()
    rest = rest.strip()
    if kind in UNCONNECTED:
        raise Bad(f"{kind}: {UNCONNECTED[kind]}; {NEAREST}")
    if kind not in KINDS or not rest:
        raise Bad(f"'{s}' is not a source address; use one of reddit:r/<sub>, reddit:u/<user>, "
                  "reddit:search:<words>, telegram:<channel>, youtube:@<handle>, bsky:<handle>, "
                  "mastodon:<user>@<instance>, gnews:<words>, rss:<https url>, or paste the link")
    if kind == "reddit":
        m = re.match(r"^(r|u|user|search)[/:](.+)$", rest, re.I)
        if not m:
            raise Bad("reddit takes r/<subreddit>, u/<user> or search:<words>")
        what, name = m.group(1).lower(), m.group(2).strip()
        if what == "search":
            q = " ".join(name.lower().split())[:120]
            return _addr("reddit", f"search:{q}", f"Reddit search “{q}”")
        if not re.match(r"^[A-Za-z0-9_\-]{2,40}$", name):
            raise Bad(f"'{name}' is not a Reddit name")
        what = "u" if what in ("u", "user") else "r"
        return _addr("reddit", f"{what}/{name.lower()}", f"Reddit {what}/{name}")
    if kind == "telegram":
        name = rest.lstrip("@").split("/")[-1]
        if not re.match(r"^[A-Za-z][A-Za-z0-9_]{3,40}$", name):
            raise Bad(f"'{rest}' is not a public Telegram channel name")
        return _addr("telegram", name.lower(), f"Telegram @{name}")
    if kind == "youtube":
        if re.match(r"^UC[\w-]{22}$", rest):
            return _addr("youtube", rest, f"YouTube {rest}")
        name = rest.lstrip("@")
        if not re.match(r"^[A-Za-z0-9_.\-]{3,40}$", name):
            raise Bad(f"'{rest}' is not a YouTube handle or channel id")
        return _addr("youtube", "@" + name.lower(), f"YouTube @{name}")
    if kind == "bsky":
        h = rest.lstrip("@").lower()
        h = h if "." in h else h + ".bsky.social"
        if not re.match(r"^[a-z0-9.\-]{3,80}$", h):
            raise Bad(f"'{rest}' is not a Bluesky handle")
        return _addr("bsky", h, f"Bluesky @{h}")
    if kind == "mastodon":
        m = re.match(r"^@?([A-Za-z0-9_]{1,40})@([a-z0-9.\-]+\.[a-z]{2,})$", rest, re.I)
        if not m:
            raise Bad("mastodon takes <user>@<instance>, e.g. Gargron@mastodon.social")
        return _addr("mastodon", f"{m.group(1).lower()}@{m.group(2).lower()}", f"Mastodon @{m.group(1)}")
    if kind == "gnews":
        q = " ".join(rest.lower().split())[:120]
        return _addr("gnews", q, f"Google News “{q}”")
    url = rest if rest.startswith(("http://", "https://")) else "https://" + rest
    _public_url(url)
    return _addr("rss", url, f"Feed {urllib.parse.urlparse(url).netloc}")


def _addr(kind: str, target: str, label: str) -> dict:
    return {"kind": kind, "target": target, "address": f"{kind}:{target}", "label": label}


def _from_url(s: str) -> dict:
    url = s if s.startswith(("http://", "https://")) else "https://" + s
    u = urllib.parse.urlparse(url)
    host, path = u.netloc.lower().removeprefix("www.").removeprefix("m.").removeprefix("old."), u.path.strip("/")
    if host in ("x.com", "twitter.com", "mobile.twitter.com"):
        return parse("x:" + path)
    for k in ("instagram", "facebook", "threads", "linkedin", "whatsapp", "stocktwits", "truthsocial"):
        if host.startswith(k + ".") or host.endswith("." + k + ".com") or host == k + ".com" or host == k + ".net":
            return parse(("truth" if k == "truthsocial" else k) + ":" + path)
    if host in ("t.me", "telegram.me"):
        return parse("telegram:" + path.removeprefix("s/").split("/")[0])
    if host == "reddit.com":
        bits = path.split("/")
        if len(bits) >= 2 and bits[0] in ("r", "u", "user"):
            return parse(f"reddit:{bits[0]}/{bits[1]}")
    if host == "youtube.com":
        if path.startswith("@"):
            return parse("youtube:" + path.split("/")[0])
        if path.startswith("channel/"):
            return parse("youtube:" + path.split("/")[1])
    if host == "bsky.app" and path.startswith("profile/"):
        return parse("bsky:" + path.split("/")[1])
    return parse("rss:" + url)


def _public_url(url: str) -> None:
    """A feed URL the model wrote must point at the public internet: never a
    private, loopback or link-local address (the server's own network)."""
    u = urllib.parse.urlparse(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise Bad("a feed must be an http(s) URL")
    try:
        infos = socket.getaddrinfo(u.hostname, None)
    except socket.gaierror:
        raise Bad(f"{u.hostname} does not resolve")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise Bad("a feed must be on the public internet")


# ══ fetching ════════════════════════════════════════════════════════════════

_host_lock = threading.Lock()
_host_last: dict[str, float] = {}
_backoff: dict[str, float] = {}       # address -> until when it is left alone
_yt_ids: dict[str, str] = {}


def _get(url: str, *, accept: str = "*/*") -> bytes:
    host = urllib.parse.urlparse(url).netloc.lower()
    gap = HOST_GAP.get(host, 1.0)
    with _host_lock:
        wait = _host_last.get(host, 0) + gap - time.time()
        if wait > 0:
            time.sleep(wait)
        _host_last[host] = time.time()
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": accept})
    import ssl
    try:
        import certifi
        ctx = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=20, context=ctx) as r:
        return r.read(MAX_BYTES)


def _epoch(s: str) -> int | None:
    s = (s or "").strip()
    if not s:
        return None
    try:
        return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())
    except ValueError:
        pass
    from email.utils import parsedate_to_datetime
    try:
        return int(parsedate_to_datetime(s).timestamp())
    except (TypeError, ValueError):
        return None


def _text(h: str) -> str:
    h = re.sub(r"<br\s*/?>|</p>", "\n", h or "", flags=re.I)
    return re.sub(r"[ \t]+", " ", html.unescape(re.sub(r"<[^>]+>", "", h))).strip()


def _item(addr: dict, key: str, ts: int | None, who: str, text: str, url: str, title: str = "") -> dict | None:
    if not ts:
        return None
    text = text.strip()
    title = (title or text.split("\n")[0])[:300].strip()
    if not title:
        return None
    return {"source": addr["address"], "key": f"{addr['kind']}:" + hashlib.sha1(key.encode()).hexdigest()[:20],
            "ts": int(ts), "symbol": "", "who": who[:120], "title": title,
            "body": text[:2000] if text != title else "", "url": url[:500]}


def _feed(addr: dict, url: str, who_default: str) -> list[dict]:
    """RSS 2.0 or Atom, namespaces ignored."""
    root = ET.fromstring(_get(url, accept="application/rss+xml, application/atom+xml, application/xml"))
    for el in root.iter():
        if isinstance(el.tag, str) and "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]
    out = []
    for e in list(root.iter("item")) + list(root.iter("entry")):
        link = e.findtext("link") or ""
        if not link:
            le = e.find("link")
            link = le.get("href", "") if le is not None else ""
        body = e.findtext("content") or e.findtext("description") or e.findtext("summary") or ""
        mg = e.find("group")
        if not body and mg is not None:
            body = mg.findtext("description") or ""
        who = (e.findtext("author/name") or e.findtext("author") or e.findtext("source") or who_default)
        ts = _epoch(e.findtext("published") or e.findtext("pubDate") or e.findtext("updated") or "")
        it = _item(addr, e.findtext("id") or e.findtext("guid") or link, ts, who.strip() or who_default,
                   _text(body), link, title=_text(e.findtext("title") or ""))
        if it:
            out.append(it)
    return out


def _telegram(addr: dict) -> list[dict]:
    try:
        page = _get(f"https://t.me/s/{addr['target']}").decode("utf-8", "replace")
    except OSError:
        # some networks reset t.me; the same page is served at telegram.me
        page = _get(f"https://telegram.me/s/{addr['target']}").decode("utf-8", "replace")
    out = []
    for block in page.split('class="tgme_widget_message_wrap')[1:]:
        post = re.search(r'data-post="([^"]+)"', block)
        when = re.search(r'<time[^>]*datetime="([^"]+)"', block)
        body = re.search(r'class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', block, re.S)
        if not (post and when and body):
            continue
        it = _item(addr, post.group(1), _epoch(when.group(1)), "@" + addr["target"], _text(body.group(1)),
                   f"https://t.me/{post.group(1)}")
        if it:
            out.append(it)
    return out


def _youtube(addr: dict) -> list[dict]:
    cid = addr["target"]
    if cid.startswith("@"):
        cid = _yt_ids.get(cid) or ""
        if not cid:
            page = _get(f"https://www.youtube.com/{addr['target']}").decode("utf-8", "replace")
            m = re.search(r'"(?:channelId|externalId)":"(UC[\w-]{22})"', page) or \
                re.search(r'channel/(UC[\w-]{22})', page)
            if not m:
                raise Bad(f"no YouTube channel {addr['target']}")
            cid = _yt_ids[addr["target"]] = m.group(1)
    return _feed(addr, f"https://www.youtube.com/feeds/videos.xml?channel_id={cid}", addr["label"])


def _bsky(addr: dict) -> list[dict]:
    got = json.loads(_get("https://public.api.bsky.app/xrpc/app.bsky.feed.getAuthorFeed?"
                          + urllib.parse.urlencode({"actor": addr["target"], "limit": 40})))
    out = []
    for f in got.get("feed") or []:
        p = f.get("post") or {}
        rec = p.get("record") or {}
        uri = p.get("uri") or ""
        rkey = uri.rsplit("/", 1)[-1]
        it = _item(addr, uri, _epoch(rec.get("createdAt") or ""), "@" + addr["target"], rec.get("text") or "",
                   f"https://bsky.app/profile/{addr['target']}/post/{rkey}")
        if it:
            out.append(it)
    return out


def _mastodon(addr: dict) -> list[dict]:
    user, inst = addr["target"].split("@", 1)
    acct = json.loads(_get(f"https://{inst}/api/v1/accounts/lookup?acct={urllib.parse.quote(user)}"))
    rows = json.loads(_get(f"https://{inst}/api/v1/accounts/{acct['id']}/statuses?limit=40&exclude_replies=true"))
    out = []
    for r in rows:
        it = _item(addr, r.get("uri") or r.get("id"), _epoch(r.get("created_at") or ""), "@" + addr["target"],
                   _text(r.get("content") or ""), r.get("url") or "")
        if it:
            out.append(it)
    return out


def fetch(address: str) -> list[dict]:
    """The latest public posts at one address, newest last. Raises Bad on a
    source that cannot be read; a network failure raises the error itself."""
    a = parse(address)
    k, t = a["kind"], a["target"]
    if k == "reddit":
        if t.startswith("search:"):
            url = "https://www.reddit.com/search.rss?" + urllib.parse.urlencode({"q": t[7:], "sort": "new"})
        elif t.startswith("u/"):
            url = f"https://www.reddit.com/user/{t[2:]}/submitted/.rss"
        else:
            url = f"https://www.reddit.com/{t}/new/.rss"
        items = _feed(a, url, a["label"])
    elif k == "telegram":
        items = _telegram(a)
    elif k == "youtube":
        items = _youtube(a)
    elif k == "bsky":
        items = _bsky(a)
    elif k == "mastodon":
        items = _mastodon(a)
    elif k == "gnews":
        items = _feed(a, "https://news.google.com/rss/search?" + urllib.parse.urlencode(
            {"q": t, "hl": "en-IN", "gl": "IN", "ceid": "IN:en"}), "Google News")
    else:
        _public_url(t)
        items = _feed(a, t, a["label"])
    return sorted(items, key=lambda x: x["ts"])


# ══ polling, for play_events' loop ══════════════════════════════════════════

_last: dict[str, float] = {}
_primed: set[str] = set()
STATS = {"reads": 0, "errors": 0, "last_error": ""}


def prime(address: str) -> None:
    """A read made elsewhere (a compile's backfill) is this address's
    baseline: the poller's next read fires only what came after it."""
    _primed.add(address)
    _last[address] = time.time()


def poll(addresses, store) -> list[dict]:
    """Read each address that is due and return the posts not seen before.
    The first read of an address is its baseline: those posts are stored for
    history and never fire."""
    now, fresh = time.time(), []
    for address in sorted(set(addresses)):
        try:
            kind = parse(address)["kind"]
        except Bad:
            continue
        if now - _last.get(address, 0) < EVERY.get(kind, 300) or _backoff.get(address, 0) > now:
            continue
        _last[address] = now
        try:
            new = store(fetch(address))
            STATS["reads"] += 1
        except Exception as exc:                            # noqa: BLE001
            STATS["errors"] += 1
            STATS["last_error"] = f"{address}: {exc}"[:200]
            # a refusal or a rate limit leaves the address alone for a while
            _backoff[address] = now + (900 if "429" in str(exc) else 300)
            continue
        if address in _primed:
            fresh.extend(new)
        else:
            _primed.add(address)
    return fresh
