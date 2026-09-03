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

# nginx serves charto/preview directly, but /stock and /_next are a compiled
# Next.js app on :5175. A git reset updates its source without updating the
# running bundle. Track the web TREE (not the repository commit) beside the
# build so backend/chart-only commits do not trigger unnecessary Next builds,
# while a missed restart self-heals on the next 30-second poll.
web_tree="$(git rev-parse "$remote:charto/web" 2>/dev/null || echo missing)"
# Hash the script that is ACTUALLY executing. During a pull, `remote` already
# points at the incoming version while this process still runs the old one; a
# remote-derived key would let an old failure suppress the new deployer's first
# attempt before that new script ever executes.
deploy_blob="$(git hash-object "$0" 2>/dev/null || echo missing)"
web_attempt="$web_tree-$deploy_blob"
web_revision_file="$REPO/charto/web/.next/charto-git-tree"
web_failed_file="$REPO/charto/web/.next/charto-git-failed-attempt"
web_status_file="$REPO/charto/preview/deploy-runtime.txt"

web_needs_build() {
  [ "$web_tree" != missing ] \
    && { [ ! -f "$web_revision_file" ] \
      || [ "$(cat "$web_revision_file" 2>/dev/null || true)" != "$web_tree" ]; } \
    && { [ ! -f "$web_failed_file" ] \
      || [ "$(cat "$web_failed_file" 2>/dev/null || true)" != "$web_attempt" ]; }
}

record_web_failure() {
  stage="$1"
  printf 'failed %s %s\n' "$stage" "$web_tree" > "$web_status_file"
  printf '%s\n' "$web_attempt" > "$web_failed_file"
}

failure_kind() {
  log_file="$1"
  if grep -Eqi 'ENOSPC|no space left on device' "$log_file"; then
    printf disk
  elif grep -Eqi 'heap out of memory|allocation failed|SIGKILL|signal[^[:alnum:]]+9|worker (process )?(exited|terminated) unexpectedly|(^|[^0-9])137([^0-9]|$)|(^|[[:space:]])killed([[:space:]]|$)' "$log_file"; then
    printf memory
  elif grep -Eqi 'EACCES|EPERM|permission denied' "$log_file"; then
    printf permissions
  elif grep -Eqi 'cannot find module|module not found|ERESOLVE' "$log_file"; then
    printf dependency
  elif grep -Eqi 'type error' "$log_file"; then
    printf typecheck
  elif grep -Eqi 'failed to collect page data' "$log_file"; then
    printf page-data
  elif grep -Eqi 'error occurred prerendering|prerender-error' "$log_file"; then
    printf prerender
  elif grep -Eqi 'failed to compile|build error occurred' "$log_file"; then
    printf compile
  else
    printf unknown
  fi
}

rebuild_web() {
  echo "deploy: company frontend changed, rebuilding charto/web"
  printf 'building %s\n' "$web_tree" > "$web_status_file"

  # Production normally owns :5175 through charto-web.service. The old helper
  # tried to SIGKILL that process as azureuser, ignored EPERM, then launched a
  # second server which could never bind. Build while the current service is
  # still available, then let systemd replace it atomically. Keep start.sh as
  # the fallback for older/manual installations without the unit.
  if systemctl cat charto-web.service >/dev/null 2>&1; then
    printf 'installing %s\n' "$web_tree" > "$web_status_file"
    if ! (cd "$REPO/charto/web" \
      && npm ci --no-audit --no-fund > /tmp/charto_web_install.log 2>&1); then
      record_web_failure "install-$(failure_kind /tmp/charto_web_install.log)"
      echo "deploy: company frontend dependency install FAILED"
      tail -20 /tmp/charto_web_install.log
      return 1
    fi
    printf 'building %s\n' "$web_tree" > "$web_status_file"
    if ! (cd "$REPO/charto/web" \
      && NEXT_TELEMETRY_DISABLED=1 NODE_OPTIONS=--max-old-space-size=1536 \
        npx next build > /tmp/charto_web_build.log 2>&1); then
      record_web_failure "build-$(failure_kind /tmp/charto_web_build.log)"
      echo "deploy: company frontend build FAILED"
      tail -20 /tmp/charto_web_build.log
      return 1
    fi
    printf 'restarting %s\n' "$web_tree" > "$web_status_file"
    if ! sudo -n /usr/bin/systemctl restart charto-web.service; then
      record_web_failure restart
      echo "deploy: charto-web.service restart FAILED"
      return 1
    fi
    sleep 2
    if ! systemctl is-active --quiet charto-web.service; then
      record_web_failure inactive
      echo "deploy: charto-web.service is not active"
      return 1
    fi
  elif ! "$REPO/charto/web/start.sh"; then
    record_web_failure legacy-start
    return 1
  fi

  printf '%s\n' "$web_tree" > "$web_revision_file"
  rm -f "$web_failed_file"
  printf 'ready %s\n' "$web_tree" > "$web_status_file"
  echo "deploy: company frontend active ($web_tree)"
}

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
  if web_needs_build; then
    rebuild_web
  fi
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

if web_needs_build; then
  rebuild_web
fi

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
  echo "deploy: static frontend already live; company frontend checked separately"
fi
