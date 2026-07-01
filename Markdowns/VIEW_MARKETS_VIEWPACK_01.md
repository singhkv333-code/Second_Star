# View Markets — View Pack 01 (fresh, curated)

> The first pack from the **new** multi-framework engine (spec:
> `VIEW_MARKETS_STRATEGY_ENGINE.md`). **The three existing views
> (monsoon / weak-IT / crude) are frozen and untouched** — this is a clean set.
>
> Built on: event-driven **≥150–200** universe (NIFTY-500 sector members +
> commodities + F&O options), the **payoff ladder** (Conservative basket →
> Balanced pair/hedge → **Aggressive derivatives** for 25%+), and the
> **average-return-per-window** convention. Register-not-execute, defined-risk-first,
> never-fabricate. Every card ends "analysis, not advice."

---

## Methodology (embeds everything we agreed)

- **Return shown = average return *per window*, never compounded.** A view's
  expression is deployed **once per occurrence / window**, so the honest headline is
  the **mean forward-window return** (event-study CAAR style), not the equity curve of
  all episodes stacked. (Same principle already in the frozen views' `precompute.py`.)
- **Distribution, not a point.** Each expression shows avg, median, **hit-rate vs
  Nifty**, **worst window**, best, and **N** — a forecast of the past, not a promise.
- **Three tiers = a return/risk spectrum**, and every view carries a **derivatives
  tier** for the 25%+ upside people actually want:
  - **Conservative** — equity basket / ETF (~5–15%, low loss-prob).
  - **Balanced** — pair / hedge / relative (market-neutral-ish).
  - **Aggressive (derivatives)** — option spread / long option / **MCX future** →
    **25%+ *if the move happens*, shown with POP + max-loss + IV-crush**, never promised.
- **Minimum capital** stated per tier (basket floor = ₹ to hold 1 share of each member;
  option = 1-lot premium; MCX = SPAN+exposure margin — leveraged).
- **Confidence tier** per view: **T1** statistically proven · **T2** moderately
  supported (history + economics) · **T3** narrative (economics + transmission; score capped).

### Data provenance (what's real vs priced-at-deploy)
- **Equity per-window returns & hit-rates:** **REAL**, computed **2026-06-30** from the
  cached NIFTY-500 daily matrix (`v3/universe`). EW basket, forward-H-day windows stepped
  monthly. `N` = non-overlapping window count (honest sample; the stepped windows overlap,
  so treat `N` as the real sample size). Measured over available history since the youngest
  constituent — young names (Paytm/Kalyan/Sona '21+) shorten their basket's history.
- **Min-capital floors:** **REAL** (Σ latest close, 1 share each), as-of 2026-06-30.
- **Option / MCX payoffs & POP:** there is **no offline option chain** → these are
  **priced AT DEPLOY** from the live chain (`option_strategies.py`); figures here are
  **indicative structure only, never a backtested option return** (the fix for the frozen
  monsoon card's flaw). The option card's *evidence* = the **underlying's** historical
  move-probability × the option's **modeled** payoff.
- **Event-conditional returns** (e.g. Middle East): the unconditional basket number is
  shown here; the **conditional-on-trigger** CAAR is computed by the engine when armed.

---

## 1. AI capex — *"more jobs than it destroys" → the tradeable version*

- **Original:** *Will AI create more jobs than it destroys by 2030?* (not measurable/India-tradeable, far)
- **Reframed (6-month, RELATIVE/THEME):** **"Over the next 6 months, will India's
  AI-capex beneficiaries — IT services + data-centre/power — beat the Nifty?"**
- **Why compelling:** the AI story everyone argues about, turned into a dated,
  benchmarked, tradeable bet. **Tier T2/T3.**
- **Transmission → universe:** AI capex → cloud/ER&D demand (IT, 27 names) + compute
  power draw (Power, 17) → ~**150** with adjacencies. US-AI leg → **MON100** proxy (never a foreign line).

| Tier | Expression | Evidence (avg **per window**) | Min capital |
|---|---|---|---|
| Conservative | EW AI/IT-capex basket (TCS, INFY, HCLTECH, PERSISTENT, COFORGE, WIPRO, TECHM) | **+11.7% / 6mo · median +9.1% · beat-Nifty 65% · worst −27.5% · N≈31** (excess **+6.0%**) | **₹11,503** |
| Balanced | Long AI-basket / short NIFTY future (isolates the theme) | market-neutral; ~excess above | ~₹1.2–1.5 L (1 lot hedge) |
| **Aggressive (derivatives)** | **Long calls / bull-call spreads on INFY·TCS·PERSISTENT or Nifty IT** | **25%+ if IT re-rates**; POP/max-loss/expected-move **at deploy** | ~1-lot premium (indicative ₹10–25k) |
| Contrarian | See View 2 (AI *disrupts* incumbents) | — | — |

---

## 2. AI vs incumbent IT — *"replace search" → the India pair*

- **Original:** *Will AI replace traditional search engines?* (US names, not tradeable here)
- **Reframed (6-month, RELATIVE pair):** **"Over 6 months, will India's AI-adopter /
  product IT (Persistent, Coforge, LTIM) beat the incumbent large-cap IT-services
  (TCS, Infosys)?"**
- **Why compelling:** the sharpest AI debate that *is* tradeable in India — disruption
  **within** IT, expressed as a clean spread. **Tier T2.**

| Tier | Expression | Evidence (avg **per window**) | Min capital |
|---|---|---|---|
| Conservative | Long adopters basket (PERSISTENT, COFORGE) — long-leg only | **+18.1% / 6mo · median +12.2% · beat-Nifty 64% · worst −28.8%** (excess **+12.4%**) | ~₹6–7k (1 share each) |
| **Balanced (flagship)** | **Pair: long adopters / short incumbents** via single-stock futures | market-neutral spread; edge = adopter−incumbent | ~₹1.5–2 L (2 SSF legs) |
| Aggressive (derivatives) | Bull-call spread on adopters + bear-put spread on incumbents | **25%+ on the spread widening**; priced at deploy | ~2-lot premia (indicative ₹15–35k) |

> Honest: long-leg-only stat shown; the **pair** neutralises IT-sector beta — its true
> edge is the *relative* move, computed on arming.

---

## 3. Power / nuclear-adjacent capex — *"nuclear comeback" → the listed proxy*

- **Original:** *Will nuclear energy make a major comeback before 2040?* (far; NPCIL unlisted)
- **Reframed (6–12-month, THEME):** **"Over 6–12 months, will India's power-capex /
  base-load basket (incl. nuclear-adjacent engineering) beat the Nifty?"**
- **Why compelling:** rides India's real power-capex supercycle; nuclear pure-plays are
  unlisted, so we express it via the **listed power-capex proxy — and say so.** **Tier T3.**

| Tier | Expression | Evidence (avg **per window**) | Min capital |
|---|---|---|---|
| Conservative | EW Power-capex basket (NTPC, TATAPOWER, NHPC, POWERGRID, BHEL, LT, SIEMENS, ABB, THERMAX) | **+8.7% / 6mo · median +5.4% · beat-Nifty 51% · worst −32.1%** (excess **+3.0%**) | **₹21,498** |
| Balanced | Long power-capex / short Nifty | thin edge (see flag) | ~₹1.3 L |
| Aggressive (derivatives) | Long calls / spreads on NTPC·POWERGRID·LT | **25%+ if the theme runs**; at deploy | ~1-lot premium |

> **Honesty flag: weak edge.** 51% hit-rate / +3% excess = barely better than the
> Nifty — this is a **T3 narrative**, not a proven edge. The card must say so and the
> Evidence Score is capped. Don't oversell it.

---

## 4. EV ecosystem — *"majority of car sales" → the 6-month beat*

- **Original:** *Will EVs become the majority of new car sales before 2035?* (far)
- **Reframed (6-month, RELATIVE/THEME):** **"Over 6 months, will India's EV-ecosystem
  basket beat the Nifty (and legacy ICE)?"**
- **Why compelling:** strong, dated, and it *works* historically. **Tier T2.**
- **Universe:** Auto (38), EV-tilted (OEMs + batteries + EV components) + charging/power adjacency.

| Tier | Expression | Evidence (avg **per window**) | Min capital |
|---|---|---|---|
| Conservative | EW EV basket (M&M, TVSMOTOR, EXIDEIND, BOSCHLTD, SONACOMS, UNOMINDA, MOTHERSON) | **+14.5% / 6mo · median +12.3% · beat-Nifty 74% · worst −26.3%** (excess **+8.8%**) | **₹48,838** (Bosch-heavy; drop BOSCHLTD → ~₹9k) |
| Balanced | Long EV-ecosystem / short legacy-ICE (pair) or / short Nifty | isolates EV vs the rest of auto | ~₹1.3–1.5 L |
| **Aggressive (derivatives)** | **Long calls / bull-call spreads on M&M / TATAMOTORS** | **25%+ on an EV-news re-rate**; at deploy | ~1-lot premium |

> Strongest equity edge in the pack (**74% hit**). Note the ₹48.8k floor is driven by
> Bosch's ₹40k share — a Bosch-free basket floors near **₹9k** (state both).

---

## 5. Middle-East escalation — *the geopolitical / commodity showcase*

- **Kept (this quarter, EVENT):** **"Will Middle-East tensions escalate further this
  quarter — and if so, does the oil/defence/gold complex pay?"**
- **Why compelling:** highest emotional pull; the cleanest **commodity-futures** showcase.
  **Tier T2**, **asymmetric** (escalation has strong expressions; de-escalation has no clean trade → say so).
- **Two dials:** *outcome* (will it escalate? **low** — few analogs) vs *expression*
  (if it does, oil/defence/gold pay — **higher**). Never collapse them.
- **Universe:** upstream/OMC (Oil&Gas 17: ONGC, OIL, GAIL, BPCL) + defence (HAL, BEL) +
  **losers** aviation (INDIGO) & paints (ASIANPAINT) + **MCX CRUDEOIL / GOLD**.

| Tier | Expression | Evidence (avg **per window**) | Min capital |
|---|---|---|---|
| Conservative | EW oil-up/defence basket (ONGC, OIL, HAL, BEL, GAIL, BPCL) | **+5.1% / 3mo (unconditional) · beat-Nifty 55% · worst −34%** — *conditional-on-escalation is larger, computed on arming* | **₹5,938** |
| Balanced | Long upstream / short OMC or aviation (pair); Nifty-put hedge overlay | isolates the oil-beneficiary spread | ~₹1.3 L |
| **Aggressive (derivatives)** | **MCX CRUDEOIL call spread + Nifty put hedge + long Gold** | **25%+ if crude spikes**; leveraged, register-not-execute, IV/margin caveat | SPAN+exposure margin (indicative ₹40–80k / lot) |
| Contrarian | Short airlines / long gold / long India-VIX (if consensus already long defence) | 2nd-order; pays when the crowded trade doesn't | option premium |

> Asymmetric: **YES** has strong expressions; **NO (no escalation) has no clean inverse**
> — the engine says "no trade," a feature not a gap.

---

## 6. Nifty 30,000 by year-end — *the pure options showcase*

- **Kept (PRICE-TARGET):** **"Will Nifty touch 30,000 before year-end?"**
- **Why compelling:** the headline number everyone watches; the **cleanest 25%+ options** demo. **Tier T1-ish (base-rate).**
- **Evidence:** base-rate of the required % move over the remaining horizon **×** the
  option's POP/expected-move — **priced at deploy** (no fabricated number).

| Tier | Expression | Evidence | Min capital |
|---|---|---|---|
| Conservative | Long NIFTYBEES + staged adds on dips | index drift; low convexity | ~₹2–5k (few units) |
| **Aggressive (derivatives, hero)** | **NIFTY bull-call spread** (defined risk) | **25–60% on the spread if it hits**; POP + max-loss (= net debit) at deploy | **1 NIFTY lot premium** (indicative ₹10–20k) |
| Aggressive+ | Long NIFTY calls (convex, cheap entry) | higher upside, **can lose 100% of premium** — mandatory warning | 1-lot premium |

> The card leads with the **defined-risk spread** (capped loss), offers the long call as
> the higher-octane option **with the total-loss warning** — high upside, honest odds.

---

## 7. Gold new all-time high — *the commodity + India-favourite*

- **Kept (this quarter, PRICE-TARGET / commodity):** **"Will gold make a new all-time
  high this quarter?"**
- **Why compelling:** the most-Indian asset; tradeable as **equity proxy AND commodity**. **Tier T2.**
- **Universe:** jewellers (TITAN, KALYANKJIL) + gold-loan NBFC (MUTHOOTFIN, MANAPPURAM) +
  **Gold/Silver ETF** + **MCX GOLD / SILVER futures & options**.

| Tier | Expression | Evidence (avg **per window**) | Min capital |
|---|---|---|---|
| Conservative | Gold-equity basket (TITAN, KALYANKJIL, MUTHOOTFIN, MANAPPURAM) | **+8.7% / 3mo · median +8.5% · beat-Nifty 67% · worst −42.6%** (excess **+5.9%**) | **₹8,133** |
| Cross-asset | Long **Gold BeES / Silver BeES** (direct, low-vol) | tracks bullion; the calm expression | ~₹1–5k / unit |
| **Aggressive (derivatives)** | **MCX GOLD call spread** or options on Muthoot/Manappuram | **25%+ if a new high prints**; leveraged, at deploy | SPAN+exposure margin |

---

## 8. Digital payments / fintech — *"UPI record" → the tradeable proxy*

- **Original:** *Will UPI transactions hit another record this year?* (near-certain → no surprise, no clean trade)
- **Reframed (6-month, THEME):** **"Over 6 months, will India's digital-payments /
  fintech beneficiaries beat the Nifty?"**
- **Why compelling:** rides the digital-India narrative — but honestly the **weakest edge
  in the pack.** **Tier T3.**
- **Universe:** listed fintech/market-infra (PAYTM, POLICYBZR, BSE, CDSL, ANGELONE, CAMS, KFINTECH, AFFLE).

| Tier | Expression | Evidence (avg **per window**) | Min capital |
|---|---|---|---|
| Conservative | EW fintech basket | **+9.9% / 6mo but median 0.0% · beat-Nifty 49% · best +122%** — hugely dispersed | **₹11,416** |
| Aggressive (derivatives) | Options on liquid names (PAYTM, BSE, POLICYBZR) | high-variance; convex both ways | ~1-lot premium |

> **Honesty flag: low confidence.** Median 0%, 49% hit, return driven by a few outliers
> (young, volatile names, short history). Ship it **only** with a loud "unproven / high
> dispersion" badge — or hold it back. This is the honest counterexample to the pack.

---

## Summary & ranking (by real per-window edge)

| # | View (reframed) | Hero avg/window | Beat-Nifty | Tier | Derivatives tier |
|---|---|---|---|---|---|
| 4 | EV ecosystem (6mo) | **+14.5%** | **74%** | T2 | M&M/Tata Motors options |
| 2 | AI adopters vs incumbents (6mo) | +18.1%* | 64% | T2 | relative option spreads |
| 1 | AI capex (6mo) | +11.7% | 65% | T2/T3 | IT calls / Nifty-IT spread |
| 7 | Gold new high (3mo) | +8.7% | 67% | T2 | **MCX gold spread** |
| 3 | Power/nuclear capex (6mo) | +8.7% | 51% ⚠ | T3 | power-name options |
| 5 | Middle-East escalation (Q) | +5.1%† | 55% | T2 | **MCX crude spread + put hedge** |
| 8 | Fintech/UPI (6mo) | +9.9% | 49% ⚠ | T3 | fintech options |
| 6 | Nifty 30k (year-end) | *base-rate* | — | T1 | **NIFTY call spread (hero)** |

\* long-leg-only (pair edge is the relative move). † unconditional; conditional-on-escalation larger.
⚠ weak edge — ship with an honest low-confidence badge or hold.

**Ship-first recommendation:** **EV (4), AI-pair (2), Gold (7), Nifty-30k (6), Middle-East
(5)** — strongest edges + the best derivatives/commodity showcases. **Power (3) and
Fintech (8)** ship only with prominent low-confidence badges, or wait for better evidence.

---

## What's real here vs computed-at-deploy
- **Real, computed 2026-06-30** from the cached 500-name matrix: every equity per-window
  return, hit-rate, worst, `N`, and the min-capital floors.
- **At deploy** (live chain / not fabricated): all option POP / max-loss / expected-move /
  premium, MCX margins, and the event-conditional (triggered) CAAR.
- **Next step:** wire this pack into the **fresh pipeline** (`event_universe` selector →
  candidate orchestrator → evidence + payoff cards → new `MarketView` rows) — leaving the
  frozen three untouched — so these become live, armable Views.
