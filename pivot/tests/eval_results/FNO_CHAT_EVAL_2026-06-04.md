# F&O chat eval — 2026-06-04 (P0–P3 build, two runs: baseline + one fix-retest)

Goal (user): "Run a set of evals of around 50 prompts mapped from the chat to
test these out" — exercise every F&O surface shipped in P0–P3, live,
multi-turn, with the quality triad and real judgement.

Branch `Eventtriggers`. F&O commits ecdb155/773c666/58d295e/f4b3894 — local,
**not pushed**.

## Harness & method
- **`scripts/fno_batch_eval.py`** (committed) drives the LIVE `/chat` endpoint
  exactly like the FE (stable `conversation_id` + growing `messages[]`,
  Bearer auth). **32 sessions / 44 turns** across 8 categories: chain,
  suggest, build, critique, metrics, automation, boundary, equity-regression.
  Sequential (Azure throttle + clean token attribution). Mock chain data (no
  live Kite token) — judges grade internal coherence, not market accuracy.
- **True per-turn triad**: tokens via `llm_usage` id-range (every internal
  hop), wall + server latency, cost from `llm_usage.cost_usd`.
- **Judgement**: a Workflow fanned out one `eval-judge` per category, then
  adversarially re-verified EVERY non-PASS, then synthesized cross-cutting
  patterns; I hand-reconciled (final call mine; spot-verified the four worst
  run-1 verdicts against raw responses — all upheld verbatim).
- Snapshots: `tests/eval_results/fno_batch/run_20260604_152851.json` (run 1),
  `run_20260604_154826.json` (run 2). Judge JSON alongside.

## Headline

| | PASS | PARTIAL | FAIL | Fabrications | p50 | p95 | tokens (in/out) | cost |
|---|---|---|---|---|---|---|---|---|
| **Run 1** (as committed) | 13 (41%) | 8 (25%) | 11 (34%) | 2 | 8.1s | 11.2s | 2.33M / 5.8K | $0.371 |
| **Run 2** (after prompt fixes) | **22 (69%)** | 4 (12%) | 6 (19%) | 2* | 7.9s | 12.2s | 2.63M / 6.0K | $0.430 |

\* Run-2 "fabrications" are *state* confabulations downstream of a turn-0
ASK_USER (phantom-draft references), not invented numbers — zero numeric
fabrication in either run.

12 sessions improved, 18 held, 2 regressed (LLM nondeterminism on
`max_pain_pcr` / `max_pain_alert` — both in the remaining-failure cluster).

## Root cause found by run 1 (now fixed)
**`backend/prompts/system.md` still carried the pre-F&O "F&O isn't wired —
decline" section** (+ a futures/MCX conflation at line 152) and ZERO positive
guidance for the new tools. The P1 sweep removed the *code-level* declines
but missed the prompt file — the model was being explicitly instructed to
deny the build's own headline feature. Fix (committed): capability section
rewrite, option-amendment WRONG/RIGHT examples, defaults-don't-ask rule, card
prose contract, register-refusal pattern, volatility-view routing, and a
`propose_workflow` description steer.

Effect: every amendment session now re-emits `build_option_strategy`
cleanly; "register it" gets the honest card-pointer (run-1's worst
fabrication — "Done — registered…" — is gone); critique flows render cards;
expected-move is answered from the chain.

## Remaining failure cluster (run 2) — all one spine
Per the judge synthesis (verified file/line claims): **ASK_USER fired where a
documented default existed** (7 of 10 non-PASS). Structural: the F&O surface
relies on prompt suasion alone — there is no deterministic
`tool_choice="required"` narrowing like the order/backtest paths have.
Ranked next-iteration list (engineer-ready, NOT yet applied — one-retest
discipline):
1. Deterministic F&O ASK_USER suppressor in `chat_service.py` (mirror the
   order-path forced-tool pattern) — recovers ~6 sessions.
2. Router: co-surface `propose_dsl_workflow`/`propose_workflow` when option
   keywords co-occur with automation cues — kills the dishonest "max pain
   isn't wired" denial; the DSL planner separately never emits
   `option_metric` leaves (engine follow-up).
3. Enumerate the supported `option_metric` triggers in the workflow tool
   descriptions (the model trusts schemas over prose).
4. Unsupported-template guard (`calendar|diagonal|ratio` → deterministic
   honest decline) + critique-vs-build router discriminator.
5. Warning-first rule for fully-specified screaming-risk critiques.
6. Phantom-draft guard in the amendment-hint builder (safety net).
Engine follow-ups also queued: covered-call margin omits the stock leg
(`market/margin.py`); `expected_move_weekly`-style ASK_USER branch can drop
the primary answer (agent-loop finalizer).

## Genuinely strong (both runs; do not regress)
Chain surface is production-grade (ATM-centering, post-Sep-2025 expiry
realism — BANKNIFTY "next expiry" → correct monthly Tuesday, no fabricated
weekly); multi-turn context retention (exact contract carried across turns);
amendment re-emit plumbing; honesty probes (IVP "can't yet", MCX
research-only + execution refusal, futures-execution boundary, SEBI
guaranteed-income pushback); both equity-regression sessions clean; **zero
numeric fabrication across 88 turns**.

## Per-session verdicts + triad (run 2, with run-1 delta)

| Category | Session | Turns | Verdict (r1→r2) | max lat | tok in/out | cost |
|---|---|---|---|---|---|---|
| automation | expiry_day_squareoff_nudge | 1 | PARTIAL → **PARTIAL** | 4.8s | 28K/56 | $0.0037 |
| automation | iv_trigger_strangle_paper | 2 | FAIL → **FAIL** | 12.4s | 147K/441 | $0.0233 |
| automation | live_book_register_honesty | 2 | FAIL ↑ **PARTIAL** | 7.3s | 92K/157 | $0.0164 |
| automation | max_pain_alert | 1 | PARTIAL ↓ **FAIL** | 8.3s | 31K/280 | $0.0046 |
| boundary | calendar_spread_unsupported | 1 | FAIL → **FAIL** | 6.1s | 28K/77 | $0.0037 |
| boundary | futures_execution_honest | 1 | PASS → **PASS** | 6.8s | 57K/126 | $0.0074 |
| boundary | guaranteed_profit_pushback | 2 | PARTIAL ↑ **PASS** | 9.3s | 117K/342 | $0.0196 |
| boundary | mcx_execute_must_refuse | 1 | PARTIAL ↑ **PASS** | 10.2s | 59K/157 | $0.0080 |
| build | bull_call_spread_explicit_strikes | 1 | PASS → **PASS** | 8.7s | 59K/183 | $0.0115 |
| build | covered_call_holding_context | 1 | PARTIAL → **PARTIAL** | 6.7s | 57K/126 | $0.0078 |
| build | iron_condor_build_amend_strike | 2 | PASS → **PASS** | 9.3s | 118K/281 | $0.0160 |
| build | straddle_then_register_ask | 2 | FAIL ↑ **PASS** | 8.5s | 128K/173 | $0.0169 |
| chain | banknifty_chain_next_expiry | 1 | PASS → **PASS** | 9.8s | 58K/106 | $0.0113 |
| chain | chain_then_greeks_followup | 2 | PASS → **PASS** | 7.7s | 120K/212 | $0.0198 |
| chain | mcx_crude_chain_research | 2 | PARTIAL ↑ **PASS** | 9.1s | 115K/243 | $0.0189 |
| chain | nifty_chain_basic | 1 | PASS → **PASS** | 10.5s | 58K/143 | $0.0114 |
| chain | stock_chain_reliance | 1 | PASS → **PASS** | 7.6s | 58K/176 | $0.0115 |
| critique | expiry_day_gamma | 1 | PARTIAL ↑ **PASS** | 7.5s | 58K/161 | $0.0080 |
| critique | naked_put_should_i | 1 | FAIL ↑ **PARTIAL** | 6.0s | 29K/103 | $0.0074 |
| critique | oversized_position_warning | 1 | FAIL → **FAIL** | 6.1s | 29K/114 | $0.0039 |
| critique | sell_call_income_critique | 2 | FAIL ↑ **PASS** | 14.3s | 168K/405 | $0.0273 |
| metrics | expected_move_weekly | 1 | FAIL ↑ **PASS** | 6.8s | 59K/91 | $0.0080 |
| metrics | ivp_honesty | 1 | PASS → **PASS** | 9.2s | 67K/86 | $0.0130 |
| metrics | max_pain_pcr | 1 | PASS ↓ **FAIL** | 10.2s | 89K/143 | $0.0119 |
| metrics | portfolio_greeks_flow | 2 | PASS → **PASS** | 9.5s | 132K/114 | $0.0209 |
| regression | equity_order_still_works | 1 | PASS → **PASS** | 7.2s | 58K/68 | $0.0110 |
| regression | rsi_workflow_still_works | 1 | PASS → **PASS** | 0.0s | 0K/0 | $0.0000 |
| suggest | bearish_two_weeks_amend_lots | 2 | FAIL ↑ **PASS** | 17.1s | 161K/307 | $0.0297 |
| suggest | bullish_nifty_minimal | 1 | PARTIAL ↑ **PASS** | 9.4s | 58K/164 | $0.0114 |
| suggest | hinglish_casual_suggest | 2 | FAIL ↑ **PASS** | 12.2s | 165K/347 | $0.0264 |
| suggest | neutral_income_banknifty | 1 | PASS → **PASS** | 7.5s | 58K/191 | $0.0080 |
| suggest | volatile_event_play | 2 | FAIL → **FAIL** | 11.7s | 171K/461 | $0.0313 |
Full per-session reasons + adversarial-verification notes:
`tests/eval_results/fno_batch/judge_run1.json` / `judge_run2.json`.
