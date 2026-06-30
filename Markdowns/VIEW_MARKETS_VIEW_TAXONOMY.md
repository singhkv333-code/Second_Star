# View Markets — View Taxonomy (V2, India-First)

> **Scope.** This document defines the **groups/types of views Pivot can realistically generate** for V2 (View Markets: *Belief → Expression → Deployment*), conditioned on what Pivot can actually get and trade in India. Every recommendation honours Pivot's hard constraints: **chat-first, India-first (NSE/BSE/NFO; MCX commodities — crude, gold, silver, metals, natgas — tradeable via register-not-execute; foreign equities out of scope → offer the listed Indian ETF proxy), register-not-execute, not-a-broker, not-an-advisor, never fabricate numbers.**
>
> View types (from the V2 spec): **EVENT** (objective outcome + resolution date), **RELATIVE** (A beats B over horizon T), **THEME** (long structural narrative with confirm/invalidate checkpoints). Each view carries a transmission map (cause → effect), a market-expectations/surprise frame, two confidence dimensions (outcome vs expression), Pre-position / Confirmation / Hybrid timing, and Conservative / Balanced / Aggressive expressions.

---

## 0. Executive summary — three constraints that re-rank everything

Pivot's V2 thesis is sound, but three India-first realities re-rank what is shippable. Read these first; every category rating below is conditioned on them.

**Constraint 1 — Prediction-market odds are now a legally radioactive *input*, not a feature.** As of May–June 2026, India (MeitY) has issued an ISP-level **blocking order against Polymarket** and is preparing the same for **Kalshi**; both are classed as "online money games" under the **Promotion and Regulation of Online Gaming Act 2025 (PROGA)**, whose rules took effect 1 May 2026, and Kalshi has added India to its restricted jurisdictions. Implications for Pivot's existing Polymarket/Kalshi adapters:
- Pivot can still **read** public odds server-side and use them internally as a *"what's priced in"* signal — that is research/data, the defensible posture.
- Pivot must **never surface a clickable odds/bet, never let a user "trade the event,"** and should treat displayed probabilities as third-party reference with a disclaimer. Safer still: use PM odds as a **hidden calibration prior** and show Pivot's own **option-implied** probability instead.
- **Net effect:** the EVENT view type loses its cleanest objective probability feed. EVENT views must lean on **official Indian resolution sources** (ECI, MoSPI, RBI, IMD, NSE) and on **option-implied** probabilities Pivot already computes — not on PM odds.

**Constraint 2 — Expression realism is throttled by what is actually *tradeable* on NSE.** NSE now publishes **34 sectoral + many thematic indices** (Railways PSU, EV & New-Age Auto, Power, Capital Goods, Defence-adjacent, …) — excellent *narrative scaffolds*, but only a handful have **liquid ETFs or futures**. Liquid F&O exists for NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY, NIFTYNXT50 and ~190 single stocks; most thematic indices are expressible only as a **basket of constituents** or a **single proxy stock/ETF**. This is exactly the user's complaint ("expressions must not always be a simple basket") — so the expression engine must **prefer the most efficient available instrument** (index option > stock option > liquid ETF > optimised basket) and be **honest** when only a basket is feasible.

**Constraint 3 — register-not-execute + not-an-advisor caps the deployment surface.** Every expression resolves to **armed orders / workflow triggers** the user confirms in their own broker app; no auto-execution; every view ends in "analysis, not advice." A view's *Deployment* is always a workflow-card + register payload, never a placed trade — and the "score" we attach must be framed as **historical reference, not a forecast.**

**One-line ranking:** ship first the categories whose resolution is **official, calendared, and Indian** and whose expression is **liquid and index-level** — **FINANCE/MACRO, WEATHER/MONSOON, CORPORATE/EARNINGS, THEME/SECTOR-STRUCTURAL** (+ **Budget** as a flagship EVENT). Defer the categories that depend on **offshore PM odds, sparse/binary political calendars, or untradeable micro-themes** — **POLITICS, GEOPOLITICS, TRADE, COMMODITIES, CONSUMER** as standalone curated launches; deliver them as overlays/sub-themes.

---

## 1. Cross-cutting data & resolution feasibility map

| Resolution source | What it objectively resolves | Cadence / latency | Pivot wiring status |
|---|---|---|---|
| **MoSPI** release calendar + eSankhyiki | CPI, GDP, IIP, PLFS prints & dates | Calendared; CPI ~12th, GDP quarterly | macro_events calendar candidate (new) |
| **RBI** (MPC schedule, DBIE portal) | Repo decision, stance, CPI projections | 6 MPC/yr, pre-announced | macro_events / event trigger |
| **NSE FII/DII** provisional + **NSDL/CDSL** FPI | Daily net institutional cash flow; fortnightly sector AUC | ~6 PM IST daily; fortnightly | scrape/API → event/indicator trigger |
| **IMD** Long-Range Forecast & rainfall portal | Monsoon % of LPA (LPA=87cm, 1971–2020); cyclones | Stage-1 Apr, update end-May, daily | macro_events (seasonal) |
| **ECI** schedule + results portal | Election dates & outcomes | Per-cycle; results on counting day | event trigger (rare cadence) |
| **NSE/BSE filings**, Trendlyne, Moneycontrol DB | Earnings dates, results, M&A, index rejig | Per-event; quarterly | existing fundamentals DB + ipo_feed pattern |
| **Polymarket / Kalshi adapters** | "What's priced in" for global macro/event | Continuous | **EXISTS but now legally caveated — hidden prior only** |
| **Option chain (Pivot/Kite NFO)** | Market-implied prob, expected move, max-pain, PCR | Live | EXISTS (option engine) |
| **Pivot backtest Trust Battery** | Historical alignment of an *expression* | On-demand | EXISTS (reuse for the "score") |

**Headline:** India gives Pivot world-class *official* calendars (MoSPI / RBI / IMD / ECI / NSE) that are free, authoritative, and objective — a **stronger EVENT-resolution backbone than PM odds ever were**, and they sidestep the PROGA problem entirely.

---

## 2. POLITICS (elections, govt formation, state polls)

**Definition.** Discrete electoral/political outcomes — who wins, seat counts, government formation, confidence votes, key appointments — that the market reprices around.

**Example views.**
- *EVENT:* "BJP-led NDA retains a majority (≥272 of 543) in the 2029 Lok Sabha — resolves on ECI counting day; benchmark: NIFTY ±X% over the 5 sessions post-result vs pre-result close."
- *EVENT:* "The incumbent alliance wins the 2027 **Uttar Pradesh** assembly — benchmark: UP-capex/infra basket vs NIFTY over result-week." (UP/Gujarat/Punjab/Uttarakhand = next big 2027 cycle; the 2026 TN/WB/Kerala/Assam cycle resolved 4 May 2026.)
- *RELATIVE:* "If a capex-continuity government forms, **Nifty PSE / Railways-PSU basket beats Nifty FMCG over 3 months.**"
- *THEME:* "Policy-continuity premium persists — government-capex beneficiaries (defence, railways, power) sustain a multi-year re-rating regardless of single-election noise."

**Resolution + source.** Objective via **ECI** schedule + results portal on counting day. PM odds for Indian elections exist but are **now blocked/illegal to surface in India** — do **not** wire as a user-facing odds feed.

**NSE instruments/expressions.** Index-level NIFTY/BANKNIFTY options for the event-vol crush (elections = classic IV-spike-then-crush → long straddle/strangle pre-event, short premium / iron condor post-event). Sector: PSE, Railways-PSU, Defence-adjacent baskets.

**Tractability & data-feasibility: LOW–MED.** Resolution is clean and official (High on that axis), **but the calendar is sparse and lumpy** (national every 5 yrs, state polls clustered), so there's rarely a live curated view, and binary outcomes are hard to score historically (each election is sui generis; n is tiny). **Defer as a standalone category** (thin cadence + PM-odds prohibition + advisor-sensitivity of "bet on an election"). **Best use:** fold the *option-vol-event* mechanic into FINANCE/MACRO ("event-vol around a known date").

---

## 3. GEOPOLITICS (war/conflict, sanctions, alliances)

**Definition.** Cross-border conflict, sanctions, supply-route disruption, alliance shifts that transmit to Indian assets mainly via **crude, defence, shipping/freight, and INR.**

**Example views.**
- *EVENT:* "A Middle-East escalation keeps **Brent > $90 through Q3** — benchmark: Nifty Oil&Gas upstream (ONGC/Oil India) vs OMCs (BPCL/HPCL/IOC, which bear the subsidy/margin hit)."
- *RELATIVE:* "Sustained border/geopolitical tension → **Defence basket (HAL, BEL, BDL, Mazagon) beats Nifty IT over 6 months.**"
- *THEME:* "Structural defence-indigenisation (Atmanirbhar) is a multi-year order-book theme independent of any single flare-up."
- *EVENT:* "Red Sea / shipping disruption widens freight — benchmark: container/logistics names vs broad market over 1 month."

**Resolution + source.** **No clean objective single source** — conflict has no "resolution date." Resolve via *proxy thresholds Pivot already prices*: Brent level (commodity feed), India VIX, INR (RBI/FBIL reference), defence order announcements (NSE filings). PM odds (war/ceasefire) blocked-and-illegal to surface.

**NSE instruments.** Crude-proxy via OMC/upstream stocks (no liquid retail crude future on NSE; **MCX crude futures/options are now tradeable via register-not-execute** as a direct leg). Defence basket; INR via importer/exporter relative trade. Hedge via NIFTY puts / India-VIX-aware option structures.

**Tractability & data-feasibility: LOW.** Subjective resolution, no calendar, headline whipsaw; of the cleanest expressions, **crude is now directly tradeable (MCX futures/options via register-not-execute)** while **FX stays off-limits for retail**. The LOW rating is driven by the un-dated/subjective *resolution*, not the expression. **Keep geopolitics as a tractable THEME (defence-indigenisation)** + a **transmission overlay** ("if Brent breaks $90, here's the chain", now optionally with a direct MCX crude leg) — not an event-betting surface.

---

## 4. TRADE (tariffs, export bans, supply chains)

**Definition.** Trade-policy shocks — US/EU tariffs on Indian goods, India's own export bans (rice, onions, sugar), PLI-linked import substitution, FTA outcomes.

**Example views.**
- *EVENT:* "US imposes/raises tariffs on Indian pharma or autos — benchmark: affected exporter basket vs domestic-demand basket over 1 month."
- *EVENT:* "India extends the **rice/sugar export curb** — benchmark: sugar names (Balrampur, Triveni) vs Nifty FMCG over the policy window."
- *RELATIVE:* "Rupee depreciation + China+1 tailwind → **IT/pharma exporters beat import-heavy capital-goods over 6 months.**"
- *THEME:* "China+1 supply-chain shift sustains a multi-year electronics/EMS (Dixon, Kaynes, Amber) re-rating."

**Resolution + source.** Indian export bans objective via **DGFT/Commerce notifications** (official, but **no fixed calendar** — event-driven). Foreign tariffs via official trade announcements (irregular). Closer to a **news_events watcher** than a macro_events calendar.

**NSE instruments.** Sector baskets (EMS, sugar, pharma exporters); INR-sensitivity as exporter-vs-importer **pairs** (reuse cointegration/pairs engine). No retail FX/agri-future execution.

**Tractability & data-feasibility: MED–LOW.** Resolution official but **un-calendared and irregular**, so "armed, dated" views are hard. Strong as **reactive THEME/RELATIVE** (China+1, export-ban-on-soft-commodity-producer) where the expression is a liquid equity basket. **Ship as a sub-theme of THEME, not a standalone V1 category.**

---

## 5. FINANCE / MACRO (rates, inflation/CPI, GDP, INR, FII flows, liquidity) — **TOP PRIORITY**

**Definition.** Scheduled macro prints and policy decisions with **fixed official calendars** and **direct, liquid, index-level transmission** — the single best fit for Pivot's machinery.

**Example views.**
- *EVENT:* "RBI **holds/cuts** repo at next MPC — benchmark: **Nifty Bank vs Nifty** over the 5 sessions post-decision; rate-sensitives (NBFCs, realty, autos) as secondary basket." (Resolves on the pre-announced MPC date.)
- *EVENT:* "Next **CPI print < 4.0% y/y** — benchmark: rate-sensitive basket vs defensives over print-day week (MoSPI ~12th)."
- *EVENT:* "Quarterly **GDP > 6.5%** — benchmark: cyclicals (capital goods, metals) vs FMCG."
- *RELATIVE:* "Sustained **net-FII-buying** (NSE provisional + NSDL) → large-cap financials beat broad mid-caps over 1 month."
- *THEME:* "A multi-quarter RBI easing cycle re-rates NBFCs/housing-finance — long the easing-beneficiary basket with rolling confirmation."

**Resolution + source.** **Best-in-class & fully official:** RBI MPC schedule (6/yr, pre-announced), **MoSPI** calendar for CPI/GDP/IIP, **NSE provisional FII/DII ~6 PM IST + NSDL/CDSL fortnightly.** All free, dated, objective → ideal **macro_events** seeds and **event/indicator triggers** the scheduler already supports (scheduled_macro exists).

**NSE instruments.** **Most liquid surface Pivot has:** BANKNIFTY/FINNIFTY/NIFTY options for decision-day event-vol (long straddle pre-print → short premium / iron condor / calendar post-print via option_strategies templates); rate-sensitive baskets via propose_basket_allocation; exporter-vs-importer and bank-vs-NBFC **pairs** for INR/rate relative views.

**Tractability & data-feasibility: HIGH.** Official calendar + objective number + liquid index expression + a clean historical-alignment story ("event study of last N MPC decisions: Bank Nifty mean 5-day move and hit-rate"). Uses **every** existing primitive (macro_events, option engine, basket, pairs, backtest battery) and best honours register-not-execute. **Ship first.**

---

## 6. LEGAL / POLICY / REGULATION (SEBI/RBI rules, PLI, Budget, sectoral policy, courts)

**Definition.** Rule and fiscal-policy changes — Union Budget, PLI tranches, sectoral regulation (telecom AGR, power tariffs, pharma price control), SEBI/RBI circulars, and consequential court rulings.

**Example views.**
- *EVENT:* "**Union Budget** raises capex allocation y/y — benchmark: capex basket (L&T, capital goods, Railways-PSU) vs FMCG over budget-week." (Budget = 1 Feb, fully calendared.)
- *EVENT:* "New **PLI tranche** for semiconductors/electronics — benchmark: EMS basket vs Nifty over the announcement month."
- *EVENT:* "A favourable **AGR/telecom** ruling or relief — benchmark: telecom names over the ruling window."
- *THEME:* "Financialisation + SEBI formalisation sustains a multi-year AMC/exchange/depository (BSE, CDSL, AMCs) re-rating."

**Resolution + source.** Budget perfectly calendared; PLI/circulars/court dates **partly scheduled, partly event-driven** (PIB/ministry/SEBI/RBI notifications, court listings). macro_events for Budget; news_events watcher for the rest.

**NSE instruments.** Capex/PLI/financialisation baskets; budget-day index-option event-vol; single-name proxies where a theme has a clean liquid leader.

**Tractability & data-feasibility: MED-HIGH for Budget, MED otherwise.** Budget is a HIGH-feasibility *annual* curated view (calendared, huge attention, liquid index + capex-basket expression). General policy is irregular. **Ship the Budget view in V1 as a flagship seasonal EVENT; treat broader policy as reactive THEME.**

---

## 7. WEATHER / CLIMATE (monsoon, El Niño, cyclones, heatwaves) — **HIGH-PRIORITY, DISTINCTIVELY INDIAN**

**Definition.** Monsoon quantum and distribution, ENSO state, cyclones, heatwaves — uniquely high-signal in India because agriculture, rural demand, FMCG, fertiliser, tractors, hydro/power, and food CPI all key off rainfall.

**Example views.**
- *EVENT:* "**Southwest monsoon ≥ 96% of LPA** (normal) this season — benchmark: rural-demand basket (Hero, M&M, tractors, FMCG, fertiliser) vs Nifty over Jun–Sep." (IMD LRF: stage-1 Apr, update end-May; LPA=87cm; 2026 forecast = **90% of LPA, below-normal**.)
- *EVENT:* "A **deficient** monsoon (<90% LPA) — benchmark: irrigation/agri-input/staples-pricing-power names vs broad ag-discretionary."
- *RELATIVE:* "Below-normal rains → **fertiliser/irrigation beats tractor/2-wheeler over the season.**"
- *THEME:* "El Niño-driven below-normal water sustains a multi-quarter food-inflation / staples-pricing-power tilt."

**Resolution + source.** **Official and objective via IMD** — LRF % of LPA, monthly updates, daily/seasonal rainfall portal, cyclone bulletins. Clean numeric bands (≥96% normal, 90–96% below-normal, <90% deficient) make these **measurable, dated EVENT views.** Strong macro_events seed.

**NSE instruments.** Rural-consumption / agri-input baskets (no single liquid "monsoon index," so basket or proxy stocks); FMCG vs agri-discretionary **pairs**; food-CPI link ties weather views to MACRO. No weather-derivative execution for retail (agri/soft commodities aren't in the MCX-tradeable set — crude/gold/silver/metals/natgas).

**Tractability & data-feasibility: HIGH (data) / MED (expression).** IMD is a gold-standard objective feed and the theme is *uniquely resonant for Indian retail* — a genuine differentiator no US copilot can replicate. Expression is basket-heavy (caveat honestly), but the historical-alignment story is strong (event-study monsoon-band years vs rural-basket performance). **Ship in V1 as the signature India-first seasonal view.**

---

## 8. COMMODITIES / ENERGY (crude, gold, gas, metals)

**Definition.** Commodity-price regimes transmitting to Indian equities — crude → OMCs/aviation/paints, gold → jewellers/financiers, metals → metal producers, gas → CGD/fertiliser.

**Example views.**
- *RELATIVE:* "**Brent > $90 sustained** → upstream (ONGC/Oil India) beats OMCs (BPCL/HPCL) over 1 month."
- *EVENT:* "Higher crude squeezes input costs → **paints/aviation/tyres underperform Nifty over a month.**"
- *THEME:* "Structurally firm gold → jewellers (Titan, Kalyan) and gold-loan NBFCs (Muthoot, Manappuram) sustain a multi-quarter tailwind." (Plus **gold/silver SIP** — already a retail priority.)
- *RELATIVE:* "Metals upcycle → Nifty Metal beats Nifty IT over 3 months."

**Resolution + source.** Commodity price levels via Kite/yfinance proxies and global benchmarks; **MCX commodities (crude, gold, silver, metals, natgas) are now tradeable via register-not-execute**, so crude/gold/metals are both a signal *and* a directly executable contract (Pivot registers/arms; the user confirms in their broker — leveraged, so keep the risk caveat). Resolution is a price threshold (objective) but **not a dated event.**

**NSE instruments.** Equity proxies and **pairs** (upstream-vs-OMC, metal-vs-IT); **gold via Indian gold ETFs / SGB-style proxy** for the SIP theme; **plus direct MCX commodity futures/options (crude, gold, silver, metals, natgas) via register-not-execute** (leveraged — keep the risk caveat).

**Tractability & data-feasibility: MED–HIGH.** Signals are clean, the equity-transmission expressions are liquid, **and the commodity itself is now directly tradeable (MCX futures/options via register-not-execute)** — so a view can be expressed *either* as a **RELATIVE/THEME equity expression driven by a commodity signal** *or* as a **direct MCX commodity leg**, alongside the existing **gold/silver SIP**. The remaining drag is that resolution is a non-dated price threshold rather than a calendared event. Ship gold/metals-as-equity-theme in V1's THEME bucket **and enable the direct-commodity (e.g. crude) route** now that MCX is tradeable.

---

## 9. CORPORATE / EARNINGS (results, M&A, management change, index inclusion) — **HIGH-PRIORITY**

**Definition.** Single-name/sector catalysts on a **known earnings calendar** plus event-driven M&A, leadership change, and **index rejig** (NIFTY/Sensex inclusion-exclusion) — the densest, most frequent, most personal-to-the-user catalyst set.

**Example views.**
- *EVENT:* "**INFY beats** consensus this quarter — benchmark: stock's 2-day post-result move; expressed as a pre-earnings long straddle (IV-crush aware)." (Earnings dates calendared via NSE/Trendlyne.)
- *EVENT:* "**Index inclusion**: stock X added to NIFTY 50 at next rejig → passive-flow pop benchmark over inclusion week." (Rejig dates scheduled/announced.)
- *RELATIVE:* "Going into IT earnings season, **TCS beats Infosys** on margin commentary over the result fortnight." (pairs engine.)
- *THEME:* "A bank-results season of improving NIMs re-rates the PSU-bank basket."

**Resolution + source.** **Objective and frequent:** results dates and actuals via **NSE/BSE filings + Trendlyne/Moneycontrol** (Pivot has the fundamentals DB and ipo_feed ingestion pattern to mirror). Index rejig via NSE Indices. Earnings recur every quarter → continuous live-view supply (unlike politics).

**NSE instruments.** **Most liquid single-name surface:** ~190 F&O stocks for pre-earnings option structures (straddle/strangle, calendar — option_strategies templates) with explicit IV-crush warning; **pairs** for peer relative views; baskets for sector-season views.

**Tractability & data-feasibility: HIGH.** Calendared + objective + frequent + personal (users already track specific stocks) + liquid single-stock option expression + a clean historical-alignment story (event-study of a stock's last N earnings IV-behaviour and post-move). Reuses option engine + pairs + fundamentals DB. **Ship in V1** — highest-*cadence* category, which keeps the View Markets surface alive between rare macro/weather events.

---

## 10. TECH / SECTOR STRUCTURAL (AI, EV, renewables, defence, semiconductors) — **THE THEME ENGINE**

**Definition.** Long, structural narratives — multi-quarter to multi-year — inherently THEME-type, mapping directly onto NSE's expanded thematic-index set and Pivot's `thematic_map.py`.

**Example views.**
- *THEME:* "**Defence indigenisation** (Atmanirbhar order books) sustains HAL/BEL/BDL/Mazagon outperformance over 12 months — confirm: order-inflow prints; invalidate: budget-capex cut."
- *THEME:* "**EV & New-Age Auto** transition (NSE has a dedicated index) — long the EV-ecosystem basket vs legacy ICE over 12 months."
- *THEME:* "**Renewables/Power capex** supercycle — Nifty Power / Capital-Goods beneficiaries."
- *THEME:* "**Semiconductors/EMS** (PLI-driven China+1) — Dixon/Kaynes/Amber basket."
- *RELATIVE:* "AI-capex tailwind → Indian IT-services *underperforms* product/ER&D peers over 6 months (a contrarian framed view)."
- *Foreign-proxy rule:* US-tech AI exposure → offer the **listed Indian ETF proxy (MON100)**, never a foreign equity.

**Resolution + source.** THEMEs resolve on a **horizon with confirm/invalidate checkpoints** (the V2 framing), not a single date — `thematic_map.py` already encodes winners/losers/confirm/invalidate for 6 macro scenarios; extend with these sector themes. Checkpoints use order-book prints, sector-index relative performance, and policy events.

**NSE instruments.** **Sector/thematic baskets via propose_basket_allocation** (equal/mcap/risk-parity/min-variance/black-litterman/factor) over `sector_universe.py`; where a liquid thematic ETF exists, prefer it; **relative theme** as a long-short basket-vs-NIFTY or peer pair (cointegration engine). This is where "not always a simple basket" is satisfied — themes can be **risk-parity/min-variance optimised baskets, long-short relative baskets, or option-overlay-hedged baskets**, not naive equal-weight.

**Tractability & data-feasibility: HIGH (expression & data); resolution is horizon-based by design.** Directly reuses thematic_map + basket allocation + sector_universe + pairs + full backtest battery, maps onto real NSE indices, and is the most *durable* view supply (a theme stays live for months). **Ship in V1 as the THEME flagship.** Caveat: many thematic indices lack liquid ETFs/futures, so expression often *is* a basket — make it a *good* (optimised, risk-managed) basket and disclose tradeability of each leg.

---

## 11. CONSUMER / DEMAND

**Definition.** Discretionary vs staples demand, rural vs urban, festive/seasonal consumption, premiumisation.

**Example views.**
- *RELATIVE:* "Strong **festive season** (Dhanteras/Diwali) → discretionary (autos, jewellery, durables) beats staples over the festive quarter."
- *THEME:* "**Premiumisation** — premium-FMCG/QSR/retail beats mass-FMCG over 12 months."
- *RELATIVE:* "Good monsoon → **rural-skewed FMCG beats urban-discretionary** (links to WEATHER §7)."
- *EVENT:* "GST-rate cut on a consumer category → affected basket vs Nifty over the policy month."

**Resolution + source.** **Weakest objective feed** — demand inferred from corporate commentary, auto monthly dispatch numbers (objective, monthly), GST collections (monthly, official), FMCG volume-growth prints (quarterly). No single clean calendar; mostly derivative of WEATHER + MACRO + EARNINGS.

**NSE instruments.** Consumption baskets; discretionary-vs-staples **pairs**; auto-dispatch-linked names.

**Tractability & data-feasibility: LOW–MED as standalone.** Real and India-relevant, but **no native resolution feed of its own** — it's a *downstream* of monsoon, rates, festive timing, and earnings. **Defer as a standalone category; deliver as relative/theme expressions hanging off WEATHER, MACRO, and EARNINGS.** (Auto monthly dispatches and GST collections are the two objective hooks worth wiring later.)

---

## 12. Beyond "a simple basket": the Expression Engine standard + the user-facing Score

The user's two explicit asks: **(i)** expressions must be *proper, effective strategies*, not always equal-weight baskets; **(ii)** real testing standards + a user-facing *historical-alignment score*. Here is the India-realistic design, all on existing primitives.

### 12.1 Expression ladder (pick the most efficient feasible instrument)
For any view, prefer in order:
1. **Index option structure** (NIFTY/BANKNIFTY/FINNIFTY) — best for EVENT/event-vol (RBI/CPI/Budget/elections): straddle/strangle pre-event, iron condor/credit spread/calendar post-event — straight from `option_strategies.py` (15+ templates w/ greeks/payoff/POP/margin/critique). The cleanest answer to "not a basket."
2. **Single-stock option structure** (F&O names) — for EARNINGS/corporate catalysts (IV-crush-aware straddle/calendar).
3. **RELATIVE / pairs** — long A short B on a cointegrated spread via `services/backtest/pairs/` (Engle-Granger / Johansen / OU half-life), entry/exit at ±z on the spread. Ideal for exporter-vs-importer, upstream-vs-OMC, peer-vs-peer (NSE auto & realty pairs notably validated).
4. **Optimised basket / long-short basket** — for THEME: `propose_basket_allocation` with **risk-parity / min-variance / black-litterman / factor** weighting over `sector_universe.py`, optionally **long-theme / short-NIFTY** to isolate the narrative. Equal-weight is the *fallback*, not the default.
5. **Option-overlay-hedged basket** — basket + protective NIFTY puts / collar for THEME views with event risk.

The engine also emits the V2 **Conservative / Balanced / Aggressive** tier by varying: option moneyness & defined-vs-undefined risk; pair z-thresholds & leverage; basket concentration & hedge ratio. And it honours **Pre-position / Confirmation / Hybrid** timing via the workflow scheduler (arm now vs arm-on-trigger vs scale-in).

### 12.2 Testing standard — two regimes (the key India-realistic nuance)
A one-off election or a single CPI print **cannot be backtested like a strategy** (n≈1). So split:

- **Continuous expressions (RELATIVE, THEME, any rules-based EVENT strategy):** run the **full existing Trust Battery** — Probabilistic Sharpe, **Deflated Sharpe** (penalising trial count), **Minimum Track Record Length**, **Monte-Carlo block bootstrap**, **walk-forward**, **no-skill permutation test** — surfaced as the plain-English Trust verdict ladder (`insufficient_data → no_edge → unproven → promising`). This is exactly what "real standards" means, and it already exists — just route View expressions through it and **log every tested expression into the trial counter** so Deflated Sharpe stays honest (critical: View Markets generates many candidate expressions per view → multiple-testing inflation is the #1 risk).
- **Discrete EVENT views (RBI decision, monsoon band, earnings, budget):** you can't Sharpe a single event, so use an **Event-Study / Base-Rate "Historical Alignment"** standard: over the last N comparable events (last 12 MPC cuts, last 10 below-normal-monsoon years, a stock's last 12 earnings), compute **mean & median benchmark-relative move, hit-rate (% of times the view's direction paid), dispersion, and worst-case drawdown over the holding window.** Report **n** prominently; if n is small (elections!), say so and downgrade confidence.

### 12.3 The user-facing "View Alignment Score" (reference, not forecast)
A single 0–100 (or 5-band) score per view, composed of:
- **Historical Alignment** — discrete events: hit-rate × effect-size, shrunk by small-n; continuous expressions: the Trust verdict band.
- **Statistical Confidence** — Deflated-Sharpe / MinTRL pass for continuous; n and dispersion for events. Penalise trial count explicitly.
- **Expression Efficiency** — how directly tradeable/liquid the chosen instrument is (index-option = high; basket-of-illiquids = low). Operationalises Constraint 2.
- **Two confidence dimensions kept separate** (per V2 spec): *Outcome confidence* (will the belief happen?) vs *Expression confidence* (if it happens, will this trade pay?). Never collapse them — a high-outcome / low-expression view is a real and common failure mode.

**Framing rules (compliance):** label it **"historical alignment for reference — not a prediction, not advice"**; show the **base rate / n**; never imply edge where the permutation/Deflated-Sharpe test says `no_edge`; never fabricate — every number traces to a card/tool value. This satisfies register-not-execute and not-an-advisor while giving users the rigorous "score" they asked for.

---

## 13. Summary matrix (category × view-type × tractability × data source)

| # | Category | Best view-type(s) | Tractability (data-feasibility) | Primary resolution source | Typical expression |
|---|---|---|---|---|---|
| 2 | **Politics** | EVENT, RELATIVE, THEME | **LOW–MED** (clean resolution, sparse cadence, low-n scoring; PM odds barred) | ECI (PM odds = hidden prior only) | Index-option event-vol; PSE/Railways/Defence basket |
| 3 | **Geopolitics** | THEME (defence), overlay | **LOW** (no dated resolution; FX off-limits, crude now tradeable via MCX) | Proxy thresholds: Brent, India VIX, INR; NSE filings | Defence basket; OMC-vs-upstream pair; NIFTY put hedge; optional direct MCX crude leg |
| 4 | **Trade** | THEME, RELATIVE, EVENT | **MED–LOW** (official but un-calendared) | DGFT/Commerce notifications; news_events watcher | EMS/sugar/pharma basket; exporter-vs-importer pair |
| 5 | **Finance / Macro** | **EVENT**, RELATIVE, THEME | **HIGH** (official calendar + liquid index) | RBI MPC, MoSPI, NSE/NSDL FII-DII | BANKNIFTY/FINNIFTY option event-vol; rate baskets; pairs |
| 6 | **Legal / Policy** | EVENT (Budget), THEME | **MED-HIGH (Budget)** / MED otherwise | Budget calendar; PIB/SEBI/RBI/court (event-driven) | Capex basket; budget-day index option |
| 7 | **Weather / Monsoon** | **EVENT**, RELATIVE, THEME | **HIGH data / MED expression** | IMD LRF % of LPA + rainfall portal | Rural/agri-input basket; FMCG-vs-agri pair |
| 8 | **Commodities / Energy** | RELATIVE, THEME, **direct MCX** | **MED–HIGH** (clean signal; commodity now tradeable via register-not-execute; resolution un-dated) | Kite/yfinance price proxies + **MCX futures/options (tradeable)** | Upstream-vs-OMC / metal-vs-IT pair; gold-ETF SIP; **direct MCX crude/gold/metals leg** |
| 9 | **Corporate / Earnings** | **EVENT**, RELATIVE, THEME | **HIGH** (calendared, frequent, liquid names) | NSE/BSE filings + Trendlyne/Moneycontrol | Single-stock option straddle/calendar; peer pair |
| 10 | **Tech / Sector-Structural** | **THEME**, RELATIVE | **HIGH** (reuses thematic_map + basket engine) | Horizon checkpoints; NSE thematic indices | Optimised / long-short / hedged sector basket |
| 11 | **Consumer / Demand** | RELATIVE, THEME, EVENT | **LOW–MED** (no native feed; downstream) | Auto dispatches, GST collections (future wiring) | Discretionary-vs-staples pair; consumption basket |

---

## 14. Prioritised V1 shortlist (curated views) vs defer

### Ship FIRST in V1 (curated, high-feasibility, India-first)
1. **FINANCE/MACRO (RBI MPC + CPI + GDP + FII flows)** — *the anchor.* Official calendar (RBI/MoSPI/NSE), liquid index-option + basket + pairs expression, clean event-study scoring, uses every primitive. Highest feasibility, broadest relevance.
2. **CORPORATE/EARNINGS (+ index inclusion)** — *the cadence engine.* Calendared, frequent (keeps the surface alive weekly), objective via NSE/Trendlyne, liquid single-stock options, personal to users. Reuses option engine + pairs + fundamentals DB.
3. **THEME / SECTOR-STRUCTURAL (defence, EV, power/renewables, EMS/semis)** — *the durability engine.* Maps onto real NSE thematic indices and existing `thematic_map.py` + basket allocation + sector_universe; views stay live for months; best showcase for "proper strategies, not equal-weight baskets."
4. **WEATHER/MONSOON** — *the India-first differentiator.* IMD LRF % of LPA is a gold-standard objective feed; uniquely resonant for Indian retail; strong base-rate scoring. (Expression is basket-heavy — disclose honestly.)
5. **BUDGET (carve-out of LEGAL/POLICY)** — *the flagship annual EVENT.* Perfectly calendared (1 Feb), huge attention, liquid index-option + capex-basket expression.

### Defer (fold in as overlays/themes, or wait for data wiring)
- **POLITICS** — sparse calendar (next big cycle UP/Gujarat 2027), binary/low-n scoring, **PM-odds prohibition under PROGA.** Keep the *option-event-vol* mechanic inside MACRO; don't build an election-betting surface.
- **GEOPOLITICS** — no objective resolution/calendar; of its cleanest expressions, **crude is now tradeable (MCX register-not-execute)** while **FX stays off-limits for retail**. Survives mainly as the **defence THEME** (already in #3) + a transmission overlay, now optionally with a direct MCX crude leg.
- **TRADE** — official but un-calendared/irregular; deliver as reactive China+1 / export-ban sub-themes under THEME.
- **COMMODITIES/ENERGY** — **commodity itself now directly tradeable (MCX futures/options via register-not-execute)**; deliver as commodity-signal-driven **equity RELATIVE/THEME** + the existing gold/silver SIP **and** a direct MCX leg (e.g. trade crude). The only residual drag is the non-dated price-threshold resolution, so curate the trigger carefully.
- **CONSUMER/DEMAND** — no native resolution feed; downstream of WEATHER+MACRO+EARNINGS. Deliver as relative expressions hanging off those; later wire auto monthly dispatches + GST collections as objective hooks.

### Cross-cutting build note
The single most important engineering caveat for V2's credibility: **wire the trial counter through every View expression** so Deflated Sharpe / MinTRL stay honest under the multiple-testing explosion a "generate many expressions per view" product inherently creates. And **demote PM-odds to a hidden internal prior** — surface Pivot's *own* option-implied probabilities to users to stay clear of PROGA. These two choices protect both the **statistical** and the **regulatory** integrity of the whole View Markets surface.

---

## Sources
1. Polymarket — Indian Elections markets/odds page.
2. Kalshi India coverage (cricket/monsoon/USD-INR/RBI markets) overview.
3. CoinDesk — "India cracks down on prediction markets: Polymarket goes dark, Kalshi could be next" (May 2026).
4. NSE Indices — Thematic indices + Business Standard "NSE launches 11 new sectoral indices (total 34)".
5. MoSPI Release Calendar / eSankhyiki + RBI DBIE.
6. IMD Long-Range Forecast for the 2026 SW Monsoon (90% of LPA; LPA=87cm 1971–2020) + rainfall portal.
7. NSE FII/DII provisional report + NSDL FPI fortnightly.
8. crypto.news / cryptotimes — Polymarket offline & Kalshi India restriction under PROGA 2025 (Jun 2026).
9. 2026 elections in India (TN/WB/Kerala/Assam/Puducherry, counted 4 May 2026) + 2027 cycle.
10. "Designing Efficient Pair-Trading Strategies Using Cointegration for the Indian Stock Market" (Engle-Granger on NSE sectors; auto/realty strongest).
