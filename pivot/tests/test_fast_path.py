"""Tests for the pre-LLM fast-path classifier.

The single most important property to lock in: it MUST NOT match
messages that have real content beyond the greeting. A regression
here silently routes "hello, what's RELIANCE's price" away from the
LLM and we'd serve canned replies to real questions.
"""
from __future__ import annotations

import pytest

from backend.services.fast_path import try_fast_path


class TestGreetings:
    @pytest.mark.parametrize("message", [
        "hi", "Hi", "HI",
        "hello", "Hello.",
        "hey", "Hey!",
        "yo",
        "namaste", "Namaste!",
        "good morning", "Good Morning",
        "good evening", "Good evening.",
        "good night",
    ])
    def test_matches_pure_greeting(self, message: str) -> None:
        result = try_fast_path(message)
        assert result is not None
        assert "build an agent" in result.lower() or "check a price" in result.lower()


class TestThanks:
    @pytest.mark.parametrize("message", [
        "thanks", "Thanks!", "thank you", "thx", "ty", "cheers",
        "appreciate it",
    ])
    def test_matches_thanks(self, message: str) -> None:
        result = try_fast_path(message)
        assert result is not None
        assert "anytime" in result.lower()


class TestHelpQueries:
    @pytest.mark.parametrize("message", [
        "what can you do", "What can you do?",
        "/help", "help",
        "how do you work",
        "what is pivot",
        "capabilities",
    ])
    def test_matches_help(self, message: str) -> None:
        result = try_fast_path(message)
        assert result is not None
        assert "indian stocks" in result.lower()
        assert "agents" in result.lower()


class TestRealQueriesPassThrough:
    """The critical regression-guard: real questions MUST return None."""

    @pytest.mark.parametrize("message", [
        # Greetings + content — real intent attached
        "hello, what's RELIANCE's price",
        "hi can you check my portfolio",
        "hey what's the market doing",
        "good morning, show me INFY",
        # Help phrases as part of a longer ask
        "help me build an agent",
        "what can you do for stop-loss orders",
        "show me what you can do for INFY",
        # Real intents that mention "thanks" or "hello" mid-text
        "thanks for the data, now buy 10 RELIANCE",
        # Trading-language inputs that should never fast-path
        "buy 10 reliance",
        "what's the price of TCS",
        "every weekday at 3:55 PM IST buy 10 RELIANCE",
        "backtest pe_ratio < 15 from 2020 to 2024",
        "RELIANCE",
        "$INFY",
    ])
    def test_does_not_match_real_query(self, message: str) -> None:
        assert try_fast_path(message) is None


class TestNormalization:
    def test_empty_message_returns_none(self) -> None:
        assert try_fast_path("") is None
        assert try_fast_path("   ") is None
        assert try_fast_path(None) is None  # type: ignore[arg-type]

    def test_trailing_punctuation_does_not_break_match(self) -> None:
        for variant in ["hi!", "hi.", "hi?", "hi!!!", "hi ,"]:
            assert try_fast_path(variant) is not None, variant

    def test_extra_internal_whitespace_collapses(self) -> None:
        # "good   morning" → "good morning" → match
        assert try_fast_path("good   morning") is not None
        assert try_fast_path("good\tmorning") is not None
