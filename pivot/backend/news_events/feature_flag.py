"""Single source of truth for the news_events on/off switch.

All news_events code reads the flag through here so a future change
(per-user gating, kill-switch table, etc.) is a single-file edit.
"""
from __future__ import annotations

from backend.config import settings


def is_enabled() -> bool:
    """Return True when the news_events subsystem should be active.

    Phase 1 reads a single global env-driven flag. Future phases may
    gate per-user or per-tier; keep call sites going through this
    function so that change is invisible to them.
    """
    return bool(settings.news_events_enabled)
