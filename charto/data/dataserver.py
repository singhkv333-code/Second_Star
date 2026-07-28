"""Charto preview data server — serves the local 1-min store on :5174.

Resamples the raw 1-minute bars (charto_bars.db) into any interval the chart
asks for, with paging for infinite-scroll history. Stdlib only; CORS open to
the preview origin.

Intervals: 1m 3m 5m 15m 30m 1h  (anchored to each session's first bar, so
09:15 IST opens a bucket exactly like Kite/TradingView do), plus 1d 1w 1mo
(aggregated on IST trade dates; note: daily close here = last 1-min close,
which differs slightly from NSE's official 30-min-VWAP close).

GET /bars?symbol=RELIANCE&interval=5m&limit=3000[&to=<epoch_s exclusive>]
  -> {symbol, interval, bars:[{t,o,h,l,c,v}], has_more, earliest, latest}
GET /meta?symbol=RELIANCE -> {symbol, count, earliest, latest}
GET /stream?symbol=RELIANCE -> SSE {type:"bar", closed_1m, bars:{1m..1h,1d}}
GET /replay?symbol=RELIANCE&speed=300[&date=YYYY-MM-DD][&stop=1]
  re-feeds a stored session through the live tick engine (dev driver; the
  same seam a Kite websocket would use). stop=1 returns the symbol to idle.

Run:  python3 charto/data/dataserver.py   (from repo root; port 5174)
"""
from __future__ import annotations

import json
import logging
import queue
import sqlite3
import threading
import time
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import indicators   # sibling module: the indicator registry
import patterns   # sibling module: candlestick / chart-pattern / structure detectors

DB_PATH = Path(__file__).parent / "charto_bars.db"
PORT = 5174

# ── Azure LLM proxy config (same Foundry endpoint Pivot chat uses) ──
# Read from pivot/.env so the key never lands in browser-served files.
_ENV_PATH = Path(__file__).resolve().parents[2] / "pivot" / ".env"
LLM_DEPLOYMENT = "gpt-5.6-luna"
LLM_EFFORT = "medium"
# Azure priority processing — premium-billed, lower/steadier latency. The
# response echoes the tier actually served; verify there, not here.
LLM_SERVICE_TIER = "priority"


def _load_azure_creds() -> tuple[str, str]:
    endpoint = key = ""
    try:
        for line in _ENV_PATH.read_text().splitlines():
            line = line.strip()
            if line.startswith("AZURE_OPENAI_ENDPOINT="):
                endpoint = line.split("=", 1)[1].strip()
            elif line.startswith("AZURE_KEY="):
                key = line.split("=", 1)[1].strip()
    except OSError:
        pass
    return endpoint.rstrip("/"), key


AZURE_ENDPOINT, AZURE_KEY = _load_azure_creds()


# ══════════════════════════════════════════════════════════════════
# TOOLS — the model picks, this code computes. Nothing here guesses.
# ══════════════════════════════════════════════════════════════════

# Annotations a tool decides to draw, collected per request (the server is
# threaded, so this is thread-local) and returned to the FE as a scene patch.
_scene = threading.local()


def _scene_reset() -> None:
    _scene.items = []
    _scene.drawn = []
    # anchors minted by ADDRESS (a named date, a scoped range) live for the
    # turn so draw_shape can re-resolve them without knowing the address
    _scene.minted_anchors = {}


def _mint_anchor(aid: str, entry: dict) -> None:
    if not hasattr(_scene, "minted_anchors"):
        _scene.minted_anchors = {}
    _scene.minted_anchors[aid.upper()] = entry


def _minted_anchors() -> dict:
    return getattr(_scene, "minted_anchors", {}) or {}


# ── per-request symbol ────────────────────────────────────────────
# One dataserver, 500 companies. Every tool and query reads the symbol
# from here; do_GET / do_POST stamp it per request. Symbols hydrate on
# first touch from the blob universe (parquet → local SQLite, ~10 s once)
# via hydrate_symbol.py under the pivot venv — this process stays stdlib.
_req = threading.local()


def _sym() -> str:
    return getattr(_req, "symbol", "RELIANCE")


_SYMBOLS_PATH = Path(__file__).parent / "symbols.json"
_symbols_cache: list[str] = []
_HYDRATE_LOCKS: dict[str, threading.Lock] = {}
_HYDRATE_GUARD = threading.Lock()
_VENV_PY = Path(__file__).resolve().parents[2] / "pivot" / ".venv" / "bin" / "python"


def _known_symbols() -> list[str]:
    global _symbols_cache
    if not _symbols_cache and _SYMBOLS_PATH.exists():
        _symbols_cache = json.loads(_SYMBOLS_PATH.read_text())
    return _symbols_cache


def _symbol_ready(sym: str) -> bool:
    return bool(_con.execute(
        "SELECT 1 FROM bars WHERE symbol=? LIMIT 1", (sym,)).fetchone())


def _ensure_symbol(sym: str) -> dict | None:
    """None when the symbol is servable; an error dict otherwise."""
    if _symbol_ready(sym):
        return None
    if sym not in _known_symbols():
        return {"error": f"unknown symbol {sym}",
                "hint": "GET /symbols lists the universe"}
    with _HYDRATE_GUARD:
        lock = _HYDRATE_LOCKS.setdefault(sym, threading.Lock())
    with lock:
        if _symbol_ready(sym):
            return None
        import subprocess
        r = subprocess.run(
            [str(_VENV_PY), str(Path(__file__).parent / "hydrate_symbol.py"),
             sym], capture_output=True, text=True, timeout=300)
        if r.returncode != 0:
            return {"error": f"hydration failed for {sym}",
                    "detail": (r.stderr or r.stdout)[-400:]}
        logging.info("hydrated %s: %s", sym, r.stdout.strip())
    return None


def _scene_add(annotation: dict) -> None:
    # Stamp WHICH tool put this on the chart. A clear used to match on kind
    # alone and reach across tools — get_levels(draw_mode="replace") silently
    # wiped a marked head and shoulders. Clears now scope by owner, so
    # narrowing your levels cannot erase another tool's geometry.
    if "owner" not in annotation and annotation.get("kind") not in ("clear", "clear_levels"):
        src = annotation.get("source") or {}
        annotation["owner"] = src.get("tool", "scene")
    if not hasattr(_scene, "items"):
        _scene.items = []
    if not hasattr(_scene, "drawn"):
        _scene.drawn = []
    _scene.items.append(annotation)
    # Ledger of what is on the CHART, not what this call drew. `items` is
    # drained after every tool call, so a second additive call in the same
    # turn would otherwise report "exactly these are drawn" while three more
    # from the first call are still sitting on screen.
    kind = annotation.get("kind")
    if kind in ("level", "zone", "segment"):
        _scene.drawn.append(annotation.get("label") or annotation.get("id"))
    elif kind in ("clear", "clear_levels"):
        _scene.drawn = []


def _drawn_ledger() -> str:
    led = getattr(_scene, "drawn", None) or []
    if not led:
        return ""
    return ("Everything now on the user's chart: " + "; ".join(led)
            + ". Describe these and only these as drawn — anything you drew "
              "earlier in this turn is still there, so include it.")


def _scene_take() -> list[dict]:
    items = getattr(_scene, "items", [])
    _scene.items = []
    return items


# ── the user's drawings, addressable by id ────────────────────────
# Set once per turn from the chart envelope. Before this existed the model
# had to TRANSCRIBE a drawing's coordinates into evaluate_* arguments, which
# is exactly the class of thing that goes silently wrong (a mis-copied
# timestamp scores a different line and still returns a confident number).
# A reference is checked; a transcription is not.
_drawings = threading.local()

# Which evaluator owns which drawing type. A type absent here has no honest
# scoring path, and saying so beats scoring it as something it is not.
_DRAW_KIND = {
    "trend": ("line", None), "ray": ("line", None), "extended": ("line", None),
    "hline": ("line", None),
    "fib": ("fib", None),
    "rect": ("drawing", "zone"), "priceRange": ("drawing", "zone"),
    "channel": ("drawing", "channel"), "regression": ("drawing", "channel"),
    "long": ("drawing", "position"), "short": ("drawing", "position"),
}
_TOOL_FOR = {"line": "evaluate_line", "fib": "evaluate_fib",
             "drawing": "evaluate_drawing"}


def _drawings_set(ctx: dict | None) -> None:
    _drawings.by_ref = {}
    for d in (ctx or {}).get("drawings") or []:
        for key in (d.get("ref"), d.get("id")):
            if key:
                _drawings.by_ref[str(key).upper()] = d
    # chat-drawn annotations, addressable by their scene id. The FE lets the
    # user DRAG these, so the context copy is the current truth — resolving
    # from it (never from what a tool drew earlier) is what makes a moved
    # plan re-price as it now stands.
    _drawings.chat_by_id = {}
    for d in (ctx or {}).get("chat_drawings") or []:
        if d.get("id"):
            _drawings.chat_by_id[str(d["id"]).upper()] = d


_CHAT_AS_TYPE = {"level": "hline", "zone": "rect", "segment": "trend",
                 "fib": "fib"}


def _chat_drawing_as_user(c: dict) -> dict | None:
    """A chat annotation reshaped so the evaluate tools can score it."""
    k = c.get("kind")
    if k == "position":
        tgt = (c.get("targets") or [None])[0]
        if tgt is None or c.get("entry") is None or c.get("stop") is None:
            return None
        pts = [{"p": c["entry"]}, {"p": tgt}, {"p": c["stop"]}]
        return {"type": "short" if c.get("side") == "short" else "long",
                "pts": pts, "id": c.get("id"), "_chat": c}
    if k == "level":
        return {"type": "hline", "pts": [{"p": c.get("price")}],
                "id": c.get("id"), "_chat": c}
    if k == "zone":
        return {"type": "rect", "pts": [{"p": c.get("lo")}, {"p": c.get("hi")}],
                "id": c.get("id"), "_chat": c}
    if k in ("segment", "fib"):
        p1, p2 = c.get("p1") or {}, c.get("p2") or {}
        return {"type": _CHAT_AS_TYPE[k],
                "pts": [{"t": p1.get("t"), "p": p1.get("p")},
                        {"t": p2.get("t"), "p": p2.get("p")}],
                "id": c.get("id"), "_chat": c}
    return None


def _drawing_get(ref: str) -> dict:
    """A drawing by ref/id, or an error naming what actually exists."""
    by = getattr(_drawings, "by_ref", None) or {}
    d = by.get(str(ref or "").upper().strip())
    if d:
        return {"ok": d}
    chat = getattr(_drawings, "chat_by_id", None) or {}
    c = chat.get(str(ref or "").upper().strip())
    if c:
        conv = _chat_drawing_as_user(c)
        if conv:
            return {"ok": conv}
    avail = sorted({v.get("ref") or v.get("id") for v in by.values()}
                   | set(chat.keys()))
    return {"error": f"no drawing '{ref}' on this chart",
            "available": avail,
            "_note": ("Nothing was scored. The user's drawings are listed in "
                      "the chart context with their refs — use one of those "
                      "exactly, and if the list is empty say the user has not "
                      "drawn anything rather than inventing coordinates.")
            if avail else
            ("The user has no drawings on this chart. Say so — do not "
             "score coordinates you made up.")}


def _drawing_points(d: dict) -> list[dict]:
    """Anchors as {t, v}. A horizontal line carries one anchor, so its second
    point is synthesised at the same value — the line is flat either way."""
    pts = [{"t": p.get("t"), "v": p.get("p", p.get("v"))} for p in d.get("pts") or []]
    if d.get("type") == "hline" and len(pts) == 1:
        pts.append({"t": pts[0]["t"], "v": pts[0]["v"], "_flat": True})
    return pts


def _drawing_for(ref: str, want: str) -> dict:
    """Resolve `ref` and confirm this tool is the one that scores its type."""
    got = _drawing_get(ref)
    if "error" in got:
        return got
    d = got["ok"]
    kind = _DRAW_KIND.get(d.get("type"))
    if not kind:
        return {"error": f"a {d.get('type')} drawing has no scoring method",
                "_note": (f"{d.get('type')} is not a shape with a record to "
                          f"check — describe what it marks instead, and say "
                          f"plainly that it cannot be scored. Scoreable: "
                          f"lines, fibs, rectangles, channels, positions.")}
    family, sub = kind
    if family != want:
        return {"error": f"{d.get('type')} is scored by {_TOOL_FOR[family]}, "
                         f"not this tool",
                "call": _TOOL_FOR[family],
                "_note": (f"Nothing was scored. Re-call {_TOOL_FOR[family]} "
                          f"with drawing_id={d.get('ref') or d.get('id')}.")}
    return {"ok": d, "sub": sub, "points": _drawing_points(d)}

def _ist(ts: int, with_time: bool = True) -> str:
    d = datetime.fromtimestamp(ts + IST_OFF, tz=timezone.utc)
    return d.strftime("%d %b %Y %H:%M") if with_time else d.strftime("%d %b %Y")


def _parse_ist(s: str | None) -> int | None:
    """Tolerant IST timestamp parse → epoch seconds.

    It MUST accept the format `_ist` emits ("08 Jul 2026 15:25"), because that
    is the only time format the model ever sees — in tool results, in anchors,
    and in the chart-context drawings list. A parser that took ISO alone was
    rejecting every timestamp the model could honestly have copied, which made
    `evaluate_line` fail outright and made `get_bars` silently answer with the
    wrong window. The display format is the contract; ISO is the extra.
    """
    if not s:
        return None
    s = s.strip().replace("T", " ").replace(",", " ")
    s = " ".join(s.split())  # collapse the double space in "8  Jul 2026"
    for fmt in ("%d %b %Y %H:%M", "%d %b %Y", "%d %B %Y %H:%M", "%d %B %Y",
                "%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d",
                "%d-%m-%Y %H:%M", "%d/%m/%Y %H:%M", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            d = datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
            return int(d.timestamp()) - IST_OFF
        except ValueError:
            continue
    return None


# ── indicator math ────────────────────────────────────────────────
# Deliberately the SAME formulas as preview/js/indicators.js: the tool
# must never disagree with the value drawn on the user's chart.

def _sma(v: list[float], n: int) -> list[float | None]:
    out, s = [], 0.0
    for i, x in enumerate(v):
        s += x
        if i >= n:
            s -= v[i - n]
        out.append(s / n if i >= n - 1 else None)
    return out


def _ema(v: list[float], n: int) -> list[float | None]:
    out, k, prev = [], 2 / (n + 1), None
    for i, x in enumerate(v):
        prev = x if prev is None else x * k + prev * (1 - k)
        out.append(prev if i >= n - 1 else None)
    return out


def _rsi(v: list[float], n: int = 14) -> list[float | None]:
    out: list[float | None] = [None]
    ag = al = 0.0
    for i in range(1, len(v)):
        ch = v[i] - v[i - 1]
        g, l = max(ch, 0.0), max(-ch, 0.0)
        if i <= n:
            ag += g / n; al += l / n
        else:
            ag = (ag * (n - 1) + g) / n; al = (al * (n - 1) + l) / n
        # the warmup guard has to come FIRST. Written as
        #   100.0 if al == 0 else <value> if i >= n else None
        # the all-zero-losses branch skipped the guard entirely, so an
        # unbroken rise from the start of the window emitted RSI 100 for
        # bars 1..n-1 — values the chart does not plot and nothing has yet
        # earned. `_divergences` reads this series, so a phantom leg could
        # be seeded from bars that are not really there.
        out.append(None if i < n else (100.0 if al == 0 else 100 - 100 / (1 + ag / al)))
    return out


def _atr(rows: list[tuple], n: int = 14) -> list[float | None]:
    out: list[float | None] = []
    prev_c, a = None, None
    for i, r in enumerate(rows):
        _, o, h, l, c, _v = r
        tr = h - l if prev_c is None else max(h - l, abs(h - prev_c), abs(l - prev_c))
        a = tr if a is None else (a * (n - 1) + tr) / n
        out.append(a if i >= n else None)
        prev_c = c
    return out


# ── support / resistance ──────────────────────────────────────────
# Pivot rule is the one in backend/core/indicators/patterns.py
# (`support_resistance_levels`): a bar is a pivot when its high/low is
# the extremum of the ±`window` bars around it. That function returns
# bare prices only — no clustering, no touch counts, no timestamps —
# so the evidence layer Charto needs is added here. Graduation path:
# upstream this richer version into patterns.py.

_EVIDENCE_HORIZON = 20  # bars allowed to judge a touch — scale-free by design:
                        # 20 bars is "the short run" on 5m and on 1d alike.


def _not_found_note(missing: list[str], kind: str, interval: str,
                    lookback_bars: int, available: list[str]) -> dict:
    """A draw_ids reference that matched nothing must SAY so.

    An unmatched id draws nothing and, without this, the tool result is
    silent about it — so the model goes on to describe a {kind} it believes
    is on the chart. get_levels had this guard from the start; the same
    selection code was copied into three other detectors without it, so it
    lives here now and every caller uses the one implementation.
    """
    if not missing:
        return {}
    return {"not_found": missing,
            "_not_found_note": (
                f"These {kind} ids do not exist at interval={interval}, "
                f"lookback_bars={lookback_bars}: {', '.join(missing)}. They "
                f"were NOT drawn — do not describe them as marked. Ids are "
                f"content-addressed, so re-read the candidate list rather "
                f"than guessing."),
            "available_ids": available[:12]}


def _median(xs: list[float]) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def _evidence(rows: list[tuple], hits: list[tuple], price: float,
              tol: float, window: int = 5) -> dict:
    """What actually happened the times price came BACK to this level.

    Two exclusions keep this from being a tautology dressed up as a stat:

    1. The earliest pivot is skipped. It CREATED the level; it did not test
       one. Grading it would score every level against its own definition.
    2. Each re-test is judged only after its own ±window, because a pivot is
       a local extremum by construction — price cannot clear it inside that
       window, so counting those bars would manufacture holds.

    Each touch is judged as the pivot it WAS: a swing high breaks when a
    later close clears the level, a swing low when a close drops through it.
    Judging by the level's CURRENT role would mis-score every flip — the
    exact levels traders care about most.

    A touch without a full horizon of future bars is left ungraded rather
    than counted as a hold; silently scoring it would flatter recent levels.
    """
    highs = [r[2] for r in rows]
    lows = [r[3] for r in rows]
    closes = [r[4] for r in rows]
    held = broke = pending = 0
    react: list[float] = []
    bars: list[float] = []
    # chronological — `hits` arrives in price order, so the "first" touch is
    # only the defining one once sorted by index
    for rank, (i, kind) in enumerate(sorted(hits)):
        if rank == 0:
            continue
        start = i + window + 1
        stop = start + _EVIDENCE_HORIZON
        if stop > len(rows):
            pending += 1
            continue
        up = kind == "resistance"
        thr = price + tol if up else price - tol
        best, best_j, broken = 0.0, 0, False
        for j in range(start, stop):
            if (closes[j] > thr) if up else (closes[j] < thr):
                broken = True
                break
            away = (price - lows[j]) if up else (highs[j] - price)
            if away > best:
                best, best_j = away, j - i
        if broken:
            broke += 1
        else:
            held += 1
            react.append(best / price * 100)
            bars.append(best_j)

    ev: dict = {"held": held, "broke": broke}
    if pending:
        ev["pending"] = pending
    # A percentage off three touches reads as precision we do not have.
    # Counts disclose their own sample size; a rate does not. State the
    # withholding as a FIELD rather than leaving the key absent: a missing
    # key is silence, and silence gets filled in with a computed percentage.
    ev.update(_rate("hold_rate", held, broke, "graded re-test"))
    if react:
        ev["react_pct"] = round(_median(react), 2)
        ev["react_bars"] = int(_median(bars))
    return ev


def _pivots(rows: list[tuple], window: int = 5) -> list[tuple]:
    """Swing highs/lows — the one definition every detector shares, so a
    trendline, a level and a divergence all agree on what a swing is."""
    highs = [r[2] for r in rows]
    lows = [r[3] for r in rows]
    out: list[tuple] = []
    for i in range(window, len(rows) - window):
        if highs[i] == max(highs[i - window:i + window + 1]):
            out.append((i, highs[i], "resistance"))
        if lows[i] == min(lows[i - window:i + window + 1]):
            out.append((i, lows[i], "support"))
    return out


def _tolerance(rows: list[tuple]) -> float:
    """Cluster/touch tolerance from real volatility (ATR), not a magic %."""
    atr_series = [a for a in _atr(rows, 14) if a is not None]
    return (atr_series[-1] * 0.5) if atr_series else rows[-1][4] * 0.002


def _levels(rows: list[tuple], window: int = 5, per_side: int = 4,
            with_time: bool = True) -> list[dict]:
    if len(rows) < window * 2 + 5:
        return []
    closes = [r[4] for r in rows]
    last = closes[-1]

    pivots = _pivots(rows, window)
    if not pivots:
        return []
    tol = _tolerance(rows)

    clusters: list[dict] = []
    for idx, price, kind in sorted(pivots, key=lambda p: p[1]):
        if clusters and abs(price - clusters[-1]["_sum"] / clusters[-1]["touches"]) <= tol:
            c = clusters[-1]
            c["_sum"] += price; c["touches"] += 1
            c["first_idx"] = min(c["first_idx"], idx)
            c["last_idx"] = max(c["last_idx"], idx)
            c["hits"].append((idx, kind))
            c["lo"] = min(c["lo"], price); c["hi"] = max(c["hi"], price)
        else:
            clusters.append({"_sum": price, "touches": 1, "hits": [(idx, kind)],
                             "first_idx": idx, "last_idx": idx,
                             "lo": price, "hi": price})

    out = []
    for c in clusters:
        price = round(c["_sum"] / c["touches"], 2)
        n = c["touches"]
        # A level is really a band, and its width is not a styling choice:
        # `tol` is the distance within which this detector already counts a
        # touch, so that IS the zone. The raw pivot spread is often a rupee
        # or two — true, but narrower than the thing it describes.
        lo, hi = c["lo"], c["hi"]
        if hi - lo < tol:
            mid = (hi + lo) / 2
            lo, hi = mid - tol / 2, mid + tol / 2
        lo, hi = round(lo, 2), round(hi, 2)
        out.append({
            "price": price,
            "zone_lo": lo, "zone_hi": hi,
            "role": "resistance" if price > last else "support",
            "touches": n,
            # graded here, not by the model: a 1-touch pivot is not a level
            # anyone should lean on, and the model shouldn't get to decide
            # what "strong" means. Evidence-hierarchy rule, applied in code.
            "strength": "strong" if n >= 4 else "moderate" if n >= 2 else "weak",
            "first_touch": _ist(rows[c["first_idx"]][0], with_time),
            "last_touch": _ist(rows[c["last_idx"]][0], with_time),
            "bars_since_last_touch": len(rows) - 1 - c["last_idx"],
            "distance_pct": round((price - last) / last * 100, 2),
            # what happened the last N times price actually reached it
            "evidence": _evidence(rows, c["hits"], price, tol, window),
        })
    # Balance the sides: a pure strength sort can return all-resistance and
    # force a second call. Take the strongest few of each, nearest first.
    # Ranked by NET evidence (held − broke), not by touch count and not by
    # holds alone: the most-touched level on this chart broke 14 of 26 tests,
    # so both of those sorts would lead with the least reliable line.
    rank = lambda x: (-(x["evidence"]["held"] - x["evidence"]["broke"]),  # noqa: E731
                      -x["touches"], abs(x["distance_pct"]))
    sup = sorted([x for x in out if x["role"] == "support"], key=rank)[:per_side]
    res = sorted([x for x in out if x["role"] == "resistance"], key=rank)[:per_side]
    final = sorted(sup + res, key=lambda x: x["price"])
    # CONTENT-ADDRESSED ids so the model can curate by reference without ever
    # typing a price. Derived from the level itself, NOT its position: an
    # ordinal id (L1, L2…) silently pointed at a different level when the
    # lookback changed between the review call and the draw call.
    seen: dict[str, int] = {}
    for lv in final:
        base = f"L{int(round(lv['price']))}"
        seen[base] = seen.get(base, 0) + 1
        lv["id"] = base if seen[base] == 1 else f"{base}{chr(96 + seen[base])}"
    return final


def _trendlines(rows: list[tuple], window: int = 5, want: int = 6,
                with_time: bool = True) -> list[dict]:
    """Sloped lines fitted through real swings.

    Any two points define a line, which is why two points prove nothing: a
    trendline needs a THIRD swing that respected it. Anchors come from the
    shared pivot pass, so the model never supplies a coordinate.
    """
    if len(rows) < window * 2 + 20:
        return []
    closes = [r[4] for r in rows]
    highs = [r[2] for r in rows]
    lows = [r[3] for r in rows]
    tol = _tolerance(rows)
    n = len(rows)
    piv = _pivots(rows, window)
    out: list[dict] = []

    for role in ("resistance", "support"):
        pts = [(i, p) for i, p, k in piv if k == role]
        if len(pts) < 3:
            continue
        cands = []
        for a in range(len(pts)):
            for b in range(a + 1, len(pts)):
                i1, p1 = pts[a]
                i2, p2 = pts[b]
                if i2 - i1 < window * 2:
                    continue
                slope = (p2 - p1) / (i2 - i1)
                at = lambda k: p1 + slope * (k - i1)  # noqa: E731
                # a swing counts as a touch when it sits on the line
                touches = [i for i, p in pts if i1 <= i <= i2 and abs(p - at(i)) <= tol]
                if len(touches) < 3:
                    continue
                # pierced = the line failed to contain price between anchors
                up = role == "resistance"
                pierce = sum(
                    1 for k in range(i1, i2 + 1)
                    if ((closes[k] > at(k) + tol) if up
                        else (closes[k] < at(k) - tol)))
                if pierce > (i2 - i1) * 0.15:
                    continue
                # does it still matter? project to the last bar
                now = at(n - 1)
                broken = (closes[-1] > now + tol) if role == "resistance" \
                    else (closes[-1] < now - tol)
                cands.append({
                    "role": role, "i1": i1, "p1": round(p1, 2),
                    "i2": i2, "p2": round(p2, 2),
                    "touches": len(touches), "span_bars": i2 - i1,
                    "slope_per_bar": round(slope, 4),
                    "projects_to": round(now, 2),
                    "distance_pct": round((now - closes[-1]) / closes[-1] * 100, 2),
                    "status": "broken" if broken else "intact",
                    "last_touch_bars_ago": n - 1 - max(touches),
                })
        # keep the best few per side, and drop near-duplicates of one another
        cands.sort(key=lambda c: (-c["touches"], -c["span_bars"]))
        kept: list[dict] = []
        for c in cands:
            if any(abs(c["projects_to"] - k["projects_to"]) <= tol
                   and abs(c["slope_per_bar"] - k["slope_per_bar"]) <= abs(c["slope_per_bar"] or 1) * 0.25
                   for k in kept):
                continue
            kept.append(c)
            if len(kept) >= want // 2:
                break
        out += kept

    for c in out:
        c["from"] = _ist(rows[c["i1"]][0], with_time)
        c["to"] = _ist(rows[c["i2"]][0], with_time)
        c["_t1"], c["_t2"] = rows[c["i1"]][0], rows[c["i2"]][0]
    # content-addressed: anchored to the prices it connects, so an id means
    # the same line whatever the lookback
    seen: dict[str, int] = {}
    for c in out:
        base = f"TL{int(round(c['p1']))}-{int(round(c['p2']))}"
        seen[base] = seen.get(base, 0) + 1
        c["id"] = base if seen[base] == 1 else f"{base}{chr(96 + seen[base])}"
    return out


def _divergences(rows: list[tuple], osc: list, window: int = 5,
                 with_time: bool = True) -> dict:
    """Price vs oscillator disagreement, with its own hit rate.

    The literature is blunt that divergences fire often and fail often, so a
    bare "bearish divergence" label would be the folklore we refuse to ship.
    Every instance in the window is therefore scored the same way levels are
    — and, per the Phase-5 rail, scoring starts AFTER the pivot's own window,
    since a swing high is a local maximum by construction and price dropping
    right after it is arithmetic, not prediction.
    """
    n = len(rows)
    closes = [r[4] for r in rows]
    piv = _pivots(rows, window)
    found: list[dict] = []

    for role, sign in (("resistance", 1), ("support", -1)):
        pts = [(i, p) for i, p, k in piv if k == role
               if osc[i] is not None]
        for a in range(len(pts) - 1):
            i1, p1 = pts[a]
            i2, p2 = pts[a + 1]
            if not (window * 2 <= i2 - i1 <= 120):
                continue
            o1, o2 = osc[i1], osc[i2]
            # bearish: higher price high, lower oscillator high (sign +1)
            price_extends = (p2 - p1) * sign > 0
            osc_fails = (o2 - o1) * sign < 0
            if not (price_extends and osc_fails):
                continue
            if abs(o2 - o1) < 1.0:          # noise, not disagreement
                continue
            start = i2 + window + 1
            outcome, move = None, None
            if start + 20 <= n:
                fwd = closes[start:start + 20]
                move = (min(fwd) - closes[i2]) / closes[i2] * 100 if sign > 0 \
                    else (max(fwd) - closes[i2]) / closes[i2] * 100
                # "resolved" = price went the way the divergence implied
                outcome = "resolved" if (move * -sign) > 0 and abs(move) >= 0.5 \
                    else "failed"
            found.append({
                "type": "bearish" if sign > 0 else "bullish",
                "role": role, "i1": i1, "i2": i2,
                "price_from": round(p1, 2), "price_to": round(p2, 2),
                "osc_from": round(o1, 2), "osc_to": round(o2, 2),
                "from": _ist(rows[i1][0], with_time), "to": _ist(rows[i2][0], with_time),
                "_t1": rows[i1][0], "_t2": rows[i2][0],
                "bars_ago": n - 1 - i2,
                "outcome": outcome,
                "move_pct": None if move is None else round(move, 2),
            })

    found.sort(key=lambda d: d["bars_ago"])
    seen: dict[str, int] = {}
    for d in found:
        base = f"DV{int(round(d['price_from']))}-{int(round(d['price_to']))}"
        seen[base] = seen.get(base, 0) + 1
        d["id"] = base if seen[base] == 1 else f"{base}{chr(96 + seen[base])}"

    # the honest part: how often did this actually work, here, recently
    track: dict = {}
    for t in ("bearish", "bullish"):
        graded = [d for d in found if d["type"] == t and d["outcome"]]
        ok = sum(1 for d in graded if d["outcome"] == "resolved")
        if not graded:
            continue
        rec = {"instances": len(graded), "resolved": ok, "failed": len(graded) - ok}
        if len(graded) >= 5:
            rec["resolve_rate"] = round(ok / len(graded) * 100)
        else:
            rec["resolve_rate"] = None
            rec["resolve_rate_withheld"] = (
                f"{len(graded)} graded instance"
                f"{'s' if len(graded) != 1 else ''} is too few for a percentage "
                f"— say 'resolved {ok} of {len(graded)}' instead")
        track[t] = rec
    return {"divergences": found, "track_record": track}


# ── tool implementations ──────────────────────────────────────────

def _rows(interval: str, limit: int, to: int | None = None) -> list[tuple]:
    d = get_bars(_sym(), interval, to, limit)
    return [(b["t"], b["o"], b["h"], b["l"], b["c"], b["v"]) for b in d["bars"]]


def tool_get_levels(interval: str = "1d", lookback_bars: int = 300,
                    draw: bool = False, draw_ids: list | None = None,
                    max_draw: int = 3, draw_mode: str = "add",
                    draw_as: str = "line", side: str = "both") -> dict:
    mode = str(draw_mode or "add").lower()
    # "clear the chart" needs a channel of its own. Without one the model has
    # to call this with draw=false, gets an empty patch, and cheerfully
    # narrates a wipe that never happened. No scan needed to erase.
    if mode == "clear":
        _scene_add({"kind": "clear_levels", "owner": "get_levels"})
        return {"cleared": True,
                "_note": "Every drawn level has been removed from the user's "
                         "chart. Confirm in one line; do not list levels."}
    lookback_bars = max(60, min(int(lookback_bars or 300), 1500))
    rows = _rows(interval, lookback_bars)
    if not rows:
        return {"error": f"no bars for interval {interval}"}
    wt = interval not in ("1d", "1w", "1mo")
    lv = _levels(rows, with_time=wt)

    # "where does it find SUPPORT" names a side. Ranking by evidence alone
    # answered it with four resistance zones, which is a true statement about
    # the window and a wrong answer to the question. Role is assigned relative
    # to the last close, so this filter is about where price is NOW.
    want_side = str(side or "both").lower()
    lv_all = lv
    lv = [x for x in lv if x["role"] == want_side] if want_side in (
        "support", "resistance") else lv

    # ── drawing: the model chose WHICH (by id) or asked for the top N;
    #    every coordinate below comes from the detector, never the model.
    picked: list[dict] = []
    missing: list[str] = []
    if draw_ids:
        wanted = {str(i).upper() for i in draw_ids}
        picked = [x for x in lv if x["id"].upper() in wanted]
        missing = sorted(wanted - {x["id"].upper() for x in picked})
    elif draw and lv:
        rank = lambda x: (-(x["evidence"]["held"] - x["evidence"]["broke"]),  # noqa: E731
                          -x["touches"], abs(x["distance_pct"]))
        picked = sorted(lv, key=rank)[:max(1, min(int(max_draw or 3), 8))]
    # "replace" lets the conversation REDUCE the scene ("just keep the strong
    # one", "drop the far level") — without it the chart could only ever grow.
    if picked and mode == "replace":
        _scene_add({"kind": "clear_levels", "owner": "get_levels"})
    for x in picked:
        ev = x["evidence"]
        graded = ev["held"] + ev["broke"]
        # The label is the at-a-glance signal, so spend it on the outcome
        # rather than the sample: "held 11/17" says everything "17 touches"
        # says, and the one thing it doesn't.
        tail = (f"held {ev['held']}/{graded}" if graded
                else f"{x['touches']} touch{'es' if x['touches'] != 1 else ''}")
        _scene_add({
            # a band when asked for one — same detection, honest width
            "kind": "zone" if str(draw_as).lower() == "zone" else "level",
            "id": x["id"], "price": x["price"],
            "lo": x["zone_lo"], "hi": x["zone_hi"], "pane": "price",
            "role": x["role"], "strength": x["strength"],
            "label": f"{'R' if x['role'] == 'resistance' else 'S'} "
                     f"{x['price']:,.2f} · {tail}",
            "source": {
                "tool": "get_levels",
                "method": "pivot-extremum (±5 bars), ATR-clustered",
                "interval": interval, "bars_scanned": len(rows),
                "touches": x["touches"], "strength": x["strength"],
                "first_touch": x["first_touch"], "last_touch": x["last_touch"],
                "evidence": ev, "horizon_bars": _EVIDENCE_HORIZON,
            },
        })
    # When something was drawn, lead with EXACTLY what landed on the chart.
    # Handing back the full candidate list first invited the model to narrate
    # a different trio than the one it drew (chart/text divergence).
    drawn_ids = [x["id"] for x in picked]
    result: dict = {}
    if missing:
        # never let a bad reference fail silently — the model would go on to
        # claim it drew something that isn't on the chart
        result["not_found"] = missing
        result["_not_found_note"] = (
            f"These ids do not exist at interval={interval}, "
            f"lookback_bars={lookback_bars}: {', '.join(missing)}. They were NOT "
            f"drawn. Ids are content-addressed (L<price>), so re-read the list "
            f"below rather than guessing."
        )
    if picked:
        result["drawn_levels"] = picked
        result["_drawn_note"] = (
            _drawn_ledger()
            + " Other candidates below were NOT drawn — mention them only as "
              "context, never as marked."
        )
        result["other_candidates"] = [x for x in lv if x["id"] not in set(drawn_ids)]
    else:
        result["levels"] = lv
    # An empty side is a fact about THIS scan window, not about the stock —
    # at the range low every pivot is overhead. Report the gap and the knobs;
    # the model decides whether widening is worth a second call.
    # Judged on the UNFILTERED scan: a `side` filter emptying the other side is
    # this call's own doing, and reporting that as a gap would be a lie.
    empty = [r for r in ("support", "resistance")
             if not any(x["role"] == r for x in lv_all)]
    if empty:
        result["_gap_note"] = (
            f"No {' or '.join(empty)} in these {len(rows)} {interval} bars. That "
            f"describes the window scanned, not the stock: a larger "
            f"lookback_bars or a higher interval often finds one — one wider "
            f"scan is worth it, repeated re-scans are not. Name the window you "
            f"scanned when you report the gap."
        )
    return {
        **result,
        "last_price": rows[-1][4],
        "provenance": {
            "method": "pivot-extremum (±5 bars), ATR-clustered, touch-counted",
            "evidence_method": (
                f"re-tests only (the level's own defining pivot is excluded), "
                f"each judged over {_EVIDENCE_HORIZON} bars starting after its "
                f"±5-bar pivot window — 'broke' if a close cleared the level by "
                f"more than the cluster tolerance, else 'held'"),
            "bars_scanned": len(rows), "interval": interval,
            "window": f"{_ist(rows[0][0], wt)} → {_ist(rows[-1][0], wt)} IST",
        },
        "_note": (
            "Lead with what happened, not how often it was tested: quote "
            "'held X of Y re-tests'. These measure different things — "
            "'strength' grades how well-tested a level is, held/broke grades "
            "whether it worked, and they often disagree: the most-touched "
            "level is frequently the least reliable. Say so when it happens. "
            "held+broke is one less than touches because the pivot that "
            "defined the level is not counted as a test of it. "
            "When hold_rate is null, hold_rate_withheld says why: obey it, and "
            "never compute the percentage yourself from held/broke — not even "
            "when one number is demanded. Give the fraction; never 'N/A', the "
            "record exists. A hold rate is a historical record, never "
            "the probability that the next test holds: don't restate it as a "
            "chance, a likelihood or odds. 'pending' touches are too recent "
            "count them as holds. react_pct is the median move away from the "
            f"level within {_EVIDENCE_HORIZON} bars, on the touches that held. "
            "Empty list means no level met the criteria: say so, don't invent "
            "one."),
    }


def tool_get_trendlines(interval: str = "1d", lookback_bars: int = 300,
                        draw: bool = False, draw_ids: list | None = None,
                        max_draw: int = 2, draw_mode: str = "add",
                        side: str = "both") -> dict:
    mode = str(draw_mode or "add").lower()
    if mode == "clear":
        _scene_add({"kind": "clear", "scope": "segment", "owner": "get_trendlines"})
        return {"cleared": True, "_note": "Trendlines removed from the chart."}
    lookback_bars = max(60, min(int(lookback_bars or 300), 1500))
    rows = _rows(interval, lookback_bars)
    if not rows:
        return {"error": f"no bars for interval {interval}"}
    wt = interval not in ("1d", "1w", "1mo")
    tl = _trendlines(rows, with_time=wt)
    if not tl:
        return {"trendlines": [],
                "_note": ("No line connected three or more swings in this "
                          "window. Say that plainly — do not describe a slope "
                          "you can see by eye as a trendline."),
                "provenance": {"method": "swing-anchored, 3+ touches required",
                               "bars_scanned": len(rows), "interval": interval}}

    # "along the highs" / "the downtrend" is not a vague request — it names a
    # side. Without this filter the auto-pick sorts on touches alone, and the
    # busier side wins regardless of what was asked; a request for the highs
    # came back as two support lines. The side has to be EXPRESSIBLE.
    want_side = str(side or "both").lower()
    if want_side in ("resistance", "support"):
        pool = [x for x in tl if x["role"] == want_side]
    else:
        pool = tl
    side_empty = want_side in ("resistance", "support") and not pool

    picked: list[dict] = []
    missing: list[str] = []
    if draw_ids:
        wanted = {str(i).upper() for i in draw_ids}
        picked = [x for x in tl if x["id"].upper() in wanted]
        missing = sorted(wanted - {x["id"].upper() for x in picked})
    elif draw and pool:
        picked = sorted(pool, key=lambda c: (-c["touches"], -c["span_bars"])
                        )[:max(1, min(int(max_draw or 2), 4))]
    if picked and mode == "replace":
        _scene_add({"kind": "clear", "scope": "segment", "owner": "get_trendlines"})
    for x in picked:
        _scene_add({
            "kind": "segment", "id": x["id"], "pane": "price", "role": x["role"],
            "p1": {"t": x["_t1"], "v": x["p1"]}, "p2": {"t": x["_t2"], "v": x["p2"]},
            "dashed": x["status"] == "broken",
            "label": f"{'R' if x['role'] == 'resistance' else 'S'} trendline · "
                     f"{x['touches']} touches · {x['status']}",
            "source": {"tool": "get_trendlines",
                       "method": "swing-anchored, 3+ touches, ATR tolerance",
                       "interval": interval, "bars_scanned": len(rows),
                       "touches": x["touches"], "strength": x["status"],
                       "first_touch": x["from"], "last_touch": x["to"]},
        })
    clean = [{k: v for k, v in x.items() if not k.startswith("_")} for x in pool]
    res: dict = _not_found_note(missing, "trendline", interval, lookback_bars,
                                [x["id"] for x in tl])
    if picked:
        ids = [x["id"] for x in picked]
        res["drawn_trendlines"] = [c for c in clean if c["id"] in ids]
        res["_drawn_note"] = _drawn_ledger()
        res["other_candidates"] = [c for c in clean if c["id"] not in ids]
    else:
        res["trendlines"] = clean
    if side_empty:
        other = sorted({x["role"] for x in tl})
        res["side_empty"] = (
            f"No {want_side} trendline connected three or more swings here. "
            f"Nothing was drawn. Say that plainly — the {' and '.join(other)} "
            f"line(s) this window does have answer a different question, so do "
            f"not offer one as if it were what was asked for.")
    note = ("Quote touches and status. 'broken' means price has "
            "already closed through it — say so rather than "
            "presenting it as live resistance. projects_to is where "
            "the line sits at the latest bar; it is an extrapolation "
            "of the fitted line, not a detected level.")
    if want_side == "both":
        note += (" These candidates include BOTH sides: a resistance line is "
                 "fitted through swing HIGHS, a support line through swing "
                 "LOWS. If the question named one side, re-call with `side` "
                 "rather than drawing whichever ranks highest.")
    return {**res,
            "last_price": rows[-1][4],
            "provenance": {"method": "swing-anchored, 3+ touches, ATR tolerance",
                           "bars_scanned": len(rows), "interval": interval,
                           "side": want_side},
            "_note": note}


def tool_get_divergences(indicator: str = "rsi", interval: str = "5m",
                         lookback_bars: int = 400, draw: bool = False,
                         draw_ids: list | None = None, max_draw: int = 1,
                         draw_mode: str = "add") -> dict:
    name = (indicator or "rsi").lower().strip()
    if name not in ("rsi", "macd"):
        return {"error": f"divergence supports rsi and macd, not '{name}'"}
    mode = str(draw_mode or "add").lower()
    if mode == "clear":
        _scene_add({"kind": "clear", "scope": "segment", "owner": "get_divergences"})
        return {"cleared": True, "_note": "Divergence markings removed."}
    rows = _rows(interval, max(120, min(int(lookback_bars or 400), 1500)))
    if not rows:
        return {"error": f"no bars for interval {interval}"}
    closes = [r[4] for r in rows]
    if name == "rsi":
        osc, period = _rsi(closes, 14), 14
    else:
        f, s = _ema(closes, 12), _ema(closes, 26)
        osc = [(a - b) if a is not None and b is not None else None
               for a, b in zip(f, s)]
        period = 12
    wt = interval not in ("1d", "1w", "1mo")
    d = _divergences(rows, osc, with_time=wt)
    found, track = d["divergences"], d["track_record"]
    if not found:
        return {"divergences": [], "_note": "No divergence in this window — say so.",
                "provenance": {"indicator": name, "interval": interval,
                               "bars_scanned": len(rows)}}

    picked: list[dict] = []
    missing: list[str] = []
    if draw_ids:
        wanted = {str(i).upper() for i in draw_ids}
        picked = [x for x in found if x["id"].upper() in wanted]
        missing = sorted(wanted - {x["id"].upper() for x in picked})
    elif draw:
        picked = found[:max(1, min(int(max_draw or 1), 3))]
    if picked and mode == "replace":
        _scene_add({"kind": "clear", "scope": "segment", "owner": "get_divergences"})
    for x in picked:
        rec = track.get(x["type"], {})
        note = (f"resolved {rec.get('resolved')}/{rec.get('instances')}"
                if rec.get("instances") else "no graded history")
        src = {"tool": "get_divergences",
               "method": f"price swing vs {name.upper()}({period}), judged "
                         f"20 bars after the pivot window",
               "interval": interval, "bars_scanned": len(rows),
               "touches": rec.get("instances", 0), "strength": x["type"],
               "first_touch": x["from"], "last_touch": x["to"],
               "record": note}
        # One divergence is TWO lines that only mean anything together: the
        # price leg and the oscillator leg. `link` keeps them one object.
        _scene_add({"kind": "segment", "id": x["id"], "pane": "price",
                    "role": x["role"], "link": x["id"], "dashed": True,
                    "p1": {"t": x["_t1"], "v": x["price_from"]},
                    "p2": {"t": x["_t2"], "v": x["price_to"]},
                    "label": f"{x['type']} divergence · {note}", "source": src})
        _scene_add({"kind": "segment", "id": x["id"] + "-osc", "pane": name,
                    "role": x["role"], "link": x["id"], "dashed": True,
                    "p1": {"t": x["_t1"], "v": x["osc_from"]},
                    "p2": {"t": x["_t2"], "v": x["osc_to"]},
                    "label": name.upper(), "source": src})
    clean = [{k: v for k, v in x.items() if not k.startswith("_")} for x in found]
    return {
        **_not_found_note(missing, "divergence", interval, lookback_bars,
                          [x["id"] for x in found]),
        "divergences": clean[:12],
        "track_record": track,
        "drawn": [x["id"] for x in picked] or None,
        "provenance": {"indicator": name, "period": period, "interval": interval,
                       "bars_scanned": len(rows),
                       "method": "consecutive same-side swings; outcome judged "
                                 "over 20 bars starting after the pivot window"},
        "_note": (
            "Divergences fire often and fail often — never present one as a "
            "signal on its own. Lead with track_record for that type in this "
            "window ('resolved X of Y'), and obey resolve_rate_withheld when "
            "resolve_rate is null. outcome is null for instances too recent to "
            "judge; never count those as resolved. Drawing one marks BOTH the "
            "price leg and the oscillator leg — the pair is the evidence."),
    }


_GAP_HORIZON = 60   # bars a gap is given to fill before it counts as unfilled


def _gaps(rows: list[tuple], with_time: bool = True) -> dict:
    """Unfilled-gap detection, with the fill record that makes it worth saying.

    A gap is the only piece of chart folklore with a genuinely quantified
    base rate, so detecting one without stating its fill history would be
    shipping the folklore and withholding the evidence.

    "Filled" means price later traded back to the FAR edge — the gap closed
    completely, not merely got touched. Recent gaps have had less time to
    fill, so a gap without a full horizon behind it is left PENDING rather
    than counted as unfilled; counting it would make every scan look like
    gaps don't fill, purely because the window ended.
    """
    n = len(rows)
    tol = _tolerance(rows)
    out: list[dict] = []
    for k in range(1, n):
        p_hi, p_lo = rows[k - 1][2], rows[k - 1][3]
        c_hi, c_lo = rows[k][2], rows[k][3]
        if c_lo > p_hi + tol:
            direction, lo, hi = "up", p_hi, c_lo
        elif c_hi < p_lo - tol:
            direction, lo, hi = "down", c_hi, p_lo
        else:
            continue
        # fill = a later bar reaching back to the far edge of the gap
        filled_at = None
        for j in range(k + 1, n):
            if (rows[j][3] <= lo + 1e-9) if direction == "up" else (rows[j][2] >= hi - 1e-9):
                filled_at = j
                break
        # The horizon decides whether a gap can be GRADED, not what happened
        # to it. A gap that filled on bar 62 filled; calling it "unfilled"
        # because a 60-bar rule expired would be a statement the data
        # contradicts. What the horizon protects against is the opposite
        # error: a gap from last week counted as unfilled when nothing has
        # had time to happen yet.
        status = ("filled" if filled_at is not None
                  else "open" if (k + _GAP_HORIZON) < n else "pending")
        out.append({
            "direction": direction,
            "lo": round(lo, 2), "hi": round(hi, 2),
            "size": round(hi - lo, 2),
            "size_pct": round((hi - lo) / rows[k][4] * 100, 2),
            "at": _ist(rows[k][0], with_time),
            "_t": rows[k][0], "_i": k,
            "status": status,
            "bars_to_fill": None if filled_at is None else filled_at - k,
            "open_now": filled_at is None,
        })

    graded = [g for g in out if g["status"] in ("filled", "open")]
    filled = sum(1 for g in graded if g["status"] == "filled")
    rec: dict = {"gaps": len(out), "graded": len(graded), "filled": filled,
                 "still_open": len(graded) - filled,
                 "pending": sum(1 for g in out if g["status"] == "pending")}
    if len(graded) >= 5:
        rec["fill_rate"] = round(filled / len(graded) * 100)
        bars = [g["bars_to_fill"] for g in graded if g["bars_to_fill"] is not None]
        if bars:
            rec["median_bars_to_fill"] = int(_median(bars))
    else:
        rec["fill_rate"] = None
        rec["fill_rate_withheld"] = (
            f"{len(graded)} graded gap{'s' if len(graded) != 1 else ''} is too few "
            f"for a percentage — say 'filled {filled} of {len(graded)}' instead")

    out.sort(key=lambda g: -g["_i"])
    seen: dict[str, int] = {}
    for g in out:
        base = f"G{int(round((g['lo'] + g['hi']) / 2))}"
        seen[base] = seen.get(base, 0) + 1
        g["id"] = base if seen[base] == 1 else f"{base}-{n - 1 - g['_i']}"
    return {"gaps": out, "record": rec}


_ANCHOR_KINDS = ("swing_high", "swing_low", "session_open", "session_close",
                 "window_high", "window_low", "gap", "high_52w", "low_52w")


def tool_get_anchors(interval: str = "5m", lookback_bars: int = 300,
                     kinds: list | None = None, limit: int = 12,
                     at_times: list | None = None, frm: str = "", to: str = "",
                     _raw: bool = False) -> dict:
    """Referenceable points, each with the bars around it.

    This exists so a shape can be composed without anyone typing a
    coordinate: the model picks anchors by id and code supplies the numbers.
    Each anchor ships its NEIGHBOURHOOD — the bars either side — because a
    point without its surroundings can be selected but not interpreted, and
    interpretation is the model's actual job.

    Two ways to ADDRESS a point directly (both still resolve to a real bar,
    so nothing here accepts a free coordinate):
      at_times — mint bar_high/bar_low anchors AT named dates/times. This is
        how a moment the conversation has already located (the day of the
        biggest fall, a specific high) becomes drawable even when no
        detector produced a pivot there.
      frm/to  — compute window_high/window_low inside that range instead of
        the whole window (corners for boxing a named period).
    Addressed anchors stay resolvable by id for the rest of the turn.
    """
    rows = _rows(interval, max(60, min(int(lookback_bars or 300), 1500)))
    if not rows:
        return {"error": f"no bars for interval {interval}"}
    wt = interval not in ("1d", "1w", "1mo")
    want = set(kinds or _ANCHOR_KINDS)
    n = len(rows)
    found: list[dict] = []
    not_placed: list[str] = []

    # ── addressed: anchors AT named moments ──
    for k, s in enumerate((at_times or [])[:4]):
        t = _parse_ist(str(s))
        if t is None:
            not_placed.append(f"'{s}' (unreadable — use the chart's format)")
            continue
        # the bar at or immediately before the named moment; a time before
        # the loaded window is refused, never silently snapped to bar 0
        i = next((j for j in range(n - 1, -1, -1) if rows[j][0] <= t), None)
        if i is None:
            not_placed.append(f"'{s}' (before the loaded window — raise "
                              "lookback_bars)")
            continue
        for suffix, col in (("H", 2), ("L", 3)):
            # the DATE is the id, not the position in the request — two
            # vlines minted as "T1H" in different turns silently replaced
            # each other on the scene, which keys shapes by id
            day = datetime.fromtimestamp(rows[i][0] + IST_OFF,
                                         tz=timezone.utc)
            aid = f"T{day.strftime('%d%m%y')}{suffix}"
            found.append({"kind": f"bar_{'high' if col == 2 else 'low'}",
                          "i": i, "value": rows[i][col], "_fixed_id": aid})

    # ── range scoping: extremes of a NAMED period, not the whole window ──
    r0, r1 = 0, n - 1
    t_frm = _parse_ist(frm) if frm else None
    t_to = _parse_ist(to) if to else None
    if (frm and t_frm is None) or (to and t_to is None):
        return {"error": "could not read frm/to",
                "hint": "chart format, e.g. '15 Jun 2026'"}
    if t_frm is not None:
        r0 = next((j for j in range(n) if rows[j][0] >= t_frm), n - 1)
    if t_to is not None:
        r1 = next((j for j in range(n - 1, -1, -1) if rows[j][0] < t_to + 86400), r0)
    ranged = t_frm is not None or t_to is not None

    if {"swing_high", "swing_low"} & want:
        for i, price, role in _pivots(rows, 5):
            kind = "swing_high" if role == "resistance" else "swing_low"
            if kind in want and r0 <= i <= r1:
                found.append({"kind": kind, "i": i, "value": price})

    if "window_high" in want:
        i = max(range(r0, r1 + 1), key=lambda k: rows[k][2])
        found.append({"kind": "window_high", "i": i, "value": rows[i][2],
                      **({"_fixed_id": f"R{datetime.fromtimestamp(rows[i][0] + IST_OFF, tz=timezone.utc).strftime('%d%m%y')}H"} if ranged else {})})
    if "window_low" in want:
        i = min(range(r0, r1 + 1), key=lambda k: rows[k][3])
        found.append({"kind": "window_low", "i": i, "value": rows[i][3],
                      **({"_fixed_id": f"R{datetime.fromtimestamp(rows[i][0] + IST_OFF, tz=timezone.utc).strftime('%d%m%y')}L"} if ranged else {})})

    # 52-week extremes are a stable fact of the DAILY series, not of whatever
    # window happens to be loaded — computing them here means "mark the 52w
    # high" never spends a round fetching candles to find a number code
    # already holds, and never depends on the on-screen interval or lookback.
    # computed when asked for by name, and always during draw_shape's _raw
    # re-resolution so the ids resolve at ANY interval — but kept out of the
    # generic listing, which they would only pad
    if ({"high_52w", "low_52w"} & want) and (kinds or _raw):
        daily = _rows("1d", 270)
        if daily:
            cut = daily[-1][0] - 364 * 86400
            win = [(j, r) for j, r in enumerate(daily) if r[0] >= cut]
            for kind, col, pick in (("high_52w", 2, max), ("low_52w", 3, min)):
                if kind in want and win:
                    j, r0 = pick(win, key=lambda x: x[1][col])
                    # nearest current-interval index, for recency sorting only;
                    # the anchor's own time/value stay exact
                    i_near = max(0, next((k for k in range(n)
                                          if rows[k][0] > r0[0]), n) - 1)
                    found.append({"kind": kind, "i": i_near, "value": r0[col],
                                  "_ts_abs": r0[0], "_daily": daily, "_j": j})

    if wt and {"session_open", "session_close"} & want:
        day = _ist_day(rows[-1][0])
        idx = [k for k in range(n) if _ist_day(rows[k][0]) == day]
        if idx and "session_open" in want:
            found.append({"kind": "session_open", "i": idx[0], "value": rows[idx[0]][1]})
        if idx and "session_close" in want:
            found.append({"kind": "session_close", "i": idx[-1], "value": rows[idx[-1]][4]})

    if "gap" in want:
        tol = _tolerance(rows)
        for k in range(1, n):
            up = rows[k][3] - rows[k - 1][2]
            dn = rows[k - 1][3] - rows[k][2]
            if up > tol:
                found.append({"kind": "gap", "i": k, "value": round((rows[k][3] + rows[k - 1][2]) / 2, 2),
                              "gap": "up", "from": rows[k - 1][2], "to": rows[k][3]})
            elif dn > tol:
                found.append({"kind": "gap", "i": k, "value": round((rows[k][2] + rows[k - 1][3]) / 2, 2),
                              "gap": "down", "from": rows[k - 1][3], "to": rows[k][2]})

    # nearest-to-now first: recency is what a chart conversation is usually about
    found.sort(key=lambda a: (-a["i"], a["kind"]))

    # Ids are assigned over the FULL candidate list, before `limit` is applied.
    # Assigning them after truncation makes an id depend on how many you asked
    # for, so the same name would point at a different point between the
    # get_anchors call and the draw_shape call. The disambiguator is bars_ago,
    # which is intrinsic to the point rather than to its position in a list.
    seen: dict[str, int] = {}
    for a in found:
        if a.get("_fixed_id"):
            a["_id"] = a["_fixed_id"]
            continue
        base = f"A{int(round(a['value']))}"
        seen[base] = seen.get(base, 0) + 1
        a["_id"] = base if seen[base] == 1 else f"{base}-{n - 1 - a['i']}"

    # Anchors the caller ADDRESSED (52w pair, at_times bars, range extremes)
    # ride exempt from the cap: sorted by recency they can land last, and
    # `limit` silently dropped them — draw_shape then reported an id that
    # get_anchors itself had just handed out as missing.
    cap = max(1, min(int(limit or 12), 30))
    addressed = [a for a in found if "_ts_abs" in a or "_fixed_id" in a]
    found = ([a for a in found
              if "_ts_abs" not in a and "_fixed_id" not in a][:cap] + addressed)

    # Addressed anchors also register for the TURN, so draw_shape can
    # resolve them without re-supplying the address.
    for a in addressed:
        if "_fixed_id" in a:
            i = a["i"]
            _mint_anchor(a["_id"], {
                "id": a["_id"], "kind": a["kind"], "_ts": rows[i][0],
                "t": _ist(rows[i][0], wt), "value": round(a["value"], 2)})

    out = []
    for a in found:
        i = a["i"]
        aid = a["_id"]
        if "_ts_abs" in a:
            # a 52w anchor's location is a daily-bar fact: report its own
            # exact date and daily neighbourhood, whatever interval is open
            dl, j = a["_daily"], a["_j"]
            lo, hi = max(0, j - 3), min(len(dl), j + 4)
            out.append({
                "id": aid, "kind": a["kind"], "t": _ist(a["_ts_abs"], False),
                **({"_ts": a["_ts_abs"]} if _raw else {}),
                "value": round(a["value"], 2),
                "window": "trailing 52 weeks of daily bars",
                "around": [[_ist(r[0], False), r[1], r[2], r[3], r[4], r[5]]
                           for r in dl[lo:hi]],
            })
            continue
        lo, hi = max(0, i - 3), min(n, i + 4)
        out.append({
            "id": aid, "kind": a["kind"], "t": _ist(rows[i][0], wt),
            # raw epoch for internal composition only; formatting the time and
            # parsing it back loses the format and is a needless round trip
            **({"_ts": rows[i][0]} if _raw else {}),
            "value": round(a["value"], 2),
            "bars_ago": n - 1 - i,
            **({"gap": a["gap"], "gap_from": a["from"], "gap_to": a["to"]}
               if a["kind"] == "gap" else {}),
            # The surroundings, so the point can be READ and not merely
            # cited. Tuples rather than objects: the key names repeated four
            # times per anchor cost more than the numbers do.
            "around": [[_ist(r[0], wt), r[1], r[2], r[3], r[4], r[5]]
                       for r in rows[lo:hi]],
        })
    return {
        "anchors": out,
        **({"not_placed": not_placed} if not_placed else {}),
        "around_fields": ["t", "open", "high", "low", "close", "volume"],
        "last_price": rows[-1][4],
        "provenance": {"interval": interval, "bars_scanned": n,
                       "window": f"{_ist(rows[0][0], wt)} → {_ist(rows[-1][0], wt)} IST",
                       "method": "swing pivots (±5 bars), window extremes, session "
                                 "open/close, ATR-sized gaps"},
        "_note": ("Compose shapes from these ids with draw_shape — never type a "
                  "price or a time yourself. 'around' is the neighbourhood of "
                  "each anchor: use it to judge whether the point means "
                  "anything before you draw it. An anchor is a location, not a "
                  "claim; nothing here says a level will hold."),
    }


_SHAPES = {"segment": 2, "ray": 2, "box": 2, "band": 2, "hline": 1,
           "vline": 1, "polyline": 3, "point": 1, "fib": 2}


def tool_draw_shape(shape: str, anchor_ids: list, interval: str = "5m",
                    lookback_bars: int = 300, pane: str = "price",
                    label: str = "", role: str = "neutral",
                    draw_mode: str = "add") -> dict:
    """Compose a shape from anchors resolved by id.

    The model chooses the shape and which anchors; every number still comes
    from the detector that produced the anchor. There is no field here that
    accepts a coordinate.
    """
    if str(draw_mode or "add").lower() == "clear":
        # shapes had no removal path at all — the chart-wide eraser was the
        # user's only recourse for a single unwanted hline
        _scene_add({"kind": "clear", "scope": "all", "owner": "draw_shape"})
        return {"cleared": True,
                "_note": "Every shape previously drawn via draw_shape is "
                         "removed. Other tools' drawings are untouched."}
    shape = (shape or "").lower().strip()
    if shape not in _SHAPES:
        return {"error": f"unknown shape '{shape}'", "available": sorted(_SHAPES)}
    ids = [str(i).upper() for i in (anchor_ids or [])]
    need = _SHAPES[shape]
    if len(ids) < need:
        return {"error": f"{shape} needs {need} anchor id(s), got {len(ids)}"}

    got = tool_get_anchors(interval, lookback_bars, limit=30, _raw=True)
    if "error" in got:
        return got
    by_id = {a["id"].upper(): a for a in got["anchors"]}
    # anchors minted by address earlier in the turn resolve by id alone
    for k, v in _minted_anchors().items():
        by_id.setdefault(k, v)
    missing = [i for i in ids if i not in by_id]
    if missing:
        return {"not_found": missing,
                "_note": (f"These anchor ids do not exist at interval={interval}, "
                          f"lookback_bars={lookback_bars}: {', '.join(missing)}. "
                          f"Nothing was drawn. Call get_anchors again and use ids "
                          f"from that result."),
                "available_ids": list(by_id)}

    picked = [by_id[i] for i in ids[:max(need, len(ids))]]
    pts = [{"t": a["_ts"], "v": a["value"]} for a in picked]

    src = {"tool": "draw_shape", "method": "composed from get_anchors ids",
           "interval": interval, "bars_scanned": got["provenance"]["bars_scanned"],
           "anchors": ids, "strength": "user-directed",
           "first_touch": picked[0]["t"], "last_touch": picked[-1]["t"]}
    # A 1-anchor shape given several anchors draws one PER anchor. It used
    # to draw only the first while the return listed them all as drawn — the
    # model then truthfully relayed a lie ("both marked") it had been told.
    if _SHAPES[shape] == 1 and len(picked) > 1:
        drawn = []
        for a in picked:
            auto = {"high_52w": "52W high", "low_52w": "52W low"}.get(
                a["kind"], a["kind"].replace("_", " "))
            one: dict = {"id": "S" + a["id"], "pane": pane, "role": role,
                         "label": label or auto, "source": {
                             **src, "anchors": [a["id"]],
                             "first_touch": a["t"], "last_touch": a["t"]}}
            if shape == "hline":
                one.update(kind="level", price=a["value"])
            elif shape == "vline":
                one.update(kind="vline", t=a["_ts"])
            else:
                one.update(kind="point", a={"t": a["_ts"], "v": a["value"]})
            _scene_add(one)
            drawn.append(one["id"])
        return {"drawn": drawn, "shape": shape, "from_anchors": ids,
                "points": [{"t": a["t"], "value": a["value"],
                            "kind": a["kind"]} for a in picked],
                "_note": (f"{len(drawn)} separate {shape}s drawn, one per "
                          "anchor. Describe each using its anchor kind.")}

    ann: dict = {"kind": "segment", "id": "S" + "-".join(ids), "pane": pane,
                 "role": role, "label": label or shape, "source": src}
    if shape in ("segment", "ray"):
        ann.update(p1={"t": pts[0]["t"], "v": pts[0]["v"]},
                   p2={"t": pts[1]["t"], "v": pts[1]["v"]},
                   extend="right" if shape == "ray" else "none")
    elif shape == "box":
        ann.update(kind="box", a={"t": pts[0]["t"], "v": pts[0]["v"]},
                   b={"t": pts[1]["t"], "v": pts[1]["v"]})
    elif shape == "band":
        ann.update(kind="zone", lo=min(pts[0]["v"], pts[1]["v"]),
                   hi=max(pts[0]["v"], pts[1]["v"]))
    elif shape == "hline":
        ann.update(kind="level", price=pts[0]["v"])
    elif shape == "vline":
        ann.update(kind="vline", t=pts[0]["t"])
    elif shape == "point":
        ann.update(kind="point", a={"t": pts[0]["t"], "v": pts[0]["v"]})
    elif shape == "polyline":
        ann.update(kind="poly", pts=[{"t": p["t"], "v": p["v"]} for p in pts])
    elif shape == "fib":
        # anchor order IS the convention: the first is the leg's start (100%),
        # the second its end (0%) — the same orientation as the FE's fib tool
        ann.update(kind="fib", p1={"t": pts[0]["t"], "v": pts[0]["v"]},
                   p2={"t": pts[1]["t"], "v": pts[1]["v"]},
                   label=label or "fib retracement")
    _scene_add(ann)
    out = {"drawn": ann["id"], "shape": shape, "from_anchors": ids,
           "points": [{"t": a["t"], "value": a["value"], "kind": a["kind"]} for a in picked],
           "_note": ("Drawn. Describe it using the anchor kinds above — this is "
                     "a shape the user asked for, not a detected structure, so "
                     "do not attach a hit rate or a hold record to it."),
           "ledger": _drawn_ledger()}
    if shape == "fib":
        out["levels"] = [{"ratio": r, "price": round(
            _fib_level(pts[0]["v"], pts[1]["v"], r), 2)} for r in _FIB_RATIOS]
        out["_note"] = (
            "The ladder is on the chart. Quote these prices — they are the same "
            "ratios the chart just drew, so do not recompute them. This only "
            "PLACES the fib; it says nothing about whether the ratios work here. "
            "Call evaluate_fib with the same two points for that, and do it "
            "whenever the user's question was about validity rather than "
            "placement.")
    return out


def tool_get_gaps(interval: str = "1d", lookback_bars: int = 400,
                  draw: bool = False, draw_ids: list | None = None,
                  max_draw: int = 3, draw_mode: str = "add",
                  only_open: bool = False) -> dict:
    mode = str(draw_mode or "add").lower()
    if mode == "clear":
        _scene_add({"kind": "clear", "scope": "zone", "owner": "get_gaps"})
        return {"cleared": True, "_note": "Gap zones removed from the chart."}
    rows = _rows(interval, max(120, min(int(lookback_bars or 400), 1500)))
    if not rows:
        return {"error": f"no bars for interval {interval}"}
    wt = interval not in ("1d", "1w", "1mo")
    d = _gaps(rows, with_time=wt)
    found = [g for g in d["gaps"] if g["open_now"]] if only_open else d["gaps"]
    if not found:
        return {"gaps": [], "record": d["record"],
                "_note": ("No gap met the size threshold in this window — say "
                          "so. Do not describe an ordinary wide bar as a gap."),
                "provenance": {"interval": interval, "bars_scanned": len(rows)}}

    picked: list[dict] = []
    missing: list[str] = []
    if draw_ids:
        wanted = {str(i).upper() for i in draw_ids}
        picked = [g for g in found if g["id"].upper() in wanted]
        missing = sorted(wanted - {g["id"].upper() for g in picked})
    elif draw:
        picked = found[:max(1, min(int(max_draw or 3), 6))]
    if picked and mode == "replace":
        _scene_add({"kind": "clear", "scope": "zone", "owner": "get_gaps"})
    for g in picked:
        # a gap IS a band — the same primitive a level-zone uses
        _scene_add({
            "kind": "zone", "id": g["id"], "pane": "price",
            "lo": g["lo"], "hi": g["hi"],
            "role": "support" if g["direction"] == "up" else "resistance",
            "strength": g["status"],
            "label": f"gap {g['direction']} · {g['status']}"
                     + (f" in {g['bars_to_fill']}b" if g["bars_to_fill"] is not None else ""),
            "source": {
                "tool": "get_gaps", "interval": interval,
                "method": f"adjacent-bar gap > ATR tolerance; filled = price "
                          f"returned to the far edge within {_GAP_HORIZON} bars",
                "bars_scanned": len(rows),
                "touches": d["record"]["graded"], "strength": g["status"],
                "first_touch": g["at"], "last_touch": g["at"],
                "record": (f"{d['record']['filled']} of {d['record']['graded']} "
                           f"gaps filled in this window"),
            },
        })
    clean = [{k: v for k, v in g.items() if not k.startswith("_")} for g in found[:14]]
    return {
        **_not_found_note(missing, "gap", interval, lookback_bars,
                          [g["id"] for g in found]),
        "gaps": clean,
        "record": d["record"],
        "drawn": [g["id"] for g in picked] or None,
        "provenance": {"interval": interval, "bars_scanned": len(rows),
                       "horizon_bars": _GAP_HORIZON,
                       "method": "adjacent-bar gap larger than the ATR tolerance"},
        "_note": (
            "Lead with the fill record for THIS symbol and window, not the "
            "textbook number — the whole point of a gap statistic is that it "
            "is measurable here. Obey fill_rate_withheld when fill_rate is "
            "null. 'pending' gaps are too recent to judge: never count them as "
            "unfilled. 'open_now' means price has not returned to the far edge "
            "yet; that is a fact about the past, not a prediction that it will."),
        "ledger": _drawn_ledger(),
    }


def _rate(key: str, good: int, bad: int, unit: str, floor: int = 5) -> dict:
    """A percentage, or an explicit refusal to give one.

    The refusal is a FIELD, never an absent key: a missing key reads as
    silence and silence gets filled in with a computed percentage.
    """
    graded = good + bad
    if graded >= floor:
        return {key: round(good / graded * 100)}
    plural = unit if graded == 1 else (
        unit + "es" if unit.endswith(("s", "x", "ch", "sh")) else unit + "s")
    # Zero is not a small sample, it is NO sample, and the two must not be
    # phrased alike. Telling the model to say "held 0 of 0" produced replies
    # reading as though the level had failed every test, when the truth is
    # that nothing has ever tested it.
    if graded == 0:
        return {key: None,
                key + "_withheld": (
                    f"No {plural} at all — this has never been tested, so it has "
                    f"no record either way. Say exactly that: 'never re-tested' "
                    f"or 'no record yet'. Do NOT say '0 of 0', which reads as a "
                    f"failure, and do not call it weak on this basis — untested "
                    f"is not the same as unreliable.")}
    return {key: None,
            key + "_withheld": (
                f"{graded} {plural} is too few for a percentage — say "
                f"'{good} of {graded}' instead, even if one number is demanded")}


def _score_line(rows: list[tuple], t1: int, v1: float, t2: int, v2: float,
                tol: float, window: int, wt: bool) -> dict:
    """Score one sloped line against real pivots.

    Extracted so a channel scores its two edges with the SAME code that scores
    a single trendline — two implementations would eventually disagree about
    what a touch is, and the whole point is that they cannot.
    """
    slope = (v2 - v1) / (t2 - t1)
    at = lambda ts_: v1 + slope * (ts_ - t1)  # noqa: E731
    n = len(rows)
    closes = [r[4] for r in rows]
    touches: list[dict] = []
    held = broke = pending = 0
    for i, price, role in _pivots(rows, window):
        line = at(rows[i][0])
        if abs(price - line) > tol:
            continue
        touches.append({"t": _ist(rows[i][0], wt), "side": role,
                        "price": round(price, 2), "line": round(line, 2)})
        start = i + window + 1
        if start + _EVIDENCE_HORIZON > n:
            pending += 1
            continue
        up = role == "resistance"
        crossed = any(
            (closes[j] > at(rows[j][0]) + tol) if up else (closes[j] < at(rows[j][0]) - tol)
            for j in range(start, start + _EVIDENCE_HORIZON))
        if crossed:
            broke += 1
        else:
            held += 1
    return {"touches": len(touches), "touch_list": touches, "held": held,
            "broke": broke, "pending": pending, "at": at, "now": at(rows[-1][0]),
            "method": (f"pivots within ±{round(tol, 2)} of the line; each touch "
                       f"judged over {_EVIDENCE_HORIZON} bars starting after its "
                       f"own ±{window}-bar pivot window")}


def tool_evaluate_line(p1_time: str = "", p1_value: float = 0.0,
                       p2_time: str = "", p2_value: float = 0.0,
                       interval: str = "5m", lookback_bars: int = 500,
                       drawing_id: str = "") -> dict:
    """Score a line the USER drew — the inverse of curate-by-reference.

    Prefer `drawing_id`: the geometry is then looked up from the chart, not
    retyped, so a mis-copied timestamp cannot score a different line and
    still return a confident number. Raw coordinates remain accepted for a
    line the user described but has not drawn. The same evidence rules as
    levels apply, including judging each touch only after its own pivot
    window so a local extremum cannot flatter the line.
    """
    if drawing_id:
        got = _drawing_for(drawing_id, "line")
        if "error" in got:
            return got
        pts = got["points"]
        p1_time, p1_value = pts[0]["t"], pts[0]["v"]
        p2_time, p2_value = pts[1]["t"], pts[1]["v"]
        if pts[1].get("_flat"):        # horizontal line: flat by construction
            t0 = _parse_ist(p1_time)
            p2_time = _ist((t0 or 0) + 86400, interval not in ("1d", "1w", "1mo"))
    elif not (p1_time and p2_time):
        return {"error": "give either drawing_id, or both p1_time and p2_time",
                "_note": ("Nothing was scored. If the user means a line they "
                          "drew, pass its ref from the chart context as "
                          "drawing_id rather than copying its coordinates.")}
    t1, t2 = _parse_ist(p1_time), _parse_ist(p2_time)
    if t1 is None or t2 is None:
        return {"error": "could not parse p1_time / p2_time (expect 'YYYY-MM-DD HH:MM' IST)"}
    if t1 == t2:
        return {"error": "the two points share a timestamp — that is a vertical line"}
    rows = _rows(interval, max(120, min(int(lookback_bars or 500), 1500)))
    if not rows:
        return {"error": f"no bars for interval {interval}"}
    wt = interval not in ("1d", "1w", "1mo")
    tol = _tolerance(rows)
    window = 5
    sc = _score_line(rows, t1, p1_value, t2, p2_value, tol, window, wt)
    last = rows[-1][4]
    res: dict = {
        "touches": sc["touches"],
        "held": sc["held"], "broke": sc["broke"],
        "projects_to": round(sc["now"], 2),
        "distance_pct": round((sc["now"] - last) / last * 100, 2),
        "side_now": "above price" if sc["now"] > last else "below price",
        "touch_list": sc["touch_list"][-8:],
        "provenance": {
            "interval": interval, "bars_scanned": len(rows),
            "tolerance": round(tol, 2), "method": sc["method"],
        },
    }
    res.update(_rate("hold_rate", sc["held"], sc["broke"], "graded touch"))
    if sc["pending"]:
        res["pending"] = sc["pending"]
    res["_note"] = (
        "This scores the user's OWN line, so say so: it is their geometry, "
        "measured. If touches is 0 or 1 the line has no record — tell them "
        "plainly that nothing has tested it rather than implying it is valid. "
        "Never invent a target from projects_to; it is where the drawn line "
        "reaches at the latest bar, an extrapolation of their drawing.")
    return res


# ── fibonacci retracement, measured ───────────────────────────────
# The most-drawn and least-validated tool in retail charting. Everyone will
# draw you a 0.618; nobody says whether it has ever mattered on this symbol.
#
# Ratio convention matches the FE's Geo.ladder exactly: r=0 sits at the END of
# the leg, r=1 at its START. The two layers must agree or the number describes
# a different line from the one on screen.
_FIB_RATIOS = (0.0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0)
# Midpoints between adjacent fib ratios: same range, same measurement, but not
# fibonacci. Without this control every rate looks like evidence — price
# reacts at SOME level in any retracement, and the whole claim is that it
# reacts at THESE ones more. Fixed, not sampled, so the answer is reproducible.
_FIB_CONTROL = (0.118, 0.309, 0.441, 0.559, 0.702, 0.893)


def _fib_level(p_start: float, p_end: float, r: float) -> float:
    return p_end + (p_start - p_end) * r


def _swing_legs(rows: list[tuple], window: int, min_move: float) -> list[tuple]:
    """Alternating pivot-to-pivot moves big enough to retrace.

    A leg is (i1, p1, i2, p2): a swing low followed by a swing high or the
    reverse. Same-side consecutive pivots are collapsed to the more extreme
    one, so a leg always spans a genuine move rather than a wobble.
    """
    piv = sorted(_pivots(rows, window))
    legs: list[tuple] = []
    prev: tuple | None = None
    for i, p, kind in piv:
        side = "high" if kind == "resistance" else "low"
        if prev is None:
            prev = (i, p, side)
            continue
        if side == prev[2]:
            # same side twice: keep the more extreme, it defines the swing
            better = p > prev[1] if side == "high" else p < prev[1]
            if better:
                prev = (i, p, side)
            continue
        if abs(p - prev[1]) >= min_move:
            legs.append((prev[0], prev[1], i, p))
        prev = (i, p, side)
    return legs


def _fib_record(rows: list[tuple], window: int, tol: float,
                ratios: tuple) -> dict:
    """How often each ratio produced a turn, over every past leg in the window.

    Two rules keep this from flattering itself:

    1. The denominator is legs where price actually REACHED the level, not all
       legs. A 0.786 that price never got near cannot be said to have failed;
       counting those would make deep ratios look weak for the wrong reason.
    2. r=0 and r=1 are the leg's own endpoints, and every endpoint is a pivot
       by construction. They are measured but never scored — the same rail
       that keeps a level from being graded against its defining pivot.

    A "reaction" is a pivot of the turning side forming within tolerance of
    the level: price came to it and went back. Watching stops when price
    exceeds the leg's end, because at that point the retracement is over and
    anything later belongs to a different structure.
    """
    highs = [r[2] for r in rows]
    lows = [r[3] for r in rows]
    n = len(rows)
    piv = _pivots(rows, window)
    pv_high = {i: p for i, p, k in piv if k == "resistance"}
    pv_low = {i: p for i, p, k in piv if k == "support"}
    legs = _swing_legs(rows, window, min_move=tol * 4)

    stat = {r: {"reached": 0, "reacted": 0} for r in ratios}
    used = 0
    for i1, p1, i2, p2 in legs:
        up = p2 > p1
        start = i2 + window + 1          # the leg's end pivot needs its own window
        horizon = min(n, start + max(_EVIDENCE_HORIZON, (i2 - i1) * 2))
        if start >= n:
            continue
        # the retracement ends when price makes a new extreme past the leg end
        stop = horizon
        for j in range(start, horizon):
            if (highs[j] > p2) if up else (lows[j] < p2):
                stop = j + 1
                break
        if stop - start < window * 2:
            continue                      # too short a window to form a turn
        used += 1
        for r in ratios:
            lvl = _fib_level(p1, p2, r)
            # reached: price traded to the level while retracing
            hit = any((lows[j] <= lvl) if up else (highs[j] >= lvl)
                      for j in range(start, stop))
            if not hit:
                continue
            stat[r]["reached"] += 1
            # reacted: it TURNED there — a pivot of the turning side, on-level
            turn = pv_low if up else pv_high
            if any(j in turn and abs(turn[j] - lvl) <= tol
                   for j in range(start, stop)):
                stat[r]["reacted"] += 1
    return {"stat": stat, "legs_used": used, "legs_found": len(legs)}


def tool_evaluate_fib(p1_time: str = "", p1_value: float = 0.0,
                      p2_time: str = "", p2_value: float = 0.0,
                      interval: str = "1d", lookback_bars: int = 600,
                      drawing_id: str = "") -> dict:
    """Score a fib retracement: this drawing, and the ratios' own track record.

    Prefer `drawing_id` — the leg is then read from the chart rather than
    retyped. Raw coordinates remain accepted for a leg the user named but
    has not drawn.
    """
    if drawing_id:
        got = _drawing_for(drawing_id, "fib")
        if "error" in got:
            return got
        pts = got["points"]
        p1_time, p1_value = pts[0]["t"], pts[0]["v"]
        p2_time, p2_value = pts[1]["t"], pts[1]["v"]
    elif not (p1_time and p2_time):
        return {"error": "give either drawing_id, or both p1_time and p2_time",
                "_note": ("Nothing was scored. If the user means a fib they "
                          "drew, pass its ref from the chart context as "
                          "drawing_id rather than copying its coordinates.")}
    t1, t2 = _parse_ist(p1_time), _parse_ist(p2_time)
    if t1 is None or t2 is None:
        return {"error": "could not read p1_time / p2_time",
                "hint": "use the chart's format, e.g. '08 Jul 2026 15:25'"}
    if t1 == t2:
        return {"error": "both points share a timestamp — a fib needs a leg"}
    if p1_value == p2_value:
        return {"error": "both points share a price — that leg has no height"}
    if t2 < t1:            # drawn right-to-left; the leg is still the same leg
        t1, t2 = t2, t1
        p1_value, p2_value = p2_value, p1_value

    rows = _rows(interval, max(120, min(int(lookback_bars or 600), 1500)))
    if not rows:
        return {"error": f"no bars for interval {interval}"}
    wt = interval not in ("1d", "1w", "1mo")
    tol, window, n = _tolerance(rows), 5, len(rows)
    highs = [r[2] for r in rows]
    lows = [r[3] for r in rows]
    last = rows[-1][4]
    up = p2_value > p1_value

    # ── 1. this drawing: where the levels are, and what price has done since
    after = [k for k in range(n) if rows[k][0] > t2]
    piv_at = {i: (p, k) for i, p, k in _pivots(rows, window)}
    # A leg drawn outside the scanned window has no "since" to report. Saying
    # reached=false for all seven would read as "price never got there".
    if not after:
        return {"error": "the leg ends at or after the last scanned bar",
                "hint": (f"nothing follows {_ist(t2, wt)} in these {n} "
                         f"{interval} bars, so there is no retracement to "
                         f"measure yet. Say that rather than reporting the "
                         f"levels as untouched."),
                "scanned": f"{_ist(rows[0][0], wt)} → {_ist(rows[-1][0], wt)} IST"}
    levels = []
    for r in _FIB_RATIOS:
        lvl = _fib_level(p1_value, p2_value, r)
        reached = any((lows[j] <= lvl) if up else (highs[j] >= lvl) for j in after)
        turned = any(
            j in piv_at and abs(piv_at[j][0] - lvl) <= tol
            and piv_at[j][1] == ("support" if up else "resistance")
            for j in after)
        levels.append({
            "ratio": r, "price": round(lvl, 2),
            "distance_pct": round((lvl - last) / last * 100, 2),
            "reached_since": reached,
            "turned_there_since": turned if reached else None,
            "defining_endpoint": r in (0.0, 1.0) or None,
        })

    # ── 2. the ratios' record on this symbol, with a non-fib control
    fib = _fib_record(rows, window, tol, _FIB_RATIOS)
    ctl = _fib_record(rows, window, tol, _FIB_CONTROL)

    def rate(s):
        return round(s["reacted"] / s["reached"] * 100) if s["reached"] >= 5 else None

    record = []
    for r in _FIB_RATIOS:
        if r in (0.0, 1.0):
            continue                      # the leg's own endpoints — never scored
        s = fib["stat"][r]
        record.append({"ratio": r, "reached": s["reached"], "turned": s["reacted"],
                       "turn_rate": rate(s)})
    c_reached = sum(ctl["stat"][r]["reached"] for r in _FIB_CONTROL)
    c_turned = sum(ctl["stat"][r]["reacted"] for r in _FIB_CONTROL)
    f_reached = sum(fib["stat"][r]["reached"] for r in _FIB_RATIOS if r not in (0.0, 1.0))
    f_turned = sum(fib["stat"][r]["reacted"] for r in _FIB_RATIOS if r not in (0.0, 1.0))
    ctl_rate = round(c_turned / c_reached * 100) if c_reached >= 5 else None
    fib_rate = round(f_turned / f_reached * 100) if f_reached >= 5 else None

    res: dict = {
        "leg": {"from": _ist(t1, wt), "from_price": round(p1_value, 2),
                "to": _ist(t2, wt), "to_price": round(p2_value, 2),
                "direction": "up" if up else "down",
                "height": round(abs(p2_value - p1_value), 2)},
        "levels": levels,
        "last_price": last,
        "ratio_record": record,
        "control": {"non_fib_ratios": list(_FIB_CONTROL),
                    "reached": c_reached, "turned": c_turned,
                    "turn_rate": ctl_rate},
        "all_fib_vs_control": {"fib_turn_rate": fib_rate,
                               "control_turn_rate": ctl_rate,
                               "legs_measured": fib["legs_used"]},
        "provenance": {
            "interval": interval, "bars_scanned": n, "tolerance": round(tol, 2),
            "legs_found": fib["legs_found"], "legs_measured": fib["legs_used"],
            "method": (
                f"every alternating swing leg taller than {round(tol * 4, 2)} in "
                f"this window; after each leg, a ratio counts as REACHED when "
                f"price traded to it and TURNED when a swing pivot formed within "
                f"{round(tol, 2)} of it before price exceeded the leg's end. "
                f"Rates are turned/reached — never turned/legs. The 0% and 100% "
                f"ratios are the leg's own endpoints and are excluded from the "
                f"record. The control ratios are the midpoints between adjacent "
                f"fib ratios: same range, same test, not fibonacci. "
                f"`levels` uses a looser rule than the record: it reports every "
                f"bar after {_ist(t2, wt)} with no cut-off, so 'reached_since' "
                f"means ever-since, not within a retracement window."),
        },
    }
    if fib_rate is not None and ctl_rate is not None:
        gap = fib_rate - ctl_rate
        res["verdict"] = (
            f"Fib ratios turned price {fib_rate}% of the times it reached them; "
            f"non-fib levels in the same zone turned it {ctl_rate}%. "
            + ("That gap is small enough to be noise — on this symbol and window "
               "the fibonacci ratios are not doing anything a nearby arbitrary "
               "level wouldn't. Say so."
               if abs(gap) <= 8 else
               f"Fib levels turned price {abs(gap)} points "
               f"{'more' if gap > 0 else 'LESS'} often than the control."))
    else:
        res["verdict"] = None
        res["verdict_withheld"] = (
            f"Too few graded retracements ({f_reached} fib, {c_reached} control) "
            f"to compare against the control. Report the counts and say the "
            f"record is too thin to judge — do not compute a rate yourself.")
    res["_note"] = (
        "Two different things are here, and conflating them is the easy mistake. "
        "`levels` is THIS drawing: where the user's own ratios sit now and "
        "whether price has been there since the leg. `ratio_record` is the base "
        "rate for each ratio across every past leg in this window — the answer "
        "to 'does the 0.618 actually work on this stock'. Lead with the control "
        "comparison whenever a verdict exists: a turn rate on its own always "
        "looks impressive, and the entire point of the control is that it "
        "usually isn't. turn_rate is null when fewer than 5 retracements reached "
        "that ratio — give 'turned X of Y' and never divide it out yourself. "
        "The two can disagree — a ratio can have turned price on THIS leg while "
        "its record across past legs is 0 of 36. That is not a contradiction "
        "and it is worth saying out loud: it happened here, and here is how "
        "often it happens. Reconcile them rather than quoting whichever one "
        "reads better. "
        "This is a historical record on one symbol, not a probability that the "
        "next retracement holds.")
    return res


# ── scoring the rest of the user's drawings ───────────────────────
# A drawing tool without evidence attached is the commodity part of a
# charting product. These close the loop for the shapes people actually use:
# a band, a channel, a planned trade.


def _zone_record(rows: list[tuple], lo: float, hi: float, tol: float,
                 window: int, wt: bool) -> dict:
    """What happened the times price came to a user-drawn BAND.

    Unlike a detected level there is no defining pivot to exclude — the user
    drew this, so nothing here was selected for looking good. That makes it
    cleaner evidence than a detector's own level, and worth saying.

    Each touch is still judged only after its own ±window, because a pivot is
    a local extremum by construction.
    """
    n = len(rows)
    highs = [r[2] for r in rows]
    lows = [r[3] for r in rows]
    closes = [r[4] for r in rows]
    touches: list[dict] = []
    held = broke = pending = 0
    for i, price, role in _pivots(rows, window):
        if not (lo - tol <= price <= hi + tol):
            continue
        touches.append({"t": _ist(rows[i][0], wt), "side": role,
                        "price": round(price, 2)})
        start = i + window + 1
        if start + _EVIDENCE_HORIZON > n:
            pending += 1
            continue
        # judged as the pivot it WAS: a swing high tested the band from below
        # and fails by closing above it; a swing low fails by closing under
        up = role == "resistance"
        gone = any((closes[j] > hi + tol) if up else (closes[j] < lo - tol)
                   for j in range(start, start + _EVIDENCE_HORIZON))
        broke, held = (broke + 1, held) if gone else (broke, held + 1)

    # A band price sits inside most of the time is not a zone, it is the
    # range. This is the honest rebuttal to a box drawn too wide, and no
    # touch count exposes it.
    inside = sum(1 for c in closes if lo <= c <= hi)
    overlap = sum(1 for k in range(n) if highs[k] >= lo and lows[k] <= hi)
    return {"touches": len(touches), "touch_list": touches[-8:],
            "held": held, "broke": broke, "pending": pending,
            "closes_inside_pct": round(inside / n * 100),
            "bars_overlapping_pct": round(overlap / n * 100)}


_ZONE_CONTROL_SLOTS = 12   # fixed, not sampled — the answer must be repeatable


def _zone_control(rows: list[tuple], lo: float, hi: float, tol: float,
                  window: int, wt: bool) -> dict:
    """The same band width, placed elsewhere, scored the same way.

    Without this a band's hold rate says more about its width than about the
    band: the wider it is, the further price must travel to close outside it.
    Placements are evenly spaced across the scanned range and fixed in number,
    so re-running returns the same answer.
    """
    width = hi - lo
    top = max(r[2] for r in rows)
    bot = min(r[3] for r in rows)
    if top - bot <= width:
        return {"placements_graded": 0,
                "median_hold_rate": None,
                "note": "the band is as tall as the whole scanned range — "
                        "there is nowhere else to put it, so it cannot be "
                        "compared. Report that instead of a comparison."}
    step = (top - bot - width) / (_ZONE_CONTROL_SLOTS - 1)
    rates: list[float] = []
    for s in range(_ZONE_CONTROL_SLOTS):
        c_lo = bot + step * s
        c_hi = c_lo + width
        # skip anything overlapping the user's own band — that is the thing
        # being tested, not a control for it
        if c_hi > lo and c_lo < hi:
            continue
        z = _zone_record(rows, c_lo, c_hi, tol, window, wt)
        if z["held"] + z["broke"] >= 5:
            rates.append(z["held"] / (z["held"] + z["broke"]) * 100)
    return {"placements_graded": len(rates),
            "median_hold_rate": round(_median(rates)) if len(rates) >= 3 else None,
            **({} if len(rates) >= 3 else {
                "note": (f"only {len(rates)} same-width placements had enough "
                         f"touches to grade — too few to compare against. Say "
                         f"the comparison is unavailable rather than implying "
                         f"the band's own rate stands on its own.")}),
            "method": (f"{_ZONE_CONTROL_SLOTS} evenly spaced placements of the "
                       f"same width across the scanned range, overlapping ones "
                       f"skipped, each scored by the identical rule")}


def _position_record(rows: list[tuple], entry: float, target: float,
                     stop: float, tol: float) -> dict:
    """From entries near this price, did target or stop come first?

    Overlapping setups are collapsed: while a trial is open, a new one cannot
    start. Without that, price resting at the entry for twenty bars would
    register twenty near-identical trials and inflate the sample enormously.
    """
    n = len(rows)
    highs = [r[2] for r in rows]
    lows = [r[3] for r in rows]
    long_ = target > entry
    horizon = _EVIDENCE_HORIZON * 3     # a trade needs room to resolve
    wins = losses = open_ = 0
    k = 0
    while k < n:
        if not (lows[k] - tol <= entry <= highs[k] + tol):
            k += 1
            continue
        end = min(n, k + 1 + horizon)
        outcome = None
        for j in range(k + 1, end):
            hit_t = highs[j] >= target if long_ else lows[j] <= target
            hit_s = lows[j] <= stop if long_ else highs[j] >= stop
            # both in one bar is unresolvable from OHLC alone — count it a
            # loss rather than guessing the intrabar path in our own favour
            if hit_t and hit_s:
                outcome = "loss"
            elif hit_t:
                outcome = "win"
            elif hit_s:
                outcome = "loss"
            if outcome:
                k = j
                break
        if outcome == "win":
            wins += 1
        elif outcome == "loss":
            losses += 1
        else:
            open_ += 1
            k = end
        k += 1
    return {"wins": wins, "losses": losses, "unresolved": open_,
            "horizon_bars": horizon}


def tool_evaluate_drawing(kind: str = "", points: list | None = None,
                          interval: str = "1d", lookback_bars: int = 600,
                          drawing_id: str = "") -> dict:
    """Score a zone, a channel or a planned position the USER drew.

    Prefer `drawing_id`: both the geometry AND the kind then come from the
    chart, so a rectangle cannot be scored as a channel and no coordinate is
    retyped. Raw points remain accepted for a shape the user described but
    has not drawn.
    """
    if drawing_id:
        got = _drawing_for(drawing_id, "drawing")
        if "error" in got:
            return got
        # the drawing knows what it is; an argument that disagrees is a
        # mis-read, and silently honouring it would score the wrong thing
        kind, points = got["sub"], got["points"]
    elif not points:
        return {"error": "give either drawing_id, or kind and points",
                "_note": ("Nothing was scored. If the user means a shape they "
                          "drew, pass its ref from the chart context as "
                          "drawing_id rather than copying its coordinates.")}
    kind = (kind or "").lower().strip()
    if kind not in ("zone", "channel", "position"):
        return {"error": f"unknown kind '{kind}'",
                "available": ["zone", "channel", "position"]}
    pts = []
    for p in points or []:
        t = _parse_ist(p.get("t")) if p.get("t") else None
        v = p.get("v", p.get("p"))
        if v is None:
            return {"error": "every point needs a value ('v')"}
        pts.append({"t": t, "v": float(v)})
    need = {"zone": 2, "channel": 3, "position": 3}[kind]
    if len(pts) < need:
        return {"error": f"{kind} needs {need} points, got {len(pts)}",
                "hint": {"zone": "the two edges of the band",
                         "channel": "two points on one edge, then a point on the other",
                         "position": "entry, then target, then stop"}[kind]}

    rows = _rows(interval, max(120, min(int(lookback_bars or 600), 1500)))
    if not rows:
        return {"error": f"no bars for interval {interval}"}
    wt = interval not in ("1d", "1w", "1mo")
    tol, window, n = _tolerance(rows), 5, len(rows)
    last = rows[-1][4]
    prov = {"interval": interval, "bars_scanned": n, "tolerance": round(tol, 2),
            "window": f"{_ist(rows[0][0], wt)} → {_ist(rows[-1][0], wt)} IST"}

    if kind == "zone":
        lo, hi = sorted((pts[0]["v"], pts[1]["v"]))
        if hi - lo < tol / 2:
            return {"error": "that band is thinner than one tolerance",
                    "hint": (f"tolerance here is {round(tol, 2)}; a band narrower "
                             f"than that is a line — use evaluate_line instead")}
        z = _zone_record(rows, lo, hi, tol, window, wt)
        res = {"zone": {"lo": round(lo, 2), "hi": round(hi, 2),
                        "width": round(hi - lo, 2),
                        "width_pct": round((hi - lo) / last * 100, 2),
                        "price_inside": lo <= last <= hi},
               **z}
        res.update(_rate("hold_rate", z["held"], z["broke"], "graded touch"))
        # A wide band is flattered by construction: price must travel further
        # to close outside it, so the rate climbs with the width rather than
        # with the band being real. Same rail as the fib control — score
        # same-width bands placed elsewhere and report the comparison.
        res["control"] = _zone_control(rows, lo, hi, tol, window, wt)
        cr = res["control"].get("median_hold_rate")
        if cr is not None and res.get("hold_rate") is not None:
            gap = res["hold_rate"] - cr
            res["verdict"] = (
                f"The band held {res['hold_rate']}% of its graded touches; bands "
                f"of the same width placed elsewhere in this range held {cr}% "
                + ("— so the number is a property of how WIDE the band is, not "
                   "evidence that this particular band matters. Say so."
                   if abs(gap) <= 8 else
                   f"— {abs(gap)} points {'better' if gap > 0 else 'WORSE'} "
                   f"than an arbitrary band of the same width."))
        prov["method"] = (
            f"swing pivots inside the band ±{round(tol, 2)}; each judged over "
            f"{_EVIDENCE_HORIZON} bars starting after its own ±{window}-bar "
            f"pivot window — 'broke' if a close left the band by more than the "
            f"tolerance on the side it was testing")
        res["provenance"] = prov
        res["_note"] = (
            "This is the user's own band, measured — say so. Lead with the "
            "verdict when there is one: a band's hold rate rises with its WIDTH, "
            "so the control comparison is the number that means something and "
            "the raw rate on its own will mislead. Then closes_inside_pct — a "
            "band price closes inside 40% of the time is not a zone, it is the "
            "range, and no touch count reveals that. Quote 'held X of Y'; obey "
            "hold_rate_withheld. If touches is 0 or 1 the band has no record — "
            "say that plainly rather than implying it is valid.")
        return res

    if kind == "channel":
        if any(p["t"] is None for p in pts[:3]):
            return {"error": "a channel needs a time on each of its three points"}
        a, b, c = pts[0], pts[1], pts[2]
        if a["t"] == b["t"]:
            return {"error": "the first two points share a timestamp"}
        # the second edge is the same slope through the third point, exactly
        # as the drawing tool builds it — parallel in DATA space
        off = c["v"] - (a["v"] + (b["v"] - a["v"]) / (b["t"] - a["t"]) * (c["t"] - a["t"]))
        e1 = _score_line(rows, a["t"], a["v"], b["t"], b["v"], tol, window, wt)
        e2 = _score_line(rows, a["t"], a["v"] + off, b["t"], b["v"] + off,
                         tol, window, wt)
        upper, lower = (e1, e2) if off < 0 else (e2, e1)
        # containment: how much of the time price actually stayed inside
        closes = [r[4] for r in rows]
        inside = 0
        for k in range(n):
            hi_, lo_ = upper["at"](rows[k][0]), lower["at"](rows[k][0])
            if lo_ - tol <= closes[k] <= hi_ + tol:
                inside += 1
        res = {
            "upper_edge": {"touches": upper["touches"], "held": upper["held"],
                           "broke": upper["broke"],
                           "projects_to": round(upper["now"], 2)},
            "lower_edge": {"touches": lower["touches"], "held": lower["held"],
                           "broke": lower["broke"],
                           "projects_to": round(lower["now"], 2)},
            "width_now": round(upper["now"] - lower["now"], 2),
            "closes_inside_pct": round(inside / n * 100),
            "price_position": ("above the channel" if last > upper["now"]
                               else "below the channel" if last < lower["now"]
                               else "inside the channel"),
        }
        res["upper_edge"].update(_rate("hold_rate", upper["held"], upper["broke"],
                                       "graded touch"))
        res["lower_edge"].update(_rate("hold_rate", lower["held"], lower["broke"],
                                       "graded touch"))
        prov["method"] = (upper["method"] + "; both edges share one slope in "
                          "data space, and containment counts closes between "
                          "them over the whole scan")
        res["provenance"] = prov
        res["_note"] = (
            "Score the two edges separately — a channel whose lower edge holds "
            "and whose upper edge does not is a real and useful finding, and "
            "averaging them hides it. closes_inside_pct is the honest headline: "
            "a channel drawn wide enough contains everything, so a high number "
            "is only meaningful alongside the edge touch counts. Never present "
            "projects_to as a target; it is an extrapolation of their drawing.")
        return res

    # position
    entry, target, stop = pts[0]["v"], pts[1]["v"], pts[2]["v"]
    long_ = target > entry
    if (stop >= entry) if long_ else (stop <= entry):
        return {"error": "the stop is on the same side as the target",
                "given": {"entry": entry, "target": target, "stop": stop},
                "hint": ("for a long the stop sits BELOW entry, for a short "
                         "above it — check the point order (entry, target, stop)")}
    reward, risk = abs(target - entry), abs(entry - stop)
    rr = reward / risk if risk else None
    p = _position_record(rows, entry, target, stop, tol)
    graded = p["wins"] + p["losses"]
    res = {
        "setup": {"side": "long" if long_ else "short", "entry": round(entry, 2),
                  "target": round(target, 2), "stop": round(stop, 2),
                  "reward": round(reward, 2), "risk": round(risk, 2),
                  "risk_reward": round(rr, 2) if rr else None},
        **{k: v for k, v in p.items() if k != "horizon_bars"},
    }
    res.update(_rate("hit_rate", p["wins"], p["losses"], "resolved trial"))
    # The control, in the same spirit as the fib one: a hit rate means nothing
    # until you know what this R:R needs to break even.
    if rr:
        be = round(1 / (1 + rr) * 100)
        res["breakeven_hit_rate"] = be
        if res.get("hit_rate") is not None:
            edge = res["hit_rate"] - be
            res["verdict"] = (
                f"At {round(rr, 2)}:1 this setup needs {be}% to break even before "
                f"costs; historically it resolved in favour {res['hit_rate']}% of "
                f"{graded} trials"
                + (". That is within noise of break-even — say so rather than "
                   "presenting it as an edge." if abs(edge) <= 8 else
                   f", {abs(edge)} points {'above' if edge > 0 else 'BELOW'} "
                   f"break-even."))
        else:
            res["verdict"] = None
            res["verdict_withheld"] = (
                f"{graded} resolved trials is too thin to compare against the "
                f"{be}% break-even rate. Give the counts and say the record is "
                f"too thin to judge.")
    prov["method"] = (
        f"every bar whose range contained the entry ±{round(tol, 2)} starts a "
        f"trial; the trial resolves on whichever of target or stop is touched "
        f"first within {p['horizon_bars']} bars. Overlapping trials are "
        f"collapsed so one long stay at the entry counts once. A bar touching "
        f"both is counted a LOSS, because OHLC cannot say which came first and "
        f"guessing in our own favour would inflate the rate.")
    res["provenance"] = prov
    res["_note"] = (
        "This measures the user's own levels against history; it is not a "
        "recommendation and must not be phrased as one — no 'take this trade', "
        "no sizing, no expectancy in rupees. Lead with the break-even "
        "comparison: a hit rate alone always reads as an edge, and against the "
        "R:R it usually is not. Obey hit_rate_withheld. 'unresolved' trials hit "
        "neither level inside the horizon — never fold them into wins. Close by "
        "saying this is historical analysis, not advice.")
    return res


def tool_plan_position(entry: float | None = None, stop: float | None = None,
                       stop_atr: float | None = None, targets: list | None = None,
                       targets_r: list | None = None, split: list | None = None,
                       qty: int | None = None, risk_amount: float | None = None,
                       capital: float | None = None, risk_pct: float | None = None,
                       side: str = "", drawing_id: str = "", interval: str = "1d",
                       draw_mode: str = "add") -> dict:
    """The user's trade plan as an overlay plus its risk arithmetic.

    Everything here is derived from the handful of numbers the user gave —
    entry, stop, targets, and one sizing input. Nothing is detected or
    recommended; the split between this and evaluate_drawing is deliberate:
    that tool scores history, this one prices a plan.
    """
    if str(draw_mode or "add").lower() == "clear":
        _scene_add({"kind": "clear", "scope": "position", "owner": "plan_position"})
        return {"cleared": True, "_note": "Plan overlay removed from the chart."}

    rows = _rows(interval, 400)
    if not rows:
        return {"error": f"no bars for interval {interval}"}
    last = rows[-1][4]
    a = _atr(rows, 14)
    atr14 = next((x for x in reversed(a) if x), None)

    if drawing_id:
        # a chat-drawn plan resolves with EVERYTHING it carries — all targets
        # and the sizing the user last set — so a dragged plan re-prices as
        # it now stands without re-typing anything
        c = (getattr(_drawings, "chat_by_id", None) or {}).get(
            str(drawing_id).upper().strip())
        if c and c.get("kind") == "position" and c.get("targets"):
            entry, stop = c["entry"], c["stop"]
            targets = list(c["targets"])
            side = side or c.get("side") or ""
            if qty is None and c.get("qty"):
                qty = c["qty"]
            if risk_amount is None and c.get("risk_amount"):
                risk_amount = c["risk_amount"]
        else:
            got = _drawing_for(drawing_id, "drawing")
            if "error" in got:
                return got
            if got["sub"] != "position":
                return {"error": f"drawing {drawing_id} is a {got['sub']}, not "
                                 "a position — draw one with the long/short "
                                 "tool or give entry/stop/targets directly"}
            pv = [p.get("v", p.get("p")) for p in got["points"][:3]]
            entry, targets, stop = pv[0], [pv[1]], pv[2]

    entry = float(entry) if entry is not None else last
    if stop is None and stop_atr is not None and atr14:
        s = (side or "").lower() or ("long" if (targets or [entry + 1])[0] > entry
                                     else "short")
        stop = entry - stop_atr * atr14 if s == "long" else entry + stop_atr * atr14
    if stop is None:
        return {"error": "a plan needs a stop — give stop, or stop_atr with side"}
    stop = float(stop)
    risk = abs(entry - stop)
    if risk <= 0:
        return {"error": "stop equals entry; there is no risk to size against"}
    long_ = stop < entry if not side else side.lower() == "long"
    if (stop >= entry) if long_ else (stop <= entry):
        return {"error": "the stop is on the same side as the target",
                "given": {"entry": entry, "stop": stop, "side": side}}
    if not targets and targets_r:
        targets = [entry + r * risk if long_ else entry - r * risk
                   for r in targets_r]
    if not targets:
        return {"error": "a plan needs at least one target (targets or targets_r)"}
    tps = sorted((float(t) for t in targets[:3]), reverse=not long_)
    for t in tps:
        if (t <= entry) if long_ else (t >= entry):
            return {"error": f"target {round(t, 2)} is on the loss side of "
                             f"entry {round(entry, 2)} for a "
                             f"{'long' if long_ else 'short'}"}

    # models send qty:0 / risk_amount:0 to mean "derive it" — honour that
    qty = int(qty) if qty else None
    risk_amount = float(risk_amount) if risk_amount else None
    if risk_amount is None and capital and risk_pct:
        risk_amount = capital * risk_pct / 100
    if qty is None and risk_amount:
        qty = int(risk_amount // risk)
        if qty < 1:
            return {"error": f"risking {round(risk_amount, 2)} cannot buy one "
                             f"share: a single share risks {round(risk, 2)} "
                             "between entry and stop"}
    if risk_amount is None and qty:
        risk_amount = qty * risk

    fr = None
    if split:
        fr = [max(0.0, float(f)) for f in split[:len(tps)]]
        tot = sum(fr) or 1
        fr = [f / tot for f in fr] + [0.0] * (len(tps) - len(fr))

    tgt = []
    for i, t in enumerate(tps):
        rr = abs(t - entry) / risk
        d = {"price": round(t, 2), "move_pct": round((t - entry) / entry * 100, 2),
             "rr": round(rr, 2), "breakeven_hit_pct": round(1 / (1 + rr) * 100)}
        if qty:
            d["pnl"] = round(abs(t - entry) * qty * (fr[i] if fr else 1))
        if fr:
            d["exit_fraction"] = round(fr[i], 2)
        tgt.append(d)

    plan = {"side": "long" if long_ else "short", "entry": round(entry, 2),
            "stop": round(stop, 2),
            "stop_pct": round((stop - entry) / entry * 100, 2),
            "risk_per_share": round(risk, 2), "targets": tgt}
    if atr14:
        plan["stop_distance_atr"] = round(risk / atr14, 2)
        plan["atr14"] = round(atr14, 2)
    if qty:
        plan["qty"] = qty
        plan["risk_amount"] = round(qty * risk)
        if capital:
            plan["capital_at_risk_pct"] = round(qty * risk / capital * 100, 2)
            plan["position_cost"] = round(qty * entry)
    if fr and qty:
        plan["blended"] = {
            "rr": round(sum(f * t["rr"] for f, t in zip(fr, tgt)), 2),
            "pnl_all_targets": round(sum(t.get("pnl", 0) for t in tgt))}

    p = _position_record(rows, entry, tps[0], stop, _tolerance(rows))
    hist = {k: v for k, v in p.items() if k != "horizon_bars"}
    hist.update(_rate("hit_rate", p["wins"], p["losses"], "resolved trial"))
    hist["_basis"] = (f"entry→first target vs stop on {len(rows)} {interval} "
                      f"bars; see evaluate_drawing for the full method")

    t0 = rows[max(0, len(rows) - 40)][0]
    _scene_add({"kind": "position", "id": "plan", "pane": "price",
                "side": plan["side"], "entry": plan["entry"],
                "stop": plan["stop"], "targets": [t["price"] for t in tgt],
                "pnl": [t.get("pnl") for t in tgt] if qty else None,
                "risk_amount": plan.get("risk_amount"),
                "qty": qty, "rr": tgt[0]["rr"], "t0": t0, "t1": rows[-1][0],
                "label": (f"{plan['side']} · R:R {tgt[0]['rr']}"
                          + (f" · qty {qty}" if qty else "")),
                "source": {"tool": "plan_position", "interval": interval}})

    return {"plan": plan, "history": hist, "_note": (
        "Drawn on the chart; a new plan_position call replaces it, "
        "draw_mode=clear removes it. Quote these figures exactly, and always "
        "put history.hit_rate NEXT TO target-1's breakeven_hit_pct — a hit "
        "rate without that benchmark reads as an edge it may not be (within "
        "~8 points is noise: say so). This prices the USER'S stated plan; it "
        "is analysis, not a recommendation, and must close as such.")}


def _classification_row(sym: str):
    try:
        return _con.execute(
            "SELECT name, industry FROM classification WHERE symbol=?",
            (sym,)).fetchone()
    except sqlite3.Error:
        return None


def tool_get_peers(symbol: str = "") -> dict:
    """The company's industry classification and its peer group."""
    sym = (symbol or _sym()).upper().strip()
    row = _classification_row(sym)
    if not row:
        return {"symbol": sym,
                "error": "no industry classification for this symbol",
                "_note": ("Say the classification is unavailable rather than "
                          "guessing peers from the name.")}
    name, ind = row
    have = {r[0] for r in _con.execute("SELECT DISTINCT symbol FROM bars")}
    peers = [{"symbol": p, "name": n, **({} if p in have else {"cold": True})}
             for p, n in _con.execute(
                 "SELECT symbol, name FROM classification "
                 "WHERE industry=? AND symbol!=? ORDER BY symbol", (ind, sym))]
    return {"symbol": sym, "name": name, "industry": ind, "peers": peers,
            "_note": (
                "Industry comes from the Moneycontrol classification; peers "
                "are limited to the 500-company chart universe. To compare, "
                "pick a handful (the user's ask decides which — do not dump "
                "the whole list) and call compare_symbols. A peer marked "
                "cold downloads its history on first use, ~6 s each.")}


def tool_compare_symbols(symbols: list | None = None, interval: str = "1d",
                         lookback_bars: int = 250) -> dict:
    """Cross-symbol comparison on locally stored bars, aligned to a
    common window so no symbol is scored over a span the others lack."""
    syms = []
    for s in (symbols or []):
        s = str(s).upper().strip()
        if s and s not in syms:
            syms.append(s)
    if not (2 <= len(syms) <= 8):
        return {"error": "give 2-8 symbols to compare"}
    for s in syms:
        err = _ensure_symbol(s)
        if err:
            return {"error": f"cannot compare: {err['error']}"}

    lb = max(60, min(int(lookback_bars or 250), 1500))
    series: dict[str, list[tuple]] = {}
    for s in syms:
        bars = get_bars(s, interval, None, lb)["bars"]
        if len(bars) < 20:
            return {"error": f"{s} has under 20 {interval} bars — too thin "
                             "to compare on this interval"}
        series[s] = [(b["t"], b["h"], b["l"], b["c"], b["v"]) for b in bars]

    start = max(v[0][0] for v in series.values())   # common window start
    out, rets = {}, {}
    for s, rows in series.items():
        rows = [r for r in rows if r[0] >= start]
        closes = [r[3] for r in rows]
        peak, dd = closes[0], 0.0
        for c in closes:
            peak = max(peak, c)
            dd = min(dd, c / peak - 1)
        tr = [max(h - l, abs(h - rows[i - 1][3]), abs(l - rows[i - 1][3]))
              for i, (_, h, l, _c, _v) in enumerate(rows) if i]
        atr = sum(tr[-14:]) / min(14, len(tr)) if tr else None
        out[s] = {
            "last": round(closes[-1], 2),
            "return_pct": round((closes[-1] / closes[0] - 1) * 100, 2),
            "max_drawdown_pct": round(dd * 100, 2),
            "atr_pct_of_price": round(atr / closes[-1] * 100, 2) if atr else None,
            "avg_daily_turnover_cr": round(
                sum(r[3] * r[4] for r in rows) / len(rows) / 1e7, 1),
            "bars": len(rows),
        }
        rets[s] = {r[0]: r[3] for r in rows}

    common = sorted(set.intersection(*(set(v) for v in rets.values())))
    corr = {}
    if len(common) >= 30:
        chg = {s: [rets[s][t2] / rets[s][t1] - 1
                   for t1, t2 in zip(common, common[1:])] for s in syms}

        def _r(a, b):
            n = len(a)
            ma, mb = sum(a) / n, sum(b) / n
            ca = sum((x - ma) * (y - mb) for x, y in zip(a, b))
            va = sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b)
            return round(ca / va ** 0.5, 2) if va else None

        corr = {f"{a}~{b}": _r(chg[a], chg[b])
                for i, a in enumerate(syms) for b in syms[i + 1:]}

    wt = interval not in ("1d", "1w", "1mo")
    res = {"window": f"{_ist(start, wt)} → {_ist(max(v[-1][0] for v in series.values()), wt)} IST",
           "interval": interval,
           "metrics": out,
           "ranked_by_return": sorted(syms, key=lambda s: -out[s]["return_pct"]),
           "return_correlation": corr or "under 30 common bars — not computed"}
    try:
        brows = _con.execute(
            "SELECT c FROM benchmark WHERE symbol='NIFTY 50' "
            "AND trade_date>=? ORDER BY trade_date",
            (_iso_day(start),)).fetchall()
        if len(brows) >= 2:
            res["nifty50_return_pct_same_window"] = round(
                (brows[-1][0] / brows[0][0] - 1) * 100, 2)
    except sqlite3.Error:
        pass
    res["_note"] = (
        "All symbols are measured over the SAME common window (a later "
        "listing shortens it for everyone — say so if 'bars' looks small). "
        "Present a markdown table; quote these numbers exactly. Turnover is "
        "rupees crore per bar. This is descriptive comparison, not a "
        "ranking of what to buy — close as analysis, not advice.")
    return res


# ── universe screening ────────────────────────────────────────────
# Daily bars for the whole universe live in bars_1d (built by
# import_universe_daily.py from the same _fold_daily the chart uses). The
# features below are plain arithmetic on those bars — the model composes any
# combination of them; this code only validates and computes.

SCREEN_FEATURES = (
    "close", "ret_1d", "ret_1w", "ret_1m", "ret_3m", "ret_6m", "ret_1y",
    "dist_52w_high", "dist_52w_low", "rsi14", "atr_pct",
    "sma20_rel", "sma50_rel", "sma200_rel",
    "range_20d_pct", "vol_z20", "turnover_20d_cr",
)

# Spelled out because an error that only lists names tells the model which
# words are legal, not which one it meant.
SCREEN_FEATURE_HELP = {
    "close": "last daily close, rupees",
    "ret_1d": "% change over the last session",
    "ret_1w": "% over 5 sessions",
    "ret_1m": "% over 21 sessions",
    "ret_3m": "% over 63 sessions",
    "ret_6m": "% over 126 sessions",
    "ret_1y": "% over 252 sessions",
    "dist_52w_high": "% from the 52-week high (0 = at it, negative = below)",
    "dist_52w_low": "% above the 52-week low",
    "rsi14": "RSI(14) on daily closes",
    "atr_pct": "ATR(14) as % of close — daily volatility",
    "sma20_rel": "% of close above (+) or below (-) the 20-day SMA",
    "sma50_rel": "% of close above (+) or below (-) the 50-day SMA",
    "sma200_rel": "% of close above (+) or below (-) the 200-day SMA",
    "range_20d_pct": "20-day high-to-low width as % of close — low = coiled",
    "vol_z20": "last session's volume in σ of the prior 20 sessions",
    "turnover_20d_cr": "avg daily close*volume over 20 sessions, rupees crore",
}

SCREEN_OPS = ("lt", "gt")
_SCREEN_SCAN_CAP = 80   # symbols a pattern pass will scan
_screen_cache: dict = {}


def _screen_stamp() -> tuple | None:
    # (count, newest, version): count+newest miss an in-place UPDATE that adds
    # no rows and no new day, so import_universe_daily bumps screen_meta on
    # every absorb — our own tooling can never leave a running server stale
    try:
        cnt, mx = _con.execute("SELECT COUNT(*), MAX(ts) FROM bars_1d").fetchone()
        ver = 0
        if _con.execute("SELECT 1 FROM sqlite_master WHERE name='screen_meta'"
                        ).fetchone():
            ver = _con.execute(
                "SELECT MAX(version) FROM screen_meta").fetchone()[0] or 0
        return (cnt, mx, ver)
    except sqlite3.Error:
        return None


def _rel(a: float, b) -> float | None:
    return None if not b else round((a - b) / b * 100, 2)


def _squash(s: str, sep: str = "") -> str:
    return sep.join("".join(ch if ch.isalnum() else " " for ch in s).lower().split())


def _screen_row_features(rows: list[tuple]) -> dict:
    """ascending daily (ts,o,h,l,c,v) for ONE symbol → its feature dict.

    Every feature whose window the symbol cannot cover is None. Falling back
    to a shorter window would rank a six-month listing against a ten-year one
    and call both a 1-year return; a null is the honest answer and the filter
    simply excludes it.
    """
    n = len(rows)
    closes = [r[4] for r in rows]
    c = closes[-1]
    f: dict = {k: None for k in SCREEN_FEATURES}
    f["close"] = round(c, 2)
    for key, back in (("ret_1d", 1), ("ret_1w", 5), ("ret_1m", 21),
                      ("ret_3m", 63), ("ret_6m", 126), ("ret_1y", 252)):
        if n > back:
            f[key] = _rel(c, closes[-1 - back])
    if n >= 252:
        w = rows[-252:]
        f["dist_52w_high"] = _rel(c, max(r[2] for r in w))
        f["dist_52w_low"] = _rel(c, min(r[3] for r in w))
    if n >= 16:
        r14 = indicators.compute("rsi", rows, 14)["last"]["rsi"]
        f["rsi14"] = None if r14 is None else round(r14, 1)
    a14 = indicators.atr(rows, 14)
    if a14 and a14[-1] is not None:
        f["atr_pct"] = round(a14[-1] / c * 100, 2) if c else None
    for key, p in (("sma20_rel", 20), ("sma50_rel", 50), ("sma200_rel", 200)):
        if n >= p:
            f[key] = _rel(c, indicators.sma(closes[-p:], p)[-1])
    if n >= 20:
        w = rows[-20:]
        f["range_20d_pct"] = round(
            (max(r[2] for r in w) - min(r[3] for r in w)) / c * 100, 2) if c else None
        f["turnover_20d_cr"] = round(
            sum(r[4] * r[5] for r in w) / len(w) / 1e7, 2)
    if n >= 21:
        vols = [r[5] for r in rows[-21:-1]]
        m = sum(vols) / len(vols)
        sd = (sum((x - m) ** 2 for x in vols) / len(vols)) ** 0.5
        f["vol_z20"] = round((rows[-1][5] - m) / sd, 2) if sd else None
    return f


def _screen_features() -> dict:
    """{symbol: {feature: value}} for every symbol in bars_1d.

    Cached on bars_1d's own (row count, newest ts) rather than on a clock:
    absorbing the universe artifact changes both, so the next call rebuilds
    and a night of no new data never pays for a rebuild.
    """
    stamp = _screen_stamp()
    if stamp is None or not stamp[0]:
        return {}
    if _screen_cache.get("stamp") == stamp:
        return _screen_cache["feats"]
    t0 = time.time()
    by_sym: dict[str, list] = {}
    for row in _con.execute(
            "SELECT symbol,ts,o,h,l,c,v FROM bars_1d ORDER BY symbol, ts"):
        by_sym.setdefault(row[0], []).append(row[1:])
    # deepest lookback is 252 sessions of ret_1y + warmup; the pattern path
    # reads [-300:] — retaining full history would hold ~370 MB at 500 symbols
    by_sym = {s: r[-560:] for s, r in by_sym.items()}
    feats = {s: _screen_row_features(r) for s, r in by_sym.items() if len(r) >= 2}
    last_day = {s: _ist_day(r[-1][0]) for s, r in by_sym.items() if r}
    days = list(last_day.values())
    mode_day = max(set(days), key=days.count) if days else None
    _screen_cache.update(stamp=stamp, feats=feats, bars=by_sym,
                         last_day=last_day, mode_day=mode_day,
                         built_s=round(time.time() - t0, 2))
    logging.info("charto screen matrix: %d symbols in %.2fs",
                 len(feats), _screen_cache["built_s"])
    return feats


def _screen_vocab(msg: str) -> dict:
    return {"error": msg,
            "features": SCREEN_FEATURE_HELP, "ops": list(SCREEN_OPS),
            "_note": ("Nothing was screened. Re-call using exactly these "
                      "feature names; a band is two filters on the same "
                      "feature (gt then lt).")}


def tool_screen_universe(filters: list | None = None, industry: str = "",
                         pattern: str = "", pattern_within: int = 5,
                         sort: str = "", limit: int = 15) -> dict:
    """Rank the whole stored universe on end-of-day features.

    Deliberately not a catalogue of named screens: the model composes the
    filters, so "coiled large-caps above their 200-day" is expressible without
    anyone having anticipated it. The engine's whole job is to refuse the
    unspeakable loudly and compute the rest exactly.
    """
    feats = _screen_features()
    if not feats:
        return {"error": "the daily universe table (bars_1d) is empty",
                "_note": ("Say universe screening is unavailable until the "
                          "daily universe is built — do not answer a "
                          "which-stocks question from the chart symbol alone.")}

    parsed: list[tuple] = []
    for spec in (filters or []):
        if not isinstance(spec, dict):
            return _screen_vocab("each filter must be an object "
                                 "{feature, op, value}")
        name = str(spec.get("feature") or "").strip()
        op = str(spec.get("op") or "").strip().lower()
        if name not in SCREEN_FEATURES:
            return _screen_vocab(f"unknown feature '{name}'")
        if op not in SCREEN_OPS:
            return _screen_vocab(f"unknown op '{op}' on {name}")
        try:
            val = float(spec.get("value"))
        except (TypeError, ValueError):
            return _screen_vocab(f"filter on {name} needs a numeric value")
        parsed.append((name, op, val))

    cls = {r[0]: (r[1], r[2]) for r in _con.execute(
        "SELECT symbol, name, industry FROM classification")}
    want_inds: set = set()
    if str(industry or "").strip():
        # Moneycontrol industry slugs carry no separators ("banksprivatesector"),
        # so both sides are squashed before matching — otherwise the natural
        # words the model actually types can never hit a single one.
        known = {i for _, i in cls.values() if i}
        q = _squash(industry)
        want_inds = {i for i in known if _squash(i) == q} or \
                    {i for i in known if q and (q in _squash(i) or _squash(i) in q)}
        if not want_inds:
            toks = [t for t in _squash(industry, " ").split() if len(t) >= 4]
            near = sorted(i for i in known
                          if any(t in _squash(i) or _squash(i).startswith(t[:5])
                                 for t in toks))
            # An empty "closest" is a dead end, so the whole vocabulary goes
            # back instead — an error the model cannot act on costs more than
            # the 192 names do.
            return {"error": f"no industry named '{industry}'",
                    ("closest" if near else "industries"): near[:15] or sorted(known),
                    "industries_total": len(known),
                    "_note": ("Nothing was screened. Industries are "
                              "Moneycontrol slugs; re-call with one of the "
                              "names above, drop `industry` to screen every "
                              "industry, or call get_peers to read a known "
                              "company's exact industry.")}

    sort_by = str(sort or "").strip()
    if sort_by and sort_by not in SCREEN_FEATURES:
        return _screen_vocab(f"cannot sort by '{sort_by}'")
    if not sort_by:
        sort_by = parsed[0][0] if parsed else "turnover_20d_cr"
    # Descending unless the screen itself asked for small values of this
    # feature — "RSI under 30" wants the most oversold first, not the least.
    desc = not any(nm == sort_by and op == "lt" for nm, op, _ in parsed)

    kind = str(pattern or "").lower().strip()
    if kind and kind not in patterns.CHART_KINDS + patterns.CANDLE_KINDS:
        return {"error": f"unknown pattern '{kind}'",
                "available": {"chart": list(patterns.CHART_KINDS),
                              "candlestick": list(patterns.CANDLE_KINDS)},
                "_note": ("Nothing was screened. Re-call with one exact name "
                          "from this list, or drop `pattern`.")}

    survivors = []
    for sym, f in feats.items():
        name, ind = cls.get(sym, (sym, None))
        if want_inds and ind not in want_inds:
            continue
        for fname, op, val in parsed:
            v = f.get(fname)
            if v is None or not (v > val if op == "gt" else v < val):
                break
        else:
            survivors.append({"symbol": sym, "name": name, "industry": ind,
                              "_f": f})
    survivors.sort(key=lambda r: (r["_f"].get(sort_by) is None,
                                  -(r["_f"].get(sort_by) or 0) if desc
                                  else (r["_f"].get(sort_by) or 0)))

    scanned = unscanned = 0
    within = max(1, min(int(pattern_within or 5), 120))
    if kind:
        pool, unscanned = survivors[:_SCREEN_SCAN_CAP], \
            max(0, len(survivors) - _SCREEN_SCAN_CAP)
        hit = []
        for r in pool:
            bars = ((_screen_cache.get("bars") or {}).get(r["symbol"]) or [])[-300:]
            if len(bars) < 60:
                continue
            scanned += 1
            ist = lambda ts: _ist(ts, False)  # noqa: E731 — daily bars
            found = patterns.candlesticks(
                bars, _atr(bars, 14), ist, {kind}, limit=8) \
                if kind in patterns.CANDLE_KINDS else patterns.chart_patterns(
                    bars, _pivots(bars, 5), _tolerance(bars), ist, {kind}, limit=8)
            # Both detectors return newest first. Without the recency filter a
            # "which stocks show an engulfing" screen matched every symbol on
            # a candle from three months ago — true, and not the question.
            found = [p for p in found if p["bars_ago"] <= within]
            if found:
                r["pattern"] = {k: v for k, v in found[0].items()
                                if not k.startswith("_")}
                hit.append(r)
        survivors = hit

    n_lim = max(1, min(int(limit or 15), 50))
    shown = survivors[:n_lim]
    # Only the features the screen actually referenced come back — a row
    # carrying all 17 is noise the model has to re-filter mentally.
    keep = ["close"] + [nm for nm, _, _ in parsed] + [sort_by]
    # as_of is the last session MOST symbols share — one symbol topped up
    # further (or holding a partial day) must not stamp the whole universe
    mode_day = _screen_cache.get("mode_day")
    last_day = _screen_cache.get("last_day") or {}
    as_of = _ist(mode_day * 86400 - IST_OFF, False) if mode_day else "unknown"
    stale_shown = 0
    rows = []
    for r in shown:
        out = {"symbol": r["symbol"], "name": r["name"],
               "industry": r["industry"]}
        d = last_day.get(r["symbol"])
        if d is not None and d != mode_day:
            out["as_of"] = _ist(d * 86400 - IST_OFF, False)
            stale_shown += 1
        for k in keep:
            if k not in out:
                out[k] = r["_f"].get(k)
        if "pattern" in r:
            out["pattern"] = r["pattern"]
        rows.append(out)
    universe = len(feats)
    res = {"universe": universe, "matched": len(survivors),
           "shown": len(rows), "as_of": as_of,
           "sorted_by": {"feature": sort_by,
                         "order": "desc" if desc else "asc"},
           "filters_applied": [{"feature": n, "op": o, "value": v}
                               for n, o, v in parsed],
           "rows": rows}
    if want_inds:
        res["industry_matched"] = sorted(want_inds)
    if kind:
        res["pattern"] = kind
        res["pattern_within_sessions"] = within
        res["symbols_scanned_for_pattern"] = scanned
        if unscanned:
            res["not_scanned_for_pattern"] = unscanned
    note = [
        f"Every value is an end-of-day figure as of {as_of}, computed across "
        f"the {universe} stocks whose daily bars are stored here — state both "
        f"the date and that universe whenever you quote a count or a rank.",
        "Symbols lacking the history a filter needs are excluded, never "
        "defaulted to zero.",
        "This is arithmetic on price and volume, not a view on any company: "
        "present the rows as a markdown table and close as analysis, not "
        "advice.",
    ]
    if not survivors:
        # A screen that finds nothing is an answer. Left unmarked it reads as
        # a failure and invites quietly loosening the filter it was asked for.
        note.insert(1, "Nothing passed. Say plainly that no stock in this "
                       "universe meets the criteria, name the filter that "
                       "bound, and offer a looser number — never relax it "
                       "yourself and present the result as if it were asked "
                       "for.")
    elif len(survivors) > len(rows):
        note.insert(1, f"{len(survivors)} names matched and {len(rows)} are "
                       f"shown — say so rather than implying the list is whole.")
    if kind:
        note.insert(1, f"A {kind} counts only if it completed within the last "
                       f"{within} sessions — say the window, and quote each "
                       f"hit's own bars_ago rather than implying it printed "
                       f"today.")
    if unscanned:
        note.insert(1, f"The pattern scan stopped at {_SCREEN_SCAN_CAP} names, "
                       f"so {unscanned} matching symbols were never checked "
                       f"for {kind} — say the scan was capped.")
    if stale_shown:
        note.insert(1, f"{stale_shown} shown row(s) carry their own as_of "
                       f"because their last stored session differs from the "
                       f"universe date — quote per-row dates for those.")
    # Measured against the classified company list rather than a fixed number,
    # so the warning retires itself the day the full artifact is absorbed and
    # never has to be edited to stop lying in either direction.
    if universe < 0.9 * max(1, len(cls)):
        note.insert(0, f"Only {universe} of the {len(cls)} companies in the "
                       f"list have daily bars so far, so this is a PARTIAL "
                       f"universe, not the market — say that before quoting "
                       f"any result.")
    res["_note"] = " ".join(note)
    return res


def tool_get_patterns(interval: str = "1d", lookback_bars: int = 300,
                      kinds: list | None = None, families: list | None = None,
                      limit: int = 20, draw: bool = False,
                      draw_ids: list | None = None, draw_mode: str = "add") -> dict:
    """Named formations: candlesticks, chart patterns, market structure.

    Two questions share one tool because they are the same scan: "what's on
    this chart" is `kinds` omitted, "is there a head and shoulders" is `kinds`
    set. The second case is why an empty result has to be LOUD — a specific
    pattern that was looked for and not found is an answer, and returning a
    bare empty list invites hedging instead of a plain no.
    """
    mode = str(draw_mode or "add").lower()
    if mode == "clear":
        _scene_add({"kind": "clear", "scope": "all", "owner": "get_patterns"})
        return {"cleared": True, "_note": "Pattern marks removed from the chart."}
    rows = _rows(interval, max(60, min(int(lookback_bars or 300), 1500)))
    if not rows:
        return {"error": f"no bars for interval {interval}"}
    wt = interval not in ("1d", "1w", "1mo")
    ist = lambda ts: _ist(ts, wt)  # noqa: E731

    asked = {str(k).lower().strip() for k in (kinds or [])}
    unknown = sorted(asked - set(patterns.ALL_KINDS))
    if unknown:
        return {"error": f"unknown pattern(s): {', '.join(unknown)}",
                "available": {"candlestick": list(patterns.CANDLE_KINDS),
                              "chart": list(patterns.CHART_KINDS),
                              "structure": list(patterns.STRUCTURE_KINDS)},
                "_note": "Nothing was scanned. Re-call with names from this list."}
    fams = {str(f).lower() for f in (families or [])}
    want_c = (not asked or asked & set(patterns.CANDLE_KINDS)) and \
             (not fams or "candlestick" in fams)
    want_p = (not asked or asked & set(patterns.CHART_KINDS)) and \
             (not fams or "chart" in fams)
    want_s = (not asked or asked & set(patterns.STRUCTURE_KINDS)) and \
             (not fams or "structure" in fams)

    tol = _tolerance(rows)
    piv = _pivots(rows, 5)
    atr_series = _atr(rows, 14)

    cands = patterns.candlesticks(
        rows, atr_series, ist, asked & set(patterns.CANDLE_KINDS) or None,
        limit=max(10, limit)) if want_c else []
    charts = patterns.chart_patterns(
        rows, piv, tol, ist, asked & set(patterns.CHART_KINDS) or None,
        limit=max(6, limit)) if want_p else []
    struct = patterns.market_structure(rows, piv, ist) if want_s else None

    # ── drawing: chart patterns have geometry worth marking
    picked: list[dict] = []
    missing: list[str] = []
    by_id = {p["id"].upper(): p for p in charts}
    if draw_ids:
        wanted = {str(i).upper() for i in draw_ids}
        picked = [by_id[i] for i in wanted if i in by_id]
        missing = sorted(wanted - set(by_id))
    elif draw:
        picked = charts[:3]
    if picked and mode == "replace":
        _scene_add({"kind": "clear", "scope": "all", "owner": "get_patterns"})
    for p in picked:
        # Draw the pattern's ACTUAL geometry — the polyline through its
        # defining swings, its neckline as a bounded segment ending at the
        # break bar, fitted edges anchored at their exact endpoint bars —
        # never a full-width level or zone standing in for a shape. Every
        # anchor is an exact (bar-epoch, pivot-price) pair from the detector.
        g = p.get("_geometry") or {}
        name = p["pattern"].replace("_", " ")
        status = p.get("status", "")
        role = {"bullish": "support", "bearish": "resistance"}.get(
            p["direction"], "neutral")
        link = p["id"]
        src = {"tool": "get_patterns",
               "method": "swing-sequence template on shared ±5-bar pivots"
                         if g.get("outline") else "fitted swing boundaries",
               "interval": interval, "bars_scanned": len(rows),
               "strength": status or "unconfirmed",
               "first_touch": p["from"], "last_touch": p["to"]}
        pt = lambda t, v: {"t": t, "v": v}  # noqa: E731
        if g.get("outline"):
            o = [pt(t, v) for t, v in g["outline"]]
            base = g.get("base")
            # solid stroke along the swing path only; the fill closes down
            # to the neckline as a separate stroke-less polygon so the base
            # edge never double-draws over the dashed neckline
            _scene_add({"kind": "poly", "id": link + "-o", "link": link,
                        "pane": "price", "role": role, "pts": o,
                        "solid": True, "source": src})
            if "head_and_shoulders" in p["pattern"] and len(o) == 5:
                for idx, tag in ((0, "left shoulder"), (2, "head"),
                                 (4, "right shoulder")):
                    _scene_add({"kind": "point", "id": f"{link}-t{idx}",
                                "link": link, "pane": "price", "role": role,
                                "a": o[idx], "label": tag, "source": src})
            if base:
                _scene_add({"kind": "poly", "id": link + "-f", "link": link,
                            "pane": "price", "role": role,
                            "pts": [pt(o[0]["t"], base["v"])] + o
                                   + [pt(o[-1]["t"], base["v"])],
                            "closed": True, "fill": True, "stroke": False,
                            "source": src})
                _scene_add({"kind": "segment", "id": link + "-n", "link": link,
                            "pane": "price", "role": role, "dashed": True,
                            "p1": pt(base["t1"], base["v"]),
                            "p2": pt(base["t2"], base["v"]),
                            "label": f"{name} · neckline {base['v']:,.2f}"
                                     + (f" · {status}" if status else ""),
                            "source": src})
        # the remaining pieces COMPOSE — a pennant is pole + edges, a cup is
        # arc outline + rim — so these are independent ifs, not a chain
        if g.get("edges"):
            e = g["edges"]
            lab = {"not_assessed": "unresolved"}.get(status, status)
            _scene_add({"kind": "segment", "id": link + "-u", "link": link,
                        "pane": "price", "role": role,
                        "p1": pt(*e["upper"][0]), "p2": pt(*e["upper"][1]),
                        **({} if g.get("pole") else {"label":
                            f"{name} · width {p.get('width_now', 0):,.2f}"
                            + (f" · {lab}" if lab else "")}),
                        "source": src})
            _scene_add({"kind": "segment", "id": link + "-l", "link": link,
                        "pane": "price", "role": role,
                        "p1": pt(*e["lower"][0]), "p2": pt(*e["lower"][1]),
                        "source": src})
            _scene_add({"kind": "poly", "id": link + "-f", "link": link,
                        "pane": "price", "role": role,
                        "pts": [pt(*e["upper"][0]), pt(*e["upper"][1]),
                                pt(*e["lower"][1]), pt(*e["lower"][0])],
                        "closed": True, "fill": True, "stroke": False,
                        "source": src})
        if g.get("pole"):
            _scene_add({"kind": "segment", "id": link + "-p", "link": link,
                        "pane": "price", "role": role,
                        "p1": pt(*g["pole"][0]), "p2": pt(*g["pole"][1]),
                        "label": f"{name} · pole {p.get('pole', 0):,.2f}"
                                 + (f" · {status}" if status else ""),
                        "source": src})
        if g.get("box"):
            _scene_add({"kind": "box", "id": link + "-b", "link": link,
                        "pane": "price", "role": role,
                        "a": pt(*g["box"][0]), "b": pt(*g["box"][1]),
                        "source": src})

    for c in charts:                       # geometry is for the chart, not
        c.pop("_geometry", None)           # the model — keep the payload lean
    res: dict = _not_found_note(missing, "pattern", interval, lookback_bars,
                                list(by_id))
    if want_c:
        res["candlesticks"] = cands
    if want_p:
        res["chart_patterns"] = charts
    if want_s and struct:
        res["market_structure"] = struct
    if picked:
        res["drawn"] = [p["id"] for p in picked]
        res["_drawn_note"] = _drawn_ledger()

    # A specific ask that found nothing must come back as a plain NO.
    if asked:
        found = {c["pattern"] for c in cands} | {c["pattern"] for c in charts}
        absent = sorted(asked - found - set(patterns.STRUCTURE_KINDS))
        if absent:
            res["not_present"] = absent
            res["_not_present_note"] = (
                f"Scanned {len(rows)} {interval} bars "
                f"({ist(rows[0][0])} → {ist(rows[-1][0])}) and found no "
                f"{', '.join(x.replace('_', ' ') for x in absent)}. Say that "
                f"plainly — 'there isn't one here' is the answer, not a reason "
                f"to hedge or to offer a loosely similar shape as if it "
                f"qualified. Name the window you scanned.")

    res["provenance"] = {
        "interval": interval, "bars_scanned": len(rows),
        "window": f"{ist(rows[0][0])} → {ist(rows[-1][0])} IST",
        "tolerance": round(tol, 2),
        "candlestick_thresholds": {
            "doji_body_max_pct_of_range": patterns._DOJI_BODY * 100,
            "long_body_x_rolling_avg": patterns._LONG_BODY,
            "small_body_x_rolling_avg": patterns._SMALL_BODY,
            "hammer_wick_over_body": patterns._WICK_RATIO,
            "rolling_avg_bars": 14,
            "trend_context_bars": patterns._TREND_BARS,
        },
        "method": ("candlesticks from bar anatomy with the disclosed thresholds "
                   "above; chart patterns from the same ±5-bar swing pivots every "
                   "other detector uses, with an ATR-derived tolerance; structure "
                   "labels from those swings directly"),
    }
    res["_note"] = (
        "These are measurements, not signals. A detected pattern is a shape "
        "that IS on the chart — say it is there and where — but nothing here "
        "says it will work, and the reply must not imply a direction is likely. "
        "'direction' is the textbook bias of the shape, not a forecast. "
        "'measured' carries the numbers that qualified each candle: quote them "
        "when the user asks why something counted. For chart patterns, "
        "'status' matters more than the name — an unconfirmed head and "
        "shoulders is a shape whose neckline has not broken, so do not "
        "describe it as playing out. 'measured_move' is the textbook "
        "projection of the pattern's own height; it is geometry, never a "
        "target or a prediction, and must be labelled as such. Candlestick "
        "patterns in particular fire often: prefer the recent ones, say how "
        "many you found, and do not list twenty.")
    return res


# These shapes are fitted at the LIVE EDGE of the series only, so there is
# no honest way to mine historical instances of them yet — the detector
# would have to be re-run at every past bar, which is a different (and
# heavier) machine. Saying so beats a fabricated history.
_EDGE_ONLY = {"ascending_triangle", "descending_triangle",
              "symmetrical_triangle", "rising_wedge", "falling_wedge",
              "rectangle", "channel_up", "channel_down", "broadening",
              "cup_and_handle", "rounding_bottom", "rounding_top"}


def tool_evaluate_pattern(kind: str = "", interval: str = "1d",
                          lookback_bars: int = 1000,
                          horizon_bars: int = 10) -> dict:
    """Has this pattern type actually been reliable ON THIS CHART?

    Every past instance of `kind`, the forward move `horizon_bars` later,
    the rate of moving in the pattern's textbook direction — and the
    unconditional base rate as CONTROL, because a rate without a control
    is decoration. Nothing here gates detection; it is evidence the model
    quotes when the user asks whether the shape has meant anything.
    """
    k = str(kind or "").lower().strip()
    if k not in patterns.ALL_KINDS or k in patterns.STRUCTURE_KINDS:
        return {"error": f"unknown pattern: {k or '(empty)'}",
                "available": {"candlestick": list(patterns.CANDLE_KINDS),
                              "chart": list(patterns.CHART_KINDS)},
                "_note": "Nothing was evaluated. Re-call with one exact id."}
    if k in _EDGE_ONLY:
        return {"unsupported": k, "_note": (
            f"{k.replace('_', ' ')} is fitted at the live edge of the chart "
            "only — there is no historical instance record to score, so no "
            "reliability rate exists. Say that plainly. Candlestick kinds "
            "and swing shapes (double/triple top/bottom, head and "
            "shoulders, flags, pennants) can be evaluated.")}
    h = max(2, min(int(horizon_bars or 10), 60))
    rows = _rows(interval, max(300, min(int(lookback_bars or 1000), 2000)))
    if not rows:
        return {"error": f"no bars for interval {interval}"}
    n = len(rows)
    closes = [r[4] for r in rows]
    wt = interval not in ("1d", "1w", "1mo")
    ist = lambda ts: _ist(ts, wt)  # noqa: E731

    skipped_unconfirmed = 0
    if k in patterns.CANDLE_KINDS:
        found = patterns.candlesticks(rows, _atr(rows, 14), ist, {k}, limit=500)
        inst = [{"i": n - 1 - f["bars_ago"], "d": f["direction"]} for f in found]
    else:
        found = patterns.chart_patterns(rows, _pivots(rows, 5), _tolerance(rows),
                                        ist, {k}, limit=60)
        inst = []
        for f in found:
            # the measurable event is the CONFIRMING break — an unconfirmed
            # shape has no completion bar to measure forward from
            if f.get("status") != "confirmed":
                skipped_unconfirmed += 1
                continue
            end_i = n - 1 - f["bars_ago"]
            inst.append({"i": min(n - 1, end_i + int(f.get("bars_to_break", 0))),
                         "d": f["direction"]})
    raw = len(inst)

    # instances closer together than the horizon share their forward window —
    # that is one piece of evidence, not several. Keep the first of a cluster.
    inst.sort(key=lambda x: x["i"])
    kept, last = [], -10**9
    for x in inst:
        if x["i"] - last >= h:
            kept.append(x)
            last = x["i"]

    evals, too_recent = [], 0
    for x in kept:
        if x["i"] + h >= n:
            too_recent += 1
            continue
        evals.append({"i": x["i"], "d": x["d"],
                      "fwd": (closes[x["i"] + h] - closes[x["i"]])
                      / closes[x["i"]] * 100})

    res: dict = {"pattern": k, "interval": interval, "horizon_bars": h,
                 "instances_found": raw, "evaluated": len(evals),
                 "declustered_out": raw - len(kept), "too_recent": too_recent}
    if skipped_unconfirmed:
        res["skipped_unconfirmed"] = skipped_unconfirmed
    base = [(closes[j + h] - closes[j]) / closes[j] * 100 for j in range(n - h)]
    kdir = evals[0]["d"] if evals else (found[0]["direction"] if found else "neutral")
    if evals:
        moves = sorted(e["fwd"] for e in evals)
        res["avg_move_pct"] = round(sum(moves) / len(moves), 2)
        res["median_move_pct"] = round(moves[len(moves) // 2], 2)
        res["recent"] = [{"t": ist(rows[e["i"]][0]), "fwd_pct": round(e["fwd"], 2)}
                         for e in evals[-4:]][::-1]
    if kdir in ("bullish", "bearish"):
        good = sum(1 for e in evals if (e["fwd"] > 0) == (kdir == "bullish"))
        res.update(_rate("with_direction_rate_pct", good, len(evals) - good,
                         "instance"))
        # "found but too recent" is NOT "never happened" — the generic
        # zero-sample text would claim the pattern has no record when it has
        # one whose forward window simply hasn't completed yet
        if not evals and raw:
            res["with_direction_rate_pct_withheld"] = (
                f"{raw} instance{'s' if raw > 1 else ''} found, but "
                f"{'all' if raw > 1 else 'it is'} too recent to grade — the "
                f"{h}-bar forward window has not completed yet. Say that, "
                "not that the pattern has never appeared.")
        bgood = sum(1 for m in base if (m > 0) == (kdir == "bullish"))
        res["control"] = {
            "base_rate_pct": round(bgood / len(base) * 100) if base else None,
            "avg_move_pct": round(sum(base) / len(base), 2) if base else None,
            "what": f"every unconditional {h}-bar move in the same window"}
        if res.get("with_direction_rate_pct") is not None and base:
            res["edge_pp"] = (res["with_direction_rate_pct"]
                              - res["control"]["base_rate_pct"])
    else:
        res["direction_note"] = (
            "neutral shape — it has no textbook direction to score, so only "
            "the move distribution is shown; do not invent a success rate")
        if base:
            res["control"] = {
                "avg_abs_move_pct": round(sum(abs(m) for m in base)
                                          / len(base), 2),
                "what": f"every unconditional {h}-bar move in the same window"}
            if evals:
                res["avg_abs_move_pct"] = round(
                    sum(abs(e["fwd"]) for e in evals) / len(evals), 2)
    res["provenance"] = {
        "window": f"{ist(rows[0][0])} → {ist(rows[-1][0])} IST",
        "bars_scanned": n,
        "method": "forward close-to-close move measured from each instance's "
                  "completion bar (candles: the pattern bar; chart shapes: "
                  "the confirming break bar)"}
    res["_note"] = (
        "Quote the pattern rate NEXT TO the base rate — the edge is the "
        "difference, and a rate alone is decoration. This is one symbol's "
        "history at one horizon, not a forecast; past instances of a shape "
        "do not obligate the next one.")
    return res


# ── quarterly results ─────────────────────────────────────────────
# One event per quarter, synced from the filings table by sync_results.py.
# `trade_date` is the first session that could react: it already rolls
# forward for after-market filings, so nothing here re-derives it.
def _results(limit: int = 200) -> list[dict]:
    # single-symbol sandbox, same constant _rows uses; the table is keyed by
    # symbol so this widens without a schema change
    try:
        rows = _con.execute(
            "SELECT quarter, period_end, trade_date, broadcast_at, "
            "after_market, filings FROM results WHERE symbol=? "
            "ORDER BY trade_date DESC LIMIT ?", (_sym(), limit)).fetchall()
    except sqlite3.Error:
        return []
    return [{"quarter": q, "period_end": pe, "trade_date": td,
             "broadcast_at": ba, "after_market": bool(am), "filings": f}
            for q, pe, td, ba, am, f in rows]


_RESULT_SNAP_DAYS = 7      # weekend + a holiday cluster, never more


def _result_bar_index(rows: list[tuple], iso_date: str) -> int | None:
    """Index of the first bar ON OR AFTER that date — a result landing on a
    holiday reacts at the next session, not at the previous one.

    The roll-forward is BOUNDED. Without a bound, a date before the loaded
    window matched bar 0, which silently stamped the window's first bar as
    that quarter's results day: an event placed on a bar that has nothing to
    do with it, reported as if it were located. A date that cannot be placed
    must come back as None so the caller can say so.
    """
    t = _parse_ist(iso_date)
    if t is None or not rows or t < rows[0][0]:
        return None
    for i, r in enumerate(rows):
        if r[0] >= t:
            return i if r[0] - t <= _RESULT_SNAP_DAYS * 86400 else None
    return None


def tool_get_results(limit: int = 8, interval: str = "1d",
                     draw: bool = False, draw_mode: str = "add") -> dict:
    """Quarterly result dates, and optionally mark them on the chart.

    Answers "when were the last results", "when did Q1 report", "mark
    earnings on the chart". The date is the session the market could first
    react to the filing, which for an after-market announcement is the
    NEXT day — that distinction is the whole point of the field.
    """
    mode = str(draw_mode or "add").lower()
    if mode == "clear":
        _scene_add({"kind": "clear", "scope": "markers", "owner": "get_results"})
        return {"cleared": True, "_note": "Result markers removed."}
    ev = _results()
    if not ev:
        return {"error": "no result dates stored",
                "_note": ("The results table is empty — say the data is not "
                          "loaded rather than estimating dates from memory.")}
    n = max(1, min(int(limit or 8), 40))
    recent = ev[:n]
    rows = _rows(interval, 2000)
    first_bar = _ist(rows[0][0], False) if rows else None

    out: dict = {
        "results": [{"quarter": e["quarter"], "reported_for_period_ending": e["period_end"],
                     "market_reacted": e["trade_date"],
                     "after_market_announcement": e["after_market"]}
                    for e in recent],
        "stored": {"events": len(ev), "from": ev[-1]["trade_date"],
                   "to": ev[0]["trade_date"]},
    }
    # how long after a quarter ends results typically land — a measured
    # spacing, offered instead of a guessed future date
    lags = []
    for e in ev:
        a, b = _parse_ist(e["period_end"]), _parse_ist(e["trade_date"])
        if a and b:
            lags.append(round((b - a) / 86400))
    if lags:
        lags.sort()
        out["typical_lag_days"] = {
            "median": lags[len(lags) // 2], "min": lags[0], "max": lags[-1],
            "what": "days from quarter end to the reacting session"}
    out["_no_future_note"] = (
        "These are announcements that have already happened; there is no "
        "scheduled future date here. If asked when the next results are, say "
        "that plainly and offer the typical lag from quarter end — never "
        "state a future date as if it were confirmed.")

    if draw:
        if mode == "replace":
            _scene_add({"kind": "clear", "scope": "markers", "owner": "get_results"})
        marks, skipped = [], []
        for e in recent:
            i = _result_bar_index(rows, e["trade_date"]) if rows else None
            if i is None:
                skipped.append(e["quarter"])
                continue
            marks.append({"t": rows[i][0], "text": e["quarter"],
                          "up": rows[i][4] >= rows[i][1]})
        if marks:
            _scene_add({"kind": "markers", "id": "RESULTS", "pane": "price",
                        "role": "neutral", "marks": marks,
                        "source": {"tool": "get_results",
                                   "method": "first session able to react to the filing",
                                   "interval": interval, "bars_scanned": len(rows),
                                   "strength": "reported", "first_touch": marks[-1]["text"],
                                   "last_touch": marks[0]["text"]}})
            out["marked"] = len(marks)
        if skipped:
            out["not_marked"] = skipped
            out["_not_marked_note"] = (
                f"These quarters predate the loaded {interval} bars"
                + (f" (which start {first_bar})" if first_bar else "")
                + " and were NOT marked — name them rather than implying "
                  "everything was placed.")
    return out


def tool_evaluate_results(horizon_bars: int = 5, interval: str = "1d",
                          lookback_bars: int = 3000) -> dict:
    """What price actually does around this company's results.

    Reaction size, direction, run-up and drift — each against the
    unconditional base rate over the same window, because a number like
    "results move it 2%" means nothing until you know an ordinary day
    moves it 1.4%. Absolute moves lead: results cause volatility far more
    reliably than they cause a direction.
    """
    h = max(1, min(int(horizon_bars or 5), 30))
    rows = _rows(interval, max(300, min(int(lookback_bars or 3000), 4000)))
    if not rows:
        return {"error": f"no bars for interval {interval}"}
    ev = _results()
    if not ev:
        return {"error": "no result dates stored"}
    n = len(rows)
    closes = [r[4] for r in rows]
    opens = [r[1] for r in rows]

    studied, too_early, too_recent = [], 0, 0
    for e in ev:
        i = _result_bar_index(rows, e["trade_date"])
        if i is None or i == 0:
            too_early += 1
            continue
        if i + h >= n:
            too_recent += 1
            continue
        prev_c = closes[i - 1]
        studied.append({
            "quarter": e["quarter"], "date": e["trade_date"],
            "gap": (opens[i] - prev_c) / prev_c * 100,
            "day": (closes[i] - prev_c) / prev_c * 100,
            "intraday": (closes[i] - opens[i]) / opens[i] * 100,
            "after": (closes[i + h] - closes[i]) / closes[i] * 100,
            "runup": ((closes[i - 1] - closes[i - h - 1]) / closes[i - h - 1] * 100
                      if i - h - 1 >= 0 else None),
        })
    res: dict = {"events_evaluated": len(studied),
                 "outside_scanned_window": too_early, "too_recent": too_recent,
                 "events_stored": len(ev),
                 "horizon_bars": h, "interval": interval}
    if not studied:
        res["_note"] = ("No result date falls inside the loaded bars. Say the "
                        "window does not cover any results rather than "
                        "describing moves that were not measured.")
        return res

    # control: every ordinary session in the same window
    base_day = [(closes[j] - closes[j - 1]) / closes[j - 1] * 100
                for j in range(1, n)]
    base_gap = [(opens[j] - closes[j - 1]) / closes[j - 1] * 100
                for j in range(1, n)]
    base_after = [(closes[j + h] - closes[j]) / closes[j] * 100
                  for j in range(n - h)]
    avg = lambda xs: round(sum(xs) / len(xs), 2) if xs else None   # noqa: E731
    aavg = lambda xs: round(sum(abs(x) for x in xs) / len(xs), 2) if xs else None  # noqa: E731

    day = [s["day"] for s in studied]
    gap = [s["gap"] for s in studied]
    aft = [s["after"] for s in studied]
    run = [s["runup"] for s in studied if s["runup"] is not None]
    up = sum(1 for d in day if d > 0)

    res["reaction_day"] = {
        "avg_abs_move_pct": aavg(day), "avg_move_pct": avg(day),
        "avg_abs_gap_pct": aavg(gap),
        "biggest": max(studied, key=lambda s: abs(s["day"]))["quarter"],
        "biggest_move_pct": round(max(day, key=abs), 2)}
    res["control"] = {
        "avg_abs_move_pct": aavg(base_day), "avg_move_pct": avg(base_day),
        "avg_abs_gap_pct": aavg(base_gap),
        "avg_move_pct_over_horizon": avg(base_after),
        "what": f"every one of the {len(base_day)} ordinary sessions in the same window"}
    if res["reaction_day"]["avg_abs_move_pct"] and res["control"]["avg_abs_move_pct"]:
        res["reaction_day"]["times_a_normal_day"] = round(
            res["reaction_day"]["avg_abs_move_pct"] / res["control"]["avg_abs_move_pct"], 2)
    res.update(_rate("up_rate_pct", up, len(day) - up, "result"))
    res["after_results"] = {
        "avg_move_pct": avg(aft), "avg_abs_move_pct": aavg(aft),
        "what": f"close on the reaction day → close {h} bars later"}
    if run:
        res["run_up_before"] = {"avg_move_pct": avg(run),
                                "what": f"the {h} sessions into the announcement"}
    res["recent"] = [{"quarter": s["quarter"], "date": s["date"],
                      "gap_pct": round(s["gap"], 2), "day_pct": round(s["day"], 2),
                      f"next_{h}_bars_pct": round(s["after"], 2)}
                     for s in studied[:6]]
    res["provenance"] = {
        "window": f"{_ist(rows[0][0], False)} → {_ist(rows[-1][0], False)} IST",
        "bars_scanned": n,
        "method": ("each event is the first session able to react to the "
                   "filing (after-market announcements roll to the next day); "
                   "one event per quarter, first filing wins")}
    res["_note"] = (
        "Lead with the reaction size AGAINST the control — 'results days move "
        "X% versus Y% on an ordinary day' — because the absolute move is the "
        "reliable finding and the direction usually is not. Never present the "
        "up-rate as a way to predict the next one, and obey the withheld "
        "message when the rate is null.")
    return res


# ── explain_move: the whole "why did it move" evidence pack in one call ────
#
# Six of the seven rungs of a causal question are local arithmetic: is the
# move abnormal for THIS stock, how much of it was the index, when in the
# session it happened, was a result nearby, what structure broke, and how
# similar moves resolved before. Fetching them one narrow tool at a time cost
# a measured 9 calls / 3 rounds / 28s — so they ship together, unasked,
# because every extra round re-pays the full prompt floor and a full RTT.
# The return states FACTS, never directives: "NIFTY fell 0.8% over the same
# window" is computation; "so search the market story" would be code deciding
# meaning, which is the disease this file is built to avoid.

def _bench_closes() -> dict:
    """ISO date -> (close, source) for the benchmark index, whole history."""
    try:
        return {d: (c, s) for d, c, s in _con.execute(
            "SELECT trade_date, c, source FROM benchmark WHERE symbol='NIFTY 50'")}
    except sqlite3.Error:
        return {}


def _iso_day(ts: int) -> str:
    return datetime.fromtimestamp(ts + IST_OFF, tz=timezone.utc).date().isoformat()


def _minutes_of(day_ts: int) -> list[tuple]:
    """1-min bars of the IST session containing day_ts."""
    day0 = _ist_day(day_ts) * 86400 - IST_OFF
    hi = day0 + 86400
    live = _live_view(_sym())
    if live and live[1] is not None:
        hi = min(hi, live[1])   # replay clock: the session's future is hidden
    rows = _con.execute(
        "SELECT ts,o,h,l,c,v FROM bars WHERE symbol=? "
        "AND ts>=? AND ts<? ORDER BY ts", (_sym(), day0, hi)).fetchall()
    if live and live[0] is not None and day0 <= live[0][0] < day0 + 86400:
        _merge_form_intraday(rows, live[0])
    return rows


def _session_anatomy(prev_close: float, mins: list[tuple]) -> dict:
    """Where inside the session the move happened — gap vs legs vs close —
    plus how concentrated the volume was at the edges of the day."""
    if not mins:
        return {}
    o, cl = mins[0][1], mins[-1][4]
    vol = sum(m[5] for m in mins) or 1

    def hm(ts: int) -> str:
        t = datetime.fromtimestamp(ts + IST_OFF, tz=timezone.utc)
        return f"{t.hour:02d}:{t.minute:02d}"

    def px_at(clock: str) -> float:
        best = o
        for m in mins:
            if hm(m[0]) <= clock:
                best = m[4]
            else:
                break
        return best

    legs = [("gap_overnight", prev_close, o)]
    for name, a, b in (("open_15m", "09:15", "09:30"),
                       ("morning", "09:30", "12:00"),
                       ("midday", "12:00", "14:30")):
        legs.append((name, px_at(a) if a != "09:15" else o, px_at(b)))
    legs.append(("last_hour", px_at("14:30"), cl))
    out = {k: round((b / a - 1) * 100, 2) for k, a, b in legs if a}
    out["vol_first_30m_pct"] = round(sum(
        m[5] for m in mins if hm(m[0]) < "09:45") / vol * 100)
    out["vol_last_30m_pct"] = round(sum(
        m[5] for m in mins if hm(m[0]) >= "15:00") / vol * 100)
    return out


# ── flows: who acted — delivery %, futures OI, bulk/block deals ────────────
# Price alone cannot say whether a fall was real owners exiting or intraday
# churn, fresh bearish bets or longs surrendering. These tables (synced by
# sync_flows.py from exchange-published data) carry exactly that, and the
# quadrant names below are the market's standard vocabulary for a piece of
# sign arithmetic — a classification, never a forecast.

_QUADRANT = {(1, 1): "long buildup", (-1, 1): "short buildup",
             (-1, -1): "long unwinding", (1, -1): "short covering"}


def _flows_have() -> bool:
    try:
        return bool(_con.execute(
            "SELECT 1 FROM delivery WHERE symbol=? LIMIT 1", (_sym(),)).fetchone())
    except sqlite3.Error:
        return False


def _deliv_history(before_iso: str, n: int = 500) -> list[float]:
    try:
        return [r[0] for r in _con.execute(
            "SELECT deliv_per FROM delivery WHERE symbol=? AND d<? "
            "AND deliv_per IS NOT NULL ORDER BY d DESC LIMIT ?",
            (_sym(), before_iso, n))]
    except sqlite3.Error:
        return []


def _flows_sessions(d0_iso: str, d1_iso: str, closes_by_day: dict) -> list[dict]:
    """Per-session flows facts for a window: delivery (with the percentile
    against this stock's own trailing history) and the OI quadrant."""
    out: dict[str, dict] = {}
    try:
        for d, per, dq, q in _con.execute(
                "SELECT d, deliv_per, deliv_qty, qty FROM delivery "
                "WHERE symbol=? AND d BETWEEN ? AND ? ORDER BY d",
                (_sym(), d0_iso, d1_iso)):
            s = out.setdefault(d, {"date": d})
            if per is not None:
                s["delivery_pct"] = round(per, 2)
                hist = _deliv_history(d)
                if len(hist) >= 100:
                    s["delivery_pctile_own_history"] = round(
                        sum(1 for x in hist if x <= per) / len(hist) * 100)
                else:
                    s["delivery_pctile_withheld"] = (
                        f"only {len(hist)} prior sessions — too few to rank")
        for d, oi, chg in _con.execute(
                "SELECT d, SUM(oi), SUM(oi_chg) FROM fut_oi "
                "WHERE symbol=? AND d BETWEEN ? AND ? GROUP BY d "
                "ORDER BY d", (_sym(), d0_iso, d1_iso)):
            s = out.setdefault(d, {"date": d})
            s["futures_oi"] = oi
            s["oi_change"] = chg
            pc = closes_by_day.get(d)
            if pc is not None and chg:
                s["oi_quadrant"] = _QUADRANT.get(
                    (1 if pc >= 0 else -1, 1 if chg > 0 else -1))
    except sqlite3.Error:
        return []
    return [out[k] for k in sorted(out)]


def _flows_deals(d0_iso: str, d1_iso: str) -> list[dict]:
    try:
        return [{"date": d, "type": k, "client": c, "side": s,
                 "qty": q, "price": p}
                for d, k, c, s, q, p in _con.execute(
                    "SELECT d, kind, client, side, qty, price FROM deals "
                    "WHERE symbol=? AND d BETWEEN ? AND ? ORDER BY d",
                    (_sym(), d0_iso, d1_iso))]
    except sqlite3.Error:
        return []


def _day_change_signs(rows: list[tuple], i0: int, i1: int) -> dict:
    """ISO day -> that session's close-on-close change sign source."""
    return {_iso_day(rows[i][0]): (rows[i][4] / rows[i - 1][4] - 1)
            for i in range(max(1, i0), i1 + 1)}


def tool_explain_move(frm: str = "", to: str = "") -> dict:
    """Everything local that bears on "why did it move" over a date window.

    Abnormality against this stock's own history, the index split, the
    intraday anatomy, results proximity, structure crossed, patterns ending
    in the window, and the record of similar past moves — one call.
    """
    rows = _rows("1d", 5000)
    if len(rows) < 60:
        return {"error": "not enough daily history"}
    closes = [r[4] for r in rows]

    # ── resolve the window to session indexes ──
    t0 = _parse_ist(frm) if frm else None
    t1 = _parse_ist(to) if to else None
    if (frm and t0 is None) or (to and t1 is None):
        return {"error": "could not read the date(s)",
                "hint": "use the chart's format, e.g. '22 Jul 2026' or "
                        "'21 Jul 2026' to '22 Jul 2026'"}
    if t0 is None and t1 is None:
        i0 = i1 = len(rows) - 1                      # the latest session
    else:
        if t0 is None:
            t0 = t1
        if t1 is None:
            t1 = t0
        if t1 < t0:
            t0, t1 = t1, t0
        i0 = next((i for i, r in enumerate(rows) if r[0] >= t0), None)
        i1 = next((i for i in range(len(rows) - 1, -1, -1)
                   if rows[i][0] < t1 + 86400), None)
        if i0 is None or i1 is None or i0 > i1:
            return {"error": "no sessions inside that window",
                    "data_spans": f"{_ist(rows[0][0], False)} → "
                                  f"{_ist(rows[-1][0], False)} IST"}
    if i0 == 0:
        i0 = 1                                       # need a prior close
    n = i1 - i0 + 1
    if n > 60:
        return {"error": f"window is {n} sessions — this tool explains moves, "
                         "not eras; narrow it to 60 sessions or fewer"}

    prev_close, last_close = closes[i0 - 1], closes[i1]
    ret = last_close / prev_close - 1
    direction = 1 if ret >= 0 else -1

    # ── per-session rows, with intraday anatomy where 1-min bars exist ──
    sessions = []
    for i in range(i0, min(i1, i0 + 9) + 1):
        pc = closes[i - 1]
        t, o, h, l, c, v = rows[i]
        v20 = [rows[j][5] for j in range(max(0, i - 20), i)]
        s = {"date": _ist(t, False),
             "close": round(c, 2), "pct": round((c / pc - 1) * 100, 2),
             "range": [round(l, 2), round(h, 2)], "volume": v,
             "vol_vs_20d_avg": round(v / (sum(v20) / len(v20)), 2)
             if v20 and sum(v20) else None}
        anatomy = _session_anatomy(pc, _minutes_of(t))
        if anatomy:
            s["anatomy_pct"] = anatomy
        sessions.append(s)
    omitted = n - len(sessions)

    out: dict = {
        "window": {"from": _ist(rows[i0][0], False), "to": _ist(rows[i1][0], False),
                   "sessions": n,
                   "move_pct": round(ret * 100, 2),
                   "from_close": round(prev_close, 2),
                   "to_close": round(last_close, 2)},
        "sessions": sessions,
    }
    if omitted > 0:
        out["sessions_omitted"] = (f"{omitted} middle sessions omitted — "
                                   "call get_bars for them if needed")

    # ── abnormality: this move against this stock's own n-session history ──
    hist = [closes[j] / closes[j - n] - 1
            for j in range(n, i0)][-500:]
    if len(hist) >= 30:
        mean = sum(hist) / len(hist)
        sd = (sum((x - mean) ** 2 for x in hist) / (len(hist) - 1)) ** 0.5
        pct_rank = sum(1 for x in hist if abs(x) <= abs(ret)) / len(hist)
        med_abs = _median([abs(x) for x in hist])
        out["abnormality"] = {
            "z_score": round(ret / sd, 2) if sd else None,
            "abs_percentile": round(pct_rank * 100),
            "typical_abs_move_pct": round((med_abs or 0) * 100, 2),
            "based_on": f"{len(hist)} prior {n}-session windows",
        }
    else:
        out["abnormality"] = {"withheld": f"only {len(hist)} prior "
                              f"{n}-session windows — too few to place this one"}

    # ── the index split: how much of the move was the market ──
    bench = _bench_closes()
    d_prev, d_last = _iso_day(rows[i0 - 1][0]), _iso_day(rows[i1][0])
    if bench.get(d_prev) and bench.get(d_last):
        b_ret = bench[d_last][0] / bench[d_prev][0] - 1
        pairs = []
        for i in range(i0 - 1, 0, -1):
            da, db = _iso_day(rows[i - 1][0]), _iso_day(rows[i][0])
            if da in bench and db in bench:
                pairs.append((closes[i] / closes[i - 1] - 1,
                              bench[db][0] / bench[da][0] - 1))
            if len(pairs) >= 250:
                break
        blk: dict = {"index": "NIFTY 50",
                     "index_pct": round(b_ret * 100, 2),
                     "source": bench[d_last][1]}
        if len(pairs) >= 60:
            mb = sum(b for _, b in pairs) / len(pairs)
            ms = sum(s for s, _ in pairs) / len(pairs)
            var = sum((b - mb) ** 2 for _, b in pairs)
            cov = sum((s - ms) * (b - mb) for s, b in pairs)
            beta = cov / var if var else None
            if beta is not None:
                blk["beta"] = round(beta, 2)
                blk["expected_from_index_pct"] = round(beta * b_ret * 100, 2)
                blk["residual_pct"] = round((ret - beta * b_ret) * 100, 2)
                blk["beta_note"] = (f"beta over {len(pairs)} pre-window "
                                    "sessions; residual = the part the "
                                    "index does not account for")
        out["index_split"] = blk
    else:
        out["index_split"] = {"withheld": "no benchmark close for "
                              f"{d_prev if not bench.get(d_prev) else d_last} "
                              "— market-vs-stock split unavailable; do not "
                              "guess which it was"}

    # ── scheduled events: results on or near the window ──
    near = []
    dates_iso = {_iso_day(rows[i][0]): i for i in range(len(rows))}
    for r in _results(200):
        j = dates_iso.get(r["trade_date"])
        if j is None:
            continue
        if i0 - 3 <= j <= i1 + 3:
            rel = ("in window" if i0 <= j <= i1 else
                   f"{i0 - j} session(s) before window" if j < i0 else
                   f"{j - i1} session(s) after window")
            # "first_reactable_session", not "date": an after-market filing's
            # release day and the day the market could trade on it differ,
            # and a bare "date" got the two conflated in replies
            near.append({"quarter": r["quarter"],
                         "first_reactable_session": r["trade_date"],
                         "filed_after_market_close": r["after_market"],
                         "position": rel})
    out["results_nearby"] = near or "none within 3 sessions of the window"

    # ── structure: pre-window levels the move crossed, and what's next ──
    pre = rows[max(0, i0 - 300):i0]
    crossed, nearest = [], {}
    if len(pre) >= 60:
        for lv in _levels(pre, with_time=False):
            p = lv["price"]
            entry = {"price": p, "role_before_move": lv["role"],
                     "touches": lv["touches"]}
            if (prev_close - p) * (last_close - p) < 0:
                crossed.append(entry)
            elif p < last_close and (
                    "below" not in nearest
                    or p > nearest["below"]["price"]):
                nearest["below"] = entry
            elif p > last_close and (
                    "above" not in nearest
                    or p < nearest["above"]["price"]):
                nearest["above"] = entry
    out["structure"] = {
        "levels_crossed": crossed[:3] or "none — the move stayed between "
                                         "its pre-window levels",
        "nearest_below": nearest.get("below"),
        "nearest_above": nearest.get("above"),
    }

    # ── flows: who acted — delivery, OI positioning, deals ──
    if _flows_have():
        signs = _day_change_signs(rows, i0, i1)
        fl: dict = {}
        sess_flows = _flows_sessions(_iso_day(rows[i0][0]),
                                     _iso_day(rows[i1][0]), signs)
        if sess_flows:
            fl["sessions"] = sess_flows[:10]
        deals = _flows_deals(_iso_day(rows[i0][0]), _iso_day(rows[i1][0]))
        fl["deals_in_window"] = deals[:6] if deals else (
            "none — no bulk or block deal was printed in this window")
        fl["_read"] = (
            "Delivery % is the share of traded quantity that changed OWNERSHIP "
            "(vs intraday churn); its percentile is against this stock's own "
            "trailing sessions. The OI quadrant is sign arithmetic on price "
            "and futures open interest — standard names, not forecasts.")
        out["flows"] = fl
    else:
        out["flows"] = {"withheld": "flows tables not synced — delivery, OI "
                        "and deals unavailable; do not infer who was buying "
                        "or selling"}

    # ── patterns whose story ends in (or right at) the window ──
    thru = rows[max(0, i1 - 300):i1 + 1]
    pats = []
    try:
        piv = _pivots(thru, 5)
        tol = _tolerance(thru)
        fmt = lambda ts: _ist(ts, False)  # noqa: E731
        for p in patterns.chart_patterns(thru, piv, tol, fmt, None, limit=8):
            pt = _parse_ist(p["to"])
            if pt and rows[i0][0] - 5 * 86400 <= pt <= rows[i1][0] + 86400:
                pats.append({"pattern": p["pattern"],
                             "direction": p["direction"],
                             "status": p.get("status"),
                             "from": p["from"], "to": p["to"]})
        atr = _atr(thru, 14)
        for c in patterns.candlesticks(thru, atr, lambda ts: ts, None,
                                       limit=12):
            if rows[i0][0] <= c["t"] <= rows[i1][0]:
                pats.append({"pattern": c["pattern"],
                             "direction": c["direction"],
                             "candle_on": _ist(c["t"], False),
                             "at": c["at"]})
    except Exception as exc:  # noqa: BLE001 — detector failure must not kill the pack
        logging.warning("explain_move patterns failed: %s", exc)
    out["patterns_in_window"] = pats[:5] or "none detected ending in the window"

    # ── the record of similar past moves, with its control ──
    events, j, last_j = [], n, -10**9
    while j < i0 - 1:
        r_j = closes[j] / closes[j - n] - 1
        if (r_j >= 0) == (ret >= 0) and abs(r_j) >= abs(ret) and j - last_j >= n:
            events.append(j)
            last_j = j
        j += 1
    cont = [1 if ((closes[e + 1] / closes[e] - 1) >= 0) == (ret >= 0) else 0
            for e in events if e + 1 <= i0 - 1]
    base_all = [1 if ((closes[i] / closes[i - 1] - 1) >= 0) == (ret >= 0) else 0
                for i in range(1, i0)]
    word = "up" if direction > 0 else "down"
    hist_blk = {"matched": f"{len(events)} prior {n}-session moves {word} "
                           f"≥ {abs(ret) * 100:.2f}% (non-overlapping)"}
    hist_blk.update(_rate("continued_next_session_pct",
                          sum(cont), len(cont) - sum(cont), "instance"))
    if base_all:
        hist_blk["control_any_session_pct"] = round(
            sum(base_all) / len(base_all) * 100)
        hist_blk["control_note"] = (f"share of ALL sessions that closed {word} "
                                    "— quote the continuation rate only "
                                    "against this")
    out["similar_moves_before"] = hist_blk

    mins_last = _minutes_of(rows[i1][0])
    if i1 == len(rows) - 1 and mins_last:
        hhmm = datetime.fromtimestamp(mins_last[-1][0] + IST_OFF,
                                      tz=timezone.utc).strftime("%H:%M")
        if hhmm < "15:25":
            out["partial_session"] = (f"the last session's data ends at {hhmm} "
                                      "IST — treat it as in progress")

    out["_note"] = (
        "Every figure above is computed from the local bar store"
        + (" (index closes: " + bench[d_last][1] + ")" if bench.get(d_last) else "")
        + ". This is the complete local evidence: quote quantities only from "
          "here. Read abnormality first — a move inside this stock's normal "
          "range needs no catalyst, and 'no clear catalyst' is a complete "
          "answer. If an outside cause is still plausible, call search_news "
          "once for dated events; behavioural readings (who was likely "
          "buying/selling) must name the observed fact they rest on and be "
          "stated as inference, not fact.")
    return out


def tool_get_flows(frm: str = "", to: str = "", lookback_sessions: int = 10) -> dict:
    """Ownership and positioning series on their own — delivery %, futures
    OI with quadrant, bulk/block deals — for direct questions that are not
    about explaining one move ("how has delivery trended", "any block deals
    this month", "are shorts building up")."""
    if not _flows_have():
        return {"error": "flows tables not synced",
                "_note": "Say delivery/OI/deal data is unavailable — do not "
                         "infer ownership or positioning from price."}
    rows = _rows("1d", 5000)
    if not rows:
        return {"error": "no daily bars"}
    t0 = _parse_ist(frm) if frm else None
    t1 = _parse_ist(to) if to else None
    if (frm and t0 is None) or (to and t1 is None):
        return {"error": "could not read the date(s)",
                "hint": "chart format, e.g. '01 Jul 2026'"}
    if t0 is None:
        n = max(2, min(int(lookback_sessions or 10), 60))
        i0, i1 = max(1, len(rows) - n), len(rows) - 1
    else:
        if t1 is None:
            t1 = t0
        if t1 < t0:
            t0, t1 = t1, t0
        i0 = next((i for i, r in enumerate(rows) if r[0] >= t0), None)
        i1 = next((i for i in range(len(rows) - 1, -1, -1)
                   if rows[i][0] < t1 + 86400), None)
        if i0 is None or i1 is None or i0 > i1:
            return {"error": "no sessions inside that window"}
        i0 = max(1, i0)
    # the per-session listing is capped, but the FULL asked window is kept —
    # capping it silently once hid a year-old block deal from "last year"
    i0_full = i0
    narrowed = i1 - i0 > 60
    if narrowed:
        i0 = i1 - 60
    d0, d1 = _iso_day(rows[i0][0]), _iso_day(rows[i1][0])
    d0_full = _iso_day(rows[i0_full][0])
    signs = _day_change_signs(rows, i0, i1)
    sess = _flows_sessions(d0, d1, signs)
    deals = _flows_deals(d0_full, d1)
    out = {"window": {"from": _ist(rows[i0_full][0], False),
                      "to": _ist(rows[i1][0], False),
                      "sessions": i1 - i0_full + 1},
           "sessions": sess[-30:],
           "deals": deals[:12] or "none printed in this window"}
    if narrowed:
        out["sessions_note"] = (
            f"the asked window spans {i1 - i0_full + 1} sessions — the "
            f"per-session delivery/OI listing covers only the LAST 61 "
            f"(from {_ist(rows[i0][0], False)}); deals cover the whole "
            "window. Say so if summarising delivery or OI for the full span.")
    if len(sess) > 30:
        out["sessions_omitted"] = f"{len(sess) - 30} earlier sessions omitted"
    pers = [s["delivery_pct"] for s in sess if "delivery_pct" in s]
    if pers:
        out["window_delivery"] = {"avg_pct": round(sum(pers) / len(pers), 2),
                                  "max_pct": max(pers), "min_pct": min(pers)}
    ois = [s for s in sess if s.get("oi_change") is not None]
    if ois:
        out["window_oi_change"] = sum(s["oi_change"] for s in ois)
    out["_note"] = (
        "Delivery % = share of traded quantity that changed ownership; its "
        "percentile is against this stock's own history. OI quadrant names "
        "are sign arithmetic on price and open interest — classifications, "
        "never forecasts, and none of this is a buy/sell signal. Quote "
        "quantities only from here.")
    return out


# ── search_news: the outside world, behind a thin function tool ────────────
#
# The hosted web_search_preview costs ~4,300 input tokens merely to be
# ATTACHED — as much as all other tools together — and the prompt floor is
# re-paid every round. So the hosted tool never enters the main wire: this
# function makes a second, throwaway Responses call that carries it, and the
# main loop pays only this thin schema plus ~300 tokens of dated events.
# The sub-call's prompt is the rail: events with dates and sources, never
# quantities — so a stale aggregator's price table can't reach the answer.

_NEWS_TTL_RECENT = 3600           # window touching the present: 1 hour
_NEWS_RECENT_DAYS = 3             # past this age a window's news is settled

_NEWS_PROMPT = (
    "You are a research clerk for an Indian equities chart (NSE: {symbol}). "
    "Search the web once — twice only if the first search returns nothing — "
    "and report what HAPPENED around {window}: company announcements or "
    "filings, analyst/rating actions, sector or market-wide events, and "
    "macro or global causes the financial press tied to those dates."
    "{focus} Reply with 3-6 lines, each 'date · what happened · source "
    "domain'. Only events that carry a date. Do NOT report prices, returns, "
    "percentages, volumes or targets — the caller holds exact figures and "
    "yours would be stale. If nothing is dated to the window, reply exactly: "
    "nothing found for this window.")


def _news_cache_get(key: str) -> dict | None:
    try:
        _con.execute("CREATE TABLE IF NOT EXISTS news_cache ("
                     "key TEXT PRIMARY KEY, fetched_at INTEGER, "
                     "recent INTEGER, payload TEXT)")
        row = _con.execute("SELECT fetched_at, recent, payload "
                           "FROM news_cache WHERE key=?", (key,)).fetchone()
    except sqlite3.Error:
        return None
    if not row:
        return None
    fetched, recent, payload = row
    import time as _t
    if recent and _t.time() - fetched > _NEWS_TTL_RECENT:
        return None
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        return None


def _news_cache_put(key: str, recent: bool, data: dict) -> None:
    import time as _t
    try:
        _con.execute("INSERT OR REPLACE INTO news_cache VALUES (?,?,?,?)",
                     (key, int(_t.time()), int(recent), json.dumps(data)))
        _con.commit()
    except sqlite3.Error:
        pass


def tool_search_news(frm: str = "", to: str = "", focus: str = "") -> dict:
    """Dated outside events for a window — an isolated browse, cached.

    Returns causes only: events with dates and source domains. Quantities
    never come from here.
    """
    if not AZURE_ENDPOINT or not AZURE_KEY:
        return {"error": "web lookup unavailable (no LLM credentials)"}
    t0 = _parse_ist(frm) if frm else None
    t1 = _parse_ist(to) if to else t0
    if t0 is None:
        return {"error": "give the window, e.g. frm='21 Jul 2026' "
                         "to='22 Jul 2026'"}
    if t1 is None:
        t1 = t0
    d0, d1 = _iso_day(t0), _iso_day(t1)
    window = d0 if d0 == d1 else f"{d0} to {d1}"

    key = f"{_sym()}|{d0}|{d1}"
    cached = _news_cache_get(key)
    if cached:
        return {**cached, "cached": True}

    import time as _t
    recent = (_t.time() - t1) < _NEWS_RECENT_DAYS * 86400
    prompt = _NEWS_PROMPT.format(
        symbol=_sym(), window=window,
        focus=f" Particular focus: {focus.strip()}." if focus.strip() else "")
    payload = {
        "model": LLM_DEPLOYMENT,
        "input": [{"role": "user", "content": prompt}],
        "tools": [{"type": "web_search_preview",
                   "search_context_size": "low"}],
        "max_output_tokens": 600,
        "reasoning": {"effort": "low"},
        "service_tier": LLM_SERVICE_TIER,
    }
    req = urllib.request.Request(
        f"{AZURE_ENDPOINT}/responses",
        data=json.dumps(payload).encode(),
        headers={"api-key": AZURE_KEY, "Content-Type": "application/json"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60, context=_ssl_ctx()) as r:
            data = json.loads(r.read())
    except Exception as exc:  # noqa: BLE001 — a dead browse must not kill the turn
        return {"error": f"web lookup failed: {exc}",
                "_note": "Answer from the local evidence and say the web "
                         "lookup was unavailable — do not guess at news."}

    text, sources, searched = [], [], 0
    for item in data.get("output", []):
        if item.get("type") == "web_search_call":
            searched += 1
        elif item.get("type") == "message":
            for c in item.get("content", []):
                if c.get("type") == "output_text":
                    text.append(c.get("text", ""))
                    for a in c.get("annotations") or []:
                        if a.get("type") == "url_citation" and a.get("url"):
                            sources.append(a["url"])
    body = "".join(text).strip()
    out = {
        "window": window,
        "events": body or "nothing found for this window",
        "sources": sorted(set(sources))[:6],
        "_note": ("These are candidate CAUSES only — events with dates. "
                  "Every quantity (price, %, volume, level) must come from "
                  "the chart tools; if a headline implies a number, use the "
                  "tool's number. An event here explains the move only if "
                  "its timing fits the anatomy (a mid-session move is not "
                  "explained by overnight news)."),
    }
    if searched and body:
        _news_cache_put(key, recent, out)
    return out


def tool_get_bars(interval: str = "5m", frm: str | None = None,
                  to: str | None = None, limit: int = 40) -> dict:
    limit = max(1, min(int(limit or 40), 80))
    t_from, t_to = _parse_ist(frm), _parse_ist(to)
    # A window that was ASKED FOR and could not be read must not fall through
    # to "the most recent bars" — that answers a different question in the
    # shape of the right one, and the dates come back looking authoritative.
    bad = [n for n, v, p in (("frm", frm, t_from), ("to", to, t_to)) if v and p is None]
    if bad:
        return {"error": f"could not read {' and '.join(bad)} as a date",
                "given": {"frm": frm, "to": to},
                "hint": "use the format the chart uses, e.g. '08 Jul 2026 15:25' "
                        "or '08 Jul 2026'. Nothing was returned — do not describe "
                        "bars for this window until the call succeeds."}
    iv = INTRADAY_MIN.get(interval, 0) * 60 or 86400

    # A point query (frm == to, or to before frm) means "the bar at that
    # moment" — widen it, else an exclusive `to` returns an empty set.
    if t_from and t_to and t_to <= t_from:
        t_to = t_from + iv * 3
        t_from -= iv * 3
    # `to` is exclusive downstream: push it out one bar so the endpoint
    # bar the user named is actually included.
    rows = _rows(interval, limit if not t_from else 2000,
                 (t_to + iv) if t_to else None)
    if t_from:
        # keep the bar CONTAINING t_from, not just bars starting after it
        rows = [r for r in rows if r[0] + iv > t_from][:limit]
    if not rows:
        return {"error": "no bars in that range",
                "hint": "history starts 2015-02-02; markets are closed on "
                        "weekends and NSE holidays"}
    return {
        "bars": [{"t": _ist(r[0]), "o": r[1], "h": r[2], "l": r[3], "c": r[4], "v": r[5]}
                 for r in rows],
        "provenance": {"method": "raw OHLCV from local 1-min store (resampled)",
                       "interval": interval, "returned": len(rows)},
    }


# What the FE chart can actually draw. Kept honest deliberately: the model
# used to be told the chart "does not support Bollinger Bands" while the FE
# had them one click away, and the opposite failure — claiming to have drawn
# something invisible — is worse. This list is the contract between the two.
# Line names per indicator, and the ROLE each line plays. The role decides
# how the chart styles it, so presentation stays in the FE while structure
# stays here — the same split geometry.js and tools.js already use.
_INDICATOR_LINES = {
    "sma": ["sma"], "ema": ["ema"], "wma": ["wma"], "hma": ["hma"], "dema": ["dema"],
    "bbands": ["middle", "upper", "lower"], "keltner": ["middle", "upper", "lower"],
    "donchian": ["middle", "upper", "lower"],
    "vwap": ["vwap"], "anchored_vwap": ["anchored_vwap"],
    "supertrend": ["supertrend_up", "supertrend_down"], "psar": ["psar"],
    "rsi": ["rsi"], "macd": ["histogram", "macd", "signal"],
    "stoch": ["k", "d"], "stochrsi": ["k", "d"],
    "adx": ["adx", "plus_di", "minus_di"], "cci": ["cci"],
    "williams_r": ["williams_r"], "roc": ["roc"], "atr": ["atr"],
    "obv": ["obv"], "ad": ["ad"], "cmf": ["cmf"], "mfi": ["mfi"],
    "aroon": ["aroon_up", "aroon_down"],
}

# Must mirror preview/js/indicators.js CATALOG exactly. If it drifts, the
# model either refuses to draw something the chart can show, or claims to have
# drawn something invisible.
_FE_RENDERABLE = {"sma", "ema", "bbands", "keltner", "donchian", "supertrend",
                  "psar", "vwap", "rsi", "macd", "stoch", "stochrsi", "adx",
                  "atr", "cci", "williams_r", "mfi", "obv", "cmf", "aroon"}


def tool_get_indicator(name: str, interval: str = "5m", period: int = 0,
                       lookback_bars: int = 400, draw: bool = False,
                       source: str = "close", mult: float = 0,
                       fast: int = 0, slow: int = 0, signal: int = 0,
                       series_points: int = 0, anchor_time: str = "",
                       at: list | None = None, frm: str = "",
                       to: str = "", mark_points: bool = False,
                       connect: bool = False,
                       mark_levels: list | None = None,
                       remove: bool = False,
                       clear_marks: bool = False) -> dict:
    """One tool over the whole indicator registry.

    The model chooses the indicator, the period, the price column and the
    interval rather than picking from a handful of frozen presets — the point
    is flexibility, since which indicator answers a question is exactly the
    kind of judgement it is better placed to make than a lookup table here.
    Every result carries the formula that produced it, so the reply can state
    what was computed instead of asserting a number.
    """
    name = (name or "").lower().strip()
    if name not in indicators.SPECS:
        by_group: dict = {}
        for k, v in indicators.SPECS.items():
            by_group.setdefault(v["group"], []).append(k)
        return {"error": f"unknown indicator '{name}'",
                "available": by_group,
                "_note": ("Nothing was computed. Pick a name from this list — "
                          "and if the user asked for something genuinely absent "
                          "here, say it is not available rather than "
                          "substituting a different indicator for it.")}
    if source not in indicators.SOURCES:
        return {"error": f"unknown source '{source}'",
                "available": list(indicators.SOURCES)}
    if remove:
        # taking a pane OFF the chart is a scene op, not a computation —
        # nothing to fetch, nothing to derive
        _scene_add({"kind": "indicator_remove", "name": name,
                    "period": int(period or 0)})
        which = f"{name}({period})" if period else f"every {name} variant"
        return {"removed": name, **({"period": int(period)} if period else {}),
                "_note": (f"Removal of {which} sent to the chart. If it was "
                          "not on the chart nothing changes — say what was "
                          "removed, and re-add with draw=true if the user "
                          "wants a fresh one.")}
    if clear_marks:
        # take the MARKS off an indicator while leaving the indicator alone —
        # the pane-removal lever was the only one, so "remove the lines you
        # added on rsi" nuked the whole pane. Marks share the id prefix
        # IX<NAME>- (the trailing dash matters: IXAD- must not catch IXADX-).
        pane = (None if indicators.SPECS[name]["pane"] == "overlay" or not period
                else f"{name}@{int(period)}")
        _scene_add({"kind": "clear", "scope": "id_prefix",
                    "prefix": "IX" + name.upper() + "-",
                    **({"pane": pane} if pane else {}),
                    "owner": "get_indicator"})
        which = f"the {name}({period}) line" if period else f"every {name} line"
        return {"marks_cleared": name,
                "_note": (f"All levels, dots and connections previously marked "
                          f"on {which} are removed. The indicator itself is "
                          "still on the chart — this cleared only the marks. "
                          "Use remove=true to take the indicator off too.")}
    rows = _rows(interval, max(200, min(int(lookback_bars or 400), 1500)))
    if not rows:
        return {"error": f"no bars for interval {interval}"}
    # an aggregate over marked points or a window may reach further back than
    # the default page — deepen the data BEFORE computing, so the tool sees
    # what the question is actually about (bounded at 5000 bars)
    if at or frm:
        wanted = [t for t in ([_parse_ist(frm)] if frm else [])
                  + [_parse_ist(str(s)) for s in (at or [])[:20]] if t]
        if wanted and min(wanted) < rows[0][0]:
            deeper = _rows(interval, 5000)
            if deeper and deeper[0][0] < rows[0][0]:
                rows = deeper

    extra: dict = {}
    mult_ignored = False
    if mult:
        # only bbands/keltner/supertrend take a width multiplier; forwarding
        # it to the rest raised TypeError and burned the whole call
        if name in indicators.MULT_OK:
            extra["mult"] = float(mult)
        else:
            mult_ignored = True
    if name == "macd":
        extra.update({k: int(v) for k, v in
                      (("fast", fast), ("slow", slow), ("signal", signal)) if v})
    if name == "anchored_vwap":
        # anchoring is the whole point of this one: resolve the user's moment
        # to a bar index rather than silently anchoring at the window start
        t = _parse_ist(anchor_time) if anchor_time else None
        if anchor_time and t is None:
            return {"error": "could not read anchor_time",
                    "hint": "use the chart's format, e.g. '11 Jun 2026' or "
                            "'08 Jul 2026 15:25'"}
        if t is not None:
            idx = next((i for i, r in enumerate(rows) if r[0] >= t), None)
            if idx is None:
                return {"error": "anchor_time is after the last scanned bar",
                        "scanned": f"{_ist(rows[0][0])} → {_ist(rows[-1][0])} IST"}
            extra["anchor_index"] = idx

    try:
        res = indicators.compute(name, rows, period, source, **extra)
    except ValueError as exc:
        return {"error": str(exc)}

    wt = interval not in ("1d", "1w", "1mo")
    out: dict = {
        "indicator": name,
        "value": res["last"],
        "as_of": _ist(rows[-1][0], wt),
        "last_price": rows[-1][4],
        "spec": res["spec"],
    }
    if mult_ignored:
        out["mult_ignored"] = (
            f"'{name}' has no band-width multiplier, so mult was ignored — "
            f"it applies only to {', '.join(sorted(indicators.MULT_OK))}. "
            "The value above is the plain computation; do not describe it "
            "as widened or narrowed.")
    # A tail of the series, when asked for — enough to see a cross or a turn
    # without shipping hundreds of numbers nobody reads.
    k = max(0, min(int(series_points or 0), 240))
    if k:
        idx = list(range(max(0, len(rows) - k), len(rows)))
        out["series"] = {
            "t": [_ist(rows[i][0], wt) for i in idx],
            **{ln: [None if v[i] is None else round(v[i], 4) for i in idx]
               for ln, v in res["lines"].items()},
        }
    # ── values at marked points / over a window, aggregated SERVER-SIDE ──
    # "Average RSI at my three marked lows" must never be the model doing
    # arithmetic over a series tail — code owns the math. The primary line
    # (the first named one) is what gets aggregated, and the result says so.
    if at or frm or to:
        prim = next(iter(res["lines"]))
        line = res["lines"][prim]
        picked: list[tuple] = []          # (display_t, value)
        unread: list[str] = []
        outside: list[str] = []
        if at:
            iv_sec = INTRADAY_MIN.get(interval, 0) * 60 or 86400
            for s in list(at)[:20]:
                t = _parse_ist(str(s))
                if t is None:
                    unread.append(str(s))
                    continue
                j = next((i for i in range(len(rows) - 1, -1, -1)
                          if rows[i][0] <= t), None)
                if j is None or t >= rows[-1][0] + iv_sec:
                    outside.append(str(s))
                    continue
                picked.append((_ist(rows[j][0], wt), line[j], rows[j][0]))
        else:
            t0, t1 = _parse_ist(frm), _parse_ist(to)
            bad = [n for n, v, p in (("frm", frm, t0), ("to", to, t1))
                   if v and p is None]
            if bad:
                return {"error": f"could not read {' and '.join(bad)} as a date",
                        "hint": "use the chart's format, e.g. '08 Jul 2026' — "
                                "nothing was aggregated."}
            idxs = [i for i, r in enumerate(rows)
                    if (t0 is None or r[0] >= t0) and (t1 is None or r[0] <= t1)]
            picked = [(_ist(rows[i][0], wt), line[i], rows[i][0]) for i in idxs]
            if t0 is not None and rows and t0 < rows[0][0]:
                out["_window_note"] = (
                    f"even at this interval's deepest page the bars start "
                    f"{_ist(rows[0][0], wt)} — the aggregate covers from "
                    f"there, not from {frm}. If the question allows, re-call "
                    f"on a coarser interval (e.g. 1d) where the window is "
                    f"reachable, and say which interval the average is from.")
        if unread:
            return {"error": f"could not read these times: {', '.join(unread)}",
                    "hint": "use the chart's format, e.g. '08 Jul 2026 15:25' — "
                            "nothing was aggregated."}
        vals = [v for _, v, _ in picked if v is not None]
        agg: dict = {"line": prim, "points": len(picked),
                     "with_value": len(vals)}
        if vals:
            sv = sorted(vals)
            agg.update(mean=round(sum(vals) / len(vals), 4),
                       median=round(sv[len(sv) // 2], 4),
                       min=round(sv[0], 4), max=round(sv[-1], 4))
        else:
            agg["note"] = ("none of these bars has a value — the line has "
                           "not warmed up there. Say that; do not average "
                           "nothing.")
        if at and len(picked) <= 12:
            agg["at_values"] = [{"t": t, "value": None if v is None
                                 else round(v, 4)} for t, v, _ in picked]
        if outside:
            agg["outside_data"] = outside
            agg["outside_note"] = ("these times fall outside the loaded bars "
                                   "and were NOT included — name them if the "
                                   "user asked about them")
        out["aggregate"] = agg

    # ── marks ON the indicator itself ─────────────────────
    # A dot, a connecting line, a reference level — drawn on the indicator's
    # own pane at its own scale. Every y-value is read off the computed
    # series (a mark at a time lands where the line actually was); there is
    # no field here that accepts a model-invented coordinate.
    if mark_levels or ((mark_points or connect) and at):
        # composite pane key: the marks belong to THIS period's line, and
        # "rsi@26" lets the chart pick the right pane when the user has two
        # RSI variants open instead of dumping marks on whichever came first
        pane_key = ("price" if indicators.SPECS[name]["pane"] == "overlay"
                    else f"{name}@{res['spec']['period']}")
        mid = "IX" + name.upper()
        msrc = {"tool": "get_indicator",
                "method": "value read off the computed series",
                "interval": interval, "strength": "user-directed",
                "bars_scanned": len(rows)}
        marked: dict = {}
        valid = ([(d, v, ts) for d, v, ts in picked if v is not None][:12]
                 if (mark_points or connect) and at else [])
        if (mark_points or connect) and at and not valid:
            marked["points_note"] = ("none of those bars has a value (line "
                                     "not warmed up there) — nothing marked")
        if mark_points and valid:
            for i, (d, v, ts) in enumerate(valid):
                _scene_add({"kind": "point", "id": f"{mid}-p{i}", "link": mid,
                            "pane": pane_key, "role": "neutral",
                            "a": {"t": ts, "v": round(v, 4)},
                            "source": {**msrc, "first_touch": d,
                                       "last_touch": d}})
            marked["points_marked"] = len(valid)
        if connect:
            if len(valid) >= 2:
                _scene_add({"kind": "poly", "id": mid + "-c", "link": mid,
                            "pane": pane_key, "role": "neutral", "solid": True,
                            "pts": [{"t": ts, "v": round(v, 4)}
                                    for _, v, ts in valid],
                            "source": {**msrc, "first_touch": valid[0][0],
                                       "last_touch": valid[-1][0]}})
                marked["connected"] = len(valid)
            else:
                marked["connect_note"] = ("connecting needs at least two bars "
                                          "with values — nothing was drawn")
        lvls = []
        for lv in (mark_levels or [])[:4]:
            try:
                lvf = float(lv)
            except (TypeError, ValueError):
                continue
            _scene_add({"kind": "level", "id": f"{mid}-l{lvf:g}",
                        "price": lvf, "pane": pane_key, "role": "neutral",
                        "strength": "user-directed",
                        "label": f"{name} {lvf:g}",
                        "source": {**msrc, "first_touch": "—",
                                   "last_touch": "—"}})
            lvls.append(lvf)
        if lvls:
            marked["levels"] = lvls
        # the marks annotate a specific line — plot that same line (same
        # name, same period) so a mark never floats over a different series
        if name in _FE_RENDERABLE and (marked.get("points_marked")
                                       or marked.get("connected") or lvls):
            _scene_add({"kind": "indicator", "name": name,
                        "period": res["spec"]["period"],
                        "source": {"tool": "get_indicator",
                                   "interval": interval}})
        out["marked"] = marked
        out["_drawn_note"] = _drawn_ledger()
    if draw:
        # The chart owns the same formulas, so drawing is naming — but only
        # for what the FE can actually render. Claiming to have drawn
        # something it cannot show is worse than saying it is unavailable.
        if name in _FE_RENDERABLE:
            _scene_add({"kind": "indicator", "name": name, "period": res["spec"]["period"],
                        "source": {"tool": "get_indicator", "interval": interval}})
            out["drawn"] = True
        else:
            out["drawn"] = False
            out["draw_unavailable"] = (
                f"The chart cannot render {name} yet, so nothing was added to "
                f"it. The values above are still real — report them as computed "
                f"numbers and say plainly that it could not be plotted. Do not "
                f"claim it was drawn. Renderable today: "
                f"{', '.join(sorted(_FE_RENDERABLE))}.")
    if any(v is None for v in res["last"].values()):
        out["_null_note"] = (
            "A null line is not an error: it means that line has no value at "
            "the latest bar. Supertrend, for instance, shows only the band on "
            "the active side. Say which side is active rather than reporting "
            "both, and never fill a null with the last value it held.")
    out["_note"] = (
        "Quote spec.formula when the user asks what an indicator means or how "
        "it was computed — it is the exact definition used, including the "
        "smoothing, and conventions differ between platforms. Wilder smoothing "
        "(RSI, ATR, ADX) is k = 1/n and is NOT an EMA. Bollinger uses "
        "population standard deviation. Bounded oscillators carry spec.bounds; "
        "reading an RSI of 40 as 'oversold' when the conventional line is 30 is "
        "the kind of thing the bounds are there to prevent. These are "
        "descriptive measurements — never present a level crossing as a signal "
        "to act on.")
    return out


TOOLS = [
    {"type": "function", "name": "explain_move",
     "description": "The evidence pack for 'why did it move / what happened' over a date window, in ONE call: how abnormal the move is versus this stock's own history, how much of it the index accounts for (beta-expected vs residual), where inside each session it happened (overnight gap vs morning vs last hour, volume concentration), WHO ACTED (delivery % with own-history percentile, futures OI quadrant, bulk/block deals in the window), results dates nearby, levels crossed, patterns ending in the window, and how similar past moves resolved (with the base rate). Call this FIRST for any cause/why question about a move, drop, rally or spike — it usually answers it without further calls.",
     "parameters": {"type": "object", "properties": {
         "frm": {"type": "string", "description": "first session of the move, chart format e.g. '21 Jul 2026'; omit both dates for the latest session"},
         "to": {"type": "string", "description": "last session of the move; omit for a single day"}},
      "required": []}},
    {"type": "function", "name": "get_flows",
     "description": "Ownership and positioning series on their own: daily delivery % (share of traded quantity that actually changed hands, with its percentile vs this stock's own history), futures open interest with the standard quadrant read (long/short buildup, unwinding, covering), and bulk/block deals with client names. Use for direct questions like 'is the selling delivery-backed', 'are shorts building up', 'any big deals lately', 'how has delivery trended' — for explaining one specific move, explain_move already includes this.",
     "parameters": {"type": "object", "properties": {
         "frm": {"type": "string", "description": "window start, chart format e.g. '01 Jul 2026'"},
         "to": {"type": "string", "description": "window end; omit for one day"},
         "lookback_sessions": {"type": "integer", "description": "used when no dates given — last N sessions, default 10, max 60"}},
      "required": []}},
    {"type": "function", "name": "search_news",
     "description": "Dated outside events for a window — filings, analyst actions, sector/market/macro causes named by the press. When the question itself already demands outside causes ('why did it fall', 'what news moved it'), call this IN THE SAME ROUND as explain_move — batching the two saves a full inference hop, and the search covers company, sector and market angles either way. Call it only after explain_move when you genuinely cannot tell yet whether the move needs a cause at all. At most one search per turn. Returns events with dates and sources, never numbers.",
     "parameters": {"type": "object", "properties": {
         "frm": {"type": "string", "description": "window start, e.g. '21 Jul 2026'"},
         "to": {"type": "string", "description": "window end; omit for one day"},
         "focus": {"type": "string", "description": "optional angle, e.g. 'market-wide selloff cause' or 'company filings'"}},
      "required": ["frm"]}},
    {"type": "function", "name": "get_levels",
     "description": "Detect real support/resistance from pivot clustering, with touch counts, strength and dates. Each level carries its own track record: how many past touches held vs broke, and the median reaction that followed — use it to say whether a level has actually worked, not just how often price reached it. Use whenever asked about levels, support, resistance, or where price reacts. To put them ON the chart set draw=true (top few) or pass draw_ids after reviewing the candidates — you choose WHICH, the detector supplies every price.",
     "parameters": {"type": "object", "properties": {
         "interval": {"type": "string", "enum": ["5m", "15m", "30m", "1h", "1d", "1w"]},
         "lookback_bars": {"type": "integer", "description": "bars to scan, default 300"},
         "draw": {"type": "boolean", "description": "draw the strongest max_draw levels"},
         "draw_ids": {"type": "array", "items": {"type": "string"},
                      "description": "ids from the candidate list, e.g. ['L1365','L1337'], to draw exactly those"},
         "max_draw": {"type": "integer", "description": "default 3"},
         "draw_mode": {"type": "string", "enum": ["add", "replace", "clear"],
                       "description": "'replace' clears previously drawn levels first — use it to narrow or correct the chart. 'clear' erases every drawn level and draws nothing — the only way to wipe the chart."},
         "draw_as": {"type": "string", "enum": ["line", "zone"],
                     "description": "'zone' shades the level's real width (from the spread of its own pivots) instead of a single line — use it when talking about an area or band rather than a price"},
         "side": {"type": "string", "enum": ["support", "resistance", "both"],
                  "description": "restrict to levels BELOW price (support) or ABOVE it (resistance). Set it whenever the question names a side — 'where does it find support', 'what's overhead'. Default 'both' ranks by evidence alone, which can answer a support question with resistance levels."}},
         "required": ["interval"]}},
    {"type": "function", "name": "get_trendlines",
     "description": "Detect SLOPED trend lines fitted through real swing highs/lows, each requiring 3+ touches, with status intact/broken. Use for any diagonal structure — trendline, rising support, falling resistance, wedge/channel edges. Set draw=true to put them on the chart. A resistance line is fitted through swing HIGHS, a support line through swing LOWS — pass `side` when the question names one.",
     "parameters": {"type": "object", "properties": {
         "interval": {"type": "string", "enum": ["5m", "15m", "30m", "1h", "1d", "1w"]},
         "lookback_bars": {"type": "integer", "description": "bars to scan, default 300"},
         "draw": {"type": "boolean"},
         "draw_ids": {"type": "array", "items": {"type": "string"},
                      "description": "ids from the candidate list, e.g. ['TL1312-1287']"},
         "max_draw": {"type": "integer", "description": "default 2"},
         "draw_mode": {"type": "string", "enum": ["add", "replace", "clear"]},
         "side": {"type": "string", "enum": ["support", "resistance", "both"],
                  "description": "'resistance' = fitted through swing HIGHS (a descending line, the top of a channel, 'along the highs', 'the downtrend line'); 'support' = through swing LOWS ('along the lows', 'rising support'). Default 'both' picks whichever has the most touches, which will answer a highs question with a lows line. For a channel, call once per side."}},
         "required": ["interval"]}},
    {"type": "function", "name": "get_divergences",
     "description": "Find price/oscillator divergences (RSI or MACD) and, crucially, how often they actually resolved on this symbol in this window. Use when asked about divergence, momentum disagreement, or whether a move is losing steam. Drawing one marks both the price leg and the oscillator leg in its own pane.",
     "parameters": {"type": "object", "properties": {
         "indicator": {"type": "string", "enum": ["rsi", "macd"]},
         "interval": {"type": "string", "enum": ["5m", "15m", "30m", "1h", "1d", "1w"]},
         "lookback_bars": {"type": "integer", "description": "default 400"},
         "draw": {"type": "boolean"},
         "draw_ids": {"type": "array", "items": {"type": "string"}},
         "max_draw": {"type": "integer", "description": "default 1 — the most recent"},
         "draw_mode": {"type": "string", "enum": ["add", "replace", "clear"]}},
         "required": ["indicator", "interval"]}},
    {"type": "function", "name": "get_gaps",
     "description": "Find price gaps and, crucially, how often gaps have actually filled on this symbol in this window, with median bars-to-fill. Use whenever gaps come up, or when asked about unfilled gaps overhead/below. Set draw=true to shade them.",
     "parameters": {"type": "object", "properties": {
         "interval": {"type": "string", "enum": ["5m", "15m", "30m", "1h", "1d", "1w"]},
         "lookback_bars": {"type": "integer", "description": "default 400"},
         "only_open": {"type": "boolean", "description": "only gaps price has not returned to"},
         "draw": {"type": "boolean"},
         "draw_ids": {"type": "array", "items": {"type": "string"}},
         "max_draw": {"type": "integer", "description": "default 3"},
         "draw_mode": {"type": "string", "enum": ["add", "replace", "clear"]}},
         "required": ["interval"]}},
    {"type": "function", "name": "get_anchors",
     "description": "Referenceable points on the chart — swing highs/lows, window extremes, session open/close, gaps, and the 52-week high/low — each returned with the bars around it so you can judge what the point means. Use this when the user asks for something drawn that no detector produces (a range, a box, a line between two moments): get anchors, then compose with draw_shape. For the 52-week high or low ask kinds=['high_52w','low_52w']. To anchor at a SPECIFIC date the conversation has located (the day of the biggest fall, a particular high), pass at_times — each mints bar_high/bar_low anchors at that real bar (ids carry the date, e.g. T060126H/T060126L for 06 Jan 2026), drawable this whole turn. To box or bound a named period, pass frm/to — window_high/window_low then become that RANGE's extremes (date-carrying ids R<ddmmyy>H/L). You never type a coordinate.",
     "parameters": {"type": "object", "properties": {
         "interval": {"type": "string", "enum": ["1m", "5m", "15m", "30m", "1h", "1d", "1w"]},
         "lookback_bars": {"type": "integer", "description": "default 300"},
         "kinds": {"type": "array", "items": {"type": "string",
                   "enum": ["swing_high", "swing_low", "session_open", "session_close",
                            "window_high", "window_low", "gap", "high_52w", "low_52w"]},
                   "description": "omit for all kinds"},
         "at_times": {"type": "array", "items": {"type": "string"},
                      "description": "up to 4 dates/times (chart format, e.g. '06 Jan 2026') — mints bar_high/bar_low anchors at each, ids T1H/T1L, T2H/T2L…"},
         "frm": {"type": "string", "description": "range start — scopes window_high/window_low (and swings) to a named period"},
         "to": {"type": "string", "description": "range end"},
         "limit": {"type": "integer", "description": "default 12, max 30"}},
         "required": ["interval"]}},
    {"type": "function", "name": "draw_shape",
     "description": "Draw a shape by referencing anchor ids from get_anchors. Shapes: segment, ray, box, band, hline, vline, point, polyline, fib. Use for anything the user asks to mark that isn't a detected level/trendline/divergence — a range between two swings, a box around a consolidation, a fib retracement across a leg, a line from one moment to another. Giving a 1-anchor shape (hline/vline/point) SEVERAL ids draws one per anchor in a single call, each auto-labelled from its anchor kind — always do that for 'mark both/all of…' asks instead of one call per marker.",
     "parameters": {"type": "object", "properties": {
         "shape": {"type": "string", "enum": ["segment", "ray", "box", "band", "hline", "vline", "point", "polyline", "fib"],
                   "description": "'fib' draws a full retracement ladder across the leg between the two anchors — the FIRST anchor is the leg's start (100%), the second its end (0%)"},
         "anchor_ids": {"type": "array", "items": {"type": "string"},
                        "description": "ids from get_anchors, e.g. ['A1312','A1271']"},
         "interval": {"type": "string", "enum": ["1m", "5m", "15m", "30m", "1h", "1d", "1w"]},
         "lookback_bars": {"type": "integer", "description": "must match the get_anchors call"},
         "pane": {"type": "string", "description": "'price', or an indicator id like 'rsi'"},
         "label": {"type": "string", "description": "short caption drawn on the chart"},
         "role": {"type": "string", "enum": ["resistance", "support", "neutral"]},
         "draw_mode": {"type": "string", "enum": ["add", "clear"],
                       "description": "'clear' removes every shape previously drawn via draw_shape (other tools' drawings stay) — anchor_ids may be empty then"}},
         "required": ["shape", "anchor_ids", "interval"]}},
    {"type": "function", "name": "evaluate_line",
     "description": "Score a line the USER drew: how many swings touched it, how many held vs broke, where it projects now. Use whenever the user asks whether their own trendline is any good, or what its record is. ALWAYS pass drawing_id when the line is one the user drew — the chart context lists every drawing with its ref, and referencing it is checked whereas copying coordinates is not. The message may also name the drawing the user tagged; that ref is the subject. Endpoints are for a line the user described but has not drawn.",
     "parameters": {"type": "object", "properties": {
         "drawing_id": {"type": "string", "description": "ref of the user's drawing (e.g. 'D3') from the chart context — preferred over coordinates"},
         "p1_time": {"type": "string", "description": "IST 'YYYY-MM-DD HH:MM' of the first endpoint"},
         "p1_value": {"type": "number"},
         "p2_time": {"type": "string"},
         "p2_value": {"type": "number"},
         "interval": {"type": "string", "enum": ["1m", "5m", "15m", "30m", "1h", "1d", "1w"]},
         "lookback_bars": {"type": "integer", "description": "default 500"}},
         "required": ["interval"]}},
    {"type": "function", "name": "evaluate_fib",
     "description": "Score a fibonacci retracement. Returns TWO things: where this drawing's levels sit and whether price has reached them since the leg, AND the base rate for each ratio across every past swing leg on this symbol — how often the 0.618 (or 0.5, or 0.382) actually turned price — measured against a non-fibonacci control so the rate can be read honestly. Use whenever a fib retracement comes up: the user drew one, asked whether fibs work here, or asked what a ratio means on this chart. Pass the leg's two endpoints from the chart context's drawings list.",
     "parameters": {"type": "object", "properties": {
         "drawing_id": {"type": "string", "description": "ref of the user's fib drawing (e.g. 'D3') from the chart context — preferred over coordinates when they drew it"},
         "p1_time": {"type": "string", "description": "IST time of the leg's START, as the chart shows it, e.g. '08 Jul 2026 15:25'"},
         "p1_value": {"type": "number", "description": "price at the start of the leg (the 100% end)"},
         "p2_time": {"type": "string", "description": "IST time of the leg's END"},
         "p2_value": {"type": "number", "description": "price at the end of the leg (the 0% end)"},
         "interval": {"type": "string", "enum": ["5m", "15m", "30m", "1h", "1d", "1w"]},
         "lookback_bars": {"type": "integer", "description": "bars to scan for the base rate, default 600"}},
         "required": ["interval"]}},
    {"type": "function", "name": "get_patterns",
     "description": "Detect named formations on the chart: 34 candlestick patterns (engulfing, hammer, doji varieties incl dragonfly/gravestone/long-legged, morning/evening star, three soldiers/crows, harami, three inside/outside up/down, piercing, dark cloud, tweezers, kickers, belt holds, rising/falling three methods, abandoned baby…), 22 chart patterns (head and shoulders and its inverse, double and triple tops/bottoms, ascending/descending/symmetrical triangles, rising/falling wedges, rectangle, channel up/down, broadening, bull/bear flags and pennants, cup and handle, rounding bottom/top) and market structure (HH/HL/LH/LL with BOS and CHoCH). Call it BOTH ways: omit `kinds` to sweep everything for 'what patterns are on this chart', or set `kinds` to answer 'is there a head and shoulders / any bullish engulfing'. `kinds` takes exact snake_case ids — e.g. bullish_belt_hold, bearish_kicker, three_inside_up, rising_three_methods, triple_top, bull_pennant, cup_and_handle. Always use this rather than reading candles out of get_bars and judging them yourself — the thresholds here are explicit and come back with the result. Set draw=true to draw chart patterns as their actual geometry — a solid outline through the defining swing points with a tinted interior, a dashed neckline segment ending at the break bar, fitted wedge/triangle edges, flag pole and box — so describe them as drawn shapes, not as horizontal levels.",
     "parameters": {"type": "object", "properties": {
         "interval": {"type": "string", "enum": ["5m", "15m", "30m", "1h", "1d", "1w"]},
         "lookback_bars": {"type": "integer", "description": "bars to scan, default 300"},
         "kinds": {"type": "array", "items": {"type": "string"},
                   "description": "specific pattern names to look for, e.g. ['head_and_shoulders'] or ['bullish_engulfing','hammer']. Omit for a full sweep. An unknown name comes back with the full list rather than scanning."},
         "families": {"type": "array", "items": {"type": "string", "enum": ["candlestick", "chart", "structure"]},
                      "description": "restrict to whole families instead of naming patterns"},
         "limit": {"type": "integer", "description": "max instances per family, default 20"},
         "draw": {"type": "boolean", "description": "mark the top chart patterns"},
         "draw_ids": {"type": "array", "items": {"type": "string"},
                      "description": "ids from the chart_patterns list, to mark exactly those"},
         "draw_mode": {"type": "string", "enum": ["add", "replace", "clear"]}},
         "required": ["interval"]}},
    {"type": "function", "name": "evaluate_pattern",
     "description": "Historical reliability of ONE named pattern on this chart: every past instance, the forward move horizon_bars after each completion, the rate of moving in the pattern's textbook direction, and the unconditional base rate as control — the edge is pattern rate minus base rate. Use for 'does X actually work here / has that pattern type been reliable'. Works for candlestick kinds and swing shapes (double/triple top/bottom, head and shoulders, flags, pennants); live-edge fitted shapes (triangles, wedges, channels, rectangle, cup, rounding) have no instance history and it will say so. Never answer reliability questions from raw bars.",
     "parameters": {"type": "object", "properties": {
         "kind": {"type": "string", "description": "one exact snake_case pattern id, e.g. bullish_engulfing, triple_top, bull_flag"},
         "interval": {"type": "string", "enum": ["5m", "15m", "30m", "1h", "1d", "1w"]},
         "lookback_bars": {"type": "integer", "description": "history to mine, default 1000, max 2000"},
         "horizon_bars": {"type": "integer", "description": "forward window per instance, default 10"}},
         "required": ["kind", "interval"]}},
    {"type": "function", "name": "get_peers",
     "description": (
         "The company's industry classification (Moneycontrol) and its peer "
         "group within the 500-company universe. Use for 'who are the "
         "peers/competitors' and as the first step of any peer comparison — "
         "then pick the relevant few and call compare_symbols."),
     "parameters": {"type": "object", "properties": {
         "symbol": {"type": "string",
                    "description": "defaults to the chart's symbol"}},
         "required": []}},
    {"type": "function", "name": "compare_symbols",
     "description": (
         "Compare 2-8 symbols over a common window: return %, max drawdown, "
         "ATR volatility, avg turnover, return correlation, and the NIFTY 50 "
         "return over the same span. Symbols not yet stored locally download "
         "first (~6 s each). Use for any cross-company or company-vs-peers "
         "question; the chart's own symbol must be listed explicitly."),
     "parameters": {"type": "object", "properties": {
         "symbols": {"type": "array", "items": {"type": "string"}},
         "interval": {"type": "string", "enum": ["1d", "1w"]},
         "lookback_bars": {"type": "integer",
                           "description": "window length, default 250 (~1y of 1d)"}},
         "required": ["symbols"]}},
    {"type": "function", "name": "screen_universe",
     "description": (
         "Screen the WHOLE stored universe — every company, not the chart's "
         "symbol — on the end-of-day features in the filter enum, optionally "
         "narrowed to an industry or to names printing a given daily pattern. "
         "Compose any combination yourself: a filter is one feature, lt or gt, "
         "and a number; a band is two filters on the same feature. Use it for "
         "every 'which stocks / find me / how many companies' question about "
         "setups, criteria or structure across many names. Results are "
         "end-of-day and carry their own as-of date and universe size — quote "
         "both."),
     "parameters": {"type": "object", "properties": {
         "filters": {"type": "array", "description": "all must pass",
                     "items": {"type": "object", "properties": {
                         "feature": {"type": "string", "enum": list(SCREEN_FEATURES)},
                         "op": {"type": "string", "enum": list(SCREEN_OPS)},
                         "value": {"type": "number"}},
                         "required": ["feature", "op", "value"]}},
         "industry": {"type": "string", "description": "one industry; a miss returns the closest names"},
         "pattern": {"type": "string", "description": "require this daily pattern, e.g. bull_flag, bullish_engulfing"},
         "pattern_within": {"type": "integer", "description": "sessions the pattern may be old, default 5"},
         "sort": {"type": "string", "description": "feature to rank by; defaults to the first filter's"},
         "limit": {"type": "integer", "description": "rows returned, 1-50, default 15"}},
         "required": []}},
    {"type": "function", "name": "plan_position",
     "description": (
         "Draw or update the trade-plan overlay (entry/stop/targets) and return "
         "its risk arithmetic: R:R and breakeven hit-rate per target, position "
         "size from a risk budget, per-target P&L, stop distance in ATR(14)s, "
         "and the historical entry→target-vs-stop record. Expresses the USER'S "
         "stated idea — never invent a trade unprompted. Entry defaults to the "
         "last close. A new call replaces the plan; draw_mode=clear removes it. "
         "To size a position the user DREW, pass its ref as drawing_id."),
     "parameters": {"type": "object", "properties": {
         "entry": {"type": "number"}, "stop": {"type": "number"},
         "stop_atr": {"type": "number",
                      "description": "alt to stop: ATR(14) multiples from entry"},
         "targets": {"type": "array", "items": {"type": "number"},
                     "description": "up to 3 prices"},
         "targets_r": {"type": "array", "items": {"type": "number"},
                       "description": "alt to targets: R multiples, e.g. [1.5, 3]"},
         "split": {"type": "array", "items": {"type": "number"},
                   "description": "scale-out fraction per target, e.g. [0.5, 0.5]"},
         "qty": {"type": "integer"},
         "risk_amount": {"type": "number",
                         "description": "rupees to risk; qty is derived"},
         "capital": {"type": "number"},
         "risk_pct": {"type": "number",
                      "description": "with capital: risk_amount = capital × risk_pct/100"},
         "side": {"type": "string", "enum": ["long", "short"]},
         "drawing_id": {"type": "string"},
         "interval": {"type": "string",
                      "enum": ["1m", "5m", "15m", "30m", "1h", "1d", "1w"]},
         "draw_mode": {"type": "string", "enum": ["add", "clear"]}},
         "required": ["interval"]}},
    {"type": "function", "name": "evaluate_drawing",
     "description": "Score a zone, channel or planned position the USER drew, against what price actually did. Use whenever the user asks whether their own box/band/channel/trade-setup is any good, has been respected, or has a record. ALWAYS pass drawing_id when the shape is one the user drew — the chart context lists every drawing with its ref, and both the geometry AND the kind are then read from the chart instead of retyped. The message may also name the drawing the user tagged; that ref is the subject. A zone reports touches held vs broke PLUS how much of the time price closes inside it (a band price lives inside is the range, not a zone). A channel scores each edge separately plus containment. A position reports how often target came before stop from that entry, against the hit rate its risk:reward needs to break even. Do not answer these from raw bars — that is eyeballing, which is what this replaces.",
     "parameters": {"type": "object", "properties": {
         "drawing_id": {"type": "string", "description": "ref of the user's drawing (e.g. 'D3') from the chart context — preferred; kind and points are then read from the chart"},
         "kind": {"type": "string", "enum": ["zone", "channel", "position"]},
         "points": {"type": "array",
                    "description": "zone: the band's two edges (value only, time optional). channel: two points on one edge then one on the other, all with times. position: entry, then target, then stop (value only). Copy them from the chart context's drawings list.",
                    "items": {"type": "object", "properties": {
                        "t": {"type": "string", "description": "IST time as the chart shows it, e.g. '08 Jul 2026 15:25' — required for channel"},
                        "v": {"type": "number", "description": "price"}}}},
         "interval": {"type": "string", "enum": ["5m", "15m", "30m", "1h", "1d", "1w"]},
         "lookback_bars": {"type": "integer", "description": "default 600"}},
         "required": ["interval"]}},
    {"type": "function", "name": "get_results",
     "description": "Quarterly result (earnings) dates for this company, newest first, and optionally mark them on the chart with event icons. Use for 'when were the last results', 'when did Q1 report', 'mark earnings on the chart', or to locate a quarter before asking what price did around it. The date returned is the session the market could FIRST react to: an after-market announcement reacts the next day, and the field already accounts for that. These are past announcements only — there is no scheduled future date here, so never state one.",
     "parameters": {"type": "object", "properties": {
         "limit": {"type": "integer", "description": "how many recent quarters, default 8, max 40"},
         "interval": {"type": "string", "enum": ["5m", "15m", "30m", "1h", "1d", "1w"]},
         "draw": {"type": "boolean", "description": "mark them on the chart as event icons"},
         "draw_mode": {"type": "string", "enum": ["add", "replace", "clear"]}},
         "required": ["interval"]}},
    {"type": "function", "name": "evaluate_results",
     "description": "What price actually does around this company's results: the reaction day's gap and move, the run-up before, and the drift after — each against the unconditional base rate over the same window, so 'results move it 2%' can be read against what an ordinary day does. Use for 'how does it usually react to results', 'is there a run-up into earnings', 'what happens after results'. Absolute move is the reliable finding; direction usually is not.",
     "parameters": {"type": "object", "properties": {
         "horizon_bars": {"type": "integer", "description": "bars after the reaction day to measure drift, default 5, max 30"},
         "interval": {"type": "string", "enum": ["1d", "1w"]},
         "lookback_bars": {"type": "integer", "description": "history to scan, default 3000 (covers every stored event that has price data)"}},
         "required": ["interval"]}},
    {"type": "function", "name": "get_bars",
     "description": "Actual OHLCV bars for a window. Use for any specific bar, date, or price the chart summary doesn't contain.",
     "parameters": {"type": "object", "properties": {
         "interval": {"type": "string", "enum": ["1m", "5m", "15m", "30m", "1h", "1d", "1w", "1mo"]},
         "frm": {"type": "string", "description": "IST start 'YYYY-MM-DD HH:MM' (optional)"},
         "to": {"type": "string", "description": "IST end (optional)"},
         "limit": {"type": "integer", "description": "max 80, default 40"}},
         "required": ["interval"]}},
    {"type": "function", "name": "get_indicator",
     "description": (
         "Compute any indicator at any period, interval and price source, and optionally add it to the chart. "
         "Trend: sma, ema, wma, hma, dema, supertrend, psar, adx (with +DI/-DI), aroon. "
         "Momentum: rsi, macd, stoch, stochrsi, cci, williams_r, roc. "
         "Volatility: bbands (with percent_b and bandwidth), keltner, donchian, atr. "
         "Volume: vwap, anchored_vwap, obv, ad, cmf, mfi. "
         "Use this rather than pulling bars and doing the arithmetic yourself — the result carries the exact "
         "formula and smoothing used, which differ between platforms. Reach for adx when the question is whether "
         "price is TRENDING or just ranging, bbands bandwidth for volatility compression, and the volume family "
         "when asked whether volume confirms a move. remove=true takes an indicator OFF the chart (period to "
         "target one variant, omit for all of that name) — removing and re-adding with new settings in the same "
         "round is how 'replace my RSI with…' is done."),
     "parameters": {"type": "object", "properties": {
         "name": {"type": "string",
                  "enum": ["sma", "ema", "wma", "hma", "dema", "bbands", "keltner", "donchian",
                           "vwap", "anchored_vwap", "supertrend", "psar", "rsi", "macd",
                           "stoch", "stochrsi", "adx", "cci", "williams_r", "roc", "atr",
                           "obv", "ad", "cmf", "mfi", "aroon"]},
         "interval": {"type": "string", "enum": ["1m", "5m", "15m", "30m", "1h", "1d", "1w"]},
         "period": {"type": "integer", "description": "omit for the indicator's conventional default"},
         "source": {"type": "string", "enum": ["close", "open", "high", "low", "hl2", "hlc3", "ohlc4"],
                    "description": "price column, default close"},
         "mult": {"type": "number", "description": "band width multiplier for bbands / keltner / supertrend"},
         "anchor_time": {"type": "string", "description": "for anchored_vwap: the bar to anchor at, in the chart's format e.g. '11 Jun 2026'"},
         "series_points": {"type": "integer", "description": "return the last N points of the series too (max 240) — use it to see a cross or a turn, or to LOCATE when a cross happened (then mark it via get_anchors at_times)"},
         "at": {"type": "array", "items": {"type": "string"},
                "description": "IST times (chart format, e.g. '08 Jul 2026 15:25', max 20) — returns the indicator's value at each plus mean/median/min/max, computed server-side. Use for 'average RSI at my marked points' by copying the times from the chart context's drawings; never average values by hand"},
         "frm": {"type": "string", "description": "aggregate over a window instead: IST start"},
         "to": {"type": "string", "description": "IST end of the aggregate window"},
         "mark_points": {"type": "boolean", "description": "draw a dot ON the indicator at each `at` time, at its computed value — for marking crosses, extremes, tests of a level"},
         "connect": {"type": "boolean", "description": "connect the `at` values with a line on the indicator's pane — an indicator trendline whose y-values come from the series, never guessed"},
         "mark_levels": {"type": "array", "items": {"type": "number"}, "description": "horizontal reference lines on the indicator's own scale, e.g. [70,30] on rsi or [0] on macd. When marking an indicator the user already has plotted, pass that line's period (it is in the chart context, e.g. 'RSI 70') so the marks sit on that exact line"},
         "draw": {"type": "boolean", "description": "add it to the user's chart"},
         "remove": {"type": "boolean", "description": "remove this indicator AND its pane from the chart — period targets one variant, omitted removes every variant of the name"},
         "clear_marks": {"type": "boolean", "description": "remove only the marks previously added ON this indicator (reference lines, dots, connections) while keeping the indicator itself — use this, not remove, when the user wants the lines gone but the indicator kept"}},
         "required": ["name", "interval"]}},
]

_DISPATCH = {"get_levels": tool_get_levels, "get_bars": tool_get_bars,
             "get_indicator": tool_get_indicator,
             "get_trendlines": tool_get_trendlines,
             "get_divergences": tool_get_divergences,
             "get_anchors": tool_get_anchors,
             "get_gaps": tool_get_gaps,
             "draw_shape": tool_draw_shape,
             "evaluate_line": tool_evaluate_line,
             "evaluate_fib": tool_evaluate_fib,
             "evaluate_drawing": tool_evaluate_drawing,
             "plan_position": tool_plan_position,
             "get_peers": tool_get_peers,
             "compare_symbols": tool_compare_symbols,
             "screen_universe": tool_screen_universe,
             "get_patterns": tool_get_patterns,
             "evaluate_pattern": tool_evaluate_pattern,
             "get_results": tool_get_results,
             "evaluate_results": tool_evaluate_results,
             "explain_move": tool_explain_move,
             "search_news": tool_search_news,
             "get_flows": tool_get_flows}


def run_tool(name: str, args: dict) -> dict:
    fn = _DISPATCH.get(name)
    if not fn:
        return {"error": f"unknown tool {name}"}
    try:
        out = fn(**args)
    except Exception as exc:  # noqa: BLE001 — a bad call must not kill the turn
        logging.warning("charto tool %s failed: %s", name, exc)
        return {"error": f"{name} failed: {exc}"}
    # Stamp WHICH drawing a score belongs to, here rather than in each tool,
    # so no return path can omit it. A score the reply cannot name is a score
    # the user cannot check against the shape they meant.
    ref = args.get("drawing_id") if isinstance(args, dict) else None
    if ref and isinstance(out, dict) and "error" not in out:
        got = _drawing_get(ref)
        d = got.get("ok") or {}
        out["scored_drawing"] = {"ref": d.get("ref") or ref, "type": d.get("type")}
        out["_scored_note"] = (
            f"These numbers are for the user's {d.get('type', 'drawing')} "
            f"{d.get('ref') or ref} — name it in the reply so they know which "
            f"shape was scored.")
    # A forming bar reads like any other bar once it is inside a tool result.
    # Say so here, once, rather than teaching every tool to caveat itself.
    st = _LIVE.get(_sym())
    form = st["form"] if st else None
    if (form and isinstance(out, dict) and "error" not in out
            and ("bars" in out or "as_of" in out or "interval" in out)):
        out["_live_note"] = (
            f"The last bar is still FORMING (as of {_hm_ist(form[0])} IST) — "
            f"treat its values as provisional, not a closed candle.")
    return out


def _n(v) -> str:
    """Indian-grouped number, trailing zeros trimmed."""
    if v is None:
        return "—"
    s = f"{v:,.2f}".rstrip("0").rstrip(".")
    return s or "0"


# Guidance, not a post-processor. The reply renders as markdown in a narrow
# side pane, so the model is told the shape of the surface it is writing into
# and picks the structure itself. Nothing here rewrites its output — when the
# reply comes back badly shaped, this block is what gets edited.
FORMAT_RULES = """\
## The surface you're writing into
Your reply renders as markdown in a resizable pane beside the chart. The text
column runs roughly 45 to 90 characters wide depending on how the user has
sized it — that shapes line and table width, not how much you write. Answer at
whatever length the question deserves. Headings, bullets, emphasis and pipe
tables all render; pick whatever shape fits. The one thing a narrow column
punishes is repeated figures buried in prose — the same fields across several
dates, bars or symbols read far better as a compact table (numbers
right-aligned with `|---:|`)."""

# The causal contract (~120 tokens). A rail, not a procedure: it says where
# quantities and causes each come from and what an honest "why" answer owes,
# and leaves every judgement — whether to search, what the evidence means —
# to the model.
CAUSAL_RULES = """\
## Explaining a move
For any why-did-it-move question, call explain_move — one call returns the
anatomy, the index split and the local evidence. When the question itself
already asks for causes or news of a NAMED move — a fall, rally, crash or
spike the user asserts happened — call search_news in the SAME round (one
batched round is a whole inference hop cheaper than two). When the user has
not named a move ("why did it move on X?" could be a flat day), call
explain_move alone and judge abnormality first: a move inside the stock's
normal range
needs none, and "no clear catalyst" is a complete, correct answer — most
days have none. search_news (at most once per turn) supplies only dated
events; every quantity comes from tools, and a stale headline never
overrides a tool. Say plainly how much was the market and how much the
stock. A cause must fit the anatomy: overnight news does not explain a
mid-session move. State behavioural readings (who was likely buying or
selling) as inference from a named observable, never as known motive."""


def build_context_block(ctx: dict | None) -> str:
    """Render the chart-state envelope into a compact system block (~250 tok).

    Every number here was computed by code (FE chart state) — the model's job
    is to quote and interpret, never to derive. The contract lines at the end
    are the honest-boundary rule: say what you can't see instead of guessing.
    """
    if not ctx:
        return ""
    stub = ("## Chart the user is viewing\n"
            "The chart has not finished loading — you cannot see it yet. "
            "Say so if asked about it.")
    if ctx.get("status") == "loading":
        return stub
    try:
        return _render_context(ctx)
    except Exception as exc:  # noqa: BLE001 — never break the reply on a bad envelope
        logging.warning("charto: malformed chart context (%s)", exc)
        return stub


def _render_context(ctx: dict) -> str:
    v, w, lb = ctx["view"], ctx["window"], ctx["last_bar"]
    L = [
        "## Chart the user is viewing",
        f"{ctx['symbol']} · {ctx['exchange']} · {ctx['interval']} · {ctx['source']}",
        f"Visible: {v['from']} → {v['to']} IST · {v['bars_visible']} bars on screen "
        f"· {v['bars_loaded']:,} loaded · history back to {v['history_from']}",
        f"Last bar {lb['t']} — O {_n(lb['o'])}  H {_n(lb['h'])}  L {_n(lb['l'])}  "
        f"C {_n(lb['c'])}  V {lb['v']:,}",
        f"Visible window: {_n(w['open'])} → {_n(w['close'])} ({w['change_pct']:+.2f}%) "
        f"· high {_n(w['high']['p'])} ({w['high']['t']}) "
        f"· low {_n(w['low']['p'])} ({w['low']['t']}) "
        f"· avg vol {w['avg_volume']:,}",
    ]
    cls = _classification_row(str(ctx.get("symbol") or ""))
    if cls:
        L.insert(2, f"{cls[0]} · industry: {cls[1]} (Moneycontrol classification)")
    _st = _LIVE.get(str(ctx.get("symbol") or ""))
    _form = _st["form"] if _st else None
    if _form:
        L.insert(2, f"live · forming bar {_hm_ist(_form[0])} IST")
    if ctx.get("session"):
        s = ctx["session"]
        L.append(f"Session {s['date']}: open {_n(s['open'])} → {_n(s['last'])} "
                 f"({s['change_pct']:+.2f}%) · high {_n(s['high'])} · low {_n(s['low'])}")
    L.append("Trajectory (close, ~20-pt downsample — shape only): "
             + ", ".join(_n(x) for x in w["trajectory"]))

    if ctx.get("indicators"):
        parts = []
        for i in ctx["indicators"]:
            at = "" if i["at_window_start"] is None else f" (window start {_n(i['at_window_start'])})"
            parts.append(f"{i['label']} = {_n(i['now'])}{at}")
        L.append("Indicators on chart: " + " · ".join(parts))
    else:
        L.append("Indicators on chart: none")

    if ctx.get("pins"):
        for p in ctx["pins"]:
            L.append(
                f"Pinned bar {p['t']} — O {_n(p['o'])}  H {_n(p['h'])}  "
                f"L {_n(p['l'])}  C {_n(p['c'])}  V {p['v']:,}"
            )
        L.append("The user clicked the pinned bar(s) above: treat them as the "
                 "subject of the question when it says 'here', 'this', 'that "
                 "candle' or names no time. Two pins means the range between them.")

    if ctx.get("drawings"):
        parts = []
        for d in ctx["drawings"]:
            pts = " → ".join(f"{p['t']} @{_n(p['p'])}" for p in d["pts"])
            tag = " (selected)" if d.get("selected") else ""
            txt = f' "{d["text"]}"' if d.get("text") else ""
            # values on an indicator pane are that indicator's units, not ₹
            on = f" on {d['on']}" if d.get("on") else ""
            parts.append(f"[{d.get('ref') or d['id']}] {d['type']}{on} {pts}{txt}{tag}")
        more = ctx.get("drawings_omitted")
        L.append("User's own drawings: " + " · ".join(parts)
                 + (f" · (+{more} more)" if more else ""))
        L.append("The bracketed code is that drawing's ref. To score one, pass "
                 "it as drawing_id to evaluate_line / evaluate_fib / "
                 "evaluate_drawing — never retype its coordinates, and never "
                 "score a drawing the user did not ask about.")
        if any(d.get("on") for d in ctx["drawings"]):
            L.append("Drawings marked 'on <indicator>' sit in that indicator's "
                     "pane: their values are that indicator's units (an RSI "
                     "level, a MACD value), never rupees.")
    else:
        L.append("User's own drawings: none")

    if ctx.get("chat_drawings"):
        parts = []
        for d in ctx["chat_drawings"]:
            k = d["kind"]
            g = (f"@{_n(d['price'])}" if k == "level"
                 else f"{_n(d['lo'])}–{_n(d['hi'])}" if k == "zone"
                 else f"{d['p1']['t']} @{_n(d['p1']['p'])} → {d['p2']['t']} "
                      f"@{_n(d['p2']['p'])}" if k in ("segment", "fib")
                 else f"{d.get('side')} entry {_n(d['entry'])} stop "
                      f"{_n(d['stop'])} targets "
                      f"{'/'.join(_n(t) for t in d['targets'])}"
                      + (f" qty {d['qty']}" if d.get("qty") else "")
                 if k == "position" else "")
            on = f" on {d['on']}" if d.get("on") else ""
            adj = " (USER-ADJUSTED)" if d.get("adjusted") else ""
            parts.append(f"[{d['id']}] {k}{on} {g}{adj}")
        L.append("Drawn by chat, still on the chart: " + " · ".join(parts))
        L.append("These geometries are CURRENT — the user can drag chat "
                 "drawings, and USER-ADJUSTED marks one they moved: its values "
                 "are the user's revision, which supersedes whatever a tool "
                 "drew earlier. Address one by passing its bracketed id as "
                 "drawing_id; a moved plan re-prices via plan_position with "
                 "drawing_id alone (its targets and sizing carry over).")

    L.append(
        "\nThese facts describe the visible chart. For anything they don't contain "
        "— a specific bar or date, a level, an indicator not listed — call a tool; "
        "never guess and never estimate. The trajectory points describe shape only: "
        "never quote one as a level, zone, or target. Support and resistance come "
        "only from get_levels: quote its prices with their touch counts, and if it "
        "returns nothing say so rather than naming a price yourself. Every number "
        "you state must come from these facts or a tool result (arithmetic on them "
        "is fine — say when you're doing it). Describe structure rather than "
        "prescribing trades: no buy/sell calls, and never assume a position or "
        "direction the user hasn't stated. If asked for a target or stop, give "
        "the levels and what would invalidate them, and say plainly that this is "
        "analysis, not advice. Be concise and concrete."
    )
    return "\n".join(L)


def _post_responses(wire: list[dict], allow_tools: bool = True) -> dict:
    payload = {
        "model": LLM_DEPLOYMENT,
        "input": wire,
        "tools": TOOLS,
        "tool_choice": "auto" if allow_tools else "none",
        "max_output_tokens": 2000,
        "reasoning": {"effort": LLM_EFFORT},
        "service_tier": LLM_SERVICE_TIER,
    }
    req = urllib.request.Request(
        f"{AZURE_ENDPOINT}/responses",
        data=json.dumps(payload).encode(),
        headers={"api-key": AZURE_KEY, "Content-Type": "application/json"},
        method="POST",
    )
    # macOS system python ships no CA bundle — use certifi's when available
    # (present in the pivot venv; run the server with .venv/bin/python).
    with urllib.request.urlopen(req, timeout=120, context=_ssl_ctx()) as resp:
        return json.loads(resp.read())


def _ssl_ctx():
    import ssl
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def _post_responses_stream(wire: list[dict], allow_tools: bool = True):
    """Same call, server-sent events. Yields parsed Responses-API events.

    Only the TEXT is worth streaming: tool calls arrive as complete items and
    mean nothing half-built. So the loop below streams every round, but only
    a round that produces prose shows anything — which is exactly the last
    one. The user sees the answer as it is written instead of after the whole
    tool chain has finished.
    """
    payload = {
        "model": LLM_DEPLOYMENT,
        "input": wire,
        "tools": TOOLS,
        "tool_choice": "auto" if allow_tools else "none",
        "max_output_tokens": 2000,
        "reasoning": {"effort": LLM_EFFORT},
        "service_tier": LLM_SERVICE_TIER,
        "stream": True,
    }
    req = urllib.request.Request(
        f"{AZURE_ENDPOINT}/responses",
        data=json.dumps(payload).encode(),
        headers={"api-key": AZURE_KEY, "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=180, context=_ssl_ctx()) as resp:
        for raw in resp:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            body = line[5:].strip()
            if not body or body == "[DONE]":
                continue
            try:
                yield json.loads(body)
            except json.JSONDecodeError:
                continue


_MAX_TOOL_ROUNDS = 3  # bounds latency; 1 round answers almost everything


def _wire_messages(messages: list[dict]) -> list[dict]:
    """History → Responses-API input items, screenshots included honestly.

    A user message may carry `image` (a data-URI chart screenshot). Only the
    NEWEST one goes to the model — re-shipping every past screenshot on every
    turn would grow input cost without bound — and an older message that had
    one says so in text, so the model never half-remembers an image it can no
    longer see."""
    last_img = max((i for i, m in enumerate(messages) if m.get("image")),
                   default=None)
    out: list[dict] = []
    for i, m in enumerate(messages):
        role = m.get("role", "user")
        txt = str(m.get("content", ""))
        # a tagged drawing IS the subject of that message — state it as a
        # fact of the turn, so "is this any good?" has an unambiguous referent
        # instead of the model guessing which shape "this" means
        tag = m.get("drawing")
        if tag and tag.get("ref"):
            txt = (f"[the user tagged drawing {tag['ref']} "
                   f"({tag.get('label') or tag.get('type') or 'drawing'}) — "
                   f"this drawing is what the message is about; score it by "
                   f"passing drawing_id={tag['ref']}]\n" + txt)
        img = m.get("image") if i == last_img else None
        if img and len(img) > 3_000_000:
            img = None
            txt += "\n[attached screenshot was too large to send — say so]"
        elif m.get("image") and i != last_img:
            txt += "\n[a chart screenshot was attached here; only the newest screenshot stays in context]"
        if img:
            out.append({"role": role, "content": [
                {"type": "input_text", "text": txt},
                {"type": "input_image", "image_url": img}]})
        else:
            out.append({"role": role, "content": txt})
    return out


def llm_chat(messages: list[dict], context: dict | None = None) -> dict:
    """Responses-API call with the tool loop.

    Wire shapes mirror backend/llm/openai_client.py: a tool call arrives as an
    `output[]` item {type:"function_call", call_id, name, arguments}; the result
    goes back as {type:"function_call_output", call_id, output}. The context
    envelope is rebuilt each turn and never accumulated.
    """
    if not AZURE_ENDPOINT or not AZURE_KEY:
        return {"error": "Azure creds not found in pivot/.env"}
    # Format rules ride along even with chart context switched off — the pane
    # is just as narrow either way. They go in the preview too, so "inspect
    # context sent" stays an honest record of everything the model was told.
    block = "\n\n".join(x for x in (build_context_block(context), FORMAT_RULES, CAUSAL_RULES) if x)
    wire: list[dict] = []
    if block:
        wire.append({"role": "system", "content": block})
    wire += _wire_messages(messages)

    _scene_reset()
    _drawings_set(context)   # tools can now resolve a drawing by ref
    tool_trace: list[dict] = []
    scene_patch: list[dict] = []
    tok_in = tok_out = 0
    for _round in range(_MAX_TOOL_ROUNDS):
        # On the final round the tools are withdrawn, so the model must answer
        # from what it already fetched. Running out of rounds used to surface a
        # dead-end apology on top of perfectly good tool results.
        data = _post_responses(wire, allow_tools=_round < _MAX_TOOL_ROUNDS - 1)
        u = data.get("usage", {})
        tok_in += u.get("input_tokens") or 0
        tok_out += u.get("output_tokens") or 0

        calls, text_parts = [], []
        for item in data.get("output", []):
            t = item.get("type")
            if t == "function_call":
                calls.append(item)
            elif t == "message":
                for c in item.get("content", []):
                    if c.get("type") == "output_text":
                        text_parts.append(c.get("text", ""))

        if not calls:
            return {
                "text": "".join(text_parts) or "(empty reply)",
                "usage": {"input_tokens": tok_in, "output_tokens": tok_out},
                "context_preview": block,
                "tools_used": tool_trace,
                "scene_patch": scene_patch,
            }

        # execute every call this round, then feed results back
        for call in calls:
            try:
                args = call.get("arguments") or "{}"
                args = json.loads(args) if isinstance(args, str) else args
            except json.JSONDecodeError:
                args = {}
            result = run_tool(call.get("name", ""), args)
            scene_patch.extend(_scene_take())
            tool_trace.append({"name": call.get("name"), "args": args,
                               "ok": "error" not in result})
            wire.append({"type": "function_call", "call_id": call.get("call_id"),
                         "name": call.get("name"), "arguments": call.get("arguments")})
            wire.append({"type": "function_call_output", "call_id": call.get("call_id"),
                         "output": json.dumps(result, default=str)})

    return {"text": "I couldn't finish that lookup — try narrowing the question.",
            "usage": {"input_tokens": tok_in, "output_tokens": tok_out},
            "context_preview": block, "tools_used": tool_trace}
def llm_chat_stream(messages: list[dict], context: dict | None = None):
    """The same tool loop, yielding events instead of returning a result.

    Shares every rule with llm_chat — the rebuilt-not-accumulated envelope, the
    round budget, and withdrawing the tools on the final round so running out
    of rounds cannot surface a dead-end apology on top of good tool results.

    Yielded events:
      {"type":"tool",  name, ok}    a tool finished — progress while the user waits
      {"type":"delta", text}        a piece of the answer
      {"type":"done",  ...}         the same payload llm_chat returns
    """
    if not AZURE_ENDPOINT or not AZURE_KEY:
        yield {"type": "done", "error": "Azure creds not found in pivot/.env"}
        return
    block = "\n\n".join(x for x in (build_context_block(context), FORMAT_RULES, CAUSAL_RULES) if x)
    wire: list[dict] = []
    if block:
        wire.append({"role": "system", "content": block})
    wire += _wire_messages(messages)

    _scene_reset()
    _drawings_set(context)   # tools can now resolve a drawing by ref
    tool_trace: list[dict] = []
    scene_patch: list[dict] = []
    tok_in = tok_out = 0

    for _round in range(_MAX_TOOL_ROUNDS):
        calls: list[dict] = []
        text_parts: list[str] = []
        by_id: dict = {}
        try:
            for ev in _post_responses_stream(wire, allow_tools=_round < _MAX_TOOL_ROUNDS - 1):
                t = ev.get("type", "")
                if t == "response.output_text.delta":
                    d = ev.get("delta") or ""
                    if d:
                        text_parts.append(d)
                        yield {"type": "delta", "text": d}
                elif t == "response.output_item.done":
                    item = ev.get("item") or {}
                    if item.get("type") == "function_call":
                        by_id[item.get("id") or len(by_id)] = item
                elif t in ("response.completed", "response.incomplete"):
                    r = ev.get("response") or {}
                    u = r.get("usage") or {}
                    tok_in += u.get("input_tokens") or 0
                    tok_out += u.get("output_tokens") or 0
                    # authoritative item list — the deltas above are only text
                    for item in r.get("output", []):
                        if item.get("type") == "function_call":
                            by_id[item.get("id") or len(by_id)] = item
                elif t == "error":
                    yield {"type": "done", "error": str(ev.get("message") or "stream error")}
                    return
        except Exception as exc:  # noqa: BLE001 — a broken stream must not hang the client
            logging.warning("charto stream failed: %s", exc)
            yield {"type": "done", "error": f"stream failed: {exc}"}
            return

        calls = list(by_id.values())
        if not calls:
            yield {"type": "done",
                   "text": "".join(text_parts) or "(empty reply)",
                   "usage": {"input_tokens": tok_in, "output_tokens": tok_out},
                   "context_preview": block,
                   "tools_used": tool_trace,
                   "scene_patch": scene_patch}
            return

        for call in calls:
            try:
                args = call.get("arguments") or "{}"
                args = json.loads(args) if isinstance(args, str) else args
            except json.JSONDecodeError:
                args = {}
            result = run_tool(call.get("name", ""), args)
            scene_patch.extend(_scene_take())
            ok = "error" not in result
            tool_trace.append({"name": call.get("name"), "args": args, "ok": ok})
            # tell the client immediately: a tool landing is the only progress
            # signal there is during a multi-round turn
            yield {"type": "tool", "name": call.get("name"), "ok": ok}
            wire.append({"type": "function_call", "call_id": call.get("call_id"),
                         "name": call.get("name"), "arguments": call.get("arguments")})
            wire.append({"type": "function_call_output", "call_id": call.get("call_id"),
                         "output": json.dumps(result, default=str)})

    yield {"type": "done",
           "text": "I couldn't finish that lookup — try narrowing the question.",
           "usage": {"input_tokens": tok_in, "output_tokens": tok_out},
           "context_preview": block, "tools_used": tool_trace,
           "scene_patch": scene_patch}


IST_OFF = 19800  # +05:30
SESSION_OPEN_MIN = 9 * 60 + 15  # 09:15 IST, minutes past midnight

INTRADAY_MIN = {"1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30, "1h": 60}

_con = sqlite3.connect(DB_PATH, check_same_thread=False)
_daily_cache: dict[str, list[list]] = {}   # symbol -> daily bars (ascending)


def _ist_day(ts: int) -> int:
    return (ts + IST_OFF) // 86400


def _bucket_stamp(ts: int, minutes: int) -> tuple[tuple[int, int], int]:
    """(day, bucket) identity and the stamped bar-open ts a 1-min row falls in.

    The single source of bucket arithmetic: the historical resampler and the
    live bar builder both call it, so a forming bar can never land on a
    different stamp than the same minute would get after it is closed.
    """
    ist = ts + IST_OFF
    day = ist // 86400
    mod = (ist % 86400) // 60
    bucket = max(0, mod - SESSION_OPEN_MIN) // minutes
    return (day, bucket), day * 86400 + (SESSION_OPEN_MIN + bucket * minutes) * 60 - IST_OFF


def _resample_intraday(rows: list[tuple], minutes: int) -> list[list]:
    """rows = ascending (ts,o,h,l,c,v) 1-min bars → bucketed bars.

    Buckets anchor to each session's minute-of-day relative to 09:15 IST so
    every trading day starts a fresh, aligned bucket (evening specials like
    Muhurat land in later buckets of the same day — still consistent).
    """
    out: list[list] = []
    cur_key = None
    for ts, o, h, l, c, v in rows:
        key, bts = _bucket_stamp(ts, minutes)
        if key != cur_key:
            out.append([bts, o, h, l, c, v])
            cur_key = key
        else:
            b = out[-1]
            b[2] = max(b[2], h)
            b[3] = min(b[3], l)
            b[4] = c
            b[5] += v
    return out


def _fold_daily(rows: list[tuple]) -> list[list]:
    """ascending 1-min rows → one bar per IST trade date."""
    out: list[list] = []
    cur_day = None
    for ts, o, h, l, c, v in rows:
        day = _ist_day(ts)
        if day != cur_day:
            out.append([day * 86400 - IST_OFF, o, h, l, c, v])
            cur_day = day
        else:
            b = out[-1]
            b[2] = max(b[2], h)
            b[3] = min(b[3], l)
            b[4] = c
            b[5] += v
    return out


def _daily(symbol: str) -> list[list]:
    cached = _daily_cache.get(symbol)   # one atomic read — the tick thread pops
    if cached is not None:
        return cached
    out = _fold_daily(_con.execute(
        "SELECT ts,o,h,l,c,v FROM bars WHERE symbol=? ORDER BY ts", (symbol,)
    ).fetchall())
    _daily_cache[symbol] = out
    return out


def _weekly_or_monthly(daily: list[list], mode: str) -> list[list]:
    out: list[list] = []
    cur_key = None
    for ts, o, h, l, c, v in daily:
        d = datetime.fromtimestamp(ts + IST_OFF, tz=timezone.utc)
        key = (d.isocalendar().year, d.isocalendar().week) if mode == "1w" \
            else (d.year, d.month)
        if key != cur_key:
            out.append([ts, o, h, l, c, v])
            cur_key = key
        else:
            b = out[-1]
            b[2] = max(b[2], h)
            b[3] = min(b[3], l)
            b[4] = c
            b[5] += v
    return out


# ── live tick engine ──────────────────────────────────────────────
# Ticks enter through one seam, _live_on_tick. Today the only driver is the
# replay thread below re-feeding stored 1-min rows; a Kite websocket would
# call the same function with the same four arguments. The engine keeps one
# FORMING 1-min bar per symbol and writes a minute to SQLite the moment it
# closes; get_bars merges the forming bar, so every tool and /bars goes live
# without knowing this file has a tick loop. Indicators stay pure functions
# of rows — nothing here stores an indicator value.
#
# horizon != None means replay: stored rows at or after it are the future
# being re-played and stay hidden until the clock reaches them.
_LIVE: dict[str, dict] = {}
_LIVE_GUARD = threading.Lock()
# Writes go on their own connection (WAL is on) so a tick can never land
# mid-read on the shared reader.
_live_writer = sqlite3.connect(DB_PATH, check_same_thread=False)
_LIVE_MIN_GAP = 0.25   # ≤4 pushes/sec/symbol, minute closes always push


def _hm_ist(ts: int) -> str:
    t = datetime.fromtimestamp(ts + IST_OFF, tz=timezone.utc)
    return f"{t.hour:02d}:{t.minute:02d}"


def _live_state(sym: str) -> dict:
    with _LIVE_GUARD:
        st = _LIVE.get(sym)
        if st is None:
            st = _LIVE[sym] = {"lock": threading.Lock(), "form": None,
                               "horizon": None, "subs": [], "replaying": False,
                               "replay_stop": False, "thread": None,
                               "last_push": 0.0}
        return st


def _live_view(sym: str) -> tuple[list | None, int | None] | None:
    """(forming bar, horizon) — None when the symbol is idle, which is what
    keeps get_bars on exactly the pre-live code path."""
    st = _LIVE.get(sym)
    if st is None:
        return None
    with st["lock"]:
        f, hz = st["form"], st["horizon"]
        if f is None and hz is None:
            return None
        return (list(f) if f is not None else None, hz)


def _live_snapshot(sym: str, form: list) -> dict:
    """The current FORMING bar of every interval, for the SSE payload.

    Bounded by construction: only the current session's closed minutes are
    read, and each interval is folded by the same bucket math the historical
    resampler uses.
    """
    day0 = _ist_day(form[0]) * 86400 - IST_OFF
    mins = _live_writer.execute(
        "SELECT ts,o,h,l,c,v FROM bars WHERE symbol=? AND ts>=? AND ts<? "
        "ORDER BY ts", (sym, day0, form[0])).fetchall()
    tail = tuple(form)
    out = {}
    for name, m in INTRADAY_MIN.items():
        _, bts = _bucket_stamp(form[0], m)
        b = _resample_intraday([r for r in mins if r[0] >= bts] + [tail], m)[-1]
        out[name] = {"t": b[0], "o": b[1], "h": b[2], "l": b[3],
                     "c": b[4], "v": b[5]}
    d = _fold_daily(mins + [tail])[-1]
    out["1d"] = {"t": d[0], "o": d[1], "h": d[2], "l": d[3], "c": d[4], "v": d[5]}
    return out


def _live_push(sym: str, form: list, closed: bool) -> None:
    st = _LIVE.get(sym)
    if st is None:
        return
    with st["lock"]:
        subs = list(st["subs"])
    if not subs:
        return
    ev = {"type": "bar", "symbol": sym, "closed_1m": closed,
          "bars": _live_snapshot(sym, form)}
    for q in subs:
        try:
            q.put_nowait(ev)
        except queue.Full:            # a subscriber that cannot keep up is gone
            q.dead = True             # its SSE loop closes the socket, so the
            with st["lock"]:          # browser reconnects instead of freezing
                if q in st["subs"]:
                    st["subs"].remove(q)


def _live_on_tick(sym: str, ts: int, price: float, vol: int) -> None:
    """The one seam every tick source calls. ts = the tick's epoch second."""
    if ((ts + IST_OFF) % 86400) // 60 < SESSION_OPEN_MIN:
        return    # pre-open prints must not be persisted as the 09:15 candle
    _, bts = _bucket_stamp(ts, 1)
    st = _live_state(sym)
    closed_bar = None
    with st["lock"]:
        f = st["form"]
        if f is not None and f[0] == bts:
            f[2] = max(f[2], price)
            f[3] = min(f[3], price)
            f[4] = price
            f[5] += vol
        else:
            if f is not None:
                _live_writer.execute(
                    "INSERT OR REPLACE INTO bars VALUES (?,?,?,?,?,?,?)",
                    (sym, f[0], f[1], f[2], f[3], f[4], int(f[5])))
                _live_writer.commit()
                _daily_cache.pop(sym, None)
                closed_bar = list(f)
            st["form"] = [bts, price, price, price, price, vol]
            if st["horizon"] is not None:
                st["horizon"] = bts        # exclusive: the minute being formed
        snap = list(st["form"])
        now = time.monotonic()
        throttled = closed_bar is None and now - st["last_push"] < _LIVE_MIN_GAP
        if not throttled:
            st["last_push"] = now
    if throttled:
        return
    if closed_bar is not None:
        # the minute's FINAL state must always reach the chart — a throttled
        # drop here would leave a permanently wrong candle on screen
        _live_push(sym, closed_bar, True)
    _live_push(sym, snap, False)


def _merge_form_intraday(rows: list, form: list) -> None:
    """Append (or replace) the forming minute onto ascending 1-min rows."""
    if rows and rows[-1][0] == form[0]:
        rows[-1] = tuple(form)
    elif not rows or form[0] > rows[-1][0]:
        rows.append(tuple(form))


def _merge_form_daily(daily: list[list], form: list) -> list[list]:
    """A copy of `daily` with the forming minute folded in — never mutates the
    cached list, which outlives any replay."""
    out = list(daily)
    if out and _ist_day(out[-1][0]) == _ist_day(form[0]):
        ts, o, h, l, c, v = out[-1]
        out[-1] = [ts, o, max(h, form[2]), min(l, form[3]), form[4], v + form[5]]
    elif not out or form[0] > out[-1][0]:
        out.append([_ist_day(form[0]) * 86400 - IST_OFF,
                    form[1], form[2], form[3], form[4], form[5]])
    return out


def _replay_run(sym: str, day0: int, rows: list[tuple], speed: float) -> None:
    """Re-feed one stored session as ticks. Each 1-min row becomes four ticks
    (o,h,l,c) — the writes it triggers are INSERT OR REPLACE of the identical
    row, so a replay leaves the store exactly as it found it."""
    st = _live_state(sym)
    step = 60.0 / speed / 4
    try:
        for ts, o, h, l, c, v in rows:
            part = int(v or 0) // 4
            for i, price in enumerate((o, h, l, c)):
                if st["replay_stop"]:
                    return
                _live_on_tick(sym, ts, price,
                              int(v or 0) - 3 * part if i == 3 else part)
                time.sleep(step)
    except Exception as exc:  # noqa: BLE001 — a dead driver must not wedge state
        logging.warning("charto replay %s failed: %s", sym, exc)
    finally:
        with st["lock"]:
            f = st["form"]
            if not st["replay_stop"]:
                if f is not None:
                    _live_writer.execute(
                        "INSERT OR REPLACE INTO bars VALUES (?,?,?,?,?,?,?)",
                        (sym, f[0], f[1], f[2], f[3], f[4], int(f[5])))
                    _live_writer.commit()
                    _daily_cache.pop(sym, None)
                st["form"] = None
                # the replayed rows are all back in the store, so plain history
                # is correct — a lingering horizon would silently clip every
                # later session from every tool with no note saying so
                st["horizon"] = None
            else:
                st["form"] = None
                st["horizon"] = None
            st["replaying"] = False


def _replay(sym: str, date: str | None, speed: float, stop: bool) -> tuple[int, dict]:
    st = _live_state(sym)
    if stop:
        st["replay_stop"] = True
        th = st["thread"]
        if th is not None and th.is_alive():
            th.join(timeout=3)
        with st["lock"]:
            st["form"] = None
            st["horizon"] = None
            st["replaying"] = False
        return 200, {"replaying": None, "symbol": sym}
    if not speed or speed <= 0:
        return 400, {"error": "speed must be > 0"}
    if date:
        try:
            d = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            return 400, {"error": f"bad date {date} — want YYYY-MM-DD"}
        day = int(d.timestamp()) // 86400
    else:
        last = _con.execute("SELECT MAX(ts) FROM bars WHERE symbol=?",
                            (sym,)).fetchone()[0]
        if last is None:
            return 404, {"error": f"no bars for {sym}"}
        day = _ist_day(last)
    day0 = day * 86400 - IST_OFF
    rows = _con.execute(
        "SELECT ts,o,h,l,c,v FROM bars WHERE symbol=? AND ts>=? AND ts<? "
        "ORDER BY ts", (sym, day0, day0 + 86400)).fetchall()
    if not rows:
        return 404, {"error": f"no bars for {sym} on {_iso_day(day0)}"}
    with st["lock"]:
        if st["replaying"]:   # checked under the lock — two starts cannot race
            return 409, {"error": f"{sym} is already replaying"}
        st["form"] = None
        st["horizon"] = day0
        st["replaying"] = True
        st["replay_stop"] = False
        st["thread"] = threading.Thread(
            target=_replay_run, args=(sym, day0, rows, speed), daemon=True)
    st["thread"].start()
    return 200, {"replaying": sym, "date": _iso_day(day0),
                 "bars": len(rows), "speed": speed}


def get_bars(symbol: str, interval: str, to: int | None, limit: int) -> dict:
    live = _live_view(symbol)
    form, horizon = live if live else (None, None)
    if interval in INTRADAY_MIN:
        mins = INTRADAY_MIN[interval]
        raw_needed = limit * mins + 400  # slack for session boundaries
        upper = to if horizon is None else (
            horizon if to is None else min(to, horizon))
        if upper:
            rows = _con.execute(
                "SELECT ts,o,h,l,c,v FROM bars WHERE symbol=? AND ts<? "
                "ORDER BY ts DESC LIMIT ?", (symbol, upper, raw_needed)
            ).fetchall()
        else:
            rows = _con.execute(
                "SELECT ts,o,h,l,c,v FROM bars WHERE symbol=? "
                "ORDER BY ts DESC LIMIT ?", (symbol, raw_needed)
            ).fetchall()
        rows.reverse()
        if form is not None and (to is None or form[0] < to):
            _merge_form_intraday(rows, form)
        bars = _resample_intraday(rows, mins)[-limit:]
        has_more = bool(rows) and _con.execute(
            "SELECT 1 FROM bars WHERE symbol=? AND ts<? LIMIT 1",
            (symbol, rows[0][0])).fetchone() is not None
    else:
        daily = _daily(symbol) if horizon is None else _fold_daily(_con.execute(
            "SELECT ts,o,h,l,c,v FROM bars WHERE symbol=? AND ts<? ORDER BY ts",
            (symbol, horizon)).fetchall())
        if form is not None:
            daily = _merge_form_daily(daily, form)
        series = daily if interval == "1d" \
            else _weekly_or_monthly(daily, interval)
        if to:
            series = [b for b in series if b[0] < to]
        bars = series[-limit:]
        has_more = len(series) > len(bars)

    return {
        "symbol": symbol, "interval": interval,
        "bars": [
            {"t": b[0], "o": b[1], "h": b[2], "l": b[3], "c": b[4], "v": b[5]}
            for b in bars
        ],
        "has_more": has_more,
        "earliest": bars[0][0] if bars else None,
        "latest": bars[-1][0] if bars else None,
    }


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_stream(self, messages: list, context: dict | None) -> None:
        """SSE. No Content-Length and no buffering — the whole point is that
        the first token reaches the screen before the turn is finished."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("Connection", "close")
        self.send_header("X-Accel-Buffering", "no")   # in case a proxy is ever added
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        try:
            for ev in llm_chat_stream(messages, context):
                self.wfile.write(f"data: {json.dumps(ev, default=str)}\n\n".encode())
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass                                       # user navigated away mid-answer
        except Exception as exc:  # noqa: BLE001
            logging.warning("charto sse failed: %s", exc)
            try:
                self.wfile.write(
                    f"data: {json.dumps({'type': 'done', 'error': str(exc)})}\n\n".encode())
                self.wfile.flush()
            except Exception:  # noqa: BLE001
                pass

    def _send_live(self, symbol: str) -> None:
        """SSE of forming bars. One queue per subscriber; a client that stops
        reading is dropped rather than back-pressuring the tick loop."""
        st = _live_state(symbol)
        q: queue.Queue = queue.Queue(maxsize=64)
        q.dead = False   # set by _live_push on eviction; ends this loop
        try:
            with st["lock"]:
                st["subs"].append(q)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache, no-transform")
            self.send_header("Connection", "close")
            self.send_header("X-Accel-Buffering", "no")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            while not q.dead:
                try:
                    ev = q.get(timeout=15)
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                else:
                    self.wfile.write(
                        f"data: {json.dumps(ev, default=str)}\n\n".encode())
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as exc:  # noqa: BLE001
            logging.warning("charto live sse failed: %s", exc)
        finally:
            with st["lock"]:
                if q in st["subs"]:
                    st["subs"].remove(q)

    def do_GET(self) -> None:  # noqa: N802
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        symbol = q.get("symbol", "RELIANCE").upper()
        _req.symbol = symbol
        try:
            if u.path == "/symbols":
                have = {r[0] for r in _con.execute(
                    "SELECT DISTINCT symbol FROM bars")}
                return self._send(200, {"symbols": _known_symbols(),
                                        "hydrated": sorted(have)})
            if u.path == "/bars":
                interval = q.get("interval", "5m")
                if interval not in (*INTRADAY_MIN, "1d", "1w", "1mo"):
                    return self._send(400, {"error": f"bad interval {interval}"})
                err = _ensure_symbol(symbol)
                if err:
                    return self._send(404, err)
                to = int(q["to"]) if q.get("to") else None
                limit = min(int(q.get("limit", 3000)), 20000)
                return self._send(200, get_bars(symbol, interval, to, limit))
            if u.path == "/indicators":
                # the catalogue the chart builds its menu from — one list, so
                # the menu and the model can never disagree about what exists
                return self._send(200, {"indicators": [
                    {"name": k, "period": v["period"], "pane": v["pane"],
                     "group": v["group"], "formula": v["formula"],
                     "lines": _INDICATOR_LINES.get(k, ["value"]),
                     **({"bounds": list(v["bounds"])} if "bounds" in v else {})}
                    for k, v in sorted(indicators.SPECS.items())]})
            if u.path == "/indicator":
                name = q.get("name", "")
                if name not in indicators.SPECS:
                    return self._send(400, {"error": f"unknown indicator {name}"})
                interval = q.get("interval", "1d")
                limit = min(int(q.get("limit", 3000)), 20000)
                rows = _rows(interval, limit)
                if not rows:
                    return self._send(400, {"error": "no bars"})
                extra = {}
                if q.get("mult"):
                    extra["mult"] = float(q["mult"])
                if q.get("anchor_index"):
                    extra["anchor_index"] = int(q["anchor_index"])
                try:
                    res = indicators.compute(name, rows, int(q.get("period", 0)),
                                             q.get("source", "close"), **extra)
                except ValueError as exc:
                    return self._send(400, {"error": str(exc)})
                # emitted as {time, value} pairs, nulls dropped — the chart
                # series API wants gaps absent rather than null-valued
                return self._send(200, {
                    "name": name, "spec": res["spec"],
                    "lines": {ln: [{"time": rows[i][0], "value": round(v, 6)}
                                   for i, v in enumerate(series) if v is not None]
                              for ln, series in res["lines"].items()},
                })
            if u.path == "/replay":
                err = _ensure_symbol(symbol)
                if err:
                    return self._send(404, err)
                code, payload = _replay(
                    symbol, q.get("date"), float(q.get("speed", 300) or 300),
                    q.get("stop") in ("1", "true", "yes"))
                return self._send(code, payload)
            if u.path == "/stream":
                err = _ensure_symbol(symbol)
                if err:
                    return self._send(404, err)
                return self._send_live(symbol)
            if u.path == "/meta":
                n, lo, hi = _con.execute(
                    "SELECT COUNT(*),MIN(ts),MAX(ts) FROM bars WHERE symbol=?",
                    (symbol,)).fetchone()
                return self._send(200, {
                    "symbol": symbol, "count": n, "earliest": lo, "latest": hi})
            return self._send(404, {"error": "not found"})
        except Exception as exc:  # noqa: BLE001
            return self._send(500, {"error": str(exc)})

    def do_OPTIONS(self) -> None:  # noqa: N802 — CORS preflight for POST /chat
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_POST(self) -> None:  # noqa: N802
        u = urlparse(self.path)
        if u.path != "/chat":
            return self._send(404, {"error": "not found"})
        try:
            n = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(n) or b"{}")
            messages = body.get("messages") or []
            if not isinstance(messages, list) or not messages:
                return self._send(400, {"error": "messages[] required"})
            sym = str((body.get("context") or {}).get("symbol")
                      or "RELIANCE").upper()
            _req.symbol = sym
            err = _ensure_symbol(sym)
            if err:
                return self._send(400, err)
            if body.get("stream"):
                return self._send_stream(messages, body.get("context"))
            return self._send(200, llm_chat(messages, body.get("context")))
        except Exception as exc:  # noqa: BLE001
            return self._send(500, {"error": str(exc)})

    def log_message(self, fmt: str, *args) -> None:  # quiet
        pass


if __name__ == "__main__":
    print(f"charto dataserver on :{PORT} (db={DB_PATH.name})")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
