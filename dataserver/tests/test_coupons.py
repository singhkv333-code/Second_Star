"""Codes (coupons.py) and the owner API (ops_api.py), over real HTTP.

Same harness as test_entitlements_api.py: the real Handler, a temp account
DB, a fake Razorpay wire. Run from dataserver/tests:
    PAYWALL_ENABLED=1 ../../pivot/.venv/bin/python -m pytest -q test_coupons.py
"""
from __future__ import annotations

import json
import secrets
import time

import pytest

import billing
import coupons
import entitlements as ent
import ops_api
from test_entitlements_api import DAY, env, hook, _sign  # noqa: F401  (fixtures)

KEY = "k" * 48


@pytest.fixture()
def rzp(monkeypatch):
    for k, v in {
        "RAZORPAY_KEY_ID": "rzp_test_x", "RAZORPAY_KEY_SECRET": "key-secret",
        "RAZORPAY_WEBHOOK_SECRET": "hook-secret",
        "RAZORPAY_PLAN_PRO_MONTHLY": "plan_pro_m",
        "RAZORPAY_PLAN_PRO_ANNUAL": "plan_pro_y",
        "RAZORPAY_PLAN_PRO_PLUS_MONTHLY": "plan_pp_m",
        "RAZORPAY_PLAN_PRO_PLUS_ANNUAL": "plan_pp_y",
    }.items():
        monkeypatch.setenv(k, v)
    calls: list[tuple] = []
    subs: dict[str, dict] = {}
    n = {"plan": 0, "sub": 0}

    def wire(method, path, body=None):
        calls.append((method, path, body))
        now = int(time.time())
        if method == "POST" and path == "/plans":
            n["plan"] += 1
            return {"id": f"plan_code{n['plan']}", "item": body["item"]}
        if method == "POST" and path == "/subscriptions":
            n["sub"] += 1
            sid = f"sub_{n['sub']}"
            subs[sid] = {"id": sid, "plan_id": body["plan_id"], "status": "created",
                         "total_count": body["total_count"]}
            return {"id": sid, "short_url": "https://rzp.io/x"}
        if method == "POST" and path.endswith("/cancel"):
            sid = path.split("/")[2]
            subs[sid]["status"] = "cancelled"
            return subs[sid]
        if method == "GET" and path.startswith("/subscriptions/"):
            return subs[path.split("/")[2]]
        if method == "GET" and path.startswith("/payments/"):
            return wire.payment
        if method == "GET" and path.startswith("/invoices/"):
            return {"subscription_id": wire.payment["sub"], "payment_id": wire.payment["id"]}
        return {}

    monkeypatch.setattr(billing, "http", wire)
    wire.calls, wire.subs, wire.payment = calls, subs, {}
    return wire


def activate(env, rzp, sid, eid):
    now = int(time.time())
    rzp.subs[sid].update(status="active", current_start=now - 60, current_end=now + 30 * DAY)
    raw = json.dumps({"event": "subscription.activated", "payload": {
        "subscription": {"entity": rzp.subs[sid]}}}).encode()
    return env.call("POST", "/billing/webhook", raw=raw, headers={
        "X-Razorpay-Signature": _sign(raw, "hook-secret"), "x-razorpay-event-id": eid})


def make(**spec):
    return coupons.create({"kind": "access", "plan": "pro", "access_days": 30, **spec}, "test")[0]


# ── access codes ───────────────────────────────────────────────────────────

def test_access_code_grants_the_plan_for_its_days_once(env):
    uid, tok = env.signup("a@x.io")
    c = make(code="FRIENDS30")
    code, out = env.post("/billing/coupon", {"code": "friends30"}, token=tok)
    assert code == 200 and out["coupon"]["summary"] == "Pro free for 30 days", out
    assert ent.plan_of(uid) == "free"                    # checking uses nothing
    code, out = env.post("/billing/redeem", {"code": " friends30 "}, token=tok)
    assert code == 200, out
    assert out["billing"]["plan"] == "pro"
    assert abs(out["redeemed"]["until"] - (time.time() + 30 * DAY)) < 60
    code, out = env.post("/billing/redeem", {"code": "FRIENDS30"}, token=tok)
    assert code == 409 and out["code"] == "already_used"
    assert coupons.usage(c["id"]) == {"redeemed": 1}


def test_access_code_ends_on_read_after_its_days(env, monkeypatch):
    uid, tok = env.signup("a@x.io")
    make(code="WEEK", access_days=7)
    env.post("/billing/redeem", {"code": "WEEK"}, token=tok)
    later = time.time() + 8 * DAY
    monkeypatch.setattr(ent, "_now", lambda: int(later))
    assert ent.plan_of(uid) == "free"


def test_limits_total_uses_window_allow_list_and_status(env):
    _, t1 = env.signup("one@x.io")
    _, t2 = env.signup("two@x.io")
    _, t3 = env.signup("three@corp.in")
    make(code="ONLYONE", max_redemptions=1)
    assert env.post("/billing/redeem", {"code": "ONLYONE"}, token=t1)[0] == 200
    code, out = env.post("/billing/redeem", {"code": "ONLYONE"}, token=t2)
    assert code == 409 and out["code"] == "used_up"

    make(code="LATER", valid_from=int(time.time()) + DAY)
    assert env.post("/billing/redeem", {"code": "LATER"}, token=t2)[1]["code"] == "not_started"
    make(code="GONE", valid_until=int(time.time()) - 1)
    assert env.post("/billing/redeem", {"code": "GONE"}, token=t2)[1]["code"] == "expired"

    make(code="VIPS", emails="two@x.io, someone@else.com")
    assert env.post("/billing/redeem", {"code": "VIPS"}, token=t3)[1]["code"] == "not_eligible"
    assert env.post("/billing/redeem", {"code": "VIPS"}, token=t2)[0] == 200
    make(code="CORP", domain="corp.in")
    assert env.post("/billing/redeem", {"code": "CORP"}, token=t1)[1]["code"] == "not_eligible"
    assert env.post("/billing/redeem", {"code": "CORP"}, token=t3)[0] == 200

    c = make(code="PAUSED")
    coupons.update(c["id"], {"status": "disabled"})
    assert env.post("/billing/redeem", {"code": "PAUSED"}, token=t1)[1]["code"] == "inactive"
    assert env.post("/billing/redeem", {"code": "NOPE"}, token=t1)[0] == 404


def test_access_code_refused_when_the_paid_plan_already_covers_it(env):
    uid, tok = env.signup("payer@x.io")
    now = int(time.time())
    ent.set_subscription(uid, plan="pro_plus", status="active", provider="razorpay",
                         period_start=now - 60, period_end=now + 30 * DAY)
    make(code="PROFREE")
    code, out = env.post("/billing/redeem", {"code": "PROFREE"}, token=tok)
    assert code == 409 and out["code"] == "has_plan"


def test_a_lower_code_never_hides_a_better_grant(env):
    uid, tok = env.signup("dev@x.io")
    ent.grant(uid, plan="pro_plus", reason="developer", granted_by="t")
    make(code="PRO7", access_days=7)
    assert env.post("/billing/redeem", {"code": "PRO7"}, token=tok)[0] == 200
    assert ent.plan_of(uid) == "pro_plus"


def test_revoke_takes_the_time_back(env):
    uid, tok = env.signup("a@x.io")
    c = make(code="TAKEBACK")
    env.post("/billing/redeem", {"code": "TAKEBACK"}, token=tok)
    rid = coupons.redemptions(c["id"])[0]["id"]
    coupons.revoke(rid)
    assert ent.plan_of(uid) == "free"
    assert coupons.redemptions(c["id"])[0]["state"] == "revoked"


def test_wrong_codes_are_rate_limited_per_account(env, monkeypatch):
    import dataserver as ds
    monkeypatch.setattr(ds, "_CODE_FAILS", {})
    _, tok = env.signup("guess@x.io")
    for i in range(ds._CODE_FAIL_LIMIT):
        assert env.post("/billing/coupon", {"code": f"GUESS{i:04d}"}, token=tok)[0] == 404
    assert env.post("/billing/coupon", {"code": "GUESS9999"}, token=tok)[0] == 429


def test_signed_out_cannot_check_or_redeem(env):
    make(code="OPEN")
    assert env.post("/billing/coupon", {"code": "OPEN"})[0] == 401
    assert env.post("/billing/redeem", {"code": "OPEN"})[0] == 401


# ── discount codes at checkout ─────────────────────────────────────────────

def discount(**spec):
    return coupons.create({"kind": "discount", "plan": "pro", "percent_off": 40, **spec}, "test")[0]


def test_discount_checkout_subscribes_on_a_plan_at_the_code_price(env, rzp):
    uid, tok = env.signup("buyer@x.io")
    c = discount(code="LAUNCH40", discount_cycles=3)
    code, out = env.post("/billing/coupon", {"code": "LAUNCH40", "plan": "pro", "cycle": "monthly"}, token=tok)
    assert code == 200
    pro_m = next(o for o in out["coupon"]["offers"] if o["plan"] == "pro" and o["cycle"] == "monthly")
    assert (pro_m["was"], pro_m["amount"]) == (49900, 29900)

    code, out = env.post("/billing/checkout", {"plan": "pro", "cycle": "monthly", "code": "launch40"}, token=tok)
    assert code == 200, out
    assert (out["amount"], out["was"], out["code"]) == (29900, 49900, "LAUNCH40")
    plan_call = next(b for m, p, b in rzp.calls if p == "/plans")
    assert plan_call["item"]["amount"] == 29900 and plan_call["period"] == "monthly"
    sub_call = next(b for m, p, b in rzp.calls if p == "/subscriptions")
    assert sub_call["plan_id"] == "plan_code1" and sub_call["total_count"] == 3
    assert coupons.usage(c["id"]) == {"reserved": 1}

    # The payment must match the code's price, not the catalog's.
    sid = out["subscription_id"]
    now = int(time.time())
    rzp.subs[sid].update(status="active", current_start=now - 60, current_end=now + 30 * DAY)
    rzp.payment = {"id": "pay_1", "status": "captured", "invoice_id": "inv_1",
                   "currency": "INR", "amount": 29900, "sub": sid}
    sig = billing._hmac_hex("key-secret", f"pay_1|{sid}".encode())
    code, out = env.post("/billing/verify", {"razorpay_payment_id": "pay_1",
                                             "razorpay_subscription_id": sid,
                                             "razorpay_signature": sig}, token=tok)
    assert code == 200, out
    assert ent.plan_of(uid) == "pro"
    assert coupons.usage(c["id"]) == {"redeemed": 1}


def test_full_catalog_amount_is_refused_for_a_code_plan(env, rzp):
    _, tok = env.signup("buyer@x.io")
    discount(code="HALF", percent_off=50)
    out = env.post("/billing/checkout", {"plan": "pro", "code": "HALF"}, token=tok)[1]
    sid = out["subscription_id"]
    now = int(time.time())
    rzp.subs[sid].update(status="active", current_start=now - 60, current_end=now + 30 * DAY)
    rzp.payment = {"id": "pay_2", "status": "captured", "invoice_id": "inv_2",
                   "currency": "INR", "amount": 49900, "sub": sid}
    sig = billing._hmac_hex("key-secret", f"pay_2|{sid}".encode())
    code, _ = env.post("/billing/verify", {"razorpay_payment_id": "pay_2",
                                           "razorpay_subscription_id": sid,
                                           "razorpay_signature": sig}, token=tok)
    assert code == 400


def test_last_use_is_held_by_an_open_checkout_and_freed_when_it_dies(env, rzp):
    _, t1 = env.signup("first@x.io")
    _, t2 = env.signup("second@x.io")
    c = discount(code="LASTONE", max_redemptions=1)
    out = env.post("/billing/checkout", {"plan": "pro", "code": "LASTONE"}, token=t1)[1]
    code, out2 = env.post("/billing/checkout", {"plan": "pro", "code": "LASTONE"}, token=t2)
    assert code == 409 and out2["code"] == "used_up"
    # the first checkout is abandoned and dies at the provider
    sid = out["subscription_id"]
    rzp.subs[sid]["status"] = "cancelled"
    raw = json.dumps({"event": "subscription.cancelled", "payload": {
        "subscription": {"entity": rzp.subs[sid]}}}).encode()
    env.call("POST", "/billing/webhook", raw=raw, headers={
        "X-Razorpay-Signature": _sign(raw, "hook-secret"), "x-razorpay-event-id": "evt_c"})
    assert coupons.usage(c["id"]) == {"released": 1}
    assert env.post("/billing/checkout", {"plan": "pro", "code": "LASTONE"}, token=t2)[0] == 200


def test_resume_keeps_the_price_and_another_code_replaces_an_unpaid_checkout(env, rzp):
    _, tok = env.signup("buyer@x.io")
    discount(code="TENOFF", percent_off=10)
    discount(code="TWENTY", percent_off=20)
    a = env.post("/billing/checkout", {"plan": "pro", "code": "TENOFF"}, token=tok)[1]
    again = env.post("/billing/checkout", {"plan": "pro", "code": "TENOFF"}, token=tok)[1]
    assert again["resumed"] and again["subscription_id"] == a["subscription_id"]
    assert again["amount"] == 44900
    b = env.post("/billing/checkout", {"plan": "pro", "code": "TWENTY"}, token=tok)
    assert b[0] == 200 and b[1]["subscription_id"] != a["subscription_id"], b
    assert rzp.subs[a["subscription_id"]]["status"] == "cancelled"
    assert b[1]["amount"] == 39900
    plain = env.post("/billing/checkout", {"plan": "pro"}, token=tok)[1]
    assert plain["amount"] == 49900 and "code" not in plain


def test_code_kinds_go_to_their_own_door(env, rzp):
    _, tok = env.signup("buyer@x.io")
    make(code="FREEPRO")
    discount(code="CHEAPER")
    assert env.post("/billing/checkout", {"plan": "pro", "code": "FREEPRO"}, token=tok)[1]["code"] == "use_redeem"
    assert env.post("/billing/redeem", {"code": "CHEAPER"}, token=tok)[1]["code"] == "use_checkout"
    discount(code="PPONLY", plan="pro_plus", cycles="annual")
    out = env.post("/billing/checkout", {"plan": "pro", "code": "PPONLY"}, token=tok)[1]
    assert out["code"] == "wrong_plan"


def test_new_customers_only(env, rzp):
    uid, tok = env.signup("returning@x.io")
    now = int(time.time())
    ent.set_subscription(uid, plan="pro", status="expired", provider="razorpay",
                         period_start=now - 60 * DAY, period_end=now - 30 * DAY)
    discount(code="WELCOME", new_only=True)
    assert env.post("/billing/coupon", {"code": "WELCOME"}, token=tok)[1]["code"] == "new_only"


def test_create_validation(env):
    with pytest.raises(coupons.CodeError):
        coupons.create({"kind": "discount", "plan": "pro", "percent_off": 40, "amount_off": 1000}, "t")
    with pytest.raises(coupons.CodeError):
        coupons.create({"kind": "discount", "plan": "pro", "percent_off": 99}, "t")
    with pytest.raises(coupons.CodeError):
        coupons.create({"kind": "access", "plan": "free", "access_days": 3}, "t")
    with pytest.raises(coupons.CodeError):
        coupons.create({"kind": "access", "plan": "pro", "access_days": 3, "emails": "not-an-email"}, "t")
    c = make(code="FIXED")
    with pytest.raises(coupons.CodeError) as e:
        coupons.update(c["id"], {"access_days": 90})
    assert e.value.code == "fixed"
    with pytest.raises(coupons.CodeError) as e:
        make(code="FIXED")
    assert e.value.code == "exists"
    batch = coupons.create({"kind": "access", "plan": "pro", "access_days": 14, "count": 50,
                            "prefix": "CREATOR"}, "t")
    assert len({b["code"] for b in batch}) == 50
    assert all(b["code"].startswith("CREATOR-") and b["batch"] == batch[0]["batch"] for b in batch)
    assert coupons.bulk_status(batch[0]["batch"], "disabled") == 50


# ── the owner API ──────────────────────────────────────────────────────────

def signed(env, method, path, body=None, key=KEY, nonce=None, ts=None):
    raw = json.dumps(body).encode() if body is not None else b""
    ts = str(ts or int(time.time()))
    nonce = nonce or secrets.token_urlsafe(18)
    sig = ops_api.sign(key.encode(), method, path, ts, nonce, raw)
    return env.call(method, path, raw=raw if method == "POST" else None, headers={
        "X-Ops-Timestamp": ts, "X-Ops-Nonce": nonce, "X-Ops-Signature": sig,
        "X-Ops-Actor": "owner", "X-Ops-Client-IP": "203.0.113.9"})


def test_ops_routes_do_not_exist_without_a_key_or_a_signature(env, monkeypatch):
    monkeypatch.delenv("PIVOT_OPS_KEY", raising=False)
    assert signed(env, "GET", "/ops/overview")[0] == 404
    monkeypatch.setenv("PIVOT_OPS_KEY", "short")
    assert signed(env, "GET", "/ops/overview", key="short")[0] == 404
    monkeypatch.setenv("PIVOT_OPS_KEY", KEY)
    assert env.get("/ops/overview")[0] == 404
    assert signed(env, "GET", "/ops/overview", key="x" * 48)[0] == 404
    assert signed(env, "GET", "/ops/overview", ts=int(time.time()) - 300)[0] == 404
    assert signed(env, "GET", "/ops/overview")[0] == 200


def test_ops_refuses_a_replay_and_a_tampered_body(env, monkeypatch):
    monkeypatch.setenv("PIVOT_OPS_KEY", KEY)
    n = secrets.token_urlsafe(18)
    assert signed(env, "GET", "/ops/overview", nonce=n)[0] == 200
    assert signed(env, "GET", "/ops/overview", nonce=n)[0] == 404
    raw = json.dumps({"kind": "access", "plan": "pro", "access_days": 30}).encode()
    ts, nonce = str(int(time.time())), secrets.token_urlsafe(18)
    sig = ops_api.sign(KEY.encode(), "POST", "/ops/coupons", ts, nonce, raw)
    evil = raw.replace(b"30", b"3000")
    code, _ = env.call("POST", "/ops/coupons", raw=evil, headers={
        "X-Ops-Timestamp": ts, "X-Ops-Nonce": nonce, "X-Ops-Signature": sig})
    assert code == 404


def test_ops_ip_allow_list(env, monkeypatch):
    monkeypatch.setenv("PIVOT_OPS_KEY", KEY)
    monkeypatch.setenv("PIVOT_OPS_ALLOWED_IPS", "198.51.100.0/24")
    assert signed(env, "GET", "/ops/overview")[0] == 404     # the test client is 127.0.0.1
    monkeypatch.setenv("PIVOT_OPS_ALLOWED_IPS", "198.51.100.0/24,127.0.0.1")
    assert signed(env, "GET", "/ops/overview")[0] == 200


def test_ops_end_to_end_codes_users_grants_and_audit(env, monkeypatch):
    monkeypatch.setenv("PIVOT_OPS_KEY", KEY)
    uid, tok = env.signup("member@x.io")
    code, out = signed(env, "POST", "/ops/coupons", {"kind": "access", "plan": "pro_plus",
                                                     "access_days": 14, "code": "PRESS14",
                                                     "max_redemptions": 10, "campaign": "press"})
    assert code == 200 and out["coupons"][0]["code"] == "PRESS14", out
    cid = out["coupons"][0]["id"]
    assert env.post("/billing/redeem", {"code": "PRESS14"}, token=tok)[0] == 200
    code, out = signed(env, "GET", f"/ops/coupons/{cid}")
    assert out["redemptions"][0]["email"] == "member@x.io"
    code, out = signed(env, "GET", "/ops/coupons")
    assert out["coupons"][0]["redeemed"] == 1
    code, out = signed(env, "GET", "/ops/users?q=member")
    assert out["users"][0]["plan"] == "pro_plus"
    code, out = signed(env, "POST", f"/ops/users/{uid}/grant", {"feature": "ai.credits",
                                                                "value": "500", "days": 30,
                                                                "reason": "beta tester"})
    assert code == 200, out
    code, out = signed(env, "GET", f"/ops/users/{uid}")
    gids = [g["id"] for g in out["user"]["grants"] if g["plan"] == "pro_plus"]
    code, out = signed(env, "POST", f"/ops/grants/{gids[0]}/revoke", {})
    assert code == 200 and out["plan"] == "free"
    code, out = signed(env, "POST", f"/ops/users/{uid}/signout", {})
    assert out["sessions_ended"] >= 1
    assert env.get("/billing/me", token=tok)[1]["plan"] == "anonymous"
    code, out = signed(env, "GET", "/ops/overview")
    assert out["users"]["total"] == 1 and out["codes"]["total"] == 1
    assert out["codes"]["redeemed_month"] == 0          # the revoke took it back
    code, out = signed(env, "GET", "/ops/audit")
    actions = [a["action"] for a in out["audit"]]
    assert actions[:4] == ["users/{}/signout".format(uid), "grants/{}/revoke".format(gids[0]),
                           f"users/{uid}/grant", "coupons"], actions
    assert all(a["actor"] == "owner" and a["ip"] == "203.0.113.9" for a in out["audit"])
    assert signed(env, "POST", "/ops/coupons/999999", {"note": "x"})[0] == 404


def test_console_round_trip_against_the_real_handler(env, monkeypatch):
    """owner-console/app.py signs; this dataserver verifies. Same key, real HTTP."""
    import importlib.util
    import io
    import pathlib
    import urllib.parse

    spec = importlib.util.spec_from_file_location(
        "owner_console_app", pathlib.Path(__file__).resolve().parents[2] / "owner-console" / "app.py")
    console = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(console)
    monkeypatch.setenv("PIVOT_OPS_KEY", KEY)
    monkeypatch.setenv("OPS_HMAC_KEY", KEY)
    monkeypatch.setenv("OPS_UPSTREAM", env.base)
    monkeypatch.setenv("OPS_PASSWORD_HASH", console.hash_password("pw-for-tests-only", n=2 ** 12))
    monkeypatch.setenv("OPS_ACTOR", "owner")

    def wsgi(method, path, body=b"", cookie="", headers=None):
        e = {"REQUEST_METHOD": method, "PATH_INFO": path.split("?")[0],
             "QUERY_STRING": path.split("?")[1] if "?" in path else "",
             "HTTP_HOST": "c.test", "HTTP_USER_AGENT": "t", "HTTP_X_FORWARDED_FOR": "198.51.100.4:1",
             "CONTENT_LENGTH": str(len(body)), "wsgi.input": io.BytesIO(body),
             "HTTP_ORIGIN": "https://c.test", **({"HTTP_COOKIE": f"{console.COOKIE}={cookie}"} if cookie else {}),
             **{"HTTP_" + k.upper().replace("-", "_"): v for k, v in (headers or {}).items()}}
        got = {}
        out = b"".join(console.app(e, lambda s, h: got.update(s=int(s.split()[0]), h=dict(h))))
        return got["s"], got["h"], out

    monkeypatch.setattr(console.time, "sleep", lambda s: None)
    s, h, _ = wsgi("POST", "/login", urllib.parse.urlencode({"password": "pw-for-tests-only"}).encode())
    assert s == 303
    cookie = h["Set-Cookie"].split(";")[0].split("=", 1)[1]
    csrf = json.loads(wsgi("GET", "/api/session", cookie=cookie)[2])["csrf"]
    s, _, out = wsgi("POST", "/api/coupons", json.dumps({"kind": "access", "plan": "pro", "access_days": 30,
                                                       "code": "ROUNDTRIP"}).encode(),
                     cookie=cookie, headers={"X-CSRF": csrf})
    assert s == 200, out
    s, _, out = wsgi("GET", "/api/coupons?q=ROUND", cookie=cookie)
    assert s == 200 and json.loads(out)["coupons"][0]["code"] == "ROUNDTRIP"
    s, _, out = wsgi("GET", "/api/overview", cookie=cookie)
    assert s == 200 and "users" in json.loads(out)
    audit = signed(env, "GET", "/ops/audit")[1]["audit"]
    assert audit[0]["action"] == "coupons" and audit[0]["ip"] == "198.51.100.4"
    # a console holding the wrong key is refused, and says why
    monkeypatch.setenv("OPS_HMAC_KEY", "z" * 48)
    s, _, out = wsgi("GET", "/api/overview", cookie=cookie)
    assert s == 502 and "signature" in json.loads(out)["error"]
