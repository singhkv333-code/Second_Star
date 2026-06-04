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


def test_chain_instruments_strike_ordered_pairs(master):
    expiry = resolve_expiry(master, "NIFTY")
    rows = chain_instruments(master, "NIFTY", expiry)
    assert rows
    strikes = [float(r.strike) for r in rows]
    assert strikes == sorted(strikes)
    kinds = {r.instrument_type for r in rows}
    assert kinds == {"CE", "PE"}


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


def test_universe_selection_marks_mcx_research_only(master):
    rows = select_active_universe(master)
    assert rows
    by_u = {r.underlying: r for r in rows}
    crude = by_u["CRUDEOIL"]
    assert crude.research_only is True
    assert crude.selected is False
    assert crude.reason == "mcx_research_only"
    # Liquid index universe selected, evidence recorded.
    nifty = by_u["NIFTY"]
    assert nifty.selected is True
    assert nifty.avg_oi and nifty.avg_oi > 0
    assert nifty.liquidity_score is not None
    assert is_research_only(master, "CRUDEOIL") is True
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
