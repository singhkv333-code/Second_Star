"""
Structured product builder — assembles synthetic financial products.
Core of the Pivot platform. Calculates leg sizes, generates payoff tables.
"""
import asyncio
import logging
from typing import Optional
from backend.agents.sizer import fetch_current_arb_yield, calculate_safety_leg, calculate_payoff_table
from backend.agents.explainer import explain_strategy
from backend.kite.market_data import get_nifty_level
from backend.safety import MIN_CAPITAL_SAFEGROW, MAX_CAPITAL_SAFEGROW

logger = logging.getLogger(__name__)

NIFTY_LOT_SIZE = 75  # NSE mandated lot size for Nifty 50 options
OPTION_PREMIUM_ESTIMATES = {
    # Strike: (6_month_call_premium, 12_month_call_premium, 6_month_put_premium)
    # Approximate premiums per lot at ATM — update from live option chain
    "atm_6m_call": 3200,
    "atm_12m_call": 5800,
    "atm_6m_put": 2900,
    "otm5_1m_call": 420,   # 5% OTM, 1 month — for covered call EarnMore
}


async def build_safegrow(
    capital: float,
    horizon_months: int = 12,
    tax_slab: float = 0.30,
) -> dict:
    """
    Build SafeGrow — Capital Guarantee Note.
    Safety leg: Arbitrage fund (returns capital at maturity)
    Growth leg: Nifty ATM Call Option (profits if Nifty rises)
    """
    if not (MIN_CAPITAL_SAFEGROW <= capital <= MAX_CAPITAL_SAFEGROW):
        raise ValueError(f"Capital must be between ₹{MIN_CAPITAL_SAFEGROW:,} and ₹{MAX_CAPITAL_SAFEGROW:,}")

    arb_yield, nifty_level = await asyncio.gather(
        fetch_current_arb_yield(),
        asyncio.get_event_loop().run_in_executor(None, get_nifty_level),
    )

    safety_leg = calculate_safety_leg(capital, arb_yield, horizon_months)
    growth_leg = round(capital - safety_leg, 2)

    # How many Nifty lots can the growth leg buy?
    premium_per_lot = OPTION_PREMIUM_ESTIMATES["atm_12m_call" if horizon_months >= 10 else "atm_6m_call"]
    lots = max(1, int(growth_leg / premium_per_lot))
    actual_growth_spend = lots * premium_per_lot
    buffer = round(growth_leg - actual_growth_spend, 2)

    payoff = calculate_payoff_table(capital, actual_growth_spend, "safegrow", nifty_level, lots)
    explanation = await explain_strategy(
        "SafeGrow - Capital Guarantee Note",
        capital, safety_leg, actual_growth_spend,
        "Mirae Asset Arbitrage Fund (Direct)",
        f"Nifty 50 ATM Call Option ({lots} lot{'s' if lots > 1 else ''})",
        arb_yield * 100, horizon_months,
    )

    return {
        "product_type": "safegrow",
        "display_name": "SafeGrow — Capital Guarantee Note",
        "capital": capital,
        "horizon_months": horizon_months,
        "arb_yield_pct": round(arb_yield * 100, 2),
        "legs": [
            {"label": "Safety Leg", "type": "safety",
             "instrument": "Mirae Asset Arbitrage Fund (Direct)",
             "instrument_type": "mutual_fund",
             "amount": safety_leg,
             "expected_return": f"Returns ₹{capital:,.0f} at maturity"},
            {"label": "Growth Leg", "type": "growth",
             "instrument": f"Nifty 50 ATM Call @ ₹{nifty_level:,.0f} strike ({lots} lot{'s' if lots > 1 else ''})",
             "instrument_type": "call_option",
             "amount": actual_growth_spend,
             "lots": lots,
             "buffer_to_liquid_fund": buffer},
        ],
        "payoff_table": payoff,
        "explanation": explanation,
        "nifty_reference_level": nifty_level,
        "disclaimer": "This is automation of your instructions, not financial advice. Capital guarantee depends on arbitrage fund performance. Past performance does not guarantee future results.",
    }


async def build_earnmore(capital: float, tax_slab: float = 0.30) -> dict:
    """
    EarnMore — Covered Call Income Engine.
    Hold Nifty BeES ETF. Sell monthly 5% OTM call. Collect premium.
    """
    nifty_level = await asyncio.get_event_loop().run_in_executor(None, get_nifty_level)
    etf_price = nifty_level / 100  # Nifty BeES ~ Nifty/100
    units = int(capital / etf_price)
    etf_value = round(units * etf_price, 2)

    lots = max(1, units // 75)
    monthly_premium = lots * OPTION_PREMIUM_ESTIMATES["otm5_1m_call"]
    annual_premium = monthly_premium * 12
    premium_yield_pct = (annual_premium / capital) * 100

    payoff = calculate_payoff_table(capital, monthly_premium, "earnmore", nifty_level, lots)

    return {
        "product_type": "earnmore",
        "display_name": "EarnMore — Monthly Income Engine",
        "capital": capital,
        "legs": [
            {"label": "ETF Holding", "type": "core",
             "instrument": f"Nifty BeES ETF ({units} units @ ₹{etf_price:.2f})",
             "instrument_type": "etf", "amount": etf_value},
            {"label": "Monthly Call Sale", "type": "income",
             "instrument": f"Nifty Call Option 5% OTM ({lots} lot{'s' if lots > 1 else ''}) — sold monthly",
             "instrument_type": "call_option_short",
             "monthly_premium": monthly_premium,
             "annual_premium": annual_premium},
        ],
        "income_summary": {
            "monthly_income": monthly_premium,
            "annual_income": annual_premium,
            "premium_yield_pct": round(premium_yield_pct, 2),
        },
        "payoff_table": payoff,
        "disclaimer": "This is automation of your instructions, not financial advice. Covered calls cap your upside. If market rises sharply above the strike, gains are limited.",
    }


async def build_stormshield(capital: float, horizon_months: int = 6) -> dict:
    """
    StormShield — Inverse Capital Guarantee (Bear Note).
    Safety leg: Arbitrage fund. Growth leg: Nifty Put Option.
    Profits if Nifty falls. Capital returned if Nifty rises.
    """
    arb_yield, nifty_level = await asyncio.gather(
        fetch_current_arb_yield(),
        asyncio.get_event_loop().run_in_executor(None, get_nifty_level),
    )

    safety_leg = calculate_safety_leg(capital, arb_yield, horizon_months)
    growth_leg = round(capital - safety_leg, 2)
    premium_per_lot = OPTION_PREMIUM_ESTIMATES["atm_6m_put"]
    lots = max(1, int(growth_leg / premium_per_lot))
    actual_spend = lots * premium_per_lot

    payoff = calculate_payoff_table(capital, actual_spend, "stormshield", nifty_level, lots)

    return {
        "product_type": "stormshield",
        "display_name": "StormShield — Bear Protection Note",
        "capital": capital,
        "horizon_months": horizon_months,
        "risk_warning": "⚠️ This product LOSES the growth leg if markets RISE. Only for investors with bearish conviction.",
        "legs": [
            {"label": "Safety Leg", "type": "safety",
             "instrument": "Arbitrage Fund", "amount": safety_leg},
            {"label": "Bear Leg", "type": "growth",
             "instrument": f"Nifty 50 ATM Put @ ₹{nifty_level:,.0f} strike ({lots} lots)",
             "instrument_type": "put_option", "amount": actual_spend},
        ],
        "payoff_table": payoff,
        "disclaimer": "This is automation of your instructions, not financial advice. High risk product — only for investors with specific bearish market view.",
    }


PRODUCT_BUILDERS = {
    "safegrow": build_safegrow,
    "earnmore": build_earnmore,
    "stormshield": build_stormshield,
}
