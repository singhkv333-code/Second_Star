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
import time
from typing import Optional, Any
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
SARVAM_M = SARVAM_MODEL
MAX_RETRIES = 3
TIMEOUT_SECONDS = 30


MOCK_RESPONSES = {
    "default": "I understand your query. Based on your portfolio and goals, let me help you think through this carefully.",
    "safegrow": '{"strategy_type": "SafeGrow - Capital Guarantee Note", "legs": [{"label": "Safety Leg", "instrument": "Arbitrage Fund", "amount": 92764}, {"label": "Growth Leg", "instrument": "Nifty 50 Call Option (ATM)", "amount": 7236}], "explanation": "Your ₹1,00,000 is split: ₹92,764 goes into an arbitrage fund that grows to exactly ₹1,00,000 at maturity. The remaining ₹7,236 buys a Nifty call option. If Nifty rises, you profit. If Nifty falls, your arbitrage fund returns your full capital.", "payoff_table": [{"scenario": "Nifty +20%", "return_pct": 52.9, "amount": 152875}, {"scenario": "Nifty +10%", "return_pct": 26.5, "amount": 126500}, {"scenario": "Nifty flat", "return_pct": 0, "amount": 100000}, {"scenario": "Nifty -15%", "return_pct": 0, "amount": 100000}, {"scenario": "Nifty -30%", "return_pct": 0, "amount": 100000}], "disclaimer": "This is automation of your instructions, not financial advice."}',
}


def _mock_response(messages: list) -> dict:
    last_user_msg = next((m["content"] for m in reversed(messages) if m.get("role") == "user"), "")
    lower = last_user_msg.lower()
    if "safegrow" in lower or "capital guarantee" in lower or "protect" in lower:
        return {"content": MOCK_RESPONSES["safegrow"], "tool_call": None,
                "model": SARVAM_MODEL, "latency_ms": 0}
    return {"content": MOCK_RESPONSES["default"], "tool_call": None,
            "model": SARVAM_MODEL, "latency_ms": 0}


_TOOL_CALL_RE = re.compile(r"<TOOL_CALL>\s*(\{.*?\})\s*</TOOL_CALL>", re.DOTALL | re.IGNORECASE)


def _build_tool_instruction(tools: list) -> str:
    """
    Sarvam-m's chat completions endpoint rejects OpenAI-style `tools`/`tool_choice`
    payloads with HTTP 400. Instead we describe the tools in the system prompt and
    ask the model to emit a structured <TOOL_CALL>{...}</TOOL_CALL> block when an
    action should be taken. We then parse that block ourselves.
    """
    lines = [
        "You can call ONE of the tools below if — and only if — the user is asking "
        "for an action that matches a tool. If no tool fits, reply normally.",
        "",
        "Tools (JSON Schema):",
    ]
    for t in tools:
        fn = t.get("function", {})
        try:
            schema = json.dumps(fn.get("parameters", {}), separators=(",", ":"))
        except Exception:
            schema = "{}"
        lines.append(f"- {fn.get('name', '')}: {fn.get('description', '')}")
        lines.append(f"  parameters: {schema}")
    lines.extend([
        "",
        "If you decide to call a tool, end your reply with EXACTLY one block:",
        "<TOOL_CALL>{\"name\":\"<tool_name>\",\"arguments\":{...}}</TOOL_CALL>",
        "Use double-quoted JSON. Do not wrap it in markdown fences. Do not invent "
        "tool names. Omit the block entirely when no tool is appropriate.",
    ])
    return "\n".join(lines)


def _extract_emulated_tool_call(raw: str) -> tuple[str, Optional[dict]]:
    """Pull a <TOOL_CALL>{...}</TOOL_CALL> block out of `raw`. Returns (clean_text, tool_call|None)."""
    if not raw:
        return raw, None
    match = _TOOL_CALL_RE.search(raw)
    if not match:
        return raw, None
    body = match.group(1)
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return raw, None
    name = parsed.get("name")
    args = parsed.get("arguments", {})
    if not name or not isinstance(args, dict):
        return raw, None
    cleaned_text = _TOOL_CALL_RE.sub("", raw).strip()
    return cleaned_text, {"name": name, "arguments": args}


async def call_sarvam(
    messages: list,
    system_prompt: str = "",
    temperature: float = 0.3,
    max_tokens: int = 1500,
    json_mode: bool = False,
    tools: Optional[list] = None,
    tool_choice: Optional[Any] = None,  # accepted for API parity but unused (Sarvam rejects it)
    model: str = SARVAM_MODEL,
    reasoning_effort: Optional[str] = None,
) -> dict:
    """
    Call Sarvam AI with retry logic.
    Returns dict: {"content": str, "tool_call": dict|None, "model": str, "latency_ms": int}.

    Tool-calling note: Sarvam-m on /v1/chat/completions returns 400 when the
    OpenAI `tools`/`tool_choice` fields are sent, so we emulate function-calling
    by injecting tool definitions into the system prompt and parsing a
    <TOOL_CALL> block from the response.
    """
    if SARVAM_MOCK_MODE:
        return _mock_response(messages)

    effective_system = system_prompt or ""
    if tools:
        instruction = _build_tool_instruction(tools)
        effective_system = (
            f"{effective_system}\n\n{instruction}" if effective_system else instruction
        )
        # Tool calls need budget to materialise — bump small caps so the model
        # doesn't truncate before emitting <TOOL_CALL>.
        if max_tokens < 600:
            max_tokens = 600

    full_messages = []
    if effective_system:
        full_messages.append({"role": "system", "content": effective_system})
    full_messages.extend(messages)

    while len(json.dumps(full_messages)) > 28000 and len(full_messages) > 2:
        start_idx = 1 if effective_system else 0
        if len(full_messages) > start_idx + 2:
            full_messages.pop(start_idx)
            full_messages.pop(start_idx)
        else:
            break

    payload: dict = {
        "model": model,
        "messages": full_messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    if reasoning_effort:
        payload["reasoning_effort"] = reasoning_effort
    # Deliberately NOT forwarding tools/tool_choice — Sarvam rejects them.

    truncation_retried = False
    started = time.monotonic()

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
                choice = data["choices"][0]["message"]

                # Native function-calling fallback (in case a future Sarvam release
                # starts returning OpenAI-shaped tool_calls).
                tool_result = None
                if choice.get("tool_calls"):
                    tc = choice["tool_calls"][0]
                    try:
                        tool_result = {
                            "name": tc["function"]["name"],
                            "arguments": json.loads(tc["function"]["arguments"]),
                        }
                    except Exception:
                        pass

                raw = choice.get("content") or ""
                cleaned = _strip_think_blocks(raw)

                # Pull emulated <TOOL_CALL> block out of the prose if present.
                if not tool_result and tools:
                    cleaned, tool_result = _extract_emulated_tool_call(cleaned)

                if (not tool_result and not truncation_retried
                        and _is_truncated(raw, cleaned)):
                    truncation_retried = True
                    # Sarvam-m starter tier caps max_tokens at 2048; cap retry there.
                    payload["max_tokens"] = min(payload["max_tokens"] * 3, 2048)
                    logger.warning(
                        "Truncated response detected; retrying once with max_tokens=%d",
                        payload["max_tokens"],
                    )
                    continue

                latency_ms = int((time.monotonic() - started) * 1000)
                return {"content": cleaned, "tool_call": tool_result,
                        "model": model, "latency_ms": latency_ms}
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429 and attempt < MAX_RETRIES - 1:
                await asyncio.sleep(2 ** attempt)
                continue
            logger.error(
                "Sarvam returned %s: %s",
                e.response.status_code,
                e.response.text[:300],
            )
            raise
        except Exception as e:
            if attempt < MAX_RETRIES - 1:
                await asyncio.sleep(1)
                continue
            logger.error(f"Sarvam API failed after {MAX_RETRIES} attempts: {e}")
            raise

    raise Exception("Sarvam API failed after all retries")
