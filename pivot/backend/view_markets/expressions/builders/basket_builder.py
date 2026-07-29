"""View Markets — Phase 3 BASKET expression builder (good basket, NOT flat).

Handles ``expression_kind == "basket"``: T1 purity/conviction basket, T2
factor-tilt, E5 PEAD long basket, E7 merger-arb long, E8 index-inclusion, E9
budget/election rotation. The whole point (spec §1.1, §4): an equal-weight basket
is a FAILURE mode — this builder runs the screened, conviction/purity-weighted,
capped pipeline instead.

Pipeline (spec §4): Universe → Purity → Liquidity → min-names floor → Conviction
weight → Cap → (optional) factor tilt. Delegates to the REAL engines:

  * ``backend.services.weighting.compute_weights_detailed`` — the scheme is
    chosen by the tier's ``basket_concentration`` (Cons=risk_parity, Bal=mcap,
    Aggr=factor), falling back to the archetype ``template_or_scheme``; the
    ``fallback_reason`` is surfaced honestly.
  * ``backend.services.sector_universe`` — ``query_screener`` for the universe +
    approximate mcaps; ``backend.services.thematic_map`` scenario winners/losers.
  * ``backend.view_markets.expressions.screens`` — ``purity_score`` /
    ``liquidity_screen`` (run BEFORE weighting) / ``min_names_floor`` (refuse a
    too-concentrated theme → ETF proxy) / ``apply_single_name_cap`` (tier cap,
    iterative redistribution) / ``basket_purity`` (headline construction score).
  * ``backend.view_markets.expressions.cross_sectional.composite_factor_scores``
    — the multi-factor composite fed to ``weighting(scheme="factor", views=...)``
    for T2.
  * ``backend.view_markets.expressions.merger_arb.merger_arb_metrics`` — for E7.
  * ``backend.view_markets.expressions.honest_short.avoid_annotation`` — the
    loser leg is an AVOID annotation, NEVER a fabricated short.

Each raw weight is multiplied by purity and renormalised (spec §4.3). When the
``min_names_floor`` refuses, the builder returns the ETF-proxy config (the
Conservative degrade) rather than a fake-diversified 3-stock basket.

Commodity (MCX) pass (2026-06-29 — commodities are tradeable via
register-not-execute): the same screened equity pipeline also builds the
**commodity-producer basket** (equity sleeve resolved from the crude
beneficiary universe via ``sector_universe.crude_up/down_beneficiaries``) and
the **crude-shock-hedged basket** — the equity defensive sleeve PLUS a DIRECT
MCX commodity leg (crude/bullion future or defined-risk option). The direct leg
is LEVERAGED: it carries ``commodities.LEVERAGE_NOTE``, is never auto-sized, and
degrades honestly (``backtest_available=False``) because the basket data layer
has no aligned direct-MCX OHLCV. A commodity SHORT leg is routed through
``honest_short.short_leg_for(is_commodity=True)`` → a TRADEABLE MCX future / put
(NEVER an AVOID or a fabricated short — commodities are symmetrically shortable).

Persists to ``config.structure`` (``config_schema.STRUCTURE_KEYS["basket"]``):
``scheme``, ``weights``, ``basket_purity``, ``single_name_cap``, ``n_names``,
plus ``purity`` / ``liquidity`` / ``factor_tilt`` / ``fallback_reason`` /
``etf_proxy`` / ``avoid`` / ``merger_arb`` / ``commodity_leg`` when relevant;
India-typed ``instruments`` are cash/delivery longs (losers are AVOID), plus an
optional DIRECT MCX ``commodity_future`` / ``commodity_option`` leg.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

from backend.core.data import historical
from backend.services import sector_universe, thematic_map, trading_costs, weighting
from backend.view_markets.expressions import (
    commodities,
    cross_sectional,
    honest_short,
    merger_arb,
    screens,
)
from backend.view_markets.expressions.catalog import KIND_BASKET
from backend.view_markets.expressions.config_schema import (
    InstrumentSpec,
    base_envelope,
)
from backend.view_markets.expressions.tiers import tier_knobs

if TYPE_CHECKING:  # pragma: no cover - typing only
    from sqlalchemy.orm import Session

    from backend.models import MarketView
    from backend.view_markets.expressions.catalog import Archetype

# Weighting schemes ``weighting.compute_weights`` actually understands.
_VALID_SCHEMES: frozenset[str] = frozenset(
    {"equal", "mcap", "risk_parity", "min_variance", "black_litterman", "factor"}
)

# Tier ``basket_concentration`` knob → a real weighting scheme (spec §4.3). The
# purity-scaling step is applied on top of whatever scheme runs, so
# "purity_scaled_mcap" maps to plain ``mcap`` here.
_CONCENTRATION_TO_SCHEME: dict[str, str] = {
    "risk_parity": "risk_parity",
    "min_variance": "min_variance",
    "purity_scaled_mcap": "mcap",
    "factor": "factor",
    "black_litterman": "black_litterman",
}

# Default factor composite for the T2 factor tilt (spec §4.3: multi-factor beats
# single-factor).
_DEFAULT_FACTORS: tuple[str, ...] = ("value", "momentum", "quality")

# How much history to pull for the covariance / momentum schemes (≥120 bars are
# needed before ``weighting`` trusts a covariance fit).
_PRICE_PERIOD: str = "2y"

# Names whose Theme Purity Score is below this are excluded outright (<10% = not
# the theme; spec §4.1).
_PURITY_EXCLUDE_BELOW: float = screens.PURITY_PERIPHERAL


def build_basket_expression(
    db: "Session",
    view: "MarketView",
    archetype: "Archetype",
    tier: str,
    *,
    symbols: list[str] | None = None,
    theme: str | None = None,
    total_inr: float | None = None,
    **ctx: Any,
) -> dict[str, Any]:
    """Build a screened, purity/conviction-weighted, capped basket expression.

    Runs ``purity_score`` + ``liquidity_screen`` over the universe, checks
    ``min_names_floor`` (refusing → ETF proxy when too concentrated), computes
    weights via ``weighting.compute_weights`` under the tier scheme, multiplies by
    purity and renormalises, then applies ``apply_single_name_cap`` with the
    tier's ``single_name_cap``. For T2 layers the multi-factor tilt; for E7 adds
    ``merger_arb_metrics``. Never ships a flat equal-weight basket where a
    conviction gradient is warranted; surfaces the ``fallback_reason`` honestly
    when a covariance scheme degraded. Returns a ``config_schema`` envelope.
    """
    knobs = tier_knobs(KIND_BASKET, tier)  # raises KeyError on an undefined cell
    theme_label = (theme or view.category or view.title or "").strip()

    env = base_envelope(
        archetype=archetype.key,
        expression_kind=KIND_BASKET,
        tier=tier,
        label=f"{archetype.label} — {theme_label or 'theme'} ({tier})",
    )
    warnings: list[str] = env["warnings"]
    cap = float(knobs.single_name_cap) if knobs.single_name_cap is not None else 0.20

    # 0. Commodity-producer universe: when no explicit symbols are supplied and
    #    the archetype/ctx carries a crude intent, resolve the equity sleeve from
    #    the crude beneficiary universe (sector_universe) — screened/weighted like
    #    any theme basket (reuse, not reinvent). ────────────────────────────────
    if not symbols:
        producer_syms = _commodity_producer_universe(archetype, ctx)
        if producer_syms:
            symbols = producer_syms
            warnings.append(
                "Resolved the equity sleeve from the crude beneficiary universe "
                "(sector_universe) — screened and conviction-weighted like any "
                "theme basket; no commodity-direction stock fabricated."
            )

    # 1. Universe + (optional) thematic scenario (for losers / invalidation). ──
    universe, scenario = _resolve_universe(symbols, theme_label, view)
    if not universe:
        return _refusal_envelope(
            env, knobs, theme_label, cap,
            reason=(
                "no investable universe could be resolved for this theme without "
                "fabricating names — supply explicit symbols or pick a known "
                "sector/scenario."
            ),
        )

    # 2. Purity score per name; drop sub-peripheral (<10%) names. ─────────────
    purities = {
        sym: screens.purity_score(db, sym, theme=theme_label)
        for sym in universe
    }
    kept = [s for s in universe if purities[s].score >= _PURITY_EXCLUDE_BELOW]
    if any(purities[s].estimated for s in kept):
        warnings.append(
            "Some Theme Purity Scores are LLM-estimated (no segment-revenue "
            "feed) — treat the purity gradient as approximate."
        )

    # 3. Liquidity screen (BEFORE weighting): drop fails, cap "watch" names. ──
    liq = {r.symbol: r for r in screens.liquidity_screen(db, kept)}
    survivors = [s for s in kept if s not in liq or liq[s].passes]
    dropped_illiquid = [s for s in kept if s in liq and not liq[s].passes]
    if dropped_illiquid:
        warnings.append(
            "Dropped for thin liquidity (below the ADV floor): "
            f"{', '.join(dropped_illiquid)}."
        )

    # 4. Min-names floor: refuse a too-concentrated "theme" → ETF proxy. ──────
    floor = screens.min_names_floor(
        survivors, theme=theme_label, min_names=screens.MIN_NAMES_DEFAULT
    )
    if not floor.ok:
        return _refusal_envelope(
            env, knobs, theme_label, cap,
            reason=floor.note,
            etf_proxy=floor.etf_proxy,
        )

    # 5. Weighting under the tier scheme. ────────────────────────────────────
    scheme = _scheme_for(knobs, archetype)
    price_history = _price_history(survivors)
    mcap = _mcap_map(survivors)
    views: Optional[dict[str, float]] = None
    factor_tilt: Optional[dict[str, Any]] = None
    if scheme == "factor":
        factors = _composite_factors(archetype)
        views = cross_sectional.composite_factor_scores(
            db, survivors, factors=factors
        )
        factor_tilt = {"factors": list(factors), "scores": dict(views)}

    wres = weighting.compute_weights_detailed(
        survivors,
        scheme,  # type: ignore[arg-type]
        price_history=price_history,
        mcap=mcap,
        views=views,
    )
    raw_weights = wres.weights
    if wres.fallback_reason:
        warnings.append(wres.fallback_reason)

    # 6. Purity-scale every weight + renormalise (spec §4.3). ─────────────────
    scaled = _purity_scale(raw_weights, purities)

    # 7. Single-name cap with iterative redistribution. ──────────────────────
    capped = screens.apply_single_name_cap(scaled, cap)

    # 8. Headline Basket Purity. ─────────────────────────────────────────────
    purity_list = [purities[s] for s in capped]
    headline_purity = screens.basket_purity(purity_list, capped)

    # 9. Instruments (all cash/delivery longs) + AVOID losers (never a short). ─
    env["instruments"] = [
        InstrumentSpec(
            symbol=sym,
            exchange="NSE",
            segment="EQ",
            instrument_type="equity",
            role="long",
            tradeable=True,
            note=f"purity {purities[sym].score:.0f} ({purities[sym].layer})",
        )
        for sym in capped
    ]
    avoid = _avoid_legs(scenario, survivors)

    # 9b. Optional DIRECT MCX commodity leg (crude / bullion) alongside the equity
    #     sleeve — the LEVERAGED leg of a crude-shock-hedged / commodity basket. ─
    commodity_spec, commodity_block = _commodity_leg(
        db, archetype, tier, ctx, warnings
    )
    if commodity_spec is not None:
        env["instruments"].append(commodity_spec)

    # 10. E7 merger-arb economics (long-only) when the deal inputs are present. ─
    merger_block = _merger_arb(archetype, ctx, warnings)

    # ── Assemble the envelope. ───────────────────────────────────────────────
    structure: dict[str, Any] = {
        "scheme": wres.scheme_used,
        "requested_scheme": scheme,
        "weights": {s: round(w, 6) for s, w in capped.items()},
        "purity": {s: round(purities[s].score, 1) for s in capped},
        "basket_purity": round(headline_purity, 1),
        "single_name_cap": cap,
        "min_names": floor.min_required,
        "n_names": len(capped),
        "liquidity": {
            s: {
                "adv_cr": liq[s].adv_cr,
                "watch": liq[s].watch,
                "options_available": liq[s].options_available,
            }
            for s in capped
            if s in liq
        },
        "rebalance": knobs.rebalance,
    }
    if factor_tilt is not None:
        structure["factor_tilt"] = factor_tilt
    if wres.fallback_reason:
        structure["fallback_reason"] = wres.fallback_reason
    if avoid:
        structure["avoid"] = avoid
    if merger_block is not None:
        structure["merger_arb"] = merger_block
    if commodity_block is not None:
        structure["commodity_leg"] = commodity_block
    env["structure"] = structure

    expr_notes: list[str] = []
    if avoid:
        expr_notes.append(
            "Underperformers expressed as AVOID, not shorted: "
            f"{', '.join(a['symbol'] for a in avoid)}."
        )
    short_mode: Optional[str] = None
    if commodity_block is not None:
        expr_notes.append(
            f"Direct MCX {commodity_block['symbol']} {commodity_block['direction']} "
            f"leg ({commodity_block['vehicle']}) sits ALONGSIDE the equity sleeve — "
            "LEVERAGED, register-not-execute, never auto-sized."
        )
        if commodity_block["direction"] == "short":
            # Commodities ARE symmetrically shortable — a TRADEABLE MCX future/put,
            # never an AVOID or a fabricated delivery short.
            short_mode = commodity_block["mode"]
            expr_notes.append(
                "Commodity short is a TRADEABLE MCX "
                f"{commodity_block['vehicle']} (honest_short, not an AVOID)."
            )
    env["expressability"] = {
        # The equity sleeve is long-only; a commodity leg is genuinely tradeable
        # (long or symmetric short) so the basket is not degraded by it.
        "symmetric": True,
        "degraded": False,
        "short_mode": short_mode,
        "notes": expr_notes,
    }
    env["scores"] = {
        "construction_alignment": _construction_alignment(headline_purity, wres),
        "basket_purity": round(headline_purity, 1),
        "alignment_kind": "basket_purity",
    }
    cost_note = "STT + slippage round-trip; cash-delivery equity basket."
    if commodity_block is not None:
        if commodity_block["instrument_type"] == "commodity_option":
            leg_bps = round(
                trading_costs.option_leg_bps("buy", segment="MCX-OPT") * 1e4, 1
            )
            cost_note += (
                f" Plus a defined-risk MCX option leg ({commodity_block['symbol']}) "
                f"— ~{leg_bps} bps of premium per leg at the MCX exchange rate."
            )
        else:
            cost_note += (
                f" Plus a LEVERAGED MCX future leg ({commodity_block['symbol']}) "
                "— SPAN+exposure margin + roll cost, sized on your confirmation."
            )
    env["costs"] = {
        "round_trip_bps": round(trading_costs.round_trip_bps(), 2),
        "note": cost_note,
    }
    env["warnings"] = warnings
    return dict(env)


# ════════════════════════════════════════════════════════════════════════════
# Helpers
# ════════════════════════════════════════════════════════════════════════════


def _resolve_universe(
    symbols: list[str] | None,
    theme_label: str,
    view: "MarketView",
) -> tuple[list[str], Optional["thematic_map.ThematicScenario"]]:
    """Resolve the candidate universe + an optional thematic scenario.

    Layered, never fabricated: explicit ``symbols`` → a recognised
    ``thematic_map`` scenario (winners) → ``sector_universe.query_screener`` for
    a known sector/theme. Returns ``([], None)`` when nothing resolves (caller
    degrades honestly rather than inventing tickers).
    """
    scenario = _detect_scenario(view, theme_label)
    if symbols:
        return [s.strip().upper() for s in symbols if s and s.strip()], scenario

    if scenario is not None:
        winners = [tk for tk, _why in scenario.winners]
        if winners:
            return winners, scenario

    sector = (
        sector_universe.normalize_sector(theme_label)
        or sector_universe.normalize_sector(view.category or "")
    )
    if sector is not None:
        rows = sector_universe.query_screener(sector=sector, limit=15)
        return [str(r["symbol"]) for r in rows], scenario

    mapping = sector_universe.resolve_theme(theme_label)
    if mapping is not None:
        out: list[str] = []
        for sec in mapping.sectors:
            out.extend(
                str(r["symbol"])
                for r in sector_universe.query_screener(sector=sec, limit=12)
            )
        return out, scenario

    return [], scenario


def _detect_scenario(
    view: "MarketView",
    theme_label: str,
) -> Optional["thematic_map.ThematicScenario"]:
    """Try to recognise a ``thematic_map`` scenario from the view text."""
    probe = " ".join(
        p for p in (theme_label, view.title, view.thesis) if p
    )
    # ``detect_thematic_scenario`` requires a positioning verb; add one so a
    # curated theme view (which reads as a thesis, not a command) still matches.
    return thematic_map.detect_thematic_scenario(f"build me something for {probe}")


def _scheme_for(knobs: Any, archetype: "Archetype") -> str:
    """Pick the weighting scheme: tier ``basket_concentration`` overrides the
    archetype ``template_or_scheme``; fall back to ``equal`` if neither is a real
    weighting scheme."""
    conc = knobs.basket_concentration
    if conc and conc in _CONCENTRATION_TO_SCHEME:
        return _CONCENTRATION_TO_SCHEME[conc]
    tos = archetype.template_or_scheme
    if tos in _VALID_SCHEMES:
        return str(tos)
    return "equal"


def _composite_factors(archetype: "Archetype") -> tuple[str, ...]:
    """The factor list for the T2 composite tilt (from archetype params)."""
    raw = archetype.params.get("composite")
    if isinstance(raw, (list, tuple)) and raw:
        return tuple(str(f) for f in raw)
    return _DEFAULT_FACTORS


def _price_history(symbols: list[str]) -> dict[str, Any]:
    """Fetch Close history for the basket (Kite primary, yfinance fallback).

    Returns ``{}`` honestly on a data failure — the covariance schemes then fall
    back to equal-weight via ``weighting`` and the reason is surfaced.
    """
    try:
        return dict(historical.get_close_dict(symbols, period=_PRICE_PERIOD))
    except Exception:  # pragma: no cover - data layer failure → honest empty
        return {}


def _mcap_map(symbols: list[str]) -> dict[str, float]:
    """Approximate ₹-crore market caps for the basket from ``sector_universe``.

    Free-float mcap is a GAP (flagged by the lead); these are approximate
    full-cap snapshots, used only for the relative mcap gradient.
    """
    wanted = set(symbols)
    out: dict[str, float] = {}
    for row in sector_universe.query_screener(limit=500):
        sym = str(row["symbol"])
        if sym in wanted:
            out[sym] = float(row["mcap_cr"])
    return out


def _purity_scale(
    weights: dict[str, float],
    purities: dict[str, "screens.PurityResult"],
) -> dict[str, float]:
    """Multiply each raw weight by its purity score and renormalise (spec §4.3).

    The "cheapest defensible upgrade over equal weight". Falls back to the raw
    weights if every purity is zero (never returns an all-zero vector).
    """
    scaled = {
        s: max(w, 0.0) * max(purities[s].score, 0.0) / 100.0
        for s, w in weights.items()
    }
    total = sum(scaled.values())
    if total <= 0.0:
        return dict(weights)
    return {s: v / total for s, v in scaled.items()}


def _avoid_legs(
    scenario: Optional["thematic_map.ThematicScenario"],
    survivors: list[str],
) -> list[dict[str, Any]]:
    """Render the scenario losers as AVOID annotations (never a short leg).

    Routes each loser through ``honest_short.avoid_annotation`` so the
    underperform leg is honestly an AVOID/underweight, not a fabricated delivery
    short (spec §3.3-#2 / §1.6).
    """
    if scenario is None:
        return []
    out: list[dict[str, Any]] = []
    survivor_set = set(survivors)
    for tk, why in scenario.losers:
        if tk in survivor_set:
            continue  # a name we actually hold can't also be an AVOID
        leg = honest_short.avoid_annotation(tk, reason=why)
        out.append(
            {
                "symbol": leg.symbol,
                "mode": leg.mode,
                "tradeable": leg.tradeable,
                "degraded": leg.degraded,
                "note": leg.note,
            }
        )
    return out


def _merger_arb(
    archetype: "Archetype",
    ctx: dict[str, Any],
    warnings: list[str],
) -> Optional[dict[str, Any]]:
    """E7 open-offer / buyback arb economics when the deal inputs are supplied.

    Long-only (acquirer-short is out of scope in India). Returns ``None`` (with a
    surfaced warning) when the required prices are absent — never fabricated.
    """
    if archetype.key != "E7_merger_arb":
        return None
    target = ctx.get("target_price")
    offer = ctx.get("offer_price")
    days = ctx.get("days_to_close")
    if target is None or offer is None or days is None:
        warnings.append(
            "Merger-arb economics need target_price / offer_price / days_to_close "
            "— omitted until the deal terms are supplied (no fabricated spread)."
        )
        return None
    m = merger_arb.merger_arb_metrics(
        target_price=float(target),
        offer_price=float(offer),
        days_to_close=int(days),
        broken_price=ctx.get("broken_price"),
        acceptance_ratio=ctx.get("acceptance_ratio"),
    )
    return {
        "spread_abs": m.spread_abs,
        "spread_pct": m.spread_pct,
        "gross_return_pct": m.gross_return_pct,
        "annualized_return_pct": m.annualized_return_pct,
        "implied_break_prob": m.implied_break_prob,
        "prorated_return_pct": m.prorated_return_pct,
        "note": m.note,
    }


# ── Commodity (MCX) helpers ───────────────────────────────────────────────────

# crude-intent tokens → the sector_universe beneficiary resolver.
_CRUDE_UP_TOKENS: frozenset[str] = frozenset(
    {"crude_up", "crude_rises", "crude_rise", "up", "rising", "long_crude"}
)
_CRUDE_DOWN_TOKENS: frozenset[str] = frozenset(
    {"crude_down", "crude_falls", "crude_fall", "down", "falling", "short_crude"}
)


def _commodity_producer_universe(
    archetype: "Archetype",
    ctx: dict[str, Any],
) -> Optional[list[str]]:
    """Resolve the equity sleeve from the crude beneficiary universe, or ``None``.

    A "commodity-producer basket" is a screened equity basket whose universe is
    the crude winners/losers — reused from ``sector_universe`` (never fabricated).
    ``crude_up`` → upstream producers (ONGC/OINL …); ``crude_down`` → refiners /
    OMCs. Only consulted when no explicit symbols were supplied.
    """
    intent = str(
        ctx.get("crude_intent") or archetype.params.get("crude_intent") or ""
    ).strip().lower()
    if intent in _CRUDE_UP_TOKENS:
        return sector_universe.crude_up_beneficiaries() or None
    if intent in _CRUDE_DOWN_TOKENS:
        return sector_universe.crude_down_beneficiaries() or None
    return None


def _commodity_leg_symbol(
    archetype: "Archetype",
    ctx: dict[str, Any],
) -> Optional[str]:
    """Resolve the DIRECT MCX commodity underlying for the leg, or ``None``.

    Honours an explicit ctx symbol first, then the archetype's
    ``direct_crude_leg`` / ``direct_mcx_sleeve`` / ``default_underlying`` params,
    and normalises via ``commodities.normalize_commodity`` (returns the DIRECT MCX
    symbol, never an ETF proxy). ``None`` when nothing resolves to a listed MCX
    commodity — the caller degrades honestly rather than fabricating a contract.
    """
    raw = (
        ctx.get("commodity_symbol")
        or ctx.get("commodity_leg")
        or ctx.get("direct_crude_leg")
        or archetype.params.get("direct_crude_leg")
        or archetype.params.get("direct_mcx_sleeve")
        or archetype.params.get("default_underlying")
    )
    if not raw:
        return None
    return commodities.normalize_commodity(str(raw))


def _commodity_leg(
    db: "Session",
    archetype: "Archetype",
    tier: str,
    ctx: dict[str, Any],
    warnings: list[str],
) -> tuple[Optional["InstrumentSpec"], Optional[dict[str, Any]]]:
    """Build the optional DIRECT MCX commodity leg (future / defined-risk option).

    The leveraged crude / bullion leg that sits alongside the equity defensive
    sleeve in a crude-shock-hedged (CM6) or commodity-flagged basket. Returns
    ``(InstrumentSpec, structure_block)`` or ``(None, None)`` when no commodity
    leg is requested / resolves.

    Vehicle rule (never auto-sized; ``commodities.LEVERAGE_NOTE`` always carried):

    * **long** → a DEFINED-RISK long MCX option (Conservative tier or an explicit
      ``commodity_defined_risk``) routed to the option-bearing sibling
      (``commodities.options_underlying``), else a leveraged MCX future.
    * **short** → ``honest_short.short_leg_for(is_commodity=True)`` → a TRADEABLE
      MCX future (the clean symmetric short) or a defined-risk MCX put — NEVER an
      AVOID or a fabricated delivery short (commodities are symmetrically
      shortable). Only a symbol confirmed off the MCX master degrades.

    Never fabricates a price or a lot: ``lot_size`` is the instrument master's or
    ``None``; the direct-MCX leg is flagged ``backtest_available=False`` (the
    basket data layer has no aligned direct-MCX OHLCV) while the equity sleeve
    still backtests.
    """
    wants_commodity = bool(archetype.params.get("commodity")) or bool(
        ctx.get("commodity_symbol")
        or ctx.get("commodity_leg")
        or ctx.get("direct_crude_leg")
    )
    if not wants_commodity:
        return None, None

    symbol = _commodity_leg_symbol(archetype, ctx)
    if symbol is None:
        warnings.append(
            "A commodity leg was requested but the underlying did not resolve to a "
            "listed MCX commodity — omitted rather than fabricating a contract."
        )
        return None, None

    direction = str(ctx.get("commodity_direction", "long")).strip().lower()
    prefer_defined_risk = bool(
        ctx.get("commodity_defined_risk", tier == "conservative")
    )
    lev_note = commodities.leverage_note(symbol)
    lot = commodities.lot_size(db, symbol)
    group = commodities.commodity_group(symbol)

    if direction == "short":
        leg = honest_short.short_leg_for(
            symbol, is_commodity=True, prefer_defined_risk=prefer_defined_risk
        )
        role = "short"
        tradeable = leg.tradeable
        mode = leg.mode
        if mode == "commodity_put":
            itype, segment, vehicle = "commodity_option", "MCX-OPT", "put"
            chain = commodities.options_underlying(symbol) or symbol
            instrument = f"{chain} PE"
        elif mode == "commodity_future":
            itype, segment, vehicle = "commodity_future", "MCX-FUT", "future"
            instrument = leg.instrument
        else:  # confirmed off the MCX master → honest AVOID (no fabricated short)
            itype, segment, vehicle = "commodity_future", "MCX-FUT", "avoid"
            instrument = leg.instrument
        leg_note = leg.note
    else:
        role, tradeable = "long", True
        if prefer_defined_risk:
            itype, segment, vehicle, mode = (
                "commodity_option", "MCX-OPT", "option", "long_option",
            )
            chain = commodities.options_underlying(symbol) or symbol
            instrument = f"{chain} CE"
            leg_note = (
                "Defined-risk long MCX call (premium-capped downside) — the "
                "Conservative commodity vehicle."
            )
        else:
            itype, segment, vehicle, mode = (
                "commodity_future", "MCX-FUT", "future", "long_future",
            )
            instrument = f"{symbol} FUT"
            leg_note = (
                "Leveraged long MCX future — the directional commodity vehicle "
                "(margin-based, symmetric)."
            )

    backtest_available = commodities.price_history_available(symbol)
    if not backtest_available:
        warnings.append(
            f"Direct MCX {symbol} leg has no aligned daily OHLCV in the basket "
            "data layer — the commodity leg is backtest-unavailable (construct-"
            "only); the equity sleeve still backtests. No price/cointegration "
            "fabricated."
        )
    if lev_note not in warnings:
        warnings.append(lev_note)
    if lot is None:
        warnings.append(
            f"Lot size for {symbol} not in the MCX instrument master — confirm the "
            "lot in your broker before arming (not fabricated)."
        )

    spec: "InstrumentSpec" = InstrumentSpec(
        symbol=instrument,
        exchange="MCX",
        segment=segment,
        instrument_type=itype,
        role=role,  # type: ignore[typeddict-item]
        tradeable=tradeable,
        note=f"{leg_note} {lev_note}",
    )
    block: dict[str, Any] = {
        "symbol": symbol,
        "group": group,
        "direction": role,
        "vehicle": vehicle,
        "mode": mode,
        "instrument": instrument,
        "exchange": "MCX",
        "segment": segment,
        "instrument_type": itype,
        "lot_size": lot,
        "tradeable": tradeable,
        "defined_risk": itype == "commodity_option",
        "backtest_available": backtest_available,
        "leverage_note": lev_note,
        "note": leg_note,
    }
    return spec, block


def _construction_alignment(headline_purity: float, wres: Any) -> float:
    """Construction-time alignment ceiling (0..100), NOT the Phase-4 Trust score.

    Anchored on the headline Basket Purity, penalised when the requested
    weighting scheme degraded to equal-weight (less conviction expressed).
    """
    base = max(0.0, min(100.0, float(headline_purity)))
    if wres.fallback_reason:
        base *= 0.7
    return round(base, 1)


def _refusal_envelope(
    env: dict[str, Any],
    knobs: Any,
    theme_label: str,
    cap: float,
    *,
    reason: str,
    etf_proxy: Optional[str] = None,
) -> dict[str, Any]:
    """Build the honest-degrade envelope when the basket can't be built.

    Either no investable universe, or ``min_names_floor`` refused → offer the
    listed ETF proxy as the (Conservative) single-holding expression instead of a
    fake-diversified basket. Never invents constituents.
    """
    proxy = etf_proxy or screens.THEME_ETF_PROXY.get(
        sector_universe.normalize_sector(theme_label) or theme_label.lower().strip()
    )
    structure: dict[str, Any] = {
        "scheme": "etf_proxy",
        "weights": {proxy: 1.0} if proxy else {},
        "basket_purity": None,
        "single_name_cap": cap,
        "min_names": screens.MIN_NAMES_DEFAULT,
        "n_names": 1 if proxy else 0,
        "etf_proxy": proxy,
        "fallback_reason": reason,
    }
    env["structure"] = structure
    if proxy:
        env["instruments"] = [
            InstrumentSpec(
                symbol=proxy,
                exchange="NSE",
                segment="ETF",
                instrument_type="etf",
                role="long",
                tradeable=True,
                note="listed ETF proxy — honest degrade for a too-thin theme",
            )
        ]
    env["expressability"] = {
        "symmetric": True,
        "degraded": True,
        "short_mode": None,
        "notes": [reason],
    }
    env["scores"] = {
        "construction_alignment": 0.0,
        "basket_purity": None,
        "alignment_kind": "basket_purity",
    }
    env["costs"] = {
        "round_trip_bps": round(trading_costs.round_trip_bps(), 2),
        "note": "single ETF holding — minimal turnover.",
    }
    env["warnings"].append(reason)
    return dict(env)


__all__ = ["build_basket_expression"]
