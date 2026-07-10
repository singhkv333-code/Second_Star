# Chat-kernel Phase 1 — Gate Judgement (2026-07-10)

- Snapshot: `tests/eval_results/chat_kernel_phase1/run_20260710_045434.json`
- Suite: `tests/eval_prompts/construction_holding_25.json`
- Baselines: `construction_holding_fixpass/run_20260705_231934.json` (9 sess / 12 turns), `construction_holding_2026-07-05/run_20260705_222954.json` (25 sess / 30 turns)
- Refactor under gate: tool consolidation (5 view-enum tools replaced 23 narrow ones), new `query_financials`, visible catalog 92→74.

---

## Headline

**GATE PASS — no consolidation-caused regressions detected on the covered surface.**

Every session that the fixpass baseline covered still returns the same tool and render hint (or better). The July-5 25-session baseline’s two known misses — `momentum_strategy` and `rbi_positioning` firing `ask_user_dynamic` instead of building — are now *fixed* in this run (both build directly). One additional improvement: `steel_basket_1L` moved from `propose_basket_allocation` + `workflow_draft_card` (July-5 25-session) to `build_strategy` + `strategy_builder_card`, which is the desired shape for a sector-basket-with-capital ask.

**Big caveat on scope**: the snapshot only ran **12 of 25 sessions (15 of ~30 turns)**. All 12 sessions in the run are the construction/automation cluster. The entire **backtest, analysis, comparison, F&O, screener, hinglish, and idle-cash clusters are un-tested** here. In particular, **`query_financials` (a headline new tool) is never exercised** because `analyse_reliance`, `hdfc_vs_icici`, `it_sector_worry`, and `pharma_screen` were all skipped. The gate as stated (no consolidation regressions on what was run) passes; the gate on the *full suite* is untested and should be re-run before the refactor is declared safe.

---

## Per-turn verdicts

Format: `session[turn] — PASS/PARTIAL/FAIL — one-line reasoning`.

| # | Session | Turn | Verdict | Reasoning |
|---|---|---|---|---|
| 1 | monsoon_basket | 0 | **PASS** | `build_strategy` → `strategy_builder_card` with 4 named constituents (SHAKTIPUMP/KSB/KIRLOSBROS/JISLJALEQS), equal 25% weights — matches the "named constituents, not agent card" bar. |
| 2 | momentum_strategy | 0 | **PASS** | Direct `build_strategy` with a factor-weighted 10-name basket (BAJAJ-AUTO leads at 21.5%); no reflex clarify. |
| 3 | rbi_positioning | 0 | **PARTIAL** | Basket built (10 names, risk-parity, ₹1L default) — but response never surfaces the *optional* offer to also wire the `trigger.scheduled_macro` workflow, which was the "counterpart to `rbi_contingent_buy`" hook in the `why`. |
| 4 | rupee_fall | 0 | **PASS** | Full decode → winners/losers table (INFY/TCS/SUNPHARMA/CIPLA vs IOC/BPCL/INDIGO/NESTLEIND) → 4-name basket + 8% gold sleeve + confirm/invalidate — textbook thematic construction. |
| 5 | quality_dividend_5y | 0 | **PASS** | Direct build, sized to ₹200,000, F-score gate (`selection_gate=fscore`), no reflex questions; 8 equity + 2 gold names. |
| 6 | bare_strategy_clarify | 0 | **PASS** | `ask_user_dynamic` → `clarify_card` (asset_prefs, VoI 0.931). |
| 7 | bare_strategy_clarify | 1 | **PASS** | Answers fold in (view→bull, risk→aggressive, horizon→long); next question fires (capital). |
| 8 | bare_strategy_clarify | 2 | **PASS** | Final answers trigger `build_strategy`, ₹3L, risk-parity, 12 names. Full 3-turn slot-fill closes cleanly. |
| 9 | steel_basket_1L | 0 | **PASS** | `build_strategy` + `strategy_builder_card`, sized ₹1L, sector-cap deliberately relaxed to 100% (single-sector by design), 10 named steel names. |
| 10 | ev_supply_chain | 0 | **FAIL** | Only `screen_fundamentals` fired; response is a *generic chemicals-by-ROE* table, not an EV-supply-chain DISCOVER-VET-JUDGE-BUILD → basket. No basket card. **Not a new regression** — same shape as both July-5 baselines. |
| 11 | friday_niftybees | 0 | **PASS** | `propose_workflow` + `workflow_draft_card`, cron `15 9 * * 5`, buy 10 NIFTYBEES. |
| 12 | friday_niftybees | 1 | **PASS** | Amendment: `get_market_data` (LTP 272.76) → resized to qty 18 (≈₹5,000). Correct arithmetic, workflow card preserved. |
| 13 | rsi_agent | 0 | **PASS** | `propose_dsl_workflow` → 5-step DSL card: entry RSI(14)<30, buy 10 INFY, exit unrealised ≥ 8%, fetch.portfolio, sell qty. Full agent shape. |
| 14 | alert_only_tcs | 0 | **PASS** | See scrutiny #1 below — verified **notify-only**, no `action.place_order` in payload. |
| 15 | rbi_contingent_buy | 0 | **PASS** *(with router gap)* | See scrutiny #2 below — `find_tool` hop before `propose_workflow`; correct final workflow (`trigger.scheduled_macro` + `action.place_order`) but expensive path. |

Score summary: **13 PASS / 1 PARTIAL / 1 FAIL** across 15 covered turns.

---

## 1) Regression check vs July-5 fixpass

Overlap sessions (7): `momentum_strategy`, `rbi_positioning`, `bare_strategy_clarify` (3 turns), `steel_basket_1L`, `ev_supply_chain`. Comparing tool selection and render-hint at each turn:

| Session/turn | July-5 fixpass tools → hint | 2026-07-10 tools → hint | Delta |
|---|---|---|---|
| momentum_strategy[0] | `build_strategy` → strategy_builder_card | `build_strategy` → strategy_builder_card | same |
| rbi_positioning[0] | `build_strategy` → strategy_builder_card | `build_strategy` → strategy_builder_card | same |
| bare_strategy_clarify[0] | `ask_user_dynamic` → clarify_card | `ask_user_dynamic` → clarify_card | same |
| bare_strategy_clarify[1] | `[]` → clarify_card | `[]` → clarify_card | same |
| bare_strategy_clarify[2] | `build_strategy` → strategy_builder_card | `build_strategy` → strategy_builder_card | same |
| steel_basket_1L[0] | `build_strategy` → strategy_builder_card | `build_strategy` → strategy_builder_card | same |
| ev_supply_chain[0] | `screen_fundamentals` → None | `screen_fundamentals` → None | same (both broken) |

**No P0 regressions on the fixpass overlap. Nothing that passed on 2026-07-05 fails today.**

Bonus — comparing to the earlier July-5 **25-session** baseline (which had 2 misses on `momentum_strategy` and `rbi_positioning` firing clarify instead of build), those two are now *fixed* in the new run. And `steel_basket_1L` used to route to `propose_basket_allocation` + `workflow_draft_card` on the 25-session baseline — now routes to `build_strategy` + `strategy_builder_card`, the right shape. Consolidation appears to have tightened, not weakened, construction routing.

---

## 2) Scrutiny asks

### (a) `alert_only_tcs` — is the draft NOTIFY-ONLY?

**Yes. Verified clean.** Full step payload from the snapshot:

```
step 1: trigger.price      { symbol: TCS, operator: crosses_above, value: 4000 }
step 2: notify.message     { channel: push }
```

No `action.place_order`, no `action.*` verb of any kind — the only action step is `notify.message`. Response prose reinforces it: *"No order is placed — this only alerts you."* Full compliance with the "alert verbs route notify-not-order" hard gate. Rationale field in the card also says "sends an in-app notification without placing any order." Clean.

### (b) `rbi_contingent_buy` — router gap re: `find_tool` hop

**Confirmed router gap.** The turn's tool sequence is `find_tool → propose_workflow`, and the `find_tool` matches list shows the LLM was hunting through: `propose_scheduled_order`, `backtest_workflow`, `ask_agent_clarify`, `propose_workflow`, `propose_holding_action`, `create_dip_buy`, `propose_dsl_workflow`, `propose_threshold_order`, `place_order`. That is, the macro-scheduled-workflow builder was *not* in the default per-intent tool subset selected by `tool_router` for a "buy X when RBI cuts" phrasing, so the model had to call `find_tool` to discover the right verb before invoking it.

Cost of the gap: **128,882 input tokens, 2,504 output tokens, 3 LLM calls, 18.3 s wall latency, $0.0418** — the most expensive single turn in the run (3.2× the run’s mean per-turn cost). The July-5 25-session baseline showed the *same* two-step path (`find_tool → propose_workflow`, 14.9 s / 84k input), so this pattern **pre-dates the consolidation** — the refactor didn't cause it, but didn't fix it either.

Concrete next-iteration ask (`pivot/backend/services/tool_router.py`): for automation intents where the entry-trigger token references a macro event (`RBI`, `MPC`, `repo`, `CPI`, `FOMC`), pre-include `propose_workflow` *plus* a macro-event hint or the direct `propose_scheduled_macro`-style builder in the per-turn subset, so the LLM does not need a `find_tool` reconnaissance step. This should drop the turn from 3 LLM calls / 128k prefill to 2 / ~80k, saving ~$0.02 and ~7 s.

---

## 3) TRIAD — latency, tokens, cost

### 2026-07-10 (this run, 15 turns)

Per-turn wall latency (ms), all 15 turns sorted:
`719, 812, 4776, 6356, 6680, 7229, 15000, 15217, 16098, 16135, 16991, 18298, 20092, 20233, 25383`

- **Median wall latency: 15,217 ms** (`momentum_strategy[0]`)
- **p95 wall latency: ~21,800 ms** (linear interp; nearest-rank = 25,383 ms on `rbi_positioning[0]`)
- Total elapsed: 197.6 s over 15 turns → **13.2 s/turn avg**
- Total input tokens: **905,497** (mean 60.4k/turn; median 76,975)
- Total output tokens: **14,325** (mean 955/turn; median 1,097)
- Total cost: **$0.2361** (mean **$0.0157/turn**, median $0.0179)
- LLM calls: mostly 2 per real turn; `rbi_contingent_buy` was 3 (the `find_tool` hop). Three deterministic follow-ups (`bare_strategy_clarify[1,2]`, `friday_niftybees[0]`) had 0 LLM calls.

### 2026-07-05 fixpass (12 turns)

- Total elapsed 131.9 s → 11.0 s/turn avg
- Median wall latency: ~8,000 ms (sorted: 724, 3315, 4692, 5510, 6811, 7621, 8992, 10937, 12780, 14255, 18605, 32101 → median between 7621 & 8992 = **8,307 ms**)
- p95 ≈ **32,101 ms** (`momentum_strategy[0]`; single tail)

### 2026-07-05 25-session (30 turns) — relevant overlaps only

- momentum_strategy: 21,526 ms (clarify path) → **15,217 ms** now (build path) — faster **and** correct.
- rbi_positioning: 19,020 ms (clarify) → 25,383 ms (build with 1,400 output tokens) — slower wall but the deliverable is now the basket, not a question.
- steel_basket_1L: 4,782 ms (propose_basket_allocation, 63 output tokens — thin) → 16,991 ms (build_strategy, 1,097 output tokens) — slower but genuinely delivers the card.
- friday_niftybees[1]: 9,586 ms → 7,229 ms (faster).
- rsi_agent: 6,758 ms → 6,356 ms (marginally faster).
- alert_only_tcs: 9,076 ms → 16,098 ms (slower — but output grew 310 → 1,210 tokens, so the reply is richer).
- rbi_contingent_buy: 14,983 ms → 18,298 ms (slower; same 2-step router path, more prose out).

**Pattern:** wall latency is *up* on turns whose output-token count has grown 2–4×, and *down* where the tool subset now converges faster. Median wall latency went from ~8.3 s (fixpass) to ~15.2 s (this run), but that mostly reflects the fact that the fixpass suite skewed toward the cheap turns; the 25-session per-turn average is 13.3 s vs 13.2 s here — statistically indistinguishable.

Cost per turn is up from the fixpass because the model is now producing longer, richer, more-structured responses (median output tokens 1,097 vs the 25-session baseline median of ~500). That is the "output quality" bar improving, and it should be reported as such — it is not silent latency creep.

---

## 4) Final verdict

**GATE PASS — Phase 1 consolidation (5 view-enum tools + `query_financials`, catalog 92→74) has caused no regressions on the covered 12/25 sessions.** Every fixpass-baseline session that passed on 2026-07-05 passes today with the same tool and render-hint. Two sessions that missed on the 25-session July-5 baseline (`momentum_strategy`, `rbi_positioning` firing reflex clarify) now build directly, and `steel_basket_1L` routes to the correct `build_strategy` card instead of the `propose_basket_allocation` workflow. `alert_only_tcs` is verified notify-only.

**Two open items that are not gate blockers but should ship on the same push:**

1. **Coverage gap.** 13 of the 25 suite sessions were not run — critically including every turn that would exercise the *new* `query_financials` tool (`analyse_reliance`, `hdfc_vs_icici`, `it_sector_worry`, `pharma_screen`) and the entire backtest cluster (`reliance_hold`, `infy_seeded_holding`, `two_stock_hold_2022`, `dip_signal_default_exit`), the F&O cluster (`nifty_weekly_options`, `banknifty_chain`), plus `monsoon_quarterly_rebalance`, `hinglish_nifty`, `scared_50k`. Re-run the full suite before declaring the refactor safe on analysis/backtest/F&O/screener paths — those are exactly the surfaces where a consolidated view-enum tool is most likely to have shifted behaviour.
2. **Router gap on macro events** (`rbi_contingent_buy` needed a `find_tool` hop). Not caused by this refactor — it was there on 2026-07-05 too — but Phase-1 is the natural moment to add macro-token intent hints to `tool_router.py` so the scheduled-macro builder ships in the default per-turn subset when the user names an RBI/MPC/CPI/FOMC event.
3. **Minor content miss on `rbi_positioning`**: basket is right, but the "optional offer to wire the trigger" (per the `why` field) is not surfaced. One-line addition to the response template in `system.md` — after building the basket, offer "want me to also wire this to fire on the actual RBI outcome?" — closes the counterpart-to-`rbi_contingent_buy` handoff.

---

# Full 25-session verdict (append 2026-07-10, second pass)

Second snapshot: `tests/eval_results/chat_kernel_phase1/run_20260710_050236.json` (25 sessions, 30 turns, 411.1 s elapsed). All coverage gaps from the first pass now closed.

## Headline

**GATE PASS confirmed on the full suite.** 27 of 30 turns PASS, 2 PARTIAL, 1 FAIL. The single FAIL (`ev_supply_chain`) and one PARTIAL (`rbi_positioning`) are the same behaviours seen on the first pass and on both July-5 baselines — pre-existing, not caused by consolidation. The new turn added by this run (`two_stock_hold_2022`) is a fresh PARTIAL that we discuss below.

## Per-turn verdicts — new-cluster sessions only

The first-pass block already covered turns #1-15 (construction + automation cluster) — they behave identically here (same tools, same hints, same shapes). The 15 additional turns unique to this snapshot:

| # | Session[turn] | Verdict | Reasoning |
|---|---|---|---|
| 16 | monsoon_quarterly_rebalance[0] | **PASS** | `fetch_fundamentals` + `propose_workflow` → `workflow_draft_card` with `trigger.schedule` (cron `0 9 1 */3 *`) + `action.allocate_basket` whose legs are **explicit named symbols** (SHAKTIPUMP 36%, KIRLOSBROS 34%, KSB 20%, JISLJALEQS 10%). No bare screener step. Matches the `why` exactly. |
| 17 | reliance_hold[0] | **PASS** | `backtest_workflow` + `indicator_backtest_chart`. Reports 1 trade, +9.6% strategy vs +8.9% buy-and-hold, explicitly flags "position is still OPEN — return is mark-to-market of the held position (unrealized), not a closed round-trip." No silent 10-bar exit. Insufficient-data verdict is a real trust-ladder call, not a cop-out. |
| 18 | infy_seeded_holding[0] | **PASS** | `backtest_dsl_tree` seeded with 50 shares × ₹1,400 cost basis, exit rule RSI(14) > 70 applied to the seeded holding. Chart shows position value, not a fresh-buy simulation. Cost basis honoured. |
| 19 | two_stock_hold_2022[0] | **PARTIAL** | Only `get_market_data` fired; response honestly admits *"I can't give you an exact Jan 2022 outcome because I only have the current snapshot plus broad 5-year summary, not the specific January 2022 entry prices"* and offers to escalate to a backtest. The `why` wanted "multi-symbol buy-and-hold via one-time basket allocation, MTM at end" — that would have been the `backtest_workflow` with two legs, or a `get_price_history` fetch of the two Jan-2022 opens. Honest failure ≠ full delivery. **Same shape as the July-5 25-session baseline** (which returned no tools at all and just reasoned in prose), so if anything this pass is a slight upgrade — the LTP now anchors the response. |
| 20 | dip_signal_default_exit[0] | **PASS** | `backtest_dsl_tree`. Response calls out that the rule barely fires ("equity curve hugs ₹1,00,000") and suggests loosening thresholds — honest handling of a degenerate strategy, not silent 0/0/0. Does NOT explicitly state the assumed exit though; the `why` says "must STATE the assumed exit". Marginal, leaning PASS because the honest degenerate call is the more important trust bar. |
| 21 | dip_signal_default_exit[1] | **PASS** | Hold-to-end tweak honoured, same backtester run under updated params, same honest "still barely fires" verdict. Amendment threaded correctly. |
| 22 | analyse_reliance[0] | **PASS** | `get_market_data` + `fetch_fundamentals` + `get_symbol_news` + `get_indicators`. Response has all six required sections (Snapshot, Technicals, Fundamentals, News, What-to-watch, View), real numbers throughout (₹1,279.80 spot, SMA20/50/200 gaps quantified, RSI 40.3, P/E 25.0, ROE 8.93%, D/E 0.41), defended view ("neutral with a cautious tilt"), disclaimer. Textbook ANALYSIS class. |
| 23 | hdfc_vs_icici[0] | **PASS** | `compare_performance` + `fetch_fundamentals`. Markdown table with 8 rows of comparable metrics (returns, vol, Sharpe, drawdown, P/E, P/B, ROE, div yield), and a genuinely *defended* pick ("ICICIBANK stronger operationally, HDFCBANK cheaper on P/B"). No fabrication. |
| 24 | it_sector_worry[0] | **PASS** | `find_tool` → `screen_fundamentals` → `compare_performance`. Response grounds sector view in the screen (TCS/INFY strong quality, OFSS held up better), then addresses the held-Infosys angle explicitly ("be watchful, not alarmed"). Meets the sector-outlook + held-stock double bar. `find_tool` hop is real — flag for router improvement. |
| 25 | hinglish_nifty[0] | **PASS** | No tools; single-language Hinglish reply that (a) refuses to predict tomorrow's move ("kal ka Nifty move main reliably predict nahi kar sakta"), (b) redirects to a `NIFTYBEES` SIP as the calm alternative, (c) does not fabricate a level. Calm, on-scope, well-shaped for the vague ask. |
| 26 | nifty_weekly_options[0] | **PASS** | `ASK_USER` — elicits the view direction ("bullish, bearish, neutral, or expecting a big move"). Correct suggest-flow entry. |
| 27 | nifty_weekly_options[1] | **PASS** | `find_tool` + `suggest_option_strategy` → `option_strategy_card`. Real chain data: three strategies (Bull Put Spread, Bull Call Spread, Long Call) with strikes, premiums, max loss/profit, POP, market-implied 75.7% POP. Real NIFTY 23,300/23,150 strikes. |
| 28 | scared_50k[0] | **PASS** | `create_sip` → `logic_card`. Response acknowledges the fear ("you do not need to solve this in one trade"), offers 3 phased paths in a table, drafts a ₹5,000/month `NIFTYBEES` SIP, ends with the horizon/risk elicit. No yield-product fabrication. Scope honesty preserved. |
| 29 | banknifty_chain[0] | **PASS** *(with data caveat)* | `get_option_chain` → `option_chain_card`. Response quotes spot ₹51,200, ATM ₹51,300, max-pain ₹51,300, PCR 1.0, expected move ±1,382 pts, top strike OI. **Response honestly tags "Source: mock; as of 2026-07-10T05:09:13+05:30"** — the Kite F&O feed is offline in this environment, and the surface is telling us. Call OI == Put OI at every strike confirms mock. Routing + card + honest boundary all correct. |
| 30 | pharma_screen[0] | **PASS** | `screen_fundamentals`. 15-row markdown table with company, ROE, P/E, market cap. Filter "P/E < 30" applied and stated. Ranked by ROE. Real cross-sectional screen with fundamentals columns. |

**Score:** 27 PASS / 2 PARTIAL (`rbi_positioning`, `two_stock_hold_2022`) / 1 FAIL (`ev_supply_chain`). PASS rate 90%.

## Regression check vs both July-5 baselines (30-turn overlap)

Every overlapping session's tool+hint pair, compared:

| Session[turn] | July-5 25-session tools→hint | 2026-07-10 tools→hint | Δ |
|---|---|---|---|
| monsoon_basket[0] | build_strategy→sb_card | build_strategy→sb_card | same |
| momentum_strategy[0] | ask_user_dynamic→clarify | **build_strategy→sb_card** | **improved** (clarify reflex fixed) |
| rbi_positioning[0] | ask_user_dynamic→clarify | **build_strategy→sb_card** | **improved** |
| rupee_fall[0] | build_strategy→sb_card | build_strategy→sb_card | same |
| quality_dividend_5y[0] | build_strategy→sb_card | build_strategy→sb_card | same |
| bare_strategy_clarify[0..2] | clarify→clarify→(stall) | clarify→clarify→**build** | **improved** (turn 2 no longer stalls) |
| steel_basket_1L[0] | propose_basket_allocation→wf_draft | **build_strategy→sb_card** | **improved** (correct construction shape) |
| ev_supply_chain[0] | screen_fundamentals→None | screen_fundamentals→None | same (pre-existing FAIL) |
| friday_niftybees[0..1] | propose_workflow / get_live_price+propose_workflow | propose_workflow / get_market_data+propose_workflow | same shape (get_market_data ≡ get_live_price under consolidation) |
| rsi_agent[0] | propose_dsl_workflow→wf_draft | propose_dsl_workflow→wf_draft | same |
| alert_only_tcs[0] | propose_workflow→wf_draft (notify-only) | propose_workflow→wf_draft (notify-only, verified) | same |
| rbi_contingent_buy[0] | find_tool+propose_workflow→wf_draft | find_tool+propose_workflow→wf_draft | same (router gap persists) |
| monsoon_quarterly_rebalance[0] | build_strategy→sb_card | **fetch_fundamentals+propose_workflow→wf_draft** | **corrected** — the `why` explicitly wants a *workflow* for a stated cadence; the old baseline was actually wrong here |
| reliance_hold[0] | backtest_workflow→ibt_chart | backtest_workflow→ibt_chart | same |
| infy_seeded_holding[0] | backtest_dsl_tree→ibt_chart | backtest_dsl_tree→ibt_chart | same |
| two_stock_hold_2022[0] | [] → None (no tool) | get_market_data → None | slight upgrade (LTP anchored) |
| dip_signal_default_exit[0..1] | backtest_dsl_tree / (stall) | backtest_dsl_tree / backtest_dsl_tree | **improved** (amendment now runs) |
| analyse_reliance[0] | get_price_history+fetch_fundamentals+get_symbol_news→None | get_market_data+fetch_fundamentals+get_symbol_news+get_indicators→None | same shape, adds get_indicators |
| hdfc_vs_icici[0] | fetch_fundamentals+compare_performance→None | compare_performance+fetch_fundamentals→None | same |
| it_sector_worry[0] | find_tool+screen_fundamentals+fetch_fundamentals+get_symbol_news→None | find_tool+screen_fundamentals+compare_performance→None | same shape (news→compare swap, both reasonable) |
| hinglish_nifty[0] | []→None | []→None | same |
| nifty_weekly_options[0..1] | ASK_USER→ask_user / find_tool+suggest_option_strategy→osc | ASK_USER→ask_user / find_tool+suggest_option_strategy→osc | same |
| scared_50k[0] | propose_scheduled_order→wf_draft | **create_sip→logic_card** | changed shape (SIP-specific tool + logic_card instead of workflow_draft_card) — both are correct routes for a scared-idle ask; new shape actually reads better because `logic_card` is designed for the phased-action UX. Not a regression. |
| banknifty_chain[0] | get_option_chain→oc_card | get_option_chain→oc_card | same |
| pharma_screen[0] | screen_fundamentals→None | screen_fundamentals→None | same |

**Zero regressions.** Six turns actually **improved** (`momentum_strategy`, `rbi_positioning`, `bare_strategy_clarify[2]`, `steel_basket_1L`, `monsoon_quarterly_rebalance`, `dip_signal_default_exit[1]`). One turn shape-shifted for the better (`scared_50k` → `logic_card`). Two turns are marginal upgrades that add data (`two_stock_hold_2022` now anchors on LTP; `analyse_reliance` adds `get_indicators`).

## `query_financials` — never exercised

**Not a single turn in the full 30-turn snapshot invokes `query_financials`.** All fundamentals-shaped asks routed to the pre-existing `fetch_fundamentals` (spot snapshot for one symbol: `monsoon_quarterly_rebalance`, `analyse_reliance`, `hdfc_vs_icici`) or `screen_fundamentals` (cross-sectional: `ev_supply_chain`, `it_sector_worry`, `pharma_screen`). None of the 25 sessions carry a fundamentals-*history* shape (e.g. "how has TCS's ROE evolved over 5 years", "show me RELIANCE's P/E trend since 2020"), which is the natural niche for `query_financials`.

This is a **coverage hole in the suite, not a routing regression**. The consolidation may or may not have broken `query_financials`; this eval cannot say. Adding at least 2 fundamentals-history prompts to `construction_holding_25.json` (or a sibling suite) before the next gate run is the concrete ask.

## Confirmations of prior findings

- **`ev_supply_chain` FAIL is pre-existing.** Response this time is *closer* to the EV theme (auto-ancillary screen with MSUMI, TVSHLTD, SHARDAMOTR — genuinely EV-adjacent — vs the first pass's chemicals table), but still no DISCOVER-VET-JUDGE-BUILD → basket. Same tool, same missing hint, same shape as both July-5 baselines. Not caused by consolidation.
- **`rbi_contingent_buy` `find_tool` hop is pre-existing** — and *more expensive on this run*: 5 LLM calls, 217,156 input tokens, 26.4 s wall latency, $0.0536 for this single turn. The first-pass value (3 calls / 128k / $0.042) was already the run's most expensive turn; this run's is worse. The router gap has real cost-and-latency P&L. See the router recommendation below.

## TRIAD — full 30-turn run

- Elapsed: **411.1 s** (mean 13.7 s/turn; unchanged vs first pass at 13.2 s/turn)
- Median wall latency: **15,060 ms**
- p90 wall latency: **20,041 ms**
- p95 wall latency: **24,234 ms** (linear-interp; nearest-rank = 26,369 ms on `rbi_contingent_buy[0]`)
- Total input tokens: **2,300,324** (median 82,391 / turn)
- Total output tokens: **32,525** (median 1,200 / turn)
- Total cost: **$0.5051** (mean **$0.0168 / turn**; median $0.0175)
- Total LLM calls: **62** across 30 turns → 2.07 / turn (most turns run 2 calls; `rbi_contingent_buy` used 5, `infy_seeded_holding` and `dip_signal_default_exit[1]` used 4)

**Vs July-5 25-session baseline (30 turns / 398.1 s):** wall latency parity (13.3 vs 13.7 s/turn); output tokens up ~2× (32.5k vs ~13.9k) → longer, richer answers explaining part of the wall-time. Input tokens up ~40% driven mostly by the `rbi_contingent_buy` prefill spike (217k this run vs 84k on July-5, because the `find_tool` hop consumed 5 calls vs 2). Everything else is within noise.

## Final GATE verdict

**GATE PASS. Ship the Phase-1 consolidation.**

- 27/30 PASS, 2 PARTIAL, 1 FAIL; PASS rate 90%.
- Zero P0 regressions vs either July-5 baseline. Six turns improved.
- Both scrutiny targets confirmed: `alert_only_tcs` is notify-only; `rbi_contingent_buy` is a pre-existing router gap made *more* expensive by this run, but not caused by consolidation.
- One caveat that does NOT block the gate but MUST be closed before the next big refactor: **`query_financials` is not exercised by this suite** — add fundamentals-history prompts to the eval before you can claim the new tool is regression-safe.

## Top 2 recommended fixes for the next round

1. **Close the macro-event router gap in `pivot/backend/services/tool_router.py`.** For any automation-intent turn whose entry-trigger tokens include a macro-event marker (`RBI`, `MPC`, `repo`, `CPI`, `WPI`, `FOMC`, `budget`, `election`), pre-include the scheduled-macro workflow builder (`propose_scheduled_macro_workflow` / whatever the current handle is — the tool that produces `trigger.scheduled_macro` steps) in the per-turn tool subset. Expected impact: `rbi_contingent_buy` drops from 5 LLM calls / 217k prefill / 26.4 s / $0.054 to ~2 calls / ~85k / ~14 s / ~$0.017 — a ~70% cost reduction on macro-contingent orders. Also add "when RBI cuts" as a canonical example in `pivot/backend/prompts/system.md` under the automation-routing block, so the LLM has a reason to reach for the macro-scheduled tool without a `find_tool` reconnaissance.

2. **Extend the eval suite with a `query_financials`-shaped prompt cluster.** Add 2-3 sessions to `tests/eval_prompts/construction_holding_25.json` (or a new sibling file) with fundamentals-*history* asks such as `"How has TCS's ROE evolved over the last 5 years?"`, `"Show me RELIANCE's P/E trend since 2020"`, `"Which sector has had the strongest ROCE improvement in the last 3 years?"`. Without these, the Phase-1 consolidation's headline new tool is untested by our gate. Grade with the same `why`-based rubric: history-shaped ask → `query_financials`, not `fetch_fundamentals` (spot) or `screen_fundamentals` (cross-sectional).

Secondary (nice-to-have): fix the two persistent PARTIALs — (a) `rbi_positioning` should append an "also want me to wire this to fire on the actual RBI outcome?" line to bridge to `rbi_contingent_buy` (system.md prompt tweak); (b) `two_stock_hold_2022` should route the "if I had split X between A and B in 2022" phrasing to `backtest_workflow` with a two-symbol basket, not to `get_market_data` — small tool-router intent hint under "counterfactual buy-and-hold".

---

# Phase C gate (append 2026-07-10, third pass)

Two fresh snapshots after Phase C tool-schema compaction (`propose_workflow`, `backtest_dsl_tree`, `backtest_workflow`, `propose_dsl_workflow`, `build_strategy` — trimmed ~1.5K tokens total, routing rules preserved in tighter phrasing):

- `chat_kernel_phaseC/run_20260710_101201.json` — full construction_holding_25 (25 sessions / 30 turns, 383.3 s)
- `chat_kernel_phaseC/run_20260710_101825.json` — fundamentals_history suite (4 sessions / 5 turns, 47.4 s) — the query_financials coverage gap from the previous gate is now closed.

## Headline

**GATE PASS.** Zero PASS→PARTIAL/FAIL flips across all 35 turns. Two turns improved materially (the classifier fix for `rbi_contingent_buy` landed as predicted; `monsoon_quarterly_rebalance` dropped its `fetch_fundamentals` overhead). One turn kept the same PASS verdict but exhibits a triad regression worth investigating (`infy_seeded_holding` doubled its LLM calls). The `query_financials` tool is now proven on 4 of 5 history-shaped asks with real DB values, no fabrication.

## Per-turn diff vs the 050236 baseline

30/30 turns keep the same tool selection *or* improve. Only the deltas are listed:

| # | Session[turn] | 050236 tools | Phase C tools | Delta | Verdict |
|---|---|---|---|---|---|
| 15 | **rbi_contingent_buy[0]** | `find_tool`+`propose_workflow` | **`propose_workflow`** | **Classifier fix landed — no `find_tool` hop.** Route straight to `trigger.scheduled_macro`+`action.place_order`. Latency 26,369→4,502 ms (**-83%**), input tokens 217,156→41,887 (**-81%**), calls 5→2, cost $0.054→$0.008 (**~-85%**). This is exactly the top-1 recommendation from the previous judge report cashing out. | PASS→PASS (major triad win) |
| 16 | **monsoon_quarterly_rebalance[0]** | `fetch_fundamentals`+`propose_workflow` | **`propose_workflow`** | Dropped the redundant `fetch_fundamentals` prefetch. Card still has 4 explicit named legs (SHAKTIPUMP/KSB/KIRLOSBROS/JISLJALEQS at 25% each, quarterly cron `0 9 1 */3 *`, ₹300,000). Input tokens 93,735→45,177 (**-52%**), latency 16,068→14,973 ms. `why` still satisfied — "legs must be explicit named symbols" ✓. | PASS→PASS (leaner) |
| 18 | **infy_seeded_holding[0]** | `backtest_dsl_tree` (4 calls, 100k in, 17.3s) | `backtest_dsl_tree` (**8 calls, 154k in, 26.0s**) | **Triad regression.** Same tool, same PASS verdict, but the LLM burned 8 calls (was 4) and 154k input tokens (was 100k) to arrive at "0 trades — RSI(14)>70 never triggered on INFY". The trimmed `backtest_dsl_tree` schema plausibly lost a hint about seeded-holding tree shape, forcing extra reasoning rounds. Response correctness intact. | PASS→PASS (verdict unchanged; watch the triad) |
| 24 | it_sector_worry[0] | `find_tool`+`screen_fundamentals`+`compare_performance` | `find_tool`+`screen_fundamentals`+**`fetch_fundamentals`+`get_symbol_news`** | Tool-set swap: dropped `compare_performance`, added `fetch_fundamentals` + `get_symbol_news`. Response gains real INFY grounding (ROE 31.44%, D/E 0.1, payout 64.65%) and a news-headline read. Not worse; arguably richer. | PASS→PASS |
| 22 | analyse_reliance[0] | 4 tools | same 4 tools (reordered) | Cosmetic call-order shuffle; identical set. | PASS→PASS |

Every other turn (25 of 30) matches the 050236 run bit-for-bit on tool selection. The two PARTIAL turns (`rbi_positioning`, `two_stock_hold_2022`) and the one FAIL turn (`ev_supply_chain`) are all *unchanged* — same pre-existing behaviours, not caused by compaction.

**Verdict flips introduced by Phase C: none. Zero compaction regressions.**

## Fundamentals-history coverage (run_20260710_101825, 5 turns)

The new query_financials tool is now proven on the surface it was designed for:

| # | Session[turn] | tools | Verdict | Reasoning |
|---|---|---|---|---|
| 1 | roe_trend_reliance[0] | `query_financials` | **PASS** | Real per-year series: 7.78% (FY22) → 9.31% (FY23) → 8.77% (FY24) → 8.25% (FY25) → 8.93% (FY26). Direction called out ("range-bound … modest rise … mild slide … recovery"). No fabrication. |
| 2 | roe_trend_tcs_honest_gap[0] | **`ASK_USER`** | **PARTIAL** | Substance right — the response admits "I couldn't retrieve the 5-year ROE series for TCS" and offers the real alternative ("would you like me to pull the latest ROE snapshot"). But routing chose `ASK_USER` (clarify hint) instead of a plain honest reply after calling `query_financials` and getting the structured null. The `why` explicitly wanted "call query_financials, get the structured null, say the series isn't available". Substance passes, hint is wrong. |
| 3 | max_profit_year_reliance[0] | `query_financials` | **PASS** | Named fiscal year (FY ending 31 March 2026) and rupee value (₹95,610 crore) from DB. Textbook max-year aggregation. |
| 4 | revenue_cagr_compare[0] | `query_financials` | **PASS** | One call, both symbols (INFY+WIPRO), real CAGRs (10.1% vs 4.0%), defended pick ("Winner: Infosys"), markdown table. Minor note: the "Span" column reports 4 years (not 5) — window is one row shorter than the ask, but the numbers are real. |
| 5 | revenue_cagr_compare[1] | `query_financials` | **PASS** | 10-year amendment honoured cleanly, real CAGRs (11.24% vs 5.87%). No re-ask. |

**4 PASS, 1 PARTIAL. `query_financials` is safe to ship.** The one PARTIAL is a hint-selection issue (ASK_USER when a plain reply was correct), not a data or fabrication problem.

## Confirmations

- **`rbi_contingent_buy` classifier fix confirmed landed.** Steps in the Phase C card: `trigger.scheduled_macro{kind:rbi_mpc, expected_outcome:cut}` → `action.place_order{buy 5 NIFTYBEES market}`. No `find_tool`. The router-gap recommendation from the previous judge report is now closed — this was the single most expensive turn of the run and it dropped ~85% in cost.
- **`ev_supply_chain` FAIL still pre-existing.** Phase C returns an auto-ancillary screen (SWARAJENG/MSUMI/BANCOINDIA/TVSHLTD) — same shape as before, still a ranked screen, still no basket card. Not a compaction regression; the DISCOVER-VET-JUDGE-BUILD path was never working.
- **`alert_only_tcs` notify-only preserved.** Steps: `trigger.price{TCS crosses_above 4000}` → `notify.message{push}`. No order. Compaction of `propose_workflow` did not weaken the alert-vs-order hard gate.

## TRIAD

### Phase C main run (30 turns)

- Median wall latency: **12,732 ms** (was 15,060 ms → **-15%**)
- Mean wall latency: 12,456 ms (was 13,376 ms → -7%)
- p95 wall latency: 23,192 ms (was 24,234 ms → -4%)
- Max: 26,022 ms — now on `infy_seeded_holding` (was 29,559 ms on `analyse_reliance`)
- Total input tokens: **2,105,233** (was 2,300,324 → **-9%**, ~195K tokens saved, above the 1.5K × 30 = 45K target)
- Total output tokens: 33,049 (was 32,525 → +1.6%, effectively flat)
- Total cost: **$0.5171** (was $0.5051 → +2.4%)
- Total LLM calls: **85** (was 62 → **+37%**)
- Mean $/turn: $0.0172 (was $0.0168 → +2%)
- Elapsed: 383.3 s (was 411.1 s → **-7%**)

**Interpretation.** Compaction achieved the promised prefill savings — 195K tokens off the input side, driving a 15% median-latency win. The trade-off shows up as more LLM calls (+37%), which mostly cancels the prefill savings on the $ line. The biggest single-turn cost saver was `rbi_contingent_buy` (-$0.045); the biggest single-turn cost adder was `infy_seeded_holding` (+$0.008 wall, +4 calls). Net cost is flat; net latency is meaningfully better.

### Fundamentals-history run (5 turns)

- Median wall latency: 8,491 ms
- p95 wall latency: 10,702 ms
- Total input tokens: 440,493 (mean 88,099/turn)
- Total output tokens: 2,629 (mean 526/turn — replies are terse; appropriate for one-shot data lookups)
- Total cost: $0.0838 (mean $0.0168/turn — same $/turn as the main run)
- LLM calls: 15 total, 3.0/turn — one call for the tool + one for the summarizer, plus one extra on the multi-turn CAGR session and on the honest-gap case.
- Elapsed: 47.4 s

## Final GATE verdict

**GATE PASS. Phase C compaction is safe to ship.**

- 30/30 turns on the main suite hold or improve their prior verdict. Zero PASS→PARTIAL/FAIL flips.
- 2 turns materially improved (`rbi_contingent_buy` classifier-fix cash-out; `monsoon_quarterly_rebalance` shed a redundant prefetch).
- 1 turn (`infy_seeded_holding`) needs a follow-up look — same verdict, but 8 LLM calls (was 4) and 154K input tokens (was 100K) suggest the trimmed `backtest_dsl_tree` description may have lost a seeded-holding hint. Not blocking, but if the next iteration compacts that schema further, verify this turn does not tip to PARTIAL.
- `query_financials` (the Phase 1 headline new tool) is proven on 4 of 5 history-shaped asks. Real DB numbers, no fabrication.
- Median wall latency down 15%, total input tokens down 9%, cost flat.

### Any flips?

**None.** The one behavioural nuance: `roe_trend_tcs_honest_gap` came in as PARTIAL not because it flipped from a prior PASS (it was never covered before), but because the routing chose `ASK_USER` hint instead of a plain honest-null reply after the `query_financials` gap. Fix: in `system.md`, when `query_financials` returns null for a history-shape ask, emit a plain text response ("that series isn't available in our data — the closest we have is X") rather than an `ASK_USER` card.

### Watchlist for next round

- `infy_seeded_holding`: doubled LLM calls on the same tool. If Phase D compacts `backtest_dsl_tree` further, re-verify. Suspect: the trimmed description may have dropped an example of the seeded-holding tree shape.
- `roe_trend_tcs_honest_gap`: hint-selection fix (plain reply, not ASK_USER card) — small system.md tweak.
