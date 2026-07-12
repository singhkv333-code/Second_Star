"""Idea-grain FIFO NAV — the forward-test slice of the paper book (P6).

A LEAF: reads PaperFill rows tagged with `idea_id` and replays them in
chronological order through a per-symbol FIFO lot deque to produce the
idea's `{committed_capital, positions_mv, idea_nav, realized_pnl,
unrealized_pnl}` snapshot. Read-only — never touches PaperPosition (the
account-grain cache); the idea slice is independent of the account's
weighted avg-cost.

Why FIFO + cost-inclusive basis (from `net_cashflow`, not `fill_price`):

  - `fill_price` is the CLEAN touch (mark) used by the broker to compute
    friction; using it for cost basis would drop charges and inflate the
    idea's realized P&L by the buy-side charge. The contract pin says
    BUY basis comes from the net DEBIT: `to_money(-net_cashflow)/qty`.
  - SELLs are cost-inclusive too: `realized = to_money(net_cashflow) −
    Σ(consumed_qty · lot_basis)` — net credit (after sell charges) minus
    the basis on the lots being closed. Partial closes decrement the
    FRONT lot in place and may span multiple lots.

NAV identity (locked by contract): `idea_nav = committed_capital +
positions_mv`. An idea has NO cash sleeve — its NAV is the cost basis of
open lots still committed plus the current MV of those lots. When a
price is unavailable, MV falls back to committed capital (lots valued at
book) and unrealized is 0 — never zero MV, never spurious P&L.

This module is the source the snapshot writer feeds into PaperIdeaNavSnapshot.
All money math is Decimal via `to_money`; floats only at the JSON edge
(scorecard cache / API), which is NOT this module's responsibility.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from decimal import Decimal
from typing import Deque, Dict, Optional

from sqlalchemy.orm import Session

from backend.models import ForwardIdea, PaperFill
from backend.paper.money import money_to_float, to_money
from backend.paper.valuation import PriceFn, _resolve_price_fn


@dataclass
class _Lot:
    """A single FIFO open lot for one symbol within one idea.

    `qty` decrements in place when a SELL partially consumes the front
    lot. `basis_per_share` is full-precision Decimal (we quantize the
    SUMS, not the per-share basis itself — quantizing per share
    accumulates a many-share rounding bias on partial closes)."""
    qty: int
    basis_per_share: Decimal


def _replay_idea_fills(
    db: Session, idea: ForwardIdea,
) -> tuple[Dict[str, "Deque[_Lot]"], Dict[str, Decimal]]:
    """FIFO-replay an idea's fills into per-symbol open lots + realized P&L.

    The shared core of ``compute_idea_nav`` (idea-level Decimal totals) and
    ``compute_idea_positions`` (per-symbol open-position rows), so both read
    the SAME charge-inclusive FIFO ledger and can never drift. Read-only;
    no flush. See the module docstring for the basis contract."""
    fills = (
        db.query(PaperFill)
        .filter(PaperFill.idea_id == idea.id)
        .order_by(PaperFill.filled_at.asc(), PaperFill.id.asc())
        .all()
    )

    # Per-symbol FIFO open-lot deque + per-symbol realized accumulator.
    open_lots: Dict[str, Deque[_Lot]] = {}
    realized_by_symbol: Dict[str, Decimal] = {}

    for f in fills:
        sym = str(f.symbol)
        side = str(f.transaction_type).upper()
        qty = float(f.quantity)  # fractional-safe (US/crypto); was int()
        if qty <= 0:
            # Defensive: a 0-qty fill shouldn't exist, but if it does it
            # contributes nothing and must not divide-by-zero.
            continue
        net_cashflow = to_money(f.net_cashflow)
        lots = open_lots.setdefault(sym, deque())
        realized_by_symbol.setdefault(sym, to_money(0))

        if side == "BUY":
            # BUY net_cashflow is NEGATIVE (debit, inclusive of charges).
            # Basis per share = (−net_cashflow) / qty.
            basis = to_money(-net_cashflow) / Decimal(qty)
            # Store basis at full precision inside the lot; the per-lot
            # cost we'll sum out at the end is qty * basis quantized.
            lots.append(_Lot(qty=qty, basis_per_share=basis))
        elif side == "SELL":
            # SELL net_cashflow is POSITIVE (credit, net of charges).
            # Consume FIFO; the front lot may be PARTIALLY consumed and
            # the SELL may span multiple lots.
            remaining = qty
            cost_consumed = Decimal("0")
            while remaining > 0 and lots:
                front = lots[0]
                take = front.qty if front.qty <= remaining else remaining
                cost_consumed += Decimal(take) * front.basis_per_share
                if take == front.qty:
                    lots.popleft()
                else:
                    front.qty -= take
                remaining -= take
            # If `remaining > 0` here, the idea_id slice has more sells
            # than buys (impossible if the resolver tagged consistently).
            # Treat residual cost as 0 — realized is just the credit.
            realized_by_symbol[sym] = (
                realized_by_symbol[sym]
                + to_money(net_cashflow - to_money(cost_consumed))
            )
        # Any other side (shouldn't exist) is silently skipped.

    return open_lots, realized_by_symbol


def compute_idea_nav(
    db: Session,
    idea: ForwardIdea,
    price_fn: Optional[PriceFn] = None,
) -> Dict[str, Decimal]:
    """Replay the idea's fills FIFO and return its current NAV slice.

    Parameters
    ----------
    db : Session
        Read-only — this function does not flush.
    idea : ForwardIdea
        The idea whose lots/series to roll up. Filtered on `idea_id`
        (NOT account); two ideas trading the same symbol within one
        account each keep their own FIFO ledger.
    price_fn : Optional[PriceFn]
        Inject for tests / batch jobs. Defaults to
        `marks.get_mark_price` via `_resolve_price_fn` (the same
        resolution used by `compute_account_nav`).

    Returns
    -------
    dict with Decimal values for the five fields the snapshot upserter
    writes into PaperIdeaNavSnapshot. Even when no fills exist, all five
    fields are present and equal `to_money(0)` (the empty-idea NAV).
    """
    pf = _resolve_price_fn(price_fn)

    open_lots, realized_by_symbol = _replay_idea_fills(db, idea)

    # ── aggregate ──────────────────────────────────────────────────────
    committed_total = to_money(0)
    positions_mv_total = to_money(0)
    unrealized_total = to_money(0)
    realized_total = to_money(0)

    for sym, lots in open_lots.items():
        open_qty = sum(lot.qty for lot in lots)
        # committed_capital[sym] = Σ qty * basis, quantized once.
        committed_sym = to_money(
            sum(
                (Decimal(lot.qty) * lot.basis_per_share for lot in lots),
                Decimal("0"),
            )
        )
        committed_total += committed_sym

        px_raw = pf(sym) if open_qty > 0 else None
        if px_raw is not None and to_money(px_raw) > 0 and open_qty > 0:
            px = to_money(px_raw)
            mv_sym = to_money(Decimal(open_qty) * px)
            # weighted avg basis = committed / open_qty (qty>0 guarded)
            weighted_avg_basis = committed_sym / Decimal(open_qty)
            unrealized_sym = to_money(
                Decimal(open_qty) * (px - weighted_avg_basis)
            )
        else:
            # Fallback: mark at cost basis -> MV == committed, unreal==0.
            mv_sym = committed_sym
            unrealized_sym = to_money(0)

        positions_mv_total += mv_sym
        unrealized_total += unrealized_sym

    for _sym, r in realized_by_symbol.items():
        realized_total += r

    idea_nav = to_money(committed_total + positions_mv_total)

    return {
        "committed_capital": committed_total,
        "positions_mv": positions_mv_total,
        "idea_nav": idea_nav,
        "realized_pnl": realized_total,
        "unrealized_pnl": unrealized_total,
    }


def compute_idea_positions(
    db: Session,
    idea: ForwardIdea,
    price_fn: Optional[PriceFn] = None,
) -> Dict[str, object]:
    """Per-symbol OPEN positions for one idea (agent) — the Positions view.

    Same charge-inclusive FIFO ledger as ``compute_idea_nav`` (shared via
    ``_replay_idea_fills``), but returns a per-symbol breakdown the agent
    Positions panel renders: each still-open symbol's net qty, weighted-avg
    cost basis, current mark, market value, and unrealized P&L since the
    fills — plus idea-level totals. Money is emitted as ``float`` here (this
    is an API-facing helper, unlike the pure-Decimal ``compute_idea_nav``).
    Fully-closed symbols contribute their realized P&L to the totals but are
    NOT listed as open positions. Read-only; no flush.
    """
    open_lots, realized_by_symbol = _replay_idea_fills(db, idea)
    pf = _resolve_price_fn(price_fn)

    positions: list[Dict[str, object]] = []
    invested_total = to_money(0)      # cost basis of still-OPEN lots
    market_value_total = to_money(0)
    unrealized_total = to_money(0)
    realized_total = to_money(0)

    for sym, lots in open_lots.items():
        open_qty = sum(lot.qty for lot in lots)
        realized_sym = realized_by_symbol.get(sym, to_money(0))
        realized_total += realized_sym
        if open_qty <= 0:
            # Fully closed — booked realized already counted; no open row.
            continue

        committed_sym = to_money(
            sum(
                (Decimal(lot.qty) * lot.basis_per_share for lot in lots),
                Decimal("0"),
            )
        )
        avg_cost = committed_sym / Decimal(open_qty)  # weighted, charge-incl.
        invested_total += committed_sym

        px_raw = pf(sym)
        if px_raw is not None and to_money(px_raw) > 0:
            px = to_money(px_raw)
            mv_sym = to_money(Decimal(open_qty) * px)
            unrealized_sym = to_money(Decimal(open_qty) * (px - avg_cost))
            last_price: Optional[float] = float(px)
        else:
            # No live mark → value at book, zero unrealized (never fabricate).
            mv_sym = committed_sym
            unrealized_sym = to_money(0)
            last_price = None

        market_value_total += mv_sym
        unrealized_total += unrealized_sym

        cost_basis = money_to_float(committed_sym)
        unrl = money_to_float(unrealized_sym)
        positions.append({
            "symbol": sym,
            "quantity": open_qty,
            "avg_cost": money_to_float(to_money(avg_cost)),
            "last_price": last_price,
            "invested": cost_basis,
            "market_value": money_to_float(mv_sym),
            "unrealized_pnl": unrl,
            "unrealized_pnl_pct": (
                round(unrl / cost_basis * 100.0, 2) if cost_basis > 0 else None
            ),
            "realized_pnl": money_to_float(realized_sym),
        })

    # realized_total is accumulated for EVERY symbol in the loop above
    # (before the open-qty guard), so closed symbols' booked P&L is included.

    positions.sort(key=lambda p: p["market_value"], reverse=True)  # type: ignore[arg-type,return-value]

    invested_f = money_to_float(invested_total)
    unrealized_f = money_to_float(unrealized_total)
    return {
        "positions": positions,
        "invested": invested_f,
        "market_value": money_to_float(market_value_total),
        "unrealized_pnl": unrealized_f,
        "unrealized_pnl_pct": (
            round(unrealized_f / invested_f * 100.0, 2)
            if invested_f > 0 else None
        ),
        "realized_pnl": money_to_float(realized_total),
    }
