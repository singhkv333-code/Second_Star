# Backtesting Strengthening Plan — "Trustworthy, not pretty"

> **Owner:** lead · **Started:** 2026-06-01 · **Status:** IN PROGRESS — research + audit done; **P0.1 + P1.2 shipped** (2026-06-01)
> **Branch:** `Eventtriggers` · **Tracking:** this doc is the single source of truth; `STATUS.md` carries the running log.
>
> **Progress:** ✅ **P0.1** look-ahead fix (signal orders fill next-bar-open; live). ✅ **P1.2** Deflated/Probabilistic Sharpe + MinTRL on every backtest (3 engines; chat shows PSR). 🟡 **P1.6** Monte-Carlo block-bootstrap drawdown / P(loss) on every backtest. ✅ **P1.3** trial counter — DSR deflates for how many variants a session backtested (live: DSR fell as N went 1→2→3). 🟡 **P1.7** sub-period robustness — time-concentration tell. ✅ **P1.8** Trust verdict — the battery synthesised into one actionable call (chat summary leads with it). ✅ **P1.9** FE "Trust" panel on the chat backtest card (verdict badge + rigor stat row + flag chips). **Every backtest now shows — in chat AND on the card — a verdict + PSR · MinTRL · DSR(+trials) · Monte-Carlo · sub-periods.** ✅ **Phase 0 COMPLETE** (0.1 look-ahead · 0.2 CAGR · 0.3 Engine 1 costs · 0.4 `run_backtest` retired · 0.5 parity test · 0.6 standardized+proven no-look-ahead accessor). 🟢 **Phase 2 in progress:** ✅ **2.2 position sizing** (fixed/pct-equity/vol-target/ATR-risk, causal, Engine 2b + chat tool; live-proven). 🟡 **2.1 cross-sectional transforms** — ✅ rank/decile/quantile/zscore/percentrank + ✅ **winsorize** (sigma-clip) + ✅ **neutralize** (industry-demean via `industry_slug`; `sector` is empty), with ✅ **transform→ranking composition** (`decile(neutralize(roe))`, `zscore(winsorize(margin,3))`) via a two-level `ranked_t`→`ranked` CTE; all live-proven against `mc` (top industry-neutral RoE decile → 79 cos; `decile(neutralize(roe))==10 AND D/E<0.5` → 53). **Remaining 2.1 = lagged-price momentum + dollar-neutral L/S short leg — GATED on yfinance-per-name return data, not a price backfill (OHLCV is yfinance's job; the 9 mc.daily_prices rows are a mistake to ignore).** 🟡 **2.3 pairs/stat-arb** — Engle-Granger cointegration + causal spread z-score strategy + OU half-life + pairwise scanner + rigor battery, REST-exposed & live-proven (ADF/OU implemented from scratch — no statsmodels); Johansen + chat-tool wiring remain. Rigor middle still pending: P1.4 walk-forward / no-skill permutation (engine-rerun adapter) → P1.5 CPCV→PBO (param grid).

**Thesis in one line:** Pivot's wedge for serious algo/quant traders is not a prettier equity
curve — every retail tool draws those — it is a backtester that tells you *whether to believe
the curve*. We make overfitting controls (Deflated Sharpe, PBO, walk-forward, Monte-Carlo) and
honest Indian-market execution the **default output of every run**, and we cover the strategy
classes single-symbol retail engines structurally cannot (pairs/stat-arb, cross-sectional
long/short, vol-targeted CTA). No Indian platform does this today.

---

## 1. Why this, and the bet

Pivot has two user types:

1. **Retail** — execute, research, alerts, portfolios, AI suggestions, simple agents. **Largely
   built.** Not the focus of this initiative.
2. **Algo / expert traders** — multi-trigger strategies built from foundational mechanisms, complex
   algos, AI analysis. **The backtester is the make-or-break surface for this persona.** It must be
   *more rigorous than anyone else's* — that is the explicit goal.

The bet: among Indian platforms (Streak, Tradetron, AlgoTest, Sensibull, Opstra) and even most
global open-source engines, **rigor is the unguarded goal.** AlgoTest models slippage well; none
of them report a Deflated Sharpe, a probability-of-backtest-overfitting, or a walk-forward
efficiency. Sensibull — a flagship "options platform" — *cannot backtest past expiries at all.*
If our backtester refuses to hand a user a beautiful-but-fake result, and can faithfully test the
strategy classes the pros actually run, we win the persona.

---

## 2. The differentiator: trust as a first-class output

A backtest is **not an experiment — it is the *selected maximum* of many experiments.** The more
configurations a user (or our chat-LLM) tries, the higher the best in-sample Sharpe climbs *even
with zero true skill*. Every method below exists to undo that optimism. The headline:

> **We already own the hard math** (`backend/services/forward_stats.py`: Probabilistic Sharpe,
> Deflated Sharpe, Minimum Track Record Length — Bailey & López de Prado, pure-stdlib) — we just
> apply it **only to paper-traded ideas** (`paper/scorecards.py`), never at backtest time. Wiring
> it into the backtest path, plus a trial counter, instantly puts us ahead of every Indian retail
> platform. The rest of the rigor ladder (walk-forward, CPCV→PBO, Monte-Carlo permutation) is
> ~400 lines of numpy on top.

**The trust ladder** (ordered gates; a strategy advances only by passing the current rung):

| # | Gate | Pass bar | We have it? |
|---|------|----------|-------------|
| 0 | **Log trial count `N`** + retain every trial's returns | N recorded | ❌ (no trial tracking) |
| 1 | In-sample edge exists | gross Sharpe > 0 + a stated rationale | ✅ engines compute it |
| 2 | **Cost & slippage stress** | profitable at 2–3× modeled costs | ⚠️ costs modeled, no stress sweep |
| 3 | **Parameter plateau + regime survival** | broad plateau (not a spike); survives sub-periods; live↔BT window parity | ⚠️ parity fixed; no plateau/regime tooling |
| 4 | **Walk-forward** | aggregate OOS profitable; WFE ≳ 0.5 | ❌ |
| 5 | **CPCV → PBO** | PBO < ~0.2; inspect worst OOS path | ❌ |
| 6 | **Deflated Sharpe + multiple-testing** | DSR > 0.95 at realistic `N`; survives haircut | ✅ **PSR/DSR/MinTRL on every backtest (P1.2); DSR deflates for real trial-count `N` on the chat path (P1.3)**; haircut/SPA + clustered-`N` pending |
| 7 | **Monte-Carlo realism** | permutation p < 0.05; acceptable 95th-pct drawdown | ❌ |
| 8 | Capacity check (square-root impact) | net Sharpe holds at target AUM | ❌ (lower priority) |
| 9 | **Live paper-trade forward test** ≥ MinTRL | matches backtest within stress bounds | ✅ **already built** (paper P0–P6 + scorecards) |

Rungs **4–7 are the statistical core and the actual moat.** Rung 9 — the one most platforms
*don't* have — **we already shipped** (chat→paper→forward-scorecard, proven live). We are closer
than the gap looks: we have the top and the bottom of the ladder; we're missing the rigorous middle.

---

## 3. Where we are today (verified against source, 2026-06-01)

We have **not two but ~4–7 backtest code paths.** This fragmentation is itself a top risk for a
quant — results aren't apples-to-apples across surfaces.

| Engine | Path | Role | Health |
|---|---|---|---|
| **1 — Cross-sectional / factor** | `pivot-backtester/` (`backtester` pkg) → `POST /api/backtest/expr/run` | Fundamental-factor screen + equal-weight portfolio over the Postgres `mc` schema | Good PIT bones; **can't rank/zscore/neutralize**; off the shared cost model |
| **2 — Workflow DSL signal** (primary chat) | `services/workflow_backtester.py` (2,263 LOC) → chat tool `backtest_workflow` | Replays a multi-branch agent workflow over daily bars | Rich DSL; **same-bar look-ahead bug**; multi-symbol/baskets/pairs-aware |
| **2b — DSL tree** (cleanest) | `workflows/dsl/backtest/engine.py` → `POST /api/backtest/dsl/run` | Single-symbol entry-tree/exit-policy; persists runs; feeds scorecards | **No look-ahead (shadow-checked)**; single position; the engine to build on |
| 3 — Legacy signal | `pivot/backend/backtester/engine.py` → `POST /backtest/run` | Substrate reused by 2b (cost fns, fetch) | Legacy |
| novelty/vestigial | `indicator_backtest.py`, `open_close_backtest.py`, `run_backtest` tool | single-indicator, intraday-proxy, legacy | `run_backtest` hardcodes 10 bps + 10%-sizing — **divergent, should be retired** |

**What's genuinely good (don't rebuild):**
- `forward_stats.py` — PSR / DSR / MinTRL, pure stdlib, erf-CDF + Acklam-PPF. ✅
- `paper/scorecards.py` — verdict ladder + promotion gate (PSR≥0.95 ∧ MinTRL ∧ DSR≥0.95), trial-aware. ✅
- Engine 2b `BacktestDataAccessor` — slices `df.iloc[:as_of_idx+1]`, indicators computed once over the
  causal series then sliced, optional `DSL_BACKTEST_STRICT` shadow-check asserting no look-ahead. ✅ **This
  is the structural look-ahead firewall the whole platform should standardize on.**
- `trading_costs.py` — India NSE **delivery** costs (~36.94 bps round-trip: STT both legs + GST + stamp + exchange). ✅
- Engine 1 PIT fundamentals — TTM = last 4 quarters with `availability_date ≤ as_of`, `HAVING COUNT(*)=4`. ✅ genuinely point-in-time.
- One indicator registry (`services/backtest_indicators.py`, ~25 indicators) shared by live + all backtests. ✅
- Live↔backtest **window parity** fixed (commit c9e7abf): `period_for_indicator` so a 200-EMA agent fires live, not just in backtest. ✅

**Verified defects (the must-fix list):**
1. **Same-bar look-ahead, primary engine.** `workflow_backtester.py:1573` fills at `loc[ts]["Open"]`
   on a signal computed from `loc[ts]` **close** (`:577`). Optimistic; disagrees with Engine 2b's correct next-open discipline.
2. **Two CAGR conventions** coexist (`backtest_metrics.py` 365.25 vs Engine 1's trading-days/252) — `backtest_metrics.py:5` documents its own inconsistency. Cross-engine numbers aren't comparable.
3. **Engine 1 off the shared cost model** — its own naïve 10 bps slippage + 3 bps commission, no STT/GST (`runner.py:42-43`), ~2× understated vs the converged model.
4. **Survivorship bias** on the whole technical stack (Engines 2/2b/legacy): bars are whatever yfinance returns *today*; delisted/renamed names just fail to fetch. Self-admitted in `backtest_metrics.py:113-115`.
5. **No in-backtest overfitting defense** — no walk-forward, OOS, CV, Monte-Carlo, PBO, or DSR-at-backtest. `forward_stats` is one import away but never called.
6. **Vestigial `run_backtest`** tool still live with hardcoded 10 bps + 10%-sizing — retire or redirect.
7. Daily-only granularity everywhere (`interval="1d"`); intraday faked via OPEN/CLOSE proxies.
8. Engine 2b is single-position, fixed-share — no vol-targeting / %-equity / ATR sizing.
9. Engine 1 grammar can't `rank`/`zscore`/`decile`/`neutralize` — the canonical long/short equity strategy is inexpressible.
10. No bars cache → every Engine-2 run re-downloads from yfinance.

---

## 4. What best-in-class looks like (distilled from the research)

The convergent design of LEAN, Nautilus, Zipline, VectorBT (full survey in Appendix B):

- **Event-driven core** that hands strategy code only a **current-time-bounded view** (LEAN's "Time
  Frontier", Zipline's `BarData.current()` vs `history()`) so look-ahead is *structurally impossible*,
  not a discipline the user must remember. We approximate this in Engine 2b's accessor — generalize it.
- **Optional vectorized fast-path** (VectorBT/Numba) for parameter sweeps — thousands of configs in one array op.
- **Point-in-time, survivorship-free, bitemporal data** — assets carry start/end dates; an "as-of" query
  returns only what was *known* then (as-reported, not restated fundamentals). Restated fundamentals beat
  as-reported by ~100 bps/yr — pure look-ahead.
- **Pluggable "reality models"** (LEAN's signature): slippage, fee, fill, buying-power, settlement, borrow,
  latency are each swappable components with sane defaults. India needs the fee/STT model to be *first-class
  and effective-dated* (rates changed in 2026; options STT is on premium, futures on notional; lot-size
  rounding, quantity-freeze splitting, circuit-band rejection, T+1 settlement).
- **Backtest ↔ paper ↔ live parity** via ports/adapters + a shared kernel (the same strategy code runs in
  all three). **We already have the paper leg** — the architectural pattern to lean into.
- **Realistic fills**: market-on-next-open, limit-touch with queue probability, **partial fills**, **volume
  caps** (can't fill more than a fraction of real bar volume).
- **Overfitting controls as default outputs** — the rigor ladder in §2.

The ~20-item "best-in-class checklist" and India-microstructure specifics are in Appendix B.

---

## 5. Gap analysis (master table)

| Capability | Best-in-class | Pivot today | Gap | Priority |
|---|---|---|---|---|
| **Look-ahead prevention** | Structural (Time Frontier) | ✅ Engine 2b; ❌ Engine 2 (same-bar bug) | Fix Engine 2; standardize 2b's accessor | **P0** |
| **Single consistent engine** | One core, parity across surfaces | ~4–7 divergent paths | Consolidate; retire vestigial | **P0** |
| **Honest cost model** | Pluggable, effective-dated, instrument-aware | Delivery only; Engine 1 off-model | Unify; add intraday/F&O variants | **P0/P3** |
| **Overfitting controls** | DSR/PBO/WF/MC default | Math exists, unused at BT | Wire `forward_stats` + build WF/CPCV/MC | **P1 (the moat)** |
| **Trial-count tracking** | First-class input to deflation | ❌ | Counter + per-trial return retention | **P1** |
| **Cross-sectional ranking** | rank/zscore/decile/neutralize | ❌ (threshold only) | Add CS transforms to Engine 1 grammar | **P2** |
| **Pairs / stat-arb** | First-class pair/basket object | ⚠️ Engine 2 can read 2 symbols, no cointegration | Cointegration + spread z-score + OU half-life | **P2** |
| **Position sizing** | vol-target, ATR, %-equity, Kelly | ❌ fixed-share / equal-weight | Sizing layer | **P2** |
| **Survivorship-free data** | Asset start/end + delisting | ⚠️ Engine 1 guard needs backfill; ❌ technical stack | Delisting backfill; PIT universe | **P3** |
| **Realistic fills** | partial, volume-cap, limit/stop | ❌ market-at-open only | Pluggable fill models | **P3** |
| **Intraday** | minute/tick | ❌ daily-only | Minute bars path | **P4** |
| **Options / F&O** | chains + Greeks + multi-leg | ❌ | Chain history + Greeks + multi-leg P&L | **P4** |
| **Bars cache / scale** | persisted, vectorized, distributed | ❌ re-downloads | Bars store + vectorized fast-path | **P3/P4** |
| **LLM authoring guardrails** | NL→sandboxed PIT DSL | ⚠️ chat authors workflows; no rigor gate | Route LLM strategies through the rigor ladder | **P1/P4** |
| **Live↔paper↔BT parity** | shared kernel | ✅ paper leg shipped | Formalize the shared core | ongoing |

---

## 6. Strategy-coverage matrix — "can we test it today?"

Direct answer to *"test the advanced strategies and compare our model."* For each pro strategy
class: what it needs, and whether our engine can faithfully test it.

| Strategy class | What faithful testing needs | Pivot today | Unlocked by |
|---|---|---|---|
| **Single-symbol trend** (MA-cross, breakout) | indicators, stop/trail, long/short | ✅ Engine 2/2b (DSL is rich) | — (fix look-ahead) |
| **Mean-reversion** (RSI(2), Bollinger z-score) | bands/z-score, *stop-optional*, fast in/out | ✅ Engine 2/2b | — |
| **CTA / vol-targeted trend** | multi-instrument futures, **vol-target + ATR sizing**, pyramiding | ⚠️ logic yes, **sizing no** | P2 sizing layer |
| **Cross-sectional momentum / factor L/S** | universe ranking, winsorize/z-score, **sector/beta neutralization**, decile portfolios, dollar-neutral | ❌ Engine 1 can threshold but **not rank** | P2 CS transforms |
| **Stat-arb / pairs / cointegration** | **≥2-symbol object**, Engle-Granger/Johansen, hedge ratio, spread z-score, OU half-life | ❌ (no pair object, no cointegration) | P2 pairs module |
| **Options / volatility** | historical **chains + Greeks + IV surface**, multi-leg P&L, STT-on-premium | ❌ | P4 options |
| **ML-driven** | triple-barrier labels, meta-labeling, uniqueness weights, **CPCV** | ❌ | P1 (CPCV) + P4 (ML pipeline) |
| **Market-making / HFT** | tick/LOB + queue + latency | ❌ — **out of scope** (daily bars can't model it) | — (state it for credibility) |

**Headline:** today we faithfully test the *single-symbol technical* family (once the look-ahead bug
is fixed) and *fundamental-factor screens* (but not ranked long/short). The high-value pro classes —
**cross-sectional L/S, pairs/stat-arb, vol-targeted CTA** — are exactly what P2 unlocks. We will
implement one reference strategy per class as an executable acceptance test as each phase lands
(e.g. a NIFTY-constituents 12-1 momentum decile L/S; a same-sector cointegrated pair; a vol-targeted
breakout) and report it through the full rigor ladder.

---

## 7. The roadmap

Phases are sequenced by *trust-per-unit-effort*. Effort: **S** ≈ hours, **M** ≈ 1–2 days,
**L** ≈ ~week, **XL** ≈ multi-week. Each phase ends green on tests + a STATUS.md entry.

### Phase 0 — Correctness & consolidation (the foundation of trust) — **M–L**
*You cannot layer rigor on top of inconsistent, look-ahead-biased engines.*
- ✅ **0.1 DONE (2026-06-01)** Fixed the same-bar look-ahead in `workflow_backtester.py` — signal-driven
  orders (`_SIGNAL_TRIGGERS`) fill at **next bar open** via `_next_bar_ts`; schedule fires stay same-bar.
  Trade-log-keyed equity rebuild means this also closed the equity-curve leak. First unit tests added
  (`tests/test_workflow_backtester_lookahead.py`). Proven live on RELIANCE. *(Full structural shadow-check
  across conditions deferred to 0.6.)*
- ✅ **0.2 DONE (2026-06-01)** Engine 1 (`pivot-backtester/.../metrics.py`) CAGR now uses the CALENDAR
  span (365.25/yr) — was bar-count/252 (the comment even lied "calendar"). Verified it matches
  `backtest_metrics.calendar_cagr_pct` exactly. **[S]**
- ✅ **0.3 DONE (2026-06-01)** Engine 1 is on the shared `trading_costs` model — the expr router sets
  `slippage_bps`/`commission_bps` so the round-trip reproduces `round_trip_bps()` (~37 bps incl. STT/GST),
  not the old ~26 bps. **[S]**
- ✅ **0.4 DONE (2026-06-01)** Retired the vestigial `run_backtest` tool (def + registry + dispatch +
  handler) — it had a hardcoded 10 bps + 10%-of-capital sizing, no rigor battery, and rsi/price_cross
  weren't even implemented. Chat backtests route to `backtest_workflow` / `backtest_dsl_tree`. **[S]**
- ✅ **0.5 DONE (2026-06-01)** `tests/test_backtest_engine_parity.py` locks the conventions (Engine 1
  CAGR == shared calendar; Engine 1 round-trip == shared bps; `run_backtest` gone from the catalog). **[M]**
- ✅ **0.6 DONE (2026-06-01)** Standardized the no-look-ahead boundary: both engines' accessors
  (`_BarStrictAccessor`, `BacktestDataAccessor`) conform to the ONE `DataAccessor` protocol and pass an
  adversarial future-trap test (`tests/test_no_lookahead_engine2.py` + the existing 2b
  `test_no_lookahead_adversarial`). Full code-unification into one object deferred — both satisfy the same
  protocol + test, and forcing Engine 2's trigger-expansion through one object is a high-risk refactor for
  no correctness gain. **Phase 0 COMPLETE.** **[M]**

**Exit:** one trustworthy daily-bar core, consistent metrics, no look-ahead, India costs everywhere.

### Phase 1 — The rigor layer (the moat) — **L**
*This is the differentiator. Mostly numpy; reuses `forward_stats`.*
- **1.1** New module `backend/services/backtest/validation/` (pattern after `forward_stats`: stdlib-first,
  scipy only where it earns it — scipy 1.17 is available).
- ✅ **1.2 DONE (2026-06-01)** **Wired `forward_stats` into every backtest result** — new
  `forward_stats_block()` attaches PSR, MinTRL, DSR, skew/kurtosis to all three engines (chat
  `backtest_workflow`, `/api/backtest/dsl` via a `ForwardStats` schema model, `/api/backtest/expr` in the
  router). Chat summary now shows "PSR NN%". `num_trials=1` until 1.3 lands (DSR == PSR(0) for now).
- ✅ **1.3 DONE (2026-06-01)** **Trial counter** — `services/backtest/validation/trials.py`: a per-session
  registry that deflates DSR for the count of DISTINCT strategy variants backtested (dedup by strategy
  fingerprint; 2h TTL session). Wired into the chat path (`backtest_workflow` `trial_group`, tool passes
  `u{uid}`); summary shows "After N variants… DSR NN%". Proven live (DSR fell as N went 1→2→3). *(Effective-N
  is the distinct-trial count for now; return-correlation clustering for a tighter N is a future refinement.
  Stateless `/dsl` + `/expr` opt in via a session id later.)*
- **1.4** **Walk-forward** (anchored + rolling) with Walk-Forward Efficiency. **[M]**
- **1.5** **Combinatorial Purged CV → PBO** (purge + embargo; `φ[N,k]` OOS paths; CSCV logit → PBO). **[L]**
- 🟡 **1.6 PARTIAL (2026-06-01)** **Monte-Carlo** — ✅ circular-block-bootstrap drawdown / terminal-wealth
  distribution (5%-worst DD, P(end in loss), P(DD > tolerance)) shipped in
  `services/backtest/validation/monte_carlo.py`, on every backtest + the chat summary. ⏳ the no-skill
  **permutation significance** test (re-run strategy on shuffled price paths) needs the engine-rerun adapter —
  lands with 1.4 walk-forward. **[M]**
- 🟡 **1.7 PARTIAL (2026-06-01)** ✅ **sub-period/regime** breakdown shipped (`validation/sub_periods.py`:
  per-span returns + positive-span fraction + a `concentration` tell), on every backtest + a chat fragility
  warning when >60% of the return came from one span. ⏳ cost-sensitivity sweep (1×/2×/3×) + parameter-plateau
  heatmap need the engine-rerun adapter (land with 1.4). **[M]**
- ✅ **1.8 DONE (2026-06-01)** **"Trust verdict"** on every backtest — `validation/verdict.py` rolls the
  battery into one ordered call (`insufficient_data` → `no_edge` → `unproven` → `promising`) + rationale +
  risk flags (selection_bias / return_concentrated / drawdown_risk / loss_likely). All 3 engines (+ a
  `TrustVerdict` schema model); the chat summary LEADS with it. *(PBO term joins once 1.5 lands.)*
- ✅ **1.9 DONE (2026-06-01)** Surfaced the battery in the FE chat backtest card — a "Trust" panel in
  `IndicatorBacktestCard` (verdict badge + confidence + rationale + 6-stat rigor row + flag chips) and a
  compact verdict pill. Types in `lib/api.ts`. tsc/lint clean. *(BacktestTab/expr card = follow-up.)* **[frontend-lead]**

**Exit:** every backtest answers "should I believe this?" — a capability *no Indian platform ships.*

### Phase 2 — Strategy-class coverage (unlock the pro classes) — **L–XL**  *(IN PROGRESS)*
- 🟡 **2.1 PARTIAL (2026-06-01)** **Cross-sectional transforms** in Engine 1's grammar — ✅ `rank` / `decile`
  / `quantile(x,n)` / `zscore` / `percentrank` compile to SQL window functions over the universe at date T
  (a `ranked` CTE), so `decile(roe) == 10` selects the top-decile names. Long-only ranked selection works
  today through the equal-weight runner; proven by executing against live `mc` Postgres. Also fixed a latent
  `==`/`!=` → `=`/`<>` SQL-operator bug.
  - ✅ **winsorize / neutralize + composition (2026-06-01).** `winsorize(x, k)` = sigma-clip to mean±k·σ;
    `neutralize(x)` = industry-demean (`PARTITION BY c.industry_slug`; `sector`/`market_cap`/`beta` are
    empty/price-gated so industry is the only viable group). Crucially, **transforms compose under rankings**
    (`decile(neutralize(roe))`, `zscore(winsorize(margin,3))`) via a two-level `ranked_t` (transforms) →
    `ranked` (rankings) CTE — window functions can't nest, so the transform becomes a column the ranking orders
    by. Validator now allows exactly *transform-inside-ranking* nesting. 10 new XS tests + 2 live contract
    checks; proven on `mc` (industry-neutral RoE decile → 79; `…==10 AND D/E<0.5` → 53).
  - ⏳ Remaining: a lagged-price **momentum** field + the **dollar-neutral long/short** short leg (runner change)
    — **both GATED on the price backfill** (returns are only meaningful with prices, and only 9 names are
    priced; see docs/DATA_AUDIT.md). **[L]**
- ✅ **2.2 DONE (2026-06-01)** **Position sizing layer** in Engine 2b — `fixed` / `pct_equity` / **`vol_target`**
  (size to an annualised volatility target) / **`atr_risk`** (risk N% of equity per trade, stop at ATR×mult).
  All causal (vol/ATR over bars BEFORE the entry) + capped at no-leverage. New `Sizing` schema model, wired into
  `_open_position`, exposed on `/api/backtest/dsl/run` AND the `backtest_dsl_tree` chat tool. 7 sizing tests +
  proven live (RELIANCE vol-target sized 13 entries 49–88 shares vs fixed's constant 10). *(Engine 2 + pyramiding/
  Kelly = follow-up.)* **[M]**
- 🟡 **2.3 PARTIAL (2026-06-01)** **Pairs / stat-arb as a first-class object** —
  `backend/services/backtest/pairs/`. ✅ ingest 2 aligned yfinance series; ✅ **Engle-Granger** cointegration
  (ADF + OU implemented from scratch — no statsmodels/sklearn in the venv — and validated on synthetic series);
  ✅ static + rolling **hedge ratio**; ✅ a **spread instrument** with causal z-score entry / mean-revert exit /
  stop and dollar-neutral P&L (trailing-window β+z, position lagged one bar → look-ahead-free, pinned by a test);
  ✅ **OU half-life** diagnostic; ✅ a pairwise cointegration **scanner**; ✅ the full Phase-1 rigor battery on the
  spread equity; ✅ REST `/api/backtest/pairs/run` + `/scan`. Proven live: scanner found AXISBANK/UNIONBANK
  cointegrated@1% (ADF −4.04, 24-day half-life) among 91 pairs; honestly, even cointegrated pairs backtest to
  `no_edge` (full-sample cointegration is an in-sample diagnostic; the causal backtest + rigor battery refuse a
  false edge). 14 deterministic tests. ⏳ Remaining: **Johansen** (>2-asset baskets) + a **chat tool** to expose
  it on the primary surface. **[L]**
- **2.4** **Multi-position portfolio state** in the tree engine (gross/net exposure, sector caps, max names). **[M]**
- **2.5** Reference-strategy acceptance tests (one per class), each reported through the Phase-1 rigor ladder. **[M]**

**Exit:** we can faithfully test the strategies pros actually run — pairs, factor L/S, vol-targeted trend.

### Phase 3 — Data & execution realism — **L–XL**
- **3.1** **Survivorship-free universe**: backfill delisting/listing dates + delisted names into `mc.companies`;
  PIT constituent lists for NIFTY/NSE-500; make the technical stack read the PIT universe, not "today's tickers." **[L]**
- **3.2** **Pluggable reality models** (LEAN-style): slippage (fixed / volume-share / spread / square-root impact),
  fill (next-open / limit-touch / partial / **volume-cap**), buying-power, **T+1 settlement**, borrow/short. **[L]**
- **3.3** **India execution model, first-class + effective-dated**: STT by instrument type (delivery 0.1% both /
  intraday 0.025% sell / futures notional / options on premium), exchange+SEBI+GST+stamp, **lot-size rounding**,
  **quantity-freeze splitting**, **circuit-band rejection**, point-in-time expiry calendars. **[M]**
- **3.4** **Bars store / cache** (persist daily OHLCV; stop re-downloading per run) + a **vectorized fast-path**
  for parameter sweeps. **[L]**

**Exit:** results survive a quant's scrutiny on data integrity and fill realism.

### Phase 4 — Intraday, options, AI surface — **XL**
- **4.1** **Intraday** (minute bars) path — unlocks intraday mean-reversion + intraday options.
- **4.2** **Options / F&O**: historical chains + Greeks + IV surface + multi-leg P&L + Indian charge modeling
  (matches/beats AlgoTest intraday, Opstra EOD).
- **4.3** **LLM authoring guardrails** (see §8) — compile chat strategies to the sandboxed PIT DSL and auto-route
  through the rigor ladder before showing any result.
- **4.4** **News/sentiment/event features** with strict point-in-time timestamps (NSE announcements, RBI text —
  ties to the existing RBI-event focus). Validate via CPCV.
- **4.5** **ML pipeline** (optional): triple-barrier labeling, meta-labeling, uniqueness sample weights, MDI/MDA/SFI
  feature importance — CPCV as the *default* validator.

---

## 8. How AI fits (concrete and skeptical)

Organizing principle: **AI is excellent at authoring, search, and reading; dangerous at validation.**
Every AI feature sits *downstream of, and policed by,* the Phase-1 rigor ladder.

- **LLM natural-language → strategy (Pivot's chat angle) — genuine advantage, *if* guarded.** Asked for "strong
  backtest performance," LLMs default to curve-fitting, look-ahead feature construction, and survivorship in
  selection — "the LLM doesn't understand that a beautiful backtest is a warning sign," and they're homogeneous
  (ask for 20 strategies, get ~6 distinct). Guardrails we enforce:
  1. Compile to the **sandboxed, point-in-time DSL/AST** (we already have the no-look-ahead accessor) — the LLM
     *cannot* read `t+1` or full-sample stats. Structural, not advisory.
  2. Reject full-sample fitting (normalizations/thresholds must be rolling/expanding only).
  3. Auto-route every LLM-authored strategy through **CPCV + DSR + PBO** before showing results; flag high PBO.
  4. Costs mandatory; **trial-count tracked** and fed into deflation.
- **AI factor discovery** (genetic programming / symbolic regression, the "101 Alphas" lineage) — *hypothesis
  generator only.* It's multiple-testing on steroids; every candidate scored under PBO/DSR.
- **Regime detection** (HMM/clustering) — a *causal risk filter* that gates existing strategies, never alpha itself.
- **Hyperparameter search** (Optuna/Bayesian) — allowed only **inside** CPCV with DSR-deflation, because efficient
  search *amplifies* overfitting.
- **LLM news/sentiment/earnings** — promising (LLM sentiment long/short has shown high Sharpe in studies) but
  acutely exposed to training-data look-ahead; use strictly as a PIT-timestamped feature, validate via CPCV.
- **What AI must NOT do:** be the discovery mechanism for validation; hand over un-deflated optimized parameters;
  have its self-reported backtest numbers trusted; paper over survivorship/look-ahead/snooping.

---

## 9. Explicitly out of scope for v1 (stated for credibility)

- **Market-making / HFT / order-book strategies** — need tick data, queue position, and latency a bar engine
  cannot model. A daily/minute-bar engine that "backtests" market-making produces fiction. We say so.
- **End-to-end RL "alpha" agents** — overfitting + reward-hacking. RL is acceptable *later* only as an
  execution-optimization overlay with a real LOB simulator.
- **Tick/L2 backtesting**, co-location/latency arbitrage.

Naming these honestly is itself a trust signal to the quant persona.

---

## 10. Immediate next actions

1. ✅ **DONE — Phase 0.1** fixed the same-bar look-ahead in `workflow_backtester.py` (the verified correctness bug).
2. ✅ **DONE — Phase 1.2** wired `forward_stats` (PSR/DSR/MinTRL) into all three engines + the chat summary.
3. ✅ **DONE — Phase 1.3** trial counter (`validation/trials.py`); DSR now deflates for the session's
   distinct-variant count on the chat path. ✅ **DONE — Phase 1.6 (part 1)** Monte-Carlo block-bootstrap.
4. **Phase 1.4 — walk-forward / sub-period robustness** + the **no-skill permutation test** (re-run the
   strategy on shuffled price paths). Both want a small **engine-rerun adapter** (run a strategy on injected
   bars / a sub-window) — build that first; it also unblocks cost-sensitivity sweeps.
5. **Phase 1.5 — CPCV→PBO** (needs a parameter grid → couples to P2), then the **Trust verdict** (1.8)
   + the **FE backtest card** (1.9) surfacing PSR/DSR/MinTRL/MC/trial-count.

> Remaining of the moat: the rigor *middle* (walk-forward → CPCV/PBO → Monte-Carlo). We already own the top
> (in-sample metrics + now PSR/DSR) and the bottom (live paper forward test).

---

## Appendix A — The rigor toolkit (what Phase 1 implements)

Formulas confirmed against primary sources (Bailey/Borwein/López de Prado/Zhu; Harvey-Liu-Zhu; White; Hansen).
**Already implemented** in `forward_stats.py`: PSR, DSR (`SR₀` via expected-max-SR over `N` trials), MinTRL,
observed Sharpe, skew, kurtosis, max-DD, normal CDF (`math.erf`) + inverse-normal (Acklam). **To build:**

- **Probabilistic Sharpe Ratio** `PSR(SR*) = Z[ (SR−SR*)·√(T−1) / √(1 − γ₃·SR + ((γ₄−1)/4)·SR²) ]`. *(done)*
- **Deflated Sharpe** = `PSR(SR₀)`, `SR₀ = √V[SR_n]·[(1−γ)·Z⁻¹(1−1/N) + γ·Z⁻¹(1−1/(N·e))]`; `N` = effective
  independent trials (cluster correlated variants → count clusters). *(done; needs the trial-set + clustering for `N`)*
- **Minimum Track Record Length** `MinTRL = 1 + [1 − γ₃·SR + ((γ₄−1)/4)·SR²]·(Z_α/(SR−SR*))²`. *(done)*
- **Walk-forward** — anchored/rolling IS→OOS; `WFE = annualized OOS return / annualized IS return`. *(build, numpy)*
- **Purged k-fold + embargo** — drop train labels whose horizon overlaps test; embargo ~1–5% after each fold. *(build)*
- **CPCV** — `N` groups, choose `k` as test → `C(N,k)` splits, `φ=(k/N)·C(N,k)` distinct OOS paths → distribution of Sharpes. *(build)*
- **PBO via CSCV** — `T×N` PnL matrix → `S` blocks → all `C(S,S/2)` IS/OOS partitions → OOS rank ω of the IS-best
  → logit λ=ln(ω/(1−ω)) → `PBO = P(λ ≤ 0)`. Free byproducts: performance-degradation slope, P(OOS loss). *(build, numpy + itertools)*
- **Monte-Carlo permutation** — permute log-returns, rebuild path, re-run; `p = (#{perm metric ≥ real}+1)/(#perm+1)`; ≥1,000 perms. *(build, numpy)*
- **Trade-order / block bootstrap** — shuffle/resample trades (block-bootstrap to preserve autocorrelation) → drawdown & terminal-wealth distributions. *(build, numpy)*
- **Multiple-testing haircut** (Bonferroni/Holm/BHY) + **White Reality Check / Hansen SPA** (bootstrap the max over rules vs no-skill null). *(build; `arch` lib optional for SPA)*

**Dependency note:** scipy 1.17.1 is available; sklearn/statsmodels are not. Keep the stdlib-first pattern of
`forward_stats`; reach for scipy only for the t-distribution CDF and (optionally) hierarchical clustering of trials.

## Appendix B — Research sources

**Platform architecture:** QuantConnect LEAN [reality modeling](https://www.quantconnect.com/docs/v2/writing-algorithms/reality-modeling/key-concepts) · [algorithm engine](https://www.quantconnect.com/docs/v2/writing-algorithms/key-concepts/algorithm-engine); Nautilus Trader [architecture](https://nautilustrader.io/docs/latest/concepts/architecture/) · [backtesting](https://nautilustrader.io/docs/latest/concepts/backtesting/); [zipline-reloaded internals](https://deepwiki.com/stefan-jansen/zipline-reloaded); [VectorBT PRO fundamentals](https://vectorbt.pro/documentation/fundamentals/); [QuantRocket vector-vs-event](https://www.quantrocket.com/blog/backtest-speed-comparison/); [Backtrader cerebro](https://www.backtrader.com/docu/cerebro/).

**Overfitting science:** Bailey/Borwein/López de Prado/Zhu — [Pseudo-Mathematics (AMS 2014)](https://www.ams.org/notices/201405/rnoti-p458.pdf) · [Probability of Backtest Overfitting](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf); Bailey & LdP — [Deflated Sharpe Ratio](https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf) · [Wikipedia](https://en.wikipedia.org/wiki/Deflated_Sharpe_ratio); López de Prado *AFML* ch.7 — [Purged CV](https://en.wikipedia.org/wiki/Purged_cross-validation); Harvey-Liu-Zhu — [Cross-Section of Expected Returns](https://people.duke.edu/~charvey/Research/Published_Papers/P118_and_the_cross.PDF); Harvey-Liu — [Backtesting / haircut Sharpe](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2345489); White — [Reality Check](https://users.ssc.wisc.edu/~bhansen/718/White2000.pdf); Hansen — [SPA](https://arch.readthedocs.io/en/latest/multiple-comparison/generated/arch.bootstrap.SPA.html); [Walk-forward (QuantInsti)](https://blog.quantinsti.com/walk-forward-optimization-introduction/); [Monte-Carlo permutation (BuildAlpha)](https://www.buildalpha.com/monte-carlo-permutation/).

**Strategies & AI:** [Time-Series Momentum (Moskowitz/Ooi/Pedersen)](https://w4.stern.nyu.edu/facdir/lpederse/papers/TimeSeriesMomentum.pdf); [Turtle rules](https://www.tradingblox.com/Manuals/UsersGuideHTML/turtlesystem.htm); [cross-sectional momentum](https://www.pfolio.io/academy/cross-sectional-momentum); [factor neutralization](https://stockalpha.ai/alpha-learning/quantitative-investing-101-introduction-to-factor-models-and-backtesting); [RSI(2)](https://chartschool.stockcharts.com/table-of-contents/trading-strategies-and-models/trading-strategies/rsi-2); [cointegration & pairs](https://sesen.ai/blog/cointegration-pairs-trading); [OU half-life](https://arxiv.org/html/2412.12458v1); [triple-barrier & meta-labeling (mlfinlab)](https://www.mlfinlab.com/en/latest/labeling/tb_meta_labeling.html); [101 Formulaic Alphas](https://arxiv.org/pdf/1601.00991); [LLM strategy-gen overfitting](https://dev.to/whetlan/i-asked-an-llm-to-generate-20-trading-strategies-14-were-the-same-thing-2f36); [HMM regime detection](https://www.quantstart.com/articles/market-regime-detection-using-hidden-markov-models/); [LLM sentiment trading](https://arxiv.org/pdf/2412.19245).

**India microstructure:** [Zerodha charges](https://zerodha.com/charges/) · [STT (ClearTax)](https://cleartax.in/s/securities-transaction-tax-stt) · [NSE F&O quantity-freeze](https://www.nseindia.com/static/products-services/equity-derivatives-individual-securities) · [AlgoTest options backtesting](https://algotest.in/blog/best-backtesting-software-for-options-trading-in-india/) · [sharpely PIT/survivorship](https://sharpely.in/blog/bias-free-backtesting-explained:-how-sharpely-uses-point-in-time-data-to-avoid-look-ahead-and-survivorship-bias).
