#!/usr/bin/env bash
# Does the box's nginx actually serve the routes this repo's config lists?
#
# The failure this catches has now happened twice, and it is silent both times.
# nginx-charto.conf is a RECORD — deploy.sh never applies it, because a bad
# reload takes the site down and that belongs behind a human. So the file and
# the box drift, and a route missing from the live allowlist does not 404: it
# falls through `try_files $uri $uri/ /index.html` and returns **200 with the
# HTML page**. The browser gets a 200, `r.ok` is true, `r.json()` throws into
# an empty object, and the feature renders as though the server had simply
# nothing to say. Nothing logs. Nothing alarms.
#
#   Round 1: /auth, /workspace, /layouts — the login form silently got HTML.
#   Round 2: /suggest, /conversations, /alerts, /journal — the Journal panel
#            and the recommended prompts came up empty in production while
#            working perfectly on localhost, which is exactly how it reads:
#            "the VM app doesn't show it".
#
# So: parse the prefixes out of the committed config, ask the live host for
# each one, and fail on any that answers text/html. Run it after every reload,
# and after shipping any new endpoint.
#
#   ./check_routes.sh                      # the production host
#   ./check_routes.sh 127.0.0.1:5174 http  # a local dataserver (all direct)
set -uo pipefail
cd "$(dirname "$0")"

HOST="${1:-charto-india.centralindia.cloudapp.azure.com}"
SCHEME="${2:-https}"
CONF=nginx-charto.conf

# Every prefix the config claims to proxy: the alternation inside the data
# allowlist, plus each exact-match location. Derived from the file rather than
# hardcoded here, so adding a route to the config also adds it to this check.
prefixes="$(sed -n 's/.*location ~ \^\/(\([^)]*\)).*/\1/p' "$CONF" | tr '|' '\n')
$(sed -n 's/.*location = \(\/[a-z_]*\).*/\1/p' "$CONF")"

fail=0
while read -r p; do
  [ -n "$p" ] || continue
  p="${p#/}"
  # The SSE endpoints stream forever by design; --max-time cuts them off and
  # the content-type header has already arrived by then, which is all we read.
  case "$p" in stream|chat|alerts/stream) t=4 ;; *) t=20 ;; esac
  ct="$(curl -s -o /dev/null -w '%{http_code} %{content_type}' \
        --max-time "$t" "$SCHEME://$HOST/$p" 2>/dev/null)"
  case "$ct" in
    *text/html*)
      printf '  BROKEN  /%-16s %s  <- falls through to index.html\n' "$p" "$ct"
      fail=1 ;;
    "")
      printf '  ?       /%-16s no response within %ss\n' "$p" "$t" ;;
    *)
      # Any status is fine — 401/404 from the backend still proves the request
      # REACHED it. Only the HTML page means nginx answered instead.
      printf '  ok      /%-16s %s\n' "$p" "$ct" ;;
  esac
done <<EOF
$prefixes
EOF

if [ "$fail" -ne 0 ]; then
  cat <<'MSG'

Routes above are in the repo's config but not on the box. Apply it:
  sudo cp charto/deploy/nginx-charto.conf /etc/nginx/sites-available/charto
  sudo nginx -t && sudo systemctl reload nginx
then re-run this script.
MSG
  exit 1
fi
echo "all routes reach the backend"
