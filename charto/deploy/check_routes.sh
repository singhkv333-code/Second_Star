#!/usr/bin/env bash
# Does the box actually serve what this repo says it serves?
#
# The failure this catches has happened three times, and it is silent every
# time. An endpoint nginx does not know about does not 404: it falls through to
# the SPA and returns **200 with the chart page**. `r.ok` is true, `r.json()`
# throws into a `catch {}`, and the feature renders blank with nothing in any
# log — perfect on localhost, dead in production.
#
#   Round 1: /auth, /workspace, /layouts — the login form silently got HTML.
#   Round 2: /suggest, /conversations, /alerts, /journal — the Journal panel
#            and the recommended prompts came up empty.
#   Round 3: /quotes, /patterns/draw, /execution/backtest, /audio/transcribe,
#            /shared — every price in the watchlist was an em-dash, the
#            backtest button did nothing, and voice input failed silently.
#
# WHAT CHANGED. There is no route allowlist any more (see nginx-charto.conf):
# anything that is not a file under preview/ is proxied to the backend. So this
# script no longer parses a list out of the config — a list is exactly what it
# used to be checking, and its absence is the fix. It checks two things:
#
#   1. THE FALL-THROUGH. A path that is not a static file and not any of the
#      special blocks must come back as the backend's JSON, whatever its status.
#      One HTML answer here means the allowlist is back and every future
#      endpoint will break the same way.
#   2. THE SPECIAL BLOCKS. Every location that picks a different upstream, zone
#      or buffering mode — the only rules left that can be individually wrong.
#      Read out of the config, so adding one adds it to the check.
#
# Plus a spot-check of the routes the frontend actually calls, because a probe
# of the mechanism is not a probe of the features that ride it.
#
#   ./check_routes.sh                      # the production host
#   ./check_routes.sh 127.0.0.1:5174 http  # a local dataserver (all direct)
set -uo pipefail
cd "$(dirname "$0")"

HOST="${1:-charto-india.centralindia.cloudapp.azure.com}"
SCHEME="${2:-https}"
CONF=nginx-charto.conf
fail=0

# GET, returning "<status> <content-type>". The SSE endpoints stream forever by
# design; --max-time cuts them off well after the headers, which is all we read.
ask() {
  case "$1" in stream|chat|alerts/stream) t=4 ;; *) t=25 ;; esac
  curl -s -o /dev/null -w '%{http_code} %{content_type}' --max-time "$t" \
       "$SCHEME://$HOST/$1" 2>/dev/null
}

# An answer is "the backend's" when it is not the chart page. Content-type, not
# status: a 400 or a 401 from the dataserver is PROOF the request reached it,
# while the fall-through's 200 text/html is the failure.
judge() {
  local label="$1" ct="$2" note="${3:-}"
  case "$ct" in
    *text/html*) printf '  BROKEN  %-22s %s  <- %s\n' "$label" "$ct" \
                   "${note:-answered by the SPA, not the backend}"; fail=1 ;;
    '')          printf '  ?       %-22s no response\n' "$label" ;;
    *)           printf '  ok      %-22s %s\n' "$label" "$ct" ;;
  esac
}

echo "1. the fall-through — an unlisted path must reach the backend"
# Deliberately a path no location block names and no file matches. If THIS is
# HTML, the allowlist has come back and every endpoint shipped from here on
# will break silently.
judge "/__route_probe__" "$(ask '__route_probe__')" \
      "the fall-through is index.html again — the allowlist is back"

echo
echo "2. the blocks that are not the fall-through"
# Read the location patterns out of the config so a new one is checked without
# editing this file. Alternations expand to one probe each; `@named` locations
# are internal and cannot be requested directly, so they are skipped — the
# fall-through check above is what exercises @backend.
# Three shapes of location, because the config has three: the regex
# alternations, the exact `=` matches, and PREFIX blocks like `/research/`
# (the research chat's upstream). The prefix pattern requires at least one
# letter so it cannot match `location / {`, which is the fall-through and is
# already probe 1.
routes="$(sed -n 's/.*location ~ \^\/(\([^)]*\)).*/\1/p' "$CONF" | tr '|' '\n')
$(sed -n 's/.*location = \(\/[a-z_]*\).*/\1/p' "$CONF")
$(sed -n 's/^[[:space:]]*location \(\/[a-z_][a-z_]*\/\) {.*/\1/p' "$CONF")"

while read -r p; do
  [ -n "$p" ] || continue
  p="${p#/}"
  case "$p" in
    stock/*|_next/*|__nextjs*)
      # A PAGE route is html when it is WORKING, so content-type cannot tell it
      # apart from the fall-through. The body can: only the company app's
      # output carries Next's build marker, and a redirect is proof in itself —
      # the bare prefix has no symbol so Next 308s it, and a static fall-through
      # never redirects.
      ct="$(ask "$p")"; code="${ct%% *}"
      body="$(curl -s --max-time 25 "$SCHEME://$HOST/$p" 2>/dev/null | head -c 4000)"
      case "$code:$body" in
        30*:*|*:*_next/static*|*:*__NEXT_DATA__*)
          printf '  ok      %-22s %s\n' "/$p" "$ct" ;;
        *)
          printf '  BROKEN  %-22s %s  <- company app (:5175) not answering\n' "/$p" "$ct"
          fail=1 ;;
      esac ;;
    *) judge "/$p" "$(ask "$p")" ;;
  esac
done <<EOF
$routes
EOF

echo
echo "3. the routes the frontend actually calls"
# Derived from the fetch calls in preview/js, not typed here, so a new endpoint
# joins this list by being used. Query strings are dropped: we are testing who
# ANSWERS, not what they say.
fe="$(grep -rhoE '\$\{(API|api|base)\}/[a-z_0-9/]+' ../preview/js/*.js 2>/dev/null \
      | sed 's/.*}\///' | sort -u)"
for p in $fe; do
  judge "/$p" "$(ask "$p")"
done

echo
if [ "$fail" -ne 0 ]; then
  cat <<'MSG'
The box is not serving this repo's config. Apply it:
  sudo bash /data/app/charto/deploy/apply_nginx.sh
(deploy.sh does this every 30s once the config lands on the deploy branch;
 apply_nginx.sh tests, backs up, reloads, probes and rolls back on failure.)
MSG
  exit 1
fi
echo "every route reaches the service that owns it"
