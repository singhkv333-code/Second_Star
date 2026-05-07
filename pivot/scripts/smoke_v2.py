"""Day 3 smoke test for chat_v2 pipeline.

Five hand-picked prompts that exercise:
  1. Idle greeting        -> short ack, no draft
  2. Read intent          -> get_live_price
  3. Build intent         -> propose_workflow draft
  4. Amendment            -> propose_workflow re-emit (still in DRAFTING)
  5. Affirmative on draft -> short ack, draft preserved

Verifies the v2 endpoint is reachable, the state machine transitions
match expectation, and the FE-shape response (response/tools_called/raw_data)
is populated correctly. Does NOT compare against v1 — that's Day 6.
"""
from __future__ import annotations

import json
import time
import uuid

import httpx

BASE = "http://127.0.0.1:8000"


def post(messages, conv_id, mode=None, endpoint="/chat/v2"):
    t0 = time.time()
    r = httpx.post(
        f"{BASE}{endpoint}",
        json={"messages": messages, "conversation_id": conv_id, "mode": mode},
        timeout=120,
    )
    ms = int((time.time() - t0) * 1000)
    return r.json(), ms


def show(label, d, ms):
    tools = d.get("tools_called") or []
    state = d.get("state", "?")
    resp = (d.get("response") or "")[:160]
    print(f"\n  {label}  state={state}  tools={tools}  ({ms}ms)")
    print(f"     resp: {resp!r}")


def main():
    cid = f"smoke_v2_{uuid.uuid4().hex[:6]}"
    history = []

    cases = [
        ("T1 greet", "Hello"),
        ("T2 read", "Show me RELIANCE"),
        ("T3 build", "Build me an agent that buys NIFTYBEES whenever its RSI dips below 30."),
        ("T4 amend", "Make it 5 shares."),
        ("T5 affirm", "ok"),
    ]

    print(f"\n=== chat_v2 smoke ({cid}) ===")
    for label, msg in cases:
        history.append({"role": "user", "content": msg})
        d, ms = post(history, cid)
        show(label, d, ms)
        history.append({"role": "assistant", "content": d.get("response", "")})

    print("\n=== expectations ===")
    print("T1 state=idle or exploring; tools=[]")
    print("T2 state=exploring; tools=['get_live_price']")
    print("T3 state=drafting; tools=['propose_workflow']")
    print("T4 state=drafting; tools=['propose_workflow'] (re-emit, NOT propose_threshold_order)")
    print("T5 state=drafting; tools=[]; resp mentions 'draft above'")


if __name__ == "__main__":
    main()
