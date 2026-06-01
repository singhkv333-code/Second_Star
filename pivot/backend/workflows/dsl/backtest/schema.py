"""Pydantic models for the backtest request + result.

Same ``_Strict`` base shape as the DSL schemas — ``extra='ignore'``
so a future grammar/result extension doesn't break old persisted
runs.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class _Strict(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


# ── Exit policy ─────────────────────────────────────────────────────
#
# Three shapes, discriminated on ``kind``:
#
#   stop_loss_pct  exit when the bar's LOW drops `value` percent below
#                  the entry price (value in 0..1, e.g. 0.03 = 3% stop).
#                  Fills at the stop price itself, not the next open —
#                  the intra-bar bar-low semantic users expect from
#                  retail SL orders.
#
#   n_day_hold     exit after `bars` bars regardless of price.
#
#   tree           exit when the supplied DSL tree evaluates TRUE on the
#                  current bar's close. Same grammar as the entry tree
#                  plus the ``position`` leaf. Fills at the configured
#                  ``exit_at`` (default 'next_open').
#
# The first two shapes are lowered to the tree shape inside the engine
# so there's exactly one code path that runs exits; the request body
# can still carry the friendlier declarative form for backwards-compat.


class ExitPolicyDeclarative(_Strict):
    """The legacy shape — kept verbatim so already-persisted requests
    keep deserialising. Internally lowered to ``ExitPolicyTree`` by
    ``lower_exit_policy`` before the engine sees it."""
    kind: Literal["stop_loss_pct", "n_day_hold"]
    value: Optional[float] = Field(
        default=None, ge=0.0, le=1.0,
        description="Stop-loss percentage when kind='stop_loss_pct'.",
    )
    bars: Optional[int] = Field(
        default=None, ge=1, le=10000,
        description="Number of bars to hold when kind='n_day_hold'.",
    )

    @model_validator(mode="after")
    def _check_kind_fields(self) -> "ExitPolicyDeclarative":
        if self.kind == "stop_loss_pct" and self.value is None:
            raise ValueError("stop_loss_pct requires 'value' (0..1)")
        if self.kind == "n_day_hold" and self.bars is None:
            raise ValueError("n_day_hold requires 'bars' (>= 1)")
        return self


class ExitPolicyTree(_Strict):
    """Full tree-based exit. ``tree`` is validated with
    ``allow_position=True`` so it may reference the ``position`` leaf
    (e.g. ``position.unrealised_pct < -0.05``)."""
    kind: Literal["tree"] = "tree"
    tree: dict = Field(
        ...,
        description=(
            "DSL tree that evaluates TRUE when the open position "
            "should close. Same shape as the entry tree plus the "
            "'position' leaf."
        ),
    )
    exit_at: Literal["next_open", "current_close", "stop_price"] = Field(
        default="next_open",
        description=(
            "Fill price when the tree fires. 'next_open' matches "
            "the entry-tree fill semantic. 'stop_price' is only "
            "meaningful for lowered stop-loss trees and uses the "
            "embedded stop level."
        ),
    )
    # When lowered from a declarative stop_loss_pct policy, the
    # engine stores the original threshold so it can fill at exactly
    # that price (the realistic Indian-retail SL behaviour). User-
    # authored trees leave this None.
    stop_price_pct: Optional[float] = Field(default=None, ge=0.0, le=1.0)


ExitPolicy = Annotated[
    Union[ExitPolicyDeclarative, ExitPolicyTree],
    Field(discriminator="kind"),
]


def lower_exit_policy(policy) -> "ExitPolicyTree":
    """Compile a declarative policy into the canonical tree form so
    the engine has exactly one code path. ``policy`` may already be a
    tree — in which case it's returned unchanged.

    Declarative lowering specs:

      stop_loss_pct(v)  →  position.unrealised_pct (basis=low) <= -v
                           exit_at='stop_price', stop_price_pct=v
      n_day_hold(n)     →  position.bars_held >= n
                           exit_at='next_open'
    """
    if isinstance(policy, ExitPolicyTree):
        return policy
    if not isinstance(policy, ExitPolicyDeclarative):
        raise TypeError(f"unknown exit policy: {type(policy).__name__}")
    if policy.kind == "stop_loss_pct":
        return ExitPolicyTree(
            kind="tree",
            tree={
                "type": "comparison", "op": "<=",
                "left": {
                    "type": "position",
                    "field": "unrealised_pct",
                    "basis": "low",
                },
                "right": {"type": "constant", "value": -float(policy.value)},
            },
            exit_at="stop_price",
            stop_price_pct=float(policy.value),
        )
    if policy.kind == "n_day_hold":
        return ExitPolicyTree(
            kind="tree",
            tree={
                "type": "comparison", "op": ">=",
                "left": {"type": "position", "field": "bars_held"},
                "right": {"type": "constant", "value": float(policy.bars)},
            },
            exit_at="next_open",
        )
    raise ValueError(f"unsupported declarative kind: {policy.kind}")


# ── Request ─────────────────────────────────────────────────────────


class BacktestRequest(_Strict):
    """Inbound payload for POST /api/backtest/dsl/run."""

    tree: dict = Field(
        ...,
        description=(
            "The DSL entry-condition tree. Same shape that "
            "trigger.compound's config.entry accepts."
        ),
    )
    primary_symbol: str = Field(
        ..., min_length=1, max_length=32,
        description=(
            "Symbol entries / exits trade on. The tree may reference "
            "other symbols (e.g. NIFTY as a filter), but trades fire on "
            "this one."
        ),
    )
    exchange: str = Field(default="NSE", max_length=8)
    start_date: date
    end_date: date
    starting_capital: float = Field(
        default=100_000.0, gt=0.0, le=1_000_000_000.0,
        description="Cash on hand at backtest start (INR).",
    )
    quantity: int = Field(
        default=1, ge=1, le=100000,
        description="Shares per entry trade.",
    )
    exit_policy: ExitPolicy = Field(
        default_factory=lambda: ExitPolicyDeclarative(kind="n_day_hold", bars=10),
        description=(
            "How to close a position. Defaults to 10-bar hold when "
            "omitted. Three shapes: stop_loss_pct, n_day_hold, or a "
            "full tree."
        ),
    )
    save: bool = Field(
        default=True,
        description=(
            "Persist the result to dsl_backtest_runs so it can be "
            "fetched later via GET /api/backtest/dsl/runs/{id}."
        ),
    )

    @field_validator("primary_symbol", "exchange")
    @classmethod
    def _strip_upper(cls, v: str) -> str:
        return v.strip().upper()

    @model_validator(mode="after")
    def _check_date_range(self) -> "BacktestRequest":
        if self.end_date <= self.start_date:
            raise ValueError("end_date must be strictly after start_date")
        return self


# ── Result ──────────────────────────────────────────────────────────


class TradeRow(_Strict):
    """One closed position. Aligned with backend.backtester.portfolio.Trade
    fields where possible so reporting code can be reused."""
    trade_id: int
    symbol: str
    entry_date: date
    entry_price: float
    exit_date: Optional[date]
    exit_price: Optional[float]
    quantity: int
    gross_pnl: float
    costs: float
    net_pnl: float
    return_pct: float = Field(
        ...,
        description="Trade return as a fraction (0.03 = 3%).",
    )
    exit_reason: Literal[
        "stop_loss", "n_day_hold", "force_close", "exit_tree",
    ]


class EquityPoint(_Strict):
    date: date
    equity: float


class ForwardStats(_Strict):
    """Bailey/Lopez de Prado per-period rigor battery on the backtest equity
    curve — the SAME lens the live forward-test scorecards apply to paper NAV.
    Computed by ``services.forward_stats.forward_stats_block``.

      * ``psr`` — confidence the Sharpe is genuinely > 0 (corrects for sample
        length, skew, fat tails).
      * ``min_trl`` — observations needed to prove that at 95%; compare against
        ``n_obs`` (MinTRL > n_obs ⇒ not yet statistically provable).
      * ``deflated_sharpe`` — PSR deflated for ``num_trials`` selection bias
        (1 ⇒ no deflation ⇒ DSR == PSR(0))."""
    observed_sharpe: Optional[float] = None
    skew: Optional[float] = None
    kurtosis: Optional[float] = None
    n_obs: int = 0
    num_trials: int = 1
    psr: Optional[float] = None
    min_trl: Optional[float] = None
    deflated_sharpe: Optional[float] = None


class MonteCarlo(_Strict):
    """Circular-block-bootstrap distribution of max-drawdown + terminal wealth
    from the realised return path. Computed by
    ``services.backtest.validation.monte_carlo_robustness``. Percentages signed
    (drawdowns negative). ``dd_p95_severity_pct`` = the drawdown breached only
    ~5% of the time; ``prob_loss`` = fraction of resampled paths ending below
    water."""
    n_sims: int = 0
    block_size: int = 0
    dd_median_pct: Optional[float] = None
    dd_p95_severity_pct: Optional[float] = None
    dd_worst_pct: Optional[float] = None
    terminal_median_pct: Optional[float] = None
    terminal_p05_pct: Optional[float] = None
    prob_loss: Optional[float] = None
    prob_dd_worse_than_tol: Optional[float] = None
    drawdown_tolerance_pct: Optional[float] = None


class SubPeriods(_Strict):
    """Time-concentration of the edge across contiguous sub-periods. Computed by
    ``services.backtest.validation.sub_period_robustness``. ``concentration`` ~
    1/n_periods = evenly spread (robust); near 1 = almost all the return from one
    window (fragile / regime-dependent)."""
    n_periods: int = 0
    period_returns_pct: list[float] = Field(default_factory=list)
    positive_period_frac: Optional[float] = None
    best_period_return_pct: Optional[float] = None
    worst_period_return_pct: Optional[float] = None
    concentration: Optional[float] = None


class TrustVerdict(_Strict):
    """One actionable call synthesised from the rigor battery
    (``services.backtest.validation.trust_verdict``). ``verdict`` ∈
    {insufficient_data, no_edge, unproven, promising}; ``confidence`` 0–100 = the
    statistical P(edge is real); ``flags`` = independent risk concerns
    (return_concentrated / drawdown_risk / loss_likely / selection_bias)."""
    verdict: str = "insufficient_data"
    label: str = ""
    confidence: int = 0
    rationale: str = ""
    flags: list[str] = Field(default_factory=list)


class BacktestMetrics(_Strict):
    """Headline performance numbers. Delegates to
    backend.backtester.metrics.calculate_metrics where the field
    names overlap."""
    total_return_pct: float
    cagr_pct: float
    max_drawdown_pct: float
    max_drawdown_duration_days: int
    win_rate_pct: float
    total_trades: int
    winning_trades: int
    losing_trades: int
    average_win_pct: Optional[float] = None
    average_loss_pct: Optional[float] = None
    profit_factor: Optional[float] = None
    sharpe_ratio: Optional[float] = None
    sortino_ratio: Optional[float] = None
    # Buy-and-hold of the primary symbol over the window, net of one
    # round-trip cost — so strategy vs benchmark is apples-to-apples.
    benchmark_return_pct: Optional[float] = None
    ending_value: float
    # Statistical-rigor battery (PSR / MinTRL / DSR) on the equity curve.
    forward_stats: Optional[ForwardStats] = None
    # Monte-Carlo (block-bootstrap) drawdown / terminal-wealth distribution.
    monte_carlo: Optional[MonteCarlo] = None
    # Time-concentration of the edge across contiguous sub-periods.
    sub_periods: Optional[SubPeriods] = None
    # One actionable call synthesised from the whole rigor battery.
    trust_verdict: Optional[TrustVerdict] = None


class BacktestDiagnostics(_Strict):
    """DSL-specific telemetry. Kept separate from metrics so the
    'how good is this strategy' numbers don't get cluttered with
    engine-internal info."""
    bars_evaluated: int
    warmup_bars_skipped: int
    unknown_value_bars: int = Field(
        ...,
        description=(
            "Count of bars where the tree evaluated to Ternary.UNKNOWN "
            "(missing data, indicator warmup, etc.) — high values "
            "suggest the strategy or data is too sparse."
        ),
    )
    fire_bars: int = Field(
        ..., description="Count of bars where tree evaluated to TRUE.",
    )
    symbols_loaded: list[str]
    indicator_cache_keys: list[str]


class BacktestResult(_Strict):
    """Returned from POST /api/backtest/dsl/run."""
    request_id: str
    user_id: int
    requested_at: datetime
    completed_at: datetime
    tree_summary: str = Field(
        ..., description="tree_to_english(tree) for the audit page.",
    )
    request: BacktestRequest
    trades: list[TradeRow]
    equity_curve: list[EquityPoint]
    metrics: BacktestMetrics
    diagnostics: BacktestDiagnostics


class RunListItem(_Strict):
    """Shape returned from GET /api/backtest/dsl/runs (list view)."""
    id: str
    primary_symbol: str
    start_date: date
    end_date: date
    tree_summary: str
    status: Literal["running", "succeeded", "failed", "cancelled"]
    total_return_pct: Optional[float] = None
    total_trades: Optional[int] = None
    started_at: datetime
    finished_at: Optional[datetime] = None


class RunListResponse(_Strict):
    runs: list[RunListItem]
