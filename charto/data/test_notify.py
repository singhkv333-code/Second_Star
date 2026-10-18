"""notify.py: the right message for each event, exactly once."""
from __future__ import annotations

import sqlite3
import threading
import time

import mailer
import notify

CHROME_MAC = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Safari/537.36"
SAFARI_IPHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1"
CATALOG = {"plans": {"pro": {"name": "Pro", "prices": {"monthly": {"amount": 49900}, "annual": {"amount": 538800}}},
                     "pro_plus": {"name": "Pro+", "prices": {"monthly": {"amount": 99900}}}}}


def make(sent: list):
    db = sqlite3.connect(":memory:", check_same_thread=False)
    db.executescript("""
CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT, name TEXT);
CREATE TABLE strategies (id INTEGER PRIMARY KEY, user_id INTEGER, name TEXT);
CREATE TABLE subscriptions (user_id INTEGER PRIMARY KEY, plan TEXT, cycle TEXT, status TEXT,
  provider TEXT, provider_sub_id TEXT, period_start INTEGER, period_end INTEGER,
  cancel_at_period_end INTEGER DEFAULT 0, grace_until INTEGER);
INSERT INTO users VALUES (1, 'asha@example.com', 'Asha Rao');
INSERT INTO strategies VALUES (7, 1, 'RSI bounce');
""")
    def send(mail, to, config, ref=""):
        sent.append((mail.kind, to, mail.subject))
        return True
    n = notify.Notifier(db, threading.Lock(), catalog=CATALOG, send=send,
                        config=mailer.Config(enabled=True, password="x"))
    return n, db


def kinds(sent):
    return [k for k, _, _ in sent]


def test_formatting():
    assert notify.rupees(538800) == "₹5,388.00"
    assert notify.rupees(12345678900) == "₹12,34,56,789.00"
    assert notify.device_of(CHROME_MAC) == "Chrome on macOS"
    assert notify.device_of(SAFARI_IPHONE) == "Safari on iPhone"


def test_email_signup_is_welcomed_at_once_and_only_once():
    sent: list = []
    n, _ = make(sent)
    n.signed_in(1, how="signup", new_account=True, ua=CHROME_MAC)
    n.drain()
    assert kinds(sent) == ["welcome"]
    # an old confirmation link clicked later does not welcome it again
    n.confirmed_email("asha@example.com")
    n.drain()
    assert kinds(sent) == ["welcome"]


def test_google_signup_is_welcomed_at_once():
    sent: list = []
    n, _ = make(sent)
    n.signed_in(1, how="google", new_account=True, ua=CHROME_MAC)
    n.drain()
    assert kinds(sent) == ["welcome"]


def test_new_device_alert_only_for_an_unseen_device():
    sent: list = []
    n, _ = make(sent)
    n.signed_in(1, how="login", new_account=False, ua=CHROME_MAC)   # first device: silent
    n.signed_in(1, how="login", new_account=False, ua=CHROME_MAC)   # known: silent
    n.signed_in(1, how="login", new_account=False, ua=SAFARI_IPHONE)  # new: alert
    n.signed_in(1, how="login", new_account=False, ua=SAFARI_IPHONE)  # now known
    n.drain()
    assert kinds(sent) == ["new_sign_in"]


def test_purchase_renewal_failure_and_upgrade():
    sent: list = []
    n, _ = make(sent)
    ent = {"id": "sub_1"}
    first = {"plan": "pro", "cycle": "monthly", "status": "active", "period_start": 1, "period_end": 2_000_000_000}
    n.subscription_changed(1, {"status": "created", "plan": "pro"}, first, ent)
    n.subscription_changed(1, {"status": "created", "plan": "pro"}, first, ent)       # webhook + browser
    renewed = dict(first, period_end=2_002_600_000)
    n.subscription_changed(1, dict(first), renewed, ent)
    failed = dict(renewed, status="past_due", grace_until=2_002_900_000)
    n.subscription_changed(1, dict(renewed), failed, ent)
    up = dict(renewed, plan="pro_plus")
    n.subscription_changed(1, dict(renewed), up, ent)
    n.drain()
    assert kinds(sent) == ["subscription_started", "payment_received", "payment_failed", "plan_changed"]
    assert "Pro" in sent[0][2] and sent[0][1] == "asha@example.com"


def test_cancel_refund_and_scheduled_change():
    sent: list = []
    n, _ = make(sent)
    n.cancelled(1, "pro", 2_000_000_000)
    n.change_scheduled(1, "pro_plus", "pro", 2_000_000_000)
    n.refunded(1, "rfnd_1", 49900, "pay_1", 1_999_000_000)
    n.refunded(1, "rfnd_1", 49900, "pay_1", 1_999_000_000)
    n.drain()
    assert kinds(sent) == ["subscription_cancelled", "plan_changed", "refund_issued"]


def test_renewal_reminder_inside_its_window_once():
    sent: list = []
    n, db = make(sent)
    soon = int(time.time()) + 2 * 86400
    db.execute("INSERT INTO subscriptions VALUES (1,'pro','monthly','active','razorpay','sub_1',0,?,0,NULL)", (soon,))
    db.commit()
    assert n.remind_renewals() == 1
    n.remind_renewals()
    n.drain()
    assert kinds(sent) == ["renewal_reminder"]


def test_paper_fills_mail_strategy_trades_only_and_cap():
    sent: list = []
    n, _ = make(sent)
    n.paper_filled(1, {"id": "manual", "symbol": "INFY", "side": "BUY", "quantity": 1, "fill_price": "1500"}, None)
    for i in range(notify.PAPER_FILLS_PER_DAY + 3):
        n.paper_filled(1, {"id": f"f{i}", "symbol": "INFY", "side": "BUY", "quantity": 1, "fill_price": "1500"}, 7)
    n.drain()
    assert kinds(sent) == ["paper_fill"] * notify.PAPER_FILLS_PER_DAY
    assert "INFY" in sent[0][2]


def test_unsent_mail_is_not_recorded_as_sent():
    sent: list = []
    n, db = make(sent)
    n.send = lambda *a, **k: False            # mail not configured
    n.confirmed_email("asha@example.com")
    n.drain()
    assert db.execute("SELECT COUNT(*) FROM mail_sent").fetchone()[0] == 0
