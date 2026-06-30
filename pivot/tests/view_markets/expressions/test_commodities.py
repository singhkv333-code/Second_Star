"""Unit tests for the Phase-3 MCX commodity foundation (``expressions.commodities``).

Commodities became tradeable via register-not-execute (2026-06-29); this module
is the universe + classification + leverage-note convention the commodity
archetypes / builders code to. Pure data + light helpers (no DB except
``lot_size``), so these tests are self-contained — they pin the contract the
builder agents depend on, especially the HONEST data gate
(``price_history_available``) that decides whether a commodity leg can backtest.
"""
from __future__ import annotations

import pytest

from backend.view_markets.expressions import commodities as cm


# ── classification + normalisation ───────────────────────────────────────────
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("crude", "CRUDEOIL"),
        ("Crude Oil", "CRUDEOIL"),
        ("WTI", "CRUDEOIL"),
        ("brent", "CRUDEOIL"),
        ("buy natural gas", "NATURALGAS"),
        ("gold", "GOLD"),
        ("SILVER", "SILVER"),
        ("copper", "COPPER"),
        ("aluminum", "ALUMINIUM"),
        ("RELIANCE", None),
        ("", None),
    ],
)
def test_normalize_commodity(text: str, expected) -> None:
    assert cm.normalize_commodity(text) == expected


def test_normalize_keeps_direct_symbol_distinct_from_etf_proxy() -> None:
    # "gold" resolves to the DIRECT MCX future GOLD, never the GOLDBEES ETF.
    assert cm.normalize_commodity("gold") == "GOLD"
    assert cm.normalize_commodity("GOLDBEES") is None  # ETF proxy is not a commodity


def test_is_commodity_and_group() -> None:
    assert cm.is_commodity("crude") is True
    assert cm.is_commodity("INFY") is False
    assert cm.commodity_group("GOLD") == "bullion"
    assert cm.commodity_group("CRUDEOIL") == "energy"
    assert cm.commodity_group("COPPER") == "base_metal"
    assert cm.commodity_group("INFY") is None


# ── options availability + mini routing ──────────────────────────────────────
def test_is_fno_full_size_vs_mini() -> None:
    assert cm.is_fno("GOLD") is True
    assert cm.is_fno("GOLDM") is False        # minis are futures-only
    assert cm.is_fno("UNOBTANIUM") is False


def test_options_underlying_routes_mini_to_full_size_sibling() -> None:
    assert cm.options_underlying("GOLD") == "GOLD"
    assert cm.options_underlying("GOLDM") == "GOLD"
    assert cm.options_underlying("CRUDEOILM") == "CRUDEOIL"
    assert cm.options_underlying("INFY") is None


# ── ETF proxy (the backtestable bullion route) ───────────────────────────────
def test_etf_proxy_only_for_bullion() -> None:
    assert cm.etf_proxy("GOLD") == "GOLDBEES"
    assert cm.etf_proxy("SILVER") == "SILVERBEES"
    assert cm.etf_proxy("GOLDM") == "GOLDBEES"
    assert cm.etf_proxy("CRUDEOIL") is None    # no liquid retail crude ETF — don't pretend
    assert cm.etf_proxy("COPPER") is None


# ── the HONEST data gate (direct MCX has no aligned OHLCV) ────────────────────
def test_price_history_gate_direct_mcx_vs_proxy() -> None:
    # Direct MCX commodity futures have NO history in the pairs/basket data layer.
    assert cm.price_history_available("GOLD") is False
    assert cm.price_history_available("CRUDEOIL") is False
    # The listed ETF proxies (and plain NSE equities) DO.
    assert cm.price_history_available("GOLDBEES") is True
    assert cm.price_history_available("SILVERBEES") is True
    assert cm.price_history_available("INFY") is True


# ── leverage-note convention ─────────────────────────────────────────────────
def test_leverage_note_is_non_blank_and_says_leveraged() -> None:
    note = cm.leverage_note("CRUDEOIL")
    assert note == cm.LEVERAGE_NOTE
    low = note.lower()
    assert "leveraged" in low
    assert "register-not-execute" in low
    assert "never auto-sized" in low


def test_ratio_and_proxy_legs_are_distinct_routes() -> None:
    assert cm.GOLD_SILVER_RATIO_LEGS == ("GOLD", "SILVER")
    assert cm.GOLD_SILVER_ETF_PROXY_LEGS == ("GOLDBEES", "SILVERBEES")
    # the two routes never share a leg (direct future vs listed ETF).
    assert set(cm.GOLD_SILVER_RATIO_LEGS).isdisjoint(cm.GOLD_SILVER_ETF_PROXY_LEGS)


# ── lot size delegates to the master, degrades honestly ──────────────────────
def test_lot_size_returns_none_without_a_master(view_db) -> None:
    # No instrument_master rows in the in-memory test DB → None (never fabricated).
    assert cm.lot_size(view_db, "GOLD") is None
