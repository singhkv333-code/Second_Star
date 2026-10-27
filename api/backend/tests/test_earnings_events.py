"""Unit tests for :mod:`backend.earnings_events`.

No network — every test injects a deterministic ``fetch`` callable so
the verifier/calendar are exercised against canned yfinance-shaped rows.
Mirrors the fail-safe coverage in :mod:`backend.macro_events` tests.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from backend.earnings_events import (
    EARNINGS_DECISIONS,
    EarningsEventDef,
    EarningsOutcome,
    due_event,
    get_next_earnings,
    verify_earnings_outcome,
)


# ── helpers ──────────────────────────────────────────────────────────


def _row(
    when: datetime,
    *,
    estimate: float | None,
    reported: float | None,
    surprise_pct: float | None = None,
) -> dict[str, Any]:
    return {
        "report_date": when,
        "eps_estimate": estimate,
        "reported_eps": reported,
        "surprise_pct": surprise_pct,
    }


def _fetch_from(rows: list[dict[str, Any]]):
    """Build a deterministic fetch callable that ignores the symbol arg."""

    def _fetch(_symbol: str) -> list[dict[str, Any]]:
        return list(rows)

    return _fetch


def _run(coro):
    # asyncio.run() avoids the "no current event loop" DeprecationWarning
    # that get_event_loop() now emits when no loop is running.
    return asyncio.run(coro)


# ── outcomes / decision vocabulary ───────────────────────────────────


def test_outcome_unknown_factory_is_fail_safe():
    o = EarningsOutcome.unknown("nothing reported yet")
    assert o.matched is False
    assert o.decision == "unknown"
    assert o.confidence == 0.0
    assert o.metric == "eps"
    assert o.audit == {"reason": "nothing reported yet"}


def test_decision_vocabulary_is_stable():
    assert EARNINGS_DECISIONS == frozenset({"beat", "miss", "meet", "unknown"})


# ── calendar: instance_key, window, due_event, get_next_earnings ─────


def test_event_def_instance_key_and_window():
    when = datetime(2026, 7, 15, 11, 0, tzinfo=timezone.utc)
    ev = EarningsEventDef(
        symbol="infy",
        report_at_utc=when,
        verify_window_minutes=2880,
        label="INFY earnings",
    )
    assert ev.instance_key() == "INFY:2026-07-15"  # noqa: E501  upper-cased + ISO date
    assert ev.window_end_utc == when + timedelta(minutes=2880)


def test_due_event_returns_in_window_occurrence():
    when = datetime(2026, 7, 15, 11, 0, tzinfo=timezone.utc)
    rows = [
        _row(when - timedelta(days=120), estimate=10.0, reported=10.5),  # old quarter
        _row(when, estimate=12.0, reported=12.8),                         # just-released
        _row(when + timedelta(days=90), estimate=14.0, reported=None),    # future
    ]
    fetch = _fetch_from(rows)

    # Inside window (an hour after release).
    now = when + timedelta(hours=1)
    ev = due_event("INFY", now, fetch=fetch)
    assert ev is not None
    assert ev.symbol == "INFY"
    assert ev.report_at_utc == when
    assert ev.instance_key() == "INFY:2026-07-15"

    # Before any window opens → None.
    pre = when - timedelta(hours=1)
    assert due_event("INFY", pre, fetch=fetch) is None

    # After the window has closed → None.
    post = when + timedelta(days=10)
    assert due_event("INFY", post, fetch=fetch) is None


def test_get_next_earnings_returns_future_occurrence():
    base = datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc)
    rows = [
        _row(base - timedelta(days=30), estimate=10.0, reported=10.2),
        _row(base + timedelta(days=10), estimate=11.0, reported=None),
        _row(base + timedelta(days=100), estimate=12.0, reported=None),
    ]
    fetch = _fetch_from(rows)
    nxt = get_next_earnings("infy", now=base, fetch=fetch)
    assert nxt is not None
    assert nxt.symbol == "INFY"
    assert nxt.report_at_utc == base + timedelta(days=10)


def test_get_next_earnings_returns_none_when_no_future_row():
    base = datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc)
    rows = [
        _row(base - timedelta(days=30), estimate=10.0, reported=10.2),
        _row(base - timedelta(days=10), estimate=11.0, reported=11.1),
    ]
    fetch = _fetch_from(rows)
    assert get_next_earnings("INFY", now=base, fetch=fetch) is None


def test_due_event_naive_now_is_treated_as_utc():
    when = datetime(2026, 7, 15, 11, 0, tzinfo=timezone.utc)
    rows = [_row(when, estimate=12.0, reported=12.4)]
    fetch = _fetch_from(rows)
    # Naive datetime — must not raise; treated as UTC.
    naive = (when + timedelta(hours=1)).replace(tzinfo=None)
    ev = due_event("INFY", naive, fetch=fetch)
    assert ev is not None and ev.report_at_utc == when


def test_calendar_fetch_provider_exception_returns_none():
    when_now = datetime(2026, 6, 1, tzinfo=timezone.utc)

    def _boom(_symbol: str) -> list[dict[str, Any]]:
        raise RuntimeError("network down")

    assert due_event("INFY", when_now, fetch=_boom) is None
    assert get_next_earnings("INFY", now=when_now, fetch=_boom) is None


# ── verifier: beat / miss / meet, threshold gating ───────────────────


def test_verify_eps_beat_matches_when_reported_above_estimate():
    when = datetime(2026, 7, 15, 11, 0, tzinfo=timezone.utc)
    rows = [_row(when, estimate=10.0, reported=11.5, surprise_pct=15.0)]
    fetch = _fetch_from(rows)
    o = _run(verify_earnings_outcome(
        "INFY", "eps", "beat", fetch=fetch,
    ))
    assert o.matched is True
    assert o.decision == "beat"
    assert o.metric == "eps"
    assert o.reported == 11.5
    assert o.estimate == 10.0
    assert o.surprise_pct == pytest.approx(15.0)
    assert o.confidence == 1.0
    assert "INFY" in (o.evidence or "")
    assert o.audit["symbol"] == "INFY"


def test_verify_eps_miss_matches_when_reported_below_estimate():
    when = datetime(2026, 7, 15, 11, 0, tzinfo=timezone.utc)
    rows = [_row(when, estimate=10.0, reported=8.0, surprise_pct=-20.0)]
    fetch = _fetch_from(rows)
    o = _run(verify_earnings_outcome("INFY", "eps", "miss", fetch=fetch))
    assert o.matched is True
    assert o.decision == "miss"


def test_verify_eps_meet_matches_within_band():
    when = datetime(2026, 7, 15, 11, 0, tzinfo=timezone.utc)
    # 0.5% surprise — inside the ±1% meet band.
    rows = [_row(when, estimate=10.0, reported=10.05, surprise_pct=0.5)]
    fetch = _fetch_from(rows)
    o = _run(verify_earnings_outcome("INFY", "eps", "meet", fetch=fetch))
    assert o.matched is True
    assert o.decision == "meet"


def test_verify_eps_user_beat_with_threshold_downgrades_when_too_small():
    when = datetime(2026, 7, 15, 11, 0, tzinfo=timezone.utc)
    # 3% beat — below the 5% surprise threshold the user requires.
    rows = [_row(when, estimate=10.0, reported=10.3, surprise_pct=3.0)]
    fetch = _fetch_from(rows)
    o = _run(verify_earnings_outcome(
        "INFY", "eps", "beat",
        surprise_threshold_pct=5.0,
        fetch=fetch,
    ))
    assert o.matched is False
    # downgraded to "meet" — not a miss, not a beat-by-the-required-margin.
    assert o.decision == "meet"


def test_verify_eps_beat_with_threshold_matches_when_large_enough():
    when = datetime(2026, 7, 15, 11, 0, tzinfo=timezone.utc)
    rows = [_row(when, estimate=10.0, reported=11.0, surprise_pct=10.0)]
    fetch = _fetch_from(rows)
    o = _run(verify_earnings_outcome(
        "INFY", "eps", "beat",
        surprise_threshold_pct=5.0,
        fetch=fetch,
    ))
    assert o.matched is True
    assert o.decision == "beat"


def test_verify_eps_picks_most_recent_reported_quarter():
    """Earlier quarter beat, later quarter missed → verdict reflects
    the later quarter only (the "just-announced" one)."""
    old = datetime(2026, 4, 15, 11, 0, tzinfo=timezone.utc)
    new = datetime(2026, 7, 15, 11, 0, tzinfo=timezone.utc)
    future = datetime(2026, 10, 15, 11, 0, tzinfo=timezone.utc)
    rows = [
        _row(old, estimate=9.0, reported=10.0, surprise_pct=11.1),     # beat
        _row(new, estimate=12.0, reported=10.0, surprise_pct=-16.7),   # miss
        _row(future, estimate=14.0, reported=None),                    # future
    ]
    fetch = _fetch_from(rows)
    o = _run(verify_earnings_outcome("INFY", "eps", "miss", fetch=fetch))
    assert o.matched is True
    assert o.decision == "miss"
    assert o.reported == 10.0
    assert o.estimate == 12.0


# ── fail-safe paths ──────────────────────────────────────────────────


def test_verify_eps_unknown_when_nothing_reported_yet():
    when = datetime(2026, 7, 15, 11, 0, tzinfo=timezone.utc)
    # All rows are FUTURE quarters — reported_eps is None.
    rows = [
        _row(when, estimate=12.0, reported=None),
        _row(when + timedelta(days=90), estimate=13.0, reported=None),
    ]
    fetch = _fetch_from(rows)
    o = _run(verify_earnings_outcome("INFY", "eps", "beat", fetch=fetch))
    assert o.matched is False
    assert o.decision == "unknown"
    assert o.confidence == 0.0


def test_verify_eps_unknown_when_estimate_missing():
    when = datetime(2026, 7, 15, 11, 0, tzinfo=timezone.utc)
    rows = [_row(when, estimate=None, reported=11.5)]
    fetch = _fetch_from(rows)
    o = _run(verify_earnings_outcome("INFY", "eps", "beat", fetch=fetch))
    assert o.matched is False
    assert o.decision == "unknown"


def test_verify_revenue_is_unsupported_and_unknown():
    when = datetime(2026, 7, 15, 11, 0, tzinfo=timezone.utc)
    rows = [_row(when, estimate=10.0, reported=11.0, surprise_pct=10.0)]
    fetch = _fetch_from(rows)
    o = _run(verify_earnings_outcome("INFY", "revenue", "beat", fetch=fetch))
    assert o.matched is False
    assert o.decision == "unknown"
    assert o.metric == "revenue"
    assert "not yet supported" in (o.audit.get("reason") or "")


def test_verify_unsupported_metric_is_unknown():
    fetch = _fetch_from([])
    o = _run(verify_earnings_outcome("INFY", "margin", "beat", fetch=fetch))
    assert o.decision == "unknown"


def test_verify_unsupported_condition_is_unknown():
    when = datetime(2026, 7, 15, 11, 0, tzinfo=timezone.utc)
    rows = [_row(when, estimate=10.0, reported=11.0, surprise_pct=10.0)]
    fetch = _fetch_from(rows)
    o = _run(verify_earnings_outcome("INFY", "eps", "explode", fetch=fetch))
    assert o.decision == "unknown"


def test_verify_fetch_failure_returns_unknown_not_exception():
    def _boom(_s: str) -> list[dict[str, Any]]:
        raise RuntimeError("yfinance down")

    o = _run(verify_earnings_outcome("INFY", "eps", "beat", fetch=_boom))
    assert o.matched is False
    assert o.decision == "unknown"
    assert "earnings fetch failed" in (o.audit.get("reason") or "")


def test_verify_min_confidence_floor_above_one_yields_unknown():
    """Caller can dial up the confidence floor to force always-unknown
    (useful for dry-run / sandbox modes)."""
    when = datetime(2026, 7, 15, 11, 0, tzinfo=timezone.utc)
    rows = [_row(when, estimate=10.0, reported=11.5, surprise_pct=15.0)]
    fetch = _fetch_from(rows)
    o = _run(verify_earnings_outcome(
        "INFY", "eps", "beat",
        min_confidence=1.5,  # impossible
        fetch=fetch,
    ))
    assert o.decision == "unknown"


def test_verify_handles_unsorted_input_rows():
    """The calendar contract is ascending-by-date; the verifier must
    sort defensively so a fetch impl that returns newest-first still
    yields a correct latest-reported pick."""
    old = datetime(2026, 4, 15, 11, 0, tzinfo=timezone.utc)
    new = datetime(2026, 7, 15, 11, 0, tzinfo=timezone.utc)
    rows = [
        _row(new, estimate=12.0, reported=14.4, surprise_pct=20.0),   # beat
        _row(old, estimate=9.0, reported=8.0, surprise_pct=-11.1),    # miss
    ]
    fetch = _fetch_from(rows)
    o = _run(verify_earnings_outcome("INFY", "eps", "beat", fetch=fetch))
    assert o.matched is True
    assert o.reported == 14.4


def test_verify_uses_computed_surprise_when_field_absent():
    """When the provider row omits surprise_pct, the verifier computes
    it from reported / estimate so the threshold gate still works."""
    when = datetime(2026, 7, 15, 11, 0, tzinfo=timezone.utc)
    rows = [_row(when, estimate=10.0, reported=12.0, surprise_pct=None)]
    fetch = _fetch_from(rows)
    o = _run(verify_earnings_outcome(
        "INFY", "eps", "beat",
        surprise_threshold_pct=15.0,    # need ≥15% beat
        fetch=fetch,
    ))
    # 20% computed surprise → still a beat
    assert o.matched is True
    assert o.decision == "beat"
    assert o.surprise_pct == pytest.approx(20.0)


def test_verify_zero_estimate_special_case_does_not_explode():
    """An estimate of exactly 0 historically caused div-by-zero in
    naive implementations. Verifier handles it as inf-positive surprise
    (a beat), and inf-negative (a miss), without raising."""
    when = datetime(2026, 7, 15, 11, 0, tzinfo=timezone.utc)
    rows_beat = [_row(when, estimate=0.0, reported=0.5, surprise_pct=None)]
    o_beat = _run(verify_earnings_outcome(
        "INFY", "eps", "beat", fetch=_fetch_from(rows_beat),
    ))
    assert o_beat.matched is True

    rows_miss = [_row(when, estimate=0.0, reported=-0.5, surprise_pct=None)]
    o_miss = _run(verify_earnings_outcome(
        "INFY", "eps", "miss", fetch=_fetch_from(rows_miss),
    ))
    assert o_miss.matched is True
