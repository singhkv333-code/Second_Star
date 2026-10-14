#!/usr/bin/env python3
"""Apply a reviewed security payload on the serving host, with file rollback.

Payload contains ONLY shared-control blocks and the identity bridge/router.
PostgreSQL schema/grants must be prepared before --apply. No user-state restore.
"""
import argparse
import hashlib
import json
from pathlib import Path
import secrets
import shutil
import subprocess
import time
import urllib.request


def run(*cmd):
    subprocess.run(cmd, check=True, capture_output=True)


def health():
    for port in (5174, 8000):
        for attempt in range(45):
            try:
                with urllib.request.urlopen(f'http://127.0.0.1:{port}/health', timeout=5) as r:
                    if r.status == 200:
                        break
            except Exception:
                time.sleep(2)
        else:
            raise RuntimeError('Service failed health check')


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('payload')
    parser.add_argument('--apply', action='store_true')
    args=parser.parse_args()
    req=urllib.request.Request('http://169.254.169.254/metadata/instance?api-version=2021-02-01',headers={'Metadata':'true'})
    if json.load(urllib.request.urlopen(req, timeout=10))['compute']['name']!='pivot-clean-stage':
        raise RuntimeError('Wrong host')
    root=Path(Path('/etc/pivot/stage-release').read_text().strip())
    payload=json.loads(Path(args.payload).read_text())
    ds=root/'charto/data/dataserver.py'
    source=ds.read_text()
    start=source.index('            if u.path == "/live":',source.index('class Handler'))
    end=source.index('            if u.path == "/stream":',start)
    source=source[:start]+payload['get_controls']+source[end:]
    start=source.index('        # Voice, before the JSON routes:',source.index('    def do_POST'))
    source=source[:start]+payload['post_controls']+source[start:]
    old='GOOGLE_CLIENT_ID = (environ.get("GOOGLE_CLIENT_ID")\n                    or _env_values("GOOGLE_CLIENT_ID")["GOOGLE_CLIENT_ID"])'
    if old not in source:raise RuntimeError('Unexpected Google configuration')
    source=source.replace(old,payload['google_config'])
    files={ds:source}
    for name in ('charto_session.py','router.py'):
        files[root/'pivot/backend/auth'/name]=payload[name]
    snippet='''    location = /api/landing/waitlist {
        limit_req zone=charto_auth burst=10 nodelay;
        client_max_body_size 12k;
        limit_except POST { deny all; }
        proxy_pass http://127.0.0.1:5175/api/waitlist;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_connect_timeout 10s;
        proxy_read_timeout 15s;
    }
'''
    for site in ('pivot-clean','pivotnow-app'):
        p=Path('/etc/nginx/sites-available')/site
        text=p.read_text()
        if '/api/landing/waitlist' in text:raise RuntimeError('Waitlist route already exists')
        files[p]=text.replace('server {','server {\n'+snippet,1)
    env=Path('/etc/pivot/runtime.env')
    text=env.read_text()
    if 'CHARTO_FEED_CONTROL_KEY=' not in text:
        files[env]=text.rstrip()+'\nCHARTO_FEED_CONTROL_KEY='+secrets.token_urlsafe(48)+'\n'
    print('PLANNED_PATHS',json.dumps([str(p) for p in files if p!=env]))
    if not args.apply:return
    current=json.load(urllib.request.urlopen('http://127.0.0.1:5174/health?deep=1',timeout=15))
    if not current.get('checks',{}).get('backup',{}).get('fresh'):
        raise RuntimeError('Fresh backup required')
    backup=Path('/var/lib/pivot-release/security-20261014')
    if backup.exists():raise RuntimeError('Existing security backup; inspect before retry')
    backup.mkdir(mode=0o700,parents=True)
    saved={}
    try:
        for i,(path,content) in enumerate(files.items()):
            saved[path]=backup/str(i)
            shutil.copy2(path,saved[path])
            path.write_text(content)
        run('/opt/pivot/venv/bin/python','-m','py_compile',*[str(p) for p in files if p.suffix=='.py'])
        run('nginx','-t')
        run('systemctl','restart','charto','pivot-api')
        health()
        run('systemctl','reload','nginx')
    except Exception:
        for path,original in saved.items():shutil.copy2(original,path)
        subprocess.run(['systemctl','restart','charto','pivot-api'],capture_output=True)
        subprocess.run(['systemctl','reload','nginx'],capture_output=True)
        raise
    manifest={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in files if p!=env}
    (backup/'manifest.json').write_text(json.dumps(manifest,indent=2))
    (backup/'paths.json').write_text(json.dumps({str(p):str(v) for p,v in saved.items()},indent=2))
    print('APPLIED_AND_HEALTHY',json.dumps(manifest))

if __name__=='__main__':main()
