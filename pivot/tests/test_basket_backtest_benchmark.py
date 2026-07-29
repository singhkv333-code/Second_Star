"""Basket backtest "buy & hold" benchmark — regression coverage for the bug
where a basket's benchmark silently compared against ONE arbitrarily-chosen
constituent (alphabetical primary_symbol fallback) instead of the basket's
own target-weight buy-and-hold. See workflow_backtester._basket_buy_hold_weights
and the benchmark block in backtest_workflow().
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backend.services import workflow_backtester as wb
from backend.services.workflow_backtester import _STARTING_CAPITAL


# ── Pure unit tests: _basket_buy_hold_weights ───────────────────────────


class _FakeBranch:
    def __init__(self, body):
        self.body = body


def test_basket_weights_from_allocate_basket_legs():
    branches = [_FakeBranch([
        {"step_type": "trigger.schedule", "config": {}},
        {"step_type": "action.allocate_basket", "config": {
            "legs": [
                {"symbol": "aaaa", "weight": 60},
                {"symbol": "zzzz", "weight": 40},
            ],
            "total_inr": 100000,
        }},
    ])]
    weights = wb._basket_buy_hold_weights(branches)
    assert weights == pytest.approx({"AAAA": 0.6, "ZZZZ": 0.4})


def test_basket_weights_equal_split_for_multi_place_order():
    branches = [_FakeBranch([
        {"step_type": "action.place_order", "config": {"symbol": "AAAA", "side": "buy"}},
        {"step_type": "action.place_order", "config": {"symbol": "BBBB", "side": "buy"}},
        {"step_type": "action.place_order", "config": {"symbol": "CCCC", "side": "buy"}},
    ])]
    weights = wb._basket_buy_hold_weights(branches)
    assert weights == pytest.approx({"AAAA": 1 / 3, "BBBB": 1 / 3, "CCCC": 1 / 3})


def test_basket_weights_empty_for_single_symbol():
    branches = [_FakeBranch([
        {"step_type": "action.place_order", "config": {"symbol": "AAAA", "side": "buy"}},
    ])]
    assert wb._basket_buy_hold_weights(branches) == {}


# ── Integration: backtest_workflow's benchmark on a real basket ────────


def _flat_or_ramp_bars(n: int, lo: float, hi: float) -> pd.DataFrame:
    closes = np.linspace(lo, hi, n)
    idx = pd.bdate_range("2024-01-01", periods=n)
    return pd.DataFrame({
        "Open": closes, "High": closes + 1, "Low": closes - 1,
        "Close": closes, "Volume": np.full(n, 1_000_000.0),
    }, index=idx)


def test_basket_benchmark_is_weighted_blend_not_one_leg(monkeypatch):
    """AAAA doubles (+100%), ZZZZ is flat (0%), basket weights 60/40. The
    benchmark must read close to the 60/40 blend (~+60%) — NOT AAAA's raw
    +100% (which is what the old alphabetical-primary_symbol fallback would
    have silently used, since AAAA sorts first and the basket has no
    trigger symbol or place_order leg to anchor on)."""
    n = 40
    bars_by_symbol = {
        "AAAA": _flat_or_ramp_bars(n, 100.0, 200.0),
        "ZZZZ": _flat_or_ramp_bars(n, 100.0, 100.0),
    }
    monkeypatch.setattr(
        wb, "_load_bars",
        lambda sym, period, interval="1d", warnings_out=None: bars_by_symbol[sym.upper()],
    )

    steps = [
        {"step_type": "trigger.schedule",
         "config": {"run_at": "2024-01-10T09:15", "timezone": "Asia/Kolkata"}},
        {"step_type": "action.allocate_basket", "config": {
            "legs": [
                {"symbol": "AAAA", "weight": 60},
                {"symbol": "ZZZZ", "weight": 40},
            ],
            "total_inr": 100000,
        }},
    ]
    res = wb.backtest_workflow(steps, period="1y", name="Basket")

    assert res.strategy_kind == "basket"
    assert res.benchmark_label == "2-name basket (ideal weights)"
    # A 60/40 blend of (a leg that roughly doubles) and (a flat leg), over
    # the one-time-entry-clipped window (run_at trims the start, so AAAA's
    # captured move is less than its full 100->200 range) — comfortably
    # between the two legs' own returns, nowhere near AAAA's ~100%+ alone.
    # That gap (this used to silently equal AAAA's own return, the exact
    # bug this test guards) is the regression signature.
    assert 25.0 < res.bench_buy_hold_return_pct < 55.0


def test_basket_benchmark_explicit_symbol_overrides_basket_default(monkeypatch):
    """An explicit benchmark_symbol (e.g. an index) always wins over the
    basket-ideal-weights default."""
    n = 40
    bars_by_symbol = {
        "AAAA": _flat_or_ramp_bars(n, 100.0, 200.0),
        "ZZZZ": _flat_or_ramp_bars(n, 100.0, 100.0),
        "NIFTYBEES": _flat_or_ramp_bars(n, 100.0, 110.0),
    }
    monkeypatch.setattr(
        wb, "_load_bars",
        lambda sym, period, interval="1d", warnings_out=None: bars_by_symbol[sym.upper()],
    )

    steps = [
        {"step_type": "trigger.schedule",
         "config": {"run_at": "2024-01-10T09:15", "timezone": "Asia/Kolkata"}},
        {"step_type": "action.allocate_basket", "config": {
            "legs": [
                {"symbol": "AAAA", "weight": 60},
                {"symbol": "ZZZZ", "weight": 40},
            ],
            "total_inr": 100000,
        }},
    ]
    res = wb.backtest_workflow(
        steps, period="1y", name="Basket", benchmark_symbol="NIFTYBEES",
    )
    assert res.benchmark_label == "NIFTYBEES"
    # ~+10% (NIFTYBEES move), not the ~60% basket blend nor AAAA's +100%.
    assert 0.0 < res.bench_buy_hold_return_pct < 15.0


# ── Regression: starting_capital must be real, not always ₹10L ─────────
# Reported 2026-07-14 — a basket backtest always simulated ₹10,00,000
# regardless of what the user actually asked to deploy, and because a
# fixed-quantity buy leaves the rest of the capital idle as cash, the
# reported return % itself (not just the label) depended on that
# hardcoded amount rather than the user's real deploy size.


def _single_symbol_steps(qty: int) -> list[dict]:
    return [
        {"step_type": "trigger.schedule",
         "config": {"run_at": "2024-01-10T09:15", "timezone": "Asia/Kolkata"}},
        {"step_type": "action.place_order",
         "config": {"symbol": "AAAA", "side": "buy", "quantity": qty}},
    ]


def test_starting_capital_is_echoed_not_hardcoded(monkeypatch):
    monkeypatch.setattr(
        wb, "_load_bars",
        lambda sym, period, interval="1d", warnings_out=None: (
            _flat_or_ramp_bars(40, 100.0, 200.0)
        ),
    )
    res = wb.backtest_workflow(
        _single_symbol_steps(5), period="1y", name="X", starting_capital=250_000,
    )
    assert res.metrics["starting_capital"] == 250_000


def test_no_starting_capital_falls_back_to_default(monkeypatch):
    monkeypatch.setattr(
        wb, "_load_bars",
        lambda sym, period, interval="1d", warnings_out=None: (
            _flat_or_ramp_bars(40, 100.0, 200.0)
        ),
    )
    res = wb.backtest_workflow(_single_symbol_steps(5), period="1y", name="X")
    assert res.metrics["starting_capital"] == wb._STARTING_CAPITAL


def test_capital_size_changes_return_via_idle_cash_drag(monkeypatch):
    # A FIXED 5-share buy at ~₹100-200 (~₹500-1000 deployed) is a tiny
    # fraction of ₹10L but a large fraction of ₹5,000 — idle cash dilutes
    # the reported return differently, so the two runs must NOT match.
    monkeypatch.setattr(
        wb, "_load_bars",
        lambda sym, period, interval="1d", warnings_out=None: (
            _flat_or_ramp_bars(40, 100.0, 200.0)
        ),
    )
    small = wb.backtest_workflow(
        _single_symbol_steps(5), period="1y", name="X", starting_capital=5_000,
    )
    large = wb.backtest_workflow(
        _single_symbol_steps(5), period="1y", name="X", starting_capital=1_000_000,
    )
    # The actual regression signature: same steps, same bars, DIFFERENT
    # capital → the return % itself must differ (idle-cash dilution),
    # not just the "starting_capital"/"ending_value" labels.
    assert small.metrics["total_return_pct"] != large.metrics["total_return_pct"]


# ── Regression: a basket leg whose bars start a few days later than the
# entry date must still be bought, not silently dropped ─────────────────
#
# Reported live 2026-07-14: a 7-name weighted basket (TRENT/DMART/
# BAJFINANCE/ICICIBANK/HDFCBANK/TATAMOTORS/CGPOWER) came back as
# `symbol: BAJFINANCE`, "1 trade(s) on the 7-name basket", -4.8% total
# return. Root cause: Kite and the yfinance fallback resolve `period="3y"`
# independently per symbol, so one leg's (TATAMOTORS') bar series started
# a few calendar days earlier than the other six's. The basket's
# schedule fired on that earliest date, which existed in ONLY that one
# leg's index — the other 6 legs' strict `ts not in lb.index` check
# silently dropped them, leaving 89% of "100% deployed" capital sitting
# unbought in cash for the entire window while the card still claimed a
# 7-name basket.


def test_basket_leg_starting_a_few_days_later_still_gets_bought(monkeypatch):
    n = 40
    early = _flat_or_ramp_bars(n, 100.0, 200.0)  # starts 2024-01-01
    late = _flat_or_ramp_bars(n, 50.0, 60.0)
    late.index = late.index[3:].append(
        pd.bdate_range(late.index[-1] + pd.Timedelta(days=1), periods=3)
    )  # shifted 3 bdays later than `early` — same length, later start

    bars_by_symbol = {"EARLY": early, "LATE": late}
    monkeypatch.setattr(
        wb, "_load_bars",
        lambda sym, period, interval="1d", warnings_out=None: bars_by_symbol[sym.upper()],
    )
    steps = [
        {"step_type": "trigger.schedule",
         "config": {"run_at": f"{early.index[0].date()}T09:15",
                    "timezone": "Asia/Kolkata"}},
        {"step_type": "action.allocate_basket", "config": {
            "legs": [
                {"symbol": "EARLY", "weight": 60},
                {"symbol": "LATE", "weight": 40},
            ],
            "total_inr": 100000,
        }},
    ]
    res = wb.backtest_workflow(steps, period="1y", name="Basket")
    # Both legs bought — not just EARLY (the alphabetically-irrelevant but
    # date-available one).
    assert res.metrics["n_trades"] == 2
    assert not any(
        s.get("side") == "buy_skipped" for s in res.signals
    )


def test_basket_leg_with_no_nearby_bar_is_dropped_with_disclosure(monkeypatch):
    """A leg whose data genuinely starts weeks after the entry date can't
    be honestly filled — it's excluded, but (unlike the live bug) that
    exclusion must be disclosed in the summary text, not silent."""
    n = 40
    early = _flat_or_ramp_bars(n, 100.0, 200.0)  # starts 2024-01-01
    much_later = _flat_or_ramp_bars(n, 50.0, 60.0)
    much_later.index = pd.bdate_range(
        early.index[0] + pd.Timedelta(days=30), periods=n,
    )

    bars_by_symbol = {"EARLY": early, "GAPPY": much_later}
    monkeypatch.setattr(
        wb, "_load_bars",
        lambda sym, period, interval="1d", warnings_out=None: bars_by_symbol[sym.upper()],
    )
    steps = [
        {"step_type": "trigger.schedule",
         "config": {"run_at": f"{early.index[0].date()}T09:15",
                    "timezone": "Asia/Kolkata"}},
        {"step_type": "action.allocate_basket", "config": {
            "legs": [
                {"symbol": "EARLY", "weight": 60},
                {"symbol": "GAPPY", "weight": 40},
            ],
            "total_inr": 100000,
        }},
    ]
    res = wb.backtest_workflow(steps, period="1y", name="Basket")
    assert res.metrics["n_trades"] == 1  # only EARLY
    assert "GAPPY" in res.summary_text
    assert "not bought" in res.summary_text or "cash" in res.summary_text


# ── Regression: a basket short leg must respect the same margin cap as a
# single place_order(short) ─────────────────────────────────────────────
#
# `allocate_basket` applied the cash check to LONG legs only — a short leg
# ("side": "short") skipped the 50%-of-equity margin gate entirely, unlike
# the single-order short path, letting a basket take on unbounded short
# exposure.


def test_basket_short_leg_respects_margin_cap(monkeypatch):
    n = 40
    bars = _flat_or_ramp_bars(n, 100.0, 100.0)
    monkeypatch.setattr(
        wb, "_load_bars",
        lambda sym, period, interval="1d", warnings_out=None: bars,
    )
    steps = [
        {"step_type": "trigger.schedule",
         "config": {"run_at": f"{bars.index[0].date()}T09:15",
                    "timezone": "Asia/Kolkata"}},
        {"step_type": "action.allocate_basket", "config": {
            "legs": [{"symbol": "TEST", "side": "short", "weight": 100}],
            # Double the starting capital as a single short leg — well
            # past the 50%-of-equity cap a single place_order(short)
            # would enforce.
            "total_inr": 2_000_000,
        }},
    ]
    res = wb.backtest_workflow(steps, period="1y", name="BasketShortMargin")

    # The oversized short must be capped, not filled — n_trades==0 and no
    # P&L means the leg never actually opened a position (the FE-facing
    # `res.signals` collapses every non-buy* raw signal to "sell" for chart
    # plotting, so "short_skipped" isn't independently visible there —
    # n_trades + a flat equity curve is the real invariant).
    assert res.metrics["n_trades"] == 0, (
        f"oversized short leg should have been capped, not filled: {res.signals}"
    )
    assert res.equity_curve[-1]["v"] == pytest.approx(_STARTING_CAPITAL)
#
# Reported live 2026-07-14: the same user's report — a TATAMOTORS leg's
# mark-to-market looked far worse than plausible. Traced to a real, unfixed
# gap: the price series this backtester reads has no spin-off/demerger
# adjustment (only a plain ticker redirect in yfinance_service.py), so the
# 2025-10-13 demerger shows up as an unadjusted ~40% overnight cliff. No
# authoritative demerger-ratio data exists in this codebase to correct the
# number, so the fix is an honest warning, not a fabricated adjustment.


def test_tatamotors_window_spanning_demerger_gets_disclosed():
    idx = pd.bdate_range("2025-09-01", periods=60)
    hist = pd.DataFrame({
        "Open": 100.0, "High": 101.0, "Low": 99.0, "Close": 100.0,
        "Volume": 1_000_000.0,
    }, index=idx)
    warnings: list[str] = []
    wb._warn_known_corporate_action_gap("TATAMOTORS", hist, warnings)
    assert len(warnings) == 1
    assert "demerg" in warnings[0].lower()


def test_tatamotors_window_before_demerger_not_flagged():
    idx = pd.bdate_range("2024-01-01", periods=60)  # entirely pre-demerger
    hist = pd.DataFrame({
        "Open": 100.0, "High": 101.0, "Low": 99.0, "Close": 100.0,
        "Volume": 1_000_000.0,
    }, index=idx)
    warnings: list[str] = []
    wb._warn_known_corporate_action_gap("TATAMOTORS", hist, warnings)
    assert warnings == []


def test_other_symbols_never_flagged():
    idx = pd.bdate_range("2025-09-01", periods=60)
    hist = pd.DataFrame({
        "Open": 100.0, "High": 101.0, "Low": 99.0, "Close": 100.0,
        "Volume": 1_000_000.0,
    }, index=idx)
    warnings: list[str] = []
    wb._warn_known_corporate_action_gap("RELIANCE", hist, warnings)
    assert warnings == []


# ── Regression: NaN Close from a raw source glitch must not leak into
# basket-level aggregates ───────────────────────────────────────────────
#
# A held basket leg's Close is read straight from the source (Kite/
# yfinance) with no null-guard in `_load_bars`. A single NaN Close on a
# date the source still keeps in its index (a suspended-trading print or
# a corporate-action gap) survives into the mark-to-market and multiplies
# straight through every basket-level aggregate downstream —
# total_return_pct / cagr_pct / ending_value / the basket buy-and-hold
# benchmark all come out as a literal NaN. Same bug class already fixed
# for fetch_fundamentals (yfinance_fundamentals._safe() drops NaN/inf
# before it reaches a metric) and for market/yfinance_service.py's
# _records_from_df (drops NaN-close rows the same way) — `_load_bars`
# now mirrors that fix by dropping any NaN-Close row before it ever
# reaches symbol_bars.


def test_load_bars_drops_nan_close_rows(monkeypatch):
    """Direct unit coverage of the fix: `_load_bars` must never hand back
    a bar with a NaN Close, regardless of which source served it."""
    n = 40
    bars = _flat_or_ramp_bars(n, 100.0, 200.0)
    bars.loc[bars.index[-1], "Close"] = float("nan")

    class _FakeTicker:
        def __init__(self, sym):
            pass

        def history(self, period=None, interval=None, auto_adjust=None):
            return bars

    monkeypatch.setattr(wb, "_kite_bars_df", lambda symbol, period, interval="1d": None)
    monkeypatch.setattr(wb.yf, "Ticker", _FakeTicker)

    out = wb._load_bars("ZZZZ", "1y", interval="1d")
    assert not out["Close"].isna().any()
    assert len(out) == n - 1


def test_basket_aggregate_finite_when_one_leg_has_nan_close(monkeypatch):
    """End-to-end: a basket leg whose raw source history has a NaN Close
    on its last bar must not poison total_return_pct / cagr_pct /
    ending_value / benchmark_return_pct into NaN. Goes through the real
    `_load_bars` (patching the source calls it makes, NOT `_load_bars`
    itself) so the fix under test is actually exercised."""
    import math

    n = 40
    aaaa = _flat_or_ramp_bars(n, 100.0, 200.0)
    zzzz = _flat_or_ramp_bars(n, 100.0, 100.0)
    zzzz.loc[zzzz.index[-1], "Close"] = float("nan")
    bars_by_symbol = {"AAAA": aaaa, "ZZZZ": zzzz}

    class _FakeTicker:
        def __init__(self, sym):
            self.sym = sym

        def history(self, period=None, interval=None, auto_adjust=None):
            # `_yf_symbol` appends a suffix for NSE names; strip back to
            # the bare symbol used as the dict key.
            bare = self.sym.split(".")[0].upper()
            return bars_by_symbol[bare]

    monkeypatch.setattr(wb, "_kite_bars_df", lambda symbol, period, interval="1d": None)
    monkeypatch.setattr(wb.yf, "Ticker", _FakeTicker)

    steps = [
        {"step_type": "trigger.schedule",
         "config": {"run_at": "2024-01-10T09:15", "timezone": "Asia/Kolkata"}},
        {"step_type": "action.allocate_basket", "config": {
            "legs": [
                {"symbol": "AAAA", "weight": 60},
                {"symbol": "ZZZZ", "weight": 40},
            ],
            "total_inr": 100000,
        }},
    ]
    res = wb.backtest_workflow(steps, period="1y", name="Basket")

    for field in ("total_return_pct", "cagr_pct", "ending_value", "benchmark_return_pct"):
        val = res.metrics.get(field)
        assert val is not None and math.isfinite(val), f"{field} leaked a non-finite value: {val}"
    for point in res.equity_curve:
        assert math.isfinite(point["v"]), f"equity_curve leaked a non-finite point: {point}"


# ── Regression: equity-curve walker must share the union calendar, not
# just the (arbitrarily alphabetical) primary symbol's own bars ───────
# Reported 2026-07-14 — a 7-leg basket buy-and-hold showed a flat 0.0%
# return over 3 years against a +24.7% benchmark, with the correct
# non-zero trade count. Root cause: `primary_symbol` for a basket with no
# `place_order` falls back to `sorted(all_symbols)[0]` (alphabetical, not
# data-driven). When that leg's own bars start LATER than the basket's
# actual one-time entry date (itself resolved from the UNION of every
# leg's calendar), the display walker — which iterated only the primary
# symbol's own bars — never reached the entry date in its loop, so the
# exact-timestamp match against the recorded trade never fired and no
# fill was ever applied to the walking equity curve.


def test_basket_walker_applies_fill_dated_before_primary_symbols_own_bars(monkeypatch):
    # Both legs end on the SAME date (isolates the start-of-window bug
    # from the separate end-of-data mark-to-market question) — AAAA
    # (sorts first -> primary_symbol) simply starts a week LATER than
    # ZZZZ. Both ramp 100 -> 200 (a real +100% move) over their own span.
    full_idx = pd.bdate_range("2024-01-01", "2024-03-15")
    aaaa_idx = full_idx[full_idx >= "2024-01-08"]
    aaaa = pd.DataFrame({
        "Open": np.linspace(100.0, 200.0, len(aaaa_idx)),
        "High": np.linspace(101.0, 201.0, len(aaaa_idx)),
        "Low": np.linspace(99.0, 199.0, len(aaaa_idx)),
        "Close": np.linspace(100.0, 200.0, len(aaaa_idx)),
        "Volume": np.full(len(aaaa_idx), 1_000_000.0),
    }, index=aaaa_idx)
    zzzz = pd.DataFrame({
        "Open": np.linspace(100.0, 200.0, len(full_idx)),
        "High": np.linspace(101.0, 201.0, len(full_idx)),
        "Low": np.linspace(99.0, 199.0, len(full_idx)),
        "Close": np.linspace(100.0, 200.0, len(full_idx)),
        "Volume": np.full(len(full_idx), 1_000_000.0),
    }, index=full_idx)
    bars_by_symbol = {"AAAA": aaaa, "ZZZZ": zzzz}
    monkeypatch.setattr(
        wb, "_load_bars",
        lambda sym, period, interval="1d", warnings_out=None: bars_by_symbol[sym.upper()],
    )
    steps = [
        {"step_type": "trigger.schedule",
         "config": {"run_at": "2024-01-01T09:15", "timezone": "Asia/Kolkata"}},
        {"step_type": "action.allocate_basket", "config": {
            "legs": [
                {"symbol": "AAAA", "weight": 50},
                {"symbol": "ZZZZ", "weight": 50},
            ],
            "total_inr": 100000,
        }},
    ]
    res = wb.backtest_workflow(steps, period="1y", name="Basket")

    # ZZZZ's leg fills on 2024-01-01 (its own bars cover that date) — a
    # fill the OLD walker (primary_bars-only, starting 2024-01-08) could
    # never apply. The bug's exact signature: total_return_pct == 0.0
    # despite a nonzero trade and a leg that genuinely doubled. AAAA's
    # own leg is skipped (no bar at the fire date) so only half the
    # capital ever deploys — the fixed walker should show that half's
    # real ~100% gain (~+5% on the whole basket), not a flat 0.0%.
    assert res.metrics["n_trades"] > 0
    assert res.metrics["total_return_pct"] > 3.0
