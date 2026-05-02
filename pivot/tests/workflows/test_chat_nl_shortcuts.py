"""Tests for natural-language shortcut detection in the chat router (#54).

Without these patterns the user can only run a backtest via:
- the dedicated Backtest tab in pivot-next, or
- the explicit slash command `/expr-backtest …`

The hotfix adds NL detection so typing `backtest pe < 15 from 2020 to 2024`
in chat short-circuits to the deterministic backtest handler. This works
even when the LLM provider is down (the slash router runs before Sarvam).

We import the regex objects directly and assert their matches; the
underlying handlers (`_run_expr_backtest`, `_run_expr_screen`) talk to
asyncpg and aren't worth booting in unit scope.
"""
from __future__ import annotations

from backend.routers.chat import _NL_BT_RE, _NL_SCREEN_RE, _normalize_date_input


# ── Backtest patterns ──────────────────────────────────────────────


def test_full_dates_with_explicit_rebalance() -> None:
    m = _NL_BT_RE.match(
        "backtest pe_ratio < 15 from 2020-01-01 to 2024-12-31 rebalance Q",
    )
    assert m is not None
    assert m.group("expr").strip() == "pe_ratio < 15"
    assert m.group("start") == "2020-01-01"
    assert m.group("end") == "2024-12-31"
    assert m.group("rb") == "Q"


def test_year_only_dates() -> None:
    m = _NL_BT_RE.match(
        "backtest roe > 18 from 2018 to 2024",
    )
    assert m is not None
    assert m.group("expr").strip() == "roe > 18"
    assert m.group("start") == "2018"
    assert m.group("end") == "2024"


def test_word_rebalance_quarterly() -> None:
    m = _NL_BT_RE.match(
        "backtest pe_ratio < 15 AND roe > 18 from 2018 to 2024 quarterly",
    )
    assert m is not None
    assert m.group("word") == "quarterly"


def test_run_a_backtest_prefix_accepted() -> None:
    m = _NL_BT_RE.match(
        "run a backtest on roe > 18 from 2018 to 2024 monthly",
    )
    assert m is not None
    assert m.group("expr").strip() == "roe > 18"
    assert m.group("word") == "monthly"


def test_normalize_date_year_only() -> None:
    assert _normalize_date_input("2020") == "2020-01-01"


def test_normalize_date_full_pass_through() -> None:
    assert _normalize_date_input("2020-06-15") == "2020-06-15"


def test_no_match_when_dates_missing() -> None:
    assert _NL_BT_RE.match("backtest pe_ratio < 15") is None


def test_no_match_for_random_chat() -> None:
    assert _NL_BT_RE.match("what is the market like today") is None
    assert _NL_BT_RE.match("buy reliance") is None


# ── Screen patterns ────────────────────────────────────────────────


def test_screen_simple() -> None:
    m = _NL_SCREEN_RE.match("screen roe > 18")
    assert m is not None
    assert m.group("expr").strip() == "roe > 18"


def test_find_companies_where_pattern() -> None:
    m = _NL_SCREEN_RE.match("find companies where pe_ratio < 15")
    assert m is not None
    assert m.group("expr").strip() == "pe_ratio < 15"


def test_screen_with_as_of_date() -> None:
    m = _NL_SCREEN_RE.match("screen pe_ratio < 15 as of 2024-06-30")
    assert m is not None
    assert m.group("date") == "2024-06-30"


def test_screen_no_match_for_buy_intent() -> None:
    """Don't accidentally match 'buy reliance' as a screen."""
    assert _NL_SCREEN_RE.match("buy reliance") is None


# ── Heuristic indicator parser ────────────────────────────────────


from backend.routers.chat import _heuristic_indicator_intent


def test_heuristic_handles_filler_phrasing() -> None:
    """User typed: 'backtest what happens buying reliance when it
    drops below rsi of 50 in the last 5 years'. Strict regex misses
    this; heuristic must catch it."""
    r = _heuristic_indicator_intent(
        "backtest what happens buying reliance when it drops below rsi of 50 in the last 5 years"
    )
    assert r is not None
    assert r["symbol"].lower() == "reliance"
    assert r["indicator"] == "rsi"
    assert r["threshold"] == "50.0"
    assert r["years"] == "5"
    assert r["op"] == "drops below"


def test_heuristic_handles_indicator_period_prefix() -> None:
    """'14-day rsi' should set indicator_period=14."""
    r = _heuristic_indicator_intent(
        "backtest infy when 14-day rsi falls below 30",
    )
    assert r is not None
    assert r["indicator"] == "rsi"
    assert r["period"] == "14"
    assert r["threshold"] == "30.0"


def test_heuristic_handles_ema_cross() -> None:
    r = _heuristic_indicator_intent(
        "backtest reliance when price crosses 200 ema over last 3 years",
    )
    assert r is not None
    assert r["indicator"] == "ema"
    assert r["period"] == "200"
    assert r["years"] == "3"
    assert r["op"] == "crosses"


def test_heuristic_skips_no_indicator() -> None:
    """Generic chat that doesn't mention an indicator should be skipped."""
    assert _heuristic_indicator_intent("buy reliance") is None
    assert _heuristic_indicator_intent("whats up with nifty") is None


def test_heuristic_skips_no_trigger_word() -> None:
    """Without 'backtest' or a buy/sell verb at the start, the heuristic
    shouldn't auto-fire a backtest just because the user mentioned RSI."""
    assert _heuristic_indicator_intent("rsi is at 50 today") is None
