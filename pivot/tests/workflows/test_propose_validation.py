"""propose_workflow validation suite — 10+ NL prompts mapped to drafts.

Per ARCHITECTURE.md Day 6 mandate ("Reviewer validates with 10 different
natural-language prompts"). We force mock mode so the test is
deterministic and runs in CI without an LLM key.

For every prompt, we assert:
  - The returned draft validates against the registry (every step config
    Pydantic-validates; every step_type is in the catalog).
  - Step 0 is a `trigger.*` (single-track invariant).
  - The draft has a non-empty name and at least one step.

Adding a new prompt is one line. The mock pattern matcher is
intentionally simple — catching prompts where it produces a poorly-shaped
draft is exactly the point of this test.
"""
from __future__ import annotations

import asyncio

import pytest

from backend.workflows import propose as propose_mod
from backend.workflows.propose import (
    WorkflowDraft,
    propose_workflow_async,
    validate_draft_against_registry,
)
from backend.workflows.registry import STEP_REGISTRY


@pytest.fixture(autouse=True)
def _force_mock_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force the deterministic mock path so this suite is hermetic."""
    monkeypatch.setattr(propose_mod.settings, "sarvam_api_key", "")
    monkeypatch.setattr(propose_mod.settings, "openai_api_key", "")


# ── The 10 prompts ──────────────────────────────────────────────────


_PROMPTS: list[tuple[str, str]] = [
    (
        "demo_canonical",
        "Every weekday at 3:55 PM IST, if my buying power is over ₹50,000, "
        "buy 10 shares of RELIANCE and notify me by email.",
    ),
    (
        "morning_sip_ish",
        "Every weekday at 9:30 AM IST buy 1 INFY and email me.",
    ),
    (
        "sell_with_sms",
        "Every Monday at 9:30 sell 5 shares of QQQ and SMS me.",
    ),
    (
        "no_condition_no_notify",
        "Buy 1 RELIANCE on weekdays.",
    ),
    (
        "with_buying_power_floor",
        "If my buying power is above 25000 buy 2 INFY at 09:15 weekdays "
        "and notify me.",
    ),
    (
        "ask_for_approval_explicit",
        "Every weekday at 14:30 buy 5 TCS, ask me first before placing the order.",
    ),
    (
        "evening_sell_push",
        "Every weekday at 3:25 PM sell 10 HDFC and push notify me.",
    ),
    (
        "no_threshold_just_buy_and_email",
        "Buy 3 TCS every weekday at 10:15 and email me.",
    ),
    (
        "sell_short_no_threshold",
        "Sell 4 INFY every weekday at 14:00 and SMS me.",
    ),
    (
        "high_threshold_large_qty",
        "If my buying power exceeds 1,00,000, buy 25 RELIANCE every weekday "
        "at 11:00 and notify me by email.",
    ),
]


@pytest.mark.parametrize("name,prompt", _PROMPTS, ids=[p[0] for p in _PROMPTS])
@pytest.mark.asyncio
async def test_prompt_produces_registry_valid_draft(
    name: str, prompt: str,
) -> None:
    """Every prompt → a draft that survives full registry validation."""
    draft = await propose_workflow_async(prompt)
    assert isinstance(draft, WorkflowDraft)
    assert draft.name and len(draft.name) > 0
    assert draft.steps, f"draft for {name!r} has no steps"

    # Step 0 must be a trigger.
    first = draft.steps[0]
    assert first.step_type.startswith("trigger."), (
        f"{name}: step 0 should be a trigger, got {first.step_type!r}"
    )

    # Re-validate against the registry — this is the same check
    # the real LLM path runs. propose_workflow_async already calls
    # this internally; calling it again is a regression guard against
    # someone bypassing the validation in the future.
    validated = validate_draft_against_registry(draft.model_dump())
    assert validated.name == draft.name


@pytest.mark.parametrize("name,prompt", _PROMPTS, ids=[p[0] for p in _PROMPTS])
@pytest.mark.asyncio
async def test_prompt_uses_only_known_step_types(
    name: str, prompt: str,
) -> None:
    """No step type drift — every step must be in the catalog. (Catches
    a future mock-pattern change that introduces a new step_type before
    it's registered.)"""
    draft = await propose_workflow_async(prompt)
    for idx, step in enumerate(draft.steps):
        assert step.step_type in STEP_REGISTRY, (
            f"{name}: step {idx} uses unknown step_type {step.step_type!r}"
        )


@pytest.mark.asyncio
async def test_canonical_demo_quality_attributes() -> None:
    """The canonical demo prompt must produce the canonical 5-step
    workflow exactly. This is the single prompt the demo recording
    uses — drift here breaks the recording."""
    intent = (
        "Every weekday at 3:55 PM IST, if my buying power is over ₹50,000, "
        "buy 10 shares of RELIANCE and notify me by email."
    )
    draft = await propose_workflow_async(intent)
    assert [s.step_type for s in draft.steps] == [
        "trigger.schedule",
        "fetch.portfolio",
        "condition.numeric",
        "action.place_order",
        "notify.message",
    ]
    place = draft.steps[3]
    assert place.config["symbol"] == "RELIANCE"
    assert place.config["quantity"] == 10
    assert place.config["side"] == "buy"
    assert place.config["requires_approval"] is True
    notify = draft.steps[4]
    # v1 schema forces channel to 'push' — see NotifyMessageConfig.
    assert notify.config["channel"] == "push"
    # Must read naturally — "Bought" not "Buyed".
    assert "Bought" in notify.config["template"]
