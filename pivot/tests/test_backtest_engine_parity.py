"""Cross-engine consistency locks (Phase 0 consolidation).

After the 2026-05-29 audit the engines diverged on conventions: Engine 1
(cross-sectional) used a bar-count/252 CAGR and its own naive 10+3 bps cost
model, while the signal engines used calendar/365.25 CAGR + the shared
trading_costs (~37 bps). These tests pin the convergence so it can't drift back.
"""
from __future__ import annotations

import datetime

import pytest

from backend.services.backtest_metrics import calendar_cagr_pct
from backend.services.trading_costs import round_trip_bps, slippage_bps


# ── 0.2 — Engine 1 CAGR is the single (calendar) convention ──────────────

def test_engine1_cagr_matches_shared_calendar_basis():
    from backtester.metrics import compute_metrics

    # Doubles over ~1 calendar year across a few bars.
    eq = [
        {"date": "2023-01-02", "value": 100.0},
        {"date": "2023-06-30", "value": 150.0},
        {"date": "2024-01-02", "value": 200.0},
    ]
    m = compute_metrics(eq)
    shared = calendar_cagr_pct(100.0, 200.0, "2023-01-02", "2024-01-02")
    assert m.cagr_pct == pytest.approx(shared, abs=0.05)
    # ~100% (doubled in a calendar year) — NOT the absurd bar-count/252 value.
    assert 95.0 < m.cagr_pct < 105.0


def test_engine1_cagr_not_bar_count_basis():
    """A 3-bar curve over a full calendar year must read ~the calendar CAGR,
    not (val)^(252/3) which the old bar-count basis produced."""
    from backtester.metrics import compute_metrics

    eq = [
        {"date": "2022-01-03", "value": 100.0},
        {"date": "2022-07-01", "value": 110.0},
        {"date": "2023-01-03", "value": 120.0},
    ]
    m = compute_metrics(eq)
    assert m.cagr_pct < 30.0  # ~20% over a year; bar-count/252 would be astronomical


# ── 0.3 — Engine 1 is on the shared NSE cost model ──────────────────────

def test_engine1_cost_model_reproduces_shared_round_trip():
    # The expr router sets Engine 1's per-leg knobs as slippage = slippage_bps(),
    # commission = round_trip/2 − slippage. Engine 1 charges slippage AND
    # commission on BOTH legs, so its round-trip = 2·(slip + comm) must equal the
    # shared round_trip_bps() (~37 bps, incl. STT/GST), not the old ~26 bps.
    _slip = slippage_bps()
    _comm = max(0.0, round_trip_bps() / 2.0 - _slip)
    engine1_round_trip = 2.0 * (_slip + _comm)
    assert engine1_round_trip == pytest.approx(round_trip_bps(), abs=0.01)
    assert round_trip_bps() > 30.0  # the converged Indian-delivery round-trip


def test_engine1_config_accepts_cost_knobs():
    from backtester.engine import BacktestConfig

    cfg = BacktestConfig(
        expression="pe_ratio < 15",
        start=datetime.date(2023, 1, 1),
        end=datetime.date(2024, 1, 1),
        rebalance="Q",
        starting_capital=100_000.0,
        benchmark_sc_id=None,
        basis="ttm",
        slippage_bps=slippage_bps(),
        commission_bps=round_trip_bps() / 2.0 - slippage_bps(),
    )
    assert cfg.slippage_bps == pytest.approx(slippage_bps())


# ── 0.4 — the vestigial run_backtest tool is retired ────────────────────

def test_run_backtest_tool_is_retired():
    from backend.agents import tools

    # ALL_TOOLS is a dict keyed by tool name.
    names = set(tools.ALL_TOOLS.keys())
    assert names, "ALL_TOOLS is empty — catalog shape changed"
    assert "run_backtest" not in names
    # backtest_workflow / backtest_dsl_tree remain the real routes.
    assert "backtest_workflow" in names and "backtest_dsl_tree" in names
    # The handler is gone too, so importing the executor module can't resolve it.
    from backend.agents import tool_executor as te
    assert not hasattr(te, "_run_backtest")
