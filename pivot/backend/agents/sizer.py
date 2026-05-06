"""
Leg sizer — calculates exact rupee amounts for each leg of synthetic products.
Core formula: safety_leg = capital / (1 + yield)^(horizon_years)
"""
import math
import httpx
import logging
from datetime import datetime
from backend.cache import redis_client

logger = logging.getLogger(__name__)

FALLBACK_ARB_YIELD = 0.078  # 7.8% — used if mfapi.in is unavailable
ARB_FUND_SCHEMES = {
    "Mirae Asset Arbitrage Fund": "119551",
    "HDFC Arbitrage Fund": "118701",
    "Nippon India Arbitrage Fund": "118989",
}


async def fetch_current_arb_yield() -> float:
    """
    Fetch current arbitrage fund yield from mfapi.in.
    Caches for 1 hour. Falls back to FALLBACK_ARB_YIELD.
    No API key needed — mfapi.in is free and public.
    """
    cache_key = "yield:arbitrage_fund"
    cached = redis_client.get(cache_key)
    if cached:
        return float(cached)

    try:
        # Use Mirae Asset Arbitrage Fund as reference
        scheme_code = "119551"
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(f"https://api.mfapi.in/mf/{scheme_code}")
            response.raise_for_status()
            data = response.json()

        navs = data["data"][:252]  # ~1 year of NAV data
        if len(navs) < 30:
            return FALLBACK_ARB_YIELD

        latest_nav = float(navs[0]["nav"])
        nav_1y_ago = float(navs[min(251, len(navs)-1)]["nav"])
        annual_yield = (latest_nav / nav_1y_ago) - 1

        # Sanity check — arb funds should yield 6-9%
        if 0.05 <= annual_yield <= 0.12:
            redis_client.set(cache_key, str(annual_yield), ex=3600)
            return annual_yield

    except Exception as e:
        logger.warning(f"mfapi.in fetch failed: {e}. Using fallback yield.")

    return FALLBACK_ARB_YIELD


def calculate_safety_leg(capital: float, arb_yield: float, horizon_months: int) -> float:
    """
    Calculate safety leg amount.
    This is the amount that, invested at arb_yield, returns exactly capital at maturity.
    Formula: safety_leg = capital / (1 + yield)^(horizon_years)
    """
    horizon_years = horizon_months / 12
    safety_leg = capital / ((1 + arb_yield) ** horizon_years)
    return round(safety_leg, 2)


def calculate_payoff_table(
    capital: float,
    growth_leg_amount: float,
    product_type: str,
    nifty_current: float = 23500.0,
    lots: int = 1,
) -> list:
    """
    Calculate payoff at 6 market scenarios.
    Returns list of {scenario, nifty_level, portfolio_value, return_pct}
    """
    scenarios = [-0.30, -0.15, -0.05, 0.0, 0.10, 0.20, 0.30]
    payoff = []

    for pct in scenarios:
        nifty_at_maturity = nifty_current * (1 + pct)

        if product_type in ["safegrow", "capital_guarantee"]:
            # Call option profit: max(0, nifty_final - strike) * 75 * lots
            strike = nifty_current  # ATM
            option_profit = max(0, (nifty_at_maturity - strike) * 75 * lots)
            portfolio_value = capital + option_profit  # safety leg returns full capital

        elif product_type == "stormshield":
            # Put option: max(0, strike - nifty_final) * 75 * lots
            strike = nifty_current
            option_profit = max(0, (strike - nifty_at_maturity) * 75 * lots)
            portfolio_value = capital + option_profit

        elif product_type == "earnmore":
            # Covered call: ETF value - max(0, nifty - strike) * 75 * lots + premium
            etf_value = capital * (1 + pct)
            cap_strike = nifty_current * 1.05  # 5% OTM
            premium_collected = 1260  # ₹1,260/month typical
            call_assignment = max(0, (nifty_at_maturity - cap_strike) * 75 * lots)
            portfolio_value = etf_value - call_assignment + premium_collected

        else:
            portfolio_value = capital * (1 + pct * 0.7)  # generic 70% participation

        return_pct = ((portfolio_value - capital) / capital) * 100
        payoff.append({
            "scenario": f"Nifty {'+' if pct >= 0 else ''}{pct*100:.0f}%",
            "nifty_level": round(nifty_at_maturity, 0),
            "portfolio_value": round(portfolio_value, 2),
            "return_pct": round(return_pct, 2),
        })

    return payoff


# ── Barbell helpers ──────────────────────────────────────────────────────


def calculate_barbell_allocation(
    capital: float,
    gold_price: float,
    equity_price: float,
) -> dict:
    """Initial 50/50 split between GOLDBEES and NIFTYBEES.

    Computes whole-unit holdings (no fractional ETFs on NSE), the rupee
    amount actually deployed per leg, the residual cash float, and the
    realised weights post-rounding.
    """
    target_each = capital * 0.50
    gold_units = max(0, int(target_each // gold_price)) if gold_price > 0 else 0
    equity_units = max(0, int(target_each // equity_price)) if equity_price > 0 else 0
    gold_amount = round(gold_units * gold_price, 2)
    equity_amount = round(equity_units * equity_price, 2)
    deployed = gold_amount + equity_amount
    cash_float = round(capital - deployed, 2)
    total = deployed if deployed > 0 else 1.0
    return {
        "gold_units": gold_units,
        "equity_units": equity_units,
        "gold_amount": gold_amount,
        "equity_amount": equity_amount,
        "gold_weight": round(gold_amount / total, 4),
        "equity_weight": round(equity_amount / total, 4),
        "cash_float": cash_float,
    }


def calculate_rebalance_triggers(
    gold_price: float,
    equity_price: float,
    threshold_pct: float = 60.0,
) -> dict:
    """Price levels at which the 50/50 Barbell hits the rebalance threshold.

    Assumes the *other* leg stays at its current price — i.e. the price the
    triggering leg has to reach in isolation for it to cross the threshold.
    Useful for the rebalance trigger card.
    """
    ratio = (threshold_pct / 100.0) / (1 - threshold_pct / 100.0)
    return {
        "threshold_pct": threshold_pct,
        "gold_up_trigger_price": round(gold_price * ratio, 2),
        "gold_down_trigger_price": round(gold_price / ratio, 2),
        "equity_up_trigger_price": round(equity_price * ratio, 2),
        "equity_down_trigger_price": round(equity_price / ratio, 2),
    }


def project_rebalancing_calendar(
    gold_history: list,
    equity_history: list,
    threshold_pct: float = 60.0,
    lookback_years: int = 3,
) -> dict:
    """Replay the 50/60 rule against historical NAVs and report rebalance freq.

    Each list entry is a dict with at least `close` (sorted oldest→newest).
    Returns avg rebalances per year and a coarse next-window estimate.
    """
    if not gold_history or not equity_history:
        return {
            "avg_rebalances_per_year": None,
            "next_window_estimate":
                "Insufficient history to project a calendar; expect 1–2 "
                "rebalances per year on a 50/60 rule historically.",
            "lookback_years": lookback_years,
        }

    n = min(len(gold_history), len(equity_history))
    threshold = threshold_pct / 100.0
    rebalances = 0
    gold_units = 1.0
    equity_units = 1.0
    # Initialise so each leg is exactly 50% of the starting portfolio.
    g0 = float(gold_history[-n]["close"])
    e0 = float(equity_history[-n]["close"])
    if g0 <= 0 or e0 <= 0:
        return {"avg_rebalances_per_year": None,
                "next_window_estimate": "no data", "lookback_years": lookback_years}
    equity_units = (gold_units * g0) / e0  # equal rupee at t=0

    for i in range(-n, 0):
        g_close = float(gold_history[i]["close"])
        e_close = float(equity_history[i]["close"])
        gold_val = gold_units * g_close
        equity_val = equity_units * e_close
        total = gold_val + equity_val
        if total <= 0:
            continue
        if (gold_val / total) > threshold or (equity_val / total) > threshold:
            rebalances += 1
            half = total / 2.0
            gold_units = half / g_close
            equity_units = half / e_close

    years_observed = max(1.0, n / 252.0)  # ~252 trading days/yr
    avg_per_year = rebalances / years_observed
    if avg_per_year >= 2:
        window = "likely within 6 months"
    elif avg_per_year >= 1:
        window = "likely within 6–12 months"
    else:
        window = "likely beyond 12 months"

    return {
        "avg_rebalances_per_year": round(avg_per_year, 2),
        "next_window_estimate": window,
        "lookback_years": round(years_observed, 1),
        "rebalances_observed": rebalances,
    }
