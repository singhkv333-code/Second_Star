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
  { "type": "session_day", "days": ["mon"|"tue"|"wed"|"thu"|"fri"|"sat"|"sun", ...] }   // boolean: TRUE on listed weekdays
  { "type": "gap", "symbol": "<SYM>" }                                          // (open - prev_close) / prev_close, signed
  { "type": "pct_change", "symbol": "<SYM>", "bars": <int> }                    // (close - close[bars]) / close[bars]
  { "type": "spread", "a": "<SYM_A>", "b": "<SYM_B>" }                          // price_a / price_b
  { "type": "math", "op": "+"|"-"|"*"|"/"|"abs"|"negate"|"min"|"max", "operands": [<node>, ...] }
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

Day-of-week filters: when the user says "on Tuesday", "every Monday", "Mon-Wed", etc., use the SESSION_DAY leaf:
  "on Tuesday"           → { "type": "session_day", "days": ["tue"] }
  "Monday and Friday"    → { "type": "session_day", "days": ["mon", "fri"] }
Compose with other conditions via logic.and / logic.or.
NEVER fake a day-of-week filter using indicator equality (e.g. RSI == RSI) — the validator rejects tautologies, and the result would never fire correctly.

ENTRY vs EXIT — the tree returned from THIS prompt is the ENTRY condition only. Exits are translated in a separate hop with their own tree. NEVER AND together a buy condition and a sell condition in one tree (e.g. RSI<30 AND RSI>30) — the validator rejects the empty intersection.

Math op operand counts:
  abs / negate         : EXACTLY 1 operand
  + / - / * / /        : EXACTLY 2 operands
  min / max            : 2..8 operands

Prefer the SHORTCUT LEAVES whenever they fit:
  "NIFTY opens 1% below yesterday's close"      → { "type":"comparison", "op":"<", "left":{"type":"gap","symbol":"NIFTY"}, "right":{"type":"constant","value":-0.01} }
  "TCS price up 3% over last 5 bars"           → { "type":"comparison", "op":">", "left":{"type":"pct_change","symbol":"TCS","bars":5}, "right":{"type":"constant","value":0.03} }
  "TCS/INFY spread is below 1.5"               → { "type":"comparison", "op":"<", "left":{"type":"spread","a":"TCS","b":"INFY"}, "right":{"type":"constant","value":1.5} }
Use the general `math` node ONLY when the shortcuts don't fit (e.g. "TCS close minus its 20-day SMA, divided by ATR").

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

  ALL of these position fields are wired and supported. Do NOT punt back
  with messages like "this system can't read entry price" or "the
  drawdown clause needs to be expressed differently" — emit the tree.

  Worked exit-tree examples:

  "trail an 8% stop from the peak unrealised gain":
    { "type":"comparison", "op":">=",
      "left":  { "type":"position", "field":"drawdown_from_peak_pct" },
      "right": { "type":"constant", "value":0.08 } }

  "exit when my position is up 4%":
    { "type":"comparison", "op":">=",
      "left":  { "type":"position", "field":"unrealised_pct" },
      "right": { "type":"constant", "value":0.04 } }

  "exit when I've held for more than 30 bars OR RSI > 75":
    { "type":"logic", "op":"or",
      "operands": [
        { "type":"comparison", "op":">",
          "left":  { "type":"position", "field":"bars_held" },
          "right": { "type":"constant", "value":30 } },
        { "type":"comparison", "op":">",
          "left":  { "type":"indicator", "indicator":"rsi",
                      "symbol":"KOTAKBANK", "period":14 },
          "right": { "type":"constant", "value":75 } } ] }

  "exit when price drops below entry_price minus 2x ATR(14)":
    { "type":"comparison", "op":"<",
      "left":  { "type":"price", "symbol":"SBIN" },
      "right": { "type":"math", "op":"-",
                 "operands": [
                   { "type":"position", "field":"entry_price" },
                   { "type":"math", "op":"*",
                     "operands": [
                       { "type":"constant", "value":2 },
                       { "type":"indicator", "indicator":"atr",
                         "symbol":"SBIN", "period":14 } ] } ] } }

  "exit when RSI > 70 OR drawdown from peak > 6%":
    { "type":"logic", "op":"or",
      "operands": [
        { "type":"comparison", "op":">",
          "left":  { "type":"indicator", "indicator":"rsi",
                      "symbol":"LT", "period":14 },
          "right": { "type":"constant", "value":70 } },
        { "type":"comparison", "op":">",
          "left":  { "type":"position", "field":"drawdown_from_peak_pct" },
          "right": { "type":"constant", "value":0.06 } } ] }
"""


class TranslationError(ValueError):
    """Raised when the LLM didn't produce parseable JSON for the tree."""


async def translate_condition_to_tree(
    condition: str,
    *,
    allow_position: bool = False,
    primary_symbol: Optional[str] = None,
    cache_key: str = "dsl.translate.v1",
) -> tuple[dict, dict[str, Any]]:
    """Hand a natural-language condition to the LLM and return its DSL
    tree as a Python dict.

    Returns ``(tree, meta)`` where ``meta`` has ``input_tokens``,
    ``output_tokens``, ``latency_ms``. Raises ``TranslationError`` if the
    LLM's reply isn't valid JSON.

    ``allow_position`` is a hint to the prompt — when True, the LLM is
    permitted to emit the ``position`` leaf (exit-tree context).

    ``primary_symbol`` is the ticker the caller already pinned (e.g.
    "INFY" when the chat user said "buy 15 INFY on the golden cross").
    When set, the translator system prompt is appended with a hint so
    leaves default to this symbol whenever the NL condition doesn't
    name one — prevents the model from falling back to "NSE" / "NIFTY"
    placeholders that don't resolve to real bars.
    """
    # Lazy import — avoids pulling the LLM stack into modules that just
    # need to know the prompt exists.
    from backend.llm.base import LLMMessage
    from backend.llm.factory import get_llm_client
    import time

    if not condition or not condition.strip():
        raise TranslationError("empty condition")

    sys_prompt = SYSTEM_PROMPT
    if primary_symbol and primary_symbol.strip():
        sym = primary_symbol.strip().upper()
        sys_prompt = (
            sys_prompt
            + f"\n\nDEFAULT SYMBOL — when a leaf node (indicator, price, "
              f"volume, gap, pct_change) needs a 'symbol' field and the "
              f"user's NL condition does NOT name one explicitly, use "
              f"\"{sym}\" as the symbol. NEVER emit \"NSE\" or \"BSE\" as "
              f"a symbol — those are exchange codes, not tickers."
        )
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


# ── Pipeline translator (multi-branch / multi-tier / mixed-action) ───
#
# The chat-side LLM picks intent and calls propose_pipeline_workflow with
# the user's NL intent verbatim. The handler invokes this translator,
# which has the FULL step catalog + DSL tree grammar + compositional
# fewshots in its system prompt. The translator emits a full steps[]
# array directly — embedded DSL trees go inline as nested JSON, no
# second hop.
#
# Mirrors translate_condition_to_tree's contract: returns
# ``(draft_dict, meta)`` where draft_dict is the validated-shape
# {name, description, steps[], rationale} payload. Raises
# TranslationError on non-JSON / malformed-shape output.


_PIPELINE_FEWSHOTS = """
COMPOSITIONAL EXAMPLES — these are the shapes ONLY this tool can build.
Each `entry` field inside trigger.compound / trigger.exit_compound /
condition.compound is a DSL tree using the grammar at the top.

Example A — multi-tier scale-out exit (4 branches: 1 entry + 3 exits).
"buy 10 RELIANCE when RSI(14)<30 and MACD hist>0. Sell 5 when up 3%, sell 3 more when up 5%, sell rest if drawdown from peak > 5% or held > 30 bars":
{
  "name": "RELIANCE compound entry, 3-tier exit",
  "description": "Entry on RSI<30 AND MACD>0; tiered scale-out at +3%, +5%, drawdown 5% or 30 bars.",
  "steps": [
    {"step_type":"trigger.compound","config":{"entry":{"type":"logic","op":"and","operands":[
       {"type":"comparison","op":"<","left":{"type":"indicator","indicator":"rsi","symbol":"RELIANCE","period":14},"right":{"type":"constant","value":30}},
       {"type":"comparison","op":">","left":{"type":"indicator","indicator":"macd","symbol":"RELIANCE","component":"hist"},"right":{"type":"constant","value":0}}
     ]},"symbol":"RELIANCE","exchange":"NSE"}},
    {"step_type":"action.place_order","config":{"symbol":"RELIANCE","side":"buy","quantity":10,"order_type":"market","product":"CNC"}},
    {"step_type":"trigger.exit_compound","config":{"entry":{"type":"comparison","op":">=","left":{"type":"position","field":"unrealised_pct"},"right":{"type":"constant","value":0.03}},"target_symbol":"RELIANCE"}},
    {"step_type":"action.place_order","config":{"symbol":"RELIANCE","side":"sell","quantity":5,"order_type":"market","product":"CNC"}},
    {"step_type":"trigger.exit_compound","config":{"entry":{"type":"comparison","op":">=","left":{"type":"position","field":"unrealised_pct"},"right":{"type":"constant","value":0.05}},"target_symbol":"RELIANCE"}},
    {"step_type":"action.place_order","config":{"symbol":"RELIANCE","side":"sell","quantity":3,"order_type":"market","product":"CNC"}},
    {"step_type":"trigger.exit_compound","config":{"entry":{"type":"logic","op":"or","operands":[
       {"type":"comparison","op":">=","left":{"type":"position","field":"drawdown_from_peak_pct"},"right":{"type":"constant","value":0.05}},
       {"type":"comparison","op":">","left":{"type":"position","field":"bars_held"},"right":{"type":"constant","value":30}}
     ]},"target_symbol":"RELIANCE"}},
    {"step_type":"fetch.portfolio","config":{}},
    {"step_type":"action.place_order","config":{"symbol":"RELIANCE","side":"sell","quantity":"{{ context.7.holdings.RELIANCE.quantity }}","order_type":"market","product":"CNC"}}
  ],
  "rationale":"1 compound entry + 3 exit branches: fixed-qty scale-out at +3% and +5%, then close the remaining holding on drawdown or time."
}

Example B — multi-trigger fan-out (3 independent branches).
"every Monday at open buy 5 NIFTYBEES; if NIFTY drops 2% intraday from open sell 10 from my NIFTYBEES holding; on Friday close squareoff my full NIFTYBEES position":
{
  "name": "NIFTYBEES weekly accumulate + intraday sell + Friday squareoff",
  "description": "Three branches: Monday buy, intraday risk-off sell, Friday close.",
  "steps": [
    {"step_type":"trigger.market_relative_time","config":{"anchor":"open","offset_minutes":0,"days":["mon"]}},
    {"step_type":"action.place_order","config":{"symbol":"NIFTYBEES","side":"buy","quantity":5,"order_type":"market","product":"CNC"}},
    {"step_type":"trigger.schedule","config":{"cron":"*/5 9-15 * * 1-5","timezone":"Asia/Kolkata"}},
    {"step_type":"fetch.relative_threshold","config":{"symbol":"NIFTY","reference":"day_open","offset_pct":-2}},
    {"step_type":"fetch.quote","config":{"symbol":"NIFTY"}},
    {"step_type":"condition.numeric","config":{"left":"{{ context.4.ltp }}","operator":"<=","right":"{{ context.3.value }}"}},
    {"step_type":"fetch.portfolio","config":{}},
    {"step_type":"action.place_order","config":{"symbol":"NIFTYBEES","side":"sell","quantity":10,"order_type":"market","product":"CNC"}},
    {"step_type":"trigger.market_relative_time","config":{"anchor":"close","offset_minutes":-5,"days":["fri"]}},
    {"step_type":"action.squareoff_symbol","config":{"symbol":"NIFTYBEES"}}
  ],
  "rationale":"Three trigger.* steps = three branches. Branch 2 uses fetch.relative_threshold to compute the 2% intraday drop reference; branch 3 uses squareoff_symbol for the EOD exit."
}

Example C — compound condition mid-branch + conditional notify-then-buy.
"every weekday at 09:30, if RSI(14)<30 AND MACD hist>0 send me a notification. If also RSI<20, buy 10 INFY":
{
  "name": "INFY morning condition check + tiered alert",
  "description": "Schedule check at 09:30. Notify on RSI<30 AND MACD>0. Buy on the stricter RSI<20.",
  "steps": [
    {"step_type":"trigger.schedule","config":{"cron":"30 9 * * 1-5","timezone":"Asia/Kolkata"}},
    {"step_type":"condition.compound","config":{"entry":{"type":"logic","op":"and","operands":[
       {"type":"comparison","op":"<","left":{"type":"indicator","indicator":"rsi","symbol":"INFY","period":14},"right":{"type":"constant","value":30}},
       {"type":"comparison","op":">","left":{"type":"indicator","indicator":"macd","symbol":"INFY","component":"hist"},"right":{"type":"constant","value":0}}
     ]}}},
    {"step_type":"notify.message","config":{"channel":"push","message":"INFY morning check: RSI<30 AND MACD>0 — watching for the stricter RSI<20 trigger."}},
    {"step_type":"condition.compound","config":{"entry":{"type":"comparison","op":"<","left":{"type":"indicator","indicator":"rsi","symbol":"INFY","period":14},"right":{"type":"constant","value":20}}}},
    {"step_type":"action.place_order","config":{"symbol":"INFY","side":"buy","quantity":10,"order_type":"market","product":"CNC"}}
  ],
  "rationale":"Single branch. condition.compound carries the AND-tree; engine halts on false. After the first compound check, notify; then the stricter compound check gates the order."
}

Example D — news-event + DSL gate.
"if RBI cuts the repo rate (news) AND BANKNIFTY is up >1% the next morning, buy 30 HDFCBANK":
{
  "name": "RBI repo cut + BANKNIFTY confirmation → HDFCBANK buy",
  "description": "News trigger filters on confirmed RBI repo cut; intraday relative threshold gates the BANKNIFTY +1% check.",
  "steps": [
    {"step_type":"trigger.event","config":{"event_description":"RBI cuts the repo rate at MPC","keywords":["RBI","MPC","repo rate","rate cut"],"min_confidence":0.7,"poll_seconds":900}},
    {"step_type":"trigger.market_relative_time","config":{"anchor":"open","offset_minutes":30}},
    {"step_type":"fetch.relative_threshold","config":{"symbol":"BANKNIFTY","reference":"prior_close","offset_pct":1}},
    {"step_type":"fetch.quote","config":{"symbol":"BANKNIFTY"}},
    {"step_type":"condition.numeric","config":{"left":"{{ context.3.ltp }}","operator":">=","right":"{{ context.2.value }}"}},
    {"step_type":"action.place_order","config":{"symbol":"HDFCBANK","side":"buy","quantity":30,"order_type":"market","product":"CNC"}}
  ],
  "rationale":"Two trigger.* steps = two branches. Branch 1 fires on confirmed news; branch 2 fires the next session at 09:45 and gates the buy on BANKNIFTY's relative move."
}

OUT OF SCOPE (do NOT attempt — return {"error":"...","needs_engine_feature":"..."}):
- "if-then-else" conditional routing within ONE branch (engine halts on condition fail; only `control.skip_if` skips the immediate next step).
- Cross-branch state ("if branch A fired today, then branch B", "2-of-3 vote across triggers"). Each branch fires independently, no shared mutable state.
- Loops / iteration ("for each holding in my portfolio do X").

HARD RULES:
  1. Step 0 MUST be a trigger.* — every workflow has at least one.
  2. No two adjacent trigger.* steps (an empty branch is rejected).
  3. trigger.compound / trigger.exit_compound / condition.compound carry their tree at config.entry — emit the tree inline as nested JSON.
  4. Mustache refs only — `{{ context.<idx>.<path> }}`. No arithmetic in refs.
  5. STAY LITERAL. Do NOT add unprompted sell branches, stop-losses, or notifications the user didn't ask for.

Output ONLY a single JSON object: {"name":"...", "description":"...", "steps":[...], "rationale":"..."}. No prose, no markdown fences.
"""


async def translate_intent_to_pipeline(
    intent: str,
    *,
    primary_symbol: Optional[str] = None,
    extra_instruction: Optional[str] = None,
    cache_key: str = "dsl.pipeline.v1",
) -> tuple[dict, dict[str, Any]]:
    """Translate a full multi-branch / multi-tier intent into a workflow
    draft dict (``{name, description, steps[], rationale}``).

    The system prompt splices the existing DSL grammar (``SYSTEM_PROMPT``
    at module top) with the live step catalog from
    ``backend.workflows.propose._build_catalog_summary`` and the
    compositional fewshots above. The translator returns the full
    ``steps[]`` array directly — embedded DSL trees ride inline at
    ``step.config.entry`` for compound / exit_compound / condition.compound
    steps.

    Returns ``(draft, meta)`` where ``draft`` is the parsed JSON object
    and ``meta`` has ``input_tokens``, ``output_tokens``, ``latency_ms``.

    Raises ``TranslationError`` if the LLM returns non-JSON or a non-
    object payload.
    """
    if not intent or not intent.strip():
        raise TranslationError("empty intent")

    from backend.llm.base import LLMMessage
    from backend.llm.factory import get_llm_client
    from backend.workflows.propose import _build_catalog_summary
    import time

    catalog = _build_catalog_summary()
    sys_prompt = (
        SYSTEM_PROMPT
        + "\n\n────────────────────────────────────────────\n"
          "PIPELINE-WORKFLOW MODE — you are now translating a FULL "
          "intent into a Pivot workflow draft, not just a single condition "
          "tree. Use the full step catalog below alongside the DSL "
          "grammar above.\n\nSTEP CATALOG:\n"
        + catalog
        + "\n"
        + _PIPELINE_FEWSHOTS
    )
    if primary_symbol and primary_symbol.strip():
        sym = primary_symbol.strip().upper()
        sys_prompt += (
            f"\n\nDEFAULT SYMBOL — when a step config needs a symbol and "
            f"the user's intent does NOT name one, use \"{sym}\"."
        )
    if extra_instruction:
        sys_prompt += f"\n\nIMPORTANT: {extra_instruction.strip()}"

    client = get_llm_client()
    t0 = time.time()
    resp = await client.complete(
        messages=[
            LLMMessage(role="system", content=sys_prompt),
            LLMMessage(role="user", content=intent),
        ],
        response_format="json_object",
        reasoning_effort="medium",
        temperature=0.2,
        max_output_tokens=2400,
        prompt_cache_key=cache_key,
    )
    elapsed_ms = (
        float(resp.latency_ms)
        if getattr(resp, "latency_ms", None) is not None
        else (time.time() - t0) * 1000.0
    )

    raw = (resp.content or "").strip()
    try:
        draft = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise TranslationError(
            f"LLM returned non-JSON content: {exc}"
        ) from None
    if not isinstance(draft, dict):
        raise TranslationError(
            f"expected JSON object, got {type(draft).__name__}"
        )
    # Engine-feature bail signal — propagated to the handler so it can
    # raise a user-facing clarification instead of trying to validate
    # a draft that the LLM declined to produce.
    if "needs_engine_feature" in draft or (
        "error" in draft and not draft.get("steps")
    ):
        raise TranslationError(
            f"pipeline declined: {draft.get('error') or draft.get('needs_engine_feature')}"
        )

    meta = {
        "input_tokens": int(resp.input_tokens or 0),
        "output_tokens": int(resp.output_tokens or 0),
        "latency_ms": elapsed_ms,
    }
    logger.info(
        "[dsl.pipeline] intent=%r tokens_in=%d tokens_out=%d ms=%.0f steps=%d",
        intent[:80], meta["input_tokens"], meta["output_tokens"],
        meta["latency_ms"], len(draft.get("steps") or []),
    )
    return draft, meta
