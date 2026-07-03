import logging
from typing import Optional
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from backend.database import get_db
from backend.models import (
    User, ProductPosition, PaperAccount, PaperNavSnapshot, BrokerSession,
)
from backend.auth.jwt_handler import get_user_id_from_token
from backend.kite.auth import read_kite_access_token
from backend.kite.portfolio import get_holdings, get_portfolio_summary, get_margins
from backend.services.portfolio_cache import (
    get_summary_cached, get_holdings_cached, cache_aside, scores_cache_key,
)
from backend.services import portfolio_scores as _scores
from backend.agents.yield_scanner import get_all_yields, calculate_after_tax_yield
from backend.cache import redis_client
import json

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


@router.get("/summary")
def portfolio_summary(user_id: int = Depends(get_user_id), db: Session = Depends(get_db)):
    token = get_kite_token(user_id, db)
    # WHY cached: dashboard polls + chat reads share this endpoint; a
    # short (10-15s) TTL collapses bursts without serving stale live
    # numbers. No other endpoint depends on this one having run first —
    # `/holdings`, `/scores`, and `/api/portfolio/performance` each derive
    # their own holdings independently, so callers should fire all four
    # concurrently rather than sequencing them.
    return get_summary_cached(user_id, token)


@router.get("/holdings")
def portfolio_holdings(user_id: int = Depends(get_user_id), db: Session = Depends(get_db)):
    token = get_kite_token(user_id, db)
    holdings = list(get_holdings_cached(user_id, token))
    # Enrich with sector data (mutates the cached list — copy first
    # so we don't pollute the cached payload across requests).
    holdings = [dict(h) for h in holdings]
    for h in holdings:
        h["sector"] = SECTOR_MAP.get(h["tradingsymbol"], "Other")
    return holdings


@router.get("/sector")
def sector_breakdown(user_id: int = Depends(get_user_id), db: Session = Depends(get_db)):
    token = get_kite_token(user_id, db)
    holdings = get_holdings_cached(user_id, token)
    sector_totals = {}
    total_value = 0
    for h in holdings:
        sector = SECTOR_MAP.get(h["tradingsymbol"], "Other")
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


# ---------------------------------------------------------------------------
# /portfolio/scores — transparent, real-data-derived portfolio scores.
# All math lives in services/portfolio_scores.py; this router only gathers
# the inputs (holdings + sector map, reusing the same logic as /holdings and
# /sector, plus the user's paper NAV series for a real return figure).
# ---------------------------------------------------------------------------

class DiversificationComponents(BaseModel):
    n_holdings: int
    n_sectors: int
    top_holding_pct: float
    top_sector_pct: float
    hhi: float


class DiversificationScore(BaseModel):
    score: int
    components: DiversificationComponents
    explainer: str


class PortfolioScoreComponents(BaseModel):
    subscores: dict[str, float]
    weights: dict[str, float]
    performance_available: bool
    total_return_pct: Optional[float] = None


class PortfolioScore(BaseModel):
    score: int
    components: PortfolioScoreComponents
    explainer: str


class CommunityScore(BaseModel):
    score: int
    percentile: float
    basis: str
    explainer: str


class PortfolioScoresResponse(BaseModel):
    diversification_score: Optional[DiversificationScore] = None
    portfolio_score: Optional[PortfolioScore] = None
    community_score: Optional[CommunityScore] = None
    reason: Optional[str] = None


def _account_and_snapshots(user_id: int, db: Session) -> tuple[Optional[PaperAccount], list]:
    account = (
        db.query(PaperAccount)
        .filter(PaperAccount.user_id == user_id)
        .first()
    )
    if account is None:
        return None, []
    nav_snapshots = (
        db.query(PaperNavSnapshot)
        .filter(PaperNavSnapshot.account_id == account.id)
        .order_by(PaperNavSnapshot.as_of_date.asc())
        .all()
    )
    return account, nav_snapshots


def _user_portfolio_score(user_id: int, db: Session) -> Optional[float]:
    """The bare ``portfolio_score`` number for one user, or None if they
    have no holdings with positive market value. Reused by both the
    requesting user's own score and the peer-pool gathering below, so the
    two are always computed identically."""
    token = get_kite_token(user_id, db)
    holdings = [dict(h) for h in get_holdings_cached(user_id, token)]
    account, nav_snapshots = _account_and_snapshots(user_id, db)
    result = _scores.compute_scores(
        holdings=holdings,
        sector_of=lambda sym: SECTOR_MAP.get(sym, "Other"),
        account=account,
        nav_snapshots=nav_snapshots,
    )
    portfolio = result.get("portfolio_score")
    return float(portfolio["score"]) if portfolio else None


_PEER_SCORES_CACHE_KEY = "portfolio_scores:real_peers:v1"
_PEER_SCORES_CACHE_TTL = 1200  # 20 min — a leaderboard-style stat, not live-tick data


def _real_peer_candidate_ids(db: Session) -> set[int]:
    """User ids with a genuinely differentiated portfolio: a connected
    broker session, or paper-trading with at least one NAV snapshot.

    Users with neither fall back to the shared MOCK_HOLDINGS with no return
    history (see ``backend/kite/portfolio.py::_use_mock``) — their score
    would be byte-identical to every other such user, so they're excluded
    from the peer pool entirely rather than counted as one (or hundreds of
    duplicate) real data points.
    """
    broker_ids = {
        uid for (uid,) in db.query(BrokerSession.user_id)
        .filter(
            BrokerSession.is_active.is_(True),
            BrokerSession.access_token.isnot(None),
        )
        .distinct()
    }
    paper_ids = {
        uid for (uid,) in db.query(PaperAccount.user_id)
        .join(PaperNavSnapshot, PaperNavSnapshot.account_id == PaperAccount.id)
        .distinct()
    }
    return broker_ids | paper_ids


def _real_peer_scores(db: Session, exclude_user_id: int) -> list[float]:
    """Real portfolio scores for other users, cached for
    ``_PEER_SCORES_CACHE_TTL`` since gathering them means walking every
    candidate user's holdings — too expensive to redo on every page load of
    every user's scores tab."""
    cache_key = _PEER_SCORES_CACHE_KEY
    by_user: dict[int, float] = {}
    try:
        cached = redis_client.get(cache_key)
        if cached:
            by_user = {int(k): v for k, v in json.loads(cached).items()}
    except Exception:  # noqa: BLE001 — cache is best-effort
        logger.debug("peer-scores cache read failed", exc_info=True)

    if not by_user:
        try:
            candidate_ids = _real_peer_candidate_ids(db)
        except Exception:  # noqa: BLE001
            logger.warning("peer-scores candidate query failed", exc_info=True)
            candidate_ids = set()
        for uid in candidate_ids:
            try:
                score = _user_portfolio_score(uid, db)
            except Exception:  # noqa: BLE001 — one bad user must never break the page
                logger.debug("peer score failed for user %s", uid, exc_info=True)
                score = None
            if score is not None:
                by_user[uid] = score
        try:
            redis_client.setex(cache_key, _PEER_SCORES_CACHE_TTL, json.dumps(by_user))
        except Exception:  # noqa: BLE001
            logger.debug("peer-scores cache write failed", exc_info=True)

    return [score for uid, score in by_user.items() if uid != exclude_user_id]


def compute_portfolio_scores(db: Session, user_id: int) -> dict:
    """Compute the three transparent portfolio scores for ``user_id``.

    Lifted from the ``/portfolio/scores`` inline closure so the cache-warm
    background task (:mod:`services.cache_warm`) can populate the same
    ``scores_cache_key(user_id)`` entry the route reads from, without
    reimplementing the peer-scan + compute in a second place. Route and
    warmer share this one function; the cache-aside key/TTL are unchanged.
    """
    token = get_kite_token(user_id, db)
    holdings = [dict(h) for h in get_holdings_cached(user_id, token)]
    account, nav_snapshots = _account_and_snapshots(user_id, db)
    peer_scores = _real_peer_scores(db, exclude_user_id=user_id)
    return _scores.compute_scores(
        holdings=holdings,
        sector_of=lambda sym: SECTOR_MAP.get(sym, "Other"),
        account=account,
        nav_snapshots=nav_snapshots,
        peer_scores=peer_scores,
    )


@router.get("/scores", response_model=PortfolioScoresResponse)
def portfolio_scores(
    user_id: int = Depends(get_user_id), db: Session = Depends(get_db),
):
    """Three transparent, real-data-derived scores for the user's holdings.

    Reuses the same holdings + ``SECTOR_MAP`` logic as ``/holdings`` and
    ``/sector``. Diversification is HHI-based; the composite portfolio score
    blends diversification, a sector-concentration penalty, and — *only if a
    real paper NAV series exists* — a return-based performance leg (otherwise
    that leg is dropped, never fabricated). The community score is a
    percentile against real peer users' scores when enough genuinely
    differentiated peers exist, else a documented benchmark cohort (see
    ``services/portfolio_scores.py``).

    Returns all three scores as ``null`` with ``reason="no_holdings"`` when
    the user has no holdings with positive market value.

    Short-TTL Redis cached (see ``services/portfolio_cache.py``) so a
    dashboard mount that fires this alongside `/summary`/`/holdings`/
    `/api/portfolio/performance` doesn't pay the peer-scan + compute cost
    more than once per TTL window. This endpoint has no dependency on
    `/summary` or `/holdings` having run first — it derives its own
    holdings (via the shared `get_holdings_cached`), so callers should
    fire all four endpoints concurrently rather than sequencing them.
    """
    return cache_aside(
        scores_cache_key(user_id),
        lambda: compute_portfolio_scores(db, user_id),
    )
