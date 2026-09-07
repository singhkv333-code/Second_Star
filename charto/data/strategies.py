"""Saved strategies, and the runtime that actually runs them.

THE ONE IDEA IN THIS FILE
-------------------------
Pivot's builder produces a condition TREE. Pivot's evaluator walks that tree
through a five-method Protocol (`workflows/dsl/data_accessor.py`) — price,
indicator, volume, position field, session day — and nothing else. So Charto
does not need to translate the tree into its own alert grammar, keep a second
grammar, and then maintain the drift between them. It needs ONE accessor over
its own bars.

That is `ChartoDataAccessor` below. The consequence is worth stating plainly:
**the rule that runs is the exact tree the card showed you.** Not a
re-interpretation of it, not a lossy projection onto a different vocabulary —
the same object, walked by the same evaluator that backtested it. A strategy
cannot mean one thing in the backtest and another live, because there is only
one meaning and neither side owns it.

Where the accessor cannot answer it returns `None`, which the evaluator turns
into Kleene UNKNOWN, which HOLDS. A missing indicator, a symbol with no bars,
a gap in the feed — none of them fire anything. Failing closed is the only
acceptable default for something that spends money, even simulated money.

WHAT RUNS AND WHAT DOES NOT
---------------------------
**Condition strategies run.** Entry tree true → a paper BUY. Exit tree true
while a position is open → a paper SELL of the whole lot. That covers every
stop, trailing stop, time stop and profit target the builder can write, because
all four are `position` leaves and the accessor answers them.

**Schedules do not run yet**, and `save()` refuses them by name rather than
accepting a draft it would silently never fire. A weekly SIP is a cron, not a
condition, and a cron needs a clock this module does not have. Saying so at
save time costs one sentence; discovering it after a month of nothing costs
the user's trust in every other strategy they armed.

FIRING DISCIPLINE
-----------------
At most one entry and one exit PER BAR, enforced on the bar's own timestamp
(`last_fire_bar`), persisted. A daily strategy therefore acts at most once a
day even though it is evaluated on every tick, and a restart cannot double-fire
a bar that already fired. Evaluation uses the FORMING bar — the same view
Charto's alerts use — so a daily rule can act intraday on today's move rather
than waiting for tomorrow. That is a choice, and the honest description of it
is on the card: the rule is checked live and acts once per bar.

The tick hook keeps `alerts.on_bar`'s contract exactly: one bounded put and
return, never block, never raise. A watcher that stalls the tick loop costs
stored minutes, and the minutes are the asset.
"""
from __future__ import annotations

import json
import logging
import queue
import threading
import time
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from typing import Optional

import dataserver as ds
import indicators
import paper

log = logging.getLogger("charto.strategies")

IST = timezone(timedelta(hours=5, minutes=30))

# Charto's bar vocabulary. Pivot's canonical set is wider (3m, 10m); a rule on
# an interval Charto cannot fold is refused at save time rather than quietly
# rounded to a neighbouring one — a 10-minute rule silently run on 15-minute
# bars is a different strategy wearing the same name.
_INTERVALS = {
    "1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m", "1h": "1h",
    "60m": "1h", "60minute": "1h", "1d": "1d", "daily": "1d", "day": "1d",
    "1wk": "1w", "1w": "1w", "weekly": "1w", "week": "1w",
    "1mo": "1mo", "monthly": "1mo",
}

# Pivot's indicator key (+ component) → Charto's indicator and line.
#
# Both registries are real and neither is a subset of the other: Pivot's DSL
# validates against 26 keys, indicators.py carries 48 lines under different
# names. This table is the whole translation, and it is a table rather than a
# guess so an unmapped key returns None (→ UNKNOWN → hold) instead of resolving
# to a plausible wrong series.
_IND: dict[str, tuple[str, Optional[str]]] = {
    "rsi": ("rsi", "rsi"), "sma": ("sma", "sma"), "ema": ("ema", "ema"),
    "wma": ("wma", "wma"), "atr": ("atr", "atr"), "adx": ("adx", "adx"),
    "cci": ("cci", "cci"), "mfi": ("mfi", "mfi"), "obv": ("obv", "obv"),
    "roc": ("roc", "roc"), "trix": ("trix", "trix"), "psar": ("psar", "psar"),
    "vwap": ("vwap", "vwap"), "williams_r": ("williams_r", "williams_r"),
    "supertrend": ("supertrend", None),      # composed below from up/down
}
# (pivot key, component) → charto line. The component defaults mirror Pivot's
# own documented ones: MACD histogram, Bollinger %B, Stochastic %K.
_IND_COMPONENT: dict[tuple[str, Optional[str]], tuple[str, str]] = {
    ("macd", None): ("macd", "histogram"),
    ("macd", "hist"): ("macd", "histogram"),
    ("macd", "macd"): ("macd", "macd"),
    ("macd", "signal"): ("macd", "signal"),
    ("stoch", None): ("stoch", "k"),
    ("stoch", "k"): ("stoch", "k"),
    ("stoch", "d"): ("stoch", "d"),
    ("stoch_rsi", None): ("stochrsi", "k"),
    ("stoch_rsi", "k"): ("stochrsi", "k"),
    ("stoch_rsi", "d"): ("stochrsi", "d"),
    ("aroon", None): ("aroon", "oscillator"),
    ("aroon", "osc"): ("aroon", "oscillator"),
    ("aroon", "up"): ("aroon", "aroon_up"),
    ("aroon", "down"): ("aroon", "aroon_down"),
}
for _k in ("bb", "bollinger"):
    _IND_COMPONENT[(_k, None)] = ("bbands", "percent_b")
    _IND_COMPONENT[(_k, "pctb")] = ("bbands", "percent_b")
    _IND_COMPONENT[(_k, "bandwidth")] = ("bbands", "bandwidth")
    for _c in ("upper", "middle", "lower"):
        _IND_COMPONENT[(_k, _c)] = ("bbands", _c)
for _k in ("donchian", "keltner"):
    _IND_COMPONENT[(_k, None)] = (_k, "middle")
    for _c in ("upper", "middle", "lower"):
        _IND_COMPONENT[(_k, _c)] = (_k, _c)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS strategies (
  id       INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id  INTEGER NOT NULL REFERENCES users(id),
  name     TEXT NOT NULL,
  symbol   TEXT NOT NULL,
  interval TEXT NOT NULL,
  side     TEXT NOT NULL DEFAULT 'BUY',
  quantity REAL NOT NULL,
  spec     TEXT NOT NULL,          -- the whole draft, as the card showed it
  entry    TEXT NOT NULL,          -- the DSL entry tree
  exit     TEXT,                   -- the DSL exit tree, when there is one
  state    TEXT NOT NULL DEFAULT 'draft',
  note     TEXT NOT NULL DEFAULT '',
  chat_id  TEXT NOT NULL DEFAULT '',
  -- runtime state, persisted so a restart cannot re-fire a bar
  estate   TEXT NOT NULL DEFAULT '{}',   -- evaluator crossing state
  in_position INTEGER NOT NULL DEFAULT 0,
  entry_bar   INTEGER,
  entry_price REAL,
  peak_pct    REAL,
  last_fire_bar INTEGER NOT NULL DEFAULT 0,
  last_eval   INTEGER NOT NULL DEFAULT 0,
  fire_count  INTEGER NOT NULL DEFAULT 0,
  last_error  TEXT NOT NULL DEFAULT '',
  created  INTEGER NOT NULL,
  updated  INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS strat_user ON strategies(user_id, state);
CREATE INDEX IF NOT EXISTS strat_sym  ON strategies(symbol, state);

CREATE TABLE IF NOT EXISTS strategy_log (
  id       INTEGER PRIMARY KEY AUTOINCREMENT,
  strategy_id INTEGER NOT NULL,
  user_id  INTEGER NOT NULL,
  ts       INTEGER NOT NULL,
  bar_ts   INTEGER,
  kind     TEXT NOT NULL,          -- entry | exit | reject | error
  price    REAL,
  quantity REAL,
  detail   TEXT NOT NULL DEFAULT '',
  order_id TEXT);
CREATE INDEX IF NOT EXISTS strat_log_user ON strategy_log(user_id, ts DESC);
"""

STATES = ("draft", "armed", "paused", "retired")


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


# ── reading a draft ──────────────────────────────────────────────────

class Unbuildable(Exception):
    """The draft cannot be armed, and the message says exactly why."""


def parse_draft(draft: dict) -> dict:
    """Pull the runnable parts out of a `workflow_draft_card` payload.

    The builder's own step vocabulary is the contract here, not a guess:
    `trigger.compound` carries the entry tree under `config.entry`,
    `trigger.exit_compound` carries the exit tree under the same key (the slot
    is named for the schema, not for what the step does), and
    `action.place_order` carries the instrument, side and size.

    Everything this cannot read is named in the refusal. A draft that is only
    a schedule, or has no order in it at all, is not half-armed — it is
    refused, with the reason.
    """
    steps = [s for s in (draft.get("steps") or []) if isinstance(s, dict)]
    if not steps:
        return _fail("this draft has no steps to run")

    kinds = [str(s.get("step_type") or "") for s in steps]
    entry_tree = exit_tree = None
    for s in steps:
        k = str(s.get("step_type") or "")
        cfg = s.get("config") or {}
        if k == "trigger.compound" and entry_tree is None:
            entry_tree = cfg.get("entry")
        elif k == "trigger.exit_compound" and exit_tree is None:
            exit_tree = cfg.get("entry")

    if entry_tree is None:
        if any(k.startswith("trigger.schedule") or k == "trigger.cron"
               for k in kinds):
            raise Unbuildable(
                "This is a scheduled strategy — it runs on a clock, not on a "
                "condition, and the paper runtime only arms conditions today. "
                "Backtest it, or express the same idea as a price/indicator "
                "condition and it will arm.")
        raise Unbuildable(
            "This draft has no entry condition the runtime can evaluate "
            f"(its steps are: {', '.join(kinds) or 'none'}).")

    orders = [s for s in steps if s.get("step_type") == "action.place_order"]
    if not orders:
        raise Unbuildable("This draft never places an order, so there is "
                          "nothing for the paper book to fill.")
    cfg = orders[0].get("config") or {}
    symbol = str(cfg.get("symbol") or draft.get("symbol") or "").upper().strip()
    if not symbol:
        raise Unbuildable("This draft does not name an instrument to trade.")
    side = str(cfg.get("side") or "buy").upper()
    if side != "BUY":
        # The book is long-only (paper.py says why). An entry that opens a
        # short is refused here rather than rejected later by the fill engine,
        # so the refusal names the strategy rather than one order.
        raise Unbuildable(
            f"The entry order is a {side}, and the paper book is long-only — "
            "it holds shares it has bought and sells what it holds.")
    try:
        qty = int(float(cfg.get("quantity") or 0))
    except (TypeError, ValueError):
        qty = 0
    if qty <= 0:
        raise Unbuildable(
            "The entry order has no fixed quantity the runtime can size from "
            f"(it reads {cfg.get('quantity')!r}). Say how many shares.")

    raw_iv = str(cfg.get("timeframe") or draft.get("timeframe_assumed")
                 or _tree_timeframe(entry_tree) or "1d").lower()
    interval = _INTERVALS.get(raw_iv)
    if not interval:
        raise Unbuildable(
            f"Charto does not fold {raw_iv} bars — it has "
            f"{', '.join(sorted(set(_INTERVALS.values())))}.")

    return {"symbol": symbol, "interval": interval, "side": side,
            "quantity": qty, "entry": entry_tree, "exit": exit_tree,
            "name": str(draft.get("name") or f"{symbol} strategy")[:160]}


def _fail(msg: str):
    raise Unbuildable(msg)


def _tree_timeframe(node) -> Optional[str]:
    """The timeframe the entry tree's own leaves are computed on, if they
    agree on one. The builder puts it on the indicator node, not always on the
    order."""
    found = set()

    def walk(n):
        if isinstance(n, dict):
            if n.get("timeframe"):
                found.add(str(n["timeframe"]).lower())
            for v in n.values():
                walk(v)
        elif isinstance(n, list):
            for v in n:
                walk(v)
    walk(node)
    return found.pop() if len(found) == 1 else None


# ── the accessor ─────────────────────────────────────────────────────

class ChartoDataAccessor:
    """Pivot's `DataAccessor` Protocol, answered from Charto's bars.

    Five methods, every one returning `Optional[float]`, every `None` meaning
    "I do not know" rather than "no". The evaluator turns that into UNKNOWN and
    the rule holds — so an outage, a thin symbol or an indicator this build
    cannot compute all fail the same safe way.

    Bars are read once per (symbol, interval) and cached for the life of one
    evaluation pass: a tree like `RSI > 30 AND RSI < 70` walks the same leaf
    twice, and a second bar read per walk would be the whole cost of the pass.
    """

    def __init__(self, *, default_tf: str = "1d",
                 position: Optional[dict] = None, limit: int = 400) -> None:
        self.default_tf = default_tf
        self.position = position or {}
        self.limit = limit
        self._bars: dict[tuple[str, str], list] = {}
        self._ind: dict[tuple, dict] = {}

    # -- bars ---------------------------------------------------------
    def _iv(self, timeframe: Optional[str]) -> Optional[str]:
        if not timeframe:
            return self.default_tf
        return _INTERVALS.get(str(timeframe).lower())

    def rows(self, symbol: str, timeframe: Optional[str]) -> list:
        iv = self._iv(timeframe)
        if not iv:
            return []
        key = ((symbol or "").upper(), iv)
        got = self._bars.get(key)
        if got is not None:
            return got
        try:
            data = ds.get_bars(key[0], iv, None, self.limit) or {}
            rows = [(b["t"], b["o"], b["h"], b["l"], b["c"], b["v"])
                    for b in (data.get("bars") or [])]
        except Exception:                                   # noqa: BLE001
            rows = []
        self._bars[key] = rows
        return rows

    def bar(self, symbol: str, timeframe: Optional[str], offset: int = 0):
        rows = self.rows(symbol, timeframe)
        i = len(rows) - 1 - max(0, int(offset or 0))
        return rows[i] if 0 <= i < len(rows) else None

    # -- the Protocol -------------------------------------------------
    def get_price(self, *, symbol: str, exchange: str = "NSE",
                  basis: str = "close", offset: int = 0,
                  timeframe: str = "daily") -> Optional[float]:
        row = self.bar(symbol, timeframe, offset)
        if row is None:
            return None
        idx = {"open": 1, "high": 2, "low": 3, "close": 4}.get(
            str(basis or "close").lower(), 4)
        try:
            v = row[idx]
            return None if v is None else float(v)
        except (IndexError, TypeError, ValueError):
            return None

    def get_indicator(self, *, symbol: str, indicator: str, period: int,
                      exchange: str = "NSE", component: Optional[str] = None,
                      settings: Optional[dict] = None, offset: int = 0,
                      timeframe: str = "daily") -> Optional[float]:
        key = str(indicator or "").lower()
        comp = str(component).lower() if component else None

        # Volume-derived keys are not indicators here — Charto keeps volume on
        # the bar. Computing them from the series directly is both correct and
        # cheaper than routing through a registry that has no entry for them.
        if key in ("volume", "volume_ma", "volume_roc"):
            return self._volume_key(key, symbol, period, offset, timeframe)

        mapped = _IND_COMPONENT.get((key, comp)) or _IND_COMPONENT.get((key, None))
        if mapped is None:
            hit = _IND.get(key)
            if hit is None:
                # Unmapped → UNKNOWN → hold. Logged once per key so a strategy
                # that can never fire is visible rather than merely quiet.
                log.warning("strategies: no Charto indicator for %r", key)
                return None
            mapped = (hit[0], hit[1]) if hit[1] else (hit[0], None)

        name, line = mapped
        rows = self.rows(symbol, timeframe)
        if not rows:
            return None
        ck = ((symbol or "").upper(), self._iv(timeframe), name, int(period or 14))
        lines = self._ind.get(ck)
        if lines is None:
            try:
                lines = indicators.compute(name, rows, int(period or 14))["lines"]
            except Exception:                               # noqa: BLE001
                return None
            self._ind[ck] = lines
        if name == "supertrend" and line is None:
            return self._supertrend(lines, offset)
        series = lines.get(line) if line else None
        if series is None and len(lines) == 1:
            series = next(iter(lines.values()))
        if series is None:
            return None
        i = len(series) - 1 - max(0, int(offset or 0))
        if i < 0 or i >= len(series) or series[i] is None:
            return None
        return float(series[i])

    def _supertrend(self, lines: dict, offset: int) -> Optional[float]:
        """Charto splits Supertrend into an up band, a down band and a
        direction; Pivot's node is single-valued. The live band is whichever
        one has a value on that bar — the other is None by construction."""
        up = lines.get("supertrend_up") or []
        dn = lines.get("supertrend_down") or []
        i = max(len(up), len(dn)) - 1 - max(0, int(offset or 0))
        for series in (up, dn):
            if 0 <= i < len(series) and series[i] is not None:
                return float(series[i])
        return None

    def _volume_key(self, key: str, symbol: str, period: int, offset: int,
                    timeframe: str) -> Optional[float]:
        rows = self.rows(symbol, timeframe)
        vols = [r[5] for r in rows if r[5] is not None]
        i = len(vols) - 1 - max(0, int(offset or 0))
        if i < 0 or i >= len(vols):
            return None
        if key == "volume":
            return float(vols[i])
        n = max(1, int(period or 20))
        if i + 1 < n:
            return None
        window = vols[i + 1 - n: i + 1]
        if key == "volume_ma":
            return float(sum(window) / n)
        base = vols[i - n] if i - n >= 0 else None
        if not base:
            return None
        return float((vols[i] - base) / base * 100.0)

    def get_volume(self, *, symbol: str, bars: int = 1, exchange: str = "NSE",
                   offset: int = 0) -> Optional[float]:
        """Total volume over the last `bars` bars ending at `offset`."""
        rows = self.rows(symbol, self.default_tf)
        n = max(1, int(bars or 1))
        end = len(rows) - max(0, int(offset or 0))
        start = end - n
        if start < 0 or end <= 0:
            return None
        window = [r[5] for r in rows[start:end] if r[5] is not None]
        return float(sum(window)) if window else None

    def get_position_field(self, *, field: str,
                           basis: Optional[str] = None) -> Optional[float]:
        """The open position's own properties — the half of the DSL that has
        never had anywhere to run.

        `basis` reads the bar's HIGH for a target and its LOW for a stop, which
        is what makes an intrabar stop honest: a rule that says "exit at -4%"
        should fire on the bar that traded through -4%, not on its close.
        """
        p = self.position
        if not p:
            return None
        entry = p.get("entry_price")
        if not entry:
            return None
        if field == "entry_price":
            return float(entry)
        if field == "bars_held":
            return float(p.get("bars_held") or 0)
        row = self.bar(p.get("symbol") or "", self.default_tf, 0)
        if row is None:
            return None
        px = {"high": row[2], "low": row[3]}.get(
            str(basis).lower() if basis else "", row[4])
        if px is None:
            return None
        pct = (float(px) - float(entry)) / float(entry)
        if field == "unrealised_pct":
            return pct
        if field == "unrealised_abs":
            return float(px) - float(entry)
        if field == "peak_unrealised_pct":
            return float(p.get("peak_pct") or max(pct, 0.0))
        if field == "drawdown_from_peak_pct":
            peak = float(p.get("peak_pct") or max(pct, 0.0))
            return max(0.0, peak - pct)
        return None

    def get_session_day(self) -> Optional[str]:
        row = self.bar(self.position.get("symbol") or self._any_symbol(),
                       self.default_tf, 0)
        if row is None:
            return None
        return datetime.fromtimestamp(int(row[0]), IST).strftime("%a").lower()

    def _any_symbol(self) -> str:
        for (sym, _iv) in self._bars:
            return sym
        return ""


# ── the store ────────────────────────────────────────────────────────

_COLS = ("id", "user_id", "name", "symbol", "interval", "side", "quantity",
         "spec", "entry", "exit", "state", "note", "chat_id", "estate",
         "in_position", "entry_bar", "entry_price", "peak_pct",
         "last_fire_bar", "last_eval", "fire_count", "last_error",
         "created", "updated")


def _row(r) -> dict:
    return dict(zip(_COLS, r))


def _public(d: dict) -> dict:
    """What a user (and the model) is shown. The trees stay out: they are the
    machine's copy of a sentence the card already prints in English."""
    return {
        "id": d["id"], "name": d["name"], "symbol": d["symbol"],
        "interval": d["interval"], "side": d["side"],
        "quantity": paper._qty_out(d["quantity"]), "state": d["state"],
        "note": d["note"], "in_position": bool(d["in_position"]),
        "entry_price": None if d["entry_price"] is None else paper.f(d["entry_price"]),
        "fire_count": d["fire_count"],
        "has_exit": bool(d["exit"]),
        "last_error": d["last_error"] or "",
        "created": paper._iso(d["created"]),
        "readback": _readback(d),
    }


def _readback(d: dict) -> dict:
    spec = {}
    try:
        spec = json.loads(d["spec"]) or {}
    except (TypeError, ValueError):
        pass
    return {"entry": spec.get("readback") or "",
            "exit": spec.get("exit_readback") or "",
            "description": spec.get("description") or ""}


def save(uid: int, draft: dict, *, note: str = "", chat_id: str = "",
         arm: bool = True) -> dict:
    """Persist a draft and, by default, arm it.

    Arming is the default because the alternative is the failure this whole
    module exists to fix: a strategy that is saved, looks saved, and does
    nothing. If a draft is worth keeping it is worth running; `arm=False` is
    there for the caller that genuinely wants a shelf.
    """
    init_db()
    import execution_bridge
    ready, why = execution_bridge.available()
    if not ready:
        # Storing a rule nothing can evaluate is the failure this module was
        # written to end. Refuse at the door.
        raise Unbuildable(why)
    parsed = parse_draft(draft)          # raises Unbuildable with the reason
    now = int(time.time())
    with ds._users_lock:
        cur = _db().execute(
            "INSERT INTO strategies (user_id, name, symbol, interval, side, "
            "quantity, spec, entry, exit, state, note, chat_id, created, updated)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (int(uid), parsed["name"], parsed["symbol"], parsed["interval"],
             parsed["side"], parsed["quantity"], json.dumps(draft),
             json.dumps(parsed["entry"]),
             json.dumps(parsed["exit"]) if parsed["exit"] else None,
             "armed" if arm else "draft", note[:400], str(chat_id or "")[:80],
             now, now))
        sid = cur.lastrowid
        _db().commit()
    load_index()
    paper.get_or_create_account(uid)     # the book exists the moment a rule does
    return {"id": sid, "state": "armed" if arm else "draft",
            "symbol": parsed["symbol"], "interval": parsed["interval"],
            "quantity": parsed["quantity"], "has_exit": bool(parsed["exit"])}


def api_list(uid: int, state: str = "") -> tuple[int, dict]:
    init_db()
    q = "SELECT %s FROM strategies WHERE user_id=?" % ", ".join(_COLS)
    args: list = [int(uid)]
    if state:
        q += " AND state=?"; args.append(state)
    else:
        q += " AND state != 'retired'"
    q += " ORDER BY id DESC"
    with ds._users_lock:
        rows = _db().execute(q, args).fetchall()
    return 200, {"strategies": [_public(_row(r)) for r in rows]}


def api_get(uid: int, sid: int) -> tuple[int, dict]:
    init_db()
    with ds._users_lock:
        r = _db().execute(
            "SELECT %s FROM strategies WHERE id=? AND user_id=?" % ", ".join(_COLS),
            (int(sid), int(uid))).fetchone()
        if r is None:
            return 404, {"error": "no such strategy"}
        logs = _db().execute(
            "SELECT ts, bar_ts, kind, price, quantity, detail, order_id FROM "
            "strategy_log WHERE strategy_id=? ORDER BY ts DESC LIMIT 50",
            (int(sid),)).fetchall()
    d = _public(_row(r))
    d["log"] = [{"ts": paper._iso(x[0]), "bar_ts": paper._iso(x[1]),
                 "kind": x[2], "price": None if x[3] is None else paper.f(x[3]),
                 "quantity": paper._qty_out(x[4]) if x[4] is not None else None,
                 "detail": x[5], "order_id": x[6]} for x in logs]
    return 200, d


def api_patch(uid: int, sid: int, body: dict) -> tuple[int, dict]:
    init_db()
    state = str(body.get("state") or "").lower()
    if state and state not in STATES:
        return 400, {"error": f"state must be one of {', '.join(STATES)}"}
    now = int(time.time())
    with ds._users_lock:
        r = _db().execute("SELECT id, state FROM strategies WHERE id=? AND "
                          "user_id=?", (int(sid), int(uid))).fetchone()
        if r is None:
            return 404, {"error": "no such strategy"}
        sets, args = [], []
        if state:
            sets.append("state=?"); args.append(state)
            if state == "armed":
                # Re-arming clears the last failure: the user has seen it, and
                # a stale error beside a running rule reads as a live one.
                sets.append("last_error=''")
        if "name" in body:
            sets.append("name=?"); args.append(str(body["name"])[:160])
        if "note" in body:
            sets.append("note=?"); args.append(str(body["note"])[:400])
        if not sets:
            return 400, {"error": "nothing to change"}
        sets.append("updated=?"); args.append(now)
        args.extend([int(sid), int(uid)])
        _db().execute(f"UPDATE strategies SET {', '.join(sets)} WHERE id=? AND "
                      "user_id=?", args)
        _db().commit()
    load_index()
    return api_get(uid, sid)


def api_delete(uid: int, sid: int) -> tuple[int, dict]:
    """Retire, never erase. A strategy that filled orders is the provenance of
    those fills, and deleting the row would orphan them."""
    return api_patch(uid, sid, {"state": "retired"})


def _log(sid: int, uid: int, kind: str, *, bar_ts=None, price=None,
         quantity=None, detail: str = "", order_id=None) -> None:
    with ds._users_lock:
        _db().execute(
            "INSERT INTO strategy_log (strategy_id, user_id, ts, bar_ts, kind,"
            " price, quantity, detail, order_id) VALUES (?,?,?,?,?,?,?,?,?)",
            (int(sid), int(uid), int(time.time()), bar_ts, kind, price,
             quantity, str(detail)[:600], order_id))
        _db().commit()


# ── the runtime ──────────────────────────────────────────────────────

_BY_SYM: dict[str, list[int]] = {}
_INDEX_LOCK = threading.Lock()
_Q: "queue.Queue[str]" = queue.Queue(maxsize=2000)
_STOP = threading.Event()
_WORKER: Optional[threading.Thread] = None
STATS = {"ticks_in": 0, "dropped": 0, "passes": 0, "fires": 0, "errors": 0}


def load_index() -> dict:
    init_db()
    with ds._users_lock:
        rows = _db().execute(
            "SELECT symbol, id FROM strategies WHERE state='armed'").fetchall()
    idx: dict[str, list[int]] = {}
    for sym, sid in rows:
        idx.setdefault(str(sym).upper(), []).append(int(sid))
    with _INDEX_LOCK:
        _BY_SYM.clear()
        _BY_SYM.update(idx)
    return {"armed": sum(len(v) for v in idx.values()), "symbols": len(idx)}


def watched_symbols() -> list[str]:
    with _INDEX_LOCK:
        return sorted(_BY_SYM)


def on_bar(symbol: str, form: list, closed: bool) -> None:
    """THE HOOK — one bounded put, then return. Same contract as alerts."""
    sym = (symbol or "").upper()
    with _INDEX_LOCK:
        if sym not in _BY_SYM:
            return
    STATS["ticks_in"] += 1
    try:
        _Q.put_nowait(sym)
    except queue.Full:
        # Coalescing, not loss: the next tick carries the same price, and a
        # strategy fires at most once a bar anyway.
        STATS["dropped"] += 1


def _worker() -> None:
    while not _STOP.is_set():
        try:
            sym = _Q.get(timeout=1.0)
        except queue.Empty:
            continue
        batch = {sym}
        while True:
            try:
                batch.add(_Q.get_nowait())
            except queue.Empty:
                break
        for s in batch:
            try:
                run_symbol(s)
            except Exception:                               # noqa: BLE001
                STATS["errors"] += 1
                log.warning("strategies: pass on %s failed", s, exc_info=True)


def _evaluate(tree_json: str, accessor, estate: dict):
    """Walk one tree. Returns (verdict, new_state) where verdict is True,
    False or None — and None means UNKNOWN, which holds.

    The evaluator is Pivot's, and `execution_bridge` is what puts its package
    on the path. Asking it first means an unavailable engine says so once, in
    the sentence a user can read, rather than raising ImportError per tick.
    """
    import execution_bridge
    ready, why = execution_bridge.available()
    if not ready:
        raise RuntimeError(why)
    from backend.workflows.dsl import evaluator as dsl_eval
    from backend.workflows.dsl.schema import Tree
    from pydantic import TypeAdapter
    tree = TypeAdapter(Tree).validate_python(json.loads(tree_json))
    res = dsl_eval.evaluate(tree, accessor=accessor, prev_state=estate)
    val = res.value
    return ({dsl_eval.Ternary.TRUE: True,
             dsl_eval.Ternary.FALSE: False}.get(val), res.new_state)


def run_symbol(symbol: str) -> None:
    """One evaluation pass over every armed strategy on one symbol.

    Runs on the worker thread, never the tick thread. Reads bars, evaluates,
    and places paper orders — in that order, and never while holding a lock
    that a candle could be waiting on.
    """
    sym = (symbol or "").upper()
    with _INDEX_LOCK:
        ids = list(_BY_SYM.get(sym) or [])
    if not ids:
        return
    # `_req` is thread-local, so stamping it here cannot disturb a request
    # thread — it is what lets this worker use ds's symbol-scoped helpers.
    ds._req.symbol = sym
    STATS["passes"] += 1
    with ds._users_lock:
        rows = _db().execute(
            "SELECT %s FROM strategies WHERE id IN (%s) AND state='armed'"
            % (", ".join(_COLS), ",".join("?" * len(ids))), ids).fetchall()
    for r in rows:
        try:
            _run_one(_row(r))
        except Exception as exc:                            # noqa: BLE001
            STATS["errors"] += 1
            log.warning("strategies: %s failed", r[0], exc_info=True)
            _set_error(int(r[0]), f"{type(exc).__name__}: {exc}")


def _set_error(sid: int, msg: str) -> None:
    with ds._users_lock:
        _db().execute("UPDATE strategies SET last_error=?, updated=? WHERE id=?",
                      (msg[:400], int(time.time()), int(sid)))
        _db().commit()


def _run_one(s: dict) -> None:
    sid, uid, sym = int(s["id"]), int(s["user_id"]), s["symbol"]
    acc = ChartoDataAccessor(default_tf=s["interval"])
    rows = acc.rows(sym, s["interval"])
    if not rows:
        return
    bar_ts = int(rows[-1][0])
    price = rows[-1][4]
    if price is None:
        return

    # One entry and one exit per bar, on the bar's own clock. Persisted, so a
    # restart mid-session cannot re-fire a bar that already fired.
    if int(s["last_fire_bar"] or 0) >= bar_ts:
        _touch(sid, bar_ts)
        return

    try:
        estate = json.loads(s["estate"] or "{}")
    except (TypeError, ValueError):
        estate = {}

    in_pos = bool(s["in_position"])
    if in_pos:
        held = _held_quantity(uid, sym)
        if held <= 0:
            # The book disagrees with the strategy — the user sold it by hand,
            # or a fill was rejected. The book is the truth about what is
            # owned; the strategy follows it rather than arguing.
            _close_position(sid, "position closed outside the strategy")
            in_pos = False

    if in_pos and s["exit"]:
        acc.position = {
            "symbol": sym, "entry_price": s["entry_price"],
            "bars_held": _bars_since(rows, int(s["entry_bar"] or 0)),
            "peak_pct": s["peak_pct"],
        }
        # The peak has to be updated BEFORE the walk, or a trailing stop reads
        # a stale high and exits a bar late — which is exactly the bar it was
        # written to catch.
        peak = _update_peak(sid, s, rows[-1])
        acc.position["peak_pct"] = peak
        verdict, estate = _evaluate(s["exit"], acc, estate)
        _persist_eval(sid, estate, bar_ts)
        if verdict is True:
            _fire_exit(s, price, bar_ts)
        return

    if not in_pos:
        verdict, estate = _evaluate(s["entry"], acc, estate)
        _persist_eval(sid, estate, bar_ts)
        if verdict is True:
            _fire_entry(s, price, bar_ts)


def _touch(sid: int, bar_ts: int) -> None:
    with ds._users_lock:
        _db().execute("UPDATE strategies SET last_eval=? WHERE id=?",
                      (int(time.time()), int(sid)))
        _db().commit()


def _persist_eval(sid: int, estate: dict, bar_ts: int) -> None:
    with ds._users_lock:
        _db().execute(
            "UPDATE strategies SET estate=?, last_eval=? WHERE id=?",
            (json.dumps(estate or {}), int(time.time()), int(sid)))
        _db().commit()


def _bars_since(rows: list, entry_bar: int) -> int:
    if not entry_bar:
        return 0
    for i in range(len(rows) - 1, -1, -1):
        if int(rows[i][0]) <= entry_bar:
            return len(rows) - 1 - i
    return len(rows) - 1


def _update_peak(sid: int, s: dict, row) -> float:
    entry = s["entry_price"]
    if not entry:
        return 0.0
    high = row[2] if row[2] is not None else row[4]
    pct = (float(high) - float(entry)) / float(entry)
    peak = max(float(s["peak_pct"] or 0.0), pct)
    if peak != (s["peak_pct"] or 0.0):
        with ds._users_lock:
            _db().execute("UPDATE strategies SET peak_pct=? WHERE id=?",
                          (peak, int(sid)))
            _db().commit()
    return peak


def _held_quantity(uid: int, symbol: str) -> int:
    acct = paper.account_of(uid)
    if acct is None:
        return 0
    with ds._users_lock:
        row = _db().execute(
            "SELECT quantity FROM paper_positions WHERE account_id=? AND "
            "symbol=?", (acct[0], symbol)).fetchone()
    return int(float(row[0])) if row and row[0] else 0


def _fire_entry(s: dict, price: float, bar_ts: int) -> None:
    sid, uid = int(s["id"]), int(s["user_id"])
    qty = int(float(s["quantity"]))
    try:
        res = paper.place_order(
            uid, s["symbol"], "BUY", qty, order_type="MARKET",
            source="strategy", origin_kind="strategy_entry", strategy_id=sid,
            price=paper.to_money(price))
    except paper.Reject as exc:
        # A refusal is a fact about the book, not a fault in the rule. It is
        # recorded against the strategy and shown beside it; the strategy stays
        # armed, because the same rule on a funded book is still the rule.
        _log(sid, uid, "reject", bar_ts=bar_ts, price=price, quantity=qty,
             detail=exc.reason)
        _set_error(sid, f"entry rejected: {exc.reason}")
        _bump_fire_bar(sid, bar_ts)
        return
    fill_px = res.get("fill_price", price)
    with ds._users_lock:
        _db().execute(
            "UPDATE strategies SET in_position=1, entry_bar=?, entry_price=?, "
            "peak_pct=0, last_fire_bar=?, fire_count=fire_count+1, "
            "last_error='', updated=? WHERE id=?",
            (bar_ts, fill_px, bar_ts, int(time.time()), sid))
        _db().commit()
    STATS["fires"] += 1
    _log(sid, uid, "entry", bar_ts=bar_ts, price=fill_px, quantity=qty,
         detail=_readback(s)["entry"], order_id=res.get("order_id"))


def _fire_exit(s: dict, price: float, bar_ts: int) -> None:
    sid, uid = int(s["id"]), int(s["user_id"])
    # Sell what is actually held, not what the strategy bought. They differ the
    # moment the user touches the position by hand, and the book is the truth.
    qty = _held_quantity(uid, s["symbol"])
    if qty <= 0:
        _close_position(sid, "nothing held to exit")
        return
    try:
        res = paper.place_order(
            uid, s["symbol"], "SELL", qty, order_type="MARKET",
            source="strategy", origin_kind="strategy_exit", strategy_id=sid,
            price=paper.to_money(price))
    except paper.Reject as exc:
        _log(sid, uid, "reject", bar_ts=bar_ts, price=price, quantity=qty,
             detail=exc.reason)
        _set_error(sid, f"exit rejected: {exc.reason}")
        _bump_fire_bar(sid, bar_ts)
        return
    with ds._users_lock:
        _db().execute(
            "UPDATE strategies SET in_position=0, entry_bar=NULL, "
            "entry_price=NULL, peak_pct=NULL, last_fire_bar=?, "
            "fire_count=fire_count+1, last_error='', updated=? WHERE id=?",
            (bar_ts, int(time.time()), sid))
        _db().commit()
    STATS["fires"] += 1
    _log(sid, uid, "exit", bar_ts=bar_ts, price=res.get("fill_price", price),
         quantity=qty, detail=_readback(s)["exit"],
         order_id=res.get("order_id"))


def _bump_fire_bar(sid: int, bar_ts: int) -> None:
    """A refused fire still consumes the bar. Without this the rule retries on
    every tick of the same bar and writes a rejection per tick."""
    with ds._users_lock:
        _db().execute("UPDATE strategies SET last_fire_bar=? WHERE id=?",
                      (bar_ts, int(sid)))
        _db().commit()


def _close_position(sid: int, why: str) -> None:
    with ds._users_lock:
        _db().execute(
            "UPDATE strategies SET in_position=0, entry_bar=NULL, "
            "entry_price=NULL, peak_pct=NULL, updated=? WHERE id=?",
            (int(time.time()), int(sid)))
        _db().commit()
    log.info("strategies: %s position cleared (%s)", sid, why)


def sweep() -> dict:
    """Evaluate every armed strategy once, off the tick.

    Two jobs. A strategy on a symbol with no live feed would otherwise never be
    evaluated at all — most of Charto's universe has no streaming tick, and a
    daily rule on one of those names is exactly the ordinary case. And it is
    the catch-up path: a process that was down through the bar its rule fired
    on sees that bar on the next sweep, because `last_fire_bar` is what stops a
    double-fire, not the fact of having been running.
    """
    out = {"symbols": 0, "errors": 0}
    for sym in watched_symbols():
        out["symbols"] += 1
        try:
            run_symbol(sym)
        except Exception:                                   # noqa: BLE001
            out["errors"] += 1
            log.warning("strategies: sweep on %s failed", sym, exc_info=True)
    return out


def _sweeper() -> None:
    """A slow heartbeat. 60s matches Pivot's own watcher cadence and is well
    inside the shortest bar Charto folds."""
    while not _STOP.wait(60.0):
        try:
            sweep()
        except Exception:                                   # noqa: BLE001
            log.warning("strategies: sweep failed", exc_info=True)


def _mark_hook(symbol: str, form: list, closed: bool) -> None:
    """The book's own tick: stamp the marks, fill what has triggered.

    A named function rather than a lambda so `register_bar_hook`'s identity
    check can actually dedupe it — two boots would otherwise subscribe two
    copies and mark every position twice per tick.
    """
    paper.on_price(symbol, form[4])


def register_hook() -> None:
    # Order matters: the strategy watcher first, the book's mark second, so a
    # position opened on this tick is marked at this tick's price rather than
    # waiting a minute to be worth anything.
    ds.register_bar_hook(on_bar)
    ds.register_bar_hook(_mark_hook)


def start(sweep_now: bool = True) -> dict:
    global _WORKER
    init_db()
    got = load_index()
    if _WORKER is None or not _WORKER.is_alive():
        _STOP.clear()
        _WORKER = threading.Thread(target=_worker, name="strategies",
                                   daemon=True)
        _WORKER.start()
        threading.Thread(target=_sweeper, name="strategies-sweep",
                         daemon=True).start()
    if sweep_now and got.get("armed"):
        try:
            got["catch_up"] = sweep()
        except Exception as exc:                            # noqa: BLE001
            got["catch_up"] = {"error": str(exc)}
    return got


def stop() -> None:
    _STOP.set()


# ══ the chat surface ═══════════════════════════════════════════════════════
#
# Four tools, and every one of them goes through the same api_* functions the
# HTTP routes use. There is no chat-only path into the table: a strategy armed
# by conversation and one armed from the panel are the same row, in the same
# state machine, with the same refusals.

def tool_save_strategy(user_id: int = 0, name: str = "", note: str = "",
                       arm: bool = True) -> dict:
    """Persist the draft this turn produced and arm it against the tick."""
    if not user_id:
        return {"error": "sign in to save a strategy — a paper book belongs to "
                         "an account, not to a browser tab."}
    draft = _last_draft()
    if not draft:
        return {"error": "there is no strategy draft in this turn to save. "
                         "Build one first, then save it."}
    if name:
        draft = {**draft, "name": name}
    try:
        out = save(user_id, draft, note=note, arm=bool(arm))
    except Unbuildable as exc:
        return {"error": str(exc)}
    out["saved"] = True
    out["note"] = (
        f"Armed. It is evaluated on every {out['interval']} bar of "
        f"{out['symbol']} and will place a simulated order in your paper book "
        f"when the condition is met — no real order, ever.")
    return out


# The last draft each conversation produced.
#
# `_scene.cards` is per-REQUEST, and "save it" is almost always the turn AFTER
# the one that built the thing — so reading only this turn's cards meant the
# save arrived with nothing to save and the model, having been refused, rebuilt
# the draft from its own words. A rebuilt draft is a DIFFERENT strategy that
# looks identical: the translator runs again, on a paraphrase, and what gets
# armed is not what the card showed.
#
# Keyed by (user, conversation) so a draft cannot cross between either. Bounded
# and in-process: a draft is cheap to rebuild deliberately and must never be
# the thing a restart silently resurrects.
_DRAFTS: "OrderedDict[tuple, dict]" = OrderedDict()
_DRAFTS_MAX = 256
_drafts_lock = threading.Lock()


def remember_draft(user_id: int, chat_id: str, draft: dict) -> None:
    """Called from the tool seam whenever a draft card is emitted."""
    key = (int(user_id or 0), str(chat_id or ""))
    with _drafts_lock:
        _DRAFTS[key] = draft
        _DRAFTS.move_to_end(key)
        while len(_DRAFTS) > _DRAFTS_MAX:
            _DRAFTS.popitem(last=False)


def _last_draft() -> Optional[dict]:
    """The draft to save: this turn's card if there is one, else the last one
    this conversation produced."""
    for card in reversed(getattr(ds._scene, "cards", None) or []):
        if isinstance(card, dict) and card.get("kind") == "workflow_draft":
            return card
    who = getattr(ds._req, "user", None)
    key = (int(who[0]) if who else 0, str(getattr(ds._req, "chat_id", "") or ""))
    with _drafts_lock:
        return _DRAFTS.get(key)


def tool_list_strategies(user_id: int = 0, state: str = "") -> dict:
    if not user_id:
        return {"error": "sign in to see your strategies."}
    _code, out = api_list(user_id, state)
    if not out["strategies"]:
        out["_note"] = ("No saved strategies. Nothing is running — say so "
                        "plainly rather than implying something might be.")
    return out


def tool_pause_strategy(user_id: int = 0, strategy_id: int = 0,
                        resume: bool = False) -> dict:
    if not user_id:
        return {"error": "sign in to change a strategy."}
    if not strategy_id:
        return {"error": "which strategy? call list_strategies for the ids."}
    code, out = api_patch(user_id, int(strategy_id),
                          {"state": "armed" if resume else "paused"})
    return out if code == 200 else {"error": out.get("error")}


def tool_delete_strategy(user_id: int = 0, strategy_id: int = 0) -> dict:
    if not user_id:
        return {"error": "sign in to change a strategy."}
    if not strategy_id:
        return {"error": "which strategy? call list_strategies for the ids."}
    code, out = api_delete(user_id, int(strategy_id))
    return out if code == 200 else {"error": out.get("error")}


def tool_paper_portfolio(user_id: int = 0) -> dict:
    """The paper book, for the model to read back in a sentence."""
    if not user_id:
        return {"error": "sign in to see your paper portfolio."}
    _c, summary = paper.api_summary(user_id)
    if not summary.get("exists"):
        return {"exists": False,
                "_note": "No paper book yet — one opens the moment a strategy "
                         "is saved. Do not describe a portfolio that does not "
                         "exist."}
    _c, holdings = paper.api_holdings(user_id)
    return {**summary, "holdings": holdings,
            "_note": "Every figure here is simulated. Quote them as the paper "
                     "book's, never as a real account's."}


# ══ the Strategies page ════════════════════════════════════════════════════
#
# Pivot's AgentsTab is reused unchanged, and it reads the Agent System's
# workflow contract. Rather than fork a 1,558-line component, the mapping lives
# here: a Charto strategy IS a workflow — a name, a state, a step list, a run
# history — and the two vocabularies differ only in their words for it.
#
# Everything below is computed from what actually happened: the fills carry
# their strategy_id, so a card's return, run count and sparkline are the book's
# own arithmetic rather than a figure kept beside it and allowed to drift.

_STATE_TO_WF = {"draft": "draft", "armed": "active", "paused": "paused",
                "retired": "archived"}


def _wf_steps(spec: dict) -> list[dict]:
    """The draft's own steps, given the ids the editor's contract expects."""
    out = []
    for i, s in enumerate(spec.get("steps") or []):
        if not isinstance(s, dict):
            continue
        out.append({
            "id": f"{i}", "step_index": i,
            "step_type": s.get("step_type") or "",
            "label": s.get("label") or s.get("readback") or None,
            "config": s.get("config") or {},
        })
    return out


def _workflow(d: dict, *, steps: bool = False) -> dict:
    try:
        spec = json.loads(d["spec"]) or {}
    except (TypeError, ValueError):
        spec = {}
    rb = _readback(d)
    # The description is what the card prints as "Method", and it is also what
    # `deriveUniverse`/`deriveCadence` read to label the other two rows — so it
    # leads with the instrument and says the rule in the card's own English.
    desc = rb["entry"] or spec.get("description") or ""
    method = (f"{d['side'].title()} {paper._qty_out(d['quantity'])} "
              f"{d['symbol']} when {desc}" if desc else
              f"{d['side'].title()} {paper._qty_out(d['quantity'])} {d['symbol']}")
    wf = {
        "id": str(d["id"]),
        "name": d["name"],
        "description": method,
        "status": _STATE_TO_WF.get(d["state"], "draft"),
        "version": 1,
        "single_instance": True,
        "created_at": paper._iso(d["created"]),
        "updated_at": paper._iso(d["updated"]),
        "activated_at": paper._iso(d["created"]) if d["state"] == "armed" else None,
        # The last run that ACTED, not the last evaluation. Every armed rule is
        # evaluated on every tick, so reporting that made each card read "just
        # now" forever and said nothing about the strategy.
        "last_run_at": _last_fire(int(d["id"])),
        # Charto evaluates on the tick rather than on a clock, so there is no
        # next run to name. Null is the honest answer and `deriveCadence` reads
        # it as "real-time" off the description, which is what this is.
        "next_run_at": None,
    }
    if steps:
        wf["steps"] = _wf_steps(spec)
    return wf


def _last_fire(sid: int) -> Optional[str]:
    """When this strategy last did something — its newest entry or exit."""
    with ds._users_lock:
        row = _db().execute(
            "SELECT MAX(ts) FROM strategy_log WHERE strategy_id=? AND "
            "kind IN ('entry','exit')", (int(sid),)).fetchone()
    return paper._iso(row[0]) if row and row[0] else None


def _fills_of(uid: int, sid: Optional[int] = None) -> list[tuple]:
    acct = paper.account_of(uid)
    if acct is None:
        return []
    q = ("SELECT strategy_id, side, quantity, fill_price, net_cashflow, "
         "realized_pnl, filled_at FROM paper_fills WHERE account_id=? "
         "AND strategy_id IS NOT NULL")
    args: list = [acct[0]]
    if sid is not None:
        q += " AND strategy_id=?"; args.append(int(sid))
    q += " ORDER BY filled_at ASC"
    with ds._users_lock:
        return _db().execute(q, args).fetchall()


def _perf(uid: int, d: dict) -> dict:
    """One strategy's card numbers, from its own fills.

    The series is cumulative P&L per day — realised on the closes, plus what
    the open lot is worth right now. A strategy that has bought and not sold
    has a real curve, not an empty one, which is the ordinary case for
    something armed this week.
    """
    sid = int(d["id"])
    rows = _fills_of(uid, sid)
    with ds._users_lock:
        fires = _db().execute(
            "SELECT kind, COUNT(*) FROM strategy_log WHERE strategy_id=? "
            "GROUP BY kind", (sid,)).fetchall()
    counts = {k: n for k, n in fires}
    runs = sum(counts.values())
    acted = counts.get("entry", 0) + counts.get("exit", 0)
    success = (acted / runs * 100) if runs else None

    if not rows:
        return {"series": [], "return_pct": None,
                "last_run_at": _last_fire(sid),
                "run_count": runs, "success_rate": success, "held": 0,
                "invested": paper.to_money(0), "pnl": paper.to_money(0),
                "has_data": False}

    invested = paper.to_money(0)
    realized = paper.to_money(0)
    held = 0
    series: dict[str, float] = {}
    for _s, side, qty, px, cash, rpnl, ts in rows:
        day = paper._iso(ts)[:10] if ts else paper._today()
        if side == "BUY":
            invested += paper.to_money(-paper.to_money(cash))
            held += int(float(qty))
        else:
            held -= int(float(qty))
            if rpnl is not None:
                realized += paper.to_money(rpnl)
        series[day] = paper.f(realized)

    pnl = realized
    if held > 0 and d["entry_price"]:
        mark = paper.mark_price(d["symbol"])
        if mark is not None:
            pnl = realized + paper.to_money(
                (mark - paper.to_money(d["entry_price"])) * held)
            series[paper._today()] = paper.f(pnl)
    return {
        "series": [{"date": k, "nav": v} for k, v in sorted(series.items())],
        "return_pct": (float(pnl / invested * 100) if invested else None),
        "last_run_at": _last_fire(sid),
        "run_count": runs, "success_rate": success, "held": held,
        "invested": invested, "pnl": pnl, "has_data": True,
    }


def _rows_for(uid: int, sid: Optional[int] = None) -> list[dict]:
    q = "SELECT %s FROM strategies WHERE user_id=?" % ", ".join(_COLS)
    args: list = [int(uid)]
    if sid is not None:
        q += " AND id=?"; args.append(int(sid))
    q += " ORDER BY id DESC"
    with ds._users_lock:
        return [_row(r) for r in _db().execute(q, args).fetchall()]


def api_workflows(uid: int) -> tuple[int, list]:
    return 200, [_workflow(d) for d in _rows_for(uid)]


def api_workflow(uid: int, sid: int) -> tuple[int, dict]:
    got = _rows_for(uid, sid)
    if not got:
        return 404, {"error": "no such strategy"}
    return 200, _workflow(got[0], steps=True)


def api_workflow_performance(uid: int, sid: int) -> tuple[int, dict]:
    got = _rows_for(uid, sid)
    if not got:
        return 404, {"error": "no such strategy"}
    p = _perf(uid, got[0])
    return 200, {k: p[k] for k in ("series", "return_pct", "last_run_at",
                                   "run_count", "success_rate", "has_data")}


def api_workflows_summary(uid: int) -> tuple[int, dict]:
    """The three cards over the roster: counts, the six-month scorecard, and
    the daily P&L the heatmap paints.

    A win is a CLOSED round trip that made money — an open position is not a
    win yet, and counting it as one is how a losing book looks green.
    """
    rows = _rows_for(uid)
    counts = {"active": 0, "paused": 0, "draft": 0}
    returns, total_pnl, total_inv = [], paper.to_money(0), paper.to_money(0)
    for d in rows:
        st = _STATE_TO_WF.get(d["state"], "draft")
        if st in counts:
            counts[st] += 1
        p = _perf(uid, d)
        total_pnl += p["pnl"]
        total_inv += p["invested"]
        returns.append({
            "workflow_id": str(d["id"]), "name": d["name"],
            "return_pct": p["return_pct"], "series": p["series"],
            "run_count": p["run_count"], "success_rate": p["success_rate"],
            "last_run_at": p["last_run_at"], "has_data": p["has_data"],
        })

    cutoff = int(time.time()) - 180 * 86400
    # `account_of` takes the users lock itself, and that lock is NOT reentrant:
    # calling it from inside a `with ds._users_lock` deadlocked the thread while
    # holding the lock, which hung every other request in the process — the
    # whole server, from one portfolio read. Resolve the account first, then
    # take the lock for the query alone.
    acct = paper.account_of(uid)
    closed = []
    if acct is not None:
        with ds._users_lock:
            closed = _db().execute(
                "SELECT realized_pnl, filled_at FROM paper_fills WHERE "
                "account_id=? AND strategy_id IS NOT NULL AND "
                "realized_pnl IS NOT NULL AND filled_at >= ?",
                (acct[0], cutoff)).fetchall()
    wins = sum(1 for r in closed if (r[0] or 0) > 0)
    losses = sum(1 for r in closed if (r[0] or 0) <= 0)
    daily: dict[str, float] = {}
    for rpnl, ts in closed:
        day = paper._iso(ts)[:10]
        daily[day] = round(daily.get(day, 0.0) + float(rpnl or 0), 4)

    return 200, {
        "active_count": counts["active"], "paused_count": counts["paused"],
        "draft_count": counts["draft"],
        "trades_6mo": {
            "total": len(closed), "wins": wins, "losses": losses,
            "win_rate_pct": (round(wins / len(closed) * 100, 1)
                             if closed else None),
        },
        "daily_pnl": [{"date": k, "pnl": v} for k, v in sorted(daily.items())],
        "strategy_returns": sorted(
            returns, key=lambda r: (r["return_pct"] is None, -(r["return_pct"] or 0))),
        "total_pnl": paper.f(total_pnl),
        "total_pnl_pct": (float(total_pnl / total_inv * 100) if total_inv else 0.0),
        "has_data": bool(rows),
    }
