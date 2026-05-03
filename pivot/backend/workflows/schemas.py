"""Per-step config + output Pydantic models.

Each step type in the registry attaches one of these as its
`config_model`. The registry derives the JSON Schema (draft 2020-12) at
catalog-emit time and validates incoming step configs against it on
every API + engine boundary (see ARCHITECTURE.md §7 invariant 7).

Strict-typed: every field declares an explicit type. We avoid `Any`
unless the field is genuinely opaque (e.g. webhook payload pass-through),
and we comment those cases.
"""
from __future__ import annotations

from typing import Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field


# Refs are resolved by backend/workflows/refs.py before each step runs.
# A "ref-or-X" field accepts either a literal value or a ref string like
# "{{ context.1.buying_power }}". We type these as Union[X, str] in the
# Pydantic model and let the resolver coerce at runtime.
RefOrNumber = Union[float, str]


class _Strict(BaseModel):
    """Base for all step config models.

    `extra='forbid'` so unknown keys are rejected with a clear error
    rather than silently ignored. `populate_by_name=True` is irrelevant
    here but keeps the door open for aliases later."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


# ── Triggers ─────────────────────────────────────────────────────────

class TriggerScheduleConfig(_Strict):
    cron: str = Field(..., description="Cron expression, 5-field")
    timezone: str = Field(
        default="Asia/Kolkata",
        description="IANA timezone, e.g. Asia/Kolkata",
    )


class TriggerPriceConfig(_Strict):
    symbol: str
    operator: Literal[">", "<", "crosses_above", "crosses_below"]
    value: float
    exchange: Literal["NSE", "BSE"] = "NSE"


class TriggerIndicatorConfig(_Strict):
    symbol: str
    indicator: Literal["rsi", "sma", "ema", "macd"]
    period: int = Field(..., ge=1, le=500)
    operator: Literal[">", "<", "crosses_above", "crosses_below"]
    value: float


class TriggerEventConfig(_Strict):
    event_type: Literal["rbi_rate_decision", "company_results", "fii_flow"]
    # `filter` is event-specific structured data — opaque at the catalog
    # level but typed as dict[str, str | int | float | bool | None] to
    # keep mypy --strict happy without resorting to Any.
    filter: dict[str, Union[str, int, float, bool, None]] = Field(
        default_factory=dict,
        description="Event-type-specific filter (e.g. {symbol:'RELIANCE'})",
    )


class TriggerManualConfig(_Strict):
    """Manual trigger has no config; user clicks Run now."""
    pass


class TriggerWebhookConfig(_Strict):
    """Webhook trigger has no config in workflow_steps. The token is
    issued separately and stored in workflow_webhook_tokens."""
    pass


# ── Data fetches ─────────────────────────────────────────────────────

class FetchQuoteConfig(_Strict):
    symbol: str
    exchange: Literal["NSE", "BSE"] = "NSE"


class FetchIndicatorConfig(_Strict):
    symbol: str
    indicator: Literal["rsi", "sma", "ema", "macd"]
    period: int = Field(..., ge=1, le=500)


class FetchFundamentalConfig(_Strict):
    symbol: str
    metric: Literal["pe", "roe", "mcap", "de"]


class FetchPortfolioConfig(_Strict):
    """No config — fetches the authenticated user's portfolio."""
    pass


class FetchNewsConfig(_Strict):
    symbol_or_query: str
    limit: int = Field(default=10, ge=1, le=50)


# ── Conditions ───────────────────────────────────────────────────────

class ConditionNumericConfig(_Strict):
    left: RefOrNumber
    operator: Literal["==", "!=", ">", "<", ">=", "<="]
    right: RefOrNumber


class ConditionMarketStatusConfig(_Strict):
    require: Literal["open", "closed", "pre", "post"]


class ConditionPositionConfig(_Strict):
    symbol: str
    require: Literal["held", "not_held"]


class ConditionTimeWindowConfig(_Strict):
    start_time: str = Field(..., description="HH:MM 24h, e.g. '09:15'")
    end_time: str = Field(..., description="HH:MM 24h, e.g. '15:30'")
    timezone: str = "Asia/Kolkata"


# ── Actions ──────────────────────────────────────────────────────────

class ActionPlaceOrderConfig(_Strict):
    symbol: str
    side: Literal["buy", "sell"]
    quantity: int = Field(..., ge=1)
    order_type: Literal["market", "limit"] = "market"
    limit_price: Optional[float] = None
    requires_approval: bool = False


class ActionCancelOrdersConfig(_Strict):
    symbol_filter: Optional[str] = None
    side_filter: Optional[Literal["buy", "sell"]] = None


class ActionSetStoplossConfig(_Strict):
    symbol: str
    trigger_price: float
    quantity: Optional[int] = Field(default=None, ge=1)


class ActionUpdateWatchlistConfig(_Strict):
    action: Literal["add", "remove"]
    symbol: str


# ── Communication ────────────────────────────────────────────────────

class NotifyMessageConfig(_Strict):
    channel: Literal["email", "sms", "push"]
    template: str
    # vars is template-specific structured data: keys map to template
    # placeholders. Typed loosely to allow primitives + refs.
    vars: dict[str, Union[str, int, float, bool, None]] = Field(
        default_factory=dict,
    )


class NotifyLogConfig(_Strict):
    message: str


class WaitApprovalConfig(_Strict):
    summary: str
    expires_in_minutes: int = Field(default=15, ge=1, le=24 * 60)


# ── Control flow ─────────────────────────────────────────────────────

class WaitDelayConfig(_Strict):
    """Either duration_seconds OR until_time — not both. Validated
    post-hoc by the engine; JSON Schema can't express XOR cleanly."""
    duration_seconds: Optional[int] = Field(default=None, ge=1)
    until_time: Optional[str] = Field(
        default=None,
        description="ISO 8601 timestamp or HH:MM",
    )
    timezone: str = "Asia/Kolkata"


class SkipIfConfig(_Strict):
    """If the inner condition holds, the NEXT step is marked skipped.
    No branching."""
    condition: dict[str, Union[str, float, int, bool, None]] = Field(
        ...,
        description="A numeric/market/position-style condition payload",
    )
