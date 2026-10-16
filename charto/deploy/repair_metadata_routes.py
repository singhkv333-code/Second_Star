#!/usr/bin/env python3
"""Repair shell metadata routing on existing VM vhosts without replacing them.

Default is a dry run. --apply backs up, tests nginx, reloads and probes both
hosts, restoring all touched files if any check fails. Run as root on the VM.
"""
import argparse
from datetime import datetime, timezone
from pathlib import Path
import re
import shutil
import subprocess
import time

BLOCK = r'''    # Next metadata assets belong to the shell, never the chart API fallback.
    location ~ ^/(icon\.svg|apple-icon\.png)$ {
        proxy_pass http://127.0.0.1:3000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

'''
SITES = (("pivot-clean", "pivot-india.centralindia.cloudapp.azure.com"),
         ("pivotnow-app", "app.pivotnow.in"))


def render(source):
    if BLOCK in source:
        return source
    if re.search(r'location\s+(?:=\s+)?/icon', source) or 'icon\\.svg' in source:
        raise ValueError('Existing metadata routing differs; inspect before changing')
    anchor = "    # The shell's real pages."
    if source.count(anchor) != 1 or 'proxy_pass http://127.0.0.1:3000;' not in source:
        raise ValueError('Unexpected vhost; refusing to replace routing')
    return source.replace(anchor, BLOCK + anchor, 1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    changes = []
    for name, host in SITES:
        path = Path('/etc/nginx/sites-available') / name
        source = path.read_text()
        candidate = render(source)
        print(f'{host}: {"needs repair" if source != candidate else "matches"}')
        if source != candidate:
            changes.append((path, candidate))
    if not args.apply or not changes:
        return
    backup = Path('/var/lib/pivot-release') / ('metadata-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    backup.mkdir(parents=True)
    for path, _ in changes:
        shutil.copy2(path, backup / path.name)
    try:
        for path, candidate in changes:
            path.write_text(candidate)
        subprocess.run(['nginx', '-t'], check=True)
        subprocess.run(['systemctl', 'reload', 'nginx'], check=True)
        # A graceful reload returns before all new workers accept requests.
        # Pin probes to this VM so DNS cannot validate a different host.
        for _, host in SITES:
            for asset, content_type in (('icon.svg', 'image/svg+xml'), ('apple-icon.png', 'image/png')):
                result = None
                for attempt in range(5):
                    result = subprocess.run([
                        'curl', '-sS', '--max-time', '15', '--resolve', f'{host}:443:127.0.0.1',
                        '-o', '/dev/null', '-w', '%{http_code} %{content_type}',
                        f'https://{host}/{asset}',
                    ], capture_output=True, text=True)
                    if result.returncode == 0 and result.stdout.startswith('200 ' + content_type):
                        break
                    time.sleep(1)
                else:
                    raise RuntimeError(f'{host}/{asset}: {result.stdout} {result.stderr}')
        print(f'Applied and verified both hosts. Backup: {backup}')
    except Exception:
        for path, _ in changes:
            shutil.copy2(backup / path.name, path)
        subprocess.run(['nginx', '-t'], check=True)
        subprocess.run(['systemctl', 'reload', 'nginx'], check=True)
        raise


if __name__ == '__main__':
    main()
