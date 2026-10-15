#!/usr/bin/env python3
"""Move every account from Pivot API's old sign-up into Pivot's one account
system, keeping each person's password. One-time, 2026-10-15.

Pivot had two sign-ups: the account service (the `users` table in
charto_users.db) and the API's own (`users` in Postgres, bcrypt hashes).
The API's sign-in is gone. So that nobody who could sign in before is shut
out, each Postgres account whose email the account service does not have is
copied there:

  - a bcrypt password is carried as it is (pw_salt = b"bcrypt"); the account
    service checks it on the first sign-in and re-stores it as scrypt
    (dataserver.py `_carried_ok`);
  - an account with no usable password (a Google sign-up) gets random bytes,
    as the account service gives its own Google accounts, so the email is
    held for its owner and Google sign-in finds it.

Rows the account service itself created in Postgres (hashed_password
starting with "!") are skipped: their account is already there. Nothing is
deleted or changed in either store besides these inserts. The Postgres row
stays where it is, and the API maps the account to it by email on the first
request (pivot/backend/auth/accounts.py).

Dry run by default. With --apply it first copies charto_users.db to
<dir>/backups/ with SQLite's backup API, then inserts in one transaction.

  /opt/pivot/venv/bin/python merge_accounts.py --users-db /srv/pivot-data/charto_users.db [--apply]

DATABASE_URL comes from the environment (/etc/pivot/runtime.env).
"""
from __future__ import annotations

import argparse
import os
import secrets
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

CARRIED = b"bcrypt"


def masked(email: str) -> str:
    local, _, domain = email.partition("@")
    return f"{local[:4]}…@{domain}"


def pivot_accounts() -> list[tuple[str, str | None, str, bool, int]]:
    import sqlalchemy as sa

    url = os.environ["DATABASE_URL"]
    engine = sa.create_engine(url, connect_args={"connect_timeout": 10} if url.startswith("postgres") else {})
    with engine.connect() as c:
        rows = c.execute(sa.text(
            "SELECT email, full_name, hashed_password, is_active, created_at FROM users ORDER BY id"
        )).fetchall()
    out = []
    for email, name, hashed, active, created in rows:
        if isinstance(created, str):
            created = datetime.fromisoformat(created)
        created_ts = int(created.replace(tzinfo=created.tzinfo or timezone.utc).timestamp()) if created else int(time.time())
        out.append((email.strip().lower(), name, hashed or "", bool(active), created_ts))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--users-db", required=True, type=Path)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    con = sqlite3.connect(str(args.users_db), timeout=30)
    have = {r[0].strip().lower() for r in con.execute("SELECT email FROM users")}

    move, skipped = [], {"already an account": 0, "made by the account service": 0, "inactive": 0}
    for email, name, hashed, active, created in pivot_accounts():
        if email in have:
            skipped["already an account"] += 1
        elif hashed.startswith("!"):
            skipped["made by the account service"] += 1
        elif not active:
            skipped["inactive"] += 1
        else:
            if hashed.startswith("$2"):
                pw_hash, pw_salt, kind = hashed.encode(), CARRIED, "password"
            else:
                pw_hash, pw_salt, kind = secrets.token_bytes(32), secrets.token_bytes(16), "no password"
            move.append((email, name, pw_hash, pw_salt, created, kind))
            have.add(email)

    for email, _n, _h, _s, _c, kind in move:
        print(f"  move {masked(email):32} {kind}")
    print(f"to move: {len(move)}; skipped: {skipped}")
    if not args.apply:
        print("dry run: nothing written (add --apply)")
        return 0

    backups = args.users_db.parent / "backups"
    backups.mkdir(exist_ok=True)
    dest = backups / f"charto_users-before-merge-{time.strftime('%Y%m%dT%H%M%S')}.db"
    with sqlite3.connect(str(dest)) as out:
        con.backup(out)
    print(f"backup: {dest}")

    with con:
        con.executemany(
            "INSERT INTO users (email, name, pw_hash, pw_salt, created) VALUES (?,?,?,?,?)",
            [(e, n, h, s, c) for e, n, h, s, c, _k in move],
        )
    print(f"inserted {len(move)}; accounts now {con.execute('SELECT count(*) FROM users').fetchone()[0]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
