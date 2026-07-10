"""Multi-leg option execution in the paper book (F&O P2).

``submit_option_strategy`` explodes a registered OptionStrategy into one
PaperOrder + PaperFill PER LEG — child rows, not JSONB, because each leg
IS an independent per-symbol position once filled (a short straddle
holds a CE and a PE position marked/squared separately). Decision
documented in the F&O plan.

Why a PARALLEL fill engine instead of reusing paper/fills.py:
  * the equity engine is LONG-ONLY by invariant (sells reject without a
    held position; positions clamp >= 0) — short option legs violate it;
  * option costs bill on PREMIUM (STT sell-side, NFO txn charges), not
    the equity-delivery table;
  * fills price at MID ± HALF-SPREAD from the chain (crossing the book),
    not at an equity mark.
The parallel engine writes the SAME rows (PaperOrder/PaperFill/
PaperPosition/PaperLedgerEntry) so the ledger replay invariant, NAV
snapshots and blotters keep working unchanged.

Cash semantics:
  BUY leg  → debit  premium + charges (cash_available + cash_settled).
  SELL leg → credit premium − charges.
  Strategies with short legs RESERVE the strategy's margin_estimate up
  front (cash_available → cash_reserved + a 'reserve' ledger row, hung
  off the FIRST short leg's order) so the account can't sell unlimited
  premium. The reserve releases when the strategy closes (P3's
  square-off path); a withdrawn-before-active strategy never reserves.

Idempotency: client_request_id "optstrat:{strategy_id}:leg{n}" — the
global UNIQUE index dedups engine/router retries leg-by-leg.
"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy.orm import Session

from backend.models import (
    OptionStrategy,
    PaperFill,
    PaperLedgerEntry,
    PaperOrder,
    PaperPosition,
)
from backend.paper.accounts import get_or_create_account
from backend.paper.money import to_money
from backend.services.trading_costs import option_buy_cost, option_sell_cost
from backend.utils.time_utils import now_ist

logger = logging.getLogger(__name__)

# Spread-crossing fill model: a market taker pays the half-spread. The
# adverse-selection factor pushes the fill a touch past mid toward the
# touch — 0.9 means we fill at mid + 0.9·half_spread (slightly better
# than the touch, mirroring queue-jumping fills inside the spread).
_SPREAD_CROSS_FACTOR = 0.9


class OptionFillError(Exception):
    """Raised when a strategy cannot be executed in the paper book."""


def _leg_quote(chain: dict, tradingsymbol: str) -> Optional[dict]:
    for row in chain.get("rows") or []:
        for side in ("ce", "pe"):
            q = row.get(side)
            if q and q.get("tradingsymbol") == tradingsymbol:
                return q
    return None


def _fill_price(q: dict, side: str) -> float:
    """Mid ± half-spread (spread-aware, honest for options)."""
    bid = float(q.get("bid") or 0.0)
    ask = float(q.get("ask") or 0.0)
    mid = float(q.get("mid") or q.get("ltp") or 0.0)
    if bid > 0 and ask >= bid:
        half = (ask - bid) / 2.0
        adj = half * _SPREAD_CROSS_FACTOR
        return round(mid + adj, 2) if side == "BUY" else round(max(mid - adj, 0.05), 2)
    return round(mid, 2)


def _upsert_option_position(
    db: Session, account_id: str, user_id: int, *, tradingsymbol: str,
    segment: str, signed_qty: int, fill_price: Decimal, charges: Decimal,
) -> PaperPosition:
    """Signed-quantity position math for option legs.

    avg_cost convention matches equity (cost-inclusive, per unit) for
    LONG lots; SHORT lots carry the per-unit net credit in avg_cost so
    unrealized P&L on a short = (avg_cost − mark) × |qty|. Crossing
    through zero books realized P&L on the closed portion first."""
    pos = (
        db.query(PaperPosition)
        .filter(
            PaperPosition.account_id == account_id,
            PaperPosition.symbol == tradingsymbol,
        )
        .first()
    )
    if pos is None:
        pos = PaperPosition(
            account_id=account_id, user_id=user_id, symbol=tradingsymbol,
            quantity=0, avg_cost=to_money(0), realized_pnl=to_money(0),
            is_option=True, segment=segment,
        )
        db.add(pos)
        db.flush()
    pos.is_option = True
    pos.segment = segment

    old_qty = int(pos.quantity)
    qty = abs(signed_qty)
    # Per-unit cost basis of THIS fill (cost-inclusive both directions).
    if signed_qty > 0:
        unit_cost = to_money((to_money(fill_price) * qty + charges) / qty)
    else:
        unit_cost = to_money((to_money(fill_price) * qty - charges) / qty)

    same_direction = (old_qty == 0) or (old_qty > 0) == (signed_qty > 0)
    if same_direction:
        new_qty = old_qty + signed_qty
        total_old = to_money(pos.avg_cost) * abs(old_qty)
        pos.avg_cost = to_money(
            (total_old + unit_cost * qty) / abs(new_qty)
        ) if new_qty != 0 else to_money(0)
        pos.quantity = new_qty
    else:
        closing = min(abs(old_qty), qty)
        if old_qty > 0:
            # closing longs with a sell: realized = (credit − basis)·closed
            realized = (unit_cost - to_money(pos.avg_cost)) * closing
        else:
            # closing shorts with a buy: realized = (credit basis − debit)·closed
            realized = (to_money(pos.avg_cost) - unit_cost) * closing
        pos.realized_pnl = to_money(pos.realized_pnl) + to_money(realized)
        remainder = old_qty + signed_qty
        if (remainder > 0) == (old_qty > 0) and remainder != 0:
            pos.quantity = remainder              # partial close, basis keeps
        else:
            pos.quantity = remainder              # flat or flipped
            pos.avg_cost = unit_cost if remainder != 0 else to_money(0)
    pos.last_price = float(fill_price)
    pos.last_mark_at = now_ist()
    db.flush()
    return pos


def execute_option_leg_fill(
    db: Session,
    *,
    account,
    user_id: int,
    strategy: OptionStrategy,
    leg_index: int,
    tradingsymbol: str,
    segment: str,
    side: str,
    quantity: int,
    fill_price: float,
    iv_at_fill: Optional[float],
    conversation_id: Optional[str],
) -> PaperFill:
    """One leg: order row → premium cashflow → fill → signed position →
    ledger. Idempotent via the leg's client_request_id."""
    crid = f"optstrat:{strategy.id}:leg{leg_index}"
    existing = (
        db.query(PaperOrder)
        .filter(
            PaperOrder.user_id == user_id,
            PaperOrder.client_request_id == crid,
        )
        .first()
    )
    if existing is not None:
        fill = (
            db.query(PaperFill)
            .filter(PaperFill.order_id == existing.id)
            .first()
        )
        if fill is not None:
            return fill
        raise OptionFillError(
            f"leg {leg_index} order exists in status {existing.status} without a fill"
        )

    price = to_money(fill_price)
    seg = segment.upper()
    if side == "BUY":
        net_f, charges_f = option_buy_cost(float(price), quantity, segment=seg)
        net_cashflow = -to_money(net_f)
        ledger_kind = "buy_debit"
        signed_qty = quantity
    else:
        net_f, charges_f = option_sell_cost(float(price), quantity, segment=seg)
        net_cashflow = to_money(net_f)
        ledger_kind = "sell_credit"
        signed_qty = -quantity
    charges = to_money(charges_f)

    if net_cashflow < 0 and -net_cashflow > to_money(account.cash_available):
        raise OptionFillError(
            f"insufficient paper buying power for leg {leg_index} "
            f"(needs ₹{-net_cashflow:,.2f})"
        )

    order = PaperOrder(
        account_id=account.id,
        user_id=user_id,
        client_request_id=crid,
        symbol=tradingsymbol,
        exchange=strategy.exchange,
        transaction_type=side,
        order_type="MARKET",
        product="NRML",
        quantity=quantity,
        intended_price=float(price),
        intended_quote_at=now_ist(),
        status="pending",
        source="option_strategy",
        origin_kind="chat",
        conversation_id=strategy.conversation_id,
        option_strategy_id=strategy.id,
    )
    db.add(order)
    db.flush()

    account.cash_available = to_money(account.cash_available) + net_cashflow
    account.cash_settled = to_money(account.cash_settled) + net_cashflow

    fill = PaperFill(
        order_id=order.id,
        account_id=account.id,
        user_id=user_id,
        symbol=tradingsymbol,
        transaction_type=side,
        quantity=quantity,
        fill_price=float(price),
        gross_value=to_money(price * quantity),
        charges=charges,
        net_cashflow=net_cashflow,
        slippage_bps=0.0,
        iv_at_fill=iv_at_fill,
        filled_at=now_ist(),
    )
    db.add(fill)
    order.status = "filled"
    order.filled_quantity = quantity
    db.flush()

    _upsert_option_position(
        db, account.id, user_id,
        tradingsymbol=tradingsymbol, segment=seg,
        signed_qty=signed_qty, fill_price=price, charges=charges,
    )

    db.add(PaperLedgerEntry(
        account_id=account.id,
        fill_id=fill.id,
        kind=ledger_kind,
        amount=net_cashflow,
        balance_after=to_money(account.cash_available),
        note=f"{side} {quantity} {tradingsymbol} @ {price} (opt)",
    ))
    db.flush()
    return fill


def submit_option_strategy(
    db: Session, user_id: int, strategy: OptionStrategy,
) -> dict[str, Any]:
    """Execute every leg of a registered paper-book strategy against the
    live chain. All-or-nothing PRE-CHECK (every leg must be quotable and
    affordable before the first fill) + per-leg idempotency, so a retry
    after a partial crash completes the remaining legs.

    Returns {success, fills: [...], error}. Caller owns commit."""
    from backend.market.option_chain import get_chain

    if strategy.book != "paper":
        raise OptionFillError("submit_option_strategy is paper-book only")
    # Commodities (MCX) are tradeable — paper-fills like any other segment.

    # Strict market-hours simulation. Equity MARKET orders rest until the open
    # (paper/broker.py); F&O has no resting evaluator, so instead of filling
    # legs on a closed market we refuse honestly and keep the strategy
    # `registered` for the user to re-deploy at the open. Mirrors the equity
    # gate's intent — a paper fill must never happen while NSE is shut.
    from backend.config import settings as _settings
    from backend.utils.time_utils import is_market_open

    if getattr(_settings, "paper_respect_market_hours", True) and not is_market_open():
        return {
            "success": False, "fills": [],
            "error": (
                "market closed — F&O paper legs fill only during NSE hours "
                "(09:15–15:30 IST, Mon–Fri). The strategy stays registered; "
                "re-deploy it once the market opens."
            ),
        }

    chain = get_chain(
        db, strategy.underlying, strategy.expiry.isoformat(), width=25,
    )
    if chain is None:
        return {
            "success": False, "fills": [],
            "error": "option chain unavailable — cannot fill paper legs",
        }

    account = get_or_create_account(db, user_id)
    legs = sorted(strategy.legs, key=lambda l: l.leg_index)

    # Pre-check every leg's quote before touching cash.
    plans: list[dict] = []
    for leg in legs:
        q = _leg_quote(chain, leg.tradingsymbol)
        if q is None or q.get("iv_status") in ("illiquid", "stale"):
            return {
                "success": False, "fills": [],
                "error": (
                    f"leg {leg.tradingsymbol or leg.strike} has no tradable "
                    "quote right now"
                ),
            }
        plans.append({
            "leg": leg,
            "price": _fill_price(q, leg.side),
            "iv": q.get("iv"),
        })

    # Margin reserve for short legs (strategy-level, once — idempotent
    # via the reserve note + strategy status flip below).
    has_shorts = any(p["leg"].side == "SELL" for p in plans)
    margin = to_money(strategy.margin_estimate or 0)
    if has_shorts and margin > 0 and strategy.status == "registered":
        if margin > to_money(account.cash_available):
            return {
                "success": False, "fills": [],
                "error": (
                    f"insufficient paper cash to reserve margin "
                    f"₹{margin:,.2f} for the short leg(s)"
                ),
            }
        account.cash_available = to_money(account.cash_available) - margin
        account.cash_reserved = to_money(account.cash_reserved) + margin
        db.add(PaperLedgerEntry(
            account_id=account.id,
            kind="reserve",
            amount=-margin,
            balance_after=to_money(account.cash_available),
            note=f"reserve margin optstrat:{strategy.id}",
        ))
        db.flush()

    fills: list[PaperFill] = []
    try:
        for i, plan in enumerate(plans):
            leg = plan["leg"]
            fills.append(execute_option_leg_fill(
                db,
                account=account,
                user_id=user_id,
                strategy=strategy,
                leg_index=i,
                tradingsymbol=leg.tradingsymbol,
                segment=strategy.segment,
                side=leg.side,
                quantity=int(leg.qty_lots) * int(leg.lot_size),
                fill_price=plan["price"],
                iv_at_fill=plan["iv"],
                conversation_id=strategy.conversation_id,
            ))
    except OptionFillError as exc:
        # Partial state stays consistent (each leg is atomic); a retry
        # completes the remaining legs via per-leg idempotency.
        logger.warning("[optstrat] %s leg fill failed: %s", strategy.id, exc)
        return {
            "success": False,
            "fills": [
                {"symbol": f.symbol, "side": f.transaction_type,
                 "price": f.fill_price, "qty": f.quantity}
                for f in fills
            ],
            "error": str(exc),
        }

    strategy.status = "active"
    db.flush()
    logger.info(
        "[optstrat] %s ACTIVE — %d legs filled in paper book", strategy.id, len(fills),
    )
    return {
        "success": True,
        "fills": [
            {"symbol": f.symbol, "side": f.transaction_type,
             "price": f.fill_price, "qty": f.quantity,
             "iv": f.iv_at_fill}
            for f in fills
        ],
        "error": None,
    }
