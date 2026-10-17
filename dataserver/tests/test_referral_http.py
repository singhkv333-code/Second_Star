"""Access-control checks through the real dataserver HTTP handler and a temp DB."""
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
    assert env.get("/admin/referrals", token=other_token)[0] == 403
    assert env.post("/admin/referrals/approve", {"payment_id": "p"}, token=token)[0] == 403
    assert env.post("/admin/referrals/resolve", {"payment_id": "p", "decision": "clear", "reason": "reviewed account"}, token=token)[0] == 403
    code, admin = env.get("/admin/referrals", token=admin_token)
    assert code == 200 and admin["signups"] == [] and admin["review"] == []
    store.db.close()


def test_conversion_to_pro_credits_the_link_that_brought_the_account(env, tmp_path, monkeypatch):
    """No payment provider involved: the account signs up through a link, our
    billing records it on an active Pro period, the referrer is credited."""
    store = Referrals(tmp_path / "referrals.db")
    monkeypatch.setattr(ds, "_referrals", store)
    monkeypatch.setattr(ds, "_ADMIN_EMAILS", {"admin@example.com"})
    for k, v in {"PIVOT_REFERRAL_RATE_BPS": "1500", "PIVOT_REFERRAL_COMMISSION_DAYS": "365"}.items():
        monkeypatch.setenv(k, v)
    referrer, _ = _account(env, "referrer@example.com")
    customer, _ = _account(env, "customer@example.com")
    _, admin_token = _account(env, "admin@example.com")
    token = store.visit(store.link(referrer))
    assert store.signup(customer, token, days=30, touch="last", rate_bps=1500, duration_days=365, terms_version="t")
    price = int(ds._ent.CATALOG["plans"]["pro"]["prices"]["monthly"]["amount"])
    start = int(time.time())
    ds._on_subscription_change(customer, None, {"plan": "pro", "cycle": "monthly", "status": "active",
                                                "period_start": start, "period_end": start + 30 * 86400}, {})
    ds._on_subscription_change(customer, None, {"plan": "pro", "cycle": "monthly", "status": "active",
                                                "period_start": start, "period_end": start + 30 * 86400}, {})
    summary = store.summary(referrer)
    assert summary["paid_customers"] == 1
    assert summary["totals_paise"]["pending"] == (price * 100 // 118) * 1500 // 10000
    code, admin = env.get("/admin/referrals", token=admin_token)
    row = admin["signups"][0]
    assert code == 200 and row["referrer_email"] == "referrer@example.com"
    assert row["subscriber_email"] == "customer@example.com" and row["paid_periods"] == 1
    store.db.close()
