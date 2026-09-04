#!/usr/bin/env bash
# Give the VM the research chat that the company page's ask bar talks to.
#
# WHY THIS EXISTS
# ---------------
# The ask bar on /stock/<SYM> posts to `/research/chat/stream`. On a laptop
# that reaches pivotted/ on :5175 and the bar answers. On this box none of it
# existed, for three independent reasons, and the bar would have shipped as a
# control that 404s on every question:
#
#   1. /data/app is a SPARSE checkout of `/charto/` and `/pivot/`. `pivotted/`
#      never landed, so a push could not put it here.
#   2. There was no unit. Nothing started it, and :5175 — its default port — is
#      the company page on this box, so even a hand-start would have collided.
#   3. nginx had no /research/ route. That part travels with the repo now and
#      apply_nginx.sh installs it; this script just makes sure it took.
#
# What it does NOT need is a new environment. pivotted/tools.py imports charto's
# own `dataserver` module for its tool table and its Azure credentials, so it
# runs on /data/venv — the same interpreter the dataserver runs on, already
# holding psycopg2 and certifi from provision_execution.sh.
#
# IDEMPOTENT. Re-running it is a few greps, a unit compare and a restart.
#
#   sudo bash /data/app/charto/deploy/provision_research.sh
#
set -euo pipefail

REPO=/data/app
VENV=/data/venv
UNIT=charto-research.service
OWNER="$(stat -c %U "$REPO")"

say() { printf '\n\033[1m== %s\033[0m\n' "$*"; }

# ── 1 · the source ──────────────────────────────────────────────────────────
# Old-style (non-cone) sparse file; `/pivotted/` is appended to the existing
# rules rather than replacing them, so `/charto/` and `/pivot/` keep working.
say "sparse checkout"
SP="$REPO/.git/info/sparse-checkout"
if grep -qx '/pivotted/' "$SP"; then
  echo "   /pivotted/ already listed"
else
  printf '/pivotted/\n' | sudo -u "$OWNER" tee -a "$SP" >/dev/null
  echo "   added /pivotted/"
fi
# read-tree materialises what the new rules allow. TRACKED files only, so the
# untracked, gitignored pivot/.env that holds AZURE_KEY is not at risk — the
# same guarantee deploy.sh's "NEVER git clean -x" warning is protecting.
sudo -u "$OWNER" git -C "$REPO" read-tree -mu HEAD
test -f "$REPO/pivotted/server.py" \
  || { echo "   FAILED: pivotted/ did not materialise"; exit 1; }
echo "   pivotted/server.py present"

# pivotted resolves charto as `Path(__file__).parent.parent / "charto" / "data"`,
# so it must sit as a SIBLING of charto/. If that ever stops being true the
# import below fails with a bare ModuleNotFoundError and no hint why.
test -d "$REPO/charto/data" \
  || { echo "   FAILED: charto/data is not a sibling of pivotted/"; exit 1; }

# ── 2 · prove the seam before installing anything that restarts ─────────────
# Importing tools.py loads charto's dataserver, which is where the Azure
# credentials and the bar store come from. If that import is going to fail it
# should fail here, in the foreground, and not as a restart loop at 3am.
say "import check on $VENV"
"$VENV/bin/python" -V
sudo -u "$OWNER" env PYTHONPATH="$REPO/pivotted" "$VENV/bin/python" - <<'PY'
import sys
sys.path.insert(0, "/data/app/pivotted")
import tools as T
print(f"   tools     : {len(T.TOOLS)}")
print(f"   dropped   : {len(T.DROPPED)}")
print(f"   azure key : {'set' if T.ds.AZURE_KEY else 'MISSING'}")
print(f"   deployment: {T.ds.LLM_DEPLOYMENT}")
if not T.ds.AZURE_KEY:
    raise SystemExit("no AZURE_KEY — pivot/.env is where the dataserver finds it")
if not T.TOOLS:
    raise SystemExit("empty tool table — the subtraction from ds.TOOLS took everything")
PY

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

# deploy.sh runs as $OWNER and restarts this on a pivotted/ or charto/data/
# change. Without the sudoers line that restart is a silent permission failure
# and the box serves a research chat built from source it no longer has.
say "sudoers"
SUDOF=/etc/sudoers.d/charto-research
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
# The first boot warms the price service and reads the tool table; it is not
# instant. Poll rather than guess a sleep.
for _ in $(seq 1 30); do
  curl -fsS --max-time 3 http://127.0.0.1:5176/health >/dev/null 2>&1 && break
  sleep 2
done
systemctl is-active --quiet "$UNIT" \
  || { echo "   FAILED to come up"; journalctl -u "$UNIT" -n 40 --no-pager; exit 1; }
curl -fsS --max-time 5 http://127.0.0.1:5176/health >/dev/null \
  || { echo "   up but /health does not answer"; journalctl -u "$UNIT" -n 40 --no-pager; exit 1; }
echo "   active, /health answering on 127.0.0.1:5176"

# Loopback only — see the unit. A listener on 0.0.0.0 here is a model turn
# anybody can spend, with nginx's rate limit bypassed.
if ss -ltn | grep -q '0\.0\.0\.0:5176'; then
  echo "   WARNING: bound to 0.0.0.0 — PIVOTTED_HOST did not take"
fi

# ── 5 · the route ───────────────────────────────────────────────────────────
# apply_nginx.sh owns the reload (syntax check, backup, probe, rollback). This
# only asserts the outcome, because a route that is in the repo and not on the
# box is exactly the drift that script exists to end.
say "nginx /research/"
bash "$REPO/charto/deploy/apply_nginx.sh" || echo "   (apply_nginx reported a problem — see above)"
curl -fsS --max-time 5 http://127.0.0.1/research/health >/dev/null 2>&1 \
  && echo "   /research/health answering through nginx" \
  || echo "   WARNING: /research/ not routed — check nginx-charto.conf landed"

say "done"
