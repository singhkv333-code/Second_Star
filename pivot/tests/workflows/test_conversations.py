"""Tests for /api/conversations endpoints (#47).

Persisted chat conversations + messages backing the redesigned chat
sidebar. Covers list/create/get/append/rename/delete + ownership 404
masking + pagination + auto-title.
"""
from __future__ import annotations

import time

from fastapi.testclient import TestClient


def test_list_unauth(client: TestClient) -> None:
    r = client.get("/api/conversations")
    assert r.status_code == 401


def test_create_and_list(
    client: TestClient, auth_headers: dict[str, str],
) -> None:
    r = client.post(
        "/api/conversations",
        headers=auth_headers,
        json={"title": "Test convo"},
    )
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    assert r.json()["title"] == "Test convo"

    listing = client.get("/api/conversations", headers=auth_headers)
    assert listing.status_code == 200
    items = listing.json()["items"]
    assert len(items) == 1
    assert items[0]["id"] == cid
    assert items[0]["title"] == "Test convo"
    assert items[0]["message_count"] == 0
    assert items[0]["preview"] is None


def test_create_without_title_then_auto_titles_from_first_user_msg(
    client: TestClient, auth_headers: dict[str, str],
) -> None:
    cid = client.post(
        "/api/conversations", headers=auth_headers, json={},
    ).json()["id"]

    msg = client.post(
        f"/api/conversations/{cid}/messages",
        headers=auth_headers,
        json={
            "role": "user",
            "content": "Buy 10 shares of RELIANCE next Tuesday at 3:55pm",
        },
    )
    assert msg.status_code == 201, msg.text

    detail = client.get(
        f"/api/conversations/{cid}", headers=auth_headers,
    ).json()
    # First user message → auto-title (truncated to 60 chars).
    assert detail["title"] is not None
    assert detail["title"].startswith("Buy 10 shares of RELIANCE")
    assert len(detail["messages"]) == 1


def test_append_message_assistant_does_not_overwrite_explicit_title(
    client: TestClient, auth_headers: dict[str, str],
) -> None:
    cid = client.post(
        "/api/conversations",
        headers=auth_headers,
        json={"title": "My RELIANCE plan"},
    ).json()["id"]

    # First message is assistant — should NOT change the explicit title.
    client.post(
        f"/api/conversations/{cid}/messages",
        headers=auth_headers,
        json={"role": "assistant", "content": "Sure, let's set that up"},
    )
    r = client.get(f"/api/conversations/{cid}", headers=auth_headers)
    assert r.json()["title"] == "My RELIANCE plan"


def test_append_invalid_role_returns_422(
    client: TestClient, auth_headers: dict[str, str],
) -> None:
    cid = client.post(
        "/api/conversations", headers=auth_headers, json={},
    ).json()["id"]

    bad = client.post(
        f"/api/conversations/{cid}/messages",
        headers=auth_headers,
        json={"role": "robot", "content": "hi"},
    )
    assert bad.status_code == 422
    assert bad.json()["error"]["code"] == "validation_error"


def test_messages_pagination_with_before(
    client: TestClient, auth_headers: dict[str, str],
) -> None:
    cid = client.post(
        "/api/conversations", headers=auth_headers, json={},
    ).json()["id"]
    for i in range(3):
        client.post(
            f"/api/conversations/{cid}/messages",
            headers=auth_headers,
            json={"role": "user", "content": f"msg {i}"},
        )
        time.sleep(0.01)  # ensure distinct created_at ordering

    all_msgs = client.get(
        f"/api/conversations/{cid}/messages", headers=auth_headers,
    ).json()["items"]
    assert len(all_msgs) == 3
    cutoff = all_msgs[2]["created_at"]
    sliced = client.get(
        f"/api/conversations/{cid}/messages",
        headers=auth_headers,
        params={"before": cutoff},
    )
    assert sliced.status_code == 200
    assert len(sliced.json()["items"]) == 2


def test_get_other_users_conversation_returns_404(
    client: TestClient, auth_headers: dict[str, str],
) -> None:
    """Ownership check returns 404 (not 403) so we don't leak existence."""
    cid = client.post(
        "/api/conversations", headers=auth_headers, json={"title": "mine"},
    ).json()["id"]

    # Register a second user.
    other = client.post(
        "/auth/register",
        json={
            "email": "other-u@pivot.com",
            "password": "password123",
            "full_name": "Other",
        },
    )
    other_token = other.json()["access_token"]
    other_headers = {"Authorization": f"Bearer {other_token}"}

    # Other user should NOT see user1's convo.
    r = client.get(f"/api/conversations/{cid}", headers=other_headers)
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"

    # Other user's listing is empty.
    listing = client.get("/api/conversations", headers=other_headers)
    assert listing.json()["items"] == []


def test_rename_conversation(
    client: TestClient, auth_headers: dict[str, str],
) -> None:
    cid = client.post(
        "/api/conversations",
        headers=auth_headers,
        json={"title": "old name"},
    ).json()["id"]
    r = client.patch(
        f"/api/conversations/{cid}",
        headers=auth_headers,
        json={"title": "new name"},
    )
    assert r.status_code == 200
    assert r.json()["title"] == "new name"


def test_rename_empty_returns_422(
    client: TestClient, auth_headers: dict[str, str],
) -> None:
    cid = client.post(
        "/api/conversations", headers=auth_headers, json={},
    ).json()["id"]
    r = client.patch(
        f"/api/conversations/{cid}",
        headers=auth_headers,
        json={"title": ""},
    )
    assert r.status_code == 422


def test_delete_cascades_messages(
    client: TestClient, auth_headers: dict[str, str],
) -> None:
    cid = client.post(
        "/api/conversations", headers=auth_headers, json={"title": "x"},
    ).json()["id"]
    client.post(
        f"/api/conversations/{cid}/messages",
        headers=auth_headers,
        json={"role": "user", "content": "hi"},
    )
    r = client.delete(f"/api/conversations/{cid}", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["deleted"] is True

    # Verify gone.
    r2 = client.get(f"/api/conversations/{cid}", headers=auth_headers)
    assert r2.status_code == 404


def test_listing_orders_by_recent_activity(
    client: TestClient, auth_headers: dict[str, str],
) -> None:
    """Conversations with newer last_message_at should sort first."""
    a = client.post(
        "/api/conversations", headers=auth_headers, json={"title": "A"},
    ).json()["id"]
    b = client.post(
        "/api/conversations", headers=auth_headers, json={"title": "B"},
    ).json()["id"]
    # Append to A AFTER B was created → A should sort first.
    time.sleep(0.01)
    client.post(
        f"/api/conversations/{a}/messages",
        headers=auth_headers,
        json={"role": "user", "content": "wake up"},
    )

    items = client.get(
        "/api/conversations", headers=auth_headers,
    ).json()["items"]
    assert items[0]["title"] == "A"
    assert items[1]["title"] == "B"


def test_message_with_tool_payload_round_trips(
    client: TestClient, auth_headers: dict[str, str],
) -> None:
    cid = client.post(
        "/api/conversations", headers=auth_headers, json={},
    ).json()["id"]
    payload = {
        "tool": "propose_workflow",
        "draft_id": "d_abc",
        "steps": [{"step_type": "trigger.schedule", "config": {"cron": "* * * * *"}}],
    }
    r = client.post(
        f"/api/conversations/{cid}/messages",
        headers=auth_headers,
        json={"role": "tool", "content": "", "tool_payload": payload},
    )
    assert r.status_code == 201, r.text
    detail = client.get(
        f"/api/conversations/{cid}", headers=auth_headers,
    ).json()
    assert detail["messages"][0]["tool_payload"] == payload
