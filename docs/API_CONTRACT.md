# Pivot Agent System — API Contract v1

> Single source of truth for the REST + WebSocket surface. Backend implements; frontend codes against. Drift is a blocking review failure.

**Versioned:** changes to this doc require both backend and frontend leads to acknowledge before merge.

---

## 1. Conventions

- **Base URL:** all endpoints under `/api`.
- **Content-Type:** `application/json` (UTF-8) for request and response unless noted.
- **Authentication:** Bearer JWT (existing scheme from `pivot/backend/auth/`). Header: `Authorization: Bearer <token>`. Webhooks (`POST /api/webhooks/{token}`) are unauthenticated.
- **User scoping:** every workflow/run/approval query filters by the authenticated user. A user can never read or mutate another user's resources. Cross-user access returns `404` (not `403`) to avoid leaking existence.
- **Timestamps:** ISO 8601 UTC, e.g. `"2026-05-08T14:25:30.123Z"`. Timezones for cron/time-window configs are stored as IANA strings (e.g. `"Asia/Kolkata"`).
- **IDs:** UUID v4 strings.
- **Status codes:** `200` for fetches, `201` for create, `204` for no-content actions, `400` for validation errors, `401` for missing/invalid auth, `404` for not found / not yours, `409` for state-conflict (e.g. activating an archived workflow), `422` for schema validation errors with detail, `500` for unhandled errors.
- **Pagination:** cursor-based. Query params: `limit` (default 20, max 100), `cursor` (opaque string returned by previous page). Response includes `next_cursor` (null if no more).
- **Idempotency:** mutating endpoints accept optional `Idempotency-Key` header. Server stores `(user_id, idempotency_key)` for 24h and returns the prior response on repeats.

---

## 2. Error format

All non-2xx responses use this exact shape:

```json
{
  "error": {
    "code": "validation_error",
    "message": "Step 2 config invalid: 'symbol' is required",
    "details": {
      "step_index": 2,
      "field": "symbol",
      "reason": "missing_required"
    }
  }
}
```

Stable error codes:
- `validation_error` — request body or step config failed schema validation (also `422`)
- `not_found` — resource doesn't exist or isn't yours (`404`)
- `state_conflict` — operation invalid for current state, e.g. activating an archived workflow (`409`)
- `unauthenticated` — missing/expired token (`401`)
- `not_yet_available` — backend dependency not ready (e.g. fundamentals DB) — actionable message (`503`)
- `internal_error` — unhandled (`500`)
- `rate_limited` — `429`

Frontend renders `error.message` to the user verbatim for non-internal errors. Internal errors get a generic message + a Sentry-style trace id.

---

## 3. Workflow shape (canonical)

Used in responses for `GET /api/workflows`, `GET /api/workflows/{id}`, etc.

```json
{
  "id": "11111111-2222-3333-4444-555555555555",
  "name": "QQQ buy close, sell open",
  "description": "Buy ₹5K of QQQ at 3:55 PM, sell at 9:35 AM next day",
  "status": "active",
  "version": 3,
  "single_instance": true,
  "created_at": "2026-05-02T08:00:00Z",
  "updated_at": "2026-05-04T11:23:00Z",
  "activated_at": "2026-05-03T09:00:00Z",
  "last_run_at": "2026-05-07T15:55:01Z",
  "next_run_at": "2026-05-08T15:55:00Z",
  "steps": [
    {
      "id": "aaaaaaaa-...-...",
      "step_index": 0,
      "step_type": "trigger.schedule",
      "label": "Every weekday at 3:55 PM IST",
      "config": { "cron": "55 15 * * 1-5", "timezone": "Asia/Kolkata" }
    },
    {
      "id": "bbbbbbbb-...-...",
      "step_index": 1,
      "step_type": "fetch.portfolio",
      "label": "Get my portfolio",
      "config": {}
    }
  ]
}
```

`steps` are returned in `step_index` order (ascending). `null` is returned for unset optional timestamps (`activated_at`, `last_run_at`, `next_run_at`).

---

## 4. Run shape (canonical)

```json
{
  "id": "99999999-...",
  "workflow_id": "11111111-...",
  "workflow_version": 3,
  "triggered_by": "manual",
  "started_at": "2026-05-07T10:15:30Z",
  "finished_at": "2026-05-07T10:15:42Z",
  "status": "succeeded",
  "halt_reason": null,
  "error_message": null,
  "context": {
    "1": { "buying_power": 75000, "total_value": 230000, "holdings": [] }
  },
  "steps": [
    {
      "step_index": 0,
      "step_type": "trigger.schedule",
      "status": "succeeded",
      "started_at": "2026-05-07T10:15:30Z",
      "finished_at": "2026-05-07T10:15:30Z",
      "output": null,
      "error_message": null,
      "attempts": 1
    },
    {
      "step_index": 1,
      "step_type": "fetch.portfolio",
      "status": "succeeded",
      "started_at": "2026-05-07T10:15:30Z",
      "finished_at": "2026-05-07T10:15:31Z",
      "output": { "buying_power": 75000, "total_value": 230000, "holdings": [] },
      "error_message": null,
      "attempts": 1
    }
  ]
}
```

`context` is keyed by stringified `step_index`. Top-level `steps[]` is the durable execution log; `context` is the runtime data bag.

---

## 5. Workflow endpoints

### 5.1 `POST /api/workflows`

Create a new workflow. Steps may be empty; status defaults to `draft`.

**Request:**
```json
{
  "name": "QQQ buy close, sell open",
  "description": "Buy ₹5K of QQQ at 3:55 PM, sell at 9:35 AM next day",
  "single_instance": true,
  "steps": [
    { "step_type": "trigger.schedule", "label": "Every weekday at 3:55 PM IST", "config": { "cron": "55 15 * * 1-5", "timezone": "Asia/Kolkata" } },
    { "step_type": "fetch.portfolio", "label": null, "config": {} }
  ]
}
```

**Response: 201** — full Workflow shape (§3).

**Errors:**
- `422 validation_error` — any step config fails schema. `details.step_index` identifies the offender.
- `400 validation_error` — `step_index=0` is not a `trigger.*`.

### 5.2 `GET /api/workflows`

List the authenticated user's workflows.

**Query params:** `status` (optional, comma-separated: `draft`, `active`, `paused`, `archived`), `limit`, `cursor`.

**Response: 200**
```json
{
  "items": [ /* Workflow shape, but without `steps` field for list view */ ],
  "next_cursor": "eyJ0Ijo..."
}
```

For list responses, omit `steps` and `context` to keep payload small. Frontend fetches `GET /api/workflows/{id}` for details.

### 5.3 `GET /api/workflows/{id}`

Full Workflow shape including `steps[]`. **404** if not found or not yours.

### 5.4 `PATCH /api/workflows/{id}`

Update name, description, single_instance, and/or steps. **Bumps `version`** if `steps` changed. Cannot edit while `status='active'`; client must pause first (else `409 state_conflict`).

**Request (any subset of):**
```json
{
  "name": "...",
  "description": "...",
  "single_instance": true,
  "steps": [ /* new full step list — replaces existing */ ]
}
```

If `steps` is provided, it **fully replaces** the existing list. Partial step edits are not supported in v1; client always sends the full list.

**Response: 200** — updated Workflow shape.

### 5.5 `POST /api/workflows/{id}/activate`

Transition `status` from `draft|paused` → `active`. Re-validates all step configs. Computes `next_run_at` if a `trigger.schedule` is present. Registers price/indicator triggers with the watcher.

**Response: 200** — updated Workflow shape with `activated_at` set and `next_run_at` populated.

**Errors:**
- `409 state_conflict` — already active, or status is `archived`.
- `422 validation_error` — re-validation failed.

### 5.6 `POST /api/workflows/{id}/pause`

`active` → `paused`. Cancels future trigger firings; in-flight runs continue to completion (or are cancelled separately via `POST /api/runs/{id}/cancel`).

**Response: 200** — updated Workflow shape.

### 5.7 `POST /api/workflows/{id}/archive`

Any status → `archived`. Soft-delete; resource still queryable but not listed by default.

**Response: 200** — updated Workflow shape.

### 5.8 `POST /api/workflows/{id}/run`

Manual run. Creates a `workflow_runs` row with `triggered_by='manual'` and enqueues. Allowed regardless of status (including `paused`), as long as not `archived`.

**Response: 201**
```json
{ "run_id": "99999999-..." }
```

Frontend should immediately open `WS /api/runs/{run_id}/stream`.

---

## 6. Run endpoints

### 6.1 `GET /api/workflows/{id}/runs`

Paginated run history for a workflow. Newest first.

**Query params:** `limit`, `cursor`, `status` (optional comma-separated filter).

**Response: 200**
```json
{
  "items": [ /* Run list-view shape — see fields below */ ],
  "next_cursor": null
}
```

List items include: `id`, `workflow_id`, `workflow_version`, `triggered_by`, `started_at`, `finished_at`, `status`, `halt_reason`, `error_message`, `step_count` (int — total steps in the workflow at run time, for display; **not** in the canonical Run shape §4 which is returned only by `GET /api/runs/{id}`). `context` and `steps[]` are omitted from list items to keep payload small.

### 6.2 `GET /api/runs/{id}`

Full Run shape (§4).

### 6.3 `POST /api/runs/{id}/cancel`

Mark an in-flight run as `cancelled`. Engine checks the cancel flag at every step boundary. No-op if run is already terminal.

**Response: 200**
```json
{ "id": "...", "status": "cancelled", "finished_at": "..." }
```

---

## 7. Approval endpoints

### 7.1 `GET /api/runs/{id}/approvals/pending`

Returns all approvals for the run that are still undecided.

**Response: 200**
```json
{
  "items": [
    {
      "id": "ccccccc-...",
      "run_id": "99999999-...",
      "step_index": 3,
      "summary": "Buy 10 RELIANCE at market",
      "requested_at": "2026-05-07T15:56:00Z",
      "expires_at": "2026-05-07T16:11:00Z"
    }
  ]
}
```

### 7.2 `POST /api/approvals/{id}/decision`

Resolve a pending approval.

**Request:**
```json
{ "decision": "approved" }
```
or
```json
{ "decision": "rejected" }
```

**Response: 200**
```json
{
  "id": "ccccccc-...",
  "decision": "approved",
  "decided_at": "2026-05-07T15:57:14Z"
}
```

On `approved`, the engine resumes the gated step. On `rejected`, the run terminates with `status='cancelled'` and `error_message='approval rejected at step <index>'`.

**Errors:**
- `409 state_conflict` — already decided, or expired (`error.details.reason='expired'`).

---

## 8. Step-type catalog

### 8.1 `GET /api/step-types`

Frontend fetches once on app load (cache 5 min). Backend changes to step types invalidate by bumping `catalog_version`.

**Response: 200**
```json
{
  "catalog_version": "2026-05-02T00:00:00Z",
  "categories": [
    { "id": "trigger",   "label": "Triggers" },
    { "id": "fetch",     "label": "Data fetches" },
    { "id": "condition", "label": "Conditions" },
    { "id": "action",    "label": "Actions" },
    { "id": "notify",    "label": "Communication" },
    { "id": "control",   "label": "Control flow" }
  ],
  "step_types": [
    {
      "step_type": "trigger.schedule",
      "category": "trigger",
      "label": "On schedule",
      "description": "Run on a cron schedule",
      "icon": "clock",
      "max_retries": 0,
      "trigger_only": true,
      "config_schema": {
        "type": "object",
        "properties": {
          "cron":     { "type": "string", "description": "Cron expression, 5-field" },
          "timezone": { "type": "string", "description": "IANA timezone, e.g. Asia/Kolkata", "default": "Asia/Kolkata" }
        },
        "required": ["cron", "timezone"]
      },
      "output_schema": null
    },
    {
      "step_type": "fetch.portfolio",
      "category": "fetch",
      "label": "Get portfolio",
      "description": "Fetches holdings, buying power, and total value",
      "icon": "wallet",
      "max_retries": 3,
      "trigger_only": false,
      "config_schema": { "type": "object", "properties": {}, "required": [] },
      "output_schema": {
        "type": "object",
        "properties": {
          "holdings":      { "type": "array" },
          "buying_power":  { "type": "number" },
          "total_value":   { "type": "number" }
        }
      }
    }
  ]
}
```

Every step type in the catalog must include: `step_type`, `category`, `label`, `description`, `icon` (lucide-react name), `max_retries`, `trigger_only`, `config_schema` (JSON Schema draft 2020-12), `output_schema` (or null if no output).

**Canonical category assignment for every v1 step type** (backend must return exactly these `category` values):

| `step_type` | `category` |
|---|---|
| `trigger.schedule` | `trigger` |
| `trigger.price` | `trigger` |
| `trigger.indicator` | `trigger` |
| `trigger.event` | `trigger` |
| `trigger.manual` | `trigger` |
| `trigger.webhook` | `trigger` |
| `fetch.quote` | `fetch` |
| `fetch.indicator` | `fetch` |
| `fetch.fundamental` | `fetch` |
| `fetch.portfolio` | `fetch` |
| `fetch.news` | `fetch` |
| `condition.numeric` | `condition` |
| `condition.market_status` | `condition` |
| `condition.position` | `condition` |
| `condition.time_window` | `condition` |
| `action.place_order` | `action` |
| `action.cancel_orders` | `action` |
| `action.set_stoploss` | `action` |
| `action.update_watchlist` | `action` |
| `notify.message` | `notify` |
| `notify.log` | `notify` |
| `wait.approval` | `notify` |
| `wait.delay` | `control` |
| `control.skip_if` | `control` |

Note: `wait.approval` carries `category: "notify"` because ARCHITECTURE.md §5.5 groups it with Communication steps (it produces a user-facing notification). `wait.delay` carries `category: "control"` because it is pure timing control with no output.

The frontend uses `config_schema` to generate the StepConfigDrawer form via `react-hook-form` + `zod` (with `@vite/json-schema-to-zod` or equivalent). Backend never accepts a `step_type` not in this catalog.

---

## 9. Webhook endpoint

### 9.1 `POST /api/webhooks/{token}`

External system fires a workflow with a `trigger.webhook` step. Token is the value stored in `workflow_webhook_tokens.token`.

**Request body:** any JSON. Stored at `run.context["webhook_payload"]` (the literal string key `"webhook_payload"`, not a numeric step index) for downstream steps to reference via `{{ context.webhook_payload.<path> }}`. See ARCHITECTURE.md §6 for the full ref namespace spec.

**Response: 202**
```json
{ "run_id": "99999999-..." }
```

**Errors:**
- `404 not_found` — token unknown.
- `409 state_conflict` — workflow not active.
- `429 rate_limited` — > 60 fires/min for the same token.

Webhook auth is the token alone; rotate by issuing a new one via the workflow editor.

---

## 10. WebSocket: live run stream

### 10.1 `WS /api/runs/{id}/stream`

Server pushes step status changes for a run. Connect with `Authorization: Bearer <token>` either in the upgrade headers (browser-friendly via `Sec-WebSocket-Protocol: bearer.<token>`) or as a `?token=` query param.

**Server → client frames** (one JSON object per frame):

Initial snapshot on connect:
```json
{ "type": "snapshot", "run": { /* Run shape (§4) */ } }
```

Step status update:
```json
{
  "type": "step_update",
  "run_id": "99999999-...",
  "step_index": 2,
  "step": {
    "step_index": 2,
    "step_type": "condition.numeric",
    "status": "succeeded",
    "started_at": "...",
    "finished_at": "...",
    "output": { "passed": true },
    "attempts": 1
  }
}
```

Run-level status update:
```json
{
  "type": "run_update",
  "run_id": "99999999-...",
  "status": "succeeded",
  "finished_at": "...",
  "halt_reason": null
}
```

Approval requested:
```json
{
  "type": "approval_requested",
  "run_id": "99999999-...",
  "approval": { /* approval object as in 7.1 */ }
}
```

Server closes with `1000` on run terminal. Idle ping every 30s (`{"type":"ping"}`); client replies `{"type":"pong"}`.

**Client → server frames:** none required. The cancel action goes through `POST /api/runs/{id}/cancel`.

**Reconnection:** if the WS drops, frontend polls `GET /api/runs/{id}` every 2s and shows a "reconnecting…" indicator until the WS comes back.

---

## 11. Frontend state model (informative)

```ts
type WorkflowSummary = Omit<Workflow, "steps">;
type Workflow = {
  id: string;
  name: string;
  description: string | null;
  status: "draft" | "active" | "paused" | "archived";
  version: number;
  single_instance: boolean;
  created_at: string;
  updated_at: string;
  activated_at: string | null;
  last_run_at: string | null;
  next_run_at: string | null;
  steps: Step[];
};

type Step = {
  id: string;
  step_index: number;
  step_type: string;       // matches catalog
  label: string | null;
  config: Record<string, unknown>;
};

// Full run detail — returned only by GET /api/runs/{id}
type Run = {
  id: string;
  workflow_id: string;
  workflow_version: number;
  triggered_by: "schedule" | "manual" | "webhook" | "price_alert" | "indicator_alert" | "event_alert";
  started_at: string;
  finished_at: string | null;
  status: "running" | "succeeded" | "failed" | "cancelled" | "awaiting_approval";
  halt_reason: "condition_not_met" | "time_budget" | null;
  error_message: string | null;
  context: Record<string, Record<string, unknown>>;
  steps: RunStep[];
};

// List-view summary — returned by GET /api/workflows/{id}/runs items
// Omits `context` and `steps`; adds `step_count` for display.
type RunSummary = Omit<Run, "context" | "steps"> & {
  step_count: number;
};

type RunStep = {
  step_index: number;
  step_type: string;
  status: "pending" | "running" | "succeeded" | "failed" | "skipped" | "awaiting_approval";
  started_at: string | null;
  finished_at: string | null;
  output: Record<string, unknown> | null;
  error_message: string | null;
  attempts: number;
};

type Approval = {
  id: string;
  run_id: string;
  step_index: number;
  summary: string;
  requested_at: string;
  expires_at: string;
  decision: "approved" | "rejected" | null;
  decided_at: string | null;
};

type StepTypeCatalog = {
  catalog_version: string;
  categories: { id: string; label: string }[];
  step_types: StepTypeDef[];
};
```

Generate these types from the OpenAPI spec produced by FastAPI at `/openapi.json` (backend lead exposes; frontend lead consumes via `openapi-typescript`).

---

## 12. Versioning & change process

- This document is the contract. Backend and frontend code against it.
- Any change requires a PR that updates this doc **before** the implementation PR lands. Reviewer blocks PRs whose impl doesn't match.
- Breaking changes during the sprint require explicit signoff from both leads.
- Post-v1, contract changes follow semver: minor for additive, major for breaking.
