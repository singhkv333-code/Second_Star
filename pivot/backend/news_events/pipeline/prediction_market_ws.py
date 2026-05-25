"""WS-driven prediction-market trigger evaluator.

Sits between ``sources/polymarket_ws.PolymarketWSClient`` and the
existing firing pipeline (``pipeline/propose.fire_spec``). The client
emits a midpoint tick whenever a subscribed token's top-of-book
moves; this evaluator decides whether that tick crosses a registered
spec's threshold and, if so, hands the spec to the firing pipeline
exactly the way ``aggregate.evaluate_firing`` would.

Why bypass ``evaluate_firing`` for WS mode:
  The Tier-3 news aggregator REQUIRES ≥2 distinct YES sources to
  fire (the prediction-market signal alone counts as one, deliberately
  insufficient as a cross-check). A user-authored WS-mode spec
  ("alert me if Modi-wins YES > 70%") IS the prediction-market signal
  — it's a standalone trigger, not a cross-check. So this evaluator
  constructs a ``FiringDecision`` directly and routes it through
  ``fire_spec`` (which owns audit + workflow handoff + idempotency).

Idempotency:
  Two layers stack:
    1. In-memory: on a fire-worthy cross, we unregister the spec
       from the evaluator before calling fire_spec, so the next
       tick on the same asset is a no-op.
    2. DB: ``fire_spec`` checks ``spec.state == 'fired'`` and the
       UNIQUE(event_spec_id) constraint on ``news_fired_events``.
       A second call across a race is a clean dedup.

Threshold semantics:
  - direction "above": fires the first time mid_price >= threshold
    while the spec is registered. Sustained-above ticks AFTER the
    fire do nothing (spec is unregistered).
  - direction "below": fires the first time mid_price <= threshold.

  Edge-trigger only — we don't fire on the very first tick if it
  arrives already-above-threshold, because that's ambiguous (we just
  woke up; was the market ABOVE before we started watching, or did
  it just cross while we were connecting?). The supervisor's
  reconciliation tick gives us a baseline; the evaluator's first
  registered tick captures the pre-state. Subsequent crosses fire.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Callable, Literal, Optional

from sqlalchemy.orm import Session

from backend.news_events.models import NewsEventSpec
from backend.news_events.pipeline.aggregate import FiringDecision
from backend.news_events.pipeline.propose import FireOutcome, fire_spec

logger = logging.getLogger(__name__)


ThresholdDirection = Literal["above", "below"]


@dataclass
class _Registration:
    """One active subscription. The evaluator holds these in memory;
    the supervisor pushes them in from the active-spec scan."""

    spec_id: str
    asset_id: str
    threshold: float
    direction: ThresholdDirection
    # Pre-state baseline. None until the first tick lands; from then
    # on, we evaluate "did THIS tick cross from below→above (or
    # above→below)" rather than "is mid >= threshold". Prevents a
    # spurious fire on a stale already-crossed market we just woke
    # up watching.
    last_mid: Optional[float] = None
    registered_at_ts: float = 0.0


SessionFactory = Callable[[], Session]


class PolymarketWSEvaluator:
    """Hot-path tick → fire dispatcher.

    Owns no I/O state across reconnects — the WS client handles that.
    Owns: in-memory registry of (asset_id → registrations) and the
    last-seen mid per registration for edge detection.

    The supervisor:
      - calls ``register(spec, asset_id, threshold, direction)`` when
        an active WS-mode spec is discovered
      - calls ``unregister(spec_id)`` when a spec is cancelled, fires
        by some other path, or no longer matches the WS-mode filter
      - reads ``subscribed_asset_ids()`` to drive the WS client's
        subscription set
      - wires ``on_tick`` and ``on_resolved`` into the client's
        callbacks at construction time
    """

    def __init__(self, *, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory
        # asset_id -> {spec_id -> registration}. A single asset can
        # back many specs (different users, different thresholds).
        self._by_asset: dict[str, dict[str, _Registration]] = {}
        # spec_id -> asset_id reverse index for unregister().
        self._spec_to_asset: dict[str, str] = {}

    # ── public API used by the supervisor ─────────────────────────────

    def register(
        self,
        *,
        spec_id: str,
        asset_id: str,
        threshold: float,
        direction: ThresholdDirection = "above",
    ) -> None:
        """Add or replace a registration. Idempotent — calling with
        the same spec_id but different threshold/direction updates
        in place without resetting the baseline."""
        if not (0.0 <= threshold <= 1.0):
            logger.warning(
                "[polymarket_ws.eval] reject register spec=%s threshold=%s "
                "out of [0,1]",
                spec_id, threshold,
            )
            return
        existing = self._by_asset.get(asset_id, {}).get(spec_id)
        if existing is not None:
            existing.threshold = threshold
            existing.direction = direction
            return
        reg = _Registration(
            spec_id=spec_id,
            asset_id=asset_id,
            threshold=threshold,
            direction=direction,
            registered_at_ts=time.time(),
        )
        self._by_asset.setdefault(asset_id, {})[spec_id] = reg
        self._spec_to_asset[spec_id] = asset_id
        logger.info(
            "[polymarket_ws.eval] registered spec=%s asset=%s "
            "threshold=%.4f direction=%s",
            spec_id, asset_id, threshold, direction,
        )

    def unregister(self, spec_id: str) -> None:
        """Drop a registration. Safe to call for unknown spec_ids."""
        asset_id = self._spec_to_asset.pop(spec_id, None)
        if asset_id is None:
            return
        by_spec = self._by_asset.get(asset_id)
        if by_spec is not None:
            by_spec.pop(spec_id, None)
            if not by_spec:
                self._by_asset.pop(asset_id, None)
        logger.info(
            "[polymarket_ws.eval] unregistered spec=%s asset=%s",
            spec_id, asset_id,
        )

    def subscribed_asset_ids(self) -> set[str]:
        """Asset set the WS client should be subscribed to."""
        return set(self._by_asset.keys())

    def registered_spec_ids(self) -> set[str]:
        return set(self._spec_to_asset.keys())

    # ── hot path: WS client callback ──────────────────────────────────

    async def on_tick(self, asset_id: str, mid_price: float, ts: float) -> None:
        """Called by PolymarketWSClient on every midpoint change.

        Hot path — keep DB work off the critical section. Only opens a
        Session when an actual fire transition is detected.
        """
        regs = self._by_asset.get(asset_id)
        if not regs:
            return
        # Snapshot to a list because fire_now may unregister mid-iter.
        for reg in list(regs.values()):
            if not self._should_fire(reg, mid_price):
                reg.last_mid = mid_price
                continue
            logger.info(
                "[polymarket_ws.eval] fire-trigger spec=%s asset=%s "
                "mid=%.4f threshold=%.4f direction=%s prev=%s",
                reg.spec_id, asset_id, mid_price, reg.threshold,
                reg.direction, reg.last_mid,
            )
            # Unregister BEFORE the DB call so a flapping market
            # doesn't re-enter while fire_spec is in flight.
            self.unregister(reg.spec_id)
            await self._fire(reg, mid_price=mid_price, ts=ts)

    async def on_resolved(self, asset_id: str, payload: dict) -> None:
        """Callback for market_resolved events. Slice 1 only logs —
        slice 2 will dispatch to a resolution-watch trigger kind. We
        wire the callback now so the supervisor's client construction
        doesn't need to change later."""
        regs = self._by_asset.get(asset_id) or {}
        logger.info(
            "[polymarket_ws.eval] market_resolved received asset=%s "
            "registered_specs=%d market=%s",
            asset_id, len(regs), payload.get("market"),
        )

    # ── fire path ─────────────────────────────────────────────────────

    @staticmethod
    def _should_fire(reg: _Registration, mid: float) -> bool:
        """Edge-trigger: True iff this tick crossed the threshold in
        the registered direction, AND we've seen at least one prior
        tick as a baseline.

        The "need a baseline" rule prevents a stale already-crossed
        market from firing the instant we wake up. Once last_mid is
        non-None, subsequent crosses fire on the transition.
        """
        if reg.last_mid is None:
            return False
        if reg.direction == "above":
            return reg.last_mid < reg.threshold <= mid
        return reg.last_mid > reg.threshold >= mid

    async def _fire(
        self,
        reg: _Registration,
        *,
        mid_price: float,
        ts: float,
    ) -> Optional[FireOutcome]:
        """Re-load the spec, build a synthetic FiringDecision, hand
        off to fire_spec. All DB work is sync so we run it in a
        worker thread to keep the event loop unblocked."""
        import asyncio

        def _load_spec(spec_id: str) -> tuple[Session, Optional[NewsEventSpec]]:
            session = self._session_factory()
            spec = session.query(NewsEventSpec).filter(
                NewsEventSpec.id == spec_id
            ).first()
            return session, spec

        session, spec = await asyncio.to_thread(_load_spec, reg.spec_id)
        try:
            if spec is None:
                logger.info(
                    "[polymarket_ws.eval] spec_id=%s vanished from DB "
                    "before fire — dropping",
                    reg.spec_id,
                )
                return None
            if spec.state != "active":
                logger.info(
                    "[polymarket_ws.eval] spec_id=%s state=%s — skip fire",
                    spec.id, spec.state,
                )
                return None

            decision = FiringDecision(
                spec_id=spec.id,
                status="fire",
                reason=(
                    f"polymarket_ws cross: mid {mid_price:.4f} "
                    f"{reg.direction} threshold {reg.threshold:.4f}"
                ),
                supporting_classification_ids=[],
                aggregated_confidence=1.0,
            )
            snapshot = {
                "source": "polymarket_ws",
                "asset_id": reg.asset_id,
                "mid_price": float(mid_price),
                "threshold": float(reg.threshold),
                "direction": reg.direction,
                "tick_ts": float(ts),
            }
            outcome = await fire_spec(
                session,
                spec=spec,
                decision=decision,
                prediction_market_snapshot=snapshot,
            )
            logger.info(
                "[polymarket_ws.eval] fire_outcome spec=%s "
                "fired_event_id=%s duplicate=%s workflow_run_id=%s",
                spec.id, outcome.fired_event_id,
                outcome.duplicate, outcome.workflow_run_id,
            )
            return outcome
        finally:
            session.close()
