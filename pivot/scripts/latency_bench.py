"""User-facing latency benchmark — measures the STREAMING endpoint the
frontend actually calls (`/chat/stream`), not the blocking `/chat`.

For each representative prompt class it reports, on the warm (2nd) run:
  TTFB  — time to first streamed byte (when the user sees output begin)
  TOTAL — time to the end of the stream (when the full answer is done)

Usage (server on :8000):
  .venv/bin/python scripts/latency_bench.py <label>
"""
import sys
import time
import uuid

import httpx

BASE = "http://localhost:8000"

# What a real retail user actually types, by class:
PROMPTS = [
    ("greeting", "hello, what can you help me with"),
    ("price", "what is TCS trading at right now"),
    ("fundamentals", "is INFY profitable - show PE and ROE"),
    ("analysis", "quick technical read on HDFCBANK"),
    ("build_agent", "buy 10 GRASIM when its RSI drops below 30"),
]


def _turn(c: httpx.Client, headers: dict, prompt: str, conv: str) -> tuple:
    t0 = time.time()
    ttfb = None
    with c.stream(
        "POST", f"{BASE}/chat/stream", headers=headers,
        json={"messages": [{"role": "user", "content": prompt}],
              "conversation_id": conv, "include_portfolio_context": False},
    ) as r:
        for chunk in r.iter_bytes():
            if chunk and ttfb is None:
                ttfb = time.time() - t0
    return ttfb, time.time() - t0


def main() -> None:
    label = sys.argv[1] if len(sys.argv) > 1 else "run"
    with httpx.Client(timeout=180.0) as c:
        tok = c.post(f"{BASE}/auth/register", json={
            "email": f"lat_{uuid.uuid4().hex[:8]}@pivoteval.com",
            "password": "password123", "full_name": "Lat",
        }).json()["access_token"]
        H = {"Authorization": f"Bearer {tok}"}
        print(f"--- bench [{label}] : /chat/stream (TTFB=first token, TOTAL=full answer) ---")
        print(f"{'class':14s}{'TTFB(s)':>9s}{'TOTAL(s)':>10s}   (warm 2nd run)")
        for cls, prompt in PROMPTS:
            warm = (None, None)
            for i in (1, 2):
                try:
                    warm = _turn(c, H, prompt, f"lat_{cls}_{label}_{i}")
                except Exception as e:  # noqa: BLE001
                    print(f"{cls:14s} ERROR {repr(e)[:60]}")
                    warm = (None, None)
                    break
            if warm[0] is not None:
                print(f"{cls:14s}{warm[0]:>9.1f}{warm[1]:>10.1f}")


if __name__ == "__main__":
    main()
