"""Phase 1 chat-cost harness.

Runs N predetermined chat turns against the running backend, in fresh
conversations each time (cold prompt cache), captures the resulting
llm_usage rows + end-to-end latency + response text preview, and writes a
JSON metrics file.

Usage:
    python3 scripts/phase1_harness.py before
    python3 scripts/phase1_harness.py after

Output goes to /tmp/phase1_<label>.json.
"""
from __future__ import annotations

import json
import sys
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import httpx

# Local imports (run from pivot/ working dir)
from backend.auth.jwt_handler import create_access_token
from backend.database import SessionLocal
from backend.models import LlmUsage, User

BACKEND_URL = "http://127.0.0.1:8000"

TEST_PROMPTS = [
    ("educational_rsi", "What is RSI in technical analysis?"),
    ("definition_cnc", "What does 'CNC' mean when placing an order?"),
    ("market_open", "Is the market open right now?"),
    ("sip_vs_lumpsum", "What's the difference between SIP and lump sum investing?"),
    ("circuit_limits", "Explain how upper and lower circuit limits work."),
]


def _decimal(x: Decimal | float | int | None) -> float | None:
    if x is None:
        return None
    return float(x)


def _mint_token() -> str:
    """Mint a JWT for the first user (dev convenience)."""
    db = SessionLocal()
    try:
        user = db.query(User).order_by(User.id).first()
        if user is None:
            raise RuntimeError("No user in DB to mint a token for.")
        return create_access_token(user_id=user.id, email=user.email)
    finally:
        db.close()


def _run_one(client: httpx.Client, token: str, label: str, prompt: str) -> dict:
    conv_id = f"phase1_{label}_{uuid.uuid4().hex[:8]}"
    body = {
        "messages": [{"role": "user", "content": prompt}],
        "include_portfolio_context": False,
        "conversation_id": conv_id,
    }
    headers = {"Authorization": f"Bearer {token}"}

    t0 = time.perf_counter()
    started_at = datetime.now(timezone.utc)
    resp = client.post(f"{BACKEND_URL}/chat", json=body, headers=headers, timeout=120.0)
    latency_ms = (time.perf_counter() - t0) * 1000

    if resp.status_code != 200:
        return {
            "label": label,
            "prompt": prompt,
            "status": resp.status_code,
            "error": resp.text[:500],
            "latency_ms": latency_ms,
        }

    response_json = resp.json()
    reply_text = response_json.get("reply") or response_json.get("text") or json.dumps(response_json)[:500]
    request_id = resp.headers.get("x-request-id")

    # Pull every llm_usage row created since this request started — there
    # may be multiple (router classifier + main turn).
    db = SessionLocal()
    try:
        rows = (
            db.query(LlmUsage)
            .filter(LlmUsage.created_at >= started_at)
            .order_by(LlmUsage.id.asc())
            .all()
        )
        usage = [
            {
                "id": r.id,
                "endpoint": r.endpoint,
                "model": r.model,
                "provider": r.provider,
                "input_tokens": r.input_tokens,
                "output_tokens": r.output_tokens,
                "reasoning_tokens": r.reasoning_tokens,
                "total_tokens": r.total_tokens,
                "cached_input_tokens": getattr(r, "cached_input_tokens", None),
                "cost_usd": _decimal(r.cost_usd),
                "latency_ms": r.latency_ms,
            }
            for r in rows
        ]
    finally:
        db.close()

    return {
        "label": label,
        "prompt": prompt,
        "conv_id": conv_id,
        "request_id": request_id,
        "status": resp.status_code,
        "latency_ms": round(latency_ms, 1),
        "reply_chars": len(reply_text) if isinstance(reply_text, str) else 0,
        "reply_preview": (reply_text if isinstance(reply_text, str) else str(reply_text))[:300],
        "llm_calls": usage,
        "turn_total_input_tokens": sum(u.get("input_tokens") or 0 for u in usage),
        "turn_total_output_tokens": sum(u.get("output_tokens") or 0 for u in usage),
        "turn_total_cost_usd": round(sum(u.get("cost_usd") or 0 for u in usage), 6),
    }


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] not in ("before", "after", "inflight"):
        print("usage: phase1_harness.py <before|after|inflight>", file=sys.stderr)
        return 1
    label = sys.argv[1]

    token = _mint_token()
    print(f"[harness] minted JWT, running {len(TEST_PROMPTS)} turns")

    out_path = f"/tmp/phase1_{label}.json"
    results = []
    with httpx.Client() as client:
        for idx, (test_label, prompt) in enumerate(TEST_PROMPTS, 1):
            print(f"  turn {idx}/{len(TEST_PROMPTS)}: {test_label} — {prompt[:60]!r}")
            r = _run_one(client, token, test_label, prompt)
            results.append(r)
            in_t = r.get("turn_total_input_tokens", 0)
            out_t = r.get("turn_total_output_tokens", 0)
            cost = r.get("turn_total_cost_usd", 0)
            latency = r.get("latency_ms", 0)
            print(f"     status={r.get('status')} in={in_t} out={out_t} cost=${cost} latency={latency}ms")

    summary = {
        "label": label,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "backend_url": BACKEND_URL,
        "n_turns": len(results),
        "total_input_tokens": sum(r.get("turn_total_input_tokens", 0) for r in results),
        "total_output_tokens": sum(r.get("turn_total_output_tokens", 0) for r in results),
        "total_cost_usd": round(sum(r.get("turn_total_cost_usd", 0) for r in results), 6),
        "p50_latency_ms": sorted([r.get("latency_ms", 0) for r in results])[len(results) // 2],
        "max_latency_ms": max(r.get("latency_ms", 0) for r in results),
        "turns": results,
    }
    with open(out_path, "w") as f:
        json.dump(summary, f, indent=2, default=str)
    print(f"\n[harness] saved → {out_path}")
    print(f"  total tokens in: {summary['total_input_tokens']}, out: {summary['total_output_tokens']}")
    print(f"  total cost:      ${summary['total_cost_usd']}")
    print(f"  p50 latency:     {summary['p50_latency_ms']}ms")
    return 0


if __name__ == "__main__":
    sys.exit(main())
