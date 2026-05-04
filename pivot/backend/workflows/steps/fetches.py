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
    FetchScreenerConfig,
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
