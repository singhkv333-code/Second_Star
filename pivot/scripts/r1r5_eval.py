"""R1..R5 eval — multi-turn sessions, same conv_id within each session.

Unlike event_multi_80_eval (fresh conv_id per prompt), this runner keeps
the conv_id stable across the turns of each session so we can probe
context preservation, "yes" resolution, draft amendment, drift-after-
analysis, etc. — the failure shapes R1..R5 were built to address.

Records per turn:
  - response text + tools_called + render_hint
  - latency wall-clock
  - input/output tokens (queried from llm_usage)
  - reply_class (re-classified locally from the prompt text)
  - verdict (PASS/FAIL/PARTIAL) against the structured expectations

Output:
  tests/eval_results/r1r5_50_<run_id>.json
  tests/eval_results/r1r5_50_<run_id>.md
"""
from __future__ import annotations

import json
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx


_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


BASE = "http://127.0.0.1:8000"
RESULTS_DIR = _PROJECT_ROOT / "tests" / "eval_results"
PROMPTS_DIR = _PROJECT_ROOT / "tests" / "eval_prompts"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)


def _register_user() -> tuple[str, int, str]:
    email = f"r1r5_{uuid.uuid4().hex[:10]}@p.com"
    r = httpx.post(
        f"{BASE}/auth/register",
        json={"email": email, "password": "password123", "full_name": "r1r5"},
        timeout=30,
    )
    r.raise_for_status()
    body = r.json()
    return body["access_token"], int(body["user_id"]), email


def _post_chat(
    token: str, messages: list[dict], conv_id: str,
) -> tuple[dict, float]:
    started = time.monotonic()
    try:
        r = httpx.post(
            f"{BASE}/chat",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json={
                "messages": messages,
                "conversation_id": conv_id,
                "include_portfolio_context": True,
            },
            timeout=180,
        )
        elapsed_ms = (time.monotonic() - started) * 1000
        if r.status_code != 200:
            return ({
                "_http_error": r.status_code,
                "body": r.text[:500],
            }, elapsed_ms)
        return r.json(), elapsed_ms
    except Exception as exc:  # noqa: BLE001
        elapsed_ms = (time.monotonic() - started) * 1000
        return ({"_exception": str(exc)}, elapsed_ms)


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
        "valid_until": rd.get("valid_until") if isinstance(rd, dict) else None,
        "expires_at": rd.get("expires_at") if isinstance(rd, dict) else None,
        "backtestable": rd.get("backtestable") if isinstance(rd, dict) else None,
        "backtest_blockers": (
            rd.get("backtest_blockers") if isinstance(rd, dict) else None
        ),
        "latency_ms_server": body.get("latency_ms"),
    }


def _classify_reply_class_local(message: str) -> str:
    # Local mirror — keep cheap. Just enough to surface a column in
    # the report; the actual budget is set server-side from this same
    # classifier.
    from backend.services.chat_service import (
        _classify_reply_class, _classify_intent,
    )
    return _classify_reply_class(message, _classify_intent(message))


def _verdict(
    expect: dict, chat: dict, prior_responses: list[str],
) -> tuple[str, str]:
    """Walks the structured expectations and returns (verdict, reason).

    PASS  — all expectations met.
    FAIL  — any explicit expectation missed, or chat errored.
    """
    if chat.get("error"):
        return "FAIL", f"chat_error: {chat['error']}"

    response = (chat.get("response") or "").strip()
    tools = chat.get("tools_called") or []
    render_hint = chat.get("render_hint")
    reasons: list[str] = []

    if "tool" in expect:
        if expect["tool"] not in tools:
            reasons.append(f"want_tool={expect['tool']} got={tools or '∅'}")

    if "tool_any_of" in expect:
        if not (set(expect["tool_any_of"]) & set(tools)):
            reasons.append(
                f"want_any_of={sorted(expect['tool_any_of'])} got={tools or '∅'}"
            )

    if "render_hint" in expect:
        if render_hint != expect["render_hint"]:
            reasons.append(
                f"want_hint={expect['render_hint']} got={render_hint}"
            )

    if "render_hint_any_of" in expect:
        if render_hint not in set(expect["render_hint_any_of"]):
            reasons.append(
                f"want_hint_any_of={sorted(str(x) for x in expect['render_hint_any_of'])} got={render_hint}"
            )

    if expect.get("no_workflow_draft") is True:
        if render_hint == "workflow_draft_card":
            reasons.append("expected_no_draft but got workflow_draft_card")

    if "min_response_chars" in expect:
        if len(response) < int(expect["min_response_chars"]):
            reasons.append(
                f"response_too_short {len(response)}<{expect['min_response_chars']}"
            )

    if "max_response_chars" in expect:
        if len(response) > int(expect["max_response_chars"]):
            reasons.append(
                f"response_too_long {len(response)}>{expect['max_response_chars']}"
            )

    if "no_phrase_any_of" in expect:
        for phrase in expect["no_phrase_any_of"]:
            if phrase.lower() in response.lower():
                reasons.append(f"forbidden_phrase={phrase!r}")

    if expect.get("has_valid_until"):
        rd_has_until = (
            chat.get("valid_until") is not None
            or chat.get("expires_at") is not None
        )
        if not rd_has_until:
            reasons.append("missing_valid_until_or_expires_at")

    if expect.get("no_unsolicited_ltp"):
        # rough heuristic — last sentence contains "live price" / "current price"
        tail = response[-220:].lower()
        if (
            "current live price" in tail
            or "current price:" in tail
            or "live price:" in tail
            or "₹" in tail and "price" in tail
        ):
            reasons.append("unsolicited_ltp_in_explainer")

    if expect.get("no_three_turn_loop"):
        # The forbidden phrasings from screenshot 7. If the LAST response
        # repeats the same opener as a prior one, FAIL.
        opener = response[:80].lower()
        for prev in prior_responses:
            if prev[:80].lower() == opener and opener:
                reasons.append("three_turn_loop_detected")
                break

    if expect.get("backtestable_false_ok"):
        # Just record — don't fail. The check is informational.
        pass

    if reasons:
        return "FAIL", "; ".join(reasons)
    return "PASS", "ok"


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


def _percentile(xs: list[float], pct: float) -> float:
    if not xs:
        return 0.0
    xs = sorted(xs)
    k = (len(xs) - 1) * pct
    f, c = int(k), min(int(k) + 1, len(xs) - 1)
    if f == c:
        return xs[f]
    return xs[f] + (xs[c] - xs[f]) * (k - f)


def _bucket_usage(
    turn_rows: list[dict], usage_rows: list[dict],
) -> None:
    for tr in turn_rows:
        start = tr["_started_at"]
        end = tr["_ended_at"]
        matched = [u for u in usage_rows if start <= u["created_at"] <= end]
        tr["tokens"] = {
            "input_tokens": sum(int(u.get("input_tokens") or 0) for u in matched),
            "output_tokens": sum(int(u.get("output_tokens") or 0) for u in matched),
            "total_tokens": sum(int(u.get("total_tokens") or 0) for u in matched),
            "cost_usd": float(sum(float(u.get("cost_usd") or 0) for u in matched)),
            "llm_calls": len(matched),
        }


def _fmt_steps_compact(steps: list[dict] | None) -> str:
    if not steps:
        return "—"
    bits = []
    for i, s in enumerate(steps):
        st = s.get("step_type", "?")
        cfg = s.get("config") or {}
        snippet_keys = []
        for k in ("symbol", "side", "quantity", "operator", "threshold",
                  "cron", "anchor", "offset_minutes", "indicator", "period"):
            if k in cfg and cfg[k] not in (None, ""):
                v = cfg[k]
                if isinstance(v, str) and len(v) > 40:
                    v = v[:37] + "…"
                snippet_keys.append(f"{k}={v}")
        snippet = ", ".join(snippet_keys[:4]) or ""
        bits.append(f"[{i}] {st}({snippet})")
    return " · ".join(bits)


def _markdown_report(
    label: str, sessions_data: list[dict], started_at: datetime,
    ended_at: datetime, user_email: str, user_id: int,
) -> str:
    from collections import Counter

    all_turns: list[dict] = []
    for sd in sessions_data:
        all_turns.extend(sd["turns"])
    n = len(all_turns)
    by_v = Counter(t["verdict"] for t in all_turns)
    wall_lats = [float(t["latency_ms_wall"]) for t in all_turns]
    server_lats = [
        float(t["latency_ms_server"]) for t in all_turns
        if t.get("latency_ms_server") is not None
    ]
    total_in = sum(t.get("tokens", {}).get("input_tokens", 0) for t in all_turns)
    total_out = sum(t.get("tokens", {}).get("output_tokens", 0) for t in all_turns)
    total_cost = sum(float(t.get("tokens", {}).get("cost_usd", 0) or 0) for t in all_turns)
    total_calls = sum(int(t.get("tokens", {}).get("llm_calls", 0) or 0) for t in all_turns)

    lines: list[str] = []
    lines.append(f"# {label}")
    lines.append(f"_user={user_email} (id={user_id})_  ")
    lines.append(
        f"_started={started_at.isoformat()}  ended={ended_at.isoformat()}_  "
    )
    lines.append("")
    lines.append("## Summary")
    lines.append(f"- Sessions: {len(sessions_data)}")
    lines.append(f"- Turns: {n}")
    pass_pct = (by_v.get('PASS', 0) / n * 100) if n else 0
    lines.append(f"- **PASS: {by_v.get('PASS', 0)} ({pass_pct:.0f}%)**")
    lines.append(f"- FAIL: {by_v.get('FAIL', 0)}")
    lines.append("")
    lines.append("## Triad (latency + tokens + verdict)")
    lines.append(f"- Wall latency p50 / p95: "
                 f"{_percentile(wall_lats, 0.5):.0f}ms / "
                 f"{_percentile(wall_lats, 0.95):.0f}ms")
    lines.append(f"- Server latency p50 / p95: "
                 f"{_percentile(server_lats, 0.5):.0f}ms / "
                 f"{_percentile(server_lats, 0.95):.0f}ms")
    lines.append(f"- Tokens IN/OUT: {total_in:,} / {total_out:,}  "
                 f"({total_calls} LLM calls, ${total_cost:.3f})")
    lines.append(f"- Per turn avg: "
                 f"in={total_in/n:.0f}, out={total_out/n:.0f}" if n else "")
    lines.append("")
    lines.append("## Per-session results")

    for sd in sessions_data:
        lines.append(f"### {sd['id']}")
        lines.append(f"_conv_id={sd['conv_id']}_")
        lines.append("")
        for i, t in enumerate(sd["turns"], 1):
            v = t["verdict"]
            mark = "✅" if v == "PASS" else "❌"
            lines.append(
                f"**T{i} {mark} {v}** — `{t['prompt']}`  "
            )
            lines.append(
                f"- reply_class: `{t.get('reply_class')}` · "
                f"tools: `{t.get('tools_called') or []}` · "
                f"hint: `{t.get('render_hint')}` · "
                f"wall: {t.get('latency_ms_wall', 0):.0f}ms · "
                f"in/out: {t.get('tokens', {}).get('input_tokens', 0)}/"
                f"{t.get('tokens', {}).get('output_tokens', 0)}"
            )
            if t.get("draft_steps"):
                lines.append(f"- steps: {_fmt_steps_compact(t['draft_steps'])}")
            if t.get("valid_until") or t.get("expires_at"):
                lines.append(
                    f"- valid_until={t.get('valid_until')} "
                    f"expires_at={t.get('expires_at')}"
                )
            if t.get("backtestable") is False:
                lines.append(
                    f"- backtestable=False blockers={t.get('backtest_blockers') or []}"
                )
            r = (t.get("response") or "").replace("\n", " ")
            if len(r) > 320:
                r = r[:317] + "…"
            lines.append(f"- response: {r}")
            if t["verdict"] == "FAIL":
                lines.append(f"- **fail_reason**: {t['fail_reason']}")
            lines.append("")
    return "\n".join(lines)


def main() -> int:
    label = sys.argv[1] if len(sys.argv) > 1 else "r1r5_50"
    spec_path = PROMPTS_DIR / f"{label}.json"
    spec = json.loads(spec_path.read_text())
    sessions = spec["sessions"]

    started = datetime.now(timezone.utc)
    print(f"[r1r5_eval] starting {label} ({len(sessions)} sessions, "
          f"{sum(len(s['turns']) for s in sessions)} turns)")
    print(f"[r1r5_eval] base={BASE}")

    try:
        token, user_id, email = _register_user()
    except Exception as e:
        print(f"register failed: {e}", file=sys.stderr)
        return 1
    print(f"[r1r5_eval] registered {email} (id={user_id})")

    sessions_data: list[dict] = []
    for sd_idx, session in enumerate(sessions, 1):
        conv_id = f"r1r5_{uuid.uuid4().hex[:10]}"
        history: list[dict] = []
        prior_responses: list[str] = []
        turns_out: list[dict] = []
        print(f"  session {sd_idx}/{len(sessions)}: {session['id']} "
              f"({len(session['turns'])} turns) conv_id={conv_id}")

        for i, turn in enumerate(session["turns"], 1):
            prompt = turn["prompt"]
            expect = turn.get("expect") or {}
            history.append({"role": "user", "content": prompt})
            print(f"    T{i}/{len(session['turns'])}: {prompt[:70]!r}")

            t0 = datetime.now(timezone.utc)
            body, wall_ms = _post_chat(token, history, conv_id)
            t1 = datetime.now(timezone.utc)
            chat_s = _summarize_chat(body)

            # Append assistant reply to history for the NEXT turn — but
            # only if there's a response (no error).
            if chat_s.get("response"):
                history.append(
                    {"role": "assistant", "content": chat_s["response"]}
                )

            try:
                rc = _classify_reply_class_local(prompt)
            except Exception:
                rc = None

            verdict, reason = _verdict(expect, chat_s, prior_responses)
            print(f"      → {verdict} ({reason}) wall={wall_ms:.0f}ms")
            prior_responses.append(chat_s.get("response") or "")

            turns_out.append({
                "prompt": prompt,
                "expect": expect,
                "response": chat_s.get("response"),
                "tools_called": chat_s.get("tools_called"),
                "render_hint": chat_s.get("render_hint"),
                "draft_name": chat_s.get("draft_name"),
                "draft_steps": chat_s.get("draft_steps"),
                "valid_until": chat_s.get("valid_until"),
                "expires_at": chat_s.get("expires_at"),
                "backtestable": chat_s.get("backtestable"),
                "backtest_blockers": chat_s.get("backtest_blockers"),
                "reply_class": rc,
                "latency_ms_wall": wall_ms,
                "latency_ms_server": chat_s.get("latency_ms_server"),
                "verdict": verdict,
                "fail_reason": reason,
                "_started_at": t0,
                "_ended_at": t1,
            })

        sessions_data.append({
            "id": session["id"],
            "conv_id": conv_id,
            "turns": turns_out,
        })

    ended = datetime.now(timezone.utc)

    try:
        usage_rows = _query_llm_usage(user_id, started)
        flat_turns = [t for sd in sessions_data for t in sd["turns"]]
        _bucket_usage(flat_turns, usage_rows)
    except Exception as e:
        print(f"[r1r5_eval] llm_usage bucket failed: {e}")

    run_id = uuid.uuid4().hex[:8]
    json_path = RESULTS_DIR / f"{label}_{run_id}.json"
    md_path = RESULTS_DIR / f"{label}_{run_id}.md"
    # Strip non-JSON datetimes before dumping.
    def _strip(obj):
        if isinstance(obj, dict):
            return {k: _strip(v) for k, v in obj.items() if not k.startswith("_")}
        if isinstance(obj, list):
            return [_strip(v) for v in obj]
        return obj
    json_path.write_text(json.dumps(
        {
            "label": label,
            "started_at": started.isoformat(),
            "ended_at": ended.isoformat(),
            "user_email": email,
            "user_id": user_id,
            "sessions": _strip(sessions_data),
        },
        indent=2, default=str,
    ))
    md_path.write_text(_markdown_report(
        label, sessions_data, started, ended, email, user_id,
    ))
    print(f"[r1r5_eval] wrote {json_path}")
    print(f"[r1r5_eval] wrote {md_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
