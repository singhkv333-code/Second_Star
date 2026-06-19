"""HTTP surface for the Moneycontrol-derived financials DB.

GET /api/financials/{symbol}
  Returns company metadata + latest snapshot of every named field +
  multi-year history for the headline P&L lines. Read-only. Auth
  follows the same dev-mode auto-fallback as the chat router so the
  stock-detail page works without a login flow in development.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Header, HTTPException

from backend.config import settings
from backend.auth.jwt_handler import get_user_id_from_token
from backend.market import financials_db as fdb
from backend.market import yfinance_fundamentals as yff


router = APIRouter(prefix="/api/financials", tags=["Financials"])


def _auth(authorization: Optional[str]) -> int:
    if not authorization:
        if getattr(settings, "app_env", "development") == "development":
            return 1
        raise HTTPException(status_code=401, detail="Missing token")
    uid = get_user_id_from_token(authorization.replace("Bearer ", ""))
    if not uid:
        raise HTTPException(status_code=401, detail="Invalid token")
    return uid


# Fields whose multi-year trajectory we surface to the FE for the
# Financials + P&L tables. Order is the display order. We keep this
# focused — the latest snapshot already carries all 26 ratios; history
# is only useful for line items that change meaningfully year-over-year.
_HISTORY_FIELDS: tuple[str, ...] = (
    # Profit & Loss lines
    "revenue",
    "operating_profit",
    "net_profit",
    "eps_basic",
    "interest_expense",
    "cash_from_ops",
    # Balance-sheet lines — power the stock page's Balance Sheet tab.
    "total_equity",
    "reserves",
    "total_debt",
    "book_value_per_share",
)

_HISTORY_LIMIT = 6  # last six fiscal years — keeps payload bounded


@router.get("/{symbol}")
def get_financials(symbol: str, authorization: Optional[str] = Header(None)) -> dict:
    """Return everything we know about `symbol` from the financials DB.

    Shape:
      {
        "available": bool,
        "company": {...} | null,
        "latest": { <field>: { value, period_end, line_item, unit } | null, ... },
        "history": { <field>: [ {period_end, value, period_label}, ... ], ... },
        "source": "moneycontrol_via_financials_db"
      }

    When the symbol has no entry in `mc.companies`, returns
    `available=false` with everything else null/empty. The FE then
    falls back to its existing placeholder rendering.
    """
    _auth(authorization)
    sym = (symbol or "").strip().upper()
    if not sym:
        raise HTTPException(status_code=400, detail="symbol is required")

    company = fdb.get_company(sym)

    # ── Moneycontrol (primary) ─────────────────────────────────────────────
    # Latest snapshot — every curated field. Caller decides which to display;
    # we don't pre-filter so adding a new metric is a FE-only change. Each
    # value is tagged with its source so the FE can show provenance.
    latest: dict[str, Optional[dict]] = {}
    history: dict[str, list[dict]] = {}
    if company is not None:
        for field in fdb.list_supported_fields():
            v = fdb.get_fundamental(company.sc_id, field)
            if v is None or v.value_numeric is None:
                latest[field] = None
                continue
            latest[field] = {
                "value": float(v.value_numeric),
                "period_end": v.period_end.isoformat() if v.period_end else None,
                "period_label": v.period_label,
                "line_item": v.line_item,
                "unit": v.unit,
                "basis": v.basis,
                "source": "moneycontrol",
            }

        for field in _HISTORY_FIELDS:
            rows = fdb.get_fundamental_history(
                company.sc_id, field, limit=_HISTORY_LIMIT,
            )
            history[field] = [
                {
                    "period_end": r.period_end.isoformat() if r.period_end else None,
                    "period_label": r.period_label,
                    "value": float(r.value_numeric) if r.value_numeric is not None else None,
                    "unit": r.unit,
                    "source": "moneycontrol",
                }
                for r in rows
            ]

    # ── yfinance (fallback) ────────────────────────────────────────────────
    # Fill any field MC left null and any empty history series. This is what
    # lets banks (HDFC has no ratios/balance-sheet rows in MC) and the ~half
    # of the universe without ratio data still render real numbers.
    # Only reach for yfinance when MC coverage is genuinely thin — otherwise a
    # well-covered name (Reliance) would make a slow .info call just to fill a
    # stray null. Headline fields drive the Key Metrics strip + Balance Sheet.
    _HEADLINE_LATEST = ("roe", "price_to_book", "net_profit_margin", "ev_to_ebitda", "current_ratio")
    _HEADLINE_HIST = ("total_equity", "total_debt", "revenue", "net_profit")
    needs_fallback = (
        company is None
        or sum(latest.get(f) is not None for f in _HEADLINE_LATEST) < 3
        or sum(bool(history.get(f)) for f in _HEADLINE_HIST) < 2
    )
    profile = None
    if needs_fallback:
        yf = yff.fetch_fundamentals(sym)
        if yf:
            profile = yf.get("profile")
            for field, val in (yf.get("latest") or {}).items():
                if latest.get(field) is None:
                    latest[field] = val
            yf_hist = yf.get("history") or {}
            for field in _HISTORY_FIELDS:
                if not history.get(field) and yf_hist.get(field):
                    history[field] = yf_hist[field]

    available = bool(
        company is not None
        or any(v is not None for v in latest.values())
        or any(history.get(f) for f in _HISTORY_FIELDS)
    )

    if company is not None:
        company_dict = company.to_dict()
    elif available:
        # Synthesize a minimal company record so the FE has a name/sector.
        company_dict = {
            "sc_id": None,
            "name": (profile or {}).get("name") or sym,
            "nse_symbol": sym,
            "bse_code": None,
            "ticker": sym,
            "sector": (profile or {}).get("sector"),
            "industry_slug": (profile or {}).get("industry"),
            "market_cap": None,
            "is_active": True,
        }
    else:
        company_dict = None

    return {
        "available": available,
        "company": company_dict,
        "latest": latest,
        "history": history,
        "profile": profile,
        "source": "moneycontrol_with_yfinance_fallback",
    }
