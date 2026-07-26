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

Run:  python3 charto/data/dataserver.py   (from repo root; port 5174)
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
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


def _scene_add(annotation: dict) -> None:
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
    graded = held + broke
    if graded >= 5:
        ev["hold_rate"] = round(held / graded * 100)
    else:
        ev["hold_rate"] = None
        ev["hold_rate_withheld"] = (
            f"{graded} graded re-test{'s' if graded != 1 else ''} is too few "
            f"for a percentage — say 'held {held} of {graded}' instead, even "
            f"if asked for one number")
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
    d = get_bars("RELIANCE", interval, to, limit)
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
        _scene_add({"kind": "clear_levels"})
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
        _scene_add({"kind": "clear_levels"})
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
        _scene_add({"kind": "clear", "scope": "segment"})
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
        _scene_add({"kind": "clear", "scope": "segment"})
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
        _scene_add({"kind": "clear", "scope": "segment"})
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
        _scene_add({"kind": "clear", "scope": "segment"})
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
                 "window_high", "window_low", "gap")


def tool_get_anchors(interval: str = "5m", lookback_bars: int = 300,
                     kinds: list | None = None, limit: int = 12,
                     _raw: bool = False) -> dict:
    """Referenceable points, each with the bars around it.

    This exists so a shape can be composed without anyone typing a
    coordinate: the model picks anchors by id and code supplies the numbers.
    Each anchor ships its NEIGHBOURHOOD — the bars either side — because a
    point without its surroundings can be selected but not interpreted, and
    interpretation is the model's actual job.
    """
    rows = _rows(interval, max(60, min(int(lookback_bars or 300), 1500)))
    if not rows:
        return {"error": f"no bars for interval {interval}"}
    wt = interval not in ("1d", "1w", "1mo")
    want = set(kinds or _ANCHOR_KINDS)
    n = len(rows)
    found: list[dict] = []

    if {"swing_high", "swing_low"} & want:
        for i, price, role in _pivots(rows, 5):
            kind = "swing_high" if role == "resistance" else "swing_low"
            if kind in want:
                found.append({"kind": kind, "i": i, "value": price})

    if "window_high" in want:
        i = max(range(n), key=lambda k: rows[k][2])
        found.append({"kind": "window_high", "i": i, "value": rows[i][2]})
    if "window_low" in want:
        i = min(range(n), key=lambda k: rows[k][3])
        found.append({"kind": "window_low", "i": i, "value": rows[i][3]})

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
        base = f"A{int(round(a['value']))}"
        seen[base] = seen.get(base, 0) + 1
        a["_id"] = base if seen[base] == 1 else f"{base}-{n - 1 - a['i']}"

    found = found[:max(1, min(int(limit or 12), 30))]

    out = []
    for a in found:
        i = a["i"]
        aid = a["_id"]
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
                    label: str = "", role: str = "neutral") -> dict:
    """Compose a shape from anchors resolved by id.

    The model chooses the shape and which anchors; every number still comes
    from the detector that produced the anchor. There is no field here that
    accepts a coordinate.
    """
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
        _scene_add({"kind": "clear", "scope": "zone"})
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
        _scene_add({"kind": "clear", "scope": "zone"})
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


def tool_evaluate_line(p1_time: str, p1_value: float, p2_time: str,
                       p2_value: float, interval: str = "5m",
                       lookback_bars: int = 500) -> dict:
    """Score a line the USER drew — the inverse of curate-by-reference.

    Coordinates are accepted here on purpose: they are the user's own
    geometry echoed back from the chart, not numbers the model invented. The
    same evidence rules as levels apply, including judging each touch only
    after its own pivot window so a local extremum cannot flatter the line.
    """
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


def tool_evaluate_fib(p1_time: str, p1_value: float, p2_time: str,
                      p2_value: float, interval: str = "1d",
                      lookback_bars: int = 600) -> dict:
    """Score a fib retracement: this drawing, and the ratios' own track record.

    Coordinates are accepted for the same reason evaluate_line accepts them —
    they are the user's geometry echoed back from the chart, not numbers the
    model invented.
    """
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


def tool_evaluate_drawing(kind: str, points: list, interval: str = "1d",
                          lookback_bars: int = 600) -> dict:
    """Score a zone, a channel or a planned position the USER drew.

    Coordinates are the user's own geometry echoed back from the chart, for
    the same reason evaluate_line accepts them: they were not invented here.
    """
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
        _scene_add({"kind": "clear", "scope": "segment"})
        _scene_add({"kind": "clear_levels"})
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
        _scene_add({"kind": "clear", "scope": "segment"})
    for p in picked:
        if p.get("neckline") is not None:
            _scene_add({
                "kind": "level", "id": p["id"], "price": p["neckline"],
                "lo": p["neckline"] - tol / 2, "hi": p["neckline"] + tol / 2,
                "pane": "price",
                "role": "support" if p["direction"] == "bearish" else "resistance",
                "strength": p.get("status", "unconfirmed"),
                "label": f"{p['pattern'].replace('_', ' ')} · neckline "
                         f"{p['neckline']:,.2f} · {p.get('status', '')}",
                "source": {"tool": "get_patterns",
                           "method": "swing-sequence template on shared ±5-bar pivots",
                           "interval": interval, "bars_scanned": len(rows),
                           "strength": p.get("status", "unconfirmed"),
                           "first_touch": p["from"], "last_touch": p["to"]},
            })
        elif p.get("points", {}).get("upper_now") is not None:
            _scene_add({
                "kind": "zone", "id": p["id"],
                "lo": p["points"]["lower_now"], "hi": p["points"]["upper_now"],
                "price": (p["points"]["lower_now"] + p["points"]["upper_now"]) / 2,
                "pane": "price", "role": "neutral", "strength": "user-directed",
                "label": f"{p['pattern'].replace('_', ' ')} · width "
                         f"{p.get('width_now', 0):,.2f}",
                "source": {"tool": "get_patterns", "method": "fitted swing boundaries",
                           "interval": interval, "bars_scanned": len(rows),
                           "first_touch": p["from"], "last_touch": p["to"]},
            })

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
                       series_points: int = 0, anchor_time: str = "") -> dict:
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
    rows = _rows(interval, max(200, min(int(lookback_bars or 400), 1500)))
    if not rows:
        return {"error": f"no bars for interval {interval}"}

    extra: dict = {}
    if mult:
        extra["mult"] = float(mult)
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
    # A tail of the series, when asked for — enough to see a cross or a turn
    # without shipping hundreds of numbers nobody reads.
    k = max(0, min(int(series_points or 0), 60))
    if k:
        idx = list(range(max(0, len(rows) - k), len(rows)))
        out["series"] = {
            "t": [_ist(rows[i][0], wt) for i in idx],
            **{ln: [None if v[i] is None else round(v[i], 4) for i in idx]
               for ln, v in res["lines"].items()},
        }
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
     "description": "Referenceable points on the chart — swing highs/lows, window extremes, session open/close, gaps — each returned with the bars around it so you can judge what the point means. Use this when the user asks for something drawn that no detector produces (a range, a box, a line between two moments): get anchors, then compose with draw_shape. You never type a coordinate.",
     "parameters": {"type": "object", "properties": {
         "interval": {"type": "string", "enum": ["1m", "5m", "15m", "30m", "1h", "1d", "1w"]},
         "lookback_bars": {"type": "integer", "description": "default 300"},
         "kinds": {"type": "array", "items": {"type": "string",
                   "enum": ["swing_high", "swing_low", "session_open", "session_close",
                            "window_high", "window_low", "gap"]},
                   "description": "omit for all kinds"},
         "limit": {"type": "integer", "description": "default 12, max 30"}},
         "required": ["interval"]}},
    {"type": "function", "name": "draw_shape",
     "description": "Draw a shape by referencing anchor ids from get_anchors. Shapes: segment, ray, box, band, hline, vline, point, polyline, fib. Use for anything the user asks to mark that isn't a detected level/trendline/divergence — a range between two swings, a box around a consolidation, a fib retracement across a leg, a line from one moment to another.",
     "parameters": {"type": "object", "properties": {
         "shape": {"type": "string", "enum": ["segment", "ray", "box", "band", "hline", "vline", "point", "polyline", "fib"],
                   "description": "'fib' draws a full retracement ladder across the leg between the two anchors — the FIRST anchor is the leg's start (100%), the second its end (0%)"},
         "anchor_ids": {"type": "array", "items": {"type": "string"},
                        "description": "ids from get_anchors, e.g. ['A1312','A1271']"},
         "interval": {"type": "string", "enum": ["1m", "5m", "15m", "30m", "1h", "1d", "1w"]},
         "lookback_bars": {"type": "integer", "description": "must match the get_anchors call"},
         "pane": {"type": "string", "description": "'price', or an indicator id like 'rsi'"},
         "label": {"type": "string", "description": "short caption drawn on the chart"},
         "role": {"type": "string", "enum": ["resistance", "support", "neutral"]}},
         "required": ["shape", "anchor_ids", "interval"]}},
    {"type": "function", "name": "evaluate_line",
     "description": "Score a line the USER drew: how many swings touched it, how many held vs broke, where it projects now. Use whenever the user asks whether their own trendline is any good, or what its record is. Pass the two endpoints exactly as they appear in the chart context's drawings list.",
     "parameters": {"type": "object", "properties": {
         "p1_time": {"type": "string", "description": "IST 'YYYY-MM-DD HH:MM' of the first endpoint"},
         "p1_value": {"type": "number"},
         "p2_time": {"type": "string"},
         "p2_value": {"type": "number"},
         "interval": {"type": "string", "enum": ["1m", "5m", "15m", "30m", "1h", "1d", "1w"]},
         "lookback_bars": {"type": "integer", "description": "default 500"}},
         "required": ["p1_time", "p1_value", "p2_time", "p2_value", "interval"]}},
    {"type": "function", "name": "evaluate_fib",
     "description": "Score a fibonacci retracement. Returns TWO things: where this drawing's levels sit and whether price has reached them since the leg, AND the base rate for each ratio across every past swing leg on this symbol — how often the 0.618 (or 0.5, or 0.382) actually turned price — measured against a non-fibonacci control so the rate can be read honestly. Use whenever a fib retracement comes up: the user drew one, asked whether fibs work here, or asked what a ratio means on this chart. Pass the leg's two endpoints from the chart context's drawings list.",
     "parameters": {"type": "object", "properties": {
         "p1_time": {"type": "string", "description": "IST time of the leg's START, as the chart shows it, e.g. '08 Jul 2026 15:25'"},
         "p1_value": {"type": "number", "description": "price at the start of the leg (the 100% end)"},
         "p2_time": {"type": "string", "description": "IST time of the leg's END"},
         "p2_value": {"type": "number", "description": "price at the end of the leg (the 0% end)"},
         "interval": {"type": "string", "enum": ["5m", "15m", "30m", "1h", "1d", "1w"]},
         "lookback_bars": {"type": "integer", "description": "bars to scan for the base rate, default 600"}},
         "required": ["p1_time", "p1_value", "p2_time", "p2_value", "interval"]}},
    {"type": "function", "name": "get_patterns",
     "description": "Detect named formations on the chart: 21 candlestick patterns (engulfing, hammer, doji, morning/evening star, three soldiers/crows, harami, piercing, tweezers, abandoned baby…), 11 chart patterns (head and shoulders and its inverse, double top/bottom, ascending/descending/symmetrical triangles, rising/falling wedges, bull/bear flags) and market structure (HH/HL/LH/LL with BOS and CHoCH). Call it BOTH ways: omit `kinds` to sweep everything for 'what patterns are on this chart', or set `kinds` to answer 'is there a head and shoulders / any bullish engulfing'. Always use this rather than reading candles out of get_bars and judging them yourself — the thresholds here are explicit and come back with the result. Set draw=true to mark chart patterns (necklines and boundaries).",
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
    {"type": "function", "name": "evaluate_drawing",
     "description": "Score a zone, channel or planned position the USER drew, against what price actually did. Use whenever the user asks whether their own box/band/channel/trade-setup is any good, has been respected, or has a record. A zone reports touches held vs broke PLUS how much of the time price closes inside it (a band price lives inside is the range, not a zone). A channel scores each edge separately plus containment. A position reports how often target came before stop from that entry, against the hit rate its risk:reward needs to break even. Do not answer these from raw bars — that is eyeballing, which is what this replaces.",
     "parameters": {"type": "object", "properties": {
         "kind": {"type": "string", "enum": ["zone", "channel", "position"]},
         "points": {"type": "array",
                    "description": "zone: the band's two edges (value only, time optional). channel: two points on one edge then one on the other, all with times. position: entry, then target, then stop (value only). Copy them from the chart context's drawings list.",
                    "items": {"type": "object", "properties": {
                        "t": {"type": "string", "description": "IST time as the chart shows it, e.g. '08 Jul 2026 15:25' — required for channel"},
                        "v": {"type": "number", "description": "price"}}}},
         "interval": {"type": "string", "enum": ["5m", "15m", "30m", "1h", "1d", "1w"]},
         "lookback_bars": {"type": "integer", "description": "default 600"}},
         "required": ["kind", "points", "interval"]}},
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
         "when asked whether volume confirms a move."),
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
         "series_points": {"type": "integer", "description": "return the last N points of the series too (max 60) — use it to see a cross or a turn, not just the current value"},
         "draw": {"type": "boolean", "description": "add it to the user's chart"}},
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
             "get_patterns": tool_get_patterns}


def run_tool(name: str, args: dict) -> dict:
    fn = _DISPATCH.get(name)
    if not fn:
        return {"error": f"unknown tool {name}"}
    try:
        return fn(**args)
    except Exception as exc:  # noqa: BLE001 — a bad call must not kill the turn
        logging.warning("charto tool %s failed: %s", name, exc)
        return {"error": f"{name} failed: {exc}"}


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
            parts.append(f"[{d['id']}] {d['type']}{on} {pts}{txt}{tag}")
        more = ctx.get("drawings_omitted")
        L.append("User's own drawings: " + " · ".join(parts)
                 + (f" · (+{more} more)" if more else ""))
        if any(d.get("on") for d in ctx["drawings"]):
            L.append("Drawings marked 'on <indicator>' sit in that indicator's "
                     "pane: their values are that indicator's units (an RSI "
                     "level, a MACD value), never rupees.")
    else:
        L.append("User's own drawings: none")

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
    block = "\n\n".join(x for x in (build_context_block(context), FORMAT_RULES) if x)
    wire: list[dict] = []
    if block:
        wire.append({"role": "system", "content": block})
    wire += [{"role": m.get("role", "user"), "content": str(m.get("content", ""))}
             for m in messages]

    _scene_reset()
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
    block = "\n\n".join(x for x in (build_context_block(context), FORMAT_RULES) if x)
    wire: list[dict] = []
    if block:
        wire.append({"role": "system", "content": block})
    wire += [{"role": m.get("role", "user"), "content": str(m.get("content", ""))}
             for m in messages]

    _scene_reset()
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


def _resample_intraday(rows: list[tuple], minutes: int) -> list[list]:
    """rows = ascending (ts,o,h,l,c,v) 1-min bars → bucketed bars.

    Buckets anchor to each session's minute-of-day relative to 09:15 IST so
    every trading day starts a fresh, aligned bucket (evening specials like
    Muhurat land in later buckets of the same day — still consistent).
    """
    out: list[list] = []
    cur_key = None
    for ts, o, h, l, c, v in rows:
        ist = ts + IST_OFF
        day = ist // 86400
        mod = (ist % 86400) // 60
        bucket = max(0, mod - SESSION_OPEN_MIN) // minutes
        key = (day, bucket)
        if key != cur_key:
            bucket_start_mod = SESSION_OPEN_MIN + bucket * minutes
            bts = day * 86400 + bucket_start_mod * 60 - IST_OFF
            out.append([bts, o, h, l, c, v])
            cur_key = key
        else:
            b = out[-1]
            b[2] = max(b[2], h)
            b[3] = min(b[3], l)
            b[4] = c
            b[5] += v
    return out


def _daily(symbol: str) -> list[list]:
    if symbol in _daily_cache:
        return _daily_cache[symbol]
    rows = _con.execute(
        "SELECT ts,o,h,l,c,v FROM bars WHERE symbol=? ORDER BY ts", (symbol,)
    ).fetchall()
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
    _daily_cache[symbol] = out
    return out


def _weekly_or_monthly(symbol: str, mode: str) -> list[list]:
    daily = _daily(symbol)
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


def get_bars(symbol: str, interval: str, to: int | None, limit: int) -> dict:
    if interval in INTRADAY_MIN:
        mins = INTRADAY_MIN[interval]
        raw_needed = limit * mins + 400  # slack for session boundaries
        if to:
            rows = _con.execute(
                "SELECT ts,o,h,l,c,v FROM bars WHERE symbol=? AND ts<? "
                "ORDER BY ts DESC LIMIT ?", (symbol, to, raw_needed)
            ).fetchall()
        else:
            rows = _con.execute(
                "SELECT ts,o,h,l,c,v FROM bars WHERE symbol=? "
                "ORDER BY ts DESC LIMIT ?", (symbol, raw_needed)
            ).fetchall()
        rows.reverse()
        bars = _resample_intraday(rows, mins)[-limit:]
        has_more = bool(rows) and _con.execute(
            "SELECT 1 FROM bars WHERE symbol=? AND ts<? LIMIT 1",
            (symbol, rows[0][0])).fetchone() is not None
    else:
        series = _daily(symbol) if interval == "1d" \
            else _weekly_or_monthly(symbol, interval)
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

    def do_GET(self) -> None:  # noqa: N802
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        symbol = q.get("symbol", "RELIANCE").upper()
        try:
            if u.path == "/bars":
                interval = q.get("interval", "5m")
                if interval not in (*INTRADAY_MIN, "1d", "1w", "1mo"):
                    return self._send(400, {"error": f"bad interval {interval}"})
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
