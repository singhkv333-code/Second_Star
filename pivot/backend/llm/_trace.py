"""LLM call tracer + cost ledger hook.

This module wears two hats:

  1. **Trace file (opt-in)** — gated by env var ``PIVOT_LLM_TRACE``.
     When set to a writable path, every call through ``LLMOpenAI.complete``
     and ``stream_openai`` emits a verbose JSONL record into that file
     (raw prompts and responses included — dev-only, PII risk).

  2. **Cost ledger (always on)** — independently of the trace file,
     every ``CallTrace`` close fires
     ``backend.services.llm_cost.record_llm_usage`` with the token
     counts, latency, model, provider, and endpoint label. That call
     writes one row to ``llm_usage`` and emits one structured
     ``event="llm.usage"`` log line. The ledger NEVER carries prompts
     or responses.

The two hats are deliberately decoupled: prod runs with the trace file
disabled but the ledger active. The ledger path swallows all exceptions
internally so cost tracking can never break an LLM call.

Original docstring (trace file format) follows:

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


_ENDPOINT_MODULE_PREFIXES: tuple[tuple[str, str], ...] = (
    # Longest-prefix first so submodules win over parent packages.
    ("backend.workflows.propose", "propose"),
    ("backend.services.chat_service", "chat"),
    ("backend.services.tool_router", "router"),
    ("backend.services.fast_path", "chat"),
    ("backend.services.cache_warmup", "warmup"),
    ("backend.services.validation_handler", "validation"),
    ("backend.agents.router", "router"),
    ("backend.agents", "agentic"),
    ("backend.routers.chat", "chat"),
)


def _infer_endpoint() -> str:
    """Walk the stack above backend.llm.* and map the closest module
    to a short endpoint label. Returns "unknown" if no match.

    This is best-effort — callers can also pass an explicit ``endpoint``
    string to ``CallTrace`` to override.
    """
    try:
        for fr in inspect.stack()[2:]:
            mod = fr.frame.f_globals.get("__name__") or ""
            if mod.startswith("backend.llm.") or mod == "backend.llm":
                continue
            if mod.startswith("backend._trace") or mod.endswith("_trace"):
                continue
            for prefix, label in _ENDPOINT_MODULE_PREFIXES:
                if mod == prefix or mod.startswith(prefix + "."):
                    return label
            # First non-llm, non-trace frame that didn't match — use it
            # but tag it generically so the row is still queryable.
            return "unknown"
    except Exception:
        pass
    return "unknown"


class CallTrace:
    """Context manager that records one ``complete`` call.

    Usage::

        with CallTrace(kind="complete", messages=msgs, tools=tooldefs,
                       model=self.model, provider="openai",
                       reasoning_effort=eff, prompt_cache_key=key,
                       max_output_tokens=mot) as t:
            response = await ... do call ...
            t.set_response(response)

    The record is flushed in ``__exit__`` whether the call succeeded or
    raised, so we always see every attempt.

    The trace file (verbose JSONL into ``PIVOT_LLM_TRACE``) is opt-in;
    the cost ledger (one ``llm_usage`` row + structured log line) is
    always on. Both share the token usage / latency / model state
    collected here, but the ledger path swallows its own exceptions so
    cost tracking can never break an LLM call.
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
        provider: str = "unknown",
        endpoint: Optional[str] = None,
    ) -> None:
        # The trace-file path is gated; the cost ledger path is not.
        # We always need t0, model, provider, endpoint, and a place to
        # stash usage. The verbose message/tools summaries only get
        # computed when the trace file is enabled (they're the
        # expensive part).
        self._enabled = is_enabled()
        self._t0 = time.monotonic()
        self._first_delta_t: Optional[float] = None
        self._kind = kind
        self._model = model
        self._provider = provider
        self._endpoint = endpoint or _infer_endpoint()
        self._reasoning_effort = reasoning_effort
        self._prompt_cache_key = prompt_cache_key
        self._max_output_tokens = max_output_tokens
        self._response_text: str = ""
        self._tool_calls: list[dict] = []
        self._usage: dict[str, Any] = {}
        self._error: Optional[str] = None
        if self._enabled:
            self._messages_summary, self._input_chars = _summarise_messages(messages)
            self._tools_count, self._tool_names = _summarise_tools(tools)
            self._caller = _caller_frame()
        else:
            # Cheap defaults — never read on the disabled path.
            self._messages_summary = []
            self._input_chars = 0
            self._tools_count = 0
            self._tool_names = []
            self._caller = ""

    def __enter__(self) -> "CallTrace":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        latency_ms_int = int((time.monotonic() - self._t0) * 1000)
        if exc_type is not None and self._error is None:
            self._error = f"{exc_type.__name__}: {exc_val}"

        if self._enabled:
            ttft_ms = (
                int((self._first_delta_t - self._t0) * 1000)
                if self._first_delta_t is not None else None
            )
            write_record({
                "kind": self._kind,
                "caller": self._caller,
                "endpoint": self._endpoint,
                "provider": self._provider,
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
                "latency_ms": latency_ms_int,
                "ttft_ms": ttft_ms,
                "error": self._error,
            })

        # --- Cost ledger: always on, exceptions swallowed internally.
        try:
            input_tokens = int(self._usage.get("input_tokens", 0) or 0)
            output_tokens = int(self._usage.get("output_tokens", 0) or 0)
            reasoning_tokens = int(self._usage.get("reasoning_tokens", 0) or 0)
            # Skip rows where the call clearly never happened (e.g. missing
            # API key short-circuit, transport error before any token was
            # exchanged). A zero-token entry would pollute the ledger
            # without representing real cost.
            if input_tokens or output_tokens or reasoning_tokens:
                # Imported lazily so the trace module stays cheap and
                # doesn't pull SQLAlchemy into the import path of every
                # client module that touches `_trace`.
                from backend.services.llm_cost import record_llm_usage
                record_llm_usage(
                    model=self._model,
                    provider=self._provider,
                    endpoint=self._endpoint,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    reasoning_tokens=reasoning_tokens,
                    latency_ms=float(latency_ms_int),
                )
        except Exception:
            # The recorder is itself defensive, but we belt-and-brace
            # here too: the LLM call MUST NOT fail because cost
            # tracking failed.
            pass

    # ── Mutators called by the LLM client ─────────────────────────────

    def mark_first_delta(self) -> None:
        if not self._enabled:
            return
        if self._first_delta_t is None:
            self._first_delta_t = time.monotonic()

    def set_response(self, response: Any) -> None:
        # Usage capture runs UNCONDITIONALLY — the cost ledger reads it
        # in __exit__. The verbose response_text / tool_calls capture is
        # gated on _enabled (it's only used by the trace file).
        self._usage = {
            "input_tokens": getattr(response, "input_tokens", 0),
            "output_tokens": getattr(response, "output_tokens", 0),
            "reasoning_tokens": getattr(response, "reasoning_tokens", 0),
            "cached_tokens": getattr(response, "cached_tokens", 0),
            "finish_reason": getattr(response, "finish_reason", None),
        }
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

    def set_stream_result(
        self,
        *,
        text: str,
        tool_calls: list[dict],
        usage: dict[str, Any],
    ) -> None:
        # As with set_response: usage is unconditional, the rest is gated.
        self._usage = usage
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
