"""Tests for the run + approval endpoints (API_CONTRACT.md §6, §7).

Covers:
  - GET /api/workflows/{id}/runs paginated, list-view (no context/steps),
    derived `step_count` matches workflow_steps count.
  - GET /api/runs/{id} returns full Run shape (§4).
  - POST /api/runs/{id}/cancel sets cancel flag and returns the row.
  - cross-user → 404 on every run/approval endpoint.
  - GET /api/runs/{id}/approvals/pending only returns undecided.
  - POST /api/approvals/{id}/decision approve resumes; reject
    terminates the run as cancelled.
  - 409 when an approval is already decided / expired.
"""
from __future__ import annotations

import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend import models


def _basic_workflow_body() -> dict[str, Any]:
    return {
        "name": "Run test",
        "description": "",
        "single_instance": True,
        "steps": [
            {"step_type": "trigger.manual", "config": {}},
            {"step_type": "fetch.portfolio", "config": {}},
            {"step_type": "condition.numeric", "config": {
                "left": "{{ context.1.buying_power }}",
                "operator": ">",
                "right": 0,
            }},
            {"step_type": "notify.log", "config": {"message": "done"}},
        ],
    }


def _approval_workflow_body() -> dict[str, Any]:
    return {
        "name": "Approval test",
        "description": "",
        "single_instance": True,
        "steps": [
            {"step_type": "trigger.manual", "config": {}},
            {"step_type": "action.place_order", "config": {
                "symbol": "INFY", "side": "buy", "quantity": 5,
                "order_type": "market", "requires_approval": True,
            }},
        ],
    }


def _wait_for_run_finish(
    client: TestClient, headers: dict[str, str], run_id: str,
    *, timeout: float = 5.0,
) -> dict[str, Any]:
    """Poll GET /api/runs/{id} until status is terminal or
    awaiting_approval. The engine runs in a background task so we
    can't be synchronous about it."""
    deadline = time.time() + timeout
    last: dict[str, Any] = {}
    while time.time() < deadline:
        r = client.get(f"/api/runs/{run_id}", headers=headers)
        assert r.status_code == 200, r.text
        last = r.json()
        if last["status"] in (
            "succeeded", "failed", "cancelled", "awaiting_approval",
        ):
            return last
        time.sleep(0.05)
    return last


# ── List runs ────────────────────────────────────────────────────────


def test_list_runs_returns_step_count_and_omits_context(
    client: TestClient, auth_headers: dict[str, str],
) -> None:
    r = client.post(
        "/api/workflows", json=_basic_workflow_body(), headers=auth_headers,
    )
    wf_id = r.json()["id"]
    r = client.post(f"/api/workflows/{wf_id}/run", headers=auth_headers)
    run_id = r.json()["run_id"]
    _wait_for_run_finish(client, auth_headers, run_id)

    r = client.get(
        f"/api/workflows/{wf_id}/runs", headers=auth_headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert "items" in body
    assert "next_cursor" in body
    assert len(body["items"]) >= 1
    item = body["items"][0]
    # `context` and `steps[]` are NOT in the list view per §6.1.
    assert "context" not in item
    assert "steps" not in item
    # `step_count` IS, per the new RunSummary type.
    assert item["step_count"] == 4


def test_list_runs_cross_user_returns_404(
    client: TestClient, auth_headers: dict[str, str],
) -> None:
    r = client.post(
        "/api/workflows", json=_basic_workflow_body(), headers=auth_headers,
    )
    wf_id = r.json()["id"]

    other_email = f"u_{uuid.uuid4().hex[:8]}@pivot.com"
    r2 = client.post(
        "/auth/register",
        json={"email": other_email, "password": "password123",
              "full_name": "Other"},
    )
    other_headers = {"Authorization": f"Bearer {r2.json()['access_token']}"}
    r = client.get(f"/api/workflows/{wf_id}/runs", headers=other_headers)
    assert r.status_code == 404


# ── Get one run ──────────────────────────────────────────────────────


def test_get_run_returns_full_shape(
    client: TestClient, auth_headers: dict[str, str],
) -> None:
    r = client.post(
        "/api/workflows", json=_basic_workflow_body(), headers=auth_headers,
    )
    wf_id = r.json()["id"]
    r = client.post(f"/api/workflows/{wf_id}/run", headers=auth_headers)
    run_id = r.json()["run_id"]
    final = _wait_for_run_finish(client, auth_headers, run_id)

    assert final["status"] in ("succeeded",)
    assert "context" in final
    assert "steps" in final
    assert len(final["steps"]) == 4
    # context bag has the portfolio fetch output.
    assert "1" in final["context"]


def test_get_run_unknown_returns_404(
    client: TestClient, auth_headers: dict[str, str],
) -> None:
    r = client.get(
        "/api/runs/00000000-0000-0000-0000-000000000000",
        headers=auth_headers,
    )
    assert r.status_code == 404


# ── Cancel ───────────────────────────────────────────────────────────


def test_cancel_terminal_run_is_idempotent(
    client: TestClient, auth_headers: dict[str, str],
) -> None:
    r = client.post(
        "/api/workflows", json=_basic_workflow_body(), headers=auth_headers,
    )
    wf_id = r.json()["id"]
    r = client.post(f"/api/workflows/{wf_id}/run", headers=auth_headers)
    run_id = r.json()["run_id"]
    final = _wait_for_run_finish(client, auth_headers, run_id)
    assert final["status"] == "succeeded"

    r = client.post(f"/api/runs/{run_id}/cancel", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    # Already terminal; cancel is a no-op.
    assert body["status"] == "succeeded"


# ── Approvals: list pending + decide ─────────────────────────────────


def test_approval_list_pending_then_approve_resumes_run(
    client: TestClient, auth_headers: dict[str, str],
) -> None:
    r = client.post(
        "/api/workflows", json=_approval_workflow_body(),
        headers=auth_headers,
    )
    wf_id = r.json()["id"]
    r = client.post(f"/api/workflows/{wf_id}/run", headers=auth_headers)
    run_id = r.json()["run_id"]
    final = _wait_for_run_finish(client, auth_headers, run_id)
    assert final["status"] == "awaiting_approval"

    r = client.get(
        f"/api/runs/{run_id}/approvals/pending", headers=auth_headers,
    )
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 1
    appr_id = items[0]["id"]
    assert items[0]["step_index"] == 1
    assert items[0]["summary"].startswith("BUY 5 INFY")

    r = client.post(
        f"/api/approvals/{appr_id}/decision",
        json={"decision": "approved"},
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert r.json()["decision"] == "approved"
    assert r.json()["decided_at"]

    final = _wait_for_run_finish(client, auth_headers, run_id)
    assert final["status"] == "succeeded", final


def test_approval_reject_terminates_run_as_cancelled(
    client: TestClient, auth_headers: dict[str, str],
) -> None:
    r = client.post(
        "/api/workflows", json=_approval_workflow_body(),
        headers=auth_headers,
    )
    wf_id = r.json()["id"]
    r = client.post(f"/api/workflows/{wf_id}/run", headers=auth_headers)
    run_id = r.json()["run_id"]
    _wait_for_run_finish(client, auth_headers, run_id)

    r = client.get(
        f"/api/runs/{run_id}/approvals/pending", headers=auth_headers,
    )
    appr_id = r.json()["items"][0]["id"]

    r = client.post(
        f"/api/approvals/{appr_id}/decision",
        json={"decision": "rejected"},
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert r.json()["decision"] == "rejected"

    r = client.get(f"/api/runs/{run_id}", headers=auth_headers)
    assert r.status_code == 200
    final = r.json()
    assert final["status"] == "cancelled"
    assert "approval rejected at step 1" in (final.get("error_message") or "")


def test_approval_decide_twice_returns_409(
    client: TestClient, auth_headers: dict[str, str],
) -> None:
    r = client.post(
        "/api/workflows", json=_approval_workflow_body(),
        headers=auth_headers,
    )
    wf_id = r.json()["id"]
    r = client.post(f"/api/workflows/{wf_id}/run", headers=auth_headers)
    run_id = r.json()["run_id"]
    _wait_for_run_finish(client, auth_headers, run_id)

    r = client.get(
        f"/api/runs/{run_id}/approvals/pending", headers=auth_headers,
    )
    appr_id = r.json()["items"][0]["id"]
    r = client.post(
        f"/api/approvals/{appr_id}/decision",
        json={"decision": "approved"},
        headers=auth_headers,
    )
    assert r.status_code == 200

    r = client.post(
        f"/api/approvals/{appr_id}/decision",
        json={"decision": "approved"},
        headers=auth_headers,
    )
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "state_conflict"


def test_approval_expired_returns_409_with_expired_reason(
    client: TestClient, auth_headers: dict[str, str], db: Session,
) -> None:
    """If the approval row's expires_at is in the past, decision
    rejected with code=state_conflict + details.reason='expired'."""
    r = client.post(
        "/api/workflows", json=_approval_workflow_body(),
        headers=auth_headers,
    )
    wf_id = r.json()["id"]
    r = client.post(f"/api/workflows/{wf_id}/run", headers=auth_headers)
    run_id = r.json()["run_id"]
    _wait_for_run_finish(client, auth_headers, run_id)

    r = client.get(
        f"/api/runs/{run_id}/approvals/pending", headers=auth_headers,
    )
    appr_id = r.json()["items"][0]["id"]

    # Force expires_at into the past.
    appr = (
        db.query(models.WorkflowApproval).filter_by(id=appr_id).one()
    )
    appr.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.commit()

    r = client.post(
        f"/api/approvals/{appr_id}/decision",
        json={"decision": "approved"},
        headers=auth_headers,
    )
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "state_conflict"
    assert r.json()["error"]["details"]["reason"] == "expired"


def test_pending_approvals_cross_user_404(
    client: TestClient, auth_headers: dict[str, str],
) -> None:
    r = client.post(
        "/api/workflows", json=_approval_workflow_body(),
        headers=auth_headers,
    )
    wf_id = r.json()["id"]
    r = client.post(f"/api/workflows/{wf_id}/run", headers=auth_headers)
    run_id = r.json()["run_id"]
    _wait_for_run_finish(client, auth_headers, run_id)

    other_email = f"u_{uuid.uuid4().hex[:8]}@pivot.com"
    r2 = client.post(
        "/auth/register",
        json={"email": other_email, "password": "password123",
              "full_name": "Other"},
    )
    other_headers = {"Authorization": f"Bearer {r2.json()['access_token']}"}
    r = client.get(
        f"/api/runs/{run_id}/approvals/pending",
        headers=other_headers,
    )
    assert r.status_code == 404
