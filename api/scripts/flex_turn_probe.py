#!/usr/bin/env python3
"""One live flex-engine turn: which tools the model calls, with what
arguments, and the shape of its answer (words, ### headings, tables).

    python scripts/flex_turn_probe.py "compare tcs with other it peers"
    python scripts/flex_turn_probe.py "give me it stocks with orb" --real-tools

By default every Pivot tool is STUBBED to fail ("unreachable"), which isolates
the model's tool choice from the data: use it where Postgres is not reachable
(e.g. a cloud session). The hosted web search still runs, so with stubbed
tools the answer falls back to the web; judge the answer only with
--real-tools. Needs the LLM env (LLM_PROVIDER, LLM_MODEL, AZURE_*); point
DATABASE_URL at a refusing port (postgresql://x:x@127.0.0.1:1/x) when the DB
is unreachable, or the LLM-cost logger blocks on connect.
"""
import asyncio
import json
import os
import re
import sys
import time
import types
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.llm import get_llm_client  # noqa: E402
from backend.services import flex_chat  # noqa: E402
from backend.services.tool_registry import ToolResult  # noqa: E402

calls: list = []


async def _stub(name, args, schemas, *, ctx, own_session):
    calls.append((name, args))
    return ToolResult(name=name, args=args, success=False, data={},
                      error="data source unreachable from this test environment")


class _Store:
    def get_active_draft(self, c): return None
    def clear_active_draft(self, c): pass
    def set_active_draft(self, *a, **k): pass


async def main(msg: str) -> None:
    t0, done = time.time(), None
    async for ev in flex_chat.stream_turn(
            message=msg, history=[], conv_id=str(uuid.uuid4()), store=_Store(),
            ctx=types.SimpleNamespace(kite_token=None, user_id=None),
            client=get_llm_client(), page_lines=[], attachment_lines=[]):
        if ev.get("type") == "done":
            done = ev
    resp = (done or {}).get("response", "")
    print(json.dumps({
        "latency_s": round(time.time() - t0, 1),
        "stubbed_calls": calls,
        "tools_called": (done or {}).get("tools_called"),
        "words": len(resp.split()),
        "headings": len(re.findall(r"^#{2,4} ", resp, re.M)),
        "tables": len(re.findall(r"^\|.*\|\s*$\n^\|[\s:|-]+\|\s*$", resp, re.M)),
        "breakdown": (done or {}).get("latency_breakdown"),
    }, default=str, indent=1))
    print("-----\n" + resp)


if __name__ == "__main__":
    if "--real-tools" not in sys.argv:
        flex_chat._run_tool = _stub
    asyncio.run(main(next(a for a in sys.argv[1:] if not a.startswith("--"))))
