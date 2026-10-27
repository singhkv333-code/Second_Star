"""Voice input: browser-recorded audio → text.

POST /audio/transcribe accepts a browser MediaRecorder blob (webm/opus on
Chrome/Firefox, mp4/aac on Safari) and returns the spoken text in English.

Provider chain (probed live 2026-07-03):
  1. Azure Speech fast-transcription — the Foundry AI-Services resource
     (settings.azure_key) bundles Speech on the same key/host; language id
     across en-IN + hi-IN. This is the FUNDED path. When the transcript
     comes back in Devanagari and mode="translate" (the default), the
     deployed chat LLM (gpt-5.4-mini) renders it into English — the
     working language of chat and company search. Latin-script Hinglish
     passes through untouched (the chat agent handles it natively).
  2. OpenAI whisper-1 /audio/translations — fallback when no Azure key.
     The current OPENAI_API_KEY has NO quota (429 insufficient_quota), so
     this path only becomes real if that key is funded/replaced.

The Foundry /openai/v1 audio route itself is NOT usable: the resource has
no whisper/gpt-4o-transcribe *deployment* (DeploymentNotFound), only the
catalog listing.
"""

from __future__ import annotations

import json
import logging
import re
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, File, Query, UploadFile
from backend.security.throttle import rate_limit
from pydantic import BaseModel

from backend.config import settings
from backend.llm.base import LLMMessage
from backend.llm.factory import get_llm_client
from backend.routers._deps import require_user
from backend.routers._errors import http_error, not_yet_available, validation_error

router = APIRouter(prefix="/audio", tags=["Audio"])
log = logging.getLogger("pivot.audio")

_FAST_TRANSCRIBE_PATH = "/speechtotext/transcriptions:transcribe"
_FAST_TRANSCRIBE_API_VERSION = "2024-11-15"
# Language-id candidates. Fast transcription picks per-segment, so a
# Hinglish sentence that flips mid-way still comes back coherent.
_LOCALES = ["en-IN", "hi-IN"]

_OPENAI_AUDIO_BASE = "https://api.openai.com/v1/audio"
_WHISPER_MODEL = "whisper-1"

# MediaRecorder blobs are opus/aac voice, ~1 KB/s — a 60 s clip is well under
# 1 MB. 15 MB rejects runaway uploads without ever touching a real recording.
_MAX_UPLOAD_BYTES = 15 * 1024 * 1024

# Browser containers we expect from MediaRecorder (content-type may carry a
# ";codecs=opus" suffix). The speech services sniff the container themselves;
# this list only rejects obviously-wrong uploads before we pay for the call.
_ACCEPTED_PREFIXES = ("audio/", "video/webm", "video/mp4", "application/octet-stream")

_DEVANAGARI_RE = re.compile(r"[ऀ-ॿ]")

_TRANSLATE_SYSTEM = (
    "You translate Indian-language voice queries into English for a stock-"
    "market app. Return ONLY the English translation — no preamble, no "
    "quotes. Keep company names, tickers, and numbers exactly as spoken."
)


def _speech_endpoint() -> str:
    """The AI-Services host that carries the Speech APIs. Same host as the
    Foundry /openai/v1 chat endpoint, minus the path."""
    parsed = urlparse(settings.azure_openai_endpoint)
    return f"{parsed.scheme}://{parsed.netloc}" if parsed.netloc else ""


class TranscriptionResult(BaseModel):
    text: str
    mode: str  # "translate" | "transcribe"
    provider: str


async def _azure_fast_transcribe(
    data: bytes, filename: str, content_type: str, user_id: int
) -> str:
    endpoint = _speech_endpoint()
    definition = json.dumps({"locales": _LOCALES})
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{endpoint}{_FAST_TRANSCRIBE_PATH}",
                params={"api-version": _FAST_TRANSCRIBE_API_VERSION},
                headers={"Ocp-Apim-Subscription-Key": settings.azure_key},
                files={
                    "audio": (filename, data, content_type or "audio/webm"),
                    "definition": (None, definition, "application/json"),
                },
            )
    except httpx.HTTPError as exc:
        log.warning("audio.azure network failure user=%s: %s", user_id, exc)
        raise http_error(
            502, "upstream_error", "speech service unreachable — try again"
        ) from exc

    if resp.status_code != 200:
        # Don't relay the raw upstream body (may echo resource ids); log it
        # for diagnosis and return an honest one-liner.
        log.warning(
            "audio.azure upstream %s user=%s body=%s",
            resp.status_code,
            user_id,
            resp.text[:300],
        )
        raise http_error(
            502, "upstream_error", "speech service rejected the audio — try again"
        )

    phrases = resp.json().get("combinedPhrases") or []
    return " ".join(p.get("text", "") for p in phrases).strip()


async def _llm_translate_to_english(text: str, user_id: int) -> str:
    """Devanagari transcript → English via the deployed chat model. Falls
    back to the original transcript on any model error — the chat agent
    reads Hindi natively, so a failed translation should never sink the
    whole voice turn."""
    try:
        response = await get_llm_client().complete(
            [
                LLMMessage(role="system", content=_TRANSLATE_SYSTEM),
                LLMMessage(role="user", content=text),
            ],
            max_output_tokens=300,
            temperature=0.0,
        )
    except Exception as exc:  # noqa: BLE001 — degrade, don't fail the turn
        log.warning("audio.translate transport failure user=%s: %s", user_id, exc)
        return text
    if response.finish_reason == "error" or not (response.content or "").strip():
        log.warning(
            "audio.translate model error user=%s finish=%s",
            user_id,
            response.finish_reason,
        )
        return text
    return response.content.strip()


async def _openai_whisper(
    data: bytes, filename: str, content_type: str, mode: str, user_id: int
) -> str:
    endpoint = "translations" if mode == "translate" else "transcriptions"
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{_OPENAI_AUDIO_BASE}/{endpoint}",
                headers={"Authorization": f"Bearer {settings.openai_api_key}"},
                data={"model": _WHISPER_MODEL, "response_format": "json"},
                files={"file": (filename, data, content_type or "audio/webm")},
            )
    except httpx.HTTPError as exc:
        log.warning("audio.whisper network failure user=%s: %s", user_id, exc)
        raise http_error(
            502, "upstream_error", "speech service unreachable — try again"
        ) from exc
    if resp.status_code != 200:
        log.warning(
            "audio.whisper upstream %s user=%s body=%s",
            resp.status_code,
            user_id,
            resp.text[:300],
        )
        raise http_error(
            502, "upstream_error", "speech service rejected the audio — try again"
        )
    return str(resp.json().get("text", "")).strip()


@router.post(
    "/transcribe",
    response_model=TranscriptionResult,
    dependencies=[Depends(rate_limit("audio", 20, 60))],
)
async def transcribe_audio(
    file: UploadFile = File(...),
    mode: str = Query(default="translate", pattern="^(translate|transcribe)$"),
    user_id: int = Depends(require_user),
) -> TranscriptionResult:
    content_type = (file.content_type or "").lower()
    if content_type and not content_type.startswith(_ACCEPTED_PREFIXES):
        raise validation_error(f"unsupported audio content type: {content_type}")

    data = await file.read()
    if not data:
        raise validation_error("empty audio upload")
    if len(data) > _MAX_UPLOAD_BYTES:
        raise validation_error("audio upload too large (max 15 MB)")

    filename = file.filename or "recording.webm"

    if settings.azure_key and _speech_endpoint():
        text = await _azure_fast_transcribe(data, filename, content_type, user_id)
        provider = "azure-speech"
        if mode == "translate" and text and _DEVANAGARI_RE.search(text):
            translated = await _llm_translate_to_english(text, user_id)
            if translated != text:
                provider = "azure-speech+llm"
            text = translated
    elif settings.openai_api_key:
        text = await _openai_whisper(data, filename, content_type, mode, user_id)
        provider = "openai-whisper-1"
    else:
        raise not_yet_available(
            "voice input is not configured on this server (no speech credentials)"
        )

    log.info(
        "audio.transcribe ok user=%s mode=%s provider=%s bytes=%d chars=%d",
        user_id,
        mode,
        provider,
        len(data),
        len(text),
    )
    return TranscriptionResult(text=text, mode=mode, provider=provider)
