"""Chat-quality regression harness.

Runs a curated prompt set against the live /chat endpoint, records what
the bot returned, and writes a JSON snapshot. We compare snapshots
across refactors (token-opt baseline → after-stripping → after-subset)
to spot tool-selection / response-quality regressions before they
poison the demo.

Usage:
    # 1. Backend must be running on http://127.0.0.1:8000
    # 2. Run with a label naming this snapshot
    .venv/bin/python scripts/eval_chat_quality.py --label baseline
    # ... apply token-opt change ...
    .venv/bin/python scripts/eval_chat_quality.py --label after_stripping
    # ... compare ...
    .venv/bin/python scripts/eval_chat_quality.py --diff baseline after_stripping

Each prompt records:
    - response text (first 300 chars)
    - tools_called  (list of tool names)
    - render_hint   (logic_card / workflow_draft_card / indicator_backtest_chart / financial_backtest_chart / None)
    - logiccard_type (when render_hint == "logic_card")
    - latency_ms
    - error          (if the call failed entirely)
    - is_fallback    (true if response matches the LLM-unavailable fallback)

The snapshot lives in tests/eval_results/<label>.json so we can diff
across branches and revert with confidence.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

import httpx


BASE = "http://127.0.0.1:8000"
RESULTS_DIR = Path(__file__).resolve().parent.parent / "tests" / "eval_results"
PROMPTS_DIR = Path(__file__).resolve().parent.parent / "tests" / "eval_prompts"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)
PROMPTS_DIR.mkdir(parents=True, exist_ok=True)


# Curated prompt set — covers every render hint + every TOOL_SUBSET +
# the slash-command short-circuits + the phrase-ticker shortcut + a
# few free-text questions. Each `expect` is a soft hint of what we
# hope to see; the diff highlights drift.

PROMPTS: list[dict] = [
    # --- Order intents (LogicCard tools) ---
    {
        "id": "order_market_buy",
        "prompt": "Buy 10 RELIANCE at market",
        "expect": {"render_hint": "logic_card", "logiccard_type": "market_order"},
    },
    {
        "id": "order_limit_buy",
        "prompt": "Buy 5 INFY at limit price 1400",
        "expect": {"render_hint": "logic_card", "logiccard_type": "limit_order"},
    },
    {
        "id": "order_gtt",
        "prompt": "Set a GTT to buy 3 HDFCBANK if it drops to 1480",
        "expect": {"render_hint": "logic_card", "logiccard_type": "gtt_order"},
    },
    {
        "id": "order_market_sell",
        "prompt": "Sell 12 WIPRO at market",
        "expect": {"render_hint": "logic_card", "logiccard_type": "market_order"},
    },

    # --- Workflow propose ---
    {
        "id": "workflow_propose_5step",
        "prompt": "Every weekday at 3:55 PM IST, if my buying power is over ₹50,000, buy 10 shares of RELIANCE and notify me by email.",
        "expect": {"render_hint": "workflow_draft_card", "tools_called": ["propose_workflow"]},
    },
    {
        "id": "workflow_propose_3step",
        "prompt": "Every Monday morning, buy 5 INFY",
        "expect": {"render_hint": "workflow_draft_card", "tools_called": ["propose_workflow"]},
    },

    # --- Indicator backtest (NL routing → no LLM) ---
    {
        "id": "indicator_backtest_rsi",
        "prompt": "backtest RELIANCE buying when RSI drops below 30 from 2023-01-01 to 2024-12-31",
        "expect": {"render_hint": "indicator_backtest_chart"},
    },
    {
        "id": "indicator_backtest_sma",
        "prompt": "backtest INFY golden cross 50 200",
        "expect": {"render_hint": "indicator_backtest_chart"},
    },

    # --- Financial backtest (NL routing → no LLM) ---
    {
        "id": "financial_backtest_pe",
        "prompt": "backtest pe_ratio < 15 from 2020-01-01 to 2022-12-31 quarterly",
        "expect": {"render_hint": "financial_backtest_chart"},
    },

    # --- Slash commands (no LLM) ---
    {
        "id": "slash_screen",
        "prompt": "/screen roe > 18",
        "expect": {"intent_or_render": "screen"},
    },

    # --- Portfolio query (LLM, tool: get_portfolio_summary / get_holdings) ---
    {
        "id": "portfolio_summary",
        "prompt": "What's in my portfolio?",
        "expect": {"tools_called_any_of": ["get_portfolio_summary", "get_holdings"]},
    },

    # --- Market queries (LLM, tool: get_live_price / get_market_status) ---
    {
        "id": "market_price",
        "prompt": "What's the current price of RELIANCE?",
        "expect": {"tools_called_any_of": ["get_live_price"]},
    },
    {
        "id": "market_status",
        "prompt": "Is the market open right now?",
        "expect": {"tools_called_any_of": ["get_market_status"]},
    },

    # --- Calculations ---
    {
        "id": "calc_qty",
        "prompt": "How many shares of TCS can I buy with ₹50,000?",
        "expect": {"tools_called_any_of": ["calculate_order_qty"]},
    },

    # --- SIP create ---
    {
        "id": "sip_create",
        "prompt": "Set up a monthly SIP of ₹5000 in INFY on the 1st",
        "expect": {"tools_called_any_of": ["create_sip"]},
    },

    # --- Free text (no tool expected) ---
    {
        "id": "free_text_what_can_you_do",
        "prompt": "What can you do?",
        "expect": {"tools_called": []},
    },
    {
        "id": "free_text_explain_sip",
        "prompt": "Briefly explain what a SIP is.",
        "expect": {"tools_called": []},
    },
    {
        "id": "free_text_greeting",
        "prompt": "Hello",
        "expect": {"tools_called": []},
    },
]


_LLM_UNAVAILABLE_PREFIX = "The AI backend is temporarily unavailable"


def register_demo_user() -> tuple[str, str]:
    email = f"eval_{uuid.uuid4().hex[:8]}@example.com"
    r = httpx.post(
        f"{BASE}/auth/register",
        json={"email": email, "password": "password123", "full_name": "Eval"},
        timeout=10,
    )
    r.raise_for_status()
    return r.json()["access_token"], email


def post_chat(token: str, prompt: str, history: list[dict]) -> dict:
    """Single-turn chat call. Carries any prior history so multi-turn
    behaviours (memory, follow-ups) can be added later if needed."""
    started = time.monotonic()
    try:
        r = httpx.post(
            f"{BASE}/chat",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "messages": [*history, {"role": "user", "content": prompt}],
                "include_portfolio_context": True,
            },
            timeout=60,
        )
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}", "latency_ms": int((time.monotonic() - started) * 1000)}

    latency = int((time.monotonic() - started) * 1000)
    if r.status_code != 200:
        return {
            "error": f"HTTP {r.status_code}",
            "body_preview": r.text[:200],
            "latency_ms": latency,
        }
    body = r.json()
    response_text = body.get("response", "") or ""
    return {
        "response_preview": response_text[:300],
        "response_full_len": len(response_text),
        "tools_called": body.get("tools_called") or [],
        "render_hint": (body.get("raw_data") or {}).get("_render_hint")
                       if isinstance(body.get("raw_data"), dict) else None,
        "logiccard_type": (body.get("logiccard") or {}).get("type")
                          if isinstance(body.get("logiccard"), dict) else None,
        "intent": body.get("intent"),
        "latency_ms": body.get("latency_ms") or latency,
        "is_fallback": response_text.startswith(_LLM_UNAVAILABLE_PREFIX),
    }


def load_prompts(spec: str | None) -> tuple[list[dict], str]:
    """Resolve a --prompts spec to a prompt list + a source description.

    - None         → built-in PROMPTS (back-compat default).
    - foo.json     → load that path (absolute or cwd-relative).
    - bare "foo"   → load pivot/tests/eval_prompts/foo.json.
    """
    if spec is None:
        return PROMPTS, "<built-in>"
    p = Path(spec)
    if p.suffix == ".json" and p.exists():
        return json.loads(p.read_text()), str(p)
    candidate = PROMPTS_DIR / f"{spec}.json"
    if candidate.exists():
        return json.loads(candidate.read_text()), str(candidate)
    raise FileNotFoundError(
        f"prompt set not found: {spec!r}. "
        f"Tried path {p} and {candidate}."
    )


def run_eval(label: str, prompts_spec: str | None = None) -> Path:
    prompts, source = load_prompts(prompts_spec)
    print(f"[eval] registering fresh user…", file=sys.stderr)
    token, email = register_demo_user()
    print(f"[eval] running {len(prompts)} prompts from {source}…", file=sys.stderr)
    rows: list[dict] = []
    for idx, p in enumerate(prompts, 1):
        print(f"  [{idx:>2}/{len(prompts)}] {p['id']}", file=sys.stderr)
        result = post_chat(token, p["prompt"], history=[])
        rows.append({
            "id": p["id"],
            "prompt": p["prompt"],
            "tags": p.get("tags") or [],
            "expect": p.get("expect", {}),
            "actual": result,
        })
    out = RESULTS_DIR / f"{label}.json"
    out.write_text(json.dumps({
        "label": label,
        "recorded_at": datetime.utcnow().isoformat() + "Z",
        "user_email": email,
        "prompts_source": source,
        "n_prompts": len(rows),
        "results": rows,
    }, indent=2))
    print(f"\n[eval] wrote {out}", file=sys.stderr)
    return out


def diff_snapshots(label_a: str, label_b: str) -> int:
    """Stable, human-readable diff. Highlights only what *changed*.

    Returns a non-zero exit code if any prompt regressed: a render
    hint or tool changed, or a non-fallback turned into a fallback.
    """
    a = json.loads((RESULTS_DIR / f"{label_a}.json").read_text())
    b = json.loads((RESULTS_DIR / f"{label_b}.json").read_text())
    by_id_a = {r["id"]: r for r in a["results"]}
    by_id_b = {r["id"]: r for r in b["results"]}

    print(f"\n=== diff: {label_a} → {label_b} ===\n")
    regressions = 0
    drifted = 0

    def actual_summary(r: dict) -> dict:
        ac = r["actual"]
        return {
            "render_hint": ac.get("render_hint"),
            "logiccard_type": ac.get("logiccard_type"),
            "tools_called": ac.get("tools_called"),
            "is_fallback": ac.get("is_fallback"),
            "error": ac.get("error"),
        }

    for pid in sorted(set(by_id_a) | set(by_id_b)):
        ra, rb = by_id_a.get(pid), by_id_b.get(pid)
        if ra is None or rb is None:
            print(f"  [PROMPT MISSING] {pid}: only in {'a' if rb is None else 'b'}")
            drifted += 1
            continue
        sa, sb = actual_summary(ra), actual_summary(rb)
        if sa == sb:
            continue

        regressed = False
        notes = []
        # Hard regressions: render-hint disappeared, tool unset, fallback appeared
        if sa["render_hint"] and not sb["render_hint"]:
            regressed = True
            notes.append(f"render_hint LOST: {sa['render_hint']!r} → None")
        if sa["render_hint"] != sb["render_hint"] and sa["render_hint"] and sb["render_hint"]:
            regressed = True
            notes.append(f"render_hint CHANGED: {sa['render_hint']!r} → {sb['render_hint']!r}")
        if sa["logiccard_type"] != sb["logiccard_type"]:
            regressed = True
            notes.append(f"logiccard_type: {sa['logiccard_type']!r} → {sb['logiccard_type']!r}")
        if sa["tools_called"] != sb["tools_called"]:
            # Soft drift if both empty; hard if a tool flipped
            if sa["tools_called"] or sb["tools_called"]:
                regressed = True
            notes.append(f"tools_called: {sa['tools_called']} → {sb['tools_called']}")
        if not sa["is_fallback"] and sb["is_fallback"]:
            regressed = True
            notes.append("FELL BACK to LLM-unavailable")
        if sa["error"] != sb["error"]:
            notes.append(f"error: {sa['error']!r} → {sb['error']!r}")

        marker = "  [REGRESSION]" if regressed else "  [drift]    "
        print(f"{marker} {pid}")
        for n in notes:
            print(f"      {n}")
        if regressed:
            regressions += 1
        else:
            drifted += 1

    print(f"\n  summary: {regressions} regression(s), {drifted} acceptable drift(s)")
    return 1 if regressions > 0 else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", help="snapshot label (writes tests/eval_results/<label>.json)")
    ap.add_argument("--prompts", default=None,
                    help="prompt set: JSON path OR bare name resolved to "
                         "pivot/tests/eval_prompts/<name>.json. "
                         "Default: built-in PROMPTS (legacy 18-prompt set).")
    ap.add_argument("--diff", nargs=2, metavar=("A", "B"),
                    help="diff two existing snapshots by label")
    args = ap.parse_args()

    if args.diff:
        return diff_snapshots(*args.diff)
    if args.label:
        run_eval(args.label, prompts_spec=args.prompts)
        return 0
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
