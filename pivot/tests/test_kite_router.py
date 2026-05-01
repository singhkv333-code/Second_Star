def test_status_unauthenticated(client):
    r = client.get("/kite/status")
    assert r.status_code == 401


def test_status_initial_disconnected(client, auth_headers):
    r = client.get("/kite/status", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["connected"] is False
    assert body["mock_mode"] is True
    assert body["kite_user_id"] is None


def test_login_url_returns_state_in_mock_mode(client, auth_headers):
    r = client.get("/kite/login_url", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["mock_mode"] is True
    assert body["login_url"] is None
    assert body["state"]


def test_connect_mock_persists_session(client, auth_headers):
    r = client.post("/kite/connect-mock", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["connected"] is True
    assert body["kite_user_id"] == "MOCK001"
    # Status reflects the new session
    s = client.get("/kite/status", headers=auth_headers).json()
    assert s["connected"] is True


def test_disconnect_clears_session(client, auth_headers):
    client.post("/kite/connect-mock", headers=auth_headers)
    r = client.delete("/kite/session", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["connected"] is False
    s = client.get("/kite/status", headers=auth_headers).json()
    assert s["connected"] is False


def test_callback_invalid_state_redirects_with_error(client):
    r = client.get(
        "/kite/callback",
        params={"request_token": "reqtok", "state": "not-a-jwt"},
        follow_redirects=False,
    )
    assert r.status_code == 302
    assert "kite=error" in r.headers["location"]
    assert "invalid_state" in r.headers["location"]


def test_callback_with_valid_state_persists_and_redirects(client, auth_headers):
    state = client.get("/kite/login_url", headers=auth_headers).json()["state"]
    r = client.get(
        "/kite/callback",
        params={"request_token": "reqtok123", "state": state},
        follow_redirects=False,
    )
    assert r.status_code == 302
    assert "kite=connected" in r.headers["location"]
    s = client.get("/kite/status", headers=auth_headers).json()
    assert s["connected"] is True
