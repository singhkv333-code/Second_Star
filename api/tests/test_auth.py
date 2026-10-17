"""The API's account surface: health, and the profile of a signed-in account.

Sign-up, sign-in and passwords belong to Pivot's account service; this API
has none (see backend/auth/router.py).
"""
from tests.conftest import make_user


def test_health_endpoint(client):
    """Test the health check endpoint."""
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert "status" in data
    assert "database" in data
    assert "redis" in data


# ─── Profile (/auth/me PATCH) ────────────────────────────────────────

def _auth_headers(client, email):
    return make_user(client, email=email)["headers"]


def test_profile_update_round_trips(client):
    h = _auth_headers(client, "prof@pivot.com")
    r = client.patch("/auth/me", headers=h, json={
        "full_name": "  Karan  ", "username": "@Karan_N", "dob": "2004-05-17",
        "avatar": "data:image/png;base64,iVBORw0KGgo=",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["full_name"] == "Karan"
    assert body["username"] == "karan_n"
    assert body["dob"] == "2004-05-17"
    me = client.get("/auth/me", headers=h).json()
    assert me["username"] == "karan_n" and me["avatar"].startswith("data:image/png")


def test_profile_update_only_changes_sent_fields(client):
    h = _auth_headers(client, "partial@pivot.com")
    client.patch("/auth/me", headers=h, json={"username": "partial", "dob": "1999-01-01"})
    r = client.patch("/auth/me", headers=h, json={"dob": None})
    assert r.json()["username"] == "partial"
    assert r.json()["dob"] is None


def test_profile_username_must_be_unique(client):
    a = _auth_headers(client, "a@pivot.com")
    b = _auth_headers(client, "b@pivot.com")
    assert client.patch("/auth/me", headers=a, json={"username": "sameone"}).status_code == 200
    r = client.patch("/auth/me", headers=b, json={"username": "SameOne"})
    assert r.status_code == 409


def test_profile_rejects_bad_values(client):
    h = _auth_headers(client, "bad@pivot.com")
    assert client.patch("/auth/me", headers=h, json={"username": "a"}).status_code == 422
    assert client.patch("/auth/me", headers=h, json={"dob": "2999-01-01"}).status_code == 422
    assert client.patch("/auth/me", headers=h, json={"avatar": "https://x/y.png"}).status_code == 422
