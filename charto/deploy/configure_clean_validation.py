#!/usr/bin/env python3
"""Install the temporary validation vhost and the clean-host backup timer."""
import json
from pathlib import Path
import subprocess
import urllib.request

CHECK_HOST = 'pivot-clean-check-20261008.centralindia.cloudapp.azure.com'
SERVING_HOST = 'pivot-india.centralindia.cloudapp.azure.com'


def main():
    req = urllib.request.Request('http://169.254.169.254/metadata/instance?api-version=2021-02-01',
                                 headers={'Metadata': 'true'})
    if json.load(urllib.request.urlopen(req, timeout=10))['compute']['name'] != 'pivot-clean-stage':
        raise SystemExit('Refusing configuration on another VM')
    release = Path('/etc/pivot/stage-release').read_text().strip()
    production = Path('/etc/nginx/sites-available/pivot-clean').read_text()
    # Copy existing route handling, not a second hand-maintained allowlist.
    tls = production.split('server {', 1)[1].split('\nserver {', 1)[0]
    validation = 'server {' + tls
    validation = validation.replace(SERVING_HOST, CHECK_HOST)
    site = Path('/etc/nginx/sites-available/pivot-clean-validation')
    if site.exists() and site.read_text() != validation:
        raise SystemExit('Unexpected validation configuration; refusing overwrite')
    site.write_text(validation)
    enabled = Path('/etc/nginx/sites-enabled/pivot-clean-validation')
    if not enabled.is_symlink():
        enabled.symlink_to(site)
    Path('/etc/nginx/conf.d/pivot-clean-server-names.conf').write_text(
        'server_names_hash_bucket_size 128;\n')
    subprocess.run(['nginx', '-t'], check=True)
    subprocess.run(['systemctl', 'reload', 'nginx'], check=True)
    service = f'''[Unit]
Description=Pivot clean host verified user-state backup
After=network-online.target
RequiresMountsFor=/srv/pivot-data
[Service]
Type=oneshot
User=pivot-data
Group=pivot-runtime
EnvironmentFile=/etc/pivot/runtime.env
ExecStart=/bin/bash {release}/charto/deploy/backup_users.sh
TimeoutStartSec=10min
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=tmpfs
ProtectSystem=strict
ReadWritePaths=/srv/pivot-data
InaccessiblePaths=/mnt /root /var/lib/waagent /var/lib/cloud
'''
    Path('/etc/systemd/system/charto-backup.service').write_text(service)
    Path('/etc/systemd/system/charto-backup.timer').write_text(
        Path(release + '/charto/deploy/charto-backup.timer').read_text())
    subprocess.run(['systemctl', 'daemon-reload'], check=True)
    subprocess.run(['systemctl', 'enable', '--now', 'charto-backup.timer'], check=True)
    subprocess.run(['systemctl', 'start', 'charto-backup.service'], check=True)
    print('VALIDATION_VHOST_AND_REMOTE_BACKUP_STARTED')


if __name__ == '__main__':
    main()
