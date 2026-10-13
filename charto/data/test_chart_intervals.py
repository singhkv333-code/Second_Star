"""Interval aggregation regressions using an in-memory minute store only."""
from __future__ import annotations

import ast
import math
from pathlib import Path
import sqlite3
import threading
import unittest


NEW_INTERVALS = {"2m": 2, "3m": 3, "10m": 10, "20m": 20, "45m": 45,
                 "2h": 120, "3h": 180, "4h": 240}
NSE = (555, 19800)
UTC = (0, 0)
MCX = (540, 19800)


class ChartIntervalTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        for table in ("bars", "bars_15m"):
            self.db.execute(f"CREATE TABLE {table} (symbol TEXT, ts INTEGER, "
                            "o REAL, h REAL, l REAL, c REAL, v REAL)")
        tree = ast.parse(Path(__file__).with_name("dataserver.py").read_text())
        functions = {"_ist_day", "_bucket_stamp", "_resample_intraday", "_fold_daily",
                     "_intraday_rows", "get_bars", "_merge_form_intraday", "_live_snapshot"}
        nodes = [node for node in tree.body
                 if (isinstance(node, ast.FunctionDef) and node.name in functions)
                 or (isinstance(node, ast.Assign) and any(
                     isinstance(target, ast.Name) and target.id == "INTRADAY_MIN"
                     for target in node.targets))]
        self.cutoff = None
        self.form = None
        self.ns = dict(_con=self.db, _live_writer=self.db, sqlite3=sqlite3,
                       IST_OFF=19800, NSE_SESSION=NSE, _WRITER_LOCK=threading.Lock(),
                       session_for=lambda symbol: {"BTC": UTC, "GOLD": MCX}.get(symbol, NSE),
                       _rollup_for_symbol=lambda symbol: self.cutoff,
                       _live_view=lambda symbol: (self.form, None) if self.form else None,
                       _intra_cached=lambda key: None, _intra_store=lambda *args: None)
        exec(compile(ast.Module(body=nodes, type_ignores=[]), "interval-fixture", "exec"), self.ns)
        self.start = 20000 * 86400 + 555 * 60 - 19800
        self.rows = self.minutes(self.start, 375) + self.minutes(self.start + 86400, 375)
        self.insert("bars", "TEST", self.rows)

    def tearDown(self):
        self.db.close()

    @staticmethod
    def minutes(start, count):
        return [(start + i * 60, 100 + i, 103 + i, 98 + i, 101 + i, i + 1)
                for i in range(count)]

    def insert(self, table, symbol, rows):
        self.db.executemany(f"INSERT INTO {table} VALUES (?,?,?,?,?,?,?)",
                            [(symbol, *row) for row in rows])

    def bars(self, interval, to=None, limit=2000):
        return self.ns["get_bars"]("TEST", interval, to, limit)

    def test_ohlcv_and_session_reset_for_every_new_interval(self):
        for interval, minutes in NEW_INTERVALS.items():
            with self.subTest(interval=interval):
                data = self.bars(interval)
                bars = data["bars"]
                per_day = math.ceil(375 / minutes)
                self.assertEqual(len(bars), 2 * per_day)
                self.assertEqual(bars[0], dict(t=self.start, o=100, h=102 + minutes,
                                             l=98, c=100 + minutes,
                                             v=minutes * (minutes + 1) / 2))
                self.assertEqual(bars[1]["t"], self.start + minutes * 60)
                self.assertEqual(bars[per_day]["t"], self.start + 86400)
                self.assertEqual(sum(bar["v"] for bar in bars), 2 * 375 * 376 / 2)

    def test_partial_end_of_session_and_missing_minutes_are_not_filled(self):
        # A 4h NSE day ends with a 135-minute candle, never a cross-day candle.
        bars = self.bars("4h")["bars"]
        self.assertEqual(bars[1]["t"], self.start + 240 * 60)
        self.assertEqual(bars[1]["c"], 475)
        self.assertEqual(bars[1]["v"], sum(range(241, 376)))
        rows = [self.rows[0], self.rows[2], (self.start + 60, 0, 0, 0, 0, 0)]
        rows.sort()
        folded = self.ns["_resample_intraday"](rows, 10, NSE)
        self.assertEqual(folded, [[self.start, 100, 105, 98, 103, 4]])

    def test_crypto_midnight_and_mcx_session_anchors(self):
        day = 20000 * 86400
        for session, start in ((UTC, day), (MCX, day + 540 * 60 - 19800)):
            for interval, minutes in NEW_INTERVALS.items():
                with self.subTest(session=session, interval=interval):
                    rows = self.minutes(start, minutes + 1) + self.minutes(start + 86400, 1)
                    folded = self.ns["_resample_intraday"](rows, minutes, session)
                    self.assertEqual([bar[0] for bar in folded],
                                     [start, start + minutes * 60, start + 86400])

    def test_fifteen_minute_rollup_and_raw_seam_preserve_exact_candles(self):
        expected = {iv: self.bars(iv)["bars"] for iv, minutes in NEW_INTERVALS.items()
                    if minutes % 15 == 0}
        self.cutoff = self.start + 86400 + 180 * 60
        prefix = [row for row in self.rows if row[0] < self.cutoff]
        rolled = self.ns["_resample_intraday"](prefix, 15, NSE)
        self.insert("bars_15m", "TEST", rolled)
        queries = []
        self.db.set_trace_callback(queries.append)
        for interval, bars in expected.items():
            with self.subTest(interval=interval):
                self.assertEqual(self.bars(interval)["bars"], bars)
        self.assertTrue(any("SELECT ts,o,h,l,c,v FROM bars_15m" in query for query in queries))

    def test_live_candle_and_historical_candle_have_identical_stamp_and_values(self):
        self.db.execute("DELETE FROM bars WHERE symbol='TEST'")
        closed = self.minutes(self.start, 271)
        self.insert("bars", "TEST", closed)
        self.form = list(self.minutes(self.start, 272)[-1])
        snapshot = self.ns["_live_snapshot"]("TEST", self.form)
        for interval in NEW_INTERVALS:
            with self.subTest(interval=interval):
                self.assertEqual(snapshot[interval], self.bars(interval)["bars"][-1])

    def test_pagination_is_exclusive_and_candles_are_complete(self):
        for interval in NEW_INTERVALS:
            with self.subTest(interval=interval):
                all_bars = self.bars(interval)["bars"]
                page = self.bars(interval, limit=2)
                older = self.bars(interval, to=page["earliest"], limit=2)
                self.assertEqual(page["bars"], all_bars[-2:])
                self.assertEqual(older["bars"], all_bars[-4:-2])
                self.assertLess(older["latest"], page["earliest"])
                self.assertTrue(page["has_more"])


if __name__ == "__main__":
    unittest.main()
