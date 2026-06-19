"""Macro-event calendar + layered outcome verifier (conservative beta).

This package powers ``trigger.scheduled_macro`` — the calendar-armed,
outcome-verified event trigger. It is intentionally a *sibling* of
``news_events`` (not inside it): the RSS firehose and prediction-market
machinery in ``news_events`` are consumed here as libraries, but macro
verification is a distinct concern (we KNOW the date; we VERIFY the
specific outcome before firing a real action).

Public surface:
  - ``calendar``        — typed known-date registry (MacroEventDef).
  - ``source_of_truth`` — per-category canonical-verification source.
  - ``verifier``        — verify_macro_outcome(...) layered checker.
  - ``outcomes``        — OutcomeResult dataclass.

Nothing here fires anything by itself; the scheduler poll loop
(Slice 3) calls ``verify_macro_outcome`` and, on a confident match,
calls ``fire_external_event``.
"""
from __future__ import annotations

from backend.macro_events.outcomes import (
    MACRO_DECISIONS,
    OutcomeResult,
)

__all__ = ["OutcomeResult", "MACRO_DECISIONS"]
