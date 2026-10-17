"""Phase 4 — natural-language parsing for the news_events surface.

Two modules:

  - ``event_spec_parser``: turns a user's free-form text into a
    structured ``ParsedSpec`` (description + tier + keyword_set +
    resolution_criteria + retraction_policy).
  - ``disambiguation``: when the parser returns Tier 3, generates
    1-3 multi-choice questions and applies the answers to mutate
    the pending spec into a draft.
"""
from __future__ import annotations
