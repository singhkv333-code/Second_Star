#!/usr/bin/env python3
"""Guest-side: give the dataserver the owner console's key, with rollback.

Run on pivot-clean-stage through `az vm run-command` (as root). The key and
the console's outbound addresses come from Key Vault secret
`owner-console-runtime` ({"PIVOT_OPS_KEY", "PIVOT_OPS_ALLOWED_IPS"}), read with
the VM's own identity; nothing secret is in this source, the run-command
arguments or the output.

    stage     write /etc/pivot/owner-console.env (600) and the charto drop-in,
              daemon-reload, NO restart. The next charto restart (the release
              deployer restarts it when dataserver.py changes) picks it up, so
              shipping the code and the key costs one restart, not two.
    restart   stage, then restart charto now and wait for health
    verify    whether the running charto holds the key and the release has
              ops_api.py (read-only)
    remove    delete the env and drop-in, daemon-reload, restart charto

Without the env the /ops routes do not exist (ops_api.enabled() is False), so
`remove` is the whole rollback.
"""
import hmac
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ENV = Path("/etc/pivot/owner-console.env")
DROPIN = Path("/etc/systemd/system/charto.service.d/owner-console.conf")
VAULT = "https://pivot-clean-kv-india.vault.azure.net/secrets/owner-console-runtime?api-version=7.4"


def read(url, headers=None):
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers or {}), timeout=15) as r:
        return json.load(r)


def run(*args):
    subprocess.run(args, check=True, capture_output=True, timeout=120)


def check_vm():
    meta = read("http://169.254.169.254/metadata/instance?api-version=2021-02-01", {"Metadata": "true"})
    if meta["compute"]["name"] != "pivot-clean-stage":
        raise RuntimeError("Wrong target VM")


def config():
    tok = read("http://169.254.169.254/metadata/identity/oauth2/token?api-version=2018-02-01"
               "&resource=https://vault.azure.net", {"Metadata": "true"})["access_token"]
    cfg = json.loads(read(VAULT, {"Authorization": "Bearer " + tok})["value"])
    if set(cfg) != {"PIVOT_OPS_KEY", "PIVOT_OPS_ALLOWED_IPS"}:
        raise RuntimeError("owner-console-runtime has unexpected keys")
    key, ips = cfg["PIVOT_OPS_KEY"], cfg["PIVOT_OPS_ALLOWED_IPS"]
    if len(key) < 32 or any(c in key + ips for c in "\r\n\x00\"\\'$`"):
        raise RuntimeError("owner-console-runtime is malformed")
    return cfg


def stage():
    check_vm()
    cfg = config()
    body = "".join(f'{k}="{v}"\n' for k, v in sorted(cfg.items()))
    if ENV.exists() and ENV.read_text() == body and DROPIN.exists():
        print("OWNER_CONSOLE_ENV_ALREADY_STAGED")
        return
    ENV.write_text(body)
    ENV.chmod(0o600)
    DROPIN.write_text("[Service]\nEnvironmentFile=/etc/pivot/owner-console.env\n")
    run("systemctl", "daemon-reload")
    print("OWNER_CONSOLE_ENV_STAGED (takes effect at the next charto restart)")


def healthy(wait=90):
    for _ in range(wait):
        try:
            if read("http://127.0.0.1:5174/health").get("ready"):
                return True
        except Exception:                               # noqa: BLE001
            pass
        time.sleep(1)
    return False


def restart():
    stage()
    run("systemctl", "restart", "charto")
    if not healthy():
        remove()
        raise RuntimeError("charto unhealthy after restart; owner console env removed")
    print("CHARTO_RESTARTED_WITH_OWNER_CONSOLE_ENV")


def verify():
    """Does the running charto hold the key? (The end-to-end proof is the
    console's Overview loading; this only reads the process environment.)"""
    check_vm()
    want = config()
    pid = subprocess.run(["systemctl", "show", "-p", "MainPID", "--value", "charto"],
                         check=True, capture_output=True, text=True).stdout.strip()
    env = dict(x.split("=", 1) for x in Path(f"/proc/{pid}/environ").read_bytes().decode(
        errors="replace").split("\0") if "=" in x)
    print(json.dumps({
        "env_staged": ENV.exists() and DROPIN.exists(),
        "running_has_key": hmac.compare_digest(env.get("PIVOT_OPS_KEY", ""), want["PIVOT_OPS_KEY"]),
        "running_allowed_ips": len([x for x in env.get("PIVOT_OPS_ALLOWED_IPS", "").split(",") if x]),
        "release_has_ops_api": (Path(Path("/etc/pivot/stage-release").read_text().strip())
                                / "charto/data/ops_api.py").exists(),
    }))


def remove():
    DROPIN.unlink(missing_ok=True)
    ENV.unlink(missing_ok=True)
    run("systemctl", "daemon-reload")
    run("systemctl", "restart", "charto")
    print("OWNER_CONSOLE_ENV_REMOVED", "healthy" if healthy() else "UNHEALTHY")


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        {"stage": stage, "restart": restart, "verify": verify, "remove": remove}[action]()
    except KeyError:
        print(__doc__)
        raise SystemExit(2)
    except Exception as exc:                            # noqa: BLE001
        print("OWNER_CONSOLE_NOT_CHANGED", type(exc).__name__, str(exc)[:200])
        raise SystemExit(1)
