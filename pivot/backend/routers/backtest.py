"""
Backtest router — full implementation.

POST /backtest/run     — run a backtest from a strategy_definition dict
POST /backtest/parse   — parse a natural-language strategy request
GET  /backtest/presets — return pre-built strategy presets
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from backend.auth.jwt_handler import get_user_id_from_token
from backend.backtester.engine import run_backtest
from backend.backtester.parser import parse_strategy
from backend.cache import redis_client

router = APIRouter(prefix="/backtest", tags=["Backtest"])
logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 3600


def get_user_id(authorization: str = Header(None)) -> int:
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing token")
    uid = get_user_id_from_token(authorization.replace("Bearer ", ""))
    if not uid:
        raise HTTPException(status_code=401, detail="Invalid token")
    return uid


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class RunRequest(BaseModel):
    strategy_definition: dict
    starting_capital: Optional[float] = Field(default=None)


class ParseRequest(BaseModel):
    message: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _strategy_cache_key(strategy: dict) -> str:
    canonical = json.dumps(strategy, sort_keys=True, default=str)
    digest = hashlib.sha1(canonical.encode("utf-8")).hexdigest()
    return f"backtest:{digest}"


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/run")
async def run_endpoint(req: RunRequest, user_id: int = Depends(get_user_id)) -> dict:
    strategy = dict(req.strategy_definition or {})
    if not strategy.get("symbol"):
        raise HTTPException(status_code=400, detail="strategy_definition.symbol is required")
    has_new_entry = isinstance(strategy.get("entry"), dict) and strategy["entry"].get("conditions")
    has_legacy_entry = bool(strategy.get("entry_signal"))
    if not (has_new_entry or has_legacy_entry):
        raise HTTPException(
            status_code=400,
            detail="strategy_definition.entry (with conditions) or entry_signal is required",
        )
    if req.starting_capital is not None:
        strategy["starting_capital"] = float(req.starting_capital)
    strategy.setdefault("starting_capital", 500_000.0)

    cache_key = _strategy_cache_key(strategy)
    try:
        cached = redis_client.get(cache_key)
        if cached:
            raw = cached.decode() if isinstance(cached, (bytes, bytearray)) else cached
            return json.loads(raw)
    except Exception as e:
        logger.debug(f"Backtest cache miss for {cache_key}: {e}")

    try:
        result = await run_backtest(strategy)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Backtest failed")
        raise HTTPException(status_code=500, detail=f"Backtest failed: {e}")

    try:
        redis_client.set(cache_key, json.dumps(result, default=str),
                          ex=CACHE_TTL_SECONDS)
    except Exception as e:
        logger.debug(f"Backtest cache write failed for {cache_key}: {e}")

    return result


@router.post("/parse")
async def parse_endpoint(req: ParseRequest, user_id: int = Depends(get_user_id)) -> dict:
    parsed = await parse_strategy(req.message or "")
    if parsed is None:
        return {"status": "not_backtest"}
    if parsed.get("status") == "needs_clarification":
        return parsed
    if parsed.get("status") == "ready":
        return parsed
    return {"status": "not_backtest"}


@router.get("/presets")
def presets_endpoint() -> list[dict]:
    base = {
        "starting_capital": 500_000.0,
        "max_positions": 5,
        "benchmark": "NIFTY50",
        "calendar_filter": None,
        "stop_loss_pct": None,
        "take_profit_pct": None,
        "position_size_pct": None,
        "period": "3y",
    }
    return [
        {
            "id": "rsi_oversold",
            "name": "RSI Oversold Entry",
            "description": "Buy when RSI(14) drops below 30. Sell when RSI crosses above 70.",
            "strategy": {
                **base,
                "symbol": "NIFTYBEES",
                "entry_signal": "rsi_cross_below",
                "entry_params": {"period": 14, "threshold": 30.0},
                "exit_signal": "rsi_cross_above",
                "exit_params": {"period": 14, "threshold": 70.0},
                "position_size_inr": 50_000.0,
            },
        },
        {
            "id": "52wk_high_momentum",
            "name": "52-Week High Momentum",
            "description": "Buy every time a stock makes a new 52-week high.",
            "strategy": {
                **base,
                "symbol": "NIFTYBEES",
                "entry_signal": "price_52wk_high",
                "entry_params": {},
                "exit_signal": "hold",
                "exit_params": {},
                "position_size_inr": 50_000.0,
            },
        },
        {
            "id": "macd_crossover",
            "name": "MACD Golden Cross",
            "description": "Buy on MACD bullish crossover. Sell on bearish crossover.",
            "strategy": {
                **base,
                "symbol": "NIFTYBEES",
                "entry_signal": "macd_cross_above_signal",
                "entry_params": {"fast": 12, "slow": 26, "signal": 9},
                "exit_signal": "macd_cross_below_signal",
                "exit_params": {"fast": 12, "slow": 26, "signal": 9},
                "position_size_inr": 50_000.0,
            },
        },
        {
            "id": "weekly_sip_sma_filter",
            "name": "Monday SIP with SMA Filter",
            "description": "Buy every Monday only when price is above its 50-day SMA.",
            "strategy": {
                **base,
                "symbol": "NIFTYBEES",
                "entry_signal": "calendar",
                "entry_params": {
                    "weekday": 0,
                    "price_condition": "above",
                    "sma_period": 50,
                },
                "exit_signal": "hold",
                "exit_params": {},
                "position_size_inr": 10_000.0,
            },
        },
        {
            "id": "bb_lower_bounce",
            "name": "Bollinger Band Lower Touch",
            "description": "Buy when price touches the lower Bollinger Band.",
            "strategy": {
                **base,
                "symbol": "NIFTYBEES",
                "entry_signal": "bb_lower_touch",
                "entry_params": {"period": 20, "std": 2.0},
                "exit_signal": "hold",
                "exit_params": {},
                "position_size_inr": 50_000.0,
            },
        },
    ]
