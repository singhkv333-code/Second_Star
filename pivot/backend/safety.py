"""
Non-overridable safety constants.
These are NEVER exposed as API parameters.
NEVER modify these limits without explicit discussion.
"""
from datetime import datetime
import pytz

MAX_SINGLE_ORDER_VALUE = 500_000      # ₹5,00,000 per single order
MAX_DAILY_ORDERS = 20                 # Max orders per user per day
MAX_DAILY_SPEND = 1_000_000           # ₹10,00,000 per day across all orders
REQUIRE_CONFIRMATION = True           # LogicCard confirmation always required
UNDO_WINDOW_SECONDS = 30              # Seconds user can cancel after confirm
MIN_CAPITAL_SAFEGROW = 10_000         # Min ₹10,000 for structured products
MAX_CAPITAL_SAFEGROW = 5_000_000      # Max ₹50,00,000 per structured product
MAX_STRATEGY_BUDGET = 200_000         # Max ₹2,00,000 per automated strategy
MARKET_HOURS_ONLY = True              # Orders only during 9:15-15:30 IST weekdays


def is_market_open() -> bool:
    """Check if NSE market is currently open."""
    ist = pytz.timezone("Asia/Kolkata")
    now = datetime.now(ist)
    if now.weekday() >= 5:
        return False
    market_open = now.replace(hour=9, minute=15, second=0, microsecond=0)
    market_close = now.replace(hour=15, minute=30, second=0, microsecond=0)
    return market_open <= now <= market_close


def validate_order_value(quantity: int, price: float) -> tuple[bool, str]:
    """Returns (is_valid, error_message)."""
    value = quantity * price
    if value > MAX_SINGLE_ORDER_VALUE:
        return False, f"Order value ₹{value:,.0f} exceeds limit of ₹{MAX_SINGLE_ORDER_VALUE:,.0f}"
    if value <= 0:
        return False, "Order value must be positive"
    return True, ""
