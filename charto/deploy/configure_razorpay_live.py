#!/usr/bin/env python3
"""Idempotent Razorpay LIVE setup; credentials stay in the existing Key Vault.

The live twin of configure_razorpay_test.py. Reads the live key pair from
charto/web/.env, finds or creates the four subscription plans at exactly the
server catalog's prices, stores the runtime config in Key Vault secret
`razorpay-live-runtime` (before any provider write, so an interrupted run
resumes with the same webhook secret), then finds or creates the live webhook.

Never prints a secret, never passes one through a command line, never changes
an existing webhook it did not create, and charges nothing: plans and
webhooks are configuration. activate_razorpay_live.py switches the VM over.
"""
import base64
import json
from pathlib import Path
import secrets
import ssl
import subprocess
import urllib.error
import urllib.request

import certifi
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[2]
VAULT = "https://pivot-clean-kv-india.vault.azure.net/secrets/razorpay-live-runtime?api-version=7.4"
URL = "https://app.pivotnow.in/billing/webhook"
EVENTS = ["subscription." + x for x in (
    "authenticated", "activated", "charged", "updated", "pending", "halted",
    "cancelled", "completed", "paused", "resumed")]


def request(url, headers, body=None, method="GET"):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, headers={**headers, "Content-Type": "application/json"},
                                 data=data, method=method)
    with urllib.request.urlopen(req, timeout=30, context=ssl.create_default_context(cafile=certifi.where())) as r:
        return json.load(r)


def main():
    env = dotenv_values(ROOT / "charto/web/.env")
    key, secret = env["RAZORPAY_KEY_ID"], env["RAZORPAY_KEY_SECRET"]
    if not key.startswith("rzp_live_"):
        raise RuntimeError("Live setup requires live keys")
    headers = {"Authorization": "Basic " + base64.b64encode(f"{key}:{secret}".encode()).decode()}
    plans = request("https://api.razorpay.com/v1/plans?count=100", headers)["items"]
    catalog = json.loads((ROOT / "charto/data/plans_catalog.json").read_text())
    values = {"RAZORPAY_KEY_ID": key, "RAZORPAY_KEY_SECRET": secret}
    for plan in ("pro", "pro_plus"):
        for cycle in ("monthly", "annual"):
            name = f"Pivot {catalog['plans'][plan]['name']} {cycle}"
            amount = catalog["plans"][plan]["prices"][cycle]["amount"]
            period = "monthly" if cycle == "monthly" else "yearly"
            match = next((x for x in plans if x["period"] == period and x["interval"] == 1
                          and x["item"]["name"] == name and x["item"]["amount"] == amount
                          and x["item"]["currency"] == "INR"), None)
            if match is None:
                match = request("https://api.razorpay.com/v1/plans", headers,
                                {"period": period, "interval": 1,
                                 "item": {"name": name, "amount": amount, "currency": "INR"}}, "POST")
            values[f"RAZORPAY_PLAN_{plan.upper()}_{cycle.upper()}"] = match["id"]
    proc = subprocess.run(["az", "account", "get-access-token", "--resource", "https://vault.azure.net",
                           "-o", "json"], capture_output=True, text=True, check=True)
    vault_headers = {"Authorization": "Bearer " + json.loads(proc.stdout)["accessToken"]}
    previous = {}
    try:
        previous = json.loads(request(VAULT, vault_headers)["value"])
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            raise
    if previous and previous.get("RAZORPAY_KEY_ID") != key:
        raise RuntimeError("Existing live configuration uses another account; review before replacing")
    values["RAZORPAY_WEBHOOK_SECRET"] = previous.get("RAZORPAY_WEBHOOK_SECRET") or secrets.token_hex(32)
    request(VAULT, vault_headers, {"value": json.dumps(values)}, "PUT")
    hooks = request("https://api.razorpay.com/v1/webhooks?count=100", headers).get("items", [])
    existing = next((x for x in hooks if x.get("url") == URL), None)
    if existing:
        if not previous or not existing.get("active"):
            raise RuntimeError("Existing webhook requires review; it was not changed")
    else:
        existing = request("https://api.razorpay.com/v1/webhooks", headers,
                           {"url": URL, "secret": values["RAZORPAY_WEBHOOK_SECRET"],
                            "active": True, "events": {event: True for event in EVENTS}}, "POST")
    print(json.dumps({"mode": "live", "vault_secret": "razorpay-live-runtime",
                      "webhook_id": existing["id"], "webhook_url": URL,
                      "plans": {k: v for k, v in values.items() if k.startswith("RAZORPAY_PLAN_")}}))


if __name__ == "__main__":
    try:
        main()
    except urllib.error.HTTPError as exc:
        # The provider's error description only: never a body that could echo a credential.
        try:
            desc = json.loads(exc.read()).get("error", {}).get("description", "")
        except Exception:
            desc = ""
        print("LIVE_SETUP_FAILED HTTPError", exc.code, exc.url.split("?")[0], desc[:200])
        raise SystemExit(1)
    except Exception as exc:
        print("LIVE_SETUP_FAILED", type(exc).__name__, getattr(exc, "code", ""))
        raise SystemExit(1)
