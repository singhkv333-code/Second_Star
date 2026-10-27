"""What sign-up and sign-in check besides the password itself.

    Limiter        attempts per window, per network address and per account,
                   so a password cannot be guessed at line speed and sign-up
                   cannot be scripted. In memory: a restart forgets, which
                   costs an attacker nothing they did not have, and nginx's
                   own per-address limit sits in front of it.
    password_problem
                   8 to 256 characters (scrypt over a megabyte of "password"
                   is a cheap way to tie up a worker), not one of the most
                   common passwords, not the email address itself.
    Verifications  "Confirm your email": a one-time link (24 hours) sent at
                   sign-up and on request. users.email_verified records it.
                   Google sign-in counts as confirmed, because Google has
                   confirmed the address.

What is stored for a confirmation link is its SHA-256, never the token,
exactly as for password resets (password_reset.py).
"""
from __future__ import annotations

import collections
import hashlib
import secrets
import threading
import time
from typing import Callable

import mail_templates
import mailer

# ── attempts per window ────────────────────────────────────────────────────

LIMITS = {
    # name: (attempts, seconds)
    "login_account": (10, 15 * 60),     # per address + account: guessing one password
    "login_ip": (40, 15 * 60),          # per address: guessing across accounts
    "signup_ip": (6, 60 * 60),
    "google_ip": (30, 15 * 60),
    "verify_ip": (20, 60 * 60),
    "resend_account": (3, 60 * 60),
}
TOO_MANY = "Too many attempts. Please wait a few minutes and try again."


class Limiter:
    def __init__(self, limits: dict[str, tuple[int, int]] | None = None,
                 clock: Callable[[], float] = time.monotonic):
        self.limits = limits or LIMITS
        self.clock = clock
        self.hits: dict[tuple[str, str], collections.deque] = {}
        self.lock = threading.Lock()

    def allow(self, rule: str, key: str) -> bool:
        """Count one attempt; False when the window is already full."""
        limit, window = self.limits[rule]
        now = self.clock()
        with self.lock:
            q = self.hits.setdefault((rule, key), collections.deque())
            while q and now - q[0] > window:
                q.popleft()
            if len(q) >= limit:
                return False
            q.append(now)
            if len(self.hits) > 50_000:          # never grow without bound
                for k in [k for k, v in self.hits.items() if not v][:10_000]:
                    del self.hits[k]
            return True

    def clear(self, rule: str, key: str) -> None:
        """A successful sign-in forgets that account's failed attempts."""
        with self.lock:
            self.hits.pop((rule, key), None)


# ── passwords ──────────────────────────────────────────────────────────────

MIN_PASSWORD, MAX_PASSWORD = 8, 256
COMMON = frozenset("""
password password1 password123 passw0rd 12345678 123456789 1234567890 12345678910
qwerty123 qwertyuiop 1q2w3e4r 1qaz2wsx iloveyou11 abcd1234 abc12345 asdfghjkl
11111111 00000000 87654321 welcome1 welcome123 admin123 letmein1 sunshine1
football baseball princess dragon123 monkey123 trustno1 superman batman123
india123 india@123 password@123 pass@123 pivot123 pivotnow
""".split())


def password_problem(password: str, email: str = "") -> str | None:
    if len(password) < MIN_PASSWORD:
        return f"Use at least {MIN_PASSWORD} characters."
    if len(password) > MAX_PASSWORD:
        return f"Use at most {MAX_PASSWORD} characters."
    if password.lower() in COMMON or len(set(password)) < 3:
        return "That password is too easy to guess. Try a longer phrase."
    if email and password.lower() in (email.lower(), email.split("@")[0].lower()):
        return "Your password can't be your email address."
    return None


# ── email confirmation ─────────────────────────────────────────────────────

TTL = 24 * 3600

SCHEMA = """
CREATE TABLE IF NOT EXISTS email_verifications (
  token_hash TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id),
  email TEXT NOT NULL,
  created INTEGER NOT NULL,
  expires INTEGER NOT NULL,
  used INTEGER);
CREATE INDEX IF NOT EXISTS email_verifications_user ON email_verifications(user_id, created);
"""


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class Verifications:
    def __init__(self, db, lock, send: Callable = mailer.send_later,
                 config: mailer.Config | None = None):
        self.db, self.lock, self.send = db, lock, send
        self.config = config or mailer.Config.from_env()
        with self.lock:
            self.db.executescript(SCHEMA)
            cols = {r[1] for r in self.db.execute("PRAGMA table_info(users)")}
            if "email_verified" not in cols:
                # NULL = not confirmed yet; a time = when it was.
                self.db.execute("ALTER TABLE users ADD COLUMN email_verified INTEGER")
            self.db.commit()

    def verified(self, user_id: int) -> bool:
        with self.lock:
            row = self.db.execute("SELECT email_verified FROM users WHERE id=?",
                                  (user_id,)).fetchone()
        return bool(row and row[0])

    def mark(self, user_id: int) -> None:
        with self.lock:
            self.db.execute("UPDATE users SET email_verified=COALESCE(email_verified, ?) "
                            "WHERE id=?", (int(time.time()), user_id))
            self.db.commit()

    def start(self, user_id: int, email: str, name: str | None = None) -> bool:
        """Email a confirmation link. False when mail is not configured."""
        now = int(time.time())
        token = secrets.token_urlsafe(32)
        with self.lock:
            self.db.execute("DELETE FROM email_verifications WHERE expires<?", (now - 86400,))
            self.db.execute(
                "INSERT INTO email_verifications (token_hash,user_id,email,created,expires) "
                "VALUES (?,?,?,?,?)", (_digest(token), user_id, email, now, now + TTL))
            self.db.commit()
        url = f"{self.config.origin}/verify-email?token={token}"
        return bool(self.send(mail_templates.verify_email(email=email, url=url, name=name,
                                                          hours=TTL // 3600,
                                                          origin=self.config.origin),
                              email, self.config, f"pivot-verify-{now}-{user_id}"))

    def confirm(self, token: str) -> tuple[int, dict]:
        now = int(time.time())
        with self.lock:
            row = self.db.execute(
                "SELECT v.user_id, v.email, u.email FROM email_verifications v "
                "JOIN users u ON u.id=v.user_id WHERE v.token_hash=? AND v.used IS NULL "
                "AND v.expires>?", (_digest(str(token or "")), now)).fetchone()
            # The link confirms the address it was sent to; if the account's
            # address has changed since, it confirms nothing.
            if not row or row[1].lower() != row[2].lower():
                return 400, {"error": "This confirmation link has expired or was already used. "
                                      "Sign in and send a new one."}
            self.db.execute("UPDATE email_verifications SET used=? WHERE user_id=? "
                            "AND used IS NULL", (now, row[0]))
            self.db.execute("UPDATE users SET email_verified=COALESCE(email_verified, ?) "
                            "WHERE id=?", (now, row[0]))
            self.db.commit()
        return 200, {"ok": True, "email": row[2]}
