"""WS-driven prediction-market trigger evaluator.

Sits between ``sources/polymarket_ws.PolymarketWSClient`` and any
consumer that wants to fire on a Polymarket signal — historically the
news_events ``fire_spec`` path, and as of slice-4 also the workflow
engine's ``fire_external_event`` path for ``trigger.polymarket`` DSL
steps.

Two firing modes per registration:
  - ``mode='threshold'`` (default) — fire when YES midpoint crosses
    ``threshold`` in ``direction``. Edge-triggered: requires at least
    one baseline tick after registration to avoid spurious fires on
    a stale already-crossed market.
  - ``mode='resolution'`` — fire when the market officially RESOLVES.
    Polymarket pushes a ``market_resolved`` event; we read the winner
    from the payload and fire if it matches ``resolve_on`` (YES / NO
    / ANY). No baseline rule — resolution is unambiguous.

The evaluator is consumer-agnostic. Each registration carries an
opaque ``key`` (``spec:<uuid>`` for news, ``wf:<workflow_id>:<step>``
for workflow steps) and a ``fire_handler`` closure. On fire the
evaluator calls the closure with a payload dict describing why; the
closure routes the fire to whichever firing pipeline it belongs to.
The two existing closure shapes are built by ``register_spec()``
(news) and the supervisor's workflow-step scan (slice 4).

Idempotency:
  Two layers stack:
    1. In-memory: on a fire-worthy event, we unregister BEFORE
       invoking the closure so a flapping market can't re-enter.
    2. Closure side: NewsEventSpec firing uses ``fire_spec``'s
       UNIQUE(event_spec_id) constraint; workflow firing uses
       ``fire_external_event``'s ``client_request_id`` dedup.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Awaitable, Callable, Literal, Optional

from sqlalchemy.orm import Session

from backend.news_events.models import NewsEventSpec
from backend.news_events.pipeline.aggregate import FiringDecision
from backend.news_events.pipeline.propose import FireOutcome, fire_spec

logger = logging.getLogger(__name__)


ThresholdDirection = Literal["above", "below"]
TriggerMode = Literal["threshold", "resolution"]
ResolveOn = Literal["YES", "NO", "ANY"]


# Closure invoked on a fire. Receives a payload dict describing why
# the trigger fired. The closure decides what to do — the evaluator
# never imports the firing-pipeline modules itself.
FireHandler = Callable[[dict], Awaitable[None]]


@dataclass
class _Registration:
    """One active subscription.

    The supervisor pushes these in; the evaluator keys them by an
    opaque ``key`` (``spec:<uuid>`` or ``wf:<workflow_id>:<step>``).
    ``last_mid`` is the edge-trigger baseline — only relevant for
    ``mode='threshold'``.
    """

    key: str
    asset_id: str
    fire_handler: FireHandler
    mode: TriggerMode = "threshold"
    threshold: Optional[float] = None
    direction: ThresholdDirection = "above"
    resolve_on: ResolveOn = "YES"
    last_mid: Optional[float] = None
    registered_at_ts: float = 0.0


SessionFactory = Callable[[], Session]


class PolymarketWSEvaluator:
    """Hot-path WS event → fire dispatcher.

    Owns: in-memory registry of ``(asset_id → {key → registration})``
    and per-registration baseline state.

    Public API used by the supervisor:
      - ``register(key, asset_id, fire_handler, mode, threshold?,
        direction?, resolve_on?)`` — add or update a registration in
        place. Same-key re-register preserves ``last_mid`` baseline.
      - ``unregister(key)`` — drop one registration.
      - ``subscribed_asset_ids()`` — drives WS client subscriptions.
      - ``registered_keys()`` — drives the supervisor's diff loop.

    Public back-compat shim:
      - ``register_spec(spec_id, asset_id, threshold, direction)`` —
        builds the news-spec fire_handler closure and delegates to
        ``register()``. Kept so the existing news_events path keeps
        working without churn.

    Callbacks wired to ``PolymarketWSClient``:
      - ``on_tick`` — fires ``mode='threshold'`` registrations on cross.
      - ``on_resolved`` — fires ``mode='resolution'`` registrations
        when payload winner matches ``resolve_on``.
    """

    def __init__(self, *, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory
        # asset_id -> {key -> registration}.
        self._by_asset: dict[str, dict[str, _Registration]] = {}
        # key -> asset_id reverse index for unregister().
        self._key_to_asset: dict[str, str] = {}

    # ── public API used by the supervisor ─────────────────────────────

    def register(
        self,
        *,
        key: str,
        asset_id: str,
        fire_handler: FireHandler,
        mode: TriggerMode = "threshold",
        threshold: Optional[float] = None,
        direction: ThresholdDirection = "above",
        resolve_on: ResolveOn = "YES",
    ) -> None:
        """Add or update a registration. Same-key re-register updates
        in place AND preserves ``last_mid`` (critical: without this,
        supervisor reconcile churn would reset the edge-trigger
        baseline and risk spurious fires on the next tick)."""
        if mode == "threshold":
            if threshold is None:
                logger.warning(
                    "[polymarket_ws.eval] reject register key=%s mode=threshold "
                    "missing threshold",
                    key,
                )
                return
            if not (0.0 <= threshold <= 1.0):
                logger.warning(
                    "[polymarket_ws.eval] reject register key=%s threshold=%s "
                    "out of [0,1]",
                    key, threshold,
                )
                return
        existing = self._by_asset.get(asset_id, {}).get(key)
        if existing is not None:
            # In-place update: preserve last_mid baseline.
            existing.fire_handler = fire_handler
            existing.mode = mode
            existing.threshold = threshold
            existing.direction = direction
            existing.resolve_on = resolve_on
            return
        reg = _Registration(
            key=key,
            asset_id=asset_id,
            fire_handler=fire_handler,
            mode=mode,
            threshold=threshold,
            direction=direction,
            resolve_on=resolve_on,
            registered_at_ts=time.time(),
        )
        self._by_asset.setdefault(asset_id, {})[key] = reg
        self._key_to_asset[key] = asset_id
        thr_str = f"{threshold:.4f}" if threshold is not None else "N/A"
        logger.info(
            "[polymarket_ws.eval] registered key=%s asset=%s mode=%s "
            "threshold=%s direction=%s resolve_on=%s",
            key, asset_id, mode, thr_str, direction, resolve_on,
        )

    def unregister(self, key: str) -> None:
        """Drop one registration. Idempotent for unknown keys."""
        asset_id = self._key_to_asset.pop(key, None)
        if asset_id is None:
            return
        by_key = self._by_asset.get(asset_id)
        if by_key is not None:
            by_key.pop(key, None)
            if not by_key:
                self._by_asset.pop(asset_id, None)
        logger.info(
            "[polymarket_ws.eval] unregistered key=%s asset=%s",
            key, asset_id,
        )

    def subscribed_asset_ids(self) -> set[str]:
        return set(self._by_asset.keys())

    def registered_keys(self) -> set[str]:
        return set(self._key_to_asset.keys())

    # ── back-compat: news-event spec registration ─────────────────────

    def register_spec(
        self,
        *,
        spec_id: str,
        asset_id: str,
        threshold: Optional[float] = None,
        direction: ThresholdDirection = "above",
        mode: TriggerMode = "threshold",
        resolve_on: ResolveOn = "YES",
    ) -> None:
        """Build the news-spec fire_handler closure + delegate to
        ``register()`` under key ``spec:<spec_id>``. Preserves the
        slice-1/2 API shape for callers that haven't migrated."""
        key = f"spec:{spec_id}"
        handler = self._build_spec_fire_handler(spec_id=spec_id, asset_id=asset_id)
        self.register(
            key=key,
            asset_id=asset_id,
            fire_handler=handler,
            mode=mode,
            threshold=threshold,
            direction=direction,
            resolve_on=resolve_on,
        )

    def unregister_spec(self, spec_id: str) -> None:
        self.unregister(f"spec:{spec_id}")

    # ── hot path: WS client callbacks ─────────────────────────────────

    async def on_tick(self, asset_id: str, mid_price: float, ts: float) -> None:
        """Tick → threshold-mode firings. Resolution-mode regs are
        ignored on tick — they wait for ``on_resolved``. Baseline is
        recorded for every threshold registration that doesn't fire so
        the next tick can detect a cross.
        """
        regs = self._by_asset.get(asset_id)
        if not regs:
            return
        for reg in list(regs.values()):
            if reg.mode != "threshold":
                continue
            if not self._should_fire(reg, mid_price):
                reg.last_mid = mid_price
                continue
            logger.info(
                "[polymarket_ws.eval] fire-threshold key=%s asset=%s "
                "mid=%.4f threshold=%.4f direction=%s prev=%s",
                reg.key, asset_id, mid_price, reg.threshold,
                reg.direction, reg.last_mid,
            )
            self.unregister(reg.key)
            payload = {
                "mode": "threshold",
                "asset_id": asset_id,
                "mid_price": float(mid_price),
                "threshold": float(reg.threshold) if reg.threshold is not None else None,
                "direction": reg.direction,
                "tick_ts": float(ts),
            }
            await self._invoke_fire_handler(reg, payload)

    async def on_resolved(self, asset_id: str, payload: dict) -> None:
        """``market_resolved`` event → resolution-mode firings.

        Parses ``winner`` from the upstream payload (Polymarket sends
        ``winner: "YES" | "NO"`` in the market_resolved frame; if the
        field is missing or unrecognized, we degrade to firing only
        ``resolve_on='ANY'`` registrations).
        """
        regs = self._by_asset.get(asset_id)
        if not regs:
            logger.info(
                "[polymarket_ws.eval] market_resolved received asset=%s "
                "no_registrations market=%s",
                asset_id, payload.get("market"),
            )
            return
        winner_raw = str(payload.get("winner") or "").strip().upper()
        winner: Optional[Literal["YES", "NO"]] = (
            "YES" if winner_raw == "YES"
            else "NO" if winner_raw == "NO"
            else None
        )
        logger.info(
            "[polymarket_ws.eval] market_resolved asset=%s winner=%s "
            "registered=%d",
            asset_id, winner or "<unknown>", len(regs),
        )
        for reg in list(regs.values()):
            if reg.mode != "resolution":
                continue
            if reg.resolve_on != "ANY":
                if winner is None:
                    logger.info(
                        "[polymarket_ws.eval] skip resolution key=%s "
                        "unknown winner, resolve_on=%s",
                        reg.key, reg.resolve_on,
                    )
                    continue
                if reg.resolve_on != winner:
                    logger.info(
                        "[polymarket_ws.eval] skip resolution key=%s "
                        "winner=%s != resolve_on=%s",
                        reg.key, winner, reg.resolve_on,
                    )
                    continue
            logger.info(
                "[polymarket_ws.eval] fire-resolution key=%s asset=%s "
                "winner=%s resolve_on=%s",
                reg.key, asset_id, winner or "ANY", reg.resolve_on,
            )
            self.unregister(reg.key)
            fire_payload = {
                "mode": "resolution",
                "asset_id": asset_id,
                "winner": winner,
                "resolve_on": reg.resolve_on,
                "market": payload.get("market"),
                "raw_payload": payload,
            }
            await self._invoke_fire_handler(reg, fire_payload)

    # ── internals ─────────────────────────────────────────────────────

    @staticmethod
    def _should_fire(reg: _Registration, mid: float) -> bool:
        """Edge-trigger for threshold mode. Requires a baseline tick
        before firing (avoids spurious fires on stale already-crossed
        markets when we connect mid-way)."""
        if reg.last_mid is None or reg.threshold is None:
            return False
        if reg.direction == "above":
            return reg.last_mid < reg.threshold <= mid
        return reg.last_mid > reg.threshold >= mid

    async def _invoke_fire_handler(
        self, reg: _Registration, payload: dict,
    ) -> None:
        """Call the registration's fire_handler closure. Closures own
        their own error handling (DB rollbacks etc.) — we only log
        unhandled exceptions so the evaluator stays up."""
        try:
            await reg.fire_handler(payload)
        except Exception:  # noqa: BLE001
            logger.exception(
                "[polymarket_ws.eval] fire_handler raised key=%s asset=%s",
                reg.key, reg.asset_id,
            )

    def _build_spec_fire_handler(
        self, *, spec_id: str, asset_id: str,
    ) -> FireHandler:
        """Build the news-spec fire_handler closure — reads the spec
        from DB, constructs a FiringDecision, calls fire_spec. Same
        behavior as the slice-1/2 hard-coded path; just lifted into a
        closure so the evaluator's dispatch is consumer-agnostic.
        """
        session_factory = self._session_factory

        async def _handler(payload: dict) -> None:
            def _load() -> tuple[Session, Optional[NewsEventSpec]]:
                session = session_factory()
                spec = session.query(NewsEventSpec).filter(
                    NewsEventSpec.id == spec_id
                ).first()
                return session, spec

            session, spec = await asyncio.to_thread(_load)
            try:
                if spec is None:
                    logger.info(
                        "[polymarket_ws.eval] spec_id=%s vanished from DB "
                        "before fire — dropping",
                        spec_id,
                    )
                    return
                if spec.state != "active":
                    logger.info(
                        "[polymarket_ws.eval] spec_id=%s state=%s — skip "
                        "fire",
                        spec_id, spec.state,
                    )
                    return
                mode = str(payload.get("mode", "threshold"))
                if mode == "threshold":
                    mid = payload.get("mid_price")
                    thr = payload.get("threshold")
                    reason = (
                        f"polymarket_ws threshold cross: mid "
                        f"{mid:.4f} {payload.get('direction')} threshold "
                        f"{thr:.4f}"
                        if isinstance(mid, (int, float)) and isinstance(thr, (int, float))
                        else "polymarket_ws threshold cross"
                    )
                else:
                    reason = (
                        f"polymarket_ws market resolved: winner="
                        f"{payload.get('winner') or 'unknown'} matches "
                        f"resolve_on={payload.get('resolve_on')}"
                    )
                decision = FiringDecision(
                    spec_id=spec.id,
                    status="fire",
                    reason=reason,
                    supporting_classification_ids=[],
                    aggregated_confidence=1.0,
                )
                snapshot = {"source": "polymarket_ws", **payload}
                # Strip raw_payload from snapshot — can be large + isn't
                # useful for the audit pane.
                snapshot.pop("raw_payload", None)
                outcome: FireOutcome = await fire_spec(
                    session,
                    spec=spec,
                    decision=decision,
                    prediction_market_snapshot=snapshot,
                )
                logger.info(
                    "[polymarket_ws.eval] spec_fire_outcome spec=%s "
                    "fired_event_id=%s duplicate=%s workflow_run_id=%s",
                    spec.id, outcome.fired_event_id,
                    outcome.duplicate, outcome.workflow_run_id,
                )
            finally:
                session.close()

        return _handler
