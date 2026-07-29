"""Provider-agnostic LLM client layer.

Every chat / propose / narrate hop in Pivot goes through the abstraction
defined in `base.py`. Two providers are wired:

  - openai_client.LLMOpenAI       — OpenAI Responses API with reasoning
    support (gpt-5-mini default).
  - azure_client.LLMAzureOpenAI   — Azure AI Foundry's /openai/v1
    endpoint (gpt-5.4-mini default). Used as the production default.

Selection is by env: LLM_PROVIDER=openai|azure, LLM_MODEL=<name>.
Default is Azure. For Azure, LLM_MODEL is the deployment name.

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
