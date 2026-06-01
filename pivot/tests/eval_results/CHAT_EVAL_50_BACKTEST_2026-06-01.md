# Backtesting chat eval — 54 detailed turns, live, in-process — 2026-06-01

Fifty-plus fully-specified algo-trader prompts (explicit date ranges, indicator
periods, thresholds, sizing targets, lookbacks, top_n, rebalance, sector caps),
run in-process via `chat_service.handle` (`:8000` runs stale code). Triad captured
per turn (tokens · latency · quality), and judged additionally on **param
fidelity** — did the system honour the specified range/parameters?

## Aggregate

| metric | value |
|---|---|
| turns | 54 (incl. 3 multi-turn tweak sequences) |
| **quality** | **37 PASS · 6 PARTIAL · 9 FAIL** (+ 1 infra blip, + 1 harness-prompt bug) |
| routing (right tool) | 42 / 54 |
| **param fidelity** (of turns that ran) | **excellent** — exact ranges, z-thresholds, lookbacks, vol targets, top_n/rebalance all honoured + echoed |
| **honesty** | strong — "not cointegrated", "no edge", "unproven", "P/B not screenable", "TATAMOTORS didn't return", no fabrication |
| tokens | 6.23 M total · ~115 k/turn (heavy) |
| latency | median 11.8 s · p90 16.5 s · max 24.2 s |

**Verdict: the substance is strong and the detailed specs are respected — the
failures are concentrated, deterministic routing bugs, all fixable.** Examples of
the param fidelity that worked: `RSI(14)<25, 10-day hold, 2020-01-01→2023-12-31`
(1 trade, flat — honest); `HDFCBANK/ICICIBANK 60-day lookback, entry z=2, exit
z=0.5, 5y → not cointegrated, 42 trades, −6.4%`; `12% vol targeting`; `top 5, 47
monthly rebalances, +35.6%, unproven PSR 0.878`; `quarterly top-4, 16 rebalances`.

## Failure patterns (all fixable)

1. **Pairs intent regex gaps (2 FAIL — c3, c5).** "Backtest a **SBIN/PNB spread**,
   enter at ±2.5σ…" and "**mean-reversion spread** between AXISBANK and KOTAKBANK"
   routed to `backtest_dsl_tree` (the single-symbol engine, → flat/no-metrics)
   instead of `backtest_pairs`. The router's pairs rule doesn't catch "X/Y spread …
   sigma" or "mean-reversion" (its `mean[\s-]?revert` misses the "-reversion" form).

2. **Portfolio intent regex gaps (2 FAIL — e4, e6).** "**12-1 momentum strategy** on
   [list], top 5" → `backtest_workflow` (ran HDFCBANK alone); "**dollar-neutral
   momentum**: long the top 5, short the bottom 5" → `ASK_USER`. The portfolio rule
   misses "12-1 / N-1 momentum strategy on [list]" and the spaced "long the top N …
   short the bottom N" form.

3. **Tweak-followup surface excludes the quant tools (3 FAIL — s2b, s2c, s3b).** In a
   portfolio/pairs conversation, "make it long/short", "cap each sector at 25%",
   "widen the entry to 2.5σ" misrouted (→ `backtest_dsl_tree`) or asked. The
   `_backtest_followup` handler narrows the tweak surface to
   {`backtest_workflow`, `backtest_dsl_tree`, `ASK_USER`} — it never includes
   `backtest_portfolio` / `backtest_pairs`, so a quant-tool tweak can't re-run its
   own tool. (Context retention itself works — s2b correctly recalled the 5-name
   momentum setup; it just couldn't route to the portfolio tool.)

4. **"Run, don't ask" still trips on 2 (a4 MACD crossover, b4 vol-target sizing).**
   The tool IS surfaced (dsl_tree) but the model asked anyway. Model-judgment; the
   surface fixes above cut most asks, this residue is harder.

## Honest gaps (not bugs — deferred work, handled gracefully)

- **g1 — deep validation in chat.** "…tell me if it's overfit, run a walk-forward
  and permutation test" → ran the backtest, then honestly: "I cannot assess
  walk-forward or permutation robustness from this." The P1.4 engine exists
  (`/api/backtest/dsl/validate`) but has **no chat tool yet**.
- **f6 — industry-neutral decile in chat.** → "this DB doesn't support true
  industry-neutral ranking; I ranked each industry separately." The Engine-1
  `decile(neutralize(roe))` exists but isn't chat-exposed; `screen_fundamentals`
  approximated honestly.
- **f5 — P/B not in `screen_fundamentals`** (it's in the Engine-1 ratios, not the
  chat screener) → said so, screened the other three conditions.
- **Sparse-metrics reporting (a3, b2):** legit `dsl_tree` runs that came back flat /
  0-trade reported as "no usable metrics" rather than the cleaner "0 trades — the
  rule never fired." Worth tightening.
- a2 was an **Azure blip** ("AI backend temporarily unavailable"); e2 was a **harness
  prompt bug** ("those same 10 names" with no antecedent in a fresh conversation).

## Recommended fixes (deterministic, high-impact)

1. **Broaden the pairs router regex** — catch "X/Y spread … σ/z", "mean-reversion /
   reversion spread between A and B". (c3, c5)
2. **Broaden the portfolio router regex** — catch "N-1 / 12-1 momentum strategy on
   [list]", "dollar-neutral momentum, long top N short bottom N". (e4, e6)
3. **Add the quant tools to the `_backtest_followup` tweak surface** so portfolio /
   pairs tweaks re-run their own tool. (s2b, s2c, s3b)
4. Wire a **P1.4 deep-validation chat tool** (closes g1); expose Engine-1
   `decile/neutralize` in chat (closes f6). [larger, separate]

## Per-turn (compact)

| id | cat | routed | quality | note |
|---|---|---|---|---|
| a1 | single | ✓ dsl_tree | PASS | RSI25/10d/2020-23 honoured; 1 trade, honest |
| a2 | crossover | — | INFRA | Azure "backend unavailable" blip |
| a3 | compound | ✓ dsl_tree | PARTIAL | "no usable metrics" (rare compound, ~0 fires) |
| a4 | crossover | ✗ ASK | FAIL | MACD crossover → asked instead of running |
| a5 | bollinger | ✓ dsl_tree | PASS | 2021-24 honoured, flat −0.22% |
| a6 | breakout | ✓ dsl_tree | PASS | |
| a7 | crossover | ✓ dsl_tree | PASS | 12/26 EMA + 5% stop, 4y |
| a8 | compound | ✓ dsl_tree | PASS | RSI<30 + vol filter, exact range |
| a9 | crossfilter | ✓ dsl_tree | PASS | NIFTY-200SMA filter |
| a10 | single | ✓ dsl_tree | PASS | trailing stop, 50 shares |
| b1 | sizing | ✓ dsl_tree | PASS | 12% vol target honoured |
| b2 | sizing | ✓ dsl_tree | PARTIAL | ATR-risk run came back metric-less |
| b3 | sizing | ✓ dsl_tree | PASS | 20% pct-equity |
| b4 | sizing | ✗ ASK | FAIL | vol-target → asked (heavy: 249k tok) |
| b5 | sizing | ✓ dsl_tree | PASS | ATR-risk 0.5%/3×ATR |
| c1 | pairs | ✓ backtest_pairs | PASS | z2/0.5, 60d, 5y all honoured |
| c2 | pairs | ✓ backtest_pairs | PASS | honest not-cointegrated |
| c3 | pairs | ✗ dsl_tree | FAIL | "SBIN/PNB spread" misrouted |
| c4 | pairs | ✓ backtest_pairs | PASS | 40d lookback, honest −41% |
| c5 | pairs | ✗ dsl_tree | FAIL | "mean-reversion spread" misrouted |
| c6 | pairs | ✓ backtest_pairs | PASS | half-life query |
| d1–d3 | scan | ✓ scan_pairs | PASS | 5%/1% levels honoured |
| d4–d5 | johansen | ✓ test_cointegration | PASS | rank verdicts |
| d6 | johansen | ✓ test_cointegration | PARTIAL | TATAMOTORS dropped (data), tested 2/3 |
| e1 | portfolio | ✓ backtest_portfolio | PASS | top5/monthly/5y, +35.6% unproven |
| e2 | portfolio | — | HARNESS | "those same 10" — my prompt bug |
| e3 | portfolio | ✓ backtest_portfolio | PASS | weekly/197 rebal (cap/top_n not echoed) |
| e4 | portfolio | ✗ workflow | FAIL | "12-1 momentum" → single-symbol |
| e5 | portfolio | ✓ backtest_portfolio | PASS | quarterly top-4, 16 rebal |
| e6 | portfolio | ✗ ASK | FAIL | dollar-neutral L/S → asked for sizing |
| e7 | portfolio | ✓ backtest_portfolio | PASS | PSU-bank top-3 |
| e8 | portfolio | ✓ backtest_portfolio | PASS | (via find_tool) |
| e9 | portfolio | ✓ backtest_portfolio | PASS | auto L/S, no_edge honest |
| e10 | portfolio | ✓ backtest_portfolio | PASS | sector-capped top-6 |
| f1–f4 | screen | ✓ screen_fundamentals | PASS | multi-condition thresholds |
| f5 | screen | ✓ screen_fundamentals | PARTIAL | honest "P/B not screenable" |
| f6 | factor | ✓ screen_fundamentals | PARTIAL | honest industry-neutral gap |
| g1 | validate | ✓ dsl_tree | PARTIAL | honest "can't walk-forward in chat" |
| g2 | rigor | ✓ (no tool) | PASS | overfitting answer |
| g3 | rigor | ✗ ASK | FAIL | "luck vs random" → asked |
| s1a/s1b | tweak | ✓ | PASS | RSI 30→20 retained |
| s1c | tweak | ✓ workflow | PASS | +5% stop (workflow OK for a stop) |
| s2a | tweak | ✓ backtest_portfolio | PASS | |
| s2b | tweak | ✗ dsl_tree | FAIL | "make it L/S" → wrong engine |
| s2c | tweak | ✗ ASK | FAIL | "cap sector 25%" → asked |
| s3a | tweak | ✓ backtest_pairs | PASS | |
| s3b | tweak | ✗ ASK | FAIL | "widen to 2.5σ" → asked |
