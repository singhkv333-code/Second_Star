"""F&O P1 — /option-strategies REST surface: register/withdraw/list,
server-side recompute, fail-closed gate (MCX block, disclosure,
expiry-day naked short), duplicate guard."""
from datetime import date

import pytest

from backend.market.instrument_master import refresh_instrument_master, resolve_expiry
from backend.services.option_strategies import resolve_strategy


@pytest.fixture(autouse=True)
def _master_and_cache(db):
    # Flush optchain:* on BOTH MockRedis and real Redis — a dev server
    # on the same local Redis shares the keyspace (flake source).
    from backend.cache import redis_client

    if hasattr(redis_client, "_store"):
        redis_client._store.clear()
        redis_client._expires_at.clear()
    elif hasattr(redis_client, "scan_iter"):
        for key in list(redis_client.scan_iter("optchain:*")):
            redis_client.delete(key)
    refresh_instrument_master(db)
    yield


def _resolved_legs(db, template: str, underlying: str = "NIFTY") -> tuple[str, list[dict]]:
    """Server-resolve once to learn valid strikes for the request body."""
    payload = resolve_strategy(db, underlying, template)
    legs = [
        {"option_type": l["option_type"], "side": l["side"], "strike": l["strike"]}
        for l in payload["editable"]["legs"]
    ]
    return payload["locked"]["expiry"], legs


def _register_body(db, template="bull_call_spread", underlying="NIFTY", **over):
    expiry, legs = _resolved_legs(db, template, underlying)
    body = {
        "underlying": underlying,
        "expiry": expiry,
        "template": template,
        "book": "paper",
        "qty_lots": 1,
        "legs": legs,
        "acknowledge_disclosure": True,
    }
    body.update(over)
    return body


def test_register_persists_server_numbers(client, auth_headers, db):
    body = _register_body(db)
    r = client.post("/option-strategies", json=body, headers=auth_headers)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["success"] is True, out
    s = out["strategy"]
    assert s["status"] == "registered"
    assert s["book"] == "paper"
    assert s["template"] == "bull_call_spread"
    assert len(s["legs"]) == 2
    # Decision quad is the SERVER's recompute, present and coherent.
    assert s["max_loss"] is not None and s["max_loss"] > 0
    assert s["pop"] is not None
    assert s["capital_required"] > 0
    assert s["legs"][0]["entry_mid"] is not None


def test_disclosure_not_acknowledged_blocks(client, auth_headers, db):
    body = _register_body(db, acknowledge_disclosure=False)
    r = client.post("/option-strategies", json=body, headers=auth_headers)
    out = r.json()
    assert out["success"] is False
    assert "disclosure" in (out["error"] or "").lower()


def test_mcx_registration_not_research_only_blocked(client, auth_headers, db):
    # Commodities (MCX) are tradeable via register-not-execute — registration
    # is no longer hard-rejected as "research-only".
    body = _register_body(db, template="long_straddle", underlying="CRUDEOIL")
    r = client.post("/option-strategies", json=body, headers=auth_headers)
    out = r.json()
    assert "research-only" not in (out["error"] or "")


def test_duplicate_register_returns_existing(client, auth_headers, db):
    body = _register_body(db)
    first = client.post("/option-strategies", json=body, headers=auth_headers).json()
    assert first["success"], first
    second = client.post("/option-strategies", json=body, headers=auth_headers).json()
    assert second["success"] is True
    assert second.get("duplicate") is True
    assert second["strategy"]["id"] == first["strategy"]["id"]


def test_withdraw_flow(client, auth_headers, db):
    body = _register_body(db, template="iron_condor")
    out = client.post("/option-strategies", json=body, headers=auth_headers).json()
    sid = out["strategy"]["id"]
    w = client.post(
        f"/option-strategies/{sid}/withdraw", headers=auth_headers,
    ).json()
    assert w["success"] is True
    assert w["strategy"]["status"] == "withdrawn"
    # Second withdraw refuses politely.
    w2 = client.post(
        f"/option-strategies/{sid}/withdraw", headers=auth_headers,
    ).json()
    assert w2["success"] is False


def test_list_returns_users_strategies(client, auth_headers, db):
    body = _register_body(db, template="long_call")
    client.post("/option-strategies", json=body, headers=auth_headers)
    out = client.get("/users/option-strategies", headers=auth_headers).json()
    assert out["strategies"]
    assert out["strategies"][0]["underlying"] == "NIFTY"


def test_invalid_strike_rejected_with_note(client, auth_headers, db):
    expiry, legs = _resolved_legs(db, "long_call")
    legs[0]["strike"] = 1234.5  # not on the ladder
    body = {
        "underlying": "NIFTY", "expiry": expiry, "template": "custom",
        "book": "paper", "qty_lots": 1, "legs": legs,
        "acknowledge_disclosure": True,
    }
    out = client.post("/option-strategies", json=body, headers=auth_headers).json()
    assert out["success"] is False
    assert "quotable" in (out["error"] or "")


def test_requires_auth(client, db):
    body = {"underlying": "NIFTY", "expiry": "2026-12-31", "template": "long_call",
            "legs": [{"option_type": "CE", "side": "BUY", "strike": 1}],
            "acknowledge_disclosure": True}
    r = client.post("/option-strategies", json=body)
    assert r.status_code == 401


def test_kill_switch_blocks(client, auth_headers, db, monkeypatch):
    monkeypatch.setenv("PIVOT_FNO_KILL_SWITCH", "1")
    body = _register_body(db)
    out = client.post("/option-strategies", json=body, headers=auth_headers).json()
    assert out["success"] is False
    assert "kill switch" in (out["error"] or "")
