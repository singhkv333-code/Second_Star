"""
Portfolio data fetching from Kite.
Falls back to mock data when KITE_MOCK_MODE=True OR when the caller
passes the placeholder "mock_token" (dev users without a real Kite
session). Without the placeholder check, a dev user with KITE_API_KEY
set in env but no completed Kite login flow would hit a TokenException
on every portfolio call.
"""
from backend.kite.auth import KITE_MOCK_MODE, get_authenticated_kite
from backend.kite.mock_data import MOCK_HOLDINGS, MOCK_POSITIONS, MOCK_MARGINS, MOCK_PROFILE


def _use_mock(access_token: str) -> bool:
    return KITE_MOCK_MODE or not access_token or access_token == "mock_token"


def get_profile(access_token: str) -> dict:
    if _use_mock(access_token):
        return MOCK_PROFILE
    kite = get_authenticated_kite(access_token)
    return kite.profile()


def get_holdings(access_token: str) -> list:
    """Returns all long-term holdings (CNC positions)."""
    if _use_mock(access_token):
        return MOCK_HOLDINGS
    kite = get_authenticated_kite(access_token)
    return kite.holdings()


def get_positions(access_token: str) -> dict:
    """Returns intraday and overnight positions."""
    if _use_mock(access_token):
        return {"net": MOCK_POSITIONS, "day": MOCK_POSITIONS}
    kite = get_authenticated_kite(access_token)
    return kite.positions()


def get_margins(access_token: str) -> dict:
    """Returns available cash and margin details."""
    if _use_mock(access_token):
        return MOCK_MARGINS
    kite = get_authenticated_kite(access_token)
    return kite.margins()


def get_portfolio_summary(access_token: str) -> dict:
    """Calculates portfolio summary from holdings."""
    holdings = get_holdings(access_token)
    total_invested = sum(h["average_price"] * h["quantity"] for h in holdings)
    total_current = sum(h["last_price"] * h["quantity"] for h in holdings)
    total_pnl = total_current - total_invested
    day_pnl = sum(h.get("day_change", 0) * h["quantity"] for h in holdings)
    return {
        "total_value": round(total_current, 2),
        "invested_value": round(total_invested, 2),
        "total_pnl": round(total_pnl, 2),
        "total_pnl_pct": round((total_pnl / total_invested * 100) if total_invested else 0, 2),
        "day_pnl": round(day_pnl, 2),
        "num_holdings": len(holdings),
    }
