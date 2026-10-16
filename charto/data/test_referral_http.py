"""Access-control checks through the real Charto HTTP handler and a temp DB."""
import secrets
import time

import pytest

from test_entitlements_api import env  # noqa: F401 - existing isolated HTTP fixture
import dataserver as ds
from referrals import Referrals


def _account(api, email):
    cur = api.db.execute("INSERT INTO users(email,name,pw_hash,pw_salt,created) VALUES (?,?,?,?,?)",
                         (email, "Test", secrets.token_bytes(32), secrets.token_bytes(16), int(time.time())))
    api.db.commit()
    return cur.lastrowid, ds._issue_session(cur.lastrowid)


def test_referral_owner_and_admin_access(env, tmp_path, monkeypatch):
    store = Referrals(tmp_path / "referrals.db")
    monkeypatch.setattr(ds, "_referrals", store)
    monkeypatch.setattr(ds, "_ADMIN_EMAILS", {"admin@example.com"})
    uid, token = _account(env, "owner@example.com")
    _, other_token = _account(env, "other@example.com")
    _, admin_token = _account(env, "admin@example.com")
    store.link(uid)
    assert env.get("/referrals/me")[0] == 401
    assert env.get("/referrals/me", token=other_token)[1]["signups"] == 0
    assert env.get("/referrals/me", token=token)[1]["code"] == store.link(uid)
    assert env.get("/admin/referrals", token=token)[0] == 403
    assert env.get("/admin/referrals/reconcile", token=other_token)[0] == 403
    assert env.post("/admin/referrals/approve", {"payment_id": "p"}, token=token)[0] == 403
    assert env.post("/admin/referrals/resolve", {"payment_id": "p", "decision": "clear", "reason": "reviewed account"}, token=token)[0] == 403
    assert env.get("/admin/referrals", token=admin_token)[0] == 200
    store.db.close()
