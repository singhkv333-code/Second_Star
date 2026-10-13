#!/usr/bin/env bash
set -euo pipefail
release="$(tr -d '\n' < /etc/pivot/stage-release)"
chmod a+rx /usr/local /usr/local/lib /usr/local/lib/node_modules /usr/local/bin
chmod -R a+rX /usr/local/lib/node_modules/pnpm
runuser -u pivot-build -- /usr/local/bin/pnpm --version
for app in pivot-next charto/web; do
  runuser -u pivot-build -- bash -c 'cd "$1" && /usr/local/bin/pnpm install --frozen-lockfile --ignore-scripts' bash "$release/$app"
  runuser -u pivot-build -- env NEXT_TELEMETRY_DISABLED=1 \
    PIVOT_BACKEND_ORIGIN=http://127.0.0.1:8000 \
    CHARTO_BACKEND=http://127.0.0.1:5174 \
    NEXT_PUBLIC_PIVOT_API_BASE=/pv/api \
    NEXT_PUBLIC_PIVOT_WS_BASE=wss://pivot-india.centralindia.cloudapp.azure.com/pv/api \
    NODE_OPTIONS=--max-old-space-size=3072 \
    bash -c 'cd "$1" && /usr/local/bin/pnpm exec next build' bash "$release/$app"
  test -f "$release/$app/.next/standalone/server.js"
  cp -a "$release/$app/.next/static" "$release/$app/.next/standalone/.next/static"
  if test -d "$release/$app/public"; then
    cp -a "$release/$app/public" "$release/$app/.next/standalone/public"
  fi
done
chown -R root:root "$release" /opt/pivot/venv
chmod -R a+rX "$release" /opt/pivot/venv
chmod -R go-w "$release" /opt/pivot/venv
echo source-and-builds-ready > /etc/pivot/stage-status
