# Pivot Agent System — Frontend Handoff

> Single onboarding doc for the human frontend dev who's wiring `pivot-next/` to the backend. Read this end-to-end once; you should have everything you need to ship.
>
> **Backend status:** 22 of 24 step types real, 41/41 smoke checks pass against live uvicorn, 178/178 backend tests pass, CORS configured for your dev ports. **You are not blocked.** Pull `dev` and start.

---

## 1. The 30-second mental model

A user describes a strategy in chat → the chatbot calls a `propose_workflow` tool → the tool returns a structured **WorkflowDraft** → your UI renders an "Open in editor →" card → user opens the panel pre-filled with the draft → user clicks **Activate** → backend arms the trigger → the engine runs the workflow autonomously, pausing for approval before placing orders.

You own everything in `pivot-next/`. You don't write Python. The backend is feature-complete for the demo; if anything's off, file a bug, don't work around it.

---

## 2. Get the backend running locally (90 seconds)

You have two options. Pick whichever is faster to set up.

### 2a. Docker (Postgres + Redis)

```bash
cd pivot
docker-compose up -d                       # postgres + redis
uvicorn backend.main:app --reload --port 8000
# → http://localhost:8000  /docs is FastAPI Swagger
```

### 2b. No Docker (SQLite, fastest path)

```bash
cd pivot
APP_ENV=test JWT_SECRET_KEY="dev-secret-key-minimum-32-characters-long" \
  uvicorn backend.main:app --reload --port 8000
```

In both cases, verify:

```bash
bash pivot/scripts/smoke_test_api.sh
# Expected: 41 / 41 checks passed
```

If smoke fails, that's a backend regression — file it, don't try to fix in the FE.

---

## 3. The single switch you need to flip

Your Day 1 + Day 2 work has a `setBackendSource('mock' | 'real')` toggle in `pivot-next/lib/api.ts`. To switch from mocks to the live backend:

```ts
// pivot-next/app/layout.tsx (or wherever app initialises)
import { setBackendSource } from "@/lib/api";

if (typeof window !== "undefined") {
  setBackendSource("real");          // was 'mock'
}
```

That's it. Catalog fetches, run-stream WS, every API call now hits `http://localhost:8000`. Nothing else needs to change in your components.

You'll also need an auth token. Easiest path:

```bash
TOKEN=$(curl -sS -X POST http://localhost:8000/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"dev@example.com","password":"password123","full_name":"Dev"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")
echo $TOKEN
```

Stash that in localStorage under whatever key your existing auth uses. (If the legacy chat uses cookies + JWT, mirror that — the backend's `auth/router.py` returns the same token shape.)

---

## 4. The contract you're integrating against

**Wire format** is locked in [API_CONTRACT.md](./API_CONTRACT.md). §13 has copy-pasteable curl for every endpoint. Treat that as the source of truth — drift between this doc and API_CONTRACT.md, trust API_CONTRACT.md.

The shapes you'll touch most often:

| Type | Where | Used for |
|---|---|---|
| `Workflow` | §3 | Editor — full shape with `steps[]` |
| `WorkflowSummary` | §11 | List view — no `steps`, no `context` |
| `Run` | §4 | RunView — full shape with `steps[]` and `context` |
| `RunSummary` | §11 | RunHistory list — adds derived `step_count`, omits `context`/`steps[]` |
| `Approval` | §11 | Approval banner in RunView |
| `StepTypeCatalog` | §8 | Picker grouping + StepConfigDrawer form generation |

You already have these as TypeScript types in `pivot-next/lib/types.ts` from your Day 1 work. Verified parity against the backend's Pydantic models — no drift.

### Error envelope — important

Every non-2xx from `/api/*` is:

```json
{ "error": { "code": "...", "message": "...", "details": {...} } }
```

Stable codes (used in your `isError()` discriminator):

| Code | HTTP | When |
|---|---|---|
| `unauthenticated` | 401, 403 | Missing / bad token |
| `not_found` | 404 | Resource doesn't exist or isn't yours (we never 403 — leaks existence) |
| `state_conflict` | 409 | e.g. activating an archived workflow, editing while active |
| `validation_error` | 400, 422 | Step config schema failure — `details.step_index` + `details.field` tell you which |
| `rate_limited` | 429 | Webhook over 60/min |
| `not_yet_available` | 503 | Backend dependency missing (e.g. `fetch.fundamental` for an unknown ticker) |
| `internal_error` | 500 | Unhandled |

Render `error.message` verbatim for non-internal errors. For `validation_error` from `/api/workflows`, surface `details.field` next to the step input that failed (`details.step_index` tells you which step card).

---

## 5. The chatbot tool result you're rendering

When the chatbot calls `propose_workflow`, the chat response includes a tool result with this shape:

```json
{
  "success": true,
  "data": {
    "name": "Buy 10 RELIANCE",
    "description": "Every weekday at 3:55 PM IST, ...",
    "steps": [ /* 5 DraftStep objects — same shape as Workflow.steps */ ],
    "rationale": "Mapped your request to a scheduled trigger ...",
    "warnings": [],
    "_render_hint": "workflow_draft_card"
  }
}
```

When you see `_render_hint === "workflow_draft_card"`:
1. Render an inline card in the chat thread with name, description, step icons, rationale.
2. Surface `warnings[]` if non-empty (e.g. "LLM proposal failed; showing best-effort draft" — user should review every field).
3. Add an **Open in editor →** button.

Clicking the button:
- Opens the AgentPanel.
- Pre-fills WorkflowEditor with `data.steps` (you already have the editor — this is just a different data source than the mock catalog).
- The user is editing a **draft** — nothing is persisted until they click Activate.

When they click Activate:
- POST the full workflow (including any inline edits) to `/api/workflows`. Returns 201 + the created Workflow with an `id`.
- Then POST `/api/workflows/{id}/activate`. Returns 200 + the updated Workflow with `status='active'` and `next_run_at` populated.
- If activate returns 422 with `details.field='config.cron'`, show the cron field's error inline — the user typed an invalid cron at some point.

---

## 6. The WebSocket — live runs

Open `WS /api/runs/{id}/stream` when the user opens a run (manual run, scheduled run, or clicking a row in RunHistory). Auth via `?token=<jwt>` query param OR `Sec-WebSocket-Protocol: bearer.<jwt>` header.

You already have `lib/ws.ts` from Day 1 with auto-reconnect + 2s polling fallback to `getRun(id)`. Just point it at the real endpoint.

**Frame types** (per [API_CONTRACT.md §10](./API_CONTRACT.md#10-websocket-live-run-stream)):

| `type` | When | Use |
|---|---|---|
| `snapshot` | On connect | Initial state — render the full Run shape |
| `step_update` | Each step status change | Update the matching StepCard in RunView |
| `approval_requested` | `wait.approval` or `requires_approval=true` fires | Show the approval banner with Approve / Reject buttons |
| `run_update` | Run reaches terminal status | Update overall status; server closes WS with code 1000 right after |
| `ping` | Every 30s idle | Reply with `{"type":"pong"}` |

When the user clicks Approve / Reject in the banner, POST to `/api/approvals/{id}/decision` with `{"decision":"approved"|"rejected"}`. The engine resumes; you'll see the next `step_update` come through the WS within ~100ms.

---

## 7. CORS — already configured for you

`backend/config.py:allowed_origins` defaults to `http://localhost:3000,http://localhost:5173`. Your Next.js dev server (`pnpm dev` at `pivot-next/`) is at `:3000` by default, so you can hit the backend directly from the browser without a proxy.

If you need to add another origin (e.g. `:3001`), edit `pivot/.env` `ALLOWED_ORIGINS` and restart uvicorn.

---

## 8. What's mock vs real on the backend

**Real** (works against the live API):
- Every endpoint in API_CONTRACT.md §5-§9
- Every step executor for the demo path + 16 others
- Cron triggers (`trigger.schedule`) — actually fire
- Price/indicator triggers — actually poll quotes during NSE market hours and fire when conditions cross
- Approval flow — pause + resume
- WebSocket frames — real, not mocked

**Mock-via-Kite** (functional, but no real broker):
- `action.place_order` returns a synthetic order ID when `KITE_API_KEY` isn't set in `.env`
- `fetch.portfolio` returns the canned holdings from `backend/kite/mock_data.py`
- `fetch.quote` falls back to yfinance when Kite mock returns only `last_price`
- `fetch.fundamental` uses yfinance directly (no Kite key needed)

**Cut to v2** (the catalog still publishes them so your picker stays consistent, but executing fails with a clear "not yet implemented" message):
- `trigger.event` — no event source wired
- `fetch.news` — no news source wired

`action.update_watchlist` is real but persists to a brand-new `watchlist_items` table — there's no read endpoint yet (no UI surface for the watchlist itself). If you want one for v2, file it.

---

## 9. The smoke test as your green/red signal

`pivot/scripts/smoke_test_api.sh` is what we use to know "the backend works end-to-end". Run it whenever:
- You hit a 4xx/5xx that surprises you (smoke confirms the endpoint itself is fine — the bug is in your request).
- The backend dev says "I just shipped X" (smoke verifies they didn't regress anything).
- Before opening a PR that depends on the backend.

Output is `41 / 41 checks passed` when green. Red prints the failing endpoint + the response body. Smoke is a contract test — if it goes red, the backend is broken; do not work around it.

---

## 10. Things you can ignore

These are all backend concerns; you don't need to know any of them to ship:

- **Engine invariants** (idempotency, persistence-before-emit, time budget, advisory locks) — handled.
- **Schema validation at the API boundary** — handled. You'll get a clean `validation_error` with `details.step_index` + `details.field`.
- **WS backpressure** — handled. The `ping`/`pong` cadence keeps the connection alive without us flooding.
- **Cron computation / DST / timezones** — handled. Just send the cron string + IANA timezone, the backend handles the rest.
- **`client_request_id` / Kite idempotency** — handled. The engine derives it deterministically; the broker rejects duplicates on retry.
- **Rate limits** — only webhooks have one (60/min per token). Your API calls aren't rate-limited.

---

## 11. Quick reference

| Need | Where |
|---|---|
| Wire format details | [API_CONTRACT.md](./API_CONTRACT.md) |
| End-to-end demo trace (chat → DB) | [SYSTEM_WALKTHROUGH.md](./SYSTEM_WALKTHROUGH.md) |
| Architecture / invariants | [ARCHITECTURE.md](./ARCHITECTURE.md) |
| What's planned for v2 | [BACKLOG.md](../BACKLOG.md) |
| Daily progress log | [STATUS.md](../STATUS.md) |
| Smoke test script | `pivot/scripts/smoke_test_api.sh` |

---

## 12. Backend contact / escalation

If something looks like a backend bug:
1. Run the smoke test. If it's red, file the failing endpoint name + response.
2. If smoke is green but your call still fails, paste the curl that reproduces, with the full response body.
3. Don't try to fix backend things in the frontend (mock-around, retry-storms, hardcoded values). It compounds.

Backend changes go to the lead session (this repo's `dev` branch). PRs welcome on the FE side; don't merge into `main` without lead approval — there's a strict no-push-without-signoff rule.

Good luck. Ship something polished.
