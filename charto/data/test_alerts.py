#!/usr/bin/env python3
"""Logic-level regression tests for Charto's alert watcher.

No market database is mutated. The tests construct bars and Rule objects in
memory, then isolate lifecycle behavior with monkeypatches.

Run from the repository root:
    pytest -q charto/data/test_alerts.py
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time

import pytest

import alerts


def bars(*closes: float) -> list[tuple]:
    return [(1_700_000_000 + i * 60, c, c + 1, c - 1, c, 1_000 + i)
            for i, c in enumerate(closes)]


def rule(when, *, freq="once", all_=True, cstate=None, all_ok=0,
         last_eval=0, last_fired=0, rid=1):
    spec = json.dumps({"when": when, "all": all_})
    return alerts.Rule((rid, 7, "TEST", "1m", spec, freq, "armed", "",
                        int(time.time()), None, json.dumps(cstate or []),
                        all_ok, last_eval, last_fired))


@pytest.mark.parametrize(
    "op,previous,current,fires",
    [("cross_up", 99, 101, True), ("cross_up", 101, 102, False),
     ("cross_down", 101, 99, True), ("cross", 99, 101, True)],
)
def test_crossing_edges(op, previous, current, fires):
    was_ok = (previous >= 100 if op == "cross_up" else
              previous <= 100 if op == "cross_down" else True)
    r = rule([{"left": "close", "op": op, "right": 100}],
             cstate=[{"side": -1 if previous < 100 else 1,
                      "ok": 1 if was_ok else 0}],
             all_ok=1 if was_ok else 0)
    hit = alerts.evaluate(r, alerts.Ctx("TEST", "1m", bars(previous, current), False))
    assert bool(hit) is fires


def test_touch_does_not_erase_crossing_side():
    r = rule([{"left": "close", "op": "cross_up", "right": 100}],
             freq="per_bar", cstate=[{"side": -1, "ok": 0}])
    assert alerts.evaluate(r, alerts.Ctx("TEST", "1m", bars(99, 100), False))
    assert r.cstate[0]["side"] == -1


def test_multi_condition_waits_for_conjunction():
    r = rule([
        {"left": "close", "op": "above", "right": 100},
        {"left": "volume", "op": "above", "right": 1_500},
    ], freq="per_bar", cstate=[{"side": -1, "ok": 0}, {"side": -1, "ok": 0}])
    ctx = alerts.Ctx("TEST", "1m", [(1, 101, 102, 100, 101, 2_000)], False)
    ctx._tick = 0.05
    assert alerts.evaluate(r, ctx)


def test_frequency_bucket_prevents_duplicate_fire():
    r = rule([{"left": "close", "op": "above", "right": 100}],
             freq="per_bar", cstate=[{"side": -1, "ok": 0}])
    ctx = alerts.Ctx("TEST", "1m", bars(101), False)
    ctx._tick = 0.05
    assert alerts.evaluate(r, ctx)
    r.all_ok = 0
    r.cstate = [{"side": -1, "ok": 0}]
    assert alerts.evaluate(r, ctx) is None


@pytest.mark.parametrize("field,value", [
    ("x", "nope"), ("x", float("inf")), ("plus_pct", float("nan")),
    ("within", 0), ("within", 501), ("within", 1.5),
])
def test_validation_refuses_bad_numeric_controls(monkeypatch, field, value):
    monkeypatch.setattr(alerts.ds, "_ensure_symbol", lambda _s: None)
    cond = {"left": "close", "op": "rises_pct", "right": 2, field: value}
    with pytest.raises(alerts.Unspeakable):
        alerts._validate({"symbol": "TEST", "interval": "1m", "when": [cond]}, 7)


def test_52_week_window_requires_52_weeks():
    r = rule([{"left": "close", "op": "above", "right": "52w.high"}])
    ctx = alerts.Ctx("TEST", "1d", bars(*range(100, 300)), False)
    with pytest.raises(alerts.Unspeakable, match="needs 260 daily bars"):
        alerts._resolve("52w.high", ctx, r)


def test_rule_failure_does_not_starve_next_rule(monkeypatch):
    bad = rule([{"left": "close", "op": "above", "right": 100}], rid=1)
    good = rule([{"left": "close", "op": "above", "right": 100}], rid=2)
    monkeypatch.setattr(alerts, "_BY_SYM", {"TEST": [bad, good]})
    monkeypatch.setattr(alerts, "_ctx_for",
                        lambda *_a, **_k: alerts.Ctx("TEST", "1m", bars(101), False))
    monkeypatch.setattr(alerts, "_is_forming", lambda _s: False)
    monkeypatch.setattr(alerts, "_set_state", lambda *_a, **_k: None)
    monkeypatch.setattr(alerts, "_load_index", lambda: None)
    monkeypatch.setattr(alerts, "_persist_eval", lambda _r: None)
    monkeypatch.setattr(alerts, "_fire", lambda *_a, **_k: None)
    seen = []

    def fake_evaluate(r, _ctx):
        seen.append(r.id)
        if r.id == 1:
            raise alerts.Unspeakable("broken")
        return None

    monkeypatch.setattr(alerts, "evaluate", fake_evaluate)
    alerts._run_symbol("TEST", closed_minute=True)
    assert seen == [1, 2]


def test_catch_up_starts_at_first_unseen_bar(monkeypatch):
    rs = bars(*range(100, 140))
    r = rule([{"left": "close", "op": "cross_up", "right": 110}],
             freq="per_bar", cstate=[{"side": -1, "ok": 0}],
             last_eval=rs[5][0])
    monkeypatch.setattr(alerts, "watched_symbols", lambda: ["TEST"])
    monkeypatch.setattr(alerts, "_BY_SYM", {"TEST": [r]})
    monkeypatch.setattr(alerts, "_rows_for", lambda *_a: rs)
    monkeypatch.setattr(alerts, "_persist_eval", lambda _r: None)
    monkeypatch.setattr(alerts, "_fire", lambda *_a, **_k: None)
    scanned = []

    def fake_evaluate(_r, ctx, late=False):
        scanned.append(ctx.rows[-1][0])
        return None

    monkeypatch.setattr(alerts, "evaluate", fake_evaluate)
    alerts.catch_up()
    assert scanned[0] == rs[6][0]


def test_once_rule_can_be_rearmed_after_fire():
    r = rule([{"left": "close", "op": "cross_up", "right": 100}],
             last_fired=123)
    assert alerts.evaluate(r, alerts.Ctx("TEST", "1m", bars(99, 101), False)) is None
    r.last_fired_bkt = 0
    r.cstate = [{"side": -1, "ok": 0}]
    assert alerts.evaluate(r, alerts.Ctx("TEST", "1m", bars(99, 101), False))


def test_api_rearm_clears_fire_bucket_and_old_pause_reason(monkeypatch):
    con = sqlite3.connect(":memory:", check_same_thread=False)
    con.execute("CREATE TABLE users (id INTEGER PRIMARY KEY)")
    con.execute("INSERT INTO users VALUES (7)")
    monkeypatch.setattr(alerts.ds, "_users", con)
    monkeypatch.setattr(alerts.ds, "_users_lock", threading.Lock())
    alerts._init_db()
    spec = json.dumps({"when": [{"left": "close", "op": "cross_up",
                                  "right": 100}], "all": True})
    con.execute(
        "INSERT INTO alerts (user_id,symbol,interval,spec,freq,state,note,created,"
        "cstate,all_ok,last_eval_ts,last_fired_bkt) VALUES "
        "(7,'TEST','1m',?,'once','fired','mine [paused: old feed]',1,'[]',0,5,99)",
        (spec,))
    con.commit()
    seeded = []
    monkeypatch.setattr(alerts, "_seed", lambda r: seeded.append(r.last_fired_bkt))
    monkeypatch.setattr(alerts, "_load_index", lambda: None)
    monkeypatch.setattr(alerts, "ensure_feed", lambda _s: {})
    code, out = alerts.api_patch(7, 1, {"state": "armed"})
    assert code == 200
    assert seeded == [0]
    assert out["alert"]["state"] == "armed"
    assert out["alert"]["note"] == "mine"


def test_feed_health_distinguishes_disconnected_from_subscribed(monkeypatch):
    monkeypatch.setattr(alerts.ds, "_live_status", lambda: {
        "kite": {"connected": False, "symbols": ["TEST"],
                 "last_tick_age_s": 900, "error": "token expired"},
    })
    monkeypatch.setattr(alerts, "watched_symbols", lambda: ["TEST"])
    monkeypatch.setattr(alerts, "_has_minutes", lambda _s: True)
    got = alerts.feed_health("TEST")["symbol"]
    assert got["subscribed"] is True
    assert got["streaming"] is False
    assert got["status"] == "disconnected"


def test_advertised_average_window_fits_loaded_rows():
    assert alerts._ROWS_LIMIT >= 501
