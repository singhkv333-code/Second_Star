"""Revenue: what a live play's trade costs, the wallet that pays for it, and
the free allowance of play builds. The spec is Revenuemodel.md at the repo
root; this file is its core, built small on purpose.

THE MODEL IN ONE LINE
---------------------
Research and paper trading are free. A LIVE play pays per trade it places,
₹10 (₹5 or 0.1% for delivery, whichever is less), and never more than ₹499 in
a calendar month (IST). There are no plans.

WHAT IS BUILT HERE, AND WHAT IS NOT YET
--------------------------------------
    built   prices and the fee function (paise, integers, GST inside)
            the ledger of billable trades, keyed on the play action so a
              replayed fill is never charged twice, and capped under the
              users lock so two fills at once cannot pass ₹499
            the prepaid wallet (Razorpay one-time payments) and its ledger
            the account state, and the one rule the engine asks before an
              entry: can this account pay for it? Exits never ask
            play builds: 3 to start, +10 a month once any play is armed on
              paper
            the month's statement, with base and GST split out
    not yet UPI AutoPay mandates and the monthly debit cycle, deep-answer and
            backtest meters, invoices. The wallet is the only billing method.

LIVE IS NOT LIVE YET
--------------------
No play places a broker order: Pivot simulates, it does not execute
(CLAUDE.md §4). `PIVOT_LIVE_PLAYS=1` lets a play be switched to the live
venue so this ledger can be exercised end to end, and such a play still fills
in the paper book. It is a local test switch, never a production setting,
until exchange empanelment and broker onboarding are done.

    PIVOT_REVENUE=1      enforce the build allowance (charges need live plays)
    PIVOT_LIVE_PLAYS=1   allow the live venue (local billing tests only)

Bound to dataserver (`bind()`) for its users connection and lock, like the
other modules that share charto_users.db.
"""
from __future__ import annotations

import logging
import os
import sqlite3
import time
from datetime import datetime, timedelta, timezone

log = logging.getLogger("revenue")

ds = None

IST = timezone(timedelta(hours=5, minutes=30))

# ── the price list: every number lives here and nowhere else ───────────────
PRICING = {
    "GST_RATE": 0.18,                  # prices include GST
    "FNO_FEE_PAISE": 1000,             # ₹10: intraday, F&O, futures, MCX, crypto
    "DELIVERY_FEE_MAX_PAISE": 500,     # ₹5
    "DELIVERY_FEE_PCT": 0.001,         # 0.1% of the value filled
    "MONTHLY_CAP_PAISE": 49900,        # ₹499
    "WALLET_TOPUPS_PAISE": (10000, 25000, 50000),
}
FREE_LIMITS = {
    "STARTER_BUILDS": 3,
    "PAPER_ARM_UNLOCK_BUILDS": 10,     # per calendar month
    "MAX_ARMED_LIVE_PLAYS": 5,
    "NEWS_JUDGE_CHECKS_PER_PLAY_PER_DAY": 20,
}
# The connected broker's own brokerage per order, for the before-arm estimate.
BROKERAGE_PAISE = {"Zerodha": 2000, "Dhan": 2000, "Upstox": 2000, "Angel One": 2000,
                   "mStock": 500, "Kotak Neo": 0}

FNO, DELIVERY = "FNO", "DELIVERY"
# What a play's do opens. A sell, a cover and a halt only ever reduce.
ENTRY_KINDS = ("buy", "short", "options", "future", "rebalance")
_DERIV_KINDS = ("short", "cover", "options", "future")
_CRYPTO = ("USDT", "-USD", "USD")
_TRUE = {"1", "true", "yes", "on"}


def enforced() -> bool:
    return os.environ.get("PIVOT_REVENUE", "").strip().lower() in _TRUE


def live_open() -> bool:
    return os.environ.get("PIVOT_LIVE_PLAYS", "").strip().lower() in _TRUE


def bind(module) -> None:
    global ds
    ds = module
    try:
        import billing
        billing.on_wallet_payment = on_payment_captured
    except Exception:                                       # noqa: BLE001
        log.warning("revenue: the Razorpay webhook will not credit wallets", exc_info=True)


class Limit(ValueError):
    """A refusal the trader can act on; its text says what to do."""


# ══ money ════════════════════════════════════════════════════════════════════

def month_of(ts: float | None = None) -> str:
    return datetime.fromtimestamp(ts if ts is not None else time.time(), IST).strftime("%Y-%m")


def fee_paise(segment: str, filled_paise: int) -> int:
    if segment == DELIVERY:
        return min(PRICING["DELIVERY_FEE_MAX_PAISE"],
                   int(round(max(0, filled_paise) * PRICING["DELIVERY_FEE_PCT"])))
    return PRICING["FNO_FEE_PAISE"]


def gst_split(total_paise: int) -> dict:
    """₹10 = ₹8.47 + ₹1.53: the base is the GST-inclusive total over 1.18."""
    base = int(round(total_paise / (1 + PRICING["GST_RATE"])))
    return {"total_paise": total_paise, "base_paise": base, "gst_paise": total_paise - base}


def segment_of(do: dict) -> str:
    """Decided by what the action trades: delivery equity is DELIVERY,
    everything else (intraday, F&O, futures, MCX, crypto) is FNO."""
    kind, product = do.get("kind"), do.get("product")
    sym = str(do.get("symbol") or "").upper()
    if kind in _DERIV_KINDS or product in ("MIS", "PERP", "NRML") or sym.endswith(_CRYPTO):
        return FNO
    return DELIVERY


def filled_paise(body: dict) -> int:
    """The rupee value an action filled, read from its play event. Unknown
    reads as 0, which can only ever undercharge a delivery trade."""
    t = body.get("ticket") or {}
    legs = t.get("legs") or body.get("legs") or []
    v = 0.0
    try:
        if legs:
            for lg in legs:
                px = lg.get("px") or lg.get("price") or lg.get("fill_price") or 0
                v += abs(float(lg.get("qty") or 0) * float(px) * float(lg.get("fx") or 1.0))
        elif t.get("qty"):
            v = abs(float(t["qty"]) * float(body.get("fill_price") or t.get("price") or 0))
    except (TypeError, ValueError):
        v = 0.0
    return int(round(v * 100))


# ══ storage ══════════════════════════════════════════════════════════════════

_SCHEMA = """
CREATE TABLE IF NOT EXISTS rev_accounts (
  user_id       INTEGER PRIMARY KEY REFERENCES users(id),
  wallet_paise  INTEGER NOT NULL DEFAULT 0,     -- may dip below 0: a trade in flight completes
  topped_up     INTEGER NOT NULL DEFAULT 0,     -- has ever paid in: NO_METHOD vs RESTRICTED
  starter_used  INTEGER NOT NULL DEFAULT 0,
  updated       INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS rev_months (
  user_id       INTEGER NOT NULL REFERENCES users(id),
  month         TEXT NOT NULL,                  -- YYYY-MM, IST
  charged_paise INTEGER NOT NULL DEFAULT 0,     -- the cap is checked against this, under the lock
  fee_paise     INTEGER NOT NULL DEFAULT 0,
  trades        INTEGER NOT NULL DEFAULT 0,
  builds_used   INTEGER NOT NULL DEFAULT 0,
  unlocked      INTEGER NOT NULL DEFAULT 0,     -- a paper play was armed this month
  cap_noted     INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (user_id, month));
CREATE TABLE IF NOT EXISTS rev_trades (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id       INTEGER NOT NULL REFERENCES users(id),
  play_id       INTEGER NOT NULL,
  action_key    TEXT NOT NULL UNIQUE,           -- one play action, one charge
  segment       TEXT NOT NULL,
  broker        TEXT NOT NULL DEFAULT 'paper',
  filled_paise  INTEGER NOT NULL,
  fee_paise     INTEGER NOT NULL,               -- before the cap
  charge_paise  INTEGER NOT NULL,               -- after it
  waived_paise  INTEGER NOT NULL,
  month         TEXT NOT NULL,
  created       INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS rev_trades_user ON rev_trades(user_id, month);
CREATE TABLE IF NOT EXISTS rev_wallet (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id       INTEGER NOT NULL REFERENCES users(id),
  delta_paise   INTEGER NOT NULL,
  reason        TEXT NOT NULL,                  -- TOPUP | TRADE_CHARGE | ADJUSTMENT
  ref           TEXT NOT NULL,
  balance_after INTEGER NOT NULL,
  created       INTEGER NOT NULL,
  UNIQUE (reason, ref));
CREATE TABLE IF NOT EXISTS rev_orders (
  order_id      TEXT PRIMARY KEY,               -- Razorpay order for a wallet top-up
  user_id       INTEGER NOT NULL REFERENCES users(id),
  amount_paise  INTEGER NOT NULL,
  status        TEXT NOT NULL,                  -- created | paid
  created       INTEGER NOT NULL);
"""
_ready_for = None


def _db():
    global _ready_for
    con = ds._users
    if _ready_for is not con:
        with ds._users_lock:
            con.executescript(_SCHEMA)
            con.commit()
        _ready_for = con
    return con


def _account(con, uid: int) -> dict:
    """Called with the lock held."""
    con.execute("INSERT OR IGNORE INTO rev_accounts (user_id, updated) VALUES (?,?)", (uid, int(time.time())))
    w, t, s = con.execute("SELECT wallet_paise, topped_up, starter_used FROM rev_accounts WHERE user_id=?",
                          (uid,)).fetchone()
    return {"wallet_paise": w, "topped_up": bool(t), "starter_used": s}


def _month(con, uid: int, month: str) -> dict:
    """Called with the lock held."""
    con.execute("INSERT OR IGNORE INTO rev_months (user_id, month) VALUES (?,?)", (uid, month))
    r = con.execute("SELECT charged_paise, fee_paise, trades, builds_used, unlocked, cap_noted "
                    "FROM rev_months WHERE user_id=? AND month=?", (uid, month)).fetchone()
    return dict(zip(("charged_paise", "fee_paise", "trades", "builds_used", "unlocked", "cap_noted"), r))


def _wallet_move(con, uid: int, delta: int, reason: str, ref: str) -> int | None:
    """Called with the lock held. None when this (reason, ref) already moved."""
    bal = _account(con, uid)["wallet_paise"] + delta
    try:
        con.execute("INSERT INTO rev_wallet (user_id, delta_paise, reason, ref, balance_after, created) "
                    "VALUES (?,?,?,?,?,?)", (uid, delta, reason, ref, bal, int(time.time())))
    except sqlite3.IntegrityError:
        return None
    con.execute("UPDATE rev_accounts SET wallet_paise=?, updated=?, topped_up=MAX(topped_up, ?) WHERE user_id=?",
                (bal, int(time.time()), 1 if reason == "TOPUP" else 0, uid))
    return bal


# ══ charging a live trade ════════════════════════════════════════════════════

def charge(uid: int, action_key: str, play_id: int, segment: str, filled: int,
           broker: str = "paper", now: float | None = None) -> dict:
    """Record one billable play action. Idempotent on `action_key`; the cap is
    read and written under the users lock, so concurrent fills cannot pass it.
    Returns the trade row, with `cap_reached` true on the one charge that
    took the month to the cap."""
    con = _db()
    month = month_of(now)
    with ds._users_lock:
        old = con.execute("SELECT fee_paise, charge_paise, waived_paise, month FROM rev_trades "
                          "WHERE action_key=?", (action_key,)).fetchone()
        if old:
            return {"duplicate": True, "fee_paise": old[0], "charge_paise": old[1],
                    "waived_paise": old[2], "month": old[3], "cap_reached": False}
        try:
            m = _month(con, uid, month)
            fee = fee_paise(segment, filled)
            room = max(0, PRICING["MONTHLY_CAP_PAISE"] - m["charged_paise"])
            amt = min(fee, room)
            con.execute("INSERT INTO rev_trades (user_id, play_id, action_key, segment, broker, filled_paise, "
                        "fee_paise, charge_paise, waived_paise, month, created) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (uid, play_id, action_key, segment, broker, int(filled), fee, amt, fee - amt, month,
                         int(now if now is not None else time.time())))
            reached = bool(room and amt == room and not m["cap_noted"])
            con.execute("UPDATE rev_months SET charged_paise=charged_paise+?, fee_paise=fee_paise+?, "
                        "trades=trades+1, cap_noted=MAX(cap_noted, ?) WHERE user_id=? AND month=?",
                        (amt, fee, 1 if reached else 0, uid, month))
            if amt:
                _wallet_move(con, uid, -amt, "TRADE_CHARGE", action_key)
            con.commit()
        except Exception:
            con.rollback()
            raise
    return {"duplicate": False, "fee_paise": fee, "charge_paise": amt, "waived_paise": fee - amt,
            "month": month, "cap_reached": reached}


def state(uid: int) -> str:
    """NO_METHOD (never paid in), ACTIVE, or RESTRICTED (the wallet cannot
    cover a ₹10 trade and the month is not yet at the cap)."""
    return "ACTIVE" if may_open(uid, FNO)[0] else (
        "RESTRICTED" if _snapshot(uid)["account"]["topped_up"] else "NO_METHOD")


def may_open(uid: int, segment: str) -> tuple[bool, str]:
    """Whether a live play may OPEN or ADD to a position now. Asked only for
    entries: exits, stops, targets and halts never ask and always run."""
    snap = _snapshot(uid)
    room = max(0, PRICING["MONTHLY_CAP_PAISE"] - snap["month"]["charged_paise"])
    need = min(PRICING["FNO_FEE_PAISE"] if segment == FNO else PRICING["DELIVERY_FEE_MAX_PAISE"], room)
    bal = snap["account"]["wallet_paise"]
    if bal >= need:
        return True, ""
    if not snap["account"]["topped_up"]:
        return False, "Live plays are paid from your Pivot wallet. Add money to it to place live trades."
    short = need - bal
    return False, (f"Paused: add {_rupees(short)} or more to your Pivot wallet to resume new entries. "
                   "Exits keep running.")


def _snapshot(uid: int) -> dict:
    con = _db()
    with ds._users_lock:
        acc = _account(con, uid)
        m = _month(con, uid, month_of())
        con.commit()
    return {"account": acc, "month": m}


def _rupees(paise: int) -> str:
    r = paise / 100
    return f"₹{r:,.0f}" if r == int(r) else f"₹{r:,.2f}"


# ══ the wallet ═══════════════════════════════════════════════════════════════

def credit(uid: int, paise: int, reason: str, ref: str) -> int | None:
    """Add money. Idempotent on (reason, ref): a replayed payment adds nothing.
    A negative balance from a trade in flight is recovered first, by addition."""
    con = _db()
    with ds._users_lock:
        bal = _wallet_move(con, uid, int(paise), reason, ref)
        con.commit()
    return bal


def _razorpay():
    import billing
    return billing


def topup_available() -> bool:
    b = _razorpay()
    return bool(b._env("RAZORPAY_KEY_ID") and b._env("RAZORPAY_KEY_SECRET"))


def topup_order(uid: int, amount_paise: int) -> tuple[int, dict]:
    if amount_paise not in PRICING["WALLET_TOPUPS_PAISE"]:
        return 400, {"error": "a top-up is ₹100, ₹250 or ₹500"}
    if not topup_available():
        return 503, {"error": "Payments are not configured on this server.", "code": "billing_unavailable"}
    b = _razorpay()
    try:
        order = b.http("POST", "/orders", {"amount": amount_paise, "currency": "INR",
                                           "receipt": f"w{uid}-{int(time.time())}",
                                           "notes": {"kind": "wallet", "user_id": str(uid)}})
    except b.BillingError as exc:
        return 502, {"error": str(exc)}
    oid = str(order.get("id") or "")
    if not oid:
        return 502, {"error": "Razorpay did not return an order"}
    con = _db()
    with ds._users_lock:
        con.execute("INSERT OR IGNORE INTO rev_orders (order_id, user_id, amount_paise, status, created) "
                    "VALUES (?,?,?,?,?)", (oid, uid, amount_paise, "created", int(time.time())))
        con.commit()
    return 200, {"order_id": oid, "amount_paise": amount_paise, "key_id": b._env("RAZORPAY_KEY_ID"),
                 "test_mode": b.test_mode()}


def topup_verify(uid: int, body: dict) -> tuple[int, dict]:
    """The checkout's fast path. The signature proves Razorpay saw this order
    paid; the amount comes from our own order row, never from the browser."""
    b = _razorpay()
    oid = str(body.get("razorpay_order_id") or "")
    pay = str(body.get("razorpay_payment_id") or "")
    sig = str(body.get("razorpay_signature") or "")
    if not b.verify_payment_signature(oid, pay, sig, b._env("RAZORPAY_KEY_SECRET")):
        return 400, {"error": "the payment could not be verified"}
    return _settle(oid, pay, uid)


def _settle(oid: str, pay: str, uid: int | None) -> tuple[int, dict]:
    con = _db()
    with ds._users_lock:
        row = con.execute("SELECT user_id, amount_paise FROM rev_orders WHERE order_id=?", (oid,)).fetchone()
        if not row or (uid is not None and row[0] != uid):
            return 404, {"error": "no such top-up"}
        bal = _wallet_move(con, row[0], row[1], "TOPUP", pay or oid)
        con.execute("UPDATE rev_orders SET status='paid' WHERE order_id=?", (oid,))
        con.commit()
    return 200, {"credited_paise": row[1] if bal is not None else 0, "duplicate": bal is None,
                 "billing": summary(row[0])}


def on_payment_captured(ev: dict) -> str | None:
    """The webhook's path, for a checkout whose browser closed before verify.
    Only payments against one of our wallet orders are ours."""
    ent = ((ev.get("payload") or {}).get("payment") or {}).get("entity") or {}
    if (ent.get("notes") or {}).get("kind") != "wallet" or not ent.get("order_id"):
        return None
    code, out = _settle(str(ent["order_id"]), str(ent.get("id") or ""), None)
    return f"wallet {code} {'duplicate' if out.get('duplicate') else 'credited'}"


# ══ the free allowance of play builds ════════════════════════════════════════

def builds(uid: int) -> dict:
    s = _snapshot(uid)
    month_left = (FREE_LIMITS["PAPER_ARM_UNLOCK_BUILDS"] - s["month"]["builds_used"]) if s["month"]["unlocked"] else 0
    return {"starter_left": max(0, FREE_LIMITS["STARTER_BUILDS"] - s["account"]["starter_used"]),
            "month_left": max(0, month_left), "unlocked": bool(s["month"]["unlocked"]),
            "enforced": enforced()}


def can_build(uid: int) -> None:
    if not enforced():
        return
    b = builds(uid)
    if b["starter_left"] or b["month_left"]:
        return
    raise Limit("You have used your free play builds. Arm any play on paper to unlock "
                f"{FREE_LIMITS['PAPER_ARM_UNLOCK_BUILDS']} more this month."
                if not b["unlocked"] else
                "You have used this month's play builds. More open on the 1st; edits to your plays stay free.")


def use_build(uid: int) -> None:
    """Spend one build: this month's unlocked ones first, so the lifetime
    starter builds last for a month with no paper arm."""
    if not enforced():
        return
    con = _db()
    month = month_of()
    with ds._users_lock:
        m = _month(con, uid, month)
        if m["unlocked"] and m["builds_used"] < FREE_LIMITS["PAPER_ARM_UNLOCK_BUILDS"]:
            con.execute("UPDATE rev_months SET builds_used=builds_used+1 WHERE user_id=? AND month=?", (uid, month))
        else:
            _account(con, uid)
            con.execute("UPDATE rev_accounts SET starter_used=starter_used+1, updated=? WHERE user_id=?",
                        (int(time.time()), uid))
        con.commit()


def paper_armed(uid: int) -> bool:
    """A play was armed on paper: unlock this month's builds. True only the
    first time in the month."""
    con = _db()
    month = month_of()
    with ds._users_lock:
        m = _month(con, uid, month)
        if m["unlocked"]:
            con.commit()
            return False
        con.execute("UPDATE rev_months SET unlocked=1 WHERE user_id=? AND month=?", (uid, month))
        con.commit()
    return True


def judge_per_day(default: int) -> int:
    return FREE_LIMITS["NEWS_JUDGE_CHECKS_PER_PLAY_PER_DAY"] if enforced() else default


# ══ what the trader sees ═════════════════════════════════════════════════════

def estimate(uid: int, segment: str, broker: str = "Zerodha") -> dict:
    """The before-arm disclosure: one trade's all-in cost, as an estimate."""
    brk = BROKERAGE_PAISE.get(broker, BROKERAGE_PAISE["Zerodha"])
    m = _snapshot(uid)["month"]
    return {"broker": broker, "brokerage_paise": brk, "brokerage_gst_paise": int(round(brk * PRICING["GST_RATE"])),
            "pivot_paise": PRICING["FNO_FEE_PAISE"] if segment == FNO else PRICING["DELIVERY_FEE_MAX_PAISE"],
            "pivot_rule": "₹10 per trade" if segment == FNO else "₹5 or 0.1% of the order, whichever is less",
            "segment": segment, "cap_paise": PRICING["MONTHLY_CAP_PAISE"], "used_paise": m["charged_paise"],
            "taxes": "STT, exchange and stamp charges depend on the order size",
            "brokers": BROKERAGE_PAISE}


def statement(uid: int, month: str | None = None) -> dict:
    month = month or month_of()
    con = _db()
    with ds._users_lock:
        rows = con.execute(
            "SELECT t.created, t.play_id, COALESCE(p.title, ''), t.segment, t.filled_paise, t.fee_paise, "
            "t.charge_paise, t.waived_paise FROM rev_trades t LEFT JOIN plays p ON p.id=t.play_id "
            "WHERE t.user_id=? AND t.month=? ORDER BY t.created DESC", (uid, month)).fetchall() \
            if _has_plays(con) else con.execute(
            "SELECT created, play_id, '', segment, filled_paise, fee_paise, charge_paise, waived_paise "
            "FROM rev_trades WHERE user_id=? AND month=? ORDER BY created DESC", (uid, month)).fetchall()
    trades = [dict(zip(("at", "play_id", "play", "segment", "filled_paise", "fee_paise", "charge_paise",
                        "waived_paise"), r)) for r in rows]
    total = sum(t["charge_paise"] for t in trades)
    return {"month": month, "trades": trades, "trade_count": len(trades),
            "fee_total_paise": sum(t["fee_paise"] for t in trades),
            "waived_paise": sum(t["waived_paise"] for t in trades), **gst_split(total)}


def _has_plays(con) -> bool:
    return bool(con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='plays'").fetchone())


def summary(uid: int) -> dict:
    s = _snapshot(uid)
    cap = PRICING["MONTHLY_CAP_PAISE"]
    return {"state": state(uid), "enforced": enforced(), "live_open": live_open(),
            "wallet_paise": s["account"]["wallet_paise"],
            "month": {"month": month_of(), "charged_paise": s["month"]["charged_paise"], "cap_paise": cap,
                      "left_paise": max(0, cap - s["month"]["charged_paise"]), "trades": s["month"]["trades"]},
            "builds": builds(uid), "topups_paise": list(PRICING["WALLET_TOPUPS_PAISE"]),
            "topup_open": topup_available(), "max_live_plays": FREE_LIMITS["MAX_ARMED_LIVE_PLAYS"],
            "statement": statement(uid)}


def api_get(uid: int, parts: list[str]) -> tuple[int, dict]:
    if parts == ["billing"]:
        return 200, summary(uid)
    if len(parts) == 2 and len(parts[1]) == 7 and parts[1][4] == "-":
        return 200, statement(uid, parts[1])
    return 404, {"error": "no billing route"}


def api_post(uid: int, parts: list[str], body: dict) -> tuple[int, dict]:
    if parts == ["billing", "topup"]:
        try:
            return topup_order(uid, int(body.get("amount_paise") or 0))
        except (TypeError, ValueError):
            return 400, {"error": "amount_paise must be a number"}
    if parts == ["billing", "topup", "verify"]:
        return topup_verify(uid, body)
    return 404, {"error": "no billing route"}

