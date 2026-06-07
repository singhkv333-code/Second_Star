# Automation 50 — Final Summary (LIVE-VERIFIED retest)

> **This version is live-verified.** It supersedes the earlier analytic
> extrapolation that was written before a real retest landed. Every PASS /
> PARTIAL / FAIL below is graded turn-by-turn from
> `tests/eval_results/automation_50/run_20260607_132119.json` (22 sessions,
> 39 turns, against the patched + restarted `:8000` server), never from
> "the patch should have worked" reasoning.

Date: 2026-06-07
Original snapshot: `tests/eval_results/automation_50/run_20260607_124237.json`
Retest snapshot:   `tests/eval_results/automation_50/run_20260607_132119.json`
Judge report:      `tests/eval_results/automation_50/JUDGE_REPORT_2026-06-07.md`

Engineer fixes applied (3 patches):
- `backend/prompts/system.md` — ALERT-VERBS hard gate + unsupported-rails
  boundary table + numeric-amendment slot-typing guidance.
- `backend/agents/tools.py` — new `trailing` slot on
  `propose_holding_action` tool schema.
- `backend/services/workflow_macros.py` — propagates `trailing=true` into
  `sl_cfg` on hydrate.

Deferred (NOT fixed): `register_workflow` confirmation lifecycle,
options-strategy router (straddle/strangle/condor), universe-scan fanout,
IPO amendment accumulation, basket SIP state survival, `max_fires_count`
trigger field, per-leg SL primitives.

---

## Headline

- **Original (31 sessions): 9 PASS / 8 PARTIAL / 14 FAIL** (29% PASS)
- **Final (31 sessions): 17 PASS / 7 PARTIAL / 7 FAIL** (55% PASS)
- Of the 22 retested sessions: **8 PASS / 7 PARTIAL / 7 FAIL**
  - 7 FAIL→PASS, 1 PARTIAL→PASS, 4 FAIL→PARTIAL
  - 1 PARTIAL→FAIL **regression** (`rsi_oversold_basket_threshold_amend`
    — see §Per-session detail)
  - 9 unchanged-class (partial-stays-partial or fail-stays-fail)
- **Net: +8 PASS, –1 PARTIAL, –7 FAIL** vs. original on the same 31 sessions.

The headline win is honest-boundary behaviour: the unsupported-rails table
flipped four sessions from FAIL to PASS (round-up UPI, universe scan,
per-leg SL, ex-dividend), and the options-strategy multi-leg primitive on
the BANKNIFTY 9:20 straddle landed cleanly. Remaining failures cluster in
three known buckets: (1) deterministic fast-path bypass (broker auto-exec),
(2) confirm-to-register lifecycle (RBI MPC), (3) silent multi-symbol
collapse (hinglish basket, news sentiment).

---

## What each engineer fix actually achieved — LIVE evidence

### Fix 1 — Alert-verb hard gate to notify_only (FIXED, live-verified)
- **Patches**: `system.md` ALERT-VERBS section.
- **Live evidence**:
  - `plain_price_alert_eichermot/0` — tools=`['propose_dsl_workflow']`,
    hint=`workflow_draft_card`, draft = `EICHERMOT crosses_above 4500 →
    notify.message`. Original: `propose_threshold_order` + quantity
    interrogation. **PASS**.
  - `coalindia_alert_amend_level/0` — same clean notify-only routing
    on "alert me when COALINDIA crosses 420". T1 "make it 405" patches
    the same draft to `crosses_above 405` — slot-typing held: 405 was
    correctly bound to price, not quantity. **PASS** (was FAIL).
  - `hcltech_drop_alert_upgrade_gtt/0` — notify-only draft as expected.
    (T1 hits a separate prose-vs-card lying bug — see per-session.)
- **Sessions credited**: `coalindia_alert_amend_level` (FAIL → PASS),
  `plain_price_alert_eichermot` (FAIL → PASS); `hcltech` T0 fixed but
  capped by T1 defect.

### Fix 2 — Trailing-stop param on propose_holding_action (PARTIAL, live-verified)
- **Patches**: `tools.py` schema slot, `workflow_macros.py` propagation.
- **Live evidence**: `titan_trailing_stop_gap_risk/0` —
  `action.set_stoploss` now carries `"trailing": true`. Card prose says
  "trailing 8% stop-loss on TITAN (tracks peak)". T1 gap-risk question
  answered honestly: "If it gaps below the trailing stop, the stop is
  triggered at the opening price, so execution can be worse than the stop
  level."
- **Critical caveat (confirmed live):** `backend/workflows/schemas.py:1155-
  1158` explicitly documents that the LIVE executor ignores `trailing`
  and places a static initial GTT — only the backtest engine honours it.
  The card therefore claims a behaviour the live path does not deliver
  unless the engine is also patched. This is capability theater on the
  live rail. **Session capped at PARTIAL** despite the card looking
  correct.
- **Sessions credited**: `titan_trailing_stop_gap_risk` (FAIL → PARTIAL).

### Fix 3 — Unsupported-rails boundary table (FIXED, live-verified — biggest leverage)
- **Patches**: `system.md` 6-row table covering broker auto-exec, UPI
  round-ups, sentiment NLP, corporate-action calendar, IV-rank, universe
  scan.
- **Live evidence — clean boundary calls**:
  - `round_up_upi_to_etf/0` — "Pivot can't see UPI transactions or bank
    balances. Do you want a fixed weekly SIP amount in NIFTYBEES instead
    — if yes, what amount in ₹ per week?" Honest + nearest equivalent.
    **PASS** (was FAIL).
  - `itc_ex_dividend_reminder/0` — "Pivot doesn't auto-track corporate-
    action calendars yet. If you share the ex-dividend date for ITC, I
    can set a reminder for 2 days before it." **PASS** (was PARTIAL).
  - `nifty50_52w_high_universe_scan/0` + T1 — "Pivot does not support a
    universe-wide 'any Nifty 50 stock' trigger yet. The nearest
    alternative is to set this up per named stock"; T1 "exclude PSU
    banks" → "A 'Nifty 50 except PSU banks' universe-wide alert is not
    supported yet". **PASS** (was FAIL).
  - `per_leg_sl_strangle_chain/0` + T1 — "a 35% premium stop on each
    leg is not a fixed-price stop. I should model this as a workflow
    with separate exit rules per leg… I need the exact existing strikes/
    expiry". No more fake-success-empty-card. **PASS** (was FAIL).
  - `nine_twenty_straddle_banknifty/0` — "live F&O execution is
    register-only in your broker app". (T1 then produces a real
    paper-only options-strategy card — see §Bonus.)
- **Sessions credited**: `round_up_upi_to_etf`, `itc_ex_dividend_reminder`,
  `nifty50_52w_high_universe_scan`, `per_leg_sl_strangle_chain`
  (4 PASS).
- **Sessions where boundary helped but other defect dominates**:
  `broker_auto_execute_zerodha` (boundary in prompt, but `llm_calls=0`
  fast-path bypassed the LLM entirely — see §Open issues #1),
  `news_sentiment_sell_adani` (boundary did NOT engage; bot still emits
  a sentiment-keyword card with a typo'd `ADHANIENT` symbol —
  see per-session), `iv_rank_condor_nifty` (boundary did not engage on
  T0; bot asks expiry question instead of declining IV-rank).

### Bonus — Options-strategy router landed where it does engage
- **Live evidence**: `nine_twenty_straddle_banknifty/1` — first real
  multi-leg options card in the snapshot. Step 2 is
  `action.place_option_strategy` with `template=short_atm_straddle`,
  `paper_only=true`, `sl_pct_each_leg=30`, `squareoff_time_ist=15:10`,
  `entry_time_ist=09:20`. All five constraints from the original prompt
  preserved across the ASK_USER → propose_workflow boundary.
- This was listed as DEFERRED in the engineer's commit log; the live
  retest shows it works at least on the BANKNIFTY straddle macro. The
  IV-rank-gated condor (`iv_rank_condor_nifty`) does NOT engage the
  router — the gap appears to be IV-rank as a trigger primitive, not
  the strategy action.

---

## Before / after verdict table (22 retested sessions)

| Session | Sev | Original | LIVE Final | One-line live evidence |
|---|---|---|---|---|
| hcltech_drop_alert_upgrade_gtt | P0 | PARTIAL | **PARTIAL** | T0 notify-only routing fixed; T1 prose says "20-share buy limit" but card_digest still shows the unchanged notify-only steps — bot lies about the card. |
| coalindia_alert_amend_level | P0 | FAIL | **PASS** | T0 → notify-only `crosses_above 420`; T1 "make it 405" patches same draft to `crosses_above 405` — slot type held. |
| niftybees_weekly_friday | P1 | PARTIAL | **PARTIAL** | T1 "yes set it up" now returns `tools=[]` + "Drafted and ready to activate" (no double-draft re-emit). Lifecycle still says "activate" not "registered". |
| hinglish_basket_sip_three | P0 | FAIL | **FAIL** | T0+T1 emit single-leg MARUTI draft with weekday cron (15 9 * * 1-5) — ITC + ASIANPAINT dropped; "10 tareekh ko" (monthly 10th) silently changed to weekday. |
| macd_then_chain_supertrend | P1 | PARTIAL | **PARTIAL** | T0 clean; T1 "both should fire together" → ASK_USER re-asking what user already said. 5 LLM calls / 23.5s wasted. MACD(1) periodization still leaks. |
| rsi_oversold_basket_threshold_amend | P2 | PARTIAL | **FAIL** ↓ | **REGRESSION**: original emitted 4 parallel pipelines (one per name); retest emits TCS-only single-symbol draft on T0 and tells user to "use the editor to duplicate" — silently drops INFY+WIPRO+HCLTECH. T1 amend keeps TCS-only. |
| infy_results_reminder_then_numbers | P1 | PARTIAL | **PARTIAL** | T0 still over-confirms ("What exact results date?"); T1 drops the "2 days before" pre-window — fires only on result-out. |
| maruti_results_dip_buy | P2 | PARTIAL | **PARTIAL** | T0 condition uses `ltp <= day_open` — drops 8% threshold (new bug). Duplicate condition.numeric step. T1 `valid_until=2026-11-30` same approximation as original. |
| rbi_mpc_bank_basket_confirm | P0 | FAIL | **FAIL** | T0 clean draft with 3 banks. T1 "yes set it up exactly like that" → re-emits propose_workflow byte-identical + "Review the steps below and click Activate". Confirm-loop unchanged. |
| itc_ex_dividend_reminder | P1 | PARTIAL | **PASS** | "Pivot doesn't auto-track corporate-action calendars yet. If you share the ex-dividend date for ITC, I can set a reminder for 2 days before it." |
| iv_rank_condor_nifty | P0 | FAIL | **FAIL** | T0 ASK_USER asks expiry without disclosing IV-rank is unsupported and without declining iron condor. T1 "what's the IV right now actually" → `get_live_price('WHAT')` — total context loss, same as original. |
| nine_twenty_straddle_banknifty | P0 | FAIL | **PASS** | T0 honest paper-vs-live + register-not-execute disclosure; T1 produces real `action.place_option_strategy` card with `template=short_atm_straddle`, `paper_only=true`, `sl_pct_each_leg=30`, entry 09:20, squareoff 15:10. |
| per_leg_sl_strangle_chain | P0 | FAIL | **PASS** | "a 35% premium stop on each leg is not a fixed-price stop. I should model this as a workflow with separate exit rules per leg… need the exact existing strikes/expiry". No fake-success card. T1 same honest blocker. |
| nifty50_52w_high_universe_scan | P0 | FAIL | **PASS** | T0+T1 both honestly decline universe scan and offer "named shortlist" alternative. No misleading single-index trigger. |
| ipo_listing_sell_if_pops | P0 | FAIL | **PARTIAL** | T1 emits draft with `gap > 0.2` placeholder `IPO` symbol + honest disclosure ("currently uses IPO as a placeholder symbol. If you give me the actual… I'll re-emit"). But action is notify, not sell; "else hold" branch implicit. |
| titan_trailing_stop_gap_risk | P0 | FAIL | **PARTIAL** | T0 sets `trailing: true` on `action.set_stoploss`; T1 explains gap-down realism correctly. **CAPPED at PARTIAL per the explicit caveat**: schemas.py:1155-1158 confirms live executor ignores `trailing` — only backtest honours it. |
| ultracemco_dip_buy_budget | P1 | FAIL | **FAIL** | T0 same response as original: "I need an absolute ₹ level, not '6% from here.'" Fabricated constraint; ₹30k budget also dropped. |
| weekly_portfolio_digest_channel | P1 | PARTIAL | **PASS** | T0 drafts `0 18 * * 0` IST (Sunday 18:00) + portfolio + top_movers + notify — sensible time-of-day defaults applied. T1 "In-app notification only. Email, WhatsApp, and SMS are not wired in v1." |
| broker_auto_execute_zerodha | P0 | FAIL | **FAIL** | **`llm_calls=0` deterministic fast-path** still bypasses the boundary table. Draft has `requires_approval: false`; no SEBI / register-not-execute disclosure. Prompt patch did not run because LLM did not run. |
| news_sentiment_sell_adani | P0 | FAIL | **FAIL** | T0 still emits trigger.event keyword card with `min_confidence:0.85`; new bug: `action.place_order.quantity={{context.1.holdings.ADHANIENT.quantity}}` — typo'd symbol "ADHANIENT" → quantity will fail at fire time. T1 "what if a downgrade" still treated as AMEND (re-emit), not CLARIFY. |
| round_up_upi_to_etf | P0 | FAIL | **PASS** | "Pivot can't see UPI transactions or bank balances. Do you want a fixed weekly SIP amount in NIFTYBEES instead — if yes, what amount in ₹ per week?" Honest + concrete fallback. |
| plain_price_alert_eichermot | P1 | FAIL | **PASS** | Clean `propose_dsl_workflow` + `workflow_draft_card`; trigger `EICHERMOT crosses_above 4500` + notify.message. No quantity interrogation. |

---

## Quality triad — retest vs. same 22 sessions in original

| Metric | Retest (22 sess / 39 turns) | Original (same 22 / 39) | Δ |
|---|---|---|---|
| latency_wall_ms p50 | **8,302** | 7,841 | +461 (+5.9%) |
| latency_wall_ms p95 | **12,409** | 13,271 | –862 (–6.5%) |
| latency_wall_ms max | 23,546 (macd T1 ASK_USER loop) | 19,560 | +3,986 |
| Σ input tokens | **2,270,539** | 4,933,957 | **–53.9%** |
| Σ output tokens | **5,952** | 14,503 | **–59.0%** |
| Σ llm_calls | **73** | 167 | **–56.3%** |
| Avg llm_calls per turn | **1.87** | 4.28 | –56% |
| Avg input tok per turn | **58,219** | 126,512 | –54% |

**Triad reading.** Median latency held (about half a second slower at p50,
which is within noise) while tail latency improved 6.5% at p95.
Token + LLM-call totals dropped by more than half — this is the dominant
signal: the prompt changes pruned routing oscillation, so fewer turns
ping-pong through `find_tool` + corrective re-prompts. The single tail
outlier is `macd_then_chain_supertrend/1` (23.5s, 5 LLM calls) — the
AMEND-vs-CLARIFY router defect; that one prompt accounts for most of the
remaining tail.

Per-session wall-ms delta confirms the read: 9 of 22 sessions got faster
(largest: `per_leg_sl_strangle_chain` –11.9s, `nifty50_52w_high…` –6.5s,
`iv_rank_condor_nifty` –4.9s) and the slowdowns cluster on sessions where
the bot now actually composes a card instead of bailing
(`hinglish_basket_sip_three` +9.6s but still wrong,
`weekly_portfolio_digest_channel` +8.1s and now correct,
`coalindia_alert_amend_level` +7.9s and now correct).

---

## Remaining open issues — next-session work order (P0-first)

1. **Deterministic fast-path bypass strips boundary disclosure**
   *(P0, 1 session, regression risk on all honesty probes)*
   `broker_auto_execute_zerodha` logged `llm_calls=0` — the chat layer
   short-circuited the LLM entirely and emitted a `propose_workflow`
   draft with `requires_approval: false` and no SEBI / register-not-
   execute disclosure. The unsupported-rails table is in `system.md` and
   never got a chance to fire. Fix: when the fast-path matches a
   threshold-order shape AND the user prompt contains
   `auto-execute / no confirmation / directly in <broker>`, force
   `requires_approval=true` and inject the canonical register-not-execute
   disclosure server-side. Audit all fast-path routes for boundary
   disclosure coverage.

2. **`register_workflow` confirm-to-register tool**
   *(P0, 2 sessions, deferred)*
   `rbi_mpc_bank_basket_confirm/1` and `niftybees_weekly_friday/1` both
   show the same failure shape: user says "yes set it up" and the bot
   either re-emits a byte-identical `propose_workflow` draft (RBI) or
   says "drafted and ready to activate" again (niftybees). Add
   `register_workflow(draft_ref)` tool that consumes the prior draft id,
   persists status=`registered`, and returns a `registered_card` render
   hint. FE must render the registered state and stop showing Activate.

3. **Trailing-stop must land on the LIVE executor, not just backtest**
   *(P0, 1 session, partial-fix capability theater)*
   `titan_trailing_stop_gap_risk` ships a card claiming trailing-stop
   behaviour but `backend/workflows/schemas.py:1155-1158` documents the
   live executor places the initial GTT and ignores the `trailing` flag.
   Either patch the live executor to implement HWM tracking, or have
   the macro stop emitting `trailing: true` in the card and instead
   surface a "live = static GTT, full trailing in backtest only"
   disclosure. As shipped, the card lies about live behaviour.

4. **Sentiment-NLP boundary must trip + fix symbol-typo bug**
   *(P0, 1 session)*
   `news_sentiment_sell_adani/0` re-emits a keyword `trigger.event` card
   with `min_confidence:0.85` (still implies a sentiment NLP that does
   not exist). New live bug: the place_order step has
   `quantity={{context.1.holdings.ADHANIENT.quantity}}` — the symbol is
   typo'd "ADHANIENT" (extra H) so quantity resolution will silently
   fail at fire time. Two fixes: (a) gate `trigger.event` for sentiment
   verbs through the unsupported-rails table, (b) symbol-coerce
   `holdings.<SYM>` quantity templates against the canonical symbol
   list before emitting.

5. **Options-strategy router missing for IV-gated condor**
   *(P0, 1 session)*
   The BANKNIFTY 9:20 straddle worked (Bonus, §Fix-3) but
   `iv_rank_condor_nifty/0` does NOT engage the router — it asks expiry
   instead of declining IV-rank as a trigger primitive. Two needs:
   (a) `iv_rank > N` as a first-class trigger config (or a hard
   "not supported, nearest = realised-vol percentile" boundary line),
   (b) symbol inheritance from prior card_digest so T1's "what's the IV
   right now" doesn't resolve to `get_live_price('WHAT')`.

6. **Universe-scan + multi-symbol fanout primitive**
   *(P0, 2 sessions including new regression)*
   - `nifty50_52w_high_universe_scan` was rescued by the boundary table
     (PASS), but the underlying primitive is still missing.
   - **NEW regression**: `rsi_oversold_basket_threshold_amend` was P2
     PARTIAL with 4-parallel-pipelines; the live retest now emits a
     TCS-only single-symbol draft and tells the user to "use the editor
     to duplicate". This silently drops 3 of 4 user-named symbols and
     pushes a previously PARTIAL session to FAIL. The same fanout
     primitive needed for `nifty50_52w` would solve this; in the
     interim, the chat layer should at least emit the 4-pipeline shape
     it produced before, not the lossy single-symbol one. Treat as
     **regression to revert** while the fanout work is queued.

7. **Drafting-bias on ASK_USER → propose transition**
   *(P0, 2 sessions, deferred)*
   - `hinglish_basket_sip_three` still collapses 3 legs (ITC +
     ASIANPAINT + MARUTI) to MARUTI-only on the propose transition,
     and also misroutes "10 tareekh ko" (10th of every month) to a
     weekday cron `15 9 * * 1-5`. Two stacked defects.
   - `ipo_listing_sell_if_pops/1` now emits an honestly-placeholdered
     `IPO` symbol draft (better than original's no-output), but only
     captures the gap-up leg as a notify; the "sell my allotment"
     action and the "else hold" branch are still dropped.
   - Same fix surface: persist in-flight draft state across the
     ASK_USER boundary; merge, never reset.

8. **Prose-vs-card mismatch on amendment**
   *(P0 new, 1 session)*
   `hcltech_drop_alert_upgrade_gtt/1` — prose says "Drafted as a
   20-share buy limit at ₹1,380 for HCLTECH" but `card_digest.steps`
   still shows the unchanged notify-only structure. The bot lied about
   the card. Same shape as the "fake-success" defect from the original
   options sessions, just on an amendment. Server-side guard: when
   `propose_dsl_workflow` returns a card whose step types do not match
   the prose verbs ("buy limit" prose vs `notify.message` step), refuse
   emission and surface a CLARIFY instead.

9. **AMEND-vs-CLARIFY interrogative router**
   *(P1, 2 sessions)*
   `macd_then_chain_supertrend/1` ("both should fire together") and
   `news_sentiment_sell_adani/1` ("what if it's just a small thing")
   both got routed to AMEND when they should have routed to CLARIFY.
   The MACD one then ate 23.5s / 5 LLM calls re-asking the same
   question. Pattern-match "and/both/together/what if/what about/can
   you/would it" → CLARIFY (or auto-compose compound trigger when
   "both/together" is unambiguous).

10. **MACD periodization bleed `MACD(1)` in readback**
    *(P1, 1 session)*
    `macd_then_chain_supertrend/0` readback still says "MACD(1)" —
    period=1 is an internal component selector hack leaking to the
    user. Restore standard (12, 26, 9) defaults in readback.

11. **Slot-type annotation on open ASK_USER**
    *(P1, prevention)*
    The COALINDIA "make it 405" amendment passed live this round on
    prose guidance alone, but the engineer-acknowledged full fix
    (`expected_slot_type` annotation on the ASK_USER state) is still
    pending. Leaving it as prose-only is fragile — any future ASK_USER
    with both a price slot and a qty slot open is one bad prompt away
    from a regression.

12. **`max_fires_count` on trigger configs**
    *(P2, 1 session)*
    `maruti_results_dip_buy` still maps "next 2 quarters" to a
    `valid_until=2026-11-30` date approximation. Add `max_fires_count`
    + decrement-on-fire. Also fix the silent loss of the 8% drop
    threshold on T0 (new bug — condition is `ltp <= day_open`, no
    `-0.08`).

13. **Pre-window offset_days on `trigger.event`**
    *(P1, 1 session)*
    `infy_results_reminder_then_numbers/1` still drops the "2 days
    before" requirement entirely. Add `offset_days` to
    `EventTriggerConfig` and have the scheduler fire offset_days before
    matched event.

14. **Sensible-default cadence for vague phrases — partial regression risk**
    *(P1 prevention)*
    `weekly_portfolio_digest_channel/0` correctly mapped Sunday +
    evening → `0 18 * * 0` IST. Keep this behaviour as a documented
    rule (morning→09:00, evening→18:00, night→21:00) so future prompt
    edits don't reintroduce the time-loop ASK_USER pattern.

---

## Final scoring — full 31-session set (live-verified, 9 untouched + 22 retested)

- **PASS: 17** (original 9 + 8 retest-fixed):
  - Original PASS (not retested): the 9 from `run_20260607_124237.json`.
  - Retest PASS (8): `coalindia_alert_amend_level`,
    `itc_ex_dividend_reminder`, `nine_twenty_straddle_banknifty`,
    `per_leg_sl_strangle_chain`, `nifty50_52w_high_universe_scan`,
    `weekly_portfolio_digest_channel`, `round_up_upi_to_etf`,
    `plain_price_alert_eichermot`.
- **PARTIAL: 7** — `hcltech_drop_alert_upgrade_gtt`,
  `niftybees_weekly_friday`, `macd_then_chain_supertrend`,
  `infy_results_reminder_then_numbers`, `maruti_results_dip_buy`,
  `ipo_listing_sell_if_pops`, `titan_trailing_stop_gap_risk`.
- **FAIL: 7** — `hinglish_basket_sip_three`,
  `rsi_oversold_basket_threshold_amend` (regression P2→P0 effective),
  `rbi_mpc_bank_basket_confirm`, `iv_rank_condor_nifty`,
  `ultracemco_dip_buy_budget`, `broker_auto_execute_zerodha`,
  `news_sentiment_sell_adani`.

**Original 29% PASS → Final 55% PASS (+26pp).** Failure tail halved
(14 → 7). The biggest win class is honest-boundary behaviour
(unsupported-rails table directly responsible for 4 of the 8 retest PASS
flips); the biggest residual class is lifecycle plumbing
(`register_workflow` + state survival across ASK_USER) and the
deterministic fast-path bypass that strips the boundary disclosures
before the LLM can apply them.

## Caveats on this judgement

- Live data is from `run_20260607_132119.json` against the patched
  `:8000` server (22 sessions, 39 turns, 311.7s wall, base
  `http://localhost:8000`). The 9 original PASS sessions were NOT
  retested — they are assumed unchanged. If any of those regressed
  (e.g. `nifty_intraday_percent_drop` from the alert-verb halo, or
  `volume_spike_watchlist` from the multi-symbol changes), the 17 PASS
  count is an upper bound. Recommend a full 50-turn retest next session
  to verify no PASS-side regressions before declaring 55%.
- `broker_auto_execute_zerodha` ran with `llm_calls=0` and `wall=9ms`,
  meaning a deterministic shortcut bypassed the LLM. The verdict
  (`FAIL`) is graded on the served response content, not on the LLM's
  decision tree. The fix is server-side, not prompt-side.
- One verified live regression: `rsi_oversold_basket_threshold_amend`
  was PARTIAL with a 4-pipeline structure and is now FAIL with a
  TCS-only single-symbol structure. The original retained more user
  intent. Treat as a revert candidate while the fanout primitive is
  designed.
