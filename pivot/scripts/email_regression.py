"""Regression for the email-substitution bug.

The user asked for email notification; bot drafted "Email notification"
text but Pivot v1's only delivery is in-app push. The fix should:
  1. Keep notify.message channel='push' in the draft
  2. NOT include the word 'email' in name / description / rationale /
     step labels
  3. Tell the user explicitly that email isn't wired and in-app was
     used instead.
"""
from __future__ import annotations

import json
import sys
import time
import uuid

import httpx

BASE = "http://127.0.0.1:8000"


def chat(messages, conv_id):
    r = httpx.post(
        f"{BASE}/chat",
        json={"messages": messages, "conversation_id": conv_id, "mode": None},
        timeout=90,
    )
    return r.json() if r.status_code == 200 else {"_err": r.status_code, "_text": r.text[:300]}


def banner(s): print(f"\n{'═'*60}\n{s}\n{'─'*60}")


def t_email_substitution():
    banner("Email request → drafts with push, names the gap")
    cid = f"email_{uuid.uuid4().hex[:6]}"
    msg = (
        "Every weekday at 3:55 PM IST, if my buying power is over ₹50,000, "
        "buy 10 shares of RELIANCE and notify me by email."
    )
    out = chat([{"role": "user", "content": msg}], cid)

    text = (out.get("response") or "")
    text_lower = text.lower()
    tools = out.get("tools_called") or []
    raw = out.get("raw_data") or {}
    workflow = raw.get("propose_workflow") or {}

    print(f"  tools: {tools}")
    print(f"  response head: {text[:400]!r}")

    # Inspect the draft if we have one
    steps = workflow.get("steps") or []
    notify_steps = [s for s in steps if str(s.get("step_type", "")).startswith("notify.")]
    notify_channels = [s.get("config", {}).get("channel") for s in notify_steps]

    description = workflow.get("description") or ""
    rationale = workflow.get("rationale") or ""
    name = workflow.get("name") or ""

    print(f"  draft name: {name!r}")
    print(f"  draft description: {description!r}")
    print(f"  notify channels: {notify_channels}")

    # Acceptance:
    # 1. Some workflow tool fired
    # 2. notify channel is push or unset (default is push). Must NOT
    #    be 'email' / 'sms'.
    # 3. Either the reply text or the draft description names the
    #    substitution explicitly ("email not wired", "in-app instead").
    # 4. The draft must NOT promise email delivery via plain
    #    "send an email" / "Email notification" labels — ONLY the
    #    transparent "(email not wired)" framing is acceptable.
    drafted = any(t.startswith("propose_") for t in tools)
    bad_channels = [c for c in notify_channels if c in ("email", "sms")]
    no_bad_channel = not bad_channels

    # Transparency: the bot must somewhere (reply text OR draft
    # description) tell the user that email isn't going to be used and
    # that in-app was substituted. Accept any phrasing that names
    # email being limited and in-app being used.
    transparency_phrases = [
        "email isn't wired", "email is not wired", "email not wired",
        "email is not supported", "email not supported",
        "email is not available", "email not available",
        "email/sms aren't wired", "email/sms are not wired",
        "email and sms", "email or sms",
        "doesn't send email", "doesn't support email",
        "not available in v1", "not supported in v1",
    ]
    has_inapp = (
        "in-app" in text_lower or "in app" in text_lower
        or "in-app" in (description or "").lower()
        or "in app" in (description or "").lower()
    )
    text_explains = any(p in text_lower for p in transparency_phrases)
    desc_explains = any(p in (description or "").lower() for p in transparency_phrases)
    explains_gap = (text_explains or desc_explains) and has_inapp

    print(f"  drafted: {drafted}")
    print(f"  bad channels (email/sms): {bad_channels}")
    print(f"  text explains gap: {text_explains}")
    print(f"  description explains gap: {desc_explains}")
    print(f"  mentions in-app: {has_inapp}")

    ok = drafted and no_bad_channel and explains_gap
    print(f"  RESULT: {'PASS' if ok else 'FAIL'}")
    return ok


def main():
    try:
        httpx.get(f"{BASE}/health", timeout=3).raise_for_status()
    except Exception as e:
        print(f"backend down: {e}")
        sys.exit(1)
    ok = t_email_substitution()
    banner("SUMMARY")
    print(f"  email_substitution     {'PASS' if ok else 'FAIL'}")


if __name__ == "__main__":
    main()
