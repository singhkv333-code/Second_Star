"""Managing a subscription after checkout: plan changes, scheduled changes,
invoices, and the trial switch.

Same harness as test_entitlements_api.py (real Handler, temp account DB, fake
Razorpay wire). Run from charto/data with the paywall on:
    PAYWALL_ENABLED=1 ../../pivot/.venv/bin/python -m pytest -q test_billing_manage.py
"""
from __future__ import annotations

import time

import pytest

import billing
import entitlements as ent
from test_entitlements_api import DAY, env, hook, sub  # noqa: F401  (fixtures)


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
    state = {"plan_id": "plan_pro_m", "fail": False}

    def wire(method, path, body=None):
        calls.append((method, path, body))
        if state["fail"]:
            raise billing.BillingError("Razorpay answered 500: down")
        now = int(time.time())
        if method == "POST" and path == "/subscriptions":
            return {"id": "sub_1", "short_url": "https://rzp.io/x"}
        if method == "PATCH" and path == "/subscriptions/sub_1":
            if body["schedule_change_at"] == "now":
                state["plan_id"] = body["plan_id"]
            return {"id": "sub_1", "plan_id": state["plan_id"], "status": "active",
                    "current_start": now - DAY, "current_end": now + 29 * DAY,
                    "has_scheduled_changes": body["schedule_change_at"] != "now"}
        if method == "GET" and path.startswith("/invoices"):
            return {"items": [
                {"id": "inv_old", "issued_at": now - 40 * DAY, "amount_paid": 49900,
                 "currency": "INR", "status": "paid", "short_url": "https://rzp.io/i/old"},
                {"id": "inv_new", "issued_at": now - DAY, "amount_paid": 49900,
                 "currency": "INR", "status": "paid", "short_url": "https://rzp.io/i/new"},
            ]}
        if method == "GET" and path == "/subscriptions/sub_1":
            return {"id": "sub_1", "payment_method": "card",
                    "short_url": "https://rzp.io/x"}
        return {}

    monkeypatch.setattr(billing, "http", wire)
    wire.calls, wire.state = calls, state
    return wire


def _subscribe(env, rzp, email="payer@x.io"):
    uid, tok = env.signup(email)
    env.post("/billing/checkout", {"plan": "pro"}, token=tok)
    hook(env, "subscription.activated", sub("active"), f"evt_{email}")
    assert ent.plan_of(uid) == "pro"
    return uid, tok


def test_upgrade_applies_now(env, rzp):
    uid, tok = _subscribe(env, rzp)
    code, out = env.post("/billing/change", {"plan": "pro_plus"}, token=tok)
    assert code == 200, out
    assert out["effective"] == "now"
    assert rzp.calls[-1][2]["schedule_change_at"] == "now"
    assert ent.plan_of(uid) == "pro_plus"
    assert out["billing"]["subscription"]["pending_change"] is None


def test_cycle_switch_is_scheduled_and_can_be_undone(env, rzp):
    uid, tok = _subscribe(env, rzp)
    code, out = env.post("/billing/change", {"plan": "pro", "cycle": "annual"},
                         token=tok)
    assert code == 200, out
    assert out["effective"] == "cycle_end"
    pend = out["billing"]["subscription"]["pending_change"]
    assert pend["plan"] == "pro" and pend["cycle"] == "annual"
    assert ent.plan_of(uid) == "pro"                 # nothing moved yet
    code, out = env.post("/billing/change", {"undo": True}, token=tok)
    assert code == 200, out
    assert rzp.calls[-1][1] == "/subscriptions/sub_1/cancel_scheduled_changes"
    assert out["billing"]["subscription"]["pending_change"] is None


def test_scheduled_change_clears_when_the_provider_applies_it(env, rzp):
    uid, tok = _subscribe(env, rzp)
    env.post("/billing/change", {"plan": "pro", "cycle": "annual"}, token=tok)
    assert ent.pending_change(uid)
    later = int(time.time()) + 30 * DAY
    hook(env, "subscription.charged", sub("active", plan_id="plan_pro_y",
         current_start=later, current_end=later + 365 * DAY), "evt_renew")
    assert ent.pending_change(uid) is None
    me = env.get("/billing/me", token=tok)[1]
    assert me["subscription"]["cycle"] == "annual"


def test_change_refusals_are_honest(env, rzp):
    _, anon_tok = env.signup("nosub@x.io")
    code, out = env.post("/billing/change", {"plan": "pro"}, token=anon_tok)
    assert code == 404 and out["code"] == "no_subscription"

    uid, tok = _subscribe(env, rzp)
    code, out = env.post("/billing/change", {"plan": "free"}, token=tok)
    assert code == 400 and out["code"] == "use_cancel"
    code, out = env.post("/billing/change", {"plan": "pro", "cycle": "monthly"},
                         token=tok)
    assert code == 409 and out["code"] == "already_subscribed"

    rzp.state["fail"] = True
    code, out = env.post("/billing/change", {"plan": "pro_plus"}, token=tok)
    assert code == 502 and ent.plan_of(uid) == "pro"   # nothing changed
    rzp.state["fail"] = False

    env.post("/billing/cancel", token=tok)
    code, out = env.post("/billing/change", {"plan": "pro_plus"}, token=tok)
    assert code == 409 and out["code"] == "cancelling"


def test_change_needs_sign_in(env, rzp):
    code, _ = env.post("/billing/change", {"plan": "pro"})
    assert code == 401


def test_invoices_newest_first_with_payment_method(env, rzp):
    _, tok = _subscribe(env, rzp)
    code, out = env.get("/billing/invoices", token=tok)
    assert code == 200, out
    assert [i["id"] for i in out["invoices"]] == ["inv_new", "inv_old"]
    assert out["invoices"][0]["amount"] == 49900
    assert out["payment_method"] == "card"
    assert env.get("/billing/invoices")[0] == 401


def test_invoices_for_someone_who_never_paid_is_empty(env, rzp):
    _, tok = env.signup("browser@x.io")
    code, out = env.get("/billing/invoices", token=tok)
    assert code == 200 and out["invoices"] == []


def test_without_keys_management_is_an_honest_503(env, monkeypatch):
    monkeypatch.delenv("RAZORPAY_KEY_ID", raising=False)
    _, tok = env.signup("x@x.io")
    assert env.post("/billing/change", {"plan": "pro"}, token=tok)[0] == 503
    assert env.get("/billing/invoices", token=tok)[0] == 503


def test_trial_is_off_until_the_catalog_sets_days(env, monkeypatch):
    assert env.get("/billing/plans")[1]["trial_days"] is None
    monkeypatch.setitem(ent.CATALOG, "trial", {"days": 7})
    assert env.get("/billing/plans")[1]["trial_days"] == 7
    assert env.get("/billing/me")[1]["trial_days"] == 7
