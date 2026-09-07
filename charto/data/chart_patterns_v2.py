"""Point-in-time chart-pattern extensions for the historical V2 ledger.

This module is deliberately separate from :mod:`patterns`.  The shipped live
detector remains the product contract while these definitions are measured on
historical data.  A kind graduates into the live detector only after the
point-in-time sweep has disclosed its frequency, stability and false-positive
rate.

Every definition uses prices, ATR-derived tolerance and bar counts.  Nothing
depends on pixels, chart aspect ratio or a visually judged angle.
"""
from __future__ import annotations

from statistics import median

DETECTOR_VERSION = "chart-v2.1"

ADDITIONAL_CHART_KINDS = (
    "measured_move_up", "measured_move_down",
    "v_bottom", "v_top",
    "island_reversal_bottom", "island_reversal_top",
    "range_breakout_up", "range_breakout_down",
    "volatility_contraction_up", "volatility_contraction_down",
    "failed_breakout_up", "failed_breakout_down",
    "breakout_retest_up", "breakout_retest_down",
    "bump_and_run_bottom", "bump_and_run_top",
    "diamond_bottom", "diamond_top",
)


def _linfit(points: list[tuple[int, float]]) -> tuple[float, float, float] | None:
    """Least-squares slope/intercept/R² in bar-index space."""
    if len(points) < 2:
        return None
    xs = [float(x) for x, _ in points]
    ys = [float(y) for _, y in points]
    xm, ym = sum(xs) / len(xs), sum(ys) / len(ys)
    den = sum((x - xm) ** 2 for x in xs)
    if den <= 0:
        return None
    slope = sum((x - xm) * (y - ym) for x, y in zip(xs, ys)) / den
    intercept = ym - slope * xm
    ss_tot = sum((y - ym) ** 2 for y in ys)
    ss_res = sum((y - (slope * x + intercept)) ** 2 for x, y in points)
    r2 = 1 - ss_res / ss_tot if ss_tot else 0.0
    return slope, intercept, r2


def additional_chart_patterns(rows: list[tuple], pivots: list[tuple],
                              tol: float, ist, kinds: set | None = None,
                              limit: int = 10_000) -> list[dict]:
    """Return confirmed additional chart-pattern events in ``rows``.

    Unlike the live-edge families in ``patterns.py``, these detectors walk the
    supplied window chronologically and attach a real completion bar.  That
    makes them safe for historical mining: future returns always begin after
    ``completion_i`` and no later bar participates in the formation.
    """
    want = set(kinds or ADDITIONAL_CHART_KINDS)
    n = len(rows)
    if n < 20:
        return []
    closes = [float(r[4]) for r in rows]
    highs = [float(r[2]) for r in rows]
    lows = [float(r[3]) for r in rows]
    vols = [float(r[5] or 0) for r in rows]
    # Point-in-time volatility tolerance.  The caller's ``tol`` describes the
    # latest bar and is only a fallback for the warm-up.  Using it for an event
    # years earlier leaks future volatility into the detector thresholds.
    tr: list[float] = []
    for i, r in enumerate(rows):
        prev = closes[i - 1] if i else float(r[1])
        tr.append(max(float(r[2]) - float(r[3]), abs(float(r[2]) - prev),
                      abs(float(r[3]) - prev)))
    local_tol = []
    for i in range(n):
        vals = tr[max(0, i - 13):i + 1]
        local_tol.append(max(1e-12, sum(vals) / len(vals) * 0.5
                             if vals else tol))
    TOL = lambda i: local_tol[max(0, min(n - 1, int(i)))]  # noqa: E731
    seq = sorted((int(i), float(p), "H" if k == "resistance" else "L")
                 for i, p, k in pivots)
    out: list[dict] = []

    def emit(kind: str, direction: str, i0: int, i1: int, comp: int,
             points: dict, **features) -> None:
        if kind not in want or not (0 <= i0 <= i1 <= comp < n):
            return
        out.append({
            "pattern": kind, "family": "chart", "direction": direction,
            "from": ist(rows[i0][0]), "to": ist(rows[i1][0]),
            "confirmed_at": ist(rows[comp][0]), "completion_i": comp,
            "bars_ago": n - 1 - i1, "span_bars": i1 - i0,
            "status": "confirmed", "points": points,
            "detector_version": DETECTOR_VERSION, **features,
        })

    # Measured move: impulse AB, corrective BC, continuation through B.  The
    # correction must retrace 25-75% of AB; D is the first confirming close.
    for a in range(len(seq) - 2):
        A, B, C = seq[a:a + 3]
        etol = TOL(C[0])
        if A[2] == "L" and B[2] == "H" and C[2] == "L":
            impulse = B[1] - A[1]
            retrace = (B[1] - C[1]) / impulse if impulse > 0 else 9
            if impulse >= 3 * etol and 0.25 <= retrace <= 0.75:
                d = next((j for j in range(C[0] + 1, min(n, C[0] + 121))
                          if closes[j] > B[1] + TOL(j)), None)
                if d is not None:
                    emit("measured_move_up", "bullish", A[0], C[0], d,
                         {"a": A[1], "b": B[1], "c": C[1]},
                         breakout_level=round(B[1], 4),
                         retracement_pct=round(retrace * 100, 2),
                         measured_move=round(C[1] + impulse, 4))
        elif A[2] == "H" and B[2] == "L" and C[2] == "H":
            impulse = A[1] - B[1]
            retrace = (C[1] - B[1]) / impulse if impulse > 0 else 9
            if impulse >= 3 * etol and 0.25 <= retrace <= 0.75:
                d = next((j for j in range(C[0] + 1, min(n, C[0] + 121))
                          if closes[j] < B[1] - TOL(j)), None)
                if d is not None:
                    emit("measured_move_down", "bearish", A[0], C[0], d,
                         {"a": A[1], "b": B[1], "c": C[1]},
                         breakout_level=round(B[1], 4),
                         retracement_pct=round(retrace * 100, 2),
                         measured_move=round(C[1] - impulse, 4))

    # V turns: two steep, similarly sized legs around one extreme, confirmed
    # only after price recovers/relinquishes at least 70% of the incoming leg.
    for i, price, side in seq:
        etol = TOL(i)
        for span in (5, 8, 13, 21):
            if i - span < 0 or i + span >= n:
                continue
            left, right = closes[i - span], closes[i + span]
            if side == "L":
                down, up = left - price, right - price
                kind, direction = "v_bottom", "bullish"
            else:
                down, up = price - left, price - right
                kind, direction = "v_top", "bearish"
            if down >= 4 * etol and up >= 0.7 * down and 0.45 <= up / down <= 1.8:
                emit(kind, direction, i - span, i, i + span,
                     {"left": left, "turn": price, "right": right},
                     leg_bars=span, left_leg=round(down, 4),
                     right_leg=round(up, 4), symmetry=round(up / down, 3))
                break

    # Island reversals: an island of 1-5 bars isolated by opposing true gaps.
    for i in range(2, n - 2):
        for island_bars in range(1, 6):
            j = i + island_bars
            if j >= n:
                break
            etol = TOL(i)
            gap_up_in = lows[i] > highs[i - 1] + etol * 0.1
            gap_down_out = highs[j] < lows[j - 1] - TOL(j) * 0.1
            gap_down_in = highs[i] < lows[i - 1] - etol * 0.1
            gap_up_out = lows[j] > highs[j - 1] + TOL(j) * 0.1
            if gap_up_in and gap_down_out:
                emit("island_reversal_top", "bearish", i, j - 1, j,
                     {"island_high": max(highs[i:j]),
                      "island_low": min(lows[i:j])},
                     island_bars=island_bars,
                     entry_gap=round(lows[i] - highs[i - 1], 4),
                     exit_gap=round(lows[j - 1] - highs[j], 4))
            elif gap_down_in and gap_up_out:
                emit("island_reversal_bottom", "bullish", i, j - 1, j,
                     {"island_high": max(highs[i:j]),
                      "island_low": min(lows[i:j])},
                     island_bars=island_bars,
                     entry_gap=round(lows[i - 1] - highs[i], 4),
                     exit_gap=round(lows[j] - highs[j - 1], 4))

    # Range/Darvas breakouts and volatility contractions.  Each event is
    # detected from bars strictly before the breakout bar.
    for j in range(25, n):
        etol = TOL(j)
        for span in (10, 20, 40):
            if j < span:
                continue
            win_hi = max(highs[j - span:j])
            win_lo = min(lows[j - span:j])
            width = win_hi - win_lo
            if width <= 0:
                continue
            touches_hi = sum(1 for x in highs[j - span:j] if win_hi - x <= etol)
            touches_lo = sum(1 for x in lows[j - span:j] if x - win_lo <= etol)
            if touches_hi >= 2 and touches_lo >= 2 and width <= 12 * etol:
                if closes[j] > win_hi + etol:
                    emit("range_breakout_up", "bullish", j - span, j - 1, j,
                         {"upper": win_hi, "lower": win_lo},
                         breakout_level=round(win_hi, 4), range_bars=span,
                         range_width=round(width, 4),
                         touches_upper=touches_hi, touches_lower=touches_lo)
                elif closes[j] < win_lo - etol:
                    emit("range_breakout_down", "bearish", j - span, j - 1, j,
                         {"upper": win_hi, "lower": win_lo},
                         breakout_level=round(win_lo, 4), range_bars=span,
                         range_width=round(width, 4),
                         touches_upper=touches_hi, touches_lower=touches_lo)
            if span == 40:
                widths = []
                for a, b in ((j - 40, j - 20), (j - 20, j - 10), (j - 10, j)):
                    widths.append(max(highs[a:b]) - min(lows[a:b]))
                if widths[0] > widths[1] > widths[2] and widths[2] <= 0.65 * widths[0]:
                    if closes[j] > max(highs[j - 10:j]) + etol:
                        emit("volatility_contraction_up", "bullish", j - 40,
                             j - 1, j, {"upper": max(highs[j - 10:j]),
                                       "lower": min(lows[j - 10:j])},
                             contraction_widths=[round(x, 4) for x in widths],
                             contraction_ratio=round(widths[2] / widths[0], 3))
                    elif closes[j] < min(lows[j - 10:j]) - etol:
                        emit("volatility_contraction_down", "bearish", j - 40,
                             j - 1, j, {"upper": max(highs[j - 10:j]),
                                       "lower": min(lows[j - 10:j])},
                             contraction_widths=[round(x, 4) for x in widths],
                             contraction_ratio=round(widths[2] / widths[0], 3))

    # Failed breaks and breakout-retests use a prior 20-bar boundary.  A
    # failure closes back inside within five bars; a retest revisits the level
    # and then closes away in the breakout direction within ten.
    for b in range(20, n - 2):
        etol = TOL(b)
        upper = max(highs[b - 20:b]); lower = min(lows[b - 20:b])
        if closes[b] > upper + etol:
            fail = next((j for j in range(b + 1, min(n, b + 6))
                         if closes[j] < upper), None)
            if fail is not None:
                emit("failed_breakout_up", "bearish", b - 20, b, fail,
                     {"level": upper, "breakout": closes[b]},
                     breakout_level=round(upper, 4), failure_bars=fail - b)
            else:
                ret = next((j for j in range(b + 1, min(n, b + 21))
                            if lows[j] <= upper + TOL(j) and closes[j] >= upper), None)
                if ret is not None:
                    hold = next((j for j in range(ret, min(n, ret + 11))
                                 if closes[j] > upper + 2 * TOL(j)), None)
                    if hold is not None:
                        emit("breakout_retest_up", "bullish", b - 20, b, hold,
                             {"level": upper, "retest_low": lows[ret]},
                             breakout_level=round(upper, 4),
                             retest_bars=ret - b, hold_bars=hold - ret)
        elif closes[b] < lower - etol:
            fail = next((j for j in range(b + 1, min(n, b + 6))
                         if closes[j] > lower), None)
            if fail is not None:
                emit("failed_breakout_down", "bullish", b - 20, b, fail,
                     {"level": lower, "breakout": closes[b]},
                     breakout_level=round(lower, 4), failure_bars=fail - b)
            else:
                ret = next((j for j in range(b + 1, min(n, b + 21))
                            if highs[j] >= lower - TOL(j) and closes[j] <= lower), None)
                if ret is not None:
                    hold = next((j for j in range(ret, min(n, ret + 11))
                                 if closes[j] < lower - 2 * TOL(j)), None)
                    if hold is not None:
                        emit("breakout_retest_down", "bearish", b - 20, b, hold,
                             {"level": lower, "retest_high": highs[ret]},
                             breakout_level=round(lower, 4),
                             retest_bars=ret - b, hold_bars=hold - ret)

    # Bump-and-run: stable lead-in regression, >=2x prior residual excursion,
    # then a close through the lead-in trendline.  This replaces visual screen
    # angles with price-per-bar slope and residual/ATR ratios.
    for side, kind, direction in (("L", "bump_and_run_top", "bearish"),
                                  ("H", "bump_and_run_bottom", "bullish")):
        pts = [(i, p) for i, p, s in seq if s == side]
        for k in range(3, len(pts)):
            lead = pts[max(0, k - 5):k]
            fit = _linfit(lead)
            if not fit or fit[2] < 0.65:
                continue
            slope, intercept, r2 = fit
            bi, bp = pts[k]
            etol = TOL(bi)
            expected = slope * bi + intercept
            residuals = [abs(p - (slope * i + intercept)) for i, p in lead]
            base_dev = max(etol, median(residuals) if residuals else etol)
            bump = (bp - expected) if side == "L" else (expected - bp)
            # For a top, rising support lows and a downward break; for a
            # bottom, falling resistance highs and an upward break.
            proper_slope = slope > 0 if side == "L" else slope < 0
            if not proper_slope or bump < 2 * base_dev:
                continue
            if side == "L":
                comp = next((j for j in range(bi + 1, min(n, bi + 121))
                             if closes[j] < slope * j + intercept - TOL(j)), None)
            else:
                comp = next((j for j in range(bi + 1, min(n, bi + 121))
                             if closes[j] > slope * j + intercept + TOL(j)), None)
            if comp is not None:
                emit(kind, direction, lead[0][0], bi, comp,
                     {"lead_start": lead[0][1], "bump": bp},
                     lead_slope_per_bar=round(slope, 6), lead_r2=round(r2, 3),
                     bump_over_baseline=round(bump, 4),
                     bump_multiple=round(bump / base_dev, 3))

    # Diamonds: alternating seven-pivot sequence whose first half expands and
    # second half contracts.  Confirmation is a close beyond the last half's
    # boundary; the preceding 20-bar move names top versus bottom.
    for a in range(len(seq) - 6):
        w = seq[a:a + 7]
        etol = TOL(w[-1][0])
        if any(w[i][2] == w[i + 1][2] for i in range(6)):
            continue
        spans = [abs(w[i + 1][1] - w[i][1]) for i in range(6)]
        if not (spans[0] < spans[1] <= spans[2]
                and spans[3] >= spans[4] > spans[5]
                and max(spans) >= 3 * etol):
            continue
        i0, end = w[0][0], w[-1][0]
        pre = closes[max(0, i0 - 20)]
        mid = median([x[1] for x in w])
        top = closes[i0] > pre and max(x[1] for x in w) > mid
        upper = max(x[1] for x in w[-3:] if x[2] == "H")
        lower = min(x[1] for x in w[-3:] if x[2] == "L")
        if top:
            comp = next((j for j in range(end + 1, min(n, end + 61))
                         if closes[j] < lower - TOL(j)), None)
            if comp is not None:
                emit("diamond_top", "bearish", i0, end, comp,
                     {"upper": upper, "lower": lower},
                     expansion=[round(x, 4) for x in spans[:3]],
                     contraction=[round(x, 4) for x in spans[3:]])
        else:
            comp = next((j for j in range(end + 1, min(n, end + 61))
                         if closes[j] > upper + TOL(j)), None)
            if comp is not None:
                emit("diamond_bottom", "bullish", i0, end, comp,
                     {"upper": upper, "lower": lower},
                     expansion=[round(x, 4) for x in spans[:3]],
                     contraction=[round(x, 4) for x in spans[3:]])

    # Content de-duplication: keep the earliest completion among substantially
    # overlapping same-kind formations.  This is deterministic across worker
    # counts and window boundaries.
    out.sort(key=lambda p: (p["completion_i"], p["pattern"], p["span_bars"]))
    uniq: list[dict] = []
    for p in out:
        i0 = p["completion_i"] - p["span_bars"]
        duplicate = False
        for q in reversed(uniq):
            if q["completion_i"] < p["completion_i"] - max(80, p["span_bars"]):
                break
            if q["pattern"] != p["pattern"]:
                continue
            q0 = q["completion_i"] - q["span_bars"]
            overlap = max(0, min(p["completion_i"], q["completion_i"])
                          - max(i0, q0))
            if overlap >= 0.6 * max(1, min(p["span_bars"], q["span_bars"])):
                duplicate = True
                break
        if not duplicate:
            uniq.append(p)
    return uniq[:max(0, int(limit))]
