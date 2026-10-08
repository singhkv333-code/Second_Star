# Full minute-universe cutover: scope, budget and current blocker

User budget: INR 15,000/month for the serving VM and disks, supplied on
8 October 2026. No resize, reimage, new billable resource, import, service
restart or production deployment was performed in this investigation.

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
