"""Sending Pivot's email: one sender address, one SMTP account, every message.

WHY HOSTINGER'S SMTP, AND NOT GMAIL
-----------------------------------
pivotnow.in's mail is hosted at Hostinger. Its SPF record authorises only
Hostinger's servers and its only DKIM key is Hostinger's
(hostingermail-a._domainkey). Alerts used to go out through smtp.gmail.com as a
send-as alias, so Gmail's servers sent mail claiming to be pivotnow.in: SPF
failed, the DKIM signature was gmail.com's, and DMARC alignment failed. With
DMARC at p=none that is delivered, but it is exactly the pattern spam filters
punish. Sending through the mailbox's own provider, as the mailbox itself,
passes SPF and DKIM for pivotnow.in with no DNS change at all.

CONFIGURATION (server-only; never in a NEXT_PUBLIC_ variable)
-------------------------------------------------------------
    CHARTO_MAIL_ENABLED=true            (CHARTO_ALERT_EMAIL_ENABLED also works)
    CHARTO_MAIL_FROM=contact@pivotnow.in
    CHARTO_MAIL_HOST=smtp.hostinger.com
    CHARTO_MAIL_PORT=465                (465 = TLS; 587 = STARTTLS)
    CHARTO_MAIL_USER=contact@pivotnow.in  (defaults to the From address)
    CHARTO_MAIL_PASSWORD_FILE=/etc/pivot/mail-password   (mode 0600 or 0640)
Each CHARTO_MAIL_* name falls back to the older CHARTO_SMTP_* name.
Hostinger allows 1,000 messages a day on Business Starter (rolling 24 hours),
shared by every sender below; alert_email.py stays under it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
import logging
import os
from pathlib import Path
import re
import smtplib
import ssl
import threading

from mail_templates import APP, CONTACT, Mail

log = logging.getLogger("charto.mailer")


def valid_address(value: str) -> bool:
    # One addr-spec only: never a display name, recipient list or header.
    return bool(re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
                            r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?"
                            r"\.[A-Za-z]{2,63}", value)) and len(value) <= 254


def _env(name: str, default: str = "") -> str:
    return os.getenv(f"CHARTO_MAIL_{name}") or os.getenv(f"CHARTO_SMTP_{name}") or default


@dataclass(frozen=True)
class Config:
    # Field order is kept from the alert outbox's original Config so existing
    # positional construction still reads (enabled, username, password, port).
    enabled: bool = False
    username: str = CONTACT
    password: str = field(default="", repr=False)
    port: int = 465
    host: str = "smtp.hostinger.com"
    sender: str = CONTACT
    origin: str = APP

    @classmethod
    def from_env(cls) -> "Config":
        enabled = (os.getenv("CHARTO_MAIL_ENABLED")
                   or os.getenv("CHARTO_ALERT_EMAIL_ENABLED") or "").lower() == "true"
        sender = _env("FROM", CONTACT)
        password = _env("PASSWORD")
        filename = _env("PASSWORD_FILE")
        if enabled and filename:
            try:
                path = Path(filename)
                if path.stat().st_mode & 0o027:
                    raise ValueError("password file must be private")
                password = path.read_text().strip()
            except (OSError, ValueError):
                log.warning("Mail password file is unavailable or not private")
                password = ""
        try:
            port = int(_env("PORT", "465"))
        except ValueError:
            port = 0
        origin = (os.getenv("CHARTO_PUBLIC_ORIGIN") or APP).rstrip("/")
        return cls(enabled, _env("USER", sender), password.replace(" ", ""), port,
                   _env("HOST", "smtp.hostinger.com"), sender, origin)

    @property
    def configured(self) -> bool:
        return (self.enabled and valid_address(self.username)
                and valid_address(self.sender) and bool(self.password)
                and bool(self.host) and self.port in (465, 587))


def build(mail: Mail, recipient: str, config: Config, ref: str) -> EmailMessage:
    """The message, headers and both bodies. Raises on a bad recipient."""
    if not valid_address(recipient):
        raise ValueError("invalid recipient")
    msg = EmailMessage()
    msg["From"] = f"Pivot <{config.sender}>"
    msg["To"] = recipient
    msg["Reply-To"] = config.sender
    msg["Subject"] = mail.subject
    msg["Date"] = formatdate(localtime=False)
    msg["Message-ID"] = (f"<{re.sub(r'[^A-Za-z0-9.-]', '', ref)}@pivotnow.in>" if ref
                         else make_msgid(domain="pivotnow.in"))
    # Machine-sent: tells auto-responders not to reply to it.
    msg["Auto-Submitted"] = "auto-generated"
    if mail.category == "product":
        # Optional mail carries an unsubscribe route that mail clients surface
        # as their own button. Security, billing and service notices do not.
        msg["List-Unsubscribe"] = f"<mailto:{config.sender}?subject=Unsubscribe%20{mail.kind}>"
    msg.set_content(mail.text)
    msg.add_alternative(mail.html, subtype="html")
    return msg


def connect(config: Config) -> smtplib.SMTP:
    """An authenticated SMTP session. The caller sends and closes."""
    context = ssl.create_default_context()
    client = (smtplib.SMTP_SSL(config.host, 465, timeout=15, context=context)
              if config.port == 465 else smtplib.SMTP(config.host, 587, timeout=15))
    try:
        if config.port == 587:
            client.starttls(context=context)
        client.login(config.username, config.password)
    except Exception:
        try:
            client.close()
        except OSError:
            pass
        raise
    return client


def send(mail: Mail, recipient: str, config: Config, ref: str = "") -> None:
    msg = build(mail, recipient, config, ref)
    client = connect(config)
    try:
        client.send_message(msg, from_addr=config.sender, to_addrs=[recipient])
    finally:
        try:
            client.close()
        except OSError:
            pass


def send_later(mail: Mail, recipient: str, config: Config | None = None,
               ref: str = "") -> bool:
    """Send on a background thread, so a request never waits on SMTP.

    For one-off account mail (a password reset). Returns False, sending
    nothing, when mail is not configured. Failures are logged without the
    recipient or the server's reply, which can echo it."""
    config = config or Config.from_env()
    if not config.configured or not valid_address(recipient):
        return False

    def run():
        try:
            send(mail, recipient, config, ref)
        except Exception as exc:          # noqa: BLE001 — never crash the caller
            log.warning("Mail %s could not be sent (%s)", mail.kind, type(exc).__name__)

    threading.Thread(target=run, name=f"mail-{mail.kind}", daemon=True).start()
    return True
