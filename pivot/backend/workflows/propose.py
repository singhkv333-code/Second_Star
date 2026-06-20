"""propose_workflow — translate NL strategy into a structured WorkflowDraft.

Used by the chatbot tool of the same name. The chatbot detects strategy-
creation intent and calls `propose_workflow_async(user_intent, user_id, db)`.
This module:

  1. Builds a focused system prompt that includes the full step-type
     catalog (constraint: LLM may NOT invent step types not in the catalog).
  2. Calls the LLM (Azure / OpenAI via get_llm_client) with json_mode=True.
  3. Parses the JSON, validates EVERY step config against the registry's
     Pydantic models. On validation failure, retries ONCE with the
     concrete error embedded in the prompt.
  4. Returns a WorkflowDraft (Pydantic model) — does NOT persist anything.
     The frontend's editor renders the draft; the user clicks Activate
     which then POSTs to /api/workflows.

Mock mode: when no LLM key is configured (OPENAI_API_KEY and AZURE_KEY
both empty), pattern-match
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

    ``diagnostics`` carries the serialized output of
    :func:`backend.workflows.compat.lint_workflow` — only the non-fatal
    (``warning`` / ``info``) findings end up here; ``error``-severity
    diagnostics are raised as :class:`ProposalValidationError` before
    the draft is returned. The FE editor dispatches on ``code`` to
    render inline hints next to the offending step.
    """
    name: str
    description: Optional[str] = None
    steps: list[DraftStep]
    rationale: Optional[str] = None
    warnings: list[str] = Field(default_factory=list)
    diagnostics: list[dict[str, Any]] = Field(default_factory=list)
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
    type with its required config keys — keeps token cost low.

    Indicator-bearing step types (``trigger.indicator``, plus compound
    triggers that wrap an indicator node in their tree) also advertise
    the optional ``timeframe`` field so prompts like "...on weekly bars"
    actually produce ``timeframe:"weekly"`` instead of silently dropping
    it. The default stays ``daily`` — adding the hint does not change
    behaviour for prompts that don't mention a timeframe.
    """
    lines: list[str] = []
    # step_types that accept the optional `timeframe` field on the
    # indicator/compound trigger config (or inside the compound tree's
    # IndicatorNode leaves). Listing them explicitly keeps the hint
    # local to where it's meaningful instead of polluting every line.
    _TIMEFRAME_AWARE = {
        "trigger.indicator",
        "trigger.compound",
        "trigger.exit_compound",
        "condition.compound",
    }
    for step_type in sorted(STEP_REGISTRY.keys()):
        defn = STEP_REGISTRY[step_type]
        # Never advertise a deprecated/collapsed id to the planner — it should
        # only emit the parameterized replacements (action.set_protective,
        # action.squareoff, fetch.price_reference, fetch.rolling_extreme).
        if getattr(defn, "deprecated", False):
            continue
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
        suffix = ""
        if step_type in _TIMEFRAME_AWARE:
            # The indicator leaf carries an OPTIONAL timeframe; default
            # daily. The LLM must propagate the user's stated bar size.
            suffix = (
                "   optional: timeframe?: \"daily\" | \"weekly\" "
                "(default daily; honor user phrases like "
                "'on weekly bars' / 'weekly RSI')"
            )
        lines.append(
            f"  - {step_type}  [{marker}]  required: {req_summary}{suffix}"
        )
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
  "rationale": "3-6 sentences explaining (a) why these instruments are the right beneficiaries of the user's stated view, (b) how each step maps to the request, (c) the material risks this carries, (d) what this automation is NOT (e.g. 'not market-neutral', 'not a true hedge'). Never a one-liner. Never generic."
}}

Rules:
  - Indian stocks default to exchange "NSE", currency INR.
  - "every weekday" → cron with day_of_week 1-5.
  - Times default to "Asia/Kolkata" unless the user specifies otherwise.
  - "if my buying power is over X" → fetch.portfolio THEN condition.numeric on context.<idx>.buying_power.
  - Order placement that mentions confirmation / approval / "ask me first" → action.place_order with requires_approval=true.
  - "notify me" / "alert me" → notify.message at the end.
  - If the user's intent is ambiguous, prefer the SIMPLER 2-3 step workflow over inventing fields.
  - Indicator timeframe: trigger.indicator / trigger.compound / trigger.exit_compound / condition.compound accept an optional `timeframe: "daily" | "weekly"`. Default is `daily`. If the user says "weekly RSI", "on weekly bars", "weekly chart", "W/F-close", etc., set `timeframe: "weekly"` on the indicator config (or on every IndicatorNode leaf inside a compound tree). Do NOT invent a non-default timeframe when the user did not ask for it.

INSTRUMENT SELECTION FOR THEMATIC / DIRECTIONAL REQUESTS (HARD RULES — getting this wrong is a correctness failure):

  1. When the user expresses a THEMATIC view ("profits from rising oil", "benefits from a weaker rupee", "plays the AI boom") DO NOT pick a single arbitrary stock. Pick a small BASKET (3-5 names) of the actual beneficiaries and prefer the basket macro shape (action.allocate_notional over a fetch.screener) when the catalog supports it. A single-name SIP into one arbitrary ticker is almost always wrong for a thematic ask.

  2. You must reason about WHO ACTUALLY BENEFITS from the move the user describes — this is not the same as "stocks in the same sector". The most important Indian examples to internalize:

       - "Profits from RISING crude / oil prices" → UPSTREAM PRODUCERS who sell crude they pull out of the ground: ONGC, OIL India (Oil India Ltd). Optionally Reliance (integrated; upstream exposure partially offset by refining). Cairn / Vedanta has crude exposure too.
         EXPLICITLY WRONG for this view: IOC, BPCL, HPCL. These are refiners / oil MARKETING companies; their gross refining margins COMPRESS when crude rises because retail fuel prices are politically administered and they can't pass through the cost in real time. Picking IOC for "profits from rising oil" is a textbook backwards trade.

       - "Profits from FALLING crude / oil prices" → flip it: refiners/marketers (IOC, BPCL, HPCL) and heavy crude-input consumers (paints: ASIANPAINT/BERGEPAINT; aviation: INDIGO; tyres) benefit. Upstream producers (ONGC, OIL India) suffer.

       - "Benefits from a WEAKER rupee" (USD/INR up) → IT exporters (TCS, INFY, HCLTECH, WIPRO), pharma exporters (SUNPHARMA, DRREDDY), some auto exporters. NOT importers, NOT oil marketers (their import bill rises).

       - "Benefits from RBI rate CUTS" → rate-sensitive: NBFCs, housing finance, autos, real estate. NOT banks straightforwardly (NIMs compress).

       - "Benefits from gold rising" → gold financiers (MUTHOOTFIN, MANAPPURAM) and gold jewellers/ETFs; NOT generic "metals" stocks.

     If the user's thematic view falls outside these and you are not confident in WHO benefits, say so in the rationale and pick the most defensible small basket plus a clear caveat — never fabricate confidence.

  3. RISK-NEUTRAL / HEDGED / MARKET-NEUTRAL constraints. If the user says "risk neutral", "hedged", "market neutral", "delta neutral", "pair trade", "long-short", or any equivalent: a long-only SIP / long-only basket is NOT a hedge and is NOT risk-neutral. You MUST either:
       (a) propose a structurally hedged shape — e.g. a long basket of the beneficiaries paired with a short on a broad index (NIFTY/BANKNIFTY) or a paired short of the natural anti-beneficiary, OR an options-defined-risk structure if the catalog supports option steps, OR
       (b) if the catalog cannot express a clean hedge for this view, emit the closest LONG-ONLY directional draft AND state plainly in the rationale: "This automation is NOT market-neutral — it has full equity beta. A true neutral version would require <pair leg / index short / option structure> which this workflow does not include." Do not silently ship a long-only structure under a hedge ask.

  4. HONESTY OVER FAKE SUCCESS. Never describe a long-only weekly SIP as "risk-neutral" or "a hedge". Never claim the workflow "profits from X" if the instruments you chose actually suffer from X. If you cannot honestly map the user's intent into the catalog, prefer a shorter, simpler draft with a forthright rationale over a polished draft that overstates what was built.
"""


def build_system_prompt() -> str:
    return _SYSTEM_PROMPT_TEMPLATE.format(catalog=_build_catalog_summary())


# ── Validation ────────────────────────────────────────────────────────


class ProposalValidationError(ValueError):
    """Raised when the LLM returns a draft that doesn't validate."""


async def resolve_polymarket_event_descriptions(raw: dict[str, Any]) -> None:
    """In-place resolve `trigger.polymarket` steps that carry only the
    `event_description` escape hatch.

    The LLM is encouraged to call ``propose_polymarket_trigger`` FIRST
    when building a compound workflow with a Polymarket leg, so the
    resolved ``market_id`` + ``token_id`` + ``side`` land in the draft
    inline. This resolver is the safety net for the occasional single-
    shot compound draft: it walks ``raw['steps']``, finds any
    ``trigger.polymarket`` step missing the resolved ids but carrying
    ``event_description``, and calls the matcher.

    Behaviour:
      - ``market_id`` AND ``token_id`` already set → skip (resolved).
      - Neither set + no ``event_description`` → skip (will fail the
        Pydantic validator with a clear message on the next pass).
      - Neither set + ``event_description`` set + matcher confidence
        ≥ 0.85 → in-place fill ``market_id``, ``token_id``, ``side``,
        and ``question`` (if absent).
      - Below confidence → raise ``ProposalValidationError`` so the
        LLM knows to call ``propose_polymarket_trigger`` first.

    Why this lives BEFORE ``validate_draft_against_registry`` rather
    than inside the Pydantic validator: the resolver is async +
    network-bound; the registry validator is sync + pure. Mixing them
    would break the existing call sites that validate in tight loops
    (e.g. tests) without a network round trip.
    """
    steps = (raw or {}).get("steps") or []
    if not isinstance(steps, list):
        return
    for idx, step in enumerate(steps):
        if not isinstance(step, dict):
            continue
        if step.get("step_type") != "trigger.polymarket":
            continue
        cfg = step.get("config") or {}
        if cfg.get("market_id") and cfg.get("token_id"):
            continue
        desc = str(cfg.get("event_description") or "").strip()
        if not desc:
            # Will fail the Pydantic validator (market_id min_length=1).
            # Don't pre-empt the error here.
            continue
        from backend.news_events.parsing.polymarket_match import (
            match_event_to_polymarket_contract,
        )
        match = await match_event_to_polymarket_contract(desc)
        if not match.matched or match.confidence < 0.85:
            raise ProposalValidationError(
                f"step {idx} (trigger.polymarket): polymarket contract "
                f"ambiguous (matcher confidence "
                f"{match.confidence:.2f} < 0.85). Call "
                f"propose_polymarket_trigger first to nail the contract "
                f"with the user, then emit this workflow with "
                f"market_id + token_id + side inline on the "
                f"trigger.polymarket step."
            )
        cfg["market_id"] = match.market_id
        cfg["token_id"] = match.token_id
        cfg["side"] = match.side or "YES"
        if match.question and not cfg.get("question"):
            cfg["question"] = match.question
        step["config"] = cfg


async def resolve_kalshi_event_descriptions(raw: dict[str, Any]) -> None:
    """In-place resolve ``trigger.kalshi`` steps carrying only the
    ``event_description`` escape hatch — the Kalshi sibling of
    :func:`resolve_polymarket_event_descriptions`.

    market_id + token_id already set → skip. event_description set + matcher
    confidence ≥ 0.85 → fill market_id/token_id/side/question inline. Below
    confidence → raise so the LLM calls ``propose_kalshi_trigger`` first.
    """
    steps = (raw or {}).get("steps") or []
    if not isinstance(steps, list):
        return
    for idx, step in enumerate(steps):
        if not isinstance(step, dict):
            continue
        if step.get("step_type") != "trigger.kalshi":
            continue
        cfg = step.get("config") or {}
        if cfg.get("market_id") and cfg.get("token_id"):
            continue
        desc = str(cfg.get("event_description") or "").strip()
        if not desc:
            continue
        from backend.news_events.parsing.kalshi_match import (
            match_event_to_kalshi_contract,
        )
        match = await match_event_to_kalshi_contract(desc)
        if not match.matched or match.confidence < 0.85:
            raise ProposalValidationError(
                f"step {idx} (trigger.kalshi): kalshi contract ambiguous "
                f"(matcher confidence {match.confidence:.2f} < 0.85). Call "
                f"propose_kalshi_trigger first to nail the contract with the "
                f"user, then emit this workflow with market_id + token_id + "
                f"side inline on the trigger.kalshi step."
            )
        cfg["market_id"] = match.market_id
        cfg["token_id"] = match.token_id
        cfg["side"] = match.side or "YES"
        if match.question and not cfg.get("question"):
            cfg["question"] = match.question
        step["config"] = cfg


def _ensure_step_labels(draft: WorkflowDraft) -> None:
    """In-place: every step gets a human label from the registry.

    The frontend's WorkflowDraftCard falls back to the raw ``step_type``
    string ("trigger.compound", "action.place_order") when ``label`` is
    null or empty. That leaks engineering ids into the chat surface.

    This helper authoritatively backfills ``step.label`` from
    ``STEP_REGISTRY[step.step_type].label`` whenever the LLM/mock path
    left it falsy. Defensive: skips step_types not in the registry
    (the validator will already have rejected those, but if a future
    caller invokes this on an unvalidated draft we don't want to crash
    here).
    """
    for step in draft.steps:
        if step.label:
            continue
        defn = STEP_REGISTRY.get(step.step_type)
        if defn is None:
            continue
        step.label = defn.label


# Deprecated/collapsed step_type → (replacement, discriminator config).
_DEPRECATED_STEP_NORMALIZERS: dict[str, tuple[str, dict[str, Any]]] = {
    "action.set_stoploss": ("action.set_protective", {"kind": "stoploss"}),
    "action.set_takeprofit": ("action.set_protective", {"kind": "takeprofit"}),
    "action.squareoff_all": ("action.squareoff", {"scope": "all"}),
    "action.squareoff_symbol": ("action.squareoff", {"scope": "symbol"}),
    "action.squareoff_all_intraday": ("action.squareoff", {"scope": "intraday"}),
    "fetch.day_open": ("fetch.price_reference", {"reference": "day_open"}),
    "fetch.prior_close": ("fetch.price_reference", {"reference": "prior_close"}),
    "fetch.rolling_high": ("fetch.rolling_extreme", {"side": "high"}),
    "fetch.rolling_low": ("fetch.rolling_extreme", {"side": "low"}),
}


def _normalize_deprecated_steps(draft: WorkflowDraft) -> None:
    """In-place: rewrite any deprecated/collapsed step_type the planner emitted
    to its parameterized replacement, injecting the discriminator field so the
    new config model validates. Idempotent — steps already on the new shape are
    left untouched. The legacy ids still execute via their alias, but freshly
    proposed drafts should use the slim catalog."""
    for step in draft.steps:
        mapping = _DEPRECATED_STEP_NORMALIZERS.get(step.step_type)
        if mapping is None:
            continue
        new_type, discriminator = mapping
        cfg = dict(step.config or {})
        for key, val in discriminator.items():
            cfg.setdefault(key, val)
        step.step_type = new_type
        step.config = cfg


# ── Event-trigger allow-list (conservative beta) ─────────────────────
#
# Pivot's beta accepts only a small set of VERIFIABLE event triggers.
# An event trigger is a condition that FIRES A REAL ACTION, so it must
# be backed by a fixed, time-boxed, trusted source we can actually
# check (RBI/Fed RSS, a listed Polymarket/Kalshi market, an exchange
# feed). Anything open-ended ("if war breaks out", "good monsoon",
# "election verdict") has no such feed — we refuse to TRIGGER on it and
# steer the user to the nearest real alternative (a theme/basket
# STRATEGY, a prediction-market resolution trigger, or a price/VIX
# trigger). This is the authoritative gate: even if the system prompt
# fails to steer the model, an excluded trigger never validates and so
# never persists.
#
# IMPORTANT — keep the two ideas separate: this gate constrains event
# TRIGGERS only. Pivot's separate ability to DESIGN A STRATEGY around a
# theme (monsoon/elections/war → a basket of beneficiaries) is
# untouched; those flow through action.allocate_* / fetch.screener, not
# through a trigger.* step.

# Allow-listed `kind` values for the calendar-armed macro trigger
# (trigger.scheduled_macro). Derived from the macro source-of-truth table
# so this gate, the calendar, and the verifier can never drift apart —
# adding a kind there automatically allows it here.
from backend.macro_events.source_of_truth import all_kinds as _macro_all_kinds

_ALLOWED_MACRO_KINDS = frozenset(_macro_all_kinds())

# Substring markers that mark a `trigger.event` ask as open-ended /
# unverifiable / explicitly-out-of-scope for the beta. Matched against
# the lower-cased event_description + keywords. A false reject is cheap
# (it just routes the planner to a clearer alternative); a false accept
# would arm a real order on a feed we cannot confirm.
_UNVERIFIABLE_EVENT_MARKERS = (
    # geopolitical / conflict
    "war", "ceasefire", "invasion", "invade", "missile", "airstrike",
    "military", "conflict", "attack",
    # weather / disaster
    "monsoon", "drought", "rainfall", "el nino", "el niño", "flood",
    "earthquake", "cyclone", "hurricane", "disaster", "pandemic",
    # politics
    "election", "exit poll", "poll result", "verdict", "coalition",
    # explicitly-excluded market-structure (per beta scope)
    "fii flow", "dii flow", "fii/dii", "net flow", "net-flow",
    "index rebalance", "rebalance", "reshuffle", "reconstitution",
)

_REFUSAL_ALTERNATIVE = (
    "Do NOT emit any trigger.* step for this. Instead reply in plain "
    "chat and offer the user the nearest REAL alternative: (1) build a "
    "theme/basket STRATEGY now around who actually benefits from the "
    "view (action.allocate_notional / fetch.screener — NOT a trigger), "
    "(2) a prediction-market resolution trigger (trigger.polymarket or "
    "trigger.kalshi) IF a listed binary market matches the ask, or "
    "(3) a price / India-VIX threshold trigger (trigger.price). Never "
    "fabricate a news feed or claim to watch something we cannot verify."
)


def validate_trigger_allowlist(draft: WorkflowDraft) -> None:
    """Enforce the conservative-beta event-trigger allow-list in place.

    Raises :class:`ProposalValidationError` (with planner-actionable
    guidance) when a draft carries an out-of-scope or unverifiable
    event trigger. Non-event triggers (price/indicator/schedule/…) and
    allow-listed event triggers pass untouched.
    """
    for idx, step in enumerate(draft.steps):
        st = step.step_type
        cfg = step.config or {}

        if st == "trigger.scheduled_macro":
            kind = str(cfg.get("kind", "")).strip().lower()
            if kind not in _ALLOWED_MACRO_KINDS:
                raise ProposalValidationError(
                    f"step {idx} (trigger.scheduled_macro): kind "
                    f"{kind!r} is not a supported macro event in this "
                    f"beta. Allowed kinds: "
                    f"{', '.join(sorted(_ALLOWED_MACRO_KINDS))}. "
                    f"{_REFUSAL_ALTERNATIVE}"
                )

        elif st == "trigger.event":
            hay = " ".join([
                str(cfg.get("event_description", "")),
                " ".join(
                    str(k) for k in (cfg.get("keywords") or [])
                    if isinstance(k, str)
                ),
            ]).lower()
            hit = next(
                (m for m in _UNVERIFIABLE_EVENT_MARKERS if m in hay), None,
            )
            if hit is not None:
                raise ProposalValidationError(
                    f"step {idx} (trigger.event): '{hit}' is not a "
                    f"verifiable event trigger in this beta — there is no "
                    f"fixed, time-boxed feed Pivot can check to confirm "
                    f"it, so it must not fire a real order. "
                    f"{_REFUSAL_ALTERNATIVE}"
                )


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

    # Upgrade any deprecated/collapsed step_type to its parameterized
    # replacement BEFORE per-step config validation (the new config models
    # require the discriminator the normalizer injects).
    _normalize_deprecated_steps(draft)

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

    # Conservative-beta event-trigger allow-list. Runs after per-step
    # registry validation (so configs are already Pydantic-valid) and
    # before the lint pass. Rejects out-of-scope / unverifiable event
    # triggers with planner-actionable guidance; the LLM retry path can
    # self-correct, and a persistent failure surfaces to the user as a
    # "here's the nearest real thing" message rather than a fake feed.
    validate_trigger_allowlist(draft)

    # Cross-step lint pass (capability / refs / structural). Runs after
    # the per-step registry validation above so the linter sees a draft
    # whose individual configs are already Pydantic-valid. ``error``
    # diagnostics rejoin the same hard-failure mechanism the per-step
    # loop uses (ProposalValidationError) so the LLM retry path can
    # self-correct on them; ``warning`` / ``info`` findings are surfaced
    # non-fatally via ``draft.warnings`` + ``draft.diagnostics`` for the
    # FE editor to render inline.
    from backend.workflows.compat import lint_workflow
    lint_steps = [
        {"step_type": s.step_type, "config": s.config or {}}
        for s in draft.steps
    ]
    lint_diags = lint_workflow(lint_steps)
    fatal = [d for d in lint_diags if d.severity == "error"]
    if fatal:
        first = fatal[0]
        raise ProposalValidationError(
            f"step {first.step_index} lint: {first.code}: {first.message}"
        )
    non_fatal = [d for d in lint_diags if d.severity != "error"]
    if non_fatal:
        draft.warnings.extend(d.message for d in non_fatal)
        draft.diagnostics = [d.model_dump() for d in non_fatal]

    # Backfill any missing human labels from the registry so the chat
    # card never falls back to the raw step_type id.
    _ensure_step_labels(draft)

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

Your job (this hop): write a plan in plain English (8-14 lines) that
explicitly REASONS ABOUT THE INSTRUMENT CHOICE before listing steps.
The plan MUST cover, in order:

  1. What is the user actually expressing a view ON? (a price move,
     a macro event, a stock, a basket, a theme). State it in one line.
  2. WHO is the natural beneficiary of that view? Reason about the
     economics — for a thematic / macro view, who actually MAKES MORE
     MONEY when the user's described move happens?
       - "Profits from RISING crude/oil" → UPSTREAM PRODUCERS like
         ONGC, OIL India (they sell crude they pull out of the ground).
         NOT IOC/BPCL/HPCL — those are refiners/marketers whose
         margins COMPRESS when crude rises because retail fuel prices
         are politically administered.
       - "Profits from FALLING crude" → flip: refiners/marketers and
         heavy crude consumers (paints, aviation, tyres) benefit.
       - "Weaker rupee" → IT/pharma exporters.
       - "RBI rate cuts" → rate-sensitive NBFCs, autos, real estate.
       - For any thematic ask, prefer a small BASKET (3-5 names) of
         the actual beneficiaries over a single arbitrary stock.
  3. Did the user impose a RISK constraint (risk-neutral, hedged,
     market-neutral, delta-neutral, defined-risk, pair, long-short)?
     If yes, a long-only structure is NOT a hedge. Either propose a
     hedged shape (long basket + short index leg / paired short / a
     defined-risk option structure if catalog supports it) OR state
     plainly in the plan: "This will be long-only directional; the
     catalog can't express a true neutral version for this view."
  4. Now list each step you'd emit (step_type + 1-line why).
  5. If a REQUIRED field (quantity, threshold, schedule time) can't
     be inferred, write "needs clarification: <field>" rather than
     defaulting.

If the user's strategy genuinely doesn't fit the linear-single-trigger
shape (e.g. "buy Monday and sell Tuesday" needs two agents), say so
explicitly so the next hop can surface a clarification rather than
fabricate an invalid draft.

Do NOT emit JSON yet. The next hop transcribes your plan into JSON.
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
  "rationale": "3-6 sentences. MUST cover (a) why these specific instruments are the right beneficiaries of the user's stated view (cite the economic mechanism: who makes more money when the move happens); (b) how each step maps to the request; (c) the material risks this carries; (d) what this automation is NOT (explicitly call out if it is not market-neutral / not a true hedge / single-stock concentration / weekly SIP rather than tactical entry). NEVER a one-liner. NEVER generic boilerplate."
}

You may ONLY use step_types from the catalog the planner referenced.

INSTRUMENT-SELECTION GUARDRAILS (hard rules):

  - "Profits from RISING crude/oil" → producers (ONGC, OIL India),
    NOT refiners/marketers (IOC, BPCL, HPCL). Picking IOC for
    rising-oil is wrong: their margins compress when crude rises.
  - "Profits from FALLING crude" → refiners + heavy crude consumers.
  - Thematic asks → small basket (3-5 names), not one arbitrary stock.
  - "Risk-neutral" / "hedged" / "market-neutral" → propose a hedged
    shape OR state explicitly in the rationale that this draft is
    NOT neutral. Never silently ship a long-only SIP under a hedge
    label.

Follow the planner's instrument list. If the planner picked the wrong
beneficiaries (e.g. listed IOC for "profits from rising oil"), CORRECT
the basket in your JSON and explain the correction in the rationale.
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
        # Raised from 900 → 2000 on 2026-06-18: the planner now reasons
        # explicitly about instrument selection (producers vs refiners,
        # hedge-vs-long-only) before listing steps, which needs the
        # headroom. Truncation here was silently capping the rationale
        # downstream and producing thin one-line summaries.
        max_output_tokens=2000,
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
        # Raised from 1500 → 4000 on 2026-06-18: the rationale field is
        # now a 3-6 sentence honest summary (instruments, risks, what
        # this is NOT) rather than a one-liner. 1500 was clipping the
        # rationale mid-sentence for any non-trivial basket draft.
        max_output_tokens=4000,
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
    the validation error embedded so the LLM can self-correct.

    Polymarket escape-hatch resolution runs BEFORE the sync validator
    on both attempts — its async/network nature can't live inside
    ``validate_draft_against_registry``.
    """
    raw = await _call_llm_for_draft(user_intent)
    try:
        parsed = _extract_json(raw)
        await resolve_polymarket_event_descriptions(parsed)
        await resolve_kalshi_event_descriptions(parsed)
        return validate_draft_against_registry(parsed)
    except ProposalValidationError as e:
        logger.info("propose_workflow LLM retry: %s", e)
        retry_raw = await _call_llm_for_draft(
            user_intent,
            extra_instruction=(
                f"Your previous response failed validation: {e}. "
                "Fix it. Output ONLY the corrected JSON object."
            ),
        )
        retry_parsed = _extract_json(retry_raw)
        await resolve_polymarket_event_descriptions(retry_parsed)
        await resolve_kalshi_event_descriptions(retry_parsed)
        return validate_draft_against_registry(retry_parsed)


# ── Mock mode (no LLM key) ───────────────────────────────────────────


def _is_mock_mode() -> bool:
    """True when no LLM provider is configured. Demo mode."""
    return not (settings.openai_api_key or settings.azure_key)


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

    # Mock-mode shortcut for news-gated workflows. Real LLM proposals
    # emit fetch.news + condition.boolean for prompts like "if RBI cuts
    # the repo rate". Offline / CI users have no LLM, so we pattern-match
    # the canonical news prompt and emit the same shape — keeps the demo
    # path and the `news_gate_on_open_buy` agentic example reproducible
    # without an API key.
    news_match = re.search(
        r"\b(rbi|sebi|repo rate|mpc|news|announce|penali[sz]e)\b", low,
    )
    if news_match:
        side = "sell" if "sell" in low else "buy"
        # Build a tight keyword set from the prompt itself — keeps the
        # mock deterministic without invoking the LLM.
        kw_pool = ["RBI", "repo rate", "MPC", "rate cut", "SEBI",
                   "penalty", "policy", "announcement"]
        keywords = [k for k in kw_pool if k.lower() in low] or ["RBI"]
        event_description = user_intent.strip()[:140]
        news_steps: list[DraftStep] = [
            DraftStep(
                step_type="trigger.schedule",
                label=f"On {cron} {tz}",
                config={"cron": cron, "timezone": tz},
            ),
            DraftStep(
                step_type="fetch.news",
                label="Check news for event",
                config={
                    "keywords": keywords,
                    "event_description": event_description,
                    "min_confidence": 0.85,
                    "hours_back": 24,
                },
            ),
            DraftStep(
                step_type="condition.boolean",
                label="Event confirmed",
                config={"left": "{{ context.1.matched }}", "value": True},
            ),
            DraftStep(
                step_type="action.place_order",
                label=f"{side.capitalize()} {qty} {symbol}",
                config={
                    "symbol": symbol,
                    "side": side,
                    "quantity": qty,
                    "order_type": "market",
                    "requires_approval": needs_approval or side == "buy",
                },
            ),
        ]
        return WorkflowDraft(
            name=f"News-gated {side} {symbol}",
            description=user_intent.strip()[:200],
            steps=news_steps,
            rationale=(
                "Mapped your request to a scheduled news check; the "
                f"{side} order fires only when the event is confirmed."
            ),
        )

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
        # Genuine offline mode (no OpenAI, no Azure). The mock path
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
