"""Tests for /products endpoints."""


def test_product_catalogue(client, auth_headers):
    r = client.get("/products/catalogue", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "products" in body
    assert isinstance(body["products"], list)
    assert len(body["products"]) >= 3


def test_safegrow_preview(client, auth_headers):
    r = client.post(
        "/products/preview",
        json={"product_type": "safegrow", "capital": 100000, "horizon_months": 12},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    for key in ("product_type", "legs", "payoff_table", "explanation"):
        assert key in body, f"missing key: {key}"
    assert isinstance(body["legs"], list)
    assert isinstance(body["payoff_table"], list)


def test_unknown_product(client, auth_headers):
    r = client.post(
        "/products/preview",
        json={"product_type": "unknown", "capital": 100000},
        headers=auth_headers,
    )
    assert r.status_code == 400


def test_products_requires_auth(client):
    r = client.post(
        "/products/preview",
        json={"product_type": "safegrow", "capital": 100000},
    )
    assert r.status_code == 401
