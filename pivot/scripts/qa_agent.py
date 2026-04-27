#!/usr/bin/env python3
"""
Agent A — Conversation Quality Tester
Runs test conversations against Pivot's Sarvam integration.
Writes findings to /tmp/pivot_qa_log.json for Agent B to fix.

Run from the pivot/ root:
  python scripts/qa_agent.py
"""

import asyncio
import json
import re
import sys
import os
import time
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))
os.environ.setdefault("APP_ENV", "development")

from backend.agents.sarvam_client import call_sarvam
# Live system prompt (the one the running chatbot actually uses)
from backend.routers.chat import PIVOT_SYSTEM_PROMPT as LIVE_SYSTEM_PROMPT

LOG_FILE = Path("/tmp/pivot_qa_log.json")


TEST_CASES = [
    {
        "id": "T01",
        "category": "greeting",
        "input": "what all can you do",
        "must_not_contain": ["<think>", "</think>", "Hindi", "Hinglish"],
        "must_contain": ["portfolio", "order"],
        "max_length": 400,
        "description": "Capability question — concise, no markdown leak, no think blocks",
    },
    {
        "id": "T02",
        "category": "order_intent",
        "input": "buy 10 shares of INFY at market price",
        "must_not_contain": ["<think>", "</think>", "I cannot", "I am unable"],
        "must_contain": ["confirm", "INFY"],
        "max_length": 300,
        "description": "Clear order intent — should propose action concisely",
    },
    {
        "id": "T03",
        "category": "product_explanation",
        "input": "explain SafeGrow to me",
        "must_not_contain": ["<think>", "</think>"],
        "must_contain": ["capital", "option"],
        "max_length": 600,
        "description": "Product explanation — accurate, no markdown symbols leaking",
    },
    {
        "id": "T04",
        "category": "portfolio",
        "input": "show me my portfolio",
        "must_not_contain": ["<think>", "</think>"],
        "must_contain": [],
        "max_length": 400,
        "description": "Portfolio request — short, direct",
    },
    {
        "id": "T05",
        "category": "clarification",
        "input": "I want to invest safely",
        "must_not_contain": ["<think>", "</think>", "Hinglish"],
        "must_contain": [],
        "max_length": 400,
        "description": "Ambiguous intent — should clarify briefly",
    },
    {
        "id": "T06",
        "category": "disclaimer",
        "input": "should I buy Reliance right now",
        "must_not_contain": ["<think>", "</think>", "you should buy", "I recommend", "guaranteed"],
        "must_contain": ["not financial advice"],
        "max_length": 400,
        "description": "Advice-seeking — must decline advice framing, include disclaimer",
    },
    {
        "id": "T07",
        "category": "number_formatting",
        "input": "what is 5% of 2 lakh",
        "must_not_contain": ["<think>", "</think>"],
        "must_contain": ["10,000"],
        "max_length": 200,
        "description": "Simple calculation — must show number in Indian format",
    },
    {
        "id": "T08",
        "category": "gtt_order",
        "input": "set a GTT to buy TCS if it falls to 3500",
        "must_not_contain": ["<think>", "</think>", "I cannot"],
        "must_contain": ["TCS", "3500"],
        "max_length": 400,
        "description": "GTT order request — clear action, no refusal",
    },
    {
        "id": "T09",
        "category": "length_control",
        "input": "hi",
        "must_not_contain": ["<think>", "</think>"],
        "must_contain": [],
        "max_length": 200,
        "description": "Short greeting — concise reply, not an essay",
    },
    {
        "id": "T10",
        "category": "no_hallucination",
        "input": "what was Nifty's closing price yesterday",
        "must_not_contain": ["<think>", "</think>"],
        "must_contain": ["real-time", "live", "check", "do not have"],
        # `must_contain` here is satisfied if ANY of these tokens shows up — the model
        # should redirect rather than fabricate. We treat the list as OR via a custom check.
        "must_contain_mode": "any",
        "max_length": 250,
        "description": "Live data request — must redirect, not invent prices",
    },
]


async def run_single_test(test: dict) -> dict:
    messages = [{"role": "user", "content": test["input"]}]

    t0 = time.time()
    error = None
    response = ""
    try:
        response = await call_sarvam(
            messages=messages,
            system_prompt=LIVE_SYSTEM_PROMPT,
            temperature=0.3,
            max_tokens=400,
        )
        if not isinstance(response, str):
            response = str(response)
    except Exception as e:
        error = f"{type(e).__name__}: {e}"
    elapsed_ms = int((time.time() - t0) * 1000)

    failures = []

    for pattern in test["must_not_contain"]:
        if pattern.lower() in response.lower():
            failures.append(f"FOUND FORBIDDEN: '{pattern}'")

    must_contain_mode = test.get("must_contain_mode", "all")
    if must_contain_mode == "any" and test["must_contain"]:
        if not any(p.lower() in response.lower() for p in test["must_contain"]):
            failures.append(f"MISSING (any): one of {test['must_contain']}")
    else:
        for pattern in test["must_contain"]:
            if pattern.lower() not in response.lower():
                failures.append(f"MISSING REQUIRED: '{pattern}'")

    char_limit = test["max_length"] * 4  # rough char-per-token allowance
    if len(response) > char_limit:
        failures.append(f"TOO LONG: {len(response)} chars (limit ~{char_limit})")

    if re.search(r"\*\*\w", response):
        failures.append("MARKDOWN LEAK: ** found in response")
    if re.search(r"^#{1,6}\s", response, flags=re.MULTILINE):
        failures.append("MARKDOWN LEAK: ## header found in response")

    if "<think>" in response.lower() or "</think>" in response.lower():
        failures.append("CRITICAL: <think> block leaked into response")

    passed = len(failures) == 0 and not error

    return {
        "test_id": test["id"],
        "category": test["category"],
        "description": test["description"],
        "input": test["input"],
        "response": response[:1000],
        "response_length": len(response),
        "latency_ms": elapsed_ms,
        "passed": passed,
        "failures": failures,
        "error": error,
        "timestamp": datetime.utcnow().isoformat(),
    }


async def run_all_tests():
    print("\n=" * 1 + "=" * 60)
    print("PIVOT QA AGENT — running conversation tests against live Sarvam")
    print("=" * 60)

    results = []
    passed = 0
    failed = 0

    for test in TEST_CASES:
        print(f"\n[{test['id']}] {test['description'][:65]}")
        print(f"  Input: \"{test['input']}\"")

        result = await run_single_test(test)
        results.append(result)

        if result["passed"]:
            passed += 1
            print(f"  PASS ({result['latency_ms']}ms, {result['response_length']} chars)")
        else:
            failed += 1
            print(f"  FAIL ({result['latency_ms']}ms, {result['response_length']} chars)")
            for f in result["failures"]:
                print(f"     -> {f}")
            if result.get("error"):
                print(f"     -> ERROR: {result['error']}")
            print(f"  Preview: {result['response'][:160]}...")

    log = {
        "run_at": datetime.utcnow().isoformat(),
        "total": len(TEST_CASES),
        "passed": passed,
        "failed": failed,
        "pass_rate": f"{passed/len(TEST_CASES)*100:.0f}%",
        "results": results,
        "system_prompt_used": LIVE_SYSTEM_PROMPT,
    }
    LOG_FILE.write_text(json.dumps(log, indent=2, ensure_ascii=False))

    print("\n" + "=" * 60)
    print(f"Results: {passed}/{len(TEST_CASES)} passed ({log['pass_rate']})")
    print(f"Log: {LOG_FILE}")
    if failed > 0:
        print("Run fix_agent.py to apply patches:  python scripts/fix_agent.py")

    return log


if __name__ == "__main__":
    asyncio.run(run_all_tests())
