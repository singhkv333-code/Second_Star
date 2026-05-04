import os
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("DATABASE_URL", "sqlite:///./pivot_test.db")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-minimum-32-characters")

from backend.agents.tools import ALL_TOOLS


# Note: the legacy intent classifier (classify_intent) and tool subsets
# (TOOL_SUBSETS / get_tools_for_subset) were removed during the v2 hard
# reset. The chatbot now exposes the full tool schema to the LLM on every
# turn. See backend/services/tool_registry.py and the tests under
# tests/services/.


def test_all_tools_have_required_fields():
    for name, t in ALL_TOOLS.items():
        assert t["type"] == "function"
        fn = t["function"]
        assert "name" in fn and "description" in fn and "parameters" in fn
        assert fn["name"] == name, f"{name} name mismatch"


def test_tool_count():
    assert len(ALL_TOOLS) >= 40


def test_extract_emulated_tool_call_parses_block():
    from backend.agents.sarvam_client import _extract_emulated_tool_call
    raw = (
        'Here is the SIP plan.\n'
        '<TOOL_CALL>{"name":"create_sip","arguments":{"symbol":"NIFTYBEES",'
        '"amount_inr":5000,"frequency":"monthly","day_of_month":1}}</TOOL_CALL>'
    )
    text, call = _extract_emulated_tool_call(raw)
    assert call is not None
    assert call["name"] == "create_sip"
    assert call["arguments"]["symbol"] == "NIFTYBEES"
    assert call["arguments"]["amount_inr"] == 5000
    assert "<TOOL_CALL>" not in text


def test_extract_emulated_tool_call_returns_none_for_plain_text():
    from backend.agents.sarvam_client import _extract_emulated_tool_call
    text, call = _extract_emulated_tool_call("Just a normal answer.")
    assert call is None
    assert text == "Just a normal answer."


def test_build_tool_instruction_contains_tool_names():
    from backend.agents.sarvam_client import _build_tool_instruction
    from backend.agents.tools import get_tools_for_subset
    instruction = _build_tool_instruction(get_tools_for_subset("ORDER_RECURRING"))
    assert "create_sip" in instruction
    assert "<TOOL_CALL>" in instruction
