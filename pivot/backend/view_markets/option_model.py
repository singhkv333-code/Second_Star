"""A small, self-contained Black–Scholes model for the View Markets OPTION tier.

Why this exists
---------------
The options an aggressive View expression uses (defined-risk vertical spreads)
have **no faithful historical price path offline** — there is no stored option
chain to backtest against — so ``precompute`` honestly leaves their historical
return as *"priced at deploy"* and lets the curve ride the underlying.

But we CAN model the **payoff shape** of a defined-risk vertical the moment it is
deployed: given the underlying's realised volatility (real, from the returns
matrix) and a horizon, Black–Scholes prices each leg and yields a REAL
max-loss / max-profit / breakeven / probability-of-profit / net-greeks and a
payoff curve. That is standard option maths, not a fabricated backtest — so it is
honest to show it (clearly labelled "modelled at reference vol; final strikes and
premia are set at deploy").

Everything is expressed on a **normalised underlying of 100** (strikes are
percentage-moneyness) and the payoff is returned as **% of the capital deployed**
(the net debit premium = the capital at risk), so the FE calculator can scale the
whole curve by any ₹ amount. No scipy dependency — the normal CDF/PDF use
``math.erf``.

Scope / honesty
---------------
* Only **defined-risk debit verticals** (bull-call / bear-put) are modelled — they
  have a bounded, known max loss (the non-negotiable: aggressive never means an
  unbounded-loss naked leg). ``max_loss`` is ALWAYS the premium.
* POP is the lognormal probability the underlying finishes past the breakeven at
  expiry under the given (real) vol and a stated risk-free rate — a modelling
  assumption, surfaced as such, never presented as a historical win-rate.
"""
from __future__ import annotations

import math
from typing import Any, Optional

# India risk-free proxy (~10y G-sec). Only affects the BS drift/POP slightly; a
# fixed, stated constant keeps the model reproducible offline.
DEFAULT_R = 0.065


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _d1_d2(s: float, k: float, t: float, r: float, sigma: float) -> tuple[float, float]:
    if t <= 0 or sigma <= 0:
        # Degenerate: treat as intrinsic (no time value).
        d1 = math.inf if s > k else (-math.inf if s < k else 0.0)
        return d1, d1
    vt = sigma * math.sqrt(t)
    d1 = (math.log(s / k) + (r + 0.5 * sigma * sigma) * t) / vt
    return d1, d1 - vt


def bs_price(s: float, k: float, t: float, r: float, sigma: float, call: bool) -> float:
    """Black–Scholes price of a European option."""
    d1, d2 = _d1_d2(s, k, t, r, sigma)
    disc = math.exp(-r * t)
    if call:
        return max(0.0, s * _norm_cdf(d1) - k * disc * _norm_cdf(d2))
    return max(0.0, k * disc * _norm_cdf(-d2) - s * _norm_cdf(-d1))


def bs_greeks(s: float, k: float, t: float, r: float, sigma: float, call: bool) -> dict[str, float]:
    """Per-option greeks: delta, gamma, vega (per 1 vol pt), theta (per day)."""
    if t <= 0 or sigma <= 0:
        delta = (1.0 if s > k else 0.0) if call else (-1.0 if s < k else 0.0)
        return {"delta": delta, "gamma": 0.0, "vega": 0.0, "theta": 0.0}
    d1, d2 = _d1_d2(s, k, t, r, sigma)
    disc = math.exp(-r * t)
    pdf = _norm_pdf(d1)
    delta = _norm_cdf(d1) if call else _norm_cdf(d1) - 1.0
    gamma = pdf / (s * sigma * math.sqrt(t))
    vega = s * pdf * math.sqrt(t) / 100.0                     # per 1 vol point
    if call:
        theta = (-s * pdf * sigma / (2 * math.sqrt(t)) - r * k * disc * _norm_cdf(d2)) / 365.0
    else:
        theta = (-s * pdf * sigma / (2 * math.sqrt(t)) + r * k * disc * _norm_cdf(-d2)) / 365.0
    return {"delta": delta, "gamma": gamma, "vega": vega, "theta": theta}


def _terminal_prob_above(s: float, level: float, t: float, r: float, sigma: float) -> float:
    """Lognormal P(S_T > level) at expiry under BS dynamics."""
    if t <= 0 or sigma <= 0:
        return 1.0 if s > level else 0.0
    vt = sigma * math.sqrt(t)
    d = (math.log(s / level) + (r - 0.5 * sigma * sigma) * t) / vt
    return _norm_cdf(d)


def model_vertical_spread(
    *,
    bullish: bool,
    sigma_annual: float,
    horizon_days: int,
    width_pct: float,
    atm_offset_pct: float = 0.0,
    r: float = DEFAULT_R,
    underlying_label: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """Model a defined-risk vertical debit spread on a normalised underlying=100.

    ``bullish`` → bull-call spread (BUY call K1, SELL call K2>K1); else bear-put
    spread (BUY put K1, SELL put K2<K1). ``width_pct`` is the strike separation in
    % of spot; ``atm_offset_pct`` shifts the long strike off ATM (0 = ATM).
    ``sigma_annual`` is the REAL annualised realised vol of the underlying.

    Returns a dict with the premium, bounded max-loss/max-profit, breakeven,
    lognormal POP, net greeks, and a payoff curve expressed as **% of the capital
    deployed** (= the net debit), so the FE can scale it by any ₹ amount. Returns
    ``None`` if the inputs are degenerate (non-positive vol/horizon) — never a
    fabricated shape.
    """
    if sigma_annual <= 0 or horizon_days <= 0 or width_pct <= 0:
        return None

    s = 100.0
    t = horizon_days / 252.0
    sigma = float(sigma_annual)

    if bullish:
        k1 = s * (1.0 + atm_offset_pct / 100.0)               # long call (~ATM)
        k2 = k1 + s * width_pct / 100.0                       # short call (OTM)
        long_leg = ("BUY", "CE", k1)
        short_leg = ("SELL", "CE", k2)
        prem = bs_price(s, k1, t, r, sigma, True) - bs_price(s, k2, t, r, sigma, True)
        breakeven = k1 + prem
        pop = _terminal_prob_above(s, breakeven, t, r, sigma)
        g_long = bs_greeks(s, k1, t, r, sigma, True)
        g_short = bs_greeks(s, k2, t, r, sigma, True)
    else:
        k1 = s * (1.0 - atm_offset_pct / 100.0)               # long put (~ATM)
        k2 = k1 - s * width_pct / 100.0                       # short put (OTM)
        long_leg = ("BUY", "PE", k1)
        short_leg = ("SELL", "PE", k2)
        prem = bs_price(s, k1, t, r, sigma, False) - bs_price(s, k2, t, r, sigma, False)
        breakeven = k1 - prem
        pop = 1.0 - _terminal_prob_above(s, breakeven, t, r, sigma)
        g_long = bs_greeks(s, k1, t, r, sigma, False)
        g_short = bs_greeks(s, k2, t, r, sigma, False)

    width = abs(k2 - k1)
    prem = max(prem, 1e-6)                                     # guard div-by-zero
    max_profit_pts = max(width - prem, 0.0)
    # As % of the capital deployed (the debit premium is 100% of capital at risk).
    max_loss_pct = -100.0
    max_profit_pct = round(max_profit_pts / prem * 100.0, 1)

    def _intrinsic(term: float) -> float:
        """Spread P&L (in points) at terminal underlying ``term``."""
        if bullish:
            payoff = max(term - k1, 0.0) - max(term - k2, 0.0)
        else:
            payoff = max(k1 - term, 0.0) - max(k2 - term, 0.0)
        return payoff - prem

    # Payoff curve over ±25% terminal move, y = P&L as % of deployed capital.
    payoff: list[dict[str, float]] = []
    steps = 51
    for i in range(steps):
        move = -25.0 + 50.0 * i / (steps - 1)
        term = s * (1.0 + move / 100.0)
        pnl_pts = _intrinsic(term)
        payoff.append({
            "move_pct": round(move, 2),
            "pnl_pct": round(pnl_pts / prem * 100.0, 1),
        })

    net_greeks = {
        "delta": round(g_long["delta"] - g_short["delta"], 4),
        "gamma": round(g_long["gamma"] - g_short["gamma"], 5),
        "vega": round(g_long["vega"] - g_short["vega"], 4),
        "theta": round(g_long["theta"] - g_short["theta"], 4),
    }

    def _leg_dict(leg: tuple[str, str, float]) -> dict[str, Any]:
        action, otype, strike = leg
        return {
            "action": action,
            "option_type": otype,
            "strike_pct": round(strike, 1),             # % moneyness (spot=100)
            "strike_label": ("ATM" if abs(strike - 100.0) < 1e-6
                             else f"{strike - 100.0:+.0f}%"),
        }

    return {
        "structure": "bull_call_spread" if bullish else "bear_put_spread",
        "direction": "bullish" if bullish else "bearish",
        "underlying_label": underlying_label,
        "legs": [_leg_dict(long_leg), _leg_dict(short_leg)],
        "net_premium_pct": round(prem, 2),              # premium as % of spot
        "width_pct": round(width, 1),
        "max_loss_pct": max_loss_pct,                   # -100% of capital (the debit)
        "max_profit_pct": max_profit_pct,              # % of capital deployed
        "breakeven_move_pct": round((breakeven - s) / s * 100.0, 2),
        "pop_pct": round(pop * 100.0, 1),
        "net_greeks": net_greeks,
        "vol_used_pct": round(sigma * 100.0, 1),
        "horizon_days": int(horizon_days),
        "payoff": payoff,
        "basis": "modelled_bs",
        "assumptions": (
            "Modelled with Black–Scholes at the underlying's realised volatility "
            "and a stated risk-free rate on a reference spot of 100; final strikes "
            "and premia are set at deploy. Max loss is capped at the debit paid."
        ),
    }


def realized_vol_annual(daily_returns) -> Optional[float]:
    """Annualised realised vol from a daily-return series/iterable. ``None`` if
    fewer than ~20 finite observations (too thin to be meaningful)."""
    vals = [float(x) for x in daily_returns
            if x is not None and math.isfinite(float(x))]
    if len(vals) < 20:
        return None
    mean = sum(vals) / len(vals)
    var = sum((x - mean) ** 2 for x in vals) / (len(vals) - 1)
    return math.sqrt(var) * math.sqrt(252.0)
