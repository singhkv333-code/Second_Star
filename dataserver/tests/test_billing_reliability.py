"""Payment lifecycle against an isolated SQLite plane; no money/network."""
import concurrent.futures
import hashlib
import hmac
import json
import sqlite3
import threading
import time

import pytest

import billing
import entitlements as ent


@pytest.fixture
def account(monkeypatch):
    db = sqlite3.connect(":memory:", check_same_thread=False)
    db.execute("CREATE TABLE users(id INTEGER PRIMARY KEY,email TEXT)")
    db.execute("INSERT INTO users VALUES (1,'billing-test@pivotnow.in'),(2,'other@pivotnow.in')")
    ent.bind(db, threading.Lock())
    for key, value in {
        "RAZORPAY_KEY_ID": "rzp_test_fixture", "RAZORPAY_KEY_SECRET": "test-secret",
        "RAZORPAY_WEBHOOK_SECRET": "hook-secret", "RAZORPAY_PLAN_PRO_MONTHLY": "plan_pro",
        "RAZORPAY_PLAN_PRO_PLUS_MONTHLY": "plan_plus",
        "PIVOT_BILLING_TEST_EMAILS": "billing-test@pivotnow.in",
    }.items():
        monkeypatch.setenv(key, value)
    now = int(time.time())
    state = {"entity": {"id": "sub_test", "plan_id": "plan_pro", "status": "active",
                         "current_start": now, "current_end": now + 30 * 86400},
             "payment": {"id": "pay_test", "invoice_id": "inv_test", "status": "captured",
                         "amount": 59900, "currency": "INR"}, "calls": [], "fail": False}

    def wire(method, path, body=None):
        state["calls"].append((method, path, body))
        if state["fail"]:
            raise billing.BillingError("provider unavailable")
        if method == "POST" and path == "/subscriptions":
            return {"id": "sub_test"}
        if path == "/subscriptions/sub_test":
            return state["entity"].copy()
        if path == "/payments/pay_test":
            return state["payment"].copy()
        if path == "/invoices/inv_test":
            return {"subscription_id": state.get("invoice_sub", "sub_test"), "payment_id": "pay_test"}
        raise AssertionError((method, path))

    monkeypatch.setattr(billing, "http", wire)
    monkeypatch.setattr(billing, "on_downgrade", None)
    yield db, state
    db.close()


def checkout():
    return billing.api_checkout(1, "billing-test@pivotnow.in", {"plan": "pro", "cycle": "monthly"})


def callback():
    return {"razorpay_payment_id": "pay_test", "razorpay_subscription_id": "sub_test",
            "razorpay_signature": hmac.new(b"test-secret", b"pay_test|sub_test", hashlib.sha256).hexdigest()}


def event(entity, eid="evt_test"):
    raw = json.dumps({"event": "subscription.charged", "payload": {"subscription": {"entity": entity}}}).encode()
    return billing.api_webhook(raw, {"X-Razorpay-Signature": hmac.new(b"hook-secret", raw, hashlib.sha256).hexdigest(),
                                     "x-razorpay-event-id": eid})


def test_test_keys_cannot_sell_to_ordinary_customers(account):
    assert billing.api_checkout(2, "other@pivotnow.in", {"plan": "pro"})[0] == 403
    assert not account[1]["calls"]


def test_missing_webhook_configuration_disables_checkout(account, monkeypatch):
    monkeypatch.delenv("RAZORPAY_WEBHOOK_SECRET")
    assert checkout()[0] == 503


def test_duplicate_click_and_concurrent_requests_reuse_one_mandate(account):
    account[1]["entity"]["status"] = "created"
    with concurrent.futures.ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: checkout(), range(2)))
    assert [r[0] for r in results] == [200, 200]
    assert len([c for c in account[1]["calls"] if c[:2] == ("POST", "/subscriptions")]) == 1
    assert ent.plan_of(1) == "free"


def test_uncertain_create_never_silently_creates_another_mandate(account):
    account[1]["fail"] = True
    assert checkout()[0] == 502
    account[1]["fail"] = False
    assert checkout()[0] == 409
    assert len(account[1]["calls"]) == 1


def test_forged_signature_or_wrong_owner_cannot_unlock(account):
    checkout()
    assert billing.api_verify(1, {**callback(), "razorpay_signature": "0" * 64})[0] == 400
    assert billing.api_verify(2, callback())[0] == 403
    assert ent.plan_of(1) == "free"


@pytest.mark.parametrize("status", ["authorized", "failed", "created"])
def test_uncaptured_payment_never_grants_access(account, status):
    checkout()
    account[1]["payment"]["status"] = status
    assert billing.api_verify(1, callback())[0] == 202
    assert ent.plan_of(1) == "free"


@pytest.mark.parametrize("change", [{"amount": 100}, {"currency": "USD"}])
def test_wrong_price_or_currency_cannot_unlock(account, change):
    checkout()
    account[1]["payment"].update(change)
    assert billing.api_verify(1, callback())[0] == 400
    assert ent.plan_of(1) == "free"


def test_payment_invoice_must_belong_to_this_subscription(account):
    checkout()
    account[1]["invoice_sub"] = "sub_other"
    assert billing.api_verify(1, callback())[0] == 400
    assert ent.plan_of(1) == "free"


def test_capture_verification_activates_and_is_idempotent(account):
    checkout()
    for _ in range(2):
        code, result = billing.api_verify(1, callback())
        assert code == 200 and result["billing"]["plan"] == "pro"
    assert account[0].execute("SELECT COUNT(*) FROM subscriptions").fetchone()[0] == 1


def test_authorized_mandate_does_not_show_success(account):
    checkout()
    account[1]["entity"]["status"] = "authenticated"
    assert billing.api_verify(1, callback())[0] == 202
    assert ent.plan_of(1) == "free"


def test_webhook_recovers_closed_browser_and_duplicate_is_noop(account):
    checkout()
    assert event(account[1]["entity"])[0] == 200
    assert ent.plan_of(1) == "pro"
    assert event(account[1]["entity"])[1]["duplicate"] is True


def test_provider_failure_is_retryable_not_acknowledged_as_processed(account):
    checkout()
    account[1]["fail"] = True
    assert event(account[1]["entity"])[0] == 503
    account[1]["fail"] = False
    assert event(account[1]["entity"])[0] == 200
    assert ent.plan_of(1) == "pro"


def test_same_cycle_stale_pending_uses_current_provider_truth(account):
    checkout()
    old = {**account[1]["entity"], "status": "pending"}
    assert event(old)[0] == 200
    assert ent.summary(1)["subscription"]["status"] == "active"


def test_crash_after_event_receipt_is_recoverable(account):
    checkout()
    entity = account[1]["entity"]
    raw = json.dumps({"event": "subscription.charged", "payload": {"subscription": {"entity": entity}}})
    assert ent.record_event("razorpay", "evt_test", "subscription.charged", raw)
    assert event(entity)[0] == 200
    assert ent.plan_of(1) == "pro"


def test_storage_failure_is_not_reported_as_duplicate(account, monkeypatch):
    def fail(*args):
        raise sqlite3.OperationalError("disk full")
    monkeypatch.setattr(ent, "record_event", fail)
    assert event(account[1]["entity"])[0] == 503


def test_bare_user_id_note_cannot_replace_an_account_subscription(account):
    entity = {**account[1]["entity"], "notes": {"user_id": "1"}}
    assert event(entity)[1].get("unmatched")
    assert ent.plan_of(1) == "free"


def test_lost_create_response_is_recovered_by_bound_checkout_token(account):
    account[1]["fail"] = True
    checkout()
    token = account[0].execute("SELECT checkout_id FROM billing_checkouts").fetchone()[0]
    account[1]["fail"] = False
    entity = {**account[1]["entity"], "notes": {"checkout_id": token, "user_id": "1"}}
    assert event(entity)[0] == 200
    assert ent.plan_of(1) == "pro"


def test_reconcile_without_callback_restores_actual_paid_access(account):
    checkout()
    assert billing.api_reconcile(1)[1]["billing"]["plan"] == "pro"


def test_event_id_cannot_be_reused_for_changed_body(account):
    checkout()
    event(account[1]["entity"])
    assert event({**account[1]["entity"], "status": "pending"})[0] == 400


def test_non_ascii_signatures_fail_closed(account):
    assert billing.api_verify(1, {**callback(), "razorpay_signature": "é" * 64})[0] == 400


def test_an_unknown_subscription_cannot_overwrite_current_one(account):
    checkout()
    assert billing._apply(1, {**account[1]["entity"], "id": "sub_old"})["ignored"] == "superseded_subscription"
