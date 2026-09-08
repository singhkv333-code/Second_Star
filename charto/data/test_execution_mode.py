"""Execution mode wires Charto's side chat onto Pivot's automation engine.

These tests are about the SEAM, not about Pivot's builder — the builder has
its own suite in pivot/tests. What can break here is the joinery: the wrong
prompt on the wire, a tool advertised that nothing can dispatch, an alert
quietly routed into an order draft, or a card whose steps still read as
engineering ids.
"""
from __future__ import annotations

import json

import execution_bridge
import dataserver as server


def _execution_mode() -> None:
    server._req.chat_mode = "execution"


def test_execution_context_replaces_the_analysis_contract() -> None:
    """The builder's contract is on the wire and the analyst's is not.

    Pinned by what the contract SAYS rather than by its heading: a title is
    the easiest thing to rename and the least load-bearing thing in the file,
    and a test that only guards the title passes a rewrite that deleted every
    rule under it.
    """
    _execution_mode()
    block = "\n\n".join((server.FORMAT_RULES, server._execution_system()))
    assert "register_plan" in block and "save_strategy" in block
    assert "Every why-did-it-move question" not in block


def test_the_contract_tells_it_to_act_before_it_asks() -> None:
    """The single behaviour this surface was rebuilt around.

    Measured before the rewrite: "buy the top 10 stocks" returned four bullet
    points and ZERO tool calls, because three separate borrowed rules told the
    model to open with ASK_USER. Nothing on this wire can render a clarify
    card, so the question was pure loss. If the instruction that replaced them
    ever goes missing, the surface silently returns to asking — which reads
    like a model regression rather than a prompt one, and is why it is pinned
    here.
    """
    _execution_mode()
    block = server._execution_system()
    assert "NEVER open with a question" in block
    # And the rules that said the opposite are gone with the modules that
    # carried them. `sips`, `order_sizing` and `stoploss` between them named
    # eleven tools this wire does not have, three of them as ASK_USER-first
    # instructions.
    for absent in ("create_sip", "create_dip_buy", "create_sl_order",
                   "get_market_data"):
        assert absent not in block, f"{absent} is taught but not callable"
    # The sentence that actually produced the menu. `backtest.md` still says
    # "do NOT `ASK_USER` to confirm …", which AGREES with the rule above — so
    # the bare name is not what to pin. What must never come back is an
    # instruction to reach for a clarify tool this wire does not have.
    assert "Call ASK_USER with ONE question" not in block
    assert "call ASK_USER" not in block


def test_no_tool_on_this_wire_tells_the_model_to_ask_the_interval() -> None:
    """The three-way interval contradiction, pinned from the losing side.

    `propose_dsl_workflow`'s `interval` parameter used to end "If user did NOT
    pin a timeframe, ASK — do not guess." Correct in Pivot's chat, which has a
    clarify card to ask WITH. Wrong here three times: no clarify tool exists on
    this wire, the adapter's first rule is never to open with a question, and
    the composer's interval is already in the model's context.

    It was also WINNING: two prompt-level rules say never ask, but both sit
    ~9k tokens away while that clause is attached to the argument being filled
    in. The observed failure — "buy 10 INFY when RSI < 30" answered with
    "which timeframe?" — costs a visible round plus two hidden translation
    hops on the rebuild.

    Asserted against the whole wire rather than the one tool, because the next
    borrowed description to carry an ask-instruction should fail here too.
    """
    _execution_mode()
    blob = json.dumps(server._tools_for_request())
    assert "ASK — do not guess" not in blob
    assert "ASK - do not guess" not in blob


def test_retargeting_does_not_mutate_pivots_own_registry() -> None:
    """Pivot's chat reads the same dict object in-process.

    `_retarget` must copy. If it ever mutated in place, this seam would edit
    the behaviour of a product that is not ours — the exact failure the
    bridge's docstring says it exists to avoid.
    """
    import execution_bridge as eb
    st = eb._ensure_pivot()
    if not st["ok"]:
        return
    eb.tools()          # force the rewrite path
    original = (st["mods"]["ALL_TOOLS"]["propose_dsl_workflow"]["function"]
                ["parameters"]["properties"]["interval"]["description"])
    assert "ASK — do not guess" in original


def test_the_absent_tools_note_names_only_real_absent_tools() -> None:
    """Two false positives that made the note worse than the problem.

    The matcher was `name in text`, an unanchored substring against Pivot's
    106-tool registry. It caught:

      · `calculate` — a real tool name AND an ordinary English word. Its only
        "occurrence" was "how the entry condition is calculated", so the note
        declared a verb unavailable.
      · `place_order` — 10 occurrences, 9 of them `action.place_order`, which
        is not a tool but the DSL STEP every armable strategy must contain
        (`strategies.parse_draft` matches that exact string). The note said
        "NOT ON THIS SURFACE: place_order" in the same payload that tells the
        model to append `action.place_order` to a draft.

    Backticks are the signal: the packs cite tools in them and step types
    dotted. Anything that reintroduces substring matching fails here.
    """
    import execution_bridge as eb
    st = eb._ensure_pivot()
    if not st["ok"]:
        return
    modules = st["mods"]["assembler"].load_prompt_modules(
        list(eb.PROMPT_MODULES))
    note = eb._absent_tools_note(modules, st["mods"]["ALL_TOOLS"])
    assert "`calculate`" not in note
    assert "`place_order`" not in note
    # …while still catching the four it exists for.
    for macro in ("propose_scheduled_order", "propose_threshold_order",
                  "propose_holding_action", "propose_basket_allocation"):
        assert f"`{macro}`" in note


def test_the_absent_tools_note_does_not_redirect_at_an_unarmable_shape() -> None:
    """The redirect must not send a clock ask at `propose_workflow`.

    It used to read "`propose_workflow` (a schedule or several steps)", which
    aimed every recurring-clock ask at the one draft shape the runtime
    refuses: `strategies.parse_draft` raises Unbuildable on
    `trigger.schedule`/`trigger.cron`. The note was contradicting
    `save_strategy`'s own description on the same wire and steering the model
    into a failure it could only recover from with another LLM round.
    """
    import execution_bridge as eb
    st = eb._ensure_pivot()
    if not st["ok"]:
        return
    modules = st["mods"]["assembler"].load_prompt_modules(
        list(eb.PROMPT_MODULES))
    note = eb._absent_tools_note(modules, st["mods"]["ALL_TOOLS"])
    assert "`propose_workflow` (a schedule" not in note
    # and it must say the honest thing about a clock instead
    assert "cannot be armed here" in note


def test_a_tool_the_pack_names_but_the_wire_lacks_is_corrected() -> None:
    """Absent tools are named as absent, never left implying a route.

    `workflows.md` is included whole and routes past four macros this surface
    does not carry. Rewriting Pivot's file would fork a contract two products
    read, so the seam appends the correction instead — and the correction is
    computed from Pivot's registry, so this test also fails if that list ever
    drifts out of sync with what is really on the wire.
    """
    _execution_mode()
    block = server._execution_system()
    names = {t["name"] for t in server._tools_for_request()}
    for macro in ("propose_scheduled_order", "propose_holding_action"):
        assert macro not in names          # really absent
        if macro in block:                 # and if named, named as absent
            assert "NOT ON THIS SURFACE" in block


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


def test_execution_mode_adds_the_builder_and_the_research_reads() -> None:
    """Execution mode is a superset of the reads, not a subset.

    The research tools used to be OFF this surface, and that is what made a
    question about a COMPANY unanswerable here: the model had price features
    and nothing else, so "the best AI stock for next month" had no way to
    learn anything about a business. A plan is only as good as what was read
    before it.
    """
    _execution_mode()
    names = {tool["name"] for tool in server._tools_for_request()}
    assert "propose_dsl_workflow" in names
    assert "backtest_dsl_tree" in names
    assert {"search_news", "explain_move", "get_results", "compare_symbols",
            "get_peers"} <= names


def test_a_researched_selection_has_somewhere_to_land() -> None:
    """Registration for the shape `save_strategy` cannot hold.

    `save_strategy` takes ONE symbol watched by one condition tree. Every
    "buy the top 10" / "put 3 lakh to work" ask is several legs, most with no
    condition at all, and before `register_plan` those turns ended with a
    rendered card and nothing stored — the research was done and then thrown
    away when the turn ended.
    """
    _execution_mode()
    names = {tool["name"] for tool in server._tools_for_request()}
    assert {"register_plan", "list_plans", "plan_status"} <= names


def test_chat_mode_is_untouched_by_the_execution_surface() -> None:
    """Chat reads; it does not commit.

    Both halves are load-bearing and the second was found by this test failing
    after `register_plan` was added: defining a tool in `TOOLS` puts it on the
    chat wire too, and chat answering "what should I buy" with a registered
    basket would commit on the surface whose promise is that it only reads.
    """
    server._req.chat_mode = "chat"
    names = {tool["name"] for tool in server._tools_for_request()}
    assert "explain_move" in names
    assert "propose_dsl_workflow" not in names
    assert not (names & server._EXECUTION_ONLY_TOOLS)


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
    # The adapter survives an unavailable engine — it is Charto's own text,
    # not Pivot's, so a mode that cannot build can still explain itself.
    assert "NEVER open with a question" in execution_bridge.system_prompt()
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
