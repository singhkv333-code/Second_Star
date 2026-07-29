#!/bin/sh
# Restart the dataserver and PROVE it is the new one.
#
# `pkill -f "python3 dataserver.py"` does not match: the process shows up as
# ".../Python.app/Contents/MacOS/Python dataserver.py". The old server kept
# the port, the new one died with EADDRINUSE, and `curl /meta` still returned
# 200 — so a passing health check meant "a server is up", not "your code is
# live". Two rounds of testing ran against stale code before anyone read the
# log. Kill by PORT, then verify the PID changed.
set -e
cd "$(dirname "$0")"
PORT=5174
OLD=$(lsof -ti tcp:$PORT || true)
[ -n "$OLD" ] && kill -9 $OLD 2>/dev/null && sleep 1
nohup python3 dataserver.py > /tmp/charto_ds.log 2>&1 &
sleep 3
NEW=$(lsof -ti tcp:$PORT || true)
if [ -z "$NEW" ] || [ "$NEW" = "$OLD" ]; then
  echo "RESTART FAILED — port $PORT still held by '${NEW:-nothing}' (was '$OLD')"
  cat /tmp/charto_ds.log
  exit 1
fi
echo "dataserver restarted: pid $OLD -> $NEW"
cat /tmp/charto_ds.log
