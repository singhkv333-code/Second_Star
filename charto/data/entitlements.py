"""Charto — what a user's plan lets them do, and how much of it they have used.

WHY A MODULE AND NOT `if plan == "pro"`
--------------------------------------
Every paywall that starts as plan checks scattered through handlers ends as a
migration: a new plan, a changed number or one customer on a custom deal means
finding every branch. So the plans are DATA (`plans_catalog.json`) and code
asks one question everywhere: "what is feature X worth for this user?" A new
plan or a new number is a catalog edit, with no handler change.

FOUR KINDS OF FEATURE, because they are enforced four different ways
---------------------------------------------------------------------
    flag    on or off                          custom timeframes, ad-free
    limit   a cap on LIVE objects, counted at  price alerts, charts per tab,
            the moment one more is written     saved screens
    quota   usage metered over a window, kept  AI credits, AI summaries
            in a ledger
    value   a parameter, not a count           alert expiry, history depth

A limit counts what exists NOW, so deleting an alert frees a slot. A quota
counts what was DONE in the window, so deleting a chat does not refund it.
Mixing the two up is the usual source of "I deleted one and it still says
I'm at the limit".

THE RULES THIS FILE KEEPS
-------------------------
1. The server decides. The client mirrors `/billing/me` for display only; a
   client that lies about its plan gets the same refusal.
2. One refusal shape: `PlanLimit.body()`, sent as HTTP 402, carries enough
   (feature, limit, used, upgrade_to, resets_at) for any client to explain it
   without a second request.
3. Metering is idempotent. `consume()` takes a key, and the ledger's UNIQUE
   index makes a retried request free instead of double-charged.
4. A failed AI turn is refunded (`refund()`), so a user is never charged for an
   error they got instead of an answer.
5. The effective plan is computed on READ from the subscription row. No cron
   has to run for a lapsed subscription to lapse, so no cron can fail to.
6. A downgrade never deletes anything. Over-limit objects stay; the next write
   is what is refused (see `excess()` for alerts, which pause instead).

The connection and lock are BOUND by dataserver (`bind()`), not imported:
dataserver runs as `__main__`, and a module that did `import dataserver` would
get a second copy with a second connection to the same file.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import threading
import time
from pathlib import Path

CATALOG_PATH = Path(__file__).with_name("plans_catalog.json")
IST = _dt.timezone(_dt.timedelta(hours=5, minutes=30))
GRACE_DAYS = 3                  # past_due keeps the paid plan this long

# ── paywall master switch ──────────────────────────────────────────────────
# OFF for now by request: no code is removed, every plan check is simply
# waved through. The four enforcement entry points (require_flag, check_limit,
# check_count, consume) short-circuit when this is False, so nothing is ever
# refused and nothing is metered. Everything else — plan_of, value, summary,
# the pricing catalog, subscription writes, grants — is untouched, so the UI
# still reads and displays plans correctly. Turn it back on by setting the env
# var PAYWALL_ENABLED to a truthy value (1/true/yes/on), or by flipping the
# default below back to True.
_TRUE = {"1", "true", "yes", "on"}


def paywall_enabled() -> bool:
    env = os.environ.get("PAYWALL_ENABLED")
    if env is None:
        return False            # default OFF until re-enabled
    return env.strip().lower() in _TRUE

# Statuses that keep the paid plan. `cancelled` keeps it too, until the period
# it already paid for ends — that check is in `_effective`, not here.
_PAID_STATUSES = {"active", "trialing"}

_con = None
_lock: threading.Lock | None = None


def _load_catalog() -> dict:
    cat = json.loads(CATALOG_PATH.read_text())
    ranks = sorted(cat["plans"], key=lambda p: cat["plans"][p]["rank"])
    for key, f in cat["features"].items():
        missing = [p for p in cat["plans"] if p not in f["values"]]
        if missing:
            raise ValueError(f"catalog: {key} has no value for {missing}")
        if f["kind"] not in ("flag", "limit", "quota", "value"):
            raise ValueError(f"catalog: {key} has unknown kind {f['kind']}")
    cat["_ranks"] = ranks
    return cat


CATALOG = _load_catalog()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS subscriptions (
  user_id   INTEGER PRIMARY KEY REFERENCES users(id),
  plan      TEXT NOT NULL,
  cycle     TEXT NOT NULL DEFAULT 'monthly',     -- monthly | annual
  status    TEXT NOT NULL,                       -- created active trialing past_due cancelled expired
  provider  TEXT NOT NULL DEFAULT '',            -- razorpay | admin
  provider_sub_id TEXT,
  period_start INTEGER,
  period_end   INTEGER,
  cancel_at_period_end INTEGER NOT NULL DEFAULT 0,
  grace_until  INTEGER,
  created   INTEGER NOT NULL,
  updated   INTEGER NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS subscriptions_provider
  ON subscriptions(provider, provider_sub_id) WHERE provider_sub_id IS NOT NULL;

-- One row per metered use. `subject` is u:<user id> or a:<hashed client> for a
-- signed-out visitor; `win` is the window the row counts against, fixed at
-- write time so a later plan change cannot re-bucket history.
CREATE TABLE IF NOT EXISTS usage_ledger (
  id       INTEGER PRIMARY KEY AUTOINCREMENT,
  subject  TEXT NOT NULL,
  feature  TEXT NOT NULL,
  win      TEXT NOT NULL,
  amount   INTEGER NOT NULL,
  idem_key TEXT NOT NULL,
  ts       INTEGER NOT NULL,
  meta     TEXT NOT NULL DEFAULT '');
CREATE UNIQUE INDEX IF NOT EXISTS usage_idem ON usage_ledger(subject, feature, idem_key);
CREATE INDEX IF NOT EXISTS usage_window ON usage_ledger(subject, feature, win);

-- Every webhook we have ever accepted, so a redelivery is recognised and
-- acknowledged without being applied twice.
CREATE TABLE IF NOT EXISTS billing_events (
  provider  TEXT NOT NULL,
  event_id  TEXT NOT NULL,
  type      TEXT NOT NULL,
  payload   TEXT NOT NULL,
  received  INTEGER NOT NULL,
  result    TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (provider, event_id));

-- Comps and custom deals: a whole plan, or one feature's value, with an end
-- date and a reason. Never edited in place; a new grant supersedes.
CREATE TABLE IF NOT EXISTS entitlement_grants (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id   INTEGER NOT NULL REFERENCES users(id),
  plan      TEXT,
  feature   TEXT,
  value     TEXT,
  expires   INTEGER,
  reason    TEXT NOT NULL,
  granted_by TEXT NOT NULL,
  created   INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS grants_user ON entitlement_grants(user_id);
"""


def bind(con, lock) -> None:
    """Point this module at the account database and create its tables."""
    global _con, _lock
    _con, _lock = con, lock
    with _lock:
        _con.executescript(_SCHEMA)
        try:
            # Added after the table shipped: a plan change the provider has
            # scheduled for the cycle end, as {"plan", "cycle", "at"}.
            _con.execute("ALTER TABLE subscriptions ADD COLUMN pending_change TEXT")
        except Exception:                               # noqa: BLE001
            pass                                        # already there
        _con.commit()


# ══ the refusal ════════════════════════════════════════════════════════════

class PlanLimit(Exception):
    """A request the user's plan does not cover. Always answered as 402."""

    status = 402

    def __init__(self, feature: str, plan: str, *, code: str, limit=None,
                 used=None, resets_at: int | None = None, message: str = ""):
        self.feature, self.plan, self.code = feature, plan, code
        self.limit, self.used, self.resets_at = limit, used, resets_at
        self.message = message or _sentence(feature, plan, code, limit)
        super().__init__(self.message)

    def body(self) -> dict:
        out = {"error": self.message, "code": self.code,
               "feature": self.feature, "plan": self.plan,
               "limit": self.limit, "used": self.used,
               "upgrade_to": upgrade_for(self.feature, self.plan, self.used)}
        if self.resets_at:
            out["resets_at"] = self.resets_at
        return out


def _lower(label: str) -> str:
    """'Price alerts' → 'price alerts', but 'AI credits' stays as written."""
    return label if label[1:2].isupper() else label[:1].lower() + label[1:]


def _sentence(feature: str, plan: str, code: str, limit) -> str:
    label = CATALOG["features"].get(feature, {}).get("label", feature)
    name = CATALOG["plans"].get(plan, {}).get("name", plan)
    if plan == "anonymous":
        return (f"Sign in to get more {_lower(label)}." if code == "quota_exhausted"
                else f"Sign in to use {_lower(label)}.")
    if code == "feature_locked":
        return f"{label} are not included in the {name} plan."
    if code == "quota_exhausted":
        return (f"You have used all {limit} {_lower(label)} in your {name} "
                f"plan for this period.")
    return f"Your {name} plan allows {limit} {_lower(label)}."


def upgrade_for(feature: str, plan: str, needed=None) -> str | None:
    """The cheapest plan above `plan` that would have allowed this request."""
    f = CATALOG["features"].get(feature)
    if not f:
        return None
    ranks = CATALOG["_ranks"]
    for p in ranks[ranks.index(plan) + 1:] if plan in ranks else ranks:
        if not CATALOG["plans"][p].get("public") and p != "free":
            continue
        v = _raw(f, p)
        if f["kind"] == "flag" and v:
            return p
        if f["kind"] in ("limit", "quota"):
            lim = v["limit"] if isinstance(v, dict) else v
            if lim is None or needed is None or lim > needed:
                return p
        if f["kind"] == "value" and v != _raw(f, plan):
            return p
    return None


# ══ which plan is this user on ════════════════════════════════════════════

def _now() -> int:
    return int(time.time())


def _sub_row(uid: int):
    return _con.execute(
        "SELECT plan, cycle, status, period_start, period_end, "
        "cancel_at_period_end, grace_until, provider FROM subscriptions "
        "WHERE user_id=?", (uid,)).fetchone()


def _effective(row, now: int) -> str:
    if not row:
        return "free"
    plan, _cycle, status, _ps, pe, _cape, grace, _prov = row
    if plan not in CATALOG["plans"]:
        return "free"
    if status in _PAID_STATUSES and (not pe or pe > now):
        return plan
    if status in _PAID_STATUSES and pe and pe <= now and grace and grace > now:
        return plan                       # renewal is late, not failed yet
    if status == "past_due" and grace and grace > now:
        return plan
    if status == "cancelled" and pe and pe > now:
        return plan                       # paid through the period
    return "free"


def _grant_plan(uid: int, now: int) -> str | None:
    row = _con.execute(
        "SELECT plan FROM entitlement_grants WHERE user_id=? AND plan IS NOT NULL "
        "AND (expires IS NULL OR expires>?) ORDER BY id DESC LIMIT 1",
        (uid, now)).fetchone()
    return row[0] if row and row[0] in CATALOG["plans"] else None


def plan_of(uid: int | None) -> str:
    """The plan in force right now. None (signed out) is `anonymous`."""
    if not uid:
        return "anonymous"
    now = _now()
    with _lock:
        paid = _effective(_sub_row(uid), now)
        comp = _grant_plan(uid, now)
    if comp and CATALOG["plans"][comp]["rank"] > CATALOG["plans"][paid]["rank"]:
        return comp
    return paid


def _raw(f: dict, plan: str):
    return f["values"].get(plan)


def value(uid: int | None, feature: str, plan: str | None = None):
    """What `feature` is worth for this user: bool, int, None (unlimited), or
    for a quota the {limit, window} it is metered on."""
    f = CATALOG["features"][feature]
    plan = plan or plan_of(uid)
    v = _raw(f, plan)
    if uid:
        with _lock:
            row = _con.execute(
                "SELECT value FROM entitlement_grants WHERE user_id=? AND "
                "feature=? AND (expires IS NULL OR expires>?) ORDER BY id DESC "
                "LIMIT 1", (uid, feature, _now())).fetchone()
        if row:
            v = json.loads(row[0])
    if f["kind"] == "quota":
        if isinstance(v, dict):
            return {"limit": v.get("limit"), "window": v.get("window", f["window"])}
        return {"limit": v, "window": f["window"]}
    return v


# ══ flags and limits ══════════════════════════════════════════════════════

def require_flag(uid: int | None, feature: str) -> None:
    if not paywall_enabled():
        return
    plan = plan_of(uid)
    if not value(uid, feature, plan):
        raise PlanLimit(feature, plan, code="feature_locked")


def check_limit(uid: int | None, feature: str, current: int, adding: int = 1) -> None:
    """Refuse if `current` live objects plus `adding` would pass the cap."""
    if not paywall_enabled():
        return
    plan = plan_of(uid)
    lim = value(uid, feature, plan)
    if lim is None:
        return
    if current + adding > lim:
        raise PlanLimit(feature, plan, code="plan_limit", limit=lim, used=current)


def check_count(uid: int | None, feature: str, n: int) -> None:
    """Refuse a single object that carries `n` of something (a layout with n
    charts) when n is over the cap."""
    if not paywall_enabled():
        return
    plan = plan_of(uid)
    lim = value(uid, feature, plan)
    if lim is not None and n > lim:
        raise PlanLimit(feature, plan, code="plan_limit", limit=lim, used=n)


# ══ quotas: the ledger ════════════════════════════════════════════════════

def subject_for(uid: int | None, client: str = "") -> str:
    """Who a meter row belongs to. A signed-out visitor is a salted hash of
    their client address, never the address itself."""
    if uid:
        return f"u:{uid}"
    h = hashlib.sha256(f"charto-anon:{client}".encode()).hexdigest()[:20]
    return f"a:{h}"


def _window(uid: int | None, window: str, now: int) -> tuple[str, int]:
    """(window key, the unix time it resets). A paid monthly quota follows the
    billing period, so a user who paid on the 17th gets a fresh pool on the
    17th; everyone else resets on the IST calendar."""
    d = _dt.datetime.fromtimestamp(now, IST)
    if window == "day":
        nxt = (d + _dt.timedelta(days=1)).replace(hour=0, minute=0, second=0,
                                                  microsecond=0)
        return d.strftime("d%Y-%m-%d"), int(nxt.timestamp())
    if uid:
        with _lock:
            row = _sub_row(uid)
        if row and row[3] and row[4] and row[3] <= now < row[4] \
                and _effective(row, now) != "free":
            return f"p{row[3]}", int(row[4])
    first = d.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    nxt = (first + _dt.timedelta(days=32)).replace(day=1)
    return d.strftime("m%Y-%m"), int(nxt.timestamp())


def _used(subject: str, feature: str, win: str) -> int:
    return int(_con.execute(
        "SELECT COALESCE(SUM(amount),0) FROM usage_ledger WHERE subject=? AND "
        "feature=? AND win=?", (subject, feature, win)).fetchone()[0])


def consume(uid: int | None, feature: str, idem_key: str, amount: int = 1, *,
            client: str = "", meta: str = "", plan: str | None = None) -> dict:
    """Debit `amount` of a quota, or raise PlanLimit. Idempotent on `idem_key`:
    the same key twice charges once and answers the same way both times.
    `plan` is for a subject with no account here but a known standing (a
    pivot-only account meters at Free)."""
    plan = plan or plan_of(uid)
    q = value(uid, feature, plan)
    now = _now()
    win, resets = _window(uid, q["window"], now)
    if not paywall_enabled():
        # Paywall off: never charge, never refuse. Report an uncapped pool so
        # any client reading the result sees headroom.
        return {"charged": 0, "replay": False, "used": 0,
                "limit": None, "resets_at": resets}
    subject = subject_for(uid, client)
    idem_key = str(idem_key)[:120] or f"t{now}"
    with _lock:
        dup = _con.execute(
            "SELECT win FROM usage_ledger WHERE subject=? AND feature=? AND "
            "idem_key=?", (subject, feature, idem_key)).fetchone()
        used = _used(subject, feature, dup[0] if dup else win)
        if dup:
            return {"charged": 0, "replay": True, "used": used,
                    "limit": q["limit"], "resets_at": resets}
        if q["limit"] is not None and used + amount > q["limit"]:
            raise PlanLimit(feature, plan, code="quota_exhausted",
                            limit=q["limit"], used=used, resets_at=resets)
        _con.execute(
            "INSERT INTO usage_ledger (subject, feature, win, amount, idem_key, "
            "ts, meta) VALUES (?,?,?,?,?,?,?)",
            (subject, feature, win, amount, idem_key, now, meta[:200]))
        _con.commit()
    return {"charged": amount, "replay": False, "used": used + amount,
            "limit": q["limit"], "resets_at": resets}


def refund(uid: int | None, feature: str, idem_key: str, *, client: str = "",
           why: str = "") -> bool:
    """Give back a debit whose work failed. Written as a reversing row, never a
    delete, so the ledger still shows that it happened and was undone.

    The debit's key is retired (renamed `…:void<id>`) in the same write, so
    the client's retry of the failed turn under the SAME key is a fresh
    charge — not a replay of a debit that was given back."""
    subject = subject_for(uid, client)
    key = str(idem_key)[:120]
    with _lock:
        row = _con.execute(
            "SELECT id, win, amount FROM usage_ledger WHERE subject=? AND "
            "feature=? AND idem_key=?", (subject, feature, key)).fetchone()
        if not row or row[2] <= 0:
            return False
        void = f"{key[:100]}:void{row[0]}"
        _con.execute("UPDATE usage_ledger SET idem_key=? WHERE id=?",
                     (void, row[0]))
        _con.execute(
            "INSERT INTO usage_ledger (subject, feature, win, amount, "
            "idem_key, ts, meta) VALUES (?,?,?,?,?,?,?)",
            (subject, feature, row[1], -row[2], f"{void}:refund",
             _now(), f"refund {why}"[:200]))
        _con.commit()
    return True


def usage(uid: int | None, feature: str, client: str = "") -> dict:
    q = value(uid, feature)
    win, resets = _window(uid, q["window"], _now())
    with _lock:
        used = _used(subject_for(uid, client), feature, win)
    return {"used": used, "limit": q["limit"], "window": q["window"],
            "resets_at": resets}


# ══ what /billing/me answers ═════════════════════════════════════════════

def summary(uid: int | None, counts: dict | None = None, client: str = "") -> dict:
    """Everything a client needs to mirror the plan, in one call. `counts` are
    the live-object counts the caller already knows (alerts by class, …)."""
    plan = plan_of(uid)
    counts = counts or {}
    feats = {}
    for key, f in CATALOG["features"].items():
        item = {"kind": f["kind"], "label": f["label"]}
        if f["kind"] == "quota":
            item.update(usage(uid, key, client))
        else:
            item["value"] = value(uid, key, plan)
            if key in counts:
                item["used"] = counts[key]
        if f.get("pending"):
            item["pending"] = True
        feats[key] = item
    sub = None
    if uid:
        with _lock:
            row = _sub_row(uid)
        if row:
            sub = {"plan": row[0], "cycle": row[1], "status": row[2],
                   "period_start": row[3], "period_end": row[4],
                   "cancel_at_period_end": bool(row[5]),
                   "grace_until": row[6], "provider": row[7],
                   "pending_change": pending_change(uid)}
    return {"plan": plan, "plan_name": CATALOG["plans"][plan]["name"],
            "subscription": sub, "features": feats,
            "trial_days": trial_days(),
            "paywall_enabled": paywall_enabled()}


def public_catalog() -> dict:
    """The pricing table, as the pricing page and any client should read it."""
    plans = []
    for p in CATALOG["_ranks"]:
        meta = CATALOG["plans"][p]
        if not meta.get("public"):
            continue
        feats = {}
        for key, f in CATALOG["features"].items():
            v = _raw(f, p)
            feats[key] = v["limit"] if isinstance(v, dict) else v
        plans.append({"id": p, "name": meta["name"], "rank": meta["rank"],
                      "prices": meta["prices"], "features": feats})
    return {"currency": CATALOG["currency"], "version": CATALOG["version"],
            "gst_inclusive": True, "plans": plans, "trial_days": trial_days(),
            "features": {k: {"kind": f["kind"], "label": f["label"],
                             **({"unit": f["unit"]} if f.get("unit") else {}),
                             **({"window": f["window"]} if f.get("window") else {})}
                         for k, f in CATALOG["features"].items()}}


# ══ writing the subscription (billing.py and the admin route call these) ══

def set_subscription(uid: int, *, plan: str, status: str, cycle: str = "monthly",
                     provider: str = "", provider_sub_id: str | None = None,
                     period_start: int | None = None, period_end: int | None = None,
                     cancel_at_period_end: bool | None = None,
                     grace_until: int | None = None) -> None:
    if plan not in CATALOG["plans"]:
        raise ValueError(f"unknown plan {plan}")
    now = _now()
    with _lock:
        old = _sub_row(uid)
        if old is None:
            _con.execute(
                "INSERT INTO subscriptions (user_id, plan, cycle, status, provider, "
                "provider_sub_id, period_start, period_end, cancel_at_period_end, "
                "grace_until, created, updated) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (uid, plan, cycle, status, provider, provider_sub_id, period_start,
                 period_end, 1 if cancel_at_period_end else 0, grace_until, now, now))
        else:
            _con.execute(
                "UPDATE subscriptions SET plan=?, cycle=?, status=?, provider=?, "
                "provider_sub_id=COALESCE(?, provider_sub_id), "
                "period_start=COALESCE(?, period_start), "
                "period_end=COALESCE(?, period_end), "
                "cancel_at_period_end=COALESCE(?, cancel_at_period_end), "
                "grace_until=?, updated=? WHERE user_id=?",
                (plan, cycle, status, provider or old[7], provider_sub_id,
                 period_start, period_end,
                 None if cancel_at_period_end is None else int(cancel_at_period_end),
                 grace_until, now, uid))
        _con.commit()


def trial_days() -> int | None:
    """Days of free trial a new paid subscription starts with, or None when no
    trial is offered. Nothing grants a trial yet; the clients read this to
    decide whether to show trial wording at all."""
    days = (CATALOG.get("trial") or {}).get("days")
    return int(days) if isinstance(days, int) and days > 0 else None


def pending_change(uid: int) -> dict | None:
    with _lock:
        row = _con.execute("SELECT pending_change FROM subscriptions WHERE "
                           "user_id=?", (uid,)).fetchone()
    if not row or not row[0]:
        return None
    try:
        return json.loads(row[0])
    except ValueError:
        return None


def set_pending_change(uid: int, change: dict | None) -> None:
    with _lock:
        _con.execute("UPDATE subscriptions SET pending_change=?, updated=? "
                     "WHERE user_id=?",
                     (json.dumps(change) if change else None, _now(), uid))
        _con.commit()


def user_for_provider_sub(provider: str, sub_id: str) -> int | None:
    with _lock:
        row = _con.execute("SELECT user_id FROM subscriptions WHERE provider=? "
                           "AND provider_sub_id=?", (provider, sub_id)).fetchone()
    return row[0] if row else None


def grant(uid: int, *, reason: str, granted_by: str, plan: str | None = None,
          feature: str | None = None, value_=None, days: int | None = None) -> int:
    if not plan and not feature:
        raise ValueError("grant a plan or a feature")
    if plan and plan not in CATALOG["plans"]:
        raise ValueError(f"unknown plan {plan}")
    if feature and feature not in CATALOG["features"]:
        raise ValueError(f"unknown feature {feature}")
    exp = _now() + int(days) * 86400 if days else None
    with _lock:
        cur = _con.execute(
            "INSERT INTO entitlement_grants (user_id, plan, feature, value, "
            "expires, reason, granted_by, created) VALUES (?,?,?,?,?,?,?,?)",
            (uid, plan, feature, json.dumps(value_) if feature else None, exp,
             reason[:300], granted_by[:120], _now()))
        _con.commit()
    return cur.lastrowid


def record_event(provider: str, event_id: str, etype: str, payload: str) -> bool:
    """True the first time an event is seen; False for a redelivery."""
    with _lock:
        try:
            _con.execute("INSERT INTO billing_events (provider, event_id, type, "
                         "payload, received) VALUES (?,?,?,?,?)",
                         (provider, event_id, etype, payload[:20000], _now()))
            _con.commit()
            return True
        except Exception:                               # noqa: BLE001
            return False


def event_result(provider: str, event_id: str, result: str) -> None:
    with _lock:
        _con.execute("UPDATE billing_events SET result=? WHERE provider=? AND "
                     "event_id=?", (result[:500], provider, event_id))
        _con.commit()
