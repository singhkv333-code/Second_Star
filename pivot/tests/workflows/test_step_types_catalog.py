"""Tests for GET /api/step-types.

Locks the catalog response shape against docs/API_CONTRACT.md §8.1 and
the canonical category-assignment table at the bottom of §8. If a step
type goes missing, gets renamed, or gets the wrong category, this
suite fails — that's the safety net for the frontend.
"""
from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient


# Single source of truth for the v1+ catalog — kept in lock-step with
# backend/workflows/steps/*.py @register_step decorators. New step
# types land here once their category is locked. Test failure here
# means either: (a) a step type was added without updating this list,
# or (b) a step type was removed/renamed without updating this list.
# The frontend picker relies on this exact set, so any drift is a
# real bug.
EXPECTED_STEP_TYPES: dict[str, str] = {
    # ── Triggers (8) ──
    "trigger.schedule": "trigger",
    "trigger.price": "trigger",
    "trigger.indicator": "trigger",
    "trigger.event": "trigger",
    "trigger.manual": "trigger",
    "trigger.webhook": "trigger",
    "trigger.market_relative_time": "trigger",
    # Phase-D5 — DSL-driven compound trigger (RSI < 30 AND price > X, etc.)
    "trigger.compound": "trigger",
    # Slice-4 — Polymarket prediction-market trigger (threshold OR resolution)
    "trigger.polymarket": "trigger",
    # Phase-D6 — exit-tree compound trigger (entry with position-state leaves)
    "trigger.exit_compound": "trigger",
    # P2 — IPO open-day reminder watcher (upcoming -> open edge)
    "trigger.ipo_open": "trigger",
    # F&O P3 — option expiry-day trigger (DTE from the contract master)
    "trigger.expiry_day": "trigger",
    # ── Data fetches (11) ──
    "fetch.quote": "fetch",
    "fetch.indicator": "fetch",
    "fetch.fundamental": "fetch",
    "fetch.portfolio": "fetch",
    "fetch.news": "fetch",
    "fetch.intraday_pnl": "fetch",
    "fetch.relative_threshold": "fetch",
    # Collapsed: replaces fetch.day_open + fetch.prior_close (reference enum)
    "fetch.price_reference": "fetch",
    # Collapsed: replaces fetch.rolling_high + fetch.rolling_low (side enum)
    "fetch.rolling_extreme": "fetch",
    "fetch.screener": "fetch",
    "fetch.spread_z_score": "fetch",
    "fetch.top_movers": "fetch",
    # ── Conditions (5) ──
    "condition.numeric": "condition",
    "condition.boolean": "condition",
    "condition.market_status": "condition",
    "condition.position": "condition",
    "condition.time_window": "condition",
    # Phase-D5 — DSL-driven compound condition (mid-branch gate)
    "condition.compound": "condition",
    # ── Actions (10) ──
    "action.place_order": "action",
    "action.cancel_orders": "action",
    # Collapsed: replaces action.set_stoploss + action.set_takeprofit (kind enum)
    "action.set_protective": "action",
    "action.update_watchlist": "action",
    "action.allocate_basket": "action",
    "action.allocate_notional": "action",
    # Collapsed: replaces action.squareoff_symbol/_all/_all_intraday (scope enum)
    "action.squareoff": "action",
    # P2 — IPO arm-intent (register-not-execute, no broker call)
    "action.arm_ipo_intent": "action",
    # F&O P3 — multi-leg option strategy (paper executes; live registers)
    "action.place_option_strategy": "action",
    # ── Communication (3) ──
    "notify.message": "notify",
    "notify.log": "notify",
    "wait.approval": "notify",
    # ── Control flow (2) ──
    "wait.delay": "control",
    "control.skip_if": "control",
}

EXPECTED_CATEGORIES: list[tuple[str, str]] = [
    ("trigger", "Triggers"),
    ("fetch", "Data fetches"),
    ("condition", "Conditions"),
    ("action", "Actions"),
    ("notify", "Communication"),
    ("control", "Control flow"),
]


def _get_catalog(client: TestClient, auth_headers: dict[str, str]) -> dict[str, Any]:
    resp = client.get("/api/step-types", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert isinstance(body, dict)
    return body


def test_requires_auth(client: TestClient) -> None:
    """No bearer token → 401 (matches API_CONTRACT.md §1)."""
    resp = client.get("/api/step-types")
    assert resp.status_code == 401


def test_top_level_shape(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    body = _get_catalog(client, auth_headers)
    assert set(body.keys()) == {"catalog_version", "categories", "step_types"}
    assert isinstance(body["catalog_version"], str)
    assert body["catalog_version"]  # non-empty
    assert isinstance(body["categories"], list)
    assert isinstance(body["step_types"], list)


def test_categories_match_contract(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    body = _get_catalog(client, auth_headers)
    actual = [(c["id"], c["label"]) for c in body["categories"]]
    assert actual == EXPECTED_CATEGORIES


def test_every_step_type_present_with_correct_category(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    """The 24 v1 step types from API_CONTRACT.md §8 must all appear, each
    with the locked category. Failing this means the picker would either
    miss a type or render it under the wrong heading."""
    body = _get_catalog(client, auth_headers)
    actual = {st["step_type"]: st["category"] for st in body["step_types"]}

    missing = set(EXPECTED_STEP_TYPES) - set(actual)
    extra = set(actual) - set(EXPECTED_STEP_TYPES)
    assert not missing, f"Missing step types: {sorted(missing)}"
    assert not extra, f"Unexpected step types: {sorted(extra)}"

    miscategorised = {
        st: (actual[st], expected)
        for st, expected in EXPECTED_STEP_TYPES.items()
        if actual[st] != expected
    }
    assert not miscategorised, f"Wrong categories: {miscategorised}"


def test_every_step_type_has_full_metadata(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    """Every entry must include the seven required fields from §8.1
    plus output_schema (which may be null)."""
    body = _get_catalog(client, auth_headers)
    required = {
        "step_type", "category", "label", "description", "icon",
        "max_retries", "trigger_only", "config_schema", "output_schema",
    }
    for entry in body["step_types"]:
        missing = required - set(entry.keys())
        assert not missing, (
            f"{entry.get('step_type')} missing fields: {sorted(missing)}"
        )
        assert isinstance(entry["label"], str) and entry["label"]
        assert isinstance(entry["description"], str) and entry["description"]
        assert isinstance(entry["icon"], str) and entry["icon"]
        assert isinstance(entry["max_retries"], int)
        assert entry["max_retries"] >= 0
        assert isinstance(entry["trigger_only"], bool)
        assert isinstance(entry["config_schema"], dict)
        assert entry["config_schema"].get("type") == "object"
        # output_schema is dict-or-null — both are valid.
        assert entry["output_schema"] is None or isinstance(
            entry["output_schema"], dict
        )


def test_max_retries_match_invariant_3(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    """ARCHITECTURE.md §7 invariant 3 locks per-category retry budgets:
    fetches=3, actions=1, notify=2, triggers/conditions/control=0."""
    body = _get_catalog(client, auth_headers)
    by_type = {st["step_type"]: st for st in body["step_types"]}

    # Triggers
    for st in (
        "trigger.schedule", "trigger.price", "trigger.indicator",
        "trigger.event", "trigger.manual", "trigger.webhook",
        "trigger.polymarket",
    ):
        assert by_type[st]["max_retries"] == 0, st
        assert by_type[st]["trigger_only"] is True, st

    # Fetches
    for st in (
        "fetch.quote", "fetch.indicator", "fetch.fundamental",
        "fetch.portfolio", "fetch.news",
    ):
        assert by_type[st]["max_retries"] == 3, st
        assert by_type[st]["trigger_only"] is False, st

    # Conditions
    for st in (
        "condition.numeric", "condition.market_status",
        "condition.position", "condition.time_window",
    ):
        assert by_type[st]["max_retries"] == 0, st

    # Actions
    for st in (
        "action.place_order", "action.cancel_orders",
        "action.set_protective", "action.update_watchlist",
    ):
        assert by_type[st]["max_retries"] == 1, st

    # Notify (notify.message, notify.log = 2; wait.approval = 0)
    assert by_type["notify.message"]["max_retries"] == 2
    assert by_type["notify.log"]["max_retries"] == 2
    assert by_type["wait.approval"]["max_retries"] == 0

    # Control
    assert by_type["wait.delay"]["max_retries"] == 0
    assert by_type["control.skip_if"]["max_retries"] == 0


def test_config_schemas_are_draft_2020_12_compatible(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    """The `config_schema` for every step type must be a valid JSON
    Schema dict (we use jsonschema's Draft202012Validator to confirm
    structure rather than test specific fields)."""
    from jsonschema import Draft202012Validator

    body = _get_catalog(client, auth_headers)
    for entry in body["step_types"]:
        schema = entry["config_schema"]
        # check_schema raises if the meta-schema is violated.
        Draft202012Validator.check_schema(schema)


def test_no_orphaned_categories(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    """Every step's `category` must appear in the top-level categories
    list — otherwise the UI can't group it."""
    body = _get_catalog(client, auth_headers)
    cat_ids = {c["id"] for c in body["categories"]}
    for st in body["step_types"]:
        assert st["category"] in cat_ids, (
            f"{st['step_type']} has unknown category {st['category']!r}"
        )


def test_skip_if_renamed(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    """Reviewer Day-1 fix: bare `skip_if` is gone, `control.skip_if` is in.
    Guards against accidental regressions during the engine build."""
    body = _get_catalog(client, auth_headers)
    types = {st["step_type"] for st in body["step_types"]}
    assert "control.skip_if" in types
    assert "skip_if" not in types
