"""Black-box tests for the paywall: every GATE/METER route in
docs/paywall/ENDPOINTS.md, driven over real HTTP through dataserver.Handler
against a throwaway account database.

What each test proves, for the route it names:
  * a Free user is refused at the limit with the agreed 402 contract
    {error, code, feature, limit, used, plan, upgrade_to[, resets_at]};
  * a Pro user is allowed past that same point;
  * the refusal is the SERVER's — a client that claims a plan gets nothing;
  * a retry with the same idempotency key is not a second charge;
  * a downgrade never deletes anything.

Nothing here touches charto_users.db, the market store's bars, the model or
Razorpay: CHARTO_USERS_DB points at a temp file BEFORE dataserver is
imported, every test gets its own fresh database, and the model, the bar
reader and the payment wire are replaced with fakes.

Run from charto/data:
    ../../pivot/.venv/bin/python -m pytest -q test_entitlements_api.py
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sqlite3
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

# Must precede `import dataserver`: its import opens the account DB.
if "dataserver" not in sys.modules:
    os.environ["CHARTO_USERS_DB"] = tempfile.mkstemp(
        prefix="ent_boot_", suffix=".db")[1]

import pytest  # noqa: E402

import dataserver as ds  # noqa: E402
import alerts  # noqa: E402
import billing  # noqa: E402
import entitlements as ent  # noqa: E402

DAY = 86400
PRICE = [{"left": "close", "op": "above", "right": 100}]
TECH = [{"left": "rsi(14)", "op": "below", "right": 30}]


# ══ the harness ════════════════════════════════════════════════════════════

def _schema_sql() -> list[str]:
    """The account DB's own DDL, read off the boot database — so these tests
    follow the schema dataserver actually creates, not a copy of it."""
    rows = ds._users.execute(
        "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL AND "
        "name NOT LIKE 'sqlite_%' ORDER BY type='index'").fetchall()
    return [r[0] for r in rows]


class Api:
    def __init__(self, base: str):
        self.base = base

    def call(self, method: str, path: str, body=None, token: str | None = None,
             headers: dict | None = None, raw: bytes | None = None):
        data = raw if raw is not None else (
            json.dumps(body).encode() if body is not None else None)
        h = {"Content-Type": "application/json", **(headers or {})}
        if token:
            h["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers=h)
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as exc:
            txt = exc.read()
            try:
                return exc.code, json.loads(txt or b"{}")
            except ValueError:
                return exc.code, {"_raw": txt.decode(errors="replace")}

    def get(self, path, **kw):
        return self.call("GET", path, **kw)

    def post(self, path, body=None, **kw):
        return self.call("POST", path, body if body is not None else {}, **kw)

    def signup(self, email: str) -> tuple[int, str]:
        code, out = self.post("/auth/signup", {"email": email,
                                               "password": "correct-horse-9"})
        assert code == 200, out
        return out["user"]["id"], out["token"]


class FakeModel:
    """Stands in for llm_chat: counts calls, can be told to fail."""

    def __init__(self):
        self.calls = 0
        self.fail = False

    def __call__(self, messages, ctx=None):
        self.calls += 1
        if self.fail:
            return {"error": "model outage"}
        return {"reply": f"answer {self.calls}"}


def fake_bars(n_total: int = 30_000):
    """A synthetic intraday series: n_total one-minute bars ending at a fixed
    time. Shape matches get_bars: {bars:[{t,o,h,l,c,v}], has_more}."""
    end = 1_790_000_000

    def get_bars(symbol, interval, to, limit):
        step = 60 * ds.INTRADAY_MIN.get(interval, 1440)
        ts = [end - (n_total - 1 - i) * step for i in range(n_total)]
        if to:
            ts = [t for t in ts if t < to]
        picked = ts[-limit:]
        return {"symbol": symbol, "interval": interval,
                "bars": [{"t": t, "o": 1, "h": 1, "l": 1, "c": 1, "v": 1}
                         for t in picked],
                "has_more": len(ts) > len(picked)}
    return get_bars


@pytest.fixture(autouse=True)
def _charto_gates_on(monkeypatch):
    """These tests are charto's enforcement. Pivot is the one paywall now, so
    the gates are off by default (entitlements.paywall_enabled); test them on."""
    monkeypatch.setattr(ent, "paywall_enabled", lambda: True)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """A fresh account DB, the real Handler on a free port, fakes for the
    model / bars / symbol store."""
    db = sqlite3.connect(tmp_path / "users.db", check_same_thread=False)
    for sql in _schema_sql():
        try:
            db.execute(sql)
        except sqlite3.OperationalError:
            pass
    db.commit()
    lock = threading.Lock()
    monkeypatch.setattr(ds, "_users", db)
    monkeypatch.setattr(ds, "_users_lock", lock)
    monkeypatch.setattr(ent, "_con", None)
    monkeypatch.setattr(ent, "_lock", None)
    ent.bind(db, lock)
    alerts._init_db()
    monkeypatch.setattr(ds, "_alerts", alerts)
    # alerts: no market data behind the engine
    monkeypatch.setattr(ds, "_ensure_symbol", lambda _s: None)
    monkeypatch.setattr(alerts, "_seed", lambda _r: None)
    monkeypatch.setattr(alerts, "ensure_feed", lambda _s: {})
    monkeypatch.setattr(alerts, "feed_health", lambda *_a, **_k: {})
    monkeypatch.setattr(alerts, "_touch_chart", lambda: None)
    # the model
    model = FakeModel()
    monkeypatch.setattr(ds, "llm_chat", model)
    # bars
    monkeypatch.setattr(ds, "get_bars", fake_bars())
    monkeypatch.setattr(ds, "_HIST_CUT", {})
    # leases are process memory; start empty
    monkeypatch.setattr(ds, "_leases", {})
    monkeypatch.setattr(ds, "_evicted", {})

    srv = ThreadingHTTPServer(("127.0.0.1", 0), ds.Handler)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    api = Api(f"http://127.0.0.1:{srv.server_address[1]}")
    api.db, api.model = db, model
    yield api
    srv.shutdown()
    srv.server_close()
    db.close()


def make_plan(uid: int, plan: str) -> None:
    now = int(time.time())
    ent.set_subscription(uid, plan=plan, status="active", provider="admin",
                         period_start=now - 60, period_end=now + 30 * DAY)


def assert_402(code: int, out: dict, feature: str, plan: str,
               kind: str = "plan_limit") -> None:
    assert code == 402, (code, out)
    for k in ("error", "code", "feature", "limit", "used", "plan", "upgrade_to"):
        assert k in out, (k, out)
    assert out["code"] == kind, out
    assert out["feature"] == feature, out
    assert out["plan"] == plan, out
    assert isinstance(out["error"], str) and out["error"], out


def alert_body(when, **kw):
    return {"symbol": "TEST", "interval": "5m", "when": when, **kw}


def fill_alerts(api, token, when, n):
    for i in range(n):
        code, out = api.post("/alerts", alert_body(when, note=f"#{i}"),
                             token=token)
        assert code == 200, (i, out)


def count_rows(api, uid, where="1=1"):
    return api.db.execute(f"SELECT COUNT(*) FROM alerts WHERE user_id=? AND "
                          f"{where}", (uid,)).fetchone()[0]


# ══ /billing/plans, /billing/me — the client's mirror ══════════════════════

def test_plans_public_and_me_reflects_the_server_plan(env):
    code, cat = env.get("/billing/plans")
    assert code == 200 and cat["gst_inclusive"] is True
    ids = [p["id"] for p in cat["plans"]]
    assert ids == ["free", "pro", "pro_plus"], ids
    pro = next(p for p in cat["plans"] if p["id"] == "pro")
    assert pro["prices"]["monthly"]["amount"] == 49900
    assert pro["features"]["alerts.price"] == 400

    code, me = env.get("/billing/me")
    assert code == 200 and me["plan"] == "anonymous"

    uid, tok = env.signup("free@x.io")
    assert env.get("/billing/me", token=tok)[1]["plan"] == "free"
    make_plan(uid, "pro")
    code, me = env.get("/billing/me", token=tok)
    assert me["plan"] == "pro"
    assert me["features"]["alerts.price"]["value"] == 400


# ══ POST /alerts — price vs technical, armed caps ══════════════════════════

def test_alert_class_follows_tradingview():
    c = alerts.alert_class
    assert c({"when": PRICE}) == "price"
    assert c({"when": [{"left": "close", "op": "cross_up",
                        "right": "pday.high"}]}) == "price"
    assert c({"when": TECH}) == "technical"
    assert c({"when": [{"left": "close", "op": "cross",
                        "right": "draw:D7"}]}) == "technical"      # drawing
    assert c({"when": [{"left": "close", "op": "rises_pct",
                        "right": 2}]}) == "technical"              # move %
    assert c({"when": [{"left": "close", "op": "enters", "right": 1,
                        "right2": 2}]}) == "technical"             # channel
    assert c({"when": [{"left": "volume", "op": "above",
                        "right": 1000}]}) == "technical"
    assert c({"when": PRICE + TECH}) == "technical"                # tie-break


def test_free_price_alert_cap_and_pro_passes(env):
    uid_f, tf = env.signup("f@x.io")
    uid_p, tp = env.signup("p@x.io")
    make_plan(uid_p, "pro")
    fill_alerts(env, tf, PRICE, 20)
    fill_alerts(env, tp, PRICE, 20)
    code, out = env.post("/alerts", alert_body(PRICE), token=tf)
    assert_402(code, out, "alerts.price", "free")
    assert out["limit"] == 20 and out["used"] == 20 and out["upgrade_to"] == "pro"
    assert count_rows(env, uid_f) == 20              # nothing half-written
    code, out = env.post("/alerts", alert_body(PRICE), token=tp)
    assert code == 200, out
    # the technical pool is separate: a Free user at the price cap can still
    # arm a technical alert
    code, out = env.post("/alerts", alert_body(TECH), token=tf)
    assert code == 200, out


def test_free_technical_cap(env):
    _uid, tf = env.signup("f@x.io")
    fill_alerts(env, tf, TECH, 20)
    code, out = env.post("/alerts", alert_body(TECH), token=tf)
    assert_402(code, out, "alerts.technical", "free")


def test_client_cannot_claim_a_plan(env):
    _uid, tf = env.signup("f@x.io")
    fill_alerts(env, tf, PRICE, 20)
    code, out = env.post(
        "/alerts", alert_body(PRICE, plan="pro_plus", entitlements={
            "alerts.price": 9999}),
        token=tf, headers={"X-Plan": "pro_plus", "X-Entitlements": "all"})
    assert_402(code, out, "alerts.price", "free")
    # nor can an anonymous caller create one at all
    code, out = env.post("/alerts", alert_body(PRICE))
    assert code == 401


def test_pause_frees_a_slot_and_rearm_is_gated(env):
    uid, tf = env.signup("f@x.io")
    fill_alerts(env, tf, PRICE, 20)
    first = env.db.execute("SELECT id FROM alerts WHERE user_id=? ORDER BY id "
                           "LIMIT 1", (uid,)).fetchone()[0]
    assert env.post(f"/alerts/{first}", {"state": "paused"}, token=tf)[0] == 200
    assert env.post("/alerts", alert_body(PRICE), token=tf)[0] == 200
    code, out = env.post(f"/alerts/{first}", {"state": "armed"}, token=tf)
    assert_402(code, out, "alerts.price", "free")
    assert count_rows(env, uid, "state='paused'") == 1


def test_edit_that_changes_class_is_gated(env):
    uid, tf = env.signup("f@x.io")
    fill_alerts(env, tf, TECH, 20)
    code, out = env.post("/alerts", alert_body(PRICE), token=tf)
    assert code == 200
    aid = out["alert"]["id"]
    code, out = env.post(f"/alerts/{aid}", {"when": TECH}, token=tf)
    assert_402(code, out, "alerts.technical", "free")
    # the alert is unchanged, still a price alert
    spec = json.loads(env.db.execute("SELECT spec FROM alerts WHERE id=?",
                                     (aid,)).fetchone()[0])
    assert alerts.alert_class(spec) == "price"
    # an edit that stays inside its class, at the cap, is never refused
    code, _ = env.post(f"/alerts/{aid}", {"when": [
        {"left": "close", "op": "below", "right": 90}]}, token=tf)
    assert code == 200


def test_racing_creates_cannot_both_take_the_last_slot(env):
    uid, tf = env.signup("f@x.io")
    fill_alerts(env, tf, PRICE, 19)
    results = []

    def go():
        results.append(env.post("/alerts", alert_body(PRICE), token=tf)[0])
    ths = [threading.Thread(target=go) for _ in range(12)]
    for t in ths:
        t.start()
    for t in ths:
        t.join()
    assert sorted(results).count(200) == 1, results
    assert results.count(402) == 11, results
    assert count_rows(env, uid, "state='armed'") == 20


# ══ alert expiry — clamped, never refused ══════════════════════════════════

def test_expiry_is_clamped_to_the_plan(env):
    uid_f, tf = env.signup("f@x.io")
    uid_x, tx = env.signup("x@x.io")
    make_plan(uid_x, "pro_plus")
    now = int(time.time())
    code, out = env.post("/alerts", alert_body(PRICE), token=tf)   # open-ended ask
    assert code == 200
    exp = out["alert"]["expires"]
    assert exp and abs(exp - (now + 60 * DAY)) < 120, exp
    assert out.get("plan_note")
    aid = out["alert"]["id"]
    # a patch cannot make a Free alert eternal
    code, out = env.post(f"/alerts/{aid}", {"expires": None}, token=tf)
    assert code == 200
    assert out["alert"]["expires"] and out["alert"]["expires"] <= now + 60 * DAY + 120
    code, out = env.post(f"/alerts/{aid}", {"expires": now + 400 * DAY}, token=tf)
    assert out["alert"]["expires"] <= now + 60 * DAY + 120
    # Pro+ keeps open-ended alerts open-ended
    code, out = env.post("/alerts", alert_body(PRICE), token=tx)
    assert code == 200 and out["alert"]["expires"] is None


def test_chat_tool_path_shares_the_alert_gate(env):
    uid, tf = env.signup("f@x.io")
    fill_alerts(env, tf, PRICE, 20)
    out = alerts.tool_set_alert(symbol="TEST", interval="5m", when=PRICE,
                                user_id=uid)
    assert out.get("code") == "plan_limit", out
    assert "alert" not in out


# ══ downgrade — pause, clamp, never delete ═════════════════════════════════

def _sign(raw: bytes, secret: str) -> str:
    return hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()


@pytest.fixture()
def razorpay(monkeypatch):
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_x")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "key-secret")
    monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", "hook-secret")
    monkeypatch.setenv("RAZORPAY_PLAN_PRO_MONTHLY", "plan_pro_m")
    monkeypatch.setenv("RAZORPAY_PLAN_PRO_PLUS_MONTHLY", "plan_pp_m")
    calls = []

    def wire(method, path, body=None):
        calls.append((method, path, body))
        if method == "POST" and path == "/subscriptions":
            return {"id": "sub_1", "short_url": "https://rzp.io/x"}
        return {}
    monkeypatch.setattr(billing, "http", wire)
    return calls


def hook(api, event, entity, eid, secret="hook-secret", sig=None):
    raw = json.dumps({"event": event, "payload": {
        "subscription": {"entity": entity}}}).encode()
    return api.call("POST", "/billing/webhook", raw=raw, headers={
        "X-Razorpay-Signature": sig or _sign(raw, secret),
        "x-razorpay-event-id": eid})


def sub(status, **kw):
    now = int(time.time())
    return {"id": "sub_1", "plan_id": "plan_pro_m", "status": status,
            "current_start": now - DAY, "current_end": now + 29 * DAY, **kw}


def test_webhook_signature_redelivery_and_activation(env, razorpay):
    uid, tok = env.signup("buyer@x.io")
    code, out = env.post("/billing/checkout", {"plan": "pro"}, token=tok)
    assert code == 200 and out["subscription_id"] == "sub_1"
    assert env.get("/billing/me", token=tok)[1]["plan"] == "free"  # not paid yet
    # a forged or mis-keyed signature changes nothing
    code, _ = hook(env, "subscription.activated", sub("active"), "evt_1",
                   secret="wrong")
    assert code == 400
    assert env.get("/billing/me", token=tok)[1]["plan"] == "free"
    code, out = hook(env, "subscription.activated", sub("active"), "evt_1")
    assert code == 200 and out.get("status") == "active"
    assert env.get("/billing/me", token=tok)[1]["plan"] == "pro"
    # redelivery of the same event is acknowledged, not re-applied
    code, out = hook(env, "subscription.activated", sub("active"), "evt_1")
    assert code == 200 and out.get("duplicate") is True
    assert env.db.execute("SELECT COUNT(*) FROM billing_events").fetchone()[0] == 1


def test_pending_keeps_pro_through_grace_then_lapses(env, razorpay, monkeypatch):
    uid, tok = env.signup("buyer@x.io")
    env.post("/billing/checkout", {"plan": "pro"}, token=tok)
    hook(env, "subscription.activated", sub("active"), "evt_1")
    hook(env, "subscription.pending", sub("pending"), "evt_2")
    assert ent.plan_of(uid) == "pro"                    # in grace
    later = int(time.time()) + (ent.GRACE_DAYS + 1) * DAY
    monkeypatch.setattr(ent, "_now", lambda: later)
    assert ent.plan_of(uid) == "free"                   # grace over


def test_cancel_runs_to_period_end(env, razorpay, monkeypatch):
    uid, tok = env.signup("buyer@x.io")
    env.post("/billing/checkout", {"plan": "pro"}, token=tok)
    hook(env, "subscription.activated", sub("active"), "evt_1")
    code, out = env.post("/billing/cancel", token=tok)
    assert code == 200 and out["active_until"]
    assert razorpay[-1][1] == "/subscriptions/sub_1/cancel"
    hook(env, "subscription.cancelled", sub("cancelled"), "evt_3")
    assert ent.plan_of(uid) == "pro"                    # paid through
    pe = sub("cancelled")["current_end"]
    monkeypatch.setattr(ent, "_now", lambda: pe + 1)
    assert ent.plan_of(uid) == "free"


def test_halted_downgrade_pauses_never_deletes(env, razorpay):
    uid, tok = env.signup("buyer@x.io")
    env.post("/billing/checkout", {"plan": "pro"}, token=tok)
    hook(env, "subscription.activated", sub("active"), "evt_1")
    fill_alerts(env, tok, PRICE, 30)                    # over Free's 20
    fill_alerts(env, tok, TECH, 25)                     # over Free's 20
    before = count_rows(env, uid)
    code, _ = hook(env, "subscription.halted", sub("halted"), "evt_9")
    assert code == 200
    assert ent.plan_of(uid) == "free"
    assert count_rows(env, uid) == before == 55         # NOTHING deleted
    assert count_rows(env, uid, "state='paused'") == 15
    counts = alerts.armed_counts(uid)
    assert counts == {"price": 20, "technical": 20}, counts
    # the oldest are the ones kept running
    kept = [r[0] for r in env.db.execute(
        "SELECT note FROM alerts WHERE user_id=? AND state='armed' AND "
        "spec LIKE '%close%' AND spec NOT LIKE '%rsi%' ORDER BY id", (uid,))]
    assert kept == [f"#{i}" for i in range(20)], kept
    # every armed alert now expires inside Free's horizon
    horizon = int(time.time()) + 60 * DAY + 120
    assert env.db.execute("SELECT COUNT(*) FROM alerts WHERE user_id=? AND "
                          "state='armed' AND (expires IS NULL OR expires>?)",
                          (uid, horizon)).fetchone()[0] == 0
    # over the limit: new ones are refused, deleting always works
    code, out = env.post("/alerts", alert_body(PRICE), token=tok)
    assert_402(code, out, "alerts.price", "free")
    paused = env.db.execute("SELECT id FROM alerts WHERE user_id=? AND "
                            "state='paused' LIMIT 1", (uid,)).fetchone()[0]
    assert env.post(f"/alerts/{paused}", {"delete": True}, token=tok)[0] == 200


def test_stale_webhook_does_not_undo_a_later_charge(env, razorpay, monkeypatch):
    """FINDING probe. Razorpay does not promise order. A `pending` from a
    failed first attempt that lands AFTER the successful retry's `charged`
    must not put a paying user back into grace (and then Free)."""
    uid, tok = env.signup("buyer@x.io")
    env.post("/billing/checkout", {"plan": "pro"}, token=tok)
    now = int(time.time())
    hook(env, "subscription.charged", sub("active", current_start=now,
                                         current_end=now + 30 * DAY), "evt_new")
    # the older event, describing the previous cycle
    hook(env, "subscription.pending", sub("pending", current_start=now - 30 * DAY,
                                         current_end=now), "evt_old")
    monkeypatch.setattr(ent, "_now", lambda: now + (ent.GRACE_DAYS + 1) * DAY)
    assert ent.plan_of(uid) == "pro", "a stale pending event downgraded a paid user"


# ══ POST /chat — AI credits ════════════════════════════════════════════════

def chat(api, token=None, text="hi", turn=None, headers=None):
    body = {"messages": [{"role": "user", "content": text}],
            "context": {"symbol": "TEST"}, "chat_id": "c1"}
    if turn:
        body["turn_id"] = turn
    return api.post("/chat", body, token=token, headers=headers)


def ledger_used(api, subject, feature="ai.credits"):
    return api.db.execute("SELECT COALESCE(SUM(amount),0) FROM usage_ledger "
                          "WHERE subject=? AND feature=?",
                          (subject, feature)).fetchone()[0]


def test_free_credits_run_out_and_the_model_is_not_called(env):
    uid, tf = env.signup("f@x.io")
    for i in range(15):
        code, out = chat(env, tf, text=f"q{i}", turn=f"t{i}")
        assert code == 200, (i, out)
    assert out["credits"]["left"] == 0 and out["credits"]["limit"] == 15
    calls = env.model.calls
    code, out = chat(env, tf, text="one more", turn="t15")
    assert_402(code, out, "ai.credits", "free", kind="quota_exhausted")
    assert out["resets_at"] and out["upgrade_to"] == "pro"
    assert env.model.calls == calls                     # no model call paid for


def test_pro_has_200_credits(env):
    uid, tp = env.signup("p@x.io")
    make_plan(uid, "pro")
    code, out = chat(env, tp, turn="a")
    assert code == 200 and out["credits"]["limit"] == 200


def test_retry_with_same_turn_is_one_charge(env):
    uid, tf = env.signup("f@x.io")
    for _ in range(3):
        code, out = chat(env, tf, text="same question", turn="turn-1")
        assert code == 200
    assert ledger_used(env, f"u:{uid}") == 1


def test_failed_turn_is_refunded(env):
    uid, tf = env.signup("f@x.io")
    env.model.fail = True
    code, _ = chat(env, tf, turn="boom")
    assert ledger_used(env, f"u:{uid}") == 0


def test_turn_id_replay_is_not_a_free_model_call(env):
    """FINDING probe. The idempotency key comes from the client. A replayed
    key answers `replay` without a charge — and the handler then runs the
    model anyway. So one fixed turn_id buys unlimited turns with DIFFERENT
    questions. A retry must be one charge; it must not be free for new
    content."""
    uid, tf = env.signup("f@x.io")
    for i in range(20):
        code, _ = chat(env, tf, text=f"different question {i}", turn="fixed")
        if code == 402:
            break
    assert env.model.calls <= 15, (
        f"{env.model.calls} model calls for {ledger_used(env, f'u:{uid}')} "
        "credit(s) charged")


def test_user_cannot_refund_their_own_turn(env):
    """FINDING probe. /billing/consume accepts the user's own bearer and
    `refund: true`, so after every successful turn the user can give the
    credit back. Refunds must be server-side only (internal key)."""
    uid, tf = env.signup("f@x.io")
    for i in range(15):
        assert chat(env, tf, text=f"q{i}", turn=f"t{i}")[0] == 200
    for i in range(15):
        env.post("/billing/consume", {"feature": "ai.credits",
                                      "idem_key": f"t{i}", "refund": True},
                 token=tf)
    assert ledger_used(env, f"u:{uid}") == 15, "user refunded their own credits"


def test_anonymous_chat_is_metered_per_client(env):
    for i in range(3):
        code, _ = chat(env, text=f"q{i}", turn=f"a{i}")
        assert code == 200
    code, out = chat(env, text="q3", turn="a3")
    assert code == 402 and out["plan"] == "anonymous"
    assert out["upgrade_to"] == "free"


def test_consume_route_meters_only_quotas_and_needs_identity(env):
    code, _ = env.post("/billing/consume", {"feature": "ai.credits",
                                            "idem_key": "k"})
    assert code == 401
    _uid, tf = env.signup("f@x.io")
    code, out = env.post("/billing/consume", {"feature": "alerts.price",
                                              "idem_key": "k"}, token=tf)
    assert code == 400                                  # a limit cannot be bought here
    code, out = env.post("/billing/consume", {"feature": "ai.summaries",
                                              "idem_key": "s1"}, token=tf)
    assert code == 200 and out["charged"] == 1
    code, out = env.post("/billing/consume", {"feature": "ai.summaries",
                                              "idem_key": "s1"}, token=tf)
    assert code == 200 and out["charged"] == 0 and out["replay"] is True


# ══ GET /bars — intraday depth ═════════════════════════════════════════════

def test_free_intraday_depth_is_capped_and_paging_stops(env):
    _uid, tf = env.signup("f@x.io")
    code, out = env.get("/bars?symbol=TEST&interval=1m&limit=20000", token=tf)
    assert code == 200
    assert len(out["bars"]) == 10_000 and out["has_more"] is False
    assert out["history_limit"]["limit"] == 10_000
    assert out["history_limit"]["upgrade_to"] == "pro"
    oldest = out["bars"][0]["t"]
    # paging past the cap with `to` returns nothing older
    code, out = env.get(f"/bars?symbol=TEST&interval=1m&limit=5000&to={oldest}",
                        token=tf)
    assert code == 200 and out["bars"] == [] and out["has_more"] is False
    # a bogus bearer is not a plan: Free depth
    code, out = env.get("/bars?symbol=TEST&interval=1m&limit=20000",
                        token="not-a-real-token")
    assert len(out["bars"]) == 10_000


def test_pro_depth_is_unlimited_and_daily_is_free_for_all(env):
    uid, tp = env.signup("p@x.io")
    make_plan(uid, "pro")
    code, out = env.get("/bars?symbol=TEST&interval=1m&limit=20000", token=tp)
    assert len(out["bars"]) == 20_000 and out["has_more"] is True
    oldest = out["bars"][0]["t"]
    code, out = env.get(f"/bars?symbol=TEST&interval=1m&limit=5000&to={oldest}",
                        token=tp)
    assert len(out["bars"]) == 5000
    code, out = env.get("/bars?symbol=TEST&interval=1d&limit=20000")
    assert len(out["bars"]) == 20_000                  # daily: no plan cap


def test_indicator_route_respects_intraday_depth(env):
    """FINDING probe. /indicator reads bars through _rows -> get_bars with its
    own limit (<= 20000) and never consults the plan, so a Free user gets
    20,000 intraday values of e.g. sma(1) -- the close series -- past the
    10K history cap that /bars enforces."""
    _uid, tf = env.signup("f@x.io")
    name = "sma" if "sma" in ds.indicators.SPECS else sorted(ds.indicators.SPECS)[0]
    code, out = env.get(f"/indicator?symbol=TEST&name={name}&period=1"
                        f"&interval=1m&limit=20000", token=tf)
    assert code == 200, out
    n = max(len(v) for v in out["lines"].values())
    assert n <= 10_000, f"{n} intraday points served to a Free user"


# ══ POST /layouts — charts per tab, indicators per chart ═══════════════════

def layout(n_charts, n_inds=0, name="L"):
    return {"name": name, "spec": {
        "v": 1, "grid": "g22",
        "charts": [{"symbol": "TEST", "interval": "1d"}] * n_charts,
        "workspace": {"indicators": [f"ind{i}" for i in range(n_inds)]}}}


def test_layout_panes_and_indicators(env):
    uid_f, tf = env.signup("f@x.io")
    uid_p, tp = env.signup("p@x.io")
    make_plan(uid_p, "pro")
    assert env.post("/layouts", layout(4, 5), token=tf)[0] == 200
    code, out = env.post("/layouts", layout(5, 0, "L2"), token=tf)
    assert_402(code, out, "chart.panes", "free")
    code, out = env.post("/layouts", layout(4, 6, "L3"), token=tf)
    assert_402(code, out, "chart.indicators", "free")
    assert env.post("/layouts", layout(8, 10), token=tp)[0] == 200
    code, out = env.post("/layouts", layout(9, 0, "L4"), token=tp)
    assert_402(code, out, "chart.panes", "pro")
    assert out["upgrade_to"] is None                    # nothing above 8


def test_downgraded_layout_still_opens_and_deletes(env):
    uid, tok = env.signup("p@x.io")
    make_plan(uid, "pro")
    code, out = env.post("/layouts", layout(8), token=tok)
    lid = out["id"]
    ent.set_subscription(uid, plan="pro", status="expired")
    assert ent.plan_of(uid) == "free"
    code, out = env.get(f"/layouts?id={lid}", token=tok)
    assert code == 200 and len(out["spec"]["charts"]) == 8
    # re-saving it as it is over the new cap is refused — the data is intact
    code, _ = env.post("/layouts", {"id": lid, **layout(8)}, token=tok)
    assert code == 402
    assert env.db.execute("SELECT COUNT(*) FROM layouts WHERE id=?",
                          (lid,)).fetchone()[0] == 1
    assert env.post("/layouts", {"id": lid, "delete": True}, token=tok)[0] == 200


def test_open_chart_tool_is_gated_by_panes(env):
    uid, _ = env.signup("f@x.io")
    ds._req.user = (uid, "f@x.io", None)
    ds._req.chart_pairs = [("TEST", "1d")] * 4
    ds._req.charts = ["TEST"]
    try:
        out = ds.tool_open_chart(symbol="TEST", interval="1d")
        assert out.get("feature") == "chart.panes" and out.get("code") == "plan_limit"
        out = ds.tool_open_chart(symbol="TEST", interval="1d", replace=True)
        assert "error" not in out or out.get("code") != "plan_limit"
    finally:
        ds._req.user = None
        ds._req.chart_pairs = []


# ══ POST /charts/lease — parallel charts ═══════════════════════════════════

def test_parallel_charts_evict_the_oldest(env):
    _uid, tf = env.signup("f@x.io")
    for i in range(10):
        code, out = env.post("/charts/lease", {"tab_id": f"tab{i}"}, token=tf)
        assert code == 200, out
        time.sleep(0.002)
    code, out = env.post("/charts/lease", {"tab_id": "tab10"}, token=tf)
    assert code == 200 and out["active"] == 10          # new tab is never refused
    code, out = env.post("/charts/lease", {"tab_id": "tab0"}, token=tf)
    assert_402(code, out, "chart.parallel", "free", kind="evicted")
    code, out = env.post("/charts/lease", {"tab_id": "tab0", "reclaim": True},
                         token=tf)
    assert code == 200 and out["active"] == 10
    # releasing frees a slot
    env.post("/charts/lease", {"tab_id": "tab5", "release": True}, token=tf)
    assert env.post("/charts/lease", {"tab_id": "tab5b"},
                    token=tf)[1]["active"] == 10


def test_pro_holds_twenty_parallel_charts(env):
    uid, tp = env.signup("p@x.io")
    make_plan(uid, "pro")
    for i in range(20):
        env.post("/charts/lease", {"tab_id": f"t{i}"}, token=tp)
    code, out = env.post("/charts/lease", {"tab_id": "t0"}, token=tp)
    assert code == 200 and out["active"] == 20 and out["limit"] == 20


# ══ admin ══════════════════════════════════════════════════════════════════

def test_admin_grant_needs_an_admin(env, monkeypatch):
    _uid, tok = env.signup("someone@x.io")
    uid2, _ = env.signup("target@x.io")
    code, _ = env.post("/admin/billing/grant", {"email": "target@x.io",
                                                "plan": "pro_plus",
                                                "reason": "x"}, token=tok)
    assert code == 403
    assert ent.plan_of(uid2) == "free"
    monkeypatch.setattr(ds, "_ADMIN_EMAILS", {"someone@x.io"})
    code, out = env.post("/admin/billing/grant", {"email": "target@x.io",
                                                  "plan": "pro_plus",
                                                  "reason": "beta", "days": 30},
                         token=tok)
    assert code == 200 and ent.plan_of(uid2) == "pro_plus"


# ══ /billing/consume as pivot and pivotted call it (post-fix contract) ═════

SVC = {"X-Internal-Key": "svc-key"}


def test_refund_needs_the_service_key(env, monkeypatch):
    """Fix #2: an end user's bearer can spend but never refund; the service
    key can refund its own failed turn."""
    monkeypatch.setenv("CHARTO_INTERNAL_KEY", "svc-key")
    uid, tf = env.signup("f@x.io")
    assert chat(env, tf, text="q", turn="t1")[0] == 200
    key = env.db.execute("SELECT idem_key FROM usage_ledger WHERE subject=?",
                         (f"u:{uid}",)).fetchone()[0]
    code, out = env.post("/billing/consume", {"feature": "ai.credits",
                                              "idem_key": key, "refund": True},
                         token=tf)
    assert code == 403 and ledger_used(env, f"u:{uid}") == 1
    code, out = env.post("/billing/consume", {"feature": "ai.credits",
                                              "idem_key": key, "refund": True},
                         token=tf, headers={"X-Internal-Key": "wrong"})
    assert code == 403 and ledger_used(env, f"u:{uid}") == 1
    code, out = env.post("/billing/consume", {"feature": "ai.credits",
                                              "idem_key": key, "refund": True},
                         token=tf, headers=SVC)
    assert code == 200 and out["refunded"] is True
    assert ledger_used(env, f"u:{uid}") == 0
    # a second refund of the same turn gives nothing more back
    env.post("/billing/consume", {"feature": "ai.credits", "idem_key": key,
                                  "refund": True}, token=tf, headers=SVC)
    assert ledger_used(env, f"u:{uid}") == 0


def test_service_debit_by_email_meters_that_account(env, monkeypatch):
    """pivot's JWT path: service key + the account's email."""
    monkeypatch.setenv("CHARTO_INTERNAL_KEY", "svc-key")
    uid, _ = env.signup("both@x.io")
    for i in range(15):
        code, _ = env.post("/billing/consume", {"feature": "ai.credits",
                           "idem_key": f"p{i}", "email": "both@x.io"},
                           headers=SVC)
        assert code == 200
    code, out = env.post("/billing/consume", {"feature": "ai.credits",
                         "idem_key": "p15", "email": "both@x.io"}, headers=SVC)
    assert_402(code, out, "ai.credits", "free", kind="quota_exhausted")
    assert ledger_used(env, f"u:{uid}") == 15
    # without the key the email is not an identity
    code, _ = env.post("/billing/consume", {"feature": "ai.credits",
                       "idem_key": "x", "email": "both@x.io"})
    assert code == 401


def test_unknown_identities_do_not_share_one_anonymous_bucket(env, monkeypatch):
    """FINDING probe (pivot/pivotted callers). A service debit whose email is
    not a charto account — every pivot-only user, since the two user tables
    are disjoint — or whose forwarded charto bearer has expired arrives with
    NO `client`. charto then meters it as anonymous subject a:hash(""), ONE
    bucket shared by all such users: three turns a day for everyone
    together, and the fourth person is refused for other people's use."""
    monkeypatch.setenv("CHARTO_INTERNAL_KEY", "svc-key")
    for i, who in enumerate(("a@pivot.only", "b@pivot.only", "c@pivot.only")):
        env.post("/billing/consume", {"feature": "ai.credits",
                 "idem_key": f"k{i}", "email": who}, headers=SVC)
    code, out = env.post("/billing/consume", {"feature": "ai.credits",
                         "idem_key": "k3", "email": "d@pivot.only"}, headers=SVC)
    assert code != 402, ("a fourth, unrelated pivot user was refused for "
                         f"three others' turns: {out}")
