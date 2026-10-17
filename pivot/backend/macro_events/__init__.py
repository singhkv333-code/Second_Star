"""Macro-event calendar + layered outcome verifier (conservative beta).

The known-date registry behind ``trigger.scheduled_macro``: which macro
releases exist (RBI MPC, FOMC, CPI), when, and which outcomes a workflow may
name. ``workflows.propose`` reads it to validate a drafted trigger.

Public surface:
  - ``calendar``        — typed known-date registry (MacroEventDef).
  - ``source_of_truth`` — per-category canonical-verification source.
  - ``outcomes``        — OutcomeResult dataclass.

The outcome verifier and the scheduler loop that fired these triggers were
removed (they were never reached), so nothing here fires anything.
"""
from __future__ import annotations

from backend.macro_events.outcomes import (
    MACRO_DECISIONS,
    OutcomeResult,
)

__all__ = ["OutcomeResult", "MACRO_DECISIONS"]
