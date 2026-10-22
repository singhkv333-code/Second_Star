"""Plays: the model-written measures, the condition replay, the autonomy
dials and the paper fills, against the stored bars and a throwaway account
database."""
from __future__ import annotations

import sqlite3

import pytest

import dataserver as server
import chat_v2
import customsec
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
    monkeypatch.setattr(customsec, "_ready", False)
    monkeypatch.setattr(customsec, "_cache", {})
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


# ── custom securities ─────────────────────────────────────────────────────────

def _defence(**over):
    sec = {"name": "defence", "title": "Defence", "kind": "basket",
           "members": [{"symbol": "HAL", "weight": 2}, {"symbol": "BEL", "weight": 1}]}
    sec.update(over)
    return plays.tool_measure("security", security=sec)


def test_a_basket_is_a_symbol_priced_from_its_members(uid) -> None:
    got = _defence()
    assert "error" not in got, got
    assert got["symbol"] == "@DEFENCE" and got["formula"] == "67% HAL + 33% BEL"
    sec = customsec.get("@DEFENCE")
    d = server.get_bars("@DEFENCE", "1d", None, 400)["bars"]
    # base 100 on the anchor day, and the weighted members' moves after it
    anchor = [b for b in d if b["t"] == sec["anchor"]][0]
    assert abs(anchor["c"] - 100) < 1e-6
    hal = {b["t"]: b["c"] for b in server.get_bars("HAL", "1d", None, 400)["bars"]}
    bel = {b["t"]: b["c"] for b in server.get_bars("BEL", "1d", None, 400)["bars"]}
    t = d[-1]["t"]
    m = {x["symbol"]: x for x in sec["members"]}
    want = 100 * (m["HAL"]["weight"] * hal[t] / m["HAL"]["anchor_px"]
                  + m["BEL"]["weight"] * bel[t] / m["BEL"]["anchor_px"])
    assert abs(d[-1]["c"] - want) < 1e-4
    assert all(b["l"] <= min(b["o"], b["c"]) and b["h"] >= max(b["o"], b["c"]) for b in d)
    # the same number whatever window was asked for
    assert server.get_bars("@DEFENCE", "1d", None, 30)["bars"][-1]["c"] == d[-1]["c"]
    assert server._symbol_ready("@DEFENCE") and server._ensure_symbol("@DEFENCE") is None
    assert server.get_bars("@DEFENCE", "15m", None, 50)["bars"]


def test_a_ratio_and_the_names_it_refuses(uid) -> None:
    got = plays.tool_measure("security", security={"name": "hdfc icici", "kind": "ratio",
                                                   "members": ["HDFCBANK", "ICICIBANK"]})
    assert got["symbol"] == "@HDFC_ICICI" and got["formula"] == "HDFCBANK ÷ ICICIBANK"
    b = server.get_bars("@HDFC_ICICI", "1d", None, 5)["bars"][-1]
    h = server.get_bars("HDFCBANK", "1d", None, 5)["bars"][-1]
    i = server.get_bars("ICICIBANK", "1d", None, 5)["bars"]
    i = {x["t"]: x["c"] for x in i}
    if b["t"] == h["t"] and b["t"] in i:
        assert abs(b["c"] - h["c"] / i[b["t"]]) < 1e-6
    bad = plays.tool_measure("security", security={"name": "x", "kind": "ratio", "members": ["TCS"]})
    assert "exactly two members" in bad["error"]
    bad = plays.tool_measure("security", security={"name": "x", "kind": "basket",
                                                   "members": ["TCS", "@HDFC_ICICI"]})
    assert "cannot hold another custom security" in bad["error"]
    # a name is the platform's: another account cannot overwrite it
    server._users.execute("INSERT INTO users (id, email) VALUES (2, 'u@x.in')")
    server._req.user = (2, "u@x.in")
    taken = plays.tool_measure("security", security={"name": "hdfc_icici", "kind": "ratio",
                                                     "members": ["HDFCBANK", "TCS"]})
    assert "is taken" in taken["error"]
    assert customsec.describe(customsec.get("@HDFC_ICICI")) == "HDFCBANK ÷ ICICIBANK"


def test_a_play_watches_a_basket_on_its_members_clock(uid) -> None:
    _defence()
    got = plays.tool_play("compile", {
        "title": "Defence under its 50-DMA", "symbol": "@DEFENCE", "interval": "1d",
        "measures": [{"name": "dma50", "title": "50-day average", "code":
                      "def compute(data, params):\n    return ta.sma(data['close'], 50)"}],
        "when": {"all": [{"left": "close", "op": "cross_down", "right": "dma50"}]},
        "contract": {"lifespan": "standing", "authority": "notify"}})
    assert "error" not in got, got
    assert got["history"]["bars"] > 200 and got["history"]["fires"] >= 1
    # a bar of HAL (the first member) is what clocks it
    assert got["play_id"] in plays._INDEX["HAL"]
    missing = plays.tool_play("compile", {"symbol": "@NOPE", "interval": "1d",
                                          "when": {"all": [{"left": "close", "op": "above",
                                                            "right": 1}]}})
    assert "define it first" in missing["error"]


def test_a_basket_is_bought_whole_and_sold_whole(uid, monkeypatch) -> None:
    _defence()
    got = plays.tool_play("compile", {"title": "buy defence", "symbol": "@DEFENCE", "interval": "1d",
                                      "do": {"kind": "buy", "symbol": "@DEFENCE",
                                             "size": {"inr": 30000}},
                                      "contract": {"lifespan": "now", "authority": "act"}})
    ev = got["did_now"]
    assert ev["kind"] == "filled", ev
    t = ev["body"]["ticket"]
    assert [lg["symbol"] for lg in t["legs"]] == ["HAL", "BEL"]
    assert t["inr"] + t["uninvested_inr"] == pytest.approx(30000)
    held = {h["symbol"]: h["quantity"] for h in paper.api_holdings(uid)[1]}
    assert held == {lg["symbol"]: lg["qty"] for lg in t["legs"]}
    play = plays.get(uid, got["play_id"])
    assert play["held_legs"] == held
    # selling the basket sells exactly what this play bought
    play["spec"]["do"] = {**play["spec"]["do"], "kind": "sell", "size": {"all": True}}
    tk, why = plays._ticket(play, 0)
    assert {lg["symbol"]: lg["qty"] for lg in tk["legs"]} == held, why
    # a ratio is something to watch, not to hold
    plays.tool_measure("security", security={"name": "pair", "kind": "ratio",
                                             "members": ["HDFCBANK", "ICICIBANK"]})
    bad = plays.tool_play("compile", {"symbol": "@PAIR", "interval": "1d",
                                      "do": {"kind": "buy", "symbol": "@PAIR"},
                                      "contract": {"lifespan": "now", "authority": "act"}})
    assert "not something to hold" in bad["error"]


# ── policy plays: rebalance once, then keep it that way ─────────────────────

def _weights(uid):
    rows = paper.api_holdings(uid)[1]
    tot = sum(float(h["market_value"]) for h in rows)
    return {h["symbol"]: float(h["market_value"]) / tot for h in rows}


def test_a_rebalance_trades_the_book_to_its_targets_and_offers_the_policy(uid) -> None:
    paper.place_order(uid, "RELIANCE", "BUY", 30)
    paper.place_order(uid, "TCS", "BUY", 2)
    got = plays.tool_play("compile", {
        "title": "Even it out", "do": {"kind": "rebalance", "of": "portfolio",
                                       "targets": [{"symbol": "RELIANCE", "weight": 1},
                                                   {"symbol": "TCS", "weight": 1}]},
        "mandate": {"max_weight": 0.6},
        "contract": {"lifespan": "now", "authority": "act"}})
    assert "error" not in got, got
    ev = got["did_now"]
    assert ev["kind"] == "filled", ev
    sides = [lg["side"] for lg in ev["body"]["ticket"]["legs"]]
    assert sides == sorted(sides, key=lambda x: x != "SELL")          # sells first
    w = _weights(uid)
    assert abs(w["RELIANCE"] - 0.5) < 0.05 and abs(w["TCS"] - 0.5) < 0.05
    card = [c for c in server._scene.cards if c.get("kind") == "play"][-1]
    assert card["offer"]["action"] == "policy"
    assert card["summary"].startswith("Rebalances the paper portfolio to")
    # the chip: kept that way, weekly and on drift
    out = plays.act(uid, got["play_id"], "policy")
    play = plays.get(uid, got["play_id"])
    assert play["state"] == "armed" and play["spec"]["contract"]["lifespan"] == "policy"
    assert out["play"]["offer"] is None and "every week" in out["play"]["summary"]
    # inside its band: the policy does nothing on this bar
    plays._set(got["play_id"], last_eval_ts=0)
    assert plays._tick(got["play_id"], force=True) == []
    # a drift past 5 points trades it back
    paper.place_order(uid, "RELIANCE", "BUY", 20)
    plays._set(got["play_id"], last_eval_ts=0)
    evs = plays._tick(got["play_id"], force=True)
    assert evs and evs[0]["kind"] == "filled" and "drifted" in evs[0]["body"]["due"]
    assert abs(_weights(uid)["RELIANCE"] - 0.5) < 0.05


def test_the_planner_is_checked_capped_and_proposed_when_it_changes_names(uid, monkeypatch) -> None:
    import play_events
    paper.place_order(uid, "RELIANCE", "BUY", 10)
    paper.place_order(uid, "TCS", "BUY", 3)
    seen = {}

    def fake(system, user, schema, name, **kw):
        seen["brief"] = user
        return {"targets": [{"symbol": "INFY", "weight": 0.7}, {"symbol": "TCS", "weight": 0.3},
                            {"symbol": "ZOMATO", "weight": 0.2}],
                "cash_weight": 0, "why": "INFY has the strongest 3 month return."}
    monkeypatch.setattr(play_events, "respond", fake)
    got = plays.tool_play("compile", {
        "title": "IT, aggressive", "do": {"kind": "rebalance", "of": "portfolio",
                                          "objective": "go aggressive into the strongest IT names",
                                          "universe": ["TCS", "INFY", "HCLTECH"]},
        "contract": {"lifespan": "now", "authority": "act", "discretion": "plan"}})
    assert "error" not in got, got
    ev = got["did_now"]
    # names held change (RELIANCE out, INFY in): proposed, never placed
    assert ev["kind"] == "proposed" and "planner changed" in ev["body"]["reason"]
    t = ev["body"]["ticket"]
    assert "ZOMATO" not in t["targets"] and "not among its candidates" in t["why"]
    assert max(t["targets"].values()) <= 0.4 + 1e-6                   # the 40% cap
    # the brief carries figures code computed, never the model's
    assert '"ret_3m_pct"' in seen["brief"] and '"holds_now"' in seen["brief"]
    eid = [e for e in plays.events(uid) if e["kind"] == "proposed"][0]["id"]
    plays.api_post(uid, f"/events/{eid}", {"action": "confirm"})
    assert "INFY" in _weights(uid) and "RELIANCE" not in _weights(uid)
    bad = plays.tool_play("compile", {"do": {"kind": "rebalance", "of": "portfolio"},
                                      "contract": {"lifespan": "now", "discretion": "plan"}})
    assert "objective" in bad["error"]


def test_a_rebalance_takes_over_a_baskets_holdings_and_weights(uid) -> None:
    _defence()
    b = plays.tool_play("compile", {"title": "buy defence", "symbol": "@DEFENCE", "interval": "1d",
                                    "do": {"kind": "buy", "symbol": "@DEFENCE", "size": {"inr": 40000}},
                                    "contract": {"lifespan": "now", "authority": "act"}})
    legs = plays.get(uid, b["play_id"])["held_legs"]
    got = plays.tool_play("compile", {"title": "keep defence balanced",
                                      "do": {"kind": "rebalance", "of": b["play_id"]},
                                      "contract": {"lifespan": "policy", "authority": "act"}})
    assert "error" not in got, got
    assert "keeps the weights the basket was built with" in got["assumed"]
    new = plays.get(uid, got["play_id"])
    assert new["held_legs"] == legs and plays.get(uid, b["play_id"])["held_legs"] == {}
    assert new["spec"]["do"]["targets"] == {"HAL": pytest.approx(2 / 3, abs=1e-5),
                                            "BEL": pytest.approx(1 / 3, abs=1e-5)}


# ── what the Plays page draws: chips and written-up alerts ──────────────────

def test_conditions_carry_chip_parts_and_events_are_written_up(uid) -> None:
    got = plays.tool_play("compile", _rsi_play(when={"all": [
        {"left": "rsi14", "op": "cross_down", "right": 30, "for_bars": 2},
        {"time": {"after": "09:30"}}]}))
    card = plays.card(plays.get(uid, got["play_id"]))
    p = card["conditions"][0]["parts"]
    assert p == {"kind": "cmp", "left": "RSI 14", "op": "cross_down", "right": "30",
                 "left_live": True, "right_live": False, "for_bars": 2}
    assert card["conditions"][1]["parts"]["kind"] == "time"
    play = plays._load(got["play_id"])
    data, _ = plays.frame(play["spec"])
    series, _ = plays.run_measures(play["spec"], data)
    plays._fire(play, data, series, len(data["t"]) - 1, late=False)
    ev = [e for e in plays.events(uid) if e["play_id"] == got["play_id"]][0]
    a = ev["alert"]
    assert a["label"] == "Stock alert" and a["symbol"] == "RELIANCE"
    text = "".join(x.get("t") or x.get("b") for x in a["line"])
    assert text.startswith("RSI 14 crossed below 30") and any("b" in x for x in a["line"])
    # an order is written up from its ticket, with the price in bold
    t = plays.transcribe({"kind": "filled", "body": {"ticket": {"symbol": "TCS", "side": "BUY", "qty": 3,
                                                                 "price": 4100.0}, "fill_price": 4102.5}},
                         {"symbol": "TCS"})
    assert t["label"] == "Placed on paper" and t["line"] == [{"t": "Bought 3 TCS at "}, {"b": "4,102.5"}]


# ── perpetuals on Hyperliquid ─────────────────────────────────────────────────

@pytest.fixture
def perps(book, monkeypatch):
    import time as _t
    import hyperliquid
    st = {"mid": 100_000.0, "funding": 0.0001}
    uni = lambda max_age=30.0: {"BTC": {"coin": "BTC", "max_leverage": 40, "sz_decimals": 5,  # noqa: E731
                                        "mark": st["mid"], "mid": st["mid"], "oracle": st["mid"],
                                        "funding": st["funding"], "premium": 0.0, "oi": 30_000.0,
                                        "day_volume_usd": 2e9, "prev_day": 99_000.0}}
    now = int(_t.time()) // 3600 * 3600

    def bars(symbol, interval, to, limit):
        rows = [{"t": now - 3600 * (300 - i), "o": 90_000 + i * 30, "h": 90_000 + i * 30 + 50,
                 "l": 90_000 + i * 30 - 50, "c": 90_000 + i * 30 + 10, "v": 1.0} for i in range(300)]
        return {"bars": rows[-limit:], "venue": "Hyperliquid"}
    monkeypatch.setattr(hyperliquid, "universe", uni)
    monkeypatch.setattr(hyperliquid, "mids", lambda max_age=3.0: {"BTC": st["mid"]})
    monkeypatch.setattr(hyperliquid, "bars", bars)
    monkeypatch.setattr(hyperliquid, "funding_history",
                        lambda s, start: [(now - 3600 * k, 0.00002) for k in range(400, 0, -1)])
    monkeypatch.setattr(paper, "fx_rate", lambda ccy: (85.0, None))
    yield st


def test_a_leveraged_perp_long_pays_funding_and_is_liquidated(perps) -> None:
    bad = plays.tool_play("compile", {"symbol": "BTC-PERP", "interval": "1h",
                                      "do": {"kind": "buy", "leverage": 50, "size": {"inr": 85_000}},
                                      "contract": {"lifespan": "now", "authority": "act"}})
    assert "1× to 40×" in bad["error"]
    got = plays.tool_play("compile", {"symbol": "BTC-PERP", "interval": "1h",
                                      "do": {"kind": "buy", "leverage": 10, "size": {"inr": 85_000}},
                                      "contract": {"lifespan": "now", "authority": "act"}})
    assert got["did_now"]["kind"] == "filled", got
    card = [c for c in server._scene.cards if c.get("kind") == "play"][-1]
    assert card["action"]["text"].startswith("A 10× long in BTC-PERP") and card["perp"]["max_leverage"] == 40
    p = daybook.open_positions(1)[0]
    assert p["kind"] == "perp" and p["side"] == "long" and p["product"] == "PERP"
    assert p["qty"] == 0.1                          # ₹85,000 × 10 / (100,000 USD × 85)
    daybook._db().execute("UPDATE day_positions SET funded_ts=funded_ts-7200 WHERE id=?", (p["id"],))
    daybook._sweep_perps(daybook.open_positions(1), daybook._now_ist())
    p = daybook.open_positions(1)[0]
    assert abs(p["funding"] - 0.0001 * 2 * 0.1 * 100_000 * 85) < 0.01      # two hours, long pays
    perps["mid"] = 90_500.0                         # 9.5% down at 10×: past the margin
    out = daybook._sweep_perps(daybook.open_positions(1), daybook._now_ist())
    assert out and out[0]["why"].startswith("liquidated") and not daybook.open_positions(1)
    alert = plays.transcribe(plays.events(1)[0], plays.get(1, got["play_id"])["spec"])
    assert alert["label"] in ("Position closed", "Perp alert")


def test_a_perp_funding_measure_is_replayed_from_its_history(perps) -> None:
    got = plays.tool_play("compile", {"symbol": "BTC-PERP", "interval": "1h",
                                      "measures": [{"name": "fund", "title": "BTC funding", "expr": "f * 100",
                                                    "inputs": {"f": {"perp": {"coin": "BTC", "field": "funding"}}}}],
                                      "when": {"all": [{"left": "fund", "op": "above", "right": 0.005}]},
                                      "do": {"kind": "short", "leverage": 3},
                                      "manage": {"stop_pct": 2},
                                      "contract": {"lifespan": "standing", "authority": "propose"}})
    assert got["state"] == "armed", got
    assert plays.watched() == {"BTC-PERP"}
    card = [c for c in server._scene.cards if c.get("kind") == "play"][-1]
    assert "3× short in BTC-PERP" in card["summary"]
    assert "square" not in " ".join(card["exits"])


# ── rationale and the trader's own edits ─────────────────────────────────────

def test_a_picked_basket_states_its_method_and_each_members_numbers(uid) -> None:
    got = plays.tool_measure("security", security={
        "name": "def5", "title": "Defence leaders", "kind": "basket", "why": "orders are rising",
        "pick": {"industry": "defence", "rank_by": "market_cap", "top": 3, "weight": "market_cap"}})
    assert "error" not in got, got
    again, _ = customsec.pick({"industry": "defence", "rank_by": "market_cap", "top": 3,
                               "weight": "market_cap"})
    assert [m["symbol"] for m in got["members"]] == [m["symbol"] for m in again]   # same method, same names
    assert all(m["why"].startswith(f"#{i + 1} of ") for i, m in enumerate(got["members"]))
    assert max(m["weight"] for m in got["members"]) <= 0.4 + 1e-9
    assert "Aerospace & Defense" in got["method"]["text"] and got["why"] == "orders are rising"
    sec = customsec.edit(1, "@DEF5", {"weights": {got["members"][2]["symbol"]: 50}})
    w = {m["symbol"]: m["weight"] for m in sec["members"]}
    assert abs(w[got["members"][2]["symbol"]] - 0.5) < 1e-6 and abs(sum(w.values()) - 1) < 1e-6
    assert sec["method"]["edited"] and all(m.get("why") for m in sec["members"])


def test_the_trader_edits_a_number_on_the_card_and_the_play_is_rebuilt(uid) -> None:
    got = plays.tool_play("compile", {**_rsi_play(), "why": "oversold bounces have held this year",
                                      "do": {"kind": "buy", "size": {"inr": 20000}},
                                      "manage": {"stop_pct": 3},
                                      "contract": {"lifespan": "standing", "authority": "propose"}})
    pid = got["play_id"]
    plays.act(1, pid, "pause")
    out = plays.edit_field(1, pid, "cond.0.right", "25")
    assert out["play"]["conditions"][0]["text"] == "RSI 14 crosses below 25"
    assert out["play"]["state"] == "paused" and out["play"]["thesis"].startswith("oversold")
    out = plays.edit_field(1, pid, "stop_pct", "1.5")
    assert out["play"]["manage"]["stop_pct"] == 1.5
    out = plays.edit_field(1, pid, "size_inr", "₹50,000")
    assert out["play"]["do"]["size"]["inr"] == 50000 and out["play"]["mandate"]["capital_inr"] >= 50000
    with pytest.raises(plays.Bad):
        plays.edit_field(1, pid, "leverage", 3)
    with pytest.raises(plays.Bad):
        plays.edit_field(1, pid, "stop_pct", 150)


def test_a_plays_conversation_is_kept_on_the_server_with_card_references(uid, monkeypatch) -> None:
    synced = []
    monkeypatch.setattr(server, "_conv_sync", lambda u, chats: synced.append(chats) or {})
    pid = plays.tool_play("compile", _rsi_play())["play_id"]
    card = plays.card(plays.get(1, pid))
    plays.chat_save(1, {"id": "c1", "title": "RSI idea", "turns": [
        {"role": "user", "content": "alert me on oversold RELIANCE"},
        {"role": "assistant", "content": "Built it. [[panel]]", "steps": [{"name": "play", "hint": "compile"}],
         "cards": [card]}]})
    got = plays.chat_get(1, "c1")
    assert got["turns"][1]["cards"] == [{"kind": "play", "id": pid, "title": "RELIANCE RSI"}]
    assert plays.chats_list(1)[0]["last"] == "Built it. "
    assert synced and synced[0][0]["id"] == "plays-c1"
    assert plays.chat_delete(1, "c1")["deleted"] and not plays.chats_list(1)


def test_the_card_offers_choices_from_the_plays_own_data(book) -> None:
    got = plays.tool_play("compile", {"symbol": "RELIANCE", "interval": "5m",
                                      "when": {"all": [{"left": "close", "op": "below", "right": 1000}]},
                                      "do": {"kind": "short", "size": {"inr": 50000}},
                                      "manage": {"stop_pct": 1, "trail_pct": 0.8},
                                      "contract": {"lifespan": "once_when", "authority": "propose"}})
    pid = got["play_id"]
    o = plays.options(1, pid)
    f = o["fields"]
    assert o["atr_pct"] and "× ATR" in f["stop_pct"]["choices"][1]["hint"]
    assert f["size_inr"]["choices"][1] == {"value": 100000.0, "label": "₹1,00,000",
                                           "hint": "risks ₹1,000 at your 1% stop"}
    assert {"max_loss_inr", "target_inr", "exit_at", "target_pct"} <= set(o["more"])
    assert [c["value"] for c in f["cond.0.op"]["choices"]][:2] == ["above", "below"]
    out = plays.edit_field(1, pid, "exit_at", "15:10")
    assert out["play"]["manage"]["exit_at"] == "15:10"
    out = plays.edit_field(1, pid, "cond.0.op", "cross_down")
    assert "crosses below" in out["play"]["conditions"][0]["text"]
    out = plays.edit_field(1, pid, "authority", "manage")
    assert out["play"]["authority"] == "manage"
    out = plays.edit_field(1, pid, "max_loss_inr", "1500")
    assert out["play"]["manage"]["max_loss_inr"] == 1500


def test_one_play_call_builds_its_basket_and_the_play_on_it(uid) -> None:
    got = plays.tool_play("compile", {
        "title": "Defence below its 50-day", "symbol": "@DEF3",
        "security": {"name": "def3", "kind": "basket", "why": "orders",
                     "pick": {"industry": "defence", "top": 3, "rank_by": "market_cap"}},
        "measures": [{"name": "sma50", "title": "50-day average", "code":
                      "def compute(data, params):\n    return ta.sma(data['close'], 50)"}],
        "when": {"all": [{"left": "close", "op": "cross_down", "right": "sma50"}]},
        "contract": {"lifespan": "standing", "authority": "notify"}})
    assert got["state"] == "armed", got
    assert got["security"]["symbol"] == "@DEF3" and len(got["security"]["members"]) == 3
    kinds = [c["kind"] for c in server._scene.cards]
    assert kinds[-2:] == ["custom_security", "play"]


# ── exits written as rules, and what history says they did ─────────────────────

ATR = "def compute(data, params):\n    return ta.atr(data['high'], data['low'], data['close'], 14)"


def _dip(**over):
    p = {"title": "RELIANCE RSI dip", "symbol": "RELIANCE", "interval": "1d",
         "measures": [{"name": "rsi14", "title": "RSI 14", "code": RSI},
                      {"name": "atr14", "title": "ATR 14", "code": ATR}],
         "when": {"all": [{"left": "rsi14", "op": "cross_up", "right": 35}]},
         "do": {"kind": "buy", "size": {"inr": 20000}},
         "manage": {"exit_when": {"any": [{"left": "close", "op": "below", "right": "hi_since - 2 * atr14"},
                                          {"left": "bars_in", "op": "above", "right": 20}]}},
         "contract": {"lifespan": "standing", "authority": "manage"}}
    p.update(over)
    return p


def test_an_exit_rule_reads_the_position_and_history_counts_round_trips(uid) -> None:
    got = plays.tool_play("compile", _dip())
    assert "error" not in got, got
    t = got["history"]["trades"]
    assert t["kind"] == "trades" and t["n"] >= 2, t
    assert set(t["exits"]) <= {"exit rule", "open"}
    assert t["control"]["n"] > 100 and "expectancy" in t["control"]
    assert "curve" not in t and all(x["out"] > x["in"] for x in t["last"])
    assert t["per_trade_inr"] is not None and t["cost_pct"] > 0
    card = [c for c in server._scene.cards if c.get("kind") == "play"][-1]
    assert len(card["history"]["trades"]["curve"]) == card["history"]["trades"]["n"]
    assert card["exits"][0] == ("exits when RELIANCE close below high since entry - 2 × ATR 14 or "
                                "bars held above 20"), card["exits"]
    assert card["exit_rule"][0]["right"] == "high since entry - 2 × ATR 14"


def test_position_names_belong_to_exits_only(uid) -> None:
    bad = plays.tool_play("compile", _dip(when={"all": [{"left": "close", "op": "above", "right": "entry"}]}))
    assert "manage.exit_when" in bad["error"]
    bad = plays.tool_play("compile", _dip(do={"kind": "notify"}, contract={"authority": "notify"}))
    assert "must open one" in bad["error"]
    bad = plays.tool_play("compile", _dip(when={"all": [{"left": "close", "op": "above", "right": "__import__('os')"}]}))
    assert "error" in bad


def test_a_play_with_no_exit_shows_what_followed_against_any_bar(uid) -> None:
    got = plays.tool_play("compile", _dip(manage={}))
    t = got["history"]["trades"]
    assert t["kind"] == "forward" and set(t["forward"]) == {"5", "20"}
    assert t["forward"]["20"]["any_bar"]["n"] > 300


def test_the_exit_rule_closes_the_live_position_from_its_entry_bar(uid) -> None:
    got = plays.tool_play("compile", _dip(manage={"exit_when": {"all": [
        {"left": "bars_in", "op": "above", "right": 2}]}}))
    pid = got["play_id"]
    play = plays._load(pid)
    data, _ = plays.frame(play["spec"])
    series, _ = plays.run_measures(play["spec"], data)
    n = len(data["t"])
    plays._set(pid, accepted=1, fires=1)
    ev = plays._fire(plays._load(pid), data, series, n - 5, late=False)
    assert ev["kind"] == "filled", ev
    assert plays._load(pid)["entry_bar"] == data["t"][n - 5]
    assert plays._maybe_rule_exit(plays._load(pid), data, series, n - 3) is None    # 2 bars in
    ex = plays._maybe_rule_exit(plays._load(pid), data, series, n - 2)
    assert ex["kind"] == "filled" and ex["body"]["ticket"]["side"] == "SELL", ex
    assert "exit rule: bars held above 2" == ex["body"]["reason"]
    assert plays._load(pid)["held_qty"] == 0


# ── guards over the trader's own book, and the review ─────────────────────────

def _closed_trade(uid, grp, pnl, ent, ext, why="stop", play_id=None):
    import daybook
    daybook.init_db()
    with server._users_lock:
        server._users.execute(
            "INSERT INTO day_positions (user_id, play_id, grp, symbol, venue, kind, side, qty, product, "
            "entry_px, entry_ts, state, exit_px, exit_ts, exit_why, costs, pnl, lot) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (uid, play_id, grp, "RELIANCE", "NSE", "equity", "long", 10, "MIS", 100, ent, "closed",
             100, ext, why, 20, pnl, 1))
        server._users.commit()


def test_a_guard_reads_the_book_and_halts_new_entries_but_not_exits(book) -> None:
    got = plays.tool_play("compile", {
        "title": "Stop after two losses", "symbol": "RELIANCE", "interval": "5m",
        "measures": [{"name": "streak", "title": "losses in a row today", "expr": "x",
                      "inputs": {"x": {"book": "loss_streak"}}}],
        "when": {"all": [{"left": "streak", "op": "above", "right": 1}]},
        "do": {"kind": "halt", "close": True}})
    assert "error" not in got, got
    assert got["contract"]["acts"].startswith("Halts on its own")
    pid = got["play_id"]
    play = plays._load(pid)
    data, _ = plays.frame(play["spec"])
    t = data["t"]
    now = int(plays.time.time())
    _closed_trade(1, "a", -500, now - 1800, now - 1500)
    _closed_trade(1, "b", -300, now - 1200, now - 900)
    # the latest bar reads the book as it is now, whatever day the clock's bars end on
    assert plays._book_series(1, "loss_streak", [t[-2], t[-1]], "5m")[-1] == 2.0
    play = plays._load(pid)
    series, _ = plays.run_measures(play["spec"], data)
    ev = plays._fire(play, data, series, len(t) - 1, late=False)
    assert ev["kind"] == "fired" and ev["body"]["halted_until"] > 0
    assert plays._halted(1)["play_id"] == pid
    # an intraday short is refused while halted; lifting it lets entries through
    sp = plays.tool_play("compile", {"symbol": "RELIANCE", "interval": "5m", "do": {"kind": "short"},
                                     "contract": {"lifespan": "now", "authority": "act"}})
    assert sp["did_now"]["kind"] == "rejected" and "halted" in sp["did_now"]["body"]["reason"]
    assert plays.api_post(1, "/halt", {"action": "lift"})[1]["lifted"] is True
    assert plays._halted(1) is None


def test_the_review_cuts_closed_trades_and_flags_trades_after_a_loss(uid) -> None:
    base = 1_790_000_000
    _closed_trade(1, "a", -400, base, base + 600, "stop")
    _closed_trade(1, "b", -250, base + 900, base + 1500, "stop")        # within 30 min of a loss
    _closed_trade(1, "c", 900, base + 7200, base + 9000, "target")
    _closed_trade(1, "d", 300, base + 90000, base + 93000, "time exit at 15:15")
    old = plays.time.time
    plays.time.time = lambda: base + 100000
    try:
        r = plays.tool_play("review")
    finally:
        plays.time.time = old
    assert r["trades"] == 4 and r["net"] == 550 and r["win_rate"] == 0.5
    assert r["after_a_loss"]["n"] == 1 and r["after_a_loss"]["expectancy"] == -250
    assert {x["key"] for x in r["by_exit"]} == {"stop", "target", "time exit"}
    assert r["max_drawdown"] == -650 and r["minutes_held"]["losses"] == 10.0
    assert [c for c in server._scene.cards if c.get("kind") == "review"]


def test_a_guard_stops_the_day_on_the_exit_that_breaks_its_limit(book) -> None:
    got = plays.tool_play("compile", {
        "title": "Down 1,000", "symbol": "RELIANCE", "interval": "1d",
        "measures": [{"name": "pnl", "title": "P&L today", "expr": "x", "inputs": {"x": {"book": "day_pnl"}}}],
        "when": {"all": [{"left": "pnl", "op": "below", "right": -1000}]},
        "do": {"kind": "halt"}})
    assert got["history"] is not None and "every 5m" in [c for c in server._scene.cards if c.get("kind") == "play"][-1]["summary"]
    assert plays._load(got["play_id"])["spec"]["interval"] == "5m"
    pid = plays.tool_play("compile", {"symbol": "RELIANCE", "interval": "5m", "do": {"kind": "short", "size": {"inr": 50000}},
                                      "contract": {"lifespan": "now", "authority": "act"}})["play_id"]
    assert plays._halted(1) is None
    p = daybook.open_positions(1)[0]
    daybook.close(p, p["entry_px"] * 1.05, "stop")          # a 5% loss on ₹50,000 short
    h = plays._halted(1)
    assert h and h["play_id"] == got["play_id"], h


# ── scans, and a play over the shortlist ──────────────────────────────────────

def test_a_scan_runs_the_models_measures_across_a_universe_and_ranks_them(uid) -> None:
    got = plays.tool_measure("scan", scan={
        "universe": {"from": "all", "top_by": "market_cap", "n": 40},
        "measures": [{"name": "rsi14", "title": "RSI 14", "code": RSI},
                     {"name": "ret20", "title": "20-bar return %",
                      "code": "def compute(data, params):\n    c = data['close']\n    return [(c[i] / c[i-20] - 1) * 100 if i >= 20 else None for i in range(len(c))]"}],
        "when": {"all": [{"left": "rsi14", "op": "above", "right": 0}]},
        "rank_by": "ret20", "top": 5})
    assert "error" not in got, got
    assert got["scanned"] == 40 and got["with_data"] >= 30 and got["shown"] == 5
    r = got["rows"]
    assert r[0]["pct"] == 100.0 and r[0]["ret20"] >= r[-1]["ret20"]
    assert got["symbols"] == [x["symbol"] for x in r] and "rsi14" in got["summary"]
    assert [c for c in server._scene.cards if c.get("kind") == "scan"]
    bad = plays.tool_measure("scan", scan={"universe": {"from": "all", "industry": "zzzz"}})
    assert "no stored company" in bad["error"]


def test_a_play_over_a_universe_replays_every_name_and_fires_for_the_one_that_triggered(uid) -> None:
    got = plays.tool_play("compile", {
        "title": "RSI dips across five", "interval": "1d",
        "universe": ["RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK"],
        "measures": [{"name": "rsi14", "title": "RSI 14", "code": RSI}],
        "when": {"all": [{"left": "rsi14", "op": "cross_up", "right": 35}]},
        "do": {"kind": "buy", "size": {"inr": 10000}},
        "manage": {"exit_when": {"all": [{"left": "bars_in", "op": "above", "right": 10}]}},
        "contract": {"authority": "manage"}})
    assert "error" not in got, got
    card = [c for c in server._scene.cards if c.get("kind") == "play"][-1]
    assert card["universe"]["symbols"][:2] == ["RELIANCE", "TCS"] and card["universe"]["max_open"] == 5
    assert card["summary"].startswith("Checks each of 5 names")
    t = got["history"]["trades"]
    assert t["n"] > 20 and t["control"]["n"] > 100, t
    assert "watches 5 names" in " ".join(got["assumed"])
    pid = got["play_id"]
    plays._load_index()
    assert pid in plays._INDEX["TCS"] and pid in plays._INDEX["INFY"]
    # fire for TCS: it is bought and held as TCS, the rest untouched
    play = plays._load(pid)
    plays._set(pid, accepted=1, fires=1)
    mp = plays._member(plays._load(pid), "TCS")
    data, _ = plays.frame(mp["spec"])
    series, _ = plays.run_measures(mp["spec"], data)
    ev = plays._fire(mp, data, series, len(data["t"]) - 3, late=False)
    assert ev["kind"] == "filled" and ev["body"]["ticket"]["symbol"] == "TCS", ev
    play = plays._load(pid)
    assert set(play["held_legs"]) == {"TCS"} and play["marks"]["TCS"]["entry"] == data["t"][-3]
    mp = plays._member(play, "TCS")
    assert plays._holding(mp) and not plays._holding(plays._member(play, "INFY"))
    # an edit on the card rebuilds it with the same names
    got2 = plays.edit_field(1, pid, "size_inr", 12000)
    assert got2["play"]["universe"]["symbols"] == card["universe"]["symbols"], got2
    assert got2["play"]["history"]["trades"]["list"][-1]["symbol"] in card["universe"]["symbols"]
    bad = plays.tool_play("compile", {"universe": {"from": "all"}, "when": {"all": [{"left": "close", "op": "above", "right": 1}]}})
    assert "at most 60" in bad["error"]
