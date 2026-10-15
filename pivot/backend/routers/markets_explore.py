"""The Markets page (`/api/markets/explore`, `/api/markets/brief`).

Thin: everything is computed in `services/market_explore.py`.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from fastapi.concurrency import run_in_threadpool

from backend.routers._deps import require_user
from backend.routers._errors import http_error
from backend.services import market_explore

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/markets", tags=["Markets"])


@router.get("/explore", summary="The 500 largest companies: breadth, sectors and every stock's numbers")
async def get_explore(_user_id: int = Depends(require_user)) -> dict:
    try:
        data = await run_in_threadpool(market_explore.build)
    except Exception as exc:  # noqa: BLE001
        logger.exception("markets explore failed")
        raise http_error(503, "not_yet_available", "market data is temporarily unavailable") from exc
    if not data.get("stocks"):
        raise http_error(503, "not_yet_available", "market data is temporarily unavailable")
    return data


@router.get("/brief", summary="Three sentences on the session, from the explore numbers only")
async def get_brief(_user_id: int = Depends(require_user)) -> dict:
    try:
        data = await run_in_threadpool(market_explore.build)
    except Exception as exc:  # noqa: BLE001
        raise http_error(503, "not_yet_available", "market data is temporarily unavailable") from exc
    if not data.get("stocks"):
        raise http_error(503, "not_yet_available", "market data is temporarily unavailable")
    out = await market_explore.brief(data)
    return {"text": out["text"], "source": out["source"], "as_of": data["as_of"]}
