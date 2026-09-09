from __future__ import annotations

import pytest

from backend.execution.spec import StrategySpec, compile_workflow, strategy_readback
from backend.execution.prompt import EXECUTION_MODE_SYSTEM
from backend.execution.tools import EXECUTION_TOOLS
from backend.services.backtest_indicators import validate_indicator_settings
from backend.workflows.dsl.schema import ComparisonNode, ConstantNode, IndicatorNode
from backend.workflows.dsl.validators import DSLValidationError, semantic_validate


def _spec() -> StrategySpec:
    return StrategySpec.model_validate({
        "name": "Custom RSI recovery",
        "instrument": {"symbol": "reliance", "interval": "15m"},
        "entry": {
            "type": "comparison", "op": "crosses_above",
            "left": {"type": "indicator", "indicator": "rsi", "symbol": "RELIANCE", "period": 21},
            "right": {"type": "aggregate", "op": "ema", "bars": 10,
                      "source": {"type": "indicator", "indicator": "rsi", "symbol": "RELIANCE", "period": 21}},
        },
        "exit": {"kind": "tree", "tree": {
            "type": "comparison", "op": ">=",
            "left": {"type": "position", "field": "unrealised_pct"},
            "right": {"type": "constant", "value": 0.08},
        }},
        "order": {"quantity": 10},
        "assumptions": ["RSI average means EMA rather than SMA"],
    })


def test_nested_indicator_custom_average_survives_readback() -> None:
    text = strategy_readback(_spec())
    assert "RSI(21)" in text["entry"]
    assert "exponential average" in text["entry"]
    assert "10 bars" in text["entry"]


def test_compile_is_approval_gated_and_preserves_exit() -> None:
    draft = compile_workflow(_spec())
    assert [s["step_type"] for s in draft["steps"]] == [
        "trigger.compound", "action.place_order", "trigger.exit_compound",
        "fetch.portfolio", "action.place_order",
    ]
    orders = [s for s in draft["steps"] if s["step_type"] == "action.place_order"]
    assert all(s["config"]["requires_approval"] for s in orders)
    assert orders[0]["config"]["quantity"] == 10


def test_macd_secondary_settings_are_validated() -> None:
    assert validate_indicator_settings("macd", {"fast": 5, "slow": 35, "signal": 7}) == {
        "fast": 5, "slow": 35, "signal": 7,
    }
    node = ComparisonNode(op=">", left=IndicatorNode(
        indicator="macd", symbol="INFY", period=35,
        settings={"fast": 40, "slow": 20}), right=ConstantNode(value=0))
    with pytest.raises(DSLValidationError, match="fast.*smaller"):
        semantic_validate(node)


def test_unknown_indicator_setting_is_not_silently_dropped() -> None:
    node = ComparisonNode(op=">", left=IndicatorNode(
        indicator="rsi", symbol="INFY", period=14,
        settings={"mystery_smoothing": 5}), right=ConstantNode(value=0))
    with pytest.raises(DSLValidationError, match="does not support"):
        semantic_validate(node)


def test_strategy_schema_exposes_the_recursive_tree_contract() -> None:
    schema = StrategySpec.model_json_schema()
    entry = schema["properties"]["entry"]
    assert entry["discriminator"]["propertyName"] == "type"
    assert len(entry["oneOf"]) >= 10
    assert "ComparisonNode" in schema["$defs"]
    exit_tree = schema["$defs"]["ExitSpec"]["properties"]["tree"]
    assert any("oneOf" in branch for branch in exit_tree["anyOf"])


def test_execution_contract_is_capability_driven_not_example_driven() -> None:
    prompt = EXECUTION_MODE_SYSTEM.lower()
    for example_token in ("reliance", "rsi(", "ema(", "buy 10"):
        assert example_token not in prompt
    assert "keyword or phrase routing" in prompt
    assert {tool["name"] for tool in EXECUTION_TOOLS} == {
        "build_execution_strategy",
    }
    operations = EXECUTION_TOOLS[0]["parameters"]["properties"]["operation"]
    assert operations["enum"] == ["build", "backtest"]


@pytest.mark.parametrize("entry", [
    {
        "type": "comparison", "op": ">",
        "left": {"type": "price", "symbol": "INFY", "basis": "close"},
        "right": {
            "type": "aggregate", "op": "highest", "bars": 40,
            "source": {"type": "price", "symbol": "INFY", "basis": "close", "offset": 1},
        },
    },
    {
        "type": "logic", "op": "and", "operands": [
            {
                "type": "comparison", "op": ">",
                "left": {"type": "volume", "symbol": "HDFCBANK"},
                "right": {
                    "type": "aggregate", "op": "avg", "bars": 30,
                    "source": {"type": "volume", "symbol": "HDFCBANK"},
                },
            },
            {
                "type": "comparison", "op": "<",
                "left": {"type": "gap", "symbol": "HDFCBANK"},
                "right": {"type": "constant", "value": 0},
            },
        ],
    },
])
def test_different_tree_families_compile_without_strategy_templates(entry: dict) -> None:
    spec = StrategySpec.model_validate({
        "name": "Generated rule",
        "instrument": {"symbol": "INFY" if "INFY" in str(entry) else "HDFCBANK"},
        "entry": entry,
        "exit": {"kind": "hold_to_end"},
        "order": {"quantity": 3},
    })
    draft = compile_workflow(spec)
    assert draft["_render_hint"] == "workflow_draft_card"
    assert draft["steps"][0]["config"]["entry"]["type"] == entry["type"]
