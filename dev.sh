#!/usr/bin/env bash
# Bring the whole platform up with one command, and take it down with one ^C.
#
# WHY THIS EXISTS
# ---------------
# Pivot is one product and four processes, and until this script the only way
# to see it was to start them by hand in four terminals, in the right order,
# remembering that the chart needs Pivot's venv rather than bare python3. The
# Chart tab then showed "localhost refused to connect" for whichever one had
# not been started — which reads as a broken feature, not a missing process.
#
#   :5174  charto dataserver   bars, indicators, patterns, alerts, live ticks,
#                              the paper book, armed strategies, auth
#   :5173  charto preview      the chart app itself (static, no-cache)
#   :8000  pivot API           workflows DSL, backtesters, tool registry
#   :3000  pivot-next          THE SHELL — the only URL you open
#
# You open http://localhost:3000. Everything else is proxied to by
# next.config.ts so the browser sees ONE origin, which is what lets the chart
# share the shell's localStorage instead of being separately signed out.
#
# Already-running ports are LEFT ALONE rather than restarted: this is meant to
# be safe to run when you already have a dataserver up with a warm SQLite page
# cache (a cold read costs 5-6.5s, measured).
set -uo pipefail
cd "$(dirname "$0")"
ROOT="$PWD"
PY="$ROOT/pivot/.venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3)"

pids=()

up() { lsof -ti "tcp:$1" -sTCP:LISTEN >/dev/null 2>&1; }

start() {                       # start <port> <name> <logfile> <cmd...>
  local port="$1" name="$2" log="$3"; shift 3
  if up "$port"; then
    printf '  %-18s :%-5s already running — left alone\n' "$name" "$port"
    return
  fi
  ( "$@" >"$log" 2>&1 ) &
  pids+=($!)
  printf '  %-18s :%-5s starting   (log: %s)\n' "$name" "$port" "$log"
}

stop() {
  echo
  echo "shutting down…"
  # The children are process GROUPS (npm spawns next, uvicorn spawns a
  # reloader), so killing the pid alone orphans the real server and leaves the
  # port held — which is exactly the EADDRINUSE that made restart.sh have to
  # kill by port instead of by name.
  for p in "${pids[@]:-}"; do
    kill -- "-$p" 2>/dev/null || kill "$p" 2>/dev/null || true
  done
  wait 2>/dev/null || true
  echo "stopped."
}
trap stop EXIT INT TERM

echo "Pivot — starting the platform"
start 5174 "charto data"   /tmp/pivot_dev_dataserver.log \
      env -C "$ROOT/charto/data" "$PY" -u dataserver.py
start 5173 "charto chart"  /tmp/pivot_dev_preview.log \
      env -C "$ROOT/charto/preview" "$PY" -u serve.py
start 8000 "pivot api"     /tmp/pivot_dev_api.log \
      env -C "$ROOT/pivot" "$PY" -m uvicorn backend.main:app --reload --port 8000
start 3000 "pivot shell"   /tmp/pivot_dev_next.log \
      env -C "$ROOT/pivot-next" npm run dev

echo
echo "waiting for the ports…"
for _ in $(seq 1 90); do
  ready=0
  for p in 5174 5173 8000 3000; do up "$p" && ready=$((ready + 1)); done
  [ "$ready" -eq 4 ] && break
  sleep 1
done

echo
for p in 5174:"charto data" 5173:"charto chart" 8000:"pivot api" 3000:"pivot shell"; do
  port="${p%%:*}"; name="${p#*:}"
  if up "$port"; then printf '  \033[32mup\033[0m    %-14s :%s\n' "$name" "$port"
  else printf '  \033[31mDOWN\033[0m  %-14s :%s  — see the log above\n' "$name" "$port"; fi
done
echo
echo "  open  http://localhost:3000     (Chart tab is live immediately)"
echo "  ^C    stops everything this script started"
echo
wait
