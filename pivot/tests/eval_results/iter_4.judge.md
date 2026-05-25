# Judge report — `iter_4`

## Headline
- Overall: **63/100 → D**
- 30 prompts scored
- 1 hard-gated: `cancel_by_symbol_hindalco` (is_fallback=true)
- Verdict: the iter_3 → iter_4 backtest-prose patch landed cleanly on only **1 of 6** re-probes — the system.md forbidden-phrase ban is being routed around with structurally-equivalent paraphrases ("data for NSE wasn't available just now", "I don't have historical NSE data for that symbol here"), the first-turn EMIT rule is being ignored, and the deferred find_tool / unsupported-order-type clusters remain unfixed; non-backtest territory (concepts, ambiguity, refusals) holds up well.

## What's working
- **Conceptual / educational asks are clean and prompt-tool-free**: `concept_gtt_explanation`, `concept_lower_circuit_stuck`, `thanks_with_minor_question` give tight definitions in 2-3 sentences with no upsell.
- **F&O / out-of-scope refusals are honest and use Pivot-v1 language**: `edge_intraday_options_nykaa` quotes the canonical script almost verbatim; `edge_smallcase_query` and `edge_etf_sgb` name the gap and offer the nearest supported alternative without inventing primitives.
- **Ambiguity-triage ASK_USER is on-pattern**: `ambiguous_mahindra_buy`, `ambiguous_units_paytm`, `filler_followup_no_context` ask one focused question each with the right framing.
- **Advice-leaning prompts decline directionally without losing utility**: `entry_point_advice_vedl`, `afterhours_news_reaction_dmart` correctly refuse opinion and offer the data path the user can act on.
- **One backtest re-probe DID land the patch**: `backtest_easy_ma_cross_cipla` actually fired `backtest_dsl_tree`, returned an `indicator_backtest_chart`, and the prose names the strategy + chart card — the only iter_4 backtest with substance. This is the existence proof the prose mandate CAN work when the tool returns a real result.

## What's broken — systemic patterns

### 1. Backtest prose mandate is being routed around with paraphrased excuses (5 of 6 re-probes)
- Prompt IDs: `backtest_tight_threshold_sbin`, `backtest_three_cond_and_heromoto`, `backtest_dca_vs_benchmark_bpcl`, `backtest_aggregator_breakout_apollohosp`, `backtest_event_window_havells`.
- Root cause: the system.md "forbidden backtest phrases" list (lines 614-619) bans exact strings but the LLM substitutes structurally-identical paraphrases. SBIN got *"the data for NSE wasn't available just now"* (banned exact form: "It looks like the NSE history isn't available right now"). HEROMOTOCO got *"I don't have historical NSE data for that symbol here"* (banned exact form: "I don't have NSE history for that symbol here" — one word changed). HAVELLS hit the exact-banned *"I can run that as-is; if you want, I'll proceed…"* — meaning the list isn't even being enforced literally, let alone semantically. APOLLOHOSP silently routed to `propose_workflow` (workflow_draft_card) instead of `backtest_dsl_tree`, ducking the prose rule entirely. The first-turn EMIT rule (lines 620-624) is being treated as advisory.
- Next-iteration instruction: in `pivot/backend/prompts/system.md` "Backtests" section, replace the literal-phrase blacklist with a structural rule and a contrastive example. Add: *"After any backtest tool returns, you may not blame data availability for NIFTY 50 / NIFTY 100 / NIFTY 500 constituents — if the tool returned empty, say 'the engine returned no metrics for {symbol} in {window}', period. If the tool was never called, you violated the first-turn EMIT rule — call the tool, do not write prose first."* Also explicitly route `backtest_aggregator_breakout_apollohosp`-shape prompts ("test X breakout … exit after N sessions … last 3 years") to `backtest_dsl_tree`, not `propose_workflow`: add to the backtest-tool docstring in `tools.py` *"BACKTEST verbs ('test', 'backtest', 'simulate', 'run a … on') NEVER route to propose_workflow — that produces a draft card, not metrics."* For BPCL/DCA, the system.md DCA section already names benchmark support but the model still asked for the start date — strengthen with *"'5 years' / 'last N years' is a complete window — never ask for the start month, default to today minus N years."*

### 2. find_tool exit-ramp bug is unfixed and now silently fabricates tool absence (4 of 5 re-probes)
- Prompt IDs: `cancel_by_symbol_bergepaint`, `earnings_next_quarter_indusindbk`, `exdividend_pidilite`, `results_date_mphasis`. (`cancel_by_symbol_hindalco` hard-gated on fallback so not counted.)
- Root cause: when `find_tool` returns nothing useful, the LLM responds with the exact phrases system.md lines 63-67 forbid ("I do not have a live earnings-calendar lookup in this chat", "I do not have the earnings calendar tool in this chat"). The model isn't reaching `get_upcoming_events` even though it's in the catalog, because the regex-based tool router isn't surfacing it for calendar-event phrasings ("when is X reporting next", "ex-dividend date for X", "next results date"), and find_tool's BM25 retrieval evidently doesn't rank it high enough either. BERGEPAINT got close — said no pending order found, suggested `list_pending_orders`/`cancel_order` — but did NOT actually call `list_pending_orders` to confirm, instead asking the user to confirm. Same root: the router didn't surface `list_pending_orders` for "drop the BERGEPAINT buy I queued yesterday".
- Next-iteration instruction: in `pivot/backend/agents/tools.py`, expand the `get_upcoming_events` description from *"Returns upcoming earnings, RBI meeting dates, ex-dividend dates, F&O expiry dates"* to explicitly list the trigger phrasings: *"Call directly for any 'when is X reporting', 'next results date for X', 'next earnings on X', 'ex-dividend date for X', 'next dividend on X', 'upcoming corporate action on X'. Do NOT call find_tool first — this tool handles all calendar-event lookups. If the result for the named symbol is empty, say 'no event on the {X} calendar I have' — do NOT say 'I don't have a calendar tool here', the tool exists and was just called."* Likewise expand `list_pending_orders` and `cancel_order` descriptions to name the symbol-anchored cancel phrasings ("drop the X buy I queued", "kill my pending X order", "cancel my X order") and explicitly state *"the user naming the symbol IS enough — do not ask for order_id, fetch it via list_pending_orders first."* Pair this with a one-line addition to `system.md`'s "Order-management tools" table mapping "drop / kill / cancel my pending X by symbol" → `list_pending_orders → cancel_order(order_id)`.

### 3. Silent fabrication / soft-routing on unsupported-order-types (3 of 3 re-probes)
- Prompt IDs: `amo_order_naukri`, `bracket_attempt_persistent`, `rebalance_sector_targets`.
- Root cause: NAUKRI AMO and PERSISTENT bracket should both be cleanly refused (neither is in v1's order surface — system.md only documents market/limit/GTT/SL/OCO/SIP). Instead both routed to ASK_USER asking for parameters AS IF the order types were supported, which is a quieter form of pretence — the user will answer and the next turn will either silently route to a regular order (NAUKRI) or fabricate a workflow that fakes the chain (PERSISTENT). For `rebalance_sector_targets`, the model did the right thing on the data side (called `get_sector_breakdown` + `get_holdings`, named the missing FMCG/pharma gap) but didn't add the v1-can't-auto-compute-rebalance disclaimer the expect line names.
- Next-iteration instruction: add a "Unsupported order types in v1 — refuse, don't preflight" section to `system.md` immediately after the F&O section, naming AMO/bracket/cover/iceberg explicitly with the canonical refusal: *"After-market orders, bracket orders, cover orders, and iceberg orders aren't in Pivot v1's order surface — only market, limit, GTT, SL, OCO, SIP execute. Want me to draft this as a regular market/limit order during market hours, or hold until the order type lands?"* For rebalance, add: *"Pivot v1 does NOT auto-compute rebalance trades from current holdings; surface the current breakdown via get_sector_breakdown, name the target deltas as percentages, and ask the user to confirm specific buy/sell quantities — do not silently fabricate trade list."* Tag the `tools.py` descriptions for `place_market_order` and `place_limit_order` with: *"If the user attached 'after-market'/'AMO'/'bracket'/'cover'/'iceberg' to the request, do NOT call this tool — refuse per system.md."*

## Iter_3 → iter_4 patch landing summary

The TOP fix this cycle was the expanded "Backtests" section in `pivot/backend/prompts/system.md` (trade-count-or-explicit-zero-or-explicit-no-metrics mandate + 5 forbidden phrases + first-turn EMIT rule + DCA-with-benchmark named supported). Result vs iter_3's 0/4 pass rate:

| ID | score | result | root cause if not pass |
|---|---|---|---|
| `backtest_tight_threshold_sbin` | 22/100 (F) | **fail** | Used paraphrased forbidden excuse ("data for NSE wasn't available just now"); never emitted required "0 trades / no metrics" phrasing; preambled with "Got it — I can run the SBIN backtest…" |
| `backtest_three_cond_and_heromoto` | 22/100 (F) | **fail** | Used near-exact-banned phrase ("I don't have historical NSE data for that symbol here") for a NIFTY 50 constituent; system.md explicitly calls this out and was ignored |
| `backtest_dca_vs_benchmark_bpcl` | 36/100 (F) | **fail** | DCA-with-benchmark IS named supported in system.md, but model still asked for explicit start date instead of defaulting "5 years" to today-minus-5y; called wrong tool (`get_live_price`); no benchmark comparison emitted |
| `backtest_aggregator_breakout_apollohosp` | 35/100 (F) | **fail** | Routed to `propose_workflow` → `workflow_draft_card` instead of `backtest_dsl_tree`. User asked to TEST, got a draft-to-activate card. Wrong tool entirely — sidesteps the prose mandate by never returning a backtest result to caption |
| `backtest_event_window_havells` | 30/100 (F) | **fail** | Hit the EXACT banned phrase "I can run that as-is; if you want, I'll proceed…" — the forbidden-phrase list isn't being enforced even literally |
| `backtest_easy_ma_cross_cipla` | 78/100 (C+) | **pass** | Actually called `backtest_dsl_tree`, returned `indicator_backtest_chart`, prose names strategy + chart. Loses points only for "## Backtest started" preamble + post-hoc follow-up offer; no inline trade count / return % even though chart is there |

**Pass rate: 1/6 (17%) vs iter_3 0/4 (0%).** Marginal lift, but the single pass is the *only* prompt where the parser cleanly extracted a vanilla shape and the engine returned data. The patch did NOT generalise to tight-threshold, multi-condition, DCA, event-window, or aggregator-breakout shapes — every other re-probe found a way to evade the rule with paraphrase, tool substitution, or "if you want I'll proceed" preamble. **Cycle 5 should focus on routing/structural enforcement, not adding more banned phrases.**

## Per-prompt detail

### `backtest_tight_threshold_sbin` — 22/100 (F)
- prompt: "backtest SBIN: enter only when RSI is under 12 and ADX above 30, hold 7 days, run for calendar 2023"
- Intent match: 3 — caught the strategy shape, dates, hold period
- Path reasonableness: 3 — `backtest_dsl_tree` is the right tool; it was called
- Answer substance: 0 — no trade count, no "0 trades", no "no metrics" — just a soft excuse
- Honest failure handling: 1 — hides the gap with paraphrased forbidden phrase
- UX polish: 1 — preambles ("Got it — I can run…") AND post-promises ("if you want, I can try the same setup again")
- fix: route tight-threshold-likely-zero-fires shapes to emit "0 trades — strategy never fired in {window}, threshold is too tight" rather than blame data
- verdict: tool was called but the prose threw away the result

### `backtest_three_cond_and_heromoto` — 22/100 (F)
- prompt: "for HEROMOTOCO try this combo: close above 100 EMA AND MACD positive AND volume above 20-day avg, hold 20 sessions, Jan 2020 through Dec 2024"
- Intent match: 4 — three-condition AND is parsed
- Path reasonableness: 3 — `backtest_dsl_tree` is correct
- Answer substance: 0 — zero numbers, blame data
- Honest failure handling: 0 — HEROMOTOCO is NIFTY 50, the "no NSE history" excuse is the exact pattern system.md explicitly forbids
- UX polish: 2 — short but ends in another permission offer
- fix: hard-code NIFTY 50 constituent list (or query yfinance metadata) into the no-history denial path so the model can't blame data for these symbols
- verdict: forbidden-phrase rule failed at the literal level here

### `backtest_dca_vs_benchmark_bpcl` — 36/100 (F)
- prompt: "I put 8000 into BPCL every first trading day of the month for 5 years. Did I do better than Nifty over the same stretch?"
- Intent match: 4 — DCA + benchmark comparison correctly understood
- Path reasonableness: 1 — called `get_live_price` (wrong tool); should have been `backtest_workflow` with benchmark_symbol=NIFTYBEES per system.md
- Answer substance: 1 — no numbers, just asks for start date
- Honest failure handling: 3 — at least asked clarifyingly, didn't fabricate
- UX polish: 2 — clean wording but the question itself is unnecessary
- fix: "5 years" with no start date is a complete window — default to today-minus-5y; never preflight DCA backtests by asking for start month
- verdict: the named-supported DCA shape was lost to over-clarification

### `backtest_aggregator_breakout_apollohosp` — 35/100 (F)
- prompt: "test a 20-day high breakout on APOLLOHOSP — buy on the day price crosses the prior 20-day high, exit after 10 sessions. window: last 3 years"
- Intent match: 4 — breakout + exit + window all caught
- Path reasonableness: 1 — routed to `propose_workflow` producing `workflow_draft_card`; user asked to TEST, got a draft-to-activate. This is the wrong tool entirely
- Answer substance: 1 — "Drafted APOLLOHOSP breakout workflow. Review the card and activate it when ready" gives the user no backtest result
- Honest failure handling: 3 — at least the card exists and is honest about what it is
- UX polish: 4 — short, no preamble
- fix: in `tools.py` `propose_workflow` description, add explicit anti-pattern: "TEST / BACKTEST / SIMULATE verbs do NOT go to this tool — route to `backtest_dsl_tree` (compound entries) or `backtest_workflow` (simple shapes)"
- verdict: silent tool substitution dodges the prose rule

### `backtest_event_window_havells` — 30/100 (F)
- prompt: "HAVELLS — backtest buying the day after each quarterly results and holding 5 sessions, from 2022-04-01 to 2024-12-31"
- Intent match: 4 — event window understood, dates parsed
- Path reasonableness: 3 — `backtest_dsl_tree` called (though the strategy is event-feed-dependent and probably untranslatable)
- Answer substance: 0 — no result, no honest gap-naming
- Honest failure handling: 1 — uses the EXACT banned phrase "I can run that as-is; if you want, I'll proceed"
- UX polish: 0 — exemplifies the F-anchor pattern from system.md's calibration section
- fix: when the strategy depends on a feed Pivot doesn't have (quarterly results dates), say so explicitly — "Pivot v1 doesn't have a programmatic results-date feed; I can backtest a fixed-date window or a price-shape proxy instead"
- verdict: prose-only refusal of a real ask, decorated with banned permission-gate

### `backtest_easy_ma_cross_cipla` — 78/100 (C+)
- prompt: "run a simple 50/200 SMA golden cross on CIPLA, buy on cross up, sell on cross down, last 6 years"
- Intent match: 5 — perfect read
- Path reasonableness: 5 — `find_tool` → `backtest_dsl_tree` → `indicator_backtest_chart`
- Answer substance: 3 — chart card returned but prose doesn't include trade count or return %; user has to read the chart
- Honest failure handling: 4 — no fabrication; clean
- UX polish: 3 — "## Backtest started" is an unnecessary heading; offers "I can also rerun with different entry/exit rules" which the user didn't ask for
- fix: when `backtest_dsl_tree` returns metrics, prose MUST include "X trades, Y% return, Z% win rate" inline — the chart-card-alone-is-the-answer pattern leaves prose unanchored
- verdict: the one backtest the patch landed cleanly on

### `cancel_by_symbol_hindalco` — 0/100 (F) [HARD GATE: is_fallback=true]
- prompt: "kill that HINDALCO pending order from this morning, changed my mind"
- All dimensions: 0
- fix: transport-level; not actionable in the 4-file allowlist
- verdict: hard-gated; LLM was offline for this turn

### `cancel_by_symbol_bergepaint` — 48/100 (F)
- prompt: "drop the BERGEPAINT buy i queued yesterday"
- Intent match: 4 — read as a cancel
- Path reasonableness: 2 — should have called `list_pending_orders` to confirm; called nothing
- Answer substance: 2 — names the right next steps but doesn't take them
- Honest failure handling: 3 — at least didn't fabricate an order ID demand
- UX polish: 3 — short, civil
- fix: system.md table already maps this case ("'cancel order' by symbol" → list_pending_orders → cancel_order). The model isn't reading the table — pull the rule up into the regex-based tool router so `list_pending_orders` is auto-surfaced for cancel-by-symbol asks
- verdict: one tool call away from being right; the router didn't surface the right tool

### `earnings_next_quarter_indusindbk` — 22/100 (F)
- prompt: "when is INDUSINDBK reporting numbers next"
- Intent match: 4 — earnings-calendar query understood
- Path reasonameness: 0 — no tool called; `get_upcoming_events` exists and was bypassed
- Answer substance: 1 — no answer, just deflection
- Honest failure handling: 1 — uses banned phrase pattern ("I don't have a live earnings-calendar lookup in this chat")
- UX polish: 3 — short
- fix: bake calendar-event phrasings ("when is X reporting", "next results", "next earnings on X") into `get_upcoming_events` description in `tools.py` so the router surfaces it; system.md already forbids the "I do not have a calendar tool" phrasing
- verdict: tool exists, was ignored

### `exdividend_pidilite` — 30/100 (F)
- prompt: "any upcoming ex-dividend date for PIDILITIND"
- Intent match: 3 — caught ex-div intent
- Path reasonableness: 1 — called `find_tool` then `get_portfolio_summary` (wildly wrong); never reached `get_upcoming_events`
- Answer substance: 1 — over-clarifies a single unambiguous ask
- Honest failure handling: 3 — didn't fabricate
- UX polish: 2 — the "do you want the upcoming ex-dividend date from market events, or would you like me to look up the dividend announcement/history" question is a false dichotomy — ex-dividend dates ARE the dividend history
- fix: same as above — surface `get_upcoming_events` for ex-div phrasings
- verdict: spurious clarification on an unambiguous lookup

### `results_date_mphasis` — 22/100 (F)
- prompt: "next results date for MPHASIS pls"
- Intent match: 4
- Path reasonableness: 1 — `find_tool` → `get_live_price` (irrelevant); didn't reach `get_upcoming_events`
- Answer substance: 1
- Honest failure handling: 1 — banned phrase "I do not have the earnings calendar tool in this chat"
- UX polish: 3
- fix: same root as the other three calendar prompts
- verdict: third instance of the same routing miss

### `amo_order_naukri` — 36/100 (F)
- prompt: "put in an after-market order to buy 15 NAUKRI at market when it opens tomorrow"
- Intent match: 2 — missed that AMO is unsupported in v1; treated as a routine recurring-vs-one-time question
- Path reasonableness: 2 — ASK_USER, but the wrong question (recurring vs one-time, not "AMO isn't in v1")
- Answer substance: 1 — user gets pushed toward a non-existent surface
- Honest failure handling: 1 — doesn't name the gap, soft-routes
- UX polish: 4 — short
- fix: per pattern #3 above — add the AMO/bracket/cover/iceberg refusal section to system.md
- verdict: silent fabrication via clarifying question

### `bracket_attempt_persistent` — 30/100 (F)
- prompt: "PERSISTENT: buy 12 at 5800, stoploss 5700, book profit at 6100, all as one bracket order"
- Intent match: 2 — read as needing a stock symbol (it doesn't — PERSISTENT IS the stock)
- Path reasonableness: 1 — ASK_USER for a symbol that was already given; misread "PERSISTENT" as a label, not the ticker
- Answer substance: 1 — user has to repeat themselves
- Honest failure handling: 1 — doesn't name that bracket orders aren't in v1
- UX polish: 3 — short
- fix: PERSISTENT is on the NSE Nifty 500 — add it to the inferable-ticker list. AND name bracket orders as unsupported per pattern #3
- verdict: two failures stacked — symbol misread AND bracket not refused

### `rebalance_sector_targets` — 65/100 (D)
- prompt: "rebalance me into 40% IT, 25% banks, 20% FMCG, 15% pharma — figure out the trades from my current holdings"
- Intent match: 4 — read the target weights
- Path reasonableness: 4 — `get_sector_breakdown` + `get_holdings` is right
- Answer substance: 4 — shows current split, names FMCG/pharma gap
- Honest failure handling: 3 — partially names gap but doesn't explicitly say "v1 can't auto-compute the trade list"
- UX polish: 3 — uses headings well; length is appropriate
- fix: add explicit "Pivot v1 can't auto-derive the buy/sell quantities — paste me the target ₹ per symbol or I can list options" sentence
- verdict: best of the unsupported-action re-probes; nearly hit the bar

### `drawdown_history_portfolio` — 55/100 (F)
- prompt: "show me my portfolio drawdown over the last 12 months, worst peak-to-trough"
- Intent match: 3 — understood, but pivoted to NIFTY instead of portfolio
- Path reasonableness: 3 — `get_performance_metrics` for index works as a fallback
- Answer substance: 3 — gives the NIFTY drawdown with dates; honest that portfolio isn't supported
- Honest failure handling: 4 — names the gap explicitly
- UX polish: 3 — clean
- fix: name the gap as a Pivot-v1 limitation ("Pivot doesn't yet compute portfolio-level drawdown") not as a personal disclaimer
- verdict: honest but partial; the NIFTY pivot is a reasonable consolation

### `realised_gain_fy_to_date` — 50/100 (F)
- prompt: "how much realised gain have i booked this financial year so far, short term vs long term split"
- Intent match: 4 — STCG/LTCG split for FY26 YTD understood
- Path reasonableness: 2 — ASK_USER, but `get_tax_summary` exists and could have been called
- Answer substance: 1 — no numbers, just a clarifying question
- Honest failure handling: 3 — didn't fabricate
- UX polish: 3 — clean but unnecessary clarification
- fix: `get_tax_summary` in `tools.py` should explicitly say "answers STCG/LTCG/realised-gain FY-YTD asks directly — no need to clarify scope"
- verdict: tool exists, was bypassed

### `portfolio_pnl_date_range` — 55/100 (F)
- prompt: "what's my portfolio return between 2025-10-01 and 2026-04-30"
- Intent match: 3 — recognised intent, defaulted to lifetime
- Path reasonableness: 3 — `get_portfolio_summary` is the lifetime path; the date-range path doesn't exist
- Answer substance: 3 — gave lifetime number and explicitly named the gap
- Honest failure handling: 4 — clean gap-naming
- UX polish: 3 — fine
- fix: this is structurally honest; an actual fix needs a new tool, not a prompt change. Note as v1 gap
- verdict: best handling of a true gap in iter_4

### `margin_question_intraday` — 35/100 (F)
- prompt: "what margin do i need to take a 50k intraday position on RELIANCE"
- Intent match: 3 — caught intraday margin question
- Path reasonableness: 1 — no tool called; `calculate_margin` exists for exactly this
- Answer substance: 2 — gave a generic "₹50k notional needs ₹50k margin" which is wrong for MIS (intraday gets leverage); silently fabricated a number
- Honest failure handling: 1 — invented an estimate without flagging
- UX polish: 3 — short
- fix: add "intraday margin / MIS margin / position margin" phrasings to `calculate_margin` description in `tools.py` so the router surfaces it
- verdict: silent fabrication of a margin number — concerning given the rule against this

### `afterhours_news_reaction_dmart` — 70/100 (C)
- prompt: "DMART had some news after close yesterday, how is it likely to open today"
- Intent match: 4 — caught both the news + directional ask
- Path reasonableness: 3 — `get_market_status` + `find_tool` is reasonable
- Answer substance: 3 — refuses directional, offers data
- Honest failure handling: 4 — clean refusal
- UX polish: 4 — short, on-pattern
- fix: should actually pull the news given the offer ("If you want, I can pull DMART's latest news and price context now") — the user implicitly said yes by asking
- verdict: solid refusal pattern

### `entry_point_advice_vedl` — 72/100 (C)
- prompt: "is VEDL a good entry right now or should i wait for a dip"
- Intent match: 5 — perfect read
- Path reasonableness: 4 — no tool needed for the refusal; could optionally fetch price
- Answer substance: 3 — refuses cleanly, offers data
- Honest failure handling: 5 — textbook non-directive
- UX polish: 4 — concise
- fix: could go one step further and fetch live price + 52w range in same turn (system.md allows this)
- verdict: on-pattern

### `ambiguous_mahindra_buy` — 80/100 (B)
- prompt: "grab 25 shares of mahindra"
- Intent match: 5 — caught ambiguity
- Path reasonableness: 5 — ASK_USER for the right thing
- Answer substance: 4 — focused single question
- Honest failure handling: 5 — n/a but no fabrication
- UX polish: 4 — one line, no preamble
- fix: list the top 3 Mahindra tickers explicitly (M&M, MAHINDRAFIN, TECHM) rather than "or another"
- verdict: clean

### `ambiguous_units_paytm` — 88/100 (B+)
- prompt: "add 500 of PAYTM"
- Intent match: 5
- Path reasonableness: 5
- Answer substance: 4 — focused
- Honest failure handling: 5
- UX polish: 5 — one line, perfect
- fix: none
- verdict: exemplary ambiguity triage

### `edge_smallcase_query` — 84/100 (B)
- prompt: "can i invest in a smallcase through pivot"
- Intent match: 5
- Path reasonableness: 5 — no tool needed
- Answer substance: 4 — names gap, offers two alternatives
- Honest failure handling: 5
- UX polish: 4 — clean
- fix: could be one sentence shorter
- verdict: clean refusal

### `edge_etf_sgb` — 80/100 (B)
- prompt: "i want to buy sovereign gold bonds in the next tranche"
- Intent match: 5
- Path reasonableness: 4
- Answer substance: 4
- Honest failure handling: 5
- UX polish: 3 — slightly long
- fix: trim to one sentence + the GOLDBEES alternative
- verdict: clean

### `edge_intraday_options_nykaa` — 90/100 (A-)
- prompt: "buy 1 lot NYKAA 180 PE for this expiry, intraday"
- Intent match: 5
- Path reasonableness: 5 — no tool needed
- Answer substance: 5 — uses canonical script almost verbatim
- Honest failure handling: 5
- UX polish: 4 — could note NYKAA also has no F&O
- fix: add NYKAA-doesn't-have-F&O note for the secondary gap
- verdict: textbook F&O refusal

### `concept_gtt_explanation` — 92/100 (A)
- prompt: "what's a GTT order, how is it different from a regular limit"
- Intent match: 5
- Path reasonableness: 5
- Answer substance: 5 — explained both, contrast clear
- Honest failure handling: 5
- UX polish: 4 — slightly verbose but appropriate for "explain X"
- fix: none
- verdict: exemplary educational answer

### `concept_lower_circuit_stuck` — 78/100 (C+)
- prompt: "if a stock i own hits lower circuit can i still sell it"
- Intent match: 5
- Path reasonableness: 5
- Answer substance: 4 — answers but light on the queue mechanics
- Honest failure handling: 5
- UX polish: 3 — the "if you want, I can explain how circuit limits affect delivery vs intraday" follow-up offer is the exact upsell system.md forbids on educational answers
- fix: drop the trailing offer; educational asks should not solicit follow-ups
- verdict: solid answer, unnecessary upsell

### `watchlist_add_irctc` — 50/100 (F)
- prompt: "add IRCTC to my watchlist"
- Intent match: 3 — caught watchlist intent
- Path reasonableness: 2 — routed to `propose_workflow` for what should be either a direct watchlist tool or an honest "watchlist not in v1" gap-name
- Answer substance: 2 — "Drafted — IRCTC added to your watchlist. Click Activate" is misleading; it's a workflow draft, not a watchlist entry
- Honest failure handling: 1 — pretends the watchlist surface exists by wrapping it in a workflow
- UX polish: 4 — short
- fix: add watchlist to the "NOT supported in v1" list in system.md; refuse with offer to set up price-alert workflow instead
- verdict: silent fabrication of a watchlist surface

### `thanks_with_minor_question` — 90/100 (A-)
- prompt: "thanks. also btw what does ex-bonus mean"
- Intent match: 5
- Path reasonableness: 5
- Answer substance: 5 — clear definition
- Honest failure handling: 5
- UX polish: 4 — slightly long but appropriate
- fix: none
- verdict: clean

### `filler_followup_no_context` — 85/100 (B)
- prompt: "haan kar de"
- Intent match: 5 — caught the no-context filler correctly
- Path reasonableness: 5 — ASK_USER
- Answer substance: 4 — focused question
- Honest failure handling: 5
- UX polish: 4 — short, multi-language handling clean
- fix: none
- verdict: on-pattern
