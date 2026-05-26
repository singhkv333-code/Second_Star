"""80-prompt eval covering event triggers, multi-condition entry+exit,
holding actions, diagnostics, and conversational fallbacks.

Runs each prompt through POST /chat with a FRESH conversation_id per
prompt (so cross-prompt state never leaks). For prompt categories that
emit a workflow draft (dsl_entry, dsl_entry_exit, holding_exit,
pair_session, scheduled, market_time, threshold_order, holding_action,
news_event, pm_compound, basket), the resulting draft is replayed
through /api/workflows/backtest-draft so we capture eligibility +
metrics.

Output:
  tests/eval_results/<label>.json   — full structured snapshot
  tests/eval_results/<label>.md     — readable per-prompt report
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import httpx

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


BASE = "http://127.0.0.1:8000"
RESULTS_DIR = _PROJECT_ROOT / "tests" / "eval_results"
PROMPTS_DIR = _PROJECT_ROOT / "tests" / "eval_prompts"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)


# Categories whose drafts should be replayed through the backtester.
_BACKTEST_CATEGORIES = {
    "dsl_entry",
    "dsl_entry_exit",
    "holding_exit",
    "pair_session",
    "scheduled",
    "market_time",
    "threshold_order",
    "holding_action",
    "news_event",
    "pm_compound",
    "basket",
    "backtest_only",
}


def _load_prompts(spec: str) -> tuple[str, list[dict]]:
    p = Path(spec)
    if p.suffix == ".json" and p.exists():
        raw = json.loads(p.read_text())
    else:
        raw = json.loads((PROMPTS_DIR / f"{spec}.json").read_text())
    if isinstance(raw, dict) and "prompts" in raw:
        return raw.get("label", spec), raw["prompts"]
    if isinstance(raw, list):
        return spec, raw
    raise ValueError(f"prompt spec must be list or {{prompts:[]}}, got {type(raw)}")


def _register_user() -> tuple[str, int, str]:
    email = f"eval_{uuid.uuid4().hex[:10]}@p.com"
    r = httpx.post(
        f"{BASE}/auth/register",
        json={"email": email, "password": "password123", "full_name": "eval"},
        timeout=30,
    )
    r.raise_for_status()
    body = r.json()
    return body["access_token"], int(body["user_id"]), email


def _post_chat(token: str, prompt: str, conv_id: str) -> tuple[dict, float]:
    started = time.monotonic()
    try:
        r = httpx.post(
            f"{BASE}/chat",
            headers={"Authorization": f"Bearer {token}",
                     "Content-Type": "application/json"},
            json={
                "messages": [{"role": "user", "content": prompt}],
                "conversation_id": conv_id,
                "include_portfolio_context": True,
            },
            timeout=180,
        )
        elapsed_ms = (time.monotonic() - started) * 1000
        if r.status_code != 200:
            return ({"_http_error": r.status_code,
                     "body": r.text[:500]}, elapsed_ms)
        return r.json(), elapsed_ms
    except Exception as exc:
        elapsed_ms = (time.monotonic() - started) * 1000
        return ({"_exception": str(exc)}, elapsed_ms)


def _post_backtest(token: str, steps: list[dict], name: str) -> dict:
    try:
        r = httpx.post(
            f"{BASE}/api/workflows/backtest-draft",
            headers={"Authorization": f"Bearer {token}",
                     "Content-Type": "application/json"},
            json={"name": name, "steps": steps, "period": "2y"},
            timeout=240,
        )
        if r.status_code != 200:
            return {"_http_error": r.status_code, "body": r.text[:300]}
        return r.json()
    except Exception as exc:
        return {"_exception": str(exc)}


def _summarize_chat(body: dict) -> dict:
    if "_http_error" in body or "_exception" in body:
        return {
            "error": body.get("_exception") or f"HTTP {body.get('_http_error')}",
            "body": body.get("body"),
        }
    rd = body.get("raw_data") or {}
    return {
        "response": body.get("response") or "",
        "tools_called": body.get("tools_called") or [],
        "render_hint": rd.get("_render_hint") if isinstance(rd, dict) else None,
        "draft_steps": rd.get("steps") if isinstance(rd, dict) else None,
        "draft_name": rd.get("name") if isinstance(rd, dict) else None,
        "readback": rd.get("readback") if isinstance(rd, dict) else None,
        "exit_readback": rd.get("exit_readback") if isinstance(rd, dict) else None,
        # Polymarket-specific fields:
        "mode": rd.get("mode") if isinstance(rd, dict) else None,
        "direction": rd.get("direction") if isinstance(rd, dict) else None,
        "resolve_on": rd.get("resolve_on") if isinstance(rd, dict) else None,
        "matched": rd.get("matched") if isinstance(rd, dict) else None,
        "threshold": rd.get("threshold") if isinstance(rd, dict) else None,
        "threshold_was_assumed": (
            rd.get("threshold_was_assumed") if isinstance(rd, dict) else None
        ),
        "threshold_presets": (
            rd.get("threshold_presets") if isinstance(rd, dict) else None
        ),
        "events_count": (
            len(rd.get("events") or []) if isinstance(rd, dict) else 0
        ),
        "latency_ms_server": body.get("latency_ms"),
    }


def _summarize_backtest(bt: dict) -> dict:
    if "_http_error" in bt or "_exception" in bt:
        return {
            "eligible": None,
            "error": bt.get("_exception") or f"HTTP {bt.get('_http_error')}",
            "body": bt.get("body"),
        }
    out = {
        "eligible": bt.get("eligible"),
        "reason": bt.get("reason"),
        "warnings": bt.get("warnings") or [],
    }
    m = bt.get("metrics") or {}
    if isinstance(m, dict):
        out["n_trades"] = m.get("n_trades")
        out["total_return_pct"] = m.get("total_return_pct")
        out["cagr_pct"] = m.get("cagr_pct")
        out["max_drawdown_pct"] = m.get("max_drawdown_pct")
        out["win_rate_pct"] = m.get("win_rate_pct")
    out["bench_buy_hold_return_pct"] = bt.get("bench_buy_hold_return_pct")
    return out


def _judge(prompt_row: dict, chat: dict, bt: dict) -> tuple[str, str]:
    """Verdict logic.

    PASS    — all expectations met. For backtestable categories, draft
              must also pass /workflows/backtest-draft (eligible=true).
    PARTIAL — chat-side expectations met but backtester rejected the
              draft (only applicable when backtest was attempted).
    FAIL    — any chat-side expectation missed, or chat errored.
    """
    expect = prompt_row.get("expect") or {}
    tools = chat.get("tools_called") or []
    render_hint = chat.get("render_hint")
    response = chat.get("response") or ""

    if chat.get("error"):
        return "FAIL", f"chat_error: {chat['error']}"

    reasons: list[str] = []
    chat_ok = True

    if "tool" in expect:
        if expect["tool"] not in tools:
            chat_ok = False
            reasons.append(f"want_tool={expect['tool']} got={tools or '∅'}")

    if "tool_any_of" in expect:
        want = set(expect["tool_any_of"])
        if not (want & set(tools)):
            chat_ok = False
            reasons.append(
                f"want_any_of={sorted(want)} got={tools or '∅'}"
            )

    if "render_hint" in expect:
        if render_hint != expect["render_hint"]:
            chat_ok = False
            reasons.append(
                f"want_hint={expect['render_hint']} got={render_hint}"
            )

    if "render_hint_any_of" in expect:
        want = set(expect["render_hint_any_of"])
        if render_hint not in want:
            chat_ok = False
            reasons.append(
                f"want_hint_any_of={sorted(str(x) for x in want)} got={render_hint}"
            )

    if expect.get("no_workflow_draft") is True:
        steps = chat.get("draft_steps")
        if isinstance(steps, list) and steps:
            chat_ok = False
            reasons.append(
                f"expected_no_draft but got {len(steps)}-step draft"
            )
        if render_hint == "workflow_draft_card":
            chat_ok = False
            reasons.append("expected_no_draft but got workflow_draft_card")

    if "min_response_chars" in expect:
        n = len(response.strip())
        if n < int(expect["min_response_chars"]):
            chat_ok = False
            reasons.append(
                f"response_too_short {n}<{expect['min_response_chars']}"
            )

    if "mode" in expect and chat.get("mode") != expect["mode"]:
        chat_ok = False
        reasons.append(f"want_mode={expect['mode']} got={chat.get('mode')}")

    if "direction" in expect and chat.get("direction") != expect["direction"]:
        chat_ok = False
        reasons.append(
            f"want_direction={expect['direction']} got={chat.get('direction')}"
        )

    if "resolve_on" in expect and chat.get("resolve_on") != expect["resolve_on"]:
        chat_ok = False
        reasons.append(
            f"want_resolve_on={expect['resolve_on']} got={chat.get('resolve_on')}"
        )

    if "threshold_was_assumed" in expect:
        if chat.get("threshold_was_assumed") != expect["threshold_was_assumed"]:
            chat_ok = False
            reasons.append(
                f"want_thr_assumed={expect['threshold_was_assumed']} "
                f"got={chat.get('threshold_was_assumed')}"
            )

    if expect.get("threshold_presets_nonempty") is True:
        ps = chat.get("threshold_presets")
        if not isinstance(ps, list) or len(ps) == 0:
            chat_ok = False
            reasons.append("threshold_presets empty")

    if not chat_ok:
        return "FAIL", "; ".join(reasons)

    # Chat OK. If backtest was attempted, fold in its verdict.
    if "eligible" in bt and bt.get("eligible") is not None:
        if bt["eligible"] is True:
            n = bt.get("n_trades")
            ret = bt.get("total_return_pct")
            return "PASS", f"chat OK + bt_ok: trades={n} ret={ret}%"
        return "PARTIAL", f"chat OK; bt_rejected: {bt.get('reason')}"
    if bt.get("error"):
        return "PARTIAL", f"chat OK; bt_error: {bt['error']}"
    return "PASS", "chat OK"


def _query_llm_usage(user_id: int, since: datetime) -> list[dict]:
    from backend.database import SessionLocal
    from sqlalchemy import text
    rows: list[dict] = []
    db = SessionLocal()
    try:
        result = db.execute(text("""
            SELECT created_at, input_tokens, output_tokens, total_tokens,
                   cost_usd, latency_ms
            FROM llm_usage
            WHERE user_id = :uid AND created_at >= :since
            ORDER BY created_at
        """), {"uid": user_id, "since": since})
        for r in result.mappings():
            rows.append(dict(r))
    finally:
        db.close()
    return rows


def _bucket_usage_to_prompts(
    prompt_rows: list[dict], usage_rows: list[dict],
) -> None:
    for pr in prompt_rows:
        start = pr["_started_at"]
        end = pr["_ended_at"]
        matched = [u for u in usage_rows if start <= u["created_at"] <= end]
        pr["tokens"] = {
            "input_tokens": sum(int(u.get("input_tokens") or 0) for u in matched),
            "output_tokens": sum(int(u.get("output_tokens") or 0) for u in matched),
            "total_tokens": sum(int(u.get("total_tokens") or 0) for u in matched),
            "cost_usd": float(sum(float(u.get("cost_usd") or 0) for u in matched)),
            "llm_calls": len(matched),
        }


def _percentile(xs: list[float], pct: float) -> float:
    if not xs:
        return 0.0
    xs = sorted(xs)
    k = (len(xs) - 1) * pct
    f, c = int(k), min(int(k) + 1, len(xs) - 1)
    if f == c:
        return xs[f]
    return xs[f] + (xs[c] - xs[f]) * (k - f)


def _fmt_steps_compact(steps: list[dict] | None) -> str:
    if not steps:
        return "—"
    bits = []
    for i, s in enumerate(steps):
        st = s.get("step_type", "?")
        cfg = s.get("config") or {}
        snippet_keys = []
        for k in ("symbol", "target_symbol", "side", "quantity", "operator",
                  "threshold", "cron", "anchor", "offset_minutes",
                  "event_description", "market_id", "side"):
            if k in cfg and cfg[k] not in (None, ""):
                v = cfg[k]
                if isinstance(v, str) and len(v) > 40:
                    v = v[:37] + "…"
                snippet_keys.append(f"{k}={v}")
        snippet = ", ".join(snippet_keys[:4]) or ""
        bits.append(f"[{i}] {st}({snippet})")
    return "  \n".join(bits)


def _markdown_report(
    label: str, prompt_rows: list[dict],
    started_at: datetime, ended_at: datetime,
    user_email: str, user_id: int,
) -> str:
    from collections import Counter, defaultdict

    n = len(prompt_rows)
    by_v: Counter[str] = Counter(pr["verdict"] for pr in prompt_rows)
    by_cat: dict[str, Counter[str]] = defaultdict(Counter)
    for pr in prompt_rows:
        by_cat[pr["category"]][pr["verdict"]] += 1

    chat_lats = [float(pr["latency_ms_wall"]) for pr in prompt_rows]
    bt_lats = [
        float(pr["backtest_latency_ms_wall"])
        for pr in prompt_rows
        if pr.get("backtest_latency_ms_wall") is not None
    ]

    total_in = sum(pr.get("tokens", {}).get("input_tokens", 0) for pr in prompt_rows)
    total_out = sum(pr.get("tokens", {}).get("output_tokens", 0) for pr in prompt_rows)
    total_tok = sum(pr.get("tokens", {}).get("total_tokens", 0) for pr in prompt_rows)
    total_cost = sum(pr.get("tokens", {}).get("cost_usd", 0.0) for pr in prompt_rows)
    total_calls = sum(pr.get("tokens", {}).get("llm_calls", 0) for pr in prompt_rows)

    n_eligible = sum(1 for pr in prompt_rows if pr.get("bt", {}).get("eligible") is True)
    n_rejected = sum(1 for pr in prompt_rows if pr.get("bt", {}).get("eligible") is False)
    n_errored = sum(1 for pr in prompt_rows if pr.get("bt", {}).get("error"))
    n_attempted = sum(
        1 for pr in prompt_rows
        if pr.get("backtest_latency_ms_wall") is not None
    )

    out: list[str] = []
    out.append(f"# 80-prompt event + multi-condition eval — `{label}`")
    out.append("")
    out.append(f"- recorded_at: `{started_at.isoformat()}` → `{ended_at.isoformat()}`")
    out.append(f"- prompts: **{n}**")
    out.append(f"- backend: `{BASE}`")
    out.append(f"- eval user: `{user_email}` (id={user_id})")
    out.append("")

    out.append("## Triad summary")
    out.append("")
    out.append("### Quality — verdict distribution")
    out.append("")
    for v in ("PASS", "PARTIAL", "FAIL"):
        pct = 100.0 * by_v.get(v, 0) / max(1, n)
        out.append(f"- **{v}**: {by_v.get(v, 0)} / {n} ({pct:.0f}%)")
    out.append("")

    out.append("### Backtest acceptance")
    out.append("")
    out.append(
        f"- attempted: **{n_attempted}** / {n} (only backtestable categories) — "
        f"eligible **{n_eligible}**, rejected **{n_rejected}**, errored **{n_errored}**"
    )
    out.append("")

    out.append("### Latency (ms)")
    out.append("")
    out.append(
        f"- **chat**: mean {sum(chat_lats)/max(1,len(chat_lats)):.0f} / "
        f"p50 {_percentile(chat_lats,0.5):.0f} / "
        f"p95 {_percentile(chat_lats,0.95):.0f}"
    )
    if bt_lats:
        out.append(
            f"- **backtest**: mean {sum(bt_lats)/max(1,len(bt_lats)):.0f} / "
            f"p50 {_percentile(bt_lats,0.5):.0f} / "
            f"p95 {_percentile(bt_lats,0.95):.0f}"
        )
    out.append("")

    out.append("### Tokens & cost")
    out.append("")
    out.append(
        f"- input: **{total_in:,}** · output: **{total_out:,}** · "
        f"total: **{total_tok:,}** · cost: **${total_cost:.4f}** "
        f"({total_calls} LLM calls)"
    )
    out.append("")

    out.append("### Verdicts by category")
    out.append("")
    out.append("| category | PASS | PARTIAL | FAIL | n |")
    out.append("|---|---:|---:|---:|---:|")
    for cat in sorted(by_cat):
        c = by_cat[cat]
        tot = c["PASS"] + c["PARTIAL"] + c["FAIL"]
        out.append(
            f"| `{cat}` | {c['PASS']} | {c['PARTIAL']} | {c['FAIL']} | {tot} |"
        )
    out.append("")

    out.append("---")
    out.append("")
    out.append("## Per-prompt detail")
    out.append("")

    # Group by category for readability
    by_cat_rows: dict[str, list[dict]] = defaultdict(list)
    for pr in prompt_rows:
        by_cat_rows[pr["category"]].append(pr)

    for cat in sorted(by_cat_rows):
        out.append(f"### `{cat}`")
        out.append("")
        for pr in by_cat_rows[cat]:
            verdict = pr["verdict"]
            badge = {
                "PASS": "✅ PASS",
                "PARTIAL": "⚠️ PARTIAL",
                "FAIL": "❌ FAIL",
            }.get(verdict, verdict)
            out.append(f"#### `{pr['id']}` — {badge}")
            out.append("")
            out.append(f"**Prompt**: {pr['prompt']}")
            out.append("")
            tokens = pr.get("tokens", {})
            out.append(
                f"- tools_called: `{pr.get('tools_called') or '∅'}`"
            )
            out.append(
                f"- render_hint: `{pr.get('render_hint')}` · "
                f"draft_steps: {len(pr.get('draft_steps') or []) if isinstance(pr.get('draft_steps'), list) else '—'}"
            )
            out.append(
                f"- chat latency: {pr['latency_ms_wall']:.0f}ms wall "
                f"({pr.get('latency_ms_server') or '?'}ms server) · "
                f"tokens in/out/total: "
                f"{tokens.get('input_tokens', 0):,}/"
                f"{tokens.get('output_tokens', 0):,}/"
                f"{tokens.get('total_tokens', 0):,} "
                f"({tokens.get('llm_calls', 0)} calls) · "
                f"cost ${tokens.get('cost_usd', 0):.4f}"
            )
            if pr.get("backtest_latency_ms_wall") is not None:
                bt = pr.get("bt") or {}
                out.append(
                    f"- backtest: eligible=`{bt.get('eligible')}` · "
                    f"trades={bt.get('n_trades')} · "
                    f"ret={bt.get('total_return_pct')}% · "
                    f"bench={bt.get('bench_buy_hold_return_pct')}% · "
                    f"latency {pr['backtest_latency_ms_wall']:.0f}ms"
                    + (f" · reason: {bt.get('reason')}" if bt.get('reason') else "")
                    + (f" · error: {bt.get('error')}" if bt.get('error') else "")
                )
            if verdict != "PASS":
                out.append(f"- verdict_reason: {pr['verdict_reason']}")
            out.append("")
            resp = (pr.get("response") or "").strip()
            if resp:
                # Trim very long responses for readability
                if len(resp) > 1400:
                    resp = resp[:1400] + " …[truncated]"
                out.append("<details><summary>Assistant reply</summary>")
                out.append("")
                out.append("```")
                out.append(resp)
                out.append("```")
                out.append("")
                out.append("</details>")
                out.append("")
            steps = pr.get("draft_steps")
            if isinstance(steps, list) and steps:
                out.append("<details><summary>Draft steps</summary>")
                out.append("")
                out.append("```")
                out.append(_fmt_steps_compact(steps))
                out.append("```")
                out.append("")
                out.append("</details>")
                out.append("")
            out.append("")
        out.append("")

    return "\n".join(out)


def run_eval(label: str, prompts_spec: str) -> Path:
    spec_label, prompts = _load_prompts(prompts_spec)
    print(f"[eval] registering fresh user…", file=sys.stderr)
    token, user_id, email = _register_user()
    print(f"[eval] user_id={user_id} email={email}", file=sys.stderr)
    print(f"[eval] running {len(prompts)} prompts (chat + selective backtest)…",
          file=sys.stderr)

    started_at = datetime.now(timezone.utc)
    prompt_rows: list[dict] = []
    for idx, p in enumerate(prompts, 1):
        cat = p.get("category") or "uncategorized"
        print(f"  [{idx:>2}/{len(prompts)}] {p['id']} ({cat})",
              file=sys.stderr, end="", flush=True)
        # Fresh conv_id per prompt — prevents Redis draft state from leaking.
        conv_id = f"s_eval_{uuid.uuid4().hex[:12]}"
        t_start = datetime.now(timezone.utc)
        body, latency_ms = _post_chat(token, p["prompt"], conv_id)
        chat_summary = _summarize_chat(body)

        bt_summary: dict = {}
        bt_latency_ms: Optional[float] = None
        steps = chat_summary.get("draft_steps")
        if (cat in _BACKTEST_CATEGORIES
                and isinstance(steps, list) and steps):
            bt_started = time.monotonic()
            bt_raw = _post_backtest(
                token, steps,
                name=chat_summary.get("draft_name") or p["id"],
            )
            bt_latency_ms = (time.monotonic() - bt_started) * 1000
            bt_summary = _summarize_backtest(bt_raw)

        t_end = datetime.now(timezone.utc)
        verdict, reason = _judge(p, chat_summary, bt_summary)
        row = {
            "id": p["id"],
            "category": cat,
            "prompt": p["prompt"],
            "tags": p.get("tags") or [],
            "expect": p.get("expect") or {},
            "conv_id": conv_id,
            "_started_at": t_start,
            "_ended_at": t_end,
            "latency_ms_wall": latency_ms,
            "backtest_latency_ms_wall": bt_latency_ms,
            "verdict": verdict,
            "verdict_reason": reason,
            **chat_summary,
            "bt": bt_summary,
        }
        prompt_rows.append(row)
        ret = (bt_summary or {}).get("total_return_pct")
        print(
            f" → {verdict} chat={int(latency_ms)}ms "
            + (f"bt_elig={bt_summary.get('eligible')} "
               f"trades={bt_summary.get('n_trades')} "
               f"ret={ret}% " if bt_summary else "")
            + f"({reason[:80]})",
            file=sys.stderr,
        )

    ended_at = datetime.now(timezone.utc)

    print(f"[eval] querying llm_usage for user_id={user_id}…", file=sys.stderr)
    usage_rows = _query_llm_usage(user_id, since=started_at)
    print(f"[eval]   → {len(usage_rows)} rows", file=sys.stderr)
    _bucket_usage_to_prompts(prompt_rows, usage_rows)

    json_rows = []
    for pr in prompt_rows:
        pr_out = {
            **pr,
            "_started_at": pr["_started_at"].isoformat(),
            "_ended_at": pr["_ended_at"].isoformat(),
        }
        json_rows.append(pr_out)

    snapshot = {
        "label": label,
        "spec_label": spec_label,
        "recorded_at": started_at.isoformat(),
        "ended_at": ended_at.isoformat(),
        "user_id": user_id, "user_email": email,
        "n_prompts": len(prompt_rows),
        "n_llm_usage_rows": len(usage_rows),
        "results": json_rows,
    }
    out_path = RESULTS_DIR / f"{label}.json"
    out_path.write_text(json.dumps(snapshot, indent=2, default=str))
    print(f"[eval] wrote {out_path}", file=sys.stderr)

    md = _markdown_report(
        label, prompt_rows, started_at, ended_at, email, user_id,
    )
    md_path = RESULTS_DIR / f"{label}.md"
    md_path.write_text(md)
    print(f"[eval] wrote {md_path}", file=sys.stderr)
    return out_path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True)
    ap.add_argument("--prompts", required=True)
    args = ap.parse_args()
    run_eval(args.label, args.prompts)
    return 0


if __name__ == "__main__":
    sys.exit(main())
