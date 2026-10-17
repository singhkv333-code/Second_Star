"""Pivot owner console: codes, accounts and the state of the business.

A small WSGI app (standard library only; gunicorn serves it on Azure App
Service). It holds no data. Every read and write goes to the dataserver's
`/ops/*` API, signed with OPS_HMAC_KEY (see charto/data/ops_api.py), so the
console's own disk never holds an account, a code or a payment.

THE DOOR
--------
One long passphrase, checked against an scrypt hash (OPS_PASSWORD_HASH). The
passphrase itself is stored nowhere on Azure.

    failed sign-ins     5 per address per 15 minutes, then that address waits
                        15 minutes; every failure costs ~1 s; sign-ins are
                        checked one at a time
    session             random 256-bit token in a __Host- cookie (Secure,
                        HttpOnly, SameSite=Strict); the server keeps only its
                        SHA-256. Ends after 30 minutes idle or 8 hours, on
                        sign-out, and on restart.
    writes              POST only, with the session's CSRF token in a header
                        and an Origin that is this site
    every response      strict CSP (no inline script, no third-party origin),
                        HSTS, no framing, no referrer, no caching, noindex
    the API             an allow list of routes and query keys; nothing else
                        is forwarded

Environment:
    OPS_PASSWORD_HASH   scrypt$N$r$p$salt_b64$hash_b64 (tools/make_password.py)
    OPS_HMAC_KEY        shared with the dataserver's PIVOT_OPS_KEY (>= 32 chars)
    OPS_UPSTREAM        https://app.pivotnow.in
    OPS_ALLOWED_HOSTS   comma-separated Host names this site answers to
    OPS_ACTOR           the name written to the audit log (default: owner)
"""
from __future__ import annotations

import base64
import csv
import hashlib
import hmac
import io
import json
import mimetypes
import os
import re
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.cookies import SimpleCookie
from pathlib import Path

HERE = Path(__file__).resolve().parent
STATIC = HERE / "static"
COOKIE = "__Host-pivot_owner"
IDLE = 30 * 60
ABSOLUTE = 8 * 3600
FAIL_LIMIT = 5
FAIL_WINDOW = 15 * 60
LOCKOUT = 15 * 60
MAX_BODY = 2 * 1024 * 1024

# What the browser may reach upstream: (method, path pattern). Paths are
# relative to /ops. Anything else is a 404 from the console itself.
ALLOW = [
    ("GET", r"/overview"), ("GET", r"/health"), ("GET", r"/catalog"),
    ("GET", r"/coupons"), ("GET", r"/coupons/\d{1,9}"),
    ("POST", r"/coupons"), ("POST", r"/coupons/\d{1,9}"), ("POST", r"/coupons/batch"),
    ("GET", r"/redemptions"), ("POST", r"/redemptions/\d{1,9}/revoke"),
    ("GET", r"/users"), ("GET", r"/users/\d{1,9}"),
    ("POST", r"/users/\d{1,9}/grant"), ("POST", r"/users/\d{1,9}/signout"),
    ("GET", r"/grants"), ("POST", r"/grants/\d{1,9}/revoke"),
    ("GET", r"/subscriptions"), ("GET", r"/events"), ("GET", r"/audit"),
]
QUERY_KEYS = {"q", "status", "batch", "all"}

HEADERS = [
    ("Content-Security-Policy",
     "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
     "connect-src 'self'; font-src 'self'; form-action 'self'; frame-ancestors 'none'; "
     "base-uri 'none'; manifest-src 'none'"),
    ("Strict-Transport-Security", "max-age=63072000; includeSubDomains"),
    ("X-Frame-Options", "DENY"),
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "same-origin"),
    ("Cross-Origin-Opener-Policy", "same-origin"),
    ("Cross-Origin-Resource-Policy", "same-origin"),
    ("Cross-Origin-Embedder-Policy", "require-corp"),
    ("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=(), usb=(), "
                           "interest-cohort=()"),
    ("Cache-Control", "no-store"),
    ("X-Robots-Tag", "noindex, nofollow, noarchive"),
]


# ── the passphrase ─────────────────────────────────────────────────────────

def hash_password(password: str, n: int = 2 ** 15, r: int = 8, p: int = 1) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, maxmem=128 * 1024 * 1024, dklen=32)
    return f"scrypt${n}${r}${p}${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"


def check_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt, want = stored.split("$")
        if algo != "scrypt":
            return False
        dk = hashlib.scrypt(password.encode()[:1024], salt=base64.b64decode(salt), n=int(n),
                            r=int(r), p=int(p), maxmem=128 * 1024 * 1024, dklen=32)
        return hmac.compare_digest(dk, base64.b64decode(want))
    except (ValueError, TypeError):
        return False


# ── state (process memory: one gunicorn worker, many threads) ─────────────

class State:
    def __init__(self):
        self.lock = threading.Lock()
        self.sessions: dict[str, dict] = {}
        self.fails: dict[str, list[float]] = {}
        self.locked: dict[str, float] = {}
        self.door = threading.Lock()            # one sign-in check at a time

    def new_session(self, ip: str, ua: str) -> tuple[str, dict]:
        tok = secrets.token_urlsafe(32)
        now = time.time()
        s = {"created": now, "seen": now, "csrf": secrets.token_urlsafe(32),
             "ua": hashlib.sha256(ua.encode()).hexdigest(), "ip": ip}
        with self.lock:
            self.sessions[hashlib.sha256(tok.encode()).hexdigest()] = s
        return tok, s

    def session(self, tok: str, ua: str) -> dict | None:
        if not tok or len(tok) > 100:
            return None
        key = hashlib.sha256(tok.encode()).hexdigest()
        now = time.time()
        with self.lock:
            for k, s in list(self.sessions.items()):
                if now - s["seen"] > IDLE or now - s["created"] > ABSOLUTE:
                    del self.sessions[k]
            s = self.sessions.get(key)
            if not s or not hmac.compare_digest(s["ua"], hashlib.sha256(ua.encode()).hexdigest()):
                return None
            s["seen"] = now
            return s

    def end(self, tok: str) -> None:
        with self.lock:
            self.sessions.pop(hashlib.sha256(tok.encode()).hexdigest(), None)

    def is_locked(self, ip: str) -> float:
        now = time.time()
        with self.lock:
            until = self.locked.get(ip, 0)
            if until and until <= now:
                self.locked.pop(ip, None)
                return 0
            return until

    def failed(self, ip: str) -> None:
        now = time.time()
        with self.lock:
            recent = [t for t in self.fails.get(ip, []) if now - t < FAIL_WINDOW] + [now]
            self.fails[ip] = recent
            if len(recent) >= FAIL_LIMIT:
                self.locked[ip] = now + LOCKOUT
                self.fails[ip] = []

    def succeeded(self, ip: str) -> None:
        with self.lock:
            self.fails.pop(ip, None)


STATE = State()


# ── the request ────────────────────────────────────────────────────────────

def client_ip(environ) -> str:
    """App Service's front end appends the caller's address to
    X-Forwarded-For (as ip:port); the LAST entry is the one it wrote."""
    xff = environ.get("HTTP_X_FORWARDED_FOR", "")
    last = xff.split(",")[-1].strip() if xff else environ.get("REMOTE_ADDR", "")
    if last.startswith("["):
        last = last[1:].split("]")[0]
    elif last.count(":") == 1:
        last = last.split(":")[0]
    return re.sub(r"[^0-9a-fA-F:.]", "", last)[:64] or "unknown"


def _host_ok(environ) -> bool:
    allowed = {h.strip().lower() for h in os.environ.get("OPS_ALLOWED_HOSTS", "").split(",") if h.strip()}
    if not allowed:
        return True
    host = environ.get("HTTP_HOST", "").split(":")[0].lower()
    return host in allowed


def _origin_ok(environ) -> bool:
    """Did this write come from the console's own page?

    A browser says so in Sec-Fetch-Site, which no page can set. Origin is the
    fallback for browsers without it; a form POST can carry `Origin: null`
    (privacy settings, referrer policies), so null alone never passes. The
    CLI sends neither and names itself; a cross-site page cannot add that
    header without a CORS preflight this app never answers."""
    host = environ.get("HTTP_HOST", "")
    origin = environ.get("HTTP_ORIGIN", "")
    site = environ.get("HTTP_SEC_FETCH_SITE", "")
    if site:
        return site == "same-origin"
    if origin:
        return origin == f"https://{host}"
    return environ.get("HTTP_X_CONSOLE_CLIENT") == "cli"


def _cookie(environ) -> str:
    c = SimpleCookie()
    try:
        c.load(environ.get("HTTP_COOKIE", ""))
    except Exception:                                   # noqa: BLE001 — a bad cookie is no cookie
        return ""
    return c[COOKIE].value if COOKIE in c else ""


def _read(environ, limit: int = MAX_BODY) -> bytes | None:
    try:
        n = int(environ.get("CONTENT_LENGTH") or 0)
    except ValueError:
        return None
    if n < 0 or n > limit:
        return None
    return environ["wsgi.input"].read(n) if n else b""


def _respond(start, status: str, body: bytes, ctype: str, extra: list | None = None):
    start(status, [("Content-Type", ctype), ("Content-Length", str(len(body))), *HEADERS, *(extra or [])])
    return [body]


def _json(start, code: int, obj, extra=None):
    reason = {200: "OK", 400: "Bad Request", 401: "Unauthorized", 403: "Forbidden", 404: "Not Found",
              409: "Conflict", 413: "Payload Too Large", 429: "Too Many Requests",
              500: "Internal Server Error", 502: "Bad Gateway"}.get(code, "Error")
    return _respond(start, f"{code} {reason}", json.dumps(obj).encode(), "application/json", extra)


def _set_cookie(tok: str, max_age: int) -> tuple[str, str]:
    return ("Set-Cookie", f"{COOKIE}={tok}; Path=/; Max-Age={max_age}; Secure; HttpOnly; SameSite=Strict")


# ── upstream ───────────────────────────────────────────────────────────────

def sign(key: bytes, method: str, path: str, ts: str, nonce: str, body: bytes) -> str:
    msg = "\n".join([method, path, ts, nonce, hashlib.sha256(body).hexdigest()])
    return hmac.new(key, msg.encode(), hashlib.sha256).hexdigest()


def upstream(method: str, path: str, query: dict, body: bytes, ip: str) -> tuple[int, dict]:
    base = os.environ.get("OPS_UPSTREAM", "").rstrip("/")
    key = os.environ.get("OPS_HMAC_KEY", "").strip().encode()
    if not base.startswith("https://") and not base.startswith("http://127.0.0.1"):
        return 500, {"error": "OPS_UPSTREAM is not set to an https address."}
    if len(key) < 32:
        return 500, {"error": "OPS_HMAC_KEY is not set."}
    qs = urllib.parse.urlencode(sorted(query.items()))
    target = "/ops" + path + (f"?{qs}" if qs else "")
    ts, nonce = str(int(time.time())), secrets.token_urlsafe(24)
    req = urllib.request.Request(base + target, method=method, data=body if method == "POST" else None,
                                 headers={
                                     "Content-Type": "application/json",
                                     "X-Ops-Timestamp": ts, "X-Ops-Nonce": nonce,
                                     "X-Ops-Signature": sign(key, method, target, ts, nonce,
                                                             body if method == "POST" else b""),
                                     "X-Ops-Actor": os.environ.get("OPS_ACTOR", "owner")[:60],
                                     "X-Ops-Client-IP": ip,
                                     "User-Agent": "pivot-owner-console/1"})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            out = json.loads(exc.read() or b"{}")
        except ValueError:
            out = {}
        if exc.code == 404 and out.get("error") == "not found":
            return 502, {"error": "The server refused the console's signature or address. "
                                  "Check PIVOT_OPS_KEY and PIVOT_OPS_ALLOWED_IPS on the VM."}
        return exc.code, out if isinstance(out, dict) else {"error": f"HTTP {exc.code}"}
    except (OSError, ValueError) as exc:
        return 502, {"error": f"Could not reach the Pivot server ({type(exc).__name__})."}


def _allowed(method: str, path: str) -> bool:
    return any(m == method and re.fullmatch(p, path) for m, p in ALLOW)


def _query(environ) -> dict:
    out = {}
    for k, v in urllib.parse.parse_qsl(environ.get("QUERY_STRING", ""), keep_blank_values=False):
        if k in QUERY_KEYS and len(v) <= 200:
            out[k] = v
    return out


def _csv(rows: list[dict], cols: list[str]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(cols)
    for r in rows:
        # A cell that starts like a formula is quoted so a spreadsheet shows
        # it as text rather than running it.
        w.writerow([("'" + s) if (s := "" if r.get(c) is None else str(r.get(c))) and s[0] in "=+-@\t\r" else s
                    for c in cols])
    return buf.getvalue().encode()


# ── pages ──────────────────────────────────────────────────────────────────

def _page(title: str, body: str, csrf: str = "") -> bytes:
    meta = f'<meta name="csrf" content="{csrf}">' if csrf else ""
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>{title}</title>{meta}
<link rel="icon" href="/static/icon.svg" type="image/svg+xml">
<link rel="stylesheet" href="/static/console.css?v=1">
</head><body>{body}</body></html>""".encode()


LOGIN = """<main class="door">
  <form class="door-card" method="post" action="/login" autocomplete="off">
    <div class="brand"><img src="/static/icon.svg" alt="" width="28" height="28"><span>Pivot owner console</span></div>
    <label for="pw">Passphrase</label>
    <input id="pw" name="password" type="password" required autofocus maxlength="1024" autocomplete="current-password">
    {error}
    <button type="submit">Enter</button>
    <p class="fine">Five wrong attempts lock this address for fifteen minutes.</p>
  </form>
</main>"""


def _login_page(start, error: str = "", status: str = "200 OK"):
    err = f'<p class="err" role="alert">{error}</p>' if error else ""
    return _respond(start, status, _page("Pivot owner console", LOGIN.replace("{error}", err)),
                    "text/html; charset=utf-8")


def _shell(start, s: dict):
    body = ('<div id="app"></div><noscript>The console needs JavaScript.</noscript>'
            '<script src="/static/console.js?v=1" defer></script>')
    return _respond(start, "200 OK", _page("Pivot owner console", body, s["csrf"]), "text/html; charset=utf-8")


def _static(start, name: str):
    if not re.fullmatch(r"[a-z0-9_-]+\.(css|js|svg)", name):
        return _json(start, 404, {"error": "not found"})
    path = STATIC / name
    if not path.is_file():
        return _json(start, 404, {"error": "not found"})
    ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
    if name.endswith(".js"):
        ctype = "text/javascript; charset=utf-8"
    elif name.endswith(".css"):
        ctype = "text/css; charset=utf-8"
    return _respond(start, "200 OK", path.read_bytes(), ctype)


# ── the app ────────────────────────────────────────────────────────────────

def app(environ, start):
    method = environ.get("REQUEST_METHOD", "GET")
    path = environ.get("PATH_INFO", "/") or "/"
    # App Service's warm-up and health probes.
    if path in ("/robots933456.txt", "/healthz"):
        return _respond(start, "200 OK", b"ok", "text/plain")
    if path == "/robots.txt":
        return _respond(start, "200 OK", b"User-agent: *\nDisallow: /\n", "text/plain")
    if not _host_ok(environ):
        return _json(start, 404, {"error": "not found"})
    ip = client_ip(environ)
    ua = environ.get("HTTP_USER_AGENT", "")[:400]
    tok = _cookie(environ)
    s = STATE.session(tok, ua)

    if path.startswith("/static/") and method == "GET":
        return _static(start, path[len("/static/"):])

    if path == "/login" and method == "POST":
        if not _origin_ok(environ):
            return _login_page(start, "That request did not come from this page.", "403 Forbidden")
        stored = os.environ.get("OPS_PASSWORD_HASH", "")
        if not stored:
            return _login_page(start, "The console has no passphrase configured.", "500 Internal Server Error")
        until = STATE.is_locked(ip)
        if until:
            mins = max(1, int((until - time.time()) // 60) + 1)
            return _login_page(start, f"Too many wrong attempts. Try again in {mins} minutes.",
                               "429 Too Many Requests")
        raw = _read(environ, 4096)
        if raw is None:
            return _login_page(start, "That request was too large.", "413 Payload Too Large")
        form = urllib.parse.parse_qs(raw.decode(errors="replace"))
        pw = (form.get("password") or [""])[0]
        with STATE.door:
            ok = bool(pw) and check_password(pw, stored)
            if not ok:
                time.sleep(1.0)
        if not ok:
            STATE.failed(ip)
            return _login_page(start, "That passphrase is not right.", "401 Unauthorized")
        STATE.succeeded(ip)
        if tok:
            STATE.end(tok)
        new, _ = STATE.new_session(ip, ua)
        start("303 See Other", [("Location", "/"), _set_cookie(new, ABSOLUTE), *HEADERS])
        return [b""]

    if path == "/logout" and method == "POST":
        if s and _origin_ok(environ) and hmac.compare_digest(
                environ.get("HTTP_X_CSRF", ""), s["csrf"]):
            STATE.end(tok)
        start("303 See Other", [("Location", "/"), _set_cookie("", 0), *HEADERS])
        return [b""]

    if path == "/" and method == "GET":
        return _shell(start, s) if s else _login_page(start)

    if path.startswith("/api/"):
        if not s:
            return _json(start, 401, {"error": "Signed out. Reload the page and sign in."})
        sub = path[len("/api"):]
        if sub == "/session" and method == "GET":
            return _json(start, 200, {"csrf": s["csrf"], "idle_seconds": IDLE,
                                      "ends": int(s["created"] + ABSOLUTE)})
        if method == "POST":
            if not _origin_ok(environ) or not hmac.compare_digest(environ.get("HTTP_X_CSRF", ""), s["csrf"]):
                return _json(start, 403, {"error": "This request did not come from the console page."})
        if sub == "/coupons/export.csv" and method == "GET":
            code, out = upstream("GET", "/coupons", _query(environ), b"", ip)
            if code != 200:
                return _json(start, code, out)
            cols = ["code", "summary", "status", "redeemed", "reserved", "max_redemptions", "per_user",
                    "valid_from", "valid_until", "campaign", "batch", "note", "created"]
            return _respond(start, "200 OK", _csv(out.get("coupons", []), cols), "text/csv; charset=utf-8",
                            [("Content-Disposition", 'attachment; filename="pivot-codes.csv"')])
        if not _allowed(method, sub):
            return _json(start, 404, {"error": "not found"})
        body = b""
        if method == "POST":
            raw = _read(environ)
            if raw is None:
                return _json(start, 413, {"error": "That request was too large."})
            try:
                parsed = json.loads(raw or b"{}")
            except ValueError:
                return _json(start, 400, {"error": "bad JSON"})
            if not isinstance(parsed, dict):
                return _json(start, 400, {"error": "bad JSON"})
            body = json.dumps(parsed).encode()
        code, out = upstream(method, sub, _query(environ) if method == "GET" else {}, body, ip)
        return _json(start, code, out)

    return _json(start, 404, {"error": "not found"})
