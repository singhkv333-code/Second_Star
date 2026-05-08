"""A single realistic 14-turn user session that mixes EVERY interaction
shape v2 needs to handle correctly:

  - capability question
  - read-only fetch
  - follow-up indicator inheritance ("what about X?")
  - independent intent mid-conversation
  - build-shaped imperative routed to workflow (NOT order)
  - clarification answered with affirmative
  - amendment chain
  - filler reply (must NOT re-emit)
  - cancel
  - second build in the same chat (post-cancel isolation)
  - pure affirmative on draft (fast ack)

All in ONE conversation_id so we exercise the cross-turn state
machinery, not just per-turn classification.

Compare /chat (v1) vs /chat/v2 side-by-side. Saves results to
/tmp/realistic_v1.json and /tmp/realistic_v2.json so you can diff.
"""
from __future__ import annotations

import json
import time
import uuid

import httpx

BASE = "http://127.0.0.1:8000"

TURNS: list[tuple[str, str, str]] = [
    # (label, user msg, what we expect — for logging only)
    ("T01 capability_q",
     "Can I try this without using real money first?",
     "prose answer about backtest/paper, NO draft"),
    ("T02 read_quote",
     "Show me RELIANCE",
     "get_live_price (or stock_quote)"),
    ("T03 read_indicator",
     "What's its RSI?",
     "get_indicator(RELIANCE, rsi)"),
    ("T04 followup_a",
     "what about TCS?",
     "get_indicator(TCS, rsi) — same indicator inherited"),
    ("T05 followup_b",
     "and INFY?",
     "get_indicator(INFY, rsi) — still RSI"),
    ("T06 independent_mid_focus",
     "actually wait — what's NIFTY at right now?",
     "get_index_level(NIFTY) — independent intent"),
    ("T07 workflow_build_shaped",
     "Buy 5 NIFTYBEES every Monday at 9:20 with a 5% stop loss",
     "propose_workflow with trigger.schedule + place_order + set_stoploss — NOT a SIP"),
    ("T08 amend_qty",
     "Make it 10 shares",
     "propose_workflow re-emit with quantity=10"),
    ("T09 affirm_on_draft",
     "ok",
     "fast ack about the draft above (~25ms)"),
    ("T10 filler",
     "thanks",
     "brief ack, no re-emit"),
    ("T11 cancel",
     "actually cancel that, never mind",
     "cleared, draft gone"),
    ("T12 second_build_after_cancel",
     "Now buy 10 RELIANCE at the market",
     "place_market_order(RELIANCE, qty=10) — fresh start, no NIFTYBEES leak"),
    ("T13 amend_to_limit",
     "Actually make it a limit at ₹1,450",
     "place_limit_order(RELIANCE, qty=10, price=1450)"),
    ("T14 followup_q",
     "what about INFY's RSI?",
     "get_indicator(INFY, rsi) — independent intent evicts the order draft"),
]


def post(messages, cid, endpoint):
    t0 = time.time()
    try:
        r = httpx.post(
            f"{BASE}{endpoint}",
            json={"messages": messages, "conversation_id": cid, "mode": None},
            timeout=120,
        )
        d = r.json()
    except Exception as e:
        d = {"_err": str(e), "response": ""}
    return d, int((time.time() - t0) * 1000)


def trace_chars(d):
    return f"{(d.get('response') or '')[:140]!r}"


def run_against(endpoint: str, label: str):
    cid = f"realistic_{label}_{uuid.uuid4().hex[:6]}"
    history: list[dict] = []
    rows: list[dict] = []
    print(f"\n{'='*70}\n=== {label.upper()} via {endpoint}  conv={cid}\n{'='*70}")

    for tlabel, msg, _expected in TURNS:
        history.append({"role": "user", "content": msg})
        d, ms = post(history, cid, endpoint)
        history.append({"role": "assistant", "content": d.get("response", "")})

        tools = d.get("tools_called") or []
        state = d.get("state", "?")
        rd = d.get("raw_data") or {}
        macros = [k for k in rd if k.startswith("propose_") or k.startswith("place_") or k.startswith("create_")]
        print(f"\n  {tlabel}  ({ms}ms)  state={state}")
        print(f"    user:  {msg[:80]}")
        print(f"    tools: {tools}")
        if macros:
            print(f"    macro keys in raw_data: {macros}")
        print(f"    bot:   {(d.get('response') or '')[:200]!r}")
        rows.append({
            "label": tlabel, "user": msg, "ms": ms, "tools": tools,
            "state": state, "response": d.get("response", "")[:400],
            "raw_data_keys": list(rd.keys()),
        })

    return rows


def diff_summary(v1, v2):
    print(f"\n{'='*70}\n=== SIDE-BY-SIDE DIFF\n{'='*70}")
    print(f"\n{'turn':<22} {'v1 tools':<35} {'v2 tools':<35}")
    print("-" * 95)
    for a, b in zip(v1, v2):
        a_tools = ",".join(a["tools"])[:33]
        b_tools = ",".join(b["tools"])[:33]
        same = "✓" if a["tools"] == b["tools"] else " "
        print(f"{a['label']:<22} {a_tools:<35} {b_tools:<35} {same}")

    # Latency totals
    v1_ms = sum(r["ms"] for r in v1)
    v2_ms = sum(r["ms"] for r in v2)
    print(f"\n  total latency  v1: {v1_ms}ms   v2: {v2_ms}ms   delta: {v2_ms-v1_ms:+d}ms")

    # Tool-call delta
    v1_calls = sum(len(r["tools"]) for r in v1)
    v2_calls = sum(len(r["tools"]) for r in v2)
    print(f"  total tool calls  v1: {v1_calls}   v2: {v2_calls}")


def main():
    v1 = run_against("/chat", "v1")
    json.dump(v1, open("/tmp/realistic_v1.json", "w"), indent=2)

    v2 = run_against("/chat/v2", "v2")
    json.dump(v2, open("/tmp/realistic_v2.json", "w"), indent=2)

    diff_summary(v1, v2)


if __name__ == "__main__":
    main()
