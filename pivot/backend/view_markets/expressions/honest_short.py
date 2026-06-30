"""View Markets — Phase 3 honest-short decision rule (no fabricated shorts).

Indian retail CANNOT short single stocks or ETFs in delivery (spec §1.6). The
expression generator ENFORCES this; it never invents a fake delivery short or a
fake price. This module owns the one decision rule + the AVOID/underweight
expression representation:

    single-stock short  → SSF-eligible? single-stock FUTURE
                          else long PUT / PUT-SPREAD (deliverable-safe if closed
                          pre-expiry; flag STT-on-intrinsic + physical settlement)
                          else AVOID/underweight annotation
    index short         → NIFTY/BANKNIFTY FUTURE or PUT/PUT-SPREAD
                          NEVER an ETF delivery short ("short NIFTYBEES" is not a
                          real expression — no SLB depth)
    commodity short     → MCX commodity FUTURE (the clean, SYMMETRIC short) — unlike
                          equities there is NO retail-delivery-short constraint, so a
                          commodity is genuinely shortable; or a long MCX PUT when a
                          defined-risk short is preferred. NEVER AVOID for a listed
                          MCX F&O commodity. This is the whole point of commodities:
                          symmetric long/short + producer-vs-importer expressions that
                          equities can't do. LEVERAGED — carry the leverage note, never
                          auto-size (register-not-execute).
    no F&O on either leg → degrade to AVOID/underweight and SAY SO (Alignment
                          Score must drop; symmetric expression impossible)

India microstructure hard-coded (spec §1.7, as of 2026):
  * Weeklies: NIFTY 50 and SENSEX only. BANK NIFTY is MONTHLY-only.
  * Single-stock options: MONTHLY + PHYSICALLY settled + STT charged on the
    INTRINSIC value at expiry → force/flag pre-expiry square-off.
  * Foreign legs → the listed Indian ETF proxy (e.g. MON100 for US tech); never
    a US instrument.

The actual SSF-eligibility / liquidity tables are sourced in INTEGRATE (NSE F&O
master / instrument master); the constants below are the SEEDS + the contract.
Functions raise ``NotImplementedError`` in the skeleton — the BUILD agent fills
them, but the decision-rule shape is frozen here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional

# ── India microstructure constants (hard-coded, 2026) ───────────────────────

# Indices with NSE/BSE WEEKLY option expiries (everything else = monthly only).
WEEKLY_INDICES: frozenset[str] = frozenset({"NIFTY", "NIFTY50", "SENSEX"})
# BANKNIFTY is liquid but monthly-only since 2026 — kept explicit for the guard.
MONTHLY_ONLY_INDICES: frozenset[str] = frozenset({"BANKNIFTY", "NIFTYBANK"})
# Index underlyings whose FUTURE/PUT is the legal short vehicle.
SHORTABLE_INDEX_FUTURES: frozenset[str] = frozenset(
    {"NIFTY", "NIFTY50", "BANKNIFTY", "NIFTYBANK", "SENSEX"}
)
# Foreign exposure → listed Indian ETF proxy (spec scope contract). Seed map.
FOREIGN_ETF_PROXY: dict[str, str] = {
    "NASDAQ100": "MON100",     # Motilal Oswal NASDAQ 100 ETF
    "US_TECH": "MON100",
    "SP500": "MOM100",         # placeholder; INTEGRATE pins the live ticker
}

# ETFs are effectively un-shortable (no SLB depth) — "short NIFTYBEES" is never
# a real expression. Used by the guard to reject an ETF delivery short outright.
UNSHORTABLE_ETF_NOTE = (
    "ETFs have no SLB depth in India — an ETF delivery short is not a real "
    "expression. Short the index via a NIFTY/BANKNIFTY future or put instead."
)

# STT-on-intrinsic + physical-settlement warning stamped on every single-stock
# option leg (spec §1.7 / earnings §).
SINGLE_STOCK_OPTION_WARNING = (
    "Single-stock options are monthly, physically settled, and STT is charged "
    "on intrinsic value at expiry — square off before expiry to avoid physical "
    "delivery + the STT-on-intrinsic trap."
)

ShortMode = Literal[
    "ssf_future",        # single-stock future (SSF-eligible name)
    "put",               # long put (deliverable-safe if closed pre-expiry)
    "put_spread",        # put debit spread (defined risk)
    "index_future",      # NIFTY/BANKNIFTY future
    "index_put",         # index put / put-spread
    "commodity_future",  # MCX commodity future — the SYMMETRIC commodity short (leveraged)
    "commodity_put",     # long MCX commodity put — defined-risk commodity short
    "avoid",             # AVOID / underweight annotation — NOT a tradeable short
]


@dataclass(frozen=True)
class ShortLeg:
    """The honest representation of a desired short leg.

    ``mode`` is the legal vehicle chosen (or ``"avoid"`` when none exists).
    ``instrument`` names the actual tradeable (the SSF symbol, the option
    underlying, the index future) or echoes the original symbol for an AVOID.
    ``tradeable`` is ``False`` only for ``mode == "avoid"``. ``degraded`` is
    ``True`` whenever we could not give a clean symmetric short (put/avoid
    fallbacks) — dispatch lowers the Alignment Score when this is set.
    ``note`` is the user-facing one-liner (carries the microstructure warning).
    """

    symbol: str
    mode: ShortMode
    instrument: str
    tradeable: bool
    degraded: bool
    note: str
    warnings: list[str] = field(default_factory=list)


def _norm(symbol: str) -> str:
    """Upper-case + strip spaces/underscores so ``Nifty 50`` == ``NIFTY50``."""
    return "".join(ch for ch in (symbol or "").upper() if ch.isalnum())


# Future / SSF note (futures are physically settled in India + carry roll/margin;
# distinct from the option STT-on-intrinsic warning).
_SSF_FUTURE_NOTE = (
    "Single-stock future: monthly contract, ~15-20% SPAN+exposure margin, "
    "carries a roll cost, physically settled — square off before expiry."
)
_INDEX_FUTURE_NOTE = (
    "Short the index via its NFO future (the only legal index short — never an "
    "ETF delivery short)."
)
_NO_FNO_NOTE = (
    "No F&O / SLB depth — retail cannot hold a delivery or overnight short on "
    "this name. Rendered as an AVOID/underweight annotation, not a short."
)
_UNCONFIRMED_SSF_NOTE = (
    "SSF/F&O eligibility unconfirmed — verify in the instrument master before "
    "arming; using a defined-risk long put as the deliverable-safe proxy."
)
_INTRADAY_NOTE = (
    "An intraday MIS short is technically possible but must be squared off the "
    "same day — Pivot arms position trades, not intraday scalps, so this leg "
    "stays an AVOID for a multi-day view."
)
# Commodity short notes (MCX). Unlike equities, commodities ARE shortable — the
# MCX future is the clean symmetric short. Both vehicles are LEVERAGED, so the
# note carries the leverage + register-not-execute reminder (never auto-sized).
_COMMODITY_FUTURE_NOTE = (
    "Commodity short via the MCX future — commodities are SYMMETRICALLY shortable "
    "(no SLB / delivery-short constraint that blocks equities). LEVERAGED: SPAN + "
    "exposure margin, carries a roll cost, physically/cash settled — square off or "
    "roll before expiry. Never auto-sized; confirm the lots in your broker app "
    "(register-not-execute)."
)
_COMMODITY_PUT_NOTE = (
    "Commodity short expressed as a long MCX put — DEFINED risk (premium paid), no "
    "margin-call tail. Square off before expiry. The option is leveraged on the "
    "commodity notional; never auto-sized (register-not-execute)."
)
_COMMODITY_NOT_LISTED_NOTE = (
    "Not a recognised MCX F&O commodity — there is no listed future/option to "
    "short, so this leg is an AVOID rather than a fabricated commodity contract. "
    "Verify the symbol against the MCX instrument master."
)


def short_leg_for(
    symbol: str,
    *,
    is_index: bool = False,
    is_commodity: bool = False,
    ssf_eligible: Optional[bool] = None,
    fno_eligible: Optional[bool] = None,
    prefer_defined_risk: bool = False,
    allow_intraday: bool = False,
) -> ShortLeg:
    """Resolve the honest short vehicle for ``symbol`` per the decision rule.

    Decision rule (spec §1.6 / §2 "honest short"), hard-coded — never fabricates
    a delivery short or a price:

    * **index** → if the index has an NFO future (NIFTY/BANKNIFTY/SENSEX) use the
      ``index_future`` (clean symmetric short); otherwise (an ETF passed as an
      "index", e.g. ``NIFTYBEES``) → ``avoid`` with :data:`UNSHORTABLE_ETF_NOTE`.
      Never an ETF delivery short.
    * **commodity (`is_commodity is True`)** → commodities ARE shortable on MCX
      (no retail-delivery-short constraint), so this returns a TRADEABLE short,
      NEVER an AVOID for a listed F&O commodity: a ``commodity_future`` (the clean
      SYMMETRIC short — ``degraded=False``), or a defined-risk ``commodity_put``
      when ``prefer_defined_risk`` is set. Only ``fno_eligible is False`` (a
      symbol confirmed NOT on the MCX F&O master) degrades to ``avoid``. Both
      vehicles are LEVERAGED — the note carries the leverage + register-not-execute
      reminder and the leg is never auto-sized. This is what lets the engine build
      producer-vs-importer / gold-vs-silver expressions equities can't.
    * **single stock, SSF/F&O-eligible (`ssf_eligible is True`)** → ``ssf_future``
      (clean, not degraded).
    * **single stock, eligibility unknown (`ssf_eligible is None`)** → degrade to
      a defined-risk long ``put`` proxy, FLAGGED unconfirmed (``degraded=True``);
      the builder passes a known bool once the master is wired (INTEGRATE).
    * **single stock, NOT F&O-eligible (`ssf_eligible is False`)** → no future and
      no listed option exist → ``avoid``. (Offering a put here would fabricate an
      instrument that does not trade — the anti-fabrication guard.) ``allow_intraday``
      only adds an explanatory note; Pivot arms position trades, not intraday.

    Parameters
    ----------
    symbol
        The name we want short exposure to (single stock, index, or commodity).
    is_index
        ``True`` when ``symbol`` is an index → route to an index future, NEVER an
        ETF delivery short.
    is_commodity
        ``True`` when ``symbol`` is an MCX commodity → route to a commodity future
        (or defined-risk put). Mutually exclusive with ``is_index`` (index wins).
    ssf_eligible
        Whether ``symbol`` is in the ~208 single-stock-future universe. ``None``
        means "unknown — look it up" (INTEGRATE wires the master).
    fno_eligible
        Commodity F&O eligibility (MCX). ``None``/``True`` → assume the listed MCX
        commodity is tradeable (the common case) and emit a clean future; ``False``
        → AVOID (confirmed not on the MCX master). Only consulted when
        ``is_commodity`` is set.
    prefer_defined_risk
        For a commodity short, return a defined-risk long put (``commodity_put``)
        instead of the leveraged future. Only consulted when ``is_commodity``.
    allow_intraday
        Whether an intraday MIS square-off short is acceptable for this view's
        horizon (rarely, for an EVENT day-trade). Default ``False``.

    Returns
    -------
    ShortLeg
        Never a fabricated delivery short. ``tradeable`` is ``False`` iff
        ``mode == "avoid"``; ``degraded`` is ``True`` for any non-clean vehicle
        (equity put proxy / avoid). A commodity future/put is a clean, tradeable,
        non-degraded short.
    """
    sym = _norm(symbol)

    if is_index:
        if sym in SHORTABLE_INDEX_FUTURES:
            note = _INDEX_FUTURE_NOTE
            warnings = [_INDEX_FUTURE_NOTE]
            if sym in MONTHLY_ONLY_INDICES:
                warnings.append(
                    f"{symbol} is monthly-only (no weeklies as of 2026)."
                )
            elif is_weekly_eligible(symbol):
                warnings.append(f"{symbol} has weekly expiries (NIFTY/SENSEX).")
            return ShortLeg(
                symbol=symbol,
                mode="index_future",
                instrument=f"{sym} FUT",
                tradeable=True,
                degraded=False,
                note=note,
                warnings=warnings,
            )
        # An ETF (or non-F&O index) passed as a short — "short NIFTYBEES" is not a
        # real expression. Degrade to AVOID and say why.
        return ShortLeg(
            symbol=symbol,
            mode="avoid",
            instrument=symbol,
            tradeable=False,
            degraded=True,
            note=UNSHORTABLE_ETF_NOTE,
            warnings=[UNSHORTABLE_ETF_NOTE],
        )

    # ── commodity (MCX) ───────────────────────────────────────────────────────
    # Commodities are SYMMETRICALLY shortable — the whole point of the MCX pass.
    # A listed F&O commodity gets a TRADEABLE short (future, or defined-risk put),
    # never an AVOID. Only a symbol confirmed off the MCX master degrades.
    if is_commodity:
        if fno_eligible is False:
            return ShortLeg(
                symbol=symbol,
                mode="avoid",
                instrument=symbol,
                tradeable=False,
                degraded=True,
                note=_COMMODITY_NOT_LISTED_NOTE,
                warnings=[_COMMODITY_NOT_LISTED_NOTE],
            )
        if prefer_defined_risk:
            return ShortLeg(
                symbol=symbol,
                mode="commodity_put",
                instrument=f"{sym} PE",
                tradeable=True,
                degraded=False,
                note=_COMMODITY_PUT_NOTE,
                warnings=[_COMMODITY_PUT_NOTE],
            )
        return ShortLeg(
            symbol=symbol,
            mode="commodity_future",
            instrument=f"{sym} FUT",
            tradeable=True,
            degraded=False,
            note=_COMMODITY_FUTURE_NOTE,
            warnings=[_COMMODITY_FUTURE_NOTE],
        )

    # ── single stock ────────────────────────────────────────────────────────
    if ssf_eligible is True:
        return ShortLeg(
            symbol=symbol,
            mode="ssf_future",
            instrument=f"{sym} FUT",
            tradeable=True,
            degraded=False,
            note=_SSF_FUTURE_NOTE,
            warnings=[_SSF_FUTURE_NOTE],
        )

    if ssf_eligible is None:
        # Unknown eligibility (pre-INTEGRATE): offer a defined-risk long put as a
        # deliverable-safe proxy, explicitly flagged unconfirmed + degraded.
        return ShortLeg(
            symbol=symbol,
            mode="put",
            instrument=f"{sym} PE",
            tradeable=True,
            degraded=True,
            note=_UNCONFIRMED_SSF_NOTE,
            warnings=[SINGLE_STOCK_OPTION_WARNING, _UNCONFIRMED_SSF_NOTE],
        )

    # ssf_eligible is False → confirmed no F&O → AVOID (do NOT fabricate a put).
    warnings = [_NO_FNO_NOTE]
    if allow_intraday:
        warnings.append(_INTRADAY_NOTE)
    return ShortLeg(
        symbol=symbol,
        mode="avoid",
        instrument=symbol,
        tradeable=False,
        degraded=True,
        note=_NO_FNO_NOTE,
        warnings=warnings,
    )


def avoid_annotation(
    symbol: str,
    *,
    reason: str,
    suggested_underweight: float = 0.0,
) -> ShortLeg:
    """Build a first-class AVOID / underweight ``ShortLeg`` (mode ``"avoid"``).

    The §3.3-#2 "express the underperform leg without shorting" answer: render
    the name as an AVOID/underweight annotation, NOT a tradeable short. Always
    ``tradeable=False``, ``degraded=True``.

    ``suggested_underweight`` is a 0..1 portfolio underweight (e.g. ``0.0`` =
    drop the name entirely, ``0.05`` = hold at half a normal 10% slot); it is
    surfaced in the ``note`` for the basket builder / card to honour.
    """
    note = reason.strip() if reason and reason.strip() else "AVOID — not shortable by retail."
    if suggested_underweight and suggested_underweight > 0:
        note = f"{note} Suggested underweight: {suggested_underweight:.0%} of a normal slot."
    return ShortLeg(
        symbol=symbol,
        mode="avoid",
        instrument=symbol,
        tradeable=False,
        degraded=True,
        note=note,
        warnings=[],
    )


def is_weekly_eligible(underlying: str) -> bool:
    """True only for NIFTY/SENSEX (weeklies); BANKNIFTY and single stocks → False."""
    return _norm(underlying) in WEEKLY_INDICES


def foreign_proxy(symbol: str) -> Optional[str]:
    """Map a foreign exposure to its listed Indian ETF proxy, or ``None``.

    Matches on the normalised key and also passes through a symbol that is
    already a known proxy ticker (so ``foreign_proxy("MON100") == "MON100"``).
    """
    sym = _norm(symbol)
    for key, proxy in FOREIGN_ETF_PROXY.items():
        if _norm(key) == sym or _norm(proxy) == sym:
            return proxy
    return None


__all__ = [
    "WEEKLY_INDICES",
    "MONTHLY_ONLY_INDICES",
    "SHORTABLE_INDEX_FUTURES",
    "FOREIGN_ETF_PROXY",
    "UNSHORTABLE_ETF_NOTE",
    "SINGLE_STOCK_OPTION_WARNING",
    "ShortMode",
    "ShortLeg",
    "short_leg_for",
    "avoid_annotation",
    "is_weekly_eligible",
    "foreign_proxy",
]
