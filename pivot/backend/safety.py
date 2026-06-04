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


# ── F&O pre-trade gate (P1; the SINGLE source of truth) ──────────────
#
# Called by BOTH the /option-strategies registration router (P1) and
# the action.place_option_strategy workflow executor (P3). FAIL-CLOSED:
# every check must pass; the first failure blocks with a reason. The
# checklist grows in P2/P3 (margin pre-check vs paper cash, daily-loss
# cap, feed-health, kill-switch); P1 ships the structural checks.

MAX_OPTION_LOTS_PER_STRATEGY = 100   # hard sanity ceiling per strategy
FNO_KILL_SWITCH_ENV = "PIVOT_FNO_KILL_SWITCH"


def run_option_pretrade_gate(payload: dict, *, acknowledged: bool) -> tuple[bool, str]:
    """Validate a SERVER-resolved option_strategy payload (the output of
    ``option_strategies.resolve_strategy``) before persisting. Returns
    (ok, reason). The payload is trusted because the server just built
    it from the live chain — client numbers never reach this gate."""
    import os

    if os.getenv(FNO_KILL_SWITCH_ENV, "0") == "1":
        return False, "F&O registration is temporarily disabled (kill switch)."

    validation = payload.get("validation") or {}
    editable = payload.get("editable") or {}
    legs = editable.get("legs") or []

    if validation.get("mcx_execution_blocked"):
        return False, (
            "Commodity (MCX) options are research-only on Pivot — "
            "execution and registration are blocked."
        )
    if validation.get("requires_disclosure") and not acknowledged:
        return False, (
            "The SEBI risk disclosure must be acknowledged before "
            "registering an F&O strategy."
        )
    if not legs:
        return False, "Strategy has no legs."
    qty = int(editable.get("qty_lots") or 0)
    if qty < 1 or qty > MAX_OPTION_LOTS_PER_STRATEGY:
        return False, (
            f"qty_lots must be between 1 and {MAX_OPTION_LOTS_PER_STRATEGY}."
        )
    # Liquidity: an unquotable leg never reaches here (resolution throws),
    # but wide-spread legs do — registering through a wide spread is
    # allowed with the flag carried on the row; an ILLIQUID leg is not.
    for leg in legs:
        if leg.get("iv_status") in ("illiquid", "stale", "no_arb"):
            return False, (
                f"Leg {leg.get('tradingsymbol') or leg.get('strike')} is not "
                f"tradable right now ({leg.get('iv_status')})."
            )
    # Expiry-day naked shorts are gate-BLOCKED (not just warned) —
    # the single most account-destroying retail pattern. Defined-risk
    # structures on expiry day pass with the card-level warning.
    if (
        validation.get("expiry_gamma_warn")
        and (payload.get("computed") or {}).get("max_loss") is None
    ):
        return False, (
            "Naked short option(s) on expiry day are blocked — add a "
            "protective wing (defined risk) or pick a later expiry."
        )
    return True, ""
