"""Tests for pure-function sizer math in backend.agents.sizer."""
from backend.agents.sizer import calculate_safety_leg, calculate_payoff_table


def test_safety_leg_math():
    result = calculate_safety_leg(100000, 0.078, 12)
    assert 92700 <= result <= 92800, f"expected ~92764, got {result}"


def test_payoff_table_length():
    table = calculate_payoff_table(100000, 7236, "safegrow", 23500, 1)
    assert len(table) == 7


def test_payoff_scenarios_labeled():
    table = calculate_payoff_table(100000, 7236, "safegrow", 23500, 1)
    required_keys = {"scenario", "nifty_level", "portfolio_value", "return_pct"}
    for row in table:
        assert required_keys.issubset(row.keys()), f"missing keys in row: {row}"
