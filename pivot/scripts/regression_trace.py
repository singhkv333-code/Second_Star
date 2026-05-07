"""Targeted regression test mirroring the user-reported chat trace.

Three scenarios, each in its own conv_id:
  1. Sector basket → must call propose_basket_allocation, not propose_workflow.
  2. Market-relative trigger → must NOT reject as "doesn't fit".
  3. Cross-draft amendment → "100 shares" reply attaches to the most
     recent ASK_USER question (RELIANCE 14:00), not the older basket.

Runs with mode='agent' for the first scenario to also exercise the
broadened agent-mode pin.
"""
from __future__ import annotations

import json
import sys
import time
import uuid

import httpx

BASE = "http://127.0.0.1:8000"


def chat(messages, conv_id, mode=None):
    payload = {"messages": messages, "conversation_id": conv_id, "mode": mode}
    t0 = time.time()
    try:
        r = httpx.post(f"{BASE}/chat", json=payload, timeout=90.0)
    except Exception as e:
        return {"_error": str(e), "tools_called": [], "response": "", "latency_ms": 0}
    out = r.json() if r.status_code == 200 else {"_http": r.status_code, "_text": r.text[:300], "tools_called": [], "response": ""}
    out["latency_ms"] = int((time.time() - t0) * 1000)
    return out


def banner(s): print(f"\n{'═'*60}\n{s}\n{'─'*60}")
def line(prefix, val): print(f"  {prefix} {val}")


def scenario_basket():
    banner("1. Sector basket — must use propose_basket_allocation")
    cid = f"reg_basket_{uuid.uuid4().hex[:8]}"
    history = []

    msg = "make me a basket of steel stocks with equal weightage and 1L to invest"
    history.append({"role": "user", "content": msg})
    out = chat(history, cid, mode="agent")
    history.append({"role": "assistant", "content": out.get("response", "")})

    line("msg:", repr(msg))
    line("tools:", out.get("tools_called"))
    line("hint:", (out.get("raw_data") or {}).get("_render_hint"))
    line("latency:", f"{out.get('latency_ms')}ms")

    tools = out.get("tools_called") or []
    pass_t1 = "propose_basket_allocation" in tools
    line("RESULT:", "PASS" if pass_t1 else "FAIL — wanted propose_basket_allocation")
    return pass_t1


def scenario_market_relative():
    banner("2. Market-relative trigger — must NOT be rejected")
    cid = f"reg_relative_{uuid.uuid4().hex[:8]}"
    history = []

    msg = "everyday after 1 hour of open buy reliance and sell it at 2 PM"
    history.append({"role": "user", "content": msg})
    out = chat(history, cid, mode="agent")
    history.append({"role": "assistant", "content": out.get("response", "")})

    line("msg:", repr(msg))
    line("tools:", out.get("tools_called"))
    line("hint:", (out.get("raw_data") or {}).get("_render_hint"))
    line("response head:", repr((out.get("response") or "")[:300]))

    text = (out.get("response") or "").lower()
    tools = out.get("tools_called") or []
    rejected = any(p in text for p in [
        "doesn't fit", "does not fit", "not supported", "isn't supported",
        "trigger types", "fixed price level",
    ])
    drafted = "propose_workflow" in tools or "propose_scheduled_order" in tools
    asked = "ASK_USER" in tools

    pass_t2 = drafted or asked  # asked is OK; outright rejection isn't
    pass_t2 = pass_t2 and not rejected
    line("RESULT:", "PASS" if pass_t2 else "FAIL — bot rejected a supported trigger")
    return pass_t2


def scenario_cross_draft():
    banner("3. Cross-draft amend — '100 shares' attaches to RELIANCE")
    cid = f"reg_cross_{uuid.uuid4().hex[:8]}"
    history = []

    # Turn 1: build a basket draft.
    history.append({"role": "user", "content": "make me a basket of steel stocks with equal weight and 1L to invest"})
    out1 = chat(history, cid, mode="agent")
    history.append({"role": "assistant", "content": out1.get("response", "")})
    line("T1 tools:", out1.get("tools_called"))

    # Turn 2: switch context — user asks for a recurring RELIANCE buy.
    history.append({"role": "user", "content": "everyday at 2PM buy me reliance"})
    out2 = chat(history, cid, mode="agent")
    history.append({"role": "assistant", "content": out2.get("response", "")})
    line("T2 tools:", out2.get("tools_called"))
    line("T2 head:", repr((out2.get("response") or "")[:200]))

    # Turn 3: short reply — should attach to RELIANCE, not basket.
    history.append({"role": "user", "content": "100 shares"})
    out3 = chat(history, cid, mode="agent")
    history.append({"role": "assistant", "content": out3.get("response", "")})

    line("T3 tools:", out3.get("tools_called"))
    line("T3 hint:", (out3.get("raw_data") or {}).get("_render_hint"))
    line("T3 head:", repr((out3.get("response") or "")[:300]))

    text = (out3.get("response") or "").lower()
    tools = out3.get("tools_called") or []

    # Failure shape: bot asks "(A) basket or (B) reliance".
    re_asked_ab = (("(a)" in text and "(b)" in text)
                   or ("a or b" in text)
                   or ("basket" in text and "reliance" in text and "?" in text))
    drafted = any(t in tools for t in [
        "propose_workflow", "propose_scheduled_order", "place_market_order",
    ])
    pass_t3 = drafted and not re_asked_ab
    line("RESULT:", "PASS" if pass_t3 else "FAIL — bot reverted to A/B clarification")
    return pass_t3


def main():
    try:
        httpx.get(f"{BASE}/health", timeout=3).raise_for_status()
    except Exception as e:
        print(f"backend not reachable: {e}")
        sys.exit(1)

    results = [
        ("basket", scenario_basket()),
        ("market_relative", scenario_market_relative()),
        ("cross_draft", scenario_cross_draft()),
    ]
    banner("SUMMARY")
    passed = sum(1 for _, p in results if p)
    for name, p in results:
        print(f"  {name:18s}  {'PASS' if p else 'FAIL'}")
    print(f"\n  {passed}/{len(results)} passed")


if __name__ == "__main__":
    main()
