"""Tier-3 disambiguation flow.

Phase-0 spec: at most THREE multi-choice questions covering the
three things that genuinely change Tier-3 firing behaviour:

  1. exact event — what "the event happened" means (first major
     call, multi-source consensus, official certification,
     prediction-market resolution).
  2. resolution criterion — how many secondary confirmations are
     required.
  3. retraction policy — what to do if the event is retracted
     within the safety window.

These are templated rather than LLM-generated. The downside is
fixed phrasing; the upside is determinism, zero LLM cost on
disambiguation, and easy testability. The Phase-4 parser already
populates sensible Tier-3 defaults, so the disambiguation merely
lets the user confirm or override.

``apply_option`` and ``apply_answers`` mutate a pending spec dict
(NOT a ParsedSpec — the disambiguation operates on the same dict
shape that ends up in the DB columns) and return the result. Pure
functions; no DB.
"""
from __future__ import annotations

import copy
from typing import Any

from backend.news_events.parsing.event_spec_parser import ParsedSpec
from backend.news_events.schemas import (
    DisambiguationOption,
    DisambiguationQuestion,
)


# ── Question templates ───────────────────────────────────────────────


_Q_EXACT_EVENT = DisambiguationQuestion(
    id="exact_event",
    text=(
        "Which signal counts as the event having happened? Pick the "
        "strictest definition you're comfortable with — stricter "
        "definitions reduce false positives at the cost of latency."
    ),
    options=[
        DisambiguationOption(
            id="first_major_call",
            label=(
                "First major call by a top wire service "
                "(AP, Reuters, BBC, Bloomberg)"
            ),
            apply={
                "resolution_criteria": {
                    "min_secondary_confirmations": 0,
                },
                "description_suffix": (
                    " — fires on the first major wire-service call."
                ),
            },
        ),
        DisambiguationOption(
            id="multi_source_consensus",
            label="Multi-source consensus (at least 2 independent sources)",
            apply={
                "resolution_criteria": {
                    "min_secondary_confirmations": 1,
                },
                "description_suffix": (
                    " — fires only on multi-source consensus."
                ),
            },
        ),
        DisambiguationOption(
            id="official_certification",
            label="Wait for official certification (regulator / election commission)",
            apply={
                "resolution_criteria": {
                    "min_secondary_confirmations": 2,
                    "conflict_policy": "hold",
                },
                "description_suffix": (
                    " — waits for official certification."
                ),
            },
        ),
        DisambiguationOption(
            id="prediction_market_resolution",
            label=(
                "Prediction market resolution (e.g. Polymarket settled "
                "above the threshold)"
            ),
            apply={
                "resolution_criteria": {
                    "min_secondary_confirmations": 1,
                    "prediction_market_threshold": 0.85,
                },
                "description_suffix": (
                    " — confirmed via prediction-market resolution."
                ),
            },
        ),
    ],
)


_Q_RETRACTION = DisambiguationQuestion(
    id="retraction_policy",
    text=(
        "If the event is retracted within the safety window, what "
        "should Pivot do?"
    ),
    options=[
        DisambiguationOption(
            id="cancel_pending_approvals",
            label="Cancel any orders awaiting my approval (default)",
            apply={
                "retraction_policy": {
                    "action": "cancel_pending_approvals",
                    "safety_window_minutes": 240,
                },
            },
        ),
        DisambiguationOption(
            id="cancel_and_alert",
            label=(
                "Cancel pending orders AND surface an explicit alert"
            ),
            apply={
                "retraction_policy": {
                    "action": "cancel_and_alert",
                    "safety_window_minutes": 240,
                },
            },
        ),
        DisambiguationOption(
            id="ignore",
            label=(
                "Ignore — proceed with the original plan even if the "
                "event is later retracted (NOT recommended for Tier 3)"
            ),
            apply={
                "retraction_policy": {
                    "action": "ignore",
                    "safety_window_minutes": 0,
                },
            },
        ),
    ],
)


# Question ordering — first to last. Keep it short; 2 questions are
# the sweet spot for Tier 3.
_TIER3_QUESTIONS: list[DisambiguationQuestion] = [
    _Q_EXACT_EVENT,
    _Q_RETRACTION,
]


def questions_for(tier: str) -> list[DisambiguationQuestion]:
    """Return the question list for a given tier. Tier 1/2 returns
    an empty list — those don't require disambiguation."""
    if tier == "tier3":
        return list(_TIER3_QUESTIONS)
    return []


# ── Apply answers to a pending spec ──────────────────────────────────


def _deep_merge(base: dict, patch: dict) -> dict:
    """Shallow-deep merge for the spec dicts. Patch wins; nested
    dicts are merged one level deep. Lists are replaced wholesale."""
    out = dict(base)
    for key, val in patch.items():
        if (
            isinstance(val, dict)
            and isinstance(out.get(key), dict)
        ):
            out[key] = _deep_merge(out[key], val)
        else:
            out[key] = val
    return out


def apply_option(
    pending_spec: dict[str, Any],
    *,
    question: DisambiguationQuestion,
    option_id: str,
) -> dict[str, Any]:
    """Apply one selected option to the pending spec dict. Returns
    the mutated copy; the original is not modified."""
    selected = next(
        (o for o in question.options if o.id == option_id), None
    )
    if selected is None:
        raise ValueError(
            f"option_id {option_id!r} not in question {question.id!r}"
        )
    out = copy.deepcopy(pending_spec)

    apply = dict(selected.apply or {})
    for key in ("resolution_criteria", "retraction_policy"):
        patch = apply.pop(key, None)
        if not isinstance(patch, dict):
            continue
        existing = out.get(key) or {}
        if not isinstance(existing, dict):
            existing = {}
        out[key] = _deep_merge(existing, patch)

    suffix = apply.pop("description_suffix", None)
    if isinstance(suffix, str) and suffix.strip():
        out["description"] = (
            str(out.get("description", "")).rstrip(".")
            + suffix
        )

    return out


def apply_answers(
    pending_spec: dict[str, Any],
    *,
    answers: dict[str, str],
    questions: list[DisambiguationQuestion],
) -> dict[str, Any]:
    """Apply ALL answered questions in order. Unanswered questions
    are left untouched (defaults from the parser remain in effect).
    """
    out = pending_spec
    for q in questions:
        option_id = answers.get(q.id)
        if not option_id:
            continue
        out = apply_option(out, question=q, option_id=option_id)
    return out


def parsed_spec_to_pending_dict(parsed: ParsedSpec) -> dict[str, Any]:
    """Coerce a ParsedSpec into the dict shape that flows through
    disambiguation. The state machine in ``specs.py`` reads this
    shape directly into the DB columns."""
    return {
        "description": parsed.description,
        "tier": parsed.tier,
        "keyword_set": parsed.keyword_set.model_dump(),
        "resolution_criteria": parsed.resolution_criteria.model_dump(),
        "retraction_policy": parsed.retraction_policy.model_dump(),
    }
