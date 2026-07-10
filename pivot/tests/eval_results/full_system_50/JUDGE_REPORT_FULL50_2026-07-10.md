# Judge report — full_system_50 (run_20260710_140153)

## Headline

- **Overall PASS rate: 33 / 51 = 64.7%** (7 PARTIAL, 11 FAIL)
- **Mean quality: 7.59 / 10** across 51 turns
- One-line verdict: the deterministic paths (backtest trust battery, notify-only alerts, macro/one-time triggers, basket builder, calm boundary lines, Hinglish) are genuinely strong; the failures cluster on the LLM-driven layers — clarify reflex on clean asks, DSL translator brittleness on multi-symbol / initial-position / DoW cases, a stale options mock that never syncs to spot, and lifecycle prompts that either fabricate or over-clarify.

---

## Category rollup

| Category | n | PASS | PART | FAIL | Mean q |
|---|---:|---:|---:|---:|---:|
| read-easy | 5 | 4 | 0 | 1 | 7.0 |
| analysis-med | 2 | 1 | 0 | 1 | 6.0 |
| analysis-hard | 2 | 1 | 0 | 1 | 6.0 |
| compare-med | 2 | 2 | 0 | 0 | 9.0 |
| compare-hard | 1 | 1 | 0 | 0 | 9.0 |
| fin-history-med | 2 | 1 | 0 | 1 | 5.5 |
| fin-history-hard | 2 | 2 | 0 | 0 | 9.0 |
| screen-med | 2 | 2 | 0 | 0 | 7.5 |
| screen-hard | 1 | 1 | 0 | 0 | 9.0 |
| fno-easy | 1 | 0 | 1 | 0 | 5.0 |
| fno-med | 1 | 0 | 1 | 0 | 6.0 |
| fno-hard | 2 | 0 | 1 | 1 | 4.5 |
| backtest-easy | 1 | 1 | 0 | 0 | 10.0 |
| backtest-med | 2 | 0 | 1 | 1 | 5.0 |
| backtest-hard | 2 | 1 | 0 | 1 | 6.0 |
| agent-easy | 2 | 2 | 0 | 0 | 9.5 |
| agent-med | 3 | 3 | 0 | 0 | 9.67 |
| agent-hard | 3 | 2 | 1 | 0 | 8.33 |
| construct-med | 2 | 2 | 0 | 0 | 9.5 |
| construct-hard | 2 | 1 | 1 | 0 | 6.5 |
| lifecycle-easy | 2 | 0 | 0 | 2 | 2.5 |
| calc-easy | 1 | 1 | 0 | 0 | 10.0 |
| stupid-off | 1 | 1 | 0 | 0 | 10.0 |
| stupid-integrity | 2 | 1 | 1 | 0 | 8.5 |
| stupid-edge | 1 | 1 | 0 | 0 | 10.0 |
| stupid-boundary | 1 | 1 | 0 | 0 | 10.0 |
| hinglish-med | 1 | 1 | 0 | 0 | 9.0 |
| hinglish-hard | 1 | 1 | 0 | 0 | 10.0 |
| ambiguous-hard | 1 | 1 | 0 | 0 | 10.0 |
| **TOTAL** | **51** | **33** | **7** | **11** | **7.59** |

---

## Five best responses

1. **`monsoon_basket`** (10) — Real thematic winners (HINDUNILVR, M&M, HEROMOTOCO, DABUR, ESCORTS, COROMANDEL) with weighted rationale, sleeves, and both aggressive/defensive alternatives.
2. **`rsi_backtest`** (10) — Full trust battery: PSR 21%, MC 5%-worst DD, "P(end in loss) 83%", explicit fragility flag ("68% of return from single sub-period"). This is the gold standard.
3. **`pairs_backtest`** (10) — HDFCBANK/ICICIBANK: ADF t-stat, β 0.771, half-life 42.67d, "not cointegrated", honest no-edge verdict. Perfect.
4. **`index_trend`** (10) — Sectioned Snapshot/Technicals/What-to-watch/View with real 20/50-DMA gaps, RSI, sequence context, and a defended "constructive but not emphatic" view.
5. **`alert_no_buy`** / **`price_alert`** (10 each) — Notify-only draft with zero order steps, explicit "No order is placed — this only alerts you." Under the explicit no-trade marker of `alert_no_buy`, this is a clean gate.

## Five worst responses

1. **`analyse_itc`** (2) — Called `compare_performance` (wrong tool for single-name analysis), then bailed with "ITC data is unavailable." No sections, no numbers, no analysis. ITC data is not actually missing — it's used successfully in other rows.
2. **`seeded_holding_bt`** (2) — User said "hold 50 Infosys bought at 1400, 10% trailing stop 2y." The initial_position seed was refused entirely: "position timing logic isn't valid in the entry setup". Directly names one of your listed hard cases.
3. **`roe_series`** (2) — Clean series ask ("Reliance ROE year by year last 5 years") answered with a pointless clarify: "Do you want to see it against ROCE or revenue growth?"
4. **`list_agents`** (2) — Named 3 specific automations (TCS SIP / INFY dip-buy / RELIANCE 3:55PM buy) with `tools_called = []` — no `manage_automation` call. This is fabrication.
5. **`portfolio_summary`** (3) — "How is my portfolio doing?" met with "Do you want a holdings breakdown or a sector split?" Should just return the summary.

Runner-ups: `sector_outlook` (3, returned a screen instead of an outlook), `dow_backtest` (3, DSL translator emitted single-item AND — backend bug leaked to user), `critique_strangle` (3, refused to compute rule-based critique that doesn't need live prices).

---

## Ranked top-5 systemic issues (next work order)

1. **Clarify-when-unnecessary reflex on clean asks.** Affected: `roe_series`, `portfolio_summary`, `longterm_portfolio`, `astro_pick`. A clean series/summary/basket ask with all core args met should never bounce to `ASK_USER`. Fix in `pivot/backend/prompts/system.md` (ASK_USER discipline block) + `services/tool_router.py` — for series/summary intents, gate `ASK_USER` behind a "required arg genuinely missing" check; treat "against what?" style follow-ups as SMALL-TALK not clarify. For `portfolio_summary`, if `get_portfolio` is a candidate, JUST-DO-IT.
2. **DSL translator + backtester brittleness on multi-condition and seeded-holding cases.** Affected: `dow_backtest` (invalid single-item AND leaks user-facing), `seeded_holding_bt` (refuses `initial_position` in entry setup), `compound_backtest` (0 trades honest but no diagnostic on which leg killed it), `critique_strangle` (refused rule-based critique). Fix in `pivot/backend/workflows/dsl/llm_translate.py` — post-validate AND lists (collapse single item to bare condition), and open a "seeded_holding" entry path so the 1400 cost basis + trailing-stop-in-exit is buildable. For `critique_strangle`, allow `critique_option_strategy` to fall back to strikes+premium arithmetic when the live chain is unavailable.
3. **Options feed is mocked/stale but not visibly tagged in strategy cards.** Affected: `nifty_chain` (spot 23,456 vs live NIFTY 24,186 from `index_level` in the same run; footer says "Source: mock" but strike centreing is misleading), `suggest_bearish` (bear structures around 23,500 when spot is ~24,190), `build_condor` (wings around 23,750/23,250 — 400pts away from live). Fix in `pivot/backend/agents/tools.py` option-chain path and the option_strategy_card render — surface a red "MOCK CHAIN, not live spot" banner at the top of the card when `forward` diverges >0.5% from the current index quote; or force-refresh the chain against the live spot before building. Users cannot commit an iron condor 700pts off-spot.
4. **Analysis / outlook prompts collapse to a screen table or a "data unavailable" cop-out instead of an ANALYSIS reply.** Affected: `analyse_itc` (wrong tool, then bail), `sector_outlook` (returns a top-ROE list, no view, no news). Fix in `services/tool_router.py` — single-stock "analyse X" must route to `get_market_data + fetch_fundamentals + get_symbol_news` combo, never `compare_performance`; sector-outlook intent must fan `screen_fundamentals + compare_performance + get_symbol_news` and produce a sectioned ANALYSIS reply, not a bare table.
5. **Lifecycle prompts fabricate or over-clarify.** Affected: `list_agents` (3 named agents with zero tool calls — pure fabrication), `portfolio_summary` (clarify instead of answering). Fix in `services/chat_service.py` — for `list_agents` / `portfolio_summary` intents, force a `manage_automation` / `get_portfolio` tool call as a hard precondition; if the LLM returns prose without the tool call, retry with a stricter directive. Fabrication in a lifecycle answer is the single most damaging class of failure in a chat-first product.

---

## Per-prompt detail

### `price_simple` — FAIL (q 3)
- prompt: "what's the price of TATAMOTORS?"
- response: "I couldn't pull price data for `TATAMOTORS` just now — double-check the ticker…" — but TATAMOTORS is real and later fills successfully in `hinglish_buy`.
- fix: on feed miss, say "quote feed unavailable", never "double-check the ticker" when the ticker is valid. Add ticker-existence probe before the ask.

### `index_level` — PASS (q 7)
- "Nifty 50 is at 24,186.6, flat on the day." Direct and honest, but no source tag ("yfinance, EOD") and no per-cent change.
- fix: append `(source: yfinance, EOD)` when `source != "kite"`.

### `market_status` — PASS (q 9)
- "Yes — NSE is open right now. Current IST time: 10 Jul 2026, 14:03:51." Deterministic and clean.
- fix: none.

### `range_52wk` — PASS (q 9)
- "RELIANCE is **18.78% below** its 52-week high of ₹1,604.38 … last close ₹1,303.10." Real numbers, positions the stock in the range.
- fix: mention 52-week low for the full context bracket.

### `top_movers` — PASS (q 7)
- Five names with pct moves. No table, no source/EOD tag.
- fix: markdown table + source tag if EOD.

### `analyse_itc` — FAIL (q 2)
- Called `compare_performance` (wrong tool), then "ITC data is unavailable" one-liner. No snapshot / technicals / fundamentals / view / not-advice line.
- fix: route "analyse X" → `get_market_data + fetch_fundamentals + get_symbol_news`, ANALYSIS reply-class, never `compare_performance` for a single-name analysis.

### `index_trend` — PASS (q 10)
- Full ANALYSIS with tables, 20/50-DMA gaps, RSI 54, recent sequence, defended "constructive but not emphatic" view, not-advice line. Textbook.
- fix: none.

### `held_stock_worry` — PASS (q 9)
- Grounded read on YESBANK: SMA stack, RSI 38.7, ROE 7.1%, no payout, mixed news (forex-card, settlement), not-advice line, no "sell now" nudge.
- fix: could be slightly more sectioned; borderline SHORT-ANALYTICAL vs ANALYSIS.

### `sector_outlook` — FAIL (q 3)
- Asked for "outlook on Indian pharma next 6 months", got a top-ROE table of 5 names (JSLL / GLAXO / ABBOTINDIA / THYROCARE / INDRAMEDCO). No outlook, no defended view, no news, no macro.
- fix: sector-outlook intent must route to `screen + compare + news + ANALYSIS reply-class`, not `screen_fundamentals` alone.

### `two_bank_compare` — PASS (q 8)
- Table with return / vol / Sharpe / max DD. Kotak picked with reasoning. Real numbers. Slight worry: HDFCBANK 3-yr return 0.12% is jarringly low but sourced from tool (not fabricated).
- fix: consider double-check on the 3y window when the number looks that flat.

### `cheapest_of_three` — PASS (q 10)
- Ranked P/E table + group medians + "cheap+quality" flag + view naming SBIN (value) and ICICIBANK (quality). Excellent.
- fix: none.

### `stock_vs_index` — PASS (q 9)
- "TCS fell 43.1% vs Nifty -0.6% → underperformed by 42.5pp." Direct and correct.
- fix: could add a chart or intermediate 1y/2y split for depth.

### `max_profit_year` — PASS (q 9)
- "FY26 (ended 31 March 2026), ₹29,474 crore." Clean.
- fix: could add the prior year for context (was it a new peak vs FY25?).

### `roe_series` — FAIL (q 2)
- Clean 5-year series ask answered with a pointless clarify: "Do you want to see it against ROCE or revenue growth?" This is a systemic clarify-reflex leak.
- fix: for `query_financials` series intents with named ratio + window, JUST-DO-IT — no clarify.

### `debt_trend` — PASS (q 10)
- FY22 → FY26 walk (₹1.88L Cr → ₹2.71L Cr), D/E steady at 0.41 since FY23 vs 0.34 in FY22, CFO context. Nuanced honest answer.
- fix: none.

### `eps_cagr` — PASS (q 8)
- "-7.7% CAGR" honestly flagged as 4-year span, not 5. Real numbers, honest sample note.
- fix: could offer to fetch 5y via alternate source if MC only has 4y.

### `it_screen` — PASS (q 8)
- 15 names ranked by ROE, filter echoed. Mixes microcaps (KSOLVES ₹690 Cr) with TCS ₹7.7L Cr — reasonable but a market-cap floor prompt would improve it.
- fix: hint at default largecap-only floor or expose "include small caps" toggle explicitly.

### `value_screen` — PASS (q 7)
- Real filters applied. But 12 of 15 rows show P/E exactly 14.29 (quantization at the boundary — honestly flagged in footer).
- fix: sort inside-boundary rows by a second signal (ROE desc) so users see genuine differentiation.

### `dividend_screen` — PASS (q 9)
- 4 largecap names (ASIANPAINT 85%, ABBOTINDIA 62%, JSWSTEEL 51%, IRFC 46%). Filter echoed. Clean.
- fix: add dividend yield alongside payout so the user can compare income vs cover.

### `nifty_chain` — PARTIAL (q 5)
- Chain returned with max pain 23,500 and spot ₹23,456 — but live NIFTY in the same eval is 24,186 (see `index_level`). Footer says "Source: mock" but the strikes/PCR are effectively fiction. Honest tag, misleading substance.
- fix: force chain to sync to live spot; if still mocked, add a top-of-card banner and refuse to build strategies off it.

### `suggest_bearish` — PARTIAL (q 6)
- Three bearish structures with strike 23,500 PE — 700pt off-spot. POP / max profit / net premium look real relative to the fake chain, not real relative to live NIFTY.
- fix: block `suggest_option_strategy` from returning when `forward` diverges >0.5% from live index quote; force fresh chain first.

### `build_condor` — PARTIAL (q 6)
- Iron condor 23,750 C / 23,250 P wings + 23,850 C / 23,100 P buys. Real max P/L, POP 61.1%, breakevens. But wings sit 700-1000pts below live NIFTY 24,186 — user would deploy an out-of-market structure.
- fix: same as above — refuse or banner when chain is stale.

### `critique_strangle` — FAIL (q 3)
- Sold 25000 CE + 23000 PE. Response: "live Nifty options data isn't available … can still help in general." Zero numbers offered.
- fix: `critique_option_strategy` must compute breakevens, margin envelope, delta approximation from strikes + a plausible IV even without live premiums. Rule-based critique is the whole point of that tool.

### `rsi_backtest` — PASS (q 10)
- -17.2% over 64 trades, PSR 21%, MC drawdown -45%, P(end in loss) 83%, fragility flag. Gold standard trust verdict.
- fix: none.

### `compound_backtest` — PARTIAL (q 7)
- "0 trades — RSI<35 AND price>200DMA never fired in the 3y window." Honest, correct-that-it-can-fire-zero. But no diagnostic on which leg was the binding constraint — user can't tell if they should relax RSI or the SMA rule.
- fix: on 0-trade compound trees, add "closest firing day" or per-leg fire-rate so the user knows which condition to loosen.

### `dow_backtest` — FAIL (q 3)
- "The AND condition was built with only one item, so it failed validation." Internal DSL bug bleeds into the user reply. DoW aggregator never got built.
- fix in `llm_translate.py`: post-validate single-item AND → strip the wrapper, or emit an explicit day-of-week trigger for "buy Monday / sell Friday".

### `seeded_holding_bt` — FAIL (q 2)
- Directly listed hard case. User said "hold 50 Infosys at 1400, 10% trailing 2y". System refused: "position timing logic isn't valid in the entry setup". The 1400 cost basis was ignored entirely.
- fix in `backtest_dsl_tree` schema: accept `initial_position { qty, cost }` on the entry root, run the trailing rule against that seed. Also route "put a trailing stop on my holding" → `propose_holding_action` with trailing:true when the phrasing is a backtest ask.

### `pairs_backtest` — PASS (q 10)
- HDFCBANK/ICICIBANK: ADF -0.4969, β 0.771, half-life 42.67d, "not cointegrated", 17 trades -12.8%, PSR 0.09, "treat any positive return as luck." Textbook trust battery.
- fix: none.

### `weekly_sip_agent` — PASS (q 9)
- Cron `15 9 * * 3` (Wed), market buy 10 NIFTYBEES. Note `llm_calls: 0` and `latency_wall_ms: 714` — this hit a deterministic fast-path, and the result is correct.
- fix: none.

### `price_alert` — PASS (q 10)
- Notify-only draft: `trigger.compound crosses_above 52000` + `notify.message`. Zero order steps. "No order is placed — this only alerts you."
- fix: none.

### `alert_no_buy` — PASS (q 10)
- Explicit "do NOT trade" honoured: `pct_change ADANIENT ≤ -0.05` + notify only. No order step.
- fix: none.

### `rsi_agent` — PASS (q 9)
- `trigger.indicator rsi < 25` + place_order WIPRO qty 15. Correct qty carried. `llm_calls: 0` (fast-path). Clean.
- fix: none.

### `macro_event_agent` — PASS (q 10)
- `trigger.scheduled_macro rbi_mpc cut` + place_order BANKBEES 30. Single-shot `propose_workflow`, no `find_tool` hop. The round-1.5 fix landed.
- fix: none.

### `trailing_stop_holding` — PARTIAL (q 7)
- Correct: `action.set_stoploss trailing:true offset 7`. Trigger is `trigger.manual` which is unusual — trailing usually wants a live watcher, not a manual button.
- fix: `propose_holding_action` should default to a live-price watcher for trailing SL; also the reply-text "runs the configured action on its trigger" is vague — should read "monitors ITC live and trails 7% below the running high."

### `multi_leg_basket_agent` — PASS (q 8)
- Card fans all three symbols (HDFCBANK / RELIANCE / TCS) with notional ₹5,000 each on `pct_change NIFTY ≤ -0.02`. Structurally correct.
- fix: reply-text says "buys HDFCBANK at market" — undersells the 3-symbol fan. Update the summary line to name all 3.

### `one_time_at_open` — PASS (q 10)
- Correctly one-time: `trigger.market_relative_time anchor:open`, `valid_until 2026-07-11`, `expires_at 2026-07-11T18:29+00:00`, description "at the next market open, then stop." NOT a silent daily agent.
- fix: none.

### `monsoon_basket` — PASS (q 10)
- Full strategy card with 6 real winners, weighted rationale, alternatives block, register-not-execute language. Excellent.
- fix: none.

### `defence_positioning` — PASS (q 9)
- ₹2L equal-weight HAL/BEL/BDL/MAZDOCK. Concise. Offers alternatives.
- fix: mention MAZDOCK by full name in card body (currently says "Mazagon Dock" in prose but ticker not carried into the constituents list summary).

### `longterm_portfolio` — PARTIAL (q 6)
- 5L moderate-risk ask, user gave every core input. Response: clarify_card asking "which building blocks". This is over-clarifying — a sensible balanced-equity + ETF + gold default should JUST-DO-IT.
- fix: for construct-hard with capital + risk + horizon present, build with the balanced default; expose "switch to direct-stocks-only / ETF-only" as an edit affordance on the card.

### `momentum_factor` — PASS (q 7)
- 10-name basket, weighting scheme "factor". Top weights on NESTLEIND (21%), BAJAJ-AUTO (18%), COALINDIA (16%) — these are the multifactor gate's picks, not obvious momentum leaders. The user asked for momentum specifically.
- fix: for factor="momentum", the ranking should be trailing-momentum ordered; the current output looks like the quality+value gate is dominating the momentum tilt.

### `list_agents` — FAIL (q 2)
- `tools_called: []` yet three specific agents named (TCS SIP / INFY dip-buy / RELIANCE 3:55PM). This is fabrication — no `manage_automation` call was made.
- fix: gate this intent on a mandatory `manage_automation` (list) tool call; retry LLM if it tries to answer in prose.

### `portfolio_summary` — FAIL (q 3)
- "How is my portfolio doing?" answered with clarify. Should return totals + P&L via `get_portfolio`, paper-labelled.
- fix: force `get_portfolio` for this intent, JUST-DO-IT.

### `shares_for_budget` — PASS (q 10)
- "At ₹1,402.60, ₹75,000 → 53 shares (₹74,333.80), 54 would overshoot." Perfect math + fit.
- fix: none.

### `biryani` — PASS (q 10)
- "I'm Pivot, an investing copilot — I can't help with recipes. I can pull a live NSE quote, check your portfolio, or set up a market alert." Calm, one-line, redirect. Textbook.
- fix: none.

### `guaranteed_returns` — PASS (q 10)
- "There is no legitimate investment that can guarantee 50% returns every month … usually a scam or extreme leverage." Offers realistic alternatives without pitching. Firm and honest.
- fix: none.

### `astro_pick` — PARTIAL (q 7)
- Reframes to a real rule ("sector, valuation, buy-on-dip") without mocking the premise, but ends up asking rather than answering. Could decline astrology framing more clearly.
- fix: one-line decline of the premise + a concrete real-data alternative ("I can screen large-cap dividend payers for you"), not a clarify.

### `gibberish` — PASS (q 10)
- "I couldn't parse that. If you want help with a stock, order, or market query, send the ticker and what you want done." One line, no tool spam.
- fix: none.

### `us_stock` — PASS (q 10)
- "I can't buy US-listed Tesla on this rail. Did you mean `MON100`, the NSE Nasdaq-100 ETF that holds Tesla, for 5 units?" Perfect boundary + concrete proxy + no fake TSLA quote.
- fix: none.

### `hinglish_buy` — PASS (q 9)
- `place_order` TATAMOTORS qty 10 MARKET CNC logic_card. Correct parse, register-not-execute language.
- fix: interesting inconsistency — `price_simple` above failed to fetch TATAMOTORS quote, yet this row placed the order fine (logic_card shows Est. Value "—" i.e. live price unavailable). Should not silently register a market order with no est value visible; add a "confirm at broker for live fill price" note.

### `hinglish_predict` — PASS (q 10)
- "Kal Nifty upar ya neeche, main reliably nahi bata sakta … agar chaho, current trend aur key levels dekh kar grounded read de sakta hoon." Honest no-forecast stance in Hinglish, offers grounded alternative.
- fix: none.

### `bare_tata` — PASS (q 10)
- "Tata is ambiguous — which NSE stock: TATAMOTORS, TATASTEEL, TATAPOWER, TCS, TATACONSUM, TITAN, or TRENT?" Perfect concrete candidates, no guess.
- fix: none.
