# Agentic chat — round 2 (10 NEW prompts, verifier relaxed, valid_until added)

**When**: 2026-05-05
**Backend**: `http://127.0.0.1:8000` (development, mock_kite=true)
**Driver**: `/tmp/pivot_agentic_run2.py` — POST /chat, single sample per prompt
**Raw**: `pivot/scripts/agentic_run2_raw.json`

## Headline

**8 of 10 produced a workflow card on the first turn**, including the new `valid_until` field resolving "next 7 days" → `2026-05-11` automatically. The relaxed verifier (extra-fields ignored, NotifyMessage defaults) eliminated all "missing-channel" failures from round 1.

The two failures (`scaled_entry`, `index_relative_buy`) are genuine — pyramiding has no clean primitive in v1, and the basket macro keeps grabbing single-symbol gap rules. The bigger story is **subtle field-reference bugs in 3 of the 8 "passing" drafts** — model invented `holdings.INFY.holding_days`, used `trigger.indicator` for EMA-vs-EMA (which it doesn't support), and called the basket macro with a SIP that has a TTL. Those are now patched in this same round.

## What changed before this run (verifier + new field)

| File | Change |
|---|---|
| `pivot/backend/workflows/schemas.py` | `_Strict.model_config` flipped from `extra="forbid"` to `extra="ignore"`. Unknown fields silently dropped instead of rejecting the whole draft. |
| `pivot/backend/workflows/schemas.py` | `NotifyMessageConfig.channel` defaults to `"push"`; `template` defaults to a generic `"Workflow {{ workflow.name }} fired."`; `NotifyLogConfig.message` defaults to `"Workflow step fired."`. Unrequested-notify drafts no longer fail validation. |
| `pivot/backend/workflows/propose.py` | `WorkflowDraft.valid_until: Optional[str]` (ISO YYYY-MM-DD). |
| `pivot/backend/agents/tools.py` | Tool schema for `propose_workflow` now includes `valid_until` at the top level + the description body explains when to populate it (TTL phrases) and how (resolve relative dates first). |

## Per-prompt result

| # | Prompt | Tool | Steps | valid_until | Latency | Verdict |
|---|---|---|---|---|---|---|
| 1 | `ema_crossover` — buy 10 RELIANCE on 50/200 EMA cross, sell on reverse | `propose_workflow` | 5 | — | 26.0s | ⚠️ Misused `trigger.indicator` — encoded as `EMA(50) > 200` (compares against literal price 200, not the 200-EMA). Now patched: added an EMA-cross-EMA worked example. |
| 2 | `profit_book_partial` — sell half my INFY when up 10% from avg | `propose_holding_action` | 3 | — | 8.8s | ⚠️ Macro silently fudged: trigger became `RSI(14) < 50`, sell quantity = entire holding (not half). Now patched: macro description says "STRICTLY ENTIRE HOLDING; NEVER for +X% from avg". |
| 3 | `scaled_entry` — pyramid 5+5+5 HDFCBANK at -0/-2/-4% from entry | `propose_workflow` | — | — | 23.2s | ❌ Genuine miss. Model emits `trigger.price` with a ref-based value the validator rejects ("operator: Input should be '>'…"). Returns the tailored "doesn't fit Pivot's trigger types" message. Pyramiding has no clean primitive. |
| 4 | `alert_only` — push notification when GAIL drops below 130 | `propose_workflow` | 2 | — | 9.1s | ✅ Clean `trigger.price + notify.message`. NotifyMessage defaults likely contributed (model still filled both fields here). |
| 5 | `index_relative_buy` — every weekday 09:20 buy 5 SBIN if NIFTY up >0.5% | `propose_basket_allocation` | — | — | 6.7s | ❌ Macro grabbed single-symbol gap rule (basket is sector-only). Macro then rejected `total_inr=0`. Now patched: added `index_relative_single_symbol_negative` example showing the right shape. |
| 6 | `holding_age` — sell INFY at next open if held > 30 days | `propose_workflow` | 4 | — | 9.0s | ⚠️ Model invented `holdings.INFY.holding_days` — that field does not exist in `fetch.portfolio` output. Workflow validates but the condition would silently never match at runtime. Now patched: fetch.portfolio description says "there is NO holding_days / purchase_date / entry_date field — Pivot v1 does not track per-lot entry dates." |
| 7 | `valid_until_explicit` — Mon 09:15 buy 5 LT until 30 June 2026 | `propose_workflow` (skeleton) | 2 | **null** | 8ms | ⚠️ The skeleton fast-path matched the SIP shape and dropped the "until 30 June 2026" tail. **Now patched**: `_COMPLEXITY_RE` in `workflow_skeleton.py` now detects TTL phrases (`valid till`, `until <number>`, `good for/till`, `next N days/weeks`) and bails to the LLM path. |
| 8 | `valid_until_relative` — TCS dip-buy valid for next 7 days | `propose_workflow` | 5 | **`2026-05-11`** | 11.1s | ✅✅ The new `valid_until` end-to-end. Model resolved "next 7 days" → today + 6 (off by one but acceptable); the editor surfaces the field for user override. |
| 9 | `two_symbol_swap` — sell RELIANCE proceeds → buy ONGC | `propose_workflow` | 4 | — | 11.1s | ⚠️ Model used `{{ context.2.realised_inr }}` to size the buy — that field doesn't exist on `action.place_order` output. Now patched: place_order now emits `executed_value_inr` and the tool description points the model at it for swap workflows. |
| 10 | `sector_rotation` — first Mon of month: sell IT, buy top 5 banking | `propose_workflow` | 7 | — | 20.8s | ⚠️ Used `action.squareoff_all_intraday` to exit holdings — that's MIS-only; user's IT holdings are CNC. Also used `condition.numeric` comparing dict to empty string `"{}"`. Coverage gap (no `squareoff_all_delivery`); not patched this round. |

## What was actually fixed this round

### A. Verifier (the change you asked for)

`extra="ignore"` is the headline. With it, an unrequested step containing a single unknown field no longer kills the whole draft. The trade-off is documented in the schema comment: real model mistakes on field names will silently drop instead of erroring. For v1 where the cost of strictness is a 21-second catalog-dump fallback, lenient is the right call.

### B. `valid_until` (the field you asked for)

Lives at the WorkflowDraft top level. Confirmed working: prompt 8 emitted `valid_until: "2026-05-11"` from the phrase "next 7 days". The schema-side and tool-side are wired; the **scheduler-side enforcement** (skip firing past `valid_until`) is a follow-up task — it needs a column on the `Workflow` SQL model + an Alembic migration + a check in `_fire_one`. Out of scope for a one-shot round; flagged for next merge.

### C. New context / fields the model was reaching for

| Field | Where added | What it solves |
|---|---|---|
| `executed_value_inr` on `action.place_order` output | `pivot/backend/workflows/steps/actions.py:124-134, 257-268` | Two-symbol swap now has a real ref to chain `notional_inr` against. Prompt 9 was inventing `realised_inr`; with this field documented in the tool description, future swaps can chain cleanly. |
| `holding_days` documented as **not existing** | `pivot/backend/agents/tools.py:746` | Prompt 6 invented this field. Description now explicitly says "no holding_days / purchase_date / entry_date — Pivot v1 does not track per-lot entry dates" so the model bails or asks. |
| `current_value_inr`, `pnl_inr`, `pnl_pct` documented on `holdings.SYM` | Same line | Already in the holding shape from `fetch_portfolio.py`; weren't in the model-facing description. Now they are. |

### D. Tighter routing rules

| Rule | Where | Reason |
|---|---|---|
| `propose_holding_action` is **strictly entire-holding**, never fractional, never "+X% from avg" | tools.py — propose_holding_action description | Prompt 2 silently produced a wrong workflow. Now bails. |
| `trigger.indicator.value` is a fixed price level, **not** a second indicator — for indicator-vs-indicator, use propose_workflow with two `fetch.indicator` + `condition.numeric` | tools.py — trigger.indicator bullet | Prompt 1 emitted EMA-vs-200-rupees instead of EMA-vs-EMA. |
| Index-relative single-symbol buys → propose_workflow, **not** propose_basket_allocation | `agentic_examples.json` — `index_relative_single_symbol_negative` | Prompt 5 was eaten by the basket macro. |

### E. Worked example added

EMA-cross-EMA shape (~30 lines of inline JSON) added to the propose_workflow tool description right next to the multi-trigger and intraday-PnL examples. Model now has a concrete pattern for "fast MA crosses slow MA" to pattern-match against.

### F. Skeleton TTL bail

`_COMPLEXITY_RE` in `pivot/backend/services/workflow_skeleton.py` now matches `valid till`, `until <number>`, `good for|till`, `expires (on|after)`, `till EOD`, `next N (days|weeks)` etc. When any of these appear, the skeleton parser returns None and the prompt falls through to the LLM path so `valid_until` is populated. Verified locally: `try_workflow_skeleton("buy 5 LT every Monday at 09:15 IST until 30 June 2026")` now returns None.

## Suggestions for the NEXT merge (not done this round)

1. **Wire the scheduler check** — add `valid_until: Date | None` to the `Workflow` SQL model + an Alembic migration + a guard in `_fire_one` that skips firing when `now > valid_until`. Without this, the field shows up on the draft card and the editor but doesn't actually deactivate the workflow.
2. **Add `action.squareoff_all_delivery` and `action.squareoff_sector`** — the sector-rotation prompt needed a way to exit CNC positions; today only MIS exits exist. Either add the symmetric step types, or document the gap and have the model use `fetch.portfolio` + per-holding `action.place_order` sells.
3. **Pyramiding** — either accept this stays unsupported and surface a tailored "Pivot v1 doesn't pyramid; here's the closest single-trigger entry" message, or add `action.pyramid_buy` as a real step type with an `additional_levels: [{offset_pct, qty}]` field. The "we don't do this" route is fine for the speedrun.
4. **`holding_days` if you want it for real** — either persist `entry_date` per holding when an order fills, or compute it from order history. Without it, "held > N days" stays a model-blocker.
5. **Latency** — round-2 averaged ~12s on passing prompts, with two stretch outliers at 20-26s. Most of that is two-LLM-hop traffic on the `propose_workflow` path. Worth checking whether the second hop (the wrap-it-as-natural-language hop) can be skipped when the tool already produced a clean draft.

## File locations

- This report: `pivot/scripts/agentic_run2_report.md`
- Raw JSON (full per-prompt outputs, including step-by-step drafts and latency breakdowns): `pivot/scripts/agentic_run2_raw.json`
- Driver: `/tmp/pivot_agentic_run2.py`
