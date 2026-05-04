#!/usr/bin/env python3
"""
Self-improvement loop for the Pivot chatbot.

Runs:
  1. qa_agent  -> tests live Sarvam responses, writes /tmp/pivot_qa_log.json
  2. fix_agent -> reads the log, patches code (system prompt, max_tokens, etc.)
  3. repeat until pass rate stops improving or hits 100% or hits max iterations

Run from pivot/ root:
  python scripts/improve_loop.py
"""

import asyncio
import importlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts import qa_agent, fix_agent

MAX_ITERATIONS = 4
LOG_FILE = Path("/tmp/pivot_qa_log.json")


async def run_loop():
    history = []
    last_passed = -1

    for i in range(1, MAX_ITERATIONS + 1):
        print(f"\n#### ITERATION {i} ####")

        # Reload modules so patches applied by fix_agent take effect
        importlib.reload(qa_agent)

        log = await qa_agent.run_all_tests()
        passed = log["passed"]
        history.append(passed)

        if passed == log["total"]:
            print(f"\n[loop] 100% pass rate hit at iteration {i}. Done.")
            break
        if passed == last_passed and i > 1:
            print(f"\n[loop] No improvement (still {passed}/{log['total']}). Stopping.")
            break
        last_passed = passed

        if i == MAX_ITERATIONS:
            print(f"\n[loop] Max iterations reached. Final: {passed}/{log['total']}")
            break

        # Run fixer
        importlib.reload(fix_agent)
        changed = fix_agent.run_all_fixes()
        if not changed:
            print("\n[loop] Fixer made no changes — remaining failures need manual review.")
            break

    print("\n" + "=" * 60)
    print("Pass-rate history:", " -> ".join(f"{p}/{log['total']}" for p in history))
    print("Final log:", LOG_FILE)


if __name__ == "__main__":
    asyncio.run(run_loop())
