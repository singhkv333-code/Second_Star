# Opinion Markets — Quant Research Log
**Session date:** 2026-07-06 / 2026-07-07 · Pivot / Opinion Markets (formerly View Markets)

This file is the single reference for everything tested in this research session:
the 14-opinion fund playbook (statistically rigorous), the 60-item "vivid opinions"
sweep (deliberately simple, historical-precedent testing), and the 12 one-off
historical-event mechanism tests. Read this before re-deriving any of it.

---

## Part A — The 14-opinion fund playbook (rigorous track)

**Deliverable:** self-contained HTML artifact, also copied to `~/Downloads/opinion-markets-quant-playbook.html`.
**Build pipeline:** this folder — `download_data.py` → `run_analysis.py` → `run_extras.py` →
`run_cpi.py` → `india_wide_screens.py` (full-tape screens) → `content_a.py`/`content_b.py`
(the 14 opinion write-ups) → `pi.json` (web-verified "what's priced in" facts) →
`build_v2.py` (assembles final HTML).

**Universe:** India = full NSE tape, 2,209 equities (`india_wide.pkl`, sourced from
`pivot/scripts/strategy_research/v3/_cache/close_*.parquet`, 2010→2026-07-02) +
NIFTY-500 sector tags. US = all 503 S&P 500 constituents + 75 diversified ETFs
(sectors/factors/bonds/countries/REITs/inverse) + 11 commodity futures
(`us_wide.pkl`, 2013→2026-07-06, no crypto in this universe by design).
yfinance is the source (tagged "(yfinance, EOD)" per the Kite-primary data contract);
cross-checked against Alpaca IEX bars, max divergence 0.16%.

**Methodology (the "8 rules" in the artifact's methodology section):**
1. Market-implied expectations, not raw outcomes — every event labeled above/below/in-line vs consensus.
2. Ask what was DONE, not observed (Pearl's do-operator) — cause-check every trade (2013 vs 2022 INR/IT reversal).
3. Short windows for scheduled events; matched replays (not factor models) for long-window/thematic claims.
4. One event = one observation (avoid pseudo-replication); India-calibrated tests, not US defaults.
5. Never-seen-before events get target-trial replays (protocol frozen before looking), not regressions.
6. Event is the decision unit — typed, dated, with its own persisted reaction history.
7. Slow diffusion / consensus-is-fragile (Brunnermeier, Veldkamp) — confirmation entries still capture edge; flag "priced in" >80% as fragile.
8. Ticket sizes are real (₹700–2,000 India, fractional US); size by relative substitution, not isolation.

Twelve external papers were read and mapped to these rules (Goldsmith-Pinkham & Lyu 2025,
El Ghoul et al. 2022, Haddad/He/Huebner/Kondor/Loualiche 2025, Ding et al. 2015/16,
"Trade the Event" 2021, Tang et al. 2024 causal discovery, Janus-Q 2026, Brunnermeier,
Veldkamp 2011, Pearl *Causality*, Hernán & Robins *What If*). Engine roadmap (not yet built):
synthetic-control replicating portfolios, a causal-discovery-validated transmission DAG,
a persisted per-occurrence event→CAR data model.

**World-state facts baked into the doc (post-training-cutoff, web-verified — reuse without re-deriving):**
- A SECOND, larger Iran war ran Feb 28 – Jun 17 2026 (Khamenei killed, Hormuz closed Mar 27,
  Brent $72→$120→ fully round-tripped to ~$71.5 by Jul). Ceasefire memorandum Jun 17, 60-day framework, holding.
- Gold ATH Jan 29 2026 ($5,318 futures / ~$5,590 spot) then −22% crash by July; **failed as a war hedge**
  this cycle (peaked day 1 of the war, then fell through it).
- USD/INR ATH ₹96.84 (May 20 2026, war-driven), spot ~₹95.4 in July.
- RBI repo 5.25% (held all H1 2026); Fed 3.50–3.75% (~80% priced zero cuts in 2026).
- Monsoon 2026 is DEFICIENT (IMD 90% LPA, June −40% vs normal, driest June since 1901).
- US-India tariffs: peak 50% (Aug 2025) → ~18-27% (Feb 2026 interim framework); Section-122
  tranche expires ~Jul 24 2026 (unresolved binary at time of writing).
- Nifty PE ~20 vs 23.4 ten-year median (cheap after lagging S&P: Nifty −3% vs S&P +9% through the war).
- Arena/LLM leaderboard mid-2026: Claude Opus 4.8 #1; hyperscaler 2026 capex ~$725bn (+77% YoY).

**Strongest tested edges (event-study, alpha vs market, cite don't recompute):**
- RBI dovish surprise (n=9): NBFC day0-1 +1.9% (t=2.4), realty 1mo +3.4% (t=2.3); banks NEGATIVE — excluded.
- Oil shock (n=5, 5/5 hit both windows): India defence t=4.8-5.0, US defence +3.0% d0-1 (t=3.0),
  OMCs −2.1% d0-1 (t=−4.1, the anti-trade).
- Hot CPI print (n=4): Nifty UP 4/4 by day 5 (fear sold BEFORE the print); gold FELL morning after
  every hot print (0/4) — folk wisdom backwards on both counts.
- Tariff escalations (n=4): textiles −3.8%/5d; one relief event: +4.6%/5d.
- Recession payout (raw, if belief true): SH +92.6%/+42.3%/+28.7% across GFC/COVID/2022; TLT FAILS
  in inflation-recessions (−29.3% in 2022) — pair with SH/PSQ, not bonds, when inflation is the cause.
- **Uniform 1-month (21-trading-day) raw returns**, all 42 strategies, computed and reported —
  several strategies that looked strong on 5-day/multi-month windows are flat-to-negative on
  a strict 1-month clock (OP2 Balanced −5.9%, OP4 Balanced −5.1%, OP9 Conservative −6.8%,
  OP14 Aggressive −1.5%) — each has its own correct holding period, forcing uniformity understates them.
- **Beta-verified integration:** 12 "biggest historical gainer" candidates were beta-tested against
  each opinion's own existing basket (regression of candidate's per-event return on basket's
  per-event return, same dates). Only **Elecon Engineering (OP7, Nifty drawdown)** cleared both
  bars (t=2.39, beta=1.26, plausible mechanism: high-beta smallcap industrial overshoots on
  recovery) — integrated into OP7 Aggressive tier, boosting 40-event tested return from +2.0%→+4.7%.
  Rejected despite numerically passing: TPL (n=4, no mechanism), Block Inc/XYZ (n=5, no mechanism),
  Netweb (numerically unstable beta).
- **Options torque tier** (event-replayed against REAL underlying paths, Black-Scholes premium
  modelled at realized-vol×1.25): Monsoon Thunder (M&M 2mo call, 10% OTM, on IMD above-normal
  forecast) — 8.1× average, 3/3 profitable, ticket ~₹8-13k. Snapback Spread (LICHSGFIN call
  spread on hot-CPI fade) — 1.69× average, 3/4 profitable. RBI/oil/ceasefire option structures
  were TESTED AND REJECTED (0.3-1.2× — the underlying move is real but too small for option pricing).
- **Data-quality bugs caught and fixed** (reusable warning for any future pull from these
  cached parquets or fresh yfinance pulls): GOLDBEES/NIFTYBEES had a 2-day bad-tick freeze at
  ₹0.34 on 2019-12-19/20 (yfinance provider glitch, not a real move) — corrupted early raw-return
  scans until caught via 5-day-median local-outlier filter. TMPV.NS had an unadjusted bonus/split
  causing a false −42% "return" on 2025-10-14 (permanent step, not reverting — the median-ratio
  filter catches revert-type glitches but NOT permanent unadjusted corporate actions; check for
  these separately). GOODYEAR.NS/ARIHANT.NS in the full 2,209-name tape showed +2,800%/+2,189%
  "gains" from the same class of bug — excluded via a stricter per-name suspect-flag (any single-day
  |return|>70% that never reverts). **Lesson: any full-tape scan MUST run both the revert-detector
  AND the permanent-step detector before ranking, or the "biggest gainer" will be a data artifact.**

---

## Part B — The 60 "vivid opinions" (simple track — no stat gatekeeping, per user instruction)

Full list delivered as `fifty_opinions.md` (items 1-50) + 10 more added later (51-60, "different
domains than finance": sports/space/health/law/education/food-policy/gaming/religion/awards).
Universe for this track: crypto (BTC/ETH/DOGE/SOL/XRP) + event-proxy equities, fetched fresh
(`download_crypto_proxies.py` → `proxies50.pkl`; `extra50.pkl`, `extra50b.pkl`, `extra50c.pkl`
for later additions). **Deliberately NOT the same rigor as Part A** — per user: "keep the logic
simple," just find historically who gained most after the event, 1 month later, raw return.

### Methodology note (answers "are you doing it properly")
Two different failure modes surfaced and were corrected mid-session:
1. **Wrong dates from memory** — my first-pass Elon/Dogecoin dates and the "wheat ban May 2024"
   claim were both wrong; a research agent (WebSearch) verified real dates against primary
   sources. ALWAYS verify event dates before testing; recalled dates are unreliable.
2. **Full-tape scan on n=1 events surfaces pure noise, not mechanism** — proven directly:
   scanning all 503 S&P names for the "biggest gainer" after SVB's collapse, Altman's firing,
   and the Pfizer vaccine news mostly returned coincidental high-beta movers unrelated to the
   event (Carvana, AppLovin, Lululemon, Robinhood — no causal story). Only the mechanistically
   sound picks (gold miners on SVB flight-to-safety; energy complex on the vaccine "reopening
   trade") survive scrutiny. **For one-off (n=1) historical events, reason about the mechanism
   FIRST, then check that specific candidate — do NOT trust a blind full-scan winner.** For
   repeatable events (n≥4), the full-scan approach (Part A) is legitimate rigor.

### Verdict table — all 60 vivid opinions + 12 extra one-off historical events

**✅ Real, tested, tradeable mechanism (11):**
| # | Opinion | Biggest gainer / mechanism | 1mo result |
|---|---|---|---|
| 33 | Elon tweets bullish on Dogecoin (6 real dates: Dec'20, Jan'28/Feb'04/May'08-2021, Dec'21, Oct'24) | RIOT (BTC miner) | +80.4% avg, hit 67%, best +211% |
| — | MicroStrategy/Saylor announces big BTC buy (3 dates 2020-2024) | COIN/RIOT/MARA | +41-51% avg, hit 67-100% |
| 16 | Trump bans TikTok (4 real dates 2020-2025) | SNAP | +10.5% avg, hit 75%, best +46% |
| 43 | Adani announces new port/energy project abroad (3 real 2024-25 dates) | ADANIPORTS itself | +9.7% avg, hit 100% |
| — | Binance/CZ hit by US regulator (2023-11-21) | COIN (rival benefits) | +32.4% (n=1) |
| 4 | Category-5 hurricane US landfall (Michael'18/Ian'22/Milton'24) | Travelers (insurer) | +6.5% avg, hit 67% — homebuilders/HD actually FELL |
| 11 | North Korea nuclear test (2017-09-03, the real 6th test) | Northrop Grumman, GD, LMT | +2-5% (n=1, sensible defense mechanism) |
| — | Credit Suisse rescued by UBS (2023-03-19) | UBS | +13.7% (n=1) |
| — | Boeing 737 MAX door-plug blowout (2024-01-05) | Airbus (EADSY) | +7.2% (n=1) |
| — | Israel-Hamas war begins (2023-10-09) | RTX, LMT, GD | +1.8-13.3% (n=1) |
| 54 | WHO declares new PHEIC (mpox, 2024-08-14) | Emergent BioSolutions | +38.6% in 5 days, faded to +4.0% by 1mo — SHORT-WINDOW trade only |
| 55 | Court rules against Big Tech (Google, District Court not SCOTUS: Aug'24/Apr'25/Sep'25 rulings) | GOOGL — counter-intuitive RELIEF RALLY on "guilty" verdicts that avoided breakup | −1.8%/+10.2%/+16.0% |

**⚠️ Real event, thin/mixed signal:**
- BTC fresh-ATH day (13 occurrences) → ETH gains MORE than BTC itself after BTC's own ATHs (+32.5% vs +21.2%, hit 83%/67%)
- Trump's "Strategic Bitcoin Reserve" — clean buy-rumor/sell-news split (pre-announcement pop, EO-day selloff, both n=1)
- NVDA fresh-ATH day (26 occurrences) → NVDA itself +3.8% avg, hit 73% — modest, real, unspectacular
- India GST cut on autos (2025-09-22) → SONACOMS +9.8% (n=1); (TMPV result INVALID — see data-bug note above)
- 2025 India real-money-gaming ban → Nazara −8.4%, Delta Corp −7.9% (n=1) — this is an AVOID/short
  signal, not a beneficiary trade; NOTE this is a different event than "BGMI-style ban" (#58) despite
  surface similarity — don't conflate them.
- Japan earthquake (2011/2024) → TSM +8.7%, EWJ +4.0% (only 1 of 2 dates had usable data)
- Taiwan tension spike (Pelosi visit 2022-08-02) → small defense-stock moves only (GD +3.1%, TSM ~flat)

**❌ Confirmed dead ends / mechanism hypothesis DISPROVEN by data (test the reasoning, don't trust it blind):**
- First Republic collapses, JPM "wins" the FDIC deal → JPM ACTUALLY FELL −3.9% (sector-wide contagion fear overrode the "good deal" thesis)
- UK Truss mini-budget crisis → UK equities (EWU) ROSE +3.8%, not fell (GBP-weakness helps multinational earnings; crisis reversed fast)
- US chip export controls to China (2022-10-07) → NVDA rose +18.4% — but this date landed at the 2022 bear-market bottom; almost certainly CONFOUNDED by the broad market rally, not causal
- Suez Canal blockage (2021-03-23) → tanker stocks barely moved (FRO +1.1%, STNG −0.9%) — cleared too fast to matter
- GameStop squeeze → entry AT the peak (Jan 27 2021) = −60 to −71% next month; entry 2 WEEKS BEFORE
  the peak = +67 to +156% — pure entry-timing artifact, not a repeatable strategy (no real-time
  signal existed to know Jan 13 was "before" the mania)
- FTX collapse → Coinbase fell −32.7% (contagion, no "flight to survivor" trade materialized)
- Taylor Swift tour (Eras Tour precedent) → no clean stock-moving date found by either research pass; Live Nation/Disney/Netflix all flat-to-negative
- Tata Group defense/semiconductor contract wins → real events (C295 Vadodara Oct 2024, Dholera fab)
  but the entities (Tata Advanced Systems, Tata Electronics) are UNLISTED; listed-Tata-basket halo
  effect tested and found NOTHING reliable (n=2, mixed signs)
- India Budget day tax changes (2024 capital-gains hike, 2025 income-tax relief) → n=1 each,
  reactions look idiosyncratic/noisy (Voltas +6.5% in 2025 while everything else including the
  "relevant" names was negative) — not a repeatable pattern
- India bans Chinese apps (2020-06-29) → Reliance +26.9% BUT almost certainly CONFOUNDED by the
  simultaneous, much bigger Jio Platforms stake-sale news cycle (Facebook/Google investments) —
  wrong causal attribution, don't use
- Onion/wheat/rice export bans — real, dated (onion duty Aug'23/ban Dec'23/lifted May'24; wheat
  ban 2022-05-13 NOT 2024; rice ban Jul'23, lifted Sep-Oct'24) — but NO clean single LISTED
  security found by either research pass (commodity trade is mostly unbranded/unlisted)
- Vedanta demerger approval → my test date (2024-11-11) was an UNVERIFIED GUESS, never
  agent-confirmed — treat the +9.7% result as unreliable until the real date is confirmed
- Jio satellite internet announcement → weak (+2.1%, n=1), date also unverified

**No historical precedent exists at all (18 items — hasn't happened / structurally impossible / no listed proxy):**
Cricket World Cup win (#51), Olympic athletics gold (#52), NEET scrap/reform (#56, ed-tech mostly
unlisted), Maha Kumbh record (#59, no clean attribution), Oscar win for Indian film (#60, producer
DVV unlisted), Modi Cabinet reshuffle (#9, doesn't move markets), UK rejoins EU customs union (#10),
UNSC Russia sanctions (#15, Russia has veto — structurally can't pass), OpenAI IPO (#18), Apple
own frontier LLM (#19), AI antitrust breakup order (#22, duplicate of #55 — no breakup has happened),
humanoid robot mass production (#23), Marvel/DC $2B movie (#26, never happened), Netflix acquires
studio (#27), K-pop India concert (#28), Disney India park (#29), Indian film $100M US B.O. (#30),
Hollywood star political campaign (#31, no recent instance), streaming price hike India (#32,
no India-listed platform), X crypto payments (#38), Trump's own crypto venture $10B (#39, WLFI
exists but no reliable price data source), Gaganyaan crewed launch (#53, milestone dates known —
TV-D1 Oct 21 2023 — but no documented HAL/BEL/L&T stock reaction to any past ISRO milestone),
RBI digital-rupee pilot (#47, real launch dates Nov/Dec 2022 but any bank-stock correlation is
almost certainly coincidental with that quarter's broader bank rally, not causal), E20 ethanol
engine damage (#50, no dated event found at all), Antarctic sea ice record (#5), volcanic eruption
disrupts EU flights (#6, Eyjafjallajökull 2010 — no price data available that far back for the
airline tickers on hand), India monsoon onset delay (#3, redundant with Part A's monsoon work),
income tax exemption limit (#48, duplicate of the Budget-2025 test above), EU carbon border tax
on Indian steel (#13, real CBAM start date Oct 1 2023 — Tata Steel/JSW Steel both FELL −5.5%/−7.9%,
so if anything this is a HEDGE/avoid signal not a gainer story), Pakistan sovereign default (#14,
never actually happened — tested Sri Lanka's real 2022 default as the nearest analog, India
market reaction was weak/thin +2.8% n=1, no real spillover mechanism found).

---

## Bottom-line shortlist (what's actually strong enough to build a strategy card from)

From the 60+12 vivid-opinion sweep, ranked by (mechanism soundness × sample size × real return):
1. **MicroStrategy/Saylor BTC buy → COIN/RIOT/MARA** (n=3, 100% hit, clean mechanism)
2. **Elon bullish-Dogecoin tweets → RIOT/SOL** (n=6, real dates, +61-80% avg)
3. **Adani new-project announcement → ADANIPORTS itself** (n=3, 100% hit, cleanest of all — company IS the trade)
4. **Trump bans a foreign app → SNAP** (n=4, 75% hit, proven TikTok-ban mechanism)
5. **Regulator hits a crypto exchange → the surviving listed rival (COIN)** (n=1 but very clean logic, repeatable in spirit)
6. **Hurricane Cat-5 landfall → insurance names (TRV)**, NOT homebuilders (n=3, 67% hit)
7. Options torque: **Monsoon Thunder (M&M calls)** — 8.1× avg, 3/3, from Part A — already integrated in the fund artifact

Everything else needs either (a) a real research-agent-verified date before it can be trusted, or
(b) more occurrences before the "biggest gainer" is more than a coincidence.

---

## File index (all in this folder — `.../scratchpad/quant/`)
- **Data:** `prices.pkl`/`prices_clean.pkl` (macro+ETF, cleaned), `india_wide.pkl` (2,209 NSE),
  `us_wide.pkl` (503 S&P+75 ETF+11 commod), `proxies50.pkl`+`extra50*.pkl` (crypto/vivid-opinion proxies)
- **Fund playbook build:** `download_data.py`, `run_analysis.py`, `run_extras.py`, `run_cpi.py`,
  `india_wide_screens.py`, `lib.py` (event-study/beta/stress-test library), `events.py` (event date lists),
  `pi.json` (priced-in facts), `content_a.py`/`content_b.py` (14 opinion write-ups), `build_common.py`
  (CSS/HTML helpers), `build_v2.py` (assembles the final HTML) → **`opinion-markets-quant-playbook.html`**
  (also at `~/Downloads/opinion-markets-quant-playbook.html`)
- **Options torque:** `options_torque.py` → `torque.json`
- **Biggest-gainer / vivid-opinion testing:** `biggest_gainers.py`, `download_crypto_proxies.py`,
  `raw_returns.py`, `fifty_opinions.md` (the 50-item list)
- **Screens:** `india_wide_screens.json`, `india_n500_screens.json`, `us_wide_screens.json`
