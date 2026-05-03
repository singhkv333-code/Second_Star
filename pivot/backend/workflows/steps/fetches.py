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
    """Fetch latest quote (LTP, OHLC, volume) for a single symbol.

    Path 1: try Kite live quote (real or mock — backend.kite.market_data
            handles the routing).
    Path 2: yfinance fallback for the OHLC + volume backfill — Kite
            mock returns only `last_price`, so we round-trip via yfinance
            to populate the rest. yfinance is keyless and works for
            most NSE symbols (.NS suffix).

    Output shape matches the catalog declaration. `asof` is always
    UTC ISO 8601.
    """
    from datetime import datetime, timezone

    from backend.kite.market_data import (
        get_historical_ohlcv,
        get_live_quote,
    )
    from backend.workflows.steps.actions import _kite_token_for_run

    cfg = ctx.config
    symbol = str(cfg["symbol"]).upper()
    exchange = str(cfg.get("exchange", "NSE")).upper()
    instrument = f"{exchange}:{symbol}"
    token = _kite_token_for_run(ctx)

    quote = (get_live_quote(token, [instrument]) or {}).get(instrument, {})
    ltp = float(quote.get("last_price", 0) or 0)

    ohlc = (quote.get("ohlc") or {})
    open_p = float(ohlc.get("open", 0) or 0)
    high_p = float(ohlc.get("high", 0) or 0)
    low_p = float(ohlc.get("low", 0) or 0)
    close_p = float(ohlc.get("close", 0) or 0)
    volume = float(quote.get("volume", 0) or 0)

    # Backfill from yfinance if Kite path returned a stub.
    if open_p == 0 or close_p == 0 or volume == 0:
        try:
            bars = get_historical_ohlcv(symbol, period="5d", interval="1d") or []
        except Exception:
            bars = []
        if bars:
            latest = bars[-1]
            open_p = open_p or float(latest.get("open", 0) or 0)
            high_p = high_p or float(latest.get("high", 0) or 0)
            low_p = low_p or float(latest.get("low", 0) or 0)
            close_p = close_p or float(latest.get("close", 0) or 0)
            volume = volume or float(latest.get("volume", 0) or 0)
            ltp = ltp or close_p

    if ltp <= 0:
        # No live data and no historical data → fail loudly.
        raise NotYetAvailableError(
            f"fetch.quote: no quote available for {instrument}"
        )

    return {
        "ltp": ltp,
        "open": open_p,
        "high": high_p,
        "low": low_p,
        "close": close_p,
        "volume": volume,
        "asof": datetime.now(timezone.utc).isoformat(),
    }


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
    """Compute a technical indicator (RSI / SMA / EMA / MACD) for a
    symbol. Pulls historical OHLCV via yfinance (keyless) and runs
    pandas_ta_classic. Returns the latest indicator value + asof.

    For MACD we return the macd-minus-signal value (the histogram-style
    delta) — that's the most useful single-number output for a
    threshold trigger ('macd > 0' = bullish crossover).
    """
    from datetime import datetime, timezone

    import pandas as pd  # type: ignore[import-untyped]
    import pandas_ta_classic as ta  # type: ignore[import-untyped]

    from backend.kite.market_data import get_historical_ohlcv

    cfg = ctx.config
    symbol = str(cfg["symbol"]).upper()
    indicator = str(cfg["indicator"]).lower()
    period = int(cfg["period"])

    # Need enough history for the indicator. RSI needs ~period+1; MACD
    # default 26+9. Pull 6 months for headroom.
    bars = get_historical_ohlcv(symbol, period="6mo", interval="1d") or []
    if len(bars) < period + 5:
        raise NotYetAvailableError(
            f"fetch.indicator: not enough history for {symbol} "
            f"({len(bars)} bars; need {period + 5}+)"
        )

    df = pd.DataFrame(bars)
    if "close" not in df.columns:
        raise NotYetAvailableError(
            f"fetch.indicator: history for {symbol} missing 'close' column"
        )

    series: Optional[pd.Series] = None
    if indicator == "rsi":
        series = ta.rsi(df["close"], length=period)
    elif indicator == "sma":
        series = ta.sma(df["close"], length=period)
    elif indicator == "ema":
        series = ta.ema(df["close"], length=period)
    elif indicator == "macd":
        # Standard MACD with the user-supplied period as the slow EMA.
        # Returns a DataFrame with MACD/MACDh/MACDs columns; we use
        # the histogram (macd - signal).
        macd_df = ta.macd(df["close"], fast=12, slow=max(period, 13), signal=9)
        if macd_df is None or macd_df.empty:
            raise NotYetAvailableError(
                f"fetch.indicator: MACD computation returned empty for {symbol}"
            )
        # Histogram column name pattern: MACDh_12_26_9
        hist_col = next(
            (c for c in macd_df.columns if c.startswith("MACDh_")),
            None,
        )
        if hist_col is None:
            raise NotYetAvailableError(
                "fetch.indicator: MACD output missing histogram column"
            )
        series = macd_df[hist_col]
    else:
        raise ValueError(f"unsupported indicator: {indicator!r}")

    if series is None or series.dropna().empty:
        raise NotYetAvailableError(
            f"fetch.indicator: {indicator}({period}) on {symbol} "
            f"produced no values"
        )

    value = float(series.dropna().iloc[-1])
    return {
        "value": value,
        "computed_at": datetime.now(timezone.utc).isoformat(),
    }


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
    """Fetch a fundamental metric (PE / ROE / market cap / D/E) for a
    symbol via yfinance `Ticker.info`. yfinance is keyless and works
    for most NSE symbols (.NS suffix).

    Metric mapping:
      pe   → trailingPE (fall back to forwardPE if missing)
      roe  → returnOnEquity (decimal — 0.18 = 18%)
      mcap → marketCap (INR for .NS symbols)
      de   → debtToEquity

    Raises NotYetAvailableError when yfinance returns None for the
    requested metric (common for newly-listed or unusual symbols).
    """
    from datetime import date, datetime, timezone

    import yfinance as yf  # type: ignore[import-untyped]

    cfg = ctx.config
    symbol = str(cfg["symbol"]).upper()
    metric = str(cfg["metric"]).lower()
    ticker_symbol = symbol if symbol.endswith(".NS") else f"{symbol}.NS"

    try:
        info = yf.Ticker(ticker_symbol).info or {}
    except Exception as e:
        raise NotYetAvailableError(
            f"fetch.fundamental: yfinance lookup failed for {symbol}: {e}"
        ) from e

    metric_to_keys = {
        "pe":   ["trailingPE", "forwardPE"],
        "roe":  ["returnOnEquity"],
        "mcap": ["marketCap"],
        "de":   ["debtToEquity"],
    }
    keys = metric_to_keys.get(metric)
    if keys is None:
        raise ValueError(f"unsupported fundamental metric: {metric!r}")

    raw_value: Optional[float] = None
    for key in keys:
        v = info.get(key)
        if isinstance(v, (int, float)) and v is not None:
            raw_value = float(v)
            break
    if raw_value is None:
        raise NotYetAvailableError(
            f"fetch.fundamental: {metric} not available for {symbol} "
            f"(yfinance returned no value)"
        )

    out: dict[str, Any] = {
        "value": raw_value,
        "source": "yfinance",
    }
    # Best-effort period_end for statement-derived metrics.
    last_fiscal = info.get("lastFiscalYearEnd")
    if isinstance(last_fiscal, (int, float)):
        out["period_end"] = datetime.fromtimestamp(
            int(last_fiscal), tz=timezone.utc,
        ).date().isoformat()
    elif isinstance(last_fiscal, date):
        out["period_end"] = last_fiscal.isoformat()
    return out


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
