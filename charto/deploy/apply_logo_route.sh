#!/usr/bin/env bash
# Give company logos their own nginx rate limit and cache on the live box.
#
#   sudo bash apply_logo_route.sh <release-dir>
#
# WHY A PATCH AND NOT A COPY. /etc/nginx/sites-available/pivot-clean is not a
# pure render of nginx-charto.conf any more: publish_app_domain.py edits it in
# place (the app domain, TLS). Copying the template over it would revert those
# edits, so this inserts the one logo block before `location /api/pivot/` and
# leaves the rest of the live file exactly as it is.
#
# Idempotent: a file that already carries the block is left alone. Any failure
# of `nginx -t` restores both files from the backup before nginx is reloaded.
set -euo pipefail

REL="${1:?usage: apply_logo_route.sh <release-dir>}"
SITE=/etc/nginx/sites-available/pivot-clean
RATE=/etc/nginx/conf.d/charto-ratelimit.conf
BAK="/root/nginx-backup-$(date -u +%Y%m%dT%H%M%SZ)"

mkdir -p "$BAK"
cp -a "$SITE" "$BAK/site"
cp -a "$RATE" "$BAK/ratelimit"

restore() {
  cp -a "$BAK/site" "$SITE"
  cp -a "$BAK/ratelimit" "$RATE"
  echo "restored from $BAK" >&2
}

cp "$REL/charto/deploy/nginx-ratelimit.conf" "$RATE"
python3 - "$REL/charto/deploy/nginx-charto.conf" "$SITE" <<'PY'
import pathlib, sys
tmpl, site = pathlib.Path(sys.argv[1]).read_text(), pathlib.Path(sys.argv[2])
live = site.read_text()
if "zone=charto_logo" in live:
    print("logo block already present")
    sys.exit(0)
start = tmpl.index("    # Logos before the general /api/pivot/ rule")
anchor = "    location /api/pivot/ {"
block = tmpl[start:tmpl.index(anchor)]
if live.count(anchor) != 1:
    sys.exit("expected exactly one /api/pivot/ location in the live site")
site.write_text(live.replace(anchor, block + anchor, 1))
print("logo block inserted")
PY

if ! nginx -t; then
  restore
  exit 1
fi
systemctl reload nginx
echo "applied; backup in $BAK"
