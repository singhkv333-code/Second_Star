"""Broad chat-surface eval — 50 prompts through POST /chat.

Categories (10 each):
  A simple / read-side facts          (price, chart, market hours, ...)
  B backtest                          (single + compound conditions)
  C workflow / agent / automation     (notify / buy on trigger)
  D portfolio analysis / read-side   (P&L, overexposure, correlation, ...)
  E ambiguous / edge / unusual        (one-word, mixed-language, ill-formed)

For each prompt we record:
  - HTTP status
  - tools_called   (list of tool names the chat layer picked)
  - card kind      (_render_hint from raw_data)
  - tokens         (sum across LLM hops, from latency_breakdown if available)
  - chat-side latency (total wall-clock from /chat)
  - response excerpt (first 240 chars)
  - error message if any

Output:
  /tmp/chat_surface_eval.json    (full per-prompt data)
  stdout markdown summary
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
except ImportError:
    pass


BASE = "http://localhost:8000"


# ── Prompt catalogue ───────────────────────────────────────────────


@dataclass
class Prompt:
    code: str
    text: str
    expected_card: Optional[str] = None   # hint, not assertion
    notes: str = ""


PROMPTS: list[Prompt] = [
    # ── A. Simple / read-side ──────────────────────────────────────
    Prompt("A01", "What is the current price of TCS?", expected_card=None,
           notes="single live quote"),
    Prompt("A02", "Show me the 1-year chart of INFY"),
    Prompt("A03", "What is the 52-week high of RELIANCE?"),
    Prompt("A04", "Is the market open right now?"),
    Prompt("A05", "What's my portfolio worth right now?"),
    Prompt("A06", "List my current holdings"),
    Prompt("A07", "What is my total P&L for today?"),
    Prompt("A08", "Compare returns of HDFCBANK vs ICICIBANK over the last 3 years"),
    Prompt("A09", "What is the current RSI(14) of NIFTYBEES?"),
    Prompt("A10", "What is the next scheduled job?"),

    # ── B. Backtests ───────────────────────────────────────────────
    Prompt("B01", "Backtest: buy TCS when 14-day RSI drops below 30, hold 10 days",
           expected_card="indicator_backtest_chart",
           notes="single-condition; either backtester is fine"),
    Prompt("B02", "Simulate INFY buy on MACD crossing above its signal line, with a 5% stop loss",
           expected_card="indicator_backtest_chart",
           notes="indicator-vs-indicator crossing → dsl_tree preferred"),
    Prompt("B03", "How would buying NIFTYBEES on lower Bollinger band touches have performed over the last 3 years?",
           expected_card="indicator_backtest_chart",
           notes="multi-output BB component → dsl_tree preferred"),
    Prompt("B04", "Backtest: buy HDFCBANK when its 14-day RSI is below 30 AND price is above 200-day SMA",
           expected_card="indicator_backtest_chart",
           notes="compound AND → dsl_tree preferred"),
    Prompt("B05", "Show me the result of buying ICICIBANK when its 14-day ATR is in the top 30% of the last 252 days",
           expected_card="indicator_backtest_chart",
           notes="percentrank aggregator → dsl_tree"),
    Prompt("B06", "Test a 20-bar high breakout strategy on RELIANCE",
           expected_card="indicator_backtest_chart",
           notes="aggregator highest → dsl_tree"),
    Prompt("B07", "Backtest a 20-day z-score mean-reversion entry on RELIANCE (buy when z-score below -1.5)",
           expected_card="indicator_backtest_chart",
           notes="zscore aggregator → dsl_tree"),
    Prompt("B08", "If I had bought TCS every time its RSI(14) crossed below 25 since Jan 2023, how would it look?",
           expected_card="indicator_backtest_chart"),
    Prompt("B09", "Simulate: buy INFY when its MACD line crosses above the signal line, hold 15 days",
           expected_card="indicator_backtest_chart",
           notes="multi-output MACD components → dsl_tree"),
    Prompt("B10", "Backtest: buy TCS when its RSI(14) is lower than INFY's RSI(14)",
           expected_card="indicator_backtest_chart",
           notes="cross-symbol comparison → dsl_tree"),

    # ── C. Workflow / agent / automation ───────────────────────────
    Prompt("C01", "Create an automation: notify me when TCS 14-day RSI drops below 30",
           expected_card="workflow_draft_card"),
    Prompt("C02", "Set up a workflow that buys NIFTYBEES every Monday at 9:30 AM",
           expected_card="workflow_draft_card",
           notes="recurring schedule → propose_scheduled_order"),
    Prompt("C03", "Build me an agent that watches HDFCBANK and pushes me a notification on MACD signal cross",
           expected_card="workflow_draft_card",
           notes="compound → propose_dsl_workflow"),
    Prompt("C04", "Watch ICICIBANK for a 20-day high breakout and tell me when it happens",
           expected_card="workflow_draft_card",
           notes="aggregator → propose_dsl_workflow"),
    Prompt("C05", "Automate: buy 5 shares of INFY whenever its RSI is below 25 AND volume is above 5 million",
           expected_card="workflow_draft_card",
           notes="compound + order → propose_dsl_workflow"),
    Prompt("C06", "Make an agent that alerts me if NIFTY drops more than 2% intraday",
           expected_card="workflow_draft_card",
           notes="ambiguous — could be threshold_order"),
    Prompt("C07", "Set up a 5% trailing stop loss on my RELIANCE holding",
           expected_card="workflow_draft_card",
           notes="propose_holding_action / threshold_order"),
    Prompt("C08", "Create a strategy: buy TCS when 50-day SMA crosses above 200-day SMA",
           expected_card="workflow_draft_card",
           notes="indicator-vs-indicator → propose_dsl_workflow"),
    Prompt("C09", "Build an automation: when 50-day correlation of TCS and INFY drops below 0.3, buy 10 shares of TCS",
           expected_card="workflow_draft_card",
           notes="correlation aggregator → propose_dsl_workflow"),
    Prompt("C10", "I want to be notified when the 14-day ATR of NIFTYBEES enters the top decile of last 252 days",
           expected_card="workflow_draft_card",
           notes="percentrank → propose_dsl_workflow"),

    # ── D. Portfolio analysis / read-side ──────────────────────────
    Prompt("D01", "How is my portfolio performing this month?"),
    Prompt("D02", "Which of my holdings has the highest drawdown right now?"),
    Prompt("D03", "What is my total realized P&L on TCS so far?"),
    Prompt("D04", "Show me the correlation matrix of my top 5 holdings"),
    Prompt("D05", "Am I overexposed to financial-sector stocks?"),
    Prompt("D06", "What is the best-performing stock in my portfolio over the last 6 months?"),
    Prompt("D07", "Compare my portfolio return against NIFTY over the last 1 year"),
    Prompt("D08", "List my pending orders"),
    Prompt("D09", "Did any of my SIPs run today?"),
    Prompt("D10", "What is the overall risk metric of my portfolio?"),

    # ── E. Ambiguous / edge / unusual ──────────────────────────────
    Prompt("E01", "Buy something",
           notes="extremely ambiguous, should ask for clarification"),
    Prompt("E02", "tcs maybe?",
           notes="ambiguous short prompt"),
    Prompt("E03", "should i sell?",
           notes="no context; expect clarification"),
    Prompt("E04", "Make me rich",
           notes="impossible; assistant should redirect"),
    Prompt("E05", "Backtest the magic formula",
           notes="references a named strategy not in our grammar"),
    Prompt("E06", "Test the Joel Greenblatt strategy on NIFTY",
           notes="named external strategy"),
    Prompt("E07", "What if I had invested in 2020?",
           notes="needs a stock + amount + date — should clarify"),
    Prompt("E08", "क्या आज मार्केट खुला है?",
           notes="Hindi: is the market open today?"),
    Prompt("E09", "RSI > 70 sell",
           notes="terse; symbol missing"),
    Prompt("E10", "Help",
           notes="meta-question"),
]


# ── HTTP / auth helpers ────────────────────────────────────────────


def register_user() -> str:
    body = {
        "email": f"chat_eval_{int(time.time())}_{os.getpid()}@pivot.com",
        "password": "password123",
        "full_name": "Chat Surface Eval",
    }
    req = urllib.request.Request(
        f"{BASE}/auth/register", method="POST",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())["access_token"]


def chat_call(*, token: str, prompt_text: str, conv_id: str) -> tuple[int, dict, float]:
    body = {
        "messages": [{"role": "user", "content": prompt_text}],
        "include_portfolio_context": True,
        "conversation_id": conv_id,
    }
    req = urllib.request.Request(
        f"{BASE}/chat", method="POST",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
    )
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            payload = json.loads(resp.read())
            return resp.status, payload, (time.time() - t0) * 1000.0
    except urllib.error.HTTPError as e:
        try:
            payload = json.loads(e.read())
        except Exception:  # noqa: BLE001
            payload = {"raw": "<unreadable error>"}
        return e.code, payload, (time.time() - t0) * 1000.0


# ── Result ─────────────────────────────────────────────────────────


@dataclass
class PromptResult:
    code: str
    text: str
    expected_card: Optional[str]
    http_status: Optional[int] = None
    tools_called: list[str] = field(default_factory=list)
    card_kind: Optional[str] = None
    response_excerpt: Optional[str] = None
    latency_ms: Optional[float] = None
    latency_breakdown: dict = field(default_factory=dict)
    error: Optional[str] = None


# ── Runner ─────────────────────────────────────────────────────────


def _tools_called_names(payload: dict) -> list[str]:
    tc = payload.get("tools_called") or []
    out: list[str] = []
    for entry in tc:
        if isinstance(entry, dict):
            n = entry.get("name") or entry.get("tool_name") or entry.get("tool")
            if n:
                out.append(n)
        elif isinstance(entry, str):
            out.append(entry)
    return out


def _excerpt(payload: dict) -> str:
    txt = (payload.get("response") or "")
    return (txt[:240] + "…") if len(txt) > 240 else txt


def run_prompt(*, token: str, prompt: Prompt, conv_id: str) -> PromptResult:
    print(f"[{prompt.code}] {prompt.text[:80]}", flush=True)
    status, payload, ms = chat_call(
        token=token, prompt_text=prompt.text, conv_id=conv_id,
    )
    raw_data = payload.get("raw_data") or {}
    card_kind = raw_data.get("_render_hint") if isinstance(raw_data, dict) else None
    err = None
    if status != 200:
        err = str(payload)[:240]
    return PromptResult(
        code=prompt.code,
        text=prompt.text,
        expected_card=prompt.expected_card,
        http_status=status,
        tools_called=_tools_called_names(payload),
        card_kind=card_kind,
        response_excerpt=_excerpt(payload),
        latency_ms=float(payload.get("latency_ms") or ms),
        latency_breakdown=payload.get("latency_breakdown") or {},
        error=err,
    )


def main() -> None:
    token = register_user()
    print(f"registered token (len={len(token)})", flush=True)

    results: list[PromptResult] = []
    base_ts = int(time.time())
    for i, p in enumerate(PROMPTS):
        # Fresh conversation id per prompt so context doesn't leak.
        conv_id = f"eval_{base_ts}_{i:02d}_{p.code}"
        try:
            r = run_prompt(token=token, prompt=p, conv_id=conv_id)
        except Exception as exc:  # noqa: BLE001
            r = PromptResult(
                code=p.code, text=p.text,
                expected_card=p.expected_card,
                error=f"client crash: {type(exc).__name__}: {exc}",
            )
        results.append(r)

    # Persist
    out_path = "/tmp/chat_surface_eval.json"
    with open(out_path, "w") as fh:
        json.dump([asdict(r) for r in results], fh, indent=2, default=str)
    print(f"\nWrote {out_path}", flush=True)

    # Summary
    total = len(results)
    http_ok = sum(1 for r in results if r.http_status == 200)
    with_tools = sum(1 for r in results if r.tools_called)
    with_card = sum(1 for r in results if r.card_kind)
    avg_ms = sum((r.latency_ms or 0) for r in results) / max(total, 1)

    print(f"\n## Headlines")
    print(f"- total: {total}")
    print(f"- HTTP 200: {http_ok}/{total}")
    print(f"- ≥1 tool called: {with_tools}/{total}")
    print(f"- emitted a card: {with_card}/{total}")
    print(f"- avg chat latency: {avg_ms:.0f} ms")


if __name__ == "__main__":
    main()
