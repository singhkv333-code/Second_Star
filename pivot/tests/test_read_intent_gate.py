"""Unit tests for `_read_intent_gate`'s comparison/ownership branches.

Added 2026-07-14 alongside a fix for two fabrication bugs found in the
50-prompt correctness eval: a two-stock comparison ("BAJFINANCE vs
BAJAJFINSV") cited precise numbers with zero tools called, and a
promoter-pledge question ("ZEEL") invented a pledge % Pivot doesn't
track. Both are read-intent gates, same mechanism as the pre-existing
lifecycle/portfolio/series/single-analyse gates in this module.
"""
from __future__ import annotations

from backend.services.chat_service import _named_symbol_count, _read_intent_gate

_ALL_TOOLS = {
    "compare_performance", "fetch_fundamentals", "get_market_data",
    "get_symbol_news", "get_indicators", "get_correlation_matrix",
    "manage_automation", "get_portfolio", "query_financials",
}


def test_named_symbol_count_ignores_stopwords():
    assert _named_symbol_count("is a SIP better than an ETF") == 0
    assert _named_symbol_count("BAJFINANCE vs BAJAJFINSV") == 2
    assert _named_symbol_count("compare TCS and INFY") == 2


def test_comparison_gate_fires_for_two_stock_comparison():
    gate = _read_intent_gate("BAJFINANCE vs BAJAJFINSV which is better",
                              _ALL_TOOLS)
    assert gate is not None
    names, tool_choice, directive = gate
    assert "compare_performance" in names
    assert tool_choice == "required"
    assert "compare_performance" in directive


def test_comparison_gate_skips_generic_comparison_no_symbols():
    gate = _read_intent_gate("is a SIP better than a lump sum investment",
                              _ALL_TOOLS)
    assert gate is None


def test_ownership_gate_fires_for_pledge_question():
    gate = _read_intent_gate("what is ZEEL's promoter pledge percentage",
                              _ALL_TOOLS)
    assert gate is not None
    names, tool_choice, directive = gate
    assert "fetch_fundamentals" in names
    assert tool_choice == "required"
    assert "pledge" in directive.lower()
    assert "not track" in directive.lower() or "isn't tracked" in directive.lower()


def test_ownership_gate_fires_for_shareholding_pattern():
    gate = _read_intent_gate("show me RELIANCE's shareholding pattern",
                              _ALL_TOOLS)
    assert gate is not None
    assert "fetch_fundamentals" in gate[0]


def test_no_gate_fires_for_unrelated_message():
    assert _read_intent_gate("why did the market fall today",
                              _ALL_TOOLS) is None


def test_single_analyse_gate_still_wins_over_comparison_when_no_symbols():
    # "compare" appears but with no second symbol-shaped token — single
    # analyse's own comparison-marker exclusion still applies, so this
    # should fall through to no gate rather than the analyse gate either
    # (analyse gate requires "analyse"/"analysis of"/"deep dive").
    assert _read_intent_gate("compare this to what I already know",
                              _ALL_TOOLS) is None
