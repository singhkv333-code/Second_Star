"""
Model routing — decides which AI model handles each request type.
Sarvam (FREE) for most tasks. OpenAI for complex maths/guaranteed JSON.
"""
from enum import Enum


class TaskType(str, Enum):
    CHAT = "chat"                   # General conversation → Sarvam
    INTENT = "intent"               # Intent classification → Sarvam
    EXPLAIN = "explain"             # Strategy explanation → Sarvam
    SIZE_LEGS = "size_legs"         # Leg sizing maths → OpenAI (needs precision)
    BACKTEST = "backtest"           # Backtest interpretation → OpenAI
    STRUCTURED_JSON = "structured"  # Guaranteed JSON schema → OpenAI


ROUTING_TABLE = {
    TaskType.CHAT: "sarvam",
    TaskType.INTENT: "sarvam",
    TaskType.EXPLAIN: "sarvam",
    TaskType.SIZE_LEGS: "openai",
    TaskType.BACKTEST: "openai",
    TaskType.STRUCTURED_JSON: "openai",
}


async def route_and_call(
    task_type: TaskType,
    messages: list,
    system_prompt: str = "",
    json_mode: bool = False,
    max_tokens: int = 1000,
) -> str:
    """Routes to correct model and returns response."""
    from backend.agents.sarvam_client import call_sarvam
    from backend.agents.openai_client import call_openai

    model = ROUTING_TABLE.get(task_type, "sarvam")

    if model == "sarvam":
        return await call_sarvam(messages, system_prompt, json_mode=json_mode, max_tokens=max_tokens)
    else:
        return await call_openai(messages, system_prompt, json_mode=True, max_tokens=max_tokens)
