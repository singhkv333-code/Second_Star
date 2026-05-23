"""Recursive Pydantic schema for the v1 condition tree.

Fourteen node types behind a single discriminator field ``type``:

  - ``indicator``    — RSI / SMA / EMA / MACD / ATR / ... value
  - ``price``        — bar open/high/low/close for a symbol
  - ``volume``       — bar volume (summed over the last N bars)
  - ``constant``     — a literal number
  - ``position``     — properties of the currently-open position;
                      only meaningful inside an EXIT tree
  - ``session_day``  — boolean: True when the as-of bar's date falls
                      on one of the listed weekdays (mon..sun)
  - ``gap``          — (open - prev_close) / prev_close, signed
  - ``pct_change``   — (close - close[bars]) / close[bars], signed
  - ``spread``       — price_a / price_b (ratio between two symbols)
  - ``math``         — +/-/*/÷/abs/negate/min/max arithmetic
  - ``conditional``  — if/then/else value picker (Pine Script ?:)
  - ``aggregate``    — lookback aggregator (highest/lowest/percentrank/...)
  - ``comparison``   — two operand sub-trees + a binary operator
  - ``logic``        — and / or / not joining operand sub-trees

Why a discriminated union rather than separate top-level types: it
keeps the JSON shape uniform (every node has a ``type`` field), which
makes the LLM proposer's emission target stable, and lets the
evaluator walk via a single dispatch.

The Tree type alias is the recursive bit; Pydantic 2's
``RootModel`` + ``Field(discriminator=)`` resolves the forward
references at validation time.

Hard limits enforced here:
  - period: 1..5000
  - constants: float or int, no NaN, no infinity
  - logic.operands: 2-8 for and/or; exactly 1 for not
  - max depth is enforced by ``validators.semantic_validate`` — not
    here, because the natural place for depth checks is on the full
    composed tree, not on a single node.
"""
from __future__ import annotations

import math
from typing import Annotated, Literal, Optional, Union

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


# ── Strict base — same rationale as workflows/schemas.py:_Strict ─────


class _Strict(BaseModel):
    """All DSL nodes inherit from this. We deliberately allow unknown
    fields (extra='ignore') so a future grammar extension doesn't
    break old persisted trees — the new fields just get dropped on
    the way in.
    """

    model_config = ConfigDict(extra="ignore", populate_by_name=True)


# ── Leaf nodes ───────────────────────────────────────────────────────


class IndicatorNode(_Strict):
    """A registry-backed technical indicator value.

    ``indicator`` must be a key supported by
    ``backend.services.backtest_indicators``. ``validators.py`` does
    that lookup; Pydantic just enforces the field shape.

    ``component`` is optional and only meaningful for multi-output
    indicators (BB upper/middle/lower, MACD line/signal/hist, Stoch
    %K/%D, Aroon up/down/osc, Donchian/Keltner bands). The allowed
    values per indicator are whitelisted by
    ``validators.semantic_validate``. ``None`` keeps the existing
    default series (Bollinger %B, MACD histogram, Stoch %K, ...) for
    backwards-compat with already-persisted trees.

    ``offset`` reads the indicator value N bars ago (Pine Script's
    ``[n]`` operator). 0 = current bar; max 500 to bound memory.
    """

    type: Literal["indicator"] = "indicator"
    indicator: str = Field(..., min_length=1, max_length=32)
    symbol: str = Field(..., min_length=1, max_length=32)
    period: int = Field(..., ge=1, le=5000)
    exchange: str = Field(default="NSE", min_length=1, max_length=8)
    component: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=16,
        description=(
            "Optional component selector for multi-output indicators "
            "(e.g. 'lower' for Bollinger lower band, 'signal' for "
            "MACD signal line). Single-output indicators must leave "
            "this field unset."
        ),
    )
    offset: int = Field(
        default=0, ge=0, le=500,
        description=(
            "How many bars in the past to read. 0 = current bar; "
            "1 = previous bar; max 500. Same semantics as Pine "
            "Script's [n] operator."
        ),
    )

    @field_validator("indicator", "symbol", "exchange")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip()

    @field_validator("component")
    @classmethod
    def _norm_component(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        s = v.strip().lower()
        return s or None


class PriceNode(_Strict):
    """Last traded close for a symbol, or any other OHLC bar component
    selected via ``basis``. ``offset`` reads N bars in the past."""

    type: Literal["price"] = "price"
    symbol: str = Field(..., min_length=1, max_length=32)
    exchange: str = Field(default="NSE", min_length=1, max_length=8)
    basis: Literal["open", "high", "low", "close"] = Field(
        default="close",
        description=(
            "Bar component to read. Defaults to close. 'open' enables "
            "gap calculations; 'low' / 'high' enable intra-bar stop / "
            "target checks."
        ),
    )
    offset: int = Field(
        default=0, ge=0, le=500,
        description=(
            "How many bars in the past to read. 0 = current bar's "
            "basis; 1 = previous bar's basis; max 500."
        ),
    )

    @field_validator("symbol", "exchange")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip()


class VolumeNode(_Strict):
    """Volume summed over the last ``bars`` bars (default 1 = current
    bar). ``offset`` shifts the WINDOW backwards: offset=0 reads bars
    ending at the current bar; offset=5 reads bars ending 5 bars ago."""

    type: Literal["volume"] = "volume"
    symbol: str = Field(..., min_length=1, max_length=32)
    bars: int = Field(default=1, ge=1, le=500)
    exchange: str = Field(default="NSE", min_length=1, max_length=8)
    offset: int = Field(
        default=0, ge=0, le=500,
        description=(
            "Shift the volume window backwards by this many bars. "
            "0 = the window ends at the current bar."
        ),
    )


class ConstantNode(_Strict):
    """A literal number — the right-hand side of most comparisons."""

    type: Literal["constant"] = "constant"
    value: float = Field(..., description="Number literal. No NaN, no Inf.")

    @field_validator("value")
    @classmethod
    def _finite(cls, v: float) -> float:
        if math.isnan(v) or math.isinf(v):
            raise ValueError("constant must be a finite number")
        return float(v)


class PositionNode(_Strict):
    """Properties of the currently-open backtest/live position.

    Only meaningful inside an EXIT tree. ``validators.semantic_validate``
    rejects trees that put a ``position`` leaf in the entry-tree slot
    (where no position exists yet).

    ``field`` enumerates the addressable properties:

      ``entry_price``        - ₹ paid at entry; constant for the position's life.
      ``unrealised_pct``     - (current_price - entry_price) / entry_price,
                               signed. Defaults to bar CLOSE; pass ``basis``
                               to read from bar LOW (stops) or HIGH (targets).
      ``unrealised_abs``     - current_price - entry_price, signed ₹.
                               Same basis semantics as unrealised_pct.
      ``bars_held``          - integer count of bars since entry (>= 0).
      ``peak_unrealised_pct``- running max of unrealised_pct seen so far
                               this position. Always uses bar HIGH internally.
      ``drawdown_from_peak_pct`` - peak_unrealised_pct - unrealised_pct,
                               non-negative. For trailing-stop semantics.

    ``basis`` is only honoured for ``unrealised_pct`` / ``unrealised_abs``.
    Other fields ignore it.
    """

    type: Literal["position"] = "position"
    field: Literal[
        "entry_price",
        "unrealised_pct",
        "unrealised_abs",
        "bars_held",
        "peak_unrealised_pct",
        "drawdown_from_peak_pct",
    ]
    basis: Optional[Literal["close", "low", "high"]] = Field(
        default=None,
        description=(
            "Bar component to read for unrealised_pct / unrealised_abs. "
            "Defaults to 'close'. Use 'low' for stop-loss checks, 'high' "
            "for profit-target checks. Ignored for other fields."
        ),
    )


# ── Inner nodes — forward refs ───────────────────────────────────────
#
# Comparison and Logic both nest other nodes. Pydantic 2's
# discriminated-union types are declared as ``Annotated[Union[...],
# Field(discriminator=...)]`` and the resolution happens via the
# ``Tree`` alias below. We attach the union type to ``ComparisonNode``
# / ``LogicNode`` via forward references then ``model_rebuild()`` at
# the bottom of this module.


class ComparisonNode(_Strict):
    """A binary comparison: ``left <op> right``.

    Operators:
      - ``>``, ``<``, ``>=``, ``<=`` — numeric comparisons.
      - ``==`` — strict equality (rare; use sparingly on floats).
      - ``crosses_above`` — true on the tick where ``left`` transitions
        from ``≤ right`` to ``> right``. Requires the evaluator's
        previous-state plumbing.
      - ``crosses_below`` — symmetric.

    Both operands are recursive Trees, so either side can itself be a
    comparison's input — though in practice you'll only nest indicators
    inside comparisons inside logic nodes.
    """

    type: Literal["comparison"] = "comparison"
    op: Literal[
        ">", "<", ">=", "<=", "==",
        "crosses_above", "crosses_below",
    ]
    left: "Tree"
    right: "Tree"


class SessionDayNode(_Strict):
    """Boolean leaf: True when the as-of bar's date lands on one of
    the listed weekdays (Mon..Sun, lowercase 3-letter codes).

    Returns ``UNKNOWN`` if the accessor can't tell us the bar's date
    (live mode without market context). Useful for opening-range /
    weekly-rule strategies ("buy Tuesday on RSI<30, sell Wednesday on
    RSI>30") — the day-of-week filter combines with any other tree.

    ``days`` must be a non-empty subset of mon/tue/wed/thu/fri/sat/sun.
    """

    type: Literal["session_day"] = "session_day"
    days: list[
        Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    ] = Field(..., min_length=1, max_length=7)


class GapNode(_Strict):
    """Single-leaf gap percentage for a symbol:

        (today's open - yesterday's close) / yesterday's close

    Signed; negative = gap-down. Convenience shortcut for the very
    common "buy when X opens below yesterday's close by Y%" pattern
    so the LLM doesn't have to emit a depth-3 math sub-tree.
    """

    type: Literal["gap"] = "gap"
    symbol: str = Field(..., min_length=1, max_length=32)
    exchange: str = Field(default="NSE", min_length=1, max_length=8)


class PctChangeNode(_Strict):
    """N-bar percent change in CLOSE: (close - close[bars]) / close[bars].
    Signed. ``bars=5`` reads "today's close vs the close 5 bars ago"."""

    type: Literal["pct_change"] = "pct_change"
    symbol: str = Field(..., min_length=1, max_length=32)
    bars: int = Field(..., ge=1, le=500)
    exchange: str = Field(default="NSE", min_length=1, max_length=8)


class SpreadNode(_Strict):
    """Ratio of two symbols' close prices: price_a / price_b.

    Use for pairs / relative-strength signals
    (e.g. "TCS / INFY is in the bottom decile of its 252-day range")."""

    type: Literal["spread"] = "spread"
    a: str = Field(..., min_length=1, max_length=32,
                   description="Numerator symbol")
    b: str = Field(..., min_length=1, max_length=32,
                   description="Denominator symbol")
    exchange: str = Field(default="NSE", min_length=1, max_length=8)


class MathNode(_Strict):
    """Arithmetic operator over numeric sub-trees.

    Supported ops:
      Binary  : ``+``, ``-``, ``*``, ``/``    (2 operands)
      Unary   : ``abs``, ``negate``           (1 operand)
      Variadic: ``min``, ``max``               (2..8 operands)

    The validator (``validators.py``) enforces these counts per op.
    Returns ``UNKNOWN`` (None) when any operand is UNKNOWN, and on
    divide-by-zero.
    """

    type: Literal["math"] = "math"
    op: Literal[
        "+", "-", "*", "/", "abs", "negate", "min", "max",
    ]
    operands: list["Tree"] = Field(..., min_length=1, max_length=8)


class ConditionalNode(_Strict):
    """Pine Script's ``?:`` ternary — pick one of two values based on
    the truthiness of an ``if`` sub-tree.

    Semantics:
      - ``if`` MUST evaluate to a boolean (comparison or logic root).
        Numeric leaves on the ``if`` slot return ``UNKNOWN`` via
        Kleene rules.
      - ``then`` and ``else`` are arbitrary sub-trees. They may return
        either numbers or booleans; the validator only requires that
        BOTH branches return the same kind so the result type is
        deterministic.
      - When ``if`` is ``UNKNOWN``, the whole conditional is
        ``UNKNOWN`` (Kleene propagation).
      - When ``if`` is TRUE, the result is ``then``'s value; FALSE
        gives ``else``'s value. Only the chosen branch is evaluated
        — short-circuit semantics, important for cheaper trees.
    """

    type: Literal["conditional"] = "conditional"
    if_: "Tree" = Field(..., alias="if")
    then: "Tree"
    else_: "Tree" = Field(..., alias="else")


class AggregateNode(_Strict):
    """Look back over the last ``bars`` bars of a source sub-tree and
    reduce to a scalar.

    Supported ops (all returning a number unless flagged):

      ``highest``      / ``lowest``    - max / min over the window
      ``sum``          / ``avg``       - sum / mean (NaN-aware)
      ``std``                          - sample standard deviation
      ``percentrank``                  - 0..1, fraction of window strictly
                                         below the CURRENT value of source
      ``zscore``                       - (current - mean) / std
      ``barssince``                    - integer count of bars since
                                         source was last TRUE (source must
                                         evaluate to boolean); returns
                                         UNKNOWN when no fire seen in window
      ``valuewhen``                    - value of ``second`` at the last bar
                                         where ``source`` was TRUE
      ``count_when`` / ``any_when``    - count of TRUE bars (count) or 1.0/0.0
                                         (any) — source must be boolean
      ``correlation``                  - Pearson correlation between
                                         ``source`` and ``second`` over the
                                         window. Requires ``second``.

    ``bars`` is bounded [1, 2000] — large windows are valid for
    percentile-of-year-style regime detection.
    """

    type: Literal["aggregate"] = "aggregate"
    op: Literal[
        "highest", "lowest", "sum", "avg", "std",
        "count_when", "any_when",
        "percentrank", "zscore",
        "barssince", "valuewhen", "correlation",
    ]
    source: "Tree"
    bars: int = Field(..., ge=1, le=2000)
    # ``second`` is only meaningful for binary ops (correlation,
    # valuewhen). Validator rejects it for the unary ops to catch
    # planner bugs early.
    second: Optional["Tree"] = Field(default=None)


class LogicNode(_Strict):
    """``and`` / ``or`` / ``not`` over a list of sub-trees.

    Validation rules:
      - ``and``/``or`` need at least 2 operands (1 operand reduces to
        the operand itself; reject with a clear error so the planner
        emits a tighter tree).
      - ``not`` needs exactly 1 operand.
      - Cap operand count at 8 to bound the LLM's emission size.
    """

    type: Literal["logic"] = "logic"
    op: Literal["and", "or", "not"]
    operands: list["Tree"] = Field(..., min_length=1, max_length=8)

    @model_validator(mode="after")
    def _operand_count_matches_op(self) -> "LogicNode":
        if self.op == "not" and len(self.operands) != 1:
            raise ValueError("logic.not expects exactly 1 operand")
        if self.op in ("and", "or") and len(self.operands) < 2:
            raise ValueError(
                f"logic.{self.op} expects at least 2 operands "
                f"(got {len(self.operands)})"
            )
        return self


# ── Discriminated union ──────────────────────────────────────────────


Tree = Annotated[
    Union[
        IndicatorNode,
        PriceNode,
        VolumeNode,
        ConstantNode,
        PositionNode,
        SessionDayNode,
        GapNode,
        PctChangeNode,
        SpreadNode,
        MathNode,
        ConditionalNode,
        AggregateNode,
        ComparisonNode,
        LogicNode,
    ],
    Field(discriminator="type"),
]


# Resolve the forward references in nodes with recursive Tree children.
ComparisonNode.model_rebuild()
LogicNode.model_rebuild()
ConditionalNode.model_rebuild()
AggregateNode.model_rebuild()
MathNode.model_rebuild()
