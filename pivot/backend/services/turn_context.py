"""Per-turn ambient context (a leaf module — safe to import anywhere).

Carries the current chat ``conversation_id`` down to tool handlers that don't
receive it in their signature (e.g. the backtest tools, which take ``(a, kt, db,
uid)``). The Deflated-Sharpe trial counter uses it to group a session's
backtests by CONVERSATION rather than by user — so tuning the same idea in one
chat deflates together, but unrelated chats stay independent.

``ContextVar`` is task- and thread-local and is copied into ``asyncio.to_thread``
workers, so a value set in the async request handler is visible to the engine
running in a worker thread.
"""
from __future__ import annotations

from contextvars import ContextVar
from typing import Optional

_conversation_id: ContextVar[Optional[str]] = ContextVar(
    "chat_conversation_id", default=None
)


def set_conversation_id(conv_id: Optional[str]) -> None:
    _conversation_id.set(conv_id)


def get_conversation_id() -> Optional[str]:
    return _conversation_id.get()


def trial_group_for(uid: Optional[int]) -> Optional[str]:
    """The Deflated-Sharpe trial group for the current turn: the conversation
    if known (``c:<conv_id>``), else the user (``u:<uid>``), else None (no
    grouping — a lone backtest with no session, num_trials stays 1)."""
    conv = get_conversation_id()
    if conv:
        return f"c:{conv}"
    if uid:
        return f"u:{uid}"
    return None
