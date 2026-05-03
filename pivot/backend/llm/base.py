"""Provider-agnostic LLM contract.

Every Pivot LLM call goes through `LLMClient.complete(...)` with the
same input/output shape regardless of which provider is wired. The
abstraction was added when we moved off Sarvam-m as the primary chat
backend; before this, calls were scattered across two clients with
slightly different signatures (Sarvam's call_sarvam returned a dict,
the legacy call_openai returned a string), which made swapping
providers a multi-file refactor every time.

What's intentional in this contract:

  - Tool calls are returned as a list of dicts (not provider-specific
    objects) so callers can iterate without isinstance checks. Each
    dict has ``id``, ``name``, ``arguments`` (already JSON-decoded).
  - Token usage is surfaced separately for input / output / reasoning
    so we can attribute cost on reasoning models (GPT-5 mini's
    <think> tokens are billed but not visible in `content`).
  - ``raw`` carries the full provider response for debugging — never
    rely on its shape from caller code.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


FinishReason = Literal["stop", "tool_calls", "length", "error", "needs_clarification"]
"""
- stop: model finished its reply normally
- tool_calls: model wants us to execute one or more tools
- length: hit max_output_tokens before finishing
- error: provider returned an error or response was malformed
- needs_clarification: synthetic — model called the ASK_USER tool
"""


class LLMMessage(BaseModel):
    """One conversation turn from any side of the dialogue.

    `role` is one of: 'system', 'user', 'assistant', 'tool'.
    A 'tool' message is the output we feed back after executing a tool
    call; `tool_call_id` MUST match the id we got from the model's
    tool_calls.
    """
    model_config = ConfigDict(extra="forbid")

    role: Literal["system", "user", "assistant", "tool"]
    content: str = ""
    # Set on assistant messages that emitted tool calls.
    tool_calls: Optional[list[dict[str, Any]]] = None
    # Set on tool-result messages. Matches a tool_call's `id`.
    tool_call_id: Optional[str] = None
    # Set on tool-result messages. The tool's name (provider needs it).
    name: Optional[str] = None


class ToolDef(BaseModel):
    """Single tool definition exposed to the model. Matches OpenAI's
    function-tool shape (name, description, parameters JSON Schema)
    plus an optional `strict` flag for structured-output enforcement.
    """
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    strict: bool = False


class LLMResponse(BaseModel):
    """Provider-agnostic response."""
    model_config = ConfigDict(extra="forbid")

    content: Optional[str] = None
    """The model's user-visible text. May be None when only tool_calls
    are returned."""

    tool_calls: Optional[list[dict[str, Any]]] = None
    """Each item: {id: str, name: str, arguments: dict[str, Any]}.
    Arguments are already JSON-decoded — provider raw payloads put
    them as a JSON string."""

    finish_reason: FinishReason = "stop"

    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    """Reasoning tokens are billable on GPT-5 / o-series models even
    though they're not in `content`. 0 for non-reasoning providers."""

    latency_ms: int = 0
    model: str = ""
    raw: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------
# Reasoning effort — supported on OpenAI's reasoning models, ignored
# elsewhere. We default to "medium" for chat / propose hops because
# Pivot's planning needs are thin compared to e.g. coding agents, and
# every token of <think> is paid for.
# ---------------------------------------------------------------------

ReasoningEffort = Literal["minimal", "low", "medium", "high"]


class LLMClient(ABC):
    """The single abstraction every Pivot LLM call goes through."""

    provider_name: str = "abstract"
    model: str = ""

    @abstractmethod
    async def complete(
        self,
        messages: list[LLMMessage],
        *,
        tools: Optional[list[ToolDef]] = None,
        tool_choice: Literal["auto", "required", "none"] = "auto",
        max_output_tokens: int = 1500,
        reasoning_effort: Optional[ReasoningEffort] = None,
        temperature: float = 0.2,
        response_format: Optional[Literal["json_object"]] = None,
    ) -> LLMResponse:
        """Run a single completion.

        Implementation contract:
          - Never raise on a 4xx/5xx — always return an LLMResponse with
            finish_reason='error' and the error in content. (Network/
            transport errors still raise; those are caller-recoverable.)
          - When tools are given, return tool_calls populated only if
            the model actually asked to call one. Don't fabricate.
          - reasoning_effort is silently ignored on non-reasoning models.
          - response_format='json_object' must coerce the provider into
            JSON-shaped output. Sarvam supports this; OpenAI Responses
            API does too via response_format.
        """
        ...
