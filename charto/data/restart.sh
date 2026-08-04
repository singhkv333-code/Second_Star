#!/bin/sh
# Restart the dataserver and PROVE it is the new one.
#
# `pkill -f "python3 dataserver.py"` does not match: the process shows up as
# ".../Python.app/Contents/MacOS/Python dataserver.py". The old server kept
# the port, the new one died with EADDRINUSE, and `curl /meta` still returned
# 200 — so a passing health check meant "a server is up", not "your code is
# live". Two rounds of testing ran against stale code before anyone read the
# log. Kill by PORT, then verify the PID changed.
#
# Runs under PIVOT's venv, not bare python3. The live venue drivers start
# inside this process (a stream in a separate process writes minutes to SQLite
# but its forming bar never reaches a chart), and the Kite driver needs
# kiteconnect + sqlalchemy to read the session. Measured: bare python3 has
# none of numpy/pandas/kiteconnect/sqlalchemy/certifi, pivot's venv has all of
# them AND imports dataserver unchanged — it is a strict superset, so this
# costs nothing and unblocks /live?venue=kite.
set -e
cd "$(dirname "$0")"
PORT=5174
PY="$(cd ../../pivot && pwd)/.venv/bin/python"
[ -x "$PY" ] || PY=python3
OLD=$(lsof -ti tcp:$PORT || true)
[ -n "$OLD" ] && kill -9 $OLD 2>/dev/null && sleep 1
# -u is load-bearing, not a nicety: stdout is a FILE here, so Python
# block-buffers it and every print the server makes — driver start-up,
# gap-fill reports, refusal reasons — sits in a buffer that is never flushed
# while the process runs. The log read as empty for a whole debugging session,
# which is indistinguishable from "the code never ran".
nohup "$PY" -u dataserver.py > /tmp/charto_ds.log 2>&1 &
sleep 3
NEW=$(lsof -ti tcp:$PORT || true)
if [ -z "$NEW" ] || [ "$NEW" = "$OLD" ]; then
  echo "RESTART FAILED — port $PORT still held by '${NEW:-nothing}' (was '$OLD')"
  cat /tmp/charto_ds.log
  exit 1
fi
echo "dataserver restarted: pid $OLD -> $NEW"
cat /tmp/charto_ds.log
