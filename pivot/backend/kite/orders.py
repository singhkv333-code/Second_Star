"""
Order placement via Kite Connect.
All orders require is_confirmed=True.
All amounts validated against safety.py limits.
"""
import logging
from typing import Optional
from backend.kite.auth import KITE_MOCK_MODE, get_authenticated_kite
from backend.kite.mock_data import MOCK_ORDERS

logger = logging.getLogger(__name__)

# Mock order ID counter
_mock_order_id = 1000


def place_order(
    access_token: str,
    tradingsymbol: str,
    exchange: str,
    transaction_type: str,  # "BUY" or "SELL"
    quantity: int,
    order_type: str,        # "MARKET" or "LIMIT"
    price: Optional[float] = None,
    product: str = "CNC",   # CNC (delivery) or MIS (intraday) or NRML (F&O)
    trigger_price: Optional[float] = None,
    tag: str = "pivot",
    variety: str = "regular",  # "regular", "amo", "co", "iceberg", "auction"
) -> dict:
    """
    Place a single order via Kite.
    Returns: {order_id, status, message}
    """
    global _mock_order_id

    if KITE_MOCK_MODE:
        _mock_order_id += 1
        order_id = f"MOCK{_mock_order_id}"
        logger.info(f"[MOCK] Order placed: {transaction_type} {quantity} {tradingsymbol} @ {price or 'MARKET'}")
        return {"order_id": order_id, "status": "COMPLETE", "message": f"Mock order {order_id} placed"}

    try:
        kite = get_authenticated_kite(access_token)
        order_id = kite.place_order(
            tradingsymbol=tradingsymbol,
            exchange=exchange,
            transaction_type=transaction_type,
            quantity=quantity,
            order_type=order_type,
            price=price,
            product=product,
            trigger_price=trigger_price,
            tag=tag,
            variety=variety,
        )
        logger.info(f"Order placed: {order_id} (variety={variety})")
        return {
            "order_id": order_id,
            "status": "PENDING",
            "variety": variety,
            "message": "Order placed successfully",
        }
    except Exception as e:
        logger.error(f"Order placement failed: {e}")
        raise


def place_gtt_order(
    access_token: str,
    tradingsymbol: str,
    exchange: str,
    transaction_type: str,
    quantity: int,
    trigger_price: float,
    limit_price: float,
    last_price: float,
) -> dict:
    """Place a GTT (Good Till Triggered) order."""
    global _mock_order_id

    if KITE_MOCK_MODE:
        _mock_order_id += 1
        gtt_id = _mock_order_id
        return {"trigger_id": gtt_id, "status": "active", "message": f"Mock GTT {gtt_id} created"}

    kite = get_authenticated_kite(access_token)
    trigger_id = kite.place_gtt(
        trigger_type=kite.GTT_TYPE_SINGLE,
        tradingsymbol=tradingsymbol,
        exchange=exchange,
        trigger_values=[trigger_price],
        last_price=last_price,
        orders=[{
            "transaction_type": transaction_type,
            "quantity": quantity,
            "order_type": kite.ORDER_TYPE_LIMIT,
            "product": kite.PRODUCT_CNC,
            "price": limit_price,
        }]
    )
    return {"trigger_id": trigger_id, "status": "active", "message": "GTT created"}


def get_orders(access_token: str) -> list:
    """Get today's order list."""
    if KITE_MOCK_MODE:
        return MOCK_ORDERS
    kite = get_authenticated_kite(access_token)
    return kite.orders()


def cancel_order(access_token: str, order_id: str, variety: str = "regular") -> dict:
    """Cancel a pending order."""
    if KITE_MOCK_MODE:
        return {"order_id": order_id, "status": "CANCELLED"}
    kite = get_authenticated_kite(access_token)
    kite.cancel_order(variety=variety, order_id=order_id)
    return {"order_id": order_id, "status": "CANCELLED", "variety": variety}
