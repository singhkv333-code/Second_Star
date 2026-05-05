# Agentic chat — 10-prompt evaluation

**When**: 2026-05-05
**Backend**: `http://127.0.0.1:8000` (development, mock_kite=true, openai live)
**Driver**: `/tmp/pivot_agentic_run.py` — POST /chat, single sample per prompt
**Raw**: `pivot/scripts/agentic_run_raw.json`

## Headline

**3 of 10 produced a usable card on the first turn.** 1 produced a usable-but-bloated card. 5 fell back to `ask_user` with a stock catalog dump or a validation failure. 1 emitted a draft with the literal string `"OCO"` as the symbol — that's a bug, not a missing feature.

The two new primitives shipped in commit `923977a` (market-relative-time trigger, intraday P&L fetch) are **not actually being used by the model** even when the prompt explicitly calls for them. That's the highest-value fix.

## Per-prompt result

| # | Prompt | Tool | Outcome | Latency | Verdict |
|---|---|---|---|---|---|
| 1 | `simple_sip` — Buy 5 NIFTYBEES every weekday 09:15 | `propose_workflow` | 2-step draft (schedule → place_order) | **11ms** | ✅ Reference quality. Fast-path skeleton hit. |
| 2 | `multi_trigger` — Mon buy + Mon-close RSI sell | `propose_workflow` | `ask_user` ("step shape isn't in catalog") | 21.5s | ❌ **Regression.** Identical prompt to the existing grader's `multi_trigger_agent` control case (`grade_automation_quality.py:142–155`); supposed to emit one draft with two trigger.schedule steps. |
| 3 | `rsi_exit_only` — Sell INFY when RSI(14) > 70 | `propose_workflow` | 3-step draft, uses `{{ context.1.holdings.INFY.quantity }}` | <1ms (skeleton) | ✅ Clean. |
| 4 | `dip_buy_sl` — HDFCBANK 2% below open + 2% SL | `propose_workflow` | 9-step draft | **24.8s** | ⚠️ Works but over-engineered: adds a `fetch.portfolio` + `condition.numeric` "buying power gate" that the executor can't actually evaluate — the broker rejects insufficient orders anyway. 3 LLM hops for a routine ask. |
| 5 | `market_relative_close` — 5min before close, exit MIS if PnL < -2% | `propose_workflow` | `ask_user` (catalog dump) | 12.3s | ❌ **The two primitives this prompt needs (`trigger.market_relative_time`, `fetch.intraday_pnl`) literally just shipped — and the model didn't reach for either.** |
| 6 | `expiry_window` — Buy RELIANCE on RSI<30, valid till month-end | `propose_threshold_order` | Validation fail → `ask_user` | 3.4s | ❌ Routed to wrong tool because there's no `valid_until` slot in `propose_workflow`. The "valid till month end" phrase is what tipped routing into the threshold tool. |
| 7 | `cross_symbol` — Sell SBIN if NIFTY 50 drops >1% from open | `propose_workflow` | `ask_user` (catalog dump) | 17.1s | ❌ Step shape is expressible (`fetch.quote NIFTY 50` + `fetch.relative_threshold` + `condition.numeric` + `action.place_order SBIN`), but the model has no example showing trigger-on-A → action-on-B. |
| 8 | `basket_dca` — ₹10k Mon 09:30 across 4 symbols | `propose_basket_allocation` | "what's the sector?" | 3.0s | ❌ Wrong tool. User gave 4 explicit symbols; basket tool only takes a sector descriptor. Should have routed to `propose_workflow` with 4 `action.place_order` steps. |
| 9 | `earnings_window` — Sell half TCS 5min before close on earnings day | `propose_holding_action` | Validation fail → `ask_user` | 3.5s | ❌ Honest miss — there is no event-calendar primitive. But the failure message ("I couldn't run … with the values I had. Could you restate that with specific values?") is unhelpful. |
| 10 | `conditional_oco` — Buy 5 LT, then 2% SL + 5% target as OCO | `propose_holding_action` | Draft emitted with `symbol: "OCO"` | 11.6s | 🐞 **True bug.** Model parsed "OCO" as the ticker, lost LT entirely, and dropped the buy + target legs. The card on screen would be unusable. |

## What's actually broken

### 1. New primitives are invisible to the model (HIGH)

Commit `923977a` added `TriggerMarketRelativeTimeConfig` and `FetchIntradayPnLConfig` to the schema. Prompt 5 maps **exactly** onto those primitives and the model didn't use them. Two likely causes — both worth checking:

- `pivot/backend/prompts/agentic_examples.json` doesn't have an example using `trigger.market_relative_time` or `fetch.intraday_pnl`. The model only invokes step types it has seen in examples.
- The system prompt's "available step types" list (the catalog dump that gets returned in the `ask_user` fallback) **does** mention them, which means the catalog the user sees and the catalog the model is shown probably diverged. Worth grepping for where the catalog is declared and confirming both surfaces read from the same source of truth.

**Fix**: add 1–2 examples to `agentic_examples.json` that look like:

```json
{
  "user": "5 minutes before close every weekday, exit MIS if intraday PnL < -2%",
  "draft": {
    "name": "EOD MIS auto-cut",
    "steps": [
      {"step_type": "trigger.market_relative_time", "config": {"anchor": "close", "offset_minutes": -5, "weekdays": "1-5"}},
      {"step_type": "fetch.intraday_pnl", "config": {"scope": "intraday"}},
      {"step_type": "condition.numeric", "config": {"left": "{{ context.1.total_pct }}", "operator": "<", "right": -2}},
      {"step_type": "action.square_off", "config": {"scope": "all_intraday"}}
    ]
  }
}
```

### 2. `multi_trigger_agent` regressed (HIGH)

This is the same prompt that's been the system's bread-and-butter agentic test (`pivot/scripts/grade_automation_quality.py:142`). Today it returns the catalog-help string instead of a 2-step-trigger draft. **Run the grader** before and after the next merge and confirm whether `923977a` flipped this; if so, look at `chat_service.py` and `prompts/system.md` for what changed in the LLM output handling. The catalog-help branch is firing when it shouldn't.

### 3. Tool router is too eager to pick narrow tools (HIGH)

Three of the failures (prompts 6, 8, 10) routed away from `propose_workflow` to a narrower tool that then validation-failed:

- `propose_threshold_order` — picked because of "valid till"
- `propose_basket_allocation` — picked because "split rupees across" looks like a basket, even though the user gave specific symbols
- `propose_holding_action` — picked twice (prompts 9 and 10) and produced garbage both times

Either (a) tighten the description of these tools so the LLM only routes to them when their constraints are satisfied (e.g. `propose_basket_allocation` description should say "ONLY for sector-named baskets, never for explicit symbol lists"), or (b) collapse them all into `propose_workflow` and let the workflow primitives do the work. Option (b) is cleaner and matches the v1 design — every actionable shape can be expressed as workflow steps.

### 4. The `OCO` symbol bug (MEDIUM, but visible)

Prompt 10's draft has `symbol: "OCO"`. This will be obviously wrong to anyone glancing at the card. Likely the routing-to-`propose_holding_action` truncated the parse — by the time it asked for the symbol, "OCO" was the most recent capitalized token in the LLM's working memory. Either kill `propose_holding_action` (see fix 3) or have it validate that the symbol resolves to a real instrument before returning.

### 5. Workflow drafts are bloated when they do work (MEDIUM)

Prompt 4 produced 9 steps for what should be 4–5:

```
trigger.schedule
fetch.relative_threshold
fetch.quote
condition.numeric  ← good
action.place_order
action.set_stoploss
```

The two extra steps are `fetch.portfolio` + `condition.numeric` for "buying power" — neither of which the executor can usefully evaluate (Pivot doesn't have reliable buying-power data; Kite returns it but it's stale by the time the workflow fires). And the `notify.message` step is gratuitous — the action steps should produce their own confirmations. Remove these from the agentic examples.

### 6. Catalog-dump fallback is a 12–17s no-op (MEDIUM)

When the model gives up and shows the catalog (prompts 2, 5, 7), latency is 12–21s — the system is paying for two full LLM hops to produce a static help message. If hop 1's output is going to trigger the catalog branch, hop 2 should be skipped. Look at `chat_service.py` for where this branch is decided and short-circuit there.

## Suggestions, ranked

1. **Add 3–4 agentic examples** for the cases that failed today: market-relative trigger + intraday PnL exit, cross-symbol condition, basket SIP with explicit symbols, expiry-window. This is a 30-minute change to `agentic_examples.json` and probably moves 4–5 of the 6 failures into the success column. Highest ROI.
2. **Run the grader to confirm `multi_trigger_agent` regressed** and bisect against `923977a`. If it regressed, the per-prompt agentic examples may have been re-ordered or trimmed — restore the multi-trigger example.
3. **Add `valid_until` (ISO date or relative phrase)** as an optional field on workflow drafts. Scheduler reads it at arming time and unschedules the job after that date. This is a small backend change that unlocks all the "valid till …" prompts and is the right model — not an LLM-generated TTL handler (per our earlier conversation).
4. **Sunset or harden `propose_holding_action`, `propose_threshold_order`, `propose_basket_allocation`**. Either fix routing so they're only invoked on their narrow happy path, or fold their behavior into `propose_workflow`. The current state — three narrower tools that take traffic away from the working tool — is the source of half the failures here.
5. **Short-circuit the catalog-dump branch** before LLM hop 2. Saves ~6–10s on every fallback.
6. **Strip the buying-power gate** from the dip-buy and similar examples. It's not actionable in v1.
7. **Add a real "no event calendar yet"sh rejection message** for earnings/dividend prompts. Right now we say "couldn't run X with the values I had" — that's the wrong frame; the right frame is "I don't have an earnings calendar; rephrase as a date-bound rule and I can do that."
8. **For the speedrun pitch:** prompts 5 and 6 are the ones to demo if these fixes land. They are exactly the "expressive in plain English, no other Indian platform handles them" cases — which is the differentiation message.

## File locations

- This report: `pivot/scripts/agentic_run_report.md`
- Raw JSON output (full responses, step-by-step drafts, latency breakdowns): `pivot/scripts/agentic_run_raw.json`
- Driver script (ephemeral, in /tmp): `/tmp/pivot_agentic_run.py`
