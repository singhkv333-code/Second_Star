"""Router tests for the unified /brokers connection surface.

Replaces the old /kite router tests (that system was removed — there is now
ONE broker connection surface). Covers status / login_url / connect-mock /
disconnect / OAuth callback for Kite in mock mode, plus the broker list and the
unknown-broker 404.
"""


def test_status_unauthenticated(client):
    r = client.get("/brokers/kite/status")
    assert r.status_code == 401


def test_status_initial_disconnected(client, auth_headers):
    r = client.get("/brokers/kite/status", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["connected"] is False
    assert body["mock_mode"] is True
    assert body["broker_user_id"] is None


def test_list_brokers_includes_kite_and_dhan(client, auth_headers):
    r = client.get("/brokers", headers=auth_headers)
    assert r.status_code == 200
    brokers = r.json()["brokers"]
    by_id = {b["id"]: b for b in brokers}
    assert {"kite", "dhan"} <= set(by_id)
    # Dhan advertises the unattended, no-daily-login persistence.
    assert by_id["dhan"]["supports_unattended"] is True
    assert by_id["kite"]["supports_unattended"] is False
    # Deep links present for onboarding (redirect-first UX).
    assert by_id["dhan"]["deep_links"]


def test_unknown_broker_404(client, auth_headers):
    r = client.get("/brokers/nope/status", headers=auth_headers)
    assert r.status_code == 404


def test_login_url_returns_state_in_mock_mode(client, auth_headers):
    r = client.get("/brokers/kite/login_url", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["mock_mode"] is True
    assert body["login_url"] is None
    assert body["state"]


def test_connect_mock_persists_session(client, auth_headers):
    r = client.post("/brokers/kite/connect-mock", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["connected"] is True
    assert body["broker_user_id"] == "MOCK001"
    s = client.get("/brokers/kite/status", headers=auth_headers).json()
    assert s["connected"] is True


def test_disconnect_clears_session(client, auth_headers):
    client.post("/brokers/kite/connect-mock", headers=auth_headers)
    r = client.delete("/brokers/kite/session", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["connected"] is False
    s = client.get("/brokers/kite/status", headers=auth_headers).json()
    assert s["connected"] is False


def test_callback_invalid_state_redirects_with_error(client):
    r = client.get(
        "/brokers/kite/callback",
        params={"request_token": "reqtok", "state": "not-a-jwt"},
        follow_redirects=False,
    )
    assert r.status_code == 302
    assert "broker=error" in r.headers["location"]
    assert "invalid_state" in r.headers["location"]


def test_callback_with_valid_state_persists_and_redirects(client, auth_headers):
    state = client.get(
        "/brokers/kite/login_url", headers=auth_headers
    ).json()["state"]
    r = client.get(
        "/brokers/kite/callback",
        params={"request_token": "reqtok123", "state": state},
        follow_redirects=False,
    )
    assert r.status_code == 302
    assert "broker=connected" in r.headers["location"]
    s = client.get("/brokers/kite/status", headers=auth_headers).json()
    assert s["connected"] is True
