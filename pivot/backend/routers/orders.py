from fastapi import APIRouter, Depends, HTTPException, status, Header
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime, timezone
from backend.database import get_db
from backend.models import TradeLog, User
from backend.auth.jwt_handler import get_user_id_from_token
from backend.kite import orders as kite_orders
from backend.kite import portfolio as kite_portfolio
from backend.agents.explainer import explain_order
from backend.safety import validate_order_value, is_market_open, REQUIRE_CONFIRMATION
from backend.utils.time_utils import format_ist, now_ist
import logging

router = APIRouter(prefix="/orders", tags=["Orders"])
logger = logging.getLogger(__name__)


class OrderPreviewRequest(BaseModel):
    tradingsymbol: str = Field(..., description="NSE ticker e.g. INFY")
    exchange: str = Field(default="NSE")
    transaction_type: str = Field(..., description="BUY or SELL")
    quantity: int = Field(..., gt=0)
    order_type: str = Field(default="MARKET", description="MARKET or LIMIT")
    price: Optional[float] = Field(default=None)
    product: str = Field(default="CNC")


class OrderConfirmRequest(BaseModel):
    preview_id: str
    is_confirmed: bool = Field(..., description="Must be True to execute")


class GTTOrderRequest(BaseModel):
    tradingsymbol: str
    exchange: str = "NSE"
    transaction_type: str
    quantity: int = Field(..., gt=0)
    trigger_price: float
    limit_price: float
    last_price: float
    is_confirmed: bool = False


def get_current_user_token(authorization: str = Header(None)) -> tuple:
    """Extract user_id and token from Authorization header."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid token")
    token = authorization.replace("Bearer ", "")
    user_id = get_user_id_from_token(token)
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return user_id, token


# In-memory preview store (replace with Redis in production)
_preview_store = {}


@router.post("/preview")
async def preview_order(
    request: OrderPreviewRequest,
    auth: tuple = Depends(get_current_user_token),
    db: Session = Depends(get_db),
):
    """
    Preview an order — shows explanation and cost without executing.
    Returns preview_id for confirmation.
    """
    user_id, token = auth

    if request.price:
        is_valid, error = validate_order_value(request.quantity, request.price)
        if not is_valid:
            raise HTTPException(status_code=400, detail=error)

    estimated_price = request.price or 100.0  # use market price estimate
    explanation = await explain_order(
        request.tradingsymbol, request.transaction_type,
        request.quantity, estimated_price
    )

    preview_id = f"prev_{user_id}_{datetime.now().timestamp()}"
    _preview_store[preview_id] = {
        "user_id": user_id,
        "request": request.dict(),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    return {
        "preview_id": preview_id,
        "explanation": explanation,
        "order_details": {
            "symbol": request.tradingsymbol,
            "action": request.transaction_type,
            "quantity": request.quantity,
            "estimated_value": round(request.quantity * estimated_price, 2),
        },
        "is_confirmed": False,
        "disclaimer": "Review carefully. This order will execute immediately upon confirmation.",
    }


@router.post("/confirm")
async def confirm_order(
    request: OrderConfirmRequest,
    authorization: str = Header(None),
    db: Session = Depends(get_db),
):
    """Execute an order after user confirms the preview."""
    if not request.is_confirmed:
        raise HTTPException(status_code=400, detail="is_confirmed must be True to execute")

    if not authorization:
        raise HTTPException(status_code=401, detail="Missing token")
    token = authorization.replace("Bearer ", "")
    user_id = get_user_id_from_token(token)
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token")

    preview = _preview_store.get(request.preview_id)
    if not preview or preview["user_id"] != user_id:
        raise HTTPException(status_code=404, detail="Preview not found or expired")

    req = preview["request"]

    # Get user's Kite access token
    user = db.query(User).filter(User.id == user_id).first()
    kite_token = "mock_token"
    if user and user.kite_session:
        kite_token = user.kite_session.access_token

    result = kite_orders.place_order(
        access_token=kite_token,
        tradingsymbol=req["tradingsymbol"],
        exchange=req["exchange"],
        transaction_type=req["transaction_type"],
        quantity=req["quantity"],
        order_type=req["order_type"],
        price=req.get("price"),
        product=req.get("product", "CNC"),
    )

    # Log to DB — store placed_at as IST-aware datetime
    trade_log = TradeLog(
        user_id=user_id,
        kite_order_id=result.get("order_id"),
        symbol=req["tradingsymbol"],
        exchange=req["exchange"],
        transaction_type=req["transaction_type"],
        order_type=req["order_type"],
        quantity=req["quantity"],
        price=req.get("price"),
        status=result.get("status", "PENDING"),
        source="chat",
        placed_at=now_ist(),
    )
    db.add(trade_log)
    db.commit()

    del _preview_store[request.preview_id]

    confirmed_at_ist = format_ist(now_ist())
    return {
        **result,
        "confirmed_at": confirmed_at_ist,
        "message": (
            f"Order placed at {confirmed_at_ist}. "
            f"Order ID: {result.get('order_id', '—')}."
        ),
    }


@router.get("/history")
def get_order_history(
    auth: tuple = Depends(get_current_user_token),
    db: Session = Depends(get_db),
    limit: int = 20,
):
    """Get recent order history for the current user."""
    user_id, _ = auth
    trades = (db.query(TradeLog)
              .filter(TradeLog.user_id == user_id)
              .order_by(TradeLog.placed_at.desc())
              .limit(limit).all())
    return [{"id": t.id, "symbol": t.symbol, "action": t.transaction_type,
             "quantity": t.quantity, "status": t.status,
             "placed_at": format_ist(t.placed_at)}
            for t in trades]


@router.post("/gtt")
async def create_gtt_order(
    request: GTTOrderRequest,
    auth: tuple = Depends(get_current_user_token),
    db: Session = Depends(get_db),
):
    """Create a GTT (Good Till Triggered) order."""
    user_id, token = auth
    if not request.is_confirmed:
        return {
            "preview": True,
            "message": f"GTT: {request.transaction_type} {request.quantity} {request.tradingsymbol} "
                       f"when price hits ₹{request.trigger_price}",
            "is_confirmed": False,
        }
    user = db.query(User).filter(User.id == user_id).first()
    kite_token = user.kite_session.access_token if user and user.kite_session else "mock"
    return kite_orders.place_gtt_order(
        access_token=kite_token,
        tradingsymbol=request.tradingsymbol,
        exchange=request.exchange,
        transaction_type=request.transaction_type,
        quantity=request.quantity,
        trigger_price=request.trigger_price,
        limit_price=request.limit_price,
        last_price=request.last_price,
    )
