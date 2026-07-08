from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from typing import Optional
from sqlalchemy.orm import Session
from backend.database import get_db
from backend.auth.jwt_handler import get_user_id_from_token
from backend.agents.structured_builder import PRODUCT_BUILDERS, build_safegrow
from backend.safety import MIN_CAPITAL_SAFEGROW

router = APIRouter(prefix="/products", tags=["Structured Products"])


def get_user_id(authorization: str = Header(None)) -> int:
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing token")
    uid = get_user_id_from_token(authorization.replace("Bearer ", ""))
    if not uid:
        raise HTTPException(status_code=401, detail="Invalid token")
    return uid


class ProductPreviewRequest(BaseModel):
    product_type: str = Field(..., description="safegrow, earnmore, stormshield")
    capital: float = Field(..., ge=10000)
    horizon_months: Optional[int] = Field(default=12, ge=1, le=60)
    tax_slab: Optional[float] = Field(default=0.30)


@router.post("/preview")
async def preview_product(request: ProductPreviewRequest, user_id: int = Depends(get_user_id)):
    """Build and preview a synthetic product without executing."""
    builder = PRODUCT_BUILDERS.get(request.product_type)
    if not builder:
        raise HTTPException(status_code=400, detail=f"Unknown product type: {request.product_type}. "
                          f"Available: {list(PRODUCT_BUILDERS.keys())}")
    result = await builder(capital=request.capital, horizon_months=request.horizon_months or 12)
    return result


@router.get("/catalogue")
def get_product_catalogue():
    """Returns all available synthetic products with descriptions."""
    return {
        "products": [
            {"type": "safegrow", "name": "SafeGrow", "tagline": "Your money can only go up or stay flat",
             "category": "Protection", "min_capital": 10000, "risk_level": "Very Low",
             "horizon": "6-24 months"},
            {"type": "earnmore", "name": "EarnMore", "tagline": "Earn monthly income on money you already have",
             "category": "Income", "min_capital": 50000, "risk_level": "Medium",
             "horizon": "Ongoing monthly"},
            {"type": "stormshield", "name": "StormShield", "tagline": "Profit if markets fall, protected if they rise",
             "category": "Protection/Bearish", "min_capital": 10000, "risk_level": "Medium",
             "horizon": "3-6 months"},
            {"type": "ratebet", "name": "RateBet", "tagline": "Positioned to profit when RBI cuts rates",
             "category": "Macro", "min_capital": 50000, "risk_level": "Medium-High",
             "horizon": "6-12 months"},
            {"type": "warbasket", "name": "GeoBasket", "tagline": "Multi-asset geopolitical conviction basket",
             "category": "Macro/High Risk", "min_capital": 25000, "risk_level": "High",
             "horizon": "1-4 weeks"},
        ]
    }
