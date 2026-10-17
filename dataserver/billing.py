"""The dataserver — taking money for a plan, through Razorpay Subscriptions.

WHAT THIS FILE OWNS, AND WHAT IT DOES NOT
-----------------------------------------
It moves a user between plans in response to a PAYMENT PROVIDER. It never
decides what a plan allows (`entitlements.py` does), and nothing outside it
talks to Razorpay.

THE FLOW
--------
    checkout   POST /billing/checkout {plan, cycle}
               → we create a Razorpay subscription for the plan id configured
                 for (plan, cycle) and answer {subscription_id, key_id} for
                 Checkout.js. Nothing is granted yet.
    verify     POST /billing/verify {razorpay_payment_id,
                                     razorpay_subscription_id,
                                     razorpay_signature}
               → HMAC-SHA256(key_secret, payment_id|subscription_id) must
                 match. Then we FETCH the subscription from Razorpay, so the
                 plan and period come from the provider, never from the
                 browser, and activate it. This is only the fast path: the
                 user sees Pro the moment the payment clears.
    webhook    POST /billing/webhook (raw body)
               → HMAC-SHA256(webhook_secret, raw body) must match
                 X-Razorpay-Signature. Each event is recorded under its
                 x-razorpay-event-id before it is applied, so a redelivery is
                 acknowledged and ignored. This is the source of truth for
                 renewals, failures and cancellations.
    cancel     POST /billing/cancel → cancel at cycle end. The user keeps what
               they paid for until period_end; entitlements handles that.
    change     POST /billing/change {plan, cycle} | {undo: true}
               → PATCH the subscription to another Razorpay plan id. An
                 UPGRADE applies now (Razorpay charges the difference); a
                 downgrade or a cycle switch is scheduled for the cycle end,
                 so nobody loses days they paid for. The scheduled change is
                 kept on our row (`pending_change`) until the provider says it
                 happened, and `undo` withdraws it.
    invoices   GET /billing/invoices → the subscription's invoices as Razorpay
               lists them, the payment method on the mandate, and the hosted
               link where the customer manages it. Read-only.

WITHOUT KEYS
------------
Every route answers 503 "billing is not configured" when the environment has
no Razorpay keys. That is the honest boundary: the plans still exist, admin
grants still work, and nothing pretends a payment happened.

Environment:
    RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET, RAZORPAY_WEBHOOK_SECRET
    RAZORPAY_PLAN_PRO_MONTHLY, RAZORPAY_PLAN_PRO_ANNUAL,
    RAZORPAY_PLAN_PRO_PLUS_MONTHLY, RAZORPAY_PLAN_PRO_PLUS_ANNUAL
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time
import secrets
import sqlite3
import threading
import urllib.error
import urllib.request
from os import environ

import entitlements as ent

log = logging.getLogger("dataserver.billing")

API = "https://api.razorpay.com/v1"
PROVIDER = "razorpay"
# Only billing operations serialize here. Market-data reads/ticks and the
# shared user-store lock are never held while waiting on Razorpay.
_operations = threading.RLock()
CYCLES = ("monthly", "annual")
# How many renewals a subscription is authorised for. Razorpay requires a
# number; ten years of monthly or ten annual renewals is effectively "until
# cancelled" without asking the bank to authorise an unbounded mandate.
TOTAL_COUNT = {"monthly": 120, "annual": 10}

# Razorpay's subscription status → ours. `authenticated` means the mandate is
# set up but nothing has been charged, so it grants nothing yet.
_STATUS = {
    "created": "created", "authenticated": "created", "active": "active",
    "pending": "past_due", "halted": "expired", "cancelled": "cancelled",
    "completed": "expired", "expired": "expired", "paused": "expired",
}


def _env(name: str) -> str:
    return (environ.get(name) or "").strip()


def configured() -> bool:
    return bool(_env("RAZORPAY_KEY_ID") and _env("RAZORPAY_KEY_SECRET")
                and _env("RAZORPAY_WEBHOOK_SECRET") and plan_ids())


def test_mode() -> bool:
    return _env("RAZORPAY_KEY_ID").startswith("rzp_test_")


def checkout_available(email: str = "") -> bool:
    if not configured():
        return False
    # Test payments must never grant free paid access to ordinary customers.
    allowed = {x.strip().lower() for x in _env("PIVOT_BILLING_TEST_EMAILS").split(",") if x.strip()}
    return not test_mode() or email.strip().lower() in allowed


def plan_ids() -> dict[tuple[str, str], str]:
    out = {}
    for plan in ("pro", "pro_plus"):
        for cycle in CYCLES:
            pid = _env(f"RAZORPAY_PLAN_{plan.upper()}_{cycle.upper()}")
            if pid:
                out[(plan, cycle)] = pid
    return out


def _plan_for_id(pid: str) -> tuple[str, str] | None:
    for k, v in plan_ids().items():
        if v == pid:
            return k
    return None


# ── the wire, replaceable in tests ─────────────────────────────────────────

def _http(method: str, path: str, body: dict | None = None) -> dict:
    auth = base64.b64encode(
        f"{_env('RAZORPAY_KEY_ID')}:{_env('RAZORPAY_KEY_SECRET')}".encode()).decode()
    req = urllib.request.Request(
        API + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Basic {auth}",
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as exc:
        raise BillingError(f"Razorpay answered HTTP {exc.code}") from None
    except (OSError, ValueError) as exc:
        raise BillingError(f"Razorpay connection unavailable ({type(exc).__name__})") from None


http = _http


class BillingError(Exception):
    pass


_NOT_CONFIGURED = (503, {"error": "Billing is not configured on this server.",
                         "code": "billing_unavailable"})


# ── signatures ─────────────────────────────────────────────────────────────

def _hmac_hex(secret: str, msg: bytes) -> str:
    return hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()


def verify_webhook_signature(raw: bytes, signature: str, secret: str) -> bool:
    if not secret or not signature or len(signature) != 64 or not signature.isascii():
        return False
    return hmac.compare_digest(_hmac_hex(secret, raw), signature.strip())


def verify_payment_signature(payment_id: str, sub_id: str, signature: str,
                             key_secret: str) -> bool:
    if not (payment_id and sub_id and signature and key_secret) or len(signature) != 64 or not signature.isascii():
        return False
    return hmac.compare_digest(
        _hmac_hex(key_secret, f"{payment_id}|{sub_id}".encode()), signature.strip())


# ── applying a provider subscription to our row ────────────────────────────

def _apply(uid: int, entity: dict) -> dict:
    """Write what Razorpay says about a subscription onto the user's row."""
    pc = _plan_for_id(str(entity.get("plan_id") or ""))
    if not pc:
        raise BillingError(f"unknown Razorpay plan id {entity.get('plan_id')}")
    plan, cycle = pc
    status = _STATUS.get(str(entity.get("status") or ""), "expired")
    ps = entity.get("current_start") or None
    pe = entity.get("current_end") or None
    # Webhooks arrive at least once and in no promised order. An event about
    # an EARLIER billing cycle than the one on file (a late `pending` for the
    # cycle a later `charged` already paid) must not overwrite it.
    with ent._lock:
        cur = ent._con.execute(
            "SELECT provider_sub_id, period_end, status, grace_until FROM subscriptions WHERE "
            "user_id=?", (uid,)).fetchone()
    if cur and cur[0] and cur[0] != str(entity.get("id")):
        return {"ignored": "superseded_subscription"}
    # Grace is for an existing paid renewal, not a failed first purchase.
    if status == "past_due" and (not cur or cur[2] not in ("active", "past_due", "cancelled")):
        status = "created"
    if status == "active" and (not ps or not pe or int(pe) <= int(ps)):
        raise BillingError("Provider has not confirmed a paid billing period")
    if cur and cur[0] == str(entity.get("id")) and cur[1] and pe \
            and int(pe) < int(cur[1]):
        return {"ignored": "stale", "period_end": cur[1]}
    grace = None
    if status == "past_due":
        grace = (cur[3] if cur and cur[2] == "past_due" and cur[1] == pe and cur[3]
                 else min(int(pe or time.time()), int(time.time())) + ent.GRACE_DAYS * 86400)
    elif status == "active" and pe:
        grace = int(pe) + ent.GRACE_DAYS * 86400      # a late renewal webhook
    with ent._lock:
        prev = ent._con.execute(
            "SELECT plan, cycle, status, period_end FROM subscriptions WHERE user_id=?",
            (uid,)).fetchone()
    ent.set_subscription(
        uid, plan=plan, status=status, cycle=cycle, provider=PROVIDER,
        provider_sub_id=str(entity.get("id")), period_start=ps, period_end=pe,
        cancel_at_period_end=None, grace_until=grace)
    if on_subscription_change:
        try:
            on_subscription_change(
                uid,
                dict(zip(("plan", "cycle", "status", "period_end"), prev)) if prev else None,
                {"plan": plan, "cycle": cycle, "status": status, "period_start": ps,
                 "period_end": pe, "grace_until": grace},
                entity)
        except Exception as exc:                        # noqa: BLE001 — mail never blocks billing
            log.warning("subscription hook failed for %s: %s", uid, exc)
    # A scheduled change is done once the provider reports the plan it named,
    # or reports that nothing is scheduled any more.
    pend = ent.pending_change(uid)
    if pend and ((pend["plan"], pend["cycle"]) == (plan, cycle)
                 or entity.get("has_scheduled_changes") is False):
        ent.set_pending_change(uid, None)
    return {"plan": plan, "cycle": cycle, "status": status,
            "period_end": pe}


# ── routes ─────────────────────────────────────────────────────────────────

def api_checkout(uid: int, email: str, body: dict) -> tuple[int, dict]:
    with _operations:
        return _checkout(uid, email, body)


def _checkout(uid: int, email: str, body: dict) -> tuple[int, dict]:
    if not configured():
        return _NOT_CONFIGURED
    if not checkout_available(email):
        return 403, {"error": "Test checkout is restricted to approved test accounts.",
                     "code": "test_checkout_restricted"}
    plan = str(body.get("plan") or "")
    cycle = str(body.get("cycle") or "monthly")
    if cycle not in CYCLES:
        return 400, {"error": f"cycle must be one of {', '.join(CYCLES)}"}
    pid = plan_ids().get((plan, cycle))
    if not pid:
        return 400, {"error": f"no {cycle} price is configured for plan '{plan}'"}
    cur = ent.summary(uid)["subscription"]
    if cur and cur["provider"] == PROVIDER \
            and ent.plan_of(uid) != "free":
        return 409, {"error": "You already have a subscription. Change plan "
                              "instead of starting a second one.",
                     "code": "already_subscribed"}
    with ent._lock:
        attempt = ent._con.execute("SELECT checkout_id,plan,cycle,state,provider_sub_id "
                                   "FROM billing_checkouts WHERE user_id=?", (uid,)).fetchone()
    if attempt:
        if attempt[3] != "ready" or not attempt[4]:
            return 409, {"error": "Your previous checkout is being reconciled. Do not pay again.",
                         "code": "checkout_pending"}
        # Resume the same mandate after dismissal, double click or reload.
        # A plan switch must not quietly leave another payable mandate behind.
        try:
            existing = http("GET", f"/subscriptions/{attempt[4]}")
        except BillingError:
            return 503, {"error": "Could not check your existing checkout. Please retry shortly.",
                         "code": "checkout_pending"}
        if existing.get("status") in ("cancelled", "expired", "completed"):
            with ent._lock:
                ent._con.execute("DELETE FROM billing_checkouts WHERE user_id=?", (uid,))
                ent._con.commit()
        elif existing.get("status") in ("active", "pending"):
            _apply(uid, existing)
            return 409, {"error": "This subscription is already being confirmed. Check your billing status before paying again.",
                         "code": "checkout_pending"}
        elif (attempt[1], attempt[2]) != (plan, cycle):
            return 409, {"error": "Finish or cancel your existing checkout before choosing another plan.",
                         "code": "checkout_pending"}
        else:
            return 200, {"subscription_id": attempt[4], "key_id": _env("RAZORPAY_KEY_ID"),
                         "plan": plan, "cycle": cycle, "test_mode": test_mode(),
                         "prefill": {"email": email}, "resumed": True}
    checkout_id = secrets.token_hex(24)
    with ent._lock:
        ent._con.execute("INSERT INTO billing_checkouts "
                         "(user_id,checkout_id,plan,cycle,state,created,updated) "
                         "VALUES (?,?,?,?,'creating',?,?)",
                         (uid, checkout_id, plan, cycle, int(time.time()), int(time.time())))
        ent._con.commit()
    try:
        sub = http("POST", "/subscriptions", {
            "plan_id": pid, "total_count": TOTAL_COUNT[cycle],
            "customer_notify": 1,
            "notes": {"user_id": str(uid), "checkout_id": checkout_id}})
        if not isinstance(sub.get("id"), str) or not sub["id"].startswith("sub_"):
            raise BillingError("Missing provider subscription ID")
    except BillingError as exc:
        with ent._lock:
            ent._con.execute("UPDATE billing_checkouts SET state='uncertain',updated=? "
                             "WHERE user_id=?", (int(time.time()), uid))
            ent._con.commit()
        log.warning("checkout failed for %s: %s", uid, exc)
        return 502, {"error": "Checkout could not be confirmed. Do not start another payment; "
                              "contact Pivot to reconcile it.", "code": "checkout_pending"}
    ent.set_subscription(uid, plan=plan, status="created", cycle=cycle,
                         provider=PROVIDER, provider_sub_id=str(sub["id"]),
                         cancel_at_period_end=False)
    with ent._lock:
        ent._con.execute("UPDATE billing_checkouts SET state='ready',provider_sub_id=?,updated=? "
                         "WHERE user_id=?", (sub["id"], int(time.time()), uid))
        ent._con.commit()
    return 200, {"subscription_id": sub["id"], "key_id": _env("RAZORPAY_KEY_ID"),
                 "short_url": sub.get("short_url"), "plan": plan,
                 "cycle": cycle, "test_mode": test_mode(), "prefill": {"email": email}}


def api_verify(uid: int, body: dict) -> tuple[int, dict]:
    with _operations:
        return _verify(uid, body)


def _verify(uid: int, body: dict) -> tuple[int, dict]:
    if not configured():
        return _NOT_CONFIGURED
    pay = str(body.get("razorpay_payment_id") or "")
    sid = str(body.get("razorpay_subscription_id") or "")
    sig = str(body.get("razorpay_signature") or "")
    if not verify_payment_signature(pay, sid, sig, _env("RAZORPAY_KEY_SECRET")):
        return 400, {"error": "The payment could not be verified.",
                     "code": "bad_signature"}
    if ent.user_for_provider_sub(PROVIDER, sid) != uid:
        return 403, {"error": "That subscription belongs to another account."}
    try:
        entity = http("GET", f"/subscriptions/{sid}")
        payment = http("GET", f"/payments/{pay}")
        if payment.get("status") != "captured":
            return 202, {"pending": True, "note": "Payment is not captured yet. Your plan is not active."}
        invoice_id = payment.get("invoice_id")
        if not isinstance(invoice_id, str) or not invoice_id.startswith("inv_"):
            raise BillingError("Provider payment has no subscription invoice")
        invoice = http("GET", f"/invoices/{invoice_id}")
        pc = _plan_for_id(str(entity.get("plan_id") or ""))
        if not pc or invoice.get("subscription_id") != sid or invoice.get("payment_id") != pay:
            return 400, {"error": "Payment does not match this subscription."}
        expected = ent.CATALOG["plans"][pc[0]]["prices"][pc[1]]["amount"]
        if payment.get("currency") != "INR" or payment.get("amount") != expected:
            return 400, {"error": "Payment amount or currency does not match this plan."}
        out = _apply(uid, entity)
    except BillingError as exc:
        # The signature proved a payment; the webhook will still land it.
        log.warning("verify fetch failed for %s: %s", sid, exc)
        return 202, {"pending": True, "note": "Payment received. Your plan "
                     "will switch as soon as the provider confirms it."}
    summary = ent.summary(uid)
    if out.get("status") != "active" or summary["plan"] == "free":
        return 202, {"pending": True, "note": "Waiting for the subscription to become active.",
                     "billing": summary}
    return 200, {"ok": True, **out, "billing": summary}


def api_reconcile(uid: int) -> tuple[int, dict]:
    """Recover a closed browser or missing callback from the provider's state.

    No client-supplied subscription/price is accepted. Razorpay's current
    active subscription is authoritative; an authenticated mandate is not.
    """
    if not configured():
        return _NOT_CONFIGURED
    with _operations:
        sid = _provider_sub_id(uid)
        if not sid:
            return 200, {"billing": ent.summary(uid)}
        try:
            out = _apply(uid, http("GET", f"/subscriptions/{sid}"))
        except BillingError:
            return 503, {"error": "The payment provider is unavailable. Your payment has not been retried."}
        return 200, {**out, "billing": ent.summary(uid)}


def api_cancel(uid: int) -> tuple[int, dict]:
    if not configured():
        return _NOT_CONFIGURED
    cur = ent.summary(uid)["subscription"]
    if not cur or cur["provider"] != PROVIDER or cur["status"] not in (
            "active", "past_due", "created"):
        return 404, {"error": "You have no active subscription to cancel."}
    with ent._lock:
        row = ent._con.execute("SELECT provider_sub_id FROM subscriptions WHERE "
                               "user_id=?", (uid,)).fetchone()
    try:
        http("POST", f"/subscriptions/{row[0]}/cancel", {
            "cancel_at_cycle_end": 0 if cur["status"] == "created" else 1})
    except BillingError as exc:
        log.warning("cancel failed for %s: %s", uid, exc)
        return 502, {"error": "The payment provider did not answer. Your "
                              "subscription was not changed.", "code": "provider_error"}
    ent.set_subscription(uid, plan=cur["plan"], status=cur["status"],
                         cycle=cur["cycle"], provider=PROVIDER,
                         cancel_at_period_end=True,
                         grace_until=cur.get("grace_until"))
    # A cancelled subscription has nothing left to change into.
    ent.set_pending_change(uid, None)
    return 200, {"ok": True, "active_until": cur["period_end"],
                 "billing": ent.summary(uid)}


def _provider_sub_id(uid: int) -> str | None:
    with ent._lock:
        row = ent._con.execute("SELECT provider_sub_id FROM subscriptions WHERE "
                               "user_id=? AND provider=?", (uid, PROVIDER)).fetchone()
    return row[0] if row and row[0] else None


def api_change(uid: int, body: dict) -> tuple[int, dict]:
    """Move a live subscription to another paid plan or billing cycle.

    Free is not a plan you change TO: that is a cancellation, and it has its
    own route so the user sees what they keep and until when."""
    if not configured():
        return _NOT_CONFIGURED
    cur = ent.summary(uid)["subscription"]
    sid = _provider_sub_id(uid)
    if not cur or not sid or cur["status"] not in ("active", "past_due"):
        return 404, {"error": "You have no active subscription to change.",
                     "code": "no_subscription"}
    if body.get("undo"):
        if not ent.pending_change(uid):
            return 404, {"error": "There is no scheduled change to undo."}
        try:
            http("POST", f"/subscriptions/{sid}/cancel_scheduled_changes", {})
        except BillingError as exc:
            log.warning("undo change failed for %s: %s", uid, exc)
            return 502, {"error": "The payment provider did not answer. The "
                                  "scheduled change still stands.",
                         "code": "provider_error"}
        ent.set_pending_change(uid, None)
        return 200, {"ok": True, "billing": ent.summary(uid)}
    if cur["cancel_at_period_end"]:
        return 409, {"error": "This subscription is set to end. Once it does, "
                              "subscribe again on the plan you want.",
                     "code": "cancelling"}
    plan = str(body.get("plan") or "")
    cycle = str(body.get("cycle") or cur["cycle"])
    if plan == "free":
        return 400, {"error": "To move to Free, cancel the subscription. You "
                              "keep your plan until the period ends.",
                     "code": "use_cancel"}
    if cycle not in CYCLES:
        return 400, {"error": f"cycle must be one of {', '.join(CYCLES)}"}
    pid = plan_ids().get((plan, cycle))
    if not pid:
        return 400, {"error": f"no {cycle} price is configured for plan '{plan}'"}
    if (plan, cycle) == (cur["plan"], cur["cycle"]):
        return 409, {"error": "You are already on this plan.",
                     "code": "already_subscribed"}
    ranks = ent.CATALOG["plans"]
    upgrade = ranks[plan]["rank"] > ranks[cur["plan"]]["rank"]
    when = "now" if upgrade else "cycle_end"
    try:
        entity = http("PATCH", f"/subscriptions/{sid}", {
            "plan_id": pid, "schedule_change_at": when, "customer_notify": 1})
    except BillingError as exc:
        log.warning("change failed for %s: %s", uid, exc)
        return 502, {"error": "The payment provider did not accept the change. "
                              "Your plan was not changed.", "code": "provider_error"}
    if when == "now":
        ent.set_pending_change(uid, None)
        try:
            out = _apply(uid, entity)
        except BillingError:
            # The provider took it; its webhook will land the new plan.
            out = {"pending": True}
        return 200, {"ok": True, "effective": "now", **out,
                     "billing": ent.summary(uid)}
    ent.set_pending_change(uid, {"plan": plan, "cycle": cycle,
                                 "at": cur["period_end"]})
    return 200, {"ok": True, "effective": "cycle_end", "at": cur["period_end"],
                 "billing": ent.summary(uid)}


def api_invoices(uid: int) -> tuple[int, dict]:
    """The subscription's invoices, newest first, exactly as Razorpay issued
    them. Amounts stay in paise; the client formats them."""
    if not configured():
        return _NOT_CONFIGURED
    sid = _provider_sub_id(uid)
    if not sid:
        return 200, {"invoices": [], "payment_method": None, "manage_url": None}
    try:
        listed = http("GET", f"/invoices?subscription_id={sid}&count=50")
        entity = http("GET", f"/subscriptions/{sid}")
    except BillingError as exc:
        log.warning("invoices failed for %s: %s", uid, exc)
        return 502, {"error": "The payment provider did not answer. Try again "
                              "in a moment.", "code": "provider_error"}
    items = []
    for inv in listed.get("items") or []:
        items.append({
            "id": inv.get("id"),
            "date": inv.get("issued_at") or inv.get("date") or inv.get("created_at"),
            "amount": inv.get("amount_paid") or inv.get("amount"),
            "currency": inv.get("currency") or "INR",
            "status": inv.get("status"),
            "period_start": inv.get("billing_start"),
            "period_end": inv.get("billing_end"),
            "url": inv.get("short_url"),
        })
    items.sort(key=lambda i: i["date"] or 0, reverse=True)
    return 200, {"invoices": items,
                 "payment_method": entity.get("payment_method"),
                 "manage_url": entity.get("short_url")}


def api_webhook(raw: bytes, headers) -> tuple[int, dict]:
    with _operations:
        try:
            return _webhook(raw, headers)
        except sqlite3.Error:
            # Never acknowledge a webhook that could not be durably stored.
            log.error("Billing webhook storage unavailable")
            return 503, {"error": "Billing storage unavailable; retry this event."}


def _webhook(raw: bytes, headers) -> tuple[int, dict]:
    secret = _env("RAZORPAY_WEBHOOK_SECRET")
    if not secret:
        return _NOT_CONFIGURED
    if not verify_webhook_signature(raw, headers.get("X-Razorpay-Signature") or "",
                                    secret):
        return 400, {"error": "bad signature"}
    try:
        ev = json.loads(raw or b"{}")
    except ValueError:
        return 400, {"error": "bad JSON body"}
    if not isinstance(ev, dict) or not isinstance(ev.get("payload", {}), dict):
        return 400, {"error": "bad webhook body"}
    etype = str(ev.get("event") or "")
    eid = str(headers.get("x-razorpay-event-id") or "") or \
        hashlib.sha256(raw).hexdigest()
    try:
        if not ent.record_event(PROVIDER, eid, etype, raw.decode(errors="replace")):
            return 200, {"ok": True, "duplicate": True}
    except ValueError:
        return 400, {"error": "event payload does not match its original receipt"}
    if etype in ("payment.captured", "refund.processed",
                 "payment.dispute.created", "payment.dispute.won",
                 "payment.dispute.lost"):
        try:
            result = on_verified_payment_event(etype, eid, ev) if on_verified_payment_event else "referrals disabled"
        except Exception as exc:  # event is retained for admin reconciliation
            log.exception("verified payment event %s needs review: %s", eid, exc)
            result = f"referral exception: {exc}"
        ent.event_result(PROVIDER, eid, result)
        if etype == "refund.processed":
            _refund_mail(ev)
        return 200, {"ok": True, "result": result}
    if not etype.startswith("subscription."):
        ent.event_result(PROVIDER, eid, "ignored")
        return 200, {"ok": True, "ignored": etype}
    entity = (((ev.get("payload") or {}).get("subscription") or {})
              .get("entity") or {})
    sid = str(entity.get("id") or "")
    uid = ent.user_for_provider_sub(PROVIDER, sid)
    if uid is None:
        # Recover a create whose HTTP response was lost. A user_id in notes
        # alone is NOT authority to replace somebody's subscription.
        token = (entity.get("notes") or {}).get("checkout_id")
        with ent._lock:
            attempt = ent._con.execute("SELECT user_id,plan,cycle FROM billing_checkouts "
                                       "WHERE checkout_id=? AND state IN ('creating','uncertain')",
                                       (token,)).fetchone() if token else None
        if attempt and _plan_for_id(str(entity.get("plan_id"))) == (attempt[1], attempt[2]):
            uid = attempt[0]
            ent.set_subscription(uid, plan=attempt[1], cycle=attempt[2], status="created",
                                 provider=PROVIDER, provider_sub_id=sid,
                                 cancel_at_period_end=False)
            with ent._lock:
                ent._con.execute("UPDATE billing_checkouts SET state='ready',provider_sub_id=?,updated=? "
                                 "WHERE user_id=?", (sid, int(time.time()), uid))
                ent._con.commit()
    if uid is None:
        ent.event_result(PROVIDER, eid, f"no user for {sid}")
        return 200, {"ok": True, "unmatched": sid}
    try:
        # Event ordering is not guaranteed, including within the same cycle.
        # Reconcile the current provider snapshot, not an old event snapshot.
        current = http("GET", f"/subscriptions/{sid}")
        if current.get("id") != sid:
            raise BillingError("Provider subscription mismatch")
        out = _apply(uid, current)
    except BillingError as exc:
        ent.event_result(PROVIDER, eid, "retry: provider unavailable")
        return 503, {"error": "Provider confirmation unavailable; retry this event."}
    if current.get("status") in ("halted", "cancelled", "completed", "expired",
                                "paused") and ent.plan_of(uid) == "free":
        _on_downgrade(uid)
    ent.event_result(PROVIDER, eid, json.dumps(out))
    return 200, {"ok": True, **out}


# Set by dataserver: pauses whatever the new plan no longer covers. A hook, so
# this file does not import the alert engine.
on_downgrade = None
on_verified_payment_event = None
# Set by dataserver: the account mail (notify.py). Called after a write, never
# allowed to fail the request or the webhook.
on_subscription_change = None
on_refund = None


def _refund_mail(ev: dict) -> None:
    """Tell the account a refund went out: refund -> payment -> invoice ->
    subscription -> user. Best effort; a refund is never held up by mail."""
    if not on_refund:
        return
    try:
        pl = ev.get("payload") or {}
        rf = (pl.get("refund") or {}).get("entity") or {}
        pay = (pl.get("payment") or {}).get("entity") or {}
        pid = str(rf.get("payment_id") or pay.get("id") or "")
        if pid and not pay.get("invoice_id"):
            pay = http("GET", f"/payments/{pid}")
        inv = pay.get("invoice_id")
        sid = http("GET", f"/invoices/{inv}").get("subscription_id") if inv else None
        uid = ent.user_for_provider_sub(PROVIDER, str(sid)) if sid else None
        if uid is not None:
            on_refund(uid, str(rf.get("id") or ""), rf.get("amount"), pid, pay.get("created_at"))
    except Exception as exc:                            # noqa: BLE001
        log.warning("refund mail skipped: %s", exc)


def _on_downgrade(uid: int) -> None:
    if on_downgrade:
        try:
            on_downgrade(uid)
        except Exception as exc:                        # noqa: BLE001
            log.warning("downgrade hook failed for %s: %s", uid, exc)
