"""Kalshi REST source — pure parsers + asset-id synthesis (no network)."""
from __future__ import annotations

from backend.news_events.sources import kalshi


# ── cents → 0..1 normalization ───────────────────────────────────────


def test_yes_price_mid_of_bid_ask() -> None:
    assert kalshi._parse_yes_price({"yes_bid": 60, "yes_ask": 64}) == 0.62


def test_yes_price_falls_back_to_ask_then_last() -> None:
    assert kalshi._parse_yes_price({"yes_ask": 55}) == 0.55
    assert kalshi._parse_yes_price({"last_price": 40}) == 0.40


def test_yes_price_dollars_fallback() -> None:
    assert kalshi._parse_yes_price({"yes_ask_dollars": "0.55"}) == 0.55


def test_yes_price_none_when_no_fields() -> None:
    assert kalshi._parse_yes_price({}) is None


def test_yes_price_clamped() -> None:
    assert kalshi._parse_yes_price({"yes_bid": 120, "yes_ask": 130}) == 1.0


# ── snapshot ─────────────────────────────────────────────────────────


def test_snapshot_open_market() -> None:
    snap = kalshi._snapshot_from_payload({
        "ticker": "KXFED-26JAN-H", "title": "Fed cuts in Jan?",
        "yes_bid": 60, "yes_ask": 64, "status": "active",
        "event_ticker": "KXFED-26JAN",
    })
    assert snap is not None
    assert snap.market_id == "KXFED-26JAN-H"
    assert snap.question == "Fed cuts in Jan?"
    assert snap.yes_price == 0.62
    assert snap.closed is False
    assert snap.settled is False


def test_snapshot_requires_ticker() -> None:
    assert kalshi._snapshot_from_payload({"title": "no ticker", "yes_ask": 50}) is None


def test_snapshot_settled_yes_derives_price() -> None:
    snap = kalshi._snapshot_from_payload({
        "ticker": "T1", "status": "settled", "result": "yes",
    })
    assert snap is not None
    assert snap.yes_price == 1.0
    assert snap.settled is True
    assert snap.closed is True


def test_snapshot_settled_no() -> None:
    snap = kalshi._snapshot_from_payload({
        "ticker": "T2", "status": "finalized", "result": "no",
    })
    assert snap is not None
    assert snap.yes_price == 0.0
    assert snap.settled is True


# ── asset id synthesis ───────────────────────────────────────────────


def test_asset_id_round_trip() -> None:
    aid = kalshi.kalshi_asset_id("KXFED-26JAN-H", "yes")
    assert aid == "KXFED-26JAN-H:YES"
    assert kalshi.split_kalshi_asset_id(aid) == ("KXFED-26JAN-H", "YES")


def test_split_defaults_side_yes() -> None:
    assert kalshi.split_kalshi_asset_id("TICKER") == ("TICKER", "YES")


# ── resolution payload ───────────────────────────────────────────────


def test_resolution_payload_settled_yes() -> None:
    snap = kalshi._snapshot_from_payload({
        "ticker": "T1", "status": "settled", "result": "yes",
    })
    payload = kalshi.resolution_payload(snap)
    assert payload == {"winner": "YES", "market": "T1"}


def test_resolution_payload_open_is_none() -> None:
    snap = kalshi._snapshot_from_payload({
        "ticker": "T1", "status": "active", "yes_ask": 50,
    })
    assert kalshi.resolution_payload(snap) is None
