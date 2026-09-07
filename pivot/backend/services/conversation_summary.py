"""Per-conversation chat-history summary — the durable 'gist' of a chat.

Generates a concise natural-language summary of a Postgres conversation via the
chat LLM and persists it to ``conversation_summaries`` (one row per
conversation, refreshed as the chat grows). Read by the UI so a returning user
sees what a long chat was about without replaying every message.

Ownership is enforced on every path: a summary is only ever generated/returned
when ``conversation.user_id == user_id`` — User A can never summarise User B's
chat.
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy.orm import Session

from backend.llm import LLMMessage, get_llm_client
from backend.models import ChatSummary, Conversation, ConversationMessage

logger = logging.getLogger(__name__)

# Regenerate once at least this many NEW user/assistant messages have landed
# since the last summary — keeps LLM cost off the every-message growth path.
REFRESH_EVERY_N = 6
# Cap the transcript fed to the summarizer (newest-N), bounding token cost.
_MAX_MESSAGES = 60
_PER_MSG_CHARS = 800


def get_summary(
    db: Session, conversation_id: str, user_id: int,
) -> Optional[ChatSummary]:
    """The stored summary row for this (conversation, user), or None."""
    return (
        db.query(ChatSummary)
        .filter(
            ChatSummary.conversation_id == conversation_id,
            ChatSummary.user_id == user_id,
        )
        .first()
    )


def _owned_messages(
    db: Session, conversation_id: str, user_id: int,
) -> Optional[list[ConversationMessage]]:
    """Messages of an OWNED conversation (oldest first), or None when the
    conversation isn't found / isn't this user's (the isolation gate)."""
    convo = (
        db.query(Conversation)
        .filter(
            Conversation.id == conversation_id,
            Conversation.user_id == user_id,
        )
        .first()
    )
    if convo is None:
        return None
    return (
        db.query(ConversationMessage)
        .filter(ConversationMessage.conversation_id == conversation_id)
        .order_by(ConversationMessage.created_at)
        .all()
    )


def _text_messages(msgs: list[ConversationMessage]) -> list[ConversationMessage]:
    return [
        m for m in msgs
        if m.role in ("user", "assistant") and (m.content or "").strip()
    ]


async def generate_and_store(
    db: Session, conversation_id: str, user_id: int,
) -> Optional[ChatSummary]:
    """Summarise the conversation via the LLM and upsert the row. Best-effort:
    returns None (and never raises) if the conversation isn't owned, has no
    text, or the LLM call fails."""
    msgs = _owned_messages(db, conversation_id, user_id)
    if msgs is None:
        return None
    text_msgs = _text_messages(msgs)
    if not text_msgs:
        return None

    transcript = "\n".join(
        f"{m.role.upper()}: {str(m.content).strip()[:_PER_MSG_CHARS]}"
        for m in text_msgs[-_MAX_MESSAGES:]
    )
    sys = (
        "Summarise this investing-assistant conversation in 2-4 sentences for "
        "the user's own later recall: what they asked about, the symbols / "
        "strategies / automations / backtests discussed, and any decision or "
        "next step. Be concrete and neutral. No preamble, no bullet points. "
        "Preserve any specific numeric result verbatim (trade counts, win "
        "rates, returns, prices, quantities) — a later turn may ask to "
        "recall an exact figure, and a paraphrase that drops it makes that "
        "recall impossible even though the number was genuinely available "
        "here (reported 2026-07-14: a backtest's win-rate/return figures "
        "were unrecallable a few turns later because the summary dropped "
        "them)."
    )
    try:
        client = get_llm_client()
        resp = await client.complete(
            messages=[
                LLMMessage(role="system", content=sys),
                LLMMessage(role="user", content=transcript),
            ],
            tools=None,
            tool_choice="none",
            max_output_tokens=300,
            temperature=0.2,
        )
        summary = (getattr(resp, "content", None) or "").strip()
    except Exception as e:  # noqa: BLE001 — best-effort, never break the caller
        logger.warning("chat summary generation failed for %s: %s",
                       conversation_id, e)
        return None
    if not summary:
        return None

    row = get_summary(db, conversation_id, user_id)
    if row is None:
        row = ChatSummary(conversation_id=conversation_id, user_id=user_id)
        db.add(row)
    row.summary = summary
    row.message_count = len(text_msgs)
    row.model = (
        getattr(client, "model", None)
        or getattr(client, "provider_name", None)
    )
    db.commit()
    db.refresh(row)
    return row


async def ensure_summary(
    db: Session, conversation_id: str, user_id: int, *, force: bool = False,
) -> Optional[ChatSummary]:
    """Return a fresh summary, regenerating only when stale (>= REFRESH_EVERY_N
    new messages since the last one) or ``force``. Cheap when already fresh."""
    msgs = _owned_messages(db, conversation_id, user_id)
    if msgs is None:
        return None
    text_count = len(_text_messages(msgs))
    existing = get_summary(db, conversation_id, user_id)
    if (
        not force
        and existing is not None
        and (text_count - int(existing.message_count or 0)) < REFRESH_EVERY_N
    ):
        return existing
    if text_count == 0:
        return existing
    return await generate_and_store(db, conversation_id, user_id)
