"""HTTP surface for the Moneycontrol-derived financials DB.

GET /api/financials/{symbol}
  Returns company metadata + latest snapshot of every named field +
  multi-year history for the headline P&L lines. Read-only. Auth
  follows the same dev-mode auto-fallback as the chat router so the
  stock-detail page works without a login flow in development.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Query

from backend.config import settings
from backend.auth.jwt_handler import get_user_id_from_token
from backend.cache import redis_client
from backend.market import financials_db as fdb
from backend.market import yfinance_fundamentals as yff


router = APIRouter(prefix="/api/financials", tags=["Financials"])
logger = logging.getLogger(__name__)

# Fundamentals change quarterly, so the assembled response is safe to cache for a
# long time. Stale-while-revalidate (2026-07-03 perf pass): entries live for
# _RESP_HARD_TTL, but once older than _RESP_SOFT_TTL a read returns the stale
# payload IMMEDIATELY and refreshes on a background thread — so no user ever
# pays the ~1.7s Azure+yfinance assembly after the very first fill. Bump the
# version suffix when the payload shape changes.
_RESP_SOFT_TTL = 1800        # 30 min — background-refresh threshold
_RESP_HARD_TTL = 6 * 3600    # 6 h — absolute expiry
_RESP_CACHE_PREFIX = "financials:resp:v3:"  # v3: bank fields (NPA/NIM/CASA)

# One in-flight background refresh per symbol.
_refresh_inflight: set[str] = set()
_refresh_lock = threading.Lock()


def _write_financials_cache(sym: str, payload: dict) -> None:
    try:
        redis_client.setex(
            f"{_RESP_CACHE_PREFIX}{sym}", _RESP_HARD_TTL,
            json.dumps({"_swr_v": payload, "_swr_ts": time.time()}),
        )
    except Exception:  # noqa: BLE001 — cache write is best-effort
        logger.debug("financials cache write failed for %s", sym, exc_info=True)


def _kick_financials_refresh(sym: str) -> None:
    with _refresh_lock:
        if sym in _refresh_inflight:
            return
        _refresh_inflight.add(sym)

    def _run() -> None:
        try:
            _write_financials_cache(sym, _build_financials_payload(sym))
        except Exception:  # noqa: BLE001 — stale keeps serving
            logger.debug("financials SWR refresh failed for %s", sym, exc_info=True)
        finally:
            with _refresh_lock:
                _refresh_inflight.discard(sym)

    threading.Thread(target=_run, name=f"fin-swr:{sym}", daemon=True).start()


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

    # ── response cache (public fundamentals — same for every user) ─────────
    # SWR: fresh → return; stale (> soft TTL) → return stale NOW + refresh in
    # the background; miss → build inline (the only path that ever waits).
    cache_key = f"{_RESP_CACHE_PREFIX}{sym}"
    try:
        cached = redis_client.get(cache_key)
        if cached:
            if isinstance(cached, (bytes, bytearray)):
                cached = cached.decode()
            env = json.loads(cached)
            if isinstance(env, dict) and "_swr_v" in env:
                if time.time() - float(env.get("_swr_ts") or 0) > _RESP_SOFT_TTL:
                    _kick_financials_refresh(sym)
                return env["_swr_v"]
    except Exception:  # noqa: BLE001 — cache is best-effort, never fatal
        logger.debug("financials cache read miss/error for %s", sym, exc_info=True)

    result = _build_financials_payload(sym)
    _write_financials_cache(sym, result)
    return result


def _build_financials_payload(sym: str) -> dict:
    """Assemble the full financials response for ``sym`` (MC primary +
    yfinance fallback + enrich profile). Pure function of the symbol — no
    request-scoped state — so the SWR refresher can run it on a thread."""
    company = fdb.get_company(sym)

    # ── Enrichment DB (yfinance-scraped profile: name/blurb/website/sector) ─
    # Separate Postgres (`pivot_enrich`) filled offline; primary source for the
    # Company Overview panel's website/blurb since `mc.companies` has no such
    # columns. Never raises — a missing DSN or lookup failure just leaves
    # `enr = None` and the router falls back to yfinance profile if available.
    enr = None
    try:
        from backend.market import enrich_db

        if enrich_db.is_enabled():
            enr = enrich_db.get_by_sc_id(company.sc_id) if company is not None else None
            if enr is None:
                enr = enrich_db.get_by_ticker(sym)
    except Exception:  # noqa: BLE001 — enrichment is best-effort
        enr = None

    # ── Moneycontrol (primary) ─────────────────────────────────────────────
    # Latest snapshot + history for EVERY curated field in ONE batched query
    # (was an N+1: ~36 per-field calls, each its own Azure round-trip ≈ 7s). The
    # selection semantics (basis preference / latest period / synonym priority)
    # are identical to the per-field helpers. Each value is tagged with its
    # source so the FE can show provenance.
    latest: dict[str, Optional[dict]] = {}
    history: dict[str, list[dict]] = {}
    if company is not None:
        bulk_latest, bulk_history = fdb.get_company_fundamentals_bulk(
            company.sc_id,
            fields=fdb.list_supported_fields(),
            history_fields=_HISTORY_FIELDS,
            history_limit=_HISTORY_LIMIT,
        )
        for field, v in bulk_latest.items():
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
            history[field] = [
                {
                    "period_end": r.period_end.isoformat() if r.period_end else None,
                    "period_label": r.period_label,
                    "value": float(r.value_numeric) if r.value_numeric is not None else None,
                    "unit": r.unit,
                    "source": "moneycontrol",
                }
                for r in bulk_history.get(field, [])
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
    yf = yff.fetch_fundamentals(sym) if needs_fallback else None
    if yf:
        for field, val in (yf.get("latest") or {}).items():
            if latest.get(field) is None:
                latest[field] = val
        yf_hist = yf.get("history") or {}
        for field in _HISTORY_FIELDS:
            if not history.get(field) and yf_hist.get(field):
                history[field] = yf_hist[field]

    # ── Profile assembly (independent of `needs_fallback`) ─────────────────
    # The Company Overview panel needs name/blurb/website even for MC-well-
    # covered names like RELIANCE. Prefer enrich_db (offline yfinance scrape,
    # no per-request latency); fall back to the yfinance profile we already
    # fetched IFF the ratio-fallback path ran. Never make a *second* live
    # `.info()` call just to fill profile.
    yf_profile = yf.get("profile") if yf else None
    profile: Optional[dict] = None
    if enr is not None or yf_profile is not None:
        profile = {
            "name": (enr.long_name if enr else None) or (yf_profile or {}).get("name"),
            "blurb": (enr.long_business_summary if enr else None) or (yf_profile or {}).get("blurb"),
            "sector": (enr.sector if enr else None) or (yf_profile or {}).get("sector"),
            "industry": (enr.industry if enr else None) or (yf_profile or {}).get("industry"),
            "website": (enr.website if enr else None) or (yf_profile or {}).get("website"),
            "ceo": (yf_profile or {}).get("ceo"),
        }

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


# ── Full balance sheet grid (stock detail page's Balance Sheet tab) ─────────
# Separate from the flat FIELD_MAP snapshot above: this returns every line
# item MC publishes for the statement, with section headers and a multi-year
# column, sourced ONLY from mc_html/mc_api (never yfinance, never
# pivot_derived — that source has no balance_sheet rows anyway). No
# fallback-to-yfinance here on purpose: yfinance's balance sheet has a
# completely different line-item vocabulary, so filling gaps from it would
# silently mix two incompatible schemas in one table.
_BS_CACHE_PREFIX = "financials:bs:v1:"


@router.get("/{symbol}/balance_sheet")
def get_balance_sheet(
    symbol: str,
    basis: str = Query("consolidated", pattern="^(consolidated|standalone)$"),
    authorization: Optional[str] = Header(None),
) -> dict:
    """Full balance sheet for `symbol`.

    Shape:
      {
        "available": bool,
        "company": {...} | null,
        "basis": "consolidated" | "standalone",
        "unit": "Rs. Cr." | null,
        "periods": ["Mar 26", "Mar 25", ...],
        "rows": [
          {"section": str | null, "line_item": str,
           "values": {period_label: float | null},
           "value_texts": {period_label: str | null}},
          ...
        ],
        "source": "moneycontrol"
      }
    """
    _auth(authorization)
    sym = (symbol or "").strip().upper()
    if not sym:
        raise HTTPException(status_code=400, detail="symbol is required")

    cache_key = f"{_BS_CACHE_PREFIX}{sym}:{basis}"
    try:
        cached = redis_client.get(cache_key)
        if cached:
            if isinstance(cached, (bytes, bytearray)):
                cached = cached.decode()
            return json.loads(cached)
    except Exception:  # noqa: BLE001 — cache is best-effort, never fatal
        logger.debug("balance_sheet cache read miss/error for %s", sym, exc_info=True)

    company = fdb.get_company(sym)
    statement = fdb.get_balance_sheet_statement(sym, basis=basis)

    result = {
        "available": bool(statement and statement.get("rows")),
        "company": company.to_dict() if company is not None else None,
        "basis": (statement or {}).get("basis", basis),
        "unit": (statement or {}).get("unit"),
        "periods": (statement or {}).get("periods", []),
        "rows": (statement or {}).get("rows", []),
        "source": "moneycontrol",
    }
    try:
        redis_client.setex(cache_key, _RESP_HARD_TTL, json.dumps(result))
    except Exception:  # noqa: BLE001 — cache write is best-effort
        logger.debug("balance_sheet cache write failed for %s", sym, exc_info=True)
    return result
