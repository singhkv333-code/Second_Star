"""Pivot — the owner API behind the owner console (`/ops/*`).

WHO MAY CALL IT
---------------
Nobody with a browser. The console (owner-console/, on Azure App Service)
calls it server to server, and every request is signed:

    X-Ops-Timestamp   unix seconds, within ±60 s of this server's clock
    X-Ops-Nonce       16-64 url-safe characters, never seen before
    X-Ops-Signature   hex HMAC-SHA256(PIVOT_OPS_KEY, method \\n path?query \\n
                      timestamp \\n nonce \\n sha256-hex(body))
    X-Ops-Actor       who is acting, for the audit log
    X-Ops-Client-IP   the owner's address as the console saw it, for the log

and, when PIVOT_OPS_ALLOWED_IPS is set, it must come from one of those
addresses (nginx's X-Real-IP; the dataserver only listens on 127.0.0.1, so
that header is nginx's and cannot be forged from outside).

Without PIVOT_OPS_KEY (or with one shorter than 32 characters) the routes do
not exist. Every refusal is the same 404, so a prober learns nothing about
which check failed. Every write lands in `ops_audit`.

WHAT IT CAN DO
--------------
Read the business (users, plans in force, money collected, webhooks), manage
codes (coupons.py), grant or revoke a plan or a feature value for one account,
and sign an account out everywhere. It cannot move money, cancel a provider
subscription, read passwords or sessions, or read anyone's charts or chats.
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import re
import threading
import time
from os import environ

import coupons
import entitlements as ent

SKEW = 60
_nonces: dict[str, float] = {}
_nonce_lock = threading.Lock()
# Set by dataserver: the /health report, and what to do after a plan drops.
health = None
on_downgrade = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS ops_audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts INTEGER NOT NULL,
  actor TEXT NOT NULL,
  ip TEXT NOT NULL DEFAULT '',
  action TEXT NOT NULL,
  target TEXT NOT NULL DEFAULT '',
  detail TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS ops_audit_ts ON ops_audit(ts);
"""


def _create(con) -> None:
    con.executescript(SCHEMA)


ent.register_schema(_create)


def ensure_schema() -> None:
    with ent._lock:
        _create(ent._con)
        ent._con.commit()


def _key() -> bytes:
    k = (environ.get("PIVOT_OPS_KEY") or "").strip()
    return k.encode() if len(k) >= 32 else b""


def enabled() -> bool:
    return bool(_key())


def _ip_allowed(ip: str) -> bool:
    raw = (environ.get("PIVOT_OPS_ALLOWED_IPS") or "").strip()
    if not raw:
        return True
    try:
        addr = ipaddress.ip_address(ip.strip())
    except ValueError:
        return False
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            if addr in ipaddress.ip_network(item, strict=False):
                return True
        except ValueError:
            continue
    return False


def sign(key: bytes, method: str, path: str, ts: str, nonce: str, body: bytes) -> str:
    msg = "\n".join([method.upper(), path, ts, nonce, hashlib.sha256(body).hexdigest()])
    return hmac.new(key, msg.encode(), hashlib.sha256).hexdigest()


def authenticate(method: str, path: str, headers, body: bytes, peer_ip: str) -> str | None:
    """The actor's name if this request is the console's, else None."""
    key = _key()
    if not key or not _ip_allowed(peer_ip):
        return None
    ts = str(headers.get("X-Ops-Timestamp") or "")
    nonce = str(headers.get("X-Ops-Nonce") or "")
    sig = str(headers.get("X-Ops-Signature") or "")
    if not ts.isdigit() or abs(time.time() - int(ts)) > SKEW:
        return None
    if not re.fullmatch(r"[A-Za-z0-9_-]{16,64}", nonce) or not re.fullmatch(r"[0-9a-f]{64}", sig):
        return None
    if not hmac.compare_digest(sign(key, method, path, ts, nonce, body), sig):
        return None
    now = time.time()
    with _nonce_lock:
        for n, t in list(_nonces.items()):
            if now - t > 2 * SKEW:
                del _nonces[n]
        if nonce in _nonces:
            return None                                 # a replay
        _nonces[nonce] = now
    actor = re.sub(r"[^\w .@:-]", "", str(headers.get("X-Ops-Actor") or "console"))[:80]
    return actor or "console"


def audit(actor: str, ip: str, action: str, target: str = "", detail=None) -> None:
    with ent._lock:
        ent._con.execute(
            "INSERT INTO ops_audit (ts,actor,ip,action,target,detail) VALUES (?,?,?,?,?,?)",
            (int(time.time()), actor, ip[:64], action, str(target)[:200],
             json.dumps(detail, default=str)[:4000] if detail is not None else ""))
        ent._con.commit()


# ── reads ──────────────────────────────────────────────────────────────────

def _q(sql: str, args: tuple = ()) -> list:
    with ent._lock:
        return ent._con.execute(sql, args).fetchall()


def _cols(table: str) -> set[str]:
    return {r[1] for r in _q(f"PRAGMA table_info({table})")}


def _price(plan: str, cycle: str, provider_plan_amount: int | None = None) -> int:
    if provider_plan_amount:
        return provider_plan_amount
    return coupons.catalog_price(plan, cycle) or 0


def overview() -> dict:
    now = int(time.time())
    day = 86400
    users = _q("SELECT COUNT(*), SUM(created>?), SUM(created>?), SUM(created>?) FROM users",
               (now - day, now - 7 * day, now - 30 * day))[0]
    verified = (_q("SELECT COUNT(*) FROM users WHERE email_verified=1")[0][0]
                if "email_verified" in _cols("users") else None)
    active7 = _q("SELECT COUNT(DISTINCT user_id) FROM sessions WHERE last_seen>?", (now - 7 * day,))[0][0]
    # Daily signups, IST days, for the last 30.
    series = {}
    for (created,) in _q("SELECT created FROM users WHERE created>?", (now - 30 * day,)):
        d = time.strftime("%Y-%m-%d", time.gmtime(created + 19800))
        series[d] = series.get(d, 0) + 1
    days = [time.strftime("%Y-%m-%d", time.gmtime(now + 19800 - i * day)) for i in range(29, -1, -1)]
    # Who is on what, as entitlements resolves it right now.
    plans: dict[str, int] = {}
    ids = [r[0] for r in _q("SELECT id FROM users")]
    for uid in ids:
        p = ent.plan_of(uid)
        plans[p] = plans.get(p, 0) + 1
    subs = _q("SELECT s.user_id, s.plan, s.cycle, s.status, s.provider, s.period_end, "
              "s.cancel_at_period_end, s.provider_sub_id FROM subscriptions s")
    paid = {"active": 0, "past_due": 0, "cancelling": 0}
    mrr = 0
    pp = {r[0]: r[1] for r in _q("SELECT provider_plan_id, amount FROM coupon_provider_plans")}
    sub_plan = {}
    for (sid, pid) in _q("SELECT provider_sub_id, provider_plan_id FROM coupon_redemptions "
                         "WHERE provider_sub_id IS NOT NULL AND state='redeemed'"):
        sub_plan[sid] = pp.get(pid)
    for uid, plan, cycle, status, prov, pe, cape, psid in subs:
        if prov != "razorpay" or status not in ("active", "past_due"):
            continue
        if pe and pe < now and status == "active":
            continue
        paid["cancelling" if cape else status] += 1
        amt = _price(plan, cycle, sub_plan.get(psid))
        if not cape:
            mrr += amt if cycle == "monthly" else amt // 12
    grants = _q("SELECT COUNT(*), COUNT(DISTINCT user_id) FROM entitlement_grants WHERE plan IS NOT NULL "
                "AND (expires IS NULL OR expires>?)", (now,))[0]
    collected = refunded = 0
    for etype, payload in _q("SELECT type, payload FROM billing_events WHERE received>? AND type IN "
                             "('payment.captured','refund.processed')", (now - 30 * day,)):
        try:
            pl = json.loads(payload).get("payload") or {}
            ent_ = (pl.get("refund") if etype == "refund.processed" else pl.get("payment")) or {}
            amt = int((ent_.get("entity") or {}).get("amount") or 0)
        except (ValueError, TypeError, AttributeError):
            continue
        if etype == "payment.captured":
            collected += amt
        else:
            refunded += amt
    codes = _q("SELECT COUNT(*), SUM(status='active') FROM coupons")[0]
    red = _q("SELECT SUM(created>?), SUM(created>?) FROM coupon_redemptions WHERE state='redeemed'",
             (now - 7 * day, now - 30 * day))[0]
    events_bad = _q("SELECT COUNT(*) FROM billing_events WHERE received>? AND (result LIKE 'retry%' "
                    "OR result LIKE 'no user%' OR result LIKE '%exception%')", (now - 7 * day,))[0][0]
    import billing                                      # noqa: PLC0415 — after bind
    return {
        "now": now,
        "users": {"total": users[0], "day": users[1] or 0, "week": users[2] or 0,
                  "month": users[3] or 0, "verified": verified, "active_week": active7},
        "signups": [{"day": d, "n": series.get(d, 0)} for d in days],
        "plans": plans,
        "paid": paid, "mrr": mrr,
        "grants": {"active": grants[0], "accounts": grants[1]},
        "money_30d": {"collected": collected, "refunded": refunded},
        "codes": {"total": codes[0], "active": codes[1] or 0,
                  "redeemed_week": red[0] or 0, "redeemed_month": red[1] or 0},
        "billing": {"configured": billing.configured(), "test_mode": billing.test_mode(),
                    "paywall_enabled": ent.paywall_enabled(), "events_needing_review_7d": events_bad},
    }


def _user_row(uid: int) -> dict | None:
    cols = _cols("users")
    extra = ", email_verified" if "email_verified" in cols else ""
    r = _q(f"SELECT id, email, name, created{extra} FROM users WHERE id=?", (uid,))
    if not r:
        return None
    r = r[0]
    out = {"id": r[0], "email": r[1], "name": r[2], "created": r[3],
           "verified": bool(r[4]) if extra else None}
    s = _q("SELECT COUNT(*), MAX(last_seen) FROM sessions WHERE user_id=?", (uid,))[0]
    out.update(sessions=s[0], last_seen=s[1], plan=ent.plan_of(uid))
    return out


def users(q: str = "", limit: int = 100) -> list[dict]:
    q = q.strip()
    if q.isdigit():
        rows = _q("SELECT id FROM users WHERE id=?", (int(q),))
    elif q:
        rows = _q("SELECT id FROM users WHERE email LIKE ? OR name LIKE ? ORDER BY id DESC LIMIT ?",
                  (f"%{q}%", f"%{q}%", int(limit)))
    else:
        rows = _q("SELECT id FROM users ORDER BY id DESC LIMIT ?", (int(limit),))
    out = []
    for (uid,) in rows:
        u = _user_row(uid)
        sub = _q("SELECT plan, status, provider FROM subscriptions WHERE user_id=?", (uid,))
        u["subscription"] = dict(zip(("plan", "status", "provider"), sub[0])) if sub else None
        out.append(u)
    return out


def user(uid: int) -> dict | None:
    u = _user_row(uid)
    if not u:
        return None
    u["billing"] = ent.summary(uid)
    keys = ("id", "plan", "feature", "value", "expires", "reason", "granted_by", "created")
    u["grants"] = [dict(zip(keys, r)) for r in _q(
        f"SELECT {','.join(keys)} FROM entitlement_grants WHERE user_id=? ORDER BY id DESC", (uid,))]
    u["redemptions"] = coupons.redemptions(uid=uid, limit=100)
    return u


def grants(active_only: bool = True) -> list[dict]:
    now = int(time.time())
    where = "WHERE (g.expires IS NULL OR g.expires>?)" if active_only else "WHERE g.id>?"
    rows = _q("SELECT g.id, g.user_id, u.email, g.plan, g.feature, g.value, g.expires, g.reason, "
              "g.granted_by, g.created FROM entitlement_grants g LEFT JOIN users u ON u.id=g.user_id "
              f"{where} ORDER BY g.id DESC LIMIT 1000", (now if active_only else 0,))
    keys = ("id", "user_id", "email", "plan", "feature", "value", "expires", "reason",
            "granted_by", "created")
    return [dict(zip(keys, r)) for r in rows]


def subscriptions() -> list[dict]:
    rows = _q("SELECT s.user_id, u.email, s.plan, s.cycle, s.status, s.provider, s.period_start, "
              "s.period_end, s.cancel_at_period_end, s.grace_until, s.updated FROM subscriptions s "
              "LEFT JOIN users u ON u.id=s.user_id ORDER BY s.updated DESC LIMIT 1000")
    keys = ("user_id", "email", "plan", "cycle", "status", "provider", "period_start",
            "period_end", "cancel_at_period_end", "grace_until", "updated")
    return [dict(zip(keys, r)) for r in rows]


def events(limit: int = 200) -> list[dict]:
    rows = _q("SELECT provider, event_id, type, received, result FROM billing_events "
              "ORDER BY received DESC LIMIT ?", (min(int(limit), 1000),))
    keys = ("provider", "event_id", "type", "received", "result")
    return [dict(zip(keys, r)) for r in rows]


def audit_log(limit: int = 300) -> list[dict]:
    rows = _q("SELECT id, ts, actor, ip, action, target, detail FROM ops_audit "
              "ORDER BY id DESC LIMIT ?", (min(int(limit), 2000),))
    keys = ("id", "ts", "actor", "ip", "action", "target", "detail")
    return [dict(zip(keys, r)) for r in rows]


# ── writes ─────────────────────────────────────────────────────────────────

def _grant(uid: int, body: dict, actor: str) -> dict:
    if not _user_row(uid):
        raise coupons.CodeError("not_found", "No such account.", 404)
    reason = str(body.get("reason") or "").strip()
    if not reason:
        raise coupons.CodeError("invalid", "A grant needs a reason.")
    plan = body.get("plan") or None
    feature = body.get("feature") or None
    days = body.get("days")
    days = coupons._int(days, "Days", 1, coupons.MAX_ACCESS_DAYS) if days not in (None, "") else None
    value = body.get("value")
    if feature and isinstance(value, str):
        value = None if value.strip().lower() in ("", "none", "unlimited") else (
            int(value) if value.strip().lstrip("-").isdigit() else value.strip().lower() == "true")
    try:
        gid = ent.grant(uid, reason=reason, granted_by=f"console:{actor}"[:120], plan=plan,
                        feature=feature, value_=value, days=days)
    except ValueError as exc:
        raise coupons.CodeError("invalid", str(exc)) from None
    return {"grant_id": gid, "plan": ent.plan_of(uid)}


def _revoke_grant(gid: int) -> dict:
    now = int(time.time())
    r = _q("SELECT user_id, expires FROM entitlement_grants WHERE id=?", (gid,))
    if not r:
        raise coupons.CodeError("not_found", "No such grant.", 404)
    if r[0][1] is not None and r[0][1] <= now:
        raise coupons.CodeError("invalid", "This grant has already ended.")
    with ent._lock:
        ent._con.execute("UPDATE entitlement_grants SET expires=? WHERE id=?", (now, gid))
        ent._con.execute("UPDATE coupon_redemptions SET state='revoked', updated=? WHERE grant_id=? "
                         "AND state='redeemed'", (now, gid))
        ent._con.commit()
    uid = r[0][0]
    if on_downgrade and ent.CATALOG["plans"][ent.plan_of(uid)]["rank"] <= 0:
        try:
            on_downgrade(uid)
        except Exception:                               # noqa: BLE001 — never fails the revoke
            pass
    return {"grant_id": gid, "user_id": uid, "plan": ent.plan_of(uid)}


def _signout(uid: int) -> dict:
    with ent._lock:
        n = ent._con.execute("DELETE FROM sessions WHERE user_id=?", (uid,)).rowcount
        ent._con.commit()
    return {"user_id": uid, "sessions_ended": n}


def _code_out(c: dict) -> dict:
    return {**c, "summary": coupons.describe(c)["summary"], "uses": coupons.usage(c["id"])}


# ── the router ─────────────────────────────────────────────────────────────

def handle(method: str, path: str, query: dict, body: dict, actor: str, ip: str) -> tuple[int, dict]:
    """Route one authenticated request. Writes are audited after they succeed."""
    parts = [p for p in path.split("/") if p][1:]      # drop "ops"
    try:
        if method == "GET":
            return _get(parts, query)
        out = _post(parts, body, actor)
        if out[0] != 200:
            return out
        audit(actor, ip, "/".join(parts[:3]), "/".join(parts[1:2]),
              {k: v for k, v in body.items() if k not in ("emails",)} | (
                  {"emails": f"{len(str(body.get('emails', '')).split(','))} addresses"}
                  if body.get("emails") else {}))
        return out
    except coupons.CodeError as exc:
        return exc.status, exc.body()


def _get(parts: list[str], q: dict) -> tuple[int, dict]:
    if parts == ["overview"]:
        return 200, overview()
    if parts == ["health"]:
        if not health:
            return 200, {"ok": None}
        code, rep = health()
        return 200, {"status": code, **rep}
    if parts == ["catalog"]:
        return 200, ent.public_catalog()
    if parts == ["coupons"]:
        return 200, {"coupons": coupons.listing(q.get("status") or None, q.get("q", ""),
                                                q.get("batch", ""))}
    if len(parts) == 2 and parts[0] == "coupons" and parts[1].isdigit():
        c = coupons.get(int(parts[1]))
        if not c:
            return 404, {"error": "No such code."}
        return 200, {"coupon": {**_code_out(c), **coupons.describe(c)},
                     "redemptions": coupons.redemptions(int(parts[1]))}
    if parts == ["redemptions"]:
        return 200, {"redemptions": coupons.redemptions(limit=500)}
    if parts == ["users"]:
        return 200, {"users": users(q.get("q", ""))}
    if len(parts) == 2 and parts[0] == "users" and parts[1].isdigit():
        u = user(int(parts[1]))
        return (200, {"user": u}) if u else (404, {"error": "No such account."})
    if parts == ["grants"]:
        return 200, {"grants": grants(q.get("all") != "1")}
    if parts == ["subscriptions"]:
        return 200, {"subscriptions": subscriptions()}
    if parts == ["events"]:
        return 200, {"events": events()}
    if parts == ["audit"]:
        return 200, {"audit": audit_log()}
    if parts == ["trading"]:
        # plays, their trades and what live ones paid (revenue.py); a server
        # without the execution layer says so rather than showing zeros
        try:
            import revenue                              # noqa: PLC0415 — bound with plays
            days = int(q.get("days") or 30)
            return 200, revenue.metrics(days) if revenue.ds is not None else \
                {"available": False, "reason": "plays are not on this server"}
        except ValueError:
            return 400, {"error": "days must be a whole number"}
    return 404, {"error": "not found"}


def _post(parts: list[str], body: dict, actor: str) -> tuple[int, dict]:
    if parts == ["coupons"]:
        made = coupons.create(body, f"console:{actor}")
        return 200, {"coupons": [_code_out(c) for c in made],
                     "batch": made[0]["batch"] if made else ""}
    if len(parts) == 2 and parts[0] == "coupons" and parts[1].isdigit():
        return 200, {"coupon": _code_out(coupons.update(int(parts[1]), body))}
    if parts == ["coupons", "batch"]:
        return 200, {"updated": coupons.bulk_status(str(body.get("batch") or ""),
                                                    str(body.get("status") or ""))}
    if len(parts) == 3 and parts[0] == "redemptions" and parts[1].isdigit() and parts[2] == "revoke":
        return 200, coupons.revoke(int(parts[1]))
    if len(parts) == 3 and parts[0] == "users" and parts[1].isdigit():
        uid = int(parts[1])
        if parts[2] == "grant":
            return 200, _grant(uid, body, actor)
        if parts[2] == "signout":
            return 200, _signout(uid)
    if len(parts) == 3 and parts[0] == "grants" and parts[1].isdigit() and parts[2] == "revoke":
        return 200, _revoke_grant(int(parts[1]))
    return 404, {"error": "not found"}
