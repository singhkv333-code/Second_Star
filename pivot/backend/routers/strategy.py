from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field, field_validator
from typing import Literal, Optional
from datetime import datetime, timezone
from sqlalchemy.orm import Session
import json
from backend.database import get_db
from backend.models import Strategy, StrategyStatus
from backend.auth.jwt_handler import get_user_id_from_token

router = APIRouter(prefix="/strategies", tags=["Strategies"])

# Equity baskets reuse the `strategies` table (no migration): strategy_type is
# pinned to this marker and the basket itself (members / weighting / capital)
# lives in the action_config JSON blob.
EQUITY_BASKET_TYPE = "equity_basket"


def get_user_id(authorization: str = Header(None)) -> int:
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing token")
    uid = get_user_id_from_token(authorization.replace("Bearer ", ""))
    if not uid:
        raise HTTPException(status_code=401, detail="Invalid token")
    return uid


class StrategyCreateRequest(BaseModel):
    name: str
    description: Optional[str] = None
    strategy_type: str = Field(..., description="price_drop, price_cross, rsi, scheduled")
    trigger_symbol: Optional[str] = None
    trigger_condition: dict = Field(default_factory=dict)
    action_config: dict = Field(default_factory=dict)
    max_budget: Optional[float] = Field(default=None, le=200_000)


@router.post("", status_code=201)
def create_strategy(
    request: StrategyCreateRequest,
    user_id: int = Depends(get_user_id),
    db: Session = Depends(get_db),
):
    strategy = Strategy(
        user_id=user_id,
        name=request.name,
        description=request.description,
        strategy_type=request.strategy_type,
        trigger_symbol=request.trigger_symbol,
        trigger_condition=json.dumps(request.trigger_condition),
        action_config=json.dumps(request.action_config),
        max_budget=request.max_budget,
        status=StrategyStatus.active,
    )
    db.add(strategy)
    db.commit()
    db.refresh(strategy)
    return {"id": strategy.id, "status": "created", "name": strategy.name}


@router.get("")
def list_strategies(user_id: int = Depends(get_user_id), db: Session = Depends(get_db)):
    strategies = db.query(Strategy).filter(Strategy.user_id == user_id).all()
    return [{"id": s.id, "name": s.name, "type": s.strategy_type,
             "status": s.status, "trigger_symbol": s.trigger_symbol,
             "max_budget": s.max_budget,
             "last_triggered": s.last_triggered_at.isoformat() if s.last_triggered_at else None}
            for s in strategies]


@router.patch("/{strategy_id}/pause")
def pause_strategy(strategy_id: int, user_id: int = Depends(get_user_id), db: Session = Depends(get_db)):
    s = db.query(Strategy).filter(Strategy.id == strategy_id, Strategy.user_id == user_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="Strategy not found")
    s.status = StrategyStatus.paused
    db.commit()
    return {"id": strategy_id, "status": "paused"}


@router.patch("/{strategy_id}/resume")
def resume_strategy(strategy_id: int, user_id: int = Depends(get_user_id), db: Session = Depends(get_db)):
    s = db.query(Strategy).filter(Strategy.id == strategy_id, Strategy.user_id == user_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="Strategy not found")
    s.status = StrategyStatus.active
    db.commit()
    return {"id": strategy_id, "status": "active"}


@router.delete("/{strategy_id}")
def delete_strategy(strategy_id: int, user_id: int = Depends(get_user_id), db: Session = Depends(get_db)):
    s = db.query(Strategy).filter(Strategy.id == strategy_id, Strategy.user_id == user_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="Strategy not found")
    s.status = StrategyStatus.completed
    db.commit()
    return {"id": strategy_id, "status": "deleted"}


# ── Equity baskets ─────────────────────────────────────────────────────────
#
# A user-built basket of equities / ETFs — the "equity strategy" surfaced in
# the Agents → Strategies tab alongside option strategies. Weighting is either
# "equal" (each name = 100/n) or "custom" (caller-supplied, normalised to 100).
# Stored on the existing `strategies` table (see EQUITY_BASKET_TYPE).


class BasketMember(BaseModel):
    symbol: str
    # Percent 0-100. Ignored (recomputed) for equal-weight baskets.
    weight: float = 0.0

    @field_validator("symbol")
    @classmethod
    def _clean_symbol(cls, v: str) -> str:
        # NSE tradingsymbols carry no ".NS"; normalise to bare uppercase.
        s = (v or "").replace(".NS", "").strip().upper()
        if not s:
            raise ValueError("symbol is required")
        return s


class EquityBasketCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    description: Optional[str] = None
    members: list[BasketMember] = Field(..., min_length=1)
    weighting: Literal["equal", "custom"] = "equal"
    capital_inr: Optional[float] = Field(default=None, gt=0)


class EquityBasketUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    description: Optional[str] = None
    members: Optional[list[BasketMember]] = Field(default=None, min_length=1)
    weighting: Optional[Literal["equal", "custom"]] = None
    capital_inr: Optional[float] = Field(default=None, gt=0)


def _normalise_members(
    members: list[BasketMember], weighting: str
) -> list[dict]:
    """Dedup by symbol (last write wins) and resolve final weights. Equal →
    100/n each; custom → the provided weights renormalised to sum 100 (so the
    stored basket always sums to 100 regardless of what the caller sent)."""
    dedup: dict[str, float] = {}
    for m in members:
        dedup[m.symbol] = max(0.0, float(m.weight))
    symbols = list(dedup.keys())
    n = len(symbols)
    if n == 0:
        raise HTTPException(status_code=422, detail="basket needs at least one name")
    if weighting == "equal":
        w = round(100.0 / n, 4)
        return [{"symbol": s, "weight": w} for s in symbols]
    total = sum(dedup.values())
    if total <= 0:
        # No usable custom weights → fall back to equal rather than divide by 0.
        w = round(100.0 / n, 4)
        return [{"symbol": s, "weight": w} for s in symbols]
    return [{"symbol": s, "weight": round(dedup[s] * 100.0 / total, 4)} for s in symbols]


def _basket_out(s: Strategy) -> dict:
    """Serialise a Strategy row (equity_basket) into the FE basket shape."""
    try:
        cfg = json.loads(s.action_config) if s.action_config else {}
    except (ValueError, TypeError):
        cfg = {}
    return {
        "id": s.id,
        "name": s.name,
        "description": s.description,
        "weighting": cfg.get("weighting", "equal"),
        "members": cfg.get("members", []),
        "capital_inr": cfg.get("capital_inr"),
        "status": s.status.value if hasattr(s.status, "value") else str(s.status),
        "created_at": s.created_at.isoformat() if s.created_at else None,
        "updated_at": s.updated_at.isoformat() if s.updated_at else None,
    }


@router.post("/baskets", status_code=201)
def create_basket(
    request: EquityBasketCreate,
    user_id: int = Depends(get_user_id),
    db: Session = Depends(get_db),
):
    members = _normalise_members(request.members, request.weighting)
    cfg = {
        "members": members,
        "weighting": request.weighting,
        "capital_inr": request.capital_inr,
    }
    row = Strategy(
        user_id=user_id,
        name=request.name.strip(),
        description=(request.description or "").strip() or None,
        strategy_type=EQUITY_BASKET_TYPE,
        action_config=json.dumps(cfg),
        max_budget=request.capital_inr,
        status=StrategyStatus.active,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _basket_out(row)


@router.get("/baskets")
def list_baskets(user_id: int = Depends(get_user_id), db: Session = Depends(get_db)):
    rows = (
        db.query(Strategy)
        .filter(
            Strategy.user_id == user_id,
            Strategy.strategy_type == EQUITY_BASKET_TYPE,
            Strategy.status != StrategyStatus.completed,  # soft-deleted hidden
        )
        .order_by(Strategy.created_at.desc().nullslast(), Strategy.id.desc())
        .all()
    )
    return {"baskets": [_basket_out(s) for s in rows]}


def _load_basket_or_404(db: Session, user_id: int, basket_id: int) -> Strategy:
    s = (
        db.query(Strategy)
        .filter(
            Strategy.id == basket_id,
            Strategy.user_id == user_id,
            Strategy.strategy_type == EQUITY_BASKET_TYPE,
        )
        .first()
    )
    if not s or s.status == StrategyStatus.completed:
        raise HTTPException(status_code=404, detail="Basket not found")
    return s


@router.patch("/baskets/{basket_id}")
def update_basket(
    basket_id: int,
    request: EquityBasketUpdate,
    user_id: int = Depends(get_user_id),
    db: Session = Depends(get_db),
):
    s = _load_basket_or_404(db, user_id, basket_id)
    try:
        cfg = json.loads(s.action_config) if s.action_config else {}
    except (ValueError, TypeError):
        cfg = {}

    if request.name is not None:
        s.name = request.name.strip()
    if request.description is not None:
        s.description = request.description.strip() or None
    if request.weighting is not None:
        cfg["weighting"] = request.weighting
    if request.members is not None:
        cfg["members"] = _normalise_members(
            request.members, request.weighting or cfg.get("weighting", "equal")
        )
    elif request.weighting is not None:
        # Weighting changed but members didn't — re-resolve existing members.
        existing = [BasketMember(**m) for m in cfg.get("members", [])]
        if existing:
            cfg["members"] = _normalise_members(existing, request.weighting)
    if request.capital_inr is not None:
        cfg["capital_inr"] = request.capital_inr
        s.max_budget = request.capital_inr

    s.action_config = json.dumps(cfg)
    s.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(s)
    return _basket_out(s)


@router.delete("/baskets/{basket_id}")
def delete_basket(
    basket_id: int,
    user_id: int = Depends(get_user_id),
    db: Session = Depends(get_db),
):
    s = _load_basket_or_404(db, user_id, basket_id)
    s.status = StrategyStatus.completed
    db.commit()
    return {"id": basket_id, "status": "deleted"}


# ── Trade a basket ─────────────────────────────────────────────────────────
#
# Turn a saved basket into real orders: weight% × capital ÷ live price → whole
# shares per name, then route each BUY through the SAME order seam the chat and
# View /place use (broker-or-paper by account mode, register-not-execute
# kill-switch, honest 409 when no broker is connected). `dry_run` prices the
# basket and returns the computed legs WITHOUT placing anything (the modal's
# preview step).


class BasketTradeRequest(BaseModel):
    # Overrides the basket's saved capital for this trade; falls back to it.
    capital_inr: Optional[float] = Field(default=None, gt=0)
    # Preview only — compute shares + cost, place nothing.
    dry_run: bool = False


def _basket_legs_at_prices(members: list[dict], capital: float) -> tuple[list[dict], list[dict]]:
    """Size each member to whole shares from its live mark. Returns
    (placeable_legs, skipped) where skipped names had no price or couldn't
    afford even one share at their weight."""
    from backend.paper.marks import get_mark_price

    legs: list[dict] = []
    skipped: list[dict] = []
    for m in members:
        symbol = str(m.get("symbol") or "").replace(".NS", "").strip().upper()
        weight = float(m.get("weight") or 0.0)
        if not symbol or weight <= 0:
            continue
        price = get_mark_price(symbol)
        if price is None or float(price) <= 0:
            skipped.append({"symbol": symbol, "reason": "no live price"})
            continue
        price_f = float(price)
        alloc = capital * weight / 100.0
        shares = int(alloc // price_f)
        if shares < 1:
            skipped.append({
                "symbol": symbol,
                "reason": f"₹{round(alloc)} won't buy one share at ₹{round(price_f)}",
            })
            continue
        legs.append({
            "symbol": symbol,
            "exchange": "NSE",
            "transaction_type": "BUY",
            "order_type": "MARKET",
            "quantity": shares,
            "price": None,
            "trigger_price": None,
            "product": "CNC",
            "_est_price": round(price_f, 2),
            "_est_cost": round(shares * price_f, 2),
        })
    return legs, skipped


@router.post("/baskets/{basket_id}/trade", status_code=200)
def trade_basket(
    basket_id: int,
    request: BasketTradeRequest,
    user_id: int = Depends(get_user_id),
    db: Session = Depends(get_db),
):
    s = _load_basket_or_404(db, user_id, basket_id)
    out = _basket_out(s)
    capital = request.capital_inr or out.get("capital_inr")
    if not capital or float(capital) <= 0:
        raise HTTPException(
            status_code=422,
            detail="Set the capital to trade this basket (how much to invest).",
        )

    legs, skipped = _basket_legs_at_prices(out.get("members", []), float(capital))
    if not legs:
        raise HTTPException(
            status_code=422,
            detail="Capital is too small to buy a whole share of any name in this basket.",
        )

    est_total = round(sum(leg["_est_cost"] for leg in legs), 2)

    # Preview: return the computed shares/cost, place nothing.
    if request.dry_run:
        return {
            "dry_run": True,
            "capital_inr": float(capital),
            "est_total": est_total,
            "legs": [
                {"symbol": leg["symbol"], "quantity": leg["quantity"],
                 "est_price": leg["_est_price"], "est_cost": leg["_est_cost"]}
                for leg in legs
            ],
            "skipped": skipped,
        }

    # Real trade — needs a connected broker (register-not-execute; never a
    # silent paper fill on a basket the user chose to trade). Reuses the shared
    # order seam so the kill-switch + paper/broker routing apply uniformly.
    from backend.brokers.sessions import get_active_broker_session
    from backend.paper.routing import should_use_paper
    from backend.routers.orders import _persist_leg
    from backend.utils.time_utils import format_ist

    if get_active_broker_session(db, user_id) is None:
        raise HTTPException(
            status_code=409,
            detail=(
                "No broker connected — connect your broker (e.g. Zerodha Kite) "
                "in Brokers settings to trade this basket."
            ),
        )

    paper = should_use_paper(db, user_id)
    rows = [
        _persist_leg(db, user_id, {k: v for k, v in leg.items() if not k.startswith("_")})
        for leg in legs
    ]
    db.commit()
    for r in rows:
        db.refresh(r)

    return {
        "dry_run": False,
        "routed_to": "paper" if paper else "broker",
        "count": len(rows),
        "est_total": est_total,
        "registered": [
            {"id": r.id, "symbol": r.symbol, "transaction_type": r.transaction_type,
             "quantity": r.quantity, "status": r.status,
             "placed_at": format_ist(r.placed_at)}
            for r in rows
        ],
        "skipped": skipped,
    }
