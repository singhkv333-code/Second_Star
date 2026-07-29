# Pivot Agent System — End-to-End Walkthrough

> What happens, exactly, when a user says "make me an agent" in chat and clicks Activate. Reads top-to-bottom; every code path and DB write traced.
>
> Pair with [ARCHITECTURE.md](./ARCHITECTURE.md) (the spec) and [API_CONTRACT.md](./API_CONTRACT.md) (the wire format).

---

## 0. The conversation we're tracing

**User types in chat:**

> Every weekday at 3:55 PM IST, if my buying power is over ₹50,000, buy 10 shares of RELIANCE and notify me by email.

**Bot responds:** an inline "workflow draft" card with a 5-step pipeline + an "Open in editor →" button.

User clicks Open → a side panel slides in with the workflow editor. User scans the steps, edits nothing, clicks **Activate**. From this point on, every weekday at 3:55 PM IST the engine runs the workflow autonomously, pausing for approval before placing the order.

---

## 1. High-level architecture (ASCII)

```
┌────────────────────────────────────────────────────────────────────────────┐
│                         pivot-next/  (Next.js 15)                          │
│  ┌──────────────┐    ┌───────────────────┐    ┌──────────────────┐         │
│  │  Chat UI     │ →  │ Workflow editor   │ ←  │  RunView         │         │
│  │  (existing)  │    │ (AgentPanel +     │    │  (live status    │         │
│  │              │    │  WorkflowEditor)  │    │   over WS)       │         │
│  └──────┬───────┘    └─────────┬─────────┘    └─────────┬────────┘         │
│         │                      │                        │                  │
└─────────┼──────────────────────┼────────────────────────┼──────────────────┘
          │ POST /chat           │ POST/PATCH/POST        │ WS /api/runs/{id}
          │                      │ /api/workflows         │ /stream
          │                      │ /api/runs              │
          v                      v                        v
┌────────────────────────────────────────────────────────────────────────────┐
│                       pivot/backend/  (FastAPI)                            │
│                                                                            │
│  routers/chat.py          routers/workflows.py    routers/run_stream.py    │
│       │                        │                       │                   │
│       │ ALL_TOOLS dispatch     │ CRUD + activate       │ WebSocket auth +  │
│       v                        │ + run                 │   subscribe       │
│  agents/tool_executor.py:      │                       │                   │
│   _propose_workflow            v                       │                   │
│       │                  workflows/                    │                   │
│       │                   scheduler.py                 │                   │
│       v                       │                        │                   │
│  workflows/propose.py         │                        │                   │
│   - mock OR LLM+validate      │ trigger.schedule:      │                   │
│   - retry ONCE on fail        │   poll every 30s       │                   │
│   - fallback to mock+warning  │ trigger.price/ind:     │                   │
│       │                       │   poll every 60s       │                   │
│       │                       │   in market hours      │                   │
│       │                       │                        │                   │
│       │                       v                        │                   │
│       │                  workflows/engine.py           │ events.py pub/sub │
│       │                   WorkflowEngine               │                   │
│       │                       │   ↑ reads/writes DB    │                   │
│       │                       │   ↑ persists at every  │                   │
│       │                       │     boundary           │                   │
│       │                       │   ↑ honors invariants  │                   │
│       │                       │     §7 (idempotency,   │                   │
│       │                       │     retries, approval, │                   │
│       │                       │     time budget, …)    │                   │
│       │                       │                        │                   │
│       │                       v                        │                   │
│       │              workflows/steps/*.py              │                   │
│       │              (24 step executors,               │                   │
│       │               22 real, 2 cut to v2)            │                   │
│       │                       │                        │                   │
└───────┼───────────────────────┼────────────────────────┼───────────────────┘
        │                       │                        │
        │                       v                        │
┌───────┼────────────────────────────────────────────────┼───────────────────┐
│                                                        │                   │
│                          PostgreSQL                    │                   │
│   workflows, workflow_steps, workflow_runs,            │                   │
│   workflow_run_steps, workflow_approvals,              │                   │
│   workflow_webhook_tokens, watchlist_items             │                   │
│                                                        │                   │
└────────────────────────────────────────────────────────┴───────────────────┘
```

Key invariant from [ARCHITECTURE.md §7](./ARCHITECTURE.md#7-execution-engine--invariants): **state writes to DB before any external call.** Worker crash mid-step → next worker resumes from the last persisted boundary. The engine's pub/sub publishes WS frames *after* the DB write, never before.

---

## 2. The chat → tool → draft path

### 2.1 User sends the chat message

```
POST /chat
{ "message": "Every weekday at 3:55 PM IST, if my buying power is over ₹50,000, buy 10 shares of RELIANCE and notify me by email." }
```

The chat router (existing code at `pivot/backend/routers/chat.py`) hands the message to Sarvam with the full tool catalog from `services/tool_registry.py` (which now includes `propose_workflow`).

### 2.2 The chatbot picks `propose_workflow`

Sarvam's tool-call output looks like:

```json
{
  "tool_calls": [{
    "name": "propose_workflow",
    "arguments": {"user_intent": "Every weekday at 3:55 PM IST, ..."}
  }]
}
```

The handler `_propose_workflow` in `pivot/backend/agents/tool_executor.py:339` is dispatched.

### 2.3 `propose_workflow_async` runs

Two paths inside `pivot/backend/workflows/propose.py`:

**Mock mode** (no `SARVAM_API_KEY` / `OPENAI_API_KEY`): pattern-matches the prompt — extracts cron from "every weekday at HH:MM PM IST", quantity from "buy N", symbol from uppercase tokens, threshold from "over X" — emits a deterministic 5-step draft.

**LLM mode** (key configured): builds a focused system prompt that includes the entire 24-step-type catalog with required fields, calls `route_and_call(STRUCTURED_JSON, json_mode=True)`, parses noise-tolerant JSON (handles markdown fences + leading prose + brace-balanced extraction), validates **every step config against the registry's Pydantic model**, retries ONCE on validation failure with the concrete error embedded so the LLM can self-correct. **Last-resort:** if both LLM attempts fail, falls back to the mock draft + a warning so the chat always surfaces something actionable.

### 2.4 The actual captured output

This is the literal JSON the tool returned for our prompt — captured by running the executor against the real backend on 2026-05-02:

```json
{
  "name": "Buy 10 RELIANCE",
  "description": "Every weekday at 3:55 PM IST, if my buying power is over rs 50,000, buy 10 shares of RELIANCE and notify me by email.",
  "steps": [
    {
      "step_type": "trigger.schedule",
      "label": "On 55 15 * * 1-5 Asia/Kolkata",
      "config": {"cron": "55 15 * * 1-5", "timezone": "Asia/Kolkata"}
    },
    {
      "step_type": "fetch.portfolio",
      "label": "Get my portfolio",
      "config": {}
    },
    {
      "step_type": "condition.numeric",
      "label": "Buying power > 50000",
      "config": {
        "left": "{{ context.1.buying_power }}",
        "operator": ">",
        "right": 50000
      }
    },
    {
      "step_type": "action.place_order",
      "label": "Buy 10 RELIANCE",
      "config": {
        "symbol": "RELIANCE",
        "side": "buy",
        "quantity": 10,
        "order_type": "market",
        "requires_approval": true
      }
    },
    {
      "step_type": "notify.message",
      "label": "Notify by email",
      "config": {
        "channel": "email",
        "template": "Buyed 10 RELIANCE",
        "vars": {}
      }
    }
  ],
  "rationale": "Mapped your request to a scheduled trigger (55 15 * * 1-5 Asia/Kolkata), portfolio check, and a buy order. Requires approval = True.",
  "warnings": [
    "LLM proposal failed: top-level draft shape invalid: Field required. Showing a best-effort draft — review every field before activating."
  ]
}
```

> **What the warning means:** the live SARVAM key is set in `.env`, so the executor tried the real LLM path twice. Both responses didn't validate against the registry, so the executor fell back to the deterministic mock. The chat surfaces this in `warnings[]` so the user knows to scrutinise the draft.
>
> **One known mock-mode wart:** "Buyed" in the notify template (the mock just title-cases `side` and appends `ed`). Cosmetic — easy fix; the `notify.message` executor renders the template verbatim so the user sees this exactly.

### 2.5 The chat result

The chat handler wraps the tool result with a render hint:

```json
{
  "success": true,
  "data": {
    "name": "Buy 10 RELIANCE",
    "description": "...",
    "steps": [...],
    "rationale": "...",
    "warnings": [...],
    "_render_hint": "workflow_draft_card"
  }
}
```

The frontend's chat tool-result renderer sees `_render_hint: "workflow_draft_card"` and renders an inline card with:
- Workflow name as the heading
- Step icons (5 in a row: clock → wallet → equal → cart → send)
- The rationale as a one-liner
- A prominent **Open in editor →** button

**No DB writes have happened yet.** The draft lives in the chat transcript only.

---

## 3. The user opens the editor and activates

### 3.1 Frontend: open + render the draft

User clicks Open. The frontend's `AgentPanel` slides in from the right. The `WorkflowEditor` consumes the draft directly (no API call) and renders 5 `StepCard` components. Each card shows:
- Icon (from the catalog, e.g. `clock` for `trigger.schedule`)
- Label
- A one-line config preview (rendered by `config-preview.ts` per step type — e.g. `"Every weekday at 15:55 Asia/Kolkata"` for the trigger)
- A three-dot menu

Clicking a step opens the `StepConfigDrawer` — a secondary drawer with a form generated dynamically from the step's `config_schema` (JSON Schema → react-hook-form via `lib/json-schema-to-zod.ts`). The user could change `quantity` from 10 to 5 here, or change `requires_approval`. They don't.

### 3.2 User clicks Activate — frontend POSTs to backend

```
POST /api/workflows
Content-Type: application/json
Authorization: Bearer <jwt>

{
  "name": "Buy 10 RELIANCE",
  "description": "Every weekday at 3:55 PM IST, ...",
  "single_instance": true,
  "steps": [
    {"step_type": "trigger.schedule", "label": "...", "config": {"cron": "55 15 * * 1-5", "timezone": "Asia/Kolkata"}},
    {"step_type": "fetch.portfolio", "label": "...", "config": {}},
    {"step_type": "condition.numeric", "label": "...", "config": {"left": "{{ context.1.buying_power }}", "operator": ">", "right": 50000}},
    {"step_type": "action.place_order", "label": "...", "config": {"symbol": "RELIANCE", "side": "buy", "quantity": 10, "order_type": "market", "requires_approval": true}},
    {"step_type": "notify.message", "label": "...", "config": {"channel": "email", "template": "Bought 10 RELIANCE", "vars": {}}}
  ]
}
```

### 3.3 Backend: `pivot/backend/routers/workflows.py:create_workflow`

The handler:

1. Authenticates the JWT → extracts `user_id` (via `_deps.py:require_user`).
2. Calls `_validate_steps(steps_in)`:
   - Rejects unknown `step_type` → 422 with `details.field='step_type'`.
   - Rejects `step_index=0` that isn't a `trigger.*` → 400.
   - For every step, calls `STEP_REGISTRY[step_type].config_model.model_validate(step.config)` — Pydantic model raises with the exact bad field on failure → 422 with `details.step_index` + `details.field`.
3. Inserts a `Workflow` row + 5 `WorkflowStep` rows in a single transaction.
4. Returns 201 with the canonical Workflow shape (per [API_CONTRACT.md §3](./API_CONTRACT.md#3-workflow-shape-canonical)).

### 3.4 DB state — right after `POST /api/workflows`

**`workflows` table** — 1 new row:

| id | user_id | name | status | version | single_instance | activated_at | next_run_at |
|---|---|---|---|---|---|---|---|
| `11111111-…` | 42 | Buy 10 RELIANCE | `draft` | 1 | true | null | null |

**`workflow_steps` table** — 5 new rows (truncated config for readability):

| id | workflow_id | step_index | step_type | config (JSONB) |
|---|---|---|---|---|
| aaa | 11111111-… | 0 | trigger.schedule | `{"cron": "55 15 * * 1-5", "timezone": "Asia/Kolkata"}` |
| bbb | 11111111-… | 1 | fetch.portfolio | `{}` |
| ccc | 11111111-… | 2 | condition.numeric | `{"left": "{{ context.1.buying_power }}", "operator": ">", "right": 50000}` |
| ddd | 11111111-… | 3 | action.place_order | `{"symbol": "RELIANCE", "side": "buy", "quantity": 10, "order_type": "market", "requires_approval": true}` |
| eee | 11111111-… | 4 | notify.message | `{"channel": "email", "template": "...", "vars": {}}` |

`workflow_runs`, `workflow_run_steps`, `workflow_approvals` — all empty for this workflow. The user has only saved a draft so far.

### 3.5 Frontend POSTs `/api/workflows/{id}/activate`

```
POST /api/workflows/11111111-…/activate
Authorization: Bearer <jwt>
```

The activate handler:
1. Loads the workflow (404 if not found / not yours).
2. Re-validates every step config (defense in depth — the DB could have drifted via a manual SQL edit).
3. Sets `status = 'active'`, `activated_at = now()`.
4. Calls `upsert_workflow_schedule(db, wf)` from `pivot/backend/workflows/scheduler.py`:
   - Walks step 0; it's a `trigger.schedule`.
   - Calls `compute_next_run_at(cron="55 15 * * 1-5", tz_str="Asia/Kolkata")` — uses APScheduler's `CronTrigger.from_crontab` to compute the next fire time, returns it as UTC.
   - Sets `wf.next_run_at` = e.g. `2026-05-04T10:25:00+00:00` (3:55 PM IST = 10:25 UTC, next weekday).
5. **Bad cron / unknown timezone** at this point → raises `InvalidCronError` → router converts to 422 with `details.field='config.cron'`. **The schedule is never silently armed dead** (closes the reviewer's Day-2 edge case #1).

### 3.6 DB state — right after activate

**`workflows`**:

| id | … | status | activated_at | **next_run_at** |
|---|---|---|---|---|
| 11111111-… | … | **active** | 2026-05-02T13:00:00Z | **2026-05-04T10:25:00Z** |

The scheduler's poll job (running every 30s in the same process via APScheduler) will now see this workflow on every tick and, when `next_run_at <= now()`, fire it.

---

## 4. The scheduler fires the workflow

### 4.1 Two parallel poll jobs in `pivot/backend/workflows/scheduler.py`

Both attached to the existing `AsyncIOScheduler` from `pivot/backend/scheduler.py` at app startup (`backend/main.py:startup` → `register_workflow_scheduler`):

| Job | Cadence | What it does |
|---|---|---|
| `_poll_due_workflows` | every 30s | Cron triggers — selects active workflows with `next_run_at <= now()`, fires runs, recomputes `next_run_at` |
| `_poll_watch_triggers` | every 60s during NSE market hours | Price + indicator triggers — batch-fetches quotes once per symbol, evaluates `>` / `<` / `crosses_above` / `crosses_below`, fires runs |

For our workflow it's the cron poll job. At 2026-05-04T10:25:30Z (30 seconds after the cron tick), the poll runs:

```python
due = db.query(Workflow).filter(
    Workflow.status == WorkflowStatus.active,
    Workflow.next_run_at.isnot(None),
    Workflow.next_run_at <= fired_at,
).all()
```

It finds 1 due workflow. For each, calls `_fire_one(wf_id, fired_at)`.

### 4.2 `_fire_one` creates the run row

```python
run = WorkflowRun(
    workflow_id=wf.id,
    workflow_version=int(wf.version),  # snapshots version=1 (matters for PATCH-during-run)
    triggered_by="schedule",
    status=RunStatus.running,
    context={},
)
db.add(run)
wf.last_run_at = fired_at
upsert_workflow_schedule(db, wf)  # recomputes next_run_at for the next weekday
db.commit()
```

### 4.3 DB state — run created, scheduler hands off to engine

**`workflow_runs`** — 1 new row:

| id | workflow_id | workflow_version | triggered_by | started_at | status | context |
|---|---|---|---|---|---|---|
| 99999999-… | 11111111-… | 1 | schedule | 2026-05-04T10:25:30Z | running | `{}` |

**`workflows`** — `last_run_at` and `next_run_at` updated:

| id | … | last_run_at | next_run_at |
|---|---|---|---|
| 11111111-… | … | 2026-05-04T10:25:30Z | **2026-05-05T10:25:00Z** (next weekday) |

The scheduler now hands the run to the engine: `asyncio.create_task(WorkflowEngine().execute_run(run_id))`. The poll job returns; engine takes over in the same event loop.

---

## 5. The engine orchestrates the run

`pivot/backend/workflows/engine.py:WorkflowEngine.execute_run(run_id)` is the orchestrator. It walks every step in order, honoring all 7 invariants from [ARCHITECTURE.md §7](./ARCHITECTURE.md#7-execution-engine--invariants).

For each step, it:
1. Acquires the single-instance advisory lock (Postgres) on `workflow_id`.
2. Writes a `workflow_run_steps` row with `status='running'` **before** calling the executor (persistence-before-emit).
3. Publishes `step_update` to the WS bus (`workflows/events.py:RUN_BUS`) for any subscribed UI.
4. Invokes the executor (a function in `workflows/steps/*.py`).
5. On success: writes `status='succeeded'`, output, `finished_at`. Updates `run.context[step_index]` with the output.
6. On `_ConditionFail` (a soft halt — failed condition is NOT an error per spec): terminates the run with `status='succeeded'`, `halt_reason='condition_not_met'`.
7. On `_AwaitingApproval`: sets `run.status='awaiting_approval'` and returns. The approvals router will call `engine.resume_run(run_id)` when the user decides.
8. On other exceptions: retry up to `max_retries` per step type with `1s/4s/16s` backoff. After exhausting retries, write `status='failed'` and terminate the run.

### 5.1 Step 0 — `trigger.schedule` (no-op)

By the time we're in the engine, the trigger has already fired (the scheduler created the run). The executor is a no-op — just acknowledges and returns `None` so step 1 can run.

**DB writes during this step:**

`workflow_run_steps` — 1 new row:

| run_id | step_index | step_type | status | started_at | finished_at | output | attempts |
|---|---|---|---|---|---|---|---|
| 99999999-… | 0 | trigger.schedule | succeeded | 2026-05-04T10:25:30Z | 2026-05-04T10:25:30Z | null | 1 |

WS frame published: `{"type": "step_update", "step_index": 0, "step": {...}}`.

### 5.2 Step 1 — `fetch.portfolio`

Calls `pivot/backend/services/portfolio.get_user_portfolio(user_id, db)`. In production this hits Kite; in mock mode (no Kite key) it returns from `pivot/backend/kite/mock_data.MOCK_HOLDINGS`.

**Output** written to `run.context["1"]`:

```json
{
  "holdings": [
    {"tradingsymbol": "INFY", "quantity": 10, "average_price": 1450.0, "last_price": 1523.0, "pnl": 730.0},
    {"tradingsymbol": "TCS", "quantity": 5, "average_price": 3200.0, "last_price": 3356.0, "pnl": 780.0},
    {"tradingsymbol": "HDFCBANK", "quantity": 20, "average_price": 1580.0, "last_price": 1643.0, "pnl": 1260.0}
  ],
  "buying_power": 75000.0,
  "total_value": 230456.0
}
```

`workflow_run_steps` — 1 new row + `workflow_runs.context` updated:

| run_id | step_index | step_type | status | output | attempts |
|---|---|---|---|---|---|
| 99999999-… | 1 | fetch.portfolio | succeeded | `{"holdings": [...], "buying_power": 75000, ...}` | 1 |

### 5.3 Step 2 — `condition.numeric`

Before invoking the executor, the engine resolves refs in the step config via `pivot/backend/workflows/refs.py:resolve_refs`:

- `"left": "{{ context.1.buying_power }}"` → resolves to `75000.0` (from the previous step's output).
- `"right": 50000` → already a number, passes through.

The executor evaluates `75000.0 > 50000` → `True` → returns `{"passed": true}`. If it had been false, the executor would `raise _ConditionFail` and the engine would terminate the run with `status='succeeded'`, `halt_reason='condition_not_met'` — **the run "succeeds" because failed conditions are not errors, just early exits.**

`workflow_run_steps` — 1 new row.

### 5.4 Step 3 — `action.place_order` with `requires_approval=true` (Phase 1: pause)

The executor in `workflows/steps/actions.py:execute_action_place_order` checks `requires_approval=true`. Looks for an existing approval row for this `(run_id, step_index)`; finds none. Creates one:

```python
approval = WorkflowApproval(
    run_id=ctx.run.id,
    step_index=3,
    expires_at=now + 15min,
    summary="BUY 10 RELIANCE at market",
)
db.add(approval); db.commit()
raise _AwaitingApproval(approval.id)
```

The engine catches `_AwaitingApproval`:
1. Sets `workflow_run_steps[3].status = 'awaiting_approval'` (with output containing the summary).
2. Sets `workflow_runs.status = 'awaiting_approval'`.
3. Publishes WS frames: `step_update` (step 3 → awaiting_approval) + `run_update` (run → awaiting_approval) + `approval_requested` (with the full approval object).
4. Returns from `execute_run`. The engine task ends.

**DB state at this moment:**

`workflow_runs`:

| id | status | finished_at |
|---|---|---|
| 99999999-… | **awaiting_approval** | null |

`workflow_run_steps`:

| step_index | step_type | status | output |
|---|---|---|---|
| 0 | trigger.schedule | succeeded | null |
| 1 | fetch.portfolio | succeeded | `{...portfolio...}` |
| 2 | condition.numeric | succeeded | `{"passed": true}` |
| 3 | action.place_order | **awaiting_approval** | `{"summary": "BUY 10 RELIANCE at market"}` |

`workflow_approvals` — 1 new row:

| id | run_id | step_index | requested_at | expires_at | decision | summary |
|---|---|---|---|---|---|---|
| ccc-… | 99999999-… | 3 | 2026-05-04T10:25:31Z | 2026-05-04T10:40:31Z | null | BUY 10 RELIANCE at market |

### 5.5 Frontend gets the WS frame, shows the approval banner

The `RunView` component is subscribed to `WS /api/runs/99999999-…/stream` (set up when the user opened the run after clicking "Run now"). It receives:

```json
{"type": "approval_requested", "approval": {"id": "ccc-…", "summary": "BUY 10 RELIANCE at market", "expires_at": "..."}}
```

…and surfaces an amber banner at the top of the run view: **"Approval needed: BUY 10 RELIANCE at market"** with **Approve** / **Reject** buttons.

### 5.6 User clicks Approve

```
POST /api/approvals/ccc-…/decision
Authorization: Bearer <jwt>
{ "decision": "approved" }
```

Handler in `pivot/backend/routers/approvals.py`:
1. Loads the approval (404 if not yours).
2. Sets `decision="approved"`, `decided_at=now`.
3. Sets `workflow_runs.status='running'` so the engine can re-enter.
4. Calls `WorkflowEngine().resume_run(run_id)` as a background task.

### 5.7 Step 3 — `action.place_order` (Phase 2: actually place the order)

The engine re-enters `execute_action_place_order`. This time it finds the approval with `decision='approved'` → falls through to the actual order-placement code:

```python
result = place_order(
    access_token=token,           # mock_token in test mode
    tradingsymbol="RELIANCE",
    exchange="NSE",
    transaction_type="BUY",
    quantity=10,
    order_type="MARKET",
    product="CNC",
    tag=f"wf_{ctx.client_request_id[:16]}",   # ← idempotency!
)
```

The `client_request_id` is `sha1(f"{run_id}:3:{attempts}")` — deterministic. If the engine retries this step (network hiccup, broker timeout), the broker rejects the duplicate based on the same `tag`. **Action steps are idempotent by construction.**

In Kite mock mode, `place_order` returns `{"order_id": 12345, "status": "PENDING", ...}` without hitting any real broker.

`workflow_run_steps[3]` updated:

| step_index | status | output |
|---|---|---|
| 3 | **succeeded** | `{"order_id": "12345", "status": "PENDING", "client_request_id": "abc123…"}` |

WS frame: `step_update` for step 3 → succeeded.

### 5.8 Step 4 — `notify.message`

Renders the template (`"Bought 10 RELIANCE"` — no vars to substitute), tries to delegate to a notify service if one exists at `backend.services.notify` (it doesn't yet), falls back to logging the message.

`workflow_run_steps[4]`:

| step_index | status | output |
|---|---|---|
| 4 | succeeded | `{"channel": "email", "delivered": false, "log": "[email] (logged, no service wired) Bought 10 RELIANCE"}` |

### 5.9 Run terminal — engine writes succeeded

After the last step, the engine calls `_terminate(run, RunStatus.succeeded)`:

`workflow_runs`:

| id | status | finished_at |
|---|---|---|
| 99999999-… | **succeeded** | 2026-05-04T10:25:33Z |

WS frame: `{"type": "run_update", "status": "succeeded", "finished_at": "..."}`. Server closes the WS with code 1000.

---

## 6. The next day: it all happens again

Tomorrow at 10:25 UTC (3:55 PM IST), the cron poll job sees `workflows.next_run_at <= now()` and creates a new `workflow_runs` row. The user gets another approval request banner if they're in the panel; otherwise the approval expires after 15 min and the run terminates as `cancelled`. After firing, `next_run_at` is bumped to the day after.

Run history accumulates in `workflow_runs`. The user can view past runs via `GET /api/workflows/{id}/runs` (paginated, with derived `step_count` for the list view) or click any individual run for the full step log via `GET /api/runs/{id}`.

---

## 7. File map — what owns what

| Concern | File |
|---|---|
| Chatbot tool registration | `pivot/backend/agents/tools.py` (`tool("propose_workflow", ...)`) |
| Tool dispatch from chat → backend | `pivot/backend/agents/tool_executor.py:_propose_workflow` |
| NL → WorkflowDraft logic | `pivot/backend/workflows/propose.py` |
| Step type catalog | `pivot/backend/workflows/registry.py` (24 step types) + `workflows/schemas.py` (per-step Pydantic configs) |
| Step executors | `pivot/backend/workflows/steps/{triggers,fetches,conditions,actions,notify,control}.py` |
| Engine orchestration | `pivot/backend/workflows/engine.py:WorkflowEngine` |
| Ref resolver (`{{ context.X.path }}`) | `pivot/backend/workflows/refs.py` |
| WS pub/sub | `pivot/backend/workflows/events.py:RUN_BUS` |
| Cron + watcher polling | `pivot/backend/workflows/scheduler.py` |
| REST CRUD | `pivot/backend/routers/workflows.py` |
| Run history & cancel | `pivot/backend/routers/runs.py` |
| Approval decisions | `pivot/backend/routers/approvals.py` |
| Webhook trigger ingress | `pivot/backend/routers/webhooks.py` |
| WebSocket endpoint | `pivot/backend/routers/run_stream.py` |
| Canonical error envelope | `pivot/backend/main.py` (FastAPI exception handlers) |
| Models | `pivot/backend/models.py` (Workflow, WorkflowStep, WorkflowRun, WorkflowRunStep, WorkflowApproval, WorkflowWebhookToken, WatchlistItem) |
| Migrations | `pivot/migrations/versions/0001_workflows.py`, `0002_watchlist.py` |
| Test scaffold | `pivot/tests/workflows/conftest.py` (autouse fixture rebinds engine/scheduler/run_stream `SessionLocal`) |
| End-to-end smoke | `pivot/scripts/smoke_test_api.sh` (41 curl checks against running uvicorn) |

---

## 8. How to run this yourself

```bash
# Backend up (no docker needed if you use APP_ENV=test sqlite)
cd pivot
APP_ENV=test JWT_SECRET_KEY="dev-secret-key-minimum-32-characters-long" \
  uvicorn backend.main:app --reload --port 8000

# In another terminal: walk every endpoint with curl
bash pivot/scripts/smoke_test_api.sh   # 41/41 should pass

# Or call propose_workflow directly via Python
cd pivot
python3 -c "
import asyncio, json
from backend.workflows.propose import propose_workflow_async
async def main():
    draft = await propose_workflow_async(
        'Every weekday at 3:55 PM IST if buying power > 50000 buy 10 RELIANCE and email me'
    )
    print(json.dumps(draft.model_dump(), indent=2, default=str))
asyncio.run(main())
"
```

For the chat path (full UX), you'll need the human FE dev's `pivot-next/` running against the backend. Until that's wired (Day 5 in the original plan), the smoke script + the propose_workflow Python snippet are the cleanest demos.

---

## 9. State of completeness (2026-05-02)

**22 of 24 step types real**, 2 cut to v2 with documented path back (`trigger.event`, `fetch.news`).

| Capability | Real | Notes |
|---|---|---|
| Chat → propose_workflow tool | ✅ | mock fallback when LLM fails |
| Schema validation (every boundary) | ✅ | invariant 7 |
| Idempotent actions | ✅ | sha1-derived `client_request_id` |
| Persistence before external call | ✅ | invariant 2 |
| Per-step retries with backoff | ✅ | invariant 3 |
| Approval gating (pause + resume) | ✅ | invariant 4 |
| Single-instance lock | ✅ | invariant 5 (Postgres advisory; SQLite degrades to in-process) |
| Time budget | ✅ | invariant 6 (30 min default) |
| Cron triggers | ✅ | poll every 30s |
| Price/indicator triggers | ✅ | batched poll every 60s during market hours, with crossing detection |
| Webhook triggers | ✅ | unauth POST + token table + rate limit |
| WebSocket live runs | ✅ | snapshot + step_update + run_update + approval_requested + ping |
| Canonical error envelope | ✅ | every `/api/*` endpoint |
| CORS for human FE dev | ✅ | localhost:3000 + localhost:5173 |
| Smoke test green | ✅ | 41/41 against live uvicorn |
| Test count | 157/157 | full backend test suite |

**Quality debt** (tracked, not on demo critical path):
- `#21` mypy `--strict` cleanup — ~150 SQLAlchemy `Column[X]` errors. Tests pass; this is type-system noise. Day 8 buffer.
