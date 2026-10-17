"""Macro-event calendar registry — window logic + instance keys."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from backend.macro_events.calendar import (
    due_event,
    events_for_kind,
    next_event,
)
from backend.macro_events.source_of_truth import all_kinds, get_source_of_truth


def test_every_calendar_kind_has_a_source_of_truth() -> None:
    """No orphan kinds — every calendar kind resolves to a verification
    source (and vice-versa via the allow-list)."""
    for kind in ("rbi_mpc", "us_fomc", "us_cpi", "india_cpi"):
        assert events_for_kind(kind), f"{kind} has no calendar entries"
        assert get_source_of_truth(kind) is not None


def test_source_of_truth_kinds_match_allowlist() -> None:
    assert set(all_kinds()) == {"rbi_mpc", "us_fomc", "india_cpi", "us_cpi"}


def test_due_event_inside_window() -> None:
    ev = events_for_kind("rbi_mpc")[0]
    # 1 minute after fire → inside the verify window.
    now = ev.fire_at_utc + timedelta(minutes=1)
    due = due_event("rbi_mpc", now)
    assert due is not None
    assert due.instance_key() == ev.instance_key()


def test_due_event_before_fire_is_none() -> None:
    ev = events_for_kind("rbi_mpc")[0]
    now = ev.fire_at_utc - timedelta(minutes=5)
    assert due_event("rbi_mpc", now) is None


def test_due_event_after_window_is_none() -> None:
    ev = events_for_kind("rbi_mpc")[0]
    now = ev.window_end_utc + timedelta(minutes=1)
    assert due_event("rbi_mpc", now) is None


def test_instance_key_encodes_date() -> None:
    ev = events_for_kind("rbi_mpc")[0]
    assert ev.instance_key() == f"rbi_mpc:{ev.fire_at_utc.date().isoformat()}"


def test_next_event_is_strictly_after() -> None:
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    nxt = next_event("us_fomc", base)
    assert nxt is not None
    assert nxt.fire_at_utc > base


def test_unknown_kind_has_no_events() -> None:
    assert events_for_kind("nope") == []
    assert due_event("nope", datetime(2026, 6, 1, tzinfo=timezone.utc)) is None
