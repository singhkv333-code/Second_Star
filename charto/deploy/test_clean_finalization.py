"""Small offline handoff fixture, with no live account or market-data store."""
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

module = importlib.util.spec_from_file_location('clean_finalize', Path(__file__).with_name('finalize_clean_universe.py'))
finalize = importlib.util.module_from_spec(module)
module.loader.exec_module(finalize)


class FinalizationTests(unittest.TestCase):
    def test_retains_legacy_and_caches_without_old_executable_schema_and_is_idempotent(self):
        with tempfile.TemporaryDirectory(prefix='pivot-finalize-fixture-') as folder:
            old, new, audit = [str(Path(folder) / name) for name in ('old.db', 'new.db', 'audit.json')]
            for path in (old, new):
                c = sqlite3.connect(path)
                for table in ('bars', 'bars_1d', 'bars_15m'):
                    c.execute(f'CREATE TABLE {table}(symbol TEXT, ts INTEGER, o REAL, h REAL, l REAL, c REAL, v REAL, PRIMARY KEY(symbol,ts))')
                c.commit(); c.close()
            c = sqlite3.connect(old)
            c.executescript('CREATE TABLE financials(symbol TEXT PRIMARY KEY, payload TEXT); CREATE TABLE deals(symbol TEXT, qty INTEGER); CREATE TABLE unrelated_private(value TEXT); CREATE VIEW old_executable_view AS SELECT * FROM financials; CREATE TRIGGER old_trigger AFTER INSERT ON financials BEGIN INSERT INTO unrelated_private VALUES (new.symbol); END;')
            c.execute("INSERT INTO financials VALUES ('NSE', '{}')")
            c.execute("INSERT INTO deals VALUES ('NSE', 5)")
            for table in ('bars', 'bars_1d'):
                c.execute(f'INSERT INTO {table} VALUES (?,?,?,?,?,?,?)', ('CRYPTO', 60, 10, 11, 9, 10, 0.5))
            c.commit(); c.close()
            c = sqlite3.connect(new)
            c.executescript("CREATE TABLE instrument_master(id TEXT PRIMARY KEY, session_open INTEGER, wrap INTEGER); INSERT INTO instrument_master VALUES ('NSE',555,0); CREATE TABLE rollup_meta(through_ts INTEGER); INSERT INTO rollup_meta VALUES (9999999999);")
            for table in ('bars', 'bars_15m'):
                c.execute(f'INSERT INTO {table} VALUES (?,?,?,?,?,?,?)', ('NSE', 100000, 10, 11, 9, 10, 1))
            c.commit(); c.close()
            Path(audit).write_text(json.dumps({'failed': []}))
            first = finalize.retain(old, new, audit)
            second = finalize.retain(old, new, audit)
            self.assertEqual(first, second)
            self.assertEqual(first['minute_ready_ids'], 1)
            c = sqlite3.connect(new)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM deals').fetchone()[0], 1)
            self.assertEqual(c.execute("SELECT v FROM bars WHERE symbol='CRYPTO'").fetchone()[0], 0.5)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM sqlite_master WHERE type IN ('view','trigger')").fetchone()[0], 0)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM rollup_meta').fetchone()[0], 0)
            self.assertEqual(c.execute('SELECT through_ts FROM rollup_symbols').fetchone()[0], (100000 + 19800) // 86400 * 86400 - 19800)
            c.close()
            deferred = finalize.retain(old, new, audit, defer_legacy=True)
            self.assertEqual(deferred['deferred_legacy_ids'], ['CRYPTO'])
            self.assertEqual(deferred['retained_legacy_ids'], [])
            c = sqlite3.connect(old)
            self.assertEqual(c.execute("SELECT v FROM bars WHERE symbol='CRYPTO'").fetchone()[0], 0.5)
            c.close()


if __name__ == '__main__':
    unittest.main()
