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
    monkeypatch.setattr(wb, "_load_bars", lambda sym, period, **_kw: bars)
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
    monkeypatch.setattr(wb, "_load_bars", lambda sym, period, **_kw: bars)
    steps = [
        {"step_type": "trigger.price",
         "config": {"symbol": "TEST", "operator": ">", "value": 109}},
        {"step_type": "action.place_order",
         "config": {"symbol": "TEST", "side": "buy", "quantity": 10}},
    ]
    res = wb.backtest_workflow(steps, period="1y", name="LookAhead")
    by_date = {p["t"]: p["v"] for p in res.equity_curve}
    # Signal bar: still 100% cash, no position marked yet — equity equals
    # the run's OWN starting capital (the curve is rebased to the
    # strategy's peak deployed cost for fixed-qty drafts, so we assert
    # against the reported basis, not the module's ₹10L pool constant).
    start = res.metrics["starting_capital"]
    assert by_date["2024-01-04"] == pytest.approx(start)
    # Fill bar: cash spent on 10 shares @ ~100 open, marked to 100 close.
    assert by_date["2024-01-05"] != pytest.approx(start)


def test_schedule_order_fills_same_bar_open(monkeypatch):
    """A schedule fire is known a-priori → it fills at the CURRENT bar's
    open. A daily cron's first fill must be bar 0 (2024-01-01)."""
    bars = _bars(
        opens=[100.0, 101, 102, 103, 104, 105],
        highs=[100.5, 101.5, 102.5, 103.5, 104.5, 105.5],
        lows=[99.5, 100.5, 101.5, 102.5, 103.5, 104.5],
        closes=[100.2, 101.2, 102.2, 103.2, 104.2, 105.2],
    )
    monkeypatch.setattr(wb, "_load_bars", lambda sym, period, **_kw: bars)
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
    monkeypatch.setattr(wb, "_load_bars", lambda sym, period, **_kw: bars)
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


def test_return_on_deployed_pct_for_small_fixed_qty_signal_trade(monkeypatch):
    """A fixed 5-share signal round-trip deploys ~₹500 of the simulated
    ₹10L pool. Reported 2026-07-14: the whole-account total_return_pct
    reads as ~0% regardless of the trade's real edge, because it's
    diluted by the other ~₹9,99,500 sitting idle in cash. This engine
    (Engine 2 / workflow_backtester, used for Agent-built workflows)
    must surface `return_on_deployed_pct` / `capital_utilization_pct`
    the same way the DSL-tree engine already does, so a genuinely
    profitable trade doesn't read as "no edge"."""
    # Price only ever ASCENDS after the first crossing (never dips back
    # below 105) so "crosses_above 105" fires exactly once — a second,
    # unintended re-entry (from a dip-then-recross) would leave a lot
    # open at window end and confound this test with the OPEN-position
    # accounting covered separately below.
    bars = _bars(
        opens=[100, 100, 100, 105, 108, 120, 135, 145],
        highs=[101, 101, 101, 110, 109, 121, 145, 146],
        lows=[99, 99, 99, 99, 99, 99, 99, 99],
        closes=[100, 100, 100, 108, 108, 120, 142, 145],
    )
    monkeypatch.setattr(wb, "_load_bars", lambda sym, period, **_kw: bars)
    steps = [
        {"step_type": "trigger.price",
         "config": {"symbol": "TEST", "operator": "crosses_above", "value": 105}},
        {"step_type": "action.place_order",
         "config": {"symbol": "TEST", "side": "buy", "quantity": 5}},
        {"step_type": "trigger.price",
         "config": {"symbol": "TEST", "operator": "crosses_above", "value": 140}},
        {"step_type": "action.place_order",
         "config": {"symbol": "TEST", "side": "sell", "quantity": 5}},
    ]
    res = wb.backtest_workflow(steps, period="1y", name="SmallQtySignal")

    # 2026-07-17: the HEADLINE itself now runs on deployed capital — a
    # fixed-qty draft with no stated capital rebases the curve to its own
    # peak concurrent cost, so the ~40% trade reads as ~40%, not the
    # ₹10L-pool-diluted ~0% this test originally documented.
    assert res.metrics["capital_basis"] == "peak_deployed"
    assert res.metrics["starting_capital"] < 1000.0  # ~5 shares @ ~105
    assert res.metrics["total_return_pct"] > 30.0
    # The dollar-weighted deployed return agrees.
    assert res.metrics["return_on_deployed_pct"] is not None
    assert res.metrics["return_on_deployed_pct"] > 30.0
    assert res.metrics["capital_utilization_pct"] is not None
    assert 0.0 < res.metrics["capital_utilization_pct"] <= 100.0


def test_capital_deployed_reflects_still_open_position_not_zero(monkeypatch):
    """Reported 2026-07-14: a never-sold (still-open) position always had
    _entry_cost == 0 in the old FIFO sell-only accounting, so
    capital_utilization_pct read "0% of the window" and
    return_on_deployed_pct came back None on a position that was, in
    fact, fully invested from its entry date onward. Fixed by marking
    still-open lots to the window's last known close."""
    bars = _bars(
        opens=[100, 100, 100, 105, 108, 120, 135, 150],
        highs=[101, 101, 101, 110, 109, 121, 136, 151],
        lows=[99, 99, 99, 99, 99, 99, 99, 99],
        closes=[100, 100, 100, 108, 108, 120, 135, 150],
    )
    monkeypatch.setattr(wb, "_load_bars", lambda sym, period, **_kw: bars)
    steps = [
        {"step_type": "trigger.price",
         "config": {"symbol": "TEST", "operator": "crosses_above", "value": 105}},
        {"step_type": "action.place_order",
         "config": {"symbol": "TEST", "side": "buy", "quantity": 5}},
    ]
    res = wb.backtest_workflow(steps, period="1y", name="NeverSold")

    assert res.metrics["n_trades"] == 1  # one buy, no sell — still open
    # Bought ~108, marked at window-end close 150 — a real, large gain.
    assert res.metrics["return_on_deployed_pct"] is not None
    assert res.metrics["return_on_deployed_pct"] > 20.0
    # Deployed from the fill date through window end — NOT the old "0%".
    assert res.metrics["capital_utilization_pct"] is not None
    assert res.metrics["capital_utilization_pct"] > 0.0


# ── Regression: trigger.exit_compound's crosses_above/below must have a
# real baseline, not a fresh {} every bar ─────────────────────────────
#
# Reported live 2026-07-14: "change the strategy for selling when 50 ema
# goes below the 200 ema" on a RELIANCE golden-cross agent produced "4
# buys, 0 sells" over 5 years, despite 5 genuine EMA(50)/EMA(200)
# crossunders in the real data. Root cause: `_eval_exit_compound` passed
# `prev_state={}` on every single call instead of threading it bar-to-
# bar (as `_expand_compound` already does for entry trees), so
# crosses_above/below — which need the PRIOR bar's value to detect a
# transition — could never fire; the exit silently never triggers a
# single sell, no matter how many real crossovers occur.


def test_exit_compound_crosses_below_fires_with_real_baseline(monkeypatch):
    bars = _bars(
        opens=[110, 108, 105, 95, 90, 92],
        highs=[111, 109, 106, 96, 91, 93],
        lows=[109, 107, 104, 94, 89, 91],
        closes=[110, 108, 105, 95, 90, 92],
    )
    monkeypatch.setattr(wb, "_load_bars", lambda sym, period, **_kw: bars)
    steps = [
        {"step_type": "trigger.schedule",
         "config": {"run_at": "2024-01-01T09:15", "timezone": "Asia/Kolkata"}},
        {"step_type": "action.place_order",
         "config": {"symbol": "TEST", "side": "buy", "quantity": 5}},
        {"step_type": "trigger.exit_compound", "config": {
            "entry": {
                "type": "comparison", "op": "crosses_below",
                "left": {"type": "price", "symbol": "TEST", "basis": "close"},
                "right": {"type": "constant", "value": 100.0},
            },
            "target_symbol": "TEST",
        }},
        {"step_type": "action.place_order",
         "config": {"symbol": "TEST", "side": "sell", "quantity": 5}},
    ]
    res = wb.backtest_workflow(steps, period="1y", name="ExitCompound")

    sells = [s for s in res.signals if s["side"] == "sell"]
    assert len(sells) == 1, f"exit never fired: {res.signals}"
    assert res.metrics["n_trades"] == 2  # 1 buy + 1 sell, not "still open"


def test_exit_compound_does_not_refire_once_flat(monkeypatch):
    """Once the exit has closed the position, the branch must stay quiet
    even while the underlying condition remains true (price stays below
    100) — the position-held gate, not the crossing state, governs
    whether the branch can fire again."""
    bars = _bars(
        opens=[110, 95, 92, 130, 128, 90],
        highs=[111, 96, 93, 131, 129, 91],
        lows=[109, 94, 91, 129, 127, 89],
        closes=[110, 95, 92, 130, 128, 90],
    )
    monkeypatch.setattr(wb, "_load_bars", lambda sym, period, **_kw: bars)
    steps = [
        {"step_type": "trigger.schedule",
         "config": {"run_at": "2024-01-01T09:15", "timezone": "Asia/Kolkata"}},
        {"step_type": "action.place_order",
         "config": {"symbol": "TEST", "side": "buy", "quantity": 5}},
        {"step_type": "trigger.exit_compound", "config": {
            "entry": {
                "type": "comparison", "op": "crosses_below",
                "left": {"type": "price", "symbol": "TEST", "basis": "close"},
                "right": {"type": "constant", "value": 100.0},
            },
            "target_symbol": "TEST",
        }},
        {"step_type": "action.place_order",
         "config": {"symbol": "TEST", "side": "sell", "quantity": 5}},
    ]
    res = wb.backtest_workflow(steps, period="1y", name="ExitCompoundReset")
    sells = [s for s in res.signals if s["side"] == "sell"]
    # Crossunder detected 2024-01-02 (close 95 < 100 vs bar0's 110); signal-
    # driven fill lands next-bar-open (2024-01-03), same no-look-ahead rule
    # as every other signal trigger in this engine. Bar 2024-01-04 (130,
    # back above 100) is a flat bar — no re-entry trigger in this workflow,
    # so only ONE sell total.
    assert len(sells) == 1
    assert sells[0]["t"] == "2024-01-03"


# ── Regression: condition.compound's crosses_above/below must also thread
# state — same bug class as trigger.exit_compound, different code path ────
#
# A live audit (2026-07-15) found `_eval_condition_compound` passed
# `prev_state={}` on every call, exactly like the exit_compound bug fixed
# above — a crosses_below gate used as a mid-branch condition (not the
# trigger itself) could never fire.


def test_condition_compound_crosses_below_fires_with_real_baseline(monkeypatch):
    bars = _bars(
        opens=[110, 108, 105, 95, 90, 92],
        highs=[111, 109, 106, 96, 91, 93],
        lows=[109, 107, 104, 94, 89, 91],
        closes=[110, 108, 105, 95, 90, 92],
    )
    monkeypatch.setattr(wb, "_load_bars", lambda sym, period, **_kw: bars)
    steps = [
        # An always-true trigger.compound so the branch (and its
        # condition.compound gate) gets evaluated on every bar.
        {"step_type": "trigger.compound", "config": {
            "entry": {
                "type": "comparison", "op": ">",
                "left": {"type": "price", "symbol": "TEST", "basis": "close"},
                "right": {"type": "constant", "value": 0.0},
            },
        }},
        {"step_type": "condition.compound", "config": {
            "entry": {
                "type": "comparison", "op": "crosses_below",
                "left": {"type": "price", "symbol": "TEST", "basis": "close"},
                "right": {"type": "constant", "value": 100.0},
            },
        }},
        {"step_type": "action.place_order",
         "config": {"symbol": "TEST", "side": "buy", "quantity": 5}},
    ]
    res = wb.backtest_workflow(steps, period="1y", name="ConditionCompound")

    buys = [s for s in res.signals if s["side"] == "buy"]
    assert len(buys) == 1, f"condition.compound gate never fired: {res.signals}"
    # Crossunder detected 2024-01-04 (close 95 < 100 vs prior 105); signal-
    # driven fill lands next-bar-open (2024-01-05).
    assert buys[0]["t"] == "2024-01-05"


# ── Regression: a short position must be marked to market in the equity
# curve, not skipped as if it were flat ─────────────────────────────────
#
# `qty <= 0: continue` in the equity-curve walker silently excluded every
# short leg from market_value while cash already carried the short-sale
# proceeds — equity looked flat/positive no matter how far price moved
# against the short, instead of reflecting the real loss.


def test_equity_curve_marks_short_position_to_market(monkeypatch):
    bars = _bars(
        opens=[100, 100, 120, 140, 160, 200],
        highs=[101, 101, 121, 141, 161, 201],
        lows=[99, 99, 119, 139, 159, 199],
        closes=[100, 100, 120, 140, 160, 200],
    )
    monkeypatch.setattr(wb, "_load_bars", lambda sym, period, **_kw: bars)
    steps = [
        {"step_type": "trigger.schedule",
         "config": {"run_at": "2024-01-01T09:15", "timezone": "Asia/Kolkata"}},
        {"step_type": "action.place_order",
         "config": {"symbol": "TEST", "side": "short", "quantity": 10}},
    ]
    res = wb.backtest_workflow(steps, period="1y", name="ShortMTM")

    final_equity = res.equity_curve[-1]["v"]
    # Price DOUBLED against a short — this must show up as a real loss
    # (equity below starting capital), not a flat/inflated equity that
    # only ever reflects the short-sale proceeds credited to cash.
    assert final_equity < _STARTING_CAPITAL, (
        f"short position not marked to market: final equity "
        f"{final_equity} vs starting capital {_STARTING_CAPITAL}"
    )
