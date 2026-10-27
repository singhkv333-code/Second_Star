#!/usr/bin/env python3
"""Guest-side activation of restricted test billing, with config rollback.

Credentials are fetched by the existing VM identity from Key Vault. No
credentials in this source, run-command arguments, output, or build env.
"""
import base64
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import time
import urllib.request


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
    # The release manager builds before changing source. Do not enable test
    # keys on the old adapter, which had no customer test-mode restriction.
    for attempt in range(100):
        if "test_checkout_restricted" in (root / "charto/data/billing.py").read_text():
            break
        time.sleep(5)
    else:
        raise RuntimeError("Reviewed billing source is not deployed; configuration unchanged")
    health = read("http://127.0.0.1:5174/health?deep=1")
    if not health.get("ready") or not health.get("checks", {}).get("backup", {}).get("fresh"):
        raise RuntimeError("Healthy service and fresh whole-state backup required")
    token = read("http://169.254.169.254/metadata/identity/oauth2/token?api-version=2018-02-01&resource=https://vault.azure.net",
                 {"Metadata": "true"})["access_token"]
    config = json.loads(read("https://pivot-clean-kv-india.vault.azure.net/secrets/razorpay-test-runtime?api-version=7.4",
                             {"Authorization": "Bearer " + token})["value"])
    if not config.get("RAZORPAY_KEY_ID", "").startswith("rzp_test_"):
        raise RuntimeError("Refusing to enable live keys")
    if config.get("CHARTO_BILLING_TEST_EMAILS") != "billing-e2e-20261015@pivotnow.in":
        raise RuntimeError("Unexpected test allowlist")
    allowed = {"RAZORPAY_KEY_ID", "RAZORPAY_KEY_SECRET", "RAZORPAY_WEBHOOK_SECRET",
               "CHARTO_BILLING_TEST_EMAILS", *[f"RAZORPAY_PLAN_{p}_{c}" for p in ("PRO", "PRO_PLUS")
                                               for c in ("MONTHLY", "ANNUAL")]}
    if set(config) != allowed or any(not isinstance(v, str) or not v or any(x in v for x in "\r\n\x00\"\\")
                                     for v in config.values()):
        raise RuntimeError("Invalid test configuration")
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
    env = Path("/etc/pivot/razorpay-test.env")
    dropin = Path("/etc/systemd/system/charto.service.d/razorpay-test.conf")
    if env.exists():
        raise RuntimeError("Test environment already exists; inspect before changing")
    backup = Path("/var/lib/pivot-release/billing-test-20261015")
    backup.mkdir(mode=0o700, exist_ok=False)
    with sqlite3.connect("file:/srv/pivot-data/charto_users.db?mode=ro", uri=True) as source:
        with sqlite3.connect(backup / "charto_users.db") as target:
            source.backup(target)
            if target.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise RuntimeError("Whole-state backup failed")
    (backup / "charto_users.db").chmod(0o600)
    previous = dropin.read_bytes() if dropin.exists() else None
    if previous:
        (backup / "previous-dropin").write_bytes(previous)
    try:
        fd = os.open(env, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w") as stream:
            stream.write("".join(f'{k}="{v}"\n' for k, v in sorted(config.items())))
        dropin.parent.mkdir(parents=True, exist_ok=True)
        dropin.write_text("[Service]\nEnvironmentFile=/etc/pivot/razorpay-test.env\n")
        run("systemctl", "daemon-reload")
        run("systemctl", "restart", "charto")
        for attempt in range(30):
            try:
                healthy = read("http://127.0.0.1:5174/health").get("ready")
                plans = read("http://127.0.0.1:5174/billing/plans")
                if healthy and plans.get("checkout") and plans.get("test_mode"):
                    print("RESTRICTED_RAZORPAY_TEST_BILLING_ACTIVE; whole-state backup preserved")
                    return
            except Exception:
                pass
            time.sleep(1)
        raise RuntimeError("Billing activation health check failed")
    except Exception:
        # Only files created by this activation; preserve all customer data.
        if previous is None:
            dropin.unlink(missing_ok=True)
        else:
            dropin.write_bytes(previous)
        if env.exists():
            env.rename(backup / "disabled-test.env")
        run("systemctl", "daemon-reload")
        run("systemctl", "restart", "charto")
        raise


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("TEST_BILLING_NOT_ACTIVATED", type(exc).__name__)
        raise SystemExit(1)
