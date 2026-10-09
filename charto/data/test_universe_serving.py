"""Small-store regressions; no production DB, sockets or model calls."""
from __future__ import annotations
import ast
from pathlib import Path
import sqlite3
import time
import unittest


class ServingTests(unittest.TestCase):
    def setUp(self):
        c = self.db = sqlite3.connect(':memory:')
        for table in ('bars', 'bars_15m', 'bars_1d'):
            c.execute(f'CREATE TABLE {table} (symbol TEXT, ts INTEGER, o REAL, h REAL, l REAL, c REAL, v REAL, PRIMARY KEY(symbol,ts))')
        c.executescript('CREATE TABLE instrument_master(id TEXT); CREATE TABLE sync_state(symbol TEXT); CREATE TABLE rollup_meta(through_ts INTEGER);')
        tree = ast.parse(Path(__file__).with_name('dataserver.py').read_text())
        names = {'_symbols_with_bars', '_symbol_ready', '_intraday_rows', '_bucket_stamp'}
        functions = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
        self.ns = dict(_con=c, sqlite3=sqlite3, time=time, _bar_symbols_cache=None,
                       _BAR_SYMBOLS_TTL=300, NSE_SESSION=(0, 0),
                       session_for=lambda s: (0, 0), _rollup_for_symbol=lambda s: 3600)
        exec(compile(ast.Module(body=functions, type_ignores=[]), 'serving-fixture', 'exec'), self.ns)

    def row(self, table, symbol, stamp):
        self.db.execute(f'INSERT INTO {table} VALUES (?,?,?,?,?,?,?)', (symbol, stamp, 10, 11, 9, 10, 1))

    def test_partial_sync_inventory_does_not_hide_imported_master(self):
        self.db.executemany('INSERT INTO instrument_master VALUES (?)', [('OLD',), ('BSE:NEW',), ('DAILY_ONLY',)])
        self.db.execute("INSERT INTO sync_state VALUES ('OLD')")
        self.row('bars', 'OLD', 0); self.row('bars', 'BSE:NEW', 0)
        self.row('bars_1d', 'CRYPTO', 0); self.row('bars', 'CRYPTO', 0)
        self.assertEqual(self.ns['_symbols_with_bars'](), {'OLD', 'BSE:NEW', 'CRYPTO'})

    def test_verified_inventory_avoids_daily_scan(self):
        self.db.execute('CREATE TABLE serving_inventory(symbol TEXT PRIMARY KEY)')
        self.db.executemany('INSERT INTO serving_inventory VALUES (?)', [('OLD',), ('BSE:NEW',)])
        queries = []
        self.db.set_trace_callback(queries.append)
        self.assertEqual(self.ns['_symbols_with_bars'](), {'OLD', 'BSE:NEW'})
        self.assertFalse(any('GROUP BY' in q for q in queries))

    def test_global_watermark_does_not_hide_legacy_minutes_without_rollups(self):
        self.row('bars', 'CRYPTO', 60)
        self.assertEqual(len(self.ns['_intraday_rows']('CRYPTO', None, 100, 15)), 1)

    def test_rollup_suffix_does_not_hide_older_minutes(self):
        self.row('bars', 'CRYPTO', 60); self.row('bars_15m', 'CRYPTO', 900)
        rows = self.ns['_intraday_rows']('CRYPTO', None, 100, 15)
        self.assertEqual(rows[0][0], 60)

    def test_sparse_first_trade_can_use_correctly_anchored_rollup(self):
        self.row('bars', 'THIN', 360); self.row('bars_15m', 'THIN', 0)
        rows = self.ns['_intraday_rows']('THIN', None, 100, 15)
        self.assertEqual(rows[0][0], 0)


if __name__ == '__main__':
    unittest.main()
