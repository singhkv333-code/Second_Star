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
    out, k, prev = [], 2 / (n + 1), None
    for i, x in enumerate(v):
        prev = x if prev is None else x * k + prev * (1 - k)
        out.append(prev if i >= n - 1 else None)
    return out


def wilder(v: list[float], n: int) -> Series:
    """k = 1/n. NOT ema(n) — see the module docstring."""
    out, prev = [], None
    for i, x in enumerate(v):
        prev = x if prev is None else (prev * (n - 1) + x) / n
        out.append(prev if i >= n - 1 else None)
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
            ist_offset=19800):
    """VWAP reset on TradingView's Anchor Period — the IST trading day, the
    week, or the month. Anchoring to a chosen BAR is anchored_vwap's job."""
    a = (anchor or "session").lower()

    def bucket(ts: int):
        d = (ts + ist_offset) // session_seconds
        if a == "week":
            # epoch day 0 was a Thursday; shift so a bucket breaks on Monday
            return (d + 3) // 7
        if a == "month":
            g = _time.gmtime(ts + ist_offset)
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
        hi = max(range(len(w)), key=lambda k: w[k][2])
        lo = min(range(len(w)), key=lambda k: w[k][3])
        up.append(hi / n * 100)
        dn.append(lo / n * 100)
    return {"aroon_up": up, "aroon_down": dn,
            "oscillator": [None if (up[i] is None) else up[i] - dn[i]
                           for i in range(len(rows))]}


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
    "roc":       dict(fn=_f_roc, period=12, pane="own", group="momentum",
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
    "aroon":     dict(fn=_f_aroon, period=25, pane="own", group="trend", bounds=(0, 100),
                      formula="up = 100*(bars since the n-bar high)/n, down likewise for the low"),
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
# parameter and ignore it — CCI and MFI are always typical price, Supertrend
# is always hl2, Stochastic reads the highs and lows directly — so a Source
# dropdown on those would be decoration.
SOURCE_OK = frozenset({"sma", "ema", "wma", "hma", "dema", "rsi", "macd",
                       "bbands", "keltner", "roc", "cci", "williams_r", "mfi",
                       "stochrsi"})

# plumbing arguments: the chart passes them, the user never sets them
_HIDDEN_PARAMS = frozenset({"session_seconds", "ist_offset", "anchor_index"})

# TradingView's own wording, which is what a user has read on every other
# chart they have used. Where our math differs from TV's we say so rather
# than borrowing a label that promises a different formula: our PSAR has one
# acceleration step, so it gets "Increment", never TV's separate "Start".
_PERIOD_LABEL = {
    "rsi": "RSI Length", "adx": "ADX Smoothing", "stoch": "%K Length",
    "stochrsi": "Stochastic Length", "supertrend": "ATR Length",
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
    ("macd", "signal"): "Signal Smoothing",
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
}
_PARAM_RANGE = {                       # key -> (min, max, step)
    "mult": (0.1, 50.0, 0.1),
    "start": (0.001, 1.0, 0.001),
    "step": (0.001, 1.0, 0.001),
    "cap":  (0.01, 1.0, 0.01),
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
}
_ENUM_LABELS = {
    **MA_LABELS,
    "atr": "Average True Range", "tr": "True Range", "range": "Range",
    "session": "Session", "week": "Week", "month": "Month",
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
                    "options": list(SOURCES)})
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
    n = int(period or spec["period"] or 14)
    if n < 1 or n > 500:
        raise ValueError("period must be between 1 and 500")
    if len(rows) < n + 2:
        raise ValueError(f"{name}({n}) needs at least {n + 2} bars, got {len(rows)}")
    # An instrument that prints no traded quantity cannot have a volume
    # indicator computed on it, and the arithmetic does not say so: measured
    # on NIFTY 50 daily bars, OBV and A/D come back a flat 0.0, VWAP quietly
    # degenerates to an unweighted typical-price mean, and MFI(14) returns
    # 100.0 — a maximally-overbought reading manufactured from nothing. That
    # is a fabricated number on the index users ask about most, so it is
    # refused here, at the one place every caller goes through.
    kw = {k: v for k, v in extra.items() if v is not None}
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
            "spec": {"name": name, "period": n, "source": source,
                     "pane": spec["pane"], "group": spec["group"],
                     "formula": spec["formula"],
                     **({"bounds": list(spec["bounds"])} if "bounds" in spec else {}),
                     **({k: v for k, v in kw.items()} if kw else {})}}
