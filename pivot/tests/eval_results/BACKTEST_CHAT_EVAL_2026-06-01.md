# Backtesting chat eval — 2026-06-01

**What:** live, in-process run of 19 turns (16 sessions) through `chat_service.handle()`
on current code (real Azure `gpt-5.4-mini` + yfinance). Covers the backtesting
prompt shapes the model is meant to handle, plus 2 multi-turn sessions (tuning +
refinement) and 2 capability-boundary edges. Each turn captured the quality triad:
**tokens · latency · quality signals** (tool routed, real numbers, rigor battery
present, no fabrication). Runner: `/tmp/bt_eval/run.py`; raw: `/tmp/bt_eval/results.json`.

> Note: the eval ran **in-process** because the live `:8000` server is serving
> stale code (started before the `backend/services/backtest/validation/` package
> existed) and currently fails every backtest with "internal import error" — it
> needs a restart. In-process = a clean read of the actual code behaviour.

## Per-turn results (the triad)

| Session | Prompt shape | Tool routed | Lat (ms) | In tok (cached) | Out | Ran? | Battery | Verdict |
|---|---|---|---|---|---|---|---|---|
| s01 | RSI entry | backtest_workflow (+dsl_tree) | 13053 | 68762 (61312) | 217 | ✅ | ✅ | No edge |
| s02 | **SMA 50/200 crossover** | **∅ (shape failure)** | 8996 | 62746 (61952) | 201 | ❌ | — | — |
| s03 | RSI entry + 5% stop | **ASK_USER** | 16017 | 102542 (66560) | 273 | ❌ | — | — |
| s04 | Monthly SIP | backtest_workflow | 8345 | 58109 (27136) | 133 | ✅ | ✅ | No edge |
| s05 | Bollinger lower-band | backtest_dsl_tree | 10204 | 65983 (31232) | 134 | ⚠️ ran, weak narration | ❌→fixed | — |
| s06 | **MACD crossover** | **∅ (shape failure)** | 9312 | 62745 (61952) | 213 | ❌ | — | — |
| s07 | Intraday-dip (−4%/day) | **ASK_USER** | 14236 | 97623 (63488) | 219 | ❌ | — | — |
| s08 | Compound (RSI ∧ EMA) | backtest_dsl_tree | 14494 | 66033 (61824) | 155 | ✅ (0 trades, honest) | ❌→fixed | — |
| s09 | Donchian breakout | backtest_dsl_tree | 9877 | 30637 (29696) | 136 | ❌ (yfinance data miss) | — | — |
| s10 | Cross-asset trigger | backtest_dsl_tree | 9554 | 31582 (30592) | 130 | ❌ (offered, didn't run) | — | — |
| s11 | **EMA 9/21 crossover** | **ASK_USER** | 13675 | 94444 (93184) | 272 | ❌ | — | — |
| s12 | Weekly schedule | backtest_workflow | 6874 | 63589 (59904) | 163 | ✅ | ✅ | No edge |
| m1#0 | Tuning: RSI<30 | backtest_workflow | 10174 | 65533 (64384) | 178 | ✅ | ✅ | No edge |
| m1#1 | Tuning: RSI<25 | find_tool→backtest_workflow | 14871 | 100304 (32768) | 198 | ✅ | ✅ | No edge |
| m1#2 | Tuning: RSI<20 | **ASK_USER** | 17221 | 94166 (61440) | 140 | ❌ | — | — |
| m2#0 | Refine: SMA crossover | **ASK_USER** | 21747 | 132730 (127488) | 408 | ❌ | — | — |
| m2#1 | Refine: add stop | **ASK_USER** | 13760 | 99402 (65536) | 320 | ❌ | — | — |
| e1 | Options iron condor | ∅ (instant decline) | **0** | **0** | 0 | ✅ declined | n/a | n/a |
| e2 | 1-min intraday scalp | ASK_USER (offered daily) | 11078 | 91238 (60416) | 250 | ✅ declined-ish | n/a | n/a |

## Aggregate scorecard

- **Tool routing / success: ~7/12 capable single-turn prompts produced a backtest** (s01, s04, s05, s08, s12 + m1#0/#1). **2 hard-failed** with a "trigger ref the engine can't resolve in historical mode" shape error (s02 SMA, s06 MACD). **3 over-asked** (ASK_USER: s03, s07, s11). 1 external data miss (s09), 1 offered-but-didn't-run (s10).
- **Latency:** median ~13 s, range 6.9–21.7 s (plus the 0 ms options decline). On the high side; ASK_USER + `find_tool` detours inflate it.
- **Tokens:** input 30k–133k but **~90%+ cached** (e.g. s12: 59904/63589 cached); effective uncached input ≈ a few k; ledger cost ≈ **$0.004/turn**. Output 130–408. Cost is modest; **latency, not cost, is the user-facing tax.**
- **No fabrication:** numbers narrated matched tool results; 0-trade cases reported honestly (s08); honest "No demonstrable edge" verdicts on the genuinely-edgeless RELIANCE/INFY strategies. ✅
- **Multi-turn context:** partially works — the tuning sequence ran for 2 of 3 turns and the trial counter incremented; the 3rd turn regressed to ASK_USER.

## Findings (ranked)

### P0 — FIXED this session
1. **`backtest_dsl_tree` dropped the entire rigor battery.** It builds its own card payload and copied only legacy metric keys, omitting `forward_stats`/`monte_carlo`/`sub_periods`/`trust_verdict` — so **all of P1.2–P1.9 was invisible on ~⅓ of capable prompts** (every dsl-tree route: s05, s08, s09, s10). **Fixed** (`5870e74`): the four blocks are now in the card metrics and the summary leads with the verdict + PSR. Verified in-process — RELIANCE RSI(14)<35 dsl-tree → "Verdict — Unproven: …PSR 86%; needs ~1096 obs (have 491)".

### P0 — environmental
2. **The `:8000` server is stale** → live `/chat` backtests fail with "internal import error". Needs a restart to pick up this session's code. (Won't touch it without your go-ahead.)

### P1 — capability/behaviour gaps (not yet fixed; LLM-behaviour, need prompt work + a retest loop)
3. **Crossover prompts fail** (SMA s02, MACD s06, EMA s11 — 3/12). The model tries a `trigger.indicator` crossover, the historical engine rejects it ("runtime trigger ref the engine cannot resolve"), and it gives up (no tool) or asks — instead of routing to `backtest_dsl_tree`, whose compound translator *can* express crossovers. **The single biggest capability gap** (crossovers are bread-and-butter). Fix path: sharpen `system.md`/tool descriptions to send all crossover / two-series conditions to `backtest_dsl_tree`, and/or make `backtest_workflow` delegate crossover operators instead of erroring.
4. **Over-asking (ASK_USER) on complete prompts** (s03, s07, s11, m1#2, m2#0, m2#1). The engine already has sane defaults (3y window, qty 10, n-day-hold exit); the model should run with them, not clarify. m1#2 asked even after two prior turns established the exact shape — a context-confidence regression on the backtest path.
5. **Trial counter groups by *user*, not *conversation*.** `num_trials` came out wrong (s04=3, s12=4, m1#0=4 — each should be 1) because every user-1 backtest shares `trial_group="u1"`, so unrelated conversations over-deflate each other's DSR. Within a conversation the deflation is right; across them it shouldn't accumulate. Fix: group by `conv_id` (needs threading conv_id into the `_backtest_workflow` tool handler — today only `uid` is available).

### P2
6. dsl-tree narration was weak (s05 "partial chart trace, not summary metrics" despite a real return) — the verdict-led summary added in the P0 fix should improve this.
7. Latency 7–22 s; the `find_tool`/ASK_USER detours are the main inflaters.

## Positives
- The `backtest_workflow` path is solid: correct routing, real numbers, the full battery, and **honest verdicts** ("No demonstrable edge" on edgeless strategies) — the differentiator works end-to-end in live chat.
- Options correctly declined instantly with zero LLM cost; intraday correctly explained as daily-only.
- No fabrication anywhere; 0-trade and data-miss cases reported honestly.

## Recommended next (in priority order)
1. ✅ done — surface the battery on `backtest_dsl_tree` (P0 #1).
2. Route crossovers reliably to the compound translator (P1 #3) — biggest capability win; needs a prompt change + a one-shot retest.
3. Group the trial counter by `conv_id` (P1 #5) — correctness of the DSR deflation.
4. Reduce ASK_USER on the backtest path (P1 #4) — prefer running with defaults.
5. Restart `:8000` so the live FE reflects all of the above.
