"""Pivot — codes: free time on a paid plan, or a lower price at checkout.

TWO KINDS, because they reach the plan two different ways
--------------------------------------------------------
    access     "Pro free for 30 days". Redeemed in the app with no payment:
               it becomes an `entitlement_grants` row with an end date, the
               same writer comps use, so the plan lapses on READ when the
               days run out and nothing has to sweep it.
    discount   "40% off Pro" or "₹200 off Pro+ yearly". Used at checkout:
               the code gets its own Razorpay plan at the discounted price
               (made once per code, plan and cycle, through the Plans API),
               and the subscription is created on that plan. The provider
               charges the lower price; billing.py checks the payment against
               it exactly as it checks a catalog price.
               `discount_cycles` bounds it: the subscription is authorised for
               that many cycles at the lower price and then ends, and the
               customer renews at the catalog price. NULL = every renewal.

THE LIMITS
----------
    max_redemptions   across everyone (NULL = no cap)
    per_user          how many times one account can use it (default 1)
    valid_from/until  the window, epoch seconds
    emails / domain   only these accounts ("for specific people")
    new_only          only accounts that have never had a paid subscription

A discount use is RESERVED when its checkout is created, so a code with one
use left cannot be paid for twice at once. It becomes `redeemed` when the
provider confirms the subscription active, and `released` when the
subscription dies unpaid. A reservation older than RESERVE_SECONDS that never
got paid stops counting on its own.

Terms that set a price (kind, plan, percent, amount, cycles) are fixed once a
code exists: a Razorpay plan may already carry them. Limits, dates, the allow
list, the note and the status can change.

The connection and lock are entitlements' (`ent._con`, `ent._lock`): one
account store, bound once by dataserver.

Run as a script against an account database (local, or on the VM):
    python3 coupons.py charto_users.db create --kind access --plan pro --days 30
    python3 coupons.py charto_users.db create --kind discount --plan pro --percent 40 --count 25 --prefix DIWALI
    python3 coupons.py charto_users.db list
    python3 coupons.py charto_users.db show PIVOT-ABCD-EFGH
    python3 coupons.py charto_users.db disable PIVOT-ABCD-EFGH
"""
from __future__ import annotations

import json
import re
import secrets
import threading
import time

import entitlements as ent

KINDS = ("access", "discount")
STATUSES = ("active", "disabled", "archived")
CYCLES = ("monthly", "annual")
# No 0/O, 1/I/L: a code read out on a call or copied from an image survives.
ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_RE = re.compile(r"^[A-Z0-9](?:[A-Z0-9-]{2,38})[A-Z0-9]$")
RESERVE_SECONDS = 2 * 86400
MAX_BATCH = 1000
MIN_PRICE = 1000                 # paise: below ₹10 use an access code instead
MAX_ACCESS_DAYS = 3660

# Check-then-write for one code is serialised here, so the last use of a code
# cannot be taken twice by two requests landing together.
_redeem = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS coupons (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  code TEXT NOT NULL UNIQUE COLLATE NOCASE,
  kind TEXT NOT NULL,                     -- access | discount
  plan TEXT NOT NULL,                     -- access: what it grants; discount: pro | pro_plus | any
  cycles TEXT NOT NULL DEFAULT 'any',     -- discount: monthly | annual | any
  access_days INTEGER,
  percent_off INTEGER,
  amount_off INTEGER,                     -- paise, GST included like the catalog
  discount_cycles INTEGER,                -- NULL = every renewal
  max_redemptions INTEGER,                -- NULL = no cap
  per_user INTEGER NOT NULL DEFAULT 1,
  valid_from INTEGER,
  valid_until INTEGER,
  emails TEXT NOT NULL DEFAULT '',        -- comma-separated allow list
  domain TEXT NOT NULL DEFAULT '',
  new_only INTEGER NOT NULL DEFAULT 0,
  campaign TEXT NOT NULL DEFAULT '',
  note TEXT NOT NULL DEFAULT '',
  batch TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'active',  -- active | disabled | archived
  created_by TEXT NOT NULL,
  created INTEGER NOT NULL,
  updated INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS coupons_batch ON coupons(batch);
CREATE TABLE IF NOT EXISTS coupon_redemptions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  coupon_id INTEGER NOT NULL REFERENCES coupons(id),
  user_id INTEGER NOT NULL REFERENCES users(id),
  state TEXT NOT NULL,                    -- reserved | redeemed | released | revoked
  grant_id INTEGER,
  checkout_id TEXT,
  provider_sub_id TEXT,
  provider_plan_id TEXT,
  plan TEXT,
  cycle TEXT,
  amount INTEGER,
  created INTEGER NOT NULL,
  updated INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS coupon_redemptions_coupon ON coupon_redemptions(coupon_id, state);
CREATE INDEX IF NOT EXISTS coupon_redemptions_user ON coupon_redemptions(user_id);
CREATE INDEX IF NOT EXISTS coupon_redemptions_sub ON coupon_redemptions(provider_sub_id);
-- One provider plan per (code, plan, cycle), made the first time somebody
-- checks out with it. billing.py maps a subscription's plan_id back through
-- this table to our plan, cycle and the amount the payment must match.
CREATE TABLE IF NOT EXISTS coupon_provider_plans (
  provider_plan_id TEXT PRIMARY KEY,
  coupon_id INTEGER NOT NULL REFERENCES coupons(id),
  plan TEXT NOT NULL,
  cycle TEXT NOT NULL,
  amount INTEGER NOT NULL,
  total_count INTEGER,
  created INTEGER NOT NULL,
  UNIQUE (coupon_id, plan, cycle));
"""


class CodeError(Exception):
    """A code that cannot be used here, with the reason a person can read."""

    def __init__(self, code: str, message: str, status: int = 400):
        self.code, self.message, self.status = code, message, status
        super().__init__(message)

    def body(self) -> dict:
        return {"error": self.message, "code": self.code}


def _create(con) -> None:
    con.executescript(SCHEMA)
    try:
        # Which code a pending checkout was started with, so a resumed
        # checkout keeps its price and a different code starts afresh.
        con.execute("ALTER TABLE billing_checkouts ADD COLUMN coupon_id INTEGER")
    except Exception:                                   # noqa: BLE001
        pass                                            # already there


ent.register_schema(_create)


def ensure_schema() -> None:
    """For callers that bound entitlements before importing this module."""
    with ent._lock:
        _create(ent._con)
        ent._con.commit()


def _now() -> int:
    return int(time.time())


def normalize(code: str) -> str:
    return re.sub(r"\s+", "", str(code or "")).upper()


def generate(prefix: str = "PIVOT", groups: int = 2) -> str:
    body = "-".join("".join(secrets.choice(ALPHABET) for _ in range(4))
                    for _ in range(groups))
    return f"{prefix}-{body}" if prefix else body


_COLS = ("id", "code", "kind", "plan", "cycles", "access_days", "percent_off",
         "amount_off", "discount_cycles", "max_redemptions", "per_user",
         "valid_from", "valid_until", "emails", "domain", "new_only", "campaign",
         "note", "batch", "status", "created_by", "created", "updated")


def _row(r) -> dict | None:
    return dict(zip(_COLS, r)) if r else None


def find(code: str) -> dict | None:
    with ent._lock:
        r = ent._con.execute(f"SELECT {','.join(_COLS)} FROM coupons WHERE code=?",
                             (normalize(code),)).fetchone()
    return _row(r)


def get(cid: int) -> dict | None:
    with ent._lock:
        r = ent._con.execute(f"SELECT {','.join(_COLS)} FROM coupons WHERE id=?",
                             (int(cid),)).fetchone()
    return _row(r)


# ── what a code is worth ───────────────────────────────────────────────────

def _plans() -> dict:
    return ent.CATALOG["plans"]


def plan_name(plan: str) -> str:
    return "any paid plan" if plan == "any" else _plans().get(plan, {}).get("name", plan)


def catalog_price(plan: str, cycle: str) -> int | None:
    p = (_plans().get(plan) or {}).get("prices", {}).get(cycle)
    return int(p["amount"]) if p and p.get("amount") else None


def price_after(c: dict, plan: str, cycle: str) -> int | None:
    """The discounted amount in paise, rounded down to a whole rupee."""
    base = catalog_price(plan, cycle)
    if base is None:
        return None
    if c.get("percent_off"):
        amt = base * (100 - int(c["percent_off"])) // 100
    else:
        amt = base - int(c.get("amount_off") or 0)
    return max(0, amt // 100 * 100)


def _applies(c: dict, plan: str, cycle: str) -> bool:
    return (c["plan"] in ("any", plan)) and (c["cycles"] in ("any", cycle))


def offers(c: dict) -> list[dict]:
    """Every (plan, cycle) a discount code covers, with before and after."""
    out = []
    for plan in ("pro", "pro_plus"):
        for cycle in CYCLES:
            if not _applies(c, plan, cycle):
                continue
            was, now = catalog_price(plan, cycle), price_after(c, plan, cycle)
            if was is None or now is None:
                continue
            out.append({"plan": plan, "plan_name": plan_name(plan), "cycle": cycle,
                        "was": was, "amount": now, "discount_cycles": c["discount_cycles"]})
    return out


def describe(c: dict) -> dict:
    """What a customer is told a code does. No limits, no allow list."""
    base = {"code": c["code"], "kind": c["kind"], "valid_until": c["valid_until"]}
    if c["kind"] == "access":
        return {**base, "plan": c["plan"], "plan_name": plan_name(c["plan"]),
                "days": c["access_days"],
                "summary": f"{plan_name(c['plan'])} free for {c['access_days']} days"}
    off = (f"{c['percent_off']}% off" if c["percent_off"]
           else f"₹{int(c['amount_off']) // 100:,} off")
    span = ("" if not c["discount_cycles"] else
            f" for {c['discount_cycles']} billing cycle{'s' if c['discount_cycles'] != 1 else ''}")
    return {**base, "plan": c["plan"], "plan_name": plan_name(c["plan"]),
            "cycles": c["cycles"], "percent_off": c["percent_off"],
            "amount_off": c["amount_off"], "discount_cycles": c["discount_cycles"],
            "offers": offers(c), "summary": f"{off} {plan_name(c['plan'])}{span}"}


# ── may this account use this code ─────────────────────────────────────────

def _counted(where: str, args: tuple, now: int) -> int:
    """Uses that count against a limit: redeemed, or reserved and still fresh."""
    with ent._lock:
        return ent._con.execute(
            f"SELECT COUNT(*) FROM coupon_redemptions WHERE {where} AND "
            "(state='redeemed' OR (state='reserved' AND created>?))",
            (*args, now - RESERVE_SECONDS)).fetchone()[0]


def _ever_paid(uid: int) -> bool:
    with ent._lock:
        r = ent._con.execute(
            "SELECT 1 FROM subscriptions WHERE user_id=? AND provider='razorpay' "
            "AND period_start IS NOT NULL", (uid,)).fetchone()
        g = ent._con.execute(
            "SELECT 1 FROM coupon_redemptions WHERE user_id=? AND state='redeemed' "
            "AND provider_sub_id IS NOT NULL", (uid,)).fetchone()
    return bool(r or g)


def _paid_plan(uid: int) -> str:
    with ent._lock:
        return ent._effective(ent._sub_row(uid), _now())


def check(code: str, uid: int, email: str, plan: str | None = None,
          cycle: str | None = None, *, checkout_id: str | None = None) -> dict:
    """The code's row if this account may use it now, else CodeError.
    `checkout_id` excuses the account's own reservation for that checkout,
    so resuming a checkout is not "already used"."""
    c = find(code)
    now = _now()
    if not c or c["status"] == "archived":
        raise CodeError("not_found", "That code is not valid.", 404)
    if c["status"] != "active":
        raise CodeError("inactive", "This code is no longer active.")
    if c["valid_from"] and c["valid_from"] > now:
        raise CodeError("not_started", "This code is not active yet.")
    if c["valid_until"] and c["valid_until"] <= now:
        raise CodeError("expired", "This code has expired.")
    email = (email or "").strip().lower()
    allow = {e.strip().lower() for e in c["emails"].split(",") if e.strip()}
    dom = c["domain"].strip().lower().lstrip("@")
    if (allow or dom) and not (email in allow or (dom and email.endswith("@" + dom))):
        raise CodeError("not_eligible", "This code is not available for your account.", 403)
    if c["new_only"] and _ever_paid(uid):
        raise CodeError("new_only", "This code is for accounts that have not subscribed before.", 403)
    mine_where, mine_args = "coupon_id=? AND user_id=?", (c["id"], uid)
    if checkout_id:
        mine_where += " AND COALESCE(checkout_id,'')!=?"
        mine_args += (checkout_id,)
    if _counted(mine_where, mine_args, now) >= int(c["per_user"] or 1):
        raise CodeError("already_used", "You have already used this code.", 409)
    if c["max_redemptions"] is not None:
        all_where, all_args = "coupon_id=?", (c["id"],)
        if checkout_id:
            all_where += " AND COALESCE(checkout_id,'')!=?"
            all_args += (checkout_id,)
        if _counted(all_where, all_args, now) >= int(c["max_redemptions"]):
            raise CodeError("used_up", "This code has been fully used.", 409)
    if c["kind"] == "access":
        have = _paid_plan(uid)
        if _plans()[have]["rank"] >= _plans()[c["plan"]]["rank"]:
            raise CodeError("has_plan", f"Your subscription already includes {plan_name(c['plan'])}.", 409)
    if c["kind"] == "discount" and plan:
        if not _applies(c, plan, cycle or "monthly"):
            where = plan_name(c["plan"]) + ("" if c["cycles"] == "any" else
                                            f", billed {'yearly' if c['cycles'] == 'annual' else 'monthly'}")
            raise CodeError("wrong_plan", f"This code applies to {where}.")
    return c


def redeem_access(code: str, uid: int, email: str) -> dict:
    """Use an access code now: a plan grant that ends after its days."""
    with _redeem:
        c = check(code, uid, email)
        if c["kind"] != "access":
            raise CodeError("use_checkout", "This is a discount code. Enter it at checkout.")
        gid = ent.grant(uid, plan=c["plan"], days=int(c["access_days"]),
                        reason=f"code {c['code']}", granted_by=f"code:{c['id']}")
        now = _now()
        with ent._lock:
            ent._con.execute(
                "INSERT INTO coupon_redemptions (coupon_id,user_id,state,grant_id,plan,"
                "amount,created,updated) VALUES (?,?,'redeemed',?,?,0,?,?)",
                (c["id"], uid, gid, c["plan"], now, now))
            ent._con.commit()
    return {**describe(c), "until": now + int(c["access_days"]) * 86400}


# ── discounts at checkout (billing.py calls these) ─────────────────────────

def reserve_discount(code: str, uid: int, email: str, plan: str, cycle: str,
                     checkout_id: str, make_plan) -> dict:
    """Hold one use of a discount code for this checkout and name the provider
    plan to subscribe on. `make_plan(plan, cycle, amount, coupon)` creates the
    provider plan the first time and returns its id."""
    with _redeem:
        c = check(code, uid, email, plan, cycle, checkout_id=checkout_id)
        if c["kind"] != "discount":
            raise CodeError("use_redeem", "This code gives free time. Redeem it from Plan & billing, no payment needed.")
        amount = price_after(c, plan, cycle)
        if amount is None:
            raise CodeError("wrong_plan", "This code does not apply to that plan.")
        if amount < MIN_PRICE:
            raise CodeError("bad_price", "This code would make the plan free. Ask for an access code instead.")
        with ent._lock:
            r = ent._con.execute(
                "SELECT provider_plan_id, total_count FROM coupon_provider_plans "
                "WHERE coupon_id=? AND plan=? AND cycle=?", (c["id"], plan, cycle)).fetchone()
        if r:
            pid, total = r
        else:
            pid = make_plan(plan, cycle, amount, c)
            total = c["discount_cycles"]
            with ent._lock:
                ent._con.execute(
                    "INSERT INTO coupon_provider_plans (provider_plan_id,coupon_id,plan,"
                    "cycle,amount,total_count,created) VALUES (?,?,?,?,?,?,?)",
                    (pid, c["id"], plan, cycle, amount, total, _now()))
                ent._con.commit()
        now = _now()
        with ent._lock:
            ent._con.execute(
                "UPDATE coupon_redemptions SET state='released', updated=? WHERE "
                "user_id=? AND state='reserved' AND COALESCE(checkout_id,'')!=?",
                (now, uid, checkout_id))
            have = ent._con.execute(
                "SELECT id FROM coupon_redemptions WHERE coupon_id=? AND user_id=? AND "
                "checkout_id=? AND state='reserved'", (c["id"], uid, checkout_id)).fetchone()
            if not have:
                ent._con.execute(
                    "INSERT INTO coupon_redemptions (coupon_id,user_id,state,checkout_id,"
                    "provider_plan_id,plan,cycle,amount,created,updated) "
                    "VALUES (?,?,'reserved',?,?,?,?,?,?,?)",
                    (c["id"], uid, checkout_id, pid, plan, cycle, amount, now, now))
            ent._con.commit()
    return {"coupon_id": c["id"], "code": c["code"], "provider_plan_id": pid,
            "amount": amount, "was": catalog_price(plan, cycle), "total_count": total,
            "summary": describe(c)["summary"]}


def attach_subscription(checkout_id: str, provider_sub_id: str) -> None:
    with ent._lock:
        ent._con.execute("UPDATE coupon_redemptions SET provider_sub_id=?, updated=? "
                         "WHERE checkout_id=?", (provider_sub_id, _now(), checkout_id))
        ent._con.commit()


def release_checkout(checkout_id: str) -> None:
    with ent._lock:
        ent._con.execute("UPDATE coupon_redemptions SET state='released', updated=? "
                         "WHERE checkout_id=? AND state='reserved'", (_now(), checkout_id))
        ent._con.commit()


def on_subscription(provider_sub_id: str, status: str) -> None:
    """billing._apply calls this with every status it writes."""
    if not provider_sub_id:
        return
    new = {"active": "redeemed", "past_due": "redeemed",
           "expired": "released", "cancelled": "released"}.get(status)
    if not new:
        return
    with ent._lock:
        # Only a reservation moves: a redeemed use stays redeemed when the
        # subscription later ends, because it was used.
        ent._con.execute("UPDATE coupon_redemptions SET state=?, updated=? WHERE "
                         "provider_sub_id=? AND state='reserved'",
                         (new, _now(), provider_sub_id))
        ent._con.commit()


def provider_plan(pid: str) -> dict | None:
    """{plan, cycle, amount} for a provider plan a code made, else None."""
    if not pid or ent._con is None:
        return None
    with ent._lock:
        try:
            r = ent._con.execute("SELECT plan, cycle, amount FROM coupon_provider_plans "
                                 "WHERE provider_plan_id=?", (pid,)).fetchone()
        except Exception:                               # noqa: BLE001 — table not made yet
            return None
    return {"plan": r[0], "cycle": r[1], "amount": r[2]} if r else None


# ── the owner side: making and managing codes ──────────────────────────────

def _int(v, name: str, lo: int, hi: int, *, required=False) -> int | None:
    if v in (None, ""):
        if required:
            raise CodeError("invalid", f"{name} is required.")
        return None
    try:
        n = int(v)
    except (TypeError, ValueError):
        raise CodeError("invalid", f"{name} must be a whole number.") from None
    if not lo <= n <= hi:
        raise CodeError("invalid", f"{name} must be between {lo} and {hi}.")
    return n


def _emails(v) -> str:
    items = v if isinstance(v, list) else str(v or "").replace("\n", ",").split(",")
    out = []
    for e in items:
        e = str(e).strip().lower()
        if not e:
            continue
        if not re.match(r"^[^@\s,]+@[^@\s,]+\.[^@\s,]+$", e) or len(e) > 254:
            raise CodeError("invalid", f"'{e[:60]}' is not an email address.")
        out.append(e)
    if len(out) > 5000:
        raise CodeError("invalid", "An allow list holds at most 5,000 emails.")
    return ",".join(dict.fromkeys(out))


def _domain(v) -> str:
    d = str(v or "").strip().lower().lstrip("@")
    if d and not re.match(r"^[a-z0-9.-]+\.[a-z]{2,}$", d):
        raise CodeError("invalid", f"'{d[:60]}' is not a domain.")
    return d


def _limits(spec: dict) -> dict:
    """The fields that may change after a code exists, validated."""
    out = {}
    if "max_redemptions" in spec:
        out["max_redemptions"] = _int(spec["max_redemptions"], "Total uses", 1, 10_000_000)
    if "per_user" in spec:
        out["per_user"] = _int(spec["per_user"], "Uses per account", 1, 1000) or 1
    for k, label in (("valid_from", "Start"), ("valid_until", "End")):
        if k in spec:
            out[k] = _int(spec[k], label, 0, 4_102_444_800)
    if "emails" in spec:
        out["emails"] = _emails(spec["emails"])
    if "domain" in spec:
        out["domain"] = _domain(spec["domain"])
    if "new_only" in spec:
        out["new_only"] = 1 if spec["new_only"] in (True, 1, "1", "true", "on", "yes") else 0
    for k, n in (("campaign", 80), ("note", 500)):
        if k in spec:
            out[k] = str(spec[k] or "").strip()[:n]
    if "status" in spec:
        if spec["status"] not in STATUSES:
            raise CodeError("invalid", f"status must be one of {', '.join(STATUSES)}.")
        out["status"] = spec["status"]
    if out.get("valid_from") and out.get("valid_until") and out["valid_until"] <= out["valid_from"]:
        raise CodeError("invalid", "The end must come after the start.")
    return out


def create(spec: dict, actor: str) -> list[dict]:
    """One code, or `count` codes with the same terms (a batch)."""
    kind = spec.get("kind")
    if kind not in KINDS:
        raise CodeError("invalid", "kind must be 'access' or 'discount'.")
    plan = str(spec.get("plan") or "")
    row = {"kind": kind, "plan": plan, "cycles": "any", "access_days": None,
           "percent_off": None, "amount_off": None, "discount_cycles": None}
    paid = [p for p, m in _plans().items() if m["rank"] > 0 and m.get("public")]
    if kind == "access":
        if plan not in paid:
            raise CodeError("invalid", f"An access code grants one of: {', '.join(paid)}.")
        row["access_days"] = _int(spec.get("access_days"), "Days", 1, MAX_ACCESS_DAYS, required=True)
    else:
        if plan not in (*paid, "any"):
            raise CodeError("invalid", f"A discount applies to one of: {', '.join(paid)}, any.")
        cycles = str(spec.get("cycles") or "any")
        if cycles not in (*CYCLES, "any"):
            raise CodeError("invalid", "cycles must be monthly, annual or any.")
        row["cycles"] = cycles
        pct = _int(spec.get("percent_off"), "Percent off", 1, 95)
        amt = _int(spec.get("amount_off"), "Amount off (paise)", 100, 10_000_000)
        if (pct is None) == (amt is None):
            raise CodeError("invalid", "Give a percent off or an amount off, not both.")
        row["percent_off"], row["amount_off"] = pct, amt
        row["discount_cycles"] = _int(spec.get("discount_cycles"), "Billing cycles", 1, 120)
        probe = {**row}
        if not offers(probe):
            raise CodeError("invalid", "No catalog price matches that plan and cycle.")
        low = min(o["amount"] for o in offers(probe))
        if low < MIN_PRICE:
            raise CodeError("invalid", "That discount would bring a price under ₹10. Use an access code for free time.")
    lim = _limits({"per_user": 1, **spec})
    count = _int(spec.get("count"), "Number of codes", 1, MAX_BATCH) or 1
    custom = normalize(spec.get("code") or "")
    if custom and count > 1:
        raise CodeError("invalid", "A custom code is one code. Leave it empty to generate a batch.")
    if custom and not CODE_RE.match(custom):
        raise CodeError("invalid", "A code is 4 to 40 letters, digits or dashes.")
    prefix = normalize(spec.get("prefix") or "PIVOT")[:16]
    if prefix and not re.match(r"^[A-Z0-9]+$", prefix):
        raise CodeError("invalid", "A prefix is letters and digits only.")
    batch = secrets.token_hex(6) if count > 1 else ""
    now = _now()
    made = []
    with ent._lock:
        for _ in range(count):
            for _attempt in range(20):
                code = custom or generate(prefix)
                try:
                    cols = {**row, **lim, "code": code, "batch": batch,
                            "status": lim.get("status", "active"),
                            "created_by": actor[:120], "created": now, "updated": now}
                    cur = ent._con.execute(
                        f"INSERT INTO coupons ({','.join(cols)}) VALUES "
                        f"({','.join('?' * len(cols))})", tuple(cols.values()))
                    made.append(cur.lastrowid)
                    break
                except Exception as exc:                # noqa: BLE001 — sqlite3.IntegrityError
                    if "UNIQUE" not in str(exc):
                        ent._con.rollback()
                        raise
                    if custom:
                        ent._con.rollback()
                        raise CodeError("exists", f"The code {custom} already exists.", 409) from None
            else:
                ent._con.rollback()
                raise CodeError("exists", "Could not find a free code. Use a longer prefix.", 409)
        ent._con.commit()
    return [get(i) for i in made]


def update(cid: int, spec: dict) -> dict:
    c = get(cid)
    if not c:
        raise CodeError("not_found", "No such code.", 404)
    fixed = {"kind", "plan", "cycles", "access_days", "percent_off", "amount_off",
             "discount_cycles", "code"} & set(spec)
    if fixed:
        raise CodeError("fixed", f"{', '.join(sorted(fixed))} cannot change once a code exists. "
                                 "Disable it and make a new one.")
    ch = _limits(spec)
    vf = ch.get("valid_from", c["valid_from"])
    vu = ch.get("valid_until", c["valid_until"])
    if vf and vu and vu <= vf:
        raise CodeError("invalid", "The end must come after the start.")
    if not ch:
        return c
    with ent._lock:
        ent._con.execute(
            f"UPDATE coupons SET {', '.join(f'{k}=?' for k in ch)}, updated=? WHERE id=?",
            (*ch.values(), _now(), cid))
        ent._con.commit()
    return get(cid)


def bulk_status(batch: str, status: str) -> int:
    if status not in STATUSES or not batch:
        raise CodeError("invalid", "A batch and a status are required.")
    with ent._lock:
        n = ent._con.execute("UPDATE coupons SET status=?, updated=? WHERE batch=?",
                             (status, _now(), batch)).rowcount
        ent._con.commit()
    return n


def usage(cid: int) -> dict:
    now = _now()
    with ent._lock:
        rows = ent._con.execute(
            "SELECT state, COUNT(*) FROM coupon_redemptions WHERE coupon_id=? "
            "AND (state!='reserved' OR created>?) GROUP BY state",
            (cid, now - RESERVE_SECONDS)).fetchall()
    return {s: n for s, n in rows}


def listing(status: str | None = None, q: str = "", batch: str = "",
            limit: int = 500) -> list[dict]:
    where, args = ["1=1"], []
    if status:
        where.append("c.status=?")
        args.append(status)
    if q:
        where.append("(c.code LIKE ? OR c.campaign LIKE ? OR c.note LIKE ?)")
        args += [f"%{q}%"] * 3
    if batch:
        where.append("c.batch=?")
        args.append(batch)
    now = _now()
    with ent._lock:
        rows = ent._con.execute(
            f"SELECT {','.join('c.' + k for k in _COLS)}, "
            "(SELECT COUNT(*) FROM coupon_redemptions r WHERE r.coupon_id=c.id AND r.state='redeemed'), "
            "(SELECT COUNT(*) FROM coupon_redemptions r WHERE r.coupon_id=c.id AND r.state='reserved' AND r.created>?) "
            f"FROM coupons c WHERE {' AND '.join(where)} ORDER BY c.id DESC LIMIT ?",
            (now - RESERVE_SECONDS, *args, int(limit))).fetchall()
    out = []
    for r in rows:
        c = _row(r[:len(_COLS)])
        c.update(redeemed=r[-2], reserved=r[-1], summary=describe(c)["summary"],
                 emails_count=len([e for e in c["emails"].split(",") if e]))
        out.append(c)
    return out


def redemptions(cid: int | None = None, uid: int | None = None, limit: int = 500) -> list[dict]:
    where, args = ["1=1"], []
    if cid:
        where.append("r.coupon_id=?")
        args.append(cid)
    if uid:
        where.append("r.user_id=?")
        args.append(uid)
    with ent._lock:
        rows = ent._con.execute(
            "SELECT r.id, r.coupon_id, c.code, r.user_id, u.email, r.state, r.grant_id, "
            "r.provider_sub_id, r.plan, r.cycle, r.amount, r.created, r.updated "
            "FROM coupon_redemptions r JOIN coupons c ON c.id=r.coupon_id "
            "LEFT JOIN users u ON u.id=r.user_id "
            f"WHERE {' AND '.join(where)} ORDER BY r.id DESC LIMIT ?",
            (*args, int(limit))).fetchall()
    keys = ("id", "coupon_id", "code", "user_id", "email", "state", "grant_id",
            "provider_sub_id", "plan", "cycle", "amount", "created", "updated")
    return [dict(zip(keys, r)) for r in rows]


def revoke(rid: int) -> dict:
    """Take back an access code's free time. A discount that was paid for is
    a subscription: cancel that from billing, not here."""
    with ent._lock:
        r = ent._con.execute("SELECT state, grant_id, provider_sub_id FROM coupon_redemptions "
                             "WHERE id=?", (rid,)).fetchone()
    if not r:
        raise CodeError("not_found", "No such redemption.", 404)
    state, gid, sid = r
    if state not in ("redeemed", "reserved"):
        raise CodeError("invalid", f"This use is already {state}.")
    if sid and state == "redeemed":
        raise CodeError("paid", "This discount is on a paid subscription. It ends when the subscription is cancelled.")
    now = _now()
    with ent._lock:
        if gid:
            ent._con.execute("UPDATE entitlement_grants SET expires=? WHERE id=? AND "
                             "(expires IS NULL OR expires>?)", (now, gid, now))
        ent._con.execute("UPDATE coupon_redemptions SET state='revoked', updated=? WHERE id=?",
                         (now, rid))
        ent._con.commit()
    return {"id": rid, "state": "revoked"}


# ── the script ─────────────────────────────────────────────────────────────

def _cli(argv: list[str]) -> int:
    import argparse
    import sqlite3

    ap = argparse.ArgumentParser(prog="coupons.py", description="Make and manage Pivot codes.")
    ap.add_argument("db", help="path to charto_users.db")
    sub = ap.add_subparsers(dest="cmd", required=True)
    mk = sub.add_parser("create", help="make one code or a batch")
    mk.add_argument("--kind", choices=KINDS, required=True)
    mk.add_argument("--plan", required=True, help="pro | pro_plus (discount: also any)")
    mk.add_argument("--days", type=int, dest="access_days", help="access: free days")
    mk.add_argument("--percent", type=int, dest="percent_off")
    mk.add_argument("--rupees-off", type=int, help="discount: flat rupees off")
    mk.add_argument("--cycles", default="any", help="discount: monthly | annual | any")
    mk.add_argument("--discount-cycles", type=int, help="discount: billing cycles at the lower price")
    mk.add_argument("--code", help="a custom code, e.g. DIWALI50")
    mk.add_argument("--prefix", default="PIVOT")
    mk.add_argument("--count", type=int, default=1)
    mk.add_argument("--max", type=int, dest="max_redemptions", help="total uses across everyone")
    mk.add_argument("--per-user", type=int, default=1)
    mk.add_argument("--expires-days", type=int, help="valid for this many days from now")
    mk.add_argument("--emails", default="", help="comma-separated: only these accounts")
    mk.add_argument("--domain", default="")
    mk.add_argument("--new-only", action="store_true")
    mk.add_argument("--campaign", default="")
    mk.add_argument("--note", default="")
    sub.add_parser("list", help="every code with its use count")
    for name in ("show", "disable", "enable", "archive"):
        p = sub.add_parser(name)
        p.add_argument("code")
    a = ap.parse_args(argv)

    con = sqlite3.connect(a.db, check_same_thread=False)
    con.execute("PRAGMA busy_timeout=10000")
    ent.bind(con, threading.Lock())
    ensure_schema()
    try:
        if a.cmd == "create":
            spec = {k: v for k, v in vars(a).items() if v not in (None, "") and k not in (
                "db", "cmd", "rupees_off", "expires_days")}
            if a.rupees_off:
                spec["amount_off"] = a.rupees_off * 100
            if a.expires_days:
                spec["valid_until"] = _now() + a.expires_days * 86400
            for c in create(spec, "script"):
                print(f"{c['code']}\t{describe(c)['summary']}")
        elif a.cmd == "list":
            for c in listing():
                cap = c["max_redemptions"] if c["max_redemptions"] is not None else "∞"
                print(f"{c['code']}\t{c['status']}\t{c['redeemed']}/{cap}\t{c['summary']}")
        else:
            c = find(a.code)
            if not c:
                print(f"{a.code}: no such code")
                return 1
            if a.cmd == "show":
                print(json.dumps({**c, **describe(c), "uses": usage(c["id"]),
                                  "redemptions": redemptions(c["id"], limit=50)}, indent=2))
            else:
                st = {"disable": "disabled", "enable": "active", "archive": "archived"}[a.cmd]
                update(c["id"], {"status": st})
                print(f"{c['code']}: {st}")
    except CodeError as exc:
        print(f"refused: {exc.message}")
        return 2
    return 0


if __name__ == "__main__":
    import sys
    raise SystemExit(_cli(sys.argv[1:]))
