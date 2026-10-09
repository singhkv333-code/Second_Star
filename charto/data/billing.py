"""Charto — taking money for a plan, through Razorpay Subscriptions.

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
import urllib.error
import urllib.request
from os import environ

import entitlements as ent

log = logging.getLogger("charto.billing")

API = "https://api.razorpay.com/v1"
PROVIDER = "razorpay"
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
    return bool(_env("RAZORPAY_KEY_ID") and _env("RAZORPAY_KEY_SECRET"))


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
        detail = exc.read().decode(errors="replace")[:300]
        raise BillingError(f"Razorpay answered {exc.code}: {detail}") from None


http = _http


class BillingError(Exception):
    pass


_NOT_CONFIGURED = (503, {"error": "Billing is not configured on this server.",
                         "code": "billing_unavailable"})


# ── signatures ─────────────────────────────────────────────────────────────

def _hmac_hex(secret: str, msg: bytes) -> str:
    return hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()


def verify_webhook_signature(raw: bytes, signature: str, secret: str) -> bool:
    if not secret or not signature:
        return False
    return hmac.compare_digest(_hmac_hex(secret, raw), signature.strip())


def verify_payment_signature(payment_id: str, sub_id: str, signature: str,
                             key_secret: str) -> bool:
    if not (payment_id and sub_id and signature and key_secret):
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
            "SELECT provider_sub_id, period_end FROM subscriptions WHERE "
            "user_id=?", (uid,)).fetchone()
    if cur and cur[0] == str(entity.get("id")) and cur[1] and pe \
            and int(pe) < int(cur[1]):
        return {"ignored": "stale", "period_end": cur[1]}
    grace = None
    if status == "past_due":
        grace = int(time.time()) + ent.GRACE_DAYS * 86400
    elif status == "active" and pe:
        grace = int(pe) + ent.GRACE_DAYS * 86400      # a late renewal webhook
    ent.set_subscription(
        uid, plan=plan, status=status, cycle=cycle, provider=PROVIDER,
        provider_sub_id=str(entity.get("id")), period_start=ps, period_end=pe,
        cancel_at_period_end=None, grace_until=grace)
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
    if not configured():
        return _NOT_CONFIGURED
    plan = str(body.get("plan") or "")
    cycle = str(body.get("cycle") or "monthly")
    if cycle not in CYCLES:
        return 400, {"error": f"cycle must be one of {', '.join(CYCLES)}"}
    pid = plan_ids().get((plan, cycle))
    if not pid:
        return 400, {"error": f"no {cycle} price is configured for plan '{plan}'"}
    cur = ent.summary(uid)["subscription"]
    if cur and cur["provider"] == PROVIDER and cur["status"] in ("active", "past_due") \
            and ent.plan_of(uid) != "free":
        return 409, {"error": "You already have a subscription. Change plan "
                              "instead of starting a second one.",
                     "code": "already_subscribed"}
    try:
        sub = http("POST", "/subscriptions", {
            "plan_id": pid, "total_count": TOTAL_COUNT[cycle],
            "customer_notify": 1, "notes": {"user_id": str(uid)}})
    except BillingError as exc:
        log.warning("checkout failed for %s: %s", uid, exc)
        return 502, {"error": "The payment provider did not answer. Nothing "
                              "was charged; try again.", "code": "provider_error"}
    ent.set_subscription(uid, plan=plan, status="created", cycle=cycle,
                         provider=PROVIDER, provider_sub_id=str(sub["id"]))
    return 200, {"subscription_id": sub["id"], "key_id": _env("RAZORPAY_KEY_ID"),
                 "short_url": sub.get("short_url"), "plan": plan,
                 "cycle": cycle, "prefill": {"email": email}}


def api_verify(uid: int, body: dict) -> tuple[int, dict]:
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
        out = _apply(uid, entity)
    except BillingError as exc:
        # The signature proved a payment; the webhook will still land it.
        log.warning("verify fetch failed for %s: %s", sid, exc)
        return 202, {"pending": True, "note": "Payment received. Your plan "
                     "will switch as soon as the provider confirms it."}
    return 200, {"ok": True, **out, "billing": ent.summary(uid)}


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
        http("POST", f"/subscriptions/{row[0]}/cancel", {"cancel_at_cycle_end": 1})
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
    etype = str(ev.get("event") or "")
    eid = str(headers.get("x-razorpay-event-id") or "") or \
        hashlib.sha256(raw).hexdigest()
    if not ent.record_event(PROVIDER, eid, etype, raw.decode(errors="replace")):
        return 200, {"ok": True, "duplicate": True}
    if not etype.startswith("subscription."):
        ent.event_result(PROVIDER, eid, "ignored")
        return 200, {"ok": True, "ignored": etype}
    entity = (((ev.get("payload") or {}).get("subscription") or {})
              .get("entity") or {})
    sid = str(entity.get("id") or "")
    uid = ent.user_for_provider_sub(PROVIDER, sid)
    if uid is None:
        note = (entity.get("notes") or {}).get("user_id")
        uid = int(note) if str(note or "").isdigit() else None
    if uid is None:
        ent.event_result(PROVIDER, eid, f"no user for {sid}")
        return 200, {"ok": True, "unmatched": sid}
    try:
        out = _apply(uid, entity)
    except BillingError as exc:
        ent.event_result(PROVIDER, eid, f"error: {exc}")
        return 200, {"ok": True, "error": str(exc)}
    if entity.get("status") in ("halted", "cancelled", "completed", "expired",
                                "paused") and ent.plan_of(uid) == "free":
        _on_downgrade(uid)
    ent.event_result(PROVIDER, eid, json.dumps(out))
    return 200, {"ok": True, **out}


# Set by dataserver: pauses whatever the new plan no longer covers. A hook, so
# this file does not import the alert engine.
on_downgrade = None


def _on_downgrade(uid: int) -> None:
    if on_downgrade:
        try:
            on_downgrade(uid)
        except Exception as exc:                        # noqa: BLE001
            log.warning("downgrade hook failed for %s: %s", uid, exc)
