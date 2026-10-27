"""Webhook endpoint (API_CONTRACT.md §9).

  - POST /api/webhooks/{token}     unauth, body: any JSON

External systems POST to this endpoint to fire workflows that have a
`trigger.webhook` step at index 0. The body is stored in
`run.context["webhook_payload"]` (literal key) so downstream steps can
reference it via `{{ context.webhook_payload.<path> }}`.

Auth: token alone — no JWT. The token IS the auth proof. Tokens live
in `workflow_webhook_tokens` (not in step config JSON, per
ARCHITECTURE.md "secrets" rule).

Rate limit: 60/min per token via Redis (or MockRedis in dev/test). The
token row is the rate-limit subject; legitimate clients won't fire
that often.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from backend.cache import redis_client
from backend.database import get_db
from backend.models import (
    RunStatus,
    Workflow,
    WorkflowRun,
    WorkflowStatus,
    WorkflowWebhookToken,
)
from backend.routers._errors import (
    not_found,
    rate_limited,
    state_conflict,
    validation_error,
)
from backend.workflows.engine import WorkflowEngine

router = APIRouter(prefix="/api", tags=["Agents"])

logger = logging.getLogger(__name__)


# Per-token sliding-window rate limit. Implementation uses a Redis
# counter keyed by token+minute-bucket; works against MockRedis too
# (in-memory dict). 60 requests per 60-second bucket.
_RATE_LIMIT_PER_MIN = 60


def _check_rate_limit(token: str) -> bool:
    """Return True if under the limit, False if exceeded.

    The Redis client is shared with the rest of the app. We use a per-
    minute bucket key so a burst of 60 in 1s still passes; the next
    request in the same minute is rejected. Trade-off vs sliding
    window: simpler, faster, good enough for v1.
    """
    bucket = int(time.time() // 60)
    key = f"wf:webhook:{token}:{bucket}"
    try:
        # MockRedis.set always returns True; real Redis returns OK.
        # We need INCR semantics — fall back to get/set for MockRedis.
        if hasattr(redis_client, "incr"):
            count = int(redis_client.incr(key))
            if count == 1:
                # Best-effort TTL so stale buckets don't accumulate.
                if hasattr(redis_client, "expire"):
                    redis_client.expire(key, 70)
            return count <= _RATE_LIMIT_PER_MIN
        # MockRedis path: read, increment, write.
        cur = redis_client.get(key)
        n = int(cur) if cur else 0
        n += 1
        redis_client.set(key, str(n), ex=70)
        return n <= _RATE_LIMIT_PER_MIN
    except Exception as e:
        logger.warning(
            "webhook rate-limit check failed (allowing through): %s", e,
        )
        return True


@router.post(
    "/webhooks/{token}",
    status_code=202,
    summary="External webhook fire",
)
async def fire_webhook(
    token: str,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Unauthenticated. Token presence + validity is the only auth.

    Reads the request body, validates the token, checks the workflow
    is `active`, creates a run row with `triggered_by='webhook'` and
    `context['webhook_payload']` populated, then enqueues the run on
    the engine. Returns the new run_id.
    """
    if not _check_rate_limit(token):
        raise rate_limited(
            f"webhook rate limit exceeded ({_RATE_LIMIT_PER_MIN}/min)",
        )

    row = (
        db.query(WorkflowWebhookToken)
        .filter(WorkflowWebhookToken.token == token)
        .first()
    )
    if row is None:
        raise not_found("unknown webhook token")

    wf = db.query(Workflow).filter_by(id=row.workflow_id).first()
    if wf is None:
        # Token row should cascade-delete with workflow; defensive.
        raise not_found("workflow not found")
    if wf.status != WorkflowStatus.active:
        raise state_conflict(
            "workflow not active",
            details={"current_status": wf.status.value
                     if hasattr(wf.status, "value")
                     else str(wf.status)},
        )

    # Body may be empty or non-JSON; treat both as `{}`.
    try:
        raw = await request.body()
        payload: Any = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        raise validation_error(
            "request body is not valid JSON",
            details={"reason": "invalid_json"},
        )
    if not isinstance(payload, dict):
        # Per the contract, the body is stored at
        # context["webhook_payload"]. Top-level non-objects (lists,
        # primitives) are wrapped so the literal key always points to
        # a dict for downstream refs.
        payload = {"_value": payload}

    run = WorkflowRun(
        workflow_id=wf.id,
        workflow_version=int(wf.version),
        triggered_by="webhook",
        status=RunStatus.running,
        context={"webhook_payload": payload},
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    engine = WorkflowEngine()
    asyncio.create_task(engine.execute_run(str(run.id)))

    return {"run_id": str(run.id)}
