"""Azure AI Foundry / Azure OpenAI client.

Reuses LLMOpenAI's payload + parsing logic — the Foundry /openai/v1
path is wire-compatible with OpenAI's Responses API (verified 2026-05-20
against deploymentpivot111). Only differences:

  - Base URL points at the tenant's Foundry/AOAI resource.
  - Auth header is `api-key:` instead of `Authorization: Bearer`.
  - `model` field carries the *deployment name* (e.g. `gpt-5.4-mini`),
    which Azure resolves to its bound underlying model + version.
"""
from __future__ import annotations

from typing import Optional

from backend.config import settings
from backend.llm.openai_client import LLMOpenAI


class LLMAzureOpenAI(LLMOpenAI):
    """Azure-flavoured Responses API client.

    Endpoint comes from `settings.azure_openai_endpoint` (the base URL
    ending in `/openai/v1`), key from `settings.azure_key`. Model name
    is the deployment name configured in the Azure portal.
    """

    provider_name = "azure"

    def __init__(
        self,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        endpoint: Optional[str] = None,
    ) -> None:
        self.model = model or settings.llm_model or "gpt-5.4-mini"
        self._api_key = api_key or settings.azure_key
        base = (endpoint or settings.azure_openai_endpoint or "").rstrip("/")
        if not base:
            raise ValueError(
                "AZURE_OPENAI_ENDPOINT is empty — set it to the Foundry "
                "/openai/v1 URL (e.g. https://<resource>.services.ai.azure.com/openai/v1)."
            )
        # Set as instance attribute so it shadows the class-level URL
        # without mutating the parent class's default.
        self.API_URL = f"{base}/responses"

    def _auth_headers(self) -> dict[str, str]:
        return {
            "api-key": self._api_key,
            "Content-Type": "application/json",
        }

    # Azure's gpt-5.4 deployments reject reasoning.effort='minimal' — they
    # accept 'none', 'low', 'medium', 'high', 'xhigh'. Pivot's chat hops
    # send 'minimal' when planning is thin; map that onto 'none' so the
    # request goes through without a 400.
    _EFFORT_MAP = {
        "minimal": "none",
        "low": "low",
        "medium": "medium",
        "high": "high",
    }

    def _translate_reasoning_effort(self, effort):
        if effort is None:
            return None
        return self._EFFORT_MAP.get(effort, effort)
