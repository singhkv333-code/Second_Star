import logging
import re
from typing import Optional
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from backend.database import get_db
from backend.models import (
    User, ProductPosition,
)
from backend.auth.jwt_handler import get_user_id_from_token
from backend.kite.auth import read_kite_access_token
from backend.services.portfolio_cache import (
    get_summary_cached,
)
from backend.agents.yield_scanner import get_all_yields, calculate_after_tax_yield

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/portfolio", tags=["Portfolio"])

SECTOR_MAP = {
    "INFY": "IT", "TCS": "IT", "WIPRO": "IT", "HCLTECH": "IT",
    "HDFCBANK": "Banking", "ICICIBANK": "Banking", "SBIN": "Banking",
    "KOTAKBANK": "Banking", "AXISBANK": "Banking",
    "RELIANCE": "Energy", "ONGC": "Energy", "NTPC": "Energy",
    "TATAMOTORS": "Auto", "MARUTI": "Auto", "BAJAJ-AUTO": "Auto",
    "HAL": "Defence", "BEL": "Defence", "BHEL": "Defence",
    "NIFTYBEES": "Index ETF", "GOLDBEES": "Gold ETF",
    "NESTLEIND": "FMCG", "HINDUNILVR": "FMCG",
}


def get_user_id(authorization: str = Header(None)) -> int:
    if not authorization:
        # Mirror the chat router (backend/routers/chat.py): in development
        # we fall back to the default dev user so the FE works without
        # a login flow. Production still requires a real token.
        from backend.config import settings as _cfg
        if getattr(_cfg, "app_env", "development") == "development":
            return 1
        raise HTTPException(status_code=401, detail="Missing token")
    uid = get_user_id_from_token(authorization.replace("Bearer ", ""))
    if not uid:
        raise HTTPException(status_code=401, detail="Invalid token")
    return uid


def get_kite_token(user_id: int, db: Session) -> str:
    user = db.query(User).filter(User.id == user_id).first()
    if user and user.active_broker_session and user.active_broker_session.access_token:
        return read_kite_access_token(user.active_broker_session) or "mock_token"
    return "mock_token"


def _paper_summary_in_kite_shape(db: Session, user_id: int) -> Optional[dict]:
    """The paper account rolled up into the ``/summary`` (Kite) shape, or None
    when the user isn't in paper mode. ``total_value`` is NAV (cash + positions)
    so it matches the header + the FE's ``adaptPaperSummary``."""
    from backend.paper.routing import should_use_paper

    if not should_use_paper(db, int(user_id)):
        return None
    from backend.paper.portfolio import account_summary

    s = account_summary(db, int(user_id))
    if not s.get("exists"):
        return None
    return {
        "total_value": s["nav"],
        "invested_value": s["invested"],
        "total_pnl": s["total_pnl"],
        "total_pnl_pct": s["total_pnl_pct"],
        "day_pnl": s["day_pnl"],
        "num_holdings": s["num_positions"],
    }


@router.get("/summary")
def portfolio_summary(user_id: int = Depends(get_user_id), db: Session = Depends(get_db)):
    # Paper mode: roll up the SIMULATED book (NAV incl. cash) so chat portfolio
    # reads agree with the Portfolio page. Live/broker: the cached Kite summary.
    paper = _paper_summary_in_kite_shape(db, user_id)
    if paper is not None:
        return paper
    token = get_kite_token(user_id, db)
    # WHY cached: dashboard polls + chat reads share this endpoint; a
    # short (10-15s) TTL collapses bursts without serving stale live
    # numbers. No other endpoint depends on this one having run first —
    # `/holdings`, `/scores`, and `/api/portfolio/performance` each derive
    # their own holdings independently, so callers should fire all four
    # concurrently rather than sequencing them.
    return get_summary_cached(user_id, token)


def _market_cap_tier(mcap_cr: Optional[float]) -> Optional[str]:
    """large | mid | small | None, using the SAME thresholds as the screener
    (`backend.routers.screener._MCAP_TIERS`) so Portfolio and Screener agree
    on what counts as large/mid/small cap."""
    if mcap_cr is None:
        return None
    from backend.routers.screener import _MCAP_TIERS

    for tier, (lo, hi) in _MCAP_TIERS.items():
        if (lo is None or mcap_cr >= lo) and (hi is None or mcap_cr < hi):
            return tier
    return None


# Option/future contract symbols (e.g. NIFTY2672123850PE, BANKNIFTY26AUG58700CE,
# RELIANCE26JULFUT) — these are F&O positions, not cap-tierable equities.
_DERIVATIVE_SYMBOL_RE = re.compile(r"\d(?:CE|PE)$|FUT$")


def universe_by_symbols(symbols: set[str]) -> dict[str, dict]:
    """`{symbol: universe_row}` for the given symbols, from the Redis-cached
    whole-market screener universe. Empty dict on any failure — sector/mcap
    enrichment is best-effort and must never fail a portfolio read."""
    if not symbols:
        return {}
    try:
        from backend.routers.screener import _full_universe

        return {r["symbol"]: r for r in _full_universe() if r["symbol"] in symbols}
    except Exception:  # noqa: BLE001
        logger.warning("[portfolio] universe lookup failed", exc_info=True)
        return {}


def resolve_sector(symbol: str, urow: Optional[dict] = None) -> str:
    """Rich sector label for a holding: hand-map → screener universe
    ``sector_label`` → "Other". F&O contracts read "F&O"; ETFs/BEES read their
    label or "ETF". This is the single source of truth every portfolio surface
    (``/holdings``, ``/sector``, the paper book) shares, so a name outside the
    tiny hand-map still shows its real sector instead of "Other".

    Pass ``urow`` (from ``universe_by_symbols``) when enriching a batch to avoid
    a per-symbol universe scan; it's looked up lazily when omitted.
    """
    sym = symbol or ""
    if _DERIVATIVE_SYMBOL_RE.search(sym):
        return "F&O"
    # Multi-asset legs (US equities/ETFs, crypto) aren't in the NSE universe, so
    # bucket them by asset class instead of dumping them into "Other". Gate the
    # heavier classify() behind the fast check so Indian names skip it.
    try:
        from backend.market.security_meta import (
            classify,
            is_us_or_crypto_fast,
        )

        if is_us_or_crypto_fast(sym):
            ac = classify(sym).get("asset_class")
            if ac == "crypto":
                return "Crypto"
            if ac in ("us_equity", "us_etf"):
                return "US Equity"
    except Exception:  # noqa: BLE001 — classification is best-effort
        pass
    if urow is None:
        urow = universe_by_symbols({sym}).get(sym)
    sector = SECTOR_MAP.get(sym) or (urow or {}).get("sector_label")
    if sym.endswith("BEES") or "ETF" in (sector or ""):
        return sector or "ETF"
    return sector or "Other"


@router.get("/holdings")
def portfolio_holdings(user_id: int = Depends(get_user_id), db: Session = Depends(get_db)):
    from backend.services.portfolio_source import resolve_holdings

    token = get_kite_token(user_id, db)
    # Paper mode → the simulated positions; live → Kite holdings. Same shape.
    holdings = [dict(h) for h in resolve_holdings(db, user_id, token)]
    # Enrich with sector + real market-cap tier (mutates the copied list, never
    # the cached one). Market-cap tier is best-effort: any lookup failure just
    # leaves it null rather than failing the whole holdings read.
    symbols = {h["tradingsymbol"] for h in holdings}
    universe_by_symbol = universe_by_symbols(symbols)
    for h in holdings:
        sym = h["tradingsymbol"] or ""
        urow = universe_by_symbol.get(sym)
        # Sector: shared resolver (hand-map → universe label → "Other"), so
        # anything the small SECTOR_MAP misses still gets its real sector.
        h["sector"] = resolve_sector(sym, urow)
        if _DERIVATIVE_SYMBOL_RE.search(sym):
            h["market_cap_tier"] = "derivative"
        elif h["sector"] == "ETF" or "ETF" in h["sector"] or sym.endswith("BEES"):
            h["market_cap_tier"] = "etf"
        else:
            h["market_cap_tier"] = _market_cap_tier((urow or {}).get("mcap_cr"))
    return holdings


@router.get("/sector")
def sector_breakdown(user_id: int = Depends(get_user_id), db: Session = Depends(get_db)):
    from backend.services.portfolio_source import resolve_holdings

    token = get_kite_token(user_id, db)
    holdings = resolve_holdings(db, user_id, token)
    umap = universe_by_symbols({h["tradingsymbol"] for h in holdings})
    sector_totals = {}
    total_value = 0
    for h in holdings:
        sector = resolve_sector(h["tradingsymbol"], umap.get(h["tradingsymbol"]))
        value = h["last_price"] * h["quantity"]
        sector_totals[sector] = sector_totals.get(sector, 0) + value
        total_value += value
    return {
        "sectors": [{"sector": s, "value": round(v, 2),
                     "pct": round(v / total_value * 100, 1) if total_value else 0}
                    for s, v in sorted(sector_totals.items(), key=lambda x: -x[1])],
        "total_value": round(total_value, 2),
        "is_concentrated": any(v / total_value > 0.40 for v in sector_totals.values()) if total_value else False,
    }


@router.get("/products")
def active_products(user_id: int = Depends(get_user_id), db: Session = Depends(get_db)):
    products = (db.query(ProductPosition)
                .filter(ProductPosition.user_id == user_id, ProductPosition.status == "active")
                .all())
    return [{"id": p.id, "product_type": p.product_type, "display_name": p.display_name,
             "capital_deployed": p.capital_deployed, "maturity_date": p.maturity_date.isoformat() if p.maturity_date else None,
             "status": p.status} for p in products]


@router.get("/yields")
async def yield_comparison(user_id: int = Depends(get_user_id), tax_slab: float = 0.30):
    yields = await get_all_yields()
    result = []
    for instrument, gross in yields.items():
        after_tax = calculate_after_tax_yield(gross, instrument, tax_slab)
        result.append({
            "instrument": instrument.replace("_", " ").title(),
            "key": instrument,
            "gross_yield_pct": round(gross * 100, 2),
            "after_tax_yield_pct": round(after_tax * 100, 2),
            "tax_slab_used": tax_slab,
        })
    result.sort(key=lambda x: -x["after_tax_yield_pct"])
    result[0]["is_best"] = True
    return result
