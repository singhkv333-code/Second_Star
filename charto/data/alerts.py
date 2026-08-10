"""Charto — the watcher: composed conditions in, fired evidence out.

WHAT THIS IS, AND WHY IT IS NOT A LIST OF ALERT TYPES
-----------------------------------------------------
The obvious build is an enum: price alerts, volume alerts, indicator alerts,
percent-move alerts — a `kind` column and a branch per kind. That is the same
mistake `mark.py` names in its own header ("a tool per sentence, forever") and
the same one `screen_universe` refuses ("deliberately not a catalogue of named
screens: the model composes the filters"). A `kind` ladder cannot express
"volume above twice its own average WHILE price breaks the 200-day", and every
new idea costs a migration.

So an alert here is a **composed expression**: a list of conditions, each one
`left <op> right`, where both sides are ADDRESSES resolved against the real
bars. The model writes the address; code resolves it, refuses what it cannot
speak, and owns every millisecond of the firing. That split is CHARTO.md §3
exactly — the LLM decides what a belief means, and is banned from watcher
evaluation and alert firing.

    CONDITION := {left, op, right, right2, x, plus_pct, within}
    RULE      := {symbol, interval, when: [CONDITION, ...], all, freq, expires}

THE ADDRESS GRAMMAR (`OPERANDS` below is the machine-readable copy)
-------------------------------------------------------------------
    a number            1420 · 30 · 2.5
    bar fields          close open high low volume hl2 hlc3 ohlc4
                        close[1] · high[3]        n bars back on this interval
    the session         day.open day.high day.low day.close day.volume
                        pday.*                     the previous session
    a window            52w.high 52w.low · 20d.high · 10d.low
    any indicator       rsi(14) · sma(200) · vwap() · atr(14)
                        macd().signal · bbands(20).upper · stoch(14).k
                        rsi(14)[1]                 one bar back — for crossings
    an average          avg(volume,20) · avg(close,50)
    volume profile      poc · vah · val            of the last N sessions
    a drawing           draw:D7    the drawing's price AT THE CURRENT BAR, so
                                   a sloped trendline's level moves with time
    a detector          pattern(bullish_engulfing) · divergence(rsi) · results()
                        1 when it completed on the last CLOSED bar, else 0

Every indicator in `indicators.py` is addressable the day it is added there —
26 today — because nothing here holds an indicator list. Same for
`patterns.py`'s 34 candles and 22 shapes.

    OPS  cross · cross_up · cross_down     an event: the side flipped
         above · below                     a state, cleared by ≥1 min tick
         rises_pct · falls_pct · changes_pct   over `within` bars
         enters · exits                    a band, needs `right2`
         is_true                           a detector completed

`x` multiplies the right side and `plus_pct` offsets it, which is how "volume
above 2× average" and "2% below yesterday's close" are said without either
becoming its own kind, and without anyone writing an arithmetic parser.

MULTI-CONDITION, IN ONE RULE
----------------------------
`all: true` is AND, and it is the whole reason CHARTO.md #45 ("fire only on
confirmed completion, never approach") is buildable here: a breakout condition
ANDed with a volume-confirmation condition fires once, on confirmation.

Events and states mix under one rule, evaluated as:

    fire when  every condition is TRUE NOW
               and (something flipped on this pass  OR  the conjunction itself
                    just became true)

That single sentence covers both "price crossed 1420" (an event) and "RSI is
under 30 and price is above its 200-day" (a conjunction of states) without a
second code path.

THE FOUR THINGS THAT MAKE IT TRUSTWORTHY
----------------------------------------
1. THE ARMING SIDE IS PERSISTED. A crossing needs the previous side. Held only
   in memory, a restart reads 1,430 against a 1,420 level with no history and
   either fires a phantom cross or refuses forever. `cstate` is a column.
2. A RESTART DOES NOT LOSE A CROSS. On boot, every armed rule is replayed over
   the stored 1-minute bars since its own watermark, and anything found fires
   with the BAR's timestamp and `late=1`. Silently skipping the window the
   process was down for is the failure that ends trust in the widget.
3. THE LOG RECORDS WHAT IT SAW. `value` is the observed number and `level` is
   the target as resolved at that instant — not as typed. That is why a fired
   alert is still evidence a week later.
4. NOTHING HERE MAY BREAK A CANDLE. The tick seam calls `on_bar`, which does
   one bounded `put_nowait` and returns; every evaluation happens on this
   module's own worker thread, and the hook cannot raise into `_live_on_tick`.
   A watcher that stalls the tick loop costs stored minutes, and minutes are
   the asset.

Delivery is in-app: an SSE event per fire on /alerts/stream, plus the durable
log and an unseen count for the bell. With every tab shut nothing is pushed
anywhere — the log is waiting when you come back, and no copy may imply more.
"""
from __future__ import annotations

import json
import logging
import queue
import re
import threading
import time

import dataserver as ds
import indicators

log = logging.getLogger("charto.alerts")

# ── caps, all of them about a shared box rather than about taste ────────────
MAX_PER_USER = 200          # TradingView's own ceiling is this order
MAX_CONDITIONS = 4          # a rule nobody can read is a rule nobody trusts
MAX_LOG_ROWS = 500          # per user, trimmed on write
_ROWS_LIMIT = 320           # bars fetched per (symbol, interval) evaluation
_ROWS_TTL = 1.0             # seconds a folded-rows read is reused for
_TICK_TTL = 3600.0          # min-tick is a listing fact; measure it hourly
_QUEUE_MAX = 2048
_CATCHUP_MAX_MIN = 3 * 24 * 60   # replay at most three days on boot

INTERVALS = tuple(ds.INTRADAY_MIN) + ("1d",)
FREQS = ("once", "per_bar", "per_bar_close", "per_day")
STATES = ("armed", "paused", "fired")


class Unspeakable(Exception):
    """An address or op this engine will not pretend to understand."""


# ══ the vocabulary ═════════════════════════════════════════════════════════
# Spelled out rather than listed, for the reason `SCREEN_FEATURE_HELP` gives:
# an error that only lists names tells the model which words are legal, not
# which one it meant.

OPERANDS = {
    "<number>": "a literal — 1420, 30, 2.5. Range-checked against the loaded "
                "bars, so a magnitude slip is refused rather than armed",
    "close / open / high / low / volume": "the CURRENT bar of this interval, "
                                         "forming bar included",
    "hl2 / hlc3 / ohlc4": "the usual derived prices of the current bar",
    "close[n]": "n bars back on this interval — close[1] is the last CLOSED "
                "bar. Works on every bar field",
    "day.open / day.high / day.low / day.close / day.volume":
        "today's session so far, whatever interval the rule runs on",
    "pday.*": "the previous session's open/high/low/close/volume",
    "<n>d.high / <n>d.low / 52w.high / 52w.low":
        "the extreme of a trailing window — 20d.high, 52w.low",
    "<indicator>(<period>)":
        "any indicator in indicators.py: rsi(14), sma(200), ema(21), atr(14), "
        "vwap(), supertrend(10). Omit the period for its default",
    "<indicator>(<period>).<line>":
        "one line of a multi-line indicator: macd().signal, bbands(20).upper, "
        "stoch(14).k, supertrend(10).direction",
    "<indicator>(<period>)[n]":
        "that indicator n bars back — rsi(14)[1]. Pair with the current value "
        "to express an indicator crossing another series",
    "avg(<field>,<n>)":
        "mean of the last n values — avg(volume,20) is the average-volume "
        "baseline, avg(close,50) a simple mean of closes",
    "poc / vah / val":
        "the volume profile's point of control and value-area edges over the "
        "last `vp_sessions` sessions (default 20). Needs 1-minute bars",
    "draw:<ref>":
        "a drawing of yours, priced AT THE CURRENT BAR: draw:D7. A sloped "
        "trendline's level therefore moves with time, which a typed number "
        "cannot do",
    "pattern(<kind>)":
        "1 when that pattern completed on the last CLOSED bar, else 0 — "
        "pattern(bullish_engulfing), pattern(falling_wedge). Use with is_true "
        "for a completion alert; evaluated on bar close only",
    "divergence(<rsi|macd|...>)":
        "1 when a divergence on that oscillator completed on the last closed "
        "bar, else 0",
    "results()":
        "1 when this symbol's quarterly results land in the current session",
}

OPS = {
    "cross": "the two sides crossed, either direction",
    "cross_up": "left went from below right to at-or-above it",
    "cross_down": "left went from above right to at-or-below it",
    "above": "left is above right, clearing it by at least one minimum tick",
    "below": "left is below right by at least one minimum tick",
    "rises_pct": "left rose by right percent or more within `within` bars",
    "falls_pct": "left fell by right percent or more within `within` bars",
    "changes_pct": "left moved right percent in either direction within `within`",
    "enters": "left came inside the band [right, right2]",
    "exits": "left left the band [right, right2]",
    "is_true": "a detector operand completed (right is ignored)",
}

_EVENT_OPS = frozenset({"cross", "cross_up", "cross_down"})
_BAND_OPS = frozenset({"enters", "exits"})
_MOVE_OPS = frozenset({"rises_pct", "falls_pct", "changes_pct"})
# Detectors read a completed formation off closed bars. Evaluating them on a
# forming bar would announce a pattern that the next tick can un-form, which is
# precisely the approach-vs-completion failure #45 is about.
_CLOSED_ONLY = re.compile(r"^(pattern|divergence|results)\(")

# The verb the LOG prints. Composed from the op, so a new op arrives with its
# sentence and nothing has to remember to add one.
_VERB = {
    "cross": "crossed", "cross_up": "crossed above", "cross_down": "crossed below",
    "above": "rose above", "below": "fell below",
    "rises_pct": "rose", "falls_pct": "fell", "changes_pct": "moved",
    "enters": "entered", "exits": "left", "is_true": "completed",
}
# The row's own sentence. Kept in step with the labels the dialog offers, so a
# rule does not read one way while you build it and another way once it is armed.
_PHRASE = {
    "cross": "Crossing", "cross_up": "Crossing up", "cross_down": "Crossing down",
    "above": "Greater than", "below": "Less than",
    "rises_pct": "Rising by", "falls_pct": "Falling by",
    "changes_pct": "Moving by", "enters": "Entering", "exits": "Leaving",
    "is_true": "",
}
FREQ_LABEL = {"once": "Once only", "per_bar": "Once per bar",
              "per_bar_close": "Once per bar close", "per_day": "Once per day"}


def vocab(msg: str) -> dict:
    """A refusal that hands back the whole grammar — `_screen_vocab`'s shape,
    for `_screen_vocab`'s reason: an error the model cannot act on costs more
    than the vocabulary does."""
    return {"error": msg, "operands": OPERANDS, "ops": list(OPS),
            "intervals": list(INTERVALS), "frequencies": list(FREQS),
            "_note": ("No alert was created or changed. Re-call with addresses "
                      "from this grammar; `x` scales the right side and "
                      "`plus_pct` offsets it, so 'volume above twice its "
                      "average' is {left:'volume', op:'above', "
                      "right:'avg(volume,20)', x:2}.")}


# ══ storage ════════════════════════════════════════════════════════════════
# In charto_users.db, for the reason its own header gives: bars are a derived
# store that gets dropped and rebuilt, and a user's alerts cannot be
# regenerated from anything upstream.

_SCHEMA = """
CREATE TABLE IF NOT EXISTS alerts (
  id       INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id  INTEGER NOT NULL REFERENCES users(id),
  symbol   TEXT NOT NULL,
  interval TEXT NOT NULL,
  spec     TEXT NOT NULL,                  -- {when:[...], all:bool, ...}
  freq     TEXT NOT NULL,
  state    TEXT NOT NULL,
  note     TEXT NOT NULL DEFAULT '',
  created  INTEGER NOT NULL,
  expires  INTEGER,
  cstate   TEXT NOT NULL DEFAULT '[]',     -- per-condition {side, ok} (§1)
  all_ok   INTEGER NOT NULL DEFAULT 0,     -- was the conjunction true last pass
  last_eval_ts   INTEGER NOT NULL DEFAULT 0,   -- catch-up watermark (§2)
  last_fired_bkt INTEGER NOT NULL DEFAULT 0,   -- the bucket `freq` gates on
  fired_at   INTEGER,
  fired_value REAL,
  fire_count INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS alerts_user ON alerts(user_id, state);
CREATE INDEX IF NOT EXISTS alerts_sym  ON alerts(symbol, state);

CREATE TABLE IF NOT EXISTS alert_log (
  id       INTEGER PRIMARY KEY AUTOINCREMENT,
  alert_id INTEGER NOT NULL,
  user_id  INTEGER NOT NULL REFERENCES users(id),
  ts       INTEGER NOT NULL,               -- the BAR's clock, not wall clock
  symbol   TEXT NOT NULL,
  interval TEXT NOT NULL,
  verb     TEXT NOT NULL,
  level    TEXT NOT NULL,                  -- target as resolved at fire time
  value    REAL NOT NULL,                  -- WHAT IT SAW
  meta     TEXT NOT NULL,
  late     INTEGER NOT NULL DEFAULT 0,
  seen     INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS alert_log_user ON alert_log(user_id, ts DESC);
"""


def _db():
    return ds._users


def _init_db() -> None:
    with ds._users_lock:
        _db().executescript(_SCHEMA)
        _db().commit()


# ══ the rule ═══════════════════════════════════════════════════════════════

class Rule:
    """One row of `alerts`, parsed once. Nothing here reads the database."""

    __slots__ = ("id", "user_id", "symbol", "interval", "when", "all", "freq",
                 "state", "note", "created", "expires", "cstate", "all_ok",
                 "last_eval_ts", "last_fired_bkt", "spec", "needs_rows",
                 "closed_only", "vp_sessions")

    def __init__(self, row: tuple) -> None:
        (self.id, self.user_id, self.symbol, self.interval, spec_json,
         self.freq, self.state, self.note, self.created, self.expires,
         cstate_json, self.all_ok, self.last_eval_ts, self.last_fired_bkt) = row[:14]
        self.spec = json.loads(spec_json)
        self.when = self.spec.get("when") or []
        self.all = bool(self.spec.get("all", True))
        self.vp_sessions = int(self.spec.get("vp_sessions") or 20)
        try:
            self.cstate = json.loads(cstate_json)
        except (ValueError, TypeError):
            self.cstate = []
        while len(self.cstate) < len(self.when):
            self.cstate.append({"side": 0, "ok": 0})
        # Classified once, at load: a rule that only compares the last price
        # against literals costs nothing per tick, and must not be made to pay
        # for a folded-rows read that the expensive rules need.
        blob = json.dumps(self.when)
        self.needs_rows = bool(re.search(
            r"\[|\(|day\.|pday\.|52w\.|\bpoc\b|\bvah\b|\bval\b|draw:|\d+[dw]\.",
            blob))
        self.closed_only = bool(_CLOSED_ONLY.search(blob)) \
            or self.freq == "per_bar_close"

    def label(self) -> dict:
        """The three strings the widget's row prints. Composed from the rule,
        never stored — an edited rule cannot then disagree with its own row."""
        parts = [_cond_phrase(c) for c in self.when]
        joiner = " and " if self.all else " or "
        return {"cond": joiner.join(p[0] for p in parts).strip(),
                "level": joiner.join(p[1] for p in parts if p[1]).strip(),
                "meta": f"{FREQ_LABEL.get(self.freq, self.freq)} · {self.interval}"}


def _fmt(v: float) -> str:
    a = abs(v)
    d = 2 if a >= 100 else 3 if a >= 10 else 4 if a >= 1 else 6
    s = f"{v:,.{d}f}"
    return s.rstrip("0").rstrip(".") if d > 2 and "." in s else s


def _right_label(c: dict) -> str:
    """What the right-hand side IS, in words — the number when it is one, the
    address when it is a moving thing, with the scaling said out loud."""
    r = c.get("right")
    base = _fmt(float(r)) if isinstance(r, (int, float)) else str(r)
    x, pct = c.get("x"), c.get("plus_pct")
    if c["op"] in _MOVE_OPS:
        w = c.get("within") or 1
        return f"{base}% in {w} bar{'s' if w != 1 else ''}"
    if x not in (None, 1):
        base = f"{_fmt(float(x))}× {base}"
    if pct not in (None, 0):
        base = f"{base} {'+' if float(pct) > 0 else '−'}{_fmt(abs(float(pct)))}%"
    if c["op"] in _BAND_OPS:
        r2 = c.get("right2")
        base += f"–{_fmt(float(r2)) if isinstance(r2, (int, float)) else r2}"
    return base


def _cond_phrase(c: dict) -> tuple[str, str]:
    op = c.get("op", "")
    left = str(c.get("left", ""))
    if op == "is_true":
        return (left.replace("_", " ").rstrip(")").replace("(", " ")
                .strip() + " completes", "")
    lead = _PHRASE.get(op, op)
    # A price-side left is the implied subject and saying it adds nothing; any
    # other left has to name itself or the row is ambiguous.
    if left not in ("close", "last", "price"):
        lead = f"{left} {lead.lower()}"
    return (lead, _right_label(c))


# ══ resolution ═════════════════════════════════════════════════════════════
# One function, `_resolve`, and a context that carries the rows it reads. Every
# unknown address raises Unspeakable rather than defaulting — an alert armed on
# a misread address is worse than one refused at creation.

_BAR_FIELD = {"open": 1, "high": 2, "low": 3, "close": 4, "volume": 5,
              "last": 4, "price": 4}
_RE_IND = re.compile(r"^([a-z_][a-z0-9_]*)\(\s*([0-9]*)\s*\)"
                     r"(?:\.([a-z_][a-z0-9_]*))?(?:\[(\d+)\])?$")
_RE_BAR = re.compile(r"^([a-z]+)(?:\[(\d+)\])?$")
_RE_AVG = re.compile(r"^avg\(\s*([a-z0-9_]+)\s*,\s*(\d+)\s*\)$")
_RE_WIN = re.compile(r"^(\d+)([dw])\.(high|low)$")
_RE_DET = re.compile(r"^(pattern|divergence)\(\s*([a-z0-9_]+)\s*\)$")


class Ctx:
    """Everything an evaluation of one (symbol, interval) may read.

    `rows` is fetched at most once per pass and shared by every rule on that
    pair — the difference between one folded read and one per rule.

    `forming` says whether the LAST row is a bar still in progress. It is a
    property of the rows, not of the tick that triggered the pass: the tick
    engine closes MINUTES, and whether the alert's own 5-minute or daily bar has
    closed is a different question. Confusing the two is what made a
    `per_bar_close` alert on 5m fire at the first minute-close inside the bar.
    """

    __slots__ = ("symbol", "interval", "rows", "forming", "_ind", "_vp",
                 "_draw", "_tick")

    def __init__(self, symbol: str, interval: str, rows: list, forming: bool):
        self.symbol, self.interval, self.rows, self.forming = \
            symbol, interval, rows, forming
        self._ind: dict = {}
        self._vp: dict | None = None
        self._draw: dict | None = None
        self._tick: float | None = None

    # -- the bar the addresses are relative to -------------------
    def bar(self, back: int) -> tuple:
        i = len(self.rows) - 1 - back
        if i < 0:
            raise Unspeakable(
                f"{self.symbol} {self.interval} has {len(self.rows)} bars — "
                f"cannot read {back} bars back")
        return self.rows[i]

    def indicator(self, name: str, period: int, line: str, back: int) -> float:
        key = (name, period)
        got = self._ind.get(key)
        if got is None:
            if name not in indicators.SPECS:
                raise Unspeakable(
                    f"unknown indicator '{name}' — indicators.py has "
                    f"{len(indicators.SPECS)}: {', '.join(sorted(indicators.SPECS))}")
            try:
                got = self._ind[key] = indicators.compute(
                    name, self.rows, period)["lines"]
            except ValueError as exc:
                raise Unspeakable(str(exc)) from None
        if line:
            if line not in got:
                raise Unspeakable(
                    f"{name}() has no line '{line}' — it has "
                    f"{', '.join(sorted(got))}")
            series = got[line]
        elif name in got:
            series = got[name]
        else:
            # single-line indicators name their line after themselves; the rest
            # must be addressed explicitly rather than guessed at
            if len(got) != 1:
                raise Unspeakable(
                    f"{name}() has several lines — address one of "
                    f"{', '.join(sorted(got))}, e.g. {name}().{sorted(got)[0]}")
            series = next(iter(got.values()))
        i = len(series) - 1 - back
        if i < 0 or series[i] is None:
            raise Unspeakable(
                f"{name}({period or 'default'}) has no value "
                f"{back} bar(s) back on {self.symbol} {self.interval}")
        return float(series[i])

    def min_tick(self) -> float:
        """One minimum tick, MEASURED — `above`/`below` need something to clear
        and an assumed 0.05 is wrong on a sub-rupee instrument and on crypto."""
        if self._tick is None:
            self._tick = _min_tick(self.symbol) or abs(self.bar(0)[4]) * 1e-5
        return self._tick

    def profile(self) -> dict:
        if self._vp is None:
            self._vp = _vp_of(self.symbol)
        return self._vp

    def drawings(self, uid: int) -> dict:
        # Keyed by owner: one Ctx is shared by every rule on this (symbol,
        # interval) pass, and those rules can belong to different people.
        if self._draw is None:
            self._draw = {}
        if uid not in self._draw:
            self._draw[uid] = _drawings_of(self.symbol, uid)
        return self._draw[uid]


_tick_cache: dict[str, tuple[float, float | None]] = {}


def _min_tick(symbol: str) -> float | None:
    hit = _tick_cache.get(symbol)
    now = time.monotonic()
    if hit and now - hit[0] < _TICK_TTL:
        return hit[1]
    try:
        mins = ds._con.execute(
            "SELECT ts,o,h,l,c,v FROM bars WHERE symbol=? ORDER BY ts DESC "
            "LIMIT 4000", (symbol,)).fetchall()
        got = ds._infer_tick(list(reversed(mins))) if mins else None
    except Exception:                                       # noqa: BLE001
        got = None
    _tick_cache[symbol] = (now, got)
    return got


def _resolve(addr, ctx: Ctx, rule: Rule) -> float:
    """An address → a number. Raises Unspeakable, never guesses."""
    if isinstance(addr, bool):
        return 1.0 if addr else 0.0
    if isinstance(addr, (int, float)):
        return float(addr)
    s = str(addr or "").strip().lower()
    if not s:
        raise Unspeakable("an empty address")
    # a literal that arrived as a string, with mark.py's range guard
    try:
        v = float(s.replace(",", "").lstrip("₹$"))
    except ValueError:
        pass
    else:
        return v            # magnitude is checked by _magnitude_guard, which
                            # can see what the number is being compared to

    if s.startswith("level:"):
        try:
            return float(s[6:].replace(",", ""))
        except ValueError:
            raise Unspeakable(f"'{addr}' is not a price") from None

    if s.startswith("draw:"):
        ref = str(addr).strip()[5:]
        mine = ctx.drawings(rule.user_id)
        got = mine.get(ref.upper())
        if got is None:
            have = ", ".join(sorted(mine)) or "none"
            raise Unspeakable(f"no drawing '{ref}' on {ctx.symbol} "
                              f"(have: {have})")
        return _draw_price_at(got, ctx)

    if s in ("poc", "vah", "val"):
        prof = ctx.profile()
        if prof.get(s) is None:
            raise Unspeakable(
                f"no volume profile for {ctx.symbol} — it needs 1-minute "
                f"bars, and this symbol is stored daily only")
        return float(prof[s])

    if s == "results()":
        return 1.0 if _results_today(ctx.symbol) else 0.0

    m = _RE_DET.match(s)
    if m:
        return _detector(m.group(1), m.group(2), ctx)

    m = _RE_AVG.match(s)
    if m:
        field, n = m.group(1), int(m.group(2))
        if field not in _BAR_FIELD:
            raise Unspeakable(
                f"avg() takes a bar field, not '{field}' — one of "
                f"{', '.join(sorted(set(_BAR_FIELD)))}")
        if n < 1 or n > 500:
            raise Unspeakable("avg() needs a window between 1 and 500")
        col = _BAR_FIELD[field]
        # the CURRENT (possibly forming) bar is excluded: an average that
        # contains the value being compared to it drifts toward its own input,
        # and "volume above its average" then means less every tick
        seq = [r[col] for r in ctx.rows[-(n + 1):-1]]
        if len(seq) < n:
            raise Unspeakable(f"avg({field},{n}) needs {n} closed bars, "
                              f"have {len(seq)}")
        return sum(seq) / len(seq)

    m = _RE_WIN.match(s)
    if m or s in ("52w.high", "52w.low"):
        if s.startswith("52w."):
            n, unit, side = 52, "w", s[4:]
        else:
            n, unit, side = int(m.group(1)), m.group(2), m.group(3)
        bars = n * (5 if unit == "w" else 1)
        if ctx.interval in ds.INTRADAY_MIN:
            # a 52-week window on 5-minute bars is not 52 weeks of 5-minute
            # bars we hold — say so rather than silently measuring a fortnight
            raise Unspeakable(
                f"'{s}' is a daily window and this rule runs on "
                f"{ctx.interval}. Put the rule on 1d, or address "
                f"high[n]/low[n] on this interval instead")
        w = ctx.rows[-bars:]
        if len(w) < min(bars, 20):
            raise Unspeakable(f"'{s}' needs {bars} daily bars, have {len(w)}")
        return max(r[2] for r in w) if side == "high" else min(r[3] for r in w)

    if s.startswith("day.") or s.startswith("pday."):
        return _session_field(s, ctx)

    m = _RE_IND.match(s)
    if m:
        name, per, line, back = m.group(1), m.group(2), m.group(3), m.group(4)
        return ctx.indicator(name, int(per or 0), line or "", int(back or 0))

    m = _RE_BAR.match(s)
    if m and m.group(1) in _BAR_FIELD:
        return float(ctx.bar(int(m.group(2) or 0))[_BAR_FIELD[m.group(1)]])
    if m and m.group(1) in ("hl2", "hlc3", "ohlc4"):
        b, back = None, int(m.group(2) or 0)
        b = ctx.bar(back)
        if m.group(1) == "hl2":
            return (b[2] + b[3]) / 2
        if m.group(1) == "hlc3":
            return (b[2] + b[3] + b[4]) / 3
        return (b[1] + b[2] + b[3] + b[4]) / 4

    raise Unspeakable(f"cannot read '{addr}' as an address")


def _price_like(addr) -> bool:
    """Does this address resolve to a PRICE of this instrument?

    The magnitude guard needs to know, and a bare number cannot say. Volume is
    deliberately not price-like: 4.2 million shares next to a ₹1,300 close is
    a correct comparison of two different quantities.
    """
    if isinstance(addr, (int, float)):
        return False
    s = str(addr).strip().lower()
    if s in ("poc", "vah", "val") or s.startswith("level:") or s.startswith("draw:"):
        return True
    if _RE_WIN.match(s) or s in ("52w.high", "52w.low"):
        return True
    if s.startswith("day.") or s.startswith("pday."):
        return s.split(".", 1)[1] != "volume"
    m = _RE_AVG.match(s)
    if m:
        return m.group(1) != "volume"
    m = _RE_BAR.match(s)
    if m and (m.group(1) in _BAR_FIELD or m.group(1) in ("hl2", "hlc3", "ohlc4")):
        return m.group(1) != "volume"
    m = _RE_IND.match(s)
    if m:
        spec = indicators.SPECS.get(m.group(1))
        # an overlay indicator is drawn ON the price axis, which is exactly
        # what "quoted in the instrument's own units" means
        return bool(spec) and spec.get("pane") == "overlay"
    return False


def _bounds_of(addr) -> tuple[float, float] | None:
    """An oscillator's declared range, straight off indicators.SPECS — so RSI's
    0–100 is read from the one place that already states it."""
    if isinstance(addr, (int, float)):
        return None
    m = _RE_IND.match(str(addr).strip().lower())
    if not m:
        return None
    b = (indicators.SPECS.get(m.group(1)) or {}).get("bounds")
    return (float(b[0]), float(b[1])) if b else None


def _magnitude_guard(c: dict, ctx: Ctx) -> None:
    """mark.py's guard, moved to where it can actually work.

    A magnitude slip is the single most likely way a typed number goes wrong,
    and an alert armed at 142 instead of 1420 never fires with nothing to see.
    But a literal is only checkable against WHAT IT IS COMPARED TO: 30 is wild
    beside a ₹1,309 close and perfectly ordinary beside RSI. So the guard runs
    only when exactly one side is a literal, and it asks the OTHER side what
    units it speaks — the price range on screen, or the oscillator's own
    declared bounds. A comparison of two addresses is never second-guessed.
    """
    if c["op"] in _MOVE_OPS:
        return                       # the right side is a percentage, not a level
    left, right = c.get("left"), c.get("right")
    lit_left = isinstance(left, (int, float))
    lit_right = isinstance(right, (int, float))
    if lit_left == lit_right:
        return                       # both literals, or neither: nothing to check
    lit = float(left if lit_left else right)
    other = right if lit_left else left
    # scaling is an explicit instruction to change magnitude; honouring it and
    # then complaining about the result would refuse the user's own arithmetic
    if not lit_left and (c.get("x") not in (None, "", 1)
                         or c.get("plus_pct") not in (None, "", 0)):
        return
    bounds = _bounds_of(other)
    if bounds:
        lo, hi = bounds
        pad = (hi - lo) * 0.25
        if not (lo - pad <= lit <= hi + pad):
            raise Unspeakable(
                f"{_fmt(lit)} is outside {other}'s range of "
                f"{_fmt(lo)}–{_fmt(hi)} — it can never be reached")
        return
    if not _price_like(other) or not ctx.rows:
        return
    w = ctx.rows[-500:]
    lo = min(r[3] for r in w)
    hi = max(r[2] for r in w)
    if lo <= 0:
        return
    # The tolerance scales with the price LEVEL, not with the window's range.
    # A span-based band came first and let 14 through beside a price of 100 —
    # one volatile bar widened the span until twenty spans reached zero. A
    # slipped decimal is a FACTOR error, so a factor is what bounds it.
    #
    # Five-fold, not ten: the slip is a factor off the value the user MEANT, not
    # off the price. Someone at 100 who meant 140 and typed 14 is 7x from the
    # price, so a ten-fold band would still admit it. Five admits everything
    # anyone really watches for — a 40% crash, a doubling — and the asymmetry
    # settles it: a false refusal is one sentence in the preview and is retyped
    # in seconds, while a false accept arms a rule that silently never fires.
    if not (lo / 5 <= lit <= hi * 5):
        raise Unspeakable(
            f"{_fmt(lit)} is far outside {ctx.symbol}'s range "
            f"({_fmt(lo)}–{_fmt(hi)}) — check the magnitude")


def _session_field(s: str, ctx: Ctx) -> float:
    prev = s.startswith("pday.")
    field = s.split(".", 1)[1]
    if field not in _BAR_FIELD:
        raise Unspeakable(
            f"'{s}' — the session fields are open, high, low, close, volume")
    daily = ds.get_bars(ctx.symbol, "1d", None, 3)["bars"]
    if len(daily) < (2 if prev else 1):
        raise Unspeakable(f"{ctx.symbol} has no {'previous ' if prev else ''}"
                          f"session to read '{s}' from")
    b = daily[-2 if prev else -1]
    return float(b[{"open": "o", "high": "h", "low": "l", "close": "c",
                    "volume": "v", "last": "c", "price": "c"}[field]])


def _draw_price_at(d: dict, ctx: Ctx) -> float:
    """A drawing's price at the CURRENT bar.

    A horizontal line is its own level. A two-point line is interpolated — and
    then EXTRAPOLATED past its second anchor, because a trendline you are
    watching for a break is by definition being watched to the right of where
    you drew it. That is the whole reason a drawing-anchored alert beats a typed
    number: move the line, and the level the engine watches moves with it.

    Anchors come through ds._drawing_points, which is already the one place that
    knows a drawing carries `pts` of {t, p} and that an hline has only one.
    """
    pts = [p for p in ds._drawing_points(d) if p.get("v") is not None]
    if not pts:
        raise Unspeakable(
            f"drawing {d.get('ref') or d.get('id')} carries no price")
    flat = len(pts) < 2 or pts[1].get("_flat") \
        or d.get("type") in ("hline", "hray")
    if flat:
        return float(pts[0]["v"])
    t1, v1 = pts[0].get("t"), float(pts[0]["v"])
    t2, v2 = pts[1].get("t"), float(pts[1]["v"])
    if t1 is None or t2 is None or t2 == t1:
        return v2               # no time span to slope along; the level is flat
    now = float(ctx.bar(0)[0])
    return v1 + (v2 - v1) * (now - float(t1)) / (float(t2) - float(t1))


def _drawings_of(symbol: str, uid: int = 0) -> dict:
    """The user's drawings for this symbol, by ref.

    Read from the `drawings` row of workspace_state — the same row the chart
    autosaves — so moving a line on screen moves the level this engine watches,
    with no second store to keep in step. Scoped to the OWNER: two accounts can
    each have a D3 on RELIANCE, and resolving one against the other's line would
    be a silent, confident wrong answer.
    """
    out: dict = {}
    try:
        with ds._users_lock:
            rows = _db().execute(
                "SELECT json FROM workspace_state WHERE symbol=? AND "
                "key='drawings'" + (" AND user_id=?" if uid else ""),
                (symbol, uid) if uid else (symbol,)).fetchall()
    except Exception:                                       # noqa: BLE001
        return out
    for (blob,) in rows:
        try:
            data = json.loads(blob)
        except (ValueError, TypeError):
            continue
        items = data if isinstance(data, list) else (
            data.get("drawings") or data.get("items") or [])
        if not isinstance(items, list):
            continue
        for d in items:
            if not isinstance(d, dict):
                continue
            for key in (d.get("ref"), d.get("id")):
                if key:
                    out[str(key).upper()] = d
    return out


def _vp_of(symbol: str) -> dict:
    """POC and value-area edges off the swept vp_screen table — the screener's
    own source, so an alert and a screen can never quote different levels."""
    try:
        row = ds._con.execute(
            "SELECT poc, val, vah FROM vp_screen WHERE symbol=?",
            (symbol,)).fetchone()
    except Exception:                                       # noqa: BLE001
        return {"poc": None, "vah": None, "val": None}
    if not row:
        return {"poc": None, "vah": None, "val": None}
    return {"poc": row[0], "val": row[1], "vah": row[2]}


def _results_today(symbol: str) -> bool:
    try:
        row = ds._con.execute(
            "SELECT trade_date FROM results WHERE symbol=? "
            "ORDER BY trade_date DESC LIMIT 1", (symbol,)).fetchone()
    except Exception:                                       # noqa: BLE001
        return False
    if not row or not row[0]:
        return False
    t = ds._parse_ist(str(row[0]))
    if t is None:
        return False
    return ds._ist_day(t) == ds._ist_day(int(time.time()))


def _detector(family: str, kind: str, ctx: Ctx) -> float:
    """1 when the named formation completed on the LAST CLOSED BAR.

    Completion only, never approach — CHARTO.md #45. `bars_ago == 0` on closed
    bars is exactly that, and it is why these operands refuse to be evaluated
    against a forming bar.
    """
    import patterns
    rows = [r for r in ctx.rows]
    # drop the forming bar: a pattern on a bar that is still moving is a claim
    # the next tick can withdraw
    if ctx.forming and rows:
        rows = rows[:-1]
    if len(rows) < 60:
        raise Unspeakable(
            f"{family}({kind}) needs 60 closed bars, have {len(rows)}")
    off = ds.session_for(ctx.symbol)[1]

    def _ist(ts: int, _o: int = off) -> str:
        return ds.datetime.fromtimestamp(
            ts + _o, tz=ds.timezone.utc).strftime("%d %b %Y")

    if family == "divergence":
        if kind not in indicators.SPECS:
            raise Unspeakable(f"divergence() takes an oscillator, not '{kind}'")
        try:
            osc = indicators.compute(kind, rows, 0)["lines"]
        except ValueError as exc:
            raise Unspeakable(str(exc)) from None
        series = osc.get(kind) or next(iter(osc.values()))
        # _divergences returns {divergences, track_record}, not a bare list —
        # the track record is the honesty half and is not what fires anything.
        got = ds._divergences(rows, series, 5) or {}
        found = got.get("divergences") or []
        return 1.0 if any(d.get("bars_ago") == 0 for d in found) else 0.0

    if kind not in patterns.ALL_KINDS:
        raise Unspeakable(
            f"unknown pattern '{kind}' — patterns.py has "
            f"{len(patterns.CANDLE_KINDS)} candles and "
            f"{len(patterns.CHART_KINDS)} shapes")
    if kind in patterns.CANDLE_KINDS:
        found = patterns.candlesticks(rows, ds._atr(rows, 14), _ist,
                                      {kind}, limit=4)
    else:
        found = patterns.chart_patterns(rows, ds._pivots(rows, 5),
                                        ds._tolerance(rows), _ist, {kind},
                                        limit=4)
    return 1.0 if any(p.get("bars_ago") == 0 for p in (found or [])) else 0.0


# ══ evaluation ═════════════════════════════════════════════════════════════

def _target(c: dict, ctx: Ctx, rule: Rule) -> float:
    """The right-hand side, scaled. `x` and `plus_pct` are what keep 'twice its
    average' and '2% below yesterday' from each needing their own alert kind."""
    v = _resolve(c.get("right"), ctx, rule)
    x = c.get("x")
    if x not in (None, ""):
        v *= float(x)
    pct = c.get("plus_pct")
    if pct not in (None, "", 0):
        v *= 1 + float(pct) / 100.0
    return v


def _eval_condition(c: dict, st: dict, ctx: Ctx, rule: Rule) -> dict:
    """One condition → {ok, edge, value, target, side}.

    `ok` is the STATE (is it true now), `edge` is the EVENT (did it become true
    on this pass). Keeping both is what lets one rule mix "crossed 1420" with
    "and RSI is under 30" without a second code path.
    """
    op = c.get("op")
    left_addr = c.get("left")
    _magnitude_guard(c, ctx)
    if op == "is_true":
        v = _resolve(left_addr, ctx, rule)
        ok = v > 0
        return {"ok": ok, "edge": ok and not st.get("ok"), "value": v,
                "target": 1.0, "side": 1 if ok else -1}

    if op in _MOVE_OPS:
        back = max(1, int(c.get("within") or 1))
        now = _resolve(left_addr, ctx, rule)
        then_addr = _shift(left_addr, back)
        then = _resolve(then_addr, ctx, rule)
        if not then:
            raise Unspeakable(f"'{left_addr}' was zero {back} bars ago — "
                              f"a percentage move off zero is undefined")
        move = (now - then) / abs(then) * 100.0
        need = float(_resolve(c.get("right"), ctx, rule))
        ok = (move >= need if op == "rises_pct" else
              move <= -need if op == "falls_pct" else abs(move) >= need)
        return {"ok": ok, "edge": ok and not st.get("ok"), "value": now,
                "target": then, "side": 1 if ok else -1, "move": move}

    left = _resolve(left_addr, ctx, rule)
    if op in _BAND_OPS:
        lo = _target(c, ctx, rule)
        hi = _resolve(c.get("right2"), ctx, rule)
        if lo > hi:
            lo, hi = hi, lo
        inside = lo <= left <= hi
        ok = inside if op == "enters" else not inside
        return {"ok": ok, "edge": ok and not st.get("ok"), "value": left,
                "target": lo if op == "enters" else hi,
                "side": 1 if ok else -1}

    tgt = _target(c, ctx, rule)
    prev_side = int(st.get("side") or 0)
    d = left - tgt
    if op in ("above", "below"):
        # TradingView's rule, with a measured tick rather than an assumed one:
        # `greater than` means cleared by at least one minimum increment, so a
        # print exactly ON the level is not yet above it.
        eps = ctx.min_tick()
        side = 1 if d >= eps else -1 if d <= -eps else 0
        ok = side > 0 if op == "above" else side < 0
        edge = ok and prev_side != (1 if op == "above" else -1)
    else:
        side = 1 if d > 0 else -1 if d < 0 else 0
        # A touch (side 0) is not a cross yet, and it must not reset the memory
        # of which side we came from — otherwise a level being sat on flips to
        # "unknown" and the next tick in either direction reads as a crossing.
        if op == "cross_up":
            ok, edge = side >= 0, prev_side == -1 and side >= 0
        elif op == "cross_down":
            ok, edge = side <= 0, prev_side == 1 and side <= 0
        else:
            ok = True
            edge = prev_side != 0 and side != 0 and side != prev_side
    return {"ok": ok, "edge": edge, "value": left, "target": tgt,
            "side": side if side else prev_side}


def _shift(addr, back: int):
    """The same address, n bars earlier — `close` → `close[2]`, `rsi(14)` →
    `rsi(14)[2]`. One helper, so a percent-move rule needs no second grammar."""
    if isinstance(addr, (int, float)):
        return addr
    s = str(addr).strip()
    if s.endswith("]"):
        head, _, tail = s.rpartition("[")
        try:
            return f"{head}[{int(tail[:-1]) + back}]"
        except ValueError:
            raise Unspeakable(f"cannot shift '{addr}'") from None
    return f"{s}[{back}]"


def _bucket(rule: Rule, ctx: Ctx) -> int:
    """The bar the frequency gate counts in — the alert's own interval, or the
    session for `per_day`."""
    ts = int(ctx.bar(0)[0])
    if rule.freq == "per_day":
        return ds._ist_day(ts, ds.session_for(rule.symbol)[1])
    return ts


def evaluate(rule: Rule, ctx: Ctx, *, late: bool = False) -> dict | None:
    """Run one rule against one context. Returns a fire record, or None.

    Raises Unspeakable when an address stops being readable — the caller
    pauses the rule and says why rather than letting it silently never fire.
    """
    # A confirmed-close rule must never see a bar that is still moving. The
    # caller hands it a closed view; this is the belt.
    if rule.closed_only and ctx.forming:
        return None
    # `once` means once, and this is where that is enforced rather than in the
    # index. _fire flips the state and _load_index drops the rule, but those are
    # two steps and a tick can land between them — measured in a state-machine
    # harness, an alert on `once` fired a second time from the same Rule object.
    if rule.state != "armed" or (rule.freq == "once" and rule.last_fired_bkt):
        return None
    results, sides = [], []
    for c, st in zip(rule.when, rule.cstate):
        r = _eval_condition(c, st, ctx, rule)
        results.append(r)
        sides.append({"side": r["side"], "ok": 1 if r["ok"] else 0})
    if not results:
        return None
    all_ok = all(r["ok"] for r in results) if rule.all \
        else any(r["ok"] for r in results)
    # THE firing sentence: everything true now, and either something flipped on
    # this pass or the conjunction itself just became true. The second half is
    # what makes a rule of pure states ("RSI under 30 and above the 200-day")
    # fire on its own edge instead of never firing at all.
    edged = any(r["edge"] for r in results)
    fire = all_ok and (edged or not rule.all_ok)

    rule.cstate = sides
    rule.all_ok = 1 if all_ok else 0
    rule.last_eval_ts = int(ctx.bar(0)[0])
    if not fire:
        return None
    bkt = _bucket(rule, ctx)
    if rule.freq in ("per_bar", "per_bar_close", "per_day") \
            and rule.last_fired_bkt == bkt:
        return None                       # already spoken for in this bucket
    rule.last_fired_bkt = bkt
    # The log's four fields, written from the state that fired rather than from
    # the rule as typed — see the header, point 3.
    lead = results[0]
    verb = " and ".join(dict.fromkeys(
        _VERB.get(c.get("op"), c.get("op", "")) for c in rule.when))
    subject = str(rule.when[0].get("left", "price"))
    if len(rule.when) > 1:
        verb = f"{verb} ({len(rule.when)} conditions)"
    return {
        "ts": int(ctx.bar(0)[0]),
        "verb": verb if subject in ("close", "last", "price")
                else f"{subject} {verb}",
        "level": _fmt(lead["target"]) if rule.when[0].get("op") != "is_true"
                 else rule.label()["cond"],
        "value": round(float(lead["value"]), 6),
        "meta": f"{rule.interval} · {_subject_word(subject)}",
        "late": 1 if late else 0,
    }


def _subject_word(addr: str) -> str:
    s = str(addr)
    if s in ("close", "last", "price", "open", "high", "low"):
        return "price"
    if s.startswith("volume") or s.startswith("avg(volume"):
        return "volume"
    m = _RE_IND.match(s)
    return m.group(1) if m else s.split("(")[0].split(".")[0]


# ══ the index, and the worker ══════════════════════════════════════════════
# _BY_SYM is the only thing the tick path touches, and it is replaced whole
# rather than mutated, so a reader never sees a half-built list.

_BY_SYM: dict[str, list[Rule]] = {}
_INDEX_LOCK = threading.Lock()
_Q: queue.Queue = queue.Queue(maxsize=_QUEUE_MAX)
_SUBS: dict[int, list] = {}
_SUBS_LOCK = threading.Lock()
_WORKER: threading.Thread | None = None
_STOP = threading.Event()
STATS = {"ticks_in": 0, "dropped": 0, "evals": 0, "fires": 0, "refusals": 0,
         "late_fires": 0, "started": 0}


def _load_index() -> None:
    with ds._users_lock:
        rows = _db().execute(
            "SELECT id,user_id,symbol,interval,spec,freq,state,note,created,"
            "expires,cstate,all_ok,last_eval_ts,last_fired_bkt FROM alerts "
            "WHERE state='armed'").fetchall()
    by_sym: dict[str, list[Rule]] = {}
    now = int(time.time())
    for row in rows:
        try:
            r = Rule(row)
        except Exception as exc:                            # noqa: BLE001
            log.warning("alerts: rule %s unreadable, skipped: %s", row[0], exc)
            continue
        if r.expires and r.expires <= now:
            _set_state(r.id, "paused", why="expired")
            continue
        by_sym.setdefault(r.symbol, []).append(r)
    with _INDEX_LOCK:
        _BY_SYM.clear()
        _BY_SYM.update(by_sym)


def watched_symbols() -> list[str]:
    """Every symbol something is armed on — what the feed has to cover."""
    with _INDEX_LOCK:
        return sorted(_BY_SYM)


def on_bar(symbol: str, form: list, closed: bool) -> None:
    """THE HOOK. Called from ds._live_on_tick, on the tick thread.

    It does one bounded put and returns. It must never block, never read
    SQLite, and never raise — a watcher that stalls the tick loop costs stored
    minutes, and the minutes are the asset. The caller wraps this in its own
    try/except as a second belt.
    """
    with _INDEX_LOCK:
        if symbol not in _BY_SYM:
            return
    STATS["ticks_in"] += 1
    try:
        _Q.put_nowait((symbol, form[0], form[4], closed))
    except queue.Full:
        # Coalescing, not loss of correctness: the next tick carries the same
        # price and the catch-up scan covers a real gap. Counted so a queue
        # that is always full is visible rather than merely slow.
        STATS["dropped"] += 1


def _worker() -> None:
    while not _STOP.is_set():
        try:
            item = _Q.get(timeout=1.0)
        except queue.Empty:
            continue
        # Drain and COALESCE: many ticks on one symbol collapse to its latest,
        # and a bar close is sticky so a closing minute can never be swallowed
        # by the tick that follows it.
        batch: dict[str, tuple[int, float, bool]] = {}
        while True:
            sym, ts, px, closed = item
            prev = batch.get(sym)
            batch[sym] = (ts, px, closed or bool(prev and prev[2]))
            try:
                item = _Q.get_nowait()
            except queue.Empty:
                break
        for sym, (_ts, _px, closed) in batch.items():
            try:
                _run_symbol(sym, closed)
            except Exception:                               # noqa: BLE001
                log.warning("alerts: pass on %s failed", sym, exc_info=True)


_rows_cache: dict[tuple, tuple[float, list]] = {}


def _rows_for(symbol: str, interval: str) -> list:
    """Folded bars for one (symbol, interval), reused across the rules of a
    pass. One read where a rule-per-read would be dozens."""
    key = (symbol, interval)
    hit = _rows_cache.get(key)
    now = time.monotonic()
    if hit and now - hit[0] < _ROWS_TTL:
        return hit[1]
    d = ds.get_bars(symbol, interval, None, _ROWS_LIMIT)
    rows = [(b["t"], b["o"], b["h"], b["l"], b["c"], b["v"]) for b in d["bars"]]
    _rows_cache[key] = (now, rows)
    return rows


def _is_forming(symbol: str) -> bool:
    """Is the last bar get_bars returns a bar still in progress?

    get_bars merges the live forming minute into whatever interval it folds, so
    a live symbol's last bar is always partial — at every interval, daily
    included. With no feed running, every stored bar is complete.
    """
    return ds._live_view(symbol) is not None


def _ctx_for(symbol: str, interval: str, want_closed: bool,
             cache: dict) -> Ctx | None:
    """The rows one rule needs, built at most once per (interval, view).

    Two views of the same interval, and the difference is the whole meaning of
    `per_bar_close`: the LIVE view ends on the bar being formed, the CLOSED view
    ends on the last bar that finished.
    """
    key = (interval, want_closed)
    got = cache.get(key)
    if got is not None:
        return got or None
    rows = _rows_for(symbol, interval)
    forming = _is_forming(symbol) and bool(rows)
    if want_closed and forming:
        rows = rows[:-1]
        forming = False
    if not rows:
        cache[key] = False
        return None
    ctx = cache[key] = Ctx(symbol, interval, rows, forming)
    return ctx


def _run_symbol(symbol: str, closed_minute: bool) -> None:
    with _INDEX_LOCK:
        rules = list(_BY_SYM.get(symbol) or [])
    if not rules:
        return
    # Thread-local, so stamping it here cannot disturb a request thread — it is
    # what lets this worker use ds's own symbol-scoped helpers unchanged.
    ds._req.symbol = symbol
    now = int(time.time())
    ctxs: dict = {}
    for r in rules:
        if r.expires and r.expires <= now:
            _set_state(r.id, "paused", why="expired")
            continue
        # A minute closing is only the CUE to look. Whether this rule's own bar
        # closed is decided below, against its own interval.
        if r.closed_only and not closed_minute:
            continue
        try:
            ctx = _ctx_for(symbol, r.interval, r.closed_only, ctxs)
            if ctx is None:
                continue
            # THE `per_bar_close` GATE. The rule is evaluated once the interval's
            # bar is finished and not before: if the newest closed bar is one it
            # has already read, nothing has closed since and there is nothing to
            # confirm. Without this the rule ran on every minute-close inside the
            # bar and fired up to four minutes early on a 5m alert — the exact
            # opposite of what "once per bar close" promises.
            if r.closed_only and ctx.rows[-1][0] <= r.last_eval_ts:
                continue
            STATS["evals"] += 1
            hit = evaluate(r, ctx)
        except Unspeakable as exc:
            # An address that stopped resolving pauses the rule and SAYS SO.
            # Leaving it armed would be a rule that silently never fires, which
            # is the one outcome an alert must never have.
            STATS["refusals"] += 1
            _set_state(r.id, "paused", why=str(exc))
            _load_index()
            return
        except Exception:                                   # noqa: BLE001
            log.warning("alerts: rule %s errored", r.id, exc_info=True)
            continue
        _persist_eval(r)
        if hit:
            _fire(r, hit)


# ══ firing ═════════════════════════════════════════════════════════════════

def _persist_eval(r: Rule) -> None:
    with ds._users_lock:
        _db().execute(
            "UPDATE alerts SET cstate=?, all_ok=?, last_eval_ts=?, "
            "last_fired_bkt=? WHERE id=?",
            (json.dumps(r.cstate), r.all_ok, r.last_eval_ts,
             r.last_fired_bkt, r.id))
        _db().commit()


def _fire(r: Rule, hit: dict) -> None:
    lab = r.label()
    with ds._users_lock:
        cur = _db().execute(
            "INSERT INTO alert_log (alert_id,user_id,ts,symbol,interval,verb,"
            "level,value,meta,late,seen) VALUES (?,?,?,?,?,?,?,?,?,?,0)",
            (r.id, r.user_id, hit["ts"], r.symbol, r.interval, hit["verb"],
             hit["level"], hit["value"], hit["meta"], hit["late"]))
        log_id = cur.lastrowid
        # `once` stops itself — the row stays and wears the Fired pill, which
        # is what the widget already draws. Every other frequency stays armed.
        new_state = "fired" if r.freq == "once" else "armed"
        _db().execute(
            "UPDATE alerts SET state=?, fired_at=?, fired_value=?, "
            "fire_count=fire_count+1, cstate=?, all_ok=?, last_eval_ts=?, "
            "last_fired_bkt=? WHERE id=?",
            (new_state, hit["ts"], hit["value"], json.dumps(r.cstate),
             r.all_ok, r.last_eval_ts, r.last_fired_bkt, r.id))
        _trim_log(r.user_id)
        _db().commit()
    r.state = new_state
    STATS["fires"] += 1
    if hit["late"]:
        STATS["late_fires"] += 1
    if new_state == "fired":
        _load_index()
    log.info("alerts: %s fired on %s %s (%s %s, saw %s)", r.id, r.symbol,
             r.interval, hit["verb"], hit["level"], hit["value"])
    push(r.user_id, {
        "type": "fired",
        "alert": {"id": r.id, "symbol": r.symbol, "interval": r.interval,
                  "state": new_state, "note": r.note, **lab},
        "log": {"id": log_id, "ts": hit["ts"], "symbol": r.symbol,
                "interval": r.interval, "verb": hit["verb"],
                "level": hit["level"], "value": hit["value"],
                "meta": hit["meta"], "late": bool(hit["late"]), "seen": False},
    })


def _trim_log(uid: int) -> None:
    _db().execute(
        "DELETE FROM alert_log WHERE user_id=? AND id NOT IN "
        "(SELECT id FROM alert_log WHERE user_id=? ORDER BY ts DESC LIMIT ?)",
        (uid, uid, MAX_LOG_ROWS))


def _set_state(alert_id: int, state: str, why: str = "") -> None:
    with ds._users_lock:
        if why:
            row = _db().execute("SELECT note FROM alerts WHERE id=?",
                                (alert_id,)).fetchone()
            note = (row[0] if row else "") or ""
            stamp = f"[{state}: {why}]"
            if stamp not in note:
                note = (note + " " + stamp).strip()[:400]
            _db().execute("UPDATE alerts SET state=?, note=? WHERE id=?",
                          (state, note, alert_id))
        else:
            _db().execute("UPDATE alerts SET state=? WHERE id=?",
                          (state, alert_id))
        _db().commit()


# ── delivery ───────────────────────────────────────────────────────────────

def subscribe(uid: int) -> queue.Queue:
    q: queue.Queue = queue.Queue(maxsize=64)
    q.dead = False
    with _SUBS_LOCK:
        _SUBS.setdefault(uid, []).append(q)
    return q


def unsubscribe(uid: int, q: queue.Queue) -> None:
    with _SUBS_LOCK:
        lst = _SUBS.get(uid) or []
        if q in lst:
            lst.remove(q)
        if not lst:
            _SUBS.pop(uid, None)


def push(uid: int, ev: dict) -> None:
    """_live_push's rule, for _live_push's reason: a subscriber that cannot
    keep up is dropped so its socket closes and the browser reconnects,
    rather than back-pressuring the thing that fired."""
    with _SUBS_LOCK:
        subs = list(_SUBS.get(uid) or [])
    for q in subs:
        try:
            q.put_nowait(ev)
        except queue.Full:
            q.dead = True
            unsubscribe(uid, q)


# ══ catch-up: the window the process was down for ══════════════════════════

def catch_up() -> dict:
    """Replay stored 1-minute bars since each armed rule's own watermark.

    A cross that happened while this process was not running is in `bars` and
    nowhere else. Firing it late, stamped with the BAR's time and marked
    `late`, is the honest answer; skipping it silently is the failure that ends
    trust in the whole widget. Bounded to three days so a machine that was off
    for a month does not replay a month.
    """
    out = {"scanned": 0, "fired": 0, "symbols": 0}
    for symbol in watched_symbols():
        with _INDEX_LOCK:
            rules = list(_BY_SYM.get(symbol) or [])
        if not rules:
            continue
        out["symbols"] += 1
        ds._req.symbol = symbol
        for r in rules:
            if not r.last_eval_ts:
                # never evaluated: seed the sides off the newest bar rather
                # than replaying history, or a rule created below the level
                # fires for every cross that ever happened
                try:
                    _seed(r)
                except Unspeakable as exc:
                    _set_state(r.id, "paused", why=str(exc))
                continue
            try:
                rows = _rows_for(symbol, r.interval)
            except Exception:                               # noqa: BLE001
                continue
            fresh = [i for i, b in enumerate(rows) if b[0] > r.last_eval_ts]
            if not fresh:
                continue
            first = max(fresh[0], 30)
            if (len(rows) - first) > _CATCHUP_MAX_MIN:
                first = len(rows) - _CATCHUP_MAX_MIN
            for i in range(first, len(rows)):
                out["scanned"] += 1
                # every bar in a replay slice is finished by definition
                ctx = Ctx(symbol, r.interval, rows[:i + 1], False)
                try:
                    hit = evaluate(r, ctx, late=True)
                except Unspeakable as exc:
                    _set_state(r.id, "paused", why=str(exc))
                    break
                except Exception:                           # noqa: BLE001
                    break
                if hit:
                    _fire(r, hit)
                    out["fired"] += 1
                    if r.state == "fired":
                        break
            _persist_eval(r)
    if out["fired"]:
        _load_index()
    return out


def _seed(r: Rule) -> None:
    """Arm a brand-new rule against the CURRENT bar without firing.

    This is header point 1: the arming side has to be recorded, or the first
    tick after creation reads as a crossing of a level price was already past.
    """
    rows = _rows_for(r.symbol, r.interval)
    if not rows:
        raise Unspeakable(f"{r.symbol} has no {r.interval} bars to arm against")
    forming = _is_forming(r.symbol)
    # A confirmed-close rule is armed against the last CLOSED bar, so its
    # watermark is a closed bar's timestamp and the gate in _run_symbol lets it
    # run at the very next close rather than skipping one.
    if r.closed_only and forming and len(rows) > 1:
        rows, forming = rows[:-1], False
    ctx = Ctx(r.symbol, r.interval, rows, forming)
    sides = []
    for c in r.when:
        got = _eval_condition(c, {}, ctx, r)
        sides.append({"side": got["side"], "ok": 1 if got["ok"] else 0})
    r.cstate = sides
    # all_ok is seeded TRUE when the rule is already satisfied at creation, so
    # an alert armed on a condition that already holds waits for it to reset
    # instead of firing the instant it is made.
    r.all_ok = 1 if (all(s["ok"] for s in sides) if r.all
                     else any(s["ok"] for s in sides)) else 0
    r.last_eval_ts = int(ctx.bar(0)[0])
    _persist_eval(r)


# ══ validation ═════════════════════════════════════════════════════════════

def _validate(body: dict, uid: int) -> tuple[dict, str, str, str, int | None]:
    symbol = str(body.get("symbol") or "").upper().strip()
    if not symbol:
        raise Unspeakable("symbol is required")
    if ds._ensure_symbol(symbol):
        raise Unspeakable(f"{symbol} is not in the chart universe")
    interval = str(body.get("interval") or "5m").lower().strip()
    if interval not in INTERVALS:
        raise Unspeakable(f"interval '{interval}' — one of "
                          f"{', '.join(INTERVALS)}")
    freq = str(body.get("freq") or "once").lower().strip()
    if freq not in FREQS:
        raise Unspeakable(f"freq '{freq}' — one of {', '.join(FREQS)}")
    when = body.get("when")
    if not isinstance(when, list) or not when:
        raise Unspeakable("when[] needs at least one condition "
                          "{left, op, right}")
    if len(when) > MAX_CONDITIONS:
        raise Unspeakable(f"at most {MAX_CONDITIONS} conditions in one alert")
    clean = []
    for c in when:
        if not isinstance(c, dict):
            raise Unspeakable("each condition is an object {left, op, right}")
        op = str(c.get("op") or "").lower().strip()
        if op not in OPS:
            raise Unspeakable(f"unknown op '{op}'")
        if c.get("left") in (None, ""):
            raise Unspeakable(f"the {op} condition has no left address")
        if op != "is_true" and c.get("right") in (None, ""):
            raise Unspeakable(f"the {op} condition has no right address")
        if op in _BAND_OPS and c.get("right2") in (None, ""):
            raise Unspeakable(f"{op} needs a band — give right and right2")
        out = {"left": c.get("left"), "op": op}
        for k in ("right", "right2", "x", "plus_pct", "within"):
            if c.get(k) not in (None, ""):
                out[k] = c[k]
        if op in _MOVE_OPS and "within" not in out:
            out["within"] = 1
        clean.append(out)
    spec = {"when": clean, "all": bool(body.get("all", True))}
    if body.get("vp_sessions"):
        spec["vp_sessions"] = int(body["vp_sessions"])
    expires = body.get("expires")
    expires = int(expires) if expires else None
    if expires and expires <= int(time.time()):
        raise Unspeakable("expires is in the past")
    return spec, symbol, interval, freq, expires


# ══ the HTTP surface ═══════════════════════════════════════════════════════
# GET/POST only: dataserver's Handler implements those two and OPTIONS, and
# `layouts` already models delete-by-body-flag. Following it beats teaching the
# server a verb for one feature.

def _row_public(row: tuple) -> dict:
    r = Rule(row)
    out = {"id": r.id, "symbol": r.symbol, "interval": r.interval,
           "state": r.state, "freq": r.freq, "note": r.note,
           "created": r.created, "expires": r.expires,
           "when": r.when, "all": r.all,
           "fired_at": row[14], "fired_value": row[15],
           "fire_count": row[16], **r.label()}
    return out


_LIST_COLS = ("id,user_id,symbol,interval,spec,freq,state,note,created,"
              "expires,cstate,all_ok,last_eval_ts,last_fired_bkt,"
              "fired_at,fired_value,fire_count")


def api_list(uid: int) -> tuple[int, dict]:
    with ds._users_lock:
        rows = _db().execute(
            f"SELECT {_LIST_COLS} FROM alerts WHERE user_id=? "
            "ORDER BY created DESC", (uid,)).fetchall()
        logs = _db().execute(
            "SELECT id,ts,symbol,interval,verb,level,value,meta,late,seen,"
            "alert_id FROM alert_log WHERE user_id=? ORDER BY ts DESC "
            "LIMIT ?", (uid, MAX_LOG_ROWS)).fetchall()
    out_alerts = []
    for row in rows:
        try:
            out_alerts.append(_row_public(row))
        except Exception:                                   # noqa: BLE001
            continue
    return 200, {
        "alerts": out_alerts,
        "log": [{"id": l[0], "ts": l[1], "symbol": l[2], "interval": l[3],
                 "verb": l[4], "level": l[5], "value": l[6], "meta": l[7],
                 "late": bool(l[8]), "seen": bool(l[9]), "alert_id": l[10]}
                for l in logs],
        "unseen": sum(1 for l in logs if not l[9]),
        "feed": feed_health(),
        "vocab": {"operands": OPERANDS, "ops": list(OPS),
                  "intervals": list(INTERVALS), "frequencies": list(FREQS)},
    }


def api_create(uid: int, body: dict) -> tuple[int, dict]:
    with ds._users_lock:
        n = _db().execute("SELECT COUNT(*) FROM alerts WHERE user_id=? AND "
                          "state!='fired'", (uid,)).fetchone()[0]
    if n >= MAX_PER_USER:
        return 400, {"error": f"you already hold {n} alerts — the ceiling is "
                              f"{MAX_PER_USER}. Delete one first"}
    try:
        spec, symbol, interval, freq, expires = _validate(body, uid)
    except Unspeakable as exc:
        return 400, vocab(str(exc))
    now = int(time.time())
    with ds._users_lock:
        cur = _db().execute(
            "INSERT INTO alerts (user_id,symbol,interval,spec,freq,state,note,"
            "created,expires,cstate,all_ok) VALUES (?,?,?,?,?,'armed',?,?,?,"
            "'[]',0)",
            (uid, symbol, interval, json.dumps(spec), freq,
             str(body.get("note") or "")[:400], now, expires))
        aid = cur.lastrowid
        _db().commit()
    # Arm it before it is indexed: a rule that enters _BY_SYM unseeded could be
    # evaluated by the very next tick with no side recorded (header point 1).
    with ds._users_lock:
        row = _db().execute(f"SELECT {_LIST_COLS} FROM alerts WHERE id=?",
                            (aid,)).fetchone()
    r = Rule(row)
    ds._req.symbol = symbol
    try:
        _seed(r)
    except Unspeakable as exc:
        with ds._users_lock:
            _db().execute("DELETE FROM alerts WHERE id=?", (aid,))
            _db().commit()
        return 400, vocab(str(exc))
    _load_index()
    ensure_feed(symbol)
    with ds._users_lock:
        row = _db().execute(f"SELECT {_LIST_COLS} FROM alerts WHERE id=?",
                            (aid,)).fetchone()
    return 200, {"alert": _row_public(row), "feed": feed_health(symbol)}


def api_patch(uid: int, aid: int, body: dict) -> tuple[int, dict]:
    with ds._users_lock:
        row = _db().execute(f"SELECT {_LIST_COLS} FROM alerts WHERE id=? AND "
                            "user_id=?", (aid, uid)).fetchone()
    if not row:
        return 404, {"error": "no such alert"}
    if body.get("delete"):
        with ds._users_lock:
            _db().execute("DELETE FROM alerts WHERE id=? AND user_id=?",
                          (aid, uid))
            _db().commit()
        _load_index()
        return 200, {"deleted": aid}

    cur = Rule(row)
    sets, args = [], []
    if "state" in body:
        want = str(body["state"]).lower()
        if want not in STATES:
            return 400, {"error": f"state — one of {', '.join(STATES)}"}
        sets.append("state=?")
        args.append(want)
        if want == "armed":
            # re-arming after a pause or a fire must re-seed, or it inherits a
            # side that is now months old
            sets += ["cstate=?", "all_ok=?", "last_eval_ts=?"]
            args += ["[]", 0, 0]
    if "note" in body:
        sets.append("note=?")
        args.append(str(body["note"] or "")[:400])
    if "expires" in body:
        exp = body["expires"]
        sets.append("expires=?")
        args.append(int(exp) if exp else None)
    if "freq" in body:
        if str(body["freq"]).lower() not in FREQS:
            return 400, {"error": f"freq — one of {', '.join(FREQS)}"}
        sets.append("freq=?")
        args.append(str(body["freq"]).lower())
    if "when" in body or "interval" in body or "all" in body:
        merged = {"symbol": cur.symbol,
                  "interval": body.get("interval") or cur.interval,
                  "freq": body.get("freq") or cur.freq,
                  "when": body.get("when") or cur.when,
                  "all": body.get("all", cur.all)}
        try:
            spec, _sym, interval, _f, _e = _validate(merged, uid)
        except Unspeakable as exc:
            return 400, vocab(str(exc))
        sets += ["spec=?", "interval=?", "cstate=?", "all_ok=?",
                 "last_eval_ts=?"]
        args += [json.dumps(spec), interval, "[]", 0, 0]
    if not sets:
        return 400, {"error": "nothing to change"}
    with ds._users_lock:
        _db().execute(f"UPDATE alerts SET {', '.join(sets)} WHERE id=? AND "
                      "user_id=?", (*args, aid, uid))
        _db().commit()
    with ds._users_lock:
        row = _db().execute(f"SELECT {_LIST_COLS} FROM alerts WHERE id=?",
                            (aid,)).fetchone()
    r = Rule(row)
    if r.state == "armed" and not r.last_eval_ts:
        ds._req.symbol = r.symbol
        try:
            _seed(r)
        except Unspeakable as exc:
            _set_state(aid, "paused", why=str(exc))
    _load_index()
    if r.state == "armed":
        ensure_feed(r.symbol)
    with ds._users_lock:
        row = _db().execute(f"SELECT {_LIST_COLS} FROM alerts WHERE id=?",
                            (aid,)).fetchone()
    return 200, {"alert": _row_public(row)}


def api_seen(uid: int) -> tuple[int, dict]:
    with ds._users_lock:
        _db().execute("UPDATE alert_log SET seen=1 WHERE user_id=? AND seen=0",
                      (uid,))
        _db().commit()
    return 200, {"unseen": 0}


def api_check(uid: int, body: dict) -> tuple[int, dict]:
    """Resolve a rule WITHOUT arming it — what the create dialog previews.

    The dialog can then say "right now: close 1,412.40 vs 1,420.00" instead of
    accepting an address and only discovering at 09:20 that it never resolved.
    """
    try:
        spec, symbol, interval, freq, expires = _validate(body, uid)
    except Unspeakable as exc:
        return 400, vocab(str(exc))
    ds._req.symbol = symbol
    rows = _rows_for(symbol, interval)
    if not rows:
        return 400, {"error": f"{symbol} has no {interval} bars"}
    fake = Rule((0, uid, symbol, interval, json.dumps(spec), freq, "armed", "",
                 int(time.time()), expires, "[]", 0, 0, 0))
    # The preview shows what the rule sees RIGHT NOW, forming bar included —
    # that is the honest answer to "what is it looking at", even for a rule that
    # will only ever act on a closed bar.
    ctx = Ctx(symbol, interval, rows, _is_forming(symbol))
    out = []
    for c in fake.when:
        try:
            got = _eval_condition(c, {}, ctx, fake)
        except Unspeakable as exc:
            return 400, vocab(str(exc))
        out.append({"left": c["left"], "op": c["op"],
                    "value": round(got["value"], 6),
                    "target": round(got["target"], 6),
                    "true_now": bool(got["ok"])})
    return 200, {"ok": True, "conditions": out, **fake.label(),
                 "feed": feed_health(symbol),
                 "already_true": all(c["true_now"] for c in out) if fake.all
                                 else any(c["true_now"] for c in out)}


# ══ feed health — §4.2 made visible ════════════════════════════════════════

def feed_health(symbol: str = "") -> dict:
    """Whether the thing that fires alerts is actually receiving prices.

    A watcher whose feed died at 06:00 and says nothing is worse than no
    watcher, and charto's Kite session expires daily. This is the fact the UI
    needs to be able to say "not being watched right now" with.
    """
    streams = ds._live_status()
    live: set[str] = set()
    for st in streams.values():
        for s in (st.get("symbols") or []):
            live.add(s)
    out = {"streams": {v: {"connected": bool(s.get("connected")),
                           "symbols": len(s.get("symbols") or []),
                           "last_tick_age_s": s.get("last_tick_age_s"),
                           "error": s.get("error")}
                       for v, s in streams.items()},
           "watched": len(watched_symbols()),
           "live_symbols": len(live),
           "engine": dict(STATS)}
    if symbol:
        sym = symbol.upper()
        out["symbol"] = {
            "symbol": sym,
            "streaming": sym in live,
            "has_minutes": _has_minutes(sym),
            # One short sentence the UI prints verbatim, rather than composing
            # it from three booleans and getting it subtly wrong.
            "note": ("Watched live." if sym in live else
                     "Not watched live yet — no price feed is connected. "
                     "It will be checked when one is."
                     if _has_minutes(sym) else
                     "This symbol has daily bars only, so it is checked at the "
                     "end of each day rather than live."),
        }
    return out


def _has_minutes(symbol: str) -> bool:
    try:
        return ds._con.execute("SELECT 1 FROM bars WHERE symbol=? LIMIT 1",
                               (symbol,)).fetchone() is not None
    except Exception:                                       # noqa: BLE001
        return False


def ensure_feed(symbol: str) -> dict:
    """Ask the running venue driver to cover a symbol an alert now needs.

    Kite's adapter fixes its symbol set at construction, so this cannot
    subscribe into a live socket. It reports the truth instead of pretending —
    the create response carries it, and the UI says it.
    """
    sym = symbol.upper()
    streams = ds._live_status()
    for st in streams.values():
        if sym in (st.get("symbols") or []):
            return {"streaming": True}
    return {"streaming": False, "has_minutes": _has_minutes(sym)}


# ══ lifecycle ══════════════════════════════════════════════════════════════

def register_hook() -> None:
    """Attach to the tick seam. Separate from start() so the hook can be in
    place before catch_up() runs — a cross that happens DURING the replay
    should be seen by the live path, not fall between the two."""
    ds.register_bar_hook(on_bar)


def start(catchup: bool = True) -> dict:
    """Called once, from dataserver's boot block, after the module alias."""
    global _WORKER
    _init_db()
    _load_index()
    if _WORKER is None or not _WORKER.is_alive():
        _STOP.clear()
        _WORKER = threading.Thread(target=_worker, name="alerts",
                                  daemon=True)
        _WORKER.start()
    STATS["started"] = int(time.time())
    got = {"armed": sum(len(v) for v in _BY_SYM.values()),
           "symbols": len(_BY_SYM)}
    if catchup:
        try:
            got["catch_up"] = catch_up()
        except Exception as exc:                            # noqa: BLE001
            log.warning("alerts: catch-up failed: %s", exc)
            got["catch_up"] = {"error": str(exc)}
    return got


def stop() -> None:
    _STOP.set()


# ══ the chat surface ═══════════════════════════════════════════════════════
# The model composes; this engine owns the firing. CHARTO.md §3's line exactly.

def tool_set_alert(symbol: str = "", interval: str = "5m",
                   when: list | None = None, all: bool = True,
                   freq: str = "once", expires_in_days: int = 0,
                   note: str = "", user_id: int = 0) -> dict:
    if not user_id:
        return {"error": "alerts need an account",
                "_note": ("Say the user must sign in to create alerts — they "
                          "run on the server so they can fire while the "
                          "browser is closed. Do not offer a local one.")}
    expires = (int(time.time()) + int(expires_in_days) * 86400
               if expires_in_days else None)
    code, out = api_create(user_id, {
        "symbol": symbol or ds._sym(), "interval": interval, "when": when or [],
        "all": all, "freq": freq, "expires": expires, "note": note})
    if code != 200:
        return out
    a = out["alert"]
    return {"alert": a, "_render_hint": "alert_card",
            "_note": (f"Armed: {a['cond']} {a['level']} on {a['symbol']} "
                      f"{a['interval']} ({a['meta']}). "
                      + (out.get("feed", {}).get("symbol", {}).get("note") or ""))}


def tool_list_alerts(user_id: int = 0) -> dict:
    if not user_id:
        return {"error": "alerts need an account"}
    _code, out = api_list(user_id)
    return {"alerts": out["alerts"], "log": out["log"][:20],
            "unseen": out["unseen"]}


def tool_cancel_alert(alert_id: int = 0, user_id: int = 0) -> dict:
    if not user_id:
        return {"error": "alerts need an account"}
    code, out = api_patch(user_id, int(alert_id), {"delete": True})
    return out if code == 200 else out
