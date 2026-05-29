"""Batched retail-investor chat eval — drives the LIVE /chat endpoint.

WHY this exists (2026-05-29, user redirect): we strengthened the backend
for the REAL retail prompt categories — stock/return comparison,
fundamental screening (cheap banks / best dividend / single PE-ROE),
SIP+gold+silver+GTT, RBI rate-cut event agents, buy-on-dip / sell-at-
profit, IPO-in-chat, oil/MCX graceful decline, analysis-with-search, and
the multi-turn context-retention regressions. This harness exercises ALL
of them in ONE instrumented pass and emits a full transcript snapshot for
a thinking-model judge to grade (NOT a regex auto-verdict — see
feedback_eval_must_be_multiturn_live + feedback_quality_check_triad).

Design choices:
  * SEQUENTIAL live execution. Each /chat turn hits Azure gpt-5.4-mini;
    parallel bursts throttle ("temporarily unavailable") AND corrupt
    per-turn token attribution. Sequential keeps both clean. Judging is
    parallelised downstream (no live calls), which is where fan-out is
    safe.
  * TRUE per-turn tokens via the llm_usage table, captured by id-range
    (MAX(id) before -> SUM where id > prev). This includes EVERY internal
    hop (chat + router + propose + agentic 'unknown' rows), not just the
    final hop a log-scrape would see.
  * Full response text + render_hint + trimmed raw_data dumped to JSON so
    the judge reasons over what the retail user actually got.

Quality triad per turn: input/output/total tokens + cost_usd + n_hops
(from llm_usage) | latency (wall + server) | judged downstream.

Usage:
    .venv/bin/python scripts/retail_batch_eval.py
Backend must be running on :8000. Writes a snapshot to
tests/eval_results/retail_batch/run_<ts>.json and prints a triad table.
"""
from __future__ import annotations

import json
import logging
import sys
import time
import uuid
from pathlib import Path

import httpx

# Quiet the SQLAlchemy engine echo (the global engine has echo on).
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)

from sqlalchemy import func  # noqa: E402

from backend.database import SessionLocal  # noqa: E402
from backend.models import LlmUsage  # noqa: E402

BASE = "http://127.0.0.1:8000"
_ROOT = Path(__file__).resolve().parent.parent
_OUT = _ROOT / "tests" / "eval_results" / "retail_batch"
_OUT.mkdir(parents=True, exist_ok=True)


# ── Batches: each session is a multi-turn conversation (shared conv_id).
# `note` documents what recent fix the turn validates, for the judge.
BATCHES = [
    ("comparison", [
        {"name": "compare_tcs_infy_3y",
         "why": "A-vs-B comparison over an arbitrary 3y window (not silently 2y/5y); real CAGR, not fabricated",
         "turns": [
             {"say": "compare tcs and infosys"},
             {"say": "over the last 3 years"},
             {"say": "which one is less volatile?"},
         ]},
        {"name": "lakh_in_hdfc_vs_fd",
         "why": "return math on a real 5y window + compare to an FD; numbers must be grounded, not invented",
         "turns": [
             {"say": "if I had put 1 lakh in HDFC Bank 5 years ago, what would it be worth now?"},
             {"say": "and how does that compare to a bank FD at 7%?"},
         ]},
    ]),
    ("screening", [
        {"name": "cheap_banks",
         "why": "vague sort-only screen; banks must return real P/E (FIELD_MAP bank label fix), not 0 or a clarifier",
         "turns": [{"say": "show me some cheap banking stocks"}]},
        {"name": "best_dividend",
         "why": "dividend screen must return a real payout-ranked list, not 'not wired'",
         "turns": [{"say": "which stocks pay the best dividends?"}]},
        {"name": "reliance_pe_roe",
         "why": "single-fundamental lookup should resolve directly (no wasted find_tool hop) + reason on it",
         "turns": [
             {"say": "what's reliance's PE and ROE?"},
             {"say": "is that expensive for the sector?"},
         ]},
        {"name": "roe_pe_screen",
         "why": "explicit numeric screen (ROE>15 & PE<20) must apply both filters",
         "turns": [{"say": "screen large caps with ROE above 15 and PE below 20"}]},
    ]),
    ("sip_gold_silver_gtt", [
        {"name": "nifty_sip_amend",
         "why": "monthly SIP into an index fund + amount amendment must retain context (not re-ask)",
         "turns": [
             {"say": "start a 5000 rupee monthly SIP into a nifty 50 index fund"},
             {"say": "actually make it 10000"},
         ]},
        {"name": "gold_sip_every_month",
         "why": "'invest X in gold every month' must surface create_sip (gold), not decline",
         "turns": [{"say": "I want to invest 2000 in gold every month"}]},
        {"name": "silver_sip",
         "why": "silver SIP resolves to SILVERBEES, not 'no quote'",
         "turns": [{"say": "set up a monthly 1500 sip in silver"}]},
        {"name": "gtt_reliance",
         "why": "GTT buy at a target price drafts a registerable order",
         "turns": [{"say": "set a GTT to buy reliance at 1200"}]},
    ]),
    ("rbi_event", [
        {"name": "rbi_rate_cut_banks",
         "why": "RBI repo-cut event agent must draft (event trigger), then confirm should register not loop",
         "turns": [
             {"say": "build me an agent that buys banking stocks when the RBI cuts the repo rate"},
             {"say": "yes, set it up"},
         ]},
    ]),
    ("dip_profit", [
        {"name": "dip_simple",
         "why": "simple buy-on-dip should draft directly, not over-clarify",
         "turns": [{"say": "buy hdfc bank on a 10% dip"}]},
        {"name": "dip_profit_compound",
         "why": "compound dip+take-profit: profit must be entry-relative; confirm should register, not re-confirm loop",
         "turns": [
             {"say": "buy reliance 5 shares on a 10% dip and sell at 8% profit"},
             {"say": "yes"},
         ]},
    ]),
    ("ipo", [
        {"name": "ipo_browse_apply",
         "why": "IPO feed shown IN chat; empty feed handled gracefully; 'apply' acknowledged",
         "turns": [
             {"say": "any IPOs open right now?"},
             {"say": "show me the full details of the most recent one"},
             {"say": "I want to apply"},
         ]},
    ]),
    ("oil_mcx_decline", [
        {"name": "war_oil_mcx",
         "why": "war->crude futures on MCX is F&O/commodity-futures out of scope; must gracefully decline, not crash or fabricate",
         "turns": [{"say": "if war breaks out in the middle east, buy crude oil futures on MCX"}]},
    ]),
    ("analysis_search", [
        {"name": "why_nifty_down",
         "why": "must fetch a REAL index level (get_index_level ^-ticker fallback) + ground with movers, not level=None",
         "turns": [{"say": "why is nifty down today?"}]},
        {"name": "it_sector_outlook",
         "why": "sector-outlook analysis should think/search and answer with substance, not deflect",
         "turns": [{"say": "what's the outlook for the IT sector right now?"}]},
    ]),
    ("context_regression", [
        {"name": "qty_amendment_expiry",
         "why": "after '10 shares', an unrelated expiry amendment must NOT re-ask qty (M2 guard)",
         "turns": [
             {"say": "build me an agent that buys hdfc bank on a 10% dip and exits on a 5% rise"},
             {"say": "10 shares"},
             {"say": "set an expiry for the next 30 days"},
         ]},
    ]),
    ("multi_symbol", [
        {"name": "basket_three",
         "why": "a 3-symbol order must name all three; no silent drop to RELIANCE-only",
         "turns": [{"say": "buy 1 share each of reliance, tcs and infosys when nifty rises 1%"}]},
    ]),
]


def _register() -> tuple[str, int]:
    e = f"retail_{uuid.uuid4().hex[:10]}@p.com"
    r = httpx.post(f"{BASE}/auth/register",
                   json={"email": e, "password": "password123", "full_name": "retail"},
                   timeout=30)
    r.raise_for_status()
    body = r.json()
    return body["access_token"], body.get("user", {}).get("id", -1)


def _find_n_trades(obj):
    if isinstance(obj, dict):
        if "n_trades" in obj and isinstance(obj["n_trades"], (int, float)):
            return int(obj["n_trades"])
        for v in obj.values():
            r = _find_n_trades(v)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = _find_n_trades(v)
            if r is not None:
                return r
    return None


def _tokens_since(db, prev_max_id: int):
    row = (db.query(
        func.coalesce(func.sum(LlmUsage.input_tokens), 0),
        func.coalesce(func.sum(LlmUsage.output_tokens), 0),
        func.coalesce(func.sum(LlmUsage.total_tokens), 0),
        func.coalesce(func.sum(LlmUsage.cost_usd), 0),
        func.count(LlmUsage.id),
        func.coalesce(func.max(LlmUsage.id), prev_max_id),
    ).filter(LlmUsage.id > prev_max_id).one())
    return {
        "in_tok": int(row[0]), "out_tok": int(row[1]), "total_tok": int(row[2]),
        "cost_usd": float(row[3]), "n_hops": int(row[4]), "new_max_id": int(row[5]),
    }


def run():
    token, user_id = _register()
    hdr = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    db = SessionLocal()
    prev_max = db.query(func.coalesce(func.max(LlmUsage.id), 0)).scalar()

    results = []
    t_start = time.time()
    for category, sessions in BATCHES:
        for sess in sessions:
            conv = f"s_{uuid.uuid4().hex[:8]}"
            msgs = []
            turn_rows = []
            for ti, turn in enumerate(sess["turns"]):
                msgs.append({"role": "user", "content": turn["say"]})
                t0 = time.monotonic()
                try:
                    r = httpx.post(f"{BASE}/chat", headers=hdr,
                                   json={"messages": msgs, "conversation_id": conv,
                                         "include_portfolio_context": True},
                                   timeout=200)
                    wall = int((time.monotonic() - t0) * 1000)
                    resp = r.json() if r.status_code == 200 else {
                        "response": f"[HTTP {r.status_code}] {r.text[:300]}",
                        "tools_called": [], "raw_data": None, "latency_ms": wall}
                except Exception as exc:  # noqa: BLE001
                    wall = int((time.monotonic() - t0) * 1000)
                    resp = {"response": f"[EXC] {exc}", "tools_called": [],
                            "raw_data": None, "latency_ms": wall}
                # commit to release any snapshot so the SUM sees fresh rows
                db.commit()
                tok = _tokens_since(db, prev_max)
                prev_max = tok["new_max_id"]
                raw = resp.get("raw_data") or {}
                render = raw.get("_render_hint") if isinstance(raw, dict) else None
                raw_json = ""
                try:
                    raw_json = json.dumps(raw)[:3500]
                except Exception:  # noqa: BLE001
                    raw_json = str(raw)[:3500]
                text = resp.get("response") or ""
                turn_rows.append({
                    "i": ti,
                    "say": turn["say"],
                    "response": text,
                    "tools_called": resp.get("tools_called") or [],
                    "render_hint": render,
                    "raw_keys": sorted(raw.keys()) if isinstance(raw, dict) else [],
                    "n_trades": _find_n_trades(raw),
                    "raw_trim": raw_json,
                    "latency_wall_ms": wall,
                    "latency_server_ms": resp.get("latency_ms"),
                    "in_tok": tok["in_tok"], "out_tok": tok["out_tok"],
                    "total_tok": tok["total_tok"], "cost_usd": round(tok["cost_usd"], 5),
                    "n_hops": tok["n_hops"],
                })
                msgs.append({"role": "assistant", "content": text})
                print(f"  [{category}/{sess['name']}] turn{ti} "
                      f"{wall}ms in={tok['in_tok']} out={tok['out_tok']} "
                      f"hops={tok['n_hops']} render={render} "
                      f"tools={resp.get('tools_called')}", flush=True)
            results.append({
                "category": category, "name": sess["name"], "why": sess["why"],
                "conv": conv, "turns": turn_rows,
            })
    db.close()
    elapsed = int(time.time() - t_start)

    ts = time.strftime("%Y%m%d_%H%M%S")
    out_json = _OUT / f"run_{ts}.json"
    snapshot = {
        "ts": ts, "elapsed_s": elapsed, "base": BASE, "user_id": user_id,
        "n_sessions": len(results),
        "n_turns": sum(len(s["turns"]) for s in results),
        "sessions": results,
    }
    out_json.write_text(json.dumps(snapshot, indent=2))

    # triad aggregate
    all_turns = [t for s in results for t in s["turns"]]
    lat = sorted(t["latency_wall_ms"] for t in all_turns)
    p50 = lat[len(lat) // 2]
    p95 = lat[min(len(lat) - 1, int(len(lat) * 0.95))]
    tot_in = sum(t["in_tok"] for t in all_turns)
    tot_out = sum(t["out_tok"] for t in all_turns)
    tot_cost = sum(t["cost_usd"] for t in all_turns)
    print(f"\n{'='*90}")
    print(f"RETAIL BATCH EVAL — {len(results)} sessions / {len(all_turns)} turns "
          f"in {elapsed}s")
    print(f"TRIAD: p50 {p50}ms p95 {p95}ms | in {tot_in} out {tot_out} tok | "
          f"${tot_cost:.4f} | snapshot: {out_json}")
    print(f"{'='*90}")
    print(str(out_json))
    return 0


if __name__ == "__main__":
    sys.exit(run())
