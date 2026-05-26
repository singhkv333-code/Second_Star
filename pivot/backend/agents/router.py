"""Legacy task-typed routing layer — now a thin wrapper over the
unified `LLMClient` abstraction.

Historically this module fanned out between two raw clients (one
HTTP client for chat/intent/explain, the OpenAI client for
maths / structured JSON). The unified `backend.llm.factory.get_llm_client`
now returns whichever provider `LLM_PROVIDER` selects (Azure OpenAI
by default), so `route_and_call` just forwards every TaskType to it.
The TaskType enum is preserved so existing callers
(`agents/explainer.py`, `agents/parser.py`) compile unchanged.
"""
from __future__ import annotations

from enum import Enum

from backend.llm.base import LLMMessage
from backend.llm.factory import get_llm_client


class TaskType(str, Enum):
    CHAT = "chat"
    INTENT = "intent"
    EXPLAIN = "explain"
    SIZE_LEGS = "size_legs"
    BACKTEST = "backtest"
    STRUCTURED_JSON = "structured"


async def route_and_call(
    task_type: TaskType,
    messages: list,
    system_prompt: str = "",
    json_mode: bool = False,
    max_tokens: int = 1000,
) -> str:
    """Forward every task to the configured LLM. Returns content as str."""
    msgs: list[LLMMessage] = []
    if system_prompt:
        msgs.append(LLMMessage(role="system", content=system_prompt))
    for m in messages:
        role = m.get("role") if isinstance(m, dict) else None
        content = m.get("content") if isinstance(m, dict) else None
        if role and content:
            msgs.append(LLMMessage(role=role, content=content))

    client = get_llm_client()
    resp = await client.complete(
        messages=msgs,
        max_output_tokens=max_tokens,
        temperature=0.2,
        response_format="json_object" if json_mode else None,
    )
    return resp.content or ""
