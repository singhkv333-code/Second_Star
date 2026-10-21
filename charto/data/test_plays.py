"""Plays: the model-written measures, the condition replay, the autonomy
dials and the paper fills, against the stored bars and a throwaway account
database."""
from __future__ import annotations

import sqlite3

import pytest

import dataserver as server
import chat_v2
import paper
import plays

RSI = "def compute(data, params):\n    return ta.rsi(data['close'], 14)"


@pytest.fixture
def uid(monkeypatch, tmp_path):
    con = sqlite3.connect(tmp_path / "users.db", check_same_thread=False)
    con.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT, name TEXT, pw_hash TEXT, "
                "pw_salt TEXT, created INTEGER, email_verified INTEGER)")
    con.execute("INSERT INTO users (id, email) VALUES (1, 't@x.in')")
    con.commit()
    monkeypatch.setattr(server, "_users", con)
    monkeypatch.setattr(plays, "_ready", False)
    monkeypatch.setattr(paper, "_ready", False)
    plays.bind(server, None, paper)
    server._req.user = (1, "t@x.in")
    server._req.symbol, server._req.ctx_interval = "RELIANCE", "1d"
    yield 1
    server._req.user = None
    server._req.plays = None
    plays._INDEX.clear()


def _rsi_play(**over):
    p = {"title": "RELIANCE RSI", "symbol": "RELIANCE", "interval": "1d",
         "measures": [{"name": "rsi14", "title": "RSI 14", "code": RSI}],
         "when": {"all": [{"left": "rsi14", "op": "cross_down", "right": 30}]},
         "contract": {"lifespan": "standing", "authority": "notify"}}
    p.update(over)
    return p


def test_a_ratio_measure_needs_no_backend(uid) -> None:
    got = plays.tool_measure("test", {
        "name": "pair", "title": "RELIANCE / TCS", "expr": "a / b",
        "inputs": {"a": {"bars": {"symbol": "RELIANCE"}}, "b": {"bars": {"symbol": "TCS"}}}},
        "RELIANCE", "1d")
    assert "error" not in got, got
    assert got["with_value"] > 100 and got["range"][0] > 0


def test_a_measure_that_reads_the_future_is_refused(uid) -> None:
    peek = "def compute(data, params):\n    c = data['close']\n    return [c[min(i + 1, len(c) - 1)] for i in range(len(c))]"
    got = plays.tool_measure("test", {"name": "peek", "code": peek}, "RELIANCE", "1d")
    assert "reads the future" in got["error"]


def test_compile_replays_history_and_arms(uid) -> None:
    got = plays.tool_play("compile", _rsi_play())
    assert got["state"] == "armed"
    assert got["history"]["bars"] > 300 and got["history"]["fires"] >= 1
    assert got["contract"] == {"runs": "Every time it triggers", "acts": "Tells you"}
    card = [c for c in server._scene.cards if c.get("kind") == "play"][-1]
    assert card["summary"].startswith("Checks RELIANCE at each daily close and tells you each time")
    assert card["conditions"][0]["text"] == "RSI 14 crosses below 30"


def test_errors_say_what_to_fix(uid) -> None:
    bad = plays.tool_play("compile", _rsi_play(when={"all": [{"left": "foo", "op": "above", "right": 1}]}))
    assert "not a number, a field" in bad["error"]
    bad = plays.tool_play("compile", _rsi_play(when={"all": [{"left": "rsi14", "op": "bigger", "right": 1}]}))
    assert "must be one of" in bad["error"]


def test_defaults_are_stated_not_hidden(uid) -> None:
    got = plays.tool_play("compile", {"symbol": "RELIANCE", "interval": "1d",
                                      "when": {"all": [{"left": "close", "op": "above", "right": 1}]},
                                      "do": {"kind": "buy"}})
    assert "sized at ₹10,000" in got["assumed"]
    assert "it proposes the order for you to confirm" in got["assumed"]


def test_act_now_fills_and_first_unattended_fire_proposes(uid) -> None:
    got = plays.tool_play("compile", {"title": "buy", "symbol": "RELIANCE", "interval": "1d",
                                      "do": {"kind": "buy", "size": {"inr": 5000}},
                                      "manage": {"stop_pct": 5},
                                      "contract": {"lifespan": "now", "authority": "act"}})
    assert got["did_now"]["kind"] == "filled"
    held = {h["symbol"]: h["quantity"] for h in paper.api_holdings(uid)[1]}
    assert held["RELIANCE"] == got["did_now"]["body"]["ticket"]["qty"]
    # an armed play told to act still proposes on its first fire: nobody is watching it
    p = plays.tool_play("compile", _rsi_play(do={"kind": "buy", "size": {"inr": 4000}},
                                             contract={"lifespan": "standing", "authority": "act"}))
    play = plays._load(p["play_id"])
    data, _ = plays.frame(play["spec"])
    series, _ = plays.run_measures(play["spec"], data)
    ev = plays._fire(play, data, series, len(data["t"]) - 1, late=False)
    assert ev["kind"] == "proposed" and "first fire" in ev["body"]["reason"]


def test_confirming_a_proposal_fills_it_once(uid) -> None:
    got = plays.tool_play("compile", {"title": "tcs", "symbol": "TCS", "interval": "1d",
                                      "do": {"kind": "buy", "size": {"inr": 8000}},
                                      "contract": {"lifespan": "now", "authority": "propose"}})
    assert got["did_now"]["kind"] == "proposed"
    eid = [e for e in plays.events(uid) if e["kind"] == "proposed"][0]["id"]
    assert plays.api_post(uid, f"/events/{eid}", {"action": "confirm"})[1]["status"] == "confirmed"
    assert plays.api_post(uid, f"/events/{eid}", {"action": "confirm"})[0] == 400
    assert plays.get(uid, got["play_id"])["accepted"] == 1


def test_missed_bars_are_reported_late_and_never_traded(uid) -> None:
    p = plays.tool_play("compile", _rsi_play(do={"kind": "buy", "size": {"inr": 4000}},
                                             contract={"lifespan": "standing", "authority": "act"}))
    last = p["history"]["last_fire"]
    plays._set(p["play_id"], last_eval_ts=last - 86400 * 5)
    out = plays._tick(p["play_id"], force=True)
    assert out and all(e["kind"] == "fired" and e["body"]["late"] for e in out)
    assert not paper.api_holdings(uid)[1]


def test_an_edit_reports_what_moved_structurally(uid) -> None:
    p = plays.tool_play("compile", _rsi_play(contract={"lifespan": "standing", "authority": "notify",
                                                       "inferred_from": "tell me"}))
    got = plays.tool_play("edit", _rsi_play(when={"all": [{"left": "rsi14", "op": "cross_down",
                                                           "right": 25}]}), play_id=p["play_id"])
    assert [d["what"] for d in got["changed"]] == ["conditions"]


def test_plays_replace_alerts_and_strategy_on_the_v2_surface(uid) -> None:
    server._req.chat_mode, server._req.engine, server._req.chat_v2 = "chat", None, True
    server._req.plays = True
    names = [t["name"] for t in server._tools_for_request()]
    assert "play" in names and "measure" in names
    assert "alerts" not in names and "strategy" not in names
    assert "(play, measure)" in chat_v2.prompt()
    server._req.plays = False
    assert "alerts" in [t["name"] for t in server._tools_for_request()]
    server._req.chat_v2 = None


# ── events: filings and headlines ───────────────────────────────────────────

import play_events  # noqa: E402


@pytest.fixture
def events_db(monkeypatch, tmp_path):
    monkeypatch.setattr(play_events, "_PATH", tmp_path / "events.db")
    monkeypatch.setattr(play_events, "_con", None)
    monkeypatch.setattr(play_events, "fetch_filings", lambda symbol="": [])
    play_events.bind(server)
    yield
    play_events._con = None


def _item(source="filings", sym="RELIANCE", title="Award of Order", body="Reliance Industries has "
          "informed the Exchange about receipt of an order worth Rs 1,200 crore", ts=None):
    import time as _t
    t = int(ts or _t.time())
    return {"source": source, "key": f"{source}:{t}:{title}", "ts": t, "seen": t, "symbol": sym,
            "who": "Reliance Industries Limited" if source == "filings" else "Mint", "title": title,
            "body": body, "url": "https://example.test/x", "late": False}


def _event_play(**over):
    p = {"title": "RELIANCE order wins", "symbol": "RELIANCE", "interval": "1d",
         "on": {"sources": ["filings"], "symbols": ["RELIANCE"], "match": ["order"],
                "judge": "Does this announce a new order?"},
         "contract": {"lifespan": "standing", "authority": "notify"}}
    p.update(over)
    return p


def test_unconnected_sources_are_refused_in_one_sentence(uid, events_db) -> None:
    got = plays.tool_play("compile", _event_play(on={"sources": ["x"], "match": ["Elon"]}))
    assert "not connected yet" in got["error"] and "filings" in got["error"]


def test_an_event_play_reads_filings_and_quotes_its_judge(uid, events_db, monkeypatch) -> None:
    got = plays.tool_play("compile", _event_play())
    assert got["state"] == "armed", got
    card = [c for c in server._scene.cards if c.get("kind") == "play"][-1]
    assert card["summary"].startswith("Reads NSE filings from RELIANCE and tells you each time")
    asked = []
    monkeypatch.setattr(play_events, "_call_model", lambda q, it: asked.append(q) or {
        "verdict": True, "confidence": 0.9, "value": 1200,
        "quote": "receipt of an order worth Rs 1,200 crore"})
    plays.on_items([_item(), _item(sym="TCS", title="Order", body="TCS order")])
    evs = plays.events(uid)
    assert len(evs) == 1 and evs[0]["kind"] == "fired"         # TCS's filing is not this play's
    assert evs[0]["body"]["event"]["quote"] == "receipt of an order worth Rs 1,200 crore"
    assert asked == ["Does this announce a new order?"]
    # the store hands each item over once; a judged item is never asked twice
    plays.on_items([_item()])
    assert asked == ["Does this announce a new order?"]


def test_a_judge_that_cannot_point_at_the_words_does_not_fire(uid, events_db, monkeypatch) -> None:
    plays.tool_play("compile", _event_play())
    monkeypatch.setattr(play_events, "_call_model", lambda q, it: {
        "verdict": True, "confidence": 0.95, "value": None, "quote": "a 5,000 crore defence order"})
    plays.on_items([_item()])
    assert plays.events(uid) == []


def test_a_headline_alone_never_places_an_order(uid, events_db, monkeypatch) -> None:
    plays.tool_play("compile", _event_play(
        on={"sources": ["news"], "match": ["Reliance"], "judge": "Is this good news for Reliance?"},
        do={"kind": "buy", "size": {"inr": 4000}}, contract={"lifespan": "standing", "authority": "act"}))
    pid = plays.list_plays(uid)[0]["id"]
    plays._set(pid, accepted=1, fires=1)                       # past the first-fire check
    monkeypatch.setattr(play_events, "_call_model", lambda q, it: {
        "verdict": True, "confidence": 0.9, "value": None, "quote": "Reliance shares jump"})
    plays.on_items([_item(source="news", sym="", title="BREAKING: Reliance shares jump", body="")])
    ev = plays.events(uid)[0]
    assert ev["kind"] == "proposed" and "headline alone" in ev["body"]["reason"]
    assert not paper.api_holdings(uid)[1]


def test_cue_words_are_stripped_before_the_judge_reads(events_db) -> None:
    assert play_events._clean("BREAKING: JUST IN 🚨 Reliance wins order") == "Reliance wins order"


# ── the day book: shorts, option legs, trailing, leg rules, square-off ──────

import daybook  # noqa: E402
from datetime import datetime as _dt  # noqa: E402


def _fake_chain(prices):
    rows = [{"strike": k, "ce": {"sym": f"NIFTY26OCT{int(k)}CE", "ltp": prices.get(f"{int(k)}CE", 100.0)},
             "pe": {"sym": f"NIFTY26OCT{int(k)}PE", "ltp": prices.get(f"{int(k)}PE", 100.0)}}
            for k in (22700.0, 22750.0, 22800.0)]
    return {"available": True, "underlying": "NIFTY", "index": True, "source": "kite", "expiry": "2026-10-27",
            "lot_size": 65, "spot": 22760.0, "step": 50.0, "atm_strike": 22750.0, "rows": rows}


@pytest.fixture
def book(uid, monkeypatch):
    import options
    quotes = {}
    monkeypatch.setattr(daybook, "_ready", False)
    daybook.bind(server, quote_fn=lambda keys: {k: {"last_price": quotes[k]} for k in keys if k in quotes})
    monkeypatch.setattr(options, "chain", lambda *a, **k: _fake_chain({"22750CE": 120.0, "22750PE": 110.0}))
    daybook.on_event = plays._day_event
    yield quotes
    daybook.on_event = None


def _straddle(**over):
    p = {"title": "NIFTY 9:20 straddle", "symbol": "RELIANCE", "interval": "5m",
         "when": {"all": [{"time": {"at": "09:20"}}]},
         "do": {"kind": "options", "underlying": "NIFTY",
                "legs": [{"opt": "CE", "strike": "ATM", "side": "sell", "lots": 1},
                         {"opt": "PE", "strike": "ATM", "side": "sell", "lots": 1}]},
         "manage": {"stop_pct": 25, "on_leg_stop": "move_others_to_cost"},
         "contract": {"lifespan": "standing", "authority": "act"}}
    p.update(over)
    return p


def _fire_now(pid):
    play = plays._load(pid)
    plays._set(pid, accepted=1, fires=1)
    play = plays._load(pid)
    data, _ = plays.frame(play["spec"])
    series, _ = plays.run_measures(play["spec"], data)
    return plays._fire(play, data, series, len(data["t"]) - 1, late=False)


def test_a_straddle_opens_two_short_legs_with_premium_stops(book) -> None:
    got = plays.tool_play("compile", _straddle())
    assert got["state"] == "armed", got
    card = [c for c in server._scene.cards if c.get("kind") == "play"][-1]
    assert card["action"]["text"].startswith("NIFTY options: sell 1 lot ATM CE, sell 1 lot ATM PE")
    ev = _fire_now(got["play_id"])
    assert ev["kind"] == "filled", ev
    legs = {p["opt"]: p for p in daybook.open_positions(1)}
    assert legs["CE"]["side"] == "short" and legs["CE"]["qty"] == 65 and legs["CE"]["product"] == "MIS"
    assert legs["CE"]["stop_px"] == 150.0 and legs["PE"]["stop_px"] == 137.5


def test_one_leg_stopped_moves_the_other_to_cost(book) -> None:
    pid = plays.tool_play("compile", _straddle(do={**_straddle()["do"], "product": "MIS"}))["play_id"]
    _fire_now(pid)
    book["NFO:NIFTY26OCT22750CE"] = 151.0        # the call runs through its stop
    book["NFO:NIFTY26OCT22750PE"] = 80.0
    out = daybook.sweep(now=_dt(2026, 10, 22, 10, 0, tzinfo=daybook.IST))
    assert [o["why"] for o in out] == ["stop"]
    pe = daybook.open_positions(1)[0]
    assert pe["opt"] == "PE" and pe["stop_px"] == pe["entry_px"]
    kinds = [e["kind"] for e in plays.events(1)]
    assert "exited" in kinds and "note" in kinds
    # and at 15:20 the intraday leg that is left is squared off
    out = daybook.sweep(now=_dt(2026, 10, 22, 15, 21, tzinfo=daybook.IST))
    assert out and out[0]["why"].startswith("intraday square-off") and not daybook.open_positions(1)


def test_a_whole_position_loss_limit_closes_every_leg(book) -> None:
    pid = plays.tool_play("compile", _straddle(manage={"max_loss_inr": 3000}))["play_id"]
    _fire_now(pid)
    book["NFO:NIFTY26OCT22750CE"] = 150.0
    book["NFO:NIFTY26OCT22750PE"] = 130.0       # both against: about ₹3,250 gross plus costs
    out = daybook.sweep(now=_dt(2026, 10, 22, 11, 0, tzinfo=daybook.IST))
    assert len(out) == 2 and all("limit" in o["why"] for o in out)


def test_an_intraday_short_trails_and_a_delivery_short_is_refused(book) -> None:
    bad = plays.tool_play("compile", {"symbol": "RELIANCE", "interval": "5m",
                                      "do": {"kind": "short", "product": "CNC"},
                                      "contract": {"lifespan": "now", "authority": "act"}})
    assert "intraday only" in bad["error"]
    got = plays.tool_play("compile", {"symbol": "RELIANCE", "interval": "5m",
                                      "do": {"kind": "short", "size": {"inr": 50000}},
                                      "manage": {"trail_pct": 1},
                                      "contract": {"lifespan": "now", "authority": "act"}})
    assert got["did_now"]["kind"] == "filled", got
    p = daybook.open_positions(1)[0]
    assert p["side"] == "short" and p["product"] == "MIS"
    entry = p["entry_px"]
    assert daybook.check(p, entry * 0.97, stale=False) is None      # it falls: the trail follows
    p = daybook.open_positions(1)[0]
    assert abs(p["stop_px"] - entry * 0.97 * 1.01) < 0.01
    r = daybook.check(p, entry * 0.985, stale=False)                # bounces through the trail
    assert r and r["why"] == "trailing stop" and r["pnl"] > 0
