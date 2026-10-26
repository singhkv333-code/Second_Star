"""Plays: the faults the 2026-10-26 review found, each held to its fix.

Found by a live run of vague chat prompts and by checks that need no model
(a generator of plays in the tool's own shape, a random-sequence lifecycle
machine, a doc-against-validator contract). Each test names what went wrong
and what must hold instead."""
from __future__ import annotations

from datetime import datetime

import pytest

import daybook
import dataserver as server
import effects
import paper
import plays
from test_plays import book, uid  # noqa: F401  (the throwaway account; the day book bound to it)

RIL = {"title": "t", "symbol": "RELIANCE", "interval": "1d"}
NOW = {"lifespan": "now", "authority": "act"}


def _held(uid) -> dict:
    return {h["symbol"]: float(h["quantity"]) for h in paper.api_holdings(uid)[1] if float(h["quantity"])}


def _compile(uid, heard=None, **raw):
    return plays.compile_play(uid, {**RIL, **raw}, chart_symbol="RELIANCE", chart_interval="1d", heard=heard)


def _resting(uid) -> list[tuple]:
    return server._users.execute("SELECT symbol, side, order_type, quantity, trigger_price, limit_price "
                                 "FROM paper_orders WHERE user_id=? AND status='resting'", (uid,)).fetchall()


def _at(monkeypatch, hh, mm, day=22):
    monkeypatch.setattr(daybook, "_now_ist", lambda: datetime(2026, 10, day, hh, mm, tzinfo=daybook.IST))


# ── what the model sends ────────────────────────────────────────────────────

def test_numbers_sent_as_text_are_read_and_words_are_refused(uid) -> None:
    got = _compile(uid, do={"kind": "buy", "symbol": "RELIANCE", "size": {"inr": "₹15,000"}}, contract=NOW)
    assert got["play"]["spec"]["do"]["size"] == {"inr": 15000.0}
    assert _compile(uid, do={"kind": "buy", "symbol": "RELIANCE", "size": "10k"},
                    contract=NOW)["play"]["spec"]["do"]["size"] == {"inr": 10000.0}
    before = server._users.execute("SELECT COUNT(*) FROM plays").fetchone()[0]
    for bad in ({"do": {"kind": "sell", "symbol": "RELIANCE", "size": {"pct": "half"}}},
                {"do": {"kind": "notify"}, "when": {"all": [{"left": "close", "op": "falls_pct", "right": 3}]},
                 "contract": {"expires_in_days": "a week"}},
                {"do": {"kind": "buy", "symbol": "RELIANCE"}, "mandate": {"capital_inr": "lots"}},
                {"do": {"kind": "buy", "symbol": "RELIANCE", "size": {"qty": -3}}}):
        with pytest.raises(plays.Bad):
            _compile(uid, **bad)
    # a refusal leaves nothing behind: no play, no proposal to confirm twice
    assert server._users.execute("SELECT COUNT(*) FROM plays").fetchone()[0] == before


def test_a_band_needs_both_edges(uid) -> None:
    with pytest.raises(plays.Bad, match="right2"):
        _compile(uid, do={"kind": "notify"}, when={"all": [{"left": "close", "op": "outside", "right": 1}]})


def test_a_buy_of_all_is_refused_at_compile(uid) -> None:
    with pytest.raises(plays.Bad, match="size.all is for a sell"):
        _compile(uid, do={"kind": "buy", "symbol": "RELIANCE", "size": {"all": True}}, contract=NOW)


def test_an_any_with_a_branch_that_always_holds_acts_now() -> None:
    assert plays._norm_when({"any": [{"left": "100", "op": "above", "right": 1},
                                     {"left": "close", "op": "above", "right": 5000}]}) is None
    kept = plays._norm_when({"any": [{"left": "1", "op": "above", "right": 100},
                                     {"left": "close", "op": "above", "right": 5000}]})
    assert kept == {"any": [{"left": "close", "op": "above", "right": 5000}]}


def test_a_size_the_trader_never_named_is_the_default(uid) -> None:
    got = _compile(uid, heard="buy reliance", do={"kind": "buy", "symbol": "RELIANCE", "size": {"qty": 1}},
                   contract={"lifespan": "now", "authority": "propose"})
    assert got["play"]["spec"]["do"]["size"] == {"inr": plays.DEFAULT_SIZE_INR}
    assert any("sized at ₹10,000" in a for a in got["assumed"])
    kept = _compile(uid, heard="buy 5 reliance", do={"kind": "buy", "symbol": "RELIANCE", "size": {"qty": 5}},
                    contract={"lifespan": "now", "authority": "propose"})
    assert kept["play"]["spec"]["do"]["size"] == {"qty": 5.0}


# ── limits ──────────────────────────────────────────────────────────────────

def test_a_share_count_buy_is_not_refused_by_a_limit_nobody_set(uid) -> None:
    got = _compile(uid, do={"kind": "buy", "symbol": "RELIANCE", "size": {"qty": 100}}, contract=NOW)
    assert got["play"]["_now_event"]["kind"] == "filled", got["play"]["_now_event"]["body"].get("reason")
    assert _held(uid)["RELIANCE"] == 100
    assert got["card"]["contract"]["limit"] == "100 per order"
    # a limit the trader did set still holds
    capped = _compile(uid, do={"kind": "buy", "symbol": "RELIANCE", "size": {"qty": 100}}, contract=NOW,
                      mandate={"capital_inr": 50000})
    assert capped["play"]["_now_event"]["kind"] == "rejected"


def test_a_safety_check_that_cannot_run_places_nothing(uid, monkeypatch) -> None:
    def boom(*a, **k):
        raise RuntimeError("no book")
    monkeypatch.setattr(effects, "laws", boom)
    assert effects.vet({"id": 1, "user_id": uid}, {"symbol": "X", "side": "BUY", "qty": 1}).startswith(
        "a safety check could not run")


# ── rebalances ──────────────────────────────────────────────────────────────

def test_every_target_at_zero_sells_and_never_buys(uid) -> None:
    for sym, q in (("RELIANCE", 10), ("TCS", 5), ("INFY", 6)):
        paper.place_order(uid, sym, "BUY", q, source="eval")
    got = _compile(uid, do={"kind": "rebalance", "of": "portfolio",
                            "targets": [{"symbol": "RELIANCE", "weight": 0}, {"symbol": "TCS", "weight": 0}]},
                   contract=NOW)
    legs = got["play"]["_now_event"]["body"]["ticket"]["legs"]
    assert legs and all(lg["side"] == "SELL" for lg in legs)
    assert _held(uid) == {"INFY": 6.0}


# ── stops and trails on what is held ────────────────────────────────────────

def test_a_stop_on_a_holding_rests_and_sells_nothing_now(uid) -> None:
    paper.place_order(uid, "RELIANCE", "BUY", 10, source="eval")
    got = _compile(uid, do={"kind": "sell", "symbol": "RELIANCE"}, manage={"stop_pct": 5}, contract=NOW)
    ev = got["play"]["_now_event"]
    assert ev["kind"] == "filled" and ev["body"]["resting"]
    assert _held(uid) == {"RELIANCE": 10.0}
    (sym, side, otype, qty, trig, _), = _resting(uid)
    px = float(paper.mark_price("RELIANCE"))
    assert (sym, side, otype, qty) == ("RELIANCE", "SELL", "SL-M", 10.0)
    assert trig == pytest.approx(px * 0.95, abs=0.01)
    assert plays.transcribe(ev, got["play"]["spec"])["line"][0]["t"].startswith("Stop set: sell 10 RELIANCE")
    paper.on_price("RELIANCE", trig - 1)
    assert "RELIANCE" not in _held(uid)


def test_a_trailing_stop_on_a_holding_moves_up_and_never_down(uid) -> None:
    paper.place_order(uid, "RELIANCE", "BUY", 10, source="eval")
    got = _compile(uid, do={"kind": "sell", "symbol": "RELIANCE"}, manage={"trail_pct": 5}, contract=NOW)
    pid = got["play"]["id"]
    play = plays._load(pid)
    assert play["state"] == "armed" and play["why"] == plays.EXITS_ONLY
    px = float(paper.mark_price("RELIANCE"))
    plays._ratchet(play, {"t": [0], "close": [px * 1.10]}, 0)
    trig = _resting(uid)[0][4]
    assert trig == pytest.approx(px * 1.10 * 0.95, abs=0.02)
    plays._ratchet(plays._load(pid), {"t": [0], "close": [px * 1.02]}, 0)
    assert _resting(uid)[0][4] == trig
    paper.on_price("RELIANCE", trig - 1)
    ev = plays._ratchet(plays._load(pid), {"t": [0], "close": [trig - 1]}, 0)
    assert ev["kind"] == "exited" and plays.get(uid, pid)["state"] == "done"


def test_a_trailing_stop_keeps_a_delivery_buy_a_delivery_buy(uid) -> None:
    got = _compile(uid, do={"kind": "buy", "symbol": "RELIANCE", "size": {"inr": 20000}},
                   manage={"trail_pct": 3}, contract=NOW)
    spec = got["play"]["spec"]
    assert spec["do"]["product"] == "CNC"
    assert spec["manage"]["exit_when"]["all"][0]["right"] == "hi_since * 0.97"
    assert any("3% trail" in a for a in got["assumed"])


def test_an_edit_that_adds_a_stop_puts_it_in_the_book(uid) -> None:
    got = _compile(uid, do={"kind": "buy", "symbol": "RELIANCE", "size": {"qty": 5}}, contract=NOW)
    pid = got["play"]["id"]
    again = plays.compile_play(uid, {**got["play"]["spec"], "manage": {"stop_pct": 3}}, pid=pid)
    assert [r[:4] for r in _resting(uid)] == [("RELIANCE", "SELL", "SL-M", 5.0)]
    assert any("a stop at" in a for a in again["assumed"])


def test_a_stop_sells_what_is_left_when_part_was_sold(uid) -> None:
    px = float(paper.mark_price("RELIANCE"))
    paper.place_order(uid, "RELIANCE", "BUY", 10, source="eval", stop_loss=round(px * 0.9, 2))
    paper.place_order(uid, "RELIANCE", "SELL", 4, source="eval")
    paper.on_price("RELIANCE", px * 0.85)
    assert "RELIANCE" not in _held(uid)


# ── conditions that already hold ────────────────────────────────────────────

def test_a_close_whose_condition_already_holds_acts_at_the_next_bar(uid) -> None:
    paper.place_order(uid, "RELIANCE", "BUY", 10, source="eval")
    got = _compile(uid, do={"kind": "sell", "symbol": "RELIANCE"},
                   when={"all": [{"left": "close", "op": "above", "right": 1}]},
                   contract={"lifespan": "once_when", "authority": "propose"})
    assert got["play"]["history"].get("true_now")
    assert any("acts at the next bar close" in a for a in got["assumed"])
    pid = got["play"]["id"]
    data, _ = plays.frame(got["play"]["spec"], limit=5)
    plays._set(pid, last_eval_ts=data["t"][-2])
    out = plays._tick(pid, force=True)
    assert [e["kind"] for e in out if e] == ["proposed"]


# ── one book, many plays ────────────────────────────────────────────────────

def test_a_play_gives_up_shares_another_play_sold(uid) -> None:
    a = _compile(uid, do={"kind": "buy", "symbol": "RELIANCE", "size": {"qty": 20}}, contract=NOW)
    _compile(uid, do={"kind": "sell", "symbol": "RELIANCE", "size": {"pct": 50}}, contract=NOW)
    assert plays._load(a["play"]["id"])["held_qty"] == _held(uid)["RELIANCE"] == 10


# ── the clock ───────────────────────────────────────────────────────────────

def test_no_intraday_position_opens_after_the_square_off(uid, book, monkeypatch) -> None:
    _at(monkeypatch, 15, 40)
    got = _compile(uid, do={"kind": "buy", "symbol": "RELIANCE", "size": {"inr": 20000}, "product": "MIS"},
                   contract=NOW)
    ev = got["play"]["_now_event"]
    assert ev["kind"] == "rejected" and "market is closed" in ev["body"]["reason"]
    assert not daybook.open_positions(uid)


def test_a_delivery_fill_after_the_close_says_so(uid, monkeypatch) -> None:
    _at(monkeypatch, 18, 0)
    got = _compile(uid, do={"kind": "buy", "symbol": "RELIANCE", "size": {"inr": 20000}}, contract=NOW)
    body = got["play"]["_now_event"]["body"]
    assert body["after_hours"] and "market is closed" in body["reason"]
    _at(monkeypatch, 11, 0)
    got = _compile(uid, do={"kind": "buy", "symbol": "RELIANCE", "size": {"inr": 20000}}, contract=NOW)
    assert not got["play"]["_now_event"]["body"].get("after_hours")


def test_a_crypto_intraday_leg_sized_in_coins_resolves(monkeypatch) -> None:
    monkeypatch.setattr(daybook, "_equity_px", lambda s: (65000.0, False))
    monkeypatch.setattr(paper, "fx_rate", lambda ccy: (88.0, 0))
    leg, = daybook.resolve({"kind": "long", "symbol": "BTCUSDT", "size": {"qty": 0.01}}, "MIS")
    assert leg["qty"] == 0.01 and leg["fx"] == 88.0


def test_pausing_a_once_play_that_fired_does_not_let_it_fire_again(uid) -> None:
    """A once play that proposed, paused and resumed fired again beside its
    waiting proposal; confirming both bought twice."""
    got = _compile(uid, do={"kind": "buy", "symbol": "RELIANCE", "size": {"qty": 3}},
                   when={"all": [{"left": "close", "op": "above", "right": 1}], "every_bar": True},
                   contract={"lifespan": "once_when", "authority": "propose"})
    pid = got["play"]["id"]
    data, _ = plays.frame(got["play"]["spec"], limit=5)
    plays._set(pid, last_eval_ts=data["t"][-2])
    assert [e["kind"] for e in plays._tick(pid, force=True) if e] == ["proposed"]
    for action in ("pause", "resume", "resume"):
        plays.act(uid, pid, action)
    plays._set(pid, last_eval_ts=data["t"][-2])
    assert not [e for e in plays._tick(pid, force=True) if e]
    eid = [e for e in plays.events(uid) if e["kind"] == "proposed"][0]["id"]
    plays.resolve_event(uid, eid, "confirm")
    assert _held(uid) == {"RELIANCE": 3.0}
