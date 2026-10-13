#!/usr/bin/env bash
# Stage only. Does not touch production traffic, data disks or credentials.
set -euo pipefail
umask 077
: "${PIVOT_ARTIFACT:?private blob name required}"
: "${PIVOT_ARTIFACT_SHA256:?expected source checksum required}"

python3 - <<'PY'
import json, urllib.request
url = 'http://169.254.169.254/metadata/instance?api-version=2021-02-01'
req = urllib.request.Request(url, headers={'Metadata': 'true'})
name = json.load(urllib.request.urlopen(req, timeout=10))['compute']['name']
if name != 'pivot-clean-stage':
    raise SystemExit('Refusing to bootstrap outside the isolated staging VM')
PY

export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get upgrade -y -qq
apt-get install -y -qq python3-venv python3-dev build-essential libpq-dev \
  nodejs npm nginx curl ca-certificates git unzip certbot python3-certbot-nginx
systemctl stop nginx

for account in pivot-build pivot-data pivot-web pivot-api; do
  id "$account" >/dev/null 2>&1 || useradd --system --create-home \
    --shell /usr/sbin/nologin "$account"
done
install -d -m 0755 /opt/pivot/releases
install -d -m 0700 /etc/pivot

python3 - <<'PY'
import hashlib, json, os, pathlib, tarfile, tempfile, urllib.request
expected = os.environ['PIVOT_ARTIFACT_SHA256']
if len(expected) != 64 or any(c not in '0123456789abcdef' for c in expected):
    raise SystemExit('Invalid checksum')
release = pathlib.Path('/opt/pivot/releases') / expected[:16]
if release.exists():
    raise SystemExit('Release directory exists; inspect it before retrying')
name = os.environ['PIVOT_ARTIFACT']
if not name.startswith('clean-cutover/') or '..' in name:
    raise SystemExit('Invalid artifact name')
imds = ('http://169.254.169.254/metadata/identity/oauth2/token?'
        'api-version=2018-02-01&resource=https://storage.azure.com/')
token = json.load(urllib.request.urlopen(urllib.request.Request(imds,
    headers={'Metadata': 'true'}), timeout=10))['access_token']
url = 'https://pivotmarketdata.blob.core.windows.net/charto-jobs/' + name
req = urllib.request.Request(url, headers={'Authorization': 'Bearer ' + token,
    'x-ms-version': '2023-11-03'})
with tempfile.TemporaryDirectory(prefix='pivot-source-') as tmp:
    archive = pathlib.Path(tmp) / 'source.tgz'
    digest = hashlib.sha256()
    with urllib.request.urlopen(req, timeout=120) as response, archive.open('wb') as out:
        while chunk := response.read(1024 * 1024):
            digest.update(chunk); out.write(chunk)
    if digest.hexdigest() != expected:
        raise SystemExit('Source checksum mismatch')
    with tarfile.open(archive) as source:
        for item in source.getmembers():
            p = pathlib.PurePosixPath(item.name)
            if p.is_absolute() or '..' in p.parts or item.issym() or item.islnk():
                raise SystemExit('Unsafe archive member: ' + item.name)
            if not (item.isfile() or item.isdir()):
                raise SystemExit('Unsupported archive member')
        release.mkdir()
        source.extractall(release, filter='data')
pathlib.Path('/etc/pivot/stage-release').write_text(str(release) + '\n')
print('CHECKED_SOURCE', expected)
PY

release="$(tr -d '\n' < /etc/pivot/stage-release)"
# Git ignores the patched chart library. The clean artifact must carry it;
# otherwise a successful frontend build still serves a permanently blank chart.
test -s "$release/charto/preview/vendor/lightweight-charts.standalone.production.js"
python3 "$release/charto/preview/patch-vendor.py"
chown -R pivot-build:pivot-build "$release"
npm install -g pnpm@10.33.2 --prefix /usr/local --ignore-scripts --no-audit --no-fund
chmod a+rx /usr/local /usr/local/lib /usr/local/lib/node_modules /usr/local/bin
chmod -R a+rX /usr/local/lib/node_modules/pnpm
install -d -o pivot-build -g pivot-build /opt/pivot/venv
runuser -u pivot-build -- python3 -m venv /opt/pivot/venv
runuser -u pivot-build -- /opt/pivot/venv/bin/pip install --disable-pip-version-check \
  -r "$release/pivot/requirements.txt"

for app in pivot-next charto/web; do
  runuser -u pivot-build -- bash -c 'cd "$1" && /usr/local/bin/pnpm install --frozen-lockfile --ignore-scripts' \
    bash "$release/$app"
  runuser -u pivot-build -- env NEXT_TELEMETRY_DISABLED=1 \
    PIVOT_BACKEND_ORIGIN=http://127.0.0.1:8000 \
    CHARTO_BACKEND=http://127.0.0.1:5174 \
    NEXT_PUBLIC_PIVOT_API_BASE=/pv/api \
    NEXT_PUBLIC_PIVOT_WS_BASE=wss://pivot-india.centralindia.cloudapp.azure.com/pv/api \
    NODE_OPTIONS=--max-old-space-size=3072 \
    bash -c 'cd "$1" && /usr/local/bin/pnpm exec next build' bash "$release/$app"
  test -f "$release/$app/.next/standalone/server.js"
  cp -a "$release/$app/.next/static" "$release/$app/.next/standalone/.next/static"
  if [ -d "$release/$app/public" ]; then
    cp -a "$release/$app/public" "$release/$app/.next/standalone/public"
  fi
done
chown -R root:root "$release" /opt/pivot/venv
chmod -R a+rX "$release" /opt/pivot/venv
chmod -R go-w "$release" /opt/pivot/venv
printf '%s\n' 'source-and-builds-ready; no traffic or credentials activated' \
  > /etc/pivot/stage-status
echo STAGE_BUILDS_READY
