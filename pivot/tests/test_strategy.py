"""Strategy lifecycle tests: create → list → pause → resume → delete."""


def test_strategy_lifecycle(client, auth_headers):
    # Create
    r = client.post(
        "/strategies",
        json={
            "name": "test",
            "strategy_type": "price_drop",
            "trigger_condition": {"threshold_pct": 5},
            "action_config": {},
        },
        headers=auth_headers,
    )
    assert r.status_code == 201, r.text
    strat = r.json()
    strat_id = strat["id"]

    # List
    r = client.get("/strategies", headers=auth_headers)
    assert r.status_code == 200, r.text
    ids = [item["id"] for item in r.json()]
    assert strat_id in ids

    # Pause
    r = client.patch(f"/strategies/{strat_id}/pause", headers=auth_headers)
    assert r.status_code == 200, r.text

    # Resume
    r = client.patch(f"/strategies/{strat_id}/resume", headers=auth_headers)
    assert r.status_code == 200, r.text

    # Delete
    r = client.delete(f"/strategies/{strat_id}", headers=auth_headers)
    assert r.status_code == 200, r.text
