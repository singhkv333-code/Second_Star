"""Persistence for ConvContext.

Stores the v2 conversation context as JSON under
`chat_v2:ctx:<conv_id>`. Uses the same Redis client as v1 but is
isolated from v1 keys. Falls back to an in-process dict if Redis is
unreachable (mirrors v1's MockRedis behavior, important for tests).

History is intentionally NOT stored here — chat_v2 reuses v1's
existing ConvStore.get_history() / .append() so the FE chat
transcript is unchanged.
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict
from typing import Optional

from backend.cache import redis_client
from backend.chat_v2.state import (
    ConvContext, ConvState, MacroKind, DiscardedDraft,
)

logger = logging.getLogger(__name__)

_KEY_PREFIX = "chat_v2:ctx:"
_TTL_SECONDS = 60 * 60 * 24 * 7  # 7 days


def _key(conv_id: str) -> str:
    return f"{_KEY_PREFIX}{conv_id}"


def load_context(conv_id: str) -> ConvContext:
    """Load ConvContext from Redis, or return a fresh IDLE context
    if missing / corrupt."""
    try:
        raw = redis_client.get(_key(conv_id))
    except Exception as e:
        logger.warning("redis get failed for ctx %s: %s", conv_id, e)
        return ConvContext(conv_id=conv_id)
    if not raw:
        return ConvContext(conv_id=conv_id)
    try:
        data = json.loads(raw)
        return _from_dict(conv_id, data)
    except Exception as e:
        logger.warning("ctx load corrupt for %s: %s — starting fresh", conv_id, e)
        return ConvContext(conv_id=conv_id)


def save_context(ctx: ConvContext) -> None:
    """Persist ConvContext to Redis. No-op on Redis failure."""
    try:
        raw = json.dumps(_to_dict(ctx), default=str)
        redis_client.set(_key(ctx.conv_id), raw, ex=_TTL_SECONDS)
    except Exception as e:
        logger.warning("redis set failed for ctx %s: %s", ctx.conv_id, e)


def reset_context(conv_id: str) -> None:
    """Clear the v2 context (e.g. fresh-session eviction)."""
    try:
        redis_client.delete(_key(conv_id))
    except Exception:
        pass


# ─────────────── (de)serialisation ─────────────────────────────────


def _to_dict(ctx: ConvContext) -> dict:
    """Serialise ConvContext to a plain dict for JSON encoding.
    Enums become their .value strings; nested dataclasses become dicts."""
    d = asdict(ctx)
    d["state"] = ctx.state.value if isinstance(ctx.state, ConvState) else ctx.state
    if ctx.macro_kind is not None:
        d["macro_kind"] = ctx.macro_kind.value if isinstance(ctx.macro_kind, MacroKind) else ctx.macro_kind
    # discarded_drafts items already dicts via asdict, but their
    # macro_kind enum needs flattening too.
    for dd in d.get("discarded_drafts", []):
        if dd.get("macro_kind") and not isinstance(dd["macro_kind"], str):
            dd["macro_kind"] = dd["macro_kind"].value
    return d


def _from_dict(conv_id: str, data: dict) -> ConvContext:
    """Reconstruct ConvContext from a JSON-loaded dict."""
    state = data.get("state") or ConvState.IDLE.value
    try:
        state_enum = ConvState(state)
    except ValueError:
        state_enum = ConvState.IDLE
    kind_raw = data.get("macro_kind")
    kind: Optional[MacroKind] = None
    if kind_raw:
        try:
            kind = MacroKind(kind_raw)
        except ValueError:
            kind = None
    discarded = []
    for dd in data.get("discarded_drafts", []) or []:
        try:
            dd_kind = MacroKind(dd.get("macro_kind")) if dd.get("macro_kind") else None
        except ValueError:
            dd_kind = None
        discarded.append(DiscardedDraft(
            macro_kind=dd_kind,
            macro_tool=dd.get("macro_tool"),
            summary=dd.get("summary", ""),
            turns_ago=int(dd.get("turns_ago", 0)),
        ))
    return ConvContext(
        conv_id=conv_id,
        state=state_enum,
        macro_kind=kind,
        macro_tool=data.get("macro_tool"),
        macro_draft=data.get("macro_draft"),
        draft_summary=data.get("draft_summary"),
        pending_clarification_text=data.get("pending_clarification_text"),
        last_tool=data.get("last_tool"),
        last_tool_args=data.get("last_tool_args") or {},
        focus_symbols=data.get("focus_symbols") or [],
        discarded_drafts=discarded,
        activations=data.get("activations") or [],
        turn_count=int(data.get("turn_count", 0)),
    )
