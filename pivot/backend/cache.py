"""
Redis client with a MockRedis fallback.

If the configured Redis is reachable within 2s, use the real client.
Otherwise, fall back to an in-memory MockRedis so the app works with no
external dependencies (for dev, tests, and airplane mode).
"""
import logging

logger = logging.getLogger(__name__)


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

    def ping(self):
        return True

    def close(self):
        return None


def get_redis():
    """Returns real Redis if available, MockRedis otherwise."""
    try:
        import redis
        from backend.config import settings
        r = redis.from_url(settings.redis_url, socket_connect_timeout=2)
        r.ping()
        return r
    except Exception as e:
        logger.info(f"Redis unavailable ({e}); falling back to MockRedis")
        return MockRedis()


redis_client = get_redis()
