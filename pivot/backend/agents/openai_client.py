"""
OpenAI fallback client — used for complex reasoning and guaranteed JSON output.
Falls back to mock when OPENAI_API_KEY not set.
"""
import json
import logging
import asyncio
import httpx
from backend.config import settings

logger = logging.getLogger(__name__)

OPENAI_MOCK_MODE = not bool(settings.openai_api_key)
OPENAI_API_URL = "https://api.openai.com/v1/chat/completions"
OPENAI_MODEL = "gpt-4o-mini"


async def call_openai(
    messages: list,
    system_prompt: str = "",
    json_mode: bool = True,
    max_tokens: int = 1000,
) -> str:
    """Call OpenAI GPT-4o mini. Enforces JSON mode for structured outputs."""
    if OPENAI_MOCK_MODE:
        return json.dumps({"result": "mock_openai_response", "note": "OpenAI key not set"})

    full_messages = []
    if system_prompt:
        full_messages.append({"role": "system", "content": system_prompt})
    full_messages.extend(messages)

    payload = {
        "model": OPENAI_MODEL,
        "messages": full_messages,
        "max_tokens": max_tokens,
        "temperature": 0.2,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    async with httpx.AsyncClient(timeout=45) as client:
        response = await client.post(
            OPENAI_API_URL,
            headers={"Authorization": f"Bearer {settings.openai_api_key}",
                     "Content-Type": "application/json"},
            json=payload,
        )
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]
