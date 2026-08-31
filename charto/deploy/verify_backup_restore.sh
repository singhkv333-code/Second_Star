#!/usr/bin/env bash
# Download the newest Charto users backup into a temporary directory and prove
# it opens, passes SQLite quick_check, and contains the beta's durable tables.
# The live database is never opened for writing and the restored copy is
# deleted on exit.
set -euo pipefail
umask 077

ACCOUNT="${CHARTO_BACKUP_ACCOUNT:-pivotmarketdata}"
CONTAINER="${CHARTO_BACKUP_CONTAINER:-kite-1min}"
PREFIX="${CHARTO_BACKUP_PREFIX:-backup/users}"
MAX_AGE_HOURS="${CHARTO_BACKUP_VERIFY_MAX_AGE_HOURS:-3}"
PYTHON_BIN="${CHARTO_PYTHON:-python3}"

work="$(mktemp -d /tmp/charto-restore-verify.XXXXXX)"
trap 'rm -rf "$work"' EXIT

token="${AZURE_STORAGE_TOKEN:-}"
if [ -z "$token" ]; then
  token="$(curl -fsS -H Metadata:true --max-time 10 \
    'http://169.254.169.254/metadata/identity/oauth2/token?api-version=2018-02-01&resource=https://storage.azure.com/' \
    | "$PYTHON_BIN" -c 'import sys,json; print(json.load(sys.stdin)["access_token"])')"
fi
[ -n "$token" ] || { echo "restore-check: no Azure Storage token" >&2; exit 1; }

curl -fsS --max-time 60 \
  -H "Authorization: Bearer $token" \
  -H 'x-ms-version: 2021-08-06' \
  "https://$ACCOUNT.blob.core.windows.net/$CONTAINER?restype=container&comp=list&prefix=$PREFIX&maxresults=5000" \
  -o "$work/list.xml"

latest="$("$PYTHON_BIN" - "$work/list.xml" "$MAX_AGE_HOURS" <<'PY'
import datetime as dt
import email.utils
import sys
import xml.etree.ElementTree as ET

path, max_hours = sys.argv[1], float(sys.argv[2])
root = ET.parse(path).getroot()
items = []
for blob in root.findall(".//Blob"):
    name = blob.findtext("Name") or ""
    modified = blob.findtext("Properties/Last-Modified") or ""
    if name and modified:
        items.append((email.utils.parsedate_to_datetime(modified), name))
if not items:
    raise SystemExit("restore-check: no user-database backups found")
modified, name = max(items)
age = (dt.datetime.now(dt.timezone.utc) - modified).total_seconds() / 3600
if age > max_hours:
    raise SystemExit(f"restore-check: newest backup is stale ({age:.1f}h > {max_hours:.1f}h)")
print(name)
PY
)"

# Blob names here contain only the fixed prefix, timestamp and filename-safe
# characters, so their path form is already the canonical request target.
curl -fsS --max-time 120 \
  -H "Authorization: Bearer $token" \
  -H 'x-ms-version: 2021-08-06' \
  "https://$ACCOUNT.blob.core.windows.net/$CONTAINER/$latest" \
  -o "$work/users.db.gz"
gzip -dc "$work/users.db.gz" > "$work/users.db"

"$PYTHON_BIN" - "$work/users.db" "$latest" <<'PY'
import json
import sqlite3
import sys

path, blob = sys.argv[1:]
db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
check = db.execute("PRAGMA quick_check").fetchone()[0]
if check != "ok":
    raise SystemExit(f"restore-check: SQLite quick_check failed: {check}")
have = {r[0] for r in db.execute(
    "SELECT name FROM sqlite_master WHERE type='table'")}
required = {"users", "sessions", "workspace_state", "layouts",
            "conversations", "alerts", "journal_trades"}
missing = sorted(required - have)
if missing:
    raise SystemExit("restore-check: missing durable tables: " + ", ".join(missing))
counts = {name: db.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
          for name in sorted(required)}
db.close()
print(json.dumps({"ok": True, "blob": blob, "quick_check": check,
                  "counts": counts}, separators=(",", ":")))
PY
