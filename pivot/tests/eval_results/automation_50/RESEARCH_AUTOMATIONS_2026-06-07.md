# Pivot Automation Research — Merged Ranked Taxonomy

**Purpose:** A single deduplicated, demand-ranked taxonomy of the automation types Indian retail
investors most commonly want, synthesized from 5 research angles (global retail, India-specific,
consumer automation patterns, F&O/options, event-driven).

**Ranking method:** Rank is driven by popularity evidence (how often real people request each
pattern across broker feature pages, alert/screener apps, no-code platforms, and community
forums). India relevance is the strong tiebreaker, since Pivot serves Indian retail. Weights
reflect *real-world demand*, not what Pivot currently supports. Weights sum to 100.

**Key cross-angle convergence:** Four findings recur in every angle and dominate the top:
(1) the price-level "ping me when it hits X" trigger is the universal #1 first automation;
(2) target+stop-loss / GTT-OCO is the universal #1 risk automation and maps 1:1 to Pivot's
register-not-execute model; (3) recurring buy / SIP is the highest-*volume* retail automation in
India by far; (4) event triggers (results, RBI, IPO, expiry) form a large, under-served cluster
that is Pivot's clearest differentiator.

---

## 1. Ranked Taxonomy (14 entries)

### Rank 1 — Price-level alert / target / stop-loss trigger (GTT-style, register-not-execute) — **weight 16%**

**What users want:** Be pinged (or have an order drafted) the moment a stock/index hits a price
they care about — a buy level below current, a sell/target above, or a stop-loss to cap a loss —
without staring at the screen. Often two legs at once (OCO: target + stop, whichever hits first),
valid for months. They decide and confirm in their broker.

**Evidence summary:** Universally the #1 alert type *and* the #1 risk feature, appearing in all 5
angles. TradingView docs call price alerts the simplest/most common type; IFTTT's entire finance
hub is built on stock-drop/rise threshold applets (9+ variants). Zerodha Kite alerts are free
(capped 500/account — implying power-user demand) and Zerodha GTT (free, 1-year validity, OCO) is
one of the most-used Indian order types, explicitly marketed for "investors who don't have time to
track markets daily." Every major broker (Schwab, Fidelity, IBKR, Robinhood) front-pages
stop-loss/take-profit/OCO. *(Merges global "Price-level alert" + "Stop-loss/take-profit",
India "Price-target & stop-loss GTT trigger", consumer "Price-drop alert" + "Conditional
if-this-then-buy/sell".)*

**Example phrasings:** "ping me if RELIANCE drops to 1250"; "set a stoploss at 3200 and target
3800 on my TCS, whichever hits first"; "alert me + place a GTT to buy HDFC Bank at 1450".

**India notes:** GTT-OCO is the most popular GTT pattern in India and a uniquely prominent Indian
primitive (exchanges clear order books end-of-day). Register-not-execute maps 1:1: the app drafts
the trigger, user confirms in broker — fully inside SEBI's compliant zone.

---

### Rank 2 — Recurring buy / SIP / DCA (schedule → invest; stocks, ETFs, gold/silver) — **weight 13%**

**What users want:** Invest a fixed rupee amount on a schedule (weekly/monthly, often salary day)
into the same stock, ETF, index fund, or gold — set once, forget, no market timing. The mutual-fund
SIP habit extended to single stocks and gold/silver.

**Evidence summary:** The highest-*volume* retail automation in India and a top consumer-automation
shape globally. SIP is a cultural default (≈1 in 3 new SIPs in India created on Groww per its 2025
RHP; Zerodha Coin offers commission-free MF SIPs). Stock SIP is now first-class on Zerodha, Groww,
Upstox, ICICI Direct; gold/silver SIP starts at ₹100/mo; smallcase offers basket SIPs. UPI Autopay
(~1 billion recurring txns/month, projected 70%+ of recurring payments incl. SIPs by 2026) makes
scheduled money automation culturally native. IBKR/Schwab/M1 all promote recurring buys. *(Merges
global "Recurring buy/DCA/SIP", India "Stock/ETF/gold SIP", consumer "Recurring scheduled buy".)*

**Example phrasings:** "buy 5000 of niftybees every month on the 1st"; "start a weekly SIP of 2000
in this stock"; "put 1000 into gold every month for me".

**India notes:** Arguably the single biggest retail-investing behaviour in India and the
automation ordinary Indians most readily understand. Schedule trigger → register order / paper-buy
maps perfectly.

---

### Rank 3 — Technical-indicator alert (RSI / EMA crossover / MACD / SuperTrend) — **weight 9%**

**What users want:** Notify or draft an order when an indicator condition fires — RSI crosses
30/70, 50 EMA crosses 200 EMA (golden/death cross), MACD turn — optionally chaining a couple of
conditions ("RSI < 30 AND above 50 EMA"), without writing code.

**Evidence summary:** TradingView's entire alert ecosystem and a huge community-script library are
built on this; it appears in 4 of 5 angles. Zerodha Sentinel adds indicator alerts; Streak
(Zerodha's no-code arm) supports 100+ indicators, chains up to 5 conditions, and its
most-deployed templates are RSI/EMA/VWAP setups. RSI and MA crossovers are the most-taught
indicators in Indian retail content. *(Merges global "Technical-indicator alert", India "RSI/EMA
indicator-crossover alert", consumer "Technical-indicator trigger".)*

**Example phrasings:** "alert me when RSI goes below 30 on HDFC Bank"; "tell me when the 50 day
crosses above the 200 day"; "ping if MACD crosses up on Nifty".

**India notes:** Streak + Sentinel prove strong Indian demand; chat-native phrasing removes even
Streak's mild learning curve. Notify-only is fully SEBI-compliant. Pivot already supports RSI/EMA
triggers.

---

### Rank 4 — Event trigger: earnings / results-day (reminder + result-out + reaction) — **weight 8%**

**What users want:** Be reminded 1–2 days before a held stock reports, be pinged the moment results
actually file on BSE/NSE (ideally with headline numbers + concall summary), and optionally have a
hedge/exit/buy-the-dip order drafted on the reaction.

**Evidence summary:** Strong, repeat, under-served demand across the global and event-driven angles.
Dedicated products: Earnings Reminder/Alert apps, Stock Alarm upcoming-earnings, Wall Street
Horizon, Zacks/Seeking Alpha. India-specific: NiftyTrader Results Calendar, earnings.thecore.in
(1000+ companies), EquiSense/myAlerts concall summaries, Trendlyne result alerts. SEBI mandates
results within 45 days; dates drop 7–10 days prior; mid-cap post-results moves average 5–8%.
*(Merges global "Event-triggered automation (earnings)", India "Results-day event trigger",
event-driven "Results-out alert" + "Upcoming-results reminder" + "Post-results reaction trade".)*

**Example phrasings:** "remind me 2 days before INFY results"; "tell me as soon as TCS declares
results, with the numbers"; "buy the dip if a good company falls 8% after results".

**India notes:** Q1 results season (Jul–Aug, starting with TCS) is a fixed ritual. Concall
summaries are an India-specific value-add. Pivot already supports earnings event triggers and can
both detect the event AND draft a register-not-execute order in the same chat turn.

---

### Rank 5 — Options: IV / VIX-rank premium-selling timing + strategy build (straddle / strangle / iron condor) — **weight 8%**

**What users want:** Enter premium-selling structures only when IV/IV-rank/India-VIX is high
(e.g. IV rank > 50, VIX > 18) to sell rich premium and ride the IV crush; buy options when IV is
cheap. They want a card that reads IV, suggests/builds the right multi-leg strategy for the regime,
and alerts when IV crosses a threshold. Includes the "wheel" (covered calls / cash-secured puts).

**Evidence summary:** Big global *and* Indian options behaviour. Global traders gate strategy on
IV-rank thresholds (>50 premium-selling, >70 condors, <20 long options); thinkorswim TTIV scans
are standard. Sensibull (India's largest options platform, "10 lakh+ traders") is built around
IV-percentile-driven Iron Condor vs straddle selection, 25+ pre-built strategies, WhatsApp P&L
alerts. India rule of thumb: "sell premium above VIX 18, buy below 13." *(Merges global "Options:
IV-rank trigger & premium-selling", India "IV-based option strategy timing", F&O "IV/IV-rank/VIX
premium-selling entry".)*

**Example phrasings:** "alert me when IV rank goes above 50 to sell a call"; "is Nifty IV cheap
right now, should I buy a straddle"; "build me an iron condor since premiums are rich".

**India notes:** Indian index-options retail is among the largest in the world. Pivot already
supports IV triggers + multi-leg suggest/build/critique tools — directly validated.

---

### Rank 6 — Expiry-day option automation: 9:20 short straddle / 0DTE theta (schedule/event → multi-leg) — **weight 7%**

**What users want:** The weekly-expiry workhorse. At a fixed time (classically 9:20 AM) sell the
ATM CE+PE on Nifty/Bank Nifty with a per-leg stop and a hard exit by ~3:10 PM; or on expiry day
enter a defined-risk spread / far-OTM sell to scalp 0DTE theta. User just states index, SL %,
exit time; strikes auto-selected off spot.

**Evidence summary:** The most distinctive India-specific F&O demand. The "9:20 straddle went
viral across social media"; it is the default first template on every Indian algo platform
(AlgoTest, StockMock, Tradetron, Streak, SquareOff). Sensibull launched a dedicated "Expiry
Trades" feature; expiry-day premium "loses 70–80% between 1 PM and 3 PM." *(Merges F&O "9:20 short
straddle" + "Expiry-day 0DTE automation"; relates to global/India expiry-day triggers.)*

**Example phrasings:** "sell Nifty ATM straddle at 9:20, 25% SL each leg, square off 3:10";
"every weekly expiry do a credit spread, exit by 3"; "paper trade an expiry-day straddle sell".

**India notes:** Weekly Tue (Nifty) / prior Thu expiry cadence makes near-daily 0DTE the busiest
retail F&O window globally. Pivot has an expiry-day trigger + multi-leg paper fills — squarely
on-target; paper/register keeps it compliant.

---

### Rank 7 — Breakout / 52-week high-low / volume-spike scanner alert — **weight 7%**

**What users want:** A standing scan across many stocks that pings when something breaks out — new
52-week high/low, crosses 200 DMA, golden cross, or volume > 2x average — so they discover
candidates without re-scanning manually.

**Evidence summary:** Appears in 4 of 5 angles. Large TradingView script ecosystem; thinkorswim
distinguishes a manual scan from a scan-to-alert. India: Chartink is "one of the most popular
beginner-friendly screeners," Streak publishes a "52 Week High Breakout" scanner, Trendlyne offers
free SMA-crossover alerts, NSE publishes a live 52-week high/low page, ICICIdirect offers 52-week
alerts, PKScreener sends daily Telegram breakout alerts. *(Merges global "Breakout/52wk/volume
alert" + "Screener/scan-based alert", India "52-week high/breakout/golden-cross scanner",
event-driven "52-week high/low breakout".)*

**Example phrasings:** "alert me when any Nifty50 stock makes a new 52-week high"; "scan for
stocks where volume is more than double the average today"; "tell me if RELIANCE does a golden
cross".

**India notes:** Chartink-style scans are a national pastime among Indian swing/momentum traders.
Combine price-level + volume + criteria triggers → notify with a draft basket; stays
notify/register-only.

---

### Rank 8 — IPO lifecycle automation: apply / allotment / listing-day (GMP-aware) — **weight 6%**

**What users want:** Track upcoming IPOs — remind on the last day to apply, on allotment day, and
at listing — watch grey-market premium (GMP) as sentiment, and optionally draft a "sell on listing"
or "buy if it lists below issue price" plan.

**Evidence summary:** A whole cottage industry exists purely for this Indian-retail obsession:
IPO Watch, ipopremium.in, mainboardgmp.com, IPO Central, IPO Notify (WhatsApp alerts), Groww's IPO
GMP page, broker allotment-status pages. Tracks QIB/HNI/RII subscription, allotment (T+6), listing.
*(Merges India "IPO listing-day & application reminder", event-driven "IPO open/close/allotment/
listing alert", global/consumer event triggers for IPO listing.)*

**Example phrasings:** "remind me before the last day to apply for the Swiggy IPO"; "tell me when
my allotment status is out"; "alert me at listing, I want to sell if it pops".

**India notes:** Distinctly Indian retail mania (record demat openings, SME IPO frenzy). Pivot
already has a chat-native IPO widget + listing event trigger; register-not-execute with GMP shown
as info only.

---

### Rank 9 — Trailing stop-loss / OCO / multi-target trade planner (dynamic exit) — **weight 6%**

**What users want:** Plan a full swing trade up front: entry, a stop that ratchets up with the
price (never down), multiple partial profit targets, and OCO so a target auto-cancels the stop —
so the exit manages itself dynamically instead of manual SL edits.

**Evidence summary:** Heavily requested and a genuine *unmet* gap. Robinhood launched trailing
stops by user demand; IBKR lists them as core. Critically, Indian GTT was static — trailing SL is
described by Zerodha as "one of the most requested features," with long TradingQnA threads
(81994, 176736, 194095) and an "Advanced Trade Planner" feature request (194416) a Zerodha rep
confirmed aligns with roadmap. *(Merges global "Trailing stop-loss", India "Trailing SL / OCO /
multi-target trade planner".)*

**Example phrasings:** "trail my stop 10% below the high"; "sell half at 1500, rest at 1600, move
my stop to breakeven"; "if target hits cancel my stoploss, OCO style".

**India notes:** An explicit, repeatedly-voiced gap the largest broker admits it hasn't fully met.
Pivot can simulate via a high-water-mark recalculating trigger → alert/register exit — a strong
differentiator for active Indian traders.

---

### Rank 10 — Macro-event trigger: RBI MPC / Fed / Union Budget (notify + position) — **weight 5%**

**What users want:** Be alerted around scheduled macro events — RBI repo decision, FOMC outcome,
Union Budget — and optionally act on rate-sensitive baskets (banks, NBFCs, autos, realty) or
de-risk before the event.

**Evidence summary:** Appears across global, India, consumer, and event-driven angles. RBI MPC
meets ~6×/year on fixed dates with a 10 AM announcement (cleanly automatable); India tracks it
closely for rate-sensitive stocks. FOMC is covered as a key India event (drives FII flows /
next-day Nifty open). Union Budget is a single-day high-volatility sector-rotation catalyst
(STT-hike line moved Nifty ~2%). Investing.com lets users create economic-event alerts with
15/30/60-min pre-reminders. *(Merges global "Event-triggered (central-bank/macro)", India "RBI
policy/macro-event trigger", consumer "Real-world event trigger", event-driven "RBI MPC" + "Fed/
FOMC" + "Union Budget".)*

**Example phrasings:** "alert me before the next RBI rate decision"; "if RBI cuts rates ping me to
look at bank stocks"; "tell me what the Fed did overnight before market opens".

**India notes:** RBI MPC and Budget are calendar-fixed, market-moving Indian events with well-known
rate-sensitive baskets. Pivot already supports RBI-meeting event triggers — direct fit.

---

### Rank 11 — Buy-the-dip / drawdown trigger (% drop → notify or extra buy) — **weight 5%**

**What users want:** Be told when a stock or the index falls a chunk (down X% today, or X% off its
high) so they can deploy cash / top up a SIP — instead of panicking.

**Evidence summary:** Pervasive retail mantra across all angles. Stock Alarm and most alert apps
offer lower-limit / %-change "buy opportunity" alerts; Zerodha Kite alerts support %-change
triggers; SIP-app content frames disciplined dip-buying as a core benefit. *(Merges global
"Buy-the-dip / percent-drop alert", consumer "Buy-the-dip rule (drawdown trigger)".)*

**Example phrasings:** "alert me if Nifty is down 2% in a day"; "tell me when TCS falls 10% from
its high, I'll add"; "if Nifty falls 10% from its high invest extra 10000".

**India notes:** Very common Indian framing ("buy on dips," "accumulate on correction"). Pairs
naturally with SIP top-ups and RBI-rate-cut events. Drawdown-from-high trigger → notify or
register buy.

---

### Rank 12 — Portfolio digest / rebalance / smallcase-style maintenance — **weight 4%**

**What users want:** Two related asks: (a) a scheduled rollup ("how did my portfolio do this
week?" — one clean summary so I don't over-check), and (b) rebalancing — when allocation drifts
past a band or on a fixed date, tell me what to buy/sell to get back to target (incl. themed
baskets and basket SIPs).

**Evidence summary:** Digest/rollup is one of the most-cloned templates on every no-code platform
(n8n daily digests, Robinhood Cortex Digests, Sharesight weekly summary, Barchart EOD emails).
Rebalancing is Boglehead doctrine; M1 built its product on auto-pie rebalancing; smallcase
(₹15,000cr+ AUM, 500+ baskets) is built on review-and-accept rebalance notifications + basket SIPs.
*(Merges global "Portfolio rebalancing", India "Portfolio rebalance / smallcase basket", consumer
"Daily/weekly portfolio digest".)*

**Example phrasings:** "send me a weekly summary of my portfolio every Sunday"; "rebalance back to
target every quarter"; "tell me when my EV basket needs rebalancing".

**India notes:** smallcase made theme-basket + rebalance-notification mainstream. A chat digest
combats over-checking/panic-selling; a drift trigger → notify with a suggested register-basket
replicates smallcase while staying register-not-execute.

---

### Rank 13 — Smart-money / corporate-action / FII-DII event alerts (dividend, bonus, bulk/block, superstar, flows, news) — **weight 4%**

**What users want:** A cluster of "tell me when something happens to MY stocks" events beyond
price: ex-dividend/record dates, bonus/split announcements, bulk/block/insider/SAST deals,
superstar-investor portfolio changes, daily FII/DII flows, index inclusion/exclusion, and adverse
news / credit-rating changes — ideally with a draftable reaction.

**Evidence summary:** A large under-served event-driven cluster validated by a dense alert
industry: Trendlyne Superstar/Deals/Corporate-Action alerts, myAlerts.in, EquiSense, ICICIdirect
smart alerts, ValuePickr corporate-announcement threads (~1000 subscribers), Groww FII/DII pages,
Informe/MktRecap news-sentiment bots, NSE bulk/block reports, NiftyIndices rebalance schedule.
Pain in their words: "I don't want to keep refreshing BSE/NSE — just ping me when something happens
to my stocks." *(Merges event-driven "Dividend/record-date" + "Bonus/split" + "Bulk/block/insider"
+ "Superstar portfolio" + "FII/DII flow" + "Index rebalance" + "Adverse-news" + "Credit-rating",
and global "Dividend reinvestment (DRIP)".)*

**Example phrasings:** "remind me before the ex-date for ITC dividend so I hold"; "alert me on any
bulk or block deal in my watchlist"; "alert me if there's any bad news or a SEBI action on a stock
I own"; "send me the FII/DII numbers every day after close".

**India notes:** Smart-money following (promoters, FIIs, big-bull investors), GMP/bulk-deal/FII-DII
culture, and post-Adani news anxiety are distinctly Indian. All filed to BSE/NSE → cleanly
detectable. DRIP has no native Indian broker support — a genuine gap (event: dividend credited →
register buy).

---

### Rank 14 — Per-leg / overall MTM management + auto square-off + signal-to-action pipeline (options risk + webhook) — **weight 2%**

**What users want:** For multi-leg option positions: a per-leg premium stop-loss (exit a runaway
leg, keep the other), an overall MTM target/stop with lock-and-trail ("book at +4000, lock 1000 at
+2000 and trail"), and a hard auto square-off / reminder before the 3:15 cutoff and on expiry.
Plus, for the algo-curious: a "signal → prepare order" pipeline (when my chart/strategy fires,
draft the trade).

**Evidence summary:** Documented as headline settings across every Indian options platform
(AlgoTest leg-wise SL, Quantiply MTM lock-and-trail, configurable auto-squareoff). Webhook/
signal-to-action is the canonical r/algotrading pipeline; Zerodha's ATO (Alert Triggers Order) is
the broker-native register-not-execute version. Ranked lower because these are
power-user/advanced-seller features layered on top of the entry automations above (smaller
absolute audience than SIP/price/results). *(Merges F&O "Per-leg SL" + "Overall MTM lock/trail" +
"Auto square-off" + re-entry/adjustment/delta-hedge, global "Webhook/signal-to-action" +
"Conditional/bracket entry".)*

**Example phrasings:** "put a 30% stoploss on each leg of my strangle"; "exit my whole position if
I make 5000 or lose 3000 today"; "remind me to square off all options by 3:10"; "when my
TradingView strategy fires, prepare the order".

**India notes:** Expiry/intraday option sellers rely on these daily; auto-square-off avoids broker
penalty fees and expiry theta collapse. ATO is the exact register-not-execute pattern Pivot uses —
SEBI's Feb 2025 algo framework keeps true auto-execution constrained, making draft-and-confirm the
compliant sweet spot. Pivot's portfolio-Greeks engine already computes net delta.

---

**Weight check:** 16 + 13 + 9 + 8 + 8 + 7 + 7 + 6 + 6 + 5 + 5 + 4 + 4 + 2 = **100**.

---

## 2. Trigger → Action Shape Inventory

Across every no-code platform (Zapier/IFTTT/n8n) and every broker/options tool, the same small set
of underlying shapes recurs. Retail investors don't want new shapes — they want the shapes they
already trust, pointed at markets and expressed in plain chat.

| Shape | Definition | Popularity notes | Maps to ranks |
|---|---|---|---|
| **Threshold → notify** | A number crosses a line → ping me. | THE universal #1 beginner finance automation. IFTTT's finance hub is 9+ stock-drop/rise applets; TradingView calls price alerts the most common type; Zerodha Kite alerts (free, capped 500). | 1, 7, 11, 13 |
| **Threshold → act** | Number crosses a line → place/queue the order. | The "if-this-then-that" apex. Zerodha GTT (free, 1-yr, OCO) and ATO are the mass-market, SEBI-friendly versions; the exact register-not-execute pattern Pivot uses. | 1, 9, 11, 14 |
| **Schedule → act** | Every day/week/month at time X → invest/transfer/buy. | Backbone of consumer automation; in money it's the biggest recurring behaviour in India (SIP + UPI Autopay ~1B txns/mo). 9:20 straddle is a schedule trigger. | 2, 6, 12 |
| **Event → notify** | A real-world event happens → tell me. | Whole alert industry (earnings/IPO/RBI/dividend/bulk-deal calendars; Trendlyne, IPO Notify, Investing.com event alerts). Under-served and high-demand. | 4, 8, 10, 13 |
| **Event → act** | Event happens → draft a hedge/exit/reaction trade. | Earnings-reaction, IPO listing-day sell, RBI rate-cut basket buy, expiry-day 0DTE. Pivot's edge: detect event AND draft register order in one chat turn. | 4, 6, 8, 10 |
| **Trailing / dynamic** | A level that recalculates (ratchets with price; strike picked off live spot; lock-and-trail MTM). | Explicitly under-served in India (GTT was static; trailing SL "most requested"). Includes premium/delta-relative strike selection and option MTM lock-and-trail. | 6, 9, 14 |
| **Multi-condition** | Chain 2+ conditions (RSI<30 AND >50 EMA; IV high AND no event) before firing. | Streak chains up to 5 conditions; bracket/conditional entry attaches SL+target; the power-user evolution of the single threshold. | 3, 5, 6, 14 |
| **Digest / summary (rollup)** | Collect things over a period → send one summary. | One of the most-cloned no-code templates (n8n daily digest, Robinhood Cortex Digests, Sharesight weekly, Barchart EOD). Combats over-checking; fits chat-first. | 12 (+ FII/DII daily rollup in 13) |
| **Per-event aggregation** | Accumulate a delta per real-world transaction → invest it (round-ups, sweep spare change). | Acorns popularized it (14M+ users, $27B+). Conceptually loved; India lacks a clean card-roundup rail (maps to UPI round-up / periodic small-buy). Lower priority for Pivot today. | (sub-pattern of 2) |

**Cross-cutting delivery preference:** WhatsApp / Telegram is the strongly preferred notification
channel in India (Sensibull, Wegro, Pocketful all default to it; email lands in promotions). Any
trigger's "notify" action should default to messaging, not email — a low-cost, high-impact
India-specific differentiator that applies across *all* shapes above.

**Cross-cutting guardrail pattern:** A "behavioral nudge / risk warning" (event = risky order →
notify "are you sure?") is validated by Zerodha Nudge (penny-stock volume dropped 50%+ after
launch). It is an event→notify shape pointed inward at the user's own action, and fits
notify-only/register-not-execute perfectly.

---

## 3. Source List

**Brokers / order types / GTT / ATO / nudges**
- https://zerodha.com/z-connect/kite/introducing-gtt-good-till-triggered-orders
- https://support.zerodha.com/category/trading-and-markets/charts-and-orders/gtt/articles/what-is-the-good-till-triggered-gtt-feature
- https://support.zerodha.com/category/trading-and-markets/alerts-and-nudges/kite-alerts/articles/what-are-kite-alerts-and-how-do-i-use-them
- https://zerodha.com/z-connect/business-updates/introducing-alert-triggers-order-ato-feature-on-kite
- https://support.zerodha.com/category/trading-and-markets/alerts-and-nudges/nudges/articles/penny-stock-block
- https://medium.com/@m8madhu/how-zerodha-uses-nudge-theory-to-become-investors-favorite-cdccd86ac766
- https://zerodha.com/z-connect/featured/advanced-order-features-on-kite-a-comprehensive-guide
- https://support.zerodha.com/category/trading-and-markets/charts-and-orders/basket-order/articles/kite-basket-orders
- https://www.interactivebrokers.com/en/trading/ordertypes.php
- https://www.interactivebrokers.com/en/trading/recurring-investments.php
- https://www.schwab.com/learn/story/how-to-use-advanced-stock-order-types
- https://www.fidelity.com/learning-center/trading-investing/trading/conditional-order-types
- https://robinhood.com/us/en/newsroom/trailing-stop-orders-are-here/
- https://robinhood.com/us/en/support/articles/cortex-digests/
- https://www.warriortrading.com/bracket-orders/

**Alerts / screeners / TradingView / Streak / Chartink / Trendlyne**
- https://www.tradingview.com/support/solutions/43000763313-how-to-use-price-alerts/
- https://www.tradingview.com/script/swAaY8La-Volume-Spike-Breakout-Alerts/
- https://toslc.thinkorswim.com/center/howToTos/thinkManual/Scan/Stock-Hacker
- https://www.streak.tech/  ·  https://www.streak.tech/scanners  ·  https://www.streak.tech/scanner/52-week-high-breakout  ·  https://www.streak.tech/scanner/rsi-oversold
- https://help.streak.tech/dynamic_contract/
- https://chartink.com/screeners  ·  https://chartink.com/screener/intraday-breakout
- https://trendlyne.com/alerts/  ·  https://help.trendlyne.com/support/solutions/articles/84000396716-what-are-the-different-types-of-alerts-
- https://github.com/pkjmesra/PKScreener
- https://www.nseindia.com/market-data/52-week-high-equity-market

**SIP / recurring / UPI Autopay / smallcase / gold**
- https://groww.in/blog/sip-in-etf
- https://www.icicidirect.com/equity-products/stock-sip
- https://www.digigold.com/sip  ·  https://nestapp.in/blogs/gold-sip-vs-digital-gold-best-way-to-invest-monthly-in-gold-india
- https://www.smallcase.com/  ·  https://www.smallcase.com/learn/what-is-smallcase/
- https://www.bhimupi.org.in/upiautopay  ·  https://zerodha.com/z-connect/coin/upi-autopay-on-coin-automate-your-investments
- https://www.icicidirect.com/equity-products/smart-stock-alerts

**Options / F&O / IV / expiry / Sensibull / AlgoTest / Quantiply**
- https://sensibull.com/  ·  https://web.sensibull.com/option-strategy-builder  ·  https://web.sensibull.com/options-screener  ·  https://web.sensibull.com/open-interest
- https://zerodha.com/z-connect/sensibull/introducing-expiry-trades
- https://www.marketcalls.in/futures-and-options/how-the-9-20-intraday-straddlers-are-being-gamed.html
- https://docs.algotest.in/strategy-builder/additional-information/stop-loss/  ·  https://docs.algotest.in/features/overall-strategy-settings/  ·  https://docs.algotest.in/execution-settings/auto-squareoff/
- https://algotest.in/blog/bank-nifty-expiry-day/  ·  https://algotest.in/blog/refining-strike-selection-introducing-atm-straddle-premium/
- https://quantiply.tech/documentation/other-features/mtm-setting-increase-decrease/
- https://tradetron.tech/  ·  https://squareoff.in/short-straddle-intraday-strategy/
- https://zerodha.com/varsity/chapter/iron-condor/  ·  https://zerodha.com/varsity/chapter/max-pain-pcr-ratio/
- https://www.5paisa.com/blog/event-driven-option-trades  ·  https://www.5paisa.com/blog/straddle-and-strangle-strategies-when-india-vix-is-high
- https://www.sahi.com/blogs/nifty-expiry-day-strategies-scalping-guide  ·  https://www.sahi.com/blogs/how-to-trade-options-on-expiry-day
- https://web.quantsapp.com/alerts  ·  https://www.warriortrading.com/implied-volatility-iv-rank/

**Event-driven: results / dividend / bonus / IPO / bulk-deal / superstar / FII-DII / news**
- https://forum.valuepickr.com/t/realtime-alerts-for-bse-corporate-announcements/845  ·  https://forum.valuepickr.com/t/myalerts-in-instant-whatsapp-email-notifications-ai-powered/201905  ·  https://forum.valuepickr.com/t/introducing-equisenses-feeds-a-free-tool-for-smart-investors/202247
- https://earnings.thecore.in/  ·  https://www.niftytrader.in/results-calendar  ·  https://in.investing.com/earnings-calendar
- https://cleartax.in/s/ex-dividend-date  ·  https://www.stockgro.club/blogs/stock-market-101/dividend-capture-strategy/
- https://www.5paisa.com/share-market-today/bonus  ·  https://www.chittorgarh.com/newportal/stock-market-home.asp
- https://iponotify.me/  ·  https://ipowatch.in/ipo-allotment-status-how-to-check/  ·  https://groww.in/ipo/gmp  ·  https://mainboardgmp.com/
- https://trendlyne.com/portfolio/bulk-block-deals/  ·  https://www.nseindia.com/report-detail/display-bulk-and-block-deals
- https://trendlyne.com/portfolio/superstar-shareholders/index/  ·  https://www.tickertape.in/stocks/collections/top-20-investors-in-india-portfolio  ·  https://www.smallcase.com/star-investors/
- https://groww.in/fii-dii-data  ·  https://fii-diidata.mrchartist.com/
- https://www.niftyindices.com/resources/index-rebalancing-schedule  ·  https://insights.dsij.in/dsijarticledetail/index-inclusion-and-exclusion...
- https://informe.in/  ·  https://pulse.zerodha.com/

**Macro events: RBI / Fed / Budget**
- https://sundayguardianlive.com/business/rbi-mpc-meeting-today-2026...
- https://www.outlookbusiness.com/markets/us-fed-meeting-how-indian-markets-can-react-to-rate-change  ·  https://www.businesstoday.in/markets/stocks/story/fomc-meeting-key-takeaways-for-indian-investors...
- https://groww.in/blog/union-budget-2026-impact  ·  https://cleartax.in/s/budget-day-market-movement-history-in-india
- https://www.investing.com/economic-calendar  ·  https://www.investing.com/central-banks/fed-rate-monitor

**Consumer automation / round-ups / digests / DRIP**
- https://zapier.com/blog/popular-zaps/  ·  https://ifttt.com/finance  ·  https://ifttt.com/explore/top-applets-on-ifttt
- https://n8n.io/workflows/6662-automate-daily-email-digest-with-gmail-and-gpt-summary-sent-every-afternoon/  ·  https://n8n.io/workflows/4709-daily-news-digest-summarize-rss-feeds-with-openai-and-deliver-to-whatsapp/
- https://www.google.com/alerts  ·  https://www.acorns.com/round-ups/
- https://www.sharesight.com/blog/3-sharesight-email-alerts-you-should-enable-today/  ·  https://www.barchart.com/my/eod-portfolio-emails
- https://www.nerdwallet.com/investing/best/drip-brokers-for-dividend-investing  ·  https://www.dripinvesting.org/drip-brokers/
- https://www.sofi.com/learn/content/how-to-automate-savings/  ·  https://www.capitalone.com/bank/autosave/

**Notification channel / SEBI algo framework**
- https://wegro.app/  ·  https://www.pocketful.in/blog/best-stock-alert-apps-in-india/  ·  https://portfoliotrackr.com/blog/telegram-stock-alerts
- https://www.sebi.gov.in/legal/circulars/feb-2025/safer-participation-of-retail-investors-in-algorithmic-trading_91614.html
- https://www.businesstoday.in/markets/story/why-91-of-retail-fo-traders-lost-money-in-fy24-expert-explains-527176-2026-04-23
