"""
Sarvam AI API client with retry logic and mock fallback.
Sarvam is FREE — use as primary model.
Falls back to mock when SARVAM_API_KEY not set.
"""
import httpx
import json
import logging
import re
import asyncio
from typing import Optional
from backend.config import settings

logger = logging.getLogger(__name__)


_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>\s*", re.DOTALL | re.IGNORECASE)


_TRUNCATED_THINK_RE = re.compile(r"<think>.*", re.DOTALL | re.IGNORECASE)

def _strip_truncated_think(text: str) -> str:
    """Strip an unterminated <think>...EOF block (Sarvam truncates when hitting max_tokens)."""
    if not text or "</think>" in text.lower(): return text
    return _TRUNCATED_THINK_RE.sub("", text).strip()

def _strip_think_blocks(text: str) -> str:
    """Remove <think>...</think> reasoning blocks that some models leak into output."""
    if not text:
        return text
    cleaned = _THINK_BLOCK_RE.sub("", text).strip()
    return _strip_truncated_think(cleaned)


def _is_truncated(raw: str, cleaned: str) -> bool:
    """Detect mid-stream truncation that should trigger a retry with bigger budget.

    Cases handled:
      (a) cleaned response is fully empty (the only case the old heuristic caught)
      (b) cleaned response opens a <LOGICCARD without closing </LOGICCARD>
      (c) raw response opens a <think> without closing </think> (model burned the
          entire budget reasoning and never produced an answer at all)
    """
    if not cleaned:
        return True
    low_clean = cleaned.lower()
    if "<logiccard" in low_clean and "</logiccard>" not in low_clean:
        return True
    low_raw = (raw or "").lower()
    if "<think>" in low_raw and "</think>" not in low_raw:
        return True
    return False


SARVAM_MOCK_MODE = not bool(settings.sarvam_api_key)
SARVAM_API_URL = "https://api.sarvam.ai/v1/chat/completions"
SARVAM_MODEL = "sarvam-m"
MAX_RETRIES = 3
TIMEOUT_SECONDS = 30


MOCK_RESPONSES = {
    "default": "I understand your query. Based on your portfolio and goals, let me help you think through this carefully.",
    "safegrow": '{"strategy_type": "SafeGrow - Capital Guarantee Note", "legs": [{"label": "Safety Leg", "instrument": "Arbitrage Fund", "amount": 92764}, {"label": "Growth Leg", "instrument": "Nifty 50 Call Option (ATM)", "amount": 7236}], "explanation": "Your ₹1,00,000 is split: ₹92,764 goes into an arbitrage fund that grows to exactly ₹1,00,000 at maturity. The remaining ₹7,236 buys a Nifty call option. If Nifty rises, you profit. If Nifty falls, your arbitrage fund returns your full capital.", "payoff_table": [{"scenario": "Nifty +20%", "return_pct": 52.9, "amount": 152875}, {"scenario": "Nifty +10%", "return_pct": 26.5, "amount": 126500}, {"scenario": "Nifty flat", "return_pct": 0, "amount": 100000}, {"scenario": "Nifty -15%", "return_pct": 0, "amount": 100000}, {"scenario": "Nifty -30%", "return_pct": 0, "amount": 100000}], "disclaimer": "This is automation of your instructions, not financial advice."}',
}


async def call_sarvam(
    messages: list,
    system_prompt: str = "",
    temperature: float = 0.3,
    max_tokens: int = 1500,
    json_mode: bool = False,
) -> str:
    """
    Call Sarvam AI API with retry logic.
    Returns the assistant's response text.

    NOTE: /no_think and enable_thinking:false are NOT sent — sarvam-m silently
    ignores both. Cycle-2 testing confirmed every response still contained a
    <think> block. Instead we budget enough tokens (default 1500) for the
    reasoning prelude AND a complete answer, then strip <think> server-side.
    """
    if SARVAM_MOCK_MODE:
        last_user_msg = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        if "safegrow" in last_user_msg.lower() or "capital guarantee" in last_user_msg.lower() or "protect" in last_user_msg.lower():
            return MOCK_RESPONSES["safegrow"]
        return MOCK_RESPONSES["default"]

    full_messages = []
    if system_prompt:
        full_messages.append({"role": "system", "content": system_prompt})
    full_messages.extend(messages)

    # Trim to 8K context — keep system prompt + last N messages
    while len(json.dumps(full_messages)) > 28000 and len(full_messages) > 2:
        # Remove oldest user+assistant pair (indices 1 and 2 if system exists)
        start_idx = 1 if system_prompt else 0
        if len(full_messages) > start_idx + 2:
            full_messages.pop(start_idx)
            full_messages.pop(start_idx)
        else:
            break

    payload = {
        "model": SARVAM_MODEL,
        "messages": full_messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    truncation_retried = False

    for attempt in range(MAX_RETRIES):
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
                response = await client.post(
                    SARVAM_API_URL,
                    headers={"Authorization": f"Bearer {settings.sarvam_api_key}",
                             "Content-Type": "application/json"},
                    json=payload,
                )
                response.raise_for_status()
                data = response.json()
                content = data["choices"][0]["message"]["content"] or ""
                cleaned = _strip_think_blocks(content)
                # Retry-on-truncation: empty cleaned output, unclosed LOGICCARD,
                # or unclosed <think>. Cap at one truncation retry to control cost.
                if not truncation_retried and _is_truncated(content, cleaned):
                    truncation_retried = True
                    payload["max_tokens"] = payload["max_tokens"] * 3
                    logger.warning(
                        "Truncated response detected (empty/unclosed LOGICCARD/unclosed think); "
                        "retrying once with max_tokens=%d",
                        payload["max_tokens"],
                    )
                    continue
                return cleaned
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429 and attempt < MAX_RETRIES - 1:
                await asyncio.sleep(2 ** attempt)
                continue
            raise
        except Exception as e:
            if attempt < MAX_RETRIES - 1:
                await asyncio.sleep(1)
                continue
            logger.error(f"Sarvam API failed after {MAX_RETRIES} attempts: {e}")
            raise

    raise Exception("Sarvam API failed after all retries")
