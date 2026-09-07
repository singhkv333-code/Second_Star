#!/usr/bin/env bash
# Give the VM Pivot's API on :8000, reached through nginx at /api/pivot/.
#
# WHY THIS EXISTS
# ---------------
# Pivot has been in production for months as a LIBRARY, never a service:
# `charto/data/execution_bridge.py` imports its strategy engine and
# `pivotted/fundamentals.py` imports its financials layer, but nothing served
# it over HTTP. This is additive — it adds one route and touches none of the
# existing ones, so every path on this box keeps being answered by the process
# that answers it today.
#
# What it does NOT need is a new interpreter or a new checkout. `/data/app` is
# already a sparse checkout that includes `/pivot/`, and execution_bridge
# already imports that package into `/data/venv`. A venv of its own would be a
# second copy of the dependencies the tools were tested against, free to drift.
#
# What it DOES need is a `.env` that actually reaches Postgres. `config.py`
# defaults DATABASE_URL to a localhost server that is not on this box, so an
# unset value is not an error anywhere: it is a connection refused to a
# database that was never going to be there, and the API would start, answer
# /health, and 500 every route that touches a row. Step 2 refuses to provision
# without it — the same failure mode, and the same fix, as
# provision_research.sh's FINANCIALS_DSN check.
#
# IDEMPOTENT. Re-running is a few greps, a unit compare and a restart.
#
#   sudo bash /data/app/charto/deploy/provision_pivot_api.sh
#
set -euo pipefail

REPO=/data/app
VENV=/data/venv
UNIT=pivot-api.service
ENVF="$REPO/pivot/.env"
OWNER="$(stat -c %U "$REPO")"

say() { printf '\n\033[1m== %s\033[0m\n' "$*"; }

# ── 1 · the source ──────────────────────────────────────────────────────────
say "source"
test -f "$REPO/pivot/backend/main.py" \
  || { echo "   FAILED: $REPO/pivot/backend/main.py missing — is /pivot/ in .git/info/sparse-checkout?"; exit 1; }
echo "   pivot/backend/main.py present"

# ── 2 · the database, proven with a real row ────────────────────────────────
# A reachability check is not enough: the DSN can point at a server that is up
# and holds none of this, and an empty database answers a ping perfectly.
say "database"
grep -q '^DATABASE_URL=' "$ENVF" || {
  cat <<'MSG'
   FAILED: DATABASE_URL is not in pivot/.env

   backend/config.py then falls back to a localhost Postgres that is not on
   this box. The API would start, /health would answer, and every route that
   reads a row would 500. Copy DATABASE_URL from the .env this repo is
   developed against, then re-run.
MSG
  exit 1
}
cd "$REPO/pivot"
sudo -u "$OWNER" env PYTHONPATH="$REPO/pivot" "$VENV/bin/python" - <<'PY'
import sys
sys.path.insert(0, "/data/app/pivot")
from backend.database import SessionLocal
from sqlalchemy import text
db = SessionLocal()
try:
    n = db.execute(text("SELECT count(*) FROM users")).scalar()
    m = db.execute(text("SELECT count(*) FROM instrument_master")).scalar()
    print(f"   users={n}  instrument_master={m}")
    if m == 0:
        raise SystemExit("   FAILED: connected, but instrument_master is empty")
finally:
    db.close()
PY

# ── 2b · Charto's account store, which is where sign-in lives ───────────────
# Pivot accepts a Charto session token (backend/auth/charto_session.py). That
# read is best-effort by design — a missing store means Charto users simply
# cannot authenticate to this API, which is a silent half-working deployment.
# Checked here so it is loud once instead of confusing forever.
say "charto session store"
CU="${CHARTO_USERS_DB:-$REPO/charto/data/charto_users.db}"
if [ -f "$CU" ]; then
  echo "   $CU present"
else
  echo "   WARNING: $CU not found — Charto sign-ins will NOT authenticate here."
  echo "   Set CHARTO_USERS_DB in the unit if the store lives elsewhere."
fi

# ── 3 · the unit ────────────────────────────────────────────────────────────
say "$UNIT"
SRC="$REPO/charto/deploy/$UNIT"
DST="/etc/systemd/system/$UNIT"
if cmp -s "$SRC" "$DST"; then
  echo "   unchanged"
else
  install -m 0644 "$SRC" "$DST"
  systemctl daemon-reload
  echo "   installed"
fi

# deploy.sh runs as $OWNER and restarts this on a pivot/ change. Without the
# sudoers line that restart is a silent permission failure and the box serves
# code it no longer has.
say "sudoers"
SUDOF=/etc/sudoers.d/pivot-api
LINE="$OWNER ALL=(root) NOPASSWD: /usr/bin/systemctl restart $UNIT, /usr/bin/systemctl start $UNIT, /usr/bin/systemctl stop $UNIT"
if [ -f "$SUDOF" ] && grep -qF "$LINE" "$SUDOF"; then
  echo "   already granted"
else
  printf '%s\n' "$LINE" > "$SUDOF"
  chmod 0440 "$SUDOF"
  visudo -cf "$SUDOF" >/dev/null || { rm -f "$SUDOF"; echo "   FAILED: bad sudoers"; exit 1; }
  echo "   granted"
fi

# ── 4 · start, and prove it answers ─────────────────────────────────────────
say "start"
systemctl enable --quiet "$UNIT" 2>/dev/null || true
systemctl restart "$UNIT"
for _ in $(seq 1 45); do
  curl -fsS --max-time 3 http://127.0.0.1:8000/health >/dev/null 2>&1 && break
  sleep 2
done
systemctl is-active --quiet "$UNIT" \
  || { echo "   FAILED to come up"; journalctl -u "$UNIT" -n 40 --no-pager; exit 1; }
curl -fsS --max-time 5 http://127.0.0.1:8000/health >/dev/null \
  || { echo "   up but /health does not answer"; journalctl -u "$UNIT" -n 40 --no-pager; exit 1; }
echo "   active, /health answering on 127.0.0.1:8000"

# The two things this unit turns off, asserted rather than assumed. A scheduler
# running here costs a core Charto's tick engine needs, and a second Kite
# socket fights the one kite_stream.py already holds.
if journalctl -u "$UNIT" -n 200 --no-pager | grep -q "Background jobs disabled"; then
  echo "   background jobs OFF (correct on this host)"
else
  echo "   WARNING: background jobs appear to be ON — check the unit's env"
fi

# Loopback only. A listener on 0.0.0.0 bypasses nginx and its rate limit.
if ss -ltn | grep -q '0\.0\.0\.0:8000'; then
  echo "   WARNING: bound to 0.0.0.0 — the --host flag did not take"
fi

# ── 5 · the route ───────────────────────────────────────────────────────────
say "nginx /api/pivot/"
bash "$REPO/charto/deploy/apply_nginx.sh" || echo "   (apply_nginx reported a problem — see above)"
HOSTNAME_="$(grep -m1 -oE 'server_name[[:space:]]+[^;]+' \
  "$REPO/charto/deploy/nginx-charto.conf" | awk '{print $2}')"
# Through the REAL server block: --resolve pins the public name to loopback so
# TLS and server_name both match what a visitor gets. Plain http://127.0.0.1/
# answers from the default block, which is how a live route once reported
# itself as "not routed".
code="$(curl -sk -o /dev/null -w '%{http_code}' --max-time 8 \
  --resolve "$HOSTNAME_:443:127.0.0.1" "https://$HOSTNAME_/api/pivot/health" 2>/dev/null)"
if [ "$code" = 200 ]; then
  echo "   /api/pivot/health answering through nginx"
else
  echo "   WARNING: /api/pivot/health returned '$code' through nginx"
fi

say "done"
