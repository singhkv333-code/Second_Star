"""Trade-automation quality grader.

Runs a curated set of 15 prompts through ``POST /chat`` and scores
each turn by a deterministic rubric. Used to validate that the chat
surface — its tool routing, fast-path, completeness gate, and
propose_workflow tool — actually delivers a usable automation
experience without over-asking, mis-routing, or wasting LLM hops.

The same prompt can score 0 or 10 on different runs because gpt-5-mini
at low reasoning emits a clean draft only ~70% of the time on hop 1.
``--samples N`` runs each prompt N times and aggregates with the
median, which is robust to that single-shot variance.

Prompts span four buckets:

  - **clear**       The user gave full info; assistant should act
                    immediately (one tool, no clarification).
  - **vague**       Some required info missing; assistant should
                    take a sensible default OR ask exactly one
                    focused question — never two.
  - **agent**       Multi-step automation request; assistant should
                    call propose_workflow and emit a draft.
  - **off_topic**   Off-domain or non-actionable; assistant should
                    answer briefly without calling tools.

Scoring (each prompt /10):

  +5  expected tool fired (or fast-path matched, when expected)
  +3  ask count <= max_acceptable_asks
  +2  no error / no fallback
  -3  wrong tool called
  -3  asked more than max_acceptable_asks
  -2  finished in > 4 LLM hops
  -2  response is the LLM-unavailable fallback string

Plus a per-bucket aggregate so we can see if (e.g.) all the failures
are concentrated in vague-order handling or in agent generation.

Usage::

    PIVOT_LLM_TRACE=/tmp/pivot_llm_trace.jsonl uvicorn backend.main:app --reload --port 8000
    python -m scripts.grade_automation_quality --label baseline
    python -m scripts.grade_automation_quality --label after_fix --diff baseline
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


# ── Prompts + expected outcomes ─────────────────────────────────────


@dataclass(frozen=True)
class Prompt:
    name: str
    bucket: str
    text: str
    # Acceptable tool names — the run is "expected tool fired" if
    # ANY of these appears in tools_called. Empty set means "no tool
    # should be called".
    expected_tools: frozenset[str]
    # 0 means must not ASK_USER. 1 means at most one ask (vague case).
    max_acceptable_asks: int = 0
    # Set for prompts that should hit the deterministic shortcut.
    expected_intent: Optional[str] = None
    # If provided, the response must include one of these substrings
    # (lowercased match). Used for off-topic prompts where there's no
    # tool but we still want a sensible reply.
    required_phrases: tuple[str, ...] = ()
    # Allowed render_hints for full credit on the "tool fired" axis.
    expected_render_hints: tuple[str, ...] = ()
    # Description of what a perfect answer looks like.
    notes: str = ""


PROMPTS: list[Prompt] = [
    # ── clear ────────────────────────────────────────────────────────
    Prompt(
        name="price_query",
        bucket="clear",
        text="What's the live price of RELIANCE?",
        expected_tools=frozenset({"get_live_price"}),
        max_acceptable_asks=0,
        notes="One tool, no asks. Symbol is given.",
    ),
    Prompt(
        name="portfolio_summary",
        bucket="clear",
        text="Show me my portfolio",
        expected_tools=frozenset({"get_portfolio_summary", "get_holdings"}),
        max_acceptable_asks=0,
    ),
    Prompt(
        name="market_order_complete",
        bucket="clear",
        text="Buy 10 RELIANCE at market",
        expected_tools=frozenset({"place_market_order"}),
        max_acceptable_asks=0,
        expected_render_hints=("logic_card",),
        notes="Symbol + qty + order type all present. Should produce LogicCard.",
    ),
    Prompt(
        name="indicator_backtest",
        bucket="clear",
        text="Backtest buying RELIANCE whenever its RSI drops below 30 over the last 3 years",
        expected_tools=frozenset(),  # routes pre-LLM
        max_acceptable_asks=0,
        expected_intent="INDICATOR_BACKTEST",
        expected_render_hints=("indicator_backtest_chart",),
        notes="Deterministic shortcut should fire.",
    ),
    Prompt(
        name="52wk_high",
        bucket="clear",
        text="What is the 52-week high of TCS?",
        expected_tools=frozenset({"get_52wk_range", "get_price_history"}),
        max_acceptable_asks=0,
    ),

    # ── agent (single + multi trigger) ──────────────────────────────
    Prompt(
        name="single_trigger_agent",
        bucket="agent",
        text=(
            "Build me an agent that buys 5 NIFTYBEES every weekday at "
            "09:15 IST. Automatic execution."
        ),
        expected_tools=frozenset({"propose_workflow"}),
        max_acceptable_asks=0,
        expected_render_hints=("workflow_draft_card",),
    ),
    Prompt(
        name="multi_trigger_agent",
        bucket="agent",
        text=(
            "Build me an agent that buys 5 NIFTYBEES every Monday at "
            "09:15 IST and sells the entire holding at Monday close "
            "(15:30 IST) if the daily 14-period RSI is below 30. "
            "Automatic execution."
        ),
        expected_tools=frozenset({"propose_workflow"}),
        max_acceptable_asks=0,
        expected_render_hints=("workflow_draft_card",),
        notes="Should emit one draft with TWO trigger.schedule steps.",
    ),
    Prompt(
        name="conditional_dip_buy",
        bucket="agent",
        text=(
            "Create a strategy that watches HDFCBANK and buys 3 shares "
            "when the price drops 2% below today's open, with a 2% stop "
            "loss after the buy. Automatic execution."
        ),
        expected_tools=frozenset({"propose_workflow", "create_strategy"}),
        max_acceptable_asks=1,
        expected_render_hints=("workflow_draft_card", "logic_card"),
    ),

    # ── vague (one ask OR sensible default) ─────────────────────────
    Prompt(
        name="vague_buy_no_qty",
        bucket="vague",
        text="Buy some RELIANCE",
        expected_tools=frozenset({"place_market_order"}),
        max_acceptable_asks=1,
        notes="Either default to 1 share with LogicCard OR ASK_USER for qty.",
    ),
    Prompt(
        name="vague_chart",
        bucket="vague",
        text="Show me a chart of HDFCBANK",
        expected_tools=frozenset({"get_price_history"}),
        max_acceptable_asks=0,
        notes="Default period (1y).",
    ),
    Prompt(
        name="stoploss_no_qty",
        bucket="vague",
        text="Set a stop loss on my INFY",
        expected_tools=frozenset({
            "create_sl_order", "calculate_sl_price",
            "get_holding_detail", "get_holdings",
        }),
        max_acceptable_asks=1,
        notes="Needs trigger price OR pulls qty from holdings.",
    ),

    # ── off_topic ───────────────────────────────────────────────────
    Prompt(
        name="weather",
        bucket="off_topic",
        text="What's the weather like in Mumbai?",
        expected_tools=frozenset(),
        max_acceptable_asks=0,
        required_phrases=(),
        notes="Should reply without calling any tool — admit it's not in scope.",
    ),
    Prompt(
        name="should_i_buy",
        bucket="off_topic",
        text="Should I buy now?",
        expected_tools=frozenset(),
        max_acceptable_asks=1,
        notes="Non-directive answer; no recommendation.",
    ),
    Prompt(
        name="build_something",
        bucket="vague",
        text="build something",
        expected_tools=frozenset(),  # too vague — should ask, not build.
        max_acceptable_asks=1,
        notes="Should ask the user what kind of automation they want.",
    ),
    Prompt(
        name="conditional_simple",
        bucket="agent",
        text="Sell my INFY when RSI rises above 70",
        expected_tools=frozenset({"propose_workflow", "create_strategy"}),
        max_acceptable_asks=1,
        expected_render_hints=("workflow_draft_card", "logic_card"),
    ),
    # User-reported failing prompt (2026-05-04): the model asked for
    # qty, the user said "2 shares", then the model produced a verbal
    # "Confirm: …" turn instead of emitting the draft. Then on
    # confirm it hit a generic validation error and gave up. Tracks
    # whether the system emits the draft directly with quantity=2.
    Prompt(
        name="tcs_mon_buy_tue_sell",
        bucket="agent",
        text=(
            "Build me an agent that buys 2 TCS at Monday open if Monday "
            "open is below previous close, and sells 2 TCS at Tuesday "
            "open if current price is above the average buy price. "
            "Automatic execution."
        ),
        expected_tools=frozenset({"propose_workflow"}),
        max_acceptable_asks=0,
        expected_render_hints=("workflow_draft_card",),
        notes="Multi-trigger agent. Should NOT add a verbal confirmation step.",
    ),
]


_BACKTEST_PROMPTS: list[Prompt] = [
    # ── Backtest bucket — must produce an inline chart card without
    # asking the user for clarification on canonical phrasings. The
    # indicator-backtest shortcut should fire pre-LLM for these.
    Prompt(
        name="bt_rsi_oversold",
        bucket="backtest",
        text="Backtest buying INFY whenever RSI drops below 30 over the last 5 years",
        expected_tools=frozenset(),
        max_acceptable_asks=0,
        expected_intent="INDICATOR_BACKTEST",
        expected_render_hints=("indicator_backtest_chart",),
    ),
    Prompt(
        name="bt_sma_cross",
        bucket="backtest",
        text="Backtest buying TCS when it crosses above 200 SMA over the past 3 years",
        expected_tools=frozenset(),
        max_acceptable_asks=0,
        expected_intent="INDICATOR_BACKTEST",
        expected_render_hints=("indicator_backtest_chart",),
    ),
    Prompt(
        name="bt_ema_natural",
        bucket="backtest",
        text="What if I had bought RELIANCE every time it dropped below its 200 EMA over the last 2 years",
        expected_tools=frozenset(),
        max_acceptable_asks=0,
        expected_intent="INDICATOR_BACKTEST",
        expected_render_hints=("indicator_backtest_chart",),
        notes="Natural-language phrasing — no leading verb.",
    ),
    Prompt(
        name="bt_slash_command",
        bucket="backtest",
        text="/expr-backtest pe_ratio < 15 from 2020-01-01 to 2024-12-31",
        expected_tools=frozenset(),
        max_acceptable_asks=0,
        expected_intent="EXPR_BACKTEST",
        expected_render_hints=("financial_backtest_chart",),
        notes="Explicit slash command for fundamentals backtest.",
    ),
    Prompt(
        name="bt_vague",
        bucket="backtest",
        text="Backtest a strategy on HDFCBANK",
        expected_tools=frozenset({"run_backtest"}),
        max_acceptable_asks=1,
        expected_render_hints=("indicator_backtest_chart",),
        notes="Vague — needs one focused clarification (which strategy?).",
    ),
]

PROMPTS.extend(_BACKTEST_PROMPTS)


# Bucket subsets — used for focused per-turn-trace tests.
AGENT_PROMPT_NAMES: tuple[str, ...] = tuple(
    p.name for p in PROMPTS if p.bucket == "agent"
)
BACKTEST_PROMPT_NAMES: tuple[str, ...] = tuple(
    p.name for p in PROMPTS if p.bucket == "backtest"
)


# ── Streaming chat client ───────────────────────────────────────────


def _resolve_token() -> str:
    p = Path("/tmp/pivot_token.txt")
    if p.exists():
        tok = p.read_text().strip()
        if tok:
            return tok
    email = f"grader_{int(time.time())}@example.com"
    body = json.dumps({
        "email": email, "password": "password123", "full_name": "Grader",
    }).encode()
    req = urllib.request.Request(
        "http://127.0.0.1:8000/auth/register",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        token = json.loads(r.read())["access_token"]
    p.write_text(token)
    return token


@dataclass
class Outcome:
    name: str
    bucket: str
    prompt: str
    response: str
    tools_called: list[str]
    n_asks: int
    n_llm_hops: int
    render_hint: Optional[str]
    intent: Optional[str]
    latency_ms: int
    error: Optional[str]
    score: int = 0
    score_breakdown: list[str] = field(default_factory=list)


@dataclass
class Aggregate:
    """Per-prompt aggregate across N samples."""
    name: str
    bucket: str
    prompt: str
    samples: list[Outcome] = field(default_factory=list)
    median_score: int = 0
    min_score: int = 0
    max_score: int = 0
    median_latency_ms: int = 0
    # The "representative" run — the one closest to the median score.
    rep: Optional[Outcome] = None

    def finalize(self) -> None:
        scores = sorted(s.score for s in self.samples)
        latencies = sorted(s.latency_ms for s in self.samples)
        if not scores:
            return
        mid = len(scores) // 2
        # Median: lower midpoint when even count (consistent with
        # the "pessimistic" reading we want for a quality bar).
        self.median_score = scores[mid] if len(scores) % 2 else scores[mid - 1]
        self.min_score = scores[0]
        self.max_score = scores[-1]
        self.median_latency_ms = (
            latencies[mid] if len(latencies) % 2 else latencies[mid - 1]
        )
        # Pick the sample whose score is closest to the median.
        self.rep = min(
            self.samples,
            key=lambda s: (abs(s.score - self.median_score), s.latency_ms),
        )


def run_one(prompt: Prompt, token: str) -> Outcome:
    body = json.dumps({
        "messages": [{"role": "user", "content": prompt.text}],
        "include_portfolio_context": True,
        "conversation_id": f"grader-{prompt.name}-{int(time.time()*1000)}",
    }).encode()

    # First try the streaming endpoint. If we get a non-streaming
    # response (intent set on POST /chat slash routes), the streaming
    # endpoint also handles it identically — but those routes return
    # an `intent` field on POST /chat that the streaming endpoint
    # doesn't surface in `done`. So we hit POST /chat directly to
    # capture intent for shortcut-routed prompts.
    req = urllib.request.Request(
        "http://127.0.0.1:8000/chat",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:
        return Outcome(
            name=prompt.name, bucket=prompt.bucket, prompt=prompt.text,
            response="", tools_called=[], n_asks=0, n_llm_hops=0,
            render_hint=None, intent=None,
            latency_ms=int((time.monotonic() - t0) * 1000),
            error=f"HTTP {e.code}: {e.read().decode()[:200]}",
        )
    except Exception as e:
        return Outcome(
            name=prompt.name, bucket=prompt.bucket, prompt=prompt.text,
            response="", tools_called=[], n_asks=0, n_llm_hops=0,
            render_hint=None, intent=None,
            latency_ms=int((time.monotonic() - t0) * 1000),
            error=f"{type(e).__name__}: {e}",
        )

    bd = data.get("latency_breakdown") or {}
    n_llm_hops = sum(
        1 for k in bd
        if k.startswith("llm_hop_") and not k.endswith("_cached")
    )
    render_hint = ((data.get("raw_data") or {}) or {}).get("_render_hint")
    tools_called: list[str] = data.get("tools_called") or []
    n_asks = tools_called.count("ASK_USER") + (
        1 if render_hint == "ask_user" else 0
    )
    # Avoid double counting: if ASK_USER is in tools_called AND render_hint
    # is "ask_user", that's still ONE ask.
    n_asks = max(
        tools_called.count("ASK_USER"),
        1 if render_hint == "ask_user" else 0,
    )

    return Outcome(
        name=prompt.name, bucket=prompt.bucket, prompt=prompt.text,
        response=(data.get("response") or "")[:600],
        tools_called=tools_called,
        n_asks=n_asks,
        n_llm_hops=n_llm_hops,
        render_hint=render_hint,
        intent=data.get("intent"),
        latency_ms=int(data.get("latency_ms") or (time.monotonic() - t0) * 1000),
        error=None,
    )


# ── Scoring ─────────────────────────────────────────────────────────


_LLM_UNAVAILABLE_MARKER = "AI backend is temporarily unavailable"


def grade(prompt: Prompt, out: Outcome) -> Outcome:
    score = 0
    breakdown: list[str] = []

    if out.error:
        out.score = 0
        out.score_breakdown = [f"error: {out.error[:80]}"]
        return out

    if _LLM_UNAVAILABLE_MARKER in out.response:
        out.score = 0
        out.score_breakdown = ["LLM-unavailable fallback returned"]
        return out

    # Tool axis (+5 / -3)
    expected = prompt.expected_tools
    actual_real_tools = {t for t in out.tools_called if t != "ASK_USER"}
    asked_only = (out.n_asks > 0 and not actual_real_tools)
    if not expected:
        # We expected NO tool. Bucket-specific rules:
        if prompt.bucket == "off_topic":
            if not actual_real_tools:
                score += 5
                breakdown.append("+5 no tool (off-topic, correct)")
            else:
                score -= 3
                breakdown.append(
                    f"-3 wrong tool fired on off-topic: {sorted(actual_real_tools)}"
                )
        else:
            # Deterministic-shortcut prompts (intent set, no LLM tool).
            if prompt.expected_intent and out.intent == prompt.expected_intent:
                score += 5
                breakdown.append(f"+5 deterministic shortcut hit ({out.intent})")
            elif prompt.expected_intent:
                score -= 3
                breakdown.append(
                    f"-3 expected intent {prompt.expected_intent}, got {out.intent}"
                )
            elif not actual_real_tools and not prompt.expected_render_hints:
                score += 3
                breakdown.append("+3 no tool (vague — sensible)")
            else:
                score += 0
    else:
        if actual_real_tools & expected:
            score += 5
            breakdown.append(
                f"+5 expected tool fired ({sorted(actual_real_tools & expected)})"
            )
        elif asked_only and prompt.max_acceptable_asks > 0:
            # Vague prompt, model chose to ASK_USER instead of guessing —
            # acceptable when the prompt's ask budget allows it.
            score += 4
            breakdown.append(
                "+4 model asked a focused clarification (vague prompt)"
            )
        elif actual_real_tools:
            score -= 3
            breakdown.append(
                f"-3 wrong tool: got {sorted(actual_real_tools)}, "
                f"expected one of {sorted(expected)}"
            )
        else:
            # No tool fired at all.
            score -= 3
            breakdown.append(f"-3 no tool fired; expected {sorted(expected)}")

    # Ask-count axis (+3 / -3)
    if out.n_asks <= prompt.max_acceptable_asks:
        score += 3
        breakdown.append(f"+3 asks={out.n_asks} <= max={prompt.max_acceptable_asks}")
    else:
        score -= 3
        breakdown.append(
            f"-3 over-asked: asks={out.n_asks}, max={prompt.max_acceptable_asks}"
        )

    # Render hint axis (informational only, full credit if expected hits)
    if prompt.expected_render_hints:
        if out.render_hint in prompt.expected_render_hints:
            score += 2
            breakdown.append(f"+2 render_hint={out.render_hint}")
        else:
            score -= 1
            breakdown.append(
                f"-1 render_hint={out.render_hint}, expected one of "
                f"{prompt.expected_render_hints}"
            )
    else:
        # No specific hint expected; baseline credit if no error.
        score += 2
        breakdown.append("+2 no error")

    # Hop budget (-2 if too chatty)
    if out.n_llm_hops > 4:
        score -= 2
        breakdown.append(f"-2 too many LLM hops ({out.n_llm_hops})")

    # Required phrases — informational, not graded for now.
    if prompt.required_phrases:
        low = out.response.lower()
        if not any(p.lower() in low for p in prompt.required_phrases):
            score -= 1
            breakdown.append(
                f"-1 response missing expected phrase ({prompt.required_phrases})"
            )

    # Cap each prompt at 10 to keep the rubric proportional.
    score = max(0, min(10, score))
    out.score = score
    out.score_breakdown = breakdown
    return out


# ── Reporting ───────────────────────────────────────────────────────


def _print_table(aggs: list[Aggregate], n_samples: int) -> None:
    print()
    print("=" * 132)
    if n_samples > 1:
        header = (
            f"  {'name':22s} {'bucket':10s} {'med':>4s} {'min':>4s} {'max':>4s} "
            f"{'p50_ms':>7s}  {'rep_tools'}"
        )
    else:
        header = (
            f"  {'name':22s} {'bucket':10s} {'score':>5s} {'asks':>4s} "
            f"{'hops':>4s}  {'tools'}"
        )
    print(header)
    print("-" * 132)
    for a in aggs:
        rep = a.rep
        tools = ",".join(rep.tools_called) if rep else "-"
        if len(tools) > 50:
            tools = tools[:50] + "…"
        if n_samples > 1:
            print(
                f"  {a.name:20s} {a.bucket:10s} "
                f"{a.median_score:>4d} {a.min_score:>4d} {a.max_score:>4d} "
                f"{a.median_latency_ms:>7d}  {tools}"
            )
        else:
            print(
                f"  {a.name:20s} {a.bucket:10s} "
                f"{a.median_score:>5d} {rep.n_asks:>4d} {rep.n_llm_hops:>4d}  "
                f"{tools}"
            )
    print("-" * 132)
    total = sum(a.median_score for a in aggs)
    max_total = len(aggs) * 10
    label = "MEDIAN TOTAL" if n_samples > 1 else "TOTAL"
    print(f"  {label}: {total} / {max_total}  ({100*total//max_total}%)")

    # Per-bucket aggregate (median scores).
    bucket_scores: dict[str, list[int]] = defaultdict(list)
    for a in aggs:
        bucket_scores[a.bucket].append(a.median_score)
    print()
    print("  Per-bucket (median):")
    for bucket, scores in sorted(bucket_scores.items()):
        avg = sum(scores) / len(scores)
        print(f"    {bucket:10s}  n={len(scores):2d}  avg={avg:.1f}/10")


def _print_failures(aggs: list[Aggregate]) -> None:
    failed = [a for a in aggs if a.median_score < 8]
    if not failed:
        return
    print()
    print("=" * 132)
    print("UNDER-PERFORMING PROMPTS (median < 8)")
    print("=" * 132)
    for a in failed:
        rep = a.rep
        print()
        print(
            f"  [{a.name}] (bucket={a.bucket}, "
            f"median={a.median_score}, range=[{a.min_score},{a.max_score}])"
        )
        print(f"    prompt: {a.prompt}")
        if rep is None:
            continue
        print(f"    rep response: {rep.response[:240]}")
        print(
            f"    rep: tools={rep.tools_called}  asks={rep.n_asks}  "
            f"hops={rep.n_llm_hops}  render={rep.render_hint}"
        )
        for line in rep.score_breakdown:
            print(f"      {line}")


# ── CLI ─────────────────────────────────────────────────────────────


def _snapshot_aggregate_total(snap: dict) -> int:
    """Pull a comparable total from a snapshot. Newer snapshots store
    ``median_total``; older ones store ``total`` from a single run.
    Either way returns an int comparable across labels."""
    if "median_total" in snap:
        return int(snap["median_total"])
    return int(snap.get("total", 0))


def _snapshot_per_prompt_score(snap: dict, name: str) -> Optional[int]:
    """Score for one prompt out of a snapshot (median if multi-sample,
    score if single-sample). Used by the diff view."""
    for entry in snap.get("aggregates", []) or []:
        if entry.get("name") == name:
            return int(entry.get("median_score", 0))
    for entry in snap.get("outcomes", []) or []:
        if entry.get("name") == name:
            return int(entry.get("score", 0))
    return None


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--label", default="run", help="snapshot label")
    p.add_argument("--diff", default=None, help="compare against an earlier label")
    p.add_argument("--results-dir", default="tests/grader_results")
    p.add_argument("--prompts", default=None,
                   help="comma-separated prompt names to filter (default: all)")
    p.add_argument("--samples", type=int, default=1,
                   help="run each prompt N times; aggregate by median (variance-robust)")
    args = p.parse_args()

    if args.samples < 1:
        print("[grader] --samples must be >= 1")
        return 2

    selected = PROMPTS
    if args.prompts:
        wanted = {n.strip() for n in args.prompts.split(",") if n.strip()}
        selected = [pr for pr in PROMPTS if pr.name in wanted]
        if not selected:
            print(f"no prompts matched {wanted}; known names:")
            for pr in PROMPTS:
                print(f"  {pr.name}")
            return 2

    token = _resolve_token()
    print(f"[grader] token: {token[:18]}…")
    print(f"[grader] running {len(selected)} prompts × {args.samples} sample(s)")

    aggregates: list[Aggregate] = []
    for prompt in selected:
        agg = Aggregate(name=prompt.name, bucket=prompt.bucket, prompt=prompt.text)
        print(f"  • {prompt.name:24s}  ", end="", flush=True)
        for sample_idx in range(args.samples):
            out = run_one(prompt, token)
            out = grade(prompt, out)
            agg.samples.append(out)
            sym = "✓" if out.score >= 8 else ("·" if out.score >= 5 else "✗")
            print(sym, end="", flush=True)
        agg.finalize()
        median_sym = (
            "✓" if agg.median_score >= 8
            else ("·" if agg.median_score >= 5 else "✗")
        )
        print(
            f" {median_sym} median={agg.median_score}/10 "
            f"range=[{agg.min_score},{agg.max_score}] p50={agg.median_latency_ms}ms"
        )
        aggregates.append(agg)

    _print_table(aggregates, args.samples)
    _print_failures(aggregates)

    # Persist snapshot. New shape uses `aggregates`; we still emit
    # `outcomes` (every sample, flattened) so older diff readers can
    # consume the file without code changes.
    out_dir = Path(args.results_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    snap_path = out_dir / f"{args.label}.json"
    snap = {
        "label": args.label,
        "ts": time.time(),
        "samples_per_prompt": args.samples,
        "aggregates": [
            {
                "name": a.name,
                "bucket": a.bucket,
                "prompt": a.prompt,
                "median_score": a.median_score,
                "min_score": a.min_score,
                "max_score": a.max_score,
                "median_latency_ms": a.median_latency_ms,
                "samples": [s.__dict__ for s in a.samples],
            }
            for a in aggregates
        ],
        "outcomes": [
            s.__dict__
            for a in aggregates
            for s in a.samples
        ],
        "median_total": sum(a.median_score for a in aggregates),
        "total": sum(s.score for a in aggregates for s in a.samples) // max(args.samples, 1),
        "max": len(aggregates) * 10,
    }
    snap_path.write_text(json.dumps(snap, indent=2, default=str))
    print(f"\n[grader] snapshot written to {snap_path}")

    if args.diff:
        prev_path = out_dir / f"{args.diff}.json"
        if prev_path.exists():
            prev = json.loads(prev_path.read_text())
            print()
            print("=" * 80)
            label = (
                "median" if args.samples > 1 or "median_total" in prev else "score"
            )
            print(f"DIFF vs {args.diff}  ({label})")
            print("=" * 80)
            for a in aggregates:
                p_score = _snapshot_per_prompt_score(prev, a.name) or 0
                delta = a.median_score - p_score
                arrow = "→" if delta == 0 else ("↑" if delta > 0 else "↓")
                print(
                    f"  {a.name:24s} {p_score} {arrow} {a.median_score}  ({delta:+d})"
                )
            prev_total = _snapshot_aggregate_total(prev)
            print(
                f"\n  Total ({label}): {prev_total} → {snap['median_total']}  "
                f"({snap['median_total'] - prev_total:+d})"
            )
        else:
            print(f"\n[grader] no prior snapshot at {prev_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
