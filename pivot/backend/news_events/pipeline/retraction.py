"""Phase 6 — retraction-window watcher logic.

After a spec fires, we set ``news_fired_events.retraction_window_ends_at``
to ``fired_at + safety_window_minutes``. During that window, a
``RETRACTION`` verdict from any source on the same spec should
trigger the spec's ``retraction_policy.action``. This module owns
the detection-and-handling work; ``workers/retraction_watcher.py``
schedules it.

Action semantics (defined in
``backend/news_events/schemas.py::RetractionPolicy.action``):

  - ``cancel_pending_approvals``
        Cancel any open ``workflow_approvals`` rows for the
        ``workflow_run_id`` linked to the fired event. The existing
        approval-decision path (the engine reads
        ``WorkflowApproval.decision == 'rejected'`` and halts the
        run) handles propagation.

  - ``cancel_and_alert``
        Same approval cancellation PLUS a structured log line at
        WARNING level so ops dashboards surface it. Phase 6 stops
        at the log; richer alerting (email, Slack) is downstream.

  - ``ignore``
        No-op. The audit row still records the retraction was
        detected, but no approvals are touched and no log is fired.

The watcher is idempotent: a fired event whose ``retraction_status``
is already ``'handled'`` or ``'detected'`` is skipped. Re-running the
function within the same window is safe.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from backend.database import SessionLocal
from backend.models import WorkflowApproval
from backend.news_events.models import (
    NewsArticleClassification,
    NewsEventSpec,
    NewsFiredEvent,
)

logger = logging.getLogger(__name__)


@dataclass
class RetractionTickResult:
    """Per-tick summary returned by ``scan_for_retractions``."""

    candidates_seen: int = 0
    retractions_detected: int = 0
    approvals_cancelled: int = 0
    alerts_emitted: int = 0
    actions: dict[str, int] = field(default_factory=dict)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _normalise(dt: Optional[datetime]) -> Optional[datetime]:
    """SQLite returns naive datetimes; normalise to UTC-aware so the
    comparisons in the scan query are consistent across dialects."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _find_open_approvals(db: Session, *, run_id: str) -> list[WorkflowApproval]:
    """Return WorkflowApproval rows that have no decision yet for the
    given workflow_run."""
    return (
        db.query(WorkflowApproval)
        .filter(
            WorkflowApproval.run_id == run_id,
            WorkflowApproval.decision.is_(None),
        )
        .all()
    )


def _cancel_approvals(
    db: Session, *, run_id: str, reason: str
) -> int:
    """Mark every open approval for this run as 'rejected'. The engine
    reads ``decision == 'rejected'`` and halts the run."""
    approvals = _find_open_approvals(db, run_id=run_id)
    if not approvals:
        return 0
    now = _now()
    for ap in approvals:
        ap.decision = "rejected"
        ap.decided_at = now
        # Append the reason to the summary so the user can see why.
        suffix = f"\n\n[news_events retraction] {reason}"
        ap.summary = (ap.summary or "") + suffix
    db.flush()
    return len(approvals)


def _latest_retraction_classification(
    db: Session, *, spec_id: str, after: datetime
) -> Optional[NewsArticleClassification]:
    """The most recent RETRACTION verdict for this spec, created after
    the spec fired."""
    return (
        db.query(NewsArticleClassification)
        .filter(
            NewsArticleClassification.event_spec_id == spec_id,
            NewsArticleClassification.classifier_verdict == "RETRACTION",
            NewsArticleClassification.created_at > after,
        )
        .order_by(NewsArticleClassification.created_at.desc())
        .first()
    )


def handle_one_retraction(
    db: Session,
    *,
    fired: NewsFiredEvent,
    spec: NewsEventSpec,
    classification: NewsArticleClassification,
    result: Optional[RetractionTickResult] = None,
) -> str:
    """Run the spec's retraction_policy.action and persist the audit
    columns on the fired-event row. Returns the action that was
    actually taken (may differ from the policy when the engine has
    moved on — e.g. ``no_pending_approvals``)."""
    policy = dict(spec.retraction_policy or {})
    action = str(policy.get("action", "cancel_and_alert"))
    now = _now()

    # Mark detected up front so a partial failure still leaves a
    # consistent audit trail.
    fired.retraction_status = "detected"
    fired.retraction_detected_at = now
    fired.retraction_classification_id = classification.id
    db.flush()

    actual_action: str
    if action == "ignore":
        actual_action = "ignore"
    elif not fired.workflow_run_id:
        actual_action = "workflow_run_missing"
    else:
        cancelled = _cancel_approvals(
            db,
            run_id=fired.workflow_run_id,
            reason=(
                f"Event retraction detected at {now.isoformat()} "
                f"(classification {classification.id})."
            ),
        )
        if cancelled == 0:
            actual_action = "no_pending_approvals"
        else:
            actual_action = action
            if result is not None:
                result.approvals_cancelled += cancelled
        if action == "cancel_and_alert":
            logger.warning(
                "[news_events.retraction] cancel_and_alert spec_id=%s "
                "fired_event_id=%s workflow_run_id=%s cancelled=%d "
                "classification_id=%s",
                spec.id,
                fired.id,
                fired.workflow_run_id,
                cancelled,
                classification.id,
            )
            if result is not None:
                result.alerts_emitted += 1

    fired.retraction_action_taken = actual_action
    fired.retraction_status = "handled"
    db.flush()

    logger.info(
        "[news_events.retraction] handled spec_id=%s fired_event_id=%s "
        "action=%s",
        spec.id, fired.id, actual_action,
    )
    if result is not None:
        result.actions[actual_action] = result.actions.get(actual_action, 0) + 1
        result.retractions_detected += 1
    return actual_action


def scan_for_retractions(
    *,
    db_session: Optional[Session] = None,
) -> RetractionTickResult:
    """Find fired events still inside their safety window with a new
    RETRACTION classification, and dispatch the policy action for
    each.

    Same session-management dual mode as ``ingest_one_source``: pass
    ``db_session`` (route path) and the caller owns lifecycle;
    otherwise we open and close our own.
    """
    owns_session = db_session is None
    db = db_session if db_session is not None else SessionLocal()
    result = RetractionTickResult()
    try:
        now = _now()
        # Candidates: fired events whose retraction window is still
        # open and that haven't already been handled.
        candidates = (
            db.query(NewsFiredEvent)
            .filter(
                NewsFiredEvent.retraction_status == "none",
                NewsFiredEvent.retraction_window_ends_at.is_not(None),
            )
            .all()
        )
        for fired in candidates:
            window_end = _normalise(fired.retraction_window_ends_at)
            if window_end is None or window_end < now:
                # Window expired without a retraction — mark
                # 'handled' with no action so the row exits the
                # scan set on subsequent ticks.
                fired.retraction_status = "handled"
                fired.retraction_action_taken = "window_expired"
                db.flush()
                continue

            result.candidates_seen += 1
            classification = _latest_retraction_classification(
                db,
                spec_id=fired.event_spec_id,
                after=_normalise(fired.fired_at) or now,
            )
            if classification is None:
                continue

            spec = (
                db.query(NewsEventSpec)
                .filter(NewsEventSpec.id == fired.event_spec_id)
                .first()
            )
            if spec is None:
                logger.warning(
                    "[news_events.retraction] orphan fired event id=%s "
                    "spec_id=%s missing",
                    fired.id, fired.event_spec_id,
                )
                continue
            handle_one_retraction(
                db,
                fired=fired,
                spec=spec,
                classification=classification,
                result=result,
            )
        db.commit()
    finally:
        if owns_session:
            db.close()

    logger.info(
        "[news_events.retraction] tick candidates=%d detected=%d "
        "cancelled=%d alerts=%d actions=%s",
        result.candidates_seen,
        result.retractions_detected,
        result.approvals_cancelled,
        result.alerts_emitted,
        result.actions,
    )
    return result
