"""Option-strategy persistence + serialization (F&O P1).

Mirrors ipo_application_service: the router stays thin, this module owns
the DB shape. The SERVER re-resolves every strategy against the live
chain at registration time and persists ITS numbers — client-supplied
economics are never written (the card is a preview, not an order form).
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any, Optional

from sqlalchemy.orm import Session

from backend.models import OptionLeg, OptionStrategy

logger = logging.getLogger(__name__)


def persist_option_strategy(
    db: Session,
    *,
    user_id: int,
    payload: dict[str, Any],
    book: str,
    qty_lots: int,
    conversation_id: Optional[str] = None,
    workflow_id: Optional[str] = None,
    source: str = "chat",
) -> OptionStrategy:
    """Write the OptionStrategy + legs from a SERVER-resolved payload."""
    locked = payload["locked"]
    computed = payload["computed"]
    editable = payload["editable"]

    strategy = OptionStrategy(
        user_id=user_id,
        underlying=locked["underlying"],
        segment=locked["segment"],
        exchange=locked["exchange"],
        template=editable["template"],
        expiry=date.fromisoformat(locked["expiry"]),
        book=book,
        status="registered",
        qty_lots=qty_lots,
        lot_size=int(locked["lot_size"]),
        net_premium=computed["net_premium"],
        max_loss=computed["max_loss"],
        max_profit=computed["max_profit"],
        pop=computed["pop"],
        capital_required=computed["capital_required"],
        margin_estimate=computed["margin_estimate"],
        net_greeks_json=computed["net_greeks"],
        critique_verdict=(payload.get("critique") or {}).get("verdict"),
        conversation_id=conversation_id,
        workflow_id=workflow_id,
        source=source,
    )
    db.add(strategy)
    db.flush()  # strategy.id for the legs

    for i, leg in enumerate(editable["legs"]):
        db.add(OptionLeg(
            strategy_id=strategy.id,
            leg_index=i,
            instrument_token=leg.get("instrument_token"),
            tradingsymbol=leg.get("tradingsymbol"),
            option_type=leg["option_type"],
            side=leg["side"],
            strike=leg["strike"],
            qty_lots=qty_lots,
            lot_size=int(locked["lot_size"]),
            entry_mid=leg.get("mid"),
            entry_iv=leg.get("iv"),
            entry_delta=leg.get("delta"),
        ))
    db.commit()
    db.refresh(strategy)
    logger.info(
        "[option-strategy] registered %s %s %s x%s lots (%s book) id=%s",
        strategy.underlying, strategy.template, strategy.expiry,
        qty_lots, book, strategy.id,
    )
    return strategy


def find_open_duplicate(
    db: Session, user_id: int, underlying: str, template: str, expiry: date,
) -> Optional[OptionStrategy]:
    """Same user + underlying + template + expiry, still registered —
    re-registering is almost always a double-click, not intent."""
    return (
        db.query(OptionStrategy)
        .filter(
            OptionStrategy.user_id == user_id,
            OptionStrategy.underlying == underlying,
            OptionStrategy.template == template,
            OptionStrategy.expiry == expiry,
            OptionStrategy.status == "registered",
        )
        .first()
    )


def serialize_option_strategy(s: OptionStrategy) -> dict[str, Any]:
    return {
        "id": s.id,
        "underlying": s.underlying,
        "segment": s.segment,
        "exchange": s.exchange,
        "template": s.template,
        "expiry": s.expiry.isoformat(),
        "book": s.book,
        "status": s.status,
        "qty_lots": s.qty_lots,
        "lot_size": s.lot_size,
        "net_premium": float(s.net_premium) if s.net_premium is not None else None,
        "max_loss": float(s.max_loss) if s.max_loss is not None else None,
        "max_profit": float(s.max_profit) if s.max_profit is not None else None,
        "pop": s.pop,
        "capital_required": (
            float(s.capital_required) if s.capital_required is not None else None
        ),
        "margin_estimate": (
            float(s.margin_estimate) if s.margin_estimate is not None else None
        ),
        "net_greeks": s.net_greeks_json,
        "critique_verdict": s.critique_verdict,
        "legs": [
            {
                "option_type": leg.option_type,
                "side": leg.side,
                "strike": float(leg.strike),
                "tradingsymbol": leg.tradingsymbol,
                "qty_lots": leg.qty_lots,
                "lot_size": leg.lot_size,
                "entry_mid": leg.entry_mid,
                "entry_iv": leg.entry_iv,
            }
            for leg in s.legs
        ],
        "created_at": s.created_at.isoformat() if s.created_at else None,
    }
