"""Indicator engine for Charto.

A registry, not a switch statement. Each entry declares what it needs and
what it returns, so `get_indicator` stays one tool over many indicators
rather than one tool per indicator — and the model picks the name, the
period, the source column and the interval itself instead of choosing from a
handful of frozen presets. Adding an indicator is adding a row.

**Conventions are stated, because this is where implementations silently
disagree.** Each spec carries a `formula` string that travels back with the
result, so a reply can say what it computed rather than asserting a number.
The two traps this file is careful about:

- **Wilder smoothing is not EMA.** Wilder uses k = 1/n; EMA uses k = 2/(n+1).
  RSI, ATR and ADX are Wilder-smoothed. Confusing the two is the single most
  common indicator bug and it produces plausible, wrong numbers.
- **Standard deviation is population, not sample.** Bollinger Bands divide by
  n, matching TradingView. Dividing by n-1 moves the bands visibly.

Recursive indicators (Supertrend, PSAR) must be computed forward over the
whole series, never over a window, or the ratchet starts in the wrong place.
"""
from __future__ import annotations

import time as _time
import math as _math

# ── primitives ────────────────────────────────────────────────────
Series = list          # list[float | None], aligned 1:1 with rows


def _src(rows: list[tuple], name: str = "close") -> list[float]:
    """The price column an indicator runs on. rows = (t, o, h, l, c, v)."""
    i = {"open": 1, "high": 2, "low": 3, "close": 4, "volume": 5}
    if name in i:
        return [r[i[name]] for r in rows]
    if name == "hl2":
        return [(r[2] + r[3]) / 2 for r in rows]
    if name == "hlc3":                      # a.k.a. typical price
        return [(r[2] + r[3] + r[4]) / 3 for r in rows]
    if name == "ohlc4":
        return [(r[1] + r[2] + r[3] + r[4]) / 4 for r in rows]
    raise ValueError(f"unknown source '{name}'")


SOURCES = ("close", "open", "high", "low", "hl2", "hlc3", "ohlc4", "volume")


def sma(v: list[float], n: int) -> Series:
    out, s = [], 0.0
    for i, x in enumerate(v):
        s += x
        if i >= n:
            s -= v[i - n]
        out.append(s / n if i >= n - 1 else None)
    return out


def ema(v: list[float], n: int) -> Series:
    """k = 2/(n+1), SEEDED WITH SMA(n) at bar n-1 — what Pine's ta.ema does.

    The other common choice is to seed from the first value and let the
    recursion run through the warmup. It is wrong here for one reason: it
    disagrees with the chart the user is comparing against. The gap decays by
    (1-k) a bar and vanishes a few hundred bars in, so it is invisible at the
    3000-bar default fetch — but on a thinly-listed symbol, or a shortened
    `limit`, the differing part is the part actually on screen.
    """
    out, k, prev = [], 2 / (n + 1), None
    for i, x in enumerate(v):
        if i < n - 1:
            out.append(None)
            continue
        prev = sum(v[:n]) / n if prev is None else x * k + prev * (1 - k)
        out.append(prev)
    return out


def wilder(v: list[float], n: int) -> Series:
    """k = 1/n. NOT ema(n) — see the module docstring.

    Seeded with SMA(n), the same convention as ema() above and as Pine's
    ta.rma. It matters more here than it does for an EMA: RMA decays at
    (1 - 1/n), which is slower than an EMA of the same length, so a bad seed
    takes longer to wash out of ATR and ADX.
    """
    out, prev = [], None
    for i, x in enumerate(v):
        if i < n - 1:
            out.append(None)
            continue
        prev = sum(v[:n]) / n if prev is None else (prev * (n - 1) + x) / n
        out.append(prev)
    return out


def wma(v: list[float], n: int) -> Series:
    out, den = [], n * (n + 1) / 2
    for i in range(len(v)):
        out.append(sum(v[i - n + 1 + j] * (j + 1) for j in range(n)) / den
                   if i >= n - 1 else None)
    return out


def hma(v: list[float], n: int) -> Series:
    """Hull: wma(2*wma(n/2) - wma(n), sqrt(n)) — fast and low-lag."""
    half, root = max(1, n // 2), max(1, int(n ** 0.5))
    a, b = wma(v, half), wma(v, n)
    raw = [None if (a[i] is None or b[i] is None) else 2 * a[i] - b[i]
           for i in range(len(v))]
    start = next((i for i, x in enumerate(raw) if x is not None), len(raw))
    tail = wma([x for x in raw[start:]], root)
    return [None] * start + tail


def stdev(v: list[float], n: int) -> Series:
    """Population standard deviation — the Bollinger convention."""
    out = []
    for i in range(len(v)):
        if i < n - 1:
            out.append(None)
            continue
        w = v[i - n + 1:i + 1]
        m = sum(w) / n
        out.append((sum((x - m) ** 2 for x in w) / n) ** 0.5)
    return out


def vwma(v: list[float], vol: list[float], n: int) -> Series:
    out = []
    for i in range(len(v)):
        if i < n - 1:
            out.append(None); continue
        num = sum(v[j] * vol[j] for j in range(i - n + 1, i + 1))
        den = sum(vol[j] for j in range(i - n + 1, i + 1))
        out.append(num / den if den else None)
    return out


# The names TradingView's "MA Type" dropdowns offer, and the one place they
# resolve. Basis MA Type, Oscillator MA Type, Signal Line MA Type and ATR
# Smoothing all come through here, so "EMA" cannot mean one thing on the
# Bollinger basis and another on the MACD line.
MA_TYPES = ("sma", "ema", "rma", "wma", "hma", "vwma")
MA_LABELS = {"sma": "SMA", "ema": "EMA", "rma": "SMMA (RMA)",
             "wma": "WMA", "hma": "HMA", "vwma": "VWMA"}


def _vol(rows: list[tuple]) -> list[float]:
    return [r[5] for r in rows]


def _ma(kind: str, v: list[float], n: int, vol: list[float] | None = None) -> Series:
    k = (kind or "sma").lower()
    if k == "sma":
        return sma(v, n)
    if k == "ema":
        return ema(v, n)
    if k in ("rma", "smma", "wilder"):
        return wilder(v, n)
    if k == "wma":
        return wma(v, n)
    if k == "hma":
        return hma(v, n)
    if k == "vwma":
        if vol is None or len(vol) != len(v):
            raise ValueError("VWMA needs a volume column the same length as the source")
        return vwma(v, vol, n)
    raise ValueError(f"unknown moving average '{kind}'")


def true_range(rows: list[tuple]) -> list[float]:
    out, prev_c = [], None
    for r in rows:
        _t, _o, h, l, c, _v = r
        out.append(h - l if prev_c is None
                   else max(h - l, abs(h - prev_c), abs(l - prev_c)))
        prev_c = c
    return out


def atr(rows: list[tuple], n: int = 14) -> Series:
    return wilder(true_range(rows), n)


def _last(s: Series):
    """The value AT the latest bar — not the last non-null anywhere.

    Scanning backwards for a non-null looks harmless until an indicator
    switches lines: Supertrend reported both an up-band and a down-band as
    current, the inactive one being a stale value from whenever the trend last
    flipped. A null at the latest bar is information ("this line is not active
    right now") and must survive.
    """
    return s[-1] if s else None


# ── the indicators ────────────────────────────────────────────────
# Each returns {line_name: Series}. Multi-line indicators name every line, so
# a caller never has to guess which one is "the" value.

def _f_sma(rows, n, src):    return {"sma": sma(_src(rows, src), n)}
def _f_ema(rows, n, src):    return {"ema": ema(_src(rows, src), n)}
def _f_wma(rows, n, src):    return {"wma": wma(_src(rows, src), n)}
def _f_hma(rows, n, src):    return {"hma": hma(_src(rows, src), n)}


def _f_vwma(rows, n, src):
    return {"vwma": vwma(_src(rows, src), _vol(rows), n)}


def _f_rma(rows, n, src):
    return {"rma": wilder(_src(rows, src), n)}


def _f_tema(rows, n, src):
    v = _src(rows, src)
    e1 = ema(v, n)
    s1 = next((i for i, x in enumerate(e1) if x is not None), len(v))
    e2t = ema([x for x in e1[s1:] if x is not None], n)
    e2 = [None] * s1 + e2t
    s2 = next((i for i, x in enumerate(e2) if x is not None), len(v))
    e3t = ema([x for x in e2[s2:] if x is not None], n)
    e3 = [None] * s2 + e3t
    return {"tema": [None if e3[i] is None else 3 * e1[i] - 3 * e2[i] + e3[i]
                     for i in range(len(v))]}


def _rolling_extreme(v, n, fn):
    return [None if i < n - 1 else fn(v[i - n + 1:i + 1])
            for i in range(len(v))]


def _f_ichimoku(rows, n, src, base_length=26, span_b_length=52,
                displacement=26):
    hi, lo = _src(rows, "high"), _src(rows, "low")
    def midpoint(length):
        hh, ll = _rolling_extreme(hi, length, max), _rolling_extreme(lo, length, min)
        return [None if hh[i] is None else (hh[i] + ll[i]) / 2
                for i in range(len(rows))]
    conversion = midpoint(n)
    base = midpoint(base_length)
    span_b = midpoint(span_b_length)
    span_a = [None if conversion[i] is None or base[i] is None
              else (conversion[i] + base[i]) / 2 for i in range(len(rows))]
    # Displacement belongs to presentation. The arrays remain aligned with the
    # bars so chat reads the value calculated at the latest bar; the HTTP
    # adapter shifts their timestamps and can project the cloud into the future.
    return {"conversion": conversion, "base": base, "senkou_a": span_a,
            "senkou_b": span_b, "chikou": _src(rows, "close")}


def _bucket_key(ts, timeframe, tz_offset):
    g = _time.gmtime(ts + tz_offset)
    if timeframe == "week":
        return (g.tm_year, g.tm_yday - g.tm_wday)
    if timeframe == "month":
        return (g.tm_year, g.tm_mon)
    return (g.tm_year, g.tm_yday)


def _f_pivots(rows, n, src, pivot_type="traditional", timeframe="auto",
              tz_offset=19800):
    if timeframe == "auto":
        gaps = sorted(rows[i][0] - rows[i - 1][0] for i in range(1, len(rows)))
        step = gaps[len(gaps) // 2]
        timeframe = "day" if step <= 15 * 60 else ("week" if step < 86400 else "month")
    groups, cur, key = [], [], None
    for i, r in enumerate(rows):
        k = _bucket_key(r[0], timeframe, tz_offset)
        if key is not None and k != key:
            groups.append(cur); cur = []
        key = k; cur.append(i)
    if cur: groups.append(cur)
    names = ["pivot", "r1", "s1", "r2", "s2", "r3", "s3", "r4", "s4"]
    out = {k: [None] * len(rows) for k in names}
    out.update({"cpr_top": [None] * len(rows), "cpr_bottom": [None] * len(rows)})
    for gi in range(1, len(groups)):
        prev, here = groups[gi - 1], groups[gi]
        h = max(rows[i][2] for i in prev); l = min(rows[i][3] for i in prev)
        c = rows[prev[-1]][4]; rng = h - l; p = (h + l + c) / 3
        vals = {"pivot": p}
        if pivot_type == "fibonacci":
            for j, m in enumerate((.382, .618, 1.0), 1):
                vals[f"r{j}"] = p + m * rng; vals[f"s{j}"] = p - m * rng
        elif pivot_type == "camarilla":
            for j, d in enumerate((12, 6, 4, 2), 1):
                vals[f"r{j}"] = c + 1.1 * rng / d; vals[f"s{j}"] = c - 1.1 * rng / d
        else:
            vals.update(r1=2*p-l, s1=2*p-h, r2=p+rng, s2=p-rng,
                        r3=2*p+h-2*l, s3=2*p-2*h+l,
                        r4=3*p+h-3*l, s4=3*p-3*h+l)
        bc = (h + l) / 2
        vals["cpr_top"], vals["cpr_bottom"] = max(p, 2*p-bc), min(p, 2*p-bc)
        for i in here:
            for name, value in vals.items(): out[name][i] = value
    return out


def _f_atr(rows, n, src, smoothing="rma"):
    """TradingView's ATR takes a Smoothing choice; Wilder (RMA) is its default
    and the only one the textbook formula uses."""
    return {"atr": _ma(smoothing, true_range(rows), n, _vol(rows))}


def _f_dema(rows, n, src):
    v = _src(rows, src)
    e1 = ema(v, n)
    e2 = ema([x for x in e1 if x is not None], n)
    pad = len(v) - len(e2)
    e2 = [None] * pad + e2
    return {"dema": [None if (e1[i] is None or e2[i] is None) else 2 * e1[i] - e2[i]
                     for i in range(len(v))]}


def _f_rsi(rows, n, src):
    v = _src(rows, src)
    out: Series = [None]
    ag = al = 0.0
    for i in range(1, len(v)):
        ch = v[i] - v[i - 1]
        g, l = max(ch, 0.0), max(-ch, 0.0)
        if i <= n:
            ag += g / n
            al += l / n
        else:
            ag = (ag * (n - 1) + g) / n
            al = (al * (n - 1) + l) / n
        out.append(None if i < n else (100.0 if al == 0 else 100 - 100 / (1 + ag / al)))
    return {"rsi": out}


def _f_macd(rows, n, src, fast=12, slow=26, signal=9,
            osc_ma="ema", signal_ma="ema"):
    v = _src(rows, src)
    vol = _vol(rows)
    ef, es = _ma(osc_ma, v, fast, vol), _ma(osc_ma, v, slow, vol)
    line = [None if (ef[i] is None or es[i] is None) else ef[i] - es[i]
            for i in range(len(v))]
    start = next((i for i, x in enumerate(line) if x is not None), len(line))
    sig_tail = _ma(signal_ma, [x for x in line[start:]], signal, vol[start:])
    sig = [None] * start + sig_tail
    hist = [None if (line[i] is None or sig[i] is None) else line[i] - sig[i]
            for i in range(len(v))]
    return {"macd": line, "signal": sig, "histogram": hist}


def _f_bbands(rows, n, src, mult=2.0, ma_type="sma"):
    v = _src(rows, src)
    mid, sd = _ma(ma_type, v, n, _vol(rows)), stdev(v, n)
    # the basis and the deviation warm up at different bars once the basis is
    # something other than an SMA, so both have to be present for a band
    ok = [mid[i] is not None and sd[i] is not None for i in range(len(v))]
    up = [mid[i] + mult * sd[i] if ok[i] else None for i in range(len(v))]
    lo = [mid[i] - mult * sd[i] if ok[i] else None for i in range(len(v))]
    # %B locates price IN the bands; bandwidth is the squeeze measure
    pctb = [None if (up[i] is None or up[i] == lo[i]) else (v[i] - lo[i]) / (up[i] - lo[i])
            for i in range(len(v))]
    bw = [None if (not ok[i] or not mid[i]) else (up[i] - lo[i]) / mid[i]
          for i in range(len(v))]
    return {"upper": up, "middle": mid, "lower": lo, "percent_b": pctb,
            "bandwidth": bw}


def _f_keltner(rows, n, src, mult=2.0, use_ema=True, bands_style="atr",
               atr_length=10):
    """TradingView's Keltner: the basis MA can be simple or exponential, and
    the band width comes from ATR, plain true range, or the bar's range."""
    v = _src(rows, src)
    mid = ema(v, n) if use_ema else sma(v, n)
    style = (bands_style or "atr").lower()
    if style in ("tr", "true_range"):
        band = true_range(rows)
    elif style == "range":
        band = [r[2] - r[3] for r in rows]
    else:
        band = atr(rows, atr_length or n)
    up = [None if (mid[i] is None or band[i] is None) else mid[i] + mult * band[i]
          for i in range(len(rows))]
    lo = [None if (mid[i] is None or band[i] is None) else mid[i] - mult * band[i]
          for i in range(len(rows))]
    return {"upper": up, "middle": mid, "lower": lo}


def _f_donchian(rows, n, src):
    hi, lo, mid = [], [], []
    for i in range(len(rows)):
        if i < n - 1:
            hi.append(None); lo.append(None); mid.append(None); continue
        w = rows[i - n + 1:i + 1]
        h, l = max(r[2] for r in w), min(r[3] for r in w)
        hi.append(h); lo.append(l); mid.append((h + l) / 2)
    return {"upper": hi, "middle": mid, "lower": lo}


def _f_stoch(rows, n, src, smooth_k=3, smooth_d=3):
    k_raw = []
    for i in range(len(rows)):
        if i < n - 1:
            k_raw.append(None); continue
        w = rows[i - n + 1:i + 1]
        hh, ll = max(r[2] for r in w), min(r[3] for r in w)
        k_raw.append(50.0 if hh == ll else (rows[i][4] - ll) / (hh - ll) * 100)
    start = next((i for i, x in enumerate(k_raw) if x is not None), len(k_raw))
    k = [None] * start + sma([x for x in k_raw[start:]], smooth_k)
    s2 = next((i for i, x in enumerate(k) if x is not None), len(k))
    d = [None] * s2 + sma([x for x in k[s2:]], smooth_d)
    return {"k": k, "d": d}


def _f_stochrsi(rows, n, src, smooth_k=3, smooth_d=3, rsi_length=14):
    """`n` is the STOCHASTIC window; the RSI it runs on has its own length,
    the way TradingView splits them. They were the same number before, which
    made 'Stoch RSI 14' quietly unable to express TV's default pairing."""
    r = _f_rsi(rows, rsi_length or n, src)["rsi"]
    out = []
    for i in range(len(r)):
        w = [x for x in r[max(0, i - n + 1):i + 1] if x is not None]
        if r[i] is None or len(w) < n:
            out.append(None); continue
        hh, ll = max(w), min(w)
        out.append(50.0 if hh == ll else (r[i] - ll) / (hh - ll) * 100)
    start = next((i for i, x in enumerate(out) if x is not None), len(out))
    k = [None] * start + sma([x for x in out[start:]], smooth_k)
    s2 = next((i for i, x in enumerate(k) if x is not None), len(k))
    d = [None] * s2 + sma([x for x in k[s2:]], smooth_d)
    return {"k": k, "d": d}


def _f_adx(rows, n, src, di_length=14):
    """Double-smoothed: DI from Wilder(DM)/Wilder(TR) over `di_length`, then
    ADX = Wilder(DX) over `n`. TradingView calls the two ADX Smoothing and DI
    Length and lets them differ; they were locked together here."""
    plus_dm, minus_dm = [], []
    for i, r in enumerate(rows):
        if i == 0:
            plus_dm.append(0.0); minus_dm.append(0.0); continue
        up = r[2] - rows[i - 1][2]
        dn = rows[i - 1][3] - r[3]
        plus_dm.append(up if (up > dn and up > 0) else 0.0)
        minus_dm.append(dn if (dn > up and dn > 0) else 0.0)
    di = di_length or n
    tr_s, p_s, m_s = wilder(true_range(rows), di), wilder(plus_dm, di), wilder(minus_dm, di)
    pdi, mdi, dx = [], [], []
    for i in range(len(rows)):
        if tr_s[i] is None or not tr_s[i]:
            pdi.append(None); mdi.append(None); dx.append(None); continue
        p = 100 * p_s[i] / tr_s[i]
        m = 100 * m_s[i] / tr_s[i]
        pdi.append(p); mdi.append(m)
        dx.append(0.0 if (p + m) == 0 else 100 * abs(p - m) / (p + m))
    start = next((i for i, x in enumerate(dx) if x is not None), len(dx))
    # A bar whose smoothed true range is zero leaves DX undefined, and at a
    # short DI Length that happens mid-series rather than only during warmup.
    # Undefined directional movement IS zero movement (Pine's nz()), so the
    # gap is filled rather than carried into the smoother, which would poison
    # every value after it.
    adx = [None] * start + wilder([0.0 if x is None else x for x in dx[start:]], n)
    return {"adx": adx, "plus_di": pdi, "minus_di": mdi}


def _f_cci(rows, n, src):
    # `src` defaults to hlc3 through the spec's src_default, so the textbook
    # typical-price CCI is still what you get without touching anything
    tp = _src(rows, src)
    m = sma(tp, n)
    out = []
    for i in range(len(tp)):
        if m[i] is None:
            out.append(None); continue
        w = tp[i - n + 1:i + 1]
        md = sum(abs(x - m[i]) for x in w) / n
        out.append(None if not md else (tp[i] - m[i]) / (0.015 * md))
    return {"cci": out}


def _f_willr(rows, n, src):
    v = _src(rows, src)          # the value located in the range; close by default
    out = []
    for i in range(len(rows)):
        if i < n - 1:
            out.append(None); continue
        w = rows[i - n + 1:i + 1]
        hh, ll = max(r[2] for r in w), min(r[3] for r in w)
        out.append(None if hh == ll else (hh - v[i]) / (hh - ll) * -100)
    return {"williams_r": out}


def _f_roc(rows, n, src):
    v = _src(rows, src)
    return {"roc": [None if (i < n or not v[i - n]) else (v[i] - v[i - n]) / v[i - n] * 100
                    for i in range(len(v))]}


def _f_obv(rows, n, src):
    out, cum = [], 0.0
    for i, r in enumerate(rows):
        if i:
            cum += r[5] if r[4] > rows[i - 1][4] else (-r[5] if r[4] < rows[i - 1][4] else 0)
        out.append(cum)
    return {"obv": out}


def _f_ad(rows, n, src):
    out, cum = [], 0.0
    for r in rows:
        _t, _o, h, l, c, v = r
        cum += 0.0 if h == l else ((c - l) - (h - c)) / (h - l) * v
        out.append(cum)
    return {"ad": out}


def _f_cmf(rows, n, src):
    mfv = [0.0 if r[2] == r[3] else ((r[4] - r[3]) - (r[2] - r[4])) / (r[2] - r[3]) * r[5]
           for r in rows]
    vol = [r[5] for r in rows]
    out = []
    for i in range(len(rows)):
        if i < n - 1:
            out.append(None); continue
        sv = sum(vol[i - n + 1:i + 1])
        out.append(None if not sv else sum(mfv[i - n + 1:i + 1]) / sv)
    return {"cmf": out}


def _f_mfi(rows, n, src):
    tp = _src(rows, src)         # hlc3 by default, via the spec's src_default
    out: Series = [None]
    for i in range(1, len(rows)):
        if i < n:
            out.append(None); continue
        pos = neg = 0.0
        for j in range(i - n + 1, i + 1):
            flow = tp[j] * rows[j][5]
            if tp[j] > tp[j - 1]:
                pos += flow
            elif tp[j] < tp[j - 1]:
                neg += flow
        out.append(100.0 if neg == 0 else 100 - 100 / (1 + pos / neg))
    return {"mfi": out}


def _f_vwap(rows, n, src, anchor="session", session_seconds=86400,
            tz_offset=19800):
    """VWAP reset on TradingView's Anchor Period — the trading day, the week,
    or the month. Anchoring to a chosen BAR is anchored_vwap's job.

    `tz_offset` is the instrument's own UTC offset in seconds, and it is a
    parameter rather than a constant because the session boundary is a
    property of the INSTRUMENT, not of this codebase. It was hardcoded to
    +05:30, which is right for NSE and wrong for everything else: a BTCUSD
    session VWAP reset at 18:30 UTC while TradingView reset it at 00:00, so
    every intraday value on a crypto chart was anchored to the wrong session.
    The default stays 19800 so an Indian symbol is unchanged when the caller
    omits it; the chart sends `Sym.of(symbol).tz`, which is already the
    number it uses to place the candles.
    """
    a = (anchor or "session").lower()

    def bucket(ts: int):
        d = (ts + tz_offset) // session_seconds
        if a == "week":
            # epoch day 0 was a Thursday; shift so a bucket breaks on Monday
            return (d + 3) // 7
        if a == "month":
            g = _time.gmtime(ts + tz_offset)
            return (g.tm_year, g.tm_mon)
        return d

    out, key, pv, vol = [], object(), 0.0, 0.0
    for r in rows:
        b = bucket(r[0])
        if b != key:
            key, pv, vol = b, 0.0, 0.0
        tp = (r[2] + r[3] + r[4]) / 3
        v = r[5] or 1
        pv += tp * v
        vol += v
        out.append(pv / vol if vol else None)
    return {"vwap": out}


def _f_anchored_vwap(rows, n, src, anchor_index=0):
    """VWAP from one chosen bar forward — pairs with the chart's pin."""
    out, pv, vol = [], 0.0, 0.0
    for i, r in enumerate(rows):
        if i < anchor_index:
            out.append(None); continue
        tp = (r[2] + r[3] + r[4]) / 3
        v = r[5] or 1
        pv += tp * v
        vol += v
        out.append(pv / vol if vol else None)
    return {"anchored_vwap": out}


def _f_supertrend(rows, n, src, mult=3.0):
    """Ratcheting bands — must run forward over the whole series."""
    a = atr(rows, n)
    hl2 = _src(rows, "hl2")
    up: Series = [None] * len(rows)
    dn: Series = [None] * len(rows)
    trend: Series = [None] * len(rows)
    fu = fl = None
    dirn = 1
    for i, r in enumerate(rows):
        if a[i] is None:
            continue
        bu, bl = hl2[i] + mult * a[i], hl2[i] - mult * a[i]
        c, pc = r[4], rows[i - 1][4] if i else r[4]
        fu = bu if (fu is None or bu < fu or pc > fu) else fu
        fl = bl if (fl is None or bl > fl or pc < fl) else fl
        if fu is not None and c > fu:
            dirn = 1
        elif fl is not None and c < fl:
            dirn = -1
        trend[i] = dirn
        up[i] = fl if dirn == 1 else None
        dn[i] = fu if dirn == -1 else None
    return {"supertrend_up": up, "supertrend_down": dn, "direction": trend}


def _f_psar(rows, n, src, start=0.02, step=0.02, cap=0.20):
    """TradingView splits the acceleration factor into Start (where it begins
    after a flip) and Increment (how much each new extreme adds). They were
    one number here, so TV's defaults could not be reproduced exactly."""
    out: Series = [None] * len(rows)
    if len(rows) < 3:
        return {"psar": out}
    bull = rows[1][4] > rows[0][4]
    af, sar = start, rows[0][3] if bull else rows[0][2]
    ep = rows[0][2] if bull else rows[0][3]
    for i in range(1, len(rows)):
        h, l = rows[i][2], rows[i][3]
        sar = sar + af * (ep - sar)
        if bull:
            sar = min(sar, rows[i - 1][3], rows[max(0, i - 2)][3])
            if l < sar:
                bull, sar, ep, af = False, ep, l, start
            elif h > ep:
                ep, af = h, min(cap, af + step)
        else:
            sar = max(sar, rows[i - 1][2], rows[max(0, i - 2)][2])
            if h > sar:
                bull, sar, ep, af = True, ep, h, start
            elif l < ep:
                ep, af = l, min(cap, af + step)
        out[i] = sar
    return {"psar": out}


def _f_aroon(rows, n, src):
    up, dn = [], []
    for i in range(len(rows)):
        if i < n:
            up.append(None); dn.append(None); continue
        w = rows[i - n:i + 1]
        # Aroon measures bars since the MOST RECENT period high/low. Iterating
        # newest-first makes max/min keep the latest occurrence when an
        # extreme is tied; the default oldest-first scan reported 0 instead
        # of 100 on a flat window.
        newest_first = range(len(w) - 1, -1, -1)
        hi = max(newest_first, key=lambda k: w[k][2])
        newest_first = range(len(w) - 1, -1, -1)
        lo = min(newest_first, key=lambda k: w[k][3])
        up.append(hi / n * 100)
        dn.append(lo / n * 100)
    return {"aroon_up": up, "aroon_down": dn,
            "oscillator": [None if (up[i] is None) else up[i] - dn[i]
                           for i in range(len(rows))]}


def _f_percent_b(rows, n, src, mult=2.0, ma_type="sma"):
    return {"percent_b": _f_bbands(rows, n, src, mult, ma_type)["percent_b"]}


def _f_bandwidth(rows, n, src, mult=2.0, ma_type="sma"):
    return {"bandwidth": _f_bbands(rows, n, src, mult, ma_type)["bandwidth"]}


def _f_awesome(rows, n, src, slow=34):
    v = _src(rows, "hl2"); a, b = sma(v, n), sma(v, slow)
    return {"awesome": [None if a[i] is None or b[i] is None else a[i] - b[i]
                        for i in range(len(v))]}


def _f_chaikin_osc(rows, n, src, slow=10):
    ad = _f_ad(rows, n, src)["ad"]; fast_ma, slow_ma = ema(ad, n), ema(ad, slow)
    return {"chaikin_osc": [None if fast_ma[i] is None or slow_ma[i] is None
                            else fast_ma[i] - slow_ma[i] for i in range(len(rows))]}


def _f_vortex(rows, n, src):
    tr = true_range(rows); vp = [0.0]; vm = [0.0]
    for i in range(1, len(rows)):
        vp.append(abs(rows[i][2] - rows[i-1][3]))
        vm.append(abs(rows[i][3] - rows[i-1][2]))
    plus, minus = [], []
    for i in range(len(rows)):
        if i < n - 1: plus.append(None); minus.append(None); continue
        den = sum(tr[i-n+1:i+1])
        plus.append(sum(vp[i-n+1:i+1]) / den if den else None)
        minus.append(sum(vm[i-n+1:i+1]) / den if den else None)
    return {"vi_plus": plus, "vi_minus": minus}


def _f_ultimate(rows, n, src, middle=14, long=28):
    bp, tr = [], []
    for i, r in enumerate(rows):
        pc = rows[i-1][4] if i else r[4]
        bp.append(r[4] - min(r[3], pc)); tr.append(max(r[2], pc) - min(r[3], pc))
    out = []
    for i in range(len(rows)):
        if i < long - 1: out.append(None); continue
        av = []
        for p in (n, middle, long):
            den = sum(tr[i-p+1:i+1]); av.append(sum(bp[i-p+1:i+1]) / den if den else 0)
        out.append(100 * (4*av[0] + 2*av[1] + av[2]) / 7)
    return {"ultimate": out}


def _ema_nullable(v, n):
    start = next((i for i, x in enumerate(v) if x is not None), len(v))
    return [None] * start + ema([0.0 if x is None else x for x in v[start:]], n)


def _sma_nullable(v, n):
    start = next((i for i, x in enumerate(v) if x is not None), len(v))
    return [None] * start + sma([0.0 if x is None else x for x in v[start:]], n)


def _f_trix(rows, n, src, signal=9):
    v = _src(rows, src); e1 = ema(v, n); e2 = _ema_nullable(e1, n); e3 = _ema_nullable(e2, n)
    line = [None if i == 0 or e3[i] is None or e3[i-1] in (None, 0)
            else 100 * (e3[i] - e3[i-1]) / e3[i-1] for i in range(len(v))]
    return {"trix": line, "signal": _ema_nullable(line, signal)}


def _roc_series(v, n):
    return [None if i < n or not v[i-n] else 100 * (v[i] - v[i-n]) / v[i-n]
            for i in range(len(v))]


def _f_kst(rows, n, src, roc2=15, roc3=20, roc4=30,
           sma1=10, sma2=10, sma3=10, sma4=15, signal=9):
    v = _src(rows, src)
    parts = [_sma_nullable(_roc_series(v, r), s)
             for r, s in ((n,sma1),(roc2,sma2),(roc3,sma3),(roc4,sma4))]
    line = [None if any(p[i] is None for p in parts)
            else parts[0][i] + 2*parts[1][i] + 3*parts[2][i] + 4*parts[3][i]
            for i in range(len(v))]
    return {"kst": line, "signal": _sma_nullable(line, signal)}


def _f_dpo(rows, n, src):
    v = _src(rows, src); basis = sma(v, n); shift = n // 2 + 1
    return {"dpo": [None if i < shift or basis[i] is None
                    else v[i-shift] - basis[i] for i in range(len(v))]}


def _f_force(rows, n, src):
    v = _src(rows, src); raw = [None] + [(v[i]-v[i-1]) * rows[i][5]
                                         for i in range(1, len(rows))]
    return {"force": _ema_nullable(raw, n)}


def _f_eom(rows, n, src, divisor=10000.0):
    raw = [None]
    for i in range(1, len(rows)):
        distance = ((rows[i][2]+rows[i][3]) - (rows[i-1][2]+rows[i-1][3])) / 2
        box = (rows[i][5] / divisor) / (rows[i][2]-rows[i][3]) if rows[i][2] != rows[i][3] else 0
        raw.append(distance / box if box else 0)
    return {"eom": _sma_nullable(raw, n)}


def _f_chop(rows, n, src):
    tr = true_range(rows); out = []
    for i in range(len(rows)):
        if i < n-1: out.append(None); continue
        rng = max(r[2] for r in rows[i-n+1:i+1]) - min(r[3] for r in rows[i-n+1:i+1])
        out.append(None if not rng else 100 * _math.log10(sum(tr[i-n+1:i+1])/rng) / _math.log10(n))
    return {"choppiness": out}


def _f_fisher(rows, n, src):
    hl2 = _src(rows, "hl2"); value, fish = [], []
    prev_v = prev_f = 0.0
    for i, x in enumerate(hl2):
        if i < n-1: value.append(None); fish.append(None); continue
        w = hl2[i-n+1:i+1]; hi, lo = max(w), min(w)
        pos = 0 if hi == lo else 2*((x-lo)/(hi-lo)-.5)
        prev_v = max(-.999, min(.999, .33*pos + .67*prev_v))
        f = .5*_math.log((1+prev_v)/(1-prev_v)) + .5*prev_f
        value.append(prev_v); fish.append(f); prev_f = f
    return {"fisher": fish, "trigger": [None] + fish[:-1]}


def _f_rvi(rows, n, src):
    num, den = [], []
    for i in range(len(rows)):
        if i < 3: num.append(None); den.append(None); continue
        num.append(((rows[i][4]-rows[i][1]) + 2*(rows[i-1][4]-rows[i-1][1]) +
                    2*(rows[i-2][4]-rows[i-2][1]) + rows[i-3][4]-rows[i-3][1]) / 6)
        den.append(((rows[i][2]-rows[i][3]) + 2*(rows[i-1][2]-rows[i-1][3]) +
                    2*(rows[i-2][2]-rows[i-2][3]) + rows[i-3][2]-rows[i-3][3]) / 6)
    sn, sd = _sma_nullable(num, n), _sma_nullable(den, n)
    line = [None if sn[i] is None or not sd[i] else sn[i]/sd[i] for i in range(len(rows))]
    signal = [None if i < 3 or any(line[j] is None for j in range(i-3,i+1))
              else (line[i]+2*line[i-1]+2*line[i-2]+line[i-3])/6 for i in range(len(rows))]
    return {"rvi": line, "signal": signal}


def _percent_rank(v, n):
    out = []
    for i, x in enumerate(v):
        if i < n or x is None: out.append(None); continue
        w = [z for z in v[i-n:i] if z is not None]
        out.append(100 * sum(z < x for z in w) / len(w) if w else None)
    return out


def _rsi_values(v, n):
    fake = [(i, x, x, x, x, 1) for i, x in enumerate(v)]
    return _f_rsi(fake, n, "close")["rsi"]


def _f_connors(rows, n, src, streak_length=2, rank_length=100):
    v = _src(rows, src); streak = [0.0]
    for i in range(1, len(v)):
        streak.append((streak[-1]+1 if v[i] > v[i-1] else streak[-1]-1 if v[i] < v[i-1] else 0))
    a, b = _rsi_values(v, n), _rsi_values(streak, streak_length)
    c = _percent_rank(_roc_series(v, 1), rank_length)
    return {"connors_rsi": [None if a[i] is None or b[i] is None or c[i] is None
                            else (a[i]+b[i]+c[i])/3 for i in range(len(v))]}


def _f_kama(rows, n, src, fast=2, slow=30):
    v = _src(rows, src); out = [None] * len(v); fast_sc=2/(fast+1); slow_sc=2/(slow+1)
    if len(v) > n: out[n-1] = sum(v[:n])/n
    for i in range(n, len(v)):
        change=abs(v[i]-v[i-n]); volatility=sum(abs(v[j]-v[j-1]) for j in range(i-n+1,i+1))
        er=change/volatility if volatility else 0; sc=(er*(fast_sc-slow_sc)+slow_sc)**2
        out[i]=out[i-1]+sc*(v[i]-out[i-1])
    return {"kama": out}


def _f_alma(rows, n, src, offset=.85, sigma=6.0):
    v=_src(rows,src); m=offset*(n-1); s=n/sigma; weights=[_math.exp(-((i-m)**2)/(2*s*s)) for i in range(n)]; den=sum(weights)
    return {"alma": [None if i<n-1 else sum(v[i-n+1+j]*weights[j] for j in range(n))/den for i in range(len(v))]}


def _f_lsma(rows, n, src, offset=0):
    v=_src(rows,src); out=[]; sx=n*(n-1)/2; sxx=(n-1)*n*(2*n-1)/6; den=n*sxx-sx*sx
    for i in range(len(v)):
        if i<n-1: out.append(None); continue
        w=v[i-n+1:i+1]; sy=sum(w); sxy=sum(j*x for j,x in enumerate(w)); slope=(n*sxy-sx*sy)/den
        intercept=(sy-slope*sx)/n; out.append(intercept+slope*(n-1-offset))
    return {"lsma": out}


# ── registry ──────────────────────────────────────────────────────
# pane: "overlay" sits on price, "own" gets a sub-pane. `formula` is returned
# with every result so a reply can state what it computed.
SPECS: dict = {
    "sma":       dict(fn=_f_sma, period=20, pane="overlay", group="trend",
                      formula="mean of the last n values of the chosen source"),
    "ema":       dict(fn=_f_ema, period=21, pane="overlay", group="trend",
                      formula="EMA_i = P_i*k + EMA_(i-1)*(1-k), k = 2/(n+1), seeded from the first value"),
    "wma":       dict(fn=_f_wma, period=20, pane="overlay", group="trend",
                      formula="linearly weighted mean, weight j on the j-th most recent of n"),
    "hma":       dict(fn=_f_hma, period=21, pane="overlay", group="trend",
                      formula="WMA(2*WMA(n/2) - WMA(n), sqrt(n))"),
    "dema":      dict(fn=_f_dema, period=21, pane="overlay", group="trend",
                      formula="2*EMA(n) - EMA(EMA(n))"),
    "tema":      dict(fn=_f_tema, period=9, pane="overlay", group="trend",
                      formula="3*EMA(n) - 3*EMA(EMA(n)) + EMA(EMA(EMA(n)))"),
    "vwma":      dict(fn=_f_vwma, period=20, pane="overlay", group="volume",
                      formula="sum(source*volume,n) / sum(volume,n)"),
    "rma":       dict(fn=_f_rma, period=14, pane="overlay", group="trend",
                      formula="Wilder smoothed moving average, alpha=1/n, seeded with SMA(n)"),
    "kama":      dict(fn=_f_kama, period=10, pane="overlay", group="trend",
                      formula="Kaufman adaptive MA using n-bar efficiency ratio and fast/slow smoothing constants"),
    "alma":      dict(fn=_f_alma, period=9, pane="overlay", group="trend",
                      formula="Gaussian weighted moving average with offset and sigma"),
    "lsma":      dict(fn=_f_lsma, period=25, pane="overlay", group="trend",
                      formula="least-squares regression value at the last bar, shifted by offset"),
    "ichimoku":  dict(fn=_f_ichimoku, period=9, pane="overlay", group="trend",
                      formula="Tenkan midpoint(9), Kijun midpoint(26), Span A midpoint of both, Span B midpoint(52); cloud leads and Chikou lags by displacement"),
    "pivots":    dict(fn=_f_pivots, period=0, pane="overlay", group="levels",
                      formula="prior pivot-period OHLC levels plus CPR: pivot=(H+L+C)/3, BC=(H+L)/2, TC=2*pivot-BC"),
    "bbands":    dict(fn=_f_bbands, period=20, pane="overlay", group="volatility",
                      formula="SMA(n) +/- mult * POPULATION stdev(n) of the source; "
                              "percent_b = (P-lower)/(upper-lower); bandwidth = (upper-lower)/middle"),
    "keltner":   dict(fn=_f_keltner, period=20, pane="overlay", group="volatility",
                      formula="EMA(n) +/- mult * ATR(n)"),
    "donchian":  dict(fn=_f_donchian, period=20, pane="overlay", group="volatility",
                      formula="highest high and lowest low of the last n bars"),
    "vwap":      dict(fn=_f_vwap, period=0, pane="overlay", group="volume",
                      formula="sum(typical price * volume) / sum(volume), typical = (H+L+C)/3, "
                              "reset at each IST session start"),
    "anchored_vwap": dict(fn=_f_anchored_vwap, period=0, pane="overlay", group="volume",
                          formula="same as VWAP but accumulated from one chosen anchor bar forward"),
    "supertrend": dict(fn=_f_supertrend, period=10, pane="overlay", group="trend",
                       formula="bands (H+L)/2 +/- mult*ATR(n), ratcheted forward; direction flips "
                               "when a close crosses the active band"),
    "psar":      dict(fn=_f_psar, period=0, pane="overlay", group="trend",
                      formula="parabolic SAR, acceleration 0.02 stepping to 0.20"),
    "rsi":       dict(fn=_f_rsi, period=14, pane="own", group="momentum", bounds=(0, 100),
                      formula="100 - 100/(1+RS), RS = Wilder avg gain / Wilder avg loss over n (k = 1/n)"),
    "macd":      dict(fn=_f_macd, period=0, pane="own", group="momentum",
                      formula="EMA(fast) - EMA(slow); signal = EMA(signal) of that; histogram = macd - signal"),
    "stoch":     dict(fn=_f_stoch, period=14, pane="own", group="momentum", bounds=(0, 100),
                      formula="%K = 100*(C - lowest low n)/(highest high n - lowest low n), "
                              "smoothed by SMA(smooth_k); %D = SMA(smooth_d) of %K"),
    "stochrsi":  dict(fn=_f_stochrsi, period=14, pane="own", group="momentum", bounds=(0, 100),
                      formula="stochastic of RSI(n) over its own n-window, same smoothing as stoch"),
    "adx":       dict(fn=_f_adx, period=14, pane="own", group="trend", bounds=(0, 100),
                      formula="+DI/-DI = 100*Wilder(+DM or -DM)/Wilder(TR); DX = 100*|+DI - -DI|/(+DI + -DI); "
                              "ADX = Wilder(DX) — double-smoothed"),
    "cci":       dict(fn=_f_cci, period=20, pane="own", group="momentum",
                      src_default="hlc3",
                      formula="(typical - SMA(typical, n)) / (0.015 * mean absolute deviation)"),
    "williams_r": dict(fn=_f_willr, period=14, pane="own", group="momentum", bounds=(-100, 0),
                       formula="-100 * (highest high n - C) / (highest high n - lowest low n)"),
    "roc":       dict(fn=_f_roc, period=9, pane="own", group="momentum",
                      formula="100 * (P - P n bars ago) / P n bars ago"),
    "atr":       dict(fn=_f_atr, period=14, pane="own", group="volatility",
                      formula="Wilder(n) of true range, TR = max(H-L, |H-C_prev|, |L-C_prev|)"),
    "obv":       dict(fn=_f_obv, period=0, pane="own", group="volume",
                      formula="running sum of +volume when close rises, -volume when it falls"),
    "ad":        dict(fn=_f_ad, period=0, pane="own", group="volume",
                      formula="running sum of volume * ((C-L)-(H-C))/(H-L)"),
    "cmf":       dict(fn=_f_cmf, period=20, pane="own", group="volume",
                      formula="n-sum of money flow volume / n-sum of volume"),
    "mfi":       dict(fn=_f_mfi, period=14, pane="own", group="volume", bounds=(0, 100),
                      src_default="hlc3",
                      formula="RSI applied to typical price * volume, split by whether typical price rose"),
    # 14, not Chande's original 25: TradingView's built-in defaults to 14 and
    # that is the line a user has already seen on every other chart.
    "aroon":     dict(fn=_f_aroon, period=14, pane="own", group="trend", bounds=(0, 100),
                      formula="up = 100*(bars since the n-bar high)/n, down likewise for the low"),
    "percent_b": dict(fn=_f_percent_b, period=20, pane="own", group="volatility",
                      formula="(source-lower Bollinger band)/(upper-lower); 0 is lower band and 1 is upper band"),
    "bandwidth": dict(fn=_f_bandwidth, period=20, pane="own", group="volatility",
                      formula="(upper Bollinger band-lower band)/basis"),
    "awesome": dict(fn=_f_awesome, period=5, pane="own", group="momentum",
                    formula="SMA(5) of HL2 minus SMA(34) of HL2"),
    "chaikin_osc": dict(fn=_f_chaikin_osc, period=3, pane="own", group="volume",
                    formula="EMA(3) of Accumulation/Distribution minus EMA(10) of A/D"),
    "vortex": dict(fn=_f_vortex, period=14, pane="own", group="trend",
                    formula="VI+ = sum(|H-Lprev|,n)/sum(TR,n); VI- = sum(|L-Hprev|,n)/sum(TR,n)"),
    "ultimate": dict(fn=_f_ultimate, period=7, pane="own", group="momentum", bounds=(0,100),
                    formula="100*(4*BP/TR(7)+2*BP/TR(14)+BP/TR(28))/7"),
    "trix": dict(fn=_f_trix, period=18, pane="own", group="momentum",
                    formula="one-bar percent change of a triple EMA(n), with EMA signal"),
    "kst": dict(fn=_f_kst, period=10, pane="own", group="momentum",
                    formula="SMA-smoothed ROC(10)+2*ROC(15)+3*ROC(20)+4*ROC(30), with signal SMA"),
    "dpo": dict(fn=_f_dpo, period=21, pane="own", group="momentum",
                    formula="source shifted back n/2+1 bars minus SMA(n) at that bar"),
    "force": dict(fn=_f_force, period=13, pane="own", group="volume",
                    formula="EMA(n) of (close-close_prev)*volume"),
    "eom": dict(fn=_f_eom, period=14, pane="own", group="volume",
                    formula="SMA(n) of midpoint movement divided by volume/range box ratio"),
    "choppiness": dict(fn=_f_chop, period=14, pane="own", group="volatility", bounds=(0,100),
                    formula="100*log10(sum(TR,n)/(highest high-lowest low))/log10(n)"),
    "fisher": dict(fn=_f_fisher, period=9, pane="own", group="momentum",
                    formula="Fisher transform of normalized HL2, recursively smoothed, with one-bar trigger"),
    "rvi": dict(fn=_f_rvi, period=10, pane="own", group="momentum",
                    formula="SMA of symmetrically weighted close-open divided by SMA of high-low, with 4-bar signal"),
    "connors_rsi": dict(fn=_f_connors, period=3, pane="own", group="momentum", bounds=(0,100),
                    formula="mean of RSI(3), RSI(2) of up/down streak, and 100-bar percent rank of one-bar return"),
}

NAMES = tuple(sorted(SPECS))

# Which indicators actually take a band-width multiplier — derived from the
# functions' own signatures so it can never drift from them. Forwarding
# `mult` to the rest raised TypeError and burned the whole tool call.
import inspect as _inspect
MULT_OK = frozenset(
    k for k, v in SPECS.items()
    if "mult" in _inspect.signature(v["fn"]).parameters)

# ── the user-editable inputs of each indicator ────────────────────
# The settings dialog builds its Inputs tab from this, and it is derived from
# the functions' own signatures (the MULT_OK trick, generalised) so the dialog
# can never offer a knob the math does not have. A control that silently
# changes nothing is worse than a missing one.

# Indicators whose `src` argument actually reaches _src(). The rest accept the
# parameter and ignore it — Supertrend is always hl2, Stochastic and Donchian
# read the highs and lows directly, ADX works off directional movement — so a
# Source dropdown on those would be decoration.
#
# CCI and MFI ARE in here: both run _src(rows, src), they simply default to
# hlc3 via their spec's src_default, so omitting the argument still gives the
# textbook typical-price formula while the dropdown remains honest.
SOURCE_OK = frozenset({"sma", "ema", "wma", "hma", "dema", "tema", "vwma", "rma",
                       "kama", "alma", "lsma", "rsi", "macd",
                       "bbands", "keltner", "roc", "cci", "williams_r", "mfi",
                       "stochrsi", "percent_b", "bandwidth", "trix", "kst", "dpo",
                       "connors_rsi"})

# A price source can sensibly vary inside Williams %R's high/low range, but a
# volume value cannot: it has different units and sends the declared -100..0
# oscillator into five-digit readings. Keep the shared source vocabulary for
# studies where volume is meaningful while withholding it here.
SOURCE_EXCLUDE = {"williams_r": frozenset({"volume"})}

# plumbing arguments: the chart passes them, the user never sets them
_HIDDEN_PARAMS = frozenset({"session_seconds", "tz_offset", "anchor_index"})

# TradingView's own wording, which is what a user has read on every other
# chart they have used. Where our math differs from TV's we say so rather
# than borrowing a label that promises a different formula — a label is a
# promise about the arithmetic behind it, not decoration.
_PERIOD_LABEL = {
    "rsi": "RSI Length", "adx": "ADX Smoothing", "stoch": "%K Length",
    "stochrsi": "Stochastic Length", "supertrend": "ATR Length",
    "ichimoku": "Conversion Line Length", "awesome": "Fast Length",
    "chaikin_osc": "Fast Length", "ultimate": "Short Length",
    "connors_rsi": "RSI Length",
}
_PARAM_LABEL = {
    ("bbands", "mult"): "StdDev",
    ("bbands", "ma_type"): "Basis MA Type",
    ("keltner", "mult"): "Multiplier",
    ("keltner", "use_ema"): "Use Exponential MA",
    ("keltner", "bands_style"): "Bands Style",
    ("keltner", "atr_length"): "ATR Length",
    ("supertrend", "mult"): "Factor",
    ("macd", "fast"): "Fast Length",
    ("macd", "slow"): "Slow Length",
    ("macd", "signal"): "Signal Length",
    ("macd", "osc_ma"): "Oscillator MA Type",
    ("macd", "signal_ma"): "Signal Line MA Type",
    ("atr", "smoothing"): "Smoothing",
    ("adx", "di_length"): "DI Length",
    ("stoch", "smooth_k"): "%K Smoothing",
    ("stoch", "smooth_d"): "%D Smoothing",
    ("stochrsi", "smooth_k"): "%K Smoothing",
    ("stochrsi", "smooth_d"): "%D Smoothing",
    ("stochrsi", "rsi_length"): "RSI Length",
    ("psar", "start"): "Start",
    ("psar", "step"): "Increment",
    ("psar", "cap"): "Max value",
    ("vwap", "anchor"): "Anchor Period",
    ("ichimoku", "base_length"): "Base Line Length",
    ("ichimoku", "span_b_length"): "Leading Span B Length",
    ("ichimoku", "displacement"): "Displacement",
    ("pivots", "pivot_type"): "Type", ("pivots", "timeframe"): "Pivots Timeframe",
    ("awesome", "slow"): "Slow Length", ("chaikin_osc", "slow"): "Slow Length",
    ("ultimate", "middle"): "Middle Length", ("ultimate", "long"): "Long Length",
    ("kst", "roc2"): "ROC Length 2", ("kst", "roc3"): "ROC Length 3",
    ("kst", "roc4"): "ROC Length 4", ("kst", "sma1"): "SMA Length 1",
    ("kst", "sma2"): "SMA Length 2", ("kst", "sma3"): "SMA Length 3",
    ("kst", "sma4"): "SMA Length 4", ("force", "n"): "Length",
    ("eom", "divisor"): "Divisor", ("connors_rsi", "streak_length"): "Streak RSI Length",
    ("connors_rsi", "rank_length"): "ROC Rank Length",
    ("kama", "fast"): "Fast Length", ("kama", "slow"): "Slow Length",
    ("alma", "offset"): "Offset", ("alma", "sigma"): "Sigma",
}
_PARAM_RANGE = {                       # key -> (min, max, step)
    "mult": (0.1, 50.0, 0.1),
    "start": (0.001, 1.0, 0.001),
    "step": (0.001, 1.0, 0.001),
    "cap":  (0.01, 1.0, 0.01),
    "offset": (0.0, 1.0, 0.01), "sigma": (0.1, 20.0, 0.1),
    "divisor": (1.0, 1000000000.0, 1.0),
}

# The choices behind every dropdown that is not a price source. Each list is
# the set of values the function actually branches on — an option that fell
# through to the default would be a control that lies.
_ENUM_VALUES = {
    "ma_type": list(MA_TYPES),
    "osc_ma": ["sma", "ema"],
    "signal_ma": ["sma", "ema"],
    "smoothing": ["rma", "sma", "ema", "wma"],
    "bands_style": ["atr", "tr", "range"],
    "anchor": ["session", "week", "month"],
    "pivot_type": ["traditional", "fibonacci", "camarilla"],
    "timeframe": ["auto", "day", "week", "month"],
}
_ENUM_LABELS = {
    **MA_LABELS,
    "atr": "Average True Range", "tr": "True Range", "range": "Range",
    "session": "Session", "week": "Week", "month": "Month",
    "traditional": "Traditional", "fibonacci": "Fibonacci", "camarilla": "Camarilla",
    "auto": "Auto", "day": "Daily",
}


def _enum(key: str) -> dict:
    vals = _ENUM_VALUES[key]
    return {"type": "enum",
            "options": [{"value": v, "label": _ENUM_LABELS.get(v, v.upper())}
                        for v in vals]}


def inputs(name: str) -> list[dict]:
    """The editable inputs of one indicator, in dialog order."""
    spec = SPECS[name]
    out: list[dict] = []
    if spec["period"]:                 # period 0 means "this one has no length"
        out.append({"key": "period", "label": _PERIOD_LABEL.get(name, "Length"),
                    "type": "int", "min": 1, "max": 500, "step": 1,
                    "default": spec["period"]})
    if name in SOURCE_OK:
        out.append({"key": "source", "label": "Source", "type": "source",
                    "default": spec.get("src_default", "close"),
                    "options": [s for s in SOURCES
                                if s not in SOURCE_EXCLUDE.get(name, ())]})
    # everything after (rows, n, src) that carries a default is a real knob
    params = list(_inspect.signature(spec["fn"]).parameters.items())[3:]
    for k, p in params:
        if k in _HIDDEN_PARAMS or p.default is _inspect.Parameter.empty:
            continue
        field = {"key": k,
                 "label": _PARAM_LABEL.get((name, k), k.replace("_", " ").title()),
                 "default": p.default}
        if k in _ENUM_VALUES:
            field.update(_enum(k))
        elif isinstance(p.default, bool):      # before int: bool IS an int
            field["type"] = "bool"
        else:
            is_float = isinstance(p.default, float)
            lo, hi, st = _PARAM_RANGE.get(
                k, (0.01, 100.0, 0.01) if is_float else (1, 500, 1))
            field.update({"type": "float" if is_float else "int",
                          "min": lo, "max": hi, "step": st})
        out.append(field)
    return out


def compute(name: str, rows: list[tuple], period: int = 0,
            source: str = "", **extra) -> dict:
    """Run one indicator. Returns {lines: {...}, last: {...}, spec: {...}}.

    An empty `source` means "this indicator's own default column" — CCI and
    MFI are typical-price instruments, and a caller that simply omits the
    argument must still get the textbook formula rather than a close-based
    variant that happens to share the name.
    """
    spec = SPECS.get(name)
    if not spec:
        raise ValueError(f"unknown indicator '{name}'")
    source = source or spec.get("src_default", "close")
    if source in SOURCE_EXCLUDE.get(name, ()):
        raise ValueError(f"{source} is not a valid source for {name}")
    n = int(period or spec["period"] or 14)
    if n < 1 or n > 500:
        raise ValueError("period must be between 1 and 500")
    warmup = n if spec["period"] else 1
    if len(rows) < warmup + 2:
        raise ValueError(f"{name}({n}) needs at least {warmup + 2} bars, got {len(rows)}")
    # An instrument that prints no traded quantity cannot have a volume
    # indicator computed on it, and the arithmetic does not say so: measured
    # on NIFTY 50 daily bars, OBV and A/D come back a flat 0.0, VWAP quietly
    # degenerates to an unweighted typical-price mean, and MFI(14) returns
    # 100.0 — a maximally-overbought reading manufactured from nothing. That
    # is a fabricated number on the index users ask about most, so it is
    # refused here, at the one place every caller goes through.
    # Only kwargs THIS function actually declares. MULT_OK is the same guard
    # written out for one parameter; deriving it from the signature covers
    # every parameter at once, so a caller that forwards `tz_offset` to RSI
    # gets it dropped rather than raising TypeError and burning the whole
    # call — which is exactly how `mult` failed before MULT_OK existed.
    accepts = _inspect.signature(spec["fn"]).parameters
    kw = {k: v for k, v in extra.items() if v is not None and k in accepts}
    # Volume dependence is a property of THIS CALL, not only of the
    # indicator's group. The settings dialog can point any moving average at
    # VWMA, and a source dropdown can point any indicator at the volume
    # column — so Bollinger Bands, group "volatility", becomes volume-based
    # the moment its Basis MA Type is VWMA. Measured on NIFTY 50 that returned
    # zero points on all five lines: the bands simply vanished off the chart
    # with nothing said, which is the same silent failure as a fabricated
    # number wearing different clothes.
    why = ("is a volume study" if spec["group"] == "volume"
           else "is being computed on the volume column" if source == "volume"
           else "is set to VWMA, which weights by volume"
           if any(str(v).lower() == "vwma" for v in kw.values()) else "")
    if why and not any(r[5] for r in rows):
        # One short factual sentence, because this string is read by BOTH a
        # person (the chart's status bar) and the model (the tool's error).
        # Guidance on how to phrase a reply belongs in the tool's `_note`
        # alongside every other model instruction, not in a UI toast.
        raise ValueError(
            f"{name} {why} and this instrument prints no volume — every bar "
            f"in the window has v=0, as indices and India VIX are quoted. "
            f"Price-only indicators (RSI, MACD, ATR, Bollinger) work here.")
    lines = spec["fn"](rows, n, source, **kw)
    return {"lines": lines,
            "last": {k: (None if _last(v) is None else round(_last(v), 4))
                     for k, v in lines.items()},
            "spec": {"name": name, "period": n if spec["period"] else 0, "source": source,
                     "pane": spec["pane"], "group": spec["group"],
                     "formula": spec["formula"],
                     **({"bounds": list(spec["bounds"])} if "bounds" in spec else {}),
                     **({k: v for k, v in kw.items()} if kw else {})}}
