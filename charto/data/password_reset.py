"""Forgotten passwords: a one-time emailed link, instead of "contact support".

    POST /auth/reset/request {email}
        Always answers the same, whether or not the address has an account:
        the reply must not tell anyone which emails are registered. When it
        does, a link is emailed that works once, for 30 minutes.
    POST /auth/reset {token, password}
        Sets the new password, burns every outstanding link for the account,
        signs out every existing session, emails a "password changed" notice,
        and signs the person in.

What is stored is the SHA-256 of the token, never the token: a copy of
charto_users.db cannot be turned into working reset links. Rates are capped
per address and per network address so the endpoint cannot be used to flood a
mailbox. nginx also rate-limits ^/auth/reset at the credential-endpoint rate.

A Google-only account can use this too. It proves control of the same inbox
Google verified, and it gives that account a password alongside Google.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import secrets
import time
from typing import Callable
from zoneinfo import ZoneInfo

import mail_templates
import mailer

TTL = 30 * 60
PER_ADDRESS_HOURLY = 3
PER_IP_HOURLY = 10
MIN_PASSWORD = 8

SCHEMA = """
CREATE TABLE IF NOT EXISTS password_resets (
  token_hash TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id),
  created INTEGER NOT NULL,
  expires INTEGER NOT NULL,
  used INTEGER,
  ip TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS password_resets_user ON password_resets(user_id, created);
CREATE INDEX IF NOT EXISTS password_resets_ip ON password_resets(ip, created);
"""

SENT = {"ok": True,
        "message": "If an account exists for that email, a reset link is on its way. "
                   "It works for 30 minutes."}


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _ist(ts: int) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).astimezone(
        ZoneInfo("Asia/Kolkata")).strftime("%d %b %Y, %H:%M IST")


class Resets:
    def __init__(self, db, lock, pw_hash: Callable[[str, bytes], bytes],
                 send: Callable = mailer.send_later, config: mailer.Config | None = None):
        self.db, self.lock, self.pw_hash, self.send = db, lock, pw_hash, send
        self.config = config or mailer.Config.from_env()
        with self.lock:
            self.db.executescript(SCHEMA)
            self.db.commit()

    def request(self, email: str, ip: str = "") -> tuple[int, dict]:
        email = str(email or "").strip().lower()
        if not mailer.valid_address(email):
            return 400, {"error": "enter a valid email address"}
        now = int(time.time())
        with self.lock:
            # Old rows are only evidence for the rate limit; a day is plenty.
            self.db.execute("DELETE FROM password_resets WHERE created<?", (now - 86400,))
            row = self.db.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
            recent_ip = self.db.execute(
                "SELECT COUNT(*) FROM password_resets WHERE ip=? AND created>?",
                (ip, now - 3600)).fetchone()[0] if ip else 0
            if recent_ip >= PER_IP_HOURLY:
                self.db.commit()
                return 429, {"error": "Too many reset requests. Please try again in an hour."}
            if not row:
                self.db.commit()
                return 200, dict(SENT)
            recent = self.db.execute(
                "SELECT COUNT(*) FROM password_resets WHERE user_id=? AND created>?",
                (row[0], now - 3600)).fetchone()[0]
            if recent >= PER_ADDRESS_HOURLY:
                self.db.commit()
                return 200, dict(SENT)      # same answer: never confirm the account
            token = secrets.token_urlsafe(32)
            self.db.execute(
                "INSERT INTO password_resets (token_hash,user_id,created,expires,ip) "
                "VALUES (?,?,?,?,?)", (_digest(token), row[0], now, now + TTL, ip))
            self.db.commit()
        url = f"{self.config.origin}/reset-password?token={token}"
        self.send(mail_templates.password_reset(email=email, url=url, minutes=TTL // 60,
                                                origin=self.config.origin),
                  email, self.config, f"pivot-reset-{now}-{row[0]}")
        return 200, dict(SENT)

    def reset(self, token: str, password: str) -> tuple[int, dict | int]:
        """(200, user_id) on success — the caller issues the new session."""
        token, password = str(token or ""), str(password or "")
        if len(password) < MIN_PASSWORD:
            return 400, {"error": f"password must be at least {MIN_PASSWORD} characters"}
        now = int(time.time())
        salt = secrets.token_bytes(16)
        hashed = self.pw_hash(password, salt)       # slow; never under the lock
        with self.lock:
            row = self.db.execute(
                "SELECT r.user_id, u.email FROM password_resets r JOIN users u "
                "ON u.id=r.user_id WHERE r.token_hash=? AND r.used IS NULL "
                "AND r.expires>?", (_digest(token), now)).fetchone()
            if not row:
                return 400, {"error": "This reset link has expired or was already used. "
                                      "Request a new one."}
            uid, email = row
            self.db.execute("UPDATE users SET pw_hash=?, pw_salt=? WHERE id=?",
                            (hashed, salt, uid))
            self.db.execute("UPDATE password_resets SET used=? WHERE user_id=? "
                            "AND used IS NULL", (now, uid))
            self.db.execute("DELETE FROM sessions WHERE user_id=?", (uid,))
            self.db.commit()
        self.send(mail_templates.password_changed(email=email, when=_ist(now),
                                                  origin=self.config.origin),
                  email, self.config, f"pivot-pwchanged-{now}-{uid}")
        return 200, uid
