#!/usr/bin/env bash
# Send referral links (/r/<code>) to the shell on the live box.
#
#   sudo bash apply_referral_route.sh
#
# The launch commit (3afcbe31) added `r` to the shell's route list in
# nginx-charto.conf, but the enabled sites were rendered before it and never
# got it: /r/<code> fell through to the chart server and every affiliate link
# answered {"error": "not found"} (seen 2026-10-16). This adds `r` to the
# route list of each enabled site that lacks it, backs the files up, and
# restores them if `nginx -t` fails. Idempotent.
set -euo pipefail

BAK="/root/nginx-backup-$(date -u +%Y%m%dT%H%M%SZ)-referral"
mkdir -p "$BAK"
SITES=$(for f in /etc/nginx/sites-enabled/*; do readlink -f "$f"; done)
for s in $SITES; do cp -a "$s" "$BAK/"; done

for s in $SITES; do
  if grep -q '|affiliate|paywall-gallery)(/|\$) {' "$s"; then
    sed -i 's#|affiliate|paywall-gallery)(/|\$) {#|affiliate|r|paywall-gallery)(/|$) {#' "$s"
    echo "$(basename "$s"): /r/ now routed to the shell"
  else
    echo "$(basename "$s"): unchanged"
  fi
done

if ! nginx -t; then
  for s in $SITES; do cp -a "$BAK/$(basename "$s")" "$s"; done
  echo "nginx -t failed; restored from $BAK" >&2
  exit 1
fi
systemctl reload nginx
echo "applied; backup in $BAK"
