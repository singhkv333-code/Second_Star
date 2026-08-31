"""Execution mode wires Charto's side chat onto Pivot's automation engine.

These tests are about the SEAM, not about Pivot's builder — the builder has
its own suite in pivot/tests. What can break here is the joinery: the wrong
prompt on the wire, a tool advertised that nothing can dispatch, an alert
quietly routed into an order draft, or a card whose steps still read as
engineering ids.
"""
from __future__ import annotations

import execution_bridge
import dataserver as server


def _execution_mode() -> None:
    server._req.chat_mode = "execution"


def test_execution_context_replaces_the_analysis_contract() -> None:
    _execution_mode()
    block = "\n\n".join((server.FORMAT_RULES, server._execution_system()))
    assert "Strategy Builder" in block
    assert "Every why-did-it-move question" not in block


def test_alerts_route_to_charto_not_to_a_pivot_workflow() -> None:
    """The one rule that keeps a 'tell me when' from becoming a buy order.

    Pivot's proposal tools refuse notify-only drafts outright, so without
    this the only actionable tool on the surface would compile an ORDER for
    an alert ask. Both halves have to hold: the instruction, and the alert
    tools actually being on the wire to receive it.
    """
    _execution_mode()
    assert "set_alert" in server._execution_system()
    names = {tool["name"] for tool in server._tools_for_request()}
    assert {"set_alert", "list_alerts", "cancel_alert"} <= names


def test_every_advertised_tool_can_be_dispatched() -> None:
    _execution_mode()
    names = {tool["name"] for tool in server._tools_for_request()}
    assert names <= set(server._DISPATCH)


def test_execution_mode_adds_the_builder_and_drops_the_commentary_reads() -> None:
    _execution_mode()
    names = {tool["name"] for tool in server._tools_for_request()}
    assert "propose_dsl_workflow" in names
    assert "backtest_dsl_tree" in names
    assert "search_news" not in names
    assert "explain_move" not in names


def test_chat_mode_is_untouched_by_the_execution_surface() -> None:
    server._req.chat_mode = "chat"
    names = {tool["name"] for tool in server._tools_for_request()}
    assert "explain_move" in names
    assert "propose_dsl_workflow" not in names


def test_bridge_offers_exactly_the_pivot_tools_it_declares() -> None:
    ready, reason = execution_bridge.available()
    assert ready, reason
    assert ([t["name"] for t in execution_bridge.tools()]
            == list(execution_bridge.PIVOT_TOOLS))


def test_unavailable_engine_degrades_instead_of_raising(monkeypatch) -> None:
    """A missing Pivot must not take the chat turn down with it.

    `_execution_system()` is called inside the streaming turn; if it raised,
    a server that merely failed to import Pivot would 500 on every execution
    message instead of answering that the mode is unavailable.
    """
    monkeypatch.setattr(execution_bridge, "_state",
                        {"tried": True, "ok": False, "error": "simulated",
                         "mods": None})
    ready, reason = execution_bridge.available()
    assert not ready and "simulated" in reason
    assert execution_bridge.tools() == []
    assert "Strategy Builder" in execution_bridge.system_prompt()
    result = execution_bridge.dispatch("propose_dsl_workflow", {})
    assert result["error"] == "execution_engine_unavailable"


def test_draft_steps_are_humanized_for_the_card() -> None:
    """A card shows labels and a sentence, never a step_type and a parse tree."""
    draft = {
        "steps": [
            {"step_type": "trigger.compound", "config": {"entry": {
                "type": "comparison", "op": "<",
                "left": {"type": "indicator", "indicator": "rsi",
                         "symbol": "INFY", "period": 14},
                "right": {"type": "constant", "value": 30}}}},
            {"step_type": "action.place_order",
             "label": "action.place_order",
             "config": {"symbol": "INFY", "side": "buy", "quantity": 10}},
        ],
    }
    execution_bridge._humanize(draft)
    entry, order = draft["steps"]
    assert entry["label"] and entry["label"] != "trigger.compound"
    assert "RSI(14)" in entry["readback"] and "30" in entry["readback"]
    # A leaked id in the label is overwritten, not kept because it was set.
    assert order["label"] != "action.place_order"


def _chart_ctx() -> dict:
    """The envelope shape the FE actually sends. A context thin enough to make
    `_render_context` raise falls back to the loading STUB, which carries no
    contract at all — which is how a probe can pass on a prompt the real chart
    fails, and why this fixture is complete."""
    return {
        "symbol": "RELIANCE", "exchange": "NSE", "source": "kite",
        "interval": "15m",
        "view": {"from": "a", "to": "b", "bars_visible": 10,
                 "bars_loaded": 100, "history_from": "c"},
        "last_bar": {"t": "b", "o": 1, "h": 2, "l": 1, "c": 1, "v": 1},
        "window": {"open": 1, "close": 1, "change_pct": 0.0,
                   "high": {"p": 2, "t": "b"}, "low": {"p": 1, "t": "b"},
                   "avg_volume": 1, "trajectory": [1, 2]},
        "indicators": [], "drawings": [], "pins": [],
    }


def test_the_research_trade_contract_never_reaches_the_builder() -> None:
    """The half of the chart contract that forbids a buy call is RESEARCH's.

    It rode into execution mode inside `build_context_block` — which the test
    above never built, so the contradiction was invisible to this suite: one
    system message told the model to draft the order the user asked for and,
    further down, to make no buy/sell call and to close by saying this was
    analysis and not advice.
    """
    _execution_mode()
    built = server.build_context_block(_chart_ctx())
    assert "no buy/sell calls" not in built
    assert "analysis, not advice" not in built
    # …and research still gets every word of it.
    server._req.chat_mode = "chat"
    read = server.build_context_block(_chart_ctx())
    assert "no buy/sell calls" in read
    assert "analysis, not advice" in read


def test_an_undeployed_engine_says_so_instead_of_improvising(monkeypatch) -> None:
    """A mode that cannot work must not be offered as though it could.

    The VM runs a sparse checkout of `charto/` only, so `backend` is not
    importable there and the bridge correctly reports zero tools. Nothing
    asked it, so the turn went to the model anyway and the model invented a
    boundary — "I can't place or automate that order here" — for a capability
    that exists and is merely not deployed.
    """
    monkeypatch.setattr(execution_bridge, "_state",
                        {"tried": True, "ok": False, "mods": None,
                         "error": "ModuleNotFoundError: No module named 'backend'"})
    _execution_mode()
    said = server._execution_unavailable_reply()
    assert said and "not available on this server" in said
    assert "No module named 'backend'" in said      # the REAL reason, quoted
    assert "Research mode is unaffected" in said     # and where to go instead
    # research is never gated, and a working engine never trips it
    server._req.chat_mode = "chat"
    assert server._execution_unavailable_reply() is None
