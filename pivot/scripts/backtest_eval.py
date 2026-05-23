"""Backtest evaluation harness — runs ~15 diverse strategies end-to-end.

For each prompt, the harness:

  1. Calls Azure to translate the natural-language strategy into a
     DSL tree (same system prompt the dsl_eval harness uses).
  2. POSTs the tree + a static BacktestRequest envelope to the
     running backend at /api/backtest/dsl/run.
  3. Records LLM tokens / latency, backtest runtime, the result's
     headline metrics (total return, drawdown, trades, win rate),
     and any errors.

Output: stdout markdown table + a JSON sidecar at
/tmp/backtest_eval_results.json.

Prerequisites:
  - Backend is running on http://localhost:8000 with the
    /api/backtest/dsl/* routes mounted (Phase B).
  - Network access to yfinance (the engine pulls real OHLCV).
  - A user account that can be JWT-authed via /auth/register.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

# Load .env so Azure credentials reach get_llm_client.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
try:
    from dotenv import load_dotenv  # type: ignore[import-untyped]
    load_dotenv(PROJECT_ROOT / ".env")
except ImportError:
    pass

from backend.llm.base import LLMMessage
from backend.llm.factory import get_llm_client


BASE = "http://localhost:8000"


# ── System prompt (same as the DSL eval — we want comparable token use)


_SYSTEM_PROMPT = """You translate natural-language trading conditions into Pivot's DSL — a small JSON tree of expressions.

Return ONLY a single JSON object representing the tree, no commentary, no markdown fences.

The tree is built from six node types, each tagged with a "type" field:

  { "type": "indicator", "indicator": "<KEY>", "symbol": "<SYM>", "period": <int>, "exchange": "NSE" }
  { "type": "price", "symbol": "<SYM>", "exchange": "NSE" }
  { "type": "volume", "symbol": "<SYM>", "bars": <int>, "exchange": "NSE" }
  { "type": "constant", "value": <number> }
  { "type": "comparison", "op": "<OP>", "left": <node>, "right": <node> }
  { "type": "logic", "op": "and"|"or"|"not", "operands": [<node>, ...] }

Supported indicator keys: rsi, sma, ema, macd, atr, adx, aroon, bb, cci, donchian, keltner, mfi, obv, psar, roc, stoch, stoch_rsi, supertrend, trix, volume, volume_ma, volume_roc, vwap, williams_r, wma.

Multi-output indicators accept an optional "component" field to pick a specific output. Single-output indicators (rsi, sma, ema, atr, adx, cci, mfi, roc, supertrend, trix, williams_r, wma, vwap, obv, psar, volume, volume_ma, volume_roc) MUST omit this field.
  - bb:        upper, middle, lower, pctb (default), bandwidth   — pctb is the 0..1 Percent-B; use upper/middle/lower for actual price-comparable bands.
  - macd:      macd, signal, hist (default)                       — hist is the histogram (macd − signal); 0 = crossover.
  - stoch:     k (default), d
  - stoch_rsi: k (default), d
  - aroon:     up, down, osc (default)
  - donchian:  upper, middle (default), lower
  - keltner:   upper, middle (default), lower

Example — "buy NIFTYBEES when its price drops below the lower Bollinger band, 20-day":
  { "type":"comparison", "op":"<",
    "left":  { "type":"price", "symbol":"NIFTYBEES" },
    "right": { "type":"indicator", "indicator":"bb", "symbol":"NIFTYBEES", "period":20, "component":"lower" } }

Supported comparison operators: ">", "<", ">=", "<=", "==", "crosses_above", "crosses_below".

Logic operators: "and", "or" need 2-8 operands; "not" needs exactly 1.

The root MUST be a "comparison" or "logic" node.

Hard limits: tree depth ≤ 4; period in [1, 5000]; constants finite; constant <op> constant rejected.

The tree expresses ONLY the ENTRY condition. Exits are configured separately on the request and may also be a tree — see the EXIT GRAMMAR below if asked, but DO NOT emit an exit tree unless the user explicitly asks for one as part of this turn.

EXIT GRAMMAR (only when the request is explicitly about an exit condition):
  Exit trees use the same six node types PLUS a seventh:
  { "type": "position", "field": "<F>", "basis": "close"|"low"|"high" }
  Fields:
    entry_price           — entry price (₹), constant for the position's life
    unrealised_pct        — (current_price - entry_price)/entry_price, signed
    unrealised_abs        — current_price - entry_price (₹)
    bars_held             — integer count of bars since entry
    peak_unrealised_pct   — running max of unrealised_pct
    drawdown_from_peak_pct— peak_unrealised_pct - unrealised_pct, non-negative
  Use basis="low" for stop-style checks, basis="high" for target-style checks,
  basis="close" (default) for general reads. Basis is only valid for
  unrealised_pct / unrealised_abs.
"""


# ── 15 backtest scenarios ──────────────────────────────────────────


@dataclass
class Scenario:
    label: str
    prompt: str
    primary_symbol: str
    start_date: str
    end_date: str
    exit_policy: dict
    starting_capital: float = 100_000.0
    quantity: int = 10


SCENARIOS: list[Scenario] = [
    Scenario(
        "S01-RSI-oversold-TCS",
        "Buy TCS when its 14-day RSI drops below 30",
        primary_symbol="TCS", start_date="2023-01-01", end_date="2025-12-31",
        exit_policy={"kind": "n_day_hold", "bars": 5},
    ),
    Scenario(
        "S02-RSI-with-stop-INFY",
        "Buy INFY when RSI(14) goes below 30",
        primary_symbol="INFY", start_date="2023-01-01", end_date="2025-12-31",
        exit_policy={"kind": "stop_loss_pct", "value": 0.05},
    ),
    Scenario(
        "S03-SMA-trend-RELIANCE",
        "Buy RELIANCE when its price is above its 50-day SMA",
        primary_symbol="RELIANCE", start_date="2023-01-01", end_date="2025-12-31",
        exit_policy={"kind": "n_day_hold", "bars": 20},
    ),
    Scenario(
        "S04-mean-revert-NIFTYBEES",
        "Buy NIFTYBEES when its 14-day RSI is below 35",
        primary_symbol="NIFTYBEES", start_date="2023-01-01", end_date="2025-12-31",
        exit_policy={"kind": "n_day_hold", "bars": 5},
    ),
    Scenario(
        "S05-double-condition-HDFC",
        "Buy HDFCBANK when its price is above the 200-day SMA AND its RSI(14) is above 50",
        primary_symbol="HDFCBANK", start_date="2023-01-01", end_date="2025-12-31",
        exit_policy={"kind": "n_day_hold", "bars": 10},
    ),
    Scenario(
        "S06-EMA-cross-ICICI",
        "Buy ICICIBANK when its closing price is below its 50-day EMA",
        primary_symbol="ICICIBANK", start_date="2023-01-01", end_date="2025-12-31",
        exit_policy={"kind": "n_day_hold", "bars": 10},
    ),
    Scenario(
        "S07-volume-spike-TCS",
        "Buy TCS when single-bar volume is above 1,000,000 AND RSI(14) is below 40",
        primary_symbol="TCS", start_date="2023-01-01", end_date="2025-12-31",
        exit_policy={"kind": "stop_loss_pct", "value": 0.03},
    ),
    Scenario(
        "S08-MACD-cross-INFY",
        "Buy INFY when its MACD(12) crosses above 0",
        primary_symbol="INFY", start_date="2023-01-01", end_date="2025-12-31",
        exit_policy={"kind": "n_day_hold", "bars": 15},
    ),
    Scenario(
        "S09-multi-symbol-gate-TCS",
        "Buy TCS when its RSI(14) is below 30 AND NIFTY price is above 22000",
        primary_symbol="TCS", start_date="2023-01-01", end_date="2025-12-31",
        exit_policy={"kind": "n_day_hold", "bars": 5},
    ),
    Scenario(
        "S10-cross-symbol-RSI-TCS-INFY",
        "Buy TCS when its RSI(14) is lower than INFY's RSI(14)",
        primary_symbol="TCS", start_date="2023-01-01", end_date="2025-12-31",
        exit_policy={"kind": "n_day_hold", "bars": 7},
    ),
    Scenario(
        "S11-Hindi-RSI-TCS",
        "TCS का 14-day RSI 30 से नीचे जाए तो खरीदो",
        primary_symbol="TCS", start_date="2023-01-01", end_date="2025-12-31",
        exit_policy={"kind": "n_day_hold", "bars": 5},
    ),
    Scenario(
        "S12-triple-condition-RELIANCE",
        "Buy RELIANCE when RSI(14) is below 30 AND price is above 200-day SMA AND single-bar volume > 500,000",
        primary_symbol="RELIANCE", start_date="2023-01-01", end_date="2025-12-31",
        exit_policy={"kind": "n_day_hold", "bars": 10},
    ),
    Scenario(
        "S13-crosses-below-RSI-TCS",
        "Buy TCS the moment its 14-day RSI crosses below 30",
        primary_symbol="TCS", start_date="2023-01-01", end_date="2025-12-31",
        exit_policy={"kind": "n_day_hold", "bars": 7},
    ),
    Scenario(
        "S14-long-horizon-HDFC",
        "Buy HDFCBANK when its closing price is below the 50-day SMA",
        primary_symbol="HDFCBANK", start_date="2022-01-01", end_date="2025-12-31",
        exit_policy={"kind": "n_day_hold", "bars": 30},
    ),
    Scenario(
        "S15-bollinger-NIFTYBEES",
        "Buy NIFTYBEES when its price drops below the lower Bollinger Band (bb period 20)",
        primary_symbol="NIFTYBEES", start_date="2023-01-01", end_date="2025-12-31",
        exit_policy={"kind": "n_day_hold", "bars": 7},
    ),

    # ── E-series: exit-tree scenarios (Phase B+1) ─────────────────────
    # Entry tree comes from the LLM as usual; exit_policy is a
    # hardcoded tree so we test the engine's new exit path against
    # real OHLCV. Exit-prompt NL translation is a follow-up.

    Scenario(
        "E01-RSI-entry-RSI-exit-TCS",
        "Buy TCS when its 14-day RSI drops below 30",
        primary_symbol="TCS", start_date="2023-01-01", end_date="2025-12-31",
        exit_policy={
            "kind": "tree",
            "tree": {
                "type": "comparison", "op": ">",
                "left": {"type": "indicator", "indicator": "rsi",
                          "symbol": "TCS", "period": 14},
                "right": {"type": "constant", "value": 70},
            },
            "exit_at": "next_open",
        },
    ),
    Scenario(
        "E02-trailing-stop-NIFTYBEES",
        "Buy NIFTYBEES when its 14-day RSI is below 35",
        primary_symbol="NIFTYBEES", start_date="2023-01-01", end_date="2025-12-31",
        exit_policy={
            "kind": "tree",
            "tree": {
                "type": "comparison", "op": ">=",
                "left": {"type": "position",
                          "field": "drawdown_from_peak_pct"},
                "right": {"type": "constant", "value": 0.05},
            },
            "exit_at": "next_open",
        },
    ),
    Scenario(
        "E03-target-or-stop-or-time-RELIANCE",
        "Buy RELIANCE when its price is above its 50-day SMA",
        primary_symbol="RELIANCE", start_date="2023-01-01", end_date="2025-12-31",
        exit_policy={
            "kind": "tree",
            "tree": {
                "type": "logic", "op": "or", "operands": [
                    {"type": "comparison", "op": ">=",
                     "left": {"type": "position", "field": "unrealised_pct",
                              "basis": "high"},
                     "right": {"type": "constant", "value": 0.08}},
                    {"type": "comparison", "op": "<=",
                     "left": {"type": "position", "field": "unrealised_pct",
                              "basis": "low"},
                     "right": {"type": "constant", "value": -0.04}},
                    {"type": "comparison", "op": ">=",
                     "left": {"type": "position", "field": "bars_held"},
                     "right": {"type": "constant", "value": 30}},
                ],
            },
            "exit_at": "next_open",
        },
    ),
    Scenario(
        "E04-MACD-reversal-exit-ICICI",
        "Buy ICICIBANK when its closing price is below its 50-day EMA",
        primary_symbol="ICICIBANK", start_date="2023-01-01", end_date="2025-12-31",
        exit_policy={
            "kind": "tree",
            "tree": {
                "type": "comparison", "op": "crosses_below",
                "left": {"type": "indicator", "indicator": "macd",
                          "symbol": "ICICIBANK", "period": 26,
                          "component": "macd"},
                "right": {"type": "indicator", "indicator": "macd",
                          "symbol": "ICICIBANK", "period": 26,
                          "component": "signal"},
            },
            "exit_at": "next_open",
        },
    ),
    Scenario(
        "E05-bars-or-drawdown-HDFC",
        "Buy HDFCBANK when its price is above the 200-day SMA AND its RSI(14) is above 50",
        primary_symbol="HDFCBANK", start_date="2023-01-01", end_date="2025-12-31",
        exit_policy={
            "kind": "tree",
            "tree": {
                "type": "logic", "op": "or", "operands": [
                    {"type": "comparison", "op": ">=",
                     "left": {"type": "position", "field": "bars_held"},
                     "right": {"type": "constant", "value": 60}},
                    {"type": "comparison", "op": ">=",
                     "left": {"type": "position",
                              "field": "drawdown_from_peak_pct"},
                     "right": {"type": "constant", "value": 0.08}},
                ],
            },
            "exit_at": "next_open",
        },
    ),
]


# ── Result schema ──────────────────────────────────────────────────


@dataclass
class ScenarioResult:
    label: str
    prompt: str
    primary_symbol: str
    # LLM step
    llm_input_tokens: int = 0
    llm_output_tokens: int = 0
    llm_latency_ms: float = 0.0
    llm_error: Optional[str] = None
    tree: Optional[dict] = None
    tree_summary: Optional[str] = None
    # Backtest step
    http_status: int = 0
    backtest_latency_ms: float = 0.0
    error: Optional[str] = None
    total_return_pct: Optional[float] = None
    cagr_pct: Optional[float] = None
    max_drawdown_pct: Optional[float] = None
    win_rate_pct: Optional[float] = None
    total_trades: Optional[int] = None
    winning_trades: Optional[int] = None
    losing_trades: Optional[int] = None
    bars_evaluated: Optional[int] = None
    fire_bars: Optional[int] = None
    unknown_value_bars: Optional[int] = None
    ending_value: Optional[float] = None


# ── Step 1: translate prompt → tree via the LLM ───────────────────


async def translate(prompt: str) -> tuple[Optional[dict], int, int, float, Optional[str]]:
    client = get_llm_client()
    t0 = time.time()
    try:
        resp = await client.complete(
            messages=[
                LLMMessage(role="system", content=_SYSTEM_PROMPT),
                LLMMessage(role="user", content=prompt),
            ],
            response_format="json_object",
            reasoning_effort="minimal",
            temperature=0.0,
            max_output_tokens=900,
            prompt_cache_key="backtest.eval.v1",
        )
    except Exception as exc:  # noqa: BLE001
        return None, 0, 0, (time.time() - t0) * 1000.0, f"{type(exc).__name__}: {exc}"
    latency = (
        float(resp.latency_ms) if getattr(resp, "latency_ms", None) is not None
        else (time.time() - t0) * 1000.0
    )
    raw = (resp.content or "").strip()
    try:
        tree = json.loads(raw)
    except json.JSONDecodeError as exc:
        return None, int(resp.input_tokens or 0), int(resp.output_tokens or 0), latency, f"not JSON: {exc}"
    if isinstance(tree, dict) and "warning" in tree:
        tree.pop("warning", None)
    return tree, int(resp.input_tokens or 0), int(resp.output_tokens or 0), latency, None


# ── Step 2: register a fresh user, POST the backtest ──────────────


def _register_user() -> str:
    """Returns a JWT bearer token for a fresh test user."""
    body = {
        "email": f"bt_{int(time.time())}_{os.getpid()}@pivot.com",
        "password": "password123",
        "full_name": "Backtest Eval",
    }
    req = urllib.request.Request(
        f"{BASE}/auth/register", method="POST",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())["access_token"]


def post_backtest(
    *, token: str, scenario: Scenario, tree: dict,
) -> tuple[int, dict, float]:
    body = {
        "tree": tree,
        "primary_symbol": scenario.primary_symbol,
        "start_date": scenario.start_date,
        "end_date": scenario.end_date,
        "starting_capital": scenario.starting_capital,
        "quantity": scenario.quantity,
        "exit_policy": scenario.exit_policy,
        "save": True,
    }
    req = urllib.request.Request(
        f"{BASE}/api/backtest/dsl/run", method="POST",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
    )
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            payload = json.loads(resp.read())
            return resp.status, payload, (time.time() - t0) * 1000.0
    except urllib.error.HTTPError as e:
        try:
            payload = json.loads(e.read())
        except Exception:
            payload = {"raw": "<unreadable error>"}
        return e.code, payload, (time.time() - t0) * 1000.0


# ── Step 3: run one scenario end-to-end ────────────────────────────


async def run_scenario(token: str, sc: Scenario) -> ScenarioResult:
    out = ScenarioResult(
        label=sc.label, prompt=sc.prompt, primary_symbol=sc.primary_symbol,
    )
    tree, in_tok, out_tok, latency, err = await translate(sc.prompt)
    out.llm_input_tokens = in_tok
    out.llm_output_tokens = out_tok
    out.llm_latency_ms = latency
    if err is not None:
        out.llm_error = err
        return out
    if tree is None:
        out.llm_error = "no tree returned"
        return out
    out.tree = tree

    status, payload, bt_latency = post_backtest(
        token=token, scenario=sc, tree=tree,
    )
    out.http_status = status
    out.backtest_latency_ms = bt_latency
    if status != 200:
        out.error = payload.get("detail") or json.dumps(payload)[:200]
        return out

    out.tree_summary = payload.get("tree_summary")
    m = payload.get("metrics") or {}
    out.total_return_pct = m.get("total_return_pct")
    out.cagr_pct = m.get("cagr_pct")
    out.max_drawdown_pct = m.get("max_drawdown_pct")
    out.win_rate_pct = m.get("win_rate_pct")
    out.total_trades = m.get("total_trades")
    out.winning_trades = m.get("winning_trades")
    out.losing_trades = m.get("losing_trades")
    out.ending_value = m.get("ending_value")

    d = payload.get("diagnostics") or {}
    out.bars_evaluated = d.get("bars_evaluated")
    out.fire_bars = d.get("fire_bars")
    out.unknown_value_bars = d.get("unknown_value_bars")
    return out


# ── Reporting ──────────────────────────────────────────────────────


def _short(s: Optional[str], n: int = 60) -> str:
    if s is None:
        return ""
    s = str(s).strip().replace("\n", " ")
    return s if len(s) <= n else s[: n - 1] + "…"


def _fmt_pct(v: Optional[float]) -> str:
    if v is None:
        return "—"
    return f"{v:+.2f}%"


def render(results: list[ScenarioResult]) -> str:
    lines: list[str] = []
    lines.append("# Backtest evaluation — 15 strategies, live yfinance + Phase-B engine")
    lines.append("")
    lines.append(f"Run completed at: {time.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    lines.append("")
    ok_llm = sum(1 for r in results if r.tree is not None)
    ok_bt = sum(1 for r in results if r.http_status == 200)
    total_in = sum(r.llm_input_tokens for r in results)
    total_out = sum(r.llm_output_tokens for r in results)
    total_bt_ms = sum(r.backtest_latency_ms for r in results)
    lines.append("## Headline")
    lines.append("")
    lines.append(f"- prompts attempted: **{len(results)}**")
    lines.append(f"- tree-translation success: **{ok_llm}/{len(results)}**")
    lines.append(f"- backtest run success: **{ok_bt}/{len(results)}**")
    lines.append(f"- LLM tokens total: **{total_in + total_out:,}** ({total_in:,} in + {total_out:,} out)")
    lines.append(f"- backtest engine wall-clock total: **{total_bt_ms / 1000.0:.1f} s**")
    lines.append("")
    lines.append("## Per-scenario detail")
    lines.append("")
    lines.append(
        "| # | label | symbol | trades | win% | return | drawdown | bars_eval | "
        "fire_bars | bt_ms | http |"
    )
    lines.append(
        "|---|---|---|---|---|---|---|---|---|---|---|"
    )
    for i, r in enumerate(results, 1):
        trades = r.total_trades if r.total_trades is not None else "—"
        winp = (
            f"{r.win_rate_pct:.0f}%" if r.win_rate_pct is not None else "—"
        )
        ret = _fmt_pct(r.total_return_pct)
        dd = _fmt_pct(-r.max_drawdown_pct) if r.max_drawdown_pct is not None else "—"
        bars = r.bars_evaluated if r.bars_evaluated is not None else "—"
        fires = r.fire_bars if r.fire_bars is not None else "—"
        ms = f"{r.backtest_latency_ms:.0f}"
        http = r.http_status if r.http_status else "—"
        lines.append(
            f"| {i:02d} | `{r.label}` | {r.primary_symbol} | {trades} | {winp} | "
            f"{ret} | {dd} | {bars} | {fires} | {ms} | {http} |"
        )
    lines.append("")
    lines.append("## Readback + raw metrics per scenario")
    lines.append("")
    for r in results:
        lines.append(f"### `{r.label}`")
        lines.append(f"> {r.prompt}")
        if r.llm_error:
            lines.append(f"- **LLM error:** `{_short(r.llm_error, 200)}`")
        if r.tree_summary:
            lines.append(f"- **Readback:** {r.tree_summary}")
        if r.error:
            lines.append(f"- **Backtest error:** `{_short(r.error, 200)}`")
        if r.total_trades is not None:
            lines.append(
                f"- **Run:** {r.total_trades} trades  "
                f"({r.winning_trades or 0}W / {r.losing_trades or 0}L)  "
                f"return {_fmt_pct(r.total_return_pct)}  "
                f"max DD {_fmt_pct(-r.max_drawdown_pct) if r.max_drawdown_pct is not None else '—'}  "
                f"ending ₹{r.ending_value:,.0f}"
            )
        if r.bars_evaluated is not None:
            lines.append(
                f"- **Diagnostics:** {r.bars_evaluated} bars evaluated, "
                f"{r.fire_bars} bars fired, "
                f"{r.unknown_value_bars} UNKNOWN-result bars"
            )
        lines.append(
            f"- **LLM:** {r.llm_input_tokens} in / {r.llm_output_tokens} out, "
            f"{r.llm_latency_ms:.0f} ms"
        )
        lines.append(
            f"- **Engine wall-clock:** {r.backtest_latency_ms:.0f} ms"
        )
        lines.append("")
    return "\n".join(lines)


# ── Main ───────────────────────────────────────────────────────────


async def main() -> int:
    token = _register_user()
    print(f"registered user, token length={len(token)}", file=sys.stderr)
    results: list[ScenarioResult] = []
    for sc in SCENARIOS:
        print(f"  running {sc.label} ...", file=sys.stderr, flush=True)
        r = await run_scenario(token, sc)
        results.append(r)
        # tiny sleep so yfinance doesn't see a burst
        await asyncio.sleep(0.5)

    with open("/tmp/backtest_eval_results.json", "w") as fh:
        json.dump([asdict(r) for r in results], fh, indent=2, default=str)
    print("\n# JSON sidecar at /tmp/backtest_eval_results.json\n", file=sys.stderr)

    print(render(results))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
