"""PaperBroker — the non-Kite execution path.

Mirrors the backend.kite.orders interface (place_order / place_gtt_order)
plus a ``client_request_id`` idempotency key. It accepts the Kite keyword
set (``access_token``, ``tag``) and ignores what it doesn't need, so the
P2 routing shim can pick PaperBroker vs the Kite mock by account mode and
forward the SAME kwargs — no change at the chat / workflow-action sites.

P1 scope: the synchronous MARKET path is fully wired (price -> fill ->
position -> cash -> ledger). LIMIT / SL / GTT orders are persisted as
RESTING rows (a LIMIT/SL BUY reserves cash); the evaluator that fills
resting orders on a price tick is P3.

Transactions: the broker uses the caller's session and FLUSHES; the caller
owns commit (matching the chat routers + the workflow engine, which does
multi-step work in ONE transaction). The risky insert is wrapped in a
SAVEPOINT (begin_nested) so a client_request_id collision rolls back ONLY
that insert, never the caller's other uncommitted work.

Idempotency: the lookup is USER-SCOPED (a known (user_id,
client_request_id) replays that user's prior order). The DB unique index
on client_request_id is GLOBAL, however, so callers MUST namespace crids
per user — all current callers do (workflow: wf:{run_id}:…; chat:
chat-confirm:{preview_id} / chat-gtt:{user_id}:…; sip:{sip_id}:…, each
embedding a per-user id). A hypothetical cross-user crid reuse would raise
IntegrityError (not silently return another user's order); if a future
caller can't guarantee per-user namespacing, make the index composite
(user_id, client_request_id). The P2 shim already passes a RETRY-STABLE
key (run_id:step_index, excluding the engine's attempts counter) so an
engine retry dedups instead of double-filling.
"""
from __future__ import annotations

from typing import Callable, Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.models import PaperFill, PaperLedgerEntry, PaperOrder
from backend.paper.accounts import get_or_create_account
from backend.paper.fills import execute_market_fill
from backend.paper.marks import get_mark_price
from backend.paper.money import to_money
from backend.services.trading_costs import buy_cost
from backend.utils.time_utils import now_ist

# Order types that rest until a price tick fills them (P3 evaluator).
_RESTING_TYPES = {"LIMIT", "SL", "SL-M", "GTT"}
# Resting types that carry a limit price (SL-M is market-on-trigger).
_LIMIT_BEARING = {"LIMIT", "SL", "GTT"}
# Resting BUYs that reserve cash immediately (trigger-based GTT/SL-M
# reserve at fill time in P3, not now).
_IMMEDIATE_RESERVE = {"LIMIT", "SL"}

# paper status -> kite-ish status string (so downstream readers that key
# off the Kite vocabulary keep working through the P2 shim).
_STATUS_MAP = {
    "filled": "COMPLETE",
    "rejected": "REJECTED",
    "resting": "OPEN",
    "pending": "PENDING",
}


class PaperBroker:
    def __init__(
        self,
        db: Session,
        user_id: int,
        *,
        price_fn: Optional[Callable[[str], object]] = None,
    ) -> None:
        self.db = db
        self.user_id = int(user_id)
        # Injectable so tests are deterministic and offline; defaults to
        # the real mark resolver (Kite live -> yfinance).
        self._price_fn = price_fn

    # ── price ──────────────────────────────────────────────────────────
    def _price(self, symbol: str):
        if self._price_fn is not None:
            return self._price_fn(symbol)
        return get_mark_price(symbol)

    # ── public: place_order ────────────────────────────────────────────
    def place_order(
        self,
        *,
        tradingsymbol: str,
        transaction_type: str,            # BUY / SELL
        quantity: int,
        order_type: str = "MARKET",       # MARKET / LIMIT / SL / SL-M / GTT
        exchange: str = "NSE",
        price: Optional[float] = None,    # limit price for LIMIT/SL/GTT
        product: str = "CNC",
        trigger_price: Optional[float] = None,
        variety: str = "regular",
        client_request_id: Optional[str] = None,
        # Accepted for Kite-interface parity (the P2 shim forwards them);
        # paper ignores access_token and uses client_request_id — NOT tag —
        # as its idempotency key.
        access_token: Optional[str] = None,
        tag: str = "pivot",
        # attribution
        source: Optional[str] = None,
        origin_kind: Optional[str] = None,
        workflow_id: Optional[str] = None,
        workflow_run_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        strategy_id: Optional[int] = None,
        idea_id: Optional[str] = None,
    ) -> dict:
        db = self.db
        side = str(transaction_type).upper()
        ot = str(order_type).upper()

        # Validate malformed input BEFORE persisting anything.
        qty = int(quantity)
        if qty <= 0:
            return self._reject_dict(
                "invalid_quantity", str(tradingsymbol).upper(), side, qty,
                client_request_id,
            )

        # Idempotency fast-path, scoped to this user. (The authoritative
        # guarantee is the unique index + the flush-collision handler
        # below; this SELECT just avoids the insert in the common case.)
        if client_request_id:
            existing = self._find_by_crid(client_request_id)
            if existing is not None:
                return self._result(existing, idempotent=True)

        account = get_or_create_account(db, self.user_id)
        limit_price = (
            float(price) if (ot in _LIMIT_BEARING and price is not None) else None
        )

        order = PaperOrder(
            account_id=account.id,
            user_id=self.user_id,
            client_request_id=client_request_id,
            symbol=str(tradingsymbol).upper(),
            exchange=exchange,
            transaction_type=side,
            order_type=ot,
            product=str(product).upper(),
            variety=variety,
            quantity=qty,
            limit_price=limit_price,
            trigger_price=float(trigger_price) if trigger_price is not None else None,
            status="pending",
            source=source,
            origin_kind=origin_kind,
            workflow_id=workflow_id,
            workflow_run_id=workflow_run_id,
            conversation_id=conversation_id,
            strategy_id=strategy_id,
            idea_id=idea_id,
        )
        try:
            # SAVEPOINT: a client_request_id collision rolls back ONLY this
            # insert, preserving the caller's other uncommitted work.
            with db.begin_nested():
                db.add(order)
                db.flush()
        except IntegrityError:
            existing = self._find_by_crid(client_request_id)
            if existing is not None:
                return self._result(existing, idempotent=True)
            raise

        if ot == "MARKET":
            mark = self._price(order.symbol)
            if mark is None or to_money(mark) <= 0:
                order.status = "rejected"
                order.reject_reason = "price_unavailable"
                db.flush()
                return self._result(order)
            order.intended_price = float(to_money(mark))
            order.intended_quote_at = now_ist()
            execute_market_fill(db, order, mark)  # sets order.status
            db.flush()
            return self._result(order)

        if ot in _RESTING_TYPES:
            order.status = "resting"
            # Capture a decision-time reference price so the P3 evaluator
            # infers a trigger's direction (SL below entry vs TP above)
            # from when the order was PLACED, not the first scheduler tick.
            # Harmless for LIMIT (which keys off limit_price, not direction).
            if order.intended_price is None:
                ref = self._price(order.symbol)
                if ref is not None and to_money(ref) > 0:
                    order.intended_price = float(to_money(ref))
            # A resting LIMIT/SL BUY reserves cash up front so buying power
            # can't be double-spent before it fills (P3 fills/releases it).
            # Reject if the reservation exceeds buying power (mirrors the
            # MARKET path's check) so cash_available can't go negative.
            if side == "BUY" and ot in _IMMEDIATE_RESERVE and limit_price:
                # Reserve the CHARGES-INCLUSIVE net debit (not just
                # limit*qty): the fill re-checks net_debit (incl. charges)
                # against the released reserve, so an exclusive reserve let
                # a near-max order self-reject after releasing. buy_cost at
                # the limit is an upper bound on the fill-at-mark net debit.
                reserve = to_money(buy_cost(float(limit_price), qty)[0])
                # cash_available is the free balance; a new reserve is taken
                # from it, so gate against cash_available (NOT available -
                # reserved, which would double-count an existing reserve).
                buying_power = to_money(account.cash_available)
                if reserve > buying_power:
                    order.status = "rejected"
                    order.reject_reason = "insufficient_buying_power"
                    db.flush()
                    return self._result(order)
                account.cash_available = to_money(account.cash_available) - reserve
                account.cash_reserved = to_money(account.cash_reserved) + reserve
                order.reserved_cash = reserve
                db.add(PaperLedgerEntry(
                    account_id=account.id,
                    kind="reserve",
                    amount=-reserve,
                    balance_after=to_money(account.cash_available),
                    note=f"reserve {order.symbol} {ot} buy",
                ))
            db.flush()
            return self._result(order)

        order.status = "rejected"
        order.reject_reason = f"unsupported_order_type:{ot}"
        db.flush()
        return self._result(order)

    # ── public: place_gtt_order ────────────────────────────────────────
    def place_gtt_order(
        self,
        *,
        tradingsymbol: str,
        transaction_type: str,
        quantity: int,
        trigger_price: float,
        limit_price: float,
        last_price: Optional[float] = None,   # Kite parity; unused in paper
        exchange: str = "NSE",
        access_token: Optional[str] = None,   # Kite parity; ignored
        client_request_id: Optional[str] = None,
        **attribution,
    ) -> dict:
        """A GTT rests as an order_type='GTT' row (status='resting'); the
        P3 evaluator fills it when LTP crosses trigger_price. Returns a
        GTT-shaped result (trigger_id + status='active') for Kite parity —
        action.set_stoploss / set_takeprofit read result['trigger_id']."""
        res = self.place_order(
            tradingsymbol=tradingsymbol,
            transaction_type=transaction_type,
            quantity=quantity,
            order_type="GTT",
            exchange=exchange,
            price=limit_price,
            trigger_price=trigger_price,
            client_request_id=client_request_id,
            **attribution,
        )
        # GTT contract: callers read trigger_id + a Kite-style status.
        res["trigger_id"] = res.get("order_id")
        if res.get("paper_status") == "resting":
            res["status"] = "active"
        return res

    # ── helpers ────────────────────────────────────────────────────────
    def _find_by_crid(self, client_request_id: Optional[str]) -> Optional[PaperOrder]:
        if not client_request_id:
            return None
        return (
            self.db.query(PaperOrder)
            .filter(
                PaperOrder.user_id == self.user_id,
                PaperOrder.client_request_id == client_request_id,
            )
            .first()
        )

    def _reject_dict(
        self, reason: str, symbol: str, side: str, quantity: int,
        client_request_id: Optional[str],
    ) -> dict:
        """A reject that never touched the DB (malformed input)."""
        return {
            "order_id": None,
            "status": "REJECTED",
            "paper_status": "rejected",
            "average_price": None,
            "executed_price": None,
            "quantity": quantity,
            "filled_quantity": 0,
            "symbol": symbol,
            "side": side,
            "executed_value_inr": None,
            "reject_reason": reason,
            "client_request_id": client_request_id,
            "idempotent_replay": False,
            "message": f"paper order rejected: {reason}",
        }

    def _result(self, order: PaperOrder, *, idempotent: bool = False) -> dict:
        fill = None
        if order.status == "filled":
            fill = (
                self.db.query(PaperFill)
                .filter(PaperFill.order_id == order.id)
                .order_by(PaperFill.filled_at.desc())
                .first()
            )
        avg = float(fill.fill_price) if fill else None
        return {
            "order_id": order.id,
            "status": _STATUS_MAP.get(order.status, order.status.upper()),
            "paper_status": order.status,
            "average_price": avg,
            "price": avg,            # alias for kite-parity readers
            "executed_price": avg,
            "quantity": order.quantity,
            "filled_quantity": order.filled_quantity,
            "symbol": order.symbol,
            "side": order.transaction_type,
            "executed_value_inr": float(fill.gross_value) if fill else None,
            "reject_reason": order.reject_reason,
            "client_request_id": order.client_request_id,
            "idempotent_replay": idempotent,
            "message": f"paper order {order.status}",
        }
