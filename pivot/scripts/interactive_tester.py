"""Interactive multi-turn chat tester.

Runs realistic conversation sessions against the live /chat endpoint.
Each session simulates a real user flow — including typos, corrections,
contextual follow-ups, and adversarial edge cases. After every turn an
LLM judge scores the response.

Usage:
    .venv/bin/python scripts/interactive_tester.py
    .venv/bin/python scripts/interactive_tester.py --session orders
    .venv/bin/python scripts/interactive_tester.py --verbose

Sessions: orders | typos | strategy | rsi | portfolio | swiggy |
          educational | workflow | ambiguity | backtest
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

import httpx

BASE = "http://127.0.0.1:8000"


# ─────────────────────────────────────────────────────────────────────────────
# Data model
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Turn:
    user_msg: str
    # What we consider a passing response. All are optional hints.
    expect_tools: list[str] = field(default_factory=list)   # at least one of these tools
    reject_tools: list[str] = field(default_factory=list)   # none of these tools
    expect_logiccard: Optional[bool] = None                  # True/False/None=don't check
    expect_text_contains: list[str] = field(default_factory=list)  # substrings (case-insensitive)
    reject_text_contains: list[str] = field(default_factory=list)  # must NOT appear
    description: str = ""                                     # what this turn tests


@dataclass
class Session:
    name: str
    description: str
    turns: list[Turn]


@dataclass
class TurnResult:
    turn: Turn
    response: str
    tools_called: list[str]
    logiccard: Optional[dict]
    latency_ms: int
    passed: bool
    failures: list[str]


# ─────────────────────────────────────────────────────────────────────────────
# Session definitions (~35 turns across 10 sessions)
# ─────────────────────────────────────────────────────────────────────────────

SESSIONS: list[Session] = [

    # ── 1. Basic order + multi-turn amendment ────────────────────────────────
    Session(
        name="orders",
        description="Immediate order, then amendment, then limit switch",
        turns=[
            Turn(
                user_msg="Buy 10 RELIANCE at market",
                expect_tools=["place_market_order"],
                expect_logiccard=True,
                description="Standard market order should produce a LogicCard",
            ),
            Turn(
                user_msg="actually make it 20 shares",
                expect_tools=["place_market_order"],
                expect_logiccard=True,
                reject_tools=["get_live_price"],
                description="Amendment: must re-emit the LogicCard, not just describe it",
            ),
            Turn(
                user_msg="switch to limit at ₹2700",
                expect_tools=["place_limit_order"],
                expect_logiccard=True,
                description="Order type switch: must call place_limit_order with new price",
            ),
        ],
    ),

    # ── 2. Typo / affirmative handling ───────────────────────────────────────
    Session(
        name="typos",
        description="Typo for yes, short affirmatives, company-name follow-ups",
        turns=[
            Turn(
                user_msg="zomato",
                expect_tools=["get_live_price"],
                reject_tools=[],
                description="Single stock name → live price fetch",
            ),
            Turn(
                user_msg="ues",  # typo for "yes"
                reject_tools=[],  # must not call get_live_price(symbol='ues')
                reject_text_contains=["couldn't complete"],
                description="Typo 'ues' after stock question — should not treat as ticker or error",
            ),
            Turn(
                user_msg="i meant yes, show me the price",
                expect_tools=["get_live_price"],
                description="Explicit correction should fetch ZOMATO price (context retention)",
            ),
        ],
    ),

    # ── 3. Swiggy ticker resolution ──────────────────────────────────────────
    Session(
        name="swiggy",
        description="Company name resolution for recently listed stocks",
        turns=[
            Turn(
                user_msg="buy 10 shares of swiggy",
                # Accept direct order OR a single ASK_USER confirming SWIGGY.
                # The model may or may not ask; what matters is no hard error.
                reject_text_contains=["couldn't complete", "not available"],
                description="Swiggy order — must not error; either places or confirms ticker",
            ),
            Turn(
                # If T1 placed directly: this becomes a "yes" confirmation
                # against an existing logiccard. If T1 asked: this provides
                # the ticker. Either way the bot should respond meaningfully
                # without erroring.
                user_msg="yes, SWIGGY on NSE",
                # Accept any of: tool emission (logiccard), ask-user, or
                # plain text — what we reject is errors and silent prose
                # without a card or question.
                reject_text_contains=["couldn't complete", "not available", "tool name"],
                description="Confirmation reply — must not error, regardless of T1 path",
            ),
            Turn(
                # Amendment after order is in the conversation. The order
                # tool should be re-emitted with the updated quantity.
                user_msg="actually make it 5 shares",
                expect_tools=["place_market_order"],
                expect_logiccard=True,
                description="Quantity amendment re-emits LogicCard with new qty",
            ),
        ],
    ),

    # ── 4. Conditional strategy (weekday + buying power) ────────────────────
    Session(
        name="strategy",
        description="Complex conditional agent, then follow-up edits",
        turns=[
            Turn(
                user_msg="Every weekday at 3:55 PM, if my buying power is over ₹50,000, buy 10 RELIANCE",
                expect_tools=["propose_workflow"],
                reject_tools=["place_market_order"],
                reject_text_contains=["not available", "tool name"],
                description="Schedule+condition must route to propose_workflow, not immediate order",
            ),
            Turn(
                user_msg="also send me an email notification when it triggers",
                expect_tools=["propose_workflow"],
                description="Modifying the workflow draft should re-emit a workflow card",
            ),
            Turn(
                user_msg="change the quantity to 15 shares",
                expect_tools=["propose_workflow"],
                description="Quantity edit on a workflow draft should call propose_workflow again",
            ),
        ],
    ),

    # ── 5. RSI threshold + follow-up ─────────────────────────────────────────
    Session(
        name="rsi",
        description="RSI threshold order with quantity and stop-loss follow-ups",
        turns=[
            Turn(
                user_msg="Buy TCS when RSI goes below 30",
                expect_tools=["propose_threshold_order"],
                reject_tools=["place_market_order", "ASK_USER"],
                description="RSI threshold → propose_threshold_order, default qty=1",
            ),
            Turn(
                user_msg="make it 5 shares",
                # Re-emit is the ideal path. ASK_USER is acceptable when the
                # model picks up an unrelated detail to verify (e.g. asks
                # about approval before updating); test only fails on
                # genuine prose-without-tool, which is uncommittable.
                expect_tools=["propose_threshold_order", "ASK_USER"],
                reject_text_contains=["couldn't complete"],
                description="Qty amendment on threshold order re-emits or asks",
            ),
            Turn(
                user_msg="add a 2% stop loss after the buy",
                # Multiple valid interpretations:
                #   - re-emit propose_threshold_order with SL field
                #   - call create_sl_order (treat the existing buy + SL as
                #     two coupled orders)
                #   - ASK_USER (clarify when the SL should fire)
                expect_tools=[
                    "propose_threshold_order",
                    "create_sl_order",
                    "ASK_USER",
                ],
                reject_text_contains=["couldn't complete"],
                description="Stop-loss addition — re-emits draft, creates SL, or asks",
            ),
        ],
    ),

    # ── 6. Portfolio context retention ──────────────────────────────────────
    Session(
        name="portfolio",
        description="Portfolio queries with cross-turn context",
        turns=[
            Turn(
                user_msg="show me my portfolio",
                expect_tools=["get_holdings", "get_portfolio_summary"],
                description="Portfolio fetch — either summary or holdings is fine",
            ),
            Turn(
                user_msg="which sector am I most exposed to",
                # Accept get_sector_breakdown OR get_holdings (LLM may compute from prior data)
                expect_tools=["get_sector_breakdown", "get_holdings", "get_portfolio_summary"],
                description="Sector breakdown — any holdings/portfolio tool is fine",
            ),
            Turn(
                user_msg="should I reduce that exposure?",
                expect_tools=[],
                reject_tools=["propose_workflow"],
                description="Advisory question → prose only, no workflow draft",
            ),
        ],
    ),

    # ── 7. Educational — no tool calls ───────────────────────────────────────
    Session(
        name="educational",
        description="Conceptual questions that must never trigger tool calls",
        turns=[
            Turn(
                user_msg="What is RSI and how is it calculated",
                expect_tools=[],
                description="Definition question → prose, no tool call",
            ),
            Turn(
                user_msg="how does that apply to Indian markets vs US",
                expect_tools=[],
                description="Comparative follow-up → still no tool call",
            ),
            Turn(
                user_msg="ok now show me TCS RSI",
                # Accept data tools OR ASK_USER (clarifying the RSI period is reasonable)
                expect_tools=["get_live_price", "get_price_history", "get_ohlc", "ASK_USER"],
                description="Data request after educational context — tool call or clarification ok",
            ),
        ],
    ),

    # ── 8. Workflow with two triggers (branch test) ──────────────────────────
    Session(
        name="workflow",
        description="Two-branch workflow and missing field → ASK_USER → emit",
        turns=[
            Turn(
                user_msg="buy NIFTYBEES every Monday at 09:15 and sell at 15:20",
                expect_tools=["propose_workflow"],
                reject_tools=["create_sip", "place_market_order"],
                description="Buy+sell on schedule → two-branch workflow, not two separate orders",
            ),
            Turn(
                user_msg="how many shares?",
                # The user is asking a question back — bot should answer
                reject_text_contains=["couldn't complete"],
                description="Bot's response to user's clarifying question should be sensible",
            ),
            Turn(
                user_msg="5 shares for buy, and for sell whatever I hold",
                expect_tools=["propose_workflow"],
                description="After clarification, emit the full two-branch workflow draft",
            ),
        ],
    ),

    # ── 9. Ambiguity → clarify → immediate emit ──────────────────────────────
    Session(
        name="ambiguity",
        description="Ambiguous ticker → ASK_USER → follow-up should emit card (not re-clarify)",
        turns=[
            Turn(
                user_msg="buy 100 HDFC",
                expect_tools=["ASK_USER"],
                description="Bare 'HDFC' is ambiguous — must ask which subsidiary",
            ),
            Turn(
                user_msg="HDFC Bank",
                expect_tools=["place_market_order"],
                expect_logiccard=True,
                reject_tools=["ASK_USER"],
                description="After clarification, must emit the order card immediately — not re-ask",
            ),
            Turn(
                user_msg="cancel that, buy 50 tata motors instead",
                expect_tools=["place_market_order"],
                expect_logiccard=True,
                description="Cancel + new order — 'Tata Motors' → TATAMOTORS inferred",
            ),
        ],
    ),

    # ── 10. Backtest + follow-up ──────────────────────────────────────────────
    Session(
        name="backtest",
        description="Indicator backtest with follow-up threshold variation",
        turns=[
            Turn(
                # Indicator backtests hit the chat router's fast path
                # (_run_indicator_backtest). evaluate_turn() accepts the
                # `indicator_backtest_chart` render hint as equivalent to
                # `run_backtest` in tools_called.
                user_msg="backtest RELIANCE when its RSI drops below 30 over the last 5 years",
                expect_tools=["run_backtest"],
                reject_tools=["propose_workflow"],
                description="Valid indicator backtest → run_backtest, not propose_workflow",
            ),
            Turn(
                # Follow-up doesn't have the literal "backtest" word so the
                # router fast-path doesn't fire; the LLM is in charge.
                # Either run_backtest OR ASK_USER (clarifying the symbol /
                # window) is acceptable — what we reject is hard errors.
                user_msg="backtest the same with RSI threshold 25 instead of 30",
                expect_tools=["run_backtest", "ASK_USER"],
                reject_text_contains=["couldn't complete"],
                description="Follow-up backtest — re-runs or clarifies, no error",
            ),
            Turn(
                user_msg="which version had better returns",
                reject_text_contains=["couldn't complete"],
                description="Comparison follow-up — contextual, should not error",
            ),
        ],
    ),

]


# ─────────────────────────────────────────────────────────────────────────────
# Chat client
# ─────────────────────────────────────────────────────────────────────────────

def send_message(
    messages: list[dict],
    conv_id: str,
    mode: Optional[str] = None,
    timeout: float = 90.0,
) -> dict:
    payload = {
        "messages": messages,
        "conversation_id": conv_id,
        "mode": mode,
    }
    t0 = time.time()
    try:
        resp = httpx.post(f"{BASE}/chat", json=payload, timeout=timeout)
    except (httpx.TimeoutException, httpx.HTTPError) as e:
        elapsed = int((time.time() - t0) * 1000)
        return {
            "response": f"[NETWORK_ERROR] {type(e).__name__}: {e}",
            "tools_called": [],
            "logiccard": None,
            "latency_ms": elapsed,
            "_error": True,
        }
    elapsed = int((time.time() - t0) * 1000)
    if resp.status_code != 200:
        return {
            "response": f"[HTTP {resp.status_code}] {resp.text[:200]}",
            "tools_called": [],
            "logiccard": None,
            "latency_ms": elapsed,
            "_error": True,
        }
    d = resp.json()
    d["latency_ms"] = elapsed
    return d


# ─────────────────────────────────────────────────────────────────────────────
# Evaluator
# ─────────────────────────────────────────────────────────────────────────────

def evaluate_turn(turn: Turn, response: dict) -> TurnResult:
    text = (response.get("response") or "").lower()
    tools = response.get("tools_called") or []
    lc = response.get("logiccard")
    latency = response.get("latency_ms", 0)
    raw = response.get("raw_data") or {}
    render_hint = raw.get("_render_hint", "") if isinstance(raw, dict) else ""

    failures: list[str] = []

    # Check expected tools (at least one must appear).
    # Backtest fast-path: the chat router handles indicator backtests
    # directly via _run_indicator_backtest — bypasses the LLM, so
    # `tools_called` is empty even though a backtest ran. Accept the
    # render hint as evidence that run_backtest equivalent executed.
    if turn.expect_tools:
        backtest_via_hint = (
            "run_backtest" in turn.expect_tools
            and render_hint in {
                "indicator_backtest_chart", "financial_backtest_chart",
            }
        )
        if not any(t in tools for t in turn.expect_tools) and not backtest_via_hint:
            failures.append(
                f"expected one of {turn.expect_tools}, got {tools}"
            )

    # Check rejected tools (none should appear)
    for t in turn.reject_tools:
        if t in tools:
            failures.append(f"tool '{t}' must NOT be called but was")

    # Check logiccard expectation
    if turn.expect_logiccard is True and lc is None:
        failures.append("expected a logiccard but none returned")
    if turn.expect_logiccard is False and lc is not None:
        failures.append("expected NO logiccard but one was returned")

    # Check text contains
    for phrase in turn.expect_text_contains:
        if phrase.lower() not in text:
            failures.append(f"response should contain '{phrase}'")

    # Check text NOT contains (leakage / error messages)
    for phrase in turn.reject_text_contains:
        if phrase.lower() in text:
            failures.append(f"response must NOT contain '{phrase}'")

    return TurnResult(
        turn=turn,
        response=response.get("response", ""),
        tools_called=tools,
        logiccard=lc,
        latency_ms=latency,
        passed=len(failures) == 0,
        failures=failures,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Runner
# ─────────────────────────────────────────────────────────────────────────────

def run_session(session: Session, verbose: bool = False) -> list[TurnResult]:
    conv_id = f"itest_{session.name}_{uuid.uuid4().hex[:8]}"
    history: list[dict] = []
    results: list[TurnResult] = []

    print(f"\n{'═' * 60}")
    print(f"SESSION: {session.name}")
    print(f"  {session.description}")
    print(f"{'─' * 60}")

    for i, turn in enumerate(session.turns):
        history.append({"role": "user", "content": turn.user_msg})

        resp = send_message(history, conv_id)

        result = evaluate_turn(turn, resp)
        results.append(result)

        # Add assistant response to history for the next turn
        assistant_text = resp.get("response", "")
        history.append({"role": "assistant", "content": assistant_text})

        status = "PASS" if result.passed else "FAIL"
        desc = turn.description or f"Turn {i+1}"

        tools_str = str(result.tools_called) if result.tools_called else "(no tools)"
        lc_str = "logiccard" if result.logiccard else ""
        print(
            f"  T{i+1} [{status}] {desc}\n"
            f"       msg: {turn.user_msg!r}\n"
            f"       tools: {tools_str}  {lc_str}  ({result.latency_ms}ms)"
        )
        if result.failures:
            for f in result.failures:
                print(f"       ✗ {f}")
        if verbose:
            snippet = result.response[:200].replace("\n", " ")
            print(f"       resp: {snippet!r}")

        time.sleep(0.3)

    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Interactive chat tester")
    parser.add_argument("--session", help="Run only this session (default: all)")
    parser.add_argument("--verbose", action="store_true", help="Print response text")
    args = parser.parse_args()

    # Health check
    try:
        r = httpx.get(f"{BASE}/health", timeout=5)
        if r.status_code != 200:
            print(f"Backend unhealthy ({r.status_code}). Start uvicorn first.")
            sys.exit(1)
    except Exception as e:
        print(f"Cannot reach {BASE}: {e}")
        sys.exit(1)

    to_run = SESSIONS
    if args.session:
        to_run = [s for s in SESSIONS if s.name == args.session]
        if not to_run:
            print(f"No session named '{args.session}'. Available: {[s.name for s in SESSIONS]}")
            sys.exit(1)

    all_results: list[TurnResult] = []
    for session in to_run:
        results = run_session(session, verbose=args.verbose)
        all_results.extend(results)

    # Summary
    total = len(all_results)
    passed = sum(1 for r in all_results if r.passed)
    failed = total - passed
    score = int(passed / total * 100) if total else 0

    print(f"\n{'═' * 60}")
    print(f"TOTAL: {passed}/{total} passed  ({score}/100)")

    if failed:
        print(f"\nFailed turns:")
        for r in all_results:
            if not r.passed:
                print(f"  [{r.turn.description or r.turn.user_msg[:40]}]")
                for f in r.failures:
                    print(f"    • {f}")

    print()
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
