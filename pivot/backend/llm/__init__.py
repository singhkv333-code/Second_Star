"""Provider-agnostic LLM client layer.

Every chat / propose / narrate hop in Pivot goes through the abstraction
defined in `base.py`. Two providers are wired:

  - openai_client.LLMOpenAI  — primary. Uses OpenAI's Responses API
    with reasoning support (gpt-5-mini default). Has native function
    calling and structured outputs.

  - sarvam_client.LLMSarvam  — fallback. Sarvam-m has a tight 7K
    context window and rejects OpenAI-style tool/tool_choice payloads,
    so this client emulates function calling by injecting tool
    descriptions into the system prompt and parsing a <TOOL_CALL>
    block out of the response.

Selection is by env: LLM_PROVIDER=openai|sarvam, LLM_MODEL=<name>.
Default in dev and prod is OpenAI; Sarvam stays available for cheap
automated tests where output quality doesn't matter.

The `factory.get_llm_client()` is the only place callers should touch
to pick a client — never instantiate the concrete classes directly,
that breaks env-driven swap.
"""
from __future__ import annotations

from .base import LLMClient, LLMMessage, LLMResponse, ToolDef, FinishReason
from .factory import get_llm_client, reset_llm_client_cache


__all__ = [
    "LLMClient",
    "LLMMessage",
    "LLMResponse",
    "ToolDef",
    "FinishReason",
    "get_llm_client",
    "reset_llm_client_cache",
]
