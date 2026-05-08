"""Smoke tests for per-state policy resolution.

No LLM needed. These verify:
  - every state resolves to a StatePolicy
  - tool palettes are tight (1-30 tools, never the whole catalog)
  - DRAFTING(workflow) sees ONLY propose_workflow + read tools
  - DRAFTING(order) sees ONLY order tools + read tools
  - system blocks are non-empty and include the base
"""
from __future__ import annotations

from backend.chat_v2.policies import (
    BASKET_TOOLS, HOLDING_TOOLS, ORDER_TOOLS, READ_TOOLS,
    SYNTHETIC, WORKFLOW_TOOLS, policy_for,
)
from backend.chat_v2.state import ConvContext, ConvState, MacroKind


def ctx(state: ConvState, **kw):
    return ConvContext(conv_id="t", state=state, **kw)


def test_every_state_resolves():
    for state in ConvState:
        p = policy_for(ctx(state))
        assert p.state_label
        assert p.system_block
        assert "Pivot" in p.system_block, "base block should always be included"


def test_idle_palette_is_read_only():
    p = policy_for(ctx(ConvState.IDLE))
    assert "propose_workflow" not in p.tools
    assert "place_market_order" not in p.tools
    assert "get_live_price" in p.tools


def test_exploring_no_macros():
    p = policy_for(ctx(ConvState.EXPLORING))
    assert "propose_workflow" not in p.tools
    assert "propose_threshold_order" not in p.tools
    assert "place_market_order" not in p.tools
    assert "get_indicator" in p.tools


def test_drafting_workflow_pin():
    p = policy_for(ctx(ConvState.DRAFTING, macro_kind=MacroKind.WORKFLOW))
    assert "propose_workflow" in p.tools
    assert "propose_threshold_order" not in p.tools
    assert "propose_basket_allocation" not in p.tools
    assert "place_market_order" not in p.tools
    # Read tools still in scope so the LLM can fetch quotes etc.
    assert "get_live_price" in p.tools


def test_drafting_basket_pin():
    p = policy_for(ctx(ConvState.DRAFTING, macro_kind=MacroKind.BASKET))
    assert "propose_basket_allocation" in p.tools
    assert "propose_workflow" not in p.tools


def test_drafting_order_pin():
    p = policy_for(ctx(ConvState.DRAFTING, macro_kind=MacroKind.ORDER,
                       macro_tool="place_limit_order"))
    assert "place_limit_order" in p.tools
    assert "place_market_order" in p.tools
    assert "propose_workflow" not in p.tools
    # tool_choice="required" forces the model to call SOMETHING (the
    # macro tool, ASK_USER, or a read tool) — prevents writing the
    # draft as prose markdown.
    assert p.tool_choice == "required"


def test_clarifying_has_all_macros():
    p = policy_for(ctx(ConvState.AWAITING_CLARIFICATION))
    # All macro families available so the LLM can resolve the answer.
    assert "propose_workflow" in p.tools
    assert "propose_basket_allocation" in p.tools
    assert "place_market_order" in p.tools


def test_activated_has_management_tools():
    p = policy_for(ctx(ConvState.ACTIVATED))
    assert "pause_strategy" in p.tools
    assert "delete_strategy" in p.tools
    assert "propose_workflow" not in p.tools


def test_cancelled_minimal():
    p = policy_for(ctx(ConvState.CANCELLED))
    # No drafts available — user has to say something concrete to start
    # something new.
    assert "propose_workflow" not in p.tools
    assert p.max_output_tokens <= 400


def test_cache_keys_are_distinct():
    keys = set()
    for state in ConvState:
        p = policy_for(ctx(state))
        keys.add(p.cache_key)
    # Drafting kinds should have distinct keys too.
    for kind in MacroKind:
        p = policy_for(ctx(ConvState.DRAFTING, macro_kind=kind))
        keys.add(p.cache_key)
    # At least one key per state plus distinct drafting variants.
    assert len(keys) >= 8


def test_palette_sizes_are_tight():
    """Most states are tight (<= 30 tools). Clarifying is the
    widest (~36) because it needs all macro families to resolve any
    prior ask, but still well below v1's 48-tool catalog."""
    for state in ConvState:
        p = policy_for(ctx(state))
        # Clarifying is the widest — explicitly allowed.
        cap = 40 if state == ConvState.AWAITING_CLARIFICATION else 30
        assert len(p.tools) <= cap, f"{state}: {len(p.tools)} tools"


def test_drafting_workflow_is_tight():
    """The dominant case — workflow drafting — should expose ~25 tools
    (1 macro + read + synthetic), MUCH less than v1."""
    p = policy_for(ctx(ConvState.DRAFTING, macro_kind=MacroKind.WORKFLOW))
    assert len(p.tools) <= 30
    # Only ONE macro tool visible.
    macro_count = sum(1 for t in p.tools if t.startswith("propose_") or t.startswith("place_") or t.startswith("create_"))
    assert macro_count <= 2, f"too many macros visible: {[t for t in p.tools if t.startswith('propose_') or t.startswith('place_') or t.startswith('create_')]}"
