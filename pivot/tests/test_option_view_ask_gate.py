"""Regression tests for `_is_option_view_ask`.

Reported 2026-07-14: "create me a bullish option strategy on nifty"
intermittently answered with a hedged "I can't provide live data" reply
instead of calling `suggest_option_strategy`, even though the identical
phrasing succeeds in other sessions. Root cause: a VIEW-based option ask
(a directional/volatility stance, not a named template like "iron
condor") had no deterministic tool-forcing gate — `_is_named_option_build`
only fires for named templates — so `agent_tool_choice` stayed "auto" and
the model sometimes escaped to a cautious non-answer instead of calling
the tool. This is a routing gap, not a live-data outage.
"""
from __future__ import annotations

from backend.services.chat_service import _is_named_option_build, _is_option_view_ask


def test_bullish_view_ask_on_nifty_detected():
    assert _is_option_view_ask("create me a bullish option strategy on nifty")


def test_bearish_view_ask_on_banknifty_detected():
    assert _is_option_view_ask(
        "build a bearish strategy on banknifty using options"
    )


def test_plain_price_query_not_a_view_ask():
    assert not _is_option_view_ask("what is the price of nifty")


def test_named_template_build_is_not_double_claimed_as_view_ask():
    """"iron condor" is a named template — `_is_named_option_build`
    already forces the tool for it; `_is_option_view_ask` must stay
    False so the two gates don't fight over which tool to force."""
    msg = "build me an iron condor on nifty"
    assert _is_named_option_build(msg)
    assert not _is_option_view_ask(msg)


def test_view_word_without_underlying_not_a_view_ask():
    assert not _is_option_view_ask("suggest a bullish options strategy")


def test_view_word_without_build_verb_not_a_view_ask():
    assert not _is_option_view_ask("nifty options look bullish today")
