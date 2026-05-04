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

from typing import Annotated, Any, Literal, Optional, Union

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator


# Refs are resolved by backend/workflows/refs.py before each step runs.
# A "ref-or-X" field accepts either a literal value or a ref string like
# "{{ context.1.buying_power }}". We type these as Union[X, str] in the
# Pydantic model and let the resolver coerce at runtime.
RefOrNumber = Union[float, str]


def _is_mustache_ref(s: str) -> bool:
    """A string is a Mustache template reference if it has at least one
    matching `{{ ... }}` pair. Used by the int/float coercers below so
    the registry accepts a draft like
    ``{"quantity": "{{ context.5.holdings.NIFTYBEES.quantity }}"}``
    even though `quantity` is logically an integer.
    The reference is resolved at execution time by `refs.resolve_refs`."""
    return "{{" in s and "}}" in s.split("{{", 1)[1]


def _coerce_int_or_ref(v: Any) -> Any:
    """Accepts an int (passes through) OR a numeric string (coerced to
    int) OR a Mustache reference string (kept verbatim for runtime
    resolution). Anything else raises ValueError so Pydantic surfaces
    a clean validation error."""
    if isinstance(v, bool):
        # Pydantic treats bool as int subclass; reject.
        raise ValueError("expected integer or {{ ... }} reference")
    if isinstance(v, int):
        return v
    if isinstance(v, str):
        s = v.strip()
        if _is_mustache_ref(s):
            return s
        try:
            return int(s)
        except ValueError:
            pass
    raise ValueError("expected integer or {{ ... }} reference")


def _coerce_float_or_ref(v: Any) -> Any:
    """Same shape as `_coerce_int_or_ref` but for float-valued fields
    (price, threshold). Mustache refs pass through untouched."""
    if isinstance(v, bool):
        raise ValueError("expected number or {{ ... }} reference")
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        s = v.strip()
        if _is_mustache_ref(s):
            return s
        try:
            return float(s)
        except ValueError:
            pass
    raise ValueError("expected number or {{ ... }} reference")


# A field whose value is an int at runtime, but at draft time may be
# a Mustache template string the engine resolves later. The model's
# common pattern: `quantity = "{{ context.5.holdings.NIFTYBEES.quantity }}"`
# for "sell entire holding" branches.
IntOrRef = Annotated[Union[int, str], BeforeValidator(_coerce_int_or_ref)]
FloatOrRef = Annotated[Union[float, str], BeforeValidator(_coerce_float_or_ref)]


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


# ── Day-anchored fetches ─────────────────────────────────────────────
#
# Workflows v1 originally had no way to reference today's open or the
# previous session's close — every trigger.price needed a literal
# absolute level. Prompts like "if RELIANCE dips 5% from Monday's open"
# had no expressible shape and looped to the agent loop's circuit
# breaker. These three fetches close that gap:
#
#   fetch.day_open       → today's open price
#   fetch.prior_close    → last completed session's close
#   fetch.relative_threshold → "X% above/below day_open / prior_close"
#                              in one step so condition.numeric can
#                              compare current price to a precomputed
#                              level WITHOUT needing arithmetic in refs.

class FetchDayOpenConfig(_Strict):
    symbol: str
    exchange: Literal["NSE", "BSE"] = "NSE"


class FetchPriorCloseConfig(_Strict):
    symbol: str
    exchange: Literal["NSE", "BSE"] = "NSE"
    sessions_back: int = Field(
        default=1, ge=1, le=10,
        description=(
            "How many trading sessions to look back. 1 = previous "
            "trading day's close. Useful for 'last week's close' = 5."
        ),
    )


class FetchScreenerConfig(_Strict):
    """Filter + rank the sector universe.

    Drives portfolio-construction prompts like *"top 10 steel sector
    stocks by market cap"*. Output is a list of symbols + display
    metadata that downstream `action.allocate_notional` can consume
    via Mustache ref to fan out a basket buy.
    """
    sector: Optional[str] = Field(
        default=None,
        description=(
            "Canonical sector name or alias (steel, metals, banking, "
            "psu_bank, private_bank, it, auto, pharma, fmcg, energy, "
            "cement, defence, telecom). When None, all symbols match."
        ),
    )
    mcap_min_cr: Optional[int] = Field(default=None, ge=0)
    mcap_max_cr: Optional[int] = Field(default=None, ge=0)
    sort_by: Literal["mcap", "symbol"] = Field(default="mcap")
    descending: bool = Field(default=True)
    limit: int = Field(default=10, ge=1, le=50)


class FetchRelativeThresholdConfig(_Strict):
    """Compute an absolute price level relative to today's open or a
    prior close, plus a percentage offset.

    Example: "5% below today's open" →
      { symbol: 'RELIANCE', reference: 'day_open', offset_pct: -5 }
    The output `value` is an absolute price the next step's
    condition.numeric can compare against current price directly."""
    symbol: str
    reference: Literal["day_open", "prior_close", "prior_high", "prior_low"]
    offset_pct: float = Field(
        default=0.0, ge=-50.0, le=50.0,
        description=(
            "Percentage offset from the reference. Negative for "
            "'below' (e.g. -5 means 5% below). Positive for 'above'."
        ),
    )
    exchange: Literal["NSE", "BSE"] = "NSE"


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
    # Accepts an integer share count, a Mustache reference (e.g.
    # `{{ context.5.holdings.NIFTYBEES.quantity }}` for "sell entire
    # holding"), OR — when notional_inr is provided instead — left
    # absent and computed at run time as
    # `floor(notional_inr / fill_price)`. Exactly one of quantity /
    # notional_inr must be supplied (validator below).
    quantity: Optional[IntOrRef] = None
    # Notional alternative to quantity. Set this when the user
    # expresses size in INR ("buy ₹5,000 of RELIANCE", "put ₹500
    # daily into NIFTYBEES"). Executor fetches the live price at fire
    # time and converts to integer shares. Mustache refs are accepted
    # for cross-step composition (e.g.
    # `{{ context.0.total_inr }} / 10`-style would NOT work since
    # arithmetic isn't supported, but referencing a precomputed
    # number is fine).
    notional_inr: Optional[FloatOrRef] = None
    order_type: Literal["market", "limit"] = "market"
    limit_price: Optional[FloatOrRef] = None
    requires_approval: bool = False

    @model_validator(mode="after")
    def _exactly_one_size(self) -> "ActionPlaceOrderConfig":
        has_qty = self.quantity is not None
        has_notional = self.notional_inr is not None
        if has_qty and has_notional:
            raise ValueError(
                "specify either quantity or notional_inr, not both"
            )
        if not has_qty and not has_notional:
            raise ValueError("must specify quantity or notional_inr")
        return self


class ActionCancelOrdersConfig(_Strict):
    symbol_filter: Optional[str] = None
    side_filter: Optional[Literal["buy", "sell"]] = None


class ActionSetStoplossConfig(_Strict):
    symbol: str
    # Either an absolute trigger_price OR a percentage offset below the
    # entry fill (resolved at execution time from the preceding
    # action.place_order). Exactly one must be supplied; the engine
    # rejects drafts with both/neither. trigger_offset_pct exists because
    # users describe stop-losses as "2% stop loss", which has no
    # absolute price at draft time — without this field the propose_workflow
    # path looped to circuit-breaker on every percentage-stop request.
    trigger_price: Optional[FloatOrRef] = None
    trigger_offset_pct: Optional[float] = Field(
        default=None,
        gt=0,
        le=50,
        description=(
            "Stop-loss trigger as a percentage below the entry price "
            "from the preceding action.place_order step. e.g. 2 means "
            "trigger 2% below the buy fill. Use this when the user "
            "expressed the SL in % terms; use trigger_price when they "
            "gave an absolute number."
        ),
    )
    quantity: Optional[IntOrRef] = None

    @model_validator(mode="after")
    def _exactly_one_trigger(self) -> "ActionSetStoplossConfig":
        has_price = self.trigger_price is not None
        has_pct = self.trigger_offset_pct is not None
        if has_price and has_pct:
            raise ValueError(
                "specify either trigger_price or trigger_offset_pct, not both"
            )
        if not has_price and not has_pct:
            raise ValueError(
                "must specify trigger_price or trigger_offset_pct"
            )
        return self


class ActionUpdateWatchlistConfig(_Strict):
    action: Literal["add", "remove"]
    symbol: str


class ActionAllocateNotionalConfig(_Strict):
    """Spread a rupee budget across N symbols and place each as one
    order under a single logical batch.

    The user mental model is *"invest ₹1L equally across these 10
    stocks"* — one tool call should cover it. Without this step the
    workflow would need 10 separate `action.place_order` steps with
    each `notional_inr` set to the per-symbol slice, which the model
    can't easily compute (no arithmetic in refs) and is verbose to
    review on the draft card.
    """
    # Ref string pointing at a list of symbols (typically the output
    # of `fetch.screener`), or a literal list of symbols.
    symbols: Union[str, list[str]] = Field(
        ...,
        description=(
            "Either a list of tickers, or a Mustache ref to a step "
            "output that holds one (e.g. "
            "`{{ context.4.symbols }}`). The executor resolves the "
            "ref before allocating."
        ),
    )
    side: Literal["buy", "sell"]
    total_inr: FloatOrRef = Field(
        ...,
        description=(
            "Total INR budget to deploy across the symbols list. "
            "Refs accepted (e.g. a previously fetched buying_power)."
        ),
    )
    strategy: Literal["equal", "mcap_weighted"] = Field(
        default="equal",
        description=(
            "How to split total_inr across symbols. 'equal' divides "
            "evenly. 'mcap_weighted' requires the symbols list to "
            "carry mcap data (i.e. came from fetch.screener); falls "
            "back to equal if mcap is missing."
        ),
    )
    order_type: Literal["market", "limit"] = "market"
    requires_approval: bool = False


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
