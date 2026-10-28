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
    import types
    import brokers
    monkeypatch.setenv("PIVOT_LIVE_PLAYS", "1")
    monkeypatch.setattr(revenue, "_ready_for", None)
    # a connected broker that takes every order: billing is what is tested here
    monkeypatch.setattr(brokers, "active_connection", lambda user_id: types.SimpleNamespace(broker="zerodha"))
    monkeypatch.setattr(plays, "_live_orders", lambda t: [])
    monkeypatch.setattr(plays, "_send_live", lambda play, orders, eid: [{"order_id": "T1"}])
    return uid


def _fire_live(play, data, i):
    """A live fire is a proposal; the trader confirms it and it fills."""
    ev = plays._fire(play, data, {}, i, late=False)
    if ev["kind"] != "proposed":
        return ev
    return plays.resolve_event(play["user_id"], ev["id"], "confirm")["result"]


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
    plays._set(pid, venue="live")                     # F&O is billed like this once it can run live
    ev = _fire_now(pid)
    assert ev["kind"] == "proposed", ev
    ev = plays.resolve_event(live, ev["id"], "confirm")["result"]
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
    assert _fire_live(play, data, len(data["t"]) - 1)["kind"] == "filled"
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
    assert _fire_live(sell, data, len(data["t"]) - 1)["kind"] == "filled"
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
    import types
    import brokers
    monkeypatch.setattr(brokers, "active_connection", lambda user_id: None)
    with pytest.raises(plays.Bad, match="Connect your broker"):
        plays.act(uid, pid, "live")
    monkeypatch.setattr(brokers, "active_connection", lambda user_id: types.SimpleNamespace(broker="zerodha"))
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


# ── the routes, the wallet's payment paths ─────────────────────────────────

def test_the_trader_routes_answer(live) -> None:
    revenue.credit(live, 10000, "TOPUP", "pay_r")
    pid = _live_play(live, {**RIL, "do": {"kind": "buy", "symbol": "RELIANCE", "size": {"inr": 5000}},
                            "when": {"all": [{"left": "close", "op": "above", "right": 0}]},
                            "contract": {"lifespan": "standing", "authority": "act"}})
    code, b = plays.api_get(live, "/billing")
    assert code == 200 and b["state"] == "ACTIVE" and b["live_open"] is True
    code, est = plays.api_get(live, f"/billing/estimate/{pid}")
    assert code == 200 and est["segment"] == "DELIVERY" and est["pivot_paise"] == 500
    assert plays.api_get(live, "/billing/2026-10")[1]["month"] == "2026-10"
    assert plays.api_post(live, "/billing/topup", {"amount_paise": 999})[0] == 400


@pytest.fixture
def razorpay(monkeypatch):
    import billing
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_x")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "secret_k")
    monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", "secret_w")
    made = []

    def fake(method, path, body=None):
        made.append((method, path, body))
        return {"id": f"order_{len(made)}", "amount": body["amount"]}
    monkeypatch.setattr(billing, "http", fake)
    return billing


def test_a_topup_credits_once_and_only_its_owner(uid, razorpay) -> None:
    code, o = plays.api_post(uid, "/billing/topup", {"amount_paise": 25000})
    assert code == 200 and o["order_id"] == "order_1" and o["key_id"] == "rzp_test_x"
    sig = razorpay._hmac_hex("secret_k", b"order_1|pay_9")
    good = {"razorpay_order_id": "order_1", "razorpay_payment_id": "pay_9", "razorpay_signature": sig}
    assert plays.api_post(uid, "/billing/topup/verify", {**good, "razorpay_signature": "0" * 64})[0] == 400
    assert plays.api_post(2, "/billing/topup/verify", good)[0] == 404              # another account
    code, out = plays.api_post(uid, "/billing/topup/verify", good)
    assert code == 200 and out["credited_paise"] == 25000
    assert plays.api_post(uid, "/billing/topup/verify", good)[1]["duplicate"] is True
    assert revenue.summary(uid)["wallet_paise"] == 25000


def test_the_webhook_credits_a_topup_whose_browser_closed(uid, razorpay) -> None:
    import json as _json
    import entitlements as ent
    ent.bind(server._users, server._users_lock)
    plays.api_post(uid, "/billing/topup", {"amount_paise": 10000})
    raw = _json.dumps({"event": "payment.captured", "payload": {"payment": {"entity": {
        "id": "pay_w", "order_id": "order_1", "amount": 10000, "notes": {"kind": "wallet"}}}}}).encode()
    hdr = {"X-Razorpay-Signature": razorpay._hmac_hex("secret_w", raw), "x-razorpay-event-id": "evt_1"}
    assert razorpay.api_webhook(raw, hdr)[1]["result"].startswith("wallet 200")
    assert razorpay.api_webhook(raw, hdr)[1].get("duplicate") is True             # redelivered
    assert revenue.summary(uid)["wallet_paise"] == 10000


# ── what is measured, and what PostHog hears ───────────────────────────────

def test_trades_are_counted_by_venue_and_sent_to_posthog(live, monkeypatch) -> None:
    import ops_api
    heard = []
    monkeypatch.setattr(revenue, "track", lambda uid, ev, props=None: heard.append((ev, props or {})))
    revenue.credit(live, 10000, "TOPUP", "pay_1")
    buy = {**RIL, "do": {"kind": "buy", "symbol": "RELIANCE", "size": {"inr": 5000}},
           "when": {"all": [{"left": "close", "op": "above", "right": 0}]},
           "contract": {"lifespan": "standing", "authority": "act"}}
    paper_pid = plays.compile_play(live, buy, chart_symbol="RELIANCE", chart_interval="1d")["play"]["id"]
    live_pid = _live_play(live, buy)
    for pid in (paper_pid, live_pid):
        plays._set(pid, accepted=1, fires=1)
        play = plays._load(pid)
        data, _ = plays.frame(play["spec"])
        assert _fire_live(play, data, len(data["t"]) - 1)["kind"] == "filled"
    trades = [p for e, p in heard if e == "play_trade"]
    assert [t["venue"] for t in trades] == ["paper", "live"]
    assert trades[1]["charge_paise"] == revenue.fee_paise(revenue.DELIVERY, round(trades[1]["value_inr"] * 100))
    assert ("play_venue_changed", {"venue": "live", "play_id": live_pid, "segment": "DELIVERY"}) in heard
    m = revenue.metrics(7)
    assert m["trades"]["total"] == 2 and m["trades"]["traders"] == 1
    assert m["trades"]["by_venue"]["live"]["trades"] == 1 and m["trades"]["by_venue"]["paper"]["trades"] == 1
    assert m["trades"]["by_segment"]["DELIVERY"]["volume_paise"] == m["trades"]["volume_paise"] > 0
    assert m["revenue"]["month"]["live_traders"] == 1 and m["revenue"]["month"]["charged_paise"] == trades[1]["charge_paise"]
    assert m["series"][-1]["trades"] == 2 and m["top_traders"][0]["email"] == "t@x.in"
    assert m["plays"]["armed_live"] == 1 and m["wallet"]["topups"] == 1
    code, out = ops_api._get(["trading"], {"days": "7"})
    assert code == 200 and out["trades"]["total"] == 2
    assert ops_api._get(["trading"], {"days": "a week"})[0] == 400


def test_a_sell_is_counted_as_an_exit() -> None:
    assert revenue.is_exit("filled", {"ticket": {"side": "SELL"}}, {"kind": "buy"})
    assert revenue.is_exit("exited", {}, {"kind": "options"})
    assert not revenue.is_exit("filled", {"ticket": {"side": "BUY"}}, {"kind": "buy"})
