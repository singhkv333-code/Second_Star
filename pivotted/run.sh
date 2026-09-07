#!/usr/bin/env bash
# Pivotted on :5175. Runs on pivot's venv — it needs sqlalchemy/psycopg2 for
# the filings DB and certifi for the Azure call (macOS system python ships no
# CA bundle).
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="$HERE/../pivot/.venv/bin/python"

PORT="${PIVOTTED_PORT:-5175}"
# pkill never reliably matched this process name; kill by port and verify.
PID="$(lsof -ti tcp:"$PORT" || true)"
if [ -n "$PID" ]; then
  echo "stopping $PID on :$PORT"; kill "$PID" 2>/dev/null || true; sleep 1
  if lsof -ti tcp:"$PORT" >/dev/null 2>&1; then kill -9 "$PID" 2>/dev/null || true; sleep 1; fi
fi

exec "$PY" "$HERE/server.py"
