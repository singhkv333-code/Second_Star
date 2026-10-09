#!/usr/bin/env python3
"""Checksum-verified, explicit-file patch delivery to the isolated clean VM.

No traffic changes, service restarts, shell credentials, or source from the
old machine. Previous file contents are retained beside each patched file.
"""
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
FILES = (
    # This patched dependency is intentionally ignored by Git, so a clean
    # source archive alone cannot render a chart. Ship the verified local copy.
    'charto/preview/vendor/lightweight-charts.standalone.production.js',
    'charto/data/dataserver.py', 'charto/data/kite_stream.py',
    'charto/deploy/finalize_clean_universe.py',
    'charto/deploy/activate_clean_data.sh',
    'charto/deploy/catch_up_clean_universe.py',
    'charto/deploy/snapshot_users_for_cutover.py',
    'charto/deploy/retain_legacy_equities.py',
)


def az(*args):
    p = subprocess.run(['az', *args, '-o', 'json'], capture_output=True, text=True)
    if p.returncode:
        if args[:3] == ('vm', 'run-command', 'create'):
            # This command contains code/checksums only, never credentials.
            print(p.stderr[-2000:])
        raise RuntimeError('Azure operation failed; no secret output forwarded')
    return json.loads(p.stdout) if p.stdout.strip() else None


def main():
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode='w:gz') as archive:
        for relative in FILES:
            path = ROOT / relative
            if not path.is_file() or path.is_symlink():
                raise RuntimeError('Missing or unsafe explicit patch file')
            body = path.read_bytes()
            info = tarfile.TarInfo(relative)
            info.size, info.mode = len(body), 0o644
            archive.addfile(info, io.BytesIO(body))
    payload = data.getvalue()
    digest = hashlib.sha256(payload).hexdigest()
    blob = f'clean-cutover/{digest}/runtime-patch.tgz'
    bearer = az('account', 'get-access-token', '--resource', 'https://storage.azure.com/')['accessToken']
    url = 'https://pivotmarketdata.blob.core.windows.net/charto-jobs/' + blob
    request = urllib.request.Request(url, data=payload, method='PUT', headers={
        'Authorization': 'Bearer ' + bearer, 'x-ms-version': '2023-11-03',
        'x-ms-blob-type': 'BlockBlob', 'If-None-Match': '*',
    })
    with urllib.request.urlopen(request, timeout=120) as response:
        if response.status != 201:
            raise RuntimeError('Artifact upload not confirmed')
    script = f'''python3 - <<'PY'
import hashlib, io, json, os, pathlib, tarfile, urllib.request
req = urllib.request.Request('http://169.254.169.254/metadata/instance?api-version=2021-02-01', headers={{'Metadata':'true'}})
if json.load(urllib.request.urlopen(req, timeout=10))['compute']['name'] != 'pivot-clean-stage':
    raise SystemExit('Refusing patch on another VM')
imds = 'http://169.254.169.254/metadata/identity/oauth2/token?api-version=2018-02-01&resource=https://storage.azure.com/'
token = json.load(urllib.request.urlopen(urllib.request.Request(imds, headers={{'Metadata':'true'}}), timeout=10))['access_token']
req = urllib.request.Request({url!r}, headers={{'Authorization':'Bearer '+token, 'x-ms-version':'2023-11-03'}})
payload = urllib.request.urlopen(req, timeout=120).read()
if hashlib.sha256(payload).hexdigest() != {digest!r}:
    raise SystemExit('Artifact checksum mismatch')
release = pathlib.Path('/etc/pivot/stage-release').read_text().strip()
allowed = {list(FILES)!r}
with tarfile.open(fileobj=io.BytesIO(payload), mode='r:gz') as archive:
    members = archive.getmembers()
    if sorted(m.name for m in members) != sorted(allowed) or any(not m.isfile() for m in members):
        raise SystemExit('Unexpected patch members')
    for member in members:
        target = pathlib.Path(release) / member.name
        if target.is_symlink():
            raise SystemExit('Unexpected source symlink')
        if target.exists():
            backup = target.with_name(target.name + '.before-{digest[:16]}')
            if not backup.exists():
                backup.write_bytes(target.read_bytes()); backup.chmod(0o644)
        pending = target.with_name(target.name + '.patch-{digest[:16]}')
        pending.write_bytes(archive.extractfile(member).read()); pending.chmod(0o644)
        os.replace(pending, target)
print('EXPLICIT_PATCH_VERIFIED', {digest!r}, len(allowed))
PY'''
    name = 'pivot-clean-patch-' + digest[:16]
    az('vm', 'run-command', 'create', '-g', 'PIVOT', '--vm-name', 'pivot-clean-stage',
       '--run-command-name', name, '--location', 'centralindia',
       '--async-execution', 'true', '--script', script)
    print(json.dumps({'command': name, 'sha256': digest, 'bytes': len(payload)}))


if __name__ == '__main__':
    main()
