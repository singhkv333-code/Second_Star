# View Markets — The Multi-Framework Strategy Derivation Engine

> *From "event → top gainers → basket" to an institutional event-intelligence engine.*
>
> Companion to `VIEW_MARKETS_STRATEGY_DESIGN.md` (how a single expression is
> *constructed*) and `VIEW_MARKETS_TESTING_AND_SCORING.md` (how it is *tested/scored*).
> This doc defines the layer **above** both: how, given a belief/event, Pivot
> **generates many candidate expressions from several independent frameworks**,
> then adjudicates them into a small, diverse, evidence-carrying set.
>
> Grounded in the real repo (2026-07-01). Every "EXISTS" names a module that is
> actually present in `pivot/backend/view_markets/`. When this drifts from code,
> the code wins.

---

## Part 0 — The philosophy: positive expected value, not prediction

You can never be certain. If certainty existed the market would arbitrage it away
— *markets don't pay for certainty; they pay for bearing uncertainty, for being
right when others are wrong, for superior interpretation.* So the objective is
never `Event → Guaranteed Return`. It is:

```
Event / Belief  →  Distribution of outcomes  →  Positive expected value  →  Honestly-scored expression
```

Five commitments follow, and they govern every framework below:

1. **Evidence, not guarantees.** A view must be backed by history *and/or* causal
   logic *and/or* fundamental exposure — never "economics says so, buy this basket."
2. **Distribution, not a point.** We derive `Event → distribution of returns`
   (hit-rate, mean, median, worst, percentiles), not `Event → one number`.
3. **Surprise, not outcome.** Markets move on `Actual − Expected`. A fully-priced
   "good" outcome pays nothing. Every event study conditions on the **surprise**.
4. **Expressions, not securities.** `Event → a menu of expressions` (basket, pair,
   option, cross-asset, contrarian), each with its own risk/payoff — not `Event → a stock`.
5. **The absence of a trade is a valid answer.** Some sides of some events have no
   clean expression; the engine says so instead of forcing one.

**The positioning statement (the honest north star):**

> We are not predicting the future. We identify situations where historical
> evidence and economic reasoning suggest specific expressions have exhibited
> **positive expected outcomes** — shown with their full distribution, sample
> size, and confidence. High evidence can still lose. This is analysis, not advice.

---

## Part 1 — What's wrong with single-framework (event → top gainers)

The shipped V1 views (`precompute.py`: monsoon, weak-IT, crude) are one framework:

```
Event occurs → find in-window top gainers → estimate event beta → basket
```

This is *event-study + cross-sectional momentum*, and it silently ships four
failure modes (your list, all real):

- **Unrelated rallies** — a top gainer may have moved for reasons unrelated to the event.
- **Already priced** — the move happened before the window; the basket buys the exhaust.
- **Missed second-order** — the best beneficiary is a supplier two hops away, never a top gainer.
- **Non-repeating winners** — last occurrence's winners are this occurrence's laggards (survivorship + regime).

**One framework = one failure mode, undiversified.** The fix is not a better single
framework — it is **many independent frameworks whose errors are uncorrelated**,
adjudicated together. That is the moat: anyone can build `Question → top gainers`;
almost nobody builds `Question → causality + event-study + factors + fundamental
sensitivity + cross-asset → a diverse menu of scored expressions`.

---

## Part 2 — The engine architecture

```
                 Belief / Event (curated view)
                            │
        ┌───────────────────┼───────────────────────────────┐
        │  A. EVIDENCE      │  B. CAUSALITY   │  C. EXPOSURE  │   ← candidate GENERATORS
        │  (backward, stat) │  (structural)   │ (fundamental) │      (independent analysts)
        │  F1 event study   │  F2 transmission│  F3 factor    │
        │  F6 abnormal ret  │  F8 network/2nd │  F4 revenue-Δ │
        │  F9 surprise      │  F11 narrative  │  F5 earnings-Δ│
        │  F10 analog search│                 │               │
        └─────────┬─────────┴────────┬────────┴──────┬────────┘
                  │  each emits candidate driver→beneficiary hypotheses
                  ▼
           ┌──────────────┐
           │ CANDIDATE POOL│  (driver, beneficiary/loser, direction, rationale, source-framework)
           └──────┬────────┘
                  │
        D. CONDITIONERS re-weight every candidate:
        F7 regime fit · F12 crowd positioning · F13 cross-asset lens
                  │
                  ▼
        ┌───────────────────────┐
        │ CLUSTER + DIVERSITY    │  collapse near-duplicates; keep a set with
        │ SELECTION              │  DECORRELATED failure modes (§7)
        └──────────┬────────────┘
                   │
     E. SYNTHESIS: F14 turn surviving drivers into tradeable EXPRESSIONS,
        F15 add a contrarian candidate → Conservative / Balanced / Aggressive /
        Relative / Contrarian  (expressions/dispatch.py + catalog + builders)
                   │
                   ▼
        ┌───────────────────────┐
        │ EVIDENCE + SCORING     │  event_study + confidence(two-dial) + MC distribution
        │ → Evidence Card (§4)   │  capped by Confidence Tier (§5)
        └──────────┬────────────┘
                   ▼
           Deployment (timing→trigger, register-not-execute)
```

**The key architectural change:** today `expressions/dispatch.suggest_expressions`
is a *single* archetype-driven generator. It becomes **one of N generators (E/synthesis)**
that consumes a **candidate pool** produced by Families A/B/C and conditioned by D.
The pool + conditioners + diversity selection is the net-new orchestration layer.

---

## Part 3 — The 15 frameworks, mapped to the real repo

Legend: **EXISTS** (built, reuse) · **PARTIAL** (pieces exist, needs a generator wrapper) · **GAP** (build).

### Family A — Evidence generators (backward-looking, statistical)
*"What actually happened, isolated from noise, conditioned on surprise?"*

| # | Framework | Method | Pivot primitive | Status |
|---|---|---|---|---|
| **F1** | **Event study** | CAAR of analog events on chosen instruments, split by surprise sign/magnitude | `view_markets/event_study.py` (`run_event_study`, `EventStudyResult`) + Trust Battery | **EXISTS** |
| **F6** | **Abnormal returns** | `AR = actual − (α+β·mkt)` (market model), not raw return — isolates event impact from beta/market drift | `event_study.abnormal_returns_for_event` + `compare_performance`/correlation betas | **EXISTS** |
| **F9** | **Surprise model** | `Surprise = Actual − Expected`; abnormal return materialises only when realized **exceeds priced-in** | `view_markets/expectations.py` (`compute_surprise`) + `implied_move.py` (expected move, implied prob) | **EXISTS** |
| **F10** | **Similar-event retrieval** | pull a *richer* analog sample (Gulf War, Soleimani, Russia-Ukraine…) to fight small-N | `view_markets/feeds.sample_analog_events` (shim today) → real analog store | **PARTIAL** |

> F1/F6/F9 are the biggest surprise: Pivot already does event study **the right
> way** (abnormal returns + surprise-conditioning + significance), it's just not
> wired into the shipped views. **Move the shipped views onto `event_study.py`.**

### Family B — Causality generators (structural, few observations needed)
*"How should this propagate, and who is indirectly connected?"*

| # | Framework | Method | Pivot primitive | Status |
|---|---|---|---|---|
| **F2** | **Economic transmission graph** | walk the cause→effect DAG (`RBI cut → borrowing cost↓ → credit↑ → bank earnings → autos`); each node = a candidate beneficiary basket | `view_markets/transmission.py` (`build_dag`, `seed_transmission_from_scenario`) + `thematic_map.py` winners/losers/confirm/invalidate | **PARTIAL** (enricher today → make it *emit candidates*) |
| **F8** | **Network / 2nd-order** | surface indirect beneficiaries (defence↑ → HAL/BEL direct; electronics/metals/logistics suppliers indirect) via supply-chain + correlation-cluster graph | correlation matrix clustering + transmission DAG depth ≥2 | **GAP** |
| **F11** | **Narrative similarity** | map a narrative to historical analogs (AI ⟵ dot-com/cloud/mobile) → infra/software/semis plays | analog-narrative store over `thematic_map` themes | **GAP** |

### Family C — Exposure generators (fundamental)
*"Who is economically / earnings exposed, independent of price history?"*

| # | Framework | Method | Pivot primitive | Status |
|---|---|---|---|---|
| **F3** | **Factor exposure** | screen on characteristics that benefit (GDP-surprise → high domestic-rev, high op-leverage, cyclical, high-beta); rank cross-sectionally | `expressions/cross_sectional.py` (`rank_scores`, `decile_split`, `composite_factor_scores`, `factor_etf`) + `weighting.py` `factor` | **EXISTS** |
| **F4** | **Revenue sensitivity** | `ΔRevenue / ΔDriver` (oil↑ → ONGC +, airlines −, paints −) from segment/fundamentals data | Moneycontrol fundamentals DB + segment tags → new elasticity table | **GAP** |
| **F5** | **Earnings sensitivity** | whose *profits* move most (rate cut → bank NIMs, auto demand, housing volumes); op-leverage aware — often beats price sensitivity | fundamentals DB (margins, leverage) → new earnings-elasticity score | **GAP** |

### Family D — Conditioners (re-weight every candidate; don't generate)
*"Does this hold in THIS regime, and is the crowd already there?"*

| # | Framework | Method | Pivot primitive | Status |
|---|---|---|---|---|
| **F7** | **Correlation regimes** | the same event flips sign by regime (oil↑ → energy↑ *or* ↓ by inflation/valuation regime); condition every candidate on regime | regime tags over `thematic_map` scenarios + rolling correlation matrix | **PARTIAL** |
| **F12** | **Crowd positioning** | down-weight already-crowded trades ("everyone long gold" → war may not move it); `Priced-in ⇒ small surprise` | `prediction_market.py` (Polymarket/Kalshi, read-only) + FII/DII flows + option-implied prob (`implied_move.py`) | **PARTIAL** |
| **F13** | **Cross-asset lens** | the best expression may be **bonds / gold / vol**, not equities (RBI cut → maybe long duration, not banks) | `expressions/commodities.py` (MCX gold/silver/crude) + gilt/Bharat-Bond ETFs + `option_strategies` vol | **PARTIAL** |

### Family E — Synthesis (drivers → tradeable, tiered expressions)
*"Turn surviving drivers into a diverse menu, not a stock."*

| # | Framework | Method | Pivot primitive | Status |
|---|---|---|---|---|
| **F14** | **Derive expressions, not securities** | one driver → {basket, optimised long-short, index/stock option, cross-asset, pair, hedge}; honest short; timing→trigger | `expressions/dispatch.suggest_expressions` + `catalog.py` + `builders/{basket,pair,hedge,multi_asset,option}` + `honest_short.py` + `timing.py` + `tiers.py` | **EXISTS** |
| **F15** | **Contrarian expression** | when positioning (F12) is crowded, generate the *opposite/second-order* trade (all long defence → short airlines / long gold / long vol) | new contrarian generator gated on F12 crowding + `honest_short.py` | **GAP** |

---

## Part 4 — The Evidence Card (ships on every expression)

Every expression carries a card that shows the **distribution and the evidence**,
never a bare "buy this." Composed from `event_study.py` + `confidence.py` +
`expectations.py` + the Monte-Carlo distribution (already in `precompute.py`).

```
┌─ EXPRESSION: Long PSU-bank basket (Confirmation timing) ──────────────┐
│  DISTRIBUTION OF OUTCOMES  (not a point estimate)                     │
│    Historical similar events ....... 22        (N — shown prominently)│
│    Positive-outcome frequency ...... 68%                              │
│    Average return .................. +7.2%     Median ...... +5.1%    │
│    Worst / Best (5–95pct) .......... −9% / +18%                       │
│    ▁▂▄▆█▆▄▂▁  (fan chart / MC terminal distribution)                  │
│                                                                       │
│  SURPRISE FRAME  (P&L = realized − priced-in)                        │
│    Expected (option-implied move) .. ±1.4%   ·  our view ... +3%      │
│    Edge vs priced-in ............... small-positive                   │
│                                                                       │
│  TWO DIALS  (never averaged — confidence.py)                          │
│    OUTCOME confidence  B (72)   ·   EXPRESSION confidence  C (54)     │
│                                                                       │
│  EVIDENCE SCORE  ★★★★☆   (capped by Confidence Tier, §5)             │
│    Historical data ★★★★★ · Economic logic ★★★★★ · Sample ★★★☆☆        │
│    Regime fit ★★★★☆ · Positioning ★★★☆☆ · Liquidity ★★★★☆            │
│                                                                       │
│  Confidence Tier: 1 (statistically proven)  ·  Framework: F1+F2+F5    │
│  "High evidence can still lose. Analysis, not advice."                │
└───────────────────────────────────────────────────────────────────────┘
```

The **Distribution** block is the core reframe: `Event → distribution`, not
`Event → security`. Reuse `monte_carlo_terminal_distribution` (already producing
p05/median/p95/prob_loss in `precomputed_views.json`) for the fan chart, and
`event_study.EventStudyResult` for hit-rate/CAAR/worst.

---

## Part 5 — Confidence tiers govern *what evidence is required*

The tier is not decoration — it decides which frameworks must fire and **caps the
Evidence Score** so a narrative can never masquerade as proven.

| Tier | Name | Examples | Required evidence | Score ceiling |
|---|---|---|---|---|
| **1** | **Statistically proven** | earnings surprise, rate cuts, momentum/value factors | Family A: N ≥ MinTRL, significance (BMP + non-parametric **agree**), DSR pass | up to ★★★★★ |
| **2** | **Moderately supported** | oil shocks, elections, trade wars | small N → **A augmented by B (economics) + C (exposure) + D (regime)**; analog-search (F10) to lift N | capped ★★★★☆ |
| **3** | **Narrative** | AI revolution, India manufacturing | **B (transmission/narrative-analog) + C (exposure)**; explicitly labelled; **no significance claim** | capped ★★★☆☆ |

**Rule:** insufficient sample (below MinTRL) ⇒ the OUTCOME dial shows *"too few
analog events (N=k) to score"* and the number is suppressed — never a confident 72
on 3 events (`confidence.py` already implements the MinTRL suppression gate).

This is exactly the institutional layered approach: `Confidence = f(History,
Economics, Regime, Positioning)` — Family A (history), B (economics), D (regime,
positioning) — so an event with only 5–10 observations (US-strikes-Iran) can still
earn Tier 2 on economics + analogs, instead of being discarded for thin data.

---

## Part 6 — Evidence Score composition

Two user-facing dials (kept separate) + a star Evidence Score that *summarises the
inputs but is capped by statistics*:

```
OUTCOME dial     = f(analog hit-rate, edge-vs-priced-in, relationship strength, sample)   [confidence.score_outcome_dial]
EXPRESSION dial  = f(CAAR/BHAR alignment, significance, cost-survival, payoff geometry)    [confidence.score_expression_dial]

EVIDENCE SCORE (★) = clamp(
    weighted_blend( Historical, Economic, Sample, RegimeFit, Positioning, Liquidity ),
    ceiling = Confidence_Tier )        # statistics CAP; no soft dimension inflates past the tier
```

- **Historical** ← F1/F6 event study strength (hit-rate × effect-size, shrunk by N).
- **Economic** ← F2 transmission-path clarity + F4/F5 fundamental-sensitivity magnitude.
- **Sample** ← N vs MinTRL (F10 analog search raises this).
- **Regime fit** ← F7 (does the relationship hold in the current regime?).
- **Positioning** ← F12 (crowded = lower; under-owned = higher).
- **Liquidity** ← expression tradeability (index-option > pair > basket-of-illiquids; `honest_short.py` gates).

Reuses `confidence.two_dial_score` (the clamp-by-Trust-verdict pattern already
exists); the Evidence Score is a thin presentation layer over the same inputs.

---

## Part 7 — Diversity selection (the anti-"three flavors of the same beta")

The candidate pool will contain many correlated ideas (long banks / long NBFCs /
long autos are all "long domestic beta"). The final menu must be **decorrelated in
its failure modes**, so the user gets a *distribution of expressions*, not one bet
sliced three ways:

```
From the scored pool, pick a set that spans failure modes:
  • one DIRECTIONAL beta expression        (e.g. bank basket)        — fails if market falls
  • one MARKET-NEUTRAL relative/pair       (e.g. NBFC vs bank)       — fails if the SPREAD breaks, not the market
  • one CROSS-ASSET / hedged               (e.g. long duration ETF, or collar) — fails on a different driver
  • one CONTRARIAN / second-order (F15)    (e.g. short airlines, long gold) — pays when the consensus trade doesn't
Then map onto tiers: Conservative / Balanced / Aggressive + a Relative + a Contrarian.
```

Selection criterion: maximise **evidence-weighted return per unit of *shared* risk**
— prefer candidates whose return drivers are least correlated with those already
chosen (a greedy decorrelation pick over the correlation matrix). This is what turns
"top-3 gainers" into a genuine portfolio of expressions.

---

## Part 8 — Build order (EXISTS / PARTIAL / GAP)

| Priority | Work | Frameworks | Status | Why first |
|---|---|---|---|---|
| **P0** | **Wire the shipped views through `event_study.py`** (abnormal-return CAAR + surprise + two-dial), replacing the top-gainer episode path in `precompute.py` | F1,F6,F9 | reuse EXISTS | The engine already exists; the shipped views just don't use it. Biggest credibility win for least code. |
| **P1** | **Candidate-pool orchestrator** — a `derive_candidates(view) -> list[Candidate]` that fans out to A/B/C generators and returns `(driver, beneficiary, direction, framework, rationale)` | all | GAP (glue) | The core net-new layer; everything else plugs in. |
| **P1** | **Transmission-as-generator** — make `transmission.py` *emit* candidate baskets per node, not just enrich | F2 | PARTIAL | Causality needs no large N — unlocks Tier-2/3 views. |
| **P2** | **Revenue- & earnings-sensitivity engines** over the fundamentals DB | F4,F5 | GAP | The fundamental-exposure moat; "who's *economically* exposed." |
| **P2** | **Diversity/decorrelation selector** over the candidate pool | §7 | GAP | Turns candidates into a real expression menu. |
| **P2** | **Evidence Card + Outcome-distribution surface** (reuse MC + event_study + confidence) | §4 | PARTIAL | The user-facing honesty artefact. |
| **P3** | **Analog-event & narrative-similarity retrieval** (real store behind `feeds.sample_analog_events`) | F10,F11 | PARTIAL/GAP | Lifts small-N events (oil/geopolitics) into Tier 2. |
| **P3** | **Regime + positioning conditioners** (F7 rolling-corr regime tag; F12 crowding from PM odds + FII flows + implied prob) | F7,F12 | PARTIAL | Re-weights the pool; catches "already priced" / "wrong regime." |
| **P3** | **2nd-order network + contrarian generators** | F8,F15 | GAP | The differentiated, underappreciated candidates. |
| **P4** | **Cross-asset candidate generator** (bonds/gold/vol compete with equities) | F13 | PARTIAL | "Best trade may be long duration, not banks." |

> Note the shape: **P0 is pure reuse** (the shipped views are worse than the code
> Pivot already has). The real new build is **P1's orchestrator + the exposure
> engines + the diversity selector** — the layer that makes it multi-framework.

---

## Part 9 — Worked example: "Will RBI cut rates?"

One event, run through the frameworks, producing **different, decorrelated**
candidates (not three flavors of "long banks"):

| Framework | Candidate it proposes | Evidence it carries |
|---|---|---|
| **F1 event study** | Long Bank Nifty into/after the decision | CAAR of last 12 cuts, hit-rate 67%, worst −6%, N=12 (Tier 1) |
| **F9 surprise** | *Only* trade if cut > priced-in (option-implied) | expected 25bp priced; edge only on 50bp/dovish surprise |
| **F2 transmission** | RBI cut → credit↑ → **autos** basket (2nd node) | causal chain, needs no large N (Tier 2 support) |
| **F5 earnings-Δ** | **NBFC** basket (NIMs benefit more than banks) | earnings elasticity > price beta — the pro's leg |
| **F3 factor** | High-domestic-rev / high-op-leverage screen | cross-sectional rank, decile long |
| **F13 cross-asset** | **Long duration (gilt/Bharat-Bond ETF)** — sometimes the *best* cut trade | rate-sensitivity, decorrelated from equity beta |
| **F12 positioning** | Down-weight banks if already crowded (FII long) | PM/flows crowding signal |
| **F15 contrarian** | If banks crowded → **NBFC-vs-bank pair** or long duration | pays when the consensus bank trade doesn't |

**Diversity selection → the menu:**
- **Conservative** — rate-sensitive basket (risk-parity), Confirmation timing · *72*
- **Balanced** — **NBFC-vs-bank pair** (market-neutral; F5+F15) · *74*
- **Aggressive** — Bank Nifty bull-call spread, Pre-position (surprise-gated by F9) · *68*
- **Cross-asset** — long gilt/Bharat-Bond ETF (F13) · *66*
- **Relative** — long domestic-cyclicals / short FMCG · *70*

Each ships an Evidence Card (§4) with its own distribution, N, and tier. None is "trust us."

---

## Part 10 — The moat

Everyone can build `Question → top gainers → basket`. Almost no retail system builds:

```
Question
  → Economic causality (transmission, 2nd-order network)
  + Historical event studies (abnormal returns, surprise-conditioned)
  + Factor & fundamental sensitivity (revenue-Δ, earnings-Δ)
  + Regime & positioning conditioning
  + Cross-asset alternatives
  → a DIVERSE menu of expressions, each with a distribution + evidence + confidence tier
```

That is an **event-intelligence engine**, not a strategy recommender — and Pivot is
~60% of the way there in code already. The remaining work is the **orchestrator +
exposure engines + diversity selector**, plus moving the shipped views onto the
event-study machinery they currently bypass.

**The one sentence to keep everyone honest:** *we don't predict the future — we find
situations where evidence and reasoning suggest an expression has positive expected
value, and we show you the whole distribution, sample size, and confidence so you
can decide.* Intellectually honest, statistically defensible, institution-grade.

---

## Part 11 — The analysis universe & compute budget

The analysis universe is **event-driven and ≥150–200 securities**, not "the Nifty 50."
It is a union of three pools, selected per view by the affected drivers:

1. **Equities — Nifty-500, sector-driven.** `scripts/strategy_research/v3/universe.py`
   already holds the **500-name universe across 20 NSE sectors** (`industry_map()` /
   `sector_symbols(sector)`), cached in `close_all.parquet`. The selector takes the
   view's **affected sectors** (from F2 transmission / `thematic_map` winners-losers)
   and unions their Nifty-500 members → naturally 150–200 names.
2. **Commodities — MCX futures/options** (`CRUDEOIL, GOLD, SILVER, NATGAS, COPPER,
   ZINC, ALUMINIUM`) added **whenever the driver is a commodity**, as first-class
   tradeable legs (register-not-execute, leveraged — keep the caveat). Historical
   *series* for the abnormal-return math uses the **liquid proxy** per
   `expressions/commodities.price_history_available()` — global (`BZ=F`, `GC=F`,
   `SI=F`) or ETF (`GOLDBEES.NS`, `SILVERBEES.NS`) — while the **tradeable leg** is
   the MCX contract. `drivers.parquet` already carries Brent; widen it to gold/
   silver/metals/USD-INR so every commodity driver has a cached series.
3. **Options — index + liquid single-stock F&O** (NIFTY/BANKNIFTY/FINNIFTY/SENSEX +
   the ~190–208 F&O names) — the **high-convexity leg** (Part 12). Included for any
   view whose underlying is F&O-eligible; sized/priced from the **live chain**, never
   from a fabricated historical option series.

**`event_universe(view) -> tickers`** = `∪(affected-sector Nifty-500 members)
∪ affected commodities ∪ F&O-eligible options subset → dedup → liquidity screen`.

**Compute budget (why 150–200 × many views is *not* "too much"):**
- **One universe-wide returns matrix, built once, parquet-cached** (`close_all.parquet`,
  extended with commodity/proxy columns). Everything downstream is a **vectorized
  row-slice** of it: `post_event = (1+returns.loc[d+1:d+T, cols]).prod()-1` — all 200
  names at once. CAAR over N events = one numpy reduction.
- **Fetch once/day, bulk + incremental append** (`fetch_bars`, chunked yfinance +
  retry) — never per-view, never per-strategy.
- **Abnormal returns per `(driver, universe)` computed once, cached**; reused by every
  view/framework on that driver.
- **Route by applicability + stage the compute** (§ the routing rule): a view lights up
  ~5–7 frameworks, not 15; the heavy Trust Battery runs only on the ~3–5 diverse
  finalists, *after* selection.
- **yfinance is NOT used for sector→constituents** (its `yf.Sector` is US-weighted —
  verified: `Sector('technology')` returns NVDA/AAPL/…, 0 Indian names). NSE's
  Nifty-500 classification is the source; yfinance is only for OHLC + per-ticker tags.

---

## Part 12 — The payoff ladder: offering 25%+ **honestly** (options + commodity futures)

**Why this exists.** A basket/pair returns ~5–15% per occurrence. The audience wants
convex, 25%+ upside. That is a *legitimate* need — and the honest instrument for it is
**option convexity** (and, secondarily, **commodity-futures leverage**), *not* a
prettier basket. So every view's ladder now spans a real return/risk spectrum:

| Tier | Instrument | Typical upside | Risk profile |
|---|---|---|---|
| **Conservative** | cash basket / ETF | ~5–15% | low prob of loss, no leverage |
| **Balanced** | pair / hedged / risk-parity basket | market-neutral-ish | spread/beta risk |
| **Aggressive (high-convexity)** | **defined-risk option spread / long option / MCX commodity future** | **25%+ *if the move happens*** | premium-at-risk / leveraged |

**Instruments for the high tier** (all from existing engines):
- **Options** — bull-call / bear-put **debit spreads** (defined risk), **long calls/puts**
  (convex), ratio / broken-wing — via `option_strategies.py` (real POP, greeks, payoff,
  max-loss, expected-move, margin, critique). Index weeklies (NIFTY/SENSEX) and liquid
  single-stock monthlies.
- **Commodity futures/options (MCX)** — leveraged directional on `CRUDEOIL/GOLD/SILVER/…`
  via `expressions/commodities.py` + `honest_short.py` (which already returns tradeable
  `commodity_future`/`commodity_put`). Register-not-execute; **leverage caveat on the card.**

**The honesty rails (NON-NEGOTIABLE — this is what makes high-upside legal & credible):**
1. **Never "promises 25%."** The card says *"can return ~X% **if** \<the move\> occurs"*
   and shows the **probability of that move** (from the underlying event-study, Part 4).
2. **Always show max loss + prob-of-total-loss.** Defined-risk = premium/spread-width;
   long options can lose **100% of premium**; the IV-crush warning is mandatory on any
   pre-event long-vol structure.
3. **Distribution, not a point.** The Aggressive card shows the **full modeled payoff
   distribution** (POP, breakevens, P05/median/P95), never a single hero number.
4. **Register-not-execute, defined-risk-first.** No naked shorts; Pivot arms, the user
   confirms in their broker.

**Data-honesty for options (fixes the shipped-view flaw).** There is no offline
historical option chain, so an option tier's Evidence Card is built from **two real,
labelled parts** — never a fabricated option return series:
- **(a) Directional evidence** = the *underlying's* event-study distribution (Part 4):
  *"how often, and by how much, did the underlying make the move this structure needs?"*
- **(b) Forward payoff** = the option's **modeled** POP / greeks / payoff / max-loss /
  expected-move from the **live chain** (`option_strategies.py`).
  Labelled *"modeled payoff × historical move-probability — not a historical option track
  record."* (This is exactly the fix for the current monsoon "bull call spread" card,
  which wrongly plotted the underlying's raw return as the option's return.)

**Where the high tier fires (routing).** Option Aggressive tiers are generated when the
underlying is **F&O-eligible and liquid** (`honest_short.is_weekly_eligible` / the F&O
list); commodity legs when the **driver is a commodity**. For non-F&O names the
Aggressive tier degrades to a leveraged/factor basket and **says so** (no fake option).

---

## Part 13 — Building new views: fresh pipeline, existing three untouched

**Hard constraint:** the three shipped curated views (monsoon / weak-IT / crude) —
their DB rows, `plain_copy.py` copy, and the `precompute.py` → `precomputed_views.json`
path — are **frozen. The new engine must not read, edit, or regenerate them.**

New views are produced by a **separate pipeline**: the multi-framework generator
(Parts 2–3) over the event-driven universe (Part 11) and the full payoff ladder
(Part 12), writing **new** `MarketView`/`ViewExpression` rows (and its own evidence
cache), leaving the curated three and their precompute path bit-for-bit unchanged. This
keeps the proven, hand-verified views stable while the fresh engine is built and tested
in isolation.
