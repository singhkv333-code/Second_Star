"""Generic chat eval — live, multi-turn, triad-instrumented, sessions from JSON.

Parameterized twin of fno_batch_eval.py (2026-06-04): drives the LIVE
/chat endpoint exactly like the frontend (stable conversation_id +
growing messages[] window, Bearer auth), SEQUENTIALLY (Azure throttle +
clean token attribution), and captures the full quality triad per turn:

  tokens   — llm_usage id-range (MAX(id) before → SUM(WHERE id > prev)),
             so every internal hop is attributed to its turn
  latency  — wall-clock + the server's latency_ms
  quality  — judged AFTER the run (eval-judge panel + reconcile);
             this script only snapshots the raw evidence

Sessions file schema (same shape as the hardcoded SESSIONS lists):
  [{"category": str, "name": str, "why": str, "turns": [str, ...]}, ...]

Run (server on :8000, cwd=pivot/):
  .venv/bin/python scripts/auto_batch_eval.py --sessions <path.json> \
      [--out <dir>] [--only name1,name2] [--limit N]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path

import httpx

BASE = os.getenv("PIVOT_BASE", "http://localhost:8000")


def _register_user(client: httpx.Client) -> tuple[str, int]:
    # NOTE: .test is a reserved TLD that email-validator rejects.
    email = f"auto_eval_{uuid.uuid4().hex[:8]}@pivoteval.com"
    r = client.post(f"{BASE}/auth/register", json={
        "email": email, "password": "password123", "full_name": "Auto Eval",
    })
    r.raise_for_status()
    body = r.json()
    return body["access_token"], int(body.get("user_id") or 0)


def _llm_usage_max_id() -> int:
    from backend.database import SessionLocal
    from backend.models import LlmUsage
    from sqlalchemy import func as sqlfunc

    db = SessionLocal()
    try:
        return int(db.query(sqlfunc.max(LlmUsage.id)).scalar() or 0)
    finally:
        db.close()


def _llm_usage_since(prev_max: int) -> dict:
    from backend.database import SessionLocal
    from backend.models import LlmUsage
    from sqlalchemy import func as sqlfunc

    db = SessionLocal()
    try:
        row = (
            db.query(
                sqlfunc.coalesce(sqlfunc.sum(LlmUsage.input_tokens), 0),
                sqlfunc.coalesce(sqlfunc.sum(LlmUsage.output_tokens), 0),
                sqlfunc.coalesce(sqlfunc.sum(LlmUsage.cost_usd), 0),
                sqlfunc.count(LlmUsage.id),
            )
            .filter(LlmUsage.id > prev_max)
            .one()
        )
        return {
            "input_tokens": int(row[0]), "output_tokens": int(row[1]),
            "cost_usd": float(row[2]), "llm_calls": int(row[3]),
        }
    finally:
        db.close()


_HEAVY_KEYS = {"payoff", "rows", "candles", "bars", "history", "equity_curve"}


def _compact(raw: dict, limit: int = 1500) -> str:
    """Heavy-array-stripped JSON dump, truncated — generic judge evidence."""
    slim = {k: v for k, v in raw.items() if k not in _HEAVY_KEYS}
    s = json.dumps(slim, default=str)
    return s[:limit] + ("…" if len(s) > limit else "")


def _card_digest(raw: object) -> dict | None:
    """Small, judge-readable digest of the card payload (no payoff bulk)."""
    if not isinstance(raw, dict) or not raw.get("_render_hint"):
        return None
    hint = raw["_render_hint"]
    if hint == "option_strategy_card":
        c = raw.get("computed") or {}
        return {
            "template": (raw.get("editable") or {}).get("template"),
            "legs": [
                {"t": l.get("option_type"), "s": l.get("side"),
                 "k": l.get("strike"), "mid": l.get("mid")}
                for l in (raw.get("editable") or {}).get("legs", [])
            ],
            "qty_lots": (raw.get("editable") or {}).get("qty_lots"),
            "max_loss": c.get("max_loss"), "max_profit": c.get("max_profit"),
            "pop": c.get("pop"), "breakevens": c.get("breakevens"),
            "capital": c.get("capital_required"),
            "margin": c.get("margin_estimate"),
            "verdict": (raw.get("critique") or {}).get("verdict"),
            "flags": [f.get("severity") for f in (raw.get("critique") or {}).get("flags", [])],
            "candidates": [x.get("template") for x in raw.get("candidates", [])],
            "mcx_blocked": (raw.get("validation") or {}).get("mcx_execution_blocked"),
        }
    if hint == "option_chain_card":
        return {
            "underlying": raw.get("underlying"), "expiry": raw.get("expiry"),
            "rows": len(raw.get("rows") or []), "atm": raw.get("atm_strike"),
            "forward": raw.get("forward"),
            "expected_move": raw.get("expected_move"),
            "research_only": raw.get("research_only"),
        }
    if hint == "portfolio_greeks_card":
        return {
            "net": raw.get("net"), "positions": raw.get("position_count"),
            "delta_notional": raw.get("delta_notional"),
        }
    if hint == "workflow_draft_card":
        # Automation evals live or die on trigger/action params — keep them.
        steps = raw.get("steps") or (raw.get("draft") or {}).get("steps") or []
        return {
            "steps": [
                {"type": st.get("step_type"),
                 "params": json.dumps(st.get("params") or st.get("config")
                                      or {}, default=str)[:400]}
                for st in steps if isinstance(st, dict)
            ],
            "compact": _compact(raw),
        }
    return {"hint": hint, "compact": _compact(raw)}


def _logiccard_digest(card: object) -> dict | None:
    """GAN R4 F13: judge-readable digest of the top-level `logiccard`
    field (SIP/order/GTT/dip-buy cards live here, NOT in raw_data).
    Keeps the action/symbol/amount/schedule the discriminators score on
    so SIP-class cards are visible and don't read as 'empty card'."""
    if not isinstance(card, dict):
        return None
    keep = (
        "kind", "type", "card_type", "action", "side", "symbol",
        "quantity", "qty", "notional_inr", "amount", "frequency",
        "day_of_week", "day_of_month", "schedule", "time_ist", "cron",
        "dip_pct", "order_type", "price", "trigger", "status",
        "register_not_execute", "requires_approval",
    )
    out = {k: card[k] for k in keep if k in card}
    # Carry a compact JSON of the whole card too (truncated) so nothing
    # load-bearing is silently dropped.
    out["_full"] = json.dumps(card, default=str)[:600]
    return out or None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sessions", required=True, help="sessions JSON path")
    ap.add_argument("--out", default="tests/eval_results/automation_50")
    ap.add_argument("--only", default="", help="comma-separated session names")
    ap.add_argument("--limit", type=int,
                    default=int(os.getenv("PIVOT_EVAL_LIMIT", "0") or 0),
                    help="harness smoke only — the real run is unlimited")
    args = ap.parse_args()

    sessions: list[dict] = json.loads(Path(args.sessions).read_text())
    if args.only:
        only = {n.strip() for n in args.only.split(",") if n.strip()}
        sessions = [s for s in sessions if s["name"] in only]
        missing = only - {s["name"] for s in sessions}
        if missing:
            print(f"WARNING: --only names not found: {sorted(missing)}")
    if args.limit:
        sessions = sessions[:args.limit]

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    started = time.time()

    with httpx.Client(timeout=180.0) as client:
        token, user_id = _register_user(client)
        headers = {"Authorization": f"Bearer {token}"}

        sessions_out: list[dict] = []
        n_turns = 0
        for s in sessions:
            conv = f"s_{uuid.uuid4().hex[:10]}"
            messages: list[dict] = []
            turns_out: list[dict] = []
            for i, say in enumerate(s["turns"]):
                messages.append({"role": "user", "content": say})
                prev_max = _llm_usage_max_id()
                t0 = time.time()
                try:
                    r = client.post(
                        f"{BASE}/chat", headers=headers,
                        json={
                            "messages": messages,
                            "conversation_id": conv,
                            "include_portfolio_context": False,
                        },
                    )
                    wall_ms = int((time.time() - t0) * 1000)
                    body = r.json()
                except Exception as exc:  # noqa: BLE001 — snapshot the failure
                    wall_ms = int((time.time() - t0) * 1000)
                    body = {"response": f"<<HTTP ERROR: {exc}>>"}
                usage = _llm_usage_since(prev_max)
                response = body.get("response") or ""
                raw = body.get("raw_data") or {}
                # GAN R4 F13: SIP-class cards ship in the top-level
                # `logiccard` field (chat.py:441-452), NOT raw_data — the
                # prior harness snapshotted only raw_data and produced a
                # false "empty card" verdict on cheaper_one. Snapshot
                # logiccard too, and let the digest fall back to it.
                logiccard = body.get("logiccard") if isinstance(body, dict) else None
                turn = {
                    "i": i,
                    "say": say,
                    "response": response,
                    "tools_called": body.get("tools_called"),
                    "render_hint": raw.get("_render_hint"),
                    "raw_keys": sorted(raw.keys())[:20] if isinstance(raw, dict) else None,
                    "card_digest": _card_digest(raw),
                    "logiccard": _logiccard_digest(logiccard),
                    "latency_wall_ms": wall_ms,
                    "latency_server_ms": body.get("latency_ms"),
                    **usage,
                }
                turns_out.append(turn)
                messages.append({"role": "assistant", "content": response})
                n_turns += 1
                hint = turn["render_hint"] or "-"
                print(
                    f"[{n_turns:02d}] {s['name']}/{i} {wall_ms/1000:.1f}s "
                    f"tools={turn['tools_called']} hint={hint}",
                    flush=True,
                )
            sessions_out.append({
                "category": s["category"], "name": s["name"],
                "why": s["why"], "conv": conv, "turns": turns_out,
            })

    out = {
        "ts": ts,
        "elapsed_s": round(time.time() - started, 1),
        "base": BASE,
        "user_id": user_id,
        "sessions_file": str(args.sessions),
        "only": args.only or None,
        "n_sessions": len(sessions_out),
        "n_turns": n_turns,
        "sessions": sessions_out,
    }
    path = out_dir / f"run_{ts}.json"
    path.write_text(json.dumps(out, indent=1, default=str))
    print(f"\nSnapshot: {path} ({n_turns} turns, {out['elapsed_s']}s)")


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    main()
