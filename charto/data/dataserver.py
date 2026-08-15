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

import hashlib
import hmac
import json
import logging
import queue
import re
import secrets
import sqlite3
import sys
from os import environ
import threading
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

# BEFORE any sibling that does `import dataserver`. Served as a script this
# module is named __main__, so that name resolves by loading this file a
# SECOND time — a separate module object with its own _req, its own _drawings,
# its own connections. The boot block aliases the name for exactly this reason
# and says so at length, but it runs at the END of the module body, which is
# far too late for a sibling imported partway down it: journal.py was imported
# ~1,800 lines earlier and got the phantom copy, so every drawing the request
# had resolved was invisible to it and `from_drawing` could not find a plan
# that was plainly on the chart. The alias belongs here, where nothing has
# had a chance to import anything yet. setdefault, so a normal `import
# dataserver` (tests, tooling) is left exactly as it was.
sys.modules.setdefault("dataserver", sys.modules[__name__])

import indicators   # sibling module: the indicator registry
import mark   # sibling module: symbolic addresses → real chart coordinates
import patterns   # sibling module: candlestick / chart-pattern / structure detectors

DB_PATH = Path(__file__).parent / "charto_bars.db"
PORT = int(environ.get("CHARTO_PORT") or 5174)

# ── Azure LLM proxy config (same Foundry endpoint Pivot chat uses) ──
# Read from pivot/.env so the key never lands in browser-served files.
_ENV_PATH = Path(__file__).resolve().parents[2] / "pivot" / ".env"
# Deployment name is overridable via CHARTO_LLM_MODEL in pivot/.env. It is
# deliberately NOT pivot's LLM_MODEL: the backend runs its own deployment
# (gpt-5.4-mini) and Charto should not drag it onto a different model.
LLM_DEPLOYMENT_DEFAULT = "gpt-5.6-luna"
LLM_EFFORT_DEFAULT = "medium"
# Azure priority processing — premium-billed, lower/steadier latency. The
# response echoes the tier actually served; verify there, not here.
LLM_SERVICE_TIER = "priority"


def _env_values(*keys: str) -> dict[str, str]:
    """Read bare KEY=value lines out of pivot/.env (no python-dotenv here)."""
    found = {k: "" for k in keys}
    try:
        for line in _ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("#"):
                continue
            for k in keys:
                if line.startswith(f"{k}="):
                    found[k] = line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return found


def _load_azure_creds() -> tuple[str, str]:
    v = _env_values("AZURE_OPENAI_ENDPOINT", "AZURE_KEY")
    return v["AZURE_OPENAI_ENDPOINT"].rstrip("/"), v["AZURE_KEY"]


AZURE_ENDPOINT, AZURE_KEY = _load_azure_creds()
LLM_DEPLOYMENT = _env_values("CHARTO_LLM_MODEL")["CHARTO_LLM_MODEL"] or LLM_DEPLOYMENT_DEFAULT
# Overridable the same way, so an A/B between efforts is a restart rather than
# an edit — a benchmark needing a code change between its arms is one nobody
# re-runs. Env wins over .env so a single run can be pinned from the shell.
LLM_EFFORT = (environ.get("CHARTO_LLM_EFFORT")
              or _env_values("CHARTO_LLM_EFFORT")["CHARTO_LLM_EFFORT"]
              or LLM_EFFORT_DEFAULT)


def _creds_error() -> str:
    """Name the key that is actually missing, not just 'creds not found'."""
    missing = [n for n, v in (("AZURE_OPENAI_ENDPOINT", AZURE_ENDPOINT),
                              ("AZURE_KEY", AZURE_KEY)) if not v]
    return (f"chat disabled — {' and '.join(missing)} empty in pivot/.env "
            f"(model {LLM_DEPLOYMENT}); fill them in and restart the dataserver")


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


_bar_symbols_cache: tuple[float, set[str]] | None = None
_BAR_SYMBOLS_TTL = 300.0


def _symbols_with_bars() -> set[str]:
    """Which symbols have 1-minute bars — WITHOUT scanning `bars`.

    `SELECT DISTINCT symbol FROM bars` is a full table scan: SQLite has no
    index skip-scan, so both DISTINCT and GROUP BY plan as SCAN. Measured on
    the 413M-row universe store that is 124.71s, which made /symbols time out
    and left the chart stuck on "Loading..." — the symbol picker asks for this
    on every page load. It was survivable at 118M rows locally and is not at
    413M, which is exactly the kind of thing only deploying finds.

    Ask a small table instead: sync_state carries one row per symbol (0.00s),
    bars_1d GROUP BY is 0.06s over 1.1M rows. The scan stays as a last resort
    so a store with neither table still answers, slowly, rather than failing.
    """
    global _bar_symbols_cache
    now = time.monotonic()
    if _bar_symbols_cache and now - _bar_symbols_cache[0] < _BAR_SYMBOLS_TTL:
        return _bar_symbols_cache[1]
    out: set[str] = set()
    for sql in ("SELECT symbol FROM sync_state",
                "SELECT symbol FROM bars_1d GROUP BY symbol",
                "SELECT symbol FROM bars GROUP BY symbol"):
        try:
            out = {r[0] for r in _con.execute(sql)}
        except sqlite3.Error:
            continue
        if out:
            break
    _bar_symbols_cache = (now, out)
    return out


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
    # Every kind that puts a visible mark on the chart, not just the four the
    # detectors happened to emit. A ledger that says "describe these and only
    # these" while silently omitting the boxes, bands and vlines in the same
    # call is worse than no ledger: it instructs the model to under-report.
    if kind in ("level", "zone", "segment", "vprofile", "box", "vline",
                "vband", "poly", "point", "candle", "label", "markers"):
        _scene.drawn.append(annotation.get("label") or annotation.get("id"))
    elif kind in ("clear", "clear_levels"):
        _scene.drawn = []


def _drawn_ledger() -> str:
    led = [x for x in (getattr(_scene, "drawn", None) or []) if x]
    if not led:
        return ""
    # One repeated shape is ONE thing to the user — "the first hour of each
    # of the last five sessions", not five entries. Collapsing here keeps a
    # session map from filling the ledger with its own copies.
    seen: dict[str, int] = {}
    for x in led:
        seen[x] = seen.get(x, 0) + 1
    names = [f"{k} (×{n})" if n > 1 else k for k, n in seen.items()]
    return ("Everything now on the user's chart: " + "; ".join(names)
            + ". Describe these and only these as drawn — anything you drew "
              "earlier in this turn is still there, so include it.")


def _scene_take() -> list[dict]:
    items = getattr(_scene, "items", [])
    _scene.items = []
    return items


# A SEPARATE channel from the scene patch, deliberately. A scene op's `pane`
# means an INDICATOR pane (the RSI strip under the price), and scene.apply()
# will open one on demand for any op that names it. A chart pane is a different
# thing entirely, so reusing that key would have had a request for a second
# CHART quietly open an indicator strip instead. View ops move the workspace —
# which charts exist, what each is showing — and nothing else.
def _view_add(op: dict) -> None:
    if not hasattr(_scene, "views"):
        _scene.views = []
    _scene.views.append(op)


def _view_take() -> list[dict]:
    ops = getattr(_scene, "views", [])
    _scene.views = []
    return ops


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

def _tz_off() -> int:
    """Offset for the symbol being served. `_ist` and `_parse_ist` MUST agree
    on it — the model reads one and writes the other back, so a mismatch is
    the same class of P0 as a rejected timestamp format."""
    return session_for(_sym())[1]


def _tzl() -> str:
    """Clock label for the symbol being served, so a UTC-anchored crypto bar
    is never stamped 'IST' next to a chart axis that reads UTC."""
    return "IST" if _tz_off() else "UTC"


def _ist(ts: int, with_time: bool = True) -> str:
    d = datetime.fromtimestamp(ts + _tz_off(), tz=timezone.utc)
    return d.strftime("%d %b %Y %H:%M") if with_time else d.strftime("%d %b %Y")


def _parse_ist(s: str | None) -> int | None:
    """Tolerant local-clock timestamp parse → epoch seconds.

    The inverse of `_ist`, and it reads the same `_tz_off()` — for a crypto
    symbol both sides run on UTC, so a timestamp the model copied out of a
    tool result still round-trips to the second.

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
            return int(d.timestamp()) - _tz_off()
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
            "window": f"{_ist(rows[0][0], wt)} → {_ist(rows[-1][0], wt)} {_tzl()}",
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
                       "window": f"{_ist(rows[0][0], wt)} → {_ist(rows[-1][0], wt)} {_tzl()}",
                       "method": "swing pivots (±5 bars), window extremes, session "
                                 "open/close, ATR-sized gaps"},
        "_note": ("Compose shapes from these ids with draw_shape — never type a "
                  "price or a time yourself. 'around' is the neighbourhood of "
                  "each anchor: use it to judge whether the point means "
                  "anything before you draw it. An anchor is a location, not a "
                  "claim; nothing here says a level will hold."),
    }


_SHAPES = {"segment": 2, "ray": 2, "box": 2, "band": 2, "hline": 1,
           "vline": 1, "polyline": 3, "point": 1, "fib": 2, "candle": 1}


def _candle_hl(ts: int, interval: str, lookback_bars: int) -> dict:
    """The high and low of the bar at `ts`, for a candle mark.

    Resolved HERE rather than left to the client. The client can fall back to
    the bar it has loaded, but only while the chart is on the interval the
    anchor came from — send the real extremes and the mark stays correct when
    the user switches to 5m and no bar carries this stamp at all.
    """
    for r in _rows(interval, max(60, min(int(lookback_bars or 300), 1500))):
        if r[0] == ts:
            return {"hi": r[2], "lo": r[3]}
    return {}


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
            elif shape == "candle":
                one.update(kind="candle", t1=a["_ts"], t2=a["_ts"],
                           **_candle_hl(a["_ts"], interval, lookback_bars))
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
    elif shape == "candle":
        ann.update(kind="candle", t1=pts[0]["t"], t2=pts[0]["t"],
                   **_candle_hl(pts[0]["t"], interval, lookback_bars))
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


def tool_mark(shapes: list | None = None, interval: str = "1d",
              lookback_bars: int = 300, pane: str = "price",
              draw_mode: str = "add") -> dict:
    """Draw anything, addressed rather than detected.

    draw_shape can only compose points a DETECTOR produced. That covers
    structure and nothing else — there is no detector for "the first hour of
    every session", for the day a result landed, for 1,300, for the stretch
    between two dates. Those are things the model legitimately knows and
    could not say.

    This is the general capability: the model writes an address, `mark.py`
    resolves it against the real bars, and every shape the chart can render
    becomes reachable without a tool per idea. See mark.py for the address
    grammar — the one rule that matters here is that no coordinate arrives
    ready-made, so a magnitude slip is caught rather than drawn.
    """
    if str(draw_mode or "add").lower() == "clear":
        _scene_add({"kind": "clear", "scope": "all", "owner": "mark"})
        return {"cleared": True,
                "_note": "Every mark is removed. Other tools' drawings stay."}
    if not shapes:
        return {"error": "no shapes given",
                "_note": "Pass shapes:[{shape, at|from|to, label}]."}

    rows = _rows(interval, max(60, min(int(lookback_bars or 300), 1500)))
    if not rows:
        return {"error": f"no bars for interval {interval}"}
    out = mark.build(shapes, rows, {"tz_off": _tz_off(),
                                    "parse_time": _parse_ist, "fmt_time": _ist},
                     pane=str(pane or "price"))
    for item in out["items"]:
        _scene_add(item)

    res: dict = {"drawn": out["report"], "interval": interval,
                 "window": f"{_ist(rows[0][0])} → {_ist(rows[-1][0])} {_tzl()}"}
    if out["errors"]:
        res["not_drawn"] = out["errors"]
    if not out["items"]:
        res["_note"] = ("Nothing was drawn. Read not_drawn, fix the address, "
                        "and say plainly what could not be placed.")
        return res
    # The provenance line. These marks came from the conversation, not from a
    # detector — so they are placements, and a placement has no record.
    res["_note"] = (
        "Drawn. Every coordinate above is what actually landed on the chart, "
        "resolved from your address against the real bars — quote these, not "
        "what you asked for, and name what each mark is FOR. These are marks "
        "you placed, not structure anything detected: never attach a hit rate, "
        "a hold record or a strength to them."
        + (" Some shapes failed — read not_drawn and say which."
           if out["errors"] else ""))
    res["ledger"] = _drawn_ledger()
    return res


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
        return {"error": "could not parse p1_time / p2_time "
                         f"(expect 'YYYY-MM-DD HH:MM' {_tzl()})"}
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
                "scanned": f"{_ist(rows[0][0], wt)} → {_ist(rows[-1][0], wt)} {_tzl()}"}
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
            "window": f"{_ist(rows[0][0], wt)} → {_ist(rows[-1][0], wt)} {_tzl()}"}

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
                       draw_mode: str = "add", basis: str = "") -> dict:
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

    # A plan is about what happens NEXT.
    #
    # This overlay used to start 40 bars in the PAST and end at the last bar,
    # so the risk and reward boxes were painted over price that has already
    # printed — the one stretch of chart the plan can say nothing about. It
    # read as though the trade had been running all along, and it hid the
    # candles it was drawn over for no gain.
    #
    # It now starts AT the entry bar and projects forward into the blank chart
    # on the right. The width is the same horizon the history above was
    # measured over, so the box covers exactly the window those win/loss
    # numbers came from — bounded, so it stays near the viewport.
    #
    # The step is the last bar gap, not a median, because that is the number
    # the client extrapolates with: matching it makes the box land exactly
    # `ahead` bars past the last one, weekend gap or not.
    step = rows[-1][0] - rows[-2][0] if len(rows) > 1 else 86400
    ahead = max(6, min(int(p["horizon_bars"]), 30))
    t0 = rows[-1][0]
    _scene_add({"kind": "position", "id": "plan", "pane": "price",
                "side": plan["side"], "entry": plan["entry"],
                "stop": plan["stop"], "targets": [t["price"] for t in tgt],
                "pnl": [t.get("pnl") for t in tgt] if qty else None,
                "risk_amount": plan.get("risk_amount"),
                "qty": qty, "rr": tgt[0]["rr"],
                "t0": t0, "t1": t0 + step * ahead,
                "label": (f"{plan['side']} · R:R {tgt[0]['rr']}"
                          + (f" · qty {qty}" if qty else "")
                          + (f" · {basis}" if basis else "")),
                "source": {"tool": "plan_position", "interval": interval,
                           **({"basis": basis} if basis else {})}})

    return {"plan": plan, "history": hist, "_note": (
        "Drawn on the chart, projecting FORWARD from the latest bar — it "
        "marks the window the trade would run in, so never describe it as "
        "something the chart has already done. Name the level or pattern each "
        "of entry, stop and target came from; a plan whose numbers have no "
        "stated origin is a guess wearing arithmetic. "
        "A new plan_position call replaces it, "
        "draw_mode=clear removes it. Quote these figures exactly, and always "
        "put history.hit_rate NEXT TO target-1's breakeven_hit_pct — a hit "
        "rate without that benchmark reads as an edge it may not be (within "
        "~8 points is noise: say so). This prices the USER'S stated plan; it "
        "is analysis, not a recommendation, and must close as such.")}


def _logo_map() -> dict[str, str]:
    """symbol -> logo URL, across BOTH sources.

    Companies get theirs from company_profile (logo.dev, resolved by domain);
    everything else from instrument_logo (sync_instrument_logos.py) — crypto
    through the same logo.dev ladder, FX as composited circular flags, indices
    and commodities as generated badges.

    Two tables rather than one because they state different facts: a row in
    company_profile means "a company stands behind this symbol", which is not
    true of Bitcoin or of NIFTY IT, and /company would then answer for them.
    company_profile wins a collision — a real listed company outranks any
    generated mark.
    """
    out: dict[str, str] = {}
    for table, col in (("instrument_logo", "logo_url"),
                       ("company_profile", "logo_url")):
        try:
            out.update({s: u for s, u in _con.execute(
                f"SELECT symbol, {col} FROM {table} "
                f"WHERE {col} IS NOT NULL AND {col} != ''")})
        except sqlite3.Error:
            pass          # table absent — the other source still answers
    return out


def _classification_row(sym: str):
    try:
        return _con.execute(
            "SELECT name, industry FROM classification WHERE symbol=?",
            (sym,)).fetchone()
    except sqlite3.Error:
        return None


_PROFILE_COLS = ("sc_id", "name", "long_name", "industry_slug", "sector",
                 "industry", "market_cap", "summary", "website", "employees",
                 "city", "country", "logo_url", "eps", "eps_basis",
                 "eps_period", "ceo", "pb", "ev_sales", "ev_ebitda", "roe",
                 "roa", "net_margin", "current_ratio", "debt_to_equity",
                 "book_value_ps")

# The range buttons, in the same set the Pivot stock page offers. Each maps to
# an interval this store actually holds, so the page's chart is the chart's
# bars — not a second, differently-sourced series.
# bar counts are one window exactly: an NSE session is 375 minutes, so 75 5m
# bars is one day, 125 15m bars is five sessions, 7 hourly bars is a session.
_RANGE_SPEC = {"1D": ("5m", 75), "1W": ("15m", 125), "1M": ("1h", 154),
               "6M": ("1d", 126), "1Y": ("1d", 252), "5Y": ("1d", 1260)}


def company_history(sym: str, rng: str) -> dict:
    """Close series for one range button, read through get_bars so the page
    sees the same (live-merged) bars the chart draws."""
    rng = (rng or "5Y").upper()
    interval, want = _RANGE_SPEC.get(rng, _RANGE_SPEC["5Y"])
    bars = get_bars(sym, interval, None, want)["bars"]
    note = None
    if bars and interval != "1d":
        # trim to whole sessions: the last stored session can be partial, so a
        # fixed bar count spills into the previous day and "1D" would show two.
        sessions = {"1D": 1, "1W": 5}.get(rng, 22)
        days = sorted({(b["t"] + IST_OFF) // 86400 for b in bars})[-sessions:]
        bars = [b for b in bars if (b["t"] + IST_OFF) // 86400 >= days[0]]
    if not bars and interval != "1d":
        # intraday is stored per hydrated symbol; a cold one has daily only.
        # Say which series is on screen rather than drawing a shorter window
        # and calling it the intraday one.
        interval, bars = "1d", get_bars(sym, "1d", None, 126)["bars"]
        note = f"no intraday history stored for {sym} — showing daily closes"
    # keep the line light without lying: every Nth real close, and ALWAYS the
    # newest bar — a decimated tail would end the line on a stale price while
    # the header quotes the last close.
    step = max(1, len(bars) // 420)
    keep = bars[::step]
    if bars and keep[-1] is not bars[-1]:
        keep.append(bars[-1])
    out = {"range": rng, "interval": interval,
           "points": [{"t": b["t"], "c": b["c"]} for b in keep]}
    if note:
        out["note"] = note
    return out


def company_page(sym: str, rng: str = "5Y") -> dict:
    """Everything the company page shows, in one read.

    The profile half is synced prose and identity (sync_company_profile.py);
    the price half is computed from THIS store's bars, so the page and the
    chart can never quote different numbers for the same session.
    """
    sym = sym.upper().strip()
    prof: dict = {}
    try:
        row = _con.execute(
            f"SELECT {','.join(_PROFILE_COLS)} FROM company_profile "
            "WHERE symbol=?", (sym,)).fetchone()
        if row:
            prof = {k: v for k, v in zip(_PROFILE_COLS, row) if v is not None}
    except sqlite3.Error:
        prof = {}
    cls = _classification_row(sym)
    if cls and "name" not in prof:
        prof["name"] = cls[0]
    if cls and "industry_slug" not in prof:
        prof["industry_slug"] = cls[1]

    daily = _con.execute(
        "SELECT ts,o,h,l,c,v FROM bars_1d WHERE symbol=? ORDER BY ts",
        (sym,)).fetchall()
    out: dict = {"symbol": sym, "exchange": "NSE", **prof}
    if not daily:
        out["_unavailable"] = ("no stored price history for this symbol — "
                               "the profile is shown without market data")
        return out

    last, prev = daily[-1], (daily[-2] if len(daily) > 1 else daily[-1])
    win = daily[-252:]
    out["price"] = {
        "last": round(last[4], 2), "open": round(last[1], 2),
        "high": round(last[2], 2), "low": round(last[3], 2),
        "prev_close": round(prev[4], 2), "volume": int(last[5] or 0),
        "change": round(last[4] - prev[4], 2),
        "change_pct": round((last[4] - prev[4]) / prev[4] * 100, 2)
        if prev[4] else None,
        "as_of": _ist(last[0], False),
        "sessions_stored": len(daily),
    }
    out["range_52w"] = {
        "high": round(max(r[2] for r in win), 2),
        "low": round(min(r[3] for r in win), 2),
        "sessions": len(win),
        "full_year": len(win) >= 252,
    }
    eps = prof.get("eps")
    if eps and eps > 0:
        out["valuation"] = {
            "pe": round(last[4] / eps, 1), "eps": round(eps, 2),
            "basis": prof.get("eps_basis"), "period": prof.get("eps_period")}
    # P/B and the EV multiples are the enrichment DB's own trailing figures —
    # kept separate from the P/E we compute here, because they are as of the
    # enrichment run, not as of this store's last close.
    ratios = {k: round(prof[k], 2) for k in ("pb", "ev_sales", "ev_ebitda")
              if isinstance(prof.get(k), (int, float))}
    if ratios:
        out["ratios"] = ratios
    feats = (_screen_features() or {}).get(sym) or {}
    out["metrics"] = {k: feats[k] for k in (
        "ret_1w", "ret_1m", "ret_3m", "ret_1y", "rsi14", "atr_pct",
        "sma50_rel", "sma200_rel", "turnover_20d_cr", "dist_52w_high")
        if feats.get(k) is not None}
    ind = prof.get("industry_slug")
    if ind:
        peers = _con.execute(
            "SELECT c.symbol, c.name, p.market_cap, p.logo_url FROM classification c "
            "LEFT JOIN company_profile p ON p.symbol = c.symbol "
            "WHERE c.industry=? AND c.symbol!=? ORDER BY "
            "COALESCE(p.market_cap, 0) DESC LIMIT 8", (ind, sym)).fetchall()
        out["peers"] = [{"symbol": s, "name": n, "market_cap": m, "logo_url": lo}
                        for s, n, m, lo in peers]
    out["history"] = company_history(sym, rng)
    return out


# ── Pivot-shaped API ────────────────────────────────────────────────────────
# charto/web is Pivot's stock page, copied file-for-file. Rather than edit that
# page to speak charto's protocol, this answers the endpoints it already calls
# (`/api/markets/quote`, `/sparkline`, `/ohlc`, `/financials/…`) in Pivot's own
# response shapes — out of charto's store, so the page and the chart quote the
# same bars. Anything charto genuinely has no source for is returned as the
# honest empty state the page already knows how to render (`available: false`,
# null fields), never as a filler number.

def _iso(ts: int) -> str:
    return datetime.fromtimestamp(
        ts, tz=timezone(timedelta(seconds=IST_OFF))).isoformat()


def _api_quote(sym: str) -> tuple[int, dict]:
    d = company_page(sym, "1D")
    p = d.get("price")
    if not p:
        return 404, {"detail": f"no quote available for {sym}"}
    r = d.get("range_52w") or {}
    return 200, {
        "symbol": sym, "name": d.get("long_name") or d.get("name") or sym,
        "exchange": "NSE", "sector": d.get("sector"),
        "industry": d.get("industry"),
        "ltp": p["last"], "change": p["change"], "change_pct": p["change_pct"],
        "open": p["open"], "high": p["high"], "low": p["low"],
        "prev_close": p["prev_close"], "volume": p["volume"],
        "w52_high": r.get("high"), "w52_low": r.get("low"),
        "market_cap": d.get("market_cap"),
        "pe_ratio": (d.get("valuation") or {}).get("pe"),
        "last_updated": _iso(_con.execute(
            "SELECT MAX(ts) FROM bars_1d WHERE symbol=?", (sym,)).fetchone()[0]),
        "logo_url": d.get("logo_url"),
        # the store is Kite 1-minute history, replayed from disk — not a live
        # feed, so `live` stays false and the page keeps its delayed styling
        "live": False, "source": "kite_rest", "is_index": False,
    }


def _api_financials(sym: str) -> tuple[int, dict]:
    """Pivot's financials payload: the statements as Pivot's own code
    assembled them (sync_financials.py), plus the profile and the two
    yfinance-sourced multiples this store already holds."""
    row = _con.execute(
        f"SELECT {','.join(_PROFILE_COLS)} FROM company_profile WHERE symbol=?",
        (sym,)).fetchone()
    prof = dict(zip(_PROFILE_COLS, row)) if row else {}
    try:
        blob = _con.execute("SELECT payload FROM financials WHERE symbol=?",
                            (sym,)).fetchone()
    except sqlite3.Error:
        blob = None
    out = json.loads(blob[0]) if blob else {
        "available": False, "company": None, "latest": {}, "history": {},
        "source": "moneycontrol_via_financials_db"}

    if prof:
        # Moneycontrol carries no ratios for a chunk of the universe (banks
        # especially); Pivot fills those from yfinance live. The same two
        # figures are already synced here, so they fill the same gaps —
        # tagged yfinance, and only where MC left a hole.
        for field, key, item in (("price_to_book", "pb", "Price to Book"),
                                 ("ev_to_sales", "ev_sales", "EV to Sales"),
                                 ("ev_to_ebitda", "ev_ebitda", "EV to EBITDA"),
                                 ("roe", "roe", "Return on Equity"),
                                 ("roa", "roa", "Return on Assets"),
                                 ("net_profit_margin", "net_margin",
                                  "Net Profit Margin"),
                                 ("current_ratio", "current_ratio",
                                  "Current Ratio"),
                                 ("debt_to_equity", "debt_to_equity",
                                  "Debt to Equity"),
                                 ("book_value_per_share", "book_value_ps",
                                  "Book Value / Share")):
            v = prof.get(key)
            if out.get("latest", {}).get(field) is None and v is not None:
                out.setdefault("latest", {})[field] = {
                    "value": float(v), "period_end": None,
                    "period_label": "TTM", "line_item": item, "unit": None,
                    "basis": "consolidated", "source": "yfinance"}
        out["profile"] = {"name": prof.get("long_name") or prof.get("name"),
                          "blurb": prof.get("summary"),
                          "sector": prof.get("sector"),
                          "industry": prof.get("industry"),
                          "website": prof.get("website"), "ceo": prof.get("ceo")}
        if not out.get("company"):
            out["company"] = {
                "sc_id": prof.get("sc_id") or "", "name": prof.get("name") or sym,
                "nse_symbol": sym, "bse_code": None, "ticker": sym,
                "sector": prof.get("sector"),
                "industry_slug": prof.get("industry_slug"),
                "market_cap": prof.get("market_cap"), "is_active": True}
        out["available"] = bool(out.get("available")) or any(
            v is not None for v in (out.get("latest") or {}).values())
    return 200, out


def _api_balance_sheet(sym: str, basis: str) -> tuple[int, dict]:
    basis = basis if basis in ("consolidated", "standalone") else "consolidated"
    try:
        row = _con.execute(
            "SELECT payload FROM balance_sheet WHERE symbol=? AND basis=?",
            (sym, basis)).fetchone()
    except sqlite3.Error:
        row = None
    if not row:
        return 200, {"available": False, "company": None, "basis": basis,
                     "unit": None, "periods": [], "rows": [],
                     "source": "not stored in charto"}
    return 200, json.loads(row[0])


def _api_search(q: str, limit: int) -> dict:
    q = (q or "").strip().upper()
    if not q:
        return {"results": []}
    rows = _con.execute(
        "SELECT c.symbol, c.name, p.sector, p.logo_url FROM classification c "
        "LEFT JOIN company_profile p ON p.symbol = c.symbol "
        "WHERE c.symbol LIKE ? OR UPPER(c.name) LIKE ? "
        "ORDER BY CASE WHEN c.symbol LIKE ? THEN 0 ELSE 1 END, c.symbol "
        "LIMIT ?", (f"%{q}%", f"%{q}%", f"{q}%", max(1, min(limit, 25)))
    ).fetchall()
    return {"results": [{"symbol": s, "name": n or s, "sector": sec,
                         "has_fundamentals": True, "logo_url": lo}
                        for s, n, sec, lo in rows]}


def api_route(path: str, q: dict) -> tuple[int, dict]:
    """Dispatch one Pivot-shaped request. Returns (status, payload)."""
    parts = [p for p in path.split("/") if p]        # ["api", "markets", …]
    tail = parts[1:]
    def sym_of(i):
        return unquote(tail[i]).upper().strip() if len(tail) > i else ""

    if tail[:2] == ["markets", "quote"]:
        s = sym_of(2)
        if s not in _known_symbols():
            return 404, {"detail": f"no quote available for {s}"}
        return _api_quote(s)
    if tail[:2] in (["markets", "sparkline"], ["markets", "ohlc"]):
        s = sym_of(2)
        if s not in _known_symbols():
            return 404, {"detail": f"no history available for {s}"}
        rng = (q.get("range") or "1M").upper()
        interval, want = _RANGE_SPEC.get(rng, _RANGE_SPEC["1M"])
        bars = get_bars(s, interval, None, want)["bars"]
        if not bars and interval != "1d":
            interval, bars = "1d", get_bars(s, "1d", None, 126)["bars"]
        if tail[1] == "sparkline":
            return 200, {"symbol": s, "range": rng, "interval": interval,
                         "points": [{"t": _iso(b["t"]), "v": b["c"]} for b in bars]}
        return 200, {"symbol": s, "range": rng, "interval": interval,
                     "source": "kite",
                     "bars": [{"t": _iso(b["t"]), "o": b["o"], "h": b["h"],
                               "l": b["l"], "c": b["c"], "v": int(b["v"] or 0)}
                              for b in bars]}
    if tail[:1] == ["financials"]:
        s = sym_of(1)
        if len(tail) > 2:      # /financials/{sym}/balance_sheet
            return _api_balance_sheet(s, q.get("basis", "consolidated"))
        return _api_financials(s)
    if tail[:2] == ["markets", "metric-series"]:
        return 200, {"symbol": sym_of(2), "metric": q.get("metric", "pe"),
                     "range": q.get("range", "1Y"), "available": False,
                     "points": [], "source": "none"}
    if tail[:2] == ["companies", "search"]:
        return 200, _api_search(q.get("q", ""), int(q.get("limit", 10) or 10))
    if tail[:2] == ["companies", "logos"]:
        want = [x.strip().upper() for x in (q.get("symbols") or "").split(",") if x.strip()]
        have = _logo_map() if want else {}
        return 200, {"logos": {s: have.get(s) for s in want}}
    return 404, {"detail": f"{path} is not served by charto"}


# ── volume at price ─────────────────────────────────────────────────────────
#
# Volume profile, built the only way an Indian retail feed permits: from the
# stored 1-minute bars, each bar's volume spread UNIFORMLY across its own
# high-low. That assumption is the entire error budget, so this tool measures
# it rather than hiding it — the volume-weighted mean bar span — and refuses
# to draw rows finer than the smear it is built from.
#
# Measured on NSE large caps, a 1-minute bar spans a median of 18 ticks and
# ZERO bars are single-price. So a 120-row profile on a stock whose bars smear
# over 20 ticks is 6x more resolution than the input carries: it looks precise
# and most of that detail is manufactured. The row count is therefore derived
# from the data and the caller may only ask for COARSER, never finer.
#
# What this is NOT, and must never be labelled as: order flow. Delta,
# cumulative delta, footprint and bid/ask imbalance all need the aggressor
# side of each trade — whether it hit the bid or lifted the ask — which
# requires true tick-by-tick. Kite throttles to ~1 snapshot/second, so that
# data does not exist on any Indian retail feed at any price. The optional
# up/down split here is a BAR-DIRECTION heuristic (close >= open), the same
# one TradingView uses, and it is labelled as such in the payload so the
# model cannot narrate it as buying versus selling.
#
# The construction is otherwise identical to TradingView's, which also builds
# from 1-minute bars — so POC and value area agree with what the user sees
# elsewhere. The edge we have is depth: composites over years, off the local
# archive, which no free tier will build.

_VP_MAX_ROWS = 60          # beyond this the histogram is thinner than a pixel
_VP_MIN_ROWS = 6
_VP_MAX_SESSIONS = 250     # ~1 trading year of 1-min bars, ~94k rows


def _infer_tick(mins: list[tuple]) -> float | None:
    """Smallest price increment actually observed. Reported, never assumed —
    an instrument's tick is a listing fact and we only have its prints."""
    seen: set[float] = set()
    for b in mins[:4000]:
        seen.update((b[1], b[2], b[3], b[4]))
    xs = sorted(x for x in seen if x)
    if len(xs) < 32:
        return None
    gap = min((b - a for a, b in zip(xs, xs[1:]) if b - a > 1e-9), default=None)
    if gap is None or gap < xs[-1] * 1e-7:
        return None
    return round(gap, 6)


def _px(v: float, ref: float) -> float:
    """Round a price to a precision its own magnitude can carry.

    A flat round(…, 2) is an equity habit. On DOGEUSDT at $0.07 it collapsed
    every row bound, the value area and the row height to the same two
    digits: a 45-row profile reported a value area of 0.07–0.07 and a width
    of 0.0%, which then ranked it top of a "tightest value area" screen. The
    precision has to follow the instrument, not the rupee.
    """
    a = abs(ref)
    d = (2 if a >= 100 else 3 if a >= 10 else 4 if a >= 1
         else 6 if a >= 0.01 else 8)
    return round(v, d)


def _profile(mins: list[tuple], rows: int = 0,
             value_area_pct: float = 70.0) -> dict | None:
    """The volume-at-price arithmetic, and the ONLY copy of it.

    The chart tool, the HTTP route and the universe sweep all come through
    here. A screener that disagreed with the chart about where the point of
    control sits would be worse than no screener, and two implementations
    drift the moment one is tuned.

    None when there is nothing to profile — no volume, or no range.
    """
    total_v = sum(b[5] or 0 for b in mins)
    if not total_v:
        return None
    lo = min(b[3] for b in mins)
    hi = max(b[2] for b in mins)
    rng = hi - lo
    if rng <= 0:
        return None

    # the measurement that bounds everything below: a row can never be finer
    # than the price span a single bar smears its volume across
    vw_span = sum((b[2] - b[3]) * (b[5] or 0) for b in mins) / total_v
    ceiling = int(rng / vw_span) if vw_span > 0 else _VP_MAX_ROWS
    ceiling = max(_VP_MIN_ROWS, min(_VP_MAX_ROWS, ceiling))
    asked = int(rows or 0)
    n = max(_VP_MIN_ROWS, min(asked, ceiling) if asked > 0 else ceiling)
    capped = asked > ceiling

    row_h = rng / n
    vol = [0.0] * n
    up = [0.0] * n
    dn = [0.0] * n
    for _t, o, h, l, c, v in mins:
        if not v:
            continue
        side = up if c >= o else dn
        i0 = max(0, min(n - 1, int((l - lo) / row_h)))
        i1 = max(0, min(n - 1, int((h - lo) / row_h)))
        if i1 <= i0:
            vol[i0] += v
            side[i0] += v
            continue
        span = h - l
        for i in range(i0, i1 + 1):
            r_lo = lo + i * row_h
            ov = min(h, r_lo + row_h) - max(l, r_lo)
            if ov <= 0:
                continue
            share = v * ov / span
            vol[i] += share
            side[i] += share

    # ── point of control and value area (classic two-row expansion) ──
    poc = max(range(n), key=lambda i: vol[i])
    target = total_v * max(50.0, min(90.0, float(value_area_pct))) / 100.0
    a = b = poc
    acc = vol[poc]
    while acc < target and (a > 0 or b < n - 1):
        upper = (vol[b + 1] + (vol[b + 2] if b + 2 < n else 0.0)
                 if b < n - 1 else -1.0)
        lower = (vol[a - 1] + (vol[a - 2] if a - 2 >= 0 else 0.0)
                 if a > 0 else -1.0)
        if upper >= lower:
            for _ in range(2):
                if b < n - 1 and acc < target:
                    b += 1
                    acc += vol[b]
        else:
            for _ in range(2):
                if a > 0 and acc < target:
                    a -= 1
                    acc += vol[a]

    return {"lo": lo, "hi": hi, "rng": rng, "total_v": total_v,
            "vw_span": vw_span, "ceiling": ceiling, "capped": capped,
            "asked": asked, "n": n, "row_h": row_h, "vol": vol,
            "up": up, "dn": dn, "poc_i": poc, "a": a, "b": b, "acc": acc,
            "poc": _px(lo + (poc + 0.5) * row_h, hi),
            "val": _px(lo + a * row_h, hi),
            "vah": _px(lo + (b + 1) * row_h, hi)}


def tool_volume_profile(frm: str = "", to: str = "", lookback_sessions: int = 1,
                        rows: int = 0, value_area_pct: float = 70.0,
                        split: bool = False, draw: bool = True,
                        draw_mode: str = "replace") -> dict:
    """Volume traded at each price over a window, from 1-minute bars.

    Returns the point of control, the value area, and the high/low volume
    nodes — plus the measured smear that bounds how finely any of it can
    honestly be resolved.
    """
    if str(draw_mode or "").lower() == "clear":
        _scene_add({"kind": "clear", "scope": "vprofile",
                    "owner": "volume_profile"})
        return {"cleared": True, "_note": "Volume profile removed."}

    # ── resolve the window to a raw-bar time range ──
    daily = _rows("1d", 4000)
    if not daily:
        return {"error": "no daily history to locate a window in"}
    t0 = _parse_ist(frm) if frm else None
    t1 = _parse_ist(to) if to else None
    if (frm and t0 is None) or (to and t1 is None):
        return {"error": "could not read the date(s)",
                "hint": "use the chart's format, e.g. '22 Jul 2026'"}
    if t0 is None and t1 is None:
        n_sess = max(1, min(int(lookback_sessions or 1), _VP_MAX_SESSIONS))
        window = daily[-n_sess:]
        lo_ts, hi_ts = window[0][0], window[-1][0] + 86400
    else:
        if t0 is None:
            t0 = t1
        if t1 is None:
            t1 = t0
        if t1 < t0:
            t0, t1 = t1, t0
        lo_ts, hi_ts = t0, t1 + 86400
        n_sess = sum(1 for r in daily if lo_ts <= r[0] < hi_ts)
        if n_sess > _VP_MAX_SESSIONS:
            return {"error": f"window is {n_sess} sessions — a profile reads "
                             f"1-minute bars, so it is capped at "
                             f"{_VP_MAX_SESSIONS}",
                    "hint": "narrow the window, or ask for a daily-bar study"}

    live = _live_view(_sym())
    if live and live[1] is not None:
        hi_ts = min(hi_ts, live[1])   # replay clock hides the session's future
    mins = _con.execute(
        "SELECT ts,o,h,l,c,v FROM bars WHERE symbol=? AND ts>=? AND ts<? "
        "ORDER BY ts", (_sym(), lo_ts, hi_ts)).fetchall()
    if live and live[0] is not None and lo_ts <= live[0][0] < hi_ts:
        _merge_form_intraday(mins, live[0])
    if not mins:
        return {"error": "no 1-minute bars in that window",
                "data_spans": f"{_ist(daily[0][0], False)} → "
                              f"{_ist(daily[-1][0], False)} {_tzl()}"}

    prof = _profile(mins, rows, value_area_pct)
    if prof is None:
        # Same boundary the indicator engine enforces: an instrument that
        # prints no volume has no volume to profile, and a flat histogram
        # would read as "no interest" rather than "not quoted".
        if not sum(b[5] or 0 for b in mins):
            return {"error": "this instrument prints no volume",
                    # human-facing: the status line shows this one verbatim
                    "hint": ("indices and India VIX are quoted as levels, not "
                             "traded — try a constituent stock"),
                    "_note": ("Every bar in the window has v=0 — indices and "
                              "India VIX are quoted as levels, not traded "
                              "instruments, so there is no volume at price to "
                              "build. Say that plainly. A profile on the index "
                              "FUTURES or on a constituent stock is the nearest "
                              "real thing.")}
        return {"error": "price did not move in that window"}

    lo, hi, rng = prof["lo"], prof["hi"], prof["rng"]
    total_v, vw_span = prof["total_v"], prof["vw_span"]
    n, row_h, vol = prof["n"], prof["row_h"], prof["vol"]
    up, dn = prof["up"], prof["dn"]
    poc, a, b, acc = prof["poc_i"], prof["a"], prof["b"], prof["acc"]
    ceiling, capped, asked = prof["ceiling"], prof["capped"], prof["asked"]
    tick = _infer_tick(mins)

    mean_v = total_v / n
    price_of = lambda i: lo + (i + 0.5) * row_h          # noqa: E731
    hvn = [_px(price_of(i), hi) for i in range(1, n - 1)
           if vol[i] >= 1.3 * mean_v
           and vol[i] > vol[i - 1] and vol[i] >= vol[i + 1]]
    lvn = [_px(price_of(i), hi) for i in range(1, n - 1)
           if vol[i] <= 0.6 * mean_v
           and vol[i] < vol[i - 1] and vol[i] <= vol[i + 1]]

    peak = max(vol) or 1.0
    out_rows = []
    for i in range(n):
        # Both denominators, both named. One un-suffixed "share" got read as
        # a share of total volume and printed as a column of percentages
        # summing to 400% — a row's height on the chart is its share of the
        # BUSIEST row, which is a different number from its share of the day.
        r = {"lo": _px(lo + i * row_h, hi),
             "hi": _px(lo + (i + 1) * row_h, hi),
             "volume": int(vol[i]),
             "pct_of_total": round(vol[i] / total_v * 100, 1),
             "pct_of_busiest_row": round(vol[i] / peak * 100, 1)}
        if split:
            r["up_bar_volume"] = int(up[i])
            r["down_bar_volume"] = int(dn[i])
        out_rows.append(r)

    poc_price, val, vah = prof["poc"], prof["val"], prof["vah"]
    ccy = quote_ccy(_sym())
    unit = "₹" if ccy == "INR" else "$"

    if draw:
        if str(draw_mode or "replace").lower() == "replace":
            _scene_add({"kind": "clear", "scope": "vprofile",
                        "owner": "volume_profile"})
        _scene_add({
            "kind": "vprofile", "id": "VP", "pane": "price", "role": "neutral",
            "rows": [{"lo": r["lo"], "hi": r["hi"],
                      "share": round(vol[i] / peak, 3)}
                     for i, r in enumerate(out_rows)],
            "poc": poc_price, "val": val, "vah": vah,
            "label": f"POC {unit}{poc_price} · VA {unit}{val}–{unit}{vah}",
            "source": {"tool": "volume_profile", "interval": "1m",
                       "bars_scanned": len(mins),
                       "method": (f"volume at price, {n} rows of "
                                  f"{unit}{_px(row_h, hi)}")}})

    note = (f"Volume at price from {len(mins):,} one-minute bars over "
            f"{n_sess} session(s), in {n} rows of {unit}{_px(row_h, hi)}. Each "
            f"bar's volume is spread uniformly across its own high-low; the "
            f"volume-weighted mean bar span is {unit}{_px(vw_span, hi)}"
            + (f" (~{vw_span / tick:.0f} ticks)" if tick else "")
            + f", which is why the row height is what it is. "
              f"Quote the levels; describe this as volume at price built from "
              f"1-minute bars — the same construction TradingView uses. It is "
              f"NOT order flow: never call it delta, footprint, or buying vs "
              f"selling pressure.")
    if capped:
        note += (f" The requested {asked} rows was reduced to {n}: a single "
                 f"1-minute bar already smears its volume across "
                 f"{unit}{_px(vw_span, hi)} of price, so rows finer than that would "
                 f"be invented detail, not measured detail. Say that — give "
                 f"the reason and the number, not just the cap.")
    if split:
        note += (" The up/down split is a bar-direction heuristic (close >= "
                 "open), not the aggressor side of trades. Present it that "
                 "way or not at all.")
    # A 3-month composite spans a price range an intraday chart never shows,
    # so most of the histogram renders above or below the visible window.
    # Nothing is wrong with the profile — the user just cannot see it. This
    # rides in its OWN key: appended to the end of _note it competed with
    # four other instructions and the model dropped it every time.
    view = ""
    if n_sess >= 10:
        view = (f"The profile spans {unit}{_px(lo, hi)}–{unit}{_px(hi, hi)}, which is "
                f"wider than an intraday chart shows — most of it is off "
                f"screen right now. Tell the user to switch to the D or W "
                f"timeframe to see the whole thing. This tool cannot change "
                f"the timeframe itself.")

    return {
        "window": {"from": _ist(mins[0][0], False), "to": _ist(mins[-1][0], False),
                   "sessions": n_sess, "minute_bars": len(mins), "tz": _tzl()},
        "point_of_control": poc_price,
        # Coarse rows cannot land exactly on 70%: the area grows a whole row
        # at a time, so the achieved share is the tightest one that CLEARS
        # the target. Both numbers are named so a 84% value area is reported
        # as what it is rather than asserted to be the 70% convention.
        "value_area": {"low": val, "high": vah,
                       "pct_achieved": round(acc / total_v * 100, 1),
                       "pct_requested": round(float(value_area_pct), 1)},
        "range": {"low": _px(lo, hi), "high": _px(hi, hi)},
        "total_volume": int(total_v),
        "high_volume_nodes": hvn, "low_volume_nodes": lvn,
        "rows": out_rows,
        "resolution": {
            "row_height": _px(row_h, hi),
            "rows": n, "max_rows_supported": ceiling,
            "requested_rows": asked or None, "capped": capped,
            "volume_weighted_bar_span": _px(vw_span, hi),
            "tick_size": tick,
            "why": ("A row cannot be finer than the smear it is built from. "
                    "Each 1-minute bar's volume is spread uniformly across "
                    "its high-low, so the volume-weighted mean span is the "
                    "floor on row height."),
        },
        "method": {
            "built_from": "1-minute bars (the stored granularity)",
            "distribution": "uniform across each bar's high-low",
            "value_area": f"{value_area_pct:.0f}% of volume, classic two-row "
                          f"expansion outward from the point of control",
            "hvn_lvn": "local maxima >= 1.3x mean row volume / local minima "
                       "<= 0.6x mean row volume",
            "not_available": ("delta, cumulative delta, footprint and bid/ask "
                              "imbalance — all require the aggressor side of "
                              "each trade, which needs true tick-by-tick. No "
                              "Indian retail feed carries it."),
        },
        **({"to_see_it_all": view} if view else {}),
        "_note": note,
    }


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
    have = _symbols_with_bars()
    peers = [{"symbol": p, "name": n, **({} if p in have else {"cold": True})}
             for p, n in _con.execute(
                 "SELECT symbol, name FROM classification "
                 "WHERE industry=? AND symbol!=? ORDER BY symbol", (ind, sym))]
    # Counted, not hardcoded. It read "500-company" until the universe grew to
    # hold indices, crypto, MCX futures and INR pairs — and India VIX, asked
    # for its peers, answered "no peers in the available 500-company chart
    # universe", which was both the wrong number and the wrong noun.
    n_uni = _con.execute(
        "SELECT COUNT(*) FROM classification").fetchone()[0]
    return {"symbol": sym, "name": name, "industry": ind, "peers": peers,
            "_note": (
                f"Industry comes from the Moneycontrol classification; peers "
                f"are limited to the {n_uni}-instrument chart universe, which "
                f"holds NSE stocks, indices, India VIX, spot crypto, MCX "
                f"futures and INR pairs — do not call it a company universe. "
                f"An instrument alone in its industry has no peers here; say "
                f"that plainly rather than implying it has no counterparts "
                f"anywhere. To compare "
                "price paths, pick a handful (the user's ask decides which — "
                "do not dump the whole list) and call compare_symbols. To "
                f"compare a single METRIC across the whole peer set — RSI, "
                f"returns, distance from highs, any screen feature — call "
                f"screen_universe with industry='{ind}' and sort by that "
                f"feature; it covers every peer at once, cold or not. A peer "
                "marked cold downloads its history on first use, ~6 s each.")}


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
            # Crore-rupees is the wrong unit AND the wrong scale for a
            # dollar-quoted asset: BTC's ~$670M daily notional came back as
            # "67.3 cr", which a reply would state as ₹67 crore. The key name
            # carries the unit so a mixed basket cannot silently blend them.
            **({"avg_daily_turnover_musd": round(
                sum(r[3] * r[4] for r in rows) / len(rows) / 1e6, 1)}
               if session_for(s) == UTC_SESSION else
               {"avg_daily_turnover_cr": round(
                   sum(r[3] * r[4] for r in rows) / len(rows) / 1e7, 1)}),
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
    res = {"window": f"{_ist(start, wt)} → {_ist(max(v[-1][0] for v in series.values()), wt)} {_tzl()}",
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
    "sma50_cross_ago", "sma200_cross_ago",
    "range_20d_pct", "vol_z20", "turnover_20d_cr", "turnover_20d_musd",
    "vp20_pos", "vp20_va_width_pct", "vp20_poc_dist_pct", "vp20_poc_shift_pct",
)

# Volume-profile features come from the swept vp_screen table, not from
# bars_1d — they need 1-MINUTE bars, which only the hydrated symbols have.
# That makes their coverage a fraction of the universe's, and a screen that
# quietly ranked 54 rows as though they were 549 would be the same lie as
# computing MFI on an index. Every screen that filters or sorts on one of
# these reports how many instruments could be scored at all.
_VP_FEATURES = frozenset(
    {"vp20_pos", "vp20_va_width_pct", "vp20_poc_dist_pct",
     "vp20_poc_shift_pct"})

# Features that are arithmetic on VOLUME. The universe now holds instruments
# with no volume at all: an index prints no traded quantity, so bars_1d
# carries v=0 on 100% of its days (all 24 indices and India VIX). Computing
# these anyway does not fail loudly — it fabricates. Measured on NIFTY 50:
# turnover came out 0.0 (a real zero, and the DEFAULT sort key), OBV and A/D
# flat 0.0, and MFI(14) reported 100.0 — a maximally-overbought reading
# manufactured out of no data. A feature whose input does not exist is None.
_VOLUME_FEATURES = frozenset(
    {"vol_z20", "turnover_20d_cr", "turnover_20d_musd"})

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
    "sma50_cross_ago": "sessions since close last crossed its 50-day SMA "
                       "(either direction — sma50_rel's sign says which side "
                       "it is on NOW); 'just crossed above' = this lt N plus "
                       "sma50_rel gt 0. Null if no cross within ~120 sessions",
    "sma200_cross_ago": "sessions since close last crossed its 200-day SMA "
                        "(either direction — pair with sma200_rel's sign); "
                        "null if no cross within ~120 sessions",
    "range_20d_pct": "20-day high-to-low width as % of close — low = coiled",
    "vol_z20": "last session's volume in σ of the prior 20 sessions. Null for "
               "instruments that print no volume (indices, India VIX)",
    "turnover_20d_cr": "avg daily close*volume over 20 sessions, RUPEES CRORE "
                       "— INR-quoted instruments only. Null for dollar-quoted "
                       "ones (use turnover_20d_musd) and for indices",
    "turnover_20d_musd": "avg daily close*volume over 20 sessions, MILLIONS "
                         "OF US DOLLARS — dollar-quoted instruments (spot "
                         "crypto) only. Null for INR-quoted ones",
    "vp20_pos": "where the close sits inside the 20-session VALUE AREA, as % "
                "of that area's width: 0 = at the value-area low, 100 = at "
                "the high, gt 100 = trading ABOVE accepted value, lt 0 = "
                "below it. 'above value' = gt 100; 'back inside value' = "
                "gt 0 plus lt 100",
    "vp20_va_width_pct": "the 20-session value area as % of its point of "
                         "control — how tightly volume agreed on price. Low "
                         "= coiled/balanced, high = distributed",
    "vp20_poc_dist_pct": "% the close sits above (+) or below (-) the "
                         "20-session point of control (the most-traded price)",
    "vp20_poc_shift_pct": "% this 20-session POC moved against the PRIOR 20 "
                          "sessions' POC — value migration, the profile's "
                          "own trend measure. Positive = value building "
                          "higher",
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
        # vp_screen is swept on its OWN schedule, so it has to enter the
        # stamp too — otherwise a fresh sweep sits unread behind a matrix
        # cached against unchanged daily bars
        vp = 0
        if _con.execute("SELECT 1 FROM sqlite_master WHERE name='vp_screen'"
                        ).fetchone():
            vp = _con.execute(
                "SELECT MAX(built_at) FROM vp_screen").fetchone()[0] or 0
        return (cnt, mx, ver, vp)
    except sqlite3.Error:
        return None


def _rel(a: float, b) -> float | None:
    return None if not b else round((a - b) / b * 100, 2)


def _vp_screen_rows() -> dict:
    """The swept volume-profile features, keyed by symbol.

    Absent table or absent row both mean "not scored here" rather than zero:
    the profile needs 1-minute bars and most of the universe is stored daily
    until something hydrates it. Built by sweep_vp.py.
    """
    try:
        cur = _con.execute(
            "SELECT symbol, pos, va_width_pct, poc_dist_pct, poc_shift_pct, "
            "poc, val, vah, n_rows, row_h FROM vp_screen")
    except sqlite3.Error:
        return {}          # never swept on this box
    out = {}
    for (s, pos, w, d, sh, poc, val, vah, n, rh) in cur:
        out[s] = {"vp20_pos": pos, "vp20_va_width_pct": w,
                  "vp20_poc_dist_pct": d, "vp20_poc_shift_pct": sh,
                  "_vp": {"poc": poc, "value_area": [val, vah],
                          "rows": n, "row_height": rh}}
    return out


def _squash(s: str, sep: str = "") -> str:
    return sep.join("".join(ch if ch.isalnum() else " " for ch in s).lower().split())


def _screen_row_features(rows: list[tuple], ccy: str = "INR") -> dict:
    """ascending daily (ts,o,h,l,c,v) for ONE symbol → its feature dict.

    Every feature whose window the symbol cannot cover is None. Falling back
    to a shorter window would rank a six-month listing against a ten-year one
    and call both a 1-year return; a null is the honest answer and the filter
    simply excludes it.

    The same rule now covers the INPUT, not just the window: a symbol that
    prints no volume gets None for every volume feature rather than the zero
    the arithmetic would produce, and turnover lands in the feature that
    carries its own currency.
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
    # a cross is a state CHANGE — "which side now" is the smaX_rel sign, this
    # is how many sessions ago the side last flipped
    for key, p in (("sma50_cross_ago", 50), ("sma200_cross_ago", 200)):
        if n >= p + 2:
            s = indicators.sma(closes, p)
            if s[-1] is None:
                continue
            latest = closes[-1] > s[-1]
            for i in range(n - 2, max(p - 2, n - 122), -1):
                if s[i] is None:
                    break
                if (closes[i] > s[i]) != latest:
                    f[key] = n - 2 - i
                    break
    if n >= 20:
        w = rows[-20:]
        f["range_20d_pct"] = round(
            (max(r[2] for r in w) - min(r[3] for r in w)) / c * 100, 2) if c else None
    # `has_vol` is read off the data, not off a symbol list: an instrument
    # that starts printing volume tomorrow simply starts screening on it, and
    # nothing here has to be edited to stop being wrong.
    has_vol = any(r[5] for r in rows[-60:])
    if has_vol and n >= 20:
        w = rows[-20:]
        notional = sum(r[4] * r[5] for r in w) / len(w)
        key = "turnover_20d_musd" if ccy == "USD" else "turnover_20d_cr"
        f[key] = round(notional / (1e6 if ccy == "USD" else 1e7), 2)
    if has_vol and n >= 21:
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
    feats = {s: _screen_row_features(r, quote_ccy(s))
             for s, r in by_sym.items() if len(r) >= 2}
    # volume-profile features ride in from their own swept table; a symbol
    # with no 1-minute bars simply keeps the Nones it was initialised with
    vp = _vp_screen_rows()
    for s, f in feats.items():
        row = vp.get(s)
        if row:
            f.update(row)
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
        # substring matching needs length to mean anything: "it" sits inside
        # wh"it"egoods and hosp"it"al, so short queries match by prefix only
        want_inds = {i for i in known if _squash(i) == q} or (
            {i for i in known if q and (q in _squash(i) or _squash(i) in q)}
            if len(q) >= 4 else
            {i for i in known if q and _squash(i).startswith(q)})
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

    kind = str(pattern or "").lower().strip()
    if kind and kind not in patterns.CHART_KINDS + patterns.CANDLE_KINDS:
        return {"error": f"unknown pattern '{kind}'",
                "available": {"chart": list(patterns.CHART_KINDS),
                              "candlestick": list(patterns.CANDLE_KINDS)},
                "_note": ("Nothing was screened. Re-call with one exact name "
                          "from this list, or drop `pattern`.")}

    # A volume profile needs 1-MINUTE bars and most of the universe is stored
    # daily until something hydrates it, so these features score a subset. The
    # screen says how big that subset is rather than presenting a ranking of
    # 54 rows as a ranking of 549.
    vp_used = any(nm in _VP_FEATURES for nm, _, _ in parsed) \
        or sort_by in _VP_FEATURES
    # Counted over the set actually being screened. Reported universe-wide,
    # "54 of 549" next to an industry-filtered table read as "54 of 549
    # cryptocurrency instruments" — the model localised a global number to
    # the filter, and the sentence was wrong in a way only the screener could
    # see. When an industry narrows the pool, the pool is what gets counted.
    def _in_pool(sym: str) -> bool:
        return not want_inds or cls.get(sym, (sym, None))[1] in want_inds

    vp_pool = [f for s, f in feats.items() if _in_pool(s)] if vp_used else []
    vp_scored = sum(1 for f in vp_pool if f.get("vp20_pos") is not None)

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

    # The default sort is picked AFTER the survivors are known, because
    # turnover no longer exists for every instrument: an all-crypto screen has
    # None in turnover_20d_cr for every row, and sorting on it would order the
    # result arbitrarily while reporting `sorted_by: turnover_20d_cr`. Falls
    # through to the first key any survivor actually carries.
    if not sort_by:
        sort_by = parsed[0][0] if parsed else next(
            (k for k in ("turnover_20d_cr", "turnover_20d_musd", "ret_1m",
                         "close")
             if any(r["_f"].get(k) is not None for r in survivors)),
            "close")
    # Descending unless the screen itself asked for small values of this
    # feature — "RSI under 30" wants the most oversold first, not the least.
    desc = not any(nm == sort_by and op == "lt" for nm, op, _ in parsed)
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
            # each scanned symbol dates its own bars: `_ist` reads the CHART's
            # timezone, which would stamp a UTC-anchored crypto day with the
            # IST calendar of whatever symbol happens to be open
            off = session_for(r["symbol"])[1]
            ist = (lambda ts, _o=off: datetime.fromtimestamp(  # noqa: E731
                ts + _o, tz=timezone.utc).strftime("%d %b %Y"))
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
    # `_ist` renders in the CHART symbol's timezone, but a screen day is a
    # universe-wide IST calendar date built with _ist_day/IST_OFF. Rendering
    # it through the chart's clock moved every date a day back whenever the
    # open chart was a crypto pair. Pinned to the offset it was computed with.
    def _day_str(day: int) -> str:
        return datetime.fromtimestamp(day * 86400, tz=timezone.utc).strftime(
            "%d %b %Y")

    as_of = _day_str(mode_day) if mode_day else "unknown"
    stale_shown = 0
    # One read per scope present in the shown rows — the pooled record is
    # identical for every row of the same market, and a screen that mixes
    # NSE stocks with crypto must not stamp one market's rate on both.
    uni_by_scope: dict[str, dict] = {}
    if kind:
        for r in shown:
            sc = scope_for(r["symbol"])
            if sc not in uni_by_scope:
                uni_by_scope[sc] = _pattern_universe_stats(
                    kind, "1d", 10, sc) or {}
    uni = next((u for u in uni_by_scope.values() if u), None)
    rows = []
    for r in shown:
        out = {"symbol": r["symbol"], "name": r["name"],
               "industry": r["industry"]}
        d = last_day.get(r["symbol"])
        if d is not None and d != mode_day:
            out["as_of"] = _day_str(d)
            stale_shown += 1
        for k in keep:
            if k not in out:
                out[k] = r["_f"].get(k)
        # a value-area screen is unreadable without the area it screened on
        if vp_used and r["_f"].get("_vp"):
            out["volume_profile"] = r["_f"]["_vp"]
        if "pattern" in r:
            out["pattern"] = r["pattern"]
            u = uni_by_scope.get(scope_for(r["symbol"])) or {}
            if u.get("with_direction_rate_pct") is not None:
                out["universe_rate"] = {
                    "with_direction_rate_pct": u["with_direction_rate_pct"],
                    "n": u.get("n"), "scope": u.get("scope"),
                    "scope_label": u.get("scope_label")}
        rows.append(out)
    universe = len(feats)
    res = {"universe": universe, "matched": len(survivors),
           "shown": len(rows), "as_of": as_of,
           "sorted_by": {"feature": sort_by,
                         "order": "desc" if desc else "asc"},
           "filters_applied": [{"feature": n, "op": o, "value": v}
                               for n, o, v in parsed],
           "rows": rows}
    if vp_used:
        pool_n = len(vp_pool)
        pool_label = (f"instruments in {'/'.join(sorted(want_inds))}"
                      if want_inds else "stored instruments")
        res["volume_profile_coverage"] = {
            "scored": vp_scored, "pool": pool_n, "universe": universe,
            "window_sessions": 20,
            "_note": (f"Volume-profile features are built from 1-MINUTE bars, "
                      f"which only {vp_scored} of the {pool_n} {pool_label} "
                      f"currently have — the rest hold daily bars only and "
                      f"were scored as null, so they are absent from this "
                      f"ranking rather than ranked last. Say '{vp_scored} of "
                      f"{pool_n}' and describe the pool exactly as written "
                      f"here; do not restate it against the whole "
                      f"{universe}-instrument universe. Indices and India VIX "
                      f"print no volume and can never be scored."),
        }
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
    if uni:
        note.insert(1, f"`universe_rate` is the same 10-session forward "
                       f"reliability pooled on DAILY bars as of "
                       f"{uni.get('as_of') or 'the pooled run'}, and each row "
                       f"carries the rate for ITS OWN market (see its "
                       f"`scope_label`) — it describes the shape in that "
                       f"market, not these rows, so never present it as a "
                       f"stock's own record and never quote one row's rate "
                       f"for a row in another scope.")
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
                      draw_ids: list | None = None, draw_mode: str = "add",
                      mark_limit: int = 5) -> dict:
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
    # Candlesticks get marked too, and draw_ids addresses chart patterns only,
    # so an explicit id list means "just those" and leaves the candles alone.
    #
    # `cands` is already NEWEST FIRST — patterns.candlesticks sorts on
    # bars_ago ascending — so taking them in order fills the cap with the most
    # recent bars. Do not reverse it: its docstring used to claim "newest
    # last", and the version that believed it marked the OLDEST three while
    # the reply's table listed the newest three. The chart and the text
    # disagreed and neither was obviously wrong to read.
    #
    # The cap is `mark_limit`, an argument with a default — not a slice buried
    # in this loop. It has to be nameable, changeable and REPORTED, or the
    # reply invents a rule to explain why the chart shows fewer than the list.
    cpicked = cands if (draw and not draw_ids) else []
    n_marks = max(0, int(mark_limit if mark_limit is not None else 5))
    if (picked or cpicked) and mode == "replace":
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
            # No dots on the shoulders and head. The outline already turns at
            # each of them — a five-point zigzag IS a left shoulder, a head
            # and a right shoulder, in that order, and naming them with a
            # filled circle adds a mark the user can't grab and doesn't need.
            # A point annotation still exists for the one case that means it:
            # get_indicator's mark_points, where the dot IS the answer.
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

    # ── drawing: a candlestick has no geometry to trace — it IS the bar
    #
    # So the mark points at it from OUTSIDE rather than drawing over it: one
    # dot, above the high, centred on the bar. An outline or a box would hide
    # the very anatomy (body against wick) that made the pattern qualify.
    #
    # What crosses the wire is the bar span and its TRUE high/low, not screen
    # geometry — the client owns the gap in pixels, so the mark stays put
    # through zoom, and stays correct through an interval change that leaves
    # no bar at this exact stamp.
    marks: dict[tuple, dict] = {}
    for c in cpicked:
        i = len(rows) - 1 - c["bars_ago"]
        if not 0 <= i < len(rows):
            continue                       # bars_ago out of range: mark nothing
        seg = rows[max(0, i - max(1, c["bars"]) + 1):i + 1]
        span = (seg[0][0], seg[-1][0])
        name = c["pattern"].replace("_", " ")
        if span in marks:
            # One bar often carries several names — a doji is usually also a
            # long-legged doji. Two dots land on the same pixels and say
            # nothing the first didn't; the label carries both names. This
            # costs no slot: it is the same mark.
            marks[span]["label"] += " · " + name
            c["drawn_as"] = marks[span]["id"]
            continue
        if len(marks) >= n_marks:
            continue                       # cap reached — older bars go unmarked
        cid = "K" + "".join(w[0] for w in c["pattern"].split("_")).upper() \
              + str(c["bars_ago"])
        marks[span] = {
            "kind": "candle", "id": cid, "pane": "price",
            "role": {"bullish": "support", "bearish": "resistance"}.get(
                c["direction"], "neutral"),
            "t1": span[0], "t2": span[1],
            "hi": max(r[2] for r in seg), "lo": min(r[3] for r in seg),
            "label": name,
            "source": {"tool": "get_patterns",
                       "method": "bar anatomy against the disclosed thresholds",
                       "interval": interval, "bars_scanned": len(rows),
                       "strength": "detected",
                       "first_touch": c["t"], "last_touch": c["t"]}}
        c["drawn_as"] = cid
    for m in marks.values():
        _scene_add(m)

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
    if picked or marks:
        res["drawn"] = [p["id"] for p in picked] + [m["id"] for m in marks.values()]
        res["_drawn_note"] = _drawn_ledger()
    if marks:
        shown = sum(1 for c in cands if c.get("drawn_as"))
        res["_marked_note"] = (
            f"{len(marks)} bar(s) marked with a dot above the high, carrying "
            f"{shown} of the {len(cands)} candlestick pattern(s) listed. "
            + (f"The chart shows the {len(marks)} most recent; the older "
               f"{len(cands) - shown} are in `candlesticks` and were NOT "
               "drawn — say so plainly if it matters, and re-call with a "
               f"higher mark_limit (now {n_marks}) to draw more. "
               if shown < len(cands) else "Every pattern found is drawn. ")
            + "Say which bar carries which pattern; the dot is a pointer, "
            "not a finding.")

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
        "window": f"{ist(rows[0][0])} → {ist(rows[-1][0])} {_tzl()}",
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

# Horizons the pooled artifact is computed at. A tool horizon of 13 is
# answered with the 10-bar record and the mismatch is never hidden — see
# `horizon_bars` inside the universe block.
_UNIVERSE_HORIZONS = (5, 10, 20)
# Only the fields the artifact is allowed to surface; anything else in the
# table stays there. Same names as this chart's own result, so the model
# compares like with like instead of two vocabularies.
_UNIVERSE_FIELDS = ("n", "n_symbols", "with_direction_rate_pct",
                    "with_direction_rate_pct_withheld",
                    "control_base_rate_pct", "edge_pp", "edge_se_pp",
                    "avg_move_pct")


def _pattern_universe_stats(kind: str, interval: str, h: int,
                            scope: str = "") -> dict | None:
    """The same evaluation pooled across the stored universe, if it exists.

    Precomputed by an offline artifact into `pattern_stats`; this only
    reads it. The table is tiny and usually absent, so a missing table is
    a plain None (house pattern) and every caller degrades to the
    single-chart answer it already had.

    Scoped to the served symbol's own market. Serving Bitcoin the pooled
    record of 500 NSE stocks would be a fabricated comparison — a shape's
    reliability is a property of the market it was measured in, and there is
    no such thing as "the" base rate across a 375-minute session and a 24/7
    one. A scope with no swept rows returns None, and the caller falls back
    to this chart's own record.
    """
    sc = scope or scope_for(_sym())
    try:
        has_scope = any(r[1] == "scope" for r in
                        _con.execute("PRAGMA table_info(pattern_stats)"))
        if has_scope:
            cur = _con.execute(
                "SELECT * FROM pattern_stats WHERE kind=? AND interval=? "
                "AND horizon=? AND scope=?", (kind, interval, int(h), sc))
        else:
            # pre-migration table: its only content is the equity sweep, so
            # answering a crypto chart from it would be the exact mix this
            # scope column exists to prevent
            if sc != "equity_in":
                return None
            cur = _con.execute(
                "SELECT * FROM pattern_stats WHERE kind=? AND interval=? "
                "AND horizon=?", (kind, interval, int(h)))
        cols = [d[0] for d in cur.description]
        row = cur.fetchone()
        if not row:
            return None
        rec = dict(zip(cols, row))
        as_of = None
        try:
            meta_scoped = any(r[1] == "scope" for r in _con.execute(
                "PRAGMA table_info(pattern_stats_meta)"))
            m = _con.execute(
                "SELECT value FROM pattern_stats_meta WHERE key='as_of' "
                "AND scope=?", (sc,)).fetchone() if meta_scoped else \
                _con.execute("SELECT value FROM pattern_stats_meta "
                             "WHERE key='as_of'").fetchone()
            as_of = m[0] if m else None
        except sqlite3.Error:
            as_of = None
    except sqlite3.Error:
        return None
    out = {k: rec[k] for k in _UNIVERSE_FIELDS
           if rec.get(k) is not None}
    if not out:
        return None
    if out.get("with_direction_rate_pct") is None and \
            "with_direction_rate_pct_withheld" not in out:
        out["with_direction_rate_pct_withheld"] = (
            "the pooled run graded no instance at this horizon — say the "
            "universe record is unavailable, not that the rate is zero")
    out["horizon_bars"] = int(h)
    out["interval"] = interval
    # The scope is not decoration: it is the only thing that says what
    # "across the universe" meant, and the model must name it rather than
    # implying one market's record covers another.
    out["scope"] = sc
    out["scope_label"] = SCOPE_LABEL.get(sc, sc)
    if as_of:
        out["as_of"] = as_of
        lag = _evidence_lag(as_of)
        if lag:
            out["as_of_note"] = lag
    return out


def _evidence_lag(as_of: str | None, symbol: str = "") -> str | None:
    """A note when a DERIVED table's evidence stops before the chart does.

    pattern_stats, vp_screen and the universe screen are SWEPT, not computed
    per request, so each carries its own as_of while the bar store moves
    underneath it independently. Measured 2026-08-02: the equity pattern ledger
    was mined to 22 Jul and the charts had been topped up to 31 Jul, so a
    pooled rate covering neither of the last seven sessions was being shown
    beside a chart that drew all of them, with nothing saying so.

    Stating the lag costs one line. Letting the model imply the evidence covers
    the visible chart is a fabrication, and it is the kind that survives review
    because every individual number in it is true.
    """
    at = _parse_ist(as_of)
    if at is None:
        return None
    sym = symbol or _sym()
    if not sym:
        return None
    try:
        last = _con.execute("SELECT MAX(ts) FROM bars_1d WHERE symbol=?",
                            (sym,)).fetchone()[0]
        if last is None:
            return None
        tz_off = session_for(sym)[1]
        if _ist_day(last, tz_off) <= _ist_day(at, tz_off):
            return None
        behind = _con.execute(
            "SELECT COUNT(*) FROM bars_1d WHERE symbol=? AND ts>?",
            (sym, at)).fetchone()[0]
    except sqlite3.Error:
        return None
    if behind <= 0:
        return None
    return (f"this evidence was mined up to {as_of}; {sym} has traded "
            f"{behind} session(s) since, to {_ist(last, False)}. Say so rather "
            f"than implying the record covers the chart on screen.")


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
            # The same error bar the pooled ledger carries. This path is the
            # one that needed it MOST and did not have it: a single chart
            # grades a handful of instances, not thousands. Measured live,
            # India VIX reported a hammer with a "+22 percentage-point
            # historical edge" off 16 cases — one standard error is 11.6pp
            # there, so the edge was inside the noise and got narrated as a
            # finding. The control term is negligible here (it spans every
            # bar in the window) but is included so both paths agree.
            p = res["with_direction_rate_pct"] / 100
            se = (p * (1 - p) / len(evals)) ** 0.5 * 100
            pc = res["control"]["base_rate_pct"] / 100
            se = (se ** 2 + pc * (1 - pc) / len(base) * 1e4) ** 0.5
            res["edge_se_pp"] = round(se, 1)
            res["edge_verdict"] = (
                "within sampling noise — say the shape is indistinguishable "
                "from its base rate on this chart, and do NOT quote the edge "
                "as a finding"
                if abs(res["edge_pp"]) <= 2 * se else
                "larger than twice its sampling error — a real difference on "
                "this chart, still a historical tendency and not a forecast")
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
        "window": f"{ist(rows[0][0])} → {ist(rows[-1][0])} {_tzl()}",
        "bars_scanned": n,
        "method": "forward close-to-close move measured from each instance's "
                  "completion bar (candles: the pattern bar; chart shapes: "
                  "the confirming break bar)"}
    res["_note"] = (
        "Quote the pattern rate NEXT TO the base rate — the edge is the "
        "difference, and a rate alone is decoration. This is one symbol's "
        "history at one horizon, not a forecast; past instances of a shape "
        "do not obligate the next one."
        + (f" `edge_verdict` decides how this chart's edge may be described: "
           f"{res['edge_verdict']}. An edge inside its error bar must not be "
           f"called an edge, a tendency, or 'modest' — it is a coin flip on "
           f"this many cases. Never present a percentage-point difference "
           f"without saying how many instances produced it."
           if res.get("edge_verdict") else ""))
    uh = min(_UNIVERSE_HORIZONS, key=lambda x: (abs(x - h), x))
    uni = _pattern_universe_stats(k, interval, uh)
    if uni:
        res["universe"] = uni
        # Verified: candle instances are bit-identical between the pooled
        # sweep and this tool; chart shapes are NOT — the sweep detects in
        # rolling 600-bar windows each judged against its own volatility,
        # so shape counts legitimately differ from this single-pass scan.
        same_method = k in patterns.CANDLE_KINDS
        res["_note"] += (
            f" The `universe` block pools {uni.get('n_symbols') or 'the'} "
            f"{uni.get('scope_label', 'stored instruments')} on the same "
            f"{interval} interval, as of "
            f"{uni.get('as_of') or 'the pooled run'}"
            + (f" at a {uh}-bar horizon, the nearest graded to this call's "
               f"{h}" if uh != h else "") + ". "
            + "It covers ONLY that market and that interval — never present "
              "it as the shape's record everywhere, and never carry a rate "
              "measured on one interval across to another. "
            + ("For candlestick kinds it is the SAME method as this chart's "
               "number — quote both scopes and, when they disagree, say so "
               "plainly; the pooled record does not override what this "
               "symbol actually did."
               if same_method else
               "For chart shapes it is a DIFFERENT measurement: formations "
               "found in rolling 600-bar windows, each judged against its "
               "own volatility, while this chart was scanned in one pass — "
               "the two can legitimately count different instances. Present "
               "it as its own scope; never reconcile the counts.")
            + " Each rate must be quoted next to its OWN control (the pooled "
              "control is full-history, this chart's is its window). "
              "Edge-fitted shapes have no universe record at all."
            + (f" `edge_se_pp` is the sampling error on the EDGE — it carries "
               f"both the pattern's own count and the control's, so a thin "
               f"base rate widens it too. This edge of "
               f"{uni.get('edge_pp')}pp is "
               + ("WITHIN it, so call the shape indistinguishable from its "
                  "base rate — do not report it as an edge in either "
                  "direction"
                  if uni.get("edge_pp") is not None
                  and uni.get("edge_se_pp")
                  and abs(uni["edge_pp"]) <= 2 * uni["edge_se_pp"]
                  else f"more than twice it ({uni.get('edge_se_pp')}pp), so "
                       f"it is a real difference — still a small one, and "
                       f"not a forecast")
               + "." if uni.get("edge_se_pp") is not None else ""))
    else:
        # Silence here was the bug behind "why can't it show monthly
        # patterns". The shape IS detected on 1w/1mo — the pooled sweep simply
        # never graded those intervals, and with no block and no explanation
        # the model had nothing to say and hedged the whole answer away. Name
        # the boundary and hand back this chart's own measurement, which is
        # exactly what the detector just produced.
        covered = _pattern_stats_intervals()
        res["universe"] = None
        res["_note"] += (
            " There is NO pooled universe record for this interval"
            + (f" — the sweep graded {', '.join(covered)} only, and this call "
               f"is {interval}" if covered and interval not in covered else "")
            + ". The rates above are THIS chart's own record, measured just "
              "now by the detector, and are the answer — report them. Say the "
              "cross-symbol base rate is unavailable at this interval; do not "
              "imply one exists, and do not withhold the formation itself.")
    return res


def _pattern_stats_intervals() -> list[str]:
    """Which intervals the offline sweep actually graded, for this market.

    Read rather than hardcoded: the sweep is re-run independently of this
    file, and a stale literal here would tell the model a base rate exists
    where it does not.
    """
    try:
        has_scope = any(r[1] == "scope" for r in
                        _con.execute("PRAGMA table_info(pattern_stats)"))
        cur = _con.execute(
            "SELECT DISTINCT interval FROM pattern_stats WHERE scope=?",
            (scope_for(_sym()),)) if has_scope else \
            _con.execute("SELECT DISTINCT interval FROM pattern_stats")
        return sorted(r[0] for r in cur if r[0])
    except sqlite3.Error:
        return []


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
        "window": f"{_ist(rows[0][0], False)} → {_ist(rows[-1][0], False)} {_tzl()}",
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
                                  f"{_ist(rows[-1][0], False)} {_tzl()}"}
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


# ── get_deals: the disclosure record, by client or by symbol ───────────────
#
# Bulk (>0.5% of equity in a day) and block (negotiated window) deals are the
# only place the buyer is named by law. This tool's job is to hand that record
# back FAITHFULLY — it does not score, rank, filter or conclude. Three things
# had to be got right for "faithfully" to be true:
#
#   1. Coverage. Answered off the hydrated copy, "what has this client bought"
#      returns the few symbols that happen to be local and reads as the whole
#      truth. The market sweep is preferred wherever attached, and whichever
#      store answered is stated in the reply.
#   2. Corporate actions. A 2017 RELIANCE deal printed at 1270.25; that same
#      session's adjusted close is 294.70. Dividing today's price by the
#      PRINTED one returns +3% where the truth is +344%. So every return here
#      is close-to-close on the adjusted series, the printed price is labelled
#      as printed, and the gap between them is a field — a corporate action
#      should be visible, not quietly smoothed away.
#   3. Selection. The return is computed for every deal returned, buy and
#      sell alike. Showing it only where it flatters is how a record turns
#      into a track record.
_BLOCK_MIN_CHANGED = "2025-12-07"   # SEBI raised the block floor 10cr -> 25cr


def _deal_clients(query: str, tbl: str) -> list[str]:
    """Raw client strings whose normalised form contains the asked one.

    One legal entity prints under many spellings ("SBI MUTUAL FUND",
    "SBI MUTUAL FUND A/C ..."). Matching the raw string alone silently drops
    rows, and a dropped row is exactly the failure this tool exists to avoid.
    """
    norm = lambda s: "".join(ch for ch in (s or "").upper() if ch.isalnum())  # noqa: E731
    want = norm(query)
    if not want:
        return []
    try:
        return sorted({c for (c,) in _con.execute(
            f"SELECT DISTINCT client FROM {tbl}") if want in norm(c)})
    except sqlite3.Error:
        return []


def tool_get_deals(client: str = "", symbol: str = "", frm: str = "",
                   to: str = "", limit: int = 40) -> dict:
    """The bulk/block deal record — who traded, when, how much, at what price."""
    tbl = "mkt.deals" if _HAVE_MKT else "deals"
    sym = (symbol or "").strip().upper()
    where, args = [], []
    out: dict = {}

    if client:
        names = _deal_clients(client, tbl)
        if not names:
            return {"error": f"no client matching {client!r} in the deal record",
                    "_read": "Say no deal is recorded under that name — do not "
                             "guess at who they are or what they hold."}
        where.append(f"client IN ({','.join('?' * len(names))})")
        args += names
        out["matched_client_names"] = names[:25]
        if len(names) > 25:
            out["matched_client_names_note"] = f"{len(names) - 25} more folded in"
    if sym or not client:
        where.append("symbol=?")
        args.append(sym or _sym())
    d0 = _iso_day(t) if (t := _parse_ist(frm)) else ""
    d1 = _iso_day(t) if (t := _parse_ist(to)) else ""
    if (frm and not d0) or (to and not d1):
        return {"error": "could not read the date(s)",
                "hint": "chart format, e.g. '01 Jul 2026'"}
    if d0:
        where.append("d>=?")
        args.append(d0)
    if d1:
        where.append("d<=?")
        args.append(d1)

    n = max(1, min(int(limit or 40), 200))
    try:
        # Two passes, and both earn their place.
        #   DISTINCT first: `deals` carries no primary key and a handful of
        #   rows are exact repeats; a double-counted deal is a wrong answer.
        #   GROUP BY second: one disclosed purchase arrives as many legs (SBI
        #   MF's 18-Jun ANTHEM buy is 7 rows at one price). Handed the legs,
        #   the model added them up itself and published 2,894,500 against a
        #   true 2,694,616 — a fabricated quantity on the one surface whose
        #   entire purpose is fidelity. Legs are summed HERE, in SQL, and the
        #   count rides along so nothing is hidden by the folding.
        rows = _con.execute(
            f"SELECT d, symbol, kind, client, side, SUM(qty), "
            f"       SUM(qty*price)/NULLIF(SUM(qty),0), COUNT(*) "
            f"FROM (SELECT DISTINCT d, symbol, kind, client, side, qty, price "
            f"      FROM {tbl} WHERE {' AND '.join(where)}) "
            f"GROUP BY d, symbol, kind, client, side "
            f"ORDER BY d DESC, SUM(qty*price) DESC LIMIT ?",
            (*args, n + 1)).fetchall()
    except sqlite3.Error as exc:
        return {"error": f"deal record unreadable: {exc}"}
    if not rows:
        return {"error": "no deals on record for that scope",
                "_read": "Say plainly that none is recorded — silence here "
                         "means none was PRINTED, not that none happened. "
                         "Only deals crossing the disclosure threshold appear."}
    more, rows = len(rows) > n, rows[:n]

    # closes come from the adjusted daily series, never from the printed price
    closes: dict[str, dict] = {}
    for s in {r[1] for r in rows}:
        try:
            closes[s] = {_iso_day(ts): c for ts, c in _con.execute(
                "SELECT ts, c FROM bars_1d WHERE symbol=? ORDER BY ts", (s,))}
        except sqlite3.Error:
            closes[s] = {}

    deals, blocks, days = [], [], []
    for d, s, kind, cl, side, qty, px, legs in rows:
        val = (qty or 0) * (px or 0)
        rec = {"date": d, "symbol": s, "type": kind, "client": cl,
               "side": side, "qty": qty, "price_as_printed": px,
               "value_cr": round(val / 1e7, 2)}
        if legs > 1:
            rec["legs"] = legs
            rec["price_as_printed"] = round(px, 2) if px else px
            rec["_legs_note"] = (f"{legs} disclosed legs, already summed here; "
                                 f"price is quantity-weighted. Do not re-add.")
        by_day = closes.get(s) or {}
        c0 = by_day.get(d)
        if c0:
            last_d = max(by_day)
            rec["close_on_date_adjusted"] = round(c0, 2)
            if px:
                rec["printed_vs_close_pct"] = round((px / c0 - 1) * 100, 1)
            if last_d > d:
                rec["return_close_to_latest_pct"] = round(
                    (by_day[last_d] / c0 - 1) * 100, 1)
                rec["latest_close_on"] = last_d
        else:
            rec["close_withheld"] = ("no local daily bar for this symbol on "
                                     "this date — no return can be quoted")
        if kind == "block":
            blocks.append(d)
        days.append(d)
        deals.append(rec)

    out["deals"] = deals
    out["scope"] = {
        "client": client or "any", "symbol": sym or (None if client else _sym()),
        "from": d0 or "earliest on record", "to": d1 or "latest on record",
        "returned": len(deals), "more_exist": more,
        "source": ("market-wide deal sweep" if _HAVE_MKT
                   else "LOCAL deal copy — hydrated symbols only, so a "
                        "client's record here is INCOMPLETE; say so")}

    if client:
        try:
            tot, gross, net, syms, lo, hi = _con.execute(
                f"SELECT COUNT(*), SUM(qty*price), "
                f"SUM(CASE WHEN side='BUY' THEN qty*price ELSE -qty*price END), "
                f"COUNT(DISTINCT symbol), MIN(d), MAX(d) FROM "
                f"(SELECT DISTINCT d, symbol, kind, client, side, qty, price "
                f" FROM {tbl} WHERE client IN ({','.join('?' * len(names))}))",
                names).fetchone()
            # over the WHOLE record, never the returned page: this block reads
            # as a property of everything the client has done, and a share
            # measured on one page of it would be a quietly wrong number.
            sd, both = _con.execute(
                f"SELECT COUNT(*), SUM(CASE WHEN n>1 THEN 1 ELSE 0 END) FROM "
                f"(SELECT COUNT(DISTINCT side) n FROM "
                f" (SELECT DISTINCT d, symbol, kind, client, side, qty, price "
                f"  FROM {tbl} WHERE client IN ({','.join('?' * len(names))})) "
                f" GROUP BY symbol, d)", names).fetchone()
            rc = {"deals_on_record": tot, "distinct_symbols": syms,
                  "first": lo, "last": hi,
                  "gross_traded_cr": round((gross or 0) / 1e7),
                  "net_cr": round((net or 0) / 1e7)}
            if gross:
                rc["net_as_pct_of_gross"] = round(abs(net or 0) / gross * 100, 1)
            rc.update(_rate("both_sides_same_day_pct", both or 0,
                            (sd or 0) - (both or 0), "symbol-day"))
            rc["_read"] = (
                "Properties of this client's OWN record, not a judgement on "
                "it. A near-zero net against a large gross, with both sides "
                "traded on most symbol-days, is what a market maker's record "
                "looks like; a large net across few deals is what a "
                "one-directional buyer's does. State the numbers and let the "
                "reader draw that line — do not label the client, and do not "
                "leave the numbers out, which would read as endorsement.")
            out["client_record"] = rc
        except sqlite3.Error:
            pass

    notes = []
    # only a comparison ACROSS the change can be misled by it, so the warning
    # rides on the returned block deals straddling that date — not on merely
    # having one.
    if blocks and min(days) < _BLOCK_MIN_CHANGED <= max(days):
        notes.append(
            f"SEBI raised the block-deal floor from Rs10cr to Rs25cr on "
            f"{_BLOCK_MIN_CHANGED} (band +/-1% -> +/-3%). Block counts and "
            f"sizes are NOT comparable across that date; flow also shifted "
            f"into bulk. Say so before comparing periods that span it.")
    notes.append(
        "price_as_printed is the raw traded price, unadjusted for splits and "
        "bonuses; close_on_date_adjusted is the same session on the adjusted "
        "series. A large printed_vs_close_pct is a corporate action since the "
        "deal, not an error — never divide today's price by the printed one.")
    out["data_notes"] = notes
    out["_read"] = (
        "This is a DISCLOSURE record: report what was traded, by whom, when, "
        "and at what price, and stop there. Deals are published after market "
        "hours on the trade date, so the market had the session before anyone "
        "could read them. Do NOT turn a deal into a recommendation, do not "
        "call it accumulation or distribution, do not infer intent, conviction "
        "or a view from it, and do not imply the reader should follow it. "
        "Where return_close_to_latest_pct is present it is part of the record "
        "and belongs in the answer — SHOW it, for every deal that has one, "
        "winners and losers alike, and say 'not available' for the rest. "
        "Withholding it wherever it looks unflattering is the one way this "
        "table can lie while every number in it stays true. It is arithmetic "
        "on public closes between two stated dates: not a track record, not a "
        "forecast, and not a verdict on the client. Never add up qty or value "
        "across rows yourself — a summed row already says so in `legs`, and "
        "totals not present here must not be published. When more_exist is "
        "true say these are the most recent N, not that they are all of them. "
        "Only trades crossing the disclosure threshold appear at all, so this "
        "is never the whole of what an investor did.")
    return out


# ── voice: a spoken question becomes a typed one ────────────────────────────
#
# Pivot's chain, unchanged, on Charto's server (pivot/backend/routers/audio.py):
# the browser records an opus/aac blob, Azure Speech fast-transcription turns
# it into text, and a transcript that comes back in Devanagari is rendered to
# English by the same deployment that answers the chat. Hinglish written in
# Latin script passes straight through — the agent reads it natively.
#
# Same resource, same credential: the Foundry AI-Services account behind
# AZURE_ENDPOINT bundles Speech on the SAME key, at the host with the /openai
# path stripped. There is no second key to provision and no audio deployment
# to create — the Foundry /openai audio route has none, which is exactly why
# this endpoint exists.
_SPEECH_PATH = "/speechtotext/transcriptions:transcribe"
_SPEECH_API_VERSION = "2024-11-15"
# Per-segment language id, so a sentence that flips mid-way still comes back
# whole.
_SPEECH_LOCALES = ["en-IN", "hi-IN"]
# MediaRecorder voice is ~1 KB/s; a 60 s clip is well under 1 MB. This rejects
# a runaway upload without ever touching a real recording.
_AUDIO_MAX_BYTES = 15 * 1024 * 1024
_DEVANAGARI_RE = re.compile(r"[ऀ-ॿ]")


def _speech_host() -> str:
    """The AI-Services host carrying the Speech APIs — the chat endpoint
    without its path."""
    p = urlparse(AZURE_ENDPOINT or "")
    return f"{p.scheme}://{p.netloc}" if p.netloc else ""


def _multipart(fields: list) -> tuple[bytes, str]:
    """A multipart/form-data body. Written out rather than pulled in: this is
    the only upload the server makes, and `requests` is not a dependency."""
    boundary = "----charto" + secrets.token_hex(12)
    out = bytearray()
    for name, filename, ctype, payload in fields:
        out += f"--{boundary}\r\n".encode()
        disp = f'form-data; name="{name}"'
        if filename:
            disp += f'; filename="{filename}"'
        out += f"Content-Disposition: {disp}\r\n".encode()
        if ctype:
            out += f"Content-Type: {ctype}\r\n".encode()
        out += b"\r\n" + payload + b"\r\n"
    out += f"--{boundary}--\r\n".encode()
    return bytes(out), f"multipart/form-data; boundary={boundary}"


def transcribe(data: bytes, content_type: str = "") -> dict:
    """Spoken audio → text. Errors are returned, never raised: a failed
    transcription must leave the composer exactly as the user left it."""
    host = _speech_host()
    if not host or not AZURE_KEY:
        return {"error": "voice input is not configured on this server"}
    if not data:
        return {"error": "empty recording"}
    if len(data) > _AUDIO_MAX_BYTES:
        return {"error": "recording too large (max 15 MB)"}
    body, ctype = _multipart([
        ("audio", "recording.webm", content_type or "audio/webm", data),
        ("definition", None, "application/json",
         json.dumps({"locales": _SPEECH_LOCALES}).encode()),
    ])
    req = urllib.request.Request(
        f"{host}{_SPEECH_PATH}?api-version={_SPEECH_API_VERSION}",
        data=body,
        headers={"Ocp-Apim-Subscription-Key": AZURE_KEY, "Content-Type": ctype},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60, context=_ssl_ctx()) as r:
            out = json.loads(r.read().decode())
    except Exception as exc:                                # noqa: BLE001
        # Never relay the upstream body — it can echo resource ids.
        logging.warning("charto transcribe failed: %s", exc)
        return {"error": "the speech service rejected the audio — try again"}
    text = " ".join(p.get("text", "")
                    for p in (out.get("combinedPhrases") or [])).strip()
    if not text:
        return {"error": "couldn't hear anything — try again"}
    provider = "azure-speech"
    # Devanagari only. Latin-script Hinglish is left alone: the agent reads it
    # as spoken, and a round trip through translation would flatten it.
    if _DEVANAGARI_RE.search(text):
        englished = _translate_to_english(text)
        if englished and englished != text:
            text, provider = englished, "azure-speech+llm"
    return {"text": text, "provider": provider}


_TRANSLATE_SYSTEM = (
    "You translate Indian-language voice queries into English for a stock-"
    "market charting app. Return ONLY the English translation — no preamble, "
    "no quotes. Keep company names, tickers, and numbers exactly as spoken.")


def _translate_to_english(text: str) -> str:
    """Degrade, never fail: the chat agent reads Hindi, so a translation that
    does not come back must not sink the voice turn."""
    try:
        payload = {"model": LLM_DEPLOYMENT, "input": [
            {"role": "system", "content": _TRANSLATE_SYSTEM},
            {"role": "user", "content": text}],
            "max_output_tokens": 300, "reasoning": {"effort": "minimal"}}
        req = urllib.request.Request(
            f"{AZURE_ENDPOINT}/responses", data=json.dumps(payload).encode(),
            headers={"api-key": AZURE_KEY, "Content-Type": "application/json"},
            method="POST")
        with urllib.request.urlopen(req, timeout=30, context=_ssl_ctx()) as r:
            data = json.loads(r.read().decode())
        for item in data.get("output", []):
            for part in item.get("content", []) or []:
                if part.get("type") == "output_text" and part.get("text"):
                    return str(part["text"]).strip()
    except Exception as exc:                                # noqa: BLE001
        logging.warning("charto transcribe translate failed: %s", exc)
    return text


# ── open_chart: the model arranges the workspace itself ────────────────────
#
# Every other tool READS a chart the user opened. This one puts one on screen.
# It is deliberately the only tool that does, and it validates before it
# promises: a symbol that will not hydrate must fail HERE, as a refusal the
# model can relay, rather than as a pane that opens empty on the user's screen
# while the reply says the chart is ready.
_OPEN_INTERVALS = {"1m", "5m", "15m", "30m", "1h", "1d", "1w", "1mo"}


def tool_open_chart(symbol: str = "", interval: str = "", replace: bool = False,
                    layout: str = "") -> dict:
    sym = (symbol or "").strip().upper()
    if not sym:
        return {"error": "which symbol should the chart show?"}
    iv = (interval or "").strip() or "1d"
    if iv not in _OPEN_INTERVALS:
        return {"error": f"unknown interval {interval!r}",
                "allowed": sorted(_OPEN_INTERVALS)}
    if err := _ensure_symbol(sym):
        return err          # the pane is never opened on a symbol with no bars
    op = {"kind": "open_chart", "symbol": sym, "interval": iv,
          "replace": bool(replace)}
    if layout:
        op["layout"] = layout
    _view_add(op)
    # A chart the model just opened IS a chart in this conversation, so it
    # joins the list the symbol gate reads. Without this, "open TCS and tell me
    # its RSI" opened the pane and then refused to read it — the gate is there
    # to stop a ticker drifting in from stale transcript, not to stop the model
    # reading what it deliberately put on screen this turn.
    if sym not in (getattr(_req, "charts", None) or []):
        _req.charts = (getattr(_req, "charts", None) or []) + [sym]
    # Which of the two placements you chose decides whether anything can ever
    # be drawn on the result, and nothing here used to say so: an added pane is
    # a reference chart with no drawing layer, and "opened" read as done to a
    # model that had just promised to draw.
    on_main = bool(getattr(_req, "drawable", True))
    if replace and on_main:
        placement = (f"swapped the main chart — {sym} IS the main chart now, "
                     f"so it is drawable")
        read = ("The workspace RELOADS onto this chart, so this must be the "
                "last thing you do this turn: say the chart is open and that "
                "you can mark levels on it when asked. Do not draw in this "
                "same turn — the reload would discard the drawing while your "
                "reply claimed it landed.")
    elif replace:
        placement = ("swapped the focused reference pane; the main chart is "
                     "unchanged, so this chart still cannot be drawn on")
        read = ("A reference pane has no drawing layer. If the user wants "
                "something drawn here, say the chart would have to be opened "
                "as the main chart, and that selecting the main chart first is "
                "what makes that possible.")
    else:
        placement = ("added a REFERENCE pane; the layout grows to fit. It can "
                     "be read but never drawn on")
        read = ("Reading it works; drawing on it does not — only the main "
                "chart carries drawings. If the ask was to DRAW something for "
                "this symbol, this was not the call: it has to become the main "
                "chart (open_chart replace=true while the main chart is in "
                "focus). Say that plainly rather than reporting it as drawn.")
    return {"opened": sym, "interval": iv, "placement": placement,
            "drawable": bool(replace and on_main),
            "main_chart": str(getattr(_req, "main_chart", "") or ""),
            "_read": "The chart is now on screen. Say so in a few words and "
                     "answer whatever was actually asked — do not describe the "
                     "layout mechanics, and do not claim to see anything on it "
                     "that you have not read with a tool. " + read}


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
_NEWS_TTL_OVERHANG = 86400        # open conditions evolve daily, not hourly
_NEWS_TTL_UPCOMING = 43200        # calendars move slower still

# A cause is an interval, not a point: a succession question opened on the
# 18th is still pressing on the 22nd, but a "what happened on the 22nd"
# search will never rank the 18th's article. So the full scope asks three
# differently-shaped questions concurrently — dated catalysts, OPEN
# conditions, and scheduled anticipations — and the cache for the two new
# legs is keyed by symbol alone: market truth, shared by every session,
# refreshed by TTL rather than re-discovered per window.

_NEWS_OVERHANG_PROMPT = (
    "You are a research clerk for an Indian equities chart (NSE: {symbol}). "
    "Search the web once — twice only if the first search returns nothing — "
    "for company-specific situations that were OPEN AND UNRESOLVED as of "
    "{window}, regardless of when they began: leadership or succession "
    "questions, regulatory approvals awaited, legal or tax disputes, rating "
    "watches, deal or merger uncertainty, guidance doubts. Reply with 1-4 "
    "lines, each 'origin date · the open situation and its state as of "
    "{window} · source domain'. Only situations with a dated origin and a "
    "source. Do NOT report prices, returns, percentages or targets. If "
    "nothing is genuinely open, reply exactly: no open overhangs found.")

_NEWS_UPCOMING_PROMPT = (
    "You are a research clerk for an Indian equities chart (NSE: {symbol}). "
    "Search the web once — twice only if the first search returns nothing — "
    "for scheduled events shortly AFTER {window} that investors position "
    "around: board meetings, results dates, ex-dividend or record dates, "
    "regulatory decisions due. Reply with 1-3 lines, each 'date · scheduled "
    "event · source domain'. Only dated, sourced items; no prices or "
    "estimates. If nothing is scheduled, reply exactly: nothing scheduled.")

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


def _news_cache_get(key: str, ttl: int | None = None) -> dict | None:
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
    age = _t.time() - fetched
    if ttl is not None:
        if age > ttl:
            return None
    elif recent and age > _NEWS_TTL_RECENT:
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


def _news_browse(prompt: str) -> tuple[str, list, int] | dict:
    """One isolated clerk browse. Returns (body, sources, searched) or an
    error dict — the caller decides how a dead leg degrades."""
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
        return {"error": f"web lookup failed: {exc}"}
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
    return "".join(text).strip(), sources, searched


def _news_leg(key: str, ttl: int, prompt: str, empty: str,
              field: str) -> tuple[str, list, bool]:
    """(text, sources, cached) for one cached clerk leg; degrades honestly."""
    hit = _news_cache_get(key, ttl)
    if hit:
        return hit.get(field, empty), hit.get("sources", []), True
    got = _news_browse(prompt)
    if isinstance(got, dict):
        return f"web lookup unavailable for this section ({got['error']})", [], False
    body, sources, searched = got
    text = body or empty
    if searched and body:
        _news_cache_put(key, True, {field: text, "sources": sources})
    return text, sources, False


def tool_search_news(frm: str = "", to: str = "", focus: str = "",
                     scope: str = "events") -> dict:
    """Outside causes for a window — an isolated browse, cached.

    scope='events': dated happenings in the window (point catalysts).
    scope='full': three differently-shaped questions asked CONCURRENTLY —
    dated events, OPEN unresolved company situations, and scheduled
    upcoming events — merged into one result. One tool call, one hop;
    wall time is the slowest leg, not the sum.
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
    sym = _sym()

    import time as _t
    recent = (_t.time() - t1) < _NEWS_RECENT_DAYS * 86400
    ev_key = f"{sym}|{d0}|{d1}"
    ev_prompt = _NEWS_PROMPT.format(
        symbol=sym, window=window,
        focus=f" Particular focus: {focus.strip()}." if focus.strip() else "")

    base_note = ("These are candidate CAUSES only — events with dates. "
                 "Every quantity (price, %, volume, level) must come from "
                 "the chart tools; if a headline implies a number, use the "
                 "tool's number. An event here explains the move only if "
                 "its timing fits the anatomy (a mid-session move is not "
                 "explained by overnight news).")

    if scope != "full":
        cached = _news_cache_get(ev_key)
        if cached:
            return {**cached, "cached": True}
        got = _news_browse(ev_prompt)
        if isinstance(got, dict):
            return {**got, "_note": "Answer from the local evidence and say "
                    "the web lookup was unavailable — do not guess at news."}
        body, sources, searched = got
        out = {"window": window,
               "events": body or "nothing found for this window",
               "sources": sorted(set(sources))[:6], "_note": base_note}
        if searched and body:
            _news_cache_put(ev_key, recent, out)
        return out

    # full scope: three legs, concurrent, each with its own cache and its
    # own honest failure line — a dead leg never sinks the others
    from concurrent.futures import ThreadPoolExecutor

    def ev_leg() -> tuple[str, list, bool]:
        hit = _news_cache_get(ev_key)
        if hit:
            return hit.get("events", ""), hit.get("sources", []), True
        got = _news_browse(ev_prompt)
        if isinstance(got, dict):
            return (f"web lookup unavailable for this section "
                    f"({got['error']})", [], False)
        body, sources, searched = got
        text = body or "nothing found for this window"
        if searched and body:
            _news_cache_put(ev_key, recent, {
                "window": window, "events": text,
                "sources": sorted(set(sources))[:6], "_note": base_note})
        return text, sources, False

    with ThreadPoolExecutor(3) as ex:
        f_ev = ex.submit(ev_leg)
        f_oh = ex.submit(_news_leg, f"{sym}|overhangs", _NEWS_TTL_OVERHANG,
                         _NEWS_OVERHANG_PROMPT.format(symbol=sym, window=window),
                         "no open overhangs found", "open_overhangs")
        f_up = ex.submit(_news_leg, f"{sym}|upcoming", _NEWS_TTL_UPCOMING,
                         _NEWS_UPCOMING_PROMPT.format(symbol=sym, window=window),
                         "nothing scheduled", "upcoming")
    ev_t, ev_s, ev_c = f_ev.result()
    oh_t, oh_s, oh_c = f_oh.result()
    up_t, up_s, up_c = f_up.result()

    return {
        "window": window,
        "events": ev_t,
        "open_overhangs": oh_t,
        "upcoming": up_t,
        "sources": sorted(set(ev_s + oh_s + up_s))[:8],
        "cached_legs": [n for n, c in
                        (("events", ev_c), ("overhangs", oh_c),
                         ("upcoming", up_c)) if c],
        "_note": base_note + (
            " Open overhangs are CONDITIONS, not events: they explain "
            "multi-day drift, persistent weakness and levels — never a "
            "sharp intraday move; quote each with its origin date. "
            "Upcoming items explain positioning ahead of them, not past "
            "moves. Say which shape of cause fits what the chart shows."),
    }


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
        # The floor is PER SYMBOL now that the store holds more than NSE
        # equities — crypto starts 2015-07-20 (BTC) or 2021 (Bybit pairs), a
        # listed future only a few months back. A hardcoded 2015-02-02 sent
        # the model to argue with a user about a window that never existed.
        lo = _con.execute("SELECT MIN(ts) FROM bars WHERE symbol=?",
                          (_sym(),)).fetchone()[0]
        closes = ("this symbol trades 24/7" if session_for(_sym()) == UTC_SESSION
                  else "markets are closed on weekends and holidays")
        return {"error": "no bars in that range",
                "hint": (f"{_sym()} history starts {_ist(lo)}; {closes}"
                         if lo else f"no bars stored for {_sym()} at all")}
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
                       source: str = "", mult: float = 0,
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
    # "" means the indicator's own default column (hlc3 for CCI and MFI), so
    # a call that simply omits the argument still gets the textbook formula
    if source and source not in indicators.SOURCES:
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
                        "scanned": f"{_ist(rows[0][0])} → {_ist(rows[-1][0])} {_tzl()}"}
            extra["anchor_index"] = idx

    try:
        res = indicators.compute(name, rows, period, source, **extra)
    except ValueError as exc:
        err: dict = {"error": str(exc)}
        if indicators.SPECS.get(name, {}).get("group") == "volume":
            err["_note"] = (
                "Nothing was computed. Say plainly that this instrument "
                "publishes no traded volume so the study has no input — do "
                "NOT report a number, and do not substitute a different "
                "indicator without saying you switched. Offer a price-only "
                "one by name instead.")
        return err

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
    {"type": "function", "name": "open_chart",
     "description": "Put a chart on the user's screen yourself. Use when the answer is about an instrument that is NOT already open — 'show me TCS', 'pull up the Nifty', 'compare this with HDFCBANK', 'open it on the daily' — and when a follow-up is clearly about a different symbol than the one in focus. Opening ADDS a reference pane and the layout grows to fit; pass replace=true to change what the focused chart shows instead of adding another. DRAWABILITY decides which one you want: an added pane can be read but NEVER drawn on, because only the main chart carries drawings. So if the user wants levels, marks or a plan DRAWN for a symbol that is not the main chart, the only route is replace=true while the main chart is in focus — that makes the symbol the main chart (the workspace reloads onto it, so end the turn there and draw when next asked). The symbol is validated before the pane opens, so a bad ticker fails here rather than opening an empty chart. Opening a chart does NOT read it: call the reading tools afterwards for anything you intend to say about it.",
     "parameters": {"type": "object", "properties": {
         "symbol": {"type": "string", "description": "ticker to open, e.g. 'TCS'"},
         "interval": {"type": "string", "enum": ["1m", "5m", "15m", "30m", "1h", "1d", "1w", "1mo"], "description": "default 1d"},
         "replace": {"type": "boolean", "description": "true = swap the focused chart's symbol instead of adding a pane"}},
      "required": ["symbol"]}},
    {"type": "function", "name": "get_deals",
     "description": "The bulk/block deal DISCLOSURE record — who traded a stock, on what date, how much, and at what price, as published by the exchange. Bulk and block deals are the only place the counterparty is named by law. Two axes: pass client to follow ONE named entity across every stock it has traded ('what has SBI Mutual Fund bought', 'has GQG been selling', 'show me Fidelity's deals'), or omit client to get the deals printed on the current chart's symbol ('any big deals in this name', 'who bought this stock'). Client names are matched loosely across spelling variants and every variant folded in is listed back. Returns each deal with its printed price, that session's split/bonus-adjusted close, and the close-to-latest return, computed for every deal shown, buys and sells alike. This tool REPORTS the record; it does not rank, score or filter it, and a deal is never a recommendation.",
     "parameters": {"type": "object", "properties": {
         "client": {"type": "string", "description": "name or fragment of the trading entity, e.g. 'SBI Mutual Fund', 'GQG', 'Fidelity'"},
         "symbol": {"type": "string", "description": "restrict to one symbol; omit to use the current chart's symbol when no client is given"},
         "frm": {"type": "string", "description": "window start, chart format e.g. '01 Jul 2026'"},
         "to": {"type": "string", "description": "window end"},
         "limit": {"type": "integer", "description": "deals returned, default 40, max 200"}},
      "required": []}},
    {"type": "function", "name": "search_news",
     "description": "Dated outside events for a window — filings, analyst actions, sector/market/macro causes named by the press. When the question itself already demands outside causes ('why did it fall', 'what news moved it'), call this IN THE SAME ROUND as explain_move — batching the two saves a full inference hop, and the search covers company, sector and market angles either way. Call it only after explain_move when you genuinely cannot tell yet whether the move needs a cause at all. At most one search_news call per turn. Returns events with dates and sources, never numbers.",
     "parameters": {"type": "object", "properties": {
         "frm": {"type": "string", "description": "window start, e.g. '21 Jul 2026'"},
         "to": {"type": "string", "description": "window end; omit for one day"},
         "focus": {"type": "string", "description": "optional angle, e.g. 'market-wide selloff cause' or 'company filings'"},
         "scope": {"type": "string", "enum": ["events", "full"],
                   "description": "'events' (default): dated happenings in the window. 'full': ALSO scans for open unresolved company situations (leadership, regulatory, legal, deals) and scheduled upcoming events, concurrently at no extra latency — use it for open-ended asks ('what does the news suggest', 'why is it weak lately') and whenever the day's events fail to explain the move."}},
      "required": ["frm"]}},
    {"type": "function", "name": "set_alert",
     "description": (
         "Arm a server-side alert on the chart's instrument. Use it whenever the "
         "user asks to be TOLD or NOTIFIED about something — 'tell me if', 'let "
         "me know when', 'alert me', 'watch for', or an intent that plainly "
         "means it ('I'm out for the day, don't want to miss the breakout'). It "
         "notifies only; it never places an order. The alert is a COMPOSED "
         "expression: a list of conditions, each `left <op> right`, where both "
         "sides are ADDRESSES from the list below, resolved against the real "
         "bars. `x` multiplies the right side and `plus_pct` offsets it, so "
         "'volume above twice its average' is {left:'volume', op:'above', "
         "right:'avg(volume,20)', x:2}. Several conditions with all=true is an "
         "AND — that is how a confirmed breakout is expressed in one alert. "
         "A VAGUE ask is still an alert: read the vague word against the chart "
         "rather than refusing it or inventing a number. 'Breaks out' means a "
         "level you got from get_levels, not one you chose; 'oversold' is the "
         "indicator's own convention; 'dumps' or 'takes off' is a percent move "
         "or a session extreme. Ask only when the ask has no defensible "
         "reading — and when you do ask, offer concrete options priced off this "
         "chart. If an address or op is wrong the engine refuses and hands back "
         "the whole grammar; re-call with the names it lists rather than "
         "guessing again."),
     "parameters": {"type": "object", "properties": {
         "symbol": {"type": "string", "description": "defaults to the chart in focus"},
         "interval": {"type": "string",
                      "enum": ["1m", "3m", "5m", "15m", "30m", "1h", "1d"],
                      "description": "the bars the rule is evaluated on; also what 'per bar' means"},
         "when": {"type": "array", "description": "1-4 conditions",
                  "items": {"type": "object", "properties": {
                      "left": {"type": "string", "description": "an address, e.g. 'close' or 'rsi(14)'"},
                      "op": {"type": "string",
                             "enum": ["cross", "cross_up", "cross_down", "above",
                                      "below", "rises_pct", "falls_pct",
                                      "changes_pct", "enters", "exits", "is_true"]},
                      "right": {"description": "a number, or an address; omit for is_true"},
                      "right2": {"description": "the band's other edge, for enters/exits"},
                      "x": {"type": "number", "description": "multiply the right side"},
                      "plus_pct": {"type": "number", "description": "offset the right side by a percentage"},
                      "within": {"type": "integer", "description": "bars, for the _pct ops"}},
                      "required": ["left", "op"]}},
         "all": {"type": "boolean", "description": "true (default) = AND, false = OR"},
         "freq": {"type": "string",
                  "enum": ["once", "per_bar", "per_bar_close", "per_day"],
                  "description": "'once' (default) fires a single time on the forming bar; 'per_bar_close' waits for the confirmed close, which is the honest choice for an indicator or a setup"},
         "expires_in_days": {"type": "integer", "description": "0 = open-ended"},
         "note": {"type": "string", "description": "why the user is watching it"}},
      "required": ["when"]}},
    {"type": "function", "name": "check_alert",
     "description": (
         "Resolve an alert expression against the real bars WITHOUT arming it. "
         "Takes exactly what set_alert takes and returns, per condition, the "
         "value observed right now beside the target as resolved, plus whether "
         "the rule is already true. Two uses: answering 'where is it now "
         "relative to that?', and proving an address resolves before you "
         "promise the user it is being watched. Prefer it over guessing when "
         "the expression is unusual — a refusal here is a sentence in the "
         "conversation, a refusal at 09:20 is an alert that never fired. It "
         "also works signed out, so an expression can be shown to someone who "
         "has not made an account yet."),
     "parameters": {"type": "object", "properties": {
         "symbol": {"type": "string", "description": "defaults to the chart in focus"},
         "interval": {"type": "string",
                      "enum": ["1m", "3m", "5m", "15m", "30m", "1h", "1d"]},
         "when": {"type": "array", "description": "the same conditions set_alert takes",
                  "items": {"type": "object", "properties": {
                      "left": {"type": "string"},
                      "op": {"type": "string",
                             "enum": ["cross", "cross_up", "cross_down", "above",
                                      "below", "rises_pct", "falls_pct",
                                      "changes_pct", "enters", "exits", "is_true"]},
                      "right": {"description": "a number, or an address; omit for is_true"},
                      "right2": {"description": "the band's other edge, for enters/exits"},
                      "x": {"type": "number"},
                      "plus_pct": {"type": "number"},
                      "within": {"type": "integer"}},
                      "required": ["left", "op"]}},
         "all": {"type": "boolean", "description": "true (default) = AND, false = OR"}},
      "required": ["when"]}},
    {"type": "function", "name": "list_alerts",
     "description": ("The user's own alerts and the most recent things that "
                     "fired, with the value each one actually saw. Use it for "
                     "'what am I watching', 'did anything trigger', or before "
                     "updating or cancelling one so the id is real. Narrow with "
                     "`symbol` or `state` when the question is about one chart "
                     "or only the paused ones. `mark_seen` clears the bell's "
                     "unseen count — pass it only when the user is actually "
                     "acknowledging the fires, never merely to look."),
     "parameters": {"type": "object", "properties": {
         "symbol": {"type": "string", "description": "restrict to one instrument"},
         "state": {"type": "string", "enum": ["armed", "paused", "fired"]},
         "mark_seen": {"type": "boolean",
                       "description": "mark fired entries read (clears the bell)"}}}},
    {"type": "function", "name": "update_alert",
     "description": (
         "Change an alert that already exists: pause it (state='paused'), put "
         "it back to work (state='armed'), or rewrite what it watches. Pass "
         "only the fields that change — the others are left alone. Re-arming "
         "re-seeds the crossing side against the current bar, so a re-armed "
         "rule watches from now rather than firing on a move it slept through. "
         "`when` REPLACES the condition list rather than merging into it, so "
         "send every condition the rule should end up with. Prefer pausing to "
         "cancelling when the user may want it back: a delete is final."),
     "parameters": {"type": "object", "properties": {
         "alert_id": {"type": "integer"},
         "state": {"type": "string", "enum": ["armed", "paused"],
                   "description": "'fired' is a state the engine sets, never you"},
         "freq": {"type": "string",
                  "enum": ["once", "per_bar", "per_bar_close", "per_day"]},
         "interval": {"type": "string",
                      "enum": ["1m", "3m", "5m", "15m", "30m", "1h", "1d"]},
         "when": {"type": "array", "description": "the complete new condition list",
                  "items": {"type": "object", "properties": {
                      "left": {"type": "string"},
                      "op": {"type": "string",
                             "enum": ["cross", "cross_up", "cross_down", "above",
                                      "below", "rises_pct", "falls_pct",
                                      "changes_pct", "enters", "exits", "is_true"]},
                      "right": {"description": "a number, or an address; omit for is_true"},
                      "right2": {"description": "the band's other edge, for enters/exits"},
                      "x": {"type": "number"},
                      "plus_pct": {"type": "number"},
                      "within": {"type": "integer"}},
                      "required": ["left", "op"]}},
         "all": {"type": "boolean", "description": "true = AND, false = OR"},
         "expires_in_days": {"type": "integer", "description": "0 clears the expiry"},
         "note": {"type": "string", "description": "why the user is watching it"}},
      "required": ["alert_id"]}},
    {"type": "function", "name": "cancel_alert",
     "description": ("Delete one alert by id, permanently. Call list_alerts "
                     "first unless the id is already known from this "
                     "conversation. If the user only wants it to stop for now, "
                     "update_alert with state='paused' keeps the rule."),
     "parameters": {"type": "object", "properties": {
         "alert_id": {"type": "integer"}},
      "required": ["alert_id"]}},
    {"type": "function", "name": "log_trade",
     "description": (
         "Write a trade into the user's journal. Use whenever they report having "
         "taken or closed one — 'I bought 50 here', 'took the breakout', 'I'm out "
         "of that TCS long'. It records; it never places an order.\n"
         "A journal row is expensive to type, and almost none of it needs to be "
         "typed here — the trade is on the screen the user is describing it "
         "from:\n"
         "· from_drawing takes a plan already drawn by plan_position and fills "
         "side, entry, stop, size and risk from it. If such a plan exists and "
         "the user is logging THAT trade, this is the whole call.\n"
         "· entry_at / exit_at take a TIME instead of a price ('03 Aug 2026 "
         "09:20') and read the fill off that real bar, recording which bar it "
         "used. Use it whenever the user points at when rather than at how much.\n"
         "· `stop` becomes the trade's initial risk (|entry − stop| × quantity). "
         "Pass it whenever a stop is known or was mentioned: without it the row "
         "can carry no R-multiple, and expectancy across the journal stays null. "
         "It is the single most valuable field after the fill itself.\n"
         "Omitting the exit opens the trade; close it later with update_trade "
         "rather than logging a second row. Only symbol, side, quantity and an "
         "entry are needed to start — ask for those and take everything else "
         "from the chart or from what was already said."),
     "parameters": {"type": "object", "properties": {
         "symbol": {"type": "string", "description": "defaults to the chart in focus"},
         "side": {"type": "string", "enum": ["long", "short"]},
         "quantity": {"type": "number", "description": "shares/units — cannot be inferred"},
         "entry_price": {"type": "number"},
         "entry_at": {"type": "string", "description": "chart-format time, read off that bar instead of a price"},
         "exit_price": {"type": "number", "description": "omit to leave the trade open"},
         "exit_at": {"type": "string", "description": "chart-format time of the exit"},
         "stop": {"type": "number", "description": "the stop the trade was taken with — becomes initial risk"},
         "initial_risk": {"type": "number", "description": "rupees at risk, if stated directly instead of a stop"},
         "fees": {"type": "number"},
         "thesis": {"type": "string", "description": "why the trade was taken, in the user's own words"},
         "tags": {"type": "array", "items": {"type": "string"}},
         "plan": {"type": "object", "description": "open structure: targets, setup, anything the trader plans by"},
         "review": {"type": "object", "description": "open structure: adherence, emotion, lesson"},
         "from_drawing": {"type": "string", "description": "id of a plan_position drawing on this chart"},
         "interval": {"type": "string", "description": "bars used to resolve entry_at/exit_at, default 1m"}},
      "required": ["side", "quantity"]}},
    {"type": "function", "name": "list_trades",
     "description": ("The user's journal — their trades and the statistics it "
                     "exists for (net P&L, win rate, profit factor, expectancy "
                     "in R, plan adherence). Use for 'how am I doing', 'what "
                     "did I trade this week', 'am I following my plan', and "
                     "before updating one so the id is real. Narrow with symbol "
                     "or status. The overview is computed from the rows, so "
                     "expectancy and adherence stay null until trades carry an "
                     "initial risk and a review — say so rather than reporting "
                     "a blank as a result."),
     "parameters": {"type": "object", "properties": {
         "symbol": {"type": "string"},
         "status": {"type": "string", "enum": ["open", "closed"]},
         "limit": {"type": "integer", "description": "default 20, max 100"}}}},
    {"type": "function", "name": "update_trade",
     "description": (
         "Change a journal trade that already exists: close it, price its exit "
         "off a bar, add the stop that defines its risk, or write the review. "
         "Pass only what changes. `exit_at` reads the exit off a real bar the "
         "same way log_trade does. plan and review MERGE over what is stored, so "
         "writing a lesson cannot erase the thesis. Adding `stop` to a trade "
         "logged without one back-fills its initial risk and gives it an "
         "R-multiple — worth offering whenever a row has none. Every write "
         "keeps an audited revision."),
     "parameters": {"type": "object", "properties": {
         "trade_id": {"type": "integer"},
         "exit_price": {"type": "number"},
         "exit_at": {"type": "string", "description": "chart-format time of the exit"},
         "status": {"type": "string", "enum": ["open", "closed"]},
         "fees": {"type": "number"},
         "stop": {"type": "number", "description": "back-fills initial risk"},
         "initial_risk": {"type": "number"},
         "tags": {"type": "array", "items": {"type": "string"}},
         "plan": {"type": "object"},
         "review": {"type": "object"},
         "lesson": {"type": "string", "description": "what the trade taught, in their words"},
         "emotion": {"type": "string", "description": "how it was traded — impatient, hesitant, calm"},
         "adherence": {"type": "boolean", "description": "did it follow the plan"},
         "interval": {"type": "string", "description": "bars used to resolve exit_at, default 1m"}},
      "required": ["trade_id"]}},
    {"type": "function", "name": "update_journal_trade",
     "description": ("Apply a user-requested edit to an attached journal trade. "
                     "Use only when the user clearly asks to save/change a field, "
                     "not when they merely ask for feedback. plan, review, tags and "
                     "custom are deliberately open structures: preserve existing "
                     "fields by including them in full when replacing one. Execution "
                     "facts remain numeric. The tool writes an audited revision."),
     "parameters": {"type": "object", "properties": {
         "trade_id": {"type": "integer"},
         "changes": {"type": "object", "description": "fields to update; flexible plan/review/custom objects are accepted"}},
      "required": ["trade_id", "changes"]}},
    {"type": "function", "name": "get_levels",
     "description": "Detect real support/resistance from pivot clustering, with touch counts, strength and dates. Each level carries its own track record: how many past touches held vs broke, and the median reaction that followed — use it to say whether a level has actually worked, not just how often price reached it. Use whenever asked about levels, support, resistance, or where price reacts. To put them ON the chart set draw=true (top few) or pass draw_ids after reviewing the candidates — you choose WHICH, the detector supplies every price.",
     "parameters": {"type": "object", "properties": {
         "interval": {"type": "string", "enum": ["5m", "15m", "30m", "1h", "1d", "1w", "1mo"]},
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
         "interval": {"type": "string", "enum": ["5m", "15m", "30m", "1h", "1d", "1w", "1mo"]},
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
         "interval": {"type": "string", "enum": ["5m", "15m", "30m", "1h", "1d", "1w", "1mo"]},
         "lookback_bars": {"type": "integer", "description": "default 400"},
         "draw": {"type": "boolean"},
         "draw_ids": {"type": "array", "items": {"type": "string"}},
         "max_draw": {"type": "integer", "description": "default 1 — the most recent"},
         "draw_mode": {"type": "string", "enum": ["add", "replace", "clear"]}},
         "required": ["indicator", "interval"]}},
    {"type": "function", "name": "get_gaps",
     "description": "Find price gaps and, crucially, how often gaps have actually filled on this symbol in this window, with median bars-to-fill. Use whenever gaps come up, or when asked about unfilled gaps overhead/below. Set draw=true to shade them.",
     "parameters": {"type": "object", "properties": {
         "interval": {"type": "string", "enum": ["5m", "15m", "30m", "1h", "1d", "1w", "1mo"]},
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
         "interval": {"type": "string", "enum": ["1m", "5m", "15m", "30m", "1h", "1d", "1w", "1mo"]},
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
     "description": "Draw a shape by referencing anchor ids from get_anchors. Shapes: segment, ray, box, band, hline, vline, point, polyline, fib, candle. Use for anything the user asks to mark that isn't a detected level/trendline/divergence — a range between two swings, a box around a consolidation, a fib retracement across a leg, a line from one moment to another. Use 'candle' to single out a BAR for any reason ('mark the day it gapped', 'highlight that big red candle', 'which bar was the reversal') — it puts a dot just above the bar's high, pointing at it without covering the body and wicks. Giving a 1-anchor shape (hline/vline/point/candle) SEVERAL ids draws one per anchor in a single call, each auto-labelled from its anchor kind — always do that for 'mark both/all of…' asks instead of one call per marker.",
     "parameters": {"type": "object", "properties": {
         "shape": {"type": "string", "enum": ["segment", "ray", "box", "band", "hline", "vline", "point", "polyline", "fib", "candle"],
                   "description": "'fib' draws a full retracement ladder across the leg between the two anchors — the FIRST anchor is the leg's start (100%), the second its end (0%). 'candle' dots the bar an anchor sits on, just above its high"},
         "anchor_ids": {"type": "array", "items": {"type": "string"},
                        "description": "ids from get_anchors, e.g. ['A1312','A1271']"},
         "interval": {"type": "string", "enum": ["1m", "5m", "15m", "30m", "1h", "1d", "1w", "1mo"]},
         "lookback_bars": {"type": "integer", "description": "must match the get_anchors call"},
         "pane": {"type": "string", "description": "'price', or an indicator id like 'rsi'"},
         "label": {"type": "string", "description": "short caption drawn on the chart"},
         "role": {"type": "string", "enum": ["resistance", "support", "neutral"]},
         "draw_mode": {"type": "string", "enum": ["add", "clear"],
                       "description": "'clear' removes every shape previously drawn via draw_shape (other tools' drawings stay) — anchor_ids may be empty then"}},
         "required": ["shape", "anchor_ids", "interval"]}},
    {"type": "function", "name": "mark",
     "description": (
         "Draw ANYTHING by describing where, for everything no detector "
         "produces: session structure ('shade the first hour of every day', "
         "'line at each open'), a moment the conversation located ('the day "
         "the results landed'), a plain number ('a line at 1300'), a stretch "
         "of time ('shade June'), a note pinned to a point, a forward "
         "projection. draw_shape composes DETECTED anchors; this is for "
         "everything else, and you choose the shape that says it most "
         "precisely. You still never type a coordinate — you write an ADDRESS "
         "and it is resolved against the real bars.\n"
         "ADDRESS = '<time>' | '<price>' | '<time> @ <price>'.\n"
         "  time: '08 Jul 2026 15:25' | '2026-07-08' | '09:15' (time of day) "
         "| open | close (that day's first/last bar) | first | last | '-20' "
         "(20 bars back) | '+10' (10 bars forward, into blank chart) | '+1h' "
         "/ '+30m' / '+2d' (a duration from the address before it, so the "
         "opening hour is from 'open' to '+1h')\n"
         "  price: 1300 (a literal — refused if far off the loaded range) | "
         "high | low | open | close | mid | '+2%' | '-1.5%'\n"
         "What high/low/mid mean follows the shape: a BOX reads both corners "
         "off the bars between its two times, so from '09:15 @ high' to "
         "'10:15 @ low' is the opening range; a LINE (segment/ray/poly) reads "
         "each point off its own bar, so from '-120 @ low' to '-40 @ low' is "
         "a trendline through two swing lows; a full-width shape (hline/band) "
         "reads the whole session or window.\n"
         "repeat='session' resolves the shape once per trading day — that is "
         "how you mark every session's open, first hour or close in ONE call "
         "instead of listing dates.\n"
         "SHAPES, pick by what you mean: hline (a value across the chart) · "
         "band (a price zone) · vline (a moment) · vband (a stretch of TIME, "
         "full height — the shape for sessions, a date range, an event "
         "window) · segment (two points) · ray (extended right) · box (a "
         "region in time AND price) · poly (3+ points) · dot (pin one point) "
         "· candle (a dot above one bar — 'that bar') · note (a text chip at "
         "a point) · marker (an arrow or circle ON a bar)."),
     "parameters": {"type": "object", "properties": {
         "shapes": {"type": "array", "description": "several shapes in one call — always batch",
                    "items": {"type": "object", "properties": {
                        "shape": {"type": "string",
                                  "enum": ["hline", "band", "vline", "vband",
                                           "segment", "ray", "box", "poly",
                                           "dot", "candle", "note", "marker"]},
                        "at": {"type": "string", "description": "address, for a one-point shape"},
                        "from": {"type": "string", "description": "first address of a two-point shape"},
                        "to": {"type": "string", "description": "second address"},
                        "points": {"type": "array", "items": {"type": "string"},
                                   "description": "3+ addresses, for poly"},
                        "label": {"type": "string", "description": "short caption — and the text of a note/marker"},
                        "role": {"type": "string", "enum": ["support", "resistance", "neutral"],
                                 "description": "colour only: amber above, cyan below, violet otherwise"},
                        "repeat": {"type": "string", "enum": ["none", "session", "week"],
                                   "description": "resolve this shape once per trading day / week"},
                        "sessions": {"type": "integer",
                                     "description": "how many recent repeats, default 5, max 30"}},
                        "required": ["shape"]}},
         "interval": {"type": "string", "enum": ["1m", "5m", "15m", "30m", "1h", "1d", "1w"],
                      "description": "the timeframe the addresses are resolved on — match the chart"},
         "lookback_bars": {"type": "integer", "description": "default 300; must reach any date you address"},
         "pane": {"type": "string", "description": "'price', or an indicator id like 'rsi' (a literal there is an indicator value, not a price)"},
         "draw_mode": {"type": "string", "enum": ["add", "clear"],
                       "description": "'clear' removes every mark this tool drew; shapes may be empty"}},
         "required": ["shapes", "interval"]}},
    {"type": "function", "name": "evaluate_line",
     "description": "Score a line the USER drew: how many swings touched it, how many held vs broke, where it projects now. Use whenever the user asks whether their own trendline is any good, or what its record is. ALWAYS pass drawing_id when the line is one the user drew — the chart context lists every drawing with its ref, and referencing it is checked whereas copying coordinates is not. The message may also name the drawing the user tagged; that ref is the subject. Endpoints are for a line the user described but has not drawn.",
     "parameters": {"type": "object", "properties": {
         "drawing_id": {"type": "string", "description": "ref of the user's drawing (e.g. 'D3') from the chart context — preferred over coordinates"},
         "p1_time": {"type": "string", "description": "IST 'YYYY-MM-DD HH:MM' of the first endpoint"},
         "p1_value": {"type": "number"},
         "p2_time": {"type": "string"},
         "p2_value": {"type": "number"},
         "interval": {"type": "string", "enum": ["1m", "5m", "15m", "30m", "1h", "1d", "1w", "1mo"]},
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
         "interval": {"type": "string", "enum": ["5m", "15m", "30m", "1h", "1d", "1w", "1mo"]},
         "lookback_bars": {"type": "integer", "description": "bars to scan for the base rate, default 600"}},
         "required": ["interval"]}},
    {"type": "function", "name": "get_patterns",
     "description": "Detect named formations on the chart: 34 candlestick patterns (engulfing, hammer, doji varieties incl dragonfly/gravestone/long-legged, morning/evening star, three soldiers/crows, harami, three inside/outside up/down, piercing, dark cloud, tweezers, kickers, belt holds, rising/falling three methods, abandoned baby…), 22 chart patterns (head and shoulders and its inverse, double and triple tops/bottoms, ascending/descending/symmetrical triangles, rising/falling wedges, rectangle, channel up/down, broadening, bull/bear flags and pennants, cup and handle, rounding bottom/top) and market structure (HH/HL/LH/LL with BOS and CHoCH). Call it BOTH ways: omit `kinds` to sweep everything for 'what patterns are on this chart', or set `kinds` to answer 'is there a head and shoulders / any bullish engulfing'. `kinds` takes exact snake_case ids — e.g. bullish_belt_hold, bearish_kicker, three_inside_up, rising_three_methods, triple_top, bull_pennant, cup_and_handle. Always use this rather than reading candles out of get_bars and judging them yourself — the thresholds here are explicit and come back with the result. Set draw=true to draw chart patterns as their actual geometry — a solid outline through the defining swing points with a tinted interior, a dashed neckline segment ending at the break bar, fitted wedge/triangle edges, flag pole and box — so describe them as drawn shapes, not as horizontal levels. draw=true ALSO marks candlestick patterns, with a dot above the high of the bar that qualified — the 5 most recent bars by default, or however many `mark_limit` says. The result reports how many were found versus how many were drawn: quote that, never guess at why the chart shows fewer than the list. Name the bar and its pattern; the dot is a pointer, not a finding.",
     "parameters": {"type": "object", "properties": {
         "interval": {"type": "string", "enum": ["5m", "15m", "30m", "1h", "1d", "1w", "1mo"]},
         "lookback_bars": {"type": "integer", "description": "bars to scan, default 300"},
         "kinds": {"type": "array", "items": {"type": "string"},
                   "description": "specific pattern names to look for, e.g. ['head_and_shoulders'] or ['bullish_engulfing','hammer']. Omit for a full sweep. An unknown name comes back with the full list rather than scanning."},
         "families": {"type": "array", "items": {"type": "string", "enum": ["candlestick", "chart", "structure"]},
                      "description": "restrict to whole families instead of naming patterns"},
         "limit": {"type": "integer", "description": "max instances per family, default 20"},
         "draw": {"type": "boolean", "description": "mark the top chart patterns"},
         "draw_ids": {"type": "array", "items": {"type": "string"},
                      "description": "ids from the chart_patterns list, to mark exactly those"},
         "mark_limit": {"type": "integer", "description": "how many candlestick BARS to mark, most recent first — default 5. Raise it when the user asks for all of them ('mark every candle pattern'); the result says how many were found versus drawn."},
         "draw_mode": {"type": "string", "enum": ["add", "replace", "clear"]}},
         "required": ["interval"]}},
    {"type": "function", "name": "evaluate_pattern",
     "description": "Historical reliability of ONE named pattern on this chart: every past instance, the forward move horizon_bars after each completion, the rate of moving in the pattern's textbook direction, and the unconditional base rate as control — the edge is pattern rate minus base rate. Use for 'does X actually work here / has that pattern type been reliable'. Works for candlestick kinds and swing shapes (double/triple top/bottom, head and shoulders, flags, pennants); live-edge fitted shapes (triangles, wedges, channels, rectangle, cup, rounding) have no instance history and it will say so. Never answer reliability questions from raw bars.",
     "parameters": {"type": "object", "properties": {
         "kind": {"type": "string", "description": "one exact snake_case pattern id, e.g. bullish_engulfing, triple_top, bull_flag"},
         "interval": {"type": "string", "enum": ["5m", "15m", "30m", "1h", "1d", "1w", "1mo"]},
         "lookback_bars": {"type": "integer", "description": "history to mine, default 1000, max 2000"},
         "horizon_bars": {"type": "integer", "description": "forward window per instance, default 10"}},
         "required": ["kind", "interval"]}},
    {"type": "function", "name": "get_peers",
     "description": (
         "The company's industry classification (Moneycontrol) and its peer "
         "group within the 500-company universe. Use for 'who are the "
         "peers/competitors' and as the first step of any peer comparison — "
         "then compare_symbols for price paths, or screen_universe with the "
         "industry to rank ONE metric (RSI, returns, any feature) across "
         "every peer at once."),
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
         "setups, criteria or structure across many names — including "
         "comparing one metric across a sector's peers (industry + sort by "
         "the metric; the chart symbol's OWN industry is already stated in "
         "your context, so call this directly — a get_peers round first "
         "just to learn it wastes a full hop) and fresh crossovers "
         "(smaX_cross_ago lt N with smaX_rel's sign for the direction). "
         "Results are end-of-day and carry their own as-of date and "
         "universe size — quote both. The vp20_* features screen on the "
         "20-session VOLUME PROFILE: vp20_pos places the close inside the "
         "value area (gt 100 = trading above accepted value, lt 0 = below), "
         "vp20_va_width_pct finds coiled vs distributed names, and "
         "vp20_poc_shift_pct finds value migrating up or down. Use them for "
         "'above/below value', 'accepted', 'balanced', 'value migrating' "
         "asks. They need 1-minute bars, so they score a SUBSET of the "
         "universe — the result reports how many and you must say so rather "
         "than implying full coverage."),
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
    {"type": "function", "name": "recall_conversations",
     "description": "Search the user's EARLIER conversations — the ones from previous sessions, stored against their account. Call it ONLY when the user refers to something outside this conversation: 'what did we say about ITC last week', 'the level I asked about yesterday', 'have I looked at this before', 'remind me what my plan was'. NEVER call it for anything said in the current conversation — every turn of that is already in front of you, and re-fetching it wastes a round trip and makes one remark look like two. Omit `query` to list recent conversations (an index: title, date, symbols); pass `query` and/or `symbol` to search their text and get the matching passages back. Nothing is stored for a signed-out user, and the result says so — relay that rather than recalling anything yourself. An empty result is an ANSWER ('no earlier conversation mentions it'), not a reason to hedge. Old conversations record what was SAID; current prices and levels still come from the data tools.",
     "parameters": {"type": "object", "properties": {
         "query": {"type": "string", "description": "words to look for in what was said, e.g. 'stop loss' or 'wedge breakout'. Omit to list recent conversations."},
         "symbol": {"type": "string", "description": "narrow to a symbol the conversation was about, e.g. 'ITC'"},
         "limit": {"type": "integer", "description": "conversations returned, 1-10, default 5"}},
         "required": []}},
    {"type": "function", "name": "plan_position",
     "description": (
         "Draw or update the trade-plan overlay (entry/stop/targets) and return "
         "its risk arithmetic: R:R and breakeven hit-rate per target, position "
         "size from a risk budget, per-target P&L, stop distance in ATR(14)s, "
         "and the historical entry→target-vs-stop record. Expresses the USER'S "
         "stated idea — never invent a trade unprompted. Entry defaults to the "
         "last close. A new call replaces the plan; draw_mode=clear removes it. "
         "To size a position the user DREW, pass its ref as drawing_id. "
         "The overlay projects FORWARD from the entry bar into blank chart — "
         "it is the window the trade would live in, not a record of the past. "
         "Whenever the levels come from something already on the chart — a "
         "wedge edge, a support level, a neckline — pass `basis` so the plan "
         "carries what it was built on, and name those levels in the reply."),
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
                         "description": "rupees the user is prepared to LOSE "
                         "('risking 50k' means this, NOT capital); qty is "
                         "derived from it"},
         "capital": {"type": "number",
                     "description": "rupees DEPLOYED to buy — only when the "
                     "user says invest/deploy, never for 'risking X'"},
         "risk_pct": {"type": "number",
                      "description": "with capital: risk_amount = capital × risk_pct/100"},
         "side": {"type": "string", "enum": ["long", "short"]},
         "drawing_id": {"type": "string"},
         "basis": {"type": "string", "description": "the chart feature these levels came from, a few words — 'falling wedge upper edge 1,318.15', 'support 1,271 · 4 touches'. Rides on the overlay so the plan says what it was built on. Leave empty only when the user gave bare numbers with no reason."},
         "interval": {"type": "string",
                      "enum": ["1m", "5m", "15m", "30m", "1h", "1d", "1w", "1mo"]},
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
         "interval": {"type": "string", "enum": ["5m", "15m", "30m", "1h", "1d", "1w", "1mo"]},
         "lookback_bars": {"type": "integer", "description": "default 600"}},
         "required": ["interval"]}},
    {"type": "function", "name": "get_results",
     "description": "Quarterly result (earnings) dates for this company, newest first, and optionally mark them on the chart with event icons. Use for 'when were the last results', 'when did Q1 report', 'mark earnings on the chart', or to locate a quarter before asking what price did around it. The date returned is the session the market could FIRST react to: an after-market announcement reacts the next day, and the field already accounts for that. These are past announcements only — there is no scheduled future date here, so never state one.",
     "parameters": {"type": "object", "properties": {
         "limit": {"type": "integer", "description": "how many recent quarters, default 8, max 40"},
         "interval": {"type": "string", "enum": ["5m", "15m", "30m", "1h", "1d", "1w", "1mo"]},
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
         "interval": {"type": "string", "enum": ["1m", "5m", "15m", "30m", "1h", "1d", "1w", "1mo"]},
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
    {"type": "function", "name": "volume_profile",
     "description": "How much volume traded at each PRICE over a window, built from 1-minute bars: the point of control (most-traded price), the value area (where 70% of volume changed hands, with its high and low), high- and low-volume nodes, and the total. Use for 'volume profile', 'point of control', 'value area', 'where has most volume traded', 'which levels has price accepted or rejected', and for finding acceptance/imbalance zones. The row height is chosen FROM THE DATA — each bar's volume is spread uniformly across its high-low, so rows can never be finer than that smear; ask for fewer rows if you want it coarser, and a finer request will be reduced and reported. This is volume at price, the same construction TradingView uses. It is NOT order flow: delta, cumulative delta, footprint and bid/ask imbalance need the aggressor side of each trade and no Indian retail feed carries it — never present it as buying versus selling. Indices and India VIX print no volume and the tool will say so. THIS TOOL IS ONE SYMBOL AT A TIME. For a question about MANY instruments — 'all cryptos', 'which stocks', 'compare across the sector', 'who is above value' — do NOT say it cannot be done and do NOT loop this tool over a list you guessed: call screen_universe with the vp20_* features (optionally industry='cryptocurrency' or any other industry), which reads the same 20-session profiles for every scored instrument in one call and returns each one's POC and value area. Loop THIS tool only for a handful of symbols the user actually named.",
     "parameters": {"type": "object", "properties": {
         "frm": {"type": "string", "description": "window start in the chart's format, e.g. '21 Jul 2026'; omit to use lookback_sessions"},
         "to": {"type": "string", "description": "window end; omit for a single day"},
         "lookback_sessions": {"type": "integer", "description": "used when no dates are given — the last N sessions, default 1 (today's profile), max 250. Multi-session builds a COMPOSITE profile, which is the right call for 'where has volume built up over the last month/quarter'."},
         "rows": {"type": "integer", "description": "how many price rows. Omit to let the data decide, which is almost always right. A value finer than the bars support is capped and reported; only pass this to make the profile COARSER."},
         "value_area_pct": {"type": "number", "description": "share of volume in the value area, default 70 (the convention)"},
         "split": {"type": "boolean", "description": "also return each row split by bar direction (close >= open). This is a heuristic, NOT the aggressor side — only ask for it if the user wants it, and label it as bar direction."},
         "draw": {"type": "boolean", "description": "draw the histogram with POC and value-area lines on the chart, default true"},
         "draw_mode": {"type": "string", "enum": ["replace", "add", "clear"],
                       "description": "'replace' (default) swaps any existing profile, 'clear' removes it"}},
      "required": []}},
]

def tool_recall_conversations(query: str = "", symbol: str = "",
                              limit: int = 5) -> dict:
    """Search the user's EARLIER conversations. Never this one.

    Two exclusions do that, and both are load-bearing:

      · the conversation open right now is filtered out by chat_id. Its turns
        are already in the model's context — every one of them — so returning
        them here would spend tokens re-reading what it just read, and worse,
        the same sentence arriving twice from two places reads as two
        occasions on which it was said.
      · nothing is stored for a signed-out user, so there is nothing to leak
        between people sharing a browser.

    A miss is an ANSWER: "you have no earlier conversations mentioning X" is
    a fact worth relaying, not a reason to hedge or to invent a recollection.
    """
    me = getattr(_req, "user", None)
    if not me:
        return {"available": False, "_note": (
            "Conversation history is stored per ACCOUNT and this user is not "
            "signed in, so there is nothing to search. Say exactly that — "
            "signed out, so earlier chats were never saved — and do not "
            "recall anything from memory.")}
    uid, cur = me[0], str(getattr(_req, "chat_id", "") or "")
    terms = [w for w in re.split(r"\W+", f"{query} {symbol}".lower()) if len(w) > 2]

    with _users_lock:
        rows = _users.execute(
            "SELECT chat_id, title, symbols, started, updated, turns "
            "FROM conversations WHERE user_id=? AND chat_id<>? "
            "ORDER BY updated DESC LIMIT 400", (uid, cur)).fetchall()
    if not rows:
        return {"conversations": [], "searched": 0, "_note": (
            "This account has no EARLIER conversations — only the one in "
            "progress, which is already in context. Say so plainly.")}

    lim = max(1, min(int(limit or 5), 10))
    out = []
    for cid, title, syms, started, updated, blob in rows:
        try:
            turns = json.loads(blob)
        except json.JSONDecodeError:
            continue
        rec = {"when": _ist(updated, False), "title": title,
               "symbols": [s for s in syms.split(",") if s],
               "turns": len(turns)}
        if not terms:
            out.append(rec)                       # no query: just the index
        else:
            # Score on TURNS, not the blob, so the excerpt returned is the
            # passage that matched rather than a conversation-shaped guess.
            hits = [t for t in turns
                    if any(w in t["content"].lower() for w in terms)]
            if not hits:
                continue
            rec["matched_turns"] = len(hits)
            rec["excerpt"] = [{"role": t["role"], "said": t["content"][:600]}
                              for t in hits[:6]]
            out.append(rec)
        if len(out) >= lim:
            break

    note = ("Everything here is from an EARLIER conversation, not this one. "
            "Date each recollection ('on 22 Jul you asked…') so the user can "
            "tell a memory from something said a moment ago, and quote rather "
            "than paraphrase — these are their own words. What the CHART "
            "shows now still comes from the tools; an old conversation is a "
            "record of what was said, never a source of current prices.")
    if terms and not out:
        note = (f"Searched {len(rows)} earlier conversation(s) and none "
                f"mentions {' or '.join(terms[:3])}. That is the answer: say "
                "there is no earlier discussion of it rather than hedging.")
    return {"conversations": out, "searched": len(rows),
            "query": " ".join(terms), "_note": note}


_DISPATCH = {"get_levels": tool_get_levels, "get_bars": tool_get_bars,
             "get_indicator": tool_get_indicator,
             "get_trendlines": tool_get_trendlines,
             "get_divergences": tool_get_divergences,
             "get_anchors": tool_get_anchors,
             "get_gaps": tool_get_gaps,
             "draw_shape": tool_draw_shape,
             "mark": tool_mark,
             "evaluate_line": tool_evaluate_line,
             "evaluate_fib": tool_evaluate_fib,
             "evaluate_drawing": tool_evaluate_drawing,
             "plan_position": tool_plan_position,
             "volume_profile": tool_volume_profile,
             "get_peers": tool_get_peers,
             "compare_symbols": tool_compare_symbols,
             "screen_universe": tool_screen_universe,
             "get_patterns": tool_get_patterns,
             "evaluate_pattern": tool_evaluate_pattern,
             "get_results": tool_get_results,
             "evaluate_results": tool_evaluate_results,
             "explain_move": tool_explain_move,
             "search_news": tool_search_news,
             "get_flows": tool_get_flows,
             "get_deals": tool_get_deals,
             "recall_conversations": tool_recall_conversations,
             "open_chart": tool_open_chart}

# The watcher's three, added only when alerts.py is loaded. `user_id` is NOT a
# tool parameter and never appears in the schema: it is read off the request's
# own bearer token, so the model has no way to address another account's alerts
# even if it invents the argument.
def _alert_tool(name: str):
    def call(**args):
        if _alerts is None:
            return {"error": "the alert engine is not loaded on this server"}
        # `_req.user` is already set per chat request for recall_conversations —
        # (id, email, name) or None. Reusing it beats a second identity field,
        # and signed out resolves to 0, which the tool answers honestly.
        who = getattr(_req, "user", None)
        return getattr(_alerts, name)(user_id=(who[0] if who else 0), **args)
    return call


for _n in ("set_alert", "check_alert", "list_alerts", "update_alert",
           "cancel_alert"):
    _DISPATCH[_n] = _alert_tool("tool_" + _n)


def _teach_alert_grammar() -> None:
    """Put the ENGINE's own address grammar into the alert tools' schema.

    The addresses were re-typed into the tool description by hand, and had
    already drifted from the dict the resolver actually reads: results(),
    the derived prices, ema/supertrend, avg(close,50), stoch(14).k and the
    shifted form rsi(14)[1] were all resolvable, and none of them were
    offered. The model cannot compose what it has not been told exists — so
    "tell me when results are out" reached for a news search, which was the
    only instrument it had, while `results()` sat in OPERANDS unused.

    Generated from OPERANDS/OPS, it cannot drift again: an operand added to
    alerts.py is offered to the model the day it lands, the same way an
    indicator added to indicators.py is already addressable the day IT lands.
    This is a schema, not a router — nothing here inspects the user's words.
    """
    if _alerts is None:
        return

    # Trimmed to the clause that says what the address IS. The rest of each
    # entry is a caveat the ENGINE enforces anyway (range checks, closed-bar
    # evaluation, tick clearance) and paying for it on every turn buys the
    # model nothing it can act on. `ops` are already an enum on the parameter,
    # so only their one-line meaning is worth carrying.
    def head(s: str) -> str:
        s = " ".join(str(s).split())
        for stop in (". ", " — ", ", so ", ". "):
            if stop in s:
                s = s.split(stop)[0]
        return s[:96]

    addrs = "; ".join(f"{k} = {head(v)}" for k, v in _alerts.OPERANDS.items())
    ops = "; ".join(f"{k}: {head(v)}" for k, v in _alerts.OPS.items())
    grammar = (f"\n\nADDRESSES (this list IS the resolver's own — anything on "
               f"it works, anything off it is refused): {addrs}"
               f"\n\nOPS: {ops}")
    # Carried by set_alert alone. check_alert takes the identical `when`, and
    # both schemas reach the model in the same request, so a second copy is a
    # second bill for a grammar already on screen.
    for t in TOOLS:
        if t.get("name") == "set_alert":
            t["description"] = t["description"] + grammar
        elif t.get("name") == "check_alert":
            t["description"] = (t["description"] + " Takes exactly the "
                                "addresses and ops listed under set_alert.")


def _journal_update_tool(trade_id: int, changes: dict):
    who = getattr(_req, "user", None)
    if not who:
        return {"error": "sign in before changing a journal trade"}
    if _journal is None:
        return {"error": "the journal is unavailable"}
    if not isinstance(changes, dict):
        return {"error": "changes must be an object"}
    # The public patch path already validates ownership, numeric facts and
    # flexible objects. Chat is an origin, not a second write implementation.
    status, payload = _journal.api_patch(who[0], int(trade_id),
                                         {**changes, "origin": "chat"})
    return payload if status < 400 else {"error": payload.get("error", "update failed")}


_DISPATCH["update_journal_trade"] = _journal_update_tool


# The journal's own three, on the same terms as the watcher's: `user_id` is
# read off the request's bearer token and is never a parameter, so the model
# cannot address another account's book even if it invents the argument.
def _journal_tool(name: str):
    def call(**args):
        if _journal is None:
            return {"error": "the journal is not loaded on this server"}
        who = getattr(_req, "user", None)
        return getattr(_journal, name)(user_id=(who[0] if who else 0), **args)
    return call


for _n in ("log_trade", "list_trades", "update_trade"):
    _DISPATCH[_n] = _journal_tool("tool_" + _n)


# ── which chart a tool reads ────────────────────────────────────────────────
#
# The screen can hold several charts, and the model can now aim ANY chart-
# scoped tool at any of them with `symbol=` — the same tool, pointed somewhere
# else. That is the whole of the multi-chart capability: there is no compare
# mode, no comparison tool set, no branch that detects "this is a comparison".
# Whatever the model can establish about one chart it establishes about the
# others by calling the same tools again, and composes the answer itself.
#
# Two exclusions, and both are about honesty rather than plumbing:
#   · the tools that DRAW (draw_shape, mark, plan_position) or score a shape the user
#     drew (evaluate_*) act on the chart the drawings actually live on. Aiming
#     them elsewhere would compute against one instrument and draw on another.
#   · get_peers / compare_symbols / screen_universe already name their own
#     symbols — they were never single-chart tools.
_CHART_SCOPED = frozenset({
    "get_levels", "get_bars", "get_indicator", "get_trendlines",
    "get_divergences", "get_anchors", "get_gaps", "get_patterns",
    "evaluate_pattern", "get_results", "evaluate_results", "explain_move",
    "search_news", "get_flows", "volume_profile",
})

_SYMBOL_ARG = {
    "type": "string",
    "description": ("which chart on screen to read — a ticker listed in the "
                    "chart context. Omit for the chart in focus. Reading a "
                    "second chart is this argument, not a different tool."),
}
for _t in TOOLS:
    if _t.get("name") in _CHART_SCOPED:
        _t["parameters"]["properties"].setdefault("symbol", dict(_SYMBOL_ARG))


_INK_ARGS = ("draw", "mark_points", "connect", "mark_levels", "remove",
             "clear_marks")


def _wants_ink(args: dict) -> list:
    """Which arguments of this call would put something on the user's chart."""
    return [k for k in _INK_ARGS if args.get(k)]


def _no_ink_note(want: str = "") -> str:
    """Why this cannot be drawn, and the route that actually exists.

    The drawing layer belongs to ONE chart — the page's main chart. A
    secondary pane has none at all. So "click the {want} pane and I'll draw
    it there" is never true: on a secondary pane it fails again with the same
    refusal, and when {want} has no pane it names something that isn't on
    screen. Both were being said, because the refusal knew what it could not
    do and not where the ink could go. It knows now (`_req.main_chart`).
    """
    main = str(getattr(_req, "main_chart", "") or getattr(_req, "symbol", "") or "")
    # No symbol argument means the working chart — which, on a reference pane,
    # is still not the main chart. Without this default the note fell back to
    # "select the main chart", i.e. draw THIS pane's levels onto a different
    # instrument: the same mistake in the other direction.
    want = str(want or getattr(_req, "symbol", "") or "").upper()
    lead = (f"Only the main chart carries drawings, and the main chart is "
            f"{main}. A secondary pane has no drawing layer, so clicking one "
            f"never makes it drawable — do not tell the user to click a pane "
            f"to get this drawn.")
    if want and main and want != main.upper():
        # The one real route: that symbol has to BECOME the main chart.
        # open_chart(replace=true) swaps the FOCUSED chart, so it only reaches
        # the main chart while the main chart is the focused one — which is
        # exactly the case where drawing was refused for the symbol alone.
        if getattr(_req, "drawable", True):
            return (lead + f" To draw {want} it has to become the main chart: "
                    f"say so and offer it, and if the user agrees call "
                    f"open_chart with symbol='{want}' and replace=true, then "
                    f"draw. Otherwise quote the {want} values in the reply.")
        return (lead + f" The focused pane is a reference chart, so nothing "
                f"can be drawn from here at all. Quote the {want} values, and "
                f"say {want} would have to be opened as the main chart for "
                f"them to be drawn.")
    return (lead + " The focused pane is a reference chart. Quote the values "
            "in the reply, and say the user can select the main chart to have "
            "them drawn there.")


def run_tool(name: str, args: dict) -> dict:
    fn = _DISPATCH.get(name)
    if not fn:
        return {"error": f"unknown tool {name}"}
    # draw_shape / mark / plan_position exist only to draw — the whole call
    # is ink, so there is no version of them that a reference pane can serve.
    if name in ("draw_shape", "mark", "plan_position") and not getattr(_req, "drawable", True):
        return {"error": "this chart cannot be drawn on",
                "_note": _no_ink_note(str((args or {}).get("symbol") or ""))}
    # `symbol` is routing, not a parameter of the computation: the tools that
    # take it in their own signature (get_peers) keep it, and for everything
    # else it swaps the request's working chart for the length of this one
    # call. Doing it here means a tool never has to know that more than one
    # chart exists — and a tool added tomorrow inherits this for free.
    args = dict(args or {})
    want = str(args.pop("symbol", "") or "").upper().strip() \
        if name in _CHART_SCOPED and "symbol" not in _fn_params(fn) else ""
    prev = getattr(_req, "symbol", "RELIANCE")
    # The chart in focus may itself be a secondary pane, which has no drawing
    # layer at all. Anything that would put ink on the screen is refused with
    # the reason — the alternative is a reply that says "drawn" while the line
    # appears on a different chart than the one it was computed from.
    if not getattr(_req, "drawable", True) and _wants_ink(args):
        return {"error": "this chart cannot be drawn on",
                "_note": _no_ink_note(want)}
    if want and want != prev:
        # `symbol=` means "which chart on screen to read" — its own description
        # says so — but nothing checked it, and the model does not only pick
        # from the screen. It also picks from the TRANSCRIPT. A conversation
        # survives a symbol change (chats are deliberately un-scoped: one
        # thread runs across RELIANCE, then TCS, then GOLD), so six turns of
        # NIFTY prose can sit above one line of GOLD envelope, and the stale
        # ticker rides into `symbol=`. It resolves — it is a real instrument —
        # the tool computes it correctly, and the reply quotes NIFTY numbers
        # to someone looking at gold. Every number right, every number about a
        # chart that is not on screen, and nothing downstream can tell.
        # So the aim is bounded by the conversation's own charts. Cross-symbol
        # work is unaffected: get_peers / compare_symbols / screen_universe
        # name their own symbols and were never in _CHART_SCOPED.
        on_screen = [s.upper() for s in (getattr(_req, "charts", []) or [])]
        if on_screen and want not in on_screen:
            return {"error": f"{want} is not a chart in this conversation",
                    "_note": (f"The charts here are: {', '.join(on_screen)}. "
                              f"`symbol=` chooses among THOSE — it is not a way "
                              f"to read an instrument the user is not looking "
                              f"at. An earlier turn may have been asked on a "
                              f"chart that has since been changed; the envelope "
                              f"above is the current one. Answer for a chart on "
                              f"screen, and if {want} is genuinely the subject, "
                              f"say it is not open and offer open_chart.")}
        # An unloaded chart must say so. Answering from the focused chart
        # under another chart's name is the one failure that cannot be caught
        # downstream — every number would look right and belong to the wrong
        # company.
        if not _symbol_ready(want):
            open_now = ", ".join(getattr(_req, "charts", []) or [prev])
            return {"error": f"no local bars for {want}",
                    "_note": (f"Nothing is stored for that symbol. The charts in "
                              f"this conversation are: {open_now}. Say which ones "
                              f"you can read rather than answering for one you "
                              f"cannot.")}
        # Reading another chart is free; DRAWING on one is not. The scene layer
        # and the indicator panes belong to the chart in focus, so a request to
        # draw while aimed elsewhere would compute on one instrument and put
        # the line on another. Refused with the reason, never silently dropped.
        drawing = _wants_ink(args)
        if drawing:
            return {"error": f"cannot draw on {want} from here",
                    "_note": (f"Reading {want} works — drop "
                              f"{', '.join(drawing)} and the same call returns "
                              f"the values. " + _no_ink_note(want))}
        _req.symbol = want
    try:
        out = _run_tool(name, fn, args)
    finally:
        # every stamp below reads the working chart, so it is restored only
        # once the whole result is built
        _req.symbol = prev
    # A result that came from another chart must carry that chart's name, or
    # two tool results in the same turn are indistinguishable in the reply.
    if want and isinstance(out, dict):
        out.setdefault("symbol", want)
    return out


def _fn_params(fn) -> set:
    import inspect
    try:
        return set(inspect.signature(fn).parameters)
    except (TypeError, ValueError):
        return set()


def _run_tool(name: str, fn, args: dict) -> dict:
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
            f"The last bar is still FORMING (as of {_hm_ist(form[0])} {_tzl()}) — "
            f"treat its values as provisional, not a closed candle.")
    # Geometry drawn on a timeframe the chart is not showing is invisible to
    # the user until they switch — a "drawn" claim with nothing on screen
    # reads as a failure, so the reply must name the switch.
    iv, ctx_iv = args.get("interval"), getattr(_req, "ctx_interval", "")
    if (iv and ctx_iv and iv != ctx_iv and isinstance(out, dict)
            and "error" not in out and getattr(_scene, "items", None)):
        out["_interval_note"] = (
            f"This was drawn on the {iv} timeframe but the chart currently "
            f"shows {ctx_iv} — tell the user to click the {iv} interval "
            f"button to see it; the chat cannot switch the view.")
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
    journal_ctx = ctx.get("journal") if isinstance(ctx, dict) else None
    journal_block = ""
    if isinstance(journal_ctx, dict):
        trade = journal_ctx.get("trade") or journal_ctx
        # The record is server-owned data relayed through the client. It may
        # guide interpretation, but numbers are facts and edits must use the
        # journal tool rather than being merely claimed in prose.
        journal_block = ("## Journal trade attached\n"
                         + json.dumps(trade, ensure_ascii=False, separators=(",", ":"))
                         + "\nDiscuss any field freely. Use update_journal_trade for changes; "
                           "never say a journal change was saved unless that tool succeeds.")
    # The stub has to NAME the chart, and this is why. Changing symbol reloads
    # the page; the conversation restores from localStorage instantly while the
    # bars are still in flight, so the very first question on the new chart
    # arrives with status=loading. Unnamed, the stub said only "you cannot see
    # the chart" — and the model, left with a transcript that talked about
    # NIFTY 50 for six turns, answered for NIFTY 50 while the composer chip
    # read GOLD. Not being able to read the bars yet is a different fact from
    # not knowing WHICH chart is open, and the envelope always knows the second.
    who = str(ctx.get("symbol") or "").upper()
    iv = str(ctx.get("interval") or "")
    stub = ("## Chart the user is viewing\n"
            + (f"{who}{f' · {iv}' if iv else ''} — this is the chart, and it is "
               f"the subject of the question. Its bars have not finished "
               f"loading, so you cannot read values off it yet: say that "
               f"rather than answering from earlier turns, which may have been "
               f"asked on a different chart. Never answer for another "
               f"instrument merely because the transcript discussed one."
               if who else
               "The chart has not finished loading — you cannot see it yet. "
               "Say so if asked about it."))
    if ctx.get("status") == "loading":
        return "\n\n".join(x for x in (stub, journal_block) if x)
    if not ctx.get("symbol"):
        return journal_block
    try:
        return "\n\n".join(x for x in (_render_context(ctx), journal_block) if x)
    except Exception as exc:  # noqa: BLE001 — never break the reply on a bad envelope
        logging.warning("charto: malformed chart context (%s)", exc)
        return stub


def _render_context(ctx: dict) -> str:
    """The focused chart, then any others the user has put in the conversation.

    Several charts are ONE list of the same block, not a comparison mode: each
    is described exactly as a lone chart would be, and the model reads across
    them itself. The focused chart is the one the drawings, the chat's own
    annotations and the pinned bars belong to — those sections only appear
    there, because that is the only chart they exist on.
    """
    # A chart is identified by symbol AND interval: the same company on two
    # timeframes is two charts, and matching on the ticker alone would silently
    # drop the second one from a conversation that is explicitly about both.
    me = (ctx.get("symbol"), ctx.get("interval"))
    others = [c for c in (ctx.get("charts") or [])
              if isinstance(c, dict) and (c.get("symbol"), c.get("interval")) != me
              and c.get("view") and c.get("window") and c.get("last_bar")]
    block = _render_chart(ctx, focused=True)
    if others:
        block += "\n\n" + "\n\n".join(_render_chart(c, focused=False) for c in others)
        names = ", ".join(f"{c['symbol']} ({c['interval']})"
                          for c in [ctx] + others)
        # Focus and drawability are two different things, and saying "the
        # focused chart is the one carrying drawings" made them one: a focused
        # SECONDARY pane carries none of it, and the chart that does may not be
        # the one in focus at all.
        main = str(ctx.get("main_chart") or "")
        named = f" ({main})" if main else ""
        owns = (f"It is also the main chart, so it is the one carrying drawings, "
                f"chat annotations and pinned bars."
                if ctx.get("drawable") is not False else
                f"It is a reference pane: the drawings, chat annotations and "
                f"pinned bars live on the main chart{named}, the only chart "
                f"anything can be drawn on.")
        block += (
            f"\n\nThe user has {len(others) + 1} charts in this conversation: {names}. "
            f"Every chart-reading tool takes `symbol` — pass one of these to aim it at "
            f"that chart, and call it once per chart when a question spans them. "
            f"{ctx.get('symbol')} is the one in focus; a bare 'this chart' means it. "
            f"{owns} "
            f"Attribute every number to the chart it came from.")
    return block + "\n" + _CONTEXT_CONTRACT


def _render_chart(ctx: dict, focused: bool = True) -> str:
    v, w, lb = ctx["view"], ctx["window"], ctx["last_bar"]
    L = [
        "## Chart the user is viewing" if focused
        else f"## Also in this conversation — {ctx['symbol']}",
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
        L.insert(2, f"live · forming bar {_hm_ist(_form[0])} {_tzl()}")
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

    # Drawings, chat annotations and pinned bars exist on ONE chart. A second
    # chart in the conversation has none of them, and listing "none" under it
    # would invite the model to reason about their absence.
    if not focused:
        return "\n".join(L)

    # Where ink can land, said on the way in rather than only as a refusal.
    # "The chart in focus is the drawable one" is false twice over — a focused
    # secondary pane has no drawing layer, and a question about a symbol that
    # is not the main chart cannot be drawn at all — and the model, left to
    # fill the gap, sent users to click panes that could never take the ink.
    main = str(ctx.get("main_chart") or "")
    if ctx.get("drawable") is False:
        L.append(f"NOT DRAWABLE: this is a reference pane and has no drawing "
                 f"layer. The only chart that can be drawn on is the main "
                 f"chart{f' ({main})' if main else ''}. If something is asked "
                 f"to be drawn, give the geometry in words and say it can be "
                 f"drawn on the main chart — clicking this pane will not make "
                 f"it drawable.")
    elif main:
        L.append(f"Drawings land on the main chart ({main}) and nowhere else. "
                 f"Levels read from another symbol cannot be drawn here — "
                 f"quote them, or offer to open that symbol as the main chart "
                 f"(open_chart replace=true) and draw them there.")

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

    return "\n".join(L)


# The contract that closes the envelope — one copy, after the last chart,
# because it governs every number in it rather than any one chart.
_CONTEXT_CONTRACT = (
        "\nThese facts describe the chart(s) above. For anything they don't contain "
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


SUGGEST_PROMPT = (
    "You write the three questions a user is most likely to want to ask NEXT, "
    "given the conversation so far.\n\n"
    "This is a chart-analysis chat for Indian markets (NSE/BSE equities, "
    "indices, MCX commodities, crypto). It can answer from bars: price and "
    "volume history, indicators, candlestick and chart patterns, support and "
    "resistance levels, trendlines, divergences, gaps, volume profile, "
    "correlations and comparisons between symbols, peers, company "
    "fundamentals, bulk and block deal disclosures, and it can draw on the "
    "chart. It cannot place orders, hold a portfolio, or predict.\n\n"
    "Rules:\n"
    "- Each must be answerable by this app from chart or company data. Never "
    "suggest a prediction, a recommendation, a buy/sell, a target, or "
    "anything about a market it does not carry.\n"
    "- Follow the thread. Prefer the obvious next step from what was just "
    "answered, and the loose end left earlier in the conversation that was "
    "never picked up.\n"
    "- Three DIFFERENT directions — not three rewordings of one. Going "
    "deeper, widening to another symbol or timeframe, and testing what was "
    "claimed are good axes.\n"
    "- Write them as the user would type them: first person, plain, no "
    "pleasantries. Name the actual symbol under discussion rather than "
    "'this stock'.\n"
    "- Under 60 characters each. Short enough to read at a glance.\n\n"
    "A question about what WILL happen is the easy mistake, and it is always "
    "wrong here — rewrite it as the measurable thing underneath:\n"
    "  'Will RELIANCE break out of the wedge?' -> 'Where are the wedge's "
    "edges now?'\n"
    "  'Is this a good entry?' -> 'How often has this pattern held on "
    "RELIANCE?'\n"
    "  'Should I wait for confirmation?' -> 'What would confirm the "
    "breakout?'\n\n"
    "Return exactly three lines. One question per line. No numbering, no "
    "bullets, no quotes, no commentary."
)


def suggest_clean(raw: str) -> str:
    """One suggestion line, as it should be shown.

    Shared with the client, which applies the SAME two rules to the partial
    line it is drawing mid-stream. If the two disagreed, every suggestion
    would visibly twitch the moment the final list replaced the streamed one.
    """
    # the model still sometimes numbers them despite being told not to
    line = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", raw).strip()
    return line.strip("\"'").strip()


def _suggest_stream(messages: list[dict]):
    """Yield SSE events for three follow-ups: deltas, then a final list.

    An isolated sub-call, deliberately: the main turn is already streamed and
    finished by the time this runs, and giving the chat agent a fourth job
    would put these words through the whole tool loop and the system contract
    to produce thirty tokens.

    It STREAMS for the same reason the answer above it does — the first
    question is readable about a second in, rather than three lines appearing
    at once when the call returns. There is no typewriter here: what arrives
    is what the model has actually written so far.

    Every failure path ends in a `done` carrying [] and the row simply never
    appears. A suggestion is a convenience; nothing here may cost an answer.
    """
    tail = []
    for m in messages[-8:]:
        role = m.get("role")
        if role not in ("user", "assistant"):
            continue
        body = str(m.get("content") or "")[:1200]
        if body.strip():
            tail.append({"role": role, "content": body})
    if not tail:
        yield {"type": "done", "suggestions": []}
        return
    payload = {
        "model": LLM_DEPLOYMENT,
        "input": [{"role": "system", "content": SUGGEST_PROMPT}, *tail],
        "max_output_tokens": 700,
        "reasoning": {"effort": "low"},
        "service_tier": LLM_SERVICE_TIER,
        "stream": True,
    }
    req = urllib.request.Request(
        f"{AZURE_ENDPOINT}/responses",
        data=json.dumps(payload).encode(),
        headers={"api-key": AZURE_KEY, "Content-Type": "application/json"},
        method="POST")
    text = []
    try:
        with urllib.request.urlopen(req, timeout=30, context=_ssl_ctx()) as r:
            for raw in r:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                body = line[5:].strip()
                if not body or body == "[DONE]":
                    continue
                try:
                    ev = json.loads(body)
                except json.JSONDecodeError:
                    continue
                if ev.get("type") == "response.output_text.delta":
                    d = ev.get("delta") or ""
                    if d:
                        text.append(d)
                        yield {"type": "delta", "text": d}
    except Exception as exc:  # noqa: BLE001 — a dead suggest must not surface
        logging.info("suggest failed: %s", exc)
        yield {"type": "done", "suggestions": []}
        return
    out, seen = [], set()
    for raw in "".join(text).splitlines():
        line = suggest_clean(raw)
        if not line or len(line) > 90 or line.lower() in seen:
            continue
        seen.add(line.lower())
        out.append(line)
        if len(out) == 3:
            break
    yield {"type": "done", "suggestions": out if len(out) == 3 else []}


def _suggest_events(messages: list[dict], answer: str):
    """The follow-ups, re-tagged to ride the ANSWER's stream instead of their own.

    Why they moved off /suggest. A second endpoint is the obvious shape and it
    is what we shipped, but it costs two things. The small one is a hop, and
    hop count is the latency lever here. The large one is that a new route is
    invisible in production until nginx's allowlist learns it — and nginx is a
    record in this repo that a human has to install, so /suggest spent its
    whole life answering 200 text/html on the VM while working perfectly on
    localhost. /chat is already allowlisted, already unbuffered, already
    ungzipped. Nothing that rides it can go missing that way.

    This is also what the products doing it well do: Perplexity returns its
    related questions in the same response as the answer, and the Vercel AI
    SDK's whole "data parts" mechanism exists to put typed extras on the one
    stream. A separate call is the older shape.

    Emitted AFTER the turn's `done`, never before. The client has the answer,
    the scene patch and the view ops in hand and has already acted on them by
    the time the first of these arrives, so the three questions cost the reply
    nothing — which is the same rule /suggest had: a suggestion is a
    convenience, and nothing here may cost an answer. A failure yields an
    empty list and the row simply never appears.
    """
    convo = [m for m in messages if m.get("role") in ("user", "assistant")]
    convo = convo + [{"role": "assistant", "content": answer}]
    try:
        for ev in _suggest_stream(convo):
            if ev.get("type") == "delta":
                yield {"type": "suggest_delta", "text": ev.get("text") or ""}
            elif ev.get("type") == "done":
                yield {"type": "suggest_done",
                       "suggestions": ev.get("suggestions") or []}
    except Exception as exc:  # noqa: BLE001 — never let a follow-up break a turn
        logging.info("suggest events failed: %s", exc)
        yield {"type": "suggest_done", "suggestions": []}


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


_MAX_TOOL_ROUNDS = 4  # bounds latency; 1 round answers almost everything
# Why 4 and not 3. Every detector that can DRAW does it in its own call
# (get_levels, volume_profile, get_indicator), so those finish inside two
# rounds and drew reliably. get_anchors is the exception by design — it mints
# ids that draw_shape composes — which makes "mark the range" a THREE-round
# path: explain, anchor, draw. On a compound turn ("why is it stuck in a
# range, and mark the boundaries") the causal half spent the first round and
# the turn ended on get_anchors with the drawing never made. Measured over
# the why-set: every zero-draw case that had asked for one ended on either
# get_anchors or a get_levels called with draw=false, with no round left to
# correct it.


def _wire_messages(messages: list[dict]) -> list[dict]:
    """History → Responses-API input items, screenshots included honestly.

    A user message may carry `image` (a data-URI chart screenshot). Only the
    NEWEST one goes to the model — re-shipping every past screenshot on every
    turn would grow input cost without bound — and an older message that had
    one says so in text, so the model never half-remembers an image it can no
    longer see.

    It may also carry `symbol`: the chart that was on screen when it was
    asked. A conversation is not a property of an instrument — one thread runs
    across RELIANCE, TCS, then GOLD — but the transcript read as though it
    were, because nothing in it said WHICH chart each turn was about. The
    envelope describes only NOW, and it sits at the TOP of the wire, above
    every turn: the model's most recent reading of "the chart" was six turns
    of NIFTY prose, not the one GOLD line preceding them. So the turns asked
    on a chart that is no longer in focus are marked as such, in place. Only
    those turns — a mark on every turn would be noise, and the ones that agree
    with the envelope need no correction."""
    last_img = max((i for i, m in enumerate(messages) if m.get("image")),
                   default=None)
    now_sym = str(getattr(_req, "symbol", "") or "").upper()
    # The FE stamps the chart on the USER turn. The reply that follows it is
    # about the same chart, so the stamp carries forward until the next one —
    # and the assistant prose is what actually names the stale ticker.
    seen_sym = ""
    out: list[dict] = []
    for i, m in enumerate(messages):
        role = m.get("role", "user")
        txt = str(m.get("content", ""))
        if m.get("symbol"):
            seen_sym = str(m["symbol"]).upper()
        if now_sym and seen_sym and seen_sym != now_sym:
            txt = (f"[asked while viewing {seen_sym}; the chart in focus is "
                   f"now {now_sym} — do not carry {seen_sym}'s numbers, "
                   f"levels or marked patterns onto it]\n" + txt)
        # a tagged drawing IS the subject of that message — state it as a
        # fact of the turn, so "is this any good?" has an unambiguous referent
        # instead of the model guessing which shape "this" means
        tag = m.get("drawing")
        if tag and tag.get("annotation"):
            # An annotation the CHAT drew, tagged from its card's "Ask about
            # this" — the same gesture as tagging a drawing, on the other
            # layer. Only SOME of these have a scoring path: a level, a zone,
            # a segment, a fib and a position convert to something the
            # evaluate tools understand (see _chat_drawing_as_user), and a
            # pattern's polygon does not. The frontend sends a ref only for
            # the first set, so this never sends the model after a handle
            # that cannot resolve — which would come back an error instead of
            # an answer about the shape the user is pointing at.
            what = tag.get("label") or tag.get("kind") or "annotation"
            where = f", on {tag['on']}" if tag.get("on") else ""
            facts = f" — {tag['detail']}" if tag.get("detail") else ""
            how = (f" Score it by passing drawing_id={tag['ref']}, never by "
                   f"retyping its coordinates."
                   if tag.get("ref") else
                   " It has no scoring method of its own, so answer from what "
                   "the chart context and your tools say about that region — "
                   "and never invent coordinates for it.")
            txt = (f"[the user tagged the annotation you drew: {what}{where}"
                   f"{facts}. That annotation is what this message is about, "
                   f"and it is still on the chart.{how}]\n" + txt)
        elif tag and tag.get("ref"):
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
        return {"error": _creds_error()}
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
    _req.ctx_interval = str((context or {}).get("interval") or "")
    tool_trace: list[dict] = []
    scene_patch: list[dict] = []
    view_ops: list[dict] = []
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
                "view_ops": view_ops,
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
            view_ops.extend(_view_take())
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
        yield {"type": "done", "error": _creds_error()}
        return
    block = "\n\n".join(x for x in (build_context_block(context), FORMAT_RULES, CAUSAL_RULES) if x)
    wire: list[dict] = []
    if block:
        wire.append({"role": "system", "content": block})
    wire += _wire_messages(messages)

    _scene_reset()
    _drawings_set(context)   # tools can now resolve a drawing by ref
    _req.ctx_interval = str((context or {}).get("interval") or "")
    tool_trace: list[dict] = []
    scene_patch: list[dict] = []
    view_ops: list[dict] = []
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
            answer = "".join(text_parts) or "(empty reply)"
            yield {"type": "done",
                   "text": answer,
                   "usage": {"input_tokens": tok_in, "output_tokens": tok_out},
                   "context_preview": block,
                   "tools_used": tool_trace,
                   "scene_patch": scene_patch, "view_ops": view_ops}
            # The turn is complete and delivered. What follows is the three
            # follow-ups on the same connection — see _suggest_events.
            yield from _suggest_events(messages, answer)
            return

        for call in calls:
            try:
                args = call.get("arguments") or "{}"
                args = json.loads(args) if isinstance(args, str) else args
            except json.JSONDecodeError:
                args = {}
            result = run_tool(call.get("name", ""), args)
            scene_patch.extend(_scene_take())
            view_ops.extend(_view_take())
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
           "scene_patch": scene_patch, "view_ops": view_ops}


IST_OFF = 19800  # +05:30
SESSION_OPEN_MIN = 9 * 60 + 15  # 09:15 IST, minutes past midnight

INTRADAY_MIN = {"1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30, "1h": 60}

# A bucket anchor is (session-open minute-of-day, tz offset from UTC). NSE is
# the default everywhere; the others exist because backfill_crypto.py and
# backfill_macro.py put non-NSE instruments in the same `bars` table. Anchoring
# a 24/7 series to 09:15 IST would collapse every bar from midnight to the open
# into one bucket, so crypto/FX days start at UTC midnight instead.
NSE_SESSION = (SESSION_OPEN_MIN, IST_OFF)
UTC_SESSION = (0, 0)                 # 24/7 crypto, 24/5 FX
MCX_SESSION = (9 * 60, IST_OFF)      # MCX opens 09:00 IST, runs to 23:30

_MCX_SYMBOLS = {"GOLD", "GOLDM", "SILVER", "SILVERM", "CRUDEOIL",
                "NATURALGAS", "COPPER", "ZINC", "ALUMINIUM",
                "LEAD", "NICKEL", "COTTON", "MENTHAOIL"}


def session_for(symbol: str) -> tuple[int, int]:
    """Bucket anchor for a symbol. Unknown symbols stay on the NSE clock."""
    if symbol.endswith("USDT") or symbol.endswith("-USD"):
        return UTC_SESSION
    if symbol in _MCX_SYMBOLS:
        return MCX_SESSION
    return NSE_SESSION


# The last 1-min bar of a FULL session, as a minute-of-day on the symbol's own
# clock (NSE closes 15:30, so its final minute OPENS at 15:29). Every value here
# is MEASURED off complete stored sessions, not read off an exchange brochure:
#   NSE 09:15->15:29 = 375 bars   MCX 09:00->23:29 = 870   CDS 09:00->16:59 = 480
_FX_SYMBOLS = {"USDINR", "EURINR", "GBPINR", "JPYINR"}
_FX_CLOSE_MIN = 16 * 60 + 59
_SESSION_CLOSE_MIN = {NSE_SESSION: 15 * 60 + 29,
                      MCX_SESSION: 23 * 60 + 29,
                      UTC_SESSION: 23 * 60 + 59}


def session_close_for(symbol: str) -> int:
    """Minute-of-day of the last bar of a COMPLETE session.

    Freshness cannot be read off MAX(ts): it cannot tell "the market closed at
    15:29" from "the fetcher died at 14:29". RELIANCE is stored to 23 Jul 14:29
    while its 40 peers stop at 22 Jul 15:29 — by MAX(ts) it looks the freshest
    of the lot and it is the only truncated one. Everything that judges a
    session complete measures against this instead.

    The INR pairs need their own entry rather than inheriting the NSE close:
    session_for() puts them on the NSE anchor, but the currency segment trades
    09:00-17:00, so a 15:29 bar would mark the day complete and every top-up
    would silently skip 15:30-16:59 forever.
    """
    if symbol in _FX_SYMBOLS:
        return _FX_CLOSE_MIN
    return _SESSION_CLOSE_MIN.get(session_for(symbol), 15 * 60 + 29)


# ── asset scope ───────────────────────────────────────────────────
# A pooled pattern rate is a property of a MARKET, not of a shape. 500 NSE
# stocks trade 375 minutes a day with a gap every night; Bitcoin trades 1440
# with no gap and no circuit limits. Averaging a hammer's forward return over
# both produces a number that describes neither, so scope is a stored
# dimension of the ledger (PRIMARY KEY carries it) rather than a filter
# applied afterwards. Interval stays a separate dimension for the same reason:
# a 10-bar horizon is two weeks on 1d and 2.5 hours on 15m.
SCOPES = ("equity_in", "index_in", "volatility_in", "crypto",
          "commodity_in", "fx_in")

_SCOPE_BY_INDUSTRY = {
    "indexbroad": "index_in", "indexsector": "index_in",
    "volatility": "volatility_in", "cryptocurrency": "crypto",
    "commoditypreciousmetals": "commodity_in",
    "commoditybasemetals": "commodity_in",
    "commodityenergy": "commodity_in",
    "commoditysoft": "commodity_in", "currency": "fx_in",
}

# India VIX started out pooled with the indices — it is quoted by the same
# exchange on the same session. The swept controls said otherwise: at h=10 on
# daily bars it rises 46.9% of the time against the 23 indices' 57.2%, and its
# average absolute 10-bar move is 11.21% against their 4.81%. A mean-reverting
# volatility series is not a price index, and pooling it would have pulled the
# index base rate down and handed every bearish shape a manufactured edge.
SCOPE_LABEL = {
    "equity_in": "NSE-listed stocks",
    "index_in": "Indian equity indices",
    "volatility_in": "India VIX",
    "crypto": "spot crypto (24/7)",
    "commodity_in": "MCX commodity futures",
    "fx_in": "INR currency futures",
}


def quote_ccy(symbol: str) -> str:
    """The currency a symbol's PRICE is quoted in — "INR" or "USD".

    Distinct from session_for on purpose. They happen to agree today, but one
    is about when a bar opens and the other about what its numbers mean; a
    turnover figure that inherits its unit from a timezone is one listing away
    from being wrong.
    """
    return "USD" if (symbol.endswith("USDT")
                     or symbol.endswith("-USD")) else "INR"


_scope_cache: dict[str, str] = {}


def scope_for(symbol: str) -> str:
    """Which pooled universe a symbol's evidence belongs to.

    Classification-first so adding an instrument to classify_macro.py is
    enough; the symbol-shape fallback keeps a freshly backfilled crypto pair
    out of the equity pool during the window before it is classified.
    """
    hit = _scope_cache.get(symbol)
    if hit:
        return hit
    row = _classification_row(symbol)          # (name, industry) or None
    ind = (row[1] if row else "") or ""
    if ind in _SCOPE_BY_INDUSTRY:
        sc = _SCOPE_BY_INDUSTRY[ind]
    elif symbol.endswith("USDT") or symbol.endswith("-USD"):
        sc = "crypto"
    elif symbol in _MCX_SYMBOLS:
        sc = "commodity_in"
    else:
        sc = "equity_in"
    _scope_cache[symbol] = sc
    return sc


_con = sqlite3.connect(DB_PATH, check_same_thread=False)
# The store is 9.7 GB and 89M minute rows; SQLite's default 2 MB page cache
# means a 180k-row read for an hourly chart re-reads pages it just evicted.
# Measured on the 1h path: 250ms -> 185ms, and a full-symbol minute scan
# 1548ms -> 867ms. mmap lets the OS page cache do the work instead of
# copying every page through SQLite's own buffer.
# Tunable because the right value depends on the host, not the code: a laptop
# store is 13 GB against plenty of RAM, the VM store is 23 GB against 8 GB, and
# there a cold symbol costs 6.24s against 0.74s warm (measured 2026-08-03 on
# SBIN, 1h/4000 through nginx). Negative = KiB.
_CACHE_KIB = int(environ.get("CHARTO_CACHE_KIB") or 262144)       # 256 MB
_MMAP_BYTES = int(environ.get("CHARTO_MMAP_BYTES") or 4294967296)  # 4 GB
_con.execute(f"PRAGMA cache_size=-{_CACHE_KIB}")
_con.execute(f"PRAGMA mmap_size={_MMAP_BYTES}")

# The served store carries deals only for symbols that have hydrated (41 of
# them, 1.7k rows). The market-wide sweep is 153k rows over 3,446 symbols,
# and the difference is not cosmetic: asked what one client has bought, the
# hydrated copy answers with the handful that happen to be local and reads
# as the whole truth. Under-disclosure is the one failure this surface
# cannot have, so the sweep is attached read-only and preferred when there.
#
# Attached by PLAIN path, not by a `file:...?mode=ro` URI. URI filenames are a
# compile-time option and this venv's SQLite (3.39.4) has them off — there the
# URI is taken as a LITERAL filename, so the attach "succeeds" against a newly
# created empty database and leaves a file called `file:flows_market.db?mode=ro`
# on disk. A read-only flag that silently invents an empty store is worse than
# no flag: what actually keeps this read-only is that nothing writes to `mkt.`.
# Hence the probe below — an attach that cannot see `deals` is detached again
# rather than left to answer questions with nothing in it.
_MKT_PATH = Path(__file__).parent / "flows_market.db"
_HAVE_MKT = False
try:
    if _MKT_PATH.exists():
        _con.execute("ATTACH DATABASE ? AS mkt", (str(_MKT_PATH),))
        _HAVE_MKT = bool(_con.execute("SELECT 1 FROM mkt.deals LIMIT 1").fetchone())
        if not _HAVE_MKT:
            _con.execute("DETACH DATABASE mkt")
except sqlite3.Error as exc:  # noqa: BLE001 — absent sweep degrades, never kills
    logging.warning("market flows attach failed: %s", exc)
_daily_cache: dict[str, list[list]] = {}   # symbol -> daily bars (ascending)


# ── accounts, sessions and saved work ──────────────────────────────────────
#
# A SEPARATE database, and that is the whole point. charto_bars.db is a 9.7 GB
# derived store — rebuilt from the blob universe, re-synced, occasionally
# dropped and re-imported. Accounts and a user's saved layouts are the only
# data here that CANNOT be regenerated from an upstream source, so they must
# not share a file with the one that gets thrown away and rebuilt.
#
# Auth is a bearer token, not a cookie. The chart is served from :5173 and this
# API answers on :5174 — cross-origin, where a cookie needs SameSite=None plus
# Secure plus an echoed origin plus Allow-Credentials, and still behaves
# differently across browsers on plain http. A token in an Authorization
# header needs none of that. The trade is honest: a bearer token in
# localStorage is readable by any XSS on the page, where an HttpOnly cookie
# would not be. For a locally-served analysis tool that is the right side of
# the trade; if this is ever hosted for real, revisit it.
# Test/dev can isolate account state while reading the same immutable market
# store. Production keeps the adjacent durable DB unless explicitly overridden.
_USERS_PATH = Path(environ.get("CHARTO_USERS_DB")
                   or Path(__file__).parent / "charto_users.db")
_users = sqlite3.connect(_USERS_PATH, check_same_thread=False)
_users.execute("PRAGMA journal_mode=WAL")
_users.execute("PRAGMA foreign_keys=ON")
_users.execute("PRAGMA busy_timeout=10000")
_users.executescript("""
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  email TEXT NOT NULL UNIQUE COLLATE NOCASE,
  name TEXT,
  pw_hash BLOB NOT NULL,
  pw_salt BLOB NOT NULL,
  created INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS sessions (
  token TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id),
  created INTEGER NOT NULL,
  last_seen INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS sessions_user ON sessions(user_id);
-- one row per (user, symbol, key): the chat transcript, the scene the chat
-- drew, the drawings, the volume-profile window. Exactly the keys Store
-- already scopes by symbol in localStorage, so the FE contract is unchanged.
CREATE TABLE IF NOT EXISTS workspace_state (
  user_id INTEGER NOT NULL REFERENCES users(id),
  symbol TEXT NOT NULL,
  key TEXT NOT NULL,
  json TEXT NOT NULL,
  updated INTEGER NOT NULL,
  PRIMARY KEY (user_id, symbol, key));
CREATE TABLE IF NOT EXISTS layouts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id),
  name TEXT NOT NULL,
  spec TEXT NOT NULL,
  updated INTEGER NOT NULL,
  UNIQUE (user_id, name));
-- Past conversations, so "what did we decide about ITC last week" has an
-- answer. TEXT ONLY: no images, no chart context, no scene patches. This
-- exists to be READ BACK by recall_conversations, and a stored screenshot
-- would be a large private thing kept for a feature that cannot use it.
CREATE TABLE IF NOT EXISTS conversations (
  user_id INTEGER NOT NULL REFERENCES users(id),
  chat_id TEXT NOT NULL,
  title   TEXT NOT NULL,
  symbols TEXT NOT NULL DEFAULT '',   -- comma-separated, for "that TCS chat"
  started INTEGER NOT NULL,
  updated INTEGER NOT NULL,
  turns   TEXT NOT NULL,              -- JSON [{role, content}]
  PRIMARY KEY (user_id, chat_id));
CREATE INDEX IF NOT EXISTS conversations_recent
  ON conversations(user_id, updated DESC);
""")

# `layouts` predates the save/open/share system and had five columns. Added
# here rather than in the CREATE above so an existing account keeps its saved
# work: the CREATE is a no-op once the table exists, so a new column has to
# arrive as an ALTER or it only ever appears on a fresh database.
for _col, _decl in (
        ("created", "INTEGER NOT NULL DEFAULT 0"),
        ("opened", "INTEGER NOT NULL DEFAULT 0"),      # for RECENTLY USED
        ("symbols", "TEXT NOT NULL DEFAULT ''"),       # summary line, no parse
        ("autosave", "INTEGER NOT NULL DEFAULT 0"),
        ("chat_id", "TEXT NOT NULL DEFAULT ''"),       # the conversation had here
        ("thumb", "TEXT NOT NULL DEFAULT ''"),         # data: URI, ~30 KB JPEG
        ("share_token", "TEXT")):                      # NULL = private
    try:
        _users.execute(f"ALTER TABLE layouts ADD COLUMN {_col} {_decl}")
    except sqlite3.OperationalError:
        pass                                           # already there
_users.execute("CREATE UNIQUE INDEX IF NOT EXISTS layouts_share "
               "ON layouts(share_token) WHERE share_token IS NOT NULL")
_users.commit()
_users_lock = threading.Lock()

# Loaded only after the users table exists: journal.py adds foreign-keyed
# records to this same durable account database. A broken optional feature
# must not stop charts, chat or auth from starting.
try:
    import journal as _journal
except Exception as _journal_exc:  # noqa: BLE001
    logging.warning("charto journal unavailable: %s", _journal_exc)
    _journal = None

_SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1}   # ~100ms/hash, OWASP-tier for scrypt
_SESSION_TTL = 30 * 86400


def _pw_hash(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT, dklen=32)


def _user_public(row: tuple) -> dict:
    return {"id": row[0], "email": row[1], "name": row[2]}


def _auth_user(headers) -> tuple | None:
    """The user behind an Authorization: Bearer token, or None.

    Touches last_seen so an active session does not expire under someone who
    is plainly still using it.
    """
    raw = headers.get("Authorization") or ""
    if not raw.startswith("Bearer "):
        return None
    tok, now = raw[7:].strip(), int(time.time())
    with _users_lock:
        row = _users.execute(
            "SELECT u.id, u.email, u.name, s.created FROM sessions s "
            "JOIN users u ON u.id = s.user_id WHERE s.token=?", (tok,)).fetchone()
        if not row or now - row[3] > _SESSION_TTL:
            if row:
                _users.execute("DELETE FROM sessions WHERE token=?", (tok,))
                _users.commit()
            return None
        _users.execute("UPDATE sessions SET last_seen=? WHERE token=?", (now, tok))
        _users.commit()
    return row[:3]


def _issue_session(user_id: int) -> str:
    tok, now = secrets.token_urlsafe(32), int(time.time())
    with _users_lock:
        _users.execute("INSERT INTO sessions VALUES (?,?,?,?)",
                       (tok, user_id, now, now))
        _users.commit()
    return tok


def _auth_signup(body: dict) -> tuple[int, dict]:
    email = str(body.get("email") or "").strip().lower()
    pw = str(body.get("password") or "")
    name = (str(body.get("name") or "").strip() or None)
    if "@" not in email or "." not in email.split("@")[-1]:
        return 400, {"error": "enter a valid email address"}
    if len(pw) < 8:
        return 400, {"error": "password must be at least 8 characters"}
    salt = secrets.token_bytes(16)
    now = int(time.time())
    try:
        with _users_lock:
            cur = _users.execute(
                "INSERT INTO users (email, name, pw_hash, pw_salt, created) "
                "VALUES (?,?,?,?,?)",
                (email, name, _pw_hash(pw, salt), salt, now))
            _users.commit()
            uid = cur.lastrowid
    except sqlite3.IntegrityError:
        return 409, {"error": "an account already exists for that email"}
    return 200, {"token": _issue_session(uid),
                 "user": {"id": uid, "email": email, "name": name}}


def _auth_login(body: dict) -> tuple[int, dict]:
    email = str(body.get("email") or "").strip().lower()
    pw = str(body.get("password") or "")
    with _users_lock:
        row = _users.execute(
            "SELECT id, email, name, pw_hash, pw_salt FROM users WHERE email=?",
            (email,)).fetchone()
    # The same reply and the same work either way: branching early on "no such
    # user" tells an attacker which emails are registered, and skipping the
    # hash makes that difference measurable on the clock as well.
    salt = row[4] if row else secrets.token_bytes(16)
    want = row[3] if row else b"\0" * 32
    if not hmac.compare_digest(_pw_hash(pw, salt), want):
        return 401, {"error": "email or password is incorrect"}
    return 200, {"token": _issue_session(row[0]), "user": _user_public(row)}


# ── saved work: per-symbol workspace state, and named layouts ──────────────
#
# workspace_state mirrors exactly what Store already keeps in localStorage
# (chat / scene / vp / drawings, scoped by symbol) so signing in changes WHERE
# the same shape is kept, not what it is. Logged out, the FE keeps using
# localStorage and nothing here is reached.

def _ws_get(uid: int, symbol: str) -> dict:
    with _users_lock:
        rows = _users.execute(
            "SELECT key, json FROM workspace_state WHERE user_id=? AND symbol=?",
            (uid, symbol)).fetchall()
    out = {}
    for k, blob in rows:
        try:
            out[k] = json.loads(blob)
        except ValueError:      # a corrupt blob must not take the workspace down
            logging.warning("charto: unreadable workspace %s/%s/%s", uid, symbol, k)
    return out


def _ws_put(uid: int, symbol: str, state: dict) -> int:
    now, n = int(time.time()), 0
    with _users_lock:
        for k, v in (state or {}).items():
            _users.execute(
                "INSERT INTO workspace_state VALUES (?,?,?,?,?) "
                "ON CONFLICT(user_id, symbol, key) DO UPDATE SET "
                "json=excluded.json, updated=excluded.updated",
                (uid, symbol, str(k), json.dumps(v), now))
            n += 1
        _users.commit()
    return n


_LAYOUT_COLS = ("id, name, symbols, created, updated, opened, autosave, "
                "chat_id, share_token")


def _layout_row(r: tuple) -> dict:
    """A layout WITHOUT its spec — what a list needs and no more.

    The spec is the whole workspace (every pane, every drawing, the scene);
    forty of them in one response would be megabytes to render a menu.
    `shared` is a boolean, never the token: a list of layouts is not a place
    to hand out live links to all of them at once.
    """
    return {"id": r[0], "name": r[1], "symbols": [s for s in r[2].split(",") if s],
            "created": r[3], "updated": r[4], "opened": r[5],
            "autosave": bool(r[6]), "chat_id": r[7], "shared": bool(r[8])}


_THUMB_MAX = 220_000     # a 480px JPEG is ~30 KB; this is the runaway guard


def _layouts_list(uid: int, thumbs: bool = False) -> list[dict]:
    """The index. Thumbnails only when asked for, and that is the point.

    A picture per layout is 30 KB. Forty of them is over a megabyte, which is
    the wrong price for opening a dropdown — so the menu asks without them and
    the Open dialog, where a user has deliberately gone to LOOK at their
    layouts, asks with.
    """
    cols = _LAYOUT_COLS + (", thumb" if thumbs else "")
    with _users_lock:
        rows = _users.execute(
            f"SELECT {cols} FROM layouts WHERE user_id=? "
            "ORDER BY updated DESC", (uid,)).fetchall()
    out = []
    for r in rows:
        rec = _layout_row(r)
        if thumbs:
            rec["thumb"] = r[9]
        out.append(rec)
    return out


def _layout_get(uid: int, lid: int) -> tuple[int, dict]:
    """One layout, spec included, and stamp it as the most recently opened."""
    now = int(time.time())
    with _users_lock:
        r = _users.execute(f"SELECT {_LAYOUT_COLS}, spec, thumb FROM layouts "
                           "WHERE user_id=? AND id=?", (uid, lid)).fetchone()
        if not r:
            return 404, {"error": "no such layout"}
        _users.execute("UPDATE layouts SET opened=? WHERE user_id=? AND id=?",
                       (now, uid, lid))
        _users.commit()
    out = _layout_row(r)
    out["opened"] = now
    # presence, not the bytes: the caller is about to draw this layout, not
    # show a picture of it, and it only needs to know whether to backfill one
    out["has_thumb"] = bool(r[10])
    try:
        out["spec"] = json.loads(r[9])
    except ValueError:
        return 500, {"error": "this layout's saved state is unreadable"}
    return 200, out


def _layout_free_name(uid: int, want: str) -> str:
    """`want`, or `want (2)`, `want (3)`… — the first one not taken.

    Names are unique per user because that is what makes "save over the one
    I opened" unambiguous. A copy therefore cannot reuse the name, and
    failing the request would be a worse answer than picking the obvious
    next one — which is what every file manager does.

    THE CALLER HOLDS `_users_lock`. Every caller is already inside it to make
    the check-then-insert atomic, and `_users_lock` is a plain Lock, not an
    RLock — taking it again here deadlocked the request thread outright.
    """
    taken = {n for (n,) in _users.execute(
        "SELECT name FROM layouts WHERE user_id=?", (uid,))}
    if want not in taken:
        return want
    for i in range(2, 500):
        cand = f"{want} ({i})"
        if cand not in taken:
            return cand
    return f"{want} {secrets.token_hex(3)}"


def _clean_thumb(v) -> str:
    """A layout thumbnail, or "". Only a small inline JPEG/PNG is accepted.

    This column is echoed straight back into an <img src>, so it must not be
    able to carry anything else — a `javascript:` or `data:text/html` URI
    would execute in the picker. Prefix-checked and size-capped rather than
    trusted because the client sent it.
    """
    s = str(v or "")
    if not s.startswith(("data:image/jpeg;base64,", "data:image/png;base64,")):
        return ""
    return s if len(s) <= _THUMB_MAX else ""


def _layout_save(uid: int, name: str, spec: dict, lid: int | None = None,
                 symbols: list | None = None, autosave: bool | None = None,
                 chat_id: str = "", thumb: str = "") -> tuple[int, dict]:
    """Create or overwrite. `id` given means SAVE OVER that one, even renamed.

    Without an id this is "Create new layout", and a clashing name gets the
    next free one rather than silently overwriting work the user cannot see.
    """
    name = (name or "").strip()[:120]
    if not name:
        return 400, {"error": "a layout needs a name"}
    now = int(time.time())
    syms = ",".join(dict.fromkeys(str(s).upper()[:24] for s in (symbols or []) if s))
    blob = json.dumps(spec or {})
    with _users_lock:
        if lid:
            owned = _users.execute("SELECT id FROM layouts WHERE user_id=? AND id=?",
                                   (uid, lid)).fetchone()
            if not owned:
                return 404, {"error": "no such layout"}
            clash = _users.execute("SELECT id FROM layouts WHERE user_id=? AND "
                                   "name=? AND id<>?", (uid, name, lid)).fetchone()
            if clash:
                return 409, {"error": f"you already have a layout called “{name}”"}
            sets = "name=?, spec=?, symbols=?, updated=?, opened=?"
            args: list = [name, blob, syms, now, now]
            pic = _clean_thumb(thumb)
            if pic:
                # only when one was sent — a save from a chart that could not
                # be captured must not blank the picture already stored
                sets += ", thumb=?"
                args.append(pic)
            if autosave is not None:
                sets += ", autosave=?"
                args.append(1 if autosave else 0)
            if chat_id:
                sets += ", chat_id=?"
                args.append(chat_id[:64])
            _users.execute(f"UPDATE layouts SET {sets} WHERE user_id=? AND id=?",
                           (*args, uid, lid))
            _users.commit()
            new_id = lid
        else:
            # A clashing name gets the next free one rather than a 500 from
            # the UNIQUE index — "Create new layout" while one called Untitled
            # already exists is the most ordinary thing a user can do, and it
            # must not fail, nor silently overwrite work they cannot see.
            name = _layout_free_name(uid, name)
            _users.execute(
                "INSERT INTO layouts (user_id, name, spec, symbols, created, "
                "updated, opened, autosave, chat_id, thumb) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (uid, name, blob, syms, now, now, now,
                 1 if autosave else 0, chat_id[:64], _clean_thumb(thumb)))
            _users.commit()
            new_id = _users.execute("SELECT id FROM layouts WHERE user_id=? AND "
                                    "name=?", (uid, name)).fetchone()[0]
    return 200, {"id": new_id, "name": name, "updated": now, "opened": now}


def _layout_copy(uid: int, lid: int) -> tuple[int, dict]:
    with _users_lock:
        r = _users.execute("SELECT name, spec, symbols, chat_id, thumb "
                           "FROM layouts WHERE user_id=? AND id=?",
                           (uid, lid)).fetchone()
    if not r:
        return 404, {"error": "no such layout"}
    now = int(time.time())
    # A copy is NOT shared even if its original is: a share token names one
    # layout, and duplicating the link along with the contents would publish
    # something the user only asked to duplicate.
    with _users_lock:
        name = _layout_free_name(uid, f"{r[0]} copy")
        _users.execute(
            "INSERT INTO layouts (user_id, name, spec, symbols, created, "
            "updated, opened, autosave, chat_id, thumb) "
            "VALUES (?,?,?,?,?,?,?,0,?,?)",
            (uid, name, r[1], r[2], now, now, now, r[3], r[4]))
        _users.commit()
        new_id = _users.execute("SELECT id FROM layouts WHERE user_id=? AND name=?",
                                (uid, name)).fetchone()[0]
    return 200, {"id": new_id, "name": name, "updated": now}


def _layout_share(uid: int, lid: int, on: bool) -> tuple[int, dict]:
    """Mint or revoke an unlisted read-only link.

    Revoking DELETES the token rather than flagging it, so a link that was
    turned off is dead the moment it is turned off — a disabled row that
    still holds a valid-looking token is the shape of an accident.
    """
    with _users_lock:
        if not _users.execute("SELECT 1 FROM layouts WHERE user_id=? AND id=?",
                              (uid, lid)).fetchone():
            return 404, {"error": "no such layout"}
        tok = secrets.token_urlsafe(18) if on else None
        _users.execute("UPDATE layouts SET share_token=? WHERE user_id=? AND id=?",
                       (tok, uid, lid))
        _users.commit()
    return 200, {"id": lid, "shared": bool(tok), "token": tok}


def _layout_shared_get(token: str) -> tuple[int, dict]:
    """A shared layout, for anyone holding the link. No account needed.

    Read-only and deliberately thin: the workspace and who made it, never the
    owner's email, their other layouts, or the conversation that was had in
    it. A shared chart is a chart, not a window into an account.
    """
    tok = (token or "").strip()
    if len(tok) < 16:
        return 404, {"error": "not found"}
    with _users_lock:
        r = _users.execute(
            "SELECT l.name, l.symbols, l.updated, l.spec, u.name "
            "FROM layouts l JOIN users u ON u.id = l.user_id "
            "WHERE l.share_token=?", (tok,)).fetchone()
    if not r:
        return 404, {"error": "this link is not active"}
    try:
        spec = json.loads(r[3])
    except ValueError:
        return 500, {"error": "this layout's saved state is unreadable"}
    spec.pop("chat", None)          # never travels with a link
    return 200, {"name": r[0], "symbols": [s for s in r[1].split(",") if s],
                 "updated": r[2], "by": r[4] or "a Charto user",
                 "read_only": True, "spec": spec}


_CONV_KEEP = 200          # conversations retained per user
_CONV_TURNS = 80          # turns retained per conversation
_CONV_CHARS = 4000        # characters retained per turn


def _conv_sync(uid: int, chats: list) -> dict:
    """Mirror the browser's conversation archive into the DB.

    The browser is the OWNER of a conversation while it is being had — this is
    a copy kept so a LATER session can be asked about an earlier one, which is
    the only thing that needs it. So it is a plain upsert of whatever the
    client sends, not a merge: the client's copy is the one the user has been
    reading.

    Stripped to text on the way in. A turn carries a screenshot, a chart
    context envelope and a scene patch; none of that can be read back by a
    recall, and keeping a user's screenshots on a server for a feature that
    cannot use them is a cost with no matching benefit.
    """
    now, wrote = int(time.time()), 0
    with _users_lock:
        for c in chats[:_CONV_KEEP]:
            cid = str((c or {}).get("id") or "").strip()[:64]
            turns = [t for t in ((c or {}).get("turns") or [])
                     if isinstance(t, dict) and t.get("role") in ("user", "assistant")
                     and str(t.get("content") or "").strip()]
            if not cid or not turns:
                continue          # an empty conversation is not a record
            lean = [{"role": t["role"],
                     "content": str(t["content"])[:_CONV_CHARS]}
                    for t in turns[-_CONV_TURNS:]]
            # The title is the first thing the user said, which is what they
            # will recognise it by — never the model's opening line.
            first = next((t["content"] for t in lean if t["role"] == "user"), "")
            _users.execute(
                "INSERT INTO conversations "
                "(user_id, chat_id, title, symbols, started, updated, turns) "
                "VALUES (?,?,?,?,?,?,?) "
                "ON CONFLICT(user_id, chat_id) DO UPDATE SET "
                "title=excluded.title, symbols=excluded.symbols, "
                "updated=excluded.updated, turns=excluded.turns",
                (uid, cid, str(first)[:200],
                 ",".join(sorted({str(s).upper()[:24]
                                  for s in ((c or {}).get("symbols") or [])})),
                 int((c or {}).get("created") or now * 1000) // 1000,
                 int((c or {}).get("updated") or now * 1000) // 1000,
                 json.dumps(lean)))
            wrote += 1
        # Keep the archive bounded per user, oldest first.
        _users.execute(
            "DELETE FROM conversations WHERE user_id=? AND chat_id NOT IN ("
            "  SELECT chat_id FROM conversations WHERE user_id=? "
            "  ORDER BY updated DESC LIMIT ?)", (uid, uid, _CONV_KEEP))
        _users.commit()
    return {"saved": wrote}


def _ist_day(ts: int, tz_off: int = IST_OFF) -> int:
    return (ts + tz_off) // 86400


def _bucket_stamp(ts: int, minutes: int,
                  session: tuple[int, int] = NSE_SESSION) -> tuple[tuple[int, int], int]:
    """(day, bucket) identity and the stamped bar-open ts a 1-min row falls in.

    The single source of bucket arithmetic: the historical resampler and the
    live bar builder both call it, so a forming bar can never land on a
    different stamp than the same minute would get after it is closed.
    """
    open_min, tz_off = session
    local = ts + tz_off
    day = local // 86400
    mod = (local % 86400) // 60
    bucket = max(0, mod - open_min) // minutes
    return (day, bucket), day * 86400 + (open_min + bucket * minutes) * 60 - tz_off


def _resample_intraday(rows: list[tuple], minutes: int,
                       session: tuple[int, int] = NSE_SESSION) -> list[list]:
    """rows = ascending (ts,o,h,l,c,v) 1-min bars → bucketed bars.

    Buckets anchor to each session's minute-of-day relative to 09:15 IST so
    every trading day starts a fresh, aligned bucket (evening specials like
    Muhurat land in later buckets of the same day — still consistent).
    Pass `session` for instruments on another clock (see session_for).
    """
    out: list[list] = []
    cur_key = None
    for ts, o, h, l, c, v in rows:
        if not (o and h and l and c):
            continue   # all-zero placeholder minutes are no-data, not prices
        key, bts = _bucket_stamp(ts, minutes, session)
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


def _fold_daily(rows: list[tuple],
                session: tuple[int, int] = NSE_SESSION) -> list[list]:
    """ascending 1-min rows → one bar per trade date on the symbol's clock."""
    tz_off = session[1]
    out: list[list] = []
    cur_day = None
    for ts, o, h, l, c, v in rows:
        if not (o and h and l and c):
            continue   # zero placeholder minutes: same rule as _resample_intraday
        day = _ist_day(ts, tz_off)
        if day != cur_day:
            out.append([day * 86400 - tz_off, o, h, l, c, v])
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
    # Folding the whole minute history to get daily bars re-derives, every
    # time a symbol is first opened, something bars_1d already stores: 1.06M
    # rows read and folded (1548ms) to reproduce 2837 rows that read in 5ms.
    #
    # So read the stored dailies and fold ONLY the minutes newer than the last
    # one. bars_1d is written by the universe import on its own schedule and
    # the minute table is topped up on another, so trusting it wholesale would
    # show a stale last candle the moment the two diverge — the tail fold is
    # what keeps this a speed change rather than a freshness regression.
    session = session_for(symbol)
    stored = [list(r) for r in _con.execute(
        "SELECT ts,o,h,l,c,v FROM bars_1d WHERE symbol=? ORDER BY ts",
        (symbol,))]
    if stored:
        # re-fold the last stored day too: it may have been written while the
        # session was still open, so its high/low/close can still move
        cut = stored[-1][0]
        tail = _fold_daily(_con.execute(
            "SELECT ts,o,h,l,c,v FROM bars WHERE symbol=? AND ts>=? ORDER BY ts",
            (symbol, cut)).fetchall(), session)
        by_day = {r[0]: r for r in tail}
        out = [by_day.pop(r[0], r) for r in stored]
        out.extend(by_day[k] for k in sorted(by_day))
    else:
        out = _fold_daily(_con.execute(
            "SELECT ts,o,h,l,c,v FROM bars WHERE symbol=? ORDER BY ts",
            (symbol,)).fetchall(), session)
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
_live_writer = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=60)
# A closing minute gets ONE chance: it is written the instant the bar closes
# and the forming state is then reset, so a SQLITE_BUSY here loses that minute
# permanently rather than deferring it. The default busy timeout is 0, which
# means any concurrent backfill holding the write lock — and a crypto refetch
# holds it for minutes at a time — would silently punch holes in live data.
_live_writer.execute("PRAGMA busy_timeout=60000")
# Every use of _live_writer goes through this. sqlite3 allows the connection to
# be shared across threads, but NOT interleaved transactions on it — and with a
# venue driver per socket there are now several tick threads. Measured
# 2026-08-02 with Coinbase and Bybit live together: thread A's INSERT opened a
# transaction, thread B's commit closed it, and A's commit then raised
# "cannot commit - no transaction is active", which surfaced as
# "trade dropped for ADAUSDT". That is a lost minute, not a cosmetic warning.
_WRITER_LOCK = threading.Lock()
_LIVE_MIN_GAP = 0.25   # ≤4 pushes/sec/symbol, minute closes always push


def _exact_vol(x) -> int | float:
    """Volume, exactly — integral when it is, fractional when it is not.

    The closed-bar write used to be `int(f[5])`, which truncates toward zero.
    On NSE that is free (volume is a share count) but a fraction of a coin is a
    normal crypto minute: measured 2026-08-02, 990,989 of BTC-USD's 5,731,677
    stored minutes (17.3%) carried v=0 for minutes that really traded, and the
    volume profile reads exactly these bars. `v INTEGER` is an AFFINITY, not a
    constraint — SQLite stores a REAL that cannot be narrowed losslessly as a
    REAL — so keeping the fraction needs no migration and NSE rows stay ints.
    """
    f = float(x or 0)
    return int(f) if f.is_integer() else round(f, 8)


def _hm_ist(ts: int) -> str:
    t = datetime.fromtimestamp(ts + _tz_off(), tz=timezone.utc)
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
    sess = session_for(sym)
    day0 = _ist_day(form[0], sess[1]) * 86400 - sess[1]
    with _WRITER_LOCK:
        mins = _live_writer.execute(
            "SELECT ts,o,h,l,c,v FROM bars WHERE symbol=? AND ts>=? AND ts<? "
            "ORDER BY ts", (sym, day0, form[0])).fetchall()
    tail = tuple(form)
    out = {}
    for name, m in INTRADAY_MIN.items():
        _, bts = _bucket_stamp(form[0], m, sess)
        b = _resample_intraday([r for r in mins if r[0] >= bts] + [tail], m, sess)[-1]
        out[name] = {"t": b[0], "o": b[1], "h": b[2], "l": b[3],
                     "c": b[4], "v": b[5]}
    d = _fold_daily(mins + [tail], sess)[-1]
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


# ── the watcher ───────────────────────────────────────────────────
# Set by the boot block below, which imports alerts.py AFTER the module alias
# is in place — the same requirement kite_stream has, and for the same reason:
# a second copy of this module would hold a second _LIVE and the hook would
# feed a watcher nothing. None means the routes answer 501 rather than 500.
_alerts = None


# ── the watcher's seam ────────────────────────────────────────────
# alerts.py registers here at boot. It stays None on a build without that
# module, and it is called through _bar_hook rather than directly for one
# reason: an exception raised into _live_on_tick would abort the tick, and the
# forming bar it was in the middle of maintaining is how minutes get stored.
# A watcher bug must cost an alert, never a candle.
_ON_BAR = None


def register_bar_hook(fn) -> None:
    global _ON_BAR
    _ON_BAR = fn


def _bar_hook(sym: str, form: list, closed: bool) -> None:
    fn = _ON_BAR
    if fn is None:
        return
    try:
        fn(sym, form, closed)
    except Exception:                                 # noqa: BLE001
        logging.warning("charto bar hook failed on %s", sym, exc_info=True)


def _live_on_tick(sym: str, ts: int, price: float, vol: int) -> None:
    """The one seam every tick source calls. ts = the tick's epoch second."""
    sess = session_for(sym)
    open_min, tz_off = sess
    if price <= 0:
        return    # a zero print is not a trade and must not enter any candle
    mod = ((ts + tz_off) % 86400) // 60
    if mod < open_min or mod > session_close_for(sym):
        return
    # ^ gated on the SYMBOL's clock at BOTH ends: a 09:15-IST gate would have
    # dropped every crypto tick between UTC midnight and 03:45 UTC as
    # "pre-open", and an open-only gate accepted after-hours prints. Measured
    # 2026-08-02 (a Sunday): connecting the Kite ticker to a shut market still
    # delivered one snapshot at ~17:50 IST with no exchange timestamp, so the
    # wall-clock fallback bucketed it into a 17:50 minute. It survived only
    # because a forming bar needs a second tick to flush — two would have
    # written a Sunday candle into RELIANCE that no session ever traded.
    _, bts = _bucket_stamp(ts, 1, sess)
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
                with _WRITER_LOCK:
                    _live_writer.execute(
                        "INSERT OR REPLACE INTO bars VALUES (?,?,?,?,?,?,?)",
                        (sym, f[0], f[1], f[2], f[3], f[4], _exact_vol(f[5])))
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
        _bar_hook(sym, closed_bar, True)
    _live_push(sym, snap, False)
    # The watcher sees exactly what the chart sees, at exactly the same
    # cadence: ≤4 snapshots/sec plus every close, never more.
    _bar_hook(sym, snap, False)


def _merge_form_intraday(rows: list, form: list) -> None:
    """Append (or replace) the forming minute onto ascending 1-min rows."""
    if rows and rows[-1][0] == form[0]:
        rows[-1] = tuple(form)
    elif not rows or form[0] > rows[-1][0]:
        rows.append(tuple(form))


def _merge_form_daily(daily: list[list], form: list,
                      session: tuple[int, int] = NSE_SESSION) -> list[list]:
    """A copy of `daily` with the forming minute folded in — never mutates the
    cached list, which outlives any replay."""
    tz_off = session[1]
    out = list(daily)
    if out and _ist_day(out[-1][0], tz_off) == _ist_day(form[0], tz_off):
        ts, o, h, l, c, v = out[-1]
        out[-1] = [ts, o, max(h, form[2]), min(l, form[3]), form[4], v + form[5]]
    elif not out or form[0] > out[-1][0]:
        out.append([_ist_day(form[0], tz_off) * 86400 - tz_off,
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
                    with _WRITER_LOCK:
                        _live_writer.execute(
                            "INSERT OR REPLACE INTO bars VALUES (?,?,?,?,?,?,?)",
                            (sym, f[0], f[1], f[2], f[3], f[4], _exact_vol(f[5])))
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
        day = _ist_day(last, session_for(sym)[1])
    # the replayed window is the symbol's own day: asking for 2026-07-20 on a
    # 24/7 symbol used to hand back 19 Jul 18:30 -> 20 Jul 18:30 UTC (IST
    # midnight), which is a different session than the date names.
    day0 = day * 86400 - session_for(sym)[1]
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


# ── live venue drivers, IN THIS PROCESS ───────────────────────────
# _LIVE and the SSE subscriber lists are module state. A stream started as a
# separate CLI process therefore writes closed minutes to SQLite correctly and
# still never moves a chart: its forming bar lives in that process's memory, and
# the browser is subscribed to this one. Only a driver running here completes
# the path tick -> forming bar -> SSE -> chart, which is exactly why _replay_run
# is a thread rather than a script.
_DRIVERS: dict[str, object] = {}
_DRIVER_GUARD = threading.Lock()

# crypto_stream routes by symbol (venue_for: "-USD" -> coinbase, "USDT" ->
# bybit), so one class serves both venues and the venue name here only selects
# the module. Mixing families in one call is refused by the driver itself
# rather than silently opening two sockets.
_VENUES = {"coinbase": ("crypto_stream", "CryptoStream"),
           "bybit": ("crypto_stream", "CryptoStream"),
           "kite": ("kite_stream", "KiteStream")}


def _venue_symbols(venue: str) -> list[str]:
    """The instruments a venue owns that this store actually has history for.

    Taken from each venue's own source of truth rather than a second hardcoded
    copy, then intersected with `bars` — subscribing to a symbol with no local
    history writes today's minutes onto nothing and draws a chart that looks
    live and is one minute long.

    `kite` used to fall through this function to `[]`, which meant
    CHARTO_LIVE_VENUES could arm the two crypto venues and never the Indian
    one: `symbols=ALL` 400'd, so the NSE feed existed only because somebody
    curled /live by hand — and deploy.sh restarts this service on any backend
    change. The store's own 1-minute coverage is the honest list: everything
    `bars` holds that is not a crypto pair, which is exactly the set
    kite_stream's plan() is willing to stream. It still refuses per symbol
    there, so this being generous costs nothing.
    """
    have = _symbols_with_bars()
    if venue == "kite":
        return sorted(s for s in have if scope_for(s) != "crypto")
    try:
        import backfill_crypto as bc
        listed = {"coinbase": bc.COINBASE, "bybit": bc.BYBIT}.get(venue, [])
    except Exception:                                 # noqa: BLE001
        return []
    return [s for s, _ in listed if s in have]


def _live_stream(venue: str, symbols: list[str], stop: bool) -> tuple[int, dict]:
    venue = (venue or "").lower()
    with _DRIVER_GUARD:
        if stop:
            drv = _DRIVERS.pop(venue, None)
            if drv is None:
                return 404, {"error": f"no {venue} stream is running"}
            try:
                drv.stop()
            except Exception as exc:                  # noqa: BLE001
                return 500, {"error": f"{venue} stop failed: {exc}"}
            return 200, {"streaming": None, "venue": venue}
        if venue not in _VENUES:
            return 400, {"error": f"unknown venue {venue!r} — "
                                  f"want one of {', '.join(sorted(_VENUES))}"}
        if venue in _DRIVERS:
            return 409, {"error": f"{venue} is already streaming"}
        mod_name, cls_name = _VENUES[venue]
        if not symbols or symbols == ["ALL"]:
            # "every pair this venue owns" is the normal ask once the store is
            # current, and typing ten tickers by hand is how one gets dropped.
            symbols = _venue_symbols(venue)
            if not symbols:
                return 400, {"error": f"symbols is required — could not derive "
                                      f"the {venue} pair list"}
        try:
            mod = __import__(mod_name)
            cls = getattr(mod, cls_name)
        except Exception as exc:                      # noqa: BLE001
            # an honest boundary beats a 500: the adapter may simply not be
            # built yet, and the caller should be told which one is missing
            return 501, {"error": f"{venue} driver unavailable "
                                  f"({mod_name}.{cls_name}: {exc})"}
        try:
            drv = cls(symbols)
        except Exception as exc:                      # noqa: BLE001
            return 500, {"error": f"{venue} construct failed: {exc}"}
        _DRIVERS[venue] = drv
        # start() must NOT run in the request. CryptoStream.start() fills each
        # symbol's gap over REST first, and with a venue's full pair list that
        # is minutes of fetching — the /live call simply hung, and an HTTP
        # request that blocks on market data is a request that times out under
        # any real client. The whole lifecycle goes on the thread and the
        # caller polls /live?status=1, which is where refusals already live.
        threading.Thread(target=_driver_run, args=(venue, drv),
                         name=f"live-{venue}", daemon=True).start()
    # Proof the driver pushes into THIS module's _LIVE rather than a second
    # copy of it. False means closed bars still reach SQLite while /stream
    # delivers nothing — a failure that looks like a dead venue, so it is
    # reported rather than left to be rediscovered.
    shares = getattr(mod, "ds", None) is sys.modules[__name__]
    return 202, {"starting": venue, "symbols": symbols,
                 "shares_live_state": shares,
                 "note": "gap-fill then connect; poll /live?status=1"}


def _driver_run(venue: str, drv) -> None:
    """Own the driver's whole lifecycle off the request thread.

    The two adapters differ: KiteStream.start() spawns its own reader and has
    no run(); CryptoStream.start() only resolves symbols and returns a bool,
    with the socket loop in a blocking run(). Assuming one shape cost a silent
    no-op once already — connected=false, zero messages, no error anywhere,
    which reads exactly like a dead venue.
    """
    runner = getattr(drv, "run", None)
    try:
        ok = drv.start()
        if ok is False:
            logging.warning("charto live %s: every symbol refused", venue)
        elif callable(runner):
            runner()
    except Exception as exc:                          # noqa: BLE001
        logging.warning("charto live driver %s stopped: %s", venue, exc)
        ok = False
    finally:
        # Only a driver whose blocking run() returned has actually finished.
        # KiteStream has no run() — start() spawns its own reader and returns —
        # so unregistering on fall-through deleted a LIVE stream from the
        # registry: /live?status=1 reported {} while the socket kept ticking,
        # stop had nothing to stop, and a second start would have opened a
        # SECOND socket against the same API key.
        if callable(runner) or ok is False:
            with _DRIVER_GUARD:
                if _DRIVERS.get(venue) is drv:
                    _DRIVERS.pop(venue, None)


def _live_status() -> dict:
    with _DRIVER_GUARD:
        out = {}
        for venue, drv in _DRIVERS.items():
            try:
                out[venue] = drv.status()
            except Exception as exc:                  # noqa: BLE001
                out[venue] = {"error": str(exc)}
        return out


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
        bars = _resample_intraday(rows, mins, session_for(symbol))[-limit:]
        has_more = bool(rows) and _con.execute(
            "SELECT 1 FROM bars WHERE symbol=? AND ts<? LIMIT 1",
            (symbol, rows[0][0])).fetchone() is not None
    else:
        daily = _daily(symbol) if horizon is None else _fold_daily(_con.execute(
            "SELECT ts,o,h,l,c,v FROM bars WHERE symbol=? AND ts<? ORDER BY ts",
            (symbol, horizon)).fetchall(), session_for(symbol))
        if form is not None:
            daily = _merge_form_daily(daily, form, session_for(symbol))
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


# ── quotes ──────────────────────────────────────────────────────────────────
#
# What a watchlist row is, and nothing more: the last trade and the move since
# the previous session's close, for many symbols in one call. Read off the same
# daily series get_bars folds — including the forming minute — so a row and the
# candles beside it can never quote different numbers for the same session.
#
# It deliberately does NOT hydrate. /bars pulls a cold symbol out of the blob
# store (~6 s of subprocess) because the user asked to LOOK at it; a watchlist
# asks for thirty at once, on a timer, and firing thirty hydrations off a
# repaint would take the server down with it. A symbol this store holds nothing
# for answers `last: null`, which is the honest state and the one the panel
# already knows how to draw — never a filler number.

_QUOTES_MAX = 120        # a panel that scrolls, not a screener
_QUOTE_MIN_TAIL = 3000   # ≥2 sessions of minutes on every venue we carry


def _q(v: float) -> float:
    """Round for display without flattening a sub-rupee instrument to 0.0."""
    return round(v, 2) if abs(v) >= 1 else round(v, 6)


def _quote_daily(sym: str) -> list[list]:
    """This symbol's daily bars — on any shape of store.

    `_daily` is the fast path and the one the chart uses. It reads bars_1d,
    which a trimmed dev store does not carry at all; rather than fail the whole
    call, fold the TAIL of the minute table instead. Bounded on purpose — a
    quote needs two sessions, not the whole history.
    """
    try:
        daily = _daily(sym)
    except sqlite3.Error:
        daily = []
    if daily:
        return daily
    rows = _con.execute(
        "SELECT ts,o,h,l,c,v FROM bars WHERE symbol=? ORDER BY ts DESC LIMIT ?",
        (sym, _QUOTE_MIN_TAIL)).fetchall()
    rows.reverse()
    return _fold_daily(rows, session_for(sym))


def quote_for(sym: str) -> dict:
    """One row's worth of price. `last: null` when we hold nothing."""
    daily = _quote_daily(sym)
    live = _live_view(sym)
    form = live[0] if live else None
    if form is not None and daily:
        daily = _merge_form_daily(daily, form, session_for(sym))
    if not daily:
        return {"symbol": sym, "last": None,
                "note": f"no stored price history for {sym}"}
    last = daily[-1]
    # A single stored session has no previous close, so the day's move is
    # UNKNOWN rather than zero — the row shows a price and no change.
    prev = daily[-2][4] if len(daily) > 1 else None
    out = {
        "symbol": sym, "last": _q(last[4]), "open": _q(last[1]),
        "high": _q(last[2]), "low": _q(last[3]),
        "prev_close": _q(prev) if prev else None,
        "change": None, "change_pct": None,
        "as_of": last[0], "currency": quote_ccy(sym),
        # the asset class the store itself assigns (classification-backed), so
        # the panel groups its rows off one source instead of a second guess
        "scope": scope_for(sym),
    }
    if prev:
        out["change"] = _q(last[4] - prev)
        out["change_pct"] = round((last[4] - prev) / prev * 100, 2)
    return out


def quotes_for(names: list[str]) -> list[dict]:
    seen: set[str] = set()
    out = []
    for raw in names:
        s = raw.strip().upper()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(quote_for(s))
    return out


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _account_post(self, path: str, body: dict) -> tuple[int, dict]:
        """Accounts and saved work. Everything past /auth/* needs a session."""
        if path == "/auth/signup":
            return _auth_signup(body)
        if path == "/auth/login":
            return _auth_login(body)
        if path == "/auth/logout":
            raw = self.headers.get("Authorization") or ""
            if raw.startswith("Bearer "):
                with _users_lock:
                    _users.execute("DELETE FROM sessions WHERE token=?",
                                   (raw[7:].strip(),))
                    _users.commit()
            return 200, {"ok": True}          # logging out is never an error
        me = _auth_user(self.headers)
        if not me:
            return 401, {"error": "sign in to save your work"}
        if path == "/workspace":
            sym = str(body.get("symbol") or "").upper()
            if not sym:
                return 400, {"error": "symbol required"}
            return 200, {"saved": _ws_put(me[0], sym, body.get("state") or {})}
        if path == "/conversations":
            chats = body.get("chats")
            if not isinstance(chats, list):
                return 400, {"error": "chats[] required"}
            return 200, _conv_sync(me[0], chats)
        if path == "/layouts":
            uid = me[0]
            lid = int(body["id"]) if str(body.get("id") or "").isdigit() else None
            if body.get("delete"):
                with _users_lock:
                    if lid:
                        _users.execute("DELETE FROM layouts WHERE user_id=? AND id=?",
                                       (uid, lid))
                    else:       # the pre-id call site, kept working
                        _users.execute("DELETE FROM layouts WHERE user_id=? AND name=?",
                                       (uid, str(body.get("name") or "")))
                    _users.commit()
                return 200, {"ok": True}
            if body.get("copy"):
                if not lid:
                    return 400, {"error": "id required"}
                return _layout_copy(uid, lid)
            if "share" in body:
                if not lid:
                    return 400, {"error": "id required"}
                return _layout_share(uid, lid, bool(body.get("share")))
            if "thumb" in body and "spec" not in body:
                # Picture only. Layouts saved before thumbnails existed have
                # none, and the client backfills one the first time such a
                # layout is opened — so it must be impossible for that to
                # touch the saved workspace. No spec, no symbols, no name.
                if not lid:
                    return 400, {"error": "id required"}
                pic = _clean_thumb(body.get("thumb"))
                if not pic:
                    return 400, {"error": "not an inline image"}
                with _users_lock:
                    cur = _users.execute("UPDATE layouts SET thumb=? WHERE "
                                         "user_id=? AND id=?", (pic, uid, lid))
                    _users.commit()
                if not cur.rowcount:
                    return 404, {"error": "no such layout"}
                return 200, {"id": lid, "thumb": True}
            if "autosave" in body and "spec" not in body:
                # the toggle on its own — flipping it must not silently write
                # the current workspace over a layout the user is only arming
                if not lid:
                    return 400, {"error": "id required"}
                with _users_lock:
                    cur = _users.execute("UPDATE layouts SET autosave=? WHERE "
                                         "user_id=? AND id=?",
                                         (1 if body["autosave"] else 0, uid, lid))
                    _users.commit()
                # 0 rows means it is not theirs (or gone). Answering 200 said
                # "armed" for a layout that does not exist, and the toggle
                # would have sat on in a UI backed by nothing.
                if not cur.rowcount:
                    return 404, {"error": "no such layout"}
                return 200, {"id": lid, "autosave": bool(body["autosave"])}
            return _layout_save(
                uid, str(body.get("name") or ""), body.get("spec") or {},
                lid=lid, symbols=body.get("symbols") or [],
                autosave=body.get("autosave"),
                chat_id=str(body.get("chat_id") or ""),
                thumb=str(body.get("thumb") or ""))
        return 404, {"error": "not found"}

    def _send_events(self, events) -> None:
        """Pump an already-built event generator down an SSE response.

        The headers are the load-bearing part — see _send_stream — so both
        streams set them in one place rather than each remembering
        X-Accel-Buffering for itself.
        """
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("Connection", "close")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        try:
            for ev in events:
                self.wfile.write(f"data: {json.dumps(ev, default=str)}\n\n".encode())
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as exc:  # noqa: BLE001
            logging.warning("charto sse failed: %s", exc)

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

    def _send_alerts(self, uid: int) -> None:
        """SSE of this user's fired alerts. The same shape as _send_live and for
        the same reasons — one queue per subscriber, a slow reader dropped so
        its socket closes and the browser reconnects, and a 15s keepalive so a
        quiet market is not mistaken for a dead connection."""
        q = _alerts.subscribe(uid)
        try:
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
            logging.warning("charto alerts sse failed: %s", exc)
        finally:
            _alerts.unsubscribe(uid, q)

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
            if u.path.startswith("/api/"):
                # Pivot's stock page, copied verbatim into charto/web, calls
                # its own backend's routes — served here from charto's store.
                code, payload = api_route(u.path, q)
                return self._send(code, payload)
            if u.path == "/symbols":
                have = _symbols_with_bars()
                # names ride along so a reply that writes "Caplin Labs" can be
                # linked to its company page as readily as one that writes the
                # ticker — the model picks whichever reads better
                try:
                    names = dict(_con.execute(
                        "SELECT symbol, name FROM classification"))
                except sqlite3.Error:
                    names = {}
                logos = _logo_map()
                try:
                    # the Moneycontrol short name is wrong for a few rows
                    # (TITAN reads "IAG Company"); the enrichment long name is
                    # the one to SHOW, while the short one still has to match
                    # what a reply writes, so both are sent
                    longs = dict(_con.execute(
                        "SELECT symbol, long_name FROM company_profile "
                        "WHERE long_name IS NOT NULL"))
                except sqlite3.Error:
                    # only the long-name lookup is guarded here; _logo_map
                    # handles its own failures, and clearing it on this error
                    # would drop every mark because one name column was absent
                    longs = {}
                # Anything already in the store is searchable even when it is
                # absent from the NSE universe file: indices, India VIX, MCX
                # futures, INR pairs and crypto arrive via backfill_macro.py /
                # backfill_crypto.py rather than through hydration, so keying
                # the picker off the universe alone made them unreachable
                # except by typing ?symbol= into the URL.
                # A crypto listing is stored venue-qualified — "Bitcoin / USDT
                # (Bybit)" — so BTCUSDT and BTC-USD stay distinguishable in a
                # peer table. A reply writes "Bitcoin". The chat marks names by
                # exact spelling, so without the plain name every crypto row
                # went unmarked while all 500 companies carried a logo.
                # Both listings of an asset alias to the same word on purpose:
                # they are one asset and one mark, and the alias is only ever
                # used to choose a logo.
                alias = {sym: n.split(" / ")[0].strip()
                         for sym, n in names.items() if " / " in n}
                return self._send(200, {"symbols": sorted(set(_known_symbols()) | have),
                                        "hydrated": sorted(have),
                                        "names": names, "long": longs,
                                        "alias": alias, "logos": logos})
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
            if u.path == "/quotes":
                want = (q.get("symbols") or "").split(",")
                if len([s for s in want if s.strip()]) > _QUOTES_MAX:
                    return self._send(400, {
                        "error": f"at most {_QUOTES_MAX} symbols per call"})
                return self._send(200, {"quotes": quotes_for(want)})
            if u.path == "/indicators":
                # the catalogue the chart builds its menu from — one list, so
                # the menu and the model can never disagree about what exists
                return self._send(200, {"indicators": [
                    {"name": k, "period": v["period"], "pane": v["pane"],
                     "group": v["group"], "formula": v["formula"],
                     "lines": _INDICATOR_LINES.get(k, ["value"]),
                     # the settings dialog's Inputs tab is built from this, so
                     # the knobs on screen are exactly the knobs the math has
                     "inputs": indicators.inputs(k),
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
                # every knob the dialog can show, forwarded by the schema that
                # produced it — one list, so a new parameter on a function
                # reaches the chart without a second edit here
                for field in indicators.inputs(name):
                    k, t = field["key"], field["type"]
                    if k in ("period", "source") or q.get(k) in (None, ""):
                        continue
                    raw = q[k]
                    try:
                        if t == "bool":
                            extra[k] = raw.lower() in ("1", "true", "yes", "on")
                        elif t == "enum":
                            allowed = [o["value"] for o in field["options"]]
                            if raw not in allowed:
                                return self._send(400, {
                                    "error": f"bad {k} '{raw}'", "allowed": allowed})
                            extra[k] = raw
                        else:
                            extra[k] = float(raw) if t == "float" else int(raw)
                    except ValueError:
                        return self._send(400, {"error": f"bad {k} '{raw}'"})
                if q.get("anchor_index"):
                    extra["anchor_index"] = int(q["anchor_index"])
                try:
                    # empty source -> the indicator's own default column
                    res = indicators.compute(name, rows, int(q.get("period", 0)),
                                             q.get("source", ""), **extra)
                except ValueError as exc:
                    return self._send(400, {"error": str(exc)})
                # LEADING nulls are dropped; INTERIOR nulls become whitespace
                # points — {time} with no value — which is the series API's
                # actual mechanism for a gap.
                #
                # Dropping them all was wrong in a way that only shows on an
                # indicator whose line legitimately stops and restarts.
                # Supertrend is exactly that: it publishes supertrend_up on
                # up-bars and supertrend_down on down-bars, each None on the
                # other's bars. With those points simply absent, the series had
                # holes in TIME, and a line series joins its neighbours across a
                # hole — so every trend flip drew a long diagonal straight
                # through the candles, which is what made the indicator look
                # wrong. TradingView breaks the line there; so does this now.
                def _pts(series: list) -> list[dict]:
                    first = next((i for i, v in enumerate(series) if v is not None), None)
                    if first is None:
                        return []
                    return [{"time": rows[i][0]} if v is None
                            else {"time": rows[i][0], "value": round(v, 6)}
                            for i, v in enumerate(series[first:], start=first)]

                return self._send(200, {
                    "name": name, "spec": res["spec"],
                    "lines": {ln: _pts(series) for ln, series in res["lines"].items()},
                })
            if u.path == "/volume_profile":
                # The manual path into the same tool chat calls. It returns
                # the SCENE annotation alongside the numbers so the menu and
                # the model put an identical object on the chart — a second
                # renderer here is how the two would drift.
                err = _ensure_symbol(symbol)
                if err:
                    return self._send(404, err)
                _scene.items = []
                res = tool_volume_profile(
                    frm=q.get("frm", ""), to=q.get("to", ""),
                    lookback_sessions=int(q.get("lookback_sessions", 1) or 1),
                    rows=int(q.get("rows", 0) or 0),
                    value_area_pct=float(q.get("value_area_pct", 70) or 70),
                    split=q.get("split") in ("1", "true", "yes"),
                    draw=True, draw_mode="replace")
                patch = list(getattr(_scene, "items", []))
                _scene.items = []
                if "error" in res:
                    return self._send(400, res)
                res["scene"] = patch
                return self._send(200, res)
            if u.path == "/live":
                # start/stop a venue driver inside this process — see the
                # _DRIVERS comment for why a CLI process cannot move a chart
                if q.get("status") in ("1", "true", "yes"):
                    return self._send(200, {"streams": _live_status()})
                syms = [s.strip() for s in (q.get("symbols") or "").split(",")
                        if s.strip()]
                code, payload = _live_stream(
                    q.get("venue", ""), syms,
                    q.get("stop") in ("1", "true", "yes"))
                return self._send(code, payload)
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
            if u.path == "/company":
                if symbol not in _known_symbols():
                    return self._send(404, {
                        "error": f"{symbol} is not in the chart universe",
                        "hint": "GET /symbols lists it"})
                rng = q.get("range", "5Y")
                # a range button re-reads the series only — the profile half
                # never changes between clicks
                if q.get("only") == "history":
                    return self._send(200, company_history(symbol, rng))
                return self._send(200, company_page(symbol, rng))
            if u.path == "/shared":
                # No account: whoever holds the link. The only unauthenticated
                # read of user-created content in the server, which is why the
                # helper hands back the workspace and nothing else about the
                # person who made it.
                return self._send(*_layout_shared_get(q.get("token", "")))
            if u.path in ("/alerts", "/alerts/stream"):
                # Alerts are per-user by construction — they run on the server
                # so they can fire while the browser is shut, which means they
                # belong to an account and not to a tab.
                if _alerts is None:
                    return self._send(501, {"error": "the alert engine is not "
                                                     "loaded on this server"})
                me = _auth_user(self.headers)
                if not me:
                    return self._send(401, {"error": "sign in to use alerts"})
                if u.path == "/alerts/stream":
                    return self._send_alerts(me[0])
                return self._send(*_alerts.api_list(me[0]))
            if u.path == "/journal" or u.path.startswith("/journal/"):
                if _journal is None:
                    return self._send(501, {"error": "journal is unavailable"})
                me = _auth_user(self.headers)
                if not me:
                    return self._send(401, {"error": "sign in to use the journal"})
                tail = u.path[len("/journal"):].strip("/")
                if not tail or tail == "bootstrap":
                    return self._send(*_journal.api_bootstrap(me[0]))
                if tail.startswith("trades/") and tail.split("/")[-1].isdigit():
                    return self._send(*_journal.api_get(me[0], int(tail.split("/")[-1])))
                return self._send(404, {"error": f"no journal route '{tail}'"})
            if u.path in ("/auth/me", "/workspace", "/layouts"):
                me = _auth_user(self.headers)
                if not me:
                    # /auth/me answers "nobody" rather than failing: the FE asks
                    # it on boot to decide which UI to paint, and a 401 there is
                    # an answer, not a fault.
                    return self._send(200 if u.path == "/auth/me" else 401,
                                      {"user": None} if u.path == "/auth/me"
                                      else {"error": "sign in to load your work"})
                if u.path == "/auth/me":
                    return self._send(200, {"user": _user_public(me)})
                if u.path == "/layouts":
                    q = parse_qs(u.query)
                    lid = (q.get("id") or [""])[0]
                    if lid.isdigit():
                        return self._send(*_layout_get(me[0], int(lid)))
                    name = (q.get("name") or [""])[0]
                    if not name:
                        thumbs = (q.get("thumbs") or [""])[0] in ("1", "true")
                        return self._send(200, {
                            "layouts": _layouts_list(me[0], thumbs)})
                    with _users_lock:
                        row = _users.execute(
                            "SELECT name, spec, updated FROM layouts "
                            "WHERE user_id=? AND name=?", (me[0], name)).fetchone()
                    if not row:
                        return self._send(404, {"error": f"no layout named {name!r}"})
                    return self._send(200, {"name": row[0], "updated": row[2],
                                            "spec": json.loads(row[1])})
                sym = (parse_qs(u.query).get("symbol") or [""])[0].upper()
                if not sym:
                    return self._send(400, {"error": "symbol required"})
                return self._send(200, {"symbol": sym,
                                        "state": _ws_get(me[0], sym)})
            if u.path == "/meta":
                n, lo, hi = _con.execute(
                    "SELECT COUNT(*),MIN(ts),MAX(ts) FROM bars WHERE symbol=?",
                    (symbol,)).fetchone()
                # the LLM arm is reported by the SERVER, not assumed by the
                # caller — an A/B whose two arms cannot be told apart from
                # outside is an A/A nobody notices
                return self._send(200, {
                    "symbol": symbol, "count": n, "earliest": lo, "latest": hi,
                    "model": LLM_DEPLOYMENT, "effort": LLM_EFFORT})
            return self._send(404, {"error": "not found"})
        except Exception as exc:  # noqa: BLE001
            return self._send(500, {"error": str(exc)})

    def do_OPTIONS(self) -> None:  # noqa: N802 — CORS preflight for POST /chat
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers",
                         "Content-Type, Authorization")
        self.end_headers()

    def do_POST(self) -> None:  # noqa: N802
        u = urlparse(self.path)
        # Voice, before the JSON routes: the body is an audio blob, so it must
        # never reach a json.loads. Posted RAW rather than as multipart —
        # there is one file and no other field, and a hand-rolled multipart
        # parser on a public endpoint is a liability with nothing to buy.
        # Signed in, like every other thing kept per user: the transcript is
        # the user's words and the call costs money.
        if u.path == "/audio/transcribe":
            me = _auth_user(self.headers)
            if not me:
                return self._send(401, {"error": "sign in to use voice input"})
            try:
                ln = int(self.headers.get("Content-Length", 0))
            except (TypeError, ValueError):
                ln = 0
            if ln <= 0:
                return self._send(400, {"error": "empty recording"})
            if ln > _AUDIO_MAX_BYTES:
                return self._send(413, {"error": "recording too large"})
            out = transcribe(self.rfile.read(ln),
                             self.headers.get("Content-Type", ""))
            return self._send(400 if "error" in out else 200, out)
        if u.path == "/journal" or u.path.startswith("/journal/"):
            if _journal is None:
                return self._send(501, {"error": "journal is unavailable"})
            try:
                ln = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(ln) or b"{}")
            except (ValueError, TypeError):
                return self._send(400, {"error": "bad JSON body"})
            me = _auth_user(self.headers)
            if not me:
                return self._send(401, {"error": "sign in to use the journal"})
            tail = u.path[len("/journal"):].strip("/")
            if tail == "trades":
                return self._send(*_journal.api_create(me[0], body))
            if tail.startswith("trades/") and tail.split("/")[-1].isdigit():
                return self._send(*_journal.api_patch(me[0], int(tail.split("/")[-1]), body))
            if tail == "playbooks":
                return self._send(*_journal.api_playbook(me[0], body))
            if tail.startswith("playbooks/") and tail.split("/")[-1].isdigit():
                return self._send(*_journal.api_playbook(me[0], body, int(tail.split("/")[-1])))
            return self._send(404, {"error": f"no journal route '{tail}'"})
        if u.path == "/alerts" or u.path.startswith("/alerts/"):
            if _alerts is None:
                return self._send(501, {"error": "the alert engine is not "
                                                 "loaded on this server"})
            try:
                ln = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(ln) or b"{}")
            except (ValueError, TypeError):
                return self._send(400, {"error": "bad JSON body"})
            me = _auth_user(self.headers)
            if not me:
                return self._send(401, {"error": "sign in to use alerts"})
            tail = u.path[len("/alerts"):].strip("/")
            if not tail:
                return self._send(*_alerts.api_create(me[0], body))
            if tail == "check":
                return self._send(*_alerts.api_check(me[0], body))
            if tail == "seen":
                return self._send(*_alerts.api_seen(me[0]))
            if tail.isdigit():
                # patch, pause/resume, re-arm, or {delete:true} — the shape
                # /layouts already uses, so the server keeps two verbs
                return self._send(*_alerts.api_patch(me[0], int(tail), body))
            return self._send(404, {"error": f"no alerts route '{tail}'"})
        if u.path.startswith("/auth/") or u.path in ("/workspace", "/layouts",
                                                     "/conversations"):
            try:
                ln = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(ln) or b"{}")
            except (ValueError, TypeError):
                return self._send(400, {"error": "bad JSON body"})
            return self._send(*self._account_post(u.path, body))
        if u.path == "/suggest":
            # LEGACY. The follow-ups now ride /chat's own stream, after that
            # turn's `done` — see _suggest_events for why the route mattered
            # more than the hop. Kept because a browser holding a cached
            # index.html still posts here for a deploy or two, and answering
            # it costs nothing; nothing we ship calls it any more.
            try:
                ln = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(ln) or b"{}")
            except (ValueError, TypeError):
                return self._send(400, {"error": "bad JSON body"})
            msgs = body.get("messages")
            if not isinstance(msgs, list):
                return self._send(400, {"error": "messages[] required"})
            return self._send_events(_suggest_stream(msgs))
        if u.path != "/chat":
            return self._send(404, {"error": "not found"})
        try:
            n = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(n) or b"{}")
            messages = body.get("messages") or []
            if not isinstance(messages, list) or not messages:
                return self._send(400, {"error": "messages[] required"})
            ctx = body.get("context") or {}
            sym = str(ctx.get("symbol") or "RELIANCE").upper()
            _req.symbol = sym
            # Who is asking, and which conversation this is. Both exist for
            # recall_conversations and nothing else: the user scopes the
            # archive, and the chat_id is what EXCLUDES the conversation
            # already in context from a search of the earlier ones. A signed
            # out request sets user None and the tool says so honestly.
            _req.user = _auth_user(self.headers)
            _req.chat_id = str(body.get("chat_id") or "")[:64]
            err = _ensure_symbol(sym)
            if err:
                return self._send(400, err)
            # Every chart the user put in the conversation has to be loadable
            # before the turn starts — a tool aimed at one of them mid-round
            # cannot wait ~6 s for a cold hydrate. One that will not load is
            # dropped from the envelope rather than named to the model as
            # something it can read.
            charts, keep = [sym], []
            for c in (ctx.get("charts") or []):
                s = str((c or {}).get("symbol") or "").upper()
                if not s or s in charts:
                    keep.append(c)
                    continue
                if _ensure_symbol(s):
                    logging.warning("charto: chart %s unavailable, dropped", s)
                    continue
                charts.append(s)
                keep.append(c)
            ctx["charts"] = keep
            _req.charts = charts
            # a reference pane has no drawing layer; the envelope says which
            # kind of chart is in focus and the tools honour it
            _req.drawable = ctx.get("drawable") is not False
            # WHICH chart owns that drawing layer. Refusing a draw is only half
            # an answer — the other half is where ink can actually go, and
            # without this the model invented a route ("click that pane") that
            # cannot work. Older frontends don't send it; the focused chart is
            # then the best available answer and matches the old behaviour.
            _req.main_chart = str(ctx.get("main_chart") or sym or "").upper()
            if body.get("stream"):
                return self._send_stream(messages, ctx)
            return self._send(200, llm_chat(messages, ctx))
        except Exception as exc:  # noqa: BLE001
            return self._send(500, {"error": str(exc)})

    def log_message(self, fmt: str, *args) -> None:  # quiet
        pass


if __name__ == "__main__":
    # Serving this file as a script names it `__main__`. Every helper module
    # here does `import dataserver`, which would then load this file a SECOND
    # time as a separate module object with its own _LIVE, its own subscriber
    # lists and its own connections. Closed bars still reach the chart because
    # SQLite is shared, so the split is invisible in /bars — but the live path
    # dead-ends: measured 2026-08-02, the in-process Coinbase driver took 612
    # trades and closed 2 minutes while /stream delivered zero events and only
    # its 15s keepalive ping, because the driver was pushing forming bars into
    # one _LIVE and the SSE handler was reading another.
    #
    # Aliasing the name to this module makes `import dataserver` return the
    # running server, so a driver started through /live shares its state.
    sys.modules.setdefault("dataserver", sys.modules[__name__])

    # CHARTO_LIVE_VENUES="bybit,coinbase" arms those feeds at boot.
    #
    # Without this a stream only exists because somebody curled /live, and the
    # unit is Restart=always — so any crash, deploy or reboot silently returns
    # the box to a state where it serves charts and records NOTHING. Crypto
    # trades 24/7, so there is no daily open to notice it at; the hole is only
    # found later as missing bars. The venue drivers are already idempotent
    # (_live_stream refuses a second driver for a live venue) and each one
    # gap-fills before it connects, so a restart repairs the minutes it missed
    # while down instead of streaming on top of them.
    #
    # Deliberately opt-in and deliberately NOT defaulted to every venue: a
    # laptop running this file should not open sockets nobody asked for.
    for _v in (environ.get("CHARTO_LIVE_VENUES") or "").replace(" ", "").split(","):
        if not _v:
            continue
        try:
            _code, _body = _live_stream(_v, _venue_symbols(_v), stop=False)
            print(f"charto live autostart {_v}: {_code} {_body.get('note') or _body}")
        except Exception as _exc:                              # noqa: BLE001
            # Never let a venue keep the chart server down — data first.
            print(f"charto live autostart {_v} FAILED: {_exc}")

    # The watcher, after the alias and after the venues: catch_up() replays the
    # window this process was down for, and a feed that is already connecting
    # means fewer minutes for it to have to replay. Never fatal — the chart is
    # the product and it must come up even if the alert engine cannot.
    try:
        import alerts as _alerts_mod
        _alerts = _alerts_mod
        # before anything can be asked: the tools' schema carries the engine's
        # own operand list, so the model is never offered a grammar narrower
        # than the one the resolver speaks
        _teach_alert_grammar()
        _alerts_mod.register_hook()
        _boot = _alerts_mod.start()
        print(f"charto alerts: {_boot.get('armed', 0)} armed on "
              f"{_boot.get('symbols', 0)} symbol(s), "
              f"catch-up {_boot.get('catch_up')}")
    except Exception as _exc:                                  # noqa: BLE001
        print(f"charto alerts UNAVAILABLE: {_exc}")

    print(f"charto dataserver on :{PORT} (db={DB_PATH.name})")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
