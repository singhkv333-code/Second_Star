"""Send a sequence of chats and read /tmp/llm_trace.jsonl for usage."""
import json
import time
import uuid

import httpx

BASE = "http://127.0.0.1:8000"

PROMPTS = [
    ("c1", "what's the price of TCS"),
    ("c1", "and INFY"),
    ("c1", "and HDFCBANK"),
    ("c2", "what's the price of RELIANCE"),
    ("c2", "what about WIPRO"),
    ("c3", "build me an agent that buys 5 NIFTYBEES every weekday at 9:15"),
    ("c3", "make it 10 shares"),
]


def main():
    convs = {}
    cids = {}
    for cid_name, prompt in PROMPTS:
        history = convs.setdefault(cid_name, [])
        history.append({"role": "user", "content": prompt})
        cid = cids.setdefault(cid_name, f"probe_{cid_name}_{uuid.uuid4().hex[:6]}")
        r = httpx.post(
            f"{BASE}/chat",
            json={"messages": history, "conversation_id": cid, "mode": None},
            timeout=60,
        )
        if r.status_code != 200:
            print(f"FAIL {prompt}: {r.status_code}")
            continue
        d = r.json()
        history.append({"role": "assistant", "content": d.get("response", "")})
        time.sleep(0.4)

    # Read trace
    print()
    print(f"{'#':<3} {'caller':<48} {'cache_key':<32} {'in':<6} {'cached':<7} {'pct':<5} {'lat_ms':<7}")
    print("-" * 115)
    with open("/tmp/llm_trace.jsonl") as f:
        for i, line in enumerate(f, 1):
            try:
                rec = json.loads(line)
            except Exception:
                continue
            usage = rec.get("usage") or {}
            in_tok = usage.get("input_tokens", 0) or 0
            cached = usage.get("cached_tokens", 0) or 0
            pct = f"{(cached / in_tok * 100):.0f}%" if in_tok else "?"
            caller = (rec.get("caller") or "")[:46]
            key = (rec.get("prompt_cache_key") or "")[:30]
            print(
                f"{i:<3} {caller:<48} {key:<32} "
                f"{in_tok:<6} {cached:<7} {pct:<5} {rec.get('latency_ms', 0):<7}"
            )


if __name__ == "__main__":
    main()
