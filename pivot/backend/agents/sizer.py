"""
Leg sizer — calculates exact rupee amounts for each leg of synthetic products.
Core formula: safety_leg = capital / (1 + yield)^(horizon_years)
"""
import httpx
import logging
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
