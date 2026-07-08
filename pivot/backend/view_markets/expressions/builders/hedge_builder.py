"""View Markets — Phase 3 HEDGE expression builder (INDEX-level overlay).

Handles ``expression_kind == "hedge"``: T3 optionized/hedged overlay (protective
put / zero-cost collar / covered-call-financed put / long call spread). India
gate (spec §4.5): hedge at the INDEX level with Nifty (or Bank Nifty), NOT
name-by-name — single-stock options are thin/short-dated; Nifty is extremely
liquid with a 12-month tenor.

Delegates to the REAL option engine — never reinvents the hedge math:

  * ``backend.services.option_strategies.resolve_strategy(db, underlying,
    template_name, *, expiry=None, qty_lots=1, explicit_legs=None, chain=None)``
    — for the ``bull_call_spread`` template, and explicit-leg collars (long OTM
    put + short OTM call). Returns the full payload with ``max_loss`` (the
    floor), ``net_premium`` (collar ≈ zero-cost), ``net_greeks``, ``critique``.
    The hedge is a DEFENDED, sized object (POP / margin run on it).
  * ``backend.view_markets.implied_move.implied_move`` — sizes the put strike /
    collar wings to the priced-in expected move over the theme's horizon.

Tier knobs (``tiers.tier_knobs("hedge", tier)``): Conservative = zero-cost collar
(floor + finance), Balanced = covered-call-financed put (put nearer the money,
financed by a further OTM call), Aggressive = long call spread (convex, capital-
light). ``underlying`` defaults to NIFTY; BANKNIFTY is allowed but monthly-only
(no weeklies) — stamped as a warning. Non-index underlyings are coerced to NIFTY
with a warning (the gate: we hedge the theme at the index level, never
name-by-name).

Persists to ``config.structure`` (``config_schema.STRUCTURE_KEYS["hedge"]``):
``underlying_index``, ``hedge_template``, ``legs``, ``max_loss`` (the floor),
plus ``net_cost``, ``floor_level``, ``net_greeks``, ``pop``, ``breakevens``,
``capital_required``; ``instruments`` are index options (NFO-OPT).
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from backend.view_markets.expressions import config_schema
from backend.view_markets.expressions.honest_short import (
    MONTHLY_ONLY_INDICES,
    WEEKLY_INDICES,
)
from backend.view_markets.expressions.tiers import tier_knobs

if TYPE_CHECKING:  # pragma: no cover - typing only
    from sqlalchemy.orm import Session

    from backend.models import MarketView
    from backend.view_markets.expressions.catalog import Archetype

# Indices we will write a hedge against (the only legal, liquid index-option
# underlyings). Aliases normalise to the canonical chain underlying.
_INDEX_ALIASES: dict[str, str] = {
    "NIFTY": "NIFTY",
    "NIFTY50": "NIFTY",
    "NIFTY 50": "NIFTY",
    "BANKNIFTY": "BANKNIFTY",
    "NIFTYBANK": "BANKNIFTY",
    "BANK NIFTY": "BANKNIFTY",
    "SENSEX": "SENSEX",
}

# Tier ``option_moneyness`` → collar wing offsets (as a fraction of the 1σ
# implied move) OR a direct template. ``put_k``/``call_k`` size the put/call
# strikes off ``forward ∓ k·expected_move``; ``template`` short-circuits to a
# resolved option template (the long call spread).
_HEDGE_PLAN: dict[str, dict[str, Any]] = {
    # Conservative — symmetric ≈ zero-cost collar (floor + finance).
    "zero_cost_collar": {"kind": "collar", "put_k": 1.0, "call_k": 1.0},
    # Balanced — put nearer the money (more protection), financed by a further
    # OTM short call.
    "covered_call_financed_put": {"kind": "collar", "put_k": 0.5, "call_k": 1.0},
    # Aggressive — convex, capital-light long call spread.
    "long_call_spread": {"kind": "template", "template": "bull_call_spread"},
}


def _normalize_index(underlying: str | None) -> tuple[str, list[str]]:
    """Coerce ``underlying`` to a tradeable index symbol (the India gate).

    Returns ``(index_symbol, warnings)``. A non-index underlying is coerced to
    NIFTY with a warning — we hedge the theme at the index level, never
    name-by-name (single-stock options are thin/short-dated, spec §4.5).
    """
    warnings: list[str] = []
    raw = (underlying or "NIFTY").strip().upper()
    idx = _INDEX_ALIASES.get(raw)
    if idx is None:
        warnings.append(
            f"{raw} is not an index — hedging at the index level via NIFTY "
            "instead of name-by-name (single-stock options are thin and "
            "short-dated)."
        )
        idx = "NIFTY"
    if idx in MONTHLY_ONLY_INDICES:
        warnings.append(
            f"{idx} options are monthly-only (no weeklies as of 2026) — the "
            "hedge uses the nearest monthly expiry."
        )
    elif idx not in WEEKLY_INDICES:
        warnings.append(f"{idx} expiry cadence unverified — using listed expiries.")
    return idx, warnings


def _nearest_strike(rows: list[dict[str, Any]], target: float) -> float:
    """Snap ``target`` to the nearest listed strike on the chain (no fabrication)."""
    return float(min(rows, key=lambda r: abs(float(r["strike"]) - target))["strike"])


def _instruments_from_legs(
    index_symbol: str, legs: list[dict[str, Any]],
) -> list[config_schema.InstrumentSpec]:
    """Map resolved option legs to India-typed ``config.instruments`` entries."""
    out: list[config_schema.InstrumentSpec] = []
    for leg in legs:
        role = "hedge" if leg.get("side") == "BUY" else "short"
        out.append(
            config_schema.InstrumentSpec(
                symbol=index_symbol,
                exchange="NFO",
                segment="NFO-OPT",
                instrument_type="index_option",
                role=role,  # type: ignore[typeddict-item]
                tradeable=True,
                note=(
                    f"{leg.get('side')} {leg.get('option_type')} "
                    f"{leg.get('strike')}"
                ),
            )
        )
    return out


def build_hedge_expression(
    db: "Session",
    view: "MarketView",
    archetype: "Archetype",
    tier: str,
    *,
    underlying: str = "NIFTY",
    expiry: str | None = None,
    qty_lots: int = 1,
    horizon_days: int | None = None,
    **ctx: Any,
) -> dict[str, Any]:
    """Build an index-level collar / call-spread hedge overlay.

    Resolves the tier's hedge template against the Nifty (or Bank Nifty) chain
    via ``resolve_strategy``, sizes the collar wings off ``implied_move`` and the
    horizon, and reports the floor (``max_loss``) + net cost (collar ≈ zero).
    Refuses a name-by-name single-stock hedge (India gate). Defined-risk first:
    a structure whose ``max_loss`` is ``None`` (unbounded) is rejected.

    Raises ``option_strategies.StrategyResolutionError`` when the chain / implied
    move is unavailable (no fabrication) so dispatch can skip the archetype.
    """
    # Lazy heavy imports (keep builder-module import light for dispatch).
    from backend.market.option_chain import get_chain
    from backend.services import trading_costs
    from backend.services.option_strategies import (
        StrategyResolutionError,
        resolve_strategy,
    )
    from backend.view_markets.implied_move import implied_move_from_chain

    index_symbol, warnings = _normalize_index(underlying)

    knobs = tier_knobs("hedge", tier)
    moneyness = knobs.option_moneyness or "zero_cost_collar"
    plan = _HEDGE_PLAN.get(moneyness, _HEDGE_PLAN["zero_cost_collar"])

    chain = get_chain(db, index_symbol, expiry, width=15)
    if not chain:
        raise StrategyResolutionError(
            f"No option chain for {index_symbol} — cannot build an index hedge "
            "(instrument master not refreshed or unknown expiry)."
        )
    rows = chain.get("rows") or []
    if not rows:
        raise StrategyResolutionError(f"Empty option chain for {index_symbol}.")

    # Size off the option-implied expected move over the view horizon.
    im = implied_move_from_chain(chain, horizon_days=horizon_days)

    if plan["kind"] == "template":
        # Aggressive: convex, capital-light long call spread (template picks its
        # own strikes off delta/atm).
        template_name = plan["template"]
        resolved = resolve_strategy(
            db, index_symbol, template_name,
            expiry=expiry, qty_lots=qty_lots, chain=chain,
        )
        floor_level: float | None = None
    else:
        # Conservative / Balanced: collar = long OTM put + short OTM call sized
        # to the implied move. Without a usable implied move we cannot size the
        # wings honestly — degrade rather than fabricate strikes.
        if im is None:
            raise StrategyResolutionError(
                f"No option-implied expected move for {index_symbol} — cannot "
                "size the collar wings without fabricating strikes."
            )
        forward = im.forward
        em = im.expected_move_abs
        put_strike = _nearest_strike(rows, forward - plan["put_k"] * em)
        call_strike = _nearest_strike(rows, forward + plan["call_k"] * em)
        explicit_legs = [
            {"option_type": "PE", "side": "BUY", "strike": put_strike},
            {"option_type": "CE", "side": "SELL", "strike": call_strike},
        ]
        template_name = "collar"
        resolved = resolve_strategy(
            db, index_symbol, template_name,
            expiry=expiry, qty_lots=qty_lots,
            explicit_legs=explicit_legs, chain=chain,
        )
        floor_level = put_strike

    computed = resolved["computed"]
    legs = resolved["editable"]["legs"]
    max_loss = computed.get("max_loss")

    # Defined-risk first: a hedge with unbounded loss is not a hedge. Reject.
    if max_loss is None:
        raise StrategyResolutionError(
            f"{archetype.key} resolved an unbounded-loss structure on "
            f"{index_symbol} — a hedge must be defined-risk (max_loss is the "
            "floor)."
        )

    net_premium = computed.get("net_premium") or 0.0
    net_cost = round(-float(net_premium), 2)  # +ve = premium paid, -ve = credit

    # ── Envelope ──
    label = f"{archetype.label} — {index_symbol} {moneyness.replace('_', ' ')}"
    cfg = config_schema.base_envelope(
        archetype=archetype.key,
        expression_kind="hedge",
        tier=tier,
        label=label,
    )
    cfg["instruments"] = _instruments_from_legs(index_symbol, legs)
    cfg["structure"] = {
        "underlying_index": index_symbol,
        "hedge_template": template_name,
        "legs": legs,
        "max_loss": max_loss,
        "net_cost": net_cost,
        "floor_level": floor_level,
        "net_premium": net_premium,
        "net_greeks": computed.get("net_greeks"),
        "pop": computed.get("pop"),
        "breakevens": computed.get("breakevens"),
        "capital_required": computed.get("capital_required"),
    }
    # Index option overlays are clean, legal shorts (short index option, not a
    # delivery short) — symmetric, never honest-short degraded.
    cfg["expressability"] = {
        "symmetric": True,
        "degraded": False,
        "short_mode": None,
        "notes": [
            "Index-level hedge overlay — defined-risk, "
            "register-not-execute (you confirm the order in your broker app)."
        ],
    }
    cfg["scores"] = {
        # Construction-time only; Phase-4 attaches the Trust verdict + backtest.
        "construction_alignment": 70.0 if (im and im.source == "iv") else 55.0,
        "basket_purity": None,
        "alignment_kind": "event_study",
    }
    rt_bps = round(
        (trading_costs.option_leg_bps("buy") + trading_costs.option_leg_bps("sell"))
        * 10_000.0,
        2,
    )
    cfg["costs"] = {
        "round_trip_bps": rt_bps,
        "note": "Per-leg NFO-OPT premium charges (STT on the sell side).",
    }
    cfg["warnings"] = warnings + list(resolved.get("validation", {}).get(
        "liquidity_flags", []
    ))
    return cfg


__all__ = ["build_hedge_expression"]
