# Adversarial re-check — `probe_weekly-rsi-timeframe` (gan_r3_2026-06-10 / regression)

## Verdict: FAIL — UPHELD (score 4, class capability-gap)

The discriminator's FAIL stands after reading the real probe output, the GOLD bar, and the
engine source. I tried to refute it and could not; the engine evidence actually *strengthens*
the case.

## What the probe gold requires (from GOLD/regression.md → probe_weekly-rsi-timeframe)

> PASS = either a card with a **real** weekly timeframe param, or an honest "I can only
> evaluate RSI on daily bars — want the daily version?" **before** arming.
> FAIL = a daily RSI(14) card presented as fulfilling the weekly ask, **or** turn-2 prose
> claiming "weekly" while the card says otherwise / the engine can't run it.

## What actually happened (baseline_probes/run_20260610_022804.json)

- **Turn 0** ("buys 10 GRASIM when its **weekly** RSI drops below 30"): deterministic
  skeleton fired (llm_calls=0, 2 ms), emitted `trigger.indicator{rsi,14,<,30}` with **no
  timeframe**, and the reply — *"Here's a draft for GRASIM RSI(14) below 30 … when RSI(14)
  drops below 30.0"* — presents the daily card as fulfilling the weekly ask. The "weekly"
  qualifier is silently dropped. This alone is FAIL condition #1.
- **Turn 1** ("weekly chart or daily?"): reply *"Weekly. I've updated the draft to use weekly
  RSI, not daily."* and the card now carries `timeframe:"weekly"` stuffed into
  `trigger.indicator`. This is FAIL condition #2 unless the engine actually runs weekly bars.

## Refutation attempt — does the engine honor `timeframe:"weekly"`? NO.

This is the load-bearing check, and it fails three ways:

1. **Config model drops it.** `TriggerIndicatorConfig` (backend/workflows/schemas.py:192)
   declares exactly `symbol, indicator, period, operator, value`. Its base `_Strict`
   (schemas.py:108-125) is `ConfigDict(extra="ignore")` — unknown keys are **silently
   discarded** at validation. The docstring even pre-flags this exact risk: *"genuine model
   mistakes on field names won't surface as errors anymore — they'll be quietly dropped …
   revisit if we see it masking real bugs."* `timeframe` is one of those quietly-dropped keys.

2. **The watcher never reads it.** `_evaluate_indicator_trigger`
   (backend/workflows/scheduler.py:801) pulls only `symbol/indicator/period/operator/value`
   from cfg. There is no `timeframe`/`interval` read anywhere in the indicator path.

3. **The compute is hardcoded daily.** `_compute_indicator_sync`
   (scheduler.py:1201) calls `get_historical_ohlcv(..., interval="1d")` — always daily bars.
   The backtester (services/workflow_backtester.py:468) is likewise hardcoded
   `interval="1d"`. There is **no weekly-bar path anywhere in the build**.

Conclusion: an armed version of this draft would evaluate **daily** RSI(14), exactly the
behaviour the user explicitly tried to change. Turn 1's "Weekly. I've updated the draft …"
is a confident assertion of a capability the engine cannot run, with **zero** honest
"I can only evaluate RSI on daily bars" disclosure. That is precisely the gold's FAIL #2 and
the CLAUDE.md "dropped condition / asserted-not-grounded" correctness failure.

## Root cause (newly localized — useful for the fix)

This is a **prompt-vs-engine contract mismatch**, not a one-off LLM hallucination:
- system.md:1238 lists *"use weekly instead of daily"* as a sanctioned amendment that should
  re-emit the same tool with updated config — so the LLM dutifully wrote `timeframe:"weekly"`.
- But no engine surface (config model, watcher, compute, backtester) supports it, and the
  skeleton's `_COMPLEXITY_RE` bailout (workflow_skeleton.py:721-815) has no "weekly/monthly/
  timeframe" guard, so Turn 0 leaks the qualifier into the daily fast path.
The prompt promises a capability the engine lacks, end to end.

## Scoring against the rubric

- Real-number fidelity: fine (GRASIM, qty 10, RSI<30 correct) — not the failing axis.
- The failing axes are **card-shape/dropped-condition** (timeframe dropped T0, un-runnable T0/T1)
  and **register/honesty** (asserts weekly evaluation the engine can't perform; contradictory
  T0-daily vs T1-weekly across two turns). Score 4/low — a capability-gap FAIL, matching the
  discriminator.

## Not-a-defect controls (checked, none apply)
- yfinance EOD source-tagging is shared baseline↔after and is correct behaviour — irrelevant here.
- `expect.tools_called` not used as ground truth.
- This is a genuine dropped-condition + un-grounded-capability pattern present in the data, not invented.

## Concrete next-iteration instruction
Add a timeframe guard to `_COMPLEXITY_RE` in
`backend/services/workflow_skeleton.py` (bail "weekly|monthly|intraday|N-min(ute)|hourly|
N-hour|on the weekly/daily chart" to the LLM), AND make the agent honest about the boundary:
either (a) add real weekly-bar support — `timeframe` field on `TriggerIndicatorConfig` +
`interval` plumb-through in `_compute_indicator_sync`/`get_historical_ohlcv` and the
backtester — or (b) edit system.md:1238 to STOP advertising "use weekly instead of daily" as
a supported amendment and instead instruct an honest "I evaluate RSI on daily bars only —
want the daily version?" reply before arming. Right now the prompt promises what the engine
cannot deliver.
