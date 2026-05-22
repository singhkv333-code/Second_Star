"""Semantic checks beyond Pydantic.

Pydantic catches type errors and shallow field constraints (period
in range, op in enum). The semantic validator runs on a fully-parsed
``Tree`` and catches:

  - Tree too deep (DSL grammar limit 4 → bigger = LLM hallucinating
    or user trying to express something too complex for v1).
  - Indicator key not in the live registry
    (``backtest_indicators.supported_indicators``). Pydantic only
    knows the field is a string; this is where we check the value.
  - "Vacuous" comparisons (constant on both sides) — the LLM
    occasionally emits ``constant(30) < constant(50)`` which
    backtests/lives trivially True and is almost always a bug.
  - "Dangling indicators" at the root — a Tree whose root is a leaf
    (not a comparison or logic) can never decide whether to fire.

Run ``semantic_validate(tree)`` from the step-config validator and
from any LLM-emission boundary.
"""
from __future__ import annotations

from typing import Iterable

from backend.workflows.dsl.schema import (
    ComparisonNode,
    ConstantNode,
    IndicatorNode,
    LogicNode,
    PriceNode,
    VolumeNode,
)


# Public — surfaces an LLM-emission failure cleanly.
class DSLValidationError(ValueError):
    """Raised when a tree parses via Pydantic but fails a semantic check."""


# Limits intentionally small for v1; revisit if real users hit them.
MAX_DEPTH = 4


# ── Public entry point ───────────────────────────────────────────────


def semantic_validate(tree) -> None:
    """Walk the tree and raise ``DSLValidationError`` on any violation.

    Returns None on success — callers chain this after Pydantic
    parsing inside the step-config validator.
    """
    _check_root_shape(tree)
    _check_depth(tree)
    _check_indicator_registry(tree)
    _check_no_vacuous_comparisons(tree)


# ── Root shape ──────────────────────────────────────────────────────


def _check_root_shape(node) -> None:
    """The root of a Tree fed to a trigger MUST resolve to a boolean,
    not a number. A leaf node alone (e.g. just an IndicatorNode) at
    the root would evaluate to a float and the engine wouldn't know
    when to fire."""
    if isinstance(node, (ComparisonNode, LogicNode)):
        return
    raise DSLValidationError(
        "Tree root must be a 'comparison' or 'logic' node — a leaf "
        "by itself doesn't tell the engine when to fire. Wrap it in "
        "a comparison, e.g. {indicator} > {constant}."
    )


# ── Depth ───────────────────────────────────────────────────────────


def _depth(node) -> int:
    """Tree depth measured as the longest path from this node to a
    leaf, counting THIS node as 1."""
    if isinstance(node, ComparisonNode):
        return 1 + max(_depth(node.left), _depth(node.right))
    if isinstance(node, LogicNode):
        return 1 + max((_depth(c) for c in node.operands), default=0)
    # Leaves: indicator / price / volume / constant
    return 1


def _check_depth(node) -> None:
    d = _depth(node)
    if d > MAX_DEPTH:
        raise DSLValidationError(
            f"Tree depth {d} exceeds v1 limit of {MAX_DEPTH}. "
            "Simplify the expression or split it into multiple workflow steps."
        )


# ── Indicator registry ──────────────────────────────────────────────


def _check_indicator_registry(node) -> None:
    keys = set()
    for n in _walk_all(node):
        if isinstance(n, IndicatorNode):
            keys.add(n.indicator.lower())
    if not keys:
        return
    # Lazy import — avoids circular import at module load.
    from backend.services.backtest_indicators import supported_indicators
    supported = set(s.lower() for s in supported_indicators())
    unknown = sorted(keys - supported)
    if unknown:
        sample = ", ".join(unknown[:5])
        more = f" (+{len(unknown) - 5} more)" if len(unknown) > 5 else ""
        supp_list = ", ".join(sorted(supported)[:12])
        raise DSLValidationError(
            f"Unknown indicator(s): {sample}{more}. Supported keys: "
            f"{supp_list}, … (see backend.services.backtest_indicators)."
        )


# ── Vacuous comparisons ─────────────────────────────────────────────


def _check_no_vacuous_comparisons(node) -> None:
    for n in _walk_all(node):
        if not isinstance(n, ComparisonNode):
            continue
        if isinstance(n.left, ConstantNode) and isinstance(n.right, ConstantNode):
            raise DSLValidationError(
                f"Vacuous comparison: both sides are constants "
                f"({n.left.value} {n.op} {n.right.value}). At least one "
                "operand must be a market value (indicator / price / volume)."
            )


# ── Internal walker ────────────────────────────────────────────────


def _walk_all(node) -> Iterable:
    """Yield this node and all descendants. Same shape as the
    evaluator's _walk but yields, doesn't compute."""
    yield node
    if isinstance(node, ComparisonNode):
        yield from _walk_all(node.left)
        yield from _walk_all(node.right)
    elif isinstance(node, LogicNode):
        for child in node.operands:
            yield from _walk_all(child)
    # Leaves yield only themselves; covered by the initial yield.
