"""POST /chat/followups — the related questions under a finished answer.

A route of its own rather than a field on the chat response, so the answer
is never held back waiting for it: the frontend renders the reply, then asks
for these, then slots them in when they arrive. A slow or failed call here
costs the turn nothing.

Lives outside routers/chat.py deliberately — that module owns the two
delicate agentic loops and does not need a third, unrelated endpoint in it.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from backend.auth.jwt_handler import get_user_id_from_token
from backend.security.throttle import rate_limit
from backend.services.followups import generate_followups

router = APIRouter(prefix="/chat", tags=["Chat"])


def get_user_id(authorization: str = Header(None)) -> int:
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing token")
    user_id = get_user_id_from_token(authorization.replace("Bearer ", ""))
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token")
    return user_id


class FollowupRequest(BaseModel):
    # Bounded on the way in: this text is pasted straight into a prompt, and
    # an unbounded body would let a client drive an arbitrarily large call.
    question: str = Field(default="", max_length=4000)
    answer: str = Field(default="", max_length=20000)


class FollowupResponse(BaseModel):
    suggestions: list[str]


@router.post(
    "/followups",
    response_model=FollowupResponse,
    dependencies=[Depends(rate_limit("chat_followups", 60, 60))],
)
async def followups(
    request: FollowupRequest,
    user_id: int = Depends(get_user_id),
) -> FollowupResponse:
    return FollowupResponse(
        suggestions=await generate_followups(request.question, request.answer)
    )
