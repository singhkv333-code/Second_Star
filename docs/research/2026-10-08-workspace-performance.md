# Workspace performance investigation — 2026-10-08

## Measured causes

Read-only Azure diagnostics on `Claudecodeforpivot` found load averages
20.22 / 15.17 / 15.41 on a two-vCPU VM, 239 MB available memory and 1,231 MB
swap in use. Multiple `wp2s-worker` executables and `wp2s_crack.py` Python
processes ran from `/tmp/.tiktouk`, outside the application checkout. Treat
this as a suspected compromise, not a normal chart capacity problem.
An application `npm ci` was also using about 831 MB RSS during deployment.

The market database WAL was 47.26 GB. This is a separate investigation item,
not permission to delete it or run a blocking checkpoint on the live service.

Loopback requests during that pressure:

| Request | Duration |
| --- | ---: |
| Symbols | 2.098 s |
| Two quotes | 4.667 s |
| TCS 15m, 300 bars | 1.669 s |
| Same bars immediately repeated | 0.017 s |

Existing backend caching works on repeat requests. Host contention remains
the priority. Increasing capacity on an untrusted machine is not remediation.
Microsoft documents abnormal resource use as a possible compromised-process
indicator: [IaaS security guidance](https://learn.microsoft.com/en-us/azure/security/fundamentals/iaas).

## Local fixes

- Share simultaneous identical bar, indicator and symbol requests. Consumers
  receive independent response bodies; failures are not retained.
- Cache bar/indicator head requests for one second, older `to` pages for
  thirty seconds, symbols for sixty seconds. Cap at 64 entries / 8 MiB of
  estimated response-body storage; use access-order eviction.
- Separate request keys by full URL, request headers and credentials mode.
  No persistent response cache. Abortable requests bypass sharing/caching.
- Do not cache quotes, user state, sessions, paper actions or alerts. Existing
  live stream remains unchanged; no new model selection rules or geometry.
- Allow only one hover prefetch at a time, and none while changing interval
  or when the document is hidden.
- Reject superseded interval responses so they cannot overwrite the current
  chart or release its loading latch.

## Verification and boundaries

`node --test charto/preview/tests/net-cache.test.cjs`: five passing tests,
including a twelve-consumer burst producing exactly one network request,
session isolation, TTL expiry, eviction and recovery after failure.
Syntax and diff checks passed. Local workspace HTTP path returned 200 in
0.027 seconds and served the modified cache code; local bars measured 39 ms,
then 1.7 ms on repeat. These are single diagnostic samples, not a load test.

Interactive visual verification remains unavailable because the browser tool
denied access. No alternate browser bypass was attempted. Preview script
versions were bumped. Local changes are not pushed or deployed by this patch.

The user separately authorized containment of the verified suspicious
workload, preserving evidence. Containment is not proof of eradication:
assess persistence, credentials and entry point before trusting the host.

## Authorized containment result

Preserved process metadata, file hashes, a workload archive and crontab
backups in root-only directory `/root/pivot-incident-antw97ej` on the VM.
Suspended fourteen exact-path workers first. Removed two confirmed
`azureuser` cron entries referencing the workload, then terminated those
same verified workers. No application service, database or workload file
was deleted. Cron configuration can be recovered from the evidence backup;
do not restore the malicious entries during ordinary recovery.

Immediate follow-up found zero matching workers. Available RAM was 1,316 MB.
Symbols measured 493 ms, two quotes 57 ms, and the same bar request 220 ms.
These before/after observations do not prove complete eradication or establish
a production latency SLA. A further respawn/service check is required.

The subsequent check at VM time 15:16:23 UTC found no matching workers and
one-minute load 0.28 (down from 20.22). All six application services were
active. Loopback nginx page probes returned HTTP errors (status not captured);
they did not use the public hostname or HTTPS. Public HTTPS verification
succeeded: homepage 200 / 195 ms,
company page 200 / 2.11 s, symbols 200 / 385 ms. No interactive visual check
or proof of complete security recovery is implied by these HTTP checks.
