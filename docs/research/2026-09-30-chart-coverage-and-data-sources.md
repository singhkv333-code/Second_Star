# Chart coverage and data sources — research (2026-09-30)

Question: what should the charts cover (India-first, plus major global
indices/commodities), which futures, where the data comes from, how many
symbols in total, and how live prices reach the browser.

## 1. Today (verified in code)

| Group | Symbols | Source |
|---|---:|---|
| NSE equities (NIFTY 500, `charto/data/symbols.json`) | 500 | Kite history + Kite WS (`kite_stream.py`) |
| Indian indices + India VIX (`backfill_macro.INDICES`) | 24 | Kite |
| MCX futures (`backfill_macro.METALS`) | 13 | Kite |
| INR currency futures (`backfill_macro.CURRENCY`) | 4 | Kite |
| Crypto (`backfill_crypto.COINBASE` / `BYBIT`) | 20 | Coinbase + Bybit public REST/WS (`crypto_stream.py`) |

About 560 symbols in total.

## 2. The blocking issue: licensing, not code

Exchanges (NSE, BSE, MCX, CME, ICE) and index owners (S&P DJI, Nasdaq, FTSE)
own their prices and sell them. Kite Connect data is for the account holder's
personal use only; displaying it to other users is a violation and can end API
access ([Kite terms](https://kite.trade/terms/),
[forum](https://kite.trade/forum/discussion/10075/presenting-the-data-fetched-from-the-api-to-end-users)).
Serving charts to the public needs an **exchange-authorised vendor** plus
exchange display fees. The same logic abroad is why TradingView shows broker
CFD quotes (e.g. `OANDA:SPX500USD`) next to licensed index values.

NSE tariff reference points (confirm the current sheet before signing):
- Real-time via a vendor: variable ~₹150–300 per user per month for the
  capital market segment, by frequency (1-min / 1-sec / tick)
  ([NSE pricing, 2026-03](https://nsearchives.nseindia.com/web/mediaattachment/2026-03/NSE_Pricing_file_-_Domestic_clients_20260309171343.pdf)).
- 15-min delayed: fixed fee per segment per medium (~₹90,000 for CM, and the
  same again for F&O), with no per-user fee; web and app count as separate
  media ([delayed tariff](https://nsearchives.nseindia.com/web/sites/default/files/inline-files/Download%2015%20mins%20delayed%20data%20tariff.pdf)).
  This is a possible free-tier path: delayed for free users, real-time for paid.

## 3. Recommended coverage

### India (core)

| Segment | Scope | Approx. count |
|---|---|---:|
| Cash equities | All actively traded NSE names (~2,770 in 2026), plus NSE ETFs (incl. MON100/N100 proxies), REITs and InvITs; BSE-only illiquid names later | ~3,000 |
| Indices | NSE broad/sectoral/thematic, SENSEX, BANKEX, India VIX | ~70 |
| Stock futures | All F&O stocks (~210; changes quarterly) | ~210 |
| Index futures | NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY, NIFTYNXT50, SENSEX, BANKEX, SENSEX50 | 8 |
| MCX | Full list incl. minis (bullion, energy, base metals, agri) + iCOMDEX indices | ~25 |
| Currency futures | USDINR, EURINR, GBPINR, JPYINR + EURUSD, GBPUSD, USDJPY | 7 |
| Options | Not stored per contract; load a chain when a user opens it | on demand |

Futures are charted as one continuous front-month series per underlying
(TradingView's `NIFTY1!` model), rolled on liquidity. `backfill_macro.py`
already chooses the most active contract, so this extends existing logic.

### International (context for Indian traders)

| Group | Symbols | Count |
|---|---|---:|
| GIFT Nifty (NSE IX) | The overnight cue for NIFTY's open; highest value | 1 |
| US indices | S&P 500, Nasdaq 100, Dow, Russell 2000, VIX | 5 |
| Europe/Asia indices | FTSE 100, DAX, CAC 40, Euro Stoxx 50, Nikkei 225, Hang Seng, Shanghai Comp, KOSPI, ASX 200 | ~10 |
| Global commodities | WTI, Brent, natural gas, gold, silver, copper, platinum, wheat, corn, soybeans, sugar, coffee | ~12 |
| FX and rates | 7 majors, DXY, USDINR spot, US 10Y, India 10Y | ~11 |
| Crypto | Extend 20 → ~30 | ~30 |

Individual US stocks stay out of scope (CLAUDE.md §4). Listed Indian ETFs are
the proxy.

**Total: ~3,400 charted symbols**, about 6× today, plus options on demand.

## 4. Sources

| Segment | Source | Notes |
|---|---|---|
| NSE/BSE/MCX/CDS, live + history | Authorised vendor: [TrueData](https://www.truedata.in/market-data-apis) or [Global Datafeeds](https://globaldatafeeds.in/authorised-data-vendors/) | WS + REST, tick and history; redistribution needs exchange approval and fees |
| GIFT Nifty | A vendor with NSE IX coverage; TrueData does not carry it | Ask Global Datafeeds |
| Global indices/commodities/FX | One aggregator: [Twelve Data Venture](https://twelvedata.com/pricing-business) (from $149/mo, external display) or [Massive (ex-Polygon)](https://massive.com/indices) | One integration instead of five licences |
| True CME futures (later) | [Databento](https://databento.com/futures) | Live CME from ~$36.50/mo for own use; displaying to users needs a CME distributor licence |
| Crypto | Keep Coinbase + Bybit; add Binance | Free public feeds; check each venue's display terms |
| yfinance | Development only | Yahoo terms restrict it to personal use |

Kite stays for what it is licensed for: a user's own broker account.

## 5. Live-price architecture

1. Vendor WS → **our server only** (never the browser: key secrecy and licence terms).
2. Normalise every feed to (symbol, exchange ts, price, volume). `kite_stream.py`
   and `crypto_stream.py` already feed one seam, `dataserver._live_on_tick`; a
   new vendor is one more adapter of that shape.
3. Build 1-minute bars and write them to the store (exists).
4. Fan out through Redis to our own WS/SSE endpoint (SSE exists).
5. Subscribe on demand: stream what users view plus watchlists, holdings and
   indices, not all ~3,400 symbols constantly. Vendors price by symbol count,
   and Kite caps instruments per connection.

## 6. Suggested sequence

1. **Licensing:** vendor contract for NSE/BSE/MCX (a business decision, like
   `live_orders_enabled`). Consider launching on 15-min delayed data.
2. **Broaden India:** full NSE mainboard, ETFs, ~70 indices, continuous
   futures, through the existing pipeline.
3. **Global:** one aggregator plus GIFT Nifty (~70 symbols).
4. **Options:** chains on demand.

Other sources used: [NSE/BSE listed counts](https://rupeezy.in/blog/how-many-companies-are-listed-on-nse-and-bse),
[F&O stock list](https://venturasecurities.com/invest/stocks/fno-stocks-list),
[index lot sizes and expiries](https://algotest.in/blog/nifty-lot-size/),
[MCX products](https://www.mcxindia.com/products/index),
[Databento CME](https://roadmap.databento.com/announcements/live-cme-data-is-now-open-to-all-users-starting-at-3265month).
