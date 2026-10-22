"""Chat v2: ten flexible tools over the same engine, and one system prompt.

The v1 surface is 53 tools whose descriptions route between each other. Each
round of a turn paid ~2.9 s before the first token for carrying them
(measured 2026-10-18, gpt-6-luna, 99.6% cached), and a top-down read took a
round per timeframe. v2 keeps every computation and changes only the surface
the model sees: ten tools, each a broad verb with plain arguments, that fan
out to the existing tool functions in dataserver.py. Nothing here computes a
market number itself except `query`, which runs the model's own SQL read-only
and its own statistics in the indicator sandbox.

Selected per request (dataserver._v2_on). The module is bound to dataserver at
import time rather than importing it, because dataserver usually runs as
__main__ and a second import would load a second copy of its state.
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

ds = None            # dataserver, bound by bind()
_POOL: ThreadPoolExecutor | None = None


def bind(module) -> None:
    global ds
    ds = module


# ── calling the engine ───────────────────────────────────────────────────

def _call(name: str, args: dict) -> dict:
    """One existing tool, through run_tool, so symbol scoping, the drawing
    budget, the live-bar note and the interval switch all still apply."""
    prev = getattr(ds._req, "v2_inner", False)
    ds._req.v2_inner = True
    try:
        return ds.run_tool(name, {k: v for k, v in (args or {}).items() if v not in (None, "")})
    finally:
        ds._req.v2_inner = prev


def _pool() -> ThreadPoolExecutor:
    # Its own pool, not the round's: a read_chart already running in a round
    # worker would otherwise wait on the pool it is occupying.
    global _POOL
    if _POOL is None:
        _POOL = ThreadPoolExecutor(max_workers=8, thread_name_prefix="v2-read")
    return _POOL


def _fan(jobs: list[tuple[str, dict]]) -> list[dict]:
    """Several existing tools side by side. What each drew, moved or printed is
    merged into this thread's channels in job order, the way dataserver's own
    round dispatch merges a round."""
    if not jobs:
        return []
    if len(jobs) == 1:
        return [_call(*jobs[0])]
    prev = getattr(ds._req, "v2_inner", False)
    ds._req.v2_inner = True
    snap = [(loc, dict(loc.__dict__)) for loc in (ds._req, ds._scene, ds._drawings)]
    try:
        futs = [_pool().submit(ds._run_call_in_worker, snap, n,
                               {k: v for k, v in a.items() if v not in (None, "")})
                for n, a in jobs]
        outs = [f.result() for f in futs]
    finally:
        ds._req.v2_inner = prev
    results = []
    switched = any(v.get("kind") == "set_interval" for v in getattr(ds._scene, "views", []) or [])
    for o in outs:
        for k, v in (o.get("req") or {}).items():
            if k != "v2_inner":
                setattr(ds._req, k, v)
        for k, v in (o.get("ledgers") or {}).items():
            setattr(ds._scene, k, v)
        ds._scene.items = (getattr(ds._scene, "items", None) or []) + o["scene"]
        for v in o["views"]:
            if v.get("kind") == "set_interval":
                if switched:
                    continue
                switched = True
            ds._view_add(v)
        for c in o["cards"]:
            ds._card_add(c)
        results.append(o["result"])
    return results


def _iv(x) -> str:
    """The chart's interval in the engine's spelling."""
    s = str(x or "").strip()
    return {"D": "1d", "W": "1w", "M": "1mo", "1D": "1d", "1W": "1w", "1M": "1mo"}.get(s, s) or "1d"


def _chart_iv() -> str:
    return _iv(getattr(ds._req, "ctx_interval", "") or "1d")


def _on_screen(sym: str | None) -> bool:
    if not sym:
        return True
    charts = [str(c).upper() for c in (getattr(ds._req, "charts", None) or [])]
    return not charts or ds.canon_symbol(sym) in charts


def _days_ago(n: int) -> str:
    return (datetime.now() - timedelta(days=n)).strftime("%Y-%m-%d")


def _old_prop(tool: str, prop: str) -> dict:
    """A parameter schema borrowed from a v1 tool, so the two cannot drift."""
    for t in ds.TOOLS:
        if t.get("name") == tool:
            return dict((t.get("parameters") or {}).get("properties", {}).get(prop) or {})
    return {}


# ── 1. read_chart ────────────────────────────────────────────────────────

READS = ["trend", "indicators", "studies", "levels", "trendlines", "swings", "gaps",
         "volume_profile", "patterns", "pattern_stats", "divergences", "reversal",
         "results_reaction", "move", "bars", "drawings"]
_INTERVALS = ["1m", "5m", "15m", "30m", "1h", "1d", "1w", "1mo"]
_TWO_NUM = {"supertrend", "bbands", "keltner", "donchian", "psar"}


def _study(spec: str) -> dict:
    """'ema 200' / 'supertrend 10 3' / 'rsi' → get_indicator arguments."""
    parts = re.split(r"[\s,:()/]+", str(spec).strip().lower())
    parts = [p for p in parts if p]
    if not parts:
        return {}
    out = {"name": parts[0]}
    nums = [float(p) for p in parts[1:] if re.fullmatch(r"\d+(\.\d+)?", p)]
    if nums:
        out["period"] = int(nums[0])
    if len(nums) > 1 and parts[0] in _TWO_NUM:
        out["mult"] = nums[1]
    return out


def _drawing_job(ref: str, iv: str, lookback) -> tuple[str, dict]:
    got = ds._drawing_get(ref)
    d = got.get("ok") or {}
    t = str(d.get("type") or "").lower()
    base = {"drawing_id": ref, "interval": iv, "lookback_bars": lookback}
    if t == "fib":
        return "evaluate_fib", base
    if t in ("trendline", "ray", "line", "segment", "extended", "hline", "horizontal",
             "horizontal_ray", "arrow"):
        return "evaluate_line", base
    if t in ("rect", "rectangle", "zone", "box"):
        return "evaluate_drawing", {**base, "kind": "zone"}
    if t in ("channel", "parallel_channel"):
        return "evaluate_drawing", {**base, "kind": "channel"}
    if t in ("long", "short", "position"):
        return "evaluate_drawing", {**base, "kind": "position"}
    return "read_drawing", {"drawing_id": ref, "interval": iv, "lookback_bars": lookback}


def read_chart(read=None, symbol=None, intervals=None, studies=None, patterns=None,
               direction=None, drawing_ids=None, frm=None, to=None,
               lookback_bars=None, draw=False, detail=None, result_dates=None) -> dict:
    read = [r for r in (read or []) if r in READS] or ["trend", "levels", "indicators"]
    ivs = [_iv(i) for i in (intervals or []) if _iv(i) in _INTERVALS] or [_chart_iv()]
    note = []
    if draw and not _on_screen(symbol):
        draw = False
        note.append(f"{symbol} is not on screen, so nothing was drawn. Open it with "
                    f"workspace show_chart first if the trader wants it marked.")
    sym = {"symbol": symbol} if symbol else {}
    lb = lookback_bars
    jobs: list[tuple[str, str, dict]] = []      # (key, tool, args)

    # A ladder across timeframes is one call to multi_timeframe, which prints
    # one panel for the lot rather than a panel per interval.
    ladder = len(ivs) > 1 and {"trend", "indicators", "levels"} & set(read)
    if ladder:
        focus = "levels" if set(read) & {"trend", "indicators", "levels"} == {"levels"} else "momentum"
        jobs.append(("timeframes", "multi_timeframe",
                     {"intervals": ivs, "draw": bool(draw), "lookback_bars": lb, "focus": focus, **sym}))
    for iv in ivs:
        tag = f"{iv} " if len(ivs) > 1 else ""
        per_iv = [r for r in read if not (ladder and r in ("trend", "indicators", "levels"))]
        for r in per_iv:
            if r == "trend":
                jobs.append((tag + r, "get_trend", {"interval": iv, "lookback_bars": lb, "draw": draw, **sym}))
            elif r == "indicators":
                jobs.append((tag + r, "read_indicators", {"interval": iv, "lookback_bars": lb, **sym}))
            elif r == "levels":
                jobs.append((tag + r, "get_levels", {"interval": iv, "lookback_bars": lb, "draw": draw, **sym}))
            elif r == "trendlines":
                jobs.append((tag + r, "get_trendlines", {"interval": iv, "lookback_bars": lb, "draw": draw, **sym}))
            elif r == "swings":
                jobs.append((tag + r, "get_anchors", {"interval": iv, "lookback_bars": lb,
                                                      "frm": frm, "to": to, **sym}))
            elif r == "gaps":
                jobs.append((tag + r, "get_gaps", {"interval": iv, "lookback_bars": lb, "draw": draw, **sym}))
            elif r == "patterns":
                jobs.append((tag + r, "get_patterns", {"interval": iv, "lookback_bars": lb, "kinds": patterns,
                                                       "draw": draw, "detail": detail, **sym}))
            elif r == "pattern_stats":
                for k in (patterns or [])[:4]:
                    jobs.append((f"{tag}{k} stats", "evaluate_pattern",
                                 {"kind": k, "interval": iv, "lookback_bars": lb, **sym}))
            elif r == "divergences":
                for ind in ("rsi", "macd"):
                    jobs.append((f"{tag}{ind} divergences", "get_divergences",
                                 {"indicator": ind, "interval": iv, "lookback_bars": lb, "draw": draw, **sym}))
            elif r == "reversal":
                for dirn in ([direction] if direction in ("bullish", "bearish") else ["bullish", "bearish"]):
                    jobs.append((f"{tag}{dirn} reversal", "confirm_reversal",
                                 {"direction": dirn, "interval": iv, "lookback_bars": lb, **sym}))
            elif r == "studies":
                for s in (studies or ["rsi"])[:6]:
                    a = _study(s)
                    if a:
                        jobs.append((f"{tag}{s}", "get_indicator",
                                     {**a, "interval": iv, "draw": draw, "series_points": 20, **sym}))
            elif r == "bars":
                jobs.append((tag + r, "get_bars", {"interval": iv, "frm": frm, "to": to, "limit": 80, **sym}))
            elif r == "drawings":
                for ref in (drawing_ids or [])[:4]:
                    name, a = _drawing_job(ref, iv, lb)
                    jobs.append((f"{tag}drawing {ref}", name, a))
    # interval-free reads, once
    if "volume_profile" in read:
        jobs.append(("volume_profile", "volume_profile", {"frm": frm, "to": to, "draw": draw, **sym}))
    if "move" in read:
        jobs.append(("move", "explain_move", {"frm": frm, "to": to, **sym}))
    if "results_reaction" in read:
        jobs.append(("results_reaction", "evaluate_results",
                     {"interval": "1w" if ivs[0] == "1w" else "1d",
                      **({"dates": result_dates} if result_dates else {}), **sym}))

    if not jobs:
        return {"error": "nothing to read", "_note": f"read takes any of: {', '.join(READS)}"}
    got = _fan([(t, a) for _, t, a in jobs])
    out: dict = {"symbol": (symbol or getattr(ds._req, "symbol", "")).upper(), "read": {}}
    for (key, _, _), res in zip(jobs, got):
        out["read"][key] = res
    if note:
        out["_note"] = " ".join(note)
    return out


# ── 2. draw ──────────────────────────────────────────────────────────────

def draw(shapes=None, interval=None, symbol=None, pane=None, clear=False,
         lookback_bars=None) -> dict:
    iv = _iv(interval) if interval else _chart_iv()
    if symbol and not _on_screen(symbol):
        return {"error": f"{symbol} is not on screen",
                "_note": "Open it first with workspace show_chart, then draw on it."}
    sym = {"symbol": symbol} if symbol else {}
    out: dict = {}
    if clear:
        out["cleared"] = _call("mark", {"shapes": [], "interval": iv, "draw_mode": "clear", **sym})
    by_addr, anchored = [], []
    for s in shapes or []:
        if not isinstance(s, dict):
            continue
        (anchored if s.get("anchors") else by_addr).append(s)
    # draw_shape composes only its own shapes and the ratio tools; a ruler,
    # arrow or note on swing ids is drawn by address on the same bars
    loose = [s for s in anchored if str(s.get("shape") or "").lower() not in ds._SHAPES]
    if loose:
        anchored = [s for s in anchored if s not in loose]
        addressed, missing = _anchors_as_addresses(loose, iv, symbol, lookback_bars or 400)
        by_addr += addressed
        if missing:
            out["not_found"] = missing
    lookback = max(lookback_bars or 400, _reach(by_addr, iv))
    jobs = []
    if by_addr:
        jobs.append(("mark", {"shapes": by_addr, "interval": iv, "pane": pane,
                              "lookback_bars": lookback, **sym}))
    for s in anchored:
        jobs.append(("draw_shape", {"shape": s.get("shape"), "anchor_ids": s.get("anchors"),
                                    "interval": iv, "label": s.get("label"), "role": s.get("role"),
                                    "pane": pane, **sym}))
    for (name, _), res in zip(jobs, _fan(jobs) if len(jobs) > 1 else [_call(*j) for j in jobs]):
        out.setdefault(name, []).append(res)
    if not jobs and not clear:
        return out if out else {"error": "no shapes given"}
    return out


_MINUTES = {"1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30, "1h": 60}


def _reach(shapes: list, iv: str) -> int:
    """Bars a repeated shape needs: every session it repeats over, plus slack."""
    need = 0
    for s in shapes:
        rep = str(s.get("repeat") or "none").lower()
        if rep not in ("session", "day", "week"):
            continue
        per_day = -(-375 // _MINUTES[iv]) + 1 if iv in _MINUTES else 1
        days = int(s.get("sessions") or 5) * (5 if rep == "week" else 1) + 1
        need = max(need, per_day * days + 20)
    return min(need, 1500)


def _anchors_as_addresses(shapes: list, iv: str, symbol, lookback) -> tuple[list, list]:
    """Swing ids to 'date @ high/low' addresses, so the resolver reads the bar."""
    prev = getattr(ds._req, "symbol", None)
    if symbol:
        ds._req.symbol = symbol
    try:
        got = ds.tool_get_anchors(iv, lookback, limit=30, _raw=True)
        by_id = {a["id"].upper(): a for a in got.get("anchors", [])}
        for k, v in ds._minted_anchors().items():
            by_id.setdefault(k.upper(), v)
    finally:
        ds._req.symbol = prev
    fmt = "%Y-%m-%d" if iv in ("1d", "1w", "1mo") else "%Y-%m-%d %H:%M"
    out, missing = [], []
    for s in shapes:
        addrs, found = [], []
        for aid in s.get("anchors") or []:
            a = by_id.get(str(aid).upper())
            if not a or "_ts" not in a:
                missing.append(aid)
                continue
            found.append(a)
        if len(found) == 2:          # a leg reads forward in time
            found.sort(key=lambda a: a["_ts"])
        for a in found:
            kind = str(a.get("kind") or "")
            side = "high" if "high" in kind else "low" if "low" in kind else a.get("value")
            addrs.append(f"{time.strftime(fmt, time.gmtime(a['_ts'] + 19800))} @ {side}")
        if not addrs:
            continue
        s = {k: v for k, v in s.items() if k != "anchors"}
        if len(addrs) == 1:
            s["at"] = addrs[0]
        elif len(addrs) == 2:
            s["from"], s["to"] = addrs
        else:
            s["points"] = addrs
        out.append(s)
    return out, missing


# ── 3. workspace ─────────────────────────────────────────────────────────

_CHART_OPS = {"show_chart", "add_chart", "add_study", "remove_study",
              "build_indicator", "edit_indicator", "list_indicators"}
_WIDGETS = ["watch", "screener", "notes", "sheet", "financials", "news", "options", "calendar",
            "alerts", "journal", "portfolio", "strategy", "browser", "docs", "code", "depth", "tv"]
_FOLLOWS_CHART = {"news", "financials", "options", "depth", "notes"}


def workspace(ops=None) -> dict:
    results, widget_ops = [], []

    def flush():
        if widget_ops:
            results.append({"widgets": _call_direct_workspace(list(widget_ops))})
            widget_ops.clear()

    for op in ops or []:
        if not isinstance(op, dict):
            continue
        kind = op.get("op")
        if kind not in _CHART_OPS:
            if kind == "open" and op.get("type") in _FOLLOWS_CHART and not (op.get("symbol") or op.get("link")):
                # a news or financials pane opened beside a chart is about that chart
                op = {**op, "symbol": getattr(ds._req, "symbol", None), "link": "chart"}
            widget_ops.append(op)
            continue
        flush()
        iv = _iv(op.get("interval")) if op.get("interval") else None
        if kind in ("show_chart", "add_chart"):
            sym = op.get("symbol") or getattr(ds._req, "symbol", "")
            results.append({kind: _call("open_chart", {"symbol": sym, "interval": iv,
                                                       "replace": kind == "show_chart"})})
        elif kind in ("add_study", "remove_study"):
            a = _study(op.get("study") or "")
            if not a:
                results.append({kind: {"error": "name the study, e.g. 'ema 200'"}})
                continue
            flag = {"draw": True} if kind == "add_study" else {"remove": True}
            results.append({kind: _call("get_indicator", {**a, "interval": iv or _chart_iv(), **flag})})
        elif kind in ("build_indicator", "edit_indicator", "list_indicators"):
            act = {"build_indicator": "build", "edit_indicator": "edit", "list_indicators": "list"}[kind]
            results.append({kind: _call("custom_indicator", {"action": act, "request": op.get("request"),
                                                             "id": op.get("indicator_id"),
                                                             "interval": iv})})
    flush()
    return {"results": results}


def _call_direct_workspace(ops: list) -> dict:
    # the v1 tool shares this name, so it is called by function, not by name
    prev = getattr(ds._req, "v2_inner", False)
    ds._req.v2_inner = True
    try:
        return ds.run_tool("workspace", {"ops": ops})
    finally:
        ds._req.v2_inner = prev


# ── 4. market_data ───────────────────────────────────────────────────────

def _profile_row(sym: str) -> dict:
    try:
        con = sqlite3.connect(f"file:{ds.DB_PATH}?mode=ro", uri=True, timeout=5)
        con.row_factory = sqlite3.Row
        r = con.execute("SELECT name, sector, industry, market_cap, eps, pb, ev_ebitda, roe, roa, "
                        "net_margin, debt_to_equity, book_value_ps, summary FROM company_profile "
                        "WHERE symbol = ?", (ds.canon_symbol(sym),)).fetchone()
        con.close()
        if not r:
            return {}
        d = dict(r)
        if d.get("summary"):
            d["summary"] = d["summary"][:400]
        return {k: v for k, v in d.items() if v not in (None, "")}
    except sqlite3.Error:
        return {}


_Q_FIELDS = ("revenue", "other_income", "ebitda", "pbt", "net_profit", "eps_basic")


def _quarters(sym: str, n: int = 8) -> dict:
    """The filed quarterly figures (Rs crore), newest first, with year-on-year
    growth and net margin computed here — the model quotes, never derives."""
    try:
        con = sqlite3.connect(f"file:{ds.DB_PATH}?mode=ro", uri=True, timeout=5)
        rows = con.execute("SELECT basis, period_end, payload FROM quarters WHERE symbol = ? "
                           "ORDER BY period_end DESC", (ds.canon_symbol(sym),)).fetchall()
        con.close()
    except sqlite3.Error:
        return {}
    by_basis: dict = {}
    for basis, pe, payload in rows:
        try:
            by_basis.setdefault(basis, {})[pe] = json.loads(payload)
        except (TypeError, ValueError):
            continue
    basis = "consolidated" if "consolidated" in by_basis else next(iter(by_basis), None)
    if not basis:
        return {}
    q = by_basis[basis]
    ends = sorted(q, reverse=True)
    out = []
    for pe in ends[:n]:
        p = q[pe]
        row = {"quarter": p.get("period_label") or pe, "period_end": pe}
        row.update({k: p.get(k) for k in _Q_FIELDS if p.get(k) is not None})
        prev = q.get(f"{int(pe[:4]) - 1}{pe[4:]}")
        for k in ("revenue", "net_profit"):
            a, b = p.get(k), (prev or {}).get(k)
            if isinstance(a, (int, float)) and isinstance(b, (int, float)) and b:
                row[f"{k}_yoy_pct"] = round((a / b - 1) * 100, 1)
        if p.get("revenue") and p.get("net_profit") is not None:
            row["net_margin_pct"] = round(p["net_profit"] / p["revenue"] * 100, 1)
        out.append(row)
    return {"basis": basis, "units": "Rs crore (EPS in Rs)", "quarters": out}


def market_data(kind=None, symbols=None, interval=None, frm=None, to=None, expiry=None,
                strikes=None, client=None, limit=None) -> dict:
    syms = [s for s in (symbols or []) if s] or [getattr(ds._req, "symbol", "")]
    if kind == "compare":
        return _call("compare_symbols", {"symbols": syms, "interval": _iv(interval) if interval else "1d"})
    jobs = []
    focus = str(getattr(ds._req, "symbol", "")).upper()
    for s in syms[:6]:
        if kind == "profile":
            if ds.canon_symbol(s) == focus:
                # read_symbol is for OTHER instruments; the chart in focus is read directly
                jobs.append((s, "get_bars", {"interval": "1d", "limit": 5}))
            else:
                jobs.append((s, "read_symbol", {"symbol": s, "interval": _iv(interval) if interval else "1d",
                                                "bars": 5, "levels": True}))
        elif kind == "results":
            jobs.append((s, "get_results", {"symbol": s, "interval": "1d", "limit": limit or 8}))
        elif kind == "peers":
            jobs.append((s, "get_peers", {"symbol": s}))
        elif kind == "deals":
            jobs.append((s, "get_deals", {"symbol": s, "client": client, "frm": frm, "to": to,
                                          "limit": limit}))
        elif kind == "flows":
            jobs.append((s, "get_flows", {"frm": frm, "to": to}))
            break
        elif kind == "option_chain":
            jobs.append((s, "get_option_chain", {"symbol": s, "expiry": expiry, "strikes": strikes}))
        else:
            return {"error": f"unknown kind '{kind}'",
                    "_note": "kind is one of profile, results, peers, compare, deals, flows, option_chain"}
    got = _fan([(t, a) for _, t, a in jobs])
    out = {}
    for (s, _, _), res in zip(jobs, got):
        if kind == "profile" and isinstance(res, dict):
            prof = _profile_row(s)
            if prof:
                res = {**res, "fundamentals": prof}
        if kind == "results" and isinstance(res, dict):
            figs = _quarters(s, limit or 8)
            res = {"figures": figs or {"note": "no filed quarterly figures stored for this symbol"},
                   "announcement_dates": res.get("results"), **{k: v for k, v in res.items()
                                                                if k not in ("results",)}}
        out[s.upper()] = res
    return out if len(out) > 1 else next(iter(out.values()), {})


# ── 5. search ────────────────────────────────────────────────────────────

def search(source=None, query=None, symbol=None, frm=None, to=None) -> dict:
    if source == "social":
        import social
        import play_events
        try:
            items = social.fetch(str(query or ""))
        except social.Bad as exc:
            return {"error": str(exc)}
        except Exception as exc:                            # noqa: BLE001
            return {"error": f"{query} could not be read just now ({exc}); say so"}
        try:
            play_events.store(items)          # kept, so a play on this source has history to replay
        except Exception:                                   # noqa: BLE001
            pass
        return {"source": play_events.label(items[0]["source"]) if items else str(query),
                "posts": [{"when": time.strftime("%d %b %Y %H:%M IST", time.gmtime(it["ts"] + 19800)),
                           "who": it["who"], "text": (it["title"] + ("\n" + it["body"] if it["body"] else ""))[:500],
                           "url": it["url"]} for it in items[-20:][::-1]],
                "_note": "Posts are what people wrote, not facts: attribute each claim to its author."}
    if source == "web":
        return _call("search_web", {"q": query or symbol or ""})
    if source == "conversations":
        return _call("recall_conversations", {"query": query, "symbol": symbol})
    return _call("search_news", {"frm": frm or _days_ago(14), "to": to, "focus": query,
                                 "symbol": symbol})


# ── 6. query: the model's own SQL, and its own statistics ────────────────

_SQL_MAX_ROWS = 50_000
_SQL_SHOW = 40
_SQL_SECONDS = 8.0
_BANNED = re.compile(r"\b(attach|detach|pragma|insert|update|delete|replace|create|drop|alter|"
                     r"vacuum|reindex|analyze|load_extension)\b", re.I)
_TABLE_NOTES = {
    "bars": "1-minute OHLCV for every instrument. ts is epoch seconds (UTC; IST = ts+19800). "
            "Always filter on symbol, and on ts for anything wider than a few months.",
    "bars_1d": "daily OHLCV, same columns as bars",
    "benchmark": "index daily OHLC (symbol 'NIFTY 50'), trade_date ISO",
    "company_profile": "one row per company: sector, industry, market_cap (Rs), eps, pb, ev_ebitda, roe, roa, net_margin, debt_to_equity",
    "classification": "symbol → name, sector, industry",
    "results": "quarterly result dates: quarter, trade_date (first reactable session), after_market",
    "quarters": "quarterly statements, payload is JSON (use json_extract)",
    "deals": "bulk and block deals: d (date), kind, client, side, qty, price",
    "pattern_stats": "base rates for every pattern: hit rate vs control, sample size",
    "vp_screen": "20-session volume profile per symbol: poc, val, vah, position",
}


def _schema() -> dict:
    import fo_store
    con = sqlite3.connect(f"file:{ds.DB_PATH}?mode=ro", uri=True, timeout=5)
    fo = fo_store.attach(con)
    notes = {**_TABLE_NOTES, **fo_store.TABLES}
    try:
        out = {}
        for db in ("main", "fo") if fo else ("main",):
            for (name,) in con.execute(f"SELECT name FROM {db}.sqlite_master WHERE type='table' ORDER BY name"):
                if name not in notes:
                    continue
                cols = [r[1] for r in con.execute(f'PRAGMA {db}.table_info("{name}")')]
                out[name] = {"columns": cols, "about": notes[name]}
        return out
    finally:
        con.close()


def query(sql=None, python=None, params=None) -> dict:
    if not sql:
        return {"tables": _schema(),
                "_note": "Write one SELECT against these. Symbols are upper case NSE tickers "
                         "('RELIANCE', 'NIFTY 50')."}
    s = str(sql).strip().rstrip(";")
    if ";" in s or not re.match(r"^\s*(select|with)\b", s, re.I) or _BANNED.search(s):
        return {"error": "only one read-only SELECT (or WITH … SELECT) is allowed"}
    con = sqlite3.connect(f"file:{ds.DB_PATH}?mode=ro", uri=True, timeout=5)
    import fo_store
    fo_store.attach(con)
    deadline = time.monotonic() + _SQL_SECONDS
    con.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 20_000)
    try:
        cur = con.execute(s)
        cols = [d[0] for d in cur.description or []]
        rows = cur.fetchmany(_SQL_MAX_ROWS + 1)
    except sqlite3.OperationalError as exc:
        msg = str(exc)
        if "interrupted" in msg:
            return {"error": f"the query ran past {_SQL_SECONDS:.0f}s",
                    "_note": "Narrow it: filter bars on symbol and a ts range, or use bars_1d."}
        return {"error": f"SQL error: {msg}", "_note": "Call query with no sql to see the tables."}
    finally:
        con.close()
    capped = len(rows) > _SQL_MAX_ROWS
    rows = rows[:_SQL_MAX_ROWS]
    out: dict = {"columns": cols, "row_count": len(rows), **({"capped_at": _SQL_MAX_ROWS} if capped else {})}
    if not python:
        out["rows"] = [list(r) for r in rows[:_SQL_SHOW]]
        if len(rows) > _SQL_SHOW:
            out["_note"] = (f"{len(rows)} rows; the first {_SQL_SHOW} are shown. Aggregate in SQL "
                            f"or pass python to compute over all of them.")
        return out
    data = {c: [r[i] for r in rows] for i, c in enumerate(cols)}
    import indicator_sandbox
    try:
        reply = indicator_sandbox.run(str(python), [{"id": "q", "free": True, "bars": data,
                                                     "params": params or {}}], timeout=20)
    except indicator_sandbox.SandboxError as exc:
        return {**out, "error": f"python could not run: {exc}"}
    if not reply.get("ok"):
        return {**out, "error": f"python refused: {reply.get('error')}",
                "_note": "Define compute(data, params) at the top level; no imports, no names "
                         "starting with '_'. math and ta are available."}
    res = reply["results"][0]
    if not res.get("ok"):
        return {**out, "error": f"python failed: {res.get('error')}", "line": res.get("line")}
    val = res.get("value")
    text = json.dumps(val, default=str)
    if len(text) > 20_000:
        return {**out, "error": "the python result is too large to read back",
                "_note": "Return summary statistics, not series."}
    out["result"] = val
    return out


# ── 7. screen ────────────────────────────────────────────────────────────

def screen(filters=None, industry=None, pattern=None, pattern_within=None, sort=None,
           limit=None, like=None, interval=None, symbols=None) -> dict:
    if like:
        return _call("scan_setup", {"like": like, "interval": _iv(interval) if interval else "1d",
                                    "industry": industry, "symbols": symbols, "limit": limit,
                                    "patterns": True})
    return _call("screen_universe", {"filters": filters, "industry": industry, "pattern": pattern,
                                     "pattern_within": pattern_within, "sort": sort, "limit": limit})


# ── 8. alerts ────────────────────────────────────────────────────────────

def alerts(action=None, **args) -> dict:
    name = {"create": "set_alert", "update": "update_alert", "check": "check_alert",
            "list": "list_alerts", "cancel": "cancel_alert"}.get(action)
    if not name:
        return {"error": "action is one of create, update, check, list, cancel"}
    if args.get("interval"):
        args["interval"] = _iv(args["interval"])
    return _call(name, args)


# ── 9. journal ───────────────────────────────────────────────────────────

def journal(action=None, **args) -> dict:
    if action == "portfolio":
        return _call("paper_portfolio", {})
    if action == "list":
        return _call("list_trades", {k: args.get(k) for k in ("symbol", "status", "limit")})
    if action == "update":
        if args.get("changes"):
            return _call("update_journal_trade", {"trade_id": args.get("trade_id"),
                                                  "changes": args["changes"]})
        return _call("update_trade", args)
    if action == "log":
        return _call("log_trade", args)
    return {"error": "action is one of log, update, list, portfolio"}


# ── 10. strategy ─────────────────────────────────────────────────────────

def strategy(action=None, condition=None, exit_condition=None, symbol=None, interval=None,
             frm=None, to=None, candidates=None, capital=None, strategy_id=None, **args) -> dict:
    sym = symbol or getattr(ds._req, "symbol", "")
    iv = _iv(interval) if interval else _chart_iv()
    if action == "build":
        return _call("propose_dsl_workflow", {"condition": condition, "primary_symbol": sym,
                                              "exit_condition": exit_condition, "interval": iv,
                                              "name": args.get("name")})
    if action == "backtest":
        return _call("backtest_dsl_tree", {"condition": condition, "primary_symbol": sym,
                                           "exit_condition": exit_condition, "interval": iv,
                                           "start_date": frm, "end_date": to,
                                           "starting_capital": capital,
                                           "exit_kind": args.get("exit_kind"),
                                           "exit_bars": args.get("exit_bars"),
                                           "exit_pct": args.get("exit_pct"),
                                           "direction": args.get("side")})
    if action == "compare":
        return _call("evaluate_strategies", {"primary_symbol": sym, "candidates": candidates,
                                             "interval": iv, "start_date": frm, "end_date": to,
                                             "starting_capital": capital})
    if action == "plan":
        keys = ("entry", "stop", "stop_atr", "targets", "targets_r", "qty", "risk_amount",
                "risk_pct", "side", "drawing_id", "basis")
        return _call("plan_position", {**{k: args.get(k) for k in keys}, "capital": capital,
                                       "interval": iv})
    if action == "list":
        return _call("list_strategies", {})
    if action == "pause":
        return _call("pause_strategy", {"strategy_id": strategy_id, "resume": args.get("resume")})
    if action == "delete":
        return _call("delete_strategy", {"strategy_id": strategy_id})
    return {"error": "action is one of build, backtest, compare, plan, list, pause, delete"}


# ── 11. play, 12. measure (the execution layer, plays.py) ───────────────

def play(action=None, play=None, play_id=None, arm=None) -> dict:
    import plays
    return plays.tool_play(action=action or "", play=play, play_id=int(play_id or 0),
                           arm=True if arm is None else bool(arm))


def measure(action=None, measure=None, symbol=None, interval=None, security=None, scan=None) -> dict:
    import plays
    return plays.tool_measure(action=action or "test", measure=measure, symbol=symbol or "",
                              interval=_iv(interval) if interval else "", security=security, scan=scan)


DISPATCH = {"read_chart": read_chart, "draw": draw, "workspace": workspace,
            "market_data": market_data, "search": search, "query": query,
            "screen": screen, "alerts": alerts, "journal": journal, "strategy": strategy,
            "play": play, "measure": measure}


def plays_on() -> bool:
    """The execution layer replaces `alerts` and `strategy` on the v2 surface:
    an alert is a play that notifies. Per request (`plays` in the /chat body),
    else PIVOT_PLAYS=1."""
    want = getattr(ds._req, "plays", None)
    if want is not None:
        return bool(want)
    import os
    return os.environ.get("PIVOT_PLAYS", "0") == "1"


def run(name: str, args: dict) -> dict:
    fn = DISPATCH[name]
    # the model fills optional fields with "" rather than leaving them out;
    # an empty string means "not given", never "the empty window"
    args = {k: v for k, v in dict(args or {}).items() if v != ""}
    # the schema says from/to; python cannot name a parameter `from`
    if "from" in args:
        args["frm"] = args.pop("from")
    panel = bool(args.pop("panel", False))
    before = len(getattr(ds._scene, "cards", None) or [])
    try:
        return _panels(fn(**args), before, panel)
    except TypeError as exc:
        return {"error": f"{name}: {exc}"}
    except Exception as exc:  # noqa: BLE001 — a failed tool must not kill the turn
        import logging
        logging.exception("chat v2 tool %s failed", name)
        return {"error": f"{name} failed: {exc}"}


# Panels that repeat what the reply already says in words. A read fans out to
# several engine tools and each used to bring its own, so one full read put
# four or five cards above a reply that covered the same numbers.
_RESTATES = {"trend", "indicators", "patterns", "confirmation", "timeframes", "levels"}


def _panels(out, before: int, asked: bool):
    """Keep the panels that carry the answer (a chain, a screen, a backtest, a
    comparison); a restating one only when the model asked for it, and then
    one. The result then names exactly what is on screen."""
    cards = getattr(ds._scene, "cards", None) or []
    new = cards[before:]
    keep, restating = [], 0
    for c in new:
        if c.get("kind") in _RESTATES:
            if not asked or restating:
                continue
            restating += 1
        keep.append(c)
    ds._scene.cards = cards[:before] + keep
    if isinstance(out, dict) and new:
        _drop_card_notes(out)
        if keep:
            kinds = ", ".join(str(c.get("kind")).replace("_", " ") for c in keep)
            out["_panel"] = (f"Shown in the reply as a panel: {kinds}. Write [[panel]] on its own "
                             "line where it belongs, once per panel; do not repeat its numbers "
                             "line by line in the prose.")
    return out


def _drop_card_notes(x) -> None:
    if isinstance(x, dict):
        x.pop("_card_note", None)
        for v in x.values():
            _drop_card_notes(v)
    elif isinstance(x, list):
        for v in x:
            _drop_card_notes(v)


# ── the surface the model sees ───────────────────────────────────────────

def _fn(name: str, description: str, props: dict, required: list | None = None) -> dict:
    return {"type": "function", "name": name, "description": description,
            "parameters": {"type": "object", "properties": props, "required": required or []}}


_STR = {"type": "string"}
_ARR = {"type": "array", "items": {"type": "string"}}
_IVS = {"type": "string", "enum": _INTERVALS}
_PALETTE = ["blue", "teal", "green", "red", "orange", "yellow", "purple", "pink", "grey"]


def tools() -> list[dict]:
    out = _tools()
    if plays_on():
        out = [t for t in out if t["name"] not in ("alerts", "strategy")] + _play_tools()
    return out


_PLAY_DOC = (
    "Build and run a play: the trader's idea as a rule Pivot checks on every bar close and acts on, "
    "on paper. compile builds and arms it; edit changes one (play_id, the whole play again); list, "
    "pause, resume, delete; explain (what it saw and did); review (their closed trades cut by hour, "
    "play, exit, instrument and after-a-loss; play: {days}). Never ask before building: choose "
    "sensibly, then state the choices.\n"
    "A play is {title (the idea in a few words, with no number the rule lacks), ask (their words "
    "verbatim, messages joined with …), why "
    "(the reason, from data you read), symbol (the bars that clock it), interval, measures, when, on, "
    "do, manage, contract, mandate, security, universe}. universe: up to 60 names (a scan's symbols, a "
    "list, or the universe shape measure's scan takes), each checked on its own bars and traded as "
    "itself (mandate.max_open names at once, default 5). security: a basket, ratio or spread that does not exist "
    "yet (measure's security shape, pick included); use @NAME as symbol or do.symbol and one call "
    "builds both. Calls that do not depend on each other go in the same response.\n"
    "measures: any series the rule needs beyond price fields, written by you: {name, title, formula "
    "(in words), inputs, expr ('a / b') or code ('def compute(data, params): return [one value per "
    "bar]')}. data has t (bar open, epoch seconds), open, high, low, close, volume, hhmm (the bar's "
    "close in IST as an integer: 09:20 is 920), each input and earlier measures; ta (sma, ema, rsi, "
    "atr, highest, lowest, stdev, crossover, change, roc, vwma and more) and math are there. inputs: "
    "{alias: {bars: {symbol, field}}} another instrument; {sql: 'SELECT <when known>, <value> ...'} "
    "anything in the market database, F&O history included (query lists the tables; each bar sees "
    "rows known by its close); {perp: {coin, field: funding | premium | oi}}; {book: day_pnl | "
    "realized_today | trades_today | loss_streak} the trader's own day book; {social: {source, match}} "
    "posts per bar at a social address. Never type a number the "
    "data should supply; if you do not know a formula, search for it.\n"
    "when: {all | any: [{left, op, right, right2?, bars?, for_bars?}]}, op above, below, cross_up, "
    "cross_down, between, outside, rises_pct, falls_pct, is_true. Operands: numbers, measure names, "
    "close/open/high/low/volume, 'SYMBOL.close', or arithmetic over names ('or_high + 0.5 * atr14'). "
    "{time: {after: '09:20', before: '15:00'} | {at: '09:20'}} reads the bar close in IST. for_bars N: "
    "it must hold N bars running.\n"
    "on (events clock it instead of bars): {sources, symbols (whose NSE filings), match: [specific "
    "names or terms, never common words], judge: 'a yes/no question each item must answer yes to'}. "
    "sources: filings (NSE announcements), news (Economic Times, Mint, CNBC-TV18, BusinessLine, BBC, "
    "CoinDesk), or a social address: reddit:r/<sub> | reddit:u/<user> | reddit:search:<words>, "
    "telegram:<channel>, youtube:@<handle>, bsky:<handle>, mastodon:<user>@<host>, gnews:<words>, "
    "rss:<url>, or the pasted link. X, StockTwits, Instagram and the like cannot be read; compile "
    "says why and what is nearest. With on, "
    "`when` confirms on the latest bar, and a headline alone only proposes.\n"
    "do: {kind: notify}; {kind: buy | sell | short | cover, symbol, size: {inr} | {qty} | {all: "
    "true}, product: CNC | MIS}; {kind: options, underlying, legs: [{opt: CE | PE, strike: ATM | "
    "ATM+1 | ATM-2 | 22750, side: buy | sell, lots, expiry: weekly | next | monthly | YYYY-MM-DD}]}; "
    "{kind: future, underlying, side, lots, expiry}; perps (Hyperliquid, USD, 24 hours): symbol "
    "COIN-PERP, kind buy | short | sell | cover, leverage up to the market's maximum, size.inr is the "
    "margin, funding hourly, an estimated liquidation closes it; {kind: halt, close?: true}: a guard "
    "that stops new intraday, F&O and perp entries for the rest of the day (and closes what is open), "
    "never waiting for a tap. Code works out quantities, lots and the strike. Equity shorts are "
    "intraday only.\n"
    "{kind: rebalance, of: portfolio | self | <play_id> (takes over that play's holdings), targets: "
    "[{symbol, weight, why (the figure that earns it)}], cash_pct, size: {inr} (only new money they "
    "add; leave it out to rebalance what is held), objective (their "
    "words), universe}: no `when`; code works out whole-share trades (a planner stays within "
    "mandate.max_weight, 0.4; weights they state are kept). "
    "Once (lifespan now) you write the targets from data you read; kept (lifespan policy, policy: "
    "{drift_pct, every: day | week | month only if they named a cadence}) it reruns on drift or that "
    "calendar, and with contract.discretion plan a planner sets the targets each run (a change of "
    "names is proposed).\n"
    "manage: {stop_pct, target_pct (an option's are percents of its premium), trail_pct, exit_at: "
    "'15:15', on_leg_stop: exit_all | move_others_to_cost, max_loss_inr, target_inr (the whole "
    "position), exit_when}. exit_when is a `when` that closes the position; it may also read entry, "
    "bars_in, hi_since, lo_since and pnl_pct (the move in its favour), e.g. {left: close, op: below, "
    "right: 'hi_since - 2 * atr14'}. An option play clocked on its index exits on the index's levels "
    "this way.\n"
    "contract: {lifespan: now | once_when (default with a condition) | standing ('whenever', 'each "
    "time') | policy ('keep it'), authority: notify ('tell me') | propose (default for an order) | act "
    "('just do it'; the first unattended fire still asks once) | manage (act, and runs its exits), "
    "inferred_from (their exact words that set the dials, quoted; a dial above the default without "
    "them is refused), expires_in_days (a window they named), now: true or {size} when the first order differs (also act once immediately, "
    "then the lifespan takes over: 'rebalance now and keep it', 'buy 10k now and 5k on each dip')}. "
    "mandate: {capital_inr, max_fires}.\n"
    "Build exactly what was asked: keep their verb (a buy stays an order, an alert an alert), add no "
    "condition, exit, close, schedule, size, window or order they did not ask for (nor drop "
    "one they did); for vague words take the plainest reading, the shorter lifespan and the lower "
    "authority, and say it and what one tap upgrades. Code fills "
    "only what is required and says so; a useful extra is one sentence offering it.\n"
    "compile replays the play over stored history: how often it fires and, when it opens a position, "
    "every round trip through its exits against a control (the same exits from entries anywhere in "
    "the same hours). So 'has this worked?' about a play is answered by compiling it (arm: false only "
    "tests it). Everything is simulated in the paper books; nothing reaches a broker.")


def _play_tools() -> list[dict]:
    obj = {"type": "object", "additionalProperties": True}
    return [
        _fn("play", _PLAY_DOC,
            {"action": {"type": "string", "enum": ["compile", "edit", "list", "pause", "resume",
                                                   "delete", "explain", "review"]},
             "play": {**obj, "description": "the whole play, for compile and edit"},
             "play_id": {"type": "integer"},
             "arm": {"type": "boolean", "description": "false keeps it as a draft; default arms it"}},
            ["action"]),
        _fn("measure",
            "test a measure (the shape is in play's description) on a symbol, or define it so later plays "
            "can name it; list shows saved measures and securities. A play defines measures inline, so "
            "test only when unsure a formula is right.\n"
            "security makes the trader's own instrument, a symbol like any other: {name (becomes @NAME), "
            "title, kind: basket | ratio | spread, why, and pick or members, k (spread: A - k x B)}. A basket "
            "(2 to 30 names) starts at 100 a year back and holds its weights; a ratio is A / B. pick chooses "
            "members by a method you state, run by code: {industry (their words, a list is fine) or sector, "
            "rank_by: market_cap | turnover | return_1y | roe | low_volatility, top, min_turnover_cr, weight: "
            "equal | market_cap | inverse_vol}; each member comes back with the numbers that put it there. "
            "Prefer pick when they name a theme; members: [{symbol, weight, why (a fact)}] when they name "
            "stocks. Say the method, who made it and who was left out. When a play will use it in this "
            "request, put it in the play's `security` instead (one call).\n"
            "scan runs your measures and condition across many symbols and ranks what passes: {universe: "
            "all | fno | perps | @BASKET | [symbols] | {from, industry or sector, sql ('SELECT symbol "
            "...'), min_turnover_cr, top_by: market_cap | turnover, n}, interval, measures, when (the "
            "clause, as in a play), fresh (only names where it turned true within N bars), rank_by (a "
            "measure or field), order: desc | asc, top}. Use it to find what to watch or choose from: "
            "anything a measure computes (indicators, distance from highs, volume against average, "
            "strength against an index input, F&O data by SQL) can be scanned, and a play's universe "
            "can then watch the shortlist.",
            {"action": {"type": "string", "enum": ["test", "define", "list", "security", "scan"]},
             "measure": obj, "symbol": _STR, "interval": _IVS,
             "security": {**obj, "description": "the security, for action security"},
             "scan": {**obj, "description": "for action scan"}},
            ["action"]),
    ]


def _tools() -> list[dict]:
    when = _old_prop("set_alert", "when") or {"type": "array", "items": {"type": "object"}}
    feat = _old_prop("screen_universe", "filters") or {"type": "array", "items": {"type": "object"}}
    shape_names = ["hline", "band", "vline", "vband", "segment", "ray", "arrow", "box", "poly",
                   "measure", "dot", "candle", "marker", "note", "text"]
    try:
        import drawtools
        shape_names += list(drawtools.TOOLS)
    except Exception:  # noqa: BLE001
        pass
    return [
        _fn("read_chart",
            "Read any instrument's chart and get computed numbers back. Ask for several things and "
            "several timeframes in one call; they are computed together. Reads: trend (structure, ADX, "
            "DI, fitted trendlines), indicators (EMA 20/50, Supertrend, RSI, MACD, ADX snapshot), "
            "studies (any named indicator with its settings), levels (support and resistance with "
            "touch counts), trendlines, swings (swing highs and lows with ids), gaps, volume_profile "
            "(POC, value area), patterns (34 candlestick, 22 chart patterns, market structure), "
            "pattern_stats (how a named pattern has played out on this stock), divergences (RSI and "
            "MACD), reversal (what would confirm a turn), results_reaction (how price reacted to past "
            "results; stored dates exist for few companies, so for the rest find the announcement dates "
            "yourself and pass result_dates), move (why it moved over a window: anatomy, index share, news; pass from and to, "
            "or only the last session is explained), bars (raw "
            "OHLCV), drawings (score the trader's own drawings by id). Defaults to the chart in focus "
            "and the window on screen. draw=true marks what was found on the chart.",
            {"read": {"type": "array", "items": {"type": "string", "enum": READS},
                      "description": "what to read; any combination"},
             "symbol": {**_STR, "description": "any NSE/BSE stock, index, MCX commodity or crypto pair (BTCUSDT, ETHUSDT); omit for the chart in focus"},
             "intervals": {"type": "array", "items": _IVS,
                           "description": "one or more timeframes; omit for the chart's own"},
             "studies": {**_ARR, "description": "for studies: name and settings, e.g. 'rsi', 'ema 200', "
                         "'supertrend 10 3', 'bbands 20 2', 'vwap', 'macd', 'atr 14', 'obv'"},
             "patterns": {**_ARR, "description": "for patterns or pattern_stats: snake_case ids, e.g. "
                          "'head_and_shoulders', 'bull_flag', 'bullish_engulfing'; omit to scan all"},
             "direction": {"type": "string", "enum": ["bullish", "bearish"],
                           "description": "for reversal; omit to test both"},
             "drawing_ids": {**_ARR, "description": "for drawings: the trader's drawing refs, e.g. 'D3'"},
             "from": {**_STR, "description": "IST 'YYYY-MM-DD' or 'YYYY-MM-DD HH:MM': window start for move, bars, swings, volume_profile"},
             "to": {**_STR, "description": "window end"},
             "lookback_bars": {"type": "integer", "description": "reach further back than the screen shows"},
             "draw": {"type": "boolean", "description": "put levels, trendlines, patterns and studies on the chart"},
             "panel": {"type": "boolean", "description": "also show one compact panel of these readings in the "
                       "reply; only when a grid of numbers says more than the prose would, such as a "
                       "timeframe-by-timeframe comparison the trader asked for"},
             "detail": {"type": "string", "enum": ["brief", "full"]},
             "result_dates": {"type": "array", "items": {"type": "string"},
                              "description": "for results_reaction when the company has no stored "
                              "results dates: past announcement dates you found (YYYY-MM-DD, "
                              "oldest to newest in any order); reactions are measured on the real bars"}},
            ["read"]),
        _fn("draw",
            "Mark up the chart the way a trader would: levels, zones, trendlines, rays, arrows, boxes, "
            "rulers that print the move, Fibonacci and Gann tools, text notes and bar markers, each with "
            "its own colour, line style and caption. Every point is an ADDRESS resolved against the real "
            "bars, never a computed coordinate: a time ('2026-08-12', '09:15', 'open', '-20' bars back, "
            "'+10' bars ahead), a price (1450, 'high', 'low', 'close', '+2%'), or both ('-35 @ low', "
            "'2026-08-12 15:25 @ high'). A line reads each point off its own bar; a box reads both "
            "corners off the bars between its two times. repeat='session' draws a shape once per "
            "trading day. Prefer word addresses (high, low, open, close, a time) over typed prices, so "
            "the resolver reads the real bar. measure is a ruler that prints the points, percent and "
            "bars of a move. Batch the whole markup in one call. clear=true first "
            "removes what the chat drew. Examples: the opening range of the last five sessions is "
            "{shape:'box', from:'09:15 @ high', to:'09:30 @ low', repeat:'session', sessions:5}; a "
            "breakout path is {shape:'arrow', from:'-1 @ close', to:'+15 @ 1450', color:'blue', "
            "line:'dashed', label:'Measured target'}.",
            {"shapes": {"type": "array", "items": {"type": "object", "properties": {
                "shape": {"type": "string", "enum": shape_names},
                "at": {**_STR, "description": "address for a one-point shape"},
                "from": {**_STR, "description": "first address"},
                "to": {**_STR, "description": "second address"},
                "points": {**_ARR, "description": "3+ addresses for poly, or a ratio tool's ordered anchors"},
                "anchors": {**_ARR, "description": "swing ids from read_chart swings, instead of addresses; any shape"},
                "label": {**_STR, "description": "caption, or the text itself for note and text"},
                "color": {"type": "string", "enum": _PALETTE},
                "line": {"type": "string", "enum": ["solid", "dashed", "dotted"]},
                "weight": {"type": "string", "enum": ["thin", "normal", "thick"]},
                "role": {"type": "string", "enum": ["support", "resistance", "neutral"],
                         "description": "default colour when no color is given"},
                "repeat": {"type": "string", "enum": ["none", "session", "week"]},
                "sessions": {"type": "integer", "description": "how many repeats, default 5"}},
                "required": ["shape"]}},
             "interval": {**_IVS, "description": "timeframe the addresses resolve on; omit for the chart's"},
             "symbol": {**_STR, "description": "which chart on screen; omit for the one in focus"},
             "pane": {**_STR, "description": "'price' or an indicator pane such as 'rsi'"},
             "clear": {"type": "boolean"},
             "lookback_bars": {"type": "integer", "description": "must reach any date you address"}},
            ["shapes"]),
        _fn("workspace",
            "Arrange the trader's screen. Chart ops: show_chart (switch the main chart's symbol or "
            "timeframe), add_chart (a second chart beside it), add_study / remove_study (e.g. 'ema 200'), "
            "build_indicator / edit_indicator (a custom indicator from a plain description), "
            "list_indicators. Widget ops: open, write, configure, read, move, focus, close, describe. "
            "Open a widget and write into it in the same call; open reuses the widget of that kind "
            "already on the desk for the same symbol, and new=true adds a second one. What write takes: watch {add: [symbols], "
            "remove: [symbols]}; notes {text: light markdown with ### headings, bullets and - [ ] "
            "checklists, section: a name}; sheet {table: {title, columns, rows}}; screener {name, "
            "filters, industry, pattern}; browser {url or q}; strategy {name, steps}. News, financials, "
            "options and notes follow the chart in focus unless given a symbol. describe returns a "
            "widget's settings, for configure. Ops run in order. Widgets on screen are listed under "
            "'Workspace now'.",
            {"ops": {"type": "array", "items": {"type": "object", "properties": {
                "op": {"type": "string", "enum": sorted(_CHART_OPS) + ["open", "write", "configure",
                                                                      "read", "move", "focus",
                                                                      "close", "describe"]},
                "symbol": _STR, "interval": _IVS,
                "study": {**_STR, "description": "for add_study/remove_study"},
                "request": {**_STR, "description": "for build/edit_indicator: what it should compute"},
                "indicator_id": _STR,
                "type": {"type": "string", "enum": _WIDGETS},
                "id": {**_STR, "description": "widget id from 'Workspace now'"},
                "where": {**_STR, "description": "left|right|top|bottom|float|tab:<id>|split:<id>:<side>"},
                "settings": {"type": "object"},
                "content": {"type": "object", "description": "what write puts in the widget (keys above)"},
                "title": _STR,
                "link": {"type": "string", "enum": ["1", "2", "3", "4", "chart", "pin"]},
                "new": {"type": "boolean", "description": "open a second widget of a kind already on the desk; only when asked"}},
                "required": ["op"]}}},
            ["ops"]),
        _fn("market_data",
            "Company and market facts: profile (price, returns, levels, valuation and fundamentals), "
            "results (quarterly revenue, profit, margins, EPS and growth), peers, compare (several "
            "symbols side by side), deals (bulk and block), flows (FII and DII), option_chain (OI, OI "
            "change, IV, Greeks, PCR, max pain by strike).",
            {"kind": {"type": "string", "enum": ["profile", "results", "peers", "compare", "deals",
                                                 "flows", "option_chain"]},
             "symbols": {**_ARR, "description": "one or more; omit for the chart in focus"},
             "interval": {"type": "string", "enum": ["1d", "1w"]},
             "from": _STR, "to": _STR,
             "expiry": {**_STR, "description": "option expiry YYYY-MM-DD; omit for the nearest"},
             "strikes": {"type": "integer", "description": "strikes each side of the money"},
             "client": {**_STR, "description": "deals: a client name to filter on"},
             "limit": {"type": "integer"}},
            ["kind"]),
        _fn("search",
            "Search dated market news and events for an instrument, the open web, or the trader's own "
            "earlier conversations. Use short keywords; search again only if a fact you need is missing. "
            "social reads the latest posts at a social address given as query (the addresses in play's "
            "description, or a pasted link).",
            {"source": {"type": "string", "enum": ["news", "web", "conversations", "social"]},
             "query": _STR,
             "symbol": _STR,
             "from": {**_STR, "description": "news window start YYYY-MM-DD; default two weeks back"},
             "to": _STR},
            ["source"]),
        _fn("query",
            "Run your own analysis on Pivot's market database: one read-only SQL query, optionally "
            "followed by Python that computes statistics over every row it returns. Use it for anything "
            "the other tools do not answer directly: returns over custom windows, seasonality, "
            "day-of-week or time-of-day behaviour, gap fill rates, opening-range statistics, "
            "drawdowns, volatility, correlation, rankings across stocks, fundamentals filters, and NSE "
            "F&O history (fo_eod: every option and future by day with OI, volume and premiums; fo_snap: "
            "the live chain through the day) for OI buildup, PCR, max pain, IV or skew. Call with "
            "no sql to list the tables and columns. Example: sql \"SELECT ts, o, h, l, c FROM bars_1d "
            "WHERE symbol='RELIANCE' AND ts > strftime('%s','2025-01-01')\" with python \"def "
            "compute(data, params):\\n    c = data['c']\\n    r = [c[i]/c[i-1]-1 for i in range(1, "
            "len(c))]\\n    return {'days': len(r), 'up_share': sum(x > 0 for x in r)/len(r)}\".",
            {"sql": {**_STR, "description": "one SQLite SELECT or WITH; bars.ts is epoch seconds UTC "
                     "(IST = ts+19800); up to 50,000 rows reach python, 40 are shown without it"},
             "python": {**_STR, "description": "define compute(data, params); data is {column: [values]}; "
                        "return any JSON. math and ta (sma, ema, rma, stdev …) are available; no imports"},
             "params": {"type": "object"}}),
        _fn("screen",
            "Find stocks across the 500-stock universe by conditions on returns, momentum, trend, "
            "volatility, volume, 52-week distance, opening range and volume profile, optionally within "
            "an industry or with a recent daily pattern; or pass like=<symbol> to find the stocks whose "
            "chart looks most like that one now.",
            {"filters": feat,
             "industry": _STR,
             "pattern": {**_STR, "description": "a recent daily pattern, e.g. bull_flag"},
             "pattern_within": {"type": "integer"},
             "sort": {**_STR, "description": "feature to rank by"},
             "limit": {"type": "integer"},
             "like": {**_STR, "description": "find setups like this symbol's"},
             "interval": _IVS,
             "symbols": {**_ARR, "description": "restrict a like-search to these"}}),
        _fn("alerts",
            "Create, change, test against history, list or cancel alerts on price, indicators or the "
            "trader's drawings. A condition compares a left side ('close', 'rsi(14)', 'ema(50)', a "
            "drawing ref such as 'D3') with a number or another side.",
            {"action": {"type": "string", "enum": ["create", "update", "check", "list", "cancel"]},
             "alert_id": {"type": "integer"}, "symbol": _STR,
             "interval": {"type": "string", "enum": ["1m", "3m", "5m", "15m", "30m", "1h", "1d"]},
             "when": when, "all": {"type": "boolean", "description": "all conditions (default) or any"},
             "freq": {"type": "string", "enum": ["once", "per_bar", "per_bar_close", "per_day"]},
             "email": {"type": "boolean"}, "expires_in_days": {"type": "integer"},
             "note": _STR, "state": {"type": "string", "enum": ["armed", "paused", "fired"]}},
            ["action"]),
        _fn("journal",
            "The trader's journal and paper portfolio: log a trade with its plan and thesis, update or "
            "close it, review past trades, or show the paper holdings and P&L.",
            {"action": {"type": "string", "enum": ["log", "update", "list", "portfolio"]},
             "trade_id": {"type": "integer"}, "symbol": _STR,
             "side": {"type": "string", "enum": ["long", "short"]},
             "quantity": {"type": "number"}, "entry_price": {"type": "number"}, "entry_at": _STR,
             "exit_price": {"type": "number"}, "exit_at": _STR, "stop": {"type": "number"},
             "fees": {"type": "number"}, "thesis": _STR, "tags": _ARR,
             "status": {"type": "string", "enum": ["open", "closed"]},
             "lesson": _STR, "emotion": _STR, "plan": {"type": "object"},
             "changes": {"type": "object", "description": "update: any fields to change"},
             "limit": {"type": "integer"}},
            ["action"]),
        _fn("strategy",
            "Turn a trading idea into a rule and test it honestly. build (plain English to a rule), "
            "backtest (on real bars, with a trust verdict: insufficient data, no edge, unproven, "
            "promising), compare (several rule variants), plan (entry, stop, targets, size and risk, "
            "drawn on the chart), list, pause, delete saved strategies. Nothing is armed or traded.",
            {"action": {"type": "string", "enum": ["build", "backtest", "compare", "plan", "list",
                                                   "pause", "delete"]},
             "condition": {**_STR, "description": "entry rule in words, e.g. 'RSI 14 crosses above 30 "
                           "while close is above the 200 EMA'"},
             "exit_condition": _STR, "symbol": _STR, "interval": _IVS,
             "from": _STR, "to": _STR,
             "candidates": {"type": "array", "items": {"type": "object"},
                            "description": "compare: [{name, condition, exit_condition}]"},
             "capital": {"type": "number"},
             "exit_kind": {"type": "string", "enum": ["n_day_hold", "stop_loss_pct", "hold_to_end"]},
             "exit_bars": {"type": "integer"}, "exit_pct": {"type": "number"},
             "entry": {"type": "number"}, "stop": {"type": "number"}, "stop_atr": {"type": "number"},
             "targets": {"type": "array", "items": {"type": "number"}},
             "targets_r": {"type": "array", "items": {"type": "number"}},
             "qty": {"type": "integer"}, "risk_amount": {"type": "number"},
             "risk_pct": {"type": "number"},
             "side": {"type": "string", "enum": ["long", "short"]},
             "drawing_id": {**_STR, "description": "plan from the trader's long/short drawing"},
             "strategy_id": {"type": "integer"}, "resume": {"type": "boolean"}},
            ["action"]),
    ]


# ── the system prompt ────────────────────────────────────────────────────

PROMPT = """\
You are Pivot, the assistant inside a charting workspace for Indian markets. Traders and investors of every kind use you: intraday traders on 1 to 15 minute charts, swing traders on hourly and daily, positional traders and investors on daily and weekly, and options traders reading the chain. Infer the trader's horizon from the chart's interval and the question, and answer for that horizon.

## What you can do
You read charts with computed evidence (read_chart), mark them up (draw), arrange the screen and its widgets (workspace), pull company and market facts (market_data), search news, the web and past conversations (search), run your own SQL and statistics on the market database (query), screen the 500-stock universe (screen), manage alerts (alerts), keep the trade journal and paper portfolio (journal), and build, backtest and plan strategies (strategy). Coverage is NSE and BSE equities, NIFTY, BANKNIFTY and SENSEX, NSE options, MCX commodities and crypto as USDT pairs (BTCUSDT, ETHUSDT). For anything outside that, say so in one line and name the nearest listed proxy (US tech: MON100; gold: GOLDBEES).

## How to work
- Gather what the question needs in as few rounds as possible. read_chart takes several reads and several timeframes in one call, and independent calls in the same round run in parallel. A full read is usually one read_chart call plus one draw call.
- When no tool answers a question directly (seasonality, how often a gap fills, average move after an event, a custom ranking), write it yourself with query: SQL to fetch, Python to compute. First pin down the event and the outcome in the trader's own words ("the day after a gap down" is the NEXT session, measured against the gap day's close), compute exactly that, and state the definition and the sample size with the result.
- Questions about how a stock behaves around events (results, gaps, ex-dates) are answered from the real bars. If the stored dates are missing for that company, find them: search the news and exchange announcements for the past results dates, then hand them to read_chart results_reaction as result_dates, or compute from them with query. A first empty answer is where the work starts; keep going until you have a measured number with its sample size, and say where the dates came from.
- Pass only the parameters a request needs and leave the optional ones out. An expiry, a direction or a lookback the trader did not ask for changes the result.
- For a why-did-it-move question, read move with from and to set to the window being asked about.
- "The last swing leg" is the most recent completed move: from the last confirmed swing point to the one before it, or to the latest bar if price has moved on since.
- A follow-up builds on what the conversation already settled. A position plan for a setup you just tested keeps that setup's trigger and its exit rule (a 10-day hold stays a 10-day hold); when the setup is not live, plan from the current price and say what has to happen first.
- A question about an idea with no instrument in it ("what is a bear flag") is answered from knowledge, without tools.
- When a request is ambiguous in a way that changes the answer, answer the likelier reading and name the other in a clause. Ask only when the readings lead somewhere genuinely different.

## Evidence
- Every price, level, rate and date you state comes from a tool result in this turn or the chart context below. If a tool returns nothing or fails, say the value is unavailable and what you tried.
- A hit rate is stated with its sample size and the ordinary-day comparison ("worked 27 of 41 times, 66%, against 52% on an ordinary day"). Too few cases is a finding, say so.
- When a result's source is not Kite (a cache, yfinance, seed data), say so in a short clause.
- Never say something was drawn, saved, set or opened unless the tool that does it succeeded. Never say a tool cannot do something because one call failed; read the error and fix the call.
- A successful draw or workspace call is done; repeat it only to fix what its result reported.
- When you set an alert or an invalidation level, say in a sentence why that level matters on this chart.
- You give analysis and frameworks, not personal buy or sell advice: describe the structure, the levels and what would invalidate them, and let the trader decide. Do not write a disclaimer line; the app prints one under every answer.

## Marking up the chart
When the trader asks for a read, a setup, a plan or "what do you see", the chart is part of the answer. Draw the four to eight things the read rests on, in one draw call after the reads:
- key support and resistance as hline or band, with captions such as "R 1,452 · 3 tests";
- the trendline or channel that defines the move;
- the active pattern, a ruler (measure) on the last leg or the room to the next level;
- arrows for the expected path or a breakout direction, and short text notes at the decisive bar ("Close above confirms").
Use colour for meaning and keep it consistent: green for support and targets, red for resistance and stops, blue for trendlines and paths, orange for the zone being watched, grey for context. Solid lines for confirmed structure, dashed for projections and conditional levels. Use read_chart draw=true when the detected levels or patterns themselves are what you want on the chart. Whatever your answer names by price or date and the trader would want to see, such as a breakout level, a support zone, a projected line, or the dates of past results with the move after each, goes on the chart in the same turn with its caption, so the prose and the drawing say the same thing (results dates become a vline each, captioned with the quarter and the move). Do not draw for a quick factual question or when asked not to. Say in one line what you drew.

## The workspace
Open or fill widgets when the work produces something the trader will come back to: notes from a read (short lines, levels and a checklist of triggers), a sheet for a table worth keeping, a screener for a scan they may refine, a watchlist for names to follow, a second chart for the index or a peer. A widget you open for a purpose starts useful: write the notes, the table or the screen into it in the same call (a notes widget for results day gets the dates, levels and what to watch). When 'Workspace now' already has that kind for the same symbol, use it rather than adding a copy (open does this unless new is set). A quick answer opens nothing. Workspace changes can go in the same round as the reads. Say which symbol each opened widget follows.

## Writing the answer
Your reply renders as markdown in a narrow pane beside the chart. Aim for a mid-length, structured answer:
- Start with the bottom line in one or two sentences, with the one number that carries it in bold.
- Then two to four short sections with ### headings named for what they hold (for example Trend, Levels to watch, Momentum, Plan, What would change the view). Each section is two to four sentences or a few bullets.
- Use a markdown table when the same fields repeat across levels, timeframes or symbols, with numbers right-aligned (|---:|).
- Analysis runs about 180 to 320 words. A quick factual or capability question gets a direct answer under 90 words. An explainer gets 150 to 250 words with one worked example.
- Use a trader's words: trend, support, resistance, breakout, pullback, higher highs, RSI, ADX. Give a technical term its meaning in a few words the first time it matters. Translate tool vocabulary (rung, pooled, panel, detector, edge, tally) into plain trading language.
- Punctuate with commas, colons, semicolons, full stops and parentheses. Use a dash only in a numeric range (1,289-1,299).
- When a tool result's _panel says a panel is shown, place it: write [[panel]] on its own line where the reader should meet it, after the sentence that introduces it, and let the panel carry its numbers. A panel is for a table the trader would otherwise scroll through (an option chain, a screen, a backtest); a read is told in prose.
- Cite news or web facts inline as a markdown link naming the publisher: ([Economic Times](https://...)). Chart and database numbers need no citation.
- Calm, professional voice. No emoji, no hype. Defend a view when the evidence supports one, and say what would invalidate it.

<example>
**RELIANCE is in a daily uptrend that is pausing under 1,452**, with momentum cooling but no break in structure.

### Trend
Higher highs and higher lows since 12 Aug; ADX 27 says the trend is real, and +DI still leads -DI.

### Levels to watch
| Level | Why it matters |
|---|---:|
| 1,452 | resistance, rejected 3 times since Sep |
| 1,398 | support, the last higher low |
| 1,365 | 50-day EMA |

### Momentum
RSI 58, off its 71 peak; MACD histogram positive but shrinking, so buyers are slowing rather than leaving.

### What would change the view
A daily close below 1,398 breaks the higher-low sequence. A close above 1,452 on rising volume opens the measured move to about 1,520.

I marked the two levels, the trendline and a ruler on the last leg.
</example>
(The numbers in the example are placeholders; real answers use tool values only.)"""


_PLAYS_LINE = (
    "manage the trade journal and paper portfolio (journal), and turn any idea into a play that "
    "watches price, indicators, your own measures, NSE filings or news headlines and tells the trader, "
    "proposes an order, or places it on paper (play, measure)")


def prompt() -> str:
    if not plays_on():
        return PROMPT
    return PROMPT.replace(
        "manage alerts (alerts), keep the trade journal and paper portfolio (journal), and build, "
        "backtest and plan strategies (strategy)", _PLAYS_LINE)


def system_block(context_block: str) -> str:
    """The fixed prompt first, the per-turn chart context after it, so the
    prompt stays in the cached prefix while the context changes every turn."""
    return "\n\n".join(x for x in (prompt(), context_block) if x)
