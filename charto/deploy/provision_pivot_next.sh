#!/usr/bin/env bash
# Give the VM Pivot's shell on :3000, reached through nginx at /app/.
#
# WHY THIS EXISTS
# ----------------
# pivot-next is the intended one app (CLAUDE.md §9 step 3): Chart · Chat ·
# Screener · Strategies · Portfolio · Paper, mounted beside Charto's chart,
# which keeps `/`. This script is the pivot-api playbook (provision_pivot_api.sh)
# applied to a Next.js process instead of a Python one — same numbered
# sections, same idempotency, same loud-failure-with-a-journalctl-tail shape.
# Read that script first if this one is confusing; it isn't trying to be
# different from it.
#
# THREE THINGS THAT ARE SPECIFIC TO NEXT AND WILL BITE IF SKIPPED
# -----------------------------------------------------------------
# 1. `/data/app` is a sparse checkout whose current list is exactly
#    `charto pivot pivotted` — `pivot-next/` is not on disk until step 1 adds
#    it. Nothing past that step has anything to build.
# 2. `basePath` is baked into the bundle at BUILD time, not read at runtime.
#    next.config.ts sets `basePath` from `NEXT_BASE_PATH`, and both the router
#    and every asset URL follow it. Build without the variable set and the
#    unit's `NEXT_BASE_PATH=/app` at runtime changes nothing already in the
#    HTML — the page loads with every `/_next/...` asset 404ing at the root.
#    So the variable MUST be present at `next build`, not only in the unit.
# 3. `output: "standalone"` produces `.next/standalone/server.js`, a server
#    that carries only the node_modules the app actually imports — but it does
#    NOT copy `.next/static` or `public/` into that tree, because Next assumes
#    a container build copies those in as a separate Docker layer. There is no
#    Docker layer here, so step 4 is the copy that layer would have done. Skip
#    it and the server answers HTML with every CSS/JS/image/font 404ing.
#
# MEMORY: 2 vCPU / 7.9 GB, genuinely tight (see pivot-next.service's own
# comment for the budget). `next build` is the heaviest thing that runs on
# this box, so it gets the same `--max-old-space-size` cap deploy.sh's
# rebuild_web() uses for the other Next app here, for the same reason: an
# uncapped build competing with the live chart process is how the chart goes
# down for a frontend rebuild that has nothing to do with it.
#
# IDEMPOTENT. Re-running on a provisioned box is a few checks, maybe a
# rebuild, and a restart — never a failure.
#
#   sudo bash /data/app/charto/deploy/provision_pivot_next.sh
#
set -euo pipefail

REPO=/data/app
PN="$REPO/pivot-next"
UNIT=pivot-next.service
OWNER="$(stat -c %U "$REPO")"

say() { printf '\n\033[1m== %s\033[0m\n' "$*"; }

# npm, git and the build itself must run as the repo owner, not root — a
# root-owned node_modules or .next would break every future deploy.sh run,
# which restarts services but never chowns. -H so npm's cache and config
# resolve against the owner's HOME, not root's.
run_as_owner() { sudo -u "$OWNER" -H bash -c "$1"; }

# ── 1 · the source ───────────────────────────────────────────────────────────
# pivot-next is not in the sparse-checkout list this box was provisioned with.
# `sparse-checkout add` is set-like — adding an already-present path is a
# no-op, not an error — so this is safe to run on every re-run.
say "sparse-checkout"
run_as_owner "cd '$REPO' && git sparse-checkout add pivot-next"
test -f "$PN/package.json" || {
  cat <<MSG
   FAILED: $PN/package.json missing after sparse-checkout add.

   Either the branch checked out here does not contain pivot-next/, or the
   working copy is not in sparse-checkout cone mode and 'add' silently did
   nothing. Run 'git sparse-checkout list' on the box and compare against
   'git ls-tree -d HEAD pivot-next' before re-running.
MSG
  exit 1
}
echo "   pivot-next/package.json present"

# ── 2 · dependencies ─────────────────────────────────────────────────────────
# Same guard as deploy.sh's rebuild_web(): an `npm ci` that dies partway (an
# OOM kill on this box is not hypothetical) leaves the tree it was replacing
# behind as node_modules.rollback/, and a later `tsc`/`next build` type-checks
# straight through it — a "type error" in a vendor's own TypeScript, in a file
# nobody here touched, that reads as a broken source tree when the tree is
# fine. Removed before AND after, so neither a leftover from a previous failed
# run nor one from this run's own failure survives to confuse the next step.
say "dependencies"
rm -rf "$PN/node_modules.rollback"
if ! run_as_owner "cd '$PN' && npm ci --no-audit --no-fund" \
    > /tmp/pivot_next_install.log 2>&1; then
  rm -rf "$PN/node_modules.rollback"
  echo "   FAILED: npm ci"
  tail -20 /tmp/pivot_next_install.log
  exit 1
fi
rm -rf "$PN/node_modules.rollback"
echo "   installed"

# ── 3 · build ─────────────────────────────────────────────────────────────────
# NEXT_BASE_PATH must be set HERE, not only in the unit — see the header. Do
# NOT delete .next before building, for the same reason rebuild_web() doesn't:
# deleting it up front means a failed build leaves the unit nothing to serve,
# and it restart-loops until a human notices. Move the last good build aside
# instead and put it back if the new one never lands, so a broken build costs
# a stale bundle, not an outage. (A leading dot keeps .next.prev out of
# tsconfig's wildcards, same as charto/web's .next.prev.)
#
# SKIP_LINT is available in next.config.ts but is NOT set here on purpose —
# a build that fails lint should fail loudly on this box, not silently ship.
say "build"
rm -rf "$PN/.next.prev"
if [ -d "$PN/.next" ]; then
  mv "$PN/.next" "$PN/.next.prev"
fi
if ! run_as_owner "cd '$PN' && NEXT_TELEMETRY_DISABLED=1 NEXT_BASE_PATH=/app \
    NODE_OPTIONS=--max-old-space-size=1536 npx next build" \
    > /tmp/pivot_next_build.log 2>&1; then
  rm -rf "$PN/.next"
  if [ -d "$PN/.next.prev" ]; then
    mv "$PN/.next.prev" "$PN/.next"
  fi
  echo "   FAILED: build (previous bundle left in place, if one existed)"
  tail -20 /tmp/pivot_next_build.log
  exit 1
fi
rm -rf "$PN/.next.prev"
echo "   built"

test -f "$PN/.next/standalone/server.js" || {
  echo "   FAILED: .next/standalone/server.js missing after a successful build."
  echo "   Check next.config.ts still sets output: \"standalone\"."
  exit 1
}

# ── 4 · standalone assets ────────────────────────────────────────────────────
# The step people forget. `output: "standalone"` bundles only server code and
# the node_modules it actually imports; it assumes a container build copies
# .next/static and public/ in as a separate layer. There is no layer here, so
# this copy IS that step. Skipped, the unit serves HTML whose every asset
# 404s — the page loads, blank, console full of red. Removed and re-copied
# fresh every build so a stale asset from a previous bundle can never be
# served under a new build's HTML (hashed filenames usually save you; do not
# rely on usually).
say "standalone assets"
rm -rf "$PN/.next/standalone/.next/static" "$PN/.next/standalone/public"
mkdir -p "$PN/.next/standalone/.next"
cp -a "$PN/.next/static" "$PN/.next/standalone/.next/static"
if [ -d "$PN/public" ]; then
  cp -a "$PN/public" "$PN/.next/standalone/public"
fi
chown -R "$OWNER:$OWNER" "$PN/.next/standalone"
echo "   copied .next/static and public/ into .next/standalone"

# ── 5 · the unit ──────────────────────────────────────────────────────────────
say "$UNIT"
SRC="$REPO/charto/deploy/$UNIT"
DST="/etc/systemd/system/$UNIT"
test -f "$SRC" || { echo "   FAILED: $SRC missing"; exit 1; }
if cmp -s "$SRC" "$DST"; then
  echo "   unchanged"
else
  install -m 0644 "$SRC" "$DST"
  systemctl daemon-reload
  echo "   installed"
fi

# ── 6 · sudoers ───────────────────────────────────────────────────────────────
# deploy.sh runs as $OWNER and will need to restart this unit on a pivot-next
# change, the same way it already does for charto-web.service and
# pivot-api.service. Without this line that restart is a silent permission
# failure — the box keeps serving the old bundle and nothing in the deploy log
# says so unless someone thinks to check.
say "sudoers"
SUDOF=/etc/sudoers.d/pivot-next
LINE="$OWNER ALL=(root) NOPASSWD: /usr/bin/systemctl restart $UNIT, /usr/bin/systemctl start $UNIT, /usr/bin/systemctl stop $UNIT"
if [ -f "$SUDOF" ] && grep -qF "$LINE" "$SUDOF"; then
  echo "   already granted"
else
  printf '%s\n' "$LINE" > "$SUDOF"
  chmod 0440 "$SUDOF"
  visudo -cf "$SUDOF" >/dev/null || { rm -f "$SUDOF"; echo "   FAILED: bad sudoers"; exit 1; }
  echo "   granted"
fi

# ── 7 · start, and prove it answers ──────────────────────────────────────────
say "start"
systemctl enable --quiet "$UNIT" 2>/dev/null || true
systemctl restart "$UNIT"
for _ in $(seq 1 45); do
  curl -fsS --max-time 3 http://127.0.0.1:3000/app >/dev/null 2>&1 && break
  sleep 2
done
systemctl is-active --quiet "$UNIT" \
  || { echo "   FAILED to come up"; journalctl -u "$UNIT" -n 40 --no-pager; exit 1; }
curl -fsS --max-time 5 http://127.0.0.1:3000/app >/dev/null \
  || { echo "   up but /app does not answer"; journalctl -u "$UNIT" -n 40 --no-pager; exit 1; }
echo "   active, /app answering on 127.0.0.1:3000"

# Loopback only. A listener on 0.0.0.0 bypasses nginx and its rate limit —
# the unit sets HOSTNAME=127.0.0.1 for exactly this reason.
if ss -ltn | grep -q '0\.0\.0\.0:3000'; then
  echo "   WARNING: bound to 0.0.0.0 — the unit's HOSTNAME env did not take"
fi

# Through nginx, the same way apply_nginx.sh's own probe works: --resolve pins
# the public hostname to loopback so TLS and server_name both match what a
# visitor gets. This is a WARNING, not a hard failure — nginx-charto.conf
# already carries the /app routes (checked while writing this script), but
# this script has no way to know whether that config has been applied to
# THIS box yet, and a deploy of the nginx side is out of scope here.
say "nginx /app/"
HOSTNAME_="$(grep -m1 -oE 'server_name[[:space:]]+[^;]+' \
  "$REPO/charto/deploy/nginx-charto.conf" 2>/dev/null | awk '{print $2}')"
HOSTNAME_="${HOSTNAME_:-pivot-india.centralindia.cloudapp.azure.com}"
code="$(curl -sk -o /dev/null -w '%{http_code}' --max-time 8 \
  --resolve "$HOSTNAME_:443:127.0.0.1" "https://$HOSTNAME_/app/" 2>/dev/null)"
if [ "$code" = 200 ]; then
  echo "   /app/ answering through nginx"
else
  echo "   WARNING: /app/ returned '$code' through nginx."
  echo "   The service itself is up (see above); this means nginx has not"
  echo "   picked up the /app route yet. Run charto/deploy/apply_nginx.sh"
  echo "   (or wait for deploy.sh's next 30s tick) and re-probe."
fi

say "done"
cat <<SUMMARY
   pivot-next is provisioned:
     1. pivot-next/ added to the sparse checkout at $REPO
     2. dependencies installed with npm ci (owner: $OWNER)
     3. built with NEXT_BASE_PATH=/app baked in at build time
     4. .next/static and public/ copied into .next/standalone
     5. $UNIT installed to /etc/systemd/system
     6. $OWNER granted NOPASSWD restart/start/stop of $UNIT
     7. $UNIT active and answering on 127.0.0.1:3000/app
SUMMARY
