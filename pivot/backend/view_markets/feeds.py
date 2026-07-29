"""View Markets — event / surprise data-feed shims (STUB after event-
automation removal).

The macro / news / prediction-market subsystems that used to back this module
have been removed from the codebase; the dated-events calendar, the analog-
event sampler, the consensus feed and the outcome verifier all now return
empty / ``available=False`` results. The dataclasses and function signatures
are retained so downstream callers (``event_study``, ``expectations``,
``lifecycle``) keep importing cleanly and treat every result as the honest
"no data" signal (``insufficient_data``).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Optional


@dataclass(frozen=True)
class DatedEvent:
    """A single calendar occurrence, normalised off the (removed) macro
    calendar. No longer produced by any live feed."""

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


def dated_events(
    kind: str,  # noqa: ARG001 — retained for signature compatibility
    *,
    after: Optional[datetime] = None,  # noqa: ARG001
    limit: Optional[int] = None,  # noqa: ARG001
) -> list[DatedEvent]:
    """No dated-events feed is wired any more; returns an empty list."""
    return []


def due_dated_event(
    kind: str,  # noqa: ARG001 — retained for signature compatibility
    *,
    now: Optional[datetime] = None,  # noqa: ARG001
) -> Optional[DatedEvent]:
    """No dated-events feed is wired any more; returns ``None``."""
    return None


def sample_analog_events(
    tag: str,  # noqa: ARG001 — retained for signature compatibility
    *,
    lookback_years: Optional[int] = None,  # noqa: ARG001
    max_events: Optional[int] = None,  # noqa: ARG001
    now: Optional[datetime] = None,  # noqa: ARG001
) -> list[AnalogEvent]:
    """No analog-event feed is wired any more; returns an empty list. The
    caller MUST treat the empty sample as ``insufficient_data``."""
    return []


def consensus_for_event(
    tag: str,
    *,
    metric: Optional[str] = None,
) -> ConsensusPoint:
    """Consensus expected-value for an event metric, with EAR-only fallback.

    No consensus feed is wired, so this returns a ``ConsensusPoint`` with
    ``available=False`` and ``source="ear_fallback"``.
    """
    return ConsensusPoint(
        metric=metric or tag,
        expected_value=None,
        source="ear_fallback",
        available=False,
        note=(
            "STUB: no consensus feed wired. Use the option-implied expected "
            "move (EAR) alone — no consensus delta."
        ),
    )


async def read_event_outcome(
    kind: str,  # noqa: ARG001
    expected_outcome: str,  # noqa: ARG001
    *,
    min_confidence: float = 0.85,  # noqa: ARG001
    comparison: Optional[str] = None,  # noqa: ARG001
    threshold: Optional[float] = None,  # noqa: ARG001
    allow_prediction_market_fallback: bool = True,  # noqa: ARG001
) -> Any:
    """The verifier this used to wrap has been removed; returns ``None`` so
    callers see the honest "unable to resolve" signal rather than a
    fabricated outcome. Kept async so the existing await sites still work.
    """
    return None


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
