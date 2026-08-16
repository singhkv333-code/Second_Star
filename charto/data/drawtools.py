"""Charto — the ratio drawing catalogue, backend half.

The frontend owns the GEOMETRY. `preview/js/tools.js` builds every one of
these shapes from its anchors, and both layers that draw — the user's rail and
the chat's scene — run that one builder, so there is exactly one construction
of a Gann fan in this app and nothing here needs to reproduce it.

What the geometry cannot say back is the two things a REPLY needs:

  · how many anchors a tool takes, so `draw_shape` and `mark` can refuse a
    three-point tool given two points instead of drawing a broken figure and
    reporting success;
  · what its ratios resolve to in rupees and in dates, so the model quotes the
    ladder that is actually on the chart rather than recomputing one. A model
    that recomputes 61.8% is a model that can get it wrong; a model that reads
    it back cannot.

That makes this module a MIRROR of tools.js's ratio catalogue, and a mirror
drifts. `test_drawtools.py` reads tools.js and asserts every array here still
matches the one over there — a fib that says 61.8% in the reply and draws 60%
on the chart is the exact failure this file would otherwise introduce.

Pure and stdlib-only, like mark.py: handed points, returns numbers.
"""
from __future__ import annotations

# ── the ratio catalogue (mirrors js/tools.js) ───────────────────────────
RETRACEMENT = [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1]
EXTENSION = [0, 0.382, 0.5, 0.618, 1, 1.272, 1.618, 2.618, 4.236]
FAN = [0.236, 0.382, 0.5, 0.618, 0.786]
ARC = [0.236, 0.382, 0.5, 0.618, 0.786, 1]
TIME_ZONE = [0, 1, 2, 3, 5, 8, 13, 21, 34, 55]
TIME_RATIO = [0.618, 1, 1.618, 2.618, 4.236]
GANN = [0, 0.25, 0.382, 0.5, 0.618, 0.75, 1]
GANN_EIGHTHS = [0, 0.125, 0.25, 0.375, 0.5, 0.625, 0.75, 0.875, 1]
GANN_FAN = [[1, 8], [1, 4], [1, 3], [1, 2], [1, 1],
            [2, 1], [3, 1], [4, 1], [8, 1]]
GANN_SQUARE_BARS = 52


# ── the tools ───────────────────────────────────────────────────────────
# `reads` is what the tool's divisions are divisions OF, and it is the whole
# reason a report can be generated without knowing any geometry:
#   "price"  the ladder lands on price levels           → quote rupees
#   "time"   the divisions land on dates                → quote dates
#   "both"   a grid or a box divides price AND time     → quote both
#   "slope"  rays and spirals divide an ANGLE, and an angle has no level to
#            quote. Saying so is the honest report; inventing one is not.
TOOLS: dict[str, dict] = {
    "fib": {
        "anchors": 2, "label": "Fib retracement", "reads": "price",
        "ratios": RETRACEMENT,
        "how": "the leg's start is anchor 1 (100%), its end anchor 2 (0%)"},
    "fibExtension": {
        "anchors": 3, "label": "Trend-based fib extension", "reads": "price",
        "ratios": EXTENSION,
        "how": "anchors 1→2 are the measured leg; levels project from anchor 3"},
    "fibChannel": {
        "anchors": 3, "label": "Fib channel", "reads": "price",
        "ratios": RETRACEMENT,
        "how": "anchors 1→2 are the baseline, anchor 3 sets the 100% rail; "
               "the levels slope with the trend, so each one is a line and "
               "not a single price"},
    "fibTimeZone": {
        "anchors": 2, "label": "Fib time zone", "reads": "time",
        "ratios": TIME_ZONE,
        "how": "anchors 1→2 are ONE unit of time; verticals land on the "
               "fibonacci numbers of it"},
    "fibSpeedFan": {
        "anchors": 2, "label": "Fib speed resistance fan", "reads": "slope",
        "ratios": FAN,
        "how": "rays from anchor 1 cutting the box's far edge at each ratio "
               "of its height, and its bottom edge at each ratio of its width"},
    "fibTimeExtension": {
        "anchors": 3, "label": "Trend-based fib time", "reads": "time",
        "ratios": TIME_RATIO,
        "how": "anchors 1→2 are the measured duration; verticals count from "
               "anchor 3"},
    "fibCircles": {
        "anchors": 2, "label": "Fib circles", "reads": "slope",
        "ratios": ARC,
        "how": "rings about anchor 1, crossing the move at each ratio — the "
               "claim is distance from the pivot in price AND time at once"},
    "fibSpiral": {
        "anchors": 2, "label": "Fib spiral", "reads": "slope", "ratios": [],
        "how": "anchor 2 is the spiral's outer end; it winds inward to "
               "anchor 1, shrinking by φ every quarter turn"},
    "fibArcs": {
        "anchors": 2, "label": "Fib speed resistance arcs", "reads": "slope",
        "ratios": ARC,
        "how": "half-rings off anchor 1, crossing the trend line at each ratio"},
    "fibWedge": {
        "anchors": 3, "label": "Fib wedge", "reads": "slope", "ratios": ARC,
        "how": "anchor 1 is the apex; the rungs meet both rays at each ratio"},
    "pitchfan": {
        "anchors": 3, "label": "Pitchfan", "reads": "slope",
        "ratios": RETRACEMENT,
        "how": "anchor 1 is the handle; rays run through each ratio of the "
               "base between anchors 2 and 3"},
    "gannBox": {
        "anchors": 2, "label": "Gann box", "reads": "both", "ratios": GANN,
        "how": "the same fractions cut across price and across time"},
    "gannSquare": {
        "anchors": 2, "label": "Gann square", "reads": "both", "ratios": GANN,
        "how": "the box's grid, plus the fan of rational angles about anchor "
               "1 and the arcs carrying each fraction between the two axes"},
    "gannSquareFixed": {
        "anchors": 1, "label": "Gann square fixed", "reads": "both",
        "ratios": GANN_EIGHTHS,
        "how": f"one anchor: the square runs {GANN_SQUARE_BARS} bars forward "
               f"and is as tall as those bars actually ranged, cut into "
               f"eighths. Its second corner is derived on the chart from the "
               f"real bars, so there is no level table to quote here"},
    "gannFan": {
        "anchors": 2, "label": "Gann fan", "reads": "slope",
        "ratios": [],
        "how": "anchor 2 defines the 1×1 — one unit of price per unit of "
               "time; every other ray is that rate at a whole-number multiple"},
}


# Tool names are camelCase because the chart's catalogue is JavaScript and a
# second spelling on this side would be a second thing to keep in step. The
# callers here have always been forgiving about case — "GANNFAN" and
# "gann_fan" are the same request — so the forgiveness lives in one place
# rather than in each of them, and it resolves to the ONE canonical spelling
# the chart will recognise.
_ALIASES = {t.lower().replace("_", ""): t for t in TOOLS}


def canonical(name: str) -> str | None:
    """The catalogue's own spelling of `name`, or None if it is not ours."""
    return _ALIASES.get(str(name or "").strip().lower().replace("_", ""))


def anchors(tool: str) -> int | None:
    """How many points this tool takes, or None if it is not one of ours."""
    spec = TOOLS.get(tool)
    return spec["anchors"] if spec else None


# ── the time axis is a queue of BARS ────────────────────────────────────
# A chart's x-axis has one slot per bar: a weekend takes no width, and nor do
# the sixteen hours between one session's close and the next one's open. So a
# time HALF WAY along a span is half its bars, not half its seconds — and a
# reply that computes it in seconds quotes a date the chart never drew, some
# of them falling where no bar exists at all. `bars` is the sorted list of bar
# timestamps the caller already has; without it these fall back to wall clock,
# which is right only for a gapless series and is flagged as such by the
# caller passing nothing.


def _index_at(bars: list[int], t: int) -> float:
    if not bars:
        return float(t)
    n = len(bars) - 1
    if n == 0:
        return 0.0
    if t <= bars[0]:
        return (t - bars[0]) / max(1, bars[1] - bars[0])
    if t >= bars[n]:
        return n + (t - bars[n]) / max(1, bars[n] - bars[n - 1])
    lo, hi = 0, n
    while hi - lo > 1:
        m = (lo + hi) // 2
        if bars[m] <= t:
            lo = m
        else:
            hi = m
    return lo + (t - bars[lo]) / max(1, bars[hi] - bars[lo])


def _time_at(bars: list[int], i: float) -> int:
    if not bars:
        return int(i)
    n = len(bars) - 1
    if n == 0:
        return bars[0]
    if i <= 0:
        return int(round(bars[0] + i * max(1, bars[1] - bars[0])))
    if i >= n:
        return int(round(bars[n] + (i - n) * max(1, bars[n] - bars[n - 1])))
    lo = int(i)
    return int(round(bars[lo] + (i - lo) * (bars[lo + 1] - bars[lo])))


def _t_lerp(bars, t0: int, t1: int, r: float) -> int:
    """The time `r` of the way from t0 to t1, measured in bars."""
    if not bars:
        return int(round(t0 + (t1 - t0) * r))
    i0, i1 = _index_at(bars, t0), _index_at(bars, t1)
    return _time_at(bars, i0 + (i1 - i0) * r)


def _t_shift(bars, t: int, n: float) -> int:
    """`n` bars from `t`."""
    if not bars:
        return int(round(t + n))
    return _time_at(bars, _index_at(bars, t) + n)


def _bars_from(bars, t0: int, t1: int) -> float:
    if not bars:
        return float(t1 - t0)
    return _index_at(bars, t1) - _index_at(bars, t0)


def _price_ladder(v0: float, v1: float, ratios) -> list[dict]:
    """Geo.ladder, exactly: r=0 sits at the END of the leg, r=1 at its START.

    The convention is the app's, not this function's — the chart, the fib
    evaluator and the rail all read it this way, so flipping it here would
    rename every level in the reply while the drawing stayed put.
    """
    return [{"ratio": r, "price": round(v1 + (v0 - v1) * r, 2)} for r in ratios]


def levels(tool: str, pts: list[dict], fmt_time=None,
           bars: list[int] | None = None) -> dict | None:
    """What this tool's divisions resolved to, for the reply to quote.

    `pts` are the resolved anchors, [{t, v}], in the order they were given.
    `bars` is the sorted bar timestamps of the interval they were resolved
    on — pass it whenever you have them, because every DATE below is a
    fraction of a span measured in bars, not in seconds.
    Returns None for a tool with nothing quotable — which is a real answer and
    the one four of these tools have: a ray has a slope, not a level, and a
    fan that reported "levels" would be handing the model numbers to attribute
    to lines that do not sit at them.
    """
    spec = TOOLS.get(tool)
    if not spec or not pts:
        return None
    reads, ratios = spec["reads"], spec["ratios"]
    ts = lambda t: (fmt_time(t) if fmt_time else t)      # noqa: E731

    if tool == "fib":
        return {"levels": _price_ladder(pts[0]["v"], pts[1]["v"], ratios)}

    if tool == "fibExtension" and len(pts) >= 3:
        leg = pts[1]["v"] - pts[0]["v"]
        return {"levels": [{"ratio": r, "price": round(pts[2]["v"] + leg * r, 2)}
                           for r in ratios]}

    if tool == "fibChannel" and len(pts) >= 3:
        # Each level is a LINE, so a single price would be a fiction. What is
        # quotable is where each rail sits at the two ends of the baseline.
        # in BARS, and with Geo.valueAt's own zero-span fallback (the far
        # anchor's value). Falling back to the NEAR anchor instead flipped the
        # offset's sign, so the reply quoted the mirror image of the rails on
        # screen — a degenerate case, but a silently mirrored one.
        span = _bars_from(bars, pts[0]["t"], pts[1]["t"])
        at = (pts[0]["v"] + (pts[1]["v"] - pts[0]["v"])
              * (_bars_from(bars, pts[0]["t"], pts[2]["t"]) / span)
              if span else pts[1]["v"])
        off = pts[2]["v"] - at
        return {"rails": [{"ratio": r,
                           "from": round(pts[0]["v"] + off * r, 2),
                           "to": round(pts[1]["v"] + off * r, 2)}
                          for r in ratios],
                "_note": "each rail is a sloping line — quote it as a range "
                         "between its two ends, never as one price"}

    if tool == "fibTimeZone":
        u = _bars_from(bars, pts[0]["t"], pts[1]["t"])
        return {"unit_bars": round(u, 2),
                "dates": [{"n": n, "at": ts(_t_shift(bars, pts[0]["t"], u * n))}
                          for n in ratios]} if u else None

    if tool == "fibTimeExtension" and len(pts) >= 3:
        u = _bars_from(bars, pts[0]["t"], pts[1]["t"])
        return {"unit_bars": round(u, 2),
                "dates": [{"ratio": r, "at": ts(_t_shift(bars, pts[2]["t"], u * r))}
                          for r in ratios]} if u else None

    if tool == "gannSquareFixed":
        # Its second corner is derived on the chart from the real bars (see
        # tools.js), so nothing here knows where the square ends. Reporting a
        # level table would mean guessing at it.
        return {"squared_over_bars": GANN_SQUARE_BARS,
                "divisions": ratios,
                "_note": "the square's far corner is derived on the chart "
                         "from the loaded bars, so there is no level table "
                         "here — read the grid off the chart, or use gannBox "
                         "with two anchors when the user wants the numbers"}

    if reads == "both" and len(pts) >= 2:
        dv = pts[1]["v"] - pts[0]["v"]
        return {"price_levels": [{"ratio": r,
                                  "price": round(pts[0]["v"] + dv * r, 2)}
                                 for r in ratios],
                "time_levels": [
                    {"ratio": r,
                     "at": ts(_t_lerp(bars, pts[0]["t"], pts[1]["t"], r))}
                    for r in ratios]}

    return None


def report(tool: str, pts: list[dict], fmt_time=None,
           bars: list[int] | None = None) -> dict:
    """The whole of what a drawn ratio tool can honestly say about itself."""
    spec = TOOLS.get(tool)
    if not spec:
        return {"error": f"unknown tool '{tool}'", "available": sorted(TOOLS)}
    # `tool_label` and not `label`: a caller's report already carries the
    # user's own caption under `label`, and merging this in used to overwrite
    # it with the tool's generic name.
    out: dict = {"tool": tool, "tool_label": spec["label"],
                 "construction": spec["how"]}
    got = levels(tool, pts, fmt_time, bars)
    if got:
        out.update(got)
        out.setdefault(
            "_note",
            "These are the divisions the chart drew — quote them, do not "
            "recompute them. Placing a ratio tool says nothing about whether "
            "its ratios work on this symbol; call evaluate_fib for that, and "
            "say plainly that the rest of this family has no such record.")
    else:
        out["_note"] = (
            f"A {spec['label'].lower()} divides an angle, not a price axis, so "
            f"there is no level table to quote — describe where its rays or "
            f"rings fall against the bars instead, and never attribute a "
            f"number to one.")
    return out
