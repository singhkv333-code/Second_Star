"""Sign-up checks, attempt limits and email confirmation. No network: the
email checks run with deliverability off, mail goes to a recording sender."""
import sqlite3
import threading

import pytest

import auth_guard as g
import email_check as ec
import mailer


@pytest.mark.parametrize("raw,ok", [
    ("asha@gmail.com", "asha@gmail.com"),
    ("  Asha.Rao+pivot@Gmail.COM ", "asha.rao+pivot@gmail.com"),
    ("asha@mail.com", "asha@mail.com"),
    ("asha@ymail.com", "asha@ymail.com"),
])
def test_good_addresses_are_normalised(raw, ok):
    assert ec.check(raw, deliverability=False) == (ok, None)


@pytest.mark.parametrize("raw,says", [
    ("", "Enter your email"),
    ("a@b", "valid email"),
    ("asha@@gmail.com", "valid email"),
    ("a b@gmail.com", "valid email"),
    ("asha@10.0.0.1", "valid email"),
    ("asha@localhost", "valid email"),
    ("asha@mailinator.com", "permanent email"),
    ("x@sub.yopmail.com", "permanent email"),
    ("asha@gmial.com", "Did you mean asha@gmail.com?"),
    ("asha@yaho.com", "Did you mean asha@yahoo.com?"),
    ("asha@redifmail.com", "Did you mean asha@rediffmail.com?"),
    ("a" * 250 + "@gmail.com", "too long"),
])
def test_bad_addresses_are_refused_with_a_reason(raw, says):
    address, reason = ec.check(raw, deliverability=False)
    assert address is None and says in reason


@pytest.mark.parametrize("pw,email,bad", [
    ("short", "", True),
    ("x" * 257, "", True),
    ("password123", "", True),
    ("aaaaaaaaaaaa", "", True),
    ("asha.rao", "asha.rao@gmail.com", True),
    ("a-good-passphrase", "asha@gmail.com", False),
])
def test_password_policy(pw, email, bad):
    assert (g.password_problem(pw, email) is not None) is bad


def test_limiter_window_and_clear():
    t = [0.0]
    lim = g.Limiter({"r": (3, 60)}, clock=lambda: t[0])
    assert [lim.allow("r", "k") for _ in range(4)] == [True, True, True, False]
    assert lim.allow("r", "other")              # keys are separate
    t[0] = 61
    assert lim.allow("r", "k")                  # the window slid
    lim.clear("r", "k")
    assert all(lim.allow("r", "k") for _ in range(3))


@pytest.fixture
def box():
    db = sqlite3.connect(":memory:", check_same_thread=False)
    db.executescript("CREATE TABLE users(id INTEGER PRIMARY KEY,email TEXT UNIQUE,name TEXT,"
                     "pw_hash BLOB,pw_salt BLOB,created INTEGER);"
                     "INSERT INTO users VALUES(7,'asha@example.com','Asha',x'00',x'00',0);")
    sent = []
    cfg = mailer.Config(True, "contact@pivotnow.in", "pw", origin="https://app.pivotnow.in")
    v = g.Verifications(db, threading.Lock(), send=lambda m, to, c, ref: sent.append((m, to)) or True,
                        config=cfg)
    return v, db, sent


def _token(sent):
    return sent[-1][0].text.split("verify-email?token=")[1].split()[0].rstrip(")")


def test_confirmation_link_confirms_once(box):
    v, db, sent = box
    assert not v.verified(7)
    assert v.start(7, "asha@example.com", "Asha")
    assert sent[-1][0].kind == "verify_email" and sent[-1][1] == "asha@example.com"
    token = _token(sent)
    assert db.execute("SELECT token_hash FROM email_verifications").fetchone()[0] != token
    assert v.confirm(token) == (200, {"ok": True, "email": "asha@example.com"})
    assert v.verified(7)
    assert v.confirm(token)[0] == 400                    # one use only


def test_link_for_an_old_address_confirms_nothing(box):
    v, db, sent = box
    v.start(7, "asha@example.com")
    db.execute("UPDATE users SET email='asha@new.example' WHERE id=7")
    assert v.confirm(_token(sent))[0] == 400 and not v.verified(7)


def test_expired_link_is_refused(box, monkeypatch):
    v, _, sent = box
    v.start(7, "asha@example.com")
    monkeypatch.setattr(g.time, "time", lambda: 10 ** 11)
    assert v.confirm(_token(sent))[0] == 400
