"""F&O P2 — paper execution of multi-leg option strategies: spread-aware
fills, signed positions, premium cashflows, margin reserve, idempotency,
portfolio Greeks, SPAN margin, option marks, snapshots."""
from datetime import date

import pytest

from backend.market.instrument_master import refresh_instrument_master
from backend.models import (
    OptionStrategy,
    PaperFill,
    PaperGreeksSnapshot,
    PaperLedgerEntry,
    PaperOrder,
    PaperPosition,
    User,
)
from backend.paper.accounts import get_or_create_account
from backend.paper.money import to_money
from backend.paper.options_routing import submit_option_strategy
from backend.services.option_strategies import resolve_strategy
from backend.services.option_strategy_service import persist_option_strategy


@pytest.fixture(autouse=True)
def _master_and_cache(db):
    from backend.cache import redis_client

    if hasattr(redis_client, "_store"):
        redis_client._store.clear()
        redis_client._expires_at.clear()
    elif hasattr(redis_client, "scan_iter"):
        for key in list(redis_client.scan_iter("optchain:*")):
            redis_client.delete(key)
    refresh_instrument_master(db)
    yield


@pytest.fixture()
def user(db):
    u = User(email="p2@test.com", hashed_password="h")
    db.add(u)
    db.flush()
    return u


def _registered(db, user, template, underlying="NIFTY", qty_lots=1):
    payload = resolve_strategy(db, underlying, template, qty_lots=qty_lots)
    return persist_option_strategy(
        db, user_id=user.id, payload=payload, book="paper",
        qty_lots=qty_lots, source="test",
    )


def test_long_straddle_fills_both_legs_with_spread(db, user):
    strategy = _registered(db, user, "long_straddle")
    account = get_or_create_account(db, user.id)
    cash_before = to_money(account.cash_available)

    result = submit_option_strategy(db, user.id, strategy)
    assert result["success"], result["error"]
    assert len(result["fills"]) == 2
    assert strategy.status == "active"

    fills = db.query(PaperFill).filter(PaperFill.user_id == user.id).all()
    assert len(fills) == 2
    for f in fills:
        assert f.transaction_type == "BUY"
        assert f.iv_at_fill and 0.05 < f.iv_at_fill < 1.0
        assert float(f.charges) > 0
        # Spread-aware: a BUY fills ABOVE mid (crossing the book).
        leg = next(l for l in strategy.legs if l.tradingsymbol == f.symbol)
        assert f.fill_price >= leg.entry_mid - 0.01

    # Two long option positions, cash debited by both net debits.
    positions = (
        db.query(PaperPosition)
        .filter(PaperPosition.user_id == user.id, PaperPosition.is_option == True)  # noqa: E712
        .all()
    )
    assert len(positions) == 2
    assert all(p.quantity == 65 for p in positions)
    assert all(p.segment == "NFO-OPT" for p in positions)
    total_debit = sum(-to_money(f.net_cashflow) for f in fills)
    assert to_money(account.cash_available) == cash_before - total_debit


def test_short_strangle_short_positions_and_margin_reserve(db, user):
    strategy = _registered(db, user, "short_strangle")
    account = get_or_create_account(db, user.id)
    margin = to_money(strategy.margin_estimate)
    assert margin > 0

    result = submit_option_strategy(db, user.id, strategy)
    assert result["success"], result["error"]

    positions = (
        db.query(PaperPosition)
        .filter(PaperPosition.user_id == user.id, PaperPosition.is_option == True)  # noqa: E712
        .all()
    )
    assert len(positions) == 2
    assert all(p.quantity == -65 for p in positions)   # SIGNED shorts

    # Margin reserved + premium credited.
    assert to_money(account.cash_reserved) == margin
    reserve_rows = (
        db.query(PaperLedgerEntry)
        .filter(PaperLedgerEntry.kind == "reserve")
        .all()
    )
    assert any(f"optstrat:{strategy.id}" in (r.note or "") for r in reserve_rows)
    credits = (
        db.query(PaperFill)
        .filter(PaperFill.user_id == user.id)
        .all()
    )
    assert all(float(f.net_cashflow) > 0 for f in credits)


def test_submit_is_idempotent_per_leg(db, user):
    strategy = _registered(db, user, "iron_condor")
    first = submit_option_strategy(db, user.id, strategy)
    assert first["success"]
    fills_before = db.query(PaperFill).filter(PaperFill.user_id == user.id).count()
    orders_before = db.query(PaperOrder).filter(PaperOrder.user_id == user.id).count()

    second = submit_option_strategy(db, user.id, strategy)
    assert second["success"]
    assert db.query(PaperFill).filter(PaperFill.user_id == user.id).count() == fills_before
    assert db.query(PaperOrder).filter(PaperOrder.user_id == user.id).count() == orders_before


def test_mcx_strategy_refuses_execution(db, user):
    from backend.paper.options_routing import OptionFillError

    payload = resolve_strategy(db, "CRUDEOIL", "long_straddle")
    strategy = persist_option_strategy(
        db, user_id=user.id, payload=payload, book="paper",
        qty_lots=1, source="test",
    )
    with pytest.raises(OptionFillError, match="research-only"):
        submit_option_strategy(db, user.id, strategy)


def test_insufficient_cash_blocks_margin_reserve(db, user):
    strategy = _registered(db, user, "short_straddle", qty_lots=50)
    account = get_or_create_account(db, user.id)
    # Default paper seed can't margin 50 lots of short straddle.
    result = submit_option_strategy(db, user.id, strategy)
    assert result["success"] is False
    assert "margin" in result["error"]
    assert strategy.status == "registered"   # nothing filled
    assert to_money(account.cash_reserved) == to_money(0)


# ── Portfolio Greeks ─────────────────────────────────────────────────


def test_portfolio_greeks_live_marks(db, user):
    strategy = _registered(db, user, "short_strangle")
    submit_option_strategy(db, user.id, strategy)
    account = get_or_create_account(db, user.id)

    from backend.services.portfolio_greeks import compute_portfolio_greeks

    data = compute_portfolio_greeks(db, account.id)
    assert data["position_count"] == 2
    assert not data["unmarked"]
    # Short strangle: positive theta, negative vega, near-zero delta.
    assert data["net"]["theta"] > 0
    assert data["net"]["vega"] < 0
    assert abs(data["net"]["delta"]) < 65  # within one lot of flat
    assert "NIFTY" in data["by_underlying"]
    assert data["by_underlying"]["NIFTY"]["positions"] == 2
    assert data["by_expiry"]


def test_greeks_snapshot_idempotent(db, user):
    strategy = _registered(db, user, "long_straddle")
    submit_option_strategy(db, user.id, strategy)

    from backend.services.portfolio_greeks import snapshot_portfolio_greeks

    assert snapshot_portfolio_greeks(db) == 1
    assert snapshot_portfolio_greeks(db) == 1   # update, not duplicate
    rows = db.query(PaperGreeksSnapshot).all()
    assert len(rows) == 1
    snap = rows[0]
    assert snap.as_of == date.today()
    assert snap.position_count == 2
    assert snap.net_vega > 0          # long straddle = long vega
    assert snap.breakdown_json and "NIFTY" in snap.breakdown_json


def test_option_mark_resolves_via_chain(db, user):
    strategy = _registered(db, user, "long_call")
    submit_option_strategy(db, user.id, strategy)
    leg = strategy.legs[0]

    from backend.paper import marks as marks_mod

    # get_option_mark opens its own SessionLocal (separate engine) which
    # can't see this test's uncommitted instrument master — patch it to
    # the test session for the lookup.
    import backend.database as dbmod

    orig = dbmod.SessionLocal

    class _Sess:
        def __call__(self):
            return _Wrapper()

    class _Wrapper:
        def query(self, *a, **k):
            return db.query(*a, **k)

        def close(self):
            pass

    dbmod_sessions = dbmod.SessionLocal
    try:
        dbmod.SessionLocal = lambda: _Wrapper()  # type: ignore[assignment]
        mark = marks_mod.get_option_mark(leg.tradingsymbol)
    finally:
        dbmod.SessionLocal = dbmod_sessions
    assert mark is not None and float(mark) > 0
    assert marks_mod._looks_like_option(leg.tradingsymbol)
    assert not marks_mod._looks_like_option("RELIANCE")
    assert not marks_mod._looks_like_option("PE")  # too short


# ── SPAN margin ──────────────────────────────────────────────────────


def test_span_margin_shapes(db):
    ic = resolve_strategy(db, "NIFTY", "iron_condor")
    ss = resolve_strategy(db, "NIFTY", "short_strangle")
    lc = resolve_strategy(db, "NIFTY", "long_call")
    # Defined-risk margin never exceeds max loss.
    assert ic["computed"]["margin_estimate"] <= ic["computed"]["max_loss"] + 0.01
    # Naked strangle margin must dwarf the condor's.
    assert ss["computed"]["margin_estimate"] > ic["computed"]["margin_estimate"]
    # Long premium: margin == debit.
    assert lc["computed"]["margin_estimate"] == pytest.approx(
        -lc["computed"]["net_premium"], abs=0.02,
    )
    assert "SPAN" in ss["computed"]["margin_note"]


def test_register_endpoint_executes_paper_book(client, auth_headers, db, monkeypatch):
    monkeypatch.setenv("PAPER_TRADING_ENABLED", "true")
    from backend.config import settings as _settings

    monkeypatch.setattr(_settings, "paper_trading_enabled", True, raising=False)

    payload = resolve_strategy(db, "NIFTY", "bull_call_spread")
    body = {
        "underlying": "NIFTY",
        "expiry": payload["locked"]["expiry"],
        "template": "bull_call_spread",
        "book": "paper",
        "qty_lots": 1,
        "legs": [
            {"option_type": l["option_type"], "side": l["side"],
             "strike": l["strike"]}
            for l in payload["editable"]["legs"]
        ],
        "acknowledge_disclosure": True,
    }
    out = client.post("/option-strategies", json=body, headers=auth_headers).json()
    assert out["success"], out
    assert out["execution"] is not None
    assert out["execution"]["success"], out["execution"]
    assert len(out["execution"]["fills"]) == 2
    assert out["strategy"]["status"] == "active"
