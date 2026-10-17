"""Regression: a clarification answer must resolve against the intent that
SPAWNED the question — not the first (stale) intent in a multi-intent
session.

The reported bug: in one thread a user (1) built an oil basket, (2)
backtested it, (3) asked to build an OPTION strategy → the assistant asked
a clarifying question, (4) answered it — and the assistant re-ran a BASKET
BACKTEST instead of building the option strategy. Root cause: the
clarify-followup hint bound "original intent" to the FIRST user turn in the
window (the oil basket / "backtest it"), and the backtest-followup gate
fired off that stale "backtest" keyword. See _originating_user_intent and
the _is_backtest_clarify_followup gate in chat_service.py.
"""
import pytest

from backend.llm.factory import set_llm_client_for_tests
from backend.llm import LLMResponse
from backend.services.chat_service import (
    ChatService,
    UserContext,
    _BACKTEST_INTENT_RE,
    _looks_like_clarification_followup,
    _originating_user_intent,
)
from backend.services.conversation_store import PendingResolution
from tests.test_chat_service_with_stub_llm import _StubClient, _StubStore

# history EXCLUDES the current message (the router strips the last turn),
# so it ends with the assistant's clarification question.
_MULTI_INTENT_HISTORY = [
    {"role": "user", "content": "construct an oil basket"},
    {"role": "assistant", "content": "Here is your oil basket. [card]"},
    {"role": "user", "content": "backtest it"},
    {"role": "assistant", "content": "Backtest done: +2.29%/yr. [card]"},
    {"role": "user",
     "content": "construct an option strategy for a bullish view on crude"},
    {"role": "assistant",
     "content": "Sure — is your view bullish, bearish, neutral, or a big "
                "move either way?"},
]


def test_originating_intent_is_the_spawning_ask_not_first_in_window():
    """The option-strategy ask (turn just before the question), NOT the
    stale oil-basket first turn."""
    orig = _originating_user_intent(_MULTI_INTENT_HISTORY)
    assert orig == "construct an option strategy for a bullish view on crude"
    # The old (buggy) value was the first user turn — assert we moved off it.
    assert orig != _MULTI_INTENT_HISTORY[0]["content"]


def test_option_clarify_original_intent_is_not_a_backtest():
    """The corrected original intent must NOT trip the backtest gate — that
    is what re-routed the option answer into a basket backtest."""
    orig = _originating_user_intent(_MULTI_INTENT_HISTORY)
    assert _looks_like_clarification_followup(_MULTI_INTENT_HISTORY) is True
    assert _BACKTEST_INTENT_RE.search(orig) is None
    # But the stale first-in-window turns DO look backtest-y (the trap):
    window_backtesty = any(
        _BACKTEST_INTENT_RE.search(h["content"])
        for h in _MULTI_INTENT_HISTORY if h["role"] == "user"
    )
    assert window_backtesty is True


def test_genuine_backtest_clarify_still_attributes_to_backtest():
    """No regression: answering a real backtest clarification still yields a
    backtest-flagged original intent (so the backtest tools stay forced)."""
    history = [
        {"role": "user", "content": "build a golden-cross strategy on TCS"},
        {"role": "assistant", "content": "Built. [card]"},
        {"role": "user",
         "content": "backtest how this would have performed over 5 years"},
        {"role": "assistant",
         "content": "Over what starting capital should I run it?"},
    ]
    orig = _originating_user_intent(history)
    assert orig == "backtest how this would have performed over 5 years"
    assert _BACKTEST_INTENT_RE.search(orig) is not None


def test_originating_intent_falls_back_to_first_user_when_no_prior_turn():
    """Single-intent window (question is the first assistant turn): the
    originating ask is the only preceding user turn."""
    history = [
        {"role": "user", "content": "build me a momentum strategy"},
        {"role": "assistant", "content": "What lookback window?"},
    ]
    assert _originating_user_intent(history) == "build me a momentum strategy"


def test_originating_intent_empty_history():
    assert _originating_user_intent([]) == ""


@pytest.mark.asyncio
async def test_handle_option_clarify_answer_not_forced_into_backtest():
    """End-to-end through handle(): answering the option-strategy clarify in
    a session that ALSO ran a basket backtest earlier must NOT force the
    backtest tool surface, and the injected hint must carry the OPTION intent
    forward — not the stale oil-basket / 'backtest it' turns."""
    store = _StubStore()
    conv = "c_xintent"
    # The option-strategy clarification that is in flight (set when the model
    # asked "bullish/bearish/…"). original_intent = the OPTION ask.
    store.set_pending_resolution(conv, PendingResolution(
        question="Is your view bullish, bearish, neutral, or a big move?",
        options=["Bullish", "Bearish", "Neutral", "Big move"],
        original_intent="construct an option strategy for a bullish view on crude",
        asked_at_iso="2026-07-13T00:00:00Z",
    ))
    stub = _StubClient(queue=[
        LLMResponse(content="Here's a bullish crude call spread. [card]",
                    finish_reason="stop"),
    ])
    set_llm_client_for_tests(stub)
    svc = ChatService(store=store)
    ctx = UserContext(user_id=1, kite_token="x", db=None, holdings=[])

    await svc.handle(
        "bullish, about a month out, moderate risk",
        conv, ctx, history_override=_MULTI_INTENT_HISTORY,
    )
    set_llm_client_for_tests(None)

    assert stub.calls, "LLM was never called (turn was short-circuited)"
    first = stub.calls[0]
    tool_names = {t.name for t in (first["kwargs"].get("tools") or [])}
    # The bug force-NARROWED scope to the backtest-only set; ensure we did NOT.
    assert tool_names != {"backtest_workflow", "backtest_dsl_tree", "ASK_USER"}

    # Isolate the clarify-followup / pending-resolution HINT messages (the
    # ones that echo the "original request") — NOT the whole system blob,
    # which includes system.md (it has its own 'oil basket' example text).
    hint_msgs = [
        (m.content or "") for m in first["messages"]
        if getattr(m, "role", "") == "system"
        and ("ORIGINAL request was" in (m.content or "")
             or "Original intent" in (m.content or ""))
    ]
    assert hint_msgs, "no clarify-followup / pending-resolution hint injected"
    hint_blob = "\n".join(hint_msgs)
    # The forwarded original intent is the OPTION ask, not the oil basket /
    # 'backtest it' — this is the crux of the cross-intent fix.
    assert "option strategy" in hint_blob
    assert "oil basket" not in hint_blob
    assert "backtest it" not in hint_blob
    # And no directive forcing a backtest (that clause is gated on the
    # original intent being a backtest — which it is not here).
    assert "MUST call" not in hint_blob or "backtest" not in hint_blob.split(
        "MUST call", 1)[1][:40]
