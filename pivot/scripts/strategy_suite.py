"""Strategy-class suite — composition / mean-reversion / pairs /
rotation / regime-gating prompts. Tests that the model EITHER
correctly drafts the workflow OR honestly names the gap (no silent
approximation, no fabricated primitives).

Run:
    cd /Users/karanveersingh/Downloads/Second_Star/pivot
    .venv/bin/python scripts/strategy_suite.py
"""
from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Callable, Optional

import httpx

BASE = "http://127.0.0.1:8000"
TRACE_PATH = "/tmp/llm_trace.jsonl"


@dataclass
class TestCase:
    tid: str
    name: str
    probe: str
    setup: list[str] = field(default_factory=list)
    check: Optional[Callable] = None  # (d, history) → (passed, notes)


@dataclass
class Result:
    tid: str
    name: str
    passed: bool
    tools: list[str]
    workflow_steps: list[str]
    response_chars: int
    response_head: str
    latency_ms: int
    in_tok: int
    cached_tok: int
    out_tok: int
    notes: str


def chat(messages, cid):
    t0 = time.time()
    try:
        r = httpx.post(f"{BASE}/chat",
                       json={"messages": messages, "conversation_id": cid, "mode": None},
                       timeout=90)
        d = r.json() if r.status_code == 200 else {"_err": r.status_code}
    except Exception as e:
        d = {"_err": type(e).__name__}
    return d, int((time.time() - t0) * 1000)


def read_last_trace():
    try:
        with open(TRACE_PATH) as f:
            lines = [json.loads(l) for l in f if l.strip()]
        if lines:
            u = lines[-1].get("usage") or {}
            return (u.get("input_tokens", 0),
                    u.get("cached_tokens", 0),
                    u.get("output_tokens", 0))
    except Exception:
        pass
    return 0, 0, 0


def steps_of(d):
    raw = d.get("raw_data") or {}
    wf = raw.get("propose_workflow") or {}
    return [s.get("step_type", "?") for s in (wf.get("steps") or [])]


def has_tool(d, *names):
    return any(n in (d.get("tools_called") or []) for n in names)


def text_has(d, *substrs):
    t = (d.get("response") or "").lower()
    return any(s.lower() in t for s in substrs)


# Pass criteria per test:
#   - SUPPORTED strategies: model MUST draft a workflow with sensible steps.
#   - UNSUPPORTED strategies: model MUST name the specific gap (Bollinger /
#     volume / spread / Sharpe-rank / VIX) and offer a closest-fit, NOT
#     silently approximate.

TESTS: list[TestCase] = [
    # ── SUPPORTED — should draft ────────────────────────────────
    TestCase("S01", "Multi-indicator buy entry",
        probe=("Build me an agent that buys NIFTYBEES when RSI(14)<30 AND "
               "MACD line crosses above its signal line."),
        check=lambda d, h: (
            has_tool(d, "propose_workflow")
            and len(steps_of(d)) >= 5
            and any("indicator" in s for s in steps_of(d))
            and any("condition.numeric" == s for s in steps_of(d))
            and any("action.place_order" == s for s in steps_of(d)),
            f"steps={steps_of(d)}",
        )),
    TestCase("S02", "Multi-indicator with exit",
        probe=("Buy TCS when RSI(14)<30 AND price closes above 50-EMA. "
               "Sell when RSI>70."),
        check=lambda d, h: (
            has_tool(d, "propose_workflow")
            and len(steps_of(d)) >= 6
            and steps_of(d).count("trigger.schedule") + steps_of(d).count("trigger.indicator") >= 2,
            f"steps={steps_of(d)}",
        )),
    TestCase("S03", "Indicator-vs-indicator crossover",
        probe=("Buy 10 INFY when its 50-day EMA crosses above its 200-day EMA, "
               "sell when it crosses back below."),
        check=lambda d, h: (
            has_tool(d, "propose_workflow")
            and steps_of(d).count("fetch.indicator") >= 2,
            f"steps={steps_of(d)}",
        )),
    TestCase("S04", "Schedule + portfolio guard",
        probe=("Every Monday at 9:15 IST, if my buying power is over ₹50,000, "
               "buy 5 NIFTYBEES at market."),
        check=lambda d, h: (
            has_tool(d, "propose_workflow")
            and "fetch.portfolio" in steps_of(d)
            and "condition.numeric" in steps_of(d),
            f"steps={steps_of(d)}",
        )),
    TestCase("S05", "Two-branch buy then sell same day",
        probe=("Buy 1 RELIANCE every weekday at the open and sell the entire "
               "RELIANCE holding at the same day's close."),
        check=lambda d, h: (
            has_tool(d, "propose_workflow")
            and steps_of(d).count("trigger.market_relative_time") + steps_of(d).count("trigger.schedule") >= 2,
            f"steps={steps_of(d)}",
        )),
    TestCase("S06", "Sector basket allocation",
        probe="Allocate ₹50,000 equally across the top 5 banking stocks every Monday at 9:20.",
        check=lambda d, h: (
            has_tool(d, "propose_basket_allocation"),
            f"tools={d.get('tools_called')}",
        )),
    TestCase("S07", "Holding-action sell on indicator",
        probe="Sell my entire INFY position when its RSI(14) goes above 70.",
        check=lambda d, h: (
            has_tool(d, "propose_holding_action", "propose_workflow"),
            f"tools={d.get('tools_called')}",
        )),

    # ── UNSUPPORTED — must NAME the gap, not silently fake it ───
    TestCase("S08", "Bollinger mean reversion (gap)",
        probe=("Buy RELIANCE when its price drops below the lower Bollinger "
               "Band on the daily chart."),
        check=lambda d, h: (
            text_has(d, "bollinger", "isn't wired", "not wired",
                     "approximation", "rsi", "sma", "closest")
            or has_tool(d, "ASK_USER", "propose_workflow"),
            f"resp={(d.get('response') or '')[:240]!r}",
        )),
    TestCase("S09", "Pairs trade (gap)",
        probe=("Build a pairs trade between TCS and INFY: buy the spread when "
               "it's more than 2 standard deviations from its 60-day mean."),
        check=lambda d, h: (
            text_has(d, "pair", "spread", "doesn't have", "isn't wired",
                     "no spread", "two separate", "long leg", "closest fit"),
            f"resp={(d.get('response') or '')[:240]!r}",
        )),
    TestCase("S10", "Sharpe-rank rotation (gap)",
        probe=("Every Monday at 9:30, rebalance my portfolio to hold the top 3 "
               "by Sharpe ratio from RELIANCE, TCS, INFY, HDFCBANK, WIPRO."),
        check=lambda d, h: (
            text_has(d, "sharpe", "rank", "screener", "mcap", "market cap",
                     "rotation", "isn't wired", "fixed list", "named")
            or has_tool(d, "ASK_USER"),
            f"resp={(d.get('response') or '')[:240]!r}",
        )),
    TestCase("S11", "Volume confirmation (gap)",
        probe=("Buy TCS when RSI(14) is below 30 AND today's volume is more "
               "than 2x the 20-day average volume."),
        check=lambda d, h: (
            text_has(d, "volume", "isn't wired", "not wired", "rsi alone",
                     "drop the volume", "closest fit")
            or has_tool(d, "ASK_USER"),
            f"resp={(d.get('response') or '')[:240]!r}",
        )),
    TestCase("S12", "VIX regime gate (gap)",
        probe=("Buy NIFTYBEES every Monday at 09:15 only when India VIX is "
               "below 15."),
        check=lambda d, h: (
            text_has(d, "vix", "regime", "isn't wired", "not wired",
                     "drop the regime", "closest fit", "nifty-relative")
            or has_tool(d, "ASK_USER"),
            f"resp={(d.get('response') or '')[:240]!r}",
        )),
    TestCase("S13", "Z-score mean reversion (gap)",
        probe=("Buy TCS when its 20-day z-score is below -2."),
        check=lambda d, h: (
            text_has(d, "z-score", "z score", "std", "isn't wired",
                     "closest", "fixed %", "approximation", "sma")
            or has_tool(d, "ASK_USER"),
            f"resp={(d.get('response') or '')[:240]!r}",
        )),

    # ── COMPOSITE — uses both supported and unsupported ─────────
    TestCase("S14", "Composite: multi-cond entry + reverse-cross exit",
        probe=("Buy 10 RELIANCE when its 50-EMA crosses above the 200-EMA, "
               "sell the entire holding when 50-EMA crosses back below."),
        check=lambda d, h: (
            has_tool(d, "propose_workflow")
            and steps_of(d).count("fetch.indicator") >= 2
            and "action.place_order" in steps_of(d),
            f"steps={steps_of(d)}",
        )),
    TestCase("S15", "Composite: scheduled buy + RSI-conditioned sell",
        probe=("Every weekday at 9:30 buy 1 share of HDFCBANK, and at 14:30 "
               "the same day sell the position only if its RSI(14) is above 70."),
        check=lambda d, h: (
            has_tool(d, "propose_workflow")
            and len(steps_of(d)) >= 5
            and "fetch.indicator" in steps_of(d),
            f"steps={steps_of(d)}",
        )),
]


def run_test(t: TestCase) -> Result:
    cid = f"strat_{t.tid}_{uuid.uuid4().hex[:6]}"
    history = []
    for s in t.setup:
        history.append({"role": "user", "content": s})
        d, _ = chat(history, cid)
        history.append({"role": "assistant", "content": d.get("response", "")})
    history.append({"role": "user", "content": t.probe})
    d, ms = chat(history, cid)
    in_tok, cached_tok, out_tok = read_last_trace()
    notes = ""
    passed = False
    if t.check:
        try:
            passed, notes = t.check(d, history)
        except Exception as e:
            notes = f"check_raised: {e}"
    return Result(
        tid=t.tid, name=t.name, passed=passed,
        tools=d.get("tools_called") or [],
        workflow_steps=steps_of(d),
        response_chars=len(d.get("response") or ""),
        response_head=(d.get("response") or "")[:240],
        latency_ms=ms, in_tok=in_tok, cached_tok=cached_tok, out_tok=out_tok,
        notes=notes,
    )


def main():
    only = os.environ.get("STRAT_FILTER", "").strip()
    selected = [t for t in TESTS if not only or t.tid in only.split(",")]
    print(f"Running {len(selected)} strategy prompts...\n")
    open(TRACE_PATH, "w").close()
    results = []
    for t in selected:
        r = run_test(t)
        results.append(r)
        mark = "PASS" if r.passed else "FAIL"
        steps_s = " | ".join(r.workflow_steps)[:60]
        tools_s = ",".join(r.tools)[:40]
        print(f"[{mark}] {r.tid:<4} {r.name[:40]:<40} tools={tools_s:<40} "
              f"in={r.in_tok:<6} ms={r.latency_ms:<5}")
        if r.workflow_steps:
            print(f"      steps: {steps_s}")
        if not r.passed:
            print(f"      head:  {r.response_head[:200]!r}")
            print(f"      notes: {r.notes}")
    # Aggregate
    n = len(results)
    p = sum(1 for r in results if r.passed)
    print()
    print(f"OVERALL: {p}/{n} ({p/n*100:.0f}%)")
    avg_in = int(sum(r.in_tok for r in results) / n) if n else 0
    avg_cached = int(sum(r.cached_tok for r in results) / n) if n else 0
    avg_out = int(sum(r.out_tok for r in results) / n) if n else 0
    avg_ms = int(sum(r.latency_ms for r in results) / n) if n else 0
    pct = (avg_cached / avg_in * 100) if avg_in else 0
    print(f"AVG  in={avg_in} cached={pct:.0f}% out={avg_out} ms={avg_ms}")
    with open("/tmp/strategy_suite_results.json", "w") as f:
        json.dump([asdict(r) for r in results], f, indent=2, default=str)
    print(f"\nDetails: /tmp/strategy_suite_results.json")


if __name__ == "__main__":
    main()
