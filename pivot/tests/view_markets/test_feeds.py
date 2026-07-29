"""Focused unit tests for ``backend.view_markets.feeds``.

Self-contained: the macro calendar is real (hardcoded 2026 dates, no network),
and the only async/external dependency — the verifier — is monkeypatched so the
``read_event_outcome`` passthrough is exercised without RSS/LLM/PM calls.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from backend.macro_events.outcomes import OutcomeResult
from backend.view_markets import feeds


# A deterministic "now" anchored inside 2026, after the Feb/Apr RBI prints but
# before the Jun one, so the calendar split is predictable.
_NOW = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)


# ── dated_events ─────────────────────────────────────────────────────
def test_dated_events_returns_sorted_normalised_events() -> None:
    events = feeds.dated_events("rbi_mpc")
    assert events, "rbi_mpc has known 2026 occurrences"
    assert all(isinstance(e, feeds.DatedEvent) for e in events)
    # Sorted ascending by fire time, and normalised fields present.
    fire_times = [e.fire_at_utc for e in events]
    assert fire_times == sorted(fire_times)
    first = events[0]
    assert first.kind == "rbi_mpc"
    assert first.instance_key.startswith("rbi_mpc:")
    assert first.source_of_truth_id == "rbi_mpc"
    assert first.window_end_utc > first.fire_at_utc


def test_dated_events_after_and_limit() -> None:
    after = datetime(2026, 6, 1, tzinfo=timezone.utc)
    events = feeds.dated_events("rbi_mpc", after=after, limit=2)
    assert len(events) <= 2
    assert all(e.fire_at_utc > after for e in events)


def test_dated_events_after_naive_datetime_is_coerced_utc() -> None:
    # A naive datetime must not raise (coerced to UTC).
    events = feeds.dated_events("us_fomc", after=datetime(2026, 1, 1))
    assert events and all(isinstance(e, feeds.DatedEvent) for e in events)


def test_dated_events_unknown_kind_empty() -> None:
    assert feeds.dated_events("not_a_kind") == []


# ── due_dated_event ──────────────────────────────────────────────────
def test_due_dated_event_inside_window() -> None:
    # The 6 Jun 2026 RBI MPC fires at 04:30 UTC with a 240-min window.
    inside = datetime(2026, 6, 6, 5, 0, tzinfo=timezone.utc)
    due = feeds.due_dated_event("rbi_mpc", now=inside)
    assert due is not None
    assert due.kind == "rbi_mpc"
    assert due.instance_key == "rbi_mpc:2026-06-06"


def test_due_dated_event_outside_window_none() -> None:
    outside = datetime(2026, 6, 6, 12, 0, tzinfo=timezone.utc)  # window closed
    assert feeds.due_dated_event("rbi_mpc", now=outside) is None


# ── sample_analog_events ─────────────────────────────────────────────
def test_sample_analog_events_macro_newest_first_past_only() -> None:
    analogs = feeds.sample_analog_events("rbi_mpc", now=_NOW)
    assert analogs, "there are RBI MPC prints before 2026-05-01"
    assert all(isinstance(a, feeds.AnalogEvent) for a in analogs)
    # All strictly in the past relative to _NOW.
    assert all(
        datetime(a.event_date.year, a.event_date.month, a.event_date.day,
                 tzinfo=timezone.utc) < _NOW
        for a in analogs
    )
    # Newest first.
    dates = [a.event_date for a in analogs]
    assert dates == sorted(dates, reverse=True)
    # Provenance stamped, no fabricated surprise.
    assert analogs[0].meta.get("source") == "macro_calendar"
    assert analogs[0].surprise_sign is None
    assert analogs[0].surprise_magnitude is None


def test_sample_analog_events_max_events_caps() -> None:
    analogs = feeds.sample_analog_events("us_fomc", now=_NOW, max_events=1)
    assert len(analogs) == 1


def test_sample_analog_events_lookback_years_filters() -> None:
    # A 0-year lookback window excludes everything (cutoff == now).
    analogs = feeds.sample_analog_events("rbi_mpc", now=_NOW, lookback_years=0)
    assert analogs == []


def test_sample_analog_events_non_macro_tag_is_stub_empty() -> None:
    # Earnings / news analogs are an explicit STUB boundary -> [].
    assert feeds.sample_analog_events("INFY_earnings", now=_NOW) == []
    assert feeds.sample_analog_events("some_news_tag", now=_NOW) == []


# ── consensus_for_event ──────────────────────────────────────────────
def test_consensus_for_event_ear_fallback() -> None:
    cp = feeds.consensus_for_event("us_cpi", metric="cpi_yoy")
    assert isinstance(cp, feeds.ConsensusPoint)
    assert cp.available is False
    assert cp.source == "ear_fallback"
    assert cp.expected_value is None
    assert cp.metric == "cpi_yoy"
    assert cp.note and "EAR" in cp.note


def test_consensus_for_event_defaults_metric_to_tag() -> None:
    cp = feeds.consensus_for_event("rbi_mpc")
    assert cp.metric == "rbi_mpc"


# ── read_event_outcome (async passthrough) ───────────────────────────
@pytest.mark.asyncio
async def test_read_event_outcome_passthrough(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    async def _fake_verify(kind, expected_outcome, **kwargs):
        captured["kind"] = kind
        captured["expected_outcome"] = expected_outcome
        captured["kwargs"] = kwargs
        return OutcomeResult(
            matched=True, decision="cut", confidence=0.9, tier="official",
        )

    # The function imports the verifier lazily inside its body.
    monkeypatch.setattr(
        "backend.macro_events.verifier.verify_macro_outcome", _fake_verify
    )

    result = await feeds.read_event_outcome(
        "rbi_mpc", "cut", min_confidence=0.8, comparison="<", threshold=6.0,
        allow_prediction_market_fallback=False,
    )
    assert result.matched is True
    assert result.decision == "cut"
    assert captured["kind"] == "rbi_mpc"
    assert captured["expected_outcome"] == "cut"
    assert captured["kwargs"]["min_confidence"] == 0.8
    assert captured["kwargs"]["comparison"] == "<"
    assert captured["kwargs"]["threshold"] == 6.0
    assert captured["kwargs"]["allow_prediction_market_fallback"] is False


@pytest.mark.asyncio
async def test_read_event_outcome_unknown_kind_is_failsafe() -> None:
    # No monkeypatch: real verifier short-circuits an unknown kind to
    # OutcomeResult.unknown without any network (get_source_of_truth -> None).
    result = await feeds.read_event_outcome("not_a_kind", "cut")
    assert result.matched is False
    assert result.decision == "unknown"
