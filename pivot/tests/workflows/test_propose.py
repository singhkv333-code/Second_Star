"""Tests for backend/workflows/propose.py.

Coverage:
  - Mock mode end-to-end: demo prompt → 5-step canonical draft, every
    config validates against the registry.
  - Mock-mode parsing: cron extraction, symbol pickup, threshold
    parsing, side detection (buy/sell), notify channel.
  - Validation: extract_json tolerates markdown fences + leading prose,
    validate_draft_against_registry rejects unknown step_type, rejects
    non-trigger at step 0, rejects bad config.
  - LLM path: stub the LLM call to return well-formed / malformed /
    fix-on-retry JSON. Verify retry-on-validation-fail.
  - Last-resort fallback: LLM fails twice → mock draft surfaced with
    a warning explaining the LLM failure.
"""
from __future__ import annotations

import json

import pytest

from backend.workflows import propose as propose_mod
from backend.workflows.propose import (
    ProposalValidationError,
    WorkflowDraft,
    _build_catalog_summary,
    _extract_json,
    _is_mock_mode,
    _mock_propose,
    propose_workflow_async,
    validate_draft_against_registry,
)


# ── Mock mode (no LLM keys configured) ───────────────────────────────


def test_demo_prompt_produces_canonical_5_step_draft() -> None:
    """The demo path prompt should map to schedule → portfolio →
    condition → place_order → notify."""
    intent = (
        "Every weekday at 3:55 PM IST, if my buying power is over "
        "₹50,000, buy 10 shares of RELIANCE and notify me by email."
    )
    draft = _mock_propose(intent)
    assert [s.step_type for s in draft.steps] == [
        "trigger.schedule",
        "fetch.portfolio",
        "condition.numeric",
        "action.place_order",
        "notify.message",
    ]
    # Quantity, symbol, threshold all parsed correctly.
    place = draft.steps[3]
    assert place.config["symbol"] == "RELIANCE"
    assert place.config["quantity"] == 10
    assert place.config["side"] == "buy"
    assert place.config["requires_approval"] is True  # buy defaults to approval
    cond = draft.steps[2]
    assert cond.config["right"] == 50000
    assert cond.config["left"] == "{{ context.1.buying_power }}"
    notif = draft.steps[4]
    assert notif.config["channel"] == "email"


def test_mock_3_step_when_no_condition_clause() -> None:
    """Sell with no 'if' / no condition → trigger + action + notify."""
    intent = "Every Monday at 9:30 sell 5 shares of QQQ and SMS me."
    draft = _mock_propose(intent)
    assert [s.step_type for s in draft.steps] == [
        "trigger.schedule",
        "action.place_order",
        "notify.message",
    ]
    assert draft.steps[1].config["side"] == "sell"
    assert draft.steps[1].config["quantity"] == 5
    assert draft.steps[1].config["symbol"] == "QQQ"
    assert draft.steps[2].config["channel"] == "sms"


def test_mock_cron_default_when_no_time() -> None:
    """No time mentioned → falls back to 09:30 weekday IST."""
    intent = "Buy 1 RELIANCE on weekdays."
    draft = _mock_propose(intent)
    assert draft.steps[0].step_type == "trigger.schedule"
    assert draft.steps[0].config["timezone"] == "Asia/Kolkata"
    assert "1-5" in draft.steps[0].config["cron"]


def test_mock_pm_time_converts_to_24h() -> None:
    """3:55 PM should produce hh=15."""
    intent = "Every weekday at 3:55 PM IST buy 1 RELIANCE."
    draft = _mock_propose(intent)
    cron = draft.steps[0].config["cron"]
    # cron is "MM HH * * 1-5"
    parts = cron.split()
    assert parts[1] == "15"
    assert parts[0] == "55"


def test_mock_draft_validates_against_registry() -> None:
    """Every mock draft must be a valid registry-shape draft.

    This is what propose_workflow_async asserts before returning —
    test it directly so a registry change can't silently desync the
    mock without a CI red."""
    intent = "Every weekday at 09:30 IST if my buying power > 25000 buy 2 INFY and email me."
    draft = _mock_propose(intent)
    validated = validate_draft_against_registry(draft.model_dump())
    assert isinstance(validated, WorkflowDraft)


# ── validate_draft_against_registry ──────────────────────────────────


def test_validate_rejects_unknown_step_type() -> None:
    bad = {
        "name": "x", "description": None, "rationale": None, "warnings": [],
        "steps": [{"step_type": "trigger.invented", "label": None, "config": {}}],
    }
    with pytest.raises(ProposalValidationError, match="unknown step_type"):
        validate_draft_against_registry(bad)


def test_validate_rejects_non_trigger_at_step_0() -> None:
    bad = {
        "name": "x", "description": None, "rationale": None, "warnings": [],
        "steps": [{"step_type": "fetch.portfolio", "label": None, "config": {}}],
    }
    with pytest.raises(ProposalValidationError, match="step 0 must be a trigger"):
        validate_draft_against_registry(bad)


def test_validate_rejects_trigger_after_step_0() -> None:
    bad = {
        "name": "x", "description": None, "rationale": None, "warnings": [],
        "steps": [
            {
                "step_type": "trigger.schedule", "label": None,
                "config": {"cron": "0 9 * * 1-5", "timezone": "UTC"},
            },
            {"step_type": "trigger.manual", "label": None, "config": {}},
        ],
    }
    with pytest.raises(ProposalValidationError, match="trigger.* may only appear at step 0"):
        validate_draft_against_registry(bad)


def test_validate_rejects_bad_config() -> None:
    """trigger.schedule without `cron` → registry rejects."""
    bad = {
        "name": "x", "description": None, "rationale": None, "warnings": [],
        "steps": [
            {
                "step_type": "trigger.schedule", "label": None,
                "config": {"timezone": "UTC"},  # missing cron
            },
        ],
    }
    with pytest.raises(ProposalValidationError, match="config invalid"):
        validate_draft_against_registry(bad)


def test_validate_rejects_empty_steps() -> None:
    with pytest.raises(ProposalValidationError, match="at least one step"):
        validate_draft_against_registry({
            "name": "x", "description": None, "rationale": None,
            "warnings": [], "steps": [],
        })


# ── _extract_json (LLM noise tolerance) ──────────────────────────────


def test_extract_json_strips_markdown_fences() -> None:
    raw = "```json\n{\"a\": 1, \"b\": 2}\n```"
    assert _extract_json(raw) == {"a": 1, "b": 2}


def test_extract_json_tolerates_leading_prose() -> None:
    raw = 'Here is your workflow:\n{"name": "x", "steps": []}'
    assert _extract_json(raw) == {"name": "x", "steps": []}


def test_extract_json_handles_nested_braces() -> None:
    raw = '{"name": "x", "config": {"nested": {"deep": 1}}}'
    assert _extract_json(raw)["config"]["nested"]["deep"] == 1


def test_extract_json_raises_on_no_json() -> None:
    with pytest.raises(ProposalValidationError, match="did not return JSON"):
        _extract_json("just prose, no json here")


def test_extract_json_raises_on_malformed() -> None:
    with pytest.raises(ProposalValidationError, match="malformed|unbalanced"):
        _extract_json('{"unterminated": "string')


# ── catalog summary ──────────────────────────────────────────────────


def test_catalog_summary_includes_every_step_type() -> None:
    summary = _build_catalog_summary()
    # Spot-check: a trigger, a fetch, a condition, an action, a notify, a control.
    for step_type in [
        "trigger.schedule", "fetch.portfolio", "condition.numeric",
        "action.place_order", "notify.message", "control.skip_if",
    ]:
        assert step_type in summary, f"missing {step_type} in catalog summary"
    # Triggers tagged TRIGGER.
    assert "TRIGGER" in summary


# ── propose_workflow_async with stubbed LLM ──────────────────────────


@pytest.mark.asyncio
async def test_propose_in_mock_mode_uses_pattern_matcher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When SARVAM/OpenAI keys are empty, route through _mock_propose
    and never call the LLM."""
    monkeypatch.setattr(propose_mod.settings, "sarvam_api_key", "")
    monkeypatch.setattr(propose_mod.settings, "openai_api_key", "")
    assert _is_mock_mode() is True

    async def _no_llm(*args, **kwargs):  # pragma: no cover
        raise AssertionError("must not call LLM in mock mode")

    monkeypatch.setattr(propose_mod, "_call_llm_for_draft", _no_llm)
    draft = await propose_workflow_async(
        "Every weekday at 09:30 IST if buying power > 25000 buy 1 INFY and email me."
    )
    assert draft.steps[0].step_type == "trigger.schedule"
    assert any(s.step_type == "action.place_order" for s in draft.steps)


@pytest.mark.asyncio
async def test_propose_with_llm_happy_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stub LLM to return valid JSON on first try."""
    monkeypatch.setattr(propose_mod.settings, "sarvam_api_key", "x")
    monkeypatch.setattr(propose_mod.settings, "openai_api_key", "")
    valid = json.dumps({
        "name": "Buy 1 INFY",
        "description": "Daily INFY",
        "rationale": "schedule + action",
        "warnings": [],
        "steps": [
            {
                "step_type": "trigger.schedule", "label": "weekdays 09:30",
                "config": {"cron": "30 9 * * 1-5", "timezone": "Asia/Kolkata"},
            },
            {
                "step_type": "action.place_order", "label": "buy 1 INFY",
                "config": {
                    "symbol": "INFY", "side": "buy", "quantity": 1,
                    "order_type": "market", "requires_approval": True,
                },
            },
        ],
    })
    calls: list[str] = []

    async def _stub_llm(intent: str, *, extra_instruction: str = "") -> str:
        calls.append(extra_instruction)
        return valid

    monkeypatch.setattr(propose_mod, "_call_llm_for_draft", _stub_llm)
    draft = await propose_workflow_async("daily INFY")
    assert draft.name == "Buy 1 INFY"
    # Single call — no retry needed.
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_propose_with_llm_retries_on_validation_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """First call returns invalid (unknown step_type); second call
    fixed by the retry instruction. Final draft is the corrected one."""
    monkeypatch.setattr(propose_mod.settings, "sarvam_api_key", "x")
    monkeypatch.setattr(propose_mod.settings, "openai_api_key", "")
    bad = json.dumps({
        "name": "x", "description": None, "rationale": None, "warnings": [],
        "steps": [{"step_type": "trigger.invented", "label": None, "config": {}}],
    })
    good = json.dumps({
        "name": "x", "description": None, "rationale": None, "warnings": [],
        "steps": [
            {
                "step_type": "trigger.manual", "label": None, "config": {},
            },
        ],
    })
    responses = iter([bad, good])
    calls: list[str] = []

    async def _stub_llm(intent: str, *, extra_instruction: str = "") -> str:
        calls.append(extra_instruction)
        return next(responses)

    monkeypatch.setattr(propose_mod, "_call_llm_for_draft", _stub_llm)
    draft = await propose_workflow_async("Manual run only.")
    assert draft.steps[0].step_type == "trigger.manual"
    assert len(calls) == 2
    # Second call's instruction mentions the validation error so the
    # LLM has actionable feedback.
    assert "failed validation" in calls[1].lower()


@pytest.mark.asyncio
async def test_propose_falls_back_to_mock_after_two_llm_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both LLM attempts fail → return a mock draft with a warning so
    the chat surfaces SOMETHING actionable instead of an exception."""
    monkeypatch.setattr(propose_mod.settings, "sarvam_api_key", "x")
    monkeypatch.setattr(propose_mod.settings, "openai_api_key", "")

    async def _always_bad(intent: str, *, extra_instruction: str = "") -> str:
        return "this is not json at all"

    monkeypatch.setattr(propose_mod, "_call_llm_for_draft", _always_bad)
    draft = await propose_workflow_async(
        "Every weekday at 09:30 IST buy 1 RELIANCE and email me."
    )
    # Mock fallback runs.
    assert draft.steps[0].step_type == "trigger.schedule"
    # Warning is set so the UI can flag "best effort" to the user.
    assert any("LLM proposal failed" in w for w in draft.warnings)


@pytest.mark.asyncio
async def test_propose_raises_on_empty_intent() -> None:
    with pytest.raises(ProposalValidationError, match="empty"):
        await propose_workflow_async("")
