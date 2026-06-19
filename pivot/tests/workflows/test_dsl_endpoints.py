"""Tests for the DSL builder helper endpoints.

  GET  /api/workflows/dsl/schema    — read-only metadata for the
                                      ConditionBuilder operand pickers.
  POST /api/workflows/dsl/describe  — english readback of a DSL tree
                                      with semantic validation.

Both endpoints are auth-required and never 500 on bad input — invalid
trees come back as 200 with `{"english": "", "error": "..."}` so the
builder UI surfaces the message inline. Full-workflow validation
remains the job of POST /api/workflows/lint.
"""
from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient


# ── /dsl/schema ──────────────────────────────────────────────────────


def _get_schema(client: TestClient, auth_headers: dict[str, str]) -> dict[str, Any]:
    resp = client.get("/api/workflows/dsl/schema", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert isinstance(body, dict)
    return body


def test_dsl_schema_requires_auth(client: TestClient) -> None:
    resp = client.get("/api/workflows/dsl/schema")
    assert resp.status_code == 401


def test_dsl_schema_top_level_shape(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    body = _get_schema(client, auth_headers)
    expected_keys = {
        "indicators", "operators", "operand_kinds", "price_bases",
        "position_fields", "logic_ops", "timeframes", "tree_fields",
    }
    assert set(body.keys()) == expected_keys


def test_dsl_schema_indicators_include_rsi_and_macd(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    """Indicators come from the live registry — assert the two the
    builder MVP demos against are both present, and that the multi-output
    ones (macd) advertise their components."""
    body = _get_schema(client, auth_headers)
    indicators = body["indicators"]
    assert isinstance(indicators, list)
    assert indicators, "indicator list must not be empty"

    by_id = {entry["id"]: entry for entry in indicators}
    assert "rsi" in by_id
    assert "macd" in by_id

    rsi = by_id["rsi"]
    assert rsi["label"]  # non-empty
    assert isinstance(rsi["default_period"], int) and rsi["default_period"] >= 1
    assert rsi["multi_output"] is False
    assert rsi["components"] == []

    macd = by_id["macd"]
    assert macd["multi_output"] is True
    # MACD exposes line/signal/hist per the contract.
    assert set(macd["components"]) >= {"macd", "signal", "hist"}


def test_dsl_schema_static_pieces_match_contract(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    body = _get_schema(client, auth_headers)
    op_ids = [op["id"] for op in body["operators"]]
    assert op_ids == [
        ">", "<", ">=", "<=", "==", "crosses_above", "crosses_below",
    ]
    assert body["operand_kinds"] == [
        "indicator", "price", "constant", "position",
    ]
    assert body["price_bases"] == ["close", "open", "high", "low"]
    assert body["logic_ops"] == ["and", "or"]
    assert body["timeframes"] == ["daily", "weekly"]

    pf_ids = [pf["id"] for pf in body["position_fields"]]
    assert pf_ids == [
        "entry_price", "unrealised_pct", "unrealised_abs", "bars_held",
        "peak_unrealised_pct", "drawdown_from_peak_pct",
    ]


def test_dsl_schema_tree_fields_cover_three_compound_steps(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    """The three compound step_types must each map to the config field
    holding the DSL tree, with mode='exit' only for the exit trigger."""
    body = _get_schema(client, auth_headers)
    tree_fields = body["tree_fields"]
    assert isinstance(tree_fields, dict)
    assert set(tree_fields.keys()) == {
        "trigger.compound", "trigger.exit_compound", "condition.compound",
    }
    # Every compound step stores its tree under config['entry'].
    for step_type, meta in tree_fields.items():
        assert meta["field"] == "entry", step_type
    assert tree_fields["trigger.compound"]["mode"] == "entry"
    assert tree_fields["condition.compound"]["mode"] == "entry"
    assert tree_fields["trigger.exit_compound"]["mode"] == "exit"


# ── /dsl/describe ────────────────────────────────────────────────────


def _describe(
    client: TestClient,
    auth_headers: dict[str, str],
    *,
    tree: dict[str, Any],
    mode: str = "entry",
) -> dict[str, Any]:
    resp = client.post(
        "/api/workflows/dsl/describe",
        headers=auth_headers,
        json={"tree": tree, "mode": mode},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert isinstance(body, dict)
    assert set(body.keys()) >= {"english"}
    return body


def test_dsl_describe_requires_auth(client: TestClient) -> None:
    resp = client.post(
        "/api/workflows/dsl/describe",
        json={"tree": {}, "mode": "entry"},
    )
    assert resp.status_code == 401


def test_dsl_describe_daily_rsi_tree_renders_english(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    """A daily RSI tree → an english string containing the indicator,
    the symbol, the operator and the constant — and NO weekly suffix."""
    tree = {
        "type": "comparison",
        "op": "<",
        "left": {
            "type": "indicator",
            "indicator": "rsi",
            "symbol": "RELIANCE",
            "period": 14,
            "timeframe": "daily",
            "exchange": "NSE",
            "offset": 0,
        },
        "right": {"type": "constant", "value": 40},
    }
    body = _describe(client, auth_headers, tree=tree)
    english = body["english"]
    assert english, "expected non-empty english"
    assert body.get("error") in (None, ""), body
    assert "RSI(14)" in english
    assert "RELIANCE" in english
    assert "< 40" in english
    assert "weekly" not in english.lower()


def test_dsl_describe_weekly_tree_adds_weekly_suffix(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    """When any indicator leaf is on weekly bars, the readback appends
    'on weekly bars' so the user sees the timeframe in the confirmation."""
    tree = {
        "type": "comparison",
        "op": "<",
        "left": {
            "type": "indicator",
            "indicator": "rsi",
            "symbol": "GRASIM",
            "period": 14,
            "timeframe": "weekly",
            "exchange": "NSE",
            "offset": 0,
        },
        "right": {"type": "constant", "value": 30},
    }
    body = _describe(client, auth_headers, tree=tree)
    english = body["english"]
    assert english
    assert body.get("error") in (None, ""), body
    assert "on weekly bars" in english
    # core sentence still present
    assert "RSI(14)" in english
    assert "GRASIM" in english


def test_dsl_describe_invalid_tree_returns_200_with_error(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    """A bare leaf at the root fails semantic validation (root must be
    a comparison or logic). The endpoint MUST NOT 500 — it returns 200
    with `english=''` and an error string so the builder can render the
    message inline."""
    bad_tree = {
        "type": "indicator",
        "indicator": "rsi",
        "symbol": "RELIANCE",
        "period": 14,
        "timeframe": "daily",
        "exchange": "NSE",
        "offset": 0,
    }
    resp = client.post(
        "/api/workflows/dsl/describe",
        headers=auth_headers,
        json={"tree": bad_tree, "mode": "entry"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["english"] == ""
    assert body.get("error"), "expected a non-empty error message"


def test_dsl_describe_position_leaf_rejected_in_entry_allowed_in_exit(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    """PositionNodes are only valid in exit trees. The endpoint's `mode`
    flag must gate this — entry mode 200s with an error, exit mode 200s
    with a rendered english string."""
    tree = {
        "type": "comparison",
        "op": ">=",
        "left": {"type": "position", "field": "unrealised_pct"},
        "right": {"type": "constant", "value": 0.10},
    }
    # entry mode → semantic_validate rejects position leaf
    body_entry = _describe(client, auth_headers, tree=tree, mode="entry")
    assert body_entry["english"] == ""
    assert body_entry.get("error")

    # exit mode → accepted; english rendered
    body_exit = _describe(client, auth_headers, tree=tree, mode="exit")
    assert body_exit["english"]
    assert body_exit.get("error") in (None, ""), body_exit
    assert "unrealised" in body_exit["english"].lower()
