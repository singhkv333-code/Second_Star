from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session
from backend.database import get_db
from backend.models import User, ProductPosition
from backend.auth.jwt_handler import get_user_id_from_token
from backend.kite.portfolio import get_holdings, get_portfolio_summary, get_margins
from backend.services.portfolio_cache import (
    get_summary_cached, get_holdings_cached,
)
from backend.agents.yield_scanner import get_all_yields, calculate_after_tax_yield
import json

router = APIRouter(prefix="/portfolio", tags=["Portfolio"])

SECTOR_MAP = {
    "INFY": "IT", "TCS": "IT", "WIPRO": "IT", "HCLTECH": "IT",
    "HDFCBANK": "Banking", "ICICIBANK": "Banking", "SBIN": "Banking",
    "KOTAKBANK": "Banking", "AXISBANK": "Banking",
    "RELIANCE": "Energy", "ONGC": "Energy", "NTPC": "Energy",
    "TATAMOTORS": "Auto", "MARUTI": "Auto", "BAJAJ-AUTO": "Auto",
    "HAL": "Defence", "BEL": "Defence", "BHEL": "Defence",
    "NIFTYBEES": "Index ETF", "GOLDBEES": "Gold ETF",
    "NESTLEIND": "FMCG", "HINDUNILVR": "FMCG",
}


def get_user_id(authorization: str = Header(None)) -> int:
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing token")
    uid = get_user_id_from_token(authorization.replace("Bearer ", ""))
    if not uid:
        raise HTTPException(status_code=401, detail="Invalid token")
    return uid


def get_kite_token(user_id: int, db: Session) -> str:
    user = db.query(User).filter(User.id == user_id).first()
    if user and user.kite_session and user.kite_session.access_token:
        return user.kite_session.access_token
    return "mock_token"


@router.get("/summary")
def portfolio_summary(user_id: int = Depends(get_user_id), db: Session = Depends(get_db)):
    token = get_kite_token(user_id, db)
    # WHY cached: dashboard polls + chat reads share this endpoint;
    # 30s TTL collapses bursts.
    return get_summary_cached(user_id, token)


@router.get("/holdings")
def portfolio_holdings(user_id: int = Depends(get_user_id), db: Session = Depends(get_db)):
    token = get_kite_token(user_id, db)
    holdings = list(get_holdings_cached(user_id, token))
    # Enrich with sector data (mutates the cached list — copy first
    # so we don't pollute the cached payload across requests).
    holdings = [dict(h) for h in holdings]
    for h in holdings:
        h["sector"] = SECTOR_MAP.get(h["tradingsymbol"], "Other")
    return holdings


@router.get("/sector")
def sector_breakdown(user_id: int = Depends(get_user_id), db: Session = Depends(get_db)):
    token = get_kite_token(user_id, db)
    holdings = get_holdings_cached(user_id, token)
    sector_totals = {}
    total_value = 0
    for h in holdings:
        sector = SECTOR_MAP.get(h["tradingsymbol"], "Other")
        value = h["last_price"] * h["quantity"]
        sector_totals[sector] = sector_totals.get(sector, 0) + value
        total_value += value
    return {
        "sectors": [{"sector": s, "value": round(v, 2),
                     "pct": round(v / total_value * 100, 1) if total_value else 0}
                    for s, v in sorted(sector_totals.items(), key=lambda x: -x[1])],
        "total_value": round(total_value, 2),
        "is_concentrated": any(v / total_value > 0.40 for v in sector_totals.values()) if total_value else False,
    }


@router.get("/products")
def active_products(user_id: int = Depends(get_user_id), db: Session = Depends(get_db)):
    products = (db.query(ProductPosition)
                .filter(ProductPosition.user_id == user_id, ProductPosition.status == "active")
                .all())
    return [{"id": p.id, "product_type": p.product_type, "display_name": p.display_name,
             "capital_deployed": p.capital_deployed, "maturity_date": p.maturity_date.isoformat() if p.maturity_date else None,
             "status": p.status} for p in products]


@router.get("/yields")
async def yield_comparison(user_id: int = Depends(get_user_id), tax_slab: float = 0.30):
    yields = await get_all_yields()
    result = []
    for instrument, gross in yields.items():
        after_tax = calculate_after_tax_yield(gross, instrument, tax_slab)
        result.append({
            "instrument": instrument.replace("_", " ").title(),
            "key": instrument,
            "gross_yield_pct": round(gross * 100, 2),
            "after_tax_yield_pct": round(after_tax * 100, 2),
            "tax_slab_used": tax_slab,
        })
    result.sort(key=lambda x: -x["after_tax_yield_pct"])
    result[0]["is_best"] = True
    return result
