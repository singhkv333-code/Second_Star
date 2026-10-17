#!/usr/bin/env python3
"""Make and manage Pivot codes from a terminal, through the owner console.

It signs in to the console with the passphrase (the same door the browser
uses, with the same lockout), so it works from anywhere and the console
stays the only thing that can reach the live server.

    pivot_codes.py create --kind access --plan pro --days 30 --emails ravi@x.in
    pivot_codes.py create --kind discount --plan pro --percent 40 --discount-cycles 3 --code LAUNCH40
    pivot_codes.py create --kind access --plan pro_plus --days 14 --count 50 --prefix CREATOR --max 50
    pivot_codes.py list [--status active] [--q diwali]
    pivot_codes.py show LAUNCH40
    pivot_codes.py disable LAUNCH40 | enable LAUNCH40 | archive LAUNCH40
    pivot_codes.py export > codes.csv

Configuration (first match wins):
    console URL   --url, $PIVOT_CONSOLE_URL, ~/.config/pivot-ops/console-url
    passphrase    $PIVOT_CONSOLE_PASSWORD, ~/.config/pivot-ops/password
"""
from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

CONF = Path.home() / ".config" / "pivot-ops"
UA = "pivot-codes-cli/1"


def _setting(env: str, file: str) -> str:
    if os.environ.get(env):
        return os.environ[env].strip()
    p = CONF / file
    return p.read_text().strip() if p.is_file() else ""


class Console:
    def __init__(self, url: str, password: str):
        self.url = url.rstrip("/")
        self.jar = http.cookiejar.CookieJar()
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar),
                                              _NoRedirect())
        self.csrf = ""
        self._login(password)

    def _req(self, method: str, path: str, data: bytes | None = None, ctype: str = "application/json",
             extra: dict | None = None):
        req = urllib.request.Request(self.url + path, data=data, method=method, headers={
            "User-Agent": UA, "Content-Type": ctype, "X-Console-Client": "cli",
            **({"X-CSRF": self.csrf} if self.csrf else {}), **(extra or {})})
        try:
            with self.op.open(req, timeout=40) as r:
                return r.status, r.read(), r.headers
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read(), exc.headers

    def _login(self, password: str) -> None:
        code, body, _ = self._req("POST", "/login", urllib.parse.urlencode({"password": password}).encode(),
                                  "application/x-www-form-urlencoded")
        if code != 303:
            text = body.decode(errors="replace")
            reason = "wrong passphrase" if code == 401 else "locked out for a while" if code == 429 else f"HTTP {code}"
            raise SystemExit(f"sign-in failed: {reason}" + ("" if code in (401, 429) else f"\n{text[:300]}"))
        code, body, _ = self._req("GET", "/api/session")
        if code != 200:
            raise SystemExit(f"sign-in did not hold a session (HTTP {code})")
        self.csrf = json.loads(body)["csrf"]

    def get(self, path: str) -> dict:
        code, body, _ = self._req("GET", "/api" + path)
        return _out(code, body)

    def post(self, path: str, payload: dict) -> dict:
        code, body, _ = self._req("POST", "/api" + path, json.dumps(payload).encode())
        return _out(code, body)

    def raw(self, path: str) -> bytes:
        code, body, _ = self._req("GET", "/api" + path)
        if code != 200:
            _out(code, body)
        return body


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def _out(code: int, body: bytes) -> dict:
    try:
        data = json.loads(body or b"{}")
    except ValueError:
        data = {"error": body.decode(errors="replace")[:300]}
    if code != 200:
        raise SystemExit(f"refused ({code}): {data.get('error', data)}")
    return data


def _find(con: Console, code: str) -> dict:
    hits = [c for c in con.get(f"/coupons?q={urllib.parse.quote(code)}")["coupons"]
            if c["code"].upper() == code.strip().upper()]
    if not hits:
        raise SystemExit(f"{code}: no such code")
    return hits[0]


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="pivot_codes.py", description="Make and manage Pivot codes.")
    ap.add_argument("--url", help="the console's https address")
    sub = ap.add_subparsers(dest="cmd", required=True)
    mk = sub.add_parser("create", help="make one code or a batch")
    mk.add_argument("--kind", choices=("access", "discount"), required=True)
    mk.add_argument("--plan", required=True, help="pro | pro_plus (discount: also any)")
    mk.add_argument("--days", type=int, help="access: free days")
    mk.add_argument("--percent", type=int, help="discount: percent off")
    mk.add_argument("--rupees-off", type=int, help="discount: rupees off")
    mk.add_argument("--cycles", default="any", help="discount: monthly | annual | any")
    mk.add_argument("--discount-cycles", type=int, help="discount: billing cycles at the lower price")
    mk.add_argument("--code", help="a custom code, e.g. DIWALI50")
    mk.add_argument("--count", type=int, help="make this many unique codes")
    mk.add_argument("--prefix", default="PIVOT")
    mk.add_argument("--max", type=int, help="total uses across everyone")
    mk.add_argument("--per-user", type=int, default=1)
    mk.add_argument("--expires-days", type=int, help="stops working after this many days")
    mk.add_argument("--emails", default="", help="comma-separated: only these accounts")
    mk.add_argument("--domain", default="", help="only accounts at this email domain")
    mk.add_argument("--new-only", action="store_true", help="only accounts that never subscribed")
    mk.add_argument("--campaign", default="")
    mk.add_argument("--note", default="")
    ls = sub.add_parser("list", help="codes with their use counts")
    ls.add_argument("--status", choices=("active", "disabled", "archived"))
    ls.add_argument("--q", default="")
    for name in ("show", "disable", "enable", "archive"):
        sub.add_parser(name).add_argument("code")
    ex = sub.add_parser("export", help="CSV of codes to stdout")
    ex.add_argument("--batch", default="")
    a = ap.parse_args(argv)

    url = a.url or _setting("PIVOT_CONSOLE_URL", "console-url")
    password = _setting("PIVOT_CONSOLE_PASSWORD", "password")
    if not url or not password:
        raise SystemExit("Set the console URL and passphrase: see --help.")
    con = Console(url, password)

    if a.cmd == "create":
        body = {"kind": a.kind, "plan": a.plan, "per_user": a.per_user, "emails": a.emails,
                "domain": a.domain, "new_only": a.new_only, "campaign": a.campaign, "note": a.note}
        if a.kind == "access":
            body["access_days"] = a.days
        else:
            body["cycles"] = a.cycles
            if a.percent:
                body["percent_off"] = a.percent
            if a.rupees_off:
                body["amount_off"] = a.rupees_off * 100
            if a.discount_cycles:
                body["discount_cycles"] = a.discount_cycles
        if a.code:
            body["code"] = a.code
        if a.count:
            body["count"], body["prefix"] = a.count, a.prefix
        if a.max:
            body["max_redemptions"] = a.max
        if a.expires_days:
            body["valid_until"] = int(time.time()) + a.expires_days * 86400
        out = con.post("/coupons", body)
        for c in out["coupons"]:
            print(f"{c['code']}\t{c['summary']}")
        if out.get("batch"):
            print(f"# batch {out['batch']}", file=sys.stderr)
    elif a.cmd == "list":
        qs = urllib.parse.urlencode({k: v for k, v in (("status", a.status), ("q", a.q)) if v})
        for c in con.get("/coupons" + (f"?{qs}" if qs else ""))["coupons"]:
            cap = "∞" if c["max_redemptions"] is None else c["max_redemptions"]
            print(f"{c['code']}\t{c['status']}\t{c['redeemed']}/{cap}\t{c['summary']}")
    elif a.cmd == "export":
        sys.stdout.write(con.raw("/coupons/export.csv" + (f"?batch={a.batch}" if a.batch else "")).decode())
    else:
        c = _find(con, a.code)
        if a.cmd == "show":
            print(json.dumps(con.get(f"/coupons/{c['id']}"), indent=2))
        else:
            st = {"disable": "disabled", "enable": "active", "archive": "archived"}[a.cmd]
            con.post(f"/coupons/{c['id']}", {"status": st})
            print(f"{c['code']}: {st}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
