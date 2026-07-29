"""Paper-aware portfolio read source.

One resolver that answers "what are this user's holdings, in the Kite holdings
shape?" — sourced from the **simulated paper book** when the account is in
paper mode (``should_use_paper``), else from the Kite/broker path (via the
short-TTL ``get_holdings_cached``).

WHY this exists: the portfolio read endpoints (``/portfolio/summary``,
``/holdings``, ``/sector``, ``/scores`` and the ``/api/portfolio/performance``
chart) were all hard-wired to the Kite source, so the Portfolio page + its
value graph showed the shared MOCK book regardless of what the user actually
did. Routing them through this resolver makes every one of them REACTIVE to the
user's real paper positions (their buys/sells, baskets, opinion-market
expressions and armed agents all fill the same paper book). The paper rows are
adapted to the exact Kite holdings shape so downstream consumers need no
per-source branching.

Leaf, read-only: never writes, marks or commits.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from backend.services.portfolio_cache import get_holdings_cached


def _paper_to_kite_holding(row: dict) -> dict:
    """Map one paper-book holding (``backend.paper.portfolio.holdings`` shape)
    onto the Kite holdings shape the portfolio endpoints emit.

    ``day_change`` is Kite's PER-SHARE day move; the paper book stores the
    position's TOTAL ``day_pnl``, so we divide by quantity (0 when qty is 0).
    ``last_price`` falls back to book cost for an unmarked lot (mirrors the FE
    ``adaptPaperHolding``). Exchange is NSE — the paper book is NSE-only.
    """
    qty = int(row.get("quantity") or 0)
    # Prefer the clean BUY price (ex-charges) so the holdings table shows the
    # price actually paid next to the LTP, not the charge-inclusive cost basis.
    avg = float(row.get("buy_price") or row.get("avg_cost") or 0.0)
    last = row.get("last_price")
    last = float(last) if last is not None else avg
    day_pnl = float(row.get("day_pnl") or 0.0)
    invested = float(row.get("invested") or (avg * qty))
    return {
        "tradingsymbol": row.get("symbol"),
        "exchange": "NSE",
        "quantity": qty,
        "average_price": round(avg, 2),
        "last_price": round(last, 2),
        "pnl": round(float(row.get("unrealized_pnl") or 0.0), 2),
        "day_change": round(day_pnl / qty, 4) if qty else 0.0,
        "day_change_percentage": (
            round(day_pnl / invested * 100, 2) if invested else 0.0
        ),
    }


def resolve_holdings(db: Session, user_id: int, kite_token: str) -> list[dict]:
    """Holdings in the Kite shape, from the paper book (paper mode) or Kite.

    Falls back to the Kite/cached source on any paper-side error so a portfolio
    read never hard-fails on a paper-book blip.
    """
    from backend.paper.routing import should_use_paper

    if should_use_paper(db, int(user_id)):
        try:
            from backend.paper import portfolio as _paper
            return [_paper_to_kite_holding(r) for r in _paper.holdings(db, int(user_id))]
        except Exception:  # noqa: BLE001 — never let paper break a read
            import logging
            logging.getLogger(__name__).warning(
                "resolve_holdings: paper read failed for user %s; "
                "falling back to kite", user_id, exc_info=True,
            )
    return [dict(h) for h in get_holdings_cached(user_id, kite_token)]


def paper_cash_and_nav(db: Session, user_id: int) -> Optional[tuple[float, float]]:
    """``(cash, nav)`` for the user's paper account, or None when not in paper
    mode / no account. ``cash`` = available + reserved (money still owned,
    just held against a resting order); ``nav`` = cash + positions market value.

    Used by the performance chart to (a) add cash as a flat baseline so the
    reconstructed curve ends at the true NAV, and (b) draw a flat NAV line for
    an all-cash book instead of erroring on "no holdings".
    """
    from backend.paper.routing import should_use_paper

    if not should_use_paper(db, int(user_id)):
        return None
    try:
        from backend.paper.portfolio import account_summary
        summary = account_summary(db, int(user_id))
    except Exception:  # noqa: BLE001
        return None
    if not summary.get("exists"):
        return None
    cash = float(summary.get("cash_available") or 0.0) + float(
        summary.get("cash_reserved") or 0.0
    )
    nav = float(summary.get("nav") or cash)
    return cash, nav
