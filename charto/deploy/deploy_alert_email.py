#!/usr/bin/env python3
"""Deploy only alert-email files to the clean VM, with backup and rollback.

Reuses the checksum-verified private-blob transport. No Git push, credentials,
DNS changes, mail sending or rollback-host writes. Source drift fails closed.
"""
import deliver_clean_patch as delivery

FILES = ('charto/data/alert_email.py', 'charto/data/alerts.py',
         'charto/preview/js/alerts.js', 'charto/preview/js/panels.js')
ORIGINAL_HASHES = {
    'charto/data/alerts.py': '07533c646be6139a106770646f6db0a360b8847d450b6057c9628c6192a500a9',
    'charto/preview/js/alerts.js': '3aba728319014e37fe9a272f42f72d8ae13b14c5d61fce6e79212a708d88efd8',
    'charto/preview/js/panels.js': '1ccd2ff213f47345a55c34a3679cce07b8cab76eb40a518d7d8fae2d15595339',
}
PATCHES = {
    'charto/data/dataserver.py': (
        ('         "when": {"type": "array", "description": "1-4 conditions",',
         '         "email": {"type": "boolean", "description": "default true: email the account owner when triggered; false disables email. Sender availability is reported by the tool. Never supply a recipient."},\n'
         '         "when": {"type": "array", "description": "1-4 conditions",'),
        ('         "alert_id": {"type": "integer"},\n         "state": {"type": "string", "enum": ["armed", "paused"],',
         '         "alert_id": {"type": "integer"},\n'
         '         "email": {"type": "boolean", "description": "enable or disable email to the owner\'s account; omit to preserve"},\n'
         '         "state": {"type": "string", "enum": ["armed", "paused"],'),
    ),
    'charto/preview/index.html': (
        ('./js/alerts.js?v=20', './js/alerts.js?v=21'),
        ('./js/panels.js?v=22', './js/panels.js?v=23'),
        ('.al-stack { display: flex; flex-direction: column; gap: 6px; min-width: 0; width: 100%; }',
         '.al-stack { display: flex; flex-direction: column; gap: 6px; min-width: 0; width: 100%; }\n'
         '.al-email-note { color: var(--muted-foreground); font-size: var(--text-sm); overflow-wrap: anywhere; }'),
    ),
}


def main():
    # These three files were clean before this feature. Dataserver/index have
    # unrelated local edits; only our exact transformations are sent for them.
    expected = ORIGINAL_HASHES
    original_az = delivery.az

    def dispatch(*args):
        if args[:3] != ('vm', 'run-command', 'create'):
            return original_az(*args)
        args = list(args)
        i = args.index('--run-command-name') + 1
        stamp = args[i].rsplit('-', 1)[-1]
        preflight = f'''set -eu
python3 - <<'PY'
import hashlib, json, os, pathlib, shutil, sqlite3, urllib.request
req=urllib.request.Request('http://169.254.169.254/metadata/instance?api-version=2021-02-01',headers={{'Metadata':'true'}})
if json.load(urllib.request.urlopen(req,timeout=10))['compute']['name']!='pivot-clean-stage':
    raise SystemExit('Wrong target VM')
release=pathlib.Path(pathlib.Path('/etc/pivot/stage-release').read_text().strip())
for relative,digest in {expected!r}.items():
    if hashlib.sha256((release/relative).read_bytes()).hexdigest()!=digest:
        raise SystemExit('Source drift: '+relative)
for relative,edits in {PATCHES!r}.items():
    body=(release/relative).read_text()
    if any(body.count(before)!=1 for before,after in edits):
        raise SystemExit('Patch context drift: '+relative)
backup=pathlib.Path('/opt/pivot/alert-email-rollbacks/{stamp}')
backup.mkdir(parents=True,mode=0o700,exist_ok=False)
for relative in {list(FILES) + list(PATCHES)!r}:
    source=release/relative
    if source.exists():
        target=backup/relative;target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(source,target)
with sqlite3.connect('file:/srv/pivot-data/charto_users.db?mode=ro',uri=True) as source:
    target=backup/'charto_users.db'
    with sqlite3.connect(target) as destination:
        source.backup(destination)
        if destination.execute('PRAGMA quick_check').fetchone()[0]!='ok':
            raise SystemExit('Backup check failed')
    target.chmod(0o600)
print('SOURCE_AND_WHOLE_USER_BACKUP_VERIFIED')
PY
'''
        activation = f'''
python3 - <<'PY'
import json, pathlib, shutil, sqlite3, subprocess, time, urllib.request
release=pathlib.Path(pathlib.Path('/etc/pivot/stage-release').read_text().strip())
backup=pathlib.Path('/opt/pivot/alert-email-rollbacks/{stamp}')
try:
    for relative,edits in {PATCHES!r}.items():
        target=release/relative;body=target.read_text()
        for before,after in edits: body=body.replace(before,after,1)
        target.write_text(body)
    subprocess.run(['/opt/pivot/venv/bin/python','-m','py_compile',str(release/'charto/data/alert_email.py'),str(release/'charto/data/alerts.py'),str(release/'charto/data/dataserver.py')],check=True)
    for relative in ('charto/preview/js/alerts.js','charto/preview/js/panels.js'):
        subprocess.run(['node','--check',str(release/relative)],check=True)
    subprocess.run(['systemctl','restart','charto'],check=True,timeout=45)
    ready=False
    for attempt in range(20):
        try:
            with urllib.request.urlopen('http://127.0.0.1:5174/health',timeout=3) as response:
                health=json.load(response)
            with sqlite3.connect('file:/srv/pivot-data/charto_users.db?mode=ro',uri=True) as db:
                ready=bool(db.execute("SELECT 1 FROM sqlite_master WHERE name='alert_email_outbox'").fetchone())
            if ready: break
        except Exception: pass
        time.sleep(1)
    if not ready: raise RuntimeError('Email schema/readiness check failed')
    print('ALERT_EMAIL_DEPLOYED; real SMTP delivery is not enabled by this deployment')
    print('SOURCE_ROLLBACK',str(backup))
except Exception:
    for relative in {list(FILES) + list(PATCHES)!r}:
        previous=backup/relative
        if previous.exists(): shutil.copy2(previous,release/relative)
    subprocess.run(['systemctl','restart','charto'],check=False,timeout=45)
    print('SOURCE_ROLLED_BACK; additive outbox and current user state preserved')
    raise
PY
'''
        i = args.index('--script') + 1
        args[i] = preflight + args[i] + activation
        return original_az(*args)

    delivery.FILES = FILES
    delivery.az = dispatch
    delivery.main()


if __name__ == '__main__':
    main()
