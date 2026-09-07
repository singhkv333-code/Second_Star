#!/usr/bin/env python3
"""Synthetic-bar tests for every pattern detector.

Each case BUILDS bars that contain the pattern by construction and asserts
the detector names it; the negative controls build the near-miss that a
sloppy detector would also flag and assert silence. Bars are handmade, so a
failure here is always the detector's fault, never the data's.

Run: python3 test_patterns.py   (exit 0 = all pass)
"""
from __future__ import annotations

import sys

import patterns
from dataserver import _atr, _pivots, _tolerance

T0, DAY = 1700000000, 86400
IST = str  # tests never render timestamps; identity keeps asserts readable


def rows_of(specs: list[tuple]) -> list[tuple]:
    """[(o,h,l,c), ...] -> full bar tuples with sequential times."""
    return [(T0 + i * DAY, o, h, l, c, 1000.0)
            for i, (o, h, l, c) in enumerate(specs)]


def flat_ctx(n: int = 16, px: float = 100.0, body: float = 0.5):
    """Alternating small bars: rolling avg body ~= `body`, no trend."""
    out = []
    for i in range(n):
        if i % 2 == 0:
            o, c = px, px + body
        else:
            o, c = px + body, px
        out.append((o, max(o, c) + 0.3, min(o, c) - 0.3, c))
    return out


def trend_ctx(n: int = 16, px: float = 100.0, per: float = 1.0):
    """Directional bars closing `per` further each bar (sign = direction)."""
    out, o = [], px
    for _ in range(n):
        c = o + per
        out.append((o, max(o, c) + 0.3, min(o, c) - 0.3, c))
        o = c
    return out


def candles_in(specs, name):
    rows = rows_of(specs)
    found = patterns.candlesticks(rows, _atr(rows), IST, limit=200)
    return [f for f in found if f["pattern"] == name]


def charts_in(specs, name=None):
    rows = rows_of(specs)
    found = patterns.chart_patterns(rows, _pivots(rows, 5), _tolerance(rows),
                                    IST, limit=30)
    return [f for f in found if name is None or f["pattern"] == name]


FAILS: list[str] = []


def check(label: str, ok: bool, detail: str = ""):
    if not ok:
        FAILS.append(f"{label}  {detail}")
    print(("PASS " if ok else "FAIL ") + label + (f"  {detail}" if not ok and detail else ""))


def last_bar_hit(hits, specs) -> bool:
    return any(h["bars_ago"] == 0 for h in hits)


# ── candlesticks: positives ───────────────────────────────────────
def test_candles():
    # single bar
    c = flat_ctx()
    check("doji", last_bar_hit(candles_in(c + [(100, 101, 99, 100.05)], "doji"), c))
    check("dragonfly_doji", last_bar_hit(
        candles_in(c + [(100, 100.1, 98, 100.02)], "dragonfly_doji"), c))
    check("gravestone_doji", last_bar_hit(
        candles_in(c + [(100, 102, 99.9, 100.02)], "gravestone_doji"), c))
    check("long_legged_doji", last_bar_hit(
        candles_in(c + [(100, 101.2, 98.9, 100.05)], "long_legged_doji"), c))
    down, up = trend_ctx(per=-1.0), trend_ctx(per=1.0)
    dpx, upx = down[-1][3], up[-1][3]           # last close of each run
    hammer_bar = lambda p: (p, p + 0.35, p - 1.0, p + 0.3)      # noqa: E731
    istar_bar = lambda p: (p, p + 1.1, p - 0.05, p + 0.3)       # noqa: E731
    check("hammer", last_bar_hit(candles_in(down + [hammer_bar(dpx)], "hammer"), down))
    check("hanging_man", last_bar_hit(candles_in(up + [hammer_bar(upx)], "hanging_man"), up))
    check("inverted_hammer", last_bar_hit(candles_in(down + [istar_bar(dpx)], "inverted_hammer"), down))
    check("shooting_star", last_bar_hit(candles_in(up + [istar_bar(upx)], "shooting_star"), up))
    check("marubozu", last_bar_hit(candles_in(c + [(100, 102.02, 99.99, 102)], "marubozu"), c))
    check("spinning_top", last_bar_hit(candles_in(c + [(100, 101, 99.4, 100.3)], "spinning_top"), c))
    check("bullish_belt_hold", last_bar_hit(
        candles_in(down + [(dpx, dpx + 2.3, dpx - 0.02, dpx + 2)], "bullish_belt_hold"), down))
    check("bearish_belt_hold", last_bar_hit(
        candles_in(up + [(upx, upx + 0.02, upx - 2.3, upx - 2)], "bearish_belt_hold"), up))

    # two bar
    check("bullish_engulfing", last_bar_hit(candles_in(
        c + [(100.5, 100.6, 99.9, 100), (99.8, 100.9, 99.7, 100.8)], "bullish_engulfing"), c))
    check("bearish_engulfing", last_bar_hit(candles_in(
        c + [(100, 100.6, 99.9, 100.5), (100.8, 100.9, 99.7, 99.8)], "bearish_engulfing"), c))
    check("bullish_harami", last_bar_hit(candles_in(
        c + [(101.5, 101.6, 99.4, 99.5), (100, 100.6, 99.9, 100.5)], "bullish_harami"), c))
    check("bearish_harami", last_bar_hit(candles_in(
        c + [(99.5, 101.6, 99.4, 101.5), (100.5, 100.6, 99.9, 100)], "bearish_harami"), c))
    check("piercing_line", last_bar_hit(candles_in(
        c + [(101, 101.1, 99.4, 99.5), (99.2, 100.5, 99.1, 100.4)], "piercing_line"), c))
    check("dark_cloud_cover", last_bar_hit(candles_in(
        c + [(99.5, 101.1, 99.4, 101), (101.4, 101.5, 99.9, 100)], "dark_cloud_cover"), c))
    check("tweezer_top", last_bar_hit(candles_in(
        c + [(100, 101.5, 99.9, 101), (100.9, 101.45, 100.1, 100.2)], "tweezer_top"), c))
    check("tweezer_bottom", last_bar_hit(candles_in(
        c + [(101, 101.1, 99.5, 100), (100.1, 100.9, 99.55, 100.8)], "tweezer_bottom"), c))
    check("bullish_kicker", last_bar_hit(candles_in(
        c + [(100, 100.1, 98.9, 99), (100.5, 101.7, 100.4, 101.6)], "bullish_kicker"), c))
    check("bearish_kicker", last_bar_hit(candles_in(
        c + [(100, 101.1, 99.9, 101), (99.5, 99.6, 98.3, 98.4)], "bearish_kicker"), c))

    # three bar
    check("morning_star", last_bar_hit(candles_in(
        c + [(101, 101.1, 99.4, 99.5), (99.3, 99.5, 99.0, 99.15),
             (99.4, 100.7, 99.3, 100.6)], "morning_star"), c))
    check("evening_star", last_bar_hit(candles_in(
        c + [(99.5, 101.1, 99.4, 101), (101.2, 101.5, 101.0, 101.35),
             (101.1, 101.2, 99.8, 99.9)], "evening_star"), c))
    check("bullish_abandoned_baby", last_bar_hit(candles_in(
        c + [(101, 101.1, 99.4, 99.5), (99.1, 99.2, 98.9, 99.12),
             (99.6, 100.9, 99.5, 100.8)], "bullish_abandoned_baby"), c))
    check("bearish_abandoned_baby", last_bar_hit(candles_in(
        c + [(99.5, 101.1, 99.4, 101), (101.4, 101.6, 101.3, 101.42),
             (101.1, 101.2, 99.9, 100.0)], "bearish_abandoned_baby"), c))
    check("three_white_soldiers", last_bar_hit(candles_in(
        c + [(100, 101.1, 99.9, 101), (100.5, 101.9, 100.4, 101.8),
             (101.2, 102.8, 101.1, 102.7)], "three_white_soldiers"), c))
    check("three_black_crows", last_bar_hit(candles_in(
        c + [(101, 101.1, 99.9, 100), (100.5, 100.6, 99.1, 99.2),
             (99.8, 99.9, 98.2, 98.3)], "three_black_crows"), c))
    check("three_inside_up", last_bar_hit(candles_in(
        c + [(101.5, 101.6, 99.4, 99.5), (100, 100.7, 99.9, 100.6),
             (100.7, 101.9, 100.6, 101.8)], "three_inside_up"), c))
    check("three_inside_down", last_bar_hit(candles_in(
        c + [(99.5, 101.6, 99.4, 101.5), (101, 101.1, 100.3, 100.4),
             (100.3, 100.4, 99.1, 99.2)], "three_inside_down"), c))
    check("three_outside_up", last_bar_hit(candles_in(
        c + [(100.5, 100.6, 99.9, 100), (99.8, 100.9, 99.7, 100.8),
             (100.9, 101.3, 100.8, 101.2)], "three_outside_up"), c))
    check("three_outside_down", last_bar_hit(candles_in(
        c + [(100, 100.6, 99.9, 100.5), (100.8, 100.9, 99.7, 99.8),
             (99.7, 99.8, 99.3, 99.4)], "three_outside_down"), c))

    # five bar
    check("rising_three_methods", last_bar_hit(candles_in(
        c + [(100, 102.2, 99.9, 102), (101.8, 101.9, 101.5, 101.6),
             (101.6, 101.7, 101.3, 101.4), (101.4, 101.5, 101.1, 101.2),
             (101.5, 103.2, 101.4, 103)], "rising_three_methods"), c))
    check("falling_three_methods", last_bar_hit(candles_in(
        c + [(102, 102.1, 99.8, 100), (100.2, 100.5, 100.1, 100.4),
             (100.4, 100.7, 100.3, 100.6), (100.6, 100.9, 100.5, 100.8),
             (100.5, 100.6, 98.8, 99)], "falling_three_methods"), c))

    # negatives: the near-miss a sloppy detector would also flag
    check("NOT crows on gap-down", not candles_in(
        flat_ctx() + [(101, 101.1, 99.9, 100), (99.0, 99.1, 98.0, 98.1),
                      (97.5, 97.6, 96.5, 96.6)], "three_black_crows"))
    check("NOT hammer in uptrend", not candles_in(
        trend_ctx(per=1.0) + [hammer_bar(trend_ctx(per=1.0)[-1][3])], "hammer"))
    check("NOT kicker without open jump", not candles_in(
        flat_ctx() + [(100, 100.1, 98.9, 99), (99.5, 100.7, 99.4, 100.6)], "bullish_kicker"))
    check("NOT doji with real body", not candles_in(
        flat_ctx() + [(100, 101, 99, 100.6)], "doji"))
    check("NOT three methods when mid pokes out", not candles_in(
        flat_ctx() + [(100, 102.2, 99.9, 102), (101.8, 102.5, 101.5, 101.6),
                      (101.6, 101.7, 101.3, 101.4), (101.4, 101.5, 101.1, 101.2),
                      (101.5, 103.2, 101.4, 103)], "rising_three_methods"))


# ── chart patterns ────────────────────────────────────────────────
def path(*segs):
    """Piecewise-linear closes: path((100, 8, 120), (None, 8, 110), ...) —
    each seg is (start_or_None, bars, end); None chains from the prior end."""
    closes = []
    cur = None
    for start, bars, end in segs:
        a = cur if start is None else start
        for j in range(1, bars + 1):
            closes.append(a + (end - a) * j / bars)
        cur = end
    return closes


def bars_from_closes(closes, wick=0.4):
    """The wick rides the MOVE side only: a down bar opening at the prior
    peak must not tie the peak bar's high, or the pivot pass sees two swing
    highs at one crest and every H-L-H-L-H sequence dissolves."""
    out, o = [], closes[0]
    for c in closes:
        if c >= o:
            out.append((o, c + wick, o - wick * 0.25, c))
        else:
            out.append((o, o + wick * 0.25, c - wick, c))
        o = c
    return out


def osc(n, lo_fn, hi_fn, period=12):
    """Triangle wave riding between two boundary functions of bar index —
    crests/troughs are >= period/2 apart so ±5-bar pivots see them."""
    closes = []
    for i in range(n):
        ph = (i % period) / period
        w = 2 * ph if ph <= 0.5 else 2 * (1 - ph)   # 0..1..0
        closes.append(lo_fn(i) + (hi_fn(i) - lo_fn(i)) * w)
    return closes


def test_charts():
    # swing-sequence shapes
    hs = bars_from_closes(path((100, 8, 120), (None, 8, 108), (None, 8, 135),
                               (None, 8, 108.4), (None, 8, 121), (None, 8, 100)))
    check("head_and_shoulders", bool(charts_in(hs, "head_and_shoulders")))
    ihs = bars_from_closes(path((135, 8, 115), (None, 8, 127), (None, 8, 100),
                                (None, 8, 126.6), (None, 8, 114), (None, 8, 135)))
    check("inverse_head_and_shoulders", bool(charts_in(ihs, "inverse_head_and_shoulders")))
    dt = bars_from_closes(path((100, 8, 120), (None, 8, 110), (None, 8, 120.3),
                               (None, 8, 104)))
    check("double_top", bool(charts_in(dt, "double_top")))
    db = bars_from_closes(path((120, 8, 100), (None, 8, 110), (None, 8, 99.8),
                               (None, 8, 116)))
    check("double_bottom", bool(charts_in(db, "double_bottom")))
    tt = bars_from_closes(path((100, 8, 120), (None, 8, 110), (None, 8, 120.3),
                               (None, 8, 110.2), (None, 8, 119.8), (None, 8, 104)))
    hits = charts_in(tt, "triple_top")
    check("triple_top", bool(hits))
    check("triple_top not ALSO h&s", not charts_in(tt, "head_and_shoulders"))
    tb = bars_from_closes(path((120, 8, 100), (None, 8, 110), (None, 8, 99.7),
                               (None, 8, 109.8), (None, 8, 100.2), (None, 8, 116)))
    check("triple_bottom", bool(charts_in(tb, "triple_bottom")))

    # fitted-boundary shapes (80 bars, period-12 oscillation)
    check("ascending_triangle", bool(charts_in(bars_from_closes(
        osc(80, lambda i: 100 + i * 0.20, lambda i: 120 - i * 0.02)), "ascending_triangle")))
    check("descending_triangle", bool(charts_in(bars_from_closes(
        osc(80, lambda i: 100 + i * 0.02, lambda i: 120 - i * 0.20)), "descending_triangle")))
    check("symmetrical_triangle", bool(charts_in(bars_from_closes(
        osc(80, lambda i: 100 + i * 0.11, lambda i: 120 - i * 0.11)), "symmetrical_triangle")))
    check("rising_wedge", bool(charts_in(bars_from_closes(
        osc(80, lambda i: 100 + i * 0.55, lambda i: 108 + i * 0.45)), "rising_wedge")))
    check("falling_wedge", bool(charts_in(bars_from_closes(
        osc(80, lambda i: 100 - i * 0.55, lambda i: 108 - i * 0.65)), "falling_wedge")))
    check("rectangle", bool(charts_in(bars_from_closes(
        osc(80, lambda i: 100, lambda i: 112)), "rectangle")))
    check("channel_up", bool(charts_in(bars_from_closes(
        osc(80, lambda i: 100 + i * 0.5, lambda i: 110 + i * 0.5)), "channel_up")))
    check("channel_down", bool(charts_in(bars_from_closes(
        osc(80, lambda i: 200 - i * 0.5, lambda i: 210 - i * 0.5)), "channel_down")))
    check("broadening", bool(charts_in(bars_from_closes(
        osc(80, lambda i: 100 - i * 0.18, lambda i: 108 + i * 0.18)), "broadening")))

    # impulse shapes
    flat20 = [100.0] * 24
    flag = bars_from_closes(flat20 + path((100, 10, 130), (None, 12, 127.5)))
    check("bull_flag", bool(charts_in(flag, "bull_flag")))
    bflag = bars_from_closes(flat20 + path((100, 10, 70), (None, 12, 72.5)))
    check("bear_flag", bool(charts_in(bflag, "bear_flag")))
    # convergence must be slow enough that the edges never cross inside the
    # window — crossed boundaries stop being a pennant (or any shape)
    pen_tail = osc(14, lambda i: 128 + i * 0.15, lambda i: 133 - i * 0.15, period=6)
    pen = bars_from_closes(flat20 + path((100, 10, 130)) + pen_tail, wick=0.15)
    check("bull_pennant", bool(charts_in(pen, "bull_pennant")))
    bpen_tail = osc(14, lambda i: 67 + i * 0.15, lambda i: 72 - i * 0.15, period=6)
    bpen = bars_from_closes(flat20 + path((100, 10, 70)) + bpen_tail, wick=0.15)
    check("bear_pennant", bool(charts_in(bpen, "bear_pennant")))

    # rounded shapes
    lead = [130.0] * 8
    cup_arc = [100 + ((i - 30) ** 2) / 30 for i in range(61)]     # 130→100→130
    handle = [127, 126, 125.5, 125.8, 126.2, 126.0, 126.4, 126.1]
    cup = bars_from_closes(lead + cup_arc + handle)
    check("cup_and_handle", bool(charts_in(cup, "cup_and_handle")))
    rb = bars_from_closes(lead + cup_arc)
    check("rounding_bottom", bool(charts_in(rb, "rounding_bottom")))
    lead_lo = [100.0] * 8
    top_arc = [130 - ((i - 30) ** 2) / 30 for i in range(61)]     # 100→130→100
    rt = bars_from_closes(lead_lo + top_arc)
    check("rounding_top", bool(charts_in(rt, "rounding_top")))

    # negative: a straight trend must not read as a rounded turn
    check("NOT rounding on straight trend", not charts_in(
        bars_from_closes(path((100, 70, 170))), "rounding_bottom"))


def test_structure():
    up = bars_from_closes(path((100, 8, 110), (None, 8, 104), (None, 8, 118),
                               (None, 8, 111), (None, 8, 126), (None, 8, 118),
                               (None, 8, 134)))
    rows = rows_of(up)
    s = patterns.market_structure(rows, _pivots(rows, 5), IST)
    check("structure swings labeled", bool(s and s.get("swings")))
    check("structure uptrend seen", s.get("trend") == "up", f"got {s.get('trend')}")


if __name__ == "__main__":
    test_candles()
    test_charts()
    test_structure()
    print()
    every = set(patterns.CANDLE_KINDS) | set(patterns.CHART_KINDS)
    print(f"{len(every)} detectors under test; {len(FAILS)} failures")
    if FAILS:
        print("\n".join("  " + f for f in FAILS))
        sys.exit(1)
