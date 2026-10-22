"""Plays: a trader's idea, written once by the model, watched on every bar close.

A play is the one object the execution layer has. The model writes it from the
trader's words; code validates it, replays it over history, arms it, and runs
it. See docs/plans/2026-10-22-execution-layer-architecture.md for the why.

    when    conditions over MEASURES (series the model defines: a ratio, an
            RSI, a basket's value, a filing count) and the bar's clock time
    do      notify, or a paper order sized in rupees by code
    manage  stop, target and a time exit, for what this play bought
    contract  how long it lives, who pulls the trigger, how much the model
            decides at fire time (the three autonomy dials)
    mandate   the limits code enforces before anything is placed

The rule this module lives under: the model decides what things mean, code
decides when they happen and what is allowed. Nothing here calls a model.
A measure is model-written code, but it runs in the indicator sandbox over
data this module fetched, and it is the SAME code for the history replay and
for the live bar, so the fire count shown before arming and the live fires
cannot disagree about what the rule means.

Bound to dataserver at import (dataserver usually runs as __main__, and a
second import would load a second copy of its state).
"""
from __future__ import annotations

import ast
import json
import logging
import math
import os
import queue
import re
import sqlite3
import threading
import time
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN

import customsec
import hyperliquid
import policy

log = logging.getLogger("plays")

ds = None            # dataserver, bound by bind()
_alerts = None       # the alerts module: its SSE stream carries play events too
_paper = None


def bind(module, alerts_mod=None, paper_mod=None) -> None:
    global ds, _alerts, _paper
    ds, _alerts, _paper = module, alerts_mod, paper_mod
    policy.bind(module, paper_mod)
    import scan
    scan.bind(module)


# ══ storage ═════════════════════════════════════════════════════════════════
# charto_users.db, beside alerts and the paper book: a user's plays cannot be
# regenerated from anything upstream.

_SCHEMA = """
CREATE TABLE IF NOT EXISTS plays (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id   INTEGER NOT NULL REFERENCES users(id),
  title     TEXT NOT NULL,
  ask       TEXT NOT NULL DEFAULT '',        -- the trader's own words
  spec      TEXT NOT NULL,                   -- the compiled play (JSON)
  symbol    TEXT NOT NULL,                   -- the clock symbol
  interval  TEXT NOT NULL,
  symbols   TEXT NOT NULL DEFAULT '',        -- every symbol it reads, comma separated
  state     TEXT NOT NULL,                   -- armed | paused | done | draft
  why       TEXT NOT NULL DEFAULT '',        -- why it is paused or done
  created   INTEGER NOT NULL,
  updated   INTEGER NOT NULL,
  expires   INTEGER,
  fires     INTEGER NOT NULL DEFAULT 0,
  accepted  INTEGER NOT NULL DEFAULT 0,      -- proposals the trader confirmed
  spent_inr REAL NOT NULL DEFAULT 0,
  held_qty  REAL NOT NULL DEFAULT 0,         -- what THIS play bought and still holds
  held_sym  TEXT NOT NULL DEFAULT '',
  held_legs TEXT NOT NULL DEFAULT '{}',      -- a basket's members it holds {symbol: qty}
  last_eval_ts INTEGER NOT NULL DEFAULT 0,   -- the last closed bar it read
  needs_confirm INTEGER NOT NULL DEFAULT 0,  -- an edit changed its structure
  history   TEXT NOT NULL DEFAULT '{}');     -- the replay summary shown before arming
CREATE INDEX IF NOT EXISTS plays_user ON plays(user_id, state);

CREATE TABLE IF NOT EXISTS play_events (
  id       INTEGER PRIMARY KEY AUTOINCREMENT,
  play_id  INTEGER NOT NULL,
  user_id  INTEGER NOT NULL REFERENCES users(id),
  ts       INTEGER NOT NULL,                 -- the BAR's clock
  created  INTEGER NOT NULL,
  kind     TEXT NOT NULL,                    -- fired | proposed | filled | exited | rejected | note
  title    TEXT NOT NULL,
  body     TEXT NOT NULL,                    -- what it saw, and what it did (JSON)
  status   TEXT NOT NULL DEFAULT 'open',     -- open | confirmed | dismissed | done
  seen     INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS play_events_user ON play_events(user_id, created DESC);

CREATE TABLE IF NOT EXISTS measures (
  id       INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id  INTEGER NOT NULL REFERENCES users(id),
  name     TEXT NOT NULL,
  spec     TEXT NOT NULL,
  created  INTEGER NOT NULL,
  updated  INTEGER NOT NULL,
  UNIQUE(user_id, name));

CREATE TABLE IF NOT EXISTS play_halts (
  user_id  INTEGER PRIMARY KEY REFERENCES users(id),
  until    INTEGER NOT NULL,                 -- no new entries before this moment
  why      TEXT NOT NULL,
  play_id  INTEGER,
  created  INTEGER NOT NULL);

CREATE TABLE IF NOT EXISTS play_chats (
  id       TEXT NOT NULL,                    -- the page's conversation id
  user_id  INTEGER NOT NULL REFERENCES users(id),
  title    TEXT NOT NULL DEFAULT '',
  focus    INTEGER,                          -- the play it was opened to change
  turns    TEXT NOT NULL DEFAULT '[]',       -- [{role, content, steps, meta, cards: [refs]}]
  created  INTEGER NOT NULL,
  updated  INTEGER NOT NULL,
  PRIMARY KEY (user_id, id));
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
        cols = {r[1] for r in _db().execute("PRAGMA table_info(plays)")}
        if "held_legs" not in cols:
            _db().execute("ALTER TABLE plays ADD COLUMN held_legs TEXT NOT NULL DEFAULT '{}'")
        if "entry_bar" not in cols:      # the bar its open position was entered on
            _db().execute("ALTER TABLE plays ADD COLUMN entry_bar INTEGER NOT NULL DEFAULT 0")
        if "marks" not in cols:          # a universe play's per-symbol clock: {sym: {eval, entry, done}}
            _db().execute("ALTER TABLE plays ADD COLUMN marks TEXT NOT NULL DEFAULT '{}'")
        _db().commit()
    _ready = True


# ══ vocabulary ══════════════════════════════════════════════════════════════

INTERVALS = {"1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "1d": 1440}
FIELDS = ("open", "high", "low", "close", "volume")
LIFESPANS = ("now", "once_when", "standing", "policy")
AUTHORITIES = ("notify", "propose", "act", "manage")
DISCRETIONS = ("none", "judge", "plan")
OPS = ("above", "below", "cross_up", "cross_down", "between", "outside",
       "rises_pct", "falls_pct", "is_true")
LOOKBACK = {"1m": 1500, "3m": 1500, "5m": 1500, "15m": 1200, "30m": 1000, "1h": 1000, "1d": 750}
DEFAULT_SIZE_INR = 10_000
DERIV_CAPITAL = 2_00_000
UNIVERSE_MAX = 60
PERP_FIELDS = ("funding", "premium", "oi")   # funding is hourly, with history; the others live only
# the trader's own day book as series, so a guard is a rule like any other:
# realised P&L so far today, plus what is open on the latest bar; positions
# opened today; losing trades in a row today
BOOK_FIELDS = ("day_pnl", "realized_today", "trades_today", "loss_streak")
DO_KINDS = ("notify", "buy", "sell", "short", "cover", "options", "future", "rebalance", "halt")
_NAME = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
_HHMM = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
_SQL_SECONDS = 6.0
_BANNED_SQL = re.compile(r"\b(attach|detach|pragma|insert|update|delete|replace|create|drop|"
                         r"alter|vacuum|reindex|analyze)\b", re.I)
# Names an exit rule reads about the position it is closing, on the clock
# symbol: the close it entered at, bars since, the highest high and lowest low
# since, and the move in the position's favour in percent. Code fills them per
# trade, so "back below entry after 6 bars" or "1.5 ATR under the high since
# entry" are written, not built in.
POSITION = ("entry", "bars_in", "hi_since", "lo_since", "pnl_pct")
_RESERVED = set(FIELDS) | {"t", "hhmm", "data", "params", "ta", "math"} | set(POSITION)


class Bad(ValueError):
    """A play or measure that cannot be built as written. The message says
    what to change, because the model reads it and fixes the call."""


# ══ measures: the model's own series ════════════════════════════════════════
#
# A measure is {name, title, formula, inputs?, expr | code}.
#   expr  a one-line expression over input names, evaluated per bar:
#         "a / b", "(close - sma20) / sma20 * 100"
#   code  def compute(data, params): return [one value per bar]
#         data has t, open, high, low, close, volume, hhmm (bar close, IST)
#         for the play's clock symbol, every input by its name, and every
#         measure defined before this one. `ta` and `math` are available.
# inputs  {alias: {"bars": {"symbol": "ICICIBANK", "field": "close"}}}
#         {alias: {"sql": "SELECT <time>, <value> FROM ...", "refresh": "1d"}}
#         A SQL input's first column is WHEN the value became known (epoch
#         seconds or YYYY-MM-DD); each bar sees the latest value known by its
#         own close, so history never reads the future. One column means
#         "live only": the value applies to the latest bar and has no history.

def _clean_name(raw) -> str:
    n = re.sub(r"[^a-z0-9_]+", "_", str(raw or "").strip().lower()).strip("_")
    if n and n[0].isdigit():
        n = "m_" + n
    return n[:40]


def _norm_measure(m: dict, uid: int | None = None) -> dict:
    if not isinstance(m, dict):
        raise Bad("each measure is an object {name, title, expr or code, inputs}")
    name = _clean_name(m.get("name"))
    if not name or not _NAME.match(name) or name in _RESERVED:
        raise Bad(f"measure name {m.get('name')!r} must be lower_snake_case and not one of "
                  f"{sorted(_RESERVED)}")
    expr, code = (m.get("expr") or "").strip(), (m.get("code") or "").strip()
    if bool(expr) == bool(code):
        raise Bad(f"measure {name}: give exactly one of expr (a one-line formula over input "
                  f"names) or code (def compute(data, params) returning one value per bar)")
    inputs = {}
    for alias, src in (m.get("inputs") or {}).items():
        a = _clean_name(alias)
        if not a or a in _RESERVED:
            raise Bad(f"measure {name}: input name {alias!r} is reserved or empty")
        if not isinstance(src, dict):
            raise Bad(f"measure {name}: input {alias} must be {{bars: {{symbol, field}}}} or "
                      f"{{sql: 'SELECT time, value ...'}}")
        if "bars" in src:
            b = src["bars"] if isinstance(src["bars"], dict) else {"symbol": src["bars"]}
            sym = ds.canon_symbol(str(b.get("symbol") or ""))
            field = str(b.get("field") or "close").lower()
            if not sym:
                raise Bad(f"measure {name}: input {alias} names no symbol")
            if field not in FIELDS:
                raise Bad(f"measure {name}: input {alias} field must be one of {FIELDS}")
            inputs[a] = {"bars": {"symbol": sym, "field": field}}
        elif "perp" in src:
            pp = src["perp"] if isinstance(src["perp"], dict) else {"symbol": src["perp"]}
            psym = str(pp.get("symbol") or pp.get("coin") or "").upper()
            psym = psym if psym.endswith(hyperliquid.SUFFIX) else psym + hyperliquid.SUFFIX
            field = str(pp.get("field") or "funding").lower()
            if field not in PERP_FIELDS:
                raise Bad(f"measure {name}: input {alias} perp field must be one of {PERP_FIELDS}")
            if not hyperliquid.known(psym):
                raise Bad(f"measure {name}: Hyperliquid lists no perpetual {psym}")
            inputs[a] = {"perp": {"symbol": psym, "field": field}}
        elif "book" in src:
            b = src["book"] if isinstance(src["book"], dict) else {"field": src["book"]}
            field = str(b.get("field") or "day_pnl").lower()
            if field not in BOOK_FIELDS:
                raise Bad(f"measure {name}: input {alias} book field must be one of {BOOK_FIELDS}")
            if not uid:
                raise Bad(f"measure {name}: a book input reads the trader's own day book, which needs "
                          f"an account")
            inputs[a] = {"book": {"field": field, "uid": int(uid)}}
        elif "sql" in src:
            s = str(src["sql"]).strip().rstrip(";")
            if ";" in s or not re.match(r"^\s*(select|with)\b", s, re.I) or _BANNED_SQL.search(s):
                raise Bad(f"measure {name}: input {alias} must be one read-only SELECT")
            inputs[a] = {"sql": s, "refresh": str(src.get("refresh") or "1d")}
        else:
            raise Bad(f"measure {name}: input {alias} needs bars, perp, book or sql")
    out = {"name": name, "title": str(m.get("title") or name)[:80],
           "formula": str(m.get("formula") or expr or "")[:240], "inputs": inputs,
           "unit": str(m.get("unit") or "")[:20]}
    if expr:
        out["expr"] = expr
    else:
        out["code"] = code
    return out


class _Rename(ast.NodeTransformer):
    def __init__(self, names: dict):
        self.names = names

    def visit_FunctionDef(self, node):
        if node.name in self.names:
            node.name = self.names[node.name]
        self.generic_visit(node)
        return node

    def visit_Name(self, node):
        if node.id in self.names:
            node.id = self.names[node.id]
        return node


_EXPR_OK = {"abs", "min", "max", "round", "math", "sum", "len", "float", "int", "pow", "ta",
            "True", "False", "None"}


def _measure_source(m: dict, known: set) -> str:
    """One measure as a function named m_<name>. Its helpers are renamed with
    the same prefix so two measures' helpers cannot collide."""
    fn = f"m_{m['name']}"
    if "expr" in m:
        try:
            tree = ast.parse(m["expr"], mode="eval")
        except SyntaxError as exc:
            raise Bad(f"measure {m['name']}: expr is not a valid formula ({exc.msg})") from None
        names = sorted({n.id for n in ast.walk(tree) if isinstance(n, ast.Name)})
        unknown = [n for n in names if n not in known and n not in _EXPR_OK]
        if unknown:
            raise Bad(f"measure {m['name']}: expr uses {', '.join(unknown)}, which is not an input, "
                      f"a field ({', '.join(FIELDS)}) or an earlier measure. Add it under inputs.")
        keys = [n for n in names if n in known]
        args = ", ".join(keys) or "x"
        cols = ", ".join(f"data[{k!r}][i]" for k in keys) or "0"
        return (f"def {fn}(data, params):\n"
                f"    n = len(data['t'])\n"
                f"    f = lambda {args}: ({m['expr']})\n"
                f"    out = []\n"
                f"    for i in range(n):\n"
                f"        xs = [{cols}]\n"
                f"        if any(x is None for x in xs):\n"
                f"            out.append(None)\n"
                f"            continue\n"
                f"        try:\n"
                f"            out.append(f(*xs))\n"
                f"        except (ZeroDivisionError, ValueError, OverflowError, TypeError):\n"
                f"            out.append(None)\n"
                f"    return out\n")
    try:
        tree = ast.parse(m["code"])
    except SyntaxError as exc:
        raise Bad(f"measure {m['name']}: code has a syntax error on line {exc.lineno}: {exc.msg}") from None
    tops = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
    if "compute" not in tops:
        raise Bad(f"measure {m['name']}: code must define compute(data, params)")
    ren = {t: (fn if t == "compute" else f"{fn}_{t}") for t in tops}
    tree = _Rename(ren).visit(tree)
    return ast.unparse(ast.fix_missing_locations(tree)) + "\n"


def _program(measures: list[dict], base: set) -> str:
    known = set(base)
    parts, calls = [], []
    for m in measures:
        known |= set(m["inputs"])
        parts.append(_measure_source(m, known))
        calls.append(f"    v = m_{m['name']}(data, params)\n"
                     f"    data[{m['name']!r}] = v\n"
                     f"    out[{m['name']!r}] = v\n")
        known.add(m["name"])
    return ("\n".join(parts) + "\ndef compute(data, params):\n    out = {}\n"
            + "".join(calls) + "    return out\n")


# ── the data a play reads ───────────────────────────────────────────────────

def _rows(symbol: str, interval: str, limit: int, closed_only: bool = True,
          ensure: bool = True) -> list[tuple]:
    if ensure and hasattr(ds, "_symbol_ready") and not ds._symbol_ready(symbol):
        ds._ensure_symbol(symbol)
    d = ds.get_bars(symbol, interval, None, limit + 1)
    rows = [(b["t"], b["o"], b["h"], b["l"], b["c"], b["v"]) for b in d.get("bars") or []]
    clock = customsec.members(symbol)[0] if customsec.is_custom(symbol) else symbol
    if closed_only and rows and ds._live_view(clock) is not None:
        rows = rows[:-1]           # the bar still forming is not a fact yet
    elif closed_only and rows and hyperliquid.is_perp(clock) and \
            rows[-1][0] + INTERVALS[interval] * 60 > time.time():
        rows = rows[:-1]
    return rows[-limit:]


def _bar_close(t: int, interval: str) -> int:
    return t + INTERVALS[interval] * 60 if interval != "1d" else t


def _hhmm(t: int, interval: str) -> int:
    """The bar's CLOSE time in IST as an integer, 09:20 → 920. Daily bars
    carry the session's close."""
    if interval == "1d":
        return 1530
    lt = time.gmtime(_bar_close(t, interval) + ds.IST_OFF)
    return lt.tm_hour * 100 + lt.tm_min


def _as_epoch(v) -> int | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return int(v)
    s = str(v).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            # a date with no time became known at that day's IST midnight
            return int(datetime.strptime(s[:len(fmt) + 2].strip(), fmt)
                       .replace(tzinfo=timezone.utc).timestamp()) - ds.IST_OFF
        except ValueError:
            continue
    return None


_sql_cache: dict[str, tuple[float, list]] = {}


def _sql_rows(sql: str, refresh: str) -> list:
    ttl = {"1m": 60, "5m": 300, "1h": 3600, "1d": 86400}.get(refresh, 3600)
    hit = _sql_cache.get(sql)
    if hit and time.monotonic() - hit[0] < ttl:
        return hit[1]
    con = sqlite3.connect(f"file:{ds.DB_PATH}?mode=ro", uri=True, timeout=5)
    import fo_store
    fo_store.attach(con)
    deadline = time.monotonic() + _SQL_SECONDS
    con.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 20_000)
    try:
        rows = con.execute(sql).fetchmany(20_000)
    except sqlite3.OperationalError as exc:
        raise Bad(f"SQL input failed: {exc}") from None
    finally:
        con.close()
    _sql_cache[sql] = (time.monotonic(), rows)
    return rows


def frame(spec: dict, limit: int | None = None, ensure: bool = True) -> tuple[dict, list[str]]:
    """Everything a play's measures read, on the clock symbol's closed bars.
    Returns (data, notes). Every series has one value per clock bar. A scan
    passes ensure=False: reading 500 names must never start 500 downloads."""
    sym, iv = spec["symbol"], spec["interval"]
    rows = _rows(sym, iv, limit or LOOKBACK[iv], ensure=ensure)
    if not rows:
        raise Bad(f"no bars stored for {sym} at {iv}; pick a symbol Pivot has data for")
    data = {"t": [r[0] for r in rows], "hhmm": [_hhmm(r[0], iv) for r in rows]}
    for k, col in zip(FIELDS, zip(*[r[1:6] for r in rows])):
        data[k] = list(col)
    notes = []
    members: dict[str, list] = {}
    for m in spec.get("measures") or []:
        for alias, src in m["inputs"].items():
            if "bars" in src and src["bars"]["symbol"] != sym:
                members.setdefault(src["bars"]["symbol"], None)
    if members:
        import custom_indicators
        got = {s: _rows(s, iv, len(rows) + 50) for s in members}
        empty = [s for s, r in got.items() if not r]
        if empty:
            raise Bad(f"no bars stored for {', '.join(empty)} at {iv}")
        aligned = custom_indicators.align_basket(rows, got)
        for j, s in enumerate(aligned["symbols"]):
            members[s] = {k: aligned[k][j] for k in FIELDS}
    for m in spec.get("measures") or []:
        for alias, src in m["inputs"].items():
            if "bars" in src:
                b = src["bars"]
                data[alias] = (data[b["field"]] if b["symbol"] == sym
                               else members[b["symbol"]][b["field"]])
            elif "perp" in src:
                data[alias], note = _perp_series(src["perp"], data["t"], iv)
                if note:
                    notes.append(f"{alias}: {note}")
            elif "book" in src:
                data[alias] = _book_series(src["book"]["uid"], src["book"]["field"], data["t"], iv)
            else:
                got = _sql_rows(src["sql"], src["refresh"])
                if got and len(got[0]) >= 2:
                    pts = sorted(((_as_epoch(r[0]), r[1]) for r in got if _as_epoch(r[0]) is not None),
                                 key=lambda p: p[0])
                    col, j, cur = [], 0, None
                    for t in data["t"]:
                        close = _bar_close(t, iv)
                        while j < len(pts) and pts[j][0] <= close:
                            cur = pts[j][1]
                            j += 1
                        col.append(cur)
                    data[alias] = col
                else:
                    v = got[0][0] if got else None
                    data[alias] = [None] * (len(rows) - 1) + [v]
                    notes.append(f"{alias} has no time column, so it applies to the latest bar only "
                                 f"and the replay cannot test it")
    return data, notes


def _perp_series(src: dict, ts: list[int], iv: str) -> tuple[list, str]:
    """A perp's funding rate (hourly, each bar reads the last rate set by its
    close) or its premium or open interest, which Hyperliquid gives only as
    of now, so they apply to the latest bar and the replay cannot test them."""
    if src["field"] == "funding" and ts:
        pts = hyperliquid.funding_history(src["symbol"], ts[0] - 3600)
        col, j, cur = [], 0, None
        for t in ts:
            close = _bar_close(t, iv)
            while j < len(pts) and pts[j][0] <= close:
                cur = pts[j][1]
                j += 1
            col.append(cur)
        live = (hyperliquid.ctx(src["symbol"]) or {}).get("funding")
        if col and live is not None:
            col[-1] = live
        return col, ""
    c = hyperliquid.ctx(src["symbol"]) or {}
    v = c.get(src["field"])
    return [None] * (len(ts) - 1) + [v], (f"Hyperliquid gives {src['field']} only as of now, so it "
                                          f"applies to the latest bar and the replay cannot test it")


def _book_series(uid: int, field: str, ts: list[int], iv: str) -> list:
    """The day book on each bar's close: what was realised that IST day up to
    then, how many positions were opened, and the losing trades in a row. A
    trade is a group of legs opened together. The latest bar's day_pnl adds
    what is open, marked."""
    import daybook
    daybook.init_db()
    with ds._users_lock:
        rows = _db().execute("SELECT grp, entry_ts, exit_ts, pnl, state, side, entry_px, mark_px, qty, "
                             "costs, kind FROM day_positions WHERE user_id=? AND entry_ts >= ?",
                             (uid, (ts[0] if ts else 0) - 86400)).fetchall()
    groups: dict[str, dict] = {}
    for grp, ent, ext, pnl, state, *_ in rows:
        g = groups.setdefault(grp, {"in": ent, "out": 0, "pnl": 0.0, "open": False})
        g["in"] = min(g["in"], ent)
        if state == "closed":
            g["out"] = max(g["out"], ext or 0)
            g["pnl"] += pnl or 0.0
        else:
            g["open"] = True
    closed = sorted((g for g in groups.values() if not g["open"]), key=lambda g: g["out"])
    opened = sorted(g["in"] for g in groups.values())
    out = []
    for k, t in enumerate(ts):
        # the latest bar reads the book as it is now: a guard checked after a
        # fill or an exit must see that fill, whatever bar the clock is on
        close = max(_bar_close(t, iv), int(time.time())) if k == len(ts) - 1 else _bar_close(t, iv)
        day0 = _day_of(close) * 86400 - ds.IST_OFF
        if field == "trades_today":
            out.append(float(sum(1 for x in opened if day0 <= x <= close)))
            continue
        done = [g for g in closed if day0 <= g["out"] <= close]
        if field == "loss_streak":
            k = 0
            for g in reversed(done):
                if g["pnl"] >= 0:
                    break
                k += 1
            out.append(float(k))
        else:
            out.append(round(sum(g["pnl"] for g in done), 2))
    if field == "day_pnl" and out:
        live = 0.0
        for grp, ent, ext, pnl, state, side, epx, mpx, qty, costs, kind in rows:
            if state == "open":
                p = {"side": side, "entry_px": epx, "qty": qty, "kind": kind}
                live += daybook._gross(p, mpx or epx) - (costs or 0)
        out[-1] = round(out[-1] + live, 2)
    return out


def run_measures(spec: dict, data: dict, check: bool = False) -> tuple[dict, list[str]]:
    """Every measure's series, from one sandbox run. With check=True the same
    program also runs on the data cut short, and a measure whose past values
    change when later bars arrive is refused: it reads the future."""
    measures = spec.get("measures") or []
    if not measures:
        return {}, []
    import indicator_sandbox
    base = set(data) - {"t"} | {"t"}
    code = _program(measures, base)
    n = len(data["t"])
    jobs = [{"id": "full", "free": True, "bars": data}]
    cuts = [c for c in (n * 3 // 5, n * 4 // 5) if c > 20] if check else []
    jobs += [{"id": f"cut{c}", "free": True, "bars": data, "truncate": c} for c in cuts]
    try:
        reply = indicator_sandbox.run(code, jobs, timeout=20)
    except indicator_sandbox.SandboxError as exc:
        raise Bad(f"measures could not run: {exc}") from None
    if not reply.get("ok"):
        raise Bad(f"measure code refused: {reply.get('error')}. No imports, no names starting "
                  f"with '_'; ta and math are available.")
    res = {r["id"]: r for r in reply["results"]}
    full = res["full"]
    if not full.get("ok"):
        raise Bad(f"measure failed: {full.get('error')}"
                  + (f" (line {full['line']})" if full.get("line") else ""))
    out, problems = {}, []
    for m in measures:
        v = (full.get("value") or {}).get(m["name"])
        if isinstance(v, (int, float, bool)) or v is None:
            v = [None] * (n - 1) + [v]
            problems.append(f"{m['name']} returns one value, not one per bar, so it has no history")
        if not isinstance(v, list) or len(v) != n:
            raise Bad(f"measure {m['name']} must return a list with one value per bar ({n}); got "
                      f"{type(v).__name__} of length {len(v) if isinstance(v, list) else '-'}")
        out[m["name"]] = [(1.0 if x is True else 0.0 if x is False else
                           float(x) if isinstance(x, (int, float)) and math.isfinite(x) else None)
                          for x in v]
        if n > 20 and all(x is None for x in out[m["name"]]):
            # a series that is empty on every bar is a broken formula, not a
            # quiet market; arming it would wait for a fire that cannot come
            raise Bad(f"measure {m['name']} has no value on any of the {n} bars, so the play could "
                      f"never fire; check its code (hhmm is an integer, 09:20 is 920; t is epoch "
                      f"seconds) and test it again")
        if all(x is None for x in out[m["name"]][-5:]):
            problems.append(f"{m['name']} has no value on the latest bars")
    for c in cuts:
        r = res.get(f"cut{c}") or {}
        for m in measures:
            short = (r.get("value") or {}).get(m["name"])
            if not isinstance(short, list):
                continue
            for i in range(min(len(short), c)):
                a, b = short[i], out[m["name"]][i]
                if (a is None) != (b is None) or (a is not None and abs(float(a) - b) > 1e-6 * max(1.0, abs(b))):
                    raise Bad(f"measure {m['name']} changes its value on bar {i} when later bars "
                              f"arrive, so it reads the future. Compute each bar from that bar and "
                              f"earlier ones only.")
    return out, problems


# ══ conditions ══════════════════════════════════════════════════════════════
#
# when := {"all": [cond, ...]} | {"any": [cond, ...]} | cond
# cond := {"left": operand, "op": OP, "right": operand, "right2"?: operand,
#          "bars"?: int (for rises_pct/falls_pct), "for_bars"?: int}
#       | {"time": {"after": "09:20", "before": "15:00"}} | {"time": {"at": "09:20"}}
# operand := a number | a measure name | close/open/high/low/volume (clock symbol)
#          | "SYMBOL.close" (another instrument's bar field)
#          | arithmetic over those names: "hi_since - 1.5 * atr14", "or_high + 0.25 * or_range"

_SYM_FIELD = re.compile(r"^.+\.(open|high|low|close|volume)$", re.I)
_EXPR_FUNCS = {"abs": abs, "min": min, "max": max}


def _is_expr(s: str) -> bool:
    return (isinstance(s, str) and not _SYM_FIELD.match(s.strip())
            and bool(re.search(r"[-+*/()]", s)))


def _expr_tree(s: str) -> ast.Expression:
    try:
        tree = ast.parse(s.strip(), mode="eval")
    except SyntaxError:
        raise Bad(f"operand {s!r} is not a name, a number or arithmetic over names") from None
    ok = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.USub,
          ast.UAdd, ast.Constant, ast.Name, ast.Load, ast.Call)
    for n in ast.walk(tree):
        if not isinstance(n, ok) or (isinstance(n, ast.Constant) and not isinstance(n.value, (int, float))) \
                or (isinstance(n, ast.Call) and not (isinstance(n.func, ast.Name) and n.func.id in _EXPR_FUNCS
                                                     and not n.keywords)):
            raise Bad(f"operand {s!r}: only + - * /, parentheses, numbers, names and abs/min/max")
    return tree


def _expr_names(s: str) -> list[str]:
    return sorted({n.id for n in ast.walk(_expr_tree(s)) if isinstance(n, ast.Name)
                   and n.id not in _EXPR_FUNCS})


def _eval_expr(s: str, data: dict, series: dict, n: int) -> list:
    tree = _expr_tree(s)
    cols = {k: _resolve(k, data, series, n) for k in _expr_names(s)}

    def ev(node, i):
        if isinstance(node, ast.Expression):
            return ev(node.body, i)
        if isinstance(node, ast.Constant):
            return float(node.value)
        if isinstance(node, ast.Name):
            return cols[node.id][i]
        if isinstance(node, ast.UnaryOp):
            v = ev(node.operand, i)
            return None if v is None else (-v if isinstance(node.op, ast.USub) else v)
        if isinstance(node, ast.Call):
            args = [ev(a, i) for a in node.args]
            return None if any(a is None for a in args) else _EXPR_FUNCS[node.func.id](*args)
        a, b = ev(node.left, i), ev(node.right, i)
        if a is None or b is None:
            return None
        if isinstance(node.op, ast.Add):
            return a + b
        if isinstance(node.op, ast.Sub):
            return a - b
        if isinstance(node.op, ast.Mult):
            return a * b
        return a / b if b else None
    return [ev(tree, i) for i in range(n)]


def _operands(when) -> list:
    out = []
    if isinstance(when, dict):
        for k in ("all", "any"):
            for c in when.get(k) or []:
                out += _operands(c)
        for k in ("left", "right", "right2"):
            if k in when:
                out.append(when[k])
    return out


def _resolve(op, data: dict, series: dict, n: int) -> list:
    if isinstance(op, bool):
        return [1.0 if op else 0.0] * n
    if isinstance(op, (int, float)):
        return [float(op)] * n
    s = str(op).strip()
    try:
        return [float(s.replace(",", ""))] * n
    except ValueError:
        pass
    key = _clean_name(s)
    if key in series:
        return series[key]
    if key in data and key not in ("t", "hhmm"):
        return data[key]
    if _is_expr(s):
        return _eval_expr(s, data, series, n)
    if key in POSITION:
        raise Bad(f"{key} describes an open position, so only manage.exit_when can read it")
    raise Bad(f"condition operand {op!r} is not a number, a field ({', '.join(FIELDS)}) or a "
              f"measure in this play")


def _time_ok(spec: dict, hhmm: int) -> bool:
    def parse(x):
        m = _HHMM.match(str(x or "").strip())
        if not m:
            raise Bad(f"time {x!r} must be HH:MM (24 hour, IST)")
        return int(m.group(1)) * 100 + int(m.group(2))
    if "at" in spec:
        return hhmm == parse(spec["at"])
    lo = parse(spec["after"]) if spec.get("after") else 0
    hi = parse(spec["before"]) if spec.get("before") else 2400
    return lo <= hhmm <= hi


def truth(when, data: dict, series: dict) -> list:
    """One value per bar: True, False, or None when an input is missing (the
    play holds; missing data never fires anything)."""
    n = len(data["t"])
    if not when:
        return [False] * n
    if "all" in when or "any" in when:
        parts = [truth(c, data, series) for c in (when.get("all") or when.get("any") or [])]
        if not parts:
            return [False] * n
        out = []
        for i in range(n):
            vals = [p[i] for p in parts]
            if "all" in when:
                out.append(False if any(v is False for v in vals) else
                           None if any(v is None for v in vals) else True)
            else:
                out.append(True if any(v is True for v in vals) else
                           None if any(v is None for v in vals) else False)
        return out
    if "time" in when:
        return [_time_ok(when["time"], h) for h in data["hhmm"]]
    op = when.get("op")
    if op not in OPS:
        raise Bad(f"condition op {op!r} must be one of {', '.join(OPS)}")
    L = _resolve(when.get("left"), data, series, n)
    if op == "is_true":
        out = [None if x is None else bool(x) for x in L]
    elif op in ("rises_pct", "falls_pct"):
        k = max(1, int(when.get("bars") or 1))
        pct = _resolve(when.get("right"), data, series, n)
        out = []
        for i in range(n):
            if i < k or L[i] is None or L[i - k] in (None, 0) or pct[i] is None:
                out.append(None)
                continue
            ch = (L[i] / L[i - k] - 1) * 100
            out.append(ch >= pct[i] if op == "rises_pct" else -ch >= pct[i])
    else:
        R = _resolve(when.get("right"), data, series, n)
        R2 = _resolve(when["right2"], data, series, n) if op in ("between", "outside") else None
        out = []
        for i in range(n):
            l, r = L[i], R[i]
            if l is None or r is None:
                out.append(None)
            elif op == "above":
                out.append(l > r)
            elif op == "below":
                out.append(l < r)
            elif op in ("between", "outside"):
                r2 = R2[i]
                if r2 is None:
                    out.append(None)
                else:
                    inside = min(r, r2) <= l <= max(r, r2)
                    out.append(inside if op == "between" else not inside)
            else:
                pl, pr = (L[i - 1], R[i - 1]) if i else (None, None)
                if pl is None or pr is None:
                    out.append(None)
                elif op == "cross_up":
                    out.append(pl <= pr and l > r)
                else:
                    out.append(pl >= pr and l < r)
    k = int(when.get("for_bars") or 1)
    if k > 1:
        held = []
        for i in range(n):
            w = out[max(0, i - k + 1):i + 1]
            held.append(None if len(w) < k or any(v is None for v in w) else all(w))
        out = held
    return out


def fire_points(tr: list) -> list[int]:
    """Bars where the whole condition BECOMES true. A condition that stays
    true is one event, not one per bar."""
    return [i for i in range(1, len(tr)) if tr[i] is True and tr[i - 1] is not True]


# ══ compiling a play ════════════════════════════════════════════════════════

def _hours(spec) -> str:
    return str(spec or "")


def _norm_on(raw, sym: str) -> dict | None:
    """The events a play is clocked by: {sources, symbols, match, judge}."""
    if not raw:
        return None
    if not isinstance(raw, dict):
        raise Bad("on must be {sources, symbols, match, judge}")
    import play_events
    srcs = [str(x).lower().strip() for x in (raw.get("sources") or ["filings", "news"])]
    off = sorted({x for x in srcs if x.split(":")[0].lstrip("@") in play_events.UNCONNECTED})
    if off:
        raise Bad(f"{', '.join(off)} {'is' if len(off) == 1 else 'are'} not connected yet. The sources "
                  f"are filings (NSE corporate announcements) and news (Economic Times, Mint, "
                  f"CNBC-TV18, BusinessLine, BBC, CoinDesk headlines). Use those and say so.")
    bad = [x for x in srcs if x not in play_events.SOURCES]
    if bad:
        raise Bad(f"on.sources must be filings and/or news, not {', '.join(bad)}")
    syms = [ds.canon_symbol(str(x)) for x in raw.get("symbols") or [] if str(x).strip()]
    words = [str(w).strip() for w in raw.get("match") or [] if str(w).strip()][:20]
    question = str(raw.get("judge") or "").strip()[:400]
    if not (syms or words or question):
        raise Bad("on needs symbols (whose filings), match (words an item must contain) or judge "
                  "(the question each item must answer yes to)")
    if "news" in srcs and not (words or question):
        raise Bad("a news source needs match words or a judge question, or every headline fires")
    return {"sources": srcs, "symbols": syms, "match": words, "judge": question,
            "min_confidence": float(raw.get("min_confidence") or 0.6)}


def _norm_when(when) -> dict | None:
    when = when or None
    if isinstance(when, list):
        when = {"all": when}
    if when and ("left" in when or "time" in when):
        when = {"all": [when]}
    return when


def normalise(raw: dict, uid: int, chart_symbol: str = "", chart_interval: str = "") -> tuple[dict, list[str]]:
    """The model's play, checked and filled in. Returns (spec, assumptions):
    every default this function chose is listed, so the reply can say it."""
    if not isinstance(raw, dict):
        raise Bad("play must be an object")
    said: list[str] = []
    uni = None
    if raw.get("universe"):
        import scan
        u = raw["universe"]
        try:
            if isinstance(u, dict) and isinstance(u.get("symbols"), list):
                # a resolved universe (an edit of a stored play) keeps its names
                syms = [ds.canon_symbol(str(x)) for x in u["symbols"] if str(x or "").strip()]
                words, more = str(u.get("words") or f"the {len(syms)} symbols named"), []
            else:
                words, syms, more = scan.resolve(u, uid)
        except scan.Bad as exc:
            raise Bad(f"universe: {exc}") from None
        if len(syms) > UNIVERSE_MAX:
            raise Bad(f"the universe has {len(syms)} names and a play watches at most {UNIVERSE_MAX}: scan "
                      f"first (measure action scan) and give the play the shortlist, or narrow it")
        uni = {"words": words, "symbols": syms}
        said += more
        raw = {**raw, "symbol": syms[0]}
    sym = ds.canon_symbol(str(raw.get("symbol") or chart_symbol or ""))
    if not sym and (raw.get("do") or {}).get("kind") == "rebalance":
        sym = "NIFTY 50" if ds._symbol_ready("NIFTY 50") else "RELIANCE"   # only its daily clock
    if not sym:
        raise Bad("play needs a symbol (the instrument whose bars it watches)")
    if customsec.is_custom(sym) and not customsec.get(sym):
        raise Bad(f"there is no custom security {sym}; define it first (measure, action security)")
    iv = str(raw.get("interval") or chart_interval or "5m")
    iv = {"D": "1d", "1D": "1d", "day": "1d", "daily": "1d", "60m": "1h"}.get(iv, iv)
    if iv not in INTERVALS:
        raise Bad(f"interval must be one of {', '.join(INTERVALS)}")
    if not raw.get("interval") and (raw.get("do") or {}).get("kind") != "halt":
        said.append(f"checks on each {iv} bar close")

    measures, seen = [], set()
    lib = {m["name"]: m for m in list_measures(uid)} if uid else {}
    for m in raw.get("measures") or []:
        nm = _norm_measure(m, uid)
        if nm["name"] in seen:
            raise Bad(f"two measures are named {nm['name']}")
        seen.add(nm["name"])
        measures.append(nm)

    when = _norm_when(raw.get("when"))
    exit_when = _norm_when((raw.get("manage") or {}).get("exit_when"))
    names = []
    for op in _operands(when) + _operands(exit_when):
        if isinstance(op, str):
            names += _expr_names(op) if _is_expr(op) else [op]
    if any(_clean_name(x) in POSITION for x in _operands(when) if isinstance(x, str)) or \
            any(_clean_name(x) in POSITION for op in _operands(when) if _is_expr(op)
                for x in _expr_names(op)):
        raise Bad(f"{', '.join(POSITION)} describe an open position: use them in manage.exit_when, "
                  f"not in when")
    # operands naming a saved measure pull it in; "SYMBOL.field" becomes an input
    for op in names:
        key = _clean_name(op)
        if key in POSITION:
            continue
        if key in seen or key in FIELDS:
            continue
        if key in lib:
            measures.insert(0, lib[key])
            seen.add(key)
            continue
        if "." in op:
            s, _, f = op.rpartition(".")
            f = f.lower()
            if f in FIELDS and s:
                s = ds.canon_symbol(s)
                alias = _clean_name(op)
                measures.insert(0, {"name": alias, "title": f"{s} {f}", "formula": op,
                                    "inputs": {"x": {"bars": {"symbol": s, "field": f}}},
                                    "expr": "x", "unit": ""})
                seen.add(alias)

    on = _norm_on(raw.get("on"), sym)
    contract = dict(raw.get("contract") or {})
    rebalance = (raw.get("do") or {}).get("kind") == "rebalance"
    lifespan = contract.get("lifespan") or ("standing" if uni and when else
                                            "once_when" if (when or on) else "now")
    if uni and (on or (raw.get("do") or {}).get("kind") in ("rebalance", "halt")):
        raise Bad("a universe is for a rule read on each name's own bars; events, rebalances and "
                  "guards take one symbol")
    if uni and (not when or lifespan == "now"):
        raise Bad("a play over a universe needs `when`: the rule each name is checked against, on its "
                  "own bars")
    if lifespan not in LIFESPANS:
        raise Bad(f"contract.lifespan must be one of {', '.join(LIFESPANS)}")
    if lifespan != "now" and not when and not on and not (rebalance and lifespan == "policy"):
        raise Bad("this play has no `when` and no `on`; give conditions or events, or set "
                  "contract.lifespan to now")
    authority = contract.get("authority") or "notify"
    if authority not in AUTHORITIES:
        raise Bad(f"contract.authority must be one of {', '.join(AUTHORITIES)}")
    discretion = contract.get("discretion") or "none"
    if discretion not in DISCRETIONS:
        raise Bad(f"contract.discretion must be one of {', '.join(DISCRETIONS)}")
    if discretion == "judge" or (discretion == "plan" and not rebalance):
        said.append(f"{discretion} discretion is not wired for this kind of play yet, so it runs "
                    "as a fixed rule")
        discretion = "none"
    if lifespan == "policy" and not rebalance:
        said.append("standing policies with a planner are not wired yet, so it runs every time "
                    "the condition is met")
        lifespan = "standing"
    if not contract.get("authority"):
        said.append("it tells you when it fires and places nothing")

    do = dict(raw.get("do") or {})
    kind = do.get("kind") or ("notify" if authority == "notify" else "buy")
    if kind not in DO_KINDS:
        raise Bad(f"do.kind must be one of {', '.join(DO_KINDS)}")
    if kind == "notify" and authority != "notify":
        authority = "notify"
    if kind == "halt":
        if not when:
            raise Bad("a halt needs `when`: the condition that stops the day (a book input such as "
                      "day_pnl or loss_streak, a time, a market move)")
        # stopping trading only ever reduces risk, so it never waits for a tap
        authority = "act"
        if iv == "1d":
            iv = "5m"
        said.append("checked after every fill and exit in the day book, and on each 5m bar")
        if not contract.get("lifespan"):
            lifespan = "standing"
        do = {"kind": "halt", "close": bool(do.get("close"))}
        said[:] = [x for x in said if not x.startswith("it tells you")]
    mg_raw = raw.get("manage") or {}
    intraday = bool(mg_raw.get("exit_at") or mg_raw.get("trail_pct") or mg_raw.get("on_leg_stop")
                    or mg_raw.get("max_loss_inr") or mg_raw.get("target_inr"))
    product = str(do.get("product") or "").upper()
    perp = hyperliquid.is_perp(str(do.get("symbol") or sym)) and kind not in ("options", "future", "rebalance")
    if perp:
        product = "PERP"
        # a perp is clocked on its own candles (Hyperliquid, 24 hours), not on
        # a spot pair of the same coin that may have no live stream here
        dsym = ds.canon_symbol(str(do.get("symbol") or sym))
        if sym != dsym and re.sub(r"(USDT|-USD|USD)$", "", sym) == hyperliquid.coin(dsym):
            said.append(f"checks {dsym}'s own bars rather than {sym}'s")
            sym = dsym
    if kind == "short" and product == "CNC" and not str(do.get("symbol") or sym).endswith(("USDT", "USD")):
        raise Bad("a short in Indian equities is intraday only (product MIS, squared off at 15:20); "
                  "delivery shorts are not allowed. Say so, and offer the intraday short")
    if perp:
        pass
    elif kind in ("short", "cover", "options", "future"):
        product = product if product in ("MIS", "NRML") else ("MIS" if intraday or kind in ("short", "cover")
                                                             else "NRML")
    elif kind in ("buy", "sell"):
        product = product if product in ("MIS", "CNC") else ("MIS" if intraday else "CNC")
    do["product"] = product or None
    if kind in ("options", "future"):
        und = str(do.get("underlying") or sym)
        do["underlying"] = und.upper()
        if kind == "options":
            legs = do.get("legs") or []
            if not legs:
                raise Bad("do.legs is the option legs: [{opt: CE|PE, strike: ATM|ATM+1|ATM-2|22750, "
                          "side: buy|sell, lots, expiry: weekly|next|monthly|YYYY-MM-DD}]")
            for lg in legs:
                if str(lg.get("opt") or "").upper() not in ("CE", "PE"):
                    raise Bad("each option leg needs opt CE or PE")
                if str(lg.get("side") or "").lower() not in ("buy", "sell"):
                    raise Bad("each option leg needs side buy or sell")
                lg["opt"], lg["side"] = lg["opt"].upper(), lg["side"].lower()
                lg["strike"] = str(lg.get("strike") or "ATM")
                lg["lots"] = int(lg.get("lots") or 1)
        if authority == "notify":
            authority = "propose"
            said.append("it proposes the order for you to confirm")
    elif kind not in ("notify", "halt"):
        do["symbol"] = ds.canon_symbol(str(do.get("symbol") or sym))
        size = do.get("size") or {}
        if not isinstance(size, dict):
            size = {"inr": size}
        if not size.get("inr") and not size.get("qty") and not size.get("all"):
            size = {"inr": DEFAULT_SIZE_INR} if kind in ("buy", "short") else {"all": True}
            said.append(f"sized at ₹{DEFAULT_SIZE_INR:,}" if kind in ("buy", "short")
                        else "closes what this play holds")
        do["size"] = size
        if perp:
            c = hyperliquid.ctx(do["symbol"])
            if not c:
                raise Bad(f"Hyperliquid lists no perpetual {do['symbol']}; perps are written COIN-PERP "
                          "(BTC-PERP, ETH-PERP, SOL-PERP)")
            if kind in ("buy", "short"):
                lev = do.get("leverage")
                if lev in (None, ""):
                    lev = 1
                    said.append("at 1× leverage (the rupees are the margin)")
                lev = float(lev)
                if not 1 <= lev <= c["max_leverage"]:
                    raise Bad(f"{do['symbol']} allows 1× to {c['max_leverage']}× leverage on Hyperliquid")
                do["leverage"] = lev
        if customsec.is_custom(do["symbol"]):
            sec = customsec.get(do["symbol"])
            if not sec:
                raise Bad(f"there is no custom security {do['symbol']}; define it first")
            if sec["kind"] != "basket":
                raise Bad(f"{do['symbol']} is a {sec['kind']}, a number to watch, not something to "
                          "hold; buy or sell one of its members instead")
            if kind not in ("buy", "sell") or do["product"] == "MIS" or intraday:
                raise Bad("a basket is bought or sold whole, for delivery (CNC); intraday, shorts "
                          "and trailing exits are per instrument")
        if authority == "notify":
            authority = "propose"
            said.append("it proposes the order for you to confirm")
    do["kind"] = kind
    pol = None
    if kind == "rebalance":
        do, pol = _norm_rebalance(do, raw, lifespan, discretion, said)
        iv = "1d"

    manage = {}
    for k in ("stop_pct", "target_pct"):
        if (raw.get("manage") or {}).get(k) is not None:
            v = float(raw["manage"][k])
            if not 0 < v < 100:
                raise Bad(f"manage.{k} is a percent between 0 and 100")
            manage[k] = v
    ex = (raw.get("manage") or {}).get("exit_at")
    if ex:
        if not _HHMM.match(str(ex)):
            raise Bad("manage.exit_at must be HH:MM (IST)")
        manage["exit_at"] = str(ex)
    if mg_raw.get("trail_pct") is not None:
        v = float(mg_raw["trail_pct"])
        if not 0 < v < 100:
            raise Bad("manage.trail_pct is a percent between 0 and 100")
        manage["trail_pct"] = v
    if mg_raw.get("on_leg_stop"):
        if mg_raw["on_leg_stop"] not in ("exit_all", "move_others_to_cost"):
            raise Bad("manage.on_leg_stop is exit_all or move_others_to_cost")
        manage["on_leg_stop"] = mg_raw["on_leg_stop"]
    for k in ("max_loss_inr", "target_inr"):
        if mg_raw.get(k):
            manage[k] = abs(float(mg_raw[k]))
    if exit_when:
        if kind not in ("buy", "short", "options", "future"):
            raise Bad("manage.exit_when closes a position, so the play must open one (do.kind buy, "
                      "short, options or future)")
        manage["exit_when"] = exit_when
    if (manage.get("trail_pct") or manage.get("on_leg_stop") or manage.get("max_loss_inr")
            or manage.get("target_inr")) and do.get("product") not in ("MIS", "NRML", "PERP"):
        raise Bad("trailing stops, leg rules and profit or loss exits run in the day book: set "
                  "do.product MIS (intraday) or use a short, options or future order")

    if uni:
        if kind in ("buy", "sell", "short", "cover"):
            if customsec.is_custom(do.get("symbol") or ""):
                raise Bad("a universe play trades each name itself, not a basket")
            do["symbol"] = "*"
        if kind in ("options", "future"):
            do["underlying"] = "*"
    cap = float((raw.get("mandate") or {}).get("capital_inr")
                or ((do.get("size") or {}).get("inr") or 0) * (1 if lifespan != "standing" else 5)
                or (DERIV_CAPITAL if kind in ("options", "future") else DEFAULT_SIZE_INR))
    if kind in ("options", "future") and not (raw.get("mandate") or {}).get("capital_inr"):
        said.append(f"margin limit ₹{DERIV_CAPITAL:,} (estimated at fire)")
    mandate = {"capital_inr": cap,
               "max_order_inr": float((raw.get("mandate") or {}).get("max_order_inr") or cap),
               "max_fires": int((raw.get("mandate") or {}).get("max_fires")
                                or (1 if lifespan in ("now", "once_when") else
                                    10_000 if lifespan == "policy" or kind == "halt" else 20))}
    if uni:
        mx = int((raw.get("mandate") or {}).get("max_open") or (0 if kind == "notify" else 5))
        mandate["max_open"] = mx
        if not (raw.get("mandate") or {}).get("max_fires"):
            mandate["max_fires"] = 500
        if mx and not (raw.get("mandate") or {}).get("capital_inr") and (do.get("size") or {}).get("inr"):
            mandate["capital_inr"] = float(do["size"]["inr"]) * mx
        said.append(f"watches {len(uni['symbols'])} names ({uni['words']}), each on its own bars"
                    + (f", holding at most {mx} at once" if mx else ""))
    if kind == "rebalance":
        mandate["max_weight"] = float((raw.get("mandate") or {}).get("max_weight")
                                      or policy.DEFAULT_MAX_WEIGHT)
        if not (raw.get("mandate") or {}).get("max_order_inr"):
            mandate["max_order_inr"] = 10_00_000
    days = contract.get("expires_in_days")
    spec = {
        "title": str(raw.get("title") or "")[:80] or f"{sym} play",
        "ask": str(raw.get("ask") or "")[:600],
        "thesis": str(raw.get("why") or raw.get("thesis") or "").strip()[:500],
        "symbol": sym, "interval": iv, "measures": measures, "when": when, "on": on, "universe": uni,
        "do": do, "manage": manage, "mandate": mandate, "policy": pol,
        "contract": {"lifespan": lifespan, "authority": authority, "discretion": discretion,
                     "inferred_from": str(contract.get("inferred_from") or "")[:200],
                     "expires_in_days": int(days) if days else None},
    }
    return spec, said


def _norm_rebalance(do: dict, raw: dict, lifespan: str, discretion: str,
                    said: list[str]) -> tuple[dict, dict | None]:
    of = do.get("of") or "self"
    if isinstance(of, str) and of.isdigit():
        of = int(of)
    if of not in ("self", "portfolio") and not isinstance(of, int):
        raise Bad("do.of is self (what this play holds), portfolio (the whole paper delivery book) "
                  "or the id of the play whose holdings it takes over")
    out = {"kind": "rebalance", "of": of, "product": "CNC",
           "objective": str(do.get("objective") or "")[:300],
           "universe": sorted({ds.canon_symbol(str(x)) for x in do.get("universe") or []})[:30],
           "cash_pct": float(do.get("cash_pct") or 0)}
    size = do.get("size") or {}
    if not isinstance(size, dict):
        size = {"inr": size}
    if size.get("inr"):
        out["size"] = {"inr": float(size["inr"])}
    if do.get("targets"):
        rs = do["targets"] if isinstance(do["targets"], list) else []
        out["reasons"] = {ds.canon_symbol(str(x.get("symbol"))): str(x["why"]).strip()[:200]
                          for x in rs if isinstance(x, dict) and x.get("why")} or \
            {k: v for k, v in (do.get("reasons") or {}).items() if k in do["targets"]}
        cap = float((raw.get("mandate") or {}).get("max_weight") or policy.DEFAULT_MAX_WEIGHT)
        try:
            out["targets"], cut = policy.norm_targets(do["targets"], out["cash_pct"], cap)
        except policy.Bad as exc:
            raise Bad(str(exc)) from None
        said.extend(cut)
    elif discretion != "plan" and of == "self":
        raise Bad("a rebalance of its own holdings needs targets: [{symbol, weight}], or "
                  "contract.discretion plan with an objective so the planner chooses")
    if discretion == "plan" and not out["objective"]:
        raise Bad("a planned rebalance needs do.objective: the trader's instruction in their words")
    if discretion == "plan" and not out["universe"] and not out.get("targets") and of == "self":
        raise Bad("a planned rebalance needs do.universe: the names the planner may choose from")
    pol = None
    if lifespan == "policy":
        p = raw.get("policy") or {}
        every = p.get("every") or "week"
        if every not in policy.EVERY:
            raise Bad("policy.every is day, week or month")
        pol = {"every": every, "drift_pct": float(p.get("drift_pct") or 5)}
        if not raw.get("policy"):
            said.append("checks weekly and whenever a holding drifts 5 points from its target")
    return out, pol


def _event_points(spec: dict, data: dict, tr: list) -> tuple[list[int], list[str], list[dict]]:
    """Bars where a past event would have fired the play: each stored item the
    words match, on the first bar that closed after it was published, where
    the play's own conditions held. The judge reads new items only, so the
    count on history is of items that MATCH, and the note says so."""
    import play_events
    on = spec["on"]
    if "filings" in on["sources"]:
        for sym in on["symbols"] or []:
            try:
                play_events.backfill_filings(sym)
            except Exception:                               # noqa: BLE001
                pass
    t = data["t"]
    items = [it for it in play_events.history(on["sources"], t[0], on["symbols"] or None)
             if play_events.matches(it, on)]
    pts, hits, j = [], [], 0
    for it in items:
        while j < len(t) and _bar_close(t[j], spec["interval"]) < it["ts"]:
            j += 1
        if j >= len(t):
            break
        if tr and tr[j] is not True:
            continue
        pts.append(j)
        hits.append({"ts": it["ts"], "who": it["who"], "title": it["title"], "url": it["url"]})
    notes = []
    if items:
        first = min(it["ts"] for it in items)
        notes.append(f"history counts {len(items)} stored item(s) since "
                     f"{time.strftime('%d %b %Y', time.gmtime(first + ds.IST_OFF))} that match its words"
                     + ("; the judge's question is asked of new items only" if on["judge"] else ""))
    else:
        notes.append("no stored filing or headline matches it yet, so history cannot show a count; "
                     "it reads every new one")
    return pts, notes, hits[-5:]


# ── round trips on history ───────────────────────────────────────────────────
# A fire count says how often; it does not say what happened next. The replay
# also walks every entry forward through the play's own exits on the clock
# symbol's bars, and walks the same exits from entries with no condition at
# all (the same hours of the day) as the control. Measured on the clock
# symbol's price: for an option play that is the underlying's move, not the
# premium, and the card says so.

def _direction(spec: dict) -> int:
    do = spec["do"]
    if do["kind"] == "short":
        return -1
    if do["kind"] == "future":
        return -1 if str(do.get("side") or "buy").lower() in ("sell", "short") else 1
    if do["kind"] == "options":
        v = 0
        for lg in do.get("legs") or []:
            bull = (lg["opt"] == "CE") == (lg["side"] == "buy")
            v += (1 if bull else -1) * int(lg.get("lots") or 1)
        return (v > 0) - (v < 0)
    return 1


def _position_series(data: dict, i0: int, d: int) -> dict:
    n, c, h, lo = len(data["t"]), data["close"], data["high"], data["low"]
    out = {k: [None] * n for k in POSITION}
    e = c[i0]
    if e is None:
        return out
    hh = ll = e
    for j in range(i0, n):
        if j > i0:
            hh = max(hh, h[j]) if h[j] is not None else hh
            ll = min(ll, lo[j]) if lo[j] is not None else ll
        out["entry"][j], out["bars_in"][j], out["hi_since"][j], out["lo_since"][j] = e, float(j - i0), hh, ll
        out["pnl_pct"][j] = (d or 1) * (c[j] / e - 1) * 100 if c[j] is not None else None
    return out


def _uses_position(when) -> bool:
    for op in _operands(when):
        if isinstance(op, str):
            names = _expr_names(op) if _is_expr(op) else [op]
            if any(_clean_name(x) in POSITION for x in names):
                return True
    return False


def _day_of(t: int) -> int:
    return (t + ds.IST_OFF) // 86400


def _walk(spec: dict, data: dict, series: dict, i0: int, d: int, ex_tr: list | None) -> dict:
    """One position from bar i0's close through the play's exits."""
    mg, do = spec.get("manage") or {}, spec["do"]
    t, o, h, lo, c, hm = (data[k] for k in ("t", "open", "high", "low", "close", "hhmm"))
    n, e = len(t), c[i0]
    pct = do["kind"] != "options" and d       # an option's percents are of its premium
    sd = d or 1
    stop = e * (1 - sd * mg["stop_pct"] / 100) if pct and mg.get("stop_pct") else None
    target = e * (1 + sd * mg["target_pct"] / 100) if pct and mg.get("target_pct") else None
    trail = mg.get("trail_pct") / 100 if pct and mg.get("trail_pct") else None
    if mg.get("exit_when") and ex_tr is None:
        ex_tr = truth(mg["exit_when"], data, {**series, **_position_series(data, i0, d)})
    cut = None
    if spec["interval"] != "1d":
        if mg.get("exit_at"):
            hh, mm = (int(x) for x in mg["exit_at"].split(":"))
            cut = hh * 100 + mm
        if do.get("product") == "MIS" and not hyperliquid.is_perp(spec["symbol"]):
            cut = min(cut or 1520, 1520)
    best = e
    for j in range(i0 + 1, n):
        if None in (o[j], h[j], lo[j], c[j]):
            continue
        if cut and _day_of(t[j]) != _day_of(t[i0]):
            return {"i": i0, "j": j - 1, "px": c[j - 1], "why": "square-off"}
        adverse, favour = (lo[j], h[j]) if sd > 0 else (h[j], lo[j])
        worse = (lambda a, b: min(a, b)) if sd > 0 else (lambda a, b: max(a, b))
        better = (lambda a, b: max(a, b)) if sd > 0 else (lambda a, b: min(a, b))
        lvl = stop
        if trail:
            ts = best * (1 - sd * trail)
            lvl = ts if lvl is None else better(lvl, ts)
        if lvl is not None and (adverse <= lvl if sd > 0 else adverse >= lvl):
            return {"i": i0, "j": j, "px": worse(o[j], lvl), "why": "trailing stop" if trail else "stop"}
        if target is not None and (favour >= target if sd > 0 else favour <= target):
            return {"i": i0, "j": j, "px": better(o[j], target), "why": "target"}
        best = better(best, favour)
        if ex_tr and ex_tr[j] is True:
            return {"i": i0, "j": j, "px": c[j], "why": "exit rule"}
        if cut and hm[j] >= cut:
            return {"i": i0, "j": j, "px": c[j], "why": "time exit"}
    return {"i": i0, "j": n - 1, "px": c[n - 1], "why": "open"}


def _stats(rets: list[float]) -> dict:
    if not rets:
        return {"n": 0}
    wins, losses = [r for r in rets if r > 0], [r for r in rets if r <= 0]
    srt = sorted(rets)
    out = {"n": len(rets), "win_rate": round(len(wins) / len(rets), 3),
           "expectancy": round(sum(rets) / len(rets), 3), "median": round(srt[len(srt) // 2], 3),
           "avg_win": round(sum(wins) / len(wins), 3) if wins else None,
           "avg_loss": round(sum(losses) / len(losses), 3) if losses else None,
           "best": round(srt[-1], 3), "worst": round(srt[0], 3)}
    if losses and sum(losses):
        out["profit_factor"] = round(sum(wins) / -sum(losses), 2) if wins else 0.0
    return out


def _time_only(when) -> dict | None:
    conds = [c for c in (when or {}).get("all") or [] if "time" in c]
    return {"all": conds} if conds else None


def round_trips(spec: dict, data: dict, series: dict, tr: list) -> dict | None:
    """Every entry on history walked through the exits, one position at a
    time, against the same exits from unconditioned entries."""
    do, mg = spec["do"], spec.get("manage") or {}
    if do["kind"] not in ("buy", "short", "options", "future") or spec.get("on") or not tr:
        return None
    d = _direction(spec)
    pos_aware = bool(mg.get("exit_when")) and _uses_position(mg["exit_when"])
    ex_once = (truth(mg["exit_when"], data, series)
               if mg.get("exit_when") and not pos_aware else None)
    has_exit = bool(mg.get("exit_when") or mg.get("exit_at") or (do.get("product") == "MIS"
                    and spec["interval"] != "1d") or (do["kind"] != "options" and d and
                    (mg.get("stop_pct") or mg.get("target_pct") or mg.get("trail_pct"))))
    n = len(data["t"])
    sd = d or 1
    ret = (lambda w: sd * (w["px"] / data["close"][w["i"]] - 1) * 100) if d else \
        (lambda w: abs(w["px"] / data["close"][w["i"]] - 1) * 100)
    out = {"basis": (f"{spec['symbol']}'s move" + (", not the option's premium" if do["kind"] == "options"
                                                    else "")), "direction": d, "notes": []}
    if not has_exit:
        # nothing closes it: say what followed instead, at two horizons, against every bar
        hs = (5, 20) if spec["interval"] == "1d" else (3, 12)
        fwd = {}
        for hz in hs:
            pts = [i for i in fire_points(tr) if i + hz < n]
            base = [i for i in range(n - hz) if data["close"][i]]
            f = lambda ii: [sd * (data["close"][i + hz] / data["close"][i] - 1) * 100 for i in ii]
            fwd[str(hz)] = {"after": _stats(f(pts)), "any_bar": _stats(f(base))}
        out.update(kind="forward", bars=list(hs), forward=fwd)
        out["notes"].append("it has no exit, so history shows what followed each fire rather than trades")
        return out
    trades, i = [], 1
    pts = fire_points(tr)
    for p in pts:
        if p < i:
            continue                       # one position at a time
        w = _walk(spec, data, series, p, d, ex_once)
        trades.append(w)
        i = w["j"] + 1
    window = truth(_time_only(spec.get("when")), data, series) if _time_only(spec.get("when")) else None
    pool = [k for k in range(n - 1) if (window is None or window[k] is True) and data["close"][k]]
    step = max(1, len(pool) // (120 if pos_aware else 300))
    ctrl = [ret(_walk(spec, data, series, k, d, ex_once)) for k in pool[::step]]
    rets = [ret(w) for w in trades]
    st = _stats(rets)
    st["avg_bars"] = round(sum(w["j"] - w["i"] for w in trades) / len(trades), 1) if trades else None
    whys: dict[str, int] = {}
    for w in trades:
        whys[w["why"]] = whys.get(w["why"], 0) + 1
    out["_rets"], out["_ctrl"] = rets, ctrl
    out["_ins"] = [data["t"][w["i"]] for w in trades]
    out.update(kind="trades", **st, exits=whys, control=_stats(ctrl),
               list=[{"in": data["t"][w["i"]], "out": data["t"][w["j"]], "entry": data["close"][w["i"]],
                      "exit": round(w["px"], 4), "ret": round(ret(w), 3), "why": w["why"]}
                     for w in trades[-12:]],
               curve=[round(x, 3) for x in _cum(rets)][-120:])
    out.update(_verdict(st, out["control"], d))
    if not d:
        out["notes"].append("a position with no direction: each trade is the size of the move while it "
                            "was held, in either direction")
    if do["kind"] == "options" and (mg.get("stop_pct") or mg.get("target_pct") or mg.get("trail_pct")):
        out["notes"].append("its stop, target and trail are percents of the option's premium, which "
                            "stored history does not carry, so they are not replayed")
    if mg.get("max_loss_inr") or mg.get("target_inr"):
        out["notes"].append("the rupee limits are not replayed")
    if st["n"] and st["n"] < 20:
        out["notes"].append(f"only {st['n']} trades: too few to tell an edge from luck")
    size = (do.get("size") or {}).get("inr")
    if size and do["kind"] in ("buy", "short") and do.get("product") != "PERP" and st["n"]:
        import daybook
        px = data["close"][-1] or 1
        q = max(1.0, size / px)
        cost = daybook.charges("equity", "BUY", px, q) + daybook.charges("equity", "SELL", px, q)
        out["cost_pct"] = round(cost / size * 100, 3)
        out["per_trade_inr"] = round((st["expectancy"] - out["cost_pct"]) / 100 * size, 0)
    return out


def _verdict(st: dict, ctl: dict, d: int) -> dict:
    if not (st.get("n") and ctl.get("n") and d):
        return {}
    edge = st["expectancy"] - ctl["expectancy"]
    return {"edge": round(edge, 3),
            "verdict": ("too_few" if st["n"] < 20 else
                        "ahead" if edge > 0 and st["win_rate"] >= ctl["win_rate"] else
                        "behind" if edge < 0 else "mixed")}


def _cum(rets: list[float]) -> list[float]:
    acc, out = 0.0, []
    for r in rets:
        acc += r
        out.append(acc)
    return out


def replay(spec: dict, check: bool = True, keep_raw: bool = False, pre: tuple | None = None) -> dict:
    """The play over stored history: the same measures and the same condition
    code the live bar runs. What the trader sees before it is armed."""
    if spec.get("universe") and not spec.get("member"):
        return replay_universe(spec)
    if pre:
        (data, notes), (series, problems) = pre
    else:
        data, notes = frame(spec)
        series, problems = run_measures(spec, data, check=check)
    tr = truth(spec["when"], data, series) if spec.get("when") else []
    hits = []
    if spec.get("on"):
        pts, more, hits = _event_points(spec, data, tr)
        notes = notes + more
    else:
        pts = fire_points(tr) if tr else []
    n = len(data["t"])
    first, last = data["t"][0], data["t"][-1]
    span_days = max(1.0, (last - first) / 86400)
    now_vals = {}
    for name, s in series.items():
        now_vals[name] = s[-1] if s else None
    out = {
        "bars": n, "from": first, "to": last, "fires": len(pts),
        "per_month": round(len(pts) / span_days * 30.4, 2),
        "last_fire": data["t"][pts[-1]] if pts else None,
        "fire_ts": [data["t"][i] for i in pts[-12:]],
        "true_now": bool(tr and tr[-1] is True),
        "now": {k: (round(v, 6) if isinstance(v, float) else v) for k, v in now_vals.items()},
        "price": data["close"][-1],
        "spark": {k: [round(x, 6) if isinstance(x, float) else x for x in s[-60:]]
                  for k, s in series.items()},
        "notes": notes + problems, "events": hits,
    }
    if spec.get("on") or not spec.get("when"):
        return out, data, series, tr       # nothing to replay: it acts on events or now
    try:
        rt = round_trips(spec, data, series, tr)
    except Bad as exc:
        raise Bad(f"manage.exit_when: {exc}") from None
    if rt:
        out["trades"] = rt if keep_raw else {k: v for k, v in rt.items() if not k.startswith("_")}
    reads_book = any("book" in src for m in spec.get("measures") or [] for src in m["inputs"].values())
    if tr and not pts and reads_book:
        out["notes"].append("it reads your own day book, and nothing in it would have set it off on the "
                            "stored history")
    elif tr and not pts:
        out["notes"].append("it would not have fired once on the stored history; check the levels")
    elif out["per_month"] > 40 and spec["interval"] != "1m":
        out["notes"].append(f"it would have fired about {out['per_month']:.0f} times a month; "
                            f"that is close to every day")
    return out, data, series, tr


def replay_universe(spec: dict) -> tuple:
    """Every member replayed on its own bars, the measures for all of them in
    one sandbox run; fires and round trips pooled, so the sample is the whole
    universe's and the control is too."""
    import indicator_sandbox
    syms = spec["universe"]["symbols"]
    frames, skipped = {}, []
    for s in syms:
        try:
            # stored bars only: 60 names must not start 60 downloads
            frames[s] = frame(_member_spec(spec, s), ensure=False)
        except Bad:
            skipped.append(s)
    if not frames:
        raise Bad(f"none of the universe's {len(syms)} names has stored {spec['interval']} bars")
    first = next(iter(frames))
    run_measures(_member_spec(spec, first), frames[first][0], check=True)   # refuse a look-ahead once
    series_of = {s: {} for s in frames}
    if spec.get("measures"):
        code = _program(spec["measures"], set(frames[first][0]) | {"t"})
        reply = indicator_sandbox.run(code, [{"id": s, "free": True, "bars": d} for s, (d, _) in frames.items()],
                                      timeout=min(120, 20 + len(frames) / 4))
        if not reply.get("ok"):
            raise Bad(f"measure code refused: {reply.get('error')}")
        for r in reply["results"]:
            if r.get("ok"):
                n = len(frames[r["id"]][0]["t"])
                series_of[r["id"]] = {m["name"]: [float(x) if isinstance(x, (int, float)) and not isinstance(x, bool)
                                                  and math.isfinite(x) else None
                                                  for x in ((r.get("value") or {}).get(m["name"]) or [None] * n)]
                                      for m in spec["measures"]}
    fires, per_month, fire_ts, met, rets, ctrl, exits, members, notes = 0, 0.0, [], [], [], [], {}, [], []
    last: list[dict] = []
    timed: list[tuple] = []
    keep = None
    for s, (data, nts) in frames.items():
        sr = series_of.get(s) or {}
        if spec.get("measures") and not sr:
            skipped.append(s)
            continue
        h, data, sr, tr = replay(_member_spec(spec, s), keep_raw=True, pre=((data, nts), (sr, [])))
        keep = keep or (data, sr, tr)
        fires += h["fires"]
        per_month += h["per_month"]
        fire_ts += h.get("fire_ts") or []
        if h.get("true_now"):
            met.append(s)
        t = h.get("trades") or {}
        last += [{**x, "symbol": s} for x in t.get("list") or []]
        rets += t.get("_rets") or []
        timed += list(zip(t.get("_ins") or [], t.get("_rets") or []))
        ctrl += t.get("_ctrl") or []
        for k, v in (t.get("exits") or {}).items():
            exits[k] = exits.get(k, 0) + v
        members.append({"symbol": s, "fires": h["fires"], "trades": t.get("n"),
                        "expectancy": t.get("expectancy"), "now": h.get("true_now")})
        for x in t.get("notes") or []:
            if x not in notes and not x.startswith("only "):
                notes.append(x)
    d0 = next(iter(frames.values()))[0]
    out = {"bars": len(d0["t"]), "from": d0["t"][0], "to": d0["t"][-1], "fires": fires,
           "per_month": round(per_month, 2), "fire_ts": sorted(fire_ts)[-12:],
           "last_fire": max(fire_ts) if fire_ts else None, "met_now": met,
           "members": sorted(members, key=lambda m: -(m["fires"] or 0)),
           "notes": ([f"{len(skipped)} of {len(syms)} names have no stored {spec['interval']} bars and are "
                      f"not watched: {', '.join(skipped[:8])}"] if skipped else []), "now": {}, "spark": {},
           "price": None}
    if rets or ctrl:
        st = _stats(rets)
        tr0 = {"kind": "trades", "basis": "each name's own move" + (", not the option's premium"
                                                                    if spec["do"]["kind"] == "options" else ""),
               "direction": _direction(spec), **st, "exits": exits, "control": _stats(ctrl), "notes": notes,
               "curve": [round(x, 3) for x in _cum([r for _, r in sorted(timed)])][-120:],
               "list": sorted(last, key=lambda x: x["out"])[-12:],
               **_verdict(st, _stats(ctrl), _direction(spec))}
        if st.get("n") and st["n"] < 20:
            tr0["notes"] = tr0["notes"] + [f"only {st['n']} trades across the universe: too few to tell an "
                                           f"edge from luck"]
        out["trades"] = tr0
    if fires == 0:
        out["notes"].append("no name in it would have fired on the stored history; check the levels")
    data, sr, tr = keep or (d0, {}, [])
    return out, data, sr, tr


def _status(spec: dict, data: dict, series: dict) -> list[dict]:
    """Each top-level condition's state on the latest bar, Instinct-style:
    met, or how far away."""
    rows = []
    when = spec.get("when") or {}
    conds = when.get("all") or when.get("any") or []
    for c in conds:
        row = {"text": describe(c, spec), "parts": _parts(c, spec)}
        try:
            t = truth(c, data, series)
            row["met"] = bool(t and t[-1] is True)
            if "left" in c and c.get("op") in ("above", "below", "cross_up", "cross_down"):
                n = len(data["t"])
                lv = _resolve(c["left"], data, series, n)[-1]
                rv = _resolve(c["right"], data, series, n)[-1]
                row["now"], row["target"] = lv, rv
                price_like = isinstance(c["left"], str) and _clean_name(c["left"]).split("_")[-1] in FIELDS
                if price_like and lv is not None and rv not in (None, 0) and not row["met"]:
                    row["away_pct"] = round(abs(rv - lv) / abs(rv) * 100, 2)
        except Bad:
            row["met"] = None
        rows.append(row)
    return rows


def _with_parts(rows: list[dict], spec: dict) -> list[dict]:
    """Rows stored before conditions carried their parts get them from the
    rule itself, by position."""
    when = spec.get("when") or {}
    conds = when.get("all") or when.get("any") or []
    return [r if r.get("parts") or i >= len(conds) else {**r, "parts": _parts(conds[i], spec)}
            for i, r in enumerate(rows)]


_POSITION_WORDS = {"entry": "entry price", "bars_in": "bars held", "hi_since": "high since entry",
                   "lo_since": "low since entry", "pnl_pct": "% in favour"}


def _label(x, spec: dict) -> str:
    if isinstance(x, (int, float)):
        return _fmt_num(float(x))
    names = {m["name"]: m.get("title") or m["name"] for m in spec.get("measures") or []}
    if _is_expr(x):
        try:
            words = {k: names.get(k) or _POSITION_WORDS.get(k) or k for k in _expr_names(x)}
        except Bad:
            return str(x)
        return re.sub(r"[A-Za-z_][A-Za-z0-9_]*", lambda m: words.get(m.group(0), m.group(0)),
                      str(x)).replace("*", "×")
    k = _clean_name(x)
    if k in names:
        return names[k]
    if k in _POSITION_WORDS:
        return _POSITION_WORDS[k]
    if k in FIELDS and spec.get("universe") and not spec.get("member"):
        return f"its {k}"
    return f"{spec['symbol']} {k}" if k in FIELDS else str(x)


def _parts(c: dict, spec: dict) -> dict:
    """A condition as pieces the card draws as chips: what is read, the
    comparison, and what it is compared with. Generated from the tree."""
    if "all" in c or "any" in c:
        return {"kind": "group", "join": "and" if "all" in c else "or",
                "items": [_parts(x, spec) for x in (c.get("all") or c.get("any") or [])]}
    if "time" in c:
        return {"kind": "time", "text": describe(c, spec)}
    op = c.get("op")
    out = {"kind": "cmp", "left": _label(c.get("left"), spec), "op": op,
           "left_live": not isinstance(c.get("left"), (int, float)),
           "right_live": not isinstance(c.get("right"), (int, float))}
    if op in ("between", "outside"):
        out["right"] = f"{_label(c.get('right'), spec)} – {_label(c.get('right2'), spec)}"
    elif op in ("rises_pct", "falls_pct"):
        out["right"] = f"{_label(c.get('right'), spec)}% in {int(c.get('bars') or 1)} bar(s)"
    elif op != "is_true":
        out["right"] = _label(c.get("right"), spec)
    if int(c.get("for_bars") or 1) > 1:
        out["for_bars"] = int(c["for_bars"])
    return out


def _fmt_num(v) -> str:
    if v is None:
        return "n/a"
    if abs(v) >= 1000:
        return f"{v:,.2f}".rstrip("0").rstrip(".")
    return f"{v:.4g}"


def _op_word(op: str) -> str:
    return {"above": "above", "below": "below", "cross_up": "crosses above",
            "cross_down": "crosses below", "between": "between", "outside": "outside",
            "rises_pct": "rises by", "falls_pct": "falls by", "is_true": "is true"}.get(op, op)


def describe(c: dict, spec: dict) -> str:
    """A condition in the trader's words, generated from the tree, never
    written by the model, so the card cannot say something the rule does not."""
    if "all" in c or "any" in c:
        j = " and " if "all" in c else " or "
        return j.join(describe(x, spec) for x in (c.get("all") or c.get("any") or []))
    if "time" in c:
        t = c["time"]
        if "at" in t:
            return f"at the {t['at']} bar close"
        if t.get("after") and t.get("before"):
            return f"between {t['after']} and {t['before']}"
        return f"after {t['after']}" if t.get("after") else f"before {t['before']}"
    name = lambda x: _label(x, spec)    # noqa: E731
    op = c.get("op")
    s = f"{name(c.get('left'))} {_op_word(op)}"
    if op in ("between", "outside"):
        s += f" {name(c.get('right'))} and {name(c.get('right2'))}"
    elif op in ("rises_pct", "falls_pct"):
        s += f" {name(c.get('right'))}% in {int(c.get('bars') or 1)} bar(s)"
    elif op != "is_true":
        s += f" {name(c.get('right'))}"
    if int(c.get("for_bars") or 1) > 1:
        s += f" for {int(c['for_bars'])} bars"
    return s


def _contract_line(spec: dict) -> dict:
    c, m = spec["contract"], spec["mandate"]
    runs = {"now": "Once, now", "once_when": "Once, when it triggers",
            "standing": "Every time it triggers", "policy": "Keeps it maintained"}[c["lifespan"]]
    acts = {"notify": "Tells you", "propose": "Proposes, you confirm",
            "act": "Places it on paper, then tells you",
            "manage": "Places and manages it on paper"}[c["authority"]]
    if spec["do"]["kind"] == "halt":
        acts = "Halts on its own and tells you; you can lift it"
    out = {"runs": runs, "acts": acts}
    if spec["do"]["kind"] == "halt":
        pass
    elif spec["do"]["kind"] == "rebalance":
        new = (spec["do"].get("size") or {}).get("inr")
        out["limit"] = f"at most {m.get('max_weight', policy.DEFAULT_MAX_WEIGHT) * 100:.0f}% in one name" + \
            (f", ₹{new:,.0f} of new money" if new else "")
    elif spec["do"]["kind"] != "notify":
        out["limit"] = f"₹{m['capital_inr']:,.0f}"
    return out


def _paper_qty(symbol: str, inr: float, price: float) -> float:
    inst_crypto = symbol.endswith(("USDT", "USD", "BTC"))
    if price <= 0:
        return 0
    q = Decimal(str(inr)) / Decimal(str(price))
    if inst_crypto:
        return float(q.quantize(Decimal("0.000001"), rounding=ROUND_DOWN))
    return float(q.to_integral_value(rounding=ROUND_DOWN))


def order_words(spec: dict) -> str:
    w = _order_words(spec)
    return w.replace("*", "the name that triggered") if spec.get("universe") and not spec.get("member") else w


def _order_words(spec: dict) -> str:
    """The order in a trader's words, from the tree: "sell NIFTY ATM CE and
    ATM PE, 1 lot each, weekly expiry"."""
    do = spec["do"]
    if do["kind"] == "halt":
        return ("stops new intraday, F&O and perp entries for the rest of the day"
                + (" and closes what is open" if do.get("close") else ""))
    if do["kind"] == "options":
        legs = do.get("legs") or []
        parts = [f"{lg['side']} {lg.get('lots', 1)} lot{'s' if lg.get('lots', 1) != 1 else ''} "
                 f"{lg['strike']} {lg['opt']}" for lg in legs]
        exp = (legs[0].get("expiry") if legs else None) or "the nearest"
        return f"{do['underlying']} options: " + ", ".join(parts) + f" ({exp} expiry)"
    if do["kind"] == "future":
        return f"{do.get('side') or 'buy'} {do.get('lots') or 1} lot of the {do['underlying']} future"
    if do["kind"] == "rebalance":
        book = "the paper portfolio" if do.get("of") == "portfolio" else "its holdings"
        if spec["contract"]["discretion"] == "plan":
            return f"rebalances {book} to targets the planner sets for “{do['objective']}”"
        t = do.get("targets") or {}
        top = ", ".join(f"{k} {v * 100:.0f}%" for k, v in sorted(t.items(), key=lambda x: -x[1])[:4])
        return f"rebalances {book} to {top}{' and more' if len(t) > 4 else ''}"
    if do.get("product") == "PERP":
        size = do.get("size") or {}
        if do["kind"] in ("sell", "cover"):
            return f"a close of this play's {do['symbol']} position"
        amt = (f"₹{size['inr']:,.0f} of margin" if size.get("inr") else f"{size.get('qty', 0):g} contracts")
        side = "long" if do["kind"] == "buy" else "short"
        return f"a {do.get('leverage', 1):g}× {side} in {do['symbol']} ({amt}, Hyperliquid, on paper)"
    if do["kind"] == "short":
        size = do.get("size") or {}
        amt = f"₹{size['inr']:,.0f} of " if size.get("inr") else f"{size.get('qty', 0):g} "
        return f"a short of {amt}{do['symbol']} (intraday)" if do.get("product") == "MIS" else \
            f"a short of {amt}{do['symbol']}"
    if do["kind"] == "cover":
        return f"a cover of this play's {do['symbol']} short"
    return do["kind"]


def summary(spec: dict) -> str:
    """One sentence for the whole play, generated from the tree (Catalyst
    shows the same line beside its graph): what it checks, how often, what it
    does and how many times."""
    iv = spec["interval"]
    every = "each daily close" if iv == "1d" else f"every {iv} bar close"
    c, do = spec["contract"], spec["do"]
    when = spec.get("when")
    cond = describe(when, spec) if when else ""
    if do["kind"] == "rebalance":
        words = order_words(spec)
        if c["authority"] == "propose":
            words = "proposes that it " + words.replace("rebalances", "rebalance", 1)
        if c["lifespan"] == "now":
            return f"{words[0].upper()}{words[1:]}, now, on paper."
        pol = spec.get("policy") or {}
        return (f"{words[0].upper()}{words[1:]}: every {pol.get('every', 'week')}, and whenever a "
                f"holding drifts {pol.get('drift_pct', 5):g} points from its target. On paper.")
    if do["kind"] == "halt":
        w = order_words(spec)
        return f"{w[0].upper()}{w[1:]} when {cond}. Checked after every fill and exit, and every {iv}."
    if do["kind"] == "notify":
        act = "tells you"
    elif do["kind"] in ("options", "future", "short", "cover") or do.get("product") == "PERP":
        verb = "proposes" if c["authority"] == "propose" else "places"
        act = f"{verb} {order_words(spec)}"
    else:
        size = do.get("size") or {}
        amt = (f"₹{size['inr']:,.0f} of " if size.get("inr") else
               f"{size['qty']:g} " if size.get("qty") else "all of its ")
        verb = {"propose": "proposes buying", "act": "buys", "manage": "buys"}.get(
            c["authority"], "proposes buying") if do["kind"] == "buy" else \
            {"propose": "proposes selling", "act": "sells", "manage": "sells"}.get(
                c["authority"], "proposes selling")
        act = f"{verb} {amt}{do['symbol']}"
    if c["lifespan"] == "now":
        return f"{act[0].upper()}{act[1:]} now, on paper." if do["kind"] != "notify" else "Tells you now."
    times = "the first time" if c["lifespan"] == "once_when" else "each time"
    on = spec.get("on")
    if on:
        return f"Reads {_on_words(on)} and {act} {times} one {_on_test(on)}" + \
            (f", while {cond}" if cond else "") + "."
    if spec.get("universe") and not spec.get("member"):
        u = spec["universe"]
        act = act.replace("*", "that name")
        return (f"Checks each of {len(u['symbols'])} names ({u['words']}) at {every} and {act} "
                f"{'the first time' if c['lifespan'] == 'once_when' else 'each time'} {cond}.")
    return f"Checks {spec['symbol']} at {every} and {act} {times} {cond}."


def _on_words(on: dict) -> str:
    parts = []
    if "filings" in on["sources"]:
        parts.append("NSE filings" + (f" from {', '.join(on['symbols'])}" if on["symbols"] else ""))
    if "news" in on["sources"]:
        parts.append("market news headlines")
    return " and ".join(parts)


def _on_test(on: dict) -> str:
    bits = []
    if on["match"]:
        bits.append("mentions " + " or ".join(f"“{w}”" for w in on["match"][:4]))
    if on["judge"]:
        bits.append(f"answers yes to “{on['judge']}”")
    return " and ".join(bits) or "arrives"


def card(row: dict, live: dict | None = None) -> dict:
    """The play as the reply's panel and the widget's row. Built from the
    stored row only, so a card can never show a play that was not saved."""
    spec = row["spec"]
    hist = row.get("history") or {}
    do = spec["do"]
    action = None
    if do["kind"] in ("options", "future", "short", "cover", "rebalance", "halt") or do.get("product") == "PERP":
        action = {"side": do["kind"], "symbol": do.get("symbol") or do.get("underlying"),
                  "text": order_words(spec)[0].upper() + order_words(spec)[1:]}
    elif do["kind"] != "notify":
        size = do.get("size") or {}
        action = {"side": do["kind"], "symbol": do["symbol"],
                  "size": (f"₹{size['inr']:,.0f}" if size.get("inr") else
                           f"{size['qty']:g} qty" if size.get("qty") else "what it holds")}
    if action and action.get("symbol") == "*":
        action["symbol"] = None
    exits = []
    mg = spec.get("manage") or {}
    if mg.get("stop_pct"):
        exits.append(f"stop {mg['stop_pct']:g}% against entry" if do["kind"] in ("short", "options")
                     else f"stop {mg['stop_pct']:g}% below entry")
    if mg.get("target_pct"):
        exits.append(f"target {mg['target_pct']:g}% in favour" if do["kind"] in ("short", "options")
                     else f"target {mg['target_pct']:g}% above entry")
    if mg.get("exit_at"):
        exits.append(f"exit at {mg['exit_at']}")
    if mg.get("trail_pct"):
        exits.append(f"trail {mg['trail_pct']:g}%")
    if mg.get("on_leg_stop"):
        exits.append("one leg stopped: " + ("exit all" if mg["on_leg_stop"] == "exit_all"
                                            else "move the others to cost"))
    if mg.get("max_loss_inr"):
        exits.append(f"exit all at ₹{mg['max_loss_inr']:,.0f} loss")
    if mg.get("target_inr"):
        exits.append(f"exit all at ₹{mg['target_inr']:,.0f} profit")
    if mg.get("exit_when"):
        exits.insert(0, "exits when " + describe(mg["exit_when"], spec))
    if do.get("product") == "MIS":
        exits.append("squared off at 15:20" if not do.get("symbol", "").endswith(("USDT", "USD")) else "")
    exits = [e for e in exits if e]
    when = spec.get("when") or {}
    perp_sym = next((s for s in (do.get("symbol"), spec["symbol"]) if hyperliquid.is_perp(s or "")), None)
    perp = None
    if perp_sym:
        c = hyperliquid.ctx(perp_sym) or {}
        perp = {"symbol": perp_sym, "coin": hyperliquid.coin(perp_sym), "icon": hyperliquid.icon(perp_sym),
                "mark": c.get("mark"), "funding": c.get("funding"), "oi": c.get("oi"),
                "day_volume_usd": c.get("day_volume_usd"), "max_leverage": c.get("max_leverage"),
                "leverage": do.get("leverage") if do.get("product") == "PERP" else None}
    return {
        "kind": "play", "id": row["id"], "title": spec["title"], "state": row["state"],
        "why": row.get("why") or "", "symbol": spec["symbol"], "interval": spec["interval"],
        "join": "any" if "any" in when else "all", "summary": summary(spec),
        "on": ({"reads": _on_words(spec["on"]), "test": _on_test(spec["on"]),
                "match": spec["on"].get("match") or [], "judge": spec["on"].get("judge") or "",
                "recent": hist.get("events") or []} if spec.get("on") else None),
        # a universe's rule is read on each name: no one name's values stand for it
        "conditions": _with_parts([{"text": describe(c, spec)} for c in (when.get("all") or when.get("any") or [])]
                                  if spec.get("universe") else
                                  (live or {}).get("conditions") or hist.get("conditions") or
                                  [{"text": describe(c, spec)} for c in (when.get("all") or when.get("any") or [])],
                                  spec),
        "measures": [{"name": m["name"], "title": m["title"], "formula": m["formula"],
                      "now": (hist.get("now") or {}).get(m["name"]),
                      "spark": (hist.get("spark") or {}).get(m["name"])}
                     for m in spec.get("measures") or [] if not m["name"].endswith(tuple(f"_{f}" for f in FIELDS))
                     or m.get("code")],
        "universe": ({"words": spec["universe"]["words"], "symbols": spec["universe"]["symbols"],
                      "met_now": hist.get("met_now") or [], "members": (hist.get("members") or [])[:12],
                      "max_open": (spec.get("mandate") or {}).get("max_open"),
                      "holding": sorted(k for k, v in (row.get("held_legs") or {}).items() if v)}
                     if spec.get("universe") else None),
        "action": action, "exits": exits, "contract": _contract_line(spec),
        "history": {k: hist.get(k) for k in ("fires", "per_month", "last_fire", "bars", "from", "to",
                                             "fire_ts", "price", "trades")},
        "exit_rule": ([_parts(c, spec) for c in (mg["exit_when"].get("all") or mg["exit_when"].get("any") or [])]
                      if mg.get("exit_when") else None),
        "exit_join": "any" if "any" in (mg.get("exit_when") or {}) else "all",
        "do": do, "manage": mg, "mandate": spec.get("mandate"), "policy": spec.get("policy"), "perp": perp,
        "lifespan": spec["contract"]["lifespan"], "authority": spec["contract"]["authority"],
        "discretion": spec["contract"]["discretion"], "created": row.get("created"),
        "held": row.get("held_legs") or ({row["held_sym"]: row["held_qty"]}
                                         if row.get("held_qty") and row.get("held_sym") else {}),
        "spent_inr": row.get("spent_inr") or 0,
        "notes": hist.get("notes") or [], "fires": row.get("fires", 0),
        "expires": row.get("expires"),
        "thesis": spec.get("thesis") or "",
        "targets": ([{"symbol": k, "weight": v, "why": (do.get("reasons") or {}).get(k)}
                     for k, v in sorted((do.get("targets") or {}).items(),
                                                                    key=lambda x: -x[1])]
                    if do["kind"] == "rebalance" else None),
        # the one-tap upgrade: a rebalance done once, kept that way
        "offer": ({"action": "policy", "label": "Keep it this way: check weekly and on a 5-point drift"}
                  if do["kind"] == "rebalance" and spec["contract"]["lifespan"] == "now" else None),
    }


# ══ persistence ═════════════════════════════════════════════════════════════

_COLS = ("id", "user_id", "title", "ask", "spec", "symbol", "interval", "symbols", "state", "why",
         "created", "updated", "expires", "fires", "accepted", "spent_inr", "held_qty",
         "held_sym", "held_legs", "last_eval_ts", "needs_confirm", "history", "entry_bar", "marks")


def _row(t: tuple) -> dict:
    r = dict(zip(_COLS, t))
    r["spec"] = json.loads(r["spec"])
    r["history"] = json.loads(r["history"] or "{}")
    r["held_legs"] = json.loads(r["held_legs"] or "{}")
    r["marks"] = json.loads(r.get("marks") or "{}")
    return r


def get(uid: int, pid: int) -> dict | None:
    with ds._users_lock:
        t = _db().execute(f"SELECT {', '.join(_COLS)} FROM plays WHERE id=? AND user_id=?",
                          (pid, uid)).fetchone()
    return _row(t) if t else None


def list_plays(uid: int, states=("armed", "paused", "done", "draft")) -> list[dict]:
    with ds._users_lock:
        rows = _db().execute(
            f"SELECT {', '.join(_COLS)} FROM plays WHERE user_id=? AND state IN "
            f"({','.join('?' * len(states))}) ORDER BY updated DESC LIMIT 200",
            (uid, *states)).fetchall()
    return [_row(t) for t in rows]


def _symbols_of(spec: dict) -> list[str]:
    out = {spec["symbol"], *((spec.get("universe") or {}).get("symbols") or [])}
    for m in spec.get("measures") or []:
        for src in m["inputs"].values():
            if "bars" in src:
                out.add(src["bars"]["symbol"])
    if spec["do"].get("symbol") and spec["do"]["symbol"] != "*":
        out.add(spec["do"]["symbol"])
    return sorted(out)


def _save(uid: int, spec: dict, hist: dict, *, state: str, pid: int | None = None,
          last_eval_ts: int = 0, needs_confirm: bool = False) -> int:
    now = int(time.time())
    days = spec["contract"].get("expires_in_days")
    expires = now + int(days) * 86400 if days else None
    syms = ",".join(_symbols_of(spec))
    with ds._users_lock:
        if pid:
            _db().execute(
                "UPDATE plays SET title=?, ask=?, spec=?, symbol=?, interval=?, symbols=?, state=?, "
                "why='', updated=?, expires=?, last_eval_ts=?, needs_confirm=?, history=? "
                "WHERE id=? AND user_id=?",
                (spec["title"], spec.get("ask", ""), json.dumps(spec), spec["symbol"],
                 spec["interval"], syms, state, now, expires, last_eval_ts, int(needs_confirm),
                 json.dumps(hist, default=str), pid, uid))
        else:
            cur = _db().execute(
                "INSERT INTO plays (user_id, title, ask, spec, symbol, interval, symbols, state, "
                "created, updated, expires, last_eval_ts, history) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (uid, spec["title"], spec.get("ask", ""), json.dumps(spec), spec["symbol"],
                 spec["interval"], syms, state, now, now, expires, last_eval_ts,
                 json.dumps(hist, default=str)))
            pid = cur.lastrowid
        _db().commit()
    _load_index()
    return pid


def _set(pid: int, **cols) -> None:
    if not cols:
        return
    with ds._users_lock:
        _db().execute(f"UPDATE plays SET {', '.join(f'{k}=?' for k in cols)}, updated=? WHERE id=?",
                      (*cols.values(), int(time.time()), pid))
        _db().commit()


def _event(play: dict, ts: int, kind: str, title: str, body: dict, status: str = "open") -> dict:
    now = int(time.time())
    with ds._users_lock:
        cur = _db().execute(
            "INSERT INTO play_events (play_id, user_id, ts, created, kind, title, body, status) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (play["id"], play["user_id"], ts, now, kind, title, json.dumps(body, default=str), status))
        _db().commit()
        eid = cur.lastrowid
    ev = {"id": eid, "play_id": play["id"], "play": play["spec"]["title"], "ts": ts,
          "created": now, "kind": kind, "title": title, "body": body, "status": status}
    if _alerts is not None:
        try:
            _alerts.push(play["user_id"], {"type": "play", "event": ev})
        except Exception:                                   # noqa: BLE001
            log.warning("plays: push failed", exc_info=True)
    return ev


def events(uid: int, limit: int = 60) -> list[dict]:
    with ds._users_lock:
        rows = _db().execute(
            "SELECT e.id, e.play_id, p.title, e.ts, e.created, e.kind, e.title, e.body, e.status, "
            "e.seen FROM play_events e LEFT JOIN plays p ON p.id = e.play_id WHERE e.user_id=? "
            "ORDER BY e.created DESC, e.id DESC LIMIT ?", (uid, limit)).fetchall()
    out = [{"id": r[0], "play_id": r[1], "play": r[2] or "", "ts": r[3], "created": r[4],
            "kind": r[5], "title": r[6], "body": json.loads(r[7] or "{}"), "status": r[8],
            "seen": bool(r[9])} for r in rows]
    specs: dict[int, dict] = {}
    for e in out:
        if e["play_id"] not in specs:
            p = _load(e["play_id"]) if e["play_id"] else None
            specs[e["play_id"]] = p["spec"] if p else {}
        e["alert"] = transcribe(e, specs[e["play_id"]])
    return out


_PAST = (("crosses above", "crossed above"), ("crosses below", "crossed below"),
         ("rises", "rose"), ("falls", "fell"), ("is above", "is above"), ("is below", "is below"))


def _past(text: str) -> str:
    for a, b in _PAST:
        text = text.replace(a, b)
    return text


def transcribe(ev: dict, spec: dict) -> dict:
    """An event as one alert: a label ("Stock alert"), one sentence in parts
    ({t: text} and {b: the value that decided it}), the source when an item
    clocked it, and the symbol whose mark it carries. Built from what the
    event recorded, so the sentence can only say what was seen or done."""
    b, kind = ev.get("body") or {}, ev["kind"]
    sym = (b.get("symbol") or spec.get("symbol") or "")
    src = b.get("event") or None
    t = b.get("ticket") or {}
    if kind == "fired":
        label = ("Filing alert" if src and src.get("source") == "filings" else
                 "News alert" if src else
                 "Basket alert" if sym.startswith("@") else
                 "Perp alert" if sym.endswith(hyperliquid.SUFFIX) else
                 "Crypto alert" if sym.endswith(("USDT", "-USD")) else
                 "Index alert" if sym.startswith(("NIFTY", "SENSEX", "BANKNIFTY")) else "Stock alert")
    else:
        label = {"proposed": "Waiting for your confirm", "filled": "Placed on paper",
                 "rejected": "Not placed", "exited": "Position closed",
                 "note": "Policy check"}.get(kind, "Update")
    line: list[dict] = []

    def say(*bits):
        for x in bits:
            if isinstance(x, tuple):
                line.append({"b": x[0]})
            elif x:
                line.append({"t": x})
    seen = b.get("seen") or []
    if t.get("rebalance"):
        buys = sum(1 for lg in t.get("legs") or [] if lg["side"] == "BUY")
        sells = len(t.get("legs") or []) - buys
        say(f"{'Rebalanced' if kind == 'filled' else 'Rebalance'}: {buys} buy{'s' if buys != 1 else ''} and "
            f"{sells} sell{'s' if sells != 1 else ''}, ", (f"₹{t.get('buys_inr', 0):,.0f}",), " in and ",
            (f"₹{t.get('sells_inr', 0):,.0f}",), " out")
    elif t.get("basket"):
        say(f"{'Bought' if t.get('side') == 'BUY' else 'Sold'} {t['basket']} in {len(t.get('legs') or [])} "
            "trades for ", (f"₹{t.get('inr') or 0:,.0f}",))
        sym = t["basket"]
    elif t.get("book") == "day" and t.get("legs"):
        lg = t["legs"][0]
        more = f" and {len(t['legs']) - 1} more" if len(t["legs"]) > 1 else ""
        verb = ("Closed" if t.get("close") else "Opened") if kind == "filled" else "Open"
        say(f"{verb} {lg['side']} {_fmt_num(float(lg['qty']))} {lg['symbol']}{more} at ",
            (_fmt_num(float(lg.get("px") or 0)),))
        if lg.get("kind") == "perp" and lg.get("leverage"):
            say(f" USD, {float(lg['leverage']):g}× leverage")
    elif t.get("symbol"):
        verb = {"filled": "Bought" if t["side"] == "BUY" else "Sold",
                "proposed": "Buy" if t["side"] == "BUY" else "Sell"}.get(kind, "Order:")
        px = b.get("fill_price") or t.get("price")
        say(f"{verb} {_fmt_num(float(t['qty']))} {t['symbol']} at {'about ' if kind == 'proposed' else ''}",
            (_fmt_num(float(px or 0)),))
        sym = t["symbol"]
    elif seen and not src:
        r = seen[0]
        say(_past(r.get("text") or ""), ", now " if r.get("left") is not None else "",
            (_fmt_num(float(r["left"])),) if r.get("left") is not None else "")
    elif b.get("reason") and not src:
        say(str(b["reason"]))
    if kind in ("rejected", "note") and b.get("reason") and line and not line[-1].get("t", "").endswith(b["reason"]):
        line = [{"t": str(b["reason"])}]
    return {"label": label, "line": line, "symbol": sym,
            "source": ({"who": src.get("who"), "title": src.get("title"), "url": src.get("url"),
                        "quote": src.get("quote"), "kind": src.get("source")} if src else None),
            "late": bool(b.get("late"))}


# ── measures library ────────────────────────────────────────────────────────

def list_measures(uid: int) -> list[dict]:
    with ds._users_lock:
        rows = _db().execute("SELECT spec FROM measures WHERE user_id=? ORDER BY updated DESC",
                             (uid,)).fetchall()
    return [json.loads(r[0]) for r in rows]


def save_measure(uid: int, m: dict) -> None:
    now = int(time.time())
    with ds._users_lock:
        _db().execute(
            "INSERT INTO measures (user_id, name, spec, created, updated) VALUES (?,?,?,?,?) "
            "ON CONFLICT(user_id, name) DO UPDATE SET spec=excluded.spec, updated=excluded.updated",
            (uid, m["name"], json.dumps(m), now, now))
        _db().commit()


# ══ the runtime ═════════════════════════════════════════════════════════════

_INDEX: dict[str, list[int]] = {}     # clock symbol → armed play ids
_INDEX_LOCK = threading.Lock()
_Q: queue.Queue = queue.Queue(maxsize=2048)
_STOP = threading.Event()
_WORKER: threading.Thread | None = None
STATS = {"evals": 0, "fires": 0, "errors": 0, "dropped": 0}


def _load_index() -> None:
    with ds._users_lock:
        rows = _db().execute("SELECT id, symbol, spec FROM plays WHERE state='armed'").fetchall()
    idx: dict[str, list[int]] = {}
    for pid, sym, sp in rows:
        uni = (json.loads(sp).get("universe") or {}).get("symbols") if '"universe"' in sp else None
        for s in uni or [sym]:
            if customsec.is_custom(s):
                s = (customsec.members(s) or [s])[0]
            idx.setdefault(s, []).append(pid)
    with _INDEX_LOCK:
        _INDEX.clear()
        _INDEX.update(idx)


def on_bar(symbol: str, form: list, closed: bool) -> None:
    """The tick hook. One bounded put; never blocks, never raises."""
    if not closed:
        return
    with _INDEX_LOCK:
        if symbol not in _INDEX:
            return
    try:
        _Q.put_nowait((symbol, int(form[0])))
    except queue.Full:
        STATS["dropped"] += 1


def _due(interval: str, minute_ts: int, utc: bool = False) -> bool:
    """Has this play's bar just closed? Intraday: the closing minute ends on
    the interval's boundary. Daily: checked every 15 minutes, and the closed-
    bar watermark decides."""
    if interval == "1d":
        return (minute_ts // 60) % 15 == 0
    step = INTERVALS[interval] * 60
    if utc:
        return (minute_ts + 60) % step == 0
    return ((minute_ts + 60 + ds.IST_OFF - 15 * 60) % step == 0) or step == 60


def watched() -> set[str]:
    """The perps armed plays are clocked on (the Hyperliquid poller reports
    their minutes; every other symbol has its own stream)."""
    with _INDEX_LOCK:
        return {s for s in _INDEX if hyperliquid.is_perp(s)}


def _worker() -> None:
    while not _STOP.is_set():
        try:
            sym, ts = _Q.get(timeout=1.0)
        except queue.Empty:
            continue
        with _INDEX_LOCK:
            pids = list(_INDEX.get(sym) or [])
        for pid in pids:
            try:
                _tick(pid, ts, sym=sym)
            except Exception:                               # noqa: BLE001
                STATS["errors"] += 1
                log.warning("plays: play %s failed", pid, exc_info=True)


def _load(pid: int) -> dict | None:
    with ds._users_lock:
        t = _db().execute(f"SELECT {', '.join(_COLS)} FROM plays WHERE id=?", (pid,)).fetchone()
    return _row(t) if t else None


def _tick(pid: int, minute_ts: int | None = None, force: bool = False, sym: str = "") -> list[dict]:
    play = _load(pid)
    if not play or play["state"] != "armed":
        return []
    spec = play["spec"]
    if spec.get("universe"):
        if not sym:
            return [e for s in spec["universe"]["symbols"] for e in _tick(pid, minute_ts, force, s)]
        return _tick_member(play, sym, minute_ts, force)
    if minute_ts is not None and not force and not _due(spec["interval"], minute_ts,
                                                         hyperliquid.is_perp(spec["symbol"])):
        return []
    if play["expires"] and play["expires"] <= time.time():
        _set(pid, state="done", why="expired")
        _load_index()
        return []
    data, _ = frame(spec)
    series, _ = run_measures(spec, data)
    STATS["evals"] += 1
    t = data["t"]
    new = [i for i in range(len(t)) if t[i] > play["last_eval_ts"]]
    if not new:
        return []
    tr = truth(spec["when"], data, series) if spec.get("when") else []
    if spec.get("on"):
        tr = []                       # its entries are clocked by events (on_items)
    out = []
    if spec["contract"]["lifespan"] == "policy" and spec["do"]["kind"] == "rebalance":
        # one run on the latest closed bar at most; bars missed while offline are not replayed
        why = policy.due(play, t[-1])
        if why:
            ev = _fire(play, data, series, len(t) - 1, late=False)
            if ev:
                ev["body"]["due"] = why
            out.append(ev)
        _set(pid, last_eval_ts=t[-1])
        return out
    for i in new:
        late = i != len(t) - 1
        if play["why"] == EXITS_ONLY and not _holding(play):
            _entries_over(pid, "fired")
            break
        if (tr and i > 0 and tr[i] is True and tr[i - 1] is not True
                and play["why"] not in (EXITS_ONLY, "fired")):
            out.append(_fire(play, data, series, i, late=late))
            play = _load(pid)
            if play["state"] != "armed":
                break
        if play["held_qty"] and not late:
            ex = _maybe_time_exit(play, data, i)
            if ex:
                out.append(ex)
                play = _load(pid)
        if not late and (spec.get("manage") or {}).get("exit_when") and _holding(play):
            ex = _maybe_rule_exit(play, data, series, i)
            if ex:
                out.append(ex)
                play = _load(pid)
    _set(pid, last_eval_ts=t[-1])
    return out


def _holding(play: dict) -> bool:
    member = play["spec"].get("member")
    if _day_route(play["spec"]["do"]):
        import daybook
        opens = daybook.open_positions(play["user_id"], play["id"])
        return bool([p for p in opens if not member or member in (p["symbol"], p["underlying"])])
    if member:
        return bool((play.get("held_legs") or {}).get(member))
    return bool(play["held_qty"] or any((play.get("held_legs") or {}).values()))


# ── a play over a universe ───────────────────────────────────────────────────
# Each symbol in play.universe is its own clock: the same measures and rule
# read on its bars, entries in it sized and placed for it, its own exit. The
# play holds at most mandate.max_open names at once; once_when means once
# per name.

def _member_spec(spec: dict, sym: str) -> dict:
    do = dict(spec["do"])
    if do.get("symbol") == "*":
        do["symbol"] = sym
    if do.get("underlying") == "*":
        do["underlying"] = sym
    return {**spec, "symbol": sym, "do": do, "member": sym}


def _member(play: dict, sym: str) -> dict:
    mk = (play.get("marks") or {}).get(sym) or {}
    held = (play.get("held_legs") or {}).get(sym, 0.0)
    return {**play, "spec": _member_spec(play["spec"], sym), "held_qty": held,
            "held_sym": sym if held else "", "entry_bar": mk.get("entry", 0)}


def _mark_member(pid: int, sym: str, **kv) -> None:
    with ds._users_lock:
        r = _db().execute("SELECT marks FROM plays WHERE id=?", (pid,)).fetchone()
        marks = json.loads((r or ["{}"])[0] or "{}")
        marks[sym] = {**(marks.get(sym) or {}), **kv}
        _db().execute("UPDATE plays SET marks=? WHERE id=?", (json.dumps(marks), pid))
        _db().commit()


def _open_names(play: dict) -> set[str]:
    if _day_route(play["spec"]["do"]):
        import daybook
        return {p["underlying"] or p["symbol"] for p in daybook.open_positions(play["user_id"], play["id"])}
    return {k for k, v in (play.get("held_legs") or {}).items() if v}


def _tick_member(play: dict, sym: str, minute_ts: int | None, force: bool) -> list[dict]:
    spec = play["spec"]
    if minute_ts is not None and not force and not _due(spec["interval"], minute_ts,
                                                         hyperliquid.is_perp(sym)):
        return []
    if play["expires"] and play["expires"] <= time.time():
        _set(play["id"], state="done", why="expired")
        _load_index()
        return []
    mp = _member(play, sym)
    try:
        data, _ = frame(mp["spec"], ensure=False)
        series, _ = run_measures(mp["spec"], data)
    except Bad:
        return []
    STATS["evals"] += 1
    t = data["t"]
    mk = (play.get("marks") or {}).get(sym) or {}
    since = mk.get("eval") or play["last_eval_ts"]
    new = [i for i in range(len(t)) if t[i] > since]
    if not new:
        return []
    tr = truth(spec["when"], data, series) if spec.get("when") else []
    out = []
    for i in new:
        late = i != len(t) - 1
        fresh = tr and i > 0 and tr[i] is True and tr[i - 1] is not True
        if fresh and not mk.get("done") and play["why"] not in (EXITS_ONLY, "fired") and not _holding(mp):
            cap = int(spec["mandate"].get("max_open") or 0)
            busy = _open_names(play)
            if spec["do"]["kind"] != "notify" and cap and len(busy) >= cap:
                out.append(_event(mp, t[i], "rejected", spec["title"],
                                  {"symbol": sym, "price": data["close"][i], "seen": _seen(mp["spec"], data, series, i),
                                   "reason": f"it already holds {len(busy)} of its names ({', '.join(sorted(busy))}), "
                                             f"its limit of {cap} at once"}, status="done"))
            else:
                out.append(_fire(mp, data, series, i, late=late))
            play = _load(play["id"])
            if play["state"] != "armed":
                break
            mp = _member(play, sym)
            mk = (play.get("marks") or {}).get(sym) or {}
        if not late and (spec.get("manage") or {}).get("exit_when") and _holding(mp):
            ex = _maybe_rule_exit(mp, data, series, i)
            if ex:
                out.append(ex)
                play = _load(play["id"])
                mp = _member(play, sym)
    _mark_member(play["id"], sym, eval=t[-1])
    return out


def _maybe_rule_exit(play: dict, data: dict, series: dict, i: int) -> dict | None:
    """manage.exit_when, read on the bar that just closed with the position's
    own names filled in from the bar it was entered on."""
    spec = play["spec"]
    ew = spec["manage"]["exit_when"]
    t = data["t"]
    i0 = next((k for k in range(len(t)) if t[k] >= (play.get("entry_bar") or 0)), 0)
    if i <= i0:
        return None
    tr = truth(ew, data, {**series, **_position_series(data, i0, _direction(spec))})
    if tr[i] is not True:
        return None
    why = "exit rule: " + describe(ew, spec)
    body = {"reason": why, "seen": _seen({"when": ew, "measures": spec.get("measures"),
                                          "symbol": spec["symbol"]}, data,
                                         {**series, **_position_series(data, i0, _direction(spec))}, i),
            "symbol": spec["symbol"], "price": data["close"][i]}
    if _day_route(spec["do"]):
        import daybook
        if spec.get("member"):
            res = [daybook.close(p, p["mark_px"] or p["entry_px"], why)
                   for p in daybook.open_positions(play["user_id"], play["id"])
                   if spec["member"] in (p["symbol"], p["underlying"])]
        else:
            res = daybook.close_play(play["id"], why)    # an exit never waits for a tap
        return _event(play, t[i], "exited", spec["title"], {**body, "closed": res}, status="done")
    if customsec.is_custom(play["held_sym"]):
        legs = {s: q for s, q in _held_legs_now(play).items() if q}
        ticket = {"basket": play["held_sym"], "side": "SELL",
                  "legs": [{"symbol": s, "side": "SELL", "qty": q, "price": _mark(s)} for s, q in legs.items()]}
    else:
        held = _held_now(play)
        if not held:
            _set(play["id"], held_qty=0)
            return None
        ticket = {"symbol": play["held_sym"], "side": "SELL", "qty": held, "price": data["close"][i]}
    if spec["contract"]["authority"] == "manage":
        return _place(play, ticket, t[i], body)
    return _event(play, t[i], "proposed", spec["title"], {**body, "ticket": ticket})


def _seen(spec: dict, data: dict, series: dict, i: int) -> list[dict]:
    """What the rule saw on the bar it fired on: each condition with the
    values on both sides. The receipt."""
    n = len(data["t"])
    rows = []
    when = spec.get("when") or {}
    for c in when.get("all") or when.get("any") or []:
        row = {"text": describe(c, spec)}
        try:
            if "left" in c:
                row["left"] = _resolve(c["left"], data, series, n)[i]
                if "right" in c:
                    row["right"] = _resolve(c["right"], data, series, n)[i]
        except Bad:
            pass
        rows.append(row)
    return rows


def _fire(play: dict, data: dict, series: dict, i: int, *, late: bool,
          event: dict | None = None) -> dict:
    spec, do, c = play["spec"], play["spec"]["do"], play["spec"]["contract"]
    ts, price = data["t"][i], data["close"][i]
    seen = _seen(spec, data, series, i)
    if event:
        ts = event["ts"]
    STATS["fires"] += 1
    fires = play["fires"] + 1
    _set(play["id"], fires=fires)
    body = {"seen": seen, "price": price, "symbol": spec["symbol"], "interval": spec["interval"],
            "late": late}
    if event:
        body["event"] = event
    title = spec["title"]
    authority = c["authority"]
    reason = ""
    if late:
        authority, reason = "notify", "it fired while Pivot was offline, so nothing was placed"
    elif (authority in ("act", "manage") and play["accepted"] == 0 and play["fires"] == 0
          and c["lifespan"] != "now"):
        # a play told to act NOW was confirmed by the trader in the same breath;
        # the first-fire check is for fires nobody is watching
        authority, reason = "propose", "first fire: confirm it once and it acts on its own after"
    elif authority in ("act", "manage") and play["needs_confirm"]:
        authority, reason = "propose", "the play was edited, so this one needs your confirm"
    elif (authority in ("act", "manage") and event and event.get("source") == "news"
          and not spec.get("when")):
        authority, reason = "propose", ("a headline alone never places an order; a filing or a "
                                        "price condition has to back it")
    ev = None
    halt = _halted(play["user_id"]) if _halts_entries(do) else None
    if do["kind"] == "halt":
        ev = _halt(play, ts, body)
    elif halt and authority != "notify":
        ev = _event(play, ts, "rejected", title, {**body, "reason": halt["text"]}, status="done")
    elif do["kind"] == "notify" or authority == "notify":
        ev = _event(play, ts, "fired", title, {**body, "reason": reason}, status="done")
    else:
        ticket, why = _ticket(play, price)
        if ticket and ticket.get("structural") and authority in ("act", "manage"):
            authority, reason = "propose", "the planner changed which names it holds, so you confirm"
        if ticket and ticket.get("rebalance") and not ticket["legs"]:
            ev = _event(play, ts, "note", title, {**body, "ticket": ticket, "reason":
                        "every holding is within its band of the target, so nothing was traded"},
                        status="done")
        elif not ticket:
            ev = _event(play, ts, "rejected", title, {**body, "reason": why}, status="done")
        elif authority == "propose":
            ev = _event(play, ts, "proposed", title, {**body, "ticket": ticket, "reason": reason})
        else:
            ev = _place(play, ticket, ts, body)
    if spec.get("member"):
        if c["lifespan"] in ("once_when", "now"):
            _mark_member(play["id"], spec["member"], done=True)
        if fires >= spec["mandate"]["max_fires"]:
            _entries_over(play["id"], "reached its fire limit")
        return ev
    if c["lifespan"] in ("once_when", "now") or fires >= spec["mandate"]["max_fires"]:
        _entries_over(play["id"], "fired" if c["lifespan"] in ("once_when", "now")
                      else "reached its fire limit", waiting=bool(ev and ev["kind"] == "proposed"))
    return ev


def _halts_entries(do: dict) -> bool:
    """What a halt stops: new trading positions. Exits always run, and a
    delivery buy or a rebalance is investing, not the day's trading."""
    return do["kind"] in ("short", "options", "future") or (
        do["kind"] == "buy" and do.get("product") in ("MIS", "PERP"))


def _next_open(now: float) -> int:
    lt = datetime.fromtimestamp(now, timezone.utc).timestamp() + ds.IST_OFF
    day = int(lt // 86400) + 1
    return day * 86400 - ds.IST_OFF + 9 * 3600


def _halted(uid: int) -> dict | None:
    with ds._users_lock:
        r = _db().execute("SELECT until, why, play_id FROM play_halts WHERE user_id=?", (uid,)).fetchone()
    if not r or r[0] <= time.time():
        return None
    when = time.strftime("%d %b %H:%M", time.gmtime(r[0] + ds.IST_OFF))
    return {"until": r[0], "why": r[1], "play_id": r[2],
            "text": f"trading is halted until {when} IST: {r[1]}"}


def _halt(play: dict, ts: int, body: dict) -> dict:
    spec = play["spec"]
    why = describe(spec["when"], spec) if spec.get("when") else spec["title"]
    until = _next_open(time.time())
    with ds._users_lock:
        _db().execute("INSERT OR REPLACE INTO play_halts (user_id, until, why, play_id, created) "
                      "VALUES (?,?,?,?,?)", (play["user_id"], until, why, play["id"], int(time.time())))
        _db().commit()
    closed = []
    if spec["do"].get("close"):
        import daybook
        for p in daybook.open_positions(play["user_id"]):
            r = daybook.close(p, p["mark_px"] or p["entry_px"], f"halted: {why}")
            if r:
                closed.append(r)
    return _event(play, ts, "fired", spec["title"],
                  {**body, "halted_until": until, "closed": closed,
                   "reason": f"no new intraday, F&O or perp entries until "
                             f"{time.strftime('%d %b %H:%M', time.gmtime(until + ds.IST_OFF))} IST"},
                  status="done")


def lift_halt(uid: int) -> dict:
    with ds._users_lock:
        n = _db().execute("DELETE FROM play_halts WHERE user_id=?", (uid,)).rowcount
        _db().commit()
    return {"lifted": bool(n)}


def _entries_over(pid: int, why: str, waiting: bool = False) -> None:
    """No more entries. A play that still holds shares and has a time exit
    stays armed to run it (its stop and target already rest in the book); a
    proposal still waiting keeps it open until it is answered."""
    play = _load(pid)
    mg = play["spec"].get("manage") or {}
    exits = (bool(mg.get("exit_at")) and play["held_qty"] > 0) or \
        (bool(mg.get("exit_when")) and _holding(play))
    if exits or waiting:
        _set(pid, why=EXITS_ONLY if exits else why)
        return
    _set(pid, state="done", why=why)
    _load_index()


EXITS_ONLY = "entries done; watching its exit"


def _day_route(do: dict) -> bool:
    return do["kind"] in ("short", "cover", "options", "future") or (
        do["kind"] in ("buy", "sell") and do.get("product") in ("MIS", "PERP"))


def _day_ticket(play: dict) -> tuple[dict | None, str]:
    import daybook
    spec, do, m = play["spec"], play["spec"]["do"], play["spec"]["mandate"]
    if do["kind"] in ("cover", "sell"):
        opens = daybook.open_positions(play["user_id"], play["id"])
        if not opens:
            return None, "this play has no open position to close"
        return {"book": "day", "close": True, "legs": [
            {"symbol": p["symbol"], "side": p["side"], "qty": p["qty"], "px": p["mark_px"]} for p in opens]}, ""
    rd = dict(do)
    if do["kind"] == "buy":
        rd["kind"] = "long"
    try:
        legs = daybook.resolve(rd, do.get("product") or "MIS")
    except daybook.Refused as exc:
        return None, str(exc)
    need = daybook.group_margin(legs)
    if need > m["capital_inr"] + 1e-6:
        return None, (f"it needs about ₹{need:,.0f} of margin, above the play's ₹{m['capital_inr']:,.0f} "
                      f"limit")
    stale = [lg["symbol"] for lg in legs if lg.get("stale")]
    return {"book": "day", "legs": legs, "margin_est": round(need, 2),
            "stale": stale, "product": do.get("product")}, ""


def _ticket(play: dict, price: float) -> tuple[dict | None, str]:
    """The order a fire would place, sized and checked against the mandate by
    code. The model chose rupees; this turns them into a quantity."""
    spec, do, m = play["spec"], play["spec"]["do"], play["spec"]["mandate"]
    if _day_route(do):
        return _day_ticket(play)
    if do["kind"] == "rebalance":
        return _rebalance_ticket(play)
    sym = do["symbol"]
    if customsec.is_custom(sym):
        return _basket_ticket(play)
    mark = None
    if _paper is not None:
        try:
            mark = _paper.mark_price(sym)
        except Exception:                                   # noqa: BLE001
            mark = None
    px = float(mark) if mark is not None else (price if sym == spec["symbol"] else None)
    if not px:
        return None, f"no current price for {sym}"
    size = do.get("size") or {}
    if do["kind"] == "sell":
        held = play["held_qty"] if play["held_sym"] == sym else 0
        qty = held if size.get("all") or not size.get("qty") else min(float(size["qty"]), held)
        if not qty:
            return None, f"this play holds no {sym} to sell"
        return {"symbol": sym, "side": "SELL", "qty": qty, "price": px}, ""
    inr = float(size.get("inr") or 0) or (float(size["qty"]) * px if size.get("qty") else 0)
    if inr > m["max_order_inr"] + 1e-6:
        return None, f"₹{inr:,.0f} is above the play's ₹{m['max_order_inr']:,.0f} order limit"
    if play["spent_inr"] + inr > m["capital_inr"] + 1e-6:
        return None, (f"₹{inr:,.0f} more would pass the play's ₹{m['capital_inr']:,.0f} limit "
                      f"(₹{play['spent_inr']:,.0f} already used)")
    qty = float(size["qty"]) if size.get("qty") else _paper_qty(sym, inr, px)
    if qty <= 0:
        return None, f"₹{inr:,.0f} buys less than one unit of {sym} at {px:,.2f}"
    t = {"symbol": sym, "side": "BUY", "qty": qty, "price": px, "inr": round(qty * px, 2)}
    mg = spec.get("manage") or {}
    if mg.get("stop_pct"):
        t["stop_loss"] = round(px * (1 - mg["stop_pct"] / 100), 2)
    if mg.get("target_pct"):
        t["take_profit"] = round(px * (1 + mg["target_pct"] / 100), 2)
    return t, ""


def _rebalance_ticket(play: dict) -> tuple[dict | None, str]:
    do = play["spec"]["do"]
    last = do.get("targets") or {}
    if play["spec"]["contract"]["discretion"] == "plan":
        targets, why, problem = policy.plan(play)
        if not targets:
            return None, problem
        held = set(policy.book(play))
        structural = set(targets) != (set(last) or held)
        return policy.ticket(play, targets, why=why + (f" ({problem})" if problem else ""),
                             source="planner", structural=structural, reasons=play.get("_reasons"))
    return policy.ticket(play, last, source="fixed")


def _place_rebalance(play: dict, t: dict, ts: int, body: dict) -> dict:
    fills, failed, got = policy.place(play, t)
    spec = dict(play["spec"])
    spec["do"] = {**spec["do"], "targets": t["targets"]}
    with ds._users_lock:
        _db().execute("UPDATE plays SET spec=? WHERE id=?", (json.dumps(spec), play["id"]))
        _db().commit()
    _set(play["id"], held_legs=json.dumps(got["held"]), held_sym="rebalance",
         held_qty=float(sum(got["held"].values())), spent_inr=play["spent_inr"] + got["net"])
    out = {**body, "ticket": t, "fills": fills}
    if failed:
        out["failed"] = failed
        out["reason"] = (f"{len(failed)} of {len(t['legs'])} trades did not fill: "
                         + "; ".join(f"{f['symbol']} ({f['reason']})" for f in failed))
    return _event(play, ts, "filled" if fills or not t["legs"] else "rejected", play["spec"]["title"],
                  out, status="done")


def _mark(sym: str) -> float | None:
    try:
        v = _paper.mark_price(sym) if _paper is not None else None
    except Exception:                                       # noqa: BLE001
        v = None
    return float(v) if v else None


def _basket_ticket(play: dict) -> tuple[dict | None, str]:
    """A basket order is one order per member: its share of the rupees at
    the member's weight, in whole shares at the member's own price. What
    rounds away is said, never spent."""
    spec, do, m = play["spec"], play["spec"]["do"], play["spec"]["mandate"]
    sec = customsec.get(do["symbol"])
    size = do.get("size") or {}
    if do["kind"] == "sell":
        held = _held_legs_now(play)
        if not any(held.values()):
            return None, f"this play holds none of {do['symbol']} to sell"
        legs = [{"symbol": s, "side": "SELL", "qty": q, "price": _mark(s)} for s, q in held.items() if q]
        return {"basket": do["symbol"], "side": "SELL", "legs": legs}, ""
    inr = float(size.get("inr") or 0)
    if not inr:
        return None, "size a basket in rupees (size.inr)"
    if inr > m["max_order_inr"] + 1e-6:
        return None, f"₹{inr:,.0f} is above the play's ₹{m['max_order_inr']:,.0f} order limit"
    if play["spent_inr"] + inr > m["capital_inr"] + 1e-6:
        return None, (f"₹{inr:,.0f} more would pass the play's ₹{m['capital_inr']:,.0f} limit "
                      f"(₹{play['spent_inr']:,.0f} already used)")
    legs, short = [], []
    for mem in sec["members"]:
        px = _mark(mem["symbol"])
        if not px:
            return None, f"no current price for {mem['symbol']}"
        q = _paper_qty(mem["symbol"], inr * mem["weight"], px)
        if q <= 0:
            short.append(mem["symbol"])
            continue
        legs.append({"symbol": mem["symbol"], "side": "BUY", "qty": q, "price": px,
                     "weight": mem["weight"], "inr": round(q * px, 2)})
    if short:
        return None, (f"₹{inr:,.0f} buys less than one share of {', '.join(short)} at its weight; "
                      f"size the basket larger")
    used = sum(lg["inr"] for lg in legs)
    t = {"basket": do["symbol"], "side": "BUY", "legs": legs, "inr": round(used, 2),
         "uninvested_inr": round(inr - used, 2)}
    mg = spec.get("manage") or {}
    for lg in legs:
        if mg.get("stop_pct"):
            lg["stop_loss"] = round(lg["price"] * (1 - mg["stop_pct"] / 100), 2)
        if mg.get("target_pct"):
            lg["take_profit"] = round(lg["price"] * (1 + mg["target_pct"] / 100), 2)
    return t, ""


def _place_basket(play: dict, ticket: dict, ts: int, body: dict) -> dict:
    held = dict(play["held_legs"] or {})
    fills, failed, spent = [], [], 0.0
    for lg in ticket["legs"]:
        try:
            got = _paper.place_order(play["user_id"], lg["symbol"], lg["side"], lg["qty"],
                                     source="play", origin_kind=f"play:{play['id']}",
                                     stop_loss=lg.get("stop_loss"), take_profit=lg.get("take_profit"))
        except Exception as exc:                            # noqa: BLE001
            failed.append({"symbol": lg["symbol"], "reason": str(exc)})
            continue
        px = float(((got or {}).get("fill") or {}).get("price") or lg["price"] or 0)
        fills.append({"symbol": lg["symbol"], "side": lg["side"], "qty": lg["qty"], "price": px})
        sign = 1 if lg["side"] == "BUY" else -1
        held[lg["symbol"]] = max(0.0, held.get(lg["symbol"], 0.0) + sign * lg["qty"])
        spent += lg["qty"] * px if lg["side"] == "BUY" else 0
    held = {k: v for k, v in held.items() if v}
    _set(play["id"], held_legs=json.dumps(held), held_sym=ticket["basket"],
         held_qty=float(sum(held.values())), spent_inr=play["spent_inr"] + spent,
         **({"entry_bar": ts} if ticket["side"] == "BUY" and not play["held_qty"] else {}))
    out = {**body, "ticket": ticket, "fills": fills}
    if failed:
        out["failed"] = failed
        out["reason"] = (f"{len(failed)} of {len(ticket['legs'])} members did not fill: "
                         + "; ".join(f"{f['symbol']} ({f['reason']})" for f in failed))
    return _event(play, ts, "filled" if fills else "rejected", play["spec"]["title"], out, status="done")


def _held_legs_now(play: dict) -> dict:
    held = dict(play.get("held_legs") or {})
    if not held or _paper is None:
        return held
    try:
        pos = {p["symbol"]: float(p["quantity"]) for p in _paper.api_holdings(play["user_id"])[1]}
    except Exception:                                       # noqa: BLE001
        return held
    return {s: min(q, pos.get(s, 0.0)) for s, q in held.items()}


def _place(play: dict, ticket: dict, ts: int, body: dict) -> dict:
    if ticket.get("book") == "day":
        import daybook
        if ticket.get("close"):
            res = daybook.close_play(play["id"], "closed by the play")
            return _event(play, ts, "exited", play["spec"]["title"], {**body, "closed": res}, status="done")
        got = daybook.open_group(play["user_id"], play["id"], ticket["legs"], play["spec"].get("manage") or {})
        cost = sum(lg["px"] * lg["qty"] * lg.get("fx", 1.0) for lg in ticket["legs"] if lg["side"] == "long")
        if play["spec"].get("member"):
            _set(play["id"], spent_inr=play["spent_inr"] + (ticket.get("margin_est") or cost))
            _mark_member(play["id"], play["spec"]["member"], entry=ts)
        else:
            _set(play["id"], spent_inr=play["spent_inr"] + (ticket.get("margin_est") or cost), entry_bar=ts)
        note = ("priced at the last close because no live quote came back. Quotes are asked for "
                "every 5 seconds; its stop and target are watched from the first live quote, and "
                "until then they are not" if ticket.get("stale") else "")
        ev = _event(play, ts, "filled", play["spec"]["title"],
                    {**body, "ticket": ticket, "legs": got["legs"], "group": got["group"],
                     "estimates": got["estimates"], **({"reason": note} if note else {})}, status="done")
        check_guards(play["user_id"])
        return ev
    if _paper is None:
        return _event(play, ts, "rejected", play["spec"]["title"],
                      {**body, "reason": "the paper book is not running"}, status="done")
    if ticket.get("basket"):
        return _place_basket(play, ticket, ts, body)
    if ticket.get("rebalance"):
        return _place_rebalance(play, ticket, ts, body)
    try:
        got = _paper.place_order(play["user_id"], ticket["symbol"], ticket["side"], ticket["qty"],
                                 source="play", origin_kind=f"play:{play['id']}",
                                 stop_loss=ticket.get("stop_loss"),
                                 take_profit=ticket.get("take_profit"))
    except Exception as exc:                                # noqa: BLE001
        return _event(play, ts, "rejected", play["spec"]["title"],
                      {**body, "ticket": ticket, "reason": str(exc)}, status="done")
    fill = (got or {}).get("fill") or {}
    px = float(fill.get("price") or ticket["price"])
    member = play["spec"].get("member")
    if member:
        legs = dict((_load(play["id"]) or play).get("held_legs") or {})
        was = legs.get(member, 0.0)
        legs[member] = max(0.0, was + (ticket["qty"] if ticket["side"] == "BUY" else -ticket["qty"]))
        legs = {k: v for k, v in legs.items() if v}
        _set(play["id"], held_legs=json.dumps(legs), held_sym="*", held_qty=float(sum(legs.values())),
             spent_inr=play["spent_inr"] + (ticket["qty"] * px if ticket["side"] == "BUY" else 0))
        if ticket["side"] == "BUY" and not was:
            _mark_member(play["id"], member, entry=ts)
    elif ticket["side"] == "BUY":
        _set(play["id"], held_qty=play["held_qty"] + ticket["qty"], held_sym=ticket["symbol"],
             spent_inr=play["spent_inr"] + ticket["qty"] * px,
             **({} if play["held_qty"] else {"entry_bar": ts}))
    else:
        _set(play["id"], held_qty=max(0.0, play["held_qty"] - ticket["qty"]))
    return _event(play, ts, "filled", play["spec"]["title"],
                  {**body, "ticket": ticket, "fill_price": px,
                   "order_id": (got or {}).get("order_id") or (got or {}).get("id")}, status="done")


def _maybe_time_exit(play: dict, data: dict, i: int) -> dict | None:
    mg = play["spec"].get("manage") or {}
    if not mg.get("exit_at") or play["spec"]["interval"] == "1d":
        return None
    hh, mm = (int(x) for x in mg["exit_at"].split(":"))
    if data["hhmm"][i] < hh * 100 + mm:
        return None
    if customsec.is_custom(play["held_sym"]):
        legs = {s: q for s, q in _held_legs_now(play).items() if q}
        if not legs:
            _set(play["id"], held_qty=0, held_legs="{}")
            return None
        ticket = {"basket": play["held_sym"], "side": "SELL",
                  "legs": [{"symbol": s, "side": "SELL", "qty": q, "price": _mark(s)} for s, q in legs.items()]}
    else:
        held = _held_now(play)
        if not held:
            _set(play["id"], held_qty=0)
            return None
        ticket = {"symbol": play["held_sym"], "side": "SELL", "qty": held,
                  "price": data["close"][i]}
    body = {"reason": f"time exit at {mg['exit_at']}", "symbol": play["held_sym"]}
    if play["spec"]["contract"]["authority"] == "manage":
        ev = _place(play, ticket, data["t"][i], body)
    else:
        ev = _event(play, data["t"][i], "proposed", play["spec"]["title"],
                    {**body, "ticket": ticket})
    return ev


def _held_now(play: dict) -> float:
    """What this play bought, capped by what the book still holds: a stop or
    target that already filled must not be sold twice."""
    held = play["held_qty"]
    if not held or _paper is None:
        return held
    try:
        pos = {p["symbol"]: float(p["quantity"]) for p in _paper.api_holdings(play["user_id"])[1]}
    except Exception:                                       # noqa: BLE001
        return held
    return min(held, pos.get(play["held_sym"], 0.0))


def _day_event(play_id: int, kind: str, title: str, body: dict) -> None:
    """An exit or a note from the day book, into the play's inbox."""
    play = _load(play_id)
    if play:
        _event(play, int(time.time()), kind, play["spec"]["title"], {**body, "detail": title},
               status="done")
        if kind == "exited":
            check_guards(play["user_id"])


def check_guards(uid: int) -> dict | None:
    """The trader's armed guards, read against the book as it is now. Run
    after every fill and exit, so a loss limit stops the day on the exit that
    breaks it, not at the next bar close. Never raises."""
    try:
        if _halted(uid):
            return None
        with ds._users_lock:
            ids = [r[0] for r in _db().execute("SELECT id FROM plays WHERE user_id=? AND state='armed' "
                                               "AND spec LIKE '%\"kind\": \"halt\"%'", (uid,))]
        for pid in ids:
            play = _load(pid)
            if not play or play["spec"]["do"]["kind"] != "halt":
                continue
            data, _ = frame(play["spec"])
            series, _ = run_measures(play["spec"], data)
            tr = truth(play["spec"]["when"], data, series)
            if tr and tr[-1] is True:
                return _fire(play, data, series, len(data["t"]) - 1, late=False)
    except Exception:                                       # noqa: BLE001
        log.warning("plays: guard check failed", exc_info=True)
    return None


JUDGE_PER_DAY = 300
_judged: dict[tuple, int] = {}


def _judge_budget(pid: int) -> bool:
    """A play's judge reads at most JUDGE_PER_DAY items a day. Words that match
    half the news would otherwise spend a model call per headline."""
    key = (pid, int(time.time() // 86400))
    n = _judged.get(key, 0)
    if n >= JUDGE_PER_DAY:
        if n == JUDGE_PER_DAY:
            log.info("plays: play %s reached its judge budget for today", pid)
            _judged[key] = n + 1
        return False
    _judged[key] = n + 1
    return True


def on_items(items: list[dict]) -> None:
    """New filings and headlines, from play_events' poller. Each armed play
    clocked by events gets the ones its words match; its judge reads each;
    its own conditions are checked on the latest closed bar; then it fires."""
    import play_events
    with ds._users_lock:
        rows = _db().execute(f"SELECT {', '.join(_COLS)} FROM plays WHERE state='armed' "
                             "AND spec LIKE '%\"on\": {%'").fetchall()
    for t in rows:
        play = _row(t)
        on = play["spec"].get("on")
        if not on:
            continue
        for it in items:
            try:
                if not play_events.matches(it, on):
                    continue
                verdict = None
                if on["judge"] and not _judge_budget(play["id"]):
                    continue
                if on["judge"]:
                    verdict = play_events.judge(on["judge"], it)
                    if not verdict.get("verdict") or verdict.get("confidence", 0) < on["min_confidence"]:
                        continue
                ev = {"source": it["source"], "who": it["who"], "symbol": it["symbol"],
                      "title": it["title"], "url": it["url"], "ts": it["ts"],
                      "quote": (verdict or {}).get("quote"), "value": (verdict or {}).get("value"),
                      "question": on["judge"]}
                play = _load(play["id"])
                if not play or play["state"] != "armed" or play["why"] in (EXITS_ONLY, "fired"):
                    break
                data, _ = frame(play["spec"])
                series, _ = run_measures(play["spec"], data)
                i = len(data["t"]) - 1
                if play["spec"].get("when"):
                    tr = truth(play["spec"]["when"], data, series)
                    if tr[i] is not True:
                        continue
                _fire(play, data, series, i, late=bool(it.get("late")), event=ev)
            except Exception:                               # noqa: BLE001
                STATS["errors"] += 1
                log.warning("plays: event play %s failed", play["id"], exc_info=True)


def start() -> dict:
    global _WORKER
    init_db()
    _load_index()
    if _WORKER is None or not _WORKER.is_alive():
        _STOP.clear()
        _WORKER = threading.Thread(target=_worker, name="plays", daemon=True)
        _WORKER.start()
    try:
        import daybook
        daybook.bind(ds)
        daybook.on_event = _day_event
        daybook.start()
    except Exception:                                       # noqa: BLE001
        log.warning("plays: the day book did not start", exc_info=True)
    try:
        import play_events
        play_events.bind(ds)
        play_events.start(on_items)
    except Exception:                                       # noqa: BLE001
        log.warning("plays: the event poller did not start", exc_info=True)
    if os.environ.get("CHARTO_PLAYS") == "1":
        try:
            import fo_store
            fo_store.watch_fn = _option_underlyings
            fo_store.start()
        except Exception:                                   # noqa: BLE001
            log.warning("plays: the F&O history store did not start", exc_info=True)
    with _INDEX_LOCK:
        return {"armed": sum(len(v) for v in _INDEX.values()), "symbols": len(_INDEX)}


def _option_underlyings() -> set[str]:
    """What armed option and future plays trade: the F&O store snapshots
    their chains through the day."""
    with ds._users_lock:
        rows = _db().execute("SELECT spec FROM plays WHERE state='armed'").fetchall()
    out = set()
    for (sp,) in rows:
        do = json.loads(sp).get("do") or {}
        if do.get("kind") in ("options", "future") and do.get("underlying"):
            out.add(str(do["underlying"]).upper())
    return out


def register_hook() -> None:
    ds.register_bar_hook(on_bar)


def stop() -> None:
    _STOP.set()


# ══ compile, edit, act on a play ════════════════════════════════════════════

def compile_play(uid: int, raw: dict, *, chart_symbol: str = "", chart_interval: str = "",
                 arm: bool = True, pid: int | None = None) -> dict:
    init_db()
    spec, said = normalise(raw, uid, chart_symbol, chart_interval)
    prior = get(uid, pid) if pid else None
    if pid and not prior:
        raise Bad(f"no play {pid} on this account")
    adopt = _adoption(uid, spec, said) if spec["do"]["kind"] == "rebalance" else None
    try:
        hist, data, series, tr = replay(spec)
    except Bad:
        # "do it now" reads no bars: an option order on an index whose chart has
        # no stored bars here is still an order
        if spec["contract"]["lifespan"] != "now" or spec.get("measures") or spec.get("when") \
                or spec.get("on"):
            raise
        data, series, tr = _bare(), {}, []
        hist = {"bars": 0, "fires": 0, "per_month": 0, "notes": [], "now": {}}
    status = _status(spec, data, series)
    hist["conditions"] = status
    diff = _diff(prior["spec"], spec) if prior else None
    structural = bool(diff and any(d["what"] != "limits" for d in diff))
    state = "armed" if arm else "draft"
    # arm from the latest closed bar: history is the replay's, not the runtime's
    pid = _save(uid, spec, hist, state=state, pid=pid, last_eval_ts=data["t"][-1],
                needs_confirm=bool(prior and structural and prior.get("fires")))
    if adopt:
        # the holdings move with the instruction: the old play no longer owns them
        _set(pid, held_legs=json.dumps(adopt["legs"]), held_sym="rebalance",
             held_qty=float(sum(adopt["legs"].values())), spent_inr=adopt["spent"])
        _set(adopt["from"], held_legs="{}", held_qty=0, held_sym="")
    if prior and structural:
        _set(pid, accepted=0)
    row = get(uid, pid)
    if state == "armed" and spec["contract"]["lifespan"] == "now":
        play = _load(pid)
        ev = _fire(play, data, series, len(data["t"]) - 1, late=False)
        row = get(uid, pid)
        row["_now_event"] = ev
    feed = None
    if _alerts is not None and state == "armed" and (spec.get("when") or not spec.get("on")):
        try:
            if hyperliquid.is_perp(spec["symbol"]):
                feed = None                 # the Hyperliquid poller clocks it (dataserver boot)
            elif spec.get("universe"):
                fs = [_alerts.ensure_feed(s) for s in spec["universe"]["symbols"]]
                feed = next((f for f in fs if isinstance(f, dict) and f.get("note")), None)
            elif customsec.is_custom(spec["symbol"]):
                fs = [_alerts.ensure_feed(s) for s in customsec.members(spec["symbol"])]
                feed = next((f for f in fs if isinstance(f, dict) and f.get("note")), fs[0] if fs else None)
            else:
                feed = _alerts.ensure_feed(spec["symbol"])
        except Exception:                                   # noqa: BLE001
            feed = None
    return {"play": row, "assumed": said, "diff": diff, "feed": feed,
            "card": card(row, {"conditions": status})}


def _adoption(uid: int, spec: dict, said: list[str]) -> dict | None:
    """A rebalance of another play's holdings takes them over, and with no
    targets given keeps the weights that play was built with (a basket's)
    or, failing that, the weights it holds now."""
    do = spec["do"]
    if isinstance(do["of"], int):
        src = get(uid, do["of"])
        if not src:
            raise Bad(f"no play {do['of']} on this account")
        legs = dict(src.get("held_legs") or {})
        if not legs and src["held_qty"] and src["held_sym"] and not customsec.is_custom(src["held_sym"]):
            legs = {src["held_sym"]: src["held_qty"]}
        if not legs:
            raise Bad(f"play {do['of']} holds nothing to rebalance")
        do["of"] = "self"
        if not do.get("targets") and spec["contract"]["discretion"] != "plan":
            sec = customsec.get(src["held_sym"]) if customsec.is_custom(src["held_sym"]) else None
            rows = ([{"symbol": m["symbol"], "weight": m["weight"]} for m in sec["members"]] if sec else
                    [{"symbol": k, "weight": v * (policy.mark(k) or 0)} for k, v in legs.items()])
            do["targets"], _ = policy.norm_targets(rows, do.get("cash_pct") or 0, 1.0)
            said.append("keeps the weights " + ("the basket was built with" if sec else "it holds now"))
        return {"from": src["id"], "legs": legs, "spent": src["spent_inr"]}
    if do["of"] == "portfolio" and not do.get("targets") and spec["contract"]["discretion"] != "plan":
        probe = {"user_id": uid, "held_legs": {}, "spent_inr": 0, "spec": spec}
        w, _, _ = policy.weights_now(probe)
        if not w:
            raise Bad("the paper portfolio holds nothing to rebalance")
        do["targets"], _ = policy.norm_targets([{"symbol": k, "weight": v} for k, v in w.items()],
                                               0, 1.0)
        said.append("keeps the weights the portfolio holds now")
    return None


def _bare() -> dict:
    """One bar at the present moment, for a play that acts now and reads
    nothing: the clock its event is stamped with, and no price of its own."""
    t = int(time.time()) // 60 * 60
    return {"t": [t], "hhmm": [_hhmm(t, "1m")], "open": [None], "high": [None], "low": [None],
            "close": [None], "volume": [None]}


def _diff(a: dict, b: dict) -> list[dict]:
    """What an edit moved, compared as structure, never by reading the
    trader's words. A changed number is a parameter; a changed shape is a
    different rule."""
    out = []
    if json.dumps(a.get("when"), sort_keys=True) != json.dumps(b.get("when"), sort_keys=True):
        out.append({"what": "conditions", "from": describe(a.get("when") or {}, a),
                    "to": describe(b.get("when") or {}, b)})
    am = {m["name"]: m for m in a.get("measures") or []}
    bm = {m["name"]: m for m in b.get("measures") or []}
    for k in sorted(set(am) | set(bm)):
        if json.dumps(am.get(k), sort_keys=True) != json.dumps(bm.get(k), sort_keys=True):
            out.append({"what": "measure", "name": k,
                        "from": (am.get(k) or {}).get("formula"), "to": (bm.get(k) or {}).get("formula")})
    def plain(k, d):
        d = dict(d.get(k) or {})
        d.pop("inferred_from", None)       # the trader's words, not the rule
        return json.dumps(d, sort_keys=True)
    for k in ("do", "manage", "contract"):
        if plain(k, a) != plain(k, b):
            out.append({"what": k, "from": a.get(k), "to": b.get(k)})
    if json.dumps(a.get("mandate"), sort_keys=True) != json.dumps(b.get("mandate"), sort_keys=True):
        out.append({"what": "limits", "from": a.get("mandate"), "to": b.get("mandate")})
    if a.get("interval") != b.get("interval") or a.get("symbol") != b.get("symbol"):
        out.append({"what": "clock", "from": f"{a.get('symbol')} {a.get('interval')}",
                    "to": f"{b.get('symbol')} {b.get('interval')}"})
    return out


def act(uid: int, pid: int, action: str) -> dict:
    play = get(uid, pid)
    if not play:
        raise Bad(f"no play {pid} on this account")
    if action == "pause":
        _set(pid, state="paused", why="paused by you")
    elif action == "resume":
        if play["state"] == "done":
            raise Bad("this play has finished; compile it again to run it again")
        # resuming never replays the bars it was paused through
        data, _ = frame(play["spec"], limit=5)
        _set(pid, state="armed", why="", last_eval_ts=data["t"][-1])
    elif action == "policy":
        if play["spec"]["do"]["kind"] != "rebalance":
            raise Bad("only a rebalance can become a standing policy")
        spec = dict(play["spec"])
        spec["contract"] = {**spec["contract"], "lifespan": "policy",
                            "discretion": "plan" if spec["do"].get("objective") and
                            (spec["do"].get("universe") or spec["do"].get("targets")) else "none"}
        spec["policy"] = {"every": "week", "drift_pct": 5.0}
        spec["mandate"] = {**spec["mandate"], "max_fires": 10_000}
        data, _ = frame(spec, limit=5)
        with ds._users_lock:
            _db().execute("UPDATE plays SET spec=? WHERE id=?", (json.dumps(spec), pid))
            _db().commit()
        _set(pid, state="armed", why="", last_eval_ts=data["t"][-1])
    elif action == "delete":
        with ds._users_lock:
            _db().execute("DELETE FROM plays WHERE id=? AND user_id=?", (pid, uid))
            _db().commit()
        _load_index()
        return {"deleted": pid}
    else:
        raise Bad("action is pause, resume, policy or delete")
    _load_index()
    return {"play": card(get(uid, pid))}


EDITABLE = ("title", "size_inr", "leverage", "stop_pct", "target_pct", "trail_pct", "exit_at",
            "capital_inr", "interval", "policy.every", "policy.drift_pct")


def edit_field(uid: int, pid: int, field: str, value) -> dict:
    """One number or word on a play, changed by the trader on its card. The
    play is rebuilt through the same checks the chat's edits go through, so a
    value the chat could not set cannot be set here either; it keeps its state
    (a paused play stays paused) and the replay is redone on the new value.
    Fields: EDITABLE, cond.<i>.right, cond.<i>.for_bars, weight.<SYMBOL> (a
    percent)."""
    play = get(uid, pid)
    if not play:
        raise Bad(f"no play {pid} on this account")
    spec = json.loads(json.dumps(play["spec"]))
    if spec["contract"]["lifespan"] == "now" and play.get("fires"):
        raise Bad("this play has already run; ask for a new one instead")
    if play["state"] == "done":
        raise Bad("this play has finished; ask for a new one instead")
    do, mg, md = spec["do"], spec.setdefault("manage", {}), spec["mandate"]
    blank = value in (None, "", "off")

    def num(lo=None, hi=None):
        try:
            v = float(str(value).replace(",", "").replace("₹", "").replace("%", "").replace("×", "").strip())
        except ValueError:
            raise Bad(f"{field} must be a number") from None
        if (lo is not None and v < lo) or (hi is not None and v > hi):
            raise Bad(f"{field} must be between {lo:g} and {hi:g}")
        return v
    if field == "title":
        if blank:
            raise Bad("a play needs a title")
        spec["title"] = str(value)[:80]
    elif field == "size_inr":
        if do["kind"] not in ("buy", "short"):
            raise Bad("only a buy or a short has a rupee size")
        do["size"] = {"inr": num(100, 1e9)}
        if md.get("capital_inr", 0) < do["size"]["inr"] * (5 if spec["contract"]["lifespan"] == "standing" else 1):
            md["capital_inr"] = do["size"]["inr"] * (5 if spec["contract"]["lifespan"] == "standing" else 1)
            md["max_order_inr"] = max(md.get("max_order_inr") or 0, do["size"]["inr"])
    elif field == "leverage":
        if do.get("product") != "PERP":
            raise Bad("leverage is for a perpetual")
        do["leverage"] = num(1, 200)
    elif field in ("stop_pct", "target_pct", "trail_pct"):
        if blank:
            mg.pop(field, None)
        else:
            mg[field] = num(0.01, 99.99)
    elif field == "exit_at":
        if blank:
            mg.pop("exit_at", None)
        else:
            mg["exit_at"] = str(value).strip()
    elif field in ("max_loss_inr", "target_inr"):
        if blank:
            mg.pop(field, None)
        else:
            mg[field] = num(100, 1e9)
    elif field == "product":
        if do["kind"] not in ("buy", "sell") or do.get("product") == "PERP" or customsec.is_custom(do.get("symbol", "")):
            raise Bad("only a stock buy or sell can switch between delivery and intraday")
        if str(value) not in ("CNC", "MIS"):
            raise Bad("product is CNC (delivery) or MIS (intraday)")
        do["product"] = str(value)
        if value == "CNC":
            for k in ("trail_pct", "exit_at", "max_loss_inr", "target_inr", "on_leg_stop"):
                mg.pop(k, None)
    elif field == "authority":
        if do["kind"] == "notify":
            raise Bad("this play only tells you; ask in chat to make it trade")
        if value not in ("propose", "act", "manage"):
            raise Bad("authority is propose, act or manage")
        spec["contract"]["authority"] = str(value)
    elif field == "lifespan":
        if spec["contract"]["lifespan"] in ("now", "policy") or value not in ("once_when", "standing"):
            raise Bad("this play's lifespan cannot be changed here")
        spec["contract"]["lifespan"] = str(value)
        md["max_fires"] = 1 if value == "once_when" else max(md.get("max_fires") or 0, 20)
    elif field == "on_leg_stop":
        if blank:
            mg.pop("on_leg_stop", None)
        elif value in ("exit_all", "move_others_to_cost"):
            mg["on_leg_stop"] = str(value)
        else:
            raise Bad("on_leg_stop is exit_all or move_others_to_cost")
    elif field == "capital_inr":
        md["capital_inr"] = md["max_order_inr"] = num(100, 1e10)
    elif field == "interval":
        spec["interval"] = str(value)
    elif field in ("policy.every", "policy.drift_pct"):
        if not spec.get("policy"):
            raise Bad("only a standing rebalance has a schedule")
        k = field.split(".")[1]
        spec["policy"][k] = str(value) if k == "every" else num(0.5, 50)
    elif field.startswith("cond."):
        _, i, k = (field.split(".") + ["", ""])[:3]
        when = spec.get("when") or {}
        rows = when.get("all") or when.get("any") or []
        if not i.isdigit() or int(i) >= len(rows) or k not in ("right", "right2", "for_bars", "bars", "op"):
            raise Bad("that condition is not on this play")
        c = rows[int(i)]
        if k == "right" and isinstance(c.get("right"), str) and not _is_num(c["right"]):
            raise Bad(f"this condition compares with {c['right']}, not a number; ask in chat to change it")
        if k == "op":
            if value not in ("above", "below", "cross_up", "cross_down"):
                raise Bad("op is above, below, cross_up or cross_down")
            c["op"] = str(value)
        else:
            c[k] = int(num(1, 500)) if k in ("for_bars", "bars") else num()
    elif field.startswith("weight."):
        if do["kind"] != "rebalance" or not isinstance(do.get("targets"), dict):
            raise Bad("weights are for a rebalance with targets")
        sym = ds.canon_symbol(field.split(".", 1)[1])
        t = dict(do["targets"])
        if sym not in t:
            raise Bad(f"{sym} is not one of its targets")
        w = num(0, 100) / 100
        rest = sum(v for k2, v in t.items() if k2 != sym)
        room = sum(t.values())                          # what the names share (cash stays as it was)
        if w > room:
            raise Bad(f"{sym} can take at most {room * 100:.0f}% (the rest is held as cash)")
        for k2 in t:
            t[k2] = w if k2 == sym else (t[k2] / rest * (room - w) if rest else (room - w) / (len(t) - 1))
        do["targets"] = [{"symbol": k2, "weight": v} for k2, v in t.items()]
        do["cash_pct"] = max(0.0, (1 - sum(t.values())) * 100)
    else:
        raise Bad(f"{field} cannot be changed here; ask in chat")
    raw = {**spec, "why": spec.get("thesis") or ""}
    state = play["state"]
    got = compile_play(uid, raw, arm=state == "armed", pid=pid)
    if state == "paused":
        _set(pid, state="paused", why=play.get("why") or "paused by you")
    _load_index()
    row = get(uid, pid)
    return {"play": card(row, {"conditions": row["history"].get("conditions")}),
            "assumed": got["assumed"], "changed": got["diff"]}


def _atr_pct(symbol: str, interval: str, n: int = 14) -> float | None:
    """The symbol's average true range on the play's own bar, as a percent of
    price: the yardstick the stop and trail choices are measured in."""
    try:
        rows = _rows(symbol, interval, n * 4 + 1)
    except Exception:                                       # noqa: BLE001
        return None
    if len(rows) < n + 1:
        return None
    trs = [max(r[2] - r[3], abs(r[2] - p[4]), abs(r[3] - p[4])) for p, r in zip(rows, rows[1:])][-n:]
    px = rows[-1][4]
    return sum(trs) / len(trs) / px * 100 if px else None


def _inr(v: float) -> str:
    """₹1,00,000: Indian grouping, as the page writes money."""
    n = f"{abs(v):.0f}"
    head, tail = n[:-3], n[-3:]
    while len(head) > 2:
        tail = head[-2:] + "," + tail
        head = head[:-2]
    return ("−" if v < 0 else "") + "₹" + (head + "," + tail if head else tail)


def _pct_label(v: float) -> str:
    return f"{v:.2f}".rstrip("0").rstrip(".")


def options(uid: int, pid: int) -> dict:
    """What can be changed on a play's card and the choices worth offering,
    from its own data: stops and trails in multiples of the symbol's ATR on
    the play's bar, sizes that risk a round number of rupees at its stop,
    leverage steps with their liquidation distance. `more` is every setting
    the play does not use yet, so the card can offer it."""
    play = get(uid, pid)
    if not play:
        raise Bad(f"no play {pid} on this account")
    spec = play["spec"]
    do, mg, c = spec["do"], spec.get("manage") or {}, spec["contract"]
    kind, product = do["kind"], do.get("product")
    day = product in ("MIS", "NRML", "PERP")
    orders = kind not in ("notify", "rebalance")
    sym = do.get("symbol") or do.get("underlying") or spec["symbol"]
    atr = _atr_pct(sym if not customsec.is_custom(sym) else spec["symbol"], spec["interval"]) \
        if kind not in ("options",) else None
    size = (do.get("size") or {}).get("inr")
    lev = float(do.get("leverage") or 1)
    f: dict[str, dict] = {}

    def risk(pct):
        if not size:
            return ""
        return f"₹{size * lev * pct / 100:,.0f} at risk on this size"
    if orders:
        if kind == "options":
            stops = [(x, f"{x}% of the premium") for x in (20, 25, 30, 40, 50)]
            targets = [(x, f"{x}% of the premium") for x in (25, 50, 75, 100)]
        elif atr:
            stops = [(round(atr * m, 2), f"{m:g}× ATR ({spec['interval']}) · {risk(atr * m)}".rstrip(" ·"))
                     for m in (1, 1.5, 2, 3, 4)]
            targets = [(round(atr * m, 2), f"{m:g}× ATR · {m / 1.5:.1f}R against a 1.5× ATR stop")
                       for m in (1.5, 3, 4.5, 6)]
        else:
            stops = [(x, risk(x)) for x in (0.5, 1, 2, 3, 5)]
            targets = [(x, "") for x in (1, 2, 4, 6, 10)]
        f["stop_pct"] = {"label": "Stop", "unit": "%", "value": mg.get("stop_pct"), "removable": True,
                         "choices": [{"value": v, "label": f"{_pct_label(v)}%", "hint": h} for v, h in stops]}
        f["target_pct"] = {"label": "Take profit", "unit": "%", "value": mg.get("target_pct"), "removable": True,
                           "choices": [{"value": v, "label": f"{_pct_label(v)}%", "hint": h} for v, h in targets]}
        if day and kind != "options":
            f["trail_pct"] = {"label": "Trailing stop", "unit": "%", "value": mg.get("trail_pct"), "removable": True,
                              "choices": [{"value": round(atr * m, 2), "label": f"{_pct_label(round(atr * m, 2))}%",
                                           "hint": f"{m:g}× ATR ({spec['interval']}): follows the best price by this much"}
                                          for m in (1, 1.5, 2, 3)] if atr else
                              [{"value": v, "label": f"{v}%", "hint": ""} for v in (0.5, 0.8, 1, 1.5, 2)]}
        if kind in ("buy", "short"):
            ch = [{"value": v, "label": _inr(v), "hint": ""} for v in (10_000, 25_000, 50_000, 1_00_000)]
            if mg.get("stop_pct"):
                ch = [{"value": round(r / (mg["stop_pct"] / 100) / lev, -2), "label":
                       _inr(round(r / (mg['stop_pct'] / 100) / lev, -2)),
                       "hint": f"risks {_inr(r)} at your {_pct_label(mg['stop_pct'])}% stop"} for r in (500, 1000, 2000, 5000)] + ch
            f["size_inr"] = {"label": "Margin" if product == "PERP" else "Allocate", "unit": "₹", "value": size,
                             "choices": ch}
        if day:
            cap = (size or 0) * lev
            f["max_loss_inr"] = {"label": "Loss limit (whole position)", "unit": "₹", "value": mg.get("max_loss_inr"),
                                 "removable": True, "choices": [{"value": round(cap * x / 100), "label": _inr(cap * x / 100),
                                                                 "hint": f"{x}% of the position"} for x in (0.5, 1, 2)] if cap else []}
            f["target_inr"] = {"label": "Profit target (whole position)", "unit": "₹", "value": mg.get("target_inr"),
                               "removable": True, "choices": [{"value": round(cap * x / 100), "label": _inr(cap * x / 100),
                                                               "hint": f"{x}% of the position"} for x in (1, 2, 4)] if cap else []}
        if day and product != "PERP":
            f["exit_at"] = {"label": "Exit by", "unit": "time", "value": mg.get("exit_at"), "removable": True,
                            "choices": [{"value": t, "label": t, "hint": h} for t, h in
                                        (("14:30", ""), ("15:00", ""), ("15:10", ""),
                                         ("15:15", "five minutes before the 15:20 square-off"))]}
        if kind == "options":
            f["on_leg_stop"] = {"label": "When one leg is stopped", "value": mg.get("on_leg_stop"), "removable": True,
                                "choices": [{"value": "exit_all", "label": "Exit every leg", "hint": ""},
                                            {"value": "move_others_to_cost", "label": "Move the others to cost",
                                             "hint": "the rest can no longer lose"}]}
        if product == "PERP":
            cx = hyperliquid.ctx(do["symbol"]) or {}
            mx = cx.get("max_leverage") or 1
            mm = 0.5 / mx
            f["leverage"] = {"label": "Leverage", "unit": "×", "value": lev, "choices": [
                {"value": x, "label": f"{x}×", "hint": f"liquidated about {(1 / x - mm) * 100:.1f}% against you"}
                for x in (1, 2, 3, 5, 10, 20, 25, 40, 50) if x <= mx]}
        if kind in ("buy", "sell") and product in ("CNC", "MIS") and not customsec.is_custom(do.get("symbol", "")):
            f["product"] = {"label": "Holding", "value": product, "choices": [
                {"value": "CNC", "label": "Delivery", "hint": "held overnight"},
                {"value": "MIS", "label": "Intraday", "hint": "squared off at 15:20; stops, trails and limits run in the day book"}]}
        f["authority"] = {"label": "Who places it", "value": c["authority"], "choices": [
            {"value": "propose", "label": "Ask me first", "hint": "a ticket you confirm"},
            {"value": "act", "label": "Place it", "hint": "the first fire still asks once"},
            {"value": "manage", "label": "Place and manage it", "hint": "and runs its exits"}]}
    if c["lifespan"] in ("once_when", "standing"):
        f["lifespan"] = {"label": "Runs", "value": c["lifespan"], "choices": [
            {"value": "once_when", "label": "Once", "hint": "the first time it triggers"},
            {"value": "standing", "label": "Every time", "hint": "re-enters after each exit"}]}
    if spec.get("policy"):
        f["policy.every"] = {"label": "Check", "value": spec["policy"]["every"], "choices": [
            {"value": x, "label": f"Every {x}", "hint": ""} for x in ("day", "week", "month")]}
        f["policy.drift_pct"] = {"label": "Drift", "unit": "pts", "value": spec["policy"]["drift_pct"], "choices": [
            {"value": x, "label": f"{x} points", "hint": ""} for x in (2, 5, 10)]}
    when = spec.get("when") or {}
    status = {i: r for i, r in enumerate((play.get("history") or {}).get("conditions") or [])}
    for i, cnd in enumerate(when.get("all") or when.get("any") or []):
        if "left" not in cnd:
            continue
        if cnd.get("op") in ("above", "below", "cross_up", "cross_down"):
            f[f"cond.{i}.op"] = {"label": "Comparison", "value": cnd["op"], "choices": [
                {"value": o, "label": w, "hint": h} for o, w, h in
                (("above", "is above", "true on every bar it holds"), ("below", "is below", "true on every bar it holds"),
                 ("cross_up", "crosses above", "only the bar it crosses"),
                 ("cross_down", "crosses below", "only the bar it crosses"))]}
        if _is_num(cnd.get("right")):
            now = (status.get(i) or {}).get("now")
            ch = []
            if isinstance(now, (int, float)):
                ch = [{"value": round(now * (1 + x / 100), 2), "label": _fmt_num(now * (1 + x / 100)),
                       "hint": "now" if not x else f"{x:+g}% from now"} for x in (-2, -1, 0, 1, 2)]
            f[f"cond.{i}.right"] = {"label": "Level", "value": float(cnd["right"]), "choices": ch}
        if cnd.get("for_bars"):
            f[f"cond.{i}.for_bars"] = {"label": "Must hold for", "unit": "bars", "value": cnd["for_bars"],
                                       "choices": [{"value": x, "label": f"{x} bars", "hint": ""} for x in (1, 2, 3, 5)]}
    more = [k for k, v in f.items() if v.get("value") in (None, "") and v.get("removable")]
    return {"fields": f, "more": more, "atr_pct": round(atr, 3) if atr else None, "interval": spec["interval"]}


def _is_num(x) -> bool:
    try:
        float(x)
        return True
    except (TypeError, ValueError):
        return False


def resolve_event(uid: int, eid: int, action: str) -> dict:
    with ds._users_lock:
        r = _db().execute("SELECT play_id, kind, body, status, ts FROM play_events "
                          "WHERE id=? AND user_id=?", (eid, uid)).fetchone()
    if not r:
        raise Bad(f"no event {eid}")
    pid, kind, body, status, ts = r[0], r[1], json.loads(r[2] or "{}"), r[3], r[4]
    if kind != "proposed" or status != "open":
        raise Bad("only an open proposal can be confirmed or dismissed")
    if action == "dismiss":
        _event_status(eid, "dismissed")
        _after_proposal(pid)
        return {"event": eid, "status": "dismissed"}
    if action != "confirm":
        raise Bad("action is confirm or dismiss")
    play = _load(pid)
    if not play:
        raise Bad("the play behind this proposal was deleted")
    if play["spec"].get("universe"):
        play = _member(play, body.get("symbol") or (body.get("ticket") or {}).get("symbol") or "")
    h = _halted(uid) if _halts_entries(play["spec"]["do"]) and not body.get("ticket", {}).get("close") \
        and (body.get("ticket") or {}).get("side") != "SELL" else None
    if h:
        raise Bad(h["text"])
    t = dict(body.get("ticket") or {})
    if t.get("book") == "day":
        fresh, why = _day_ticket(play)
        if not fresh:
            raise Bad(why)
        t = fresh
    elif t.get("rebalance"):
        fresh, why = policy.ticket(play, t["targets"], why=t.get("why") or "", source=t.get("source") or "")
        if not fresh:
            raise Bad(why)
        t = fresh
    elif t.get("side") == "BUY":
        fresh, why = _ticket(play, float(t.get("price") or 0))
        if not fresh:
            raise Bad(why)
        t = fresh
    ev = _place(play, t, ts, {"confirmed": eid, "symbol": t.get("symbol")})
    _event_status(eid, "confirmed")
    _set(pid, accepted=play["accepted"] + 1, needs_confirm=0)
    _after_proposal(pid)
    return {"event": eid, "status": "confirmed", "result": ev}


def _event_status(eid: int, status: str) -> None:
    with ds._users_lock:
        _db().execute("UPDATE play_events SET status=? WHERE id=?", (status, eid))
        _db().commit()


def _after_proposal(pid: int) -> None:
    play = _load(pid)
    if not play:
        return
    c = play["spec"]["contract"]
    if c["lifespan"] in ("once_when", "now") and play["fires"] and play["state"] == "armed":
        _entries_over(pid, "fired")


# ══ the Plays page ══════════════════════════════════════════════════════════

def surface_block(ctx: dict) -> str:
    """The context block for the Plays page: no chart, and what the trader
    already has, so "make it 25" can find the play it means."""
    uid = _uid()
    lines = ["## Where the trader is",
             "The Plays page: a conversation for building and adjusting plays. No chart is on "
             "screen, so do not draw; read_chart, market_data and the rest still work for any "
             "symbol. Every play you compile or edit appears in this conversation as its card. "
             "When they ask to change a play (in this conversation, or by naming it), edit it (play "
             "edit with its id and the whole play) rather than making a second one. A new request that "
             "only resembles one of their plays is a new play: build it as asked, and mention the "
             "similar one in a line so they can delete it; never strip or rewrite an existing play "
             "they did not point at. The trader can also change numbers on a card "
             "themselves (size, stop, target, leverage, a condition's value, a weight); the spec below "
             "is the current one.",
             "Answer the way the main chat does: when they ask why a play is built a certain way, "
             "why a stock is in a basket, or whether an idea holds up, reason from data (read it "
             "with the tools: prices, fundamentals, the play's own history and receipts), give a "
             "defended view, use sections and tables where they help, and say what would change "
             "the view. Never a bare restatement of the card."]
    if not uid:
        lines.append("They are signed out: plays need an account, so say they must sign in.")
        return "\n".join(lines)
    init_db()
    rows = list_plays(uid, ("armed", "paused", "draft", "done"))
    focus = ctx.get("focus_play")
    # the latest few, each in a line; the rest by count. This block is sent
    # again on every round of a turn, so it stays short as plays accumulate
    shown = rows[:8]
    if rows:
        lines.append("\n## Their latest plays (id · state · title · what it does)")
        for p in shown:
            lines.append(f"- {p['id']} · {p['state']} · {p['spec']['title']} · {summary(p['spec'])}")
        if len(rows) > len(shown):
            lines.append(f"- and {len(rows) - len(shown)} older ones: play list shows them all")
    try:
        secs = customsec.list_for(uid)[:5]
    except Exception:                                       # noqa: BLE001
        secs = []
    if secs:
        lines.append("\n## Their custom securities")
        for x in secs:
            lines.append(f"- {x['symbol']} · {x['title']} · {customsec.describe(x)}")
        lines.append("(measure list gives each one's method and why every member is in it)")
    if focus:
        p = get(uid, int(focus)) if str(focus).isdigit() else None
        if p:
            lines.append(f"\nThey opened play {p['id']} ({p['spec']['title']}) to work on it: a change "
                         f"they ask for applies to it. Its spec: "
                         + json.dumps(p["spec"], separators=(",", ":"), default=str)[:3000])
    return "\n".join(lines)


# ══ HTTP ════════════════════════════════════════════════════════════════════

def api_list(uid: int) -> tuple[int, dict]:
    init_db()
    plays = list_plays(uid)
    import daybook
    _, book = daybook.api_list(uid)
    names = {p["id"]: p["spec"]["title"] for p in plays}
    for p in book["positions"]:
        p["play"] = names.get(p["play_id"], "")
    evs = events(uid)
    try:
        secs = [customsec.card(x) for x in customsec.list_for(uid)]
    except Exception:                                       # noqa: BLE001
        log.warning("plays: securities did not load", exc_info=True)
        secs = []
    return 200, {"plays": [card(p) for p in plays], "events": evs,
                 "unseen": sum(1 for e in evs if not e["seen"]), "book": book,
                 "securities": secs, "halt": _halted(uid)}


# ── the review: what the trader's own trades say ───────────────────────────
# Numbers only, cut the ways a trading coach would cut them; what they MEAN is
# the model's to say. A trade is a group of legs opened together.

def _exit_kind(why: str) -> str:
    w = (why or "").lower()
    for key, word in (("trailing", "trailing stop"), ("exit rule", "exit rule"), ("halted", "halt"),
                      ("square-off", "square-off"), ("time exit", "time exit"), ("liquidat", "liquidated"),
                      ("past its", "loss limit"), ("lost ₹", "loss limit"), ("made ₹", "profit target"),
                      ("another leg", "leg rule"), ("expired", "expiry"), ("by you", "closed by you"),
                      ("target", "target"), ("stop", "stop")):
        if key in w:
            return word
    return "closed by the play"


def review(uid: int, days: int = 30, play_id: int | None = None) -> dict:
    import daybook
    daybook.init_db()
    since = int(time.time()) - int(days) * 86400
    q = ("SELECT grp, play_id, symbol, underlying, kind, side, entry_ts, exit_ts, pnl, costs, exit_why "
         "FROM day_positions WHERE user_id=? AND state='closed' AND exit_ts >= ?")
    args: list = [uid, since]
    if play_id:
        q += " AND play_id=?"
        args.append(int(play_id))
    with ds._users_lock:
        rows = _db().execute(q, args).fetchall()
        titles = dict(_db().execute("SELECT id, title FROM plays WHERE user_id=?", (uid,)).fetchall())
    groups: dict[str, dict] = {}
    for grp, pid, sym, und, kind, side, ent, ext, pnl, costs, why in rows:
        g = groups.setdefault(grp, {"play_id": pid, "play": titles.get(pid, "") if pid else "",
                                    "what": und or sym, "kind": kind, "legs": 0, "in": ent, "out": ext,
                                    "pnl": 0.0, "costs": 0.0, "why": why})
        g["legs"] += 1
        g["in"], g["out"] = min(g["in"], ent), max(g["out"], ext)
        g["pnl"] += pnl or 0.0
        g["costs"] += costs or 0.0
        if ext >= g["out"]:
            g["why"] = why
    trades = sorted(groups.values(), key=lambda g: g["out"])
    if not trades:
        return {"days": days, "trades": 0,
                "note": "no closed intraday, F&O or perp trades in the day book in this window"}
    pnls = [round(g["pnl"], 2) for g in trades]
    st = _stats(pnls)
    curve, peak, dd = [], 0.0, 0.0
    for v in _cum(pnls):
        curve.append(round(v, 2))
        peak = max(peak, v)
        dd = min(dd, v - peak)

    def cut(key) -> list[dict]:
        out: dict = {}
        for g in trades:
            k = key(g)
            b = out.setdefault(k, {"n": 0, "pnl": 0.0, "wins": 0})
            b["n"] += 1
            b["pnl"] += g["pnl"]
            b["wins"] += g["pnl"] > 0
        return [{"key": k, "n": b["n"], "pnl": round(b["pnl"], 2), "win_rate": round(b["wins"] / b["n"], 2)}
                for k, b in sorted(out.items(), key=lambda x: str(x[0]))]
    ist = lambda t: time.gmtime(t + ds.IST_OFF)                                # noqa: E731
    held = lambda gs: round(sum((g["out"] - g["in"]) / 60 for g in gs) / len(gs), 1) if gs else None  # noqa: E731
    wins = [g for g in trades if g["pnl"] > 0]
    losses = [g for g in trades if g["pnl"] <= 0]
    # a trade opened within 30 minutes of a losing one closing, the same day
    after = [b for a, b in zip(trades, trades[1:])
             if a["pnl"] < 0 and 0 <= b["in"] - a["out"] <= 1800]
    rest = [g for g in trades if g not in after]
    gross = sum(g["pnl"] + g["costs"] for g in trades)
    costs = sum(g["costs"] for g in trades)
    by_day = cut(lambda g: time.strftime("%Y-%m-%d", ist(g["in"])))
    return {
        "days": days, "trades": len(trades), **st, "net": round(sum(pnls), 2), "gross": round(gross, 2),
        "costs": round(costs, 2), "max_drawdown": round(dd, 2), "curve": curve[-200:],
        "minutes_held": {"wins": held(wins), "losses": held(losses)},
        "after_a_loss": {"n": len(after), **({k: v for k, v in _stats([g["pnl"] for g in after]).items()
                                               if k in ("win_rate", "expectancy")} if after else {}),
                         "others": {k: v for k, v in _stats([g["pnl"] for g in rest]).items()
                                    if k in ("win_rate", "expectancy")}},
        "by_hour": cut(lambda g: f"{ist(g['in']).tm_hour:02d}:00"),
        "by_weekday": cut(lambda g: time.strftime("%a", ist(g["in"]))),
        "by_play": cut(lambda g: g["play"] or "placed by hand"),
        "by_exit": cut(lambda g: _exit_kind(g["why"])),
        "by_instrument": cut(lambda g: g["kind"]),
        "by_day": by_day[-60:],
        "green_days": sum(1 for d in by_day if d["pnl"] > 0), "trading_days": len(by_day),
        "recent": [{"what": g["what"], "play": g["play"], "in": g["in"], "out": g["out"],
                    "pnl": round(g["pnl"], 2), "exit": _exit_kind(g["why"])} for g in trades[-15:]],
    }


# ── the Plays page's conversations ─────────────────────────────────────────
CHAT_KEEP, CHAT_TURNS, CHAT_CHARS = 200, 120, 20_000


def _card_ref(c: dict) -> dict | None:
    """A card is kept as a reference and drawn from the live play or security
    when the conversation is opened again, so an old thread never shows a
    play as it was rather than as it is."""
    if not isinstance(c, dict):
        return None
    if c.get("kind") == "play" and c.get("id"):
        return {"kind": "play", "id": int(c["id"]), "title": str(c.get("title") or "")[:80]}
    if c.get("kind") == "custom_security" and c.get("symbol"):
        return {"kind": "custom_security", "symbol": str(c["symbol"])[:30]}
    if c.get("kind") == "review":
        return dict(c)          # a review is of its moment: kept as it was read
    return None


def chats_list(uid: int) -> list[dict]:
    init_db()
    with ds._users_lock:
        rows = _db().execute("SELECT id, title, focus, updated, turns FROM play_chats WHERE user_id=? "
                             "ORDER BY updated DESC LIMIT ?", (uid, CHAT_KEEP)).fetchall()
    out = []
    for cid, title, focus, updated, turns in rows:
        t = json.loads(turns or "[]")
        last = next((x.get("content") or "" for x in reversed(t) if x.get("role") == "assistant"), "")
        out.append({"id": cid, "title": title, "focus": focus, "updated": updated, "n_turns": len(t),
                    "last": re.sub(r"\[\[panel[^\]]*\]\]|[*#|`]", "", last)[:160]})
    return out


def chat_get(uid: int, cid: str) -> dict | None:
    init_db()
    with ds._users_lock:
        r = _db().execute("SELECT id, title, focus, turns, created, updated FROM play_chats "
                          "WHERE user_id=? AND id=?", (uid, cid)).fetchone()
    if not r:
        return None
    return {"id": r[0], "title": r[1], "focus": r[2], "turns": json.loads(r[3] or "[]"),
            "created": r[4], "updated": r[5]}


def chat_save(uid: int, body: dict) -> dict:
    """Upsert one conversation as the page holds it. Cards are stored as
    references; the text is also mirrored into the account's conversation
    archive so the main chat can be asked about it later."""
    init_db()
    cid = re.sub(r"[^A-Za-z0-9_-]", "", str(body.get("id") or ""))[:64]
    if not cid:
        raise Bad("a conversation needs an id")
    turns = []
    for t in (body.get("turns") or [])[-CHAT_TURNS:]:
        if not isinstance(t, dict) or t.get("role") not in ("user", "assistant"):
            continue
        turns.append({"role": t["role"], "content": str(t.get("content") or "")[:CHAT_CHARS],
                      "steps": [{"name": str(x.get("name") or "")[:40], "hint": str(x.get("hint") or "")[:120]}
                                for x in (t.get("steps") or [])[:20] if isinstance(x, dict)],
                      "meta": str(t.get("meta") or "")[:200],
                      "cards": [r for r in (_card_ref(c) for c in t.get("cards") or []) if r]})
    if not turns:
        raise Bad("an empty conversation is not saved")
    now = int(time.time())
    focus = body.get("focus")
    focus = int(focus) if str(focus or "").isdigit() else None
    with ds._users_lock:
        _db().execute(
            "INSERT INTO play_chats (id, user_id, title, focus, turns, created, updated) VALUES (?,?,?,?,?,?,?) "
            "ON CONFLICT(user_id, id) DO UPDATE SET title=excluded.title, focus=excluded.focus, "
            "turns=excluded.turns, updated=excluded.updated",
            (cid, uid, str(body.get("title") or "")[:120], focus, json.dumps(turns), now, now))
        _db().execute("DELETE FROM play_chats WHERE user_id=? AND id NOT IN (SELECT id FROM play_chats "
                      "WHERE user_id=? ORDER BY updated DESC LIMIT ?)", (uid, uid, CHAT_KEEP))
        _db().commit()
    try:
        ds._conv_sync(uid, [{"id": f"plays-{cid}", "title": body.get("title") or "",
                             "turns": [{"role": t["role"], "content": t["content"]} for t in turns]}])
    except Exception:                                       # noqa: BLE001
        log.warning("plays: conversation mirror failed", exc_info=True)
    return {"id": cid, "updated": now, "turns": len(turns)}


def chat_delete(uid: int, cid: str) -> dict:
    init_db()
    with ds._users_lock:
        n = _db().execute("DELETE FROM play_chats WHERE user_id=? AND id=?", (uid, cid)).rowcount
        _db().commit()
    return {"deleted": bool(n)}


def api_get(uid: int, tail: str) -> tuple[int, dict]:
    init_db()
    parts = tail.strip("/").split("/")
    if parts == ["chats"]:
        return 200, {"chats": chats_list(uid)}
    if parts == ["review"]:
        return 200, review(uid)
    if len(parts) == 2 and parts[0] == "chats":
        c = chat_get(uid, parts[1])
        return (200, {"chat": c}) if c else (404, {"error": "no such conversation"})
    if len(parts) == 2 and parts[0].isdigit() and parts[1] == "options":
        try:
            return 200, options(uid, int(parts[0]))
        except Bad as exc:
            return 404, {"error": str(exc)}
    pid = tail.strip("/")
    if not pid.isdigit():
        return 404, {"error": f"no plays route '{tail}'"}
    p = get(uid, int(pid))
    if not p:
        return 404, {"error": f"no play {pid}"}
    return 200, {"play": card(p), "events": [e for e in events(uid, 300) if e["play_id"] == p["id"]][:40]}


def api_post(uid: int, tail: str, body: dict) -> tuple[int, dict]:
    init_db()
    try:
        parts = tail.strip("/").split("/")
        if len(parts) == 1 and parts[0].isdigit():
            if body.get("action") == "set":
                return 200, edit_field(uid, int(parts[0]), str(body.get("field") or ""), body.get("value"))
            return 200, act(uid, int(parts[0]), str(body.get("action") or ""))
        if parts == ["chats"]:
            return 200, chat_save(uid, body)
        if parts == ["halt"] and body.get("action") == "lift":
            return 200, lift_halt(uid)
        if len(parts) == 2 and parts[0] == "chats" and body.get("action") == "delete":
            return 200, chat_delete(uid, parts[1])
        if parts == ["securities"]:
            try:
                sec = customsec.edit(uid, str(body.get("symbol") or ""), body)
            except customsec.Bad as exc:
                return 400, {"error": str(exc)}
            return 200, {"security": customsec.card(sec)}
        if len(parts) == 2 and parts[0] == "events" and parts[1].isdigit():
            return 200, resolve_event(uid, int(parts[1]), str(body.get("action") or ""))
        if len(parts) == 2 and parts[0] == "positions" and parts[1].isdigit():
            import daybook
            return daybook.api_close(uid, int(parts[1]))
        if parts == ["seen"]:
            with ds._users_lock:
                _db().execute("UPDATE play_events SET seen=1 WHERE user_id=?", (uid,))
                _db().commit()
            return 200, {"ok": True}
        return 404, {"error": f"no plays route '{tail}'"}
    except Bad as exc:
        return 400, {"error": str(exc)}


# ══ the chat tools ══════════════════════════════════════════════════════════

def _uid() -> int | None:
    u = getattr(ds._req, "user", None)
    return u[0] if u else None


def tool_play(action: str = "", play: dict | str | None = None, play_id: int = 0,
              arm: bool = True) -> dict:
    uid = _uid()
    if not uid:
        return {"error": "plays need an account",
                "_note": "Say the trader must sign in: plays run on the server so they can fire "
                         "while the browser is closed."}
    init_db()
    if isinstance(play, str):
        try:
            play = json.loads(play)
        except ValueError:
            return {"error": "play must be a JSON object"}
    try:
        if action in ("compile", "edit"):
            if action == "edit" and not play_id:
                return {"error": "edit needs play_id"}
            no_chart = getattr(ds._req, "surface", "") == "plays"
            play = dict(play or {})
            built = None
            if isinstance(play.get("security"), dict):
                # one call builds the trader's security and the play on it: the
                # play needs the security's symbol, so as two tools they could
                # only run one after the other, a model round apart
                try:
                    sec = customsec.define(uid, play.pop("security"))
                except customsec.Bad as exc:
                    return {"error": f"security: {exc}", "_note": "Fix the security as the error says and call again."}
                built = customsec.card(sec)
                ds._card_add(built)
                for k in ("symbol",):
                    if not play.get(k) or str(play[k]).upper().lstrip("@") == sec["symbol"].lstrip("@"):
                        play[k] = sec["symbol"]
                if play.get("do") and str((play["do"] or {}).get("symbol") or "").upper().lstrip("@") == \
                        sec["symbol"].lstrip("@"):
                    play["do"] = {**play["do"], "symbol": sec["symbol"]}
            got = compile_play(uid, play,
                               chart_symbol="" if no_chart else getattr(ds._req, "symbol", ""),
                               chart_interval="" if no_chart else getattr(ds._req, "ctx_interval", ""),
                               arm=arm, pid=play_id or None)
            ds._card_add(got["card"])
            row = got["play"]
            c = got["card"]
            hist = dict(c["history"])
            if hist.get("trades"):
                # the card draws the curve and the list; the model reads the figures
                tr = {k: v for k, v in hist["trades"].items() if k not in ("curve", "list")}
                tr["last"] = (hist["trades"].get("list") or [])[-3:]
                hist["trades"] = tr
            out = {"play_id": row["id"], "state": row["state"], "title": c["title"],
                   "conditions": c["conditions"], "contract": c["contract"],
                   "history": hist, "now": row["history"].get("now"),
                   "assumed": got["assumed"], "notes": c["notes"]}
            if c.get("exits"):
                out["exits"] = c["exits"]
            if got["diff"]:
                out["changed"] = got["diff"]
            if built:
                out["security"] = {k: built[k] for k in ("symbol", "title", "formula", "now", "change",
                                                         "members", "method", "why")}
            if row.get("_now_event"):
                out["did_now"] = {k: row["_now_event"].get(k) for k in ("kind", "body")}
            fs = got.get("feed") if isinstance(got.get("feed"), dict) else {}
            if fs.get("note") and row["spec"]["do"]["kind"] != "halt":
                out["feed"] = (fs["note"] if not row["spec"].get("on") else
                               "Filings and headlines are read live. The price condition reads the "
                               "latest stored bar, and this symbol is not on a live stream, so that "
                               "bar may be old.")
            if row["spec"]["do"]["kind"] == "rebalance":
                out["targets"] = row["spec"]["do"].get("targets")
                out["reasons"] = row["spec"]["do"].get("reasons") or {}
                out["_note"] = ("The play is on the trader's screen as a card; do not repeat it line by "
                                "line, explain it. Give the targets as a table with the reason for each "
                                "weight (from reasons, or the figures you read), then, if it ran now, the "
                                "trades from did_now (whole shares, cash left over) and what that does to "
                                "concentration. A rebalance has no historical fire count: do not quote "
                                "history. For a policy, say when it runs (schedule and drift) and why that "
                                "cadence suits it. Say every item in `assumed` as a choice they can "
                                "change. If it ran once, say the card can keep it that way.")
            elif row["spec"].get("on"):
                out["_note"] = ("The play is on the trader's screen as a card; explain it rather than "
                                "repeat it: what it reads, why those words and that judge question catch "
                                "the event the trader means (and what they would miss), and what it does "
                                "when one arrives. Filings and headlines are polled live whatever the "
                                "symbol's stream. For history, quote the note about how many stored items "
                                "match its words (not bars). Say every item in `assumed` as a choice they "
                                "can change.")
            else:
                out["_note"] = ("The play is on the trader's screen as a card; explain it rather than "
                                "repeat it. Say what it watches and why each condition is there (the "
                                "logic of the setup, and what the values in `now` say about where it "
                                "stands today), how often it would have fired on history (history.fires "
                                "over history.bars bars, with the dates it covers) and what that rate "
                                "means for how often they will hear from it, what it does when it fires "
                                "and how the exits limit the loss, and every item in `assumed` as a "
                                "choice they can change. A fire count is not an edge: do not call it "
                                "one. When history.trades is there, give its round trips against the "
                                "control (win rate, average win and loss, expectancy, after costs when "
                                "given) and say plainly whether the play beat entering anywhere; with "
                                "few trades, say it proves nothing. For an option play it is the "
                                "underlying's move, not the premium. Quote only numbers from this "
                                "result or other tools.")
                if row["spec"]["do"]["kind"] == "halt":
                    out["_note"] = ("A guard is on the trader's screen as a card. Say what stops the day, "
                                    "what it blocks (new intraday, F&O and perp entries; exits always "
                                    "run), when it resets (the next morning), and that the card can lift "
                                    "it. Quote only numbers from this result.")
            return out
        if action == "list":
            out = {"plays": [{k: c[k] for k in ("id", "title", "state", "symbol", "interval",
                                                "contract", "fires")}
                             for c in (card(p) for p in list_plays(uid))]}
            h = _halted(uid)
            return {**out, "halt": h} if h else out
        if action == "review":
            r = review(uid, int((play or {}).get("days") or 30) if isinstance(play, dict) else 30,
                       play_id or None)
            ds._card_add({"kind": "review", **r})
            r.pop("curve", None)
            r["_note"] = ("Their own trades, cut several ways; a review card with the curve is on screen. "
                          "Read it like a coach who has seen the numbers: where the money is made and "
                          "lost (hours, plays, exits, instruments), whether losers are held longer than "
                          "winners, how trades opened soon after a loss do against the rest, what costs "
                          "take out of the gross, and the one or two changes the numbers support, each as "
                          "a play or guard you can build for them. Quote only these numbers; with few "
                          "trades, say the sample is small.")
            return r
        if action in ("pause", "resume", "delete"):
            return act(uid, int(play_id), action)
        if action == "explain":
            p = get(uid, int(play_id))
            if not p:
                return {"error": f"no play {play_id}"}
            evs = [e for e in events(uid, 200) if e["play_id"] == p["id"]][:10]
            return {"play": card(p), "events": evs, "spec": p["spec"]}
        return {"error": "action is compile, edit, list, pause, resume, delete, explain or review"}
    except Bad as exc:
        return {"error": str(exc), "_note": "Fix the play as the error says and call again."}


def tool_measure(action: str = "test", measure: dict | str | None = None,
                 symbol: str = "", interval: str = "", security: dict | str | None = None,
                 scan: dict | str | None = None) -> dict:
    uid = _uid()
    if isinstance(measure, str):
        try:
            measure = json.loads(measure)
        except ValueError:
            return {"error": "measure must be a JSON object"}
    if action == "scan":
        import scan as scanner
        try:
            raw = json.loads(scan) if isinstance(scan, str) else dict(scan or {})
            r = scanner.run(raw, uid)
        except (scanner.Bad, Bad, ValueError) as exc:
            return {"error": str(exc), "_note": "Fix the scan as the error says and call again."}
        ds._card_add({"kind": "scan", **r})
        return {**r, "_note": ("The shortlist is on screen as a table. If the summary shows a measure "
                               "off by a sign or a unit, or `passes` shows one clause leaving almost "
                               "nothing, fix the measure and scan again before answering. Say what was "
                               "scanned (universe, "
                               "with_data, left_out), the clause and the ranking in words, then the names "
                               "that stand out and why, from their own values, and how they sit against "
                               "the summary. A scan is a list to look at, not a call to buy. To watch "
                               "them, a play takes universe: the symbols.")}
    try:
        if action == "list":
            return {"measures": [{k: m.get(k) for k in ("name", "title", "formula")}
                                 for m in (list_measures(uid) if uid else [])],
                    "securities": [{"symbol": x["symbol"], "title": x["title"],
                                    "formula": customsec.describe(x), "why": x.get("why") or "",
                                    "method": (x.get("method") or {}).get("text"),
                                    "members": [{"symbol": m["symbol"], "weight": round(m["weight"], 4),
                                                 "why": m.get("why")} for m in x["members"]]}
                                   for x in (customsec.list_for(uid) if uid else [])]}
        if action == "security":
            if not uid:
                return {"error": "a custom security needs an account"}
            try:
                sec = customsec.define(uid, security if isinstance(security, dict) else
                                       json.loads(security or "{}"))
            except (customsec.Bad, ValueError) as exc:
                return {"error": str(exc), "_note": "Fix the security as the error says and call again."}
            c = customsec.card(sec)
            ds._card_add(c)
            return {**{k: c[k] for k in ("symbol", "title", "formula", "now", "change", "members",
                                          "method", "why")},
                    "anchor_day": sec.get("anchor"), "bars_daily": len(c["spark"]),
                    "_note": (f"{sec['symbol']} is now a symbol. Explain how it was built: the method "
                              "(method.text, when it was picked) and a table of the members with their "
                              "weights and each one's why, who was left out and why, and what the "
                              "change figures say about how the group has moved. Its card has an Open "
                              "chart button; nothing has been opened, so say the button is there, not "
                              f"that the chart is open. A play can watch it "
                              f"(symbol {sec['symbol']}) or, for a basket, buy and sell it whole "
                              f"(do.symbol {sec['symbol']}). A basket starts at {sec.get('base') or 100:g} "
                              "on its anchor day and holds its weights from there. Quote only these "
                              "numbers.")}
        m = _norm_measure(measure or {}, uid)
        sym = ds.canon_symbol(symbol or getattr(ds._req, "symbol", "") or "")
        iv = interval or getattr(ds._req, "ctx_interval", "") or "1d"
        iv = {"D": "1d", "1D": "1d"}.get(iv, iv)
        if iv not in INTERVALS:
            return {"error": f"interval must be one of {', '.join(INTERVALS)}"}
        spec = {"symbol": sym, "interval": iv, "measures": [m]}
        data, notes = frame(spec)
        series, problems = run_measures(spec, data, check=True)
        s = series[m["name"]]
        vals = [x for x in s if x is not None]
        out = {"name": m["name"], "formula": m["formula"], "symbol": sym, "interval": iv,
               "now": s[-1], "last": [round(x, 6) if x is not None else None for x in s[-5:]],
               "bars": len(s), "with_value": len(vals),
               "range": [min(vals), max(vals)] if vals else None,
               "problems": notes + problems}
        if action == "define":
            if not uid:
                return {**out, "error": "saving a measure needs an account"}
            init_db()
            save_measure(uid, m)
            out["saved"] = True
        return out
    except Bad as exc:
        return {"error": str(exc)}
