#!/bin/sh
# Start the company page on :5175 — production build by default.
# Kill by PORT then verify the PID changed — same rule as data/restart.sh,
# because a dev server that lost the port exits quietly and leaves the old
# code serving.
set -e
cd "$(dirname "$0")"
PORT=5175
OLD=$(lsof -ti tcp:$PORT || true)
[ -n "$OLD" ] && kill -9 $OLD 2>/dev/null && sleep 1
# PRODUCTION by default, `./start.sh dev` for the edit-refresh loop.
#
# `next dev` ships the app unminified and unsplit: measured on the company
# page, 21 MB of JavaScript across 8 chunks, ~2.1s before a single API call
# could even fire. The same page built is 223 kB on top of a 106 kB shared
# bundle. The APIs were never the slow part — they answer in 7-76 ms — so
# serving dev JS locally made the page look an order of magnitude slower than
# the thing it is, and made it slower than the deployed site.
if [ "${1:-}" = "dev" ]; then
  nohup npx next dev -p $PORT > /tmp/charto_web.log 2>&1 &
  sleep 6
else
  # `next dev` and `next build` write incompatible things into .next, so a
  # build over a dev tree (or the reverse) leaves the running server serving
  # chunks that 404. Clear it rather than debug that twice.
  [ -f .next/BUILD_ID ] || rm -rf .next
  npx next build > /tmp/charto_web_build.log 2>&1 || {
    echo "BUILD FAILED"; tail -20 /tmp/charto_web_build.log; exit 1; }
  nohup npx next start -p $PORT > /tmp/charto_web.log 2>&1 &
  sleep 4
fi
NEW=$(lsof -ti tcp:$PORT || true)
if [ -z "$NEW" ] || [ "$NEW" = "$OLD" ]; then
  echo "START FAILED — port $PORT held by '${NEW:-nothing}' (was '$OLD')"
  cat /tmp/charto_web.log
  exit 1
fi
echo "company page up on :$PORT (pid $NEW)"
