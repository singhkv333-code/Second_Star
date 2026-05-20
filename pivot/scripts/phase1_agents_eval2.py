"""Phase 1 quality-regression battery — round 2.

Targets the edge cases the first eval didn't touch, focusing on areas
where the prompt cuts are most likely to bite:

  - multi-turn draft modification (pronoun-extend)   ← cut from examples
  - alternate F&O phrasings                          ← wrong-phrasing list cut
  - theme-based basket allocation                    ← theme examples cut
  - strategy backtest                                ← minimal cuts, verify
  - ticker inference variants                        ← table moved out of tool desc
  - unsupported order types (OCO, bracket)           ← unsupported list cut
  - educational comparison                           ← anecdote-style cut
  - symbol disambiguation                            ← multiple HDFC entities

Each "case" is one or two prompts. Multi-turn cases reuse the same
conversation_id so the model sees the prior draft.
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

# Multi-turn cases use a shared conv_id so the second turn sees the draft.
# Each case: (label, list of prompts, share_conv: bool)
CASES = [
    # 1. Multi-turn: build a draft, then modify it (pronoun-extend)
    ("modify_draft_add_sl",
     ["Buy 5 shares of TCS at market price.",
      "Add a stop-loss at 5% below entry."],
     True),

    # 2. Alternate F&O phrasings (should all refuse)
    ("fno_sell_pe",
     ["Sell a NIFTY 22500 put option."],
     False),

    # 3. Theme-based basket — we cut theme examples
    ("theme_basket_ev",
     ["Invest ₹50,000 across EV-themed stocks."],
     False),

    # 4. Strategy backtest — minimal cuts but verify
    ("backtest_sma_cross",
     ["Backtest a 50-day vs 200-day SMA crossover strategy on NIFTYBEES over 2 years."],
     False),

    # 5. Ticker variant — "Reliance" should map to RELIANCE
    ("ticker_variant_reliance",
     ["What's the current price of Reliance?"],
     False),

    # 6. Ticker disambiguation — "HDFC" today means HDFCBANK (after merger)
    ("ticker_hdfc_after_merger",
     ["Show me HDFC's PE ratio."],
     False),

    # 7. Unsupported order type (OCO / bracket)
    ("oco_unsupported",
     ["Set an OCO order for TCS with target 4500 and stoploss 4200."],
     False),

    # 8. Educational comparison
    ("compare_rsi_macd",
     ["What's the difference between RSI and MACD?"],
     False),

    # 9. Cancel / pause flow (multi-turn — create then cancel)
    ("cancel_active_draft",
     ["Buy 1 share of INFY at market open.",
      "Cancel that."],
     True),

    # 10. Notification-style automation (we cut email/SMS hints)
    ("alert_only_no_order",
     ["Notify me when ITC drops below 400."],
     False),

    # ─── Multi-workflow / complex agent construction ──────────────────────

    # 11. Multi-condition AND (compound trigger logic)
    ("multi_cond_and",
     ["Buy TCS only if RSI is below 30 AND the price is below the 200-day SMA."],
     False),

    # 12. Multi-condition OR (compound exit logic)
    ("multi_cond_or",
     ["Sell my TCS holdings if price drops 5% from entry OR if RSI hits 70."],
     False),

    # 13. Position scaling / DCA-style ladder
    ("dca_ladder",
     ["Buy 2 shares of TCS every time it drops 1% from previous close, up to a maximum of 10 shares."],
     False),

    # 14. MULTI-TURN workflow edit (the high-risk one)
    ("workflow_edit",
     ["Build me an agent to buy NIFTYBEES at the 20-day SMA crossover.",
      "Make it 50/100 SMA instead.",
      "And add a profit target of 8%."],
     True),

    # 15. Combined action — order + notification on same trigger
    ("combined_action",
     ["When TCS hits 4500, sell 50% of my holdings AND notify me."],
     False),

    # 16. Portfolio-risk gated automation
    ("risk_gated",
     ["Buy NIFTYBEES every Monday for ₹10,000, but stop the SIP if my portfolio drops more than 10% from peak."],
     False),

    # 17. Time-window + conditional
    ("time_window_conditional",
     ["Buy 1 RELIANCE between 10:00 and 10:30 IST, but only on days when NIFTY opened red."],
     False),

    # 18. Template request — well-known strategy name
    ("template_bollinger",
     ["Set up a Bollinger Band breakout strategy on RELIANCE."],
     False),
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
    requires_clarification = response_json.get("requires_clarification", False)
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
        "requires_clarification": requires_clarification,
        "render_hint": render_hint,
        "has_draft": has_draft,
        "reply_chars": len(reply_text) if isinstance(reply_text, str) else 0,
        "reply_preview": (reply_text if isinstance(reply_text, str) else str(reply_text))[:600],
        "assistant_reply_full": reply_text,
        "llm_calls": usage,
        "turn_input_tokens": sum(u["input_tokens"] for u in usage),
        "turn_cached_tokens": sum(u["cached_input_tokens"] for u in usage),
        "turn_output_tokens": sum(u["output_tokens"] for u in usage),
        "turn_cost_usd": round(sum(u["cost_usd"] or 0 for u in usage), 6),
    }


def main() -> int:
    token = _mint_token()
    print(f"[agents-eval-2] running {len(CASES)} cases ({sum(len(c[1]) for c in CASES)} prompts)")

    results = []
    with httpx.Client() as client:
        for case_idx, (label, prompts, share_conv) in enumerate(CASES, 1):
            conv_id = f"phase1_e2_{label}_{uuid.uuid4().hex[:8]}"
            history = []
            for prompt_idx, prompt in enumerate(prompts, 1):
                turn_label = f"{label}.t{prompt_idx}" if len(prompts) > 1 else label
                print(f"  {case_idx}.{prompt_idx} {turn_label} — {prompt[:80]!r}")
                # If sharing conv, also send the prior assistant reply in history
                # (mirror what the FE does).
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
    out_path = "/tmp/phase1_agents_2.json"
    with open(out_path, "w") as f:
        # Strip the long assistant_reply_full for compactness; keep reply_preview
        light = json.loads(json.dumps(summary, default=str))
        for t in light["turns"]:
            t.pop("assistant_reply_full", None)
        json.dump(light, f, indent=2)
    print(f"\n[agents-eval-2] saved → {out_path}")
    print(f"  total: {summary['total_input_tokens']} in / {summary['total_cached_tokens']} cached / "
          f"{summary['total_output_tokens']} out")
    print(f"  cost:  ${summary['total_cost_usd']}")
    print(f"  p50 latency: {summary['p50_latency_ms']:.0f}ms")
    return 0


if __name__ == "__main__":
    sys.exit(main())
