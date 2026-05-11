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

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, field_validator, model_validator

from backend.services.backtest_indicators import (
    get_spec as _indicator_spec,
    supported_indicators as _supported_indicators,
)


def _validate_indicator_key(v: Any) -> str:
    """Validator for the ``indicator`` field on TriggerIndicatorConfig +
    FetchIndicatorConfig. Accepts any key registered in the
    backtest_indicators registry (single source of truth across the
    workflow backtester, the live watcher, and the fetch step). Raising
    here gives the same surface as the old ``Literal[...]`` cap, but
    new indicators land by editing the registry — no schema patch."""
    if not isinstance(v, str):
        raise ValueError("indicator must be a string")
    key = v.strip().lower()
    if not key:
        raise ValueError("indicator is required")
    if _indicator_spec(key) is None:
        raise ValueError(
            f"unsupported indicator {v!r}; supported: "
            + ", ".join(_supported_indicators())
        )
    return key


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

    Originally `extra='forbid'`, but we observed the planner LLM dropping
    a draft over a single unrequested field on a single step (e.g. a
    spurious `notify.message` step missing its `channel`, or a
    `requires_approval` flag tacked onto a step type that doesn't carry
    one). Rejecting the whole draft for one harmless extra field
    produced a 21-second catalog-dump fallback for what was otherwise
    a usable workflow. `extra='ignore'` keeps validation strict on
    REQUIRED fields and types, but silently drops unknown keys so the
    draft survives. The trade-off: genuine model mistakes on field
    names won't surface as errors anymore — they'll be quietly dropped.
    Acceptable for v1; revisit if we see it masking real bugs.

    `populate_by_name=True` keeps the door open for aliases later."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)


# ── Triggers ─────────────────────────────────────────────────────────

class TriggerScheduleConfig(_Strict):
    cron: str = Field(..., description="Cron expression, 5-field")
    timezone: str = Field(
        default="Asia/Kolkata",
        description="IANA timezone, e.g. Asia/Kolkata",
    )


class TriggerMarketRelativeTimeConfig(_Strict):
    """Schedule trigger anchored to NSE market hours rather than a
    fixed wall-clock time.

    User asks like *"5 minutes before close"* or *"at the open"* would
    otherwise need the model to remember that NSE opens at 09:15 IST and
    closes at 15:30 IST — fragile, and breaks on early-close days
    (Diwali muhurat, special sessions). This trigger lets the model say
    `{anchor: 'close', offset_minutes: -5}` and the scheduler resolves
    to the correct concrete cron at job-registration time.

    `days` defaults to NSE trading weekdays. `offset_minutes` is signed:
    negative = before, positive = after. Resolution happens once at job
    arming; the scheduler does NOT re-resolve daily, so if NSE shifts
    its session times mid-week the workflow holds the old time until
    the next save.
    """
    anchor: Literal["open", "close", "pre_open", "post_close"] = Field(
        ...,
        description=(
            "Which market boundary to anchor to. open=09:15 IST, "
            "close=15:30 IST, pre_open=09:00 IST, post_close=16:00 IST."
        ),
    )
    offset_minutes: int = Field(
        default=0, ge=-90, le=90,
        description=(
            "Signed minutes from the anchor. -5 = 5min before, "
            "+30 = 30min after. Bounds keep us in/around market hours."
        ),
    )
    days: list[Literal[
        "monday", "tuesday", "wednesday", "thursday", "friday",
        "weekday",
    ]] = Field(
        default_factory=lambda: ["weekday"],
        description=(
            "Days the trigger fires on. 'weekday' is shorthand for "
            "Mon–Fri. Weekends and known holidays are always skipped."
        ),
    )
    timezone: str = Field(
        default="Asia/Kolkata",
        description="IANA timezone; almost always Asia/Kolkata.",
    )


class TriggerPriceConfig(_Strict):
    symbol: str
    operator: Literal[">", "<", "crosses_above", "crosses_below"]
    value: float
    exchange: Literal["NSE", "BSE"] = "NSE"


class TriggerIndicatorConfig(_Strict):
    symbol: str
    # Validated against the backtest_indicators registry (rsi/sma/ema/macd/
    # adx/supertrend/bollinger/stoch/cci/mfi/williams_r/atr/keltner/
    # donchian/aroon/psar/wma/roc/trix/stoch_rsi/obv/vwap …). Adding a
    # new indicator there makes it instantly authorable here.
    indicator: Annotated[str, BeforeValidator(_validate_indicator_key)]
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
    # Same registry-validated set as TriggerIndicatorConfig — keeps live
    # fetch and backtest in lockstep.
    indicator: Annotated[str, BeforeValidator(_validate_indicator_key)]
    period: int = Field(..., ge=1, le=500)


class FetchFundamentalConfig(_Strict):
    symbol: str
    metric: Literal["pe", "roe", "mcap", "de"]


class FetchPortfolioConfig(_Strict):
    """No config — fetches the authenticated user's portfolio."""
    pass


class FetchIntradayPnLConfig(_Strict):
    """Compute realised + unrealised P&L from the user's holdings.

    Drives risk-gate prompts like *"every weekday at 15:25, if my
    intraday P&L < -2%, exit all MIS positions"*. The output is a
    structured dict downstream `condition.numeric` can compare against:

        {
          "total_pct": -1.23,           # P&L as % of cost basis
          "total_inr": -2456.0,         # absolute P&L in INR
          "unrealised_inr": -2456.0,    # mark-to-market on open positions
          "realised_inr": 0.0,          # closed-position P&L (today)
          "cost_basis_inr": 200000.0,   # what you paid for the open lot
          "by_symbol": {                # per-symbol breakdown
            "RELIANCE": {"qty": 10, "avg": 2500.0, "ltp": 2475.0,
                         "pnl_inr": -250.0, "pnl_pct": -1.0},
            ...
          }
        }

    `scope` selects which positions count. Default 'all' covers both
    delivery (CNC) and intraday (MIS). 'intraday' restricts to MIS only
    so a 'square off intraday' guard doesn't trip on long-term holders.
    """
    scope: Literal["all", "intraday", "delivery"] = Field(
        default="all",
        description=(
            "Which positions to include. 'intraday' = MIS only, "
            "'delivery' = CNC only, 'all' = both."
        ),
    )


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


class FetchRollingHighConfig(_Strict):
    """Highest HIGH over the last ``lookback`` daily bars (rolling window),
    optionally multiplied by ``multiplier``.

    Use ``multiplier`` to precompute "X% below the recent high" as one
    fetch — condition.numeric can't do arithmetic, so emit the offset
    directly:

        fetch.rolling_high(symbol=NVDA, lookback=20, multiplier=0.90)
            → context.K.value = 20-day high × 0.90
        condition.numeric(close ≤ context.K.value)   # "10% off the 20-day high"
    """
    symbol: str
    lookback: int = Field(
        default=20, ge=2, le=500,
        description=(
            "Number of trading days the rolling window spans. 20 = "
            "one trading month, 252 = one year, 50 = ~10 weeks."
        ),
    )
    multiplier: float = Field(
        default=1.0, ge=0.1, le=2.0,
        description=(
            "Multiplier applied to the rolling-high value. 0.9 = '10% "
            "below the recent high' (drawdown trigger). 1.05 = '5% "
            "above the recent high' (breakout trigger). Default 1.0 "
            "returns the high unchanged."
        ),
    )
    exchange: Literal["NSE", "BSE"] = "NSE"


class FetchSpreadZScoreConfig(_Strict):
    """Z-score of the (close_a − close_b) spread over a rolling window.

    Drives pairs-trade entries: open the pair when |z| > threshold,
    close when z reverts toward 0. Example:

        fetch.spread_z_score(symbol_a=ITC, symbol_b=HINDUNILVR, lookback=30)
            → context.K.value = z-score of (ITC.close − HUL.close)
                                 over the last 30 trading days
        condition.numeric(left=context.K.value, operator='>', right=2)
            → opens "short ITC, long HUL" when ITC is rich vs HUL

    The output sign tells the user which leg is rich: positive z means
    symbol_a is unusually expensive relative to symbol_b.
    """
    symbol_a: str
    symbol_b: str
    lookback: int = Field(
        default=30, ge=5, le=252,
        description=(
            "Rolling window for the mean / std. 30 = ~1.5 trading "
            "months, 60 = ~3 months. Shorter windows are noisier; "
            "longer windows are slower to react to regime changes."
        ),
    )
    exchange: Literal["NSE", "BSE"] = "NSE"


class FetchRollingLowConfig(_Strict):
    """Lowest LOW over the last ``lookback`` daily bars, optionally
    multiplied. Mirror of FetchRollingHighConfig."""
    symbol: str
    lookback: int = Field(default=20, ge=2, le=500)
    multiplier: float = Field(
        default=1.0, ge=0.1, le=5.0,
        description=(
            "Multiplier applied to the rolling-low. 1.10 = '10% above "
            "the recent low' (mean-reversion long entry)."
        ),
    )
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


class FetchTopMoversConfig(_Strict):
    """Top gainers / losers in a universe (currently NIFTY 50).

    Drives prompts like *"buy the top gainer of the day at close"*.
    Output is a list of {symbol, ltp, change_pct, seed?} rows that
    downstream `action.place_order` can consume via Mustache ref to
    enter the chosen symbol.
    """
    direction: Literal["gainers", "losers"] = Field(
        default="gainers",
        description="`gainers` for the largest positive % movers today; "
                    "`losers` for the largest negative.",
    )
    universe: Literal["nifty50"] = Field(
        default="nifty50",
        description="Stock universe to rank. Only `nifty50` is wired in v1.",
    )
    limit: int = Field(default=1, ge=1, le=20)


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
    # buy   = open or extend a long position (positive qty)
    # sell  = close or trim a long position (clamped to held qty)
    # short = open or extend a short position (negative qty); backtest-
    #         only today, live executor refuses with a clear error.
    # cover = buy-to-close an open short.
    side: Literal["buy", "sell", "short", "cover"]
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
    # CNC = delivery / overnight (default — most v1 workflows are
    # delivery). MIS = intraday, required when the workflow pairs the
    # entry with action.squareoff_all_intraday. Live executor and the
    # backtester both honour this field.
    product: Literal["CNC", "MIS"] = "CNC"
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
    # When True the stop ratchets up bar-by-bar, tracking a high-water
    # mark of the bar's HIGH and re-pricing the trigger as
    # ``hwm * (1 - trigger_offset_pct / 100)``. Requires
    # ``trigger_offset_pct`` (a trailing stop has no absolute trigger
    # price by construction). Pure-backtest support today; live
    # executor places the initial GTT and ignores the flag.
    trailing: bool = Field(
        default=False,
        description=(
            "Trailing stop. When True the trigger price moves UP with "
            "the underlying — set to (high-water-mark × (1 - "
            "trigger_offset_pct/100)) on every new bar high. Requires "
            "trigger_offset_pct. Backtest-only today."
        ),
    )

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


class ActionSetTakeprofitConfig(_Strict):
    """Take-profit sell — fires when HIGH ≥ trigger_price.

    Mirror of action.set_stoploss on the upside. ``trigger_price``
    OR ``trigger_offset_pct`` (above the entry fill) — exactly one.
    Backtest fills at the trigger price with one-side friction; live
    executor (Day 4+) places a GTT sell.
    """
    symbol: str
    trigger_price: Optional[FloatOrRef] = None
    trigger_offset_pct: Optional[float] = Field(
        default=None,
        gt=0,
        le=200,
        description=(
            "Take-profit trigger as a percentage ABOVE the entry price "
            "from the preceding action.place_order. 30 = trigger 30% "
            "above the buy fill."
        ),
    )
    quantity: Optional[IntOrRef] = None

    @model_validator(mode="after")
    def _exactly_one_trigger(self) -> "ActionSetTakeprofitConfig":
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


class ActionAllocateBasketLeg(_Strict):
    """One leg of a weighted basket. ``side`` per-leg lets a single
    basket mix long and short positions (the synthetic-security
    pattern: long oil + gold + defense, short Europe)."""
    symbol: str
    weight: float = Field(
        ..., gt=0, le=1.0,
        description=(
            "Fraction of total_inr allocated to this leg (0–1). The "
            "executor accepts non-normalised weights and re-scales "
            "them so they sum to 1, but caller is encouraged to "
            "supply normalised values for clarity."
        ),
    )
    side: Literal["long", "short"] = Field(
        default="long",
        description=(
            "Direction for this leg. 'long' = buy at the trigger bar's "
            "open. 'short' = sell-to-open (backtest-only)."
        ),
    )
    exchange: Literal["NSE", "BSE"] = "NSE"


class ActionAllocateBasketConfig(_Strict):
    """Open a weighted basket of long and/or short positions in one
    step.

    Drives the synthetic-security pattern: ``long oil + gold + defense
    @ 30/20/30, short europe @ 20``. Each leg gets ``total_inr * weight``
    notional, converted to integer share count at the leg's bar OPEN.
    Backtest fills every leg on the trigger bar; live executor places
    one order per leg under per-leg client_request_ids.

    Pair with action.squareoff_all (or a separate exit trigger that
    sells/covers each leg) for the close.
    """
    legs: list[ActionAllocateBasketLeg] = Field(
        ..., min_length=1, max_length=20,
        description="Per-leg symbol + weight + side. 1–20 legs.",
    )
    total_inr: FloatOrRef = Field(
        ...,
        description=(
            "Total INR notional to deploy across the basket. Refs "
            "accepted (e.g. a previously fetched buying_power)."
        ),
    )
    order_type: Literal["market", "limit"] = "market"
    requires_approval: bool = False


class ActionSquareoffAllConfig(_Strict):
    """Close every open position — long AND short — at the trigger bar's
    close. The companion exit step for action.allocate_basket; cleaner
    than enumerating squareoff_symbol per leg."""
    pass


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


# ── Squareoff actions ─────────────────────────────────────────────────


class ActionSquareoffAllIntradayConfig(_Strict):
    """Exit all open intraday (MIS) positions with market sells.

    Pairs with the EOD risk-gate pattern *"5 minutes before close, if
    intraday P&L < -2%, exit all MIS"*. The executor walks live
    positions, filters to product=MIS with non-zero net qty, and places
    one market sell per leg under per-leg idempotent client_request_ids.

    No config — scope is fixed (intraday only). For per-symbol exits
    use ``action.squareoff_symbol``.
    """
    pass


class ActionSquareoffSymbolConfig(_Strict):
    """Exit a single symbol's open lot at market.

    Used for per-symbol risk gates and basket trims. ``product`` selects
    intraday vs delivery; defaults to MIS since "exit my X" is most
    often an intraday cut.
    """
    symbol: str
    product: Literal["MIS", "CNC"] = "MIS"


# ── Communication ────────────────────────────────────────────────────

class NotifyMessageConfig(_Strict):
    # Defaults are deliberate: the planner LLM frequently appends an
    # unrequested notify step at the tail of a workflow without
    # bothering to fill `channel` or `template`. Rejecting the whole
    # draft for that turned a usable workflow into a 21s catalog-dump
    # fallback. With defaults, the step still validates and the user
    # sees a generic in-app notification — they can rename or remove
    # it from the editor.
    #
    # WHY this is `Literal["push"]` and not the broader email/sms/push:
    # Pivot v1's email and SMS surfaces are NOT wired (notify.py just
    # logs to stdout for non-push channels). The earlier permissive
    # enum let the model pass channel='email' through, the workflow
    # validated, and the user was told "I'll send you an email" — but
    # the agent silently logged instead of delivering. Restricting to
    # push at the schema layer means an email/sms emit fails
    # validation, which routes through the existing email-aware
    # canned reject in chat_service that names the gap and offers
    # in-app instead. Honest UX over silent downgrade.
    channel: Literal["push"] = Field(
        default="push",
        description=(
            "In-app push only. Pivot v1 does NOT send email, SMS, "
            "WhatsApp, or Slack — those channels aren't wired."
        ),
    )
    template: str = Field(
        default="Workflow {{ workflow.name }} fired.",
        description="Defaults to a generic auto-generated message.",
    )
    # vars is template-specific structured data: keys map to template
    # placeholders. Typed loosely to allow primitives + refs.
    vars: dict[str, Union[str, int, float, bool, None]] = Field(
        default_factory=dict,
    )


class NotifyLogConfig(_Strict):
    message: str = Field(
        default="Workflow step fired.",
        description="Default to a non-empty placeholder so a missing "
                    "message field doesn't reject the draft.",
    )


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
