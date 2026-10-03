#!/usr/bin/env bash
# Build and run the Browser widget's back half on Azure, apart from the VM:
#
#   rb       charto/browser — a real Chromium per widget, streamed (port 5177)
#   searxng  charto/browser/searxng — the search engine API (port 8080)
#
# Both run in ONE Azure Container Instances group on its own subnet of
# pivot-vnet with a private address only. The VM's nginx proxies /rb/ to it
# and the dataserver calls SearXNG on it; nothing on the internet can reach it.
# The subnet's NSG lets in the VM alone and refuses outbound traffic to the
# rest of the VNet, so a page in the browser cannot reach the VM or anything
# else private (egress.py refuses private addresses as well).
#
# Images are built IN Azure (az acr build): no local Docker.
#
# Idempotent. Run from a machine with `az` signed in:
#   bash charto/deploy/provision_browser.sh
# It prints the group's private IP; nginx-charto.conf and pivot/.env on the VM
# carry it (SEARXNG_URL, the /rb/ upstream).
set -euo pipefail
RG="${RG:-PIVOT}"
LOC="${LOC:-centralindia}"
ACR="${ACR:-pivotchartoacr}"
VNET="${VNET:-pivot-vnet}"
SUBNET="${SUBNET:-charto-browser}"
PREFIX="${PREFIX:-10.0.1.0/27}"
NSG="${NSG:-charto-browser-nsg}"
GROUP="${GROUP:-charto-browser}"
VM_IP="${VM_IP:-10.0.0.4}"
TAG="${TAG:-$(git -C "$(dirname "$0")/../.." rev-parse --short HEAD)}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
SECRET_FILE="${SECRET_FILE:-$HOME/.charto-browser-secret}"

say() { printf 'browser: %s\n' "$*"; }

az acr show -n "$ACR" -g "$RG" >/dev/null 2>&1 || {
  say "creating registry $ACR (Basic)"
  az acr create -n "$ACR" -g "$RG" -l "$LOC" --sku Basic --admin-enabled true -o none
}

say "building images in Azure ($TAG)"
az acr build -r "$ACR" -t "rb:$TAG" -t rb:latest "$HERE/browser" -o none --no-logs
az acr build -r "$ACR" -t "searxng:$TAG" -t searxng:latest --build-arg "SEARXNG_SECRET=$(openssl rand -hex 24)" \
  "$HERE/browser/searxng" -o none --no-logs

az network nsg show -g "$RG" -n "$NSG" >/dev/null 2>&1 || {
  say "creating NSG $NSG"
  az network nsg create -g "$RG" -n "$NSG" -l "$LOC" -o none
  az network nsg rule create -g "$RG" --nsg-name "$NSG" -n allow-vm-in --priority 100 --direction Inbound \
    --access Allow --protocol Tcp --source-address-prefixes "$VM_IP" --destination-port-ranges 5177 8080 -o none
  az network nsg rule create -g "$RG" --nsg-name "$NSG" -n deny-vnet-in --priority 200 --direction Inbound \
    --access Deny --protocol '*' --source-address-prefixes VirtualNetwork --destination-port-ranges '*' -o none
  az network nsg rule create -g "$RG" --nsg-name "$NSG" -n deny-vnet-out --priority 100 --direction Outbound \
    --access Deny --protocol '*' --destination-address-prefixes VirtualNetwork --destination-port-ranges '*' -o none
}

az network vnet subnet show -g "$RG" --vnet-name "$VNET" -n "$SUBNET" >/dev/null 2>&1 || {
  say "creating subnet $SUBNET ($PREFIX)"
  az network vnet subnet create -g "$RG" --vnet-name "$VNET" -n "$SUBNET" --address-prefixes "$PREFIX" \
    --delegations Microsoft.ContainerInstance/containerGroups --network-security-group "$NSG" \
    --default-outbound true -o none
}

[ -s "$SECRET_FILE" ] || { umask 077; openssl rand -base64 36 | tr -d '\n' > "$SECRET_FILE"; }
SECRET="$(cat "$SECRET_FILE")"
USER_="$(az acr credential show -n "$ACR" --query username -o tsv)"
PASS_="$(az acr credential show -n "$ACR" --query 'passwords[0].value' -o tsv)"

say "creating container group $GROUP"
cat > /tmp/charto-browser-aci.yaml <<YAML
apiVersion: '2023-05-01'
location: $LOC
name: $GROUP
properties:
  osType: Linux
  restartPolicy: Always
  subnetIds:
    - id: $(az network vnet subnet show -g "$RG" --vnet-name "$VNET" -n "$SUBNET" --query id -o tsv)
  ipAddress:
    type: Private
    ports:
      - { protocol: TCP, port: 5177 }
      - { protocol: TCP, port: 8080 }
  imageRegistryCredentials:
    - { server: $ACR.azurecr.io, username: $USER_, password: $PASS_ }
  containers:
    - name: rb
      properties:
        image: $ACR.azurecr.io/rb:$TAG
        ports: [ { port: 5177 } ]
        resources: { requests: { cpu: 1.0, memoryInGB: 2.5 } }
        environmentVariables:
          - { name: RB_MAX_SESSIONS, value: '3' }
          - { name: CHARTO_BROWSER_SECRET, secureValue: '$SECRET' }
    - name: searxng
      properties:
        image: $ACR.azurecr.io/searxng:$TAG
        ports: [ { port: 8080 } ]
        resources: { requests: { cpu: 0.5, memoryInGB: 1.0 } }
        environmentVariables:
          - { name: SEARXNG_BASE_URL, value: 'http://searxng.internal/' }
YAML
az container create -g "$RG" -f /tmp/charto-browser-aci.yaml -o none
rm -f /tmp/charto-browser-aci.yaml
IP="$(az container show -g "$RG" -n "$GROUP" --query ipAddress.ip -o tsv)"
say "running at $IP (rb :5177, searxng :8080)"
