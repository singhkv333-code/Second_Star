"""Tests for the role-aware prompt assembler.

Covers:
  - Each role assembles the expected layers
  - Domain primer is included unconditionally
  - User context block appears when supplied, omitted when not
  - Extra context preserves caller-supplied keys
  - The cached loaders can be reset between tests
"""
from __future__ import annotations

import pytest

from backend.prompts import build_system_prompt
from backend.prompts.assembler import (
    UserContext,
    _load_chat_system_md,
    _load_domain_primer,
    reload_prompts,
)


@pytest.fixture(autouse=True)
def _reload_prompts():
    reload_prompts()
    yield
    reload_prompts()


def test_chat_role_includes_system_md_and_primer():
    out = build_system_prompt("chat")
    sysmd = _load_chat_system_md()
    primer = _load_domain_primer()
    assert sysmd in out
    assert primer in out
    # Primer comes after system instructions.
    assert out.index(sysmd) < out.index(primer)


def test_propose_workflow_role_uses_propose_instructions_not_chat():
    out = build_system_prompt("propose_workflow")
    sysmd = _load_chat_system_md()
    # The propose role has its own instructions block; the chat
    # system.md should NOT be the leading block.
    assert "translate the user" in out.lower()
    # Even if propose copy mentions tools generally, it shouldn't pull
    # in the chat-role file verbatim.
    assert sysmd not in out


def test_narrate_tool_result_role():
    out = build_system_prompt("narrate_tool_result")
    assert "executed a tool" in out
    assert _load_domain_primer() in out


def test_user_context_block_appears_when_supplied():
    ctx = UserContext(
        user_id=42,
        full_name="Karan",
        portfolio_total_inr=100000.0,
        holdings_count=5,
        active_workflows_count=3,
    )
    out = build_system_prompt("chat", user_context=ctx)
    assert "## User context" in out
    assert "Karan" in out
    assert "₹1,00,000" in out
    assert "5 symbols" in out
    assert "3" in out  # active workflows


def test_user_context_block_omitted_when_no_context():
    out = build_system_prompt("chat")
    assert "## User context" not in out


def test_extra_context_appears_under_additional_section():
    out = build_system_prompt(
        "propose_workflow",
        extra_context={"Catalog": "trigger.schedule, action.place_order, ..."},
    )
    assert "## Additional context" in out
    assert "### Catalog" in out
    assert "trigger.schedule" in out


def test_domain_primer_mentions_indian_specifics():
    """Sanity guard: if someone edits the primer to be generic, this
    catches it. The primer's value is the domain knowledge."""
    primer = _load_domain_primer()
    assert "Indian retail" in primer
    assert "NSE" in primer or "BSE" in primer
    assert "RBI" in primer
    assert "₹" in primer
    # Parameter ranges that prevent the model from suggesting nonsense
    assert "RSI" in primer
    assert "stop loss" in primer.lower() or "stoploss" in primer.lower()


def test_unknown_role_falls_back_safely():
    """A future role that's been added to PromptRole but not
    ROLE_INSTRUCTIONS shouldn't crash — it just gets the default
    chat-style fallback."""
    out = build_system_prompt("correlation_decompose")  # placeholder
    # Doesn't blow up; primer still attached.
    assert _load_domain_primer() in out
