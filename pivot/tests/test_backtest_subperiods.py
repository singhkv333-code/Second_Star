"""Sub-period robustness — time-concentration of the edge."""
from __future__ import annotations

from backend.services.backtest.validation import sub_period_robustness


def test_too_short_returns_none():
    assert sub_period_robustness([100.0, 101.0]) is None
    assert sub_period_robustness([]) is None


def test_steady_growth_is_spread_and_consistent():
    # Monotone compounding → every span positive, low concentration.
    eq = [100.0 * (1.01 ** i) for i in range(40)]
    res = sub_period_robustness(eq, n_periods=4)
    assert res is not None
    assert res["n_periods"] == 4
    assert res["positive_period_frac"] == 1.0
    # Evenly spread → concentration near 1/n_periods (= 0.25), well below 1.
    assert res["concentration"] < 0.5


def test_one_lucky_window_is_highly_concentrated():
    # Flat, then a single big jump, then flat → almost all return in one span.
    eq = [100.0] * 12 + [100.0] * 0
    eq = [100.0] * 10 + [200.0] * 10 + [201.0, 199.0] * 5  # jump between span 1→2
    res = sub_period_robustness(eq, n_periods=4)
    assert res is not None
    # The doubling span dominates total log-return → concentration high.
    assert res["concentration"] > 0.6
    # Not every span made money.
    assert res["positive_period_frac"] < 1.0


def test_product_of_spans_matches_total_return():
    eq = [100.0, 110.0, 99.0, 130.0, 120.0, 150.0, 140.0, 175.0, 160.0, 190.0]
    res = sub_period_robustness(eq, n_periods=3)
    assert res is not None
    prod = 1.0
    for r in res["period_returns_pct"]:
        prod *= (1.0 + r / 100.0)
    total = eq[-1] / eq[0]
    # Spans telescope to the total return; tolerance covers the 2-dp rounding
    # of the displayed per-span percentages.
    assert abs(prod - total) < 2e-3


def test_auto_reduces_n_periods_for_short_curves():
    res = sub_period_robustness([100.0, 101.0, 102.0, 103.0, 104.0], n_periods=10)
    assert res is not None
    assert 2 <= res["n_periods"] <= 2  # 5 pts, ≥2 per span → 2 spans
