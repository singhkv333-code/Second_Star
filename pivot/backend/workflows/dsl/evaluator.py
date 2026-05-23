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
        return accessor.get_price(
            symbol=node.symbol,
            exchange=node.exchange,
            basis=node.basis,
            offset=node.offset + _additional_offset(),
        )
    if isinstance(node, IndicatorNode):
        return accessor.get_indicator(
            symbol=node.symbol,
            indicator=node.indicator,
            period=node.period,
            exchange=node.exchange,
            component=node.component,
            offset=node.offset + _additional_offset(),
        )
    if isinstance(node, VolumeNode):
        return accessor.get_volume(
            symbol=node.symbol,
            bars=node.bars,
            exchange=node.exchange,
            offset=node.offset + _additional_offset(),
        )
    if isinstance(node, PositionNode):
        return accessor.get_position_field(
            field=node.field, basis=node.basis,
        )
    if isinstance(node, SessionDayNode):
        day = accessor.get_session_day()
        if day is None:
            return None
        return day in node.days
    if isinstance(node, GapNode):
        return _eval_gap(node, accessor=accessor)
    if isinstance(node, PctChangeNode):
        return _eval_pct_change(node, accessor=accessor)
    if isinstance(node, SpreadNode):
        return _eval_spread(node, accessor=accessor)
    if isinstance(node, MathNode):
        return _eval_math(node, accessor=accessor, state=state)
    if isinstance(node, ConditionalNode):
        return _eval_conditional(node, accessor=accessor, state=state)
    if isinstance(node, AggregateNode):
        return _eval_aggregate(node, accessor=accessor, state=state)
    if isinstance(node, ComparisonNode):
        return _eval_comparison(node, accessor=accessor, state=state)
    if isinstance(node, LogicNode):
        return _eval_logic(node, accessor=accessor, state=state)
    # Unknown node type — Pydantic would have rejected this at parse
    # time. Defensive return.
    return None


# ── Shortcut leaves: gap / pct_change / spread ────────────────────


def _eval_gap(node: GapNode, *, accessor: DataAccessor) -> Optional[float]:
    """(today's open - yesterday's close) / yesterday's close, signed.
    Returns None when either bar is missing."""
    cur_open = accessor.get_price(
        symbol=node.symbol, exchange=node.exchange,
        basis="open", offset=_additional_offset(),
    )
    prev_close = accessor.get_price(
        symbol=node.symbol, exchange=node.exchange,
        basis="close", offset=1 + _additional_offset(),
    )
    if cur_open is None or prev_close is None:
        return None
    if prev_close == 0.0:
        return None
    return (cur_open - prev_close) / prev_close


def _eval_pct_change(
    node: PctChangeNode, *, accessor: DataAccessor,
) -> Optional[float]:
    """(close - close[bars]) / close[bars], signed."""
    cur = accessor.get_price(
        symbol=node.symbol, exchange=node.exchange,
        basis="close", offset=_additional_offset(),
    )
    past = accessor.get_price(
        symbol=node.symbol, exchange=node.exchange,
        basis="close", offset=int(node.bars) + _additional_offset(),
    )
    if cur is None or past is None or past == 0.0:
        return None
    return (cur - past) / past


def _eval_spread(
    node: SpreadNode, *, accessor: DataAccessor,
) -> Optional[float]:
    """price_a / price_b. Returns None on missing data or zero denom."""
    a = accessor.get_price(
        symbol=node.a, exchange=node.exchange,
        basis="close", offset=_additional_offset(),
    )
    b = accessor.get_price(
        symbol=node.b, exchange=node.exchange,
        basis="close", offset=_additional_offset(),
    )
    if a is None or b is None or b == 0.0:
        return None
    return a / b


# ── Math node ─────────────────────────────────────────────────────


_MATH_BINARY = {"+", "-", "*", "/"}
_MATH_UNARY = {"abs", "negate"}
_MATH_VARIADIC = {"min", "max"}


def _eval_math(
    node: MathNode,
    *,
    accessor: DataAccessor,
    state: dict[str, float],
) -> Optional[float]:
    """Arithmetic over numeric operand sub-trees. UNKNOWN propagates;
    divide-by-zero returns UNKNOWN."""
    vals: list[Optional[float]] = []
    for sub in node.operands:
        v = _walk(sub, accessor=accessor, state=state)
        if v is None:
            return None
        if isinstance(v, bool):
            v = float(v)
        if not isinstance(v, (int, float)):
            return None
        vals.append(float(v))

    op = node.op
    if op in _MATH_UNARY:
        if len(vals) != 1:
            return None
        return abs(vals[0]) if op == "abs" else -vals[0]
    if op in _MATH_BINARY:
        if len(vals) != 2:
            return None
        a, b = vals
        if op == "+":
            return a + b
        if op == "-":
            return a - b
        if op == "*":
            return a * b
        if op == "/":
            return None if b == 0.0 else a / b
    if op in _MATH_VARIADIC:
        if not vals:
            return None
        return min(vals) if op == "min" else max(vals)
    return None


def _eval_conditional(
    node: ConditionalNode,
    *,
    accessor: DataAccessor,
    state: dict[str, float],
):
    """Pine Script's ternary. UNKNOWN propagates; otherwise pick a
    branch and evaluate ONLY that one (short-circuit)."""
    cond = _walk(node.if_, accessor=accessor, state=state)
    if cond is None:
        return None
    if not isinstance(cond, bool):
        # Numeric ``if`` slot can't be boolean → UNKNOWN
        return None
    chosen = node.then if cond else node.else_
    return _walk(chosen, accessor=accessor, state=state)


def _eval_aggregate(
    node: AggregateNode,
    *,
    accessor: DataAccessor,
    state: dict[str, float],
):
    """Evaluate the source sub-tree over the last ``bars`` bars of
    history. Delegates to the accessor's ``get_aggregate`` if it
    exposes one (backtest path — vectorised); otherwise falls back to
    a slow per-bar walk by introspecting the leaves' ``offset`` slot
    (only safe for trees whose source is a single leaf node, sufficient
    for live mode v1)."""
    if hasattr(accessor, "evaluate_aggregate"):
        return accessor.evaluate_aggregate(
            node=node, evaluator=_walk, state=state,
        )
    return _slow_aggregate(node, accessor=accessor, state=state)


def _slow_aggregate(
    node: AggregateNode,
    *,
    accessor: DataAccessor,
    state: dict[str, float],
):
    """Live-path fallback. Walks the source sub-tree once per offset
    in the lookback window. Acceptable for live (one call per minute)
    but too slow for backtest — the accessor short-circuit above
    handles that case."""
    import math
    # Collect source values across offset = 0 .. bars-1.
    src_values: list[Optional[float]] = []
    second_values: list[Optional[float]] = []
    n = int(node.bars)
    for off in range(n):
        with _shifted_evaluation(off):
            sv = _walk(node.source, accessor=accessor, state=state)
            src_values.append(sv if not isinstance(sv, bool) else float(sv))
            if node.second is not None:
                tv = _walk(node.second, accessor=accessor, state=state)
                second_values.append(tv if not isinstance(tv, bool) else float(tv))
    # src_values[0] = current bar; src_values[n-1] = oldest.
    return _reduce_aggregate(node, src_values, second_values)


def _reduce_aggregate(
    node: AggregateNode,
    src: list[Optional[float]],
    second: list[Optional[float]],
):
    """Pure reduction over already-evaluated source values. Pulled
    out so the backtest fast path and the live slow path share this
    logic exactly."""
    import math
    op = node.op
    # Drop None for value-ops; UNKNOWN-source bars are skipped, not
    # propagated, so percentile / count / highest etc. work on whatever
    # the source could resolve. The exception: barssince / valuewhen
    # which treat None as "condition not observed".
    if op in ("highest", "lowest", "sum", "avg", "std", "percentrank", "zscore"):
        clean = [v for v in src if v is not None]
        if not clean:
            return None
        if op == "highest":
            return float(max(clean))
        if op == "lowest":
            return float(min(clean))
        if op == "sum":
            return float(sum(clean))
        if op == "avg":
            return float(sum(clean) / len(clean))
        if op == "std":
            if len(clean) < 2:
                return None
            mean = sum(clean) / len(clean)
            var = sum((x - mean) ** 2 for x in clean) / (len(clean) - 1)
            return float(math.sqrt(var))
        if op == "percentrank":
            current = src[0]
            if current is None:
                return None
            below = sum(1 for v in clean if v < current)
            return float(below) / float(len(clean))
        if op == "zscore":
            current = src[0]
            if current is None or len(clean) < 2:
                return None
            mean = sum(clean) / len(clean)
            var = sum((x - mean) ** 2 for x in clean) / (len(clean) - 1)
            std = math.sqrt(var)
            if std <= 0.0:
                return None
            return float((current - mean) / std)
    if op in ("count_when", "any_when"):
        true_count = sum(1 for v in src if v == 1.0 or v is True)
        if op == "count_when":
            return float(true_count)
        return float(1.0 if true_count > 0 else 0.0)
    if op == "barssince":
        for i, v in enumerate(src):
            if v == 1.0 or v is True:
                return float(i)
        return None  # condition never fired in window → UNKNOWN
    if op == "valuewhen":
        if not second:
            return None
        for i, v in enumerate(src):
            if v == 1.0 or v is True:
                return second[i] if i < len(second) else None
        return None
    if op == "correlation":
        if not second:
            return None
        pairs = [
            (s, t) for s, t in zip(src, second)
            if s is not None and t is not None
        ]
        if len(pairs) < 2:
            return None
        n = float(len(pairs))
        sx = sum(p[0] for p in pairs)
        sy = sum(p[1] for p in pairs)
        sxx = sum(p[0] * p[0] for p in pairs)
        syy = sum(p[1] * p[1] for p in pairs)
        sxy = sum(p[0] * p[1] for p in pairs)
        denom_sq = (n * sxx - sx * sx) * (n * syy - sy * sy)
        if denom_sq <= 0:
            return None
        denom = math.sqrt(denom_sq)
        return float((n * sxy - sx * sy) / denom)
    return None


# ── Offset shifting for the slow-path aggregator ────────────────────


class _ShiftCtx:
    """Thread-local stack of additional offsets applied to every leaf
    read inside a slow-path aggregator walk. Live-mode only; the
    backtest accessor exposes ``evaluate_aggregate`` and bypasses
    this."""

    stack: list[int] = []


def _additional_offset() -> int:
    return sum(_ShiftCtx.stack)


def _shifted_evaluation(extra: int):
    """Context manager that pushes ``extra`` onto the offset stack."""
    class _CM:
        def __enter__(self_inner):
            _ShiftCtx.stack.append(extra)
            return self_inner
        def __exit__(self_inner, *args):
            _ShiftCtx.stack.pop()
    return _CM()


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
