# Free data sources and Kite capacity — research (2026-09-30)

Companion to `2026-09-30-chart-coverage-and-data-sources.md`. That report
answers "what should we cover" and assumes a paid vendor. This one answers
"how far do we get with Kite plus free sources", with every number measured
today unless marked otherwise.

**Read this first:** free and *redistributable* almost never coincide. Every
source below except the government ones is licensed for the account holder's
own use. The free stack is for development, the closed beta and our own
research. A public launch still needs the vendor path in the companion report.

## 1. What Kite can serve (counted from the live instruments dump)

`https://api.kite.trade/instruments` (public, no login) returned
**108,842 instruments** today. Chartable, non-option, non-debt:

| Group | Count | Notes |
|---|---:|---|
| NSE equities, ETFs, REITs, InvITs | **3,884** | EQ 3,019 (≈248 ETFs), SME 469+105, BE 237, BZ 27, InvIT 21, REIT 6 |
| BSE equities (scrip codes 5xxxxx) | 5,422 | ~2,650 have no NSE symbol match (upper bound; names differ across exchanges) |
| Indices | **236** | NSE 136, BSE 76, MCX iCOMDEX 11, `GLOBAL` 12, `NSEIX` 1 (GIFT NIFTY) |
| Continuous futures (one per underlying) | **291** | NFO 219 (stocks and indices), BFO 4, MCX 30, NSE commodities (`NCO`) 23, CDS 15 |
| Options | 97,000+ contracts | NFO, BFO, MCX, NCO, CDS; load a chain on demand, never store |

**≈4,400 symbols without the BSE tail, ≈7,000 with it**, against about 560
today. All of these come on the one ₹500/month Kite Connect key; historical
data has been included in that fee since Feb 2025. The free "Personal" plan
has **no** market data.

Useful finds in the dump:
- **`GLOBAL` segment:** US500, US100, US30, USCOMPOSITE, UK100, GERMANY40,
  FRANCE40, JAPAN225, HANGSENG, SHANGHAICHINA, AUS200, US10YRYIELD.
- **`NSEIX:GIFT NIFTY`:** the overnight NIFTY cue. Yahoo does not carry it
  (`SGX-NIFTY` and `GIFTNIFTY.NS` both return no data), so Kite is our only
  free source.
- **`NCO` (NSE commodities):** includes `BRCRUDEOIL`, which is Brent in INR.
  MCX has only WTI.
- **CDS:** also lists G-sec and T-bill interest-rate futures besides the four
  INR pairs.

**Unverified:** there was no Kite session on this machine, and the VM is off
limits (see the 2026-09-26 compromise). So whether `GLOBAL` and `NSEIX` return
historical candles, or only quotes, is untested. The forum reports a working
GIFT NIFTY quote. Test it with one logged-in call:
`kite.historical_data(<token>, from, to, "minute")` for each of the 13 tokens.

### Kite limits that shape the design

| Limit | Value | Consequence |
|---|---|---|
| WebSocket | 3,000 instruments × 3 connections per key = **9,000 live** | Enough to stream every Indian symbol above at once (~4,400) |
| Historical | ~3 req/s documented (we measured ~13 req/s at 8 workers); minute data in 60-day windows | Backfilling 10 years of minutes for 3,884 names is ~237k requests: ~5 h at 13/s, ~22 h at 3/s |
| Expired contracts | No history (tokens are recycled) | Continuous futures must be stitched from our own stored front-month bars from today on |
| Licence | Personal use only; display to others is a violation ([terms](https://kite.trade/terms/), [support](https://support.zerodha.com/category/trading-and-markets/general-kite/kite-api/articles/can-i-use-historical-and-live-data-taken-from-kite-connect-api-on-other-platforms)) | Fine for dev and the 11 accounts; not for a public launch |

Storage: 500 names are 24.4 GB / 413M minute bars today. Keep minute history
for the liquid ~1,000 and daily history (from bhavcopy, below) for the tail.
Illiquid names have sparse minutes anyway.

## 2. Free sources beyond Kite (probed today)

### India, end of day (public archives, no key)

| Source | Status | Use |
|---|---|---|
| NSE CM bhavcopy (UDiFF) `nsearchives…/content/cm/BhavCopy_NSE_CM_…zip` | ✅ 200 | Daily OHLCV for every NSE name; independent of Kite |
| NSE F&O bhavcopy `…/content/fo/…zip` | ✅ 200 | Daily futures/options incl. expired contracts, which fixes Kite's gap |
| NSE index close `…/content/indices/ind_close_all_DDMMYYYY.csv` | ✅ 200 | Daily OHLC for every NSE index |
| BSE bhavcopy `bseindia.com/download/BhavCopy/Equity/…CSV` | ✅ 200 | Daily for all BSE names |
| NSE live JSON (`/api/quote-equity`) | ❌ 403 | Akamai blocks scripts; don't build on it |
| MCX bhavcopy page | ❌ 403 | Same; use Kite for MCX |

### Global indices, futures, FX, rates

| Source | Free limit | Coverage | Verdict |
|---|---|---|---|
| **Yahoo (yfinance 0.2.66, installed)** | Unofficial; 1m for ~7 days, 5m/15m for 60 days, 1h for 730 days, daily for decades; `yf.WebSocket` live stream | Tested 40 symbols, all live except GIFT NIFTY and BSE `.BO`: S&P/Nasdaq/Dow/Russell/VIX, FTSE/DAX/CAC/Stoxx50/Nikkei/HSI/Shanghai/KOSPI/ASX, ES/NQ/CL/BZ/NG/GC/SI/HG/PL/ZW/ZC/ZS/SB/KC/CT futures, DXY, US10Y, EURUSD, USDINR | **Best free coverage.** Personal-use terms and no SLA; record 1m forward to accumulate history |
| Twelve Data | 800 credits/day, 8/min, **8 WS symbols** | Global indices, FX, crypto, some commodities | Too small for live; OK for a daily top-up |
| Finnhub | 60/min, **50 WS symbols** | US stocks, FX, crypto (no indices or futures on free) | FX live only |
| Alpha Vantage (MCP connected) | 25 req/day | Daily/monthly WTI, Brent, NG, copper, aluminium, wheat, corn, cotton, sugar, coffee; FX | Daily commodity backfill only |
| OANDA practice account | Free with a demo account; REST + streaming | FX, index CFDs, metals, energy, grains (the TradingView `OANDA:SPX500USD` model) | Needs an account (not tested); India eligibility is inconsistently reported |
| EIA open data | Public domain | WTI/Brent/Henry Hub spot, daily | ✅ 200. Redistributable |
| FRED | Keyless CSV | Rates, FX, many commodity spots | Timed out from this network; retry from Azure. Own series are redistributable; licensed ones (S&P 500) are not |
| Stooq | — | — | ❌ Now behind a JavaScript challenge; no longer scriptable |
| Dukascopy tick archive | — | FX/CFD ticks | ❌ 503/timeout today |

### Crypto (genuinely free and live; all probed ✅)

Binance spot + USDT-M futures, OKX, Bybit, Coinbase, Kraken, **Delta Exchange
India** (`api.india.delta.exchange`, INR-relevant, FIU-registered) and
CoinGecko (daily/30-min OHLC). `crypto_stream.py`'s `Venue` is three pure
functions, so each venue is a small adapter.

### Other Indian broker APIs (free data, same personal-use licence)

Upstox (free; WS v3, 2 conns × 5,000 LTPC), Angel One SmartAPI (free),
Fyers (free), Shoonya (free), Dhan (data ₹499/mo; 5 conns × 5,000). These are
redundancy for Kite, not new coverage: they carry the same exchanges.

## 3. How to go ahead

1. **Broaden India on Kite now (no new spend).** Extend `symbols.json` from
   500 to the 3,884 NSE names, all 236 indices and the 291 continuous futures,
   through the existing `backfill_1min.py` / `backfill_macro.py` /
   `kite_stream.py` path. Subscribe the WS on demand plus watchlists; the cap
   is 9,000, so it is not binding.
2. **Add bhavcopy as the daily spine.** One nightly job pulling NSE CM, F&O,
   indices and BSE. It covers the BSE tail and expired futures, and it is a
   second source that catches Kite gaps. (`pivot_db` fails silently; this
   should not.)
3. **Global context via Yahoo, behind one adapter.** About 40 symbols. Pull
   daily history once, record 1m forward from `yf.WebSocket` into the same
   `_live_on_tick` seam, and tag `source="yahoo"` (the relay rule). Swap the
   adapter for Twelve Data Venture or Massive when we go public.
4. **GIFT NIFTY and the `GLOBAL` segment:** verify on the next Kite login
   (§1). If they only quote, poll the quote each minute and build bars.
5. **Crypto:** add Binance and Delta India to the two existing venues.
6. **Before any public launch:** the vendor contract from the companion
   report. A cheaper alternative worth asking Upstox and Zerodha about: each
   user's charts fed by *that user's own* broker session through our existing
   six-broker layer. That is arguably their personal use, but get it in
   writing; it is not settled by anything here.

Sources: [Kite WebSocket limits](https://kite.trade/docs/connect/v3/websocket/),
[Kite pricing](https://support.zerodha.com/category/trading-and-markets/general-kite/kite-api/articles/what-are-the-charges-for-kite-apis),
[historical now free with Connect](https://kite.trade/forum/discussion/14806/historical-data-is-now-free-with-base-kite-connect-subscription),
[GIFT NIFTY on Kite](https://kite.trade/forum/discussion/13705/fetching-gift-nifty-code-returning-other-symbol),
[Kite redistribution forum](https://kite.trade/forum/discussion/10075/presenting-the-data-fetched-from-the-api-to-end-users),
[Twelve Data trial](https://support.twelvedata.com/en/articles/5335783-trial),
[Finnhub free tier](https://freeapi.watch/finnhub/),
[OANDA dev guide](https://developer.oanda.com/rest-live-v20/development-guide/),
[OANDA India eligibility](https://brokerchooser.com/broker-reviews/oanda-review/oanda-india),
[Indian broker API comparison](https://github.com/VisualDigitalAgency/options-strategy-dashboard/issues/97).
