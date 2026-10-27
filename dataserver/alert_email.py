"""Transactional alert-email outbox. No network I/O on the market-data thread.

SMTP cannot guarantee exactly-once delivery: a connection can disappear after
the server accepts DATA. Such attempts (including a process crash in flight)
are marked uncertain, not blindly resent. Explicit temporary SMTP rejections
are retried. `sent` means accepted by SMTP, not delivered to the inbox.
"""
from __future__ import annotations

from datetime import datetime, timezone
from email.message import EmailMessage
import json
import logging
import smtplib
import threading
import time
from zoneinfo import ZoneInfo

import mail_templates
import mailer

log = logging.getLogger("dataserver.alert_email")
SENDER = mailer.CONTACT
MAX_AGE = 86400
MAX_ATTEMPTS = 6
# Hostinger Business Starter sends 1,000 a day on a rolling window, shared with
# every other message from this address; the rest is headroom for account mail
# such as password resets.
DAILY_LIMIT = 900
HOURLY_USER_LIMIT = 60  # one noisy account cannot flood its mailbox
SCHEMA = """
CREATE TABLE IF NOT EXISTS alert_email_outbox (
  log_id INTEGER PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id),
  alert_id INTEGER NOT NULL,
  recipient TEXT NOT NULL,
  payload TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  attempts INTEGER NOT NULL DEFAULT 0,
  created INTEGER NOT NULL,
  next_attempt INTEGER NOT NULL,
  updated INTEGER NOT NULL,
  sent_at INTEGER,
  error TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS alert_email_due
  ON alert_email_outbox(status, next_attempt);
CREATE INDEX IF NOT EXISTS alert_email_user
  ON alert_email_outbox(user_id, log_id);
CREATE INDEX IF NOT EXISTS alert_email_sent
  ON alert_email_outbox(sent_at);
CREATE INDEX IF NOT EXISTS alert_email_user_sent
  ON alert_email_outbox(user_id, sent_at);
"""


valid_address = mailer.valid_address
Config = mailer.Config


def _num(value) -> str:
    """A reading as people write it: grouped, and no more decimals than it has."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return str(value)
    a = abs(value)
    d = 2 if a >= 100 else 3 if a >= 10 else 4 if a >= 1 else 6
    s = f"{value:,.{d}f}"
    return s.rstrip("0").rstrip(".") if "." in s else s


def message(recipient: str, payload: dict, log_id: int, config: Config) -> EmailMessage:
    if not valid_address(recipient):
        raise ValueError("invalid recipient")
    stamp = datetime.fromtimestamp(payload["ts"], timezone.utc).astimezone(
        ZoneInfo("Asia/Kolkata")).strftime("%d %b %Y, %H:%M:%S IST")
    mail = mail_templates.alert_triggered(
        symbol=str(payload["symbol"]), condition=str(payload["condition"]),
        value=_num(payload["value"]), level=str(payload.get("level") or ""),
        verb=str(payload.get("verb") or ""), interval=str(payload["interval"]),
        when=stamp, alert_id=int(payload["alert_id"]),
        note_text=str(payload.get("note") or ""), late=bool(payload.get("late")),
        origin=config.origin)
    return mailer.build(mail, recipient, config, f"pivot-alert-{log_id}")


class Outbox:
    def __init__(self, db, lock, notify=lambda _uid, _event: None, config=None):
        self.db, self.lock, self.notify = db, lock, notify
        self.config = config or Config.from_env()
        self.wake = threading.Event()
        self.stopping = threading.Event()
        self.worker = None
        self.transport_error = ""
        self.retry_transport_at = 0

    def enqueue_locked(self, log_id: int, rule, hit: dict) -> str:
        """Caller holds user DB lock; inserts in the SAME transaction as fire.

        Recipient is resolved only from the authenticated owner's account.
        The immutable payload survives alert deletion and log trimming.
        """
        if rule.spec.get("email", True) is False:
            return "disabled"
        row = self.db.execute("SELECT email FROM users WHERE id=?", (rule.user_id,)).fetchone()
        recipient = str(row[0] if row else "").strip()
        status = "pending" if valid_address(recipient) else "failed"
        lab = rule.label()
        payload = dict(symbol=rule.symbol, interval=rule.interval,
                       alert_id=rule.id, condition=f"{lab['cond']} {lab['level']}".strip(),
                       note=rule.note, **hit)
        now = int(time.time())
        self.db.execute(
            "INSERT OR IGNORE INTO alert_email_outbox "
            "(log_id,user_id,alert_id,recipient,payload,status,created,next_attempt,updated,error) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (log_id, rule.user_id, rule.id, recipient, json.dumps(payload), status,
             now, now, now, "invalid_recipient" if status == "failed" else ""))
        return status

    def status(self) -> dict:
        return {"configured": self.config.configured, "sender": self.config.sender,
                "error": self.transport_error,
                "note": ("Email sends even with the browser closed."
                         if self.config.configured and not self.transport_error else
                         "Email delivery is unavailable; in-app alerts still work.")}

    def _finish(self, job, state, error="", retry_after=0):
        now = int(time.time())
        with self.lock:
            changed = self.db.execute(
                "UPDATE alert_email_outbox SET status=?,error=?,updated=?,"
                "next_attempt=?,sent_at=? WHERE log_id=? AND status='sending'",
                (state, error, now, now + retry_after, now if state == "sent" else None, job[0])).rowcount
            self.db.commit()
        if not changed:
            return
        try:
            self.notify(job[1], {"type": "email_delivery", "log_id": job[0],
                                 "email_status": state, "email_delivery": self.status()})
        except Exception:
            log.warning("Email delivery status could not be pushed to a tab")

    def deliver_one(self) -> bool:
        now = int(time.time())
        with self.lock:
            # A lost SMTP result must not cause duplicate customer mail on boot.
            self.db.execute("UPDATE alert_email_outbox SET status='uncertain',"
                            "error='interrupted_send',updated=? WHERE status='sending' "
                            "AND updated<?", (now, now - 120))
            self.db.execute("UPDATE alert_email_outbox SET status='expired',updated=? "
                            "WHERE status='pending' AND created<?", (now, now - MAX_AGE))
            self.db.execute("DELETE FROM alert_email_outbox WHERE status!='pending' "
                            "AND status!='sending' AND updated<?", (now - 30 * MAX_AGE,))
            self.db.commit()
            if not self.config.configured:
                return False
            if now < self.retry_transport_at:
                return False
            sent = self.db.execute("SELECT COUNT(*) FROM alert_email_outbox WHERE "
                                   "sent_at>?", (now - MAX_AGE,)).fetchone()[0]
            if sent >= DAILY_LIMIT:
                self.transport_error = "daily_limit"
                return False
            job = self.db.execute(
                "SELECT log_id,user_id,recipient,payload,attempts FROM alert_email_outbox q "
                "WHERE status='pending' AND next_attempt<=? AND "
                "(SELECT COUNT(*) FROM alert_email_outbox s WHERE s.user_id=q.user_id "
                "AND s.sent_at>?)<? ORDER BY log_id LIMIT 1",
                (now, now - 3600, HOURLY_USER_LIMIT)).fetchone()
            if not job:
                return False
            owner = self.db.execute("SELECT email FROM users WHERE id=?", (job[1],)).fetchone()
            if not owner or str(owner[0]).strip() != job[2]:
                self.db.execute("UPDATE alert_email_outbox SET status='cancelled',"
                                "error='account_changed',updated=? WHERE log_id=?", (now, job[0]))
                self.db.commit()
                return True
            changed = self.db.execute(
                "UPDATE alert_email_outbox SET status='sending',attempts=attempts+1,"
                "updated=? WHERE log_id=? AND status='pending'", (now, job[0])).rowcount
            self.db.commit()
        if not changed:
            return True
        # The shared DB lock is NEVER held during DNS/TLS/SMTP.
        sending = False
        try:
            msg = message(job[2], json.loads(job[3]), job[0], self.config)
            client = mailer.connect(self.config)
            try:
                sending = True
                client.send_message(msg, from_addr=self.config.sender, to_addrs=[job[2]])
                self.transport_error = ""
                self._finish(job, "sent")
            finally:
                # QUIT failure after successful DATA must not requeue a send.
                try:
                    client.close()
                except OSError:
                    pass
        except smtplib.SMTPAuthenticationError:
            self.transport_error = "authentication_failed"
            self.retry_transport_at = now + 300
            self._finish(job, "pending", "authentication_failed", 300)
        except (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused,
                smtplib.SMTPDataError) as exc:
            codes = ([v[0] for v in exc.recipients.values()]
                     if isinstance(exc, smtplib.SMTPRecipientsRefused) else [exc.smtp_code])
            retry = all(400 <= code < 500 for code in codes) and job[4] + 1 < MAX_ATTEMPTS
            self._finish(job, "pending" if retry else "failed",
                         "smtp_rejected", min(300, 5 * 2 ** job[4]))
        except (OSError, smtplib.SMTPException):
            self.transport_error = "smtp_connection"
            self.retry_transport_at = now + 5
            retry = not sending and job[4] + 1 < MAX_ATTEMPTS
            self._finish(job, "pending" if retry else "uncertain" if sending else "failed",
                         "smtp_connection", min(300, 5 * 2 ** job[4]))
        except (ValueError, KeyError, TypeError, OverflowError):
            self._finish(job, "failed", "invalid_message")
        return True

    def start(self):
        if self.worker and self.worker.is_alive():
            return
        self.stopping.clear()
        self.worker = threading.Thread(target=self._run, name="alert-email", daemon=True)
        self.worker.start()

    def _run(self):
        while not self.stopping.is_set():
            try:
                if self.deliver_one():
                    continue
            except Exception:
                # No recipient, credential, SMTP response or alert payload in logs.
                log.error("Alert email worker pass failed", exc_info=False)
            self.wake.wait(5)
            self.wake.clear()

    def stop(self):
        self.stopping.set()
        self.wake.set()
