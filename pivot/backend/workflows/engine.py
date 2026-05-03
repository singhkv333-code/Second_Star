"""WorkflowEngine — orchestrates a single run end-to-end.

Honors every ARCHITECTURE.md §7 invariant:

  1. Idempotency. Every action step generates
     `client_request_id = sha1(f"{run_id}:{step_index}:{attempts}")` and
     passes it to the executor. Retries with the same id MUST be safe.

  2. Persistence at every boundary. The engine writes the run-step row
     to the DB *before* the executor's external call, and writes the
     terminal status *before* publishing to the WS bus. A worker crash
     mid-step leaves a `running` step row whose `started_at` is set —
     the next worker treats it as in-flight and either resumes or marks
     it failed.

  3. Per-step retries with backoff. Pulled from the registry. Sleeps
     1s/4s/16s between attempts.

  4. Approval gating. `wait.approval` and `requires_approval=true` flip
     the run to `awaiting_approval` and create a workflow_approvals
     row. The engine returns; resumption is triggered by the approvals
     router calling `resume_run()`.

  5. Run isolation. Postgres advisory lock keyed on the workflow_id
     hash. SQLite has no advisory locks, so we degrade to a Python-side
     lock that's still enforced within the test process.

  6. Time budget. Default 30 min wall clock. On exceed: `failed`
     with `halt_reason='time_budget'`.

  7. Schema validation at every boundary. Step config is re-validated
     against the registry's Pydantic model right before the executor
     runs (defense in depth — the API has already validated on create
     and PATCH, but we don't trust DB drift).

Concurrency model: the engine itself is async, but the per-step
executor may be sync or async. We do all DB work via sync SQLAlchemy
sessions inside `asyncio.to_thread()` so the loop never blocks on I/O.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator, Optional

from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.database import SessionLocal
from backend.models import (
    RunStatus,
    StepStatus,
    Workflow,
    WorkflowApproval,
    WorkflowRun,
    WorkflowRunStep,
    WorkflowStep,
)
from backend.workflows.events import RUN_BUS
from backend.workflows.refs import RefNotFoundError, resolve_refs
from backend.workflows.registry import STEP_REGISTRY, StepDefinition

logger = logging.getLogger(__name__)


# Default wall-clock budget per ARCHITECTURE.md §7 invariant 6. Tests
# override via `WorkflowEngine(time_budget_seconds=…)`.
DEFAULT_TIME_BUDGET_SECONDS = 30 * 60

# Backoff schedule per invariant 3. attempts is 1-indexed; the first
# retry sleeps BACKOFFS[0]. Max retries is per-step from the registry.
_BACKOFFS = (1.0, 4.0, 16.0)

# Heartbeat staleness threshold for crash recovery (§8 last bullet).
_STALE_HEARTBEAT_SECONDS = 5 * 60

# Engine-side cancel + advisory-lock fallback. SQLite doesn't expose
# pg_try_advisory_lock; we fall back to this dict so the single-instance
# invariant still holds in tests.
_PROCESS_LOCKS: dict[str, threading.Lock] = {}
_PROCESS_LOCKS_GUARD = threading.Lock()

# In-flight cancel flags. Set by `cancel_run(run_id)`; checked at every
# step boundary inside `execute_run`.
_CANCEL_FLAGS: dict[str, bool] = {}
_CANCEL_GUARD = threading.Lock()


class EngineError(Exception):
    """Internal engine error that should mark the run as failed."""


def _client_request_id(run_id: str, step_index: int, attempts: int) -> str:
    """Deterministic id used for downstream idempotency. SHA-1 hex
    digest of `<run_id>:<step_index>:<attempts>` per §7 invariant 1."""
    h = hashlib.sha1()
    h.update(f"{run_id}:{step_index}:{attempts}".encode("utf-8"))
    return h.hexdigest()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def cancel_run(run_id: str) -> None:
    """Mark a running run as cancel-requested. The engine checks this
    flag at every step boundary and terminates with `cancelled`."""
    with _CANCEL_GUARD:
        _CANCEL_FLAGS[run_id] = True


def _is_cancelled(run_id: str) -> bool:
    with _CANCEL_GUARD:
        return _CANCEL_FLAGS.get(run_id, False)


def _clear_cancel(run_id: str) -> None:
    with _CANCEL_GUARD:
        _CANCEL_FLAGS.pop(run_id, None)


@contextmanager
def _single_instance_lock(
    db: Session, workflow_id: str, single_instance: bool
) -> Iterator[bool]:
    """Acquire the single-instance lock for `workflow_id`. Yields True
    if acquired, False if another run holds it."""
    if not single_instance:
        yield True
        return

    dialect = db.bind.dialect.name if db.bind else ""
    if dialect == "postgresql":
        # Hash the UUID to a 64-bit signed int as required by PG advisory
        # lock keys. We use the lowest 63 bits of SHA-1 so it's stable
        # across processes.
        digest = hashlib.sha1(workflow_id.encode("utf-8")).digest()
        key = int.from_bytes(digest[:8], "big", signed=True)
        got = db.execute(
            text("SELECT pg_try_advisory_lock(:k)"), {"k": key}
        ).scalar()
        try:
            yield bool(got)
        finally:
            if got:
                db.execute(
                    text("SELECT pg_advisory_unlock(:k)"), {"k": key}
                )
    else:
        # SQLite / others: process-local fallback.
        with _PROCESS_LOCKS_GUARD:
            lock = _PROCESS_LOCKS.setdefault(workflow_id, threading.Lock())
        acquired = lock.acquire(blocking=False)
        try:
            yield acquired
        finally:
            if acquired:
                lock.release()


def recover_stale_runs() -> int:
    """On app boot, mark any `running` run whose latest run-step
    heartbeat is older than _STALE_HEARTBEAT_SECONDS as `failed` with
    `error_message='worker crash'`. Returns the number recovered.

    Heartbeat = the most recent `started_at` among the run's steps. A
    crash mid-step leaves the run-step `running` with no `finished_at`;
    the engine on next boot stamps the run failed.
    """
    db = SessionLocal()
    recovered = 0
    try:
        cutoff = _utcnow() - timedelta(seconds=_STALE_HEARTBEAT_SECONDS)
        runs = (
            db.query(WorkflowRun)
            .filter(WorkflowRun.status == RunStatus.running)
            .all()
        )
        for run in runs:
            # Pick the most recent step's `started_at` as heartbeat. If
            # the run has no steps yet, fall back to the run's
            # `started_at`.
            last = (
                db.query(WorkflowRunStep)
                .filter(WorkflowRunStep.run_id == run.id)
                .order_by(WorkflowRunStep.step_index.desc())
                .first()
            )
            heartbeat = (
                last.started_at if last and last.started_at else run.started_at
            )
            if heartbeat is None:
                heartbeat = run.started_at
            # heartbeat may be naive (SQLite) — coerce to aware UTC.
            if heartbeat.tzinfo is None:
                heartbeat = heartbeat.replace(tzinfo=timezone.utc)
            if heartbeat < cutoff:
                run.status = RunStatus.failed
                run.finished_at = _utcnow()
                run.error_message = "worker crash"
                # Also stamp the in-flight step (if any) as failed so
                # the run log reflects reality.
                if last and last.status in (
                    StepStatus.running, StepStatus.pending,
                ):
                    last.status = StepStatus.failed
                    last.finished_at = _utcnow()
                    last.error_message = "worker crash"
                recovered += 1
        if recovered:
            db.commit()
    finally:
        db.close()
    return recovered


class WorkflowEngine:
    """Stateless executor — one instance per app process is fine.

    Public entry points:
      - `execute_run(run_id)`: run from start (or from the first
        `pending`/`running` step on resume).
      - `resume_run(run_id)`: re-enter an `awaiting_approval` run after
        the approval was decided.
      - `cancel_run(run_id)`: signal cancel; checked at boundaries.
    """

    def __init__(
        self,
        *,
        time_budget_seconds: int = DEFAULT_TIME_BUDGET_SECONDS,
    ) -> None:
        self.time_budget_seconds = time_budget_seconds

    # ── Public entry points ────────────────────────────────────────────
    async def execute_run(self, run_id: str) -> None:
        """Drive a run to a terminal state.

        On first entry: acquires the single-instance lock if applicable,
        then loops over the workflow's steps. On a resumption (the run
        is already in `awaiting_approval` and the approval has been
        decided), the caller should use `resume_run()` instead.
        """
        await asyncio.to_thread(self._run_loop, run_id, False)

    async def resume_run(self, run_id: str) -> None:
        """Re-enter a run that paused on an approval. The approvals
        router calls this after writing the decision."""
        await asyncio.to_thread(self._run_loop, run_id, True)

    # ── Core loop (sync, runs in a thread) ─────────────────────────────
    def _run_loop(self, run_id: str, resuming: bool) -> None:
        """The real work. Sync because every DB call is sync. Wrapped
        in `asyncio.to_thread` from the async surface above."""
        db = SessionLocal()
        try:
            run = db.query(WorkflowRun).filter_by(id=run_id).first()
            if run is None:
                logger.error("execute_run: no such run %s", run_id)
                return

            if run.status not in (
                RunStatus.running, RunStatus.awaiting_approval,
            ):
                logger.info(
                    "execute_run: run %s is %s, nothing to do",
                    run_id, run.status,
                )
                return

            workflow = (
                db.query(Workflow).filter_by(id=run.workflow_id).first()
            )
            if workflow is None:
                self._terminate(
                    db, run, RunStatus.failed,
                    error_message="workflow vanished",
                )
                return

            # Single-instance lock — only on initial execute, not on
            # resume (we already had the lock when we paused and the
            # awaiting_approval run still owns it logically).
            with _single_instance_lock(
                db, workflow.id, workflow.single_instance
            ) as got:
                if not got and not resuming:
                    self._terminate(
                        db, run, RunStatus.cancelled,
                        error_message=(
                            f"single-instance lock held by another run "
                            f"of workflow {workflow.id}"
                        ),
                    )
                    return
                self._execute_steps(db, run, workflow)
        finally:
            db.close()
            _clear_cancel(run_id)

    def _execute_steps(
        self, db: Session, run: WorkflowRun, workflow: Workflow,
    ) -> None:
        """Iterate over the workflow's steps in order. Uses the
        workflow_steps rows that match `run.workflow_version`."""
        deadline = _utcnow() + timedelta(seconds=self.time_budget_seconds)

        # Workflow steps are stored by current `workflow_id` only — v1
        # doesn't snapshot per-version step rows. We assume a paused-edit
        # cycle has updated `workflow.version` on edit, but the step
        # rows belong to the latest version. Honoured by the PATCH
        # endpoint.
        steps: list[WorkflowStep] = list(
            db.query(WorkflowStep)
            .filter(WorkflowStep.workflow_id == workflow.id)
            .order_by(WorkflowStep.step_index)
            .all()
        )

        # Resume-from-pending: existing run-step rows tell us how far we
        # got. We re-run the first non-`succeeded`/non-`skipped` row.
        run_steps_by_index: dict[int, WorkflowRunStep] = {
            rs.step_index: rs for rs in run.steps
        }

        # `skip_next` set by control.skip_if executor. The very next
        # step's status flips to `skipped` and isn't executed.
        skip_next = False

        for step in steps:
            if _is_cancelled(run.id):
                self._terminate(
                    db, run, RunStatus.cancelled,
                    error_message="cancelled by user",
                )
                return

            if _utcnow() > deadline:
                self._terminate(
                    db, run, RunStatus.failed,
                    halt_reason="time_budget",
                    error_message="run exceeded time budget",
                )
                return

            existing = run_steps_by_index.get(step.step_index)
            if existing and existing.status in (
                StepStatus.succeeded, StepStatus.skipped,
            ):
                # Already done on a prior pass — propagate skip_next
                # marker if it was set on resume.
                continue

            # Pre-resolved skip handling.
            if skip_next:
                rs = self._upsert_run_step(
                    db, run, step,
                    status=StepStatus.skipped,
                    started_at=_utcnow(),
                    finished_at=_utcnow(),
                    output={"skipped": True},
                )
                self._publish_step(run.id, rs)
                skip_next = False
                continue

            outcome = self._execute_one_step(db, run, workflow, step)

            if outcome.kind == "succeeded":
                # If this step was a control.skip_if AND it triggered,
                # mark skip_next so the upcoming step is skipped.
                if (
                    step.step_type == "control.skip_if"
                    and isinstance(outcome.output, dict)
                    and outcome.output.get("skipped_next") is True
                ):
                    skip_next = True
                continue

            if outcome.kind == "condition_fail":
                # ARCHITECTURE.md §5.3: failed condition is NOT an error.
                # Run completes successfully with halt_reason set.
                self._terminate(
                    db, run, RunStatus.succeeded,
                    halt_reason="condition_not_met",
                )
                return

            if outcome.kind == "awaiting_approval":
                # Persist the awaiting_approval state and bail out. The
                # approvals router will call `resume_run()` on decision.
                run.status = RunStatus.awaiting_approval
                db.commit()
                self._publish_run(run)
                # And surface the new approval over the bus.
                approval = (
                    db.query(WorkflowApproval)
                    .filter_by(run_id=run.id, step_index=step.step_index)
                    .order_by(WorkflowApproval.requested_at.desc())
                    .first()
                )
                if approval is not None:
                    RUN_BUS.publish_threadsafe(
                        run.id,
                        {
                            "type": "approval_requested",
                            "run_id": run.id,
                            "approval": _approval_to_dict(approval),
                        },
                    )
                return

            if outcome.kind == "cancelled":
                # Approval was rejected — terminate as cancelled.
                self._terminate(
                    db, run, RunStatus.cancelled,
                    error_message=outcome.error_message,
                )
                return

            # Anything else == failure.
            self._terminate(
                db, run, RunStatus.failed,
                error_message=outcome.error_message or "step failed",
            )
            return

        # All steps succeeded.
        self._terminate(db, run, RunStatus.succeeded)

    # ── Per-step execution ────────────────────────────────────────────
    def _execute_one_step(
        self,
        db: Session,
        run: WorkflowRun,
        workflow: Workflow,
        step: WorkflowStep,
    ) -> "_StepOutcome":
        """Run a single step, handling retries, persistence, and the
        special outcomes (condition_fail, awaiting_approval, cancelled)."""
        defn: Optional[StepDefinition] = STEP_REGISTRY.get(step.step_type)
        if defn is None:
            rs = self._upsert_run_step(
                db, run, step,
                status=StepStatus.failed,
                started_at=_utcnow(),
                finished_at=_utcnow(),
                error_message=f"unknown step_type {step.step_type!r}",
            )
            self._publish_step(run.id, rs)
            return _StepOutcome(
                kind="failed",
                error_message=f"unknown step_type {step.step_type!r}",
            )

        # Defense-in-depth schema validation (§7 invariant 7).
        try:
            defn.config_model.model_validate(step.config or {})
        except ValidationError as e:
            rs = self._upsert_run_step(
                db, run, step,
                status=StepStatus.failed,
                started_at=_utcnow(),
                finished_at=_utcnow(),
                error_message=f"config invalid: {e.errors()[0]['msg']}",
            )
            self._publish_step(run.id, rs)
            return _StepOutcome(
                kind="failed",
                error_message=f"config invalid: {e.errors()[0]['msg']}",
            )

        # Resolve refs against the run context. The webhook payload (if
        # present) lives at context["webhook_payload"]; numeric step
        # outputs live at context[str(step_index)]. The resolver doesn't
        # care which.
        try:
            resolved_config = resolve_refs(
                step.config or {},
                context=run.context or {},
                workflow_meta={
                    "id": workflow.id,
                    "name": workflow.name,
                    "version": workflow.workflow_version_or_current(),
                },
            )
        except RefNotFoundError as e:
            rs = self._upsert_run_step(
                db, run, step,
                status=StepStatus.failed,
                started_at=_utcnow(),
                finished_at=_utcnow(),
                error_message=str(e),
            )
            self._publish_step(run.id, rs)
            return _StepOutcome(kind="failed", error_message=str(e))

        # Persist running state BEFORE the executor runs. This is the
        # fundamental persistence-before-call invariant. If we crash
        # between this commit and the next, recover_stale_runs() will
        # find the stale `running` row.
        rs = self._upsert_run_step(
            db, run, step,
            status=StepStatus.running,
            started_at=_utcnow(),
        )
        self._publish_step(run.id, rs)

        max_attempts = defn.max_retries + 1
        last_error: Optional[str] = None

        for attempt in range(1, max_attempts + 1):
            if _is_cancelled(run.id):
                rs.status = StepStatus.failed
                rs.finished_at = _utcnow()
                rs.error_message = "cancelled by user"
                db.commit()
                self._publish_step(run.id, rs)
                return _StepOutcome(
                    kind="cancelled", error_message="cancelled by user",
                )

            client_request_id = _client_request_id(
                run.id, step.step_index, attempt,
            )
            ctx = _ExecutorContext(
                run=run,
                step=step,
                workflow=workflow,
                config=resolved_config,
                attempts=attempt,
                client_request_id=client_request_id,
                db=db,
            )

            try:
                output = _run_executor(defn.executor, ctx)
            except _ConditionFail:
                # Condition step explicitly signalled fail-closed.
                rs.status = StepStatus.succeeded
                rs.finished_at = _utcnow()
                rs.output = {"passed": False}
                rs.attempts = attempt
                db.commit()
                self._publish_step(run.id, rs)
                return _StepOutcome(kind="condition_fail")

            except _AwaitingApproval as wait:
                # Action requested approval. Persist awaiting_approval.
                rs.status = StepStatus.awaiting_approval
                rs.attempts = attempt
                rs.output = {"approval_id": wait.approval_id}
                db.commit()
                self._publish_step(run.id, rs)
                return _StepOutcome(
                    kind="awaiting_approval",
                    approval_id=wait.approval_id,
                )

            except Exception as e:
                last_error = str(e) or e.__class__.__name__
                logger.warning(
                    "step %s attempt %d/%d failed: %s",
                    step.step_type, attempt, max_attempts, last_error,
                )
                if attempt >= max_attempts:
                    rs.status = StepStatus.failed
                    rs.finished_at = _utcnow()
                    rs.error_message = last_error
                    rs.attempts = attempt
                    db.commit()
                    self._publish_step(run.id, rs)
                    return _StepOutcome(
                        kind="failed", error_message=last_error,
                    )
                # Backoff before retry.
                backoff = (
                    _BACKOFFS[min(attempt - 1, len(_BACKOFFS) - 1)]
                )
                # In tests we keep this short via an env-stubbable sleep.
                _engine_sleep(backoff)
                continue

            # Success — persist output to context bag too.
            rs.status = StepStatus.succeeded
            rs.finished_at = _utcnow()
            rs.output = output
            rs.attempts = attempt
            ctx_bag = dict(run.context or {})
            if output is not None:
                ctx_bag[str(step.step_index)] = output
            run.context = ctx_bag
            db.commit()
            self._publish_step(run.id, rs)
            return _StepOutcome(kind="succeeded", output=output)

        # Should never fall through.
        return _StepOutcome(
            kind="failed", error_message=last_error or "unknown error",
        )

    # ── Persistence helpers ───────────────────────────────────────────
    def _upsert_run_step(
        self,
        db: Session,
        run: WorkflowRun,
        step: WorkflowStep,
        *,
        status: StepStatus,
        started_at: Optional[datetime] = None,
        finished_at: Optional[datetime] = None,
        output: Optional[dict[str, Any]] = None,
        error_message: Optional[str] = None,
    ) -> WorkflowRunStep:
        """Get-or-create the WorkflowRunStep row for (run, step_index)
        and stamp the latest fields. Commits before returning so the
        new state is durable."""
        rs = (
            db.query(WorkflowRunStep)
            .filter_by(run_id=run.id, step_index=step.step_index)
            .first()
        )
        if rs is None:
            rs = WorkflowRunStep(
                run_id=run.id,
                step_index=step.step_index,
                step_type=step.step_type,
                status=status,
                started_at=started_at,
                finished_at=finished_at,
                output=output,
                error_message=error_message,
                attempts=1,
            )
            db.add(rs)
        else:
            rs.status = status
            if started_at is not None:
                rs.started_at = started_at
            if finished_at is not None:
                rs.finished_at = finished_at
            if output is not None:
                rs.output = output
            if error_message is not None:
                rs.error_message = error_message
        db.commit()
        db.refresh(rs)
        return rs

    def _terminate(
        self,
        db: Session,
        run: WorkflowRun,
        status: RunStatus,
        *,
        halt_reason: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> None:
        run.status = status
        run.finished_at = _utcnow()
        if halt_reason is not None:
            run.halt_reason = halt_reason
        if error_message is not None:
            run.error_message = error_message
        db.commit()
        self._publish_run(run)

    def _publish_step(self, run_id: str, rs: WorkflowRunStep) -> None:
        RUN_BUS.publish_threadsafe(
            run_id,
            {
                "type": "step_update",
                "run_id": run_id,
                "step_index": rs.step_index,
                "step": _run_step_to_dict(rs),
            },
        )

    def _publish_run(self, run: WorkflowRun) -> None:
        RUN_BUS.publish_threadsafe(
            run.id,
            {
                "type": "run_update",
                "run_id": run.id,
                "status": run.status.value
                if hasattr(run.status, "value")
                else str(run.status),
                "finished_at": (
                    run.finished_at.isoformat() if run.finished_at else None
                ),
                "halt_reason": run.halt_reason,
            },
        )


# ── Executor protocol helpers ────────────────────────────────────────


class _ExecutorContext:
    """The single argument every executor receives.

    Attributes are exposed individually so executors can pull only what
    they need. Carries a live SQLAlchemy session for executors that need
    DB access (e.g. wait.approval writing to workflow_approvals)."""

    def __init__(
        self,
        *,
        run: WorkflowRun,
        step: WorkflowStep,
        workflow: Workflow,
        config: dict[str, Any],
        attempts: int,
        client_request_id: str,
        db: Session,
    ) -> None:
        self.run = run
        self.step = step
        self.workflow = workflow
        self.config = config
        self.attempts = attempts
        self.client_request_id = client_request_id
        self.db = db


class _ConditionFail(Exception):
    """Internal signal raised by condition.* executors when their
    predicate is false. The engine catches this and terminates the run
    with `succeeded` + `halt_reason='condition_not_met'`."""


class _AwaitingApproval(Exception):
    """Internal signal raised by `wait.approval` and any action with
    `requires_approval=true` to indicate that the engine should pause
    and exit. The approval row id is carried so subscribers learn about
    the new pending approval."""

    def __init__(self, approval_id: str) -> None:
        self.approval_id = approval_id


class _StepOutcome:
    """Plain DTO for the result of a step's execution loop."""

    def __init__(
        self,
        *,
        kind: str,
        output: Optional[dict[str, Any]] = None,
        error_message: Optional[str] = None,
        approval_id: Optional[str] = None,
    ) -> None:
        # one of: succeeded | failed | condition_fail | awaiting_approval
        # | cancelled
        self.kind = kind
        self.output = output
        self.error_message = error_message
        self.approval_id = approval_id


def _run_executor(
    executor: Any, ctx: _ExecutorContext,
) -> Optional[dict[str, Any]]:
    """Run an executor synchronously. The registry signature is async
    (ARCHITECTURE.md §3 — async at boundaries), so we drive the
    coroutine to completion using asyncio.run inside this thread.

    Engine itself is invoked via asyncio.to_thread, so creating a fresh
    loop here is safe — we never re-enter the outer loop. Tests that
    poke the engine directly inside an outer loop get a fresh inner
    loop per executor call.
    """
    coro = executor(ctx)
    if asyncio.iscoroutine(coro):
        try:
            return asyncio.run(coro)
        except RuntimeError as e:
            # If we're already inside a running loop (shouldn't happen
            # via to_thread, but defensive), use a new loop in this
            # thread.
            if "asyncio.run() cannot" in str(e):
                loop = asyncio.new_event_loop()
                try:
                    return loop.run_until_complete(coro)
                finally:
                    loop.close()
            raise
    return coro  # type: ignore[no-any-return]


# ── Helpers shared with WS frame builders ────────────────────────────


def _run_step_to_dict(rs: WorkflowRunStep) -> dict[str, Any]:
    return {
        "step_index": rs.step_index,
        "step_type": rs.step_type,
        "status": rs.status.value if hasattr(rs.status, "value")
        else str(rs.status),
        "started_at": rs.started_at.isoformat() if rs.started_at else None,
        "finished_at": (
            rs.finished_at.isoformat() if rs.finished_at else None
        ),
        "output": rs.output,
        "error_message": rs.error_message,
        "attempts": rs.attempts,
    }


def _approval_to_dict(approval: WorkflowApproval) -> dict[str, Any]:
    return {
        "id": approval.id,
        "run_id": approval.run_id,
        "step_index": approval.step_index,
        "summary": approval.summary,
        "requested_at": (
            approval.requested_at.isoformat() if approval.requested_at
            else None
        ),
        "expires_at": (
            approval.expires_at.isoformat() if approval.expires_at else None
        ),
        "decision": approval.decision,
        "decided_at": (
            approval.decided_at.isoformat() if approval.decided_at else None
        ),
    }


# ── Sleep shim (override-able for tests) ─────────────────────────────


def _engine_sleep(seconds: float) -> None:
    """Synchronous sleep used between retries. Tests monkeypatch this
    to zero so suites stay fast (no 16-second waits)."""
    import time
    time.sleep(seconds)


# ── Workflow.workflow_version_or_current shim ────────────────────────
# We attach a tiny helper to Workflow at import time so refs.py can ask
# for `workflow.version` consistently. Avoids a circular import.

def _workflow_version_or_current(self: Workflow) -> int:
    return int(self.version) if self.version is not None else 1


Workflow.workflow_version_or_current = _workflow_version_or_current  # type: ignore[attr-defined]
