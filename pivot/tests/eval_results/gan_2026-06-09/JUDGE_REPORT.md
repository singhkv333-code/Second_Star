# GAN Discriminator Panel — Judge Report (Round 1)

**Snapshot:** `pivot/tests/eval_results/gan_undefined/run_20260609_155105.json`
**Date:** 2026-06-09 · **Turns:** 36 · **Sessions:** 28 · **Elapsed:** 276s
**Run stats:** p50 7.60s · p95 13.39s · in 2,020,184 tok · out 7,296 tok
**Categories:** F&O · ambiguous · edge-honesty · execution-stress · multi-turn · quality-stress · regression
**Verdicts:** both angles, every disputed verdict adversarially re-verified (`verified:true`).

Pivot is a chat-first investing copilot for Indian retail. Two quality bars (from `CLAUDE.md`):
- **Angle A — Execution correctness:** right intent → right tool → right widget/render_hint → faithful parse of intent into card params. Fails on wrong tool, empty/wrong widget, dropped condition/qty/symbol, fabricated number, a buildable thing that loops/refuses, or a mis-routed intent.
- **Angle B — Output quality:** given a correct widget+text, is the answer *good*? Convincing, data-rich (uses the real numbers), structured (sections + markdown **tables** for compares/screens), right length, defended view where warranted, widget earns its place. Correct-but-thin still fails B.

---

## 1. Scorecard

### Per-angle totals

| Angle | PASS | PARTIAL | FAIL | Mean score | n (verdicts) |
|-------|------|---------|------|-----------|--------------|
| **A — Execution correctness** | 16 | 5 | 7 | **6.04** | 28 |
| **B — Output quality** | 13 | 4 | 9 | **5.04** | 26 |

> Angle-A means/counts cover the 28 scored A-sessions; Angle-B covers the 26 scored B-sessions (multi-turn B reports 6 per-session verdicts; the ambiguous/edge/F&O/exec/quality/regression B sets supply the rest).

### Per-category breakdown

| Category | A: P/Pt/F | A mean | B: P/Pt/F | B mean | Headline issue |
|----------|-----------|--------|-----------|--------|----------------|
| **F&O** | 1 / 1 / 2 | 5.00 | 1 / 0 / 3 | 3.50 | ask_user collapse on buildable build/critique; max_pain & PCR never computed → fabricated prose |
| **ambiguous** | 1 / 1 / 0 | 7.50 | 1 / 1 / 0 | 5.50 | right ASK_USER route; drops higher-impact ambiguity (unit > threshold); no choice chips |
| **edge-honesty** | 2 / 0 / 0 | 9.00 | 1 / 1 / 0 | 5.50 | honest boundary clean; "nearest real thing" named generically, no pre-filled card |
| **execution-stress** | 4 / 0 / 2 | 6.50 | 2 / 1 / 3 | 4.67 | false-refusal on supported "at open"; alert mis-routed to order; thin-text-on-correct-card |
| **multi-turn** | 1 / 2 / 3 | 4.83 | 1 / 2 / 3 | ~3.83 | stacked-affirmative confirm fails; %-of-reference triggers mis-parsed; Hinglish symbol resolve breaks |
| **quality-stress** | 4 / 0 / 2 | 6.33 | 2 / 2 / 2 | 6.17 | `yield` routing collision; named-symbol screen → wrong tool; rigid empty-section template; missing tables |
| **regression** | 2 / 0 / 0 | 9.00 | 2 / 0 / 0 | 7.50 | source/freshness label gap; thin glance quote; empty warnings array |

**Read:** Correctness is solid on the **answer/ask path** (price, valuation, trend, compare, ASK_USER routing) and on the **canonical build path** (RSI<30 agent, bull-put suggest, basket split, trailing-stop disclosure). It collapses on (a) **percent-of-reference triggers**, (b) **false-refusal of supported time anchors**, (c) **alert-vs-order gating**, (d) **stacked-affirmative confirm**, and (e) **named-symbol screens / single-stock-dividend routing** (keyword collisions). Output quality's dominant failure is **correct-but-thin**: a right widget shipped with a content-free one-liner, no markdown tables where the data demands them, and missing benchmark/freshness anchors.

---

## 2. Ranked fix list — Angle A (Execution Correctness)

Ordered by severity × frequency. Each: where · change · evidence · ideal.

### A1 — `[CRITICAL]` Percent-of-reference triggers mis-parsed into absolute / no-op conditions
- **Where:** workflow builder prompt + tool schema examples; `workflows/schemas.py:185` (`TriggerPriceConfig`) vs the prior-close-offset path (`schemas.py:740/889`) and `FetchRollingHighConfig` multiplier idiom (`schemas.py:765`); `fetches.py:749`.
- **Change:** Teach the builder to classify `"<n>% from/below the previous close"` as `prior_close × (1 − n/100)` (never `trigger.price{value:n}`), and `"<n>% from the day/recent high"` as `fetch.rolling_high{lookback:1, multiplier:1−n/100}` (never multiplier 1.0 vs the raw high). Add a **build-time guard**: if a `%`/"from previous close"/"from high" phrase is present and the step is `trigger.price` with a small absolute value (`value < 0.5 × live price`), OR `rolling_high.multiplier == 1.0`, reject and re-route to the offset path. Add exemplars to `system.md`.
- **Evidence:** `swap_symbol_then_add_stop` T0 parsed "4% from previous close" as `trigger.price{op:'<', value:4.0}` (literal ₹4 on a ~₹500 stock → never fires); `analysis_then_build_followup` T1 emitted `fetch.rolling_high{symbol}` with no multiplier (default 1.0) + 20-day default window, so `ltp <= 20d-high` fires on nearly every poll — the 3% AND "day's high" both silently dropped.
- **Ideal:** All turns hold a correct percent-offset trigger; T2's eventual DSL rebuild shape (`prev_close × 0.96`) is the target for T0.

### A2 — `[CRITICAL]` False-refusal / capability theatre on a *supported* time anchor
- **Where:** model tool reasoning + `services/chat_service.py` redirect logic; `system.md:1583` (open/close anchor → `trigger.market_relative_time(anchor='open')`), `:782`, `:1580`; `workflows/steps/triggers.py:286` confirms the trigger exists.
- **Change:** Add an explicit anti-refusal block: *"NEVER tell the user we cannot anchor to the daily open/close — we CAN via `trigger.market_relative_time`; only DAY-RELATIVE refs needing a runtime fetch (yesterday's close, prior high) require a fetch step."* Add a routing guard in `chat_service` so `"at open" + "+N% profit" + qty-present` cannot fall into `ask_user`/redirect; it must reach `propose_workflow` with the two-branch skeleton. Normalise `BAJAJ-AUTO` ↔ `BAJAJAUTO`.
- **Evidence:** `bajajauto_buy_open_sell_3pct` — canonical "buy at open, book +3%, 5 shares" REFUSED with a fabricated capability claim ("triggers can't anchor to today's open"), conflating supported open-anchor with day-relative yesterday's-close anchoring; offered a 09:30 downgrade but built nothing. 4 LLM calls / 16.6s / 113,858 in-tok — the most expensive turn, zero output.
- **Ideal:** One `workflow_draft_card`: entry `trigger.market_relative_time(anchor='open')` → buy 5 BAJAJ-AUTO market; exit `unrealised_pct >= 0.03`. No re-ask, no refusal.

### A3 — `[CRITICAL]` Alert ("don't buy") mis-routed to an ORDER macro
- **Where:** `services/tool_router.py` / `chat_service.py` reply-class routing; `system.md:506–515` (alert → `propose_dsl_workflow(action_kind='notify_only')`, "Do NOT call propose_threshold_order").
- **Change:** Detect explicit no-trade markers ("alert me", "just notify", "don't buy/sell", "ping me") in tool selection and **force `action_kind=notify_only` via `propose_dsl_workflow`, DENY `propose_threshold_order`** for that turn. Defensively, `propose_threshold_order` should auto-redirect to notify (or refuse) when a negative-trade marker is present rather than silently re-asking quantity. Add a hard `system.md` line: "NEVER ask quantity when the user said don't buy."
- **Evidence:** `axisbank_alert_not_order` — "just alert me when AXISBANK crosses 1300, don't buy anything" called `propose_threshold_order`, re-parsed as a BUY, then asked "How many shares should the agent BUY per fire?" → `render_hint=ask_user`, empty card.
- **Ideal:** `propose_dsl_workflow{action_kind:'notify_only', condition:'price crosses above 1300', symbol:'AXISBANK'}` → `workflow_draft_card` with a `notify.message` step; no quantity asked.

### A4 — `[HIGH]` Stacked-affirmative confirm falls through to LLM → fabricated "setup issue"
- **Where:** `services/chat_service.py:1349` `_PURE_AFFIRMATIVE_RE`; fast-path at `:2567–2615`.
- **Change:** Allow an optional **leading acknowledgement clause + separator** before the action verb, e.g. prefix group `(?:(?:looks good|sounds good|got it|perfect|great|ok(?:ay)?|yes|sure|fine)\s*[,;-]?\s+)?` ahead of the verb alternation. The current regex anchors each ack phrase as a *complete* alternative, so `looks good, go ahead and register it` fails (verified: `register it` → True, but the comma-stacked compound → False). Defensively, when the LLM path *does* fire on a confirm-shaped message with an active draft, it must route to the ack, never narrate a register error.
- **Evidence:** `amend_qty_then_confirm_register` T2 — "looks good, go ahead and register it" fell through, re-invoked `propose_threshold_order`, `render_hint` flipped to `ask_user`, narrated "I hit a setup issue while registering … it didn't get added." This is the exact loop/refuse-on-confirm regression `chat_service.py:1366–1375` claimed to have fixed for the single-clause case.
- **Ideal:** T2 → deterministic ack: "Got it — armed under register-not-execute; click Save & activate on the card." No re-emit, no fabricated error.

### A5 — `[HIGH]` ask_user collapse on buildable F&O build/critique (silent-defaults not applied)
- **Where:** model contract `system.md:963–976` (silent defaults) and `:966–970` (surface risk first); engine fully capable (`services/option_strategies.py:119–136` iron_condor delta defaults; `resolve_strategy` at `:318+`); critique path `agents/tool_executor.py:1735–1768`.
- **Change:** Map a named multi-leg structure with vague modifiers ("around current levels", "reasonable wings", "monthly expiry") to the template's delta/ATM defaults, never `ask_user`. Add iron_condor / "reasonable wings" / "naked put no strike" do-not-ask examples near `system.md:963–976`. For critique, extend `_critique_option_strategy`/`resolve_strategy` to synthesize a default ATM/OTM leg when only `option_type+side` are given. If the engine genuinely can't resolve (thin expiry-day chain), surface the honest limitation ("chain too thin for liquid wings, try next expiry") — never repackage an engine failure as an `ask_user` for inputs the user shouldn't supply.
- **Evidence:** `nifty_build_iron_condor` (FAIL/3) — `build_option_strategy` called (llm_calls=2) but rendered `ask_user` demanding center strike + wing width despite delta defaults; `critique_naked_put_reliance` (FAIL/3) — rendered `ask_user` for strike/premium, gating the screaming-risk warning behind a clarifying question (the exact anti-pattern at `system.md:966–970`).
- **Ideal:** Iron condor → 4-leg `option_strategy_card` ("0.20-delta shorts, 0.10-delta wings, Tuesday expiry, 1 lot — say widen/next expiry to change"). Naked put → risk framing FIRST, then a card on an ATM/OTM default leg + bull-put-spread alternative.

### A6 — `[HIGH]` `max_pain` + PCR requested but never computed → model fabricates prose
- **Where:** `market/option_chain.py:get_chain` (line 159) return payload; routing keywords already at `tool_router.py:657`; **`market/option_metrics.py` ALREADY computes `max_pain`/`pcr_oi`/`pcr_volume`** (verified lines 14–16, 126–136) but `get_chain` never calls it.
- **Change:** In `get_chain`, call `option_metrics.compute_option_metric` over the rows it already returns to add `max_pain`, `pcr_oi`, `pcr_volume` to the payload; surface them in `option_chain_card` schema + `card_digest`. Update the `system.md` option-chain prose contract to REQUIRE quoting the card's numeric `max_pain` and `pcr` when asked, and forbid prose generalities ("max pain is typically…") — if absent, say so plainly rather than estimate.
- **Evidence:** `nifty_chain_max_pain_pcr` (A:PARTIAL/5, B:FAIL/4) — user asked max pain AND PCR AND expected move; only `expected_move` is a real field, so the model hand-waved "23,300–23,350 looks like the key magnet zone" and "put OI dominates near ATM" with no numbers. The fields exist in `option_metrics.py` and the per-strike OI is in the rows — trivially derivable.
- **Ideal:** Card carries `max_pain` strike + `pcr_oi`; prose quotes both as real numbers ("Max pain 23,350; PCR 1.18 → mildly supportive") alongside expected move.

### A7 — `[HIGH]` Hinglish symbol resolution breaks; not-found fallback grabs filler word as ticker
- **Where:** `services/chat_service.py:5805` not-found fallback regex `\b([A-Z][A-Z0-9&\-_]{1,14})\b` over `user_message.upper()`; resolve path `market/yfinance_service.py:163` / `financials_db.py:267`.
- **Change:** (1) Fix the fallback symbol extraction — resolve against the instrument/sector universe and only name a symbol the user actually referenced; strip Hinglish stopwords ("actually","nahi","ka","to","aur","kharido","bech") before guessing, or reuse `financials_db.resolve_symbol`. "ACTUALLY" must never surface as a ticker. (2) Investigate why `get_live_price` returned empty for a top-50 NSE name at eval time (Kite token/data) and ensure a valid NSE ticker with a yfinance fallback never reports "not found" — fall back, don't deny existence. (3) Map Hinglish "X% gir jaye" to the percent-from-prev-close trigger.
- **Evidence:** `hinglish_then_resize_notional` (FAIL/2) — "TATAMOTORS 5% gir jaye to 15 share kharid lo aur 7% upar bech do" → "couldn't find price data for TATAMOTORS" (a valid, liquid ticker in `sector_universe.py:100`); T1 "actually 15 share nahi, 12000 ka kharido" → "couldn't find price data for ACTUALLY". No draft ever built.
- **Ideal:** T0 resolves TATAMOTORS → buy 15 on −5% dip from prev close, exit +7%; T1 mutates the SAME draft 15 → ₹12,000 notional, both legs intact.

### A8 — `[HIGH]` `yield` routing collision hijacks single-stock dividend intent
- **Where:** `tool_router.py:581` cash-park rule (`\byield(?:s|ed)?\b|...`) fires on the bare word "yield"; single-stock rules at `:117` & analysis at `:402` don't match "is X a dividend play".
- **Change:** Gate the cash-park rule so it does NOT fire when an equity ticker/company token co-occurs or the phrasing is "`<NAME>` dividend/yield" — require cash-park context ("park cash", "idle", "FD", "where should I park"). Concurrently broaden the single-stock fundamentals/analysis rules to catch "is X a dividend play", "X dividend yield", "X's dividend" so `fetch_fundamentals` is surfaced. Reinforce in `system.md` and the `get_yield_recommendation`/`compare_yields` tool descriptions (`tools.py:862–877`) that they are CASH-PARK only.
- **Evidence:** `itc_dividend_story` (A:FAIL/2, B:FAIL/5) — "is ITC still a solid dividend play after the demerger, what's the yield actually doing" matched ONLY the cash-park rule; surfaced `compare_yields`+`get_yield_recommendation` (FD/G-Sec/liquid-fund); `fetch_fundamentals` was never in the set, so the answer is a table of cash-park instruments with ZERO ITC numbers — exactly the "around 4% vibes" the test forbids.
- **Ideal:** `fetch_fundamentals(ITC)` (yield/payout/PE/ROE) + `get_price_history(ITC)` + optional news; report ITC's REAL yield, acknowledge the demerger.

### A9 — `[HIGH]` Named-symbol valuation screen routed to a sector-wide tool that can't scope the list
- **Where:** `tool_router.py:363–375` rank/screen rule. NOTE: `tools.py:1409–1421` shows `screen_fundamentals` DOES accept a `symbols` array — so the defect is the router/model preferring the sector path and **dropping** the user's list, not a missing param.
- **Change:** When the message contains a comma/and-separated list of ≥2 tickers alongside a valuation metric, prefer the symbol-scoped path: either `fetch_fundamentals` per name OR `screen_fundamentals(symbols=[…])` (the param exists). Add a per-symbol `fetch_fundamentals` **fallback** so a bounded named list completes in-turn rather than deferring. Update the screen tool description to say it must be scoped to the named set, not the broader universe.
- **Evidence:** `screen_cheap_high_roe_banks` (A:FAIL/3, B:FAIL/3) — user named ICICIBANK/KOTAKBANK/SBIN/AXISBANK to rank on PB+ROE; model called `screen_fundamentals` (returned sector-wide) + `compare_performance` (returns/Sharpe, wrong axis); ZERO per-name PB/ROE; admitted "the screen surfaced only the broader bank universe" and DEFERRED ("I can rank these next").
- **Ideal:** Ranked markdown table over exactly the four banks (Rank | Bank | P/B | ROE% | P/E | cheap+quality flag), completed in-turn.

### A10 — `[MED]` Multi-ambiguity: model resolves the softer ambiguity, silently guesses the higher-impact one
- **Where:** `system.md:335–336` (unit ambiguity is an explicit ASK_USER case), `:1339` (ask at most one per turn). Routing emits ASK_USER correctly; the defect is *which* ambiguity it picks.
- **Change:** Add an ambiguity-PRIORITY rule: when a message has multiple genuine ambiguities and only one question is allowed, rank UNIT/order-size (shares vs rupees vs lots) ABOVE soft threshold ambiguity, and NEVER silently default the unit dimension. Optionally permit one ASK_USER to bundle two tightly-coupled order-sizing values (unit + threshold).
- **Evidence:** `hundred_of_eichermot_units` (A:PARTIAL/6) — "buy me 100 of EICHERMOT when it dips a bit" clarified only the dip threshold and silently assumed 100 = 100 shares (~₹7.1L at ~₹7,106 vs ~₹100 = fractional share); the unit ambiguity the test probed was dropped.
- **Ideal:** Bundle both into one anchored question: "EICHERMOT is ~₹7,106, so 100 shares ≈ ₹7.1L. Confirm 100 shares (not a ₹ amount), and how big a dip — 2% below LTP or a specific ₹ level?"

### A11 — `[MED]` GTT / threshold cards return only a bare render_hint, not a structured params object
- **Where:** `create_gtt_order` path; `tool_executor.py` card payload; threshold-order cards.
- **Change:** Give `create_gtt_order` (and threshold orders) a structured card payload (`symbol/side/qty/trigger_price/limit_price/exchange/type`) instead of only `_render_hint=logic_card`, so params are machine-checkable in the widget, not just prose.
- **Evidence:** `hcltech_gtt_price_level` (A:PASS/8) — routing correct (price-level → GTT, qty=30 honoured, no fabricated price) but `card_digest` is just `{_render_hint: logic_card}`; widget-richness thinner than option/workflow cards. (FE later confirmed a live quote + trigger/limit rows DO render — the gap is the serialized digest, important for machine QA.)
- **Ideal:** Structured GTT card `{symbol, side=buy, qty=30, trigger=920, type=GTT}` echoed in the digest.

### A12 — `[LOW]` Price relays don't surface source/freshness; OI read-line can self-contradict the card
- **Where:** `tool_executor.py:_get_live_price` (1178–1232) result carries `source` ('kite'|'yfinance') but the formatter states the value flatly as live; `system.md` quick-price section (~187). OI summary at the option-chain read-line.
- **Change:** When `source != 'kite'`, tag the price reply (e.g. "KOTAKBANK ₹381.70, +1.22% (yfinance, EOD)") per the Kite-primary contract. For OI summaries, derive the "largest"/ordering claim from the sorted rows server-side or instruct the model to pick the single top-OI strike — never assert an ordering it then violates.
- **Evidence:** `plain_price_kotakbank` — value presented as "trading right now" though this run fell back to yfinance (Kite cache empty); `bhartiartl_call_chain_stopword` — "1800 CE has the largest OI … at ~11.22 lakh, followed by 1820 CE at 15.37 lakh" (15.37 > 11.22, so 1800 is NOT largest).
- **Ideal:** Source-tagged price; OI read-line names the true top-OI strike consistently.

---

## 3. Ranked fix list — Angle B (Output Quality)

### B1 — `[CRITICAL]` Thin-text-on-correct-card: widget ships, prose adds nothing
- **Where:** post-draft reply across `amend_qty` (t1), `swap_symbol` (t0–t2), `analysis_then_build` (t1), `basket_three_symbol_split`, `nifty_build`/critique (where they should build).
- **Change:** Honour the post-draft contract (`system.md:1100–1123`: ≤2 sentences, name symbol+action, one caveat the card doesn't surface) — but where the floor is missed, **enforce the floor**: name the symbol + action + a relevant caveat. For multi-leg/structural changes, restate both legs ("ENTRY buy 9 on a 4% dip; EXIT sell at −5%, register-not-execute"). The blurb "Drafted. Review and activate." (47 chars, naming neither symbol nor action) is *below* the build's own floor at `system.md:1119–1122`.
- **Evidence:** `basket_three_symbol_split` text = "Drafted. Review and activate the workflow card." (correct ₹20k×3 / NIFTY −1% card, zero narration); `amend_qty` t1 = "Drafted — NESTLEIND RSI(14) < 30 buy 8 shares. Click Activate." (contract-compliant but missing the register-not-execute reassurance the session demanded).
- **Ideal:** "1 trigger → 3 buys. When NIFTY falls 1% intraday, I'll market-buy ₹20,000 each of SUNPHARMA, GRASIM, JSWSTEEL (₹60,000 total, equal split). Registers — you activate."

### B2 — `[CRITICAL]` Missing markdown tables where the data demands them
- **Where:** option-chain OI read (`bhartiartl`, `nifty_chain`), strategy legs (`banknifty_suggest`, iron condor, critique), bank valuation compare (`screen_cheap_high_roe_banks`, `infy_vs_tcs`), single-stock multiples (`is_reliance_expensive`, `analyse_hdfcbank`).
- **Change:** Auto-render a markdown table for ANY screen, multi-metric valuation block, head-to-head, or option-chain ATM band. Per `CLAUDE.md` the bar explicitly calls for tables on comparisons/screens. Bullet lists of multiples / prose narration of a 17-row chain are anti-patterns.
- **Evidence:** No F&O answer used a table; the 3-bank compare came back as bullets; the 17-row BHARTIARTL chain narrated in prose (with a wrong ranking). `screen_cheap_high_roe_banks` rendered ZERO table on the canonical table use-case.
- **Ideal:** option-chain → `Strike | Call OI | Chg OI | IV` (3–5 ATM rows); compare → `Bank | P/E | P/B | ROE | Div Yield` with callouts beneath.

### B3 — `[HIGH]` Answer-the-literal-question drift + missing benchmark anchors
- **Where:** `itc_dividend_story` (never states ITC's yield), `screen_cheap_high_roe_banks` (ranks none on the requested axes), `is_reliance_expensive`/`is_nifty_uptrend`/`analyse_hdfcbank` (verdicts asserted, not benchmarked).
- **Change:** Lead every read with the specific figure the question targets, then frame. Anchor verdicts: PE/PB vs own history or sector where available; %-distance to SMA50/SMA200 on trend reads. (Note: sector/peer-PE and PE-history tools do NOT exist in the build today — where unavailable, anchor against return profile/price structure and say so, don't fabricate a comparator.)
- **Evidence:** ITC asked "what's the yield actually doing" → 613 tokens, no ITC yield/payout/DPS; NIFTY trend gives raw SMA levels but not "~1.9% below the 50, ~6.8% below the 200"; RELIANCE "PE 25 is not obviously cheap" with the comparator omitted.
- **Ideal:** ITC → "ITC yields ~X%, payout ~Y%, DPS ₹Z (TTM)" as the top row; NIFTY → "Price 23,242 < 20d 23,562 (−1.4%) < 50d 23,700 (−1.9%) < 200d 24,941 (−6.8%) → full bearish stack."

### B4 — `[HIGH]` Honesty-path under-leverage: "nearest real thing" named generically; no pre-filled card
- **Where:** edge-honesty sessions (`us_adr_recurring_buy`, `news_sentiment_autosell`); ASK_USER schema `agents/tools.py`; ask_user card in `pivot-next/components/chat`.
- **Change:** Name the *specific* instrument/rule and quote a number; where required fields are genuinely missing, ASK_USER is correct — but enrich its payload with a structured `options` array (label + the concrete instrument/trigger it maps to) so the offered alternative renders as one-tap choice chips, and where defaultable, emit a partially-filled SIP/`workflow_draft_card` (symbol+frequency known) so the user edits one or two blanks. Add a one-line *defended* view ("MON100 is the standard retail route to NVIDIA exposure from an Indian demat").
- **Evidence:** `us_adr_recurring_buy` (B:PARTIAL/5) — "a US tech ETF you name" instead of naming MON100 (NSE-listed, holds NVDA) with the real SIP path; `news_sentiment_autosell` (overturned to PASS) names the right two rails but with no example keyword set. (Caveat: the verifier ruled `news_sentiment_autosell` correctly ASK_USERs because `keyword_set` is genuinely required and non-defaultable — do NOT invent ADANIENT keywords; seed examples only after the rail is chosen.)
- **Ideal:** "NVIDIA trades on Nasdaq (not covered). Closest NSE route is MON100 (Motilal Oswal NASDAQ-100 ETF) — it holds NVDA alongside AAPL/MSFT. Want a monthly SIP into MON100? Tell me amount (min ₹100) and day." + pre-filled SIP card.

### B5 — `[HIGH]` Depth is bimodal; thin answers are exactly the trend/screen asks that need the most structure
- **Where:** `is_nifty_uptrend` (202 tok), `screen_cheap_high_roe_banks` (224 tok), `analyse_hdfcbank` (357 tok) vs `is_reliance_expensive` (722), `itc` (613), `infy_vs_tcs` (540).
- **Change:** Set a depth floor (~450+ tok) for analysis/screen/trend intents and require the structural element the ask implies (table or SMA-stack map). A "proper full analysis" must carry a returns ladder (1w/1m/3m/6m/1y) and a valuation table; a trend read must carry the SMA stack with %-distances.
- **Evidence:** `analyse_hdfcbank` had 4 clean multiples (PE 16.53, P/B 1.95, ROE 13.82%, yield 1.76%) in a sentence not a table, only the 1Y return, SMA levels named but not priced/%-distanced, news = "some market chatter".
- **Ideal:** Snapshot → returns-ladder table → technical level map with %-distances → valuation table → 1–2 dated headlines → labelled verdict.

### B6 — `[MED]` Confirmation phrasing contradicts user intent on alert-not-order
- **Where:** `axisbank_alert_not_order` response text.
- **Change:** Drop the qty re-ask entirely for notify-only; confirm as an alert and state the channel: "Watching AXISBANK — I'll alert you the moment it crosses above ₹1,300. No order will be placed (in-app alert)." Offer "want me to also arm a buy?" only as an optional follow-up.
- **Evidence:** Despite "don't buy anything," confirmation read "Buy AXISBANK when price crosses_above ₹1300" + "How many shares should the agent buy per fire?" — fights the user's words; channel never disclosed.
- **Ideal:** alert confirmation + explicit "no buy" + channel, no quantity prompt.

### B7 — `[MED]` Missing grounding context (CMP / computed stop / freshness) on order & quote confirmations
- **Where:** `hcltech_gtt` (no CMP / dip %), `titan_trailing_stop` (no computed stop level), `plain_price_kotakbank` (no "as of HH:MM IST" stamp, no ₹-change/range/volume).
- **Change:** For GTT, add CMP + implied dip ("HCLTECH ~₹X now; arms a buy if it drops ~Y% to ₹920") and a validity note (~1yr). For trailing stop, compute the initial stop level from the holding's real price. For quotes, add ₹-change, day range, volume, and "as of <time> IST (source)". Use only real quote values, never invented. (Verifier note: where a field is absent from the tool payload — e.g. `get_live_price` returns only ltp/change%/source — do NOT fabricate; this fix applies to fields the tools actually expose.)
- **Evidence:** `plain_price_kotakbank` B:PASS/7 — appropriately terse but no freshness stamp; `titan` — "initial stop 7% below current price" with the actual level never shown.
- **Ideal:** "KOTAKBANK ₹381.70 (+₹4.60, +1.22%) · as of 15:51 IST (yfinance, EOD)"; "TITAN ~₹X → initial stop ~₹X×0.93."

### B8 — `[MED]` Rigid template force-fit produces empty / mislabelled sections
- **Where:** `itc_dividend_story` ("## Technicals" with zero technicals, "## Fundamentals" with zero fundamentals), `is_reliance_expensive`/`infy_vs_tcs` ("News: I didn't pull news").
- **Change:** Make the analysis skeleton CONTENT-DRIVEN — drop empty sections; never print a header whose data wasn't fetched. (Largely downstream of A8: once ITC routes to `fetch_fundamentals`, the sections fill.)
- **Evidence:** ITC's labelled blocks carried generic hedging, not the data the labels promise.
- **Ideal:** Only render a section when its data exists.

### B9 — `[MED]` Grounded-but-thin suggest card under-writes the rich numbers it carries
- **Where:** `banknifty_suggest_bullish` (B:PASS/6, overturned from PARTIAL) — contract-compliant but could be richer.
- **Change:** (Polish, not a grade-driver — verifier confirmed it meets the 5-field card-prose contract.) Optionally add a 1-line thesis, a 2-row leg table in prose, and an explicit R:R line ("risk ₹6,705 to make ₹2,295, ≈1:0.34, ~67.5% POP").
- **Evidence:** 69 words, two sentences; strikes carried in the card, R:R defensible but undefended in prose.
- **Ideal:** thesis → leg table → honest R:R → 1-line candidate trade-off → register/not-advice.

### B10 — `[LOW]` Empty `warnings: []` on buildable cards leaves risk caveats on the table
- **Where:** `plain_rsi_agent_grasim` (B:PASS/8, overturned to PASS) card `warnings: []`.
- **Change:** (Verifier confirmed `warnings` is a *capability-disclosure* channel, not a market-risk advisory; `[]` is the correct default for a single-leg RSI buy, and `system.md` "buy-only means buy-only" FORBIDS adding an unprompted stop. So this is **not** a fix — flagged only so the optimizer does not regress correct behaviour.) Any market-risk note must live in the ≤2-sentence handoff as "one caveat the card doesn't surface," within the word cap.
- **Evidence:** Discriminator wanted falling-knife/no-exit warnings injected; verifier refuted — those would violate the build's contract.
- **Ideal:** Leave `warnings:[]`; optionally surface a single risk caveat in prose only.

---

## 4. What excellent output looks like

Distilled from the Angle-B verifier exemplars + best-in-class option/equity research conventions (Zerodha Varsity, PL Capital research-note structure).

### 4.1 Universal shape (analysis/compare/screen)
```
[1-line VERDICT] — the answer to the literal question, with the load-bearing number.
[Structured body] — sections OR a markdown table, never a wall of prose.
[Defended view] — why, with the comparator shown (vs history / sector / SMA in %).
[Forward hook] — the one condition that would flip the call, or a buildable next step.
[Not-advice line] — once.
```
**Exemplar (the panel's bright spot — `screen_then_dont_understand` t0):** verdict-led, sectioned (snapshot / what the numbers say / interpretation / bottom line), real numbers matching external analyst data (SBIN ROE ~15.5%, PNB cheapest on P/B, BoB best yield), a defended view, a forward offer. This is the template the build-turn readbacks should aspire to.

### 4.2 Comparison / screen — ALWAYS a table
```markdown
| Bank | P/E | P/B | ROE | Div Yield |
|------|-----|-----|-----|-----------|
| ICICIBANK | 18.2 | 2.9 | 17.4% | 0.8% |
| SBIN | 9.1 | 1.4 | 15.5% | 1.6% |
| KOTAKBANK | 19.6 | 2.6 | 14.1% | 0.1% |
| AXISBANK | 12.3 | 1.9 | 16.8% | 0.4% |

**Cheapest:** SBIN (P/B 1.4) · **Best quality:** ICICIBANK (ROE 17.4%) · **Best yield:** SBIN
```
One row per symbol, one column per metric, verdict callouts beneath. Never a per-stock bullet list for a multi-name compare.

### 4.3 Option chain (max pain / PCR / OI) — number-rich verdict + ATM-band table
```
Pinning bias near 23,350 — tight weekly range.
Max pain 23,350 (spot 23,347, ~0 away → expiry pin likely) · PCR(OI) 1.18 → mildly supportive · Expected move ±86 (0.37%) → 23,261–23,433 unless a catalyst hits.
```
```markdown
| Strike | Call OI | Put OI | Read |
|--------|---------|--------|------|
| 23,400 | 15.4 L  | 4.1 L  | first overhead resistance |
| 23,350 | 9.0 L   | 8.8 L  | max-pain / battle zone |
| 23,300 | 3.2 L   | 12.6 L | support floor |
```
Lead with a single max-pain *strike* (never a vague range when a strike is computable), a numeric PCR with a band read (>1 supportive / <0.7 bearish / 0.8–1.2 range), then the expected-move band. Any "largest OI" claim must equal the max of the cited values.

### 4.4 Option strategy suggest/build — thesis + leg table + honest R:R
```
Defined-risk bullish income: you keep the credit if BANKNIFTY holds above 54,200 into nearest expiry (1 lot).
```
```markdown
| Side | Type | Strike | Premium |
|------|------|--------|---------|
| SELL | Put  | 54,200 | … |
| BUY  | Put  | 53,900 | … |
```
```
Risk ₹6,705 to make ₹2,295 (≈1:0.34) with ~67.5% POP — you win above BE 54,272.61.
bull put = income/range-floor · bull call = directional · long call = convex/aggressive.
Registers — not advice.
```
For a named structure with vague modifiers ("reasonable wings"), **build the defaulted card**; never ask_user.

### 4.5 Single-stock "full analysis"
```
Snapshot: ₹739.70, −25.24% 1Y, below all SMAs.
```
```markdown
| Window | Return |   | Metric | Value | Read |
|--------|--------|---|--------|-------|------|
| 1w | −1.2% |  | P/E | 16.53 | fair |
| 1m | −3.4% |  | P/B | 1.95 | fair |
| 3m | −8.1% |  | ROE | 13.82% | ok |
| 1y | −25.2% | | Yield | 1.76% | low |
```
```
Technicals: price ₹739.70 < 20d (−X%) < 50d (−Y%) < 200d (−Z%) → bearish stack, RSI 39.
News: [1–2 dated headlines] OR "no material fresh news."
Verdict: NEUTRAL — wait for an SMA20 reclaim. Not advice.
```

### 4.6 Honest-boundary turn (edge cases)
```
NVIDIA trades on Nasdaq, which Pivot doesn't cover. The closest NSE-listed route is MON100 (Motilal Oswal NASDAQ-100 ETF) — it holds NVIDIA alongside Apple/Microsoft. Want me to register a monthly SIP into MON100? Tell me the amount (min ₹100) and the day.
```
One-sentence boundary → a NAMED, NSE-tradable proxy with a one-line *why it fits* → an immediate buildable offer (pre-filled card where defaultable). Defer to ASK_USER only for genuinely user-specific, non-defaultable blanks (amount/day; or a `keyword_set` that must not be fabricated).

---

## 5. Cross-cutting patterns

1. **Two regression modes dominate, one per angle.**
   - *Angle A:* the **build path** turns intent into params unreliably (percent-of-reference triggers, false-refusal of supported anchors, alert-vs-order, named-symbol screens), while the **answer/ask path** is largely correct.
   - *Angle B:* **correct-but-thin** — a right widget paired with a one-line stub, no tables, no benchmark anchor.

2. **ask_user is overused as an escape hatch.** 2/6 execution-stress + both F&O build/critique + edge cases collapse to `ask_user` on fully-specified or defaultable prompts. ask_user should be reserved for genuinely missing, non-defaultable params; a guard should block it when symbol+qty+condition are present and the shape is supported.

3. **Keyword-collision routing.** "yield" → cash-park; named-symbol screen → sector-wide; "is X a dividend play" → no single-stock rule. Tool selection is too eager on bare keywords and too narrow on natural phrasings — both push the model to the wrong tool set.

4. **Percent / reference semantics are systematically lost.** "4% from prev close" → literal ₹4; "3% from the day's high" → ×1.0 vs 20-day high. The schema primitives exist (`schemas.py:765/889`); the builder doesn't select them or set the multiplier.

5. **Capability theatre vs honest degrade.** The build sometimes *under-trusts* its own DSL (refuses supported open anchors) and sometimes *over-claims* (fabricates max-pain/PCR prose for unimplemented fields). Both stem from a gap between what the prompt says is supported and what the engine actually does — close the gap by (a) computing the trivially-derivable metrics, (b) enumerating supported anchors in an anti-refusal block.

6. **Tables are the single highest-leverage Angle-B fix.** Every compare/screen/chain ask that demands a table got prose; the few that used tables (`infy_vs_tcs`) scored highest on B.

7. **Multi-turn evaluation is essential.** Single-turn checks would have passed `swap_symbol` (its broken ₹4 trigger self-repaired only on T2's rebuild) — the carried-forward bad param is invisible without turn-over-turn assertion.

8. **Bright spots to protect:** trailing-stop disclosure (honest, in card AND text), basket split (faithful ₹20k×3 / index trigger), bull-put suggest (5-field contract met), RSI<30 agent (buy-only respected, no unprompted stop), edge-honesty boundary discipline, and `screen_then_dont_understand` t0 (the gold-standard structured answer).

9. **Cost/quality inversion to watch:** the single FAIL that burned the most compute (`bajajauto`, 4 LLM calls / 16.6s / 114k in-tok) produced *no* widget. Refusal/uncertainty correlates with both high cost and low output — the opposite of desired.
