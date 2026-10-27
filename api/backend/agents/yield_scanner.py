"""
Yield scanner — fetches and compares yields across instruments daily.
No API keys needed — uses mfapi.in (free), yfinance (free), static FD rates.
"""
import httpx
import logging
import asyncio
from backend.cache import redis_client

logger = logging.getLogger(__name__)

FD_RATES = {
    "SBI": {"1y": 6.80, "2y": 7.00, "3y": 7.00},
    "HDFC Bank": {"1y": 7.10, "2y": 7.15, "3y": 7.25},
    "ICICI Bank": {"1y": 7.10, "2y": 7.15, "3y": 7.25},
    "Axis Bank": {"1y": 7.10, "2y": 7.26, "3y": 7.26},
}

MF_SCHEME_CODES = {
    "overnight_fund": "120842",   # Nippon India Overnight Fund
    "liquid_fund": "120594",      # HDFC Liquid Fund
    "arbitrage_fund": "119551",   # Mirae Asset Arbitrage Fund
    "short_duration": "100033",   # HDFC Short Duration Fund
}


async def fetch_mf_yield(scheme_code: str, days: int = 365) -> float:
    """Fetch annualised yield for a mutual fund from mfapi.in."""
    cache_key = f"yield:mf:{scheme_code}"
    cached = redis_client.get(cache_key)
    if cached:
        return float(cached)
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(f"https://api.mfapi.in/mf/{scheme_code}")
            r.raise_for_status()
            navs = r.json()["data"]
        if len(navs) < days:
            return 0.065
        latest = float(navs[0]["nav"])
        old = float(navs[min(days-1, len(navs)-1)]["nav"])
        yield_val = ((latest / old) ** (365 / days)) - 1
        if 0.03 <= yield_val <= 0.15:
            redis_client.set(cache_key, str(yield_val), ex=3600)
            return yield_val
    except Exception as e:
        logger.warning(f"mfapi fetch failed for {scheme_code}: {e}")
    return 0.065


async def get_all_yields() -> dict:
    """Fetch yields for all instruments simultaneously."""
    overnight, liquid, arbitrage, short_dur = await asyncio.gather(
        fetch_mf_yield(MF_SCHEME_CODES["overnight_fund"], 30),
        fetch_mf_yield(MF_SCHEME_CODES["liquid_fund"], 90),
        fetch_mf_yield(MF_SCHEME_CODES["arbitrage_fund"], 365),
        fetch_mf_yield(MF_SCHEME_CODES["short_duration"], 365),
    )
    return {
        "savings_account": 0.035,
        "overnight_fund": round(overnight, 4),
        "liquid_fund": round(liquid, 4),
        "arbitrage_fund": round(arbitrage, 4),
        "short_duration_fund": round(short_dur, 4),
        "fd_1y_best": max(v["1y"] for v in FD_RATES.values()) / 100,
        "gsec_10y": 0.072,  # approximate, update from RBI weekly
        "rbi_repo_rate": 0.065,
    }


def calculate_after_tax_yield(gross_yield: float, instrument: str, tax_slab: float) -> float:
    """Calculate after-tax yield based on instrument and user's tax slab."""
    if instrument in ["arbitrage_fund"]:
        # Equity taxation: 15% STCG (held < 1 year), 10% LTCG (held > 1 year)
        return gross_yield * (1 - 0.15)
    elif instrument in ["savings_account", "fd_1y_best", "overnight_fund", "liquid_fund", "short_duration_fund"]:
        # Taxed at slab rate
        return gross_yield * (1 - tax_slab)
    elif instrument == "gsec_10y":
        # Taxed at slab rate for < 3 years, 20% with indexation for > 3 years
        return gross_yield * (1 - tax_slab)
    return gross_yield
