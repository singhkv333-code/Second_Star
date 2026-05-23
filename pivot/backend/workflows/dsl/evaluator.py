"""Tree evaluator — walks a Tree, returns Ternary result + new state.

Semantics:

  - Every leaf evaluates to a ``Number`` (float) OR ``None`` (data
    missing). The evaluator never invents a number on missing data.
  - Comparisons return ``Ternary.TRUE`` / ``FALSE`` / ``UNKNOWN``.
    ``UNKNOWN`` is sticky: any comparison where either side is None
    is ``UNKNOWN``.
  - Logic operators follow Kleene three-valued logic:

        and:  T AND T = T,   T AND F = F,   T AND U = U,
              F AND F = F,   F AND U = F,   U AND U = U
        or:   T OR T = T,    T OR F = T,    T OR U = T,
              F OR F = F,    F OR U = U,    U OR U = U
        not:  not T = F, not F = T, not U = U

    Why Kleene: it prevents spurious fires on flaky data. If RSI is
    unavailable AND price is below threshold, the AND should not
    fire ("we don't know if RSI matches") rather than spuriously
    firing.

  - ``crosses_above`` / ``crosses_below`` need the previous tick's
    value to detect the transition. The evaluator threads a
    ``prev_state`` dict in and emits a ``new_state`` dict that the
    caller persists between ticks (in the watcher, on the workflow
    step's config; in backtest, on a per-symbol ledger).

The state-dict shape:
    prev_state[(side, comparison_id)] = float

where ``side`` is "left"/"right" and ``comparison_id`` is a stable
fingerprint of the comparison node (so re-using the same
``RSI(TCS, 14) crosses_above 30`` in two places shares state).
"""
from __future__ import annotations

import enum
import hashlib
import json
from dataclasses import dataclass, field
from typing import Optional

from backend.workflows.dsl.data_accessor import DataAccessor
from backend.workflows.dsl.schema import (
    ComparisonNode,
    ConstantNode,
    IndicatorNode,
    LogicNode,
    PositionNode,
    PriceNode,
    VolumeNode,
)


class Ternary(enum.Enum):
    """Kleene three-valued result type."""

    TRUE = True
    FALSE = False
    UNKNOWN = None

    def __bool__(self) -> bool:
        # Truthy only when explicitly TRUE — UNKNOWN/FALSE are falsy.
        # Callers that need to distinguish UNKNOWN from FALSE compare
        # to ``Ternary.TRUE`` explicitly.
        return self is Ternary.TRUE


@dataclass
class EvaluationResult:
    """Output of ``evaluate``. The caller persists ``new_state`` back
    onto the workflow step so the next tick's evaluation sees the
    previous values for ``crosses_above`` / ``crosses_below``."""

    value: Ternary
    new_state: dict[str, float] = field(default_factory=dict)


# ── Public entry point ───────────────────────────────────────────────


def evaluate(
    tree,
    *,
    accessor: DataAccessor,
    prev_state: Optional[dict[str, float]] = None,
) -> EvaluationResult:
    """Walk ``tree`` and return a Ternary plus updated state.

    The state dict is updated IN PLACE for new readings but a fresh
    dict is returned so the caller can persist it without aliasing.
    """
    new_state: dict[str, float] = dict(prev_state or {})
    val = _walk(tree, accessor=accessor, state=new_state)
    if val is None:
        ternary = Ternary.UNKNOWN
    elif isinstance(val, bool):
        ternary = Ternary.TRUE if val else Ternary.FALSE
    else:
        # Leaf node alone at root — not a boolean. Treat as UNKNOWN;
        # this is a programming error caught by ``semantic_validate``.
        ternary = Ternary.UNKNOWN
    return EvaluationResult(value=ternary, new_state=new_state)


# ── Internal walker ──────────────────────────────────────────────────


def _walk(node, *, accessor: DataAccessor, state: dict[str, float]):
    """Recursive walker. Returns:
      - float for leaf number nodes (indicator / price / volume / constant)
      - bool for comparison + logic
      - None when a leaf can't be resolved (data missing)
    """
    if isinstance(node, ConstantNode):
        return float(node.value)
    if isinstance(node, PriceNode):
        return accessor.get_price(symbol=node.symbol, exchange=node.exchange)
    if isinstance(node, IndicatorNode):
        return accessor.get_indicator(
            symbol=node.symbol,
            indicator=node.indicator,
            period=node.period,
            exchange=node.exchange,
            component=node.component,
        )
    if isinstance(node, VolumeNode):
        return accessor.get_volume(
            symbol=node.symbol, bars=node.bars, exchange=node.exchange,
        )
    if isinstance(node, PositionNode):
        return accessor.get_position_field(
            field=node.field, basis=node.basis,
        )
    if isinstance(node, ComparisonNode):
        return _eval_comparison(node, accessor=accessor, state=state)
    if isinstance(node, LogicNode):
        return _eval_logic(node, accessor=accessor, state=state)
    # Unknown node type — Pydantic would have rejected this at parse
    # time. Defensive return.
    return None


def _eval_comparison(
    node: ComparisonNode,
    *,
    accessor: DataAccessor,
    state: dict[str, float],
):
    left = _walk(node.left, accessor=accessor, state=state)
    right = _walk(node.right, accessor=accessor, state=state)

    # Kleene UNKNOWN: missing input on either side → can't decide.
    if left is None or right is None:
        return None

    # Both must be numeric at this point.
    if not isinstance(left, (int, float)) or not isinstance(right, (int, float)):
        return None
    left_f = float(left)
    right_f = float(right)

    op = node.op
    if op == ">":
        return left_f > right_f
    if op == "<":
        return left_f < right_f
    if op == ">=":
        return left_f >= right_f
    if op == "<=":
        return left_f <= right_f
    if op == "==":
        return left_f == right_f

    # Crossing operators need the previous tick's values for both
    # operands. We key state by a stable hash of the operand sub-trees
    # so the same expression reuses state across (a) repeated
    # occurrences inside one tree, (b) tick-over-tick evaluation.
    left_key = f"left:{_fingerprint(node.left)}"
    right_key = f"right:{_fingerprint(node.right)}"
    prev_left = state.get(left_key)
    prev_right = state.get(right_key)

    state[left_key] = left_f
    state[right_key] = right_f

    if prev_left is None or prev_right is None:
        # First tick — no transition is observable yet. Conservatively
        # return False (not UNKNOWN — we WILL observe the next tick).
        return False
    if op == "crosses_above":
        return prev_left <= prev_right and left_f > right_f
    if op == "crosses_below":
        return prev_left >= prev_right and left_f < right_f
    return None


def _eval_logic(
    node: LogicNode,
    *,
    accessor: DataAccessor,
    state: dict[str, float],
):
    if node.op == "not":
        sub = _walk(node.operands[0], accessor=accessor, state=state)
        if sub is None:
            return None
        if isinstance(sub, bool):
            return not sub
        return None

    sub_results: list[Optional[bool]] = []
    for child in node.operands:
        v = _walk(child, accessor=accessor, state=state)
        if v is None:
            sub_results.append(None)
        elif isinstance(v, bool):
            sub_results.append(v)
        else:
            sub_results.append(None)

    if node.op == "and":
        # If any operand is False, the whole AND is False (regardless
        # of UNKNOWNs). Otherwise: any UNKNOWN → UNKNOWN; else True.
        if any(r is False for r in sub_results):
            return False
        if any(r is None for r in sub_results):
            return None
        return True

    if node.op == "or":
        # Symmetric: any True → True. Else any UNKNOWN → UNKNOWN. Else False.
        if any(r is True for r in sub_results):
            return True
        if any(r is None for r in sub_results):
            return None
        return False

    return None


# ── Fingerprint for crossings state keys ─────────────────────────────


def _fingerprint(node) -> str:
    """Stable short hash of a node sub-tree. Used as the key for
    persisted crossings state. Same sub-tree → same key → re-uses
    state across evaluations of the same workflow."""
    try:
        payload = node.model_dump(mode="json") if hasattr(node, "model_dump") else str(node)
    except Exception:  # noqa: BLE001 — fallback for pre-Pydantic-2 paths
        payload = str(node)
    blob = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.blake2s(blob, digest_size=10).hexdigest()
