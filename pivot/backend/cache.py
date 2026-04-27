"""
Redis client with a MockRedis fallback.

If the configured Redis is reachable within 2s, use the real client.
Otherwise, fall back to an in-memory MockRedis so the app works with no
external dependencies (for dev, tests, and airplane mode).
"""
import logging

logger = logging.getLogger(__name__)


class MockRedis:
    """In-memory Redis substitute when Redis is unavailable."""

    def __init__(self):
        self._store: dict = {}

    def get(self, key):
        value = self._store.get(key)
        if isinstance(value, str):
            return value.encode()
        return value

    def set(self, key, value, ex=None):
        self._store[key] = value
        return True

    def delete(self, key):
        return self._store.pop(key, None) is not None

    def exists(self, key):
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
