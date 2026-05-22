"""Stage 8 — firing + audit + workflow handoff.

When the aggregator returns a ``Fire`` decision, this module:

  1. Writes a ``news_fired_events`` row capturing the audit
     payload (tier, aggregated confidence, supporting
     classification ids, fired_at). The UNIQUE constraint on
     ``event_spec_id`` is our idempotency guarantee — concurrent
     fire decisions on the same spec collapse into a single row.
  2. Flips ``news_event_specs.state`` to ``'fired'`` so the
     aggregator won't be re-invoked for this spec.
  3. If the spec carries a ``workflow_id``, calls the public
     ``fire_external_event`` seam (Touch 1) to start a
     ``WorkflowRun``. The audit_context is what lands in
     ``workflow_runs.context["news_event"]`` — the engine and
     downstream step executors can read it to decide what to do.
  4. Returns a ``FireOutcome`` summarising what happened (whether
     a workflow was fired, whether this was the first fire for
     this spec, etc.).

Stage 8 is intentionally minimal: order construction lives inside
the existing workflow engine. The user authored the workflow with
the right ``action.place_order`` steps + approval gating; Phase 5
just hands the engine the trigger signal, idempotently.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.news_events.models import NewsEventSpec, NewsFiredEvent
from backend.news_events.pipeline.aggregate import FiringDecision

logger = logging.getLogger(__name__)


@dataclass
class FireOutcome:
    """Per-fire summary returned by ``fire_spec``."""

    spec_id: str
    fired_event_id: Optional[str] = None
    workflow_run_id: Optional[str] = None
    duplicate: bool = False  # True iff the UNIQUE guard caught a re-fire
    workflow_attached: bool = False  # spec had a workflow_id
    reason: str = ""


def _build_audit_context(
    *,
    spec: NewsEventSpec,
    decision: FiringDecision,
    fired_event_id: str,
    fired_at: datetime,
) -> dict:
    """The dict that lands in ``workflow_runs.context["news_event"]``.

    Kept small so the workflow engine's context isn't bloated, but
    rich enough for a step executor or an approval-summary
    formatter to write a coherent "why was this run started"
    explanation.
    """
    return {
        "spec_id": spec.id,
        "fired_event_id": fired_event_id,
        "tier": spec.tier,
        "description": spec.description,
        "aggregated_confidence": float(decision.aggregated_confidence),
        "supporting_classification_ids": list(
            decision.supporting_classification_ids
        ),
        "reason": decision.reason,
        "fired_at": fired_at.isoformat(),
    }


async def fire_spec(
    db: Session,
    *,
    spec: NewsEventSpec,
    decision: FiringDecision,
    now: Optional[datetime] = None,
    prediction_market_snapshot: Optional[dict] = None,
) -> FireOutcome:
    """Persist the fire + (if configured) start the workflow.

    Idempotency is enforced two ways:
      - UNIQUE(event_spec_id) on ``news_fired_events`` — a second
        call on the same spec hits ``IntegrityError`` and we
        return ``duplicate=True`` without doing anything.
      - We pre-check ``spec.state == 'fired'`` so the racy case
        short-circuits before the INSERT.

    ``prediction_market_snapshot`` (Phase 6, Tier-3): when the
    aggregator consulted Polymarket, the caller passes the snapshot
    dict here. Persisted on
    ``news_fired_events.prediction_market_snapshot`` for the audit
    pane.

    Caller must be inside an open Session; this function does its
    own ``db.commit()`` so the audit row and state change land
    together before the workflow run is enqueued.
    """
    if decision.status != "fire":
        return FireOutcome(
            spec_id=spec.id,
            reason=f"aggregator returned status={decision.status!r}",
        )

    if spec.state == "fired":
        return FireOutcome(
            spec_id=spec.id,
            duplicate=True,
            reason="spec already in state 'fired'",
        )

    fired_at = now or datetime.now(timezone.utc)
    retract_policy = dict(spec.retraction_policy or {})
    safety_window_minutes = int(
        retract_policy.get("safety_window_minutes", 0) or 0
    )
    retraction_window_ends_at = (
        fired_at + _minutes(safety_window_minutes)
        if safety_window_minutes > 0
        else None
    )

    # Fast-path dedup: pre-check the audit table. Avoids the
    # IntegrityError branch in the common case while still leaving
    # it as a backstop against truly concurrent inserts.
    existing = (
        db.query(NewsFiredEvent.id)
        .filter(NewsFiredEvent.event_spec_id == spec.id)
        .first()
    )
    if existing is not None:
        logger.info(
            "[news_events.fire] duplicate spec_id=%s — audit row exists",
            spec.id,
        )
        return FireOutcome(
            spec_id=spec.id,
            duplicate=True,
            reason="fired event audit row already exists",
        )

    row = NewsFiredEvent(
        event_spec_id=spec.id,
        workflow_run_id=None,
        fired_at=fired_at,
        tier=spec.tier,
        aggregated_confidence=float(decision.aggregated_confidence),
        supporting_classification_ids=list(
            decision.supporting_classification_ids
        ),
        prediction_market_snapshot=prediction_market_snapshot,
        retraction_window_ends_at=retraction_window_ends_at,
        retraction_status="none",
    )
    # Wrap the INSERT in a SAVEPOINT so a concurrent UNIQUE
    # violation rolls back ONLY this add, not the whole session.
    db.add(row)
    try:
        savepoint = db.begin_nested()
        try:
            db.flush()
            savepoint.commit()
        except IntegrityError:
            savepoint.rollback()
            raise
    except IntegrityError:
        logger.info(
            "[news_events.fire] duplicate spec_id=%s — UNIQUE caught race",
            spec.id,
        )
        return FireOutcome(
            spec_id=spec.id,
            duplicate=True,
            reason="UNIQUE caught duplicate fire",
        )

    spec.state = "fired"
    db.flush()

    audit_context = _build_audit_context(
        spec=spec,
        decision=decision,
        fired_event_id=row.id,
        fired_at=fired_at,
    )

    # Commit the audit row + state change BEFORE we ask the
    # engine to start the run. That way a crash in the workflow
    # handoff still leaves an authoritative audit trail.
    db.commit()

    outcome = FireOutcome(
        spec_id=spec.id,
        fired_event_id=row.id,
        workflow_attached=bool(spec.workflow_id),
        reason=decision.reason,
    )

    if not spec.workflow_id:
        logger.info(
            "[news_events.fire] fired spec_id=%s — no workflow attached, "
            "audit-only",
            spec.id,
        )
        return outcome

    # Touch-1 seam — the only public function in the workflows
    # package that news_events depends on.
    from backend.workflows.scheduler import fire_external_event

    try:
        run_id = await fire_external_event(
            workflow_id=spec.workflow_id,
            triggered_step_index=0,
            fired_at=fired_at,
            audit_context=audit_context,
        )
    except Exception as exc:  # noqa: BLE001 — never crash the funnel
        logger.exception(
            "[news_events.fire] fire_external_event_failed spec_id=%s "
            "workflow_id=%s err=%s",
            spec.id,
            spec.workflow_id,
            exc,
        )
        return outcome

    if run_id is None:
        logger.warning(
            "[news_events.fire] workflow_inactive_or_missing "
            "spec_id=%s workflow_id=%s",
            spec.id,
            spec.workflow_id,
        )
        return outcome

    # Persist the link so the audit endpoint can join the two
    # halves cheaply.
    row.workflow_run_id = run_id
    db.add(row)
    db.commit()
    outcome.workflow_run_id = run_id
    logger.info(
        "[news_events.fire] fired spec_id=%s fired_event_id=%s "
        "workflow_run_id=%s",
        spec.id,
        row.id,
        run_id,
    )
    return outcome


def _minutes(n: int):
    from datetime import timedelta

    return timedelta(minutes=n)
