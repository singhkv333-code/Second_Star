#!/usr/bin/env bash
#
# Back up the ONE database that cannot be rebuilt.
#
# charto_bars.db is 29 GB and every byte of it is derivable again — from the
# blob universe, from the per-symbol parquets, from Kite. charto_users.db is
# small and none of it is: accounts, saved layouts, journal trades, past
# conversations, alerts. Losing the bars costs a re-sync; losing this costs
# the beta.
#
# So this runs often and ships small, rather than trying to protect both with
# one policy. The 29 GB store is covered by disk snapshots instead, which is
# the right instrument for a large mostly-static file and the wrong one for a
# small file that changes every minute.
#
# CONSISTENCY: sqlite3's ONLINE BACKUP API, not `cp`. The database is in WAL
# mode with a live writer; copying it byte-wise while a transaction is open
# yields an image that may be torn, and the tear stays invisible until the day
# you try to restore it. The backup API produces a file valid by construction,
# and it is reached through Python because the box has no sqlite3 CLI.
#
# AUTH: the VM's system-assigned managed identity, not a stored key. A backup
# credential sitting on the box is the same credential an attacker who reaches
# the box already has; an IMDS token cannot be read off disk and it expires.
#
# Install:  sudo cp charto-backup.{service,timer} /etc/systemd/system/
#           sudo systemctl enable --now charto-backup.timer
set -euo pipefail

DB="${CHARTO_USERS_DB:-/data/app/charto/data/charto_users.db}"
ACCOUNT="${CHARTO_BACKUP_ACCOUNT:-pivotmarketdata}"
CONTAINER="${CHARTO_BACKUP_CONTAINER:-kite-1min}"
PREFIX="${CHARTO_BACKUP_PREFIX:-backup/users}"
KEEP_LOCAL="${CHARTO_BACKUP_KEEP:-48}"
LOCAL_DIR="${CHARTO_BACKUP_DIR:-/data/backup}"

[ -f "$DB" ] || { echo "backup: no database at $DB" >&2; exit 1; }
mkdir -p "$LOCAL_DIR"

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
snap="$LOCAL_DIR/charto_users_$stamp.db"

# Snapshot, then verify, then compress. Gzipping straight out of sqlite would
# leave nothing on disk to fall back to when the upload fails.
python3 - "$DB" "$snap" <<'PY'
import sqlite3, sys
src, dst = sys.argv[1], sys.argv[2]
s = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
d = sqlite3.connect(dst)
with d:
    s.backup(d)            # online backup: safe against a concurrent writer
ok = d.execute("PRAGMA quick_check").fetchone()[0]
s.close(); d.close()
if ok != "ok":
    sys.exit(f"snapshot failed its own integrity check: {ok}")
PY

gzip -f "$snap"
snap="$snap.gz"
size=$(stat -c%s "$snap")

token="$(curl -s -H Metadata:true --max-time 10 \
  'http://169.254.169.254/metadata/identity/oauth2/token?api-version=2018-02-01&resource=https://storage.azure.com/' \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["access_token"])')"
[ -n "$token" ] || { echo "backup: no managed-identity token" >&2; exit 1; }

url="https://$ACCOUNT.blob.core.windows.net/$CONTAINER/$PREFIX/charto_users_$stamp.db.gz"
code=$(curl -s -o /dev/null -w '%{http_code}' -X PUT --max-time 120 \
  -H "Authorization: Bearer $token" \
  -H 'x-ms-version: 2021-08-06' \
  -H 'x-ms-blob-type: BlockBlob' \
  --data-binary "@$snap" "$url")

if [ "$code" != "201" ]; then
  echo "backup: upload failed (HTTP $code) — local copy kept at $snap" >&2
  exit 1
fi

# Local copies are the fast restore path; the blob is the one that survives
# the disk. Keep a rolling window here and everything there — at this size a
# year of hourly backups is a few hundred megabytes.
ls -1t "$LOCAL_DIR"/charto_users_*.db.gz 2>/dev/null | tail -n +$((KEEP_LOCAL + 1)) \
  | xargs -r rm -f

echo "backup: $stamp · ${size} bytes · uploaded"
