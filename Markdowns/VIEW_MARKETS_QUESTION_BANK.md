# View Markets — The Question Bank (V1 ideation)

> The generative companion to `VIEW_MARKETS_CORE_PHILOSOPHY.md`,
> `VIEW_MARKETS_VIEW_TAXONOMY.md`, and `VIEW_MARKETS_STRATEGY_DESIGN.md`.
> Those docs say *how* to build a view and *how* to test it. This one answers the
> prior question the shipped V1 skipped: **what should the views actually be?**
>
> Every entry is a **question a real Indian retail investor would have an opinion
> about**, paired with a **concrete, India-tradeable expression for YES and (where
> one honestly exists) for NO** — picked from Pivot's real rails, register-not-
> execute, defined-risk-first. A question that can't clear the scope filter in
> Part 1 is not in the bank, no matter how engaging.
>
> Status: ideation / planning. Numbers are illustrative of *structure*, never
> backtested claims — the backtest battery + Alignment Score decide what ships.

---

## Part 0 — What the whole thing means

Most people never think *"buy a bull call spread."* They think:

- *"Gold's going to keep ripping."*
- *"My home-loan EMI should finally come down this year."*
- *"Defence stocks have run too far."*
- *"The rupee's going to 90."*
- *"This market is due for a crash."*

View Markets is the machine that turns **that sentence** into a position. The flow
is always:

```
Opinion  →  a YES/NO question (measurable, dated, benchmarked)
         →  market consequence (who wins / who loses)
         →  an India-tradeable expression (the ladder, not "a basket")
         →  a deployable, register-not-execute strategy + an honest score
```

Two design truths govern the whole bank:

1. **The unit is a question, not a statement.** "A good monsoon lifts rural stocks"
   is a lecture. *"Will this year's monsoon be normal-or-better?"* is a stance the
   user *takes* — and taking a side is what creates engagement and a reason to
   deploy. Everything below is phrased as a question with a YES side.

2. **Binary question ≠ binary trade.** Every question gets a YES/NO. But (per the
   philosophy) **not every NO has a clean inverse trade.** "Will the US strike Iran?"
   → YES has strong expressions; NO has none (peace ≠ a specific trade). Forcing a
   NO trade where none exists is a *correctness failure*. Each row therefore states
   the NO expression **or** explicitly marks it *AVOID / no clean trade* — and that
   honesty is a feature.

---

## Part 1 — The scope filter (what makes a question shippable)

A question enters the bank only if it clears **all five gates**:

| Gate | Test |
|---|---|
| **1. Interesting** | Would an ordinary person have an opinion without being taught finance? |
| **2. Measurable** | Is there an objective resolution — a number, a print, an official source? |
| **3. Time-bound** | Is there a horizon / resolution date / season? |
| **4. Consequential** | Does the outcome move identifiable Indian assets (clear winners & losers)? |
| **5. Tradeable on Pivot's rails** | Can YES be expressed with the instruments below, register-not-execute? |

**Engagement score** (rank within the bank) = `Curiosity × Debatability × Tradeability × Resolution-clarity`. The launch set (Part 5) maximises this.

### The tradeable universe (what "expressible" means here)

**IN — Pivot can express it:**
- **Equities** — NSE/BSE cash (CNC delivery) + intraday (MIS).
- **Indices** — NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY, SENSEX, NIFTYNXT50; ~34 sectoral/thematic indices as **baskets or ETFs**.
- **Options (NFO)** — NIFTY/SENSEX **weeklies**; BANKNIFTY **monthly**; ~190–208 single-stock options (**monthly, physically settled, STT-on-intrinsic** — force pre-expiry square-off).
- **ETFs** — sector ETFs; **smart-beta** (Momentum 30 / Quality 30 / Value 20 / Low-Vol 30 / Multi-Factor); **gold/silver ETFs** (Gold BeES etc.); **MON100** (US-tech proxy); **gilt / Bharat Bond / target-maturity** (duration).
- **MCX commodities** — **crude, gold, silver, base metals, natgas** futures/options, register-not-execute (leveraged — risk caveat).
- **Single-stock futures (~208 SSF names)** — the *honest short* leg.
- **Pairs / cointegration** + **basket weighting** (equal / mcap-free-float / risk-parity / min-variance / Black-Litterman / factor).

**OUT — express via proxy or honestly decline:**
- **Foreign equities** → listed **Indian ETF proxy** (US-tech → MON100), never the foreign line.
- **The rupee / FX direct** → retail can't trade it cleanly → express via **exporter-vs-importer equity pairs**.
- **Agri / soft commodities** (not in the MCX-tradeable set) → **equity proxies** (fertiliser, sugar, FMCG).
- **Crypto** → no clean Indian-retail instrument → **engagement shelf only** (Part 3-P), flagged.
- **Sports / elections-as-bets** → never a YES/NO *contract* (PROGA); only the **equity/vol consequence** is tradeable.

### The expression ladder (always prefer the most efficient feasible instrument)

> **Index option > single-stock option > cointegrated pair > smart-beta-ETF-vs-index > optimised basket > equal-weight basket (fallback only).**
> The honest short: index short = **NIFTY/BANKNIFTY put or future** (never short an ETF); single-name short = **SSF or long put**; else **AVOID-annotate**.

---

## Part 2 — What Indian retail *genuinely* cares about (the engagement map)

Ranked by `Curiosity × Debatability × Tradeability × Resolution-clarity`. This is the
order the bank is roughly built in, and it is *deliberately the inverse of what V1
shipped* (V1 shipped two of the lowest-cadence theme-baskets and zero of the top tier).

| Tier | Clusters | Why it wins |
|---|---|---|
| **S — must-have** | Gold/silver · Crude & petrol · RBI rates / your EMI · Nifty–Sensex milestones · Crash-vs-calm (volatility) | Everyone feels these *personally*, they're loud, and they're the **cleanest** to trade (index/commodity/ETF). |
| **A — high** | Budget · Monsoon · Single-stock earnings · IPOs/listings · Defence · Railways/PSU · The rupee | High curiosity + high debatability; tradeable with one extra hop (basket/pair/proxy). |
| **B — strong themes** | AI-vs-IT · EV · Green energy · Manufacturing/China+1 · PSU-vs-private banks · Pharma · Real estate · Metals | Durable, argued-about, keep the surface alive for months; basket/pair/ETF expressions. |
| **C — global→India** | US Fed · US-tech (MON100) · China · Trade/tariffs | People follow the headlines; express through the **Indian transmission**, not the foreign asset. |
| **Shelf — engage-only** | Cricket/IPL · Crypto · Geopolitics-as-event | Massive engagement, **no clean retail trade** → honest proxy or "watch only." |

---

## Part 3 — The Question Bank

Format per row: **Question** (YES/NO, specific) · **Type** (EVENT / RELATIVE / PRICE-TARGET / RANGE-VOL / THEME) · **Resolves** · **YES expression** (most-efficient feasible instrument) · **NO expression** (or *AVOID*).

> Types follow the philosophy's six-way split, collapsed to the five Pivot can trade
> (Sports is engagement-only → the shelf).

---

### A. Gold & silver — *the most Indian asset there is* (S-tier)

Indians hold ~25,000 tonnes of gold; everyone has a price opinion, and it's one of the
few views you can trade *directly* (ETF + MCX) **and** through equities (jewellers,
gold-loan NBFCs).

| Question | Type | Resolves | YES → | NO → |
|---|---|---|---|---|
| Will **gold beat the Nifty** over the next 12 months? | RELATIVE | rolling 12m total-return, gold ETF vs NIFTYBEES | Long **Gold BeES**; or **gold ETF long vs NIFTY future** to isolate the spread | Long **NIFTYBEES** / underweight gold sleeve |
| Will **gold cross ₹1,00,000 / 10g** (MCX) within 6 months? | PRICE-TARGET | MCX gold spot threshold | **MCX gold** long (defined-risk via call/call-spread); or Gold BeES | Short via **MCX gold put-spread** (defined-risk) |
| Will **silver outperform gold** over the next 6 months (gold:silver ratio falls)? | RELATIVE | MCX gold:silver ratio | Long **Silver BeES / MCX silver**, short MCX gold (ratio trade) | Long gold / short silver (ratio reverts up) |
| With gold hot, will **gold-loan NBFCs beat the Bank Nifty** over 6 months? | RELATIVE | Muthoot+Manappuram basket vs BANKNIFTY | Long **Muthoot/Manappuram** basket vs **BANKNIFTY put/future** hedge | Long BANKNIFTY / underweight gold-loan |
| Heading into **Dhanteras/Akshaya Tritiya**, will **jewellers beat the market** into the festival? | EVENT (seasonal) | Titan/Kalyan/Senco basket vs NIFTY, festival window | Long **jewellers basket**, Pre-position 3–4 wks before | *AVOID* (no clean inverse; festival demand is one-sided) |
| Will a **gold pullback** (>8% off highs) arrive in the next quarter? | PRICE-TARGET | Gold ETF drawdown threshold | **MCX gold put-spread** (defined-risk) or trim sleeve | Long Gold BeES (dip-buy) |

---

### B. Crude, petrol & energy — *everyone feels the pump* (S-tier)

Petrol price is dinner-table conversation; India imports ~85% of its crude, so the
transmission is rich and **two-sided** (importers win on cheap oil, upstream wins on
dear oil), and crude itself is now MCX-tradeable.

| Question | Type | Resolves | YES → | NO → |
|---|---|---|---|---|
| Will **Brent stay below $75** for the next quarter (cheap-oil regime)? | RANGE-VOL | Brent level, quarter | **Importer basket** (Asian Paints/Berger, IndiGo, OMCs) long; or **MCX crude put-spread** | Long **upstream** (ONGC/Oil India) / short OMCs (pair) |
| Will **crude spike above $90** on a Middle-East flare-up in 3 months? | PRICE-TARGET / EVENT | Brent threshold | **MCX crude call-spread**; + long **ONGC/Oil India**, short **IndiGo/paints** | *AVOID on the downside* (no-flare ≠ a clean short) |
| Will **OMCs (BPCL/HPCL/IOC) beat the Nifty** over 6 months if oil stays soft? | RELATIVE | OMC basket vs NIFTY | Long **OMC basket vs NIFTY future** hedge | Long NIFTY / underweight OMCs |
| Will **upstream beat downstream** (ONGC vs BPCL) over a quarter? | RELATIVE | cointegrated pair | **Long ONGC / short BPCL** (SSF pair at hedge ratio) | Reverse the pair |
| Will **paints & airlines beat the market** in a falling-crude quarter? | RELATIVE | Asian Paints+Berger+IndiGo vs NIFTY | Long **input-cost-sensitive basket** vs NIFTY hedge | *AVOID* (margin tailwind is one-sided) |
| Will **natural gas (MCX) fall** over the next month? | PRICE-TARGET | MCX natgas | **MCX natgas put-spread**; + long CGD names (IGL/MGL) | CGD long only (margin squeeze on dear gas) |

---

### C. The rupee — *the number on every news ticker* (A-tier)

Retail can't trade USD/INR cleanly → the **honest** expression is the equity
transmission: a weak rupee helps exporters (IT, pharma), hurts importers.

| Question | Type | Resolves | YES → | NO → |
|---|---|---|---|---|
| Will the **rupee cross ₹90/$** within 6 months? | PRICE-TARGET (proxy) | RBI/FBIL reference rate | **Long IT/pharma exporters vs short importer basket** (exporter-importer pair) | Long importers (autos, OMCs, capital goods) vs exporters |
| Will **Indian IT beat the Nifty** over 6 months on rupee weakness + US demand? | RELATIVE | Nifty IT vs NIFTY | **Nifty IT ETF vs NIFTY future**; or long top-4 IT basket hedged | Long NIFTY / underweight IT |
| Will a **strong rupee quarter** see importers beat exporters? | RELATIVE | importer vs exporter basket | Long **importer basket** / short **Nifty IT** (pair) | Reverse |

---

### D. RBI, rates & your EMI — *the most personal macro view* (S-tier)

"Will my EMI come down?" is the most relatable macro question in India. RBI MPC is a
**calendared EVENT** (~6/yr) — the cleanest event-vol + surprise play Pivot owns. This
is the category V1 most conspicuously skipped.

| Question | Type | Resolves | YES → | NO → |
|---|---|---|---|---|
| Will **RBI cut the repo rate** at the next MPC? | EVENT | RBI MPC date (objective) | **Pre-position:** BANKNIFTY/Nifty **bull-call debit spread**; rate-sensitive basket (NBFC/realty/auto) | **Hold/hike:** long-vol **straddle** into the print (surprise either way) |
| If RBI cuts, will **NBFCs beat banks** over the next month (NIMs compress on a cut)? | RELATIVE | NBFC vs bank basket | **Long NBFC (Bajaj Fin/Chola/Shriram) / short HDFC+ICICI** (SSF pair) — the pro's rate trade | Long banks / short NBFCs |
| Will **realty stocks beat the Nifty** in the 3 months after a cut? | RELATIVE | Nifty Realty vs NIFTY | Long **Nifty Realty** basket vs NIFTY hedge, Confirmation timing | Long NIFTY / underweight realty |
| Will the **next CPI print come in below 4.0%** y/y? | EVENT | MoSPI release (~12th) | Rate-sensitive basket long, Pre-position into print | Defensives (FMCG/pharma) vs cyclicals |
| Will **quarterly GDP beat 6.5%**? | EVENT | MoSPI GDP release | **Cyclicals** (capital goods/metals) vs **FMCG** (pair) | FMCG/defensives over cyclicals |
| Will the **MPC surprise dovish** vs the consensus 25bps (50bps or dovish guidance)? | EVENT (surprise) | MPC decision + statement | BANKNIFTY **call-spread sized to the priced-in move** (surprise > priced) | Iron-condor / credit spread (realized < priced) |

---

### E. Index milestones — *the headline number everyone watches* (S-tier)

Round-number levels are pure curiosity and the **single most efficient thing to trade**
(index options/ETF). The shipped V1 had **zero** price-target views; this fixes it.

| Question | Type | Resolves | YES → | NO → |
|---|---|---|---|---|
| Will **Nifty cross 30,000** by year-end? | PRICE-TARGET | NIFTY level | **NIFTY call-spread** (defined-risk); or NIFTYBEES + add-on triggers | **NIFTY put-spread** (defined-risk) |
| Will **Sensex hit 1,00,000** within 12 months? | PRICE-TARGET | SENSEX level | SENSEX **call-spread** (weeklies available) | SENSEX put-spread |
| Will **Bank Nifty make a new all-time high** this quarter? | PRICE-TARGET | BANKNIFTY level | BANKNIFTY **monthly call-spread** | BANKNIFTY put-spread |
| Will **Nifty close the year higher than it opened** (up-year)? | PRICE-TARGET | NIFTY YoY | Long **NIFTYBEES** + staged adds | NIFTY collar / put-spread hedge |
| Will the **Nifty Midcap 150 beat the Nifty 50** this year (broadening rally)? | RELATIVE | Midcap vs largecap index | **MIDCPNIFTY** exposure vs **NIFTY future** hedge | Long NIFTY / short midcap (futures) |

---

### F. Crash vs calm — *the fear/greed view* (S-tier)

"Is a crash coming?" and "will the market just grind up quietly?" are the most emotional
market opinions — and they map *perfectly* onto volatility structures, which retail
rarely knows how to access. This is a uniquely high-value Pivot surface.

| Question | Type | Resolves | YES → | NO → |
|---|---|---|---|---|
| Will the **Nifty stay within a ±5% range** over the next month (calm)? | RANGE-VOL | NIFTY range, month | **Iron condor / short strangle as defined-risk iron fly** on NIFTY (sell premium) | Long **straddle** (break-out either way) |
| Will there be a **>10% Nifty drawdown** in the next quarter (crash)? | EVENT / RANGE-VOL | NIFTY drawdown threshold | **NIFTY put-spread** or **protective put / zero-cost collar** on a portfolio | Sell premium (condor) + long beta |
| Will **India VIX spike above 20** in the next month? | PRICE-TARGET (vol) | India VIX level | Long **NIFTY straddle/strangle** pre-event; trim into the spike | Short premium (defined-risk fly) |
| Will the market be **calm through a known event** (Budget/MPC/election result)? | RANGE-VOL (event) | realized vs implied move | **Sell event-vol** post-print (iron condor/calendar) | **Buy event-vol** pre-print (straddle), close into crush |

---

### G. The Budget — *the one policy day everyone watches* (A-tier)

Feb 1, perfectly calendared, enormous attention, huge sector dispersion → a flagship
annual EVENT with both an index-vol play and a capex-rotation play.

| Question | Type | Resolves | YES → | NO → |
|---|---|---|---|---|
| Will the **Budget raise capex allocation** y/y? | EVENT | Budget docs, Feb 1 | **Capex basket** (L&T, capital goods, Railways-PSU) vs NIFTY; Confirmation rotation | Consumption/FMCG over capex |
| Will the Budget give an **income-tax cut** that boosts consumption? | EVENT | Budget docs | **Consumption basket** (autos, durables, QSR, FMCG) long | Staples over discretionary |
| Will **Budget day be a big move** (>1.5%) either way? | RANGE-VOL (event) | NIFTY realized move | **NIFTY straddle** (weekly) pre-budget | Iron condor (priced-in move overstated) |
| Will **defence/railways get a capex bump** in the Budget? | EVENT | allocation lines | Long **HAL/BEL + RVNL/IRCON** basket into the read | *AVOID* (no clean short on a non-bump) |

---

### H. Elections & politics — *peak debatability* (A-tier, careful)

Maximum curiosity and disagreement, but **lumpy cadence + PROGA** (no outcome
*contracts*). Pivot trades the **market consequence and the event-vol**, never a bet on
who wins. Read PM odds only as a hidden prior.

| Question | Type | Resolves | YES → | NO → |
|---|---|---|---|---|
| Around a **big election result day**, will index volatility spike then crush? | RANGE-VOL (event) | NIFTY realized vol | **Pre-position long straddle → flip to short premium** post-result | (timing trade; no directional NO) |
| If a **capex-continuity government** is the result, will **PSE/Railways/Defence beat FMCG** over 3 months? | RELATIVE | PSE basket vs FMCG | Long **Nifty PSE / Railways basket** vs FMCG (pair) | FMCG/defensives over PSE |
| Will **state-election results** move the **capex/infra basket** in that state's beneficiaries? | EVENT | ECI counting day | Long state-capex basket, Confirmation | *AVOID* (state results rarely clean for markets) |

---

### I. Monsoon & rural — *India's oldest macro variable* (A-tier)

Genuinely India-first; IMD gives an **objective % of LPA**. (This is V1's monsoon view —
kept, but **reframed as a question with a NO side and a point-in-time basket.**)

| Question | Type | Resolves | YES → | NO → |
|---|---|---|---|---|
| Will this year's **monsoon be normal-or-better** (≥96% LPA)? | EVENT | IMD LRF + final rainfall | **Rural-demand basket** (tractors/2W/FMCG/fertiliser) vs NIFTY, Confirmation on IMD update | **Deficient** → long **irrigation/agri-input + staples-pricing-power**, underweight tractors |
| If rains are good, will **rural-skewed FMCG beat urban-discretionary** this season? | RELATIVE | rural vs urban basket | Long **rural FMCG (Dabur/Marico/HUL-rural)** vs urban-discretionary (pair) | Reverse on a weak monsoon |
| Will a **deficient monsoon push food CPI up**, lifting **fertiliser/irrigation** over tractors? | RELATIVE | Coromandel/Chambal vs M&M/Escorts | Long **agri-input** / short **tractors** (pair) | Long tractors / short agri-input |

---

### J. Festive & seasonal consumption — *Diwali, weddings, sales* (A/B-tier)

Highly relatable, calendared, one-sided demand surges.

| Question | Type | Resolves | YES → | NO → |
|---|---|---|---|---|
| Will the **festive quarter** see **discretionary beat staples** (autos/durables/jewellery)? | RELATIVE (seasonal) | discretionary vs staples basket | Long **discretionary basket** vs staples (pair), Pre-position pre-Diwali | Staples over discretionary in a weak festive |
| Into the **wedding season**, will **jewellery + consumer-durables** beat the market? | EVENT (seasonal) | basket vs NIFTY, window | Long **Titan + Voltas/Havells/Blue Star** basket | *AVOID* (seasonal demand one-sided) |
| Will the **auto-dispatch month** (festive) beat expectations and lift **auto stocks**? | EVENT | SIAM monthly dispatches | Long **Nifty Auto** basket, Confirmation on dispatch print | Underweight autos on a weak print |

---

### K. Earnings & single stocks — *the cadence engine* (A-tier)

The densest, most personal catalyst set — users already track specific stocks. Every
quarter refreshes the surface. Reuses the option engine + pairs + fundamentals DB.

| Question | Type | Resolves | YES → | NO → |
|---|---|---|---|---|
| Will **[RELIANCE/INFY/HDFCBANK/TCS] beat consensus** this quarter? | EVENT | results date | **Pre-earnings long straddle** (IV-crush-aware) on the F&O name | **IV-crush harvest:** iron fly/condor if you expect realized < priced |
| Will a stock **drift up after a beat** (PEAD), enter day +2, hold ~a quarter? | EVENT (drift) | results + 60d | Long the **post-beat name / debit vertical**, Confirmation timing | Long puts / put-spread on a post-miss name |
| Will **TCS beat Infosys** on margin commentary this results fortnight? | RELATIVE | pair, result window | **Long TCS / short INFY** (SSF pair) | Reverse |
| Will a **stock added to the Nifty 50** at the next rejig pop on passive flows? | EVENT | NSE rejig announce→effective | Long the **announced add** into the effective date | Short the **delete** only if SSF-shortable, else *AVOID* |
| Will **bank results season** lift the **PSU-bank basket** on improving NIMs? | RELATIVE / THEME | PSU bank basket vs NIFTY | Long **Nifty PSU Bank** basket vs NIFTY hedge | Private banks over PSU |

---

### L. IPOs & listings — *retail's favourite lottery* (A-tier)

Indians love IPOs and listing-day pops; Pivot already has the IPO feed
(register-not-execute, GMP off). Engagement is enormous; expression is event-shaped.

| Question | Type | Resolves | YES → | NO → |
|---|---|---|---|---|
| Will **[hot IPO] list at a premium** to its issue price? | EVENT | listing day | **IPO application** widget (register) + paper-track; post-listing, optional momentum add | *AVOID pre-listing short* (no instrument) |
| Will a **just-listed name hold above its listing price** for a month (anti-flip)? | EVENT (drift) | listing + 20d | Long post-listing on confirmation (once F&O/liquidity exists) | Avoid / wait for the lock-in unlock |
| Into a **big anchor/lock-in unlock**, will the stock underperform? | EVENT | lock-in expiry date | Long puts / put-spread if F&O-eligible | Long on absorption (contrarian) |

---

### M. Sector duels (RELATIVE) — *the "X vs Y" arguments* (B-tier)

"A beats B" is the most natural debate format and the **most professional, least
basket-y** expression (cointegrated pairs / ETF-vs-index). A whole rail of these keeps
the surface alive.

| Question | Type | YES → | NO → |
|---|---|---|---|
| Will **PSU banks beat private banks** over 6 months? | RELATIVE | Long **Nifty PSU Bank vs Private Bank** (ETF/SSF pair) | Reverse |
| Will **IT beat the Nifty** over 6 months? | RELATIVE | **Nifty IT vs NIFTY future** | Long NIFTY / underweight IT |
| Will **metals beat IT** over a quarter (cyclical rotation)? | RELATIVE | **Nifty Metal vs Nifty IT** pair | Reverse |
| Will **Reliance beat Infosys** over 3 months? | RELATIVE | **Long RELIANCE / short INFY** (SSF pair) | Reverse |
| Will **midcaps beat largecaps** this year? | RELATIVE | **MIDCPNIFTY vs NIFTY** | Largecap over midcap (de-risk) |
| Will **value beat momentum** over the next 6 months (factor rotation)? | RELATIVE | **Nifty Value 20 ETF vs Momentum 30 ETF** | Reverse |
| Will **pharma beat FMCG** as a defensive of choice over 6 months? | RELATIVE | **Nifty Pharma vs Nifty FMCG** pair | Reverse |

---

### N. Structural themes (THEME) — *the long, loud narratives* (B-tier)

Multi-quarter beliefs with confirm/invalidate checkpoints; built as **conviction-
weighted / factor-tilted / hedged baskets** (not equal-weight), or the listed **ETF
proxy** as the conservative tier.

| Question | Type | YES → | NO / invalidation → |
|---|---|---|---|
| Will the **defence supercycle** (Atmanirbhar order books) keep beating the Nifty over 12 months? | THEME | **Nifty India Defence ETF**, or conviction-weighted HAL/BEL/BDL/Mazagon basket + Nifty collar | Invalidate on capex cut; trim on order-book miss |
| Will **railways/PSU capex** keep outperforming over 12 months? | THEME | **Railways-PSU basket** (RVNL/IRCON/Titagarh) risk-parity weighted vs NIFTY | Invalidate on order slowdown |
| Will **EV & new-age auto** beat legacy ICE over 12 months? | THEME / RELATIVE | **EV-ecosystem basket vs Nifty Auto** (long-short) | Long ICE / underweight EV |
| Will **AI capex disrupt Indian IT-services** — product/ER&D beats services over 6 months? | RELATIVE (contrarian) | **Long Persistent/Coforge/LTIM vs short TCS/INFY** (pair) | Long services / short product |
| Will **green energy / solar** sustain a multi-quarter re-rating? | THEME | **Nifty Energy / renewables basket** (Adani Green/Suzlon/Tata Power), factor-tilted | Invalidate on policy/tariff reversal |
| Will **China+1 manufacturing/EMS** keep beating the Nifty over 12 months? | THEME | **Manufacturing ETF** or Dixon/Kaynes/Amber basket, capped | Invalidate on China price war |
| Will **real estate** keep outperforming on the housing upcycle over 12 months? | THEME | **Nifty Realty** basket, conviction-weighted | Invalidate on a rate-hike turn |
| Will the **financialisation** theme (AMCs/exchanges/depositories) re-rate over 12 months? | THEME | **BSE/CDSL/AMC basket** | Invalidate on SEBI fee/regulatory shock |
| Will **premiumisation** (premium FMCG/QSR/retail) beat mass-FMCG over 12 months? | RELATIVE / THEME | **Premium vs mass-FMCG** pair | Reverse on a rural-led recovery |

---

### O. Global → India transmission (C-tier)

People follow the global headline; Pivot trades the **Indian consequence**, with the
foreign leg only as an ETF proxy.

| Question | Type | YES → | NO → |
|---|---|---|---|
| Will a **dovish US Fed** lift Indian rate-sensitives + IT (FII inflows)? | EVENT / RELATIVE | Long **rate-sensitive + IT** basket (Fed = USD proxy); MON100 for the US-tech leg | Defensives if hawkish surprise |
| Will **US tech (Nasdaq-100) keep rising** this year — via the Indian proxy? | PRICE-TARGET (proxy) | Long **MON100** (never the foreign line) | MON100 put-spread / trim |
| Will **sustained net FII buying** lift large-cap financials over mid-caps for a month? | RELATIVE | NSE provisional FII + NSDL | Long **large-cap financials vs midcaps** | Reverse on FII selling |
| Will **US tariffs on Indian goods** hit exporters vs domestic-demand names? | EVENT | DGFT/US trade notices | Long **domestic-demand basket / short affected exporters** (pair) | *AVOID* if no tariff (non-event) |

---

### P. The engagement shelf — *huge interest, no clean retail trade* (be honest)

These maximise Curiosity × Debatability but **fail Gate 5**. Include them to drive
engagement, but **never fake a trade** — offer a flagged proxy or "watch only."

| Question | Why it engages | Honest Pivot stance |
|---|---|---|
| Will **India win the [World Cup / series]**? | Peak emotion | **No tradeable instrument.** Optional: sponsor/proxy *sentiment watch*, flagged "engagement only, not a trade." |
| Will **Bitcoin cross $150k** this year? | Very high | **No clean Indian-retail instrument** → watch-only; *never* imply a trade. |
| Will **the US strike [country]** within 3 months? | Extremely high | **YES** has expressions (long crude via MCX + defence basket + Nifty put hedge); **NO has no clean trade** — say so (asymmetric event). |
| Will **a specific celebrity company / Tesla-in-India** news hit? | High novelty | Trade only the **listed Indian beneficiary** if one exists (e.g. an auto-ancillary), else watch-only. |

---

## Part 4 — View-type → expression cheat-sheet

| View type | Natural expression archetype (India) | Default instrument |
|---|---|---|
| **EVENT** (RBI, Budget, earnings, monsoon, election day) | Pre-position debit spread / event-straddle → flip to defined-risk premium-sell post-print; surprise-conditioned | Index/stock **options** + Confirmation timing |
| **RELATIVE** (A beats B) | Cointegrated **pair** (SSF) / smart-beta-ETF-vs-index / sector-vs-index | **Pairs engine**, honest short = SSF/put |
| **PRICE-TARGET** (level by date) | Defined-risk **call-spread / put-spread**; ETF + staged adds | Index/commodity **options** |
| **RANGE-VOL** (calm vs crash) | **Iron condor / fly** (calm) ↔ **straddle/strangle** (move); protective put/collar | NIFTY/SENSEX **options** |
| **THEME** (multi-quarter) | Conviction-weighted / factor-tilted / hedged **basket**, or **ETF proxy**, with invalidation exit | **Basket engine** + collar |

---

## Part 5 — Prioritised V1 launch set (ship these first)

Maximise `Curiosity × Debatability × Tradeability × Resolution-clarity`, cover all five
view types, and keep the surface alive between rare events:

1. **RBI rate cut / "your EMI"** (EVENT + surprise) — the most personal macro view; cleanest event-vol. *[the category V1 skipped]*
2. **Gold vs Nifty / gold price target** (RELATIVE + PRICE-TARGET) — most Indian asset; directly tradeable. *[fills the price-target gap]*
3. **Nifty 30k / Sensex 1-lakh milestone** (PRICE-TARGET) — pure headline curiosity, most efficient trade.
4. **Crash-vs-calm / VIX** (RANGE-VOL) — the fear view; unlocks vol structures retail can't access. *[fills the volatility gap]*
5. **Crude / petrol regime** (RANGE-VOL + EVENT) — everyone feels it; two-sided + MCX-direct.
6. **Budget capex** (EVENT) — flagship annual, huge attention.
7. **Monsoon normal-or-better** (EVENT) — keep V1's view, **reframed as a question with a NO side + point-in-time basket**.
8. **Earnings beat / TCS-vs-Infosys** (EVENT + RELATIVE) — the weekly cadence engine.
9. **Defence supercycle** (THEME) — loudest structural narrative; ETF-proxy conservative tier.
10. **PSU vs private banks** (RELATIVE) — the cleanest "X vs Y" pair.
11. **Rupee → exporters vs importers** (PRICE-TARGET proxy) — the ticker everyone watches, honestly proxied.
12. **IPO listing-day** (EVENT) — retail's favourite lottery; reuse the IPO feed.

**Why this set beats what shipped:** it leads with the **S-tier personal/loud/clean-to-
trade** views (rates, gold, index levels, volatility, crude), covers **all five view
types** (V1 had only theme/relative), restores the **YES/NO + surprise + two-sided**
shape the philosophy demands, and keeps every expression on the **honest ladder**
(index option → pair → ETF-vs-index → optimised basket), with naked index-ETF shorts
and fake option payoffs explicitly designed out.

---

### Cross-cutting build notes (so the bank stays honest)

- **Phrase every view as a question with a defined NO** — and mark *AVOID* where the NO
  has no clean trade (asymmetric events). Never force an inverse.
- **Surface the surprise** — for EVENT views, show Expected vs User-View vs Difference
  using the option-implied expected move; P&L is realized − priced-in.
- **Two confidence dials** — outcome (will it resolve?) vs expression (will the trade
  pay?) — never collapsed into one.
- **Honest instruments only** — index short = put/future (never short an ETF);
  single-name short = SSF/put; options tiers must show the **real capped/decayed payoff**,
  not the underlying's path.
- **Score is reference, not a forecast** — every number traces to a real backtest/tool
  value, deflated for trial count, suppressed below MinTRL; ends "analysis, not advice."
