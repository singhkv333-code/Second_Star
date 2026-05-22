# News & Event Trigger — Phase 5 summary

**Status:** complete. Ready for human review before Phase 6.
**Date:** 2026-05-21.
**Scope:** Stage 7 confidence aggregator, Stage 8 firing + audit,
the Touch-1 seam in `workflows/scheduler.py`, the
`/api/news-events/fired/{id}` audit endpoint, and the
`events_fired` counter on `/admin/metrics`.

This is the phase that lets an active spec actually fire a workflow
when the LLM verdicts cross the per-tier confidence rule.

---

## What was built

- **`backend/news_events/pipeline/aggregate.py`** — `evaluate_firing`
  applies the per-tier rules from the Phase-0 plan:
  - **Tier 1**: a single primary-source YES (confidence ≥
    `min_confidence`) fires.
  - **Tier 2**: one primary YES OR ≥(`min_secondary_confirmations`+1)
    secondary YES from distinct sources inside the lookback window.
  - **Tier 3**: primary YES + ≥`min_secondary_confirmations`
    secondary YES; when `primary_sources` is empty (the parser's
    default), require ≥(`min_secondary_confirmations`+1) distinct
    secondary sources. Any NO / RETRACTION / AMBIGUOUS verdict
    inside the window plus `conflict_policy="hold"` causes a hold.
  - Lookback window default 120 min. Stage 1 dedup respected
    (only canonical `near_dup_of IS NULL` rows count).

- **`backend/news_events/pipeline/propose.py`** — `fire_spec` persists
  the audit row, flips spec state to `'fired'`, and hands off to
  the workflow engine via the Touch-1 seam.
  - **Idempotency** is enforced two ways: (a) a pre-check against
    `news_fired_events.event_spec_id` short-circuits the common
    case; (b) a SAVEPOINT wraps the INSERT so a UNIQUE-violation
    race only rolls back this add, not the caller's whole
    transaction.
  - Audit row written + spec state flipped + committed BEFORE the
    workflow handoff so a crash in the engine call still leaves a
    complete audit trail.
  - Engine handoff exceptions are swallowed and logged — the audit
    row stands; the user gets a "fired but workflow_run not
    linked" state that an ops operator can re-fire manually.
  - Specs without a `workflow_id` still write the audit row but
    don't call the engine. Phase 6's alerting can read those.

- **Touch 1 (`backend/workflows/scheduler.py`)** — the only
  Phase-5 modification of pre-news_events code:
  - `_fire_watch_run` gains one optional `audit_context: dict|None`
    kwarg. When supplied, the `WorkflowRun.context` is seeded with
    `{"news_event": audit_context}` instead of `{}`. The existing
    `_fire_one` and price/indicator watchers don't pass it — their
    behaviour is byte-for-byte unchanged.
  - **New** public `fire_external_event(*, workflow_id,
    triggered_step_index, fired_at, audit_context)` wrapper sits
    next to it. Always uses `triggered_by='event_alert'` (the
    pre-existing CHECK-constraint value). Returns the new
    `workflow_run.id` so the caller can link it in
    `news_fired_events.workflow_run_id`. Raises `ValueError` if
    called with an empty audit context — defense against accidental
    use from a non-news_events path.

- **`backend/news_events/pipeline/funnel.py`** — wired the
  aggregator + fire path into the per-row Stage-3-6 work. After
  each Stage-6 verdict lands, we call `evaluate_firing(spec)` and,
  if it returns Fire, call `fire_spec`. New counters
  `specs_evaluated` and `specs_fired` on `FunnelTickResult`.

- **`backend/news_events/router.py`** — `GET
  /api/news-events/fired/{id}` audit endpoint. Joins
  `news_fired_events` → `news_event_specs` →
  `news_article_classifications` → `news_articles` so the FE can
  render a single coherent "why we fired" pane.

- **`backend/news_events/router.py` /admin/metrics** — gains the
  `events_fired` counter (count of `news_fired_events` rows fetched
  inside the window).

- **`backend/news_events/schemas.py`** — `FiredEventResponse` +
  `FiredClassificationView` DTOs.

- **Tests** — 24 new (108 total):
  - `test_aggregate.py` (13) — table-driven per-tier rules
    including Tier-3 conflict-on-NO and conflict-on-RETRACTION
    handling, multi-source consensus, same-source-twice rejection.
  - `test_fire_spec.py` (6) — audit row written, state flipped,
    workflow handoff seam called with the right payload,
    workflow-handoff failure leaves the audit intact, idempotent
    on terminal state, idempotent under UNIQUE race.
  - `test_funnel_with_firing.py` (2) — end-to-end through the
    funnel orchestrator with stubbed externals; confirms the
    aggregator + fire path lights up after Stage 6.
  - `test_fired_router.py` (3) — audit endpoint happy path,
    cross-user 404, unknown-id 404.
  - **108/108 news_events tests pass.** Existing suite at the same
    25 pre-existing failures, no regressions — including no
    regression from the Touch-1 modification of
    `_fire_watch_run`.

## What changed in existing code

**One file**, exactly two changes — both pre-approved in the
Phase-0 plan:

1. `pivot/backend/workflows/scheduler.py`:
   - `_fire_watch_run` gains an optional `audit_context` kwarg
     (None by default → unchanged behaviour for existing callers).
   - New public `fire_external_event(...)` wrapper appended just
     after it.

No other file outside `backend/news_events/` and
`tests/news_events/` was modified. No new migration. No new
dependency.

## Verified live (Postgres, prod-like env, real Azure LLM + real workflow engine)

End-to-end via the running backend:

1. **Setup** — registered a user, created + activated a workflow
   (`workflow_id=3597358f-...-9ef4e`), parsed an event spec via
   the live Azure LLM ("If the RBI cuts the repo rate, run the
   workflow"), attached the workflow at creation time, activated
   the spec.

2. **Seed** — wrote one classification directly into the DB:
   `(source=rbi_press_releases, verdict=YES, confidence=0.96)` (the
   spec's `min_confidence` was 0.95).

3. **Aggregator** — `evaluate_firing` returned:
   ```
   status=fire
   reason="tier1 primary YES landed"
   aggregated_confidence=0.96
   supporting_classification_ids=[0465d27a-...]
   ```

4. **Fire** — `fire_spec` produced:
   ```
   fired_event_id=104cff67-...
   workflow_run_id=c317bf9d-...
   workflow_attached=True
   ```
   - `news_fired_events` row landed with the supporting ids,
     aggregated confidence, retraction window (60 min per spec
     policy).
   - `news_event_specs.state` flipped: `active → fired`.
   - **`workflow_runs` row created with**
     `triggered_by='event_alert'`,
     `status='running'`,
     `context.news_event` = the full audit payload (spec_id, tier,
     description, aggregated_confidence, supporting ids, fired_at).
   - `workflows.last_run_at` updated by the existing engine path.

5. **Audit endpoint** — `GET /api/news-events/fired/104cff67-...`
   returned the joined view: spec description, tier, workflow_run
   linkage, retraction window timestamp, supporting array carrying
   the article title / URL / source / verdict / confidence /
   excerpt. Ready for an FE audit pane with zero further joins.

6. **Metrics** — `GET /admin/metrics?window_hours=1` now reports
   `events_fired=1`.

## Isolation audit

- [x] All new code under `backend/news_events/` and
      `tests/news_events/`.
- [x] One existing file modified: `backend/workflows/scheduler.py`
      — Touch 1 only (one optional kwarg + one new function), per
      the Phase-0 plan.
- [x] No new migration in Phase 5 — Phase-1 tables had the right
      shape.
- [x] No new dependency.
- [x] **Confirmation step preserved.** The fire path drops the
      run into the existing workflow engine; any `action.place_order`
      step with `requires_approval=true` triggers the existing
      `workflow_approvals` machinery. We did NOT build a new
      confirmation surface — by design, per the Phase-0 plan §1.2
      ("the existing engine ... covers the mandatory user
      confirmation step").
- [x] Idempotency: pre-check + SAVEPOINT-wrapped UNIQUE constraint
      catch the duplicate-fire race without corrupting the
      surrounding transaction.
- [x] Audit-first ordering: the audit row + state change commit
      BEFORE the engine handoff. A crash in `fire_external_event`
      still leaves a complete audit trail and a clean
      `state='fired'`.
- [x] Engine handoff failures are swallowed (logged), not
      re-raised — the funnel never crashes because a downstream
      workflow has problems.
- [x] Cross-user access on the audit endpoint returns 404 (not
      403), matching the existing workflows-router convention.
- [x] Feature flag still gates the whole subsystem at the
      main.py level.

## Surprises and decisions worth flagging

1. **The Azure parser sets `min_confidence=0.95` on Tier-1 specs.**
   Higher than the 0.85 default in the schema. That's fine — the
   parser learned this from the system prompt; we may want a
   slightly more permissive Tier-1 default if false-negative rates
   bite us, but the current behaviour is what a careful operator
   would write by hand.
2. **`fire_external_event` returns the run_id; the seam-wrapper
   pattern works cleanly.** Touch 1 was the only existing-code
   change in the entire build. Worth highlighting: the Phase-0
   plan called this out in §3.5 and the cost was ~25 LOC across
   two functions in `scheduler.py`.
3. **SAVEPOINT for IntegrityError handling.** The naive
   `db.rollback()` works in production (auto-commit transactions)
   but rolls back too far inside the pytest fixture (nested
   transaction). A pre-check + `db.begin_nested()` covers both
   cases. The pre-check is also a perf win for the common
   "spec was already fired" path.
4. **Engine handoff is fire-and-forget.** `fire_external_event`
   returns the `run_id` after the INSERT commits but BEFORE
   `engine.execute_run` is awaited (it's wrapped in
   `asyncio.create_task`). So a slow workflow can't back-pressure
   the funnel.
5. **No back-channel for the engine to update the audit row.** If
   a step inside the fired workflow fails, the audit row's
   `workflow_run_id` link still points there — the FE can chase
   to `workflow_runs.status` for the actual result. Cleaner than
   maintaining two state machines.

## Open questions still outstanding

- (Q1) Touch 1 — **DONE.** Implemented this phase.
- (Q5) n8n proxy — Phase 7.
- (Q6) NSE / BSE filings — deferred.
- (Q7) Kalshi — Phase 6.
- (Q8) Approvals UI — works today via the existing
  `workflow_approvals` surface (no new endpoint needed). Whether
  the `pivot-next/` UI already lists a workflow run that was
  triggered by `event_alert` is unverified — that's a quick FE
  check before launch.
- (Q9) Prod-egress verification of BS / ET / Mint / SEBI —
  outstanding.
- (Q10) Retention policy — outstanding.

## Recommended next step

Move to **Phase 6 — Tier-3 hardening**:

- Polymarket Gamma cross-check (new `sources/polymarket.py`
  adapter that returns the current market price for a Tier-3
  event the spec watches; aggregator consults it as an additional
  confirmation signal when `prediction_market_threshold` is set).
- Retraction-window watcher: a new APScheduler job that polls
  recently-fired `news_fired_events` rows with `retraction_status='none'`
  and, when a new RETRACTION classification lands on the same
  spec, executes the spec's `retraction_policy.action`
  (`cancel_pending_approvals` cancels any open `workflow_approvals`
  for the related run; `cancel_and_alert` does that plus posts an
  alert).
- Optional: Kalshi cross-check (RSA-PSS auth — bigger effort).

Phase 7 (optional WebSub / n8n transport upgrade) is independent
of Phase 6 and can land any time.
