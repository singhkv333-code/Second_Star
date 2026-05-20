"""Phase 1 quality-regression battery — agent construction & automation prompts.

These are the prompt shapes most likely to expose quality regression from
the system.md / agentic_examples / propose_workflow cuts. Categories:

  - simple automation         (trigger + action)
  - scheduled order           (cron-style)
  - threshold order           (% below close)
  - indicator-based crossover (multi-condition)
  - basket allocation
  - holding action            (sell N% of holdings)
  - multi-branch              (if/else)
  - F&O refusal               (unsupported)
  - ambiguous symbol          (clarification)
  - square-off macro

Captures the same metrics as phase1_harness.py plus a fuller reply preview
(800 chars) so quality is eyeball-checkable.
"""
from __future__ import annotations

import json
import sys
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import httpx

from backend.auth.jwt_handler import create_access_token
from backend.database import SessionLocal
from backend.models import LlmUsage, User

BACKEND_URL = "http://127.0.0.1:8000"

# Prompts chosen to exercise the propose_workflow / propose_scheduled_order /
# propose_threshold_order / propose_basket_allocation / propose_holding_action
# macros. These are the surfaces where we cut tool description bytes.
PROMPTS = [
    ("simple_automation",
     "Set up an agent to buy 1 share of TCS at market open every weekday."),
    ("threshold_below_close",
     "Buy HDFCBANK if price drops 3% below previous close today."),
    ("indicator_crossover",
     "Build me an agent that buys NIFTYBEES when the 20-day SMA crosses above the 50-day SMA, and sells when it crosses back below."),
    ("basket_sector",
     "Allocate ₹1,00,000 equally across the top 5 IT stocks."),
    ("holding_action_partial",
     "If TCS hits ₹4500, sell 50% of my holdings."),
    ("multi_branch_index",
     "If NIFTY closes above 22000 buy 10 NIFTYBEES, else buy 10 GOLDBEES."),
    ("fno_refusal",
     "Buy a NIFTY 22000 call option for next week's expiry."),
    ("ambiguous_symbol",
     "Set an alert for HDFC when it crosses 1700."),
    ("scheduled_squareoff",
     "Square off all my intraday positions at 3:15pm every day if I'm in profit."),
    ("tell_me_about",
     "Tell me about Zomato as an investment."),
]


def _decimal(x):
    if x is None:
        return None
    return float(x)


def _mint_token() -> str:
    db = SessionLocal()
    try:
        user = db.query(User).order_by(User.id).first()
        if user is None:
            raise RuntimeError("No user in DB.")
        return create_access_token(user_id=user.id, email=user.email)
    finally:
        db.close()


def _run_one(client: httpx.Client, token: str, label: str, prompt: str) -> dict:
    conv_id = f"phase1_agents_{label}_{uuid.uuid4().hex[:8]}"
    body = {
        "messages": [{"role": "user", "content": prompt}],
        "include_portfolio_context": False,
        "conversation_id": conv_id,
    }
    headers = {"Authorization": f"Bearer {token}"}

    t0 = time.perf_counter()
    started_at = datetime.now(timezone.utc)
    resp = client.post(f"{BACKEND_URL}/chat", json=body, headers=headers, timeout=180.0)
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
    reply_text = response_json.get("reply") or response_json.get("text") or json.dumps(response_json)
    request_id = resp.headers.get("x-request-id")

    # Capture extra signals to judge quality without an LLM-judge.
    tools_called = response_json.get("tools_called") or []
    logiccard = response_json.get("logiccard")
    requires_clarification = response_json.get("requires_clarification", False)
    intent = response_json.get("intent")
    workflow_draft = response_json.get("workflow_draft") or response_json.get("draft")

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
                "endpoint": r.endpoint,
                "model": r.model,
                "input_tokens": r.input_tokens,
                "cached_input_tokens": getattr(r, "cached_input_tokens", 0),
                "output_tokens": r.output_tokens,
                "reasoning_tokens": r.reasoning_tokens,
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
        "intent": intent,
        "tools_called": tools_called,
        "requires_clarification": requires_clarification,
        "has_logiccard": bool(logiccard),
        "has_workflow_draft": bool(workflow_draft),
        "workflow_step_count": (
            len(workflow_draft.get("steps", [])) if isinstance(workflow_draft, dict) else None
        ),
        "reply_chars": len(reply_text) if isinstance(reply_text, str) else 0,
        "reply_preview": (reply_text if isinstance(reply_text, str) else str(reply_text))[:800],
        "llm_calls": usage,
        "turn_input_tokens": sum(u.get("input_tokens") or 0 for u in usage),
        "turn_cached_tokens": sum(u.get("cached_input_tokens") or 0 for u in usage),
        "turn_output_tokens": sum(u.get("output_tokens") or 0 for u in usage),
        "turn_cost_usd": round(sum(u.get("cost_usd") or 0 for u in usage), 6),
    }


def main() -> int:
    token = _mint_token()
    print(f"[agents-eval] running {len(PROMPTS)} agent/automation prompts")

    results = []
    with httpx.Client() as client:
        for idx, (label, prompt) in enumerate(PROMPTS, 1):
            print(f"  {idx}/{len(PROMPTS)} {label} — {prompt[:80]!r}")
            r = _run_one(client, token, label, prompt)
            results.append(r)
            in_t = r.get("turn_input_tokens", 0)
            cached = r.get("turn_cached_tokens", 0)
            out_t = r.get("turn_output_tokens", 0)
            cost = r.get("turn_cost_usd", 0)
            latency = r.get("latency_ms", 0)
            cache_pct = (cached / in_t * 100) if in_t else 0
            tools = ",".join(r.get("tools_called", []) or [])[:60]
            steps = r.get("workflow_step_count")
            extra = ""
            if r.get("has_workflow_draft"):
                extra += f" wf_steps={steps}"
            if r.get("has_logiccard"):
                extra += " logiccard"
            if r.get("requires_clarification"):
                extra += " CLARIFY"
            print(f"     status={r.get('status')} in={in_t} cached={cached} ({cache_pct:.0f}%) out={out_t} "
                  f"cost=${cost} {latency:.0f}ms tools=[{tools}]{extra}")

    summary = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "n_turns": len(results),
        "total_input_tokens": sum(r.get("turn_input_tokens", 0) for r in results),
        "total_cached_tokens": sum(r.get("turn_cached_tokens", 0) for r in results),
        "total_output_tokens": sum(r.get("turn_output_tokens", 0) for r in results),
        "total_cost_usd": round(sum(r.get("turn_cost_usd", 0) for r in results), 6),
        "p50_latency_ms": sorted([r.get("latency_ms", 0) for r in results])[len(results) // 2],
        "max_latency_ms": max(r.get("latency_ms", 0) for r in results),
        "turns": results,
    }
    out_path = "/tmp/phase1_agents.json"
    with open(out_path, "w") as f:
        json.dump(summary, f, indent=2, default=str)
    print(f"\n[agents-eval] saved → {out_path}")
    print(f"  total: {summary['total_input_tokens']} in / {summary['total_cached_tokens']} cached / "
          f"{summary['total_output_tokens']} out")
    print(f"  cost:  ${summary['total_cost_usd']}")
    print(f"  p50 latency: {summary['p50_latency_ms']:.0f}ms, max: {summary['max_latency_ms']:.0f}ms")
    return 0


if __name__ == "__main__":
    sys.exit(main())
