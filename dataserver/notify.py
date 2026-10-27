"""Account, billing and activity email: which event sends which message.

The templates (mail_templates.py) and the sender (mailer.py) existed; four
messages were wired (confirm email, reset, password changed, alerts). This
module sends the rest at the moment each event happens:

    first sign-in on a new account    welcome            (at sign-up, email or Google)
    sign-in from a new browser/OS     new_sign_in
    first payment                     subscription_started
    renewal paid                      payment_received
    renewal failed                    payment_failed
    plan changed                      plan_changed       (now, or scheduled)
    subscription cancelled            subscription_cancelled
    refund processed                  refund_issued
    renewal coming up                 renewal_reminder   (3 days ahead; 7 for annual)
    a strategy filled a paper trade   paper_fill         (capped per day)

RULES
-----
* Never on the caller's thread. Hooks only enqueue. Sign-in and paper fills
  run while holding the shared user-store lock (a plain Lock, not
  re-entrant); one worker does every lookup, check and send.
* Exactly once. Webhooks are retried, and a purchase is confirmed by both the
  browser and the webhook, so each message has a key and is recorded in
  `mail_sent` before it goes out. A key already there is never sent again.
* Mail that is not configured costs nothing; the events still happen.
"""
from __future__ import annotations

import logging
import queue
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

import mail_templates as T
import mailer

log = logging.getLogger("dataserver.notify")
IST = timezone(timedelta(hours=5, minutes=30))

PAPER_FILLS_PER_DAY = 10          # automated mail stays a summary, not a feed
REMIND_DAYS = {"monthly": 3, "annual": 7}


def when(ts: Optional[float] = None) -> str:
    return datetime.fromtimestamp(ts or time.time(), IST).strftime("%d %b %Y, %H:%M IST")


def day(ts: Optional[float]) -> str:
    return datetime.fromtimestamp(int(ts), IST).strftime("%d %b %Y") if ts else ""


def rupees(paise: Optional[float]) -> str:
    """₹ with Indian digit grouping: 538800 paise -> ₹5,388.00."""
    if paise is None:
        return ""
    v = round(float(paise) / 100, 2)
    whole, frac = f"{v:.2f}".split(".")
    head, tail = whole[:-3], whole[-3:]
    head = re.sub(r"(\d)(?=(\d\d)+$)", r"\1,", head)
    return f"₹{head + ',' if head else ''}{tail}.{frac}"


def device_of(ua: str) -> str:
    """'Chrome on macOS' from a User-Agent; coarse on purpose, so a browser
    update is not a new device and an IP change (which happens daily on home
    connections) is not one either."""
    ua = ua or ""
    os_ = ("iPhone" if "iPhone" in ua else "iPad" if "iPad" in ua
           else "Android" if "Android" in ua else "Windows" if "Windows" in ua
           else "macOS" if "Mac OS X" in ua or "Macintosh" in ua
           else "Linux" if "Linux" in ua else "an unknown system")
    browser = ("Edge" if "Edg/" in ua else "Opera" if "OPR/" in ua
               else "Firefox" if "Firefox/" in ua
               else "Chrome" if "Chrome/" in ua or "CriOS/" in ua
               else "Safari" if "Safari/" in ua else "a browser")
    return f"{browser} on {os_}"


class Notifier:
    def __init__(self, db, lock, *, catalog: Optional[dict] = None,
                 send: Callable = mailer.send_later,
                 config: Optional[mailer.Config] = None):
        self.db, self.lock = db, lock
        self.catalog = catalog or {}
        self.send = send
        self.config = config or mailer.Config.from_env()
        self.origin = self.config.origin
        with lock:
            db.executescript("""
CREATE TABLE IF NOT EXISTS mail_sent (
  user_id INTEGER NOT NULL,
  kind    TEXT NOT NULL,
  ref     TEXT NOT NULL,
  sent    INTEGER NOT NULL,
  PRIMARY KEY (user_id, kind, ref));
CREATE TABLE IF NOT EXISTS known_devices (
  user_id    INTEGER NOT NULL,
  device     TEXT NOT NULL,
  first_seen INTEGER NOT NULL,
  last_seen  INTEGER NOT NULL,
  PRIMARY KEY (user_id, device));
""")
            db.commit()
        self.q: "queue.Queue[tuple]" = queue.Queue(maxsize=5000)
        threading.Thread(target=self._run, name="notify", daemon=True).start()

    # ── the queue ────────────────────────────────────────────────────────
    def _put(self, fn: Callable, *args) -> None:
        try:
            self.q.put_nowait((fn, args))
        except queue.Full:
            log.warning("notify queue full; dropped %s", fn.__name__)

    def _run(self) -> None:
        while True:
            fn, args = self.q.get()
            try:
                fn(*args)
            except Exception as exc:                      # noqa: BLE001
                log.warning("notify %s failed: %s", getattr(fn, "__name__", "job"), exc)
            finally:
                self.q.task_done()

    def drain(self) -> None:
        """Wait until every queued message has been handled (tests, shutdown)."""
        self.q.join()

    # ── helpers (worker thread only) ─────────────────────────────────────
    def _user(self, uid: int) -> Optional[tuple[str, Optional[str]]]:
        with self.lock:
            row = self.db.execute("SELECT email, name FROM users WHERE id=?", (uid,)).fetchone()
        return (row[0], row[1]) if row else None

    def _once(self, uid: int, kind: str, ref: str) -> bool:
        """Claim (uid, kind, ref). False when it was already sent."""
        with self.lock:
            cur = self.db.execute("INSERT OR IGNORE INTO mail_sent VALUES (?,?,?,?)",
                                  (uid, kind, ref, int(time.time())))
            self.db.commit()
        return cur.rowcount == 1

    def _deliver(self, uid: int, kind: str, ref: str, build: Callable) -> bool:
        who = self._user(uid)
        if not who or not mailer.valid_address(who[0]):
            return False
        if not self._once(uid, kind, ref):
            return False
        mail = build(who)
        if self.send(mail, who[0], self.config, f"pivot-{kind}-{uid}-{ref}"[:120]):
            return True
        # Not sent (mail is not configured): release the claim, so the
        # message is not marked as sent when it never was.
        with self.lock:
            self.db.execute("DELETE FROM mail_sent WHERE user_id=? AND kind=? AND ref=?",
                            (uid, kind, ref))
            self.db.commit()
        return False

    def _plan_name(self, plan: str) -> str:
        return ((self.catalog.get("plans") or {}).get(plan) or {}).get("name") or plan.title()

    def _price(self, plan: str, cycle: str) -> Optional[int]:
        p = ((self.catalog.get("plans") or {}).get(plan) or {}).get("prices") or {}
        return (p.get(cycle) or {}).get("amount")

    # ── account ──────────────────────────────────────────────────────────
    def signed_in(self, uid: int, *, how: str, new_account: bool, ua: str) -> None:
        """Every successful sign-up / sign-in. `how`: signup | login | google."""
        self._put(self._signed_in, uid, how, new_account, ua)

    def _signed_in(self, uid: int, how: str, new_account: bool, ua: str) -> None:
        device, now = device_of(ua), int(time.time())
        with self.lock:
            known = self.db.execute("SELECT COUNT(*), SUM(device=?) FROM known_devices "
                                    "WHERE user_id=?", (device, uid)).fetchone()
            self.db.execute(
                "INSERT INTO known_devices VALUES (?,?,?,?) ON CONFLICT(user_id, device) "
                "DO UPDATE SET last_seen=excluded.last_seen", (uid, device, now, now))
            self.db.commit()
        if new_account:
            # Every new account is welcomed at once, email or Google; sign-up
            # sends no confirmation link first.
            self._deliver(uid, "welcome", "1", lambda w: T.welcome(name=w[1], origin=self.origin))
            return
        # The first device an account is seen on is recorded silently, so the
        # accounts that existed before this module do not all get a "new
        # sign-in" the morning it ships.
        if new_account or not known[0] or known[1]:
            return
        self._deliver(uid, "new_sign_in", f"{device}:{now // 600}",
                      lambda w: T.new_sign_in(email=w[0], when=when(now), device=device,
                                              origin=self.origin))

    def confirmed_email(self, email: str) -> None:
        """An email sign-up just confirmed its address: welcome it now."""
        self._put(self._confirmed_email, email)

    def _confirmed_email(self, email: str) -> None:
        with self.lock:
            row = self.db.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
        if row:
            self._deliver(row[0], "welcome", "1",
                          lambda w: T.welcome(name=w[1], origin=self.origin))

    # ── billing ──────────────────────────────────────────────────────────
    def subscription_changed(self, uid: int, before: Optional[dict], after: dict,
                             entity: dict) -> None:
        """billing._apply wrote `after` over `before` (status, plan, cycle,
        period_start, period_end, grace_until)."""
        self._put(self._subscription_changed, uid, before or {}, dict(after), dict(entity or {}))

    def _subscription_changed(self, uid: int, before: dict, after: dict, entity: dict) -> None:
        sid = str(entity.get("id") or "")
        plan, cycle = after.get("plan") or "", after.get("cycle") or "monthly"
        name = self._plan_name(plan)
        amount = rupees(self._price(plan, cycle))
        ps, pe = after.get("period_start"), after.get("period_end")
        period = f"{day(ps)} to {day(pe)}" if ps and pe else ""
        ref = str(entity.get("payment_id") or entity.get("latest_invoice") or sid)
        was, now_ = before.get("status"), after.get("status")
        if now_ == "active" and was not in ("active", "past_due"):
            self._deliver(uid, "subscription_started", f"{sid}", lambda w: T.subscription_started(
                plan=name, cycle=cycle.title(), amount=amount, period=period,
                renews=day(pe), reference=ref, origin=self.origin))
        elif now_ == "active" and pe and before.get("period_end") and int(pe) > int(before["period_end"]):
            self._deliver(uid, "payment_received", f"{sid}:{pe}", lambda w: T.payment_received(
                plan=name, cycle=cycle.title(), amount=amount, period=period,
                renews=day(pe), reference=ref, origin=self.origin))
        elif now_ == "past_due" and was != "past_due":
            self._deliver(uid, "payment_failed", f"{sid}:{pe}", lambda w: T.payment_failed(
                plan=name, amount=amount, access_until=day(after.get("grace_until")) or None,
                origin=self.origin))
        if (was in ("active", "past_due") and now_ in ("active", "past_due")
                and before.get("plan") and before.get("plan") != plan):
            self._deliver(uid, "plan_changed", f"{sid}:{plan}:{cycle}:now",
                          lambda w: T.plan_changed(
                              old_plan=self._plan_name(before["plan"]), new_plan=name,
                              effective=day(time.time()), immediate=True, origin=self.origin))

    def change_scheduled(self, uid: int, old_plan: str, new_plan: str, at: Optional[int]) -> None:
        self._put(lambda: self._deliver(uid, "plan_changed", f"{new_plan}:{at}", lambda w: T.plan_changed(
            old_plan=self._plan_name(old_plan), new_plan=self._plan_name(new_plan),
            effective=day(at), immediate=False, origin=self.origin)))

    def cancelled(self, uid: int, plan: str, active_until: Optional[int]) -> None:
        self._put(lambda: self._deliver(uid, "subscription_cancelled", f"{plan}:{active_until}",
                                        lambda w: T.subscription_cancelled(
                                            plan=self._plan_name(plan),
                                            access_until=day(active_until) or "the end of this period",
                                            origin=self.origin)))

    def refunded(self, uid: int, refund_id: str, amount_paise: Optional[int],
                 payment_id: str, paid_on: Optional[int]) -> None:
        original = f"{payment_id}{' on ' + day(paid_on) if paid_on else ''}"
        self._put(lambda: self._deliver(uid, "refund_issued", refund_id, lambda w: T.refund_issued(
            amount=rupees(amount_paise), reference=refund_id, original=original,
            origin=self.origin)))

    def remind_renewals(self) -> int:
        """Queue a reminder for every active, renewing subscription inside its
        window. Safe to call as often as you like: one per period."""
        now = int(time.time())
        with self.lock:
            rows = self.db.execute(
                "SELECT user_id, plan, cycle, period_end, provider_sub_id FROM subscriptions "
                "WHERE status='active' AND cancel_at_period_end=0 AND period_end>? "
                "AND provider='razorpay'", (now,)).fetchall()
        n = 0
        for uid, plan, cycle, pe, sid in rows:
            if int(pe) - now > REMIND_DAYS.get(cycle, 3) * 86400:
                continue
            n += 1
            self._put(lambda u=uid, p=plan, c=cycle, e=pe, s=sid: self._deliver(
                u, "renewal_reminder", f"{s}:{e}", lambda w: T.renewal_reminder(
                    plan=self._plan_name(p), amount=rupees(self._price(p, c)), date=day(e),
                    cycle=c, origin=self.origin)))
        return n

    def start_reminders(self, every_s: int = 3600) -> None:
        def loop():
            while True:
                try:
                    self.remind_renewals()
                except Exception as exc:                  # noqa: BLE001
                    log.warning("renewal reminders failed: %s", exc)
                time.sleep(every_s)
        threading.Thread(target=loop, name="notify-renewals", daemon=True).start()

    # ── activity ─────────────────────────────────────────────────────────
    def paper_filled(self, uid: int, fill: dict, strategy_id: Optional[int]) -> None:
        """Only fills a strategy placed: an order you placed yourself you saw."""
        if strategy_id:
            self._put(self._paper_filled, uid, dict(fill), int(strategy_id))

    def _paper_filled(self, uid: int, fill: dict, strategy_id: int) -> None:
        today = datetime.now(IST).strftime("%Y-%m-%d")
        with self.lock:
            sent = self.db.execute("SELECT COUNT(*) FROM mail_sent WHERE user_id=? AND "
                                   "kind='paper_fill' AND ref LIKE ?", (uid, f"{today}:%")).fetchone()[0]
            try:
                row = self.db.execute("SELECT name FROM strategies WHERE id=?",
                                      (strategy_id,)).fetchone()
            except Exception:                             # noqa: BLE001
                row = None
        if sent >= PAPER_FILLS_PER_DAY:
            return
        name = row[0] if row else f"Strategy {strategy_id}"
        self._deliver(uid, "paper_fill", f"{today}:{fill.get('id')}", lambda w: T.paper_fill(
            strategy=name, symbol=str(fill.get("symbol")), side=str(fill.get("side")),
            quantity=str(fill.get("quantity")), price=f"₹{fill.get('fill_price')}",
            when=when(), origin=self.origin))
