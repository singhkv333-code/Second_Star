"""F&O P0 — instrument master refresh, derived expiry kinds, lot-size
reads (regression guard: lots come from the master, never constants),
dynamic universe selection incl. the MCX research-only block."""
from datetime import date, timedelta

import pytest

from backend.market.instrument_master import (
    chain_instruments,
    get_lot_size,
    is_research_only,
    list_expiries,
    list_option_underlyings,
    refresh_instrument_master,
    resolve_expiry,
    select_active_universe,
)
from backend.models import InstrumentMaster, OptionUniverse


@pytest.fixture()
def master(db):
    """Refreshed (mock-mode) instrument master."""
    counts = refresh_instrument_master(db)
    assert counts["source"] == "mock"
    assert counts["inserted"] > 0
    return db


def test_refresh_is_idempotent(master):
    first = master.query(InstrumentMaster).count()
    counts = refresh_instrument_master(master)
    assert counts["inserted"] == 0
    assert counts["updated"] == first
    assert master.query(InstrumentMaster).count() == first


def test_underlyings_discovered_dynamically_not_hardcoded(master):
    unders = {u["underlying"] for u in list_option_underlyings(master)}
    # The mock dump defines these — the code must DISCOVER them.
    assert {"NIFTY", "BANKNIFTY", "RELIANCE", "SENSEX", "CRUDEOIL"} <= unders
    segments = {u["segment"] for u in list_option_underlyings(master)}
    assert "MCX-OPT" in segments and "BFO-OPT" in segments


def test_expiry_kind_derived_from_date_spacing(master):
    expiries = list_expiries(master, "NIFTY")
    assert len(expiries) >= 3
    kinds = {e["kind"] for e in expiries}
    # NIFTY mock has weeklies + monthlies; the LAST expiry of a month
    # must classify monthly, the others weekly.
    assert kinds == {"weekly", "monthly"}
    # BANKNIFTY is monthly-only post Sep-2025 — every expiry monthly.
    bn = list_expiries(master, "BANKNIFTY")
    assert bn and all(e["kind"] == "monthly" for e in bn)


def test_lot_size_read_from_master(master):
    # Values come from the mock dump — the assertion is that the API
    # returns whatever the master holds (not a constant in code).
    assert get_lot_size(master, "NIFTY") == 65
    assert get_lot_size(master, "BANKNIFTY") == 30
    assert get_lot_size(master, "CRUDEOIL") == 100
    assert get_lot_size(master, "NO_SUCH_THING") is None


def test_no_hardcoded_lot_constants_in_fno_modules():
    """Regression guard from the plan: no NIFTY/BANKNIFTY lot-size
    integer constants may appear in the F&O modules — lots must flow
    from InstrumentMaster."""
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parents[1] / "backend"
    suspicious = re.compile(
        r"(NIFTY|BANKNIFTY)[\"']?\s*[:=]\s*(75|65|35|30)\b"
    )
    offenders = []
    for path in (root / "market").rglob("*.py"):
        text = path.read_text()
        if suspicious.search(text):
            offenders.append(str(path))
    assert not offenders, f"hardcoded lot sizes in: {offenders}"


def test_resolve_expiry_nearest_and_explicit(master):
    nearest = resolve_expiry(master, "NIFTY")
    assert nearest is not None and nearest >= date.today()
    explicit = resolve_expiry(master, "NIFTY", nearest.isoformat())
    assert explicit == nearest
    assert resolve_expiry(master, "NIFTY", "1999-01-01") is None
    assert resolve_expiry(master, "UNKNOWN") is None


def test_list_expiries_excludes_todays_already_closed_session(master, monkeypatch):
    """Reported 2026-07-14 (live): asking for a NIFTY option strategy
    after-hours ON NIFTY's own weekly expiry date resolved to that SAME
    dead, already-settled contract (t_years=0.0, spot=None, no solvable
    IV) instead of the next real expiry. `list_expiries` must stop
    counting "today" as tradable once NSE market hours (15:30 IST) have
    passed."""
    import datetime as _dt

    from backend.market import instrument_master as im

    today_expiries = [e["expiry"] for e in list_expiries(master, "NIFTY")]
    first_expiry = today_expiries[0]

    class _FixedNow:
        @staticmethod
        def now_ist():
            # 21:00 IST on the day of the nearest listed expiry — well
            # past market close.
            d = _dt.date.fromisoformat(first_expiry)
            return _dt.datetime(d.year, d.month, d.day, 21, 0)

    monkeypatch.setattr(
        "backend.utils.time_utils.now_ist", _FixedNow.now_ist,
    )
    after_hours = [e["expiry"] for e in list_expiries(master, "NIFTY")]
    assert first_expiry not in after_hours
    assert after_hours[0] > first_expiry

    class _BeforeClose:
        @staticmethod
        def now_ist():
            d = _dt.date.fromisoformat(first_expiry)
            return _dt.datetime(d.year, d.month, d.day, 10, 0)

    monkeypatch.setattr(
        "backend.utils.time_utils.now_ist", _BeforeClose.now_ist,
    )
    during_hours = [e["expiry"] for e in list_expiries(master, "NIFTY")]
    assert first_expiry in during_hours


def test_chain_instruments_strike_ordered_pairs(master):
    expiry = resolve_expiry(master, "NIFTY")
    rows = chain_instruments(master, "NIFTY", expiry)
    assert rows
    strikes = [float(r.strike) for r in rows]
    assert strikes == sorted(strikes)
    kinds = {r.instrument_type for r in rows}
    assert kinds == {"CE", "PE"}


def test_chain_instruments_drops_stale_mock_duplicate(master):
    """Regression: a stale, incompletely-purged synthetic-dump row (token
    in the reserved mock band, see instrument_master._MOCK_TOKEN_LOW/HIGH)
    must never coexist with a real row for the same (strike, type,
    expiry) — that's exactly how a live quote fetch keyed off the mock
    row's tradingsymbol silently resolved to a genuinely different, real
    contract, mixing another expiry's premium into this chain (2026-07-14
    50-prompt eval: inverted put pricing in a NIFTY chain)."""
    expiry = resolve_expiry(master, "NIFTY")
    rows = chain_instruments(master, "NIFTY", expiry)
    assert rows
    target = rows[0]  # a genuine mock-mode row (all rows are mock here)
    real_token = 90_000_001  # well outside the reserved mock band
    master.add(InstrumentMaster(
        instrument_token=real_token,
        first_seen=date.today(), last_seen=date.today(),
        refreshed_on=date.today(),
        tradingsymbol="DUPLICATE_REAL_ROW",
        name="NIFTY", underlying="NIFTY",
        exchange=target.exchange, segment=target.segment,
        instrument_type=target.instrument_type,
        strike=target.strike, expiry=target.expiry,
        expiry_kind=target.expiry_kind, lot_size=target.lot_size,
    ))
    master.commit()

    dupes = [
        r for r in master.query(InstrumentMaster).filter(
            InstrumentMaster.underlying == "NIFTY",
            InstrumentMaster.strike == target.strike,
            InstrumentMaster.instrument_type == target.instrument_type,
            InstrumentMaster.expiry == expiry,
        ).all()
    ]
    assert len(dupes) == 2  # the collision now genuinely exists in the DB

    deduped = chain_instruments(master, "NIFTY", expiry)
    matches = [
        r for r in deduped
        if float(r.strike) == float(target.strike)
        and r.instrument_type == target.instrument_type
    ]
    # Exactly one row survives per (strike, type) — the real one, not
    # the mock-band row that was already present.
    assert len(matches) == 1
    assert matches[0].instrument_token == real_token


def test_disappeared_contract_keeps_row_with_stale_last_seen(master):
    token = int(
        master.query(InstrumentMaster.instrument_token).first()[0]
    )
    stale = date.today() - timedelta(days=3)
    master.query(InstrumentMaster).filter(
        InstrumentMaster.instrument_token == token
    ).update({"last_seen": stale, "refreshed_on": stale})
    master.commit()
    refresh_instrument_master(master)
    row = master.get(InstrumentMaster, token)
    # Mock dump still contains it → bumped. The semantics under test:
    # refresh UPDATES rather than deletes; nothing ever drops rows.
    assert row is not None
    assert row.last_seen == date.today()


def test_universe_selection_mcx_tradeable(master):
    # Commodities (MCX) are now tradeable via register-not-execute — no
    # research-only marking; they go through the same liquidity gate.
    rows = select_active_universe(master)
    assert rows
    by_u = {r.underlying: r for r in rows}
    crude = by_u["CRUDEOIL"]
    assert crude.research_only is False
    assert crude.reason != "mcx_research_only"
    assert crude.reason in ("liquidity_ok", "below_liquidity_percentile")
    # Liquid index universe selected, evidence recorded.
    nifty = by_u["NIFTY"]
    assert nifty.selected is True
    assert nifty.avg_oi and nifty.avg_oi > 0
    assert nifty.liquidity_score is not None
    assert is_research_only(master, "CRUDEOIL") is False
    assert is_research_only(master, "NIFTY") is False


def test_universe_selection_idempotent_per_day(master):
    select_active_universe(master)
    select_active_universe(master)
    today_rows = (
        master.query(OptionUniverse)
        .filter(OptionUniverse.as_of == date.today())
        .all()
    )
    unders = [r.underlying for r in today_rows]
    assert len(unders) == len(set(unders))  # one row per (underlying, day)
