"""Data fetch step executors.

Per ARCHITECTURE.md §7 invariant 3, fetches have max_retries=3 with
1s/4s/16s backoff — the engine drives the retry loop, executors raise
on transient errors and the engine handles the rest.

For Day 2 we ship `fetch.portfolio` (real, via backend.services.portfolio).
The other fetches stay NotImplementedError until their backing data
sources land (Day 3-4 for quotes/indicators, later for fundamentals/news).

Where the underlying source isn't ready, the real executor raises
NotYetAvailableError with a clear message — never fake data. Day 2
stubs raise NotImplementedError to make it loud.
"""
from __future__ import annotations

from typing import Any, Optional

from backend.services.portfolio import get_user_portfolio
from backend.workflows.registry import register_step
from backend.workflows.schemas import (
    FetchFundamentalConfig,
    FetchIndicatorConfig,
    FetchNewsConfig,
    FetchPortfolioConfig,
    FetchQuoteConfig,
)


class NotYetAvailableError(RuntimeError):
    """Raised by fetch executors whose backing data source hasn't
    landed yet (e.g. moneycontrol fundamentals). The engine maps this
    to a step failure with a clear message — frontend renders verbatim.

    Never substitute fake data. ARCHITECTURE.md §5.2 footnote.
    """


@register_step(
    step_type="fetch.quote",
    category="fetch",
    label="Get live quote",
    description="Fetch the latest LTP, OHLC, and volume for a symbol",
    icon="bar-chart-3",
    max_retries=3,
    trigger_only=False,
    config_model=FetchQuoteConfig,
    output_schema={
        "type": "object",
        "properties": {
            "ltp": {"type": "number"},
            "open": {"type": "number"},
            "high": {"type": "number"},
            "low": {"type": "number"},
            "close": {"type": "number"},
            "volume": {"type": "number"},
            "asof": {"type": "string", "format": "date-time"},
        },
        "required": ["ltp", "asof"],
    },
)
async def execute_fetch_quote(ctx: Any) -> Optional[dict[str, Any]]:
    raise NotImplementedError("fetch.quote executor lands Day 3")


@register_step(
    step_type="fetch.indicator",
    category="fetch",
    label="Get indicator value",
    description="Compute a technical indicator (RSI, SMA, EMA, MACD)",
    icon="line-chart",
    max_retries=3,
    trigger_only=False,
    config_model=FetchIndicatorConfig,
    output_schema={
        "type": "object",
        "properties": {
            "value": {"type": "number"},
            "computed_at": {"type": "string", "format": "date-time"},
        },
        "required": ["value", "computed_at"],
    },
)
async def execute_fetch_indicator(ctx: Any) -> Optional[dict[str, Any]]:
    raise NotImplementedError("fetch.indicator executor lands Day 3")


@register_step(
    step_type="fetch.fundamental",
    category="fetch",
    label="Get fundamental",
    description="Fetch a fundamental metric (P/E, ROE, market cap, D/E)",
    icon="book-open",
    max_retries=3,
    trigger_only=False,
    config_model=FetchFundamentalConfig,
    output_schema={
        "type": "object",
        "properties": {
            "value": {"type": "number"},
            "period_end": {"type": "string", "format": "date"},
            "source": {"type": "string"},
        },
        "required": ["value"],
    },
)
async def execute_fetch_fundamental(ctx: Any) -> Optional[dict[str, Any]]:
    raise NotYetAvailableError(
        "fetch.fundamental requires the fundamentals data source — "
        "not yet wired in v1"
    )


@register_step(
    step_type="fetch.portfolio",
    category="fetch",
    label="Get portfolio",
    description="Fetches holdings, buying power, and total value",
    icon="wallet",
    max_retries=3,
    trigger_only=False,
    config_model=FetchPortfolioConfig,
    output_schema={
        "type": "object",
        "properties": {
            "holdings": {"type": "array"},
            "buying_power": {"type": "number"},
            "total_value": {"type": "number"},
        },
        "required": ["holdings", "buying_power", "total_value"],
    },
)
async def execute_fetch_portfolio(ctx: Any) -> Optional[dict[str, Any]]:
    """Delegate to the portfolio service. The engine passes us a live
    SQLAlchemy Session via ctx.db so we don't have to open our own.

    The Workflow row carries the user_id; the service resolves the
    user's Kite token (real or mock) and returns the canonical shape.
    """
    user_id = int(ctx.workflow.user_id)
    return get_user_portfolio(user_id, ctx.db)


@register_step(
    step_type="fetch.news",
    category="fetch",
    label="Get news",
    description="Fetch recent news articles and average sentiment",
    icon="newspaper",
    max_retries=3,
    trigger_only=False,
    config_model=FetchNewsConfig,
    output_schema={
        "type": "object",
        "properties": {
            "articles": {"type": "array"},
            "avg_sentiment": {"type": "number"},
        },
        "required": ["articles"],
    },
)
async def execute_fetch_news(ctx: Any) -> Optional[dict[str, Any]]:
    raise NotImplementedError("fetch.news executor lands Day 4")
