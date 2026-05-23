"""DSL evaluation harness — runs a curated battery of NL prompts
through an inline LLM-to-tree translator and reports parse success,
semantic-validation success, readback quality, evaluator output,
and LLM usage per call.

This script is what the future workflows/propose.py extension will
distill into a permanent feature. For now it's a one-off evaluation
tool — run with::

    cd pivot && python -m scripts.dsl_eval

Output goes to stdout as a structured Markdown table plus a JSON
sidecar at /tmp/dsl_eval_results.json.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import textwrap
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

# Load .env first so Azure credentials reach get_llm_client().
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
try:
    from dotenv import load_dotenv  # type: ignore[import-untyped]
    load_dotenv(PROJECT_ROOT / ".env")
except ImportError:
    pass

from pydantic import TypeAdapter, ValidationError

from backend.llm.base import LLMMessage
from backend.llm.factory import get_llm_client
from backend.workflows.dsl.evaluator import Ternary, evaluate
from backend.workflows.dsl.readback import tree_to_english
from backend.workflows.dsl.schema import Tree
from backend.workflows.dsl.validators import DSLValidationError, semantic_validate


# ── The system prompt that teaches the grammar ──────────────────────


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

Supported comparison operators: ">", "<", ">=", "<=", "==", "crosses_above", "crosses_below".

Logic operators: "and", "or" need 2-8 operands; "not" needs exactly 1.

The root MUST be a "comparison" or "logic" node (never a bare leaf).

Hard limits:
  - tree depth ≤ 4 (logic-of-logic-of-comparison-of-leaf is the max)
  - period in [1, 5000]
  - constants must be finite numbers (no NaN, no Infinity)
  - constant <op> constant is rejected (vacuous)

Examples:

User: "RSI of TCS below 30 and NIFTY above 23000"
{ "type": "logic", "op": "and", "operands": [
  { "type": "comparison", "op": "<",
    "left":  { "type": "indicator", "indicator": "rsi", "symbol": "TCS", "period": 14 },
    "right": { "type": "constant", "value": 30 } },
  { "type": "comparison", "op": ">",
    "left":  { "type": "price", "symbol": "NIFTY" },
    "right": { "type": "constant", "value": 23000 } }
]}

User: "MACD of INFY crosses above zero or RSI(14) > 70"
{ "type": "logic", "op": "or", "operands": [
  { "type": "comparison", "op": "crosses_above",
    "left":  { "type": "indicator", "indicator": "macd", "symbol": "INFY", "period": 14 },
    "right": { "type": "constant", "value": 0 } },
  { "type": "comparison", "op": ">",
    "left":  { "type": "indicator", "indicator": "rsi", "symbol": "INFY", "period": 14 },
    "right": { "type": "constant", "value": 70 } }
]}

User: "price of TCS is above its 50-day SMA"
{ "type": "comparison", "op": ">",
  "left":  { "type": "price", "symbol": "TCS" },
  "right": { "type": "indicator", "indicator": "sma", "symbol": "TCS", "period": 50 } }

If the request can't be expressed in the grammar (e.g. requires multi-bar sustain or
multi-timeframe), return the tree anyway with your best mapping, plus an extra top-level
key "warning" with a one-sentence explanation. Tools downstream will validate; partial
trees are useful telemetry.
"""

_PROMPT_CACHE_KEY = "dsl.eval.v1.prompt"


# ── The prompt battery ─────────────────────────────────────────────


PROMPTS: list[tuple[str, str]] = [
    ("P01-AND-canonical", "When the RSI of TCS falls below 30 AND NIFTY is above 23000, buy"),
    ("P02-OR-basic", "Alert me when TCS RSI(14) is above 70 OR its price exceeds 4000"),
    ("P03-NOT", "Don't fire when NIFTY is below 22000"),
    ("P04-crosses-above", "When RSI of INFY crosses above 30"),
    ("P05-crosses-below", "When the MACD of TCS crosses below its signal line — assume signal=0"),
    ("P06-nested-OR-inside-AND", "(TCS RSI is below 30 OR its MACD crossed above zero) AND its price is above the 50-day SMA"),
    ("P07-cross-symbol", "When TCS RSI(14) is lower than INFY RSI(14)"),
    ("P08-volume-spike", "When volume of TCS is above 1,000,000 AND price > 4000"),
    ("P09-three-condition-AND", "RSI<30 AND price<3000 AND volume>500000 for TCS"),
    ("P10-indicator-vs-indicator", "TCS price above its 50-day EMA"),
    ("P11-sustained-NOT-IN-GRAMMAR", "TCS RSI has been below 30 for the last 5 days"),
    ("P12-vague", "When the market looks bullish for TCS"),
    ("P13-hindi", "जब TCS का RSI 30 से नीचे जाए और NIFTY 23000 से ऊपर हो"),
    ("P14-long-compound", "RSI(TCS)<30 AND RSI(INFY)<30 AND NIFTY>22000 AND TCS volume > 500000"),
    ("P15-realistic-multi-clause", "Buy TCS when its RSI is below 30 and its price is above the 20-day SMA, but only if Nifty is above 22000"),
]


# ── Per-prompt result record ───────────────────────────────────────


@dataclass
class PromptResult:
    label: str
    prompt: str
    # LLM call
    llm_input_tokens: int = 0
    llm_output_tokens: int = 0
    llm_cached_tokens: int = 0
    llm_total_tokens: int = 0
    llm_latency_ms: float = 0.0
    llm_error: Optional[str] = None
    raw_response: Optional[str] = None
    # Schema parse
    parsed_ok: bool = False
    parse_error: Optional[str] = None
    # Semantic validate
    semantic_ok: bool = False
    semantic_error: Optional[str] = None
    # Readback
    readback: Optional[str] = None
    warning_from_model: Optional[str] = None
    # Evaluator (against stub data)
    eval_value: Optional[str] = None  # "TRUE" / "FALSE" / "UNKNOWN" / None
    eval_error: Optional[str] = None


# ── Stub data accessor for the evaluator step ──────────────────────


class _SyntheticAccessor:
    """Returns plausible values for every known symbol/indicator so
    we can see how each tree evaluates without touching the network.
    Anything unknown returns None (UNKNOWN), which exercises the
    Kleene-logic path."""

    _PRICES = {
        "TCS": 3850.0, "INFY": 1620.0, "NIFTY": 23250.0, "RELIANCE": 2870.0,
    }
    _INDICATORS = {
        ("TCS", "rsi", 14): 28.5,           # below 30 (oversold)
        ("INFY", "rsi", 14): 42.0,
        ("TCS", "sma", 50): 3700.0,
        ("TCS", "sma", 20): 3800.0,
        ("TCS", "ema", 50): 3720.0,
        ("INFY", "macd", 14): 1.4,
        ("TCS", "macd", 14): 0.2,
    }
    _VOLUMES = {("TCS", 1): 1_250_000.0, ("INFY", 1): 900_000.0}

    def get_price(self, *, symbol, exchange="NSE"):
        return self._PRICES.get(symbol.upper())

    def get_indicator(self, *, symbol, indicator, period, exchange="NSE"):
        return self._INDICATORS.get((symbol.upper(), indicator.lower(), int(period)))

    def get_volume(self, *, symbol, bars=1, exchange="NSE"):
        return self._VOLUMES.get((symbol.upper(), int(bars)))


# ── Pipeline ───────────────────────────────────────────────────────


_TREE_ADAPTER = TypeAdapter(Tree)


async def run_one(label: str, prompt: str) -> PromptResult:
    out = PromptResult(label=label, prompt=prompt)
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
            prompt_cache_key=_PROMPT_CACHE_KEY,
        )
    except Exception as exc:  # noqa: BLE001
        out.llm_error = f"{type(exc).__name__}: {exc}"
        out.llm_latency_ms = (time.time() - t0) * 1000.0
        return out

    out.llm_latency_ms = (
        float(resp.latency_ms) if getattr(resp, "latency_ms", None) is not None
        else (time.time() - t0) * 1000.0
    )
    out.llm_input_tokens = int(getattr(resp, "input_tokens", 0) or 0)
    out.llm_output_tokens = int(getattr(resp, "output_tokens", 0) or 0)
    out.llm_cached_tokens = int(getattr(resp, "cached_tokens", 0) or 0)
    out.llm_total_tokens = out.llm_input_tokens + out.llm_output_tokens
    out.raw_response = (resp.content or "")[:1200]

    # Strip the optional "warning" key the system prompt allows.
    try:
        raw = json.loads((resp.content or "").strip())
    except json.JSONDecodeError as exc:
        out.parse_error = f"not JSON: {exc}"
        return out
    if isinstance(raw, dict) and "warning" in raw:
        out.warning_from_model = str(raw.pop("warning"))[:300]

    # Pydantic parse.
    try:
        tree = _TREE_ADAPTER.validate_python(raw)
        out.parsed_ok = True
    except ValidationError as exc:
        out.parse_error = str(exc).splitlines()[0][:240]
        return out

    # Semantic validate.
    try:
        semantic_validate(tree)
        out.semantic_ok = True
    except DSLValidationError as exc:
        out.semantic_error = str(exc)[:240]
        # Still produce a readback + evaluation for visibility.

    out.readback = tree_to_english(tree)

    try:
        ev = evaluate(tree, accessor=_SyntheticAccessor())
        out.eval_value = ev.value.name
    except Exception as exc:  # noqa: BLE001
        out.eval_error = f"{type(exc).__name__}: {exc}"

    return out


# ── Reporting ──────────────────────────────────────────────────────


def _short(s: Optional[str], n: int = 70) -> str:
    if s is None:
        return ""
    s = s.strip().replace("\n", " ")
    return s if len(s) <= n else s[: n - 1] + "…"


def render_table(results: list[PromptResult]) -> str:
    lines: list[str] = []
    lines.append("# DSL evaluation — 15 prompts, real Azure LLM")
    lines.append("")
    lines.append(f"Run completed at: {time.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    lines.append("")
    # Headline counters
    ok_parse = sum(1 for r in results if r.parsed_ok)
    ok_sem = sum(1 for r in results if r.semantic_ok)
    ok_fully = sum(1 for r in results if r.parsed_ok and r.semantic_ok)
    total_input = sum(r.llm_input_tokens for r in results)
    total_output = sum(r.llm_output_tokens for r in results)
    total_cached = sum(r.llm_cached_tokens for r in results)
    avg_latency = (
        sum(r.llm_latency_ms for r in results) / len(results) if results else 0.0
    )
    lines.append("## Headline")
    lines.append("")
    lines.append(f"- prompts attempted: **{len(results)}**")
    lines.append(f"- Pydantic-parsed cleanly: **{ok_parse}/{len(results)}**")
    lines.append(f"- semantic-validated cleanly: **{ok_sem}/{len(results)}**")
    lines.append(f"- end-to-end (parse + semantic + readback): **{ok_fully}/{len(results)}**")
    lines.append(f"- total tokens: **{total_input + total_output:,}** "
                 f"({total_input:,} in + {total_output:,} out, "
                 f"{total_cached:,} cached)")
    lines.append(f"- average latency per call: **{avg_latency:.0f} ms**")
    lines.append("")
    # Per-prompt detail
    lines.append("## Per-prompt detail")
    lines.append("")
    lines.append(
        "| # | label | prompt | parse | sem | eval | tokens (in/out/cached) | ms |"
    )
    lines.append(
        "|---|---|---|---|---|---|---|---|"
    )
    for i, r in enumerate(results, 1):
        parse = "✓" if r.parsed_ok else "✗"
        sem = "✓" if r.semantic_ok else "✗"
        ev = r.eval_value or "—"
        toks = f"{r.llm_input_tokens}/{r.llm_output_tokens}/{r.llm_cached_tokens}"
        ms = f"{r.llm_latency_ms:.0f}"
        lines.append(
            f"| {i:02d} | `{r.label}` | {_short(r.prompt, 56)} | {parse} | {sem} | {ev} | {toks} | {ms} |"
        )
    lines.append("")
    # Per-prompt readback + warnings + errors
    lines.append("## Readback (what the user would see in the confirmation card)")
    lines.append("")
    for r in results:
        lines.append(f"### `{r.label}`")
        lines.append(f"> {r.prompt}")
        if r.llm_error:
            lines.append(f"- **LLM error:** `{r.llm_error}`")
        if r.parse_error:
            lines.append(f"- **Pydantic parse failed:** `{_short(r.parse_error, 200)}`")
        if r.semantic_error:
            lines.append(f"- **Semantic validation failed:** `{_short(r.semantic_error, 200)}`")
        if r.warning_from_model:
            lines.append(f"- **Model warning:** `{_short(r.warning_from_model, 200)}`")
        if r.readback:
            lines.append(f"- **Readback:** {r.readback}")
        if r.eval_value:
            lines.append(f"- **Evaluator on synthetic data:** `{r.eval_value}`")
        if r.eval_error:
            lines.append(f"- **Evaluator error:** `{r.eval_error}`")
        lines.append("")
    return "\n".join(lines)


async def main() -> int:
    results: list[PromptResult] = []
    for label, prompt in PROMPTS:
        print(f"  running {label} ...", file=sys.stderr, flush=True)
        results.append(await run_one(label, prompt))

    # Write JSON sidecar for any downstream analysis.
    out_json = "/tmp/dsl_eval_results.json"
    with open(out_json, "w", encoding="utf-8") as fh:
        json.dump([asdict(r) for r in results], fh, indent=2, default=str)
    print(f"\n# JSON sidecar at {out_json}\n", file=sys.stderr)

    print(render_table(results))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
