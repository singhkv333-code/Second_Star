"""Paper option-expiry settlement (F&O).

When a paper-book option strategy reaches its expiry, real exchanges
CASH-SETTLE each contract at its intrinsic value against the underlying's
settlement price. The paper book must do the same, or expired legs linger
forever — stale marks (the chain can't price a dead contract), leaked
short-leg margin (the reserve only releases on square-off), and a drifting
NAV. This module is that settlement pass.

Model — cash-settled at intrinsic, uniform across index + stock options
(the go-to per the product owner; physical delivery is not modelled):

    intrinsic(CE) = max(0, S - K)
    intrinsic(PE) = max(0, K - S)

where ``S`` is the underlying's settlement price on expiry day (fetched
LIVE — never fabricated) and ``K`` the strike. Each open leg position is
flattened AT its intrinsic with ZERO trade charges (settlement is not a
market trade), which books the correct realized P&L through the same
signed-quantity crossing math the square-off path uses. Short-leg margin
is released and the strategy flips to the terminal status ``expired``
(distinct from ``closed`` = a user squared it off).

Settlement price, matched to LIVE data for surety:
  - expiry == today  → the underlying's live LTP just after the 15:30
    close (the job runs 15:34 IST) ≈ that day's settlement price.
  - expiry <  today  → the underlying's historical daily CLOSE on the
    expiry date (a missed expiry, e.g. job downtime, settled on the next
    run against the price that actually prevailed at expiry — never at a
    later, wrong-date price).
Either source unavailable → the strategy is SKIPPED (never settled at a
guessed price) and retried on the next run.

Idempotent: each settlement leg fill keys on client_request_id
``optstrat:{id}:leg{n}:expiry`` and the strategy only leaves ``active``
once, so a retry after a partial crash completes the remaining legs.
"""
from __future__ import annotations

import logging
from datetime import date, time
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy.orm import Session

from backend.models import (
    OptionLeg,
    OptionStrategy,
    PaperFill,
    PaperLedgerEntry,
    PaperOrder,
    PaperPosition,
)
from backend.paper.accounts import get_or_create_account
from backend.paper.money import to_money
from backend.paper.options_routing import _upsert_option_position
from backend.utils.time_utils import now_ist

logger = logging.getLogger(__name__)

_NSE_CLOSE = time(15, 30)

# Option underlyings that are INDICES → their live-quote (exchange, symbol)
# and, implicitly, the yfinance index ticker (get_ohlcv resolves NIFTY→^NSEI
# etc. via market.yfinance_service.INDEX_TICKERS). Anything not here is a
# single-stock (or commodity) underlying quoted under its own symbol.
_INDEX_UNDERLYINGS: dict[str, tuple[str, str]] = {
    "NIFTY": ("NIFTY 50", "NSE"),
    "NIFTY50": ("NIFTY 50", "NSE"),
    "NIFTY 50": ("NIFTY 50", "NSE"),
    "BANKNIFTY": ("NIFTY BANK", "NSE"),
    "NIFTYBANK": ("NIFTY BANK", "NSE"),
    "NIFTY BANK": ("NIFTY BANK", "NSE"),
    "FINNIFTY": ("NIFTY FIN SERVICE", "NSE"),
    "MIDCPNIFTY": ("NIFTY MID SELECT", "NSE"),
    "SENSEX": ("SENSEX", "BSE"),
    "BANKEX": ("BANKEX", "BSE"),
}


def _resolve_underlying_quote(underlying: str, segment: str) -> tuple[str, str]:
    """(quote_symbol, exchange) for a live underlying quote. Indices map to
    their NSE/BSE index name; commodities to MCX; everything else to the NSE
    equity of the same name."""
    u = (underlying or "").strip().upper()
    if u in _INDEX_UNDERLYINGS:
        return _INDEX_UNDERLYINGS[u]
    exch = "MCX" if str(segment or "").upper() in ("MCX", "COM", "COMMODITY") else "NSE"
    return u, exch


def _live_underlying_ltp(underlying: str, segment: str) -> Optional[Decimal]:
    """Underlying's live LTP (settlement proxy on expiry day). None on miss."""
    try:
        from backend.kite.live_quote import get_kite_quote
        sym, exch = _resolve_underlying_quote(underlying, segment)
        q = get_kite_quote(sym, exch)
        ltp = (q or {}).get("last_price")
        if ltp and float(ltp) > 0:
            return to_money(ltp)
    except Exception:  # noqa: BLE001 — never let a data error abort settlement
        logger.warning("[opt-settle] live LTP lookup failed for %s", underlying, exc_info=True)
    return None


def _historical_close_on(underlying: str, expiry: date) -> Optional[Decimal]:
    """Underlying's daily CLOSE on ``expiry`` (or the last bar on/before it).
    Handles indices AND stocks — get_ohlcv resolves the yfinance ticker
    (NIFTY→^NSEI, RELIANCE→RELIANCE.NS) internally. None on miss."""
    try:
        from backend.core.data.historical import get_ohlcv
        df = get_ohlcv(underlying, period="3mo", interval="1d")
        # Walk newest→oldest for the first bar dated on/before the expiry.
        for ts in reversed(df.index):
            d = ts.date() if hasattr(ts, "date") else ts
            if d <= expiry:
                return to_money(float(df.loc[ts, "Close"]))
    except Exception:  # noqa: BLE001
        logger.warning("[opt-settle] historical close lookup failed for %s", underlying, exc_info=True)
    return None


def settlement_price(underlying: str, segment: str, expiry: date) -> Optional[Decimal]:
    """The underlying's settlement price, matched to LIVE data. Same-day
    expiry → live LTP (post-close ≈ settlement); missed (past) expiry →
    historical close on the expiry date. None when neither is available
    (caller SKIPS — a settlement is never booked at a guessed price)."""
    today = now_ist().date()
    if expiry >= today:
        # Expiry today: the just-closed live price is the settlement ref.
        px = _live_underlying_ltp(underlying, segment)
        if px is not None:
            return px
        # Live miss on expiry day — fall back to today's posted daily close.
        return _historical_close_on(underlying, expiry)
    # Missed/past expiry: the close that actually prevailed AT expiry, never
    # today's (wrong-date) live price.
    return _historical_close_on(underlying, expiry)


def _intrinsic(option_type: str, strike: Decimal, spot: Decimal) -> Decimal:
    """Per-unit intrinsic value at expiry. Never negative."""
    k = to_money(strike)
    s = to_money(spot)
    if str(option_type).upper() == "CE":
        return to_money(max(Decimal(0), s - k))
    return to_money(max(Decimal(0), k - s))


def _position_for(db: Session, account_id: str, tradingsymbol: str) -> Optional[PaperPosition]:
    return (
        db.query(PaperPosition)
        .filter(
            PaperPosition.account_id == account_id,
            PaperPosition.symbol == tradingsymbol,
        )
        .first()
    )


def settle_expired_strategy(
    db: Session, user_id: int, strategy: OptionStrategy, spot: Decimal,
) -> dict[str, Any]:
    """Cash-settle every open leg of one ACTIVE strategy at intrinsic value
    against ``spot``, release short-leg margin, flip status to 'expired'.
    Caller owns the commit. Idempotent per leg via client_request_id."""
    account = get_or_create_account(db, user_id)
    legs = sorted(strategy.legs, key=lambda l: int(l.leg_index))
    settled: list[dict[str, Any]] = []

    for leg in legs:
        pos = _position_for(db, account.id, leg.tradingsymbol)
        if pos is None or int(pos.quantity) == 0:
            continue  # already flat (shared/partially-closed leg) — nothing to settle
        current_qty = int(pos.quantity)

        crid = f"optstrat:{strategy.id}:leg{int(leg.leg_index)}:expiry"
        if (
            db.query(PaperOrder)
            .filter(PaperOrder.user_id == user_id, PaperOrder.client_request_id == crid)
            .first()
            is not None
        ):
            continue  # this leg already settled on a prior run

        intrinsic = _intrinsic(leg.option_type, Decimal(str(leg.strike)), spot)
        qty = abs(current_qty)
        side = "SELL" if current_qty > 0 else "BUY"   # flatten the position
        # Long is settled by RECEIVING intrinsic (credit); short by PAYING it
        # (debit). Zero trade charges — a settlement is not a market fill.
        net_cashflow = to_money(intrinsic) * current_qty

        order = PaperOrder(
            account_id=account.id,
            user_id=user_id,
            client_request_id=crid,
            symbol=leg.tradingsymbol,
            exchange=strategy.exchange,
            transaction_type=side,
            order_type="MARKET",
            product="NRML",
            quantity=qty,
            intended_price=float(intrinsic),
            intended_quote_at=now_ist(),
            status="filled",
            filled_quantity=qty,
            source="option_expiry_settlement",
            origin_kind="settlement",  # String(16); ledger kind mirrors this
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
            symbol=leg.tradingsymbol,
            transaction_type=side,
            quantity=qty,
            fill_price=float(intrinsic),
            gross_value=to_money(intrinsic) * qty,
            charges=to_money(0),
            net_cashflow=net_cashflow,
            slippage_bps=0.0,
            filled_at=now_ist(),
        )
        db.add(fill)
        db.flush()

        # Flatten the position at intrinsic (charges 0) — books realized P&L
        # via the shared signed-qty crossing math and drops quantity to 0.
        _upsert_option_position(
            db, account.id, user_id,
            tradingsymbol=leg.tradingsymbol,
            segment=strategy.segment,
            signed_qty=-current_qty,
            fill_price=to_money(intrinsic),
            charges=to_money(0),
        )

        db.add(PaperLedgerEntry(
            account_id=account.id,
            fill_id=fill.id,
            kind="settlement",  # allowed by ck_paper_ledger_kind
            amount=net_cashflow,
            balance_after=to_money(account.cash_available),
            note=(
                f"{leg.option_type} {leg.strike} exp {strategy.expiry} settled "
                f"@ intrinsic ₹{float(intrinsic):.2f} (spot ₹{float(spot):.2f})"
            ),
        ))
        db.flush()
        settled.append({
            "leg": int(leg.leg_index),
            "symbol": leg.tradingsymbol,
            "intrinsic": float(intrinsic),
            "qty": current_qty,
        })

    # Release any short-leg margin reserved on entry (mirrors the square-off
    # path; idempotent via the release-ledger existence check).
    margin = to_money(strategy.margin_estimate or 0)
    if margin > 0:
        already_released = (
            db.query(PaperLedgerEntry)
            .filter(
                PaperLedgerEntry.account_id == account.id,
                PaperLedgerEntry.kind == "release",
                PaperLedgerEntry.note == f"release margin optstrat:{strategy.id}",
            )
            .first()
            is not None
        )
        if not already_released:
            account.cash_reserved = to_money(account.cash_reserved) - margin
            account.cash_available = to_money(account.cash_available) + margin
            db.add(PaperLedgerEntry(
                account_id=account.id,
                kind="release",
                amount=margin,
                balance_after=to_money(account.cash_available),
                note=f"release margin optstrat:{strategy.id}",
            ))
            db.flush()

    strategy.status = "expired"
    db.flush()
    logger.info(
        "[opt-settle] %s EXPIRED — %d leg(s) cash-settled at spot ₹%.2f",
        strategy.id, len(settled), float(spot),
    )
    return {"strategy_id": strategy.id, "spot": float(spot), "legs": settled}


def settle_expired_options(db: Session) -> dict[str, Any]:
    """Daily post-close pass (backend/scheduler.py, 15:34 IST): cash-settle
    every ACTIVE paper option strategy whose expiry has arrived.

    Per (underlying, expiry) the settlement price is fetched ONCE and shared
    across strategies. A strategy with no available settlement price is
    skipped (not settled at a guess) and retried on the next run. Each
    strategy settles in its own commit so one failure can't roll back the
    others."""
    now = now_ist()
    today = now.date()
    past_close = now.time() >= _NSE_CLOSE

    candidates: list[OptionStrategy] = (
        db.query(OptionStrategy)
        .filter(
            OptionStrategy.book == "paper",
            OptionStrategy.status == "active",
            OptionStrategy.expiry <= today,
        )
        .all()
    )

    settled = 0
    skipped = 0
    errors = 0
    price_cache: dict[tuple, Optional[Decimal]] = {}

    for strat in candidates:
        # Expiry-day strategies only settle AFTER the 15:30 close (the cron
        # fires at 15:34, but guard against an off-schedule manual run).
        if strat.expiry == today and not past_close:
            skipped += 1
            continue

        key = (str(strat.underlying).upper(), str(strat.segment).upper(), strat.expiry)
        if key not in price_cache:
            price_cache[key] = settlement_price(
                strat.underlying, strat.segment, strat.expiry,
            )
        spot = price_cache[key]
        if spot is None:
            skipped += 1
            logger.warning(
                "[opt-settle] no settlement price for %s exp %s — skip, retry next run",
                strat.underlying, strat.expiry,
            )
            continue

        try:
            settle_expired_strategy(db, strat.user_id, strat, spot)
            db.commit()
            settled += 1
        except Exception:  # noqa: BLE001 — one bad strategy must not kill the sweep
            db.rollback()
            errors += 1
            logger.exception("[opt-settle] settlement failed for strategy %s", strat.id)

    result = {
        "candidates": len(candidates),
        "settled": settled,
        "skipped": skipped,
        "errors": errors,
    }
    if candidates:
        logger.info("[opt-settle] sweep complete: %s", result)
    return result
