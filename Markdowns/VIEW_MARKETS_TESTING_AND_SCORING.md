# View Markets (V2) — Testing Standards & User-Facing Conviction Scoring

**Status:** design spec · **Scope:** Pivot V2 "View Markets" (Belief → Expression → Deployment)
**Owner principle:** India-first, **register-not-execute**, never fabricate numbers, **reuse existing primitives**.

This document defines two things:

1. **Testing standards** — how Pivot validates a View Markets strategy *honestly*, per view type
   (EVENT / RELATIVE / THEME), reusing the existing **Backtest Trust Battery**.
2. **The user-facing score** — a concrete, honest **two-dial conviction / historical-alignment**
   scheme (outcome confidence vs expression confidence), its components, combination, presentation,
   guardrails, and the mapping onto existing modules vs net-new work.

> **One sentence that governs everything below:** P&L is driven by the gap between the *realized
> outcome* and *what was already priced in* — so every test measures historical alignment of a
> structure conditioned on surprise, and every score is a **forecast of the past, never a promise of
> the future.**

---

# PART 1 — TESTING STANDARDS PER VIEW TYPE

Pivot recognises three view types. Each has a different correct test methodology, but all three feed
the **same existing Trust Battery** (Probabilistic Sharpe, Deflated Sharpe, Minimum Track Record
Length, Monte-Carlo block bootstrap, walk-forward, no-skill permutation, trial counter, plain-English
Trust verdict) so the honesty layer and the language stay consistent.

## 1.1 EVENT views — event-study methodology

An EVENT view ("RBI holds in Aug → BANKNIFTY rallies", "INFY beats → drifts up") has an **objective
outcome and a resolution date** — exactly what the classical **event-study framework** was built to
measure [1][6]. We treat "how have analog events historically moved the proposed instruments?" as a
formal event study over a **sample of historical analog events**, never a single anecdote.

### Windows

- **Estimation window** — the clean pre-event period used to learn "normal" behaviour. Short-horizon
  standard: **~120 trading days** ending a few days before the event, regressing the stock's return on
  a market index (NIFTY/BANKNIFTY for Pivot) [1][6]. Leave a **10–20 day gap** between estimation and
  event windows so leakage doesn't contaminate the baseline.
- **Event window** — symmetric band around the event day `t=0`, e.g. **[-5, +5]**, plus a **post-event
  drift window** (e.g. **[+1, +60]**) because India exhibits genuine **post-earnings-announcement drift**
  (turnaround firms ~9% CAAR in the week after earnings; NSE studies reject semi-strong efficiency
  around earnings) [9][10]. This is the empirical justification that a **Confirmation-timing** (enter
  after the print) expression can still capture edge.

### Models for "normal" return

1. **Mean-adjusted** — normal = average estimation-window return. Simplest, weak.
2. **Market model (default, recommended)** — `R_it = α_i + β_i·R_mt + ε_it`; `AR = actual − (α̂ + β̂·R_mt)`
   [1][6]. Right default for Pivot because we already have Kite OHLCV + NIFTY for the regression.
3. **Factor models** — add size/value if needed; rarely worth it for a retail short-horizon tool.

> **Joint-hypothesis / bad-model caveat:** every "significant" abnormal return jointly tests *the event*
> **and** *the model of normal return* [11][13]. Pivot must phrase results as **model-conditional**,
> never as proof.

### Abnormal-return statistics

- **AR** — abnormal return per firm per day = actual − expected [1][6].
- **CAR** — cumulative AR over the event window (standard short-window measure) [1][6].
- **AAR / CAAR** — average AR / cumulative average AR across the **sample of analog events** (turns one
  anecdote into a base rate).
- **BHAR** — buy-and-hold abnormal return, the *compounded* return an investor actually earns. **Use
  BHAR for the user-facing "what would I have made" number** (matches lived experience) [12][13]; **use
  CAR for the significance test** because summed ARs are statistically better-behaved (BHARs are
  right-skewed, overlapping, prone to spurious rejection — Fama 1998 / Mitchell-Stafford /
  Kothari-Warner) [11][12][13]. **Report both, labelled.**

### Significance testing (where the rigor lives)

Implement a **parametric + non-parametric pair** and report both [3]:

| Situation (typical Pivot event) | Test |
|---|---|
| Single stock, single event | Plain t-test `AR / S_AR` (weak — disclose) [3] |
| Sample of analog events, normal-ish | Cross-sectional t `√N · AAR / S_AAR` [3] |
| **Announcements (earnings/RBI) — event-induced volatility** | **BMP / standardized cross-sectional test** — default short-window parametric test; neutralises the volatility spike a news event causes [3] |
| Events clustering on one date (all banks on RBI day) | **Kolari-Pynnönen** adjustment to BMP `√[(1−r̄)/(1+(N−1)r̄)]` [3] |
| Skewed / non-normal / small N | **Non-parametric**: generalized sign test, **Corrado rank**, or **generalized rank-t** (most robust) [3] |

**Practical rule:** retail analog samples are small and event-clustered → default to **BMP + a
non-parametric (sign or rank) test**, and only call an effect "historically reliable" when **both
agree**. Surface **t-stat / p-value and N (analog count)** on the card — small N is itself a guardrail
signal.

### Sourcing the analog sample (India-first)

- **Earnings events** → Moneycontrol / earnings calendar; multi-year quarterly prints per stock/sector.
- **Macro events** → RBI MPC dates, Union Budget (Feb 1), index events; reuse `thematic_map.py` macro
  scenarios and `scheduled_macro` triggers.
- **Timing maps to event-window slices:** Pre-position = `[-5, 0]` capture; Confirmation = `[+1, +T]`
  drift capture; Hybrid = both legs.
- **What's-priced-in:** Polymarket/Kalshi adapters give market-implied probability; for index/stock
  events the **options expected move** `= Price × IV × √(DTE/365)` (≈ ATM straddle × 0.85) is the
  cleanest surprise yardstick — abnormal return materialises only if realized move **exceeds** the
  priced expected move [14][16]. This is the formal "surprise vs market-expectations" definition and
  reuses our option-chain primitives.

## 1.2 RELATIVE / PAIRS views — relative-strength backtesting

A RELATIVE view ("A beats B over T", "private banks beat PSU banks") maps onto the **existing
pairs/cointegration engine** (Engle-Granger, Johansen, OU half-life).

### In-sample vs out-of-sample with cointegration *stability*

The most-violated fact: **relationships decay out-of-sample.** Empirically, high correlation persists
into the adjacent OOS period only **~50%** of the time, cointegration only **~40%** [4][5]. Therefore:

- **Mandatory OOS split** — form the spread / hedge ratio in-sample, trade it in a held-out window;
  never report in-sample spread Sharpe alone.
- **Re-test cointegration in the OOS window** — don't assume the in-sample Engle-Granger/Johansen
  relation still holds. Report **OOS ADF/Johansen p-value** and **OU half-life stability** (a blown-out
  half-life = relationship broke).
- **Rolling re-estimation** — re-fit the hedge ratio on a rolling basis; flag parameter drift as a
  **risk**, not noise [4].

### Regime / sub-period robustness

Spread behaviour changes across regimes (bull/bear/high-vol) [4][5]. **Slice the backtest into
sub-periods** (pre/post a vol regime, COVID crash, rate-hike cycle) and require the edge to survive in
**more than one** regime before calling a relative view "robust." A pair that only worked 2021–22 is a
single-regime artifact.

### Indian transaction costs (this kills naïve pairs P&L)

Pairs trades are **two-sided and high-turnover**, so costs dominate; studies show profits "much
reduced" after costs and that fancy methods underperform plain cointegration **after costs** [4]. The
cost model (partly in `trading_costs.py`) must use **current Indian frictions** [7][8]:

| Leg type | STT (2026) | Other |
|---|---|---|
| Equity **delivery** | 0.1% buy + 0.1% sell | + exchange txn, SEBI, stamp, 18% GST on (brokerage+txn), DP charges |
| Equity **intraday** | 0.025% sell-side only | same stack |
| **Options** | 0.15% on premium (sell) / 0.15% on exercise (Apr-2026 hike) | premium-based |
| **Futures** | 0.05% sell-side | |

Plus **slippage** (realistic bid-ask + impact, larger for mid-caps; indices/large-caps tighter).
Because relative trades round-trip *both legs repeatedly*, **show gross vs net equity curves and a
"cost drag" line** — an India-realism feature most retail tools omit.

### Expression beyond "a basket"

A RELATIVE view's **proper expressions** are *spread/relative* structures, not a long-only basket:
- **Long-A / short-B pair** at the cointegration hedge ratio (textbook).
- **Ratio / relative-strength** entry on the OU z-score with explicit entry/exit bands and
  half-life-based holding.
- **Options relative** (long calls on A funded by short calls on B; or call-spread vs put-spread)
  reusing `option_strategies.py` — capital-efficient, defined-risk, fits register-not-execute.
- **Sector-neutral long/short** via existing risk-parity / min-variance weighting on the two legs.

> **India short-leg note:** no easy single-stock delivery shorting → default the short leg to
> **puts / put-spreads / futures / SLB**, never cash short.

## 1.3 THEME views — thematic backtest hygiene

A THEME view ("defence multi-year", "China+1 manufacturing", "US-tech via MON100 proxy") is a long
structural basket. The dominant failure modes are **data biases**, which must be engineered out.

### Survivorship bias

Backtesting today's constituents over history silently deletes the losers. Magnitude is large:
survivorship-free vs biased annualized returns differed **7.4% vs 9.0%** (1.6%/yr inflation) in the
classic 1926–2001 study; ETF attrition is brutal (**41–58%** of funds gone within a decade) [2].
**Mandate point-in-time constituents:** reconstruct the theme basket as it *was* at each rebalance,
including names later delisted/dropped.

### Look-ahead & pre-inclusion bias

Using *future* index membership to pick *past* holdings ("pre-inclusion bias") distorts annual returns
by **0.1–0.9%** [2]. The thematic backtest must use only information available at each point in time —
entry/screen rules evaluated as-of that date, no forward fundamentals, no "we know this became a
10-bagger."

### Basket reconstitution

Define and disclose the **rebalance cadence** (quarterly/semi-annual), reconstitution rules, and the
**turnover cost** each rebalance incurs (Indian costs from 1.2). Matters for India because thematic
baskets often hold mid/small-caps with real slippage. Where a **foreign theme** is requested, substitute
the **listed Indian ETF proxy** (e.g. MON100 for US-tech) and backtest *that* — never a fabricated
foreign series.

### Proper thematic expressions

Reuse `propose_basket_allocation` + `weighting.py` but offer **more than equal-weight**: market-cap,
**risk-parity**, **min-variance**, factor-tilt, or Black-Litterman with the user's view as the tilt.
Conservative / Balanced / Aggressive then map to **weighting scheme + concentration + optional satellite
options overlay**, not just position size. This is how a THEME stops being "a flat basket."

## 1.4 Mapping the existing Trust Battery onto all three view types

Pivot already ships the gold-standard overfitting/validation layer (Bailey & López de Prado). Here is
**how each tool applies to each view type** — the bridge between V2 strategies and the existing engine
[15][17][18][19].

| Trust-Battery tool | What it corrects | EVENT | RELATIVE | THEME |
|---|---|---|---|---|
| **Probabilistic Sharpe (PSR)** | Is SR>benchmark given skew/kurtosis/N? [15] | P(post-event SR>0) given few events | Spread SR confidence (fat tails) | Basket SR vs NIFTY |
| **Deflated Sharpe (DSR)** | **Selection bias from many trials** + non-normality [15] | Many event-windows/instruments → deflate | We scanned many pairs → **critical**, pairs are the #1 data-mining trap | Many weight schemes → deflate |
| **Min Track Record Length (MinTRL)** | Min history for SR significance [18][19] | "Need ≥K analog events" — drives the N guardrail | Min OOS days for spread edge | Min years of theme history |
| **Monte-Carlo block bootstrap** | Path/sequence luck | Resample event outcomes → CAAR *distribution* | Block-bootstrap spread (preserves autocorr) | Bootstrap basket paths |
| **Walk-forward** | Realistic sequential OOS [19] | Rolling estimation→event windows | Rolling hedge-ratio re-fit (catches ~40% decay) | Rolling reconstitution |
| **No-skill permutation** | Edge > random? | Shuffle event dates → null CAAR | Shuffle entry signals | Shuffle basket membership |
| **Trial counter** | Track # configs → feeds DSR | Count instrument/window combos | **Count pairs scanned** (huge) | Count weight schemes |
| **Trust verdict** (`insufficient_data → no_edge → unproven → promising`) | Plain-English honesty | EVENT card badge | RELATIVE badge | THEME badge |

**Key product insight:** the **Deflated Sharpe + trial counter** combination is the antidote to the
pairs/theme search problem — scanning hundreds of pairs or weight schemes and reporting the winner is
textbook overfitting, and DSR penalises exactly that. **CPCV** (combinatorial purged cross-validation)
is the natural next upgrade over walk-forward — it yields a *distribution* of OOS Sharpes and
lower PBO / higher DSR, at the cost of compute [17]. The **Trust verdict ladder already maps perfectly
onto a user-facing badge** — reused verbatim as the spine of the conviction score below.

---

# PART 2 — THE USER-FACING SCORE

## 2.1 Design principle: TWO dials, never one number

The most important honesty decision: **outcome confidence and expression confidence are different
questions and must be separate scores** [V2 spec; supported by the calibration literature]:

- **OUTCOME confidence** — *will the event / relationship / thesis actually happen?* ("Will RBI cut in
  Aug?", "Will private banks beat PSUs over 6m?"). A **forecast-calibration** problem, scored against
  base rates and what's-priced-in [21][22][23].
- **EXPRESSION confidence** — *given the event happens, does this specific structure actually pay?*
  ("Does buying BANKNIFTY calls benefit if RBI cuts? Does the pair pay if the relationship holds?"). An
  **event-study / backtest historical-alignment** problem [1][12].

A view can be **high-outcome / low-expression** (you're right about RBI but the option is so expensive
the priced-in move swamps you) or **low-outcome / high-expression** (unlikely event but the structure
pays hugely if it hits — a cheap lottery). Collapsing these into one number would hide exactly the
trade-off retail investors most need to see, and would drift toward "advice." **Two dials = honesty.**

## 2.2 The dimensions feeding each dial

### Dial 1 — OUTCOME confidence (the thesis)

| Dimension | Source | Academic basis |
|---|---|---|
| **Historical hit-rate of analog events** | 1.1 event study: fraction of N analogs that resolved in the thesis direction (base rate `b`) | Base-rate forecasting; BSS uses `b(1−b)` baseline [21][23] |
| **Base-rate vs market-priced odds** | Compare our base rate to Polymarket/Kalshi odds or option-implied probability; gap = perceived edge | Brier / calibration; proper scoring [21][22] |
| **Relationship strength** (RELATIVE only) | Cointegration p-value + correlation stability (the ~40–50% persistence reality) | [4][5] |
| **Sample sufficiency** | N analog events vs MinTRL requirement | Bailey-LdP MinTRL [18][19] |

> Honest framing: the outcome dial is a **forecast, not a fact**. If Pivot's own prior is close to
> market-priced odds, the dial should say "the market already agrees — little edge," not "high
> confidence."

### Dial 2 — EXPRESSION confidence (the structure)

| Dimension | Source | Academic basis |
|---|---|---|
| **Historical alignment / CAAR & BHAR** of this structure given the event | 1.1 event study on the *chosen instruments* | [1][6][12] |
| **Statistical significance** | BMP + non-parametric agreement; t-stat / p-value | [3] |
| **Trust verdict** | Existing Trust Battery on the expression's backtest (DSR, PBO, walk-forward) | [15][17][19] |
| **Cost-survivability** | Does net-of-Indian-cost edge survive? (1.2 / 1.3) | [4][7][8] |
| **Payoff geometry** (options) | POP / expected-move coverage from `option_strategies.py` | [14][16] |

## 2.3 Combining into a score — multi-dial, 0–100 + letter, with bands

**Recommendation: two 0–100 sub-scores → letter grades, plus a small set of always-visible flags.**

*Why this presentation over alternatives:* a single 0–100 is the most familiar to retail but collapses
the outcome/expression trade-off (rejected). A pure multi-dial radar is honest but illegible to
non-experts. The **two-number + letter** form keeps the two dimensions visibly separate (honesty),
gives a glanceable grade (legibility), and the **letter bands reuse Pivot's existing Trust ladder
language** so vocabulary is consistent across the product.

Within each dial, combine dimensions as a **weighted blend that is gated (capped) by the Trust
verdict** — statistics can *cap* the score, but no single soft dimension can inflate it (mirrors
fffinstill's pillar-blend [20], adds a hard statistical ceiling):

```
OUTCOME score    = clamp( weighted_blend(hit_rate, edge_vs_priced, relationship_strength, sample),
                          ceiling = f(Trust_verdict, N) )

EXPRESSION score = clamp( weighted_blend(CAAR/BHAR_alignment, significance, cost_survival, payoff),
                          ceiling = f(Trust_verdict, DSR, PBO) )
```

**Bands (reuse Pivot's Trust ladder so language is consistent):**

| Range | Letter | Outcome-dial meaning | Expression-dial meaning | Trust verdict |
|---|---|---|---|---|
| 80–100 | **A** | Strong, well-priced edge vs market | Historically + statistically aligned, survives costs | **promising** |
| 60–79 | **B** | Favorable but partly priced-in | Aligned but thinner significance / cost drag | **promising / unproven** |
| 40–59 | **C** | Coin-flip / market already agrees | Mixed; works in some regimes only | **unproven** |
| 20–39 | **D** | Thesis weak or contradicted by base rate | Poor alignment / negative net of costs | **no_edge** |
| 0–19 | **E** | Contradicted / no analog support | Structure doesn't benefit even if event hits | **no_edge** |
| n/a | **—** | "Not enough history (N=k)" | "Not enough history" | **insufficient_data** → score suppressed |

**Crucial gate:** if Trust verdict = `insufficient_data` (N below MinTRL), **do not display a number at
all** — show *"Too few analog events (N=3) to score honestly."* This single rule prevents the worst
failure (a confident-looking 72 built on 3 events) [18][19].

## 2.4 Example score cards

### Example 1 — EVENT view (high outcome, modest expression)

**View:** "RBI holds rates in Aug → BANKNIFTY rallies." **Expression (Balanced):** BANKNIFTY bull-call
spread, Confirmation timing.

```
OUTCOME confidence:   B  (72/100)
  • Analog hit-rate: RBI held in 9/12 recent meetings; BANKNIFTY +ve in 7/9   (N=12)
  • Base rate vs priced-in: our 75% vs Kalshi/option-implied ~70% → small +edge
  • Sample: N=12 ≥ MinTRL ✓    Trust: promising
EXPRESSION confidence: C  (54/100)
  • Historical alignment: mean CAAR[+1,+5] = +1.1% (BMP t=2.0, sign test agrees)
  • BUT option-implied expected move already ~1.4% → priced-in swamps edge
  • Net of STT/slippage on spread: edge ≈ break-even    Trust: unproven
  • POP of the spread: ~48%
FLAGS: event largely priced-in · 12 analogs · net-of-cost thin
→ "You're probably right on RBI, but the market has priced most of it; the spread is close to fair."
```

### Example 2 — RELATIVE view (overfitting risk caught)

**View:** "HDFCBANK beats SBIN over 3 months." **Expression:** long HDFCBANK / short SBIN at hedge
ratio.

```
OUTCOME confidence:   C  (49/100)
  • Cointegration: in-sample p=0.03 ✓ but OOS p=0.21 ✗ (relationship not stable)
  • Correlation persistence into OOS ~50% (literature base rate)
  • OU half-life drifted 8d → 31d → relationship weakening
EXPRESSION confidence: D  (31/100)
  • Spread Sharpe in-sample 1.8 → Deflated Sharpe 0.4 after 140 pairs scanned
  • Walk-forward: edge only in 1 of 4 sub-periods (single-regime)
  • Net of two-sided STT+slippage: P&L turns negative    Trust: no_edge
FLAGS: 140 pairs scanned (selection bias) · single-regime · cost-negative
→ "This pair looks good only in-sample; after honest deflation and Indian costs there's no edge."
```

### Example 3 — THEME view (clean, properly built)

**View:** "India defence multi-year." **Expression:** risk-parity basket of point-in-time defence
constituents, quarterly rebalance.

```
OUTCOME confidence:   B  (68/100)  — structural thesis, no precise resolution date
                                     (themes score outcome qualitatively)
EXPRESSION confidence: B  (74/100)
  • Survivorship-free, point-in-time basket; pre-inclusion bias removed
  • Backtest net of rebalance costs: CAGR > NIFTY, max-DD disclosed
  • Probabilistic Sharpe 0.9; walk-forward survives 3 of 4 sub-periods
  • Risk-parity weighting beats equal-weight on Sortino    Trust: promising
FLAGS: theme = long-horizon, no hard resolution date · quarterly turnover cost shown
→ "Historically well-aligned and bias-controlled; remember themes are slow and this measures the past."
```

---

# PART 3 — GUARDRAILS

So the score is **reference, never advice or certainty**:

1. **Two dials, never averaged into one** — averaging would imply a single "go" signal = advice.
2. **Hard suppression below MinTRL / `insufficient_data`** — no number on thin samples [18][19].
3. **Deflate for search** — the score must consume the **trial counter** so scanning many
   pairs/weights/windows *lowers*, not raises, the grade (Deflated Sharpe principle) [15]. Surface "we
   evaluated K configurations" on the card.
4. **Always net-of-cost** — Indian STT/slippage applied; show **gross vs net** so the number isn't a
   frictionless fantasy [4][7][8].
5. **Model-conditional language** — "relative to the market-model baseline over N analog events," never
   "will." Honors the joint-hypothesis / bad-model caveat [11][13].
6. **Past ≠ future banner** — *"measures historical alignment, not future price direction; a high score
   can still lose"* [20].
7. **Not advice / not a broker** — the score ends with Pivot's standard "analysis, not financial
   advice." **Register-not-execute unchanged** — the score decorates the *card the user arms in their
   own broker app*; it never auto-sizes or auto-trades.
8. **Calibration self-audit** — log realized outcomes and compute Pivot's own **Brier score /
   reliability** over time, so the OUTCOME dial stays honest and can be re-weighted if mis-calibrated
   [21][22][23]. Roadmap item, but the logging hook should exist from day one.
9. **No fabricated inputs** — every dimension quotes a **real tool value** (CAAR, p-value, IV,
   cointegration p, cost); if a value is unavailable, the dimension is **greyed, not guessed**.

---

# PART 4 — IMPLEMENTATION NOTES

## 4.1 Reuse (already in the codebase)

| Need | Existing module |
|---|---|
| Normal-return regression, abnormal returns, betas | `services/backtest/*`, `compare_performance`, correlation matrix |
| OOS / walk-forward / forward stats | `services/backtest/forward_stats.py` |
| PSR / DSR / MinTRL / MC bootstrap / permutation / trial counter | Trust Battery in `services/backtest/*` |
| Plain-English verdict ladder (`insufficient_data → no_edge → unproven → promising`) | `services/backtest/verdict.py` — **reuse verbatim as the score badge spine** |
| Pairs / cointegration / OU half-life | `services/backtest/pairs/` (Engle-Granger, Johansen) |
| Basket allocation + weighting schemes | `propose_basket_allocation`, `weighting.py`, `sector_universe.py` |
| Defined-risk option structures + greeks/POP/payoff/margin/critique | `services/option_strategies.py` |
| Macro scenarios (winners/losers/confirm/invalidate) | `thematic_map.py` |
| "What's priced in" | Polymarket/Kalshi adapters (read-only) + option expected-move from the chain |
| Indian cost model | `trading_costs.py` (extend with current STT schedule) |
| Arming the card | workflow engine triggers (`schedule/price/indicator/event/scheduled_macro/polymarket/kalshi`) |

## 4.2 Net-new (build, in priority order)

1. **Event-Study CAR/CAAR/BHAR module** — gather N past instances of an event tag, compute abnormal
   returns on the chosen instrument(s) conditioned on **surprise sign/magnitude**, run the result
   through the existing Trust Battery → the **EXPRESSION dial** + the alignment number. Reuses
   `compare_performance` + correlation betas + the battery. *(Highest leverage — the structures already
   exist; this is the missing aggregator.)*
2. **Significance test pack** — BMP (standardized cross-sectional) + Kolari-Pynnönen clustering
   adjustment + a non-parametric (generalized sign / Corrado rank / generalized rank-t); require
   parametric + non-parametric **agreement** before labelling an effect reliable [3].
3. **Priced-in / implied-move calculator** — derive the event's expected move from the ATM straddle
   (already have the chain) → feeds the OUTCOME dial's "edge vs priced" and the EXPRESSION dial's
   "priced-in swamps edge" check. Small, high-leverage [14][16].
4. **Two-dial Score aggregator** — the `clamp(weighted_blend(...), ceiling=f(Trust_verdict, …))` logic,
   band/letter mapping, and the `insufficient_data` suppression gate. Pure composition over existing
   outputs; no new statistics.
5. **Event/surprise + analog-sample feeds** — RBI/FOMC dates + consensus/OIS-implied probabilities;
   earnings calendar + consensus estimates (SUE) — without consensus, fall back to **EAR-only** (3-day
   announcement abnormal return, computable from Kite OHLCV + NIFTY); corporate-actions/deal feed;
   political-event calendar. **This is the dominant *data* gap.**
6. **Point-in-time constituent store** (THEME) — survivorship-free, as-of membership for thematic
   baskets; rebalance/turnover accounting. Plus universe tags (duration-ETF set, gold ETF/SGB set,
   energy importer-vs-exporter map).
7. **CPCV upgrade** (optional, later) — combinatorial purged cross-validation over walk-forward for a
   *distribution* of OOS Sharpes and tighter PBO/DSR [17].
8. **Calibration logger** — persist (predicted probability, realized outcome) per view to compute
   Pivot's rolling **Brier / reliability**; re-weight the OUTCOME dial if mis-calibrated [21][22][23].

## 4.3 India realism to hard-code into every card

- **Weeklies:** NIFTY 50 & SENSEX only (BANKNIFTY is monthly-only as of 2026) — pick the liquid event
  vehicle accordingly.
- **Single-stock options:** monthly + **physically settled**, STT-on-intrinsic at expiry → flag/force
  pre-expiry square-off.
- **No easy single-stock delivery shorting** → short legs default to **puts / put-spreads / futures /
  SLB**, never cash short.
- **MCX commodities (crude, gold, silver, metals, natgas) are tradeable via register-not-execute**
  (leveraged — keep the risk caveat) → oil/commodity views can go **direct (MCX futures/options)** *in
  addition to* routing through equities + gold ETFs/SGBs.
- **Foreign legs** offered only as **Indian ETF proxies** (e.g. MON100).
- **Everything register-not-execute, defined-risk-first** — Pivot arms a bounded-max-loss structure;
  the user confirms in their broker.

---

## Sources

1. [Event study — Wikipedia](https://en.wikipedia.org/wiki/Event_study)
2. [Bias-Free Backtesting / survivorship & look-ahead — sharpely.in](https://sharpely.in/blogs/bias-free-backtesting-explained-sharpely-uses-point-time-data-avoid-look/); [Survivorship Bias — QuantifiedStrategies](https://www.quantifiedstrategies.com/survivorship-bias-in-backtesting/)
3. [Event Study Significance Tests — EventStudyTools](https://www.eventstudytools.com/significance-tests)
4. [Robust dynamic pairs trading with cointegration — ScienceDirect](https://www.sciencedirect.com/science/article/abs/pii/S0167637716302930)
5. [Cointegration vs correlation persistence — Amberdata](https://blog.amberdata.io/crypto-pairs-trading-why-cointegration-beats-correlation)
6. [The Basics of Event Study — The Data Hall](https://thedatahall.com/the-basics-of-event-study/)
7. [STT rates 2025/2026 — Zerodha](https://support.zerodha.com/category/account-opening/resident-individual/ri-charges/articles/how-is-the-securities-transaction-tax-stt-calculated); [ClearTax STT guide](https://cleartax.in/s/securities-transaction-tax-stt)
8. [Securities Transaction Tax — NSE Clearing](https://www.nseclearing.in/clearing-settlement/equity-derivatives/securities-transaction-tax)
9. [Post-Earnings-Announcement Drift Anomaly in India — ResearchGate](https://www.researchgate.net/publication/328500162_Post-Earnings-Announcement_Drift_Anomaly_in_India_A_Test_of_Market_Efficiency)
10. [EMH & PEAD: Turnaround Companies in India — Ganguli (SSRN)](https://papers.ssrn.com/sol3/Delivery.cfm/SSRN_ID1619234_code1082496.pdf)
11. [Econometrics of Event Studies — Kothari & Warner](https://www.bu.edu/econ/files/2011/01/KothariWarner2.pdf)
12. [The Buy-and-Hold Abnormal Return Approach](https://1library.net/article/the-buy-and-hold-abnormal-return-approach.zwgdnogq)
13. [Measuring Long-Horizon Security Price Performance — Kothari & Warner 1997](https://leeds-faculty.colorado.edu/bhagat/KothariWarner1997.pdf)
14. [Expected Move in Options — Volatility Box](https://volatilitybox.com/research/expected-move-options/)
15. [The Deflated Sharpe Ratio — Bailey & López de Prado (SSRN)](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551)
16. [Calculating Expected Moves Using Options — Options Hawk](https://optionshawk.com/calculating-expected-moves-using-options/)
17. [Purged cross-validation / CPCV — Wikipedia](https://en.wikipedia.org/wiki/Purged_cross-validation); [Backtest overfitting comparison — ScienceDirect](https://www.sciencedirect.com/science/article/abs/pii/S0950705124011110)
18. [The Probability of Backtest Overfitting — Bailey, Borwein, López de Prado, Zhu](https://www.researchgate.net/publication/318600389_The_probability_of_backtest_overfitting)
19. [The Dangers of Backtesting (PSR/DSR/MinTRL/walk-forward) — Portfolio Optimization Book §8.3](https://portfoliooptimizationbook.com/book/8.3-dangers-backtesting.html)
20. [Conviction Score (0–100) — fffinstill](https://fffinstill.com/learning/concepts/conviction-score-0-100)
21. [The Brier Score for Probability Forecasts — MetricGate](https://metricgate.com/blogs/brier-score-explained/)
22. [How to Measure Forecasting Calibration — Convexly](https://www.convexly.app/answers/how-to-measure-forecasting-calibration)
23. [Brier Skill Score: Definition and Evaluation — EmergentMind](https://www.emergentmind.com/topics/brier-skill-score)
