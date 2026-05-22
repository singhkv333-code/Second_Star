"""Tests for POST /api/propose-workflow (#38).

The chatbot tool `propose_workflow` is now also surfaced as a REST
endpoint so the frontend can demo the chat→draft flow without porting
the legacy chatbot. Same underlying code path (mock OR LLM, validate,
retry-once, fallback-to-mock-with-warning) — this just adds an HTTP
shell.
"""
from __future__ import annotations


from fastapi.testclient import TestClient


def test_unauth_returns_401(client: TestClient) -> None:
    resp = client.post(
        "/api/propose-workflow",
        json={"user_intent": "buy 10 INFY weekdays"},
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "unauthenticated"


def test_empty_intent_rejected_with_validation_error(
    client: TestClient, auth_headers: dict[str, str],
) -> None:
    resp = client.post(
        "/api/propose-workflow",
        headers=auth_headers,
        json={"user_intent": ""},
    )
    assert resp.status_code == 422
    body = resp.json()
    assert body["error"]["code"] == "validation_error"


def test_returns_draft_with_canonical_demo_prompt(
    client: TestClient, auth_headers: dict[str, str],
) -> None:
    """The canonical demo prompt → 5-step draft via the mock matcher
    (mock mode kicks in because tests run with empty SARVAM/OpenAI keys)."""
    resp = client.post(
        "/api/propose-workflow",
        headers=auth_headers,
        json={"user_intent": (
            "Every weekday at 3:55 PM IST, if my buying power is over "
            "rs 50,000, buy 10 shares of RELIANCE and notify me by email."
        )},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # Top-level shape
    assert "name" in body and len(body["name"]) > 0
    assert "steps" in body and isinstance(body["steps"], list)
    assert "warnings" in body and isinstance(body["warnings"], list)
    assert "rationale" in body

    # Canonical 5-step demo
    step_types = [s["step_type"] for s in body["steps"]]
    assert step_types == [
        "trigger.schedule",
        "fetch.portfolio",
        "condition.numeric",
        "action.place_order",
        "notify.message",
    ]

    # action.place_order is the demo's headline step
    place = body["steps"][3]
    assert place["config"]["symbol"] == "RELIANCE"
    assert place["config"]["quantity"] == 10
    assert place["config"]["side"] == "buy"
    assert place["config"]["requires_approval"] is True

    # notify.message — schema restricts channel to 'push' in v1
    # (NotifyMessageConfig in backend/workflows/schemas.py). The LLM
    # proposer either omits the channel and lets the default fill in,
    # or emits 'push' explicitly.
    notify = body["steps"][4]
    assert notify["config"]["channel"] == "push"
    assert "Bought" in notify["config"]["template"]


def test_simpler_prompt_produces_3_step_variant(
    client: TestClient, auth_headers: dict[str, str],
) -> None:
    """No 'if' condition + simpler intent → 3-step draft
    (trigger → action → notify)."""
    resp = client.post(
        "/api/propose-workflow",
        headers=auth_headers,
        json={"user_intent": "Every Monday at 9:30 sell 5 shares of QQQ and SMS me."},
    )
    assert resp.status_code == 200
    body = resp.json()
    step_types = [s["step_type"] for s in body["steps"]]
    assert step_types == [
        "trigger.schedule",
        "action.place_order",
        "notify.message",
    ]
    assert body["steps"][1]["config"]["side"] == "sell"
    # Same 'push' restriction as the canonical-demo test above — even when
    # the user asks for SMS, the schema forces push and the chat layer
    # is responsible for surfacing the channel-not-wired explanation.
    assert body["steps"][2]["config"]["channel"] == "push"


def test_response_is_validated_draft_shape(
    client: TestClient, auth_headers: dict[str, str],
) -> None:
    """Every returned step's config must be valid against the registry's
    Pydantic models — this is the same guarantee the chatbot tool provides."""
    resp = client.post(
        "/api/propose-workflow",
        headers=auth_headers,
        json={"user_intent": "Buy 1 RELIANCE on weekdays at 09:30."},
    )
    assert resp.status_code == 200
    body = resp.json()

    # Re-validate via the registry to make sure the endpoint's output
    # really is registry-valid (not just JSON-shaped).
    from backend.workflows.propose import validate_draft_against_registry
    validate_draft_against_registry({
        "name": body["name"],
        "description": body.get("description"),
        "rationale": body.get("rationale"),
        "warnings": body.get("warnings", []),
        "steps": body["steps"],
    })
