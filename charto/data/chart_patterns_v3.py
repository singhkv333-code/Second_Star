"""Event-driven chart-pattern discovery for the chart-v3 research pipeline.

Geometry is recomputed only when a five-bar swing becomes knowable. Between
swings, small candidate records are advanced one bar at a time until they
confirm or expire. This preserves native-bar breakout timing without running
every detector against a 600-bar window on every bar.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone

import dataserver as ds
import patterns as pt

DETECTOR_VERSION = "chart-v3.0-pilot"
PIVOT_WINDOW = 5
DISCOVERY_WINDOW = 600
MAX_CANDIDATE_BARS = 180
RAIL_KINDS = {"ascending_triangle", "descending_triangle",
              "symmetrical_triangle", "rising_wedge", "falling_wedge",
              "rectangle", "channel_up", "channel_down", "broadening"}


@dataclass
class Candidate:
    pattern: dict
    discovered_i: int
    formation_start_i: int
    formation_end_i: int
    expires_i: int
    signature: str


def _new_pivots(rows: list[tuple], current: int) -> list[tuple]:
    """Return the pivot that becomes observable at ``current``, if any."""
    i = current - PIVOT_WINDOW
    if i < PIVOT_WINDOW:
        return []
    lo, hi = i - PIVOT_WINDOW, i + PIVOT_WINDOW + 1
    highs = [rows[j][2] for j in range(lo, hi)]
    lows = [rows[j][3] for j in range(lo, hi)]
    out = []
    if rows[i][2] == max(highs): out.append((i, rows[i][2], "resistance"))
    if rows[i][3] == min(lows): out.append((i, rows[i][3], "support"))
    return out


def _signature(pattern: dict, rows: list[tuple], start: int, end: int) -> str:
    points = pattern.get("points") or {}
    stable = "|".join((pattern["pattern"], str(rows[start][0]), str(rows[end][0]),
                       repr(sorted(points.items()))))
    return hashlib.sha1(stable.encode()).hexdigest()[:20]


def _break_on_bar(rows: list[tuple], candidate: Candidate, j: int) -> str | None:
    p = candidate.pattern; points = p.get("points") or {}
    dt = j - candidate.formation_end_i
    upper = points.get("upper_now"); lower = points.get("lower_now")
    if upper is not None:
        upper += float(points.get("upper_slope_per_bar") or 0) * dt
    if lower is not None:
        lower += float(points.get("lower_slope_per_bar") or 0) * dt
    kind = p["pattern"]
    if kind in ("cup_and_handle", "rounding_bottom", "rounding_top"):
        level = points.get("right_rim")
        if level is not None and kind != "rounding_top" and rows[j][4] > level:
            return "up"
        if level is not None and kind == "rounding_top" and rows[j][4] < level:
            return "down"
        return None
    if upper is not None and rows[j][4] > upper and p.get("direction") != "bearish":
        return "up"
    if lower is not None and rows[j][4] < lower and p.get("direction") != "bullish":
        return "down"
    return None


def _discover_rails(rows: list[tuple], pivots: list[tuple], j: int,
                    tol: float, kinds: set[str]) -> list[tuple[dict, int]]:
    """Direct O(recent pivots) rail classification; no general detector pass."""
    out = []; n = j + 1; flat = tol / max(1, min(DISCOVERY_WINDOW, n)) * 4
    highs = [(i, p) for i, p, k in pivots if k == "resistance"]
    lows = [(i, p) for i, p, k in pivots if k == "support"]
    for span in (60, 90, 120):
        if n < span + 10: continue
        i0 = n - span
        hp = [(i, p) for i, p in highs if i >= i0]
        lp = [(i, p) for i, p in lows if i >= i0]
        if len(hp) < 2 or len(lp) < 2: continue
        fh, fl = pt._fit(hp), pt._fit(lp)
        if not fh or not fl: continue
        sh, sl = fh[0], fl[0]
        top = sh * j + fh[1]; bot = sl * j + fl[1]
        old_top = sh * i0 + fh[1]; old_bot = sl * i0 + fl[1]
        if top <= bot: continue
        ratio = (top - bot) / max(1e-9, old_top - old_bot)
        converging, widening = ratio < .85, ratio > 1.15
        parallel = .85 <= ratio <= 1.15
        kind = direction = None
        if converging and abs(sh) <= flat and sl > flat:
            kind, direction = "ascending_triangle", "bullish"
        elif converging and abs(sl) <= flat and sh < -flat:
            kind, direction = "descending_triangle", "bearish"
        elif converging and sh < -flat and sl > flat:
            kind, direction = "symmetrical_triangle", "neutral"
        elif converging and sh > flat and sl > flat:
            kind, direction = "rising_wedge", "bearish"
        elif converging and sh < -flat and sl < -flat:
            kind, direction = "falling_wedge", "bullish"
        elif parallel and abs(sh) <= flat and abs(sl) <= flat:
            kind, direction = "rectangle", "neutral"
        elif parallel and sh > flat and sl > flat:
            kind, direction = "channel_up", "bullish"
        elif parallel and sh < -flat and sl < -flat:
            kind, direction = "channel_down", "bearish"
        elif widening and sh > flat and sl < -flat:
            kind, direction = "broadening", "neutral"
        if kind in kinds:
            out.append(({"pattern": kind, "direction": direction,
                         "points": {"upper_now": round(top, 2),
                                    "lower_now": round(bot, 2),
                                    "upper_slope_per_bar": round(sh, 4),
                                    "lower_slope_per_bar": round(sl, 4)},
                         "span_bars": span - 1, "bars_ago": 0}, i0))
            break
    return out


def _discover_rounding(rows: list[tuple], closes: list[float], j: int, tol: float,
                       kinds: set[str]) -> list[tuple[dict, int]]:
    """Bounded direct quadratic fits for the three rounded edge families."""
    out = []
    def parabola(end, span):
        i0 = end - span + 1
        if i0 < 5: return None
        q = pt._quadfit(closes[i0:end + 1])
        if not q: return None
        a2, a1, a0, r2 = q; m = span
        if r2 < .75 or a2 == 0: return None
        vx = -a1 / (2 * a2)
        if not (.25 * (m - 1) <= vx <= .75 * (m - 1)): return None
        fit = lambda x: a2*x*x + a1*x + a0
        return i0, a2, r2, fit(0), fit(m-1), fit(vx)
    if "cup_and_handle" in kinds:
        for span in (40, 60, 90, 120):
            found = False
            for handle in (8, 12):
                e = j - handle; q = parabola(e, span)
                if not q or q[1] <= 0: continue
                i0, _, r2, left, right, turn = q
                depth = min(left, right) - turn
                hs = rows[e+1:j+1]
                if (depth >= tol*3 and abs(left-right) <= max(tol*2.5, .15*depth)
                        and all(r[4] < right+tol for r in hs[:-1])
                        and min(r[3] for r in hs) >= right-.5*depth):
                    out.append(({"pattern":"cup_and_handle","direction":"bullish",
                                 "points":{"right_rim":right},"span_bars":j-i0,
                                 "bars_ago":0,"r2":r2}, i0)); found=True; break
            if found: break
    for span in (40, 60, 90, 120):
        q = parabola(j, span)
        if not q: continue
        i0, a2, r2, left, right, turn = q
        if a2 > 0 and "rounding_bottom" in kinds and min(left,right)-turn >= tol*3:
            out.append(({"pattern":"rounding_bottom","direction":"bullish",
                         "points":{"right_rim":right},"span_bars":span-1,
                         "bars_ago":0,"r2":r2}, i0)); break
        if a2 < 0 and "rounding_top" in kinds and turn-max(left,right) >= tol*3:
            out.append(({"pattern":"rounding_top","direction":"bearish",
                         "points":{"right_rim":right},"span_bars":span-1,
                         "bars_ago":0,"r2":r2}, i0)); break
    return out


def event_driven_edge_patterns(rows: list[tuple], symbol: str,
                               kinds: set[str]) -> list[dict]:
    """Discover on pivot events and grade candidates on every native bar."""
    if len(rows) < 20: return []
    pivots: list[tuple] = []; active: dict[str, Candidate] = {}; emitted = []
    closes = [r[4] for r in rows]
    for j in range(len(rows)):
        # Native-bar confirmation is cheap: only active candidates are checked.
        for sig, candidate in list(active.items()):
            direction = _break_on_bar(rows, candidate, j)
            if direction:
                emitted.append({"pattern": candidate.pattern["pattern"],
                    "direction": candidate.pattern.get("direction", "neutral"),
                    "breakout_direction": direction,
                    "formation_start_i": candidate.formation_start_i,
                    "formation_end_i": candidate.formation_end_i,
                    "first_detectable_i": candidate.discovered_i,
                    "completion_i": j, "features": candidate.pattern,
                    "signature": sig})
                del active[sig]
            elif j > candidate.expires_i:
                del active[sig]
        fresh = _new_pivots(rows, j); pivots.extend(fresh)
        if pivots and pivots[0][0] < j - DISCOVERY_WINDOW:
            pivots = [p for p in pivots if p[0] >= j - DISCOVERY_WINDOW]
        tol = ds._tolerance(rows[max(0, j - 20):j + 1])
        found = _discover_rails(rows, pivots, j, tol, kinds)
        found += _discover_rounding(rows, closes, j, tol, kinds)
        for pattern, begin in found:
            end = j
            sig = _signature(pattern, rows, begin, end)
            if sig not in active:
                active[sig] = Candidate(pattern, j, begin, end,
                                        j + MAX_CANDIDATE_BARS, sig)
    # Content de-duplication mirrors V2: same-kind, substantially overlapping
    # formations contribute only their earliest native-bar confirmation.
    emitted.sort(key=lambda e: (e["completion_i"], e["pattern"]))
    kept = []
    for event in emitted:
        duplicate = False
        for prior in reversed(kept):
            if prior["completion_i"] < event["completion_i"] - 180: break
            if prior["pattern"] != event["pattern"]: continue
            overlap = max(0, min(prior["formation_end_i"], event["formation_end_i"])
                          - max(prior["formation_start_i"], event["formation_start_i"]))
            shorter = max(1, min(prior["formation_end_i"] - prior["formation_start_i"],
                                 event["formation_end_i"] - event["formation_start_i"]))
            if overlap >= 0.6 * shorter:
                duplicate = True; break
        if not duplicate: kept.append(event)
    return kept
