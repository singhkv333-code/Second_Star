"""Focused agent-bucket test with per-turn trace dump.

Wraps `grade_automation_quality.run_one + grade` for the agent prompts
only, runs N samples each, and after every prompt prints the LLM trace
records that fired during that turn — caller, latency, tool_calls, and
the args the model actually emitted. Surfaces exactly where the agent
build went wrong (model didn't emit, validation failed, model added a
verbal confirm step).

Usage::

    PIVOT_LLM_TRACE=/tmp/pivot_llm_trace.jsonl uvicorn backend.main:app --reload --port 8000
    python -m scripts.agent_trace_test --samples 3
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Optional

# Reuse the grader's prompt set + scoring rather than duplicating it.
from scripts.grade_automation_quality import (
    AGENT_PROMPT_NAMES,
    PROMPTS,
    Aggregate,
    grade,
    run_one,
    _resolve_token,
)


def _read_trace_since(path: str, baseline_offset: int) -> list[dict]:
    if not os.path.exists(path):
        return []
    out: list[dict] = []
    with open(path, "r", encoding="utf-8") as fh:
        fh.seek(baseline_offset)
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def _print_trace_records(records: list[dict]) -> None:
    if not records:
        print("    (no LLM trace records — fast-path / shortcut path)")
        return
    for i, t in enumerate(records, 1):
        usage = t.get("usage") or {}
        u_in = usage.get("input_tokens", 0)
        u_cached = usage.get("cached_tokens", 0)
        u_out = usage.get("output_tokens", 0)
        u_reason = usage.get("reasoning_tokens", 0)
        finish = usage.get("finish_reason", "?")
        latency = t.get("latency_ms", 0)
        ttft = t.get("ttft_ms")
        ttft_str = f"ttft={ttft}ms" if ttft is not None else ""
        kind = t.get("kind", "?")
        cache = t.get("prompt_cache_key") or "-"
        re_eff = t.get("reasoning_effort") or "-"
        ntools = t.get("tools_count", 0)
        msgs = t.get("input_messages") or []
        last_user_or_tool = ""
        for m in reversed(msgs):
            role = m.get("role")
            if role in {"user", "tool"}:
                last_user_or_tool = (
                    f"[last {role}] " + (m.get("content_preview") or "")[:160]
                )
                break
        print(f"    hop {i}  kind={kind} effort={re_eff} cache={cache[-12:]} tools_in={ntools}")
        print(f"            tokens: in={u_in} cached={u_cached} out={u_out} reasoning={u_reason} finish={finish}")
        print(f"            latency={latency}ms {ttft_str}")
        if last_user_or_tool:
            print(f"            {last_user_or_tool}")
        for tc in t.get("tool_calls") or []:
            preview = (tc.get("arguments_preview") or "")[:200]
            print(f"            → {tc.get('name')}({tc.get('arguments_chars',0)}c): {preview}")
        if t.get("response_chars"):
            print(
                f"            ← assistant text[{t.get('response_chars')}c]: "
                f"{(t.get('response_text') or '')[:160]}"
            )


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--samples", type=int, default=3)
    p.add_argument("--trace-path", default=os.environ.get(
        "PIVOT_LLM_TRACE", "/tmp/pivot_llm_trace.jsonl",
    ))
    p.add_argument("--reset-trace", action="store_true",
                   help="Truncate the trace before running")
    args = p.parse_args()

    if args.reset_trace and os.path.exists(args.trace_path):
        open(args.trace_path, "w").close()

    selected = [p for p in PROMPTS if p.name in AGENT_PROMPT_NAMES]
    print(f"[trace-test] running {len(selected)} agent prompts × {args.samples} samples")

    token = _resolve_token()
    print(f"[trace-test] token: {token[:18]}…")
    print(f"[trace-test] trace: {args.trace_path}")

    aggs: list[Aggregate] = []
    for prompt in selected:
        agg = Aggregate(name=prompt.name, bucket=prompt.bucket, prompt=prompt.text)
        print()
        print("=" * 132)
        print(f"PROMPT [{prompt.name}]: {prompt.text[:140]}")
        print("=" * 132)
        for s in range(args.samples):
            before = (
                os.path.getsize(args.trace_path) if os.path.exists(args.trace_path) else 0
            )
            out = run_one(prompt, token)
            out = grade(prompt, out)
            time.sleep(0.05)
            recs = _read_trace_since(args.trace_path, before)
            agg.samples.append(out)
            print()
            print(
                f"  sample {s+1}/{args.samples}  "
                f"score={out.score}/10  asks={out.n_asks}  "
                f"hops={out.n_llm_hops}  render={out.render_hint}  "
                f"latency={out.latency_ms}ms"
            )
            print(f"    response: {out.response[:200]}")
            print(f"    tools_called: {out.tools_called}")
            print(f"    score: " + " | ".join(out.score_breakdown))
            _print_trace_records(recs)
        agg.finalize()
        print()
        print(
            f"  → median={agg.median_score}/10  "
            f"range=[{agg.min_score},{agg.max_score}]  "
            f"p50_latency={agg.median_latency_ms}ms"
        )
        aggs.append(agg)

    print()
    print("=" * 132)
    print("SUMMARY")
    print("=" * 132)
    print(f"  {'name':28s} {'med':>4s} {'min':>4s} {'max':>4s} {'p50_ms':>8s}")
    for a in aggs:
        print(
            f"  {a.name:28s} "
            f"{a.median_score:>4d} {a.min_score:>4d} {a.max_score:>4d} "
            f"{a.median_latency_ms:>8d}"
        )
    total = sum(a.median_score for a in aggs)
    max_total = len(aggs) * 10
    print()
    print(
        f"  Median total: {total} / {max_total}  ({100*total//max_total}%)"
    )

    return 0


if __name__ == "__main__":
    sys.exit(main())
