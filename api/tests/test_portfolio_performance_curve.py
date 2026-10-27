"""Regression tests for the paper-book equity curve (`_paper_performance`).

Guards the two things the portfolio chart got wrong for a fractional (crypto/US)
book:
  1. The curve must OPEN at the account's starting capital (initial capital),
     not at a broken reconstruction, and
  2. A book in the red must slope DOWN to the live NAV — never rise.

Offline by construction: `paper_cash_and_nav` (live-marked NAV) and the yfinance
history fetch are both stubbed so the reconstruction is deterministic and the
crypto legs mark at their stored average cost.
"""
import datetime as dt

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from backend.database import Base
from backend.models import (  # noqa: F401 — registers tables on Base.metadata
    PaperAccount,
    PaperFill,
    PaperLedgerEntry,
    PaperNavSnapshot,
    PaperOrder,
    PaperPosition,
    User,
)
from backend.paper.broker import PaperBroker
from backend.paper.money import to_money
import backend.routers.portfolio_perf as perf
import backend.services.portfolio_source as psource


@pytest.fixture()
def session():
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )

    @event.listens_for(engine, "connect")
    def _fk(c, _):
        cur = c.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    yield db
    db.close()
    engine.dispose()


def _user(db):
    u = User(email=f"x{id(db)}@e.com", hashed_password="x")
    db.add(u)
    db.flush()
    return u


def _buy(db, user_id, sym, qty, px):
    PaperBroker(db, user_id, price_fn=lambda s: to_money(px)).place_order(
        tradingsymbol=sym,
        transaction_type="BUY",
        quantity=qty,
        order_type="MARKET",
    )


def test_curve_starts_at_initial_capital_and_slopes_down_for_a_loss(
    session, monkeypatch
):
    """Two fractional crypto lots, then a live NAV slightly below the seed:
    the curve opens at the starting capital and ends at the (lower) NAV."""
    u = _user(session)
    # Fractional crypto buys — the qty the old int() truncation mangled.
    _buy(session, u.id, "SOL-USD", 6.77657126, 7394.29)
    _buy(session, u.id, "DOGE-USD", 7124.94300045, 7.03)
    acct = (
        session.query(PaperAccount)
        .filter(PaperAccount.user_id == u.id)
        .first()
    )
    seed = float(acct.starting_capital)
    cash = float(acct.cash_available)
    nav_now = seed - 2116.0  # the book is in the red (matches the report bug)

    # Live-marked NAV is stubbed (no network); yfinance history returns nothing
    # so both crypto legs mark at their stored average cost.
    monkeypatch.setattr(
        psource, "paper_cash_and_nav", lambda db, uid: (cash, nav_now)
    )
    monkeypatch.setattr(perf, "_fetch_one_series", lambda *a, **k: (1.0, None))

    data = perf._paper_performance(session, u.id, "1Y")
    assert data is not None
    pts = data["points"]
    assert len(pts) >= 2
    values = [p["v"] for p in pts]

    # 1) Opens at the initial capital (within a few rupees of rounding/charges).
    assert values[0] == pytest.approx(seed, abs=5.0)
    assert data["starting_value"] == pytest.approx(seed, abs=5.0)

    # 2) Ends at the live NAV (== the portfolio header value).
    assert values[-1] == pytest.approx(nav_now, abs=0.01)

    # 3) A losing book must NOT rise: no point sits meaningfully above the seed,
    #    and the reported return is negative (agrees with Total P&L direction).
    assert max(values) <= seed + 5.0
    assert data["total_return"] < 0
    assert data["total_return_pct"] == pytest.approx(
        (nav_now - seed) / seed * 100, abs=0.01
    )


def test_fractional_quantity_is_not_truncated(session, monkeypatch):
    """The interior mark of a fractional lot uses the true (sub-unit) quantity,
    not int(qty) — otherwise ~0.78 SOL of value silently vanishes from the
    curve. We mark at cost and check the position leg contributes qty*avg_cost
    in full."""
    u = _user(session)
    _buy(session, u.id, "SOL-USD", 6.77657126, 7394.29)
    acct = (
        session.query(PaperAccount)
        .filter(PaperAccount.user_id == u.id)
        .first()
    )
    seed = float(acct.starting_capital)
    cash = float(acct.cash_available)
    pos = (
        session.query(PaperPosition)
        .filter(PaperPosition.account_id == acct.id)
        .first()
    )
    qty = float(pos.quantity)
    avg = float(pos.avg_cost)
    assert qty == pytest.approx(6.77657126, abs=1e-8)  # fractional preserved

    monkeypatch.setattr(
        psource, "paper_cash_and_nav", lambda db, uid: (cash, seed)
    )
    monkeypatch.setattr(perf, "_fetch_one_series", lambda *a, **k: (1.0, None))

    data = perf._paper_performance(session, u.id, "1M")
    # Find a plotted point on/after the fill day (positions held): value there
    # must equal cash + full fractional qty * avg cost, NOT the int-truncated 6.
    post = [p["v"] for p in data["points"]]
    reconstructed = cash + qty * avg
    truncated = cash + int(qty) * avg
    # The post-trade interior points equal the full-fractional value, and that
    # differs from the int-truncated value by ~0.78*avg ≈ ₹5.7k.
    assert any(v == pytest.approx(reconstructed, abs=1.0) for v in post)
    assert abs(reconstructed - truncated) > 1000.0
