"""Charto's paper book — a simulated account that actually keeps the shares.

WHAT THIS IS
------------
Execution mode could build a strategy and test it, and then the conversation
ended and the strategy ceased to exist. This module is the other half: an
account with cash, positions that persist, orders that rest until their price
arrives, fills with their real charges, and a NAV curve you can look at
tomorrow. `strategies.py` arms a rule against the tick; when the rule says yes
it calls in here, and this is where the shares end up.

Ported from Pivot's `backend/paper/` (fills, positions, valuation, portfolio,
snapshots) — the same avg-cost engine, the same money discipline, the same
read shapes, re-expressed for Charto's stdlib + SQLite server. The READ shapes
are copied field-for-field on purpose: `charto/web/lib/api.ts` already declares
`PaperSummary`, `PaperHolding`, `PaperOpenOrder`, `PaperFillRow` and
`PaperNavPoint`, and the dashboard components are byte-identical to Pivot's. A
payload that matches means the page works without touching a component.

MONEY
-----
Every figure is a Decimal quantized to 4 dp (`to_money`) and stored in a REAL
column. That is exact, not a compromise: a 4-dp decimal below ~Rs 900 billion
round-trips through float64 without loss, and `to_money` goes via `str()` on
the way in and `Decimal(str(...))` on the way out — so a fill/settle/close
chain reconciles by replay rather than drifting by paise. Cast to float only
at the JSON edge.

WHAT IT REFUSES
---------------
**Long-only.** A SELL past the held quantity is rejected, not turned into a
short. India bans naked short delivery and this book is a delivery book; a
strategy that wants to be short is a strategy this surface will not run, and
saying so is cheaper than modelling margin nobody asked for.

**Nothing here reaches a broker.** There is no order id that means anything
outside this database, and no code path that could acquire one. That is the
constitution's line (§8, register-not-execute) expressed as an absence rather
than a flag someone could flip.

THE TICK
--------
`on_price` is the seam, called from the live tick with the same discipline
`alerts.on_bar` follows: it must not block, must not raise, and must not do
anything a candle would notice. It stamps marks and fills resting orders whose
price has arrived. Everything slow — a bar read, a mark resolution — happens
before the write lock is taken.
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from os import environ
from typing import Optional

# Safe at import: dataserver aliases itself into sys.modules before it imports
# this module — the same requirement alerts.py and journal.py have.
import dataserver as ds
import trading_costs

log = logging.getLogger("charto.paper")

IST = timezone(timedelta(hours=5, minutes=30))

# The seed. A beta budget, overridable per deployment without touching a
# constant that tests read.
SEED_CAPITAL = Decimal(environ.get("CHARTO_PAPER_SEED") or "150000")

CENTS = Decimal("0.0001")

# Resting order types. MARKET fills the instant it is placed; everything else
# waits for a price. GTT is a resting trigger with no expiry, which is exactly
# what a "buy it if it ever gets to X" strategy leg is.
_RESTING_TYPES = ("LIMIT", "SL", "SL-M", "GTT")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS paper_accounts (
  id       INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id  INTEGER NOT NULL UNIQUE REFERENCES users(id),
  mode     TEXT NOT NULL DEFAULT 'paper',
  starting_capital REAL NOT NULL,
  cash_available   REAL NOT NULL,
  cash_settled     REAL NOT NULL,
  cash_reserved    REAL NOT NULL DEFAULT 0,
  created  INTEGER NOT NULL,
  updated  INTEGER NOT NULL);

CREATE TABLE IF NOT EXISTS paper_positions (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  account_id INTEGER NOT NULL REFERENCES paper_accounts(id),
  user_id    INTEGER NOT NULL,
  symbol     TEXT NOT NULL,
  quantity   REAL NOT NULL DEFAULT 0,
  avg_cost   REAL NOT NULL DEFAULT 0,
  realized_pnl REAL NOT NULL DEFAULT 0,
  last_price REAL,
  prev_close REAL,
  last_mark_at INTEGER,
  stale      INTEGER NOT NULL DEFAULT 0,
  opened_at  INTEGER,
  UNIQUE(account_id, symbol));
CREATE INDEX IF NOT EXISTS paper_pos_sym ON paper_positions(symbol, quantity);

CREATE TABLE IF NOT EXISTS paper_orders (
  id         TEXT PRIMARY KEY,
  account_id INTEGER NOT NULL REFERENCES paper_accounts(id),
  user_id    INTEGER NOT NULL,
  symbol     TEXT NOT NULL,
  side       TEXT NOT NULL CHECK(side IN ('BUY','SELL')),
  order_type TEXT NOT NULL,
  quantity   REAL NOT NULL,
  limit_price   REAL,
  trigger_price REAL,
  reserved_cash REAL NOT NULL DEFAULT 0,
  status     TEXT NOT NULL,
  reject_reason TEXT,
  source     TEXT,
  origin_kind TEXT,
  strategy_id INTEGER,
  created    INTEGER NOT NULL,
  updated    INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS paper_ord_open ON paper_orders(status, symbol);
CREATE INDEX IF NOT EXISTS paper_ord_acct ON paper_orders(account_id, status);

CREATE TABLE IF NOT EXISTS paper_fills (
  id         TEXT PRIMARY KEY,
  order_id   TEXT NOT NULL,
  account_id INTEGER NOT NULL REFERENCES paper_accounts(id),
  user_id    INTEGER NOT NULL,
  symbol     TEXT NOT NULL,
  side       TEXT NOT NULL,
  quantity   REAL NOT NULL,
  fill_price REAL NOT NULL,
  gross_value REAL NOT NULL,
  charges    REAL NOT NULL,
  net_cashflow REAL NOT NULL,
  realized_pnl REAL,
  strategy_id INTEGER,
  filled_at  INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS paper_fill_acct ON paper_fills(account_id, filled_at DESC);

CREATE TABLE IF NOT EXISTS paper_ledger (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  account_id INTEGER NOT NULL REFERENCES paper_accounts(id),
  kind       TEXT NOT NULL,
  amount     REAL NOT NULL,
  balance_after REAL NOT NULL,
  ref_id     TEXT,
  ts         INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS paper_ledger_acct ON paper_ledger(account_id, ts DESC);

CREATE TABLE IF NOT EXISTS paper_nav (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  account_id INTEGER NOT NULL REFERENCES paper_accounts(id),
  as_of_date TEXT NOT NULL,
  nav        REAL NOT NULL,
  cash_available REAL NOT NULL,
  positions_mv   REAL NOT NULL,
  realized_pnl_cum REAL NOT NULL,
  unrealized_pnl   REAL NOT NULL,
  nifty_close REAL,
  ts         INTEGER NOT NULL,
  UNIQUE(account_id, as_of_date));
"""


# ── money ────────────────────────────────────────────────────────────

def to_money(x) -> Decimal:
    """Any numeric to a 4-dp Decimal. Floats route through `str()` so binary
    noise is never inherited into the ledger."""
    d = x if isinstance(x, Decimal) else Decimal(str(x if x is not None else 0))
    return d.quantize(CENTS, rounding=ROUND_HALF_UP)


def f(x) -> float:
    """The JSON edge, and only the JSON edge."""
    return float(to_money(x))


def _qty(x) -> Decimal:
    """Quantities are whole shares here. Indian delivery equity has no
    fractional unit, and truncating toward zero means a fill can never claim
    more than the cash bought."""
    try:
        return Decimal(int(Decimal(str(x or 0))))
    except Exception:                                       # noqa: BLE001
        return Decimal(0)


def _qty_out(q):
    n = float(q or 0)
    return int(n) if n == int(n) else n


def _iso(ts) -> Optional[str]:
    if not ts:
        return None
    return datetime.fromtimestamp(int(ts), IST).isoformat()


def _today() -> str:
    return datetime.now(IST).date().isoformat()


# ── the store ────────────────────────────────────────────────────────

def _db():
    return ds._users


_ready = False
_init_lock = threading.Lock()


def init_db() -> None:
    global _ready
    with _init_lock:
        if _ready:
            return
        with ds._users_lock:
            _db().executescript(_SCHEMA)
            _db().commit()
        _ready = True


def _account_row(uid: int):
    return _db().execute(
        "SELECT id, mode, starting_capital, cash_available, cash_settled, "
        "cash_reserved FROM paper_accounts WHERE user_id=?", (int(uid),)
    ).fetchone()


def get_or_create_account(uid: int) -> tuple:
    """The user's book, opened on first use with the seed balance.

    Opening it lazily is the point: a signed-in user who has never run a
    strategy has no account, and `/paper/summary` says `{"exists": false}`
    rather than inventing a portfolio of nothing.
    """
    init_db()
    with ds._users_lock:
        row = _account_row(uid)
        if row is not None:
            return row
        now = int(time.time())
        seed = f(SEED_CAPITAL)
        _db().execute(
            "INSERT INTO paper_accounts (user_id, mode, starting_capital, "
            "cash_available, cash_settled, cash_reserved, created, updated) "
            "VALUES (?,'paper',?,?,?,0,?,?)", (int(uid), seed, seed, seed, now, now))
        _db().execute(
            "INSERT INTO paper_ledger (account_id, kind, amount, balance_after,"
            " ref_id, ts) VALUES ((SELECT id FROM paper_accounts WHERE user_id=?),"
            " 'seed', ?, ?, NULL, ?)", (int(uid), seed, seed, now))
        _db().commit()
        return _account_row(uid)


def account_of(uid: int):
    """Read-only lookup — never opens a book."""
    init_db()
    with ds._users_lock:
        return _account_row(uid)


# ── marks ────────────────────────────────────────────────────────────
#
# One resolver, so the holdings table, the summary and the strategy watcher
# can never disagree about what a symbol is worth. Charto's own bars are the
# only source: the forming minute when a feed is live, the last stored close
# otherwise. There is no network call in here and there must not be one — this
# runs inside a portfolio read and, on the tick path, inside a candle.

_mark_cache: dict[str, tuple[float, Optional[Decimal]]] = {}
_mark_ttl = 5.0
_mark_lock = threading.Lock()


def mark_price(symbol: str) -> Optional[Decimal]:
    sym = (symbol or "").upper()
    if not sym:
        return None
    now = time.monotonic()
    with _mark_lock:
        hit = _mark_cache.get(sym)
        if hit and now - hit[0] < _mark_ttl:
            return hit[1]
    px: Optional[Decimal] = None
    try:
        live = ds._live_view(sym)
        if live and live.get("c"):
            px = to_money(live["c"])
    except Exception:                                       # noqa: BLE001
        px = None
    if px is None:
        try:
            bars = (ds.get_bars(sym, "1d", None, 2) or {}).get("bars") or []
            if bars and bars[-1].get("c") is not None:
                px = to_money(bars[-1]["c"])
        except Exception:                                   # noqa: BLE001
            px = None
    if px is not None and px <= 0:
        px = None
    with _mark_lock:
        _mark_cache[sym] = (now, px)
    return px


def prev_close(symbol: str) -> Optional[Decimal]:
    """Yesterday's close, for day P&L. Distinct from the mark: the second-last
    DAILY bar, whatever interval anything else is looking at."""
    try:
        bars = (ds.get_bars((symbol or "").upper(), "1d", None, 3)
                or {}).get("bars") or []
        if len(bars) >= 2 and bars[-2].get("c") is not None:
            return to_money(bars[-2]["c"])
    except Exception:                                       # noqa: BLE001
        pass
    return None


def _sector(symbol: str) -> str:
    try:
        row = ds._classification_full((symbol or "").upper())
    except Exception:                                       # noqa: BLE001
        return "Unclassified"
    if not row:
        return "Unclassified"
    return str(row[3] or row[2] or row[1] or "Unclassified")


# ── the fill engine ──────────────────────────────────────────────────

class Reject(Exception):
    """A refusal with a reason the caller can show. Not an error."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _position(acct_id: int, uid: int, symbol: str):
    row = _db().execute(
        "SELECT id, quantity, avg_cost, realized_pnl FROM paper_positions "
        "WHERE account_id=? AND symbol=?", (acct_id, symbol)).fetchone()
    if row is not None:
        return row
    _db().execute(
        "INSERT INTO paper_positions (account_id, user_id, symbol, quantity, "
        "avg_cost, realized_pnl, opened_at) VALUES (?,?,?,0,0,0,?)",
        (acct_id, uid, symbol, int(time.time())))
    return _db().execute(
        "SELECT id, quantity, avg_cost, realized_pnl FROM paper_positions "
        "WHERE account_id=? AND symbol=?", (acct_id, symbol)).fetchone()


def _fill(acct: tuple, order: dict, price: Decimal, *, seed_prev: Optional[Decimal]) -> dict:
    """Fill one order at `price`. CALLER HOLDS THE WRITE LOCK.

    The cost model is `trading_costs`, which already bakes slippage into
    `charges` — so the fill happens at the clean mark and takes ALL its
    friction from the charges figure. Slipping the price too would count it
    twice, which is how a paper book quietly becomes pessimistic.

    `avg_cost` is inclusive of buy-side charges (net_debit / qty), so the
    realized P&L on the eventual sell is the true, cost-inclusive number
    rather than a gross one the user then has to discount in their head.
    """
    acct_id, _mode, _start, cash_av, cash_st, cash_rs = acct
    uid = int(order["user_id"])
    symbol = order["symbol"]
    qty = _qty(order["quantity"])
    side = order["side"]
    if qty <= 0:
        raise Reject("quantity_must_be_positive")

    cash_av = to_money(cash_av)
    cash_st = to_money(cash_st)
    cash_rs = to_money(cash_rs)
    reserved = to_money(order.get("reserved_cash") or 0)

    if side == "BUY":
        net_f, charges_f = trading_costs.buy_cost(float(price), int(qty))
        net = to_money(net_f)
        charges = to_money(charges_f)
        # A resting BUY already moved its estimate out of cash_available into
        # cash_reserved. Release it first, then spend — otherwise the same
        # rupees are counted twice and a legitimate fill is rejected for
        # insufficient funds against money it is itself holding.
        if reserved:
            cash_av += reserved
            cash_rs -= reserved
        if net > cash_av:
            raise Reject("insufficient_buying_power")
        cashflow = -net
        pos = _position(acct_id, uid, symbol)
        cur_qty = _qty(pos[1])
        cur_avg = to_money(pos[2])
        new_qty = cur_qty + qty
        new_avg = to_money((cur_avg * cur_qty + net) / new_qty)
        realized = None
        opens = cur_qty == 0
        _db().execute(
            "UPDATE paper_positions SET quantity=?, avg_cost=? WHERE id=?",
            (_qty_out(new_qty), f(new_avg), pos[0]))
        if opens:
            # Seed a mark the moment the position opens. Without this, P&L
            # reads zero until the next tick and Day P&L has no reference at
            # all — a position that shows "0.00%" the second it is bought
            # looks broken even though it is merely unmarked.
            _db().execute(
                "UPDATE paper_positions SET last_price=?, prev_close=?, "
                "last_mark_at=?, stale=0, opened_at=? WHERE id=?",
                (f(price), f(seed_prev if seed_prev is not None else price),
                 int(time.time()), int(time.time()), pos[0]))
        kind = "buy_debit"
    else:
        pos_row = _db().execute(
            "SELECT id, quantity, avg_cost, realized_pnl FROM paper_positions "
            "WHERE account_id=? AND symbol=?", (acct_id, symbol)).fetchone()
        cur_qty = _qty(pos_row[1]) if pos_row else Decimal(0)
        # Long-only: an oversell is refused rather than opening a short. See
        # the module header — this is a delivery book.
        if pos_row is None or qty > cur_qty:
            raise Reject("insufficient_position")
        net_f, charges_f = trading_costs.sell_cost(float(price), int(qty))
        net = to_money(net_f)
        charges = to_money(charges_f)
        cashflow = net
        cur_avg = to_money(pos_row[2])
        realized = to_money(net - cur_avg * qty)
        new_qty = cur_qty - qty
        new_avg = cur_avg if new_qty > 0 else to_money(0)
        _db().execute(
            "UPDATE paper_positions SET quantity=?, avg_cost=?, realized_pnl=? "
            "WHERE id=?", (_qty_out(new_qty), f(new_avg),
                           f(to_money(pos_row[3]) + realized), pos_row[0]))
        kind = "sell_credit"

    cash_av += cashflow
    cash_st += cashflow
    now = int(time.time())
    _db().execute(
        "UPDATE paper_accounts SET cash_available=?, cash_settled=?, "
        "cash_reserved=?, updated=? WHERE id=?",
        (f(cash_av), f(cash_st), f(cash_rs), now, acct_id))

    fill_id = uuid.uuid4().hex
    gross = to_money(price * qty)
    _db().execute(
        "INSERT INTO paper_fills (id, order_id, account_id, user_id, symbol, "
        "side, quantity, fill_price, gross_value, charges, net_cashflow, "
        "realized_pnl, strategy_id, filled_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (fill_id, order["id"], acct_id, uid, symbol, side, _qty_out(qty),
         f(price), f(gross), f(charges), f(cashflow),
         None if realized is None else f(realized),
         order.get("strategy_id"), now))
    _db().execute(
        "INSERT INTO paper_ledger (account_id, kind, amount, balance_after, "
        "ref_id, ts) VALUES (?,?,?,?,?,?)",
        (acct_id, kind, f(cashflow), f(cash_av), fill_id, now))
    _db().execute(
        "UPDATE paper_orders SET status='filled', reserved_cash=0, updated=? "
        "WHERE id=?", (now, order["id"]))
    return {
        "id": fill_id, "order_id": order["id"], "symbol": symbol, "side": side,
        "quantity": _qty_out(qty), "fill_price": f(price),
        "gross_value": f(gross), "charges": f(charges),
        "net_cashflow": f(cashflow),
        "realized_pnl": None if realized is None else f(realized),
        "filled_at": _iso(now),
    }


# ── placing an order ─────────────────────────────────────────────────

def place_order(uid: int, symbol: str, side: str, quantity, *,
                order_type: str = "MARKET", limit_price=None,
                trigger_price=None, source: str = "manual",
                origin_kind: Optional[str] = None,
                strategy_id: Optional[int] = None,
                price: Optional[Decimal] = None) -> dict:
    """Place one order into the book.

    MARKET fills synchronously at the resolved mark and returns its fill.
    Everything else RESTS: a BUY reserves its estimated cost so the same
    rupees cannot be spent twice while it waits, and `on_price` fills it when
    the price arrives.

    `price` lets a caller that has already resolved a mark (the strategy
    watcher, holding a bar it just evaluated) pass it in rather than making
    this function look it up again — the fill must happen at the price the
    rule fired on, not at whatever the cache says a moment later.
    """
    init_db()
    sym = (symbol or "").upper().strip()
    side = (side or "").upper().strip()
    otype = (order_type or "MARKET").upper().strip()
    qty = _qty(quantity)
    if not sym:
        raise Reject("symbol_required")
    if side not in ("BUY", "SELL"):
        raise Reject(f"bad_side:{side}")
    if qty <= 0:
        raise Reject("quantity_must_be_positive")
    if otype != "MARKET" and otype not in _RESTING_TYPES:
        raise Reject(f"bad_order_type:{otype}")

    # Everything slow happens before the lock: a mark resolution reads bars,
    # and holding the account lock across a bar read would serialise every
    # other writer behind it.
    px = to_money(price) if price is not None else None
    seed_prev = None
    if otype == "MARKET":
        if px is None:
            px = mark_price(sym)
        if px is None:
            raise Reject("no_price_available")
        seed_prev = prev_close(sym)
    # An SL-M knows its trigger but not its fill, so its reserve has to be
    # estimated off the mark. Resolved HERE because it reads bars, and the
    # account lock below must never be held across a bar read.
    ref_px = None
    if otype != "MARKET" and side == "BUY" and not (limit_price or trigger_price):
        ref_px = mark_price(sym)

    acct = get_or_create_account(uid)
    now = int(time.time())
    oid = uuid.uuid4().hex
    order = {"id": oid, "user_id": int(uid), "symbol": sym, "side": side,
             "quantity": _qty_out(qty), "strategy_id": strategy_id,
             "reserved_cash": 0}

    with ds._users_lock:
        acct = _account_row(uid)
        reserve = to_money(0)
        if otype != "MARKET" and side == "BUY":
            # Reserve at the price it would rest at, or at the mark when it
            # has none (an SL-M knows its trigger, not its fill).
            ref = to_money(limit_price or trigger_price or 0)
            if ref <= 0:
                ref = ref_px or to_money(0)
            est, _c = trading_costs.buy_cost(float(ref), int(qty))
            reserve = to_money(est)
            if reserve > to_money(acct[3]):
                raise Reject("insufficient_buying_power")
        _db().execute(
            "INSERT INTO paper_orders (id, account_id, user_id, symbol, side, "
            "order_type, quantity, limit_price, trigger_price, reserved_cash, "
            "status, source, origin_kind, strategy_id, created, updated) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (oid, acct[0], int(uid), sym, side, otype, _qty_out(qty),
             None if limit_price is None else f(limit_price),
             None if trigger_price is None else f(trigger_price),
             f(reserve), "resting" if otype != "MARKET" else "pending",
             source, origin_kind, strategy_id, now, now))
        if reserve:
            _db().execute(
                "UPDATE paper_accounts SET cash_available=cash_available-?, "
                "cash_reserved=cash_reserved+?, updated=? WHERE id=?",
                (f(reserve), f(reserve), now, acct[0]))
            order["reserved_cash"] = f(reserve)

        if otype != "MARKET":
            _db().commit()
            _index_add(sym)
            return {"order_id": oid, "status": "resting", "symbol": sym,
                    "side": side, "quantity": _qty_out(qty),
                    "order_type": otype,
                    "limit_price": None if limit_price is None else f(limit_price),
                    "trigger_price": None if trigger_price is None else f(trigger_price)}

        try:
            fill = _fill(acct, order, px, seed_prev=seed_prev)
        except Reject as exc:
            _db().execute(
                "UPDATE paper_orders SET status='rejected', reject_reason=?, "
                "updated=? WHERE id=?", (exc.reason, now, oid))
            _db().commit()
            raise
        _db().commit()
    return {"order_id": oid, "status": "filled", **fill}


def cancel_order(uid: int, order_id: str) -> dict:
    init_db()
    now = int(time.time())
    with ds._users_lock:
        row = _db().execute(
            "SELECT o.id, o.account_id, o.status, o.reserved_cash FROM "
            "paper_orders o WHERE o.id=? AND o.user_id=?",
            (str(order_id), int(uid))).fetchone()
        if row is None:
            return {"error": "no such order"}
        if row[2] != "resting":
            return {"error": f"order is {row[2]}, not resting"}
        if to_money(row[3]) > 0:
            _db().execute(
                "UPDATE paper_accounts SET cash_available=cash_available+?, "
                "cash_reserved=cash_reserved-?, updated=? WHERE id=?",
                (row[3], row[3], now, row[1]))
        _db().execute(
            "UPDATE paper_orders SET status='cancelled', reserved_cash=0, "
            "updated=? WHERE id=?", (now, row[0]))
        _db().commit()
    return {"ok": True, "order_id": order_id, "status": "cancelled"}


# ── the tick ─────────────────────────────────────────────────────────
#
# Two jobs per price: stamp the marks so the portfolio is current, and fill
# any resting order whose price has arrived. Both are gated on an in-memory
# index of symbols this book actually cares about, so a tick on a symbol
# nobody holds costs one set lookup.

_index: set[str] = set()
_index_lock = threading.Lock()


def _index_add(symbol: str) -> None:
    with _index_lock:
        _index.add((symbol or "").upper())


def load_index() -> dict:
    """Rebuild the watched-symbol set from the book. Called at boot and after
    any write that could add a symbol."""
    init_db()
    with ds._users_lock:
        rows = _db().execute(
            "SELECT DISTINCT symbol FROM paper_positions WHERE quantity != 0 "
            "UNION SELECT DISTINCT symbol FROM paper_orders WHERE status='resting'"
        ).fetchall()
    with _index_lock:
        _index.clear()
        _index.update(str(r[0]).upper() for r in rows)
        return {"symbols": len(_index)}


def _triggered(order: tuple, px: Decimal) -> bool:
    """Has this resting order's price arrived?

    LIMIT is a price you are willing to pay or accept — a BUY fills at or
    below it, a SELL at or above. SL / SL-M / GTT are the mirror: they arm on
    the way THROUGH the trigger, so a stop-loss SELL fires when price falls to
    it and a breakout BUY fires when price rises to it.
    """
    otype, limit_p, trig_p, side = order[5], order[7], order[8], order[4]
    if otype == "LIMIT":
        ref = to_money(limit_p or 0)
        if ref <= 0:
            return False
        return px <= ref if side == "BUY" else px >= ref
    ref = to_money(trig_p or limit_p or 0)
    if ref <= 0:
        return False
    return px >= ref if side == "BUY" else px <= ref


def on_price(symbol: str, price: float) -> None:
    """THE HOOK, called from the tick thread. Never blocks, never raises.

    A tick on a symbol the book does not hold and has no order for returns on
    a set lookup. Everything else takes the write lock briefly to stamp a mark
    and fill what has triggered — the same discipline `alerts.on_bar` keeps,
    for the same reason: a watcher that stalls the tick loop costs stored
    minutes, and the minutes are the asset.
    """
    sym = (symbol or "").upper()
    with _index_lock:
        if sym not in _index:
            return
    try:
        px = to_money(price)
        if px <= 0:
            return
        now = int(time.time())
        fills = []
        with ds._users_lock:
            _db().execute(
                "UPDATE paper_positions SET last_price=?, last_mark_at=?, "
                "stale=0 WHERE symbol=? AND quantity != 0",
                (f(px), now, sym))
            resting = _db().execute(
                "SELECT id, account_id, user_id, symbol, side, order_type, "
                "quantity, limit_price, trigger_price, reserved_cash, "
                "strategy_id FROM paper_orders WHERE status='resting' AND symbol=?",
                (sym,)).fetchall()
            for row in resting:
                if not _triggered(row, px):
                    continue
                acct = _db().execute(
                    "SELECT id, mode, starting_capital, cash_available, "
                    "cash_settled, cash_reserved FROM paper_accounts WHERE id=?",
                    (row[1],)).fetchone()
                if acct is None:
                    continue
                order = {"id": row[0], "user_id": row[2], "symbol": row[3],
                         "side": row[4], "quantity": row[6],
                         "reserved_cash": row[9], "strategy_id": row[10]}
                # A LIMIT fills at its own price when the market has gone
                # past it (that is the price the user asked for); a trigger
                # order fills at the market, which is what a stop does.
                fill_px = px
                if row[5] == "LIMIT" and row[7]:
                    lim = to_money(row[7])
                    fill_px = lim if ((row[4] == "BUY" and px < lim)
                                      or (row[4] == "SELL" and px > lim)) else px
                try:
                    fills.append(_fill(acct, order, fill_px, seed_prev=None))
                except Reject as exc:
                    _db().execute(
                        "UPDATE paper_orders SET status='rejected', "
                        "reject_reason=?, reserved_cash=0, updated=? WHERE id=?",
                        (exc.reason, now, row[0]))
                    if to_money(row[9]) > 0:
                        _db().execute(
                            "UPDATE paper_accounts SET "
                            "cash_available=cash_available+?, "
                            "cash_reserved=cash_reserved-?, updated=? WHERE id=?",
                            (row[9], row[9], now, row[1]))
            # Always: the mark stamp above is a write, and gating the commit
            # on there being a resting order meant a book with none never
            # persisted a price at all.
            _db().commit()
        if fills:
            load_index()
    except Exception:                                       # noqa: BLE001
        log.warning("paper: tick on %s failed", sym, exc_info=True)


# ── valuation + reads ────────────────────────────────────────────────

def _value(pos: dict, mark: Optional[Decimal]) -> dict:
    """One position's numbers at a given mark.

    Fallback order for the price is deliberate and shows itself in the reply:
    the live mark, then the last stored mark, then the cost basis. A lot that
    has never been priced is worth what it cost — not zero, and never a made-up
    move.
    """
    qty = _qty(pos["quantity"])
    avg = to_money(pos["avg_cost"])
    stored = None if pos["last_price"] is None else to_money(pos["last_price"])
    px = mark if mark is not None else (stored if stored is not None else avg)
    mv = to_money(px * qty)
    invested = to_money(avg * qty)
    unreal = to_money(mv - invested)
    prev = None if pos["prev_close"] is None else to_money(pos["prev_close"])
    day = to_money((px - prev) * qty) if prev is not None else to_money(0)
    return {"px": px, "mv": mv, "invested": invested,
            "unrealized": unreal, "day": day}


def _positions(acct_id: int) -> list[dict]:
    cols = ("id", "symbol", "quantity", "avg_cost", "realized_pnl",
            "last_price", "prev_close", "last_mark_at", "stale")
    with ds._users_lock:
        rows = _db().execute(
            f"SELECT {', '.join(cols)} FROM paper_positions WHERE account_id=?",
            (acct_id,)).fetchall()
    return [dict(zip(cols, r)) for r in rows]


def api_summary(uid: int) -> tuple[int, dict]:
    """The account roll-up, live-marked on read.

    NAV counts reserved cash — it is still the user's money, merely held
    against a resting buy — so the equity curve does not dip the moment an
    order rests and recover when it fills. That dip is not a loss and drawing
    it as one is a lie about the strategy.
    """
    acct = account_of(uid)
    if acct is None:
        return 200, {"exists": False}
    acct_id, mode, start, cash_av, cash_st, cash_rs = acct
    positions = _positions(acct_id)

    mv = to_money(0); invested = to_money(0); unreal = to_money(0)
    day = to_money(0); realized = to_money(0)
    n_open = 0; stale = False
    for p in positions:
        realized += to_money(p["realized_pnl"])
        if _qty(p["quantity"]) == 0:
            continue
        n_open += 1
        mark = mark_price(p["symbol"])
        v = _value(p, mark)
        mv += v["mv"]; invested += v["invested"]
        unreal += v["unrealized"]; day += v["day"]
        if mark is None and p["last_price"] is None:
            stale = True

    cash_av = to_money(cash_av); cash_rs = to_money(cash_rs)
    start_m = to_money(start)
    nav = cash_av + cash_rs + mv
    total = nav - start_m
    with ds._users_lock:
        n_orders = _db().execute(
            "SELECT COUNT(*) FROM paper_orders WHERE account_id=? AND "
            "status='resting'", (acct_id,)).fetchone()[0]
    return 200, {
        "exists": True, "mode": mode,
        "starting_capital": f(start_m), "cash_available": f(cash_av),
        "cash_settled": f(cash_st), "cash_reserved": f(cash_rs),
        "buying_power": f(cash_av),
        "positions_mv": f(mv), "invested": f(invested), "nav": f(nav),
        "unrealized_pnl": f(unreal), "realized_pnl_cum": f(realized),
        "day_pnl": f(day), "total_pnl": f(total),
        "total_pnl_pct": float(total / start_m * 100) if start_m else 0.0,
        "unrealized_pct": float(unreal / invested * 100) if invested else 0.0,
        "num_positions": n_open, "num_open_orders": int(n_orders),
        "is_stale": stale,
    }


def api_holdings(uid: int) -> tuple[int, list]:
    acct = account_of(uid)
    if acct is None:
        return 200, []
    out = []
    for p in _positions(acct[0]):
        if _qty(p["quantity"]) == 0:
            continue
        mark = mark_price(p["symbol"])
        v = _value(p, mark)
        out.append({
            "symbol": p["symbol"], "quantity": _qty_out(p["quantity"]),
            "avg_cost": f(p["avg_cost"]), "buy_price": f(p["avg_cost"]),
            "last_price": f(v["px"]),
            "market_value": f(v["mv"]), "unrealized_pnl": f(v["unrealized"]),
            "unrealized_pct": (float(v["unrealized"] / v["invested"] * 100)
                               if v["invested"] else 0.0),
            "day_pnl": f(v["day"]), "invested": f(v["invested"]),
            "realized_pnl": f(p["realized_pnl"]),
            "sector": _sector(p["symbol"]),
            # Stale means one thing: we could not price this lot at all and
            # the figure beside it is what it cost. Not "the market is shut" —
            # outside hours the last close is the right price, not a warning.
            "stale": mark is None and p["last_price"] is None,
            "last_mark_at": _iso(p["last_mark_at"]),
        })
    out.sort(key=lambda r: r["market_value"], reverse=True)
    return 200, out


def api_orders(uid: int) -> tuple[int, list]:
    acct = account_of(uid)
    if acct is None:
        return 200, []
    with ds._users_lock:
        rows = _db().execute(
            "SELECT id, symbol, side, order_type, quantity, limit_price, "
            "trigger_price, reserved_cash, status, source, origin_kind, created "
            "FROM paper_orders WHERE account_id=? AND status='resting' "
            "ORDER BY created DESC", (acct[0],)).fetchall()
    return 200, [{
        "id": r[0], "symbol": r[1], "side": r[2], "order_type": r[3],
        "quantity": _qty_out(r[4]), "limit_price": r[5], "trigger_price": r[6],
        "reserved_cash": f(r[7]), "status": r[8], "source": r[9],
        "origin_kind": r[10], "created_at": _iso(r[11]),
    } for r in rows]


def api_fills(uid: int, limit: int = 50, offset: int = 0) -> tuple[int, list]:
    acct = account_of(uid)
    if acct is None:
        return 200, []
    limit = max(1, min(500, int(limit or 50)))
    with ds._users_lock:
        rows = _db().execute(
            "SELECT id, symbol, side, quantity, fill_price, gross_value, "
            "charges, net_cashflow, realized_pnl, filled_at, order_id "
            "FROM paper_fills WHERE account_id=? ORDER BY filled_at DESC, "
            "rowid DESC LIMIT ? OFFSET ?",
            (acct[0], limit, max(0, int(offset or 0)))).fetchall()
    return 200, [{
        "id": r[0], "symbol": r[1], "side": r[2], "quantity": _qty_out(r[3]),
        "fill_price": f(r[4]), "gross_value": f(r[5]), "charges": f(r[6]),
        "net_cashflow": f(r[7]),
        "realized_pnl": None if r[8] is None else f(r[8]),
        "filled_at": _iso(r[9]), "order_id": r[10],
    } for r in rows]


def api_nav(uid: int, start: str = "", end: str = "") -> tuple[int, list]:
    """The equity curve, oldest first.

    Today's point is computed live and appended rather than read from the
    table: the snapshot lands once, after the close, and a curve that stops at
    yesterday while the summary above it shows today's NAV is two numbers
    disagreeing on the same screen.
    """
    acct = account_of(uid)
    if acct is None:
        return 200, []
    with ds._users_lock:
        opened = _db().execute(
            "SELECT created FROM paper_accounts WHERE id=?", (acct[0],)
        ).fetchone()
    where, args = "account_id=?", [acct[0]]
    if start:
        where += " AND as_of_date >= ?"; args.append(str(start))
    if end:
        where += " AND as_of_date <= ?"; args.append(str(end))
    with ds._users_lock:
        rows = _db().execute(
            "SELECT as_of_date, nav, cash_available, positions_mv, "
            f"realized_pnl_cum, unrealized_pnl, nifty_close FROM paper_nav "
            f"WHERE {where} ORDER BY as_of_date ASC", args).fetchall()
    out = [{"as_of_date": r[0], "nav": f(r[1]), "cash_available": f(r[2]),
            "positions_mv": f(r[3]), "realized_pnl_cum": f(r[4]),
            "unrealized_pnl": f(r[5]),
            "nifty_close": None if r[6] is None else f(r[6])} for r in rows]
    # The opening point. A book that has traded today but has no end-of-day
    # snapshot yet had exactly ONE point, and the chart needs two — so it drew
    # nothing and said the curve appears after the first snapshot, on a
    # portfolio the user had just watched a strategy fill. NAV at the moment
    # the account opened is the seed, which is a fact rather than a
    # reconstruction, so the curve starts where the money did.
    if opened and opened[0]:
        first_date = datetime.fromtimestamp(int(opened[0]), IST).date().isoformat()
        if not any(r["as_of_date"] == first_date for r in out) and \
                (not start or first_date >= str(start)):
            out.insert(0, {
                "as_of_date": first_date, "nav": f(to_money(acct[2])),
                "cash_available": f(to_money(acct[2])), "positions_mv": 0.0,
                "realized_pnl_cum": 0.0, "unrealized_pnl": 0.0,
                "nifty_close": None})
    today = _today()
    if not end or end >= today:
        _code, s = api_summary(uid)
        if s.get("exists"):
            live = {"as_of_date": today, "nav": s["nav"],
                    "cash_available": s["cash_available"],
                    "positions_mv": s["positions_mv"],
                    "realized_pnl_cum": s["realized_pnl_cum"],
                    "unrealized_pnl": s["unrealized_pnl"], "nifty_close": None}
            if len(out) > 1 and out[-1]["as_of_date"] == today:
                out[-1] = live
            elif not (len(out) == 1 and out[0] is live):
                out.append(live)
    return 200, out


def snapshot_nav(uid: int) -> dict:
    """Freeze today's NAV, and refresh prev_close so tomorrow's day-P&L has a
    reference. Idempotent per date."""
    acct = account_of(uid)
    if acct is None:
        return {"skipped": "no account"}
    _code, s = api_summary(uid)
    if not s.get("exists"):
        return {"skipped": "no account"}
    now = int(time.time())
    with ds._users_lock:
        _db().execute(
            "INSERT INTO paper_nav (account_id, as_of_date, nav, "
            "cash_available, positions_mv, realized_pnl_cum, unrealized_pnl, "
            "nifty_close, ts) VALUES (?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(account_id, as_of_date) DO UPDATE SET nav=excluded.nav,"
            " cash_available=excluded.cash_available,"
            " positions_mv=excluded.positions_mv,"
            " realized_pnl_cum=excluded.realized_pnl_cum,"
            " unrealized_pnl=excluded.unrealized_pnl, ts=excluded.ts",
            (acct[0], _today(), s["nav"], s["cash_available"],
             s["positions_mv"], s["realized_pnl_cum"], s["unrealized_pnl"],
             None, now))
        _db().commit()
    return {"ok": True, "as_of_date": _today(), "nav": s["nav"]}


def roll_prev_close() -> dict:
    """Set every open position's prev_close to its current mark. Run once a
    day, after the close — day P&L is 'the move since the last close', and
    without this roll it silently becomes 'the move since I bought it'."""
    init_db()
    with ds._users_lock:
        rows = _db().execute(
            "SELECT DISTINCT symbol FROM paper_positions WHERE quantity != 0"
        ).fetchall()
    n = 0
    for (sym,) in rows:
        px = mark_price(sym)
        if px is None:
            continue
        with ds._users_lock:
            _db().execute(
                "UPDATE paper_positions SET prev_close=? WHERE symbol=? AND "
                "quantity != 0", (f(px), sym))
            _db().commit()
        n += 1
    return {"rolled": n}


def start() -> dict:
    """Called once from dataserver's boot block, after the module alias."""
    init_db()
    return load_index()


# ── the Portfolio page's own two reads ───────────────────────────────
#
# Pivot's PortfolioTab is reused here unchanged, so these two answer the paths
# it already calls. Everything else it needs — the summary, the holdings, the
# fills, the resting orders — it gets through `lib/api`'s paper branch, which
# reads the endpoints above. These are the two with no paper equivalent.

_PERIOD_DAYS = {"1M": 30, "3M": 90, "6M": 180, "1Y": 365, "5Y": 1825}


def api_performance(uid: int, period: str = "1Y") -> tuple[int, dict]:
    """The equity curve, as the chart's own shape.

    Built from `api_nav`, which already carries the opening point, every
    end-of-day snapshot and today's live NAV — so the curve here and the NAV
    figure above it are the same numbers, and cannot drift into disagreeing on
    one screen.
    """
    period = (period or "1Y").upper()
    days = _PERIOD_DAYS.get(period, 365)
    start = (datetime.now(IST) - timedelta(days=days)).date().isoformat()
    _code, rows = api_nav(uid, start, "")
    points = [{"t": r["as_of_date"], "v": r["nav"]} for r in rows
              if r.get("as_of_date")]
    if not points:
        return 200, {"period": period, "points": [], "starting_value": 0.0,
                     "ending_value": 0.0, "total_return": 0.0,
                     "total_return_pct": 0.0}
    first, last = points[0]["v"], points[-1]["v"]
    ret = to_money(last) - to_money(first)
    return 200, {
        "period": period, "points": points,
        "starting_value": f(first), "ending_value": f(last),
        "total_return": f(ret),
        "total_return_pct": float(ret / to_money(first) * 100) if first else 0.0,
    }


def api_scores(uid: int) -> tuple[int, dict]:
    """Diversification, and a portfolio score built out of it.

    Real arithmetic over the real book: how many names, how many sectors, what
    the largest single position and largest sector are as a share of market
    value, and the Herfindahl index of the weights.

    `community_score` is NULL and stays null. It is a percentile against other
    users' portfolios, and Charto has no such population to rank against — a
    number here would be invented, and the panel is built to render its absence.
    """
    acct = account_of(uid)
    empty = {"diversification_score": None, "portfolio_score": None,
             "community_score": None, "reason": "no_holdings"}
    if acct is None:
        return 200, empty

    rows = []
    for p in _positions(acct[0]):
        if _qty(p["quantity"]) == 0:
            continue
        v = _value(p, mark_price(p["symbol"]))
        rows.append((p["symbol"], v["mv"], _sector(p["symbol"])))
    if not rows:
        return 200, empty

    total = sum((r[1] for r in rows), to_money(0))
    if total <= 0:
        return 200, empty
    weights = [float(r[1] / total) for r in rows]
    by_sector: dict[str, Decimal] = {}
    for _s, mv, sector in rows:
        by_sector[sector] = by_sector.get(sector, to_money(0)) + mv

    n_holdings = len(rows)
    n_sectors = len(by_sector)
    top_holding = max(weights) * 100
    top_sector = float(max(by_sector.values()) / total) * 100
    hhi = sum(w * w for w in weights)

    # A concentrated book scores badly and should say so. The shape: an even
    # split across many names approaches 100, everything in one name is 0.
    div = max(0.0, min(100.0, (1.0 - hhi) * 100))
    spread = min(100.0, n_sectors / 8 * 100)
    div = round(div * 0.7 + spread * 0.3, 1)
    concentration_penalty = round(max(0.0, top_holding - 25.0), 1)

    _c, summary = api_summary(uid)
    total_ret = summary.get("total_pnl_pct") if summary.get("exists") else None
    perf_available = total_ret is not None
    subscores = {"diversification": div,
                 "concentration_penalty": concentration_penalty}
    weights_used = {"diversification": 0.6, "concentration_penalty": 0.4}
    if perf_available:
        subscores["performance"] = round(max(0.0, min(100.0, 50 + total_ret)), 1)
        weights_used = {"diversification": 0.45,
                        "concentration_penalty": 0.25, "performance": 0.30}
    score = round(sum(subscores[k] * weights_used[k] for k in weights_used
                      if k != "concentration_penalty")
                  - concentration_penalty * weights_used["concentration_penalty"], 1)
    score = max(0.0, min(100.0, score))

    top_name = max(rows, key=lambda r: r[1])[0]
    top_sec_name = max(by_sector, key=lambda k: by_sector[k])
    return 200, {
        "diversification_score": {
            "score": div,
            "components": {
                "n_holdings": n_holdings, "n_sectors": n_sectors,
                "top_holding_pct": round(top_holding, 1),
                "top_sector_pct": round(top_sector, 1),
                "hhi": round(hhi, 4),
            },
            "explainer": (
                f"{n_holdings} holding{'s' if n_holdings != 1 else ''} across "
                f"{n_sectors} sector{'s' if n_sectors != 1 else ''}. "
                f"{top_name} is {top_holding:.1f}% of the book and "
                f"{top_sec_name} is {top_sector:.1f}%."),
        },
        "portfolio_score": {
            "score": score,
            "components": {
                "subscores": subscores, "weights": weights_used,
                "performance_available": perf_available,
                "total_return_pct": total_ret,
            },
            "explainer": (
                "Diversification and concentration, plus return where there is "
                "enough history to read one. Simulated book."),
        },
        "community_score": None,
        "reason": None,
    }
