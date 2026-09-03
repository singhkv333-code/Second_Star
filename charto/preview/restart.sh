#!/bin/sh
# Restart the preview server and PROVE it is the new one.
#
# data/restart.sh already carries this lesson for the dataserver: a server that
# keeps the port while the new one dies of EADDRINUSE still answers 200, so a
# passing check means "a server is up", not "your code is live". Two rounds of
# testing ran against stale code before anyone read the log.
#
# The preview never got the same script, and it cost the same day. serve.py had
# been running since a Monday; /paper was added to its proxy table on the
# Wednesday, and the browser answered "File not found" — the static handler's
# 404, from a process that had never heard of the route. Nothing was wrong with
# the code. Nothing pointed at the process either.
#
# So: kill by PORT, restart, verify the PID CHANGED, and check a route that
# only exists in the new code.
#
#   ./restart.sh                    the default :5173
#   CHARTO_PREVIEW_PORT=5183 ./restart.sh
set -e
cd "$(dirname "$0")"
PORT="${CHARTO_PREVIEW_PORT:-5173}"

OLD=$(lsof -ti tcp:"$PORT" 2>/dev/null | head -1 || true)
if [ -n "$OLD" ]; then
  kill "$OLD" 2>/dev/null || true
  sleep 1
  # A handler mid-relay can hold the port past a TERM.
  lsof -ti tcp:"$PORT" >/dev/null 2>&1 && { kill -9 "$OLD" 2>/dev/null || true; sleep 1; }
fi

CHARTO_PREVIEW_PORT="$PORT" nohup python3 serve.py > /tmp/charto_preview_$PORT.log 2>&1 &
sleep 2

NEW=$(lsof -ti tcp:"$PORT" 2>/dev/null | head -1 || true)
if [ -z "$NEW" ]; then
  echo "preview FAILED to start on :$PORT"
  tail -20 /tmp/charto_preview_$PORT.log
  exit 1
fi
if [ "$OLD" = "$NEW" ]; then
  echo "preview did NOT restart — :$PORT is still pid $NEW (old code is live)"
  exit 1
fi
echo "preview restarted on :$PORT — pid ${OLD:-none} -> $NEW"

# The chart itself, and one proxied route per upstream. A 404 on /paper means
# this is a serve.py without the paper split; a 502 means the app behind it is
# down, and the body says which.
for probe in "/ chart" "/paper page" "/paper/summary data"; do
  path=$(echo "$probe" | cut -d' ' -f1)
  what=$(echo "$probe" | cut -d' ' -f2)
  code=$(curl -s -o /dev/null -w '%{http_code}' -m 10 "http://127.0.0.1:$PORT$path" || echo 000)
  echo "  $what $path -> $code"
done
echo "  (/paper/summary answers 401 when signed out — that is the backend, and correct)"
