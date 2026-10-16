#!/usr/bin/env bash
# Send the public research pages and the crawler files to the shell.
#
#   sudo bash apply_seo_routes.sh
#
# The shell (pivot-next, :3000) now serves free, indexable pages and the files
# search and answer engines read:
#
#   /stocks, /stocks/<letter|list>, /sectors, /sectors/<sector>, /sitemaps/*
#   /robots.txt /sitemap.xml /llms.txt /llms-full.txt /indexnow-key.txt
#
# (/stock/<symbol> already goes to the shell.) Without this they fall through
# to the chart server and answer {"error": "not found"}. This adds the three
# page prefixes to each enabled site's shell route list and one exact-file
# location beside it, backs the files up, and restores them if `nginx -t`
# fails. Idempotent: a site that already has the routes is left alone.
set -euo pipefail

BAK="/root/nginx-backup-$(date -u +%Y%m%dT%H%M%SZ)-seo"
mkdir -p "$BAK"
SITES=$(for f in /etc/nginx/sites-enabled/*; do readlink -f "$f"; done)
for s in $SITES; do cp -a "$s" "$BAK/"; done

restore() {
  for s in $SITES; do cp -a "$BAK/$(basename "$s")" "$s"; done
  echo "restored from $BAK" >&2
}
trap restore ERR

for s in $SITES; do
python3 - "$s" <<'PY'
import pathlib, sys
site = pathlib.Path(sys.argv[1])
text = site.read_text()
anchor = "|affiliate|r|paywall-gallery)(/|$) {"
if "|stocks|sectors|sitemaps" in text:
    print(f"{site.name}: unchanged")
    sys.exit(0)
if text.count(anchor) != 1:
    print(f"{site.name}: no shell route list, skipped")
    sys.exit(0)
text = text.replace(anchor, "|affiliate|r|paywall-gallery|stocks|sectors|sitemaps)(/|$) {", 1)
files = """    # Crawler files, served by the shell (charto/deploy/apply_seo_routes.sh).
    location ~ ^/(robots\\.txt|sitemap\\.xml|llms\\.txt|llms-full\\.txt|indexnow-key\\.txt)$ {
        limit_req zone=charto_data burst=40 nodelay;
        proxy_pass http://127.0.0.1:3000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

"""
# Before the shell route list's own location line, so both sit together.
head = "    location ~ ^/(login|"
if text.count(head) != 1:
    sys.exit(f"{site.name}: expected one shell route location")
text = text.replace(head, files + head, 1)
site.write_text(text)
print(f"{site.name}: SEO routes added")
PY
done

if ! nginx -t; then
  restore
  exit 1
fi
systemctl reload nginx
echo "applied; backup in $BAK"
