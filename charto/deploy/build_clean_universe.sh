#!/usr/bin/env bash
# Only the new offline store is imported. The old live VM is untouched.
set -euo pipefail
umask 022
release="$(tr -d '\n' < /etc/pivot/stage-release)"
data=/mnt/pivot-old-data
source_root=/mnt/pivot-source
test -f "$data/charto_bars.db"
test -f "$source_root/data/fix/sessions.json"
test ! -e "$data/serving/charto_bars.db"
mkdir -p "$data/serving" "$data/import-sources"

# Recover/checkpoint the point-in-time CLONE, never the live original disk.
# Retains all committed rows, and frees its copied 47GB WAL for the new store.
python3 - "$data/charto_bars.db" <<'PY'
import sqlite3, sys
c = sqlite3.connect(sys.argv[1], timeout=60)
assert c.execute("SELECT 1 FROM bars LIMIT 1").fetchone()
result = c.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()
assert result[0] == 0, result
c.close()
print('CLONE_CHECKPOINT_OK', result, flush=True)
PY

python3 - "$source_root" "$data/import-sources" <<'PY'
import json, pathlib, sys
root, out = map(pathlib.Path, sys.argv[1:])
for folder in ('backfill', 'backfill2'):
    for p in (root / 'data' / folder).glob('*.db'):
        # Foreign-market inventory isn't approval to widen the product scope.
        if p.stem == 'idx_global':
            continue
        (out / p.name).symlink_to(p)
(out / 'sessions.json').write_bytes((root / 'data/fix/sessions.json').read_bytes())
end_path = root / 'data/backfill/state/end_ts'
if end_path.exists():
    end = int(end_path.read_text().strip())
    (out / 'end.json').write_text(json.dumps({'end_ts': end}))
print('SOURCE_FILES_READY', len(list(out.glob('*.db'))), flush=True)
PY
python3 "$release/charto/data/audit_minute_universe.py" --src "$data/import-sources" \
  --sessions "$data/import-sources/sessions.json" --report "$data/serving/source-audit.json"

python3 -u "$release/charto/data/land_universe.py" --src "$data/import-sources" \
  --dst "$data/serving/charto_bars.db"

# Retain data through the audited allowlist, without transporting old SQL or
# scanning the multi-billion-row minute table for symbol discovery.
python3 "$release/charto/deploy/finalize_clean_universe.py" \
  --old "$data/charto_bars.db" --new "$data/serving/charto_bars.db" \
  --audit "$data/serving/source-audit.json" --report "$data/serving/serving-universe.json"
echo OFFLINE_UNIVERSE_READY
