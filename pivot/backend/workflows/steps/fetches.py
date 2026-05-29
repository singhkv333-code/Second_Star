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
    FetchDayOpenConfig,
    FetchFundamentalConfig,
    FetchIndicatorConfig,
    FetchIntradayPnLConfig,
    FetchNewsConfig,
    FetchPortfolioConfig,
    FetchPriorCloseConfig,
    FetchQuoteConfig,
    FetchRelativeThresholdConfig,
    FetchRollingHighConfig,
    FetchRollingLowConfig,
    FetchSpreadZScoreConfig,
    FetchScreenerConfig,
    FetchTopMoversConfig,
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
    """Compute a technical indicator for a symbol via the unified
    ``backend.services.backtest_indicators`` registry.

    Supports every indicator the registry knows about (rsi, sma, ema,
    wma, macd, adx, supertrend, bollinger, stoch, stoch_rsi, cci, mfi,
    williams_r, atr, keltner, donchian, aroon, psar, roc, trix, obv,
    vwap …). The returned ``value`` is the canonical scalar — for
    composite indicators that means histogram (MACD), %B (Bollinger),
    direction (Supertrend), %K (Stochastic) etc. — see the registry
    for the full mapping.
    """
    from datetime import datetime, timezone

    import pandas as pd  # type: ignore[import-untyped]

    from backend.kite.market_data import get_historical_ohlcv, period_for_indicator
    from backend.services.backtest_indicators import (
        compute_series, get_spec,
    )

    cfg = ctx.config
    symbol = str(cfg["symbol"]).upper()
    indicator = str(cfg["indicator"]).lower()
    period = int(cfg["period"])

    spec = get_spec(indicator)
    if spec is None:
        raise ValueError(f"unsupported indicator: {indicator!r}")

    # P0 parity: window sized to the indicator period (guard floor 30 here).
    bars = get_historical_ohlcv(
        symbol, period=period_for_indicator(period, floor=30), interval="1d",
    ) or []
    if len(bars) < max(period + 5, 30):
        raise NotYetAvailableError(
            f"fetch.indicator: not enough history for {symbol} "
            f"({len(bars)} bars; need {max(period + 5, 30)}+)"
        )

    df = pd.DataFrame(bars)
    series = compute_series(df, indicator, period)
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
    """Fetch a fundamental metric for a symbol.

    Two emission shapes (see FetchFundamentalConfig docstring):
      * Named metric (any FIELD_MAP key, or legacy code pe/roe/de/mcap)
      * Formula  (`metric: "formula"`, `formula: "<arithmetic expr>"`)

    Resolution order:
      1. financials DB via `resolve_metric` (same path the backtester
         uses, so live and replay agree).
      2. yfinance fallback only for legacy short codes (pe/roe/de/mcap).
         Named FIELD_MAP keys and formulas that the DB can't satisfy
         return NotYetAvailableError — there's no live equivalent for
         e.g. ROCE or a custom formula.
    """
    from datetime import date, datetime, timezone

    from backend.market.financials_db import resolve_metric

    cfg = ctx.config
    symbol = str(cfg["symbol"]).upper()
    metric = str(cfg.get("metric") or "").lower()
    formula = cfg.get("formula")

    # ---- Path 1: financials DB --------------------------------------------
    try:
        db_value = resolve_metric(symbol, metric, formula=formula)
    except Exception:  # noqa: BLE001 — never let DB miss block the live path
        db_value = None
    if db_value is not None:
        return {
            "value": db_value,
            "source": "financials_db",
            "metric": metric if metric != "formula" else f"formula: {formula}",
        }

    # ---- Path 2: yfinance fallback (legacy short codes only) -------------
    _LEGACY_YF_KEYS = {
        "pe":   ["trailingPE", "forwardPE"],
        "roe":  ["returnOnEquity"],
        "mcap": ["marketCap"],
        "de":   ["debtToEquity"],
    }
    keys = _LEGACY_YF_KEYS.get(metric)
    if keys is None:
        raise NotYetAvailableError(
            f"fetch.fundamental: {metric!r} not available for {symbol} "
            f"in the financials DB and has no yfinance fallback. "
            f"Try a different metric or formula."
        )

    import yfinance as yf  # type: ignore[import-untyped]
    ticker_symbol = symbol if symbol.endswith(".NS") else f"{symbol}.NS"
    try:
        info = yf.Ticker(ticker_symbol).info or {}
    except Exception as e:
        raise NotYetAvailableError(
            f"fetch.fundamental: yfinance lookup failed for {symbol}: {e}"
        ) from e

    raw_value: Optional[float] = None
    for key in keys:
        v = info.get(key)
        if isinstance(v, (int, float)) and v is not None:
            raw_value = float(v)
            break
    if raw_value is None:
        raise NotYetAvailableError(
            f"fetch.fundamental: {metric} not available for {symbol} "
            f"(neither financials DB nor yfinance returned a value)"
        )

    out = {"value": raw_value, "source": "yfinance", "metric": metric}
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
    step_type="fetch.intraday_pnl",
    category="fetch",
    label="Get intraday P&L",
    description="Compute realised + unrealised P&L from current holdings",
    icon="trending-down",
    max_retries=3,
    trigger_only=False,
    config_model=FetchIntradayPnLConfig,
    output_schema={
        "type": "object",
        "properties": {
            "total_pct": {"type": "number"},
            "total_inr": {"type": "number"},
            "unrealised_inr": {"type": "number"},
            "realised_inr": {"type": "number"},
            "cost_basis_inr": {"type": "number"},
            "by_symbol": {"type": "object"},
        },
        "required": ["total_pct", "total_inr", "cost_basis_inr"],
    },
)
async def execute_fetch_intraday_pnl(ctx: Any) -> Optional[dict[str, Any]]:
    """Compute P&L from the user's holdings without an extra Kite hop.

    `fetch.portfolio` already returns last_price and average_price per
    holding; we recompute mark-to-market here so the workflow doesn't
    need a separate Kite call. The result fits a `condition.numeric`
    that compares against `total_pct` (a percentage) directly:

        - step 1: fetch.intraday_pnl(scope='intraday')
        - step 2: condition.numeric(left='{{ context.1.total_pct }}',
                                    operator='<', right=-2)
        - step 3: action.squareoff_all_intraday()

    Realised P&L from closed positions today is not separately tracked
    here (Kite needs the orderbook for that); we surface 0 and put
    everything into `unrealised_inr`. Acceptable for the v1 risk-gate
    use case where the unrealised number is what people watch.
    """
    user_id = int(ctx.workflow.user_id)
    portfolio = get_user_portfolio(user_id, ctx.db)
    holdings = portfolio.get("holdings", []) or []

    cfg = ctx.config or {}
    scope = str(cfg.get("scope", "all")).lower()

    by_symbol: dict[str, dict[str, float]] = {}
    total_unrealised = 0.0
    total_cost_basis = 0.0
    for h in holdings:
        # The portfolio dict only carries delivery (CNC) lots today —
        # the intraday MIS positions live in Kite's `positions` endpoint
        # which we'll wire on demand. For now `scope` is informational;
        # all surfaced holdings count toward the calc.
        del scope  # silence linter; gated by future Kite positions wire
        try:
            qty = float(h.get("quantity") or 0)
            avg = float(h.get("average_price") or 0)
            ltp = float(h.get("last_price") or 0)
        except (TypeError, ValueError):
            continue
        if qty <= 0 or avg <= 0:
            continue
        cost = qty * avg
        mtm = qty * ltp
        pnl = mtm - cost
        by_symbol[str(h.get("tradingsymbol") or "?")] = {
            "qty": qty,
            "avg": round(avg, 2),
            "ltp": round(ltp, 2),
            "pnl_inr": round(pnl, 2),
            "pnl_pct": round((pnl / cost) * 100.0 if cost else 0.0, 3),
        }
        total_unrealised += pnl
        total_cost_basis += cost

    realised = 0.0
    total_pnl = total_unrealised + realised
    total_pct = (total_pnl / total_cost_basis * 100.0) if total_cost_basis else 0.0

    return {
        "total_pct": round(total_pct, 3),
        "total_inr": round(total_pnl, 2),
        "unrealised_inr": round(total_unrealised, 2),
        "realised_inr": round(realised, 2),
        "cost_basis_inr": round(total_cost_basis, 2),
        "by_symbol": by_symbol,
    }


@register_step(
    step_type="fetch.news",
    category="fetch",
    label="Get news",
    description=(
        "Fetch recent news articles for keywords and (optionally) "
        "classify them against an event description"
    ),
    icon="newspaper",
    max_retries=3,
    trigger_only=False,
    config_model=FetchNewsConfig,
    output_schema={
        "type": "object",
        "properties": {
            "articles": {"type": "array"},
            "matched": {"type": "boolean"},
            "max_confidence": {"type": "number"},
            "matched_count": {"type": "integer"},
            "top_article": {"type": ["object", "null"]},
            "event_description": {"type": "string"},
        },
        "required": [
            "articles", "matched", "max_confidence",
            "matched_count", "event_description",
        ],
    },
)
async def execute_fetch_news(ctx: Any) -> Optional[dict[str, Any]]:
    """Fetch keyword-matching articles, optionally classify them.

    Flow:
      1. ``news_client.fetch_news(keywords, hours_back=...)`` — returns
         up to 20 articles (or [] in mock mode / on transient HTTP fail).
      2. If ``sources`` is set, drop anything whose ``source_id`` isn't
         in the allowlist.
      3. If ``event_description`` is set, run
         ``classifier.classify_article`` over the articles in parallel
         (Semaphore(5) so we don't flood the LLM provider). Each article's
         ``matched``, ``match_confidence`` and ``reason`` are populated.
      4. Aggregate: ``matched = any(a.matched and confidence >=
         min_confidence)``. ``top_article`` is the highest-confidence
         match (or None).

    The aggregate ``matched`` flag is what a downstream
    ``condition.boolean`` keys off:

        condition.boolean(left='{{ context.<idx>.matched }}', value=true)

    Mock-mode tolerance: with an empty ``NEWSAPI_KEY``, the client
    returns [] and we surface an empty result with ``matched=False``
    without raising.
    """
    import asyncio
    import logging

    from backend.triggers.classifier import classify_article
    from backend.triggers.news_client import fetch_news

    logger = logging.getLogger(__name__)

    cfg = ctx.config or {}
    keywords_raw = cfg.get("keywords") or []
    if not isinstance(keywords_raw, list) or not keywords_raw:
        raise ValueError("fetch.news: 'keywords' must be a non-empty list")
    keywords = [str(k) for k in keywords_raw if isinstance(k, str) and k.strip()]
    if not keywords:
        raise ValueError("fetch.news: 'keywords' must be a non-empty list")

    event_description = cfg.get("event_description") or ""
    event_description = str(event_description).strip()
    sources_raw = cfg.get("sources")
    source_allow: Optional[set[str]] = None
    if isinstance(sources_raw, list) and sources_raw:
        source_allow = {str(s).strip().lower() for s in sources_raw if s}
    min_confidence = float(cfg.get("min_confidence", 0.85) or 0.85)
    hours_back = int(cfg.get("hours_back", 48) or 48)

    articles = await fetch_news(keywords, hours_back=hours_back)
    if not articles:
        # Either mock mode (no API key) or a transient miss. Surface
        # cleanly so a downstream condition.boolean evaluates `matched`
        # as False and the workflow's "no news" branch runs.
        logger.warning(
            "fetch.news returned no articles (keywords=%s, hours_back=%d). "
            "Likely mock mode (empty NEWSAPI_KEY) or rate-limited NewsAPI.",
            keywords, hours_back,
        )
        return {
            "articles": [],
            "matched": False,
            "max_confidence": 0.0,
            "matched_count": 0,
            "top_article": None,
            "event_description": event_description,
        }

    if source_allow is not None:
        articles = [
            a for a in articles
            if (a.source_id or "").strip().lower() in source_allow
        ]

    if event_description and articles:
        # Bounded fan-out so we never flood the LLM provider on a hot
        # NewsAPI day. 5 in flight is plenty for the typical 20-article
        # page; classifier latency is the dominant cost either way.
        sem = asyncio.Semaphore(5)

        async def _classify_one(a: Any) -> None:
            async with sem:
                matched, confidence, reason = await classify_article(
                    a, event_description,
                )
            a.matched = matched
            a.match_confidence = confidence
            a.reason = reason

        await asyncio.gather(*(_classify_one(a) for a in articles))

    # Aggregate. An article counts toward `matched` only if it
    # cleared the configured confidence floor.
    qualifying = [
        a for a in articles
        if a.matched and (a.match_confidence or 0.0) >= min_confidence
    ]
    matched_any = bool(qualifying)
    max_confidence = max(
        (float(a.match_confidence or 0.0) for a in articles),
        default=0.0,
    )
    top: Optional[dict[str, Any]] = None
    if qualifying:
        top_article = max(
            qualifying, key=lambda a: float(a.match_confidence or 0.0),
        )
        top = top_article.model_dump(mode="json")

    return {
        "articles": [a.model_dump(mode="json") for a in articles],
        "matched": matched_any,
        "max_confidence": round(max_confidence, 4),
        "matched_count": len(qualifying),
        "top_article": top,
        "event_description": event_description,
    }


# ── Day-anchored fetches ─────────────────────────────────────────────


def _resolve_day_anchor(
    symbol: str, exchange: str, kite_token: str,
    *, want_open: bool, sessions_back: int = 0,
) -> tuple[float, str]:
    """Shared helper for the three day-anchored fetches below.

    Returns (price, session_iso_date) where price is:
      - today's OPEN if want_open=True and sessions_back==0
      - the CLOSE of `sessions_back` trading days ago otherwise
        (sessions_back=1 → previous trading day's close)

    Strategy: prefer Kite live quote for today's open (one round-trip,
    same data path as fetch.quote). Use yfinance for historical opens
    or any prior-session close — yfinance returns daily bars including
    the most recent completed sessions.

    Raises NotYetAvailableError when neither source produces a price.
    """
    from datetime import datetime, timezone

    from backend.kite.market_data import (
        get_historical_ohlcv,
        get_live_quote,
    )

    instrument = f"{exchange}:{symbol}"

    if want_open and sessions_back == 0:
        quote = (get_live_quote(kite_token, [instrument]) or {}).get(
            instrument, {}
        )
        ohlc = quote.get("ohlc") or {}
        open_p = float(ohlc.get("open", 0) or 0)
        if open_p > 0:
            return open_p, datetime.now(timezone.utc).date().isoformat()
        # Fall through to yfinance.

    # yfinance path. Pull more bars than we need so we can index back.
    period = "1mo" if sessions_back > 5 else "5d"
    bars = get_historical_ohlcv(symbol, period=period, interval="1d") or []
    if not bars:
        raise NotYetAvailableError(
            f"fetch.day_anchor: no history available for {instrument}"
        )
    # Bars are oldest-first. For today's open we want the latest bar;
    # for prior closes we go N back from the latest.
    if want_open and sessions_back == 0:
        bar = bars[-1]
        price = float(bar.get("open", 0) or 0)
    else:
        idx = -1 - sessions_back
        if abs(idx) > len(bars):
            raise NotYetAvailableError(
                f"fetch.day_anchor: only {len(bars)} sessions of history "
                f"for {instrument}; cannot look back {sessions_back}"
            )
        bar = bars[idx]
        price = float(bar.get("close", 0) or 0)
    if price <= 0:
        raise NotYetAvailableError(
            f"fetch.day_anchor: no price for {instrument} session "
            f"{bar.get('date', '?')}"
        )
    return price, str(bar.get("date", "")) or ""


@register_step(
    step_type="fetch.day_open",
    category="fetch",
    label="Get today's open",
    description="Fetch today's market open price for a symbol",
    icon="sunrise",
    max_retries=3,
    trigger_only=False,
    config_model=FetchDayOpenConfig,
    output_schema={
        "type": "object",
        "properties": {
            "value": {"type": "number"},
            "session_date": {"type": "string"},
        },
        "required": ["value"],
    },
)
async def execute_fetch_day_open(ctx: Any) -> Optional[dict[str, Any]]:
    """Today's market-open price for `symbol`. Use this when a workflow
    needs to compare current price against today's open (gap-up/gap-down
    intraday rules). Live quotes carry `ohlc.open`; for early-pre-market
    runs we fall back to yfinance's most recent daily bar."""
    from backend.workflows.steps.actions import _kite_token_for_run

    cfg = ctx.config
    symbol = str(cfg["symbol"]).upper()
    exchange = str(cfg.get("exchange", "NSE")).upper()
    token = _kite_token_for_run(ctx)
    price, session = _resolve_day_anchor(
        symbol, exchange, token, want_open=True, sessions_back=0,
    )
    return {"value": price, "session_date": session}


@register_step(
    step_type="fetch.prior_close",
    category="fetch",
    label="Get prior close",
    description="Fetch the previous trading day's closing price",
    icon="sunset",
    max_retries=3,
    trigger_only=False,
    config_model=FetchPriorCloseConfig,
    output_schema={
        "type": "object",
        "properties": {
            "value": {"type": "number"},
            "session_date": {"type": "string"},
            "sessions_back": {"type": "integer"},
        },
        "required": ["value"],
    },
)
async def execute_fetch_prior_close(ctx: Any) -> Optional[dict[str, Any]]:
    """The closing price `sessions_back` trading days ago. Default 1 =
    previous trading day. Use this for 'X% above last close' style
    rules without forcing the user to look up the number."""
    from backend.workflows.steps.actions import _kite_token_for_run

    cfg = ctx.config
    symbol = str(cfg["symbol"]).upper()
    exchange = str(cfg.get("exchange", "NSE")).upper()
    sessions_back = int(cfg.get("sessions_back", 1))
    token = _kite_token_for_run(ctx)
    price, session = _resolve_day_anchor(
        symbol, exchange, token,
        want_open=False, sessions_back=sessions_back,
    )
    return {
        "value": price,
        "session_date": session,
        "sessions_back": sessions_back,
    }


@register_step(
    step_type="fetch.rolling_high",
    category="fetch",
    label="Get rolling N-day high",
    description=(
        "Highest HIGH over the last N daily bars — the recent peak."
    ),
    icon="trending-up",
    max_retries=3,
    trigger_only=False,
    config_model=FetchRollingHighConfig,
    output_schema={
        "type": "object",
        "properties": {
            "value": {"type": "number"},
            "lookback": {"type": "integer"},
        },
        "required": ["value"],
    },
)
async def execute_fetch_rolling_high(ctx: Any) -> Optional[dict[str, Any]]:
    """Highest HIGH across the last `lookback` trading days × multiplier.
    Pairs with condition.numeric for "X% off the recent high" rules."""
    import pandas as pd  # type: ignore[import-untyped]
    from backend.kite.market_data import get_historical_ohlcv

    cfg = ctx.config
    symbol = str(cfg["symbol"]).upper()
    lookback = int(cfg.get("lookback", 20))
    multiplier = float(cfg.get("multiplier", 1.0))
    bars = get_historical_ohlcv(symbol, period="1y", interval="1d") or []
    if len(bars) < lookback:
        raise NotYetAvailableError(
            f"fetch.rolling_high: need {lookback} bars for {symbol}, "
            f"got {len(bars)}"
        )
    df = pd.DataFrame(bars[-lookback:])
    high_col = "high" if "high" in df.columns else "High"
    return {
        "value": float(df[high_col].astype(float).max()) * multiplier,
        "lookback": lookback,
        "multiplier": multiplier,
    }


@register_step(
    step_type="fetch.rolling_low",
    category="fetch",
    label="Get rolling N-day low",
    description=(
        "Lowest LOW over the last N daily bars — the recent trough."
    ),
    icon="trending-down",
    max_retries=3,
    trigger_only=False,
    config_model=FetchRollingLowConfig,
    output_schema={
        "type": "object",
        "properties": {
            "value": {"type": "number"},
            "lookback": {"type": "integer"},
        },
        "required": ["value"],
    },
)
async def execute_fetch_rolling_low(ctx: Any) -> Optional[dict[str, Any]]:
    """Lowest LOW across the last `lookback` trading days × multiplier."""
    import pandas as pd  # type: ignore[import-untyped]
    from backend.kite.market_data import get_historical_ohlcv

    cfg = ctx.config
    symbol = str(cfg["symbol"]).upper()
    lookback = int(cfg.get("lookback", 20))
    multiplier = float(cfg.get("multiplier", 1.0))
    bars = get_historical_ohlcv(symbol, period="1y", interval="1d") or []
    if len(bars) < lookback:
        raise NotYetAvailableError(
            f"fetch.rolling_low: need {lookback} bars for {symbol}, "
            f"got {len(bars)}"
        )
    df = pd.DataFrame(bars[-lookback:])
    low_col = "low" if "low" in df.columns else "Low"
    return {
        "value": float(df[low_col].astype(float).min()) * multiplier,
        "lookback": lookback,
        "multiplier": multiplier,
    }


@register_step(
    step_type="fetch.spread_z_score",
    category="fetch",
    label="Compute spread z-score",
    description=(
        "Z-score of (close_a − close_b) spread over a rolling window."
    ),
    icon="git-compare",
    max_retries=3,
    trigger_only=False,
    config_model=FetchSpreadZScoreConfig,
    output_schema={
        "type": "object",
        "properties": {
            "value": {"type": "number"},
            "spread": {"type": "number"},
            "mean": {"type": "number"},
            "std": {"type": "number"},
        },
        "required": ["value"],
    },
)
async def execute_fetch_spread_z_score(
    ctx: Any,
) -> Optional[dict[str, Any]]:
    """Compute the z-score of the close-price spread between two symbols
    over a rolling window. Returns the latest z-score for pairs-trade
    entry / exit gating.

    Symmetric: swapping symbol_a and symbol_b flips the sign of the
    z-score. By convention, positive z means symbol_a is rich relative
    to symbol_b.
    """
    import pandas as pd  # type: ignore[import-untyped]
    from backend.kite.market_data import get_historical_ohlcv, period_for_bars

    cfg = ctx.config
    sym_a = str(cfg["symbol_a"]).upper()
    sym_b = str(cfg["symbol_b"]).upper()
    lookback = int(cfg.get("lookback", 30))

    # P0 parity: window sized to the lookback (guard is lookback+5).
    _spread_period = period_for_bars(lookback + 5, cap="2y")
    bars_a = get_historical_ohlcv(sym_a, period=_spread_period, interval="1d") or []
    bars_b = get_historical_ohlcv(sym_b, period=_spread_period, interval="1d") or []
    if len(bars_a) < lookback + 5 or len(bars_b) < lookback + 5:
        raise NotYetAvailableError(
            f"fetch.spread_z_score: need {lookback + 5} bars each for "
            f"{sym_a}/{sym_b}; got {len(bars_a)}/{len(bars_b)}"
        )
    df_a = pd.DataFrame(bars_a).set_index("date")["close"].astype(float)
    df_b = pd.DataFrame(bars_b).set_index("date")["close"].astype(float)
    spread = (df_a - df_b).dropna()
    if len(spread) < lookback:
        raise NotYetAvailableError(
            f"fetch.spread_z_score: spread has {len(spread)} aligned "
            f"bars; need {lookback}"
        )
    window = spread.iloc[-lookback:]
    mean = float(window.mean())
    std = float(window.std(ddof=0))
    current = float(spread.iloc[-1])
    z = (current - mean) / std if std > 0 else 0.0
    return {
        "value": z,
        "spread": current,
        "mean": mean,
        "std": std,
    }


@register_step(
    step_type="fetch.relative_threshold",
    category="fetch",
    label="Compute relative price level",
    description=(
        "Compute an absolute price level relative to today's open or a "
        "prior session value, plus a percentage offset"
    ),
    icon="git-compare",
    max_retries=3,
    trigger_only=False,
    config_model=FetchRelativeThresholdConfig,
    output_schema={
        "type": "object",
        "properties": {
            "value": {"type": "number"},
            "reference_value": {"type": "number"},
            "reference_label": {"type": "string"},
            "applied_offset_pct": {"type": "number"},
        },
        "required": ["value", "reference_value", "reference_label"],
    },
)
async def execute_fetch_relative_threshold(
    ctx: Any,
) -> Optional[dict[str, Any]]:
    """Single-step "X% above/below today's open / prior_close / etc."

    Why this exists: condition.numeric compares left vs right with
    operators only — no arithmetic. So expressing "current price is
    5% below today's open" used to need a Mustache expression like
    `{{ context.2.value }} * 0.95` which the resolver can't evaluate.
    This step does the multiplication once at run time, returning the
    absolute level the next step can compare against directly.

    Output `value` is `reference_value * (1 + offset_pct/100)`. So
    `offset_pct=-5` returns 95% of the reference; `offset_pct=2`
    returns 102%. Callers point a `condition.numeric.right` at
    `{{ context.<idx>.value }}` and they're done.
    """
    from backend.workflows.steps.actions import _kite_token_for_run

    cfg = ctx.config
    symbol = str(cfg["symbol"]).upper()
    exchange = str(cfg.get("exchange", "NSE")).upper()
    reference = str(cfg["reference"])
    offset_pct = float(cfg.get("offset_pct", 0) or 0)
    token = _kite_token_for_run(ctx)

    # Map the named reference to a (want_open, sessions_back, label).
    # prior_high / prior_low share the prior_close path's bar lookup
    # but pull a different field — handled inline below.
    if reference == "day_open":
        ref_value, _ = _resolve_day_anchor(
            symbol, exchange, token, want_open=True, sessions_back=0,
        )
        ref_label = "today's open"
    elif reference == "prior_close":
        ref_value, _ = _resolve_day_anchor(
            symbol, exchange, token, want_open=False, sessions_back=1,
        )
        ref_label = "prior close"
    elif reference in {"prior_high", "prior_low"}:
        from backend.kite.market_data import get_historical_ohlcv
        bars = get_historical_ohlcv(symbol, period="5d", interval="1d") or []
        if len(bars) < 2:
            raise NotYetAvailableError(
                f"fetch.relative_threshold: only {len(bars)} sessions for "
                f"{exchange}:{symbol}; can't resolve {reference}"
            )
        bar = bars[-2]  # one session back
        field = "high" if reference == "prior_high" else "low"
        ref_value = float(bar.get(field, 0) or 0)
        if ref_value <= 0:
            raise NotYetAvailableError(
                f"fetch.relative_threshold: no {field} on prior session "
                f"for {exchange}:{symbol}"
            )
        ref_label = f"prior {field}"
    else:
        # The schema's Literal already constrains this, but defensive
        # check keeps the executor honest if someone bypasses validation.
        raise ValueError(f"unknown reference: {reference}")

    value = ref_value * (1.0 + offset_pct / 100.0)
    return {
        "value": round(value, 4),
        "reference_value": round(ref_value, 4),
        "reference_label": ref_label,
        "applied_offset_pct": offset_pct,
    }


# ── Sector screener ──────────────────────────────────────────────────


@register_step(
    step_type="fetch.screener",
    category="fetch",
    label="Screen stocks by sector / cap",
    description=(
        "Filter and rank Indian stocks by sector and market cap. "
        "Returns a symbols list the next step can act on."
    ),
    icon="filter",
    max_retries=1,
    trigger_only=False,
    config_model=FetchScreenerConfig,
    output_schema={
        "type": "object",
        "properties": {
            "symbols": {"type": "array", "items": {"type": "string"}},
            "ranked": {"type": "array"},
            "n": {"type": "integer"},
            "ranked_by": {"type": "string"},
            "sector_resolved": {"type": ["string", "null"]},
        },
        "required": ["symbols", "n"],
    },
)
async def execute_fetch_screener(ctx: Any) -> Optional[dict[str, Any]]:
    """Static-universe screener. Backed by
    `backend.services.sector_universe.query_screener` — a curated list
    of NIFTY sectoral index constituents with approximate market caps.

    Output shape:
      symbols          — bare symbol list, ready for `action.allocate_notional`
      ranked           — same rows with name/sector/mcap_cr fields, for the
                         draft card UI to display
      n                — count
      ranked_by        — 'mcap' (default) or 'symbol'
      sector_resolved  — the canonical sector after alias resolution,
                         or None when no sector filter was applied.
    """
    from backend.services.sector_universe import (
        normalize_sector, query_screener,
    )

    cfg = ctx.config
    sector_input = cfg.get("sector") or None
    rows = query_screener(
        sector=sector_input,
        mcap_min_cr=cfg.get("mcap_min_cr"),
        mcap_max_cr=cfg.get("mcap_max_cr"),
        sort_by=cfg.get("sort_by", "mcap"),
        descending=bool(cfg.get("descending", True)),
        limit=int(cfg.get("limit", 10)),
    )
    if not rows and sector_input:
        # Sector unknown OR universe is empty for that filter. Surface
        # cleanly as an executor error so the engine logs it.
        raise ValueError(
            f"fetch.screener: no symbols matched sector={sector_input!r}. "
            f"Known sectors: see backend.services.sector_universe."
        )
    return {
        "symbols": [r["symbol"] for r in rows],
        "ranked": rows,
        "n": len(rows),
        "ranked_by": cfg.get("sort_by", "mcap"),
        "sector_resolved": (
            normalize_sector(sector_input) if sector_input else None
        ),
    }


# ── Top movers (gainers / losers) ─────────────────────────────────────


@register_step(
    step_type="fetch.top_movers",
    category="fetch",
    label="Top gainers / losers",
    description=(
        "Today's biggest movers in NIFTY 50. Use to drive prompts "
        "like 'buy the top gainer at close' or 'short the day's "
        "top loser'."
    ),
    icon="trending-up",
    max_retries=1,
    trigger_only=False,
    config_model=FetchTopMoversConfig,
    output_schema={
        "type": "object",
        "properties": {
            "symbols": {"type": "array", "items": {"type": "string"}},
            "ranked": {"type": "array"},
            "n": {"type": "integer"},
            "direction": {"type": "string"},
            "seeded": {"type": "boolean"},
        },
        "required": ["symbols", "n", "direction"],
    },
)
async def execute_fetch_top_movers(ctx: Any) -> Optional[dict[str, Any]]:
    """Pulls top gainers / losers from yfinance via
    `backend.services.top_movers.get_top_movers`. yfinance access is
    blocking; the engine awaits us, so the call cost is the network
    round-trip (one batched download for ~50 symbols).

    When yfinance fails, the underlying service returns a curated
    seed list with `seed=True` on each row — the workflow continues
    to fire on stale data rather than 503-ing the whole run, and the
    UI / model can disclose seeded values to the user.
    """
    from backend.services.top_movers import get_top_movers

    cfg = ctx.config
    rows = get_top_movers(
        direction=cfg.get("direction", "gainers"),
        universe=cfg.get("universe", "nifty50"),
        limit=int(cfg.get("limit", 1)),
    )
    seeded = bool(rows and rows[0].get("seed"))
    return {
        "symbols": [r["symbol"] for r in rows],
        "ranked": rows,
        "n": len(rows),
        "direction": cfg.get("direction", "gainers"),
        "seeded": seeded,
    }
