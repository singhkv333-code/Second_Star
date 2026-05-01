# STATUS — Pivot Agent System Sprint

> Daily reviewer-owned status report. Read top-to-bottom: most recent day first. Lead reads this to plan the next session.

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
