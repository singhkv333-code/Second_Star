"""Control-flow step stubs.

Single-track only — no branching, no loops, no sub-workflows
(ARCHITECTURE.md §5.6 + §13). max_retries=0 for both: retrying a
sleep or a skip-marker has no semantic meaning."""
from __future__ import annotations

from typing import Any, Optional

from backend.workflows.registry import register_step
from backend.workflows.schemas import SkipIfConfig, WaitDelayConfig


@register_step(
    step_type="wait.delay",
    category="control",
    label="Wait",
    description="Pause for a duration or until a specific time",
    icon="timer",
    max_retries=0,
    trigger_only=False,
    config_model=WaitDelayConfig,
    output_schema=None,
)
async def execute_wait_delay(*args: Any, **kwargs: Any) -> Optional[dict[str, Any]]:
    raise NotImplementedError("not yet implemented")


@register_step(
    # Note: renamed from bare "skip_if" to "control.skip_if" per the
    # reviewer's contract audit on Day 1 — every step type now follows
    # the category.subtype dotted pattern. See STATUS.md Day 1, fix 1.
    step_type="control.skip_if",
    category="control",
    label="Skip if",
    description="Skip the next step when a condition holds",
    icon="skip-forward",
    max_retries=0,
    trigger_only=False,
    config_model=SkipIfConfig,
    output_schema={
        "type": "object",
        "properties": {"skipped_next": {"type": "boolean"}},
        "required": ["skipped_next"],
    },
)
async def execute_control_skip_if(*args: Any, **kwargs: Any) -> Optional[dict[str, Any]]:
    raise NotImplementedError("not yet implemented")
