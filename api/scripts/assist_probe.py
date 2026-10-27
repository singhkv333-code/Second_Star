#!/usr/bin/env python3
"""One live page-assistant turn, with its timeline.

    python scripts/assist_probe.py home "how are markets today"
    python scripts/assist_probe.py stock:TCS "is it expensive?"
    python scripts/assist_probe.py portfolio "what's my biggest loser" --warm

Prints context/first-thought/first-token/total times, the reasoning summary,
tools, token usage and the answer. --warm builds the snapshot first (as the
browser does while the user types) so the timing shows the question alone.
Where Postgres is unreachable, point DATABASE_URL, FINANCIALS_DSN and
ENRICH_DSN at a refusing port (postgresql://x:x@127.0.0.1:1/x): DB-backed
sources then read "unavailable" instead of hanging.
"""
import asyncio
import json
import os
import sys
import time
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.llm import get_llm_client
from backend.services import assist_chat


async def main(page_arg: str, message: str, warm: bool) -> None:
    page, _, symbol = page_arg.partition(":")
    ctx = types.SimpleNamespace(kite_token=None, user_id=int(os.environ.get("PROBE_USER", "0")) or None,
                                db=None, holdings=[])
    if warm:
        t = time.monotonic()
        await assist_chat.page_snapshot(page, symbol or None, ctx)
        print(f"warm snapshot: {int((time.monotonic() - t) * 1000)} ms")
    t0 = time.monotonic()
    marks: dict[str, int] = {}
    thought, answer, tools = [], [], []
    done = None
    async for ev in assist_chat.stream_turn(message=message, history=[], ctx=ctx,
                                            client=get_llm_client(), page=page,
                                            symbol=symbol or None):
        ms = int((time.monotonic() - t0) * 1000)
        et = ev["type"]
        if et == "thought":
            marks.setdefault("first_thought_ms", ms)
            thought.append(ev["delta"])
        elif et == "tool_start":
            tools.append(f"{ev.get('label')} @{ms}ms")
        elif et == "delta":
            answer.append(ev["text"])
        elif et == "replace":
            answer = []
        elif et in ("done", "error"):
            done = ev
    print(json.dumps({**marks, **((done or {}).get("timing") or {}),
                      "tools": tools, "usage": (done or {}).get("usage"),
                      "error": (done or {}).get("message") if (done or {}).get("type") == "error" else None},
                     indent=1))
    print("--- thought\n" + "".join(thought).strip())
    print("--- answer\n" + "".join(answer).strip())


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    asyncio.run(main(args[0], args[1], "--warm" in sys.argv))
