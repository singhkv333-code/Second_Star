"""Wave C — construction-vs-automation routing regression tests.

Guards the new `construction` intent kind (build/own a basket/portfolio/
strategy expressing a view, with NO contingent action) against the
agent/automation paths, and the structural scope surgery that makes a
construction ask render a `strategy_builder_card` — never a
`workflow_draft_card`.

See docs/plans/CONSTRUCTION_VS_AUTOMATION_REDESIGN_2026-07-05.md §4 (C1-C6).
"""
import re

from backend.services.chat_service import (
    _classify_intent,
    _is_construction_intent,
    _apply_construction_scope,
    _CONSTRUCTION_FORCE_IN,
    _CONSTRUCTION_FORCE_OUT,
    _THEMATIC_BASKET_TOOLS,
    _thematic_guard_text,
)
from backend.services.thematic_map import detect_thematic_scenario
from backend.services import tool_router


# ── C1 / C6: intent classifier ─────────────────────────────────────

# The four canonical acceptance prompts must all classify as construction.
CANONICAL_CONSTRUCTION = [
    "Make me a basket of stocks that profit from a good monsoon.",
    "Build me a strategy that benefits from momentum.",
    "Create a strategy around the RBI rate decision.",
    "Design a long-term portfolio of quality stocks for the long run.",
]

# Agent / automation counter-examples — a stated contingency, an alert verb,
# an explicit agent noun, or a stated cadence → NEVER construction.
NOT_CONSTRUCTION = [
    "buy 10 INFY when RSI<30",
    "buy 10 NIFTYBEES every Friday",
    "alert me when TCS crosses 4000",
    "make me a basket rebalanced quarterly",
    "build an agent that buys the dip on RELIANCE",
    "set up a workflow to rebalance my portfolio monthly",
    "build a call spread strategy on NIFTY",  # F&O keeps options path
]


def test_canonical_prompts_classify_as_construction():
    for msg in CANONICAL_CONSTRUCTION:
        assert _is_construction_intent(msg), f"expected construction: {msg!r}"
        assert _classify_intent(msg) == "construction", msg


def test_counter_examples_are_not_construction():
    for msg in NOT_CONSTRUCTION:
        assert not _is_construction_intent(msg), f"should NOT be construction: {msg!r}"
        assert _classify_intent(msg) != "construction", msg


def test_contingency_flips_construction_off():
    # Same construction shape, but a contingency word appended → not construction.
    assert _is_construction_intent("build me a momentum strategy")
    assert not _is_construction_intent("build me a momentum strategy, rebalance every week")
    assert not _is_construction_intent("build me a momentum strategy and alert me daily")


def test_explicit_agent_noun_wins():
    assert not _is_construction_intent("build me an automation strategy")
    assert _classify_intent("build me an automation strategy") == "agent"


# ── C2 / C6: construction scope surgery ─────────────────────────────

def test_construction_scope_forces_builder_in_workflow_out():
    base = frozenset({
        "propose_workflow", "propose_dsl_workflow",
        "propose_scheduled_order", "place_market_order",
        "get_live_price", "get_holdings",
    })
    scoped = _apply_construction_scope(base)
    # Builder + read/vet tools present.
    assert "build_strategy" in scoped
    assert "ask_user_dynamic" in scoped
    assert _CONSTRUCTION_FORCE_IN <= scoped
    # No workflow / macro / immediate-order tool survives.
    assert not (_CONSTRUCTION_FORCE_OUT & scoped)
    assert "propose_workflow" not in scoped
    assert "place_market_order" not in scoped
    # Unrelated read tools are preserved.
    assert "get_holdings" in scoped


def test_construction_scope_noop_in_whitelist_mode():
    assert _apply_construction_scope(None) is None


def test_force_in_and_out_are_disjoint():
    assert not (_CONSTRUCTION_FORCE_IN & _CONSTRUCTION_FORCE_OUT)


# ── C3 / C6: thematic path steers to the basket card, not a workflow ─

def test_thematic_guard_no_longer_instructs_workflow_card():
    scenario = detect_thematic_scenario(
        "make me a basket that profits from a good monsoon"
    )
    assert scenario is not None
    guard = _thematic_guard_text(
        "make me a basket that profits from a good monsoon, I have 2 lakh",
        scenario,
    )
    # Steers to build_strategy + strategy_builder_card ...
    assert "build_strategy" in guard
    assert "strategy_builder_card" in guard
    # ... and no longer POSITIVELY instructs a workflow card for the basket
    # (the only remaining mentions are the explicit "do NOT" prohibitions).
    assert "render a `workflow_draft_card`" not in guard
    assert "call `propose_workflow` with one" not in guard
    for m in re.finditer(r"workflow_draft_card|propose_workflow", guard):
        window = guard[max(0, m.start() - 60):m.start()]
        assert "do NOT" in window or "not" in window.lower(), (
            f"workflow mention not in a prohibition context: ...{window}"
        )
    # symbols= pins the seed winners.
    assert "symbols=[" in guard


def test_thematic_basket_toolset_excludes_workflow_drafters():
    assert "build_strategy" in _THEMATIC_BASKET_TOOLS
    assert "propose_workflow" not in _THEMATIC_BASKET_TOOLS
    assert "propose_dsl_workflow" not in _THEMATIC_BASKET_TOOLS


# ── C5: tool_router construction rule + module injection ────────────

def test_router_surfaces_builder_on_factor_and_positioning_prompts():
    for msg in [
        "build me a strategy that benefits from momentum",
        "give me a low-vol basket of quality names",
        "a portfolio of stocks that profit from a rate cut",
    ]:
        names = tool_router.select_tool_names(msg)
        assert "build_strategy" in names, msg
        assert "ask_user_dynamic" in names, msg


def test_module_selection_injects_baskets_on_construction_verbs():
    for msg in [
        "build me a strategy that benefits from momentum",
        "design a long-term portfolio of quality stocks",
        "create a basket of monsoon winners",
    ]:
        mods = tool_router.select_prompt_modules(msg)
        assert "baskets" in mods, msg


# ── C6: handler parity — both handlers use the identical branch ─────

def test_both_handlers_share_construction_branch():
    """handle() and handle_stream() must apply the SAME construction scope
    surgery — the known drift trap. Assert the branch text appears exactly
    twice (once per handler) and calls the shared helper."""
    import inspect
    import backend.services.chat_service as cs

    src = inspect.getsource(cs)
    # The shared-helper call must appear in both handlers.
    n_calls = len(re.findall(
        r"selected_names = _apply_construction_scope\(selected_names\)", src
    ))
    assert n_calls == 2, f"expected the construction branch in BOTH handlers, found {n_calls}"
    # The intent flag must be derived identically in both.
    n_flags = len(re.findall(
        r'is_construction_intent = intent_kind == "construction"', src
    ))
    assert n_flags == 2, f"expected is_construction_intent in BOTH handlers, found {n_flags}"
