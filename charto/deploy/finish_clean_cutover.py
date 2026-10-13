#!/usr/bin/env python3
"""Essential release gate, consistent state handoff, reversible IP cutover.

Operator-only, explicit old/new Azure resources. Does not rotate shared
credentials, delete disks, push Git, or enable broker order execution.
"""
import argparse
import base64
import json
from pathlib import Path
import subprocess
import time
import urllib.parse
import urllib.request

RG = 'PIVOT'
OLD = 'Claudecodeforpivot'
NEW = 'pivot-clean-stage'
PUBLIC = 'Claudecodeforpivot-ip'
CHECK = 'https://pivot-clean-check-20261008.centralindia.cloudapp.azure.com'
LIVE = 'https://pivot-india.centralindia.cloudapp.azure.com'


def az(*args):
    p = subprocess.run(['az', *args, '-o', 'json'], capture_output=True, text=True)
    if p.returncode:
        raise RuntimeError('Azure operation failed: ' + ' '.join(args[:3]))
    return json.loads(p.stdout) if p.stdout.strip() else None


def guest(vm, name, script):
    az('vm', 'run-command', 'create', '-g', RG, '--vm-name', vm,
       '--run-command-name', name, '--location', 'centralindia',
       '--async-execution', 'true', '--script', script)
    deadline = time.monotonic() + 600
    while time.monotonic() < deadline:
        state = az('vm', 'run-command', 'show', '-g', RG, '--vm-name', vm,
                   '--run-command-name', name, '--expand', 'instanceView').get('instanceView', {})
        if state.get('executionState') == 'Succeeded':
            return state.get('output', '')
        if state.get('executionState') in ('Failed', 'TimedOut', 'Canceled'):
            raise RuntimeError('Guest operation failed; inspect named command ' + name)
        time.sleep(10)
    raise RuntimeError('Guest operation still pending; inspect ' + name)


def check(base):
    with urllib.request.urlopen(base + '/health', timeout=20) as r:
        if not json.load(r).get('ready'):
            raise RuntimeError('Chart readiness gate failed')
    with urllib.request.urlopen(base + '/symbols', timeout=30) as r:
        inventory = json.load(r)
    if len(inventory.get('hydrated', [])) < 5900:
        raise RuntimeError('Expanded minute inventory not ready')
    bse = next((s for s in inventory['hydrated'] if s.startswith('BSE:')), None)
    if bse is None:
        raise RuntimeError('No minute-ready BSE identity')
    for symbol in ('RELIANCE', 'HDFCBANK', bse, 'NIFTY 50'):
        url = base + '/bars?' + urllib.parse.urlencode(
            {'symbol': symbol, 'interval': '15m', 'limit': 150})
        with urllib.request.urlopen(url, timeout=30) as r:
            data = json.load(r)
        if not data.get('bars'):
            raise RuntimeError('Actual chart bars missing: ' + symbol)
    print('ESSENTIAL_READY_AND_REAL_BAR_GATE_PASSED', base, flush=True)


def handoff(stamp):
    snapshot = Path(__file__).with_name('snapshot_users_for_cutover.py').read_bytes()
    code = base64.b64encode(snapshot).decode()
    output = guest(OLD, 'pivot-final-state-freeze-' + stamp,
                   "python3 - <<'PY'\nimport base64,sys\nsys.argv=['snapshot','--freeze']\nexec(compile(base64.b64decode(" + repr(code) + "),'trusted-state-handoff','exec'))\nPY")
    records = [json.loads(line) for line in output.splitlines() if line.startswith('{"blob"')]
    if len(records) != 1 or not records[0].get('frozen'):
        raise RuntimeError('Frozen state backup not confirmed; old writers may be stopped')
    record = records[0]
    print('FROZEN_FULL_PLANE_BACKUP', json.dumps(record), flush=True)
    activation = f'''python3 - <<'PY'
import gzip,hashlib,json,os,pathlib,sqlite3,subprocess,tempfile,urllib.request
record={record!r}
subprocess.run(['systemctl','stop','charto-backup.timer','charto-backup.service','charto','pivot-api'],check=True)
folder=pathlib.Path(tempfile.mkdtemp(prefix='final-handoff-',dir='/srv/pivot-data'))
imds='http://169.254.169.254/metadata/identity/oauth2/token?api-version=2018-02-01&resource=https://storage.azure.com/'
token=json.load(urllib.request.urlopen(urllib.request.Request(imds,headers={{'Metadata':'true'}}),timeout=15))['access_token']
url='https://pivotmarketdata.blob.core.windows.net/kite-1min/'+record['blob']
packed=urllib.request.urlopen(urllib.request.Request(url,headers={{'Authorization':'Bearer '+token,'x-ms-version':'2023-11-03'}}),timeout=120).read()
if hashlib.sha256(packed).hexdigest()!=record['sha256']: raise SystemExit('State checksum mismatch')
target=folder/'users.db';target.write_bytes(gzip.decompress(packed))
c=sqlite3.connect(target)
if c.execute('PRAGMA integrity_check').fetchone()[0]!='ok': raise SystemExit('State integrity failed')
for table,count in record['counts'].items():
    if c.execute('SELECT COUNT(*) FROM "'+table+'"').fetchone()[0]!=count: raise SystemExit('State count mismatch')
if c.execute("SELECT 1 FROM sqlite_master WHERE type IN ('view','trigger') LIMIT 1").fetchone(): raise SystemExit('Unexpected executable state schema')
c.execute('DELETE FROM sessions');c.commit();c.close()
live=pathlib.Path('/srv/pivot-data/charto_users.db')
for suffix in ('','-wal','-shm'):
    prior=pathlib.Path(str(live)+suffix)
    if prior.exists(): prior.rename(str(prior)+'.before-final-{stamp}')
os.replace(target,live)
subprocess.run(['chown','pivot-data:pivot-runtime',str(live)],check=True);live.chmod(0o660)
for unit in ('charto','pivot-api'):
    path=pathlib.Path('/etc/systemd/system')/(unit+'.service')
    text=path.read_text()
    if 'EnvironmentFile=/etc/pivot/staging-runtime.env' not in text: raise SystemExit('Unexpected user-plane configuration')
    path.write_text(text.replace('EnvironmentFile=/etc/pivot/staging-runtime.env','EnvironmentFile=/etc/pivot/runtime.env'))
subprocess.run(['systemctl','daemon-reload'],check=True)
subprocess.run(['systemctl','start','charto-backup.service'],check=True)
subprocess.run(['systemctl','enable','--now','charto-backup.timer','charto','pivot-api'],check=True)
for attempt in range(12):
    try:
        with urllib.request.urlopen('http://127.0.0.1:5174/health',timeout=10) as r: ready=json.load(r).get('ready')
        if ready: break
    except Exception: ready=False
    __import__('time').sleep(5)
if not ready: raise SystemExit('Final serving readiness failed; public IP not moved')
print('FINAL_USER_PLANE_RESTORED; OLD_SESSIONS_INVALIDATED; NEW_SERVICE_READY')
PY'''
    print(guest(NEW, 'pivot-final-state-activate-' + stamp, activation), flush=True)
    check(CHECK)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--handoff-and-switch', action='store_true')
    args = parser.parse_args()
    check(CHECK)
    if not args.handoff_and_switch:
        return
    stamp = str(int(time.time()))
    # Record exact reversible public-IP ownership before moving it.
    old_config = az('network', 'nic', 'ip-config', 'show', '-g', RG,
                    '--nic-name', 'claudecodeforpivot599', '-n', 'ipconfig1')
    if not old_config.get('publicIPAddress', {}).get('id', '').endswith('/' + PUBLIC):
        raise RuntimeError('Public IP ownership changed; refusing cutover')
    try:
        handoff(stamp)
    except Exception:
        # Restore service even if activation fails before any NIC change.
        # Never let two copies of the real user plane accept writes.
        try:
            guest(NEW, 'pivot-handoff-failure-stop-' + stamp, 'systemctl stop charto pivot-api')
        finally:
            guest(OLD, 'pivot-handoff-failure-start-' + stamp, 'systemctl start charto pivot-api')
        raise
    az('network', 'nic', 'ip-config', 'update', '-g', RG,
       '--nic-name', 'claudecodeforpivot599', '-n', 'ipconfig1', '--remove', 'publicIPAddress')
    try:
        az('network', 'nic', 'ip-config', 'update', '-g', RG,
           '--nic-name', 'pivot-clean-stage-nic', '-n', 'ipconfig1', '--public-ip-address', PUBLIC)
        for attempt in range(6):
            try:
                check(LIVE)
                print('PUBLIC_IP_CUTOVER_VERIFIED; BOTH_VMS_REMAIN_POWERED', flush=True)
                return
            except Exception:
                time.sleep(5)
        raise RuntimeError('Public health did not pass')
    except Exception:
        # Prevent split user-state writes before restoring old serving.
        guest(NEW, 'pivot-cutover-rollback-stop-' + stamp, 'systemctl stop charto pivot-api')
        az('network', 'nic', 'ip-config', 'update', '-g', RG,
           '--nic-name', 'pivot-clean-stage-nic', '-n', 'ipconfig1', '--remove', 'publicIPAddress')
        az('network', 'nic', 'ip-config', 'update', '-g', RG,
           '--nic-name', 'claudecodeforpivot599', '-n', 'ipconfig1', '--public-ip-address', PUBLIC)
        guest(OLD, 'pivot-cutover-rollback-start-' + stamp, 'systemctl start charto pivot-api')
        raise RuntimeError('Cutover failed; old serving restored, new data preserved') from None


if __name__ == '__main__':
    main()
