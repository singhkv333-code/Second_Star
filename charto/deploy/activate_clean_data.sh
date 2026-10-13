#!/usr/bin/env bash
# Private activation only. Traffic and live-feed cutover remain explicit.
set -euo pipefail
release="$(tr -d '\n' < /etc/pivot/stage-release)"
while systemctl is-active --quiet pivot-clean-universe-v3; do sleep 15; done
test "$(systemctl show pivot-clean-universe-v3 -p Result --value)" = success
retention_flags=()
if [ "${PIVOT_DEFER_LEGACY_HISTORY:-0}" = 1 ]; then
  retention_flags+=(--defer-legacy)
fi
python3 "$release/charto/deploy/finalize_clean_universe.py" \
  --old /mnt/pivot-old-data/charto_bars.db \
  --new /srv/pivot-data/charto_bars.db \
  --audit /srv/pivot-data/source-audit.json \
  --report /srv/pivot-data/serving-universe.json "${retention_flags[@]}"
chown pivot-data:pivot-runtime /srv/pivot-data/charto_bars.db
chmod 0660 /srv/pivot-data/charto_bars.db
systemctl enable --now charto
echo PRIVATE_DATA_SERVICE_STARTED
