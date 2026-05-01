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

## Day 1 — 2026-05-02 (reviewer)

### Shipped
- Contract audit: cross-checked ARCHITECTURE.md and API_CONTRACT.md end-to-end. All findings resolved with direct doc edits (reviewer authority as contract owner). See "Decisions locked" below.
- Demo path readiness checklist added to STATUS.md (above).

Backend-lead / Frontend-lead Day 1 work in flight; entries to be appended by them on completion.

### Backend-lead — Day 1
- Shipped tasks #7, #8, #9: Alembic migration `0001_workflows.py` (6 tables, 3 PG enums, JSONB, advisory-lock-ready FK structure, `triggered_by` CHECK constraint), SQLA 2.0 models (`Workflow` / `WorkflowStep` / `WorkflowRun` / `WorkflowRunStep` / `WorkflowApproval` / `WorkflowWebhookToken`) + Pydantic v2 schemas covering every API_CONTRACT.md §3-§4 + §8.1 shape, full step-type registry with all 24 v1 step types and stub executors raising `NotImplementedError`, and `GET /api/step-types` mounted in main.py. Absorbed both reviewer Day-1 contract fixes: `control.skip_if` rename, `webhook_payload` ref namespace ruling (no Day-1 code ships refs.py yet — rule absorbed for Day 2-3 implementation). 14 workflow tests pass (5 model smoke + 9 catalog contract); ruff + mypy --strict clean on new modules. `jsonschema==4.23.0` added to `pivot/requirements.txt` for Day 2 engine-side config validation.

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
