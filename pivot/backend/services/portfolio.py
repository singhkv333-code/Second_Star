"""Portfolio service — the canonical place for fetching a user's holdings,
buying power, and total value.

Wraps the existing Kite client (`backend/kite/portfolio.py`). The
workflows engine and the legacy `routers/portfolio.py` both call into
here so the data shape is consistent. The function returns plain dicts
(no SQLAlchemy objects) so it's safe to embed directly into a run's
context bag.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from backend.kite.auth import read_kite_access_token
from backend.kite.portfolio import (
    get_holdings,
    get_margins,
    get_portfolio_summary,
)
from backend.models import User


def _kite_token_for_user(user_id: int, db: Session) -> str:
    """Resolve a user's Kite access token. Falls back to
    `'mock_token'` when no session is present so KITE_MOCK_MODE picks
    up the mock data path. Mirrors routers/portfolio.py."""
    user = db.query(User).filter(User.id == user_id).first()
    if user and user.active_broker_session and user.active_broker_session.access_token:
        return read_kite_access_token(user.active_broker_session) or "mock_token"
    return "mock_token"


def get_user_portfolio(user_id: int, db: Session) -> dict[str, Any]:
    """Return the portfolio shape consumed by the `fetch.portfolio`
    workflow step:

        {
          "holdings": [{...}, ...],
          "buying_power": float,
          "total_value": float,
        }

    `buying_power` is `equity.available.live_balance` from Kite margins,
    `total_value` is the summary's mark-to-market sum of holdings.
    """
    token = _kite_token_for_user(user_id, db)

    holdings_raw = get_holdings(token)
    summary = get_portfolio_summary(token)
    margins = get_margins(token)

    equity = margins.get("equity", {}) if isinstance(margins, dict) else {}
    available = equity.get("available", {}) if isinstance(equity, dict) else {}
    buying_power = float(available.get("live_balance", 0.0) or 0.0)

    total_value = float(summary.get("total_value", 0.0) or 0.0)

    # Strip down to the columns the engine cares about; deeper data is
    # available via the dedicated /portfolio/* routes.
    holdings = [
        {
            "tradingsymbol": h.get("tradingsymbol"),
            "exchange": h.get("exchange"),
            "quantity": h.get("quantity"),
            "average_price": h.get("average_price"),
            "last_price": h.get("last_price"),
        }
        for h in (holdings_raw or [])
    ]

    return {
        "holdings": holdings,
        "buying_power": buying_power,
        "total_value": total_value,
    }
