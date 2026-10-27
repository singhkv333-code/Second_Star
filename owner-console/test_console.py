"""The owner console's door and proxy, driven through its WSGI app.

    cd owner-console && python3 -m pytest -q test_console.py

The live round trip (console signs, dataserver verifies) is in
charto/data/test_coupons.py::test_console_round_trip_against_the_real_handler.
"""
from __future__ import annotations

import io
import json
import time
import urllib.parse

import pytest

import app as console

HOST = "console.example.net"
UA = "pytest-browser"


def call(method="GET", path="/", body=b"", cookie="", headers=None, ip="198.51.100.7"):
    env = {
        "REQUEST_METHOD": method, "PATH_INFO": path.split("?")[0],
        "QUERY_STRING": path.split("?")[1] if "?" in path else "",
        "SERVER_NAME": HOST, "SERVER_PORT": "443", "wsgi.url_scheme": "https",
        "HTTP_HOST": HOST, "HTTP_USER_AGENT": UA, "HTTP_X_FORWARDED_FOR": f"{ip}:51234",
        "CONTENT_LENGTH": str(len(body)), "wsgi.input": io.BytesIO(body),
    }
    if cookie:
        env["HTTP_COOKIE"] = f"{console.COOKIE}={cookie}"
    for k, v in (headers or {}).items():
        env["HTTP_" + k.upper().replace("-", "_")] = v
    got = {}

    def start(status, hdrs):
        got["status"], got["headers"] = int(status.split()[0]), hdrs

    out = b"".join(console.app(env, start))
    return got["status"], dict(got["headers"]), got["headers"], out


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setattr(console, "STATE", console.State())
    monkeypatch.setenv("OPS_PASSWORD_HASH", console.hash_password("right-horse-battery", n=2 ** 12))
    monkeypatch.setenv("OPS_ALLOWED_HOSTS", HOST)
    monkeypatch.setattr(console.time, "sleep", lambda s: None)
    calls = []

    def fake_upstream(method, path, query, body, ip):
        calls.append((method, path, query, body, ip))
        return 200, {"ok": True, "path": path, "coupons": [{"code": "=HYPERLINK(1)", "summary": "x"}]}

    monkeypatch.setattr(console, "upstream", fake_upstream)
    return calls


ORIGIN = {"Origin": f"https://{HOST}", "Sec-Fetch-Site": "same-origin"}


def login(pw="right-horse-battery", ip="198.51.100.7"):
    return call("POST", "/login", urllib.parse.urlencode({"password": pw}).encode(),
                headers={**ORIGIN, "Content-Type": "application/x-www-form-urlencoded"}, ip=ip)


def session():
    code, h, raw, _ = login()
    assert code == 303
    cookie = h["Set-Cookie"].split(";")[0].split("=", 1)[1]
    page = call("GET", "/", cookie=cookie)[3].decode()
    csrf = page.split('name="csrf" content="')[1].split('"')[0]
    return cookie, csrf


def test_every_response_carries_the_security_headers():
    code, h, _, body = call("GET", "/")
    assert code == 200 and b"Passphrase" in body
    assert "script-src 'self'" in h["Content-Security-Policy"] and "unsafe-inline" not in h["Content-Security-Policy"]
    assert h["X-Frame-Options"] == "DENY" and h["Cache-Control"] == "no-store"
    assert h["Strict-Transport-Security"].startswith("max-age=63072000")
    assert "noindex" in h["X-Robots-Tag"]


def test_a_real_browser_form_post_with_origin_null_signs_in():
    """Chrome sends Origin: null on a form POST under some referrer policies;
    Sec-Fetch-Site is what proves the page was ours."""
    form = urllib.parse.urlencode({"password": "right-horse-battery"}).encode()
    browser = {"Origin": "null", "Sec-Fetch-Site": "same-origin", "Content-Type": "application/x-www-form-urlencoded"}
    assert call("POST", "/login", form, headers=browser)[0] == 303
    assert call("POST", "/login", form, headers={**browser, "Sec-Fetch-Site": "cross-site"})[0] == 403
    assert call("POST", "/login", form, headers={"Origin": "null"})[0] == 403
    assert call("POST", "/login", form, headers={"Origin": "https://evil.example"})[0] == 403
    assert call("POST", "/login", form)[0] == 403                         # nothing at all
    assert call("POST", "/login", form, headers={"X-Console-Client": "cli"})[0] == 303


def test_the_cookie_is_host_only_secure_httponly_strict():
    code, h, _, _ = login()
    c = h["Set-Cookie"]
    assert code == 303 and c.startswith("__Host-pivot_owner=")
    for flag in ("Path=/", "Secure", "HttpOnly", "SameSite=Strict"):
        assert flag in c
    assert "Domain" not in c


def test_wrong_passphrase_locks_the_address_after_five():
    for _ in range(5):
        assert login("nope")[0] == 401
    assert login()[0] == 429                       # even the right one waits
    assert login(ip="203.0.113.50")[0] == 303      # another address is not locked


def test_the_lock_lifts_after_its_time(monkeypatch):
    for _ in range(5):
        login("nope")
    t = time.time() + console.LOCKOUT + 1
    monkeypatch.setattr(console.time, "time", lambda: t)
    assert login()[0] == 303


def test_api_needs_a_session_and_writes_need_csrf_and_origin(fresh):
    assert call("GET", "/api/overview")[0] == 401
    cookie, csrf = session()
    assert call("GET", "/api/overview", cookie=cookie)[0] == 200
    body = json.dumps({"kind": "access"}).encode()
    assert call("POST", "/api/coupons", body, cookie=cookie, headers=ORIGIN)[0] == 403
    assert call("POST", "/api/coupons", body, cookie=cookie,
                headers={"X-CSRF": csrf, "Origin": "https://evil.example"})[0] == 403
    assert call("POST", "/api/coupons", body, cookie=cookie,
                headers={**ORIGIN, "X-CSRF": csrf, "Sec-Fetch-Site": "cross-site"})[0] == 403
    assert call("POST", "/api/coupons", body, cookie=cookie, headers={**ORIGIN, "X-CSRF": csrf})[0] == 200
    assert fresh[-1][:2] == ("POST", "/coupons") and fresh[-1][4] == "198.51.100.7"


def test_only_allowed_routes_and_query_keys_are_forwarded(fresh):
    cookie, _ = session()
    assert call("GET", "/api/users/1/../../health", cookie=cookie)[0] == 404
    assert call("GET", "/api/billing/me", cookie=cookie)[0] == 404
    assert call("GET", "/api/users?q=ravi&evil=1&status=active", cookie=cookie)[0] == 200
    assert fresh[-1][2] == {"q": "ravi", "status": "active"}


def test_session_is_bound_to_the_browser_and_ends_on_logout():
    cookie, csrf = session()
    env_other = call("GET", "/api/overview", cookie=cookie, headers={"User-Agent": "other"})
    assert env_other[0] == 401
    assert call("POST", "/logout", cookie=cookie, headers={**ORIGIN, "X-CSRF": csrf})[0] == 303
    assert call("GET", "/api/overview", cookie=cookie)[0] == 401


def test_session_ends_when_idle(monkeypatch):
    cookie, _ = session()
    t = time.time() + console.IDLE + 5
    monkeypatch.setattr(console.time, "time", lambda: t)
    assert call("GET", "/api/overview", cookie=cookie)[0] == 401


def test_unknown_host_is_not_served():
    code, *_ = call("GET", "/", headers={"Host": "pivot-owner.evil.example"})
    assert code == 404


def test_csv_export_neutralises_formulas():
    cookie, _ = session()
    code, h, _, body = call("GET", "/api/coupons/export.csv", cookie=cookie)
    assert code == 200 and "attachment" in h["Content-Disposition"]
    assert b"'=HYPERLINK(1)" in body


def test_static_files_only_by_exact_name():
    assert call("GET", "/static/console.js")[0] == 200
    assert call("GET", "/static/../app.py")[0] == 404
    assert call("GET", "/static/app.py")[0] == 404


def test_password_hash_round_trip():
    h = console.hash_password("a-long-passphrase", n=2 ** 12)
    assert console.check_password("a-long-passphrase", h)
    assert not console.check_password("a-long-passphrasf", h)
    assert not console.check_password("x", "garbage")
