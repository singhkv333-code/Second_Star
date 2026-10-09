#!/usr/bin/env python3
"""Explicit bounded catch-up; retains normal stream freshness refusals."""
import json
import os
from pathlib import Path
import sys
import time


def main():
    release = Path('/etc/pivot/stage-release').read_text().strip()
    sys.path.insert(0, release + '/charto/data')
    sys.path.insert(0, release + '/pivot')
    import dataserver as ds
    import kite_stream as ks
    token = ks._kite_access_token()
    # Real authentication probe, not merely "a nonempty token was found".
    ks._kite_client(token).profile()
    print('KITE_SESSION_VALID', flush=True)
    # Leave quota headroom for the old serving app until handoff is complete.
    ks.FILL_PACE = 0.5
    symbols = ds._venue_symbols('kite')
    priority = ['RELIANCE', 'HDFCBANK', 'TCS', 'INFY', 'ICICIBANK', 'NIFTY 50', 'NIFTY BANK', 'SENSEX']
    symbols.sort(key=lambda s: (s not in priority, s))
    report = {'started': int(time.time()), 'requested': len(symbols), 'batches': [], 'finished': None}
    target = Path('/srv/pivot-data/catch-up-report.json')
    for i in range(0, len(symbols), 100):
        # Bound both historical windows and memory; expired identities are
        # refused by current exchange+spelling validation, never remapped.
        result = ks.fill_gaps(symbols[i:i + 100], token, max_fill_min=20000)
        report['batches'].append(result)
        tmp = target.with_suffix('.tmp')
        tmp.write_text(json.dumps(report)); os.replace(tmp, target)
        print(json.dumps({'processed': min(i + 100, len(symbols)), 'total': len(symbols),
                          'minute_rows_written': result['filled'], 'skipped': len(result['skipped']),
                          'unfilled': len(result['unfilled']), 'too_wide': len(result['too_wide']),
                          'errors': len(result['errors'])}), flush=True)
    report['finished'] = int(time.time())
    target.write_text(json.dumps(report))
    print('BOUNDED_UNIVERSE_CATCHUP_COMPLETE', flush=True)


if __name__ == '__main__':
    main()
