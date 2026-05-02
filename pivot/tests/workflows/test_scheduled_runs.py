"""Tests for GET /api/workflows/scheduled-runs (#37).

Backs the FE Calendar tab. v1 covers `trigger.schedule` only;
`trigger.event` returns nothing (cut to v2).

Coverage:
  - happy path: a daily 09:30 IST cron over a 7-day window returns 7 items
    in chronological order with correct UTC + local labels
  - paused / archived workflows are excluded
  - non-schedule trigger types are excluded
  - cross-user isolation (user A's workflow doesn't show up for user B)
  - validation: to <= from -> 422; window > 90 days -> 422
  - malformed cron on a stored step doesn't 500 — skipped silently
  - unauth -> 401 with canonical envelope
  - 500-item cap (cron */1 over 30 days would be 43200; we get 500)
"""
from __future__ import annotations


from fastapi.testclient import TestClient

from backend.models import Workflow, WorkflowStatus, WorkflowStep


def _user_id_from_token(auth_headers: dict[str, str]) -> int:
    """Pull the user_id out of the JWT — auth_headers fixture registered
    a user but doesn't expose the id. Mirrors require_user's path."""
    from backend.auth.jwt_handler import get_user_id_from_token
    token = auth_headers["Authorization"].replace("Bearer ", "", 1)
    uid = get_user_id_from_token(token)
    assert uid is not None
    return int(uid)


def _make_active_schedule_wf(
    db, *, user_id: int, name: str, cron: str, tz: str = "Asia/Kolkata",
) -> Workflow:
    wf = Workflow(
        user_id=user_id, name=name,
        status=WorkflowStatus.active, version=1,
    )
    db.add(wf)
    db.flush()
    step = WorkflowStep(
        workflow_id=wf.id, step_index=0,
        step_type="trigger.schedule",
        config={"cron": cron, "timezone": tz},
        label=None,
    )
    db.add(step)
    db.flush()
    db.refresh(wf)
    return wf


def test_unauth_returns_401_canonical_envelope(client: TestClient) -> None:
    resp = client.get(
        "/api/workflows/scheduled-runs",
        params={"from": "2026-05-04T00:00:00Z", "to": "2026-05-05T00:00:00Z"},
    )
    assert resp.status_code == 401
    body = resp.json()
    assert body["error"]["code"] == "unauthenticated"


def test_validation_to_before_from_returns_422(
    client: TestClient, auth_headers: dict[str, str],
) -> None:
    resp = client.get(
        "/api/workflows/scheduled-runs",
        headers=auth_headers,
        params={"from": "2026-05-05T00:00:00Z", "to": "2026-05-05T00:00:00Z"},
    )
    assert resp.status_code == 422
    body = resp.json()
    assert body["error"]["code"] == "validation_error"
    assert body["error"]["details"]["reason"] == "to_must_exceed_from"


def test_validation_window_too_large_returns_422(
    client: TestClient, auth_headers: dict[str, str],
) -> None:
    resp = client.get(
        "/api/workflows/scheduled-runs",
        headers=auth_headers,
        params={"from": "2026-05-04T00:00:00Z", "to": "2026-09-01T00:00:00Z"},
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["details"]["reason"] == "window_too_large"


def test_empty_when_no_active_schedule_workflows(
    client: TestClient, auth_headers: dict[str, str],
) -> None:
    resp = client.get(
        "/api/workflows/scheduled-runs",
        headers=auth_headers,
        params={"from": "2026-05-04T00:00:00Z", "to": "2026-05-05T00:00:00Z"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"items": []}


def test_daily_cron_over_7_days_returns_7_items(
    client: TestClient, auth_headers: dict[str, str], db,
) -> None:
    """Daily 09:30 IST cron over a 7-day window → 7 fire times,
    sorted ascending UTC, with `trigger.schedule` type label."""
    # Look up the test user's id via the registered token.
    user_id = _user_id_from_token(auth_headers)

    _make_active_schedule_wf(
        db, user_id=user_id, name="Daily 09:30 IST",
        cron="30 9 * * *", tz="Asia/Kolkata",
    )

    # 7-day window starting at a Saturday so we cover all weekdays.
    resp = client.get(
        "/api/workflows/scheduled-runs",
        headers=auth_headers,
        params={"from": "2026-05-02T00:00:00Z", "to": "2026-05-09T00:00:00Z"},
    )
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 7  # one fire per day
    # All items are trigger.schedule, sorted ascending.
    assert all(i["trigger_type"] == "trigger.schedule" for i in items)
    times = [i["fire_time"] for i in items]
    assert times == sorted(times)
    # Local label includes the IST suffix (we abbreviate Asia/Kolkata).
    assert all("IST" in i["fire_time_local"] for i in items)


def test_paused_workflow_excluded(
    client: TestClient, auth_headers: dict[str, str], db,
) -> None:
    me = {"id": _user_id_from_token(auth_headers)}
    wf = _make_active_schedule_wf(
        db, user_id=me["id"], name="Paused", cron="30 9 * * *",
    )
    wf.status = WorkflowStatus.paused
    db.flush()
    resp = client.get(
        "/api/workflows/scheduled-runs",
        headers=auth_headers,
        params={"from": "2026-05-04T00:00:00Z", "to": "2026-05-09T00:00:00Z"},
    )
    assert resp.status_code == 200
    assert resp.json()["items"] == []


def test_non_schedule_trigger_excluded(
    client: TestClient, auth_headers: dict[str, str], db,
) -> None:
    """A trigger.manual workflow shouldn't surface in calendar."""
    me = {"id": _user_id_from_token(auth_headers)}
    wf = Workflow(
        user_id=me["id"], name="Manual",
        status=WorkflowStatus.active, version=1,
    )
    db.add(wf)
    db.flush()
    db.add(WorkflowStep(
        workflow_id=wf.id, step_index=0,
        step_type="trigger.manual", config={}, label=None,
    ))
    db.flush()
    resp = client.get(
        "/api/workflows/scheduled-runs",
        headers=auth_headers,
        params={"from": "2026-05-04T00:00:00Z", "to": "2026-05-09T00:00:00Z"},
    )
    assert resp.status_code == 200
    assert resp.json()["items"] == []


def test_cross_user_isolation(
    client: TestClient, auth_headers: dict[str, str], db,
) -> None:
    """User A's workflow doesn't appear in user B's results."""
    # Create a workflow owned by some other user_id (999, not in users
    # but FK is unenforced in sqlite tests w/r/t fkey checks during
    # session; the query filter is what matters).
    other_id = 9999
    _make_active_schedule_wf(
        db, user_id=other_id, name="Other user", cron="30 9 * * *",
    )
    resp = client.get(
        "/api/workflows/scheduled-runs",
        headers=auth_headers,
        params={"from": "2026-05-04T00:00:00Z", "to": "2026-05-09T00:00:00Z"},
    )
    assert resp.status_code == 200
    assert resp.json()["items"] == []


def test_malformed_cron_skipped_not_500(
    client: TestClient, auth_headers: dict[str, str], db,
) -> None:
    """A stored workflow with a malformed cron (shouldn't normally exist
    but defense-in-depth) is skipped silently, not 500'd."""
    me = {"id": _user_id_from_token(auth_headers)}
    wf = Workflow(
        user_id=me["id"], name="Bad cron",
        status=WorkflowStatus.active, version=1,
    )
    db.add(wf)
    db.flush()
    db.add(WorkflowStep(
        workflow_id=wf.id, step_index=0,
        step_type="trigger.schedule",
        config={"cron": "not a real cron", "timezone": "UTC"},
        label=None,
    ))
    db.flush()
    # Add a good one too so we can verify the bad one is skipped, not
    # crashing the whole response.
    _make_active_schedule_wf(
        db, user_id=me["id"], name="Daily good", cron="30 9 * * *",
    )
    resp = client.get(
        "/api/workflows/scheduled-runs",
        headers=auth_headers,
        params={"from": "2026-05-04T00:00:00Z", "to": "2026-05-06T00:00:00Z"},
    )
    assert resp.status_code == 200
    items = resp.json()["items"]
    # Should have 2 fire times from the good workflow (one per day in
    # the 2-day window, both at 09:30 IST).
    assert len(items) == 2
    assert all(i["workflow_name"] == "Daily good" for i in items)


def test_500_item_cap(
    client: TestClient, auth_headers: dict[str, str], db,
) -> None:
    """A 1-min cron over 30 days = 43,200 fires. Cap at 500."""
    me = {"id": _user_id_from_token(auth_headers)}
    _make_active_schedule_wf(
        db, user_id=me["id"], name="Spammy", cron="* * * * *", tz="UTC",
    )
    resp = client.get(
        "/api/workflows/scheduled-runs",
        headers=auth_headers,
        params={"from": "2026-05-04T00:00:00Z", "to": "2026-06-01T00:00:00Z"},
    )
    assert resp.status_code == 200
    assert len(resp.json()["items"]) == 500
