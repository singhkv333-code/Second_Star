"""Per-symbol earnings calendar + outcome verifier (beta).

This package powers ``trigger.earnings`` — the scheduler-armed,
outcome-verified earnings trigger. It is the earnings-specific sibling
of :mod:`backend.macro_events`: same fail-safe philosophy, same fire-
once / audit-context shape, but the calendar is *live* (yfinance per
ticker) and the verifier is arithmetic (reported vs. estimate) rather
than LLM-parsed text.

Public surface:

  - :mod:`calendar`  — :class:`EarningsEventDef`, :func:`get_next_earnings`,
    :func:`due_event`.
  - :mod:`verifier`  — :func:`verify_earnings_outcome` layered checker.
  - :mod:`outcomes`  — :class:`EarningsOutcome` dataclass.

Nothing here fires anything by itself; the scheduler poll loop calls
:func:`verify_earnings_outcome` and, on a confident match, calls
``fire_external_event``.
"""
from __future__ import annotations

from backend.earnings_events.calendar import (
    EarningsEventDef,
    due_event,
    get_next_earnings,
)
from backend.earnings_events.outcomes import EARNINGS_DECISIONS, EarningsOutcome
from backend.earnings_events.verifier import verify_earnings_outcome

__all__ = [
    "EARNINGS_DECISIONS",
    "EarningsEventDef",
    "EarningsOutcome",
    "due_event",
    "get_next_earnings",
    "verify_earnings_outcome",
]
