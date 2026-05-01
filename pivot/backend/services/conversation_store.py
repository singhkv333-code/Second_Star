"""Redis-backed conversation history with a 24h TTL.

Why Redis: the chat path is hot, history is read on every turn, and the data
is ephemeral. Persistent transcripts are a separate concern (audit log) — out
of scope for this module.

What's stored: a list of `{role, content}` dicts only — never tool-call
payloads, never assistant tool plans. Storing those caused the
`<TOOL_CALL>` text to leak into later turns.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from backend.cache import redis_client


logger = logging.getLogger(__name__)


CONV_TTL_SECONDS = 60 * 60 * 24             # 24h
CONV_MAX_TURNS = 20                          # last N turns kept
CONV_PREFIX = "chat:conv:"


@dataclass
class ConversationStore:
    """Thin wrapper around Redis. Sync because cache.redis_client is sync."""

    def _key(self, conv_id: str) -> str:
        return f"{CONV_PREFIX}{conv_id}"

    def get_history(self, conv_id: str, limit: int = 10) -> list[dict]:
        """Return the last `limit` turns for prompt assembly. Oldest first."""
        if not conv_id:
            return []
        try:
            raw = redis_client.lrange(self._key(conv_id), -limit * 2, -1)
        except Exception as e:
            logger.warning("conv history fetch failed: %s", e)
            return []
        out: list[dict] = []
        for item in raw:
            try:
                msg = json.loads(item)
            except (TypeError, json.JSONDecodeError):
                continue
            if isinstance(msg, dict) and msg.get("role") in {"user", "assistant"}:
                out.append({"role": msg["role"], "content": msg.get("content", "")})
        return out

    def append(self, conv_id: str, user_msg: str, assistant_msg: str) -> None:
        """Append the just-completed turn. We only persist plain text — no
        tool-call payloads — so nothing leaks back into a future turn."""
        if not conv_id:
            return
        try:
            key = self._key(conv_id)
            redis_client.rpush(
                key,
                json.dumps({"role": "user", "content": user_msg}),
                json.dumps({"role": "assistant", "content": assistant_msg}),
            )
            redis_client.ltrim(key, -CONV_MAX_TURNS * 2, -1)
            redis_client.expire(key, CONV_TTL_SECONDS)
        except Exception as e:
            logger.warning("conv history append failed: %s", e)

    def clear(self, conv_id: str) -> None:
        try:
            redis_client.delete(self._key(conv_id))
        except Exception as e:
            logger.warning("conv history clear failed: %s", e)


_default_store: ConversationStore | None = None


def default_store() -> ConversationStore:
    global _default_store
    if _default_store is None:
        _default_store = ConversationStore()
    return _default_store
