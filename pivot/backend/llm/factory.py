"""Pick the LLM client based on env. Cached per process.

LLM_PROVIDER=openai (default) → LLMOpenAI
LLM_PROVIDER=sarvam            → LLMSarvam

LLM_MODEL overrides the per-provider default ('gpt-5-mini' / 'sarvam-m').
Switch providers at runtime by setting these env vars and calling
`reset_llm_client_cache()`. Tests use this to swap to a stub client.
"""
from __future__ import annotations

import logging
import os
from functools import lru_cache
from typing import Optional

from backend.llm.base import LLMClient


logger = logging.getLogger(__name__)


_TESTING_OVERRIDE: Optional[LLMClient] = None


def _build_client(provider: str, model: Optional[str]) -> LLMClient:
    if provider == "openai":
        from backend.llm.openai_client import LLMOpenAI
        return LLMOpenAI(model=model)
    if provider == "sarvam":
        from backend.llm.sarvam_client import LLMSarvam
        return LLMSarvam(model=model)
    raise ValueError(
        f"Unknown LLM_PROVIDER {provider!r}; expected 'openai' or 'sarvam'"
    )


@lru_cache(maxsize=1)
def _cached_client() -> LLMClient:
    # Settings is read from .env via pydantic-settings; os.environ is
    # an extra fallback for runtime overrides in tests.
    from backend.config import settings
    provider = (
        os.environ.get("LLM_PROVIDER")
        or settings.llm_provider
        or "openai"
    ).lower().strip()
    model = (
        os.environ.get("LLM_MODEL")
        or settings.llm_model
        or ""
    ).strip() or None
    client = _build_client(provider, model)
    logger.info("LLM client initialized: provider=%s model=%s",
                client.provider_name, client.model)
    return client


def get_llm_client() -> LLMClient:
    """Return the active LLM client. Tests can override via
    `set_llm_client_for_tests(stub)`."""
    if _TESTING_OVERRIDE is not None:
        return _TESTING_OVERRIDE
    return _cached_client()


def reset_llm_client_cache() -> None:
    """Wipe the cached client. Call after changing env vars."""
    _cached_client.cache_clear()


def set_llm_client_for_tests(client: Optional[LLMClient]) -> None:
    """Tests inject a stub here; pass None to clear."""
    global _TESTING_OVERRIDE
    _TESTING_OVERRIDE = client
