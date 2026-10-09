#!/usr/bin/env bash
# Bring the whole platform up with one command, and take it down with one ^C.
#
# WHY THIS EXISTS
# ---------------
# Pivot is one product on ONE port. Two of its engines are Python services in
# different runtimes, so they still run as processes — but nothing in the
# browser ever addresses them: the shell on :3000 is the only front door, with
# the same routing table nginx serves in production (pivot-next/next.config.ts).
#
#   :3000  pivot-next          THE PRODUCT — the only URL you open. Its own pages
#                              (home, chat, stock, paper, strategies, brokers),
#                              the chart at /chart-app, and a fall-through to:
#   :5174  charto dataserver   bars, indicators, patterns, the chart's chat,
#                              alerts, live ticks, the paper book, accounts
#   :8000  pivot API           workflows DSL, backtesters, tool registry (/pv/*)
#
# Both backends listen on 127.0.0.1 only. The chart no longer needs a server of
# its own (serve.py on :5173) — the shell serves its files — and signing in once
# signs in the shell, the chart and Pivot's API together.
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
# A Python without its own CA bundle (python.org builds on macOS) fails every
# HTTPS fetch the data server makes — exchange backfills, web search — with
# CERTIFICATE_VERIFY_FAILED. certifi ships in the venv; point the runtime at it.
CA="$("$PY" -c 'import certifi; print(certifi.where())' 2>/dev/null || true)"
start 5174 "charto data"   /tmp/pivot_dev_dataserver.log \
      env -C "$ROOT/charto/data" ${CA:+SSL_CERT_FILE="$CA"} "$PY" -u dataserver.py
start 8000 "pivot api"     /tmp/pivot_dev_api.log \
      env -C "$ROOT/pivot" "$PY" -m uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
# Process env beats .env.local in Next, so these pin the one-origin bases even
# where an older .env.local still points the browser at :8000 directly.
start 3000 "pivot"         /tmp/pivot_dev_next.log \
      env -C "$ROOT/pivot-next" NEXT_PUBLIC_PIVOT_API_BASE=/pv/api \
      CHARTO_PREVIEW_DIR="$ROOT/charto/preview" npm run dev

echo
echo "waiting for the ports…"
for _ in $(seq 1 90); do
  ready=0
  for p in 5174 8000 3000; do up "$p" && ready=$((ready + 1)); done
  [ "$ready" -eq 3 ] && break
  sleep 1
done

echo
for p in 5174:"charto data" 8000:"pivot api" 3000:"pivot"; do
  port="${p%%:*}"; name="${p#*:}"
  if up "$port"; then printf '  \033[32mup\033[0m    %-14s :%s\n' "$name" "$port"
  else printf '  \033[31mDOWN\033[0m  %-14s :%s  — see the log above\n' "$name" "$port"; fi
done
echo
echo "  open  http://localhost:3000     — the whole product, one port"
echo "  ^C    stops everything this script started"
echo
wait
