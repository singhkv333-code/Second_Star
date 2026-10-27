"""Honest capability contract for execution-mode model calls.

Only tools with a working execution path are advertised. The model chooses
between them; there is deliberately no keyword or example router in front of
the call.
"""
from __future__ import annotations

import copy
from typing import Any

from backend.execution.spec import StrategySpec


def _compact_schema(value: Any) -> Any:
    """Keep validation semantics while removing token-heavy display metadata."""
    if isinstance(value, list):
        return [_compact_schema(item) for item in value]
    if not isinstance(value, dict):
        return value
    return {
        key: _compact_schema(item)
        for key, item in value.items()
        if key not in {"title", "description", "examples"}
    }


def _is_tree_union(value: dict[str, Any]) -> bool:
    discriminator = value.get("discriminator")
    return (
        isinstance(discriminator, dict)
        and discriminator.get("propertyName") == "type"
        and isinstance(discriminator.get("mapping"), dict)
        and isinstance(value.get("oneOf"), list)
    )


def _shallow_tree_node(value: dict[str, Any]) -> dict[str, Any]:
    """Non-recursive transport reference; the backend validates full depth."""
    names = sorted(value["discriminator"]["mapping"])
    return {
        "type": "object",
        "properties": {"type": {"type": "string", "enum": names}},
        "required": ["type"],
        "additionalProperties": True,
    }


def _prune_nested_tree_unions(value: Any) -> Any:
    if isinstance(value, list):
        return [_prune_nested_tree_unions(item) for item in value]
    if not isinstance(value, dict):
        return value
    if _is_tree_union(value):
        return _shallow_tree_node(value)
    return {key: _prune_nested_tree_unions(item) for key, item in value.items()}


def _transport_strategy_schema() -> dict[str, Any]:
    """Typed at both roots, non-recursive on the provider wire.

    Azure expands recursive function schemas before token accounting. Keep the
    full discriminated union for entry/exit roots, expose every node's fields
    once in ``$defs``, and represent recursive children as another typed node.
    ``StrategySpec`` remains the authoritative recursive validator after the
    call, so depth and semantic validation are unchanged.
    """
    schema = _compact_schema(StrategySpec.model_json_schema())
    tree_union = copy.deepcopy(schema["properties"]["entry"])
    schema["$defs"] = {
        name: _prune_nested_tree_unions(definition)
        for name, definition in schema.get("$defs", {}).items()
    }
    schema["properties"]["entry"] = copy.deepcopy(tree_union)
    exit_tree = schema["$defs"]["ExitSpec"]["properties"]["tree"]
    exit_tree.clear()
    exit_tree.update({"anyOf": [copy.deepcopy(tree_union), {"type": "null"}]})
    return schema


_SPEC_SCHEMA = _transport_strategy_schema()


def _object(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"type": "object", "additionalProperties": False,
            "properties": properties, "required": required}


def execution_tool_parameters() -> dict[str, Any]:
    spec = dict(_SPEC_SCHEMA)
    defs = spec.pop("$defs", {})
    properties: dict[str, Any] = {
        "strategy_spec": spec,
        "operation": {"type": "string", "enum": ["build", "backtest"],
                      "default": "build"},
        "start_date": {"type": "string", "format": "date"},
        "end_date": {"type": "string", "format": "date"},
        "starting_capital": {"type": "number", "exclusiveMinimum": 0},
    }
    required = ["strategy_spec"]
    out = _object(properties, required)
    if defs:
        out["$defs"] = defs
    return out


EXECUTION_TOOLS: list[dict[str, Any]] = [
    {
        "name": "build_execution_strategy",
        "description": (
            "Build, revise, or backtest a complete StrategySpec. Build returns "
            "an editable approval-gated workflow draft; backtest requires "
            "explicit dates and starting capital. Neither operation persists, "
            "activates, or executes."
        ),
        "parameters": execution_tool_parameters(),
    },
]
