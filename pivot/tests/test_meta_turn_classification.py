"""Regression tests for `_META_QUESTION_RE` / `_followup_turn_kind`.

A live chat-prober run (2026-07-14) found the assistant falsely claiming
an automation "will place a live order automatically" when asked
directly whether it executes live or just registers for confirmation.
Root cause: `_META_QUESTION_RE` required a literal trailing "?", which
real chat input often drops — the message fell through to
`followup_turn_kind() -> None`, skipping the register-not-execute
engine-fact grounding entirely. Fixed by not requiring the trailing "?".
"""
from __future__ import annotations

from backend.services.chat_service import (
    _followup_turn_kind,
    _META_QUESTION_RE,
    _meta_turn_hint,
    _requests_comparison_over_amendment,
)


def test_question_without_trailing_mark_still_classified():
    msg = ("will this place a live order automatically, or just "
           "register something I confirm")
    assert _followup_turn_kind(msg) == "question"


def test_question_with_trailing_mark_still_classified():
    msg = ("Will this place a live order automatically, or just "
           "register something I confirm?")
    assert _followup_turn_kind(msg) == "question"


def test_genuine_amendment_unaffected():
    assert _followup_turn_kind("reduce SL to 3%") is None
    assert _followup_turn_kind("make it 5 lots instead") is None


def test_unanchored_question_still_escapes_amendment_capture():
    # Canonical case from `_is_genuine_dependent_amendment`'s docstring —
    # contains an amendment-verb word ("reduce") but is a free-standing
    # question, not an edit to the draft on screen.
    assert _followup_turn_kind(
        "how can I reduce risk in general investing") == "question"


def test_multi_sentence_message_not_misclassified_as_question():
    # Must not match past its first sentence terminator — a long,
    # multi-sentence message starting with a question word shouldn't be
    # treated as a single short clarifying question.
    msg = ("Will this work. I already decided to go with something "
           "else instead of your approach here for now.")
    assert _followup_turn_kind(msg) != "question"


def test_contracted_negation_question_now_matches():
    """Reported 2026-07-14: "isn't IGL Indraprastha Gas, not pharma?" fell
    through _META_QUESTION_RE (only uncontracted auxiliaries were in the
    alternation) while the plain "is IGL a pharma company, yes or no"
    matched — same question, opposite treatment purely on contraction."""
    assert _META_QUESTION_RE.match("isn't IGL Indraprastha Gas, not pharma?")
    assert _META_QUESTION_RE.match("is IGL a pharma company, yes or no")
    for msg in ("aren't these the same", "doesn't this seem off",
                "can't you just check", "won't this fail",
                "wasn't that already answered"):
        assert _META_QUESTION_RE.match(msg), msg


def test_question_hint_warns_against_borrowing_other_tools_execution_fields():
    """Reported 2026-07-14: asked "does that sell all TCS or just 7
    shares, show me the steps" on a propose_holding_action draft with no
    execution-price concept — the model invented "execution price
    slightly below ₹3,150 to improve fill probability", language that
    only makes sense for a DIFFERENT tool (create_gtt_order's
    limit_price). The question-turn hint must warn against this."""
    class _Draft:
        tool_name = "propose_holding_action"
        draft = {"symbol": "TCS", "quantity": 7}

    hint = _meta_turn_hint("question", _Draft(), "does that sell all TCS?")
    assert "execution price" in hint.content.lower()
    assert "DRAFT JSON" in hint.content


def test_compare_message_with_secondary_amendment_verb_detected():
    """Live report 2026-07-14: "compare me both the baskets we built and
    tell me on the basis of latest news whihc one is better? modify of
    needed to" tripped the amendment-verb regex on "modify" and force-
    re-fired the SAME backtest_workflow tool with an identical card,
    ignoring the comparison ask. The competing-analysis detector must
    catch this exact message so the hard re-emit lock at chat_service's
    4 call sites gets skipped."""
    msg = ("compare me both the baskets we built and tell me on the basis "
           "of latest news whihc one is better? modify of needed to")
    assert _requests_comparison_over_amendment(msg)


def test_analysis_command_variants_detected():
    for msg in (
        "contrast these two strategies for me",
        "which one is better, the IT basket or the banking basket?",
        "rank the baskets we built by risk",
        "what's the difference between these two agents",
        "TCS vs INFY, which is stronger",
    ):
        assert _requests_comparison_over_amendment(msg), msg


def test_genuine_amendments_not_misclassified_as_comparison():
    """The detector must stay narrow — plain amendments (even ones
    containing a question mark) must not get swept into the comparison
    escape hatch, or genuine amendment-forcing regresses."""
    for msg in (
        "make it 5 lots instead",
        "reduce SL to 3%",
        "change the strategy for selling when 50 ema goes below the 200 ema",
        "can you make it 5 lots instead?",
        "modify the quantity to 10 shares",
    ):
        assert not _requests_comparison_over_amendment(msg), msg
