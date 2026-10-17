#!/usr/bin/env python3
"""Guest-side switch from restricted test billing to LIVE billing, with rollback.

The live twin of activate_razorpay_test.py. Credentials are fetched by the VM's
own identity from Key Vault secret `razorpay-live-runtime` (written by
configure_razorpay_live.py); none are in this source, the run-command
arguments, the output or a build environment.

Before anything changes: the right VM, a healthy service with a fresh
whole-state backup, live keys only, and every provider plan priced exactly as
the server catalog. Then a second whole-state backup, the live environment and
drop-in, and the test drop-in moved aside (systemd reads drop-ins in name
order, so `razorpay-test.conf` would otherwise override the live keys). Any
failure restores the test configuration and restarts Charto as it was.
"""
import base64
import json
from pathlib import Path
import sqlite3
import subprocess
import time
import urllib.request

ENV = Path("/etc/pivot/razorpay-live.env")
DROPIN = Path("/etc/systemd/system/charto.service.d/razorpay-live.conf")
TEST_DROPIN = Path("/etc/systemd/system/charto.service.d/razorpay-test.conf")


def read(url, headers=None):
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers or {}), timeout=15) as response:
        return json.load(response)


def run(*args):
    subprocess.run(args, check=True, capture_output=True, timeout=90)


def main():
    metadata = read("http://169.254.169.254/metadata/instance?api-version=2021-02-01", {"Metadata": "true"})
    if metadata["compute"]["name"] != "pivot-clean-stage":
        raise RuntimeError("Wrong target VM")
    root = Path(Path("/etc/pivot/stage-release").read_text().strip())
    if "def checkout_available" not in (root / "charto/data/billing.py").read_text():
        raise RuntimeError("Reviewed billing source is not deployed; configuration unchanged")
    health = read("http://127.0.0.1:5174/health?deep=1")
    if not health.get("ready") or not health.get("checks", {}).get("backup", {}).get("fresh"):
        raise RuntimeError("Healthy service and fresh whole-state backup required")
    token = read("http://169.254.169.254/metadata/identity/oauth2/token?api-version=2018-02-01&resource=https://vault.azure.net",
                 {"Metadata": "true"})["access_token"]
    config = json.loads(read("https://pivot-clean-kv-india.vault.azure.net/secrets/razorpay-live-runtime?api-version=7.4",
                             {"Authorization": "Bearer " + token})["value"])
    if not config.get("RAZORPAY_KEY_ID", "").startswith("rzp_live_"):
        raise RuntimeError("Live activation requires live keys")
    allowed = {"RAZORPAY_KEY_ID", "RAZORPAY_KEY_SECRET", "RAZORPAY_WEBHOOK_SECRET",
               *[f"RAZORPAY_PLAN_{p}_{c}" for p in ("PRO", "PRO_PLUS") for c in ("MONTHLY", "ANNUAL")]}
    if set(config) != allowed or any(not isinstance(v, str) or not v or any(x in v for x in "\r\n\x00\"\\")
                                     for v in config.values()):
        raise RuntimeError("Invalid live configuration")
    auth = base64.b64encode(f"{config['RAZORPAY_KEY_ID']}:{config['RAZORPAY_KEY_SECRET']}".encode()).decode()
    catalog = json.loads((root / "charto/data/plans_catalog.json").read_text())
    for p in ("pro", "pro_plus"):
        for c in ("monthly", "annual"):
            plan = read("https://api.razorpay.com/v1/plans/" + config[f"RAZORPAY_PLAN_{p.upper()}_{c.upper()}"],
                        {"Authorization": "Basic " + auth})
            if (plan["item"]["amount"] != catalog["plans"][p]["prices"][c]["amount"] or
                    plan["item"]["currency"] != "INR" or plan["interval"] != 1 or
                    plan["period"] != ("monthly" if c == "monthly" else "yearly")):
                raise RuntimeError("Provider price differs from the server catalog")
    if ENV.exists() or DROPIN.exists():
        raise RuntimeError("Live environment already exists; inspect before changing")
    backup = Path("/var/lib/pivot-release/billing-live-" + time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()))
    backup.mkdir(mode=0o700, parents=True, exist_ok=False)
    with sqlite3.connect("file:/srv/pivot-data/charto_users.db?mode=ro", uri=True) as source:
        with sqlite3.connect(backup / "charto_users.db") as target:
            source.backup(target)
            if target.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise RuntimeError("Whole-state backup failed")
    (backup / "charto_users.db").chmod(0o600)
    test_dropin = TEST_DROPIN.read_bytes() if TEST_DROPIN.exists() else None
    try:
        with open(ENV, "x") as stream:
            ENV.chmod(0o600)
            stream.write("".join(f'{k}="{v}"\n' for k, v in sorted(config.items())))
        DROPIN.write_text("[Service]\nEnvironmentFile=/etc/pivot/razorpay-live.env\n")
        if test_dropin is not None:
            (backup / "razorpay-test.conf").write_bytes(test_dropin)
            TEST_DROPIN.unlink()
        run("systemctl", "daemon-reload")
        run("systemctl", "restart", "charto")
        for attempt in range(45):
            try:
                healthy = read("http://127.0.0.1:5174/health").get("ready")
                plans = read("http://127.0.0.1:5174/billing/plans")
                if healthy and plans.get("checkout") and plans.get("test_mode") is False:
                    print("LIVE_RAZORPAY_BILLING_ACTIVE; backup", backup)
                    return
            except Exception:
                pass
            time.sleep(1)
        raise RuntimeError("Billing activation health check failed")
    except Exception:
        # Only files this activation touched; customer data is never restored over.
        DROPIN.unlink(missing_ok=True)
        if ENV.exists():
            ENV.rename(backup / "disabled-live.env")
        if test_dropin is not None:
            TEST_DROPIN.write_bytes(test_dropin)
        run("systemctl", "daemon-reload")
        run("systemctl", "restart", "charto")
        raise


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("LIVE_BILLING_NOT_ACTIVATED", type(exc).__name__, str(exc)[:160])
        raise SystemExit(1)
