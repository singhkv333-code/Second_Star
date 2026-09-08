"""Tests for the plan store — registration, the arithmetic, and activation.

The thing worth proving here is the SEPARATION. Registration must move
nothing: a plan can be built by a chat turn without that turn having spent
anything, and the only function that spends is the one a user presses. Half
these tests exist to hold that line, because it is the whole safety argument
for letting the builder register without asking permission first.

The other half are about the arithmetic. A weight is not a share count, and
the conversion happens at ACTIVATION against the live mark — so the tests
pin down that a plan registered at one price fills at the price when pressed,
and that a leg which cannot be priced is refused by name rather than silently
sized to zero.

Run: python3 test_plans.py   (under a venv that can import dataserver)
"""
from __future__ import annotations

import json
import sys
import time
import unittest
from decimal import Decimal
from unittest import mock

import dataserver as ds
import paper
import plans
import strategies


def _mk_user(email: str) -> int:
    """A real row in the real users table — plans are foreign-keyed to it."""
    with ds._users_lock:
        cur = ds._users.execute(
            "INSERT INTO users (email, name, pw_hash, pw_salt, created) "
            "VALUES (?,?,?,?,?)",
            (email, "test", b"x", b"y", int(time.time())))
        ds._users.commit()
        return cur.lastrowid


class PlanTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls) -> None:
        plans.init_db()
        cls.uid = _mk_user(f"plans_{int(time.time() * 1000)}@test.local")
        paper.get_or_create_account(cls.uid)

    # ── what a plan may and may not be ────────────────────────────────

    def test_a_plan_needs_legs(self):
        with self.assertRaises(plans.Unbuildable):
            plans.parse({"name": "empty", "legs": []})

    def test_a_leg_is_sized_exactly_one_way(self):
        """quantity AND weight on one leg is a contradiction, not a default."""
        with self.assertRaises(plans.Unbuildable) as ctx:
            plans.parse({"legs": [{"symbol": "INFY", "quantity": 5,
                                   "weight_pct": 50, "why": "x"}]})
        self.assertIn("ONE way", str(ctx.exception))

    def test_a_leg_with_no_size_is_refused(self):
        with self.assertRaises(plans.Unbuildable) as ctx:
            plans.parse({"legs": [{"symbol": "INFY", "why": "x"}]})
        self.assertIn("no size", str(ctx.exception))

    def test_weights_must_add_up(self):
        """Ten legs at 20% is 200% of the capital, and it is caught before a
        row exists rather than deploying twice the money the user named."""
        legs = [{"symbol": f"S{i}", "weight_pct": 20, "why": "x"}
                for i in range(10)]
        with self.assertRaises(plans.Unbuildable) as ctx:
            plans.parse({"legs": legs, "capital_inr": 100000})
        self.assertIn("200.0%", str(ctx.exception))

    def test_equal_weights_that_do_not_round_to_100_are_fine(self):
        """Three names equal-weighted is 33.3 x 3 = 99.9, and seven is 99.96.
        The band is there to catch a doubled or dropped leg, not to make the
        model pick weights that divide evenly — rejecting an honest rounding
        would force it into uglier numbers for no gain."""
        for n in (3, 6, 7, 9):
            w = round(100.0 / n, 1)
            legs = [{"symbol": f"S{i}", "weight_pct": w, "why": "x"}
                    for i in range(n)]
            out = plans.parse({"legs": legs, "capital_inr": 100000})
            self.assertEqual(len(out["legs"]), n)

    def test_a_dropped_leg_is_caught(self):
        """Nine legs at 10% is a basket that lost one on the way out, and the
        90% total is the only evidence of it."""
        legs = [{"symbol": f"S{i}", "weight_pct": 10, "why": "x"}
                for i in range(9)]
        with self.assertRaises(plans.Unbuildable) as ctx:
            plans.parse({"legs": legs, "capital_inr": 100000})
        self.assertIn("90.0%", str(ctx.exception))

    def test_weights_need_capital(self):
        with self.assertRaises(plans.Unbuildable) as ctx:
            plans.parse({"legs": [{"symbol": "INFY", "weight_pct": 100,
                                   "why": "x"}]})
        self.assertIn("capital_inr", str(ctx.exception))

    def test_a_short_leg_is_refused_by_name(self):
        with self.assertRaises(plans.Unbuildable) as ctx:
            plans.parse({"legs": [{"symbol": "INFY", "side": "sell",
                                   "quantity": 5, "why": "x"}]})
        self.assertIn("long-only", str(ctx.exception))
        self.assertIn("INFY", str(ctx.exception))

    def test_the_same_name_twice_is_refused(self):
        with self.assertRaises(plans.Unbuildable) as ctx:
            plans.parse({"legs": [
                {"symbol": "INFY", "quantity": 1, "why": "a"},
                {"symbol": "INFY", "quantity": 2, "why": "b"}]})
        self.assertIn("INFY", str(ctx.exception))

    # ── registration moves nothing ────────────────────────────────────

    def test_registering_spends_nothing(self):
        """THE safety property. A registered plan is a manifest; the book is
        untouched until someone presses the button."""
        before = paper.api_summary(self.uid)[1]
        plan = plans.register(self.uid, {
            "name": "untouched", "capital_inr": 50000,
            "legs": [{"symbol": "INFY", "weight_pct": 100, "why": "test"}]})
        after = paper.api_summary(self.uid)[1]
        self.assertEqual(plan["state"], "draft")
        self.assertEqual(before.get("cash"), after.get("cash"))
        self.assertEqual(plan["legs"][0]["state"], "pending")
        # And no quantity is claimed yet, because none has been computed.
        self.assertIsNone(plan["legs"][0]["quantity"])

    # ── the arithmetic, at activation ─────────────────────────────────

    def test_a_weight_becomes_shares_at_the_mark(self):
        """₹50,000 at 100% of a ₹1,000 mark is 50 shares — computed here, not
        by the model, and computed when the button is pressed."""
        plan = plans.register(self.uid, {
            "name": "weighted", "capital_inr": 50000,
            "legs": [{"symbol": "TESTWT", "weight_pct": 100, "why": "test"}]})
        with mock.patch.object(paper, "mark_price",
                               return_value=Decimal("1000")):
            code, out = plans.activate(self.uid, plan["id"])
        self.assertEqual(code, 200)
        leg = out["legs"][0]
        self.assertEqual(leg["state"], "filled")
        self.assertEqual(float(leg["quantity"]), 50.0)
        self.assertEqual(out["state"], "active")

    def test_a_notional_leg_rounds_down(self):
        """₹1,000 of a ₹300 share is three shares, never 3.33 and never four:
        the book holds whole shares and rounding up would spend money the
        plan did not have."""
        plan = plans.register(self.uid, {
            "name": "notional",
            "legs": [{"symbol": "TESTNOT", "notional_inr": 1000, "why": "t"}]})
        with mock.patch.object(paper, "mark_price",
                               return_value=Decimal("300")):
            code, out = plans.activate(self.uid, plan["id"])
        self.assertEqual(float(out["legs"][0]["quantity"]), 3.0)

    def test_a_leg_too_small_to_buy_one_share_is_refused_by_name(self):
        plan = plans.register(self.uid, {
            "name": "tiny",
            "legs": [{"symbol": "TESTTINY", "notional_inr": 10, "why": "t"}]})
        with mock.patch.object(paper, "mark_price",
                               return_value=Decimal("5000")):
            code, out = plans.activate(self.uid, plan["id"])
        self.assertEqual(out["legs"][0]["state"], "rejected")
        self.assertIn("TESTTINY", out["result"]["refused"][0]["reason"])
        # Nothing succeeded, so the plan did NOT become active — saying it had
        # would be the "saved the strategy" lie in a new place.
        self.assertEqual(out["state"], "draft")

    def test_an_unpriceable_leg_does_not_stop_the_others(self):
        """Partial success is the normal outcome for a basket, and it is
        reported as such: the filled legs fill, the refused one is named."""
        plan = plans.register(self.uid, {
            "name": "partial", "capital_inr": 100000,
            "legs": [{"symbol": "TESTOK", "weight_pct": 50, "why": "a"},
                     {"symbol": "TESTDEAD", "weight_pct": 50, "why": "b"}]})

        def _mark(sym):
            return Decimal("500") if sym == "TESTOK" else None

        with mock.patch.object(paper, "mark_price", side_effect=_mark):
            code, out = plans.activate(self.uid, plan["id"])
        states = {l["symbol"]: l["state"] for l in out["legs"]}
        self.assertEqual(states["TESTOK"], "filled")
        self.assertEqual(states["TESTDEAD"], "rejected")
        self.assertEqual(out["state"], "active")     # seven of ten still ran
        self.assertIn("TESTDEAD", out["last_error"])

    def test_activating_twice_is_refused(self):
        """Idempotence at the door. Two presses must not buy the basket
        twice, and the second press is a 409 rather than a silent no-op — the
        user pressed for a reason and deserves to know it already ran."""
        plan = plans.register(self.uid, {
            "name": "once",
            "legs": [{"symbol": "TESTONCE", "quantity": 1, "why": "t"}]})
        with mock.patch.object(paper, "mark_price",
                               return_value=Decimal("100")):
            self.assertEqual(plans.activate(self.uid, plan["id"])[0], 200)
            code, out = plans.activate(self.uid, plan["id"])
        self.assertEqual(code, 409)

    # ── conditional legs ──────────────────────────────────────────────

    def test_a_conditional_leg_arms_instead_of_filling(self):
        """A leg with an entry tree becomes a `strategies` row carrying the
        plan's id, and joins the tick runtime — it does NOT buy at activation.
        The tree it arms is the tree it was registered with, byte for byte."""
        tree = {"type": "condition", "left": {"type": "price", "field": "close"},
                "op": "lt", "right": {"type": "constant", "value": 1}}
        plan = plans.register(self.uid, {
            "name": "watcher",
            "legs": [{"symbol": "TESTCOND", "quantity": 3, "why": "t",
                      "entry": tree, "interval": "1d"}]})
        self.assertTrue(plan["legs"][0]["conditional"])
        with mock.patch.object(paper, "mark_price",
                               return_value=Decimal("100")):
            code, out = plans.activate(self.uid, plan["id"])
        leg = out["legs"][0]
        self.assertEqual(leg["state"], "armed")
        sid = leg["strategy_id"]
        self.assertTrue(sid)
        with ds._users_lock:
            row = ds._users.execute(
                "SELECT state, plan_id, entry, quantity FROM strategies "
                "WHERE id=?", (sid,)).fetchone()
        self.assertEqual(row[0], "armed")
        self.assertEqual(row[1], plan["id"])
        self.assertEqual(json.loads(row[2]), tree)
        self.assertEqual(int(row[3]), 3)

    def test_retiring_a_plan_pauses_the_rules_it_armed(self):
        """A plan the user put away must not keep trading. Positions it
        already opened stay — they are what happened — but nothing new fires."""
        tree = {"type": "condition", "left": {"type": "price", "field": "close"},
                "op": "lt", "right": {"type": "constant", "value": 1}}
        plan = plans.register(self.uid, {
            "name": "retire me",
            "legs": [{"symbol": "TESTRET", "quantity": 1, "why": "t",
                      "entry": tree}]})
        with mock.patch.object(paper, "mark_price",
                               return_value=Decimal("100")):
            _, out = plans.activate(self.uid, plan["id"])
        sid = out["legs"][0]["strategy_id"]
        code, out = plans.api_delete(self.uid, plan["id"])
        self.assertEqual(out["state"], "retired")
        with ds._users_lock:
            state = ds._users.execute(
                "SELECT state FROM strategies WHERE id=?", (sid,)).fetchone()[0]
        self.assertEqual(state, "paused")

    # ── identity ──────────────────────────────────────────────────────

    def test_a_plan_belongs_to_its_account(self):
        """Another user cannot read, activate or retire it — the id is not a
        capability."""
        other = _mk_user(f"other_{int(time.time() * 1000)}@test.local")
        plan = plans.register(self.uid, {
            "name": "mine",
            "legs": [{"symbol": "TESTOWN", "quantity": 1, "why": "t"}]})
        self.assertEqual(plans.get(other, plan["id"])[0], 404)
        self.assertEqual(plans.activate(other, plan["id"])[0], 404)
        self.assertEqual(plans.api_delete(other, plan["id"])[0], 404)

    def test_a_signed_out_caller_is_told_so(self):
        out = plans.tool_register_plan(
            user_id=0, legs=[{"symbol": "INFY", "quantity": 1, "why": "t"}])
        self.assertEqual(out["error"], "sign_in_required")


if __name__ == "__main__":
    unittest.main(verbosity=2, exit=False)
