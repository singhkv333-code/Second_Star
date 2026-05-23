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

from typing import Iterable, Optional

from backend.workflows.dsl.schema import (
    AggregateNode,
    ComparisonNode,
    ConditionalNode,
    ConstantNode,
    GapNode,
    IndicatorNode,
    LogicNode,
    MathNode,
    PctChangeNode,
    PositionNode,
    PriceNode,
    SessionDayNode,
    SpreadNode,
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
    _check_math_ops(tree)
    _check_no_vacuous_comparisons(tree)
    _check_no_contradictory_and(tree)


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
    if isinstance(node, MathNode):
        return 1 + max((_depth(c) for c in node.operands), default=0)
    # Leaves: indicator / price / volume / constant / position
    # / gap / pct_change / spread (the shortcut leaves)
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


# ── Math op operand counts ──────────────────────────────────────────


_MATH_UNARY = frozenset({"abs", "negate"})
_MATH_BINARY = frozenset({"+", "-", "*", "/"})
_MATH_VARIADIC = frozenset({"min", "max"})


def _check_math_ops(node) -> None:
    """Validate operand count for math ops:
       abs/negate → exactly 1 operand
       +/-/*/÷    → exactly 2 operands
       min/max    → 2..8 operands"""
    for n in _walk_all(node):
        if not isinstance(n, MathNode):
            continue
        k = len(n.operands)
        if n.op in _MATH_UNARY and k != 1:
            raise DSLValidationError(
                f"math op '{n.op}' expects exactly 1 operand, got {k}."
            )
        if n.op in _MATH_BINARY and k != 2:
            raise DSLValidationError(
                f"math op '{n.op}' expects exactly 2 operands, got {k}."
            )
        if n.op in _MATH_VARIADIC and k < 2:
            raise DSLValidationError(
                f"math op '{n.op}' expects 2..8 operands, got {k}."
            )


# ── Vacuous comparisons ─────────────────────────────────────────────


def _check_no_vacuous_comparisons(node) -> None:
    from backend.workflows.dsl.evaluator import _fingerprint
    for n in _walk_all(node):
        if not isinstance(n, ComparisonNode):
            continue
        # Both sides constant — comparison decided at LLM time.
        if isinstance(n.left, ConstantNode) and isinstance(n.right, ConstantNode):
            raise DSLValidationError(
                f"Vacuous comparison: both sides are constants "
                f"({n.left.value} {n.op} {n.right.value}). At least one "
                "operand must be a market value (indicator / price / volume)."
            )
        # Self-comparison — same expression on both sides. `X == X` is
        # tautologically true, `X < X` is tautologically false, etc.
        # The LLM occasionally emits these to fake a no-op filter (e.g.
        # to "express" a day-of-week constraint when session_day wasn't
        # in the grammar yet). Reject so the LLM picks the right node.
        if _fingerprint(n.left) == _fingerprint(n.right):
            raise DSLValidationError(
                f"Self-comparison ({n.op}) — both sides resolve to the "
                "same expression. This is either tautologically true "
                "or false; pick a different operand or a different "
                "operator. (If you meant to express a day-of-week or "
                "session filter, use the 'session_day' leaf instead.)"
            )


# ── Contradictory AND detector ──────────────────────────────────────


def _check_no_contradictory_and(node) -> None:
    """Catch ``A AND B`` chains whose intersection is empty.

    Walks every ``logic.and`` node, groups its comparison-against-
    constant operands by left-side fingerprint, computes the interval
    intersection of the constraints in each group, and rejects the
    tree when any group is empty.

    Common silent-failure pattern this catches: the LLM translating a
    buy-then-sell prompt (``buy when RSI<30, sell when RSI>30``) into
    a single AND'd entry tree (``RSI<30 AND RSI>30``) which can never
    fire. Rejecting it points the user at the exit-policy field
    instead of producing 0 trades silently.
    """
    # Local import — the evaluator pulls in the indicator registry,
    # and we keep validators.py free of that load-time dep.
    from backend.workflows.dsl.evaluator import _fingerprint

    for n in _walk_all(node):
        if not isinstance(n, LogicNode) or n.op != "and":
            continue
        # Group constant-RHS comparison operands by their left-side
        # fingerprint. Constraints on different sub-expressions stay
        # in their own groups.
        groups: dict[str, list[tuple[str, float, ComparisonNode]]] = {}
        for child in n.operands:
            if not isinstance(child, ComparisonNode):
                continue
            if not isinstance(child.right, ConstantNode):
                continue
            if child.op not in ("<", "<=", ">", ">=", "=="):
                # crosses_above / crosses_below are tick-transition
                # ops and don't fit pure interval reasoning.
                continue
            key = _fingerprint(child.left)
            groups.setdefault(key, []).append(
                (child.op, float(child.right.value), child),
            )
        for _key, constraints in groups.items():
            if len(constraints) < 2:
                continue
            if _is_empty_intersection(
                [(op, v) for op, v, _ in constraints]
            ):
                from backend.workflows.dsl.readback import _render
                phrases = [_render(c, depth=1) for _, _, c in constraints]
                raise DSLValidationError(
                    f"Contradictory entry condition: the constraints "
                    f"{' AND '.join(phrases)} cannot all hold on the "
                    "same bar — no value satisfies all of them. "
                    "If you meant a buy-then-sell strategy, the sell "
                    "rule belongs in the exit policy (exit_kind / "
                    "exit_bars / exit_pct or an exit tree), not the "
                    "entry. If you meant any-of, switch logic.op to "
                    "'or'."
                )


def _is_empty_intersection(
    constraints: list[tuple[str, float]],
) -> bool:
    """Given a list of (op, c) constraints on the same scalar
    expression, return True when their intersection is empty.

    Tracks the tightest lower / upper bound and any equality
    constraints. Empty when:
      • lower > upper, or
      • lower == upper but at least one bound is strict, or
      • two equality constraints disagree, or
      • an equality lies outside the strict-bound interval.
    """
    lower: Optional[tuple[float, bool]] = None  # (value, inclusive?)
    upper: Optional[tuple[float, bool]] = None
    exacts: list[float] = []

    for op, c in constraints:
        if op == "<":
            if upper is None or c < upper[0]:
                upper = (c, False)
            elif c == upper[0] and upper[1]:
                upper = (c, False)
        elif op == "<=":
            if upper is None or c < upper[0]:
                upper = (c, True)
        elif op == ">":
            if lower is None or c > lower[0]:
                lower = (c, False)
            elif c == lower[0] and lower[1]:
                lower = (c, False)
        elif op == ">=":
            if lower is None or c > lower[0]:
                lower = (c, True)
        elif op == "==":
            exacts.append(c)

    if len(set(exacts)) > 1:
        return True
    if lower is not None and upper is not None:
        if lower[0] > upper[0]:
            return True
        if lower[0] == upper[0] and not (lower[1] and upper[1]):
            return True
    if exacts:
        e = exacts[0]
        if lower is not None:
            if e < lower[0] or (e == lower[0] and not lower[1]):
                return True
        if upper is not None:
            if e > upper[0] or (e == upper[0] and not upper[1]):
                return True
    return False


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
    elif isinstance(node, MathNode):
        for child in node.operands:
            yield from _walk_all(child)
    # Leaves yield only themselves; covered by the initial yield.
