"""The day book: the paper book for TRADING, beside paper.py's book for holding.

paper.py is a long-only delivery book by design (India bans naked short
delivery, and it has no lot sizes or margin). Day traders need the other
half: intraday longs and shorts that square off at 15:20, NSE option and
future legs with lot sizes, and the exits they actually run: a stop and a
target per leg, a trailing stop, "when one leg stops, move the other to
cost", a whole-position profit or loss exit, a time exit. That is this book.

Simulation only. Nothing here talks to a broker. Prices:
  equities and crypto   the live tick (ds bar hook), else the last stored close
  options and futures   Kite quotes for the NFO tradingsymbol (options.py's
                        client), else the last NSE F&O close, flagged stale.
                        A stale leg's stop cannot be watched, and the event
                        that opened it says so.
Costs and margin are ESTIMATES with the usual Indian intraday/F&O charges
(brokerage, STT, exchange, SEBI, stamp, GST); every payload labels them so.

Exits never wait for a confirm: a position only exists because the trader
confirmed it or let the play act, and a stop that waits for a tap is not a
stop. That is the plan's rule that risk-reducing actions get more autonomy.
"""
from __future__ import annotations

import json
import logging
import math
import queue
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

import hyperliquid

log = logging.getLogger("daybook")

ds = None
on_event = None            # plays sets this: (play_id, kind, title, body) -> None
_quote = None              # options._kite_quote by default; tests swap it

IST = timezone(timedelta(hours=5, minutes=30))
SQUARE_OFF = {"NSE": (15, 20), "NFO": (15, 20), "MCX": (23, 25)}


def bind(module, quote_fn=None) -> None:
    global ds, _quote
    ds = module
    if quote_fn is not None:
        _quote = quote_fn
    elif _quote is None:
        import options
        _quote = options._kite_quote


_SCHEMA = """
CREATE TABLE IF NOT EXISTS day_positions (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id   INTEGER NOT NULL REFERENCES users(id),
  play_id   INTEGER,
  grp       TEXT NOT NULL,                  -- legs opened together (a straddle is one group)
  symbol    TEXT NOT NULL,                  -- what is traded: RELIANCE, BTCUSDT, NIFTY26OCT22750CE
  venue     TEXT NOT NULL,                  -- NSE | NFO | MCX | CRYPTO
  kind      TEXT NOT NULL,                  -- equity | crypto | future | option
  underlying TEXT NOT NULL DEFAULT '',
  expiry    TEXT NOT NULL DEFAULT '',
  strike    REAL,
  opt       TEXT NOT NULL DEFAULT '',       -- CE | PE
  lot       INTEGER NOT NULL DEFAULT 1,
  side      TEXT NOT NULL,                  -- long | short
  qty       REAL NOT NULL,                  -- units (lots × lot size for F&O)
  product   TEXT NOT NULL,                  -- MIS (squared off today) | NRML
  entry_px  REAL NOT NULL,
  entry_ts  INTEGER NOT NULL,
  mark_px   REAL,
  mark_ts   INTEGER,
  stale     INTEGER NOT NULL DEFAULT 0,
  stop_px   REAL,
  target_px REAL,
  trail_pct REAL,
  best_px   REAL,                           -- the best price since entry, for the trail
  state     TEXT NOT NULL DEFAULT 'open',
  exit_px   REAL,
  exit_ts   INTEGER,
  exit_why  TEXT NOT NULL DEFAULT '',
  costs     REAL NOT NULL DEFAULT 0,
  pnl       REAL,                           -- after estimated costs, once closed
  rules     TEXT NOT NULL DEFAULT '{}');    -- the group's exits: on_leg_stop, max_loss_inr, target_inr, exit_at
CREATE INDEX IF NOT EXISTS day_pos_open ON day_positions(state, symbol);
CREATE INDEX IF NOT EXISTS day_pos_user ON day_positions(user_id, entry_ts DESC);
"""
_ready = False


def _db():
    return ds._users


def init_db() -> None:
    global _ready
    if _ready:
        return
    with ds._users_lock:
        _db().executescript(_SCHEMA)
        cols = {r[1] for r in _db().execute("PRAGMA table_info(day_positions)")}
        for col, decl in (("leverage", "REAL NOT NULL DEFAULT 1"), ("funding", "REAL NOT NULL DEFAULT 0"),
                          ("funded_ts", "INTEGER NOT NULL DEFAULT 0")):
            if col not in cols:      # perps: leverage, funding paid (rupees), the last hour charged
                _db().execute(f"ALTER TABLE day_positions ADD COLUMN {col} {decl}")
        _db().commit()
    _ready = True


class Refused(ValueError):
    """An order this book will not open, with the sentence that says why."""


# ══ costs and margin (estimates) ════════════════════════════════════════════
# Zerodha-style 2026 schedule. Rates as fractions of turnover.
_RATES = {
    #          brokerage cap, brokerage pct, STT on sell, exchange, stamp on buy
    "equity": (20.0, 0.0003, 0.00025, 0.0000297, 0.00003),
    "future": (20.0, 0.0003, 0.0002, 0.0000173, 0.00002),
    "option": (20.0, None, 0.001, 0.0003503, 0.00003),
    "crypto": (0.0, 0.001, 0.0, 0.0, 0.0),      # a flat 0.1% exchange fee, no Indian levies
    "perp": (0.0, 0.00045, 0.0, 0.0, 0.0),      # Hyperliquid's base taker fee, 0.045%
}


def charges(kind: str, side: str, px: float, qty: float) -> float:
    cap, pct, stt, exch, stamp = _RATES["equity" if kind == "equity" else kind]
    turnover = px * qty
    brokerage = cap if pct is None else min(cap, turnover * pct) if cap else turnover * pct
    if kind in ("crypto", "perp"):
        return round(brokerage, 2)          # an exchange fee; no Indian levies or GST
    c = brokerage + turnover * exch + turnover * 10 / 1e7          # SEBI ₹10 a crore
    c += turnover * stt if side == "SELL" else turnover * stamp
    c += 0.18 * (brokerage + turnover * exch + turnover * 10 / 1e7)  # GST
    return round(c, 2)


def margin(leg: dict) -> float:
    """What the leg ties up, roughly: the premium for a bought option, a share
    of the notional otherwise. An estimate, labelled as one."""
    notional = leg["px"] * leg["qty"]
    if leg["kind"] == "option":
        if leg["side"] == "long":
            return notional
        und = (leg.get("spot") or 0) * leg["qty"]
        return max(notional, 0.12 * und if leg.get("index") else 0.18 * und)
    if leg["kind"] == "future":
        return 0.12 * notional if leg.get("index") else 0.20 * notional
    if leg["kind"] == "perp":
        return notional * leg.get("fx", 1.0) / max(1.0, float(leg.get("leverage") or 1))
    if leg["kind"] == "crypto":
        n = notional * leg.get("fx", 1.0)
        return n if leg["side"] == "long" else n * 0.5
    return 0.20 * notional if leg["product"] == "MIS" else notional


def group_margin(legs: list[dict]) -> float:
    """A group's margin, with the one offset brokers always give: short calls
    and short puts on the same underlying cannot both lose, so a short
    straddle or strangle is charged its larger side, not both."""
    shorts_ce = sum(margin(l) for l in legs if l["kind"] == "option" and l["side"] == "short" and l.get("opt") == "CE")
    shorts_pe = sum(margin(l) for l in legs if l["kind"] == "option" and l["side"] == "short" and l.get("opt") == "PE")
    rest = sum(margin(l) for l in legs if not (l["kind"] == "option" and l["side"] == "short"))
    other_premium = 0.0
    if shorts_ce and shorts_pe:
        side = "PE" if shorts_ce >= shorts_pe else "CE"
        other_premium = sum(l["px"] * l["qty"] for l in legs
                            if l["kind"] == "option" and l["side"] == "short" and l.get("opt") == side)
    return rest + max(shorts_ce, shorts_pe) + other_premium


# ══ resolving what to trade ═════════════════════════════════════════════════

def _now_ist() -> datetime:
    return datetime.now(IST)


def _equity_px(symbol: str) -> tuple[float | None, bool]:
    """(price, stale). The live tick when a feed is on, else the last close."""
    live = ds._live_view(symbol) if hasattr(ds, "_live_view") else None
    if live and live[0]:
        return float(live[0][4]), False
    try:
        d = ds.get_bars(symbol, "1m", None, 2)
        bars = d.get("bars") or []
        if bars:
            return float(bars[-1]["c"]), True
    except Exception:                                       # noqa: BLE001
        pass
    return None, True


def _is_crypto(symbol: str) -> bool:
    return symbol.endswith(("USDT", "USD"))


def resolve(do: dict, product: str) -> list[dict]:
    """A play's `do` as legs with real instruments and prices.

    do.kind long | short:  {symbol, size: {inr} | {qty}}
    do.kind options:       {underlying, legs: [{opt: CE|PE, strike: ATM | ATM+2 | ATM-1 |
                            22750, side: buy|sell, lots, expiry: weekly | next | monthly | date}]}
    do.kind future:        {underlying, side: buy|sell, lots, expiry}
    """
    kind = do["kind"]
    if kind in ("long", "short") and hyperliquid.is_perp(do["symbol"]):
        return [_perp_leg(do, kind)]
    if kind in ("long", "short"):
        sym = do["symbol"]
        px, stale = _equity_px(sym)
        if not px:
            raise Refused(f"no price for {sym}")
        crypto = _is_crypto(sym)
        if kind == "short" and not crypto and product != "MIS":
            raise Refused("a short in Indian equities is intraday only: product MIS, squared off at 15:20")
        size = do.get("size") or {}
        if size.get("qty"):
            qty = float(size["qty"])
        else:
            inr = float(size.get("inr") or 10_000)
            usd = 1.0
            if crypto:
                import paper
                fx, _ = paper.fx_rate("USD")
                usd = float(fx or 0) or 0
                if not usd:
                    raise Refused("no USD/INR rate to size a crypto position in rupees")
            qty = inr / (px * usd)
            qty = math.floor(qty * 1e6) / 1e6 if crypto else math.floor(qty)
        if qty <= 0:
            raise Refused(f"the size buys less than one unit of {sym} at {px:,.2f}")
        return [{"symbol": sym, "venue": "CRYPTO" if crypto else "NSE", "kind": "crypto" if crypto else "equity",
                 "side": kind, "qty": qty, "px": px, "stale": stale, "lot": 1, "product": product,
                 "fx": usd if crypto else 1.0}]
    import options
    und = str(do.get("underlying") or "").upper()
    ch = options.chain(und, _expiry_word(do.get("expiry") or (do.get("legs") or [{}])[0].get("expiry")),
                       strikes=40, live=True)
    if not ch.get("available"):
        raise Refused(ch.get("reason") or f"no F&O chain for {und}")
    lot, spot, step, atm = int(ch["lot_size"]), float(ch["spot"]), float(ch["step"]), float(ch["atm_strike"])
    live = ch.get("source") == "kite"
    index = bool(ch.get("index"))
    if kind == "future":
        import options as o
        eod = o.latest()
        u = eod["unds"][ch["underlying"]]
        exp = ch["expiry"]
        fsym = u["futsym"].get(exp) or next(iter(u["futsym"].values()), "")
        q = _quote([f"NFO:{fsym}"]).get(f"NFO:{fsym}") if fsym else None
        px = float((q or {}).get("last_price") or u["futs"].get(exp) or 0)
        if not fsym or not px:
            raise Refused(f"no future found for {und} {exp}")
        side = "long" if str(do.get("side") or "buy").lower() in ("buy", "long") else "short"
        return [{"symbol": fsym, "venue": "NFO", "kind": "future", "underlying": ch["underlying"],
                 "expiry": exp, "side": side, "qty": int(do.get("lots") or 1) * lot, "lot": lot,
                 "px": px, "stale": not q, "product": product, "spot": spot, "index": index}]
    if kind != "options":
        raise Refused(f"unknown order kind {kind}")
    rows = {r["strike"]: r for r in ch.get("rows") or []}
    if not rows:
        raise Refused(f"the {und} chain came back empty")
    legs = []
    for lg in do.get("legs") or []:
        opt = str(lg.get("opt") or lg.get("right") or "").upper()
        if opt not in ("CE", "PE"):
            raise Refused("each option leg needs opt CE or PE")
        k = _strike(lg.get("strike", "ATM"), atm, step)
        row = rows.get(k)
        q = (row or {}).get(opt.lower())
        if not q:
            raise Refused(f"{und} {k:g} {opt} is not in the {ch['expiry']} chain")
        side = "long" if str(lg.get("side") or "buy").lower() in ("buy", "long") else "short"
        # a market order crosses the spread: a buy pays the ask, a sell gets the
        # bid. Without live depth it is the last price, and the leg says so.
        bid, ask = float(q.get("bid") or 0), float(q.get("ask") or 0)
        px, at = ((ask, "ask") if side == "long" and ask > 0 else
                  (bid, "bid") if side == "short" and bid > 0 else
                  (float(q.get("mid") or q["ltp"]), "mid" if q.get("mid") else "last"))
        legs.append({"symbol": q["sym"], "venue": "NFO", "kind": "option", "underlying": ch["underlying"],
                     "expiry": ch["expiry"], "strike": k, "opt": opt, "side": side,
                     "qty": int(lg.get("lots") or 1) * lot, "lot": lot, "px": px, "filled_at": at,
                     "spread": round(ask - bid, 2) if bid > 0 and ask >= bid else None,
                     "stale": not live, "product": product, "spot": spot, "index": index})
    if not legs:
        raise Refused("an options order needs at least one leg")
    return legs


def _perp_leg(do: dict, side: str) -> dict:
    """A Hyperliquid perpetual on paper: the rupees are the margin, the
    leverage sets the notional, and the size is rounded down to the coin's
    own step."""
    sym = do["symbol"]
    c = hyperliquid.ctx(sym)
    if not c:
        raise Refused(f"Hyperliquid lists no perpetual {sym}")
    px = hyperliquid.mark(sym)
    if not px:
        raise Refused(f"no Hyperliquid price for {sym} right now")
    lev = float(do.get("leverage") or 1)
    if not 1 <= lev <= c["max_leverage"]:
        raise Refused(f"{sym} allows 1× to {c['max_leverage']}× leverage on Hyperliquid")
    import paper
    fx, _ = paper.fx_rate("USD")
    usd = float(fx or 0)
    if not usd:
        raise Refused("no USD/INR rate to size a perp in rupees")
    size = do.get("size") or {}
    if size.get("qty"):
        qty = float(size["qty"])
    else:
        inr = float(size.get("inr") or 10_000)
        qty = inr * lev / (px * usd)
    step = 10 ** -c["sz_decimals"]
    qty = math.floor(qty / step) * step
    qty = round(qty, c["sz_decimals"])
    if qty <= 0:
        raise Refused(f"the size buys less than one step ({step:g}) of {sym} at {px:,.2f} USD")
    return {"symbol": sym, "venue": "HL", "kind": "perp", "side": side, "qty": qty, "px": px, "stale": False,
            "lot": 1, "product": "PERP", "fx": usd, "leverage": lev, "underlying": hyperliquid.coin(sym)}


def _expiry_word(w):
    s = str(w or "").strip().lower()
    return None if s in ("", "weekly", "this week", "current", "nearest") else \
        ("next" if s in ("next", "next week") else w)


def _strike(raw, atm: float, step: float) -> float:
    s = str(raw).strip().upper().replace(" ", "")
    if s.startswith("ATM"):
        off = s[3:]
        n = int(off) if off and off.lstrip("+-").isdigit() else 0
        return atm + n * step
    try:
        return float(s)
    except ValueError:
        raise Refused(f"strike {raw!r} must be ATM, ATM+n, ATM-n or a number") from None


# ══ opening and closing ═════════════════════════════════════════════════════

_COLS = ("id", "user_id", "play_id", "grp", "symbol", "venue", "kind", "underlying", "expiry", "strike",
         "opt", "lot", "side", "qty", "product", "entry_px", "entry_ts", "mark_px", "mark_ts", "stale",
         "stop_px", "target_px", "trail_pct", "best_px", "state", "exit_px", "exit_ts", "exit_why",
         "costs", "pnl", "rules", "leverage", "funding", "funded_ts")


def _pos(t) -> dict:
    p = dict(zip(_COLS, t))
    p["rules"] = json.loads(p["rules"] or "{}")
    return p


def open_group(uid: int, play_id: int | None, legs: list[dict], manage: dict) -> dict:
    """Open every leg at its resolved price, with its stop, target and trail,
    and the group's rules. One group per fire."""
    init_db()
    grp = uuid.uuid4().hex[:12]
    now = int(time.time())
    rules = {k: manage[k] for k in ("on_leg_stop", "max_loss_inr", "target_inr", "exit_at")
             if manage.get(k) not in (None, "")}
    out = []
    with ds._users_lock:
        for lg in legs:
            px, short = lg["px"], lg["side"] == "short"
            stop = target = None
            if manage.get("stop_pct"):
                f = manage["stop_pct"] / 100
                stop = round(px * (1 + f) if short else px * (1 - f), 2)
            if manage.get("target_pct"):
                f = manage["target_pct"] / 100
                target = round(px * (1 - f) if short else px * (1 + f), 2)
            cost = charges(lg["kind"], "SELL" if short else "BUY", px * lg.get("fx", 1.0), lg["qty"])
            cur = _db().execute(
                "INSERT INTO day_positions (user_id, play_id, grp, symbol, venue, kind, underlying, expiry, "
                "strike, opt, lot, side, qty, product, entry_px, entry_ts, mark_px, mark_ts, stale, stop_px, "
                "target_px, trail_pct, best_px, costs, rules, leverage, funded_ts) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (uid, play_id, grp, lg["symbol"], lg["venue"], lg["kind"], lg.get("underlying", ""),
                 lg.get("expiry", ""), lg.get("strike"), lg.get("opt", ""), lg["lot"], lg["side"], lg["qty"],
                 lg["product"], px, now, px, now, int(bool(lg.get("stale"))), stop, target,
                 manage.get("trail_pct"), px, cost, json.dumps(rules), float(lg.get("leverage") or 1),
                 now // 3600 * 3600))
            out.append({**lg, "id": cur.lastrowid, "stop_px": stop, "target_px": target, "costs": cost})
        _db().commit()
    _reindex()
    return {"group": grp, "legs": out, "estimates": "costs and margin are estimates"}


def _fx(p: dict) -> float:
    if p["kind"] not in ("crypto", "perp"):
        return 1.0
    try:
        import paper
        fx, _ = paper.fx_rate("USD")
        return float(fx or 1.0)
    except Exception:                                       # noqa: BLE001
        return 1.0


def _gross(p: dict, px: float) -> float:
    d = (px - p["entry_px"]) if p["side"] == "long" else (p["entry_px"] - px)
    return d * p["qty"] * _fx(p)


def close(p: dict, px: float, why: str, *, notify: bool = True) -> dict:
    exit_cost = charges(p["kind"], "SELL" if p["side"] == "long" else "BUY", px * _fx(p), p["qty"])
    costs = round(p["costs"] + exit_cost, 2)
    pnl = round(_gross(p, px) - costs - float(p.get("funding") or 0), 2)
    now = int(time.time())
    with ds._users_lock:
        n = _db().execute("UPDATE day_positions SET state='closed', exit_px=?, exit_ts=?, exit_why=?, "
                          "costs=?, pnl=?, mark_px=?, mark_ts=? WHERE id=? AND state='open'",
                          (px, now, why, costs, pnl, px, now, p["id"])).rowcount
        _db().commit()
    if not n:
        return {}
    _reindex()
    res = {"position": p["id"], "symbol": p["symbol"], "side": p["side"], "qty": p["qty"],
           "entry": p["entry_px"], "exit": px, "pnl": pnl, "costs": costs, "why": why}
    if notify and on_event and p["play_id"]:
        try:
            on_event(p["play_id"], "exited", f"{p['symbol']}: {why}", res)
        except Exception:                                   # noqa: BLE001
            log.warning("daybook: exit event failed", exc_info=True)
    return res


def _fresh(pos_id: int) -> dict | None:
    with ds._users_lock:
        t = _db().execute(f"SELECT {', '.join(_COLS)} FROM day_positions WHERE id=? AND state='open'",
                          (pos_id,)).fetchone()
    return _pos(t) if t else None


def open_positions(uid: int | None = None, play_id: int | None = None) -> list[dict]:
    init_db()
    q, args = f"SELECT {', '.join(_COLS)} FROM day_positions WHERE state='open'", []
    if uid:
        q += " AND user_id=?"
        args.append(uid)
    if play_id:
        q += " AND play_id=?"
        args.append(play_id)
    with ds._users_lock:
        return [_pos(t) for t in _db().execute(q, args).fetchall()]


def close_play(play_id: int, why: str) -> list[dict]:
    out = []
    for p in open_positions(play_id=play_id):
        px = p["mark_px"] or p["entry_px"]
        out.append(close(p, px, why))
    return out


# ══ watching ════════════════════════════════════════════════════════════════

_BY_SYM: dict[str, list[int]] = {}
_LOCK = threading.Lock()
_Q: queue.Queue = queue.Queue(maxsize=4096)
_STOP = threading.Event()
_THREADS: list[threading.Thread] = []


def _reindex() -> None:
    with ds._users_lock:
        rows = _db().execute("SELECT id, symbol FROM day_positions WHERE state='open'").fetchall()
    idx: dict[str, list[int]] = {}
    for pid, sym in rows:
        idx.setdefault(sym, []).append(pid)
    with _LOCK:
        _BY_SYM.clear()
        _BY_SYM.update(idx)


def on_tick(symbol: str, form: list, closed: bool) -> None:
    with _LOCK:
        if symbol not in _BY_SYM:
            return
    try:
        _Q.put_nowait((symbol, float(form[4])))
    except queue.Full:
        pass


def _tick_worker() -> None:
    while not _STOP.is_set():
        try:
            sym, px = _Q.get(timeout=1.0)
        except queue.Empty:
            continue
        try:
            for pid in [p["id"] for p in open_positions() if p["symbol"] == sym]:
                p = _fresh(pid)
                if p:
                    check(p, px, stale=False)
        except Exception:                                   # noqa: BLE001
            log.warning("daybook: tick pass failed", exc_info=True)


def check(p: dict, px: float, *, stale: bool, now: datetime | None = None) -> dict | None:
    """One position against one price: trail, stop, target, then the group's
    rules. Returns the close if it closed."""
    now = now or _now_ist()
    long = p["side"] == "long"
    best = p["best_px"] or p["entry_px"]
    best = max(best, px) if long else min(best, px)
    stop = p["stop_px"]
    if p["trail_pct"]:
        f = p["trail_pct"] / 100
        trail = best * (1 - f) if long else best * (1 + f)
        stop = trail if stop is None else (max(stop, trail) if long else min(stop, trail))
    with ds._users_lock:
        _db().execute("UPDATE day_positions SET mark_px=?, mark_ts=?, stale=?, best_px=?, stop_px=? WHERE id=?",
                      (px, int(time.time()), int(stale), best, stop, p["id"]))
        _db().commit()
    p.update(mark_px=px, best_px=best, stop_px=stop)
    if stale:
        return None                    # an old price must not trigger an exit
    if stop is not None and (px <= stop if long else px >= stop):
        why = "trailing stop" if p["trail_pct"] else "stop"
        res = close(p, px, why)
        if res:
            _after_leg_stop(p)
        return res
    if p["target_px"] is not None and (px >= p["target_px"] if long else px <= p["target_px"]):
        return close(p, px, "target")
    return None


def _after_leg_stop(p: dict) -> None:
    rule = p["rules"].get("on_leg_stop")
    if not rule:
        return
    others = [o for o in open_positions(play_id=p["play_id"]) if o["grp"] == p["grp"]]
    if rule == "exit_all":
        for o in others:
            close(o, o["mark_px"] or o["entry_px"], "another leg was stopped")
    elif rule == "move_others_to_cost":
        with ds._users_lock:
            for o in others:
                _db().execute("UPDATE day_positions SET stop_px=?, trail_pct=NULL WHERE id=?",
                              (o["entry_px"], o["id"]))
            _db().commit()
        if on_event and p["play_id"]:
            on_event(p["play_id"], "note", "Stop moved to cost",
                     {"why": f"{p['symbol']} was stopped, so the other leg's stop is now its entry price",
                      "legs": [o["symbol"] for o in others]})


def group_pnl(grp: str) -> float:
    with ds._users_lock:
        rows = _db().execute(f"SELECT {', '.join(_COLS)} FROM day_positions WHERE grp=?", (grp,)).fetchall()
    tot = 0.0
    for t in rows:
        p = _pos(t)
        if p["state"] == "closed":
            tot += p["pnl"] or 0
        else:
            tot += _gross(p, p["mark_px"] or p["entry_px"]) - p["costs"]
    return tot


def sweep(now: datetime | None = None) -> list[dict]:
    """Every open position: mark derivatives from quotes, then run the time
    rules: square-off, a group's exit time, its profit or loss limits, and
    expiry. Called every few seconds by the monitor."""
    now = now or _now_ist()
    out = []
    opens = open_positions()
    derivs = [p for p in opens if p["venue"] == "NFO"]
    if derivs:
        got = _quote([f"NFO:{p['symbol']}" for p in derivs]) or {}
        for p in derivs:
            q = got.get(f"NFO:{p['symbol']}")
            p = _fresh(p["id"])          # an earlier leg's stop may have moved this one
            if not p:
                continue
            if q and q.get("last_price"):
                r = check(p, float(q["last_price"]), stale=False, now=now)
                if r:
                    out.append(r)
            elif not p["stale"]:
                with ds._users_lock:
                    _db().execute("UPDATE day_positions SET stale=1 WHERE id=?", (p["id"],))
                    _db().commit()
    perps = [p for p in open_positions() if p["kind"] == "perp"]
    if perps:
        out += _sweep_perps(perps, now)
    groups: dict[str, list[dict]] = {}
    for p in open_positions():
        groups.setdefault(p["grp"], []).append(p)
    hm = now.hour * 100 + now.minute
    for grp, legs in groups.items():
        r = legs[0]["rules"]
        why = None
        sq = SQUARE_OFF.get(legs[0]["venue"])
        if legs[0]["product"] == "MIS" and sq and hm >= sq[0] * 100 + sq[1]:
            why = f"intraday square-off at {sq[0]:02d}:{sq[1]:02d}"
        elif r.get("exit_at"):
            hh, mm = (int(x) for x in r["exit_at"].split(":"))
            if hm >= hh * 100 + mm:
                why = f"time exit at {r['exit_at']}"
        if not why and (r.get("max_loss_inr") or r.get("target_inr")):
            tot = group_pnl(grp)
            if r.get("max_loss_inr") and tot <= -abs(float(r["max_loss_inr"])):
                why = f"the position lost ₹{abs(tot):,.0f}, past its ₹{float(r['max_loss_inr']):,.0f} limit"
            elif r.get("target_inr") and tot >= float(r["target_inr"]):
                why = f"the position made ₹{tot:,.0f}, its ₹{float(r['target_inr']):,.0f} target"
        if not why and legs[0]["expiry"] and legs[0]["expiry"] < now.strftime("%Y-%m-%d"):
            why = "expired"
        if why:
            for p in legs:
                out.append(close(p, p["mark_px"] or p["entry_px"], why))
    return [o for o in out if o]


MAINT = 0.5      # Hyperliquid's maintenance margin is half the initial margin at max leverage


def liquidation_px(p: dict) -> float | None:
    """Where an isolated perp's margin is gone, roughly: entry moved by the
    margin fraction, less the maintenance share. An estimate, labelled one."""
    lev = float(p.get("leverage") or 1)
    if lev <= 1 and p["side"] == "long":
        return None
    c = hyperliquid.ctx(p["symbol"]) or {}
    mm = MAINT / max(1, c.get("max_leverage") or 1)
    f = 1 / lev - mm
    return round(p["entry_px"] * (1 - f) if p["side"] == "long" else p["entry_px"] * (1 + f), 6)


def _sweep_perps(perps: list[dict], now: datetime) -> list[dict]:
    out = []
    try:
        px = hyperliquid.mids()
    except Exception:                                       # noqa: BLE001
        px = {}
    hour = int(time.time()) // 3600 * 3600
    for p in perps:
        p = _fresh(p["id"])
        if not p:
            continue
        mid = px.get(hyperliquid.coin(p["symbol"]))
        if not mid:
            if not p["stale"]:
                with ds._users_lock:
                    _db().execute("UPDATE day_positions SET stale=1 WHERE id=?", (p["id"],))
                    _db().commit()
            continue
        # funding, charged each hour the position is held: a long pays a
        # positive rate, a short receives it (and the reverse when negative)
        if hour > (p.get("funded_ts") or 0):
            c = hyperliquid.ctx(p["symbol"]) or {}
            rate = c.get("funding") or 0.0
            hours = max(1, (hour - (p.get("funded_ts") or hour)) // 3600)
            paid = rate * hours * p["qty"] * mid * _fx(p) * (1 if p["side"] == "long" else -1)
            with ds._users_lock:
                _db().execute("UPDATE day_positions SET funding=funding+?, funded_ts=? WHERE id=?",
                              (round(paid, 4), hour, p["id"]))
                _db().commit()
            p = _fresh(p["id"])
        liq = liquidation_px(p)
        if liq and (mid <= liq if p["side"] == "long" else mid >= liq):
            out.append(close(p, mid, "liquidated (estimated: the margin was used up)"))
            continue
        r = check(p, mid, stale=False, now=now)
        if r:
            out.append(r)
    return out


def watched() -> set[str]:
    with _LOCK:
        return {s for s in _BY_SYM if hyperliquid.is_perp(s)}


def _sweeper() -> None:
    while not _STOP.is_set():
        try:
            sweep()
        except Exception:                                   # noqa: BLE001
            log.warning("daybook: sweep failed", exc_info=True)
        _STOP.wait(5)


def start() -> dict:
    init_db()
    _reindex()
    if not _THREADS or not all(t.is_alive() for t in _THREADS):
        _STOP.clear()
        _THREADS[:] = [threading.Thread(target=_tick_worker, name="daybook-ticks", daemon=True),
                       threading.Thread(target=_sweeper, name="daybook-sweep", daemon=True)]
        for t in _THREADS:
            t.start()
    ds.register_bar_hook(on_tick)
    with _LOCK:
        return {"open": sum(len(v) for v in _BY_SYM.values())}


def stop() -> None:
    _STOP.set()


# ══ HTTP ════════════════════════════════════════════════════════════════════

def api_list(uid: int) -> tuple[int, dict]:
    init_db()
    with ds._users_lock:
        rows = _db().execute(f"SELECT {', '.join(_COLS)} FROM day_positions WHERE user_id=? "
                             "ORDER BY entry_ts DESC LIMIT 200", (uid,)).fetchall()
    pos = [_pos(t) for t in rows]
    today = _now_ist().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    for p in pos:
        if p["state"] == "open":
            p["open_pnl"] = round(_gross(p, p["mark_px"] or p["entry_px"]) - p["costs"]
                                  - float(p.get("funding") or 0), 2)
            if p["kind"] == "perp":
                p["liq_px"] = liquidation_px(p)
    day = sum((p["pnl"] or 0) for p in pos if p["state"] == "closed" and (p["exit_ts"] or 0) >= today) + \
        sum(p.get("open_pnl", 0) for p in pos if p["state"] == "open")
    return 200, {"positions": pos, "day_pnl": round(day, 2), "estimates": "costs and margin are estimates"}


def api_close(uid: int, pos_id: int) -> tuple[int, dict]:
    p = next((p for p in open_positions(uid) if p["id"] == pos_id), None)
    if not p:
        return 404, {"error": "no open position with that id"}
    return 200, close(p, p["mark_px"] or p["entry_px"], "closed by you")
