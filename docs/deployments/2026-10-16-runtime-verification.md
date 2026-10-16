# Production runtime verification — 16 October 2026

Serving VM: `pivot-clean-stage` in `PIVOT`. Release manager reports
`d88ffb06fdd234f84d920de2ea934c807185337a`; runtime directory remains
`/opt/pivot/releases/8ab4474f3ae7b932` because releases update its contents.
The directory name is not the currently deployed Git revision.

## Verified

- SHA-256 comparison of 1,188 tracked Python, JavaScript, TypeScript, CSS,
  HTML and JSON files across Charto, both Next apps, Pivot backend and
  Pivotted: all match local committed HEAD. Tests/archive paths were excluded.
  This is source parity, not a claim that databases, environment variables,
  installed dependencies or uncommitted local edits are identical.
- `charto`, `pivot-api`, `pivot-next`, `charto-web`, and
  `pivot-release.timer` active.
- `mail_sent` and `known_devices` exist in the live user-state database.
  Checked using Python SQLite in read-only mode; no migration needed.
- Public `/health` reports ready with fresh remote backup; `/pv/health`
  reports ok. Login, signup, embedded chart, quote and billing-plan endpoints
  return expected content. Anonymous `/pv/auth/me` correctly returns 401.
- Crypto feeds connected and receiving messages.
- Production venv `pip check`: no broken requirements. Four application units
  each report zero automatic restarts; the data volume has about 33 GiB free.

## Metadata routing repair

`/icon.svg` and `/apple-icon.png` incorrectly fell through to Charto's API.
Added a narrowly scoped shell proxy to the repository nginx config and both
active vhosts, preserving domain certificates, security rules and all other
routing. No application service restart was needed.

Reusable repair: `charto/deploy/repair_metadata_routes.py`; default is dry run,
`--apply` performs backup, nginx validation, reload and image-type probes.
A failed first attempt rolled back; the revised probes tolerate graceful
reload worker handover and pin HTTPS to this VM using curl `--resolve`.

Successful backup: `/var/lib/pivot-release/metadata-20261016T101708Z`.
Both images verified as 200 with correct content type on both production
hostnames and directly on port 3000. Their response bytes also match the local
assets. Home, login and stock pages reference the same content-hashed icon URL. Local deployment tests: 11 passed, including restoration of both vhosts on
nginx validation failure.

Rollback: restore only the two vhost files from that backup, run `nginx -t`,
then `systemctl reload nginx`. Never restore the user database for this fix.

## Boundaries

The current release manager applies source changes but does not install nginx
configuration changes. Use the targeted repair for existing hosts; the main
nginx template also contains the fix for newly provisioned configurations.
Uncommitted local changes are deliberately not copied into production.
Kite autostart began at 10:00:27 UTC following the release restart. At the
last check it was still in the pre-socket catch-up pass: zero sockets and
zero ticks. A separate read-only RELIANCE quote using the running service's
protected credentials succeeded. This proves quote access, not websocket
connectivity. No feed stop/start, gap-limit bypass or service restart was
performed during this repair.

Thread-stack inspection confirmed the Kite startup thread inside
`fill_gaps` waiting on historical-request workers, with workers in the
process-wide 0.35-second request-spacing gate. This is active, rate-limited
catch-up, not a missing credential. Starting another driver or restarting
Charto would interrupt it again. Instruments beyond the 1,440-minute repair
limit are explicitly refused and require their regular backfill workflow.

The two systemd failed units are historical: bootstrap on 8 October and
legacy NSE retention on 9 October. Retention intentionally stopped to preserve
serving-disk headroom. Neither job was rerun or its failed state hidden during
this deployment repair. Current serving processes remain active.
