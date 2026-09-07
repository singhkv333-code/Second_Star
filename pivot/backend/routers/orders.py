from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime, timezone
from backend.database import get_db
from backend.models import TradeLog, User
from backend.auth.jwt_handler import get_user_id_from_token
from backend.paper.routing import (
    InsufficientFundsError,
    should_use_paper,
    submit_gtt_for_user,
    submit_gtt_oco_for_user,
    submit_order_for_user,
)
from backend.brokers.sessions import get_active_broker_session
from backend.paper.marks import get_mark_price
# Imported as a module (not by value) so we read the LIVE mock-mode flag —
# it flips at runtime on broker connect/disconnect.
from backend.kite import auth as kite_auth
from backend.kite.auth import read_kite_access_token
from backend.agents.explainer import explain_order
from backend.posthog_client import get_posthog
from backend.safety import validate_order_value
from backend.utils.time_utils import (
    format_ist,
    is_market_open,
    next_market_open,
    now_ist,
)
from backend.brokers.registry import get_connector
import logging

router = APIRouter(prefix="/orders", tags=["Orders"])
logger = logging.getLogger(__name__)

# TradeLog statuses that represent a still-open order the user can cancel
# before it executes. Everything else (filled / complete / cancelled /
# rejected) is terminal and lives in /orders/history. Compared lower-cased so
# broker-specific casings ("PENDING", "TRIGGER PENDING") all match.
_CANCELLABLE_STATUSES = {
    "queued",
    "pending",
    "registered",
    "open",
    "trigger pending",
    "amo req received",
    "put order req received",
    "validation pending",
}


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
    # Optional chat conversation id so paper fills can be grouped by the
    # conversation that produced them (forward-test attribution, P6).
    conversation_id: Optional[str] = None


class GTTOrderRequest(BaseModel):
    tradingsymbol: str
    exchange: str = "NSE"
    transaction_type: str
    quantity: int = Field(..., gt=0)
    trigger_price: float
    limit_price: float
    last_price: float
    is_confirmed: bool = False
    conversation_id: Optional[str] = None


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
    if user and user.active_broker_session:
        kite_token = read_kite_access_token(user.active_broker_session) or "mock_token"

    # Routes to the paper broker for accounts in mode='paper' (so the
    # confirmed order fills into the structured portfolio); falls back to
    # the Kite path otherwise. Idempotent on the preview id so a
    # double-confirm of the same preview doesn't double-fill. Funds-guard /
    # broker-reject errors are surfaced (not 500'd) so the user sees why.
    try:
        result = submit_order_for_user(
            db, user_id,
            access_token=kite_token,
            tradingsymbol=req["tradingsymbol"],
            exchange=req["exchange"],
            transaction_type=req["transaction_type"],
            quantity=req["quantity"],
            order_type=req["order_type"],
            price=req.get("price"),
            product=req.get("product", "CNC"),
            client_request_id=f"chat-confirm:{request.preview_id}",
            source="chat",
            conversation_id=request.conversation_id,
        )
    except InsufficientFundsError as exc:
        raise HTTPException(status_code=402, detail=str(exc))
    except Exception as exc:
        logger.exception("confirm order failed for %s", req.get("tradingsymbol"))
        raise HTTPException(
            status_code=502,
            detail=f"Broker rejected the order: {str(exc).strip() or type(exc).__name__}",
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

    _ph = get_posthog()
    if _ph:
        _ph.capture("order_confirmed", distinct_id=str(user_id), properties={
            "transaction_type": req["transaction_type"],
            "order_type": req["order_type"],
            "quantity": req["quantity"],
            "exchange": req["exchange"],
            "is_paper": result.get("is_paper", False),
        })

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
    offset: int = 0,
):
    """Get recent order history for the current user (paged, newest first)."""
    user_id, _ = auth
    trades = (db.query(TradeLog)
              .filter(TradeLog.user_id == user_id)
              .order_by(TradeLog.placed_at.desc())
              .offset(max(0, int(offset)))
              .limit(limit).all())
    return [{"id": t.id, "symbol": t.symbol, "action": t.transaction_type,
             "quantity": t.quantity, "status": t.status,
             "placed_at": format_ist(t.placed_at)}
            for t in trades]


@router.get("/open")
def get_open_orders(
    auth: tuple = Depends(get_current_user_token),
    db: Session = Depends(get_db),
):
    """List the user's still-open (cancellable) orders.

    These are orders that have not yet executed: AMOs queued while the market
    was closed, resting LIMIT / trigger orders, and anything the broker still
    reports as not-yet-complete. Filled / cancelled / rejected orders are
    terminal and appear in /orders/history instead.
    """
    user_id, _ = auth
    trades = (db.query(TradeLog)
              .filter(TradeLog.user_id == user_id)
              .order_by(TradeLog.placed_at.desc())
              .limit(100).all())
    open_rows = [t for t in trades
                 if str(t.status).lower() in _CANCELLABLE_STATUSES]
    return [{
        "id": t.id, "symbol": t.symbol, "exchange": t.exchange,
        "transaction_type": t.transaction_type, "order_type": t.order_type,
        "quantity": t.quantity, "price": t.price,
        "trigger_price": t.trigger_price, "status": t.status,
        "queued": str(t.status).lower() == "queued",
        "placed_at": format_ist(t.placed_at),
    } for t in open_rows]


@router.post("/{order_id}/cancel")
def cancel_order_route(
    order_id: int,
    auth: tuple = Depends(get_current_user_token),
    db: Session = Depends(get_db),
):
    """Cancel a still-open order before it executes.

    Marks the TradeLog row 'cancelled'. For a LIVE order that reached a real
    broker (a session exists, a broker order id is stored, and we're not in
    mock mode), we also ask the broker to cancel — best-effort: a broker error
    is reported but never leaves the user with an un-cancellable local row, so
    the local state is authoritative for the UI. Terminal orders (filled /
    cancelled / rejected) return 409.
    """
    user_id, _ = auth
    row = (db.query(TradeLog)
           .filter(TradeLog.id == order_id, TradeLog.user_id == user_id)
           .first())
    if row is None:
        raise HTTPException(status_code=404, detail="Order not found")
    if str(row.status).lower() not in _CANCELLABLE_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=f"Order is '{row.status}' and can no longer be cancelled.",
        )

    broker_note: Optional[str] = None
    session = get_active_broker_session(db, user_id)
    if session is not None and row.kite_order_id and not kite_auth.KITE_MOCK_MODE:
        try:
            get_connector(session.broker).cancel_order(session, row.kite_order_id)
        except Exception as exc:  # noqa: BLE001 — local cancel still proceeds
            logger.warning(
                "broker cancel failed for order %s (%s): %s",
                row.id, row.kite_order_id, exc,
            )
            broker_note = (
                "Marked cancelled here, but the broker could not confirm — "
                "verify in your broker app."
            )

    row.status = "cancelled"
    db.commit()
    db.refresh(row)

    _ph = get_posthog()
    if _ph:
        _ph.capture("order_cancelled", distinct_id=str(user_id), properties={
            "symbol": row.symbol,
            "order_type": row.order_type,
        })

    return {
        "id": row.id, "symbol": row.symbol, "status": row.status,
        "broker_note": broker_note,
    }


class OrderRegisterLeg(BaseModel):
    """Single leg of an order intent — what the LogicCard's register_payload carries."""
    symbol: str
    exchange: str = "NSE"
    transaction_type: str = Field(..., description="BUY or SELL")
    order_type: str = Field(..., description="MARKET, LIMIT, GTT, SL, OCO")
    quantity: int = Field(..., gt=0)
    price: Optional[float] = None
    trigger_price: Optional[float] = None
    product: str = "CNC"


class OrderRegisterRequest(BaseModel):
    """Body for POST /orders/register.

    Comes from the chat LogicCard "Confirm & register" button. The order is
    routed by the account's paper-vs-live mode: a PAPER account fills the
    simulated book (no broker, ever); a LIVE account (paper off) places through
    the user's active broker connector. One TradeLog row is written per
    resulting order with source="chat-confirm".

    Single-leg orders pass `symbol/transaction_type/...` at the top.
    Basket orders pass `legs: [...]`. Both forms result in one TradeLog
    row per resulting order.
    """
    # Single-leg fields (mutually exclusive with `legs`)
    symbol: Optional[str] = None
    exchange: Optional[str] = "NSE"
    transaction_type: Optional[str] = None
    order_type: Optional[str] = None
    quantity: Optional[int] = None
    price: Optional[float] = None
    trigger_price: Optional[float] = None
    product: Optional[str] = "CNC"
    # Basket form
    basket: bool = False
    legs: Optional[List[OrderRegisterLeg]] = None
    # Bracket exits (single-leg only): arm a GTT stop-loss and/or target as a
    # percentage move from the entry reference price. Both set → a true OCO
    # pair (one fills, the other cancels); one set → a single GTT. Percent of
    # the entry price, e.g. 5 = exit 5% against/for the position.
    gtt_stoploss_pct: Optional[float] = Field(default=None, gt=0, lt=90)
    gtt_target_pct: Optional[float] = Field(default=None, gt=0, lt=900)
    # Chat conversation that produced this order — so a paper fill attributes
    # to the right forward-test idea (P6). Optional.
    conversation_id: Optional[str] = None


def _persist_leg(
    db: Session,
    user_id: int,
    leg: dict,
    *,
    conversation_id: Optional[str] = None,
    origin_kind: str = "chat",
    strategy_id: Optional[int] = None,
    label: Optional[str] = None,
) -> TradeLog:
    """Persist a chat order intent as a TradeLog row, routing it by the
    account's paper-vs-live mode.

      - PAPER mode (paper trading on, account.mode == 'paper'): the order
        fills the SIMULATED paper book — no broker is ever contacted. The
        TradeLog status reflects the paper outcome (filled / resting /
        rejected / pending) and carries the paper order id.
      - LIVE mode (paper off): the order is placed through the user's active
        broker connector (Kite/Dhan/Fyers; the Kite mock helper in dev or when
        no broker session exists). The status reflects the broker outcome
        (PENDING / COMPLETE / ...) and carries the broker order id.

    ``submit_order_for_user`` owns that paper-vs-broker decision (it re-checks
    ``should_use_paper`` internally), so a paper-mode account can NEVER reach a
    real broker through this path. A routing/placement failure must not lose
    the user's intent: we log it and fall back to recording a plain registered
    order (status='registered', no order id).
    """
    symbol = leg["symbol"].upper()
    side = leg["transaction_type"]
    order_type = str(leg["order_type"]).upper()
    qty = int(leg["quantity"])

    # IDEMPOTENCY: /orders/register previously passed NO client_request_id, so a
    # double-click or a client retry of a timed-out POST created two paper fills
    # for one intended order. Derive a stable key from the leg contents +
    # conversation + a coarse time bucket: a rapid re-submit lands in the same
    # bucket → the paper broker dedups it; a deliberate identical re-order later
    # falls in a new bucket and is allowed. (/confirm and /gtt already do this.)
    import hashlib as _hashlib
    import time as _time
    _sig = f"{symbol}|{side}|{qty}|{leg.get('price')}|{order_type}|{leg.get('trigger_price')}|{conversation_id}"
    _bucket = int(_time.time() // 15)  # 15s dedup window
    _crid = (
        f"chat-register:{user_id}:"
        f"{_hashlib.sha1(_sig.encode()).hexdigest()[:16]}:{_bucket}"
    )

    paper = should_use_paper(db, user_id)

    # LIVE mode (paper off) needs a CONNECTED broker to actually reach the
    # exchange. Without a session the routing seam falls through to the Kite
    # *mock* helper, which would report a phantom "placed" that never hit the
    # broker (exactly the "card says Placed but Kite is empty" symptom). Fail
    # honestly so the UI can prompt the user to connect — except in dev mock
    # mode (no real key), where the mock placement is the intended behaviour.
    if (
        not paper
        and not kite_auth.KITE_MOCK_MODE
        and get_active_broker_session(db, user_id) is None
    ):
        raise HTTPException(
            status_code=409,
            detail=(
                "No broker connected. Connect your broker (e.g. Zerodha Kite) "
                "in Brokers settings to place live orders."
            ),
        )

    order_status = "registered"
    broker_order_id: Optional[str] = None

    try:
        if order_type == "GTT" and leg.get("trigger_price") is not None:
            # A GTT MUST go through the broker's GTT API (place_gtt), not the
            # regular place_order path — Kite rejects order_type="GTT" on a
            # plain order, so it would never create the trigger. Resolve the
            # current LTP the GTT API needs (falls back to the trigger price).
            mark = get_mark_price(symbol)
            limit_price = leg.get("price") or leg["trigger_price"]
            result = submit_gtt_for_user(
                db, user_id,
                tradingsymbol=symbol,
                exchange=leg.get("exchange", "NSE"),
                transaction_type=side,
                quantity=qty,
                trigger_price=float(leg["trigger_price"]),
                limit_price=float(limit_price),
                last_price=float(mark) if mark is not None else float(leg["trigger_price"]),
                source="chat",
                conversation_id=conversation_id,
                client_request_id=f"{_crid}:gtt",
            )
            broker_order_id = (
                str(result.get("trigger_id") or result.get("order_id") or "") or None
            )
        else:
            result = submit_order_for_user(
                db, user_id,
                tradingsymbol=symbol,
                exchange=leg.get("exchange", "NSE"),
                transaction_type=side,
                quantity=qty,
                order_type=order_type,
                price=leg.get("price"),
                product=leg.get("product", "CNC"),
                trigger_price=leg.get("trigger_price"),
                source="chat",
                conversation_id=conversation_id,
                client_request_id=_crid,
                origin_kind=origin_kind,
                strategy_id=strategy_id,
                label=label,
            )
            broker_order_id = result.get("order_id")
        # paper_status (simulated book: filled/resting/rejected/pending) takes
        # precedence; otherwise the broker's own status (PENDING/COMPLETE/
        # active/...).
        order_status = (
            result.get("paper_status") or result.get("status") or "registered"
        )
    except HTTPException:
        raise
    except InsufficientFundsError as exc:
        # Pre-trade funds guard tripped — tell the user plainly.
        raise HTTPException(status_code=402, detail=str(exc))
    except Exception as exc:
        logger.exception(
            "order routing failed for chat order %s %s", side, symbol,
        )
        if not paper:
            # LIVE order: the broker REJECTED/failed it (IP allow-list, RMS,
            # market closed, …). Surface the real reason rather than silently
            # recording a misleading "registered" that the UI shows as "Placed".
            raise HTTPException(
                status_code=502,
                detail=f"Broker rejected the order: {str(exc).strip() or type(exc).__name__}",
            )
        # PAPER/transient routing failure must not lose intent — register it.
        order_status = "registered"

    # After-market handling: a LIVE MARKET/LIMIT order placed outside NSE
    # hours cannot fill now — the exchange queues it for the next session (an
    # AMO / after-market order). The Kite mock optimistically reports
    # "COMPLETE", so without this override the card would claim an impossible
    # instant fill on a closed market. Mark it 'queued' so the UI shows
    # "executes at next open" and it lands in the cancellable open-orders
    # blotter. Paper mode is a simulator (fills are intentionally instant) and
    # GTTs already rest by nature, so neither is touched here.
    if (
        not paper
        and order_type in {"MARKET", "LIMIT"}
        and not is_market_open()
        and "reject" not in str(order_status).lower()
        and "cancel" not in str(order_status).lower()
    ):
        order_status = "queued"

    row = TradeLog(
        user_id=user_id,
        kite_order_id=broker_order_id,         # paper or broker order id (or None)
        symbol=symbol,
        exchange=leg.get("exchange", "NSE"),
        transaction_type=side,
        order_type=order_type,
        quantity=qty,
        price=leg.get("price"),
        trigger_price=leg.get("trigger_price"),
        status=order_status,
        source="chat-confirm",
        placed_at=now_ist(),
    )
    db.add(row)
    return row


def _tick(x: float) -> float:
    """Round to the NSE 0.05 tick."""
    return round(round(x / 0.05) * 0.05, 2)


def _arm_bracket_exits(
    db: Session,
    user_id: int,
    row: TradeLog,
    *,
    stoploss_pct: Optional[float],
    target_pct: Optional[float],
    conversation_id: Optional[str],
) -> tuple[Optional[dict], Optional[str]]:
    """Arm GTT stop-loss/target exits for a just-registered entry order.

    Triggers are computed as a % move from the entry reference price (the
    limit price when set, else the live mark). BUY entry → SL below / TP
    above; SELL entry mirrors. Both set → a true OCO pair (paper: shared
    gtt_oco_group; live: the broker's two-leg GTT). One set → a single GTT.

    Returns (exits_payload, None) on success or (None, reason) on failure —
    the ENTRY is already placed either way, so failures report honestly
    instead of unwinding it. Writes one TradeLog row per armed exit; caller
    owns commit.
    """
    entry_side = str(row.transaction_type).upper()
    exit_side = "SELL" if entry_side == "BUY" else "BUY"

    ref = row.price if row.price else get_mark_price(row.symbol)
    if ref is None or float(ref) <= 0:
        return None, "no reference price available to compute exit triggers"
    ref = float(ref)
    # BUY entry: stop below / target above. SELL entry: mirrored.
    sign = 1 if entry_side == "BUY" else -1
    sl_trigger = (
        _tick(ref * (1 - sign * float(stoploss_pct) / 100)) if stoploss_pct else None
    )
    tp_trigger = (
        _tick(ref * (1 + sign * float(target_pct) / 100)) if target_pct else None
    )

    exits: dict = {"reference_price": ref, "exit_side": exit_side}
    legs: list[tuple[str, float, dict]] = []  # (kind, trigger, result)
    try:
        if sl_trigger is not None and tp_trigger is not None:
            result = submit_gtt_oco_for_user(
                db, user_id,
                tradingsymbol=row.symbol,
                exchange=row.exchange or "NSE",
                exit_side=exit_side,
                quantity=int(row.quantity),
                stoploss_trigger=sl_trigger,
                target_trigger=tp_trigger,
                last_price=ref,
                client_request_id_prefix=f"bracket:{row.id}",
                source="chat",
                conversation_id=conversation_id,
            )
            exits["oco_group"] = result.get("oco_group") or result.get("trigger_id")
            legs.append(("stoploss", sl_trigger, result.get("stoploss") or result))
            legs.append(("target", tp_trigger, result.get("target") or result))
        else:
            kind = "stoploss" if sl_trigger is not None else "target"
            trigger = sl_trigger if sl_trigger is not None else tp_trigger
            assert trigger is not None
            result = submit_gtt_for_user(
                db, user_id,
                tradingsymbol=row.symbol,
                exchange=row.exchange or "NSE",
                transaction_type=exit_side,
                quantity=int(row.quantity),
                trigger_price=trigger,
                limit_price=trigger,
                last_price=ref,
                client_request_id=f"bracket:{row.id}:{kind[:2]}",
                source="chat",
                conversation_id=conversation_id,
            )
            legs.append((kind, trigger, result))
    except NotImplementedError as exc:
        return None, str(exc)
    except InsufficientFundsError as exc:
        return None, str(exc)
    except Exception as exc:  # noqa: BLE001 — entry stands; report the exit failure
        logger.exception(
            "bracket exits failed for order %s %s", row.id, row.symbol,
        )
        return None, f"exit placement failed: {str(exc).strip() or type(exc).__name__}"

    for kind, trigger, result in legs:
        status = str(
            result.get("paper_status") or result.get("status") or "active"
        )
        exit_row = TradeLog(
            user_id=user_id,
            kite_order_id=(
                str(result.get("trigger_id") or result.get("order_id") or "") or None
            ),
            symbol=row.symbol,
            exchange=row.exchange,
            transaction_type=exit_side,
            order_type="GTT",
            quantity=row.quantity,
            price=trigger,
            trigger_price=trigger,
            status=status,
            source="chat-confirm",
            placed_at=now_ist(),
        )
        db.add(exit_row)
        db.flush()
        exits[kind] = {
            "id": exit_row.id,
            "trigger_price": trigger,
            "status": status,
        }
    return exits, None


def _is_queued_status(status: str, order_type: str) -> bool:
    """True for an order that rested instead of filling because the market
    was closed at placement — the LIVE path stamps this ``"queued"`` (see
    the after-market override in ``_persist_leg`` above); the PAPER broker
    stamps the very same situation ``"resting"`` for a MARKET order (see the
    market-hours gate in ``paper/broker.py``). A LIMIT/SL order also rests
    as ``"resting"``, but for a different reason (price not hit yet, not
    market-closed) — so that case must NOT trip the "market closed, will
    execute at next open" messaging."""
    s = str(status).lower()
    if s == "queued":
        return True
    return s == "resting" and str(order_type).upper() == "MARKET"


@router.post("/register", status_code=201)
async def register_order(
    request: OrderRegisterRequest,
    auth: tuple = Depends(get_current_user_token),
    db: Session = Depends(get_db),
):
    """Place/register an order from a chat LogicCard confirm.

    Routing follows the account's paper-vs-live mode (see ``_persist_leg``):
    a PAPER account fills the simulated book (no broker, ever); a LIVE account
    (paper off) places through the user's active broker connector. Either way
    one TradeLog row is written per resulting order and returned so the UI can
    show it in order history immediately.
    """
    user_id, _ = auth

    # Basket: write one TradeLog row per leg.
    if request.basket and request.legs:
        rows = [
            _persist_leg(
                db, user_id, leg.model_dump(),
                conversation_id=request.conversation_id,
            )
            for leg in request.legs
        ]
        db.commit()
        for r in rows:
            db.refresh(r)
        return {
            "registered": [
                {
                    "id": r.id, "symbol": r.symbol, "exchange": r.exchange,
                    "transaction_type": r.transaction_type, "order_type": r.order_type,
                    "quantity": r.quantity, "price": r.price,
                    "trigger_price": r.trigger_price, "status": r.status,
                    "queued": _is_queued_status(r.status, r.order_type),
                    "placed_at": format_ist(r.placed_at),
                }
                for r in rows
            ],
            "count": len(rows),
            "market_open": is_market_open(),
            "next_open": format_ist(next_market_open(), include_seconds=False),
        }

    # Single leg.
    required = ("symbol", "transaction_type", "order_type", "quantity")
    missing = [f for f in required if getattr(request, f) in (None, "")]
    if missing:
        raise HTTPException(
            status_code=422,
            detail=f"Missing fields for single-leg register: {', '.join(missing)}",
        )

    leg = {
        "symbol": request.symbol,
        "exchange": request.exchange or "NSE",
        "transaction_type": request.transaction_type,
        "order_type": request.order_type,
        "quantity": request.quantity,
        "price": request.price,
        "trigger_price": request.trigger_price,
        "product": request.product or "CNC",
    }
    row = _persist_leg(db, user_id, leg, conversation_id=request.conversation_id)
    db.commit()
    db.refresh(row)

    # Bracket exits — armed AFTER the entry commits, so an exit failure can
    # never lose the entry. Skipped when the entry itself was rejected (no
    # position will exist for the exits to close) or for GTT entries.
    exits: Optional[dict] = None
    exits_error: Optional[str] = None
    wants_exits = request.gtt_stoploss_pct or request.gtt_target_pct
    if wants_exits and str(row.order_type).upper() in {"MARKET", "LIMIT"}:
        if "reject" in str(row.status).lower():
            exits_error = "entry was rejected — stop-loss/target not armed"
        else:
            exits, exits_error = _arm_bracket_exits(
                db, user_id, row,
                stoploss_pct=request.gtt_stoploss_pct,
                target_pct=request.gtt_target_pct,
                conversation_id=request.conversation_id,
            )
            db.commit()

    return {
        "id": row.id, "symbol": row.symbol, "exchange": row.exchange,
        "transaction_type": row.transaction_type, "order_type": row.order_type,
        "quantity": row.quantity, "price": row.price,
        "trigger_price": row.trigger_price, "status": row.status,
        "queued": _is_queued_status(row.status, row.order_type),
        "market_open": is_market_open(),
        "next_open": format_ist(next_market_open(), include_seconds=False),
        "placed_at": format_ist(row.placed_at),
        "exits": exits,
        "exits_error": exits_error,
    }


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
    kite_token = (
        read_kite_access_token(user.active_broker_session)
        if user and user.active_broker_session
        else "mock"
    ) or "mock"
    result = submit_gtt_for_user(
        db, user_id,
        access_token=kite_token,
        tradingsymbol=request.tradingsymbol,
        exchange=request.exchange,
        transaction_type=request.transaction_type,
        quantity=request.quantity,
        trigger_price=request.trigger_price,
        limit_price=request.limit_price,
        last_price=request.last_price,
        # Stable idempotency key so a double-submit doesn't double-register
        # the GTT in the paper book (mirrors /orders/confirm's preview key).
        client_request_id=(
            f"chat-gtt:{user_id}:{request.tradingsymbol}:"
            f"{request.trigger_price}:{request.limit_price}"
        ),
        source="chat",
        conversation_id=request.conversation_id,
    )
    # The paper broker only FLUSHES; the router owns commit. (The legacy
    # kite path wrote nothing to the DB, so this commit is new + required
    # for paper.) Also persist a TradeLog for parity with /orders/confirm
    # so the GTT shows in /orders/history.
    db.add(TradeLog(
        user_id=user_id,
        kite_order_id=str(result.get("trigger_id") or result.get("order_id") or ""),
        symbol=request.tradingsymbol,
        exchange=request.exchange,
        transaction_type=request.transaction_type,
        order_type="GTT",
        quantity=request.quantity,
        price=request.limit_price,
        trigger_price=request.trigger_price,
        status=str(result.get("status", "active")),
        source="chat",
        placed_at=now_ist(),
    ))
    db.commit()
    return result
