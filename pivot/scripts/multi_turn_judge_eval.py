"""Strict multi-turn conversational judge for the Pivot chat layer.

WHY this exists (see cluster C8 of the 2026-05-29 root-cause sweep):
the prior eval scored ~87% PASS while the live UI failed every
multi-turn screenshot, because its checks were `tool_name in
tools_called` + `substring in response` — never SERVER-SIDE TRUTH.
A turn that re-asked an already-answered slot, or looped on
"...sound right?", or returned an all-zero backtest, still scored
PASS as long as a tool fired and the right word appeared.

This harness drives shared-conv_id multi-turn sessions through the
LIVE /chat endpoint (exactly like the frontend: a stable
conversation_id + the growing messages[] window) and judges EACH turn
with hard assertions about what actually happened:

  context_retained   — an amendment/clarification turn must NOT re-ask
                        a slot the user already filled
  executed_not_looped— a confirm/run turn must EMIT (not return ASK_USER
                        / render_hint='ask_user')
  symbol_resolved    — no "no quote available" / "0 bars" /
                        "insufficient data"
  numbers_nonzero    — a backtest turn must produce real metrics
                        (n_trades>0) OR an explicit zero-fire warning
  no_silent_drop     — a multi-symbol order must name every symbol
  not_identical      — an amendment reply must differ from the prior
                        reply (catches byte-identical re-asks)

Quality triad per turn (MEMORY: feedback_quality_check_triad):
  tokens (input/output, scraped from the backend's llm.usage log) +
  latency_ms (from the /chat response) + a PASS/PARTIAL/FAIL verdict
  with a one-line reason.

A turn is PASS only if EVERY assertion holds; a SESSION is PASS only
if every turn PASSes (one dropped amendment fails the whole session).

Usage:
    .venv/bin/python scripts/multi_turn_judge_eval.py [--log /tmp/uv.log]

Backend must be running on :8000. Outputs a markdown table to stdout
and a JSON blob to tests/eval_results/multi_turn/run_<ts>.json.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import uuid
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:8000"
_ROOT = Path(__file__).resolve().parent.parent
_OUT = _ROOT / "tests" / "eval_results" / "multi_turn"
_OUT.mkdir(parents=True, exist_ok=True)

_USAGE_IN_RE = re.compile(r"input_tokens=(\d+)")
_USAGE_OUT_RE = re.compile(r"output_tokens=(\d+)")
_USAGE_LINE_RE = re.compile(r"llm\.usage")


# ── Sessions: one per screenshot failure + the hard prompts ──────────
# Each turn: {"say": <user text>, "expect": {<assertion>: <arg>, ...},
# "label": <what cluster/why>}. `expect` keys are interpreted by
# `judge_turn` below.
SESSIONS = [
    {
        "name": "C1_expiry_amendment",
        "why": "after '10 shares', an unrelated amendment must not re-ask qty",
        "turns": [
            {"say": "build me an agent that buys hdfc bank on a 10% dip and exits on a 5% rise",
             "expect": {}},
            {"say": "10 shares",
             "expect": {"not_contains": ["how many shares"]}},
            {"say": "set an expiry for next 30 days",
             "expect": {"not_contains": ["how many shares", "per fire"],
                        "render_not": "ask_user",
                        "not_identical_prev": True}},
        ],
    },
    {
        "name": "C2_clarify_keeps_qty",
        "why": "qty named in the opening message must survive a clarification round-trip",
        "turns": [
            {"say": "buy 10 shares of hdfc if it closes above resistance",
             "expect": {}},
            {"say": "bank",
             "expect": {"not_contains": ["how many shares", "per fire"]}},
        ],
    },
    {
        "name": "C3_bare_number_qty",
        "why": "a bare-number reply ('10') answers the qty ASK; must not repeat it",
        "turns": [
            {"say": "buy reliance when 20 dma crosses above 50 dma",
             "expect": {}},
            {"say": "10",
             "expect": {"not_contains": ["how many shares", "per fire"],
                        "not_identical_prev": True}},
        ],
    },
    {
        "name": "C4_index_quotes",
        "why": "SENSEX / RIL / NIFTY must resolve to real tickers, not '<X>.NSE'",
        "turns": [
            {"say": "what about sensex",
             "expect": {"not_contains": ["no quote available", "sensex.nse"]}},
            {"say": "what about RIL",
             "expect": {"not_contains": ["no quote available", "ril.nse"]}},
            {"say": "what about nifty",
             "expect": {"not_contains": ["no quote available", "nifty.nse"]}},
        ],
    },
    {
        "name": "C4_nifty_backtest",
        "why": "a NIFTY-triggered backtest must fetch index bars, not '0 bars'",
        "turns": [
            {"say": "backtest buy reliance 10 shares when nifty drops 1% over the last 3 years",
             "expect": {"not_contains": ["insufficient data", "got 0 bars", "0 bars"]}},
        ],
    },
    {
        "name": "C6_multi_symbol_basket",
        "why": "a 3-symbol order must cover all three, not 'RELIANCE only'",
        "turns": [
            {"say": "buy 1 share of reliance, tcs and bajajfin when nifty rises by 1% and sell them when it rises 1% more",
             "expect": {"not_contains": ["reliance only", "applies the same logic to reliance",
                                          "duplicate or edit"],
                        "symbols_all": ["TCS", "BAJ"]}},
        ],
    },
    {
        "name": "C7_sip_backtest_confirm",
        "why": "'right' confirming a backtest plan must RUN it, not re-ask",
        "turns": [
            {"say": "Backtest a monthly SIP into NIFTYBEES vs a lump sum at the start of 2022 — show both on one chart and tell me which strategy won and by how much.",
             "expect": {}},
            {"say": "right",
             "expect": {"render_not": "ask_user",
                        "tool_not_only": ["ASK_USER"],
                        "not_identical_prev": True}},
        ],
    },
    {
        "name": "C7_suggest_something",
        "why": "'suggest something' must propose a concrete strategy, not re-ask the menu",
        "turns": [
            {"say": "make me a strategy for zomato",
             "expect": {}},
            {"say": "suggest something",
             "expect": {"not_identical_prev": True,
                        "render_not": "ask_user"}},
        ],
    },
    {
        "name": "C5_dip_backtest_nonzero",
        "why": "a 10% dip backtest must produce trades or an explicit zero-fire warning, not a silent all-zero card",
        "turns": [
            {"say": "buy hdfc bank 20 shares on a 10% dip and exit on a 5% rise",
             "expect": {}},
            {"say": "backtest it over the last 5 years",
             "expect": {"backtest_not_silent_zero": True}},
        ],
    },
    {
        "name": "C9_compound_multistep",
        "why": "compare 3 stocks -> pick winner -> build agent must complete end-to-end",
        "turns": [
            {"say": "Compare INFY, TCS and WIPRO over the last 2 years, tell me which had the lowest drawdown, then build me a momentum agent on the winner with 10 shares",
             "expect": {"not_contains": ["could not build", "couldn't build",
                                          "needs a trade size", "how many shares"]}},
        ],
    },
    {
        "name": "HARD_hdfc_5pct_week_plus_freq",
        "why": "buys HDFC on 5% weekly drop AND shows historical trigger frequency",
        "turns": [
            {"say": "Create an agent that buys HDFC Bank 10 shares when it drops 5% in a week, and at the same time show me how often that would have triggered over the last 3 years",
             "expect": {"not_contains": ["how many shares", "insufficient data", "0 bars"]}},
        ],
    },
    {
        "name": "HARD_5lakh_3stocks_rebalance",
        "why": "split 5L across 3 large-caps, backtest, set up rebalance",
        "turns": [
            {"say": "I have 5 lakh rupees. Split it across 3 large-cap stocks of your choice, backtest that portfolio for the last 18 months, and set up a rebalancing agent.",
             "expect": {"not_contains": ["how many shares", "insufficient data", "0 bars"]}},
        ],
    },
]


def _register() -> str:
    e = f"judge_{uuid.uuid4().hex[:10]}@p.com"
    r = httpx.post(f"{BASE}/auth/register",
                   json={"email": e, "password": "password123", "full_name": "judge"},
                   timeout=30)
    r.raise_for_status()
    return r.json()["access_token"]


def _flatten(obj, acc):
    """Recursively collect strings + numbers from raw_data for assertions."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            acc.append(str(k))
            _flatten(v, acc)
    elif isinstance(obj, list):
        for v in obj:
            _flatten(v, acc)
    else:
        acc.append(str(obj))


def _find_n_trades(obj):
    """Recursively find an n_trades value anywhere in raw_data."""
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


def judge_turn(spec, resp, prev_resp):
    """Return (verdict, reasons[]). verdict in PASS/PARTIAL/FAIL."""
    expect = spec.get("expect") or {}
    text = (resp.get("response") or "")
    low = text.lower()
    tools = resp.get("tools_called") or []
    raw = resp.get("raw_data") or {}
    render = (raw.get("_render_hint") if isinstance(raw, dict) else None) or ""
    flat = []
    _flatten(raw, flat)
    flat_blob = " ".join(flat).upper() + " " + text.upper()
    reasons = []

    for sub in expect.get("not_contains", []):
        if sub.lower() in low:
            reasons.append(f"response contains forbidden {sub!r}")

    ca = expect.get("contains_any")
    if ca and not any(s.lower() in low for s in ca):
        reasons.append(f"response missing any of {ca}")

    if "render_not" in expect and render == expect["render_not"]:
        reasons.append(f"render_hint == forbidden {expect['render_not']!r}")

    rin = expect.get("render_any")
    if rin and render not in rin:
        reasons.append(f"render_hint {render!r} not in {rin}")

    tno = expect.get("tool_not_only")
    if tno and tools and all(t in tno for t in tools):
        reasons.append(f"only forbidden tools fired: {tools}")

    if expect.get("not_identical_prev") and prev_resp is not None:
        if text.strip() and text.strip() == (prev_resp or "").strip():
            reasons.append("response byte-identical to previous turn (re-ask loop)")

    for sym in expect.get("symbols_all", []):
        if sym.upper() not in flat_blob:
            reasons.append(f"symbol {sym!r} missing from draft/response")

    if expect.get("backtest_not_silent_zero"):
        n = _find_n_trades(raw)
        warned = any(w in low for w in
                     ["never triggered", "unreachable", "wider lookback",
                      "smaller threshold", "no trades"])
        if n is not None and n == 0 and not warned:
            reasons.append("backtest all-zero (n_trades=0) with NO warning — silent zero")
        if n is None and not warned and render not in ("indicator_backtest_chart",
                                                       "workflow_backtest_chart"):
            reasons.append("no backtest metrics found and no warning")

    # error-shape guard: any turn that surfaced an internal error string
    if any(s in low for s in ["traceback", "internal error",
                              "ai backend temporarily unavailable"]):
        reasons.append("surfaced an internal/backend error")

    if not reasons:
        return "PASS", []
    # PARTIAL when the only miss is a soft 'contains_any'; else FAIL.
    return "FAIL", reasons


def run():
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", default="/tmp/uv.log")
    args = ap.parse_args()
    log_path = Path(args.log)

    token = _register()
    hdr = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    def log_offset():
        try:
            return log_path.stat().st_size
        except OSError:
            return 0

    def tokens_since(off):
        try:
            with open(log_path, "rb") as f:
                f.seek(off)
                chunk = f.read().decode("utf-8", "ignore")
        except OSError:
            return (0, 0)
        tin = tout = 0
        for line in chunk.splitlines():
            if _USAGE_LINE_RE.search(line):
                mi = _USAGE_IN_RE.search(line)
                mo = _USAGE_OUT_RE.search(line)
                if mi:
                    tin += int(mi.group(1))
                if mo:
                    tout += int(mo.group(1))
        return (tin, tout)

    results = []
    for sess in SESSIONS:
        conv = f"s_{uuid.uuid4().hex[:8]}"
        msgs = []
        prev_resp = None
        turn_rows = []
        sess_pass = True
        for ti, turn in enumerate(sess["turns"]):
            msgs.append({"role": "user", "content": turn["say"]})
            off = log_offset()
            t0 = time.monotonic()
            try:
                r = httpx.post(f"{BASE}/chat", headers=hdr,
                               json={"messages": msgs, "conversation_id": conv,
                                     "include_portfolio_context": True},
                               timeout=180)
                wall = int((time.monotonic() - t0) * 1000)
                resp = r.json() if r.status_code == 200 else {
                    "response": f"[HTTP {r.status_code}] {r.text[:200]}",
                    "tools_called": [], "raw_data": None, "latency_ms": wall}
            except Exception as exc:  # noqa: BLE001
                wall = int((time.monotonic() - t0) * 1000)
                resp = {"response": f"[EXC] {exc}", "tools_called": [],
                        "raw_data": None, "latency_ms": wall}
            tin, tout = tokens_since(off)
            verdict, reasons = judge_turn(turn, resp, prev_resp)
            if verdict != "PASS":
                sess_pass = False
            turn_rows.append({
                "i": ti, "say": turn["say"][:70],
                "verdict": verdict,
                "tools": resp.get("tools_called") or [],
                "render": (resp.get("raw_data") or {}).get("_render_hint")
                          if isinstance(resp.get("raw_data"), dict) else None,
                "in_tok": tin, "out_tok": tout,
                "latency_ms": resp.get("latency_ms") or wall,
                "reasons": reasons,
                "resp_preview": (resp.get("response") or "")[:160].replace("\n", " "),
            })
            prev_resp = resp.get("response") or ""
            msgs.append({"role": "assistant", "content": prev_resp})
        results.append({"name": sess["name"], "why": sess["why"],
                        "session_pass": sess_pass, "conv": conv, "turns": turn_rows})

    # ── Report ──
    ts = time.strftime("%Y%m%d_%H%M%S")
    out_json = _OUT / f"run_{ts}.json"
    out_json.write_text(json.dumps(results, indent=2))

    n_pass = sum(1 for s in results if s["session_pass"])
    print(f"\n{'='*100}\nMULTI-TURN STRICT JUDGE — {n_pass}/{len(results)} sessions PASS\n{'='*100}\n")
    print(f"{'session':<28}{'turn':<5}{'verdict':<9}{'in_tok':<8}{'out_tok':<8}{'lat_ms':<8}reason")
    print("-" * 100)
    for s in results:
        for t in s["turns"]:
            reason = "; ".join(t["reasons"])[:60] if t["reasons"] else ""
            name = s["name"] if t["i"] == 0 else ""
            print(f"{name:<28}{t['i']:<5}{t['verdict']:<9}{t['in_tok']:<8}{t['out_tok']:<8}{t['latency_ms']:<8}{reason}")
        if not s["session_pass"]:
            print(f"  ↳ FAIL [{s['name']}] conv={s['conv']}")
            for t in s["turns"]:
                if t["reasons"]:
                    print(f"      turn{t['i']} say={t['say']!r}")
                    print(f"             resp={t['resp_preview']!r}")
                    print(f"             tools={t['tools']} render={t['render']}")
    # quality-triad aggregate
    all_turns = [t for s in results for t in s["turns"]]
    if all_turns:
        lat = sorted(t["latency_ms"] for t in all_turns)
        p50 = lat[len(lat) // 2]
        p95 = lat[min(len(lat) - 1, int(len(lat) * 0.95))]
        tot_in = sum(t["in_tok"] for t in all_turns)
        tot_out = sum(t["out_tok"] for t in all_turns)
        print(f"\n{'-'*100}\nQUALITY TRIAD — {len(all_turns)} turns | "
              f"p50 lat {p50}ms p95 {p95}ms | total in {tot_in} out {tot_out} tok | "
              f"{sum(1 for t in all_turns if t['verdict']=='PASS')}/{len(all_turns)} turns PASS")
    print(f"\nJSON: {out_json}")
    return 0 if n_pass == len(results) else 1


if __name__ == "__main__":
    sys.exit(run())
