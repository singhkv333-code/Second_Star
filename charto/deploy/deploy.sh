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
