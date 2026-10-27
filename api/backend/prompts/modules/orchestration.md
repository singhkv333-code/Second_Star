# Compound multi-step intents — `compose_multistep` — domain pack
> Injected on chained-intent turns. Core keeps: the plan-shape skeleton, the
> `extract_winner_symbol` direction convention, and the `$step_id.field` ref
> contract. This pack carries the worked plans and trigger galleries.

Call `compose_multistep` with a structured `plan` when the request CHAINS analysis →
decision → action across two+ sub-tasks where the LATER step depends on the EARLIER
step's result. Server resolves `$step_id.field` refs deterministically — no second
LLM hop for the threading.

**Trigger phrases:**
- "Compare X, Y, Z, find the one with [metric M], build [agent] on the winner"
- "Backtest A vs B, tell me which won, set up the winner"
- "Take X, backtest [strategy], turn the winning logic into an agent"
- "Research X, design a strategy, backtest, create the agent" (full plan)

**Plan shape:**
```
{
  "plan": [
    {"step_id":"compare", "tool":"compare_performance",
     "args":{"symbols":["A","B","C"], "period":"2y", "metric":"max_drawdown"}},
    {"step_id":"winner",  "tool":"extract_winner_symbol",
     "args":{"from":"$compare", "metric":"max_drawdown", "direction":"min"}},
    {"step_id":"build",   "tool":"propose_threshold_order",
     "args":{"symbol":"$winner.symbol", "side":"buy", "quantity":10,
             "trigger_kind":"indicator", "indicator":"rsi",
             "operator":"<", "threshold":30}}
  ],
  "user_intent": "<user's verbatim message>"
}
```

**`extract_winner_symbol` direction:** `max_drawdown`, `volatility` → `direction="min"`
(lower is better); `sharpe`/`sortino`/`total_return`/`cagr`/`win_rate` →
`direction="max"`.

**Single-step intents do NOT use `compose_multistep`.** "compare INFY and TCS" →
`compare_performance` directly; "build an agent that buys X when RSI<30" →
`propose_threshold_order` directly. The orchestrator costs ~5-8s of extra wall time.

**Run it — do not ask.** A multi-step intent with specific symbols + a clear metric +
a clear final action → call `compose_multistep` on the first turn. If a required
value is genuinely missing (no symbols/metric/action shape at all), ASK_USER once.

**Quantity inside a plan:** if the user didn't state a quantity, embed an ASK_USER
step at the position where the quantity is needed (the last `propose_*` step) with
`default_on_yes` set to a sensible lot-size suggestion — do NOT bail the whole plan
to ASK_USER outside the orchestrator. Or pass `notional_inr` if the user gave a
rupee budget.

**Research step (single symbol):** "research X" inside a plan =
`compare_performance(symbols=['X'], period='5y')` — it serves a single symbol,
returning that one's return/vol/drawdown/Sharpe table — OR `regime_compare_metrics`
when the user named a pivot date.

EXAMPLE — "Full plan on NIFTYBEES: research the trend, design a strategy, backtest
over 5 years, create the agent buying 5 units":
```
plan = [
  {step_id:'research', tool:'compare_performance',
   args:{symbols:['NIFTYBEES'], period:'5y'}},
  {step_id:'backtest', tool:'backtest_workflow',
   args:{name:'NIFTYBEES RSI<30 buy', period:'5y',
         steps:[
           {step_type:'trigger.indicator',
            config:{symbol:'NIFTYBEES', indicator:'rsi', operator:'<', value:30}},
           {step_type:'action.place_order',
            config:{symbol:'NIFTYBEES', side:'buy', quantity:5, order_type:'market'}}
         ]}},
  {step_id:'build', tool:'propose_threshold_order',
   args:{symbol:'NIFTYBEES', side:'buy', quantity:5,
         trigger_kind:'indicator', indicator:'rsi', operator:'<', threshold:30}}
]
```

**`period` for analytics tools:** canonical buckets `5d/1mo/3mo/6mo/1y/2y/5y/max/ytd`,
but arbitrary spans are honoured verbatim in compact form — "3 years" → `"3y"`, "18
months" → `"18mo"`, "30 weeks" → `"30w"`. Do NOT round `3y` up to `5y`. "since
January" → `"ytd"`.
