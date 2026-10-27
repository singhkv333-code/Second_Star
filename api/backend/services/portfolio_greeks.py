"""Portfolio-level option Greeks (F&O P2) — the universally-missing
feature this build leads with.

``compute_portfolio_greeks`` walks the account's OPEN option positions
(signed paper legs), re-marks each against the live (5s-cached) chain
and aggregates net delta / gamma / theta / vega plus the SEBI-style
**FutEq delta-equivalent notional** (delta units × forward — the right
internal exposure representation per the 2025 intraday position-limit
framework), broken down per underlying and per expiry bucket.

Greek units (the chain's conventions, scaled by SIGNED position size):
  delta  units of underlying (Δ −65 ≈ short 1 NIFTY lot at 65)
  gamma  Δ change per ₹ of underlying
  theta  ₹/day for the whole book
  vega   ₹ per vol point for the whole book

A leg whose chain row is missing (expiry rolled, strike out of slice) is
reported under ``unmarked`` rather than silently zeroed — absence is
information.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any, Optional

from sqlalchemy.orm import Session

from backend.models import (
    InstrumentMaster,
    PaperAccount,
    PaperGreeksSnapshot,
    PaperPosition,
)

logger = logging.getLogger(__name__)

_GREEKS = ("delta", "gamma", "theta", "vega")


def _open_option_positions(db: Session, account_id: str) -> list[PaperPosition]:
    return (
        db.query(PaperPosition)
        .filter(
            PaperPosition.account_id == account_id,
            PaperPosition.is_option == True,  # noqa: E712
            PaperPosition.quantity != 0,
        )
        .all()
    )


def compute_portfolio_greeks(
    db: Session, account_id: str,
) -> dict[str, Any]:
    """Live net Greeks for one paper account. Marks through the chain
    cache; never calls Kite directly (rate-limit posture)."""
    from backend.market.option_chain import get_chain

    positions = _open_option_positions(db, account_id)
    out: dict[str, Any] = {
        "net": {k: 0.0 for k in _GREEKS},
        "delta_notional": 0.0,
        "by_underlying": {},
        "by_expiry": {},
        "position_count": len(positions),
        "unmarked": [],
    }
    if not positions:
        return out

    # Resolve contracts → (underlying, expiry) chains, one fetch per pair.
    symbols = [p.symbol for p in positions]
    masters = {
        m.tradingsymbol: m
        for m in db.query(InstrumentMaster)
        .filter(InstrumentMaster.tradingsymbol.in_(symbols))
        .all()
    }
    chains: dict[tuple[str, str], Optional[dict]] = {}
    for pos in positions:
        inst = masters.get(pos.symbol)
        if inst is None or inst.expiry is None:
            out["unmarked"].append(pos.symbol)
            continue
        key = (inst.underlying, inst.expiry.isoformat())
        if key not in chains:
            chains[key] = get_chain(db, key[0], key[1], width=25)
        chain = chains[key]
        quote = None
        if chain:
            for row in chain["rows"]:
                for side in ("ce", "pe"):
                    q = row.get(side)
                    if q and q.get("tradingsymbol") == pos.symbol:
                        quote = q
                        break
                if quote:
                    break
        if not quote or quote.get("delta") is None:
            out["unmarked"].append(pos.symbol)
            continue

        qty = int(pos.quantity)  # SIGNED
        u_bucket = out["by_underlying"].setdefault(
            inst.underlying,
            {**{k: 0.0 for k in _GREEKS},
             "delta_notional": 0.0, "positions": 0},
        )
        e_bucket = out["by_expiry"].setdefault(
            inst.expiry.isoformat(),
            {**{k: 0.0 for k in _GREEKS}, "positions": 0},
        )
        forward = float(chain["forward"])
        for k in _GREEKS:
            v = float(quote.get(k) or 0.0) * qty
            out["net"][k] += v
            u_bucket[k] += v
            e_bucket[k] += v
        d_notional = float(quote.get("delta") or 0.0) * qty * forward
        out["delta_notional"] += d_notional
        u_bucket["delta_notional"] += d_notional
        u_bucket["positions"] += 1
        e_bucket["positions"] += 1
        # Re-mark the position row while we're here (display freshness).
        mid = quote.get("mid") or quote.get("ltp")
        if mid:
            pos.last_price = float(mid)

    for bucket in (out["net"], *out["by_underlying"].values(),
                   *out["by_expiry"].values()):
        for k in list(bucket):
            if isinstance(bucket[k], float):
                bucket[k] = round(bucket[k], 4)
    out["delta_notional"] = round(out["delta_notional"], 2)
    return out


def portfolio_greeks_card(db: Session, user_id: int) -> dict[str, Any]:
    """The chat-facing payload (``portfolio_greeks_card`` render hint)."""
    account = (
        db.query(PaperAccount)
        .filter(PaperAccount.user_id == user_id)
        .first()
    )
    if account is None:
        return {
            "_render_hint": "portfolio_greeks_card",
            "net": {k: 0.0 for k in _GREEKS},
            "delta_notional": 0.0,
            "by_underlying": {},
            "by_expiry": {},
            "position_count": 0,
            "unmarked": [],
            "note": "No paper account yet — register an options strategy first.",
        }
    data = compute_portfolio_greeks(db, account.id)
    data["_render_hint"] = "portfolio_greeks_card"
    if data["position_count"] == 0:
        data["note"] = (
            "No open option positions. Net Greeks build up as paper "
            "strategies fill."
        )
    return data


def snapshot_portfolio_greeks(
    db: Session, *, as_of: Optional[date] = None,
) -> int:
    """EOD job: one PaperGreeksSnapshot per account holding option
    positions. Idempotent per (account, date). Returns rows written."""
    as_of = as_of or date.today()
    account_ids = [
        a_id for (a_id,) in (
            db.query(PaperPosition.account_id)
            .filter(
                PaperPosition.is_option == True,  # noqa: E712
                PaperPosition.quantity != 0,
            )
            .distinct()
            .all()
        )
    ]
    written = 0
    for account_id in account_ids:
        account = db.get(PaperAccount, account_id)
        if account is None:
            continue
        data = compute_portfolio_greeks(db, account_id)
        snap = (
            db.query(PaperGreeksSnapshot)
            .filter(
                PaperGreeksSnapshot.account_id == account_id,
                PaperGreeksSnapshot.as_of == as_of,
            )
            .first()
        )
        if snap is None:
            snap = PaperGreeksSnapshot(
                account_id=account_id, user_id=account.user_id, as_of=as_of,
            )
            db.add(snap)
        snap.net_delta = data["net"]["delta"]
        snap.net_gamma = data["net"]["gamma"]
        snap.net_theta = data["net"]["theta"]
        snap.net_vega = data["net"]["vega"]
        snap.delta_notional = data["delta_notional"]
        snap.position_count = data["position_count"]
        snap.breakdown_json = data["by_underlying"]
        written += 1
    db.commit()
    if written:
        logger.info("[greeks-snapshot] wrote %d account snapshots", written)
    return written
