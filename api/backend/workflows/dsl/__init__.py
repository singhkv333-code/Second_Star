"""Domain-specific language for compound trigger conditions.

This package adds a small tree-based grammar that lets a workflow
express conditions like "RSI(TCS, 14) < 30 AND price(NIFTY) > 23000"
as data, instead of forcing the engine to grow a new step type per
combination of conditions.

Architecture:

  - ``schema``       — recursive Pydantic models for tree nodes.
  - ``data_accessor``— abstraction over live-quote / indicator lookups
                       so the SAME tree evaluator runs in live mode
                       (watcher tick) and backtest mode (historical
                       bars) without code changes.
  - ``evaluator``    — pure walker that returns True / False / None.
                       Stateful only via an explicit ``prev_state``
                       dict for ``crosses_above`` / ``crosses_below``.
  - ``validators``   — semantic checks (depth, indicator-registry
                       lookup) beyond what Pydantic does at parse time.
  - ``readback``     — tree → human-readable English, used by the
                       chat confirmation card.

The whole thing is opt-in via the ``trigger.compound`` step type
(``backend.workflows.steps.triggers``). Existing single-condition
``trigger.price`` / ``trigger.indicator`` workflows keep working
unchanged.
"""
from __future__ import annotations

from backend.workflows.dsl.evaluator import (
    EvaluationResult,
    Ternary,
    evaluate,
)
from backend.workflows.dsl.readback import tree_to_english
from backend.workflows.dsl.schema import (
    ComparisonNode,
    ConstantNode,
    IndicatorNode,
    LogicNode,
    PriceNode,
    Tree,
    VolumeNode,
)
from backend.workflows.dsl.validators import (
    DSLValidationError,
    semantic_validate,
)

__all__ = [
    "ComparisonNode",
    "ConstantNode",
    "DSLValidationError",
    "EvaluationResult",
    "IndicatorNode",
    "LogicNode",
    "PriceNode",
    "Ternary",
    "Tree",
    "VolumeNode",
    "evaluate",
    "semantic_validate",
    "tree_to_english",
]
