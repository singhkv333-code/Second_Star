"""Conversation persistence endpoints — back the chat sidebar.

Day 8 (#47). Adds Postgres-persisted chat conversations + messages so the
left-sidebar conversation list in pivot-next can render real history rather
than client-only state.

Endpoints (all under /api):
  GET    /api/conversations                       — list user's conversations
  POST   /api/conversations                       — create a new conversation
  GET    /api/conversations/{id}                  — get one conversation (with messages)
  GET    /api/conversations/{id}/messages         — paginated messages list
  POST   /api/conversations/{id}/messages         — append a message
  PATCH  /api/conversations/{id}                  — rename
  DELETE /api/conversations/{id}                  — soft-delete (cascade)

Authoritative for ownership: every read/write checks `conversation.user_id`
matches the requesting user, returning 404 (not 403) on mismatch so we don't
leak existence.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import desc
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.models import Conversation, ConversationMessage
from backend.routers._deps import require_user
from backend.routers._errors import not_found, validation_error

router = APIRouter(prefix="/api/conversations", tags=["Conversations"])


# ── Response models ──────────────────────────────────────────────────


class ConversationSummary(BaseModel):
    id: str
    title: str | None
    created_at: datetime
    updated_at: datetime
    last_message_at: datetime | None
    message_count: int
    preview: str | None  # first ~120 chars of last message


class ConversationListResponse(BaseModel):
    items: list[ConversationSummary]


class MessageOut(BaseModel):
    id: str
    role: str
    content: str
    tool_payload: dict[str, Any] | None
    created_at: datetime


class ConversationDetail(BaseModel):
    id: str
    title: str | None
    created_at: datetime
    updated_at: datetime
    last_message_at: datetime | None
    messages: list[MessageOut]


class MessagesResponse(BaseModel):
    items: list[MessageOut]


class CreateConversationRequest(BaseModel):
    title: str | None = Field(default=None, max_length=255)


class CreateConversationResponse(BaseModel):
    id: str
    title: str | None
    created_at: datetime


class RenameConversationRequest(BaseModel):
    title: str = Field(min_length=1, max_length=255)


class AppendMessageRequest(BaseModel):
    role: str = Field(min_length=1, max_length=16)
    content: str = ""
    tool_payload: dict[str, Any] | None = None


# ── Helpers ──────────────────────────────────────────────────────────


_VALID_ROLES = {"user", "assistant", "tool", "system"}


def _own_conversation(
    db: Session, conversation_id: str, user_id: int,
) -> Conversation:
    """Fetch a conversation iff it belongs to the requesting user.
    Returns 404 (not 403) on mismatch so we don't leak existence."""
    convo = (
        db.query(Conversation)
        .filter(
            Conversation.id == conversation_id,
            Conversation.user_id == user_id,
        )
        .first()
    )
    if not convo:
        raise not_found("conversation not found")
    return convo


def _summarise(convo: Conversation, msgs: list[ConversationMessage]) -> ConversationSummary:
    last = msgs[-1] if msgs else None
    preview: str | None = None
    if last and last.content:
        s = str(last.content).strip()
        preview = s[:120] + ("…" if len(s) > 120 else "")
    return ConversationSummary(
        id=str(convo.id),
        title=convo.title,
        created_at=convo.created_at,
        updated_at=convo.updated_at,
        last_message_at=convo.last_message_at,
        message_count=len(msgs),
        preview=preview,
    )


# ── Endpoints ────────────────────────────────────────────────────────


@router.get(
    "",
    response_model=ConversationListResponse,
    summary="List the requesting user's conversations (newest first)",
)
def list_conversations(
    limit: int = Query(50, ge=1, le=200),
    user_id: int = Depends(require_user),
    db: Session = Depends(get_db),
) -> ConversationListResponse:
    convos = (
        db.query(Conversation)
        .filter(Conversation.user_id == user_id)
        .order_by(
            desc(Conversation.last_message_at),
            desc(Conversation.updated_at),
        )
        .limit(limit)
        .all()
    )
    items: list[ConversationSummary] = []
    for c in convos:
        # Eager loading via relationship; messages list is small per convo.
        items.append(_summarise(c, list(c.messages)))
    return ConversationListResponse(items=items)


@router.post(
    "",
    response_model=CreateConversationResponse,
    status_code=201,
    summary="Create a new conversation",
)
def create_conversation(
    body: CreateConversationRequest,
    user_id: int = Depends(require_user),
    db: Session = Depends(get_db),
) -> CreateConversationResponse:
    convo = Conversation(user_id=user_id, title=body.title)
    db.add(convo)
    db.commit()
    db.refresh(convo)
    return CreateConversationResponse(
        id=str(convo.id),
        title=convo.title,
        created_at=convo.created_at,
    )


@router.get(
    "/{conversation_id}",
    response_model=ConversationDetail,
    summary="Get a conversation with its full messages list",
)
def get_conversation(
    conversation_id: str,
    user_id: int = Depends(require_user),
    db: Session = Depends(get_db),
) -> ConversationDetail:
    convo = _own_conversation(db, conversation_id, user_id)
    msgs = [
        MessageOut(
            id=str(m.id),
            role=str(m.role),
            content=str(m.content or ""),
            tool_payload=m.tool_payload,
            created_at=m.created_at,
        )
        for m in convo.messages
    ]
    return ConversationDetail(
        id=str(convo.id),
        title=convo.title,
        created_at=convo.created_at,
        updated_at=convo.updated_at,
        last_message_at=convo.last_message_at,
        messages=msgs,
    )


@router.get(
    "/{conversation_id}/summary",
    summary="A persisted natural-language summary of the conversation (the 'gist')",
)
async def get_conversation_summary(
    conversation_id: str,
    user_id: int = Depends(require_user),
    db: Session = Depends(get_db),
) -> dict:
    """Return (and lazily refresh) the chat-history summary. Ownership-gated:
    404 if the conversation isn't the requesting user's."""
    _own_conversation(db, conversation_id, user_id)  # 404 on cross-user
    from backend.services.conversation_summary import ensure_summary, get_summary
    row = await ensure_summary(db, conversation_id, user_id)
    if row is None:
        row = get_summary(db, conversation_id, user_id)
    return {
        "conversation_id": conversation_id,
        "summary": row.summary if row else None,
        "message_count": row.message_count if row else 0,
        "updated_at": (
            row.updated_at.isoformat() if row and row.updated_at else None
        ),
    }


@router.get(
    "/{conversation_id}/messages",
    response_model=MessagesResponse,
    summary="Paginated message listing for one conversation",
)
def list_messages(
    conversation_id: str,
    limit: int = Query(200, ge=1, le=1000),
    before: datetime | None = Query(default=None),
    user_id: int = Depends(require_user),
    db: Session = Depends(get_db),
) -> MessagesResponse:
    _own_conversation(db, conversation_id, user_id)
    q = (
        db.query(ConversationMessage)
        .filter(ConversationMessage.conversation_id == conversation_id)
    )
    if before is not None:
        q = q.filter(ConversationMessage.created_at < before)
    msgs = (
        q.order_by(ConversationMessage.created_at.asc()).limit(limit).all()
    )
    return MessagesResponse(
        items=[
            MessageOut(
                id=str(m.id),
                role=str(m.role),
                content=str(m.content or ""),
                tool_payload=m.tool_payload,
                created_at=m.created_at,
            )
            for m in msgs
        ]
    )


@router.post(
    "/{conversation_id}/messages",
    response_model=MessageOut,
    status_code=201,
    summary="Append a message to a conversation (and bump last_message_at)",
)
def append_message(
    conversation_id: str,
    body: AppendMessageRequest,
    user_id: int = Depends(require_user),
    db: Session = Depends(get_db),
) -> MessageOut:
    if body.role not in _VALID_ROLES:
        raise validation_error(
            f"role must be one of {sorted(_VALID_ROLES)}",
            details={"field": "role"},
        )
    convo = _own_conversation(db, conversation_id, user_id)
    msg = ConversationMessage(
        conversation_id=convo.id,
        role=body.role,
        content=body.content,
        tool_payload=body.tool_payload,
    )
    db.add(msg)
    # Bump last_message_at so the sidebar sort stays correct without
    # forcing a fresh updated_at on every read.
    convo.last_message_at = datetime.utcnow()
    # Auto-title from first user message if untitled.
    if not convo.title and body.role == "user" and body.content:
        convo.title = (body.content.strip()[:60]) or None
    db.commit()
    db.refresh(msg)
    return MessageOut(
        id=str(msg.id),
        role=str(msg.role),
        content=str(msg.content or ""),
        tool_payload=msg.tool_payload,
        created_at=msg.created_at,
    )


@router.patch(
    "/{conversation_id}",
    response_model=ConversationSummary,
    summary="Rename a conversation",
)
def rename_conversation(
    conversation_id: str,
    body: RenameConversationRequest,
    user_id: int = Depends(require_user),
    db: Session = Depends(get_db),
) -> ConversationSummary:
    convo = _own_conversation(db, conversation_id, user_id)
    convo.title = body.title
    db.commit()
    db.refresh(convo)
    return _summarise(convo, list(convo.messages))


@router.delete(
    "/{conversation_id}",
    summary="Delete a conversation (cascades to its messages)",
)
def delete_conversation(
    conversation_id: str,
    user_id: int = Depends(require_user),
    db: Session = Depends(get_db),
) -> dict[str, bool]:
    convo = _own_conversation(db, conversation_id, user_id)
    db.delete(convo)
    db.commit()
    return {"deleted": True}
