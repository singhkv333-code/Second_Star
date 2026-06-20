"""Order routing: paper broker vs Kite, by account mode (P2 shim).

This is the seam that makes triggered + chat orders land in the paper
portfolio. The order-PLACEMENT call sites (workflow action.place_order /
allocate_* / set_stoploss / set_takeprofit, and chat /orders/confirm,
/orders/gtt) call the helpers here instead of backend.kite.orders
directly. The decision:

    paper  <- settings.paper_trading_enabled AND account.mode == 'paper'
    kite   <- otherwise (the legacy mock/live path)

Two flavors:
  - submit_order / submit_gtt take a workflow StepContext `ctx` and attach
    workflow attribution (+ a RETRY-STABLE client_request_id derived from
    run_id:step_index — NOT the engine's sha1(...:attempts), which changes
    on every retry and would defeat idempotency).
  - submit_order_for_user / submit_gtt_for_user take (db, user_id) for the
    chat routers.

SCOPE NOTE (P2): squareoff_* and cancel_orders are intentionally NOT routed
here — they size from Kite get_positions / get_orders, so routing only
their placement would be incoherent. They move to paper when paper
position/order reads land (P4). Entry + GTT + chat placement is routed now.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from sqlalchemy.orm import Session

# Call Kite via the module (not name-imported) so the canonical
# backend.kite.orders.place_order / place_gtt_order stay the patchable
# seam for tests and resolve dynamically at call time.
from backend.kite import orders as _kite
from backend.brokers.registry import get_connector
from backend.brokers.sessions import get_active_broker_session
from backend.paper.accounts import get_or_create_account
from backend.paper.broker import PaperBroker

logger = logging.getLogger(__name__)


def should_use_paper(db: Session, user_id: int) -> bool:
    """True when this user's orders should fill in the paper portfolio.
    Early-returns False (no account side-effect) when the flag is off."""
    from backend.config import settings

    if not getattr(settings, "paper_trading_enabled", True):
        return False
    try:
        acct = get_or_create_account(db, int(user_id))
    except Exception:
        # Defensive: a DB blip routes to kite rather than erroring the
        # order — but LOG it, since a misroute is otherwise invisible.
        logger.warning(
            "should_use_paper: account lookup failed for user %s; "
            "routing to kite", user_id, exc_info=True,
        )
        return False
    return str(acct.mode) == "paper"


def paper_position_qty(db: Session, user_id: int, symbol: str) -> int:
    """Current net paper-position quantity for a symbol (0 if none). Used
    so a paper SL/TP sizes from the PAPER book, not the Kite holding."""
    from backend.models import PaperAccount, PaperPosition

    acct = (
        db.query(PaperAccount)
        .filter(PaperAccount.user_id == int(user_id))
        .first()
    )
    if acct is None:
        return 0
    pos = (
        db.query(PaperPosition)
        .filter(
            PaperPosition.account_id == acct.id,
            PaperPosition.symbol == str(symbol).upper(),
        )
        .first()
    )
    return int(pos.quantity) if pos is not None else 0


def _wf_crid(
    ctx: Any, kind: str, symbol: str, leg_key: Optional[str] = None,
) -> str:
    """Retry-stable per-(step, symbol, side/kind[, leg]) idempotency key.
    run_id and step_index are fixed across retries; only attempts changes,
    and we deliberately exclude it so a retried step dedups against its
    prior placement instead of double-filling. ``leg_key`` distinguishes
    multiple legs of one step that share a symbol+side (e.g. a basket that
    repeats a symbol) so they don't collapse into one under-fill."""
    base = f"wf:{ctx.run.id}:{ctx.step.step_index}:{kind}:{symbol}"
    return f"{base}:{leg_key}" if leg_key is not None else base


# ── workflow action sites ────────────────────────────────────────────────

def submit_order(
    ctx: Any,
    *,
    access_token: Optional[str] = None,   # Kite path only
    tradingsymbol: str,
    exchange: str = "NSE",
    transaction_type: str,
    quantity: int,
    order_type: str = "MARKET",
    price: Optional[float] = None,
    product: str = "CNC",
    trigger_price: Optional[float] = None,
    tag: str = "pivot",                   # Kite path only
    variety: str = "regular",
    leg_key: Optional[str] = None,        # distinguishes same-symbol basket legs
    **_ignored: Any,
) -> dict:
    db = ctx.db
    uid = int(ctx.workflow.user_id)
    symbol = str(tradingsymbol).upper()
    side = str(transaction_type).upper()
    ot = str(order_type).upper()

    if should_use_paper(db, uid):
        return PaperBroker(db, uid).place_order(
            tradingsymbol=symbol,
            transaction_type=side,
            quantity=int(quantity),
            order_type=ot,
            exchange=exchange,
            price=price,
            product=str(product).upper(),
            trigger_price=trigger_price,
            variety=variety,
            client_request_id=_wf_crid(ctx, side, symbol, leg_key),
            source="workflow",
            origin_kind="workflow",
            workflow_id=str(ctx.workflow.id),
            workflow_run_id=str(ctx.run.id),
            # Forward-test idea label (display only — the workflow idea's
            # natural key is account_id+workflow_id, not the label).
            label=getattr(ctx.workflow, "name", None),
        )
    # Live path: resolve the user's active broker session and route through
    # its connector. Falls back to the kite mock helper when no session
    # exists so mock/dev still works. `access_token` is now ignored here.
    sess = get_active_broker_session(db, uid)
    if sess is not None:
        return get_connector(sess.broker).place_order(
            sess,
            tradingsymbol=symbol,
            exchange=exchange,
            transaction_type=side,
            quantity=int(quantity),
            order_type=ot,
            price=price,
            product=product,
            trigger_price=trigger_price,
            tag=tag,
            variety=variety,
            client_request_id=_wf_crid(ctx, side, symbol, leg_key),
        )
    return _kite.place_order(
        access_token="mock_token",
        tradingsymbol=symbol,
        exchange=exchange,
        transaction_type=side,
        quantity=int(quantity),
        order_type=ot,
        price=price,
        product=product,
        trigger_price=trigger_price,
        tag=tag,
        variety=variety,
    )


def submit_gtt(
    ctx: Any,
    *,
    access_token: Optional[str] = None,
    tradingsymbol: str,
    exchange: str = "NSE",
    transaction_type: str,
    quantity: int,
    trigger_price: float,
    limit_price: float,
    last_price: Optional[float] = None,
    **_ignored: Any,
) -> dict:
    db = ctx.db
    uid = int(ctx.workflow.user_id)
    symbol = str(tradingsymbol).upper()
    side = str(transaction_type).upper()

    if should_use_paper(db, uid):
        return PaperBroker(db, uid).place_gtt_order(
            tradingsymbol=symbol,
            transaction_type=side,
            quantity=int(quantity),
            trigger_price=trigger_price,
            limit_price=limit_price,
            client_request_id=_wf_crid(ctx, "GTT", symbol),
            source="workflow",
            origin_kind="workflow",
            workflow_id=str(ctx.workflow.id),
            workflow_run_id=str(ctx.run.id),
            label=getattr(ctx.workflow, "name", None),
        )
    # Live path: route the GTT through the active broker's connector;
    # fall back to the kite mock helper when no session exists.
    sess = get_active_broker_session(db, uid)
    if sess is not None:
        return get_connector(sess.broker).place_gtt(
            sess,
            tradingsymbol=symbol,
            exchange=exchange,
            transaction_type=side,
            quantity=int(quantity),
            trigger_price=trigger_price,
            limit_price=limit_price,
            last_price=last_price if last_price is not None else limit_price,
        )
    return _kite.place_gtt_order(
        access_token="mock_token",
        tradingsymbol=symbol,
        exchange=exchange,
        transaction_type=side,
        quantity=int(quantity),
        trigger_price=trigger_price,
        limit_price=limit_price,
        last_price=last_price if last_price is not None else limit_price,
    )


# ── chat router sites ─────────────────────────────────────────────────────

def submit_order_for_user(
    db: Session,
    user_id: int,
    *,
    access_token: Optional[str] = None,
    tradingsymbol: str,
    exchange: str = "NSE",
    transaction_type: str,
    quantity: int,
    order_type: str = "MARKET",
    price: Optional[float] = None,
    product: str = "CNC",
    trigger_price: Optional[float] = None,
    tag: str = "pivot",
    variety: str = "regular",
    client_request_id: Optional[str] = None,
    source: str = "chat",
    conversation_id: Optional[str] = None,
    label: Optional[str] = None,
    **_ignored: Any,
) -> dict:
    uid = int(user_id)
    symbol = str(tradingsymbol).upper()
    side = str(transaction_type).upper()
    ot = str(order_type).upper()

    if should_use_paper(db, uid):
        return PaperBroker(db, uid).place_order(
            tradingsymbol=symbol,
            transaction_type=side,
            quantity=int(quantity),
            order_type=ot,
            exchange=exchange,
            price=price,
            product=str(product).upper(),
            trigger_price=trigger_price,
            variety=variety,
            client_request_id=client_request_id,
            source=source,
            origin_kind="chat",
            conversation_id=conversation_id,
            # Chat idea label = the SYMBOL (not side+symbol), so a BUY and a
            # later SELL of the same symbol in one conversation attribute to
            # ONE idea (the chat natural key is conversation_id+label). The
            # SELL then closes the BUY idea's FIFO lots instead of forking a
            # phantom "SELL" idea.
            label=label or symbol,
        )
    # Live path: resolve this user's active broker session and route the
    # order through its connector. Falls back to the kite mock helper when
    # no session exists. `access_token` is now ignored on the live path.
    sess = get_active_broker_session(db, uid)
    if sess is not None:
        return get_connector(sess.broker).place_order(
            sess,
            tradingsymbol=symbol,
            exchange=exchange,
            transaction_type=side,
            quantity=int(quantity),
            order_type=ot,
            price=price,
            product=product,
            trigger_price=trigger_price,
            tag=tag,
            variety=variety,
            client_request_id=client_request_id,
        )
    return _kite.place_order(
        access_token="mock_token",
        tradingsymbol=symbol,
        exchange=exchange,
        transaction_type=side,
        quantity=int(quantity),
        order_type=ot,
        price=price,
        product=product,
        trigger_price=trigger_price,
        tag=tag,
        variety=variety,
    )


def submit_gtt_for_user(
    db: Session,
    user_id: int,
    *,
    access_token: Optional[str] = None,
    tradingsymbol: str,
    exchange: str = "NSE",
    transaction_type: str,
    quantity: int,
    trigger_price: float,
    limit_price: float,
    last_price: Optional[float] = None,
    client_request_id: Optional[str] = None,
    source: str = "chat",
    conversation_id: Optional[str] = None,
    label: Optional[str] = None,
    **_ignored: Any,
) -> dict:
    uid = int(user_id)
    symbol = str(tradingsymbol).upper()
    side = str(transaction_type).upper()

    if should_use_paper(db, uid):
        return PaperBroker(db, uid).place_gtt_order(
            tradingsymbol=symbol,
            transaction_type=side,
            quantity=int(quantity),
            trigger_price=trigger_price,
            limit_price=limit_price,
            client_request_id=client_request_id,
            source=source,
            origin_kind="chat",
            conversation_id=conversation_id,
            label=label or symbol,
        )
    # Live path: route the GTT through the active broker's connector;
    # fall back to the kite mock helper when no session exists.
    sess = get_active_broker_session(db, uid)
    if sess is not None:
        return get_connector(sess.broker).place_gtt(
            sess,
            tradingsymbol=symbol,
            exchange=exchange,
            transaction_type=side,
            quantity=int(quantity),
            trigger_price=trigger_price,
            limit_price=limit_price,
            last_price=last_price if last_price is not None else limit_price,
        )
    return _kite.place_gtt_order(
        access_token="mock_token",
        tradingsymbol=symbol,
        exchange=exchange,
        transaction_type=side,
        quantity=int(quantity),
        trigger_price=trigger_price,
        limit_price=limit_price,
        last_price=last_price if last_price is not None else limit_price,
    )
