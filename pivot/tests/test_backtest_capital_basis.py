"""Every reported percent is a percent OF something — and we must say what.

`_STARTING_CAPITAL` (₹10L) is a SUFFICIENT-CASH constant so fills are never
starved. It is not a claim about the user's money, and using it as the
denominator turns a real edge into noise: "buy 25 ITC when RSI<35" risks ~₹10k,
and scoring that against ₹10L reported +0.5% for a sequence that made ~+17% on
the money it actually used.

Before 2026-07-17 the rebase only fired for fixed-qty, long-only runs deploying
under half the pool — so baskets and notional sizing silently kept the ₹10L
denominator. The basis is now computed for every shape from the peak capital the
strategy actually had at risk, with two honest exits: a stated capital wins, and
a short falls back to the pool because its capital is margin and we don't model
margin.
"""
from __future__ import annotations

import pandas as pd
import pytest

import backend.services.workflow_backtester as wb
from backend.services.workflow_backtester import _STARTING_CAPITAL

_IDX = pd.date_range("2024-01-01", periods=8, freq="D")
_BARS = pd.DataFrame(
    {"Open": [100, 100, 100, 105, 108, 120, 135, 145],
     "High": [101, 101, 101, 110, 109, 121, 145, 146],
     "Low": [99] * 8,
     "Close": [100, 100, 100, 108, 108, 120, 142, 145]},
    index=_IDX,
)

_SIGNAL_BUY = [
    {"step_type": "trigger.price",
     "config": {"symbol": "TEST", "operator": "crosses_above", "value": 105}},
    {"step_type": "action.place_order",
     "config": {"symbol": "TEST", "side": "buy", "quantity": 5}},
]


@pytest.fixture(autouse=True)
def _bars(monkeypatch):
    monkeypatch.setattr(wb, "_load_bars", lambda sym, period, **_k: _BARS)


def test_fixed_qty_reports_on_what_it_deployed_not_the_pool():
    m = wb.backtest_workflow(_SIGNAL_BUY, period="1y", name="x").metrics
    assert m["capital_basis"] == "deployed"
    # 5 shares near ~108 — hundreds of rupees, nowhere near the ₹10L pool.
    assert m["starting_capital"] < _STARTING_CAPITAL / 100
    # The edge survives instead of being diluted to ~0.
    assert m["total_return_pct"] > 5.0


def test_basket_allocate_uses_the_basket_size_not_the_pool():
    """The regression that motivated this: `action.allocate` was excluded from
    the old rebase, so a ₹3L basket was scored against ₹10L."""
    steps = [
        {"step_type": "trigger.schedule",
         "config": {"run_at": "2024-01-01T09:15", "timezone": "Asia/Kolkata"}},
        {"step_type": "action.allocate_basket",
         "config": {"total_inr": 300000,
                    "legs": [{"symbol": "TEST", "exchange": "NSE",
                              "weight": 1.0, "side": "long"}]}},
    ]
    m = wb.backtest_workflow(steps, period="1y", name="basket").metrics
    assert m["capital_basis"] == "deployed"
    assert m["starting_capital"] == pytest.approx(300000, rel=0.01)


def test_stated_capital_is_the_denominator_idle_cash_and_all():
    """When the user names their capital they're asking about THAT amount —
    idle cash is part of the honest answer, not something to rebase away."""
    m = wb.backtest_workflow(
        _SIGNAL_BUY, period="1y", name="x", starting_capital=50000,
    ).metrics
    assert m["capital_basis"] == "stated"
    assert m["starting_capital"] == 50000


def test_short_keeps_the_pool_basis_because_margin_is_not_modelled():
    steps = [
        {"step_type": "trigger.schedule",
         "config": {"run_at": "2024-01-01T09:15", "timezone": "Asia/Kolkata"}},
        {"step_type": "action.place_order",
         "config": {"symbol": "TEST", "side": "short", "quantity": 10}},
    ]
    m = wb.backtest_workflow(steps, period="1y", name="short").metrics
    assert m["capital_basis"] == "pool"
    assert m["starting_capital"] == _STARTING_CAPITAL


def test_legend_names_the_basis_so_the_reply_can_quote_it():
    for kwargs, basis in (({}, "deployed"), ({"starting_capital": 50000}, "stated")):
        res = wb.backtest_workflow(_SIGNAL_BUY, period="1y", name="x", **kwargs)
        legend = res.metrics["metric_legend"]
        assert res.metrics["capital_basis"] == basis
        assert legend["capital_basis"], "basis must explain itself to the model"
        # The rupee figure is in the legend so a reply can't invent a denominator.
        assert "₹" in legend["total_return_pct"]
