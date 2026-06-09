"""Schema-driven validation handler for tool execution.

This module is single-shot. There is no LLM retry loop here, and the
chat layer no longer iterates against the model on validation failure
either (Change 1 — see chat_service.py docstring).

Order of operations per call (each layer faster than the next):
  1. ASK_USER intercept (synthetic tool — produces a clarification).
  2. Pure-Python completeness check on required fields.
  3. JSON-Schema arg validation (type / enum / length).
  4. Tool execution.

Any layer's failure short-circuits to a `GuardedToolResult` with
either `needs_clarification=True` (with a deterministic question) or
`error=<terse string>`. The chat handler converts errors into a
deterministic question and surfaces them to the user — there is no
fix-it hop against the model.

The synthetic ASK_USER tool exists in the schema (so the model can
call it) but is intercepted here rather than dispatched to the
executor. ASK_USER returns a `needs_clarification` ToolResult that
chat_service surfaces to the user as a normal assistant message.

Module renamed from `validation_retry.py` 2026-05-04 to reflect that
it no longer retries.
"""
from __future__ import annotations

import logging
import re
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

    default_on_yes: Optional[str] = None
    """R2: the value the user is most likely to accept. If supplied,
    a pure-affirmative reply ('yes', 'do it', 'go ahead') is resolved
    to this value without an LLM hop on the next turn. Use when the
    question is a yes/no confirmation ('Did you mean HDFCBANK?' →
    default_on_yes='HDFCBANK') or a single-option suggestion."""

    options: Optional[list[str]] = None
    """R2: list of structured choices the user can pick from. When
    present, the chat layer can render and route a numeric / labelled
    pick deterministically. Combine with `default_on_yes` to define
    which option a bare 'yes' should resolve to."""


def ask_user_tool_def() -> ToolDef:
    """Tool definition the model sees alongside real tools. Calling it
    is the model's escape hatch when a required field can't be
    extracted from the conversation.

    The `question` field has minLength=5 so the model can't satisfy
    the call by passing an empty string — that path was triggering
    a "validation error" surface to the user, which made the model
    look broken on perfectly reasonable prompts.

    R2: also accepts optional `default_on_yes` (str) and `options`
    (list[str]) so the chat layer can resolve the next-turn 'yes'
    deterministically instead of letting the LLM re-parse history.
    """
    return ToolDef(
        name=ASK_USER_TOOL_NAME,
        description=(
            "Call this when you need a single piece of information from the "
            "user that you can't infer from the conversation (e.g. a missing "
            "price threshold, quantity, or specific stock). Pass exactly one "
            "focused question containing real text — do NOT pass an empty "
            "string. Do not call this for greetings, definitions, or topics "
            "where you can answer directly.\n\n"
            "ALWAYS set `default_on_yes` when the question is a yes/no "
            "confirmation (single-option suggestion or disambiguation with "
            "an obvious pick) — that value is what a bare 'yes' next turn "
            "deterministically resolves to. ALSO set `options` (list of "
            "choice labels) when the question is a multi-option pick — the "
            "chat layer uses them to route a numeric / labelled answer "
            "without re-parsing prose."
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
                "default_on_yes": {
                    "type": "string",
                    "description": (
                        "The value a bare 'yes' next turn should resolve "
                        "to. Set for ANY yes/no question or single-option "
                        "suggestion. Example: question='Did you mean "
                        "HDFCBANK?' → default_on_yes='HDFCBANK'."
                    ),
                },
                "options": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Structured list of choice labels for a "
                        "multi-option pick. Omit for free-text answers."
                    ),
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

    Kept as a debugging helper — the retry hop that originally
    consumed this is gone (Change 1, 2026-05-04). Trace logs and the
    `_format_recoverable_failure_question` helper still reference it.
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
        # OpenAI's function-calling layer sometimes emits explicit
        # `null` for optional fields the model decided not to use
        # (observed on propose_dsl_workflow.exit_condition). For a
        # field that is NOT in `required`, treat null as "field
        # omitted" — the handler reads args.get(field) which would
        # have returned None anyway. Without this, a perfectly valid
        # tool call gets rejected for emitting null on an optional
        # string, which then bounces back to the LLM as a prose
        # error and produces an over-confirmation reply.
        if value is None and field not in required:
            continue
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
    # Set when needs_clarification fires because exactly one required
    # field was missing — chat_service uses this to persist a
    # PendingToolCall so the user's next reply resumes deterministically
    # without an LLM hop.
    missing_field: Optional[MissingField] = None
    # R2: when the LLM emits ASK_USER with default_on_yes / options,
    # chat_service persists a PendingResolution so the next pure-
    # affirmative reply resolves deterministically.
    default_on_yes: Optional[str] = None
    options: Optional[list[str]] = None

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

    # Type hints we don't surface — they read as schema-explainer
    # noise to a chat user ("what's the trigger condition? (object)").
    _NOISE_HINTS = {"value", "object", "list of value", "yes/no"}

    def _hint(m: MissingField) -> str:
        if not m.type_hint or m.type_hint in _NOISE_HINTS:
            return ""
        return f" ({m.type_hint})"

    if len(missing) == 1:
        m = missing[0]
        return f"Got it — what's the {_humanize_description(m)}?{_hint(m)}"

    if len(missing) == 2:
        a, b = missing[0], missing[1]
        return (
            f"To do that I need two things: the "
            f"{_humanize_description(a)} and the {_humanize_description(b)}."
        )

    bullets = "\n".join(
        f"  • {_humanize_description(m)}{('  — ' + m.type_hint) if m.type_hint and m.type_hint not in _NOISE_HINTS else ''}"
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
    qty_context: str = "",
    suppress_qty_default_check: bool = False,
    prior_dsl_draft: Optional[dict] = None,
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
        # R2: extract structured resolution fields so chat_service can
        # persist a PendingResolution.
        raw_default = args.get("default_on_yes")
        default_on_yes = (
            str(raw_default).strip()
            if isinstance(raw_default, str) and raw_default.strip()
            else None
        )
        raw_options = args.get("options")
        options = (
            [str(o).strip() for o in raw_options if isinstance(o, str) and o.strip()]
            if isinstance(raw_options, list) else None
        )
        question_text = (args.get("question") or "").strip()
        # Echo guard (2026-05-29 retail eval, reliance_pe_roe turn0): the
        # model sometimes emits ASK_USER whose `question` is a verbatim
        # restatement of the user's own message ("what's reliance's PE and
        # ROE?" -> ASK_USER(question="what's reliance's PE and ROE?")). That
        # is a degenerate clarification — the user gets their own words
        # parroted back instead of an answer. When the question carries no new
        # ask (it's substantially identical to the latest user turn), feed an
        # error back into the agentic loop so the model takes another hop and
        # answers with a real tool (fetch_fundamentals/get_live_price/...).
        if question_text and not options and not default_on_yes:
            _norm = lambda s: re.sub(r"[^a-z0-9 ]+", "", (s or "").lower()).strip()
            nq, nu = _norm(question_text), _norm(user_message)
            if nq and nu and (nq == nu or (len(nq) > 12 and nq in nu)):
                return GuardedToolResult(
                    name=tool_name, args=args,
                    error=(
                        "ASK_USER echoed the user's own message instead of "
                        "asking something new. This request is answerable "
                        "directly — do NOT call ASK_USER. Call the appropriate "
                        "read/data tool (e.g. fetch_fundamentals, get_live_price, "
                        "get_returns, screen_fundamentals) and answer. Only use "
                        "ASK_USER for a genuinely NEW question that requests "
                        "information the user has not given."
                    ),
                    latency_ms=int((time.monotonic() - started) * 1000),
                )
        return GuardedToolResult(
            name=tool_name, args=args,
            needs_clarification=True,
            question=question_text,
            default_on_yes=default_on_yes,
            options=options or None,
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
            # Surface the FIRST missing field so chat_service can persist
            # a PendingToolCall and resume deterministically when the
            # user replies. Multi-field misses still get a question, but
            # we only auto-resume on single-field cases — the others
            # need the LLM to figure out which value goes where.
            single_missing = report.missing[0] if len(report.missing) == 1 else None
            return GuardedToolResult(
                name=tool_name, args=args,
                needs_clarification=True,
                question=question,
                latency_ms=int((time.monotonic() - started) * 1000),
                missing_field=single_missing,
            )

    # 2. JSON-Schema arg validation (type / enum / length).
    err = _validate_args_against_schema(args, schema) if schema else None
    if err is not None:
        return GuardedToolResult(
            name=tool_name, args=args, error=err,
            latency_ms=int((time.monotonic() - started) * 1000),
        )

    # 3. Execute. L14: compose_multistep re-dispatches sub-steps; thread
    # kite_token / db / user_id / llm_client / user_message through its
    # args dict via __ctx keys so the orchestrator can call back into
    # validation_handler.execute_with_completeness from inside.
    if tool_name == "compose_multistep":
        args = dict(args)
        args["__kite_token"] = kite_token
        args["__db"] = db
        args["__user_id"] = user_id
        args["__llm_client"] = llm_client
        args["__user_message"] = user_message
    elif tool_name == "propose_dsl_workflow":
        # C6: thread the original user message so the DSL tool's
        # multi-symbol guard can see action tickers that live only in
        # the prompt (e.g. "buy RELIANCE, TCS and BAJAJFIN when …") and
        # route a multi-symbol order to propose_workflow instead of
        # silently dropping all but the primary ticker. Injected AFTER
        # schema validation above, so the extra key never trips the
        # validator; the handler reads it via args.get and ignores it
        # otherwise.
        args = dict(args)
        args["__user_message"] = user_message
        # P1: thread the prior DSL draft so the PATCH fast-path can mutate
        # the prior steps for a non-structural amendment instead of
        # re-translating (which silently dropped the buy/exit/sell legs).
        if isinstance(prior_dsl_draft, dict):
            args["__prior_dsl_draft"] = prior_dsl_draft
    result = await execute(
        tool_name, args, kite_token=kite_token, db=db, user_id=user_id,
    )
    out = GuardedToolResult.from_tool_result(tool_name, args, result)
    out.latency_ms = int((time.monotonic() - started) * 1000)
    # M2: post-execution no-default validator. For tools that emit a
    # draft, check whether action.place_order.quantity is a suspicious
    # default (1, 10) NOT named anywhere in the user-side conversation.
    # Convert to a structured clarification so the LLM asks instead of
    # shipping a silent default. Skip when:
    #   - the user clearly named a quantity in the CURRENT message OR
    #     earlier in the conversation. `qty_context` carries recent
    #     user turns, so a qty confirmed two turns ago ("10 shares") is
    #     NOT re-asked when the user later amends an unrelated field
    #     ("set an expiry", "bank"). [C1/C2]
    #   - `suppress_qty_default_check` is set — compose_multistep plan
    #     steps carry an LLM-authored explicit quantity that is a
    #     deliberate choice, not a silent fallback. [C9]
    if (
        not suppress_qty_default_check
        and out.success
        and isinstance(out.data, dict)
        and _draft_has_suspicious_qty_default(
            out.data, user_message or "", qty_context or "",
        )
    ):
        out.success = False
        out.needs_clarification = True
        out.question = _qty_clarification_question(out.data)
        out.data = {}

    # M2b (P4, 2026-05-29 retail eval): single-leg conditional order tools
    # carry their quantity in the LogicCard register_payload, NOT in
    # out.data["steps"], so the steps-based M2 guard above never sees them.
    # gtt_reliance ("set a GTT to buy reliance at 1200") shipped a silent
    # qty=1. Mirror dip_simple: when one of these tools lands qty==1 and the
    # user named no size anywhere, ask instead of defaulting. qty==1 ONLY —
    # 10 is a legitimately user-confirmed workflow default elsewhere (the
    # create_gtt resume test relies on qty=10 NOT being re-asked).
    elif (
        not suppress_qty_default_check
        and out.success
        and tool_name in _QTY_DEFAULTING_ORDER_TOOLS
        and isinstance(out.logiccard, dict)
        and out.logiccard.get("register_payload", {}).get("quantity") == 1
        and not _USER_QTY_PATTERNS.search(user_message or "")
        and not _USER_QTY_PATTERNS.search(qty_context or "")
    ):
        sym = out.logiccard.get("symbol") or out.logiccard.get(
            "register_payload", {}
        ).get("symbol") or "this stock"
        out.success = False
        out.needs_clarification = True
        out.question = (
            f"How many shares of {sym} should I use? (I won't default to 1 — "
            "give me a share count or a rupee budget like ₹10,000.)"
        )
        out.data = {}
        out.logiccard = None

    # P1 lost-action guardrail: if this propose_dsl_workflow turn was an
    # amendment of a prior draft that HAD order legs, but the new draft has
    # FEWER action.place_order steps, the model silently dropped an order
    # (the notify-only collapse). Unless the user explicitly asked to remove
    # the order ("just notify me", "drop the buy"), fail LOUD so the agentic
    # loop re-emits with the full draft — never ship a corrupted card under
    # the old name. The PATCH fast-path preserves steps, so this only fires
    # when re-translation was taken and dropped a leg.
    if (
        tool_name == "propose_dsl_workflow"
        and out.success
        and isinstance(prior_dsl_draft, dict)
        and isinstance(out.data, dict)
        and not _AMEND_DROP_ACTION_RE.search(user_message or "")
    ):
        prior_orders = _count_place_orders(prior_dsl_draft.get("steps"))
        new_orders = _count_place_orders(out.data.get("steps"))
        if prior_orders >= 1 and new_orders < prior_orders:
            out.success = False
            out.error = (
                f"propose_dsl_workflow dropped an action.place_order that the "
                f"prior draft had (prior={prior_orders} orders, new={new_orders}). "
                "This was a non-structural amendment — re-emit with ALL of the "
                "prior draft preserved: action_kind (e.g. 'buy_market'), quantity, "
                "exit_condition and condition from the prior draft's readback, "
                "changing ONLY the field the user asked for."
            )
            out.data = {}
    return out


# Amendment phrasings that LEGITIMATELY reduce order legs (user wants fewer
# actions) — the lost-action guardrail must NOT fire on these.
_AMEND_DROP_ACTION_RE = re.compile(
    r"\b(?:just\s+notify|only\s+notify|notify\s+only|alert\s+only)\b"
    r"|\b(?:drop|remove|delete|cancel)\s+the\s+(?:order|buy|sell|trade|exit)\b"
    r"|\bno\s+(?:order|trade|buy|sell)\b|\bdon'?t\s+(?:buy|sell|trade|place)\b",
    re.IGNORECASE,
)


def _count_place_orders(steps) -> int:
    if not isinstance(steps, list):
        return 0
    return sum(
        1 for s in steps
        if isinstance(s, dict) and s.get("step_type") == "action.place_order"
    )


# Single-leg conditional-order tools whose qty lands in the LogicCard
# register_payload (not in steps[]) — checked by the M2b guard. Scoped to
# conditional orders only (NOT place_market/limit_order) to keep blast
# radius minimal; the bug report is about GTT.
_QTY_DEFAULTING_ORDER_TOOLS = frozenset({
    "create_gtt_order", "create_sl_order", "create_oco_order",
})


_USER_QTY_PATTERNS = re.compile(
    # C3: a message that is ENTIRELY a number — the natural answer to a
    # qty ASK ("How many shares?" → "10"). Anchored ^…$ so it never
    # matches a number embedded in a longer phrase ("buy when 20 dma…").
    r"^\s*\d{1,7}\s*$"
    # Explicit share/lot/unit/quantity counts — number FIRST ("10 shares")
    r"|\b\d+\s*(?:shares?|share|qty|quantity|lots?|units?|unit)\b"
    # …and unit FIRST ("qty 10", "quantity: 10", "size 5", "lot 2"). The
    # number-first pattern above missed "qty 10" trailing a longer prompt
    # ("…with a 2% stop loss, qty 10"), so the guard wrongly re-asked size.
    r"|\b(?:qty|quantity|size|lots?|units?)\s*[:=]?\s*\d+\b"
    # "buy 10 INFY", "sell 5 TCS" — numeric immediately after action verb
    r"|\b(?:buy|buys|buying|sell|sells|selling|short|exit|place)\s+"
    r"(?:a\s+)?\d+\b"
    # Rupee budget
    r"|[₹$]\s*[\d,]+|\b(?:rs\.?|inr|usd)\s*[\d,]+\b"
    # "₹X worth" / "₹X of"
    r"|[\d,]+\s+(?:k|K)\b|\blakh|\bcrore",
    re.IGNORECASE,
)


def _draft_has_suspicious_qty_default(
    payload: dict, *texts: str,
) -> bool:
    """True when the draft contains an action.place_order with
    quantity == 1 or 10 (common LLM defaults) AND NONE of the supplied
    texts contain an explicit quantity / rupee / lot reference.

    `texts` is checked SEPARATELY (current message, then prior-turn
    context) — NOT concatenated — because the bare-integer pattern is
    ^…$ anchored: a bare "10" reply must match on its own, and gluing
    it to the conversation context ("10 buy reliance when 20 dma…")
    would break the anchor and wrongly re-ask the quantity. [C3 regression]
    """
    if not isinstance(payload, dict):
        return False
    steps = payload.get("steps") or []
    if not isinstance(steps, list):
        return False
    for t in texts:
        if t and _USER_QTY_PATTERNS.search(t):
            return False
    for s in steps:
        if not isinstance(s, dict):
            continue
        if s.get("step_type") != "action.place_order":
            continue
        cfg = s.get("config") or {}
        qty = cfg.get("quantity")
        # Mustache refs are runtime-resolved; not a default. Skip.
        if isinstance(qty, str):
            continue
        try:
            qty_int = int(qty) if qty is not None else None
        except (TypeError, ValueError):
            continue
        # Conservative: only flag the canonical default values.
        # qty=5 or qty=15 etc. are unlikely to be silent defaults.
        if qty_int in (1, 10):
            return True
    return False


def _qty_clarification_question(payload: dict) -> str:
    """Render a focused qty-clarification question that ALSO echoes the
    understood trigger, so the user can validate the (hard) trigger logic
    in the same round-trip instead of re-confirming it after answering a
    trivial quantity. We deliberately do NOT ship the draft card with a
    placeholder qty=1 — an editable card reading "buy 1 share" is one
    mis-click from activating a wrong-sized order; echoing the trigger in
    prose gives the same validation without that footgun."""
    sym = ""
    for s in (payload.get("steps") or []):
        if not isinstance(s, dict):
            continue
        if s.get("step_type") == "action.place_order":
            sym = (s.get("config") or {}).get("symbol", "")
            break
    # The draft's one-line description already reads back trigger + action
    # ("When RSI(14) < 30, buy ...") — echo it so the user sees what we
    # understood before they commit to a size. Strip the placeholder
    # quantity ("buy 1 shares of INFY" → "buy INFY") so the echo doesn't
    # contradict the very question we're about to ask.
    readback = str(payload.get("description") or "").strip().rstrip(".")
    readback = re.sub(
        r"\b(buy|sell)\s+\d+\s+(?:shares?|units?|lots?)\s+of\s+",
        r"\1 ", readback, flags=re.IGNORECASE,
    )
    readback = re.sub(
        r"\b(buy|sell)\s+\d+\s+([A-Z][A-Z0-9&\-]{1,14})\b",
        r"\1 \2", readback, flags=re.IGNORECASE,
    )
    lead = f"Got the setup — {readback}. " if readback else ""
    if sym:
        return (
            f"{lead}How many shares of {sym} should the agent buy per fire? "
            "(I won't default to 1 — set the real size or give me a "
            "rupee budget like ₹10,000.)"
        )
    return (
        f"{lead}How many shares should the agent buy per fire? "
        "(I won't default to 1 — set the real size or give me a "
        "rupee budget like ₹10,000.)"
    )


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
