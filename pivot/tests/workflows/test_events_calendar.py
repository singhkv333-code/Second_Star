"""Tests for /api/events/calendar (#48).

Verifies the endpoint enumerates active workflows whose first step is
trigger.event and returns their canonical upcoming events from the
static 2026 macro calendar within the requested window.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.models import Workflow, WorkflowStatus, WorkflowStep


def _seed_event_workflow(
    db: Session, user_id: int, *, name: str, event_type: str,
    extra_filter: dict | None = None,
) -> str:
    """Insert one active workflow whose step 0 is trigger.event."""
    wf = Workflow(user_id=user_id, name=name, status=WorkflowStatus.active)
    db.add(wf)
    db.commit()
    db.refresh(wf)
    cfg = {"event_type": event_type, "filter": extra_filter or {}}
    step = WorkflowStep(
        workflow_id=wf.id, step_index=0, step_type="trigger.event", config=cfg,
    )
    db.add(step)
    db.commit()
    return str(wf.id)


def _user_id(client: TestClient, auth_headers: dict[str, str]) -> int:
    """Decode user_id from the auth header (handler-side)."""
    from backend.auth.jwt_handler import get_user_id_from_token
    token = auth_headers["Authorization"].replace("Bearer ", "")
    return int(get_user_id_from_token(token))


def test_events_calendar_unauth(client: TestClient) -> None:
    r = client.get("/api/events/calendar?from=2026-01-01&to=2026-12-31")
    assert r.status_code == 401


def test_events_calendar_to_before_from_returns_422(
    client: TestClient, auth_headers: dict[str, str],
) -> None:
    r = client.get(
        "/api/events/calendar",
        headers=auth_headers,
        params={"from": "2026-06-01", "to": "2026-05-01"},
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "validation_error"


def test_events_calendar_window_too_large_returns_422(
    client: TestClient, auth_headers: dict[str, str],
) -> None:
    r = client.get(
        "/api/events/calendar",
        headers=auth_headers,
        params={"from": "2026-01-01", "to": "2026-12-31"},
    )
    assert r.status_code == 422
    assert r.json()["error"]["details"]["reason"] == "window_too_large"


def test_events_calendar_no_workflows_returns_empty(
    client: TestClient, auth_headers: dict[str, str],
) -> None:
    r = client.get(
        "/api/events/calendar",
        headers=auth_headers,
        params={"from": "2026-01-01", "to": "2026-03-01"},
    )
    assert r.status_code == 200
    assert r.json()["items"] == []


def test_events_calendar_rbi_workflow_emits_mpc_dates(
    client: TestClient, auth_headers: dict[str, str], db: Session,
) -> None:
    """Active RBI MPC workflow → calendar surfaces the 2026-02 MPC date."""
    uid = _user_id(client, auth_headers)
    wf_id = _seed_event_workflow(
        db, uid, name="RBI rate watcher", event_type="rbi_rate_decision",
    )
    r = client.get(
        "/api/events/calendar",
        headers=auth_headers,
        params={"from": "2026-02-01", "to": "2026-02-28"},
    )
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert len(items) == 1
    assert items[0]["workflow_id"] == wf_id
    assert items[0]["event_type"] == "rbi_rate_decision"
    assert items[0]["label"] == "RBI MPC Outcome"
    assert items[0]["fire_time_local"].endswith("IST")


def test_events_calendar_results_workflow_emits_q4_window(
    client: TestClient, auth_headers: dict[str, str], db: Session,
) -> None:
    uid = _user_id(client, auth_headers)
    _seed_event_workflow(
        db, uid, name="Earnings watcher", event_type="company_results",
        extra_filter={"symbol": "RELIANCE"},
    )
    r = client.get(
        "/api/events/calendar",
        headers=auth_headers,
        params={"from": "2026-04-01", "to": "2026-04-30"},
    )
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 1
    assert items[0]["event_type"] == "company_results"
    assert "Q4 FY26" in items[0]["label"]


def test_events_calendar_fii_flow_emits_weekday_entries(
    client: TestClient, auth_headers: dict[str, str], db: Session,
) -> None:
    """fii_flow workflow → entries on every weekday in window."""
    uid = _user_id(client, auth_headers)
    _seed_event_workflow(
        db, uid, name="FII flow watcher", event_type="fii_flow",
    )
    # Mon 2026-05-04 → Fri 2026-05-08 = 5 weekdays.
    r = client.get(
        "/api/events/calendar",
        headers=auth_headers,
        params={"from": "2026-05-04T00:00:00Z", "to": "2026-05-08T23:59:59Z"},
    )
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 5
    for it in items:
        # No weekend entries.
        d = datetime.fromisoformat(it["fire_time"].replace("Z", "+00:00"))
        assert d.weekday() < 5


def test_events_calendar_skips_paused_workflows(
    client: TestClient, auth_headers: dict[str, str], db: Session,
) -> None:
    uid = _user_id(client, auth_headers)
    wf_id = _seed_event_workflow(
        db, uid, name="paused", event_type="rbi_rate_decision",
    )
    # Flip to paused — should disappear from results.
    db.query(Workflow).filter(Workflow.id == wf_id).update(
        {Workflow.status: WorkflowStatus.paused}
    )
    db.commit()
    r = client.get(
        "/api/events/calendar",
        headers=auth_headers,
        params={"from": "2026-01-01", "to": "2026-03-31"},
    )
    assert r.status_code == 200
    assert r.json()["items"] == []


def test_events_calendar_only_returns_own_workflows(
    client: TestClient, auth_headers: dict[str, str], db: Session,
) -> None:
    """Workflow owned by another user should not appear."""
    # Register a second user — events should not leak across users.
    other = client.post(
        "/auth/register",
        json={"email": "ev-other@pivot.com", "password": "password123"},
    )
    other_uid = other.json()["user_id"]
    _seed_event_workflow(
        db, other_uid, name="not yours", event_type="rbi_rate_decision",
    )

    r = client.get(
        "/api/events/calendar",
        headers=auth_headers,
        params={"from": "2026-01-01", "to": "2026-03-31"},
    )
    assert r.status_code == 200
    assert r.json()["items"] == []
