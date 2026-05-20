"""Phase 1 conservative retest — just the multi-turn flows that failed
in eval3, re-run after the structural prompt fixes.

10 prompts across 3 flows. Budget ~$0.07.
"""
from __future__ import annotations

import json
import sys
import time
import uuid
from datetime import datetime, timezone

import httpx

from backend.auth.jwt_handler import create_access_token
from backend.database import SessionLocal
from backend.models import LlmUsage, User

BACKEND_URL = "http://127.0.0.1:8000"

# Only the 3 previously-failing multi-turn flows
CASES = [
    ("edit_chain_momentum",
     ["Build an agent that buys NIFTYBEES when RSI drops below 30.",
      "Make it 25 instead of 30.",
      "Use weekly RSI not daily.",
      "Add an 8% profit target."],
     True),

    ("edit_chain_compound",
     ["Buy TCS when MACD crosses bullish and ADX is above 25.",
      "Lower the ADX threshold to 20.",
      "Add a 5% trailing stop loss."],
     True),

    ("backtest_chain_refine",
     ["Backtest a 10/20 SMA crossover on RELIANCE for 3 years.",
      "Try it with 20/50 SMA instead.",
      "Now add a 5% trailing stop."],
     True),
]


def _mint_token() -> str:
    db = SessionLocal()
    try:
        user = db.query(User).order_by(User.id).first()
        return create_access_token(user_id=user.id, email=user.email)
    finally:
        db.close()


def _decimal(x):
    return float(x) if x is not None else None


def _run_one(client, token, label, prompt, conv_id, history):
    body = {
        "messages": history + [{"role": "user", "content": prompt}],
        "include_portfolio_context": False,
        "conversation_id": conv_id,
    }
    headers = {"Authorization": f"Bearer {token}"}

    t0 = time.perf_counter()
    started_at = datetime.now(timezone.utc)
    resp = client.post(f"{BACKEND_URL}/chat", json=body, headers=headers, timeout=180.0)
    latency_ms = (time.perf_counter() - t0) * 1000

    if resp.status_code != 200:
        return {"label": label, "prompt": prompt, "status": resp.status_code,
                "error": resp.text[:500], "latency_ms": latency_ms}

    rj = resp.json()
    reply_text = rj.get("response") or rj.get("reply") or json.dumps(rj)
    tools_called = rj.get("tools_called") or []
    raw = rj.get("raw_data") or {}
    has_draft = any(k.startswith("propose_") for k in raw.keys()) if isinstance(raw, dict) else False
    render_hint = raw.get("_render_hint") if isinstance(raw, dict) else None

    db = SessionLocal()
    try:
        rows = (db.query(LlmUsage)
                .filter(LlmUsage.created_at >= started_at)
                .order_by(LlmUsage.id.asc()).all())
        usage = [{
            "input_tokens": r.input_tokens,
            "cached_input_tokens": getattr(r, "cached_input_tokens", 0),
            "output_tokens": r.output_tokens,
            "cost_usd": _decimal(r.cost_usd),
        } for r in rows]
    finally:
        db.close()

    return {
        "label": label,
        "prompt": prompt,
        "status": resp.status_code,
        "latency_ms": round(latency_ms, 1),
        "tools_called": tools_called,
        "render_hint": render_hint,
        "has_draft": has_draft,
        "assistant_reply_full": reply_text,
        "reply_preview": reply_text[:600],
        "turn_input_tokens": sum(u["input_tokens"] for u in usage),
        "turn_cached_tokens": sum(u["cached_input_tokens"] for u in usage),
        "turn_output_tokens": sum(u["output_tokens"] for u in usage),
        "turn_cost_usd": round(sum(u["cost_usd"] or 0 for u in usage), 6),
    }


def main() -> int:
    token = _mint_token()
    total_prompts = sum(len(c[1]) for c in CASES)
    print(f"[retest] {len(CASES)} cases / {total_prompts} prompts")

    results = []
    with httpx.Client() as client:
        for case_idx, (label, prompts, share_conv) in enumerate(CASES, 1):
            conv_id = f"phase1_retest_{label}_{uuid.uuid4().hex[:8]}"
            history = []
            for prompt_idx, prompt in enumerate(prompts, 1):
                turn_label = f"{label}.t{prompt_idx}"
                print(f"  {case_idx}.{prompt_idx} {turn_label} — {prompt[:80]!r}")
                r = _run_one(client, token, turn_label, prompt, conv_id, history)
                results.append(r)
                if share_conv and prompt_idx < len(prompts):
                    history.append({"role": "user", "content": prompt})
                    history.append({"role": "assistant", "content": r.get("assistant_reply_full", "")})
                tools = ",".join(r.get("tools_called") or [])[:50]
                cache_pct = (r.get("turn_cached_tokens", 0) / r.get("turn_input_tokens", 1) * 100) if r.get("turn_input_tokens") else 0
                draft = " DRAFT" if r.get("has_draft") else ""
                clarify = " CLARIFY" if r.get("render_hint") == "ask_user" else ""
                print(f"      in={r.get('turn_input_tokens')} cached={r.get('turn_cached_tokens')} ({cache_pct:.0f}%) "
                      f"out={r.get('turn_output_tokens')} cost=${r.get('turn_cost_usd')} "
                      f"{r.get('latency_ms')}ms tools=[{tools}]{draft}{clarify}")

    summary = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "n_prompts": len(results),
        "total_input_tokens": sum(r.get("turn_input_tokens", 0) for r in results),
        "total_cached_tokens": sum(r.get("turn_cached_tokens", 0) for r in results),
        "total_output_tokens": sum(r.get("turn_output_tokens", 0) for r in results),
        "total_cost_usd": round(sum(r.get("turn_cost_usd", 0) for r in results), 6),
        "p50_latency_ms": sorted([r.get("latency_ms", 0) for r in results])[len(results) // 2],
        "turns": results,
    }
    out_path = "/tmp/phase1_retest.json"
    with open(out_path, "w") as f:
        light = json.loads(json.dumps(summary, default=str))
        for t in light["turns"]:
            t.pop("assistant_reply_full", None)
        json.dump(light, f, indent=2)
    print(f"\n[retest] saved → {out_path}")
    print(f"  total: {summary['total_input_tokens']} in / {summary['total_cached_tokens']} cached / "
          f"{summary['total_output_tokens']} out")
    print(f"  cost:  ${summary['total_cost_usd']}")
    print(f"  p50:   {summary['p50_latency_ms']:.0f}ms")
    return 0


if __name__ == "__main__":
    sys.exit(main())
