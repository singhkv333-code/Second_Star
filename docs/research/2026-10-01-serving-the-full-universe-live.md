# Serving the full universe live — research (2026-10-01)

Question: we now hold history for ~6,600 instruments. What does it take to
serve them live on Pivot, rendered correctly, on a VM that can carry it, with
the bars each plan is entitled to?

Companions: `2026-09-30-chart-coverage-and-data-sources.md` (what to cover,
vendors) and `2026-09-30-free-sources-and-kite-capacity.md` (Kite limits).
Every number below was measured today unless marked otherwise.

## 1. What we have

On the backfill VM (`pivot-backfill/kite-backfill`, 2 vCPU / 3 GB, `/data`
86 of 123 GB used), all in charto's own `bars` shape
(`symbol, ts, o,h,l,c,v`, `PRIMARY KEY (symbol, ts) WITHOUT ROWID`):

| Store | Instruments | 1-min bars | Size |
|---|---:|---:|---:|
| `backfill/nse_1m.db` | 3,037 NSE equities | 1,187.6M | 69.3 GB |
| `backfill/bse_1m.db` | 1,914 BSE-only names | 225.9M | 13.0 GB |
| `backfill2/idx_*.db` | 236 indices (NSE 136, BSE 76, MCX 11, GLOBAL 12, NSEIX 1) | ~99M (all of phase 2) | 5.9 GB |
| `backfill2/fut_*.db` | 1,136 live contracts + 291 continuous (daily only) | (in the 99M) | 0.56 GB |

About **1.5 billion minute bars, ~89 GB**, against 497M / 31 GB on the prod
store today. Plus `bars_1d` daily history (indices from 2004, GIFT NIFTY from
2017).

Facts from the data that shape the design:

- **Sessions cross midnight.** GIFT NIFTY and US500 have minutes at every
  minute-of-day IST (0–1439); JAPAN225 runs 05:30–11:29 IST; MCX crude
  09:00–23:29. Charto buckets by IST *calendar day* with three clocks (NSE,
  MCX, UTC). A daily or 4h bar for GIFT NIFTY would be cut at midnight.
- **Futures minutes are only as deep as the contract.** NIFTY26OCTFUT starts
  2026-07-29 (its listing). Kite's `continuous=1` is daily-only, and expired
  contracts get no history (tokens are recycled). So a continuous intraday
  `NIFTY1!` is **~2 months deep today** and grows only by keeping every
  contract we record.
- **No open interest was stored** (`oi: 0` in the job). Kite's FULL ticks and
  `historical_data(oi=1)` both carry it.
- **Kite's history is adjusted as of the fetch date** for splits, bonuses,
  rights, spin-offs and dividends over 2% (intraday adjustment reliable from
  ~2017; before that, only active names), and there is no unadjusted series
  and no corporate-actions API
  ([Zerodha](https://x.com/zerodha/status/1952292763929874868),
  [forum](https://kite.trade/forum/discussion/16280/historical-5-minute-api-data-corporate-action-price-and-volume-adjustments)).
  Our backfill is adjusted to 30 Sep 2026; every minute we record live from
  now on is raw. The first split after today puts a cliff in the chart.
- **Symbols collide.** `GOLD26OCTFUT` exists on two exchanges in the target
  list; NSE and BSE share company names. Charto keys everything on a bare
  string (`"RELIANCE"`, `"NIFTY 50"`).
- **1.86M rows sit in the `anomalies` tables** across the stores. They need a
  serve/skip policy before anything is served.

## 2. How others do it

**TradingView** (the reference for what "rendered correctly" means):
- The chart asks for a time range and gets bars back (`getBars`), then
  subscribes per symbol and resolution (`subscribeBars`); the server pushes
  the *whole current bar* on every change, never a delta
  ([Datafeed API](https://www.tradingview.com/charting-library-docs/latest/connecting_data/Datafeed-API/),
  [subscriptions](https://www.tradingview.com/charting-library-docs/latest/connecting_data/datafeed-api/datafeed-subscriptions/)).
  Charto already works this way (`/bars` + `/stream` pushing the forming bar).
- **Plans gate intraday depth by bar count**: Basic 5K, Essential/Plus 10K,
  Premium 20K, Expert 25K, Ultimate 40K, **and daily charts show all
  history on every plan**
  ([TV support](https://www.tradingview.com/support/solutions/43000480679-historical-intraday-data-bars-and-limits-explained/)).
  Our `chart.history_bars` copies this (Free 10K, Pro/Pro+ unlimited,
  intraday only).
- **Real-time vs delayed is a licence line, not a plan feature.** Indian free
  users see NSE/BSE **15 min delayed** until they sign the exchange's
  non-professional agreement; F&O real-time is on by default for
  non-professionals
  ([TV India F&O](https://in.tradingview.com/support/solutions/43000777677-can-i-access-real-time-futures-and-options-data-from-nse-and-bse/),
  [delay report](https://businessupturn.com/finance/stock-market/tradingview-free-users-hit-with-a-15-minute-delay-on-nse-and-bse-data-and-how-to-switch-real-time-back-on/)).
- **Continuous futures**: `1!` = front month, `2!` = next, rolled on expiry;
  unadjusted by default, with back-adjustment as a toggle
  ([1!/2!](https://www.tradingview.com/support/solutions/43000483493-what-are-1-and-2-continuous-futures-contracts/),
  [back-adjust](https://www.tradingview.com/support/solutions/43000685266)).
  Volume-based rolls are the norm for commodities
  ([Sierra Chart](https://www.sierrachart.com/index.php?page=doc%2FContinuousFuturesContractCharts.html)).

**Zerodha/Kite charts** use ChartIQ and TradingView front-ends on Zerodha's
own feed, which is a different source from the Kite Connect websocket, so
the two can disagree on OHLC
([forum](https://kite.trade/forum/discussion/15277/ohlc-discrepancy-between-zerodha-api-websocket-and-zerodha-charts)).
Brokers show live data under their own exchange entitlement.

**Storage at this scale**: the market-data stacks store 1-minute (or tick) as
the source of truth and keep *pre-aggregated* higher timeframes that refresh
incrementally (QuestDB materialized views chaining tick → 1s → 1m; ClickHouse
pre-computed bars)
([QuestDB](https://questdb.com/docs/concepts/materialized-views/),
[ClickHouse](https://oneuptime.com/blog/post/2026-01-21-clickhouse-financial-market-data/view)).
A columnar store compresses OHLCV several-fold, but its advantage is wide
scans. A chart's read is a range on one symbol, which a clustered B-tree
(`(symbol, ts) WITHOUT ROWID`, our schema) already serves in milliseconds.

## 3. What it takes on our side

### 3.1 Licensing (gates everything that is paid)

Kite Connect data is for the account holder's personal use. Showing it to the
11 beta accounts is already outside that, and **selling plans that serve it
is unambiguous redistribution**. The paywall can meter AI, alerts and depth
on Kite data in a closed beta; charging for market data needs an authorised
vendor (TrueData / Global Datafeeds) plus exchange fees, or the
"each user's own broker session" route (to be confirmed in writing with
Zerodha/Upstox). A cheaper launch shape, as TradingView does it: **free tier
15 min delayed** (NSE's fixed delayed-data fee, no per-user charge) and
**real-time for paid**. That is a business decision, like
`live_orders_enabled`.

### 3.2 Correct rendering (code, independent of the licence)

1. **Exchange-qualified identity.** `NSE:RELIANCE`, `BSE:500325`,
   `NFO:NIFTY26OCTFUT`, `NFO:NIFTY1!`, `MCX:CRUDEOIL1!`, `NSEIX:GIFT NIFTY`,
   `GLOBAL:US500`, with a bare symbol resolving to its NSE listing so nothing
   saved today breaks. One instrument master rebuilt each morning from the
   Kite dump, carrying exchange, kind, tick size, lot, expiry, session, store
   file and Kite token.
2. **Per-instrument sessions with a trading-day anchor**, not an IST
   calendar day. NSE/BSE 09:15, CDS 09:00, MCX 09:00 (to 23:30/23:55),
   GLOBAL and NSEIX on their own open, which can fall the previous evening
   IST. Today `session_for()` knows three clocks and `_fold_daily` cuts on
   the IST date. Read the open and close off the stored minutes (the
   existing `_SESSION_CLOSE_MIN` habit), not a brochure. This matters for
   daily, weekly, 4h and session-anchored indicators (VWAP).
3. **Price precision from tick size.** CDS needs 4 decimals; yields and
   index CFDs differ. Hide volume for indices (they have none).
4. **Corporate actions.** Store raw prices; keep a factors table
   (`symbol, ex_date, price_factor, volume_factor`) and apply it at read
   time inside `get_bars`, the single chokepoint every tool and the chart
   already use. Detect ex-dates nightly by comparing the NSE bhavcopy close
   (raw) with Kite's re-fetched daily close (adjusted), and cross-check
   NSE's corporate-actions file. Backfilled history is already adjusted to
   30 Sep, so factors apply only from that date on. Without this, the first
   split shows a cliff, and every indicator and backtest across it is wrong.
5. **Continuous futures, built ourselves.** `1!`/`2!` stitched from stored
   contract minutes at read time: NFO/BFO/CDS roll on expiry, MCX/NCO roll on
   volume. Unadjusted by default with a roll marker, plus a back-adjust option.
   For history older than our contract minutes, Kite's continuous daily
   covers the daily chart. **Never delete an expired contract's minutes**:
   they are the only intraday continuous history that will ever exist.
6. **Open interest.** Record OI from FULL ticks from now on (a new column,
   or a sibling table), and backfill OI for live contracts with
   `historical_data(oi=1)`.
7. **The anomalies policy.** Decide serve, mask or repair for the 1.86M
   flagged rows before the stores are attached.

### 3.3 The store and the read path

Today a chart read is one indexed range scan, then a Python resample.
Measured locally: a 1h page (3,000 bars) reads **180,400 minute rows in
196 ms and folds them in 73 ms**; a 1m page of 5,400 rows reads in 7 ms.
That is fine for one user and becomes the bottleneck at many symbols and many
users, because the fold runs under the GIL and the 96-entry intraday cache
stops hitting once users spread across 6,600 symbols (this was the 31-Aug
incident).

Recommendation: **keep SQLite, add stored rollups, and split files by
exchange.**
- Keep `bars` (1-minute) as the source of truth. Add a `bars_15m` rollup
  (serving 15m, 30m, 1h and 4h; ~1/15 the rows, ~6 GB) next to the existing
  `bars_1d`. Write both from the closed-minute hook using the same
  `_bucket_stamp`, so stored and resampled bars cannot drift. A 1h page then
  reads ~12k rows instead of 180k. 1m and 5m keep resampling from minutes
  (≤20k rows).
- Keep the backfill's **one file per exchange and kind** (nse, bse, idx_*,
  fut_*). The instrument master names each symbol's file, and `get_bars`
  opens the right one. Files are backed up, vacuumed and caught up
  independently, and a live write to MCX never contends with an NSE read.
- **Not now: ClickHouse or QuestDB.** They earn their place with
  cross-sectional minute scans (screening and backtests over minutes) or past
  roughly 5B rows. The chart's access pattern is per symbol, and the
  dataserver is stdlib-only by design. Revisit when screens need minutes.

Growth: ~6,600 instruments × 375–870 minutes is ≈2.7M rows/day,
≈670M rows (~40 GB) a year.

### 3.4 Live ingestion

Kite: **3,000 instruments × 3 connections per key**; LTP 8 B, QUOTE 44 B,
FULL 184 B a packet
([docs](https://kite.trade/docs/connect/v3/websocket/)). FULL is roughly one
snapshot a second.

- **Record continuously, don't only stream what is viewed.** A symbol that is
  only streamed while open has a hole wherever nobody was looking, and every
  open then pays a historical fill at ~3 req/s. With 9,000 slots: connection 1
  and most of connection 2 hold NSE equities (3,037) and all indices (236).
  Connection 2 also holds every live futures contract (~1,136). That leaves
  ~1,500 slots for BSE-only names (1,914). Stream the most traded BSE names
  always and the rest on demand (ref-counted, with a fill on first open).
  This uses all three connections, so pivot's own ticker (one connection
  today) must share the feed, not open its own.
- **A separate feed process.** Today ticks enter `_live_on_tick` inside the
  web server, so the forming bar exists in one process's memory. Move the
  socket and bar builder into their own process. It writes closed minutes
  and rollups to the stores, and publishes forming bars on a local pub/sub
  (Redis is already in the stack). Then the web tier can run several
  workers, which is the only real fix for the GIL ceiling.
- **Resubscribe each morning** from the instrument master (contracts expire,
  new series list).
- **Daily login without a human.** The Kite token dies ~06:00 IST. The
  backfill job already re-mints by TOTP with a park-after-3-failures guard;
  run that at ~08:30 IST, with the secrets in Key Vault and not in a `.env`.
  The last box with a `.env` was compromised.
- **Nightly reconcile.** After each close, re-fetch the day's minutes from
  Kite history for every recorded symbol (~6,600 requests ≈ 35 min at 3/s).
  This replaces snapshot-built bars with exchange-consistent ones, closes any
  disconnect gaps, and is the moment corporate-action factors are detected.
  The NSE/BSE bhavcopy is the independent cross-check (§2 of the free-sources
  report).

### 3.5 Fan-out to browsers

Today each open chart holds an `EventSource` on the stdlib
`ThreadingHTTPServer`: one thread per chart, per tab. Pro+ allows 50
parallel charts. Thousands of users means thousands of threads on one
interpreter.

- **One multiplexed stream per browser** (subscribe and unsubscribe a list of
  symbols over a single connection, as TradingView's quote session does).
  Watchlist quotes ride the same stream instead of polling `/quotes`.
- **A small async gateway** holds the connections and relays the feed's
  pub/sub. It is a separate service; the dataserver stays as it is for
  REST. Coalesce to at most ~4 pushes a second per symbol, and push the whole
  forming bar (the TradingView contract the chart already speaks).
- nginx gains one route, added to the allowlist and checked by
  `check_routes.sh`.

### 3.6 Plans and the paywall

What exists already works the TradingView way: `chart.history_bars` limits
intraday depth only, enforced in `_bars_for_plan`, and `chart.parallel`
bounds how many streams one account can hold. To finish:
- **Depth numbers** are still open (Pro and Pro+ are blank on the sheet).
  With minutes from 2015, the natural ladder is Free 5–10K, Pro ~20K (about
  a year of 5m), Pro+ all history. That is the user's call.
- **Delayed tier for free (if the licence goes that way)** costs almost no
  code. `get_bars` already honours a `horizon` (built for replay), so a
  delayed plan is `horizon = now − 15 min` on reads plus a 15-minute buffer
  on the stream.
- **Cut-off precompute.** `_history_cutoff` finds the depth boundary by
  reading `depth` bars, which is up to 600k minute rows for a 10K-bar 1h
  chart. With the 15m rollup it is cheap; otherwise cache it per
  `(symbol, interval)` daily.
- **Scrape guard.** 1.5B bars behind `/bars?to=` paging is the whole archive
  for anyone who loops it, which is also a licence exposure. Set a per-account
  bar budget per day, with a 402/429 in the existing refusal shape.
- **Market scope as a feature key, optionally** (e.g. GLOBAL/NSEIX or BSE on
  paid plans). Code checks the key, never the plan id, as the catalog
  already requires.

## 4. The VM

The prod VM (`Claudecodeforpivot`) is compromised (2026-09-26) and too small
(2 vCPU / 7.9 GB / no swap for 31 GB). It should not receive this data.
Rebuild:

| | Recommendation | Why |
|---|---|---|
| Size | **D8as_v5 (8 vCPU / 32 GB)**; D4as_v5 (4/16) is the floor | The hot set (last ~60 days × 6,600 symbols ≈ 150M rows ≈ 8–9 GB) stays in page cache. Separate cores for the feed, web workers, the gateway and the nightly reconcile |
| Disk | Premium SSD v2, **512 GB** data disk, separate from the OS disk | 89 GB now + rollups (~10 GB) + ~40 GB/year + WAL and backup headroom; v2 lets IOPS be raised without resizing |
| Processes | `charto-feed` (Kite WS, bar builder, rollups), `charto-data` (REST, N workers), `charto-stream` (async gateway), `charto-reconcile` (timer), Redis | Each fails alone: a dead socket never takes the charts down |
| Security | Unprivileged service user, no `NOPASSWD ALL`, Kite TOTP and keys in Key Vault via managed identity, patched Next on both trees, NSG with ssh closed | The lessons of 2026-09-26 |
| Backups | Stores to blob nightly (append-only; minutes never change after reconcile); `charto_users.db` on its existing schedule | The bars archive is irreplaceable for expired futures |

**Moving the data.** Copy `/data/backfill*` to blob first (`azcopy`, the cool
tier as the archive of record). Then pull it onto the new data disk, or
attach a disk made from a snapshot of the backfill VM's disk for the fastest
copy. Once it is in blob, the backfill VM (~₹80/day) can go. Top up from
30 Sep to the switch date with one Kite catch-up run, and do not carry
minutes over from the compromised box.

## 5. Order of work

0. **Decisions:** licensing route (Kite beta-only, vendor, or the user's own
   broker session), the free tier delayed or not, the depth numbers, the
   anomalies policy, and approval to rebuild the VM.
1. **Land the data:** archive to blob, build the instrument master
   (exchange-qualified IDs, sessions, tick sizes), apply the anomalies
   policy, build `bars_15m`.
2. **Read path:** symbol resolver and per-file routing in `get_bars`,
   trading-day sessions, the corporate-action factor, continuous `1!`/`2!`,
   price precision, and volume and OI per kind. The UI's symbol search gains
   an exchange.
3. **Live:** the feed process (3 connections, the always-on plus on-demand
   set), morning resubscribe, automated login, nightly reconcile and OI
   capture.
4. **Fan-out:** the multiplexed stream gateway, with watchlists on it.
5. **Plans:** depth numbers, a delayed horizon if chosen, the scrape budget,
   and optional market-scope keys.
6. **New VM**, cut over behind the same nginx host; `check_routes.sh` and
   one live multi-symbol pass (equity, BSE-only, index, NIFTY1!, MCX crude,
   GIFT NIFTY across midnight) before DNS.
