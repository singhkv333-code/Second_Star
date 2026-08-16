"""Journal math, ownership and flexible-field contract."""
import importlib.util
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path


class JournalTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        path = Path(cls.tmp.name) / "users.db"
        db = sqlite3.connect(path)
        db.execute("CREATE TABLE users(id INTEGER PRIMARY KEY)")
        db.executemany("INSERT INTO users(id) VALUES(?)", [(1,), (2,)])
        db.commit(); db.close()
        os.environ["CHARTO_USERS_DB"] = str(path)
        spec = importlib.util.spec_from_file_location("journal_test_module",
            Path(__file__).with_name("journal.py"))
        cls.j = importlib.util.module_from_spec(spec); spec.loader.exec_module(cls.j)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_long_math_and_flexible_review(self):
        code, out = self.j.api_create(1, {
            "symbol":"reliance", "side":"long", "opened_at":100,
            "closed_at":200, "quantity":10, "entry_price":100,
            "exit_price":112, "fees":5, "initial_risk":50,
            "plan":{"my-own-rule":"wait for acceptance"},
            "review":{"adherence":True, "custom-score":"A"},
            "tags":["breakout"]})
        self.assertEqual(code, 201)
        self.assertEqual(out["trade"]["net_pnl"], 115)
        self.assertEqual(out["trade"]["r_multiple"], 2.3)
        self.assertEqual(out["trade"]["symbol"], "RELIANCE")
        self.assertEqual(out["trade"]["review"]["custom-score"], "A")

    def test_short_math_and_ownership(self):
        _, out = self.j.api_create(1, {"symbol":"TCS", "side":"short",
            "opened_at":100, "closed_at":200, "quantity":2,
            "entry_price":500, "exit_price":470, "fees":4})
        trade = out["trade"]
        self.assertEqual(trade["net_pnl"], 56)
        self.assertEqual(self.j.api_get(2, trade["id"])[0], 404)
        self.assertEqual(self.j.api_patch(2, trade["id"], {"tags":["x"]})[0], 404)

    def test_open_trade_does_not_invent_result(self):
        _, out = self.j.api_create(1, {"symbol":"INFY", "side":"long",
            "opened_at":100, "quantity":1, "entry_price":1500})
        self.assertIsNone(out["trade"]["net_pnl"])
        self.assertIsNone(out["trade"]["r_multiple"])

    def test_overview_metrics_use_the_correct_eligible_trades(self):
        trades = [
            {"net_pnl": 200, "r_multiple": 2, "review": {"adherence": True}, "reviewed": True},
            {"net_pnl": -100, "r_multiple": -1, "review": {"adherence": False}, "reviewed": True},
            {"net_pnl": 0, "r_multiple": 0, "review": {}, "reviewed": False},
            {"net_pnl": None, "r_multiple": None, "review": {"emotion": "calm"}, "reviewed": True},
        ]
        out = self.j.overview(trades)
        self.assertEqual(out["count"], 4)
        self.assertEqual(out["closed"], 3)
        self.assertEqual(out["net_pnl"], 100)
        self.assertEqual(out["win_rate"], 33.3)
        self.assertEqual(out["profit_factor"], 2)
        self.assertEqual(out["expectancy_r"], 0.33)
        self.assertEqual(out["adherence"], 50)
        self.assertEqual(out["reviewed"], 3)

    def test_chat_origin_creates_revision(self):
        _, out = self.j.api_create(1, {"symbol":"HDFCBANK", "side":"long",
            "opened_at":100, "quantity":1, "entry_price":100})
        tid = out["trade"]["id"]
        code, changed = self.j.api_patch(1, tid, {"review":{"lesson":"Wait"}, "origin":"chat"})
        self.assertEqual(code, 200)
        self.assertEqual(changed["trade"]["review"]["lesson"], "Wait")
        row = self.j._db.execute("SELECT origin FROM journal_revisions WHERE trade_id=?", (tid,)).fetchone()
        self.assertEqual(row[0], "chat")


if __name__ == "__main__":
    unittest.main()
