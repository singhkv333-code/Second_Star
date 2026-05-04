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

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from pydantic import BaseModel, ValidationError

from backend.llm.base import LLMClient, ToolDef
from backend.services.completeness import (
    MissingField,
    check_completeness,
)
from backend.services.tool_registry import ToolResult, execute


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
    # Per-step latency for the tool's own pre-flight + execution.
    # Surfaces in chat_service's latency_breakdown.
    latency_ms: int = 0

    @classmethod
    def from_tool_result(cls, name: str, args: dict[str, Any], r: ToolResult) -> "GuardedToolResult":
        return cls(
            name=name, args=args,
            success=r.success,
            data=r.data or {},
            logiccard=r.logiccard,
            error=r.error,
        )


# ── Schema-driven completeness gate ────────────────────────────────


import re as _re

# Field-name → user-friendly label. Used when the schema's `description`
# is too schema-explainer-y to leak into a chat reply (e.g. propose_workflow's
# `name` field carries "Short workflow title (e.g. 'Weekly NIFTYBEES buy')."
# which reads as gibberish in a clarification question).
_PRETTY_FIELD_NAMES: dict[str, str] = {
    "symbol": "stock or ETF",
    "quantity": "number of shares",
    "qty": "number of shares",
    "price": "price",
    "limit_price": "limit price",
    "trigger_price": "stop-loss trigger price",
    "name": "name for the agent",
    "steps": "exact steps",
    "side": "buy or sell",
    "exchange": "exchange (NSE / BSE)",
    "user_intent": "what you want the agent to do",
}


# Strip "(e.g. ...)" / "(matches ...)" / parentheticals that read as
# schema-explainer prose in a clarification reply.
_PAREN_NOISE_RE = _re.compile(r"\s*\([^)]*\)\s*$")


def _humanize_description(m: MissingField) -> str:
    """Best-effort short, conversational name for a missing field.

    Priority:
      1. Hand-curated alias for well-known fields (symbol, quantity, …).
      2. Schema description, with parenthetical examples stripped.
      3. Field name with underscores → spaces.
    """
    alias = _PRETTY_FIELD_NAMES.get(m.field_name)
    if alias:
        return alias
    desc = (m.description or "").strip()
    if desc:
        # Drop trailing parentheticals ("(e.g. 'Weekly NIFTYBEES buy')").
        desc = _PAREN_NOISE_RE.sub("", desc).rstrip(".")
        # Drop leading "Short " / "The " / "A " article-y noise.
        for prefix in ("Short ", "The ", "A "):
            if desc.startswith(prefix):
                desc = desc[len(prefix):]
                break
        # If the description is still very long or schema-explainer, fall
        # back to the field name.
        if len(desc) <= 60 and "MUST" not in desc and "registry" not in desc:
            return desc
    return m.field_name.replace("_", " ").lower()


def _format_clarification_question(missing: list[MissingField]) -> str:
    """Deterministic, template-based clarification question.

    Replaces a minimal-reasoning LLM call (~1s, ~500 tokens) that did
    nothing the schema couldn't tell us. Stitches MissingField records
    into a friendly sentence in microseconds.

    Special-cased for the most common shapes:
      - one missing field          → "Got it — what's the {pretty}?"
      - two missing fields         → "I need {a} and {b}."
      - three or more              → bulleted list.
      - structural fields (e.g.    → fall back to a generic "could you
        propose_workflow.steps)      describe it more concretely?" so
                                     we never leak internal schema text.
    """
    if not missing:
        return "Could you give me a bit more detail?"

    # Detect the "model couldn't even fill the required structural
    # fields" case (propose_workflow with no name + no steps). The
    # completeness machinery is technically correct but the only useful
    # thing to say is "describe it more concretely".
    structural = {"name", "steps"}
    field_names = {m.field_name for m in missing}
    if field_names <= structural and "steps" in field_names:
        return (
            "I couldn't quite map that into a workflow. Could you "
            "describe it a bit more concretely — what should trigger "
            "the action, and what action should run?"
        )

    if len(missing) == 1:
        m = missing[0]
        suffix = f" ({m.type_hint})" if m.type_hint and m.type_hint != "value" else ""
        return f"Got it — what's the {_humanize_description(m)}?{suffix}"

    if len(missing) == 2:
        a, b = missing[0], missing[1]
        return (
            f"To do that I need two things: the "
            f"{_humanize_description(a)} and the {_humanize_description(b)}."
        )

    bullets = "\n".join(
        f"  • {_humanize_description(m)}"
        + (f" — {m.type_hint}" if m.type_hint and m.type_hint != "value" else "")
        for m in missing
    )
    return f"I'm missing a few things — could you share:\n{bullets}"


def _fallback_question(missing: list[MissingField]) -> str:
    """Back-compat shim for any remaining caller. Same behaviour as
    `_format_clarification_question` — kept under the old name in case
    a downstream module still imports it."""
    return _format_clarification_question(missing)


async def execute_with_completeness(
    tool_name: str,
    args: dict[str, Any],
    *,
    llm_client: LLMClient,
    user_message: str,
    kite_token: str,
    db: Any,
    user_id: int,
) -> GuardedToolResult:
    """Schema-first tool execution.

    Order of operations (each layer cheaper than the next):
      1. Pure-Python completeness check on required fields.
      2. JSON-Schema arg validator (type / enum / minLength).
      3. Tool execution.

    Each layer short-circuits on failure — so a missing field
    *never* triggers a Pydantic call, an Pydantic-violating arg
    never reaches the executor.

    The completeness branch returns `needs_clarification=True` with
    a one-question prompt the chat surfaces as the assistant reply.
    No LLM retry hop here — the agentic loop already gives the
    model another chance to call a different tool on its next
    iteration if the user comes back with more info.
    """
    started = time.monotonic()

    # ASK_USER intercept (synthetic tool — no executor; also no
    # completeness check beyond the question-non-empty rule baked
    # into its schema).
    if tool_name == ASK_USER_TOOL_NAME:
        ask_schema = ask_user_tool_def().parameters
        err = _validate_args_against_schema(args, ask_schema)
        if err is not None:
            return GuardedToolResult(
                name=tool_name, args=args, error=err,
                latency_ms=int((time.monotonic() - started) * 1000),
            )
        return GuardedToolResult(
            name=tool_name, args=args,
            needs_clarification=True,
            question=(args.get("question") or "").strip(),
            latency_ms=int((time.monotonic() - started) * 1000),
        )

    # Look up the schema for the chosen tool.
    schema = _schema_for_tool(tool_name)
    description = _description_for_tool(tool_name) or ""

    # 1. Completeness check (pure Python, microseconds).
    if isinstance(schema, dict):
        report = check_completeness(tool_name, schema, args)
        if not report.is_complete:
            # propose_workflow is special: its required fields (`name`,
            # `steps`) are structural — when they're missing it's
            # almost always a model failure (the model emitted an
            # empty function call), NOT a real "user hasn't said".
            # Feed the validation error back into the agentic loop so
            # the model gets another iteration to emit a real draft,
            # instead of bouncing the user with a clarification.
            if tool_name == "propose_workflow":
                return GuardedToolResult(
                    name=tool_name, args=args,
                    error=(
                        f"Missing required fields: "
                        f"{', '.join(m.field_name for m in report.missing)}. "
                        "Re-emit the propose_workflow call with the full "
                        "draft as arguments — name, steps[] (with at "
                        "least one trigger.* at index 0), and rationale. "
                        "Do NOT call ASK_USER for these — fill in "
                        "sensible defaults and emit the draft."
                    ),
                    latency_ms=int((time.monotonic() - started) * 1000),
                )
            # Deterministic phrasing — used to be an LLM call (~1s,
            # ~500 tokens) but the schema's description + type_hint
            # already gives us everything we need to write a one-line
            # question. Removed 2026-05-04 per the LLM-call audit.
            question = _format_clarification_question(report.missing)
            return GuardedToolResult(
                name=tool_name, args=args,
                needs_clarification=True,
                question=question,
                latency_ms=int((time.monotonic() - started) * 1000),
            )

    # 2. JSON-Schema arg validation (type / enum / length).
    err = _validate_args_against_schema(args, schema) if schema else None
    if err is not None:
        return GuardedToolResult(
            name=tool_name, args=args, error=err,
            latency_ms=int((time.monotonic() - started) * 1000),
        )

    # 3. Execute.
    result = await execute(
        tool_name, args, kite_token=kite_token, db=db, user_id=user_id,
    )
    out = GuardedToolResult.from_tool_result(tool_name, args, result)
    out.latency_ms = int((time.monotonic() - started) * 1000)
    return out


def _description_for_tool(tool_name: str) -> Optional[str]:
    from backend.agents.tools import ALL_TOOLS
    defn = ALL_TOOLS.get(tool_name)
    if not defn:
        return None
    return ((defn.get("function") or {}).get("description"))


# ── The retry loop ──────────────────────────────────────────────────


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
