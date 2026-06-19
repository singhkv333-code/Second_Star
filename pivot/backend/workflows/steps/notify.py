"""Communication step executors.

notify.* steps have max_retries=2 — sending the same email twice is
annoying but not destructive, and external delivery APIs flake.

For Day 2 we ship `notify.message` as a logging executor: until the
real email/SMS/push channels are wired up we'd rather log loudly than
fake-deliver. ARCHITECTURE.md §5.5 footnote: "Don't fake-send."

`wait.approval` is also in this module since it's the user-
communication gating step. Day 2 implementation creates the approval
row and signals the engine to pause; resumption is handled by the
approvals router which calls engine.resume_run().
"""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any, Optional

from backend.models import WorkflowApproval
from backend.workflows.engine import _AwaitingApproval, _utcnow
from backend.workflows.registry import register_step
from backend.workflows.schemas import (
    NotifyLogConfig,
    NotifyMessageConfig,
    WaitApprovalConfig,
)

logger = logging.getLogger(__name__)


def _try_delegate_notify(
    channel: str, body: str, vars_: dict[str, Any],
) -> bool:
    """Best-effort delegation to a future notify service. Returns
    True if delivered, False if no delivery surface exists yet — in
    which case we fall through to the log-and-record path.

    Design choice: we lazy-import here so Day-2 ships without a hard
    dependency on a notify service module. Day 3+ implementation lands
    in `backend/services/notify.py`."""
    try:
        from backend.services import notify as notify_service  # type: ignore
    except ImportError:
        return False
    fn = getattr(notify_service, "send", None)
    if not callable(fn):
        return False
    try:
        fn(channel=channel, body=body, vars=vars_)
        return True
    except Exception as e:
        logger.warning("notify service raised: %s — recording log only", e)
        return False


@register_step(
    step_type="notify.message",
    category="notify",
    label="Send a notification",
    description="Send a push notification (email / SMS coming later).",
    icon="send",
    max_retries=2,
    trigger_only=False,
    config_model=NotifyMessageConfig,
    group="Notifications",
    output_schema={
        "type": "object",
        "properties": {
            "channel": {"type": "string"},
            "delivered": {"type": "boolean"},
            "log": {"type": "string"},
        },
        "required": ["channel", "delivered"],
    },
)
async def execute_notify_message(ctx: Any) -> Optional[dict[str, Any]]:
    """Render the template (refs are already resolved by the engine)
    and either delegate to backend.services.notify if it exists, or
    log and persist a record to the run-step output.

    Output shape includes a `log` field so the run-step output is
    self-contained — the UI can render the message it would have sent
    even when no real channel is wired."""
    cfg = ctx.config
    channel = cfg["channel"]
    template = cfg["template"]
    vars_ = dict(cfg.get("vars") or {})

    # Render the template with simple {key} formatting. Template-side
    # refs were already resolved by the engine — we only do {key}
    # substitution here for vars-supplied placeholders.
    try:
        body = template.format(**vars_)
    except KeyError as e:
        body = template
        logger.info(
            "notify.message: template placeholder %s missing in vars",
            e,
        )

    delivered = _try_delegate_notify(channel, body, vars_)

    log_line = (
        f"[{channel}] {body}"
        if delivered
        else f"[{channel}] (logged, no service wired) {body}"
    )
    logger.info("workflow notify: run=%s step=%d %s",
                ctx.run.id, ctx.step.step_index, log_line)

    return {
        "channel": channel,
        "delivered": delivered,
        "log": log_line,
    }


@register_step(
    step_type="notify.log",
    category="notify",
    label="Add a run note",
    description="Write a line into this run's log — no external message.",
    icon="file-text",
    max_retries=2,
    trigger_only=False,
    config_model=NotifyLogConfig,
    group="Notifications",
    output_schema={
        "type": "object",
        "properties": {"log": {"type": "string"}},
        "required": ["log"],
    },
)
async def execute_notify_log(ctx: Any) -> Optional[dict[str, Any]]:
    """Pure no-side-effect log step — useful for debugging and for the
    propose_workflow tool to drop breadcrumbs into a run."""
    msg = str(ctx.config["message"])
    logger.info(
        "workflow notify.log: run=%s step=%d %s",
        ctx.run.id, ctx.step.step_index, msg,
    )
    return {"log": msg}


@register_step(
    step_type="wait.approval",
    category="notify",
    label="Pause for my approval",
    description="Pause the run until you approve or reject it in the app.",
    icon="hand",
    max_retries=0,
    trigger_only=False,
    config_model=WaitApprovalConfig,
    group="Approvals",
    output_schema={
        "type": "object",
        "properties": {
            "decision": {"type": "string", "enum": ["approved", "rejected"]},
            "decided_at": {"type": "string", "format": "date-time"},
        },
        "required": ["decision"],
    },
)
async def execute_wait_approval(ctx: Any) -> Optional[dict[str, Any]]:
    """First entry creates the approval row and raises
    _AwaitingApproval — the engine pauses the run.

    On resume (`existing.decision == 'approved'`), we return the
    decision payload. Rejection is handled by the approvals router,
    which terminates the run before re-entry; this executor never sees
    a 'rejected' state."""
    existing = (
        ctx.db.query(WorkflowApproval)
        .filter(
            WorkflowApproval.run_id == ctx.run.id,
            WorkflowApproval.step_index == ctx.step.step_index,
        )
        .order_by(WorkflowApproval.requested_at.desc())
        .first()
    )

    if existing is None or existing.decision is None:
        cfg = ctx.config
        approval = WorkflowApproval(
            run_id=ctx.run.id,
            step_index=ctx.step.step_index,
            expires_at=_utcnow() + timedelta(
                minutes=int(cfg.get("expires_in_minutes", 15)),
            ),
            summary=str(cfg["summary"]),
        )
        ctx.db.add(approval)
        ctx.db.commit()
        ctx.db.refresh(approval)
        raise _AwaitingApproval(approval.id)

    if existing.decision == "rejected":
        # Defensive — see action.place_order's matching branch.
        raise RuntimeError(
            f"approval rejected at step {ctx.step.step_index}"
        )

    return {
        "decision": existing.decision,
        "decided_at": (
            existing.decided_at.isoformat() if existing.decided_at else None
        ),
    }
