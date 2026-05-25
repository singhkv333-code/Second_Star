"""Polymarket CLOB market-data WS client — push-driven price feed.

This is the transport layer for the WS-driven prediction-market
trigger path. It owns one persistent connection to
``wss://ws-subscriptions-clob.polymarket.com/ws/market``, maintains
top-of-book state per subscribed token, and emits two callback
streams to the evaluator:

  - ``on_tick(asset_id, mid_price, ts)`` — every time the midpoint
    moves on a subscribed asset
  - ``on_resolved(asset_id, payload)`` — when Polymarket pushes a
    ``market_resolved`` event for a market we're watching (the
    evaluator joins by ``market`` -> set of subscribed asset_ids)

No business logic lives here. The evaluator owns "should this tick
fire a trigger?". The supervisor owns "which tokens should we be
subscribed to right now?". This module only owns the wire.

Phase-0 wire format (verified live 2026-05-25 via
scripts/polymarket_ws_smoketest.py):

  Subscribe (single frame on connect):
    {"type": "Market", "assets_ids": [...], "custom_feature_enabled": true}

  Inbound frames (one or many events per frame; if many they arrive
  as a JSON array):
    - book: {event_type:"book", asset_id, market, bids:[{price,size}],
             asks:[{price,size}], timestamp, hash}
    - price_change: {event_type:"price_change", market,
                     price_changes:[{asset_id, price, size,
                                     side:"BUY"|"SELL"}]}
    - best_bid_ask (rare, opt-in via custom_feature_enabled):
                   {event_type:"best_bid_ask", asset_id,
                    best_bid, best_ask}
    - market_resolved (rare, opt-in): {event_type:"market_resolved",
                                       market, ...}
    - new_market (opt-in firehose): IGNORED — we don't subscribe to
      market discovery on this channel.

The class is fully async. The supervisor owns the asyncio task; this
class only exposes ``start()``, ``stop()``, ``set_subscriptions()``
to drive it.
"""
from __future__ import annotations

import asyncio
import json
import logging
import ssl
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

import certifi
import websockets

logger = logging.getLogger(__name__)


WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"

# Reconnect backoff bounds. Production-shaped: tight enough to recover
# from a transient drop in seconds, loose enough not to hammer the
# upstream during an extended outage.
_BACKOFF_INITIAL_S = 1.0
_BACKOFF_MAX_S = 30.0
_BACKOFF_FACTOR = 2.0

# How long to wait for a single recv() before re-checking shutdown
# state. Loop-control only — doesn't bound message latency.
_RECV_TIMEOUT_S = 30.0


TickCallback = Callable[[str, float, float], Awaitable[None]]
ResolvedCallback = Callable[[str, dict], Awaitable[None]]


@dataclass
class _BookState:
    """Per-asset top-of-book cache.

    ``bids`` and ``asks`` are dicts keyed by float price → size. On
    each ``price_change`` delta we set or delete the level. Midpoint
    is recomputed lazily via ``mid()``.
    """

    bids: dict[float, float] = field(default_factory=dict)
    asks: dict[float, float] = field(default_factory=dict)
    last_mid: Optional[float] = None
    last_update_ts: float = 0.0

    def best_bid(self) -> Optional[float]:
        return max(self.bids.keys()) if self.bids else None

    def best_ask(self) -> Optional[float]:
        return min(self.asks.keys()) if self.asks else None

    def mid(self) -> Optional[float]:
        bb, ba = self.best_bid(), self.best_ask()
        if bb is None or ba is None:
            return None
        return (bb + ba) / 2.0


def _ssl_context() -> ssl.SSLContext:
    """certifi bundle — macOS Python ships without the system CA store,
    so the default context fails wss:// handshakes. Pin certifi so the
    backend works on both dev (macOS) and production (linux) without
    a hosts-config branch."""
    return ssl.create_default_context(cafile=certifi.where())


def _apply_book(state: _BookState, payload: dict) -> None:
    """Replace top-of-book entirely from a fresh ``book`` snapshot."""
    state.bids.clear()
    state.asks.clear()
    for level in payload.get("bids") or []:
        try:
            state.bids[float(level["price"])] = float(level["size"])
        except (KeyError, TypeError, ValueError):
            continue
    for level in payload.get("asks") or []:
        try:
            state.asks[float(level["price"])] = float(level["size"])
        except (KeyError, TypeError, ValueError):
            continue
    state.last_update_ts = time.time()


def _apply_price_change(state: _BookState, change: dict) -> None:
    """Apply one (price, size, side) delta. size==0 removes the level."""
    try:
        price = float(change["price"])
        size = float(change["size"])
    except (KeyError, TypeError, ValueError):
        return
    side = str(change.get("side", "")).upper()
    book = state.bids if side == "BUY" else state.asks if side == "SELL" else None
    if book is None:
        return
    if size <= 0:
        book.pop(price, None)
    else:
        book[price] = size
    state.last_update_ts = time.time()


def _apply_best_bid_ask(state: _BookState, payload: dict) -> None:
    """Convenience event when the upstream gates it — collapses to a
    single bid + ask. We overwrite the book with just these two
    levels so the midpoint computation stays uniform with the
    price_change path. Falls through silently if shape is off."""
    try:
        best_bid = float(payload["best_bid"])
        best_ask = float(payload["best_ask"])
    except (KeyError, TypeError, ValueError):
        return
    state.bids = {best_bid: 1.0}
    state.asks = {best_ask: 1.0}
    state.last_update_ts = time.time()


class PolymarketWSClient:
    """Long-lived async client. One instance owns one WS connection.

    Lifecycle:
        client = PolymarketWSClient(on_tick=..., on_resolved=...)
        await client.set_subscriptions({"tok_a", "tok_b"})
        await client.start()         # spawns the run loop task
        ...
        await client.set_subscriptions({"tok_a", "tok_c"})  # reconnect
        await client.stop()

    The run loop reconnects on any failure with exponential backoff.
    Reconfiguring the subscription set forces a clean reconnect (the
    Polymarket protocol only accepts the asset list at handshake
    time on the market channel).
    """

    def __init__(
        self,
        *,
        on_tick: TickCallback,
        on_resolved: Optional[ResolvedCallback] = None,
        ws_url: str = WS_URL,
    ) -> None:
        self._on_tick = on_tick
        self._on_resolved = on_resolved
        self._ws_url = ws_url
        self._subscriptions: set[str] = set()
        self._books: dict[str, _BookState] = {}
        # market_id -> set of asset_ids we care about under it. Used
        # to route market_resolved events (which are keyed by market,
        # not asset) back to subscriber callbacks.
        self._market_to_assets: dict[str, set[str]] = {}
        self._run_task: Optional[asyncio.Task] = None
        self._stop_requested = False
        # Bumped every time the supervisor reconfigures subscriptions.
        # The run loop reads this; on bump it tears down the WS so the
        # outer reconnect loop picks up the new set on the next pass.
        self._subscription_epoch = 0
        self._current_epoch_seen = 0
        self._connected_event = asyncio.Event()

    # ── public API ────────────────────────────────────────────────────

    async def set_subscriptions(self, asset_ids: set[str]) -> None:
        """Replace the subscribed asset set.

        If different from the current set, requests a reconnect so the
        server-side handshake picks up the new list. Safe to call
        before ``start()`` (the first connect uses the configured set).
        """
        new = {a for a in asset_ids if a}
        if new == self._subscriptions:
            return
        added = new - self._subscriptions
        removed = self._subscriptions - new
        self._subscriptions = new
        # Drop book state for removed assets so we don't leak memory
        # across long-lived runs.
        for a in removed:
            self._books.pop(a, None)
        self._subscription_epoch += 1
        logger.info(
            "[polymarket_ws] subscriptions changed epoch=%d +%d/-%d "
            "now=%d",
            self._subscription_epoch,
            len(added),
            len(removed),
            len(new),
        )
        if self._run_task is not None and not self._run_task.done():
            # Force the current connection closed so the run loop
            # reconnects with the new subscription set.
            await self._force_reconnect()

    async def start(self) -> None:
        """Spawn the run loop. Idempotent — re-call is a no-op."""
        if self._run_task is not None and not self._run_task.done():
            return
        self._stop_requested = False
        self._run_task = asyncio.create_task(
            self._run_forever(), name="polymarket_ws.run"
        )
        logger.info("[polymarket_ws] run task scheduled")

    async def stop(self) -> None:
        """Graceful shutdown. Cancels the run task and waits for it."""
        self._stop_requested = True
        if self._run_task is None:
            return
        self._run_task.cancel()
        try:
            await self._run_task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
        self._run_task = None
        logger.info("[polymarket_ws] stopped")

    def current_mid(self, asset_id: str) -> Optional[float]:
        """Latest computed midpoint for an asset. None if unknown
        (never subscribed, or book hasn't arrived yet)."""
        state = self._books.get(asset_id)
        return state.mid() if state else None

    # ── internals ─────────────────────────────────────────────────────

    async def _force_reconnect(self) -> None:
        """Cancel the current connection so the outer loop reconnects.
        Implemented by setting the stop event on the in-flight task
        and recreating it. Cheaper than canceling+rescheduling the
        outer task because we keep the run loop's reconnect counter
        and backoff across config changes."""
        # The run loop polls `_subscription_epoch` between recvs and
        # breaks out of its inner ws context when it changes. No
        # explicit cancel needed.
        pass

    async def _run_forever(self) -> None:
        """Outer reconnect loop with exponential backoff."""
        backoff = _BACKOFF_INITIAL_S
        while not self._stop_requested:
            if not self._subscriptions:
                # Nothing to subscribe to — sleep briefly and re-check.
                # The supervisor will call set_subscriptions() when
                # active specs exist.
                self._connected_event.clear()
                await asyncio.sleep(2.0)
                continue
            try:
                await self._run_one_connection()
                # Clean exit means epoch changed; reconnect immediately.
                backoff = _BACKOFF_INITIAL_S
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "[polymarket_ws] connection error: %s — reconnect "
                    "in %.1fs",
                    exc, backoff,
                )
                self._connected_event.clear()
                try:
                    await asyncio.sleep(backoff)
                except asyncio.CancelledError:
                    raise
                backoff = min(_BACKOFF_MAX_S, backoff * _BACKOFF_FACTOR)

    async def _run_one_connection(self) -> None:
        """One connect → subscribe → receive cycle.

        Returns normally when the subscription epoch changes (caller
        will reconnect). Raises on socket / protocol errors so the
        outer backoff loop sees them.
        """
        sub_payload = {
            "type": "Market",
            "assets_ids": sorted(self._subscriptions),
            "custom_feature_enabled": True,
        }
        epoch = self._subscription_epoch
        self._current_epoch_seen = epoch
        async with websockets.connect(
            self._ws_url,
            ssl=_ssl_context(),
            max_size=2**22,
            ping_interval=20,
            ping_timeout=20,
        ) as ws:
            await ws.send(json.dumps(sub_payload))
            self._connected_event.set()
            logger.info(
                "[polymarket_ws] connected, subscribed to %d assets "
                "(epoch=%d)",
                len(self._subscriptions),
                epoch,
            )
            while not self._stop_requested:
                if self._subscription_epoch != epoch:
                    logger.info(
                        "[polymarket_ws] epoch changed %d→%d — closing "
                        "for resubscribe",
                        epoch, self._subscription_epoch,
                    )
                    return
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=_RECV_TIMEOUT_S)
                except asyncio.TimeoutError:
                    # Loop back to check stop / epoch. ping_interval
                    # keeps the connection alive in the background.
                    continue
                await self._handle_frame(raw)

    async def _handle_frame(self, raw: str | bytes) -> None:
        try:
            payload = json.loads(raw)
        except (ValueError, TypeError):
            logger.warning("[polymarket_ws] non-JSON frame dropped")
            return
        events = payload if isinstance(payload, list) else [payload]
        for ev in events:
            if not isinstance(ev, dict):
                continue
            kind = str(ev.get("event_type") or ev.get("type") or "")
            try:
                await self._dispatch(kind, ev)
            except Exception:  # noqa: BLE001 — never let a callback bug kill the loop
                logger.exception(
                    "[polymarket_ws] dispatch error for kind=%s", kind
                )

    async def _dispatch(self, kind: str, ev: dict) -> None:
        if kind == "book":
            asset_id = str(ev.get("asset_id") or "")
            if asset_id not in self._subscriptions:
                return
            state = self._books.setdefault(asset_id, _BookState())
            _apply_book(state, ev)
            self._track_market(asset_id, str(ev.get("market") or ""))
            await self._maybe_emit_tick(asset_id, state)
            return

        if kind == "price_change":
            for change in ev.get("price_changes") or []:
                if not isinstance(change, dict):
                    continue
                asset_id = str(change.get("asset_id") or "")
                if asset_id not in self._subscriptions:
                    continue
                state = self._books.setdefault(asset_id, _BookState())
                _apply_price_change(state, change)
                self._track_market(asset_id, str(ev.get("market") or ""))
                await self._maybe_emit_tick(asset_id, state)
            return

        if kind == "best_bid_ask":
            asset_id = str(ev.get("asset_id") or "")
            if asset_id not in self._subscriptions:
                return
            state = self._books.setdefault(asset_id, _BookState())
            _apply_best_bid_ask(state, ev)
            await self._maybe_emit_tick(asset_id, state)
            return

        if kind == "market_resolved":
            if self._on_resolved is None:
                return
            market_id = str(ev.get("market") or "")
            affected = self._market_to_assets.get(market_id) or set()
            for asset_id in affected:
                try:
                    await self._on_resolved(asset_id, ev)
                except Exception:  # noqa: BLE001
                    logger.exception(
                        "[polymarket_ws] on_resolved cb failed asset=%s",
                        asset_id,
                    )
            return

        if kind in {"new_market", "tick_size_change"}:
            # Firehose / rare housekeeping — silently ignore.
            return

        logger.debug("[polymarket_ws] unhandled event_type=%r", kind)

    def _track_market(self, asset_id: str, market_id: str) -> None:
        """Record the market_id → {asset_ids} mapping so we can route
        market_resolved events back to the right subscribers."""
        if not market_id or not asset_id:
            return
        self._market_to_assets.setdefault(market_id, set()).add(asset_id)

    async def _maybe_emit_tick(self, asset_id: str, state: _BookState) -> None:
        """Emit a tick callback if the midpoint changed.

        Equality on float midpoints is fine here — the only way the
        midpoint matches the previous value is if neither bid nor ask
        top-of-book moved, which is exactly when we want to suppress
        the tick.
        """
        mid = state.mid()
        if mid is None:
            return
        if state.last_mid is not None and abs(mid - state.last_mid) < 1e-9:
            return
        state.last_mid = mid
        try:
            await self._on_tick(asset_id, mid, state.last_update_ts)
        except Exception:  # noqa: BLE001
            logger.exception(
                "[polymarket_ws] on_tick cb failed asset=%s mid=%s",
                asset_id, mid,
            )
