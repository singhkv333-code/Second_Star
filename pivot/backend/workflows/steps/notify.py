"""Communication step stubs.

notify.* steps have max_retries=2 — they're idempotent-ish (sending the
same email twice is annoying but not destructive) and external delivery
APIs flake.

wait.approval is also in this module since it's the user-communication
gating step. It pauses the run and creates a workflow_approvals row;
max_retries=0 because retrying a pause makes no sense."""
from __future__ import annotations

from typing import Any, Optional

from backend.workflows.registry import register_step
from backend.workflows.schemas import (
    NotifyLogConfig,
    NotifyMessageConfig,
    WaitApprovalConfig,
)


@register_step(
    step_type="notify.message",
    category="notify",
    label="Send message",
    description="Send an email, SMS, or push notification",
    icon="send",
    max_retries=2,
    trigger_only=False,
    config_model=NotifyMessageConfig,
    output_schema={
        "type": "object",
        "properties": {
            "channel": {"type": "string"},
            "delivered": {"type": "boolean"},
        },
        "required": ["channel", "delivered"],
    },
)
async def execute_notify_message(*args: Any, **kwargs: Any) -> Optional[dict[str, Any]]:
    raise NotImplementedError("not yet implemented")


@register_step(
    step_type="notify.log",
    category="notify",
    label="Log message",
    description="Append a line to the run log (no external side effect)",
    icon="file-text",
    max_retries=2,
    trigger_only=False,
    config_model=NotifyLogConfig,
    output_schema={
        "type": "object",
        "properties": {"log": {"type": "string"}},
        "required": ["log"],
    },
)
async def execute_notify_log(*args: Any, **kwargs: Any) -> Optional[dict[str, Any]]:
    raise NotImplementedError("not yet implemented")


@register_step(
    step_type="wait.approval",
    category="notify",
    label="Wait for approval",
    description="Pause the run until you approve or reject in the UI",
    icon="hand",
    max_retries=0,
    trigger_only=False,
    config_model=WaitApprovalConfig,
    output_schema={
        "type": "object",
        "properties": {
            "decision": {"type": "string", "enum": ["approved", "rejected"]},
            "decided_at": {"type": "string", "format": "date-time"},
        },
        "required": ["decision"],
    },
)
async def execute_wait_approval(*args: Any, **kwargs: Any) -> Optional[dict[str, Any]]:
    raise NotImplementedError("not yet implemented")
