"""Email integration tests: scratch SQLite + fake SMTP, no customer emails."""
import json
import smtplib
import sqlite3
import threading
import time
from types import SimpleNamespace

import pytest

import alert_email as mail
import alerts


@pytest.fixture
def setup(monkeypatch):
    db = sqlite3.connect(":memory:", check_same_thread=False)
    db.executescript("CREATE TABLE users(id INTEGER PRIMARY KEY,email TEXT);"
                     "INSERT INTO users VALUES(7,'owner@example.com');"
                     "INSERT INTO users VALUES(8,'other@example.com');")
    monkeypatch.setattr(alerts.ds, "_users", db)
    monkeypatch.setattr(alerts.ds, "_users_lock", threading.Lock())
    monkeypatch.setattr(alerts, "_MAIL", None)
    alerts._init_db()
    monkeypatch.setattr(alerts, "_load_index", lambda: None)
    monkeypatch.setattr(alerts, "feed_health", lambda *_args: {})
    monkeypatch.setattr(alerts, "_plan_gate", lambda *_args, **_kwargs: (None, ""))
    monkeypatch.setattr(alerts, "_seed", lambda _rule: None)
    monkeypatch.setattr(alerts.ds, "_ensure_symbol", lambda _s: None)
    spec = {"when": [{"left": "close", "op": "cross_up", "right": 100}], "all": True}
    db.execute("INSERT INTO alerts(id,user_id,symbol,interval,spec,freq,state,created,"
               "cstate,last_eval_ts) VALUES(1,7,'TEST','1m',?,'once','armed',?, ?,10)",
               (json.dumps(spec), int(time.time()), '[{"side":-1,"ok":0}]'))
    db.commit()
    rule = alerts.Rule(db.execute(f"SELECT {alerts._LIST_COLS} FROM alerts").fetchone())
    hit = dict(ts=int(time.time()), verb="crossed above", level="100", value=101,
               meta="Once only · 1m", late=False)
    sent = []

    class SMTP:
        def __init__(self, host, port, **kwargs):
            assert host == "smtp.gmail.com"
            assert port in (465, 587)
            assert kwargs["timeout"] == 15
            assert not alerts.ds._users_lock.locked()

        def starttls(self, **kwargs):
            sent.append("tls")

        def login(self, user, password):
            assert user == mail.SENDER
            assert password == "test-app-password"

        def send_message(self, msg, **kwargs):
            assert not alerts.ds._users_lock.locked()
            assert kwargs == {"from_addr": mail.SENDER, "to_addrs": ["owner@example.com"]}
            sent.append(msg)

        def close(self):
            pass

    monkeypatch.setattr(mail.smtplib, "SMTP_SSL", SMTP)
    monkeypatch.setattr(mail.smtplib, "SMTP", SMTP)
    alerts._MAIL.config = mail.Config(True, mail.SENDER, "test-app-password")
    yield SimpleNamespace(db=db, box=alerts._MAIL, rule=rule, hit=hit, sent=sent, SMTP=SMTP)
    alerts._MAIL.stop()
    if alerts._MAIL.worker:
        alerts._MAIL.worker.join(timeout=2)
    db.close()


def fire(s):
    alerts._fire(s.rule, s.hit)
    return s.db.execute("SELECT log_id FROM alert_email_outbox").fetchone()[0]


def status(s):
    return s.db.execute("SELECT status FROM alert_email_outbox").fetchone()[0]


def test_trigger_to_owner_email_and_log_end_to_end(setup):
    s = setup
    ctx = alerts.Ctx("TEST", "1m", [(100, 99, 102, 98, 101, 1000)], False)
    hit = alerts.evaluate(s.rule, ctx)
    assert hit is not None
    alerts._fire(s.rule, hit)
    assert status(s) == "pending"
    assert s.sent == []  # fire itself never does network I/O
    assert s.box.deliver_one()
    assert status(s) == "sent"
    assert len(s.sent) == 1
    msg = s.sent[0]
    assert msg["From"] == "Pivot <info@pivotnow.in>"
    assert msg["To"] == "owner@example.com"
    assert "101" in msg.get_body(preferencelist=("plain",)).get_content()
    assert alerts.evaluate(s.rule, ctx) is None
    assert not s.box.deliver_one()
    _, out = alerts.api_list(7)
    assert out["log"][0]["email_status"] == "sent"
    assert alerts.api_list(8)[1]["log"] == []


def test_enqueue_is_idempotent_per_fire(setup):
    s = setup
    lid = fire(s)
    with alerts.ds._users_lock:
        s.box.enqueue_locked(lid, s.rule, s.hit)
        s.db.commit()
    assert s.db.execute("SELECT COUNT(*) FROM alert_email_outbox").fetchone()[0] == 1


def test_restart_recovers_pending_without_replaying_old_log(setup):
    s = setup
    fire(s)
    recovered = mail.Outbox(s.db, alerts.ds._users_lock, config=s.box.config)
    assert recovered.deliver_one()
    assert not recovered.deliver_one()
    assert len(s.sent) == 1


def test_lost_result_after_restart_not_resent(setup):
    s = setup
    fire(s)
    s.db.execute("UPDATE alert_email_outbox SET status='sending',updated=0")
    s.db.commit()
    assert not s.box.deliver_one()
    assert status(s) == "uncertain"
    assert s.sent == []


def test_unconfigured_never_claims_or_fakes_delivery(setup):
    s = setup
    fire(s)
    s.box.config = mail.Config()
    assert not s.box.deliver_one()
    assert status(s) == "pending"
    assert not alerts.api_list(7)[1]["email_delivery"]["configured"]
    assert "unavailable" in alerts._delivery_note({"email": True})


def test_disabled_alert_still_fires_in_app(setup):
    s = setup
    s.rule.spec["email"] = False
    alerts._fire(s.rule, s.hit)
    assert s.db.execute("SELECT COUNT(*) FROM alert_log").fetchone()[0] == 1
    assert s.db.execute("SELECT COUNT(*) FROM alert_email_outbox").fetchone()[0] == 0


def test_email_patch_does_not_rearm_and_cancels_pending(setup):
    s = setup
    fire(s)
    code, out = alerts.api_patch(7, 1, {"email": False})
    assert code == 200
    assert out["alert"]["email"] is False
    assert out["alert"]["state"] == "fired"
    assert status(s) == "cancelled"
    assert not s.box.deliver_one()
    assert alerts.api_patch(8, 1, {"email": True})[0] == 404


def test_rule_edit_preserves_email_preference(setup):
    s = setup
    alerts.api_patch(7, 1, {"email": False})
    assert alerts.api_patch(7, 1, {"when": s.rule.when})[1]["alert"]["email"] is False


def test_deleted_alert_cancels_pending(setup):
    fire(setup)
    assert alerts.api_patch(7, 1, {"delete": True})[0] == 200
    assert status(setup) == "cancelled"


def test_atomic_rollback_on_enqueue_failure(setup, monkeypatch):
    def fail(*_args):
        raise sqlite3.OperationalError("fixture")
    monkeypatch.setattr(setup.box, "enqueue_locked", fail)
    with pytest.raises(sqlite3.OperationalError):
        alerts._fire(setup.rule, setup.hit)
    assert setup.db.execute("SELECT COUNT(*) FROM alert_log").fetchone()[0] == 0
    assert setup.db.execute("SELECT state FROM alerts").fetchone()[0] == "armed"


@pytest.mark.parametrize("error,expected", [
    (smtplib.SMTPDataError(451, b"temporary"), "pending"),
    (smtplib.SMTPDataError(550, b"permanent"), "failed"),
    (smtplib.SMTPRecipientsRefused({"owner@example.com": (550, b"no")}), "failed"),
    (smtplib.SMTPServerDisconnected("lost after DATA"), "uncertain"),
])
def test_smtp_failures_are_classified(setup, monkeypatch, error, expected):
    s = setup
    fire(s)
    def fail(*_args, **_kwargs):
        raise error
    monkeypatch.setattr(s.SMTP, "send_message", fail)
    assert s.box.deliver_one()
    assert status(s) == expected
    assert s.db.execute("SELECT sent_at FROM alert_email_outbox").fetchone()[0] is None


def test_auth_failure_circuit_breaker(setup, monkeypatch):
    s = setup
    fire(s)
    def fail(*_args):
        raise smtplib.SMTPAuthenticationError(535, b"private server message")
    monkeypatch.setattr(s.SMTP, "login", fail)
    assert s.box.deliver_one()
    assert status(s) == "pending"
    assert not s.box.deliver_one()
    assert s.box.status()["error"] == "authentication_failed"


def test_rejected_temporary_send_retries_successfully(setup, monkeypatch):
    s = setup
    fire(s)
    original = s.SMTP.send_message
    def fail(*_args, **_kwargs):
        raise smtplib.SMTPDataError(451, b"busy")
    monkeypatch.setattr(s.SMTP, "send_message", fail)
    s.box.deliver_one()
    s.db.execute("UPDATE alert_email_outbox SET next_attempt=0")
    s.db.commit()
    monkeypatch.setattr(s.SMTP, "send_message", original)
    s.box.deliver_one()
    assert status(s) == "sent"
    assert s.db.execute("SELECT attempts FROM alert_email_outbox").fetchone()[0] == 2


def test_changed_account_address_not_sent_to_old_address(setup):
    s = setup
    fire(s)
    s.db.execute("UPDATE users SET email='new@example.com' WHERE id=7")
    s.db.commit()
    s.box.deliver_one()
    assert status(s) == "cancelled"
    assert s.sent == []


def test_expiry_bounds_stale_backlog(setup):
    fire(setup)
    setup.db.execute("UPDATE alert_email_outbox SET created=0")
    setup.db.commit()
    assert not setup.box.deliver_one()
    assert status(setup) == "expired"


def test_html_escape_and_header_injection(setup):
    fire(setup)
    payload = json.loads(setup.db.execute("SELECT payload FROM alert_email_outbox").fetchone()[0])
    payload.update(symbol="TEST\r\nBcc: attacker", note='<script>alert("x")</script>')
    msg = mail.message("owner@example.com", payload, 1, setup.box.config)
    assert msg["Bcc"] is None
    assert "<script>" not in msg.get_body(preferencelist=("html",)).get_content()
    with pytest.raises(ValueError):
        mail.message("owner@example.com\r\nBcc: attacker@example.com", payload, 1, setup.box.config)


def test_starttls_supported(setup):
    fire(setup)
    setup.box.config = mail.Config(True, mail.SENDER, "test-app-password", 587)
    setup.box.deliver_one()
    assert setup.sent[0] == "tls"
    assert status(setup) == "sent"


def test_quick_worker_and_status_callback(setup):
    s = setup
    event = threading.Event()
    s.box.notify = lambda _uid, _ev: event.set()
    s.box.start()
    fire(s)
    assert event.wait(2)
    assert status(s) == "sent"


def test_password_file_private_and_redacted(tmp_path, monkeypatch):
    path = tmp_path / "smtp-secret"
    path.write_text("test app password")
    path.chmod(0o600)
    monkeypatch.setenv("CHARTO_ALERT_EMAIL_ENABLED", "true")
    monkeypatch.setenv("CHARTO_SMTP_PASSWORD_FILE", str(path))
    config = mail.Config.from_env()
    assert config.configured
    assert config.password not in repr(config)
    path.chmod(0o644)
    assert not mail.Config.from_env().configured


def test_email_boolean_validation(setup):
    with pytest.raises(alerts.Unspeakable, match="email must be"):
        alerts._validate(dict(symbol="TEST", interval="1m", when=setup.rule.when, email="false"), 7)


@pytest.mark.parametrize("limit", ["DAILY_LIMIT", "HOURLY_USER_LIMIT"])
def test_mail_quota_keeps_job_pending(setup, monkeypatch, limit):
    fire(setup)
    monkeypatch.setattr(mail, limit, 0)
    assert not setup.box.deliver_one()
    assert status(setup) == "pending"
    assert setup.sent == []


def test_late_trigger_is_explicit_in_message(setup):
    setup.hit["late"] = True
    setup.hit["ts"] = 1_700_000_000
    fire(setup)
    setup.box.deliver_one()
    body = setup.sent[0].get_body(preferencelist=("plain",)).get_content()
    assert "historical" in body
    assert "IST" in body


def test_chat_created_alert_email_and_honest_provider_note(setup):
    setup.box.config = mail.Config()
    out = alerts.tool_set_alert("TEST", "1m", setup.rule.when, user_id=7, email=True)
    assert out["alert"]["email"] is True
    assert "Email delivery is unavailable" in out["_note"]
    assert out["_render_hint"] == "alert_card"
    changed = alerts.tool_update_alert(out["alert"]["id"], user_id=7, email=False)
    assert changed["alert"]["email"] is False
    assert "Email is disabled" in changed["_note"]
