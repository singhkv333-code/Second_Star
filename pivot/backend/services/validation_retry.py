"""Validate-and-retry loop for tool execution.

When the LLM emits a tool call, today's chat path either crashes on
malformed args or runs the tool with garbage. That makes the model
*look* worse than it is — given the validation error, it would fix
its own output most of the time.

This module wraps the tool dispatcher in a single-retry loop:
  1. Validate the model's args against the tool's input schema.
  2. On failure, send a terse error summary back to the model and let
     it fix the args (or call ASK_USER to escalate to the human).
  3. On second failure, surface a structured error — never run the
     tool with bad args.

The synthetic ASK_USER tool exists in the schema (so the model can
call it) but is intercepted here rather than dispatched to the
executor. ASK_USER returns a `needs_clarification` ToolResult that
chat_service surfaces to the user as a normal assistant message.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from pydantic import BaseModel, ValidationError

from backend.llm.base import LLMClient, LLMMessage, LLMResponse, ToolDef
from backend.services.tool_registry import ToolResult, execute, get_tool_schema


logger = logging.getLogger(__name__)


# ── Synthetic ASK_USER tool ─────────────────────────────────────────


ASK_USER_TOOL_NAME = "ASK_USER"


class AskUserArgs(BaseModel):
    question: str
    """One focused clarifying question for the user. Keep it under 200
    characters; the user will reply in the next chat turn."""


def ask_user_tool_def() -> ToolDef:
    """Tool definition the model sees alongside real tools. Calling it
    is the model's escape hatch when a required field can't be
    extracted from the conversation.

    The `question` field has minLength=5 so the model can't satisfy
    the call by passing an empty string — that path was triggering
    a "validation error" surface to the user, which made the model
    look broken on perfectly reasonable prompts.
    """
    return ToolDef(
        name=ASK_USER_TOOL_NAME,
        description=(
            "Call this when you need a single piece of information from the "
            "user that you can't infer from the conversation (e.g. a missing "
            "price threshold, quantity, or specific stock). Pass exactly one "
            "focused question containing real text — do NOT pass an empty "
            "string. Do not call this for greetings, definitions, or topics "
            "where you can answer directly."
        ),
        parameters={
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "The single question to ask the user. "
                                   "Must be a complete sentence, at least 5 characters.",
                    "minLength": 5,
                },
            },
            "required": ["question"],
        },
    )


# ── Validation error formatting ─────────────────────────────────────


def format_validation_errors_terse(e: ValidationError) -> str:
    """One-line-per-error summary for feeding back to the LLM.

    Pydantic v2's `.errors()` returns dicts like:
      {'type': 'missing', 'loc': ('quantity',), 'msg': 'Field required', ...}

    Output examples:
      'quantity: Field required.'
      'dip_threshold_pct: Input should be > 0; got -1.'
      'symbol: String should have at least 1 character.'

    Used in the retry hop to give the model a precise, structured fix
    target — the better the error, the more likely the retry succeeds.
    """
    lines: list[str] = []
    for err in e.errors():
        loc = ".".join(str(p) for p in err.get("loc", ())) or "(root)"
        msg = err.get("msg", "invalid")
        # Some pydantic errors include the bad input under 'input' —
        # surface it inline when it's a primitive.
        bad = err.get("input")
        if isinstance(bad, (str, int, float, bool)) or bad is None:
            lines.append(f"{loc}: {msg}; got {bad!r}.")
        else:
            lines.append(f"{loc}: {msg}.")
    return " ".join(lines) if lines else "validation failed (no details)"


# ── Tool input schema lookup ────────────────────────────────────────
#
# Today's tools.py builds OpenAI-style function defs with a JSON Schema
# in `parameters` but no Pydantic model attached. We can still validate
# args against the JSON Schema using jsonschema, OR we can convert to
# a dynamic Pydantic model. For now we use a lightweight check: ensure
# every required field is present and has the declared type. Real
# Pydantic validation comes when we migrate tools.py to typed models
# (Prompt 2 work).


def _validate_args_against_schema(
    args: dict[str, Any],
    schema: dict[str, Any],
) -> Optional[str]:
    """Return None on success; an error summary string on failure.

    Lightweight checks (JSON Schema is overkill for this round):
      - required fields present
      - declared `type` matches Python type for primitive types
      - enum membership when present
    """
    if not isinstance(schema, dict):
        return None
    props = schema.get("properties") or {}
    required = schema.get("required") or []
    errors: list[str] = []

    for field in required:
        if field not in args:
            errors.append(f"{field}: required field missing.")

    type_map = {
        "string": str,
        "integer": int,
        "number": (int, float),
        "boolean": bool,
        "array": list,
        "object": dict,
    }
    for field, value in args.items():
        prop = props.get(field) or {}
        declared = prop.get("type")
        if declared in type_map:
            expected = type_map[declared]
            if not isinstance(value, expected):
                errors.append(
                    f"{field}: expected {declared}, got {type(value).__name__}."
                )
        if "enum" in prop and value not in prop["enum"]:
            errors.append(
                f"{field}: must be one of {prop['enum']}; got {value!r}."
            )
        # String length constraint — caught the "ASK_USER with empty
        # question" model behaviour where it called the tool but
        # didn't actually fill the field.
        if declared == "string" and isinstance(value, str):
            min_len = prop.get("minLength")
            if min_len and len(value.strip()) < int(min_len):
                errors.append(
                    f"{field}: minimum length {min_len}; got {len(value.strip())} chars."
                )
            max_len = prop.get("maxLength")
            if max_len and len(value) > int(max_len):
                errors.append(
                    f"{field}: maximum length {max_len}; got {len(value)} chars."
                )

    return " ".join(errors) if errors else None


# ── Result type ─────────────────────────────────────────────────────


@dataclass
class GuardedToolResult:
    """Outcome of execute_tool_with_retry. Either a successful tool
    run, a clarification request, or a validation failure."""
    name: str
    args: dict[str, Any]
    success: bool = False
    needs_clarification: bool = False
    question: Optional[str] = None
    data: dict[str, Any] = field(default_factory=dict)
    logiccard: Optional[dict[str, Any]] = None
    error: Optional[str] = None

    @classmethod
    def from_tool_result(cls, name: str, args: dict[str, Any], r: ToolResult) -> "GuardedToolResult":
        return cls(
            name=name, args=args,
            success=r.success,
            data=r.data or {},
            logiccard=r.logiccard,
            error=r.error,
        )


# ── The retry loop ──────────────────────────────────────────────────


async def execute_tool_with_retry(
    tool_name: str,
    args: dict[str, Any],
    *,
    llm_client: LLMClient,
    conversation_messages: list[LLMMessage],
    tools_for_retry: list[ToolDef],
    kite_token: str,
    db: Any,
    user_id: int,
    max_retries: int = 1,
) -> GuardedToolResult:
    """Execute `tool_name(args)` with one validation-retry hop.

    If the args fail validation:
      1. Send a structured tool-message back to the LLM (with the
         validation error and the original tool schema in scope).
      2. Take whichever tool the LLM picks next:
         - ASK_USER → return needs_clarification.
         - same tool again → recurse with max_retries - 1.
         - different tool → recurse with the new name.
         - no tool call → surface the error as-is.
      3. After max_retries the loop ends and we surface the validation
         error to the user (we don't run with garbage args).

    `tools_for_retry` should include the synthetic ASK_USER tool so the
    model has the escape hatch.
    """
    # Look up schema. ASK_USER's schema lives on the tool def passed
    # to the LLM, not in the registry — fetch it directly.
    if tool_name == ASK_USER_TOOL_NAME:
        schema = ask_user_tool_def().parameters
    else:
        schema = _schema_for_tool(tool_name)

    err = _validate_args_against_schema(args, schema) if schema else None

    # Intercept ASK_USER only AFTER schema validation, so an empty-
    # question call goes through the LLM fix-it hop instead of dying.
    if err is None and tool_name == ASK_USER_TOOL_NAME:
        question = (args.get("question") or "").strip()
        return GuardedToolResult(
            name=tool_name, args=args,
            needs_clarification=True,
            question=question,
        )

    if err is None:
        # Args look fine — execute and return.
        result = await execute(
            tool_name, args, kite_token=kite_token, db=db, user_id=user_id,
        )
        return GuardedToolResult.from_tool_result(tool_name, args, result)

    # Validation failed.
    if max_retries <= 0:
        return GuardedToolResult(
            name=tool_name, args=args,
            error=f"Could not produce valid arguments after retry: {err}",
        )

    logger.info("tool %s args invalid (%s); requesting fix from LLM", tool_name, err)

    # Build the fix-it conversation: original messages + the assistant's
    # bad tool call + a tool-result message saying validation failed.
    fix_messages = list(conversation_messages) + [
        LLMMessage(
            role="assistant",
            content="",
            tool_calls=[{
                "id": "fix_attempt",
                "name": tool_name,
                "arguments": args,
            }],
        ),
        LLMMessage(
            role="tool",
            tool_call_id="fix_attempt",
            name=tool_name,
            content=f"VALIDATION_FAILED: {err}\nFix the arguments or call ASK_USER.",
        ),
    ]

    response = await llm_client.complete(
        messages=fix_messages,
        tools=tools_for_retry,
        tool_choice="auto",
        max_output_tokens=900,
    )

    if response.finish_reason == "error":
        return GuardedToolResult(
            name=tool_name, args=args,
            error=f"LLM error during retry: {response.content}",
        )

    if not response.tool_calls:
        return GuardedToolResult(
            name=tool_name, args=args,
            error=f"LLM did not produce a tool call on retry. Validation error was: {err}",
        )

    next_call = response.tool_calls[0]
    return await execute_tool_with_retry(
        next_call["name"],
        next_call["arguments"] or {},
        llm_client=llm_client,
        conversation_messages=conversation_messages,
        tools_for_retry=tools_for_retry,
        kite_token=kite_token, db=db, user_id=user_id,
        max_retries=max_retries - 1,
    )


# ── Internal helpers ────────────────────────────────────────────────


def _schema_for_tool(tool_name: str) -> Optional[dict[str, Any]]:
    """Return the JSON-Schema parameters block for a given tool, or
    None if the tool isn't in the registry. The tool catalog itself
    lives in agents/tools.py (ALL_TOOLS dict)."""
    from backend.agents.tools import ALL_TOOLS
    defn = ALL_TOOLS.get(tool_name)
    if not defn:
        return None
    return ((defn.get("function") or {}).get("parameters")) or None
