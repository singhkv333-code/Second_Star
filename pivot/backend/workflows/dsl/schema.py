"""Recursive Pydantic schema for the v1 condition tree.

Seven node types behind a single discriminator field ``type``:

  - ``indicator``  — RSI / SMA / EMA / MACD / ATR / ... value
  - ``price``      — last-traded price for a symbol
  - ``volume``     — bar volume (summed over the last N bars)
  - ``constant``   — a literal number
  - ``position``   — properties of the currently-open position;
                    only meaningful inside an EXIT tree
  - ``comparison`` — two operand sub-trees + an operator
  - ``logic``      — and / or / not joining operand sub-trees

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
    """Latest traded price for a symbol."""

    type: Literal["price"] = "price"
    symbol: str = Field(..., min_length=1, max_length=32)
    exchange: str = Field(default="NSE", min_length=1, max_length=8)

    @field_validator("symbol", "exchange")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip()


class VolumeNode(_Strict):
    """Volume summed over the last ``bars`` bars (default 1 = current bar)."""

    type: Literal["volume"] = "volume"
    symbol: str = Field(..., min_length=1, max_length=32)
    bars: int = Field(default=1, ge=1, le=500)
    exchange: str = Field(default="NSE", min_length=1, max_length=8)


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
        ComparisonNode,
        LogicNode,
    ],
    Field(discriminator="type"),
]


# Resolve the forward references in Comparison / Logic.
ComparisonNode.model_rebuild()
LogicNode.model_rebuild()
