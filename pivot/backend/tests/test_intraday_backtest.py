"""Correctness regression tests for intraday support in the WORKFLOW
backtester (``services/workflow_backtester.py``).

The load-bearing invariant these tests defend is:

    A workflow with a daily schedule (cron "0 9 * * *") backtested on
    INTRADAY bars must fire ONCE per trading day, not once per bar.

Before intraday support was threaded through ``_expand_schedule`` the
scheduler only matched on day-of-month/month/day-of-week — correct on
daily bars (one bar per day) but ~7×/day wrong on hourly bars. A
"daily buy" would then over-fill 7× per session, silently corrupting
every intraday backtest.

The tests here construct synthetic intraday bar indices (no network)
and assert:

  1. Daily-cron fires once per matching day on intraday bars.
  2. Daily-cron fires once per matching day on daily bars (unchanged).
  3. The fire lands on the correct bar within the day (first bar
     at-or-after the cron hour:minute).
  4. Interval aliases (``1hr`` / ``60minute`` / ``hourly``) normalise
     to the same run and same ``bar_interval`` on the result.
  5. Backtest result carries the requested interval in
     ``bar_interval`` — no more silent ``"1d"``.

Test-scope only: unit-testing the enumerator directly avoids any need
to hit Kite / yfinance; end-to-end coverage of the full
``backtest_workflow`` intraday path is covered by monkeypatching
``_load_bars`` to return a synthetic frame.
"""
from __future__ import annotations

import pandas as pd
import pytest

from backend.services.workflow_backtester import (
    _expand_schedule,
    _fmt_bar_ts,
    _load_bars as _bt_load_bars,
    _ts_is_intraday,
    backtest_workflow,
)


# ── Synthetic bar builders ──────────────────────────────────────────


def _intraday_hourly_index(start: str, end: str) -> pd.DatetimeIndex:
    """Hourly NSE-shaped intraday bars: 09:15, 10:15, 11:15, 12:15,
    13:15, 14:15, 15:15 IST on every business day in [start, end].
    Seven bars per session — the exact 'over-fire multiplier' the
    ``_expand_schedule`` bug produced before the fix."""
    days = pd.bdate_range(start, end)
    tss = [
        pd.Timestamp(d.date()) + pd.Timedelta(hours=h, minutes=15)
        for d in days
        for h in (9, 10, 11, 12, 13, 14, 15)
    ]
    return pd.DatetimeIndex(tss)


def _daily_index(start: str, end: str) -> pd.DatetimeIndex:
    return pd.bdate_range(start, end)


def _intraday_bars_df(start: str, end: str) -> pd.DataFrame:
    """Full OHLCV frame for the hourly index (synthetic, deterministic
    prices) — good enough for the end-to-end backtest smoke check."""
    idx = _intraday_hourly_index(start, end)
    # Deterministic ramp so buy/sell fills are non-degenerate.
    n = len(idx)
    price = pd.Series([100 + i * 0.1 for i in range(n)], index=idx)
    return pd.DataFrame({
        "Open":   price,
        "High":   price + 0.5,
        "Low":    price - 0.5,
        "Close":  price + 0.25,
        "Volume": [10_000] * n,
    }, index=idx)


# ── The load-bearing regression: once-per-day on intraday ───────────


def test_daily_cron_on_intraday_fires_once_per_day() -> None:
    """A cron of "0 9 * * *" (daily 09:00) simulated on hourly bars
    must fire ONCE per trading day, not seven times.

    THIS IS THE REGRESSION TEST for the whole task — before the fix,
    ``_expand_schedule`` ignored hour/minute and matched only day-level
    fields, so every hourly bar of a matching day fired ⇒ 7 fires/day.
    """
    idx = _intraday_hourly_index("2024-01-02", "2024-01-31")
    n_trading_days = len(pd.bdate_range("2024-01-02", "2024-01-31"))

    fires = _expand_schedule(
        {"cron": "0 9 * * *"}, idx, interval="1h",
    )

    assert len(fires) == n_trading_days, (
        f"expected exactly one fire per trading day ({n_trading_days}), "
        f"got {len(fires)} — the ~7×/day mis-fire bug is back."
    )
    # Every fire lands on a DISTINCT calendar date.
    dates = {ts.date() for ts in fires}
    assert len(dates) == n_trading_days
    # And on the FIRST bar at-or-after 09:00 — which for our hourly
    # schedule is the 09:15 bar.
    for ts in fires:
        assert (ts.hour, ts.minute) == (9, 15), (
            f"cron '0 9 * * *' should fire on the 09:15 bar (first bar "
            f"at-or-after 09:00), got {ts}"
        )


def test_intraday_cron_after_session_falls_back_to_last_bar() -> None:
    """When the cron time is past the session end (e.g. '0 16 * * *'
    on hourly bars whose last bar is 15:15), the fix falls back to the
    day's LAST bar so the schedule still fires once — better than
    silently dropping the day."""
    idx = _intraday_hourly_index("2024-01-02", "2024-01-05")
    fires = _expand_schedule(
        {"cron": "0 16 * * *"}, idx, interval="1h",
    )
    n_days = len(pd.bdate_range("2024-01-02", "2024-01-05"))
    assert len(fires) == n_days
    for ts in fires:
        assert (ts.hour, ts.minute) == (15, 15)


def test_intraday_specific_hour_targets_the_right_bar() -> None:
    """cron '30 14 * * *' → fire on the first bar at-or-after 14:30.
    For hourly bars starting at HH:15, that's the 15:15 bar (the only
    one >= 14:30 in the afternoon since 14:15 is BEFORE 14:30)."""
    idx = _intraday_hourly_index("2024-01-02", "2024-01-05")
    fires = _expand_schedule(
        {"cron": "30 14 * * *"}, idx, interval="1h",
    )
    n_days = len(pd.bdate_range("2024-01-02", "2024-01-05"))
    assert len(fires) == n_days
    for ts in fires:
        assert (ts.hour, ts.minute) == (15, 15)


def test_dow_gated_cron_on_intraday_fires_once_per_matching_day() -> None:
    """cron '0 9 * * 1' (Mondays 09:00) on hourly bars: one fire per
    Monday, still at the 09:15 bar — the DOW filter composes with the
    intraday once-per-day rule."""
    idx = _intraday_hourly_index("2024-01-01", "2024-01-31")
    fires = _expand_schedule(
        {"cron": "0 9 * * 1"}, idx, interval="1h",
    )
    # Mondays in Jan 2024: 1, 8, 15, 22, 29 = 5. (Jan 1 is a Monday.)
    mondays = [d for d in pd.bdate_range("2024-01-01", "2024-01-31")
               if d.dayofweek == 0]
    assert len(fires) == len(mondays)
    for ts in fires:
        assert ts.dayofweek == 0
        assert (ts.hour, ts.minute) == (9, 15)


# ── Daily behaviour must be untouched ───────────────────────────────


def test_daily_cron_on_daily_bars_unchanged() -> None:
    """The whole point of the intraday-aware branch is that daily bars
    keep their existing behaviour bit-for-bit."""
    idx = _daily_index("2024-01-02", "2024-01-31")
    n_days = len(idx)
    fires = _expand_schedule({"cron": "0 9 * * *"}, idx, interval="1d")
    assert len(fires) == n_days
    # Every daily bar has 00:00 time.
    for ts in fires:
        assert ts.hour == 0 and ts.minute == 0


def test_expand_schedule_defaults_to_daily_when_interval_absent() -> None:
    """The interval keyword defaults to '1d' so every existing call
    site keeps its daily semantics without touching the call."""
    idx = _daily_index("2024-01-02", "2024-01-15")
    fires = _expand_schedule({"cron": "0 9 * * *"}, idx)
    assert len(fires) == len(idx)


# ── Formatter helper: keeps daily identical, fixes intraday collision ──


def test_fmt_bar_ts_preserves_daily_format() -> None:
    ts = pd.Timestamp("2024-01-15")
    assert _fmt_bar_ts(ts) == "2024-01-15"
    assert _ts_is_intraday(ts) is False


def test_fmt_bar_ts_emits_full_iso_for_intraday() -> None:
    ts = pd.Timestamp("2024-01-15 10:15")
    assert "T" in _fmt_bar_ts(ts) or ":" in _fmt_bar_ts(ts)
    assert _fmt_bar_ts(ts) != "2024-01-15"
    assert _ts_is_intraday(ts) is True


# ── End-to-end: backtest_workflow threads interval through ─────────


def _synthetic_daily_bars(start: str, end: str) -> pd.DataFrame:
    idx = pd.bdate_range(start, end)
    price = pd.Series([100 + i * 0.5 for i in range(len(idx))], index=idx)
    return pd.DataFrame({
        "Open": price, "High": price + 1, "Low": price - 1,
        "Close": price + 0.5, "Volume": [10_000] * len(idx),
    })


@pytest.fixture
def _mocked_bars(monkeypatch: pytest.MonkeyPatch) -> dict[str, pd.DataFrame]:
    """Patch _load_bars to return interval-appropriate synthetic bars.
    Keeps the test hermetic (no Kite / yfinance) while exercising the
    real interval branching inside backtest_workflow."""
    frames: dict[str, pd.DataFrame] = {}

    def _fake_load_bars(symbol, period, interval="1d", warnings_out=None):
        from backend.core.data.intervals import (
            is_intraday, normalize_interval,
        )
        norm = normalize_interval(interval)
        if is_intraday(norm):
            df = _intraday_bars_df("2024-11-01", "2024-11-30")
        else:
            df = _synthetic_daily_bars("2024-01-01", "2024-12-31")
        frames[(symbol, norm)] = df
        return df

    monkeypatch.setattr(
        "backend.services.workflow_backtester._load_bars",
        _fake_load_bars,
    )
    return frames


def _schedule_workflow_steps() -> list[dict]:
    return [
        {
            "step_index": 0,
            "step_type": "trigger.schedule",
            "config": {"cron": "0 10 * * *", "timezone": "Asia/Kolkata"},
            "label": "daily 10am",
        },
        {
            "step_index": 1,
            "step_type": "action.place_order",
            "config": {
                "symbol": "RELIANCE", "side": "buy", "quantity": 1,
                "order_type": "MARKET", "product": "CNC",
            },
            "label": "buy 1 RIL",
        },
    ]


def test_backtest_workflow_bar_interval_reflects_request_hourly(
    _mocked_bars,
) -> None:
    result = backtest_workflow(
        _schedule_workflow_steps(),
        period="30d",
        name="hourly daily buy",
        interval="1h",
    )
    assert result.bar_interval == "1h"
    # And the walker did not fire 7×/day.
    days_in_window = len(pd.bdate_range("2024-11-01", "2024-11-30"))
    n_buys = sum(
        1 for s in (result.signals or [])
        if s.get("side") == "buy"
    )
    # We may lose the LAST day's fill (signal-driven no-next-bar rule)
    # or drop the last unclosed bar, so allow ±2 slack around the
    # trading-day count. The core assertion is: NOT ≈ 7× the day count.
    assert n_buys <= days_in_window + 2, (
        f"got {n_buys} buys across {days_in_window} trading days — "
        "intraday scheduler mis-fired multiple times per day."
    )
    assert n_buys >= max(1, days_in_window - 2)


def test_backtest_workflow_bar_interval_defaults_to_daily(
    _mocked_bars,
) -> None:
    result = backtest_workflow(
        _schedule_workflow_steps(),
        period="1y",
        name="daily buy",
    )
    assert result.bar_interval == "1d"


def test_interval_aliases_normalize_to_same_run(
    _mocked_bars,
) -> None:
    """1hr, 60minute, hourly, 1h → all the same canonical interval and
    therefore identical bar_interval on the result."""
    for alias in ("1hr", "60minute", "hourly", "1h"):
        result = backtest_workflow(
            _schedule_workflow_steps(),
            period="30d",
            name=f"alias {alias}",
            interval=alias,
        )
        assert result.bar_interval == "1h", (
            f"alias {alias!r} did not normalise to '1h'"
        )


# ── Guard: honest failure when yfinance can't serve the interval ────


def test_load_bars_raises_when_yfinance_cannot_serve_interval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """3m has no yfinance equivalent AND Kite returns None in mock
    mode — the load must raise, never silently return the wrong data.
    """
    monkeypatch.setattr(
        "backend.services.workflow_backtester._kite_bars_df",
        lambda symbol, period, interval="1d": None,
    )
    with pytest.raises(ValueError):
        _bt_load_bars("RELIANCE", "30d", interval="3m")
