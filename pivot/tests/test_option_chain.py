"""F&O P0 — option-chain service: slicing, Greeks decoration, caching,
expected move, MCX research flag, and the options cost model."""
from datetime import date

import pytest

from backend.market.instrument_master import refresh_instrument_master, resolve_expiry
from backend.market.option_chain import get_chain


@pytest.fixture()
def master(db):
    refresh_instrument_master(db)
    return db


@pytest.fixture(autouse=True)
def _fresh_chain_cache():
    """The chain cache is keyed per (underlying, expiry, width) with a 5s
    TTL — flush between tests so each test sees its own fetch. Handles
    BOTH MockRedis and a real local Redis: a dev server running against
    the same Redis (redis://localhost/0) shares the optchain:* keyspace
    and was polluting test chains (order-dependent flakes)."""
    from backend.cache import redis_client

    if hasattr(redis_client, "_store"):  # MockRedis
        redis_client._store.clear()
        redis_client._expires_at.clear()
    elif hasattr(redis_client, "scan_iter"):  # real Redis
        for key in list(redis_client.scan_iter("optchain:*")):
            redis_client.delete(key)
    yield


def test_chain_slice_is_atm_centered(master):
    chain = get_chain(master, "NIFTY", width=5)
    assert chain is not None
    assert chain["source"] == "mock"
    rows = chain["rows"]
    assert len(rows) == 11  # ATM ± 5
    strikes = [r["strike"] for r in rows]
    assert strikes == sorted(strikes)
    assert chain["atm_strike"] in strikes
    # ATM strike is the nearest to the forward.
    assert abs(chain["atm_strike"] - chain["forward"]) <= 25.0 + 1e-9
    assert chain["lot_size"] == 65


def test_chain_rows_carry_greeks_and_status(master):
    chain = get_chain(master, "NIFTY", width=5)
    atm_row = next(r for r in chain["rows"] if r["strike"] == chain["atm_strike"])
    for side in ("ce", "pe"):
        q = atm_row[side]
        assert q["iv_status"] == "ok"
        assert q["iv"] and 0.05 < q["iv"] < 1.0
        assert q["mid"] > 0
        for greek in ("delta", "gamma", "theta", "vega"):
            assert q[greek] is not None
    # Call delta positive & put delta negative at ATM, theta negative.
    assert 0.3 < atm_row["ce"]["delta"] < 0.7
    assert -0.7 < atm_row["pe"]["delta"] < -0.3
    assert atm_row["ce"]["theta"] < 0


def test_expected_move_present_and_sane(master):
    chain = get_chain(master, "NIFTY", width=5)
    em = chain["expected_move"]
    assert em is not None
    assert em["low"] < chain["forward"] < em["high"]
    assert 0 < em["pct"] < 10


def test_unknown_underlying_returns_none_not_fabrication(master):
    assert get_chain(master, "NOTREAL") is None
    expiry = resolve_expiry(master, "NIFTY")
    assert expiry is not None
    assert get_chain(master, "NIFTY", "1999-01-01") is None


def test_chain_cache_hit_skips_refetch(master, monkeypatch):
    chain1 = get_chain(master, "BANKNIFTY", width=3)
    assert chain1 is not None

    import backend.market.option_chain as oc

    def _boom(*a, **k):  # a second fetch would have to synthesize quotes
        raise AssertionError("cache miss — _mock_quotes called twice")

    monkeypatch.setattr(oc, "_mock_quotes", _boom)
    chain2 = get_chain(master, "BANKNIFTY", width=3)
    assert chain2 == chain1


def test_mcx_chain_tradeable(master):
    # Commodities (MCX) are tradeable via register-not-execute — the chain is
    # a real MCX segment but no longer flagged research-only.
    chain = get_chain(master, "CRUDEOIL", width=3)
    assert chain is not None
    assert chain["segment"] == "MCX-OPT"
    assert chain["research_only"] is False
    # MCX T-clock runs to the 23:30 close — strictly longer than an NSE
    # expiry on the same date would be; just assert it's positive.
    assert chain["t_years"] > 0


def test_quote_batching_splits_at_200():
    from backend.market.option_chain import _kite_quotes

    calls = []

    class _FakeKite:
        def quote(self, batch):
            calls.append(len(batch))
            return {k: {"last_price": 1.0} for k in batch}

    out = _kite_quotes(_FakeKite(), [f"NFO:SYM{i}" for i in range(501)])
    assert len(out) == 501
    assert calls == [200, 200, 101]


# ── Options cost model ───────────────────────────────────────────────


def test_option_costs_sell_carries_stt_buy_carries_stamp():
    from backend.services.trading_costs import (
        option_buy_cost,
        option_sell_cost,
    )

    premium, qty = 120.0, 65  # one NIFTY lot at ₹120
    net_debit, buy_charges = option_buy_cost(premium, qty)
    net_credit, sell_charges = option_sell_cost(premium, qty)
    value = premium * qty
    assert net_debit == pytest.approx(value + buy_charges)
    assert net_credit == pytest.approx(value - sell_charges)
    # STT (0.1% of premium) only on the sell side → sell leg costs more.
    assert sell_charges > buy_charges
    assert sell_charges - buy_charges == pytest.approx(
        value * 0.001 - value * 0.00003, rel=1e-6,
    )


def test_option_leg_bps_asymmetric_and_positive():
    from backend.services.trading_costs import option_leg_bps

    buy, sell = option_leg_bps("buy"), option_leg_bps("sell")
    assert 0 < buy < sell < 0.01  # both sub-1% of premium at ref notional


def test_equity_cost_model_unchanged_by_options_branch():
    from backend.services.trading_costs import buy_cost, round_trip_bps

    net, charges = buy_cost(100.0, 100)
    assert net == pytest.approx(10_000.0 + charges)
    assert 30.0 < round_trip_bps() < 50.0  # the documented ~35-40 bps
