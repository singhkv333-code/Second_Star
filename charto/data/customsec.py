"""Custom securities: a basket, a ratio or a spread the trader names, served
as a symbol like any other (@DEFENCE, @HDFC_ICICI).

A security is a thing you HOLD and PRICE; a play is a rule that ACTS. So a
custom security is chartable, has a value series, and a play can watch it or
buy it, but it never acts on its own. The model writes the definition; code
prices it.

  basket   value = base × Σ wᵢ × Pᵢ(t) / Pᵢ(anchor)     (buy-and-hold weights)
  ratio    value = A(t) / B(t)
  spread   value = A(t) − k × B(t)

The anchor prices are fixed when it is created (each member's daily close on
the anchor day), so a basket's value never depends on which window of bars
was asked for: the chart, a play's replay and its live bar all read the same
number. High and low of a basket bar are the weighted members' highs and
lows, an upper and lower bound rather than a traded price, and the payload
says so.
"""
from __future__ import annotations

import json
import re
import threading
import time

ds = None
_cache: dict[str, dict] = {}
_lock = threading.Lock()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS custom_securities (
  symbol   TEXT PRIMARY KEY,               -- @NAME, unique across the platform
  user_id  INTEGER NOT NULL REFERENCES users(id),
  title    TEXT NOT NULL,
  spec     TEXT NOT NULL,
  created  INTEGER NOT NULL,
  updated  INTEGER NOT NULL);
"""
_ready = False
KINDS = ("basket", "ratio", "spread")


class Bad(ValueError):
    pass


def bind(module) -> None:
    global ds
    ds = module


def init_db() -> None:
    global _ready
    if _ready:
        return
    with ds._users_lock:
        ds._users.executescript(_SCHEMA)
        ds._users.commit()
    _ready = True


def is_custom(symbol: str) -> bool:
    return str(symbol or "").startswith("@")


def get(symbol: str) -> dict | None:
    sym = str(symbol or "").upper()
    with _lock:
        if sym in _cache:
            return _cache[sym]
    init_db()
    with ds._users_lock:
        r = ds._users.execute("SELECT user_id, title, spec, created FROM custom_securities WHERE symbol=?",
                              (sym,)).fetchone()
    if not r:
        return None
    sec = {**json.loads(r[2]), "symbol": sym, "user_id": r[0], "title": r[1], "created": r[3]}
    with _lock:
        _cache[sym] = sec
    return sec


def members(symbol: str) -> list[str]:
    sec = get(symbol)
    return [m["symbol"] for m in sec["members"]] if sec else []


def list_for(uid: int) -> list[dict]:
    init_db()
    with ds._users_lock:
        rows = ds._users.execute("SELECT symbol FROM custom_securities WHERE user_id=? ORDER BY updated DESC",
                                 (uid,)).fetchall()
    return [get(r[0]) for r in rows]


def _daily_close_on_or_before(sym: str, ts: int) -> tuple[int, float] | None:
    d = ds.get_bars(sym, "1d", ts + 86400, 10)
    rows = [b for b in d.get("bars") or [] if b["t"] <= ts + 86400]
    return (rows[-1]["t"], float(rows[-1]["c"])) if rows else None


def define(uid: int, raw: dict) -> dict:
    """Create or replace one of the trader's securities. Returns the stored
    definition, with its anchor prices."""
    init_db()
    if not isinstance(raw, dict):
        raise Bad("security must be {name, title, kind, members, ...}")
    name = re.sub(r"[^A-Z0-9_]+", "_", str(raw.get("name") or raw.get("symbol") or "").upper().lstrip("@")).strip("_")
    if not name or len(name) > 24:
        raise Bad("security name: letters, digits and _, up to 24, e.g. DEFENCE or HDFC_ICICI")
    sym = "@" + name
    kind = str(raw.get("kind") or "basket").lower()
    if kind not in KINDS:
        raise Bad(f"security kind must be one of {', '.join(KINDS)}")
    mem = []
    for m in raw.get("members") or []:
        s = ds.canon_symbol(str(m.get("symbol") if isinstance(m, dict) else m))
        if is_custom(s):
            raise Bad("a custom security cannot hold another custom security yet")
        w = float((m.get("weight") if isinstance(m, dict) else None) or 0)
        mem.append({"symbol": s, "weight": w})
    if kind in ("ratio", "spread") and len(mem) != 2:
        raise Bad(f"a {kind} has exactly two members: [A, B]")
    if kind == "basket" and not 2 <= len(mem) <= 30:
        raise Bad("a basket has 2 to 30 members")
    missing = [m["symbol"] for m in mem if ds._ensure_symbol(m["symbol"])]
    if missing:
        raise Bad(f"no bars stored for {', '.join(missing)}; leave them out or name a listed instrument")
    if kind == "basket":
        tot = sum(max(0.0, m["weight"]) for m in mem)
        if tot <= 0:
            for m in mem:
                m["weight"] = 1.0 / len(mem)
        else:
            for m in mem:
                m["weight"] = max(0.0, m["weight"]) / tot
        # the anchor: the latest daily close every member has, a year back if
        # they all have one, so the value starts at `base` on a real day
        want = int(time.time()) - 365 * 86400
        anchor = None
        for m in mem:
            got = _daily_close_on_or_before(m["symbol"], want)
            if not got:
                got = (ds.get_bars(m["symbol"], "1d", None, 400)["bars"][0]["t"], None)
            anchor = max(anchor or 0, got[0])
        for m in mem:
            got = _daily_close_on_or_before(m["symbol"], anchor)
            if not got:
                raise Bad(f"{m['symbol']} has no daily close on the anchor day")
            m["anchor_px"] = got[1]
        spec = {"kind": kind, "members": mem, "base": float(raw.get("base") or 100),
                "anchor": anchor}
    else:
        spec = {"kind": kind, "members": mem, "k": float(raw.get("k") or 1.0)}
    title = str(raw.get("title") or name.replace("_", " ").title())[:60]
    now = int(time.time())
    with ds._users_lock:
        owner = ds._users.execute("SELECT user_id FROM custom_securities WHERE symbol=?", (sym,)).fetchone()
        if owner and owner[0] != uid:
            raise Bad(f"{sym} is taken; choose another name")
        ds._users.execute(
            "INSERT INTO custom_securities (symbol, user_id, title, spec, created, updated) VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(symbol) DO UPDATE SET title=excluded.title, spec=excluded.spec, updated=excluded.updated",
            (sym, uid, title, json.dumps(spec), now, now))
        ds._users.commit()
    with _lock:
        _cache.pop(sym, None)
    return get(sym)


def bars(symbol: str, interval: str, to: int | None, limit: int) -> dict:
    """The security's OHLC on the given interval, built from its members' bars
    on the first member's bar times (others carried causally, at most a few
    bars). A bar with a member missing is left out."""
    sec = get(symbol)
    if not sec:
        return {"bars": [], "error": f"no custom security {symbol}"}
    import custom_indicators
    mem = sec["members"]
    pad = limit + 50

    def rows_of(sym, upto):
        return [(b["t"], b["o"], b["h"], b["l"], b["c"], b["v"])
                for b in ds.get_bars(sym, interval, upto, pad).get("bars") or []]
    base_rows = rows_of(mem[0]["symbol"], to)
    if not base_rows:
        return {"bars": [], "custom": True}
    # the others over the SAME window: a member whose store runs later than
    # the first one's must not hand back bars the clock never reaches
    rows = {mem[0]["symbol"]: base_rows,
            **{m["symbol"]: rows_of(m["symbol"], base_rows[-1][0] + 1) for m in mem[1:]}}
    al = custom_indicators.align_basket(base_rows, rows)
    cols = {s: {k: al[k][j] for k in ("open", "high", "low", "close")} for j, s in enumerate(al["symbols"])}
    out = []
    for i, r in enumerate(base_rows):
        vals = [cols[m["symbol"]] for m in mem]
        if any(v["close"][i] is None for v in vals):
            continue
        if sec["kind"] == "basket":
            f = lambda k: sec["base"] * sum(m["weight"] * v[k][i] / m["anchor_px"] for m, v in zip(mem, vals))
            o, h, l, c = f("open"), f("high"), f("low"), f("close")
        elif sec["kind"] == "ratio":
            a, b = vals
            if not b["close"][i] or not b["open"][i]:
                continue
            o, c = a["open"][i] / b["open"][i], a["close"][i] / b["close"][i]
            h = max(o, c, a["high"][i] / (b["high"][i] or b["close"][i]))
            l = min(o, c, a["low"][i] / (b["low"][i] or b["close"][i]))
        else:
            a, b = vals
            k = sec["k"]
            o, c = a["open"][i] - k * b["open"][i], a["close"][i] - k * b["close"][i]
            h, l = max(o, c, a["high"][i] - k * b["high"][i]), min(o, c, a["low"][i] - k * b["low"][i])
        out.append({"t": r[0], "o": round(o, 6), "h": round(max(h, o, c), 6), "l": round(min(l, o, c), 6),
                    "c": round(c, 6), "v": 0})
    return {"bars": out[-limit:], "has_more": len(out) > limit, "custom": True,
            "note": "a custom security: high and low are bounds from its members, not traded prices"}


def describe(sec: dict) -> str:
    m = sec["members"]
    if sec["kind"] == "ratio":
        return f"{m[0]['symbol']} ÷ {m[1]['symbol']}"
    if sec["kind"] == "spread":
        return f"{m[0]['symbol']} − {sec['k']:g} × {m[1]['symbol']}"
    return " + ".join(f"{x['weight'] * 100:.0f}% {x['symbol']}" for x in m)


def card(sec: dict) -> dict:
    d = bars(sec["symbol"], "1d", None, 260)["bars"]
    closes = [b["c"] for b in d]

    def chg(n):
        return round((closes[-1] / closes[-1 - n] - 1) * 100, 2) if len(closes) > n and closes[-1 - n] else None
    return {"kind": "custom_security", "symbol": sec["symbol"], "title": sec["title"], "type": sec["kind"],
            "formula": describe(sec), "now": closes[-1] if closes else None,
            "as_of": d[-1]["t"] if d else None,
            "change": {"1d": chg(1), "1m": chg(21), "1y": chg(250)},
            "members": [{"symbol": m["symbol"], "weight": m.get("weight")} for m in sec["members"]],
            "spark": [round(x, 4) for x in closes[-120:]],
            "base": sec.get("base"), "anchor": sec.get("anchor")}
