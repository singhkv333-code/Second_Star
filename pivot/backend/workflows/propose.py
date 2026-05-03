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
    'Open in editor →' card and pre-fills the Agent panel on click."""
    name: str
    description: Optional[str] = None
    steps: list[DraftStep]
    rationale: Optional[str] = None
    warnings: list[str] = Field(default_factory=list)


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

    for idx, step in enumerate(draft.steps):
        defn = STEP_REGISTRY.get(step.step_type)
        if defn is None:
            raise ProposalValidationError(
                f"step {idx}: unknown step_type {step.step_type!r} "
                f"(allowed: {sorted(STEP_REGISTRY.keys())[:5]}... + 19 more)"
            )
        if idx == 0 and not defn.trigger_only:
            raise ProposalValidationError(
                f"step 0 must be a trigger.* (got {step.step_type!r})"
            )
        if idx > 0 and defn.trigger_only:
            raise ProposalValidationError(
                f"step {idx}: trigger.* may only appear at step 0 "
                f"(got {step.step_type!r})"
            )
        try:
            defn.config_model.model_validate(step.config)
        except ValidationError as e:
            first = e.errors()[0]
            field = ".".join(str(p) for p in first.get("loc", []))
            raise ProposalValidationError(
                f"step {idx} ({step.step_type}) config invalid: "
                f"{field}: {first.get('msg', 'unknown')}"
            ) from e

    return draft


# ── LLM call + retry loop ────────────────────────────────────────────


async def _call_llm_for_draft(
    user_intent: str,
    *,
    extra_instruction: str = "",
) -> str:
    """Calls route_and_call with a workflow-proposal-tuned prompt.
    Returns raw response text (caller parses JSON)."""
    from backend.agents.router import TaskType, route_and_call

    system = build_system_prompt()
    if extra_instruction:
        system += f"\n\nIMPORTANT: {extra_instruction}"

    return await route_and_call(
        TaskType.STRUCTURED_JSON,  # routes to OpenAI when SARVAM is too loose
        messages=[{"role": "user", "content": user_intent}],
        system_prompt=system,
        json_mode=True,
        max_tokens=2000,
    )


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
