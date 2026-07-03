"""View Markets — Phase 2 enrichment + scoring + lifecycle pipeline.

Belief -> Expression -> Deployment. Operates on MANUALLY-CURATED views (PLAN §7
beta decision; the automatic EVENT/RELATIVE/THEME generators are DEFERRED). The
eight modules compose like this:

    feeds ──────────────┐
                        ├─> event_study ──┐
    implied_move ──┐    │                 ├─> confidence ─┐
                   ├──> expectations ─────┘               ├─> curation ─> (a published MarketView)
    transmission ──┘                                       │
                                                  lifecycle (scheduler worker advancing status)

  * transmission   — cause->effect DAG (rows in view_transmission); seed from a
                     thematic_map scenario OR author manually.
  * implied_move   — option-implied expected move + implied probability.
  * feeds          — macro-calendar dated events, analog-event sampling,
                     consensus (EAR-only fallback), verifier read.
  * event_study    — CAR/CAAR/BHAR market-model abnormal returns vs NIFTY,
                     surprise-conditioned, BMP + non-parametric significance,
                     Trust Battery -> verdict.
  * expectations   — surprise aggregator (implied_move PRIMARY/user-facing +
                     consensus; prediction-market odds = HIDDEN PRIOR only).
  * confidence     — two-dial scorer (outcome vs expression) + Alignment Score,
                     gated by the Trust verdict, suppressed below MinTRL.
  * curation       — manual authoring/publish service (the generator replacement).
  * lifecycle      — module-level worker advancing MarketView.status + a
                     flag-gated scheduler registration.

All services are importable regardless of ``config.view_markets_enabled``; the
flag only gates the lifecycle scheduler registration + (Phase 7) the router.
Importing this package is side-effect-free (no scheduler start, no DB connect).
"""
from __future__ import annotations

from backend.view_markets import (
    confidence,
    curation,
    event_study,
    expectations,
    feeds,
    implied_move,
    lifecycle,
    transmission,
)

__all__ = [
    "transmission",
    "implied_move",
    "feeds",
    "event_study",
    "expectations",
    "confidence",
    "curation",
    "lifecycle",
]
