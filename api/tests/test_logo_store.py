"""Logos from our own table: same-origin, versioned, cached forever."""
from fastapi.testclient import TestClient

from backend.market import company_logos as cl, logo_store


def test_a_stored_logo_wins_and_is_versioned(monkeypatch):
    monkeypatch.setattr(logo_store, "_load_versions",
                        lambda: {"TCS": ("71a7d239a9", True), "XYZ": ("0011223344", False)})
    assert logo_store.path_for("tcs") == "/api/pivot/companies/logo/TCS?v=71a7d239a9&tile=1"
    assert logo_store.path_for("XYZ") == "/api/pivot/companies/logo/XYZ?v=0011223344"
    assert logo_store.path_for("NOPE") is None
    assert cl.get_logo_urls(["TCS"])["TCS"].startswith("/api/pivot/companies/logo/TCS")


def test_an_unstored_symbol_falls_back_to_the_old_ladder(monkeypatch):
    monkeypatch.setattr(logo_store, "_load_versions", lambda: {})
    url = cl.get_logo_urls(["TCS"])["TCS"]
    assert url and "shareperks.in" in url


def test_image_route_serves_bytes_cacheably_at_both_paths(monkeypatch):
    from backend.main import app

    svg = b"<svg xmlns='http://www.w3.org/2000/svg'/>"
    monkeypatch.setattr(logo_store, "image",
                        lambda s: (svg, "image/svg+xml", "ab" * 32) if s.upper() == "TCS" else None)
    c = TestClient(app)
    for path in ("/api/companies/logo/TCS", "/api/pivot/companies/logo/TCS?v=1"):
        r = c.get(path)
        assert r.status_code == 200 and r.content == svg
        assert "immutable" in r.headers["cache-control"]
        assert r.headers["content-security-policy"].startswith("default-src 'none'")
    etag = r.headers["etag"]
    assert c.get("/api/companies/logo/TCS", headers={"If-None-Match": etag}).status_code == 304
    assert c.get("/api/companies/logo/NOPE").status_code == 404
