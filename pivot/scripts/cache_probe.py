"""Probe OpenAI prompt-cache behavior in our chat path.

Sends a series of chats and inspects the OpenAI usage.cached_tokens
field on each response. Shows hit rate over the sequence so we can see
whether the cache warms after turn 1.

Reads logs from /tmp/uvicorn.log; prints per-turn cached_tokens.
"""
from __future__ import annotations

import json
import re
import subprocess
import time
import uuid

import httpx

BASE = "http://127.0.0.1:8000"


PROMPTS = [
    # Same conv to maximise prefix overlap
    ("c1", "what's the price of TCS"),
    ("c1", "and INFY"),
    ("c1", "show me my portfolio"),
    # New conv with same kind of ask — same toolset → should still hit
    ("c2", "what's the price of RELIANCE"),
    ("c2", "and HDFCBANK"),
    # Different toolset (workflow ask) → first turn: cache miss expected
    ("c3", "build me an agent that buys 5 NIFTYBEES every weekday at 9:15"),
    ("c3", "make it 10 shares"),
]


def chat(messages, conv_id):
    r = httpx.post(
        f"{BASE}/chat",
        json={"messages": messages, "conversation_id": conv_id, "mode": None},
        timeout=60,
    )
    return r.json() if r.status_code == 200 else {"_err": r.status_code}


def main():
    convs: dict[str, list[dict]] = {}
    print(f"{'#':<3} {'conv':<5} {'prompt':<60} {'in_tok':<8} {'cached':<8} {'pct':<6}")

    for i, (cid_name, prompt) in enumerate(PROMPTS, 1):
        history = convs.setdefault(cid_name, [])
        history.append({"role": "user", "content": prompt})
        cid = f"probe_{cid_name}_{uuid.uuid4().hex[:6]}" if len(history) == 1 else convs[f"_id_{cid_name}"]
        if len(history) == 1:
            convs[f"_id_{cid_name}"] = cid

        out = chat(history, cid)
        history.append({"role": "assistant", "content": out.get("response", "")})

        # Pull most recent cached_tokens from uvicorn log (Responses API
        # returns it but our chat router doesn't echo it on the wire).
        # Use the latest "input_tokens=" log line that follows our POST.
        # If we can't find it, mark as unknown.
        log_lines = subprocess.run(
            ["tail", "-200", "/tmp/uvicorn.log"],
            capture_output=True, text=True,
        ).stdout
        # OpenAI HTTPS request lines are logged but not the usage. Grep
        # the trace JSON if present.
        in_tok = cached = -1
        # heuristic: scan for last "input_tokens" mention near the tail
        m_in = list(re.finditer(r"input_tokens['\"]?\s*[:=]\s*(\d+)", log_lines))
        m_cc = list(re.finditer(r"cached_tokens['\"]?\s*[:=]\s*(\d+)", log_lines))
        if m_in:
            in_tok = int(m_in[-1].group(1))
        if m_cc:
            cached = int(m_cc[-1].group(1))

        pct = f"{(cached/in_tok*100):.0f}%" if in_tok > 0 and cached >= 0 else "?"
        short = (prompt[:55] + "…") if len(prompt) > 55 else prompt
        print(f"{i:<3} {cid_name:<5} {short:<60} {in_tok:<8} {cached:<8} {pct:<6}")
        time.sleep(0.5)


if __name__ == "__main__":
    main()
