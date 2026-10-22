"""The laws every play order obeys, checked over thousands of generated books
rather than a handful of hand-picked ones, and the effect a play reports at
compile.

The laws are effects.laws: a play trades only the names it names, never sells
more than is held, whole shares and whole lots, nothing above its order limit,
and a rebalance moves each name toward its target. Beside them, the rebalance
must not make the book further from its targets, must be funded, and run a
second time on its own result must have nothing left to do."""
from __future__ import annotations

import os
from unittest import mock

from hypothesis import HealthCheck, given, settings, strategies as st

import daybook
import effects
import paper
import plays
import policy
from test_plays import RSI, _equal, book, uid  # noqa: F401  (fixtures)

NAMES = ["AAA", "BBB", "CCC", "DDD", "EEE"]

# 400 books per law on every run; HYPOTHESIS_PROFILE=deep tries 10,000
settings.register_profile("default", max_examples=400, deadline=None,
                          suppress_health_check=[HealthCheck.too_slow])
settings.register_profile("deep", max_examples=10_000, deadline=None,
                          suppress_health_check=[HealthCheck.too_slow])
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "default"))


class _Book:
    """The two things the ticket code asks of the paper book."""

    def __init__(self, held: dict, px: dict):
        self.held, self.px = dict(held), dict(px)

    def api_holdings(self, uid):
        return 200, [{"symbol": s, "quantity": q} for s, q in self.held.items() if q]

    def mark_price(self, sym):
        return self.px.get(sym)


def _patched(fake):
    return mock.patch.multiple(policy, _paper=fake), mock.patch.object(effects, "_paper", fake), \
        mock.patch.object(plays, "_paper", fake)


def _rebalance_play(targets: dict, cash: float, names: list[str]) -> dict:
    return {"id": 0, "user_id": 1, "spent_inr": 0.0, "held_legs": {},
            "spec": {"symbol": names[0], "do": {"kind": "rebalance", "of": "portfolio", "names": names,
                                               "targets": targets, "size": {"inr": cash} if cash else {}},
                     "mandate": {"max_order_inr": 1e12}, "contract": {"discretion": "fixed"}}}


def _distance(held: dict, px: dict, cash: float, targets: dict) -> float:
    total = sum(held.get(s, 0) * px[s] for s in targets) + cash
    return sum(abs(held.get(s, 0) * px[s] / total - w) for s, w in targets.items()) if total else 0.0


@given(held=st.dictionaries(st.sampled_from(NAMES), st.integers(0, 400), max_size=5),
       px=st.fixed_dictionaries({s: st.floats(5, 150_000, allow_nan=False) for s in NAMES}),
       raw=st.dictionaries(st.sampled_from(NAMES[:4]), st.integers(0, 10), min_size=1, max_size=4),
       cash=st.sampled_from([0.0, 0.0, 5_000.0, 1_00_000.0]))
def test_a_rebalance_obeys_the_laws_and_settles(held, px, raw, cash) -> None:
    tot = sum(raw.values()) or len(raw)
    targets = {s: (w or (0 if sum(raw.values()) else 1)) / tot for s, w in raw.items()}
    play = _rebalance_play(targets, cash, sorted(targets))
    fake = _Book(held, px)
    a, b, c = _patched(fake)
    with a, b, c:
        t, why = policy.ticket(play, targets)
        if not t:
            assert why                                   # a refusal always says why
            return
        assert effects.laws(play, t, fake.held) == [], (effects.laws(play, t, fake.held), t)
        floor = max(policy.MIN_TRADE_INR, t["total_inr"] * 0.005)
        assert t["buys_inr"] <= t["sells_inr"] + cash + floor + 1e-6, t
        net = 0.0
        after = dict(fake.held)
        for lg in t["legs"]:
            sign = 1 if lg["side"] == "BUY" else -1
            after[lg["symbol"]] = after.get(lg["symbol"], 0) + sign * lg["qty"]
            net += sign * lg["qty"] * lg["price"]
        assert all(s in targets for s in after if after[s] != fake.held.get(s, 0)), "traded a name it was not given"
        assert _distance(after, px, cash - net, targets) <= _distance(fake.held, px, cash, targets) + 1e-9
        # run again on its own result: nothing left that it would trade
        fake.held = {s: q for s, q in after.items() if q}
        again, _ = policy.ticket({**play, "spent_inr": net}, targets)
        assert again is None or not again["legs"], again["legs"]


def _sell_play(size: dict, of: str, held_qty: float) -> dict:
    return {"id": 0, "user_id": 1, "spent_inr": 0.0, "held_legs": {},
            "held_sym": "AAA" if held_qty else "", "held_qty": held_qty,
            "spec": {"symbol": "AAA", "manage": {},
                     "do": {"kind": "sell", "symbol": "AAA", "size": size, "of": of, "product": "CNC"},
                     "mandate": {"max_order_inr": 1e12, "capital_inr": 1e12}, "contract": {"discretion": "fixed"}}}


@given(have=st.integers(0, 500), mine=st.integers(0, 600), of=st.sampled_from(["portfolio", "self"]),
       size=st.one_of(st.builds(lambda p: {"pct": p}, st.floats(0.01, 100)),
                      st.builds(lambda q: {"qty": q}, st.integers(1, 800)),
                      st.builds(lambda i: {"inr": i}, st.floats(1, 5_00_000)),
                      st.just({"all": True})),
       price=st.floats(5, 50_000))
def test_a_sell_never_sells_more_than_is_held(have, mine, of, size, price) -> None:
    # `mine` is what the play thinks it bought; the book may hold less (a stop
    # already filled, or the trader sold by hand)
    fake = _Book({"AAA": have}, {"AAA": price})
    play = _sell_play(size, of, mine)
    a, b, c = _patched(fake)
    with a, b, c:
        t, why = plays._ticket(play, price)
    if not t:
        assert why
        return
    assert effects.laws(play, t, fake.held) == [], effects.laws(play, t, fake.held)
    assert t["qty"] <= have
    if size.get("pct"):
        base = min(mine, have) if mine else have
        assert t["qty"] <= base * size["pct"] / 100 + 1e-9


def test_a_law_refuses_a_name_the_play_never_named_and_an_oversell() -> None:
    play = _rebalance_play({"AAA": 0.5, "BBB": 0.5}, 0, ["AAA", "BBB"])
    t = {"rebalance": True, "targets": {"AAA": 0.5, "BBB": 0.5}, "before": {"AAA": 0.2, "CCC": 0.8},
         "legs": [{"symbol": "CCC", "side": "SELL", "qty": 3, "price": 100},
                  {"symbol": "AAA", "side": "SELL", "qty": 9, "price": 100}]}
    bad = effects.laws(play, t, {"AAA": 5, "CCC": 3})
    assert any("CCC is outside the names" in b for b in bad), bad
    assert any("sells 9 AAA and the paper portfolio holds 5" in b for b in bad), bad
    assert any("sells AAA though it is under its target" in b for b in bad), bad


def test_the_effect_names_fields_the_order_never_read() -> None:
    do = effects.Seen({"kind": "buy", "symbol": "AAA", "size": {"inr": 5000}, "window": "10:00"})
    do["kind"], do.get("symbol"), do.get("size")
    assert effects.unread(do) == ["window"]
    child = effects.derive(do, kind="long")
    child.get("window")
    assert effects.unread(do) == []                    # a derived copy's reads count


def test_compile_reports_what_it_would_do_and_what_it_leaves_alone(uid) -> None:  # noqa: F811
    paper.place_order(uid, "RELIANCE", "BUY", 10, source="test")
    paper.place_order(uid, "INFY", "BUY", 4, source="test")
    got = plays.tool_play("compile", {"title": "Sell half", "symbol": "RELIANCE", "interval": "1d",
                                      "when": {"all": [{"left": "rsi14", "op": "above", "right": 70}]},
                                      "measures": [{"name": "rsi14", "title": "RSI 14", "code": RSI}],
                                      "do": {"kind": "sell", "size": {"pct": 50}}}, arm=False)
    assert "if_it_fired_now" in got, got
    eff = got["if_it_fired_now"]
    assert eff["changes"] == {"RELIANCE": [10.0, 5.0]} and eff["untouched"] == ["INFY"], eff
    rb = plays.tool_play("compile", _equal(contract={"lifespan": "policy", "authority": "propose"}), arm=False)
    assert "INFY" in rb["if_it_fired_now"]["untouched"], rb["if_it_fired_now"]


def test_a_ticket_that_breaks_a_law_is_refused_not_placed(uid) -> None:  # noqa: F811
    paper.place_order(uid, "RELIANCE", "BUY", 10, source="test")
    paper.place_order(uid, "INFY", "BUY", 4, source="test")
    got = plays.tool_play("compile", _equal(contract={"lifespan": "policy", "authority": "propose"}))
    play = plays._load(got["play_id"])
    stray = {"symbol": "INFY", "side": "SELL", "qty": 4, "price": 1000.0}
    ev = plays._place(play, stray, 0, {})
    assert ev["kind"] == "rejected" and "outside the names" in ev["body"]["reason"], ev
    held = {r["symbol"]: r["quantity"] for r in paper.api_holdings(uid)[1]}
    assert held["INFY"] == 4


def test_a_confirmed_sell_sells_only_what_is_still_held(uid) -> None:  # noqa: F811
    paper.place_order(uid, "RELIANCE", "BUY", 10, source="test")
    got = plays.tool_play("compile", {"title": "Sell all", "symbol": "RELIANCE", "interval": "1d",
                                      "do": {"kind": "sell", "size": {"all": True}},
                                      "contract": {"lifespan": "now", "authority": "propose"}})
    ev = got["did_now"]
    assert ev["kind"] == "proposed" and ev["body"]["ticket"]["qty"] == 10, ev
    paper.place_order(uid, "RELIANCE", "SELL", 6, source="test")          # sold by hand meanwhile
    eid = [e for e in plays.events(uid) if e["kind"] == "proposed"][0]["id"]
    res = plays.resolve_event(uid, eid, "confirm")
    assert res["result"]["kind"] == "filled" and res["result"]["body"]["ticket"]["qty"] == 4, res
    assert not [r for r in paper.api_holdings(uid)[1] if r["symbol"] == "RELIANCE"]


def test_a_cover_closes_part_of_the_traders_short_from_another_play(book) -> None:  # noqa: F811
    quotes = book
    sh = plays.tool_play("compile", {"title": "Short", "symbol": "RELIANCE", "interval": "5m",
                                     "do": {"kind": "short", "size": {"qty": 40}},
                                     "mandate": {"capital_inr": 1_00_000},
                                     "contract": {"lifespan": "now", "authority": "act"}})
    assert sh["did_now"]["kind"] == "filled", sh["did_now"]
    quotes.update({"NSE:RELIANCE": 1300.0})
    cv = plays.tool_play("compile", {"title": "Cover half", "symbol": "RELIANCE", "interval": "5m",
                                     "do": {"kind": "cover", "size": {"pct": 50}},
                                     "contract": {"lifespan": "now", "authority": "act"}})
    assert cv["did_now"]["kind"] == "exited", cv["did_now"]
    open_ = [p for p in daybook.open_positions(1) if p["symbol"] == "RELIANCE"]
    assert [p["qty"] for p in open_] == [20], open_
    assert "50% of your RELIANCE short" in plays.card(plays.get(1, cv["play_id"]))["summary"]
