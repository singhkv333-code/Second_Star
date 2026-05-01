"""Data fetch step stubs.

Per ARCHITECTURE.md §7 invariant 3, fetches have max_retries=3 with
1s/4s/16s backoff — they're idempotent reads against external APIs that
may transiently fail.

Where the underlying source isn't ready (e.g. fundamentals DB), the
real executor raises NotYetAvailableError with a clear message — never
fake data. Stubs raise NotImplementedError instead until Day 2."""
from __future__ import annotations

from typing import Any, Optional

from backend.workflows.registry import register_step
from backend.workflows.schemas import (
    FetchFundamentalConfig,
    FetchIndicatorConfig,
    FetchNewsConfig,
    FetchPortfolioConfig,
    FetchQuoteConfig,
)


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
async def execute_fetch_quote(*args: Any, **kwargs: Any) -> Optional[dict[str, Any]]:
    raise NotImplementedError("not yet implemented")


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
async def execute_fetch_indicator(*args: Any, **kwargs: Any) -> Optional[dict[str, Any]]:
    raise NotImplementedError("not yet implemented")


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
async def execute_fetch_fundamental(*args: Any, **kwargs: Any) -> Optional[dict[str, Any]]:
    raise NotImplementedError("not yet implemented")


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
async def execute_fetch_portfolio(*args: Any, **kwargs: Any) -> Optional[dict[str, Any]]:
    raise NotImplementedError("not yet implemented")


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
async def execute_fetch_news(*args: Any, **kwargs: Any) -> Optional[dict[str, Any]]:
    raise NotImplementedError("not yet implemented")
