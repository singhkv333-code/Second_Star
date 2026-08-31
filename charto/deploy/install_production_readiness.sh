#!/usr/bin/env bash
# Install the production-only Charto service, backup, and nginx controls.
# Run on the VM after the code commit is present in /data/app.
set -euo pipefail

[ "$(id -u)" -eq 0 ] || {
  echo "run as root: sudo /data/app/charto/deploy/install_production_readiness.sh" >&2
  exit 1
}

REPO="${CHARTO_REPO:-/data/app}"
DEPLOY="$REPO/charto/deploy"
PYTHON_BIN="${CHARTO_PYTHON:-/data/venv/bin/python}"

install -m 0644 "$DEPLOY/charto-backup.service" /etc/systemd/system/charto-backup.service
install -m 0644 "$DEPLOY/charto-backup.timer" /etc/systemd/system/charto-backup.timer
install -m 0644 "$DEPLOY/nginx-ratelimit.conf" /etc/nginx/conf.d/charto-ratelimit.conf
install -m 0644 "$DEPLOY/nginx-charto.conf" /etc/nginx/sites-available/charto
ln -sfn /etc/nginx/sites-available/charto /etc/nginx/sites-enabled/charto

systemctl daemon-reload
systemctl enable --now charto-backup.timer

# A fresh remote backup must exist before the service can honestly report
# ready. Starting the one-shot synchronously also exposes any managed-identity
# or storage-role failure during installation instead of an hour later.
systemctl start charto-backup.service
systemctl restart charto.service

nginx -t
systemctl reload nginx

curl -fsS --max-time 15 http://127.0.0.1:5174/health
echo
if ! curl -fsS --max-time 30 'http://127.0.0.1:5174/health?deep=1'; then
  echo "deep readiness failed; provision/fix the Execution backend, then rerun this installer" >&2
  echo "expected provisioner: $DEPLOY/provision_execution.sh" >&2
  exit 1
fi
echo
"$DEPLOY/verify_backup_restore.sh"
"$DEPLOY/check_routes.sh"
"$PYTHON_BIN" "$DEPLOY/load_check.py" \
  --base-url http://127.0.0.1:5174 --levels 1,10,20,40 --max-p95 20

echo "production readiness controls installed and verified"
