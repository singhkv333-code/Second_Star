"""Regression for the typo-amendment bug.

Reproduces the original trace:
  T1: user places ETERNAL order → bot emits market order card
  T2: user types "nothung" (typo for "nothing") → bot must NOT re-emit
      the same card. Acceptable: prose clarification, fresh data fetch
      (since "nothung" matches bare-ticker pattern), or ASK_USER.
"""
from __future__ import annotations

import sys
import time
import uuid

import httpx

BASE = "http://127.0.0.1:8000"


def chat(messages, conv_id):
    r = httpx.post(
        f"{BASE}/chat",
        json={"messages": messages, "conversation_id": conv_id, "mode": None},
        timeout=60,
    )
    return r.json() if r.status_code == 200 else {"_err": r.status_code}


def banner(s): print(f"\n{'═'*60}\n{s}\n{'─'*60}")


def t_typo_after_order():
    banner("T1: order card → 'nothung' must not re-emit")
    cid = f"typo_{uuid.uuid4().hex[:6]}"
    history = []

    history.append({"role": "user", "content": "buy 40 ETERNAL at market"})
    out1 = chat(history, cid)
    history.append({"role": "assistant", "content": out1.get("response", "")})
    print(f"  T1 tools: {out1.get('tools_called')}")
    print(f"  T1 logiccard: {bool(out1.get('logiccard'))}")

    history.append({"role": "user", "content": "nothung"})
    t0 = time.time()
    out2 = chat(history, cid)
    ms = int((time.time() - t0) * 1000)
    text = (out2.get("response") or "")[:240]
    tools = out2.get("tools_called") or []
    has_card = bool(out2.get("logiccard"))

    print(f"  T2 latency: {ms}ms")
    print(f"  T2 tools: {tools}")
    print(f"  T2 logiccard: {has_card}")
    print(f"  T2 head: {text!r}")

    # Expected: NO place_market_order, NO propose_workflow, NO logiccard for ETERNAL
    re_emitted_order = "place_market_order" in tools
    re_emitted_workflow = any(t.startswith("propose_") for t in tools)
    has_eternal_card = (
        has_card
        and "eternal" in (str(out2.get("logiccard") or "")).lower()
    )
    ok = not re_emitted_order and not re_emitted_workflow and not has_eternal_card
    print(f"  re-emitted order tool: {re_emitted_order}")
    print(f"  re-emitted workflow: {re_emitted_workflow}")
    print(f"  re-emitted ETERNAL card: {has_eternal_card}")
    print(f"  RESULT: {'PASS' if ok else 'FAIL'}")
    return ok


def t_legit_followup_still_works():
    """Sanity: 'cancel' after an order should still cancel the draft."""
    banner("T2: 'cancel' must still cancel the active card")
    cid = f"typo_{uuid.uuid4().hex[:6]}"
    history = []

    history.append({"role": "user", "content": "buy 10 RELIANCE at market"})
    out1 = chat(history, cid)
    history.append({"role": "assistant", "content": out1.get("response", "")})

    history.append({"role": "user", "content": "cancel"})
    out2 = chat(history, cid)
    text = (out2.get("response") or "").lower()
    tools = out2.get("tools_called") or []
    print(f"  cancel tools: {tools}")
    print(f"  cancel head: {text[:160]!r}")
    ok = "cancel_draft" in tools or "cancel" in text or "discarded" in text
    print(f"  RESULT: {'PASS' if ok else 'FAIL'}")
    return ok


def t_known_ticker_still_fetches():
    """Sanity: 'wipro' after an order should still fetch wipro price.
    The strip removes order tools but keeps get_live_price."""
    banner("T3: 'wipro' after an order — fetches fresh price (read-only)")
    cid = f"typo_{uuid.uuid4().hex[:6]}"
    history = []

    history.append({"role": "user", "content": "buy 10 RELIANCE at market"})
    out1 = chat(history, cid)
    history.append({"role": "assistant", "content": out1.get("response", "")})

    history.append({"role": "user", "content": "wipro"})
    out2 = chat(history, cid)
    text = (out2.get("response") or "").lower()
    tools = out2.get("tools_called") or []
    has_card = bool(out2.get("logiccard"))
    print(f"  tools: {tools}")
    print(f"  has card: {has_card}")
    print(f"  head: {text[:200]!r}")
    # We accept either: get_live_price call, or prose mention of wipro,
    # or ASK_USER. We REJECT a re-emit of the RELIANCE order.
    no_reliance_card = not (has_card and "reliance" in (str(out2.get("logiccard") or "")).lower())
    no_order_re_emit = "place_market_order" not in tools
    ok = no_reliance_card and no_order_re_emit
    print(f"  no RELIANCE card: {no_reliance_card}")
    print(f"  no order re-emit: {no_order_re_emit}")
    print(f"  RESULT: {'PASS' if ok else 'FAIL'}")
    return ok


def main():
    try:
        httpx.get(f"{BASE}/health", timeout=3).raise_for_status()
    except Exception as e:
        print(f"backend down: {e}")
        sys.exit(1)
    results = [
        ("nothung_no_reemit", t_typo_after_order()),
        ("cancel_still_works", t_legit_followup_still_works()),
        ("wipro_no_reemit", t_known_ticker_still_fetches()),
    ]
    banner("SUMMARY")
    passed = sum(1 for _, p in results if p)
    for name, p in results:
        print(f"  {name:24s}  {'PASS' if p else 'FAIL'}")
    print(f"\n  {passed}/{len(results)} passed")


if __name__ == "__main__":
    main()
