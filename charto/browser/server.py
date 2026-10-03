"""The remote browser behind the Browser widget.

A real Chromium runs here, one private (incognito) context per widget, and the
widget drives it over one WebSocket:

  server → widget   binary   a JPEG of the viewport (CDP Page.screencastFrame)
                    text     JSON: hello, state (url, title, back/forward,
                             loading), cursor, pdf, clip, notice, busy, bye
  widget → server   text     JSON: nav, back, fwd, reload, stop, size,
                             mouse, wheel, key, text (paste), copy

So every site works as it does in a desktop browser — scripts run, logins and
cookies work for the life of the session — and nothing from the site runs in
our page: the widget only ever paints pictures and sends input.

Isolation, from the inside out:
  · each widget gets its own context (no shared cookies or storage), closed
    after 90 s without a widget attached, 15 min without input, or 2 h;
  · all of Chromium's traffic goes through egress.py, which refuses private,
    loopback, link-local and metadata addresses and connects to the exact
    address it checked;
  · no downloads (a PDF is handed to the Documents widget instead), no
    service workers, no permission prompts;
  · the whole service runs in its own container (Dockerfile), as an
    unprivileged user, apart from the chart and the user data.

Who may connect: the dataserver issues a one-minute, single-use HMAC ticket
(/browser/ticket, signed-in users) and this service checks it with the shared
CHARTO_BROWSER_SECRET.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
import urllib.parse

from playwright.async_api import async_playwright
from websockets.asyncio.server import serve
from websockets.datastructures import Headers
from websockets.http11 import Response

import egress

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
log = logging.getLogger("rb")

SECRET = os.environ.get("CHARTO_BROWSER_SECRET", "")
PORT = int(os.environ.get("RB_PORT", "5177"))
PROXY_PORT = int(os.environ.get("RB_PROXY_PORT", "8899"))
MAX_SESSIONS = int(os.environ.get("RB_MAX_SESSIONS", "4"))
PER_USER = int(os.environ.get("RB_PER_USER", "2"))
GRACE, IDLE, LIFETIME = 90, 15 * 60, 2 * 3600

_used: dict[str, float] = {}
sessions: dict[str, "Session"] = {}
_pw = None
_browser = None
_ua = ""


def check_ticket(t: str) -> str | None:
    if not SECRET or "." not in (t or ""):
        return None
    raw, sig = t.split(".", 1)
    want = base64.urlsafe_b64encode(hmac.new(SECRET.encode(), raw.encode(), hashlib.sha256)
                                    .digest()).decode().rstrip("=")
    if not hmac.compare_digest(sig, want):
        return None
    try:
        p = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
    except ValueError:
        return None
    now = time.time()
    for n, exp in list(_used.items()):
        if exp < now:
            _used.pop(n, None)
    if p.get("exp", 0) < now or p.get("n") in _used:
        return None
    _used[p["n"]] = p["exp"] + 5
    return str(p.get("u") or "")


async def launch() -> None:
    global _pw, _browser, _ua
    if _pw is None:
        _pw = await async_playwright().start()
    _browser = await _pw.chromium.launch(
        headless=True,
        args=[
            f"--proxy-server=http://127.0.0.1:{PROXY_PORT}",
            "--proxy-bypass-list=<-loopback>",   # loopback goes through the proxy too, and is refused
            "--disable-quic",
            "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
            "--webrtc-ip-handling-policy=disable_non_proxied_udp",
            "--disable-dev-shm-usage",
            "--disable-background-networking",
            "--disable-sync",
            "--disable-extensions",
            "--no-first-run",
            "--mute-audio",
            "--renderer-process-limit=8",
        ])
    major = _browser.version.split(".")[0]
    _ua = (f"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
           f"Chrome/{major}.0.0.0 Safari/537.36")
    _browser.on("disconnected", lambda *_: asyncio.ensure_future(_crashed()))
    log.info("chromium %s up", _browser.version)


async def _crashed() -> None:
    log.warning("chromium went away; closing sessions and relaunching")
    for s in list(sessions.values()):
        await s.close("crash")
    await launch()


def _norm(url: str) -> str | None:
    url = (url or "").strip()
    if not url:
        return None
    if not url.lower().startswith(("http://", "https://")):
        url = "https://" + url
    u = urllib.parse.urlsplit(url)
    return url if u.scheme in ("http", "https") and u.hostname else None


_KEY_DOWN = {"down": "mousePressed", "up": "mouseReleased", "move": "mouseMoved"}
_BUTTON = ["left", "middle", "right"]
_CURSOR_JS = """([x, y]) => {
  const e = document.elementFromPoint(x, y); if (!e) return "default";
  let c = getComputedStyle(e).cursor;
  if (c === "auto") {
    if (e.closest("a[href],button,[role=button],summary,label,select")) c = "pointer";
    else if (e.closest("input:not([type=button]):not([type=submit]),textarea,[contenteditable=''],[contenteditable=true]")) c = "text";
    else c = "default";
  }
  return c;
}"""


class Session:
    def __init__(self, uid: str):
        self.sid = secrets.token_urlsafe(12)
        self.uid = uid
        self.ws = None
        self.created = self.last_input = time.time()
        self.detached = 0.0
        self.loading = False
        self.size = (1024, 700)
        self.dpr = 1.0
        self.cursor = ""
        self._cursor_busy = False
        self._last_cursor = 0.0
        self._closed = False
        self._target = ""

    async def start(self, w: int, h: int, dpr: float) -> None:
        self.size, self.dpr = (w, h), dpr
        self.ctx = await _browser.new_context(
            viewport={"width": w, "height": h}, device_scale_factor=dpr, user_agent=_ua,
            locale="en-IN", timezone_id="Asia/Kolkata", accept_downloads=True,
            service_workers="block")
        self.ctx.set_default_navigation_timeout(30000)
        self.page = await self.ctx.new_page()
        self.ctx.on("page", self._on_popup)
        self.cdp = await self.ctx.new_cdp_session(self.page)
        self.cdp.on("Page.screencastFrame", lambda p: asyncio.ensure_future(self._frame(p)))
        p = self.page
        p.on("framenavigated", lambda f: f == p.main_frame and asyncio.ensure_future(self.state()))
        p.on("request", self._on_request)
        p.on("response", self._on_response)
        p.on("load", lambda *_: self._loaded())
        p.on("domcontentloaded", lambda *_: asyncio.ensure_future(self.state()))
        p.on("download", lambda d: asyncio.ensure_future(self._download(d)))
        p.on("dialog", lambda d: asyncio.ensure_future(self._dialog(d)))
        p.on("crash", lambda *_: asyncio.ensure_future(self.close("crash")))

    # ── output ────────────────────────────────────────────────────────────
    async def send(self, obj) -> None:
        if self.ws is None:
            return
        try:
            await self.ws.send(obj if isinstance(obj, bytes) else json.dumps(obj))
        except Exception:  # noqa: BLE001 — a dropped socket is handled by the reader
            pass

    async def _frame(self, p) -> None:
        try:
            await self.send(base64.b64decode(p["data"]))
        finally:
            try:
                await self.cdp.send("Page.screencastFrameAck", {"sessionId": p["sessionId"]})
            except Exception:  # noqa: BLE001
                pass

    async def cast(self, on: bool = True) -> None:
        try:
            await self.cdp.send("Page.stopScreencast")
            if on:
                w, h = self.size
                await self.cdp.send("Page.startScreencast", {
                    "format": "jpeg", "quality": 72, "everyNthFrame": 1,
                    "maxWidth": int(w * self.dpr), "maxHeight": int(h * self.dpr)})
        except Exception:  # noqa: BLE001
            pass

    async def state(self) -> None:
        try:
            h = await self.cdp.send("Page.getNavigationHistory")
            i, n = h["currentIndex"], len(h["entries"])
        except Exception:  # noqa: BLE001
            i, n = 0, 1
        try:
            title = await self.page.title()
        except Exception:  # noqa: BLE001
            title = ""
        await self.send({"t": "state", "url": self.page.url, "title": title,
                         "back": i > 0, "fwd": i < n - 1, "loading": self.loading})

    def _on_request(self, req) -> None:
        if req.is_navigation_request() and req.frame == self.page.main_frame:
            self.loading = True
            asyncio.ensure_future(self.state())

    def _on_response(self, r) -> None:
        # a site that turns automated browsers away answers the page itself
        # with 403/429; say so rather than show a bare "Access Denied"
        if r.request.is_navigation_request() and r.frame == self.page.main_frame and r.status in (401, 403, 429, 503):
            host = urllib.parse.urlsplit(r.url).hostname or ""
            if r.status == 403 and host in egress.refused:
                asyncio.ensure_future(self.send({"t": "notice", "msg": "That address is not a public website, so it cannot be opened."}))
            else:
                asyncio.ensure_future(self.send({"t": "refused", "code": r.status, "url": r.url}))

    def _loaded(self) -> None:
        self.loading = False
        asyncio.ensure_future(self.state())

    async def _download(self, d) -> None:
        url, name = d.url, d.suggested_filename or ""
        try:
            await d.cancel()
        except Exception:  # noqa: BLE001
            pass
        if name.lower().endswith(".pdf") or urllib.parse.urlsplit(url).path.lower().endswith(".pdf"):
            await self.send({"t": "pdf", "url": url, "name": name})
        else:
            await self.send({"t": "notice", "msg": "Downloads are not available in the live browser."})
        self.loading = False
        await self.state()

    async def _dialog(self, d) -> None:
        kind, msg = d.type, (d.message or "")[:300]
        try:
            await (d.accept() if kind in ("alert", "beforeunload") else d.dismiss())
        except Exception:  # noqa: BLE001
            pass
        if kind != "beforeunload" and msg:
            await self.send({"t": "notice", "msg": f"The page said: {msg}"})

    def _on_popup(self, pg) -> None:
        if pg is self.page:
            return

        async def adopt():
            # one page per widget: a new window becomes a navigation here
            try:
                await pg.wait_for_load_state("commit", timeout=10000)
            except Exception:  # noqa: BLE001
                pass
            url = pg.url
            try:
                await pg.close()
            except Exception:  # noqa: BLE001
                pass
            if url and url != "about:blank":
                await self.nav(url)
        asyncio.ensure_future(adopt())

    # ── input ─────────────────────────────────────────────────────────────
    async def nav(self, url: str) -> None:
        u = _norm(url)
        if not u:
            return await self.send({"t": "notice", "msg": "Only web addresses (http or https) can be opened."})
        self._target = u
        log.info("nav uid=%s host=%s", self.uid, urllib.parse.urlsplit(u).hostname)
        await self._go(self.page.goto(u, wait_until="commit"))

    async def _go(self, coro) -> None:
        try:
            await coro
        except Exception as e:  # noqa: BLE001
            m = str(e).split("\n")[0]
            host = urllib.parse.urlsplit(self._target).hostname or ""
            if "ERR_TUNNEL_CONNECTION_FAILED" in m and host in egress.refused:
                await self.send({"t": "notice", "msg": "That address is not a public website, so it cannot be opened."})
            elif "ERR_HTTP2_PROTOCOL_ERROR" in m or "ERR_CONNECTION_RESET" in m:
                await self.send({"t": "refused", "code": 0, "url": self._target})
            elif "Download is starting" not in m and "ERR_ABORTED" not in m and "interrupted by another" not in m:
                await self.send({"t": "notice", "msg": "The page did not load (" + (m.split("net::")[-1].split(" ")[0] if "net::" in m else "timeout") + ")."})
            self.loading = False
        await self.state()

    async def handle(self, m: dict) -> None:
        t = m.get("t")
        self.last_input = time.time()
        c = self.cdp
        if t == "mouse":
            x, y = float(m.get("x", 0)), float(m.get("y", 0))
            e = m.get("e")
            b = int(m.get("b", 0))
            await c.send("Input.dispatchMouseEvent", {
                "type": _KEY_DOWN.get(e, "mouseMoved"), "x": x, "y": y,
                "button": "none" if e == "move" and not m.get("bs") else _BUTTON[b if 0 <= b < 3 else 0],
                "buttons": int(m.get("bs", 0)), "clickCount": int(m.get("n", 1 if e != "move" else 0)),
                "modifiers": int(m.get("m", 0))})
            if e == "move":
                self._track_cursor(x, y)
        elif t == "wheel":
            await c.send("Input.dispatchMouseEvent", {
                "type": "mouseWheel", "x": float(m.get("x", 0)), "y": float(m.get("y", 0)),
                "deltaX": float(m.get("dx", 0)), "deltaY": float(m.get("dy", 0)),
                "modifiers": int(m.get("m", 0))})
        elif t == "key":
            text = str(m.get("text") or "")[:4]
            down = m.get("e") == "down"
            p = {"type": ("keyDown" if text else "rawKeyDown") if down else "keyUp",
                 "modifiers": int(m.get("m", 0)), "key": str(m.get("key") or "")[:32],
                 "code": str(m.get("code") or "")[:32], "windowsVirtualKeyCode": int(m.get("kc", 0)),
                 "nativeVirtualKeyCode": int(m.get("kc", 0)), "autoRepeat": bool(m.get("rep")),
                 "location": int(m.get("loc", 0))}
            if text and down:
                p["text"] = p["unmodifiedText"] = text
            if down and (p["modifiers"] & 2) and p["key"].lower() == "a":
                p["commands"] = ["selectAll"]
            await c.send("Input.dispatchKeyEvent", p)
        elif t == "text":
            await c.send("Input.insertText", {"text": str(m.get("s") or "")[:20000]})
        elif t == "copy":
            try:
                s = await self.page.evaluate("() => String(window.getSelection() || '')")
            except Exception:  # noqa: BLE001
                s = ""
            if s:
                await self.send({"t": "clip", "s": s[:100000]})
        elif t == "nav":
            asyncio.ensure_future(self.nav(str(m.get("url") or "")))
        elif t == "back":
            asyncio.ensure_future(self._go(self.page.go_back(wait_until="commit")))
        elif t == "fwd":
            asyncio.ensure_future(self._go(self.page.go_forward(wait_until="commit")))
        elif t == "reload":
            asyncio.ensure_future(self._go(self.page.reload(wait_until="commit")))
        elif t == "stop":
            await c.send("Page.stopLoading")
            self.loading = False
            await self.state()
        elif t == "size":
            w, h = max(200, min(int(m.get("w", 1024)), 2560)), max(150, min(int(m.get("h", 700)), 1600))
            if (w, h) != self.size:
                self.size = (w, h)
                await self.page.set_viewport_size({"width": w, "height": h})
                await self.cast()

    def _track_cursor(self, x: float, y: float) -> None:
        now = time.time()
        if self._cursor_busy or now - self._last_cursor < 0.12:
            return
        self._cursor_busy, self._last_cursor = True, now

        async def run():
            try:
                cur = await self.page.evaluate(_CURSOR_JS, [x, y])
                if cur != self.cursor:
                    self.cursor = cur
                    await self.send({"t": "cursor", "c": cur})
            except Exception:  # noqa: BLE001
                pass
            finally:
                self._cursor_busy = False
        asyncio.ensure_future(run())

    # ── lifecycle ─────────────────────────────────────────────────────────
    async def attach(self, ws) -> None:
        old, self.ws = self.ws, ws
        if old is not None and old is not ws:
            try:
                await old.close(4000, "replaced")
            except Exception:  # noqa: BLE001
                pass
        self.detached = 0.0
        await self.send({"t": "hello", "sid": self.sid})
        await self.cast()
        await self.state()

    async def detach(self, ws) -> None:
        if self.ws is ws:
            self.ws = None
            self.detached = time.time()
            await self.cast(False)

    async def close(self, reason: str) -> None:
        if self._closed:
            return
        self._closed = True
        sessions.pop(self.sid, None)
        await self.send({"t": "bye", "reason": reason})
        if self.ws is not None:
            try:
                await self.ws.close(4001, reason)
            except Exception:  # noqa: BLE001
                pass
        try:
            await self.ctx.close()
        except Exception:  # noqa: BLE001
            pass
        log.info("closed uid=%s reason=%s open=%d", self.uid, reason, len(sessions))


async def reaper() -> None:
    while True:
        await asyncio.sleep(15)
        now = time.time()
        for s in list(sessions.values()):
            if s.ws is None and s.detached and now - s.detached > GRACE:
                await s.close("detached")
            elif s.ws is not None and now - s.last_input > IDLE:
                await s.close("idle")
            elif now - s.created > LIFETIME:
                await s.close("time")


def _num(q: dict, k: str, d: float) -> float:
    try:
        return float(q.get(k, [d])[0])
    except (TypeError, ValueError):
        return d


async def handler(ws) -> None:
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(ws.request.path).query)
    uid = check_ticket(q.get("ticket", [""])[0])
    if not uid:
        await ws.close(4003, "ticket")
        return
    sid = q.get("sid", [""])[0]
    s = sessions.get(sid)
    if s is not None and s.uid != uid:
        s = None
    if s is None:
        mine = sorted((x for x in sessions.values() if x.uid == uid), key=lambda x: x.last_input)
        if len(mine) >= PER_USER:
            await mine[0].close("replaced")
        if len(sessions) >= MAX_SESSIONS:
            idle = sorted((x for x in sessions.values() if x.ws is None), key=lambda x: x.detached)
            if idle:
                await idle[0].close("capacity")
            else:
                await ws.send(json.dumps({"t": "busy"}))
                await ws.close(4002, "busy")
                return
        s = Session(uid)
        dpr = max(1.0, min(_num(q, "dpr", 1), 2.0))
        w, h = int(max(200, min(_num(q, "w", 1024), 2560))), int(max(150, min(_num(q, "h", 700), 1600)))
        try:
            await s.start(w, h, dpr)
        except Exception as e:  # noqa: BLE001
            log.warning("start failed: %s", e)
            await ws.close(1011, "start")
            return
        sessions[s.sid] = s
        log.info("open uid=%s open=%d", uid, len(sessions))
    await s.attach(ws)
    first = q.get("url", [""])[0]
    if first and s.page.url == "about:blank":
        asyncio.ensure_future(s.nav(first))
    try:
        async for raw in ws:
            if isinstance(raw, bytes):
                continue
            try:
                m = json.loads(raw)
            except ValueError:
                continue
            if isinstance(m, dict):
                try:
                    await s.handle(m)
                except Exception as e:  # noqa: BLE001 — one bad event must not drop the session
                    log.debug("input: %s", e)
    except Exception:  # noqa: BLE001
        pass
    finally:
        await s.detach(ws)


def process_request(conn, req):
    path = urllib.parse.urlsplit(req.path).path
    if path in ("/health", "/rb/health"):
        body = json.dumps({"ok": _browser is not None and _browser.is_connected(),
                           "sessions": len(sessions), "max": MAX_SESSIONS}).encode()
        return Response(200, "OK", Headers({"Content-Type": "application/json",
                                            "Content-Length": str(len(body))}), body)
    if path not in ("/ws", "/rb/ws"):
        return Response(404, "Not Found", Headers({"Content-Length": "0"}), b"")
    return None


async def main() -> None:
    if not SECRET:
        raise SystemExit("CHARTO_BROWSER_SECRET is not set")
    await egress.serve(PROXY_PORT)
    await launch()
    asyncio.ensure_future(reaper())
    async with serve(handler, "0.0.0.0", PORT, process_request=process_request,
                     max_size=1 << 20, ping_interval=20, ping_timeout=30, compression=None):
        log.info("listening on :%d (max %d sessions)", PORT, MAX_SESSIONS)
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
