# Judge report — `iter_3`

## Headline
- Overall: **70/100 → C** (weighted mean over 30 prompts; 1 hard-gate counted as 0)
- 30 prompts scored, 1 hard-gated: `scalping_intraday_intent` (LLM fallback / "AI backend temporarily unavailable", `is_fallback: true`).
- Verdict: the iter_2 → iter_3 system.md patch landed **partially**. Of the 6 fabricated-capability re-probes it was written to fix, **3 cleanly passed** (`portfolio_state_check`, `exit_entire_position_techm`, `modify_pending_limit_titan` partial), **2 still failed in the iter_2 shape** (`cancel_single_symbol_lt` asks for an order ID, `exdividend_powergrid` says "I don't have the ex-dividend calendar in the current toolset"), and **1 partial-failed** (`earnings_calendar_drreddy` admits the gap, offers to look it up, but never actually called `get_upcoming_events`). The same fabricated-capability rot showed up in NEW shapes the patch did not anticipate (`basket_multi_sell` asks for quantities `get_holdings` would fetch; `dividend_yield_query_coalindia` correctly fetches price but punts on yield). Meanwhile, two NEW high-severity patterns emerged that were not on the iter_2 watchlist: **silent fabrication of an MOC order as a plain market buy** (`moc_order_shape`) and **silent fabrication of a news-conditional automation** (`news_conditional_rate_cut` drafts a workflow that cannot actually wait for an RBI decision). The backtest-prose-loses-numbers regression is intact and arguably worse: 4 of 4 backtest prompts produced zero numbers, all four using the same "I can run that … if you want" preamble system.md explicitly forbids.

## What's working

- **Educational / conceptual answers stay clean.** `concept_t_plus_one_settlement`, `tax_intraday_speculative`, `preopen_session_question`, `best_time_to_buy_niftybees`, `vague_help_me_save`, `zomato_eternal_rename` — all hit the right shape: no tool call, named numbers/times where relevant, no padding, no "I cannot answer that" deflection. The system.md "answer without tool when conceptual" rule continues to land.
- **`portfolio_state_check` is the headline patch win.** "remind me what stocks i'm holding and the rough pnl" → bot calls `get_holdings`, returns a 5-row markdown list with per-position P&L and a total. This is the exact shape iter_2's `portfolio_show_holdings` fabricated as "I don't have a live holding lookup here". The capability table reached this prompt.
- **`exit_entire_position_techm` uses `propose_holding_action` correctly.** "close out my TECHM completely, market price is fine" → bot calls `propose_holding_action`, renders `workflow_draft_card`. The special-case rule in system.md against `place_market_order(quantity=1)` for "sell entire X" worked here. iter_2's `sell_entire_position` placed a 1-share market order with a disclaimer — that exact failure is closed.
- **`oco_existing_ongc` is textbook.** OCO on an existing 50-share ONGC holding at SL ₹232 / target ₹268 → `create_oco_order` called, `logic_card: oco_order` rendered, prose is one sentence with symbol/qty/target/stop. Exactly what the AUTOMATION-vs-AGENT routing table prescribes.
- **`ambiguous_adani_buy` + `specific_adaniports_query` pair correctly.** Ambiguous "buy 10 shares of adani" asks for disambiguation across 6 ADANI tickers; explicit "ADANIPORTS price and 3-month chart" returns price + 3-month range + period return without re-asking. The Tata/M&M ambiguity rule generalised cleanly to the Adani family.
- **`trailing_sl_bajajauto` refuses to fabricate.** Trailing stop isn't in v1; bot asks one focused clarifying question instead of silently chaining a regular SL. Honest gap-handling.

## What's broken — systemic patterns

The three patterns below are ranked by what's fixable inside the 4-file allowlist
(`system.md`, `tools.py`, `dsl/llm_translate.py`, `scripts/eval_chat_quality.py`).

### 1. Backtest prose still produces zero numbers, and the F-anchor preamble is back on every single backtest prompt
- **IDs:** `backtest_extreme_threshold_icicibank` (F, 41), `backtest_three_cond_and_kotakbank` (F, 41), `backtest_benchmark_compare_grasim` (D, 60), `backtest_volatility_filter_eichermot` (F, 41). 4 of 4 backtest prompts produced no trade count, no return %, no win rate.
- **Root cause:** every one of these uses the exact phrasing system.md banned: *"I can run that as-is … If you want, I'll proceed with that interpretation now"* (`backtest_extreme_threshold_icicibank`); *"It looks like the NSE history isn't available right now, so I can proceed once that data source is back"* (`backtest_volatility_filter_eichermot`); *"It didn't run because I don't have NSE history for that symbol here"* (`backtest_three_cond_and_kotakbank` — for KOTAKBANK, a NIFTY 50 constituent). Two of these claim a data outage on prompts where one didn't fire (`backtest_three_cond_and_kotakbank` literally called `backtest_dsl_tree` per `tools_called`, then claimed it didn't run). The system.md "Backtests" section is a single paragraph and does not mention the "If you want, I'll proceed" forbidden phrase or require the prose to name trade count + return %.
- **Concrete next-iteration instruction (system.md, fixable this cycle):** in `pivot/backend/prompts/system.md`, expand the "Backtests" section into 4–6 lines covering: (a) after ANY backtest tool returns, the prose MUST include either trade count + headline return, OR the explicit string "0 trades — strategy never fired in this window", OR "the engine returned no metrics for this window — likely missing history for {symbol}/{window}"; (b) add explicit forbidden phrases: *"I can run that as-is"*, *"If you want, I'll proceed"*, *"It looks like the NSE history isn't available right now"*, *"the NSE history isn't available here"*; (c) add an anti-example: "User: backtest KOTAKBANK ... → WRONG: 'I don't have NSE history for that symbol here' (KOTAKBANK is a NIFTY 50 constituent; if backtest_dsl_tree returned empty, say so with the symbol/window, don't blame data). RIGHT: 'Backtested KOTAKBANK RSI<40 ∧ MACD>0 ∧ close>200EMA Jan-2021–Dec-2024. 11 trades, +X% return, win rate Y%.'"; (d) state explicitly that backtests on the FIRST turn with complete parameters MUST emit the result, not ask permission.

### 2. The capability patch generalised to 3 of 6 re-probes but mis-shoots on the order-ID and ex-dividend cases
- **IDs:** `cancel_single_symbol_lt` (F, 50 — "I need the order ID or trigger ID"), `exdividend_powergrid` (D, 60 — "I don't have the ex-dividend calendar in the current toolset"), `earnings_calendar_drreddy` (D, 60 — admits the gap, never calls `get_upcoming_events`). Plus shape-adjacent: `basket_multi_sell` (D, 60 — asks for quantities `get_holdings` would return), `dividend_yield_query_coalindia` (C, 71 — fetches price but punts on yield).
- **Root cause:** the markdown capability table in system.md says: *"cancel order #abc" → `cancel_order(order_id="abc")`* — but `cancel_single_symbol_lt` is "scrap the LT pending buy", which is the SAME shape as the iter_2 `modify_existing_order` failure (named by symbol, no broker ID), and the patch maps that shape only to *modify*, not *cancel*. The table also has *"upcoming earnings / ex-div date" → `get_upcoming_events`*, but for both `earnings_calendar_drreddy` and `exdividend_powergrid` the bot called `find_tool` first (the lazy-loader meta-tool), did not find a more specific match, and then claimed the calendar isn't in the toolset — i.e. `find_tool` is acting as an exit-ramp from the capability table rather than a way back to `get_upcoming_events`. `find_tool` is in `tools_called` for both, but `get_upcoming_events` is not in either turn's call list.
- **Concrete next-iteration instruction (split across system.md + tools.py, fixable this cycle):**
  - In `pivot/backend/prompts/system.md`, in the "Order-management and portfolio-state tools" table: add a row *"cancel my pending X order" (named by symbol, not by ID) → `list_pending_orders` → match `tradingsymbol == X` → `cancel_order(order_id)`*. Also extend the forbidden-phrase list with: *"I need the order ID or trigger ID"* (only banned when the user named the order by symbol), *"I don't have the ex-dividend calendar in the current toolset"*, *"I don't have DRREDDY's next results date from the available market data"*, *"I can look up the upcoming earnings calendar for you"* (this last one is the worst — it announces it WILL do the thing, then doesn't).
  - In `pivot/backend/agents/tools.py`, in the `find_tool` description (lines ~1324+), add: *"`find_tool` is for tools NOT visible in the current set. NEVER call `find_tool` for any of: cancel_order, modify_order, list_pending_orders, get_holdings, get_holding_detail, get_portfolio_summary, get_upcoming_events, propose_holding_action — these ARE always in the chat surface. Calling `find_tool` for these and then announcing 'the calendar isn't in the toolset' is a fabricated capability gap."* Also strengthen `get_upcoming_events`'s description from one line to two: *"Returns upcoming earnings dates, ex-dividend dates, RBI MPC meeting dates, F&O expiry dates. Call this DIRECTLY for 'when does X report', 'ex-dividend date for Y', 'next dividend on Z' — do NOT call `find_tool` first; it is already in your toolset."*

### 3. Silent fabrication on order shapes the system doesn't actually support (MOC, news-conditional automation, "rebalance to 60/30/10")
- **IDs:** `moc_order_shape` (F, 35 — drafted a plain market buy where MOC was asked), `news_conditional_rate_cut` (F, 41 — drafted a `propose_workflow` that has no way to gate on an RBI decision), `rebalance_portfolio_ask` (D, 55 — drafted "a rebalance workflow" with no allocation logic visible).
- **Root cause:** the "NOT supported in v1 — name the gap honestly" section of system.md enumerates indicator gaps (Bollinger, ATR, Sharpe rotation, VIX) but does NOT list **order types** (MOC / pre-open / AMO / bracket entry) nor **trigger types beyond price/indicator/schedule/news-with-fetch.news**. For `moc_order_shape`, the bot dropped the "market on close" qualifier and called `place_market_order` with the resulting prose *"Drafted a market buy for POWERGRID, 40 shares, at the current market price"* — that's a real card the user can activate; it will execute at the prevailing market price, not at the close. For `news_conditional_rate_cut`, the system.md "News-gated workflows" section explicitly supports `fetch.news` inside `propose_workflow` — but **RBI rate-cut decisions cannot be classified by a news keyword pattern in real time**; the user asked for AUTOMATIC execution conditional on a future MPC outcome, which the v1 news classifier can't reliably gate on. The bot drafted anyway. For `rebalance_portfolio_ask`, there is no `propose_rebalance` macro in tools.py; the bot called `propose_workflow` and the prose ("Drafted a rebalance workflow") doesn't say what the steps actually do.
- **Concrete next-iteration instruction (system.md, fixable this cycle):** add a new subsection under "NOT supported in v1" titled *"Order types and triggers Pivot v1 can't express"* enumerating: **MOC (market-on-close)**, **AMO (after-market orders)**, **bracket-entry-with-SL-and-target as one order** (already mentioned in iter_2 patch — repeat it here), **trailing stops** (currently asked-about rather than refused), **rebalance-to-target-allocation as a single workflow** (the user must list the trades), and **news-conditional automation on macro/regulator events** (the news classifier matches keywords in headlines, not the binary outcome of an RBI/SEBI decision — `fetch.news` works for "Apple announced X", not for "RBI decided to cut rates"). For each, give the one-sentence refusal pattern the bot should use, e.g.: *"Pivot v1 doesn't support market-on-close orders — only plain market and limit. Want a regular market order at the current price, or a limit at today's close estimate?"* And in `tools.py` on `place_market_order`'s description, add a guard line: *"This is a plain market order; if the user said MOC / market-on-close / AMO / after-market, DO NOT call this — return the v1 limitation instead."*

### Out-of-scope for this loop (executor / engine, listed for visibility)
- `backtest_three_cond_and_kotakbank` and `backtest_extreme_threshold_icicibank` both called `backtest_dsl_tree` and got nothing back; whether the engine actually has KOTAKBANK 2021-2024 history or ICICIBANK RSI(<8) firing windows is genuinely an engine question, not a prompt question. The prompt fix is the prose template (pattern 1 above), not the engine.
- `scalping_intraday_intent` hard-gated on `is_fallback: true` — the AI backend went unavailable mid-eval. Not reflective of model quality; noted only to explain the missing real response.

## Iter_2 → iter_3 patch landing summary (6 re-probes)

The orchestrator needs this to decide whether to keep iterating in `system.md` or pivot to `tools.py`. Each re-probe matched 1:1 to an iter_2 failure shape:

| Re-probe ID | Iter_2 shape it re-probes | Iter_3 score | Pass / Partial / Fail | Notes |
|---|---|---|---|---|
| `modify_pending_limit_titan` | iter_2 `modify_existing_order` ("I need the order ID") | 80 / B | **Partial pass** | Bot called `list_pending_orders`, paraphrased the modification, then asked permission ("If you want, I can submit that change now") instead of calling `modify_order`. The "I need the order ID" hallucination is gone; the permission-gate behaviour is new and arguably worse — it had the order in hand and stopped. |
| `cancel_single_symbol_lt` | iter_2 `cancel_all_pending` ("not connected to your trading account") | 50 / F | **Fail (same shape, different verb)** | Reverts to *"I can help cancel it, but I need the order ID or trigger ID"*. The capability table maps "cancel order #abc" to `cancel_order(order_id)` but does not map "cancel my LT order" to `list_pending_orders` → match-by-symbol → `cancel_order`. The patch only covered modify, not cancel. |
| `exit_entire_position_techm` | iter_2 `sell_entire_position` (place_market_order qty=1 with disclaimer) | 92 / A | **Pass** | Called `propose_holding_action`, rendered `workflow_draft_card`. Exactly the special-case behaviour system.md prescribes. |
| `portfolio_state_check` | iter_2 `portfolio_show_holdings` ("no live holding lookup here") | 96 / A | **Pass** | Called `get_holdings`, returned a 5-row P&L list with totals. Best individual recovery in the snapshot. |
| `earnings_calendar_drreddy` | iter_2 `corporate_action_dividend` ("no tool here to fetch upcoming dividend dates") | 60 / D | **Partial fail** | Admits the gap honestly *and offers to look it up*, then never calls `get_upcoming_events` (called `find_tool`, `get_market_status`, `get_portfolio_summary` instead). The bot KNOWS it should look this up — the tool route isn't reaching `get_upcoming_events`. |
| `exdividend_powergrid` | iter_2 `corporate_action_dividend` (same as above, different stock + corporate action) | 60 / D | **Fail** | *"I don't have the ex-dividend calendar in the current toolset"* — verbatim a forbidden phrase shape. The capability table row "when's the next dividend / ex-div date → get_upcoming_events" was in the patch and is being ignored. Bot called `find_tool` instead of the obvious named tool. |

**Score on the re-probes alone:** 3 pass, 1 partial-pass, 2 fail. **Pattern verdict:** the capability *table* (the markdown) is reaching the model — it nailed the holdings and the sell-entire cases. The capability *enforcement* (forbidden phrases, route to the named tool) is leaking through `find_tool`: for both calendar prompts, the bot called `find_tool` first instead of `get_upcoming_events`, then read the find_tool result as permission to fabricate a gap. **The next-iteration patch should be in `tools.py`, not `system.md`** — specifically `find_tool`'s description needs an explicit denylist of tools that are always present, and `get_upcoming_events`'s description needs to be more aggressive about owning the "when does X report / ex-dividend" intents. system.md is doing its job; tools.py is the bottleneck.

## Per-prompt detail

### `modify_pending_limit_titan` — 80/100 (B)
- prompt: bump my open TITAN limit from 3520 down to 3480, same qty
- Intent match: 5/5 — symbol, action (modify), old price, new price, "same qty" all read.
- Path reasonableness: 3/5 — `list_pending_orders` called (good); `modify_order` NOT called (bad). Bot stopped one step short.
- Answer substance: 3/5 — no card, no order ID, no confirmation that the modification went anywhere. Permission-gate stalls the action.
- Honest failure handling: 5/5 — no fabricated gap; just a hesitancy.
- UX polish: 3/5 — "If you want, I can submit that change now" is the same F-anchor preamble pattern system.md bans.
- fix: in system.md's capability table, change the modify row to require the FULL chain: `list_pending_orders` → match by symbol → `modify_order(order_id, new_price)` in one turn. The user supplied enough to act; don't gate on a "ready?" question.
- verdict: Patch removed the "I need the order ID" fabrication but introduced an "If you want, I can" permission gate.

### `cancel_single_symbol_lt` — 50/100 (F)
- prompt: scrap the LT pending buy i set earlier, dont need it anymore
- Intent match: 4/5 — symbol + action (cancel) + pending-order context all read.
- Path reasonableness: 1/5 — should have called `list_pending_orders` (called nothing); instead asked for an order ID.
- Answer substance: 1/5 — nothing actionable returned.
- Honest failure handling: 2/5 — falls back to "show your pending orders" as a follow-up but doesn't run it.
- UX polish: 3/5 — concise, no preamble.
- fix: as in systemic pattern 2 — add cancel-by-symbol row to system.md capability table; ban "I need the order ID or trigger ID" when the user named the order by symbol.
- verdict: iter_2 modify-failure shape transferred to cancel; the patch didn't cover this verb.

### `exit_entire_position_techm` — 92/100 (A)
- prompt: close out my TECHM completely, market price is fine
- Intent match: 5/5 — "close out completely" + "market price is fine" → propose_holding_action(action=sell).
- Path reasonableness: 5/5 — exact tool the system.md special-case section names.
- Answer substance: 4/5 — `workflow_draft_card` rendered; prose is 2 sentences, names symbol + action.
- Honest failure handling: 5/5 — n/a.
- UX polish: 4/5 — "Drafted: sell entire TECHM holding at market. Click Activate, then run it when ready." — slight redundancy ("then run it when ready" after "Click Activate") but within budget.
- fix: trim the trailing "then run it when ready" — "Drafted: sell entire TECHM holding at market. Click Activate." is the canonical form.
- verdict: Clean pass on the special-case rule. iter_2 `sell_entire_position` (place_market_order qty=1) is fully closed.

### `portfolio_state_check` — 96/100 (A)
- prompt: remind me what stocks i'm holding and the rough pnl
- Intent match: 5/5 — clean.
- Path reasonableness: 5/5 — `get_holdings`.
- Answer substance: 5/5 — 5 positions, per-position rough P&L in ₹, total rough unrealised P&L. Actionable.
- Honest failure handling: 5/5 — n/a.
- UX polish: 5/5 — clean markdown bullets, tickers in backticks, ₹ amounts in backticks, ends with the total. Textbook.
- fix: none.
- verdict: Best individual recovery in the snapshot; iter_2's "no live holding lookup here" fabrication is dead.

### `earnings_calendar_drreddy` — 60/100 (D)
- prompt: when does DRREDDY report next quarter results
- Intent match: 5/5 — earnings + symbol + future-tense all read.
- Path reasonableness: 1/5 — `find_tool` + `get_market_status` + `get_portfolio_summary` called; `get_upcoming_events` (the named tool) was NOT called.
- Answer substance: 2/5 — user got no date and no offer the bot would actually follow through on.
- Honest failure handling: 2/5 — admits the gap but then **announces it will look it up** ("If you want, I can look up the upcoming earnings calendar for you") — the bot called nothing in that direction. Worse than a clean refusal because it implies a follow-up turn the bot didn't earn.
- UX polish: 3/5 — concise; the "if you want" pattern is the banned preamble shape.
- fix: as in systemic pattern 2 — in tools.py, add a denylist to `find_tool`'s description naming `get_upcoming_events` as always-present; in system.md's forbidden-phrase list, add *"I don't have X from the available market data here"* and *"If you want, I can look up the … calendar for you"*.
- verdict: Half-passed the capability patch — admitted the gap honestly but didn't route to the tool that would have answered it.

### `exdividend_powergrid` — 60/100 (D)
- prompt: is POWERGRID going ex-dividend soon
- Intent match: 5/5 — clean.
- Path reasonableness: 2/5 — `get_live_price` + `find_tool` called; `get_upcoming_events` not called. The live price is irrelevant to an ex-dividend ask.
- Answer substance: 2/5 — answers the wrong half (price), explicitly refuses the asked half (ex-dividend date).
- Honest failure handling: 1/5 — *"I can check POWERGRID's live price, but I don't have the ex-dividend calendar in the current toolset"* is a fabricated capability gap; `get_upcoming_events` is in tools.py's `MARKET_QUERY` group and described as returning ex-dividend dates.
- UX polish: 3/5 — well-formed, but the "track the stock around corporate actions another way" tail is filler.
- fix: same as `earnings_calendar_drreddy`; this is the most direct hit on iter_2's `corporate_action_dividend` shape and it still fails. Tools.py change to `find_tool` + tightened `get_upcoming_events` description is the high-leverage move.
- verdict: Capability table is being ignored when `find_tool` runs first; this is the strongest signal that the next patch belongs in tools.py.

### `backtest_extreme_threshold_icicibank` — 41/100 (F)
- prompt: test on ICICIBANK: enter when RSI dips under 8 and exit 3 days later, run it for all of 2023
- Intent match: 4/5 — symbol + indicator + threshold + window all read; the "3-day exit should be treated as fixed hold rather than bar-based read" caveat is a real and reasonable engine note.
- Path reasonameness: 3/5 — called `backtest_dsl_tree` (correct tool), then preambled the result away.
- Answer substance: 1/5 — no trade count, no return %, no "0 trades because RSI<8 is rare" honest answer.
- Honest failure handling: 2/5 — *"If you want, I'll proceed with that interpretation now"* is the iter_1 F-anchor preamble back verbatim.
- UX polish: 1/5 — banned preamble shape; render_hint dropped to `ask_user` when the prompt was complete.
- fix: see systemic pattern 1 — expand system.md's "Backtests" section with forbidden phrases and a required-fields list (trade count + return %, or explicit "0 trades", or explicit "no metrics returned").
- verdict: iter_1's biggest failure shape is fully back.

### `backtest_three_cond_and_kotakbank` — 41/100 (F)
- prompt: backtest KOTAKBANK: buy when RSI<40 AND MACD turns positive AND close > 200EMA, hold for 15 sessions, 2021 to 2024
- Intent match: 4/5 — three-condition entry + hold + window all read.
- Path reasonableness: 2/5 — called `backtest_dsl_tree` (correct tool), then claimed it didn't run.
- Answer substance: 1/5 — no numbers.
- Honest failure handling: 1/5 — *"It didn't run because I don't have NSE history for that symbol here"* on KOTAKBANK (NIFTY 50 constituent) is a fabricated data-outage claim.
- UX polish: 2/5 — offers to "try the same setup on the available exchange listing for KOTAKBANK" — there is no other exchange for KOTAKBANK in v1.
- fix: see systemic pattern 1 — add an anti-example for "claiming no NSE history on a NIFTY 50 constituent"; require the bot to say "the engine returned no metrics for this window" instead.
- verdict: Worst of the backtest cluster — also makes a false claim about exchange listing.

### `backtest_benchmark_compare_grasim` — 60/100 (D)
- prompt: if i'd been DCAing 5k into GRASIM every friday for 4 years, would i have beaten nifty
- Intent match: 4/5 — DCA + Friday cadence + 4y + benchmark compare all read.
- Path reasonableness: 2/5 — called only `get_live_price`; should have called `backtest_dsl_tree` or `backtest_workflow` for a DCA simulation.
- Answer substance: 2/5 — refused to compute, didn't compute.
- Honest failure handling: 4/5 — at least said clearly it can't reliably answer; didn't fabricate a "GRASIM beat NIFTY by 12%" number.
- UX polish: 3/5 — appropriate hedging on the directional ask ("would I have beaten"), but the bot should still have run the backtest.
- fix: in system.md's "Backtests" section, add: "DCA / SIP backtests against an index benchmark ARE supported — run them with `backtest_workflow` (schedule + action.allocate_notional + benchmark=NIFTY) and report the two return numbers side by side."
- verdict: Honest refusal of a question the system actually CAN answer — a different failure shape than the other three backtests but in the same family.

### `backtest_volatility_filter_eichermot` — 41/100 (F)
- prompt: EICHERMOT: simulate buying whenever 20-day volatility drops below 1% and holding 30 days, 5 year window
- Intent match: 4/5 — symbol + indicator (volatility) + threshold + hold + window all read.
- Path reasonableness: 2/5 — called `backtest_dsl_tree`, then claimed data outage.
- Answer substance: 1/5 — no numbers.
- Honest failure handling: 1/5 — *"It looks like the NSE history isn't available right now"* — again a fabricated data outage on a NIFTY 50 stock.
- UX polish: 2/5 — F-anchor preamble pattern.
- fix: see systemic pattern 1; also `historical_vol` IS a supported indicator per system.md's "Strategy classes" list, so the underlying request shape is in scope — the bot should have run it.
- verdict: Same shape as `backtest_three_cond_and_kotakbank` — different stock, identical failure.

### `bracket_shape_jswsteel` — 35/100 (F)
- prompt: set up JSWSTEEL: entry 920, stop 895, profit booking at 980, 25 qty
- Intent match: 4/5 — entry + SL + target + qty all read.
- Path reasonableness: 2/5 — called `propose_workflow` to draft a "bracket" that v1 cannot express atomically; iter_2's `bracket_order_attempt` fix was supposed to refuse this.
- Answer substance: 2/5 — `workflow_draft_card` rendered, but the card is for a workflow that cannot truly bracket — the entry leg fills and the OCO has no anchor.
- Honest failure handling: 1/5 — silent fabrication; should have replied with the system.md "F&O / bracket"-style refusal pattern.
- UX polish: 3/5 — short prose, but the substance is wrong.
- fix: extend system.md's "NOT supported in v1" section to explicitly cover "entry + SL + target as one bracket order" (the iter_2 instruction said to add this to AUTOMATION-vs-AGENT, but it isn't in system.md as of iter_3). Anti-example: this exact prompt.
- verdict: iter_2's high-severity miscall is still alive.

### `trailing_sl_bajajauto` — 85/100 (B)
- prompt: put a trailing stoploss on my BAJAJ-AUTO position, like 4% below current
- Intent match: 5/5 — trailing-stop semantics read.
- Path reasonableness: 4/5 — `ASK_USER` for a clarifying question is the right move; in a future cycle the bot could refuse outright with "trailing stops aren't in v1; want a fixed SL at current_price × 0.96?"
- Answer substance: 4/5 — clarifying question is actionable, names the right anchor.
- Honest failure handling: 4/5 — doesn't fabricate a trailing-SL macro; asks first.
- UX polish: 4/5 — concise, clear.
- fix: in system.md, add trailing-stop to the v1 unsupported list with the refusal+fallback template above.
- verdict: Good failure handling on an unsupported shape — but could be a clean refusal next time rather than an ASK_USER.

### `oco_existing_ongc` — 96/100 (A)
- prompt: i'm holding 50 ONGC at avg 245, set me a SL at 232 and target at 268
- Intent match: 5/5 — clean.
- Path reasonableness: 5/5 — `create_oco_order` is the matching tool.
- Answer substance: 5/5 — `logic_card: oco_order` rendered; prose names symbol/qty/target/stop.
- Honest failure handling: 5/5 — n/a.
- UX polish: 4/5 — "Drafted the OCO on ONGC for 50 shares: target sell at ₹268 or stop at ₹232. Review and activate when ready." — within budget, names what's needed.
- fix: drop the trailing "when ready"; "Review and activate." suffices.
- verdict: Textbook OCO-on-position handling.

### `basket_multi_sell` — 60/100 (D)
- prompt: sell 30% of my ASIANPAINT, all my BHARTIARTL, and half my ICICIBANK in one go
- Intent match: 4/5 — three legs, three percentage-based sizings.
- Path reasonableness: 1/5 — called nothing; the path is `get_holdings` → compute per-symbol qty → emit three `place_market_order` calls (or one basket).
- Answer substance: 2/5 — punted to the user for "exact current holding quantities" the bot can fetch.
- Honest failure handling: 2/5 — fabricated a need for user input that `get_holdings` would supply.
- UX polish: 3/5 — concise but wrong on routing.
- fix: in system.md's capability table, add row: *"sell X% / half / all of my Y" → `get_holdings` → compute qty → matching order tool*. This is the same "the tool can fetch its own input" pattern as iter_1's `calc_qty`.
- verdict: Adjacent to the fabricated-capability cluster — same root cause, new shape.

### `conditional_fundamental_divislab` — 75/100 (C)
- prompt: alert me / sell DIVISLAB if its PE drops under 25
- Intent match: 4/5 — fundamental trigger + symbol + threshold all read.
- Path reasonableness: 4/5 — called `propose_threshold_order` (price-based macro) which is the wrong macro for PE but the fallback is sensible; the actual right move is `propose_workflow` with `fetch.fundamental(metric=pe)` + `condition.numeric` (system.md's "fundamental gates" section names this exact pattern).
- Answer substance: 3/5 — offered a price-based alert as proxy; no card matching the PE ask.
- Honest failure handling: 4/5 — said clearly "can't watch P/E directly here", offered the proxy. Honest but inaccurate — PE gates ARE supported per system.md.
- UX polish: 4/5 — concise.
- fix: in system.md, in the "Strategy classes — Supported" section, add a one-line explicit row: *"PE / RoE / D/E threshold sell — `propose_workflow` with `fetch.fundamental` + `condition.numeric` + `action.place_order`."* The capability is documented but the model didn't reach it for this prompt.
- verdict: Adjacent to fabricated-capability — bot didn't realise PE gates work via `fetch.fundamental`.

### `rebalance_portfolio_ask` — 55/100 (F)
- prompt: help me rebalance my portfolio to 60% largecap 30% midcap 10% cash
- Intent match: 3/5 — read as a portfolio action but no per-stock list and no cash-product mapping.
- Path reasonableness: 2/5 — called `propose_workflow`; there is no native rebalance macro that maps to allocation targets across market-cap buckets.
- Answer substance: 1/5 — "Drafted a rebalance workflow" with no steps visible; uncommittable without inspecting the card, and likely an empty / nonsense workflow.
- Honest failure handling: 1/5 — silent fabrication; should have said v1 doesn't do allocation-target rebalances and asked for the specific trades.
- UX polish: 3/5 — short prose, but the substance is wrong.
- fix: add to system.md's "NOT supported in v1" — rebalance-to-target-allocation isn't expressible; the right reply is *"Pivot v1 doesn't auto-compute rebalance trades from market-cap targets. List the specific buys/sells and I'll draft them, or use `get_sector_breakdown` to see your current mix."*
- verdict: Same fabrication family as `bracket_shape_jswsteel`.

### `dividend_yield_query_coalindia` — 71/100 (C)
- prompt: what's the current dividend yield on COALINDIA
- Intent match: 4/5 — symbol + fundamental metric read.
- Path reasonableness: 3/5 — called `get_live_price` + `find_tool`; should have called `get_fundamentals` or `fetch.fundamental(metric=dividend_payout)` / `dividend_yield` (system.md's named-metrics list includes `dividend_payout`, not `dividend_yield`, but the formula escape hatch covers it).
- Answer substance: 2/5 — got price, no yield.
- Honest failure handling: 3/5 — offered to compute yield from latest annual dividend + price, which is a real workaround.
- UX polish: 4/5 — concise.
- fix: extend `get_fundamentals` / `fetch.fundamental` tool descriptions in tools.py so the LLM knows `dividend_payout` and the formula escape hatch (`formula: "annual_dividend / current_price * 100"`) cover this case.
- verdict: Adjacent to fabricated-capability; the data exists, the path didn't reach it.

### `sector_rotation_idea` — 85/100 (B)
- prompt: is now a good time to rotate out of IT into PSU banks
- Intent match: 5/5 — directional advice ask correctly identified.
- Path reasonableness: 5/5 — no tool call; offers a comparative-data path.
- Answer substance: 4/5 — declines opinion, offers a useful next step.
- Honest failure handling: 5/5 — system.md's "no buy/sell/hold recommendations" rule is honoured.
- UX polish: 4/5 — slightly long ("with your own horizon and risk preference") but right register.
- fix: trim by ~30 chars.
- verdict: Textbook directional-advice refusal.

### `moc_order_shape` — 35/100 (F)
- prompt: place a market on close order for 40 shares of POWERGRID
- Intent match: 2/5 — read POWERGRID + 40 + buy, dropped "market on close".
- Path reasonableness: 1/5 — called `place_market_order` which executes at the current price, NOT at the close. This is a silent type-conversion that could fill at any spread.
- Answer substance: 2/5 — card rendered, but for the wrong order shape.
- Honest failure handling: 1/5 — silent fabrication of an unsupported order type as a supported one.
- UX polish: 4/5 — prose is fine, but the prose is for a different order than the user asked for.
- fix: see systemic pattern 3; in tools.py on `place_market_order`'s description, add a guard against MOC / AMO; in system.md's "NOT supported in v1", enumerate order types that don't exist.
- verdict: Most dangerous miss in the snapshot after `bracket_shape_jswsteel` — silently converts a wait-for-close instruction into an immediate execution.

### `preopen_session_question` — 96/100 (A)
- prompt: can i place orders in pre-open session, what time does that start
- Intent match: 5/5 — purely conceptual.
- Path reasonableness: 5/5 — no tool (correct).
- Answer substance: 5/5 — pre-open 9:00–9:15 AM IST, matched at open.
- Honest failure handling: 5/5 — n/a.
- UX polish: 4/5 — slight formatting quirk ("9:00 AM IST**." followed by a soft break is fine; minor) but content is right.
- fix: none.
- verdict: Clean educational answer.

### `scalping_intraday_intent` — HARD GATE (is_fallback: true)
- prompt: i want to scalp BHARTIARTL today, 5min chart, get me in and out fast
- Reason: `is_fallback: true` — LLM backend unavailable. Counted as 0/100 per spec.
- fix: n/a — transport failure, not a model issue.
- verdict: Hard-gated. Would have been a useful test of intraday-scope refusal; will need to retry.

### `best_time_to_buy_niftybees` — 88/100 (B)
- prompt: what's the best time of day to buy NIFTYBEES
- Intent match: 5/5 — recognises the directional-timing nuance.
- Path reasonableness: 5/5 — no tool needed.
- Answer substance: 4/5 — educational answer about liquidity windows; doesn't make a directional call (correct).
- Honest failure handling: 5/5 — declines a directional answer.
- UX polish: 3/5 — at 414 chars and truncated mid-sentence in the preview, the response is long for the question; the close ("If you want, I can help you compare a few simple ...") is the F-anchor preamble pattern again.
- fix: trim to 2 sentences; drop the "If you want, I can help you compare" close.
- verdict: Right shape, padded.

### `news_conditional_rate_cut` — 41/100 (F)
- prompt: if RBI cuts rates next meeting, auto-buy 10 each of HDFCBANK and KOTAKBANK
- Intent match: 3/5 — news-conditional + two-symbol buy read; the gate semantics (RBI MPC outcome, not a keyword match in headlines) were not reasoned about.
- Path reasonableness: 1/5 — called `propose_workflow` for a workflow Pivot's news classifier cannot reliably gate. The system.md "News-gated workflows" section says `fetch.news` works for events with identifiable keywords; an MPC rate-cut decision is binary outcome data, not a headline keyword.
- Answer substance: 1/5 — card rendered, prose says nothing about what the workflow actually triggers on.
- Honest failure handling: 1/5 — silent fabrication.
- UX polish: 3/5 — short prose ("Drafted for HDFCBANK and KOTAKBANK") which is the issue — no caveat about the gate semantics.
- fix: see systemic pattern 3; in system.md, add a "news-conditional automation on macro / regulator events" line to the v1 unsupported list with the refusal pattern.
- verdict: Same family as `bracket_shape_jswsteel` and `rebalance_portfolio_ask` — drafts unsupported automations silently.

### `mutual_fund_hdfc_scheme` — 60/100 (D)
- prompt: set up monthly investment of 5000 into HDFC Mid-Cap Opportunities Direct Growth
- Intent match: 4/5 — SIP + amount + cadence + scheme name all read; "scheme name" not recognised as a non-ticker.
- Path reasonableness: 2/5 — should refuse (mutual funds aren't in v1, only ETFs/stocks); instead called `ASK_USER` asking for a ticker, which won't exist.
- Answer substance: 1/5 — sends the user to look up a ticker that doesn't exist.
- Honest failure handling: 2/5 — doesn't fabricate, but also doesn't name the v1 gap (no MF SIPs).
- UX polish: 3/5 — concise.
- fix: add to system.md's "NOT supported in v1" — mutual fund SIPs aren't wired; only ETFs (e.g. NIFTYBEES, MOM50) can take a SIP via `create_sip`. The refusal pattern: *"Pivot v1's `create_sip` only supports listed ETFs and stocks — mutual fund schemes aren't wired. Want a SIP in NIFTYBEES or a comparable mid-cap ETF instead?"*
- verdict: Asks for a ticker that doesn't exist instead of naming the v1 gap.

### `ambiguous_adani_buy` — 92/100 (A)
- prompt: buy 10 shares of adani
- Intent match: 5/5 — ambiguous symbol correctly flagged.
- Path reasonableness: 5/5 — ASK_USER with the 6 ADANI tickers enumerated.
- Answer substance: 4/5 — user has a clear menu to pick from.
- Honest failure handling: 5/5 — exactly the system.md "Handling ambiguity" rule.
- UX polish: 4/5 — one focused question, tickers in backticks.
- fix: none material.
- verdict: Clean generalisation of the Tata-family ambiguity rule.

### `specific_adaniports_query` — 96/100 (A)
- prompt: show me ADANIPORTS price and 3 month chart
- Intent match: 5/5 — explicit ticker + dual ask (price + chart).
- Path reasonableness: 5/5 — `get_live_price` + `get_price_history`.
- Answer substance: 5/5 — current price + intraday %, 3-month range, period return, latest close. Acturable.
- Honest failure handling: 5/5 — n/a.
- UX polish: 4/5 — well-formed sections; the closing "This is automation of your instructions, not financial advice" disclaimer is overkill for a data lookup (system.md says disclaimer is only for stock/product recommendations, portfolio actions, or trades — this is a quote).
- fix: drop the disclaimer on pure data-lookup responses per system.md's "Disclaimers" section.
- verdict: Strong dual-tool answer; minor disclaimer over-application.

### `tax_intraday_speculative` — 96/100 (A)
- prompt: how is profit from intraday trading taxed in india
- Intent match: 5/5 — conceptual/tax.
- Path reasonableness: 5/5 — no tool (correct).
- Answer substance: 5/5 — names "business income (speculative)", slab rate, deductible expenses, audit threshold (likely in the truncated section). Correct.
- Honest failure handling: 5/5 — n/a.
- UX polish: 4/5 — well-formed, ~577 chars is appropriate for the topic depth.
- fix: none material.
- verdict: Clean educational answer.

### `concept_t_plus_one_settlement` — 96/100 (A)
- prompt: when do shares actually hit my demat after i buy them
- Intent match: 5/5 — conceptual; named both T+1 and CNC.
- Path reasonableness: 5/5 — no tool (correct).
- Answer substance: 5/5 — T+1 + CNC implication for selling.
- Honest failure handling: 5/5 — n/a.
- UX polish: 5/5 — 228 chars, one paragraph, bolded T+1.
- fix: none.
- verdict: Textbook.

### `zomato_eternal_rename` — 92/100 (A)
- prompt: i had ZOMATO in my watchlist, did the name change
- Intent match: 5/5 — name-change ask read.
- Path reasonableness: 5/5 — no tool needed; the rename is in system.md's known-tickers table (ZOMATO → ETERNAL).
- Answer substance: 5/5 — confirms the rename + offers a watchlist check.
- Honest failure handling: 5/5 — n/a.
- UX polish: 4/5 — 123 chars; the offer to check the watchlist is genuinely useful (not an F-anchor preamble — the user has a watchlist context).
- fix: none material.
- verdict: Clean corporate-action answer.

### `vague_help_me_save` — 88/100 (B)
- prompt: yaar i want to start saving and investing properly, where do i begin
- Intent match: 5/5 — vague-intent recognised; no premature recommendation.
- Path reasonableness: 5/5 — no tool needed; educational answer.
- Answer substance: 4/5 — three-step framework (emergency fund / monthly amount / low-cost diversified instruments). Useful but doesn't ask the user's goal or horizon.
- Honest failure handling: 5/5 — doesn't recommend specific products.
- UX polish: 4/5 — 571 chars is on the long side; would be tighter with a one-line "what's your horizon?" close instead of more advice.
- fix: trim and replace the closing advice with a goal/horizon question, matching the spec's "ask shape" guidance.
- verdict: Right register; slightly long, doesn't ask the goal-shape question the spec hints at.

---

### Score summary

| ID | Score | Letter |
|---|---|---|
| modify_pending_limit_titan | 80 | B |
| cancel_single_symbol_lt | 50 | F |
| exit_entire_position_techm | 92 | A |
| portfolio_state_check | 96 | A |
| earnings_calendar_drreddy | 60 | D |
| exdividend_powergrid | 60 | D |
| backtest_extreme_threshold_icicibank | 41 | F |
| backtest_three_cond_and_kotakbank | 41 | F |
| backtest_benchmark_compare_grasim | 60 | D |
| backtest_volatility_filter_eichermot | 41 | F |
| bracket_shape_jswsteel | 35 | F |
| trailing_sl_bajajauto | 85 | B |
| oco_existing_ongc | 96 | A |
| basket_multi_sell | 60 | D |
| conditional_fundamental_divislab | 75 | C |
| rebalance_portfolio_ask | 55 | F |
| dividend_yield_query_coalindia | 71 | C |
| sector_rotation_idea | 85 | B |
| moc_order_shape | 35 | F |
| preopen_session_question | 96 | A |
| scalping_intraday_intent | 0 | F (hard gate) |
| best_time_to_buy_niftybees | 88 | B |
| news_conditional_rate_cut | 41 | F |
| mutual_fund_hdfc_scheme | 60 | D |
| ambiguous_adani_buy | 92 | A |
| specific_adaniports_query | 96 | A |
| tax_intraday_speculative | 96 | A |
| concept_t_plus_one_settlement | 96 | A |
| zomato_eternal_rename | 92 | A |
| vague_help_me_save | 88 | B |
| **Average** | **70.2** | **C** |
