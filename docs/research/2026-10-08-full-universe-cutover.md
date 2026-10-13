# Full minute-universe cutover: scope, budget and current blocker

User budget: INR 15,000/month for the serving VM and disks, supplied on
8 October 2026. The initial investigation below preceded deployment approval;
the clean-host rollout update at the end supersedes its deployment status.

## Verified inventory

Indexed per-instrument probes on `kite-backfill` again found 5,968
minute-covered source series: NSE cash 3,035; BSE cash 1,912; NSE indices
132; BSE indices 67; MCX indices 7; GIFT index 1; global indices 12;
NFO futures 649; BFO futures 5; MCX futures 128; NCO futures 7; CDS futures
13. These are source-series records, not unique companies or the final
canonical serving IDs. Continuous ranks and exchange aliases require the
existing master builder. The twelve global series are inventory, not approval
to expand the product's market scope. Daily-only series must not be labelled
minute-ready.

Sources remain in `/data/backfill` and `/data/backfill2`; their schemas include
`bars_15m`, `bars_1d`, adjustments and repair reports. `sessions.json` lives in
`/data/fix`. Corrected derivation coverage must be validated per instrument;
table existence or a COMPLETE marker alone is insufficient.

Re-reading the retained correction report confirmed 17 stage-2 errors
(including invalid equity tokens and unsupported continuous requests), only
500 symbols reported by stage 4, and missing-held-front days in several
futures groups. Inspect per-store derivation state before treating any of
the remaining series as correctly adjusted. Missing contract days must
remain explicit gaps, not synthetic continuous minutes.

Blob listing was verified through authenticated Storage REST, avoiding the
local Azure CLI XML-library failure. Container `kite-archive` contains twelve
DB blobs under `backfill-2026-10-01`, plus job/sample artifacts. Archived file
sizes differ from retained source-store sizes; do not assume this snapshot
contains the latest corrections. Metadata/manifests and consistency need
validation before selecting the authoritative import source.

Production commit remains `2d7a5e0`. It has no `instrument_master`,
`bars_15m` or `rollup_meta`. The data filesystem has 106 GiB available. The
existing 929-series store, large WAL, imported source copies and rollback
copy must not all be piled onto this volume without a space plan.

## Reuse the current system

`universe_fix.py` repairs documented anomalies, derives instrument sessions,
checks corporate-action factors, folds daily/15-minute bars and builds futures
roll segments. `land_universe.py` maps canonical IDs and aliases, copies those
tables and materialises continuous contracts into the dataserver's existing
schema. Do not run it directly against the live DB: it replaces master rows,
uses synchronous=OFF and can hold large transactions. Build offline.

Dataserver reads indexed symbol/time windows. Intraday resampling uses the
existing session bucket function; OHLC is first/max/min/last and volume is
summed. Multiples of 15 use older 15-minute rows behind the watermark, plus
minute rows for the fresh tail. Daily reads use stored daily history plus a
minute-derived tail; weekly/monthly fold daily rows. Preserve this derivation
and all session/expiry/adjustment semantics. Validate rollup coverage for
every eligible symbol before enabling the global watermark.

Cutover checks: canonical-ID collisions, chronological uniqueness, OHLC
validity, source timestamps, session-boundary stamps, volume/OI treatment,
daily-only exclusions, rolled-versus-raw parity, futures segment bounds,
paging, indicators, drawings and live source tags. Do not manufacture absent
minutes or label stale history as live. Test NSE, BSE-only, indices and futures
before expanding, with an explicit backup/rollback procedure.

## Budget

Central India Linux consumption rates queried from the
[Azure Retail Prices API](https://learn.microsoft.com/en-us/rest/api/cost-management/retail-prices/azure-retail-prices):

| Component | INR/month estimate |
| --- | ---: |
| D4as_v5, 4 vCPU / 16 GiB, 730 hours | 7,778.00 |
| Existing P15 LRS 256-GiB data disk | 3,648.76 |
| P4 LRS 32-GiB OS disk | 506.78 |
| Subtotal | 11,933.54 |

Retail estimates exclude taxes, bandwidth, backups, transactions, other Azure
resources and any temporary migration overlap; actual agreement/invoice rates
may differ. This is a staged beta floor, not proof of capacity for unlimited
concurrency. Benchmark cold/warm active demand and maintain budget headroom.
The eight-vCPU option alone exceeds the cap at these consumption rates.
Do not buy a reservation, use an interruptible Spot serving VM or enlarge the
disk without separately accounting for its budget impact.

## Blocking security and frontend findings

No `/tmp/.tiktouk` worker respawn was found, but `visudo -c` still fails.
Containment of the confirmed malicious workload is not an OS trust check.
Importing all history into, or merely resizing, the compromised installation
is not a reliable cutover. A clean OS rebuild is a distinct action requiring
user approval; preserve the public endpoint and verified user-state backups,
and preserve the original OS/evidence for investigation.

The screenshot's frontend error is independently corroborated: root HTML
references `main-app-d842500b7edf6ec3.js`, which returns 400 from the shell
itself on port 3000 and public HTTPS; port 5175 returns 404 for that chunk.
The public root references multiple failing chunk URLs. This is not proof
of the exact build/routing defect; capture runtime/build configuration before
fixing. A 200 homepage HTTP check was not evidence of working hydration.

Next decision: authorize a clean rebuild/cutover retaining the existing
hostname, rather than an in-place resize of the untrusted OS. Preserve the
entire user-state plane and do not copy compromised credentials into the new
runtime. Only after that decision should a validated offline serving store
be landed, benchmarked and switched into production.

## Approved clean-host rollout — 9 October, 00:35 IST

The user subsequently approved clean recovery, the new VM, shared/service
credential coordination, a temporary original-VM rollback overlap, and a
final Playwright check. Kite app-secret rotation is explicitly deferred;
using the existing app temporarily does not supply a valid day token.

- `pivot-clean-stage`: D4as_v5, 4 vCPU, 16 GiB, Ubuntu 24.04, Trusted Launch,
  non-root application services, fresh TLS certificates and separate managed
  identity. Original VM remains powered and still owns the public address.
- New P15 256-GiB serving-data disk is cloned from a retained rollback
  snapshot. The backfill OS clone is mounted read-only/noexec solely to read
  data. Do not reboot with this source OS clone attached: its filesystem UUID
  duplicates the new OS image. Detach it after successful validation.
- Persistent data mounts and fail-closed mount dependencies are installed.
  There is no SSH exposure and no automatic source reset/deploy polling on
  the fresh host.
- Both patched Next production builds pass their build/type checks. Actual
  Playwright navigation, signup and sign-in shell rendering work on the
  temporary HTTPS validation hostname. Chart rendering is not yet passed:
  the data service is awaiting offline retention/finalization.
- Domestic source audit: 5,956 minute-covered records, 5,922 bounded sample
  passes, 34 continuous placeholders, zero sampled derivation failures.
  This is a sample gate, not proof every historical row is correct. Twelve
  foreign index series were deliberately excluded.
- Bulk landing completed at 18:52:25 UTC: 6,258 instrument records and 574
  continuous records. These are not counts of unique companies, nor evidence
  all have minute coverage. Final indexed serving inventory remains pending.
- Existing caches and legacy histories are being retained offline. The
  serving inventory uses small reference inventories and indexed seeks;
  rollup cutoffs are per series, not one assumed global completeness date.
  Daily caching is bounded at 256 series. Kite transport supports three
  shards of at most 3,000 tokens, process-wide REST pacing, shared-token
  fanout and one thread-safe Twisted reactor startup.
- Full restored user plane: 16 accounts, 100 conversations, 11 layouts,
  10 workspaces, 4 alerts, one paper account and one strategy. A fresh remote
  backup was downloaded and passed SQLite integrity and durable-table
  checks. Private validation uses a separate scratch SQLite user plane.
  Final handoff must freeze old writers and capture another full-plane
  backup before activating the preserved real state; old sessions must be
  invalidated at the security boundary.
- Fresh serving PG role `pivotserve_20261008` passes actual reference-row
  reads in all three databases from the non-root runtime environment. Fresh
  model key2 and JWT secret are in a separate protected vault. Old VM's
  account-wide blob-write grant was removed; its existing backup container
  and source-read paths remain narrowly available for handoff.

Budget was rechecked against the Azure Retail Prices API: Linux D4as_v5
INR 10.6548/hour × 730 = 7,778.00, P15 3,648.76/month, E4 OS disk
253.41/month. Subtotal 11,680.17; approximately 13,782.60 including 18%
tax, before IP/bandwidth/backup/transaction charges. The temporary source
disk, validation IP and original rollback VM are migration overlap, not an
approved permanent second serving installation. No reservation or Spot VM.

Remaining boundaries: no active saved Kite session was found (two inactive
sessions, newest updated 6 September). Fresh Indian catch-up and actual live
WS/SSE verification need broker login; imported histories generally end
30 September/1 October and must not be labelled current. Old shared PG admin
password, Azure model key1 and the shared Browser signing key have not yet
been revoked. External shared PG client `103.170.152.144` remains
unidentified from its idle COMMIT/ROLLBACK activity. Public PG firewall still
allows all addresses: do not declare security recovery complete. Broker
order execution and API background jobs stay disabled.

## Serving cutover completed — 9 October 2026, approximately 12:10 IST

The canonical address now serves `pivot-clean-stage` (D4as_v5, 4 vCPU,
16 GiB): https://pivot-india.centralindia.cloudapp.azure.com/#chart.
`Claudecodeforpivot` remains running without a public IP as temporary rollback.
Its data/API writers and deploy timer were stopped for a consistent handoff.
Do not restart both copies of the real user plane at once.

Final state handoff preserved 16 accounts, 100 conversations, 11 layouts,
10 workspaces, 4 alerts, one paper account and one strategy. Sessions were
invalidated intentionally. The new real-state backup passed integrity/count
checks before the public IP moved; hourly backups are active on the clean host.
The final handoff blob is
`kite-1min/backup/cutover/pivot-state-handoff-6i29z7fd/users.db.gz`, SHA-256
`9fee3588c543ec4bd79b6e1e4e810b714f036b7768f57402ce0ba54a8ea73450`.

Chart-loading causes and fixes:

- The patched Lightweight Charts v5.2.0 bundle was Git-ignored and absent
  from the clean source archive. Delivered the exact locally patched bundle,
  checksum-verified. Clean bootstrap now fails closed if it is missing.
- The bulk backfill covers the broader universe but excludes the existing
  top-500 NSE histories. Restored all 500 from the retained data clone using
  atomic, resumable, per-symbol transactions, prioritising the default chart.
- Slow blanket legacy retention was delaying the entire data service. Deferred
  non-NSE legacy histories rather than claim migration was complete. Their
  source remains intact at `/mnt/pivot-old-data/charto_bars.db`.
- A verified `serving_inventory` avoids the daily-table scan at startup.
  Public `/symbols` reports **6,917 minute-backed series**, **7,332 listed
  instrument IDs**, and **zero missing names from the existing top-500 list**.
  These are series/instrument IDs, not unique-company or live-subscription counts.
- Removed only the failed first import's derived DB/WAL/SHM files (9,285,117,808
  bytes), after verifying no process held them open and accepted data/source
  were retained. That failed copy can be rebuilt from the retained sources.
  The resumed NSE retention completed successfully; about 35 GiB remained free.

Actual Playwright inspection passed: visible RELIANCE candles, volume and
5m → 15m interval switching, with no failed chart data/script requests on the
fresh page. The hover company link reaches `/stock/RELIANCE`; a signed-out
visitor is correctly directed to sign-in. No model/chat endpoint was invoked
for this infrastructure check. A non-blocking missing login-loader SVG was
observed; this is not a claim of zero errors across every website feature.

Sampled full chart requests after cutover (single samples, not a load test):

| Series | Interval | Bars returned | End-to-end latency |
|---|---|---:|---:|
| RELIANCE | 5m | 4,000 | 282 ms |
| HDFCBANK | 15m | 3,000 | 1,756 ms |
| NIFTY 50 | 1h | 3,000 | 718 ms |
| BSE:08AGG | 15m | 3,000 | 314 ms |

All four application services and the backup timer were active; public chart
readiness passed. Twelve focused transport/serving/finalization fixtures passed.
Temporary source OS clone was unmounted and detached before any reboot, fixing
its duplicate filesystem-UUID risk. Its disk copy was removed only after
confirming it was unattached and its retained source snapshot was `Succeeded`:
`PIVOT-BACKFILL/pivot-universe-source-20261008`. The temporary validation public
IP was also removed after canonical-address verification, avoiding permanent
migration-overlap cost. Serving VM/disk specification was not increased again.

Boundaries remain explicit: historical coverage is workable, **not proof of
current live data**. Restored NSE histories end 4 September; bulk source families
generally end 30 September/1 October. A fresh Kite login is still needed for
catch-up and genuine live WS/SSE testing. Non-NSE legacy/crypto retention, shared
credential revocation/firewall coordination and Browser signing-key rotation
remain outstanding as described above. Broker execution remains disabled.

Rollback after accepting new user writes must first stop new writers and take
a fresh whole-plane backup to restore to the rollback host. An IP-only rollback
to the older snapshot would discard those new writes. Git was not pushed.
