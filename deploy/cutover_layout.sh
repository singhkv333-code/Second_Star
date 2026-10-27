#!/usr/bin/env bash
# One-time cutover of pivot-clean-stage to the repo's folder layout.
#
#   sudo bash cutover_layout.sh plan     <commit>   check everything, change nothing
#   sudo bash cutover_layout.sh apply    <commit>   build beside, switch, verify
#   sudo bash cutover_layout.sh rollback <backup>   put the old release back
#
# WHY THIS EXISTS
# ---------------
# The release on the VM still has the old tree (charto/data, charto/preview,
# charto/web, charto/deploy, pivot/, pivot-next/, pivotted/). The deployer
# (pivot_release.py) applies only paths under the tops it knows, and its units
# run release/charto/data/dataserver.py. Pushed as an ordinary commit, the new
# layout would make it DELETE every charto/ file from the release and skip the
# new paths as "outside the release". So the move is made once, by hand:
#
#   1. build a fresh release for <commit> BESIDE the live one (nothing live moves)
#   2. stop the timer, back up every file this touches
#   3. env: PYTHONPATH -> new api/, CHARTO_* keys -> PIVOT_* (the code still
#      accepts the old names, see dataserver/envcompat.py)
#   4. units: charto -> pivot-dataserver, charto-web -> pivot-site,
#      pivot-next -> pivot-app, charto-backup -> pivot-backup (+ their drop-ins
#      and state dirs); pivot-api restarts on the new PYTHONPATH
#   5. nginx: the deploy/nginx/ copies, rendered for the new release
#   6. pointer, deployer and its state move to the new release; timer resumes
#
# Any failure after step 2 unwinds everything (the same as `rollback`). The old
# release directory is left in place, untouched, for as long as you want it.
#
# ORDER MATTERS. Push <commit> on its OWN branch, never to production-live
# first: the old deployer would try to apply it and delete the dataserver.
#   git push origin cleanup/2026-10-17            (from a laptop, when approved)
#   sudo -u pivot-build git --git-dir /opt/pivot/repo.git fetch origin \
#        +refs/heads/cleanup/2026-10-17:refs/heads/cleanup/2026-10-17
#   sudo -u pivot-build git --git-dir /opt/pivot/repo.git show <sha>:deploy/cutover_layout.sh > /root/cutover_layout.sh
#   bash /root/cutover_layout.sh plan <sha>
# then run `apply` detached, since it builds two Next apps (~10-20 min):
#   systemd-run --unit=pivot-cutover --property=TimeoutStartSec=3600 \
#     /bin/bash /root/cutover_layout.sh apply <sha>
#   journalctl -fu pivot-cutover
# and only after it prints LIVE, move production-live to the same <sha>. The
# state file already names it, so the deployer finds nothing to do.
#
# Data is not moved or renamed: /srv/pivot-data/charto_bars.db and
# charto_users.db keep their names and are reached through PIVOT_DB /
# PIVOT_USERS_DB exactly as before.
set -Eeuo pipefail

MODE="${1:-}"; ARG="${2:-}"
REPO_URL="${PIVOT_RELEASE_REPO:-https://github.com/singhkv333-code/Second_Star.git}"
MIRROR=/opt/pivot/repo.git
POINTER=/etc/pivot/stage-release
STATE=/var/lib/pivot-release/state.json
DEPLOYER=/usr/local/lib/pivot-release/pivot_release.py
PY=/opt/pivot/venv/bin/python
BUILD_USER=pivot-build
UNITDIR=/etc/systemd/system

# The live nginx files this was written against (sha256, 2026-10-17). If the box
# was edited since, re-copy them into deploy/nginx/ first, or set FORCE_NGINX=1.
declare -A NGINX_HASH=(
  [/etc/nginx/sites-available/pivotnow-app]=aa04bc4d9dd648075a80257dfa2d923a7b9948a7eb1b7c331c9f98e394a601cb
  [/etc/nginx/sites-available/pivot-clean]=c3684321a930126362a4302c8f8d48a3a826b30673eaa183c74180feede6417e
  [/etc/nginx/conf.d/charto-ratelimit.conf]=1b85056ce453abc6bb4e5ff21a1885c60d5d8247da7da44d198a7495f98c671d
)
# old unit -> new unit, and the state dir each one owns under /var/lib/pivot
declare -A UNIT_NEW=([charto]=pivot-dataserver [charto-web]=pivot-site [pivot-next]=pivot-app)
declare -A STATE_NEW=([charto]=dataserver [charto-web]=site [pivot-next]=app)
# old release path -> new release path, for files the release carries that git does not
map_path() {
  case "$1" in
    charto/data/*)    echo "dataserver/${1#charto/data/}" ;;
    charto/preview/*) echo "chart/${1#charto/preview/}" ;;
    charto/web/*)     echo "site/${1#charto/web/}" ;;
    charto/deploy/*)  echo "deploy/${1#charto/deploy/}" ;;
    pivot-next/*)     echo "app/${1#pivot-next/}" ;;
    pivotted/*)       echo "research/${1#pivotted/}" ;;
    pivot/*)          echo "api/${1#pivot/}" ;;
    *)                echo "" ;;
  esac
}
NEXT_ENV=(NEXT_TELEMETRY_DISABLED=1 PIVOT_BACKEND_ORIGIN=http://127.0.0.1:8000
          PIVOT_DATASERVER_ORIGIN=http://127.0.0.1:5174 NEXT_PUBLIC_PIVOT_API_BASE=/pv/api
          NEXT_PUBLIC_PIVOT_WS_BASE=wss://pivot-india.centralindia.cloudapp.azure.com/pv/api
          NODE_OPTIONS=--max-old-space-size=3072)

say()  { printf '%s %s\n' "$(date -u +%H:%M:%S)" "$*"; }
die()  { say "ABORT: $*"; exit 1; }
as_build() { setpriv --reuid="$BUILD_USER" --regid="$BUILD_USER" --init-groups env HOME="$(getent passwd "$BUILD_USER" | cut -d: -f6)" "$@"; }
git_m() { as_build git --git-dir "$MIRROR" "$@"; }
healthy() {  # url, seconds
  local end=$(( $(date +%s) + ${2:-180} ))
  until curl -fsS -o /dev/null --max-time 5 "$1"; do
    [ "$(date +%s)" -ge "$end" ] && return 1; sleep 3
  done
}

preflight() {
  [ "$(id -u)" = 0 ] || die "run as root"
  local name; name=$(curl -fsS -H Metadata:true --max-time 5 \
    'http://169.254.169.254/metadata/instance/compute/name?api-version=2021-02-01&format=text' || true)
  [ "$name" = pivot-clean-stage ] || die "this is '$name', not pivot-clean-stage"
  OLD=$(cat "$POINTER")
  [ -f "$OLD/charto/data/dataserver.py" ] || die "$OLD is not the old layout (already cut over?)"
  DEPLOYED=$(python3 -c "import json;print(json.load(open('$STATE'))['deployed'])")
  for f in "${!NGINX_HASH[@]}"; do
    local have; have=$(sha256sum "$f" | cut -d' ' -f1)
    if [ "$have" != "${NGINX_HASH[$f]}" ] && [ "${FORCE_NGINX:-0}" != 1 ]; then
      die "$f changed since deploy/nginx/ was copied from it; re-copy it (or FORCE_NGINX=1)"
    fi
  done
  local branch="${CUTOVER_BRANCH:-cleanup/2026-10-17}"
  say "fetching $branch"
  git_m fetch --quiet --filter=blob:none origin "+refs/heads/$branch:refs/heads/$branch"
  git_m merge-base --is-ancestor "$COMMIT" "refs/heads/$branch" || die "$COMMIT is not on $branch"
  # production-live must not already name it: the old deployer would be applying it
  local live; live=$(git_m ls-remote origin refs/heads/production-live | cut -f1)
  if [ "$live" = "$COMMIT" ] && systemctl is-active --quiet pivot-release.timer; then
    die "production-live already names $COMMIT while the timer runs; stop pivot-release.timer first"
  fi
  git_m cat-file -e "$COMMIT:dataserver/dataserver.py" || die "$COMMIT has no dataserver/dataserver.py"
  git_m merge-base --is-ancestor "$DEPLOYED" "$COMMIT" || die "$COMMIT does not descend from the deployed $DEPLOYED"
  NEW=/opt/pivot/releases/${COMMIT:0:16}
  [ ! -e "$NEW" ] || die "$NEW already exists; remove it or pick another commit"
  local free; free=$(df -Pk /opt/pivot | awk 'NR==2{print $4}')
  [ "$free" -gt $((8 * 1024 * 1024)) ] || die "under 8 GiB free on /opt/pivot"
  for u in "${!UNIT_NEW[@]}"; do
    unit_drift "$u" || [ "${FORCE_UNITS:-0}" = 1 ] || die "$u.service differs from deploy/systemd/${UNIT_NEW[$u]}.service beyond paths and names (FORCE_UNITS=1 to accept)"
  done
  # files the old release carries that git does not (e.g. the patched chart bundle)
  CARRY=$(cd "$OLD" && find . -type f \
            -not -path '*/node_modules/*' -not -path '*/node_modules.before-*' -not -path '*/.next*' \
            -not -path '*/__pycache__/*' -not -name '*.pyc' -not -name '*.pivot-release-tmp' \
            -not -name '*.before-*' -not -name '*.bak' -printf '%P\n' | sort \
          | comm -23 - <(git_m ls-tree -r --name-only "$DEPLOYED" | sort))
}

unit_drift() {  # the live unit, normalised, against the repo template
  local live="$UNITDIR/$1.service" new="${UNIT_NEW[$1]}"
  diff <(sed -e "s#$OLD/charto/data/#@RELEASE@/dataserver/#; s#$OLD/charto/web/#@RELEASE@/site/#" \
             -e "s#$OLD/pivot-next/#@RELEASE@/app/#; s#/var/lib/pivot/$1\b#/var/lib/pivot/${STATE_NEW[$1]}#g" \
             -e '/^Description=/d' -e '/^$/d' "$live") \
       <(git_m show "$COMMIT:deploy/systemd/$new.service" | sed -e '/^Description=/d' -e '/^$/d') >/dev/null
}

plan() {
  preflight
  say "old release  $OLD  (deployed $DEPLOYED)"
  say "new release  $NEW  ($COMMIT)"
  say "carried over from the old release (not in git):"
  while IFS= read -r f; do
    [ -n "$f" ] || continue
    local to; to=$(map_path "$f")
    printf '    %-70s -> %s\n' "$f" "${to:-(dropped: no new home)}"
  done <<< "$CARRY"
  say "units:   charto -> pivot-dataserver, charto-web -> pivot-site, pivot-next -> pivot-app,"
  say "         charto-backup -> pivot-backup; pivot-api restarts on PYTHONPATH=$NEW/api"
  say "env:     $(grep -l '^CHARTO_' /etc/pivot/*.env | tr '\n' ' ')keys CHARTO_* -> PIVOT_*"
  say "nginx:   pivotnow-app, pivot-clean, conf.d/pivot-ratelimit.conf (replaces charto-ratelimit.conf)"
  say "plan OK. Nothing was changed."
}

build() {
  say "building $NEW"
  local stage; stage=$(mktemp -d /opt/pivot/cutover-XXXXXX); chown "$BUILD_USER:$BUILD_USER" "$stage"
  git_m archive "$COMMIT" app api chart dataserver deploy research site | as_build tar -x -C "$stage"
  rm -rf "$stage"/api/migrations "$stage"/api/scripts "$stage"/api/tests \
         "$stage"/dataserver/tests "$stage"/chart/tests "$stage"/site/.env.local
  while IFS= read -r f; do
    [ -n "$f" ] || continue
    local to; to=$(map_path "$f"); [ -n "$to" ] || continue
    mkdir -p "$stage/$(dirname "$to")"; cp -a "$OLD/$f" "$stage/$to"
  done <<< "$CARRY"
  cp -a "$OLD/pivot-next/node_modules" "$stage/app/node_modules"
  cp -a "$OLD/charto/web/node_modules" "$stage/site/node_modules"
  chown -R "$BUILD_USER:$BUILD_USER" "$stage/app" "$stage/site"
  for app in app site; do
    if ! cmp -s "$stage/$app/pnpm-lock.yaml" "$stage/$app/node_modules/.pnpm/lock.yaml"; then
      say "  $app: installing dependencies"
      (cd "$stage/$app" && as_build env NEXT_TELEMETRY_DISABLED=1 /usr/local/bin/pnpm install --frozen-lockfile --ignore-scripts)
    fi
    say "  $app: next build"
    (cd "$stage/$app" && as_build env "${NEXT_ENV[@]}" nice -n 10 /usr/local/bin/pnpm exec next build)
    [ -f "$stage/$app/.next/standalone/server.js" ] || die "$app produced no standalone server"
    cp -a "$stage/$app/.next/static" "$stage/$app/.next/standalone/.next/static"
    [ -d "$stage/$app/public" ] && cp -a "$stage/$app/public" "$stage/$app/.next/standalone/public"
  done
  chown -R root:root "$stage"; chmod -R a+rX,go-w "$stage"
  find "$stage" -name '*.py' -not -path '*/node_modules/*' -print0 | xargs -0 "$PY" -m py_compile
  for f in "$stage"/chart/js/*.js; do node --check "$f"; done
  mv "$stage" "$NEW"
  say "built $NEW"
}

BK=""
backup() {
  BK=/root/pivot-cutover-$(date -u +%Y%m%dT%H%M%SZ); mkdir -p "$BK"
  say "backing up to $BK"
  echo "$OLD" > "$BK/OLD"; echo "$NEW" > "$BK/NEW"
  cp -a /etc/pivot "$BK/etc-pivot"
  mkdir -p "$BK/units"; cp -a "$UNITDIR"/charto* "$UNITDIR"/pivot-next* "$UNITDIR"/pivot-api* "$BK/units/"
  mkdir -p "$BK/nginx"; cp -a /etc/nginx/sites-available /etc/nginx/conf.d "$BK/nginx/"
  cp -a "$STATE" "$BK/state.json"; cp -a "$DEPLOYER" "$BK/pivot_release.py"
}

rollback() {
  local bk="$1"; [ -f "$bk/OLD" ] || die "$bk is not a cutover backup"
  local old new; old=$(cat "$bk/OLD"); new=$(cat "$bk/NEW")
  say "ROLLING BACK to $old"
  set +e
  systemctl stop pivot-release.timer
  systemctl disable --now pivot-dataserver pivot-site pivot-app pivot-backup.timer 2>/dev/null
  rm -rf "$UNITDIR"/pivot-dataserver.service* "$UNITDIR"/pivot-site.service* "$UNITDIR"/pivot-app.service* \
         "$UNITDIR"/pivot-backup.service "$UNITDIR"/pivot-backup.timer
  cp -a "$bk/units/." "$UNITDIR/"
  for u in "${!STATE_NEW[@]}"; do
    [ -d "/var/lib/pivot/${STATE_NEW[$u]}" ] && [ ! -e "/var/lib/pivot/$u" ] && mv "/var/lib/pivot/${STATE_NEW[$u]}" "/var/lib/pivot/$u"
  done
  rm -rf /etc/pivot && cp -a "$bk/etc-pivot" /etc/pivot
  rm -f /etc/nginx/conf.d/pivot-ratelimit.conf
  cp -a "$bk/nginx/sites-available/." /etc/nginx/sites-available/
  cp -a "$bk/nginx/conf.d/." /etc/nginx/conf.d/
  cp -a "$bk/state.json" "$STATE"; cp -a "$bk/pivot_release.py" "$DEPLOYER"
  systemctl daemon-reload
  systemctl enable --now charto charto-web pivot-next charto-backup.timer
  systemctl restart pivot-api
  nginx -t && systemctl reload nginx
  systemctl start pivot-release.timer
  set -e
  say "rolled back. $new is left on disk; remove it when done."
}

switch() {
  backup
  trap 'say "FAILED at line $LINENO"; rollback "$BK"; exit 1' ERR
  systemctl stop pivot-release.timer
  # env: new PYTHONPATH, PIVOT_* names (renamed in place, so values never leave the box)
  sed -i "s#^PYTHONPATH=$OLD/pivot#PYTHONPATH=$NEW/api#" /etc/pivot/*.env
  sed -i -e 's/^CHARTO_BACKEND=/PIVOT_DATASERVER_ORIGIN=/' -e 's/^CHARTO_EXECUTION=/PIVOT_CHART_EXECUTION=/' \
         -e 's/^CHARTO_UPSTREAM=/PIVOT_DATASERVER_UPSTREAM=/' -e 's/^CHARTO_PREVIEW_DIR=/PIVOT_CHART_DIR=/' \
         -e 's/^CHARTO_/PIVOT_/' /etc/pivot/*.env
  grep -q "^PYTHONPATH=$NEW/api$" /etc/pivot/runtime.env
  # units: stop the old ones, carry their drop-ins and state dirs, start the new ones
  systemctl disable --now charto-backup.timer
  systemctl stop charto charto-web pivot-next
  for u in "${!UNIT_NEW[@]}"; do
    local n="${UNIT_NEW[$u]}"
    sed "s#@RELEASE@#$NEW#g" "$NEW/deploy/systemd/$n.service" > "$UNITDIR/$n.service"
    if [ -d "$UNITDIR/$u.service.d" ]; then
      mkdir -p "$UNITDIR/$n.service.d"
      for d in "$UNITDIR/$u.service.d"/*.conf; do cp -a "$d" "$UNITDIR/$n.service.d/"; done
    fi
    [ -d "/var/lib/pivot/$u" ] && mv "/var/lib/pivot/$u" "/var/lib/pivot/${STATE_NEW[$u]}"
    systemctl disable "$u"
    rm -rf "$UNITDIR/$u.service" "$UNITDIR/$u.service.d"
  done
  rm -f "$UNITDIR/pivot-api.service.d/no-demo-seeding.conf"     # its reader is gone
  sed "s#@RELEASE@#$NEW#g" "$NEW/deploy/systemd/pivot-backup.service" > "$UNITDIR/pivot-backup.service"
  cp "$NEW/deploy/systemd/pivot-backup.timer" "$UNITDIR/pivot-backup.timer"
  rm -f "$UNITDIR/charto-backup.service" "$UNITDIR/charto-backup.timer"
  systemctl daemon-reload
  systemctl enable --now pivot-dataserver
  healthy http://127.0.0.1:5174/health 240
  systemctl restart pivot-api
  healthy http://127.0.0.1:8000/health 180
  systemctl enable --now pivot-site pivot-app pivot-backup.timer
  healthy http://127.0.0.1:5175/ 120
  healthy http://127.0.0.1:3000/ 120
  # nginx: the repo's copies, rendered for this release
  for f in pivotnow-app pivot-clean; do
    sed "s#@RELEASE@#$NEW#g" "$NEW/deploy/nginx/$f.conf" > "/etc/nginx/sites-available/$f"
  done
  sed -i -e "s#$OLD/charto/preview#$NEW/chart#g" -e 's/zone=charto_/zone=pivot_/g' \
         -e 's/limit_conn charto_conn/limit_conn pivot_conn/g' -e 's/@charto_web_next/@site_next/g' \
         /etc/nginx/sites-available/pivot-clean-validation
  cp "$NEW/deploy/nginx/pivot-ratelimit.conf" /etc/nginx/conf.d/pivot-ratelimit.conf
  rm -f /etc/nginx/conf.d/charto-ratelimit.conf
  nginx -t
  systemctl reload nginx
  for p in / /chart-app/index.html /health /stock/RELIANCE; do
    curl -fsS -o /dev/null --max-time 30 --resolve app.pivotnow.in:443:127.0.0.1 "https://app.pivotnow.in$p"
  done
  # the deployer follows the new release from here
  echo "$NEW" > "$POINTER"
  cp "$NEW/deploy/pivot_release.py" "$DEPLOYER"
  python3 - "$STATE" "$COMMIT" <<'PY'
import json, sys, time
p, c = sys.argv[1], sys.argv[2]
s = json.load(open(p)); s.update(deployed=c, deployed_at=int(time.time()), note="layout cutover"); s.pop("failed", None)
json.dump(s, open(p, "w"), indent=2)
PY
  trap - ERR
  systemctl start pivot-release.timer
  say "LIVE $COMMIT in $NEW. Old release kept at $OLD; backup at $BK"
  say "rollback if needed: bash $NEW/deploy/cutover_layout.sh rollback $BK"
}

case "$MODE" in
  plan)     COMMIT="$ARG"; [ -n "$COMMIT" ] || die "usage: plan <commit>"; plan ;;
  apply)    COMMIT="$ARG"; [ -n "$COMMIT" ] || die "usage: apply <commit>"; preflight; build; switch ;;
  rollback) [ -n "$ARG" ] || die "usage: rollback <backup dir>"; rollback "$ARG" ;;
  *)        sed -n '2,6p' "$0"; exit 2 ;;
esac
