"""tree → plain English.

Used by the chat confirmation card: when the LLM proposer emits a
compound trigger, we render the tree back as a sentence the user
can read and confirm. If the readback feels off, the user can edit
the prompt (and we capture the diff for the LLM to learn from).

Style choices:

  - Indicator names go upper-case: ``RSI`` not ``rsi``.
  - Symbol stays as-given (``TCS``, ``NIFTY``).
  - Period is shown as ``RSI(14)`` not ``RSI period=14``.
  - Constants are formatted with trailing ``.0`` stripped for ints.
  - Logic ops are spelled out: ``AND``, ``OR``, ``NOT``.
  - Crossings: ``crosses above`` / ``crosses below`` (lowercase, spaced).
  - Multi-operand logic uses parentheses for clarity at depth ≥ 2.

The function is pure — no DB, no LLM. Safe to call from anywhere.
"""
from __future__ import annotations

from backend.workflows.dsl.schema import (
    ComparisonNode,
    ConstantNode,
    IndicatorNode,
    LogicNode,
    PriceNode,
    VolumeNode,
)


_OP_PHRASES = {
    ">": ">",
    "<": "<",
    ">=": "≥",
    "<=": "≤",
    "==": "=",
    "crosses_above": "crosses above",
    "crosses_below": "crosses below",
}


def tree_to_english(tree) -> str:
    """Render a Tree as a single human-readable sentence."""
    return _render(tree, depth=0)


def _render(node, *, depth: int) -> str:
    if isinstance(node, IndicatorNode):
        return f"{node.indicator.upper()}({node.period}) of {node.symbol}"
    if isinstance(node, PriceNode):
        return f"price of {node.symbol}"
    if isinstance(node, VolumeNode):
        if node.bars == 1:
            return f"volume of {node.symbol}"
        return f"{node.bars}-bar volume of {node.symbol}"
    if isinstance(node, ConstantNode):
        return _format_number(node.value)
    if isinstance(node, ComparisonNode):
        left = _render(node.left, depth=depth + 1)
        right = _render(node.right, depth=depth + 1)
        op = _OP_PHRASES.get(node.op, node.op)
        return f"{left} {op} {right}"
    if isinstance(node, LogicNode):
        if node.op == "not":
            inner = _render(node.operands[0], depth=depth + 1)
            return f"NOT ({inner})"
        joiner = " AND " if node.op == "and" else " OR "
        rendered = [_render(c, depth=depth + 1) for c in node.operands]
        joined = joiner.join(rendered)
        # Parenthesize when nested inside another logic / comparison
        # so the reader can see the grouping. The top-level call
        # (depth=0) stays unparenthesized for clean prose.
        return f"({joined})" if depth > 0 else joined
    # Unknown node — would have been caught by Pydantic.
    return repr(node)


def _format_number(v: float) -> str:
    """Strip the trailing .0 for integers; keep up to 4 dp otherwise."""
    iv = int(v)
    if float(iv) == float(v):
        return f"{iv:,}".replace(",", ",")  # thousands separator for readability
    return f"{v:,.4f}".rstrip("0").rstrip(".")
