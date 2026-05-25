"""Slice-4 chat-quality harness — tokens, latency, quality triad.

Runs a prompt set against the live /chat endpoint and records the
quality triad the user requires for every eval report:
  - tokens (input + output + total, per prompt + grand total)
  - latency_ms (per prompt + p50/p95)
  - quality verdict (PASS/PARTIAL/FAIL with one-line reason, per prompt
    and per category)

Token counts come from the llm_usage table — every LLM call by the
chat service logs a row there (verified in the existing
observability surface). We pull rows by user_id (the eval registers
a fresh user → all llm_usage rows are this run's) and bucket by
timestamp into each prompt's [started_at, ended_at] window.

Quality verdict comes from the prompt's `expect` block:
  - tool: <name>             → tools_called[0] must equal
  - tool_any_of: [<names>]   → tools_called must overlap
  - render_hint: <hint>      → raw_data._render_hint must equal
  - mode: threshold|resolution
  - matched: true|false
  - direction: above|below
  - resolve_on: YES|NO|ANY
  - threshold_was_assumed: true
  - threshold_presets_nonempty: true
  - events_nonempty: true

Each expect key counted as PASS / FAIL; PARTIAL = some pass, some
fail. The verdict reason names the first failing key.

Usage:
    python3 scripts/slice4_chat_eval.py --label slice4_run_1 \\
        --prompts slice4_eval_50

Outputs:
    tests/eval_results/<label>.json       — full per-prompt JSON
    stdout: markdown triad report
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
    candidate = PROMPTS_DIR / f"{spec}.json"
    return json.loads(candidate.read_text())


def _register_user() -> tuple[str, int, str]:
    """POST /auth/register → (jwt, user_id, email)."""
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
    """Returns (response_dict, wall_latency_ms)."""
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


def _summarize_response(body: dict) -> dict:
    """Pull the per-row fields we care about out of the chat response."""
    if "_http_error" in body:
        return {"error": f"HTTP {body['_http_error']}", "body": body.get("body")}
    rd = body.get("raw_data") or {}
    return {
        "response_preview": (body.get("response") or "")[:280],
        "tools_called": body.get("tools_called") or [],
        "render_hint": rd.get("_render_hint") if isinstance(rd, dict) else None,
        # Slice-4 chat-tool card fields we care about:
        "mode": rd.get("mode") if isinstance(rd, dict) else None,
        "matched": rd.get("matched") if isinstance(rd, dict) else None,
        "direction": rd.get("direction") if isinstance(rd, dict) else None,
        "resolve_on": rd.get("resolve_on") if isinstance(rd, dict) else None,
        "market_id": (rd.get("market_id") or rd.get("best_guess_market_id"))
                     if isinstance(rd, dict) else None,
        "token_id": (rd.get("token_id") or rd.get("best_guess_token_id"))
                    if isinstance(rd, dict) else None,
        "threshold": rd.get("threshold") if isinstance(rd, dict) else None,
        "threshold_was_assumed": rd.get("threshold_was_assumed")
                                 if isinstance(rd, dict) else None,
        "threshold_presets": rd.get("threshold_presets")
                             if isinstance(rd, dict) else None,
        "current_yes_price": rd.get("current_yes_price")
                             if isinstance(rd, dict) else None,
        "events_count": (len(rd.get("events", []))
                         if isinstance(rd, dict) and isinstance(rd.get("events"), list)
                         else None),
        "latency_ms_server": body.get("latency_ms"),
    }


def _judge(prompt_row: dict, summary: dict) -> tuple[str, str, list[str]]:
    """Heuristic verdict from the prompt's expect block.

    Returns (verdict, one_line_reason, list_of_passed_check_names).
    verdict ∈ {PASS, PARTIAL, FAIL}.
    """
    expect = prompt_row.get("expect") or {}
    if not expect:
        return "PASS", "no expectations declared", []

    if summary.get("error"):
        return "FAIL", summary["error"], []

    checks: list[tuple[str, bool, str]] = []
    tools = summary.get("tools_called") or []

    if "tool" in expect:
        want = expect["tool"]
        ok = want in tools
        checks.append(("tool", ok, f"want={want} got={tools}"))
    if "tool_any_of" in expect:
        want = set(expect["tool_any_of"])
        ok = bool(want & set(tools))
        checks.append(("tool_any_of", ok, f"want_any_of={sorted(want)} got={tools}"))
    if "render_hint" in expect:
        want = expect["render_hint"]
        ok = summary.get("render_hint") == want
        checks.append(("render_hint", ok, f"want={want} got={summary.get('render_hint')}"))
    if "mode" in expect:
        want = expect["mode"]
        ok = summary.get("mode") == want
        checks.append(("mode", ok, f"want={want} got={summary.get('mode')}"))
    if "matched" in expect:
        want = expect["matched"]
        ok = summary.get("matched") == want
        checks.append(("matched", ok, f"want={want} got={summary.get('matched')}"))
    if "direction" in expect:
        want = expect["direction"]
        ok = summary.get("direction") == want
        checks.append(("direction", ok, f"want={want} got={summary.get('direction')}"))
    if "resolve_on" in expect:
        want = expect["resolve_on"]
        ok = summary.get("resolve_on") == want
        checks.append(("resolve_on", ok, f"want={want} got={summary.get('resolve_on')}"))
    if "threshold_was_assumed" in expect:
        want = expect["threshold_was_assumed"]
        ok = summary.get("threshold_was_assumed") == want
        checks.append(("threshold_was_assumed", ok,
                       f"want={want} got={summary.get('threshold_was_assumed')}"))
    if "threshold_presets_nonempty" in expect:
        presets = summary.get("threshold_presets") or []
        ok = len(presets) > 0
        checks.append(("threshold_presets_nonempty", ok,
                       f"presets_len={len(presets)}"))
    if "events_nonempty" in expect:
        ok = bool(summary.get("events_count"))
        checks.append(("events_nonempty", ok,
                       f"events_count={summary.get('events_count')}"))

    if not checks:
        return "PASS", "no checks resolved", []
    n_pass = sum(1 for _, ok, _ in checks if ok)
    n_total = len(checks)
    passed_names = [n for n, ok, _ in checks if ok]
    if n_pass == n_total:
        return "PASS", f"{n_pass}/{n_total} checks", passed_names
    if n_pass == 0:
        first_fail = next((d for _, ok, d in checks if not ok), "")
        return "FAIL", f"0/{n_total}; first_fail: {first_fail}", passed_names
    first_fail = next((d for n, ok, d in checks if not ok), "")
    return "PARTIAL", f"{n_pass}/{n_total}; first_fail: {first_fail}", passed_names


def _query_llm_usage(user_id: int, since: datetime) -> list[dict]:
    """Query llm_usage rows for the eval user since `since`. Returns
    a list of dicts with the fields we need to bucket per prompt.

    Opens its own SQLAlchemy session — the eval runs against the
    deployed backend on a separate process, but the DB is shared.
    """
    from backend.database import SessionLocal
    from sqlalchemy import text
    rows: list[dict] = []
    db = SessionLocal()
    try:
        # llm_usage columns: id, user_id, conversation_id, request_id,
        # endpoint, provider, model, input_tokens, cached_input_tokens,
        # output_tokens, reasoning_tokens, total_tokens, cost_usd,
        # latency_ms, created_at.
        result = db.execute(text("""
            SELECT created_at, input_tokens, output_tokens, total_tokens,
                   cost_usd, latency_ms, provider, model
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
    """In-place: for each prompt_row, sum usage rows whose created_at
    falls within [started_at, ended_at] window."""
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


def _percentile(sorted_xs: list[float], pct: float) -> float:
    if not sorted_xs:
        return 0.0
    k = (len(sorted_xs) - 1) * pct
    f, c = int(k), min(int(k) + 1, len(sorted_xs) - 1)
    if f == c:
        return sorted_xs[f]
    return sorted_xs[f] + (sorted_xs[c] - sorted_xs[f]) * (k - f)


def _markdown_report(
    label: str, prompt_rows: list[dict], started_at: datetime, ended_at: datetime,
) -> str:
    n = len(prompt_rows)
    # Verdict distribution
    by_v: dict[str, int] = {"PASS": 0, "PARTIAL": 0, "FAIL": 0}
    for pr in prompt_rows:
        by_v[pr["verdict"]] = by_v.get(pr["verdict"], 0) + 1

    # Latency stats
    lats = sorted(float(pr["latency_ms_wall"]) for pr in prompt_rows)
    p50 = _percentile(lats, 0.50)
    p95 = _percentile(lats, 0.95)
    mean_lat = sum(lats) / len(lats) if lats else 0

    # Token totals
    total_in = sum(pr.get("tokens", {}).get("input_tokens", 0) for pr in prompt_rows)
    total_out = sum(pr.get("tokens", {}).get("output_tokens", 0) for pr in prompt_rows)
    total_tok = sum(pr.get("tokens", {}).get("total_tokens", 0) for pr in prompt_rows)
    total_cost = sum(pr.get("tokens", {}).get("cost_usd", 0.0) for pr in prompt_rows)
    total_calls = sum(pr.get("tokens", {}).get("llm_calls", 0) for pr in prompt_rows)

    # Per-category
    cat_counts: dict[str, dict[str, int]] = {}
    for pr in prompt_rows:
        primary = (pr.get("tags") or ["uncategorized"])[0]
        cat_counts.setdefault(primary, {"PASS": 0, "PARTIAL": 0, "FAIL": 0})
        cat_counts[primary][pr["verdict"]] += 1

    out: list[str] = []
    out.append(f"# Slice-4 chat eval — {label}\n")
    out.append(f"- recorded_at: {started_at.isoformat()} → {ended_at.isoformat()}")
    out.append(f"- prompts: {n}")
    out.append(f"- backend: {BASE}\n")
    out.append("## Triad summary\n")
    out.append("**Quality** — verdict distribution:")
    for v in ("PASS", "PARTIAL", "FAIL"):
        pct = 100.0 * by_v[v] / max(1, n)
        out.append(f"  - {v}: {by_v[v]} / {n} ({pct:.0f}%)")
    out.append("")
    out.append("**Latency** (wall-clock per prompt, ms):")
    out.append(f"  - mean: {mean_lat:.0f}")
    out.append(f"  - p50:  {p50:.0f}")
    out.append(f"  - p95:  {p95:.0f}")
    out.append("")
    out.append("**Tokens** (sum across all LLM calls in window):")
    out.append(f"  - input:  {total_in:,}")
    out.append(f"  - output: {total_out:,}")
    out.append(f"  - total:  {total_tok:,}")
    out.append(f"  - calls:  {total_calls}")
    out.append(f"  - cost_usd (Azure-recorded): ${total_cost:.4f}")
    out.append("")

    out.append("## By category (verdict counts)\n")
    out.append("| category | PASS | PARTIAL | FAIL |")
    out.append("|---|---|---|---|")
    for cat in sorted(cat_counts.keys()):
        c = cat_counts[cat]
        out.append(f"| {cat} | {c['PASS']} | {c['PARTIAL']} | {c['FAIL']} |")
    out.append("")

    out.append("## Per-prompt detail\n")
    out.append("| id | verdict | tools | hint | tok(in/out) | wall(ms) | reason |")
    out.append("|---|---|---|---|---|---|---|")
    for pr in prompt_rows:
        tks = pr.get("tokens", {})
        tk_str = f"{tks.get('input_tokens', 0):,}/{tks.get('output_tokens', 0):,}"
        tools_str = ",".join(pr.get("tools_called") or [])[:40] or "—"
        hint = pr.get("render_hint") or "—"
        reason = pr["verdict_reason"][:80]
        out.append(
            f"| {pr['id']} | {pr['verdict']} | {tools_str} | {hint} | "
            f"{tk_str} | {int(pr['latency_ms_wall']):,} | {reason} |"
        )
    out.append("")
    return "\n".join(out)


def run_eval(label: str, prompts_spec: str) -> Path:
    prompts = _load_prompts(prompts_spec)
    print(f"[eval] registering fresh user…", file=sys.stderr)
    token, user_id, email = _register_user()
    print(f"[eval] user_id={user_id} email={email}", file=sys.stderr)
    print(f"[eval] running {len(prompts)} prompts against {BASE}…", file=sys.stderr)

    started_at = datetime.now(timezone.utc)
    prompt_rows: list[dict] = []
    for idx, p in enumerate(prompts, 1):
        print(f"  [{idx:>2}/{len(prompts)}] {p['id']}", file=sys.stderr, end="", flush=True)
        t_start = datetime.now(timezone.utc)
        body, latency_ms = _post_chat(token, p["prompt"])
        t_end = datetime.now(timezone.utc)
        summary = _summarize_response(body)
        verdict, reason, passed = _judge(p, summary)
        row = {
            "id": p["id"],
            "prompt": p["prompt"],
            "tags": p.get("tags") or [],
            "expect": p.get("expect") or {},
            "_started_at": t_start,
            "_ended_at": t_end,
            "latency_ms_wall": latency_ms,
            "verdict": verdict,
            "verdict_reason": reason,
            "passed_checks": passed,
            **summary,
        }
        prompt_rows.append(row)
        print(f" → {verdict} ({int(latency_ms)}ms) {reason[:60]}", file=sys.stderr)
    ended_at = datetime.now(timezone.utc)

    print(f"[eval] querying llm_usage for user_id={user_id}…", file=sys.stderr)
    usage_rows = _query_llm_usage(user_id, since=started_at)
    print(f"[eval]   → {len(usage_rows)} rows", file=sys.stderr)
    _bucket_usage_to_prompts(prompt_rows, usage_rows)

    # Strip datetime objects before JSON serialize (use ISO strings).
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

    # Markdown report on stdout.
    md = _markdown_report(label, prompt_rows, started_at, ended_at)
    md_path = RESULTS_DIR / f"{label}.md"
    md_path.write_text(md)
    print(f"[eval] wrote {md_path}", file=sys.stderr)
    print(md)
    return out_path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True)
    ap.add_argument("--prompts", required=True,
                    help="prompt set: JSON path OR bare name resolved to "
                         "tests/eval_prompts/<name>.json")
    args = ap.parse_args()
    run_eval(args.label, args.prompts)
    return 0


if __name__ == "__main__":
    sys.exit(main())
