"""Redis-backed conversation history with a 24h TTL.

Why Redis: the chat path is hot, history is read on every turn, and the data
is ephemeral. Persistent transcripts are a separate concern (audit log) — out
of scope for this module.

What's stored: a list of `{role, content}` dicts only — never tool-call
payloads, never assistant tool plans. Storing those caused the
`<TOOL_CALL>` text to leak into later turns.

Three kinds of state live here, all keyed by `conv_id`:
  - `chat:conv:{conv_id}`          → list of {role, content} (24h TTL)
  - `chat:pending:{conv_id}`       → PendingToolCall JSON (10min TTL)
  - `chat:active_draft:{conv_id}`  → ActiveDraft JSON (1h TTL)

Pending state powers the deterministic-resume path: when the model
emits a tool call missing one required field, we persist (tool_name,
partial args, missing_field) here. On the next turn chat_service
checks pending first; if the reply parses cleanly as the missing
value, we splice and execute — no LLM hop.

Active-draft state powers multi-turn AMENDMENT of a workflow: when
propose_workflow (or the macro / skeleton paths) emits a draft, we
stash the actual JSON. On the next turn the followup hint injects
this JSON directly into the prompt so the model amends THE SAME
shape rather than reconstructing from history text. Replaces the
old regex-scan-the-history band-aid (2026-05-04).
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from typing import Any, Optional

from backend.cache import redis_client


logger = logging.getLogger(__name__)


CONV_TTL_SECONDS = 60 * 60 * 24             # 24h
CONV_MAX_TURNS = 20                          # last N turns kept (storage cap)
# Per-turn prompt window — only the LAST N turns of stored history are
# injected into the LLM call. Storage stays large for transcript / debug
# but the prompt context never grows past this. Was 20: too long a tail
# kept resurfacing stale tickers and stale drafts ("user typed RELIANCE
# 5 turns ago, now asks 'sell it'" — model picked the wrong it).
CONV_PROMPT_WINDOW_TURNS = 6
CONV_PREFIX = "chat:conv:"
PENDING_TTL_SECONDS = 60 * 10                # 10 min
PENDING_PREFIX = "chat:pending:"
# Active workflow draft TTL: was 1h. A draft that hangs around for an
# hour leaks into completely unrelated turns (PDF report case: a stale
# "Sell HDFCBANK at 10% profit" draft appeared under a "pros and cons of
# Reliance" answer). 10 min is enough to support natural amend-and-
# activate flows without bleeding across topic shifts.
ACTIVE_DRAFT_TTL_SECONDS = 60 * 10
ACTIVE_DRAFT_PREFIX = "chat:active_draft:"


@dataclass
class ActiveDraft:
    """The most recent workflow draft in this conversation.

    Persisted whenever propose_workflow (or the macro fallback / the
    workflow_skeleton fast-path) emits a draft. Read at the top of
    every chat turn — when present, the followup hint injects the
    draft JSON inline so the model amends THIS shape instead of
    reconstructing from history text.

    Cleared on:
      - Explicit user cancellation during fast resume.
      - A successful Save & Activate (frontend posts the workflow,
        we drop the draft on the next turn that observes activation).
      - TTL expiry (1h).
      - Overwrite when a brand-new propose_workflow succeeds.
    """
    tool_name: str           # always "propose_workflow" today; future-proofed
    draft: dict              # the full JSON the LLM emitted as args
    last_caption: str = ""   # human-readable text rendered alongside it
    created_at_iso: str = ""

    def to_json(self) -> str:
        return json.dumps(asdict(self), default=str)

    @classmethod
    def from_json(cls, raw: str | bytes) -> "ActiveDraft":
        data = json.loads(raw if isinstance(raw, str) else raw.decode())
        return cls(**data)


@dataclass
class PendingToolCall:
    """Snapshot of a tool call that fired ASK_USER, ready to resume.

    The chat layer persists this when a tool comes back with
    `needs_clarification + missing_field` set; the next user message
    is treated as the value for `missing_field` if it parses cleanly.
    """
    tool_name: str
    args: dict[str, Any]
    missing_field: str
    field_type: str          # int / float / str / date / enum / bool / any
    field_description: str
    enum: Optional[list[Any]] = None
    asked_at_iso: str = ""

    def to_json(self) -> str:
        return json.dumps(asdict(self), default=str)

    @classmethod
    def from_json(cls, raw: str | bytes) -> "PendingToolCall":
        data = json.loads(raw if isinstance(raw, str) else raw.decode())
        return cls(**data)


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

    # ── Pending tool call (Change 2 — deterministic resume) ─────────

    def _pending_key(self, conv_id: str) -> str:
        return f"{PENDING_PREFIX}{conv_id}"

    def set_pending(self, conv_id: str, pending: PendingToolCall) -> None:
        """Stash a partially-filled tool call so the next user reply
        can resume without an LLM hop. 10-minute TTL — if the user
        takes longer than that, they get the full LLM path."""
        if not conv_id:
            return
        try:
            redis_client.set(
                self._pending_key(conv_id),
                pending.to_json(),
                ex=PENDING_TTL_SECONDS,
            )
        except Exception as e:
            logger.warning("pending set failed: %s", e)

    def get_pending(self, conv_id: str) -> Optional[PendingToolCall]:
        """Return the pending tool call for this conversation, or None
        if there isn't one (or it expired)."""
        if not conv_id:
            return None
        try:
            raw = redis_client.get(self._pending_key(conv_id))
        except Exception as e:
            logger.warning("pending get failed: %s", e)
            return None
        if raw is None:
            return None
        try:
            return PendingToolCall.from_json(raw)
        except (TypeError, ValueError, json.JSONDecodeError) as e:
            logger.warning("pending decode failed: %s", e)
            return None

    def clear_pending(self, conv_id: str) -> None:
        if not conv_id:
            return
        try:
            redis_client.delete(self._pending_key(conv_id))
        except Exception as e:
            logger.warning("pending clear failed: %s", e)

    # ── Active workflow draft (multi-turn amendment) ────────────────

    def _draft_key(self, conv_id: str) -> str:
        return f"{ACTIVE_DRAFT_PREFIX}{conv_id}"

    def set_active_draft(self, conv_id: str, draft: ActiveDraft) -> None:
        """Stash the current workflow draft so the next turn's
        followup hint can inject the actual JSON. 1-hour TTL."""
        if not conv_id:
            return
        try:
            redis_client.set(
                self._draft_key(conv_id),
                draft.to_json(),
                ex=ACTIVE_DRAFT_TTL_SECONDS,
            )
        except Exception as e:
            logger.warning("active_draft set failed: %s", e)

    def get_active_draft(self, conv_id: str) -> Optional[ActiveDraft]:
        if not conv_id:
            return None
        try:
            raw = redis_client.get(self._draft_key(conv_id))
        except Exception as e:
            logger.warning("active_draft get failed: %s", e)
            return None
        if raw is None:
            return None
        try:
            return ActiveDraft.from_json(raw)
        except (TypeError, ValueError, json.JSONDecodeError) as e:
            logger.warning("active_draft decode failed: %s", e)
            return None

    def clear_active_draft(self, conv_id: str) -> None:
        if not conv_id:
            return
        try:
            redis_client.delete(self._draft_key(conv_id))
        except Exception as e:
            logger.warning("active_draft clear failed: %s", e)


_default_store: ConversationStore | None = None


def default_store() -> ConversationStore:
    global _default_store
    if _default_store is None:
        _default_store = ConversationStore()
    return _default_store
