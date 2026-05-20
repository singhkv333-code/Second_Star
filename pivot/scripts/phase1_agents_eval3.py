"""Phase 1 — round 3. Multi-turn workflows + backtest variations.

All sessions are multi-turn (shared conv_id, history carried) so we test
the conversation-history path the FE actually uses. Categories:

  - WORKFLOW EDIT CHAINS (build → modify → modify → cancel/activate)
  - BACKTEST VARIATIONS (single, crossover, compound, with stops, multi-symbol)
  - WORKFLOW + BACKTEST MIXED (backtest then convert to live)
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

# (label, [list of prompts], share_conv: bool)
CASES = [
    # ── WORKFLOW EDIT CHAINS ──────────────────────────────────────────────

    # 1. Build, then progressively refine a momentum agent
    ("edit_chain_momentum",
     ["Build an agent that buys NIFTYBEES when RSI drops below 30.",
      "Make it 25 instead of 30.",
      "Use weekly RSI not daily.",
      "Add an 8% profit target."],
     True),

    # 2. Build then change schedule then cancel
    ("edit_chain_schedule_cancel",
     ["Buy 10 RELIANCE every Friday at 2:30pm.",
      "Make it Mondays not Fridays.",
      "Actually cancel that."],
     True),

    # 3. Build compound condition, modify, add SL
    ("edit_chain_compound",
     ["Buy TCS when MACD crosses bullish and ADX is above 25.",
      "Lower the ADX threshold to 20.",
      "Add a 5% trailing stop loss."],
     True),

    # 4. Build basket, then refine theme + cadence
    ("edit_chain_basket",
     ["Allocate ₹50,000 across the top 5 banking stocks every month.",
      "Make it weekly instead of monthly.",
      "Use the top 8 instead of top 5."],
     True),

    # 5. Mistake-correction flow
    ("edit_chain_mistake",
     ["Buy TCS when its price crosses above the 200-day EMA.",
      "Sorry I meant 200-day SMA, not EMA.",
      "And only on weekdays."],
     True),

    # ── BACKTEST VARIATIONS (each one-shot) ───────────────────────────────

    # 6. Simple oscillator threshold both ways
    ("backtest_rsi_dual",
     ["Backtest: buy TCS when RSI(14) drops below 25 and sell when RSI(14) rises above 75, over 5 years."],
     False),

    # 7. EMA-vs-EMA crossover (similar pattern to SMA case)
    ("backtest_ema_cross",
     ["Backtest a 9-day EMA crossing above the 21-day EMA on RELIANCE, last 3 years."],
     False),

    # 8. Compound condition entry
    ("backtest_compound_entry",
     ["Backtest: buy NIFTYBEES when MACD turns bullish AND RSI is above 50, over the last 5 years."],
     False),

    # 9. Backtest with trailing stop
    ("backtest_with_trailing_stop",
     ["Backtest buying HDFCBANK on RSI below 30 with a 5% trailing stop, last 3 years."],
     False),

    # 10. Bollinger Band breakout (we said BB unsupported for live; backtest list DOES include bb)
    ("backtest_bb_breakout",
     ["Backtest: buy TCS when price closes above the upper Bollinger Band (20-period, 2 std), last 2 years."],
     False),

    # 11. Multi-turn backtest refinement (high risk pattern)
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
        return {
            "label": label,
            "prompt": prompt,
            "status": resp.status_code,
            "error": resp.text[:500],
            "latency_ms": latency_ms,
        }

    response_json = resp.json()
    reply_text = response_json.get("response") or response_json.get("reply") or json.dumps(response_json)
    tools_called = response_json.get("tools_called") or []
    raw = response_json.get("raw_data") or {}
    has_draft = any(k.startswith("propose_") for k in raw.keys()) if isinstance(raw, dict) else False
    render_hint = raw.get("_render_hint") if isinstance(raw, dict) else None

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
                "input_tokens": r.input_tokens,
                "cached_input_tokens": getattr(r, "cached_input_tokens", 0),
                "output_tokens": r.output_tokens,
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
        "status": resp.status_code,
        "latency_ms": round(latency_ms, 1),
        "tools_called": tools_called,
        "render_hint": render_hint,
        "has_draft": has_draft,
        "reply_chars": len(reply_text) if isinstance(reply_text, str) else 0,
        "reply_preview": (reply_text if isinstance(reply_text, str) else str(reply_text))[:700],
        "assistant_reply_full": reply_text,
        "llm_calls": usage,
        "turn_input_tokens": sum(u["input_tokens"] for u in usage),
        "turn_cached_tokens": sum(u["cached_input_tokens"] for u in usage),
        "turn_output_tokens": sum(u["output_tokens"] for u in usage),
        "turn_cost_usd": round(sum(u["cost_usd"] or 0 for u in usage), 6),
    }


def main() -> int:
    token = _mint_token()
    print(f"[eval-3] running {len(CASES)} cases ({sum(len(c[1]) for c in CASES)} prompts)")

    results = []
    with httpx.Client() as client:
        for case_idx, (label, prompts, share_conv) in enumerate(CASES, 1):
            conv_id = f"phase1_e3_{label}_{uuid.uuid4().hex[:8]}"
            history = []
            for prompt_idx, prompt in enumerate(prompts, 1):
                turn_label = f"{label}.t{prompt_idx}" if len(prompts) > 1 else label
                print(f"  {case_idx}.{prompt_idx} {turn_label} — {prompt[:80]!r}")
                r = _run_one(client, token, turn_label, prompt, conv_id, history)
                results.append(r)
                if share_conv and prompt_idx < len(prompts):
                    history.append({"role": "user", "content": prompt})
                    history.append({"role": "assistant", "content": r.get("assistant_reply_full", "")})
                in_t = r.get("turn_input_tokens", 0)
                cached = r.get("turn_cached_tokens", 0)
                out_t = r.get("turn_output_tokens", 0)
                cost = r.get("turn_cost_usd", 0)
                latency = r.get("latency_ms", 0)
                tools = ",".join(r.get("tools_called") or [])[:50]
                cache_pct = (cached / in_t * 100) if in_t else 0
                draft = " DRAFT" if r.get("has_draft") else ""
                clarify = " CLARIFY" if r.get("render_hint") == "ask_user" else ""
                print(f"      status={r.get('status')} in={in_t} cached={cached} ({cache_pct:.0f}%) out={out_t} "
                      f"cost=${cost} {latency:.0f}ms tools=[{tools}]{draft}{clarify}")

    summary = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "n_prompts": len(results),
        "total_input_tokens": sum(r.get("turn_input_tokens", 0) for r in results),
        "total_cached_tokens": sum(r.get("turn_cached_tokens", 0) for r in results),
        "total_output_tokens": sum(r.get("turn_output_tokens", 0) for r in results),
        "total_cost_usd": round(sum(r.get("turn_cost_usd", 0) for r in results), 6),
        "p50_latency_ms": sorted([r.get("latency_ms", 0) for r in results])[len(results) // 2],
        "max_latency_ms": max(r.get("latency_ms", 0) for r in results),
        "turns": results,
    }
    out_path = "/tmp/phase1_agents_3.json"
    with open(out_path, "w") as f:
        light = json.loads(json.dumps(summary, default=str))
        for t in light["turns"]:
            t.pop("assistant_reply_full", None)
        json.dump(light, f, indent=2)
    print(f"\n[eval-3] saved → {out_path}")
    print(f"  total: {summary['total_input_tokens']} in / {summary['total_cached_tokens']} cached / "
          f"{summary['total_output_tokens']} out")
    print(f"  cost:  ${summary['total_cost_usd']}")
    print(f"  p50 latency: {summary['p50_latency_ms']:.0f}ms")
    return 0


if __name__ == "__main__":
    sys.exit(main())
