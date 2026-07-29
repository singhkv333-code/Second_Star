"""Prediction-market evaluator — Polymarket cross-check for Tier-3 specs.

Public entry point: ``evaluate_prediction_market_signal(db, spec)``.

Behaviour:
  - Returns ``None`` immediately if the spec has no
    ``prediction_market_threshold`` set (i.e. the user didn't pick
    the prediction-market option during Tier-3 disambiguation, or
    the spec isn't Tier 3).
  - On first call for a spec, search Polymarket for a market that
    matches ``spec.description``. Cache the best match's market_id
    in ``resolution_criteria.polymarket_market_id`` so subsequent
    calls bypass the search.
  - Fetch the current snapshot for the cached market. Return
    ``True`` if the YES-side price ≥ threshold, ``False`` if a
    valid snapshot was retrieved but the price is below threshold,
    and ``None`` if no usable market exists or the fetch failed
    (graceful degradation — aggregator treats this as "no signal").

The snapshot of the LAST evaluation is returned alongside the
boolean so the firing path can persist it on
``news_fired_events.prediction_market_snapshot``.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from backend.news_events.models import NewsEventSpec
from backend.news_events.sources.polymarket import (
    PolymarketSnapshot,
    get_market,
    search_markets,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PredictionMarketSignal:
    """Output of ``evaluate_prediction_market_signal``."""

    above_threshold: Optional[bool]   # True / False / None (unknown)
    threshold: Optional[float]
    snapshot: Optional[PolymarketSnapshot]
    market_id: Optional[str]


def _threshold(spec: NewsEventSpec) -> Optional[float]:
    rc = dict(spec.resolution_criteria or {})
    raw = rc.get("prediction_market_threshold")
    if raw is None:
        return None
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return None
    if v < 0.0 or v > 1.0:
        return None
    return v


def _cached_market_id(spec: NewsEventSpec) -> Optional[str]:
    rc = dict(spec.resolution_criteria or {})
    mid = rc.get("polymarket_market_id")
    return str(mid).strip() if mid else None


def _cache_market_id(db: Session, spec: NewsEventSpec, market_id: str) -> None:
    """Persist the resolved market_id back onto the spec so we don't
    re-search every tick."""
    rc = dict(spec.resolution_criteria or {})
    rc["polymarket_market_id"] = market_id
    spec.resolution_criteria = rc
    flag_modified(spec, "resolution_criteria")
    db.flush()


async def evaluate_prediction_market_signal(
    db: Session, *, spec: NewsEventSpec
) -> PredictionMarketSignal:
    """Top-level entry. See module docstring for behaviour."""
    threshold = _threshold(spec)
    if threshold is None:
        return PredictionMarketSignal(
            above_threshold=None,
            threshold=None,
            snapshot=None,
            market_id=None,
        )

    market_id = _cached_market_id(spec)
    snapshot: Optional[PolymarketSnapshot] = None

    if market_id:
        snapshot = await get_market(market_id)
    else:
        # First-time resolution: search by description, pick the
        # highest-volume / first-returned open market. Polymarket's
        # search ranks by relevance so picking the first hit is
        # adequate for v1.
        results = await search_markets(spec.description, limit=3)
        if not results:
            logger.info(
                "[news_events.prediction_market] no market found for "
                "spec_id=%s description=%r",
                spec.id, spec.description[:80],
            )
            return PredictionMarketSignal(
                above_threshold=None,
                threshold=threshold,
                snapshot=None,
                market_id=None,
            )
        # Prefer open markets (closed ones are stale).
        open_results = [r for r in results if not r.closed]
        chosen = open_results[0] if open_results else results[0]
        _cache_market_id(db, spec, chosen.market_id)
        snapshot = chosen

    if snapshot is None:
        return PredictionMarketSignal(
            above_threshold=None,
            threshold=threshold,
            snapshot=None,
            market_id=market_id,
        )

    return PredictionMarketSignal(
        above_threshold=snapshot.yes_price >= threshold,
        threshold=threshold,
        snapshot=snapshot,
        market_id=snapshot.market_id,
    )
