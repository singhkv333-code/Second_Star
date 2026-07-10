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

## Token-budget measurement (2026-07-10, post-Phase-0)

| component | ~tokens |
|---|---|
| system prompt (system_core + calibration examples + primer) | 28.7K |
| full 92-tool schema | 28.6K |
| today's routed subset (~8-12 tools) | ~3.6K |
| prompt modules (0-2 load per turn, all 10 = 13.8K) | ~1-3K |
| **today's static input** | **~32-36K** + 6-turn history (cap: 40-45K ✓) |
| naive show-all-92 | ~57K ✗ blows the cap |
| target: ~30 consolidated compact tools | ~5K → **~34K total, cache-stable ✓** |

**Locked decision D6:** the fixed-tool-set flip ships only when the
catalog is ≤ ~30 tools at probe-style compactness. Until then the regex
router stays. **D7:** the 28.7K core prompt is the next-largest trim
target (calibration-examples block) after consolidation.

## Round log — perf check after each round (owner rule 2026-07-10)

### Round 1 — Phase 0 + query_financials + consolidation Phase 1 + ToolRedirect
- live probe (5 fixed turns): **median 7.41s, max 13.79s**; tools 5/5 correct
  (get_market_data / query_financials / get_portfolio / get_indicators /
  propose_dsl_workflow)
- static input: core ~28,736 tok · full schema **74 tools ~27,420 tok** ·
  routed subset ~1/8
- eval triad (25 sessions / 30 turns, run_20260710_050236): 411.1s → **13.7s/turn**;
  12-session gate: GATE PASS, 13/15 PASS, $0.0157/turn, zero consolidation
  regressions; full-25 judge verdict pending

### Round 1.5 — judge's two fixes (macro-event intent + query_financials gate)
- FULL-25 gate: **GATE PASS — 27/30 (90%), zero regressions, 6 turns improved**
  (JUDGE_REPORT_2026-07-10.md)
- rbi_contingent_buy: 5 LLM calls / 217K tok / 26.4s / $0.054 →
  **1 tool call / 11.5s, no recon hop** (classifier fix, live-verified)
- query_financials eval: 4 PASS + 1 honest-gap-by-design (TCS ratio
  history absent in DB; null-note now steers to covered alternatives)
- perf: 5 fundamentals turns median **8.9s**; no regression
- known small gap: bare amendment turns ("make it 10 years") re-select
  tools from the amendment text → find_tool recovers at +1 hop; candidate
  fix in the durable-state phase (thread last-turn tools into selection)

### Round 2 — calculate + get_ipo consolidation (74 → 67 visible)
- live probe: **median 6.04s** (round 1: 7.41s), max 9.88s; calculate +
  get_ipo verified live (real arithmetic; honest-null on stale IPO name)
- static: 67 tools ~26.8K tok; catalog target ≤~30 before fixed-set flip
- next shrink candidates: the giant propose_workflow/backtest schemas
  (description compaction), options family stays deliberately split

### Round 3 — regression fix + last-tools threading (overnight close)
- REGRESSION caught & fixed: module-level HANDLERS broke monkeypatching
  (2 test fails were OURS); execute_tool now late-binds via globals
- last-turn READ tools now thread into next-turn scope (15-min TTL);
  bare-amendment UX still needs the durable artifact-state phase —
  scope verified no longer the blocker
- full-suite failure triage FINAL: 42 pre-existing (stub-LLM 14,
  fast_path 7, paper_valuation 5, ...) + 2 ours (fixed)

### Rounds A + C — overnight full execution (owner-authorized)
- **A3**: bare read-amendments re-run the prior tool ("make it 10 years"
  → query_financials 10y CAGRs directly; verified with the correct
  client-carried-history contract — single-message curls WIPE state by
  design, a testing gotcha now recorded)
- **A2**: the 6-turn cliff is BRIDGED — write-only ChatSummary now
  injected read-only on overflow + background refresh in the router;
  verified: fact planted T1, recalled exactly on T9
- **A1 (artifact ledger) + Phase B (loop extraction): DEFERRED to an
  attended session** — both rewire eval-won machinery; wrong risk
  profile for unattended end-of-context work
- **Phase C**: top-5 schemas compacted (catalog 26.8K→25.2K tok);
  judge GATE PASS, zero flips, median latency 15.1s→12.7s (−15%),
  −195K input tokens, cost flat (+37% LLM calls is the trade);
  watchlist: infy_seeded_holding 4→8 calls
- probes: round-A n/a · phase-C **median 6.65s, max 7.15s**
- server restarted cleanly under a logged launcher (scratchpad
  uvicorn.log) after a wedged reload chain was diagnosed

### Full-system 51-prompt sweep (owner-requested)
median 8.6s · 65K in/640 out per prompt · $0.0141/turn · 33/51 PASS, 7.59/10
mean · agents/constructions/comparisons/integrity 9+ · P0 found: list_agents
fabrication (tools_called=[]) · full report Markdowns/FULL_SYSTEM_50_REPORT_2026-07-10.md
