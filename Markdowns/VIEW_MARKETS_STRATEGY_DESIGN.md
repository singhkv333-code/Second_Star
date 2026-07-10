# View Markets (V2) — Strategy Construction Design

*How Pivot constructs **proper, effective** strategies per view type — not "always a simple basket".*

**Scope contract.** Pivot is a chat-first copilot for **Indian retail** (NSE/BSE
equities, indices NIFTY/BANKNIFTY/SENSEX, NSE options/NFO). MCX commodities
(crude, gold, silver, metals, natgas) are **tradeable via register-not-execute**
(leveraged — keep the risk caveat); foreign legs are offered only as the listed
**Indian ETF proxy** (e.g. MON100 for US-tech). Pivot **registers/arms** structures with a bounded, stated max-loss; the
user confirms in their own broker (SEBI retail-algo posture). Paper trading is
simulated. We are **not** a broker and **not** a registered advisor — we ship data
and frameworks, never personalised advice, and never fabricate numbers (always
quote the card/tool values).

This doc is the implementation spec for the **Expression** stage of the
Belief → Expression → Deployment model, for the three view types: **EVENT**
(objective outcome + resolution date), **RELATIVE** (A beats B over horizon T),
**THEME** (long structural narrative). It is opinionated and reuse-first: the math
mostly already exists in the repo; the new work is glue, India-expressability
guards, cards, and a user-facing **Alignment Score**.

---

## 1. Principles

These nine rules govern every expression the engine emits.

1. **Effective ≠ basket.** An equal-weight basket collapses outcome-confidence and
   expression-confidence into one undifferentiated bet and silently ships three
   failures: exposure leakage/impurity, unmanaged concentration/liquidity, and no
   conviction gradient or timing. A basket is a *valid Conservative tier for a
   THEME*, never the default for EVENT or RELATIVE views.

2. **Tie every expression to the thesis.** P&L on an EVENT view is driven by
   **realized outcome minus what was already priced in**, not the outcome itself —
   a fully-expected "good" result pays nothing, and the long-vol buyer loses to IV
   crush even when directionally right. A RELATIVE view is a bet on a **spread**,
   not a direction. A THEME view has a **thesis-break (invalidation) exit**, not
   just a price stop. The Expression layer translates the *surprise/spread/
   narrative* into a payoff that is convex to the thesis and bounded on the wrong
   side.

3. **Two confidence dimensions drive the tier.** Pivot already models **outcome
   confidence** (will it resolve my way?) and **expression confidence** (given the
   outcome, will my instrument pay?). They map to the expression ladder:

   | Outcome | Expression | Expression class |
   |---|---|---|
   | Low | Low | Long-vol / non-directional (straddle, strangle, calendar) |
   | High | Low | Defined-risk directional **with a hedge** (debit/broken-wing) |
   | High | High | Aggressive directional (cash/ETF, ATM debit spread, ratio) |
   | Low | High | Pre-position small + Confirmation add (**Hybrid**) |

4. **Tiering is one pipeline, different knobs.** Conservative / Balanced /
   Aggressive are not three engines — they are knob settings (capital intensity,
   leverage, hedge ratio, # legs, purity floor, cap) on the same object. See §6 for
   the concrete per-kind definitions.

5. **Register-not-execute ⇒ defined-risk first.** The speculative leg is always a
   structure with a **stated max loss** (debit = premium; spread = width − credit).
   Pivot arms it; the user confirms. This is structurally compatible with the SEBI
   posture and is the reason naked shorts never appear.

6. **No retail delivery shorting — ever.** Indian retail cannot short single
   stocks or ETFs in delivery; only intraday (MIS, square-off same day),
   single-stock **futures** (~208 SSF-eligible names), **options**, or **SLB**
   borrow. **ETFs are effectively un-shortable** (no SLB depth) → "short NIFTYBEES"
   is *not* a real expression; the index short leg must be a NIFTY/BANKNIFTY
   **future or put**. The expression generator **enforces** this; it does not gloss
   it. When a name is neither F&O-eligible nor intraday-acceptable, the short leg
   **degrades to an AVOID/underweight annotation** and says so.

7. **India microstructure is hard-coded into every card.** As of 2026: **only
   Nifty 50 and Sensex have weeklies** (Bank Nifty is **monthly-only**); single-
   stock options are **monthly + physically settled** with **STT charged on
   intrinsic value at expiry** (force/flag pre-expiry square-off); single-stock
   options carry ~3 monthly expiries while **only Nifty runs out to 12 months**;
   most single-stock options beyond a handful of names are illiquid. MCX
   commodities (crude, gold, silver, metals, natgas) are **tradeable via
   register-not-execute** → commodity/oil views can go **direct (MCX
   futures/options)** *in addition to* routing through equities + gold/silver ETFs.

8. **Honest boundaries over fake success.** Never narrate "done/running" on a
   failure path. If a view cannot be expressed symmetrically (e.g. a beautiful
   cointegrated pair whose short leg isn't tradeable), the **Alignment Score must
   drop** and the card must say why. Kite is primary for data; never present
   yfinance/real-world dates as live when a Kite path exists.

9. **Every expression carries a score and a defended view.** Two numbers ship on
   the card: a **construction-time** alignment number (Event-Study Alignment /
   Relative-Value Alignment / Basket Purity) and a **historical** trust verdict
   from the existing Backtest Trust Battery, with the plain-English ladder
   `insufficient_data → no_edge → unproven → promising`. See §5/§7 of each section
   and the cross-cutting standard below.

---

## 2. EVENT views — a menu of strategy archetypes

An EVENT view has an objective outcome and a fixed resolution date. The backbone
principle: **frame realized-vs-priced, then pick a structure convex to the
surprise and bounded on the wrong side.** Timing is the second axis:

- **Pre-position** — enter *before* the print to catch the re-rating that happens
  *on* the announcement (rate direction, deal break). Pays most on a surprise;
  bleeds IV/theta if nothing happens. Cut size **30–50%** on the highest-
  uncertainty events; scale *up* for information-rich variants (RBI policy, Union
  Budget, FOMC dot-plot meetings), *down* for routine holds.
- **Confirmation** — enter *after* the print on the digested, second move. The
  literature is emphatic the first reaction overshoots and the secondary move is
  more sustained — the natural home of PEAD (enter day +2). Safer retail default.
- **Hybrid** — small convex defined-risk pre-position + scale-in on confirmation.

### Archetype menu

| # | Archetype | Thesis / when | Pivot primitive | Timing |
|---|---|---|---|---|
| E1 | **Rate-event defined-risk directional** — Bank Nifty/Nifty **bull-call debit spread** sized to the event move | High outcome + expression conf on rate direction | `option_strategies.py` (`bull_call_spread`, greeks/POP/margin/payoff) | Pre-position (cut size 30–50%) |
| E2 | **NBFC-vs-bank rate pair** (or PSU-vs-private) — isolates the *transmission asymmetry* (cuts help NBFC/borrowers > lender NIMs), strips index beta | High outcome, **low** expression conf | `backtest/pairs/` (Engle-Granger/OU) + `sector_universe.py` | Confirmation/Hybrid |
| E3 | **Event straddle / strangle** on the index — realized > priced | Low outcome conf, expect a big move either way | `option_strategies.py` straddle/strangle | Pre-position, close into IV-crush |
| E4 | **IV-crush harvest into earnings** — short straddle expressed as **iron fly / iron condor** (defined-risk only) or **calendar** (sell rich front, buy back-month) | You believe realized < priced; term-structure widest 1–5 days pre-print | `option_strategies.py` iron_condor/iron_butterfly/calendar | Pre-position |
| E5 | **PEAD drift** — rank on SUE + 3-day EAR, enter **day +2**, hold ~one quarter, long-tilted; or ride drift via a **cheap post-crush debit vertical** | Direction known *after* print | `propose_basket_allocation` (long), `option_strategies.py` (debit vertical), earnings event trigger | Confirmation |
| E6 | **Broken-wing butterfly** — directional lean at near-zero debit | Strong fundamental view, capped premium | `option_strategies.py` broken-wing | Pre-position |
| E7 | **Merger / open-offer arb (long-only)** — buy target ≤ offer, tender at offer price for cash (SEBI takeover/buyback retail quota); size by implied break probability from the spread | Deal completion view; ~20–25% annualized on a 3–4mo SEBI process | basket/allocation engine + event/schedule triggers; **needs** spread/break-prob calc (gap) | Pre-position, staged on CCI/NCLT/SEBI milestones (Hybrid) |
| E8 | **Index inclusion front-run** — long the announced **add** into the effective date (short the **delete** only where F&O-shortable) | Predictable passive flow; *caveat: index effect has decayed/crowded* | basket engine + event trigger | Pre-position (announce → effective) |
| E9 | **Budget/election thematic rotation** — `thematic_map` scenario ("Budget capex push", "decisive mandate") → **risk-weighted sector basket** + long-vol hedge into the event | Mandate/policy surprise; huge sector dispersion | `thematic_map.py` + `propose_basket_allocation` + index options | Confirmation (rotate into known winners) or Hybrid (exit-poll → result) |
| E10 | **Commodity/geopolitical-shock hedged basket** — long **gold ETF/SGB** + defensive basket (FMCG/pharma/utilities) via `risk_parity`/`min_variance`; **energy-vs-importer pair** (ONGC/refiners long vs aviation/OMC/paints); **Nifty put-spread or collar** as portfolio insurance | Unscheduled supply shock; India is a net energy-importer loser | weighting.py + pairs engine + option_strategies + **conditional pre-armed** indicator/VIX trigger | Confirmation-dominant with a **standing pre-armed hedge** |

### Rates events (E1–E3) — India specifics

RBI MPC (~6×/yr) and the Union Budget (Feb 1) are the high-information Indian
events; the FOMC is **out of scope as a tradable Indian instrument** — express a
Fed view through its Indian transmission (dovish Fed → rate-sensitive Nifty, IT as
a USD proxy) and offer the ETF proxy, never a US instrument. Transmission is uneven
and lagged: cuts flow faster to large rated NBFCs, slower to small ones; **banks'
NIMs compress** on a cut even as loan growth helps — so "RBI cuts → buy banks" is
the canonical **expression-confidence trap**, which is exactly why E2 (NBFC-vs-bank
pair) is the most professional rates expression. Bank Nifty is the most liquid
rates vehicle (F&O turnover >₹10 lakh cr/day) but **lost weeklies in 2026** — an
event-timed *weekly* straddle now uses **Nifty 50** or the **monthly** Bank Nifty.
Duration/steepener views route through **long-duration gilt ETFs / Bharat Bond /
target-maturity funds** (deliverable, register-friendly), **never** NSE 10Y G-sec
futures (institutional, retail-illiquid); a steepener is a long-duration-ETF vs
short-duration-ETF **pair**.

### Earnings events (E4–E6) — India specifics

Two canonical edges: **PEAD** (cumulative abnormal returns drift in the surprise
direction for weeks; enter day +2 to avoid look-ahead, hold ~60 trading days,
long-side carries most of the return — enables long-only) and **IV crush** (into
the print IV is bid far above realized and collapses within hours regardless of
direction, so the option *buyer* loses even when right). Per-stock straddles/
calendars are realistic **only on the most liquid F&O names** (RELIANCE, HDFCBANK,
INFY, TCS…); for everything else, **PEAD in cash/delivery** is the feasible
expression. Vol-sellers must be **defined-risk** (iron fly/condor) — never naked
short straddles in a register-not-execute product. The short side of a post-miss or
long-short PEAD defaults to **long puts / put debit spreads** (deliverable-safe if
closed pre-expiry), never a cash short. Flag the **STT-on-intrinsic + physical-
settlement** trap on every single-stock option card.

### Corporate actions (E7–E8) & Budget/election (E9) & shocks (E10)

Open offers and tender-route **buybacks** (reserved retail quota, high acceptance
ratios) are *the* retail-friendly Indian event arbs — clean buy-and-tender cash
longs. The spread width **is** the market's break-probability estimate; encode
tendering/proration risk, regulatory-delay risk, and deal-break risk. For
stock-swap deals the institutional long-target/short-acquirer leg is **infeasible
in India** (no cash delivery short) → offer only the long-target cash version and
say the acquirer-short is out of scope. Budget/election timing uses **Nifty
weeklies** (Bank Nifty monthly-only); prediction-market odds (Polymarket/Kalshi
adapters, read-only) supply a "what's priced in" read for election outcomes. For
shocks, MCX commodities are **tradeable via register-not-execute**, so a **direct
crude leg (MCX futures/options)** is now available *in addition to* routing oil
through equities + gold ETFs/SGBs; the "short importers" leg uses futures/puts on
F&O names or is dropped.

### EVENT standard — Event-Study Alignment Score

Every EVENT expression carries an **Event-Study Alignment Score**, not a generic
backtest:
1. **Event-study CAR/CAAR** — gather past instances of the same event tag (RBI MPC,
   Q-results, reshuffle, Budget), compute cumulative abnormal returns over [−t, +T]
   on the chosen instrument/basket, abnormal vs Nifty (use `compare_performance` +
   correlation betas).
2. **Surprise conditioning** — split instances by surprise sign/magnitude (cut vs
   hold vs hike; beat vs miss) so the score is conditional, not an unconditional
   average.
3. **Run through the Backtest Trust Battery** (Deflated/Probabilistic Sharpe,
   Minimum Track Record Length, MC block-bootstrap, walk-forward, no-skill
   permutation, trial counter) and surface the plain-English verdict. The **trial
   counter and MTRL must be loud** — an RBI decision happens ~6×/yr, so event
   strategies have very few independent observations and the small-sample warning
   is the honest headline.

---

## 3. RELATIVE views — pairs, sector-vs-index, ratio/RS, and the honest short

A RELATIVE view ("A beats B over T") is a bet on a **spread**. Pivot owns ~80% of
the machinery (`backtest/pairs/`, correlation matrix, `propose_basket_allocation` +
`weighting.py`, `sector_universe.py`, `option_strategies.py`, Trust Battery); V2 is
glue + the India expressability guard + a relative-value scoring standard.

### 3.1 Classic cointegrated pairs (the flagship non-basket expression)

The canonical recipe, already implemented in `backtest/pairs/cointegration.py`:

1. **Candidate screen** — correlation **only as a pre-filter** (price correlation
   is spurious; two stocks can trend together forever without mean-reverting), then
   test cointegration. `compare_performance` + correlation matrix → shortlist →
   `engle_granger`.
2. **Hedge ratio β** — OLS of log-price A on log-price B: `Spread = log A − β·log B`
   (`hedge_ratio(y, x)` → `(alpha, beta)`).
3. **Stationarity** — ADF on the residual, accept p<0.05/0.01 (`adf_tstat()` +
   `_verdict()`; `EngleGrangerResult.is_cointegrated` gates at 1%/5%). Multi-leg
   baskets use **Johansen** (`johansen()` / `run_johansen()`).
4. **OU half-life** — `half_life = −ln(2)/b` (`ou_half_life()`). **Use it as the
   tradeability gate vs horizon T**: half-life must be **< T** (and not absurdly
   short) or the trade can't revert in the user's window — the cleanest "does this
   fit T?" check; surface it directly.
5. **Z-score** — rolling `z = (spread − μ_L)/σ_L` (`rolling_zscore()`;
   `simulate_pairs` computes per-bar).

Bands (engine defaults, already in `run_pairs_backtest(entry_z=2.0, exit_z=0.5,
stop_z=4.0)`): enter |z|≥2 (short A/long B if z>+2, long A/short B if z<−2), exit
|z|≤0.5, **stop |z|≥4** (divergence circuit-breaker). The engine correctly **lags
the position to the next bar** (no look-ahead) and beta-hedges (`beta_t`,
`net_ret`). For the card, convert β + capital into **per-leg lot/share counts** and
report **residual market beta (≈0)**.

Risks to encode: **divergence** (hard z-stop + horizon stop at T), **regime/
cointegration breakdown** (static cointegration is insufficient — **rolling
re-estimation** of β + ADF re-check before each entry; everything correlates in
stress), **cost drag** (pairs ~double commissions; add STT, both-leg slippage,
futures roll / SLB borrow).

### 3.2 Sector rotation & sector-vs-index

Relative Strength `RS_t = SECTOR_ETF_t / NIFTY_t`, z-scored; cointegration-test the
sector-vs-index log spread with the **same** `engle_granger`/`ou_half_life`
machinery — a sector-rotation card is literally a pairs card with leg A = sector
basket (`sector_universe.py` + `propose_basket_allocation`), leg B = index.
Beta-adjust the index short to the sector's beta (`hedge_ratio(sector, nifty)`) so
the residual is sector alpha, not market drift. Risks: momentum whipsaw at regime
turns, crowding, thin-ETF tracking error.

### 3.3 Cross-sectional factor tilts (momentum / value / quality)

Textbook is long top-decile / short bottom-decile, rank-demeaned to be dollar-
neutral. The bottom-decile short is **un-executable for retail**, so three honest
expressions in descending fidelity:
1. **Smart-beta ETF long vs index-future short** (cleanest, fully tradeable) — e.g.
   long **NIFTY200 Momentum 30 / NIFTY100 Quality 30 / NIFTY50 Value 20 / Alpha
   Low-Vol 30 / Multi-Factor** ETF vs short **NIFTY future**. This is the realistic
   retail factor-tilt product.
2. **Long top-decile basket + AVOID list** (no short at all) — build the long with
   `propose_basket_allocation(factor)`; render the bottom-decile as an **AVOID/
   underweight annotation**, not a tradeable short. This is the brief's "express the
   underperform leg without shorting" answer: honest, register-able, SEBI-clean
   (but *not* market-neutral — say so).
3. **F&O-subset dollar-neutral** (advanced) — restrict both legs to the ~208 SSF
   names; short via single-stock futures; flag liquidity + lot minimums.
   Neutralise dollar / beta / **sector** (don't let "value" become "long PSU banks"
   — use `weighting.py` + `sector_universe.py` caps). Signals: momentum from Kite
   OHLCV (12-1), value/quality from the Moneycontrol fundamentals DB.

### 3.4 Ratio / relative-strength (the graceful degrade)

Trade the price ratio `R_t = A_t/B_t` (or `log A − log B`) z-scored directly —
looser than cointegration (no stable β / stationarity proof required). The right
tool when ADF **fails** but the user still has a relative view: **degrade from §3.1
to §3.4 and label the lower rigor** (reuse `rolling_zscore` on the ratio). It must
carry a **visibly lower Alignment Score** than a cointegrated pair so an RS trade is
never mistaken for a robust one.

### 3.5 Relative via options (the most India-legal expression)

Options express "A beats B" **without any short-stock leg** — defined max loss,
register-native:
- **Two-sided vertical pair** — long a **`bull_call_spread`** on A + a
  **`bear_put_spread`/`bear_call_spread`** on B (both NFO underlyings). Cleanest
  relative bull/bear; no SLB/SSF.
- **Call ratio spread** (1 long ITM, 2 short OTM) — "A grinds up modestly with
  falling IV"; benefits from IV drop + theta.
- **Synthetic short on B** — long put + short call (risk-reversal) replicates the
  underperform leg with options only.
  Report **net greeks across both underlyings** (the two option positions are not
  cross-delta-hedged).

### The honest short — decision rule (hard-coded in the expression generator)

```
single-stock short  → require SSF-eligible (or intraday MIS, or SLB-advanced),
                       else express via long put / put-spread, else AVOID-annotate
index short         → NIFTY/BANKNIFTY future or put/put-spread — NEVER ETF delivery short
no F&O on either leg → degrade to AVOID/underweight (§3.3 #2) and say so
```

### RELATIVE standard — Relative-Value Alignment Score (0–100)

Two layers. **Rigor** extends the Trust Battery with relative-value checks:
**Deflated Sharpe fed the pair-scanner's trial count** (the scan space is huge —
the family most exposed to overfitting), **PBO via CPCV/CSCV** (prioritise the open
P1.5 here), **OOS cointegration / spread-stationarity** (re-run ADF + re-fit β on
held-out windows), **market-neutrality** (regress strategy returns on NIFTY →
residual beta ≈0), **half-life < T**, and **cost-stress** (net of STT, both-leg
slippage, futures roll/SLB, option spread). **Alignment** (user-facing) blends:
statistical strength (cointegration confidence, |ADF t-stat|, hedge R²), stability
(rolling β + OOS persistence), horizon fit (half-life vs T), rigor (Deflated Sharpe
+ (1−PBO) net of costs), and the decisive **Expressability** dimension — full
credit if both legs trade cleanly, partial if the short degrades to AVOID,
explicitly flagged if not symmetrically expressible. **Tier the score ceiling by
construct rigor:** cointegrated pair > sector-vs-index cointegration > factor-ETF
tilt > ratio/RS > long-only-with-AVOID. A statistically beautiful pair retail can't
short **scores lower** — exactly the honesty the brief demands.

---

## 4. THEME views — conviction-weighted, factor-tilted, screened, hedged baskets

A THEME is a long-duration, low-time-resolution narrative ("India manufacturing
upcycle", "defence supercycle", "energy transition"). Build it as a **pipeline**,
not a flat basket: **Universe → Purity score → Liquidity screen → Conviction
weight → Cap → (optional) Factor tilt → Deployment → (optional) Hedge/Multi-asset →
Score.**

### 4.1 Purity / exposure scoring (the single biggest upgrade over flat)

The thematic-ETF industry competes on **purity, not breadth** (a low-purity
"fintech" fund holding Nestlé/Cisco/IBM is mega-cap beta with a thematic fee).
Introduce a **Theme Purity Score (0–100)** per candidate as a **layered, disclosed
approximation** (Pivot has no revenue-forecast feed):
1. **Curated tag** (highest confidence) — a `thematic_map.py`/`sector_universe.py`
   "winner" = pure-play ~80–100.
2. **Fundamentals-DB segment match** — Moneycontrol segment revenue → MSCI-style
   bands: **≥50% pure-play / 25–50% core / 10–25% peripheral / <10% excluded**
   (yfinance fallback for sector tags).
3. **LLM-judged relevance %** with a one-line rationale, clamped and flagged
   "estimated".
   Headline a **Basket Purity number** = purity-weighted average of constituents,
   shown next to the Trust verdict. This is the user's "score for reference" surfaced
   at *construction* time.

### 4.2 Liquidity screen (India realism — non-negotiable, before weighting)

Thematic names have worse liquidity than the index. Screen first: **ADV/turnover
floor** (rolling 20-day median traded value above ~₹5–10 cr/day from Kite volume;
below → hard weight cap or "watch" leg), **free-float bias** (`weighting.py` `mcap`
should use free-float, like NSE thematic indices, to avoid trapping low-float
names), **impact-cost surfaced** for the user's order size (honest, feeds register-
not-execute), and an **options-availability flag** per name (most single-stock
options are illiquid; only ~3 monthly expiries; only Nifty runs to 12 months) —
needed for the §4.5 hedge.

### 4.3 Conviction weighting + cap + factor tilt (the replacement for equal weight)

**3-tier conviction sizing** mapped onto the purity score: pure-play (≥50% /
curated) = high tier, core (25–50%) = medium, peripheral (10–25%) = foundation.
**Blend schemes from `weighting.py`** by tier (see §6 table): Conservative →
`risk_parity`/`min_variance` (equal *risk*, not equal capital — stops one volatile
small-cap dominating drawdown); Balanced → **purity-scaled free-float `mcap` +
conviction multipliers**; Aggressive → `factor` (momentum+quality composite) or
`black_litterman` with the **theme as the active view** and market-cap as the prior
(BL is literally "blend a conviction view with a neutral prior" → perfect for
Belief→Expression). Even within a scheme, **multiply each raw weight by purity and
renormalise** — the cheapest defensible upgrade over equal weight.

**Capping** — add **`single_name_cap`** to `propose_basket_allocation` (default 20%
Aggressive / 15% Balanced / 10% Conservative) with **iterative redistribution** of
excess (standard capped free-float algorithm), plus a **min-names floor** (e.g. 10);
if the "theme" is really 3 stocks, the engine **refuses** and offers the **ETF
proxy** as the Conservative tier (e.g. Motilal Oswal Nifty India Defence ETF, Mirae
Nifty India Manufacturing ETF). **Factor tilt within the theme** (theme defines the
universe, factor picks which names): **multi-factor (value+momentum+quality) beats
single-factor**; `weighting.py` `factor` should support a **composite** tilt and the
card should show the realised tilt (e.g. portfolio momentum z-score).

### 4.4 Deployment — staged entry, rebalance, trim (the layer a flat basket ignores)

This is where Pre-position/Confirmation/Hybrid live, and **all triggers already
exist** in the workflow engine (`schedule`/`price`/`indicator`/`event`/
`scheduled_macro`):
- **Pyramiding / staged entry** — decreasing-size tranches (canonical **50/30/20**),
  each add smaller than the last so a late reversal can't turn a winner into a
  loser; objective add-triggers (MA cross, breakout-with-volume, higher-high) =
  `indicator`/`price` triggers; trail the **aggregate** stop up as you add. First
  tranche tiny (1–2%) until the theme confirms. **Each tranche is a
  `register_workflow` with a trigger** — nothing new in the execution layer, and
  natively register-not-execute.
  - *Pre-position (Aggressive)* — larger first tranche now, belief-led.
  - *Confirmation (Conservative)* — 0% until a trigger fires (e.g. theme ETF >
    200-DMA), then tranche in.
  - *Hybrid (Balanced)* — small starter now + armed 50/30/20 ladder.
- **Rebalance cadence** — Conservative **semi-annual** calendar (matches Indian
  thematic-index reconstitution → basket tracks the investable ETF), Balanced
  **quarterly + drift bands**, Aggressive **drift-band only, wide, momentum
  refresh** (let winners run). Don't over-time rebalances at low conviction.
- **Trim / exit** — scaled exits at predefined targets (take 20–30% off after the
  third scale-in / on overextension, keep a runner); the drift band **is** an
  automatic trim; and critically the **invalidation exit** — wire
  `thematic_map.py`'s per-scenario **invalidate** conditions as the *thesis-break*
  exit (policy reversal, order-book collapse) via `event`/`scheduled_macro`,
  distinct from a price stop. A flat basket has no thesis to break — this is the
  structural differentiator.

### 4.5 Optionized / hedged overlay

Convert the directional theme into a **shaped payoff** with `option_strategies.py`
(critique/POP/expected-move/margin run on the chosen leg so the hedge is a
defended, sized object). **India gate: hedge at the index level with Nifty (or Bank
Nifty), not name-by-name** — single-stock options are thin/short-dated; Nifty is
extremely liquid with 12-month tenor.

| Leg | Tier | Why ≠ flat | India instrument |
|---|---|---|---|
| **Protective put** | Conservative | Floors drawdown while thesis proves | Nifty index put (12-mo OTM) |
| **Covered call** | Balanced | Pays you to wait; outperforms flat/down markets (*income, NOT a hedge*) | Nifty / liquid single-name calls |
| **Zero-cost collar** | Conservative–Balanced | Floor **and** finance, ~free — the headline India-realistic hedge | Nifty options (collar template) |
| **Long call / call spread** | Aggressive | Convex, capital-light leverage, defined premium risk | Nifty / liquid single names |

### 4.6 Multi-asset themes (equity + gold + hedge)

Some views are cross-asset. **Gold is the canonical India-available diversifier** —
a **2–10% sleeve** historically improved Sharpe and lowered max drawdown, rises in
equity drawdowns; with the **bond–equity correlation positive post-2022**, gold
carries more of the diversification load for Indian retail (bonds are a weak retail
instrument) → argue toward the higher end. Instruments: **Gold ETF / Gold BeES,
Silver ETF** (Kite-tradeable; SGBs/physical out of the chat-execution loop — offer
the listed ETF; **MCX gold/silver futures/options also tradeable via
register-not-execute**). Size with `weighting.py` `risk_parity`/`min_variance` at
the **asset-class** level so the equity sleeve doesn't swamp gold's risk
contribution — a multi-asset theme is just a 2–3 node `propose_basket_allocation`
(theme-equity basket + gold ETF + optional Nifty-put/collar). Tiers: Conservative
8–10% gold + collar/put + risk-parity sleeves; Balanced 5% gold + covered-call-
financed put; Aggressive 2–3% gold pure tail-hedge + factor-tilted/optionized
equity.

### THEME standard — Basket Purity + Trust verdict

Two scores on the card: (1) **Basket Purity Score** (construction-time: "how much
of this is actually the theme"); (2) **Trust verdict + headline stat** (Deflated/
Probabilistic Sharpe + MinTRL: "has anything like this held up, and is the sample
even big enough"). Run the constructed basket through the **Trust Battery** but be
honest that themes have **few independent regimes** → MTRL/`unproven` will fire
often; the **trial counter / Deflated Sharpe penalise** the many construction knobs
(purity floor, cap, tilt, tranche schedule). **Benchmark against the real ETF** (not
just cash) — alignment to an investable benchmark is itself a trust signal and
exposes "this basket just replicates the ETF, buy the ETF" honestly. **Permutation
test the *tilt*** — if conviction-weighting/factor-tilt adds nothing over equal-
weight-in-universe, the engine should *say the flat basket is fine* (intellectual
honesty over false sophistication). **Walk-forward over regimes** and show the
drawdown years, not just the up-cycle.

---

## 5. Conservative / Balanced / Aggressive — concrete per-kind

The tier is the same pipeline with different knobs. Concrete contract:

### EVENT

| Dimension | Conservative | Balanced | Aggressive |
|---|---|---|---|
| Structure | Defined-risk debit spread or risk-weighted basket | Relative pair (NBFC-vs-bank) or vol-sell iron fly/condor | Outright ATM debit spread / long straddle / ratio |
| Capital intensity | Premium-capped (small) | Pair = ~2× gross, dollar-neutral | Larger first tranche |
| Leverage | None (cash/defined-debit) | Defined (spread width) | Defined but ATM/larger size |
| Hedge ratio | Full (defined-risk by construction) | Beta-neutral pair | Partial — capped by event tail |
| # legs | 2 (vertical) | 2–4 (pair / iron fly) | 1–4 |
| Timing | Confirmation | Hybrid | Pre-position (cut size 30–50%) |

### RELATIVE

| Dimension | Conservative | Balanced | Aggressive |
|---|---|---|---|
| Structure | Smart-beta ETF vs index future/put; or relative defined-risk option spreads | Cointegrated SSF pair; sector-vs-index | F&O-subset dollar-neutral multi-name |
| Capital intensity | ETF long + 1 index future (cheapest) | ~2× gross (two legs) | Heavy (many SSF lots) |
| Leverage | Premium-only (option version) | Beta-hedged 1:1 | Multi-lot futures |
| Hedge ratio | Defined-risk / β-adjusted index | β-neutral (residual ≈0) | β + sector-neutral |
| # legs | 2 (ETF + future) or 4 (option pair) | 2 | many |
| Short leg | Index future/put or AVOID-annotate | SSF | SSF |

### THEME

| Stage | Conservative | Balanced | Aggressive |
|---|---|---|---|
| Universe/purity | ETF proxy or pure-play (≥50%) | core+ (≥25%) | full incl. peripheral (≥10%) |
| Weighting | risk_parity / min_variance | purity-scaled free-float + conviction tiers | factor (momentum+quality) / black_litterman |
| Single-name cap | 10% | 15% | 20% |
| Entry timing | Confirmation (trigger-gated) | Hybrid (starter + 50/30/20) | Pre-position (large first tranche) |
| Rebalance | semi-annual, tight bands | quarterly + drift bands | drift-band, wide, momentum refresh |
| Hedge | zero-cost collar / protective put (Nifty) | covered-call-financed put | long call spread (convexity) |
| Multi-asset | 8–10% gold, risk-parity sleeves | 5% gold | 2–3% gold tail-hedge only |

---

## 6. Mapping table — archetype → required primitive → EXISTS / GAP

| Archetype | Required Pivot primitive | Status |
|---|---|---|
| Defined-risk verticals, straddle/strangle, iron fly/condor, calendar, broken-wing, collar, covered call, ratio | `option_strategies.py` (15+ templates, greeks/payoff/POP/margin/critique) | **EXISTS** |
| Cointegrated pair (EG/Johansen/OU/ADF/β/z + bands, no look-ahead, beta-hedge) | `backtest/pairs/{cointegration,engine,scanner}.py` | **EXISTS** |
| Candidate screen / relative performance lines | correlation matrix + `compare_performance` | **EXISTS** |
| Risk-weighted / factor / conviction baskets | `propose_basket_allocation` + `weighting.py` (equal/mcap/risk_parity/min_variance/black_litterman/factor) + `sector_universe.py` | **EXISTS** |
| Scenario winners/losers/confirm/**invalidate** | `thematic_map.py` (6 macro scenarios) | **EXISTS** — extend with "RBI easing", "Budget capex", "oil/geopolitical shock", "decisive mandate" |
| Arming: tranche ladder, rebalance, trim, invalidation, event timing | workflow engine triggers (schedule/price/indicator/event/scheduled_macro/polymarket/kalshi) + `register_workflow` | **EXISTS** |
| "What's priced in" odds (read-only) | Polymarket + Kalshi adapters | **EXISTS** |
| Testing rigor (Deflated/Probabilistic Sharpe, MinTRL, MC bootstrap, walk-forward, permutation, trial counter, verdict ladder) | Backtest Trust Battery | **EXISTS** |
| **CPCV / PBO** OOS method (relative-value scans the largest hypothesis space) | extend Trust Battery | **GAP** (open P1.5) — prioritise |
| Two-leg **register card** with India-legal instrument per leg + SSF-eligibility gate + residual-beta + per-leg lot sizing | new card/glue over pairs engine | **GAP** |
| **AVOID / underweight** rendered as a first-class expression type | new expression type | **GAP** |
| **Combined two-underlying "relative options" card** with aggregated payoff/greeks; cross-underlying critique | extend `option_strategies.py` (`critique_strategy` is single-underlying) | **GAP** |
| Cross-sectional **decile/rank engine** + **factor→smart-beta-ETF** catalog | new `cross_sectional_rank` service + ETF map | **GAP** |
| **`single_name_cap`** (iterative redistribution) + **min-names floor** | extend `propose_basket_allocation` | **GAP** |
| **Theme Purity Score** (curated → fundamentals-DB segment → LLM relevance) + Basket Purity headline | new thin layer over existing data | **GAP** |
| **Liquidity screen** (ADV floor, free-float mcap, impact cost, options-availability flag) | new screen over Kite volume | **GAP** (free-float into `weighting.py` `mcap`) |
| **Event / calendar + surprise feeds** — RBI/FOMC dates + OIS-implied/consensus; earnings calendar + consensus (SUE); corporate-actions/deal feed (open-offer price, record/effective dates, reshuffle, buyback acceptance); political-event calendar; oil + India-VIX shock signals | new data feeds | **GAP** — *dominant gap* (structures exist; the surprise inputs that make them event-driven do not). Without consensus, default to **EAR-only PEAD** (computable from Kite OHLCV + Nifty) |
| **Priced-in / implied-move calculator** (ATM-straddle expected move) | small new calc over the option chain | **GAP** — high-leverage |
| **Event-Study CAR/CAAR** module (past instances, conditioned on surprise → Alignment Score) | new module reusing `compare_performance` + betas + battery | **GAP** |
| **Merger spread / implied-break-probability** calculator (spread, days-to-close, annualized, breakeven break-prob, proration) | small new calc | **GAP** — retail open-offer/buyback sweet spot |
| **Universe tags** — duration-ETF set, gold/silver ETF/SGB set, energy importer-vs-exporter map, SSF-eligibility list | new static tag tables | **GAP** |
| **Alignment Score** objects (Event-Study / Relative-Value w/ **Expressability** dimension / Basket Purity), ceiling-tiered by construct rigor | new scoring surface over the battery | **GAP** |

**Build priority (highest leverage first):** (1) event/calendar + surprise feeds;
(2) priced-in/implied-move calculator; (3) Event-Study CAR module + the three
Alignment Score objects; (4) CPCV/PBO; (5) two-leg register card + SSF gate +
AVOID expression type; (6) merger spread/break-prob calc; (7) purity + liquidity
screen + `single_name_cap`; (8) cross-sectional rank engine + factor→ETF map +
combined relative-options card; (9) universe tag tables.

**Through-line:** the math is largely built. What turns these into *proper,
effective, honest* strategies — and keeps them off the "always a basket" path — is
the surprise/spread/purity **inputs**, the India **expressability guards**, the
**deployment** assembly of existing triggers, and the user-facing **Alignment
Score** tiered by construct rigor.

---

### Sources

EVENT: NYC Servers (rate decisions); OptionsTrading.org & BSIC (earnings
straddles/calendars); Sahi, 5paisa, IIFL, Zerodha Varsity (India expiry/settlement,
shorting, physical settlement); DSIJ & Angel One (RBI cuts → NBFC/SMID/auto);
Bajaj Finserv (duration); Pro Trader Dashboard, Wikipedia, Quantpedia (PEAD/SUE/EAR);
OptionAlpha (FOMC sizing); InsideArbitrage, Dhruva, Wikipedia (merger arb / QSR);
QuantPedia, HBS, Eastspring (index rebalancing); MarketCalls, Choice (election/Budget
straddles); MSCI, CEPR (geopolitical oil playbook).
RELATIVE: Sesen, Amberdata, QuantInsti, GGR/Wharton, Springer, arXiv 2412.12458,
Harbourfronts (pairs/cointegration/OU/regime); IIFL, 5paisa, Zerodha Varsity,
Advocate Gandhi (India shorting / ETF-short); NSE & Dhan (SSF universe); NSE
Multi-Factor whitepaper, HDFC smart-beta, AQR Val&Mom Everywhere, S&P, Alpha
Architect (factors); Quantpedia & StockCharts/Faber (sector momentum); Dhan/NSE
(sector ETFs); Bailey–López de Prado (Deflated Sharpe), Bailey–Borwein (PBO),
ScienceDirect (CPCV); Fidelity & OptionAlpha (ratio spreads).
THEME: ETF Stream & Tema (purity); ETF Trends (weighting); NSE Indices & Value
Research (Nifty India Defence methodology/ETF); Mirae, TradingView (manufacturing
ETF); MSCI (relevance score); CAIA (risk parity); BlackRock, Global X (conviction
tiers/multi-theme); PL Capital (Nifty options liquidity/expiries); Chase, LSEG/FTSE
Russell (factor tilts); QuantStrategy.io, LuxAlgo, HeyGoTrade, TradersPost
(pyramiding); Russell, Wellington (rebalancing); Choice, Madison, Calamos,
ProShares, Religare (option hedges/collar/covered call); State Street, LSEG, World
Gold Council (gold multi-asset).
