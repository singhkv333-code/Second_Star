def test_register_new_user(client):
    """Test successful user registration."""
    response = client.post("/auth/register", json={
        "email": "newuser@pivot.com",
        "password": "securepass123",
        "full_name": "New User",
    })
    assert response.status_code == 201
    data = response.json()
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["email"] == "newuser@pivot.com"
    assert data["token_type"] == "bearer"


def test_register_duplicate_email(client):
    """Test that duplicate registration is rejected."""
    client.post("/auth/register", json={
        "email": "dup@pivot.com",
        "password": "password123",
    })
    response = client.post("/auth/register", json={
        "email": "dup@pivot.com",
        "password": "password123",
    })
    assert response.status_code == 400
    assert "already registered" in response.json()["detail"]


def test_login_valid_credentials(client):
    """Test login with correct credentials."""
    client.post("/auth/register", json={
        "email": "login@pivot.com",
        "password": "mypassword123",
    })
    response = client.post("/auth/login", json={
        "email": "login@pivot.com",
        "password": "mypassword123",
    })
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data


def test_login_wrong_password(client):
    """Test login with wrong password returns 401."""
    client.post("/auth/register", json={
        "email": "wrong@pivot.com",
        "password": "correctpass123",
    })
    response = client.post("/auth/login", json={
        "email": "wrong@pivot.com",
        "password": "wrongpassword",
    })
    assert response.status_code == 401


def test_health_endpoint(client):
    """Test the health check endpoint."""
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert "status" in data
    assert "database" in data
    assert "redis" in data


# ─── Google sign-in (/auth/google) ───────────────────────────────────

class _FakeGoogleResp:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


def _install_fake_google(monkeypatch, *, aud, email="guser@gmail.com",
                         verified="true", name="G User"):
    """Stub httpx so the endpoint's tokeninfo/userinfo calls return canned
    responses — no real network, no real Google token needed."""
    import httpx

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, url, params=None, headers=None):
            if "tokeninfo" in url:
                return _FakeGoogleResp(200, {
                    "aud": aud, "azp": aud,
                    "email": email, "email_verified": verified,
                })
            if "userinfo" in url:
                return _FakeGoogleResp(200, {"name": name, "email": email})
            return _FakeGoogleResp(404, {})

    monkeypatch.setattr(httpx, "Client", _FakeClient)


def test_google_signin_not_configured_returns_503(client):
    """With no GOOGLE_CLIENT_ID set, the endpoint 503s honestly."""
    r = client.post("/auth/google", json={"access_token": "x"})
    assert r.status_code == 503


def test_google_signin_audience_mismatch_401(client, monkeypatch):
    """A token minted for a DIFFERENT client id must be rejected."""
    from backend.config import settings
    monkeypatch.setattr(settings, "google_client_id", "my-client-id")
    _install_fake_google(monkeypatch, aud="attacker-client-id")
    r = client.post("/auth/google", json={"access_token": "x"})
    assert r.status_code == 401


def test_google_signin_unverified_email_401(client, monkeypatch):
    """Google account without a verified email is rejected."""
    from backend.config import settings
    monkeypatch.setattr(settings, "google_client_id", "my-client-id")
    _install_fake_google(monkeypatch, aud="my-client-id", verified="false")
    r = client.post("/auth/google", json={"access_token": "x"})
    assert r.status_code == 401


def test_google_signin_creates_user_and_returns_tokens(client, monkeypatch):
    """Happy path: a new verified Google identity → account + tokens."""
    from backend.config import settings
    monkeypatch.setattr(settings, "google_client_id", "my-client-id")
    _install_fake_google(
        monkeypatch, aud="my-client-id",
        email="fresh@gmail.com", name="Fresh Person",
    )
    r = client.post("/auth/google", json={"access_token": "x"})
    assert r.status_code == 200
    data = r.json()
    assert data["email"] == "fresh@gmail.com"
    assert "access_token" in data and "refresh_token" in data


def test_google_signin_links_existing_email(client, monkeypatch):
    """Same verified email as a password account → logs into that account."""
    from backend.config import settings
    client.post("/auth/register", json={
        "email": "linkme@gmail.com",
        "password": "password123",
        "full_name": "Link Me",
    })
    monkeypatch.setattr(settings, "google_client_id", "my-client-id")
    _install_fake_google(monkeypatch, aud="my-client-id", email="linkme@gmail.com")
    r = client.post("/auth/google", json={"access_token": "x"})
    assert r.status_code == 200
    assert r.json()["email"] == "linkme@gmail.com"


# ─── Profile (/auth/me PATCH) ────────────────────────────────────────

def _auth_headers(client, email):
    r = client.post("/auth/register", json={"email": email, "password": "password123"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


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
