"""No-look-ahead fill discipline + rigor-battery wiring for Engine 2
(``services.workflow_backtester.backtest_workflow``).

This engine previously had NO direct unit test — it was exercised only via
live eval prompts. The verified P0 bug it fixes: a signal computed from a
bar's CLOSE/range (indicator / price / compound triggers) was filled at that
SAME bar's OPEN — a price that printed before the signal was knowable
(look-ahead). The fix fills signal-driven orders at the NEXT bar's open;
schedule fires (known a-priori) still fill same-bar.

Bars are injected by monkeypatching ``_load_bars`` so the assertions are
deterministic and offline (no yfinance).
"""
from __future__ import annotations

import pandas as pd
import pytest

from backend.services import workflow_backtester as wb
from backend.services.workflow_backtester import _FRICTION, _STARTING_CAPITAL


def _bars(opens, highs, lows, closes) -> pd.DataFrame:
    """Synthetic daily OHLCV on consecutive business days starting Mon
    2024-01-01 (→ 01,02,03,04,05,08,...)."""
    idx = pd.bdate_range("2024-01-01", periods=len(opens))
    return pd.DataFrame(
        {
            "Open": [float(x) for x in opens],
            "High": [float(x) for x in highs],
            "Low": [float(x) for x in lows],
            "Close": [float(x) for x in closes],
            "Volume": [1_000_000] * len(opens),
        },
        index=idx,
    )


def test_signal_driven_order_fills_at_next_bar_open(monkeypatch):
    """trigger.price '>' 109 fires ONLY on bar 3 (High 110). The buy must
    fill at bar 4's OPEN (100.0), not bar 3's OPEN (105.0)."""
    bars = _bars(
        opens=[100, 100, 100, 105, 100.0, 100],
        highs=[101, 101, 101, 110, 101, 101],
        lows=[99, 99, 99, 99, 99, 99],
        closes=[100, 100, 100, 108, 100, 100],
    )
    monkeypatch.setattr(wb, "_load_bars", lambda sym, period: bars)
    steps = [
        {"step_type": "trigger.price",
         "config": {"symbol": "TEST", "operator": ">", "value": 109}},
        {"step_type": "action.place_order",
         "config": {"symbol": "TEST", "side": "buy", "quantity": 10}},
    ]
    res = wb.backtest_workflow(steps, period="1y", name="LookAhead")

    buys = [s for s in res.signals if s["side"] == "buy"]
    assert len(buys) == 1, f"expected exactly one fill, got {res.signals}"
    # NEXT bar (2024-01-05), NOT the signal bar (2024-01-04).
    assert buys[0]["t"] == "2024-01-05"
    # Recorded price is rounded to 2 dp (100.18 == 100 × (1 + _FRICTION)).
    assert buys[0]["price"] == pytest.approx(100.0 * (1 + _FRICTION), abs=0.01)
    # The buggy same-bar fill would have priced at bar 3's open (105).
    assert buys[0]["price"] < 105.0
    assert res.metrics["n_trades"] == 1


def test_no_position_on_signal_bar_in_equity_curve(monkeypatch):
    """The equity curve must NOT carry the position on the signal bar — only
    from the fill bar onward. On 2024-01-04 (signal) equity == starting
    capital; on 2024-01-05 (fill) it reflects the new position."""
    bars = _bars(
        opens=[100, 100, 100, 105, 100.0, 100],
        highs=[101, 101, 101, 110, 101, 101],
        lows=[99, 99, 99, 99, 99, 99],
        closes=[100, 100, 100, 108, 100, 100],
    )
    monkeypatch.setattr(wb, "_load_bars", lambda sym, period: bars)
    steps = [
        {"step_type": "trigger.price",
         "config": {"symbol": "TEST", "operator": ">", "value": 109}},
        {"step_type": "action.place_order",
         "config": {"symbol": "TEST", "side": "buy", "quantity": 10}},
    ]
    res = wb.backtest_workflow(steps, period="1y", name="LookAhead")
    by_date = {p["t"]: p["v"] for p in res.equity_curve}
    # Signal bar: still 100% cash, no position marked yet.
    assert by_date["2024-01-04"] == pytest.approx(_STARTING_CAPITAL)
    # Fill bar: cash spent on 10 shares @ ~100 open, marked to 100 close.
    assert by_date["2024-01-05"] != pytest.approx(_STARTING_CAPITAL)


def test_schedule_order_fills_same_bar_open(monkeypatch):
    """A schedule fire is known a-priori → it fills at the CURRENT bar's
    open. A daily cron's first fill must be bar 0 (2024-01-01)."""
    bars = _bars(
        opens=[100.0, 101, 102, 103, 104, 105],
        highs=[100.5, 101.5, 102.5, 103.5, 104.5, 105.5],
        lows=[99.5, 100.5, 101.5, 102.5, 103.5, 104.5],
        closes=[100.2, 101.2, 102.2, 103.2, 104.2, 105.2],
    )
    monkeypatch.setattr(wb, "_load_bars", lambda sym, period: bars)
    steps = [
        {"step_type": "trigger.schedule", "config": {"cron": "0 9 * * *"}},
        {"step_type": "action.place_order",
         "config": {"symbol": "TEST", "side": "buy", "quantity": 1}},
    ]
    res = wb.backtest_workflow(steps, period="1y", name="DailySIP")
    buys = [s for s in res.signals if s["side"] == "buy"]
    assert buys, "daily schedule should fire"
    # SAME bar (2024-01-01) open — schedule fills are not deferred.
    assert buys[0]["t"] == "2024-01-01"
    assert buys[0]["price"] == pytest.approx(100.0 * (1 + _FRICTION), abs=0.01)


def test_backtest_metrics_include_forward_stats(monkeypatch):
    """P1.2 — every backtest payload carries the PSR/MinTRL/DSR battery."""
    bars = _bars(
        opens=[100.0, 101, 102, 103, 104, 105, 106, 107],
        highs=[100.5, 101.5, 102.5, 103.5, 104.5, 105.5, 106.5, 107.5],
        lows=[99.5, 100.5, 101.5, 102.5, 103.5, 104.5, 105.5, 106.5],
        closes=[100.2, 101.2, 102.2, 103.2, 104.2, 105.2, 106.2, 107.2],
    )
    monkeypatch.setattr(wb, "_load_bars", lambda sym, period: bars)
    steps = [
        {"step_type": "trigger.schedule", "config": {"cron": "0 9 * * *"}},
        {"step_type": "action.place_order",
         "config": {"symbol": "TEST", "side": "buy", "quantity": 1}},
    ]
    res = wb.backtest_workflow(steps, period="1y", name="DailySIP")
    fs = res.metrics.get("forward_stats")
    assert fs is not None, "metrics must carry the forward_stats battery"
    assert set(fs) >= {
        "observed_sharpe", "skew", "kurtosis", "n_obs", "num_trials",
        "psr", "min_trl", "deflated_sharpe",
    }
    assert fs["num_trials"] == 1
    # With num_trials=1, DSR collapses to PSR(0) — they must match.
    if fs["psr"] is not None and fs["deflated_sharpe"] is not None:
        assert fs["deflated_sharpe"] == pytest.approx(fs["psr"], abs=1e-9)
