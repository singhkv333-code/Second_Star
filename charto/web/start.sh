#!/bin/sh
# Start the company page (Pivot's stock page, copied) on :5175.
# Kill by PORT then verify the PID changed — same rule as data/restart.sh,
# because a dev server that lost the port exits quietly and leaves the old
# code serving.
set -e
cd "$(dirname "$0")"
PORT=5175
OLD=$(lsof -ti tcp:$PORT || true)
[ -n "$OLD" ] && kill -9 $OLD 2>/dev/null && sleep 1
nohup npx next dev -p $PORT > /tmp/charto_web.log 2>&1 &
sleep 6
NEW=$(lsof -ti tcp:$PORT || true)
if [ -z "$NEW" ] || [ "$NEW" = "$OLD" ]; then
  echo "START FAILED — port $PORT held by '${NEW:-nothing}' (was '$OLD')"
  cat /tmp/charto_web.log
  exit 1
fi
echo "company page up on :$PORT (pid $NEW)"
