"""Unit tests for backend.paper.idea_valuation (P6 idea-grain FIFO NAV).

Offline by construction: PaperBroker fills tagged with `idea_id` are
created via an injected price_fn, then compute_idea_nav is invoked with
an injected price_fn — no network, no live quote.

Covers the four contract pins:
  1. Single BUY -> committed_capital = net debit; idea_nav marks up/down
     with the live price; unrealized math is FIFO-correct.
  2. Partial SELL spanning two lots realizes correct FIFO P&L and leaves
     the right residual committed_capital.
  3. price None / 0 -> MV fallback to cost basis, unrealized = 0.
  4. Idea slicing is independent of the account's avg-cost cache: two
     ideas on the same symbol in the same account each see their own
     FIFO ledger.
"""
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from backend.database import Base
from backend.models import (  # noqa: F401 — registers tables on Base.metadata
    ForwardIdea,
    PaperAccount,
    PaperFill,
    PaperLedgerEntry,
    PaperNavSnapshot,
    PaperOrder,
    PaperPosition,
    User,
)
from backend.paper.broker import PaperBroker
from backend.paper.idea_valuation import compute_idea_nav
from backend.paper.money import to_money


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


def _account(db, user_id):
    return (
        db.query(PaperAccount)
        .filter(PaperAccount.user_id == user_id)
        .first()
    )


def _new_idea(
    db, user_id, account_id, *, label, conversation_id=None, origin_kind="chat",
):
    """Create a ForwardIdea row directly — bypasses the resolver to keep
    these unit tests focused on valuation math. The resolver is covered
    by its own test module."""
    idea = ForwardIdea(
        user_id=user_id,
        account_id=account_id,
        origin_kind=origin_kind,
        conversation_id=conversation_id,
        label=label,
        status="paper",
    )
    db.add(idea)
    db.flush()
    return idea


def _buy(db, user_id, sym, qty, px, *, idea_id=None):
    """Open/add to a position via a real market BUY at price ``px``."""
    broker = PaperBroker(db, user_id, price_fn=lambda s: to_money(px))
    return broker.place_order(
        tradingsymbol=sym,
        transaction_type="BUY",
        quantity=qty,
        order_type="MARKET",
        idea_id=idea_id,
    )


def _sell(db, user_id, sym, qty, px, *, idea_id=None):
    broker = PaperBroker(db, user_id, price_fn=lambda s: to_money(px))
    return broker.place_order(
        tradingsymbol=sym,
        transaction_type="SELL",
        quantity=qty,
        order_type="MARKET",
        idea_id=idea_id,
    )


def _fills_for_idea(db, idea_id):
    return (
        db.query(PaperFill)
        .filter(PaperFill.idea_id == idea_id)
        .order_by(PaperFill.filled_at.asc(), PaperFill.id.asc())
        .all()
    )


# ── 1. single BUY ───────────────────────────────────────────────────────


def test_single_buy_committed_capital_equals_net_debit(session):
    """committed_capital reads the cost-INCLUSIVE basis from net_cashflow
    (NOT fill_price), so it equals the net debit the broker actually
    paid for the lot — buy charges included."""
    u = _user(session)
    acct = (
        # First fill auto-creates the account; grab it after seeding.
        _buy(session, u.id, "AAA", 10, 100) and _account(session, u.id)
    )
    # Now create the idea + a BUY tagged to that idea so it has a fill.
    idea = _new_idea(session, u.id, acct.id, label="AAA buy")
    _buy(session, u.id, "AAA", 10, 100, idea_id=idea.id)

    fills = _fills_for_idea(session, idea.id)
    assert len(fills) == 1
    f = fills[0]
    # BUY net_cashflow is negative (debit); basis = -net_cashflow.
    expected_committed = to_money(-f.net_cashflow)

    # No price -> MV falls back to committed, unrealized 0.
    out = compute_idea_nav(session, idea, price_fn=lambda s: None)
    assert out["committed_capital"] == expected_committed
    assert out["positions_mv"] == expected_committed
    assert out["idea_nav"] == to_money(expected_committed + expected_committed)
    assert out["realized_pnl"] == to_money(0)
    assert out["unrealized_pnl"] == to_money(0)
    assert all(
        isinstance(out[k], Decimal)
        for k in (
            "committed_capital", "positions_mv", "idea_nav",
            "realized_pnl", "unrealized_pnl",
        )
    )


def test_single_buy_marks_up_and_down_with_price(session):
    """idea_nav = committed_capital + positions_mv. With a live mark,
    positions_mv = qty*px and unrealized = qty*(px - weighted_avg_basis).
    """
    u = _user(session)
    _buy(session, u.id, "AAA", 10, 100)  # seed account
    acct = _account(session, u.id)
    idea = _new_idea(session, u.id, acct.id, label="AAA buy")
    _buy(session, u.id, "AAA", 10, 100, idea_id=idea.id)
    f = _fills_for_idea(session, idea.id)[0]
    committed = to_money(-f.net_cashflow)
    basis_per_share = committed / Decimal(10)

    # Mark up to 120.
    up = compute_idea_nav(session, idea, price_fn=lambda s: to_money(120))
    expected_mv_up = to_money(10 * to_money(120))
    expected_unreal_up = to_money(10 * (to_money(120) - basis_per_share))
    assert up["committed_capital"] == committed
    assert up["positions_mv"] == expected_mv_up
    assert up["idea_nav"] == to_money(committed + expected_mv_up)
    assert up["unrealized_pnl"] == expected_unreal_up
    assert up["realized_pnl"] == to_money(0)

    # Mark down to 80.
    down = compute_idea_nav(session, idea, price_fn=lambda s: to_money(80))
    expected_mv_down = to_money(10 * to_money(80))
    expected_unreal_down = to_money(10 * (to_money(80) - basis_per_share))
    assert down["positions_mv"] == expected_mv_down
    assert down["idea_nav"] == to_money(committed + expected_mv_down)
    assert down["unrealized_pnl"] == expected_unreal_down


# ── 2. partial SELL spanning lots ───────────────────────────────────────


def test_partial_sell_spans_lots_and_realizes_fifo_pnl(session):
    """Two BUY lots (10@100, 10@110) then SELL 15 @ 130: consume all of
    lot-1 + 5 of lot-2. Realized P&L = sell credit − (10·basis1 + 5·basis2).
    Residual committed_capital = 5·basis2 (lot-2 leftover)."""
    u = _user(session)
    _buy(session, u.id, "AAA", 10, 100)  # seed account
    acct = _account(session, u.id)
    idea = _new_idea(session, u.id, acct.id, label="AAA layered")

    # Two buys against the idea: 10 @ 100, then 10 @ 110.
    _buy(session, u.id, "AAA", 10, 100, idea_id=idea.id)
    _buy(session, u.id, "AAA", 10, 110, idea_id=idea.id)
    buys = _fills_for_idea(session, idea.id)
    assert len(buys) == 2
    # FIFO order matches filled_at; per-share basis from net_debit.
    basis1 = to_money(-buys[0].net_cashflow) / Decimal(10)
    basis2 = to_money(-buys[1].net_cashflow) / Decimal(10)

    # SELL 15 @ 130 -> consumes lot-1 (10) + half of lot-2 (5).
    _sell(session, u.id, "AAA", 15, 130, idea_id=idea.id)
    all_fills = _fills_for_idea(session, idea.id)
    sell = all_fills[-1]
    assert sell.transaction_type == "SELL"
    net_credit = to_money(sell.net_cashflow)

    expected_cost_consumed = to_money(
        Decimal(10) * basis1 + Decimal(5) * basis2
    )
    expected_realized = to_money(net_credit - expected_cost_consumed)

    # Residual = 5 shares of lot-2 @ basis2.
    expected_residual_committed = to_money(Decimal(5) * basis2)

    out = compute_idea_nav(session, idea, price_fn=lambda s: to_money(130))
    assert out["realized_pnl"] == expected_realized
    assert out["committed_capital"] == expected_residual_committed
    # 5 open shares marked at 130.
    expected_mv = to_money(5 * to_money(130))
    assert out["positions_mv"] == expected_mv
    assert out["idea_nav"] == to_money(
        expected_residual_committed + expected_mv
    )
    # Weighted avg basis on residual is basis2 (only one lot left).
    expected_unreal = to_money(5 * (to_money(130) - basis2))
    assert out["unrealized_pnl"] == expected_unreal


# ── 3. price None -> MV at cost & unrealized 0 ──────────────────────────


def test_price_none_falls_back_to_committed_capital(session):
    u = _user(session)
    _buy(session, u.id, "AAA", 10, 100)  # seed account
    acct = _account(session, u.id)
    idea = _new_idea(session, u.id, acct.id, label="AAA")
    _buy(session, u.id, "AAA", 7, 200, idea_id=idea.id)

    out_none = compute_idea_nav(session, idea, price_fn=lambda s: None)
    out_zero = compute_idea_nav(session, idea, price_fn=lambda s: to_money(0))

    # Both fallback paths: MV == committed, unrealized 0.
    for out in (out_none, out_zero):
        assert out["positions_mv"] == out["committed_capital"]
        assert out["unrealized_pnl"] == to_money(0)
        # idea_nav = committed + positions_mv (== 2 * committed at cost).
        assert out["idea_nav"] == to_money(
            out["committed_capital"] + out["positions_mv"]
        )
        assert out["realized_pnl"] == to_money(0)


def test_empty_idea_returns_zeros(session):
    """An idea with no fills has zero NAV — defensive aggregate."""
    u = _user(session)
    _buy(session, u.id, "AAA", 1, 100)  # seed account
    acct = _account(session, u.id)
    idea = _new_idea(session, u.id, acct.id, label="empty")
    out = compute_idea_nav(session, idea, price_fn=lambda s: to_money(123))
    assert out["committed_capital"] == to_money(0)
    assert out["positions_mv"] == to_money(0)
    assert out["idea_nav"] == to_money(0)
    assert out["realized_pnl"] == to_money(0)
    assert out["unrealized_pnl"] == to_money(0)


# ── 4. idea-grain slicing is independent of account avg-cost ───────────


def test_idea_slice_independent_of_account_avg_cost(session):
    """Same symbol, same account, TWO ideas. Idea A buys 10@100, Idea B
    buys 10@200. The PaperPosition cache shows a blended avg_cost (~150
    plus charges), but each idea's compute_idea_nav reads ONLY its own
    fills — so committed_capital splits cleanly per idea."""
    u = _user(session)
    _buy(session, u.id, "AAA", 1, 100)  # seed account
    acct = _account(session, u.id)
    idea_a = _new_idea(session, u.id, acct.id, label="A")
    idea_b = _new_idea(session, u.id, acct.id, label="B")

    _buy(session, u.id, "AAA", 10, 100, idea_id=idea_a.id)
    _buy(session, u.id, "AAA", 10, 200, idea_id=idea_b.id)

    fa = _fills_for_idea(session, idea_a.id)[0]
    fb = _fills_for_idea(session, idea_b.id)[0]
    committed_a = to_money(-fa.net_cashflow)
    committed_b = to_money(-fb.net_cashflow)

    # The account-grain PaperPosition has all 21 shares (1 seed + 10 + 10)
    # with a blended avg_cost — completely different from per-idea basis.
    pos = (
        session.query(PaperPosition)
        .filter(
            PaperPosition.account_id == acct.id,
            PaperPosition.symbol == "AAA",
        )
        .first()
    )
    assert pos is not None
    assert pos.quantity == 21
    # The blended account avg_cost is NOT equal to either idea's per-share
    # basis — proving the cache shouldn't be used for idea valuation.
    blended_avg = to_money(pos.avg_cost)
    basis_a = committed_a / Decimal(10)
    basis_b = committed_b / Decimal(10)
    assert blended_avg != basis_a
    assert blended_avg != basis_b

    # At mark 250, each idea's MV is its own qty*px, not derived from cache.
    out_a = compute_idea_nav(session, idea_a, price_fn=lambda s: to_money(250))
    out_b = compute_idea_nav(session, idea_b, price_fn=lambda s: to_money(250))

    assert out_a["committed_capital"] == committed_a
    assert out_b["committed_capital"] == committed_b
    assert committed_a != committed_b  # the slices are distinct

    expected_mv = to_money(10 * to_money(250))
    assert out_a["positions_mv"] == expected_mv
    assert out_b["positions_mv"] == expected_mv

    # Idea-grain unrealized uses ITS OWN basis, NOT the blended one.
    expected_unreal_a = to_money(10 * (to_money(250) - basis_a))
    expected_unreal_b = to_money(10 * (to_money(250) - basis_b))
    assert out_a["unrealized_pnl"] == expected_unreal_a
    assert out_b["unrealized_pnl"] == expected_unreal_b
    # And they DIFFER (the proof slicing works) — A bought lower so has
    # bigger unrealized at the same mark.
    assert out_a["unrealized_pnl"] > out_b["unrealized_pnl"]

    # idea_nav = committed + positions_mv per idea.
    assert out_a["idea_nav"] == to_money(committed_a + expected_mv)
    assert out_b["idea_nav"] == to_money(committed_b + expected_mv)


def test_idea_slice_independent_of_account_realized(session):
    """Idea A: 10 @ 100 -> SELL 10 @ 130 (realizes per its own FIFO).
    Meanwhile Idea B independently buys 5 @ 200 and never sells. The
    account-grain PaperPosition.realized_pnl reflects the WHOLE account's
    booking; Idea B's compute_idea_nav must show realized_pnl == 0."""
    u = _user(session)
    _buy(session, u.id, "AAA", 1, 100)  # seed account
    acct = _account(session, u.id)
    idea_a = _new_idea(session, u.id, acct.id, label="A")
    idea_b = _new_idea(session, u.id, acct.id, label="B")

    _buy(session, u.id, "AAA", 10, 100, idea_id=idea_a.id)
    _buy(session, u.id, "AAA", 5, 200, idea_id=idea_b.id)
    _sell(session, u.id, "AAA", 10, 130, idea_id=idea_a.id)

    # Account-grain realized booked by the SELL on the blended cache row.
    pos = (
        session.query(PaperPosition)
        .filter(
            PaperPosition.account_id == acct.id,
            PaperPosition.symbol == "AAA",
        )
        .first()
    )
    assert pos is not None
    account_realized = to_money(pos.realized_pnl)

    # Idea B has only a BUY -> realized_pnl strictly 0.
    out_b = compute_idea_nav(session, idea_b, price_fn=lambda s: to_money(130))
    assert out_b["realized_pnl"] == to_money(0)

    # Idea A's realized is its OWN FIFO realized (net_credit − 10*basis_a),
    # which need NOT match the account's blended realized number.
    sells_a = [
        f for f in _fills_for_idea(session, idea_a.id)
        if str(f.transaction_type).upper() == "SELL"
    ]
    assert len(sells_a) == 1
    buys_a = [
        f for f in _fills_for_idea(session, idea_a.id)
        if str(f.transaction_type).upper() == "BUY"
    ]
    basis_a = to_money(-buys_a[0].net_cashflow) / Decimal(10)
    expected_realized_a = to_money(
        to_money(sells_a[0].net_cashflow) - to_money(Decimal(10) * basis_a)
    )
    out_a = compute_idea_nav(session, idea_a, price_fn=lambda s: to_money(130))
    assert out_a["realized_pnl"] == expected_realized_a
    # Idea A is fully closed -> 0 committed, 0 MV, 0 unrealized.
    assert out_a["committed_capital"] == to_money(0)
    assert out_a["positions_mv"] == to_money(0)
    assert out_a["unrealized_pnl"] == to_money(0)
    # And the account's realized is BLENDED across both ideas' positions
    # — it is NOT the same as Idea A's idea-grain realized (the account
    # uses one avg_cost across both A's lots and B's lots).
    assert account_realized != expected_realized_a
