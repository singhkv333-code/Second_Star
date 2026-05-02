# BACKLOG — Pivot Agent System

> v2+ ideas and explicit out-of-scope items for v1. Anything proposed mid-sprint that lands here gets blocked.

---

## Explicitly NOT in v1 (do not build)

These are blocked by the spec. If proposed, log here and refuse.

| Item | Why deferred |
|---|---|
| Branching / if-else trees | Linear sequence is the v1 mental model; graph editor is huge UI scope |
| Loops / for-each | Scope; rarely needed in retail strategies |
| Sub-workflows / workflow composition | Adds dependency graph + cycle detection — too much for 7-9 days |
| Public template marketplace | Standalone product. Ship after v1 has users. |
| Custom code blocks | Security review + sandboxing is its own project |
| Mobile-responsive editor | Desktop-only by design; reviewers test on a laptop |
| Multi-user collab on the same workflow | Real-time CRDT or locking; not in scope |
| Workflow versioning UI | DB column exists; UI shows current only. Restore-from-history is v2. |
| Live backtest integration in editor | Link to the existing standalone backtester is fine; embedded backtest is v2 |

### Step types intentionally left as `NotImplementedError` (formally cut 2026-05-02)

The catalog still publishes these so the frontend's StepTypePicker stays consistent, but executing one fails the run with a clear "not yet implemented" message. Status:

| Step type | Reason for cut | Path back to real |
|---|---|---|
| `trigger.event` | No event source wired in this repo (RBI/results/FII feeds are external services). The legacy `_get_upcoming_events` tool returns a stub placeholder. | Build an events ingestion service or wire to a third-party provider (TrueData, NSE corporate actions API). |
| `fetch.news` | No news source wired. Sarvam-summarised search is possible but adds external dep + cost. Lowest priority of the cut items. | Wire to a news API (newsdata.io, Marketaux) or build a Sarvam-summarised RSS aggregator. |

Other previously-stubbed step types are now real (`trigger.price`/`indicator`/`webhook`, `fetch.quote`/`indicator`/`fundamental`, every condition/action except event-dependent ones, every notify/control). See `STATUS.md` for shipped status per task.

---

## v2 ideas (post-Speedrun)

Captured for triage after we ship v1. Not committed.

### Engine
- Branching (`condition.numeric` with `then`/`else` arms)
- Sub-workflows (one workflow as a step inside another)
- Configurable time budget per workflow
- Per-step custom retry policy + dead-letter queue
- Replay mode: re-run a past run with a fresh `client_request_id` namespace (debug only)

### Step types
- `fetch.macro` — RBI, FRED, Bloomberg-like macro indicators
- `fetch.options_chain` — option chain with greeks
- `action.rebalance` — multi-leg rebalance with target weights
- `notify.discord` / `notify.slack`
- `transform.expr` — small DSL for cross-step computation (vs. always using `condition.numeric`)
- `trigger.news_sentiment` — fires on sentiment threshold cross

### UX
- Templates gallery (curated, hand-built — not user-submitted)
- Inline backtest of the current draft against the last 6 months
- Run timeline visualization (Gantt-style) for long-running workflows
- Mobile read-only view (see runs, approve, but not edit)
- Share a workflow as a read-only link

### Ops
- Celery / Temporal worker (only if we hit asyncio worker scaling limits)
- Multi-region scheduler with leader election
- Per-user quota: max active workflows, max runs/hour, max watch symbols
- Audit export to CSV/JSON

### Chatbot
- Multi-turn workflow refinement ("now add a stop-loss") that diffs the draft instead of regenerating
- Voice input via Sarvam (already in the stack)
- Workflow explanation: chatbot describes what an existing workflow does in plain English

### Observability
- Per-step latency p50/p95 dashboards
- Failure-rate-by-step-type leaderboard
- "Why did my workflow halt?" page with deep-link from STATUS

---

## Triage rules

- **Blocked v2 idea proposed during sprint** → log here under v2 ideas, link the source (chat / PR / issue), refuse.
- **Bug reports** → tracked separately as GitHub issues (or comments on the relevant task), not here.
- **Day 6 cut order** (per ARCHITECTURE.md §15): `trigger.webhook` → `fetch.news` → `trigger.indicator` → `trigger.event`. Items cut land here with a note.
