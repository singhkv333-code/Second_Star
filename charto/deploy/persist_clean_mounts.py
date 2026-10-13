#!/usr/bin/env python3
"""Persist the verified data mounts; fail closed if the data disk is absent."""
import json
from pathlib import Path
import subprocess
import urllib.request

DATA_UUID = '8c8176ad-3cf0-41ec-bddc-5ab34c67b0ff'


def main():
    request = urllib.request.Request('http://169.254.169.254/metadata/instance?api-version=2021-02-01',
                                     headers={'Metadata': 'true'})
    if json.load(urllib.request.urlopen(request, timeout=10))['compute']['name'] != 'pivot-clean-stage':
        raise SystemExit('Wrong VM; refusing mount changes')
    source = subprocess.check_output(['findmnt', '-n', '-o', 'SOURCE', '/mnt/pivot-old-data'], text=True).strip()
    actual = subprocess.check_output(['blkid', '-s', 'UUID', '-o', 'value', source], text=True).strip()
    if actual != DATA_UUID:
        raise SystemExit('Data disk UUID differs; refusing mount changes')
    subprocess.run(['mountpoint', '-q', '/srv/pivot-data'], check=True)
    path = Path('/etc/fstab')
    before = path.read_text()
    backup = Path('/etc/fstab.before-pivot-clean-data')
    if not backup.exists():
        backup.write_text(before)
    additions = []
    if not any(line.strip() and not line.startswith('#') and
               line.split()[1] == '/mnt/pivot-old-data' for line in before.splitlines()):
        additions.append(f'UUID={DATA_UUID} /mnt/pivot-old-data ext4 defaults,noexec,nosuid,nodev 0 2')
    if not any(line.strip() and not line.startswith('#') and
               line.split()[1] == '/srv/pivot-data' for line in before.splitlines()):
        additions.append('/mnt/pivot-old-data/serving /srv/pivot-data none bind,noexec,nosuid,nodev,x-systemd.requires=/mnt/pivot-old-data 0 0')
    if additions:
        path.write_text(before.rstrip() + '\n' + '\n'.join(additions) + '\n')
    for unit in ('charto', 'pivot-api', 'pivot-next', 'charto-web'):
        dropin = Path('/etc/systemd/system') / (unit + '.service.d')
        dropin.mkdir(exist_ok=True)
        (dropin / 'data-mount.conf').write_text('[Unit]\nRequiresMountsFor=/srv/pivot-data\nConditionPathIsMountPoint=/srv/pivot-data\n')
    subprocess.run(['findmnt', '--verify', '--tab-file', '/etc/fstab'], check=True)
    subprocess.run(['systemctl', 'daemon-reload'], check=True)
    print('PERSISTENT_DATA_MOUNTS_VERIFIED; MISSING_DISK_FAILS_CLOSED')


if __name__ == '__main__':
    main()
