"""Process-wide Kite ticker manager.

Owns the Redis tick cache (`price:{SYMBOL}` keys with 90s TTL) and the
Redis pub/sub `ticks` channel that the `/api/ws/quotes` WS endpoint
fans out to browser clients.

Design notes:
    - Single instance per process; consumers go through
      :func:`get_ticker_manager`. The instance owns its own background
      thread; FastAPI startup calls ``start()`` and shutdown calls
      ``stop()``.
    - Mock mode (``backend.kite.auth.KITE_MOCK_MODE``) is honoured by
      making ``start()`` a no-op — the cache stays empty and other
      readers fall back to yfinance. The module imports cleanly without
      the ``kiteconnect`` package installed because we lazy-import it
      from inside ``_build_ticker``.
    - We follow the ``_MIRRORED_MODULES`` pattern from
      ``backend.kite.auth``: the auth module patches our module-level
      flag if credentials are flipped at runtime, so a re-start of the
      ticker picks up the new state.
    - Instrument-token lookup: the NSE master CSV is ~4MB; we fetch it
      once on start, build a tradingsymbol→instrument_token dict in
      memory, and never refetch unless the ticker fully restarts.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from backend.cache import get_redis
from backend.kite.auth import KITE_MOCK_MODE

logger = logging.getLogger(__name__)


# Tick cache shape — see PHASE2_CONTRACT.md §Layer 1.
_CACHE_KEY_PREFIX = "price:"
_CACHE_TTL_SECONDS = 90
_PUBSUB_CHANNEL = "ticks"

# Cap on how many symbols we will keep subscribed at once. Kite allows
# 3000 across one connection; we cap well below that.
_MAX_UNIVERSE = 500

# Default index symbols we always seed when the ticker boots — the
# context_injector and dashboard both read these from Redis.
_DEFAULT_INDEX_SYMBOLS: tuple[str, ...] = ("NIFTY 50", "NIFTY BANK", "SENSEX")

# Friendly tradingsymbol → upstream NSE/BSE name when the instruments
# CSV doesn't store the value verbatim. We don't currently rely on this,
# but kite returns different casings for indices.
_INDEX_TRADINGSYMBOL_ALIASES: dict[str, tuple[str, ...]] = {
    "NIFTY 50": ("NIFTY 50", "NIFTY50", "NIFTY"),
    "NIFTY BANK": ("NIFTY BANK", "BANKNIFTY", "NIFTYBANK"),
    "SENSEX": ("SENSEX", "BSE-30"),
}


def normalize_symbol(symbol: str) -> str:
    """Upper-case + collapse whitespace → underscores.

    Matches the Redis key convention used by every reader
    (`context_injector._cached_price`, scheduler `_get_price_for_symbol`,
    `routers/markets.get_quote`).
    """
    return (symbol or "").strip().upper().replace(" ", "_")


def cache_key(symbol: str) -> str:
    return f"{_CACHE_KEY_PREFIX}{normalize_symbol(symbol)}"


# Subset of public symbols we'd consider safe to release when the last
# WS client unsubscribes. Anything in the manager's seed universe
# (holdings + indices) is sticky for the life of the connection.


@dataclass
class _TickerState:
    running: bool = False
    user_id: Optional[int] = None
    seed_symbols: set[str] = field(default_factory=set)
    universe: set[str] = field(default_factory=set)
    subscriber_count: dict[str, int] = field(default_factory=dict)
    instrument_tokens: dict[str, int] = field(default_factory=dict)
    last_tick_ts: Optional[int] = None
    reconnects: int = 0


class KiteTickerManager:
    """Owns a single ``kiteconnect.KiteTicker`` background thread.

    Thread-safety: every public mutator grabs ``self._lock``. The
    Kite-side callbacks run on the websocket thread and also lock when
    they update state.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._state = _TickerState()
        self._ticker: Any = None  # kiteconnect.KiteTicker instance
        self._access_token: Optional[str] = None

    # ────────────────────────────────────────────────────────────────
    # Public API
    # ────────────────────────────────────────────────────────────────

    def start(
        self,
        access_token: str,
        user_id: Optional[int],
        seed_symbols: Optional[Iterable[str]] = None,
    ) -> dict[str, Any]:
        """Spin up the KiteTicker. Idempotent.

        ``seed_symbols`` is the initial subscription set (typically the
        user's holding symbols). The default index basket is always
        unioned in. In mock mode this is a no-op.
        """
        with self._lock:
            if self._state.running:
                logger.info("KiteTicker already running for user %s", self._state.user_id)
                return self.status()

            if KITE_MOCK_MODE or not access_token:
                logger.info(
                    "KiteTicker.start: skipped (mock_mode=%s, has_token=%s)",
                    KITE_MOCK_MODE,
                    bool(access_token),
                )
                return self.status()

            seeds = {normalize_symbol(s) for s in (seed_symbols or []) if s}
            seeds |= {normalize_symbol(s) for s in _DEFAULT_INDEX_SYMBOLS}
            self._state.seed_symbols = seeds
            self._state.universe = set(seeds)
            self._state.subscriber_count = {s: 1 for s in seeds}
            self._state.user_id = user_id
            self._access_token = access_token

            try:
                self._instrument_tokens_bootstrap()
                self._ticker = self._build_ticker(access_token)
                self._wire_callbacks(self._ticker)
                # KiteTicker.connect(threaded=True) spins up its own
                # background thread; the call returns immediately.
                self._ticker.connect(threaded=True, disable_ssl_verification=False)
                self._state.running = True
                logger.info(
                    "KiteTicker started (user_id=%s, seeds=%d)",
                    user_id,
                    len(seeds),
                )
            except Exception as exc:  # pragma: no cover — exercised in real Kite path
                logger.exception("KiteTicker failed to start: %s", exc)
                self._state.running = False
                self._ticker = None
                self._access_token = None
                # Leave the state mostly intact for status() to surface
                # the error; raise so the caller can decide how to react.
                raise

            return self.status()

    def stop(self) -> dict[str, Any]:
        """Tear down the ticker and clear in-memory state. Idempotent."""
        with self._lock:
            if not self._state.running and self._ticker is None:
                return self.status()
            try:
                if self._ticker is not None:
                    self._ticker.close(code=1000, reason="shutdown")
            except Exception:  # pragma: no cover — best effort
                logger.warning("KiteTicker close raised; ignoring", exc_info=True)
            self._ticker = None
            self._access_token = None
            self._state = _TickerState()
            logger.info("KiteTicker stopped")
            return self.status()

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "running": self._state.running,
                "symbol_count": len(self._state.universe),
                "last_tick_ts": self._state.last_tick_ts,
                "reconnects": self._state.reconnects,
                "user_id": self._state.user_id,
            }

    def add_symbols(self, symbols: Iterable[str]) -> list[str]:
        """Hot-subscribe to additional tradingsymbols.

        Returns the list of symbols actually added (deduped, normalised,
        and filtered to those we know the instrument token for).
        """
        with self._lock:
            added: list[str] = []
            tokens_to_subscribe: list[int] = []
            for sym in symbols:
                norm = normalize_symbol(sym)
                if not norm:
                    continue
                self._state.subscriber_count[norm] = (
                    self._state.subscriber_count.get(norm, 0) + 1
                )
                if norm in self._state.universe:
                    continue
                if len(self._state.universe) >= _MAX_UNIVERSE:
                    logger.warning(
                        "KiteTicker universe at cap (%d); refusing %s",
                        _MAX_UNIVERSE,
                        norm,
                    )
                    continue
                token = self._token_for(norm)
                if token is None:
                    # We can still accept it into the universe so REST
                    # callers see at least an empty slot; but we can't
                    # subscribe upstream.
                    logger.debug("No instrument token for %s; cannot subscribe", norm)
                    continue
                self._state.universe.add(norm)
                self._state.instrument_tokens.setdefault(norm, token)
                tokens_to_subscribe.append(token)
                added.append(norm)

            if tokens_to_subscribe and self._ticker is not None and self._state.running:
                try:
                    self._ticker.subscribe(tokens_to_subscribe)
                    self._ticker.set_mode(self._ticker.MODE_QUOTE, tokens_to_subscribe)
                except Exception:  # pragma: no cover
                    logger.exception("KiteTicker subscribe raised")
            return added

    def remove_symbols(self, symbols: Iterable[str]) -> list[str]:
        """Decrement subscriber counts; drop symbols that hit zero AND
        weren't part of the original seed universe (holdings + indices).
        """
        with self._lock:
            removed: list[str] = []
            tokens_to_unsubscribe: list[int] = []
            for sym in symbols:
                norm = normalize_symbol(sym)
                if not norm:
                    continue
                current = self._state.subscriber_count.get(norm, 0)
                if current <= 0:
                    continue
                self._state.subscriber_count[norm] = current - 1
                if self._state.subscriber_count[norm] > 0:
                    continue
                # Refcount hit zero. Only release non-seed symbols.
                if norm in self._state.seed_symbols:
                    continue
                if norm not in self._state.universe:
                    continue
                token = self._state.instrument_tokens.get(norm)
                self._state.universe.discard(norm)
                self._state.subscriber_count.pop(norm, None)
                if token is not None:
                    tokens_to_unsubscribe.append(token)
                removed.append(norm)

            if tokens_to_unsubscribe and self._ticker is not None and self._state.running:
                try:
                    self._ticker.unsubscribe(tokens_to_unsubscribe)
                except Exception:  # pragma: no cover
                    logger.exception("KiteTicker unsubscribe raised")
            return removed

    # ────────────────────────────────────────────────────────────────
    # Internal helpers — building/wiring the ticker
    # ────────────────────────────────────────────────────────────────

    def _build_ticker(self, access_token: str) -> Any:
        """Lazy-import kiteconnect so the module loads in mock mode."""
        from kiteconnect import KiteTicker  # type: ignore

        from backend.config import settings

        return KiteTicker(
            api_key=settings.kite_api_key,
            access_token=access_token,
            reconnect=True,
            reconnect_max_tries=50,
            reconnect_max_delay=60,
        )

    def _wire_callbacks(self, ticker: Any) -> None:
        ticker.on_ticks = self._on_ticks
        ticker.on_connect = self._on_connect
        ticker.on_reconnect = self._on_reconnect
        ticker.on_close = self._on_close
        ticker.on_error = self._on_error
        ticker.on_noreconnect = self._on_noreconnect

    def _instrument_tokens_bootstrap(self) -> None:
        """Populate the tradingsymbol → instrument_token map.

        Called once at start. The CSV is ~4MB; we keep the dict in
        memory for the life of the ticker.
        """
        from backend.kite.auth import get_authenticated_kite

        if not self._access_token:
            return
        try:
            kite = get_authenticated_kite(self._access_token)
            if kite is None:
                return
            # Equities universe.
            for instrument in kite.instruments("NSE"):
                ts = instrument.get("tradingsymbol")
                tok = instrument.get("instrument_token")
                if not ts or tok is None:
                    continue
                self._state.instrument_tokens[normalize_symbol(ts)] = int(tok)
            # Indices — Kite serves them on the "NSE" segment too,
            # under the "INDICES" instrument_type. Some accounts also
            # see them on "NFO". We've already loaded "NSE"; mop up
            # the alias names so contract-keyed lookups (NIFTY_50, etc.)
            # resolve.
            for canonical, aliases in _INDEX_TRADINGSYMBOL_ALIASES.items():
                norm_canonical = normalize_symbol(canonical)
                if norm_canonical in self._state.instrument_tokens:
                    continue
                for alias in aliases:
                    tok = self._state.instrument_tokens.get(normalize_symbol(alias))
                    if tok is not None:
                        self._state.instrument_tokens[norm_canonical] = tok
                        break
        except Exception as exc:  # pragma: no cover
            logger.warning(
                "Instrument master fetch failed (%s); ticker will start "
                "without a symbol→token map. add_symbols will be a no-op.",
                exc,
            )

    def _token_for(self, normalised_symbol: str) -> Optional[int]:
        return self._state.instrument_tokens.get(normalised_symbol)

    def _symbol_for(self, instrument_token: int) -> Optional[str]:
        # Linear scan but the map is small (a few hundred entries) and
        # we only do this on ticks for known symbols.
        for sym, tok in self._state.instrument_tokens.items():
            if tok == instrument_token:
                return sym
        return None

    # ────────────────────────────────────────────────────────────────
    # KiteTicker callbacks
    # ────────────────────────────────────────────────────────────────

    def _on_ticks(self, _ws: Any, ticks: list[dict[str, Any]]) -> None:
        """Persist each tick to Redis + publish on the `ticks` channel."""
        if not ticks:
            return
        try:
            rc = get_redis()
        except Exception:  # pragma: no cover
            logger.exception("Redis unavailable in on_ticks; dropping batch")
            return

        ts_now = int(time.time())
        with self._lock:
            self._state.last_tick_ts = ts_now

        for tick in ticks:
            payload = self._tick_to_payload(tick, ts_now)
            if payload is None:
                continue
            sym = payload["symbol"]
            serialised = json.dumps(payload)
            try:
                rc.set(cache_key(sym), serialised, ex=_CACHE_TTL_SECONDS)
            except Exception:  # pragma: no cover
                logger.exception("Redis SET failed for %s", sym)
            try:
                # MockRedis doesn't implement `publish`; ignore there.
                if hasattr(rc, "publish"):
                    rc.publish(_PUBSUB_CHANNEL, serialised)
            except Exception:  # pragma: no cover
                logger.exception("Redis PUBLISH failed for %s", sym)

    def _tick_to_payload(self, tick: dict[str, Any], ts_now: int) -> Optional[dict[str, Any]]:
        token = tick.get("instrument_token")
        if token is None:
            return None
        try:
            token_int = int(token)
        except (TypeError, ValueError):
            return None
        sym = self._symbol_for(token_int)
        if sym is None:
            return None
        ltp = tick.get("last_price")
        try:
            ltp_f = float(ltp) if ltp is not None else None
        except (TypeError, ValueError):
            ltp_f = None
        if ltp_f is None:
            return None
        ohlc = tick.get("ohlc") or {}
        prev_close = tick.get("close_price") or tick.get("prev_day_close") or ohlc.get("close")
        change_pct = 0.0
        try:
            if prev_close and float(prev_close) > 0:
                change_pct = round((ltp_f - float(prev_close)) / float(prev_close) * 100, 4)
        except (TypeError, ValueError):
            pass
        return {
            "symbol": sym,
            "ltp": round(ltp_f, 4),
            "change_pct": change_pct,
            "open": _safe_float(ohlc.get("open")),
            "high": _safe_float(ohlc.get("high")),
            "low": _safe_float(ohlc.get("low")),
            "prev_close": _safe_float(prev_close),
            "volume": _safe_float(tick.get("volume_traded") or tick.get("volume")),
            "ts": ts_now,
            "src": "kite_ws",
        }

    def _on_connect(self, ws: Any, _response: Any) -> None:
        """Subscribe the full universe + set quote/LTP modes."""
        with self._lock:
            stock_tokens: list[int] = []
            index_tokens: list[int] = []
            index_symbols = {normalize_symbol(s) for s in _DEFAULT_INDEX_SYMBOLS}
            for sym in self._state.universe:
                token = self._state.instrument_tokens.get(sym)
                if token is None:
                    continue
                if sym in index_symbols:
                    index_tokens.append(token)
                else:
                    stock_tokens.append(token)
        try:
            all_tokens = stock_tokens + index_tokens
            if all_tokens:
                ws.subscribe(all_tokens)
            if stock_tokens:
                ws.set_mode(ws.MODE_QUOTE, stock_tokens)
            if index_tokens:
                ws.set_mode(ws.MODE_LTP, index_tokens)
            logger.info(
                "KiteTicker connected; subscribed %d stocks + %d indices",
                len(stock_tokens),
                len(index_tokens),
            )
        except Exception:  # pragma: no cover
            logger.exception("KiteTicker on_connect subscribe failed")

    def _on_reconnect(self, _ws: Any, attempts_count: int) -> None:
        with self._lock:
            self._state.reconnects += 1
        logger.warning("KiteTicker reconnecting; attempt %s", attempts_count)

    def _on_close(self, _ws: Any, code: Any, reason: Any) -> None:
        logger.info("KiteTicker closed: code=%s reason=%s", code, reason)
        with self._lock:
            self._state.running = False

    def _on_error(self, _ws: Any, code: Any, reason: Any) -> None:
        logger.error("KiteTicker error: code=%s reason=%s", code, reason)

    def _on_noreconnect(self, _ws: Any) -> None:
        logger.error("KiteTicker gave up reconnecting")
        with self._lock:
            self._state.running = False


def _safe_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return round(float(value), 4)
    except (TypeError, ValueError):
        return None


# ────────────────────────────────────────────────────────────────────
# Module-level singleton
# ────────────────────────────────────────────────────────────────────


_MANAGER: Optional[KiteTickerManager] = None
_MANAGER_LOCK = threading.Lock()


def get_ticker_manager() -> KiteTickerManager:
    """Return the process-wide manager, creating it on first call."""
    global _MANAGER
    if _MANAGER is None:
        with _MANAGER_LOCK:
            if _MANAGER is None:
                _MANAGER = KiteTickerManager()
    return _MANAGER


def reset_ticker_manager_for_tests() -> None:
    """Test-only: wipe the singleton so individual tests start fresh."""
    global _MANAGER
    with _MANAGER_LOCK:
        if _MANAGER is not None:
            try:
                _MANAGER.stop()
            except Exception:
                pass
        _MANAGER = None
