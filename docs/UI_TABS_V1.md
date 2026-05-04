# UI Tabs v1 — Agents / Calendar / Portfolio

> Three new tabs alongside the existing Chat surface in `pivot-next/`. Pick up after Day 5 wire-up tasks (#31 real-backend, #32 chat draft card, #33 RunHistory) land. Each tab is a small, self-contained task — keep under the per-task LOC cap.
>
> **Order:** Agents tab first (highest demo value) → Calendar → Portfolio.
>
> **Design discretion is yours.** Fonts, spacing, color, dark-vs-light, animations — your call. The reference dark mock (described §5 below) is FEATURE reference, not design reference. Existing Public.com-class aesthetic is the source of truth for craft. If the mock conflicts with your design instincts, trust your instincts.

---

## 1. Agents tab — `#34`

User's saved agents — what they've built or had proposed via chat. **Not** a community catalog or seeded strategies. Lists workflows owned by the authenticated user.

**Data source:** `GET /api/workflows` (already shipped backend).

**Per-row fields** (from the `WorkflowSummary` shape, [API_CONTRACT.md §11](./API_CONTRACT.md)):
- `name` (heading)
- `description` (one-line, muted)
- `status` badge — `draft` / `active` / `paused` / `archived` — same color palette as `RunView` step statuses
- `last_run_at` — relative time via date-fns ("2h ago", "yesterday")
- `next_run_at` if not null — relative time ("tomorrow at 3:55 PM IST")

**Empty state:** when `items.length === 0`, surface a muted illustration + "No agents yet — start a chat to propose one."

**Loading state:** shadcn `Skeleton` row × 5.

**Error state:** render `error.message` from the canonical envelope.

**Click behavior:** opens the existing `AgentPanel` for that workflow (same flow as the chat-card → editor path). Read full `Workflow` via `GET /api/workflows/{id}` on click.

**Filter:** allow `?status=active,paused` query — small filter chips at the top (All / Active / Paused / Archived). All by default.

**LOC budget:** ~200 production + ~100 tests.

---

## 2. Calendar tab — `#35`

When the user's agents are scheduled to run.

**Data sources:**
- `trigger.schedule` workflows — fire times computed from cron + timezone within a date range
- `trigger.event` workflows — currently cut to v2 (no event source wired). Tab should render gracefully empty for these until backend ships.

**Endpoint:** `GET /api/workflows/scheduled-runs?from=ISO8601&to=ISO8601` — **does not exist on the backend yet**. Backend task `#37` (logged below) will add it. **Mock per [API_CONTRACT.md](./API_CONTRACT.md) §11 type extension below until backend ships.**

Mock + real shape (response):

```ts
type ScheduledRun = {
  workflow_id: string;
  workflow_name: string;
  trigger_type: "trigger.schedule" | "trigger.event";
  fire_time: string;       // ISO 8601 UTC
  fire_time_local: string; // formatted in the trigger's tz, e.g. "3:55 PM IST"
};

type ScheduledRunsResponse = { items: ScheduledRun[] };
```

Mock generator: enumerate the next 30 days from each known `trigger.schedule` workflow's cron; emit one entry per fire time per workflow.

**Views:** **Month** (calendar grid, dot markers per day, click day → drawer with that day's runs) and **Agenda** (chronological list, scrollable). Tab toggle between them.

**Per-entry fields:**
- agent name
- trigger type (icon + short label)
- fire time, formatted in the user's local tz with a tooltip showing the trigger's tz when different

**Click behavior:** opens the agent in `AgentPanel`.

**Empty state:** "No scheduled runs in this range. Activate an agent with a schedule trigger to see it here."

**LOC budget:** ~250 production + ~120 tests. Calendar grids drag — keep it minimal: month view is just a 7×6 grid of day cells with dot count; click → modal/drawer with the day's list. No per-entry rendering inside the grid cells.

---

## 3. Portfolio tab — `#36`

Read-only view of the user's holdings, P&L, and a performance chart.

**Data source:** `GET /api/portfolio` (existing endpoint — serves the same data `fetch.portfolio` reads internally).

**Top metric strip** (also visible in the header per the dark mock's pattern):
- Portfolio value (₹X,XX,XXX)
- Day P&L (+/- ₹X,XXX with arrow color)
- Total P&L (+/- ₹X,XXX +/- X.XX%)

**Holdings table:**
- symbol, qty, avg buy, LTP, P&L (₹), day % (with color), value (₹)
- Sortable by any column (default: value desc)
- Empty state when no holdings

**Performance line chart:**
- Portfolio value over time + NIFTY benchmark overlay
- Default range: 1Y; range chips for 1M / 3M / 6M / 1Y / All
- Use a chart library that's already in your inventory (recharts is the lightest add if needed; otherwise pure SVG is fine for a single-line chart)

**Behavior:** read-only. No edit/buy/sell actions on this tab — those live in chat. The Portfolio tab's job is "see my state at a glance."

**LOC budget:** ~250 production + ~120 tests. Charts are the temptation to overshoot — keep it simple.

---

## 4. Out of scope for v1 (log to BACKLOG.md if proposed)

- News tab
- Strategy catalog (community / seeded agents)
- Screener tab
- Conversations sidebar overhaul (the "YOUR CONVERSATIONS" left rail in the dark mock)
- Real-time portfolio value (use polling at 30s if you need to refresh; no WS for portfolio)
- Per-asset deep-dive page

---

## 5. The reference dark mock

The user provided a screenshot of a dark Quartr-style mock. **It is feature reference only — it tells you what info to surface. It does NOT tell you how to style it.**

What's in the mock that's relevant to these three tabs:

- **Header:** logo top-left, search bar center, top-right metric strip (Portfolio value, Day P&L, Total P&L). The metric strip is the design pattern for the Portfolio tab top.
- **Left sidebar nav:** Chat (active), Portfolio, News, Agents, Calendar, Screener. We're building Agents + Calendar + Portfolio. News + Screener are cut.
- **"YOUR CONVERSATIONS" rail** below the nav: chat history with Today / Yesterday grouping. **Out of scope** — keep your existing chat history surface.
- **Center pane:** chat thread with an inline order-draft card. This is what Day 1-2 work already covered (the workflow_draft_card render in #32).
- **Right pane:** "REVIEW YOUR AGENT" panel showing a 5-step agent ("NVDA buy the dip" → Trigger / Fetch quote / Evaluate signal / Decide / Act). This is the existing `AgentPanel` + `WorkflowEditor` you already built. **Don't touch it.**

What you should NOT take from the mock:
- The all-dark color palette (you support both light + dark modes — your call which is default)
- The numbered-step rendering in the right pane (your existing dnd-kit step cards win)
- The exact label wording (you have a richer step-type catalog — use the labels from `step_type` + the user's `label` field)

---

## 6. Hard constraints

- **TypeScript strict.** Both light and dark mode required for every new component.
- **Per-task LOC budget** — do not exceed. If a task is heading past, stop and split.
- **No new libraries** beyond your Day 1 inventory unless absolutely necessary. If you need a chart lib, `recharts` is the lightest add; document the version in the commit message.
- **Don't touch backend** or `frontend/` (legacy Vite). Don't touch what's already shipped (AgentPanel, WorkflowEditor, StepConfigDrawer, StepTypePicker, RunView, RunHistory).
- **Don't `git push`.**
- **Coordinate with backend** if you hit an endpoint that doesn't exist. The `/api/workflows/scheduled-runs` endpoint for the Calendar tab is the one known gap (task `#37`); backend will add it.

---

## 7. Backend endpoint gap (logged separately)

`GET /api/workflows/scheduled-runs?from=...&to=...` — does not exist yet. Tracked as backend task `#37`. v1 scope: enumerate next-fire times for `trigger.schedule` workflows in `[from, to]` only; `trigger.event` returns empty until events source is wired. Frontend should mock this until backend ships, then swap with no component changes.
