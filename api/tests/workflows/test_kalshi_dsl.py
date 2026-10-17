"""trigger.kalshi DSL registration + config validation + compat."""
from __future__ import annotations

import pytest

from backend.workflows.compat import CAPABILITY_RULES
from backend.workflows.propose import validate_draft_against_registry
from backend.workflows.registry import STEP_REGISTRY
from backend.workflows.schemas import TriggerKalshiConfig


def test_trigger_kalshi_registered() -> None:
    assert "trigger.kalshi" in STEP_REGISTRY
    defn = STEP_REGISTRY["trigger.kalshi"]
    assert defn.trigger_only is True
    assert defn.max_retries == 0


def test_trigger_kalshi_known_to_compat() -> None:
    assert "trigger.kalshi" in CAPABILITY_RULES


def test_kalshi_config_threshold_requires_value() -> None:
    with pytest.raises(ValueError):
        TriggerKalshiConfig.model_validate({
            "market_id": "T", "token_id": "T:YES", "mode": "threshold",
        })


def test_kalshi_config_resolution_ok_without_threshold() -> None:
    cfg = TriggerKalshiConfig.model_validate({
        "market_id": "T", "token_id": "T:NO", "side": "NO",
        "mode": "resolution", "resolve_on": "NO",
    })
    assert cfg.side == "NO"
    assert cfg.mode == "resolution"


def test_kalshi_workflow_validates_end_to_end() -> None:
    """A resolved trigger.kalshi step + action survives full registry +
    allow-list + lint validation (prediction-market family is allowed)."""
    draft = validate_draft_against_registry({
        "name": "buy on kalshi resolve",
        "steps": [
            {"step_type": "trigger.kalshi", "config": {
                "market_id": "KXFED-26JAN-H",
                "token_id": "KXFED-26JAN-H:YES",
                "side": "YES", "mode": "resolution", "resolve_on": "YES",
            }},
            {"step_type": "action.place_order", "config": {
                "symbol": "NIFTYBEES", "side": "buy",
                "quantity": 1, "order_type": "market",
            }},
        ],
    })
    assert draft.steps[0].step_type == "trigger.kalshi"
