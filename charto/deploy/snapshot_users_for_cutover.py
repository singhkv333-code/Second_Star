#!/usr/bin/env python3
"""Consistent full-plane backup on the rollback VM; never exports secrets."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import urllib.request


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--freeze', action='store_true')
    args = ap.parse_args()
    src = Path('/data/app/charto/data/charto_users.db')
    if not src.is_file():
        raise RuntimeError('Verified user-state store missing; refusing empty backup')
    if args.freeze:
        subprocess.run(['systemctl', 'stop', 'charto-deploy.timer'], check=True)
        subprocess.run(['systemctl', 'stop', 'charto', 'pivot-api'], check=True)
    folder = Path(tempfile.mkdtemp(prefix='pivot-state-handoff-', dir='/root'))
    target = folder / 'users.db'
    s = sqlite3.connect(f'file:{src}?mode=ro', uri=True, timeout=30)
    d = sqlite3.connect(target)
    s.backup(d, pages=1024, sleep=0.1)
    assert d.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    names = ('users', 'sessions', 'workspace_state', 'layouts', 'conversations',
             'alerts', 'paper_accounts', 'strategies', 'journal_trades')
    counts = {n: d.execute(f'SELECT COUNT(*) FROM "{n}"').fetchone()[0] for n in names}
    s.close(); d.close()
    payload = gzip.compress(target.read_bytes(), mtime=0)
    packed = folder / 'users.db.gz'
    packed.write_bytes(payload)
    blob = f'backup/cutover/{folder.name}/users.db.gz'
    req = urllib.request.Request('http://169.254.169.254/metadata/identity/oauth2/token?'
                                 'api-version=2018-02-01&resource=https://storage.azure.com/',
                                 headers={'Metadata': 'true'})
    token = json.load(urllib.request.urlopen(req, timeout=15))['access_token']
    req = urllib.request.Request(f'https://pivotmarketdata.blob.core.windows.net/kite-1min/{blob}',
                                 data=payload, method='PUT', headers={
                                     'Authorization': 'Bearer ' + token,
                                     'x-ms-version': '2021-08-06', 'x-ms-blob-type': 'BlockBlob',
                                     'If-None-Match': '*'})
    with urllib.request.urlopen(req, timeout=120) as response:
        assert response.status == 201
    print(json.dumps({'blob': blob, 'sha256': hashlib.sha256(payload).hexdigest(),
                      'bytes': len(payload), 'counts': counts, 'frozen': args.freeze}))


if __name__ == '__main__':
    main()
