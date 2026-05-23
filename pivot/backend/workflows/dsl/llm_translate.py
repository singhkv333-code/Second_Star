"""Helper to translate natural-language trading conditions into DSL trees.

Single source of truth for the prompt used by the chat-side tools
(``backtest_dsl_tree`` / ``propose_dsl_workflow``) and the offline eval
harness in ``scripts/backtest_eval.py``.

When the grammar grows, change the prompt here and both callers pick it
up.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """You translate natural-language trading conditions into Pivot's DSL — a small JSON tree of expressions.

Return ONLY a single JSON object representing the tree, no commentary, no markdown fences.

The tree is built from these node types, each tagged with a "type" field:

  { "type": "indicator", "indicator": "<KEY>", "symbol": "<SYM>", "period": <int>, "exchange": "NSE", "offset": <int> }
  { "type": "price", "symbol": "<SYM>", "exchange": "NSE", "basis": "open"|"high"|"low"|"close", "offset": <int> }
  { "type": "volume", "symbol": "<SYM>", "bars": <int>, "exchange": "NSE", "offset": <int> }
  { "type": "constant", "value": <number> }
  { "type": "comparison", "op": "<OP>", "left": <node>, "right": <node> }
  { "type": "logic", "op": "and"|"or"|"not", "operands": [<node>, ...] }
  { "type": "conditional", "if": <bool-node>, "then": <node>, "else": <node> }
  { "type": "aggregate", "op": "<AGG>", "source": <node>, "bars": <int>, "second": <node> }

Time-shifted access: every leaf accepts an optional "offset" (default 0). offset=1 reads the previous bar; max 500.
Price leaves also accept "basis" (default "close"). Use basis="open" for gap conditions, "low"/"high" for stop / target checks.

Aggregate ops (with what they return and what 'source' must yield):
  - highest, lowest, sum, avg, std         : numeric source → number
  - percentrank, zscore                    : numeric source → number (percentrank is 0..1, fraction of window strictly below current value)
  - count_when, any_when                   : BOOLEAN source → count / 1.0-or-0.0
  - barssince                              : BOOLEAN source → integer (bars since last TRUE in window). UNKNOWN if never observed.
  - valuewhen                              : BOOLEAN source + numeric 'second' → value of 'second' at last TRUE bar
  - correlation                            : numeric source + numeric 'second' → Pearson r over the window

Supported indicator keys: rsi, sma, ema, macd, atr, adx, aroon, bb, cci, donchian, keltner, mfi, obv, psar, roc, stoch, stoch_rsi, supertrend, trix, volume, volume_ma, volume_roc, vwap, williams_r, wma.

Multi-output indicators accept an optional "component" field to pick a specific output. Single-output indicators (rsi, sma, ema, atr, adx, cci, mfi, roc, supertrend, trix, williams_r, wma, vwap, obv, psar, volume, volume_ma, volume_roc) MUST omit this field.
  - bb:        upper, middle, lower, pctb (default), bandwidth
  - macd:      macd, signal, hist (default)
  - stoch:     k (default), d
  - stoch_rsi: k (default), d
  - aroon:     up, down, osc (default)
  - donchian:  upper, middle (default), lower
  - keltner:   upper, middle (default), lower

Supported comparison operators: ">", "<", ">=", "<=", "==", "crosses_above", "crosses_below".

Logic operators: "and", "or" need 2-8 operands; "not" needs exactly 1.

The root MUST be a "comparison" or "logic" node.

Hard limits: tree depth ≤ 6; period in [1, 5000]; aggregate bars in [1, 2000]; offset in [0, 500]; constants finite; constant <op> constant rejected.

Guidance on "reaches the band" / "touches the band" — prefer ">=" / "<=" or "crosses_above" / "crosses_below" rather than "==" (strict equality on floats rarely fires).

Examples:

  "buy NIFTYBEES when its price drops below the lower Bollinger band, 20-day":
    { "type":"comparison", "op":"<",
      "left":  { "type":"price", "symbol":"NIFTYBEES" },
      "right": { "type":"indicator", "indicator":"bb", "symbol":"NIFTYBEES", "period":20, "component":"lower" } }

  "20-day high breakout":
    { "type":"comparison", "op":">=",
      "left":  { "type":"price", "symbol":"TCS" },
      "right": { "type":"aggregate", "op":"highest",
                 "source": { "type":"price", "symbol":"TCS", "offset":1 },
                 "bars":20 } }

  "ATR(14) is in the top 30% of its last-252-bar distribution":
    { "type":"comparison", "op":">",
      "left":  { "type":"aggregate", "op":"percentrank",
                 "source": { "type":"indicator", "indicator":"atr",
                             "symbol":"NIFTY", "period":14 },
                 "bars":252 },
      "right": { "type":"constant", "value":0.7 } }

  "bars since RSI was last below 30 is at most 3":
    { "type":"comparison", "op":"<=",
      "left":  { "type":"aggregate", "op":"barssince",
                 "source": { "type":"comparison", "op":"<",
                             "left": { "type":"indicator", "indicator":"rsi",
                                       "symbol":"TCS", "period":14 },
                             "right": { "type":"constant", "value":30 } },
                 "bars":60 },
      "right": { "type":"constant", "value":3 } }

The tree expresses ONLY the ENTRY condition. Exits are configured separately.

EXIT GRAMMAR (only when the user is explicitly describing an exit condition):
  Exit trees may also reference the open position via a new leaf:
    { "type": "position",
      "field": "entry_price"|"unrealised_pct"|"unrealised_abs"|"bars_held"|"peak_unrealised_pct"|"drawdown_from_peak_pct",
      "basis": "close"|"low"|"high"   // only for unrealised_pct / unrealised_abs
    }
"""


class TranslationError(ValueError):
    """Raised when the LLM didn't produce parseable JSON for the tree."""


async def translate_condition_to_tree(
    condition: str,
    *,
    allow_position: bool = False,
    cache_key: str = "dsl.translate.v1",
) -> tuple[dict, dict[str, Any]]:
    """Hand a natural-language condition to the LLM and return its DSL
    tree as a Python dict.

    Returns ``(tree, meta)`` where ``meta`` has ``input_tokens``,
    ``output_tokens``, ``latency_ms``. Raises ``TranslationError`` if the
    LLM's reply isn't valid JSON.

    ``allow_position`` is a hint to the prompt — when True, the LLM is
    permitted to emit the ``position`` leaf (exit-tree context).
    """
    # Lazy import — avoids pulling the LLM stack into modules that just
    # need to know the prompt exists.
    from backend.llm.base import LLMMessage
    from backend.llm.factory import get_llm_client
    import time

    if not condition or not condition.strip():
        raise TranslationError("empty condition")

    sys_prompt = SYSTEM_PROMPT
    if allow_position:
        sys_prompt = (
            sys_prompt
            + "\n\nThis turn IS asking for an exit condition — you may "
              "use the 'position' leaf as documented in the EXIT GRAMMAR "
              "section above."
        )

    client = get_llm_client()
    t0 = time.time()
    resp = await client.complete(
        messages=[
            LLMMessage(role="system", content=sys_prompt),
            LLMMessage(role="user", content=condition),
        ],
        response_format="json_object",
        reasoning_effort="minimal",
        temperature=0.0,
        max_output_tokens=1200,
        prompt_cache_key=cache_key,
    )
    elapsed_ms = (
        float(resp.latency_ms)
        if getattr(resp, "latency_ms", None) is not None
        else (time.time() - t0) * 1000.0
    )

    raw = (resp.content or "").strip()
    try:
        tree = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise TranslationError(
            f"LLM returned non-JSON content: {exc}"
        ) from None

    if isinstance(tree, dict) and "warning" in tree:
        tree.pop("warning", None)

    if not isinstance(tree, dict):
        raise TranslationError(
            f"expected JSON object, got {type(tree).__name__}"
        )

    meta = {
        "input_tokens": int(resp.input_tokens or 0),
        "output_tokens": int(resp.output_tokens or 0),
        "latency_ms": elapsed_ms,
    }
    logger.info(
        "[dsl.translate] condition=%r tokens_in=%d tokens_out=%d ms=%.0f",
        condition[:80], meta["input_tokens"], meta["output_tokens"],
        meta["latency_ms"],
    )
    return tree, meta
