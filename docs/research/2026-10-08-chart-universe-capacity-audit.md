# Chart universe and serving capacity audit — 8 October 2026

## Conclusion

There is no 500-symbol chart-renderer limit in the inspected picker. The gap
is between reference records, historical source stores, and the deployed
serving store. Serve the full historical archive from a clean, appropriately
sized host, with a coverage-aware catalogue and maintained rollups; do not
simply expose the entire Postgres instrument master or hydrate everything
into the current overloaded production VM.

This was a read-only audit of Azure resources, local APIs, Postgres and SQLite.
No production configuration, data, services, or application code was changed.
Large minute tables were inventoried with indexed symbol seeks, not full
COUNT/DISTINCT scans. A bounded historical count query timed out; no current
full minute-row count is claimed.

## Measured inventory

| Layer | Count | What the number means |
|---|---:|---|
| Postgres instrument_master | 75,947 | Instrument-token records, not companies or verified chart history |
| Of those, CE/PE options | 52,038 | Separate strikes/expiries; not covered by the equity/futures archive below |
| Of those, FUT | 827 | Contract records; master refresh is stale |
| Of those, EQ | 23,082 | Includes exchange categories/indices; not a deduplicated company count |
| company_identity | 5,206 | Identity rows; exchange duplicates and symbol collisions exist |
| financials.mc.companies | 11,256 | Research corpus; does not establish OHLCV coverage |
| Local /symbols | 557 | Current localhost catalogue; hydrated field also 557 |
| Production /symbols | 929 | Current production catalogue; hydrated field also 929 |
| Production bars | 929 symbols | Actual minute-table symbol keys, independently checked |
| Production bars_1d | 557 symbols, 1,235,873 rows | Stored daily coverage is narrower than minute coverage |
| Production symbols.json | 500 | Legacy equity hydration catalogue |
| Backfill source stores, minute coverage | 5,968 series records | Sum across exchange/kind stores, excluding test databases |
| Backfill source stores, daily coverage | 6,557 series records | Includes daily-only futures series; not 6,557 unique companies |

Postgres identity breakdown: BSE 2,230 rows / 2,212 distinct symbols;
NSE 2,460 / 2,302; NSE_SME 516 / 512. These distinct counts must not be
summed as unique companies without exchange/ISIN resolution.

Instrument master groups last refreshed on 4 September (cash/EQ) or
7 September (derivatives). Its 75,947 records are not a verified current
tradable universe as of 8 October.

### Backfill VM source stores

| Store | Minute symbols | Daily symbols | File GB, decimal |
|---|---:|---:|---:|
| nse_1m | 3,035 | 3,035 | 75.64 |
| bse_1m | 1,912 | 1,912 | 16.10 |
| idx_nse | 132 | 136 | 4.53 |
| idx_bse | 67 | 74 | 1.58 |
| idx_mcx | 7 | 7 | 0.13 |
| idx_global | 12 | 12 | 0.15 |
| idx_nseix | 1 | 1 | 0.05 |
| fut_nfo | 649 | 872 | 0.61 |
| fut_bfo | 5 | 7 | <0.01 |
| fut_mcx | 128 | 181 | 0.32 |
| fut_nco | 7 | 155 | <0.01 |
| fut_cds | 13 | 165 | 0.01 |
| Total | 5,968 | 6,557 | Approximately 99.12 |

These are counts of present series, not assertions of gap-free history or
live-feed freshness. Expired futures, continuous series, exchange duplicates,
and different listings need explicit identity and coverage handling. Corrected
archive staging has COMPLETE and ARCHIVED markers, but the staged .db files
are no longer present in /data/fix. Blob contents were not independently
inventoried: local Azure CLI storage listing failed with a Python XML library
error. Validate the archived manifests before any migration.

The retained correction report adds two important caveats: its stage-2 jobs
record 17 errors, and the stage-4 adjustment pass reports only 500 symbols.
Its continuous-futures report also records days with no held front contract.
A COMPLETE marker is therefore not proof that every source series is
corrected, gap-free or continuously available; review those boundaries.

## Why only a subset appears

`charto/preview/js/universe.js` filters the payload from `/symbols`; its picker
has no fixed 500-result limit. It currently renders all matches into DOM rows,
so a larger catalogue should use windowing/pagination rather than thousands
of rows and quote requests on every search.

`dataserver.py` builds `/symbols` from symbols.json, local bar metadata, and
the local chart instrument_master when that table exists. It does not expose
Postgres's 75,947 records automatically. Production has no chart
instrument_master and no bars_15m table. The API's meta map is empty.
Production runs commit 2d7a5e0, so current checkout capabilities must not be
mistaken for completed production deployment.

The existing hydrator only knows NSE equity Parquet files and writes a full
symbol's minute history into one SQLite store. It is not a general BSE,
index, futures, continuous-series, or options loader. Existing symbols.json
contains only 500 equities. Expanding this list cannot supply absent data.

For newly minute-covered symbols, daily reads may be derived on demand; that
does not mean the stored daily screener matrix has equivalent coverage.
Production sampled RELIANCE, TCS and HDFCBANK minutes end at
**4 September 2026, 15:28 IST**. A catalogue expansion does not repair this
freshness gap.

## Current production capacity

Measured on Claudecodeforpivot, Standard_D2ads_v5:

- 2 vCPUs, approximately 7.9 GB usable RAM.
- Approximately 0.56 GB available RAM; approximately 0.84 GB swap used.
- Load averages 18.44 / 13.30 / 14.73 at the first sample.
- Azure CPU average approximately 85.7% over the sampled prior hour;
  five-minute averages reached 97.7%. This is not a controlled load benchmark.
- Serving SQLite file: 79.99 GB. Its WAL: 47.26 GB.
- Data disk: 256 GiB Premium SSD P15, 1,100 provisioned IOPS / 125 MB/s.
  Filesystem: 133 GiB used, approximately 106 GiB available.
- The VM's uncached remote-storage throughput ceiling is 82 MB/s; the disk's
  advertised 125 MB/s is not the effective ceiling for this VM configuration.

The WAL is large enough to require investigation of checkpoints, long-lived
read transactions and concurrent writers. Do not delete it: it may contain
committed data not yet checkpointed. Do not run a heavy checkpoint/import
without a planned maintenance and recovery procedure.

The existing 1 October report flags a September compromise. A current
diagnostic also returned a malformed sudoers entry. This audit does not prove
the cause of current CPU usage or establish remediation. Resolve that security
status before reusing the host; a clean rebuild is preferable to merely
resizing an unverified machine. Preserve user state separately and securely.

## What a reliable expansion needs

1. **Refresh identity and coverage.** Build the chart master with canonical
   exchange-qualified IDs, aliases, tick sizes, expiry, session clocks and
   per-timeframe first/last bars. Distinguish listed, daily-only, intraday-ready
   and live-ready. Reuse land_universe.py; it already defines these stores.
2. **Land validated history.** Check archive manifests, corrections,
   anomalies, chronological order, corporate-action adjustments, session
   boundaries and continuous-futures rolls before serving. Do not manufacture
   minute bars for daily-only series.
3. **Maintain daily and 15-minute rollups.** Current code can read bars_15m,
   but production lacks it. The inspected read path can otherwise load
   180,400 minute rows for a 3,000-bar hourly page. A rollup reduces the old
   part to roughly 12,000 rows, while preserving raw minutes for finer views.
   Use the same session bucketing for historical and incremental updates.
4. **Cache by active demand, not catalogue size.** Keep a bounded cache of
   symbol/interval/window results, invalidate on closed bars or corrections,
   and coordinate concurrent misses. The current intraday cache has 96
   entries, not 96 securities. More RAM alone does not remove Python
   resampling or the need for correct invalidation.
5. **Separate ingestion and serving.** Keep bulk imports and nightly
   reconciliation off the request path. Isolate feed/bar construction from
   REST and browser streaming; preserve one authoritative forming-bar source
   if introducing multiple workers. Prefer batched writes and incremental
   rollups over per-request computation.
6. **Keep history fresh.** Reconcile missing sessions/minutes, refresh
   instruments and contracts, handle token expiry, and measure freshness per
   series. Preserve expired-contract history for continuous intraday series.
7. **Validate end-to-end, gradually.** Equities, BSE-only listings, indices,
   futures and cross-midnight sessions; minute/daily/hourly bars, indicators,
   paging, replay, live bars and plans. Benchmark warm/cold p50/p95 latency,
   concurrency, memory, disk queueing and WAL growth before a full cutover.

The raw archive is only about 99 GB; its entire contents do not have to fit
in RAM. Catalogue count alone does not determine compute cost. Active users,
concurrent cold chart opens, requested bar depth, resampling and live tick
rate determine the hot working set and CPU demand.

## VM recommendation

**Full archive plus ongoing feeds:** provision a clean **8-vCPU / 32-GiB**
serving host (for example D8ads_v5), with a **512-GiB persistent data disk**.
Select/tune disk IOPS against the measured workload and the VM's own limits;
Premium SSD v2 is an option after checking regional/zone compatibility.
Keep backups in Blob Storage, not multiple full local database copies.

**Restricted beta / staged on-demand expansion:** 4-vCPU / 16-GiB is a
reasonable starting floor, not an assurance of capacity. Validate against
real concurrency. The current 2-vCPU host is already pressured; it should
not be the destination of another full-universe bulk load.

The existing 256-GiB disk could physically hold the roughly 99-GB source
archive with healthy WAL/checkpoint behaviour. A 512-GiB recommendation buys
growth and maintenance headroom, rather than implying every catalogue item
needs memory. An illustrative 4,947 cash series × 375 minutes × 250 sessions
adds approximately 464M minute rows/year before futures, indices or crypto;
storage growth must be calibrated from the landed schema and actual trades.

A database-engine rewrite is not required solely because there are 6,000
symbols: indexed per-symbol/time-range reads suit SQLite. Sharding and/or a
time-series/columnar engine become decisions about write concurrency,
cross-sectional scans and benchmark results, not an instrument-count rule.

## Live-feed limits and options

Kite documents 3,000 subscribed instruments per WebSocket and 3 connections
per API key. Approximately 5,968 minute-covered series could fit within that
9,000-slot ceiling in principle; continuous series reuse their underlying
contract subscriptions. Actual supported exchanges, entitlements and other
services sharing the same key must be checked. The inspected chart feed opens
one socket, so the ceiling is not equivalent to existing implemented capacity.

Kite historical candles are limited to 3 requests/second. Filling one request
per 5,968 series would already take a theoretical minimum of approximately
33 minutes, before chunking, retries and processing; it is not a substitute
for maintaining the archive ahead of chart opens.

The 52,038 options records are a separate expansion: no equivalent options
historical archive was demonstrated here, and subscribing to all of them
exceeds one key's slot ceiling. Use a scoped/on-demand chain universe or a
different entitled data service, rather than equating master rows with
chart-ready securities. Confirm data-redistribution rights independently.

## Sources and limits

- Live Azure VM/resource and Monitor queries; read-only SQL and local API
  probes, collected 8 October 2026.
- [Azure Dadsv5 specifications](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/general-purpose/dadsv5-series).
- [Kite WebSocket limits](https://kite.trade/docs/connect/v3/websocket/).
- [Kite historical API rate limits](https://kite.trade/docs/connect/v3/exceptions/#api-rate-limit).
- Existing repository research: 2026-10-01-serving-the-full-universe-live.md.
- No stress test, market-feed reconnection, database checkpoint, security
  remediation, server restart, deployment, migration or VM resize was run.
