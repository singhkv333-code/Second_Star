#!/usr/bin/env python3
"""Offline retention after landing. Copies data, not old executable schemas."""
import argparse
import json
from pathlib import Path
import re
import sqlite3

CACHES = {'financials', 'balance_sheet', 'quarters', 'statement', 'results',
          'benchmark', 'company_profile', 'classification', 'revenue_mix',
          'instrument_logo', 'screen_meta', 'pattern_stats', 'pattern_stats_meta',
          'sync_state', 'news_cache', 'deals', 'delivery', 'fut_oi', 'vp_screen'}
TYPES = {'', 'TEXT', 'INTEGER', 'INT', 'REAL', 'BLOB', 'NUMERIC', 'FLOAT', 'BOOLEAN'}


def identifier(name):
    if not re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*', name):
        raise RuntimeError('Unexpected schema identifier')
    return '"' + name + '"'


def retain(old, new, audit, *, defer_legacy=False):
    result = json.loads(Path(audit).read_text())
    if result['failed']:
        raise RuntimeError('Source aggregation gate failed')
    c = sqlite3.connect(new, isolation_level=None, timeout=60, uri=True)
    c.execute('PRAGMA trusted_schema=OFF')
    c.execute('PRAGMA synchronous=NORMAL')
    c.execute('PRAGMA cache_size=-131072')
    c.execute('PRAGMA mmap_size=2147483648')
    c.execute('ATTACH DATABASE ? AS legacy', (f'file:{old}?mode=ro',))
    c.execute('PRAGMA legacy.cache_size=-65536')
    c.execute('PRAGMA legacy.mmap_size=2147483648')
    tables = {r[0] for r in c.execute("SELECT name FROM legacy.sqlite_master WHERE type='table'")}
    copied = []
    for name in sorted(CACHES & tables):
        quoted = identifier(name)
        cols = c.execute(f'PRAGMA legacy.table_info({quoted})').fetchall()
        definitions, primary = [], []
        for _, col, typ, notnull, _, pk in cols:
            if typ.upper() not in TYPES:
                raise RuntimeError('Unexpected cache column type')
            definitions.append(f'{identifier(col)} {typ}' + (' NOT NULL' if notnull else ''))
            if pk:
                primary.append((pk, identifier(col)))
        if primary:
            definitions.append('PRIMARY KEY (' + ','.join(col for _, col in sorted(primary)) + ')')
        # Types and keys only. No old triggers, virtual tables, SQL defaults,
        # functions, views or executable DDL are transported.
        c.execute(f'CREATE TABLE IF NOT EXISTS main.{quoted} (' + ','.join(definitions) + ')')
        names = ','.join(identifier(row[1]) for row in cols)
        # This is an offline new-store handoff. Replacing each cache atomically
        # also makes a failed/retried finalization safe for unkeyed deal rows.
        c.execute('BEGIN')
        c.execute(f'DELETE FROM main.{quoted}')
        c.execute(f'INSERT INTO main.{quoted} ({names}) SELECT {names} FROM legacy.{quoted}')
        c.execute('COMMIT')
        copied.append(name)
        print('RETAINED_CACHE', name, flush=True)
    candidates = {r[0] for r in c.execute('SELECT symbol FROM legacy.bars_1d GROUP BY symbol')}
    if 'sync_state' in tables:
        candidates.update(r[0] for r in c.execute('SELECT symbol FROM legacy.sync_state'))
    retained, deferred = [], []
    for symbol in sorted(candidates):
        if c.execute('SELECT 1 FROM instrument_master WHERE id=?', (symbol,)).fetchone():
            continue
        if defer_legacy:
            # Operational staging gate only: the immutable source remains in
            # place. Do not claim a partially copied history is fully retained.
            deferred.append(symbol)
            continue
        print('RETAINING_LEGACY_HISTORY', symbol, flush=True)
        c.execute('BEGIN')
        for table in ('bars', 'bars_1d'):
            c.execute(f'INSERT OR IGNORE INTO main.{table} SELECT * FROM legacy.{table} WHERE symbol=?', (symbol,))
        c.execute('COMMIT')
        retained.append(symbol)
    c.execute('DETACH DATABASE legacy')
    # Different source families finish at different times. Never promote one
    # global date as proof of every series' coverage. Exclude each series'
    # last rolled session; later/catch-up minutes remain authoritative.
    c.execute('CREATE TABLE IF NOT EXISTS rollup_symbols (symbol TEXT PRIMARY KEY, through_ts INTEGER)')
    c.execute('BEGIN')
    c.execute('DELETE FROM rollup_meta')
    c.execute('DELETE FROM rollup_symbols')
    for sid, opening, wrap in c.execute('SELECT id,session_open,wrap FROM instrument_master').fetchall():
        last = c.execute('SELECT ts FROM bars_15m WHERE symbol=? ORDER BY ts DESC LIMIT 1', (sid,)).fetchone()
        if last:
            tz_off = 19800 - opening * 60 if wrap else 19800
            through = (last[0] + tz_off) // 86400 * 86400 - tz_off
            c.execute('INSERT INTO rollup_symbols VALUES (?,?)', (sid, through))
    c.execute('COMMIT')
    ids = c.execute('SELECT id FROM instrument_master').fetchall()
    minute = sum(bool(c.execute('SELECT 1 FROM bars WHERE symbol=? LIMIT 1', (s,)).fetchone()) for (s,) in ids)
    report = {'master_ids': len(ids), 'minute_ready_ids': minute,
              'retained_legacy_ids': retained, 'retained_caches': copied,
              'deferred_legacy_ids': deferred,
              'verified_rollup_ids': c.execute('SELECT COUNT(*) FROM rollup_symbols').fetchone()[0]}
    c.close()
    return report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--old', required=True)
    ap.add_argument('--new', required=True)
    ap.add_argument('--audit', required=True)
    ap.add_argument('--report', required=True)
    ap.add_argument('--defer-legacy', action='store_true',
                    help='Activate landed universe while preserving uncopied legacy source')
    a = ap.parse_args()
    result = retain(a.old, a.new, a.audit, defer_legacy=a.defer_legacy)
    Path(a.report).write_text(json.dumps(result))
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
