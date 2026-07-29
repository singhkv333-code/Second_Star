"""OpenAI prompt-cache warmup for top route signatures.

WHY this exists: cold-start measurements showed the first request on
a never-before-seen route signature hits 0% cache, then immediately
warms to 96%+ on the second turn (`pivot/scripts/cache_probe2.py`).
That first cold-start adds ~3-5s of latency and ~22K full-priced
input tokens.

Pre-firing a small set of representative requests at server start
warms OpenAI's prompt cache for the route signatures most users
will hit, so the first real user turn lands on a warm slot.

Each warmup hop:
  - sends a representative user message
  - lets the tool router select the same cache_key the real path will
  - calls the LLM with `max_output_tokens=16` (just enough for the
    cache prefix to land; we don't actually use the response)

Cost: ~6-8 LLM calls × ~22K input tokens, almost entirely PRE-cached
already on subsequent boots so the cost is bounded. Set
`PIVOT_DISABLE_CACHE_WARMUP=1` to skip (dev / tests).
"""
from __future__ import annotations

import asyncio
import logging
import os

logger = logging.getLogger(__name__)


# Representative messages picked to span the most common route
# signatures observed in the broad-tester probe. Order matters
# slightly: short reads first so the warmup completes early even if
# later calls time out.
_WARMUP_MESSAGES: tuple[str, ...] = (
    "what's the price of TCS",                  # MARKET_QUERY
    "show me my portfolio",                     # PORTFOLIO_QUERY
    "buy 10 RELIANCE at market",                # ORDER_IMMEDIATE
    "build me an agent that buys 5 NIFTYBEES every weekday at 9:15",  # AGENT
    "set up a SIP of 5000 in NIFTYBEES every month",  # SIP
    "compare yields on FDs and government bonds",     # YIELD_QUERY
    "today's top gainers",                      # TOP_MOVERS
    # Analytics route signatures — all four hit different tool subsets.
    "what's RELIANCE's RSI",                    # ANALYTICS_INDICATOR
    "what's TCS Sharpe ratio",                  # ANALYTICS_PERFORMANCE
    "rank RELIANCE TCS INFY by Sharpe",         # ANALYTICS_COMPARE
    "correlation between TCS, INFY, WIPRO",     # ANALYTICS_CORRELATION
)


async def warmup_prompt_cache() -> None:
    """Fire one short LLM call per representative route to warm the
    prompt cache. Runs once at startup, fire-and-forget — failures
    are logged but never crash the app.
    """
    if os.environ.get("PIVOT_DISABLE_CACHE_WARMUP", "").lower() in (
        "1", "true", "yes",
    ):
        logger.info("cache warmup disabled via PIVOT_DISABLE_CACHE_WARMUP")
        return

    # Lazy imports — keep main.py import time fast.
    try:
        from backend.llm.factory import get_llm_client
        from backend.llm.base import LLMMessage
        from backend.prompts.assembler import build_system_prompt
        from backend.services.chat_service import _registry_tools_as_tooldefs
        from backend.services.tool_router import (
            cache_key_for, select_tool_names,
        )
    except Exception as e:
        logger.info("cache warmup imports failed (%s); skipping", e)
        return

    try:
        client = get_llm_client()
    except Exception as e:
        logger.info("cache warmup: no LLM client (%s); skipping", e)
        return

    # Provider check — only OpenAI has a server-side prompt cache to
    # warm. Azure routes through the same Responses API but is treated
    # separately by its own warmer; other providers have no prompt cache.
    provider = getattr(client, "provider_name", "") or ""
    if provider.lower() != "openai":
        logger.info("cache warmup: provider=%s has no prompt cache; skipping", provider)
        return

    sys_prompt = build_system_prompt(role="chat")
    base_msgs = [LLMMessage(role="system", content=sys_prompt)]

    fired = 0
    for msg in _WARMUP_MESSAGES:
        try:
            selected = select_tool_names(msg)
            tools = _registry_tools_as_tooldefs(selected)
            cache_key = cache_key_for(selected)
            messages = [*base_msgs, LLMMessage(role="user", content=msg)]
            # max_output_tokens is intentionally tiny — we don't care
            # about the response, only that the prefix gets cached.
            await client.complete(
                messages=messages,
                tools=tools,
                tool_choice="auto",
                max_output_tokens=16,
                reasoning_effort="minimal",
                temperature=0.2,
                prompt_cache_key=cache_key,
            )
            fired += 1
            logger.info("cache warmup hit: route=%s msg=%r", cache_key, msg[:40])
        except Exception as e:
            # One failure shouldn't stop the rest.
            logger.info("cache warmup miss msg=%r: %s", msg[:40], e)
            continue

    logger.info("cache warmup completed: %d/%d routes warmed", fired, len(_WARMUP_MESSAGES))


def schedule_warmup_after_startup() -> None:
    """Schedule the warmup as a background task. Called from FastAPI's
    startup hook. Returns immediately so the server can start serving
    real traffic — warmup runs in the background.
    """
    try:
        loop = asyncio.get_event_loop()
        loop.create_task(_run_with_delay())
    except Exception as e:
        logger.info("cache warmup scheduling failed: %s", e)


async def _run_with_delay() -> None:
    """Sleep briefly so the rest of startup (DB pool, scheduler) settles
    before we spend tokens on warmup."""
    try:
        await asyncio.sleep(2.0)
        await warmup_prompt_cache()
    except Exception as e:
        logger.info("cache warmup task crashed: %s", e)
