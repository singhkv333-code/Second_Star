# STATUS — Pivot Agent System Sprint

> Daily lead-owned status report. Read top-to-bottom: most recent day first.

---

## Day 15 — 2026-06-02 (frontend-lead) — IPO P1 frontend

- **pivot-next IPO P1**: `lib/types.ts` — added `IpoSubscription`, `IpoSubscriptionResponse`; updated `IpoLockedFields.subscription` from `string|null` to `IpoSubscription|null`; added `listing_date: string|null`. `lib/api.ts` — added `getIpoSubscription(symbol)` via `requestLegacy`. `IpoApplicationCard.tsx` — structured subscription block (per-category "RII 2.1× · NII 0.8× · QIB 1.4×" + as-of + Refresh button, only when open), RHP prospectus link, allotment/registrar fallback ("check with your broker / registrar"), listing date in locked grid, contextual oversubscription note at lots stepper, GMP chip (only when `payload.gmp` present — absent in v1). `tsc --noEmit` clean; per-file lint clean.

---

## Day 14 — 2026-06-01 (lead) — Data audit + DB restructuring + Phase 2.1/2.3

Running log (updated after each build+test run, newest last).

### Data audit + trim + restructuring (committed `8825f3b`)
- **Audit** (`docs/DATA_AUDIT.md`, evidence from live Postgres): `mc` is fundamentals
  (`statement_lines` 10.7M / 6,858 cos, real PIT via `availability_date`) + a tiny
  price table. **OHLCV is yfinance's job — the 9 `mc.daily_prices` rows were a mistake,
  ignored.** Two root-cause bugs found: the TTM CTE filtered a phantom
  `statement='quarterly_results'` (data is annual-only) → every TTM field returned 0;
  and several `line_items` lists named columns MC no longer emits.
- **Restructure (pivot-backtester):** TTM now sums `period_kind='quarterly'` on the
  field's own statement, falling back to latest annual → `net_profit_ttm>0` 0→2,574,
  `roe>0` 0→652. Fixed `revenue`/`cash_from_operations` line_items. Promoted **15
  pre-computed `ratios` fields** (RoE/RoA/ROCE, margins, interest_coverage, D/E, P/B,
  EV/EBITDA, …). `line_items` lists are now an authoritative preference order. Guard:
  `tests/test_mc_field_contract.py` runs compiled SQL vs the live DB.
- **Trim (financials DB):** dropped dead scraper tables `scrape_jobs` (112k/34MB),
  `rate_bucket`, `raw_pages`, `appfeeds_probe` + the `v_job_progress` view. `mc` now =
  companies · daily_prices · statement_lines (+ `v_latest_*`). `docs/data_trim_2026-06-01.sql`.

### Phase 2.1 — winsorize + neutralize + composition (committed `c065941`)
- `winsorize(x,k)` (sigma-clip) + `neutralize(x)` (industry-demean via `industry_slug`;
  `sector` is empty). They **compose under rankings** — `decile(neutralize(roe))`,
  `zscore(winsorize(margin,3))` — via a new two-level `ranked_t`→`ranked` CTE (window
  funcs can't nest). 10 new tests; live: `decile(neutralize(roe))==10 AND D/E<0.5` → 53.

### Phase 2.3 — pairs / stat-arb engine (committed `d8a9bf2`)
- `backend/services/backtest/pairs/`: Engle-Granger cointegration + ADF + OU half-life
  (from scratch — no statsmodels), causal spread z-score strategy (trailing-window β+z,
  position lagged one bar = look-ahead-free), pairwise scanner, full rigor battery, REST
  `/api/backtest/pairs/run` + `/scan`. 14 deterministic tests (incl. the no-look-ahead
  proof). Live: scanner found AXISBANK/UNIONBANK cointegrated@1% among 91 pairs;
  cointegrated pairs still backtest `no_edge` (causal + rigor refuse a false edge).

### Phase 2.3 — pairs CHAT TOOL (run #1 — this commit)
- `backtest_pairs` + `scan_pairs` chat tools (`_pairs_chat_tools.py`) wired into the
  registry (visible set + dispatch + category) + `tools.py` defs + `system.md` routing.
  Compact, **verdict-led** summary (leads with cointegration + Trust verdict, not the
  return number). **In-process LLM routing eval — 3/3 PASS:** "pairs trade on HDFCBANK
  and ICICIBANK" → `backtest_pairs`; "is TCS/INFY cointegrated" → `backtest_pairs`;
  "find cointegrated pairs among SBIN/PNB/BANKBARODA/CANBK" → `scan_pairs`. The model
  relayed the honest "not cointegrated → no edge" call each time.

### Phase 2.3 — Johansen baskets (run #2 — this commit)
- **Johansen trace test** for ≥2-asset baskets (`cointegration.johansen`): VECM reduced-rank
  regression (unrestricted constant), eigenvalues of `S11⁻¹S01ᵀS00⁻¹S01`, trace stats vs
  Osterwald-Lenum critical values — implemented from scratch (no statsmodels). Returns the
  cointegration **rank** + the **cointegrating vector** (the stationary basket weights).
  **Validated like Engle-Granger — synthetic rank-0/1/2 all recovered** (decisive eigenvalue
  gaps; a wrong critical value would misclassify), and the rank-1 vector recovered as
  `[1, 1, −1]` for `x3 = x1 + x2 + noise`.
- `run_johansen(symbols)` (yfinance aligned closes → rank + weights mapped to tickers);
  REST `POST /api/backtest/pairs/johansen`; chat tool **`test_cointegration`** for baskets
  (distinct from `backtest_pairs` (2 stocks) and `scan_pairs` (pairwise)).
- **Routing eval 2/2 PASS:** "are RELIANCE, ONGC and BPCL cointegrated as a basket" and
  "Johansen test on TATASTEEL/JSWSTEEL/HINDALCO" → `test_cointegration`; the model relayed
  the honest "rank 0 → not cointegrated → no basket spread" each time. 5 new synthetic tests
  (19 in the pairs suite).
- **2.3 now complete** (Engle-Granger pairs + Johansen baskets + chat exposure for both).
  Only nice-to-have left: dedicated FE cards for the `pairs_backtest`/`pairs_scan`/
  `cointegration_test` render hints — chat renders the text summary today.

### Phase 2.4 — multi-position portfolio engine (run #1 — this commit)
- The tree engine (Engine 2b) is single-position/single-symbol, so 2.4's gross/net/max-names/
  sector caps are built as a NEW multi-symbol engine: `backend/services/backtest/portfolio/`.
  This also lands the 2.1 **momentum factor + dollar-neutral L/S short leg** that were "gated
  on prices" — OHLCV is yfinance.
- Pure causal core: `momentum_scores` (12-1 cross-sectional momentum, data ≤ t only),
  `target_weights` (constrained construction — **max names** + **gross budget** by
  construction; long-only equal-weight or **dollar-neutral L/S**), `simulate_portfolio`
  (drifting weights, one-bar-lag rebalance, turnover costs). `run_portfolio_backtest` fetches
  aligned yfinance closes, ranks, rebalances on a schedule, runs the full rigor battery.
- **Live (15 large-caps, monthly, 5y):** long-only momentum top-5 → +28.5%, −21% maxDD,
  gross 1.0 / net 1.0, verdict **unproven** (PSR 0.83). L/S top-5/bottom-5 → net **0.001**
  (dollar-neutral confirmed), verdict **no_edge** (momentum L/S added nothing this window) —
  honest, no manufactured edge. 10 deterministic tests (constraints, compounding, turnover
  cost, **two no-look-ahead proofs** — signal + simulation).
### Phase 2.4 — sector caps + REST + chat (run #2 — this commit)
- **Sector caps**: `target_weights` gained a per-sector NAME cap (greedy selection skips a name
  once its sector is full); `run_portfolio_backtest` takes `sector_cap` (fraction → max names/
  sector) using a **network-free curated `symbol→sector` map** (`sector_universe.symbol_sector_map`,
  80 NSE names). Live-confirmed it binds: a 1-name/sector cap on a steel-heavy basket moved the
  result 31.9% → 60.5% (forced diversification); at a looser cap that doesn't bind, the result is
  unchanged (correct). 11 deterministic tests now (added the sector-cap case).
- **REST** `POST /api/backtest/portfolio/run` (registered in main.py). **Chat tool**
  `backtest_portfolio` (`_portfolio_chat_tools.py`) — compact verdict-led summary; wired into the
  registry + `tools.py` + `system.md`.
- **Routing eval (honest):** clear phrasings route correctly — "top N momentum stocks out of
  [list]" and "long/short momentum on [list]" → `backtest_portfolio` (verdicts relayed honestly,
  e.g. L/S → no_edge). One borderline phrasing ("momentum portfolio of [list], hold top 5,
  rebalanced monthly") was mis-routed to `backtest_workflow` first; after sharpening
  system.md/tool-desc (explicit "multi-stock list → backtest_portfolio, NOT the single-symbol
  engines") it now declines-to-guess (`ASK_USER`) rather than mis-route. Not iterating the eval
  further (per discipline); the tool is fully functional via REST + 11 unit tests, and the
  wrong-tool failure is fixed.
- **2.4 complete** (max names + gross/net + sector caps + L/S, multi-symbol, REST + chat).

### Phase 2.5 — reference-strategy acceptance tests (this commit) → **Phase 2 COMPLETE**
- `tests/test_reference_strategies.py`: one canonical strategy per class, each asserted
  **through the Phase-1 rigor ladder** (a shared `_assert_rigor_ladder` checks
  forward_stats keys + monte_carlo + sub_periods + a verdict ∈ {insufficient_data, no_edge,
  unproven, promising}). A regression guard: break an engine, the look-ahead, or the rigor
  wiring and the matching reference fails.
  1. **single-symbol technical** — RSI(14)<30 mean-reversion on a SYNTHETIC fetcher (no
     network) → always-on guard for Engine 2b + the rigor wiring.
  2. **pairs / stat-arb** — HDFCBANK/ICICIBANK Engle-Granger spread (live) → cointegration
     verdict + rigor ladder.
  3. **momentum portfolio, long-only** — 10-name top-5 monthly (live) → gross ≤ 1 + ladder.
  4. **momentum portfolio, dollar-neutral L/S** (live) → net ≈ 0 + ladder.
  5. **fundamental factor screen** — `decile(neutralize(return_on_equity))==10 AND D/E<0.6`
     (live mc) → non-trivial selection.
  6. **Johansen basket** — RELIANCE/ONGC/BPCL (live) → rank verdict.
  - **6/6 pass live** (single-symbol always; the 5 live ones skip cleanly if yfinance/DB
    are down). Suites green: reference 6 + pairs 19 + portfolio 11 + expr 49.
- **Phase 2 is COMPLETE: 2.1 ✅ · 2.2 ✅ · 2.3 ✅ · 2.4 ✅ · 2.5 ✅.** The strategy classes
  pros run — single-symbol technical, fundamental factor L/S, pairs/baskets, momentum
  portfolios — are all testable and reported through one rigor ladder.
### Phase 1.4 — walk-forward + no-skill permutation (the rigor "middle")
- `backend/services/backtest/validation/walkforward.py` — the rigor middle, built on a
  **warmup-aware engine-rerun adapter** (every eval window is padded with `warmup` bars
  before its start so indicators stay warm — the naive clip-to-fold corruption the plan
  flagged is avoided):
  - **`permutation_test`** — shuffle the bar-to-bar returns (same distribution, random
    serial order), rebuild the price path, re-run the strategy, compare to that null →
    a p-value. **Validated discrimination:** a momentum rule on autocorrelated returns →
    observed +40% vs null +6.5% → **p=0.01 `beats_random`**; on an iid random walk →
    +21% vs +7.9% → **p=0.11 `no_skill`** (luck, not edge).
  - **`walk_forward`** — sequential out-of-sample folds, each re-run with its own warmup,
    stitched into one OOS curve + a `consistent_oos`/`inconsistent_oos` verdict.
  - **`deep_validate_engine2b`** — wires both to the single-symbol tree engine.
- **Exposed:** `POST /api/backtest/dsl/validate` (tree + symbol + window → permutation +
  walk-forward). EXPENSIVE (n_perm + n_folds re-runs) → opt-in, not in the per-backtest
  battery. 7 deterministic tests (perm math + discrimination + fold accounting + the
  Engine-2b adapter end-to-end on synthetic bars).
- **Remaining P1.4:** a chat tool ("is this overfit / better than random / does it hold
  out-of-sample") — deferred (the NL→tree translation + routing is a separate run).
- **Next:** P1.5 CPCV→PBO (needs a parameter grid); FE cards for pairs/portfolio.

---

## Day 13 — 2026-06-01 (lead) — Backtesting strengthening: research + plan + P0.1/P1.2 shipped

**New initiative: make our backtester the most *rigorous* one for algo/quant traders.**
Research + source-grounded audit + written plan, then the first two build items.
Full plan: [`docs/BACKTESTING_PLAN.md`](docs/BACKTESTING_PLAN.md).

### Build shipped — P0.1 look-ahead + P1.2 rigor + P1.6 MC + P1.3 trials + P1.7 sub-periods + P1.8 verdict + P1.9 FE card

- **P0.1 — fixed the verified look-ahead bug in the primary engine.**
  `services/workflow_backtester.py`: signal-driven orders (indicator / price /
  compound / exit_compound triggers — those that read THIS bar's OHLC) now fill
  at the **next bar's open** via a new `_next_bar_ts` helper + `_SIGNAL_TRIGGERS`
  set; schedule fires (known a-priori) still fill same-bar. The equity curve is
  rebuilt from the trade log keyed on each trade's stamped date, so shifting the
  fill bar also fixes the equity-curve leak (the position now appears only from
  the fill bar). Added a stable `trades.sort` before the walker as ordering
  insurance. Engine 2b (`dsl/backtest`) already did next-open correctly — this
  brings the two principal engines into agreement.
- **P1.2 — Deflated/Probabilistic Sharpe + MinTRL on EVERY backtest.** New
  `forward_stats.forward_stats_block()` (the single rigor lens already used by
  the live paper scorecards) wired into **all three** engines: chat
  `backtest_workflow` (Engine 2), `/api/backtest/dsl` (Engine 2b, new
  `ForwardStats` schema model), and `/api/backtest/expr` (Engine 1, in the
  router). The chat summary now states **"PSR NN% (confidence the Sharpe is
  genuinely > 0)"** — visible immediately, no FE change needed.
- **Proven live (real yfinance):** RELIANCE RSI(14)<35 dip-buy over 2y → 61
  trades, −1.3% return, **PSR 50%** (= no confidence the Sharpe is positive),
  kurtosis 7.33 (fat tails). The backtest now honestly says "don't believe this
  curve" — a verdict no Indian platform (Streak/Tradetron/AlgoTest/Sensibull)
  ships.
- **P1.6 (Monte-Carlo, part 1):** new `services/backtest/validation/` toolkit +
  `monte_carlo_robustness()` — circular-block-bootstrap distribution of max-drawdown
  + terminal wealth on every backtest (5%-worst drawdown, P(end in loss), P(DD > tol)).
  Wired into all 3 engines; the chat summary now also states "Monte-Carlo: 5%-worst
  drawdown −NN%, P(end in loss) NN%." **Live RELIANCE: P(loss) 53%, 5%-worst DD −29%.**
  Block bootstrap preserves vol-clustering so drawdowns aren't understated. (The no-skill
  *permutation* test — re-run on shuffled prices — lands with walk-forward's rerun adapter.)
- **P1.3 (trial counter — the keystone):** `services/backtest/validation/trials.py` — a
  per-session registry that deflates the **Deflated Sharpe for how many DISTINCT strategy
  variants a user has backtested** (multiple-testing selection-bias guard; Bailey & LdP). Wired
  into the chat path (`backtest_workflow` gains `trial_group`; the tool passes `u{uid}`). Each
  new variant raises N and the kept strategy's DSR falls unless the edge is real; re-running the
  identical strategy is deduped (not a new trial); 2h TTL = a "research session". The chat summary
  now also states "After N variants this session, deflated-Sharpe DSR NN%." **No Indian platform
  deflates for trials.** **Live RELIANCE: RSI<35→<30→<25 deflated DSR 0.497 → 0.504 → 0.461 as N
  went 1→2→3.** (Stateless `/api/backtest/dsl` + `/expr` opt in via a session id later — follow-up.)
- **P1.7 (sub-period robustness):** `services/backtest/validation/sub_periods.py` — splits the equity
  curve into contiguous spans and reports per-span returns, the fraction of spans that made money, and
  **`concentration`** (|largest span's log-return| / Σ|log-returns|): ~1/n = edge spread evenly (robust),
  near 1 = almost all the return from one window (fragile/regime-bet) — a time-concentration tell PSR/MC
  can't see. On every backtest; the chat summary shows "⚠ Fragile: NN% of the return came from a single
  sub-period" only when concentration > 0.6. Live RELIANCE: 2/4 spans positive, concentration 0.49 (fine).
- **P1.8 (Trust verdict — the capstone):** `services/backtest/validation/verdict.py` synthesises the whole
  battery into ONE ordered call + plain-English rationale + risk flags. Primary axis (statistical confidence):
  `insufficient_data` → `no_edge` (PSR < 0.6 or a loss) → `unproven` (edge possible, not established —
  needs more track record / deflated by trials) → `promising` (PSR ≥ 0.95 ∧ DSR ≥ 0.95 ∧ track ≥ MinTRL).
  Independent risk flags: `selection_bias` / `return_concentrated` / `drawdown_risk` / `loss_likely`. On
  every backtest (all 3 engines + new `TrustVerdict` schema model); the **chat summary now LEADS with the
  verdict**. Live RELIANCE RSI dip → **"Verdict — No demonstrable edge: PSR is only 50%…", flag `loss_likely`.**
- **Tests:** `test_workflow_backtester_lookahead.py` (4, first direct Engine-2 coverage)
  + `test_backtest_montecarlo.py` (6) + `test_backtest_trials.py` (7, incl. end-to-end deflation)
  + `test_backtest_subperiods.py` (5) + `test_backtest_verdict.py` (8). **542 passed**; only failures are
  pre-existing date-drift (`test_events_calendar`, now-past 2026-02 RBI date) + `test_step_types_catalog`
  catalog drift — both untouched by this work. New validation files are ruff-clean.
- **P1.9 (FE — make the rigor visible):** frontend-lead added a **"Trust" panel** to the chat backtest
  card (`pivot-next/components/chat/IndicatorBacktestCard.tsx`, +247 LOC; +45 LOC of types in `lib/api.ts`).
  Detail view: a color-coded verdict badge (emerald/amber/rose/zinc) + confidence + rationale, a 6-stat
  rigor row (PSR · Deflated-Sharpe+trials · MinTRL ✓/✗ vs n_obs · 5%-worst DD · P(loss) · concentration),
  and humanized risk-flag chips. Compact view: a small verdict pill by the return. All fields optional +
  guarded (old/short payloads render unchanged). The tool already passes `result.metrics` through
  (`tool_executor.py:738`), so it's wired end-to-end. tsc clean (only the documented pre-existing
  ChatDemo:750 error); no new lint. **Backtests now show "should I believe this?" visually, not just in text.**

### Live backtesting-prompt eval (19 turns, real Azure gpt-5.4-mini)

Ran the backtesting prompt shapes through `chat_service.handle()` in-process (the
`:8000` server is serving stale code → live `/chat` backtests fail with "internal
import error" until restarted). Full report:
[`tests/eval_results/BACKTEST_CHAT_EVAL_2026-06-01.md`](tests/eval_results/BACKTEST_CHAT_EVAL_2026-06-01.md).
Triad: median latency ~13 s (6.9–21.7); input 30k–133k tokens but ~90% cached
(≈ $0.004/turn — latency, not cost, is the tax); **no fabrication anywhere**.

- **P0 FOUND + FIXED (`5870e74`):** `backtest_dsl_tree` dropped the entire rigor
  battery — it built its own card payload and copied only the legacy metric keys, so
  **all of P1.2–P1.9 was invisible on ~⅓ of capable prompts** (every dsl-tree route).
  Now includes `forward_stats`/`monte_carlo`/`sub_periods`/`trust_verdict` + a
  verdict-led summary; verified in-process.
- **Open P1s (LLM-behaviour — need prompt work + a retest loop, not yet fixed):**
  (3) **crossover prompts fail** (SMA/MACD/EMA — the model tries a `trigger.indicator`
  crossover the historical engine rejects instead of routing to the compound
  translator — biggest capability gap); (4) **over-asking ASK_USER** on complete
  prompts instead of running with defaults; (5) **trial counter groups by user, not
  conversation** → `num_trials` over-counts across unrelated convs (fix: group by
  conv_id, needs threading it into the tool handler).
- **Solid:** the `backtest_workflow` path routes cleanly, returns real numbers, the
  full battery, and **honest verdicts** ("No demonstrable edge" on edgeless
  strategies). Options declined instantly (0 tokens); intraday explained as daily-only.

### Eval — fixes + retry on complex algo-trader prompts (`dd26dcb`)

Fixed the three P1s and retried with 15 **detailed** strategies (entry+exit+stop+
multi-condition). Report:
[`tests/eval_results/BACKTEST_CHAT_EVAL_RETRY_2026-06-01.md`](tests/eval_results/BACKTEST_CHAT_EVAL_RETRY_2026-06-01.md).
- **Crossover routing fixed.** The skeleton crossover guard only matched "cross…MA"
  (verb before MA), missing "SMA/EMA/MACD crossover" → it built a broken
  `trigger.indicator` shape instead of bailing to the LLM. Regex now catches either
  word order; `system.md` routes crossovers/multi-condition to `backtest_dsl_tree`.
  **Golden cross, EMA/SMA/MACD cross, stochastic %K/%D cross, Supertrend, Bollinger,
  RSI(2) mean-reversion all now run with the full battery + verdict** (were hard-fails).
- **Over-asking fixed.** `system.md`: run with defaults; after a backtest runs REPORT
  (0 trades is a valid finding) — never add an `ASK_USER` hop; interpret exit phrasings
  ("opposite cross", "after N days", "X% stop") literally. The 3 prompts that ran-then-
  asked now run cleanly (spot-checked).
- **Trial counter now groups by conversation, not user** (new `turn_context` contextvar
  threaded through `handle()`/`handle_stream()`) — tuning one idea deflates together,
  unrelated chats independent. Verified (conv A 1→2; conv B independent).
- Net: clean-run rate ~7/12 (run 1, crossovers failing) → **~12–13/15 complex** (only an
  external yfinance data miss for TATAMOTORS.NS remains). 526 tests pass.

### Eval round 3 — breadth + edges (no code change; findings only)

18 turns probing untested ground. Report:
[`tests/eval_results/BACKTEST_CHAT_EVAL_ROUND3_2026-06-01.md`](tests/eval_results/BACKTEST_CHAT_EVAL_ROUND3_2026-06-01.md).
**10/18 ran with the full battery.**
- **Indicator breadth works** — ADX, CCI, MFI, VWAP, Keltner, percentrank aggregator,
  position-aware exit (up8%/down4%), cross-asset relative-strength all ran with the battery.
- **The battery discriminates:** **d09 (RSI<40 gated on NIFTY>200-DMA) → "promising", DSR 0.96**
  — first strategy across 3 runs to clear the bar; the rest honestly "no edge".
- **Boundary handling correct:** "backtest a profitable strategy on a good stock" → ASKED (the
  run-with-defaults fix did NOT over-correct); options straddle → instant decline.
- **NEW top issue (P1): backtest tuning follow-ups mis-route.** "Now try RSI<25" → `get_indicator`
  (fetched live RSI); "and RSI<20" → `propose_workflow` (drafted an agent). A verb-less tweak isn't
  tagged as a backtest by the deterministic intent classifier (runs before the LLM), so the DSR
  deflation can't be observed across turns (it IS verified in isolation). Fix: `_backtest_followup`
  detection must catch verb-less tweaks after a backtest turn.
- Minor residual over-asking on *sizing/notional* (pairs leg-size, ₹-SIP notional); pairs is
  recognised but not auto-run. TATAMOTORS yfinance data unreliable (eval hygiene).
- **FIXED (`c85da90`) — the follow-up routing P1 + the deflation gap behind it.** New
  `_looks_like_backtest_tweak` detector + `_BACKTEST_TWEAK_RE`; `handle()`'s `_backtest_followup`
  block now fires on a verb-less tweak (gated by a prior backtest) and narrows the surface to the
  backtest tools, so "now try RSI<25" RE-RUNS the simulation (was get_indicator/propose_workflow).
  AND the trial counter — which was only wired into `backtest_workflow` — now deflates the DSR on
  the `backtest_dsl_tree` path too (via `turn_context` + `record_and_deflate`, verdict recomputed
  from the deflated battery). **Verified end-to-end: tuning RELIANCE RSI<35→<30→<25 shows trials
  1→2→3 and DSR 0.84→0.48 on the 2nd variant** — the selection-bias deflation finally visible
  across a chat. 515 tests pass.

### Phase 0 — consolidation (0.2–0.5 done)

Completed the plan's correctness/consolidation foundation (the rigor battery now sits on consistent ground):
- **0.2 CAGR unified.** Engine 1 (`pivot-backtester/.../metrics.py`) used a bar-count/252 CAGR (comment
  even lied "calendar") — wildly wrong on short windows. Now uses the **calendar span (365.25/yr)**; verified
  it matches `backtest_metrics.calendar_cagr_pct` exactly (100.09% vs 100.09% on a 1-yr double).
- **0.3 Engine 1 on the shared cost model.** The expr router now sets `slippage_bps`/`commission_bps` from
  `trading_costs` so Engine 1's round-trip reproduces `round_trip_bps()` (~37 bps incl. STT/GST) — was a
  naïve 10+3 bps (~26 bps, no STT/GST).
- **0.4 Vestigial `run_backtest` retired** (def + registry + dispatch + handler removed; 84→83 tools). It had a
  hardcoded 10 bps + 10%-of-capital sizing, no rigor battery, and rsi/price_cross weren't even implemented.
- **0.5 Parity test** (`tests/test_backtest_engine_parity.py`, 5 tests) locks the conventions so they can't drift.
- All new files ruff-clean; the only sweep failures are the pre-existing date/catalog-drift + a pre-existing
  `test_primitives`→`test_compare` event-loop ordering issue (chart_parser; passes in isolation; not my change).
- **0.6 DONE** — standardized + proved the no-look-ahead boundary: both engines' accessors
  (`_BarStrictAccessor`, `BacktestDataAccessor`) conform to the one `DataAccessor` protocol and pass an
  adversarial future-trap test (`tests/test_no_lookahead_engine2.py`). **Phase 0 COMPLETE.**

### Phase 2 started — 2.2 position sizing (`vol-target` / `ATR-risk` / `pct-equity`)

The highest value-per-effort strategy-coverage win: vol-targeting/ATR sizing is *where realised Sharpe
comes from* for trend/CTA — a fixed-share backtest mis-states it. Built in **Engine 2b** (the
`backtest_dsl_tree` path complex strategies take):
- New `Sizing` schema model + `_size_position`/`_atr_value` in the engine, wired into `_open_position`.
  Modes: `fixed` (→ `quantity`), `pct_equity`, **`vol_target`** (size to an annualised vol target),
  **`atr_risk`** (risk N% of equity/trade, stop at `atr_mult`×ATR). All **causal** (vol/ATR over bars
  BEFORE the entry — adversarially tested) and capped at **no-leverage**.
- Exposed on `/api/backtest/dsl/run` (the `sizing` field) AND the `backtest_dsl_tree` chat tool
  (`sizing_mode`/`target_vol`/`risk_pct`/`atr_mult`/`pct`); the chat summary notes the sizing.
- **7 sizing tests** (each mode's math, no-leverage cap, causality); existing dsl-backtest suite still 45/45
  (default `fixed` unchanged). **Proven live:** RELIANCE RSI<35 vol-target sized 13 entries to **49–88 shares**
  (varying with realised vol + equity) vs fixed's constant 10 — and the rigor battery + verdict still apply.
- Follow-up: sizing in Engine 2 + pyramiding/Kelly.

### Phase 2 — 2.1 cross-sectional transforms (ranked factor selection)

Engine 1 (the cross-sectional factor engine) could threshold a factor but not RANK it, so "long the
top-decile names" — the canonical quant-equity move — was inexpressible. Added cross-sectional functions
to the expression grammar that compile to SQL window functions over the universe at date T:
- `rank` (RANK) · `decile` (NTILE 10) · `quantile(x,n)` (NTILE n) · `zscore` ((x−AVG)/STDDEV) ·
  `percentrank` (PERCENT_RANK). So `decile(roe) == 10` = the top-decile-ROE names; the existing
  equal-weight runner trades them (long-only ranked selection works **today**).
- Implementation across the `pivot-backtester` package: `ast.Func` node, grammar func-call production +
  builder, validator (names/arity/quantile-int-literal/no-nesting), and the compiler — a `ranked` CTE
  computes the window columns over the survivorship-filtered universe (window functions can't sit in a
  WHERE), then `universe` filters the predicate on them; func-arg params bound before the predicate's.
- **Fixed a latent bug:** the language uses `==`/`!=` but SQL needs `=`/`<>` — the compiler emitted the raw
  op (never exercised against the DB since existing screens use `<`/`>`). Benefits every expression.
- **Proven by EXECUTING against live `mc` Postgres** — compiles, runs, and partitions the universe
  correctly (deciles/quantiles/percentrank behaved exactly as expected; mc's price table only has 9
  companies populated, so a large-N demo wasn't possible, but the partition math is verified).
- 15 new tests (`pivot-backtester/tests/test_expr_xs.py`); 37 package expr tests pass; pivot `test_compare`
  16/16. (The 8 package integration-test errors are pre-existing — the cleanup deleted the
  `pivot-mc-scraper/sql` their scratch-DB fixture needs.)
- **Remaining 2.1:** `winsorize`, `neutralize(sector|size|beta)`, a lagged-price momentum field, and the
  dollar-neutral **long/short short leg** (a runner change).
- **Next:** P1.4 walk-forward / sub-period robustness + the no-skill permutation test (needs the
  engine-rerun adapter), then P1.5 CPCV→PBO (needs a param grid, P2), then P1.9 the FE backtest
  card. Committed locally, **not pushed**.

### What was done
- **Web research (4 parallel threads):** (1) how best-in-class engines are architected
  (QuantConnect LEAN, Nautilus, Zipline-reloaded, VectorBT, Backtrader, + Indian platforms
  Streak/Tradetron/AlgoTest/Sensibull/Opstra); (2) the overfitting/validation science that is
  the real differentiator (Bailey/Borwein/López de Prado/Zhu — PSR/DSR/MinTRL, PBO via CSCV,
  CPCV with purge+embargo, walk-forward, Monte-Carlo permutation, White/Hansen SPA, haircut
  Sharpe); (3) advanced strategy classes + their specs (TSMOM/CTA, cross-sectional factor L/S,
  RSI(2) mean-reversion, cointegration/pairs + OU half-life, options, ML labeling); (4) where
  AI genuinely fits vs hype.
- **Source-grounded audit of our own engines** (`pivot-backtester/` + `workflow_backtester.py`
  + `dsl/backtest/` + `trading_costs`/`backtest_metrics`/`forward_stats`/`scorecards`). We have
  **~4–7 backtest code paths**, not 2.

### Headline findings (verified in source)
- **The differentiator is one import away.** `backend/services/forward_stats.py` already implements
  Probabilistic/Deflated Sharpe + Minimum Track Record Length (pure stdlib) — but it's used **only**
  by `paper/scorecards.py` on live paper NAV, **never at backtest time.** Wiring it into the backtest
  path + a trial counter puts us ahead of every Indian retail platform (none report DSR/PBO).
- **We already own the top and bottom of the "trust ladder"** (in-sample metrics + a live
  paper-trade forward-test, P0–P6) — we're missing the rigorous *middle* (walk-forward → CPCV/PBO
  → Monte-Carlo), which is ~400 lines of numpy.
- **Verified correctness bug (P0):** the *primary* chat engine `workflow_backtester.py:1573` fills
  `place_order` at the **same bar's** open on a signal computed from that **same bar's** close
  (`:577`) — a look-ahead bias. Engine 2b (`dsl/backtest`) already does this correctly (next-bar
  open, shadow-checked) and is the clean engine to standardize on.
- **Other must-fixes:** two divergent CAGR conventions (`backtest_metrics.py:5` documents its own
  inconsistency); Engine 1 off the shared cost model (naïve 10+3 bps, no STT/GST); survivorship
  bias on the whole yfinance technical stack; a vestigial `run_backtest` tool (hardcoded 10 bps).
- **Strategy coverage:** today we faithfully test single-symbol technical (after the look-ahead fix)
  and fundamental-factor *screens* — but **not** ranked cross-sectional L/S (Engine 1 can't
  rank/zscore/neutralize), **not** pairs/stat-arb (no 2-symbol object), **not** vol-targeted CTA
  (no sizing layer). Those are exactly what the plan's Phase 2 unlocks.

### Plan shape (full detail in the plan doc)
- **P0 Correctness & consolidation** — fix look-ahead, unify metrics/costs, one no-look-ahead data
  accessor, cross-engine parity test, retire the vestigial tool.
- **P1 Rigor layer (the moat)** — wire `forward_stats` into backtests + trial counter; build
  walk-forward, CPCV→PBO, Monte-Carlo permutation, cost-sensitivity, a "Trust verdict" on every run.
- **P2 Strategy coverage** — cross-sectional ranking/neutralization, position-sizing (vol-target/ATR),
  pairs/cointegration as a first-class object, multi-position portfolios.
- **P3 Data & execution realism** — survivorship-free PIT universe, pluggable LEAN-style reality
  models, effective-dated India STT/lot/freeze/circuit model, bars cache + vectorized fast-path.
- **P4 Intraday, options/F&O, AI surface** — minute bars; options chains+Greeks+multi-leg; LLM→
  sandboxed-PIT-DSL guardrails (route every chat-authored strategy through the rigor ladder).
- **Out of scope for v1 (stated for credibility):** market-making/HFT/LOB, end-to-end RL alpha.

**Recommended start:** P0.1 (fix look-ahead) + P1.2 (DSR on every backtest) — both small, both
high-signal. No code changed this session; nothing committed yet.

---

## Day 12 — 2026-05-30 (frontend-lead) — P6 Ideas scorecard components

### Shipped
- frontend-lead: built the two P6 forward-test scorecard components in `pivot-next/components/paper/` — `IdeaScorecards.tsx` (Ideas list: responsive idea-card grid, verdict chips, Dialog drill-in) and `IdeaDetailPanel.tsx` (per-idea drill-in: KPI row, dual decay chart with forward-Area + dashed backtest baseline both rebased to 100, semantic gates table). Both consume the existing `getPaperIdeas()`/`getPaperIdeaDetail()` fetchers + types from `lib/api.ts`; Quartr tokens only; tsc clean (only pre-existing ChatDemo:750 error remains) + eslint clean. Lead still owns wiring the PaperDashboard "Ideas" tab.

---

## Day 11 — 2026-05-04 (afternoon) — Two-LLM-hop audit: Changes 1 + 2 shipped together

**Validation-retry loop killed; deterministic resume after clarification.**

### What shipped

- **Change 1 — zero LLM retries on tool failure.** The agentic loop
  in `chat_service.handle()` and `handle_stream()` no longer feeds
  tool errors back to the model for self-correction. When a tool
  returns `success=False`, we build a deterministic question via
  `_format_recoverable_failure_question` and exit the loop on the
  same turn. `propose_workflow` keeps its macro-fallback path
  (deterministic, also no LLM) but the 3-attempt cap is gone — first
  failure either hits the macro or asks the user. Removed the
  `_PROPOSE_WORKFLOW_MAX_ATTEMPTS` and `_SAME_ERROR_LIMIT` tunables.

- **Change 2 — deterministic resume after clarification.**
  `ConversationStore` gained `set_pending` / `get_pending` /
  `clear_pending` (Redis SET / GET / DEL keyed `chat:pending:{conv_id}`,
  10-min TTL). When the completeness check surfaces a single missing
  field, chat_service persists `PendingToolCall(name, args,
  missing_field, type_kind, ...)`. On the user's next message, the
  new `_try_fast_resume` path checks pending, runs the cancel /
  multi-clause / type-shape off-ramps, coerces the value, and
  executes the tool — **zero LLM hops** on the resume turn. Cascading
  clarifications stay in the fast path until the tool finishes.

- **First-call robustness.** `system.md` gained a "Handling
  ambiguity (single-shot rule)" section that names the exact failure
  modes (M&M / Tata / colloquial tickers, ambiguous units, runtime-
  relative price refs) and instructs the model to call ASK_USER
  rather than guess. `place_market_order` and `create_gtt_order`
  field descriptions now spell out "if the user said an ambiguous
  company, ASK_USER" and "trigger_price is absolute INR, never a
  percentage."

- **File rename.** `validation_retry.py` → `validation_handler.py`
  to reflect that nothing in the chain retries against the LLM
  anymore. All imports + tests updated.

### Per-turn LLM hop count (before → after)

| Turn shape | Before | After |
|---|---|---|
| Tool succeeds (single tool) | 2 | 1 |
| Tool succeeds → model chains another | 3 | 2 |
| Missing field → ASK_USER | 1 | 1 |
| User replies with the value | 1 | **0** |
| Cascading: missing → reply → next missing → reply | 2 | **0** |
| Tool errors out | 2–8 | 1 |
| Pure-chat ask | 1 | 1 |
| Fast path / skeleton | 0 | 0 |

### Quality gates

- **Chat-related backend tests: 49 / 49 pass.** Added 4 new tests:
  `test_tool_error_returns_question_no_llm_retry`,
  `test_fast_resume_executes_tool_with_zero_llm_hops`,
  `test_fast_resume_cancellation_clears_pending`,
  `test_fast_resume_multiclause_falls_through_to_llm`.
- **Wider workflow + chat sweep: 348 / 350.** Two pre-existing
  failures unrelated to this change:
  `test_chat_render_hints::test_tool_summary_line_for_get_tool`
  (assertion text drifted from production months ago) and
  `test_step_types_catalog::test_every_step_type_present_with_correct_category`
  (catalog out of date with newer step types).
- Backend hot-reloaded cleanly through every edit. Live smoke:
  `localhost:8000/docs` 200, `localhost:3000` 200.

### Risks / rollback

Documented in the plan. Highest risk: `propose_workflow` errors
that the model used to fix on retry now surface as ASK_USER on the
first failure. Macro fallback covers the most common shapes; the
sharper tool descriptions + `system.md` ambiguity rule should make
the first call right more often. If quality drops on eval by more
than ~5 points, the rollback is to add back exactly one retry hop
scoped to specific error types (enum case normalisation, format
fixups), not a generic "fix the JSON."

### Files touched

- `pivot/backend/services/chat_service.py` — top-doc rewrite, added
  `_try_fast_resume`, `_maybe_set_pending`, `_is_simple_value_reply`,
  `_coerce_value`, `ValueCoercionError`; killed validation-retry
  branches in both `handle()` and `handle_stream()`; removed stale
  attempt-cap tunables.
- `pivot/backend/services/validation_handler.py` (renamed from
  `validation_retry.py`) — docstring rewrite, single-shot
  `execute_with_completeness` now sets `missing_field` on the
  `GuardedToolResult` for single-field cases.
- `pivot/backend/services/completeness.py` — `MissingField.type_kind`
  added (int / float / str / date / enum / bool / any) with
  `_kind_of(prop)` derivation.
- `pivot/backend/services/conversation_store.py` — `PendingToolCall`
  dataclass + `set_pending` / `get_pending` / `clear_pending`
  methods, Redis-backed with 10-min TTL.
- `pivot/backend/agents/tools.py` — sharpened `place_market_order`
  and `create_gtt_order` descriptions.
- `pivot/backend/prompts/system.md` — new "Handling ambiguity
  (single-shot rule)" section.
- `pivot/tests/test_validation_handler.py` (renamed),
  `pivot/tests/test_chat_service_with_stub_llm.py`,
  `pivot/tests/test_completeness.py`,
  `pivot/scripts/audit_llm_flows.py` — import / docstring updates,
  added 4 new tests, rewrote 1 retry-loop test for the new contract.

---

## Day 10 — 2026-05-03 (evening) — Prompt 2 complete

**Agentic loop live, schema-driven completeness, reasoning tuned per role, fast path active.**

### What shipped

- **Fast-path classifier** (`backend/services/fast_path.py`). Pure-Python pattern match; greetings / thanks / help asks return canned text in <1 ms with zero LLM calls. Strict equality after normalization + a `startswith + end` guard so "hello, what's RELIANCE's price" does NOT mis-route — that exact regression is locked in by 7 explicit pass-through tests.

- **Schema-driven completeness checker** (`backend/services/completeness.py`). Walks the tool's JSON Schema, flags required-but-missing or sentinel-valued fields. Pure Python, microseconds. Renders type hints to user-readable phrases ("integer ≥ 1", "ISO date (YYYY-MM-DD)", "one of: BUY, SELL"). Sits in front of arg validation so a missing field never reaches the executor or wastes a Pydantic call.

- **Agentic loop in `chat_service`**. Replaced the single-shot first-hop / second-hop pattern with a `while hop_index < MAX_TOOL_CALLS=8` loop. Each iteration appends the assistant's tool_calls + each tool's result to the message list; the model decides per turn whether to call another tool, ask the user, or finish. Errors get fed back as tool-result messages — no separate soft-failure-retry hop, the loop *is* the retry mechanism. Multi-tool reasoning ("compare RELIANCE PE to TCS and INFY") now works in one user turn.

- **`execute_with_completeness`** wraps tool execution. Order: completeness check (Python) → JSON-Schema arg validation → executor. ASK_USER intercept lives here too, with the empty-question guard. Latency tracked per-tool in the `latency_breakdown` dict.

- **Plan + draft split for `propose_workflow`**. Phase 1 = planning at `medium` reasoning (genuinely a reasoning task — what trigger type, what fetches, what conditions, does it even fit Pivot's shape). Phase 2 = JSON drafting at `minimal` reasoning (transcription only). Validation-retry inside `_propose_via_llm` skips re-planning since the original plan is implicit in the prompt + the embedded validation error. The mock fallback for LLM failures is still gone (Prompt 1 commitment).

- **Reasoning effort routed per role** (Prompt 2 §3 table): chat hop = `low`, narration = `minimal`, clarification-question generation = `minimal`, propose-plan = `medium`, propose-draft = `minimal`. Encoded in `chat_service.py`, `validation_retry.py::_generate_clarification_question`, and `workflows/propose.py`.

- **Tracing + admin endpoint** (`backend/services/chat_trace.py` + `backend/routers/admin.py`). In-memory ring buffer per conv_id (capped 25 turns × 100 events). Events emitted at every boundary: `turn.start`, `fast_path.matched`, `llm.call`, `llm.response` (with token counts), `tool.invoke`, `tool.result`, `turn.end`. Surfaced via `GET /admin/conv/{conv_id}/trace?limit=N`. Use this when chat output looks wrong — it's the cheapest way to see exactly what the loop did.

### Quality gates

- **Backend: 543 / 543 pass** (3 pre-existing pandas date-bug deselections unchanged).
- **Frontend: 210 / 210** (no FE changes this round).
- **75 new backend tests** across `test_fast_path.py` (46), `test_completeness.py` (23), `test_chat_trace.py` (6). Existing chat / propose / validation suites updated to match the new architecture.

### Live latency (Prompt 2 architecture)

| Prompt | Target | Actual | Status |
|---|---|---|---|
| Fast path "hi" | <100 ms | **0 ms** | ✓ |
| Fast path "what can you do" | <100 ms | **0 ms** | ✓ |
| Completeness gate ("buy some shares") | <2 s | 4.5 s | over (gpt-5-mini per-call floor) |
| Single tool ("price of RELIANCE") | <4 s | 8.0 s | over (2 hops × ~3.6 s each) |
| Multi-tool synthesis | 5–10 s | not measured live | passes stub test |
| Workflow propose (canonical) | <8 s | 36 s | over (3 hops: plan + draft + narrate) |
| Ambiguous order → ASK_USER | — | 30.9 s | recovers correctly |

The latency overshoots are gpt-5-mini's per-call floor (~3 s each at low reasoning, longer at medium). Architecture is correct; the slow path is the model. Mitigations parked in BACKLOG: drop narration to gpt-5-nano, prompt cache the system message, parallelise tool calls.

### Eval vs Prompt 1 baseline

`scripts/eval_chat_quality.py --diff openai_with_fixes prompt2` shows 3 changes, all neutral or improvements:

- `indicator_backtest_sma` — was `[]` (silent fail), now `run_backtest`. **Improvement.**
- `portfolio_summary` — `get_holdings` → `get_portfolio_summary`. Side-grade (both produce a complete answer).
- `calc_qty` — `calculate_order_qty` → `get_live_price`. Side-grade (model fetches price first, then computes).

Canonical workflow propose still produces `workflow_draft_card`; ambiguous prompts still escalate via ASK_USER. No quality regression.

### What's parked for Prompt 3

Logged in `BACKLOG.md` (created today):
- Streaming responses (FE doesn't consume `/chat/stream` yet)
- Few-shot examples in propose_workflow
- UserContext-rich system prompts
- Conversation history quality refactor
- Faster narration hop (gpt-5-nano? prompt cache?)

---

## Day 10 — 2026-05-03 (afternoon) — Prompt 1 complete

**GPT-5 mini wired, domain primer live, mock killed, validate-retry in place.**

### What shipped

- **`backend/llm/`** — provider-agnostic LLM contract.
  - `base.py`: `LLMClient` ABC + `LLMMessage` / `ToolDef` / `LLMResponse` Pydantic models. Token usage split into `input_tokens` / `output_tokens` / `reasoning_tokens` so reasoning-model billing is observable.
  - `openai_client.py`: `LLMOpenAI` over `/v1/responses`. Native function calling, reasoning effort, message-shape conversion (system/user/assistant/tool/tool_calls). Returns structured errors instead of raising on 4xx/5xx.
  - `sarvam_client.py`: refactor of the existing client into the `LLMClient` interface, preserving the prompt-injection emulation pattern (Sarvam-m rejects native `tools` payloads). 7K-context trimming + truncation retry kept.
  - `factory.py`: env-driven selection (`LLM_PROVIDER=openai|sarvam`, `LLM_MODEL=<name>`). Test override via `set_llm_client_for_tests`.

- **`backend/prompts/`** — role-aware assembly.
  - `domain_primer.md`: ~500 tokens of Indian retail trading reality (parameter ranges, sector linkages, workflow conventions, what NOT to do). Prepended to every system prompt across the platform via the assembler.
  - `assembler.py`: `build_system_prompt(role, user_context, extra_context)`. Roles: `chat`, `propose_workflow`, `narrate_tool_result`, `correlation_decompose` (placeholder for Prompt 2).

- **`backend/services/validation_retry.py`** — validate-and-retry tool wrapper.
  - `execute_tool_with_retry`: validates args against the tool's JSON Schema (required / type / enum / minLength). On failure, sends a terse error (`"quantity: Field required."`) back to the LLM as a tool-result message and lets it fix the args or call `ASK_USER`.
  - `ASK_USER` synthetic tool — the model's escape hatch when a required field can't be inferred. The wrapper intercepts before dispatching to the executor; the chat surfaces the question as the assistant message with `_render_hint: "ask_user"`.

- **Mock fallback in `propose_workflow` is dead.** The "LLM failed → emit hardcoded RELIANCE/buying-power template with a warning" path was a silent lie — users saw fabricated workflows regardless of their request. Now the second LLM-validation failure raises `ProposalValidationError` with the specific missing fields; the endpoint surfaces a `422` with `"I couldn't quite turn that into a workflow — {field}: required field missing. Try rephrasing with the specific values you want."`. The genuine no-LLM-key offline mock path is preserved for CI / demo recordings.

- **`chat_service.py`** rewritten through the abstraction. Uses `get_llm_client()` (no more direct `call_sarvam`), `build_system_prompt(role="chat")` for the first hop, `build_system_prompt(role="narrate_tool_result")` for the second. Tool execution always goes through `execute_tool_with_retry`. ASK_USER bubbles surface as the assistant message; validation failures after retry surface as a structured error (no card, `_render_hint: "validation_error"`).

### Quality gates

- **Backend: 462 / 462 pass** (3 pre-existing pandas date-bug deselections unchanged).
- **Frontend: 210 / 210 pass** (no FE changes this round).
- **48 new backend tests** across `test_llm_abstraction.py` (20), `test_validation_retry.py` (15), `test_prompt_assembler.py` (8), `test_chat_service_with_stub_llm.py` (5). Existing chat / propose suites unchanged.
- **Live smoke (`/chat` → OpenAI / GPT-5 mini)**: "What is the current price of RELIANCE?" → calls `get_live_price`, returns `"RELIANCE last traded at ₹1,436.00, up 0.41% (source: yfinance)."` Real data, INR formatting, no slang, no "Chill, dude."
- **Eval harness vs Sarvam baseline (`scripts/eval_chat_quality.py --diff baseline openai_with_fixes`)**: 18 prompts, 3 differences:
  - `market_status` (`[]` → `get_market_status`) — improvement.
  - `workflow_propose_3step` (`[]` → `ASK_USER`) — improvement (asks instead of failing silently).
  - `indicator_backtest_sma` (`run_backtest` → `[]`) — soft regression. Sarvam guessed defaults for the missing date range; GPT-5 mini is stricter.
  - All four order tools (market buy / limit / GTT / market sell) call the right tool with `_render_hint: "logic_card"`. The canonical 5-step workflow draft prompt produces `workflow_draft_card` end-to-end. The mock-fallback lie is gone.

### Behavioural changes the user will see

- **Order replies feel different.** GPT-5 mini drafts the LogicCard via the tool (`Prepared a market BUY order for 10 shares of RELIANCE...`), not pure prose. Confirm-button stays the commit moment.
- **Underspecified prompts get a question, not a fabricated workflow.** "Every Monday morning buy 5 INFY" → `ASK_USER` for clarification; "build me a strategy" → `422` from the propose endpoint with field-level reasons.
- **Voice is professional.** Earlier "Chill, dude. 😅" path no longer possible — system prompt explicitly bans slang/emoji and requires calm tone even on "wtf".

### What's not yet done (Prompt 2 territory)

- Multi-hop planner for `propose_workflow` (the current path is still single-shot LLM with one validation retry — fine for simple drafts, weak on multi-condition strategies).
- Correlation-decomposition role for event-driven workflows (placeholder in the assembler).
- Migrating the rest of `backend/agents/*` callers (`symbol_mapper`, `chart_parser`, `backtester/parser`) onto the LLM abstraction. They still hit `agents/sarvam_client.call_sarvam` directly. No urgency — those paths work; will migrate when their next refactor lands.

### Configuration

- `.env`: `LLM_PROVIDER=openai`, `LLM_MODEL=gpt-5-mini`, `OPENAI_API_KEY=…` (set Day 9). Sarvam stays available for cheap automated tests via `LLM_PROVIDER=sarvam`.
- `tests/conftest.py`: `DEMO_SEED_ON_REGISTER=0` so a freshly-registered test user starts empty (the demo seeder still runs in dev/prod).

---

## Day 8 — 2026-05-02

### Lead — Day 8 BE (in parallel with frontend-lead Phase 1)

Shipped 7 endpoints required by the FE 4-phase brief, in parallel with `frontend-lead` working through Phase 1 wiring. All endpoints under canonical `/api/*` envelope. 55 new tests; existing 207-test baseline unaffected (8 pre-existing test_compare/test_backtester failures are unrelated).

- **#47 — `GET/POST /api/conversations` + messages.** New `Conversation` + `ConversationMessage` tables. List/create/get/append/rename/delete + paginated messages (`?before=`). Auto-titles untitled convos from first user message. Ownership returns 404 (not 403) to avoid leaking existence. ConversationMessage uses Python-side `default=datetime.utcnow` for microsecond precision in SQLite. (`118b041`)
- **#51 — `/api/backtest/{fields,validate,run}` aliases.** Phase 2 brief uses these top-level paths; existing handlers live at `/api/backtest/expr/*`. Alias router delegates to the same callables. Both paths work; no behavioural drift. (`e3e1d5e`)
- **#50 — `GET /api/quotes/index/{symbol}/history`.** Phase 1 portfolio benchmark overlay. Maps `NIFTY50/SENSEX/BANKNIFTY/NIFTYMIDCAP100` → `^NSEI/^BSESN/^NSEBANK/^NSEMDCP50`. ^-prefixed input passes through. Same `SparklineResponse` shape as `markets/sparkline` so FE reuses one TS type. (`be6bb23`)
- **#49 — `GET /api/portfolio/performance?period=1M|3M|6M|1Y|5Y`.** Computes historical portfolio value series by multiplying each holding qty × yfinance close history, aligned on a unioned date index with forward-fill. Per-symbol fetch failures skipped silently. All-fail → 503. Empty holdings → 404. Includes starting/ending value + total return + %. (`a5cf8aa`)
- **#48 — `GET /api/events/calendar?from=&to=`.** Enumerates active workflows whose first step is `trigger.event` and surfaces upcoming events from a static 2026 macro calendar (RBI MPC outcome dates, quarterly results windows, daily FII/DII flow weekdays). Shape mirrors `/api/workflows/scheduled-runs` so the FE can union them. (`717ef92`)
- **#52 — `GET /api/stocks/{symbol}/automations`.** Phase 3 killer feature. Returns `{automations[], triggers[], past_fires[], scheduled[]}` for chart overlays. Symbol matching: top-level `config.symbol`/`symbol_filter` and nested `filter.symbol` (trigger.event). Active+paused workflows in scope; archived excluded. Past fires from `workflow_runs` (newest first, capped 40). Scheduled from cron triggers (5 forward fires/workflow). (`e3e4700`)
- **#53 — `GET /api/news?symbol=`.** Yfinance-backed news for the Phase 3 stock detail side panel. Tolerates both upstream payload shapes (old flat: `title/publisher/link/providerPublishTime`; new nested: `content.{title,pubDate,clickThroughUrl,provider,thumbnail}`). Filters titleless items. Verified live with RELIANCE — 3 real articles returned. (`9124429`)

**Quality gates (end of Day 8 BE):** 55/55 new tests pass (test_conversations 12, test_backtest_alias 4, test_quotes 6, test_portfolio_perf 6, test_events_calendar 9, test_stock_automations 11, test_news 7). Backend live on `:8000` with new schema applied. All endpoints curl-verified end-to-end with real yfinance + DB writes.

### Frontend-lead — Day 8

Shipped all 4 phases of the Day 8 FE brief.

- **Phase 1 (wiring)**: Chat → real `POST /chat` with rolling 20-message history and `_render_hint` draft card detection. Workflow editor → `createWorkflow`/`updateWorkflow`/`activateWorkflow`/`pauseWorkflow`/`runWorkflow` with spinner states and inline error display. RunView approval → `decideApproval` with loading/error. AppShell conversations sidebar → `GET /api/conversations`. CalendarTab TODO stub flipped to real `GET /api/events/calendar` unioned with `getScheduledRuns`. Portfolio performance TODO stub replaced with real `PerformanceChart` (see Phase 3 below). All `TODO(day8-be)` stubs cleared from source.

- **Phase 2 (Backtester)**: New `BacktestTab.tsx` — left column: DSL textarea with Cmd+Enter, field chips from `GET /api/backtest/expr/fields`, date range inputs, rebalance frequency chips, Run button; right column: equity curve Recharts LineChart with log/linear toggle, drawdown AreaChart, 7-metric strip (CAGR/Sharpe/Max DD/Calmar/Turnover/Hit Rate/# Companies), collapsible rebalance log accordion, audit appendix table. URL hash state persistence for expr/start/end/rebalance. Skeleton, empty, error states. 6 tests.

- **Phase 3 (Stock detail route)**: New `/stock/[symbol]` page with `StockDetailPage.tsx` — sticky back nav, `QuoteHeader` (symbol, name, exchange, sector, large price, ±change, 8-cell stats strip), `ChartCard` (1D/1W/1M/6M/1Y/5Y range buttons, Recharts LineChart, dashed ReferenceLine overlays from `GET /api/stocks/{symbol}/automations`), `SidePanel` (Fundamentals/News/Related Agents tab strip). All 4 live data sources wired. Portfolio holdings table: symbol cells now link to `/stock/[symbol]`. PortfolioTab `PerformancePlaceholder` replaced with `PerformanceChart` wired to `GET /api/portfolio/performance` + NIFTY50 benchmark via `GET /api/quotes/index/NIFTY50/history`; period selector 1M/3M/6M/1Y/5Y. CalendarTab unions `getScheduledRuns` with `getCalendarEvents`. New API wrappers: `getPortfolioPerformance`, `getIndexHistory`, `getCalendarEvents`. 7 new tests.

- **Phase 4 (polish)**: `CommandPalette.tsx` — Cmd+K opens cmdk Dialog with Navigation group (all 8 tabs) and Recent Conversations group; Esc closes; sr-only DialogTitle for a11y. Sonner `toast.success`/`toast.error` on every mutation in `workflow-editor-mock.tsx` (save/activate/pause/run) and `RunView.tsx` (approval approve/reject). Route error boundary `app/stock/[symbol]/error.tsx` with reset/go-home. CommandPalette mounted in AppShell. 5 new CommandPalette tests.

**Quality gates (end of Day 8 FE):** 21 test files, 165/165 vitest, `pnpm typecheck` clean, `pnpm lint` clean. 4 commits on `dev` (f39d6b8, 05bae71 + Phase 1+2 from prior session).

---

## Day 7 — 2026-05-02

### Frontend-lead — Day 7

- **#44 Dashboard (step 1)**: New `DashboardTab.tsx` — 4-index card strip wired to `GET /api/markets/indices` (emerald/rose signed change chips, hides silently on 503), serif "Good Evening, {name}!" greeting from `GET /auth/me`, 7 action chip row (Generate Report / Run Agent / Portfolio Health / Market Pulse / Top Movers / Earnings Calendar / News Digest) that prefill Chat textarea or route to Calendar, big chat input with Cmd+Enter. New `ActiveAgentsRail.tsx` — right-side panel with RUNNING/BLOCKED/IDLE status derived from `GET /api/workflows/{id}/runs?limit=1`, KV rows (MODEL/LAST/NEXT), VIEW AGENT link, category pill. AppShell rebuilt: left sidebar nav (Dashboard / Chat / Portfolio / News / Agents / Calendar / Screener) with active-dot indicator, global search input, avatar circle, `localStorage` conversation history section. News + Screener render honest "coming in v2" placeholders. New API functions: `getMarketIndices`, `getStockQuote`, `getSparkline`, `getMe`. ChatDemo extended with `prefill` + `onPrefillConsumed` props.

- **#44 Agents catalog (step 3)**: `AgentsTab.tsx` replaced with card grid — file-folder style cards with FILE NNN / QUANT|INCOME|TACTICAL|EVENT|PASSIVE header, risk pill (HIGH/MEDIUM/LOW color-coded), serif title ending with period, KV rows (METHOD/UNIVERSE/CADENCE/TURNOVER—/MIN TICKET—), footer VIEW AGENT + CAGR— placeholder. Grid: 1→2→3 col at sm/lg. Empty state CTA preserved.

- **#44 Stock snapshot card (step 4)**: New `StockSnapshotCard.tsx` — recommendation pill (change_pct derived: >5% STRONG BUY … STRONG SELL), big serif price, signed change + %, "Today HH:MM IST", 6 range chips (1D/1W/1M/6M/1Y/5Y) wired to `GET /api/markets/sparkline`, pure SVG area-fill sparkline, 8-cell stat grid (OPEN/DAY HIGH/DAY LOW/VOLUME/52W HIGH/52W LOW/MKT CAP/PE), Buy/Sell/Watchlist action buttons. ChatDemo: bare ticker detection (2-12 uppercase letters) renders snapshot inline instead of propose-workflow.

- **#44 Order draft card (step 5)**: New `OrderDraftCard.tsx` — status pill row (BUY|SELL · MARKET|LIMIT + draft ID + "est. fill < 1s"), 3-column body (INSTRUMENT with sparkline / QUANTITY / ESTIMATED COST with fees + cash% + Confirm button), mini sparkline via `GET /api/markets/sparkline?range=1M` hidden on 404.

- **#44 Calendar polish (step 6)**: Serif big heading "Month YYYY" + Today button + nav arrows in header; disabled category chips (Earnings / Dividends / IPOs / Macro) with `Tooltip` explaining "coming in v2"; Today pill in day detail and agenda date headers; primary-dot markers on scheduled run rows; `TooltipProvider` wrapper.

- **#44 News + Screener placeholders (step 7)**: Inline in AppShell as honest "coming in v2" empty states with appropriate icons.

- **Quality gates (end of Day 7)**: 146/146 vitest (was 140), `pnpm typecheck` clean, `pnpm lint` clean. 6 commits on `dev` (e237b5e → 2be0c96).

---

## Day 6 — 2026-05-02

### Frontend-lead — Day 6

- **#39 ChatDemo**: replaced the static `ChatPlaceholder` in the Chat tab with a working demo surface. Textarea submits `POST /api/propose-workflow` (live endpoint, no mock). Renders `WorkflowDraftCard` inline in a message thread; "Open in editor" calls `draftToWorkflow()` and mounts `AgentPanel` pre-filled. Loading skeleton (Bot icon + skeletons), error bubble with `error.message`, Cmd+Enter submit, example-prompt shortcut. New `proposeWorkflow()` in `lib/api.ts`. 10 new tests.

- **#40 Header metric strip**: portfolio value, day P&L (±), total P&L (± + %) always visible at the left of the AppShell header. Reads `getPortfolioSummary()` on mount, on every tab change, and every 30s via `setInterval`. Skeleton row while loading. Hides completely on error — never blocks tab navigation. INR `Intl.NumberFormat` formatting, lucide `TrendingUp`/`TrendingDown` icons, emerald/rose color coding, `dark:` variants. 3 new tests in `app-shell.test.tsx`.

- **#41 Light/dark mode toggle**: Sun/Moon button at the far right of the AppShell header. Reads `localStorage["pivot-theme"]` on mount; falls back to `prefers-color-scheme`. Applies/removes Tailwind `dark` class on `<html>`. Persists choice on click. Added `window.matchMedia` JSDOM stub to `tests/setup.ts`. 2 new tests.

- **#42 Demo seed agent**: "Create example agent" CTA button in `AgentsTab` empty state. Single click POSTs `DEMO_WORKFLOW` (already existed at `components/agent-panel/demo-workflow.ts`) via `createWorkflow()`, then reloads the list. Disabled while in flight ("Creating..."). Shows `error.message` inline on failure. 3 new tests.

- **Quality gates (end of Day 6)**: 140/140 vitest (was 122), `pnpm typecheck` clean, `pnpm lint` clean. Commit `f2ca49a` on `dev`.

---

## Team shape — current

**Lead + frontend-lead.** Reviewer retired. (Earlier 2026-05-02 the user said "frontend handed off to human dev" so frontend-lead was deprecated; later same day they reversed: "yes spawn the FE again". Memory `feedback_solo_backend_only.md` reflects current.)

- ✅ `frontend-lead` is spawnable for FE-only tasks (`pivot-next/` only). Sonnet 4.6, ≤300 LOC per task.
- ❌ `reviewer` stays retired. Lead owns `docs/`, `STATUS.md`, `BACKLOG.md` directly.
- ✅ Lead does all backend work + cross-cutting work (smoke script, walkthrough doc, etc.).
- ✅ `docs/HANDOFF.md` + `docs/API_CONTRACT.md` are the contract surface; smoke (`bash pivot/scripts/smoke_test_api.sh`) is the green/red signal.
- ✅ CORS includes `localhost:3000` + `localhost:5173`.

---

## Day 5 — 2026-05-02 (lead + frontend-lead)

### Shipped
- **#37 (lead) — `GET /api/workflows/scheduled-runs`.** New `routers/scheduled.py` (~145 LOC). Enumerates next-fire times for active `trigger.schedule` workflows in `[from, to]` via APScheduler `CronTrigger.get_next_fire_time` iterated forward. Caps at 500 items / 90 days. User-scoped, malformed-cron-on-stored-step skipped silently (defense in depth). 10 new tests; 188/188 backend total. Smoke +5 checks → 46/46. `API_CONTRACT.md §6.5` documents the endpoint with curl example; `ScheduledRun` type added to §11. Mounted **before** workflows_router so `/api/workflows/scheduled-runs` isn't shadowed by `/api/workflows/{id}`.
- **#31-#35 (frontend-lead, before rate-limit)** — five Day-5 FE tasks landed: `2ea8aa4` real-backend wire-up (AppBootstrap + auth-token plumbing), `6ebe971` WorkflowDraftCard (chat → editor flow), `690c3ca` RunHistory list, `4b2eede` AgentsTab with status filter chips, `2dff1d5` CalendarTab (month + agenda views, real `/api/workflows/scheduled-runs` no mock). 108 frontend tests pass.
- **#36 (lead, solo) — PortfolioTab.** New `components/agent-panel/PortfolioTab.tsx` (~370 LOC). Three sections: metric strip (portfolio value, day P&L, total P&L with %), sortable holdings table (symbol / qty / avg / LTP / P&L / day % / value, default value desc, click any header to flip sort), and an honest "Performance — coming soon" placeholder (no backing endpoint yet; per spec rule "never fake data"). Reads from legacy `GET /portfolio/{summary,holdings}` via a new `requestLegacy()` helper that strips `/api` from the base URL — `/portfolio/*` routes don't sit under `/api/*`. Both light + dark mode support, INR formatting via Intl, P&L colors emerald/rose. Loading skeleton, error with Retry, empty state. 7 new tests; 115/115 frontend total.

### Quality gates (end of Day 5)
- Backend: 188/188 pytest, 46/46 smoke checks against live uvicorn.
- Frontend: 115/115 vitest, `pnpm typecheck` + `pnpm lint` clean.
- Both servers up locally (port 8000 + 3000).

### What's left
- **#21 mypy `--strict` cleanup** — ~150 SQLA `Column[X]` errors. Tests pass; deferred to Day 8 buffer per build sequence.
- **Stitching done.** `AppShell` parent component shipped (~200 LOC) replacing the old Day-2 mock `app/page.tsx`. Sticky tab strip with Chat / Agents / Calendar / Portfolio, active tab persisted in URL hash, AgentPanel as a persistent overlay any tab can open. Default tab is Agents (highest demo value). Chat tab is an honest placeholder pointing at the legacy `frontend/` Vite app for the actual chatbot. 7 new tests; 122/122 frontend total.

### Demo path readiness (out of 14)
- Backend can serve all 14 demo steps end-to-end (verified via smoke + propose_workflow Python snippet).
- Frontend has every component built (WorkflowEditor, StepConfigDrawer, StepTypePicker, RunView, RunHistory, AgentsTab, CalendarTab, PortfolioTab) but the parent shell stitching them all into Chat / Agents / Calendar / Portfolio tabs isn't done. ~14/14 walkable in components-individually; ~0/14 walkable as a single user journey through tabs.
- Demo recording: not started. Day 9 in original plan.

### Next session
- Stitch the tabs into a parent shell so the demo path is one continuous user journey instead of N components mounted separately. ~30 LOC addition to `app/page.tsx` or a new `<TabBar>`.
- Optionally start `#21` mypy cleanup if buffer permits.

---

## Day 4 — 2026-05-02 (lead, solo backend)

### Shipped
- **#29 — Phase 4 polish: HANDOFF.md + 10-prompt validation suite + README update + mock-template fix.**
  - `docs/HANDOFF.md` (~330 lines) — single onboarding doc for the human FE dev. Covers: 30s mental model, get backend running locally (docker OR sqlite no-docker path, both 90s), the single `setBackendSource('real')` flip needed in `pivot-next/`, the wire format reference, error envelope decoder table, the chatbot tool result + `_render_hint` rendering instructions, WS frame types + reply pattern, CORS already configured, what's mock vs real vs cut on the backend, smoke test as the green/red signal, things they can ignore (engine invariants, idempotency, etc. — backend handles).
  - `tests/workflows/test_propose_validation.py` (21 tests) — 10 NL prompts × 2 suites (registry-valid draft + only-known-step-types) + 1 canonical-demo quality-attributes test (locks step sequence, place_order config, `requires_approval=true`, "Bought" not "Buyed"). Per ARCHITECTURE.md Day 6 mandate. Force-mock-mode fixture so suite is hermetic.
  - `pivot/README.md` updated with an "Agent System (Workflows v1)" section pointing at `docs/HANDOFF.md`, `docs/SYSTEM_WALKTHROUGH.md`, `docs/ARCHITECTURE.md`, `docs/API_CONTRACT.md`, `STATUS.md`. Added quickstart with both the smoke script and a direct propose_workflow Python snippet.
  - **Cosmetic fix in `propose.py` mock**: notify template was producing "Buyed"/"Selled" via naive `side.capitalize() + 'ed'`. Now uses `{"buy": "Bought", "sell": "Sold"}` mapping. The canonical demo prompt's email body now reads "Bought 10 RELIANCE".
  - **Quality gates:** 178/178 backend tests (was 157), 41/41 smoke checks.
- **#28 — `fetch.fundamental` real via yfinance + formal cut for `trigger.event`/`fetch.news`.** Replaced the `NotYetAvailableError` shim with a yfinance-backed lookup over `Ticker.info` (trailingPE→pe / forwardPE fallback, marketCap→mcap, returnOnEquity→roe, debtToEquity→de). Surfaces `period_end` from `lastFiscalYearEnd` when present. Raises `NotYetAvailableError` cleanly when yfinance returns no value (common for newly-listed symbols) or when its API rate-limits. 9 new tests cover every metric, fallback, missing-value, exception wrapping, and unsupported-metric. **`trigger.event` and `fetch.news` formally cut** in BACKLOG.md (no backing source in this repo; documented path back to real for v2). Backend test count: 157/157. Smoke 41/41.
- **#27 — Watchlist model + `action.update_watchlist` real.** New `WatchlistItem` model in `backend/models.py` (id, user_id FK, symbol, exchange, added_at, UNIQUE on (user_id, symbol, exchange)). Alembic migration `0002_watchlist.py`. Replaced the Day-3 `NotYetAvailableError` shim with a real upsert/delete that's idempotent on both sides (re-add no-ops, re-remove no-ops) so engine retries are safe by construction. Returns `{action, symbol, exchange, mutated: bool}`. 8 new tests cover happy paths, no-ops, two-user isolation, DB-level UNIQUE enforcement, and explicit BSE exchange. 148/148 in tests/workflows/. Smoke 41/41.
- **#26 — Price/indicator watcher + `fetch.indicator` real impl.** Closes the cut-order item that was the biggest functional gap.
  - `backend/workflows/scheduler.py` extended with `_poll_watch_triggers()` — runs every 60s during NSE market hours (`is_market_open() && is_trading_day()`), batch-fetches quotes for every active `trigger.price` workflow in a single Kite call (one request per symbol per tick, not one per workflow), evaluates `>` / `<` / `crosses_above` / `crosses_below` against the latest price.
  - **Crossing detection** persists `_last_price` (and `_last_value` for indicators) into `workflow_steps.config` between ticks, so `crosses_above`/`crosses_below` only fire on actual transitions — not on every tick where current is already past the threshold.
  - **Indicator triggers** evaluated per-workflow (yfinance + pandas_ta_classic computation is heavier than batched price quotes; per-workflow is OK at v1 N).
  - On match: creates a `triggered_by='price_alert'` / `'indicator_alert'` workflow_runs row, hands to engine. Same engine, same WS frames, same approval/cancel/etc. logic — the watcher is just another way to fire a run.
  - **`trigger.price` and `trigger.indicator` executors** now no-ops (return None) — by the time the engine reaches them the watcher has already created the run row. The executors just acknowledge.
  - **`fetch.indicator` real impl** (in `steps/fetches.py`): yfinance OHLC pull → `pandas_ta_classic.rsi/sma/ema/macd`. MACD returns the histogram (macd - signal) — most useful single-number value for a threshold trigger. Raises `NotYetAvailableError` cleanly on insufficient bars.
  - **16 new tests**: every threshold operator (`>`, `<`, `crosses_above`/`below` with prior + no-prior cases), market-hours short-circuit, fire-on-match, last-price persistence, two-tick crossing, no-quote graceful skip, fetch.indicator SMA/insufficient/unsupported.
  - **Quality gates:** `pytest tests/workflows/` 141/141 (was 125). `mypy --strict --follow-imports=silent backend/workflows/scheduler.py` clean. ruff clean on touched files.
  - **Smoke test still 41/41.**

---

## Day 3 — 2026-05-02 (lead, solo backend)

### Shipped
- **#25 — Path A: remaining stub executors implemented.** 8 of 9 stubs now real (1 → `NotYetAvailableError`):
  - **`wait.delay`** — `asyncio.sleep` for `duration_seconds` OR sleep until `until_time` (HH:MM in tz). Capped at 1h so a typo can't eat the time budget. Returns `{slept_seconds: int}`.
  - **`control.skip_if`** — evaluates the inner `condition` payload (numeric / market_status / time_window) and returns `{skipped_next: bool}`. The engine already honors this output (line 380 of engine.py) — marks the next step `skipped` without executing it.
  - **`condition.market_status`** — uses existing `is_market_open` + `is_trading_day` helpers; supports `open / closed / pre / post` (NSE timing). Shared matcher with `skip_if` so semantics stay in sync.
  - **`condition.position`** — delegates to `get_user_portfolio` (same source as `fetch.portfolio`); checks `held / not_held` for the symbol.
  - **`condition.time_window`** — passes when current time in tz is in `[start_time, end_time]` HH:MM. v1 doesn't cross midnight (raises clearly).
  - **`action.cancel_orders`** — lists pending orders via `get_orders`, filters by optional `symbol_filter` / `side_filter`, calls `cancel_order` for each. Idempotent (cancelling a cancelled order is a no-op via Kite mock). Returns `{cancelled_count, order_ids}`.
  - **`action.set_stoploss`** — places a Kite GTT sell with `trigger_price` as both trigger and limit. Quantity defaults to current holding for the symbol if not specified; raises clearly if no holding. Idempotent via the engine's `client_request_id`.
  - **`fetch.quote`** — Kite live quote first; if Kite mock returns only `last_price`, backfills OHLC + volume from yfinance (keyless). Raises `NotYetAvailableError` if both sources empty. Returns `{ltp, open, high, low, close, volume, asof}`.
  - **`action.update_watchlist`** → `NotYetAvailableError` per spec rule ("never fake data") — there's no Watchlist model in `backend/models.py` yet. Logged as v2 in BACKLOG.md (TODO: add the model).
  - **Cut-order items left as stubs**: `fetch.indicator`, `fetch.news`, `trigger.price`, `trigger.indicator`, `trigger.event` (all per the official cut order — they need separate watcher subprocess work that's out of scope for the demo).
  - **Tests** (25 new): every executor's happy path, edge cases, and failure modes. Sleep is patched via `_no_sleep` fixture so tests don't burn time. Total now 125/125 (was 100). Smoke test still 41/41 against live uvicorn.
  - **Quality gates:** `pytest tests/workflows/` 125/125. `ruff check` on touched files clean. mypy errors in `actions.py` are pre-existing from #14 SQLAlchemy `Column[X]` typing — not introduced by #25 (covered by #21 cleanup).

- **#24 — `propose_workflow` chatbot tool.** New `pivot/backend/workflows/propose.py` (~370 LOC). Translates a NL strategy into a validated `WorkflowDraft` with name / description / ordered steps / rationale. Path:
  - **Mock mode** (no SARVAM/OpenAI key): pattern-matches the demo prompt — extracts cron from "every weekday at HH:MM (AM|PM) IST", quantity from "buy/sell N" or "N shares/units", symbol from uppercase tokens (filters AM/PM/IST/NSE/etc), threshold from "over X" / "above X" — and emits a deterministic 5-step draft (`trigger.schedule → fetch.portfolio → condition.numeric → action.place_order → notify.message`) for the canonical demo. 3-step draft (`trigger → action → notify`) when no condition clause is present.
  - **LLM mode**: builds a focused system prompt with the full 24-type catalog + ref-namespace constraints, calls `route_and_call(STRUCTURED_JSON, json_mode=True)`, parses JSON tolerantly (markdown fences, leading prose, brace-balanced extraction), validates **every step config against the registry's Pydantic model**, retries ONCE on validation failure with the concrete error embedded in the system prompt so the LLM can self-correct. Falls back to mock with a warning if both attempts fail (chat surfaces best-effort draft + warning, never empty hands).
  - **Wired into chat:** registered in `agents/tools.py` (TOOL_SUBSETS adds `WORKFLOW_PROPOSE`, `AUTOMATION_CREATE` includes it), added to `_REAL_TOOLS` in `services/tool_registry.py`, handler `_propose_workflow` in `tool_executor.py` returns the draft dict with `_render_hint: "workflow_draft_card"` so the chat UI can render the inline "Open in editor →" card. Does NOT persist anything — frontend POSTs to `/api/workflows` when the user activates from the editor.
  - **Tests** (21 new): demo prompt → canonical 5-step draft, 3-step variant, cron parsing edge cases (PM→24h, weekday vs daily), every mock draft validates against registry, validate_draft rejects unknown step_type / non-trigger at step 0 / trigger after 0 / bad config / empty steps, _extract_json tolerates markdown fences + leading prose + nested braces + raises on no-JSON / malformed, catalog summary covers every category, LLM happy path (single call), retry-on-validation-fail (LLM gets actionable feedback), fallback-to-mock-after-2-failures with warning, empty intent rejected.
  - **Quality gates:** `pytest tests/workflows/` 100/100 (was 79). `mypy --strict --follow-imports=silent backend/workflows/propose.py` clean. `ruff check` on new files clean.
- **#23 — API curl smoke test + handoff polish.** New `pivot/scripts/smoke_test_api.sh` boots uvicorn against fresh sqlite, registers a user, hits every Agent System endpoint with curl, asserts canonical error envelope on failures, verifies CORS preflight from `http://localhost:3000`. **41/41 checks pass.** Doubles as living documentation — the human FE dev reads the script for copy-pasteable requests against a local backend. Captures: auth → catalog → workflow CRUD → state transitions (with 409 negatives) → manual run → runs list/get/cancel → approvals listing → archive → 401 envelope → CORS → bad-cron-at-activate-422. Added §13 (Quickstart for the frontend dev — every endpoint with curl example) and §14 (Reproducible smoke test) to `docs/API_CONTRACT.md` so the contract IS the integration guide.
- **#22 — Scheduler hookup for `trigger.schedule`.** New `pivot/backend/workflows/scheduler.py` (~225 LOC):
  - `compute_next_run_at(cron, tz_str, after?)` — uses APScheduler's `CronTrigger.from_crontab` + IANA tz; raises `InvalidCronError` on bad cron / unknown tz.
  - `upsert_workflow_schedule(db, workflow)` — sets `next_run_at` for `active` + `trigger.schedule`; clears for paused / archived / non-schedule trigger types. Called from activate / pause / archive routers.
  - `_poll_due_workflows()` — every 30s polls `workflows` for `status='active' AND next_run_at <= now()`, creates a `triggered_by='schedule'` run, recomputes `next_run_at` past now, hands the run to the engine. Uses `asyncio.to_thread` so the loop never blocks on sync DB I/O.
  - `register_workflow_scheduler(scheduler)` — attaches the poll job to the existing `AsyncIOScheduler` (extends, doesn't replace, per ARCHITECTURE.md §3).
- **Activate / pause / archive routers** call `upsert_workflow_schedule` so cron triggers actually fire after activation. Closes reviewer Day-2 edge case #1: bad cron at activate → 422 `validation_error` with `details.field='config.cron'` (no longer silently arms a dead schedule).
- **Startup wiring** in `backend/main.py` calls `register_workflow_scheduler` after `init_scheduler` so the poll job runs in production.
- **11 new tests** in `tests/workflows/test_scheduler.py`: cron computation (UTC, IANA tz, invalid cron, unknown tz), upsert behavior across all states + invalid cron, poll-creates-run for due workflows, poll-skips-paused, activate-rejects-bad-cron with 422.
- **Quality gates:** `pytest tests/workflows/` 79/79 pass (was 68 after #16). `mypy --strict --follow-imports=silent backend/workflows/scheduler.py` clean. `ruff check` on touched files clean. No `TODO/FIXME/XXX` in new code.

### Notes
- Test fixture `_scheduler_uses_test_db` works around a pre-existing SQLite + StaticPool cross-thread visibility issue: separate `SessionLocal()` opens in worker threads can't see flushed-but-uncommitted rows from the test session. Real Postgres handles this correctly — production code unchanged.
- The polling cadence is 30s. Cron resolution is per-minute, so worst-case latency between scheduled time and fire is 30s. Acceptable for v1.
- Price/indicator watcher (`trigger.price`, `trigger.indicator`) is NOT in this commit — that's a separate watcher, deferred per cut order. Rest of the demo path uses `trigger.schedule` + `trigger.manual`.

### Blocked
- None.

### At risk for 2026-05-17
- **mypy --strict debt (#21)** — backend has ~150 SQLAlchemy `Column[X]` vs `X` errors across engine, routers from Day 2. Tests pass; this is type-system noise, not behavior. Defer to buffer day (Day 8) per cut order.
- **propose_workflow LLM tool (Day 6)** — only critical demo blocker remaining for backend. ~4-6h of agent time when ready.

### Next session
- `propose_workflow` chatbot tool — Day 6 work, but can be brought forward if buffer permits.
- Manual `curl` smoke test of every Agent System endpoint against running uvicorn (integration-readiness for human frontend dev).
- API_CONTRACT.md polish: per-endpoint `curl` example block.

### Demo path readiness (out of 14)
Day 3: 0 / 14 walkable end-to-end (no live HTTP smoke yet), but every backing piece for steps 6, 7, 8, 13, 14 is now wired. After `propose_workflow` lands, demo path becomes exercisable end-to-end.

---

## Demo path readiness — 2026-05-02

Counted against the 14 demo steps in ARCHITECTURE.md §14:

- [ ] 1. Open the chat
- [ ] 2. Type: "Every weekday at 3:55 PM IST, if my buying power is over ₹50,000, buy 10 shares of RELIANCE and notify me by email."
- [ ] 3. See the chatbot propose a workflow
- [ ] 4. See the panel open with 5 steps: schedule → fetch portfolio → numeric condition → place order (with approval) → notification
- [ ] 5. Edit the order quantity from 10 to 5 inline
- [ ] 6. Click Activate
- [ ] 7. Click Run now
- [ ] 8. See live execution: each step lights up in sequence
- [ ] 9. See an approval banner when the order step requires confirmation
- [ ] 10. Click Approve
- [ ] 11. See the run complete
- [ ] 12. Open run history; see this run logged with all step outputs
- [ ] 13. Pause the agent
- [ ] 14. Re-open the agent later and edit it without losing config

Score: 0 / 14

---

## Day 2 — 2026-05-02 (reviewer)

BE / FE Day 2 work in flight; entries to be appended on completion.

### Shipped
- Task #20: Full Day 1 commit audit across all six commits (243c88d, 3d3ca14, e9295ab, e051a6f, 8ed728b, 37da922).
- Type safety check: mypy --strict on all new backend workflow modules (registry, schemas, refs, steps/*, routers/workflows.py, backend/schemas.py) — PASSES (errors are in pre-existing files config.py and auth/jwt_handler.py, explicitly excluded per commit message). events.py has 7 new type errors (see below).
- Frontend typecheck: `pnpm typecheck` (tsc --noEmit) — PASSES, no errors.
- Backend tests: `pytest tests/workflows/` — 14 / 14 pass.
- Frontend tests: `pnpm test` — 21 / 21 pass.
- Dead code grep (print, console.log, TODO, FIXME, XXX) in all new files — CLEAN.
- Migration ↔ ARCHITECTURE.md §4 parity check — see notes below.
- Catalog ↔ API_CONTRACT.md §8 parity check — all 24 step types present, correct categories.
- Frontend mock catalog ↔ backend catalog cross-check — discrepancies found; see notes.
- Generated 5 edge-case tasks for Day 3+.

### Reviewer notes — Day 1 audit

**Issue 1 — BLOCKING (backend-lead): `events.py` fails mypy --strict (7 errors)**
`backend/workflows/events.py` shipped in commit 3d3ca14 but was not listed in the modules the backend-lead claimed were mypy-clean. All 7 errors are `Missing type arguments for generic type "dict"` — the `asyncio.Queue[dict]` and other `dict` uses lack type parameters. This is mypy --strict Day 9 blocker material if unfixed; fix now while the file is fresh. The fix is to replace bare `dict` with `dict[str, Any]` throughout. This is a non-trivial file (WS fan-out bus) that Day 2-3 code will call. Filed as task for backend-lead.

**Issue 2 — BLOCKING (backend-lead): Auth 401 errors not in contract error envelope**
`backend/routers/workflows.py` raises `HTTPException(status_code=401, detail="Missing token")` and `detail="Invalid token"`. FastAPI's default serialization produces `{"detail": "..."}`, NOT the `{"error": {"code": "...", "message": "..."}}` envelope specified in API_CONTRACT.md §2. Every other router (when this sprint writes them) must use the envelope. The test suite currently checks for `status_code == 401` but not the body shape. This will break the frontend's `isError()` discriminator for auth errors. Fix: raise `HTTPException` with a custom error body via a FastAPI exception handler, or return `JSONResponse` directly.

**Issue 3 — Non-blocking (both leads, note): ARCHITECTURE.md §4 says `user_id UUID` but actual `users.id` is Integer**
The ARCHITECTURE.md §4 DDL says `user_id UUID NOT NULL REFERENCES users(id)`. The real `users` table (pre-existing) uses `Integer` PK. The migration and Workflow model correctly use `Integer` to match reality. This is a doc error in ARCHITECTURE.md §4, not a code error — the code is correct. The doc was written before examining the existing users table. Will update ARCHITECTURE.md §4 to say `INTEGER` to prevent future confusion. No code change required.

**Issue 4 — Non-blocking (frontend-lead, note): output_schema mismatch between backend and mock catalog**
The frontend `lib/mock-catalog.ts` output schemas for several steps differ from what the backend registry emits. These will reconcile on Day 5 when the frontend swaps to the real endpoint, but the mismatches mean any test against output_schema content would fail:
- `action.place_order`: backend has `{order_id, status, client_request_id}`; mock has `{broker_order_id, submitted_at}`.
- `action.cancel_orders`: backend has `{cancelled_count, order_ids}`; mock has `{cancelled_ids}`.
- `action.set_stoploss`: backend has `{trigger_id, client_request_id}`; mock has `{broker_order_id}`.
- `notify.message`: backend has `{channel, delivered}`; mock has `{sent_at}`.
- `fetch.quote`: backend icon is `bar-chart-3`; mock icon is `line-chart` (cosmetic, no functional impact).
- `condition.numeric`: backend icon is `equal`; mock icon is `git-branch` (cosmetic).
These are non-blocking for Day 2 (the mock is intentionally temporary) but must be reconciled before Day 5 wire-up. Frontend-lead should update mock schemas to match the backend registry output, or at minimum acknowledge the gap.

**Issue 5 — Non-blocking (backend-lead, note): mock catalog test does not cover max_retries per step type**
The frontend `tests/lib/mock-catalog.test.ts` does not have a test asserting max_retries values (e.g. fetches=3, actions=1, notify.message=2). The backend catalog test does (test_max_retries_match_invariant_3). If a frontend-lead edits the mock catalog and sets wrong retry counts, nothing catches it until the swap to the real endpoint. Non-blocking for Day 2 but should be added.

**Positive findings:**
- All 24 step types present in both backend registry and frontend mock catalog.
- Correct category assignments across all 24 types in both backend and frontend (including the renamed `control.skip_if`, `wait.approval` under `notify`, `wait.delay` under `control`).
- No hardcoded step configs in frontend components — all driven from catalog.
- Webhook tokens confirmed to be in `workflow_webhook_tokens` table, not in `workflow_steps.config`. Security invariant upheld.
- `refs.py` correctly implements the `context.webhook_payload.*` namespace per the Day 1 contract fix.
- Migration enum values match spec exactly. All six tables present. All indexes match spec.
- Frontend `lib/types.ts` matches API_CONTRACT.md §11 precisely, including `RunSummary` with `step_count`.
- Frontend WS client has the 2s polling fallback and "reconnecting" state per §10.1.
- No `print()` or `console.log()` debug statements in any new file.
- No TODO/FIXME/XXX without context in any new file.

### Edge cases filed for Day 3+

1. `POST /api/workflows/{id}/activate` with a `trigger.schedule` whose cron expression is invalid (e.g. `"99 99 * * *"`) — does the engine reject it with 422 at activation time, or does it silently arm a schedule that never fires? Backend must validate cron syntax at activation, not just config schema.
2. `PATCH /api/workflows/{id}` with `steps=[]` (empty list) — the spec says this fully replaces the step list. Does the engine reject a 0-step workflow at activation, or only at run time? Should be a 422 at activation: "Workflow must have at least one step (a trigger at index 0)."
3. `POST /api/webhooks/{token}` fires for a `trigger.price` workflow (not a `trigger.webhook` workflow) — the token lookup finds a matching row, but the step_type at that step_index is wrong. What is the response? The engine should verify the referenced step is `trigger.webhook` before proceeding.
4. WS client on `pivot-next/lib/ws.ts` enters the 2s polling fallback. If the polling `getRun()` call itself returns an error (e.g. 401 expired token mid-session), the fallback loop will call `onError` on every tick and spam the UI. The fallback should back off on repeated polling errors, not just on WS reconnect failures.
5. `POST /api/workflows/{id}/run` is called while a run for the same workflow is already `awaiting_approval` (not `running`). The single-instance advisory lock is acquired at run start — but a run in `awaiting_approval` state has already released the lock (or has it?). If not, a user can never manually re-run while waiting for approval. This interaction between `single_instance` locking and approval gating needs a test.

### Blocked
- None blocking the leads' Day 2 work.

### At risk for 2026-05-17
- **`events.py` mypy errors (7 errors, Issue 1 above)**: This file ships Day 2 logic (WS streaming). If the engine imports it on Day 2 and mypy errors compound, the count will grow. Fix now costs 15 minutes; fix on Day 9 costs a panic. Backend-lead must fix before Day 2 engine PR.
- **Auth error envelope (Issue 2 above)**: Every new router that ships Day 2-3 must use the correct error format. If this pattern (bare HTTPException) is copy-pasted into the 6+ new routers (runs.py, approvals.py, webhooks.py, run_stream.py), rectifying 20+ call sites on Day 9 is a real risk. Backend-lead must establish the correct pattern in a base exception handler TODAY.
- **Output schema drift (Issue 4 above)**: Day 5 wire-up is the integration checkpoint. If the mock catalog's output schemas are wrong, any component that reads `output_schema` to render step output (e.g. RunView) will render broken UI. Low risk now; medium risk by Day 5.

### Frontend-lead — Day 2
- Shipped tasks #17, #18, #19. Plus reviewer fixes #4 + #5 absorbed in a parity commit.
  - **#17 StepConfigDrawer:** `lib/json-schema-to-zod.ts` (hand-rolled JSON-Schema → zod adapter, supports string/number/integer/boolean/enum/object + required, throws `UnsupportedSchemaError` on `array` / `$ref` so v1 never silently drops fields). `lib/refs.ts` (4-namespace validator + chip-picker suggestion builder; `context.webhook_payload` gated on a `trigger.webhook` step at index 0). `components/agent-panel/StepConfigDrawer.tsx` — secondary drawer with form generated dynamically from `catalogEntry.config_schema`; label / description / placeholder all from JSON-Schema fields; Cmd+Enter saves; Esc closes (scoped, doesn't bubble); 422 path highlights `details.field` and renders `error.message` verbatim. `RefChipPicker.tsx` autocomplete listbox opens on `{{`, suggestions filtered to valid namespaces, live ref-validation surfaces inline. Wired into WorkflowEditorMock — clicking a step card or selecting "Edit step" opens the drawer pre-filled with the step's current config.
  - **#18 StepTypePicker:** `components/agent-panel/StepTypePicker.tsx` shadcn `Command` palette in a Dialog. Categories rendered from `catalog.categories` in server-supplied order (no hardcoded list). Single-track invariant: at insertIndex 0 only the 6 trigger.* types render; at index > 0 every trigger.* is hidden. cmdk search filters across `step_type label description`. Add-step buttons (between steps and at the end) trigger the picker; on select, a new step is inserted with config seeded from `defaultConfigFromSchema()` and indices renumbered.
  - **#19 RunView:** `lib/mock-run.ts` 5-step deterministic simulator emitting frames in the exact API_CONTRACT.md §10 shape (snapshot → step_update / approval_requested / run_update), including a 2s `awaiting_approval` pause at step 3 that auto-resumes. `lib/api.ts` got `setBackendSource('mock'|'real')` as the single global toggle for catalog + run-stream. `lib/use-run-stream.ts` hook wraps mock-run / `openRunStream` and exposes `{ run, isReconnecting, error, pendingApprovals }`. `components/agent-panel/RunView.tsx` paints status-coded step rows (pending grey, running pulsing blue, succeeded green, failed red, skipped slate italic, awaiting_approval amber); each row expand-to-detail (output JSON pretty-printed, error_message, duration via `formatDistanceStrict`, attempts); approval banner with Approve / Reject (Day 2 mock — Day 5 will hit `decideApproval`); reconnecting pill on `connection_state="reconnecting"`. Mounted in `app/page.tsx` behind a "View run" button so it's reviewable without the chat.
  - **Reviewer #4 + #5:** mock-catalog `output_schema` for `action.place_order` / `action.cancel_orders` / `action.set_stoploss` / `notify.message` realigned with backend registry truth (e.g. `place_order` now `{order_id, status, client_request_id}`). Test now asserts `max_retries` matches ARCHITECTURE.md §7 invariant 3 for every step type, plus snapshot-locks output_schema parity for the 4 drifted types — locks parity for the Day-5 wire-up.
- 79 frontend tests pass (up from 21 on Day 1). `pnpm typecheck && pnpm lint && pnpm test && pnpm build` all clean.
- `setBackendSource("real")` flips every Day 2 surface to the live backend in one call — Day 5 wire-up is one line at the app entry.

### Next session (reviewer Day 3)
- Review Day 2 backend PRs: engine.py, REST endpoint implementations (POST/GET/PATCH workflows, activate/pause/archive/run, GET runs, cancel, approvals, webhook, WS stream). Check every new router against API_CONTRACT.md §2 error envelope.
- Review Day 2 frontend PRs: WorkflowEditor (real), StepConfigDrawer, StepTypePicker, any run-view wiring.
- Re-run full test suite; verify events.py mypy issue resolved.
- Verify Issue 2 (auth envelope) fixed before more routers ship with the same pattern.
- Walk any demo steps now exercisable (likely still 0/14 — needs live endpoints).
- Generate Day 3 edge cases.

### Demo path readiness (out of 14)
Day 2: 0 / 14. No live runtime yet — Day 2 is when engine + REST endpoints begin landing. Demo path will not be walkable until Day 4 at earliest (per build sequence ARCHITECTURE.md §15).

---

## Day 1 — 2026-05-02 (reviewer)

### Shipped
- Contract audit: cross-checked ARCHITECTURE.md and API_CONTRACT.md end-to-end. All findings resolved with direct doc edits (reviewer authority as contract owner). See "Decisions locked" below.
- Demo path readiness checklist added to STATUS.md (above).

Backend-lead / Frontend-lead Day 1 work in flight; entries to be appended by them on completion.

### Backend-lead — Day 1
- Shipped tasks #7, #8, #9: Alembic migration `0001_workflows.py` (6 tables, 3 PG enums, JSONB, advisory-lock-ready FK structure, `triggered_by` CHECK constraint), SQLA 2.0 models (`Workflow` / `WorkflowStep` / `WorkflowRun` / `WorkflowRunStep` / `WorkflowApproval` / `WorkflowWebhookToken`) + Pydantic v2 schemas covering every API_CONTRACT.md §3-§4 + §8.1 shape, full step-type registry with all 24 v1 step types and stub executors raising `NotImplementedError`, and `GET /api/step-types` mounted in main.py. Absorbed both reviewer Day-1 contract fixes: `control.skip_if` rename, `webhook_payload` ref namespace ruling (no Day-1 code ships refs.py yet — rule absorbed for Day 2-3 implementation). 14 workflow tests pass (5 model smoke + 9 catalog contract); ruff + mypy --strict clean on new modules. `jsonschema==4.23.0` added to `pivot/requirements.txt` for Day 2 engine-side config validation.

### Frontend-lead — Day 1
- Shipped tasks #10, #11, #12: scaffolded `pivot-next/` (Next.js 15 app router, TypeScript strict, Tailwind, ESLint, vitest + RTL) with the full pinned shadcn primitive inventory + `@dnd-kit/sortable`, `react-hook-form`, `zod`, `lucide-react`, `date-fns`. Hand-wrote `lib/types.ts` from API_CONTRACT.md §11 (incl. the new `RunSummary` with `step_count`), built `lib/api.ts` returning `Promise<ApiResult<T>>` with auth-token / idempotency-key / 5-min catalog-cache plumbing, `lib/ws.ts` typed run-stream client with 2s `getRun()` polling fallback per §10.1, and `lib/mock-catalog.ts` containing all 24 v1 step types with the canonical category mapping from §8 (`control.skip_if` rename absorbed; `wait.approval` under `notify`; `wait.delay` under `control`). Built the persistent right-side `AgentPanel` (custom resizable drawer, NOT shadcn `Sheet`), Esc-to-close, draggable left edge clamped 420-920px, mounted in `app/page.tsx` behind an "Open agent panel" CTA. Renders the 5-step demo workflow (schedule → fetch portfolio → numeric condition → place order with approval → notification) via `WorkflowEditorMock` with `@dnd-kit/sortable` reordering and Add-step dividers, fed entirely from the mock catalog so swap to real `/api/step-types` on Day 5 is one toggle. Empty / loading / error states present (Skeleton during catalog fetch, error state rendering `error.message`). 21 tests pass (5 panel + 6 mock catalog + 4 api wrapper + 4 config-preview + 2 button sanity); `pnpm typecheck && pnpm lint && pnpm test && pnpm build` all clean.

### Contract audit — findings and fixes

**Fix 1: `skip_if` renamed to `control.skip_if`.**
All other step types follow `category.subtype` dotted notation. `skip_if` was a bare identifier with no category prefix, making it impossible for the frontend to determine its category from the step_type string alone. Renamed to `control.skip_if` in ARCHITECTURE.md §5.6. API_CONTRACT.md §8 category table updated with explicit category assignment for all 24 v1 step types.

**Fix 2: `webhook_payload` ref namespace — definitive ruling.**
ARCHITECTURE.md §6 defined allowed ref namespaces (`context.<step_index>.<path>`, `now`, `workflow.<field>`). API_CONTRACT.md §9.1 used `{{ webhook_payload.<path> }}` which introduced an undocumented fourth namespace. This is now resolved: `webhook_payload` is NOT a sibling namespace. It is stored as a reserved literal key in the `context` bag (`run.context["webhook_payload"]`). The correct ref syntax is `{{ context.webhook_payload.<path> }}`. Both docs updated.

**Fix 3: `RunSummary` type and `step_count` field.**
`GET /api/workflows/{id}/runs` list items include `step_count` (not in the canonical Run shape). The existing doc said "Run shape (§4) but without `context` and `steps[]`" which obscured this additive field. Added an explicit `RunSummary` TypeScript type in API_CONTRACT.md §11, and clarified the §6.1 description to name the field and state it is list-view only.

**Fix 4: Category assignment table for all step types.**
API_CONTRACT.md §8 only showed two example step types in the catalog response. The backend lead had no normative reference for what `category` value to assign to `wait.approval`, `wait.delay`, or `control.skip_if`. Added a complete table covering all 24 v1 step types with their canonical `category` values.

**No-change findings (documented for leads):**
- `triggered_by` values: ARCHITECTURE.md §4 SQL comment already includes all 6 values (`schedule`, `manual`, `webhook`, `price_alert`, `indicator_alert`, `event_alert`). Matches API_CONTRACT.md §11. No change needed.
- `workflow_status`, `run_status`, `step_status`: consistent across both docs.
- `halt_reason` values: consistent.
- `error.code` list: all codes used in later sections are in the §2 stable list. No unlisted codes found.
- All 16 endpoints in ARCHITECTURE.md §9 have full request/response shapes in API_CONTRACT.md §5-§10.
- `PATCH /api/workflows/{id}` blocks on `status='active'` — intentional; client must pause first. Consistent between docs.

### Blocked
- None blocking leads today. Day 1 work can proceed.

### At risk for 2026-05-17
- **`control.skip_if` rename** — this changes the step_type string from the original spec. If backend-lead has already committed any code referencing `skip_if`, they must update it. Flag: check their Day 1 PR for any hardcoded `'skip_if'` string.
- **Webhook executor writes `run.context["webhook_payload"]`** — this is now a contract requirement, not optional. Backend-lead must write the raw body to this key before handing off to the next step. If they miss it, any workflow referencing `{{ context.webhook_payload.* }}` will fail with a ref-not-found error. Verify in their executor test.
- **`step_count` in `GET /api/workflows/{id}/runs`** — backend must compute and return this field. It's not in the main `workflow_runs` table (it's a join count against `workflow_steps` for the relevant `workflow_version`). Backend-lead should note this is a derived field.

### Next session (reviewer Day 2)
- Read backend-lead Day 1 PR: check migration DDL matches ARCHITECTURE.md §4 exactly (enum values, column names, nullable/not-null), check Pydantic models match API_CONTRACT.md §3/§4, check `GET /api/step-types` response matches §8 (all 24 step types, correct category assignments per the new table, correct `max_retries` per ARCHITECTURE.md §7 invariant 3).
- Read frontend-lead Day 1 PR: check TypeScript strict mode is on, check `pivot-next/` compiles without errors, check AgentPanel shell uses the correct API types from API_CONTRACT.md §11.
- Generate Day 2 edge cases.

### Demo path readiness (out of 14)
Day 1: 0 / 14. (No runtime code shipped yet; docs locked.)

---

## Day 0 — 2026-05-02 (lead)

### Shipped
- `docs/ARCHITECTURE.md` — full architecture: data model, step catalog, engine invariants, scheduler, API surface, chatbot integration, stack decisions, build sequence, scope discipline.
- `docs/API_CONTRACT.md` — REST + WebSocket contract: error format, every endpoint with request/response shapes, step-type catalog response, WS frame schema, frontend state types.
- `STATUS.md` (this file) — seeded.
- `BACKLOG.md` — seeded with the explicit "do not build" list and v2 ideas.
- Project memory persisted: Pivot Agent System sprint context, repo layout, dev-branch / no-push rule.

### Decisions locked (vs. spec doc)
1. **Backend paths:** `pivot/backend/workflows/`, `pivot/backend/routers/{workflows,runs,approvals,webhooks,run_stream}.py`, `pivot/backend/agents/tools/propose_workflow.py`. NOT spec's `src/...`.
2. **DB driver:** sync SQLAlchemy 2.0 + psycopg2 (matches existing repo). NOT asyncpg. Async only at FastAPI handler / worker boundaries.
3. **Frontend:** Next.js 15 at `pivot-next/` (new dir, alongside legacy Vite `frontend/`). Acknowledged ~1-2 day timeline cost from porting chat UI.
4. **Scheduler:** extend existing `backend/scheduler.py` (already has `AsyncIOScheduler` + `SQLAlchemyJobStore` + 60s strategy-trigger pattern). No parallel scheduler.
5. **Hooks:** dropped the spec's `.claude/hooks.json` config — `TaskCompleted`/`TeammateIdle` are not real Claude Code events. Quality gates baked into teammate briefs and CI instead.
6. **Tool registry:** `propose_workflow` plugs into the existing `tool(name, description, properties, required)` pattern in `backend/agents/tools.py`. New tool subset `WORKFLOW_PROPOSE`.

### Blocked
- None.

### At risk for 2026-05-17
- **Vite → Next.js port** of the chat UI is the largest unknown. If it eats more than 2 calendar days, the polish phase (Day 7) compresses. Mitigation: keep the legacy Vite frontend running; only the Agent System UI is required to be in `pivot-next/` for v1. Chat UI port can be partial — Agent panel mounts inside whichever frame ships first.
- **`propose_workflow` LLM constraint** — the LLM must produce schema-valid step configs. Falls back to a parser-only path with one retry loop. If validation fails twice, surface a clear error in chat. Test with 10+ NL prompts on Day 6 (reviewer mandate).

### Next session (Day 1)
**Backend lead** picks up:
1. Schema migration `pivot/migrations/versions/0001_workflows.py` matching ARCHITECTURE.md §4.
2. SQLAlchemy models in `pivot/backend/models.py` + Pydantic schemas in `pivot/backend/schemas.py`.
3. Step-type catalog endpoint `GET /api/step-types` (read-only, returns the locked catalog).

**Frontend lead** picks up:
1. Scaffold `pivot-next/` (Next.js 15 + TypeScript strict + Tailwind + shadcn init).
2. Port `pivot-next/components/chat/` shell (no real chat plumbing yet — just the layout the Agent panel will mount inside).
3. `pivot-next/components/agent-panel/AgentPanel.tsx` shell (resizable right drawer, closeable, with mock workflow data).

**Reviewer (this role):**
1. Re-read both docs end-to-end after backend + frontend start coding; flag any drift.
2. Build the test scaffolds (pytest + vitest dirs + minimal "sanity passes" tests).

### Demo path readiness (out of 14)
Day 0: 0/14. (Docs only; no demo path exercisable yet.)

---

## Template for future days

```
## Day N — YYYY-MM-DD (role)

### Shipped
- ...

### Blocked
- ...

### At risk for 2026-05-17
- ...

### Next session
- backend-lead: ...
- frontend-lead: ...
- reviewer: ...

### Demo path readiness (out of 14)
- N/14, with notes on which steps work end-to-end.
```
