"""SIP lifecycle tests: create → list → pause → resume → delete."""


def test_sip_lifecycle(client, auth_headers):
    # Create
    r = client.post(
        "/sip",
        json={
            "name": "Test SIP",
            "symbol": "INFY",
            "amount": 5000,
            "frequency": "monthly",
        },
        headers=auth_headers,
    )
    assert r.status_code == 201, r.text
    sip = r.json()
    sip_id = sip["id"]

    # List
    r = client.get("/sip", headers=auth_headers)
    assert r.status_code == 200, r.text
    ids = [item["id"] for item in r.json()]
    assert sip_id in ids

    # Pause
    r = client.patch(f"/sip/{sip_id}/pause", headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "paused"

    # Resume
    r = client.patch(f"/sip/{sip_id}/resume", headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "active"

    # Delete
    r = client.delete(f"/sip/{sip_id}", headers=auth_headers)
    assert r.status_code == 200, r.text
