#!/usr/bin/env bash
# Give the VM the automation engine that Execution mode talks to.
#
# WHY THIS EXISTS
# ---------------
# Charto's Execution mode is a thin seam (charto/data/execution_bridge.py) onto
# Pivot's automation engine: it imports `backend.agents.tools`,
# `backend.prompts.assembler`, `backend.services.tool_registry` and
# `backend.workflows.registry` out of the SIBLING checkout at ../../pivot.
#
# On a laptop that is the whole repo under Pivot's own venv, and the mode works.
# On this box it was never there at all, for two independent reasons:
#
#   1. /data/app is a SPARSE checkout of `/charto/` only. `pivot/` never landed.
#      The directory exists solely because someone put the .env in it by hand —
#      which is exactly what deploy.sh's "NEVER git clean -x" warning is about.
#   2. /data/venv held certifi and nothing else. Even with the source present,
#      `import backend.agents.tools` wants fastapi, pydantic, sqlalchemy, pandas.
#
# So the bridge reported `available: False / tools: 0`, the mode stayed on the
# wire with no builder behind it, and the model — asked to build a rule with no
# tool to build it — INVENTED a boundary ("I can't place or automate that order
# here") for a capability that exists and is tested. dataserver.py now refuses
# to offer the mode in that state; this script is what makes the state go away.
#
# IDEMPOTENT. Re-running it is a few greps, a no-op pip resolve and a restart.
#
#   sudo bash /data/app/charto/deploy/provision_execution.sh
#
set -euo pipefail

REPO=/data/app
VENV=/data/venv
ENVF="$REPO/pivot/.env"
OWNER="$(stat -c %U "$REPO")"

say() { printf '\n\033[1m== %s\033[0m\n' "$*"; }

# ── 1 · the source ──────────────────────────────────────────────────────────
# Old-style (non-cone) sparse file; `/pivot/` is appended to the existing rules
# rather than replacing them, so `/charto/` keeps working exactly as before.
say "sparse checkout"
SP="$REPO/.git/info/sparse-checkout"
if grep -qx '/pivot/' "$SP"; then
  echo "   /pivot/ already listed"
else
  printf '/pivot/\n' | sudo -u "$OWNER" tee -a "$SP" >/dev/null
  echo "   added /pivot/"
fi
# read-tree materialises what the new rules allow. It touches TRACKED files
# only, so the untracked, gitignored pivot/.env below is safe — which is the
# whole reason that directory was hand-made in the first place.
sudo -u "$OWNER" git -C "$REPO" read-tree -mu HEAD
test -f "$REPO/pivot/backend/__init__.py" \
  || { echo "   FAILED: pivot/backend did not materialise"; exit 1; }
echo "   pivot/backend present"

# ── 2 · the three settings with no default ──────────────────────────────────
# backend/config.py resolves its env file ABSOLUTELY off __file__, so this is
# the file it reads no matter what cwd the service has. Exactly three fields
# are required-with-no-default; everything else defaults. None of the three is
# USED on this box — the bridge dispatches propose/backtest with db=None — but
# Settings is constructed at import time and will raise without them. Appended
# only when missing: the AZURE_* lines already in this file are the dataserver's
# own credentials and must not be touched.
say "pivot/.env required keys"
test -f "$ENVF" || { echo "   FAILED: $ENVF missing (holds AZURE_KEY)"; exit 1; }
add_key() {
  if grep -q "^$1=" "$ENVF"; then
    echo "   $1 already set"
  else
    printf '%s=%s\n' "$1" "$2" | sudo -u "$OWNER" tee -a "$ENVF" >/dev/null
    echo "   $1 added"
  fi
}
# Placeholders, and deliberately unreachable ones. Execution mode's proposal and
# backtest tools need no database; if something ever DOES reach for one, it must
# fail loudly here rather than quietly find a real server.
add_key DATABASE_URL "postgresql+psycopg2://unused:unused@127.0.0.1:1/unused"
add_key REDIS_URL    "redis://127.0.0.1:1/0"
add_key JWT_SECRET_KEY "$(head -c 32 /dev/urandom | base64 | tr -d '=+/' | cut -c1-32)"

# ── 3 · the dependencies ────────────────────────────────────────────────────
# Pivot's own requirements.txt, so this box runs what the engine is tested with.
# The one line allowed to fail is the vectorised greeks pair: it drags in numba
# + llvmlite, whose wheels track the Python version closely, and
# backend/market/greeks/iv.py already falls back to an owned Newton-Raphson +
# Brent solver when the toolchain is absent. A missing optional accelerator is
# not worth failing a provision over; anything else is.
say "python dependencies into $VENV"
"$VENV/bin/python" -V
if ! "$VENV/bin/pip" install --no-input -q -r "$REPO/pivot/requirements.txt"; then
  echo "   full install failed — retrying without the optional greeks solvers"
  grep -v '^py_vollib' "$REPO/pivot/requirements.txt" > /tmp/req-charto-exec.txt
  "$VENV/bin/pip" install --no-input -q -r /tmp/req-charto-exec.txt
  echo "   installed WITHOUT py_vollib* (owned IV solver will be used)"
fi
echo "   $("$VENV/bin/pip" list 2>/dev/null | wc -l) packages present"

# ── 4 · prove the seam before restarting anything ───────────────────────────
say "bridge import check"
"$VENV/bin/python" - <<'PY'
import sys
sys.path.insert(0, "/data/app/charto/data")
import execution_bridge as eb
ok, why = eb.available()
print(f"   available : {ok}{'' if ok else ' | ' + why}")
print(f"   tools     : {len(eb.tools())}")
print(f"   prompt    : {len(eb.system_prompt()):,} chars")
if not ok:
    raise SystemExit("bridge still unavailable — NOT restarting the service")
if len(eb.tools()) != len(eb.PIVOT_TOOLS):
    raise SystemExit(f"expected {len(eb.PIVOT_TOOLS)} tools on the wire")
PY

# ── 5 · restart, and prove it came back ─────────────────────────────────────
say "charto.service"
systemctl restart charto.service
sleep 3
systemctl is-active --quiet charto.service \
  || { echo "   FAILED to come back up"; journalctl -u charto.service -n 30 --no-pager; exit 1; }
echo "   active"
curl -fsS http://127.0.0.1:5174/meta >/dev/null 2>&1 \
  && echo "   /meta answering" || echo "   (could not curl /meta — not fatal)"

# ── 6 · one real turn, because a seam test is not a build ───────────────────
# The import check above proves the tools are on the wire. Only a turn proves
# the model uses them, and this is the exact prompt that came back as an
# invented refusal. Non-fatal: the mode is provisioned either way, and a slow
# or rate-limited model is not a reason to report a failed provision.
say "live execution turn"
"$VENV/bin/python" - <<'PY' || echo "   (live check inconclusive — not fatal)"
import json, urllib.request
body = {"messages": [{"role": "user",
                      "content": "Buy 10 RELIANCE when RSI(14) falls below 30."}],
        "context": {"symbol": "RELIANCE"}, "stream": False, "mode": "execution",
        "chat_id": "provision_check"}
req = urllib.request.Request("http://127.0.0.1:5174/chat",
                             data=json.dumps(body).encode(),
                             headers={"Content-Type": "application/json"})
with urllib.request.urlopen(req, timeout=240) as r:
    p = json.load(r)
tools = [t.get("name") for t in (p.get("tools_used") or [])]
print(f"   tools : {tools or '—'}")
print(f"   card  : {'yes' if p.get('cards') else 'NO'}")
print(f"   reply : {(p.get('text') or '')[:160]}")
if not p.get("cards"):
    raise SystemExit("no card — the builder did not fire")
PY

say "done — Execution mode has an engine behind it"
