"""AI credits for pivot's chat, metered by charto.

Charto's account database is the one place plans and usage live
(`charto/data/entitlements.py`). Pivot never opens that file for writing — its
`auth/charto_session.py` is deliberately read-only — so a debit is a request
to charto's `POST /billing/consume`, which is the single writer of the ledger.

Who the debit is for:
  * a Charto session token is forwarded as is, and charto resolves it;
  * a Pivot JWT has no charto session, so the service key
    (CHARTO_INTERNAL_KEY) is sent with the account's EMAIL — identity across
    the two stores is always email, never the colliding integer ids (see
    charto_session.py's header).

When charto cannot be reached the turn is ALLOWED and the miss is logged:
metering protects cost, and an outage of the meter must not become an outage
of the chat. Set PIVOT_METER_FAIL_CLOSED=1 to refuse instead.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

FEATURE = "ai.credits"


@dataclass
class Debit:
    ok: bool
    status: int = 200
    body: Optional[dict] = None       # the 402 body when refused
    credits: Optional[dict] = None    # {used, limit, left, resets_at} when charged


def _base() -> str:
    return os.getenv("CHARTO_INTERNAL_URL", "http://127.0.0.1:5174").rstrip("/")


def turn_key(conversation_id: str, messages: list, header_key: str = "") -> str:
    """One key per user turn: the conversation, the turn's position and its
    text (scoped by the client's Idempotency-Key when sent) — so a retry of
    the same message is recognised by charto and not charged twice, and a
    different message never is."""
    last = json.dumps((messages or [])[-1:], default=str)[:4000]
    # The header only SCOPES the key. Used verbatim, one header value could
    # carry any number of different questions as "retries" for one credit.
    return hashlib.sha256(
        f"pivot|{header_key[:80]}|{conversation_id}|{len(messages or [])}|{last}"
        .encode()).hexdigest()[:32]


def _who(authorization: str, db=None, user_id: Optional[int] = None) -> dict:
    """Headers and body fields that tell charto whose credit this is."""
    token = (authorization or "").replace("Bearer ", "", 1).strip()
    from backend.auth.jwt_handler import verify_token

    service = {"X-Internal-Key": os.getenv("CHARTO_INTERNAL_KEY", "")}
    headers = dict(service)
    if token and not verify_token(token, "access"):
        # A charto session: forwarded. The service key rides along so charto
        # accepts a REFUND from us; a user's bearer alone never can.
        headers["Authorization"] = f"Bearer {token}"
    # The email goes EVERY time, so a session that expired between our auth
    # and charto's still lands on the right account instead of on nobody.
    email = ""
    if db is not None and user_id is not None:
        try:
            from backend.models import User

            u = db.query(User).filter(User.id == user_id).first()
            email = (u.email if u else "") or ""
        except Exception as exc:  # noqa: BLE001
            logger.warning("meter: email lookup failed for %s: %s", user_id, exc)
    return {"headers": headers, "body": {"email": email}}


def _post(payload: dict, headers: dict) -> tuple[int, dict]:
    req = urllib.request.Request(
        _base() + "/billing/consume", data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", **headers}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read() or b"{}")
        except ValueError:
            return exc.code, {}


def debit(authorization: str, key: str, *, db=None, user_id=None) -> Debit:
    who = _who(authorization, db, user_id)
    try:
        status, out = _post({"feature": FEATURE, "idem_key": key, "amount": 1,
                             "meta": "pivot", **who["body"]}, who["headers"])
    except Exception as exc:  # noqa: BLE001 — charto down, timeout, DNS
        logger.warning("meter: charto unreachable, turn allowed: %s", exc)
        if os.getenv("PIVOT_METER_FAIL_CLOSED") == "1":
            return Debit(False, 503, {"error": "Usage metering is unavailable. "
                                               "Try again shortly.",
                                      "code": "meter_unavailable"})
        return Debit(True)
    if status == 402:
        return Debit(False, 402, out)
    if status != 200:
        # A misconfigured key or an unknown account is OUR fault, not the
        # user's: allow, and say so loudly — an unset CHARTO_INTERNAL_KEY
        # means pivot accounts are NOT being metered.
        logger.error("meter: charto answered %s (%s); turn allowed", status,
                       out.get("error"))
        return Debit(True)
    lim = out.get("limit")
    used = out.get("used")
    return Debit(True, credits={
        "used": used, "limit": lim, "resets_at": out.get("resets_at"),
        "left": None if lim is None else max(0, lim - (used or 0))})


def refund(authorization: str, key: str, *, db=None, user_id=None,
           why: str = "error") -> None:
    who = _who(authorization, db, user_id)
    try:
        _post({"feature": FEATURE, "idem_key": key, "refund": True, "why": why,
               **who["body"]}, who["headers"])
    except Exception as exc:  # noqa: BLE001
        logger.warning("meter: refund of %s not delivered: %s", key, exc)
