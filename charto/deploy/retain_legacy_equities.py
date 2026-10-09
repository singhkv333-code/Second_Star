#!/usr/bin/env python3
"""Restore existing NSE histories from the retained clone, never broker guesses.

Run only on the clean host. Each symbol commits atomically; an interrupted
run can resume. Crypto and foreign instruments are not included in this job.
"""
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess


def main():
    release = Path('/etc/pivot/stage-release').read_text().strip()
    known = set(json.loads((Path(release) / 'charto/data/symbols.json').read_text()))
    c = sqlite3.connect('/srv/pivot-data/charto_bars.db', isolation_level=None,
                        timeout=60, uri=True)
    c.execute('PRAGMA trusted_schema=OFF')
    c.execute('PRAGMA synchronous=NORMAL')
    c.execute('PRAGMA cache_size=-131072')
    c.execute('PRAGMA mmap_size=2147483648')
    c.execute('ATTACH DATABASE ? AS legacy',
              ('file:/mnt/pivot-old-data/charto_bars.db?mode=ro',))
    c.execute('PRAGMA legacy.cache_size=-65536')
    available = {r[0] for r in c.execute('SELECT symbol FROM legacy.sync_state')}
    available.update(r[0] for r in c.execute('SELECT symbol FROM legacy.bars_1d GROUP BY symbol'))
    candidates = known & available
    priority = ['RELIANCE', 'HDFCBANK', 'TCS', 'INFY', 'ICICIBANK', 'SBIN']
    ordered = [s for s in priority if s in candidates] + sorted(candidates - set(priority))
    # A durable inventory avoids a daily-table GROUP BY and thousands of cold
    # minute seeks on every application boot. Every entry is checked on data.
    c.execute('BEGIN')
    try:
        c.execute('CREATE TABLE IF NOT EXISTS serving_inventory(symbol TEXT PRIMARY KEY)')
        for (sid,) in c.execute('SELECT id FROM instrument_master').fetchall():
            if c.execute('SELECT 1 FROM bars WHERE symbol=? LIMIT 1', (sid,)).fetchone():
                c.execute('INSERT OR IGNORE INTO serving_inventory VALUES (?)', (sid,))
        c.execute('COMMIT')
    except BaseException:
        c.execute('ROLLBACK')
        raise
    refreshed = False
    for symbol in ordered:
        if not c.execute('SELECT 1 FROM bars WHERE symbol=? LIMIT 1', (symbol,)).fetchone():
            space = shutil.disk_usage('/srv/pivot-data')
            if space.free < max(30 * 1024**3, space.total * 0.12):
                raise RuntimeError('Retention paused before consuming serving disk headroom')
            print('RESTORING_NSE', symbol, flush=True)
            c.execute('BEGIN')
            try:
                for table in ('bars', 'bars_1d'):
                    c.execute(f'INSERT OR IGNORE INTO main.{table} SELECT * FROM legacy.{table} WHERE symbol=?', (symbol,))
                c.execute('COMMIT')
            except BaseException:
                c.execute('ROLLBACK')
                raise
        if c.execute('SELECT 1 FROM bars WHERE symbol=? LIMIT 1', (symbol,)).fetchone():
            c.execute('INSERT OR IGNORE INTO serving_inventory VALUES (?)', (symbol,))
            print('NSE_READY', symbol, flush=True)
        if symbol == 'HDFCBANK' and not refreshed:
            for path in Path('/srv/pivot-data').glob('charto_bars.db*'):
                if path.name in ('charto_bars.db', 'charto_bars.db-wal', 'charto_bars.db-shm'):
                    shutil.chown(path, user='pivot-data', group='pivot-runtime')
                    path.chmod(0o660)
            subprocess.run(['systemctl', 'restart', 'charto'], check=True)
            refreshed = True
        c.execute('PRAGMA main.wal_checkpoint(PASSIVE)')
    print('NSE_RETENTION_COMPLETE', len(ordered), flush=True)
    c.close()


if __name__ == '__main__':
    main()
