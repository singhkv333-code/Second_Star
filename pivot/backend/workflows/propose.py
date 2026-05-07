"""propose_workflow — translate NL strategy into a structured WorkflowDraft.

Used by the chatbot tool of the same name. The chatbot detects strategy-
creation intent and calls `propose_workflow_async(user_intent, user_id, db)`.
This module:

  1. Builds a focused system prompt that includes the full step-type
     catalog (constraint: LLM may NOT invent step types not in the catalog).
  2. Calls Sarvam (or OpenAI via route_and_call) with json_mode=True.
  3. Parses the JSON, validates EVERY step config against the registry's
     Pydantic models. On validation failure, retries ONCE with the
     concrete error embedded in the prompt.
  4. Returns a WorkflowDraft (Pydantic model) — does NOT persist anything.
     The frontend's editor renders the draft; the user clicks Activate
     which then POSTs to /api/workflows.

Mock mode: when SARVAM_API_KEY is empty (and OpenAI too), pattern-match
a small set of common prompts so the demo recording works without any
external dependency. The mock is keyed off keywords ("buy", "sell",
"every weekday", "if my buying power", "RELIANCE", "QQQ", etc.) and
emits a deterministic 5-step or 3-step draft. The mock IS the demo
narrative — keep it boring and reliable.

Constraint per ARCHITECTURE.md §10:
  - Never let the LLM invent step_types not in the catalog.
  - Always validate against the registry before returning.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

from pydantic import BaseModel, Field, ValidationError

from backend.config import settings
from backend.workflows.registry import STEP_REGISTRY


logger = logging.getLogger(__name__)


# ── Public response shape ────────────────────────────────────────────


class DraftStep(BaseModel):
    step_type: str
    label: Optional[str] = None
    config: dict[str, Any] = Field(default_factory=dict)


class WorkflowDraft(BaseModel):
    """The chat returns this to the frontend; the frontend renders an
    'Open in editor →' card and pre-fills the Agent panel on click.

    ``valid_until`` is optional. When set, the scheduler skips firing
    the workflow on or after that date — used for TTL-bound rules like
    *"buy if RSI<30, valid till 30 June"*. The model resolves relative
    phrases (*"end of the month"*, *"next Friday"*) to ISO YYYY-MM-DD
    before emitting; the editor surfaces the field so the user can
    override.
    """
    name: str
    description: Optional[str] = None
    steps: list[DraftStep]
    rationale: Optional[str] = None
    warnings: list[str] = Field(default_factory=list)
    valid_until: Optional[str] = Field(
        default=None,
        description=(
            "ISO YYYY-MM-DD date after which the workflow auto-deactivates. "
            "Set when the user attaches a TTL phrase ('valid till month "
            "end', 'until 30 June', 'good for the week'). Resolve "
            "relative dates to absolute before emitting. Leave null for "
            "perpetual workflows."
        ),
    )


# ── System prompt builder ────────────────────────────────────────────


def _build_catalog_summary() -> str:
    """Compact catalog dump for the system prompt. One line per step
    type with its required config keys — keeps token cost low."""
    lines: list[str] = []
    for step_type in sorted(STEP_REGISTRY.keys()):
        defn = STEP_REGISTRY[step_type]
        try:
            schema = defn.config_model.model_json_schema()
            required = sorted(schema.get("required", []))
            props = schema.get("properties", {}) or {}
            req_summary = (
                ", ".join(f"{k}: {props.get(k, {}).get('type', '?')}" for k in required)
                if required else "(no required fields)"
            )
        except Exception:
            req_summary = "(config schema unavailable)"
        marker = "TRIGGER" if defn.trigger_only else defn.category
        lines.append(f"  - {step_type}  [{marker}]  required: {req_summary}")
    return "\n".join(lines)


_SYSTEM_PROMPT_TEMPLATE = """You translate a user's natural-language trading strategy into a Pivot workflow.

A workflow is a LINEAR ordered list of steps. The first step (step_index=0) MUST be a trigger.* type. No branching, no loops, no sub-workflows.

You may ONLY use step_types from this catalog. Inventing a step_type that isn't listed will fail validation.

CATALOG (24 step types):
{catalog}

Inter-step references use Mustache syntax. Allowed namespaces:
  - {{{{ context.<step_index>.<dotted.path> }}}}  e.g. {{{{ context.1.buying_power }}}}
  - {{{{ context.webhook_payload.<dotted.path> }}}}  (only for trigger.webhook workflows)
  - {{{{ now }}}}
  - {{{{ workflow.<field> }}}}  (id, name, version)

Output ONLY valid JSON matching this schema (no prose, no markdown fences):
{{
  "name": "short workflow title",
  "description": "one-sentence summary in user's words",
  "steps": [
    {{
      "step_type": "trigger.schedule",
      "label": "human-readable label",
      "config": {{ "cron": "55 15 * * 1-5", "timezone": "Asia/Kolkata" }}
    }}
  ],
  "rationale": "1-2 sentences explaining why these steps map to the user's request"
}}

Rules:
  - Indian stocks default to exchange "NSE", currency INR.
  - "every weekday" → cron with day_of_week 1-5.
  - Times default to "Asia/Kolkata" unless the user specifies otherwise.
  - "if my buying power is over X" → fetch.portfolio THEN condition.numeric on context.<idx>.buying_power.
  - Order placement that mentions confirmation / approval / "ask me first" → action.place_order with requires_approval=true.
  - "notify me" / "alert me" → notify.message at the end.
  - If the user's intent is ambiguous, prefer the SIMPLER 2-3 step workflow over inventing fields.
"""


def build_system_prompt() -> str:
    return _SYSTEM_PROMPT_TEMPLATE.format(catalog=_build_catalog_summary())


# ── Validation ────────────────────────────────────────────────────────


class ProposalValidationError(ValueError):
    """Raised when the LLM returns a draft that doesn't validate."""


def validate_draft_against_registry(raw: dict[str, Any]) -> WorkflowDraft:
    """Parse the LLM's JSON output into WorkflowDraft AND validate every
    step config against the registry's Pydantic model.

    Multi-trigger rules:
      - Step 0 must be a trigger.* (every workflow needs at least one
        entry point).
      - Trigger.* may appear at any later index too — each trigger
        starts a new branch. Steps following a trigger up to the next
        trigger (or end of workflow) belong to that branch.
      - Two adjacent triggers (an empty branch) is rejected — most
        likely a model mistake; encourages the user to give every
        trigger at least one action.

    Raises ProposalValidationError with a precise message the LLM can
    use as feedback on a retry."""
    try:
        draft = WorkflowDraft.model_validate(raw)
    except ValidationError as e:
        raise ProposalValidationError(
            f"top-level draft shape invalid: {e.errors()[0].get('msg', 'unknown')}"
        ) from e

    if not draft.steps:
        raise ProposalValidationError("draft must contain at least one step")

    prev_was_trigger = False
    for idx, step in enumerate(draft.steps):
        defn = STEP_REGISTRY.get(step.step_type)
        if defn is None:
            allowed = sorted(STEP_REGISTRY.keys())
            # Suggest the closest match — the model often invents
            # near-misses ("condition.holding" vs "condition.position",
            # "condition.symbol" vs "condition.position").
            from difflib import get_close_matches
            near = get_close_matches(step.step_type, allowed, n=1, cutoff=0.5)
            suggestion = f" — did you mean {near[0]!r}?" if near else ""
            raise ProposalValidationError(
                f"step {idx}: unknown step_type {step.step_type!r}"
                f"{suggestion} Allowed step_types (full list): "
                f"{', '.join(allowed)}."
            )
        is_trigger = bool(defn.trigger_only)
        if idx == 0 and not is_trigger:
            raise ProposalValidationError(
                f"step 0 must be a trigger.* (got {step.step_type!r})"
            )
        if is_trigger and idx > 0 and prev_was_trigger:
            raise ProposalValidationError(
                f"step {idx}: two triggers in a row creates an empty "
                f"branch — give the previous trigger at least one action"
            )
        # Deterministic repair: numeric-string coercion, channel
        # collapse to push, time-string → weekday cron. Applied
        # in-place so the validated step + downstream executor see
        # the repaired config. Saves an LLM retry hop on common
        # LLM mistakes ("quantity": "ten", "channel": "email", etc.).
        from backend.services.arg_repair import repair_step_config
        repaired_cfg, _notes = repair_step_config(step.step_type, step.config or {})
        if repaired_cfg is not step.config:
            step.config = repaired_cfg

        try:
            defn.config_model.model_validate(step.config)
        except ValidationError as e:
            first = e.errors()[0]
            field = ".".join(str(p) for p in first.get("loc", []))
            raise ProposalValidationError(
                f"step {idx} ({step.step_type}) config invalid: "
                f"{field}: {first.get('msg', 'unknown')}"
            ) from e
        prev_was_trigger = is_trigger

    return draft


def trigger_step_indices(steps: list) -> list[int]:
    """Return the step_indices of every trigger.* step in order.

    Used by the scheduler / watcher / engine to enumerate branches.
    Accepts a list of WorkflowStep ORM objects OR DraftStep dicts —
    duck-typed via attribute lookup.
    """
    out: list[int] = []
    for s in steps:
        st = getattr(s, "step_type", None)
        if st is None and isinstance(s, dict):
            st = s.get("step_type")
        idx = getattr(s, "step_index", None)
        if idx is None and isinstance(s, dict):
            idx = s.get("step_index")
        if isinstance(st, str) and st.startswith("trigger."):
            try:
                out.append(int(idx))
            except (TypeError, ValueError):
                continue
    return sorted(out)


# ── LLM call + retry loop ────────────────────────────────────────────


_PLAN_SYSTEM_INSTRUCTION = """You are translating a user's natural-
language strategy description into a Pivot workflow plan.

A Pivot workflow is a LINEAR ordered list of steps:
  step 0: exactly one trigger (trigger.schedule | trigger.price |
          trigger.indicator | trigger.event | trigger.manual |
          trigger.webhook)
  step 1+: optional fetch.* (data the decision needs)
  step 2+: optional condition.* (gates continuation; halts if false)
  step N: action.* (the trade or watchlist update)
  step N+1: optional notify.* (user-facing notification)

Hard constraints:
  - Exactly ONE trigger, at step 0. No multi-trigger workflows.
  - No branching, no loops, no sub-workflows.
  - All inter-step references use {{ context.<idx>.<dotted.path> }}.

Your job (this hop): write a SHORT plan in plain English (4-8 lines)
describing each step you'd emit and why. Do NOT emit JSON yet. Do not
list step types you're unsure about — say "needs clarification" if a
required field can't be inferred from the user's request.

If the user's strategy genuinely doesn't fit the linear-single-trigger
shape (e.g. "buy Monday and sell Tuesday" needs two agents), say so
explicitly so the next hop can surface a clarification rather than
fabricate an invalid draft.
"""


_DRAFT_SYSTEM_INSTRUCTION = """You are emitting the JSON for a Pivot
workflow given a plan. You receive the user's original intent and a
pre-written plan describing each step.

Output ONLY a single JSON object matching this schema (no prose, no
markdown fences):
{
  "name": "short workflow title",
  "description": "one-sentence summary",
  "steps": [
    { "step_type": "...", "label": "...", "config": { ... } }
  ],
  "rationale": "1-2 sentences explaining the mapping"
}

You may ONLY use step_types from the catalog the planner referenced.
"""


async def _call_llm_for_plan(user_intent: str) -> str:
    """Phase 1: planning. Medium reasoning. Returns plain-English
    text describing the steps.

    Planning is the load-bearing reasoning task in propose_workflow —
    the model has to decide trigger type, what fetches are needed,
    what conditions gate execution, and whether the user's intent
    actually fits Pivot's shape at all. Cutting reasoning here is
    where quality cratered when we tried `"minimal"` for the whole
    flow on 2026-05-03.
    """
    from backend.llm import LLMMessage, get_llm_client

    catalog = _build_catalog_summary()
    system = (
        f"{_PLAN_SYSTEM_INSTRUCTION}\n\n"
        f"CATALOG (24 step types, names you can plan with):\n{catalog}"
    )
    client = get_llm_client()
    response = await client.complete(
        messages=[
            LLMMessage(role="system", content=system),
            LLMMessage(role="user", content=user_intent),
        ],
        max_output_tokens=900,
        reasoning_effort="medium",
        temperature=0.2,
    )
    if response.finish_reason == "error":
        raise ProposalValidationError(
            f"LLM error during workflow planning: {response.content}"
        )
    return (response.content or "").strip()


async def _call_llm_for_draft(
    user_intent: str,
    *,
    extra_instruction: str = "",
) -> str:
    """Phase 2: JSON drafting from the plan. Minimal reasoning.

    Two-call structure (plan → draft) collapses to a single call here
    when `extra_instruction` is set — that's the validation-retry
    path inside `_propose_via_llm`. On retry the plan is implicit in
    the original system prompt + the embedded validation error; we
    don't re-plan because the retry IS a fix-up call.
    """
    from backend.llm import LLMMessage, get_llm_client

    if extra_instruction:
        # Retry path: skip planning, go straight to draft with the
        # validation error embedded. Treat it as transcription —
        # the planner already ran in the previous iteration.
        system = build_system_prompt() + f"\n\nIMPORTANT: {extra_instruction}"
        messages = [
            LLMMessage(role="system", content=system),
            LLMMessage(role="user", content=user_intent),
        ]
    else:
        # Happy path: plan first, then transcribe.
        plan = await _call_llm_for_plan(user_intent)
        system = (
            f"{_DRAFT_SYSTEM_INSTRUCTION}\n\n"
            f"CATALOG:\n{_build_catalog_summary()}"
        )
        messages = [
            LLMMessage(role="system", content=system),
            LLMMessage(
                role="user",
                content=(
                    f"User intent: {user_intent}\n\n"
                    f"Plan:\n{plan}\n\n"
                    "Now emit the JSON workflow draft matching the schema."
                ),
            ),
        ]

    client = get_llm_client()
    response = await client.complete(
        messages=messages,
        max_output_tokens=1500,
        reasoning_effort="minimal",
        temperature=0.2,
        response_format="json_object",
    )
    if response.finish_reason == "error":
        raise ProposalValidationError(
            f"LLM error during workflow draft: {response.content}"
        )
    return response.content or ""


def _extract_json(raw: str) -> dict[str, Any]:
    """Pull the first {...} JSON object out of a possibly-noisy LLM
    response. Tolerates leading prose or trailing chatter."""
    raw = raw.strip()
    if raw.startswith("```"):
        # strip markdown fence
        raw = re.sub(r"^```(json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    # First brace-balanced object
    start = raw.find("{")
    if start < 0:
        raise ProposalValidationError(
            f"LLM did not return JSON; got: {raw[:200]!r}"
        )
    depth = 0
    for i, ch in enumerate(raw[start:], start=start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    obj = json.loads(raw[start:i + 1])
                except json.JSONDecodeError as e:
                    raise ProposalValidationError(
                        f"LLM JSON malformed: {e.msg}"
                    ) from e
                if not isinstance(obj, dict):
                    raise ProposalValidationError(
                        "LLM returned a non-object JSON"
                    )
                return obj
    raise ProposalValidationError("LLM JSON had unbalanced braces")


async def _propose_via_llm(user_intent: str) -> WorkflowDraft:
    """Two-attempt loop: call LLM, validate, on fail call again with
    the validation error embedded so the LLM can self-correct."""
    raw = await _call_llm_for_draft(user_intent)
    try:
        return validate_draft_against_registry(_extract_json(raw))
    except ProposalValidationError as e:
        logger.info("propose_workflow LLM retry: %s", e)
        retry_raw = await _call_llm_for_draft(
            user_intent,
            extra_instruction=(
                f"Your previous response failed validation: {e}. "
                "Fix it. Output ONLY the corrected JSON object."
            ),
        )
        return validate_draft_against_registry(_extract_json(retry_raw))


# ── Mock mode (no LLM key) ───────────────────────────────────────────


def _is_mock_mode() -> bool:
    """True when neither Sarvam nor OpenAI is configured. Demo mode."""
    return not (settings.sarvam_api_key or settings.openai_api_key)


_RX_NUMBER = re.compile(r"\d+(?:[\.,]\d+)?")
_RX_TIME_HHMM = re.compile(r"\b(\d{1,2})[:\.](\d{2})\b")


_RX_QTY_CONTEXT = re.compile(
    r"(?:buy|sell|order|purchase|short)\s+(\d+)"
    r"|(\d+)\s+(?:shares?|units?|stocks?|contracts?|lots?)",
    re.IGNORECASE,
)


def _parse_quantity(intent: str, default: int = 1) -> int:
    """Pull a quantity out of the prompt. Looks first for a context-
    qualified number ('buy 10', '5 shares'); otherwise falls back to
    the smallest standalone integer that isn't part of an HH:MM time
    or an obvious threshold (>1000)."""
    m = _RX_QTY_CONTEXT.search(intent)
    if m:
        try:
            return int(m.group(1) or m.group(2))
        except (TypeError, ValueError):
            pass
    # Fallback: skip any number that's part of an HH:MM time or a
    # comma-grouped threshold like "50,000".
    excluded_spans: list[tuple[int, int]] = []
    for tm in _RX_TIME_HHMM.finditer(intent):
        excluded_spans.append(tm.span())
    for thresh in re.finditer(r"\d{1,3}(?:,\d{3})+", intent):  # 1,000+ form
        excluded_spans.append(thresh.span())
    for m in _RX_NUMBER.finditer(intent):
        if any(s <= m.start() < e for s, e in excluded_spans):
            continue
        try:
            qty = int(float(m.group().replace(",", "")))
        except ValueError:
            continue
        if 1 <= qty <= 1000:
            return qty
    return default


def _parse_symbol(intent: str) -> str:
    """Pick the first uppercase ticker-like token. Falls back to
    RELIANCE for the demo."""
    for tok in re.findall(r"\b[A-Z]{2,12}\b", intent):
        if tok in {"AM", "PM", "IST", "EST", "UTC", "NSE", "BSE", "USD", "INR"}:
            continue
        return str(tok)
    return "RELIANCE"


def _parse_cron_from_text(intent: str) -> tuple[str, str]:
    """Best-effort cron + tz extraction. Defaults to weekday 09:30 IST."""
    tz = "Asia/Kolkata"
    # "every weekday at HH:MM"
    m = _RX_TIME_HHMM.search(intent)
    hh, mm = (9, 30)
    if m:
        try:
            hh, mm = int(m.group(1)), int(m.group(2))
            if "pm" in intent.lower() and hh < 12:
                hh += 12
            if "am" in intent.lower() and hh == 12:
                hh = 0
        except ValueError:
            pass
    dow = "1-5" if "weekday" in intent.lower() else "*"
    return f"{mm} {hh} * * {dow}", tz


def _parse_threshold(intent: str, default: float = 50000) -> float:
    """Pull a 'over X' / 'above X' / 'more than X' value."""
    m = re.search(r"(?:over|above|more than|>=?|at least)\s*₹?\s*([\d,]+)", intent, re.I)
    if m:
        try:
            return float(m.group(1).replace(",", ""))
        except ValueError:
            pass
    return default


def _mock_propose(user_intent: str) -> WorkflowDraft:
    """Pattern-match the user intent into a demo-friendly draft.

    The demo prompt
      "Every weekday at 3:55 PM IST, if my buying power is over ₹50,000,
       buy 10 shares of RELIANCE and notify me by email."
    maps to the canonical 5-step demo workflow.
    """
    low = user_intent.lower()
    cron, tz = _parse_cron_from_text(user_intent)
    qty = _parse_quantity(user_intent, default=1)
    symbol = _parse_symbol(user_intent)
    threshold = _parse_threshold(user_intent)
    needs_approval = bool(re.search(r"\b(approve|approval|confirm|ask me)\b", low))

    steps: list[DraftStep] = [
        DraftStep(
            step_type="trigger.schedule",
            label=f"On {cron} {tz}",
            config={"cron": cron, "timezone": tz},
        ),
    ]

    has_condition = bool(re.search(r"\bif\b|\bonly if\b|when my\b", low))
    if "buying power" in low or "balance" in low or has_condition:
        steps.append(DraftStep(
            step_type="fetch.portfolio",
            label="Get my portfolio",
            config={},
        ))
        steps.append(DraftStep(
            step_type="condition.numeric",
            label=f"Buying power > {int(threshold)}",
            config={
                "left": "{{ context.1.buying_power }}",
                "operator": ">",
                "right": threshold,
            },
        ))

    side = "sell" if "sell" in low else "buy"
    steps.append(DraftStep(
        step_type="action.place_order",
        label=f"{side.capitalize()} {qty} {symbol}",
        config={
            "symbol": symbol,
            "side": side,
            "quantity": qty,
            "order_type": "market",
            "requires_approval": needs_approval or side == "buy",
        },
    ))

    if "notify" in low or "email" in low or "alert" in low or "sms" in low:
        channel = "email" if "email" in low else (
            "sms" if "sms" in low or "text" in low else "push"
        )
        # Proper past tense — "Buyed"/"Selled" reads broken in the
        # rendered email/SMS body.
        past = {"buy": "Bought", "sell": "Sold"}.get(side, side.capitalize())
        steps.append(DraftStep(
            step_type="notify.message",
            label=f"Notify by {channel}",
            config={
                "channel": channel,
                "template": f"{past} {qty} {symbol}",
                "vars": {},
            },
        ))

    name_bits = [side.capitalize(), str(qty), symbol]
    return WorkflowDraft(
        name=" ".join(name_bits),
        description=user_intent.strip()[:200],
        steps=steps,
        rationale=(
            "Mapped your request to a scheduled trigger "
            f"({cron} {tz}), portfolio check, and a {side} order. "
            f"Requires approval = {needs_approval or side == 'buy'}."
        ),
        warnings=[],
    )


# ── Public entry point ───────────────────────────────────────────────


async def propose_workflow_async(user_intent: str) -> WorkflowDraft:
    """Translate a natural-language strategy into a validated WorkflowDraft.

    With keys configured, calls the LLM and validates strictly, retrying
    ONCE on validation failure. Without keys (offline / CI), falls back
    to deterministic pattern-matched mock for the demo recording.

    NEVER returns a fabricated workflow when the LLM was *available* but
    failed to produce a valid draft. The earlier behaviour — falling
    through to the same pattern-matched mock and surfacing it with a
    warning — silently lied to users (they'd see canned RELIANCE/buying-
    power steps regardless of what they asked for). That path was killed
    on 2026-05-03; the LLM-failure case now raises ProposalValidationError
    so callers can present a structured "I need more info" message.

    Raises:
        ProposalValidationError when LLM-based proposal can't produce a
        valid draft after one retry. The error message names the
        specific missing/invalid fields so the chat can surface them
        to the user verbatim.
    """
    user_intent = (user_intent or "").strip()
    if not user_intent:
        raise ProposalValidationError("user_intent is empty")

    if _is_mock_mode():
        # Genuine offline mode (no Sarvam, no OpenAI). The mock path
        # is the demo recording's deterministic fallback — kept so CI
        # and screencast runs work without network. NOT a graceful
        # degradation when an LLM call fails.
        draft = _mock_propose(user_intent)
        return validate_draft_against_registry(draft.model_dump())

    # LLM is configured — propose for real, no safety net. If the model
    # can't produce a valid draft after one retry, the caller (chat
    # service / propose endpoint) gets the validation error and is
    # responsible for telling the user what's missing.
    return await _propose_via_llm(user_intent)
