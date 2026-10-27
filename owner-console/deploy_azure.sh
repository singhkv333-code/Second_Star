#!/usr/bin/env bash
# Owner console on Azure App Service: create, harden, deploy, and hand the
# shared key to the VM's vault. Every step is safe to re-run.
#
#   ./deploy_azure.sh create      resource group, vault, plan, app, identity, settings, hardening
#   ./deploy_azure.sh deploy      zip app.py + static/ and deploy (Entra auth; basic auth is off)
#   ./deploy_azure.sh vm-secret   write owner-console-runtime to pivot-clean-kv-india (PRODUCTION vault)
#   ./deploy_azure.sh allow-me    let this machine's current address reach the deploy endpoint
#
# Secrets live in ~/.config/pivot-ops (mode 600): password (the passphrase),
# password.hash, ops-key, suffix (the resource name suffix). Nothing secret is
# passed on a command line; Key Vault reads them from files.
set -euo pipefail

CONF="$HOME/.config/pivot-ops"
SUF="$(cat "$CONF/suffix")"
RG=pivot-ops
LOC=centralindia
KV="pivot-ops-kv-$SUF"
PLAN=pivot-ops-plan
APP="pivot-owner-$SUF"
HOST="$APP.azurewebsites.net"
HERE="$(cd "$(dirname "$0")" && pwd)"

# Homebrew's az crashes loading pyexpat against the system libexpat; /usr/bin/env
# strips DYLD_* (SIP), so call the CLI's own python with Homebrew's expat.
az() {
  local py
  py="$(ls -d /opt/homebrew/Cellar/azure-cli/*/libexec/bin/python 2>/dev/null | tail -1)"
  if [ -n "$py" ]; then DYLD_LIBRARY_PATH=/opt/homebrew/opt/expat/lib "$py" -m azure.cli "$@"; else command az "$@"; fi
}

create() {
  local me kvid pid
  me="$(az ad signed-in-user show --query id -o tsv)"
  az group create -n "$RG" -l "$LOC" --tags purpose=owner-console -o none
  az keyvault show -n "$KV" -o none 2>/dev/null || az keyvault create -n "$KV" -g "$RG" -l "$LOC" \
    --enable-rbac-authorization true --retention-days 90 --enable-purge-protection true -o none
  kvid="$(az keyvault show -n "$KV" --query id -o tsv)"
  az role assignment create --assignee-object-id "$me" --assignee-principal-type User \
    --role "Key Vault Secrets Officer" --scope "$kvid" -o none
  az appservice plan show -n "$PLAN" -g "$RG" -o none 2>/dev/null || \
    az appservice plan create -n "$PLAN" -g "$RG" -l "$LOC" --is-linux --sku B1 -o none
  az webapp show -n "$APP" -g "$RG" -o none 2>/dev/null || \
    az webapp create -n "$APP" -g "$RG" -p "$PLAN" --runtime "PYTHON:3.12" --https-only true -o none
  az webapp identity assign -n "$APP" -g "$RG" -o none
  pid="$(az webapp show -n "$APP" -g "$RG" --query identity.principalId -o tsv)"
  az role assignment create --assignee-object-id "$pid" --assignee-principal-type ServicePrincipal \
    --role "Key Vault Secrets User" --scope "$kvid" -o none
  tr -d '\n' < "$CONF/password.hash" > "$CONF/password.hash.raw"
  tr -d '\n' < "$CONF/ops-key" > "$CONF/ops-key.raw"
  az keyvault secret set --vault-name "$KV" -n ops-password-hash --file "$CONF/password.hash.raw" --encoding utf-8 -o none
  az keyvault secret set --vault-name "$KV" -n ops-hmac-key --file "$CONF/ops-key.raw" --encoding utf-8 -o none
  az webapp config appsettings set -n "$APP" -g "$RG" -o none --settings \
    "OPS_PASSWORD_HASH=@Microsoft.KeyVault(VaultName=$KV;SecretName=ops-password-hash)" \
    "OPS_HMAC_KEY=@Microsoft.KeyVault(VaultName=$KV;SecretName=ops-hmac-key)" \
    "OPS_UPSTREAM=https://app.pivotnow.in" "OPS_ALLOWED_HOSTS=$HOST" "OPS_ACTOR=owner" \
    "SCM_DO_BUILD_DURING_DEPLOYMENT=true" "WEBSITES_PORT=8000" "PYTHONDONTWRITEBYTECODE=1"
  az webapp config set -n "$APP" -g "$RG" -o none --startup-file "gunicorn -c gunicorn.conf.py app:app" \
    --min-tls-version 1.3 --ftps-state Disabled --http20-enabled true --always-on true \
    --remote-debugging-enabled false
  az webapp update -n "$APP" -g "$RG" --client-affinity-enabled false --https-only true -o none
  for kind in ftp scm; do
    az resource update -g "$RG" --namespace Microsoft.Web --resource-type basicPublishingCredentialsPolicies \
      --parent "sites/$APP" -n "$kind" --set properties.allow=false -o none
  done
  allow_me
  az webapp show -n "$APP" -g "$RG" --query possibleOutboundIpAddresses -o tsv | tr ',' '\n' | sort -u | paste -sd, - > "$CONF/outbound-ips"
  echo "created https://$HOST"
}

allow_me() {
  local ip
  ip="$(curl -s https://api.ipify.org)"
  az webapp config access-restriction remove -n "$APP" -g "$RG" --scm-site true --rule-name owner-laptop -o none 2>/dev/null || true
  az webapp config access-restriction add -n "$APP" -g "$RG" --scm-site true --rule-name owner-laptop \
    --action Allow --ip-address "$ip/32" --priority 100 -o none
  az webapp config access-restriction set -n "$APP" -g "$RG" --scm-site true --default-action Deny -o none
  echo "deploy endpoint open to $ip only"
}

deploy() {
  local zipf
  zipf="$(mktemp -d)/owner-console.zip"
  (cd "$HERE" && zip -q -r "$zipf" app.py gunicorn.conf.py requirements.txt static -x "*.pyc" "__pycache__/*")
  az webapp deploy -n "$APP" -g "$RG" --src-path "$zipf" --type zip --async false -o none
  for _ in $(seq 1 20); do
    [ "$(curl -s -o /dev/null -w '%{http_code}' "https://$HOST/healthz")" = 200 ] && { echo "live https://$HOST"; return; }
    sleep 6
  done
  echo "deployed, but /healthz is not answering yet" >&2
  return 1
}

vm_secret() {
  # The VM reads this with its own identity (activate_owner_console.py).
  local tmp
  tmp="$(mktemp)"; chmod 600 "$tmp"
  python3 - "$CONF/ops-key" "$CONF/outbound-ips" > "$tmp" <<'PY'
import json, sys
key = open(sys.argv[1]).read().strip()
ips = open(sys.argv[2]).read().strip()
print(json.dumps({"PIVOT_OPS_KEY": key, "PIVOT_OPS_ALLOWED_IPS": ips}), end="")
PY
  az keyvault secret set --vault-name pivot-clean-kv-india -n owner-console-runtime --file "$tmp" --encoding utf-8 -o none
  rm -f "$tmp"
  echo "owner-console-runtime written to pivot-clean-kv-india"
}

case "${1:-}" in
  create) create ;;
  deploy) deploy ;;
  vm-secret) vm_secret ;;
  allow-me) allow_me ;;
  *) sed -n '2,12p' "$0"; exit 2 ;;
esac
