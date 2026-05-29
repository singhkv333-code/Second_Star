# Retail batched chat-eval — 2026-05-29 (PM)

Goal (user): "Run a set of evals in batches targeting varied types that a
retail investor would touch upon" — i.e. exercise the categories we just
strengthened, live, multi-turn, with the quality triad and real judgement.

Branch `Eventtriggers`. Commits local — **not pushed**.

## Harness & method
- **`scripts/retail_batch_eval.py`** drives the LIVE `/chat` endpoint
  (`{messages, conversation_id, include_portfolio_context}` + Bearer auth),
  exactly like the frontend: a stable `conversation_id` with a growing
  `messages[]` window. 19 sessions / 30 turns across 10 retail batches.
- **Sequential** live execution (no parallel bursts): keeps Azure off the
  "temporarily unavailable" throttle AND keeps per-turn token attribution
  clean.
- **True per-turn triad**: tokens via the `llm_usage` table by id-range
  (`MAX(id)` before → `SUM(... WHERE id > prev)`), which captures EVERY
  internal hop (chat + router + propose + agentic), not just the final hop a
  log-scrape would see. Latency wall + server. Cost from `llm_usage.cost_usd`.
- **Judgement = mine + a thinking-model panel.** A Workflow fanned out one
  `eval-judge` per category (reasoning about what the retail user *needed* vs
  *got*, given explicit scope rules), then **adversarially verified every
  non-PASS** to catch false FAILs and hidden FAILs, then synthesized
  cross-cutting patterns. I then hand-reconciled every verdict (final call is
  mine). Snapshot: `tests/eval_results/retail_batch/run_20260529_220653.json`;
  judge output: `.../judge_20260529_220653.json`.

## Result — 8 PASS / 6 PARTIAL / 5 FAIL (42% / 32% / 26%), ZERO fabrications
Run triad: **p50 12.0s, p95 22.7s**, total in 1.85M / out 6.66K tok, **$0.34**.

> The thinking-model panel scored 8/5/6; I moved only `gtt_reliance` FAIL→
> PARTIAL (it *does* register a GTT with a visible qty=1, though the silent
> default is a real M1 bug). Everything else I confirmed against the raw data.

| Category | Session | Verdict | lat (wall) | in/out tok | Note |
|---|---|---|---|---|---|
| comparison | compare_tcs_infy_3y | ✅ PASS | 12–17s | ~58–92K | real 3y window, consistent vol; 1 stray find_tool hop |
| comparison | lakh_in_hdfc_vs_fd | 🟡 PARTIAL | 10–12s | 29/61K | turn0 re-asks an amount it just acknowledged; FD leg half-delivered |
| screening | cheap_banks | ✅ PASS | 22.7s | 56K | **bank P/E label fix works** (CANBK 4.55, AXIS 14.29) |
| screening | best_dividend | 🟡 PARTIAL | 15s | 91K | payout-ratio micro-cap artifacts (RFS05, ILF02), not yield |
| screening | reliance_pe_roe | ❌ FAIL | 11s | 84K | **turn0 echoed the question** via ASK_USER (data fine: PE 25, ROE 8.93%) |
| screening | roe_pe_screen | 🟡 PARTIAL | 16s | 57K | ignored explicit "large caps"; ROE 96% artifact rows |
| sip/gold/silver/gtt | nifty_sip_amend | ✅ PASS | 8–10s | 56–58K | amount amendment retained context |
| " | gold_sip_every_month | ✅ PASS | 7s | 56K | GOLDBEES SIP (no first-run date in blurb — minor) |
| " | silver_sip | ✅ PASS | 8s | 49K | SILVERBEES resolved |
| " | gtt_reliance | 🟡 PARTIAL | 9s | 50K | **silently defaulted qty=1** (M1 violation, but registers + visible) |
| rbi_event | rbi_rate_cut_banks | ❌ FAIL | 8–19s | 58–61K | event trigger drafts but banking basket is an unresolved `{{context.1.ranked}}`; confirm re-drafts the broken basket |
| dip_profit | dip_simple | ✅ PASS | 4s | 28K | correctly asks qty (M1) — not over-clarification |
| dip_profit | dip_profit_compound | ✅ PASS | 16–26s | 86/62K | **profit is entry-relative** (the prev-unverified one — now good); slow |
| ipo | ipo_browse_apply | 🟡 PARTIAL | 8–16s | 55–90K | graceful empty-feed; turn2 asks "which IPO?" when none exist |
| oil_mcx | war_oil_mcx | ✅ PASS | 6s | 48K | clean F&O/MCX decline + offers underlying |
| analysis | why_nifty_down | 🟡 PARTIAL | 9s | 56K | fetched level (no crash) but answer generic, no real movers, level omitted |
| analysis | it_sector_outlook | ❌ FAIL | 8s | 30K | **0 tools** — evergreen prose; user explicitly wanted search-and-think |
| context | qty_amendment_expiry | ❌ FAIL | 12–19s | 60–90K | **SILENT DSL REGRESSION** (see below) |
| multi_symbol | basket_three | ❌ FAIL | 12s | 56K | "when nifty rises 1%" trigger misread as buy instrument → basket dropped |

## The headline finding — a silent regression that looks like success
`qty_amendment_expiry` is why text-only eval (and my first read) lies:
- turn1 (after "10 shares") = correct **5-step** DSL: dip-trigger → **buy 10** →
  exit-trigger(5%) → fetch portfolio → **sell**.
- turn2 (after "set an expiry for the next 30 days") = collapsed to **2 steps**:
  dip-trigger → **notify.message only**. Buy + exit + sell silently dropped —
  while the card still reads *"HDFCBANK 10-share dip buy with 5% exit"* and the
  text says *"Drafted with a 30-day expiry."*

Root cause: the amendment turn re-emits `propose_dsl_workflow` **from scratch**;
the model focuses on the expiry and omits the action/exit args, and
`propose_dsl_workflow` silently degrades a no-action call into a notify-only
workflow. The fix is to PATCH the prior draft for non-structural amendments
(expiry/qty/notes), not re-translate — deferred to a focused session because a
wrong patch would break the working qty-amendment case (turn1).

## Cross-cutting failure patterns (panel synthesis, confirmed against raw)
1. **[HIGH] ASK_USER misfires on fully-specified prompts** — clarifier (or a
   verbatim echo of the user's own text) on requests where every slot is
   filled. → `reliance_pe_roe`, `lakh_in_hdfc_vs_fd`, `it_sector_outlook`.
2. **[HIGH] Silent slot/symbol drop on confirm/amend turns** — re-translate
   loses fields instead of patching/registering. → `qty_amendment_expiry`,
   `rbi_rate_cut_banks`, `basket_three`.
3. **[HIGH] Index-vs-instrument confusion in triggers** — an index in a WHEN
   clause is treated as the order target. → `basket_three`; also `why_nifty_down`
   omits the actual index level.
4. **[HIGH] M1 quantity rule not uniform** — enforced on plain buys
   (`dip_simple` asks) but not GTT (`gtt_reliance` defaults qty=1).
5. **[MED] Screen quality** — no market-cap floor / outlier bound → micro-cap
   artifacts with ROE 96% or payout >150%. → `roe_pe_screen`, `best_dividend`.
6. **[MED] Half-delivered comparisons** — first leg given, second deferred to
   "I can compute that if you want". → `lakh_in_hdfc_vs_fd`, `why_nifty_down`.
7. **[MED] Context not consulted before ASK_USER** — empty IPO window then
   "which IPO?"; basket context lost. → `ipo_browse_apply`, `basket_three`.
8. **[LOW] Redundant find_tool hops** on follow-ups (~3–4s, ~30K tok each).
9. **[LOW] SIP copy inconsistency** (GOLD/SILVER omit first-run date).

## Fixes
### Applied this session (surgical, isolated, verified)
- **ASK_USER echo guard** (`validation_handler.py`): reject an ASK_USER whose
  question ≈ the user's last message (normalised); feed an error back into the
  agentic loop so the model answers with a read tool instead of parroting.
  Directly kills `reliance_pe_roe` turn0's worst behaviour. Isolated to the
  ASK_USER intercept — does not touch the M1/M2 qty machinery.

### Top P0 for the next focused session (need careful work / a fresh retest)
- **GTT M1 qty** (`create_gtt_order`): a GTT buy with no qty/budget should ask
  for size instead of silently defaulting qty=1. NOT done now: GTT orders have
  no `steps[]` so the existing M2 guard doesn't cover them — this needs a new
  register-payload-level check, and touching the tuned qty path on a one-shot
  retest budget is too risky for a PARTIAL.
- **DSL amendment = PATCH, not re-translate** (the silent regression above).
- **Index-as-trigger vs instrument** in `propose_workflow` so "buy A,B,C when
  NIFTY rises 1%" drafts the named basket on an index trigger (and a guardrail
  that fails loudly if any named equity is dropped).
- **Analysis auto-chains grounding tools** — "outlook for X" / "why is INDEX
  down" must call screen/compare/news/index-level, never answer with 0 tools.
- **Screen market-cap floor + ROE/payout sanity bounds**; dividend → yield.
- **Confirm intent** ("yes/set it up") after a draft card registers the
  `draft_id` instead of re-drafting.

## Round 2 fixes (2026-05-29 PM) — 5 P0s, re-judged 9/6/4 → 12/6/1

Root-caused via a 5-agent investigation workflow; applied surgically; ONE
consolidated live retest (snapshot `run_20260529_225215.json`, judge
`judge_20260529_225215.json`) confirmed every fix with no PASS-session
regressions and zero new unit-test failures. Triad improved: p50 12.0s→9.5s,
p95 22.7s→17.0s (cost $0.34→$0.39 from richer grounding + a larger prompt).

| Session | before | after |
|---|---|---|
| qty_amendment_expiry (P1) | FAIL | **PASS** — expiry amend keeps all 5 buy+sell steps |
| basket_three (P2) | FAIL | **PASS** — RELIANCE/TCS/INFY on a NIFTY trigger |
| reliance_pe_roe (echo+P5) | FAIL | PARTIAL → (collision fix) |
| it_sector_outlook (P3) | FAIL | PARTIAL — now grounds via screen_fundamentals |
| best_dividend (P5) | PARTIAL | **PASS** — recognizable names, payout-not-yield note |
| gtt_reliance (P4) | PARTIAL | **PASS** — asks for size, no silent qty=1 |
| why_nifty_down (P3) | PARTIAL | **PASS** — states real level + chains movers |
| roe_pe_screen (P5) | PARTIAL | PARTIAL → (collision fix) |
| dip_profit_compound | PASS | PARTIAL — judge variance (turn0 readback render=ask_user + 6-hop cost); draft is correct (entry-relative 8%) |

**Fixes shipped (commit `0f20604`):** P1 DSL-amendment PATCH-not-retranslate +
lost-action guardrail; P2 index-as-trigger basket guidance + formatter guard;
P3 sector-outlook routing + grounding bullets; P4 GTT M2b qty guard; P5 cap
tier + sanity bounds.

**Round-2 follow-up (commit pending):** the re-judge surfaced a screener
`sc_id` collision — symbol `RELIANCE` resolved to impostor ticker-only rows
("Reliance Infra" P/E 2.08) above the real Reliance Industries. Fixed in
`fundamentals_screen.py` by excluding ticker-only rows whose ticker
impersonates a real `nse_symbol`. Verified directly: large-cap + energy screens
now show the real Reliance (P/E 25), cheap-banks unchanged.

## Still open (next session)
1. **rbi_rate_cut_banks (the 1 remaining FAIL)** — "yes, set it up" on a draft
   that disclosed an unresolved banking-basket blocker re-drafts the same
   broken basket instead of registering. Needs: confirm-intent → register the
   draft_id; resolve the banking basket to concrete names.
2. **find_tool reconnaissance hop** on obvious follow-ups (comparison, sector
   compare, fundamentals screens) — ~3–4s + ~30K tok each. Cache last tool.
3. **lakh_in_hdfc_vs_fd / dip_profit_compound** turn-0 over-clarify / readback
   render=ask_user mislabel — emit a `readback_confirm` hint, compute the
   what-if immediately when all slots are given.
4. **ipo "I want to apply"** when the feed is empty should acknowledge the
   empty window, not ask "which IPO?".

## How to reproduce
Backend on :8000. `cd pivot && .venv/bin/python scripts/retail_batch_eval.py`
→ snapshot JSON. Judge via the `retail-eval-judge` workflow over the snapshot.
