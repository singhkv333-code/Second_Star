"""IPO paper-mode allotment simulator (P3) — a LABELLED ledger.

When a user is in paper mode and registers / arms an IPO intent, this
module records a parallel ``PaperIpoAllocation`` row that simulates the
allotment outcome with a DETERMINISTIC lottery. The row is purely a
tracking artefact — IT DOES NOT mutate the paper account's cash,
positions or NAV. That integration (cash debit on allocation, listing-
day mark, P&L recording) is deferred to P3.1.

Why this exists
---------------
SME issues, in particular, are draw-heavy: a retail user wants to "track
every SME this quarter" without putting real money in motion. P3 makes
that possible — the user's actual IPO intent row (``IPOApplication``) is
still register-not-execute, and the parallel paper allocation lets them
see the forward outcome on the Paper dashboard.

Allotment model (load-bearing simplifications)
----------------------------------------------
* DETERMINISTIC: the lottery seed is ``sha256(f"{user_id}:{symbol}:
  {app_row.id}")`` (NEVER an unseeded RNG and NEVER wall-clock). Same
  inputs -> same outcome on a re-run. This is what makes the tests in
  ``tests/test_paper_ipo_sim.py`` honest.
* PER-CATEGORY ODDS: when a live per-category subscription multiple is
  available on ``ipo_record`` (``rii`` for retail/employee, ``nii`` for
  snii/bnii, ``qib`` for QIB) and is > 1.0, we take ``prob = min(1.0,
  1.0/sub)``. Otherwise we fall back to a documented constant
  (``MAINBOARD_DEFAULT_ODDS=0.30``, ``SME_DEFAULT_ODDS=0.55``). These
  are explicitly illustrative, not predictive.
* ALL-OR-NOTHING: a win awards ``quantity_applied`` (full lot count);
  a loss awards 0. Real allotment scales partial wins by retail-bucket
  rounding; modelling that introduces noise without adding signal to a
  forward-test view. Documented here so it is never lost in code review.

NO FUND MOVEMENT
----------------
The simulator NEVER calls ``PaperBroker`` or any cash/position helper.
``PaperAccount.cash_available`` / ``cash_reserved`` are not touched.
Tests assert this invariant directly; callers (the register endpoint
and the arm executor) likewise must not synthesise a debit.

Caller contract
---------------
* Pass the freshly persisted ``IPOApplication`` row (``app_row``) and
  the live IPO record dict (``ipo_record``, the ``ipo`` sub-object from
  ``ipo_feed.get_ipo_details`` — may be ``None`` when the feed was
  unreachable, in which case we fall back to the app row's stored
  fields).
* The caller commits the session. We only ``db.add(row)`` so the same
  session that committed the IPO application also commits the
  allocation, atomically.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy.orm import Session

from backend.models import IPOApplication, PaperIpoAllocation
from backend.paper.accounts import get_or_create_account
from backend.utils.time_utils import is_trading_day

logger = logging.getLogger(__name__)


# ── Documented illustrative odds (NOT predictive) ─────────────────────────

#: Default win probability for a mainboard IPO when no per-category
#: subscription multiple is available on the live feed. ~3.3x oversub
#: implied (1/0.3). Illustrative only — the FE never quotes this number
#: as a forecast.
MAINBOARD_DEFAULT_ODDS: float = 0.30

#: Default win probability for an SME IPO when no per-category
#: subscription multiple is available. SME draws are smaller pools so
#: the implied multiple is ~1.8x.
SME_DEFAULT_ODDS: float = 0.55


# ── Lottery primitives ────────────────────────────────────────────────────

def _deterministic_uniform(user_id: int, symbol: str, application_id: int) -> float:
    """Map (user_id, symbol, application_id) -> a uniform [0, 1) value.

    The hash seed is what makes the simulator REPRODUCIBLE across runs
    and across processes — the regression test in
    ``test_lottery_outcome_is_deterministic`` asserts that two calls
    with the same inputs land on the same allotment_status.
    """
    raw = f"{int(user_id)}:{str(symbol).upper()}:{int(application_id)}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    # Take the first 8 hex chars -> 32 bits -> divide by 2^32 for a
    # uniform [0, 1) draw. /0xFFFFFFFF (2^32-1) keeps the result in
    # [0, 1.0] but never exceeds 1 because hex chars cannot exceed F.
    return int(digest[:8], 16) / 0xFFFFFFFF


def _resolve_subscription_multiple(
    ipo_record: Optional[dict[str, Any]],
    category: str,
) -> Optional[float]:
    """Pluck the per-category live subscription multiple, if any.

    Mapping (per :func:`backend.services.ipo_feed.fetch_subscription`'s
    output shape):

      retail / employee  -> rii
      snii / bnii        -> nii
      shareholder        -> shareholder (rare; falls back if missing)

    Returns ``None`` when the multiple is missing or non-positive — the
    caller then uses the type-default constant.
    """
    if not ipo_record:
        return None
    sub = ipo_record.get("subscription")
    if not isinstance(sub, dict):
        return None
    key_map = {
        "retail": "rii",
        "employee": "rii",
        "snii": "nii",
        "bnii": "nii",
        "shareholder": "shareholder",
    }
    key = key_map.get(category)
    if key is None:
        return None
    val = sub.get(key)
    try:
        if val is None:
            return None
        mult = float(val)
    except (TypeError, ValueError):
        return None
    if mult <= 0.0:
        return None
    return mult


def _win_probability(
    *,
    ipo_type: str,
    category: str,
    ipo_record: Optional[dict[str, Any]],
) -> float:
    """Probability of a full-lot allotment in the deterministic lottery.

    Live per-category subscription multiples drive the prob when they
    exist and are > 1.0 (``1/sub`` is the textbook back-of-envelope
    allotment odds). Otherwise we fall back to the documented illustrative
    constants. Always clamped to (0, 1].
    """
    multiple = _resolve_subscription_multiple(ipo_record, category)
    if multiple is not None and multiple > 1.0:
        prob = 1.0 / multiple
        return max(0.0, min(1.0, prob))
    if ipo_type == "sme":
        return SME_DEFAULT_ODDS
    return MAINBOARD_DEFAULT_ODDS


# ── Date helper ───────────────────────────────────────────────────────────

def _next_trading_day(from_day: date) -> date:
    """Return the next trading day strictly after ``from_day``.

    Uses :func:`backend.utils.time_utils.is_trading_day` (weekday check;
    holidays not modelled here). One step at a time so the loop bound is
    obvious — pathological gaps are at most ~3 days for a long weekend.
    """
    candidate = from_day + timedelta(days=1)
    # Worst case: a weekend + bank holiday, so cap the loop conservatively.
    # 7 iterations is plenty; if we still haven't hit one something is
    # very wrong upstream and we bail to avoid an infinite loop.
    for _ in range(7):
        # is_trading_day accepts a datetime; date works too because of
        # to_ist's path, but be explicit by constructing a midnight dt.
        from datetime import datetime
        as_dt = datetime(candidate.year, candidate.month, candidate.day)
        if is_trading_day(as_dt):
            return candidate
        candidate = candidate + timedelta(days=1)
    return candidate


# ── Issue-price helper ────────────────────────────────────────────────────

def _resolve_issue_price(
    *,
    app_row: IPOApplication,
    ipo_record: Optional[dict[str, Any]],
) -> Decimal:
    """The per-share price the user effectively committed to.

    cutoff bid -> price_band.max (the standard convention; the user pays
    the eventual allotment price but the band.max sets the cap)
    fixed bid  -> app_row.bid_price

    We trust the app_row's stored numbers when they are present; the
    live ``ipo_record`` is consulted only to recover band.max when the
    bid mode is cut-off. NEVER fabricates a number — we fall back to a
    last-ditch ``amount_estimate / quantity`` if all else fails so the
    Numeric column never sees None.
    """
    if app_row.bid_price_mode == "fixed" and app_row.bid_price is not None:
        return Decimal(str(app_row.bid_price))
    # cutoff path or missing fixed bid -> pull band.max from feed
    if ipo_record:
        from backend.services.ipo_feed import parse_price_band

        band = parse_price_band(ipo_record.get("price_band"))
        if band is not None and band.get("max") is not None:
            return Decimal(str(band["max"]))
    # Honest fallback: derive from amount_estimate / quantity (lots * lot_size).
    # This keeps the Numeric NOT NULL invariant intact when both the band
    # and the bid_price are absent (e.g. the stale-feed armed path).
    qty = int(app_row.quantity_lots) * int(app_row.lot_size)
    if qty > 0 and app_row.amount_estimate:
        return Decimal(str(app_row.amount_estimate)) / Decimal(qty)
    return Decimal("0")


# ── Public entrypoint ─────────────────────────────────────────────────────

def simulate_paper_ipo_allocation(
    db: Session,
    user_id: int,
    *,
    app_row: IPOApplication,
    ipo_record: Optional[dict[str, Any]] = None,
    source: str,
) -> PaperIpoAllocation:
    """Record a SIMULATED allotment for a paper-mode IPO intent.

    Parameters
    ----------
    db : Session
        Sync SQLAlchemy session. The caller commits.
    user_id : int
        The IPO applicant; used to derive the deterministic lottery
        seed and the PaperAccount lookup.
    app_row : IPOApplication
        The freshly persisted IPO application row. The deterministic
        seed depends on ``app_row.id``, so this MUST be flushed
        (i.e. ``db.commit()`` / ``db.refresh(row)`` has been called)
        before we are invoked.
    ipo_record : dict | None
        The live IPO record dict from ``ipo_feed.get_ipo_details`` (the
        ``ipo`` sub-object). Optional — when ``None`` we fall back to
        the app_row's stored fields and use the type-default odds.
    source : str
        Audit trail of which call site invoked the simulator
        (``"chat-register"`` from the REST register endpoint,
        ``"workflow-arm"`` from the arm executor).

    Returns
    -------
    PaperIpoAllocation
        The row, added to the session but NOT yet committed.

    Notes
    -----
    * The PaperAccount is fetched via ``get_or_create_account`` — same
      seam every other paper-domain helper uses.
    * No cash / positions / NAV mutation happens here. The PaperAccount
      row is read only to obtain ``.id`` for the allocation FK.
    * ``simulated=True`` is hard-coded on the resulting row. The column
      default already enforces this; we set it explicitly so a reader of
      this function never has to chase that invariant.
    """
    account = get_or_create_account(db, int(user_id))
    if app_row.id is None:
        # The caller must have committed/refreshed first — otherwise the
        # deterministic seed is built on a None id and the row's
        # ipo_application_id soft-ref is meaningless.
        raise ValueError(
            "simulate_paper_ipo_allocation: app_row.id is None — "
            "commit/refresh the IPOApplication before invoking the simulator."
        )

    lots = int(app_row.quantity_lots)
    lot_size = int(app_row.lot_size)
    quantity_applied = lots * lot_size
    # Coerce amount_estimate to Decimal for the Numeric column. The model
    # stores it as Float (legacy), so we wrap in str() to avoid binary-FP
    # round-trip surprises in the ledger.
    amount_applied = Decimal(str(app_row.amount_estimate or 0))
    issue_price = _resolve_issue_price(app_row=app_row, ipo_record=ipo_record)

    # Deterministic lottery.
    u = _deterministic_uniform(int(user_id), app_row.ipo_symbol, int(app_row.id))
    prob = _win_probability(
        ipo_type=str(app_row.ipo_type),
        category=str(app_row.category),
        ipo_record=ipo_record,
    )
    win = u < prob
    quantity_allotted = quantity_applied if win else 0
    allotment_status = "allotted" if win else "not_allotted"

    # Allotment date = close_date + 1 trading day. close_date may be
    # missing (stale feed) — in that case we leave it None (honest).
    allotment_date: Optional[date] = None
    listing_date: Optional[date] = None
    if ipo_record:
        close_raw = ipo_record.get("close_date")
        if close_raw:
            try:
                close_d = date.fromisoformat(str(close_raw))
                allotment_date = _next_trading_day(close_d)
            except (TypeError, ValueError):
                allotment_date = None
        listing_raw = ipo_record.get("listing_date")
        if listing_raw:
            try:
                listing_date = date.fromisoformat(str(listing_raw))
            except (TypeError, ValueError):
                listing_date = None

    row = PaperIpoAllocation(
        user_id=int(user_id),
        paper_account_id=str(account.id),
        ipo_application_id=int(app_row.id),
        ipo_symbol=str(app_row.ipo_symbol).upper(),
        ipo_name=app_row.ipo_name,
        ipo_type=str(app_row.ipo_type),
        lots_applied=lots,
        quantity_applied=quantity_applied,
        amount_applied=amount_applied,
        issue_price=issue_price,
        quantity_allotted=quantity_allotted,
        allotment_status=allotment_status,
        allotment_date=allotment_date,
        listing_date=listing_date,
        listing_price=None,    # P3.1 placeholder — never fabricated.
        simulated_pnl=None,    # P3.1 placeholder.
        conversation_id=app_row.conversation_id,
        workflow_id=(
            str(app_row.workflow_id) if app_row.workflow_id is not None else None
        ),
        source=source,
        simulated=True,
    )
    db.add(row)
    logger.info(
        "paper-ipo-sim: user=%s symbol=%s app_id=%s u=%.4f prob=%.3f "
        "outcome=%s qty=%s source=%s",
        user_id, app_row.ipo_symbol, app_row.id, u, prob,
        allotment_status, quantity_allotted, source,
    )
    return row


def serialize_paper_ipo_allocation(row: PaperIpoAllocation) -> dict[str, Any]:
    """JSON-safe shape for the ``GET /paper/ipo-allocations`` endpoint
    and the register-response ``paper_simulation`` field.

    All Decimals are cast to ``float`` at the JSON edge (the standard
    paper-domain convention; see ``backend/paper/portfolio.py``). Dates
    are ISO ``YYYY-MM-DD`` so the FE renderer can parse them with the
    same shared formatter the other paper sections use.
    """
    return {
        "id": str(row.id),
        "ipo_symbol": row.ipo_symbol,
        "ipo_name": row.ipo_name,
        "ipo_type": row.ipo_type,
        "lots_applied": int(row.lots_applied),
        "quantity_applied": int(row.quantity_applied),
        "amount_applied": float(row.amount_applied) if row.amount_applied is not None else 0.0,
        "issue_price": float(row.issue_price) if row.issue_price is not None else 0.0,
        "quantity_allotted": int(row.quantity_allotted),
        "allotment_status": row.allotment_status,
        "allotment_date": (
            row.allotment_date.isoformat() if row.allotment_date else None
        ),
        "listing_date": (
            row.listing_date.isoformat() if row.listing_date else None
        ),
        "conversation_id": row.conversation_id,
        "simulated": bool(row.simulated),
        "created_at": (
            row.created_at.isoformat() if row.created_at is not None else None
        ),
    }
