#!/usr/bin/env python3
"""Restore the crypto histories the cutover deferred, from the retained clone.

The crypto twin of retain_legacy_equities.py, and the same rules: run only
on the clean host, read the old store read-only, commit one symbol at a time
so an interrupted run resumes, pause before eating the serving disk's
headroom, and advertise a symbol in serving_inventory only once its minutes
are really here. Crypto is every Bybit USDT pair and Coinbase -USD pair the
old store held (392 on 2026-10-09). Prints one line per symbol and a total.
"""
import re
import shutil
import sqlite3

CRYPTO = re.compile(r'(USDT|-USD)$')


def main():
    c = sqlite3.connect('/srv/pivot-data/charto_bars.db', isolation_level=None,
                        timeout=60, uri=True)
    c.execute('PRAGMA trusted_schema=OFF')
    c.execute('PRAGMA synchronous=NORMAL')
    c.execute('PRAGMA cache_size=-131072')
    c.execute('ATTACH DATABASE ? AS legacy',
              ('file:/mnt/pivot-old-data/charto_bars.db?mode=ro',))
    c.execute('CREATE TABLE IF NOT EXISTS serving_inventory(symbol TEXT PRIMARY KEY)')
    legacy = {r[0] for r in c.execute('SELECT symbol FROM legacy.sync_state')}
    symbols = sorted(s for s in legacy if CRYPTO.search(s))
    # sync_state carries each series' sync cursor; copy only shared columns so a
    # schema that moved on in the clean store cannot break the restore.
    main_cols = [r[1] for r in c.execute('PRAGMA main.table_info(sync_state)')]
    old_cols = {r[1] for r in c.execute('PRAGMA legacy.table_info(sync_state)')}
    cols = ', '.join(x for x in main_cols if x in old_cols)
    restored = ready = 0
    for symbol in symbols:
        if not c.execute('SELECT 1 FROM bars WHERE symbol=? LIMIT 1', (symbol,)).fetchone():
            space = shutil.disk_usage('/srv/pivot-data')
            if space.free < max(30 * 1024**3, space.total * 0.12):
                raise RuntimeError('Retention paused before consuming serving disk headroom')
            c.execute('BEGIN')
            try:
                for table in ('bars', 'bars_1d'):
                    c.execute(f'INSERT OR IGNORE INTO main.{table} SELECT * FROM legacy.{table} '
                              'WHERE symbol=?', (symbol,))
                if cols:
                    c.execute(f'INSERT OR IGNORE INTO main.sync_state ({cols}) '
                              f'SELECT {cols} FROM legacy.sync_state WHERE symbol=?', (symbol,))
                c.execute('COMMIT')
            except BaseException:
                c.execute('ROLLBACK')
                raise
            restored += 1
        if c.execute('SELECT 1 FROM bars WHERE symbol=? LIMIT 1', (symbol,)).fetchone():
            c.execute('INSERT OR IGNORE INTO serving_inventory VALUES (?)', (symbol,))
            ready += 1
    print(f'CRYPTO_RESTORED {restored} READY {ready} OF {len(symbols)}', flush=True)


if __name__ == '__main__':
    main()
