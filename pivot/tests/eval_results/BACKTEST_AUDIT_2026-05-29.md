# Backtesting & indicator audit + remediation — 2026-05-29

Triggered by a "Buy NIFTYBEES when it crosses 200 EMA" agent: the EMA is on
**daily closes**, but the live path fetched only **6 months** of bars, so a
200-period EMA could never compute live (needs ≥205 daily bars, got ~123) — the
agent backtested fine yet was inert in production. Full assessment of every
backtest path + indicator, benchmarked against professional standards, then
remediated. Branch `Eventtriggers`. **Not pushed.**

## What we found
- **Interval = daily (`1d`) everywhere.** yfinance, adjusted close (splits/
  dividends handled). No intraday/weekly. Indicators: one `pandas_ta` registry
  (`backtest_indicators.py`) shared by live + backtest → math is consistent.
- **5 backtest engines**, inconsistent conventions. Main retail chat path
  (`backtest_dsl_tree` → `dsl/backtest/engine.py`) was already solid (warm-up
  skip, next-bar-open fill, realistic India costs via `backtester/engine.py`
  buy/sell_cost, calendar CAGR) but missing Sharpe/Sortino + benchmark.
  Draft-card backtest (`workflow_backtester.py`): next-bar-open ✓ but flat
  10 bps costs, `n_days/252` CAGR. Two engines (`indicator_backtest.py`,
  `open_close_backtest.py`) are **dormant** (no chat/router caller).
- **THE critical defect — live≠backtest data-window parity.** Live indicator
  fetch hardcoded `period="6mo"` at 6 sites; guard `len(bars)<max(period+5,20)`.
  ⇒ any indicator period >~120 (EMA200) and volume-MA >~50 silently returned
  `None` and never fired live, though they backtested on 2–5y. No error.
- Verified true bugs: the parity break; a same-bar-close look-ahead in the
  dormant `indicator_backtest`. Audit false alarms (NOT bugs): max-DD `min()`
  is correct on negative dd; calendar-day CAGR is the correct convention.

## Professional benchmark (web research)
Already met (main path): adjusted close, next-bar fill, warm-up, realistic
India costs, daily basis. Gaps closed below. Out of scope for retail v1
(documented limitations): point-in-time/survivorship data, walk-forward/OOS,
Monte-Carlo, intraday timeframes, tax accounting.

## What changed (this commit)
**P0 — parity (acceptance gate):**
- `market_data.period_for_indicator/period_for_bars` (pure, capped 3y) size the
  fetch to the indicator period. Replaced the hardcoded `"6mo"`/`"3mo"` at all
  6 live sites (`scheduler._compute_indicator_sync`, `dsl/data_accessor`
  get_indicator/_historical_bar_price/get_volume, `steps/fetches`
  indicator/spread-z). Floored at 6mo so small periods never shrink.
  **Verified: `_compute_indicator_sync("RELIANCE","ema",200)` → 1418.04 (was
  None); scheduler and DSL accessor return the identical value (math parity).**
- `backtest_resolvability.check_live_fireable` warns (not blocks) on any
  indicator needing >3y of history; surfaced as `draft["live_warnings"]`.

**P1 — consistency + completeness:**
- `services/trading_costs.py`: single source for NSE/BSE delivery costs
  (brokerage + slippage + STT *both sides* + exchange + SEBI + GST + stamp ≈
  **37 bps round-trip**, was 20). Legacy `backtester/engine.py` re-exports it;
  the multiplier engines' flat `_FRICTION` now = the model's per-leg average
  (round-trip identical to per-side, so no fill-loop rewrites).
- `services/backtest_metrics.py`: shared `sharpe_sortino` (×√252, rf 6.5%),
  `calendar_cagr_pct`, `methodology_note`. Sharpe/Sortino populated in
  `workflow_backtester`, `dsl/backtest/engine`, `indicator_backtest` (were
  None). CAGR standardized to calendar-year in `workflow_backtester`.
- Buy-and-hold **benchmark net of one round-trip** in every payload (new
  `benchmark_return_pct` on the DSL `BacktestMetrics`).

**P2 — transparency:**
- `methodology` block (window · after-costs · daily-bar basis · survivorship
  caveat) on every backtest payload + summary text (`_dsl_chat_tools`,
  `workflow_backtester`, `routers/workflows`, `tool_executor`).
- FE `IndicatorBacktestCard`: renders Sharpe/Sortino + the methodology strip;
  also fixed 3 pre-existing `bench…null` type errors (FE tsc 4→1, the remaining
  one is an unrelated pre-existing `ChatDemo` error).

**Verified end-to-end:** DSL backtest of the 200-EMA crossover now returns a
3y window, Sharpe/Sortino, benchmark **+14.4% net** (card honestly shows the
strategy lagged buy-and-hold), and the methodology block. Full backend suite:
**zero new failures** (18 pre-existing, confirmed via stash-baseline; the one
intended cost fixture updated). FE card tests 11/11.

## Consciously deferred
- **P1-6: dormant `indicator_backtest._simulate` look-ahead** (same-bar-close
  fill). It's not on any chat/router path (only a test), so it has no
  user-facing impact; left documented rather than restructured on this budget.
  If revived for any surface, switch it to next-bar-open like the other engines.

## How to verify
`_compute_indicator_sync("RELIANCE","ema",200)` → float. `backtest_dsl_tree(...)`
→ payload with `metrics.sharpe/sortino/benchmark_return_pct` + `methodology`.
`screen_by_fundamentals(...)` unaffected. Backend on :8000 has the new code.
