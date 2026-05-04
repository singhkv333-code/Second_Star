"""Temporary LLM call tracer.

Gated by env var ``PIVOT_LLM_TRACE``. When set to a writable path,
every call through ``LLMOpenAI.complete`` and ``stream_openai`` emits
one JSONL record into that file with:

  - ts                 ISO-8601 timestamp
  - caller             "<file>:<line> <function>" of the closest
                       application stack frame above backend.llm.*
  - kind               "complete" | "stream"
  - model
  - reasoning_effort
  - prompt_cache_key
  - tools_count
  - tool_names         (sorted, capped at 30)
  - max_output_tokens
  - input_messages     verbatim list of {role, content_chars, content_preview}
  - input_chars_total
  - response_text      full assistant text
  - tool_calls         list of {name, arguments_chars, arguments_preview}
  - usage              {input, cached, output, reasoning, finish_reason}
  - latency_ms         wall time on the API call (or first-byte for streams)
  - ttft_ms            time to first delta (stream only)

Designed to be cheap when disabled (one env-var check + early return).
Records are flushed per call so a Ctrl-C still leaves a complete file.

Remove or gate on PROD-safe before merging — the file lands user prompts
verbatim, which can include PII / order parameters.
"""
from __future__ import annotations

import inspect
import json
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any, Optional


_LOCK = threading.Lock()
_PATH: Optional[str] = None
_PATH_RESOLVED = False


def _resolve_path() -> Optional[str]:
    global _PATH, _PATH_RESOLVED
    if _PATH_RESOLVED:
        return _PATH
    raw = os.environ.get("PIVOT_LLM_TRACE", "").strip()
    _PATH = raw if raw else None
    _PATH_RESOLVED = True
    return _PATH


def is_enabled() -> bool:
    return _resolve_path() is not None


def _caller_frame() -> str:
    """Closest stack frame above backend.llm.* — the chat hop, the
    clarification call, the workflow plan call, etc."""
    try:
        for fr in inspect.stack()[2:]:
            mod = fr.frame.f_globals.get("__name__") or ""
            if mod.startswith("backend.llm.") or mod == "backend.llm":
                continue
            if mod.startswith("backend._trace") or mod.endswith("_trace"):
                continue
            return f"{mod}:{fr.lineno} {fr.function}"
    except Exception:
        pass
    return "<unknown>"


def _preview(s: str, n: int = 240) -> str:
    if not s:
        return ""
    s = s.replace("\n", " ⏎ ")
    return s if len(s) <= n else s[:n] + f"…(+{len(s) - n})"


def _summarise_messages(messages: list[Any]) -> tuple[list[dict], int]:
    out: list[dict] = []
    total_chars = 0
    for m in messages:
        # `messages` are LLMMessage instances — but stay defensive.
        role = getattr(m, "role", None) or (m.get("role") if isinstance(m, dict) else "?")
        content = getattr(m, "content", None) or (m.get("content") if isinstance(m, dict) else "") or ""
        tool_calls = getattr(m, "tool_calls", None) or (m.get("tool_calls") if isinstance(m, dict) else None)
        tool_call_id = getattr(m, "tool_call_id", None) or (m.get("tool_call_id") if isinstance(m, dict) else None)
        chars = len(content)
        total_chars += chars
        rec: dict[str, Any] = {
            "role": role,
            "content_chars": chars,
            "content_preview": _preview(content, 320),
        }
        if tool_calls:
            rec["tool_calls"] = [
                {
                    "name": (tc.get("name") if isinstance(tc, dict) else None),
                    "arguments_preview": _preview(
                        json.dumps(tc.get("arguments")) if isinstance(tc, dict) else str(tc),
                        200,
                    ),
                }
                for tc in (tool_calls or [])
            ]
        if tool_call_id:
            rec["tool_call_id"] = tool_call_id
        out.append(rec)
    return out, total_chars


def _summarise_tools(tools: Optional[list[Any]]) -> tuple[int, list[str]]:
    if not tools:
        return 0, []
    names: list[str] = []
    for t in tools:
        name = getattr(t, "name", None) or (t.get("name") if isinstance(t, dict) else None)
        if name:
            names.append(name)
    return len(tools), sorted(names)[:30]


def write_record(record: dict[str, Any]) -> None:
    path = _resolve_path()
    if not path:
        return
    record.setdefault(
        "ts",
        datetime.now(tz=timezone.utc).isoformat(timespec="milliseconds"),
    )
    record.setdefault("caller", _caller_frame())
    line = json.dumps(record, default=str, ensure_ascii=False)
    try:
        with _LOCK:
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
    except Exception:
        # Tracing must NEVER break the request path.
        pass


class CallTrace:
    """Context manager that records one ``complete`` call.

    Usage::

        with CallTrace(kind="complete", messages=msgs, tools=tooldefs,
                       model=self.model, reasoning_effort=eff,
                       prompt_cache_key=key, max_output_tokens=mot) as t:
            response = await ... do call ...
            t.set_response(response)

    The record is flushed in ``__exit__`` whether the call succeeded or
    raised, so we always see every attempt.
    """

    def __init__(
        self,
        *,
        kind: str,
        messages: list[Any],
        tools: Optional[list[Any]],
        model: str,
        reasoning_effort: Optional[str],
        prompt_cache_key: Optional[str],
        max_output_tokens: int,
    ) -> None:
        self._enabled = is_enabled()
        if not self._enabled:
            return
        self._t0 = time.monotonic()
        self._first_delta_t: Optional[float] = None
        self._kind = kind
        self._model = model
        self._reasoning_effort = reasoning_effort
        self._prompt_cache_key = prompt_cache_key
        self._max_output_tokens = max_output_tokens
        self._messages_summary, self._input_chars = _summarise_messages(messages)
        self._tools_count, self._tool_names = _summarise_tools(tools)
        self._response_text: str = ""
        self._tool_calls: list[dict] = []
        self._usage: dict[str, Any] = {}
        self._error: Optional[str] = None
        self._caller = _caller_frame()

    def __enter__(self) -> "CallTrace":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if not self._enabled:
            return
        latency_ms = int((time.monotonic() - self._t0) * 1000)
        ttft_ms = (
            int((self._first_delta_t - self._t0) * 1000)
            if self._first_delta_t is not None else None
        )
        if exc_type is not None:
            self._error = f"{exc_type.__name__}: {exc_val}"
        write_record({
            "kind": self._kind,
            "caller": self._caller,
            "model": self._model,
            "reasoning_effort": self._reasoning_effort,
            "prompt_cache_key": self._prompt_cache_key,
            "max_output_tokens": self._max_output_tokens,
            "tools_count": self._tools_count,
            "tool_names": self._tool_names,
            "input_messages": self._messages_summary,
            "input_chars_total": self._input_chars,
            "response_text": _preview(self._response_text, 4000),
            "response_chars": len(self._response_text),
            "tool_calls": self._tool_calls,
            "usage": self._usage,
            "latency_ms": latency_ms,
            "ttft_ms": ttft_ms,
            "error": self._error,
        })

    # ── Mutators called by the LLM client ─────────────────────────────

    def mark_first_delta(self) -> None:
        if not self._enabled:
            return
        if self._first_delta_t is None:
            self._first_delta_t = time.monotonic()

    def set_response(self, response: Any) -> None:
        if not self._enabled:
            return
        self._response_text = (getattr(response, "content", None) or "")
        for tc in (getattr(response, "tool_calls", None) or []):
            self._tool_calls.append({
                "name": tc.get("name"),
                "id": tc.get("id"),
                "arguments_preview": _preview(
                    json.dumps(tc.get("arguments"), default=str),
                    400,
                ),
                "arguments_chars": len(json.dumps(tc.get("arguments"), default=str)),
            })
        self._usage = {
            "input_tokens": getattr(response, "input_tokens", 0),
            "output_tokens": getattr(response, "output_tokens", 0),
            "reasoning_tokens": getattr(response, "reasoning_tokens", 0),
            "cached_tokens": getattr(response, "cached_tokens", 0),
            "finish_reason": getattr(response, "finish_reason", None),
        }

    def set_stream_result(
        self,
        *,
        text: str,
        tool_calls: list[dict],
        usage: dict[str, Any],
    ) -> None:
        if not self._enabled:
            return
        self._response_text = text
        for tc in tool_calls:
            self._tool_calls.append({
                "name": tc.get("name"),
                "id": tc.get("id"),
                "arguments_preview": _preview(
                    json.dumps(tc.get("arguments"), default=str),
                    400,
                ),
                "arguments_chars": len(json.dumps(tc.get("arguments"), default=str)),
            })
        self._usage = usage
