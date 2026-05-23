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
    AggregateNode,
    ComparisonNode,
    ConditionalNode,
    ConstantNode,
    IndicatorNode,
    LogicNode,
    PositionNode,
    PriceNode,
    VolumeNode,
)


# Public — surfaces an LLM-emission failure cleanly.
class DSLValidationError(ValueError):
    """Raised when a tree parses via Pydantic but fails a semantic check."""


# Limits intentionally small for v1; lifted in Phase C.0 from 4 to 6
# because aggregators + conditionals naturally push tree depth by 1–2.
MAX_DEPTH = 6


# ── Public entry point ───────────────────────────────────────────────


def semantic_validate(tree, *, allow_position: bool = False) -> None:
    """Walk the tree and raise ``DSLValidationError`` on any violation.

    Returns None on success — callers chain this after Pydantic
    parsing inside the step-config validator.

    ``allow_position`` defaults to False (the entry-tree context).
    Exit-tree validation passes ``allow_position=True`` so the
    ``position`` leaf is accepted.
    """
    _check_root_shape(tree)
    _check_depth(tree)
    _check_indicator_registry(tree)
    _check_indicator_component(tree)
    if not allow_position:
        _check_no_position_leaf(tree)
    _check_position_basis(tree)
    _check_aggregate_ops(tree)
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
    if isinstance(node, ConditionalNode):
        return 1 + max(_depth(node.if_), _depth(node.then), _depth(node.else_))
    if isinstance(node, AggregateNode):
        children = [_depth(node.source)]
        if node.second is not None:
            children.append(_depth(node.second))
        return 1 + max(children)
    # Leaves: indicator / price / volume / constant / position
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


# ── Indicator component ─────────────────────────────────────────────


def _check_indicator_component(node) -> None:
    """Reject ``component`` on single-output indicators, and unknown
    ``component`` values on multi-output ones. Catches LLM emissions
    like ``{"indicator":"rsi", "component":"upper"}`` (nonsense) and
    ``{"indicator":"bb", "component":"middel"}`` (typo) cleanly with
    a list of allowed values rather than silently falling back."""
    from backend.services.backtest_indicators import allowed_components
    for n in _walk_all(node):
        if not isinstance(n, IndicatorNode):
            continue
        if not n.component:
            continue
        allowed = allowed_components(n.indicator)
        if not allowed:
            raise DSLValidationError(
                f"Indicator '{n.indicator}' is single-output — drop the "
                "'component' field. Multi-output indicators that accept "
                "components: bb, macd, stoch, stoch_rsi, aroon, donchian, "
                "keltner."
            )
        if n.component not in allowed:
            raise DSLValidationError(
                f"Unknown component '{n.component}' for indicator "
                f"'{n.indicator}'. Allowed: {', '.join(allowed)}."
            )


# ── Position leaf placement ─────────────────────────────────────────


_POSITION_BASIS_BY_FIELD: dict[str, frozenset[str]] = {
    "unrealised_pct": frozenset({"close", "low", "high"}),
    "unrealised_abs": frozenset({"close", "low", "high"}),
    # Other fields don't accept a basis — Pydantic will allow it but
    # we surface a clearer error here.
    "entry_price":            frozenset(),
    "bars_held":              frozenset(),
    "peak_unrealised_pct":    frozenset(),
    "drawdown_from_peak_pct": frozenset(),
}


def _check_no_position_leaf(node) -> None:
    """Entry trees never have a position; reject the leaf with a
    clear pointer to where it belongs."""
    for n in _walk_all(node):
        if isinstance(n, PositionNode):
            raise DSLValidationError(
                "'position' leaf is only valid in an EXIT tree — entry "
                "trees evaluate before any position exists. Move this "
                f"leaf (field={n.field!r}) into the exit_policy.tree "
                "instead."
            )


def _check_position_basis(node) -> None:
    """Validate ``basis`` matches the field. Only unrealised_pct /
    unrealised_abs use the bar's low / high; other fields are
    bar-component-agnostic."""
    for n in _walk_all(node):
        if not isinstance(n, PositionNode):
            continue
        if n.basis is None:
            continue
        allowed = _POSITION_BASIS_BY_FIELD.get(n.field, frozenset())
        if not allowed:
            raise DSLValidationError(
                f"Position field '{n.field}' does not accept a 'basis' "
                f"selector — drop the field, or switch to "
                "'unrealised_pct' / 'unrealised_abs' if you need a "
                "bar-low / bar-high read."
            )
        if n.basis not in allowed:
            raise DSLValidationError(
                f"Unknown basis '{n.basis}' for position field "
                f"'{n.field}'. Allowed: {', '.join(sorted(allowed))}."
            )


# ── Aggregate op semantics ──────────────────────────────────────────


_AGG_REQUIRES_BOOLEAN_SOURCE = frozenset(
    {"barssince", "count_when", "any_when", "valuewhen"}
)
_AGG_REQUIRES_SECOND = frozenset({"correlation", "valuewhen"})


def _check_aggregate_ops(node) -> None:
    """Validate per-op constraints. Most ops accept any numeric source;
    a few require a boolean source (barssince, count_when, any_when,
    valuewhen), and two require a ``second`` operand (correlation,
    valuewhen)."""
    for n in _walk_all(node):
        if not isinstance(n, AggregateNode):
            continue
        if n.op in _AGG_REQUIRES_SECOND and n.second is None:
            raise DSLValidationError(
                f"Aggregate op '{n.op}' requires a 'second' operand "
                "(the value/series to compare against)."
            )
        if n.op not in _AGG_REQUIRES_SECOND and n.second is not None:
            raise DSLValidationError(
                f"Aggregate op '{n.op}' does not accept a 'second' "
                "operand — drop the field or switch to "
                "'correlation' / 'valuewhen'."
            )
        if n.op in _AGG_REQUIRES_BOOLEAN_SOURCE:
            if not _yields_boolean(n.source):
                raise DSLValidationError(
                    f"Aggregate op '{n.op}' needs a boolean source "
                    "(a comparison or logic node); got "
                    f"'{type(n.source).__name__}'."
                )


def _yields_boolean(node) -> bool:
    """Quick structural sniff: which sub-trees evaluate to boolean.
    Comparison and logic always do; conditional does iff both
    branches yield boolean."""
    if isinstance(node, (ComparisonNode, LogicNode)):
        return True
    if isinstance(node, ConditionalNode):
        return _yields_boolean(node.then) and _yields_boolean(node.else_)
    return False


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
    elif isinstance(node, ConditionalNode):
        yield from _walk_all(node.if_)
        yield from _walk_all(node.then)
        yield from _walk_all(node.else_)
    elif isinstance(node, AggregateNode):
        yield from _walk_all(node.source)
        if node.second is not None:
            yield from _walk_all(node.second)
    # Leaves yield only themselves; covered by the initial yield.
