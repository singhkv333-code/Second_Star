"""LLM-call audit harness.

Runs five representative user flows through POST /chat/stream while the
backend has PIVOT_LLM_TRACE enabled, then aggregates per-flow metrics
from both the SSE stream and the trace JSONL written by
``backend.llm._trace.CallTrace``.

Usage::

    # In one terminal:
    PIVOT_LLM_TRACE=/tmp/pivot_llm_trace.jsonl uvicorn backend.main:app --reload --port 8000

    # In another:
    python -m scripts.audit_llm_flows

The script prints two tables:

  1. Per-flow summary (TTFT, total wall, n_llm_calls, n_tool_calls,
     repeated tools, total / cached / reasoning tokens).
  2. Per-call breakdown (caller, latency, tokens, response preview).

Then a "Findings" block with auto-detected red flags:
  - same tool called more than once per flow (validation-retry burn)
  - LLM calls with empty system prompt where one is clearly needed
  - duplicated cached prefix that didn't hit the prompt cache
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
from pathlib import Path
from typing import Any, Optional


# ── Flows ────────────────────────────────────────────────────────────


FLOWS: list[tuple[str, str]] = [
    # (name, prompt) — kept terse + representative.
    ("greeting",      "hi"),                                    # fast-path
    ("definition",    "What is RSI in one sentence?"),          # 1-hop, no tools
    ("price_query",   "What's the live price of RELIANCE?"),    # 1-hop + 1 tool
    ("agent_build",   "Build me an agent named WeeklyBuy that buys 10 NIFTYBEES at market price with automatic execution every weekday at 09:15 IST."),  # multi-hop + propose_workflow
    ("ambiguous_buy", "Buy some RELIANCE."),                    # missing-fields → ASK_USER
]


# ── Config ───────────────────────────────────────────────────────────


def _resolve_token() -> str:
    p = Path("/tmp/pivot_token.txt")
    if p.exists():
        tok = p.read_text().strip()
        if tok:
            return tok
    # Register a fresh user.
    email = f"audit_{int(time.time())}@example.com"
    body = json.dumps({"email": email, "password": "password123", "full_name": "Audit"}).encode()
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


def _stream_one(prompt: str, conv_id: str, token: str) -> dict[str, Any]:
    """Run one flow and return aggregate timing/event counts."""
    body = json.dumps({
        "messages": [{"role": "user", "content": prompt}],
        "include_portfolio_context": False,
        "conversation_id": conv_id,
    }).encode()
    req = urllib.request.Request(
        "http://127.0.0.1:8000/chat/stream",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
            "Accept": "text/event-stream",
        },
        method="POST",
    )
    t0 = time.monotonic()
    first_delta_t: Optional[float] = None
    n_deltas = 0
    tool_starts: list[str] = []
    tool_dones: list[tuple[str, bool]] = []
    done_payload: Optional[dict[str, Any]] = None
    error_msg: Optional[str] = None

    with urllib.request.urlopen(req, timeout=120) as r:
        for raw in r:
            line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            if not line.startswith("data:"):
                continue
            try:
                ev = json.loads(line[5:].strip())
            except json.JSONDecodeError:
                continue
            t = ev.get("type")
            if t == "delta":
                if first_delta_t is None:
                    first_delta_t = time.monotonic()
                n_deltas += 1
            elif t == "tool_start":
                tool_starts.append(ev.get("name", ""))
            elif t == "tool_done":
                tool_dones.append((ev.get("name", ""), bool(ev.get("ok"))))
            elif t == "done":
                done_payload = ev
            elif t == "error":
                error_msg = ev.get("message", "")

    total_ms = int((time.monotonic() - t0) * 1000)
    ttft_ms = int((first_delta_t - t0) * 1000) if first_delta_t else None

    bd = (done_payload or {}).get("latency_breakdown", {}) or {}
    n_llm_hops = sum(1 for k in bd if k.startswith("llm_hop_") and not k.endswith("_cached"))
    return {
        "total_ms": total_ms,
        "ttft_ms": ttft_ms,
        "n_deltas": n_deltas,
        "n_llm_hops": n_llm_hops,
        "tool_starts": tool_starts,
        "tool_dones": tool_dones,
        "tools_called": (done_payload or {}).get("tools_called", []),
        "render_hint": ((done_payload or {}).get("raw_data") or {}).get("_render_hint"),
        "response_chars": len((done_payload or {}).get("response", "")),
        "latency_breakdown": bd,
        "error": error_msg,
    }


# ── Trace reader ─────────────────────────────────────────────────────


def _read_trace_since(path: str, baseline_offset: int) -> list[dict]:
    """Read records appended to the trace file after `baseline_offset`."""
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


# ── Pretty printers ──────────────────────────────────────────────────


def _print_summary_table(results: list[tuple[str, dict, list[dict]]]) -> None:
    headers = [
        "flow", "wall_ms", "ttft_ms", "n_hops", "n_tools",
        "tokens_in", "cached", "tokens_out", "reasoning",
        "tools",
    ]
    print()
    print("=" * 132)
    print("PER-FLOW SUMMARY")
    print("=" * 132)
    print("  " + " | ".join(f"{h:>10}" if i > 0 else f"{h:<14}" for i, h in enumerate(headers)))
    print("  " + "-" * 130)
    for name, r, traces in results:
        usage_in = sum(int((t.get("usage") or {}).get("input_tokens") or 0) for t in traces)
        usage_cached = sum(int((t.get("usage") or {}).get("cached_tokens") or 0) for t in traces)
        usage_out = sum(int((t.get("usage") or {}).get("output_tokens") or 0) for t in traces)
        usage_reason = sum(int((t.get("usage") or {}).get("reasoning_tokens") or 0) for t in traces)
        n_tools = len(r.get("tool_starts") or [])
        ttft = r.get("ttft_ms")
        row = [
            f"{name:<14}",
            f"{r.get('total_ms', 0):>10}",
            f"{ttft if ttft is not None else '-':>10}",
            f"{r.get('n_llm_hops', 0):>10}",
            f"{n_tools:>10}",
            f"{usage_in:>10}",
            f"{usage_cached:>10}",
            f"{usage_out:>10}",
            f"{usage_reason:>10}",
            ", ".join((r.get('tools_called') or [])[:3]) or "-",
        ]
        print("  " + " | ".join(row))


def _print_call_table(name: str, traces: list[dict]) -> None:
    if not traces:
        print(f"\n  [{name}] (no LLM calls — fast-path)")
        return
    print(f"\n  [{name}] {len(traces)} LLM call(s):")
    for i, t in enumerate(traces, 1):
        usage = t.get("usage") or {}
        u_in = usage.get("input_tokens", 0)
        u_cached = usage.get("cached_tokens", 0)
        u_out = usage.get("output_tokens", 0)
        u_reason = usage.get("reasoning_tokens", 0)
        finish = usage.get("finish_reason", "?")
        latency = t.get("latency_ms", 0)
        ttft = t.get("ttft_ms")
        ttft_str = f"ttft={ttft}ms" if ttft is not None else "ttft=n/a"
        caller = (t.get("caller") or "?").split()
        caller_short = caller[-1] if caller else "?"
        kind = t.get("kind", "?")
        re_eff = t.get("reasoning_effort") or "-"
        cache_key = t.get("prompt_cache_key") or "-"
        ntools = t.get("tools_count", 0)
        msgs = t.get("input_messages") or []
        n_msgs = len(msgs)
        in_chars = t.get("input_chars_total", 0)
        out_chars = t.get("response_chars", 0)
        n_tc = len(t.get("tool_calls") or [])
        print(
            f"    #{i} kind={kind} caller={caller_short} effort={re_eff} cache={cache_key}"
        )
        print(
            f"        msgs={n_msgs} input_chars={in_chars} tools_in={ntools} "
            f"latency={latency}ms {ttft_str} finish={finish}"
        )
        print(
            f"        tokens: in={u_in} cached={u_cached} out={u_out} reasoning={u_reason}"
        )
        if n_tc:
            for tc in t.get("tool_calls") or []:
                print(
                    f"        → tool_call {tc.get('name')!r} args[{tc.get('arguments_chars',0)}c]: "
                    f"{(tc.get('arguments_preview') or '')[:120]}"
                )
        if t.get("response_chars"):
            print(
                f"        ← assistant text[{t.get('response_chars')}c]: "
                f"{(t.get('response_text') or '')[:120]}"
            )


def _findings(name: str, r: dict, traces: list[dict]) -> list[str]:
    out: list[str] = []
    # Repeated tool calls — validation-retry burn.
    starts = r.get("tool_starts") or []
    if starts:
        c = Counter(starts)
        for tool, n in c.items():
            if n > 1:
                out.append(f"tool {tool!r} called {n}× — likely validation retry")
    # LLM calls without a system prompt where one is needed.
    for i, t in enumerate(traces, 1):
        msgs = t.get("input_messages") or []
        has_system = any(m.get("role") == "system" for m in msgs)
        if not has_system and t.get("caller", "").startswith("backend.services.validation_handler"):
            out.append(
                f"call #{i} ({t.get('caller')}): no system prompt — relies on a "
                "single user-message template; check if rule could be deterministic"
            )
    # Cache misses on what should be cached.
    for i, t in enumerate(traces, 1):
        usage = t.get("usage") or {}
        cached = int(usage.get("cached_tokens") or 0)
        in_tok = int(usage.get("input_tokens") or 0)
        if t.get("prompt_cache_key") and in_tok > 1500 and cached == 0 and i > 1:
            out.append(
                f"call #{i}: cache key set but cached=0 with input={in_tok} "
                "tokens — prefix changed or first-of-key"
            )
    return out


# ── Main ─────────────────────────────────────────────────────────────


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--trace-path", default=os.environ.get("PIVOT_LLM_TRACE",
                                                          "/tmp/pivot_llm_trace.jsonl"))
    p.add_argument("--reset-trace", action="store_true",
                   help="Truncate the trace file before running")
    args = p.parse_args()

    if args.reset_trace and os.path.exists(args.trace_path):
        open(args.trace_path, "w").close()

    token = _resolve_token()
    print(f"[audit] token: {token[:18]}…")
    print(f"[audit] trace path: {args.trace_path}")
    print(f"[audit] trace size before: {os.path.getsize(args.trace_path) if os.path.exists(args.trace_path) else 0} bytes")

    results: list[tuple[str, dict, list[dict]]] = []
    for name, prompt in FLOWS:
        before = os.path.getsize(args.trace_path) if os.path.exists(args.trace_path) else 0
        print(f"\n[audit] running flow {name!r}: {prompt!r}")
        try:
            r = _stream_one(prompt, conv_id=f"audit-{name}", token=token)
        except urllib.error.HTTPError as e:
            print(f"  HTTPError: {e}")
            continue
        except Exception as e:
            print(f"  ERROR: {type(e).__name__}: {e}")
            continue
        # tiny pause to let trace flush.
        time.sleep(0.1)
        traces = _read_trace_since(args.trace_path, before)
        results.append((name, r, traces))
        print(
            f"  done in {r['total_ms']}ms "
            f"(ttft={r['ttft_ms']}, hops={r['n_llm_hops']}, tools={len(r['tool_starts'])})"
        )

    _print_summary_table(results)

    print()
    print("=" * 132)
    print("PER-CALL DETAIL")
    print("=" * 132)
    for name, r, traces in results:
        _print_call_table(name, traces)

    print()
    print("=" * 132)
    print("FINDINGS")
    print("=" * 132)
    any_findings = False
    for name, r, traces in results:
        f = _findings(name, r, traces)
        if f:
            any_findings = True
            print(f"\n  [{name}]:")
            for line in f:
                print(f"    - {line}")
    if not any_findings:
        print("  (no automated red flags)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
