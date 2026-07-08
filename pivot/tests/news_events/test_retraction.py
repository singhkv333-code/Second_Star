"""Phase-6 retraction detection + handler tests.

Seeds fired-event rows + a follow-up RETRACTION classification, then
runs ``scan_for_retractions`` / ``handle_one_retraction`` and checks
the WorkflowApproval cancellation + audit columns.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

import pytest

from backend.models import (
    RunStatus,
    Workflow,
    WorkflowApproval,
    WorkflowRun,
    WorkflowStatus,
)
from backend.news_events.models import (
    NewsArticle,
    NewsArticleClassification,
    NewsEventSpec,
    NewsFiredEvent,
)
from backend.news_events.pipeline import retraction as retr


def _seed_workflow_and_run(
    db, *, user_id=1, with_approval=True
) -> tuple[Workflow, WorkflowRun, WorkflowApproval | None]:
    wf = Workflow(user_id=user_id, name="WF", status=WorkflowStatus.active)
    db.add(wf); db.flush()
    run = WorkflowRun(
        workflow_id=wf.id,
        workflow_version=1,
        triggered_by="event_alert",
        triggered_step_index=0,
        status=RunStatus.running,
    )
    db.add(run); db.flush()
    ap: WorkflowApproval | None = None
    if with_approval:
        ap = WorkflowApproval(
            run_id=run.id,
            step_index=0,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            summary="Approve order placement?",
        )
        db.add(ap); db.flush()
    return wf, run, ap


def _seed_fired(
    db,
    *,
    spec_id: str,
    workflow_run_id: str | None,
    safety_window_minutes: int = 120,
    fired_at: datetime | None = None,
) -> NewsFiredEvent:
    fired_at = fired_at or datetime.now(timezone.utc)
    fired = NewsFiredEvent(
        event_spec_id=spec_id,
        workflow_run_id=workflow_run_id,
        fired_at=fired_at,
        tier="tier1",
        aggregated_confidence=0.93,
        supporting_classification_ids=[],
        retraction_window_ends_at=fired_at + timedelta(minutes=safety_window_minutes),
        retraction_status="none",
    )
    db.add(fired); db.flush()
    return fired


def _seed_spec(
    db, *, action: str = "cancel_and_alert", state: str = "fired"
) -> NewsEventSpec:
    spec = NewsEventSpec(
        user_id=1,
        tier="tier1",
        description="RBI cuts repo rate",
        resolution_criteria={"primary_sources": ["rbi_press_releases"]},
        retraction_policy={
            "safety_window_minutes": 120,
            "action": action,
        },
        keyword_set={"must_have_one": ["RBI"], "must_have_one_of": [], "must_not_have": []},
        state=state,
    )
    db.add(spec); db.flush()
    return spec


_n = 0


def _seed_retraction_classification(
    db, *, spec_id: str, after: datetime
) -> NewsArticleClassification:
    """Insert an article + a RETRACTION classification dated AFTER the
    given fired_at instant."""
    global _n
    _n += 1
    art = NewsArticle(
        source_id="rbi_press_releases",
        url=f"https://example.test/r/{_n}",
        url_hash=f"ur_{_n}",
        title=f"Retraction {_n}",
        title_hash=hashlib.sha256(f"r{_n}".encode()).hexdigest(),
        summary=None,
    )
    db.add(art); db.flush()
    cls = NewsArticleClassification(
        article_id=art.id,
        event_spec_id=spec_id,
        stage_2_passed=True,
        classifier_verdict="RETRACTION",
        confidence=0.9,
        excerpt="Earlier confirmation was withdrawn.",
        model="fake",
    )
    db.add(cls); db.flush()
    # Manually backdate / forward-date created_at to land AFTER the
    # fired event so the retraction.scan sees it.
    cls.created_at = after + timedelta(seconds=10)
    db.flush()
    return cls


# ── Direct handler test ──────────────────────────────────────────────


def test_handle_cancels_pending_approvals(db):
    spec = _seed_spec(db, action="cancel_pending_approvals")
    _, run, approval = _seed_workflow_and_run(db, with_approval=True)
    fired = _seed_fired(db, spec_id=spec.id, workflow_run_id=run.id)
    cls = _seed_retraction_classification(db, spec_id=spec.id, after=fired.fired_at)
    db.commit()

    action = retr.handle_one_retraction(
        db, fired=fired, spec=spec, classification=cls
    )
    db.commit()

    assert action == "cancel_pending_approvals"
    db.refresh(approval)
    assert approval.decision == "rejected"
    assert approval.decided_at is not None
    assert "[news_events retraction]" in (approval.summary or "")

    db.refresh(fired)
    assert fired.retraction_status == "handled"
    assert fired.retraction_classification_id == cls.id
    assert fired.retraction_detected_at is not None
    assert fired.retraction_action_taken == "cancel_pending_approvals"


def test_handle_cancel_and_alert_emits_warning(db, caplog):
    import logging
    spec = _seed_spec(db, action="cancel_and_alert")
    _, run, approval = _seed_workflow_and_run(db, with_approval=True)
    fired = _seed_fired(db, spec_id=spec.id, workflow_run_id=run.id)
    cls = _seed_retraction_classification(db, spec_id=spec.id, after=fired.fired_at)
    db.commit()

    with caplog.at_level(logging.WARNING, logger="backend.news_events.pipeline.retraction"):
        action = retr.handle_one_retraction(
            db, fired=fired, spec=spec, classification=cls
        )
    db.commit()

    assert action == "cancel_and_alert"
    assert any("cancel_and_alert" in r.message for r in caplog.records)


def test_handle_ignore_action_is_a_noop_on_approvals(db):
    spec = _seed_spec(db, action="ignore")
    _, run, approval = _seed_workflow_and_run(db, with_approval=True)
    fired = _seed_fired(db, spec_id=spec.id, workflow_run_id=run.id)
    cls = _seed_retraction_classification(db, spec_id=spec.id, after=fired.fired_at)
    db.commit()

    action = retr.handle_one_retraction(
        db, fired=fired, spec=spec, classification=cls
    )
    db.commit()

    assert action == "ignore"
    db.refresh(approval)
    assert approval.decision is None  # untouched

    db.refresh(fired)
    assert fired.retraction_status == "handled"
    assert fired.retraction_action_taken == "ignore"


def test_no_pending_approvals_records_explicit_action(db):
    spec = _seed_spec(db, action="cancel_pending_approvals")
    _, run, _ = _seed_workflow_and_run(db, with_approval=False)
    fired = _seed_fired(db, spec_id=spec.id, workflow_run_id=run.id)
    cls = _seed_retraction_classification(db, spec_id=spec.id, after=fired.fired_at)
    db.commit()

    action = retr.handle_one_retraction(
        db, fired=fired, spec=spec, classification=cls
    )
    assert action == "no_pending_approvals"
    db.refresh(fired)
    assert fired.retraction_action_taken == "no_pending_approvals"


def test_workflow_run_missing_records_explicit_action(db):
    spec = _seed_spec(db, action="cancel_pending_approvals")
    fired = _seed_fired(db, spec_id=spec.id, workflow_run_id=None)
    cls = _seed_retraction_classification(db, spec_id=spec.id, after=fired.fired_at)
    db.commit()

    action = retr.handle_one_retraction(
        db, fired=fired, spec=spec, classification=cls
    )
    db.refresh(fired)
    assert action == "workflow_run_missing"
    assert fired.retraction_action_taken == "workflow_run_missing"


# ── Scan integration ────────────────────────────────────────────────


def _session_factory(db):
    class _Wrapper:
        def __init__(self, s): self._s = s
        def close(self): pass
        def __getattr__(self, n): return getattr(self._s, n)
    return _Wrapper(db)


def test_scan_handles_open_window_with_retraction(db, monkeypatch):
    spec = _seed_spec(db, action="cancel_pending_approvals")
    _, run, approval = _seed_workflow_and_run(db, with_approval=True)
    fired = _seed_fired(db, spec_id=spec.id, workflow_run_id=run.id)
    _seed_retraction_classification(db, spec_id=spec.id, after=fired.fired_at)
    db.commit()

    # Replace SessionLocal so scan_for_retractions uses our test session.
    monkeypatch.setattr(retr, "SessionLocal", lambda: _session_factory(db))

    result = retr.scan_for_retractions()
    assert result.retractions_detected == 1
    assert result.approvals_cancelled == 1
    assert result.actions.get("cancel_pending_approvals") == 1

    db.refresh(fired)
    assert fired.retraction_status == "handled"


def test_scan_marks_expired_window_handled(db, monkeypatch):
    spec = _seed_spec(db, action="cancel_and_alert")
    fired_at = datetime.now(timezone.utc) - timedelta(hours=10)
    fired = NewsFiredEvent(
        event_spec_id=spec.id,
        workflow_run_id=None,
        fired_at=fired_at,
        tier="tier1",
        aggregated_confidence=0.9,
        supporting_classification_ids=[],
        retraction_window_ends_at=fired_at + timedelta(minutes=60),  # already past
        retraction_status="none",
    )
    db.add(fired); db.commit()

    monkeypatch.setattr(retr, "SessionLocal", lambda: _session_factory(db))
    result = retr.scan_for_retractions()
    # No retraction detected, but the expired row should be flipped
    # to 'handled' so it exits the scan set.
    assert result.retractions_detected == 0

    db.refresh(fired)
    assert fired.retraction_status == "handled"
    assert fired.retraction_action_taken == "window_expired"


def test_scan_no_retraction_yet_leaves_row_alone(db, monkeypatch):
    spec = _seed_spec(db, action="cancel_and_alert")
    _, run, _ = _seed_workflow_and_run(db, with_approval=True)
    fired = _seed_fired(db, spec_id=spec.id, workflow_run_id=run.id)
    db.commit()

    monkeypatch.setattr(retr, "SessionLocal", lambda: _session_factory(db))
    result = retr.scan_for_retractions()
    assert result.candidates_seen >= 1
    assert result.retractions_detected == 0
    db.refresh(fired)
    assert fired.retraction_status == "none"  # untouched
