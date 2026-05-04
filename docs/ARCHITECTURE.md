# Pivot Agent System (Workflows v1) — Architecture

> Single source of truth for the Agent System sprint. Both backend and frontend code against this doc. Pair with [API_CONTRACT.md](./API_CONTRACT.md) for endpoint shapes.

**Sprint constraint:** Speedrun application due 2026-05-17. Demo path must work end-to-end. No half-built features in the demo flow.

---

## 1. What we're building

User-facing concept: **Agents.** The user describes a strategy in chat ("buy ₹5K of QQQ at 3:55 PM every weekday, sell at 9:35 AM the next day"), the chatbot proposes a structured **Workflow**, the workflow appears as an editable visual sequence in a right-side panel, the user reviews/edits/activates, and it runs autonomously.

Internal terms:
- **Workflow** — ordered linear list of steps (no branching, no loops, no sub-workflows)
- **Step** — one typed node (trigger, fetch, condition, action, notify, control-flow)
- **Run** — one execution attempt of a workflow, persisted for audit/history

Entry point is **inside the chat**. No standalone `/agents` route in v1 — the chat IS the front door. The chatbot proposes; the user approves via the editor; the engine runs.

---

## 2. Repo layout (adapted from spec to actual repo)

The original spec assumed `src/...`. This repo uses `pivot/backend/...`. Path conventions for this sprint:

**Backend (Python, FastAPI, sync SQLAlchemy 2):**
```
pivot/
├── backend/
│   ├── workflows/              # NEW — engine + step executors
│   │   ├── __init__.py
│   │   ├── engine.py           # WorkflowEngine.execute_run()
│   │   ├── registry.py         # @register_step decorator + step catalog
│   │   ├── refs.py             # Mustache-style {{context.X.path}} resolver
│   │   ├── schemas.py          # per-step config JSON schemas (Pydantic)
│   │   ├── scheduler.py        # extends backend/scheduler.py for triggers
│   │   ├── watcher.py          # price/indicator polling subprocess
│   │   └── steps/              # one module per step type group
│   │       ├── triggers.py
│   │       ├── fetches.py
│   │       ├── conditions.py
│   │       ├── actions.py
│   │       ├── notify.py
│   │       └── control.py
│   ├── routers/
│   │   ├── workflows.py        # NEW — /api/workflows*
│   │   ├── runs.py             # NEW — /api/runs*
│   │   ├── approvals.py        # NEW — /api/approvals*
│   │   ├── webhooks.py         # NEW — /api/webhooks/{token}
│   │   └── run_stream.py       # NEW — WS /api/runs/{id}/stream
│   ├── agents/
│   │   └── tools/
│   │       └── propose_workflow.py   # NEW — chatbot tool
│   ├── models.py               # add Workflow, WorkflowStep, WorkflowRun, …
│   └── schemas.py              # add Pydantic request/response models
├── migrations/versions/
│   └── 0001_workflows.py       # NEW — initial migration for the agent system
└── tests/
    └── workflows/              # NEW — unit + integration tests
```

**Frontend (Next.js 15 at `pivot-next/`):**
```
pivot-next/
├── package.json                # Next.js 15 + shadcn + Tailwind + dnd-kit + …
├── components.json             # shadcn config
├── tailwind.config.ts
├── app/
│   ├── layout.tsx
│   ├── page.tsx                # chat shell (mirrors the legacy Vite app)
│   └── api/                    # proxy if needed; backend stays at pivot/
├── components/
│   ├── chat/                   # chat UI ported from the Vite frontend
│   └── agent-panel/            # all Agent System UI lives here
│       ├── AgentPanel.tsx
│       ├── WorkflowEditor.tsx
│       ├── StepConfigDrawer.tsx
│       ├── StepTypePicker.tsx
│       ├── RunView.tsx
│       └── RunHistory.tsx
├── lib/
│   ├── api.ts                  # typed client for the backend
│   ├── ws.ts                   # WebSocket client for run streams
│   └── step-types.ts           # types generated from /api/step-types
└── tests/                      # vitest + react-testing-library
```

**Why two frontends in v1:** the legacy Vite `frontend/` keeps the existing chat working while we build the Next.js Agent System. v2 will collapse them.

---

## 3. Stack decisions

| Layer | Choice | Note |
|---|---|---|
| Backend lang | Python 3.11+ | matches repo |
| Web | FastAPI | matches repo |
| ORM | SQLAlchemy 2.0 + psycopg2 (sync) | matches repo. NOT asyncpg. Async only for FastAPI handlers and the run worker (`asyncio.run_in_executor` for sync DB calls). |
| Validation | Pydantic v2 | matches repo |
| Migrations | Alembic | matches repo; first version is `0001_workflows.py` |
| Scheduler | APScheduler `AsyncIOScheduler` | extend existing `backend/scheduler.py`, don't add a parallel scheduler |
| LLM (propose_workflow) | Sarvam (existing chatbot pipeline) | reuse `backend/agents/...` plumbing |
| Frontend | Next.js 15 (app router) | new dir `pivot-next/` |
| UI | shadcn/ui + Tailwind | pinned versions in `package.json` |
| Forms | react-hook-form + zod | forms generated from JSON schemas |
| DnD | @dnd-kit/sortable | NOT React Flow / xyflow — linear list |
| Icons | lucide-react | |
| Time | date-fns | |
| Tests | pytest (backend), vitest + RTL (frontend) | |
| Lint/typecheck | ruff + mypy --strict (backend), ESLint + tsc strict (frontend) | |

---

## 4. Data model

```sql
-- One row per Agent (Workflow) the user has saved.
CREATE TABLE workflows (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         INTEGER NOT NULL REFERENCES users(id),  -- users.id is an INTEGER PK in this repo
    name            TEXT NOT NULL,
    description     TEXT,
    status          workflow_status NOT NULL DEFAULT 'draft',
    -- 'draft' | 'active' | 'paused' | 'archived'
    created_at      TIMESTAMPTZ DEFAULT now(),
    updated_at      TIMESTAMPTZ DEFAULT now(),
    activated_at    TIMESTAMPTZ,
    last_run_at     TIMESTAMPTZ,
    next_run_at     TIMESTAMPTZ,                   -- pre-computed for scheduled triggers
    version         INT NOT NULL DEFAULT 1,        -- bumped on every edit; runs reference version
    single_instance BOOLEAN NOT NULL DEFAULT TRUE  -- advisory-lock concurrent runs
);

CREATE TABLE workflow_steps (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workflow_id     UUID NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,
    step_index      INT NOT NULL,                  -- 0-based ordering
    step_type       TEXT NOT NULL,                 -- 'trigger.schedule', 'action.place_order', …
    config          JSONB NOT NULL,                -- type-specific, validated against schema
    label           TEXT,                          -- user-editable display name
    UNIQUE (workflow_id, step_index)
);

CREATE TABLE workflow_runs (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workflow_id       UUID NOT NULL REFERENCES workflows(id),
    workflow_version  INT NOT NULL,                -- snapshot of which version ran
    triggered_by      TEXT NOT NULL,               -- 'schedule' | 'manual' | 'webhook' | 'price_alert' | 'indicator_alert' | 'event_alert'
    started_at        TIMESTAMPTZ DEFAULT now(),
    finished_at       TIMESTAMPTZ,
    status            run_status NOT NULL DEFAULT 'running',
    -- 'running' | 'succeeded' | 'failed' | 'cancelled' | 'awaiting_approval'
    halt_reason       TEXT,                        -- 'condition_not_met' | 'time_budget' | NULL
    context           JSONB DEFAULT '{}',          -- inter-step shared bag, keyed by step_index
    error_message     TEXT
);
CREATE INDEX ON workflow_runs (workflow_id, started_at DESC);

CREATE TABLE workflow_run_steps (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id          UUID NOT NULL REFERENCES workflow_runs(id) ON DELETE CASCADE,
    step_index      INT NOT NULL,
    step_type       TEXT NOT NULL,
    status          step_status NOT NULL,
    -- 'pending' | 'running' | 'succeeded' | 'failed' | 'skipped' | 'awaiting_approval'
    started_at      TIMESTAMPTZ,
    finished_at     TIMESTAMPTZ,
    output          JSONB,
    error_message   TEXT,
    attempts        INT DEFAULT 1
);
CREATE INDEX ON workflow_run_steps (run_id, step_index);

CREATE TABLE workflow_approvals (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id          UUID NOT NULL REFERENCES workflow_runs(id),
    step_index      INT NOT NULL,
    requested_at    TIMESTAMPTZ DEFAULT now(),
    expires_at      TIMESTAMPTZ NOT NULL,
    decision        TEXT,                          -- 'approved' | 'rejected' | NULL (pending)
    decided_at      TIMESTAMPTZ,
    summary         TEXT NOT NULL                  -- human-readable: "Buy 10 QQQ at market"
);

-- Webhook tokens stored separately so they're not in workflow_steps.config JSON.
CREATE TABLE workflow_webhook_tokens (
    token           TEXT PRIMARY KEY,              -- random url-safe string
    workflow_id     UUID NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,
    step_index      INT NOT NULL,
    created_at      TIMESTAMPTZ DEFAULT now()
);
```

`workflow_status`, `run_status`, `step_status` are Postgres enums. Pydantic mirrors them as `Literal[...]` types.

---

## 5. Step type catalog (v1)

Each step type has: a `step_type` string, a JSON schema for `config`, an executor function, UI metadata (icon, category, label hints). Backend exposes the full catalog at `GET /api/step-types`; frontend renders config forms dynamically — **never hardcode step configs in the frontend.**

### 5.1 Triggers (always `step_index = 0`)

| `step_type` | Config | Behavior |
|---|---|---|
| `trigger.schedule` | `{ cron: string, timezone: string }` | Fires on cron. `next_run_at` pre-computed at activation and after each fire. |
| `trigger.price` | `{ symbol, operator: '>'\|'<'\|'crosses_above'\|'crosses_below', value, exchange }` | Watcher polls every 1 min during market hours. Fires when condition holds. |
| `trigger.indicator` | `{ symbol, indicator: 'rsi'\|'sma'\|'ema'\|'macd', period, operator, value }` | Watcher computes indicator from quote history. Fires on threshold cross. |
| `trigger.event` | `{ event_type: 'rbi_rate_decision'\|'company_results'\|'fii_flow', filter: object }` | Fires when a matching event is detected (delegates to existing event sources). |
| `trigger.manual` | `{}` | No automation. Only runs when user clicks "Run now". |
| `trigger.webhook` | `{}` (token in `workflow_webhook_tokens`) | External system POSTs to `/api/webhooks/{token}` to fire. |

### 5.2 Data fetches (read-only, populate context)

| `step_type` | Config | Output (in `run.context[step_index]`) |
|---|---|---|
| `fetch.quote` | `{ symbol, exchange }` | `{ ltp, open, high, low, close, volume, asof }` |
| `fetch.indicator` | `{ symbol, indicator, period }` | `{ value, computed_at }` |
| `fetch.fundamental` | `{ symbol, metric: 'pe'\|'roe'\|'mcap'\|'de' }` | `{ value, period_end, source }` |
| `fetch.portfolio` | `{}` | `{ holdings: [...], buying_power, total_value }` |
| `fetch.news` | `{ symbol_or_query, limit }` | `{ articles: [...], avg_sentiment }` |

Where the underlying data source isn't ready (e.g. `fetch.fundamental` waits on the moneycontrol DB), the executor raises `NotYetAvailableError` with a clear message. **Never fake data.**

### 5.3 Conditions (gate continuation; no branching — fail closed)

| `step_type` | Config | Behavior |
|---|---|---|
| `condition.numeric` | `{ left: ref_or_number, operator: '=='\|'!='\|'>'\|'<'\|'>='\|'<=', right: ref_or_number }` | Pass → continue. Fail → run halts with `status='succeeded'`, `halt_reason='condition_not_met'`. |
| `condition.market_status` | `{ require: 'open'\|'closed'\|'pre'\|'post' }` | Same pattern. |
| `condition.position` | `{ symbol, require: 'held'\|'not_held' }` | Same pattern. |
| `condition.time_window` | `{ start_time, end_time, timezone }` | Same pattern. |

A failed condition is **not an error** — the run completes successfully but explains why downstream steps were skipped.

### 5.4 Actions (mutate state — must be idempotent)

| `step_type` | Config | Behavior |
|---|---|---|
| `action.place_order` | `{ symbol, side: 'buy'\|'sell', quantity, order_type: 'market'\|'limit', limit_price?, requires_approval: bool }` | Calls broker (Kite). If `requires_approval`, run pauses for user confirmation before submission. |
| `action.cancel_orders` | `{ symbol_filter?, side_filter? }` | Cancels matching pending orders. |
| `action.set_stoploss` | `{ symbol, trigger_price, quantity? }` | Sets stop-loss order. |
| `action.update_watchlist` | `{ action: 'add'\|'remove', symbol }` | Mutates user's watchlist. |

Each action generates a deterministic `client_request_id = sha1(f"{run_id}:{step_index}:{attempts}")`. Broker rejects duplicates so retries are safe.

### 5.5 Communication

| `step_type` | Config | Behavior |
|---|---|---|
| `notify.message` | `{ channel: 'email'\|'sms'\|'push', template, vars }` | Renders template, sends via existing channel. |
| `notify.log` | `{ message }` | Appends to `workflow_run_steps.output.log`. No external side effect. |
| `wait.approval` | `{ summary, expires_in_minutes }` | Pauses run, creates `workflow_approvals` row, resumes on user decision. |

### 5.6 Control flow (single-track)

| `step_type` | Config | Behavior |
|---|---|---|
| `wait.delay` | `{ duration_seconds }` OR `{ until_time, timezone }` | Sleeps. Run state persists across worker restarts. |
| `control.skip_if` | `{ condition: numeric/market/position config }` | If condition holds, marks the **next** step as `skipped`. Does not branch. |

---

## 6. Inter-step data passing

Every step writes its output to `run.context[step_index]` (JSONB). Later steps reference values via Mustache-style refs, e.g. `{{ context.1.buying_power }}`.

```json
{
  "step_type": "condition.numeric",
  "config": {
    "left": "{{ context.1.buying_power }}",
    "operator": ">",
    "right": 50000
  }
}
```

`backend/workflows/refs.py` resolves refs before executing each step. If a referenced path doesn't exist, the step **fails** with a clear error like `"Reference {{context.1.buying_power}} not found — step 1 (fetch.portfolio) did not produce 'buying_power'"`.

Allowed namespaces in refs:
- `context.<step_index>.<dotted.path>` — outputs of prior steps
- `context.webhook_payload.<dotted.path>` — body of the inbound webhook (only meaningful in workflows with `trigger.webhook`; the raw payload is stored at `run.context["webhook_payload"]` using the literal key `"webhook_payload"` rather than a numeric step index)
- `now` — current ISO timestamp at step execution
- `workflow.<field>` — workflow metadata (id, name, version)

`webhook_payload` is a reserved key in the `context` bag. It is NOT a sibling namespace — refs always begin with `context.`. The webhook executor is responsible for writing the raw payload to `run.context["webhook_payload"]` before the next step runs.

No arithmetic in refs. If you need computation, use `condition.numeric`.

---

## 7. Execution engine — invariants

These are non-negotiable. Every executor honors them.

1. **Idempotency.** Action steps generate `client_request_id = sha1(f"{run_id}:{step_index}:{attempts}")`. Downstream broker/notification systems must reject duplicates with the same id.
2. **Persistence at every boundary.** State writes to DB **before** any external call. If the worker crashes mid-step, the next worker reconstructs the run from DB and resumes from the last persisted step boundary.
3. **Per-step retries with backoff.** Each step type declares `max_retries`:
   - Fetches: 3
   - Actions: 1 (idempotent retry only on transient errors)
   - Notify: 2
   - Triggers: 0
   - Conditions, control: 0
   Backoff: 1s, 4s, 16s.
4. **Approval gating.** When a step is `wait.approval` or has `requires_approval=true`, run status flips to `awaiting_approval`. No further steps execute until the approval row is resolved. On approval, run resumes from the gated step. On rejection, run terminates with `cancelled`.
5. **Run isolation.** Two concurrent runs of the same workflow get separate `context`. If `workflows.single_instance=true` (default), the engine acquires a Postgres advisory lock keyed on `workflow_id` before starting; if held, the second run terminates immediately with `cancelled` and `error_message='single-instance lock held by run <id>'`.
6. **Time budget.** Default 30 min wall clock per run; configurable per workflow (column added v1.1 if needed). Beyond that, the run terminates with `failed` and `halt_reason='time_budget'`.
7. **Schema validation at every boundary.** Step `config` is validated against its JSON schema:
   - on `POST /api/workflows` (create)
   - on `PATCH /api/workflows/{id}` (update)
   - on `POST /api/workflows/{id}/activate` (re-validate before arming triggers)
   - on engine load before executing a step (defense in depth)

---

## 8. Scheduler & watchers

Extends `backend/scheduler.py` (existing `AsyncIOScheduler`).

**Cron poll job (every 30s):**
- Selects `workflows` where `status='active'` AND `next_run_at <= now()`
- For each: inserts a `workflow_runs` row with `triggered_by='schedule'`, enqueues to the worker, recomputes `next_run_at` from the cron expression.

**Price/indicator watcher (in-process subprocess via APScheduler interval job, every 60s during market hours):**
- Aggregates all `trigger.price` and `trigger.indicator` configs from active workflows into a single batch.
- Fetches quotes once per symbol per tick (no redundant calls).
- Evaluates each trigger against the latest price/indicator; for `crosses_*`, persists last-tick price in `workflow_steps.config.last_price` to detect crossings.
- On match: inserts `workflow_runs` with `triggered_by='price_alert'` / `'indicator_alert'`, enqueues.

**Event watcher (every 5 min):**
- Polls existing event sources (events scraper, results, RBI feed) and matches against active `trigger.event` configs.

**Worker:**
- Single-process asyncio worker for v1. Pulls from an in-process asyncio queue. Executes runs serially within the process; concurrency comes from `asyncio` (multiple awaiting runs share the loop).
- Move to Celery / Temporal in v2 only if needed.

**Crash recovery on startup:**
- On app boot, the worker scans `workflow_runs` where `status='running'` and either resumes them (last persisted step boundary) or marks them `failed` with `error_message='worker crash'` if heartbeat is stale (> 5 min).

---

## 9. API surface (REST + WebSocket)

Full request/response shapes in [API_CONTRACT.md](./API_CONTRACT.md).

```
POST   /api/workflows                          # create from chatbot proposal or manual
GET    /api/workflows                          # list user's workflows
GET    /api/workflows/{id}                     # get workflow + steps
PATCH  /api/workflows/{id}                     # update name/description/steps (bumps version)
POST   /api/workflows/{id}/activate            # status: draft|paused → active
POST   /api/workflows/{id}/pause               # status: active → paused
POST   /api/workflows/{id}/archive             # status: any → archived
POST   /api/workflows/{id}/run                 # manual trigger (creates a run)

GET    /api/workflows/{id}/runs                # paginated run history
GET    /api/runs/{id}                          # full run detail with all step logs
POST   /api/runs/{id}/cancel                   # cancel an in-flight run

GET    /api/runs/{id}/approvals/pending        # approvals awaiting user decision
POST   /api/approvals/{id}/decision            # body: { decision: 'approved'|'rejected' }

GET    /api/step-types                         # catalog: types, schemas, UI metadata
                                               # frontend uses this to render config forms

POST   /api/webhooks/{token}                   # external trigger endpoint

WS     /api/runs/{id}/stream                   # server pushes step status changes
```

Authn: existing JWT bearer (matches `pivot/backend/auth/`). All `/api/workflows*`, `/api/runs*`, `/api/approvals*` are user-scoped — every query filters by `user_id`. Webhooks are unauthenticated but require a valid `token`.

---

## 10. Chatbot integration: `propose_workflow`

When the chatbot's tool router decides the user is asking to create/edit an Agent, it calls a new tool registered in `backend/agents/tools.py` (using the existing `tool(name, description, properties, required)` pattern):

```python
# backend/agents/tools/propose_workflow.py
async def propose_workflow(user_intent: str, conversation_id: str) -> WorkflowDraft:
    """
    Translates a natural-language strategy description into a structured
    workflow draft. Returns the draft for the user to review in the editor panel.
    Does NOT persist anything to the workflows table.
    """
```

Implementation:
1. Build a focused system prompt that includes the **full step-type catalog** (types + JSON schemas + 1-line examples).
2. Call Sarvam (existing pipeline) with constrained JSON output.
3. Validate the response against every step's JSON schema. If validation fails, retry once with the validation error in the prompt; then surface a clear error in chat.
4. Return `WorkflowDraft` to the chat handler.
5. Chat UI renders an inline card with "Open in editor →"; clicking opens the panel pre-filled with the draft. Until the user clicks Activate, nothing is persisted.

**Constraint:** the LLM must NOT invent step types not in the catalog. Constrain via the prompt and validate output strictly. Reject any `step_type` not in the catalog.

**Tool subset:** add a new `WORKFLOW_PROPOSE` subset in `backend/agents/tools.py` containing only `propose_workflow`. Intent classifier routes strategy-creation intents here.

---

## 11. Frontend behavior (high-level)

Detailed component spec is in [API_CONTRACT.md §11](./API_CONTRACT.md#frontend-state-model) for state shape; design specifics live with the frontend lead. Key invariants:

- All state is server-side. Optimistic updates allowed only with explicit rollback on failure. **No localStorage state.**
- Step config forms are generated from the JSON schema returned by `/api/step-types`. **No hardcoded step configs in the frontend.** Adding a new step type is a backend-only change.
- The right-side panel uses a custom resizable drawer (shadcn `Sheet` was evaluated and rejected for persistent panels).
- Live run view subscribes to `WS /api/runs/{id}/stream`. On disconnect, it falls back to polling `GET /api/runs/{id}` every 2s and surfaces a "reconnecting…" indicator.
- Empty/loading/error states ship with every component. shadcn `Skeleton` for loading, custom muted illustrations for empty.
- Keyboard support: Cmd+Enter saves, Esc closes panel, ↑/↓ navigates steps.
- Desktop only (13" laptop minimum). Mobile is explicitly not in scope.
- Design language: Public.com clean monochrome + one accent. Generous whitespace. One primary CTA per screen. Status colors consistent across the app.

---

## 12. Testing strategy

**Backend (pytest):**
- Unit test every step executor with mocked external calls.
- Schema validation test for every step type (valid + invalid configs).
- Integration test: end-to-end run of a 5-step workflow against a test DB, hitting all phases (trigger → fetch → condition pass → action → notify).
- Idempotency test: kill worker mid-action, restart, verify no duplicate broker calls.
- Approval gating test: run → pause at approval → approve → resume to completion; same with reject.
- Single-instance lock test: two concurrent runs of the same workflow; second is cancelled.
- Time budget test: workflow that exceeds 30 min terminates with `failed`.
- Crash recovery test: kill worker mid-run, restart, run resumes or is marked failed.

**Frontend (vitest + RTL):**
- Render WorkflowEditor with mock data; verify every step type renders.
- StepConfigDrawer: render form from schema, validate submit/error states.
- RunView: WS update flow + reconnection fallback.
- At least one component test per file.

**Reviewer-owned integration suite:**
- Daily demo path walkthrough starting Day 4.
- API contract diff: any backend response not matching `API_CONTRACT.md` blocks the PR.

---

## 13. What we explicitly do NOT build (v1)

- ❌ Branching / if-else trees (graph editor)
- ❌ Loops / for-each
- ❌ Sub-workflows / workflow composition
- ❌ Public template marketplace
- ❌ Custom code blocks
- ❌ Mobile responsive editor
- ❌ Multi-user collaboration on the same workflow
- ❌ Workflow versioning UI (DB column exists; UI shows current only)
- ❌ Live backtest integration in the editor (link out to the existing backtester only)

If anything from this list is proposed mid-sprint, log it to `BACKLOG.md` and block.

---

## 14. Acceptance criteria (demo path)

The naive reviewer can:

1. Open the chat
2. Type: "Every weekday at 3:55 PM IST, if my buying power is over ₹50,000, buy 10 shares of RELIANCE and notify me by email."
3. See the chatbot propose a workflow
4. See the panel open with 5 steps: schedule → fetch portfolio → numeric condition → place order (with approval) → notification
5. Edit the order quantity from 10 to 5 inline
6. Click Activate
7. Click Run now
8. See live execution: each step lights up in sequence
9. See an approval banner when the order step requires confirmation
10. Click Approve
11. See the run complete
12. Open run history; see this run logged with all step outputs
13. Pause the agent
14. Re-open the agent later and edit it without losing config

Plus: backend tests pass, frontend tests pass, lint clean, no `TODO`/`FIXME`/`XXX` without an issue number, README updated, 90-second demo video at `docs/demo.mp4`.

---

## 15. Build sequence

| Day | Backend | Frontend | Reviewer |
|---|---|---|---|
| 0 | — | — | architecture + API contract docs (this doc + API_CONTRACT.md) |
| 1 | schema migration, Pydantic models, `/api/step-types` | scaffold `pivot-next/`, shadcn setup, panel shell, mock data renderer | lock API contract |
| 2-4 | engine, all executors, REST endpoints, WebSocket | editor, step config drawer, picker, mock run view | integration tests, edge case backlog |
| 5 | — | wire to real backend; demo path manually | bug bash |
| 6 | `propose_workflow` tool | chat → panel draft load flow | validate with 10 NL prompts |
| 7 | polish (empty/loading/error, keyboard, micro-interactions) | same | demo recording rehearsal |
| 8 | buffer (no new features) | same | same |
| 9 | demo lock, final video, README, application materials | same | same |

**Cut order if Day 6 is at risk:** `trigger.webhook` → `fetch.news` → `trigger.indicator` → `trigger.event`. Ship `trigger.schedule` + `trigger.price` + `trigger.manual` polished rather than all six rough.

---

## 16. Glossary

| Term | Meaning |
|---|---|
| Agent | User-facing label for a Workflow |
| Workflow | Linear ordered list of typed steps |
| Step | One typed node (trigger/fetch/condition/action/notify/control) |
| Run | One execution attempt of a workflow |
| Context | Run-scoped JSONB bag indexed by `step_index`, holding step outputs |
| Catalog | Server-known set of step types + their JSON schemas |
| Halt reason | Why a run ended without executing every step (e.g. `condition_not_met`) |
| Single-instance | Workflow flag preventing concurrent runs of itself |
