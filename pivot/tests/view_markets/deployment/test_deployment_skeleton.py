"""Phase-4 deployment skeleton: the package imports clean and the FROZEN contract
(signatures + routing maps + trust-block shape) is present for the BUILD agents.

These are contract guardrails, not behaviour tests — the three public functions
are deliberate ``NotImplementedError`` stubs until the BUILD agents implement
them. They pin the interface so an implementation can't silently drift the
signatures, the kind→engine routing, or the trust-block key set.
"""
from __future__ import annotations

import inspect

import pytest

from backend.models import ExpressionKind
from backend.view_markets import deployment
from backend.view_markets.deployment import (
    ACTION_STEP_BY_KIND,
    ENGINE_BY_KIND,
    REQUIRES_APPROVAL,
    TRUST_BLOCK_KEYS,
    TRUST_METRICS_KEYS,
    VERDICT_RANK,
    backtest_expression,
    compare_tiers,
    deploy_expression,
)


def test_package_imports_and_exports_public_api() -> None:
    """The package imports cleanly and re-exports the three public entry points."""
    for name in ("backtest_expression", "compare_tiers", "deploy_expression"):
        assert hasattr(deployment, name)
        assert callable(getattr(deployment, name))


def test_backtest_expression_signature_is_frozen() -> None:
    sig = inspect.signature(backtest_expression)
    params = list(sig.parameters)
    assert params[:2] == ["db", "expression"]
    # keyword-only knobs the BUILD agent + compare_tiers rely on.
    assert sig.parameters["trial_group"].kind is inspect.Parameter.KEYWORD_ONLY
    assert sig.parameters["period"].kind is inspect.Parameter.KEYWORD_ONLY
    assert sig.parameters["persist"].default is True


def test_compare_tiers_signature_is_frozen() -> None:
    sig = inspect.signature(compare_tiers)
    params = list(sig.parameters)
    assert params[:2] == ["db", "view"]
    assert sig.parameters["trial_group"].kind is inspect.Parameter.KEYWORD_ONLY


def test_deploy_expression_signature_is_frozen() -> None:
    sig = inspect.signature(deploy_expression)
    params = list(sig.parameters)
    assert params[:2] == ["db", "expression"]
    assert sig.parameters["activate"].default is False
    assert sig.parameters["timing_mode"].kind is inspect.Parameter.KEYWORD_ONLY


def test_engine_routing_covers_every_expression_kind() -> None:
    """Every ExpressionKind routes to a real engine, and to a deploy action."""
    kinds = {k.value for k in ExpressionKind}
    assert kinds == set(ENGINE_BY_KIND)
    assert kinds == set(ACTION_STEP_BY_KIND)
    # Routes only to engines the codebase actually has.
    assert set(ENGINE_BY_KIND.values()) == {"portfolio", "pairs", "workflow"}
    # Deploy actions are all real register-not-execute order steps.
    assert set(ACTION_STEP_BY_KIND.values()) == {
        "action.allocate_basket",
        "action.place_order",
        "action.place_option_strategy",
    }


def test_verdict_rank_matches_the_trust_ladder() -> None:
    """The four trust verdicts are ordered promising > unproven > no_edge >
    insufficient_data — the same ladder confidence.VERDICT_CEILING uses."""
    assert set(VERDICT_RANK) == {
        "promising", "unproven", "no_edge", "insufficient_data",
    }
    assert (
        VERDICT_RANK["promising"]
        > VERDICT_RANK["unproven"]
        > VERDICT_RANK["no_edge"]
        > VERDICT_RANK["insufficient_data"]
    )


def test_trust_block_shape_is_frozen() -> None:
    """The trust block carries verdict + headline metrics + the gated dial +
    an honest-degrade flag — every key load-bearing for the card / compare."""
    for key in (
        "verdict", "confidence", "rationale", "flags", "engine",
        "backtest_run_id", "metrics", "alignment", "degraded", "data_note",
    ):
        assert key in TRUST_BLOCK_KEYS
    for key in ("total_return_pct", "max_drawdown_pct", "forward_stats",
                "monte_carlo", "sub_periods"):
        assert key in TRUST_METRICS_KEYS


def test_register_not_execute_default_is_approval_gated() -> None:
    assert REQUIRES_APPROVAL is True


@pytest.mark.parametrize(
    "fn", [backtest_expression, compare_tiers, deploy_expression]
)
def test_public_functions_are_implemented(fn) -> None:
    """All three Phase-4 entry points are implemented now (behaviour is covered
    in test_backtest.py / test_compare.py / test_deploy.py) — none is a
    ``NotImplementedError`` stub any longer."""
    import inspect

    assert "raise NotImplementedError" not in inspect.getsource(fn)
