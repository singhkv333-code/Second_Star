"""Exhaustive unit tests for the v2 conversation state machine.

Every transition rule in transitions.py has at least one test here.
Adding a new transition rule? Add at least one test.

Run:  pytest tests/chat_v2/test_transitions.py -v
"""
from __future__ import annotations

import pytest

from backend.chat_v2.classifiers import classify
from backend.chat_v2.events import (
    ActivationConfirmed,
    AffirmativeAck,
    Amendment,
    BuildIntent,
    CancelIntent,
    CapabilityQuestion,
    ClarificationAnswer,
    ClarificationAsked,
    FillerReply,
    IndependentIntent,
    ModeOverride,
    ReadIntent,
    TextResponse,
    ToolEmitted,
    TurnEnd,
    TurnStart,
)
from backend.chat_v2.state import ConvContext, ConvState, MacroKind
from backend.chat_v2.transitions import transition


def ctx(**kwargs):
    """Helper: build a ConvContext with sensible defaults."""
    base = dict(conv_id="test")
    base.update(kwargs)
    return ConvContext(**base)


# ─────────────────────────── TurnStart ──────────────────────────────


class TestTurnStart:

    def test_empty_history_resets_to_idle(self):
        c = ctx(state=ConvState.DRAFTING, macro_kind=MacroKind.WORKFLOW,
                macro_draft={"x": 1})
        c = transition(c, TurnStart("hi", history_is_empty=True))
        assert c.state == ConvState.IDLE
        assert c.macro_kind is None
        assert c.macro_draft is None

    def test_normal_turn_increments_count(self):
        c = ctx(turn_count=5)
        c = transition(c, TurnStart("hi"))
        assert c.turn_count == 6

    def test_ages_discarded_drafts(self):
        c = ctx()
        c.push_discarded(MacroKind.WORKFLOW, "propose_workflow", "old")
        assert c.discarded_drafts[0].turns_ago == 0
        c = transition(c, TurnStart("hello"))
        assert c.discarded_drafts[0].turns_ago == 1


# ─────────────────────────── ModeOverride ───────────────────────────


class TestModeOverride:

    def test_agent_pill_forces_drafting_workflow(self):
        c = ctx(state=ConvState.IDLE)
        c = transition(c, ModeOverride(mode="agent"))
        assert c.state == ConvState.DRAFTING
        assert c.macro_kind == MacroKind.WORKFLOW
        assert c.macro_tool == "propose_workflow"

    def test_backtest_pill_forces_drafting_backtest(self):
        c = ctx(state=ConvState.EXPLORING)
        c = transition(c, ModeOverride(mode="backtest"))
        assert c.state == ConvState.DRAFTING
        assert c.macro_kind == MacroKind.BACKTEST

    def test_pill_evicts_different_kind_to_discarded(self):
        c = ctx(state=ConvState.DRAFTING, macro_kind=MacroKind.ORDER,
                macro_tool="place_market_order",
                macro_draft={"symbol": "RELIANCE"},
                draft_summary="market buy RELIANCE")
        c = transition(c, ModeOverride(mode="agent"))
        assert c.state == ConvState.DRAFTING
        assert c.macro_kind == MacroKind.WORKFLOW
        assert len(c.discarded_drafts) == 1
        assert c.discarded_drafts[0].summary == "market buy RELIANCE"


# ─────────────────────────── CancelIntent ──────────────────────────


class TestCancel:

    def test_cancel_from_drafting(self):
        c = ctx(state=ConvState.DRAFTING, macro_kind=MacroKind.WORKFLOW,
                macro_draft={"x": 1})
        c = transition(c, CancelIntent("never mind"))
        assert c.state == ConvState.CANCELLED
        assert c.macro_draft is None

    def test_cancel_from_clarification(self):
        c = ctx(state=ConvState.AWAITING_CLARIFICATION,
                pending_clarification_text="how much?")
        c = transition(c, CancelIntent("scratch it"))
        assert c.state == ConvState.CANCELLED
        assert c.pending_clarification_text is None


# ─────────────────────────── AffirmativeAck ─────────────────────────


class TestAffirmative:

    def test_affirmative_does_not_change_state(self):
        # The pipeline interprets affirmatives based on state; the
        # transition function leaves state alone.
        for state in [ConvState.IDLE, ConvState.EXPLORING,
                      ConvState.DRAFTING, ConvState.AWAITING_CLARIFICATION]:
            c = ctx(state=state)
            c2 = transition(c, AffirmativeAck("ok"))
            assert c2.state == state, f"affirmative changed state from {state}"


# ─────────────────────────── FillerReply ────────────────────────────


class TestFiller:

    def test_filler_does_not_amend_draft(self):
        c = ctx(state=ConvState.DRAFTING, macro_kind=MacroKind.WORKFLOW,
                macro_draft={"name": "x"})
        c2 = transition(c, FillerReply("thanks"))
        assert c2.state == ConvState.DRAFTING
        # Draft is preserved unchanged.
        assert c2.macro_draft == c.macro_draft


# ─────────────────────────── CapabilityQuestion ────────────────────


class TestCapability:

    def test_idle_to_exploring(self):
        c = ctx(state=ConvState.IDLE)
        c = transition(c, CapabilityQuestion("can I try without real money?"))
        assert c.state == ConvState.EXPLORING

    def test_drafting_does_not_evict(self):
        c = ctx(state=ConvState.DRAFTING, macro_kind=MacroKind.WORKFLOW,
                macro_draft={"x": 1})
        c2 = transition(c, CapabilityQuestion("does Pivot support futures?"))
        # Capability questions are about the system, not the draft —
        # don't push the draft to discarded.
        assert c2.state == ConvState.DRAFTING
        assert c2.macro_draft == c.macro_draft


# ─────────────────────────── IndependentIntent ─────────────────────


class TestIndependent:

    def test_drafting_to_exploring_with_eviction(self):
        c = ctx(state=ConvState.DRAFTING, macro_kind=MacroKind.WORKFLOW,
                macro_tool="propose_workflow",
                macro_draft={"name": "NIFTYBEES agent"},
                draft_summary="NIFTYBEES RSI<30 buy")
        c = transition(c, IndependentIntent("what's the RSI of RELIANCE?"))
        assert c.state == ConvState.EXPLORING
        assert c.macro_kind is None
        assert c.macro_draft is None
        assert len(c.discarded_drafts) == 1
        assert c.discarded_drafts[0].summary == "NIFTYBEES RSI<30 buy"

    def test_clarification_to_exploring(self):
        c = ctx(state=ConvState.AWAITING_CLARIFICATION,
                pending_clarification_text="how much?")
        c = transition(c, IndependentIntent("what's TCS at?"))
        assert c.state == ConvState.EXPLORING
        assert c.pending_clarification_text is None


# ─────────────────────────── BuildIntent ────────────────────────────


class TestBuildIntent:

    def test_build_workflow_pushes_to_drafting(self):
        c = ctx(state=ConvState.IDLE)
        c = transition(c, BuildIntent("build me an agent", likely_macro="workflow"))
        assert c.state == ConvState.DRAFTING
        assert c.macro_kind == MacroKind.WORKFLOW
        assert c.macro_tool == "propose_workflow"

    def test_build_order_pushes_to_drafting(self):
        c = ctx(state=ConvState.IDLE)
        c = transition(c, BuildIntent("buy 5 RELIANCE", likely_macro="order"))
        assert c.state == ConvState.DRAFTING
        assert c.macro_kind == MacroKind.ORDER

    def test_build_clears_pending_clarification(self):
        c = ctx(state=ConvState.AWAITING_CLARIFICATION,
                pending_clarification_text="leftover")
        c = transition(c, BuildIntent("build a new agent"))
        assert c.state == ConvState.DRAFTING
        assert c.pending_clarification_text is None


# ─────────────────────────── ReadIntent ─────────────────────────────


class TestReadIntent:

    def test_idle_to_exploring(self):
        c = ctx(state=ConvState.IDLE)
        c = transition(c, ReadIntent("show RELIANCE"))
        assert c.state == ConvState.EXPLORING

    def test_activated_to_exploring(self):
        c = ctx(state=ConvState.ACTIVATED)
        c = transition(c, ReadIntent("show portfolio"))
        assert c.state == ConvState.EXPLORING


# ─────────────────────────── ToolEmitted ────────────────────────────


class TestToolEmitted:

    def test_propose_workflow_pushes_to_drafting(self):
        c = ctx(state=ConvState.EXPLORING)
        c = transition(c, ToolEmitted(
            tool_name="propose_workflow",
            args={"name": "NIFTYBEES agent",
                  "steps": [{"step_type": "trigger.indicator"}]},
        ))
        assert c.state == ConvState.DRAFTING
        assert c.macro_kind == MacroKind.WORKFLOW
        assert c.macro_tool == "propose_workflow"
        assert c.macro_draft is not None

    def test_propose_holding_action_pushes_to_drafting_holding(self):
        c = ctx(state=ConvState.EXPLORING)
        c = transition(c, ToolEmitted(
            tool_name="propose_holding_action",
            args={"symbol": "INFY", "side": "sell"},
        ))
        assert c.state == ConvState.DRAFTING
        assert c.macro_kind == MacroKind.HOLDING
        assert "INFY" in c.focus_symbols

    def test_ask_user_pushes_to_awaiting_clarification(self):
        c = ctx(state=ConvState.EXPLORING)
        c = transition(c, ToolEmitted(
            tool_name="ASK_USER",
            args={"question": "How much per run?"},
        ))
        assert c.state == ConvState.AWAITING_CLARIFICATION
        assert c.pending_clarification_text == "How much per run?"

    def test_get_indicator_records_last_tool(self):
        c = ctx(state=ConvState.EXPLORING)
        c = transition(c, ToolEmitted(
            tool_name="get_indicator",
            args={"symbol": "RELIANCE", "indicator": "rsi", "period": 14},
        ))
        assert c.last_tool == "get_indicator"
        assert c.last_tool_args["indicator"] == "rsi"
        assert "RELIANCE" in c.focus_symbols

    def test_get_live_price_idle_to_exploring(self):
        c = ctx(state=ConvState.IDLE)
        c = transition(c, ToolEmitted(
            tool_name="get_live_price",
            args={"symbol": "TCS"},
        ))
        assert c.state == ConvState.EXPLORING
        assert c.last_tool == "get_live_price"


class TestClarificationAsked:

    def test_prose_question_pushes_to_clarification(self):
        c = ctx(state=ConvState.EXPLORING)
        c = transition(c, ClarificationAsked(
            "Should I map AI to the IT sector? How much per run?"
        ))
        assert c.state == ConvState.AWAITING_CLARIFICATION
        assert "IT sector" in c.pending_clarification_text


class TestActivation:

    def test_activation_confirmed_moves_to_activated(self):
        c = ctx(state=ConvState.DRAFTING, macro_kind=MacroKind.WORKFLOW,
                macro_draft={"name": "x"})
        c = transition(c, ActivationConfirmed(
            workflow_id="wf_1", summary="NIFTYBEES RSI<30 buy"
        ))
        assert c.state == ConvState.ACTIVATED
        assert c.macro_draft is None
        assert "NIFTYBEES RSI<30 buy" in c.activations[0]


# ───────────────────── End-to-end multi-event scenarios ──────────────


class TestScenarios:

    def test_clarify_phase_affirmative_does_not_change_state(self):
        """Reproduces the bug from the user's trace:
            user: build me an agent ...
            bot: should I map AI to IT?
            user: sure
        The 'sure' must NOT short-circuit; the state stays in
        AWAITING_CLARIFICATION so the LLM can resolve it.
        """
        c = ctx(state=ConvState.AWAITING_CLARIFICATION,
                pending_clarification_text="map AI to IT?")
        c = transition(c, AffirmativeAck("sure"))
        assert c.state == ConvState.AWAITING_CLARIFICATION

    def test_post_eviction_affirmative_no_resurrection(self):
        """The v1 s_draft T5 bug: after independent intent evicts the
        draft, 'ok' must not resurrect it. In v2, the draft is in
        discarded_drafts, not active. The pipeline's ack response
        must not re-emit the macro tool."""
        c = ctx(state=ConvState.DRAFTING, macro_kind=MacroKind.WORKFLOW,
                macro_tool="propose_workflow",
                macro_draft={"name": "NIFTYBEES agent"},
                draft_summary="NIFTYBEES RSI<30 buy")
        c = transition(c, IndependentIntent("what's RSI of RELIANCE?"))
        assert c.state == ConvState.EXPLORING
        c = transition(c, AffirmativeAck("ok"))
        # State is still EXPLORING; macro_draft is gone.
        assert c.state == ConvState.EXPLORING
        assert c.macro_draft is None
        # The draft is preserved as discarded for honest recall.
        assert len(c.discarded_drafts) == 1

    def test_full_build_amend_activate_flow(self):
        """End-to-end: idle -> build -> draft -> amend -> ack -> activate."""
        c = ctx()
        # T1: build intent
        c = transition(c, TurnStart("Build me an agent that buys NIFTYBEES on RSI<30"))
        c = transition(c, BuildIntent(
            "Build me an agent that buys NIFTYBEES on RSI<30",
            likely_macro="workflow",
        ))
        c = transition(c, ToolEmitted(
            tool_name="propose_workflow",
            args={"name": "NIFTYBEES RSI buy",
                  "steps": [{"step_type": "trigger.indicator",
                             "config": {"symbol": "NIFTYBEES"}},
                            {"step_type": "action.place_order",
                             "config": {"symbol": "NIFTYBEES", "quantity": 1}}]},
        ))
        assert c.state == ConvState.DRAFTING
        assert c.macro_kind == MacroKind.WORKFLOW

        # T2: amend
        c = transition(c, TurnStart("make it 5 shares"))
        c = transition(c, Amendment("make it 5 shares"))
        # Stays in DRAFTING; draft preserved.
        assert c.state == ConvState.DRAFTING

        # LLM re-emits with quantity=5
        c = transition(c, ToolEmitted(
            tool_name="propose_workflow",
            args={"name": "NIFTYBEES RSI buy",
                  "steps": [{"step_type": "action.place_order",
                             "config": {"symbol": "NIFTYBEES", "quantity": 5}}]},
        ))
        assert c.macro_draft["steps"][0]["config"]["quantity"] == 5

        # T3: ack
        c = transition(c, TurnStart("ok"))
        c = transition(c, AffirmativeAck("ok"))
        assert c.state == ConvState.DRAFTING  # unchanged

        # T4: FE activate click
        c = transition(c, ActivationConfirmed("wf_1", "NIFTYBEES RSI<30 buy"))
        assert c.state == ConvState.ACTIVATED


# ────────────────────── Classifier coverage ─────────────────────────


class TestClassifiers:

    @pytest.mark.parametrize("msg", [
        "ok", "yes", "sure", "yep", "go ahead", "do it",
        "Ok activate", "yes activate it", "great", "perfect",
    ])
    def test_pure_affirmative(self, msg):
        c = ctx(state=ConvState.IDLE)
        ev = classify(msg, c)
        assert isinstance(ev, AffirmativeAck), f"{msg!r} not classified as affirmative"

    @pytest.mark.parametrize("msg", [
        "thanks", "cool", "got it", "nice", "awesome", "noted",
    ])
    def test_filler(self, msg):
        c = ctx(state=ConvState.IDLE)
        ev = classify(msg, c)
        assert isinstance(ev, FillerReply), f"{msg!r} not classified as filler"

    @pytest.mark.parametrize("msg", [
        "cancel that", "scratch it", "never mind", "actually nevermind",
        "forget it", "abort", "undo",
    ])
    def test_cancel(self, msg):
        c = ctx(state=ConvState.DRAFTING)
        ev = classify(msg, c)
        assert isinstance(ev, CancelIntent), f"{msg!r} not classified as cancel"

    @pytest.mark.parametrize("msg", [
        "Can I try this without real money?",
        "Do you support options?",
        "What does Pivot actually do?",
        "How does this work?",
        "Is there a way to backtest first?",
        "Can you trade futures?",
    ])
    def test_capability_question(self, msg):
        c = ctx(state=ConvState.IDLE)
        ev = classify(msg, c)
        assert isinstance(ev, CapabilityQuestion), f"{msg!r} not classified as capability"

    @pytest.mark.parametrize("msg", [
        "Build me an agent that buys NIFTYBEES",
        "Create an automation for daily SIP",
        "Set me up to buy 5 RELIANCE every Monday",
        "Make me a workflow",
    ])
    def test_build_intent(self, msg):
        c = ctx(state=ConvState.IDLE)
        ev = classify(msg, c)
        assert isinstance(ev, BuildIntent), f"{msg!r} not classified as build"

    @pytest.mark.parametrize("msg", [
        "What's the RSI of RELIANCE?",
        "Show me TCS",
    ])
    def test_independent_intent_while_drafting(self, msg):
        c = ctx(state=ConvState.DRAFTING, macro_kind=MacroKind.WORKFLOW)
        ev = classify(msg, c)
        assert isinstance(ev, IndependentIntent), f"{msg!r} not flagged independent"

    def test_amendment_default_in_drafting(self):
        c = ctx(state=ConvState.DRAFTING, macro_kind=MacroKind.WORKFLOW)
        ev = classify("make it 5 shares", c)
        assert isinstance(ev, Amendment)

    def test_clarification_answer_in_clarifying(self):
        c = ctx(state=ConvState.AWAITING_CLARIFICATION,
                pending_clarification_text="how much?")
        ev = classify("about 50000", c)
        assert isinstance(ev, ClarificationAnswer)
