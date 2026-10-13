#!/usr/bin/env python3
"""Clean-host preparation only. Does not switch public traffic or start feeds."""
import grp
import json
import os
from pathlib import Path
import pwd
import subprocess
import urllib.request

RELEASE = Path('/etc/pivot/stage-release').read_text().strip()
DATA = '/srv/pivot-data'


def main():
    subprocess.run(['groupadd', '--system', 'pivot-runtime'], check=False, capture_output=True)
    for user in ('pivot-data', 'pivot-web', 'pivot-api'):
        subprocess.run(['usermod', '-aG', 'pivot-runtime', user], check=True)
    gid = grp.getgrnam('pivot-runtime').gr_gid
    os.chown('/etc/pivot', 0, gid)
    os.chmod('/etc/pivot', 0o750)
    token_url = ('http://169.254.169.254/metadata/identity/oauth2/token?'
                 'api-version=2018-02-01&resource=https://vault.azure.net')
    token = json.load(urllib.request.urlopen(urllib.request.Request(token_url, headers={'Metadata': 'true'}), timeout=15))['access_token']
    names = ['database-url', 'financials-dsn', 'enrich-dsn', 'azure-key', 'azure-openai-endpoint',
             'azure-openai-legacy-endpoint', 'azure-project-endpoint', 'kite-api-key', 'kite-api-secret',
             'kite-token-enc-key', 'google-client-id', 'jwt-secret-key', 'redis-url']
    values = {}
    for name in names:
        request = urllib.request.Request(f'https://pivot-clean-kv-india.vault.azure.net/secrets/{name}?api-version=7.4',
                                         headers={'Authorization': 'Bearer ' + token})
        value = json.load(urllib.request.urlopen(request, timeout=20))['value']
        if '\n' in value or '\r' in value:
            raise RuntimeError('Multiline configuration refused')
        values[name.replace('-', '_').upper()] = value
    values.update(APP_ENV='production', FRONTEND_URL='https://pivot-india.centralindia.cloudapp.azure.com',
                  BACKEND_URL='https://pivot-india.centralindia.cloudapp.azure.com/pv',
                  ALLOWED_ORIGINS='https://pivot-india.centralindia.cloudapp.azure.com',
                  BACKGROUND_JOBS_ENABLED='false', KITE_TICKER_AUTOSTART='false',
                  DEMO_SEED_ON_REGISTER='0',
                  DEV_AUTH_BYPASS='false', LIVE_EXECUTION_ENABLED='false', AUTO_EXECUTE_ENABLED='false',
                  CHARTO_DB=f'{DATA}/charto_bars.db', CHARTO_USERS_DB=f'{DATA}/charto_users.db',
                  CHARTO_BACKUP_REQUIRED='1', CHARTO_BACKUP_DIR=f'{DATA}/backup',
                  CHARTO_BACKUP_MARKER=f'{DATA}/backup/charto_users.last_success.json',
                  CHARTO_BACKUP_CONTAINER='pivot-clean-backups', CHARTO_DB_CACHE_MAX_KIB='32768',
                  CHARTO_DB_CACHE_SHARES='16', CHARTO_DATA_CONCURRENCY='10',
                  PYTHONPATH=f'{RELEASE}/pivot', CHARTO_LIVE_VENUES='')
    env = Path('/etc/pivot/runtime.env')
    env.write_text(''.join(f'{key}={value}\n' for key, value in values.items()))
    os.chown(env, 0, gid); os.chmod(env, 0o640)
    link = Path(RELEASE) / 'pivot/.env'
    if link.exists() or link.is_symlink():
        raise RuntimeError('Unexpected existing release credential file')
    link.symlink_to(env)
    Path(DATA).mkdir(exist_ok=True)
    subprocess.run(['mount', '--bind', '/mnt/pivot-old-data/serving', DATA], check=True)
    subprocess.run(['mount', '-o', 'remount,bind,noexec,nosuid,nodev', DATA], check=True)
    os.chown(DATA, pwd.getpwnam('pivot-data').pw_uid, gid); os.chmod(DATA, 0o770)
    Path('/var/lib/pivot').mkdir(exist_ok=True)
    for user, unit in [('pivot-data','charto'), ('pivot-api','pivot-api'), ('pivot-web','pivot-next'), ('pivot-web','charto-web')]:
        work = Path('/var/lib/pivot') / unit
        work.mkdir(exist_ok=True)
        os.chown(work, pwd.getpwnam(user).pw_uid, gid)
        os.chmod(work, 0o750)
        if unit == 'charto':
            command = f'/opt/pivot/venv/bin/python -u {RELEASE}/charto/data/dataserver.py'
            memory = '5G'
        elif unit == 'pivot-api':
            command = '/opt/pivot/venv/bin/python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 --workers 1 --no-access-log'
            memory = '1800M'
        else:
            app, port = ('pivot-next', 3000) if unit == 'pivot-next' else ('charto/web', 5175)
            command = f'/usr/bin/node {RELEASE}/{app}/.next/standalone/server.js'
            memory = '1200M'
        extra = '' if unit in ('charto', 'pivot-api') else f'Environment=NODE_ENV=production\nEnvironment=HOSTNAME=127.0.0.1\nEnvironment=PORT={port}\nEnvironment=PIVOT_BACKEND_ORIGIN=http://127.0.0.1:8000\n'
        Path(f'/etc/systemd/system/{unit}.service').write_text(f'''[Unit]
Description=Pivot clean host {unit}
After=network-online.target
[Service]
User={user}
Group=pivot-runtime
WorkingDirectory={work}
EnvironmentFile=/etc/pivot/runtime.env
Environment=PYTHONUNBUFFERED=1
{extra}ExecStart={command}
Restart=on-failure
RestartSec=5
UMask=0007
MemoryMax={memory}
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=tmpfs
ProtectSystem=strict
ReadWritePaths={DATA} {work}
InaccessiblePaths=/mnt /root /var/lib/waagent /var/lib/cloud
RestrictSUIDSGID=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
[Install]
WantedBy=multi-user.target
''')
    cfg = Path(RELEASE + '/charto/deploy/nginx-charto.conf').read_text().split('    listen 443 ssl http2;')[0]
    cfg = cfg.replace('/data/app/charto/preview', RELEASE + '/charto/preview')
    cfg += '''    listen 80 default_server;
    location ^~ /.well-known/acme-challenge/ { root /var/www/pivot-acme; }
}\n'''
    Path('/var/www/pivot-acme/.well-known/acme-challenge').mkdir(parents=True, exist_ok=True)
    Path('/etc/nginx/sites-available/pivot-clean').write_text(cfg)
    default = Path('/etc/nginx/sites-enabled/default')
    if default.is_symlink():
        default.unlink()  # Only the unused Ubuntu default symlink.
    Path('/etc/nginx/sites-enabled/pivot-clean').symlink_to('/etc/nginx/sites-available/pivot-clean')
    Path('/etc/nginx/conf.d/charto-ratelimit.conf').write_text(Path(RELEASE + '/charto/deploy/nginx-ratelimit.conf').read_text())
    Path('/etc/redis/pivot-clean.conf').write_text('bind 127.0.0.1\nprotected-mode yes\nport 6379\ndaemonize no\nmaxmemory 256mb\nmaxmemory-policy allkeys-lru\nsave ""\nappendonly no\n')
    Path('/etc/systemd/system/pivot-cache.service').write_text('''[Unit]
Description=Pivot local bounded cache
After=network.target
[Service]
User=redis
ExecStart=/usr/bin/redis-server /etc/redis/pivot-clean.conf
Restart=on-failure
MemoryMax=320M
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
[Install]
WantedBy=multi-user.target
''')
    subprocess.run(['nginx', '-t'], check=True)
    subprocess.run(['systemctl', 'daemon-reload'], check=True)
    subprocess.run(['systemctl', 'enable', '--now', 'pivot-next', 'charto-web', 'pivot-cache', 'nginx'], check=True)
    print('CLEAN_FRONTENDS_AND_CACHE_STARTED; DATA_FEEDS_NOT_STARTED')


if __name__ == '__main__':
    main()
