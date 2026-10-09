"""Lift the paywall for the people building Pivot.

    python3 comp_developers.py charto_users.db singhkv333@gmail.com dev@catalog.com

A developer comp is Pro+ plus every cap Pro+ still has, opened: AI credits,
alerts, screens and parallel charts. It goes through `entitlements.grant`,
the same writer the admin route uses, so each row carries a reason and who
granted it, and it never expires. Re-running adds nothing that is already in
force. Two caps are left alone on purpose: `chart.panes` (8 on every paid
plan, a layout ceiling rather than a price) and `alerts.fundamental` (0
everywhere — the feature is not built, so there is nothing to unlock).
"""
from __future__ import annotations

import sqlite3
import sys
import threading

import entitlements as ent

PLAN = "pro_plus"
KEEP = {"chart.panes", "alerts.fundamental"}
REASON = "developer account: paywall lifted"


def open_caps() -> list[str]:
    """Every numeric cap the top plan still has, read from the catalog."""
    top = ent.CATALOG["plans"]
    best = max(top, key=lambda p: top[p]["rank"])
    return [k for k, f in ent.CATALOG["features"].items()
            if k not in KEEP and f["kind"] in ("limit", "quota", "value")
            and isinstance(f["values"][best], int) and f["values"][best] > 0]


def main(db: str, emails: list[str]) -> int:
    con = sqlite3.connect(db, check_same_thread=False)
    con.execute("PRAGMA busy_timeout=5000")
    ent.bind(con, threading.Lock())
    caps = open_caps()
    for email in emails:
        row = con.execute("SELECT id FROM users WHERE email=? COLLATE NOCASE",
                          (email,)).fetchone()
        if not row:
            print(f"{email}: no account here")
            continue
        uid = row[0]
        if ent.plan_of(uid) != PLAN:
            ent.grant(uid, plan=PLAN, reason=REASON, granted_by="comp_developers")
        for feature in caps:
            v = ent.value(uid, feature)
            if (v["limit"] if isinstance(v, dict) else v) is not None:
                ent.grant(uid, feature=feature, value_=None, reason=REASON,
                          granted_by="comp_developers")
        print(f"{email} (id {uid}): plan={ent.plan_of(uid)}",
              {f: ent.value(uid, f) for f in caps})
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    sys.exit(main(sys.argv[1], sys.argv[2:]))
