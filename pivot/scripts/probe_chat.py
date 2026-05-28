"""Manual chat probe — sends sequences of turns through live /chat with
a fixed conv_id per session. Outputs raw responses (no auto-verdict)
so I can judge each one by reading. Usage:

    .venv/bin/python scripts/probe_chat.py <session_spec.json>

session_spec.json shape:
  {
    "name": "S04_replay",
    "turns": ["Build an agent — ...", "use 20-day rolling high"]
  }

Or pass multiple session files; each gets its own conv_id.
"""
from __future__ import annotations

import json
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx


_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


BASE = "http://127.0.0.1:8000"
RESULTS_DIR = _PROJECT_ROOT / "tests" / "eval_results" / "probes"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)


def _register_user() -> tuple[str, int, str]:
    email = f"probe_{uuid.uuid4().hex[:10]}@p.com"
    r = httpx.post(
        f"{BASE}/auth/register",
        json={"email": email, "password": "password123", "full_name": "probe"},
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
                "body": r.text[:600],
            }, elapsed_ms)
        return r.json(), elapsed_ms
    except Exception as exc:  # noqa: BLE001
        elapsed_ms = (time.monotonic() - started) * 1000
        return ({"_exception": str(exc)}, elapsed_ms)


def run(spec_path: Path, token: str, user_id: int) -> dict:
    spec = json.loads(spec_path.read_text())
    sessions = spec.get("sessions") or [
        {"name": spec.get("name", spec_path.stem),
         "turns": spec.get("turns", [])}
    ]

    results: list[dict] = []
    for sess in sessions:
        conv_id = f"probe_{uuid.uuid4().hex[:10]}"
        history: list[dict] = []
        turn_rows: list[dict] = []
        print(f"\n## SESSION: {sess['name']}  conv_id={conv_id}")
        for i, turn in enumerate(sess["turns"], 1):
            prompt = turn if isinstance(turn, str) else turn.get("prompt", "")
            history.append({"role": "user", "content": prompt})
            print(f"\n--- T{i} USER: {prompt!r}")
            body, wall_ms = _post_chat(token, history, conv_id)
            if "_http_error" in body or "_exception" in body:
                print(f"--- T{i} ERR: {body}")
                turn_rows.append({
                    "prompt": prompt,
                    "error": body,
                    "wall_ms": wall_ms,
                })
                continue
            resp = body.get("response") or ""
            tools = body.get("tools_called") or []
            rd = body.get("raw_data") or {}
            hint = rd.get("_render_hint") if isinstance(rd, dict) else None
            print(f"--- T{i} TOOLS: {tools}  HINT: {hint}  WALL: {wall_ms:.0f}ms")
            print(f"--- T{i} RESPONSE: {resp[:600]}")
            if isinstance(rd, dict):
                if rd.get("steps"):
                    sk = [s.get("step_type", "?") for s in (rd.get("steps") or [])]
                    print(f"--- T{i} STEP_TYPES: {sk}")
                if rd.get("valid_until"):
                    print(f"--- T{i} VALID_UNTIL: {rd.get('valid_until')}")
                if rd.get("backtestable") is False:
                    print(f"--- T{i} BACKTEST_BLOCKERS: {rd.get('backtest_blockers')}")
            if resp:
                history.append({"role": "assistant", "content": resp})
            turn_rows.append({
                "prompt": prompt,
                "response": resp,
                "tools": tools,
                "render_hint": hint,
                "wall_ms": wall_ms,
                "raw_data": rd,
                "latency_breakdown": body.get("latency_breakdown"),
                "latency_ms_server": body.get("latency_ms"),
            })
        results.append({
            "name": sess["name"],
            "conv_id": conv_id,
            "turns": turn_rows,
        })
    return {"results": results, "user_id": user_id}


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: probe_chat.py <spec.json> [<spec.json> ...]")
        return 1
    token, user_id, email = _register_user()
    print(f"# Registered probe user: {email} (uid={user_id})")
    all_results: list[dict] = []
    for arg in sys.argv[1:]:
        p = Path(arg)
        if not p.exists():
            print(f"missing: {p}")
            continue
        out = run(p, token, user_id)
        all_results.append({"file": str(p), "data": out})
    out_path = RESULTS_DIR / f"probe_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.json"
    out_path.write_text(json.dumps(all_results, indent=2, default=str))
    print(f"\n# Wrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
