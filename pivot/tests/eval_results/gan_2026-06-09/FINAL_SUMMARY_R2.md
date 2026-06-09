# GAN FINAL SUMMARY — Round-2 baseline vs Round-3 (post-R2-fix)

**Run files**
- R2 baseline: `latency_recheck/run_20260609_191718.json` (p50 server 8208.5ms, out_tok sum 8460)
- R3 snapshot: `r3/run_20260609_210102.json` (p50 server 8482.5ms, out_tok sum 9320)
- 28 sessions / 36 turns each, identical session set, same harness.

Judging method: re-read the **actual** R2 and R3 responses + widgets/card_digests turn-by-turn (reworded ≠ improved). Verdicts below are the judge-verified discriminators; four R3 Angle-B verdicts were upgraded to PASS on verify (news_sentiment_autosell, axisbank_alert_not_order, bajajauto_buy_open_sell_3pct, amend_qty_then_confirm_register — each was a near-verbatim execution of a sanctioned prompt exemplar, not a real defect).

---

## Scoreboard — before → after

| Angle | Pass | Partial | Fail | Mean |
|---|---:|---:|---:|---:|
| **A (execution)** R2 | 19 | 2 | 7 | 6.75 |
| **A (execution)** R3 | **19** | **5** | **4** | **7.50** |
| **B (output quality)** R2 | 15 | 7 | 6 | 5.79 |
| **B (output quality)** R3 | **16** | **8** | **4** | **6.50** |

Net: FAILs cut on both angles (A 7→4, B 6→4); means up (A +0.75, B +0.71). The Angle-A pass count is flat at 19 because three former FAILs landed as PARTIAL (fix half-worked) and two former PASSes regressed to FAIL (offsetting the gains). Angle B is the cleaner win: +1 pass, −2 fail, +0.71 mean.

---

## What each fix achieved (verified against real outputs)

**HELD / improved**

- **R1/R8 — analysis/screen reply-class routing (Angle B, big win).** Index-trend, noun-form analysis, and screen/rank asks were falling to `analytical_short` and shipping thin prose. R3 shows the working sectioned-table template now selected: `is_nifty_uptrend` 221→596 out_tok with a `## Snapshot / ## Technicals` MA table; `analyse_hdfcbank_full` 278→761 with returns/technicals/fundamentals tables; `analysis_then_build_followup` t0 169→461; `screen_cheap_high_roe_banks` 301→534 with a full bull/bear View. This is the Round-1 "mandatory tables on compares/screens" floor finally reaching every analysis intent.
- **R2 — buy-at-open de-regression (Angle A).** `bajajauto_buy_open_sell_3pct`: R2 collapsed to the banned 09:30-cron `ask_user` downgrade (FAIL); R3 emits the correct `trigger.market_relative_time(anchor=open)` + `place_order(buy,5)` + `trigger.exit_compound(unrealised_pct≥0.03)` 4-step card (PASS). Deterministic pin works.
- **R3 — notify-only de-regression (Angle A).** `axisbank_alert_not_order`: R2 bounced to `ASK_USER` on a fully-specified prompt (FAIL); R3 emits `trigger.price(crosses_above,1300)+notify.message(push)`, no order, no qty re-ask (PASS). Removing ASK_USER from scope + forcing `propose_dsl_workflow` is what fixed it.
- **R6 — confusion-after-menu teach (Angle A).** `i_dont_understand_then_clarify` t1: R2 re-dumped the same ASK_USER menu (`ask_user` again); R3 returns pure teaching prose ("Nothing has been set up yet — I only offered options…") that corrects the false premise, explains RSI<30 plainly, retains COALINDIA context (PASS).
- **R10 — workflow amend diff readback (Angle B).** `swap_symbol_then_add_stop` t2: R2 generic readback; R3 ships the best-in-class `"Changed: added a 5% stop loss. Kept: AXISBANK, 9 shares, 4% dip entry."` diff. Promote to a required amend contract.
- **R13 — qualifier-leveraged ambiguity (partial).** `the_tata_one_entity`: R2 returned a generic TCS/TATASTEEL/… list; R3 leads with `TRENT (strongest recent run)` and a curated set. The qualifier is now honored cosmetically — but see "still open" (no fetch, stale baked-in fact).
- **critique alternative quantified (Angle A/B).** `critique_naked_put_reliance`: R2 only named "a bull put spread is safer"; R3 calls `build_option_strategy` and quantifies the alternative (max loss ₹15,787.50 / max profit ₹4,212.50), risk-first.
- **R12 — de-hardcoded EICHERMOT anchor.** `hundred_of_eichermot_units` still PASS; anchor moved ₹7,100→₹7,203 via a live fetch, unit-first + bundled threshold intact.

**Regression guardrails held:** `plain_price_kotakbank` (real yfinance ₹381.70/+1.22%, source-tagged) and `plain_rsi_agent_grasim` (deterministic RSI<30 buy-10 card) both still clean on Angle A.

---

## Exemplars

- **Best new output:** `analyse_hdfcbank_full` / `is_reliance_expensive` (874 out_tok, P/E-25-vs-9%-ROE tension named, conditional defended view) — analysis engine is essentially at the Angle-B bar.
- **Best amend pattern:** `swap_symbol_then_add_stop` t2 "Changed:/Kept:" diff.
- **Best honesty boundary:** `news_sentiment_autosell` — states the sentiment-NLP boundary, names the keyword-event trigger with concrete ADANIENT-relevant seeds, ASK_USER only for the non-defaultable field.

---

## Regressions (R2 PASS/PARTIAL → R3 worse)

1. **`basket_three_symbol_split` — PASS → FAIL (worst regression).** R2 correctly built a 3-leg `propose_workflow` card with `notional_inr:20000` each on a NIFTY −1% `trigger.compound`. R3 degraded to `ask_user` re-asking "How many shares of SUNPHARMA…" for a rupee budget the user already gave (₹60,000/3). The anti-default-1-share guard now mis-fires on an explicit-budget basket; `_USER_QTY_PATTERNS` doesn't treat a bare `60000` (no ₹ prefix) as a budget.
2. **`hinglish_then_resize_notional` t1 — PARTIAL → FAIL.** R2 kept the TATAMOTORS draft on a notional resize ("still shows 15 shares; edit on card"). R3's R7 resize path fires but `get_live_price` returned no data and recovery dead-ends into `_format_recoverable_failure_question`, which extracts the symbol from the **current message only** — the Hinglish turn has no ticker, so it emits the context-amnesiac "Tell me the NSE ticker (e.g. TATAMOTORS…)" and abandons the draft. The recovery ignores the active-draft symbol in scope.
3. **`hcltech_gtt_price_level` — new Angle-B text defect (execution theatre).** R2 said "Drafted: buy 30 HCLTECH via GTT…"; R3 says **"GTT placed for HCLTECH"** while the widget still shows an unclicked Confirm CTA and the model is register-not-execute. The text now claims a terminal state it hasn't reached and contradicts its own card. (Held PASS on Angle A — parse is correct — but the prose regressed.)

---

## Still open (highest-leverage residuals)

- **R4 — named iron-condor still loops to ASK_USER (A FAIL / B FAIL).** Both R2 and R3 emit `render_hint=ask_user` asking for wing width. The R4 guard *fires* (`tools_called=['build_option_strategy']`, ASK_USER stripped) yet the user-visible outcome still collapses to a clarification — forcing the tool does NOT prevent a `success:False`/empty-card post-tool turn from re-emitting `ask_user`. **Needs a post-tool assertion** that rewrites a terminal `ask_user` into the honest "chain too thin → try next expiry" line or a next-expiry rebuild, plus injecting the resolved template+expiry (default nearest MONTHLY) into the build args.
- **R9 — F&O mandatory tables still dropped (B, 3/4 F&O sessions FAIL/PARTIAL).** `nifty_chain_max_pain_pcr` quotes max-pain/PCR/expected-move but renders **zero markdown table and names zero OI strikes** despite the system.md:1113-1131 mandate and full `rows` in the payload. `banknifty_suggest_bullish` names one of three candidates with no comparison table. `critique_naked_put_reliance` omits the mandated 2-row table and the POP line. Prompt pressure alone isn't holding — enrich `card_digest` (top-3 call/put OI strikes; per-candidate quad) so the truncated LLM view can populate the table deterministically.
- **Disambiguation fetch not enforced (A/B).** `the_tata_one_entity`: zero `get_price_history`/`get_live_price` call, no per-candidate numbers, and "TRENT strongest" is parroted from the (now stale/wrong) hardcoded system.md example. Make the qualifier-triggered fetch a hard precondition; replace the baked-in TRENT fact with a neutral placeholder.
- **Short-put "unlimited downside" mislabel (A).** Engine `option_strategies.py:449-456` reports `max_loss=None` for a short put (bounded at strike-to-0); critique prose faithfully says "unlimited". Fix in the engine (treat price=0 grid floor as closed for puts), not the prompt.
- **Widget-vs-prose on proxy SIP (B).** `us_adr_recurring_buy`: symbol (MON100) + frequency (monthly) both known, yet no pre-filled SIP draft card (`tools_called=[]`, card_digest=null) — contradicts system.md:360-362. Also missing the persuasive number (NVDA ~8.9% weight) and a 2-3 row proxy table.
- **itc trajectory ask answered with static snapshot (B).** "what's the yield ACTUALLY doing after the demerger" gets a point-in-time read; needs a then-vs-now 2-point trajectory.
- **screen ranking vs recommendation incoherence (A/B).** `screen_cheap_high_roe_banks` ranks P/B-ascending (SBIN #1) but crowns the rank-4 highest-P/B row (ICICIBANK) as the pick and ticks only that row "Cheap+Quality". Add a composite Score column and rank+recommend on it.
- **R14 (deferred) — yfinance as_of date** on the fallback payload; `(yfinance, EOD)` is undated.

---

## latency_note

R3 server p50 = **8482.5ms** vs the ~8.2s post-round-1 / R2 p50 (8208.5ms) — **~+274ms (~3%) slower** at the median, with R3 p90 actually *lower* (11739ms vs 12839ms) and mean *lower* (8204ms vs 8442ms). Output grew (sum 8460→9320 out_tok; mean 256→282) from the richer analysis-table answers, while `llm_calls` mean dropped (1.94→1.86) because several deterministic scope/tool_choice guards (R2-R6) cut an extra LLM round-trip. Net read: the small p50 bump is the cost of more substantive answers, paid back at the tail; no latency regression of concern. One outlier worth noting: `bajajauto_buy_open_sell_3pct` ran 5 llm_calls / 19.2s for the open+3% build.

---

## Next work order (priority)

1. **P0 — un-regress `basket_three_symbol_split`:** widen `validation_handler.py:705 _USER_QTY_PATTERNS` to treat a bare 4-7 digit number + a split/across cue as a budget; add a worked example fusing `trigger.compound` (index gate) + a single `action.allocate_notional(symbols=[…literal…], total_inr, strategy='equal')`. Never `ask_user` for size when a rupee budget is present.
2. **P0 — un-regress `hinglish_then_resize_notional`:** thread the active-draft symbol into `_format_recoverable_failure_question`/`_extract_user_symbol`; on `get_live_price` failure during a resize, retry on the draft symbol, fall back to last close, or keep the draft + old qty — never abandon it or re-ask for the in-scope ticker.
3. **P1 — kill the iron-condor ASK_USER collapse (R4):** post-tool assertion rewriting a terminal `ask_user` from a forced named-build into the honest thin-chain line or a next-expiry rebuild; default expiry to nearest MONTHLY.
4. **P1 — F&O table discipline (R9):** enrich `card_digest` with top-3 call/put OI strikes + per-candidate {max_profit,max_loss,pop,net} so the mandated chain/suggest/critique tables are populatable from the truncated view; assert the table + POP line are present.
5. **P2 — execution-theatre lint:** forbid "placed/created/live" in pre-confirmation order/GTT/SL text; template to "drafted / ready to register — confirm on the card".
6. **P2 — enforce disambiguation fetch + de-hardcode the TRENT example** in system.md; add the short-put bounded-loss engine fix and the proxy-SIP pre-filled card + persuasive number.
