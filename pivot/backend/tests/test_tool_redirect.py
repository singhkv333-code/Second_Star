"""Structured redirect_to (chat-kernel Task 5) — the typed route hint
that replaces regex-scanning error prose for "use <tool>"."""
import asyncio

from backend.services.tool_errors import ToolRedirect


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_tool_redirect_sets_field_on_tool_result(monkeypatch):
    from backend.services import tool_registry as tr

    async def refusing_handler(args):
        raise ToolRedirect("cannot express this; use propose_workflow",
                           redirect_to="propose_workflow")

    tr._ensure_v2_tools_registered()
    monkeypatch.setitem(tr._V2_HANDLERS, "backtest_dsl_tree",
                        refusing_handler)
    res = _run(tr.execute("backtest_dsl_tree", {"condition": "x"},
                          kite_token="t", db=None, user_id=1))
    assert res.success is False
    assert res.redirect_to == "propose_workflow"
    assert "propose_workflow" in (res.error or "")


def test_chat_loop_prefers_structured_over_regex():
    from backend.services.chat_service import _redirect_target_for_failure

    # Structured wins even when the prose names a DIFFERENT tool.
    out = _redirect_target_for_failure(
        "propose_dsl_workflow",
        "blah blah use propose_threshold_order blah",
        "buy INFY every friday",
        structured="propose_workflow",
    )
    assert out == "propose_workflow"
    # Without structured, the legacy regex still works.
    out = _redirect_target_for_failure(
        "propose_dsl_workflow",
        "blah blah use propose_threshold_order blah",
        "buy INFY every friday",
    )
    assert out == "propose_threshold_order"


def test_guarded_result_threads_redirect():
    from backend.services.tool_registry import ToolResult
    from backend.services.validation_handler import GuardedToolResult

    r = ToolResult(name="t", args={}, success=False, data={},
                   error="e", redirect_to="propose_workflow")
    g = GuardedToolResult.from_tool_result("t", {}, r)
    assert g.redirect_to == "propose_workflow"


def test_dsl_multi_action_ticker_refusal_is_typed():
    """The multi-action-ticker guard must raise ToolRedirect (not bare
    ValueError) so the redirect survives any truncation of the prose."""
    import inspect

    from backend.services import _dsl_chat_tools as d

    src = inspect.getsource(d)
    assert 'raise ToolRedirect(\n            f"propose_dsl_workflow is single-symbol' in src
    assert 'redirect_to="propose_workflow"' in src
    assert 'redirect_to="propose_holding_action"' in src
