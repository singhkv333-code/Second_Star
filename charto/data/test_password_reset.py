"""Password reset: scratch SQLite, a recording sender, no network."""
import hashlib
import sqlite3
import threading

import pytest

import mailer
import password_reset as pr


def _hash(pw: str, salt: bytes) -> bytes:
    return hashlib.sha256(salt + pw.encode()).digest()


@pytest.fixture
def box():
    db = sqlite3.connect(":memory:", check_same_thread=False)
    db.executescript(
        "CREATE TABLE users(id INTEGER PRIMARY KEY,email TEXT UNIQUE,name TEXT,"
        "pw_hash BLOB,pw_salt BLOB,created INTEGER);"
        "CREATE TABLE sessions(token TEXT,user_id INTEGER,created INTEGER,last_seen INTEGER);"
        "INSERT INTO users VALUES(7,'owner@example.com','Asha',x'00',x'00',0);"
        "INSERT INTO sessions VALUES('old-session',7,0,0);")
    sent = []
    config = mailer.Config(True, "contact@pivotnow.in", "pw", origin="https://app.pivotnow.in")
    r = pr.Resets(db, threading.Lock(), _hash,
                  send=lambda mail, to, cfg, ref: sent.append((mail, to)), config=config)
    return r, db, sent


def _token(sent) -> str:
    mail = sent[-1][0]
    return mail.text.split("reset-password?token=")[1].split()[0].rstrip(")")


def test_unknown_address_gets_the_same_answer_and_no_mail(box):
    r, _, sent = box
    assert r.request("nobody@example.com", "1.2.3.4") == r.request("owner@example.com", "1.2.3.5")
    assert [to for _, to in sent] == ["owner@example.com"]


def test_reset_changes_password_signs_out_and_notifies(box):
    r, db, sent = box
    r.request("Owner@Example.com ", "1.2.3.4")
    token = _token(sent)
    assert db.execute("SELECT token_hash FROM password_resets").fetchone()[0] != token
    code, uid = r.reset(token, "a-new-password")
    assert (code, uid) == (200, 7)
    salt = db.execute("SELECT pw_salt FROM users WHERE id=7").fetchone()[0]
    assert db.execute("SELECT pw_hash FROM users WHERE id=7").fetchone()[0] == _hash("a-new-password", salt)
    assert db.execute("SELECT COUNT(*) FROM sessions WHERE user_id=7").fetchone()[0] == 0
    assert sent[-1][0].kind == "password_changed"
    code, out = r.reset(token, "another-password")      # one use only
    assert code == 400 and "expired" in out["error"]


def test_expired_and_short_passwords_are_refused(box, monkeypatch):
    r, _, sent = box
    r.request("owner@example.com")
    token = _token(sent)
    assert r.reset(token, "short")[0] == 400
    monkeypatch.setattr(pr.time, "time", lambda: 10 ** 11)
    assert r.reset(token, "long-enough-password")[0] == 400


def test_rate_limits_never_reveal_the_account(box):
    r, _, sent = box
    for _ in range(5):
        assert r.request("owner@example.com", "9.9.9.9")[0] == 200
    assert len(sent) == pr.PER_ADDRESS_HOURLY
    for _ in range(pr.PER_IP_HOURLY):
        r.request("someone@example.com", "8.8.8.8")
    assert r.request("owner@example.com", "8.8.8.8")[0] in (200, 429)


def test_unconfigured_mail_sends_nothing():
    mail = pr.mail_templates.password_reset(email="a@example.com", url="https://app.pivotnow.in/x")
    assert mailer.send_later(mail, "a@example.com", mailer.Config()) is False
