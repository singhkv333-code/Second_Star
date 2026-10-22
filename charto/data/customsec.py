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


RANKS = ("market_cap", "turnover", "return_1y", "roe", "low_volatility")
WEIGHTS = ("equal", "market_cap", "inverse_vol")
MAX_PICK_WEIGHT = 0.4


def _norm(x) -> str:
    return re.sub(r"[^a-z0-9]", "", str(x or "").lower())


def _matches(query: str, names) -> bool:
    """Every word of the query appears in one of the names, as a substring of
    the run-together name or as a near spelling of one of its words (defence,
    defense), so the trader's words find the classification's."""
    import difflib
    joined = " ".join(_norm(n) for n in names if n)
    words = re.findall(r"[a-z0-9]+", " ".join(str(n or "").lower() for n in names))
    for q in re.findall(r"[a-z0-9]+", query.lower()):
        if len(q) < 3 or q in ("and", "the"):
            continue
        if q in joined:
            continue
        if not any(difflib.SequenceMatcher(None, q, w).ratio() >= 0.85 for w in words + joined.split()):
            if not any(difflib.SequenceMatcher(None, q, joined[i:i + len(q)]).ratio() >= 0.85
                       for i in range(0, max(1, len(joined) - len(q) + 1))):
                return False
    return True


def _daily_stats(sym: str) -> dict:
    """60-day average turnover (rupees a day), annualised volatility of daily
    returns, and the 1-year return, from the stored daily bars."""
    b = ds.get_bars(sym, "1d", None, 260).get("bars") or []
    if len(b) < 20:
        return {}
    last = b[-60:]
    turn = sum(x["c"] * x["v"] for x in last) / len(last)
    rets = [last[i]["c"] / last[i - 1]["c"] - 1 for i in range(1, len(last)) if last[i - 1]["c"]]
    mu = sum(rets) / len(rets)
    vol = (sum((r - mu) ** 2 for r in rets) / max(1, len(rets) - 1)) ** 0.5 * 252 ** 0.5
    ret = (b[-1]["c"] / b[0]["c"] - 1) if len(b) >= 240 and b[0]["c"] else None
    return {"turnover": turn, "vol": vol, "return_1y": ret, "as_of": b[-1]["t"]}


def _cr(x: float) -> str:
    cr = x / 1e7
    return f"₹{cr / 1e5:,.2f} lakh cr" if cr >= 1e5 else f"₹{cr:,.0f} cr"


def pick(raw: dict) -> tuple[list[dict], dict]:
    """Members chosen by a stated method, run by code on the stored data, so
    the same method on the same day gives the same basket and every member
    carries the numbers that put it there. The model chooses the method (which
    industry or sector, ranked by what, how many, weighted how); it never
    chooses the names."""
    if not isinstance(raw, dict):
        raise Bad("pick is {industry or sector, rank_by, top, weight}")
    ind = [str(x) for x in (raw.get("industry") if isinstance(raw.get("industry"), list)
                            else [raw.get("industry")]) if _norm(x)]
    sec = _norm(raw.get("sector"))
    if not ind and not sec:
        raise Bad("pick needs an industry (e.g. Aerospace & Defense, Banks - Regional) or a sector "
                  "(e.g. Technology, Healthcare)")
    rank_by = str(raw.get("rank_by") or "market_cap")
    if rank_by not in RANKS:
        raise Bad(f"pick.rank_by must be one of {', '.join(RANKS)}")
    weight = str(raw.get("weight") or "equal")
    if weight not in WEIGHTS:
        raise Bad(f"pick.weight must be one of {', '.join(WEIGHTS)}")
    top = int(raw.get("top") or 5)
    if not 2 <= top <= 30:
        raise Bad("pick.top is 2 to 30")
    min_turn = float(raw.get("min_turnover_cr") or 0) * 1e7
    rows = ds._con.execute(
        "SELECT c.symbol, COALESCE(c.label, c.industry), c.industry, c.industry_mc, c.sector, "
        "p.market_cap, p.roe, COALESCE(p.long_name, c.name) FROM classification c "
        "LEFT JOIN company_profile p USING(symbol)").fetchall()
    pool = []
    for sym, label, slug, mc, sector, cap, roe, nm in rows:
        if ind and not any(_matches(q, (label, slug, mc)) for q in ind):
            continue
        if sec and sec != _norm(sector):
            continue
        pool.append({"symbol": sym, "industry": label, "sector": sector, "market_cap": cap, "roe": roe,
                     "name": nm})
    if not pool:
        import difflib
        labels = sorted({r[1] for r in rows if r[1]})
        near = difflib.get_close_matches(str(raw.get("industry") or raw.get("sector") or ""), labels, 6, 0.3)
        sectors = sorted({r[4] for r in rows if r[4]})
        raise Bad("no listed company matches that " + ("industry" if ind else "sector") +
                  (f"; nearest industries: {', '.join(near)}" if near else "") +
                  f"; sectors are {', '.join(sectors)}")
    for c in pool:
        c.update(_daily_stats(c["symbol"]))
    eligible = [c for c in pool if c.get("turnover") and c["turnover"] >= min_turn]
    dropped = len(pool) - len(eligible)
    keyf = {"market_cap": lambda c: c.get("market_cap"), "turnover": lambda c: c.get("turnover"),
            "return_1y": lambda c: c.get("return_1y"), "roe": lambda c: c.get("roe"),
            "low_volatility": lambda c: -c["vol"] if c.get("vol") else None}[rank_by]
    ranked = sorted([c for c in eligible if keyf(c) is not None], key=lambda c: (-keyf(c), c["symbol"]))
    chosen = ranked[:top]
    nodata = sorted(c["symbol"] for c in eligible if keyf(c) is None)
    if len(chosen) < 2:
        raise Bad(f"only {len(chosen)} of {len(pool)} matching companies have the data to rank by "
                  f"{rank_by}; loosen the filter or rank by market_cap")
    if weight == "market_cap" and all(c.get("market_cap") for c in chosen):
        raw_w = [c["market_cap"] for c in chosen]
    elif weight == "inverse_vol" and all(c.get("vol") for c in chosen):
        raw_w = [1 / c["vol"] for c in chosen]
    else:
        raw_w = [1.0] * len(chosen)
    w = _capped([x / sum(raw_w) for x in raw_w], MAX_PICK_WEIGHT if len(chosen) * MAX_PICK_WEIGHT >= 1 else 1)
    what = {"market_cap": "market cap", "turnover": "60-day turnover", "return_1y": "1-year return",
            "roe": "return on equity", "low_volatility": "lowest volatility"}[rank_by]
    mem = []
    for i, (c, wi) in enumerate(zip(chosen, w)):
        bits = [f"#{i + 1} of {len(ranked)} by {what}"]
        if c.get("market_cap"):
            bits.append(f"market cap {_cr(c['market_cap'])}")
        bits.append(f"trades {_cr(c['turnover'])} a day")
        if c.get("return_1y") is not None:
            bits.append(f"1y {c['return_1y'] * 100:+.0f}%")
        if rank_by == "roe" and c.get("roe") is not None:
            bits.append(f"ROE {c['roe']:.1f}%")
        if rank_by == "low_volatility" or weight == "inverse_vol":
            bits.append(f"volatility {c['vol'] * 100:.0f}%")
        mem.append({"symbol": c["symbol"], "weight": round(wi, 6), "why": "; ".join(bits),
                    "industry": c["industry"]})
    filt = (", ".join(sorted({c["industry"] for c in pool}))[:120] if ind else pool[0]["sector"])
    method = {"filter": filt, "rank_by": rank_by, "top": top, "weight": weight,
              "min_turnover_cr": min_turn / 1e7 or None, "universe": len(pool), "eligible": len(eligible), "no_data": nodata,
              "as_of": max((c.get("as_of") or 0) for c in chosen),
              "text": (f"{len(pool)} listed {filt} companies"
                       + (f", {dropped} left out for trading under ₹{min_turn / 1e7:,.0f} cr a day" if dropped else "")
                       + (f", {', '.join(nodata[:6])} left out for having no {what} on file" if nodata else "")
                       + f"; the top {len(chosen)} by {what}, weighted "
                       + {"equal": "equally", "market_cap": "by market cap",
                          "inverse_vol": "inversely to volatility"}[weight]
                       + (f", no name above {MAX_PICK_WEIGHT * 100:.0f}%" if weight != "equal" else ""))}
    return mem, method


def _capped(w: list[float], cap: float) -> list[float]:
    """Weights summing to one with none above `cap`: the excess is handed to
    the uncapped names in proportion, repeated until nothing is over."""
    w = list(w)
    for _ in range(len(w)):
        over = [i for i, x in enumerate(w) if x > cap + 1e-12]
        if not over:
            break
        spare = sum(w[i] - cap for i in over)
        for i in over:
            w[i] = cap
        free = [i for i, x in enumerate(w) if x < cap - 1e-12]
        tot = sum(w[i] for i in free)
        for i in free:
            w[i] += spare * w[i] / tot if tot else spare / len(free)
    return w


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
    mem, method = [], None
    if raw.get("pick"):
        if kind != "basket":
            raise Bad("pick builds a basket; a ratio or spread names its two members")
        mem, method = pick(raw["pick"])
    for m in ([] if method else raw.get("members") or []):
        s = ds.canon_symbol(str(m.get("symbol") if isinstance(m, dict) else m))
        if is_custom(s):
            raise Bad("a custom security cannot hold another custom security yet")
        w = float((m.get("weight") if isinstance(m, dict) else None) or 0)
        why = str((m.get("why") if isinstance(m, dict) else "") or "").strip()[:200]
        ind = str((m.get("industry") if isinstance(m, dict) else "") or "")[:60]
        mem.append({"symbol": s, "weight": w, **({"why": why} if why else {}),
                    **({"industry": ind} if ind else {})})
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
    spec["why"] = str(raw.get("why") or "").strip()[:400]
    if method:
        spec["method"] = method
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


def edit(uid: int, symbol: str, body: dict) -> dict:
    """The trader's own changes to a security: new weights (percent, the
    others scaled to make room), a member removed or added. The anchor is set
    again, so the series is the new basket's from its anchor day on; a picked
    basket keeps its method and says the weights were changed by hand."""
    sec = get(symbol)
    if not sec or sec["user_id"] != uid:
        raise Bad(f"no custom security {symbol} on this account")
    mem = [dict(m) for m in sec["members"]]
    drop = {ds.canon_symbol(str(x)) for x in body.get("remove") or []}
    mem = [m for m in mem if m["symbol"] not in drop]
    for x in body.get("add") or []:
        s = ds.canon_symbol(str(x.get("symbol") if isinstance(x, dict) else x))
        if s and s not in {m["symbol"] for m in mem}:
            mem.append({"symbol": s, "weight": 1.0 / max(1, len(mem)), "why": "added by you"})
    for s, w in (body.get("weights") or {}).items():
        s = ds.canon_symbol(s)
        m = next((m for m in mem if m["symbol"] == s), None)
        if not m:
            raise Bad(f"{s} is not in {symbol}")
        w = float(w) / 100
        if not 0 < w < 1:
            raise Bad("a weight is a percent between 0 and 100")
        rest = sum(x["weight"] for x in mem if x is not m)
        for x in mem:
            x["weight"] = w if x is m else x["weight"] / rest * (1 - w) if rest else (1 - w) / (len(mem) - 1)
    raw = {"name": symbol, "title": body.get("title") or sec["title"], "kind": sec["kind"],
           "members": mem, "why": sec.get("why") or "", "k": sec.get("k"), "base": sec.get("base")}
    out = define(uid, raw)
    if sec.get("method"):
        spec = {k: out[k] for k in out if k not in ("symbol", "user_id", "title", "created")}
        spec["method"] = {**sec["method"], "edited": True}
        with ds._users_lock:
            ds._users.execute("UPDATE custom_securities SET spec=? WHERE symbol=?", (json.dumps(spec), out["symbol"]))
            ds._users.commit()
        with _lock:
            _cache.pop(out["symbol"], None)
        out = get(out["symbol"])
    return out


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
            "members": [{"symbol": m["symbol"], "weight": m.get("weight"), "why": m.get("why"),
                         "industry": m.get("industry")} for m in sec["members"]],
            "why": sec.get("why") or "", "method": sec.get("method"),
            "spark": [round(x, 4) for x in closes[-120:]],
            "base": sec.get("base"), "anchor": sec.get("anchor")}
