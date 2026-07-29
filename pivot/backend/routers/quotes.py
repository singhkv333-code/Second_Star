"""Index quotes endpoints — back the Portfolio benchmark overlay (#50).

Phase 1 of the FE redesign needs an index-history series for the
benchmark overlay on the Portfolio performance chart. It's a thin
mapping over `markets.get_sparkline` that translates user-friendly
index names (NIFTY50, SENSEX, BANKNIFTY, NIFTYMIDCAP100) into the
yfinance ticker symbols the markets endpoint already understands
(`^NSEI`, `^BSESN`, `^NSEBANK`, `^NSEMDCP50`).

Endpoint:
  GET /api/quotes/index/{symbol}/history?period=1Y

Response shape mirrors `SparklineResponse` from markets.py so the FE
can use the same TypeScript type either way.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from typing import Literal

from backend.routers._deps import require_user
from backend.routers._errors import not_found
from backend.routers.markets import (
    SparklineResponse,
    get_sparkline as _get_sparkline,
)


router = APIRouter(prefix="/api/quotes", tags=["Quotes"])


# Friendly aliases the FE sends → ^-prefixed yfinance tickers the markets
# router knows how to resolve.
_INDEX_ALIAS: dict[str, str] = {
    "NIFTY50": "^NSEI",
    "NIFTY-50": "^NSEI",
    "NIFTY_50": "^NSEI",
    "SENSEX": "^BSESN",
    "BANKNIFTY": "^NSEBANK",
    "BANK-NIFTY": "^NSEBANK",
    "NIFTYMIDCAP100": "^NSEMDCP50",
    "NIFTY-MIDCAP-100": "^NSEMDCP50",
}


_RangeLiteral = Literal["1D", "1W", "1M", "3M", "6M", "1Y", "5Y"]


@router.get(
    "/index/{symbol}/history",
    response_model=SparklineResponse,
    summary="Historical close series for a benchmark index (alias over markets/sparkline)",
)
def get_index_history(
    symbol: str,
    period: _RangeLiteral = Query("1Y"),
    user_id: int = Depends(require_user),
) -> SparklineResponse:
    """Resolve `symbol` to a yfinance ticker, then call the existing
    markets sparkline handler. Anything starting with `^` passes
    through unchanged (advanced users hitting the endpoint directly)."""
    key = symbol.upper().replace(" ", "")
    yf_sym = _INDEX_ALIAS.get(key)
    if yf_sym is None:
        if symbol.startswith("^"):
            yf_sym = symbol
        else:
            raise not_found(
                f"unknown benchmark index '{symbol}' "
                f"(known: {sorted(_INDEX_ALIAS.keys())})"
            )
    # `_get_sparkline` already applies the auth check via require_user
    # on its own DI; we've already cleared auth here, but re-running is
    # harmless. Pass the resolved ^-symbol; markets.py will skip the
    # exchange suffix because it starts with ^.
    return _get_sparkline(symbol=yf_sym, range=period, _user_id=user_id)
