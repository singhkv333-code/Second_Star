"""Admin endpoints — debug/observability only.

  GET /admin/conv/{conv_id}/trace?limit=N
    Returns the most recent N agentic-loop turns for a conversation,
    including every event emitted by chat_trace (llm.call, tool.invoke,
    completeness.missing, etc.).

Auth: same JWT required as the rest of the API. Not gated to
admin-role users yet — that's fine for v1, this is a non-mutating
read of in-memory state. When we add roles, this becomes admin-only.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from typing import Any

from backend.auth.jwt_handler import get_user_id_from_token
from backend.routers._deps import require_admin
from backend.services.chat_trace import get_recent_turns


router = APIRouter(prefix="/admin", tags=["Admin"])


def _user_from_authorization(authorization: str | None) -> int:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing or invalid token")
    user_id = get_user_id_from_token(authorization.replace("Bearer ", "", 1))
    if not user_id:
        raise HTTPException(401, "Invalid or expired token")
    # Admin-only surface (chat traces expose OTHER users' conversations).
    # Same fail-closed membership as routers/_deps.require_admin.
    from backend.config import settings

    raw = (getattr(settings, "admin_user_ids", "") or "").strip()
    admin_ids = {int(p) for p in raw.split(",") if p.strip().isdigit()}
    if int(user_id) not in admin_ids:
        raise HTTPException(403, "admin access required")
    return user_id


@router.get(
    "/conv/{conv_id}/trace",
    summary="Per-turn trace for a conversation",
    description=(
        "Returns the agentic-loop trace for the most recent N turns of a "
        "conversation. Each turn includes every event the chat_service "
        "emitted (llm.call, llm.response, tool.invoke, tool.result, "
        "turn.end). In-memory only — restarts wipe the buffer. Use this "
        "endpoint when chat output looks wrong; it's the cheapest way to "
        "see exactly what the loop did."
    ),
)
async def conv_trace(
    conv_id: str,
    limit: int = Query(10, ge=1, le=25),
    # 2026-07-04 (beta-prep): this endpoint previously had NO auth at all —
    # chat traces expose any user's conversation content, so it's admin-only.
    _admin: int = Depends(require_admin),
) -> dict[str, Any]:
    turns = get_recent_turns(conv_id, limit=limit)
    return {
        "conv_id": conv_id,
        "turn_count": len(turns),
        "turns": [t.to_dict() for t in turns],
    }
