"""Charto — the universal marking layer: addresses in, coordinates out.

Every other drawing tool here starts from a DETECTOR. get_levels finds
levels, get_patterns finds patterns, get_anchors mints referenceable points
and draw_shape composes those. That is the right default — it is what keeps
a number on the chart traceable to code rather than to a sentence.

It also leaves a whole class of true, useful marks undrawable. Nothing
detects "the first hour of every session", "the last 15 minutes", "the day
the result came out", "1,300", "the stretch between these two dates". The
model knows where those go — from the conversation, from a tool result it
already has, from the clock — and had no way to say it. Building a detector
per idea is the wrong answer: it is a tool per sentence, forever.

This module is the missing half. The model writes an ADDRESS and code
resolves it against the real bars, so the general capability exists without
handing anyone a blank coordinate field.

    ADDRESS := "<time>" | "<price>" | "<time> @ <price>"

    time    08 Jul 2026 15:25 · 2026-07-08   an absolute moment
            09:15                            a time of DAY
            open · close                     the context's first/last bar
            first · last                     the window's edges
            -20                              20 bars back
            +10                              10 bars FORWARD, past the last
                                             bar, into blank chart
            +1h · +30m · +2d                 a DURATION from the address
                                             before it — "from open, to +1h"
    price   1300                             a literal, range-checked
            high · low · open · close        of the span (see below)
            mid                              midpoint of the span's range
            +2% · -1.5%                      relative to the span's close

What high/low/mid MEAN depends on the shape, and the split is the difference
between a region and a line:

  · a REGION (box) reads both corners off the bars BETWEEN its two times, so
        from "09:15 @ high"  to "10:15 @ low"
    is the opening range — the window's own extremes, not two unrelated bar
    extremes.
  · a LINE (segment, ray, poly) reads each point off its OWN bar, so
        from "-120 @ low"  to "-40 @ low"
    is a trendline through two swing lows. Sharing a span here would collapse
    it onto one number and draw flat.
  · a FULL-WIDTH shape (hline, band) reads the whole context, so an hline at
    "high" is the session's high rather than the last bar's.

A one-point shape spans that single bar, which is the other thing you would
expect.

`repeat: "session"` resolves the whole shape once per trading day in the
window. That one word is the difference between one box and a session map,
and it is why nothing here needs a session detector.

Two guards, both about honesty rather than plumbing:
  · a literal price far outside the loaded range is REFUSED, not drawn. A
    magnitude slip silently rescales the axis and buries the candles, and
    it is the single most likely way a typed number goes wrong.
  · every resolved coordinate is reported back, so the reply quotes what
    was actually drawn instead of what was asked for.

Pure and stdlib-only: it is handed rows and a couple of the server's own
time helpers, so there is one parser and one clock in the process rather
than a second copy living here.
"""
from __future__ import annotations

import re

# Caps. A mark call may not turn into a wall — the chart is the subject and
# the annotations are notes on it. `repeat` makes over-drawing easy to ask
# for by accident, so the ceiling lives here rather than in the prompt.
MAX_SHAPES = 60          # resolved shapes per call, after repeat expands
MAX_CONTEXTS = 30        # sessions/weeks one shape may repeat over

# shape name → the scene kind that renders it. Every one of these already
# has a renderer; this module adds no new geometry, it addresses what the
# chart could always draw.
KIND = {
    "hline": "level", "band": "zone", "vline": "vline", "vband": "vband",
    "segment": "segment", "ray": "segment", "box": "box", "poly": "poly",
    "dot": "point", "candle": "candle", "note": "label", "marker": "markers",
}

# Which axes a shape actually reads, which is what lets a bare address be
# unambiguous: "1300" on an hline is a price, "09:15" on a vline is a time,
# and a shape that needs both takes "<time> @ <price>".
#   "price" — value only, spans the plot width
#   "time"  — moment only, spans the plot height
#   "tv"    — a real point in the plane
AXIS = {
    "hline": "price", "band": "price",
    "vline": "time", "vband": "time", "candle": "time", "marker": "time",
    "segment": "tv", "ray": "tv", "box": "tv", "poly": "tv",
    "dot": "tv", "note": "tv",
}

# how many addresses each shape consumes
NEED = {"hline": 1, "band": 2, "vline": 1, "vband": 2, "segment": 2,
        "ray": 2, "box": 2, "poly": 3, "dot": 1, "candle": 1, "note": 1,
        "marker": 1}


class MarkError(ValueError):
    """A shape that could not be resolved. Carries the sentence the model
    should read — every one of these names what to do instead."""


# ── addresses ───────────────────────────────────────────────────────────

def split_address(s: str) -> tuple[str, str]:
    """'09:15 @ high' → ('09:15', 'high'). No '@' → the whole thing, and the
    caller decides which axis a bare address belongs to."""
    txt = str(s or "").strip()
    if "@" in txt:
        t, _, p = txt.rpartition("@")
        return t.strip(), p.strip()
    return txt, ""


def _at(rows: list, lo: int, hi: int, t: int) -> tuple:
    """A wall-clock moment → (bar index | None, that moment). None past the
    last bar; the timestamp is kept either way, because a shape may legitimately
    reach into blank chart."""
    if t > rows[hi][0]:
        return None, t
    i = next((j for j in range(hi, lo - 1, -1) if rows[j][0] <= t), lo)
    return i, rows[i][0]


def resolve_time(expr: str, rows: list, lo: int, hi: int, env: dict,
                 prefer: str = "start", ref: int | None = None) -> tuple:
    """→ (index | None, epoch seconds). None means the moment is past the
    last bar — a forward projection, which is legitimate geometry (a plan
    lives to the right of the candles) but has no bar to read a price from.

    `prefer` decides which end of a DATE-only address is meant. A range
    reads the way a calendar does: "1 Jun to 30 Jun" includes all of the
    30th, so the closing address of a two-point shape takes that day's last
    bar and the opening one takes its first.
    """
    e = (expr or "").strip().lower()
    if not e or e in ("last", "now", "end", "close"):
        return hi, rows[hi][0]
    if e in ("first", "start", "open"):
        return lo, rows[lo][0]

    # A DURATION from the previous address — "from open, to +1h". This is
    # what the model reaches for unprompted when asked for "the opening
    # hour", and without it the shape has to be re-expressed as two clock
    # times, which costs a whole round to discover. Bars (+10) and time
    # (+1h) are both here because a chart is read in both.
    m = re.fullmatch(r"([+-])\s*((?:\d+\s*(?:mins?|m|hrs?|h|d|w)\s*)+)", e)
    if m:
        # compound too ("+1h30m"): the model writes durations the way a
        # person says them, and refusing the compound form costs a whole
        # round to rediscover as two clock times
        unit = {"m": 60, "min": 60, "mins": 60, "h": 3600, "hr": 3600,
                "hrs": 3600, "d": 86400, "w": 604800}
        secs = sum(int(n) * unit[u]
                   for n, u in re.findall(r"(\d+)\s*(mins?|m|hrs?|h|d|w)",
                                          m.group(2)))
        off = secs * (1 if m.group(1) == "+" else -1)
        return _at(rows, lo, hi, (rows[hi][0] if ref is None else ref) + off)

    m = re.fullmatch(r"([+-])\s*(\d+)", e)
    if m:
        n = int(m.group(2))
        if m.group(1) == "-":
            i = max(lo, hi - n)
            return i, rows[i][0]
        # forward: step off the last bar's own spacing. It cannot know the
        # sessions it has no bars for, so it lands approximately — which is
        # all a projection into blank chart can ever be.
        step = rows[-1][0] - rows[-2][0] if len(rows) > 1 else 86400
        return None, rows[hi][0] + step * n

    m = re.fullmatch(r"(\d{1,2}):(\d{2})", e)
    if m:
        want = int(m.group(1)) * 60 + int(m.group(2))
        tz = env["tz_off"]
        for i in range(lo, hi + 1):
            if ((rows[i][0] + tz) % 86400) // 60 >= want:
                return i, rows[i][0]
        # a time after the session's last bar clamps to the close rather
        # than failing: "15:30" on a feed whose last bar is 15:29 means the
        # end of the day, and refusing that would be pedantry
        return hi, rows[hi][0]

    t = env["parse_time"](expr)
    if t is None:
        raise MarkError(
            f"could not read the time '{expr}' — use the chart's own format "
            f"(e.g. '08 Jul 2026 15:25' or '2026-07-08'), a time of day "
            f"('09:15'), or open/close/last/-20/+10")
    tz = env["tz_off"]
    if (t + tz) % 86400 == 0:
        # A DATE with no clock means that DAY. Resolving it the way a
        # timestamp resolves — the bar at or immediately before — puts "the
        # day the results came out" on the session before the results, and
        # an off-by-one session is not something anyone reads back off a
        # chart. Falling through when the day has no bars is deliberate: a
        # holiday then lands on the session before it, which is the honest
        # nearest answer.
        day = (t + tz) // 86400
        same = [j for j in range(lo, hi + 1) if (rows[j][0] + tz) // 86400 == day]
        if same:
            i = same[-1] if prefer == "end" else same[0]
            return i, rows[i][0]
    if t > rows[hi][0]:
        step = rows[-1][0] - rows[-2][0] if len(rows) > 1 else 86400
        return (hi, rows[hi][0]) if t - rows[hi][0] < step else (None, t)
    i = next((j for j in range(hi, lo - 1, -1) if rows[j][0] <= t), None)
    if i is None:
        raise MarkError(
            f"'{expr}' is before the loaded bars — nothing was drawn. Raise "
            f"lookback_bars to reach it.")
    return i, rows[i][0]


def resolve_price(expr: str, rows: list, s0: int, s1: int,
                  guard: tuple | None) -> float:
    """A value from the span rows[s0..s1], or a literal that survives the
    range check. `guard` is (lo, hi) and is None off the price pane, where
    the axis is an indicator's own scale and price bounds mean nothing."""
    e = (expr or "").strip().lower() or "close"
    s0, s1 = min(s0, s1), max(s0, s1)
    if e in ("high", "low", "mid", "middle"):
        hi = max(rows[i][2] for i in range(s0, s1 + 1))
        lo = min(rows[i][3] for i in range(s0, s1 + 1))
        return hi if e == "high" else lo if e == "low" else (hi + lo) / 2
    if e == "open":
        return rows[s0][1]
    if e == "close":
        return rows[s1][4]

    m = re.fullmatch(r"([+-])\s*([\d.]+)\s*%", e)
    if m:
        base = rows[s1][4]
        d = float(m.group(2)) / 100
        return base * (1 + d) if m.group(1) == "+" else base * (1 - d)

    try:
        v = float(e.replace(",", "").replace("₹", "").replace("$", ""))
    except ValueError:
        raise MarkError(
            f"could not read the price '{expr}' — use a number, or "
            f"high/low/open/close/mid, or a percentage like '+2%'") from None
    if guard and not (guard[0] <= v <= guard[1]):
        raise MarkError(
            f"{v:g} is a different order of magnitude from this chart, which "
            f"trades {guard[2]:g}–{guard[3]:g} — nothing was drawn, because a "
            f"level that far off rescales the axis and buries the candles. "
            f"Check the figure; a units slip (lakhs vs crores, paise vs "
            f"rupees) is the usual cause.")
    return v


# ── repeat contexts ─────────────────────────────────────────────────────

def contexts(rows: list, tz_off: int, repeat: str, count: int) -> list:
    """The (lo, hi) bar spans a shape resolves over — one, or one per day.

    Grouping is by LOCAL day, not by a fixed number of bars: sessions are
    unequal (a half-day, a stub expiry session) and an arithmetic split
    would put "the first hour" in the wrong place on exactly the days worth
    looking at.
    """
    n = len(rows)
    rep = (repeat or "none").lower()
    if rep not in ("session", "day", "week"):
        return [(0, n - 1)]
    if rep == "week":
        def key(ts):        # ISO-ish week bucket; the epoch began on a Thursday
            return ((ts + tz_off) // 86400 + 4) // 7
    else:
        def key(ts):
            return (ts + tz_off) // 86400

    out, cur, k0 = [], 0, key(rows[0][0])
    for i in range(1, n):
        k = key(rows[i][0])
        if k != k0:
            out.append((cur, i - 1))
            cur, k0 = i, k
    out.append((cur, n - 1))
    return out[-max(1, min(int(count or 5), MAX_CONTEXTS)):]


# ── building ────────────────────────────────────────────────────────────

def _point(addr: str, axis: str, rows: list, lo: int, hi: int, env: dict,
           guard, span: tuple | None = None, prefer: str = "start",
           ref: int | None = None) -> dict:
    """One address → {t, v, i} with only the fields its axis needs.

    A price-axis shape spans the whole context by construction: an hline at
    "high" means the session's high, not the last bar's, and a band from
    "low" to "high" is the range. Nobody asks for a full-width line at one
    candle's extreme — they ask for the level that candle set.
    """
    t_expr, p_expr = split_address(addr)
    if axis == "price" and not p_expr:
        t_expr, p_expr = "", t_expr        # a bare address here IS the price
    idx, ts = resolve_time(t_expr, rows, lo, hi, env, prefer, ref)
    out = {"t": ts, "i": idx}
    if axis != "time":
        here = idx if idx is not None else hi
        s0, s1 = (lo, hi) if axis == "price" else (span or (here, here))
        out["v"] = round(resolve_price(p_expr, rows, s0, s1, guard), 4)
    return out


def build(specs: list, rows: list, env: dict, pane: str = "price",
          prefix: str = "M") -> dict:
    """Resolve every spec into scene annotations.

    Returns {"items": [...], "report": [...], "errors": [...]}. A spec that
    cannot be resolved does not sink the call — its sentence goes in
    `errors` and the rest still draws, because a five-shape request losing
    one shape silently is worse than losing it loudly.
    """
    if not rows:
        return {"items": [], "report": [],
                "errors": ["no bars loaded for this interval"]}
    win_hi = max(r[2] for r in rows)
    win_lo = min(r[3] for r in rows)
    # Deliberately loose, and MULTIPLICATIVE. This guard is here to catch a
    # change of MAGNITUDE — the 10x and 10,000x unit slips — not to police
    # extrapolation. A line at 1,400 on a chart topping out at 1,316 is a
    # perfectly ordinary thing to want marked, and a tight band around the
    # loaded range would refuse it while still passing every plausible-
    # looking wrong number. A third to triple catches the former and waves
    # the latter through.
    guard = (max(0.01, win_lo / 3), win_hi * 3, win_lo, win_hi) \
        if str(pane or "price") == "price" else None

    items: list[dict] = []
    report: list[dict] = []
    errors: list[str] = []
    marks: list[dict] = []          # every `marker` folds into one annotation
    truncated = 0

    for gi, spec in enumerate(specs or []):
        if not isinstance(spec, dict):
            errors.append(f"shape {gi + 1} is not an object")
            continue
        shape = str(spec.get("shape") or "").lower().strip()
        if shape not in KIND:
            errors.append(f"unknown shape '{shape}' — have: "
                          + ", ".join(sorted(KIND)))
            continue
        axis, need = AXIS[shape], NEED[shape]

        addrs = spec.get("points") or []
        if not addrs:
            addrs = [a for a in (spec.get("at"), spec.get("from"),
                                 spec.get("to")) if a]
        addrs = [str(a) for a in addrs if str(a).strip()]
        if len(addrs) < need:
            errors.append(f"{shape} needs {need} address(es), got {len(addrs)}"
                          + (" — use points:[…] for a polyline"
                             if shape == "poly" else ""))
            continue

        label = str(spec.get("label") or "").strip()
        role = str(spec.get("role") or "neutral").lower()
        if role not in ("support", "resistance", "neutral"):
            role = "neutral"
        spans = contexts(rows, env["tz_off"], spec.get("repeat"),
                         spec.get("sessions", 5))

        for ci, (lo, hi) in enumerate(spans):
            if len(items) + len(marks) >= MAX_SHAPES:
                truncated += 1
                continue
            use = addrs[:max(need, len(addrs))]
            # only the CLOSING address of a range takes the end of its day
            pref = ["start"] * len(use)
            if len(use) > 1:
                pref[-1] = "end"
            def resolve_all(span=None, _u=use, _p=pref, _lo=lo, _hi=hi):
                # threaded left to right, because a duration ("+1h") is
                # measured from the address before it
                out, ref = [], None
                for a, pf in zip(_u, _p):
                    q = _point(a, axis, rows, _lo, _hi, env, guard, span, pf, ref)
                    ref = q["t"]
                    out.append(q)
                return out

            try:
                # A REGION shape reads both its corners off the same span, so
                # from "09:15 @ high" to "10:15 @ low" is the opening range.
                # A LINE shape must not: "from the low here to the low there"
                # is a trendline, and sharing the span collapses it to a flat
                # line through one number. Region asks about an interval, a
                # line asks about its endpoints.
                pts = resolve_all()
                if shape == "box" and len(pts) > 1:
                    i0 = pts[0]["i"] if pts[0]["i"] is not None else hi
                    i1 = pts[-1]["i"] if pts[-1]["i"] is not None else hi
                    pts = resolve_all(span=(min(i0, i1), max(i0, i1)))
            except MarkError as exc:
                errors.append(f"{shape}: {exc}")
                break        # the same address fails in every context

            aid = f"{prefix}{gi}" + (f"-{ci}" if len(spans) > 1 else "")
            base = {"id": aid, "pane": pane, "role": role,
                    "label": label, "owner": "mark",
                    "source": {"tool": "mark", "method": "resolved address",
                               "addresses": addrs,
                               "strength": "model-placed"}}
            ann = _annotate(shape, pts, base, rows, marks, label, role)
            if ann is None:
                continue
            items.append(ann)
            report.append({
                "id": aid, "shape": shape, "label": label,
                "at": [_describe(p, axis, env) for p in pts]})

    if marks:
        items.append({"id": f"{prefix}marks", "pane": pane, "kind": "markers",
                      "role": "neutral", "owner": "mark", "marks": marks,
                      "source": {"tool": "mark", "strength": "model-placed"}})
        report.append({"id": f"{prefix}marks", "shape": "marker",
                       "label": f"{len(marks)} bar marker(s)",
                       "at": [env["fmt_time"](m.pop("_at")) for m in marks]})
    if truncated:
        errors.append(
            f"{truncated} repeat(s) were dropped at the {MAX_SHAPES}-shape "
            f"ceiling — say so, and narrow `sessions` rather than re-calling.")
    return {"items": items, "report": report, "errors": errors}


def _annotate(shape, pts, base, rows, marks, label, role):
    """The scene annotation for one resolved shape."""
    p = pts
    if shape == "hline":
        return {**base, "kind": "level", "price": p[0]["v"],
                "label": label or f"{p[0]['v']:g}"}
    if shape == "band":
        return {**base, "kind": "zone",
                "lo": min(p[0]["v"], p[1]["v"]),
                "hi": max(p[0]["v"], p[1]["v"])}
    if shape == "vline":
        return {**base, "kind": "vline", "t": p[0]["t"]}
    if shape == "vband":
        if p[0]["t"] == p[1]["t"]:
            return None          # a zero-width strip is invisible, not a mark
        return {**base, "kind": "vband",
                "t1": min(p[0]["t"], p[1]["t"]),
                "t2": max(p[0]["t"], p[1]["t"])}
    if shape in ("segment", "ray"):
        return {**base, "kind": "segment", "dashed": True,
                "p1": {"t": p[0]["t"], "v": p[0]["v"]},
                "p2": {"t": p[1]["t"], "v": p[1]["v"]},
                "extend": "right" if shape == "ray" else "none"}
    if shape == "box":
        return {**base, "kind": "box",
                "a": {"t": p[0]["t"], "v": p[0]["v"]},
                "b": {"t": p[1]["t"], "v": p[1]["v"]}}
    if shape == "poly":
        return {**base, "kind": "poly",
                "pts": [{"t": q["t"], "v": q["v"]} for q in p]}
    if shape == "dot":
        return {**base, "kind": "point",
                "a": {"t": p[0]["t"], "v": p[0]["v"]}}
    if shape == "note":
        return {**base, "kind": "label", "text": label,
                "a": {"t": p[0]["t"], "v": p[0]["v"]}}
    if shape == "candle":
        i = p[0]["i"]
        hl = {"hi": rows[i][2], "lo": rows[i][3]} if i is not None else {}
        return {**base, "kind": "candle", "t1": p[0]["t"], "t2": p[0]["t"],
                **hl}
    if shape == "marker":
        marks.append({"t": p[0]["t"], "text": label or "",
                      "position": "belowBar" if role == "support" else "aboveBar",
                      "shape": "arrowUp" if role == "support"
                               else "arrowDown" if role == "resistance"
                               else "circle",
                      "_at": p[0]["t"]})
        return None
    return None


def _describe(p: dict, axis: str, env: dict) -> str:
    """What actually got drawn, in the chart's own vocabulary — this is the
    line the reply quotes, so it must be readable rather than raw."""
    t = env["fmt_time"](p["t"]) + ("" if p["i"] is not None else " (projected)")
    if axis == "time":
        return t
    if axis == "price":
        return f"{p['v']:g}"
    return f"{t} @ {p['v']:g}"
