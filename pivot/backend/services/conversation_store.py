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
# R2: deterministic resolution of "yes" / "no" after a clarification.
# Stored when ASK_USER is fired with structured options or a
# default_on_yes. The next pure-affirmative turn consumes it
# without an LLM hop. Short TTL — clarifications are momentary.
PENDING_RESOLUTION_TTL_SECONDS = 60 * 10     # 10 min
PENDING_RESOLUTION_PREFIX = "chat:resolution:"
# Strategy clarify flow (Workstream A): the in-band slot-state + the active
# clarify_card (questions + current index) while a dynamic-questions card is on
# screen. The FE round-trips session_slot_state on the next message; we ALSO
# persist it here so the answer can be normalised + the N-of-M flow advanced
# without re-running the generator. Short TTL — a clarify card is momentary.
CLARIFY_TTL_SECONDS = 60 * 15                 # 15 min
CLARIFY_PREFIX = "chat:clarify:"
# Active workflow draft TTL: was 1h. A draft that hangs around for an
# hour leaks into completely unrelated turns (PDF report case: a stale
# "Sell HDFCBANK at 10% profit" draft appeared under a "pros and cons of
# Reliance" answer). 10 min is enough to support natural amend-and-
# activate flows without bleeding across topic shifts.
ACTIVE_DRAFT_TTL_SECONDS = 60 * 10
ACTIVE_DRAFT_PREFIX = "chat:active_draft:"
# Addressable multi-draft map (Track C): per-symbol drafts in one
# conversation. The single active_draft slot stays the "most recent"
# pointer (back-compat with every existing call site); the map lets a
# named back-reference ("change the INFY one") resolve to the right
# draft instead of mutating whatever happened to be in the slot.
DRAFT_MAP_PREFIX = "chat:draft_map:"
DRAFT_MAP_MAX_DRAFTS = 4
# Track C #1: the workflow the conversation last registered via chat —
# powers "is it actually live?" status readbacks without a DB scan.
REGISTERED_WF_PREFIX = "chat:registered_wf:"
REGISTERED_WF_TTL_SECONDS = 60 * 60 * 24


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
    # Track C: primary symbol this draft acts on (uppercase) — the
    # addressing key in the per-conversation draft map. Empty string
    # when no symbol could be derived (the draft is still usable via
    # the single most-recent slot).
    symbol: str = ""

    def to_json(self) -> str:
        return json.dumps(asdict(self), default=str)

    @classmethod
    def from_json(cls, raw: str | bytes) -> "ActiveDraft":
        data = json.loads(raw if isinstance(raw, str) else raw.decode())
        known = {
            "tool_name", "draft", "last_caption", "created_at_iso", "symbol",
        }
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass
class PendingResolution:
    """R2: structured state for a "yes" / "no" deterministic resolve.

    When the LLM emits ASK_USER with either `default_on_yes` (a single
    value the user is most likely to accept) or `options` (a list of
    labelled choices), we persist this record. On the next turn, a
    pure-affirmative reply ("yes", "do it", "go ahead") is resolved
    to `default_on_yes` without an LLM hop — fixing the
    over-confirmation loop and the "yes" → fabricated context bug
    visible in screenshots 7, 9, 10.

    `original_intent` is the user's first request that spawned the
    clarification — carried so the chat layer can stitch context for
    the follow-up tool call if needed.
    """
    question: str
    default_on_yes: Optional[str] = None
    options: list[str] = None  # type: ignore[assignment]
    original_intent: Optional[str] = None
    asked_at_iso: str = ""

    def __post_init__(self) -> None:
        if self.options is None:
            self.options = []

    def to_json(self) -> str:
        return json.dumps(asdict(self), default=str)

    @classmethod
    def from_json(cls, raw: str | bytes) -> "PendingResolution":
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
class ClarifyState:
    """Active dynamic-clarify flow (Workstream A) for one conversation.

    Persisted when ``ask_user_dynamic`` emits a clarify_card so the next user
    message (an option pick / free text / skip) can be normalised into the
    travelling slot-state and the N-of-M flow advanced WITHOUT re-running the
    VOI generator. When the budget is exhausted / the user says "just build it",
    the chat layer hands ``slot_state`` to ``strategy_builder.build_strategy``.

    Fields are plain JSON (dicts/lists), not Pydantic models, so the dataclass
    stays trivially (de)serialisable through Redis; the chat layer rehydrates
    ``SlotState`` / ``ClarifyCard`` from these dicts at the edges.

      * ``request``     — the original strategy ask (drives the eventual build).
      * ``slot_state``  — the current ``SlotState`` as a dict (the in-band state).
      * ``questions``   — the ranked ``ClarifyQuestion`` dicts (the whole card).
      * ``index``       — 0-based cursor into ``questions`` (the next to answer).
    """
    request: str
    slot_state: dict[str, Any]
    questions: list[dict[str, Any]]
    index: int = 0
    asked_at_iso: str = ""
    # Discriminator for the resume terminal. "portfolio" (default — legacy
    # state deserialises unchanged) folds answers via clarify_engine and builds
    # build_strategy; "agent" folds via agent_clarify and builds via
    # ``build_tool`` (propose_workflow). New fields carry defaults so a state
    # written before this field existed still rehydrates.
    kind: str = "portfolio"
    build_tool: str = "build_strategy"

    def to_json(self) -> str:
        return json.dumps(asdict(self), default=str)

    @classmethod
    def from_json(cls, raw: str | bytes) -> "ClarifyState":
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

    # ── Strategy clarify flow (Workstream A — dynamic questions) ────────

    def _clarify_key(self, conv_id: str) -> str:
        return f"{CLARIFY_PREFIX}{conv_id}"

    def set_clarify(self, conv_id: str, state: ClarifyState) -> None:
        """Stash the active clarify flow so the next reply advances the N-of-M
        flow in-band (no generator re-run). 15-min TTL — clarification is
        momentary; after that the user gets the full LLM path."""
        if not conv_id:
            return
        try:
            redis_client.set(
                self._clarify_key(conv_id),
                state.to_json(),
                ex=CLARIFY_TTL_SECONDS,
            )
        except Exception as e:
            logger.warning("clarify set failed: %s", e)

    def get_clarify(self, conv_id: str) -> Optional[ClarifyState]:
        """Return the active clarify flow for this conversation, or None."""
        if not conv_id:
            return None
        try:
            raw = redis_client.get(self._clarify_key(conv_id))
        except Exception as e:
            logger.warning("clarify get failed: %s", e)
            return None
        if raw is None:
            return None
        try:
            return ClarifyState.from_json(raw)
        except (TypeError, ValueError, json.JSONDecodeError) as e:
            logger.warning("clarify decode failed: %s", e)
            return None

    def clear_clarify(self, conv_id: str) -> None:
        if not conv_id:
            return
        try:
            redis_client.delete(self._clarify_key(conv_id))
        except Exception as e:
            logger.warning("clarify clear failed: %s", e)

    # ── Active workflow draft (multi-turn amendment) ────────────────
    #
    # Two layers of state:
    #   chat:active_draft:{conv}  → the MOST RECENT draft (single slot,
    #                               read by every legacy call site)
    #   chat:draft_map:{conv}     → ordered JSON list of drafts keyed by
    #                               primary symbol — lets "change the
    #                               INFY one" address a parked draft
    #                               without evicting the others.

    def _draft_key(self, conv_id: str) -> str:
        return f"{ACTIVE_DRAFT_PREFIX}{conv_id}"

    def _draft_map_key(self, conv_id: str) -> str:
        return f"{DRAFT_MAP_PREFIX}{conv_id}"

    def _read_draft_map(self, conv_id: str) -> list[ActiveDraft]:
        """Ordered list of parked drafts, oldest first. Best-effort."""
        if not conv_id:
            return []
        try:
            raw = redis_client.get(self._draft_map_key(conv_id))
        except Exception as e:
            logger.warning("draft_map get failed: %s", e)
            return []
        if raw is None:
            return []
        try:
            items = json.loads(raw if isinstance(raw, str) else raw.decode())
        except (TypeError, ValueError, json.JSONDecodeError):
            return []
        out: list[ActiveDraft] = []
        if isinstance(items, list):
            for item in items:
                try:
                    out.append(ActiveDraft.from_json(json.dumps(item)))
                except (TypeError, ValueError):
                    continue
        return out

    def _write_draft_map(self, conv_id: str, drafts: list[ActiveDraft]) -> None:
        try:
            redis_client.set(
                self._draft_map_key(conv_id),
                json.dumps([asdict(d) for d in drafts], default=str),
                ex=ACTIVE_DRAFT_TTL_SECONDS,
            )
        except Exception as e:
            logger.warning("draft_map set failed: %s", e)

    def set_active_draft(self, conv_id: str, draft: ActiveDraft) -> Optional[str]:
        """Stash the current workflow draft so the next turn's
        followup hint can inject the actual JSON.

        Also upserts into the per-symbol draft map: a draft for a NEW
        symbol is appended (the prior symbol's draft stays parked, no
        eviction); a draft for an EXISTING symbol replaces that entry
        in place. Capped at DRAFT_MAP_MAX_DRAFTS with LRU eviction.

        Returns the symbol of an LRU-evicted draft (so the caller can
        surface an honest "I dropped the oldest draft (X)" note), or
        None when nothing was evicted.
        """
        if not conv_id:
            return None
        evicted: Optional[str] = None
        try:
            redis_client.set(
                self._draft_key(conv_id),
                draft.to_json(),
                ex=ACTIVE_DRAFT_TTL_SECONDS,
            )
        except Exception as e:
            logger.warning("active_draft set failed: %s", e)
        # Upsert the per-symbol map. Key on (symbol or tool_name) so two
        # symbol-less drafts of the same tool replace each other rather
        # than piling up.
        try:
            key = draft.symbol or f"_{draft.tool_name}"
            drafts = self._read_draft_map(conv_id)
            drafts = [
                d for d in drafts
                if (d.symbol or f"_{d.tool_name}") != key
            ]
            drafts.append(draft)
            if len(drafts) > DRAFT_MAP_MAX_DRAFTS:
                dropped = drafts.pop(0)
                evicted = dropped.symbol or dropped.tool_name
            self._write_draft_map(conv_id, drafts)
        except Exception as e:  # noqa: BLE001 — map is best-effort
            logger.warning("draft_map upsert failed: %s", e)
        return evicted

    def get_active_draft(
        self, conv_id: str, symbol: Optional[str] = None,
    ) -> Optional[ActiveDraft]:
        """Return the active draft. ``symbol=None`` keeps the legacy
        behaviour (most-recent draft, single slot); a named symbol
        resolves against the per-symbol map."""
        if not conv_id:
            return None
        if symbol:
            want = symbol.strip().upper()
            for d in reversed(self._read_draft_map(conv_id)):
                if (d.symbol or "").upper() == want:
                    return d
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

    def list_active_drafts(self, conv_id: str) -> list[ActiveDraft]:
        """All parked drafts for this conversation, oldest first."""
        return self._read_draft_map(conv_id)

    def clear_active_draft(
        self, conv_id: str, symbol: Optional[str] = None,
    ) -> None:
        """``symbol=None`` clears EVERYTHING (slot + map — topic shift /
        session reset semantics). A named symbol removes only that map
        entry; the single slot is repointed to the most recent
        remaining draft (or cleared if none remain)."""
        if not conv_id:
            return
        if symbol:
            want = symbol.strip().upper()
            try:
                drafts = [
                    d for d in self._read_draft_map(conv_id)
                    if (d.symbol or "").upper() != want
                ]
                if drafts:
                    self._write_draft_map(conv_id, drafts)
                    current = self.get_active_draft(conv_id)
                    if current is not None and (
                        (current.symbol or "").upper() == want
                    ):
                        redis_client.set(
                            self._draft_key(conv_id),
                            drafts[-1].to_json(),
                            ex=ACTIVE_DRAFT_TTL_SECONDS,
                        )
                else:
                    redis_client.delete(self._draft_map_key(conv_id))
                    redis_client.delete(self._draft_key(conv_id))
            except Exception as e:
                logger.warning("active_draft named clear failed: %s", e)
            return
        try:
            redis_client.delete(self._draft_key(conv_id))
            redis_client.delete(self._draft_map_key(conv_id))
        except Exception as e:
            logger.warning("active_draft clear failed: %s", e)

    # ── Last chat-registered workflow (Track C #1) ───────────────────

    def _registered_wf_key(self, conv_id: str) -> str:
        return f"{REGISTERED_WF_PREFIX}{conv_id}"

    def set_registered_workflow_id(self, conv_id: str, workflow_id: str) -> None:
        if not conv_id or not workflow_id:
            return
        try:
            redis_client.set(
                self._registered_wf_key(conv_id),
                str(workflow_id),
                ex=REGISTERED_WF_TTL_SECONDS,
            )
        except Exception as e:
            logger.warning("registered_wf set failed: %s", e)

    def get_registered_workflow_id(self, conv_id: str) -> Optional[str]:
        if not conv_id:
            return None
        try:
            raw = redis_client.get(self._registered_wf_key(conv_id))
        except Exception as e:
            logger.warning("registered_wf get failed: %s", e)
            return None
        if raw is None:
            return None
        return raw if isinstance(raw, str) else raw.decode()

    # ── Pending resolution (R2 — deterministic "yes" / "no") ─────────

    def _resolution_key(self, conv_id: str) -> str:
        return f"{PENDING_RESOLUTION_PREFIX}{conv_id}"

    def set_pending_resolution(
        self, conv_id: str, resolution: PendingResolution,
    ) -> None:
        if not conv_id:
            return
        try:
            redis_client.set(
                self._resolution_key(conv_id),
                resolution.to_json(),
                ex=PENDING_RESOLUTION_TTL_SECONDS,
            )
        except Exception as e:
            logger.warning("pending_resolution set failed: %s", e)

    def get_pending_resolution(
        self, conv_id: str,
    ) -> Optional[PendingResolution]:
        if not conv_id:
            return None
        try:
            raw = redis_client.get(self._resolution_key(conv_id))
        except Exception as e:
            logger.warning("pending_resolution get failed: %s", e)
            return None
        if raw is None:
            return None
        try:
            return PendingResolution.from_json(raw)
        except (TypeError, ValueError, json.JSONDecodeError) as e:
            logger.warning("pending_resolution decode failed: %s", e)
            return None

    def clear_pending_resolution(self, conv_id: str) -> None:
        if not conv_id:
            return
        try:
            redis_client.delete(self._resolution_key(conv_id))
        except Exception as e:
            logger.warning("pending_resolution clear failed: %s", e)


_default_store: ConversationStore | None = None


def default_store() -> ConversationStore:
    global _default_store
    if _default_store is None:
        _default_store = ConversationStore()
    return _default_store
