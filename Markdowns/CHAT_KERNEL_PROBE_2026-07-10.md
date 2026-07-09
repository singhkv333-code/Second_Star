# GPT-5.4-mini probe — findings that drive the chat-kernel redesign
_2026-07-10 · 8 selection prompts × {current 91-tool catalog, 14-tool consolidated candidate} + 4-question model interview, run live against the Azure deployment with reasoning summaries enabled. Script + raw JSON in the session scratchpad (`probe_gpt54mini.py`, `probe_results.json`)._

## Headline numbers

| | current catalog | consolidated candidate |
|---|---|---|
| tools shown | 91 | 14 |
| tool-def payload | 110,118 chars (~27K tok) | 6,733 chars (~1.7K tok) |
| selection latency (8 prompts) | 5.6–12.0s | **3.0–6.4s** |

## Selection results (the 3 that decide the design)

| prompt | current | consolidated |
|---|---|---|
| "which year did Reliance have max profit?" | `web_search_brief` ❌ (would miss/fabricate) | `query_financials(agg=max, years=12)` ✅ |
| "buy 10 INFY when RSI < 30" | **no tool — asked "which timeframe?"** ❌ (the known over-clarify failure, reproduced live) | `propose_automation(action=order)` ✅ |
| "alert me when TCS crosses 4000, don't buy" | `propose_dsl_workflow(notify_only)` ✅ | `propose_automation(action=notify)` ✅ |
| "steel basket, 1 lakh, equal weight" | `propose_basket_allocation` (schedule-flavoured) ⚠️ | `screen_stocks` ❌ → descriptions must separate *find stocks* vs *construct investable basket* |

Consolidation is not just cheaper — it **changes selection behaviour for the better**, except where two consolidated tools share a noun ("stocks") without a disambiguating "best used for / NOT for" line.

## What the model itself asked for (interview, verbatim themes)

1. **Intent-enum over sibling tools** — for market data it wants ONE tool with an explicit `view`/`intent` field (`current_quote | today's_candle | historical_chart | 52w_range`) or descriptions of the form "Use ONLY for …".
2. **Hard `execution_mode` separation** — immediate / conditional / scheduled / alert_only as an explicit field, with "use notify-only when the user says alert/tell me/notify" in the description. (It complied with exactly this in the probe.)
3. **"Best used for" line per tool** to separate fundamentals-query vs screener vs comparison — the one family where it predicts (and demonstrated) confusion.
4. **Structured, machine-readable errors** — its requested shape: `{type, code, message, field, expected_type, received_value, retriable, suggested_fix, examples}` for validation; `{status: empty, alternatives[], suggested_next_step}` for empty results; `{type: ambiguous_match, candidates[]}` for ambiguity. It says it will attempt ONE automatic repair from context before asking the user — if the error names the field and expected type.
5. **Self-diagnosis works if fed minimal facts** — for "why didn't my SIP fire?", it can narrow causes from world knowledge (holiday, paused, funds, scheduler drift…) and lists the exact minimal facts it needs (status, expected run time, attempt result, failure code). Error payloads should carry those facts and let the model reason — not pre-baked apology prose.

## Design decisions locked by this probe

- **D1** Consolidated catalog ships with a `view`/`action`/`mode` enum per family (the model's #1 ask) and a one-line "Best for / NOT for" pair on every description.
- **D2** `screen_stocks` vs `build_strategy` get explicit mutual exclusion lines ("returns a ranked TABLE, never an investable basket" / "constructs an investable basket with capital — use when money or 'make/build' is mentioned").
- **D3** Tool errors adopt the model's requested structured shape (maps ~1:1 onto workflows/compat.py `Diagnostic`); `redirect_to` becomes a typed field, never regex-scanned prose.
- **D4** The tool-def budget target: ≤ ~8K tokens for the full always-on catalog (vs ~27K today), keeping total input comfortably inside the 40–45K cap with headroom for history + user context.
- **D5** Reasoning effort stays low for tool-selection hops (probe used low; selection was correct when schemas were unambiguous — matches the 2026-06-21 model bench).
