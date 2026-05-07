"""Regression for the two trace bugs:
  1. "What else" should NOT trigger the catalog dump.
  2. The reasoning-leak sanitizer should strip "the user now says..."
     style monologue.
  3. Sanitizer unit-test: feed it the exact leaked text from the
     bug report.
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


def t1_what_else():
    """Place an order, then 'what else' — must not dump catalog."""
    banner("1. Active draft + 'what else' must not dump catalog")
    cid = f"leak_we_{uuid.uuid4().hex[:6]}"
    history = []

    history.append({"role": "user", "content": "buy 10 RELIANCE at market"})
    out1 = chat(history, cid)
    history.append({"role": "assistant", "content": out1.get("response", "")})
    print(f"  T1 tools: {out1.get('tools_called')}")

    history.append({"role": "user", "content": "what else"})
    t0 = time.time()
    out2 = chat(history, cid)
    ms = int((time.time() - t0) * 1000)
    text = (out2.get("response") or "")[:200]
    tools = out2.get("tools_called") or []

    print(f"  T2 latency: {ms}ms")
    print(f"  T2 tools: {tools}")
    print(f"  T2 head: {text!r}")

    leaked_catalog = (
        "step shape isn't in pivot" in text.lower()
        or "triggers available today" in text.lower()
        or "fetches available today" in text.lower()
    )
    correct_path = (
        ms < 1000  # fast-path
        and not leaked_catalog
        and not tools  # no spurious propose_workflow
    )
    print(f"  RESULT: {'PASS' if correct_path else 'FAIL'}"
          f" — fastpath={ms<1000}, no_catalog={not leaked_catalog}, no_tools={not tools}")
    return correct_path


def t2_anything_else():
    banner("2. 'anything else' fast-path")
    cid = f"leak_ae_{uuid.uuid4().hex[:6]}"
    t0 = time.time()
    out = chat([{"role": "user", "content": "anything else"}], cid)
    ms = int((time.time() - t0) * 1000)
    text = (out.get("response") or "")[:160]
    print(f"  latency: {ms}ms")
    print(f"  head: {text!r}")
    ok = ms < 1000 and "would you like to do next" in text.lower()
    print(f"  RESULT: {'PASS' if ok else 'FAIL'}")
    return ok


def t3_reasoning_sanitizer():
    """Direct unit test of the sanitizer with the exact leak text."""
    banner("3. Sanitizer strips the reported leak text")
    sys.path.insert(0, "/Users/karanveersingh/Downloads/Second_Star/pivot")
    from backend.services.chat_service import (
        _strip_reasoning_leakage, _post_process,
    )

    leak = (
        "I've placed the updated market order card to buy 40 shares of "
        "ETERNAL. Review and confirm the card to execute.\n\n"
        "This is a long and complex conversation. The user now says: "
        "\"when do we square off positions and close the market?\" We "
        "must answer succinctly. Provide the times for square off and "
        "market close. Must include disclaimer. Earlier guidance: "
        "intraday square-off should be by 15:30. Let's craft final.\n\n"
        "Market close is 15:30 IST."
    )
    cleaned = _strip_reasoning_leakage(leak)
    print(f"  input chars: {len(leak)}")
    print(f"  output chars: {len(cleaned)}")
    print(f"  output:\n  {cleaned!r}")
    keeps_legit = "I've placed the updated market order card" in cleaned
    keeps_close_time = "Market close is 15:30 IST" in cleaned
    drops_leak = "the user now says" not in cleaned.lower()
    drops_meta = "let's craft" not in cleaned.lower()

    ok = keeps_legit and keeps_close_time and drops_leak and drops_meta
    print(f"  legitimate text kept: {keeps_legit}")
    print(f"  market-close fact kept: {keeps_close_time}")
    print(f"  reasoning monologue dropped: {drops_leak}")
    print(f"  meta phrases dropped: {drops_meta}")
    print(f"  RESULT: {'PASS' if ok else 'FAIL'}")
    return ok


def main():
    try:
        httpx.get(f"{BASE}/health", timeout=3).raise_for_status()
    except Exception as e:
        print(f"backend down: {e}")
        sys.exit(1)

    results = [
        ("what_else", t1_what_else()),
        ("anything_else", t2_anything_else()),
        ("sanitizer_unit", t3_reasoning_sanitizer()),
    ]
    banner("SUMMARY")
    passed = sum(1 for _, p in results if p)
    for name, p in results:
        print(f"  {name:20s}  {'PASS' if p else 'FAIL'}")
    print(f"\n  {passed}/{len(results)} passed")


if __name__ == "__main__":
    main()
