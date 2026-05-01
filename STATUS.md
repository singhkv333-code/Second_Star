# STATUS — Pivot Agent System Sprint

> Daily reviewer-owned status report. Read top-to-bottom: most recent day first. Lead reads this to plan the next session.

---

## Demo path readiness — 2026-05-02

Counted against the 14 demo steps in ARCHITECTURE.md §14:

- [ ] 1. Open the chat
- [ ] 2. Type: "Every weekday at 3:55 PM IST, if my buying power is over ₹50,000, buy 10 shares of RELIANCE and notify me by email."
- [ ] 3. See the chatbot propose a workflow
- [ ] 4. See the panel open with 5 steps: schedule → fetch portfolio → numeric condition → place order (with approval) → notification
- [ ] 5. Edit the order quantity from 10 to 5 inline
- [ ] 6. Click Activate
- [ ] 7. Click Run now
- [ ] 8. See live execution: each step lights up in sequence
- [ ] 9. See an approval banner when the order step requires confirmation
- [ ] 10. Click Approve
- [ ] 11. See the run complete
- [ ] 12. Open run history; see this run logged with all step outputs
- [ ] 13. Pause the agent
- [ ] 14. Re-open the agent later and edit it without losing config

Score: 0 / 14

---

## Day 2 — 2026-05-02 (reviewer)

BE / FE Day 2 work in flight; entries to be appended on completion.

### Shipped
- Task #20: Full Day 1 commit audit across all six commits (243c88d, 3d3ca14, e9295ab, e051a6f, 8ed728b, 37da922).
- Type safety check: mypy --strict on all new backend workflow modules (registry, schemas, refs, steps/*, routers/workflows.py, backend/schemas.py) — PASSES (errors are in pre-existing files config.py and auth/jwt_handler.py, explicitly excluded per commit message). events.py has 7 new type errors (see below).
- Frontend typecheck: `pnpm typecheck` (tsc --noEmit) — PASSES, no errors.
- Backend tests: `pytest tests/workflows/` — 14 / 14 pass.
- Frontend tests: `pnpm test` — 21 / 21 pass.
- Dead code grep (print, console.log, TODO, FIXME, XXX) in all new files — CLEAN.
- Migration ↔ ARCHITECTURE.md §4 parity check — see notes below.
- Catalog ↔ API_CONTRACT.md §8 parity check — all 24 step types present, correct categories.
- Frontend mock catalog ↔ backend catalog cross-check — discrepancies found; see notes.
- Generated 5 edge-case tasks for Day 3+.

### Reviewer notes — Day 1 audit

**Issue 1 — BLOCKING (backend-lead): `events.py` fails mypy --strict (7 errors)**
`backend/workflows/events.py` shipped in commit 3d3ca14 but was not listed in the modules the backend-lead claimed were mypy-clean. All 7 errors are `Missing type arguments for generic type "dict"` — the `asyncio.Queue[dict]` and other `dict` uses lack type parameters. This is mypy --strict Day 9 blocker material if unfixed; fix now while the file is fresh. The fix is to replace bare `dict` with `dict[str, Any]` throughout. This is a non-trivial file (WS fan-out bus) that Day 2-3 code will call. Filed as task for backend-lead.

**Issue 2 — BLOCKING (backend-lead): Auth 401 errors not in contract error envelope**
`backend/routers/workflows.py` raises `HTTPException(status_code=401, detail="Missing token")` and `detail="Invalid token"`. FastAPI's default serialization produces `{"detail": "..."}`, NOT the `{"error": {"code": "...", "message": "..."}}` envelope specified in API_CONTRACT.md §2. Every other router (when this sprint writes them) must use the envelope. The test suite currently checks for `status_code == 401` but not the body shape. This will break the frontend's `isError()` discriminator for auth errors. Fix: raise `HTTPException` with a custom error body via a FastAPI exception handler, or return `JSONResponse` directly.

**Issue 3 — Non-blocking (both leads, note): ARCHITECTURE.md §4 says `user_id UUID` but actual `users.id` is Integer**
The ARCHITECTURE.md §4 DDL says `user_id UUID NOT NULL REFERENCES users(id)`. The real `users` table (pre-existing) uses `Integer` PK. The migration and Workflow model correctly use `Integer` to match reality. This is a doc error in ARCHITECTURE.md §4, not a code error — the code is correct. The doc was written before examining the existing users table. Will update ARCHITECTURE.md §4 to say `INTEGER` to prevent future confusion. No code change required.

**Issue 4 — Non-blocking (frontend-lead, note): output_schema mismatch between backend and mock catalog**
The frontend `lib/mock-catalog.ts` output schemas for several steps differ from what the backend registry emits. These will reconcile on Day 5 when the frontend swaps to the real endpoint, but the mismatches mean any test against output_schema content would fail:
- `action.place_order`: backend has `{order_id, status, client_request_id}`; mock has `{broker_order_id, submitted_at}`.
- `action.cancel_orders`: backend has `{cancelled_count, order_ids}`; mock has `{cancelled_ids}`.
- `action.set_stoploss`: backend has `{trigger_id, client_request_id}`; mock has `{broker_order_id}`.
- `notify.message`: backend has `{channel, delivered}`; mock has `{sent_at}`.
- `fetch.quote`: backend icon is `bar-chart-3`; mock icon is `line-chart` (cosmetic, no functional impact).
- `condition.numeric`: backend icon is `equal`; mock icon is `git-branch` (cosmetic).
These are non-blocking for Day 2 (the mock is intentionally temporary) but must be reconciled before Day 5 wire-up. Frontend-lead should update mock schemas to match the backend registry output, or at minimum acknowledge the gap.

**Issue 5 — Non-blocking (backend-lead, note): mock catalog test does not cover max_retries per step type**
The frontend `tests/lib/mock-catalog.test.ts` does not have a test asserting max_retries values (e.g. fetches=3, actions=1, notify.message=2). The backend catalog test does (test_max_retries_match_invariant_3). If a frontend-lead edits the mock catalog and sets wrong retry counts, nothing catches it until the swap to the real endpoint. Non-blocking for Day 2 but should be added.

**Positive findings:**
- All 24 step types present in both backend registry and frontend mock catalog.
- Correct category assignments across all 24 types in both backend and frontend (including the renamed `control.skip_if`, `wait.approval` under `notify`, `wait.delay` under `control`).
- No hardcoded step configs in frontend components — all driven from catalog.
- Webhook tokens confirmed to be in `workflow_webhook_tokens` table, not in `workflow_steps.config`. Security invariant upheld.
- `refs.py` correctly implements the `context.webhook_payload.*` namespace per the Day 1 contract fix.
- Migration enum values match spec exactly. All six tables present. All indexes match spec.
- Frontend `lib/types.ts` matches API_CONTRACT.md §11 precisely, including `RunSummary` with `step_count`.
- Frontend WS client has the 2s polling fallback and "reconnecting" state per §10.1.
- No `print()` or `console.log()` debug statements in any new file.
- No TODO/FIXME/XXX without context in any new file.

### Edge cases filed for Day 3+

1. `POST /api/workflows/{id}/activate` with a `trigger.schedule` whose cron expression is invalid (e.g. `"99 99 * * *"`) — does the engine reject it with 422 at activation time, or does it silently arm a schedule that never fires? Backend must validate cron syntax at activation, not just config schema.
2. `PATCH /api/workflows/{id}` with `steps=[]` (empty list) — the spec says this fully replaces the step list. Does the engine reject a 0-step workflow at activation, or only at run time? Should be a 422 at activation: "Workflow must have at least one step (a trigger at index 0)."
3. `POST /api/webhooks/{token}` fires for a `trigger.price` workflow (not a `trigger.webhook` workflow) — the token lookup finds a matching row, but the step_type at that step_index is wrong. What is the response? The engine should verify the referenced step is `trigger.webhook` before proceeding.
4. WS client on `pivot-next/lib/ws.ts` enters the 2s polling fallback. If the polling `getRun()` call itself returns an error (e.g. 401 expired token mid-session), the fallback loop will call `onError` on every tick and spam the UI. The fallback should back off on repeated polling errors, not just on WS reconnect failures.
5. `POST /api/workflows/{id}/run` is called while a run for the same workflow is already `awaiting_approval` (not `running`). The single-instance advisory lock is acquired at run start — but a run in `awaiting_approval` state has already released the lock (or has it?). If not, a user can never manually re-run while waiting for approval. This interaction between `single_instance` locking and approval gating needs a test.

### Blocked
- None blocking the leads' Day 2 work.

### At risk for 2026-05-17
- **`events.py` mypy errors (7 errors, Issue 1 above)**: This file ships Day 2 logic (WS streaming). If the engine imports it on Day 2 and mypy errors compound, the count will grow. Fix now costs 15 minutes; fix on Day 9 costs a panic. Backend-lead must fix before Day 2 engine PR.
- **Auth error envelope (Issue 2 above)**: Every new router that ships Day 2-3 must use the correct error format. If this pattern (bare HTTPException) is copy-pasted into the 6+ new routers (runs.py, approvals.py, webhooks.py, run_stream.py), rectifying 20+ call sites on Day 9 is a real risk. Backend-lead must establish the correct pattern in a base exception handler TODAY.
- **Output schema drift (Issue 4 above)**: Day 5 wire-up is the integration checkpoint. If the mock catalog's output schemas are wrong, any component that reads `output_schema` to render step output (e.g. RunView) will render broken UI. Low risk now; medium risk by Day 5.

### Frontend-lead — Day 2
- Shipped tasks #17, #18, #19. Plus reviewer fixes #4 + #5 absorbed in a parity commit.
  - **#17 StepConfigDrawer:** `lib/json-schema-to-zod.ts` (hand-rolled JSON-Schema → zod adapter, supports string/number/integer/boolean/enum/object + required, throws `UnsupportedSchemaError` on `array` / `$ref` so v1 never silently drops fields). `lib/refs.ts` (4-namespace validator + chip-picker suggestion builder; `context.webhook_payload` gated on a `trigger.webhook` step at index 0). `components/agent-panel/StepConfigDrawer.tsx` — secondary drawer with form generated dynamically from `catalogEntry.config_schema`; label / description / placeholder all from JSON-Schema fields; Cmd+Enter saves; Esc closes (scoped, doesn't bubble); 422 path highlights `details.field` and renders `error.message` verbatim. `RefChipPicker.tsx` autocomplete listbox opens on `{{`, suggestions filtered to valid namespaces, live ref-validation surfaces inline. Wired into WorkflowEditorMock — clicking a step card or selecting "Edit step" opens the drawer pre-filled with the step's current config.
  - **#18 StepTypePicker:** `components/agent-panel/StepTypePicker.tsx` shadcn `Command` palette in a Dialog. Categories rendered from `catalog.categories` in server-supplied order (no hardcoded list). Single-track invariant: at insertIndex 0 only the 6 trigger.* types render; at index > 0 every trigger.* is hidden. cmdk search filters across `step_type label description`. Add-step buttons (between steps and at the end) trigger the picker; on select, a new step is inserted with config seeded from `defaultConfigFromSchema()` and indices renumbered.
  - **#19 RunView:** `lib/mock-run.ts` 5-step deterministic simulator emitting frames in the exact API_CONTRACT.md §10 shape (snapshot → step_update / approval_requested / run_update), including a 2s `awaiting_approval` pause at step 3 that auto-resumes. `lib/api.ts` got `setBackendSource('mock'|'real')` as the single global toggle for catalog + run-stream. `lib/use-run-stream.ts` hook wraps mock-run / `openRunStream` and exposes `{ run, isReconnecting, error, pendingApprovals }`. `components/agent-panel/RunView.tsx` paints status-coded step rows (pending grey, running pulsing blue, succeeded green, failed red, skipped slate italic, awaiting_approval amber); each row expand-to-detail (output JSON pretty-printed, error_message, duration via `formatDistanceStrict`, attempts); approval banner with Approve / Reject (Day 2 mock — Day 5 will hit `decideApproval`); reconnecting pill on `connection_state="reconnecting"`. Mounted in `app/page.tsx` behind a "View run" button so it's reviewable without the chat.
  - **Reviewer #4 + #5:** mock-catalog `output_schema` for `action.place_order` / `action.cancel_orders` / `action.set_stoploss` / `notify.message` realigned with backend registry truth (e.g. `place_order` now `{order_id, status, client_request_id}`). Test now asserts `max_retries` matches ARCHITECTURE.md §7 invariant 3 for every step type, plus snapshot-locks output_schema parity for the 4 drifted types — locks parity for the Day-5 wire-up.
- 79 frontend tests pass (up from 21 on Day 1). `pnpm typecheck && pnpm lint && pnpm test && pnpm build` all clean.
- `setBackendSource("real")` flips every Day 2 surface to the live backend in one call — Day 5 wire-up is one line at the app entry.

### Next session (reviewer Day 3)
- Review Day 2 backend PRs: engine.py, REST endpoint implementations (POST/GET/PATCH workflows, activate/pause/archive/run, GET runs, cancel, approvals, webhook, WS stream). Check every new router against API_CONTRACT.md §2 error envelope.
- Review Day 2 frontend PRs: WorkflowEditor (real), StepConfigDrawer, StepTypePicker, any run-view wiring.
- Re-run full test suite; verify events.py mypy issue resolved.
- Verify Issue 2 (auth envelope) fixed before more routers ship with the same pattern.
- Walk any demo steps now exercisable (likely still 0/14 — needs live endpoints).
- Generate Day 3 edge cases.

### Demo path readiness (out of 14)
Day 2: 0 / 14. No live runtime yet — Day 2 is when engine + REST endpoints begin landing. Demo path will not be walkable until Day 4 at earliest (per build sequence ARCHITECTURE.md §15).

---

## Day 1 — 2026-05-02 (reviewer)

### Shipped
- Contract audit: cross-checked ARCHITECTURE.md and API_CONTRACT.md end-to-end. All findings resolved with direct doc edits (reviewer authority as contract owner). See "Decisions locked" below.
- Demo path readiness checklist added to STATUS.md (above).

Backend-lead / Frontend-lead Day 1 work in flight; entries to be appended by them on completion.

### Backend-lead — Day 1
- Shipped tasks #7, #8, #9: Alembic migration `0001_workflows.py` (6 tables, 3 PG enums, JSONB, advisory-lock-ready FK structure, `triggered_by` CHECK constraint), SQLA 2.0 models (`Workflow` / `WorkflowStep` / `WorkflowRun` / `WorkflowRunStep` / `WorkflowApproval` / `WorkflowWebhookToken`) + Pydantic v2 schemas covering every API_CONTRACT.md §3-§4 + §8.1 shape, full step-type registry with all 24 v1 step types and stub executors raising `NotImplementedError`, and `GET /api/step-types` mounted in main.py. Absorbed both reviewer Day-1 contract fixes: `control.skip_if` rename, `webhook_payload` ref namespace ruling (no Day-1 code ships refs.py yet — rule absorbed for Day 2-3 implementation). 14 workflow tests pass (5 model smoke + 9 catalog contract); ruff + mypy --strict clean on new modules. `jsonschema==4.23.0` added to `pivot/requirements.txt` for Day 2 engine-side config validation.

### Frontend-lead — Day 1
- Shipped tasks #10, #11, #12: scaffolded `pivot-next/` (Next.js 15 app router, TypeScript strict, Tailwind, ESLint, vitest + RTL) with the full pinned shadcn primitive inventory + `@dnd-kit/sortable`, `react-hook-form`, `zod`, `lucide-react`, `date-fns`. Hand-wrote `lib/types.ts` from API_CONTRACT.md §11 (incl. the new `RunSummary` with `step_count`), built `lib/api.ts` returning `Promise<ApiResult<T>>` with auth-token / idempotency-key / 5-min catalog-cache plumbing, `lib/ws.ts` typed run-stream client with 2s `getRun()` polling fallback per §10.1, and `lib/mock-catalog.ts` containing all 24 v1 step types with the canonical category mapping from §8 (`control.skip_if` rename absorbed; `wait.approval` under `notify`; `wait.delay` under `control`). Built the persistent right-side `AgentPanel` (custom resizable drawer, NOT shadcn `Sheet`), Esc-to-close, draggable left edge clamped 420-920px, mounted in `app/page.tsx` behind an "Open agent panel" CTA. Renders the 5-step demo workflow (schedule → fetch portfolio → numeric condition → place order with approval → notification) via `WorkflowEditorMock` with `@dnd-kit/sortable` reordering and Add-step dividers, fed entirely from the mock catalog so swap to real `/api/step-types` on Day 5 is one toggle. Empty / loading / error states present (Skeleton during catalog fetch, error state rendering `error.message`). 21 tests pass (5 panel + 6 mock catalog + 4 api wrapper + 4 config-preview + 2 button sanity); `pnpm typecheck && pnpm lint && pnpm test && pnpm build` all clean.

### Contract audit — findings and fixes

**Fix 1: `skip_if` renamed to `control.skip_if`.**
All other step types follow `category.subtype` dotted notation. `skip_if` was a bare identifier with no category prefix, making it impossible for the frontend to determine its category from the step_type string alone. Renamed to `control.skip_if` in ARCHITECTURE.md §5.6. API_CONTRACT.md §8 category table updated with explicit category assignment for all 24 v1 step types.

**Fix 2: `webhook_payload` ref namespace — definitive ruling.**
ARCHITECTURE.md §6 defined allowed ref namespaces (`context.<step_index>.<path>`, `now`, `workflow.<field>`). API_CONTRACT.md §9.1 used `{{ webhook_payload.<path> }}` which introduced an undocumented fourth namespace. This is now resolved: `webhook_payload` is NOT a sibling namespace. It is stored as a reserved literal key in the `context` bag (`run.context["webhook_payload"]`). The correct ref syntax is `{{ context.webhook_payload.<path> }}`. Both docs updated.

**Fix 3: `RunSummary` type and `step_count` field.**
`GET /api/workflows/{id}/runs` list items include `step_count` (not in the canonical Run shape). The existing doc said "Run shape (§4) but without `context` and `steps[]`" which obscured this additive field. Added an explicit `RunSummary` TypeScript type in API_CONTRACT.md §11, and clarified the §6.1 description to name the field and state it is list-view only.

**Fix 4: Category assignment table for all step types.**
API_CONTRACT.md §8 only showed two example step types in the catalog response. The backend lead had no normative reference for what `category` value to assign to `wait.approval`, `wait.delay`, or `control.skip_if`. Added a complete table covering all 24 v1 step types with their canonical `category` values.

**No-change findings (documented for leads):**
- `triggered_by` values: ARCHITECTURE.md §4 SQL comment already includes all 6 values (`schedule`, `manual`, `webhook`, `price_alert`, `indicator_alert`, `event_alert`). Matches API_CONTRACT.md §11. No change needed.
- `workflow_status`, `run_status`, `step_status`: consistent across both docs.
- `halt_reason` values: consistent.
- `error.code` list: all codes used in later sections are in the §2 stable list. No unlisted codes found.
- All 16 endpoints in ARCHITECTURE.md §9 have full request/response shapes in API_CONTRACT.md §5-§10.
- `PATCH /api/workflows/{id}` blocks on `status='active'` — intentional; client must pause first. Consistent between docs.

### Blocked
- None blocking leads today. Day 1 work can proceed.

### At risk for 2026-05-17
- **`control.skip_if` rename** — this changes the step_type string from the original spec. If backend-lead has already committed any code referencing `skip_if`, they must update it. Flag: check their Day 1 PR for any hardcoded `'skip_if'` string.
- **Webhook executor writes `run.context["webhook_payload"]`** — this is now a contract requirement, not optional. Backend-lead must write the raw body to this key before handing off to the next step. If they miss it, any workflow referencing `{{ context.webhook_payload.* }}` will fail with a ref-not-found error. Verify in their executor test.
- **`step_count` in `GET /api/workflows/{id}/runs`** — backend must compute and return this field. It's not in the main `workflow_runs` table (it's a join count against `workflow_steps` for the relevant `workflow_version`). Backend-lead should note this is a derived field.

### Next session (reviewer Day 2)
- Read backend-lead Day 1 PR: check migration DDL matches ARCHITECTURE.md §4 exactly (enum values, column names, nullable/not-null), check Pydantic models match API_CONTRACT.md §3/§4, check `GET /api/step-types` response matches §8 (all 24 step types, correct category assignments per the new table, correct `max_retries` per ARCHITECTURE.md §7 invariant 3).
- Read frontend-lead Day 1 PR: check TypeScript strict mode is on, check `pivot-next/` compiles without errors, check AgentPanel shell uses the correct API types from API_CONTRACT.md §11.
- Generate Day 2 edge cases.

### Demo path readiness (out of 14)
Day 1: 0 / 14. (No runtime code shipped yet; docs locked.)

---

## Day 0 — 2026-05-02 (lead)

### Shipped
- `docs/ARCHITECTURE.md` — full architecture: data model, step catalog, engine invariants, scheduler, API surface, chatbot integration, stack decisions, build sequence, scope discipline.
- `docs/API_CONTRACT.md` — REST + WebSocket contract: error format, every endpoint with request/response shapes, step-type catalog response, WS frame schema, frontend state types.
- `STATUS.md` (this file) — seeded.
- `BACKLOG.md` — seeded with the explicit "do not build" list and v2 ideas.
- Project memory persisted: Pivot Agent System sprint context, repo layout, dev-branch / no-push rule.

### Decisions locked (vs. spec doc)
1. **Backend paths:** `pivot/backend/workflows/`, `pivot/backend/routers/{workflows,runs,approvals,webhooks,run_stream}.py`, `pivot/backend/agents/tools/propose_workflow.py`. NOT spec's `src/...`.
2. **DB driver:** sync SQLAlchemy 2.0 + psycopg2 (matches existing repo). NOT asyncpg. Async only at FastAPI handler / worker boundaries.
3. **Frontend:** Next.js 15 at `pivot-next/` (new dir, alongside legacy Vite `frontend/`). Acknowledged ~1-2 day timeline cost from porting chat UI.
4. **Scheduler:** extend existing `backend/scheduler.py` (already has `AsyncIOScheduler` + `SQLAlchemyJobStore` + 60s strategy-trigger pattern). No parallel scheduler.
5. **Hooks:** dropped the spec's `.claude/hooks.json` config — `TaskCompleted`/`TeammateIdle` are not real Claude Code events. Quality gates baked into teammate briefs and CI instead.
6. **Tool registry:** `propose_workflow` plugs into the existing `tool(name, description, properties, required)` pattern in `backend/agents/tools.py`. New tool subset `WORKFLOW_PROPOSE`.

### Blocked
- None.

### At risk for 2026-05-17
- **Vite → Next.js port** of the chat UI is the largest unknown. If it eats more than 2 calendar days, the polish phase (Day 7) compresses. Mitigation: keep the legacy Vite frontend running; only the Agent System UI is required to be in `pivot-next/` for v1. Chat UI port can be partial — Agent panel mounts inside whichever frame ships first.
- **`propose_workflow` LLM constraint** — the LLM must produce schema-valid step configs. Falls back to a parser-only path with one retry loop. If validation fails twice, surface a clear error in chat. Test with 10+ NL prompts on Day 6 (reviewer mandate).

### Next session (Day 1)
**Backend lead** picks up:
1. Schema migration `pivot/migrations/versions/0001_workflows.py` matching ARCHITECTURE.md §4.
2. SQLAlchemy models in `pivot/backend/models.py` + Pydantic schemas in `pivot/backend/schemas.py`.
3. Step-type catalog endpoint `GET /api/step-types` (read-only, returns the locked catalog).

**Frontend lead** picks up:
1. Scaffold `pivot-next/` (Next.js 15 + TypeScript strict + Tailwind + shadcn init).
2. Port `pivot-next/components/chat/` shell (no real chat plumbing yet — just the layout the Agent panel will mount inside).
3. `pivot-next/components/agent-panel/AgentPanel.tsx` shell (resizable right drawer, closeable, with mock workflow data).

**Reviewer (this role):**
1. Re-read both docs end-to-end after backend + frontend start coding; flag any drift.
2. Build the test scaffolds (pytest + vitest dirs + minimal "sanity passes" tests).

### Demo path readiness (out of 14)
Day 0: 0/14. (Docs only; no demo path exercisable yet.)

---

## Template for future days

```
## Day N — YYYY-MM-DD (role)

### Shipped
- ...

### Blocked
- ...

### At risk for 2026-05-17
- ...

### Next session
- backend-lead: ...
- frontend-lead: ...
- reviewer: ...

### Demo path readiness (out of 14)
- N/14, with notes on which steps work end-to-end.
```
