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
