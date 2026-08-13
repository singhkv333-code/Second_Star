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


def event_driven_edge_patterns(rows: list[tuple], symbol: str,
                               kinds: set[str]) -> list[dict]:
    """Discover on pivot events and grade candidates on every native bar."""
    if len(rows) < 20: return []
    offset = ds.session_for(symbol)[1]
    ist = lambda ts: datetime.fromtimestamp(ts + offset, timezone.utc).isoformat()
    pivots: list[tuple] = []; active: dict[str, Candidate] = {}; emitted = []
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
        fresh = _new_pivots(rows, j)
        if not fresh: continue
        pivots.extend(fresh)
        start = max(0, j + 1 - DISCOVERY_WINDOW)
        window = rows[start:j + 1]
        local_pivots = [(i - start, p, k) for i, p, k in pivots if i >= start]
        found = pt.chart_patterns(window, local_pivots, ds._tolerance(window),
                                  ist, kinds, limit=10_000)
        for pattern in found:
            if pattern.get("status") == "confirmed":
                continue
            end = j - int(pattern.get("bars_ago", 0)); begin = max(
                start, end - int(pattern.get("span_bars", 0)))
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
