"""Execution-mode v1 API: inspect, validate and compile StrategySpec."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import ValidationError

from backend.execution.spec import StrategySpec, compile_workflow, strategy_readback, validate_strategy
from backend.execution.tools import EXECUTION_TOOLS
from backend.routers._deps import require_user
from backend.services.backtest_indicators import indicator_setting_schema, supported_indicators
from backend.workflows.dsl.validators import DSLValidationError

router = APIRouter(prefix="/api/execution", tags=["ExecutionMode"])


@router.get("/capabilities")
async def capabilities(_user_id: int = Depends(require_user)) -> dict:
    return {
        "spec_version": "1",
        "tools": EXECUTION_TOOLS,
        "indicators": [
            {"name": name, "settings": indicator_setting_schema(name)}
            for name in supported_indicators()
        ],
        "aggregate_operations": ["highest", "lowest", "sum", "avg", "ema", "wma", "std", "count_when", "any_when", "percentrank", "zscore", "barssince", "valuewhen", "correlation"],
        "execution_boundary": "draft_and_register_only",
    }


@router.post("/strategies/validate")
async def validate(payload: StrategySpec, _user_id: int = Depends(require_user)) -> dict:
    try:
        entry, exit_tree = validate_strategy(payload)
        return {"valid": True, "strategy_spec": payload.model_dump(mode="json"),
                "canonical": {"entry": entry.model_dump(mode="json"),
                              "exit": exit_tree.model_dump(mode="json") if exit_tree else None},
                "readback": strategy_readback(payload)}
    except (ValidationError, DSLValidationError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/strategies/compile")
async def compile_strategy(payload: StrategySpec, _user_id: int = Depends(require_user)) -> dict:
    try:
        return compile_workflow(payload)
    except (ValidationError, DSLValidationError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
