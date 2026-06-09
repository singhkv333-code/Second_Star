# Workflow-30 live chat-eval — judge report (2026-06-08)

Snapshot: `tests/eval_results/workflow_30/run_20260608_224317.json`
Sessions: 31 · Turns: 33 (one 3-turn amendment session)

## Headline

| Outcome | Count |
| --- | --- |
| PASS | 22 |
| PARTIAL | 7 |
| FAIL | 2 |
| **PASS-rate** | **22 / 31 = 71%** |
| PASS + PARTIAL (any usable artefact / honest boundary) | 29 / 31 = 94% |

One-sentence verdict: **the build is solid on the bread-and-butter shapes (indicator/price/schedule/SIP/cross-symbol/basket/event/amend/Hinglish) and routes alerts/boundaries with real discipline**, but it ships two repeatable defects — *trailing-stop live-limitation undisclosed (capability theatre on TITAN)* and *qty-clarification fires even when the user obviously means "use sensible-default size"* — and one P0-borderline (the `coalindia_alert_only` notify_only routing emitted a notify card, but used a `crosses_above` price leaf instead of the system-prompt's `propose_dsl_workflow(action_kind='notify_only')` path with the cleaner readback — still a PASS on the user-visible outcome, flagged as P2 polish).

### Quality triad

| Metric | Value |
| --- | --- |
| Latency p50 (wall) | **8 354 ms** |
| Latency p95 (wall) | **15 430 ms** |
| Latency max | 15 858 ms (none over 20 s) |
| Total input tokens | **1 992 889** |
| Total output tokens | **5 471** |
| Mean LLM calls / turn | **2.24** |
| Deterministic / fast-path (llm_calls = 0) | 2 turns — `tcs_rsi_oversold_buy` (15 ms), `niftybees_920_buy` (9 ms) — both PASS, both produce correct cards via example-cache replay |
| Turns > 20 s | **0** |

p95 under 16 s with mean ~2.2 LLM calls is healthy; the **input-token bill (≈2 M for 33 turns ≈ 60 k tokens/turn)** is the biggest cost lever and is dominated by the system-prompt + tool catalog payload (each `propose_dsl_workflow` round trip ships ~30 k tokens twice). Output is tiny (165 tok/turn avg), so the cost is almost entirely *prompt overhead*, not generation.

---

## Per-session verdict table

| # | Name | Category | Verdict | Sev | One-line evidence |
| -- | ---- | -------- | ------- | --- | ----------------- |
| 0 | reliance_dip_profit_stop | pct_dip | **PARTIAL** | P1 | User gave qty=10 in the prompt, bot still asked "how many shares?" — slot extraction lost the qty (`render_hint=ask_user`, no card) |
| 1 | infy_friday_profit | schedule | **PASS** | – | Clean 4-step card: schedule(Fri) → buy 10 → exit_compound(unrealised_pct≥0.1) → sell 10; backtestable=true |
| 2 | tcs_rsi_oversold_buy | indicator | **PASS** | – | Deterministic-cache hit (15 ms, 0 LLM): trigger.indicator(rsi,14,<,30) + buy 15 — textbook |
| 3 | hdfc_golden_cross | indicator | **PARTIAL** | P2 | Reasonable qty ask (user gave none), but lost a chance to draft and let user amend qty — c.f. `wipro_macd_bullish` which drafted because user said "buy 20" |
| 4 | wipro_macd_bullish | indicator | **PARTIAL** | P1 | Card built (good) but encodes "bullish MACD crossover" as `macd > 0` (level, not crossover) — semantically wrong; backtestable=true masks the modelling error |
| 5 | tatamotors_52w_high | breakout | **PARTIAL** | P2 | Asked "52-week high or specific ₹" — fine clarification but the system-prompt has a clear "use rolling 252-bar high" path, so should have drafted with a default and offered the override |
| 6 | icici_above_sma200 | breakout | **PASS** | – | Compound: close(ICICIBANK) > SMA(200) + buy 5; alert intent acknowledged by notify-less buy (user said "alert me AND buy 5") — minor: dropped the explicit notify leg but the card is sane |
| 7 | sbin_price_cross_buy | price_level | **PASS** | – | "buy 50 SBIN if it falls to 720" → `create_gtt_order` GTT buy @720 — right tool, qty preserved |
| 8 | coalindia_alert_only | price_level | **PASS** | P2 | Notify_only routing held — `trigger.compound(crosses_above 420)` + `notify.message`; cleaner readback would use the action_kind='notify_only' macro path per system.md §412 |
| 9 | maruti_gap_down_buy | gap | **PARTIAL** | P1 | Qty ask fine, but no draft means we never see if the gap-down condition was modelled correctly (gap is the diagnostic-worthy leg, not the qty) — should draft with qty=1 placeholder + ask |
| 10 | adani_volume_spike | volume | **PARTIAL** | P1 | Same shape — qty ask without drafting the volume-vs-20DMA leg first; users can't course-correct the trigger they care about |
| 11 | niftybees_920_buy | intraday_time | **PASS** | – | Deterministic-cache (9 ms): schedule cron `20 9 * * *` + buy 100 — correct cron, correct symbol, no leak into "limit price ₹920" trap |
| 12 | infy_squareoff_325 | intraday_time | **PASS** | – | `25 15 * * 1-5` weekday cron + `action.squareoff_symbol(INFY)` — perfect |
| 13 | weekly_sip_goldbees | sip | **PASS** | – | `propose_scheduled_order` → schedule(Mon 09:15) + `notional_inr=2000` — rupee-budget passed straight through, no qty ask |
| 14 | monthly_sip_nifty | sip | **PASS** | – | `create_sip` → logic_card "monthly NIFTYBEES SIP ₹10,000 on the 1st" — right tool for monthly SIP |
| 15 | titan_trailing_stop | trailing | **FAIL** | **P0** | Card built with `trailing=true` but response is **"Drafted: trailing 8% stop-loss on TITAN…Click Activate"** with **NO disclosure of "live re-ratcheting is coming"** per system.md §1199-1203 — capability theatre on a known-broken capability |
| 16 | bajfinance_drawdown_exit | exit_logic | **PARTIAL** | P1 | Refused with "can't tie entry's peak to exit" — but `position.drawdown_from_peak_pct` IS the exit field for exactly this (system.md §1455) and prior session 1 used `position_field.unrealised_pct` successfully; should have drafted |
| 17 | hdfc_when_nifty_drops | cross_symbol | **PASS** | – | Cross-symbol leaf: trigger on `pct_change(NIFTY,1) < -0.02`, action on HDFCBANK — exactly what the DSL should do |
| 18 | infy_rsi_and_trend | multi_condition | **PARTIAL** | P1 | Qty ask but no draft — the AND-leaf modelling is the diagnostic-worthy bit and we never see it; pattern-twin of #9, #10 |
| 19 | three_stock_basket_dip | basket | **PASS** | – | Compound NIFTY < -2% + `action.allocate_notional(symbols=[3], total_inr=30000)` — no legs dropped, no qty fake-out |
| 20 | reliance_supertrend_flip | supertrend | **PARTIAL** | P2 | Qty ask, no draft — supertrend is the diagnostic leg; if unsupported, system.md asks for an honest "supertrend isn't a daily leaf yet, try EMA flip" with nearest-alt, not a generic qty ping |
| 21 | sbin_vwap_reclaim | vwap | **PARTIAL** | P2 | Same: VWAP-intraday isn't a backtestable daily leaf; honest path is "VWAP intraday isn't a workflow leaf yet" + nearest-alt, not "how many shares?" |
| 22 | rbi_policy_bank_buy | event | **PASS** | – | `trigger.event(keywords=["RBI","MPC","rate cut","repo rate"], min_confidence=0.85)` + buy 10 SBIN — exactly the event-trigger shape |
| 23 | infy_earnings_reminder | event | **PASS** | – | Honest ask for the anchor date — calendar-API isn't wired, so this is the correct boundary not a cop-out |
| 24 | expiry_day_squareoff | event | **PASS** | – | `trigger.expiry_day(underlying=NIFTY)` + notify — correctly uses the post-Sep-2025 monthly trigger, no "every Thursday" claim |
| 25 | ipo_listing_sell_pop | event | **PASS** | – | Honest "name the IPO" ask + concrete promise to set the listing-day sell-half rule once named — not a dead end, a one-field-missing question |
| 26 | ltimindtree_oco_bracket | stop_target | **PASS** | – | `create_oco_order` tool + qty ask (qty truly absent in "I bought at 5800, target 6200, stop 5600") — right tool, legit ask |
| 27 | hinglish_tatasteel_dip | hinglish | **PASS** | – | Hinglish parsed cleanly: 5% dip buy 25 → 8% gain sell. 5-step card with fetch.portfolio + jinja-resolved qty for sell |
| 28 | news_sentiment_sell | boundary | **PARTIAL** | P1 | Did NOT name the boundary — "news-sentiment NLP isn't supported" should be explicit per the honesty probe; instead it weakly asked the user to "tell me whether you mean…" which obscures the actual limit |
| 29 | broker_auto_execute | boundary | **PASS** | – | Crisp boundary: "Pivot is register-not-execute under the SEBI framework" + offered to draft + 3-step card with `wait.approval` self-documenting — gold standard for this category |
| 30 | amend_qty_then_confirm | amend | **FAIL** | **P0** | Turn 0 correct qty-ask. Turn 1 amended draft with `quantity=25` correctly. **Turn 2 "yes looks good" re-drafted ANOTHER card instead of registering** — the confirm leg loops (re-emits the same workflow_draft_card) instead of activating it; no register/activate side-effect visible |

---

## What works well

1. **Schedule and SIP routing is dead-on.** `niftybees_920_buy`, `weekly_sip_goldbees`, `monthly_sip_nifty`, `infy_squareoff_325`, `infy_friday_profit` all picked the correct tool (`propose_workflow` vs `propose_scheduled_order` vs `create_sip`) and emitted correct crons and correct notional-vs-qty handling. The 9:20-AM-vs-₹920-limit disambiguation in `niftybees_920_buy` is the kind of thing weaker prompts blow up on.
2. **DSL compound-trigger building is mature on cross-symbol and pct-change shapes.** `hdfc_when_nifty_drops`, `three_stock_basket_dip`, `hinglish_tatasteel_dip`, `icici_above_sma200`, `coalindia_alert_only` all built clean entry trees with the right operator, the right leaf type (`price` vs `pct_change` vs `indicator`), and the correct primary_symbol — exactly the shapes the engine is supposed to flex on.
3. **Event-trigger coverage is real.** `rbi_policy_bank_buy` produced an honest keyword-event trigger with sensible defaults (`min_confidence=0.85`), `expiry_day_squareoff` correctly used `trigger.expiry_day` instead of the legacy "every Thursday" cron, and `ipo_listing_sell_pop` asked the *one* missing field (the IPO name) rather than guessing.
4. **Boundary honesty on `broker_auto_execute` is genuinely good.** "Pivot is register-not-execute under the SEBI framework" + a self-documenting `wait.approval` step with the limit verbatim in the summary is exactly the discipline the rubric wants.
5. **Hinglish parsed verbatim.** "5% gir jaye to 25 share kharido aur 8% upar jaye to bech do" → correct buy/sell card with `fetch.portfolio` resolving the sell qty — non-trivial parsing held up.
6. **Determinism / fast-path:** two turns (`tcs_rsi_oversold_buy`, `niftybees_920_buy`) bypassed the LLM entirely (15 ms / 9 ms, 0 LLM calls) and still produced correct cards — the example cache is paying off.

---

## What's broken — ranked failure list

### P0-1 · `titan_trailing_stop` — undisclosed live-trailing limitation (capability theatre)
- **Repro:** `say = "trail my stop loss 8% below the running high on TITAN"` → response: *"Drafted: trailing 8% stop-loss on TITAN, using the running high. Click Activate."* — no mention that live re-ratcheting isn't wired.
- **Root cause hypothesis:** the card itself is built correctly (`propose_holding_action`, `trailing=true`), but the chat layer's summary template for `propose_holding_action.set_stoploss(trailing=true)` doesn't pull the mandatory live-limitation disclaimer from system.md §1199-1203 ("the trailing ratchet is fully modeled in backtests; live, this registers the initial 8%-below stop today and live re-ratcheting is coming"). The LLM's prose summary collapsed the nuance.
- **Fix:** in the tool-result formatter for `propose_holding_action` (look in `_dsl_chat_tools.py` or `chat_service.py`'s post-tool summarizer), when `action_kind=='set_stoploss'` and `trailing=true`, **deterministically append** the live-ratcheting disclosure to whatever the LLM produces — don't trust the LLM to remember it. Also worth adding a `live_warnings: ["trailing-stop live re-ratcheting is coming"]` entry to the card's compact and rendering it in the FE chip.

### P0-2 · `amend_qty_then_confirm` turn 2 — "yes looks good" re-drafts instead of registering
- **Repro:** turn 0 asks qty → turn 1 user says "actually make it 25 shares" → draft 25-INFY/RSI<30 card (good). Turn 2 user says "yes looks good" → response re-emits the **same workflow_draft_card** with same tool call (`propose_threshold_order`) instead of registering / activating the draft.
- **Root cause hypothesis:** the chat layer doesn't recognise "yes looks good" / "confirm" / "go" as the *activate-prior-draft* intent and instead re-routes to draft-proposal. system.md §300, §907 cover the slot-typing for amendments but the *confirm-prior-draft* path appears to have no register-action hook. Suspect there's no `register_active_draft` tool exposed, or the router doesn't fire it on bare-affirmation tokens.
- **Fix:** in `chat_service.py` routing, add a deterministic short-circuit: if the prior assistant turn emitted a `workflow_draft_card` AND the user message is a bare-affirmation (`yes`, `looks good`, `confirm`, `go ahead`, `do it`, `register it`), call a `confirm_active_draft` / `register_draft` tool instead of re-running `propose_*`. The current loop wastes a full 2-LLM-call round and — more importantly — never actually arms the workflow, which is the user's whole goal.

### P1-3 · "ask qty without drafting" pattern — 6 sessions affected
- **Repro across:** `reliance_dip_profit_stop` (qty WAS in the prompt — slot-extraction bug), `hdfc_golden_cross`, `maruti_gap_down_buy`, `adani_volume_spike`, `infy_rsi_and_trend`, `reliance_supertrend_flip`, `sbin_vwap_reclaim` — that's **6/31 = 19%** of sessions where the diagnostic-worthy leg (RSI-and-trend tree, gap-down node, volume vs 20-DMA, supertrend, VWAP) is never built because the bot blocks on qty.
- The most damning one is `reliance_dip_profit_stop` — the user literally said *"qty 10"* in the prompt and was still asked. That's a slot-extraction regression, not policy.
- **Root cause hypothesis:** the qty-required guard in `propose_dsl_workflow` / `propose_workflow` fires *before* the LLM has a chance to draft a placeholder card. Slot extraction misses "qty 10" tokens in dense compound prompts. For the genuinely qty-less prompts (`hdfc_golden_cross` etc.), the right UX is **draft a card with qty=1 + clear "set your size" CTA**, so users can validate the trigger logic (which is the harder part) in parallel with picking a size.
- **Fix:** (a) in the qty-extractor (likely `tools.py` or `structured_builder.py`), add a trailing-`qty\s*\d+` regex pass on the raw user message so explicit qty-as-suffix is never missed. (b) Relax the qty guard from "block + ask" to "draft with qty=1 + warning chip + ask in the same turn" for *all* indicator/compound/gap/volume triggers — this lets users see the trigger and qty-ask in one breath instead of two round-trips.

### P1-4 · `wipro_macd_bullish` — semantic error in "bullish MACD crossover"
- **Repro:** card encodes the entry as `macd(WIPRO, 26) > 0` (a level, not a crossover). True "bullish MACD crossover" is `macd_line > signal_line` (a crossover of two MACD outputs).
- **Root cause hypothesis:** the DSL leaf catalog probably exposes only a scalar `macd` indicator (no `macd_signal` / `macd_histogram` sister leaves). The LLM did the next-most-faithful translation (`macd > 0`) and called it a day. The user gets a workable-but-wrong card and won't know unless they read the steps.
- **Fix:** extend the indicator catalog (in `llm_translate.py` / the DSL leaf registry) with `macd_line`, `macd_signal`, `macd_hist` outputs and a `crosses_above`/`crosses_below` operator across two indicator leaves; add a few-shot in the DSL translation prompt for "bullish MACD crossover" → `macd_line crosses_above macd_signal`. Until that lands, the response template should say *"modelled as MACD > 0 (level entry); true line-vs-signal crossover isn't wired yet"* — same honesty pattern as trailing-stop should follow.

### P1-5 · `news_sentiment_sell` — boundary not named explicitly
- **Repro:** instead of "news-sentiment NLP isn't supported", the response said *"I couldn't turn that into a saved automation because the sell rule for ADANIPORTS was incomplete"* — that's misleading; the rule isn't incomplete, the *capability* is missing.
- **Root cause hypothesis:** the LLM defaulted to the generic "incomplete-rule" template instead of the boundary template. system.md probably needs a few-shot or a hard-coded line in the unsupported-features table for "news sentiment / NLP-based exits" with the nearest alt (keyword-event trigger on the ticker).
- **Fix:** add a row to the system.md "Unsupported / out-of-scope" table: *"news sentiment / NLP sentiment exits → not wired. Nearest alt: `trigger.event(keywords=[...])` on the symbol's news"*. Compare to `broker_auto_execute` which got this right because the system prompt names "register-not-execute" explicitly.

### P1-6 · `bajfinance_drawdown_exit` — false-negative refusal
- **Repro:** *"buy 5 BAJFINANCE on RSI<35 and exit if it falls 5% from its peak after entry"* refused with *"the exit depends on the entry's peak price, which the current setup can't tie together cleanly"* — but `infy_friday_profit` and `hinglish_tatasteel_dip` BOTH used `position_field.unrealised_pct` / `position.drawdown_from_peak_pct`-shaped exits in the same snapshot.
- **Root cause hypothesis:** the model's mental model of "what exits we support" is inconsistent across prompts — the DSL exit_compound path with `position_field` IS the supported shape. Likely a missing few-shot specifically for "drawdown-from-peak" entry-bound exits.
- **Fix:** add a few-shot in `llm_translate.py` (or the system-prompt unsupported/supported table) mapping `"exits if it falls N% from its peak after entry"` → `trigger.exit_compound(position_field.drawdown_from_peak_pct >= N/100)`. The capability is there (§1455) — the model just doesn't reach for it.

### P2-7 · `coalindia_alert_only` — notify routing works but uses the long path
- **Repro:** built `propose_dsl_workflow` with `trigger.compound(crosses_above 420)` + `notify.message` — produces the right outcome but bypasses the dedicated `action_kind='notify_only'` macro path in system.md §413. The readback message *"COAL INDIA 420 upside ping fired — entry condition: price of COALINDIA crosses above 420"* is also clunkier than the macro's templated form.
- **Fix:** if the macro path produces cleaner UX, route to it; otherwise document that both paths are acceptable and remove the macro-specific instructions to reduce prompt noise.

### P2-8 · `tatamotors_52w_high` — over-clarifies on a clear ask
- **Repro:** *"buy TATAMOTORS when it breaks its 52 week high"* → asked *"52-week high or specific ₹?"* — the prompt is unambiguous; the user said 52-week high.
- **Fix:** draft with the rolling-252-bar-high default + offer "want to override with a fixed ₹ instead?" in the response. Don't gate the draft.

### P2-9 · `reliance_supertrend_flip` / `sbin_vwap_reclaim` — qty-ask on unsupported leg
- **Repro:** the diagnostic-worthy bit is whether supertrend / VWAP is a real DSL leaf; asking for qty first kicks the honest-boundary call down the road and burns a round-trip.
- **Fix:** check leaf-supportability *before* the qty guard. If unsupported → name the boundary + nearest alt. If supported → draft with qty=1 + qty ask.

---

## Cross-cutting patterns (ranked by impact)

1. **Capability-theatre risk in tool-result formatters.** TITAN trailing-stop is the canonical case: the *card* knows it's a known-limited capability (system.md §1199 spells it out), but the *response prose* doesn't pull the disclaimer. Whenever a workflow has `live_warnings`, that field must surface in the chat reply deterministically, not via LLM memory. This is the highest-leverage single fix in this snapshot — same class of fix would protect MACD crossover (§P1-4) and any future "backtest works, live partial" capability.
2. **Confirm-the-prior-draft is missing from the router.** The `amend_qty_then_confirm` failure is *not* a draft-quality bug, it's a workflow-lifecycle bug: there's no "register-active-draft" intent. This will affect every multi-turn build → tweak → activate flow, which is the actual happy path for a chat-first workflow product. Highest functional-impact pattern after #1.
3. **Qty-blocking-before-drafting hurts iteration loops.** 6/31 sessions never showed the user the trigger logic because the qty guard fired first. For an iteration UX where the user wants to inspect the trigger semantics *more* than the qty (which they can amend in one word), the right default is *draft with a sentinel qty + ask in parallel*, not *block and ask*.
4. **Slot-extraction loses inline qty.** `reliance_dip_profit_stop` shipped "qty 10" verbatim and got a qty ask anyway. There's a regex or NER gap in the prompt pre-parse — cheap to fix, high signal.
5. **Boundary-honesty quality varies by capability.** `broker_auto_execute` named SEBI verbatim (great). `news_sentiment_sell` was vague (bad). The pattern: capabilities explicitly enumerated in system.md get clean boundaries; capabilities *implicitly* unsupported (NLP, supertrend, intraday-VWAP) get either over-clarification or generic refusals. Adding 4-5 rows to the "unsupported" table would fix the bottom quartile.
6. **Input-token bloat at 60 k/turn.** System prompt + tool catalog is the dominant cost. Worth a separate audit pass to (a) trim per-turn-static instructions to a cached prefix, (b) collapse 4 propose_* tools into a unified shape — many of the qty-blocking false positives also stem from the tool-fan-out and overlapping guards across the 4 propose_* paths.

---

## Files referenced

- `pivot/backend/prompts/system.md` §412 (alert verb → notify_only), §1185-1203 (trailing live disclosure), §1455 (drawdown_from_peak_pct exit field), §300 / §907 (amendment slot-typing)
- `pivot/backend/agents/tools.py` (qty guard, slot extraction)
- `pivot/backend/workflows/dsl/llm_translate.py` (DSL leaf catalog — MACD signal/hist missing)
- `pivot/backend/agents/_dsl_chat_tools.py` / `chat_service.py` (tool-result formatter for live_warnings; confirm-active-draft router)
