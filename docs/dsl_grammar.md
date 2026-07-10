# Pivot DSL Grammar — v1

> **Status:** Phase-D foundation shipped. Backtest wiring + LLM
> proposer extension + visual editor are follow-up phases.
> **Code location:** `pivot/backend/workflows/dsl/`.
> **Step type:** `trigger.compound`.

## Why this exists

> *"What happens when the RSI of TCS falls below 30 AND NIFTY is
> above 23K, then buy — and sell when both conditions reverse."*

The pre-DSL engine couldn't express that as one step. Each trigger
type encoded exactly one comparison: `trigger.indicator` was "one
indicator vs one threshold," full stop. Anything compound needed a
new step type. Multiply that by the user's imagination and you get
factorial growth in tools.

The DSL replaces the explosion with one composable primitive: a
**tree of expressions** the LLM emits as JSON, validated against a
tight schema, evaluated by a single deterministic interpreter.

---

## Node types

The whole grammar is six node types behind a single `type` field
discriminator. Every node validates via Pydantic
(`backend/workflows/dsl/schema.py`).

### Leaf nodes (numbers)

```json
{ "type": "indicator", "indicator": "rsi", "symbol": "TCS", "period": 14, "exchange": "NSE" }
{ "type": "price",     "symbol": "NIFTY", "exchange": "NSE" }
{ "type": "volume",    "symbol": "TCS",   "bars": 1, "exchange": "NSE" }
{ "type": "constant",  "value": 30.0 }
```

Constraints:
- `indicator`: must be a key in
  `backend.services.backtest_indicators.supported_indicators()`
  (rsi, sma, ema, macd, atr, adx, aroon, bb, cci, donchian, keltner,
  mfi, obv, psar, roc, stoch, stoch_rsi, supertrend, trix, volume,
  volume_ma, volume_roc, vwap, williams_r, wma — see live registry).
- `period`: 1 ≤ N ≤ 5000.
- `constant.value`: finite (NaN / Inf rejected).

### Inner nodes (booleans)

```json
{
  "type": "comparison",
  "op": ">" | "<" | ">=" | "<=" | "==" | "crosses_above" | "crosses_below",
  "left":  <Tree>,
  "right": <Tree>
}

{
  "type": "logic",
  "op": "and" | "or" | "not",
  "operands": [<Tree>, ...]
}
```

Constraints:
- `logic.and` / `logic.or`: 2–8 operands.
- `logic.not`: exactly 1 operand.
- The **root** of a tree must be a `comparison` or `logic` — a bare
  leaf can't fire (it's a number, not a boolean).
- Maximum tree depth: **4**. If you need deeper, split into multiple
  workflow steps.
- Both sides of a `comparison` cannot be `constant` (semantic check
  flags this as vacuous).

---

## Evaluation semantics

The interpreter (`evaluator.evaluate(tree, accessor, prev_state)`)
returns a `Ternary` — `TRUE`, `FALSE`, or `UNKNOWN`. **`UNKNOWN` is
sticky:** if any leaf can't be resolved (yfinance down, indicator
needs more history, etc.), it propagates through comparisons and
short-circuits logic in **Kleene three-valued logic**:

```
and:  T AND T = T,   T AND F = F,   T AND U = U,
      F AND F = F,   F AND U = F,   U AND U = U
or:   T OR T = T,    T OR F = T,    T OR U = T,
      F OR F = F,    F OR U = U,    U OR U = U
not:  not T = F,     not F = T,     not U = U
```

**The watcher fires only on `Ternary.TRUE`** — never on UNKNOWN.
This prevents spurious trades when data is flaky.

### Crossings need previous-tick state

`crosses_above` / `crosses_below` are tick-over-tick operators.
The evaluator's `prev_state` argument carries the last reading of
both operands. First tick returns `FALSE` (no transition observable
yet) and writes initial state. Subsequent ticks compare prev vs
current. The watcher persists this state on
`workflow_steps.config["_last_values"]` between ticks.

---

## Example trees mapped from English

### "Buy TCS when RSI(14) drops below 30 AND NIFTY price is above 23,000"

```json
{
  "type": "logic", "op": "and",
  "operands": [
    {
      "type": "comparison", "op": "<",
      "left":  { "type": "indicator", "indicator": "rsi", "symbol": "TCS", "period": 14 },
      "right": { "type": "constant", "value": 30 }
    },
    {
      "type": "comparison", "op": ">",
      "left":  { "type": "price", "symbol": "NIFTY" },
      "right": { "type": "constant", "value": 23000 }
    }
  ]
}
```

Readback: `RSI(14) of TCS < 30 AND price of NIFTY > 23,000`

### "Sell when RSI(TCS) crosses above 30 OR price drops below 2,500"

```json
{
  "type": "logic", "op": "or",
  "operands": [
    {
      "type": "comparison", "op": "crosses_above",
      "left":  { "type": "indicator", "indicator": "rsi", "symbol": "TCS", "period": 14 },
      "right": { "type": "constant", "value": 30 }
    },
    {
      "type": "comparison", "op": "<",
      "left":  { "type": "price", "symbol": "TCS" },
      "right": { "type": "constant", "value": 2500 }
    }
  ]
}
```

Readback: `RSI(14) of TCS crosses above 30 OR price of TCS < 2,500`

### "RSI(TCS, 14) overbought OR (volume spike AND below 50-day SMA)"

```json
{
  "type": "logic", "op": "or",
  "operands": [
    {
      "type": "comparison", "op": ">",
      "left":  { "type": "indicator", "indicator": "rsi", "symbol": "TCS", "period": 14 },
      "right": { "type": "constant", "value": 70 }
    },
    {
      "type": "logic", "op": "and",
      "operands": [
        {
          "type": "comparison", "op": ">",
          "left":  { "type": "volume", "symbol": "TCS", "bars": 1 },
          "right": { "type": "constant", "value": 500000 }
        },
        {
          "type": "comparison", "op": "<",
          "left":  { "type": "price", "symbol": "TCS" },
          "right": { "type": "indicator", "indicator": "sma", "symbol": "TCS", "period": 50 }
        }
      ]
    }
  ]
}
```

Readback: `RSI(14) of TCS > 70 OR (volume of TCS > 500,000 AND price of TCS < SMA(50) of TCS)`

This is depth 3 (logic → logic → comparison → leaf), within the
depth-4 cap.

### "RSI(TCS) AND RSI(INFY) both below 30" — comparing two indicators

```json
{
  "type": "logic", "op": "and",
  "operands": [
    {
      "type": "comparison", "op": "<",
      "left":  { "type": "indicator", "indicator": "rsi", "symbol": "TCS", "period": 14 },
      "right": { "type": "constant", "value": 30 }
    },
    {
      "type": "comparison", "op": "<",
      "left":  { "type": "indicator", "indicator": "rsi", "symbol": "INFY", "period": 14 },
      "right": { "type": "constant", "value": 30 }
    }
  ]
}
```

### "TCS RSI is below NIFTY RSI" — comparing two market values directly

```json
{
  "type": "comparison", "op": "<",
  "left":  { "type": "indicator", "indicator": "rsi", "symbol": "TCS",   "period": 14 },
  "right": { "type": "indicator", "indicator": "rsi", "symbol": "NIFTY", "period": 14 }
}
```

Both sides being market values is fine; only `constant <op> constant`
is rejected (as vacuous).

---

## How it slots into the workflow

The step config carries the tree:

```json
{
  "step_type": "trigger.compound",
  "config": {
    "entry": <Tree>,
    "_last_values": { /* engine-managed crossing state */ }
  }
}
```

The watcher (`backend/workflows/scheduler.py::_evaluate_compound_trigger`)
ticks every 60s during market hours, evaluates the tree, persists
`_last_values` back, and calls `_fire_watch_run` on `Ternary.TRUE`
with `triggered_by="indicator_alert"`.

The engine's downstream steps (`action.place_order`,
`notify.message`, etc.) see the run the same way they would for any
other trigger — the compound trigger is invisible past the firing
moment.

---

## What's intentionally NOT in v1

- **Exit-tree.** You wire entry-and-exit as a single workflow with
  two trigger.compound steps + a `condition.skip_if` for "did we
  enter?" state. A first-class `exit` field on the step config can
  land later.
- **Time qualifiers.** "Sustained for 5 bars" or "within the last
  10 minutes" aren't expressible yet. Need a `Within` node or a
  `sustain` modifier on comparisons. Punt to v2.
- **Multi-timeframe.** Everything is daily bars / live ticks today.
  No "15-min RSI above 70 AND daily RSI below 30" cross-timeframe
  combos.
- **Backtest replay.** The same evaluator will run inside the
  backtester once a bar-strict `BacktestDataAccessor` lands; the
  `DataAccessor` protocol in `data_accessor.py` is designed for
  this swap.
- **LLM proposer extension.** The chat / `propose_workflow` LLM
  doesn't know how to emit trees yet. That work needs prompt
  engineering + ~10 high-quality worked examples + the
  retry-on-validation loop the existing proposer already has.

---

## Reference

| File | Lines | Purpose |
|---|---|---|
| `backend/workflows/dsl/schema.py` | ~160 | Pydantic node types + Tree alias |
| `backend/workflows/dsl/evaluator.py` | ~200 | Pure walker + Kleene logic |
| `backend/workflows/dsl/validators.py` | ~120 | Depth, indicator registry, vacuous-comparison checks |
| `backend/workflows/dsl/data_accessor.py` | ~180 | DataAccessor protocol + LiveDataAccessor |
| `backend/workflows/dsl/readback.py` | ~90 | Tree → English |
| `backend/workflows/scheduler.py` | +90 | `_evaluate_compound_trigger` watcher branch |
| `backend/workflows/schemas.py` | +60 | `TriggerCompoundConfig` step-config model |
| `backend/workflows/steps/triggers.py` | +25 | `@register_step("trigger.compound")` |
| `tests/workflows/dsl/` | ~700 | 70 tests across schema / evaluator / validators / readback / watcher |

Total ~1,600 lines of new code, all under `dsl/` plus thin extensions
elsewhere. The existing single-condition `trigger.price` /
`trigger.indicator` workflows continue to work unchanged — the DSL
is opt-in.
