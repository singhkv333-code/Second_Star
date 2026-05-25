"""Entry+Exit eval — runs each prompt through /chat and then backtests
the resulting draft via /workflows/backtest-draft.

Output:
  tests/eval_results/<label>.json — per-prompt: chat result + backtest result
  tests/eval_results/<label>.md   — markdown summary
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any, Optional

import httpx

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


BASE = "http://127.0.0.1:8000"
RESULTS_DIR = _PROJECT_ROOT / "tests" / "eval_results"
PROMPTS_DIR = _PROJECT_ROOT / "tests" / "eval_prompts"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)


def _load_prompts(spec: str) -> list[dict]:
    p = Path(spec)
    if p.suffix == ".json" and p.exists():
        return json.loads(p.read_text())
    return json.loads((PROMPTS_DIR / f"{spec}.json").read_text())


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


def _post_chat(token: str, prompt: str) -> tuple[dict, float]:
    started = time.monotonic()
    r = httpx.post(
        f"{BASE}/chat",
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"},
        json={"messages": [{"role": "user", "content": prompt}]},
        timeout=120,
    )
    elapsed_ms = (time.monotonic() - started) * 1000
    if r.status_code != 200:
        return {"_http_error": r.status_code, "body": r.text[:300]}, elapsed_ms
    return r.json(), elapsed_ms


def _post_backtest(token: str, steps: list[dict], name: str) -> dict:
    """POST /workflows/backtest-draft with the draft's steps[]. Returns
    the parsed response — eligible/reason/metrics."""
    r = httpx.post(
        f"{BASE}/api/workflows/backtest-draft",
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"},
        json={"name": name, "steps": steps, "period": "2y"},
        timeout=180,
    )
    if r.status_code != 200:
        return {
            "_http_error": r.status_code,
            "body": r.text[:300],
        }
    return r.json()


def _summarize_chat(body: dict) -> dict:
    if "_http_error" in body:
        return {"error": f"HTTP {body['_http_error']}", "body": body.get("body")}
    rd = body.get("raw_data") or {}
    return {
        "response_preview": (body.get("response") or "")[:240],
        "tools_called": body.get("tools_called") or [],
        "render_hint": rd.get("_render_hint") if isinstance(rd, dict) else None,
        "draft_steps": rd.get("steps") if isinstance(rd, dict) else None,
        "draft_name": rd.get("name") if isinstance(rd, dict) else None,
        "readback": rd.get("readback") if isinstance(rd, dict) else None,
        "exit_readback": rd.get("exit_readback") if isinstance(rd, dict) else None,
    }


def _summarize_backtest(bt: dict) -> dict:
    """Pull the headline numbers from /workflows/backtest-draft."""
    if "_http_error" in bt:
        return {
            "eligible": None,
            "error": f"HTTP {bt['_http_error']}",
            "body": bt.get("body"),
        }
    out = {
        "eligible": bt.get("eligible"),
        "reason": bt.get("reason"),
        "warnings": bt.get("warnings") or [],
    }
    metrics = bt.get("metrics") or {}
    if isinstance(metrics, dict):
        out["n_trades"] = metrics.get("n_trades")
        out["total_return_pct"] = metrics.get("total_return_pct")
        out["cagr_pct"] = metrics.get("cagr_pct")
        out["max_drawdown_pct"] = metrics.get("max_drawdown_pct")
        out["win_rate_pct"] = metrics.get("win_rate_pct")
    out["bench_buy_hold_return_pct"] = bt.get("bench_buy_hold_return_pct")
    return out


def _judge(prompt_row: dict, chat_summary: dict, bt_summary: dict) -> tuple[str, str]:
    """Verdict folds in BOTH chat correctness AND backtest eligibility.

    PASS    — chat fired the expected tool AND rendered a draft AND
              the backtester accepted it (eligible=True, no error).
    PARTIAL — chat fired correctly but the backtester rejected it,
              OR chat was PASS-ish but the draft is missing.
    FAIL    — chat fired wrong tool, errored, or no draft."""
    expect = prompt_row.get("expect") or {}
    tools = chat_summary.get("tools_called") or []
    render_hint = chat_summary.get("render_hint")

    chat_ok = True
    chat_reason = []
    if "tool" in expect:
        if expect["tool"] not in tools:
            chat_ok = False
            chat_reason.append(f"want_tool={expect['tool']} got={tools}")
    if "tool_any_of" in expect:
        want = set(expect["tool_any_of"])
        if not (want & set(tools)):
            chat_ok = False
            chat_reason.append(
                f"want_any_of={sorted(want)} got={tools}"
            )
    if "render_hint" in expect:
        if render_hint != expect["render_hint"]:
            chat_ok = False
            chat_reason.append(
                f"want_hint={expect['render_hint']} got={render_hint}"
            )

    if not chat_ok:
        return "FAIL", "; ".join(chat_reason)

    bt_eligible = bt_summary.get("eligible")
    if bt_eligible is True:
        n = bt_summary.get("n_trades")
        ret = bt_summary.get("total_return_pct")
        return "PASS", f"chat OK + bt_ok: trades={n} ret={ret}%"
    if bt_eligible is False:
        return "PARTIAL", f"chat OK; bt_rejected: {bt_summary.get('reason')}"
    if bt_summary.get("error"):
        return "PARTIAL", f"chat OK; bt_error: {bt_summary['error']}"
    return "PARTIAL", "chat OK; backtest result unclear"


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
        matched = [
            u for u in usage_rows
            if start <= u["created_at"] <= end
        ]
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


def _markdown_report(
    label: str, prompt_rows: list[dict], started_at: datetime, ended_at: datetime,
) -> str:
    n = len(prompt_rows)
    by_v: dict[str, int] = {"PASS": 0, "PARTIAL": 0, "FAIL": 0}
    for pr in prompt_rows:
        by_v[pr["verdict"]] = by_v.get(pr["verdict"], 0) + 1

    chat_lats = sorted(float(pr["latency_ms_wall"]) for pr in prompt_rows)
    bt_lats = sorted(
        float(pr.get("backtest_latency_ms_wall") or 0)
        for pr in prompt_rows
        if pr.get("backtest_latency_ms_wall") is not None
    )

    total_in = sum(pr.get("tokens", {}).get("input_tokens", 0) for pr in prompt_rows)
    total_out = sum(pr.get("tokens", {}).get("output_tokens", 0) for pr in prompt_rows)
    total_tok = sum(pr.get("tokens", {}).get("total_tokens", 0) for pr in prompt_rows)
    total_cost = sum(pr.get("tokens", {}).get("cost_usd", 0.0) for pr in prompt_rows)
    total_calls = sum(pr.get("tokens", {}).get("llm_calls", 0) for pr in prompt_rows)

    # Backtest summary
    n_eligible = sum(1 for pr in prompt_rows if pr.get("bt", {}).get("eligible") is True)
    n_rejected = sum(1 for pr in prompt_rows if pr.get("bt", {}).get("eligible") is False)
    n_errored = sum(1 for pr in prompt_rows if pr.get("bt", {}).get("error"))

    out: list[str] = []
    out.append(f"# Entry+Exit chat-and-backtest eval — {label}\n")
    out.append(f"- recorded_at: {started_at.isoformat()} → {ended_at.isoformat()}")
    out.append(f"- prompts: {n}")
    out.append(f"- backend: {BASE}\n")

    out.append("## Triad summary\n")
    out.append("**Quality** — verdict distribution (chat AND backtest):")
    for v in ("PASS", "PARTIAL", "FAIL"):
        pct = 100.0 * by_v[v] / max(1, n)
        out.append(f"  - {v}: {by_v[v]} / {n} ({pct:.0f}%)")
    out.append("")
    out.append(f"**Backtest acceptance** — {n_eligible}/{n} drafts eligible, "
               f"{n_rejected} rejected, {n_errored} errored.")
    out.append("")
    out.append("**Latency (ms)** — chat / backtest:")
    out.append(f"  - chat mean: {sum(chat_lats)/max(1,len(chat_lats)):.0f}, "
               f"p50: {_percentile(chat_lats,0.5):.0f}, "
               f"p95: {_percentile(chat_lats,0.95):.0f}")
    out.append(f"  - backtest mean: {sum(bt_lats)/max(1,len(bt_lats)):.0f}, "
               f"p50: {_percentile(bt_lats,0.5):.0f}, "
               f"p95: {_percentile(bt_lats,0.95):.0f}")
    out.append("")
    out.append(f"**Tokens** — input {total_in:,} / output {total_out:,} / "
               f"total {total_tok:,} ({total_calls} calls). cost ${total_cost:.4f}")
    out.append("")

    out.append("## Per-prompt detail\n")
    out.append("| id | verdict | tool | steps | bt eligible | trades | ret % | bench % | reason |")
    out.append("|---|---|---|---|---|---|---|---|---|")
    for pr in prompt_rows:
        bt = pr.get("bt") or {}
        tools = ",".join(pr.get("tools_called") or [])[:32] or "—"
        steps = pr.get("draft_steps") or []
        n_steps = len(steps) if isinstance(steps, list) else "—"
        elig = bt.get("eligible")
        elig_s = "✓" if elig is True else "✗" if elig is False else "—"
        trades = bt.get("n_trades")
        ret = bt.get("total_return_pct")
        bench = bt.get("bench_buy_hold_return_pct")
        reason = pr["verdict_reason"][:90]
        out.append(
            f"| {pr['id']} | {pr['verdict']} | {tools} | {n_steps} | {elig_s} | "
            f"{trades if trades is not None else '—'} | "
            f"{ret if ret is not None else '—'} | "
            f"{bench if bench is not None else '—'} | {reason} |"
        )
    out.append("")
    return "\n".join(out)


def run_eval(label: str, prompts_spec: str) -> Path:
    prompts = _load_prompts(prompts_spec)
    print(f"[eval] registering fresh user…", file=sys.stderr)
    token, user_id, email = _register_user()
    print(f"[eval] user_id={user_id} email={email}", file=sys.stderr)
    print(f"[eval] running {len(prompts)} prompts (chat + backtest)…", file=sys.stderr)

    started_at = datetime.now(timezone.utc)
    prompt_rows: list[dict] = []
    for idx, p in enumerate(prompts, 1):
        print(f"  [{idx:>2}/{len(prompts)}] {p['id']}", file=sys.stderr, end="", flush=True)
        t_start = datetime.now(timezone.utc)
        body, latency_ms = _post_chat(token, p["prompt"])
        chat_summary = _summarize_chat(body)

        # Backtest pass — only if we got a draft with steps[].
        bt_summary: dict = {"eligible": None}
        bt_latency_ms: Optional[float] = None
        steps = chat_summary.get("draft_steps")
        if isinstance(steps, list) and steps:
            bt_started = time.monotonic()
            bt_raw = _post_backtest(
                token, steps, name=chat_summary.get("draft_name") or p["id"],
            )
            bt_latency_ms = (time.monotonic() - bt_started) * 1000
            bt_summary = _summarize_backtest(bt_raw)

        t_end = datetime.now(timezone.utc)
        verdict, reason = _judge(p, chat_summary, bt_summary)
        row = {
            "id": p["id"],
            "prompt": p["prompt"],
            "tags": p.get("tags") or [],
            "expect": p.get("expect") or {},
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
        print(
            f" → {verdict} chat={int(latency_ms)}ms "
            f"bt_elig={bt_summary.get('eligible')} "
            f"trades={bt_summary.get('n_trades')} "
            f"ret={bt_summary.get('total_return_pct')}%",
            file=sys.stderr,
        )
    ended_at = datetime.now(timezone.utc)

    print(f"[eval] querying llm_usage for user_id={user_id}…", file=sys.stderr)
    usage_rows = _query_llm_usage(user_id, since=started_at)
    print(f"[eval]   → {len(usage_rows)} rows", file=sys.stderr)
    _bucket_usage_to_prompts(prompt_rows, usage_rows)

    json_rows = []
    for pr in prompt_rows:
        pr_out = {**pr,
                  "_started_at": pr["_started_at"].isoformat(),
                  "_ended_at": pr["_ended_at"].isoformat()}
        json_rows.append(pr_out)

    snapshot = {
        "label": label,
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

    md = _markdown_report(label, prompt_rows, started_at, ended_at)
    md_path = RESULTS_DIR / f"{label}.md"
    md_path.write_text(md)
    print(f"[eval] wrote {md_path}", file=sys.stderr)
    print(md)
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
