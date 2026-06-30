"""View Markets — option-implied expected move + implied probability.

Small, reused primitive. Given an underlying / index and a horizon, returns
Pivot's OWN option-implied expected move and (optionally) a risk-neutral
implied probability that price clears a target level. This is the user-facing
"what's priced in" yardstick (PROGA caveat: we surface OUR option-implied
probability, never a prediction-market odds/bet). Consumed by
``expectations.compute_surprise``.

Formulas (testing doc §1.1 "What's-priced-in"):
  * Expected move (1σ to expiry) = Forward × IV_atm × √(T)  (≈ ATM straddle ×
    0.85 when IV is unavailable). The option-chain primitive ALREADY computes
    this — we reuse its ``expected_move`` block rather than re-deriving.
  * Implied probability = risk-neutral P(S_T {>,<} K) under a lognormal
    forward, i.e. Φ(d2)-style, off the ATM IV.
  * Horizon rescale: an expected move quoted to the chain's expiry (DTE days)
    is rescaled to an arbitrary ``horizon_days`` by ×√(horizon_days / DTE),
    the standard √t vol scaling.

Reuses (real interfaces, pinned 2026-06-29):
  * ``backend.market.option_chain.get_chain(db, underlying, expiry=None, *,
    width=..., now=None) -> dict | None``. Returned dict carries
    ``forward`` (float), ``atm_strike`` (float), ``t_years`` (float),
    ``expected_move`` ({"low","high","abs","pct"} | None), ``rows``
    (per-strike, each with ``ce``/``pe`` quotes incl. ``iv``), ``expiry``,
    ``segment``, ``asof``.
  * ``backend.services.option_strategies._prob_above(x, F, sigma, T) -> float``
    — risk-neutral P(S_T > x) under lognormal forward (the canonical primitive;
    private-but-stable). ``implied_probability`` reuses it (direction "below" =
    1 - p_above).
  * ``backend.market.greeks.black76.year_fraction`` — only if a horizon must be
    converted to T independently of the chain.

No DB writes. Returns ``None`` (never a fabricated number) when the chain is
unavailable or IV can't be solved.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:  # pragma: no cover - typing only
    from sqlalchemy.orm import Session

# The chain reports ``t_years`` as an annualised fraction; DTE = t_years × 365.
_DAYS_PER_YEAR = 365.0

# ATM-straddle → 1σ expected-move multiplier (Brenner–Subrahmanyam ≈ 0.8; the
# contract pins 0.85). Only used when the chain itself could not solve an
# expected move and we fall back to the raw straddle.
_STRADDLE_EM_MULT = 0.85


def _mid(side: Optional[dict[str, Any]]) -> float:
    """Best price for one option leg: bid/ask mid, else LTP. Mirrors the
    ``_mid_of`` logic inside ``option_chain.get_chain`` (never fabricates)."""
    if not side:
        return 0.0
    bid = side.get("bid") or 0.0
    ask = side.get("ask") or 0.0
    if bid > 0 and ask >= bid:
        return (bid + ask) / 2.0
    return float(side.get("ltp") or 0.0)


def _atm_row(chain: dict) -> Optional[dict[str, Any]]:
    atm_strike = chain.get("atm_strike")
    rows = chain.get("rows") or []
    if atm_strike is None:
        return None
    return next((r for r in rows if r.get("strike") == atm_strike), None)


def _atm_iv(chain: dict) -> Optional[float]:
    """Average of the ATM CE/PE IVs (decimal, e.g. 0.15). ``None`` if neither
    leg carries an IV."""
    row = _atm_row(chain)
    if not row:
        return None
    ivs = [
        float(row[s]["iv"])
        for s in ("ce", "pe")
        if row.get(s) and row[s].get("iv")
    ]
    if not ivs:
        return None
    return sum(ivs) / len(ivs)


def _atm_straddle(chain: dict) -> Optional[float]:
    """ATM CE+PE mid price, or ``None`` when neither leg is quoted."""
    row = _atm_row(chain)
    if not row:
        return None
    straddle = _mid(row.get("ce")) + _mid(row.get("pe"))
    return straddle if straddle > 0 else None


@dataclass(frozen=True)
class ImpliedMove:
    """Option-implied expected move for one underlying over one horizon.

    ``source`` records how it was derived: ``"iv"`` (Forward×IV×√T off the
    ATM-IV) or ``"straddle"`` (0.85 × ATM straddle fallback). ``t_years`` is the
    horizon actually used (chain DTE, or the rescaled ``horizon_days``)."""

    underlying: str
    expiry: Optional[str]
    forward: float
    atm_strike: float
    atm_iv: Optional[float]
    t_years: float
    expected_move_abs: float
    expected_move_pct: float
    low: float
    high: float
    straddle_price: Optional[float]
    source: str
    asof: Optional[str]


def implied_move_from_chain(
    chain: dict,
    *,
    horizon_days: Optional[int] = None,
) -> Optional[ImpliedMove]:
    """Build an :class:`ImpliedMove` from an already-fetched option-chain dict.

    Pure helper (NO DB) so callers that already hold a chain (e.g. the chat
    F&O path) don't re-fetch. Reads ``forward`` / ``atm_strike`` / ``t_years``
    / ``expected_move`` / ``rows`` off ``chain``. When ``horizon_days`` is set
    and differs from the chain's DTE, rescales the move by √(horizon/DTE).
    Returns ``None`` when ``expected_move`` is absent (no fabrication).
    """
    if not chain:
        return None

    forward = chain.get("forward")
    if not forward or forward <= 0:
        return None
    forward = float(forward)

    atm_strike = chain.get("atm_strike")
    if atm_strike is None:
        return None
    atm_strike = float(atm_strike)

    t_chain = chain.get("t_years")
    t_chain = float(t_chain) if t_chain else 0.0

    atm_iv = _atm_iv(chain)
    straddle_price = _atm_straddle(chain)

    # Base 1σ expected move to the chain's own expiry (DTE = t_chain × 365).
    # Reuse the chain's pre-computed block first (it already solved IV→EM or
    # the straddle fallback); only re-derive when the chain left it absent.
    em_block = chain.get("expected_move")
    em_base: Optional[float] = None
    if em_block and em_block.get("abs"):
        em_base = float(em_block["abs"])
    elif atm_iv is not None and t_chain > 0:
        em_base = forward * atm_iv * math.sqrt(t_chain)
    elif straddle_price is not None:
        em_base = _STRADDLE_EM_MULT * straddle_price

    if em_base is None or em_base <= 0:
        return None

    # Source reflects the derivation path: IV-driven when an ATM IV exists,
    # else the straddle fallback.
    source = "iv" if atm_iv is not None else "straddle"

    # Horizon √t rescale off the chain's DTE. Without a usable DTE we cannot
    # rescale, so we keep the chain-expiry move and its t_years.
    t_used = t_chain
    em = em_base
    if horizon_days is not None and horizon_days > 0:
        dte_days = t_chain * _DAYS_PER_YEAR
        if dte_days > 0:
            em = em_base * math.sqrt(horizon_days / dte_days)
            t_used = horizon_days / _DAYS_PER_YEAR

    return ImpliedMove(
        underlying=str(chain.get("underlying") or "").upper(),
        expiry=chain.get("expiry"),
        forward=round(forward, 4),
        atm_strike=atm_strike,
        atm_iv=atm_iv,
        t_years=t_used,
        expected_move_abs=round(em, 4),
        expected_move_pct=round(em / forward * 100.0, 4),
        low=round(forward - em, 4),
        high=round(forward + em, 4),
        straddle_price=round(straddle_price, 4) if straddle_price is not None else None,
        source=source,
        asof=chain.get("asof"),
    )


def implied_move(
    db: "Session",
    underlying: str,
    *,
    expiry: Optional[str] = None,
    horizon_days: Optional[int] = None,
    width: int = 10,
) -> Optional[ImpliedMove]:
    """Fetch the chain and return the option-implied expected move.

    Calls ``option_chain.get_chain(db, underlying, expiry, width=width)`` then
    delegates to :func:`implied_move_from_chain`. ``expiry`` ``None`` uses the
    chain's nearest expiry; ``horizon_days`` rescales off that expiry's DTE.
    Returns ``None`` when the underlying/expiry isn't in the master or the chain
    has no usable IV/straddle.
    """
    from backend.market.option_chain import get_chain

    chain = get_chain(db, underlying, expiry, width=width)
    if not chain:
        return None
    return implied_move_from_chain(chain, horizon_days=horizon_days)


def implied_probability(
    db: "Session",
    underlying: str,
    *,
    target_level: float,
    direction: str = "above",
    expiry: Optional[str] = None,
    horizon_days: Optional[int] = None,
    width: int = 10,
) -> Optional[float]:
    """Risk-neutral option-implied P(price clears ``target_level``) by horizon.

    ``direction`` ∈ {"above", "below"}. Computes the lognormal-forward
    probability off the ATM IV via ``option_strategies._prob_above`` (below =
    1 − above). Returns a probability in [0, 1], or ``None`` when IV/T are
    unavailable. This is the user-facing implied probability the PROGA caveat
    requires us to surface INSTEAD of any prediction-market odds.
    """
    from backend.services.option_strategies import _prob_above

    if not target_level or target_level <= 0:
        return None

    move = implied_move(
        db, underlying, expiry=expiry, horizon_days=horizon_days, width=width
    )
    # A risk-neutral probability needs a real ATM IV and a positive horizon;
    # the straddle fallback (no IV) cannot price a tail, so degrade to ``None``.
    if move is None or move.atm_iv is None or move.t_years <= 0:
        return None

    p_above = _prob_above(
        float(target_level), move.forward, move.atm_iv, move.t_years
    )
    p = p_above if direction == "above" else 1.0 - p_above
    return float(min(max(p, 0.0), 1.0))


__all__ = [
    "ImpliedMove",
    "implied_move_from_chain",
    "implied_move",
    "implied_probability",
]
