"""View Markets — surprise / market-expectations aggregator.

Combines the inputs that frame a view's SURPRISE — "what's priced in" vs the
view's own number — and persists them to ``view_expectations``:

  * PRIMARY (user-facing): Pivot's OWN option-implied expected move + implied
    probability (``implied_move``). Persisted with ``source="model"``.
  * Optional consensus expected-value (``feeds.consensus_for_event``), with the
    EAR-only fallback. Persisted with ``source="consensus"`` when available.

Surprise sign: ``positive`` (user_view > expected), ``negative`` (user_view <
expected), ``inline`` (within tolerance). Surfaced number is ALWAYS Pivot's
option-implied value, not a venue odds/bet.

Reuses (real interfaces, pinned 2026-06-29):
  * ``backend.view_markets.implied_move.{implied_move, implied_probability}``.
  * ``backend.view_markets.feeds.{consensus_for_event, ConsensusPoint}``.
  * ``backend.schemas.ViewExpectationInput`` (source / market_id /
    expected_value / user_view_value / surprise_sign).
  * ``backend.models.ViewExpectation`` (writes; ``source`` enum ∈
    {consensus, model}).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:  # pragma: no cover - typing only
    from sqlalchemy.orm import Session

    from backend.models import ViewExpectation

logger = logging.getLogger(__name__)


# Default band (fraction of expected_value) within which surprise is "inline".
INLINE_TOLERANCE: float = 0.05


@dataclass(frozen=True)
class SurpriseFraming:
    """Expected vs UserView vs Surprise for one view.

    User-facing fields: ``expected_value`` (Pivot's option-implied number),
    ``implied_probability``, ``user_view_value``, ``surprise_sign``,
    ``surprise_magnitude``, ``source`` ("model" / "consensus").

    ``hidden_prior`` / ``hidden_prior_source`` carry the prediction-market odds
    — INTERNAL ONLY, never serialized to a user surface (PROGA). They exist so
    the OUTCOME dial can compute "edge vs priced" without Pivot becoming a
    prediction exchange."""

    underlying: Optional[str]
    expected_value: Optional[float]
    user_view_value: Optional[float]
    surprise_sign: Optional[str]               # positive | negative | inline
    surprise_magnitude: Optional[float]
    implied_probability: Optional[float]
    source: str                                # "model" | "consensus"
    hidden_prior: Optional[float] = None       # PM odds — NEVER surfaced
    hidden_prior_source: Optional[str] = None  # "polymarket" | "kalshi" | None
    notes: tuple[str, ...] = field(default_factory=tuple)


def compute_surprise(
    db: "Session",
    *,
    underlying: str,
    user_view_value: Optional[float] = None,
    target_level: Optional[float] = None,
    direction: str = "above",
    expiry: Optional[str] = None,
    horizon_days: Optional[int] = None,
    consensus_tag: Optional[str] = None,
    inline_tolerance: float = INLINE_TOLERANCE,
) -> SurpriseFraming:
    """Build the user-facing surprise framing (SYNC, no prediction-market read).

    PRIMARY path: ``implied_move`` for the priced expected move (and
    ``implied_probability`` when ``target_level`` is given). Optionally folds in
    ``consensus_for_event(consensus_tag)`` (EAR-only fallback when unavailable).
    Computes ``surprise_sign`` / ``surprise_magnitude`` from ``user_view_value``
    vs the expected value within ``inline_tolerance``. ``hidden_prior`` is left
    ``None`` here — augment separately via
    :func:`augment_with_prediction_market_prior` when flags allow.
    """
    # Local imports keep the module importable without the (heavier) option /
    # feed deps and avoid import cycles (siblings build in parallel).
    from backend.view_markets.implied_move import (
        implied_move,
        implied_probability,
    )

    notes: list[str] = []

    expected_value: Optional[float] = None
    source = "model"

    # PRIMARY: Pivot's OWN option-implied "what's priced in" yardstick.
    im = implied_move(
        db, underlying, expiry=expiry, horizon_days=horizon_days,
    )
    if im is not None:
        expected_value = im.forward
        notes.append(
            "expected_value = option-implied forward "
            f"(EM +/-{im.expected_move_abs:.2f} / {im.expected_move_pct:.2%}, "
            f"source={im.source})"
        )
    else:
        notes.append(
            "no option-implied move (chain/IV unavailable) -- expected_value "
            "degraded to None"
        )

    # Optionally fold in a consensus expected-value. The consensus feed is an
    # EAR-only fallback in beta (``available=False``); when a real consensus
    # number lands it becomes the surprise reference (source="consensus").
    if consensus_tag is not None:
        from backend.view_markets.feeds import consensus_for_event

        cp = consensus_for_event(consensus_tag)
        if cp.available and cp.expected_value is not None:
            expected_value = cp.expected_value
            source = "consensus"
            notes.append(
                f"consensus reference ({cp.metric}) overrides option-implied"
            )
        else:
            notes.append(
                cp.note
                or "consensus unavailable -- EAR fallback (option-implied only)"
            )

    # User-facing implied probability that price clears the target level.
    implied_prob: Optional[float] = None
    if target_level is not None:
        implied_prob = implied_probability(
            db,
            underlying,
            target_level=target_level,
            direction=direction,
            expiry=expiry,
            horizon_days=horizon_days,
        )
        if implied_prob is None:
            notes.append("implied probability unavailable (no usable IV/T)")

    # Surprise: user's own number vs what's priced in. Inline = within
    # ``inline_tolerance`` (fraction of |expected_value|).
    surprise_sign: Optional[str] = None
    surprise_magnitude: Optional[float] = None
    if user_view_value is not None and expected_value is not None:
        diff = user_view_value - expected_value
        surprise_magnitude = abs(diff)
        denom = abs(expected_value)
        rel = (diff / denom) if denom > 0 else 0.0
        if abs(rel) <= inline_tolerance:
            surprise_sign = "inline"
        elif rel > 0:
            surprise_sign = "positive"
        else:
            surprise_sign = "negative"

    return SurpriseFraming(
        underlying=underlying,
        expected_value=expected_value,
        user_view_value=user_view_value,
        surprise_sign=surprise_sign,
        surprise_magnitude=surprise_magnitude,
        implied_probability=implied_prob,
        source=source,
        hidden_prior=None,
        hidden_prior_source=None,
        notes=tuple(notes),
    )


async def augment_with_prediction_market_prior(
    framing: SurpriseFraming,
    *,
    pm_query: Optional[str] = None,  # noqa: ARG001 — retained for call-site compat
    kalshi_ticker: Optional[str] = None,  # noqa: ARG001 — retained for call-site compat
) -> SurpriseFraming:
    """No-op stub retained for call-site compatibility.

    Prediction-market venues are no longer wired; this used to read
    Polymarket / Kalshi to fill a hidden edge-vs-priced prior. Returns
    ``framing`` unchanged.
    """
    return framing


def persist_expectations(
    db: "Session",
    view_id: str,
    framing: SurpriseFraming,
    *,
    replace: bool = True,
) -> list["ViewExpectation"]:
    """Persist the USER-FACING expectation row(s) to ``view_expectations``.

    Writes a ``source="model"`` row from the option-implied expected value
    (and a ``source="consensus"`` row when consensus was available). The
    prediction-market ``hidden_prior`` is DELIBERATELY NOT written as a
    surfaced row in beta (PROGA). ``replace=True`` clears prior rows for the
    view first. Does NOT commit (caller owns the txn). Returns the ORM rows.

    Reads/writes: ``view_expectations`` (write); ``market_views`` (FK target).
    """
    from backend.models import ExpectationSource, ViewExpectation

    if replace:
        db.query(ViewExpectation).filter(
            ViewExpectation.view_id == view_id,
        ).delete(synchronize_session=False)

    # PROGA: only the user-facing reference (model OR consensus) is written;
    # the prediction-market ``hidden_prior`` is DELIBERATELY never persisted.
    source_enum = (
        ExpectationSource.consensus
        if framing.source == "consensus"
        else ExpectationSource.model
    )

    rows: list[ViewExpectation] = []
    has_content = (
        framing.expected_value is not None
        or framing.user_view_value is not None
        or framing.surprise_sign is not None
    )
    if has_content:
        row = ViewExpectation(
            view_id=view_id,
            source=source_enum,
            market_id=None,  # never a PM venue id in beta
            expected_value=framing.expected_value,
            user_view_value=framing.user_view_value,
            surprise_sign=framing.surprise_sign,
        )
        db.add(row)
        rows.append(row)

    db.flush()
    return rows


def backfill_resolved_value(
    db: "Session",
    view_id: str,
    *,
    resolved_value: float,
) -> list["ViewExpectation"]:
    """Backfill ``resolved_value`` on a view's expectation rows once the event
    resolves (called by ``lifecycle`` on the open->resolved transition)."""
    from backend.models import ViewExpectation

    rows = (
        db.query(ViewExpectation)
        .filter(ViewExpectation.view_id == view_id)
        .all()
    )
    for row in rows:
        row.resolved_value = resolved_value
    db.flush()
    return rows


__all__ = [
    "INLINE_TOLERANCE",
    "SurpriseFraming",
    "compute_surprise",
    "augment_with_prediction_market_prior",
    "persist_expectations",
    "backfill_resolved_value",
]
