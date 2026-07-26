"""Pattern detection for Charto — candlesticks, chart patterns, market structure.

Three deliberate choices run through this file.

**Thresholds are explicit and reported.** TA-Lib's CDL* functions hide their
"long body" and "doji" cutoffs inside an invisible rolling average, so a
caller cannot state what was measured. Everything here is defined against the
bar's own range or a disclosed rolling average of body size, and the numbers
that decided each hit travel back with it. A reply can therefore say what it
means by "engulfing" rather than asserting it.

**Detectors return measurements, never verdicts.** Nothing here decides that a
hammer is bullish enough to act on, or that a triangle "will" break upward. It
reports the geometry it found and the facts around it; deciding what that
means in context is the model's job, and it has the surrounding bars to do it
with. The one place trend is used is naming — a hammer and a hanging man are
the same shape and differ only by what preceded them — and that trend
measurement is itself reported.

**The swing definition is injected, not redefined.** `chart_patterns` and
`market_structure` take the caller's pivots, so a pattern, a trendline and a
level can never disagree about what a swing high is.
"""
from __future__ import annotations

# ── shared bar anatomy ────────────────────────────────────────────
_DOJI_BODY = 0.10       # body <= 10% of the bar's range
_LONG_BODY = 1.30       # body > 1.3x the rolling average body
_SMALL_BODY = 0.60      # body < 0.6x the rolling average body
_WICK_RATIO = 2.00      # hammer's lower wick >= 2x its body
_TREND_BARS = 10        # bars of context that name a hammer vs a hanging man

CANDLE_KINDS = (
    "doji", "dragonfly_doji", "gravestone_doji", "long_legged_doji",
    "hammer", "inverted_hammer", "hanging_man", "shooting_star",
    "marubozu", "spinning_top", "bullish_belt_hold", "bearish_belt_hold",
    "bullish_engulfing", "bearish_engulfing",
    "bullish_harami", "bearish_harami", "piercing_line", "dark_cloud_cover",
    "tweezer_top", "tweezer_bottom", "bullish_kicker", "bearish_kicker",
    "morning_star", "evening_star",
    "three_white_soldiers", "three_black_crows", "bullish_abandoned_baby",
    "bearish_abandoned_baby", "three_inside_up", "three_inside_down",
    "three_outside_up", "three_outside_down",
    "rising_three_methods", "falling_three_methods",
)
CHART_KINDS = (
    "double_top", "double_bottom", "triple_top", "triple_bottom",
    "head_and_shoulders", "inverse_head_and_shoulders",
    "ascending_triangle", "descending_triangle",
    "symmetrical_triangle", "rising_wedge", "falling_wedge",
    "rectangle", "channel_up", "channel_down", "broadening",
    "bull_flag", "bear_flag", "bull_pennant", "bear_pennant",
    "cup_and_handle", "rounding_bottom", "rounding_top",
)
STRUCTURE_KINDS = ("market_structure",)
ALL_KINDS = CANDLE_KINDS + CHART_KINDS + STRUCTURE_KINDS


def _anatomy(r: tuple) -> dict:
    _t, o, h, l, c, _v = r
    rng = h - l
    body = abs(c - o)
    return {"o": o, "h": h, "l": l, "c": c, "rng": rng, "body": body,
            "up": c > o, "top": max(o, c), "bot": min(o, c),
            "upper": h - max(o, c), "lower": min(o, c) - l,
            "body_pct": (body / rng) if rng else 0.0}


def _rolling_body(rows: list[tuple], i: int, n: int = 14) -> float:
    lo = max(0, i - n)
    bodies = [abs(r[4] - r[1]) for r in rows[lo:i]]
    return (sum(bodies) / len(bodies)) if bodies else 0.0


def _trend_before(rows: list[tuple], i: int, n: int = _TREND_BARS,
                  atr: float = 0.0) -> str:
    """Direction of the run INTO this bar — the context that names the shape.

    Measured as the net close-to-close move over the preceding n bars against
    ATR, so "down" means a real move rather than a rounding error.
    """
    lo = max(0, i - n)
    if i - lo < 3:
        return "unknown"
    move = rows[i - 1][4] - rows[lo][4]
    if atr and abs(move) < atr:
        return "flat"
    return "up" if move > 0 else "down"


# ── candlesticks ──────────────────────────────────────────────────
def candlesticks(rows: list[tuple], atr_series: list, ist,
                 kinds: set | None = None, limit: int = 40) -> list[dict]:
    """Every named candle formation in `rows`, newest last.

    One bar can carry more than one name (a doji is often also a spinning
    top); all of them are reported rather than silently picking a winner,
    because which one matters depends on the question being asked.
    """
    want = kinds or set(CANDLE_KINDS)
    out: list[dict] = []
    n = len(rows)

    def add(i: int, name: str, direction: str, bars: int, **facts):
        if name not in want:
            return
        atr = atr_series[i] if i < len(atr_series) and atr_series[i] else 0.0
        out.append({
            "pattern": name, "direction": direction, "family": "candlestick",
            "t": ist(rows[i][0]), "bars_ago": n - 1 - i, "bars": bars,
            "at": round(rows[i][4], 2),
            "prior_trend": _trend_before(rows, i - bars + 1, atr=atr),
            "measured": {k: round(v, 3) if isinstance(v, float) else v
                         for k, v in facts.items()},
        })

    for i in range(n):
        a = _anatomy(rows[i])
        if not a["rng"]:
            continue
        avg = _rolling_body(rows, i)

        # ── single bar ────────────────────────────────────
        if a["body_pct"] <= _DOJI_BODY:
            add(i, "doji", "neutral", 1, body_pct_of_range=a["body_pct"] * 100)
            # the wick distribution names the doji; the varieties are
            # mutually exclusive so one bar never carries two of them
            if a["lower"] >= 0.6 * a["rng"] and a["upper"] <= 0.1 * a["rng"]:
                add(i, "dragonfly_doji", "bullish", 1,
                    lower_wick_pct_of_range=a["lower"] / a["rng"] * 100)
            elif a["upper"] >= 0.6 * a["rng"] and a["lower"] <= 0.1 * a["rng"]:
                add(i, "gravestone_doji", "bearish", 1,
                    upper_wick_pct_of_range=a["upper"] / a["rng"] * 100)
            elif a["upper"] >= 0.3 * a["rng"] and a["lower"] >= 0.3 * a["rng"]:
                add(i, "long_legged_doji", "neutral", 1,
                    upper_wick_pct_of_range=a["upper"] / a["rng"] * 100,
                    lower_wick_pct_of_range=a["lower"] / a["rng"] * 100)
        if (a["body"] and a["lower"] >= _WICK_RATIO * a["body"]
                and a["upper"] <= a["body"] and a["body_pct"] < 0.5):
            trend = _trend_before(rows, i, atr=atr_series[i] if i < len(atr_series) and atr_series[i] else 0.0)
            # identical geometry; the preceding run is the entire difference
            if trend == "down":
                add(i, "hammer", "bullish", 1, lower_wick_over_body=a["lower"] / a["body"])
            elif trend == "up":
                add(i, "hanging_man", "bearish", 1, lower_wick_over_body=a["lower"] / a["body"])
        if (a["body"] and a["upper"] >= _WICK_RATIO * a["body"]
                and a["lower"] <= a["body"] and a["body_pct"] < 0.5):
            trend = _trend_before(rows, i, atr=atr_series[i] if i < len(atr_series) and atr_series[i] else 0.0)
            if trend == "up":
                add(i, "shooting_star", "bearish", 1, upper_wick_over_body=a["upper"] / a["body"])
            elif trend == "down":
                add(i, "inverted_hammer", "bullish", 1, upper_wick_over_body=a["upper"] / a["body"])
        if a["body_pct"] >= 0.95:
            add(i, "marubozu", "bullish" if a["up"] else "bearish", 1,
                body_pct_of_range=a["body_pct"] * 100)
        # belt hold: a long bar that opens AT its extreme against the prior
        # run — the open is the low (bullish) or the high (bearish)
        if avg and a["body"] > _LONG_BODY * avg and a["body_pct"] < 0.95:
            trend = _trend_before(rows, i, atr=atr_series[i] if i < len(atr_series) and atr_series[i] else 0.0)
            if a["up"] and a["lower"] <= 0.05 * a["rng"] and trend == "down":
                add(i, "bullish_belt_hold", "bullish", 1,
                    open_off_low_pct_of_range=a["lower"] / a["rng"] * 100,
                    body_vs_avg=a["body"] / avg)
            if not a["up"] and a["upper"] <= 0.05 * a["rng"] and trend == "up":
                add(i, "bearish_belt_hold", "bearish", 1,
                    open_off_high_pct_of_range=a["upper"] / a["rng"] * 100,
                    body_vs_avg=a["body"] / avg)
        if (_DOJI_BODY < a["body_pct"] <= 0.35 and a["upper"] > a["body"]
                and a["lower"] > a["body"]):
            add(i, "spinning_top", "neutral", 1, body_pct_of_range=a["body_pct"] * 100)

        # ── two bars ──────────────────────────────────────
        if i >= 1:
            p = _anatomy(rows[i - 1])
            if p["rng"] and a["body"] and p["body"]:
                engulfs = a["top"] >= p["top"] and a["bot"] <= p["bot"]
                if engulfs and a["up"] and not p["up"]:
                    add(i, "bullish_engulfing", "bullish", 2,
                        body_ratio=a["body"] / p["body"], body_vs_avg=a["body"] / avg if avg else 0)
                if engulfs and not a["up"] and p["up"]:
                    add(i, "bearish_engulfing", "bearish", 2,
                        body_ratio=a["body"] / p["body"], body_vs_avg=a["body"] / avg if avg else 0)
                inside = a["top"] <= p["top"] and a["bot"] >= p["bot"]
                if inside and avg and p["body"] > _LONG_BODY * avg:
                    if not p["up"] and a["up"]:
                        add(i, "bullish_harami", "bullish", 2, body_ratio=a["body"] / p["body"])
                    if p["up"] and not a["up"]:
                        add(i, "bearish_harami", "bearish", 2, body_ratio=a["body"] / p["body"])
                # piercing / dark cloud need a real close INTO the prior body
                mid = (p["o"] + p["c"]) / 2
                if (not p["up"] and a["up"] and a["o"] < p["l"]
                        and mid < a["c"] < p["o"]):
                    add(i, "piercing_line", "bullish", 2,
                        penetration_pct=(a["c"] - p["c"]) / p["body"] * 100 if p["body"] else 0)
                if (p["up"] and not a["up"] and a["o"] > p["h"]
                        and p["o"] < a["c"] < mid):
                    add(i, "dark_cloud_cover", "bearish", 2,
                        penetration_pct=(p["c"] - a["c"]) / p["body"] * 100 if p["body"] else 0)
                tol = 0.1 * max(a["rng"], p["rng"])
                if abs(a["h"] - p["h"]) <= tol and p["up"] and not a["up"]:
                    add(i, "tweezer_top", "bearish", 2, high_gap=abs(a["h"] - p["h"]))
                if abs(a["l"] - p["l"]) <= tol and not p["up"] and a["up"]:
                    add(i, "tweezer_bottom", "bullish", 2, high_gap=abs(a["l"] - p["l"]))
                # kicker: the market re-opens on the FAR side of the prior
                # open and runs — bodies cannot overlap, which also makes it
                # disjoint from an engulfing by construction
                if avg and p["body"] > avg and a["body"] > avg:
                    if not p["up"] and a["up"] and a["o"] >= p["o"]:
                        add(i, "bullish_kicker", "bullish", 2,
                            gap_over_prior_open=a["o"] - p["o"],
                            body_vs_avg=a["body"] / avg)
                    if p["up"] and not a["up"] and a["o"] <= p["o"]:
                        add(i, "bearish_kicker", "bearish", 2,
                            gap_under_prior_open=p["o"] - a["o"],
                            body_vs_avg=a["body"] / avg)

        # ── three bars ────────────────────────────────────
        if i >= 2:
            x, y, z = (_anatomy(rows[i - 2]), _anatomy(rows[i - 1]), a)
            if avg and x["body"] > _LONG_BODY * avg and z["body"] > _LONG_BODY * avg:
                small_mid = y["body"] < _SMALL_BODY * avg
                if (not x["up"] and small_mid and z["up"]
                        and z["c"] > (x["o"] + x["c"]) / 2):
                    add(i, "morning_star", "bullish", 3,
                        close_into_first_pct=(z["c"] - x["c"]) / x["body"] * 100)
                if (x["up"] and small_mid and not z["up"]
                        and z["c"] < (x["o"] + x["c"]) / 2):
                    add(i, "evening_star", "bearish", 3,
                        close_into_first_pct=(x["c"] - z["c"]) / x["body"] * 100)
                # abandoned baby: the star GAPS away on both sides
                if (not x["up"] and z["up"] and y["h"] < x["l"] and y["h"] < z["l"]
                        and y["body_pct"] <= _DOJI_BODY):
                    add(i, "bullish_abandoned_baby", "bullish", 3,
                        gap_down=x["l"] - y["h"], gap_up=z["l"] - y["h"])
                if (x["up"] and not z["up"] and y["l"] > x["h"] and y["l"] > z["h"]
                        and y["body_pct"] <= _DOJI_BODY):
                    add(i, "bearish_abandoned_baby", "bearish", 3,
                        gap_up=y["l"] - x["h"], gap_down=y["l"] - z["h"])
            # three inside / three outside: the two-bar signal plus the bar
            # that PROVES it — the third close beyond the setup's extreme
            inside_prev = (y["top"] <= x["top"] and y["bot"] >= x["bot"]
                           and x["body"] and y["body"])
            if avg and x["body"] > _LONG_BODY * avg and inside_prev:
                if not x["up"] and y["up"] and z["up"] and z["c"] > x["o"]:
                    add(i, "three_inside_up", "bullish", 3,
                        close_beyond_first_open=z["c"] - x["o"])
                if x["up"] and not y["up"] and not z["up"] and z["c"] < x["o"]:
                    add(i, "three_inside_down", "bearish", 3,
                        close_beyond_first_open=x["o"] - z["c"])
            engulf_prev = (y["top"] >= x["top"] and y["bot"] <= x["bot"]
                           and x["body"] and y["body"])
            if engulf_prev:
                if not x["up"] and y["up"] and z["up"] and z["c"] > y["c"]:
                    add(i, "three_outside_up", "bullish", 3,
                        close_beyond_second=z["c"] - y["c"])
                if x["up"] and not y["up"] and not z["up"] and z["c"] < y["c"]:
                    add(i, "three_outside_down", "bearish", 3,
                        close_beyond_second=y["c"] - z["c"])
            three_up = all(q["up"] for q in (x, y, z))
            three_dn = all(not q["up"] for q in (x, y, z))
            rising = z["c"] > y["c"] > x["c"]
            falling = z["c"] < y["c"] < x["c"]
            bodies_ok = avg and min(q["body"] for q in (x, y, z)) > _SMALL_BODY * avg
            # each open must sit INSIDE the previous body — an open beyond it
            # is a gap, which is a different formation. Checking only one side
            # let a gap-down through as a crow.
            step_up = (x["bot"] <= y["o"] <= x["top"]) and (y["bot"] <= z["o"] <= y["top"])
            if three_up and rising and bodies_ok and step_up:
                add(i, "three_white_soldiers", "bullish", 3,
                    net_move_pct=(z["c"] - x["o"]) / x["o"] * 100)
            if three_dn and falling and bodies_ok and step_up:
                add(i, "three_black_crows", "bearish", 3,
                    net_move_pct=(z["c"] - x["o"]) / x["o"] * 100)

        # ── five bars: three methods ──────────────────────
        # a long bar, three small counter-drift bars held INSIDE its range,
        # then a long bar closing beyond the first — continuation proven
        if i >= 4 and avg:
            f = _anatomy(rows[i - 4])
            mids = [_anatomy(rows[i - 3]), _anatomy(rows[i - 2]),
                    _anatomy(rows[i - 1])]
            long_ends = (f["body"] > _LONG_BODY * avg
                         and a["body"] > _LONG_BODY * avg)
            held_inside = all(q["h"] <= f["h"] and q["l"] >= f["l"]
                              for q in mids)
            small_mids = all(q["body"] < _SMALL_BODY * avg for q in mids)
            if long_ends and held_inside and small_mids:
                if (f["up"] and a["up"] and mids[2]["c"] < mids[0]["c"]
                        and a["c"] > f["c"]):
                    add(i, "rising_three_methods", "bullish", 5,
                        close_beyond_first=a["c"] - f["c"],
                        drift=mids[2]["c"] - mids[0]["c"])
                if (not f["up"] and not a["up"] and mids[2]["c"] > mids[0]["c"]
                        and a["c"] < f["c"]):
                    add(i, "falling_three_methods", "bearish", 5,
                        close_beyond_first=f["c"] - a["c"],
                        drift=mids[2]["c"] - mids[0]["c"])

    out.sort(key=lambda p: p["bars_ago"])
    return out[:limit]


# ── chart patterns, from the caller's pivots ──────────────────────
def _fit(points: list[tuple]) -> tuple | None:
    """Least squares against BAR INDEX, not epoch — sessions have gaps."""
    n = len(points)
    if n < 2:
        return None
    sx = sum(p[0] for p in points)
    sy = sum(p[1] for p in points)
    sxx = sum(p[0] * p[0] for p in points)
    sxy = sum(p[0] * p[1] for p in points)
    den = n * sxx - sx * sx
    if not den:
        return None
    slope = (n * sxy - sx * sy) / den
    return slope, (sy - slope * sx) / n


def _quadfit(vals: list[float]) -> tuple | None:
    """Least-squares parabola v = a2*x² + a1*x + a0 against BAR INDEX,
    plus R². Cramer's rule on the normal equations — stdlib only, so the
    curvature claim ("rounded") is a number, not an eyeball call."""
    n = len(vals)
    if n < 8:
        return None
    s0 = float(n)
    s1 = sum(range(n)); s2 = float(sum(i * i for i in range(n)))
    s3 = float(sum(i ** 3 for i in range(n)))
    s4 = float(sum(i ** 4 for i in range(n)))
    sy = sum(vals)
    sxy = sum(i * v for i, v in enumerate(vals))
    sx2y = sum(i * i * v for i, v in enumerate(vals))
    det = (s4 * (s2 * s0 - s1 * s1) - s3 * (s3 * s0 - s1 * s2)
           + s2 * (s3 * s1 - s2 * s2))
    if abs(det) < 1e-9:
        return None
    a2 = (sx2y * (s2 * s0 - s1 * s1) - s3 * (sxy * s0 - s1 * sy)
          + s2 * (sxy * s1 - s2 * sy)) / det
    a1 = (s4 * (sxy * s0 - sy * s1) - sx2y * (s3 * s0 - s1 * s2)
          + s2 * (s3 * sy - sxy * s2)) / det
    a0 = (s4 * (s2 * sy - sxy * s1) - s3 * (s3 * sy - sxy * s2)
          + sx2y * (s3 * s1 - s2 * s2)) / det
    mean = sy / n
    ss_tot = sum((v - mean) ** 2 for v in vals)
    ss_res = sum((v - (a2 * i * i + a1 * i + a0)) ** 2
                 for i, v in enumerate(vals))
    r2 = 1 - ss_res / ss_tot if ss_tot else 0.0
    return a2, a1, a0, r2


def chart_patterns(rows: list[tuple], pivots: list[tuple], tol: float, ist,
                   kinds: set | None = None, limit: int = 12) -> list[dict]:
    """Named multi-swing formations, built from the shared pivot pass.

    Every one of these is a constraint on a sequence of swings, which is why
    they can be found honestly: the geometry is objective. Whether a given
    instance means anything is not decided here.
    """
    want = kinds or set(CHART_KINDS)
    n = len(rows)
    closes = [r[4] for r in rows]
    out: list[dict] = []
    highs = [(i, p) for i, p, k in pivots if k == "resistance"]
    lows = [(i, p) for i, p, k in pivots if k == "support"]
    seq = sorted([(i, p, "H") for i, p in highs] + [(i, p, "L") for i, p in lows])

    T = lambda i: rows[i][0]               # noqa: E731 — exact bar epoch

    def _break_idx(after: int, level: float, below: bool) -> int | None:
        for j in range(after + 1, n):
            if (closes[j] < level) if below else (closes[j] > level):
                return j
        return None

    def confirmed(after: int, level: float, below: bool) -> dict:
        """Did a close actually break the line, and when?"""
        j = _break_idx(after, level, below)
        if j is not None:
            return {"status": "confirmed", "broke_at": ist(rows[j][0]),
                    "bars_to_break": j - after}
        return {"status": "unconfirmed"}

    def _base_end(after: int, level: float, below: bool) -> int:
        """Where the drawn neckline should stop: the actual break bar if one
        exists, else the latest bar. The line ends where its story ends."""
        j = _break_idx(after, level, below)
        return j if j is not None else n - 1

    def add(name, direction, i1, i2, points, **extra):
        if name not in want:
            return
        out.append({"pattern": name, "direction": direction, "family": "chart",
                    "from": ist(rows[i1][0]), "to": ist(rows[i2][0]),
                    "bars_ago": n - 1 - i2, "span_bars": i2 - i1,
                    "points": points, **extra})

    # ── double top / bottom ───────────────────────────────
    for a in range(len(seq) - 2):
        p, q, r = seq[a], seq[a + 1], seq[a + 2]
        if p[2] == r[2] and q[2] != p[2]:
            same = abs(p[1] - r[1]) <= tol * 1.5
            depth = abs(q[1] - (p[1] + r[1]) / 2)
            if same and depth >= tol * 2:
                # geometry: the three defining pivots at their EXACT bar
                # times and pivot prices, plus the neckline drawn from the
                # first pivot to the break bar (or the latest bar if unbroken)
                geo = lambda below: dict(  # noqa: E731
                    outline=[[T(p[0]), round(p[1], 2)],
                             [T(q[0]), round(q[1], 2)],
                             [T(r[0]), round(r[1], 2)]],
                    base=dict(v=round(q[1], 2), t1=T(p[0]),
                              t2=T(_base_end(r[0], q[1], below))))
                if p[2] == "H":
                    add("double_top", "bearish", p[0], r[0],
                        {"peak_1": round(p[1], 2), "trough": round(q[1], 2),
                         "peak_2": round(r[1], 2)},
                        neckline=round(q[1], 2),
                        measured_move=round(q[1] - depth, 2),
                        peak_gap=round(abs(p[1] - r[1]), 2),
                        _geometry=geo(True),
                        **confirmed(r[0], q[1], below=True))
                else:
                    add("double_bottom", "bullish", p[0], r[0],
                        {"trough_1": round(p[1], 2), "peak": round(q[1], 2),
                         "trough_2": round(r[1], 2)},
                        neckline=round(q[1], 2),
                        measured_move=round(q[1] + depth, 2),
                        trough_gap=round(abs(p[1] - r[1]), 2),
                        _geometry=geo(False),
                        **confirmed(r[0], q[1], below=False))

    # ── head & shoulders (and inverse) ────────────────────
    for a in range(len(seq) - 4):
        w = seq[a:a + 5]
        kinds_seq = "".join(x[2] for x in w)
        if kinds_seq == "HLHLH":
            s1, l1, hd, l2, s2 = w
            # a triple top is the SAME window with three ~equal highs; the
            # head-above-shoulders requirement keeps the two exclusive
            hs_shape = (hd[1] > s1[1] and hd[1] > s2[1]
                        and hd[1] - max(s1[1], s2[1]) >= tol)
            peaks = (s1[1], hd[1], s2[1])
            if (not hs_shape
                    and max(peaks) - min(peaks) <= tol * 1.5
                    and abs(l1[1] - l2[1]) <= tol * 2
                    and min(peaks) - max(l1[1], l2[1]) >= tol * 2):
                neck = min(l1[1], l2[1])
                add("triple_top", "bearish", s1[0], s2[0],
                    {"peak_1": round(s1[1], 2), "peak_2": round(hd[1], 2),
                     "peak_3": round(s2[1], 2)},
                    neckline=round(neck, 2),
                    measured_move=round(neck - (max(peaks) - neck), 2),
                    peak_spread=round(max(peaks) - min(peaks), 2),
                    _geometry=dict(
                        outline=[[T(x[0]), round(x[1], 2)]
                                 for x in (s1, l1, hd, l2, s2)],
                        base=dict(v=round(neck, 2), t1=T(s1[0]),
                                  t2=T(_base_end(s2[0], neck, True)))),
                    **confirmed(s2[0], neck, below=True))
            if (hs_shape and abs(s1[1] - s2[1]) <= tol * 2
                    and abs(l1[1] - l2[1]) <= tol * 2):
                neck = (l1[1] + l2[1]) / 2
                add("head_and_shoulders", "bearish", s1[0], s2[0],
                    {"left_shoulder": round(s1[1], 2), "head": round(hd[1], 2),
                     "right_shoulder": round(s2[1], 2)},
                    neckline=round(neck, 2),
                    measured_move=round(neck - (hd[1] - neck), 2),
                    shoulder_gap=round(abs(s1[1] - s2[1]), 2),
                    _geometry=dict(
                        outline=[[T(x[0]), round(x[1], 2)]
                                 for x in (s1, l1, hd, l2, s2)],
                        base=dict(v=round(neck, 2), t1=T(s1[0]),
                                  t2=T(_base_end(s2[0], neck, True)))),
                    **confirmed(s2[0], neck, below=True))
        if kinds_seq == "LHLHL":
            s1, h1, hd, h2, s2 = w
            ihs_shape = (hd[1] < s1[1] and hd[1] < s2[1]
                         and min(s1[1], s2[1]) - hd[1] >= tol)
            lows3 = (s1[1], hd[1], s2[1])
            if (not ihs_shape
                    and max(lows3) - min(lows3) <= tol * 1.5
                    and abs(h1[1] - h2[1]) <= tol * 2
                    and min(h1[1], h2[1]) - max(lows3) >= tol * 2):
                neck = max(h1[1], h2[1])
                add("triple_bottom", "bullish", s1[0], s2[0],
                    {"trough_1": round(s1[1], 2), "trough_2": round(hd[1], 2),
                     "trough_3": round(s2[1], 2)},
                    neckline=round(neck, 2),
                    measured_move=round(neck + (neck - min(lows3)), 2),
                    trough_spread=round(max(lows3) - min(lows3), 2),
                    _geometry=dict(
                        outline=[[T(x[0]), round(x[1], 2)]
                                 for x in (s1, h1, hd, h2, s2)],
                        base=dict(v=round(neck, 2), t1=T(s1[0]),
                                  t2=T(_base_end(s2[0], neck, False)))),
                    **confirmed(s2[0], neck, below=False))
            if (ihs_shape and abs(s1[1] - s2[1]) <= tol * 2
                    and abs(h1[1] - h2[1]) <= tol * 2):
                neck = (h1[1] + h2[1]) / 2
                add("inverse_head_and_shoulders", "bullish", s1[0], s2[0],
                    {"left_shoulder": round(s1[1], 2), "head": round(hd[1], 2),
                     "right_shoulder": round(s2[1], 2)},
                    neckline=round(neck, 2),
                    measured_move=round(neck + (neck - hd[1]), 2),
                    shoulder_gap=round(abs(s1[1] - s2[1]), 2),
                    _geometry=dict(
                        outline=[[T(x[0]), round(x[1], 2)]
                                 for x in (s1, h1, hd, h2, s2)],
                        base=dict(v=round(neck, 2), t1=T(s1[0]),
                                  t2=T(_base_end(s2[0], neck, False)))),
                    **confirmed(s2[0], neck, below=False))

    # ── triangles and wedges: two fitted boundaries ───────
    # A converging pair of lines IS the pattern; which name it takes falls out
    # of the two slopes, so nothing is hand-classified.
    flat = tol / max(1, len(rows)) * 4     # slope small enough to read as level
    for span in (60, 90, 120):
        if n < span + 10:
            continue
        i0 = n - span
        hp = [(i, p) for i, p in highs if i >= i0]
        lp = [(i, p) for i, p in lows if i >= i0]
        if len(hp) < 2 or len(lp) < 2:
            continue
        fh, fl = _fit(hp), _fit(lp)
        if not fh or not fl:
            continue
        sh, sl = fh[0], fl[0]
        top_now, bot_now = fh[0] * (n - 1) + fh[1], fl[0] * (n - 1) + fl[1]
        top_then, bot_then = fh[0] * i0 + fh[1], fl[0] * i0 + fl[1]
        if top_now <= bot_now:
            continue                       # lines already crossed; not a shape
        # one width ratio decides the family: <0.85 converging, >1.15
        # widening, in between the boundaries are holding parallel
        ratio = (top_now - bot_now) / max(1e-9, top_then - bot_then)
        converging = ratio < 0.85
        widening = ratio > 1.15
        parallel = 0.85 <= ratio <= 1.15
        pts = {"upper_now": round(top_now, 2), "lower_now": round(bot_now, 2),
               "upper_slope_per_bar": round(sh, 4),
               "lower_slope_per_bar": round(sl, 4)}
        # The fit lives in bar-index space and the chart's x-axis is
        # index-spaced, so a segment anchored at the two endpoint bars
        # renders the fitted line EXACTLY — no intermediate points needed.
        # Endpoint values are the same numbers reported in `points`.
        common = dict(highs_used=len(hp), lows_used=len(lp),
                      width_now=round(top_now - bot_now, 2),
                      apex_bars=(round((top_now - bot_now) / (sl - sh))
                                 if sl > sh else None),
                      _geometry=dict(edges=dict(
                          upper=[[T(i0), round(top_then, 2)],
                                 [T(n - 1), round(top_now, 2)]],
                          lower=[[T(i0), round(bot_then, 2)],
                                 [T(n - 1), round(bot_now, 2)]])))
        if converging and abs(sh) <= flat and sl > flat:
            add("ascending_triangle", "bullish", i0, n - 1, pts, **common,
                **confirmed(n - 1, top_now, below=False))
        elif converging and abs(sl) <= flat and sh < -flat:
            add("descending_triangle", "bearish", i0, n - 1, pts, **common,
                **confirmed(n - 1, bot_now, below=True))
        # A wedge or symmetrical triangle has no single decisive boundary —
        # either edge can give way — so confirmation is genuinely not assessed
        # here. That has to be SAID: leaving `status` absent is a deliberate
        # omission expressed as a missing key, which reads as silence.
        na = {"status": "not_assessed",
              "status_note": ("this shape has two candidate breakout edges, so "
                              "no single confirmation level applies — report it "
                              "as an unresolved shape, never as confirmed")}
        if converging and sh < -flat and sl > flat:
            add("symmetrical_triangle", "neutral", i0, n - 1, pts, **common, **na)
        elif converging and sh > flat and sl > flat:
            add("rising_wedge", "bearish", i0, n - 1, pts, **common, **na)
        elif converging and sh < -flat and sl < -flat:
            add("falling_wedge", "bullish", i0, n - 1, pts, **common, **na)
        # parallel boundaries: a rectangle holds level, a channel holds a
        # trend, a broadening formation swings wider — same fit, same rail:
        # either edge can end it, so none of them gets a confirmation level
        elif parallel and abs(sh) <= flat and abs(sl) <= flat:
            add("rectangle", "neutral", i0, n - 1, pts, **common, **na)
        elif parallel and sh > flat and sl > flat:
            add("channel_up", "bullish", i0, n - 1, pts, **common, **na)
        elif parallel and sh < -flat and sl < -flat:
            add("channel_down", "bearish", i0, n - 1, pts, **common, **na)
        elif widening and sh > flat and sl < -flat:
            add("broadening", "neutral", i0, n - 1, pts, **common, **na)
        if out and out[-1].get("span_bars") == n - 1 - i0:
            break                          # one window is enough per shape

    # ── flags: a sharp impulse, then a small counter-drift ─
    atr_like = tol * 2
    for i in range(20, n - 5):
        for imp in (8, 12, 15):
            if i - imp < 0:
                continue
            move = closes[i] - closes[i - imp]
            if abs(move) < atr_like * 3:
                continue
            rest = rows[i:min(n, i + 20)]
            if len(rest) < 5:
                continue
            r_hi = max(r[2] for r in rest)
            r_lo = min(r[3] for r in rest)
            if (r_hi - r_lo) > abs(move) * 0.5:
                continue                   # consolidation too wide to be a flag
            drift = rest[-1][4] - rest[0][4]
            end_i = min(n, i + 20) - 1
            m = len(rest)
            # a pennant is the same impulse whose consolidation CONVERGES:
            # fitted highs falling, fitted lows rising, width down >=30%
            cfh = _fit([(j, rest[j][2]) for j in range(m)])
            cfl = _fit([(j, rest[j][3]) for j in range(m)])
            pennant = bool(
                cfh and cfl and cfh[0] < 0 < cfl[0]
                and ((cfh[1] + cfh[0] * (m - 1)) - (cfl[1] + cfl[0] * (m - 1))
                     < (cfh[1] - cfl[1]) * 0.7))
            flag_geo = dict(
                pole=[[T(i - imp), round(closes[i - imp], 2)],
                      [T(i), round(closes[i], 2)]])
            if pennant:
                flag_geo["edges"] = dict(
                    upper=[[T(i), round(cfh[1], 2)],
                           [T(end_i), round(cfh[1] + cfh[0] * (m - 1), 2)]],
                    lower=[[T(i), round(cfl[1], 2)],
                           [T(end_i), round(cfl[1] + cfl[0] * (m - 1), 2)]])
            else:
                flag_geo["box"] = [[T(i), round(r_hi, 2)],
                                   [T(end_i), round(r_lo, 2)]]
            facts = {"pole_from": round(closes[i - imp], 2),
                     "pole_to": round(closes[i], 2),
                     "flag_high": round(r_hi, 2), "flag_low": round(r_lo, 2)}
            extra = dict(pole=round(move, 2), flag_bars=m,
                         _geometry=flag_geo)
            if pennant:
                extra.update(upper_slope_per_bar=round(cfh[0], 4),
                             lower_slope_per_bar=round(cfl[0], 4))
            if move > 0 and (pennant or drift <= 0):
                add("bull_pennant" if pennant else "bull_flag", "bullish",
                    i - imp, end_i, facts, **extra,
                    measured_move=round(r_lo + move, 2),
                    **confirmed(end_i, r_hi, below=False))
            elif move < 0 and (pennant or drift >= 0):
                add("bear_pennant" if pennant else "bear_flag", "bearish",
                    i - imp, end_i, facts, **extra,
                    measured_move=round(r_hi + move, 2),
                    **confirmed(end_i, r_lo, below=True))
            break

    # ── rounded turns: cup & handle, rounding bottom / top ─
    # The parabola IS the claim: curvature sign says which way it turns,
    # R² (reported) says how well "rounded" actually fits, and the vertex
    # must sit in the middle half so a plain trend can't pass as a cup.
    def _parabola(e: int, span: int):
        """Fit closes[i0..e]; None unless it reads as a rounded turn."""
        i0 = e - span + 1
        if i0 < 5:
            return None
        vals = closes[i0:e + 1]
        q = _quadfit(vals)
        if not q:
            return None
        a2, a1, a0, r2 = q
        if r2 < 0.75 or a2 == 0:
            return None
        m = len(vals)
        vx = -a1 / (2 * a2)
        if not (0.25 * (m - 1) <= vx <= 0.75 * (m - 1)):
            return None
        fit = lambda x: a2 * x * x + a1 * x + a0  # noqa: E731
        arc = [[T(i0 + j), round(fit(j), 2)]
               for j in sorted({round(k * (m - 1) / 12) for k in range(13)})]
        return dict(i0=i0, a2=a2, r2=r2, rim_l=fit(0), rim_r=fit(m - 1),
                    turn=fit(vx), arc=arc)

    # cups get FIRST CLAIM over every span — a rounding bottom found at a
    # narrower span must not preempt the cup whose handle it is part of
    found_cup = False
    for span in (40, 60, 90, 120):
        for handle in (8, 12):
            e = n - 1 - handle
            pb = _parabola(e, span)
            if not pb or pb["a2"] <= 0:
                continue
            depth = min(pb["rim_l"], pb["rim_r"]) - pb["turn"]
            # rim symmetry is judged against the cup's own depth — an
            # absolute tolerance rejects deep cups for shallow reasons
            if (depth < tol * 3
                    or abs(pb["rim_l"] - pb["rim_r"])
                    > max(tol * 2.5, 0.15 * depth)):
                continue
            hs = rows[e + 1:n]
            if not (all(r[4] < pb["rim_r"] + tol for r in hs[:-1])
                    and min(r[3] for r in hs) >= pb["rim_r"] - 0.5 * depth):
                continue
            found_cup = True
            add("cup_and_handle", "bullish", pb["i0"], n - 1,
                {"left_rim": round(pb["rim_l"], 2),
                 "right_rim": round(pb["rim_r"], 2),
                 "low": round(pb["turn"], 2), "r2": round(pb["r2"], 3)},
                depth=round(depth, 2), handle_bars=handle,
                handle_low=round(min(r[3] for r in hs), 2),
                measured_move=round(pb["rim_r"] + depth, 2),
                _geometry=dict(
                    outline=pb["arc"],
                    base=dict(v=round(pb["rim_r"], 2), t1=T(pb["i0"]),
                              t2=T(_base_end(e, pb["rim_r"], False)))),
                **confirmed(e, pb["rim_r"], below=False))
            break
        if found_cup:
            break
    for span in (40, 60, 90, 120):
        if found_cup:
            break                          # the cup already owns this base
        pb = _parabola(n - 1, span)
        if not pb:
            continue
        if pb["a2"] > 0:
            depth = min(pb["rim_l"], pb["rim_r"]) - pb["turn"]
            if depth < tol * 3:
                continue
            add("rounding_bottom", "bullish", pb["i0"], n - 1,
                {"left_rim": round(pb["rim_l"], 2),
                 "right_rim": round(pb["rim_r"], 2),
                 "low": round(pb["turn"], 2), "r2": round(pb["r2"], 3)},
                depth=round(depth, 2),
                measured_move=round(min(pb["rim_l"], pb["rim_r"]) + depth, 2),
                _geometry=dict(
                    outline=pb["arc"],
                    base=dict(v=round(pb["rim_r"], 2), t1=T(pb["i0"]),
                              t2=T(n - 1))),
                status="forming",
                status_note=("the base ends at the latest bar, so there is "
                             "nothing to confirm yet — say it is still "
                             "forming, not that it failed"))
            break
        height = pb["turn"] - max(pb["rim_l"], pb["rim_r"])
        if height < tol * 3:
            continue
        add("rounding_top", "bearish", pb["i0"], n - 1,
            {"left_rim": round(pb["rim_l"], 2),
             "right_rim": round(pb["rim_r"], 2),
             "high": round(pb["turn"], 2), "r2": round(pb["r2"], 3)},
            height=round(height, 2),
            measured_move=round(max(pb["rim_l"], pb["rim_r"]) - height, 2),
            _geometry=dict(
                outline=pb["arc"],
                base=dict(v=round(pb["rim_r"], 2), t1=T(pb["i0"]),
                          t2=T(n - 1))),
            status="forming",
            status_note=("the base ends at the latest bar, so there is "
                         "nothing to confirm yet — say it is still "
                         "forming, not that it failed"))
        break

    # de-duplicate near-identical instances of the same shape
    seen: set = set()
    uniq: list[dict] = []
    for p in sorted(out, key=lambda x: x["bars_ago"]):
        key = (p["pattern"], p["span_bars"] // 5, p["bars_ago"] // 5)
        if key in seen:
            continue
        seen.add(key)
        uniq.append(p)
    for p in uniq:
        base = p["pattern"][:2].upper() + p["from"].replace(" ", "")[:7]
        p["id"] = base.upper()
    return uniq[:limit]


# ── market structure ──────────────────────────────────────────────
def market_structure(rows: list[tuple], pivots: list[tuple], ist,
                     limit: int = 14) -> dict:
    """HH / HL / LH / LL, and where structure actually broke.

    No folklore in here at all — it is the sequence of swings, named. That
    makes it the one pattern family with nothing to defend.
    """
    n = len(rows)
    closes = [r[4] for r in rows]
    seq = sorted([(i, p, "H" if k == "resistance" else "L") for i, p, k in pivots])
    # collapse consecutive same-side swings to the more extreme one, so a
    # label always compares like with like
    clean: list[tuple] = []
    for s in seq:
        if clean and clean[-1][2] == s[2]:
            better = s[1] > clean[-1][1] if s[2] == "H" else s[1] < clean[-1][1]
            if better:
                clean[-1] = s
            continue
        clean.append(s)

    labels: list[dict] = []
    last_h = last_l = None
    for i, p, side in clean:
        if side == "H":
            lab = "HH" if last_h is not None and p > last_h else (
                "LH" if last_h is not None else "H")
            last_h = p
        else:
            lab = "HL" if last_l is not None and p > last_l else (
                "LL" if last_l is not None else "L")
            last_l = p
        labels.append({"t": ist(rows[i][0]), "bars_ago": n - 1 - i,
                       "price": round(p, 2), "label": lab, "_i": i})

    # trend from the last four labels; a break of the most recent opposing
    # swing is a break of structure, and the FIRST one against the prevailing
    # trend is the change of character
    recent = [x["label"] for x in labels[-4:]]
    ups = sum(1 for x in recent if x in ("HH", "HL"))
    downs = sum(1 for x in recent if x in ("LL", "LH"))
    trend = "up" if ups > downs else "down" if downs > ups else "sideways"

    # BOS continues the prevailing trend, CHoCH breaks it. An earlier cut
    # only tested the against-trend case and then flipped the trend, so every
    # event came back CHoCH and BOS was unreachable — the two labels have to
    # be derived from the SAME comparison to stay distinguishable.
    events: list[dict] = []
    for k in range(1, len(labels)):
        seg = [x["label"] for x in labels[max(0, k - 3):k]]
        u = sum(1 for x in seg if x in ("HH", "HL"))
        d = sum(1 for x in seg if x in ("LL", "LH"))
        t = "up" if u > d else "down" if d > u else None
        if not t:
            continue
        lab = labels[k]["label"]
        if lab == "HH":
            kind = "BOS" if t == "up" else "CHoCH"
        elif lab == "LL":
            kind = "BOS" if t == "down" else "CHoCH"
        else:
            continue                       # HL/LH continue the leg, break nothing
        events.append({"t": labels[k]["t"], "price": labels[k]["price"],
                       "bars_ago": labels[k]["bars_ago"], "event": kind,
                       "direction": "up" if lab == "HH" else "down",
                       "prevailing_trend_before": t})
    for x in labels:
        x.pop("_i", None)
    return {"trend": trend, "swings": labels[-limit:],
            "structure_events": events[-6:], "last_price": round(closes[-1], 2)}
