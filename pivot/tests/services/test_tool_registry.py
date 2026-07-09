"""Tool registry visibility tests."""
from __future__ import annotations

from backend.services.tool_registry import _real_tools, get_tool_schema


# Tools that are deliberately NOT shown to the LLM because their handlers
# return placeholder strings. If any of these reappear in the schema without
# a real handler the eval will surface "Created" / "Connect TrueData" leaks
# again.
# (F&O P1: get_option_chain went REAL — moved out of this set; the
# suggest/build/critique/portfolio-greeks tools joined the schema with
# it. get_option_greeks' schema was deleted outright — folded into the
# chain card — so it stays here as a leak guard.)
_FORBIDDEN_FROM_SCHEMA = {
    "get_option_greeks", "get_margin_required",
    "get_upcoming_events", "modify_order",
    "place_futures_order", "place_options_order",
    "place_multileg_options", "roll_futures_position",
    "create_cash_sweep", "create_rebalancing_rule",
    "create_drawdown_protection",
}


def test_stub_tools_are_excluded_from_llm_schema():
    schema = get_tool_schema()
    visible = {t["function"]["name"] for t in schema}
    leaks = visible & _FORBIDDEN_FROM_SCHEMA
    assert not leaks, (
        f"Stub tools leaked into the LLM schema: {sorted(leaks)}. "
        "Either implement them or keep them excluded."
    )


def test_v2_tools_are_visible():
    schema = get_tool_schema()
    visible = {t["function"]["name"] for t in schema}
    expected_v2 = {"get_price_history", "get_52wk_range", "get_product_spec"}
    missing = expected_v2 - visible
    assert not missing, f"v2 tools missing from schema: {missing}"


def test_every_real_tool_has_a_schema_entry():
    schema = get_tool_schema()
    visible = {t["function"]["name"] for t in schema}
    missing = _real_tools() - visible
    # `_real_tools()` includes legacy tools loaded by tools.py; if any are missing
    # the legacy import may have failed.
    assert not missing, f"real tools missing from schema: {missing}"
