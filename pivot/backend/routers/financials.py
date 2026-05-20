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
    "revenue",
    "operating_profit",
    "net_profit",
    "eps_basic",
    "interest_expense",
    "cash_from_ops",
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
    if company is None:
        return {
            "available": False,
            "company": None,
            "latest": {},
            "history": {},
            "source": "moneycontrol_via_financials_db",
        }

    # Latest snapshot — every curated field. Caller decides which to
    # display; we don't pre-filter so adding a new metric is a FE-only
    # change.
    latest: dict[str, Optional[dict]] = {}
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
        }

    # Multi-year history for the headline lines used in the P&L table.
    history: dict[str, list[dict]] = {}
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
            }
            for r in rows
        ]

    return {
        "available": True,
        "company": company.to_dict(),
        "latest": latest,
        "history": history,
        "source": "moneycontrol_via_financials_db",
    }
