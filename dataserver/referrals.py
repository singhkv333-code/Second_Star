"""Gated referral ledger. Identity is supplied by the dataserver, never stored here.

This database is separate from users.db. All money is integer paise and
enters through a signature-verified Razorpay webhook (billing.py), never a form.
"""
from __future__ import annotations

import os
import json
import secrets
import sqlite3
import threading
import time
from pathlib import Path


SCHEMA = """
CREATE TABLE IF NOT EXISTS links (user_id INTEGER PRIMARY KEY, code TEXT NOT NULL UNIQUE,
 created INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS visits (token TEXT PRIMARY KEY, referrer_id INTEGER NOT NULL,
 visited INTEGER NOT NULL, consumed_by INTEGER);
CREATE INDEX IF NOT EXISTS visits_referrer ON visits(referrer_id, visited);
CREATE TABLE IF NOT EXISTS signups (user_id INTEGER PRIMARY KEY, referrer_id INTEGER NOT NULL,
 visit_token TEXT NOT NULL UNIQUE, created INTEGER NOT NULL, status TEXT NOT NULL,
 reason TEXT NOT NULL DEFAULT '', terms_version TEXT NOT NULL DEFAULT '',
 rate_bps INTEGER NOT NULL DEFAULT 0, duration_days INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS signups_referrer ON signups(referrer_id);
CREATE TABLE IF NOT EXISTS payment_events (event_id TEXT PRIMARY KEY, kind TEXT NOT NULL,
 payment_id TEXT NOT NULL, payload TEXT NOT NULL, received INTEGER NOT NULL,
 result TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS commissions (payment_id TEXT PRIMARY KEY,
 subscriber_id INTEGER NOT NULL, referrer_id INTEGER NOT NULL,
 customer_key TEXT,
 gross_paise INTEGER NOT NULL, rate_bps INTEGER NOT NULL,
 earned_paise INTEGER NOT NULL, reversed_paise INTEGER NOT NULL DEFAULT 0,
 status TEXT NOT NULL, created INTEGER NOT NULL, approved_at INTEGER,
 approved_by TEXT, payout_id INTEGER);
CREATE TABLE IF NOT EXISTS reversals (provider_refund_id TEXT PRIMARY KEY,
 payment_id TEXT NOT NULL, amount_paise INTEGER NOT NULL, created INTEGER NOT NULL,
 FOREIGN KEY(payment_id) REFERENCES commissions(payment_id));
CREATE TABLE IF NOT EXISTS disputes (id TEXT PRIMARY KEY, payment_id TEXT NOT NULL,
 amount_paise INTEGER NOT NULL, state TEXT NOT NULL, prior_status TEXT NOT NULL,
 created INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS payouts (id INTEGER PRIMARY KEY AUTOINCREMENT,
 referrer_id INTEGER NOT NULL, amount_paise INTEGER NOT NULL,
 reference TEXT NOT NULL UNIQUE, recorded_by TEXT NOT NULL, created INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS audit (id INTEGER PRIMARY KEY AUTOINCREMENT,
 actor TEXT NOT NULL, action TEXT NOT NULL, subject TEXT NOT NULL,
 detail TEXT NOT NULL DEFAULT '', created INTEGER NOT NULL);
"""


class Referrals:
    def __init__(self, path: str | Path):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA busy_timeout=10000")
        self.db.executescript(SCHEMA)
        if "customer_key" not in {r[1] for r in self.db.execute("PRAGMA table_info(commissions)")}:
            self.db.execute("ALTER TABLE commissions ADD COLUMN customer_key TEXT")
            self.db.commit()
        signup_columns = {r[1] for r in self.db.execute("PRAGMA table_info(signups)")}
        for name, declaration in (("terms_version", "TEXT NOT NULL DEFAULT ''"),
                                  ("rate_bps", "INTEGER NOT NULL DEFAULT 0"),
                                  ("duration_days", "INTEGER NOT NULL DEFAULT 0")):
            if name not in signup_columns:
                self.db.execute(f"ALTER TABLE signups ADD COLUMN {name} {declaration}")
        self.db.commit()
        self.lock = threading.RLock()

    def link(self, user_id: int) -> str:
        with self.lock, self.db:
            row = self.db.execute("SELECT code FROM links WHERE user_id=?", (user_id,)).fetchone()
            if row:
                return row[0]
            code = secrets.token_urlsafe(12)
            self.db.execute("INSERT INTO links VALUES (?,?,?)", (user_id, code, int(time.time())))
            return code

    def visit(self, code: str, now: int | None = None) -> str | None:
        now = int(time.time()) if now is None else now
        with self.lock, self.db:
            row = self.db.execute("SELECT user_id FROM links WHERE code=?", (code,)).fetchone()
            if not row:
                return None
            token = secrets.token_urlsafe(24)
            self.db.execute("INSERT INTO visits VALUES (?,?,?,NULL)", (token, row[0], now))
            return token

    def signup(self, user_id: int, token: str, *, days: int, touch: str,
               now: int | None = None, rate_bps: int = 0,
               duration_days: int = 0, terms_version: str = "") -> bool:
        """Bind once. Caller invokes this only after a genuinely new account insert.

        No email or IP is copied here. Duplicate account creation is already
        rejected by the dataserver's unique email constraint; this table is also unique
        on user_id and visit token. The browser's newest visit wins.
        """
        now = int(time.time()) if now is None else now
        if days <= 0 or touch != "last":
            raise ValueError("attribution policy is not configured")
        with self.lock, self.db:
            if self.db.execute("SELECT 1 FROM signups WHERE user_id=?", (user_id,)).fetchone():
                return False
            visit = self.db.execute("SELECT referrer_id, visited, consumed_by FROM visits WHERE token=?", (token,)).fetchone()
            if not visit or visit[2] is not None or visit[0] == user_id or not now - days * 86400 <= visit[1] <= now:
                return False
            self.db.execute("INSERT INTO signups(user_id,referrer_id,visit_token,created,status,reason,terms_version,rate_bps,duration_days) VALUES (?,?,?,?,?,?,?,?,?)",
                            (user_id, visit[0], token, now, "pending", "", terms_version, rate_bps, duration_days))
            self.db.execute("UPDATE visits SET consumed_by=? WHERE token=?", (user_id, token))
            self._audit("system", "signup", str(user_id), token)
            return True

    def _audit(self, actor: str, action: str, subject: str, detail: str = "") -> None:
        self.db.execute("INSERT INTO audit(actor,action,subject,detail,created) VALUES (?,?,?,?,?)",
                        (actor, action, subject, detail, int(time.time())))

    def payment(self, event_id: str, payment_id: str, subscriber_id: int,
                amount_paise: int, rate_bps: int, duration_days: int, payload: str = "",
                customer_key: str = "", paid_at: int | None = None,
                actor: str = "razorpay", kind: str = "captured") -> str:
        if not event_id or not payment_id or amount_paise <= 0 or not 0 < rate_bps <= 10000 or duration_days <= 0:
            raise ValueError("invalid verified payment")
        with self.lock, self.db:
            if self.db.execute("SELECT 1 FROM payment_events WHERE event_id=?", (event_id,)).fetchone():
                return "duplicate"
            signup = self.db.execute("SELECT referrer_id,status,created,rate_bps,duration_days FROM signups WHERE user_id=?", (subscriber_id,)).fetchone()
            result = "no eligible signup"
            effective_rate = signup[3] if signup and signup[3] else rate_bps
            effective_duration = signup[4] if signup and signup[4] else duration_days
            payment_time = int(time.time()) if paid_at is None else paid_at
            if signup and signup[1] == "pending" and signup[2] <= payment_time <= signup[2] + effective_duration*86400:
                earned = amount_paise * effective_rate // 10000
                duplicate_customer = bool(customer_key and self.db.execute("SELECT 1 FROM commissions WHERE customer_key=? AND subscriber_id<>? LIMIT 1", (customer_key, subscriber_id)).fetchone())
                status = "exception" if duplicate_customer else "pending"
                cur = self.db.execute("INSERT OR IGNORE INTO commissions(payment_id,subscriber_id,referrer_id,customer_key,gross_paise,rate_bps,earned_paise,status,created) VALUES (?,?,?,?,?,?,?,?,?)",
                                      (payment_id, subscriber_id, signup[0], customer_key or None, amount_paise, effective_rate, earned, status, payment_time))
                result = ("duplicate provider customer: review" if duplicate_customer else "pending") if cur.rowcount else "payment already recorded"
            self.db.execute("INSERT INTO payment_events VALUES (?,?,?,?,?,?)",
                            (event_id, kind, payment_id, payload, int(time.time()), result))
            self._audit(actor, kind, payment_id, result)
            if result in ("pending", "duplicate provider customer: review"):
                missed = self.db.execute("SELECT event_id,payload FROM payment_events WHERE payment_id=? AND kind='reversal' AND result='unmatched reversal'", (payment_id,)).fetchall()
                for item in missed:
                    data = json.loads(item[1])
                    replay = self.reverse(item[0]+":replay", data["refund_id"], payment_id, data["amount_paise"])
                    self.db.execute("UPDATE payment_events SET result=? WHERE event_id=?", ("replayed: "+replay,item[0]))
                disputed = self.db.execute("SELECT event_id,payload FROM payment_events WHERE payment_id=? AND kind='dispute.lost' AND result='unmatched dispute'", (payment_id,)).fetchall()
                for item in disputed:
                    data = json.loads(item[1])
                    replay = self.dispute(item[0]+":replay", data["dispute_id"], payment_id, data["amount_paise"], "lost")
                    self.db.execute("UPDATE payment_events SET result=? WHERE event_id=?", ("replayed: "+replay,item[0]))
            return result

    def reverse(self, event_id: str, refund_id: str, payment_id: str,
                amount_paise: int, payload: str = "") -> str:
        if not event_id or not refund_id or not payment_id or amount_paise <= 0:
            raise ValueError("invalid verified reversal")
        with self.lock, self.db:
            if self.db.execute("SELECT 1 FROM payment_events WHERE event_id=?", (event_id,)).fetchone():
                return "duplicate"
            row = self.db.execute("SELECT gross_paise,rate_bps,earned_paise,reversed_paise,status FROM commissions WHERE payment_id=?", (payment_id,)).fetchone()
            result = "unmatched reversal"
            if row and not self.db.execute("SELECT 1 FROM reversals WHERE provider_refund_id=?", (refund_id,)).fetchone():
                prior = self.db.execute("SELECT COALESCE(SUM(amount_paise),0) FROM reversals WHERE payment_id=?", (payment_id,)).fetchone()[0]
                applied = min(amount_paise, max(0, row[0]-prior))
                self.db.execute("INSERT INTO reversals VALUES (?,?,?,?)", (refund_id, payment_id, applied, int(time.time())))
                total = min(row[2], (prior+applied)*row[1]//10000)
                status = "exception" if row[4] == "paid" else ("reversed" if total == row[2] else row[4])
                self.db.execute("UPDATE commissions SET reversed_paise=?,status=? WHERE payment_id=?", (total,status,payment_id))
                result = status
            self.db.execute("INSERT INTO payment_events VALUES (?,?,?,?,?,?)", (event_id,"reversal",payment_id,json.dumps({"refund_id":refund_id,"amount_paise":amount_paise}),int(time.time()),result))
            self._audit("razorpay", "reversal", payment_id, f"{refund_id}: {result}")
            return result

    def dispute(self, event_id: str, dispute_id: str, payment_id: str,
                amount_paise: int, state: str, payload: str = "") -> str:
        if state not in ("created", "won", "lost") or not dispute_id or not payment_id or amount_paise <= 0:
            raise ValueError("invalid dispute")
        with self.lock, self.db:
            if self.db.execute("SELECT 1 FROM payment_events WHERE event_id=?", (event_id,)).fetchone():
                return "duplicate"
            commission = self.db.execute("SELECT status FROM commissions WHERE payment_id=?", (payment_id,)).fetchone()
            prior = self.db.execute("SELECT state,prior_status FROM disputes WHERE id=?", (dispute_id,)).fetchone()
            result = "unmatched dispute"
            if state == "created" and commission and not prior:
                self.db.execute("INSERT INTO disputes VALUES (?,?,?,?,?,?)", (dispute_id,payment_id,amount_paise,state,commission[0],int(time.time())))
                if commission[0] != "paid":
                    self.db.execute("UPDATE commissions SET status='exception' WHERE payment_id=?", (payment_id,))
                result = "held for dispute review"
            elif state == "won" and prior and prior[0] != "lost":
                self.db.execute("UPDATE disputes SET state='won' WHERE id=?", (dispute_id,))
                if commission and commission[0] == "exception":
                    self.db.execute("UPDATE commissions SET status=? WHERE payment_id=?", (prior[1],payment_id))
                result = "dispute won"
            elif state == "lost" and commission and (not prior or prior[0] != "lost"):
                if not prior:
                    self.db.execute("INSERT INTO disputes VALUES (?,?,?,?,?,?)", (dispute_id,payment_id,amount_paise,state,commission[0],int(time.time())))
                else:
                    self.db.execute("UPDATE disputes SET state='lost' WHERE id=?", (dispute_id,))
                result = self.reverse(event_id + ":reversal", "dispute:" + dispute_id,
                                      payment_id, amount_paise, payload)
            self.db.execute("INSERT INTO payment_events VALUES (?,?,?,?,?,?)", (event_id,"dispute."+state,payment_id,json.dumps({"dispute_id":dispute_id,"amount_paise":amount_paise}),int(time.time()),result))
            self._audit("razorpay","dispute."+state,payment_id,dispute_id+": "+result)
            return result

    def conversion(self, subscriber_id: int, plan: str, cycle: str, period_start: int,
                   price_paise: int, *, rate_bps: int, duration_days: int,
                   gst_inclusive: bool = True) -> str:
        """A referred account's paid plan period, as Pivot's own billing records it.

        Commission follows conversion, not a payment provider: when an account
        that signed up through a link holds an active paid plan for a new
        billing period, that period earns the referrer `rate_bps` of the plan's
        catalog price. Catalog prices include 18% GST and the terms pay on the
        price excluding it, so the base is price x 100/118. One commission per
        account per period: the key is the account and the period start, so a
        repeated or late subscription update cannot pay twice.
        """
        key = f"plan:{int(subscriber_id)}:{int(period_start)}"
        net = int(price_paise) * 100 // 118 if gst_inclusive else int(price_paise)
        return self.payment(key, key, subscriber_id, net, rate_bps, duration_days,
                            payload=json.dumps({"plan": plan, "cycle": cycle,
                                                "price_paise": int(price_paise),
                                                "net_paise": net}),
                            paid_at=int(period_start), actor="billing", kind="conversion")

    def refund(self, subscriber_id: int, refund_id: str, amount_paise: int,
               gst_inclusive: bool = True) -> str:
        """A refund Pivot's billing recorded reverses the account's latest
        commission in proportion (the refund includes GST; the base does not)."""
        with self.lock:
            row = self.db.execute("SELECT payment_id FROM commissions WHERE subscriber_id=? "
                                  "ORDER BY created DESC LIMIT 1", (subscriber_id,)).fetchone()
        if not row:
            return "no commission for this account"
        net = int(amount_paise) * 100 // 118 if gst_inclusive else int(amount_paise)
        if net <= 0:
            return "nothing to reverse"
        return self.reverse(f"refund:{refund_id}", refund_id, row[0], net)

    def attributions(self, limit: int = 200) -> list[dict]:
        """Every referred signup: whose link, which code, when, and whether it
        has converted to a paid plan and what it has earned."""
        with self.lock:
            return [dict(r) for r in self.db.execute(
                "SELECT s.user_id AS subscriber_id, s.referrer_id, l.code, s.created AS signed_up, "
                "s.status, s.terms_version, "
                "(SELECT COUNT(*) FROM commissions c WHERE c.subscriber_id=s.user_id) AS paid_periods, "
                "(SELECT COALESCE(SUM(earned_paise-reversed_paise),0) FROM commissions c "
                " WHERE c.subscriber_id=s.user_id) AS earned_paise "
                "FROM signups s LEFT JOIN links l ON l.user_id=s.referrer_id "
                "ORDER BY s.created DESC LIMIT ?", (int(limit),))]

    def summary(self, user_id: int) -> dict:
        with self.lock:
            signups = self.db.execute("SELECT COUNT(*) FROM signups WHERE referrer_id=?", (user_id,)).fetchone()[0]
            paid = self.db.execute("SELECT COUNT(DISTINCT subscriber_id) FROM commissions WHERE referrer_id=? AND status IN ('pending','approved','paid') AND earned_paise>reversed_paise", (user_id,)).fetchone()[0]
            totals = {r[0]: r[1] for r in self.db.execute("SELECT status,SUM(earned_paise-reversed_paise) FROM commissions WHERE referrer_id=? GROUP BY status", (user_id,))}
            history = [dict(r) for r in self.db.execute("SELECT amount_paise,reference,created FROM payouts WHERE referrer_id=? ORDER BY created DESC LIMIT 50", (user_id,))]
            return {"code": self.link(user_id), "signups": signups, "paid_customers": paid,
                    "totals_paise": {s: totals.get(s,0) for s in ("pending","approved","paid","exception")},
                    "payouts": history}

    def exceptions(self) -> list[dict]:
        with self.lock:
            return [dict(r) for r in self.db.execute("SELECT payment_id,subscriber_id,referrer_id,earned_paise,reversed_paise,status FROM commissions WHERE status IN ('pending','approved','exception') ORDER BY created DESC LIMIT 100")]

    def ledger_rows(self) -> list[dict]:
        with self.lock:
            return [dict(r) for r in self.db.execute("SELECT c.payment_id,c.gross_paise,c.rate_bps,c.earned_paise,c.reversed_paise,c.status, COALESCE((SELECT SUM(amount_paise) FROM disputes d WHERE d.payment_id=c.payment_id AND d.state='lost'),0) AS dispute_lost_paise FROM commissions c ORDER BY c.created DESC LIMIT 100")]

    def approve(self, payment_id: str, actor: str) -> bool:
        with self.lock, self.db:
            row = self.db.execute("SELECT status FROM commissions WHERE payment_id=?", (payment_id,)).fetchone()
            if not row or row[0] != "pending":
                return False
            self.db.execute("UPDATE commissions SET status='approved',approved_at=?,approved_by=? WHERE payment_id=?", (int(time.time()),actor,payment_id))
            self._audit(actor,"approve",payment_id)
            return True

    def resolve(self, payment_id: str, actor: str, decision: str, reason: str) -> bool:
        if decision not in ("clear", "reject") or len(reason.strip()) < 8:
            raise ValueError("decision and review reason required")
        with self.lock, self.db:
            row = self.db.execute("SELECT status,payout_id,earned_paise-reversed_paise FROM commissions WHERE payment_id=?", (payment_id,)).fetchone()
            if not row or row[0] != "exception" or row[1] is not None or (decision == "clear" and row[2] <= 0):
                return False
            self.db.execute("UPDATE commissions SET status=? WHERE payment_id=?",
                            ("pending" if decision == "clear" else "rejected", payment_id))
            self._audit(actor,"resolve:"+decision,payment_id,reason.strip()[:500])
            return True

    def payout(self, user_id: int, payment_ids: list[str], reference: str, actor: str,
               minimum_paise: int) -> int:
        if not payment_ids or len(set(payment_ids)) != len(payment_ids) or not reference:
            raise ValueError("payment ids and payout reference required")
        with self.lock, self.db:
            rows = self.db.execute(f"SELECT payment_id,earned_paise-reversed_paise FROM commissions WHERE referrer_id=? AND status='approved' AND payment_id IN ({','.join('?' for _ in payment_ids)})", (user_id,*payment_ids)).fetchall()
            if len(rows) != len(payment_ids):
                raise ValueError("all commissions must be approved for this referrer")
            amount = sum(r[1] for r in rows)
            if amount <= 0 or amount < minimum_paise:
                raise ValueError("payout minimum not met")
            cur = self.db.execute("INSERT INTO payouts(referrer_id,amount_paise,reference,recorded_by,created) VALUES (?,?,?,?,?)", (user_id,amount,reference,actor,int(time.time())))
            self.db.executemany("UPDATE commissions SET status='paid',payout_id=? WHERE payment_id=?", [(cur.lastrowid,p) for p in payment_ids])
            self._audit(actor,"payout",str(cur.lastrowid),reference)
            return amount


def configured() -> bool:
    if os.getenv("PIVOT_REFERRALS_ENABLED") != "1" or not all(os.getenv(k) for k in (
        "PIVOT_REFERRAL_RATE_BPS", "PIVOT_REFERRAL_ATTRIBUTION_DAYS",
        "PIVOT_REFERRAL_TOUCH", "PIVOT_REFERRAL_PAYOUT_MIN_PAISE",
        "PIVOT_REFERRAL_COMMISSION_DAYS", "PIVOT_REFERRAL_TERMS_VERSION",
        "PIVOT_REFERRAL_ELIGIBILITY",
        "PIVOT_REFERRAL_REVENUE_BASIS", "PIVOT_REFERRAL_REFUND_POLICY",
        "PIVOT_REFERRAL_PAYOUT_SCHEDULE", "PIVOT_REFERRAL_PAYOUT_METHOD",
        "PIVOT_REFERRAL_TERMS_CHANGE_POLICY", "PIVOT_REFERRAL_EFFECTIVE_DATE")):
        return False
    try:
        return (os.environ["PIVOT_REFERRAL_TOUCH"] == "last"
                and 0 < int(os.environ["PIVOT_REFERRAL_RATE_BPS"]) <= 10000
                and int(os.environ["PIVOT_REFERRAL_ATTRIBUTION_DAYS"]) > 0
                and int(os.environ["PIVOT_REFERRAL_COMMISSION_DAYS"]) > 0
                and int(os.environ["PIVOT_REFERRAL_PAYOUT_MIN_PAISE"]) >= 0)
    except ValueError:
        return False


def public_terms() -> dict:
    if not configured():
        raise ValueError("referrals not configured")
    return {
        "version": os.environ["PIVOT_REFERRAL_TERMS_VERSION"],
        "effective_date": os.environ["PIVOT_REFERRAL_EFFECTIVE_DATE"],
        "rate_bps": int(os.environ["PIVOT_REFERRAL_RATE_BPS"]),
        "duration_days": int(os.environ["PIVOT_REFERRAL_COMMISSION_DAYS"]),
        "attribution_days": int(os.environ["PIVOT_REFERRAL_ATTRIBUTION_DAYS"]),
        "touch": "last",
        "eligibility": os.environ["PIVOT_REFERRAL_ELIGIBILITY"],
        "revenue_basis": os.environ["PIVOT_REFERRAL_REVENUE_BASIS"],
        "refund_policy": os.environ["PIVOT_REFERRAL_REFUND_POLICY"],
        "payout_schedule": os.environ["PIVOT_REFERRAL_PAYOUT_SCHEDULE"],
        "payout_method": os.environ["PIVOT_REFERRAL_PAYOUT_METHOD"],
        "payout_min_paise": int(os.environ["PIVOT_REFERRAL_PAYOUT_MIN_PAISE"]),
        "changes": os.environ["PIVOT_REFERRAL_TERMS_CHANGE_POLICY"],
    }


def open_store(users_path: Path) -> Referrals:
    path = Path(os.getenv("PIVOT_REFERRALS_DB") or users_path.with_name("pivot_referrals.db"))
    return Referrals(path)
