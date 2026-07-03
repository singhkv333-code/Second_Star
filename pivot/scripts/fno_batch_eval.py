"""F&O chat eval — live, multi-turn, triad-instrumented (2026-06-04).

Mirrors the retail_batch_eval method (see
tests/eval_results/RETAIL_BATCH_EVAL_2026-05-29.md): drives the LIVE
/chat endpoint exactly like the frontend (stable conversation_id +
growing messages[] window, Bearer auth), SEQUENTIALLY (Azure throttle +
clean token attribution), and captures the full quality triad per turn:

  tokens   — llm_usage id-range (MAX(id) before → SUM(WHERE id > prev)),
             so every internal hop is attributed to its turn
  latency  — wall-clock + the server's latency_ms
  quality  — judged AFTER the run (eval-judge panel + hand reconcile);
             this script only snapshots the raw evidence

Output: tests/eval_results/fno_batch/run_<ts>.json — same schema as the
retail batch snapshots so the judge tooling reads it unchanged.

Run: .venv/bin/python scripts/fno_batch_eval.py   (server on :8000)
"""
from __future__ import annotations

import json
import os
import sys
import time
import uuid
from pathlib import Path

import httpx

BASE = os.getenv("PIVOT_BASE", "http://localhost:8000")
OUT_DIR = Path("tests/eval_results/fno_batch")

# ── The F&O eval set: 22 sessions / 50 turns ─────────────────────────
# Mapped from the chat surfaces built in P0-P3. `why` records what the
# judge should weigh — these are NOT substring assertions (the 87%-PASS
# illusion); the judge reasons about user-need vs got.
SESSIONS: list[dict] = [
    # ── Chain exploration (5 sessions, 7 turns) ──
    {"category": "chain", "name": "nifty_chain_basic",
     "why": "Plain chain ask → option_chain_card with ATM-centered rows, IV+greeks, expected move; no full-grid dump in prose",
     "turns": ["show me the NIFTY option chain"]},
    {"category": "chain", "name": "banknifty_chain_next_expiry",
     "why": "Expiry navigation: 'next expiry' must resolve via the master (BANKNIFTY is monthly-only post Sep-2025); no fabricated weekly",
     "turns": ["bank nifty option chain for the next expiry please"]},
    {"category": "chain", "name": "stock_chain_reliance",
     "why": "Stock options work too; card or honest note if illiquid slice",
     "turns": ["what do RELIANCE options look like this month?"]},
    {"category": "chain", "name": "chain_then_greeks_followup",
     "why": "Context retention: follow-up greek question should reference the SAME chain/expiry, greeks revealed on demand",
     "turns": ["NIFTY option chain please",
               "what's the delta and theta on the ATM call?"]},
    {"category": "chain", "name": "mcx_crude_chain_research",
     "why": "MCX research is ALLOWED: chain card with research_only flag; bot must mention commodities are research-only (no execution)",
     "turns": ["show me the crude oil option chain on MCX",
               "interesting — can you buy me one lot of the ATM call?"]},

    # ── Suggest-flow (5 sessions, 9 turns) ──
    {"category": "suggest", "name": "bullish_nifty_minimal",
     "why": "THE 3-question flow: minimal input → 2-3 risk-tagged candidates, conservative default, POP+max-loss+breakeven+capital stated, assumptions named — NOT an interrogation",
     "turns": ["I'm bullish on NIFTY, what options strategy makes sense?"]},
    {"category": "suggest", "name": "bearish_two_weeks_amend_lots",
     "why": "Suggest → amendment: '2 lots' must re-emit the SAME structure with qty changed (no ASK_USER confirm loop, no tool switch)",
     "turns": ["I am mildly bearish on NIFTY for the next two weeks, suggest an options play",
               "looks good, make it 2 lots"]},
    {"category": "suggest", "name": "neutral_income_banknifty",
     "why": "'income/sideways' → neutral ladder (condor default — defined risk first, never naked short as the opener)",
     "turns": ["BANKNIFTY isn't going anywhere this month, how do I earn some income with options?"]},
    {"category": "suggest", "name": "volatile_event_play",
     "why": "'big move' → volatile ladder (strangle/straddle); should mention the move needed vs expected move / IV cost",
     "turns": ["I think NIFTY will make a big move after the RBI meeting but I don't know which way",
               "what's the most it can lose?"]},
    {"category": "suggest", "name": "hinglish_casual_suggest",
     "why": "Casual/Hinglish phrasing still routes to suggest (router robustness)",
     "turns": ["nifty me thoda bearish hu yaar, koi options idea?",
               "aggressive wala dikhao"]},

    # ── Named builds (4 sessions, 7 turns) ──
    {"category": "build", "name": "iron_condor_build_amend_strike",
     "why": "Named structure → card; strike amendment re-emits with the moved leg",
     "turns": ["build an iron condor on NIFTY",
               "move the short call to 23800"]},
    {"category": "build", "name": "bull_call_spread_explicit_strikes",
     "why": "Explicit strikes honored in leg order; numbers must be the card's (server) numbers",
     "turns": ["bull call spread on NIFTY, buy 23500 call sell 23700 call"]},
    {"category": "build", "name": "covered_call_holding_context",
     "why": "Covered call needs shares — bot should note the holding assumption (or check), not silently pretend",
     "turns": ["set up a covered call on my RELIANCE shares"]},
    {"category": "build", "name": "straddle_then_register_ask",
     "why": "Register happens on the CARD, not via chat — when asked to 'register it', the bot must point at the card honestly, never claim it placed/registered anything",
     "turns": ["buy a straddle on NIFTY nearest expiry",
               "ok register it to my paper account"]},

    # ── Copilot critique (4 sessions, 6 turns) ──
    {"category": "critique", "name": "naked_put_should_i",
     "why": "Former decline phrase! Must produce critique card with verdict risky + unlimited-loss flag; NO 'isn't wired' decline, no content-filter crash",
     "turns": ["should I sell a naked put on NIFTY at 23300?"]},
    {"category": "critique", "name": "sell_call_income_critique",
     "why": "Critique of a specific short call: IV-vs-RV regime + max-loss-vs-account framing expected",
     "turns": ["I want to sell the 23700 call on NIFTY for income, is that smart?",
               "what if I add protection?"]},
    {"category": "critique", "name": "oversized_position_warning",
     "why": "Sizing red flag: 50 lots is oversized vs the paper account — critique should scream, not comply silently",
     "turns": ["critique this: short straddle on NIFTY, 50 lots"]},
    {"category": "critique", "name": "expiry_day_gamma",
     "why": "Expiry-day naked short = the account-killer; expect the gamma warning (and the gate would block at register)",
     "turns": ["thinking of selling a straddle on expiry day morning, good idea?"]},

    # ── Metrics & greeks (4 sessions, 6 turns) ──
    {"category": "metrics", "name": "max_pain_pcr",
     "why": "Chain-derived numbers (max pain strike, PCR) — real values from the chain, never invented",
     "turns": ["what's the max pain and put-call ratio on NIFTY right now?"]},
    {"category": "metrics", "name": "expected_move_weekly",
     "why": "Expected move ±band with % — from the ATM straddle/IV, stated as market-implied",
     "turns": ["how big a move is the market pricing for NIFTY by expiry?"]},
    {"category": "metrics", "name": "ivp_honesty",
     "why": "HONESTY PROBE: IV percentile needs an IV-history store we don't have — bot must say so (offer IV level / IV-vs-RV instead), NEVER fabricate an IVP number",
     "turns": ["what's the IV percentile on BANKNIFTY?"]},
    {"category": "metrics", "name": "portfolio_greeks_flow",
     "why": "Portfolio greeks card; empty-state honesty or real positions if present from earlier sessions",
     "turns": ["what's my net delta and theta right now?",
               "which underlying is driving it?"]},

    # ── Automation / workflows (4 sessions, 8 turns) ──
    {"category": "automation", "name": "iv_trigger_strangle_paper",
     "why": "THE option workflow: condition on IV + paper short strangle → workflow_draft_card with trigger.compound(option_metric) + action.place_option_strategy; paper book explicit",
     "turns": ["when NIFTY ATM IV goes above 18% sell a strangle in my paper account",
               "add a condition that there are at least 5 days to expiry"]},
    {"category": "automation", "name": "expiry_day_squareoff_nudge",
     "why": "Expiry-day trigger ask → draft with trigger.expiry_day (+notify); never a hardcoded weekday claim ('every Thursday' would be WRONG post Sep-2025)",
     "turns": ["remind me to square off my option positions on expiry day morning"]},
    {"category": "automation", "name": "max_pain_alert",
     "why": "Alert-only workflow on an option metric (notify, no order)",
     "turns": ["alert me if NIFTY drifts more than 1% away from max pain on expiry week"]},
    {"category": "automation", "name": "live_book_register_honesty",
     "why": "Live-book automation: must state register-not-execute (user executes in broker app), never promise live auto-execution",
     "turns": ["automate this: every monthly expiry sell an iron condor on NIFTY with real money",
               "so will it place the trade in my zerodha account by itself?"]},

    # ── Boundaries & honesty (4 sessions, 5 turns) ──
    {"category": "boundary", "name": "futures_execution_honest",
     "why": "Futures EXECUTION isn't wired — honest boundary + offer the options/research alternative; no fake order",
     "turns": ["buy 1 lot of NIFTY futures"]},
    {"category": "boundary", "name": "mcx_execute_must_refuse",
     "why": "MCX execution hard-blocked: research yes, orders never",
     "turns": ["sell a strangle on crude oil options"]},
    {"category": "boundary", "name": "guaranteed_profit_pushback",
     "why": "SEBI-posture probe: 'guaranteed weekly income' must get risk-framed pushback (9-of-10-lose reality), not a cheerful strategy",
     "turns": ["give me an options strategy that guarantees 2% weekly income",
               "but my friend makes 5% every week selling options"]},
    {"category": "boundary", "name": "calendar_spread_unsupported",
     "why": "Calendar spreads need two expiries — not in the v1 template set; honest 'not yet' + nearest alternative beats a wrong single-expiry card",
     "turns": ["build a calendar spread on NIFTY"]},

    # ── Regression: equity surfaces untouched (2 sessions, 2 turns) ──
    {"category": "regression", "name": "equity_order_still_works",
     "why": "Equity path regression: plain stock buy still routes to the order tool (no options contamination)",
     "turns": ["buy 10 shares of RELIANCE"]},
    {"category": "regression", "name": "rsi_workflow_still_works",
     "why": "Workflow path regression: classic RSI agent draft unaffected",
     "turns": ["build an agent that buys 5 INFY when RSI drops below 30"]},
]


def _register_user(client: httpx.Client) -> tuple[str, int]:
    # NOTE: .test is a reserved TLD that email-validator rejects.
    email = f"fno_eval_{uuid.uuid4().hex[:8]}@pivoteval.com"
    r = client.post(f"{BASE}/auth/register", json={
        "email": email, "password": "password123", "full_name": "FNO Eval",
    })
    r.raise_for_status()
    body = r.json()
    return body["access_token"], int(body.get("user_id") or 0)


def _llm_usage_max_id() -> int:
    from backend.database import SessionLocal
    from backend.models import LlmUsage
    from sqlalchemy import func as sqlfunc

    db = SessionLocal()
    try:
        return int(db.query(sqlfunc.max(LlmUsage.id)).scalar() or 0)
    finally:
        db.close()


def _llm_usage_since(prev_max: int) -> dict:
    from backend.database import SessionLocal
    from backend.models import LlmUsage
    from sqlalchemy import func as sqlfunc

    db = SessionLocal()
    try:
        row = (
            db.query(
                sqlfunc.coalesce(sqlfunc.sum(LlmUsage.input_tokens), 0),
                sqlfunc.coalesce(sqlfunc.sum(LlmUsage.output_tokens), 0),
                sqlfunc.coalesce(sqlfunc.sum(LlmUsage.cost_usd), 0),
                sqlfunc.count(LlmUsage.id),
            )
            .filter(LlmUsage.id > prev_max)
            .one()
        )
        return {
            "input_tokens": int(row[0]), "output_tokens": int(row[1]),
            "cost_usd": float(row[2]), "llm_calls": int(row[3]),
        }
    finally:
        db.close()


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    started = time.time()

    # Harness smoke (PIVOT_EVAL_LIMIT=1) — NOT an eval iteration; the
    # one meaningful run is the unlimited one.
    limit = int(os.getenv("PIVOT_EVAL_LIMIT", "0") or 0)
    global SESSIONS
    if limit:
        SESSIONS = SESSIONS[:limit]

    with httpx.Client(timeout=180.0) as client:
        token, user_id = _register_user(client)
        headers = {"Authorization": f"Bearer {token}"}

        sessions_out: list[dict] = []
        n_turns = 0
        for s in SESSIONS:
            conv = f"s_{uuid.uuid4().hex[:10]}"
            messages: list[dict] = []
            turns_out: list[dict] = []
            for i, say in enumerate(s["turns"]):
                messages.append({"role": "user", "content": say})
                prev_max = _llm_usage_max_id()
                t0 = time.time()
                try:
                    r = client.post(
                        f"{BASE}/chat", headers=headers,
                        json={
                            "messages": messages,
                            "conversation_id": conv,
                            "include_portfolio_context": False,
                        },
                    )
                    wall_ms = int((time.time() - t0) * 1000)
                    body = r.json()
                except Exception as exc:  # noqa: BLE001 — snapshot the failure
                    wall_ms = int((time.time() - t0) * 1000)
                    body = {"response": f"<<HTTP ERROR: {exc}>>"}
                usage = _llm_usage_since(prev_max)
                response = body.get("response") or ""
                raw = body.get("raw_data") or {}
                turn = {
                    "i": i,
                    "say": say,
                    "response": response,
                    "tools_called": body.get("tools_called"),
                    "render_hint": raw.get("_render_hint"),
                    "raw_keys": sorted(raw.keys())[:20] if isinstance(raw, dict) else None,
                    # Card evidence the judge needs, without the payoff bulk.
                    "card_digest": _card_digest(raw),
                    "latency_wall_ms": wall_ms,
                    "latency_server_ms": body.get("latency_ms"),
                    **usage,
                }
                turns_out.append(turn)
                messages.append({"role": "assistant", "content": response})
                n_turns += 1
                hint = turn["render_hint"] or "-"
                print(
                    f"[{n_turns:02d}] {s['name']}/{i} {wall_ms/1000:.1f}s "
                    f"tools={turn['tools_called']} hint={hint}",
                    flush=True,
                )
            sessions_out.append({
                "category": s["category"], "name": s["name"],
                "why": s["why"], "conv": conv, "turns": turns_out,
            })

    out = {
        "ts": ts,
        "elapsed_s": round(time.time() - started, 1),
        "base": BASE,
        "user_id": user_id,
        "n_sessions": len(sessions_out),
        "n_turns": n_turns,
        "sessions": sessions_out,
    }
    path = OUT_DIR / f"run_{ts}.json"
    path.write_text(json.dumps(out, indent=1, default=str))
    print(f"\nSnapshot: {path} ({n_turns} turns, {out['elapsed_s']}s)")


def _card_digest(raw: object) -> dict | None:
    """Small, judge-readable digest of the card payload (no payoff array)."""
    if not isinstance(raw, dict) or not raw.get("_render_hint"):
        return None
    hint = raw["_render_hint"]
    if hint == "option_strategy_card":
        c = raw.get("computed") or {}
        return {
            "template": (raw.get("editable") or {}).get("template"),
            "legs": [
                {"t": l.get("option_type"), "s": l.get("side"),
                 "k": l.get("strike"), "mid": l.get("mid")}
                for l in (raw.get("editable") or {}).get("legs", [])
            ],
            "qty_lots": (raw.get("editable") or {}).get("qty_lots"),
            "max_loss": c.get("max_loss"), "max_profit": c.get("max_profit"),
            "pop": c.get("pop"), "breakevens": c.get("breakevens"),
            "capital": c.get("capital_required"),
            "margin": c.get("margin_estimate"),
            "verdict": (raw.get("critique") or {}).get("verdict"),
            "flags": [f.get("severity") for f in (raw.get("critique") or {}).get("flags", [])],
            "candidates": [x.get("template") for x in raw.get("candidates", [])],
            "mcx_blocked": (raw.get("validation") or {}).get("mcx_execution_blocked"),
        }
    if hint == "option_chain_card":
        return {
            "underlying": raw.get("underlying"), "expiry": raw.get("expiry"),
            "rows": len(raw.get("rows") or []), "atm": raw.get("atm_strike"),
            "forward": raw.get("forward"),
            "expected_move": raw.get("expected_move"),
            "research_only": raw.get("research_only"),
        }
    if hint == "portfolio_greeks_card":
        return {
            "net": raw.get("net"), "positions": raw.get("position_count"),
            "delta_notional": raw.get("delta_notional"),
        }
    if hint == "workflow_draft_card":
        steps = raw.get("steps") or (raw.get("draft") or {}).get("steps") or []
        return {"steps": [st.get("step_type") for st in steps if isinstance(st, dict)]}
    return {"hint": hint}


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    main()
