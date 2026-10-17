#!/usr/bin/env python3
"""Guest-side switch for the plan paywall (PAYWALL_ENABLED), measured first.

    python3 enable_paywall.py report     read-only: who the Free caps would touch
    python3 enable_paywall.py enable     the same report, then switch on
    python3 enable_paywall.py enforce    switch on even if alerts pause (the owner's call)

With the switch on, every catalog limit is enforced (docs/paywall/README.md
"What is enforced") and the hourly plan sweep brings ARMED alerts inside each
account's plan: the newest over a cap are paused (never deleted), and
expiries past the plan's horizon are brought in. `enable` refuses while any
existing account would have alerts paused — that is the plan's open
"grandfathering" item, and it is the owner's call, not this script's. Expiry
clamps are reported, not blocking: the alert still exists and fires until then.

Prints counts only, never an email or an alert. Any failure after the switch
removes the drop-in and restarts Charto as it was.
"""
import json
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import time
import urllib.request

DROPIN = Path("/etc/systemd/system/charto.service.d/paywall.conf")
USERS_DB = "file:/srv/pivot-data/charto_users.db?mode=ro"
# alerts.alert_class, verbatim: drawings, channels, %-moves and indicators are technical
ADDR = re.compile(r"^(open|high|low|close|hl2|hlc3|ohlc4)(\[\d+\])?$|^(day|pday|\d+[dw])\.(open|high|low|close)$")
OPS = {"cross", "cross_up", "cross_down", "above", "below"}


def read(url, headers=None):
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers or {}), timeout=15) as response:
        return json.load(response)


def run(*args):
    subprocess.run(args, check=True, capture_output=True, timeout=90)


def alert_class(spec):
    for c in spec.get("when") or []:
        if c.get("op") not in OPS:
            return "technical"
        for k in ("left", "right", "right2"):
            v = c.get(k)
            if v is None or isinstance(v, (int, float)):
                continue
            s = str(v).strip().lower()
            try:
                float(s)
                continue
            except ValueError:
                pass
            if not ADDR.match(s):
                return "technical"
    return "price"


def impact(root):
    catalog = json.loads((root / "charto/data/plans_catalog.json").read_text())
    vals = {k: f["values"] for k, f in catalog["features"].items()}
    now = int(time.time())
    with sqlite3.connect(USERS_DB, uri=True) as c:
        users = c.execute("SELECT count(*) FROM users").fetchone()[0]
        paid = {uid: plan for uid, plan in c.execute(
            "SELECT user_id, plan FROM subscriptions WHERE status IN ('active','trialing') "
            "OR (status='cancelled' AND period_end>?)", (now,))}
        granted = {uid: plan for uid, plan in c.execute(
            "SELECT user_id, plan FROM entitlement_grants WHERE plan IS NOT NULL "
            "AND (expires IS NULL OR expires>?)", (now,))}
        # dataserver._layout_plan_refusal: charts per tab, indicators per chart.
        # A layout already over still opens; its next save is refused (402).
        layouts = []
        for uid, spec in c.execute("SELECT user_id, spec FROM layouts"):
            try:
                sp = json.loads(spec)
            except (TypeError, ValueError):
                continue
            if isinstance(sp, dict):
                layouts.append((uid, len(sp.get("charts") or []),
                                len((sp.get("workspace") or {}).get("indicators") or [])))
        per = {}
        for uid, spec, exp in c.execute("SELECT user_id, spec, expires FROM alerts WHERE state='armed'"):
            try:
                sp = json.loads(spec)
            except (TypeError, ValueError):
                continue
            d = per.setdefault(uid, {"price": [], "technical": []})
            d[alert_class(sp)].append(exp)
    out = {"users": users, "paid_or_granted": len(set(paid) | set(granted)),
           "accounts_with_armed_alerts": len(per), "accounts_paused": 0, "alerts_paused": 0,
           "accounts_clamped": 0, "alerts_clamped": 0}
    over = {uid for uid, charts, inds in layouts
            if any(v is not None and n > v for n, v in (
                (charts, vals["chart.panes"][paid.get(uid) or granted.get(uid) or "free"]),
                (inds, vals["chart.indicators"][paid.get(uid) or granted.get(uid) or "free"])))}
    out["accounts_with_layouts_over_caps"] = len(over)
    for uid, d in per.items():
        plan = paid.get(uid) or granted.get(uid) or "free"
        paused = 0
        for cls in ("price", "technical"):
            lim = vals[f"alerts.{cls}"][plan]
            if lim is not None and len(d[cls]) > lim:
                paused += len(d[cls]) - lim
        days = vals["alerts.expiry_days"][plan]
        clamped = 0 if days is None else sum(1 for e in d["price"] + d["technical"]
                                             if e is None or e > now + days * 86400)
        if paused:
            out["accounts_paused"] += 1
            out["alerts_paused"] += paused
        if clamped:
            out["accounts_clamped"] += 1
            out["alerts_clamped"] += clamped
    return out


def main(mode):
    metadata = read("http://169.254.169.254/metadata/instance?api-version=2021-02-01", {"Metadata": "true"})
    if metadata["compute"]["name"] != "pivot-clean-stage":
        raise RuntimeError("Wrong target VM")
    root = Path(Path("/etc/pivot/stage-release").read_text().strip())
    report = impact(root)
    print("IMPACT", json.dumps(report))
    if mode == "report":
        return
    if report["accounts_paused"] and mode != "enforce":
        raise RuntimeError("Existing accounts would have alerts paused; settle grandfathering first")
    if DROPIN.exists():
        raise RuntimeError("Paywall drop-in already exists; inspect before changing")
    health = read("http://127.0.0.1:5174/health?deep=1")
    if not health.get("ready") or not health.get("checks", {}).get("backup", {}).get("fresh"):
        raise RuntimeError("Healthy service and fresh whole-state backup required")
    backup = Path("/var/lib/pivot-release/paywall-" + time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()))
    backup.mkdir(mode=0o700, parents=True, exist_ok=False)
    with sqlite3.connect(USERS_DB, uri=True) as source:
        with sqlite3.connect(backup / "charto_users.db") as target:
            source.backup(target)
            if target.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise RuntimeError("Whole-state backup failed")
    (backup / "charto_users.db").chmod(0o600)
    try:
        DROPIN.write_text("[Service]\nEnvironment=PAYWALL_ENABLED=1\n")
        run("systemctl", "daemon-reload")
        run("systemctl", "restart", "charto")
        for attempt in range(45):
            try:
                if read("http://127.0.0.1:5174/health").get("ready") and \
                        read("http://127.0.0.1:5174/billing/me").get("paywall_enabled") is True:
                    print("PAYWALL_ENABLED_ACTIVE; backup", backup)
                    return
            except Exception:
                pass
            time.sleep(1)
        raise RuntimeError("Paywall health check failed")
    except Exception:
        DROPIN.unlink(missing_ok=True)
        run("systemctl", "daemon-reload")
        run("systemctl", "restart", "charto")
        raise


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "report"
    if mode not in ("report", "enable", "enforce"):
        raise SystemExit("usage: enable_paywall.py report|enable|enforce")
    try:
        main(mode)
    except Exception as exc:
        print("PAYWALL_NOT_ENABLED", type(exc).__name__, str(exc)[:160])
        raise SystemExit(1)
