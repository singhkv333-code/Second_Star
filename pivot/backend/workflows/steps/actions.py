"""Action step stubs.

Actions mutate state and MUST be idempotent (ARCHITECTURE.md §7
invariant 1). Each real executor will generate a deterministic
client_request_id = sha1(f"{run_id}:{step_index}:{attempts}") so that
broker/notification systems can reject duplicates.

max_retries=1: actions are idempotent so we tolerate one transient
retry, but no more — the broker side-effect is real and we don't want
to spam orders if the failure is structural."""
from __future__ import annotations

from typing import Any, Optional

from backend.workflows.registry import register_step
from backend.workflows.schemas import (
    ActionCancelOrdersConfig,
    ActionPlaceOrderConfig,
    ActionSetStoplossConfig,
    ActionUpdateWatchlistConfig,
)


@register_step(
    step_type="action.place_order",
    category="action",
    label="Place order",
    description="Place a market or limit order via Kite",
    icon="shopping-cart",
    max_retries=1,
    trigger_only=False,
    config_model=ActionPlaceOrderConfig,
    output_schema={
        "type": "object",
        "properties": {
            "order_id": {"type": "string"},
            "status": {"type": "string"},
            "client_request_id": {"type": "string"},
        },
        "required": ["order_id", "client_request_id"],
    },
)
async def execute_action_place_order(*args: Any, **kwargs: Any) -> Optional[dict[str, Any]]:
    raise NotImplementedError("not yet implemented")


@register_step(
    step_type="action.cancel_orders",
    category="action",
    label="Cancel orders",
    description="Cancel matching pending orders",
    icon="x-circle",
    max_retries=1,
    trigger_only=False,
    config_model=ActionCancelOrdersConfig,
    output_schema={
        "type": "object",
        "properties": {
            "cancelled_count": {"type": "integer"},
            "order_ids": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["cancelled_count"],
    },
)
async def execute_action_cancel_orders(*args: Any, **kwargs: Any) -> Optional[dict[str, Any]]:
    raise NotImplementedError("not yet implemented")


@register_step(
    step_type="action.set_stoploss",
    category="action",
    label="Set stop-loss",
    description="Place a stop-loss order on a holding",
    icon="shield",
    max_retries=1,
    trigger_only=False,
    config_model=ActionSetStoplossConfig,
    output_schema={
        "type": "object",
        "properties": {
            "trigger_id": {"type": "string"},
            "client_request_id": {"type": "string"},
        },
        "required": ["trigger_id"],
    },
)
async def execute_action_set_stoploss(*args: Any, **kwargs: Any) -> Optional[dict[str, Any]]:
    raise NotImplementedError("not yet implemented")


@register_step(
    step_type="action.update_watchlist",
    category="action",
    label="Update watchlist",
    description="Add or remove a symbol from your watchlist",
    icon="list-plus",
    max_retries=1,
    trigger_only=False,
    config_model=ActionUpdateWatchlistConfig,
    output_schema={
        "type": "object",
        "properties": {
            "action": {"type": "string"},
            "symbol": {"type": "string"},
        },
        "required": ["action", "symbol"],
    },
)
async def execute_action_update_watchlist(*args: Any, **kwargs: Any) -> Optional[dict[str, Any]]:
    raise NotImplementedError("not yet implemented")
