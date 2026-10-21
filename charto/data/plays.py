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
import queue
import re
import sqlite3
import threading
import time
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN

log = logging.getLogger("plays")

ds = None            # dataserver, bound by bind()
_alerts = None       # the alerts module: its SSE stream carries play events too
_paper = None


def bind(module, alerts_mod=None, paper_mod=None) -> None:
    global ds, _alerts, _paper
    ds, _alerts, _paper = module, alerts_mod, paper_mod


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
_NAME = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
_HHMM = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
_SQL_SECONDS = 6.0
_BANNED_SQL = re.compile(r"\b(attach|detach|pragma|insert|update|delete|replace|create|drop|"
                         r"alter|vacuum|reindex|analyze)\b", re.I)
_RESERVED = set(FIELDS) | {"t", "hhmm", "data", "params", "ta", "math"}


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


def _norm_measure(m: dict) -> dict:
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
        elif "sql" in src:
            s = str(src["sql"]).strip().rstrip(";")
            if ";" in s or not re.match(r"^\s*(select|with)\b", s, re.I) or _BANNED_SQL.search(s):
                raise Bad(f"measure {name}: input {alias} must be one read-only SELECT")
            inputs[a] = {"sql": s, "refresh": str(src.get("refresh") or "1d")}
        else:
            raise Bad(f"measure {name}: input {alias} needs bars or sql")
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

def _rows(symbol: str, interval: str, limit: int, closed_only: bool = True) -> list[tuple]:
    if hasattr(ds, "_symbol_ready") and not ds._symbol_ready(symbol):
        ds._ensure_symbol(symbol)
    d = ds.get_bars(symbol, interval, None, limit + 1)
    rows = [(b["t"], b["o"], b["h"], b["l"], b["c"], b["v"]) for b in d.get("bars") or []]
    if closed_only and rows and ds._live_view(symbol) is not None:
        rows = rows[:-1]           # the bar still forming is not a fact yet
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


def frame(spec: dict, limit: int | None = None) -> tuple[dict, list[str]]:
    """Everything a play's measures read, on the clock symbol's closed bars.
    Returns (data, notes). Every series has one value per clock bar."""
    sym, iv = spec["symbol"], spec["interval"]
    rows = _rows(sym, iv, limit or LOOKBACK[iv])
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


def normalise(raw: dict, uid: int, chart_symbol: str = "", chart_interval: str = "") -> tuple[dict, list[str]]:
    """The model's play, checked and filled in. Returns (spec, assumptions):
    every default this function chose is listed, so the reply can say it."""
    if not isinstance(raw, dict):
        raise Bad("play must be an object")
    said: list[str] = []
    sym = ds.canon_symbol(str(raw.get("symbol") or chart_symbol or ""))
    if not sym:
        raise Bad("play needs a symbol (the instrument whose bars it watches)")
    iv = str(raw.get("interval") or chart_interval or "5m")
    iv = {"D": "1d", "1D": "1d", "day": "1d", "daily": "1d", "60m": "1h"}.get(iv, iv)
    if iv not in INTERVALS:
        raise Bad(f"interval must be one of {', '.join(INTERVALS)}")
    if not raw.get("interval"):
        said.append(f"checks on each {iv} bar close")

    measures, seen = [], set()
    lib = {m["name"]: m for m in list_measures(uid)} if uid else {}
    for m in raw.get("measures") or []:
        nm = _norm_measure(m)
        if nm["name"] in seen:
            raise Bad(f"two measures are named {nm['name']}")
        seen.add(nm["name"])
        measures.append(nm)

    when = raw.get("when") or None
    if isinstance(when, list):
        when = {"all": when}
    if when and "left" in when or (when and "time" in when):
        when = {"all": [when]}
    # operands naming a saved measure pull it in; "SYMBOL.field" becomes an input
    for op in _operands(when):
        if not isinstance(op, str):
            continue
        key = _clean_name(op)
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
    lifespan = contract.get("lifespan") or ("once_when" if (when or on) else "now")
    if lifespan not in LIFESPANS:
        raise Bad(f"contract.lifespan must be one of {', '.join(LIFESPANS)}")
    if lifespan != "now" and not when and not on:
        raise Bad("this play has no `when` and no `on`; give conditions or events, or set "
                  "contract.lifespan to now")
    authority = contract.get("authority") or "notify"
    if authority not in AUTHORITIES:
        raise Bad(f"contract.authority must be one of {', '.join(AUTHORITIES)}")
    discretion = contract.get("discretion") or "none"
    if discretion not in DISCRETIONS:
        raise Bad(f"contract.discretion must be one of {', '.join(DISCRETIONS)}")
    if discretion != "none":
        said.append(f"{discretion} discretion is not wired yet, so it runs as a fixed rule")
        discretion = "none"
    if lifespan == "policy":
        said.append("standing policies with a planner are not wired yet, so it runs every time "
                    "the condition is met")
        lifespan = "standing"
    if not contract.get("authority"):
        said.append("it tells you when it fires and places nothing")

    do = dict(raw.get("do") or {})
    kind = do.get("kind") or ("notify" if authority == "notify" else "buy")
    if kind not in ("notify", "buy", "sell"):
        raise Bad("do.kind must be notify, buy or sell")
    if kind == "notify" and authority != "notify":
        authority = "notify"
    if kind != "notify":
        do["symbol"] = ds.canon_symbol(str(do.get("symbol") or sym))
        size = do.get("size") or {}
        if not isinstance(size, dict):
            size = {"inr": size}
        if not size.get("inr") and not size.get("qty") and not size.get("all"):
            size = {"inr": DEFAULT_SIZE_INR} if kind == "buy" else {"all": True}
            said.append(f"sized at ₹{DEFAULT_SIZE_INR:,}" if kind == "buy"
                        else "sells what this play holds")
        do["size"] = size
        if authority == "notify":
            authority = "propose"
            said.append("it proposes the order for you to confirm")
    do["kind"] = kind

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

    cap = float((raw.get("mandate") or {}).get("capital_inr")
                or (do.get("size", {}).get("inr") or 0) * (1 if lifespan != "standing" else 5)
                or DEFAULT_SIZE_INR)
    mandate = {"capital_inr": cap,
               "max_order_inr": float((raw.get("mandate") or {}).get("max_order_inr") or cap),
               "max_fires": int((raw.get("mandate") or {}).get("max_fires")
                                or (1 if lifespan in ("now", "once_when") else 20))}
    days = contract.get("expires_in_days")
    spec = {
        "title": str(raw.get("title") or "")[:80] or f"{sym} play",
        "ask": str(raw.get("ask") or "")[:600],
        "symbol": sym, "interval": iv, "measures": measures, "when": when, "on": on,
        "do": do, "manage": manage, "mandate": mandate,
        "contract": {"lifespan": lifespan, "authority": authority, "discretion": discretion,
                     "inferred_from": str(contract.get("inferred_from") or "")[:200],
                     "expires_in_days": int(days) if days else None},
    }
    return spec, said


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


def replay(spec: dict, check: bool = True) -> dict:
    """The play over stored history: the same measures and the same condition
    code the live bar runs. What the trader sees before it is armed."""
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
    if spec.get("on"):
        return out, data, series, tr
    if tr and not pts:
        out["notes"].append("it would not have fired once on the stored history; check the levels")
    elif out["per_month"] > 40 and spec["interval"] != "1m":
        out["notes"].append(f"it would have fired about {out['per_month']:.0f} times a month; "
                            f"that is close to every day")
    return out, data, series, tr


def _status(spec: dict, data: dict, series: dict) -> list[dict]:
    """Each top-level condition's state on the latest bar, Instinct-style:
    met, or how far away."""
    rows = []
    when = spec.get("when") or {}
    conds = when.get("all") or when.get("any") or []
    for c in conds:
        row = {"text": describe(c, spec)}
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
    names = {m["name"]: m.get("title") or m["name"] for m in spec.get("measures") or []}

    def name(x):
        if isinstance(x, (int, float)):
            return _fmt_num(float(x))
        k = _clean_name(x)
        if k in names:
            return names[k]
        return f"{spec['symbol']} {k}" if k in FIELDS else str(x)
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
    out = {"runs": runs, "acts": acts}
    if spec["do"]["kind"] != "notify":
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


def summary(spec: dict) -> str:
    """One sentence for the whole play, generated from the tree (Catalyst
    shows the same line beside its graph): what it checks, how often, what it
    does and how many times."""
    iv = spec["interval"]
    every = "each daily close" if iv == "1d" else f"every {iv} bar close"
    c, do = spec["contract"], spec["do"]
    when = spec.get("when")
    cond = describe(when, spec) if when else ""
    if do["kind"] == "notify":
        act = "tells you"
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
    if do["kind"] != "notify":
        size = do.get("size") or {}
        action = {"side": do["kind"], "symbol": do["symbol"],
                  "size": (f"₹{size['inr']:,.0f}" if size.get("inr") else
                           f"{size['qty']:g} qty" if size.get("qty") else "what it holds")}
    exits = []
    mg = spec.get("manage") or {}
    if mg.get("stop_pct"):
        exits.append(f"stop {mg['stop_pct']:g}% below entry")
    if mg.get("target_pct"):
        exits.append(f"target {mg['target_pct']:g}% above entry")
    if mg.get("exit_at"):
        exits.append(f"exit at {mg['exit_at']}")
    when = spec.get("when") or {}
    return {
        "kind": "play", "id": row["id"], "title": spec["title"], "state": row["state"],
        "why": row.get("why") or "", "symbol": spec["symbol"], "interval": spec["interval"],
        "join": "any" if "any" in when else "all", "summary": summary(spec),
        "on": ({"reads": _on_words(spec["on"]), "test": _on_test(spec["on"]),
                "recent": hist.get("events") or []} if spec.get("on") else None),
        "conditions": (live or {}).get("conditions") or hist.get("conditions") or
                      [{"text": describe(c, spec)} for c in (when.get("all") or when.get("any") or [])],
        "measures": [{"name": m["name"], "title": m["title"], "formula": m["formula"],
                      "now": (hist.get("now") or {}).get(m["name"]),
                      "spark": (hist.get("spark") or {}).get(m["name"])}
                     for m in spec.get("measures") or [] if not m["name"].endswith(tuple(f"_{f}" for f in FIELDS))
                     or m.get("code")],
        "action": action, "exits": exits, "contract": _contract_line(spec),
        "history": {k: hist.get(k) for k in ("fires", "per_month", "last_fire", "bars", "from", "to")},
        "notes": hist.get("notes") or [], "fires": row.get("fires", 0),
        "expires": row.get("expires"),
    }


# ══ persistence ═════════════════════════════════════════════════════════════

_COLS = ("id", "user_id", "title", "ask", "spec", "symbol", "interval", "symbols", "state", "why",
         "created", "updated", "expires", "fires", "accepted", "spent_inr", "held_qty",
         "held_sym", "last_eval_ts", "needs_confirm", "history")


def _row(t: tuple) -> dict:
    r = dict(zip(_COLS, t))
    r["spec"] = json.loads(r["spec"])
    r["history"] = json.loads(r["history"] or "{}")
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
    out = {spec["symbol"]}
    for m in spec.get("measures") or []:
        for src in m["inputs"].values():
            if "bars" in src:
                out.add(src["bars"]["symbol"])
    if spec["do"].get("symbol"):
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
    return [{"id": r[0], "play_id": r[1], "play": r[2] or "", "ts": r[3], "created": r[4],
             "kind": r[5], "title": r[6], "body": json.loads(r[7] or "{}"), "status": r[8],
             "seen": bool(r[9])} for r in rows]


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
        rows = _db().execute("SELECT id, symbol FROM plays WHERE state='armed'").fetchall()
    idx: dict[str, list[int]] = {}
    for pid, sym in rows:
        idx.setdefault(sym, []).append(pid)
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


def _due(interval: str, minute_ts: int) -> bool:
    """Has this play's bar just closed? Intraday: the closing minute ends on
    the interval's boundary. Daily: checked every 15 minutes, and the closed-
    bar watermark decides."""
    if interval == "1d":
        return (minute_ts // 60) % 15 == 0
    step = INTERVALS[interval] * 60
    return ((minute_ts + 60 + ds.IST_OFF - 15 * 60) % step == 0) or step == 60


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
                _tick(pid, ts)
            except Exception:                               # noqa: BLE001
                STATS["errors"] += 1
                log.warning("plays: play %s failed", pid, exc_info=True)


def _load(pid: int) -> dict | None:
    with ds._users_lock:
        t = _db().execute(f"SELECT {', '.join(_COLS)} FROM plays WHERE id=?", (pid,)).fetchone()
    return _row(t) if t else None


def _tick(pid: int, minute_ts: int | None = None, force: bool = False) -> list[dict]:
    play = _load(pid)
    if not play or play["state"] != "armed":
        return []
    spec = play["spec"]
    if minute_ts is not None and not force and not _due(spec["interval"], minute_ts):
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
    for i in new:
        late = i != len(t) - 1
        if play["why"] == EXITS_ONLY and not play["held_qty"]:
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
    _set(pid, last_eval_ts=t[-1])
    return out


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
    if do["kind"] == "notify" or authority == "notify":
        ev = _event(play, ts, "fired", title, {**body, "reason": reason}, status="done")
    else:
        ticket, why = _ticket(play, price)
        if not ticket:
            ev = _event(play, ts, "rejected", title, {**body, "reason": why}, status="done")
        elif authority == "propose":
            ev = _event(play, ts, "proposed", title, {**body, "ticket": ticket, "reason": reason})
        else:
            ev = _place(play, ticket, ts, body)
    if c["lifespan"] in ("once_when", "now") or fires >= spec["mandate"]["max_fires"]:
        _entries_over(play["id"], "fired" if c["lifespan"] in ("once_when", "now")
                      else "reached its fire limit", waiting=bool(ev and ev["kind"] == "proposed"))
    return ev


def _entries_over(pid: int, why: str, waiting: bool = False) -> None:
    """No more entries. A play that still holds shares and has a time exit
    stays armed to run it (its stop and target already rest in the book); a
    proposal still waiting keeps it open until it is answered."""
    play = _load(pid)
    exits = bool((play["spec"].get("manage") or {}).get("exit_at")) and play["held_qty"] > 0
    if exits or waiting:
        _set(pid, why=EXITS_ONLY if exits else why)
        return
    _set(pid, state="done", why=why)
    _load_index()


EXITS_ONLY = "entries done; watching its exit"


def _ticket(play: dict, price: float) -> tuple[dict | None, str]:
    """The order a fire would place, sized and checked against the mandate by
    code. The model chose rupees; this turns them into a quantity."""
    spec, do, m = play["spec"], play["spec"]["do"], play["spec"]["mandate"]
    sym = do["symbol"]
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


def _place(play: dict, ticket: dict, ts: int, body: dict) -> dict:
    if _paper is None:
        return _event(play, ts, "rejected", play["spec"]["title"],
                      {**body, "reason": "the paper book is not running"}, status="done")
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
    if ticket["side"] == "BUY":
        _set(play["id"], held_qty=play["held_qty"] + ticket["qty"], held_sym=ticket["symbol"],
             spent_inr=play["spent_inr"] + ticket["qty"] * px)
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
        import play_events
        play_events.bind(ds)
        play_events.start(on_items)
    except Exception:                                       # noqa: BLE001
        log.warning("plays: the event poller did not start", exc_info=True)
    with _INDEX_LOCK:
        return {"armed": sum(len(v) for v in _INDEX.values()), "symbols": len(_INDEX)}


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
    hist, data, series, tr = replay(spec)
    status = _status(spec, data, series)
    hist["conditions"] = status
    diff = _diff(prior["spec"], spec) if prior else None
    structural = bool(diff and any(d["what"] != "limits" for d in diff))
    state = "armed" if arm else "draft"
    # arm from the latest closed bar: history is the replay's, not the runtime's
    pid = _save(uid, spec, hist, state=state, pid=pid, last_eval_ts=data["t"][-1],
                needs_confirm=bool(prior and structural and prior.get("fires")))
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
            feed = _alerts.ensure_feed(spec["symbol"])
        except Exception:                                   # noqa: BLE001
            feed = None
    return {"play": row, "assumed": said, "diff": diff, "feed": feed,
            "card": card(row, {"conditions": status})}


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
    elif action == "delete":
        with ds._users_lock:
            _db().execute("DELETE FROM plays WHERE id=? AND user_id=?", (pid, uid))
            _db().commit()
        _load_index()
        return {"deleted": pid}
    else:
        raise Bad("action is pause, resume or delete")
    _load_index()
    return {"play": card(get(uid, pid))}


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
    t = dict(body.get("ticket") or {})
    if t.get("side") == "BUY":
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


# ══ HTTP ════════════════════════════════════════════════════════════════════

def api_list(uid: int) -> tuple[int, dict]:
    init_db()
    plays = list_plays(uid)
    return 200, {"plays": [card(p) for p in plays], "events": events(uid),
                 "unseen": sum(1 for e in events(uid) if not e["seen"])}


def api_post(uid: int, tail: str, body: dict) -> tuple[int, dict]:
    init_db()
    try:
        parts = tail.strip("/").split("/")
        if len(parts) == 1 and parts[0].isdigit():
            return 200, act(uid, int(parts[0]), str(body.get("action") or ""))
        if len(parts) == 2 and parts[0] == "events" and parts[1].isdigit():
            return 200, resolve_event(uid, int(parts[1]), str(body.get("action") or ""))
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
            got = compile_play(uid, play or {}, chart_symbol=getattr(ds._req, "symbol", ""),
                               chart_interval=getattr(ds._req, "ctx_interval", ""),
                               arm=arm, pid=play_id or None)
            ds._card_add(got["card"])
            row = got["play"]
            c = got["card"]
            out = {"play_id": row["id"], "state": row["state"], "title": c["title"],
                   "conditions": c["conditions"], "contract": c["contract"],
                   "history": c["history"], "now": row["history"].get("now"),
                   "assumed": got["assumed"], "notes": c["notes"]}
            if got["diff"]:
                out["changed"] = got["diff"]
            if row.get("_now_event"):
                out["did_now"] = {k: row["_now_event"].get(k) for k in ("kind", "body")}
            fs = got.get("feed") if isinstance(got.get("feed"), dict) else {}
            if fs.get("note"):
                out["feed"] = (fs["note"] if not row["spec"].get("on") else
                               "Filings and headlines are read live. The price condition reads the "
                               "latest stored bar, and this symbol is not on a live stream, so that "
                               "bar may be old.")
            if row["spec"].get("on"):
                out["_note"] = ("The play is on the trader's screen as a card. Say in one or two "
                                "lines what it reads and what it does; filings and headlines are "
                                "polled live whatever the symbol's stream. For history, quote the "
                                "note about how many stored items match its words (not bars). Say "
                                "every item in `assumed` as a choice they can change.")
            else:
                out["_note"] = ("The play is on the trader's screen as a card. Say in one or two "
                                "lines what it watches, how often it would have fired on history "
                                "(history.fires over history.bars bars), and every item in "
                                "`assumed` as a choice they can change. Quote only these numbers.")
            return out
        if action == "list":
            return {"plays": [{k: c[k] for k in ("id", "title", "state", "symbol", "interval",
                                                 "contract", "fires")}
                              for c in (card(p) for p in list_plays(uid))]}
        if action in ("pause", "resume", "delete"):
            return act(uid, int(play_id), action)
        if action == "explain":
            p = get(uid, int(play_id))
            if not p:
                return {"error": f"no play {play_id}"}
            evs = [e for e in events(uid, 200) if e["play_id"] == p["id"]][:10]
            return {"play": card(p), "events": evs, "spec": p["spec"]}
        return {"error": "action is compile, edit, list, pause, resume, delete or explain"}
    except Bad as exc:
        return {"error": str(exc), "_note": "Fix the play as the error says and call again."}


def tool_measure(action: str = "test", measure: dict | str | None = None,
                 symbol: str = "", interval: str = "") -> dict:
    uid = _uid()
    if isinstance(measure, str):
        try:
            measure = json.loads(measure)
        except ValueError:
            return {"error": "measure must be a JSON object"}
    try:
        if action == "list":
            return {"measures": [{k: m.get(k) for k in ("name", "title", "formula")}
                                 for m in (list_measures(uid) if uid else [])]}
        m = _norm_measure(measure or {})
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
