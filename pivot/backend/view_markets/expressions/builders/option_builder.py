"""View Markets — Phase 3 OPTION expression builder (defined-risk first).

Handles every ``expression_kind == "option_strategy"`` archetype: E1 rate
debit-spread, E3 event straddle/strangle, E4 IV-crush iron-fly/condor, E6
broken-wing, R5 relative-options (two-underlying vertical pair), and the MCX
commodity directional / event-straddle archetypes (CM1/CM2).

Commodities (MCX) became tradeable via register-not-execute on 2026-06-29. A
commodity underlying (CRUDEOIL/GOLD/SILVER/COPPER…) resolves through the SAME
``resolve_strategy`` path (it already handles MCX); the builder routes a mini to
its option-bearing sibling (``commodities.options_underlying``), India-types each
leg as ``commodity_option`` / ``MCX-OPT`` / ``MCX``, costs through the MCX
exchange rate, and stamps the LEVERAGE note (``commodities.LEVERAGE_NOTE``) on the
warnings + structure — commodities are leveraged and are NEVER auto-sized. A
bearish commodity directional view flips to the defined-risk bearish template
(``params["alt_bearish_template"]``); both stay defined-risk (max_loss ``None`` is
still rejected).

Delegates to the REAL option engine — never reinvents payoff/greeks/POP/margin:

  * ``backend.services.option_strategies.resolve_strategy(db, underlying,
    template_name, *, expiry=None, qty_lots=1, explicit_legs=None, chain=None)
    -> dict`` — the full ``option_strategy_card`` payload (``locked`` /
    ``editable`` / ``computed{net_premium,max_loss,max_profit,pop,breakevens,
    net_greeks,capital_required,margin_estimate,payoff}`` / ``critique`` /
    ``validation``). ``max_loss``/``max_profit`` ``None`` = UNLIMITED — the
    builder REJECTS an un-defined-risk structure (spec §1.5 defined-risk first).
  * ``backend.services.option_strategies.TEMPLATES`` — the template registry; the
    archetype's ``template_or_scheme`` is a key into it (``bull_call_spread`` …).
    ``broken_wing_butterfly`` (E6) is a GAP template → the builder composes
    explicit legs and calls ``resolve_strategy(..., explicit_legs=[...])``.
  * ``backend.view_markets.implied_move.implied_move(db, underlying, *,
    expiry=None, horizon_days=None) -> ImpliedMove | None`` — the priced-in
    expected move sizing the strikes/horizon to the event.

Tier knobs (``tiers.tier_knobs("option_strategy", tier)``) pick the moneyness /
leg count / timing: Conservative = OTM credit wing, Balanced = ATM spread / iron
fly, Aggressive = ATM debit / straddle / ratio (with ``size_cut`` on
high-uncertainty pre-position events).

India guards: weeklies only on NIFTY/SENSEX (BANKNIFTY monthly) and the
single-stock-option STT-on-intrinsic + physical-settlement warning
(``honest_short.SINGLE_STOCK_OPTION_WARNING``) stamped on every single-name leg.
Index-level hedges only for thin single-stock names (defer to ``hedge_builder``).

Persists to ``config.structure`` the option payload subset
(``config_schema.STRUCTURE_KEYS["option_strategy"]``) + ``instruments`` (each leg
India-typed) + ``timing`` + ``scores.construction_alignment`` (event-study
alignment for EVENT views; relative-value for R5).
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

from backend.services import option_strategies as _opt
from backend.view_markets import implied_move as _im
from backend.view_markets.expressions import (
    commodities,
    config_schema,
    honest_short,
    tiers,
)
from backend.view_markets.expressions.catalog import RELATIVE

if TYPE_CHECKING:  # pragma: no cover - typing only
    from sqlalchemy.orm import Session

    from backend.models import MarketView
    from backend.view_markets.expressions.catalog import Archetype


# Underlyings we treat as an INDEX (NFO index options) vs a single stock. Sourced
# from the honest-short microstructure tables (data, not fabricated).
_INDEX_UNDERLYINGS: frozenset[str] = (
    honest_short.WEEKLY_INDICES
    | honest_short.MONTHLY_ONLY_INDICES
    | honest_short.SHORTABLE_INDEX_FUTURES
    | frozenset({"FINNIFTY", "MIDCPNIFTY", "BANKEX"})
)

# Per-(archetype, tier) TEMPLATES key. The tier moneyness knob (§5: OTM credit
# wing / ATM spread / ATM debit) is realised here as a concrete template, never a
# naked structure (defined-risk first). Archetypes not listed fall back to the
# catalog ``template_or_scheme``. E6 (broken-wing) and R5 (two-underlying) are
# composed specially below.
_TIER_TEMPLATE: dict[str, dict[str, str]] = {
    "E1_rate_debit_spread": {
        tiers.CONSERVATIVE: "bull_put_spread",   # OTM credit wing, defined risk
        tiers.BALANCED: "bull_call_spread",      # ATM debit vertical
        tiers.AGGRESSIVE: "bull_call_spread",    # ATM debit (size_cut applied)
    },
    "E3_event_straddle": {
        tiers.CONSERVATIVE: "long_strangle",     # cheaper OTM wings
        tiers.BALANCED: "long_straddle",         # ATM long vol
        tiers.AGGRESSIVE: "long_straddle",
    },
    "E4_iv_crush_harvest": {
        tiers.CONSERVATIVE: "iron_condor",       # wide OTM, defined risk
        tiers.BALANCED: "iron_butterfly",        # ATM, bigger credit
        tiers.AGGRESSIVE: "iron_butterfly",
    },
}


# Direction tokens that flip a commodity directional structure to its bearish
# (still defined-risk) template. Anything else (incl. ``None``) stays bullish.
_BEARISH_DIRECTIONS: frozenset[str] = frozenset(
    {"bearish", "down", "short", "sell", "fall", "decline"}
)


def _commodity_template(
    archetype: "Archetype", tier: str, direction: Optional[str],
) -> Optional[str]:
    """The commodity-aware template override for CM1/CM2, or ``None``.

    CM1 (directional) flips to ``alt_bearish_template`` on a bearish view; CM2
    (event straddle) drops to its ``alt_template`` (cheaper OTM strangle) on the
    Conservative tier. Both alternatives are themselves defined-risk; the engine
    still recomputes max_loss and the builder still rejects an unbounded one.
    Non-commodity archetypes return ``None`` (the caller falls back to the
    catalog ``template_or_scheme``).
    """
    params = archetype.params
    if not params.get("commodity"):
        return None
    alt_bearish = params.get("alt_bearish_template")
    if alt_bearish and (direction or "").strip().lower() in _BEARISH_DIRECTIONS:
        return str(alt_bearish)
    alt_template = params.get("alt_template")
    if alt_template and tier == tiers.CONSERVATIVE:
        return str(alt_template)
    return None


def build_option_expression(
    db: "Session",
    view: "MarketView",
    archetype: "Archetype",
    tier: str,
    *,
    underlying: str | None = None,
    expiry: str | None = None,
    qty_lots: int = 1,
    horizon_days: int | None = None,
    **ctx: Any,
) -> dict[str, Any]:
    """Build a defined-risk option expression config envelope.

    Resolves the archetype's template (or composes explicit legs for the GAP
    broken-wing) against the live chain via ``resolve_strategy``, sizes
    strike/horizon off ``implied_move``, applies the tier moneyness/leg knobs,
    and REJECTS any structure whose ``max_loss`` is ``None`` (unlimited). For R5
    relative-options, resolves both underlyings and aggregates net greeks across
    them (cross-underlying critique is a GAP — annotate, don't fabricate). Returns
    a ``config_schema`` envelope; raises ``StrategyResolutionError`` / honest
    failure (no fabricated card) when the chain can't support the structure.
    """
    knobs = tiers.tier_knobs("option_strategy", tier)

    env = config_schema.base_envelope(
        archetype=archetype.key,
        expression_kind="option_strategy",
        tier=tier,
        label=archetype.label,
    )

    is_relative = bool(archetype.params.get("two_underlying")) or (
        archetype.key == "R5_relative_options"
    )

    if is_relative:
        _build_relative_options(
            db, env, archetype, tier, knobs,
            underlying=underlying, expiry=expiry, qty_lots=qty_lots,
            horizon_days=horizon_days, **ctx,
        )
    else:
        _build_single_underlying(
            db, env, archetype, tier, knobs,
            underlying=underlying, expiry=expiry, qty_lots=qty_lots,
            horizon_days=horizon_days, direction=ctx.get("direction"),
        )

    # A commodity (MCX) expression is leveraged → cost through the MCX exchange
    # rate and tag the round-trip note. Detected off the India-typed legs so both
    # the single-underlying and (future) relative commodity paths are covered.
    is_commodity = any(
        config_schema.is_commodity_segment(inst.get("segment"))
        for inst in env.get("instruments", [])
    )

    # Construction-time alignment score (Phase-4 layers the Trust verdict on top).
    pop = env["structure"].get("pop")
    is_gap = archetype.status == "GAP"
    env["scores"] = {
        "construction_alignment": _alignment_score(pop, gap=is_gap),
        "basket_purity": None,
        "alignment_kind": (
            "relative_value" if (is_relative or RELATIVE in archetype.view_types)
            else "event_study"
        ),
    }

    # Round-trip option transaction-cost estimate (engine constants, not invented).
    segment = "MCX-OPT" if is_commodity else "NFO-OPT"
    env["costs"] = {
        "round_trip_bps": _round_trip_option_bps(segment=segment),
        "segment": segment,
        "note": (
            "Both option legs, round trip — "
            + ("MCX exchange" if is_commodity else "exchange")
            + " + STT-on-intrinsic + slippage."
        ),
    }
    return env


# ── Single-underlying option structures (E1/E3/E4/E6 + generic) ──────────────


def _build_single_underlying(
    db: "Session",
    env: dict[str, Any],
    archetype: "Archetype",
    tier: str,
    knobs: "tiers.TierKnobs",
    *,
    underlying: Optional[str],
    expiry: Optional[str],
    qty_lots: int,
    horizon_days: Optional[int],
    direction: Optional[str] = None,
) -> None:
    sym = (underlying or archetype.params.get("default_underlying") or "NIFTY")
    sym = str(sym).strip().upper()

    # MCX commodity? Resolve the chain on the option-bearing symbol (a mini such
    # as GOLDM has no options → route to GOLD); the option engine already handles
    # MCX underlyings (research_only lifted). Never a fabricated chain target.
    is_commodity = commodities.is_commodity(sym)
    resolve_sym = (commodities.options_underlying(sym) or sym) if is_commodity else sym

    payload = _resolve_for_archetype(
        db, archetype, tier, resolve_sym, expiry=expiry, qty_lots=qty_lots,
        direction=direction,
    )
    _reject_if_unlimited(payload, resolve_sym)

    legs = payload["editable"]["legs"]
    computed = payload["computed"]
    locked = payload["locked"]
    is_index = _is_index(resolve_sym)

    instruments, warnings, notes = _legs_to_instruments(
        legs, resolve_sym, locked, is_index=is_index, is_commodity=is_commodity,
    )
    if resolve_sym in honest_short.MONTHLY_ONLY_INDICES:
        warnings.append(
            f"{resolve_sym} is monthly-only (no weeklies as of 2026) — an "
            "event-timed weekly structure must use NIFTY/SENSEX instead."
        )

    env["instruments"] = instruments
    env["warnings"] = warnings
    env["expressability"] = {
        "symmetric": True,           # defined-risk option structure is symmetric
        "degraded": False,
        "short_mode": None,
        "notes": notes,
    }
    env["structure"] = _option_structure(
        underlying=str(locked.get("underlying") or resolve_sym),
        template=payload["editable"]["template"], legs=legs, computed=computed,
        critique=payload.get("critique"),
        implied=_implied_block(
            db, resolve_sym, expiry=expiry, horizon_days=horizon_days,
        ),
        size_cut=knobs.size_cut,
        moneyness=knobs.option_moneyness,
        leverage_note=commodities.LEVERAGE_NOTE if is_commodity else None,
    )


def _resolve_for_archetype(
    db: "Session",
    archetype: "Archetype",
    tier: str,
    sym: str,
    *,
    expiry: Optional[str],
    qty_lots: int,
    direction: Optional[str] = None,
) -> dict[str, Any]:
    """Resolve the concrete structure for one underlying via the option engine.

    GAP broken-wing (E6) composes explicit legs; everything else maps the
    (archetype, tier) → a real ``TEMPLATES`` key. A commodity archetype (CM1/CM2)
    additionally honours its ``alt_bearish_template`` (a bearish directional view)
    / ``alt_template`` (the conservative event-straddle → cheaper strangle).
    """
    if archetype.template_or_scheme == "broken_wing_butterfly":
        return _resolve_broken_wing(db, sym, expiry=expiry, qty_lots=qty_lots)

    template = _TIER_TEMPLATE.get(archetype.key, {}).get(tier)
    if template is None:
        template = _commodity_template(archetype, tier, direction)
    if template is None:
        template = archetype.template_or_scheme
    if not template or template not in _opt.TEMPLATES:
        raise _opt.StrategyResolutionError(
            f"No defined-risk option template for archetype {archetype.key!r} "
            f"tier {tier!r} (resolved {template!r})."
        )
    return _opt.resolve_strategy(
        db, sym, template, expiry=expiry, qty_lots=qty_lots,
    )


def _resolve_broken_wing(
    db: "Session",
    sym: str,
    *,
    expiry: Optional[str],
    qty_lots: int,
) -> dict[str, Any]:
    """Compose a bullish broken-wing CALL butterfly from explicit chain strikes.

    Net calls = +1 (low) −2 (mid) +1 (high) = 0 → fully bounded both edges
    (defined risk). The wings are deliberately UNEQUAL ("broken") to cheapen the
    debit; the real engine prices the legs and computes max_loss/max_profit. No
    strike or premium is fabricated — strikes are taken off the live chain.
    """
    from backend.market.option_chain import get_chain

    chain = get_chain(db, sym, expiry, width=15)
    if chain is None:
        raise _opt.StrategyResolutionError(
            f"No option chain for {sym!r} — can't compose a broken-wing butterfly."
        )
    rows = chain.get("rows") or []
    atm = float(chain.get("atm_strike") or 0.0)
    strikes = sorted({float(r["strike"]) for r in rows})
    if atm <= 0 or len(strikes) < 4:
        raise _opt.StrategyResolutionError(
            f"Chain for {sym!r} too thin to build a broken-wing butterfly."
        )
    try:
        i = strikes.index(min(strikes, key=lambda s: abs(s - atm)))
    except ValueError as exc:  # pragma: no cover - defensive
        raise _opt.StrategyResolutionError(
            f"Couldn't locate the ATM strike for {sym!r}."
        ) from exc
    # Lower wing 2 steps, upper wing 1 step (the "broken"/cheap side up).
    k_low = strikes[i]
    mid_idx = min(len(strikes) - 1, i + 2)
    high_idx = min(len(strikes) - 1, mid_idx + 1)
    k_mid = strikes[mid_idx]
    k_high = strikes[high_idx]
    if len({k_low, k_mid, k_high}) < 3:
        raise _opt.StrategyResolutionError(
            f"Chain for {sym!r} lacks 3 distinct CE strikes for a broken wing."
        )
    explicit_legs = [
        {"option_type": "CE", "side": "BUY", "strike": k_low},
        {"option_type": "CE", "side": "SELL", "strike": k_mid},
        {"option_type": "CE", "side": "SELL", "strike": k_mid},
        {"option_type": "CE", "side": "BUY", "strike": k_high},
    ]
    payload = _opt.resolve_strategy(
        db, sym, "broken_wing_butterfly",
        expiry=expiry, qty_lots=qty_lots, explicit_legs=explicit_legs, chain=chain,
    )
    payload["editable"]["template"] = "broken_wing_butterfly"
    return payload


# ── R5 two-underlying relative-options ───────────────────────────────────────


def _build_relative_options(
    db: "Session",
    env: dict[str, Any],
    archetype: "Archetype",
    tier: str,
    knobs: "tiers.TierKnobs",
    *,
    underlying: Optional[str],
    expiry: Optional[str],
    qty_lots: int,
    horizon_days: Optional[int],
    **ctx: Any,
) -> None:
    sym_a = str(ctx.get("symbol_a") or underlying or "").strip().upper()
    sym_b = str(ctx.get("symbol_b") or "").strip().upper()
    if not sym_a or not sym_b:
        raise _opt.StrategyResolutionError(
            "R5 relative-options needs two NFO underlyings (symbol_a beats "
            "symbol_b) — none fabricated."
        )
    tmpl_a = archetype.template_or_scheme or "bull_call_spread"
    tmpl_b = str(archetype.params.get("leg_b_template") or "bear_put_spread")

    pay_a = _opt.resolve_strategy(db, sym_a, tmpl_a, expiry=expiry, qty_lots=qty_lots)
    _reject_if_unlimited(pay_a, sym_a)
    pay_b = _opt.resolve_strategy(db, sym_b, tmpl_b, expiry=expiry, qty_lots=qty_lots)
    _reject_if_unlimited(pay_b, sym_b)

    instruments: list[dict[str, Any]] = []
    warnings: list[str] = []
    notes: list[str] = []
    for sym, pay in ((sym_a, pay_a), (sym_b, pay_b)):
        inst, warn, note = _legs_to_instruments(
            pay["editable"]["legs"], sym, pay["locked"], is_index=_is_index(sym),
        )
        instruments.extend(inst)
        warnings.extend(warn)
        notes.extend(note)

    ca, cb = pay_a["computed"], pay_b["computed"]
    net_greeks = {
        k: round(ca["net_greeks"].get(k, 0.0) + cb["net_greeks"].get(k, 0.0), 4)
        for k in ("delta", "gamma", "theta", "vega")
    }
    # Both legs defined-risk → combined bound is the sum (no cross-delta hedge).
    combined_legs = [
        {**leg, "underlying": sym_a} for leg in pay_a["editable"]["legs"]
    ] + [
        {**leg, "underlying": sym_b} for leg in pay_b["editable"]["legs"]
    ]
    notes.append(
        "Two-underlying relative options: net greeks are summed across "
        f"{sym_a} and {sym_b} (the legs are NOT cross-delta-hedged). A combined "
        "single-underlying critique/POP/breakeven is a GAP — reported per leg."
    )

    env["instruments"] = instruments
    env["warnings"] = warnings
    env["expressability"] = {
        "symmetric": True,
        "degraded": False,
        "short_mode": None,
        "notes": notes,
    }
    env["structure"] = {
        "template": f"{tmpl_a}|{tmpl_b}",
        "legs": combined_legs,
        "net_premium": round(ca["net_premium"] + cb["net_premium"], 2),
        "max_loss": round((ca["max_loss"] or 0.0) + (cb["max_loss"] or 0.0), 2),
        "max_profit": round((ca["max_profit"] or 0.0) + (cb["max_profit"] or 0.0), 2),
        "pop": None,  # cross-underlying joint POP is a GAP — never fabricated
        "breakevens": [],  # not combinable across underlyings
        "net_greeks": net_greeks,
        "capital_required": round(
            ca["capital_required"] + cb["capital_required"], 2
        ),
        "underlyings": [sym_a, sym_b],
        "leg_a": {"underlying": sym_a, "template": tmpl_a,
                  "max_loss": ca["max_loss"], "breakevens": ca["breakevens"],
                  "pop": ca["pop"]},
        "leg_b": {"underlying": sym_b, "template": tmpl_b,
                  "max_loss": cb["max_loss"], "breakevens": cb["breakevens"],
                  "pop": cb["pop"]},
        "size_cut": knobs.size_cut,
        "moneyness": knobs.option_moneyness,
        "implied_move": {
            sym_a: _implied_block(db, sym_a, expiry=expiry, horizon_days=horizon_days),
            sym_b: _implied_block(db, sym_b, expiry=expiry, horizon_days=horizon_days),
        },
    }


# ── Shared helpers ───────────────────────────────────────────────────────────


def _reject_if_unlimited(payload: dict[str, Any], sym: str) -> None:
    """Defined-risk first: reject any structure with an unbounded max loss."""
    if payload["computed"].get("max_loss") is None:
        raise _opt.StrategyResolutionError(
            f"Refusing to register an unlimited-loss structure on {sym} — "
            "Pivot expresses defined-risk only (register-not-execute)."
        )


def _is_index(sym: str) -> bool:
    return sym.strip().upper() in _INDEX_UNDERLYINGS


def _legs_to_instruments(
    legs: list[dict[str, Any]],
    sym: str,
    locked: dict[str, Any],
    *,
    is_index: bool,
    is_commodity: bool = False,
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    """Map resolved option legs → India-typed ``config.instruments`` entries.

    Single-stock legs are stamped with the STT-on-intrinsic + physical-settlement
    warning (``honest_short.SINGLE_STOCK_OPTION_WARNING``); commodity (MCX) legs
    are typed ``commodity_option`` (segment ``MCX-OPT`` / exchange ``MCX``) and
    stamped with the LEVERAGE note (``commodities.LEVERAGE_NOTE``) instead — the
    honest-short rule for each option case. A commodity leg is never a single
    stock, so the two warnings are mutually exclusive.
    """
    if is_commodity:
        instrument_type = "commodity_option"
        exchange = str(locked.get("exchange") or "MCX")
        segment = str(locked.get("segment") or "MCX-OPT")
    elif is_index:
        instrument_type = "index_option"
        exchange = str(locked.get("exchange") or "NFO")
        segment = str(locked.get("segment") or "NFO-OPT")
    else:
        instrument_type = "stock_option"
        exchange = str(locked.get("exchange") or "NFO")
        segment = str(locked.get("segment") or "NFO-OPT")
    instruments: list[dict[str, Any]] = []
    warnings: list[str] = []
    notes: list[str] = []
    if is_commodity:
        leg_warn = commodities.LEVERAGE_NOTE
    elif not is_index:
        leg_warn = honest_short.SINGLE_STOCK_OPTION_WARNING
    else:
        leg_warn = ""
    for leg in legs:
        note = (
            f"{leg['option_type']} {leg['side']} {leg['strike']:g}"
            + (f" — {leg_warn}" if leg_warn else "")
        )
        instruments.append({
            "symbol": leg.get("tradingsymbol") or sym,
            "exchange": exchange,
            "segment": segment,
            "instrument_type": instrument_type,
            "role": "long" if leg["side"] == "BUY" else "short",
            "tradeable": True,
            "note": note,
        })
    if leg_warn:
        warnings.append(leg_warn)
        notes.append(leg_warn)
    return instruments, warnings, notes


def _option_structure(
    *,
    underlying: str,
    template: str,
    legs: list[dict[str, Any]],
    computed: dict[str, Any],
    critique: Optional[dict[str, Any]],
    implied: Optional[dict[str, Any]],
    size_cut: Optional[float],
    moneyness: Optional[str],
    leverage_note: Optional[str] = None,
) -> dict[str, Any]:
    """The ``config.structure`` block for a single-underlying option strategy.

    Carries the full ``STRUCTURE_KEYS["option_strategy"]`` set quoted straight
    from the engine ``computed`` block (never re-derived), plus the resolved
    ``underlying`` (the option-bearing chain symbol the engine locked) so Phase-4
    ``deploy_expression`` can arm ``action.place_option_strategy`` without
    re-deriving it. A commodity (MCX) structure also carries the LEVERAGE note so
    the downstream ``risk_profile`` disclosure can fold it in (commodities are
    leveraged + never auto-sized)."""
    structure = {
        "underlying": underlying,
        "template": template,
        "legs": legs,
        "net_premium": computed["net_premium"],
        "max_loss": computed["max_loss"],
        "max_profit": computed["max_profit"],
        "pop": computed["pop"],
        "breakevens": computed["breakevens"],
        "net_greeks": computed["net_greeks"],
        "capital_required": computed["capital_required"],
        "margin_estimate": computed.get("margin_estimate"),
        "critique": critique,
        "implied_move": implied,
        "size_cut": size_cut,
        "moneyness": moneyness,
    }
    if leverage_note:
        structure["leverage_note"] = leverage_note
    return structure


def _implied_block(
    db: "Session",
    sym: str,
    *,
    expiry: Optional[str],
    horizon_days: Optional[int],
) -> Optional[dict[str, Any]]:
    """The priced-in expected move for ``sym`` (or ``None`` — never fabricated)."""
    move = _im.implied_move(db, sym, expiry=expiry, horizon_days=horizon_days)
    if move is None:
        return None
    return {
        "forward": move.forward,
        "atm_iv": move.atm_iv,
        "expected_move_abs": move.expected_move_abs,
        "expected_move_pct": move.expected_move_pct,
        "low": move.low,
        "high": move.high,
        "source": move.source,
    }


def _alignment_score(pop: Optional[float], *, gap: bool) -> float:
    """Construction-time alignment (0..100) — ceiling-tiered by construct rigor.

    Built from the engine's market-implied POP (a real, non-fabricated number);
    GAP archetypes (broken-wing, two-underlying relative) carry a lower ceiling
    so a glued/approximate construct never out-scores a clean one.
    """
    base = 55.0 + 40.0 * float(pop or 0.0)
    ceiling = 70.0 if gap else 90.0
    return round(min(base, ceiling), 1)


def _round_trip_option_bps(*, segment: str = "NFO-OPT") -> float:
    """Round-trip (buy+sell) option transaction cost in bps, from the engine.

    ``segment`` routes the exchange charge: ``MCX-OPT`` for a commodity leg
    (``trading_costs.OPT_EXCHANGE_PCT_MCX``), ``NFO-OPT`` otherwise.
    """
    from backend.services import trading_costs

    return round(
        trading_costs.option_leg_bps("BUY", segment=segment)
        + trading_costs.option_leg_bps("SELL", segment=segment),
        2,
    )


__all__ = ["build_option_expression"]
