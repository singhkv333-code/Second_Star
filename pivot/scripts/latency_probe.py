"""Chat latency dissection probe.

Runs a grouped prompt set against the live /chat/stream endpoint ONCE
(sequentially, fresh conversation per prompt) and correlates four
independent measurement layers into one per-prompt record:

  1. CLIENT   — perf_counter timestamps of every SSE event as it arrives:
                first event, first tool_start, first text delta (user-
                perceived TTFT), done. Includes network + router prework.
  2. SERVER   — the `done` event's latency_ms + latency_breakdown
                (llm_hop_N, tool_X, …) measured inside handle_stream.
  3. LLM      — PIVOT_LLM_TRACE JSONL: per-call wall latency, ttft_ms
                (first streamed output item), input/cached/output/
                reasoning tokens, endpoint label.
  4. IO       — PIVOT_PERF_TRACE JSONL: every SQL statement and Redis
                command with duration + caller + conv_id, plus fresh
                DBAPI connections (sql_connect).

Cross-checks (client_total >= server_total >= Σ parts) make dropped or
double-counted time visible instead of silently wrong.

Usage (server must run with PIVOT_LLM_TRACE + PIVOT_PERF_TRACE set):

    .venv/bin/python scripts/latency_probe.py \
        --llm-trace /path/llm.jsonl --perf-trace /path/perf.jsonl \
        --out /path/report.json

Conventions honoured: ONE instrumented pass, no rerun loops; report
carries the triad (tokens + latency + a quality slot per item).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

# Runnable from any cwd: put pivot/ (this file's parent's parent) on the path
# so `backend.*` imports resolve.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── The grouped prompt set ──────────────────────────────────────────────
# Small but covering: baseline / reads by security-count / entry-exit
# automations by trigger type / F&O / backtest. One prompt per cell.
PROMPTS: list[tuple[str, str]] = [
    ("baseline_fastpath",        "hi"),
    ("capability",               "What can you do?"),
    ("read_single_price",        "What's the current price of RELIANCE?"),
    ("read_single_analysis",     "Give me a full analysis of TCS"),
    ("read_multi_compare",       "Compare INFY and TCS on fundamentals"),
    ("read_screen_multi",        "Show me IT stocks with PE below 25"),
    ("order_entry_single",       "Buy 10 INFY at market"),
    ("alert_trigger_price",      "Alert me when TCS crosses 4000"),
    ("auto_indicator_entry_exit", "Buy 10 INFY when RSI goes below 30 and sell at 8% profit"),
    ("auto_schedule",            "Every Friday at 3pm buy 1 NIFTYBEES"),
    ("fno_chain",                "Show me the option chain for NIFTY"),
    ("fno_strategy",             "Suggest a bullish options strategy on RELIANCE"),
    ("backtest_indicator",       "Backtest buying RELIANCE when RSI drops below 30 over the last 2 years"),
]

WARMUP_PROMPT = "Tell me one sentence about the Indian stock market."


def _mint_token() -> str:
    from backend.auth.jwt_handler import create_access_token
    return create_access_token(1, "dev@pivot.app")


def _iso_to_epoch_ms(iso: str) -> int:
    return int(datetime.fromisoformat(iso).timestamp() * 1000)


def run_prompt(
    client: httpx.Client, base: str, token: str, prompt: str
) -> dict[str, Any]:
    conv_id = str(uuid.uuid4())
    events: list[dict[str, Any]] = []
    done_payload: Optional[dict] = None
    t0_epoch = int(time.time() * 1000)
    t0 = time.perf_counter()

    with client.stream(
        "POST",
        f"{base}/chat/stream",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "messages": [{"role": "user", "content": prompt}],
            "conversation_id": conv_id,
            "include_portfolio_context": True,
        },
        timeout=240.0,
    ) as resp:
        resp.raise_for_status()
        for line in resp.iter_lines():
            now = time.perf_counter()
            if not line.startswith("data:"):
                continue
            try:
                ev = json.loads(line[5:].strip())
            except json.JSONDecodeError:
                continue
            etype = ev.get("type")
            rec: dict[str, Any] = {"t_ms": round((now - t0) * 1000, 1), "type": etype}
            if etype == "tool_start" or etype == "tool_done":
                rec["name"] = ev.get("name")
                if etype == "tool_done":
                    rec["ok"] = ev.get("ok")
            elif etype == "delta":
                rec["chars"] = len(ev.get("text") or "")
            elif etype == "done":
                done_payload = ev
            elif etype == "error":
                rec["message"] = ev.get("message")
            events.append(rec)

    t1_epoch = int(time.time() * 1000)
    total_ms = round((time.perf_counter() - t0) * 1000, 1)

    def first(etype: str) -> Optional[float]:
        for e in events:
            if e["type"] == etype:
                return e["t_ms"]
        return None

    deltas = [e for e in events if e["type"] == "delta"]
    return {
        "conv_id": conv_id,
        "prompt": prompt,
        "window": [t0_epoch, t1_epoch],
        "client": {
            "total_ms": total_ms,
            "first_event_ms": first("start"),
            "first_tool_start_ms": first("tool_start"),
            "first_delta_ms": deltas[0]["t_ms"] if deltas else None,
            "last_delta_ms": deltas[-1]["t_ms"] if deltas else None,
            "delta_events": len(deltas),
            "delta_chars": sum(d.get("chars", 0) for d in deltas),
        },
        "events": events,
        "server": {
            "latency_ms": (done_payload or {}).get("latency_ms"),
            "breakdown": (done_payload or {}).get("latency_breakdown"),
            "tools_called": (done_payload or {}).get("tools_called"),
            "render_hint": ((done_payload or {}).get("raw_data") or {}).get("_render_hint")
            if isinstance((done_payload or {}).get("raw_data"), dict) else None,
            "response_preview": ((done_payload or {}).get("response") or "")[:240],
            "response_chars": len((done_payload or {}).get("response") or ""),
        },
    }


def _load_jsonl(path: str) -> list[dict]:
    out: list[dict] = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    except FileNotFoundError:
        pass
    return out


def attach_traces(
    results: list[dict], llm_trace_path: str, perf_trace_path: str
) -> None:
    llm_records = _load_jsonl(llm_trace_path)
    perf_records = _load_jsonl(perf_trace_path)
    for rec in llm_records:
        try:
            rec["_epoch_ms"] = _iso_to_epoch_ms(rec["ts"])
        except Exception:
            rec["_epoch_ms"] = None

    for res in results:
        lo, hi = res["window"]
        lo -= 100
        hi += 100
        conv = res["conv_id"]

        hops = [
            r for r in llm_records
            if r.get("_epoch_ms") is not None and lo <= r["_epoch_ms"] <= hi
        ]
        res["llm_hops"] = [
            {
                "endpoint": h.get("endpoint"),
                "kind": h.get("kind"),
                "caller": h.get("caller"),
                "reasoning_effort": h.get("reasoning_effort"),
                "tools_count": h.get("tools_count"),
                "input_chars": h.get("input_chars_total"),
                "latency_ms": h.get("latency_ms"),
                "ttft_ms": h.get("ttft_ms"),
                "usage": h.get("usage"),
            }
            for h in hops
        ]

        # conv_id-tagged records are unambiguous. Null-conv records are only
        # counted when they fall in this prompt's window AND come from the
        # request path (router prework, auth, kite token) — never from the
        # APScheduler background jobs that poll the DB every 30-60s.
        _REQUEST_PATH_CALLERS = (
            # chat-request path only — a stray FE tab polling /portfolio
            # etc. must not leak into a prompt's IO attribution.
            "backend.routers.chat", "backend.auth.", "backend.kite.",
        )
        # The service scopes conversation ids per-user ("u<id>::<uuid>"),
        # so match by containment, not equality.
        mine = [
            r for r in perf_records
            if (conv in str(r.get("conv_id") or ""))
            or (
                r.get("conv_id") is None
                and lo <= r.get("ts_ms", 0) <= hi
                and str(r.get("caller") or "").startswith(_REQUEST_PATH_CALLERS)
            )
        ]
        sql = [r for r in mine if r["kind"] == "sql"]
        redis_ops = [r for r in mine if r["kind"] == "redis"]
        connects = [r for r in mine if r["kind"] == "sql_connect"]

        def _by_caller(records: list[dict]) -> list[dict]:
            agg: dict[str, dict[str, Any]] = {}
            for r in records:
                key = r.get("caller") or "?"
                slot = agg.setdefault(key, {"caller": key, "n": 0, "ms": 0.0})
                slot["n"] += 1
                slot["ms"] += max(r.get("dur_ms") or 0.0, 0.0)
            return sorted(
                ({**s, "ms": round(s["ms"], 1)} for s in agg.values()),
                key=lambda s: -s["ms"],
            )

        res["io"] = {
            "sql_count": len(sql),
            "sql_ms": round(sum(max(r.get("dur_ms") or 0, 0) for r in sql), 1),
            "sql_by_caller": _by_caller(sql)[:8],
            "redis_count": len(redis_ops),
            "redis_ms": round(sum(max(r.get("dur_ms") or 0, 0) for r in redis_ops), 1),
            "redis_by_caller": _by_caller(redis_ops)[:8],
            "sql_connects": len(connects),
        }


def fetch_turn_traces(client: httpx.Client, base: str, results: list[dict]) -> None:
    for res in results:
        try:
            turns: list = []
            # The chat service keys traces by the user-scoped conv id.
            for cid in (f"u1::{res['conv_id']}", res["conv_id"]):
                r = client.get(f"{base}/admin/conv/{cid}/trace", timeout=15.0)
                turns = (r.json() or {}).get("turns") or []
                if turns:
                    break
            if turns:
                res["turn_trace"] = [
                    {"name": e["name"], "elapsed_ms": e["elapsed_ms"],
                     **({k: v for k, v in (e.get("fields") or {}).items()
                         if k in ("tool", "latency_ms", "tools", "reason",
                                   "selected", "intent", "reply_class")})}
                    for e in turns[-1]["events"]
                ]
        except Exception as exc:  # noqa: BLE001 — trace fetch is best-effort
            res["turn_trace_error"] = str(exc)[:120]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--llm-trace", required=True)
    ap.add_argument("--perf-trace", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--skip-warmup", action="store_true")
    args = ap.parse_args()

    token = _mint_token()
    results: list[dict] = []

    with httpx.Client() as client:
        if not args.skip_warmup:
            print("warmup…", flush=True)
            w = run_prompt(client, args.base, token, WARMUP_PROMPT)
            print(f"  warmup total={w['client']['total_ms']}ms", flush=True)

        for group, prompt in PROMPTS:
            print(f"[{group}] {prompt!r}", flush=True)
            res = run_prompt(client, args.base, token, prompt)
            res["group"] = group
            t = res["client"]
            print(
                f"  total={t['total_ms']}ms first_delta={t['first_delta_ms']}ms "
                f"tools={res['server']['tools_called']}",
                flush=True,
            )
            results.append(res)

        # Give trace files a beat to flush, then correlate.
        time.sleep(1.0)
        attach_traces(results, args.llm_trace, args.perf_trace)
        fetch_turn_traces(client, args.base, results)

    report = {
        "ran_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "base": args.base,
        "results": results,
    }
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=1, ensure_ascii=False)
    print(f"\nreport → {args.out}")

    # Compact summary table.
    print(
        f"\n{'group':<26} {'total':>7} {'ttft':>6} {'llm_ms':>7} "
        f"{'sql n/ms':>10} {'redis n/ms':>11} {'hops':>4}"
    )
    for res in results:
        c, io = res["client"], res.get("io", {})
        llm_ms = sum(h.get("latency_ms") or 0 for h in res.get("llm_hops", []))
        print(
            f"{res['group']:<26} {c['total_ms']:>7.0f} "
            f"{(c['first_delta_ms'] or -1):>6.0f} {llm_ms:>7} "
            f"{io.get('sql_count', 0):>4}/{io.get('sql_ms', 0):<5.0f} "
            f"{io.get('redis_count', 0):>5}/{io.get('redis_ms', 0):<5.0f} "
            f"{len(res.get('llm_hops', [])):>4}"
        )


if __name__ == "__main__":
    main()
