"""Revenue: the spec's acceptance tests (Revenuemodel.md §13) that the core
build covers, each held to the rule it names."""
from __future__ import annotations

import threading

import pytest

import dataserver as server
import plays
import revenue
from test_plays import _fire_now, _straddle, book, uid  # noqa: F401  (fixtures and helpers)

RIL = {"title": "t", "symbol": "RELIANCE", "interval": "1d"}


@pytest.fixture
def live(uid, monkeypatch):
    monkeypatch.setenv("PIVOT_LIVE_PLAYS", "1")
    monkeypatch.setattr(revenue, "_ready_for", None)
    return uid


def _live_play(uid, raw: dict) -> int:
    pid = plays.compile_play(uid, raw, chart_symbol="RELIANCE", chart_interval="1d")["play"]["id"]
    plays.act(uid, pid, "live")
    return pid


def _charges(uid) -> list[tuple]:
    return server._users.execute("SELECT segment, fee_paise, charge_paise, waived_paise FROM rev_trades "
                                 "WHERE user_id=? ORDER BY id", (uid,)).fetchall()


# ── the fee ─────────────────────────────────────────────────────────────────

def test_delivery_is_the_lesser_of_five_rupees_and_a_tenth_of_a_percent() -> None:
    assert revenue.fee_paise(revenue.DELIVERY, 2_000_00) == 200          # ₹2,000 → ₹2.00
    assert revenue.fee_paise(revenue.DELIVERY, 50_000_00) == 500         # ₹50,000 → ₹5.00
    assert revenue.fee_paise(revenue.DELIVERY, 3_00_000_00) == 500       # a ₹3 lakh rebalance → ₹5
    assert revenue.fee_paise(revenue.FNO, 1) == 1000


def test_gst_is_inside_the_price() -> None:
    assert revenue.gst_split(1000) == {"total_paise": 1000, "base_paise": 847, "gst_paise": 153}


def test_the_segment_comes_from_what_the_action_trades() -> None:
    assert revenue.segment_of({"kind": "buy", "symbol": "RELIANCE"}) == revenue.DELIVERY
    assert revenue.segment_of({"kind": "rebalance"}) == revenue.DELIVERY
    assert revenue.segment_of({"kind": "buy", "symbol": "RELIANCE", "product": "MIS"}) == revenue.FNO
    assert revenue.segment_of({"kind": "options", "underlying": "NIFTY"}) == revenue.FNO
    assert revenue.segment_of({"kind": "buy", "symbol": "BTCUSDT"}) == revenue.FNO


# ── the ledger ──────────────────────────────────────────────────────────────

def test_a_replayed_fill_is_charged_once(uid) -> None:
    revenue.credit(uid, 10000, "TOPUP", "pay_1")
    for _ in range(3):
        revenue.charge(uid, "play_event:7", 1, revenue.FNO, 0)
    assert _charges(uid) == [("FNO", 1000, 1000, 0)]
    assert revenue.summary(uid)["wallet_paise"] == 9000


def test_the_cap_takes_what_is_left_then_waives_and_says_so_once(uid) -> None:
    revenue.credit(uid, 50000, "TOPUP", "pay_1")
    for i in range(49):
        revenue.charge(uid, f"a{i}", 1, revenue.FNO, 0)                  # ₹490
    revenue.charge(uid, "five", 1, revenue.DELIVERY, 5_000_00)           # ₹5 → ₹495
    near = revenue.charge(uid, "near", 1, revenue.FNO, 0)
    assert (near["charge_paise"], near["waived_paise"], near["cap_reached"]) == (400, 600, True)
    after = revenue.charge(uid, "after", 1, revenue.FNO, 0)
    assert (after["charge_paise"], after["cap_reached"]) == (0, False)
    st = revenue.statement(uid)
    assert st["total_paise"] == 49900 and st["waived_paise"] == 1600 and st["trade_count"] == 52


def test_fills_at_the_same_moment_never_pass_the_cap(uid) -> None:
    revenue.credit(uid, 50000, "TOPUP", "pay_1")
    for i in range(49):
        revenue.charge(uid, f"a{i}", 1, revenue.FNO, 0)                  # ₹490
    ts = [threading.Thread(target=revenue.charge, args=(uid, f"c{i}", 1, revenue.FNO, 0)) for i in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert revenue.summary(uid)["month"]["charged_paise"] == 49900


def test_a_wallet_that_dipped_below_zero_is_recovered_by_the_next_topup(uid) -> None:
    revenue.credit(uid, 300, "ADJUSTMENT", "seed")
    assert revenue.may_open(uid, revenue.FNO)[0] is False
    revenue.charge(uid, "in-flight", 1, revenue.FNO, 0)                  # the trade in flight completes
    assert revenue.summary(uid)["wallet_paise"] == -700
    revenue.credit(uid, 10000, "TOPUP", "pay_2")
    assert revenue.summary(uid)["wallet_paise"] == 9300
    assert revenue.credit(uid, 10000, "TOPUP", "pay_2") is None          # a replayed payment adds nothing


# ── the engine ──────────────────────────────────────────────────────────────

def test_a_two_leg_straddle_firing_once_is_one_ten_rupee_trade(live, book) -> None:
    revenue.credit(live, 10000, "TOPUP", "pay_1")
    pid = plays.tool_play("compile", _straddle())["play_id"]
    plays.act(live, pid, "live")
    ev = _fire_now(pid)
    assert ev["kind"] == "filled", ev
    assert _charges(live) == [("FNO", 1000, 1000, 0)]


def test_paper_plays_and_rejected_actions_are_never_charged(live) -> None:
    revenue.credit(live, 10000, "TOPUP", "pay_1")
    plays.compile_play(live, {**RIL, "do": {"kind": "buy", "symbol": "RELIANCE", "size": {"inr": 5000}},
                              "contract": {"lifespan": "now", "authority": "act"}},
                       chart_symbol="RELIANCE", chart_interval="1d")   # fills on paper, now
    pid = _live_play(live, {**RIL, "do": {"kind": "sell", "symbol": "TCS"},
                            "when": {"all": [{"left": "close", "op": "above", "right": 0}]},
                            "contract": {"lifespan": "standing", "authority": "act"}})
    plays._set(pid, accepted=1, fires=1)
    play = plays._load(pid)
    data, _ = plays.frame(play["spec"])
    ev = plays._fire(play, data, {}, len(data["t"]) - 1, late=False)
    assert ev["kind"] == "rejected"                                    # holds no TCS: zero fills
    assert _charges(live) == []


def test_an_unpaid_account_skips_entries_but_its_exits_run(live) -> None:
    revenue.credit(live, 10000, "TOPUP", "pay_1")
    buy = {**RIL, "do": {"kind": "buy", "symbol": "RELIANCE", "size": {"inr": 5000}},
           "when": {"all": [{"left": "close", "op": "above", "right": 0}]},
           "contract": {"lifespan": "standing", "authority": "act"}}
    pid = _live_play(live, buy)
    plays._set(pid, accepted=1, fires=1)
    play = plays._load(pid)
    data, _ = plays.frame(play["spec"])
    assert plays._fire(play, data, {}, len(data["t"]) - 1, late=False)["kind"] == "filled"
    # the wallet runs dry: the next entry is skipped with the reason, nothing charged
    revenue.charge(live, "drain", pid, revenue.FNO, 0)
    server._users.execute("UPDATE rev_accounts SET wallet_paise=0 WHERE user_id=?", (live,))
    server._users.commit()
    assert revenue.state(live) == "RESTRICTED"
    ev = plays._fire(plays._load(pid), data, {}, len(data["t"]) - 1, late=False)
    assert ev["kind"] == "rejected" and "Exits keep running" in ev["body"]["reason"]
    # an exit never asks: selling what the play holds runs, and is charged like any action
    sell = plays._load(pid)
    sell["spec"] = {**sell["spec"], "do": {"kind": "sell", "symbol": "RELIANCE"}}
    assert plays._fire(sell, data, {}, len(data["t"]) - 1, late=False)["kind"] == "filled"
    assert revenue.summary(live)["wallet_paise"] < 0


def test_live_needs_the_switch_and_money(uid, monkeypatch) -> None:
    raw = {**RIL, "do": {"kind": "buy", "symbol": "RELIANCE"},
           "when": {"all": [{"left": "close", "op": "above", "right": 0}]},
           "contract": {"lifespan": "standing", "authority": "act"}}
    pid = plays.compile_play(uid, raw, chart_symbol="RELIANCE", chart_interval="1d")["play"]["id"]
    monkeypatch.delenv("PIVOT_LIVE_PLAYS", raising=False)
    with pytest.raises(plays.Bad, match="not open"):
        plays.act(uid, pid, "live")
    monkeypatch.setenv("PIVOT_LIVE_PLAYS", "1")
    with pytest.raises(plays.Bad, match="wallet"):
        plays.act(uid, pid, "live")
    revenue.credit(uid, 10000, "TOPUP", "pay_1")
    assert plays.act(uid, pid, "live")["play"]["venue"] == "live"


# ── the build allowance ────────────────────────────────────────────────────

def test_builds_start_at_three_and_a_paper_arm_unlocks_ten_once_a_month(uid, monkeypatch) -> None:
    monkeypatch.setenv("PIVOT_REVENUE", "1")
    draft = {**RIL, "do": {"kind": "notify"}, "when": {"all": [{"left": "close", "op": "above", "right": 0}]}}
    for _ in range(3):
        plays.compile_play(uid, draft, chart_symbol="RELIANCE", chart_interval="1d", arm=False)
    assert revenue.builds(uid)["starter_left"] == 0
    with pytest.raises(plays.Bad, match="Arm any play on paper"):
        plays.compile_play(uid, draft, chart_symbol="RELIANCE", chart_interval="1d", arm=False)
    pid = plays.list_plays(uid)[0]["id"]
    plays.act(uid, pid, "pause")
    plays.act(uid, pid, "resume")                                        # armed on paper
    assert revenue.builds(uid) == {"starter_left": 0, "month_left": 10, "unlocked": True, "enforced": True}
    assert revenue.paper_armed(uid) is False                             # a second arm grants nothing
    plays.compile_play(uid, draft, chart_symbol="RELIANCE", chart_interval="1d")
    assert revenue.builds(uid)["month_left"] == 9
    plays.compile_play(uid, draft, chart_symbol="RELIANCE", chart_interval="1d", pid=pid)   # an edit is free
    assert revenue.builds(uid)["month_left"] == 9
