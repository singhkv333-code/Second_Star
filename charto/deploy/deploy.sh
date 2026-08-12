#!/usr/bin/env bash
# Pull charto-deploy onto the VM and restart only if the backend moved.
#
# Run by charto-deploy.timer every 30s as azureuser. Idempotent and cheap: the
# common case is a fetch that finds nothing, which costs one HTTPS round trip
# and exits before touching the working tree.
#
# Two rules here are load-bearing, and both exist because of what lives in this
# directory alongside the code:
#
#   NEVER `git clean -x`. /data/app/pivot/.env holds AZURE_KEY, and
#   dataserver.py resolves it as parents[2]/pivot/.env — it is the only reason
#   that directory exists. It is gitignored, so fetch and reset leave it alone,
#   and `clean -x` would delete it and take the service down with it. The two
#   .db symlinks (-> /data/charto_bars.db, 29 GB) would go the same way.
#
#   RESTART ONLY FOR charto/data/. nginx serves charto/preview straight off
#   disk, so a frontend change is live the moment the file lands — restarting
#   for it would kill in-flight SSE streams and drop the warm page cache on a
#   29 GB SQLite for no reason (cold reads then cost 5-6.5s, measured).
set -euo pipefail

BRANCH="${CHARTO_DEPLOY_BRANCH:-charto-deploy}"
REPO="${CHARTO_DEPLOY_REPO:-/data/app}"
cd "$REPO"

git fetch --quiet origin "$BRANCH"

remote="$(git rev-parse "origin/$BRANCH")"
local_="$(git rev-parse HEAD 2>/dev/null || echo none)"
[ "$local_" = "$remote" ] && exit 0

if [ "$local_" = none ]; then
  changed="charto/data/"            # first deploy: assume the backend moved
else
  changed="$(git diff --name-only "$local_" "$remote")"
fi

echo "deploy: ${local_:0:8} -> ${remote:0:8} ($(git log -1 --format=%s "$remote"))"
git reset --hard --quiet "$remote"

# AppleDouble litter from the era of scp-ing off a Mac: 508 of them at the time
# this was written. Untracked and harmless, but they make `git status` useless.
find "$REPO/charto" -name '._*' -type f -delete 2>/dev/null || true

if grep -q '^charto/data/' <<<"$changed"; then
  echo "deploy: backend changed, restarting charto.service"
  sudo -n /usr/bin/systemctl restart charto.service
  sleep 2
  systemctl is-active --quiet charto.service \
    && echo "deploy: charto.service active" \
    || { echo "deploy: FAILED to come back up"; exit 1; }
else
  echo "deploy: frontend only, already live (no restart)"
fi
