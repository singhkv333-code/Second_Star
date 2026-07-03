"""View Markets — event / surprise data-feed shims.

Thin wrappers over data that ALREADY exists in the repo, normalised into the
shapes ``event_study`` / ``expectations`` / ``lifecycle`` consume. NO new
external scraping — anything not yet wired is returned as a clearly-marked
stub (``available=False`` / empty list with a STUB note in ``meta``) so a
build agent can't mistake an unwired feed for a live one.

Three responsibilities:
  1. DATED EVENTS — the 2026 RBI / CPI / FOMC calendar (for event views +
     lifecycle resolution timing).
  2. ANALOG-EVENT SAMPLING — gather past instances of an event *tag* to form
     the event-study sample (turns one anecdote into a base rate).
  3. CONSENSUS INPUT — an analyst/consensus expected-value point, with an
     **EAR-only fallback** (Expected-Abnormal-Return: the option-implied
     expected move alone, no consensus delta) when no consensus number exists.

Reuses (real interfaces, pinned 2026-06-29):
  * ``backend.macro_events.calendar`` — ``MacroEventDef`` (``.kind /
    .fire_at_utc / .verify_window_minutes / .source_of_truth_id / .label /
    .window_end_utc / .instance_key()``), ``events_for_kind(kind)``,
    ``due_event(kind, now)``, ``next_event(kind, after)``. Known kinds:
    ``rbi_mpc / us_fomc / us_cpi / india_cpi``.
  * ``backend.macro_events.verifier.verify_macro_outcome(kind, expected_outcome,
    *, min_confidence=0.85, comparison=None, threshold=None,
    allow_prediction_market_fallback=True, ...) -> OutcomeResult`` (ASYNC).
  * ``backend.macro_events.outcomes.OutcomeResult`` (``.matched / .decision /
    .confidence / .tier / .evidence / .audit``; ``.unknown(reason=...)``).
  * ``backend.macro_events.source_of_truth.all_kinds`` — the conservative-beta
    macro-kind allow-list (used to tell a macro tag from a STUB earnings/news
    tag).

The macro feed is independent of ``news_events_enabled`` (mirrors the
``macro_events_enabled`` gating); reading the calendar itself is always cheap
and flag-free.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import TYPE_CHECKING, Optional

from backend.macro_events import calendar as _calendar
from backend.macro_events.calendar import MacroEventDef
from backend.macro_events.source_of_truth import all_kinds as _all_macro_kinds

if TYPE_CHECKING:  # pragma: no cover - typing only
    from backend.macro_events.outcomes import OutcomeResult


# The conservative-beta macro-event allow-list (rbi_mpc / us_fomc / us_cpi /
# india_cpi). Any tag NOT in here is treated as a non-macro (earnings / news)
# analog tag, which is an explicit STUB boundary in this module.
_MACRO_KINDS: frozenset[str] = frozenset(_all_macro_kinds())


@dataclass(frozen=True)
class DatedEvent:
    """A single calendar occurrence, normalised off ``MacroEventDef``."""

    kind: str
    label: str
    fire_at_utc: datetime
    window_end_utc: datetime
    instance_key: str
    source_of_truth_id: str


@dataclass(frozen=True)
class AnalogEvent:
    """One historical instance of an event tag, for the event-study sample.

    ``surprise_sign`` / ``surprise_magnitude`` are the conditioning variables
    the event study filters on (only fill them when a real surprise read
    exists — never fabricate). ``meta`` carries source provenance."""

    tag: str
    event_date: date
    label: Optional[str] = None
    surprise_sign: Optional[str] = None       # positive | negative | inline
    surprise_magnitude: Optional[float] = None
    meta: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ConsensusPoint:
    """An expected-value reading for an event metric.

    ``available`` is False on the EAR-only fallback (no consensus exists); the
    caller then leans on the option-implied expected move alone. ``source`` ∈
    {"consensus", "model", "ear_fallback"}."""

    metric: str
    expected_value: Optional[float]
    source: str
    available: bool
    note: Optional[str] = None


# ── time helpers ─────────────────────────────────────────────────────
def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """Normalise to a tz-aware UTC datetime (assume UTC for naive input).

    The calendar stores tz-aware UTC times; comparing those against a naive
    ``now`` / ``after`` would raise. We coerce rather than reject so callers
    that pass a plain ``datetime.utcnow()`` still get sane behaviour.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _to_dated_event(ev: MacroEventDef) -> DatedEvent:
    return DatedEvent(
        kind=ev.kind,
        label=ev.label,
        fire_at_utc=ev.fire_at_utc,
        window_end_utc=ev.window_end_utc,
        instance_key=ev.instance_key(),
        source_of_truth_id=ev.source_of_truth_id,
    )


# ── 1) dated events ──────────────────────────────────────────────────
def dated_events(
    kind: str,
    *,
    after: Optional[datetime] = None,
    limit: Optional[int] = None,
) -> list[DatedEvent]:
    """All known 2026 occurrences for ``kind``, sorted by fire time.

    Wraps ``calendar.events_for_kind`` (already sorted ascending). When
    ``after`` is set, only occurrences strictly after it are returned.
    ``limit`` caps the result. Unknown kind -> ``[]``.
    """
    events = _calendar.events_for_kind(kind)
    after_utc = _as_utc(after)
    if after_utc is not None:
        events = [e for e in events if e.fire_at_utc > after_utc]
    if limit is not None and limit >= 0:
        events = events[:limit]
    return [_to_dated_event(e) for e in events]


def due_dated_event(kind: str, *, now: Optional[datetime] = None) -> Optional[DatedEvent]:
    """The occurrence whose verify-window currently contains ``now`` (or None).

    Wraps ``calendar.due_event``. Used by ``lifecycle`` to know when an event
    view is in its resolution window. ``now`` defaults to real UTC now.
    """
    now_utc = _as_utc(now) or _utcnow()
    ev = _calendar.due_event(kind, now_utc)
    return _to_dated_event(ev) if ev is not None else None


# ── 2) analog-event sampling ─────────────────────────────────────────
def sample_analog_events(
    tag: str,
    *,
    lookback_years: Optional[int] = None,
    max_events: Optional[int] = None,
    now: Optional[datetime] = None,
) -> list[AnalogEvent]:
    """Gather past instances of an event ``tag`` for the event-study sample.

    For a MACRO ``tag`` that maps to a calendar kind (``rbi_mpc`` / ``us_fomc``
    / ``us_cpi`` / ``india_cpi``), derives past occurrences from
    ``calendar.events_for_kind`` whose ``fire_at_utc`` is strictly before
    ``now`` (default: real now), newest first, optionally bounded by
    ``lookback_years`` and capped at ``max_events``.

    STUB BOUNDARY: per-stock EARNINGS analogs and arbitrary NEWS tags are NOT
    yet wired (no Moneycontrol earnings-calendar feed in this module). For
    those tags this returns ``[]`` — the caller MUST treat an empty analog
    sample as ``insufficient_data`` (never invent events). The (empty) result
    is the honest "no data" signal; provenance for non-empty rows is in each
    ``AnalogEvent.meta``.
    """
    now_utc = _as_utc(now) or _utcnow()

    # STUB: non-macro (earnings / news) analog tags are not wired.
    if tag not in _MACRO_KINDS:
        return []

    events = _calendar.events_for_kind(tag)
    past = [e for e in events if e.fire_at_utc < now_utc]

    if lookback_years is not None:
        cutoff = now_utc - timedelta(days=365 * lookback_years)
        past = [e for e in past if e.fire_at_utc >= cutoff]

    # Newest first — the most recent analogs are the most representative.
    past.sort(key=lambda e: e.fire_at_utc, reverse=True)

    if max_events is not None and max_events >= 0:
        past = past[:max_events]

    return [
        AnalogEvent(
            tag=tag,
            event_date=e.fire_at_utc.date(),
            label=e.label,
            # No surprise read is wired here — the event study conditions on
            # surprise only when a real reading exists; never fabricated.
            surprise_sign=None,
            surprise_magnitude=None,
            meta={
                "kind": e.kind,
                "instance_key": e.instance_key(),
                "source": "macro_calendar",
                "source_of_truth_id": e.source_of_truth_id,
            },
        )
        for e in past
    ]


# ── 3) consensus input (EAR-only fallback) ───────────────────────────
def consensus_for_event(
    tag: str,
    *,
    metric: Optional[str] = None,
) -> ConsensusPoint:
    """Consensus expected-value for an event metric, with EAR-only fallback.

    No consensus feed is wired in beta, so this returns a ``ConsensusPoint``
    with ``available=False`` and ``source="ear_fallback"`` (the note explains
    the caller should use the option-implied expected move alone — no
    consensus delta). The signature is forward-compatible with a real
    consensus source dropping in later (it would return ``available=True`` /
    ``source="consensus"`` with a real ``expected_value``).
    """
    return ConsensusPoint(
        metric=metric or tag,
        expected_value=None,
        source="ear_fallback",
        available=False,
        note=(
            "STUB: no consensus feed wired in beta. Use the option-implied "
            "expected move (EAR) alone — no consensus delta."
        ),
    )


# ── 4) outcome read (verifier passthrough) ───────────────────────────
async def read_event_outcome(
    kind: str,
    expected_outcome: str,
    *,
    min_confidence: float = 0.85,
    comparison: Optional[str] = None,
    threshold: Optional[float] = None,
    allow_prediction_market_fallback: bool = True,
) -> "OutcomeResult":
    """Thin async wrapper over ``verifier.verify_macro_outcome``.

    Returns the layered ``OutcomeResult`` (``.matched`` gates a resolution).
    Used by ``lifecycle`` to read a real outcome and by ``expectations`` to
    backfill ``resolved_value``. Fail-safe: the underlying verifier returns
    ``OutcomeResult.unknown`` on any uncertainty (unknown kind, down feed,
    low confidence, hallucination guard).

    The verifier import is deferred to call-time so importing this module does
    not pull in the RSS/LLM/prediction-market machinery (keeps the package
    side-effect-free and cheap to import).
    """
    from backend.macro_events.verifier import verify_macro_outcome

    return await verify_macro_outcome(
        kind,
        expected_outcome,
        min_confidence=min_confidence,
        comparison=comparison,
        threshold=threshold,
        allow_prediction_market_fallback=allow_prediction_market_fallback,
    )


__all__ = [
    "DatedEvent",
    "AnalogEvent",
    "ConsensusPoint",
    "dated_events",
    "due_dated_event",
    "sample_analog_events",
    "consensus_for_event",
    "read_event_outcome",
]
