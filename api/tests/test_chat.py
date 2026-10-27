"""Tests for /chat endpoint (uses mock AI clients when keys are empty)."""


def test_chat_basic(client, auth_headers):
    r = client.post(
        "/chat",
        json={
            "messages": [{"role": "user", "content": "Hello"}],
            "include_portfolio_context": False,
        },
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "response" in body
    assert "intent" in body


def test_chat_requires_auth(client):
    r = client.post(
        "/chat",
        json={
            "messages": [{"role": "user", "content": "Hello"}],
            "include_portfolio_context": False,
        },
    )
    assert r.status_code == 401
