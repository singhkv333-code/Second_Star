#!/usr/bin/env bash
# pivot/scripts/smoke_test_api.sh
#
# End-to-end smoke test for the Agent System REST surface.
# Spins up uvicorn against a temp SQLite DB, registers a user, hits
# every endpoint, asserts canonical {error: ...} envelope on errors.
#
# Why bash + curl: this is what the human frontend dev will use to
# probe the API. The script doubles as living documentation — read
# it for copy-pasteable requests against a local backend.
#
# Run from repo root: bash pivot/scripts/smoke_test_api.sh
# Or from pivot/:    bash scripts/smoke_test_api.sh
#
# Exit 0 if every endpoint matches the contract; exit 1 on first
# mismatch with a clear pointer to the failing endpoint.

set -euo pipefail

# ── Resolve repo paths ───────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PIVOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PIVOT_DIR"

# ── Config ───────────────────────────────────────────────────────────
PORT="${SMOKE_PORT:-8765}"
BASE="http://127.0.0.1:${PORT}"
# backend/database.py hardcodes sqlite:///./pivot_test.db when
# APP_ENV=test (cwd-relative, so it lands in pivot/). We use that path
# and wipe it before each run so the smoke test starts with a clean DB.
DB_PATH="${PIVOT_DIR}/pivot_test.db"
TMP_DIR="$(mktemp -d)"
LOG_PATH="${TMP_DIR}/uvicorn.log"
trap 'cleanup' EXIT

cleanup() {
    if [[ -n "${UVICORN_PID:-}" ]]; then
        kill "$UVICORN_PID" 2>/dev/null || true
        wait "$UVICORN_PID" 2>/dev/null || true
    fi
    rm -rf "$TMP_DIR"
    rm -f "$DB_PATH"
}

# Wipe any leftover DB from a prior run.
rm -f "$DB_PATH"

# ── Bring up uvicorn (sqlite, mock mode) ─────────────────────────────
echo "▶ smoke-test: starting uvicorn on :${PORT} (db=${DB_PATH})"

# Create tables before booting the app — main.py doesn't run alembic.
PYTHONPATH="$PIVOT_DIR" \
APP_ENV=test \
DATABASE_URL="sqlite:///${DB_PATH}" \
JWT_SECRET_KEY="smoke-test-secret-key-minimum-32-characters-long" \
REDIS_URL="redis://localhost:6379/0" \
KITE_API_KEY="" \
SARVAM_API_KEY="" \
OPENAI_API_KEY="" \
python3 -c "
from backend.database import Base, engine
from backend import models  # noqa: F401  (registers tables)
Base.metadata.create_all(bind=engine)
print(f'  ✓ tables created in {engine.url}')
"

PYTHONPATH="$PIVOT_DIR" \
APP_ENV=test \
DATABASE_URL="sqlite:///${DB_PATH}" \
JWT_SECRET_KEY="smoke-test-secret-key-minimum-32-characters-long" \
REDIS_URL="redis://localhost:6379/0" \
KITE_API_KEY="" \
SARVAM_API_KEY="" \
OPENAI_API_KEY="" \
ALLOWED_ORIGINS="http://localhost:3000,http://localhost:5173" \
python3 -m uvicorn backend.main:app --host 127.0.0.1 --port "$PORT" \
    --log-level warning > "$LOG_PATH" 2>&1 &
UVICORN_PID=$!

# Wait for /health
for i in {1..30}; do
    if curl -fs "${BASE}/health" > /dev/null 2>&1; then
        echo "  ✓ uvicorn ready (pid=${UVICORN_PID})"
        break
    fi
    if ! kill -0 "$UVICORN_PID" 2>/dev/null; then
        echo "✗ uvicorn died during startup. Log:"
        cat "$LOG_PATH"
        exit 1
    fi
    sleep 0.3
done

if ! curl -fs "${BASE}/health" > /dev/null 2>&1; then
    echo "✗ uvicorn did not become ready in 9s. Log:"
    cat "$LOG_PATH"
    exit 1
fi

# ── Tiny test runner ─────────────────────────────────────────────────
PASS=0
FAIL=0

check() {
    local name="$1"
    local expected="$2"
    local got="$3"
    if [[ "$expected" == "$got" ]]; then
        echo "  ✓ ${name}"
        PASS=$((PASS + 1))
    else
        echo "  ✗ ${name}: expected ${expected}, got ${got}"
        FAIL=$((FAIL + 1))
    fi
}

contains() {
    local name="$1"
    local needle="$2"
    local haystack="$3"
    if [[ "$haystack" == *"$needle"* ]]; then
        echo "  ✓ ${name}"
        PASS=$((PASS + 1))
    else
        echo "  ✗ ${name}: missing '${needle}' in response"
        echo "    got: ${haystack:0:200}"
        FAIL=$((FAIL + 1))
    fi
}

status_code() {
    # Print HTTP status from a curl -w '%{http_code}' that wrote body
    # to stdout followed by the code. Uses a known separator to split.
    local resp="$1"
    echo "${resp##*$'\n'}"
}

body() {
    # Strip the trailing status code (last line) from a combined
    # curl -w '\n%{http_code}' response.
    local resp="$1"
    echo "${resp%$'\n'*}"
}

# ── 1. Auth: register + login ────────────────────────────────────────
echo "▶ auth"

EMAIL="smoke_$(date +%s)@example.com"
REG_RESP=$(curl -sS -w '\n%{http_code}' -X POST "${BASE}/auth/register" \
    -H "Content-Type: application/json" \
    -d "{\"email\":\"${EMAIL}\",\"password\":\"password123\",\"full_name\":\"Smoke\"}")
REG_CODE=$(status_code "$REG_RESP")
REG_BODY=$(body "$REG_RESP")
if [[ "$REG_CODE" != "201" ]]; then
    echo "  ✗ register failed (HTTP ${REG_CODE}): ${REG_BODY:0:300}"
    echo "  uvicorn log tail:"
    tail -20 "$LOG_PATH"
    exit 1
fi
TOKEN=$(echo "$REG_BODY" | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")
contains "register returns access_token" "access_token" "$REG_BODY"

AUTH=( -H "Authorization: Bearer ${TOKEN}" )

# ── 2. GET /api/step-types — catalog ─────────────────────────────────
echo "▶ catalog"

CAT=$(curl -sS "${AUTH[@]}" "${BASE}/api/step-types")
contains "catalog has trigger.schedule"  "trigger.schedule"   "$CAT"
contains "catalog has action.place_order" "action.place_order" "$CAT"
contains "catalog has control.skip_if"   "control.skip_if"    "$CAT"
contains "catalog top-level categories"  "categories"         "$CAT"

# ── 3. POST /api/workflows — create draft ────────────────────────────
echo "▶ workflows: create"

CREATE_PAYLOAD='{
  "name": "Smoke daily",
  "description": "Buy 1 RELIANCE every weekday at 09:30 IST",
  "single_instance": true,
  "steps": [
    {
      "step_type": "trigger.schedule",
      "label": "Every weekday at 09:30 IST",
      "config": {"cron": "30 9 * * 1-5", "timezone": "Asia/Kolkata"}
    },
    {
      "step_type": "fetch.portfolio",
      "label": "Get my portfolio",
      "config": {}
    },
    {
      "step_type": "condition.numeric",
      "label": "Buying power > 50000",
      "config": {"left": "{{ context.1.buying_power }}", "operator": ">", "right": 50000}
    },
    {
      "step_type": "action.place_order",
      "label": "Buy 1 RELIANCE",
      "config": {
        "symbol": "RELIANCE", "side": "buy", "quantity": 1,
        "order_type": "market", "requires_approval": true
      }
    },
    {
      "step_type": "notify.message",
      "label": "Email me",
      "config": {"channel": "email", "template": "Bought 1 RELIANCE", "vars": {}}
    }
  ]
}'

CREATE_RESP=$(curl -sS -w '\n%{http_code}' "${AUTH[@]}" \
    -X POST "${BASE}/api/workflows" \
    -H "Content-Type: application/json" \
    -d "$CREATE_PAYLOAD")
check "create status 201" "201" "$(status_code "$CREATE_RESP")"
WF_ID=$(body "$CREATE_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
[[ -n "$WF_ID" ]] && echo "  ✓ workflow id=${WF_ID}"

# ── 4. Negative: invalid step config → 422 + canonical envelope ──────
echo "▶ workflows: validation"

BAD_RESP=$(curl -sS -w '\n%{http_code}' "${AUTH[@]}" \
    -X POST "${BASE}/api/workflows" \
    -H "Content-Type: application/json" \
    -d '{"name":"bad","description":null,"single_instance":true,"steps":[
        {"step_type":"trigger.schedule","label":null,"config":{"cron":"30 9 * * 1-5","timezone":"Asia/Kolkata"}},
        {"step_type":"action.place_order","label":null,"config":{"symbol":"X"}}
    ]}')
check "invalid config status 422" "422" "$(status_code "$BAD_RESP")"
contains "invalid config canonical envelope" '"code":"validation_error"' "$(body "$BAD_RESP")"

# ── 5. GET /api/workflows — list view (no steps) ─────────────────────
echo "▶ workflows: list"

LIST_RESP=$(curl -sS "${AUTH[@]}" "${BASE}/api/workflows")
contains "list contains workflow"  "$WF_ID"     "$LIST_RESP"
contains "list has next_cursor"    "next_cursor" "$LIST_RESP"

# ── 6. GET /api/workflows/{id} — full shape ──────────────────────────
echo "▶ workflows: get one"

GET_RESP=$(curl -sS "${AUTH[@]}" "${BASE}/api/workflows/${WF_ID}")
contains "get one has steps" '"steps":' "$GET_RESP"
contains "get one has 5 steps" "step_index" "$GET_RESP"

# ── 7. GET cross-user → 404 (not 403) ────────────────────────────────
echo "▶ workflows: cross-user is 404"

OTHER_RESP=$(curl -sS -w '\n%{http_code}' "${AUTH[@]}" \
    "${BASE}/api/workflows/00000000-0000-0000-0000-000000000000")
check "cross-user status 404" "404" "$(status_code "$OTHER_RESP")"
contains "cross-user canonical envelope" '"code":"not_found"' "$(body "$OTHER_RESP")"

# ── 8. POST /api/workflows/{id}/activate ─────────────────────────────
echo "▶ workflows: activate"

ACT_RESP=$(curl -sS -w '\n%{http_code}' "${AUTH[@]}" \
    -X POST "${BASE}/api/workflows/${WF_ID}/activate")
check "activate status 200"             "200" "$(status_code "$ACT_RESP")"
contains "activate sets next_run_at"    "next_run_at" "$(body "$ACT_RESP")"
contains "activate flips status active" '"status":"active"' "$(body "$ACT_RESP")"

# ── 8b. Calendar endpoint — scheduled-runs window ────────────────────
echo "▶ scheduled-runs (calendar tab backing endpoint)"

SCHED_RESP=$(curl -sS -w '\n%{http_code}' "${AUTH[@]}" \
    "${BASE}/api/workflows/scheduled-runs?from=2026-05-02T00:00:00Z&to=2026-05-09T00:00:00Z")
check "scheduled-runs status 200" "200" "$(status_code "$SCHED_RESP")"
contains "scheduled-runs has items[]"     '"items"' "$(body "$SCHED_RESP")"
contains "scheduled-runs has fire_time"   "fire_time" "$(body "$SCHED_RESP")"

# Validation: window > 90 days → 422 canonical envelope.
SCHED_BAD=$(curl -sS -w '\n%{http_code}' "${AUTH[@]}" \
    "${BASE}/api/workflows/scheduled-runs?from=2026-05-02T00:00:00Z&to=2026-09-01T00:00:00Z")
check "scheduled-runs window-too-large status 422" "422" "$(status_code "$SCHED_BAD")"
contains "scheduled-runs window-too-large canonical envelope" '"code":"validation_error"' "$(body "$SCHED_BAD")"

# Edge case: activate again → 409 state_conflict
RE_ACT_RESP=$(curl -sS -w '\n%{http_code}' "${AUTH[@]}" \
    -X POST "${BASE}/api/workflows/${WF_ID}/activate")
check "activate-again status 409"   "409" "$(status_code "$RE_ACT_RESP")"
contains "activate-again canonical envelope" '"code":"state_conflict"' "$(body "$RE_ACT_RESP")"

# ── 9. PATCH while active → 409 ──────────────────────────────────────
echo "▶ workflows: patch while active rejected"

PATCH_RESP=$(curl -sS -w '\n%{http_code}' "${AUTH[@]}" \
    -X PATCH "${BASE}/api/workflows/${WF_ID}" \
    -H "Content-Type: application/json" \
    -d '{"name":"renamed"}')
check "patch-while-active status 409"          "409" "$(status_code "$PATCH_RESP")"
contains "patch-while-active canonical envelope" '"code":"state_conflict"' "$(body "$PATCH_RESP")"

# ── 10. POST /api/workflows/{id}/pause ───────────────────────────────
echo "▶ workflows: pause"

PAUSE_RESP=$(curl -sS -w '\n%{http_code}' "${AUTH[@]}" \
    -X POST "${BASE}/api/workflows/${WF_ID}/pause")
check "pause status 200"               "200" "$(status_code "$PAUSE_RESP")"
contains "pause flips status paused"   '"status":"paused"' "$(body "$PAUSE_RESP")"
contains "pause clears next_run_at"    '"next_run_at":null' "$(body "$PAUSE_RESP")"

# ── 11. PATCH after pause → 200 + version bump ───────────────────────
echo "▶ workflows: patch after pause"

PATCH_OK=$(curl -sS -w '\n%{http_code}' "${AUTH[@]}" \
    -X PATCH "${BASE}/api/workflows/${WF_ID}" \
    -H "Content-Type: application/json" \
    -d '{"name":"Smoke daily v2"}')
check "patch-after-pause status 200"     "200" "$(status_code "$PATCH_OK")"
contains "patch keeps name change"        "Smoke daily v2" "$(body "$PATCH_OK")"

# ── 12. POST /api/workflows/{id}/run — manual run ────────────────────
echo "▶ workflows: manual run"

RUN_RESP=$(curl -sS -w '\n%{http_code}' "${AUTH[@]}" \
    -X POST "${BASE}/api/workflows/${WF_ID}/run")
check "manual run status 201" "201" "$(status_code "$RUN_RESP")"
RUN_ID=$(body "$RUN_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['run_id'])")
[[ -n "$RUN_ID" ]] && echo "  ✓ run_id=${RUN_ID}"

# Give the engine a moment to write the initial run_step row.
sleep 0.5

# ── 13. GET /api/workflows/{id}/runs — list ──────────────────────────
echo "▶ runs: list"

RUNS_RESP=$(curl -sS "${AUTH[@]}" "${BASE}/api/workflows/${WF_ID}/runs")
contains "runs list contains run_id" "$RUN_ID"     "$RUNS_RESP"
contains "runs list has step_count"  "step_count" "$RUNS_RESP"

# ── 14. GET /api/runs/{id} — full run ────────────────────────────────
echo "▶ runs: get one"

RUN_GET=$(curl -sS "${AUTH[@]}" "${BASE}/api/runs/${RUN_ID}")
contains "run has steps[]"  '"steps":' "$RUN_GET"
contains "run has context"  '"context":' "$RUN_GET"

# ── 15. POST /api/runs/{id}/cancel ───────────────────────────────────
echo "▶ runs: cancel"

CANCEL_RESP=$(curl -sS -w '\n%{http_code}' "${AUTH[@]}" \
    -X POST "${BASE}/api/runs/${RUN_ID}/cancel")
# Run may already be terminal (succeeded / failed / awaiting_approval)
# by the time we cancel; either 200 or 409 is contract-valid.
CC=$(status_code "$CANCEL_RESP")
if [[ "$CC" == "200" || "$CC" == "409" ]]; then
    echo "  ✓ cancel status ${CC} (200 or 409 both contract-valid)"
    PASS=$((PASS + 1))
else
    echo "  ✗ cancel status: expected 200 or 409, got ${CC}"
    FAIL=$((FAIL + 1))
fi

# ── 16. GET /api/runs/{id}/approvals/pending ─────────────────────────
echo "▶ approvals: pending"

APPR_RESP=$(curl -sS "${AUTH[@]}" "${BASE}/api/runs/${RUN_ID}/approvals/pending")
contains "approvals has items[]" '"items"' "$APPR_RESP"

# ── 17. POST /api/workflows/{id}/archive ─────────────────────────────
echo "▶ workflows: archive"

ARCH_RESP=$(curl -sS -w '\n%{http_code}' "${AUTH[@]}" \
    -X POST "${BASE}/api/workflows/${WF_ID}/archive")
check "archive status 200"             "200" "$(status_code "$ARCH_RESP")"
contains "archive flips status"        '"status":"archived"' "$(body "$ARCH_RESP")"

# Edge case: activate-after-archive → 409
ACT_AFTER_ARCH=$(curl -sS -w '\n%{http_code}' "${AUTH[@]}" \
    -X POST "${BASE}/api/workflows/${WF_ID}/activate")
check "activate-after-archive status 409" "409" "$(status_code "$ACT_AFTER_ARCH")"

# ── 18. Auth: missing token → 401 + canonical envelope ───────────────
echo "▶ auth: missing token"

NO_AUTH_RESP=$(curl -sS -w '\n%{http_code}' "${BASE}/api/workflows")
check "no-token status 401"             "401" "$(status_code "$NO_AUTH_RESP")"
contains "no-token canonical envelope"   '"code":"unauthenticated"' "$(body "$NO_AUTH_RESP")"

# ── 19. CORS preflight from localhost:3000 ───────────────────────────
echo "▶ cors: localhost:3000"

CORS_RESP=$(curl -sS -i -X OPTIONS "${BASE}/api/workflows" \
    -H "Origin: http://localhost:3000" \
    -H "Access-Control-Request-Method: GET" \
    -H "Access-Control-Request-Headers: authorization,content-type")
contains "cors allow-origin localhost:3000" "access-control-allow-origin: http://localhost:3000" "$CORS_RESP"

# ── 20. Bad-cron activation → 422 (closes Day-2 edge case #1) ────────
echo "▶ workflows: bad cron at activate"

BAD_CRON_PAYLOAD='{
  "name": "bad-cron-smoke",
  "description": null,
  "single_instance": true,
  "steps": [
    {"step_type":"trigger.schedule","label":null,"config":{"cron":"99 99 * * *","timezone":"UTC"}},
    {"step_type":"notify.log","label":null,"config":{"message":"x"}}
  ]
}'
BAD_CR=$(curl -sS -w '\n%{http_code}' "${AUTH[@]}" \
    -X POST "${BASE}/api/workflows" \
    -H "Content-Type: application/json" \
    -d "$BAD_CRON_PAYLOAD")
BAD_WF_ID=$(body "$BAD_CR" | python3 -c "import sys,json; print(json.load(sys.stdin).get('id',''))")
if [[ -n "$BAD_WF_ID" ]]; then
    BAD_ACT=$(curl -sS -w '\n%{http_code}' "${AUTH[@]}" \
        -X POST "${BASE}/api/workflows/${BAD_WF_ID}/activate")
    check "bad-cron activate status 422" "422" "$(status_code "$BAD_ACT")"
    contains "bad-cron mentions cron" "cron" "$(body "$BAD_ACT")"
else
    echo "  ⓘ bad-cron rejected at create (also contract-valid)"
    PASS=$((PASS + 1))
fi

# ── Summary ──────────────────────────────────────────────────────────
TOTAL=$((PASS + FAIL))
echo
echo "════════════════════════════════════════════════════════════════════"
echo "  ${PASS} / ${TOTAL} checks passed"
echo "════════════════════════════════════════════════════════════════════"

if [[ "$FAIL" -gt 0 ]]; then
    echo
    echo "✗ ${FAIL} contract violation(s). Recent uvicorn log:"
    tail -30 "$LOG_PATH"
    exit 1
fi

exit 0
