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
