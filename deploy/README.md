# deploy/

How the production VM runs this repo, and the files it runs from.

## The box

| | |
|---|---|
| VM | `pivot-clean-stage` (resource group `PIVOT`, Central India). No ssh: use `az vm run-command invoke`, which runs as root. |
| Release | a trimmed copy of the repo at the path in `/etc/pivot/stage-release` (`/opt/pivot/releases/<sha16>`). No git on the box. |
| Data | `/srv/pivot-data` (noexec): `charto_bars.db`, `charto_users.db`, `footprint.db`, backups. The file names predate the rename and are reached through `PIVOT_DB` / `PIVOT_USERS_DB`; they were deliberately not renamed. |
| Env | `/etc/pivot/runtime.env` (+ `razorpay-test.env`, `referrals.env`). The code still accepts the retired `CHARTO_*` names (`dataserver/envcompat.py`). |
| Hosts | `app.pivotnow.in` (`nginx/pivotnow-app.conf`) and `pivot-india.centralindia.cloudapp.azure.com` (`nginx/pivot-clean.conf`). `www.pivotnow.in` is `site/` on Vercel, released by hand (see `docs/deployments/2026-10-13-pivotnow-domain.md`). |

## Services (`systemd/`)

| Unit | Runs | Port |
|---|---|---|
| `pivot-dataserver` | `dataserver/dataserver.py` | 5174 |
| `pivot-api` | `uvicorn backend.main:app` with `PYTHONPATH=<release>/api` | 8000 |
| `pivot-app` | `app/.next/standalone/server.js` | 3000 |
| `pivot-site` | `site/.next/standalone/server.js` (`/paper`, `/strategies`, `/brokers`, `/api/waitlist`) | 5175 |
| `pivot-cache` | redis (`/etc/redis/pivot-clean.conf`) | |
| `pivot-backup.timer` | `backup_users.sh` hourly: the users DB to blob | |
| `pivot-release.timer` | `pivot_release.py` every minute | |

`@RELEASE@` in these files and in `nginx/` stands for the release directory.
nginx serves `chart/` straight from the release at `/` and `/chart-app/`.

## Deploying

`production-live` on GitHub **is** what the box runs. `pivot-release.timer`
fetches it, diffs against the deployed commit, applies only the changed paths
the release carries, builds a changed Next app beside the live one, restarts
what moved, health-checks, and unwinds on failure. Pushing the branch deploys;
moving it back rolls back. Before pushing an `app/` or `site/` change, run
`npx eslint app components lib --quiet` and a clean `next build`: the box lints.

The deployer does **not** apply nginx, systemd, or Python packages. Change those
by hand: back up the file, install it, `nginx -t` / `systemctl daemon-reload`,
reload, probe with `check_routes.sh`, restore the backup on any failure.

The deployer is installed at `/usr/local/lib/pivot-release/pivot_release.py`
and never updates itself. Copy a new version in deliberately.

## The layout cutover (once)

The box still runs the old layout (`charto/data`, `charto/preview`,
`charto/web`, `pivot/`, `pivot-next/`, `pivotted/`). The first commit with the
new folders must go through `cutover_layout.sh`, not through `production-live`,
or the old deployer will delete the dataserver from the release. The script's
header has the exact order: push the branch under its own name, `plan`, then
`apply` (it builds beside, switches units, env, nginx, pointer and deployer
together, health-checks, and rolls everything back on failure), and only then
move `production-live` to the same commit.

## Other files

| File | What |
|---|---|
| `backup_users.sh`, `verify_backup_restore.sh` | the hourly users-DB backup and its restore check |
| `check_routes.sh` | probes every nginx route from outside and says which service answered |
| `load_check.py` | read-only concurrency smoke test against a host |
| `indexnow_submit.py` | submits the public stock pages to IndexNow |
| `provision_browser.sh` | builds and deploys `browser/` (remote Chromium + SearXNG) to ACI |
| `alerts-mail.env.example` | the mail settings the alert emails need |
