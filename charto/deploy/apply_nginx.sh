#!/usr/bin/env bash
# Install this repo's nginx config on the box, safely, without a human.
#
# WHY THIS EXISTS
# ---------------
# nginx-charto.conf and nginx-ratelimit.conf spent their whole lives as a
# RECORD. deploy.sh never applied them, on the reasoning that "a bad reload
# takes the site down and that belongs behind a human". The reasoning was
# sound and the outcome was worse than the risk it avoided: the human step
# never happened, so the repo and the box drifted for weeks at a time, and a
# route added to the config read exactly like a route nobody added. Three
# separate rounds of silently-broken production endpoints came out of that gap
# — most recently /quotes, which is every price in the watchlist.
#
# So the reload is automated, and the thing that made it scary is removed
# instead:
#
#   · the candidate is syntax-checked IN PLACE, before anything is reloaded;
#   · the live files are copied aside first, with the epoch in the name;
#   · the reload is followed by a real HTTP probe of the running server;
#   · ANY failure at any of those points restores the backup, reloads again,
#     and exits non-zero.
#
# The window in which the site can be wrong is therefore bounded by one reload
# and one curl, and it cannot end with a broken config installed. `systemctl
# reload` is itself graceful: old workers finish their requests, and if the new
# config fails to load nginx keeps serving the old one.
#
# Idempotent, and cheap when there is nothing to do: two cmp calls and exit 0.
# That is the common case, since deploy.sh runs this every 30 seconds.
#
#   sudo bash /data/app/charto/deploy/apply_nginx.sh          # apply if changed
#   sudo bash /data/app/charto/deploy/apply_nginx.sh --check  # report only
#
set -uo pipefail

SITE_SRC="$(cd "$(dirname "$0")" && pwd)/nginx-charto.conf"
RATE_SRC="$(cd "$(dirname "$0")" && pwd)/nginx-ratelimit.conf"
SITE_DST=/etc/nginx/sites-available/charto
RATE_DST=/etc/nginx/conf.d/charto-ratelimit.conf
HOSTNAME_="${CHARTO_NGINX_HOST:-pivot-india.centralindia.cloudapp.azure.com}"
PROBE="${CHARTO_NGINX_PROBE:-/meta}"
CHECK_ONLY=0
[ "${1:-}" = "--check" ] && CHECK_ONLY=1

say() { printf 'nginx: %s\n' "$*"; }

test -f "$SITE_SRC" || { say "FAILED: $SITE_SRC missing"; exit 1; }
test -f "$RATE_SRC" || { say "FAILED: $RATE_SRC missing"; exit 1; }

changed=0
cmp -s "$SITE_SRC" "$SITE_DST" || changed=1
cmp -s "$RATE_SRC" "$RATE_DST" || changed=1

if [ "$changed" -eq 0 ]; then
  [ "$CHECK_ONLY" -eq 1 ] && say "config matches the repo"
  exit 0
fi

if [ "$CHECK_ONLY" -eq 1 ]; then
  say "DRIFT — the box is not serving what this repo says it serves"
  diff -u "$SITE_DST" "$SITE_SRC" | head -40
  diff -u "$RATE_DST" "$RATE_SRC" | head -20
  exit 1
fi

[ "$(id -u)" = 0 ] || { say "FAILED: must run as root"; exit 1; }

stamp="$(date +%s)"
BAK=/root/charto-nginx.backup.$stamp
mkdir -p "$BAK"
# `|| true`: a first install has nothing to back up, which is not a failure.
cp -a "$SITE_DST" "$BAK/site" 2>/dev/null || true
cp -a "$RATE_DST" "$BAK/ratelimit" 2>/dev/null || true
say "backed up to $BAK"

restore() {
  say "ROLLING BACK"
  [ -f "$BAK/site" ]      && cp -a "$BAK/site" "$SITE_DST"
  [ -f "$BAK/ratelimit" ] && cp -a "$BAK/ratelimit" "$RATE_DST"
  nginx -t >/dev/null 2>&1 && systemctl reload nginx
  systemctl is-active --quiet nginx && say "restored, nginx active" \
                                    || say "RESTORED CONFIG BUT NGINX IS DOWN"
  exit 1
}

cp -a "$SITE_SRC" "$SITE_DST"
cp -a "$RATE_SRC" "$RATE_DST"

# The candidate is on disk but NOT loaded. This is the check that makes the
# reload safe, and it must happen with both files in place: the site references
# zones the ratelimit file declares, so testing either one alone proves nothing.
if ! out="$(nginx -t 2>&1)"; then
  say "candidate failed nginx -t:"
  printf '%s\n' "$out" | sed 's/^/       /'
  restore
fi

systemctl reload nginx || restore
sleep 1
systemctl is-active --quiet nginx || restore

# A config can be syntactically perfect and still serve nothing — a wrong
# upstream, a root that does not exist, a location that shadows the app. So ask
# the running server for something only a working proxy can answer.
#
# Through the REAL server block, not port 80: --resolve pins the hostname to
# the loopback so TLS and server_name both match what a visitor gets, while the
# request never leaves the box. -k because the cert is for the public name and
# the connection is to 127.0.0.1.
probe() {
  curl -sk -o /dev/null -w '%{http_code}' --max-time 10 \
    --resolve "$HOSTNAME_:443:127.0.0.1" "https://$HOSTNAME_$1" 2>/dev/null
}
code="$(probe "$PROBE")"
if [ "$code" != 200 ]; then
  say "post-reload probe $PROBE returned '$code'"; restore
fi

# And the thing this whole change is about: a path that is not a static file
# must reach the backend, not the chart page. /quotes with no arguments is a
# 400 from the dataserver and a 200 from the old fall-through, so the two are
# told apart by content-type, not by status.
ct="$(curl -sk -o /dev/null -w '%{content_type}' --max-time 10 \
      --resolve "$HOSTNAME_:443:127.0.0.1" "https://$HOSTNAME_/quotes" 2>/dev/null)"
case "$ct" in
  *json*) say "applied — $PROBE 200, /quotes reaches the backend ($ct)" ;;
  *)      say "/quotes answered '$ct', not JSON — the fall-through is still HTML"
          restore ;;
esac
