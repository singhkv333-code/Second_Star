"""Tests for the workflow CRUD + state-transition endpoints
(API_CONTRACT.md §5).

Covers:
  - POST happy path returns full Workflow shape (§3) with status='draft'
  - POST rejects unknown step_type with 422 + canonical envelope
  - POST rejects step_index=0 that isn't a trigger.* with 422
  - POST rejects subsequent triggers (only step 0 may be a trigger)
  - POST rejects bad config with 422 + details.step_index
  - GET /api/workflows omits steps[] (list-view shape, §5.2)
  - GET /api/workflows/{id} returns full shape
  - PATCH bumps version when steps changes
  - PATCH 409 when status='active' (caller must pause first)
  - activate/pause/archive happy paths + state guards
  - cross-user access returns 404 (never 403, never 200, §1)
"""
from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient


def _new_user(client: TestClient) -> dict[str, str]:
    """Register a fresh user and return Authorization headers.

    Mirrors tests/conftest.py:auth_headers but available as a helper
    so tests that need TWO users can call it twice. Uses `pivot.com`
    rather than `pivot.test` to satisfy the strict EmailStr validator.
    """
    import uuid
    email = f"u_{uuid.uuid4().hex[:8]}@pivot.com"
    r = client.post(
        "/auth/register",
        json={"email": email, "password": "password123", "full_name": "T"},
    )
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _basic_workflow_body() -> dict[str, Any]:
    return {
        "name": "QQQ buy close",
        "description": "demo flow",
        "single_instance": True,
        "steps": [
            {
                "step_type": "trigger.manual",
                "label": "Manual",
                "config": {},
            },
            {
                "step_type": "fetch.portfolio",
                "label": "Get portfolio",
                "config": {},
            },
            {
                "step_type": "condition.numeric",
                "label": "Buying power > 50K",
                "config": {
                    "left": "{{ context.1.buying_power }}",
                    "operator": ">",
                    "right": 50000,
                },
            },
            {
                "step_type": "action.place_order",
                "label": "Place order",
                "config": {
                    "symbol": "RELIANCE",
                    "side": "buy",
                    "quantity": 10,
                    "order_type": "market",
                    "requires_approval": False,
                },
            },
            {
                "step_type": "notify.message",
                "label": "Notify",
                "config": {
                    # Pivot v1 only wires the 'push' channel — see the
                    # rationale in backend/workflows/schemas.py
                    # NotifyMessageConfig. The previous fixture passed
                    # 'email' which was a stale carry-over from the
                    # pre-restriction schema.
                    "channel": "push",
                    "template": "Order placed",
                    "vars": {},
                },
            },
        ],
    }


# ── POST happy path ───────────────────────────────────────────────────


def test_create_workflow_happy_path(
    client: TestClient, auth_headers: dict[str, str],
) -> None:
    r = client.post(
        "/api/workflows",
        json=_basic_workflow_body(),
        headers=auth_headers,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "draft"
    assert body["version"] == 1
    assert body["single_instance"] is True
    assert len(body["steps"]) == 5
    assert body["steps"][0]["step_index"] == 0
    assert body["steps"][0]["step_type"] == "trigger.manual"
    # Server-assigned ids
    assert body["id"]
    assert all(s["id"] for s in body["steps"])


# ── POST validation ──────────────────────────────────────────────────


def test_create_unknown_step_type_returns_422(
    client: TestClient, auth_headers: dict[str, str],
) -> None:
    body = _basic_workflow_body()
    body["steps"][1]["step_type"] = "fetch.does_not_exist"
    r = client.post("/api/workflows", json=body, headers=auth_headers)
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "validation_error"
    assert err["details"]["step_index"] == 1
    assert err["details"]["field"] == "step_type"


def test_create_step_0_must_be_trigger(
    client: TestClient, auth_headers: dict[str, str],
) -> None:
    body = _basic_workflow_body()
    # Replace step 0 with a fetch — illegal at index 0.
    body["steps"][0] = {"step_type": "fetch.portfolio", "config": {}}
    r = client.post("/api/workflows", json=body, headers=auth_headers)
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["details"]["step_index"] == 0
    assert err["details"]["reason"] == "step_0_must_be_trigger"


def test_create_trigger_after_step_0_accepted_as_branch(
    client: TestClient, auth_headers: dict[str, str],
) -> None:
    """Multi-trigger: trigger.* at a later index opens a new branch.
    The API used to reject this with a 422 (single-trigger invariant);
    it now accepts as long as the trigger doesn't sit immediately
    after another trigger (empty branch)."""
    body = _basic_workflow_body()
    # Replace step 2 with a trigger and add another action after it,
    # so both branches have at least one action / step in them.
    body["steps"][2] = {"step_type": "trigger.manual", "config": {}}
    body["steps"].append({
        "step_type": "notify.log",
        "config": {"message": "branch B"},
    })
    r = client.post("/api/workflows", json=body, headers=auth_headers)
    assert r.status_code == 201, r.text


def test_create_two_adjacent_triggers_rejected(
    client: TestClient, auth_headers: dict[str, str],
) -> None:
    """Two trigger.* steps in a row leaves the first branch empty."""
    body = _basic_workflow_body()
    body["steps"][1] = {"step_type": "trigger.manual", "config": {}}
    r = client.post("/api/workflows", json=body, headers=auth_headers)
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["details"]["reason"] == "empty_branch"


def test_create_invalid_config_returns_422_with_step_index(
    client: TestClient, auth_headers: dict[str, str],
) -> None:
    body = _basic_workflow_body()
    # condition.numeric requires `right`; remove it.
    body["steps"][2]["config"] = {"left": 1, "operator": ">"}
    r = client.post("/api/workflows", json=body, headers=auth_headers)
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "validation_error"
    assert err["details"]["step_index"] == 2


# ── List + Get ────────────────────────────────────────────────────────


def test_list_omits_steps(
    client: TestClient, auth_headers: dict[str, str],
) -> None:
    r = client.post(
        "/api/workflows", json=_basic_workflow_body(), headers=auth_headers,
    )
    assert r.status_code == 201

    r = client.get("/api/workflows", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert "items" in body
    assert "next_cursor" in body
    assert len(body["items"]) >= 1
    assert "steps" not in body["items"][0]


def test_get_workflow_returns_full_shape(
    client: TestClient, auth_headers: dict[str, str],
) -> None:
    r = client.post(
        "/api/workflows", json=_basic_workflow_body(), headers=auth_headers,
    )
    wf_id = r.json()["id"]
    r = client.get(f"/api/workflows/{wf_id}", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert len(body["steps"]) == 5
    assert body["id"] == wf_id


def test_get_unknown_workflow_returns_404(
    client: TestClient, auth_headers: dict[str, str],
) -> None:
    r = client.get(
        "/api/workflows/00000000-0000-0000-0000-000000000000",
        headers=auth_headers,
    )
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"


# ── Cross-user → 404 (never 403) ─────────────────────────────────────


def test_cross_user_access_returns_404(
    client: TestClient, auth_headers: dict[str, str],
) -> None:
    # Create as user A.
    r = client.post(
        "/api/workflows", json=_basic_workflow_body(), headers=auth_headers,
    )
    wf_id = r.json()["id"]

    other = _new_user(client)
    r = client.get(f"/api/workflows/{wf_id}", headers=other)
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"

    r = client.patch(
        f"/api/workflows/{wf_id}",
        json={"name": "hijacked"},
        headers=other,
    )
    assert r.status_code == 404


# ── PATCH ─────────────────────────────────────────────────────────────


def test_patch_steps_bumps_version(
    client: TestClient, auth_headers: dict[str, str],
) -> None:
    r = client.post(
        "/api/workflows", json=_basic_workflow_body(), headers=auth_headers,
    )
    wf_id = r.json()["id"]
    assert r.json()["version"] == 1

    body = _basic_workflow_body()
    body["steps"][3]["config"]["quantity"] = 5  # change order qty
    r = client.patch(
        f"/api/workflows/{wf_id}",
        json={"steps": body["steps"]},
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert r.json()["version"] == 2


def test_patch_name_only_does_not_bump_version(
    client: TestClient, auth_headers: dict[str, str],
) -> None:
    r = client.post(
        "/api/workflows", json=_basic_workflow_body(), headers=auth_headers,
    )
    wf_id = r.json()["id"]
    r = client.patch(
        f"/api/workflows/{wf_id}",
        json={"name": "renamed"},
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert r.json()["name"] == "renamed"
    assert r.json()["version"] == 1


def test_patch_active_returns_409(
    client: TestClient, auth_headers: dict[str, str],
) -> None:
    r = client.post(
        "/api/workflows", json=_basic_workflow_body(), headers=auth_headers,
    )
    wf_id = r.json()["id"]
    r = client.post(
        f"/api/workflows/{wf_id}/activate", headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    r = client.patch(
        f"/api/workflows/{wf_id}",
        json={"name": "should fail"},
        headers=auth_headers,
    )
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "state_conflict"


# ── State transitions ────────────────────────────────────────────────


def test_activate_then_pause_then_activate(
    client: TestClient, auth_headers: dict[str, str],
) -> None:
    r = client.post(
        "/api/workflows", json=_basic_workflow_body(), headers=auth_headers,
    )
    wf_id = r.json()["id"]
    r = client.post(
        f"/api/workflows/{wf_id}/activate", headers=auth_headers,
    )
    assert r.status_code == 200
    assert r.json()["status"] == "active"
    assert r.json()["activated_at"]

    # Activating again → 409.
    r = client.post(
        f"/api/workflows/{wf_id}/activate", headers=auth_headers,
    )
    assert r.status_code == 409

    r = client.post(f"/api/workflows/{wf_id}/pause", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["status"] == "paused"

    r = client.post(
        f"/api/workflows/{wf_id}/activate", headers=auth_headers,
    )
    assert r.status_code == 200
    assert r.json()["status"] == "active"


def test_archive_blocks_activate(
    client: TestClient, auth_headers: dict[str, str],
) -> None:
    r = client.post(
        "/api/workflows", json=_basic_workflow_body(), headers=auth_headers,
    )
    wf_id = r.json()["id"]
    r = client.post(f"/api/workflows/{wf_id}/archive", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["status"] == "archived"

    r = client.post(
        f"/api/workflows/{wf_id}/activate", headers=auth_headers,
    )
    assert r.status_code == 409


# ── Manual run ────────────────────────────────────────────────────────


def test_post_run_returns_201_with_run_id(
    client: TestClient, auth_headers: dict[str, str],
) -> None:
    r = client.post(
        "/api/workflows", json=_basic_workflow_body(), headers=auth_headers,
    )
    wf_id = r.json()["id"]
    r = client.post(f"/api/workflows/{wf_id}/run", headers=auth_headers)
    assert r.status_code == 201, r.text
    assert "run_id" in r.json()
    assert r.json()["run_id"]


def test_post_run_archived_returns_409(
    client: TestClient, auth_headers: dict[str, str],
) -> None:
    r = client.post(
        "/api/workflows", json=_basic_workflow_body(), headers=auth_headers,
    )
    wf_id = r.json()["id"]
    r = client.post(f"/api/workflows/{wf_id}/archive", headers=auth_headers)
    r = client.post(f"/api/workflows/{wf_id}/run", headers=auth_headers)
    assert r.status_code == 409
