#!/usr/bin/env bash
# Give company logos their own nginx rate limit and cache on the live box.
#
#   sudo bash apply_logo_route.sh <release-dir>
#
# WHY A PATCH AND NOT A COPY. The enabled sites are not pure renders of
# nginx-charto.conf: publish_app_domain.py writes `pivotnow-app` (app.pivotnow.in,
# its own TLS) and the validation host has its own name. Copying the template
# over either would revert that, so this inserts the one logo block before
# `location /api/pivot/` in EVERY enabled site that has that route, and leaves
# the rest of each file exactly as it is.
#
# Idempotent: a file that already carries the block is left alone. Any failure
# of `nginx -t` restores every touched file from the backup before reload.
set -euo pipefail

REL="${1:?usage: apply_logo_route.sh <release-dir>}"
RATE=/etc/nginx/conf.d/charto-ratelimit.conf
BAK="/root/nginx-backup-$(date -u +%Y%m%dT%H%M%SZ)"
SITES=$(for f in /etc/nginx/sites-enabled/*; do readlink -f "$f"; done)

mkdir -p "$BAK"
cp -a "$RATE" "$BAK/ratelimit"
for s in $SITES; do cp -a "$s" "$BAK/$(basename "$s")"; done

restore() {
  cp -a "$BAK/ratelimit" "$RATE"
  for s in $SITES; do cp -a "$BAK/$(basename "$s")" "$s"; done
  echo "restored from $BAK" >&2
}
trap restore ERR

cp "$REL/charto/deploy/nginx-ratelimit.conf" "$RATE"
for SITE in $SITES; do
python3 - "$REL/charto/deploy/nginx-charto.conf" "$SITE" <<'PY'
import pathlib, sys
tmpl, site = pathlib.Path(sys.argv[1]).read_text(), pathlib.Path(sys.argv[2])
live = site.read_text()
anchor = "    location /api/pivot/ {"
if "zone=charto_logo" in live or live.count(anchor) == 0:
    print(f"{site.name}: unchanged")
    sys.exit(0)
if live.count(anchor) != 1:
    sys.exit(f"{site.name}: expected one /api/pivot/ location")
start = tmpl.index("    # Logos before the general /api/pivot/ rule")
block = tmpl[start:tmpl.index(anchor)]
site.write_text(live.replace(anchor, block + anchor, 1))
print(f"{site.name}: logo block inserted")
PY
done

if ! nginx -t; then
  restore
  exit 1
fi
systemctl reload nginx
echo "applied; backup in $BAK"
