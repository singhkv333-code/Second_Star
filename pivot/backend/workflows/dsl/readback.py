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


_OP_PHRASES = {
    ">": ">",
    "<": "<",
    ">=": "≥",
    "<=": "≤",
    "==": "=",
    "crosses_above": "crosses above",
    "crosses_below": "crosses below",
}


# Adjective shown before the indicator label when a component is set.
# E.g. ``{"indicator":"bb","component":"lower"}`` → ``lower BB(20)``.
# Single-output indicators carry no component → no prefix.
_COMPONENT_PHRASES = {
    "upper": "upper",
    "middle": "middle",
    "lower": "lower",
    "pctb": "%B",
    "bandwidth": "bandwidth",
    "macd": "line",
    "signal": "signal",
    "hist": "histogram",
    "k": "%K",
    "d": "%D",
    "up": "up",
    "down": "down",
    "osc": "oscillator",
}


def tree_to_english(tree) -> str:
    """Render a Tree as a single human-readable sentence."""
    return _render(tree, depth=0)


def _render(node, *, depth: int) -> str:
    if isinstance(node, IndicatorNode):
        base = _render_indicator_paren(node)
        if node.component:
            phrase = _COMPONENT_PHRASES.get(node.component.lower(), node.component)
            base = f"{phrase} {base}"
        suffix = _offset_phrase(node.offset)
        tf_suffix = _timeframe_phrase(node.timeframe)
        return f"{base} of {node.symbol}{tf_suffix}{suffix}"
    if isinstance(node, PriceNode):
        basis_phrase = "" if node.basis == "close" else f" {node.basis}"
        suffix = _offset_phrase(node.offset)
        return f"{basis_phrase.strip() or 'price'} of {node.symbol}{suffix}"
    if isinstance(node, VolumeNode):
        suffix = _offset_phrase(node.offset)
        if node.bars == 1:
            return f"volume of {node.symbol}{suffix}"
        return f"{node.bars}-bar volume of {node.symbol}{suffix}"
    if isinstance(node, PositionNode):
        return _render_position(node)
    if isinstance(node, ConstantNode):
        return _format_number(node.value)
    if isinstance(node, SessionDayNode):
        full = [_DAY_FULL[d] for d in node.days]
        return "on " + (full[0] if len(full) == 1 else " or ".join(full))
    if isinstance(node, GapNode):
        return f"gap of {node.symbol}"
    if isinstance(node, PctChangeNode):
        return f"{node.bars}-bar % change of {node.symbol}"
    if isinstance(node, SpreadNode):
        return f"{node.a} / {node.b}"
    if isinstance(node, MathNode):
        return _render_math(node, depth=depth)
    if isinstance(node, ConditionalNode):
        cond = _render(node.if_, depth=depth + 1)
        then = _render(node.then, depth=depth + 1)
        else_ = _render(node.else_, depth=depth + 1)
        return f"if ({cond}) then {then} else {else_}"
    if isinstance(node, AggregateNode):
        op_phrase = _AGG_PHRASES.get(node.op, node.op)
        src = _render(node.source, depth=depth + 1)
        if node.second is not None:
            second = _render(node.second, depth=depth + 1)
            return f"{op_phrase}({src}, {second}) over last {node.bars} bars"
        return f"{op_phrase}({src}) over last {node.bars} bars"
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


_AGG_PHRASES = {
    "highest": "highest",
    "lowest": "lowest",
    "sum": "sum of",
    "avg": "average of",
    "std": "stdev of",
    "count_when": "count where",
    "any_when": "any of",
    "percentrank": "percentile rank of",
    "zscore": "z-score of",
    "barssince": "bars since",
    "valuewhen": "value when",
    "correlation": "correlation of",
}


_DAY_FULL = {
    "mon": "Monday",   "tue": "Tuesday", "wed": "Wednesday",
    "thu": "Thursday", "fri": "Friday",  "sat": "Saturday",
    "sun": "Sunday",
}


_MATH_INFIX = {"+": "+", "-": "-", "*": "×", "/": "÷"}


def _render_math(node: MathNode, *, depth: int) -> str:
    """Render arithmetic. Binary ops as ``a + b``; unary as ``|a|``
    or ``-a``; variadic as ``min(a, b, c)`` / ``max(...)``."""
    rendered = [_render(c, depth=depth + 1) for c in node.operands]
    if node.op in _MATH_INFIX and len(rendered) == 2:
        glue = _MATH_INFIX[node.op]
        body = f"{rendered[0]} {glue} {rendered[1]}"
        # Parenthesise non-root to disambiguate against the enclosing
        # comparison / math node.
        return f"({body})" if depth > 0 else body
    if node.op == "abs" and len(rendered) == 1:
        return f"|{rendered[0]}|"
    if node.op == "negate" and len(rendered) == 1:
        return f"-({rendered[0]})"
    if node.op in ("min", "max"):
        return f"{node.op}({', '.join(rendered)})"
    # Fallback (shouldn't normally hit — validator rejects).
    return f"{node.op}({', '.join(rendered)})"


def _offset_phrase(offset: int) -> str:
    """Render the bar-offset suffix. 0 = no phrase, 1 = " (1 bar ago)",
    N = " (N bars ago)"."""
    if not offset:
        return ""
    if offset == 1:
        return " (1 bar ago)"
    return f" ({offset} bars ago)"


def _timeframe_phrase(timeframe: str) -> str:
    """Render the bar-timeframe suffix. Daily is the default and stays
    silent (so the existing readbacks ``RSI(14) of TCS < 30`` are
    unchanged). Weekly is disclosed inline so the user can SEE that
    "RSI < 30" is being checked on weekly closes, not daily.
    """
    if not timeframe or timeframe == "daily":
        return ""
    if timeframe == "weekly":
        return " on weekly bars"
    return f" on {timeframe} bars"


def _render_indicator_paren(node: IndicatorNode) -> str:
    """Render the bracketed parameter list for an indicator leaf.

    Most indicators surface a single ``period`` so they render as
    ``RSI(14)``. MACD is the special case: the live computation uses
    ``(fast=12, slow=period, signal=9)`` (see
    ``backend.services.backtest_indicators._macd_hist``) — printing a
    bare ``MACD(12)`` is doubly misleading when:

      a) ``period`` is the SLOW EMA (defaults to 26), not the fast one,
         so ``MACD(12)`` reads like fast=12 when it's actually slow.
      b) A ``component`` is set (``macd`` line vs ``signal`` line) —
         two readbacks side-by-side ("line MACD(12) < signal MACD(12)")
         hide that signal is a different series with its own EMA.

    When the indicator is MACD we therefore disclose the full
    ``(fast, slow, signal)`` triplet using the actual values the
    compute path uses. For every other indicator we keep the simple
    single-period form so the existing readbacks (and tests) are
    unchanged.
    """
    if node.indicator.lower() == "macd":
        # Mirror backtest_indicators._macd_hist: slow EMA is clamped to
        # at least 13 so MACD(12) on a fast-but-too-short slow still
        # computes sensibly. Signal is fixed at 9 by convention.
        fast = 12
        slow = max(int(node.period), 13)
        signal = 9
        return f"MACD({fast},{slow},{signal})"
    return f"{node.indicator.upper()}({node.period})"


def _render_position(node: PositionNode) -> str:
    """Render a position-leaf as a short noun phrase that fits inside
    a comparison sentence (`<phrase> > 0.10` →
    &ldquo;unrealised P&amp;L &gt; 10%&rdquo; reads naturally)."""
    basis_phrase = {
        "low": " at bar low",
        "high": " at bar high",
        "close": "",
        None: "",
    }.get(node.basis, "")
    base = {
        "entry_price":            "entry price",
        "unrealised_pct":         "unrealised P&L",
        "unrealised_abs":         "unrealised P&L (₹)",
        "bars_held":              "bars held",
        "peak_unrealised_pct":    "peak unrealised P&L",
        "drawdown_from_peak_pct": "drawdown from peak",
    }.get(node.field, node.field)
    return f"{base}{basis_phrase}"


def _format_number(v: float) -> str:
    """Strip the trailing .0 for integers; keep up to 4 dp otherwise."""
    iv = int(v)
    if float(iv) == float(v):
        return f"{iv:,}".replace(",", ",")  # thousands separator for readability
    return f"{v:,.4f}".rstrip("0").rstrip(".")
