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


# Removed: test_extract_emulated_tool_call_* and test_build_tool_instruction_*
# These covered Sarvam-specific tool-emulation helpers in the old
# backend.agents.sarvam_client module. That module was deleted when the
# 4 callers (chart_parser, symbol_mapper, backtester/parser, router)
# migrated to native function-calling via backend.llm.factory.get_llm_client.
# The new code path uses the provider's native tools API; there is no
# <TOOL_CALL> block emulation to test.
