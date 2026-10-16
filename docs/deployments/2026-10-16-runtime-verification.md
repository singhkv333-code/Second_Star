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

## UI deployment — 16 October, 16:08 IST

Owner requested VM deployment of the prompt-bar and market-card changes.
Deployed `31560c10d8b7f1ddfc921734b379ad9530bc90d4` through the existing
release manager using a SHA-256 verified private Git bundle. Only the 13
reviewed files in its explicit plan were installed; unrelated working-tree
edits were excluded. No Git remote was pushed.

- Replacement Next build completed before the swap; only `pivot-next`
  restarted. Charto/Kite and the other backend processes were not restarted.
- State records `manual_deploy: true`. A tested, narrowly scoped guard was
  installed in the release manager: while this flag is present, the timer
  refuses an older or divergent published branch instead of reverting the
  manual release. Normal branch-controlled rollback behavior is unchanged
  for releases without that flag. Publish the reviewed commits before future
  branch-based releases; do not remove the flag to silently discard them.
- Production favicon URL is now `/icon.svg?161c58b947d2ccf0`. The source SVG
  revision changes Next's content hash, avoiding the previously failed cached
  URL. Home, login and stock pages all reference it. HTTP 200, correct SVG
  content type, and byte-for-byte comparison with local source passed.
- Verified in the existing signed-in Chrome session: both market cards are
  315.5px high; the brief has a 195px scroll region containing 486px of content;
  internal scrolling works. Alien-handle dragging changed the bar's position;
  reset returned it to the default 40px bottom clearance. No message was sent.
- The signed-in DOM contains the new favicon reference. Browser tooling adds
  its own temporary inspection badge, preserving the app URL in
  `data-codex-original-favicon-href`; it must not be mistaken for product code.
- `/health` and `/pv/health` pass after deployment. Metadata route repair
  reports both vhosts already match. Deployment unit reports success.
- Validation before deployment: 13 deployment/route tests, five focused UI
  tests, and TypeScript checking passed.

Runtime build/source rollback is retained by the release manager under its
`backups/31560c10d8b7` and `.next.before-31560c10d8b7` paths. The release-manager
script before the manual guard is saved as
`/var/lib/pivot-release/pivot_release.before-ui-20261016.py`.
