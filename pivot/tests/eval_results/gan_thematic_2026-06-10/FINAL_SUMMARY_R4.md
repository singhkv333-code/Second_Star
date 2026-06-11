# GAN Round 4 — FINAL SYNTHESIS (thematic / vague / context / amend)

- **Date:** 2026-06-11 (Kite LIVE this round)
- **Baseline snapshot:** `run_20260610_215912.json` (18 sessions, 32 turns)
- **After snapshot:** `run_20260611_114421.json` (same 18 sessions/32 turns, post-fix, server restarted)
- **Gold:** `GOLD/{thematic-thesis,vague,single-session-context,follow-up-amend}.md`
- **Method:** gold-anchored discriminator panel per angle, every verdict adversarially verified against the
  actual transcript + card_digest; 7 discriminator calls were overturned/re-scored on verification
  (vix B, the_other_one A+B, bhartiartl B, cheaper_one B, hal B, swap B). Numbers below are the **corrected** panel.

---

## (1) QUALITY — Angle A & B, before → after

### Headline (18 sessions × 2 angles)

| Angle | Before P/Pa/F | Before mean | After P/Pa/F | After mean | Δ mean |
|---|---|---:|---|---:|---:|
| **A — execution correctness** | 2 / 3 / 13 | **3.83** | 4 / 11 / 3 | **5.94** | **+2.11** |
| **B — output quality** | 4 / 2 / 12 | **4.03** | 4 / 9 / 5 | **5.78** | **+1.75** |

Angle-A FAILs collapsed **13 → 3** (the keystone thematic decode-and-propose path landed: every thematic/vague
prompt that used to punt or dead-end now ships a real basket/SIP card on turn 1). Angle-B FAILs **12 → 5**.
The dominant residual on BOTH angles is now a single budget bug, not knowledge: the **compact-post-macro
250-token cap** (`chat_service.py:305, 5418-5422`) guillotines the analysis-class prose hop once
`propose_workflow` fires, severing the gold's back-half (confirmation/invalidation triggers, caveat, the one
sharpening question) on 5 of 8 thematic text-rich turns.

### Per-class

| Class | n | A before (P/Pa/F, mean) | A after | B before | B after |
|---|---:|---|---|---|---|
| thematic-thesis | 7 | 0/2/5, 3.29 | **1/5/1, 5.57** | 1/0/6, 3.29 | **0/4/3, 4.71** |
| vague | 4 | 0/0/4, 2.50 | **0/4/0, 5.75** | 0/0/4, 2.25 | **0/4/0, 5.25** |
| single-session-context | 4 | 0/1/3, 4.25 | **2/1/1, 6.75** | 1/1/2, 4.25 | **2/1/1, 7.00** |
| follow-up-amend | 3 | 2/0/1, 6.33 | 1/1/1, 6.00 (↓) | 2/1/0, 7.83 | 2/0/1, 7.33 (↓) |

The two new classes (thematic, vague) were the build target and moved the most (vague was 0-for-8 across both
angles at baseline; now 0 FAILs in 8 angle-instances). Follow-up-amend gave back a little — two genuine
regressions, flagged below.

### Per-session (corrected verdicts, before → after)

| Session | A before | A after | B before | B after |
|---|---|---|---|---|
| monsoon_deficit_single_turn | FAIL 3 | **PARTIAL 6** | FAIL 3 | **PARTIAL 5** |
| india_pak_conflict_refusal | FAIL 1 | **PARTIAL 6** | FAIL 1 | **PARTIAL 5** |
| rupee_depreciation_hinglish | FAIL 2 | **PARTIAL 6** | FAIL 1 | **PARTIAL 4** |
| rbi_rate_cut_cycle | PARTIAL 5 | PARTIAL 5 | FAIL 4 | FAIL 4 |
| crude_spike_hedge_probe | FAIL 3 | FAIL 3 | FAIL 4 | FAIL 3 |
| vix_gated_defence_agent_probe | PARTIAL 7 | **PASS 8** | PASS 8 | PARTIAL 8 |
| el_nino_basket_surgery | FAIL 2 | **PARTIAL 5** | FAIL 2 | FAIL 4 (↑) |
| first_salary_where_to_start | FAIL 3 | **PARTIAL 7** | FAIL 3 | **PARTIAL 5** |
| idle_two_lakh_scared | FAIL 2 | **PARTIAL 6** | FAIL 2 | **PARTIAL 6** |
| one_percent_a_day | FAIL 2 | **PARTIAL 5** | FAIL 1 | **PARTIAL 5** |
| what_to_buy_this_week | FAIL 3 | **PARTIAL 5** | FAIL 3 | **PARTIAL 5** |
| lt_draft_backtest_amend_chain | FAIL 3 | FAIL 3 | FAIL 3 | FAIL 3 |
| pronoun_after_analysis_bhartiartl | FAIL 5 | **PASS 8** | PARTIAL 4 | **PASS 8** |
| cheaper_one_sip_axis_kotak | PARTIAL 8 | PARTIAL 7 | PASS 8 | PARTIAL 8 |
| the_other_one_pharma_dipbuy | FAIL 1 | **PASS 9** | FAIL 2 | **PASS 9** |
| hal_compound_arithmetic_amend | PASS 8 | PASS 8 | PASS 8.5 | PASS 9 |
| goldbees_register_then_revert | FAIL 3 | FAIL 4 | PARTIAL 5.5 | **FAIL 4 (REGRESSION)** |
| swap_symbol_amount_hinglish | PASS 8 | **PARTIAL 6 (REGRESSION)** | PASS 9.5 | PASS 9 |

### Marquee fix verification — actual replies, before vs after

**1. War/conflict refusal calibration** (`india_pak_conflict_refusal_calibration` — the hardest historical failure)

BEFORE (FAIL 1/1 — the exact 0-score bare-binary punt the gold targets; tool `ASK_USER`, hint `ask_user`, 48 out-tokens):
> "What exact action should the agent take if that event happens — buy, sell, hedge, or alert you, and on which NSE symbol or basket?"

AFTER (PARTIAL 6/5 — tools `propose_workflow` + `suggest_option_strategy`, hint `workflow_draft_card`; decodes and proposes with ZERO moralising and no self-contradiction):
> "## Thesis decode — A real shooting war typically drives a defence-order re-rating, a gold bid, and a risk-off washout in aviation, travel, and high-beta financials. The live playbook is a **defence + gold basket**... | Winner | HAL | Fighter/helicopter prime; emergency procurement and budget re-rating. | ... | Winner | GOLDBEES | Safe-haven bid; also offsets INR/risk-off stress. | ..."
> Card: `allocate_basket` ₹1,00,000 — HAL/BEL/BDL/MAZDOCK/GOLDBEES 20% each, NSE, long, register-not-execute.

Gold criterion 5 (refusal calibration) is now genuinely cleared. Residual: text truncates mid-table at "| Avoid | BAJFIN" (the 250-token compact bug), and the option overlay is a long_strangle where the gold prefers a protective NIFTY put.

**2. Thematic turn-1 basket** (`monsoon_deficit_single_turn`)

BEFORE (FAIL 3/3 — routed to `suggest_option_strategy`, shipped a NIFTY bear-put-spread card; gold's irrigation basket and winners/losers table entirely absent; staples/fertiliser mis-framed as plays when gold marks them losers):
> "Your portfolio already has a mild tilt to quality defensives... The cleanest options card here is a **NIFTY bear put spread**: defined risk, about **₹7,434** max loss per lot..."

AFTER (PARTIAL 6/5 — `propose_workflow`, `workflow_draft_card` with SHAKTIPUMP/KSB/KIRLOSBROS/JISLJALEQS @25% each, ₹1L):
> "## Thesis decode — A below-normal monsoon can pressure rural incomes, kharif sowing, tractors, rural FMCG, and fertiliser demand, while boosting irrigation, pumps, and micro-irrigation spending... | Winner | SHAKTIPUMP | Solar/agri pumps are a direct beneficiary... | Avoid | M&M | Tractor volumes are sensitive to weaker rural sentiment... |"

Correct intent, correct tool, correct widget, both-sides table. Residual: truncated after the HINDUNILVR row — IMD %-of-LPA confirmation/invalidation, caveat, and the sharpening question never render.

**3. Vague onboarding** (`first_salary_where_to_start`)

BEFORE (FAIL 3/3 — zero tools, zero widget, 71-word blurb with out-of-scope emergency-fund/debt advice):
> "Start with a simple order: build a 3–6 month emergency fund first, then automate a monthly SIP... If you want the practical version, I can help you set a beginner plan based on your monthly take-home..."

AFTER (PARTIAL 7/5 — `propose_scheduled_order`, `workflow_draft_card`, value-first 3-path table + a tappable prefilled NIFTYBEES SIP card):
> "You do not need to find the 'perfect' first investment... ## 3 starter paths — | Index SIP | Broad market exposure, low effort | NIFTYBEES, ₹5,000/month | | Rules-based entry | Buy on a clear technical rule | INFY when RSI(14) < 30, exit at +8% / -4% | | Quality screen | ROE > 15% and low debt... | — I have also drafted a... NIFTYBEES plan for ₹5,000..."

Residual (new, found this round): the card cron is `15 9 * * 1-5` (every weekday ≈ ₹105k/month) while the prose says ₹5,000/**month** — a text↔card cadence contradiction repeated across all three SIP cards. Gold default is monthly, 1st trading day.

---

## (2) IMPROVEMENTS — what shipped this round

### Track F (fixes)

| # | Fix | Effect verified in after-run |
|---|---|---|
| F1 | Thematic bare-binary punt + conflict over-refusal carve-out | india_pak: ask_user punt → decode-and-propose, zero moralising |
| F2/C2 | Vague onboarding value-first prefilled SIP card + 3-path table | all 4 vague sessions: no-widget FAIL → turn-1 card + table |
| F3 | Backtest-verb-first routing (`backtest that`) | lt T2 now routes to `backtest_dsl_tree` (engine still returns 0 trades — F10 open) |
| F4 | Idle-cash scope inversion → scope-honesty + phased SIP | idle_two_lakh: FDs named out-of-scope, paper-mode offered, risk question asked |
| F5/C4 | Unrealistic-return decode (1%/day) → math refutation + backtest + SIP fallback | one_percent: 22-word ask_user menu → >3,600%/yr refuted + real RELIANCE backtest chart |
| F6 | Dip-buy rupee-sizing ask_user punt | bhartiartl T3 + the_other_one T2: symbol+₹ carried in ONE turn, 0 re-asks |
| F7 | create_sip draft lifecycle dead-end → propose_scheduled_order for recurring buys | goldbees now registers/amends (new duplicate-draft defect surfaced — see regressions) |
| F9/C5 | INDIAVIX wired as a real quote/trigger source (`yfinance_service.py:29`) | vix probe → PASS 8: two-branch armed agent on a genuinely live trigger, honesty gap closed |
| F11 | `_llm_unavailable` retry + intent-echoing degraded reply | the_other_one: 50s dead turn at baseline → full PASS-9 session after |
| F12 | Amend readback ₹-recompute + human-date contract (system.md) | contract landed; readbacks still thin in practice (open) |
| F13 | Harness logic_card blind spot (`auto_batch_eval.py`) | SIP/dip-buy cards no longer read as "empty" to judges |

### Track C (capabilities BUILT)

- **C1 KEYSTONE — thematic decode-and-propose path**: `thematic_map.py` seed scenarios (monsoon, conflict,
  rupee, rate-cut, el-nino, crude, VIX…) + `_apply_scenario_routing` shared by `handle()`/`handle_stream()`
  + system.md contract (thesis decode → winners/losers table → basket card → confirm/invalidate → caveat →
  one question). This is why 5 of 7 thematic sessions now carry a faithful basket card. 40/40 new
  `tests/test_thematic_map.py` pass.
- **C2** value-first vague onboarding (3-path table + prefilled SIP card).
- **C4** unrealistic-returns decode (refute + backtest + SIP fallback).
- **C5** INDIAVIX as a real trigger/quote source.
- **C3 (partial)** composite thematic basket + option overlay in one turn — contract in place, crude_spike
  still mis-routes to portfolio-analysis (the one thematic FAIL left).
- **C6 (partial)** prose-basket reconstruction on amend — turn-0 card removes most of the cascade; the
  swap/replace amend op is contracted but not engine-enforced (el_nino "replace it" dropped the swap-in).

### Deferred (infra-gated)

- **C7** mutate a registered workflow from chat (needs mutation/pause-and-replace API) — this is the goldbees FAIL.
- **C8** undo / draft-state version history (per-conversation draft version stack).
- **C9** macro-data trigger sources USDINR/IMD rainfall (no feed; honest-substitution enforced in contract).
- **C10** multi-leg SIP in one card NIFTYBEES+GOLDBEES (card schema/engine change).
- **F10** backtest empty-metrics/flat-curve engine fix (compound pct_change trigger returns 0 trades on LT;
  lives under workflows/backtester, outside this cycle).

---

## (3) LATENCY (server-side, 32 turns per snapshot)

| Metric | Baseline | After | Δ |
|---|---:|---:|---:|
| p50 | 8,818 ms | 10,401 ms | +1,583 ms |
| p95 | 18,853 ms | 14,832 ms | **−4,021 ms** |
| mean | 9,716 ms | 10,636 ms | +920 ms |
| max | 50,454 ms | 28,280 ms | **−22,174 ms** |
| min | 81 ms | 23 ms | — |

Read: the median got modestly slower because turns that used to punt in ~4s (ask_user, no tools) now do real
work (propose_workflow + basket build). The tail got much healthier — the baseline 50s outlier was the
`_llm_unavailable` stall (fixed by F11 retry), and p95 dropped 4s. This is the right trade: latency bought
actual artifacts.

---

## (4) TOKENS + COST

### PRODUCT side (Azure `llm_usage` recorded per turn in the snapshots)

| | Input tokens | Output tokens | cost_usd | per turn |
|---|---:|---:|---:|---:|
| Baseline (32 turns) | 2,161,066 | 6,453 | $0.383947 | $0.0120 |
| After (32 turns) | 2,832,502 | 9,133 | $0.465500 | $0.0145 |
| Δ | +671,436 (+31%) | +2,680 (+42%) | +$0.0816 (+21%) | +$0.0025 |

The +31% input is the cost of the thematic guard keeping basket+overlay tools in scope and of turns that now
call real tools instead of punting; +42% output is the new thesis/table prose. ~1.5¢/turn remains cheap.
Known cost defect: `what_to_buy_this_week` alone burned 251,656 input tokens / 6 LLM calls / 28.3s for a thin
answer — tool fan-out cap is on the work order.

### EVAL side (this GAN workflow, shared budget pool) — **ESTIMATE**

- Output tokens spent by the workflow so far: **~3,014,836** (`budget.spent()`, shared pool; per-agent input
  tokens are not individually metered, so this is output-only and an estimate).
- Priced at **Fable 5** output ($50/1M): **≈ $150.74**.
- Lower bound if the discriminator/judge legs ran on **Opus 4.8** ($25/1M output): ≈ $75.37.
- True all-in (with unmetered input at $5–10/1M) is plausibly **$160–250** for the full round
  (baseline panel + fix engineering + after panel + verification + synthesis).

Round economics: ~$150–250 of eval/engineering spend moved 36 angle-instances by +2.11/+1.75 mean and
converted 17 FAILs into PARTIAL-or-better.

---

## (5) CANONICAL-28 REGRESSION (Kite LIVE) — R3 baseline vs after

Snapshots: `gan_r4_2026-06-10/baseline/run_20260610_210612.json` vs `after/run_20260610_213325.json`
(28 sessions / 36 turns each: execution-stress, quality-stress, F&O, regression, edge-honesty, ambiguous, multi-turn).

**Verdict: NO regressions.** 20 of 28 sessions are tool/hint/length-identical. The 8 changed turns are all
improvements or neutral:

| Session/turn | Change | Classification |
|---|---|---|
| itc_dividend_story | no tools, 237-char blurb → fetch_fundamentals+price+news, 2,454-char data-rich story | **IMPROVEMENT** (R3 dividend-routing fix verified) |
| amend_qty_then_confirm_register T2 | "click Save & activate" deflection → real `register_workflow` call, "Registered and ARMED… register-not-execute" | **IMPROVEMENT** (confirm-register fix verified) |
| i_dont_understand_then_clarify T1 | qty-interrogation deflection → actually explains why RSI(14)<30 was chosen, honestly states nothing is armed | **IMPROVEMENT** |
| is_reliance_expensive | adds get_symbol_news; answer 2,642→3,089 chars | improvement |
| analyse_hdfcbank_full | drops redundant get_live_price (price still quoted from history: ₹747.80, SMA stack, 52w anchors); 2,545→2,985 chars | neutral/improvement |
| swap_symbol_then_add_stop T0/T1 | propose_workflow → propose_dsl_workflow, same workflow_draft_card hint, params faithful | neutral (DSL path) |
| us_adr_recurring_buy | same honest MON100 redirect, now tagged ask_user hint | neutral (honesty preserved) |
| F&O (chain/suggest/iron-condor/critique), screens, plain price/RSI, ambiguous | unchanged tools, hints, and shape | no regression |

Canonical triad also improved: p50 8,437→8,611 ms (flat), p95 15,879→14,554 ms, mean 11,372→8,602 ms;
cost $0.4524→$0.4556 (flat). The R3 residual fixes plus the thematic build did **not** regress
analysis/screen/F&O/amend surfaces.

---

## Regressions found (thematic run — to fix next)

1. **swap_symbol_amount_hinglish, Angle A: PASS 8 → PARTIAL 6.** T0 "har Wednesday ₹2,000 ka JUNIORBEES
   khareed lo" now bounces to ASK_USER ("every Wednesday, or just once?") instead of drafting — 'har
   <weekday>' is unambiguously recurring. Knock-on: T1's diff references a card that never existed. Fix: treat
   har/every/weekly/roz as definitive cadence signals; clarify only when NO cadence word is present.
2. **goldbees_register_then_revert, Angle B: PARTIAL 5.5 → FAIL 4.** F7 made the SIP registerable, which
   exposed C7: an amend AFTER register routes to `propose_scheduled_order` and mints a duplicate draft
   (workflow_id=null) while the live ₹3,000 Monday workflow stays armed; the revert never asserts "exactly one
   armed workflow exists"; plus a next-run date bug (Monday cron → "16 Jun 2026", a Tuesday).

## Residual work order (still open, priority order)

1. **Prose-budget gate (highest leverage, ~7 sessions):** don't apply `_COMPACT_POST_MACRO_MAX_OUTPUT=250` when
   `reply_class=='analysis'` (and amend/build turns needing readback) — `chat_service.py:305, 5418-5422`. This
   alone restores confirm/invalidate + caveat + question on 5 thematic turns and the action-turn 120–250w floor.
2. **crude_spike composite routing (C3):** force propose_workflow when detect_thematic_scenario fires; basket
   first, option overlay second (system.md:1177-1178); fix the empty option_strategy_card digest; prefer
   protective-put over long_strangle for directional-drawdown theses.
3. **SIP cadence default:** pin vague-class SIP to monthly 1st-trading-day; never weekday-when-unspecified;
   derive prose cadence label FROM the card config (3 sessions show text↔card contradiction).
4. **Amend-after-register (C7)** + armed-count assertion on revert + cron→next-run weekday fix.
5. **Backtest honesty pair:** model must quote metrics when present (one_percent claimed "payload incomplete"
   while raw_keys carried metrics/summary_text); F10 flat-curve/0-trade engine bug on compound pct_change.
6. **screen_fundamentals sane preset** for vague asks (curated universe + real filters; never bare ROE-sort on
   11k microcaps) + honest "screen degraded" narration + snapshot widget for any in-text pick.
7. **Losers-leg completeness:** rate-cut seed needs an avoid leg (NIM-compression names); swap/replace amend
   must drop AND add (el_nino), with a before/after table.
8. **'har <weekday>' recurring Hinglish** routing (regression #1 above).
