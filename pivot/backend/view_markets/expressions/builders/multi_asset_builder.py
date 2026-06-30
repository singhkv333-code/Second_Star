"""View Markets — Phase 3 MULTI_ASSET expression builder (equity + gold + hedge).

Handles ``expression_kind == "multi_asset"``: T4 multi-asset theme and E10
commodity/geopolitical-shock hedged basket. A multi-asset theme is just a 2–3
node allocation (theme-equity basket + gold ETF + optional Nifty put/collar),
risk-parity at the ASSET-CLASS level so the equity sleeve doesn't swamp gold's
risk contribution (spec §4.6).

MCX pass (CM5/CM6, 2026-06-29): when an archetype asks for one (or the caller
passes ``commodity_symbol=``), the builder ALSO adds a **DIRECT MCX commodity
sleeve** (gold/silver/crude via an MCX future or a defined-risk option) — the
LEVERAGED alternative to the listed gold-ETF route. The ETF route is KEPT; the
direct sleeve carries the SAME asset-class risk budget but as margin (0 capital
weight), is NEVER auto-sized (register-not-execute), always carries the
``commodities.LEVERAGE_NOTE``, and is marked backtest-unavailable (direct MCX
futures have no aligned daily OHLCV — the ETF proxy route backtests instead; we
never fabricate a return series). A bearish commodity leg is a TRADEABLE MCX
future/put resolved through ``honest_short`` — never a fabricated short or AVOID.

Delegates to the REAL engines (composes the existing builders, never reinvents):

  * ``backend.view_markets.expressions.builders.basket_builder.build_basket_expression``
    — the equity sleeve (screened, purity/conviction-weighted).
  * ``backend.services.weighting.compute_weights(..., scheme="risk_parity" |
    "min_variance", price_history=...)`` — applied at the ASSET-CLASS level over
    {equity_sleeve, gold_etf} return series so gold keeps a real risk budget.
  * ``backend.view_markets.expressions.builders.hedge_builder.build_hedge_expression``
    — the optional Nifty put / zero-cost collar sleeve.
  * Gold/silver ETF universe — ``GOLDBEES`` / ``SILVERBEES`` (Kite-tradeable;
    SGBs/physical/MCX are OUT of the chat-execution loop → offer the listed ETF
    only). Gold sleeve % comes from ``tiers.tier_knobs(...).gold_sleeve_pct``
    (Conservative 8–10% + collar/put, Balanced 5% + covered-call-financed put,
    Aggressive 2–3% pure tail-hedge + factor/optionized equity).
  * E10 energy-importer-vs-exporter pair via ``pair_builder`` + the
    ``sector_universe`` oil producer/refiner split (``crude_up_beneficiaries`` /
    ``crude_down_beneficiaries`` / ``oil_role``).

Persists to ``config.structure`` (``config_schema.STRUCTURE_KEYS["multi_asset"]``):
``asset_class_scheme`` and ``sleeves: [{kind: equity_basket|gold_etf|hedge,
weight, detail}]`` where each sleeve's ``detail`` is the nested envelope/structure
from its own builder. India-typed ``instruments`` aggregate across sleeves.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from backend.services import trading_costs, weighting
from backend.view_markets.expressions import (
    catalog,
    commodities,
    config_schema,
    honest_short,
)
from backend.view_markets.expressions.tiers import tier_knobs

if TYPE_CHECKING:  # pragma: no cover - typing only
    from sqlalchemy.orm import Session

    from backend.models import MarketView
    from backend.view_markets.expressions.catalog import Archetype

# ── India gold/silver ETF universe (Kite-tradeable; the ONLY gold vehicles the
# chat-execution loop allows — SGBs / physical / MCX are out of scope). ───────
_GOLD_ETF_ALLOWLIST: frozenset[str] = frozenset(
    {
        "GOLDBEES", "SETFGOLD", "GOLDSHARE", "HDFCGOLD", "AXISGOLD",
        "KOTAKGOLD", "ICICIGOLD", "GOLDIETF", "QGOLDHALF", "GOLDETF",
        "GOLD", "SILVERBEES", "SILVERIETF", "SILVERETF", "SILVER",
    }
)
# Tokens that mark a NON-chat-tradeable gold instrument (degrade → listed ETF).
_GOLD_BLOCKED_TOKENS: tuple[str, ...] = ("SGB", "MCX", "PHYSICAL", "BOND", "FUT")
_DEFAULT_GOLD_ETF = "GOLDBEES"

# The real catalog archetypes the multi-asset builder composes (NOT fabricated):
# the equity sleeve is the screened purity/conviction basket, the overlay is the
# index-level optionized hedge. Looked up by key so dispatch stays declarative.
_EQUITY_SLEEVE_ARCHETYPE = "T1_purity_conviction_basket"
_HEDGE_SLEEVE_ARCHETYPE = "T3_optionized_hedged"

# Key used for the synthetic "equity sleeve" asset in the asset-class weighting.
_EQUITY_ASSET_KEY = "EQUITY_SLEEVE"
# Asset-class sizing method label stored in ``structure.asset_class_scheme``.
_ASSET_CLASS_SCHEME = "risk_parity"

# ── DIRECT MCX commodity sleeve (the leveraged alternative to the gold-ETF route).
# A commodity sleeve is sized at the ASSET-CLASS level (it inherits the bullion
# risk budget the asset-class split already computed) but is expressed via a
# LEVERAGED MCX future/option — margin, not deployed capital — so it carries 0
# capital weight (its ``notional_target_weight`` records the risk budget), the
# ``commodities.LEVERAGE_NOTE``, and is NEVER auto-sized (register-not-execute).
_COMMODITY_SLEEVE_KIND = "direct_mcx_sleeve"


def _commodity_vehicle_for_tier(tier: str) -> str:
    """Map a tier to the direct-MCX vehicle (``"option"`` vs ``"future"``).

    Spec commodity per-tier convention: Conservative/Balanced lean defined-risk
    (a BUY option leg — loss capped at the premium), Aggressive takes the clean
    symmetric FUTURE leg. Lots are NEVER auto-sized either way.
    """
    return "future" if str(tier).lower() == "aggressive" else "option"


def _build_commodity_sleeve(
    db: "Session",
    symbol: str,
    *,
    tier: str,
    direction: str,
    notional_weight: float,
    expiry: Any = None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, list[str], dict[str, Any] | None]:
    """Resolve a DIRECT MCX commodity sleeve (or degrade honestly — never fabricate).

    Returns ``(sleeve, instrument, warnings, short_leg)``. When ``symbol`` is not a
    recognised MCX commodity the sleeve is ``None`` (with a warning) — we degrade to
    the listed-ETF route rather than invent a contract. A bearish leg is routed
    through ``honest_short.short_leg_for(is_commodity=True)`` so the short is a
    TRADEABLE MCX future/put, never a fabricated short or an AVOID. The leg is
    leveraged → the ``commodities.LEVERAGE_NOTE`` rides on the instrument + warnings,
    and the direct sleeve is marked ``backtest_available=False`` (the ETF proxy
    route is the backtestable alternative).
    """
    mcx_symbol = commodities.normalize_commodity(symbol)
    if mcx_symbol is None:
        return (
            None,
            None,
            [
                f"{symbol!r} is not a recognised MCX commodity — no direct MCX "
                "sleeve added (kept the listed gold/silver ETF route). Verify the "
                "symbol against the MCX instrument master; no contract fabricated."
            ],
            None,
        )

    group = commodities.commodity_group(mcx_symbol)
    opt_underlying = commodities.options_underlying(mcx_symbol)
    proxy = commodities.etf_proxy(mcx_symbol)
    backtest_ok = commodities.price_history_available(mcx_symbol)  # False = direct MCX
    lot = commodities.lot_size(db, mcx_symbol, expiry)  # None on master miss → honest
    leverage = commodities.leverage_note(mcx_symbol)
    vehicle = _commodity_vehicle_for_tier(tier)
    warnings: list[str] = []
    short_leg: dict[str, Any] | None = None

    if str(direction).lower() == "short":
        # Commodities ARE symmetrically shortable on MCX — route through
        # honest_short for a TRADEABLE short (future, or a defined-risk BUY put),
        # NEVER a fabricated short / AVOID for a listed F&O commodity.
        leg = honest_short.short_leg_for(
            mcx_symbol,
            is_commodity=True,
            prefer_defined_risk=(vehicle == "option"),
            fno_eligible=None,  # known MCX commodity → assume listed/tradeable
        )
        short_leg = {
            "symbol": leg.symbol,
            "mode": leg.mode,
            "instrument": leg.instrument,
            "tradeable": leg.tradeable,
            "degraded": leg.degraded,
            "note": leg.note,
        }
        warnings.extend(leg.warnings)
        role = "short"
        leg_tradeable = leg.tradeable
        if leg.mode == "commodity_put":  # defined-risk BUY put
            instrument_type, segment = "commodity_option", "MCX-OPT"
            leg_symbol = opt_underlying or mcx_symbol
        else:  # commodity_future — the clean symmetric short
            instrument_type, segment = "commodity_future", "MCX-FUT"
            leg_symbol = mcx_symbol
        instr_note = leg.note
    else:
        # LONG: a BUY option leg (loss capped at premium = defined-risk) for the
        # option vehicle, else the symmetric MCX future. Strikes/premia resolve at
        # arm time — no fabricated numbers reach the envelope.
        role = "long"
        leg_tradeable = True
        if vehicle == "option" and opt_underlying is not None:
            instrument_type, segment = "commodity_option", "MCX-OPT"
            leg_symbol = opt_underlying
            instr_note = (
                "Long MCX commodity option (BUY debit — loss capped at the premium, "
                "defined-risk). " + leverage
            )
        else:
            instrument_type, segment = "commodity_future", "MCX-FUT"
            leg_symbol = mcx_symbol
            instr_note = leverage

    instrument: dict[str, Any] = {
        "symbol": leg_symbol,
        "exchange": "MCX",
        "segment": segment,
        "instrument_type": instrument_type,
        "role": role,
        "tradeable": bool(leg_tradeable),
        "note": instr_note,
    }

    # Surface the leverage note + the honest data/lot gates (never fabricated).
    warnings.append(leverage)
    if not backtest_ok:
        proxy_txt = f" — backtest the {proxy} ETF-proxy route instead" if proxy else ""
        warnings.append(
            f"Direct MCX {mcx_symbol} has no aligned daily OHLCV in the "
            f"pairs/basket data layer → the direct sleeve is backtest-unavailable"
            f"{proxy_txt}; no return series / cointegration fabricated."
        )
    if lot is None:
        warnings.append(
            f"Lot size for {mcx_symbol} is not in the instrument master — confirm "
            "the MCX lot in your broker app before arming (no lot fabricated)."
        )

    sleeve: dict[str, Any] = {
        "kind": _COMMODITY_SLEEVE_KIND,
        # A leveraged MARGIN leg, not a capital sleeve → 0 capital weight; it
        # carries the SAME bullion/commodity risk budget as the ETF route, recorded
        # as ``notional_target_weight``.
        "weight": 0.0,
        "detail": {
            "symbol": leg_symbol,
            "mcx_symbol": mcx_symbol,
            "group": group,
            "direction": role,
            "vehicle": vehicle,
            "instrument_type": instrument_type,
            "segment": segment,
            "exchange": "MCX",
            "route": "direct_mcx",
            "alternative_to": "gold_etf",
            "etf_proxy_route": proxy,           # the backtestable alternative leg
            "backtest_available": backtest_ok,  # False for a direct MCX future/option
            "notional_target_weight": round(float(notional_weight), 4),
            "lot_size": lot,                    # None on master miss — never fabricated
            "auto_sized": False,                # register-not-execute; user confirms lots
            "leverage_note": leverage,
            "short_leg": short_leg,
            "rationale": (
                f"Direct MCX {group or 'commodity'} exposure — the LEVERAGED "
                "alternative to the listed-ETF route, expressing the same "
                "asset-class risk budget via an MCX "
                + ("option (defined-risk)" if instrument_type == "commodity_option" else "future")
                + ". Leveraged, never auto-sized (register-not-execute)."
            ),
        },
    }
    return sleeve, instrument, warnings, short_leg


def _resolve_gold_etf(symbol: str | None) -> tuple[str, str | None]:
    """Return a (listed_gold_etf, warning) tuple — never a fabricated instrument.

    SGB / physical / MCX gold are out of the chat-execution loop; if the caller
    asks for one, degrade honestly to the listed Gold ETF and say so rather than
    pretending we can route it.
    """
    raw = (symbol or _DEFAULT_GOLD_ETF).strip()
    upper = raw.upper()
    if not upper:
        return _DEFAULT_GOLD_ETF, None
    if any(tok in upper for tok in _GOLD_BLOCKED_TOKENS):
        return (
            _DEFAULT_GOLD_ETF,
            f"{raw} is not a chat-tradeable gold instrument "
            "(SGB / physical / MCX are out of the execution loop) — sized the "
            f"gold sleeve via the listed Gold ETF {_DEFAULT_GOLD_ETF} instead.",
        )
    # Any other ticker is accepted as a listed ETF reference (no number invented);
    # off-allowlist names are flagged so the card can confirm the instrument.
    if upper not in _GOLD_ETF_ALLOWLIST:
        return (
            upper,
            f"{raw} is treated as a listed gold/silver ETF — confirm it is "
            "Kite-tradeable (not an SGB/physical/MCX gold vehicle).",
        )
    return upper, None


def _asset_class_split(
    tier_knob: Any,
    gold_symbol: str,
    ctx: dict[str, Any],
) -> tuple[float, float, str, str]:
    """Size the equity vs gold sleeves at the ASSET-CLASS level.

    When the caller supplies per-sleeve return history
    (``ctx["asset_class_price_history"] = {EQUITY_SLEEVE: series, gold: series}``)
    we run ``weighting.compute_weights(..., "risk_parity")`` so gold keeps a real
    risk budget and the equity sleeve cannot swamp it. Without that history we do
    NOT fabricate a covariance — we size gold to the tier target
    (``gold_sleeve_pct``) and say so. Returns ``(w_equity, w_gold, scheme, note)``.
    """
    gold_target = tier_knob.gold_sleeve_pct or 0.05
    history = ctx.get("asset_class_price_history")
    if isinstance(history, dict) and _EQUITY_ASSET_KEY in history and gold_symbol in history:
        try:
            weights = weighting.compute_weights(
                [_EQUITY_ASSET_KEY, gold_symbol],
                _ASSET_CLASS_SCHEME,
                price_history=history,
            )
            w_eq = float(weights.get(_EQUITY_ASSET_KEY, 0.0))
            w_gold = float(weights.get(gold_symbol, 0.0))
            total = w_eq + w_gold
            if total > 0:
                w_eq, w_gold = w_eq / total, w_gold / total
                return (
                    w_eq,
                    w_gold,
                    _ASSET_CLASS_SCHEME,
                    "Asset-class risk-parity over the equity sleeve vs gold-ETF "
                    "return series (gold keeps an equal-risk budget).",
                )
        except Exception:  # noqa: BLE001 - degrade honestly, never fabricate
            pass
    w_gold = float(gold_target)
    w_eq = max(0.0, 1.0 - w_gold)
    return (
        w_eq,
        w_gold,
        "tier_target",
        "Asset-class risk-parity needs per-sleeve return history; sized the gold "
        f"sleeve to the tier target ({w_gold:.0%}) — no covariance fabricated.",
    )


def build_multi_asset_expression(
    db: "Session",
    view: "MarketView",
    archetype: "Archetype",
    tier: str,
    *,
    symbols: list[str] | None = None,
    theme: str | None = None,
    gold_symbol: str = "GOLDBEES",
    **ctx: Any,
) -> dict[str, Any]:
    """Build an equity + gold-ETF (+ hedge) multi-asset expression.

    Builds the equity sleeve via ``build_basket_expression``, sizes the gold
    sleeve from the tier ``gold_sleeve_pct``, runs asset-class-level risk-parity
    so gold keeps a real risk budget, and optionally attaches a Nifty put/collar
    sleeve via ``build_hedge_expression`` (E10's standing pre-armed hedge). Only
    listed gold/silver ETFs (no SGB/physical/MCX). Returns a ``config_schema``
    envelope with the ``sleeves`` structure.
    """
    # Lazy sibling imports: avoids any builders-package import cycle and lets the
    # tests monkeypatch the sub-builders at their source modules.
    from backend.view_markets.expressions.builders.basket_builder import (
        build_basket_expression,
    )
    from backend.view_markets.expressions.builders.hedge_builder import (
        build_hedge_expression,
    )

    knob = tier_knobs("multi_asset", tier)
    env = config_schema.base_envelope(
        archetype=archetype.key,
        expression_kind="multi_asset",
        tier=tier,
        label=archetype.label,
    )
    warnings: list[str] = []

    # ── 1. Equity sleeve (the screened, purity/conviction-weighted basket) ──
    equity_archetype = catalog.get_archetype(_EQUITY_SLEEVE_ARCHETYPE) or archetype
    equity_env = build_basket_expression(
        db,
        view,
        equity_archetype,
        tier,
        symbols=symbols,
        theme=theme,
        total_inr=ctx.get("total_inr"),
    )
    equity_struct = equity_env.get("structure", {}) if isinstance(equity_env, dict) else {}
    equity_expr = equity_env.get("expressability", {}) if isinstance(equity_env, dict) else {}
    warnings.extend(equity_env.get("warnings", []) if isinstance(equity_env, dict) else [])

    # ── 2. Gold sleeve (listed ETF only — no SGB/physical/MCX) ──────────────
    gold_etf, gold_warn = _resolve_gold_etf(gold_symbol)
    if gold_warn:
        warnings.append(gold_warn)

    # ── 3. Asset-class-level sizing (risk-parity so gold isn't swamped) ─────
    w_eq, w_gold, asset_class_scheme, split_note = _asset_class_split(knob, gold_etf, ctx)
    warnings.append(split_note)

    # ── 4. Optional index-level hedge overlay (Nifty put / collar) ──────────
    include_hedge = ctx.get("include_hedge", True)
    hedge_index = ctx.get("hedge_underlying", "NIFTY")
    hedge_env: dict[str, Any] | None = None
    if include_hedge:
        hedge_archetype = catalog.get_archetype(_HEDGE_SLEEVE_ARCHETYPE) or archetype
        try:
            hedge_env = build_hedge_expression(
                db,
                view,
                hedge_archetype,
                tier,
                underlying=hedge_index,
                expiry=ctx.get("expiry"),
                qty_lots=int(ctx.get("qty_lots", 1)),
                horizon_days=ctx.get("horizon_days"),
            )
        except Exception as exc:  # noqa: BLE001 - hedge is optional; degrade honestly
            hedge_env = None
            warnings.append(
                f"Index hedge sleeve unavailable ({hedge_index}: {exc}); the long "
                "sleeves stand un-hedged — re-arm the collar when the chain is fresh."
            )

    # ── 5. Assemble sleeves + India-typed instruments ───────────────────────
    sleeves: list[dict[str, Any]] = [
        {
            "kind": "equity_basket",
            "weight": round(w_eq, 4),
            "detail": {
                "label": equity_env.get("label") if isinstance(equity_env, dict) else None,
                "structure": equity_struct,
                "scores": equity_env.get("scores", {}) if isinstance(equity_env, dict) else {},
            },
        },
        {
            "kind": "gold_etf",
            "weight": round(w_gold, 4),
            "detail": {
                "symbol": gold_etf,
                "instrument_type": "gold_etf",
                "rationale": (
                    "Gold is India's canonical listed diversifier — it rises in "
                    "equity drawdowns and (with bond-equity correlation positive "
                    "post-2022) carries the diversification load for retail."
                ),
            },
        },
    ]

    instruments: list[dict[str, Any]] = []
    if isinstance(equity_env, dict):
        instruments.extend(equity_env.get("instruments", []))
    instruments.append(
        {
            "symbol": gold_etf,
            "exchange": "NSE",
            "segment": "ETF",
            "instrument_type": "gold_etf",
            "role": "long",
            "tradeable": True,
            "note": "Listed gold/silver ETF (no SGB / physical / MCX).",
        }
    )

    # ── 3b. Optional DIRECT MCX commodity sleeve (the leveraged alt to the ETF) ──
    # Triggered by an explicit ``commodity_symbol=`` or a CM5/CM6 archetype param
    # (``direct_mcx_sleeve`` / ``direct_crude_leg``). The ETF sleeve above is KEPT;
    # this adds the leveraged route carrying the same asset-class risk budget. A
    # bearish leg routes through honest_short (a tradeable MCX future/put).
    commodity_symbol = ctx.get("commodity_symbol")
    if commodity_symbol is None:
        params = getattr(archetype, "params", {}) or {}
        commodity_symbol = params.get("direct_mcx_sleeve") or params.get("direct_crude_leg")
    commodity_direction = str(ctx.get("commodity_direction", "long")).lower()
    commodity_short_mode: str | None = None
    commodity_short_degraded = False
    commodity_present = False
    if commodity_symbol:
        c_sleeve, c_instr, c_warn, c_short = _build_commodity_sleeve(
            db,
            str(commodity_symbol),
            tier=tier,
            direction=commodity_direction,
            notional_weight=w_gold,
            expiry=ctx.get("expiry"),
        )
        warnings.extend(c_warn)
        if c_sleeve is not None:
            commodity_present = True
            sleeves.append(c_sleeve)
            # The commodity leg must carry an MCX segment (the leverage convention);
            # this is the contract config_schema.is_commodity_segment enforces.
            if c_instr is not None and config_schema.is_commodity_segment(
                c_instr.get("segment")
            ):
                instruments.append(c_instr)
            if c_short is not None:
                commodity_short_mode = c_short.get("mode")
                commodity_short_degraded = bool(c_short.get("degraded"))

    if hedge_env is not None:
        sleeves.append(
            {
                "kind": "hedge",
                # The hedge is a premium-financed OVERLAY, not a long-capital
                # sleeve — it does not dilute the equity/gold split.
                "weight": 0.0,
                "detail": {
                    "label": hedge_env.get("label"),
                    "overlay": True,
                    "underlying_index": hedge_env.get("structure", {}).get(
                        "underlying_index", hedge_index
                    ),
                    "structure": hedge_env.get("structure", {}),
                },
            }
        )
        instruments.extend(hedge_env.get("instruments", []))
        warnings.extend(hedge_env.get("warnings", []))

    env["instruments"] = instruments
    env["structure"] = {
        "asset_class_scheme": asset_class_scheme,
        "sleeves": sleeves,
    }

    # ── 6. Expressability — the equity/gold legs are long + a long protective
    # hedge; the optional DIRECT MCX sleeve adds a leveraged commodity leg (a
    # TRADEABLE symmetric short via honest_short when bearish). No fabricated
    # single-name delivery short anywhere.
    equity_degraded = bool(equity_expr.get("degraded", False))
    expr_notes: list[str] = []
    if commodity_present and commodity_short_mode:
        expr_notes.append(
            "Direct MCX commodity sleeve carries a TRADEABLE symmetric short "
            f"({commodity_short_mode}) resolved through honest_short — a real MCX "
            "future/put, never a fabricated delivery short or an AVOID. Leveraged "
            "(leverage note attached), never auto-sized (register-not-execute)."
        )
    elif commodity_present:
        expr_notes.append(
            "Direct MCX commodity sleeve is a LONG leveraged leg alongside the "
            "listed-ETF route (the same asset-class risk budget) — leverage note "
            "attached, never auto-sized (register-not-execute). Direct MCX legs "
            "are backtest-unavailable; the ETF proxy route backtests."
        )
    else:
        expr_notes.append(
            "Long-only across asset classes (equity basket + gold ETF) with a "
            "defined-risk index hedge overlay — no single-name delivery short, "
            "no fabricated instrument."
        )
    expr_notes.extend(equity_expr.get("notes", []))
    env["expressability"] = {
        "symmetric": True,
        "degraded": equity_degraded or commodity_short_degraded,
        "short_mode": commodity_short_mode,
        "notes": expr_notes,
    }

    # ── 7. Construction-time scores (Phase 4 attaches the Trust verdict) ────
    basket_purity = equity_struct.get("basket_purity")
    construction_alignment = 60.0
    if asset_class_scheme == _ASSET_CLASS_SCHEME:
        construction_alignment += 12.0  # real asset-class risk-parity ran
    if hedge_env is not None:
        construction_alignment += 8.0  # defended index hedge attached
    if not equity_degraded:
        construction_alignment += 5.0
    env["scores"] = {
        "construction_alignment": min(construction_alignment, 100.0),
        "basket_purity": basket_purity,
        "alignment_kind": "basket_purity",
    }

    env["costs"] = {
        "round_trip_bps": round(trading_costs.round_trip_bps(), 2),
        "note": (
            "Round-trip equity charges apply to the equity basket + gold-ETF "
            "sleeves; the option hedge legs carry NFO premium charges (see the "
            "hedge sleeve)."
        ),
    }
    if commodity_present:
        env["costs"]["commodity_note"] = (
            "The direct MCX commodity sleeve trades on SPAN+exposure MARGIN "
            "(leveraged) with MCX exchange charges — sized by the user in-broker, "
            "never auto-sized."
        )
    env["warnings"] = warnings
    return env


__all__ = ["build_multi_asset_expression"]
