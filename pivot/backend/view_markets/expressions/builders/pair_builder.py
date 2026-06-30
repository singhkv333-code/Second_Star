"""View Markets — Phase 3 PAIR expression builder (the flagship non-basket).

Handles ``expression_kind == "pair"``: R1 cointegrated pair, R2 sector-vs-index,
R3 factor-ETF-vs-index, R4 ratio/RS (graceful degrade), E2 NBFC-vs-bank rate
pair, and the COMMODITY pairs CM3 producer-vs-importer (equity legs that
backtest) + CM4 gold/silver ratio (direct MCX bullion). Delegates to the REAL
pairs engine — never reinvents the cointegration math:

Commodity handling (MCX, tradeable via register-not-execute since 2026-06-29):
a leg that resolves to a listed MCX F&O symbol (``commodities.is_commodity``) is
LEVERAGED — its short routes through ``honest_short.short_leg_for(is_commodity=
True)`` to a TRADEABLE MCX future (the clean symmetric short, NEVER an AVOID) or
a defined-risk MCX put, and the expression carries ``commodities.LEVERAGE_NOTE``
+ is never auto-sized. Direct MCX commodity legs have NO aligned OHLCV in the
pairs data layer, so a direct-MCX pair is CONSTRUCT-ONLY (``backtest_available``
False, no fabricated β/half-life/z); a bullion ratio can be MEASURED on the
listed GOLDBEES/SILVERBEES ETF proxies (``ctx['use_etf_proxy']``) while the live
legs stay the direct MCX futures.

  * ``backend.services.backtest.pairs.cointegration``:
    - ``hedge_ratio(y, x) -> (alpha, beta)`` (OLS; spread = y − β·x)
    - ``engle_granger(y, x) -> EngleGrangerResult`` (``alpha``, ``beta``,
      ``adf_tstat``, ``cointegrated_at`` in {"1%","5%","10%",None},
      ``is_cointegrated``, ``half_life`` (OU), ``spread``)
    - ``ou_half_life(spread) -> float | None`` — the tradeability gate vs horizon
      T (half-life MUST be < T, spec §3.1-#4)
    - ``rolling_zscore(spread, window) -> np.ndarray`` — for the R4 ratio degrade
    - ``johansen(series, k_ar_diff=1)`` for 3+ leg baskets
  * ``backend.services.backtest.pairs.engine.run_pairs_backtest(symbol_a,
    symbol_b, *, period="2y", lookback=60, entry_z=2.0, exit_z=0.5, stop_z=4.0,
    hedge="rolling", ...)`` — the causal, no-look-ahead, beta-hedged simulation
    (reports ``cointegration{beta,adf_tstat,half_life_days}``, ``metrics``,
    ``series``). Phase 4 owns the full backtest; the builder calls it for the
    construction-time alignment (β + half-life + ADF verdict in one pass).
  * ``backend.view_markets.expressions.cross_sectional.FACTOR_ETF_MAP`` — the R3
    smart-beta ETF long leg.
  * ``backend.view_markets.expressions.honest_short.short_leg_for(symbol, *,
    is_index, ssf_eligible)`` — the SHORT leg vehicle (SSF future / index future
    / put / AVOID). The §3 honest short is enforced here, never a fake delivery
    short. A degraded short lowers ``expressability`` → dispatch lowers the score.

Tier knobs (``tiers.tier_knobs("pair", tier)``) set the z-bands
(``pair_z_entry``/``exit``/``stop``). The builder converts β + capital into
PER-LEG notional/weights and reports the residual market beta (≈0 by the
β-hedge: the spread A − β·B carries zero net exposure to B).

Persists to ``config.structure`` the keys in
``config_schema.STRUCTURE_KEYS["pair"]`` (``a``, ``b``, ``beta``,
``half_life_days``, ``z_entry/exit/stop``, ``leg_a``, ``leg_b``,
``residual_beta``, ``short_leg``) + India-typed ``instruments`` + a
construct-rigor-tiered ``scores.construction_alignment`` (R1 > R2 > R3 > R4;
``archetype.params["rigor_tier"]`` sets the ceiling).
"""
from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING, Any, Optional

from backend.services.backtest.pairs.engine import PairsError, run_pairs_backtest
from backend.services.trading_costs import round_trip_bps
from backend.view_markets.expressions import commodities, honest_short
from backend.view_markets.expressions.catalog import KIND_PAIR
from backend.view_markets.expressions.config_schema import base_envelope
from backend.view_markets.expressions.cross_sectional import factor_etf
from backend.view_markets.expressions.tiers import tier_knobs

if TYPE_CHECKING:  # pragma: no cover - typing only
    from sqlalchemy.orm import Session

    from backend.models import MarketView
    from backend.view_markets.expressions.catalog import Archetype

# Construction-time Alignment Score CEILING by §3 construct rigor (spec line 309:
# cointegrated pair > sector-vs-index > factor-ETF tilt > ratio/RS). The ceiling
# guarantees a ratio/RS trade can NEVER score above a true cointegrated pair.
_RIGOR_CEILING: dict[int, float] = {1: 95.0, 2: 85.0, 3: 75.0, 4: 60.0}
_DEFAULT_CEILING: float = 70.0

# How strongly the ADF verdict and the half-life-vs-horizon gate scale the score.
_COINT_FACTOR: dict[Optional[str], float] = {
    "1%": 1.0, "5%": 0.88, "10%": 0.70, None: 0.45,
}

# Index tokens whose short is an index future/put (never an ETF delivery short).
_INDEX_TOKENS: frozenset[str] = (
    honest_short.SHORTABLE_INDEX_FUTURES
    | honest_short.WEEKLY_INDICES
    | honest_short.MONTHLY_ONLY_INDICES
)

_DEFAULT_CAPITAL_INR: float = 1_000_000.0


def _is_index(symbol: str) -> bool:
    """True when ``symbol`` is an index underlying (route short → index future)."""
    return symbol.upper().replace(" ", "") in _INDEX_TOKENS


def _horizon_days(view: "MarketView", ctx: dict[str, Any]) -> Optional[int]:
    """Parse the view's ``time_horizon`` string ("1m"/"6m"/"2y+"/"30d") to days.

    Used as the OU half-life tradeability gate (spec §3.1-#4: half-life < T).
    ``ctx['horizon_days']`` wins when the caller pins an explicit horizon.
    """
    override = ctx.get("horizon_days")
    if isinstance(override, (int, float)) and override > 0:
        return int(override)
    raw = getattr(view, "time_horizon", None)
    if not raw:
        return None
    text = str(raw).strip().lower().rstrip("+")
    num = "".join(ch for ch in text if ch.isdigit())
    if not num:
        return None
    n = int(num)
    if "y" in text:
        return n * 365
    if "w" in text:
        return n * 7
    if "d" in text:
        return n
    # default unit is months ("3m", or a bare number)
    return n * 30


def _resolve_symbols(
    archetype: "Archetype",
    symbol_a: Optional[str],
    symbol_b: Optional[str],
    ctx: dict[str, Any],
) -> tuple[str, str, Optional[str]]:
    """Resolve (long leg A, short leg B, A-display-label).

    Direction is "A beats B" → long A / short B. Leg B defaults to the archetype's
    ``leg_b`` (NIFTY for sector/factor-vs-index). For R3 the long leg is a
    smart-beta ETF named via ``cross_sectional.FACTOR_ETF_MAP`` (the live ticker is
    pinned in INTEGRATE; the index label is the stable display name).
    """
    params = archetype.params
    a = symbol_a or ctx.get("symbol_a") or params.get("leg_a")
    b = symbol_b or ctx.get("symbol_b") or params.get("leg_b") or "NIFTY"

    a_label: Optional[str] = None
    factor = ctx.get("factor") or params.get("factor")
    if factor:
        mapping = factor_etf(str(factor))
        if mapping is not None:
            a_label = mapping.index
            a = a or mapping.index  # display fallback when no tradeable ticker given
    if not a:
        raise ValueError(
            f"pair builder for {archetype.key}: long leg A is unresolved "
            "(pass symbol_a / ctx['symbol_a'] / ctx['factor']); refusing to "
            "fabricate a leg."
        )
    return str(a), str(b), a_label


def _size_legs(
    a: str,
    b: str,
    beta: Optional[float],
    capital: float,
) -> tuple[dict[str, Any], dict[str, Any], Optional[float]]:
    """Convert β + capital into per-leg weights/notional + the residual market beta.

    Beta-hedged dollar-neutral construction (the same the engine simulates): the
    spread is ``A − β·B``, so gross capital splits ``1/(1+|β|)`` to A and
    ``|β|/(1+|β|)`` to B. The spread's net exposure to B is zero by construction →
    ``residual_beta = 0.0``. When β is unavailable (degraded), fall back to equal
    notional and report ``residual_beta = None`` (cannot claim neutrality).

    Lot/share counts need the live price + lot size from the instrument master
    (pinned in INTEGRATE); reported as ``None`` here rather than fabricated.
    """
    if beta is not None and abs(beta) > 1e-9:
        ab = abs(beta)
        w_a = 1.0 / (1.0 + ab)
        w_b = ab / (1.0 + ab)
        residual_beta: Optional[float] = 0.0
    else:
        w_a = w_b = 0.5
        residual_beta = None

    leg_a = {
        "symbol": a,
        "side": "long",
        "weight": round(w_a, 4),
        "notional": round(capital * w_a, 2),
        "shares": None,  # pinned to live price × lot size in INTEGRATE
        "note": "long leg",
    }
    leg_b = {
        "symbol": b,
        "side": "short",
        "weight": round(w_b, 4),
        "notional": round(capital * w_b, 2),
        "shares": None,
        "note": "short leg routed through honest_short (no fabricated delivery short)",
    }
    return leg_a, leg_b, residual_beta


def _instrument_for_short(short: honest_short.ShortLeg) -> dict[str, Any]:
    """India-type the short leg from the honest-short verdict."""
    mode = short.mode
    if mode == "index_future":
        itype, segment, exchange = "index_future", "NFO-FUT", "NFO"
    elif mode == "index_put":
        itype, segment, exchange = "index_option", "NFO-OPT", "NFO"
    elif mode == "ssf_future":
        itype, segment, exchange = "stock_future", "NFO-FUT", "NFO"
    elif mode in ("put", "put_spread"):
        itype, segment, exchange = "stock_option", "NFO-OPT", "NFO"
    elif mode == "commodity_future":
        # The clean SYMMETRIC commodity short — a tradeable MCX future, NOT AVOID.
        itype, segment, exchange = "commodity_future", "MCX-FUT", "MCX"
    elif mode == "commodity_put":
        # Defined-risk commodity short via a long MCX put.
        itype, segment, exchange = "commodity_option", "MCX-OPT", "MCX"
    else:  # avoid — not a tradeable instrument
        itype, segment, exchange = "equity", "EQ", "NSE"
    return {
        "symbol": short.instrument,
        "exchange": exchange,
        "segment": segment,
        "instrument_type": itype,
        "role": "short",
        "tradeable": short.tradeable,
        "note": short.note,
    }


def _instrument_for_long(
    symbol: str, is_factor_etf: bool, *, is_commodity: bool = False,
) -> dict[str, Any]:
    """India-type the long leg A (commodity / factor ETF / index / single stock)."""
    if is_commodity:
        # Direct MCX commodity long — a leveraged future (the symmetric long leg).
        itype, segment, exchange = "commodity_future", "MCX-FUT", "MCX"
        note = "long leg A (direct MCX commodity future — LEVERAGED, never auto-sized)"
    elif is_factor_etf:
        itype, segment, exchange = "etf", "ETF", "NSE"
        note = "long leg A"
    elif _is_index(symbol):
        itype, segment, exchange = "index", "INDEX", "NSE"
        note = "long leg A"
    else:
        itype, segment, exchange = "equity", "EQ", "NSE"
        note = "long leg A"
    return {
        "symbol": symbol,
        "exchange": exchange,
        "segment": segment,
        "instrument_type": itype,
        "role": "long",
        "tradeable": True,
        "note": note,
    }


def build_pair_expression(
    db: "Session",
    view: "MarketView",
    archetype: "Archetype",
    tier: str,
    *,
    symbol_a: str | None = None,
    symbol_b: str | None = None,
    period: str = "2y",
    **ctx: Any,
) -> dict[str, Any]:
    """Build a cointegrated-pair / sector-vs-index / factor-ETF / ratio expression.

    Fits the hedge ratio + ADF/OU via the real ``run_pairs_backtest`` engine (which
    wraps ``engle_granger``/``ou_half_life``/``rolling_zscore``), checks
    ``half_life < T``, applies the tier z-bands, sizes per-leg notional from β +
    capital, and resolves the SHORT leg through ``honest_short.short_leg_for`` (SSF
    future / index future / put / AVOID — never a fabricated delivery short). When
    the short degrades, sets ``config.expressability.degraded = True`` so dispatch
    drops the Alignment Score. Returns a ``config_schema`` envelope; never
    fabricates a number when the data is thin (raises ``ValueError`` honestly).
    """
    knobs = tier_knobs(KIND_PAIR, tier)
    z_entry = knobs.pair_z_entry or 2.0
    z_exit = knobs.pair_z_exit or 0.5
    z_stop = knobs.pair_z_stop or 4.0

    rigor_tier = int(archetype.params.get("rigor_tier", 0) or 0)
    is_ratio = archetype.template_or_scheme == "rolling_zscore"
    is_factor_etf = bool(ctx.get("factor") or archetype.params.get("factor")) or (
        archetype.key.startswith("R3")
    )

    a, b, a_label = _resolve_symbols(archetype, symbol_a, symbol_b, ctx)
    horizon = _horizon_days(view, ctx)
    capital = float(
        ctx.get("capital_inr") or ctx.get("total_inr") or ctx.get("capital")
        or _DEFAULT_CAPITAL_INR
    )

    # ── commodity (MCX) detection ────────────────────────────────────────────
    # A leg is a commodity when it resolves to a listed MCX F&O symbol (GOLD,
    # SILVER, CRUDEOIL …). A commodity expression is leveraged and carries the
    # leverage-risk note; the short leg routes through honest_short with
    # ``is_commodity=True`` → a TRADEABLE MCX future (never an AVOID).
    a_is_commodity = commodities.is_commodity(a)
    b_is_commodity = commodities.is_commodity(b)
    is_commodity_expr = (
        bool(archetype.params.get("commodity")) or a_is_commodity or b_is_commodity
    )

    warnings: list[str] = []
    label = archetype.label or f"Long {a_label or a} / short {b} pair"
    envelope = base_envelope(
        archetype=archetype.key,
        expression_kind=KIND_PAIR,
        tier=tier,
        label=label,
    )

    # ── pick the legs the cointegration runs on ──────────────────────────────
    # Direct MCX commodity futures have NO aligned daily OHLCV in the pairs data
    # layer (yfinance .NS / NSE-only), so a direct-MCX pair is CONSTRUCT-ONLY: we
    # build the structure + honest short but never fabricate a spread/β/z. A
    # bullion ratio (CM4) can instead be MEASURED on the listed GOLDBEES/SILVERBEES
    # ETF proxies when the caller opts in (``ctx['use_etf_proxy']``); the LIVE legs
    # stay the direct MCX futures (the ETF can't be shorted) — the relationship is
    # measured on the proxy, never invented.
    bt_a, bt_b = a, b
    proxy_basis = False
    backtest_blocked: Optional[str] = None
    if not (
        commodities.price_history_available(a)
        and commodities.price_history_available(b)
    ):
        proxy_a = commodities.etf_proxy(a)
        proxy_b = commodities.etf_proxy(b)
        if ctx.get("use_etf_proxy") and proxy_a and proxy_b:
            bt_a, bt_b, proxy_basis = proxy_a, proxy_b, True
        else:
            backtest_blocked = (
                f"direct MCX commodity legs ({a}/{b}) have no aligned daily OHLCV "
                "in the pairs data layer (yfinance .NS / NSE-only)"
            )

    # ── delegate cointegration + alignment to the REAL pairs engine ──────────
    alpha = beta = half_life = adf_tstat = None
    cointegrated_at: Optional[str] = None
    lookback = int(ctx.get("lookback", 60))
    degraded_stats = False
    backtest_available = True
    if backtest_blocked is not None:
        # Construct-only: do NOT call the engine with legs we KNOW have no history
        # (avoids a misleading PairsError) — degrade honestly, no fabricated stats.
        degraded_stats = True
        backtest_available = False
        warnings.append(
            f"backtest unavailable — {backtest_blocked}. Built as a CONSTRUCT-ONLY "
            "structure: the legs + honest short are real and tradeable, but the "
            "spread β / half-life / z-score are pending (no fabricated "
            "cointegration). For a backtestable bullion spread, route via the "
            "GOLDBEES/SILVERBEES ETF proxy (set use_etf_proxy)."
        )
    else:
        try:
            bt = run_pairs_backtest(
                bt_a, bt_b, period=period, lookback=lookback,
                entry_z=z_entry, exit_z=z_exit, stop_z=z_stop,
            )
            coint = bt["cointegration"]
            alpha = coint.get("alpha")
            beta = coint.get("beta")
            adf_tstat = coint.get("adf_tstat")
            half_life = coint.get("half_life_days")
            cointegrated_at = coint.get("cointegrated_at")
            if proxy_basis:
                warnings.append(
                    f"spread statistics computed on the ETF proxies {bt_a}/{bt_b} "
                    "(the listed, backtestable bullion route) — the LIVE legs remain "
                    f"the direct MCX futures {a}/{b} (LEVERAGED). The relationship is "
                    "measured on the proxy, never fabricated."
                )
        except PairsError as exc:
            # Thin/unavailable data (e.g. R3 ETF ticker not yet in the instrument
            # master). Degrade honestly — no fabricated β / ADF / half-life.
            degraded_stats = True
            backtest_available = False
            warnings.append(
                f"cointegration unavailable for {bt_a}/{bt_b} over {period}: {exc}. "
                "Spread statistics pending live aligned series (INTEGRATE pins the "
                "smart-beta ETF ticker / Kite history)."
            )

    # Half-life-vs-horizon tradeability gate (spec §3.1-#4).
    hl_factor = 0.5
    if half_life is not None and horizon:
        if half_life < horizon:
            hl_factor = 1.0
        else:
            hl_factor = 0.6
            warnings.append(
                f"OU half-life {half_life}d ≥ horizon {horizon}d — the spread may "
                "not revert inside the view's window (spec §3.1 gate)."
            )
    elif half_life is None and not degraded_stats:
        warnings.append("spread is not mean-reverting (no positive OU half-life).")

    if cointegrated_at is None and not degraded_stats:
        warnings.append(
            "residual is NOT stationary at the 10% level — this is a ratio/RS "
            "view, not a proven cointegrated pair (lower rigor)."
        )
    if is_ratio:
        warnings.append(
            "ratio / relative-strength degrade: traded on the z-scored ratio "
            "without a stationarity proof — carries a deliberately lower "
            "Alignment Score than a cointegrated pair (spec §3.4)."
        )

    # ── honest short leg (NEVER a fabricated delivery short) ─────────────────
    # For a commodity short leg, ``is_commodity=True`` routes to a TRADEABLE MCX
    # future (clean symmetric short, degraded=False) — or a defined-risk MCX put
    # when ``prefer_defined_risk`` is set — never an AVOID for a listed commodity.
    short = honest_short.short_leg_for(
        b,
        is_index=_is_index(b),
        is_commodity=b_is_commodity,
        ssf_eligible=ctx.get("ssf_eligible"),
        fno_eligible=ctx.get("fno_eligible"),
        prefer_defined_risk=bool(ctx.get("prefer_defined_risk", False)),
    )
    if short.note:
        warnings.append(short.note)
    warnings.extend(short.warnings)

    # ── commodity leverage-risk note (the convention — never auto-size) ──────
    # Every commodity expression MUST surface the leverage note in config.warnings
    # (the disclosure ``risk_profile`` column folds it in at dispatch). Lots are
    # NEVER auto-sized — register-not-execute, the user confirms in their broker.
    if is_commodity_expr:
        warnings.append(commodities.LEVERAGE_NOTE)

    # ── per-leg sizing + residual market beta ────────────────────────────────
    leg_a, leg_b, residual_beta = _size_legs(a, b, beta, capital)
    short_leg_dict = dataclasses.asdict(short)

    # ── construction-time Alignment Score (ceiling-tiered by construct rigor) ─
    ceiling = _RIGOR_CEILING.get(rigor_tier, _DEFAULT_CEILING)
    # A bullion ratio (CM4) is deliberately capped at the ratio/RS ceiling so it
    # can NEVER out-score a true cointegrated pair (spec: lower ratio rigor).
    if archetype.params.get("lower_alignment_ceiling"):
        ceiling = min(ceiling, _RIGOR_CEILING[4])
    coint_factor = 0.45 if degraded_stats else _COINT_FACTOR.get(cointegrated_at, 0.45)
    degrade_factor = 0.8 if short.degraded else 1.0
    score = max(0.0, min(100.0, ceiling * coint_factor * hl_factor * degrade_factor))

    # ── assemble the envelope ────────────────────────────────────────────────
    envelope["structure"] = {
        "a": a,
        "b": b,
        "a_label": a_label,
        "alpha": alpha,
        "beta": beta,
        "half_life_days": half_life,
        "adf_tstat": adf_tstat,
        "cointegrated_at": cointegrated_at,
        "lookback": lookback,
        "z_entry": z_entry,
        "z_exit": z_exit,
        "z_stop": z_stop,
        "leg_a": leg_a,
        "leg_b": leg_b,
        "residual_beta": residual_beta,
        "short_leg": short_leg_dict,
        "rigor_tier": rigor_tier,
        "is_commodity": is_commodity_expr,
        "backtest_available": backtest_available,
        "proxy_basis": proxy_basis,
    }
    envelope["instruments"] = [
        _instrument_for_long(a, is_factor_etf, is_commodity=a_is_commodity),
        _instrument_for_short(short),
    ]
    envelope["expressability"] = {
        "symmetric": not short.degraded,
        "degraded": short.degraded,
        "short_mode": short.mode,
        "commodity": is_commodity_expr,
        "leverage_note": commodities.LEVERAGE_NOTE if is_commodity_expr else None,
        "notes": [n for n in [short.note, *short.warnings] if n],
    }
    envelope["scores"] = {
        "construction_alignment": round(score, 1),
        "basket_purity": None,
        "alignment_kind": "relative_value",
    }
    cost_note = (
        "pairs ≈ double commissions — STT + both-leg slippage + futures "
        "roll / SLB borrow drag apply (spec §3.1)."
    )
    if is_commodity_expr:
        cost_note += (
            " Commodity legs settle on MCX (SPAN+exposure margin, contango/roll "
            "drag) — leveraged, never auto-sized."
        )
    envelope["costs"] = {
        "round_trip_bps": round_trip_bps(),
        "note": cost_note,
    }
    envelope["warnings"] = warnings
    return dict(envelope)


__all__ = ["build_pair_expression"]
