"""
Redis client with a MockRedis fallback.

If the configured Redis is reachable within 2s, use the real client.
Otherwise, fall back to an in-memory MockRedis so the app works with no
external dependencies (for dev, tests, and airplane mode).
"""
import logging
import threading
import time

logger = logging.getLogger(__name__)

# Minimum time (seconds) between reconnect attempts once we've fallen back to
# MockRedis. Prevents a permanent MockRedis stick (self-healing) while also
# not hammering a genuinely-down Redis on every call.
_RETRY_INTERVAL_SECONDS = 15.0


class MockRedis:
    """In-memory Redis substitute when Redis is unavailable.

    Emulates ``ex`` (TTL) and ``nx`` (set-if-absent) from redis-py —
    the option-chain cache/lock relies on both; without TTL emulation a
    5s chain cache would serve stale quotes for the process lifetime."""

    def __init__(self):
        self._store: dict = {}          # key -> value
        self._expires_at: dict = {}     # key -> monotonic deadline

    def _expired(self, key) -> bool:
        import time
        deadline = self._expires_at.get(key)
        if deadline is not None and time.monotonic() >= deadline:
            self._store.pop(key, None)
            self._expires_at.pop(key, None)
            return True
        return False

    def get(self, key):
        if self._expired(key):
            return None
        value = self._store.get(key)
        if isinstance(value, str):
            return value.encode()
        return value

    def set(self, key, value, ex=None, nx=False):
        import time
        self._expired(key)
        if nx and key in self._store:
            return None
        self._store[key] = value
        if ex is not None:
            self._expires_at[key] = time.monotonic() + float(ex)
        else:
            self._expires_at.pop(key, None)
        return True

    def delete(self, key):
        self._expires_at.pop(key, None)
        return self._store.pop(key, None) is not None

    def exists(self, key):
        if self._expired(key):
            return False
        return key in self._store

    def keys(self, pattern="*"):
        """Minimal glob support (only the `prefix*` shape callers use, e.g.
        cache-clearing in tests) — enough to mirror redis-py's `KEYS` for
        this codebase's actual usage, not a full glob implementation."""
        import fnmatch
        return [
            k for k in list(self._store.keys())
            if not self._expired(k) and fnmatch.fnmatch(k, pattern)
        ]

    def ping(self):
        return True

    def close(self):
        return None


def _connect_real_redis():
    """Attempts a real Redis connection + handshake. Returns the client on
    success, or None on any failure (never raises)."""
    try:
        import redis
        from backend.config import settings
        kwargs = {}
        if settings.redis_url.startswith("rediss://"):
            # python.org's macOS builds ship an empty default OpenSSL cert
            # store (no "Install Certificates.command" run), which fails TLS
            # verification against Azure Managed Redis with "self signed
            # certificate in certificate chain" even though the real chain is
            # valid. Point at certifi's bundle explicitly so this works
            # regardless of the host Python's trust-store state.
            import certifi
            kwargs["ssl_ca_certs"] = certifi.where()
        r = redis.from_url(settings.redis_url, socket_connect_timeout=2, **kwargs)
        r.ping()
        return r
    except Exception as e:
        logger.info(f"Redis unavailable ({e}); falling back to MockRedis")
        return None


# Module-level cached client + bookkeeping for get_redis(). `_client` holds
# either a real redis-py client (which manages its own internal connection
# pool across commands/pubsub — reused as-is) or a MockRedis instance.
# `_lock` guards first-construction and reconnect-attempt races between the
# FastAPI threadpool, the scheduler thread, and the WS event loop.
_client = None
_last_attempt_at: float | None = None
_lock = threading.Lock()


def get_redis():
    """Returns a cached/pooled Redis client, reconnecting only when needed.

    - First call: connects (real Redis if reachable, else MockRedis) and
      caches the result.
    - Later calls when already on a real client: return the same cached
      client immediately (no reconnect, no re-ping) so redis-py's internal
      connection pool is reused across calls and across `.pubsub()` users.
    - Later calls when currently on MockRedis: retry the real connection at
      most once per `_RETRY_INTERVAL_SECONDS`, so a transient outage at boot
      doesn't permanently stick the process to MockRedis. Between retries,
      the same MockRedis instance is returned (preserving its in-memory
      store) rather than reconnecting on every call.
    """
    global _client, _last_attempt_at

    # Fast path, no lock: real client already established.
    client = _client
    if client is not None and not isinstance(client, MockRedis):
        return client

    now = time.monotonic()
    if (
        client is not None
        and _last_attempt_at is not None
        and (now - _last_attempt_at) < _RETRY_INTERVAL_SECONDS
    ):
        return client

    with _lock:
        # Re-check inside the lock in case another thread already won the race.
        client = _client
        if client is not None and not isinstance(client, MockRedis):
            return client
        now = time.monotonic()
        if (
            client is not None
            and _last_attempt_at is not None
            and (now - _last_attempt_at) < _RETRY_INTERVAL_SECONDS
        ):
            return client

        real = _connect_real_redis()
        _last_attempt_at = time.monotonic()
        if real is not None:
            _client = real
        elif client is None:
            # First-ever attempt failed: seed a fresh MockRedis.
            _client = MockRedis()
        # else: keep the existing MockRedis instance (and its in-memory
        # store) — reconnect failed again, nothing to replace it with.
        return _client


class _RedisProxy:
    """Stable object bound to the module-level ``redis_client`` name.

    ``redis_client`` is imported via ``from backend.cache import redis_client``
    in ~23 files, which captures a direct reference at import time — binding
    it to a raw client (as before) meant a transient Redis outage during that
    first import permanently stuck every one of those call sites on MockRedis
    for the life of the process, even after get_redis()'s own self-healing
    retry (added above) would have reconnected. This proxy is the thing that
    gets captured instead; every attribute access delegates to get_redis()
    fresh, so those call sites inherit the same self-healing behavior for
    free with no changes on their end.
    """

    def __getattr__(self, name):
        return getattr(get_redis(), name)

    def __repr__(self):
        return repr(get_redis())


redis_client = _RedisProxy()
