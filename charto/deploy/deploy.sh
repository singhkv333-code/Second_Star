#!/usr/bin/env bash
# Pull charto-deploy onto the VM and restart only if the backend moved.
#
# Run by charto-deploy.timer every 30s as azureuser. Idempotent and cheap: the
# common case is a fetch plus six idempotent vendor checks; neither writes.
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

# nginx's config is IN this repo and used to be applied by hand, which meant it
# was applied roughly never — the box drifted from the record for weeks, and a
# route added here read exactly like a route nobody added. apply_nginx.sh does
# the reload with a syntax check, a backup, a probe and an automatic rollback,
# so the thing that made it a human step is gone.
#
# Idempotent and cheap: two `cmp`s and exit 0 when nothing changed, which is
# every tick but the one after a config edit. Never fatal — nginx serving the
# previous config is a stale route table; a deploy that aborts here would leave
# the BACKEND unrestarted too, which is worse.
apply_nginx() {
  if ! sudo -n /usr/bin/bash "$REPO/charto/deploy/apply_nginx.sh"; then
    echo "deploy: WARNING — nginx config was NOT applied; the box is serving"
    echo "deploy: the previous route table. Run charto/deploy/check_routes.sh."
  fi
}

patch_vendor() {
  if ! python3 "$REPO/charto/preview/patch-vendor.py"; then
    echo "deploy: WARNING — vendor patches did not apply. The price scale will be"
    echo "deploy: stock (ragged labels, alert mark over the digits). This does not"
    echo "deploy: block the deploy. See charto/preview/VENDOR_PATCHES.md."
  fi
}

# The vendor bundle is ignored by Git. Even with no new commit, retry its
# idempotent patch so a previously unsupported bundle self-heals as soon as the
# patcher learns its shape.
if [ "$local_" = "$remote" ]; then
  patch_vendor
  apply_nginx
  exit 0
fi

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

# The vendored chart bundle is PATCHED (see charto/preview/VENDOR_PATCHES.md)
# and `vendor/` is gitignored — so the patched file never travels with the repo
# and the reset above cannot put it back. Nothing ran this, which is exactly how
# the box came to serve a stock bundle: the alert mark on the price scale is
# drawn inside the crosshair plate, and without §3-§4 the plate is only as wide
# as its own text, so on a five-figure price the ring sat on the leading digit.
# Every local machine looked right, because the patched file was sitting on it.
#
# Unconditionally, not only when charto/preview/ moved: the bundle is invisible
# to `git diff` (ignored), so a stale one is never in `$changed`. Idempotent —
# the common case is six substring checks and no write.
#
# A failure must NOT abort the deploy. Under `set -e` it would take the backend
# restart down with it, and a mis-sized price plate is a cosmetic regression
# where a blocked deploy is an outage. So it is loud instead of fatal.
patch_vendor
apply_nginx

# `pivot/` counts as backend too, now that it is IN the checkout.
#
# It used to be filtered out of the sparse rules entirely, so the only thing
# under this repo that could change and matter to the running server was
# charto/data/. Execution mode imports Pivot's automation engine straight off
# this disk (see charto/deploy/provision_execution.sh), and that engine is
# loaded ONCE at process start — a new step registry, a fixed validator or an
# edited prompt module lands on disk and then does nothing at all until a
# restart. A stale builder that reports itself as current is the same class of
# bug as the missing one it replaced.
if grep -qE '^(charto/data/|pivot/)' <<<"$changed"; then
  echo "deploy: backend changed, restarting charto.service"
  sudo -n /usr/bin/systemctl restart charto.service
  sleep 2
  systemctl is-active --quiet charto.service \
    && echo "deploy: charto.service active" \
    || { echo "deploy: FAILED to come back up"; exit 1; }
else
  echo "deploy: frontend only, already live (no restart)"
fi
