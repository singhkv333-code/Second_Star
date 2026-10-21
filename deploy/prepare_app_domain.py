#!/usr/bin/env python3
"""Issue app.pivotnow.in TLS using DNS validation, without publishing app traffic.

Uses the VM's existing ACME account. The one-use TXT value is public and written
to a root-only status file for the operator to add at the authoritative DNS host.
After cutover, switch renewal to webroot with `certbot reconfigure` and test it.
"""
import json
from pathlib import Path
import subprocess
import urllib.request

DOMAIN = "app.pivotnow.in"
DIRECTORY = Path("/root/pivot-domain-cutover")

AUTH_HOOK = r'''#!/usr/bin/python3
import json, os, pathlib, time, urllib.parse, urllib.request
if os.environ.get("CERTBOT_DOMAIN") != "app.pivotnow.in":
    raise SystemExit("Unexpected certificate domain")
directory = pathlib.Path("/root/pivot-domain-cutover")
value = os.environ["CERTBOT_VALIDATION"]
name = "_acme-challenge.app.pivotnow.in"
(directory / "challenge.json").write_text(json.dumps({"name": name, "type": "TXT", "value": value}))
url = "https://dns.google/resolve?" + urllib.parse.urlencode({"name": name, "type": "TXT"})
deadline = time.monotonic() + 1200
while time.monotonic() < deadline:
    try:
        response = json.load(urllib.request.urlopen(url, timeout=10))
        if any(answer.get("type") == 16 and answer.get("data", "").strip('"') == value
               for answer in response.get("Answer", [])):
            break
    except (OSError, ValueError):
        pass
    time.sleep(10)
else:
    raise SystemExit("DNS validation did not propagate; existing traffic unchanged")
'''


def main():
    request = urllib.request.Request(
        "http://169.254.169.254/metadata/instance?api-version=2021-02-01",
        headers={"Metadata": "true"},
    )
    if json.load(urllib.request.urlopen(request, timeout=10))["compute"]["name"] != "pivot-clean-stage":
        raise SystemExit("Refusing domain preparation on another VM")
    if Path(f"/etc/letsencrypt/live/{DOMAIN}/fullchain.pem").exists():
        print("CERTIFICATE_ALREADY_PRESENT; inspect expiry before continuing")
        return
    DIRECTORY.mkdir(mode=0o700, exist_ok=True)
    hook = DIRECTORY / "dns-auth.py"
    hook.write_text(AUTH_HOOK)
    hook.chmod(0o700)
    subprocess.run([
        "systemd-run", "--unit=pivot-domain-certificate", "--collect",
        "--property=RuntimeMaxSec=25min", "--property=UMask=0077",
        "certbot", "certonly", "--manual", "--preferred-challenges=dns",
        "--manual-auth-hook", str(hook), "--non-interactive",
        "--cert-name", DOMAIN, "-d", DOMAIN,
    ], check=True)
    print("CERTIFICATE_JOB_STARTED; read challenge.json, add TXT, then verify issuance")


if __name__ == "__main__":
    main()
