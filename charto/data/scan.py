"""Scans: the model's own measures and condition run across a universe, the
shortlist ranked against the rest. The same language a play is written in,
pointed at many symbols at once.

    universe  which symbols: all stored stocks, F&O stocks, Hyperliquid perps,
              an industry or sector, a basket, a list, or a SELECT of symbols,
              narrowed by liquidity and capped by market cap or turnover
    measures  series written by the model (play.measures), computed for every
              symbol in ONE sandbox run
    when      the scan clause (play.when): kept are the symbols it holds for on
              the latest bar, or that it turned true for within `fresh` bars
    rank_by   a measure or field, ranked across the symbols that have it; each
              row carries its percentile among them

Nothing here decides what to look for. Code fetches, runs, counts, sorts and
says what was left out; the model chooses the universe, writes the measures
and the clause, and explains the shortlist. A scan that a play then watches
is play.universe: the shortlist's symbols, each its own clock.
"""
from __future__ import annotations

import math
import time

import customsec
import hyperliquid

ds = None
MAX_SYMBOLS = 600
LOOKBACK = {"1d": 300, "1h": 400, "30m": 400, "15m": 400, "5m": 400, "3m": 400, "1m": 400}


def bind(module) -> None:
    global ds
    ds = module


class Bad(ValueError):
    pass


# ── the universe ─────────────────────────────────────────────────────────────

def _classified() -> list[tuple]:
    return ds._con.execute(
        "SELECT c.symbol, COALESCE(c.label, c.industry), c.sector, p.market_cap "
        "FROM classification c LEFT JOIN company_profile p USING(symbol)").fetchall()


def resolve(u, uid: int | None = None) -> tuple[str, list[str], list[str]]:
    """(the universe in words, its symbols, what was said about it)."""
    if u in (None, ""):
        u = {"from": "all"}
    if isinstance(u, (list, tuple)):
        u = {"from": list(u)}
    if isinstance(u, str):
        u = {"from": u}
    if not isinstance(u, dict):
        raise Bad("universe is all | fno | perps | @BASKET | [symbols] or {from, industry, sector, sql, "
                  "min_turnover_cr, top_by, n}")
    src, said = u.get("from") or "all", []
    if isinstance(src, (list, tuple)):
        syms = [ds.canon_symbol(str(s)) for s in src if str(s or "").strip()]
        words = f"the {len(syms)} symbols named"
    elif str(src).startswith("@"):
        sec = customsec.get(str(src).upper())
        if not sec:
            raise Bad(f"there is no security {src}")
        syms, words = [m["symbol"] for m in sec["members"]], f"the members of {sec['symbol']}"
    elif src == "perps":
        uni = hyperliquid.universe()
        top = sorted(uni.values(), key=lambda c: -(c.get("day_volume_usd") or 0))[: int(u.get("n") or 40)]
        syms = [f"{c['coin'].upper()}{hyperliquid.SUFFIX}" for c in top]
        words = f"the {len(syms)} most traded Hyperliquid perpetuals"
    elif src == "fno":
        import options
        eod = options.latest() or {"unds": {}}
        syms = sorted(ds.canon_symbol(options.chart_symbol(k)) for k, v in eod["unds"].items()
                      if not v.get("index") and v.get("opts"))
        words = "NSE F&O stocks"
    elif src == "all":
        syms, words = [r[0] for r in _classified()], "every stored NSE stock"
    else:
        raise Bad(f"universe.from {src!r} is not all, fno, perps, @BASKET or a list")
    if u.get("sql"):
        import plays
        rows = plays._sql_rows(str(u["sql"]).strip().rstrip(";"), "1d") if _ok_sql(u["sql"]) else None
        if rows is None:
            raise Bad("universe.sql must be one read-only SELECT whose first column is a symbol")
        keep = {ds.canon_symbol(str(r[0])) for r in rows if r and r[0]}
        syms = [s for s in syms if s in keep] if src != "all" else sorted(keep)
        words += ", narrowed by your query"
    if u.get("industry") or u.get("sector"):
        want = u.get("industry") or u.get("sector")
        rows = _classified()
        hit = set()
        for q in (want if isinstance(want, list) else [want]):
            if u.get("sector"):
                hit |= {r[0] for r in rows if customsec._norm(r[2]) == customsec._norm(q)}
            else:
                hit |= {r[0] for r in rows if customsec._match_score(str(q), (r[1],)) >= 1.5}
        if not hit:
            raise Bad(f"no stored company is in {want!r}; the sectors are "
                      f"{', '.join(sorted({r[2] for r in rows if r[2]}))}")
        syms = [s for s in syms if s in hit]
        words += f" in {want if isinstance(want, str) else ', '.join(want)}"
    if not syms:
        raise Bad("the universe came out empty")
    if u.get("top_by"):
        by = str(u["top_by"])
        n = int(u.get("n") or 50)
        if by == "market_cap":
            cap = {r[0]: r[3] for r in _classified()}
            ranked = sorted((s for s in syms if cap.get(s)), key=lambda s: -cap[s])
            missing = len([s for s in syms if not cap.get(s)])
            if missing:
                said.append(f"{missing} had no market cap on file and were left out of the top {n}")
        elif by == "turnover":
            st = {s: (customsec._daily_stats(s) or {}).get("turnover") for s in syms}
            ranked = sorted((s for s in syms if st.get(s)), key=lambda s: -st[s])
        else:
            raise Bad("universe.top_by is market_cap or turnover")
        syms = ranked[:n]
        words += f", the top {n} by {by.replace('_', ' ')}"
    if u.get("min_turnover_cr"):
        floor = float(u["min_turnover_cr"]) * 1e7
        before = len(syms)
        syms = [s for s in syms if ((customsec._daily_stats(s) or {}).get("turnover") or 0) >= floor]
        said.append(f"{before - len(syms)} traded under ₹{float(u['min_turnover_cr']):g} crore a day and "
                    "were left out")
    if len(syms) > MAX_SYMBOLS:
        said.append(f"capped at {MAX_SYMBOLS} of {len(syms)}")
        syms = syms[:MAX_SYMBOLS]
    return words, list(dict.fromkeys(syms)), said


def _ok_sql(s) -> bool:
    import re
    import plays
    s = str(s or "").strip().rstrip(";")
    return bool(s) and ";" not in s and bool(re.match(r"^\s*(select|with)\b", s, re.I)) \
        and not plays._BANNED_SQL.search(s)


# ── the scan ─────────────────────────────────────────────────────────────────

def _frame(sym: str, spec: dict, limit: int) -> dict | None:
    import plays
    try:
        data, _ = plays.frame({**spec, "symbol": sym}, limit=limit, ensure=False)
    except (plays.Bad, Exception):                          # noqa: BLE001 — one name's gap is not the scan's
        return None
    return data if len(data["t"]) > 20 else None


def run(raw: dict, uid: int | None = None) -> dict:
    import indicator_sandbox
    import plays
    if not isinstance(raw, dict):
        raise Bad("scan is {universe, interval, measures, when, rank_by, order, top, fresh}")
    iv = str(raw.get("interval") or "1d")
    iv = {"D": "1d", "1D": "1d", "day": "1d", "daily": "1d", "60m": "1h"}.get(iv, iv)
    if iv not in plays.INTERVALS:
        raise Bad(f"interval must be one of {', '.join(plays.INTERVALS)}")
    words, syms, said = resolve(raw.get("universe"), uid)
    measures = [plays._norm_measure(m, uid) for m in raw.get("measures") or []]
    when = plays._norm_when(raw.get("when"))
    names = {m["name"] for m in measures}
    rank_by = str(raw.get("rank_by") or "").strip()
    order = "asc" if str(raw.get("order") or "desc").lower().startswith("asc") else "desc"
    top = max(1, min(int(raw.get("top") or 20), 100))
    fresh = int(raw.get("fresh") or 0)
    spec = {"symbol": syms[0], "interval": iv, "measures": measures}
    t0 = time.time()
    frames = {}
    for s in syms:
        d = _frame(s, spec, LOOKBACK.get(iv, 300))
        if d:
            frames[s] = d
    if not frames:
        raise Bad(f"none of the {len(syms)} symbols has stored {iv} bars here")
    series: dict[str, dict] = {s: {} for s in frames}
    if measures:
        base = set(next(iter(frames.values()))) | {"t"}
        code = plays._program(measures, base)
        jobs = [{"id": s, "free": True, "bars": d} for s, d in frames.items()]
        try:
            reply = indicator_sandbox.run(code, jobs, timeout=min(120, 20 + len(jobs) / 8))
        except indicator_sandbox.SandboxError as exc:
            raise Bad(f"the scan's measures could not run: {exc}") from None
        if not reply.get("ok"):
            raise Bad(f"measure code refused: {reply.get('error')}")
        for r in reply["results"]:
            if not r.get("ok"):
                continue
            n = len(frames[r["id"]]["t"])
            out = {}
            for m in measures:
                v = (r.get("value") or {}).get(m["name"])
                if not isinstance(v, list) or len(v) != n:
                    v = [None] * (n - 1) + [v if isinstance(v, (int, float)) else None]
                out[m["name"]] = [float(x) if isinstance(x, (int, float)) and not isinstance(x, bool)
                                  and math.isfinite(x) else (1.0 if x is True else 0.0 if x is False else None)
                                  for x in v]
            series[r["id"]] = out
        failed = [r["id"] for r in reply["results"] if not r.get("ok")]
        if failed and len(failed) == len(frames):
            first = next(r for r in reply["results"] if not r.get("ok"))
            raise Bad(f"the measures failed on every symbol: {first.get('error')}")
    # "now" for this scan: where most names' bars end, so one name with
    # fresher bars cannot make every other one look stale
    ends = sorted(d["t"][-1] for d in frames.values())
    newest = ends[(len(ends) * 3) // 4]
    step = plays.INTERVALS[iv] * 60
    rows, stale, everyone, near = [], [], [], []
    parts = (when or {}).get("all") or (when or {}).get("any") or []
    passes = [0] * len(parts)
    for s, d in frames.items():
        sr = series.get(s) or {}
        if measures and not sr:
            continue
        n = len(d["t"])
        if (newest - d["t"][-1] > 3 * step and iv != "1d") or newest - d["t"][-1] > 5 * 86400:
            stale.append(s)
            continue
        met, since = True, None
        vals = {m["name"]: sr[m["name"]][-1] for m in measures}
        everyone.append(vals)
        if when:
            try:
                tr = plays.truth(when, d, sr)
                each = [plays.truth(c, d, sr)[-1] is True for c in parts]
            except plays.Bad as exc:
                raise Bad(f"when: {exc}") from None
            for k, ok in enumerate(each):
                passes[k] += ok
            met = tr[-1] is True
            if not met and parts and sum(each) == len(parts) - 1:
                near.append({"symbol": s, "close": d["close"][-1],
                             "fails": plays.describe(parts[each.index(False)], {"symbol": "its", "measures": measures}),
                             **{k: (round(v, 4) if isinstance(v, float) else v) for k, v in vals.items()}})
            if met:
                k = n - 1
                while k > 0 and tr[k - 1] is True:
                    k -= 1
                since = n - 1 - k
            if fresh and not (met and since is not None and since < fresh):
                continue
            if not fresh and not met:
                continue
        c = d["close"]
        row = {"symbol": s, "close": c[-1],
               "chg_pct": round((c[-1] / c[-2] - 1) * 100, 2) if n > 1 and c[-2] else None}
        if since is not None:
            row["since_bars"] = since
        for m in measures:
            v = sr[m["name"]][-1]
            row[m["name"]] = round(v, 4) if isinstance(v, float) else v
        if rank_by and rank_by not in names:
            try:
                row[rank_by] = plays._resolve(rank_by, d, sr, n)[-1]
            except plays.Bad as exc:
                raise Bad(f"rank_by: {exc}") from None
        rows.append(row)
    matched = len(rows)
    if rank_by:
        have = [r for r in rows if isinstance(r.get(rank_by), (int, float))]
        have.sort(key=lambda r: r[rank_by], reverse=order == "desc")
        k = len(have)
        for i, r in enumerate(have):
            r["pct"] = round(100 * (k - 1 - i) / (k - 1), 1) if k > 1 else 100.0
        rows = have + [r for r in rows if r not in have]
    else:
        rows.sort(key=lambda r: (r.get("since_bars") if r.get("since_bars") is not None else 1e9, r["symbol"]))
    # across every name with data, not only those that passed: a measure whose
    # values are all off by a sign or a unit shows here, and so does the clause
    # that left nothing standing
    summary = {}
    for m in [m["name"] for m in measures]:
        vals = sorted(v[m] for v in everyone if isinstance(v.get(m), (int, float)))
        if vals:
            q = lambda f: round(vals[min(len(vals) - 1, int(f * len(vals)))], 4)   # noqa: E731
            summary[m] = {"low": round(vals[0], 4), "p10": q(0.1), "median": q(0.5), "p90": q(0.9),
                          "high": round(vals[-1], 4), "n": len(vals)}
    names_of = dict(ds._con.execute("SELECT symbol, COALESCE(name, symbol) FROM classification").fetchall())
    for r in rows[:top]:
        r["name"] = names_of.get(r["symbol"], "")
    return {"universe": words, "interval": iv, "scanned": len(syms), "with_data": len(frames),
            "matched": matched, "shown": min(top, len(rows)), "as_of": newest,
            "rank_by": rank_by or None, "order": order, "rows": rows[:top],
            "symbols": [r["symbol"] for r in rows[:top]], "summary": summary,
            "measures": [{"name": m["name"], "title": m["title"]} for m in measures],
            "when": plays.describe(when, {"symbol": "each symbol", "measures": measures}) if when else None,
            "left_out": {"no_data": len(syms) - len(frames), "stale": len(stale)},
            "passes": [{"text": plays.describe(c, {"symbol": "its", "measures": measures}), "n": passes[k]}
                       for k, c in enumerate(parts)] if len(parts) > 1 else None,
            "near": near[:6] if not matched else None,
            "notes": said, "secs": round(time.time() - t0, 1)}
