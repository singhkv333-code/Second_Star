"""
Wide primitive backtester library.

All indicator and signal primitives, plus combinators, plus the registries
(SIGNAL_REGISTRY, WARMUP) used by the composer/engine.

NO LOOK-AHEAD: every crossover signal uses .shift(1) on the underlying series
before comparing, so signals on day N depend only on data through day N-1.
"""
from __future__ import annotations

import calendar

import numpy as np
import pandas as pd

try:
    import pandas_ta_classic as ta
except ImportError:  # pragma: no cover
    import pandas_ta as ta


WARMUP = {
    "sma": lambda p: p * 2,
    "ema": lambda p: p * 3,
    "rsi": lambda p, **k: p * 2 + 10,
    "macd": lambda fast, slow, sig, **k: slow * 2 + sig,
    "bb": lambda p, **k: p * 2,
    "stoch": lambda k, d, **kw: (k + d) * 2,
    "adx": lambda p: p * 2,
    "atr": lambda p: p * 2,
    "cci": lambda p: p * 2,
    "williams_r": lambda p: p,
    "mfi": lambda p: p * 2,
    "obv": lambda: 5,
    "supertrend": lambda p: p * 3,
    "ichimoku": lambda: 60 * 2,
    "keltner": lambda p: p * 3,
    "donchian": lambda p: p,
    "vwap": lambda: 1,
    "roc": lambda p: p,
    "momentum": lambda p: p,
    "cmf": lambda p: p * 2,
    "aroon": lambda p: p,
    "psar": lambda: 5,
    "squeeze": lambda: 40,
    "52wk": lambda: 252,
    "sma200": lambda: 250,
}


def _bool(s: pd.Series, index) -> pd.Series:
    return pd.Series(s, index=index).fillna(False).astype(bool)


def _cross_up(prev: pd.Series, curr: pd.Series, threshold) -> pd.Series:
    if isinstance(threshold, pd.Series):
        return (prev <= threshold.shift(1)) & (curr > threshold)
    return (prev <= threshold) & (curr > threshold)


def _cross_down(prev: pd.Series, curr: pd.Series, threshold) -> pd.Series:
    if isinstance(threshold, pd.Series):
        return (prev >= threshold.shift(1)) & (curr < threshold)
    return (prev >= threshold) & (curr < threshold)


# ════════════════════════════════════════════════════════════
# SECTION A — MOVING AVERAGES
# ════════════════════════════════════════════════════════════

def calc_sma(df: pd.DataFrame, period: int) -> pd.Series:
    return df['close'].rolling(period).mean()


def calc_ema(df: pd.DataFrame, period: int) -> pd.Series:
    return df['close'].ewm(span=period, adjust=False).mean()


def calc_wma(df: pd.DataFrame, period: int) -> pd.Series:
    out = ta.wma(df['close'], length=period)
    return out if out is not None else df['close'].rolling(period).apply(
        lambda x: np.dot(x, np.arange(1, period + 1)) / (period * (period + 1) / 2), raw=True)


def calc_hma(df: pd.DataFrame, period: int) -> pd.Series:
    out = ta.hma(df['close'], length=period)
    return out


def calc_dema(df: pd.DataFrame, period: int) -> pd.Series:
    return ta.dema(df['close'], length=period)


def calc_tema(df: pd.DataFrame, period: int) -> pd.Series:
    return ta.tema(df['close'], length=period)


def calc_vwma(df: pd.DataFrame, period: int) -> pd.Series:
    pv = df['close'] * df['volume']
    return pv.rolling(period).sum() / df['volume'].rolling(period).sum()


def calc_kama(df: pd.DataFrame, period: int = 10, fast: int = 2, slow: int = 30) -> pd.Series:
    out = ta.kama(df['close'], length=period, fast=fast, slow=slow)
    return out


# ── Section A signals ──

def sig_price_cross_above_sma(df: pd.DataFrame, period: int) -> pd.Series:
    sma = calc_sma(df, period)
    prev_close = df['close'].shift(1)
    prev_sma = sma.shift(1)
    sig = (prev_close < prev_sma) & (df['close'] >= sma)
    return _bool(sig, df.index)


def sig_price_cross_below_sma(df: pd.DataFrame, period: int) -> pd.Series:
    sma = calc_sma(df, period)
    prev_close = df['close'].shift(1)
    prev_sma = sma.shift(1)
    sig = (prev_close > prev_sma) & (df['close'] <= sma)
    return _bool(sig, df.index)


def sig_price_above_sma(df: pd.DataFrame, period: int) -> pd.Series:
    sma = calc_sma(df, period)
    return _bool(df['close'] > sma, df.index)


def sig_price_below_sma(df: pd.DataFrame, period: int) -> pd.Series:
    sma = calc_sma(df, period)
    return _bool(df['close'] < sma, df.index)


def sig_sma_cross_above_sma(df: pd.DataFrame, fast_period: int = 50, slow_period: int = 200) -> pd.Series:
    fast = calc_sma(df, fast_period)
    slow = calc_sma(df, slow_period)
    sig = (fast.shift(1) < slow.shift(1)) & (fast >= slow)
    return _bool(sig, df.index)


def sig_sma_cross_below_sma(df: pd.DataFrame, fast_period: int = 50, slow_period: int = 200) -> pd.Series:
    fast = calc_sma(df, fast_period)
    slow = calc_sma(df, slow_period)
    sig = (fast.shift(1) > slow.shift(1)) & (fast <= slow)
    return _bool(sig, df.index)


def sig_ema_cross_above_ema(df: pd.DataFrame, fast_period: int = 12, slow_period: int = 26) -> pd.Series:
    fast = calc_ema(df, fast_period)
    slow = calc_ema(df, slow_period)
    sig = (fast.shift(1) < slow.shift(1)) & (fast >= slow)
    return _bool(sig, df.index)


def sig_ema_cross_below_ema(df: pd.DataFrame, fast_period: int = 12, slow_period: int = 26) -> pd.Series:
    fast = calc_ema(df, fast_period)
    slow = calc_ema(df, slow_period)
    sig = (fast.shift(1) > slow.shift(1)) & (fast <= slow)
    return _bool(sig, df.index)


def sig_price_above_vwma(df: pd.DataFrame, period: int) -> pd.Series:
    vwma = calc_vwma(df, period)
    return _bool(df['close'] > vwma, df.index)


def sig_hma_direction_up(df: pd.DataFrame, period: int) -> pd.Series:
    hma = calc_hma(df, period)
    sig = hma > hma.shift(1)
    return _bool(sig, df.index)


# ════════════════════════════════════════════════════════════
# SECTION B — MOMENTUM OSCILLATORS
# ════════════════════════════════════════════════════════════

def calc_rsi(df: pd.DataFrame, period: int = 14) -> pd.Series:
    return ta.rsi(df['close'], length=period)


def calc_rsi_divergence(df: pd.DataFrame, period: int = 14, lookback: int = 5) -> pd.Series:
    rsi = calc_rsi(df, period)
    out = pd.Series(0, index=df.index, dtype=int)
    price_low = df['close'].rolling(lookback).min()
    price_high = df['close'].rolling(lookback).max()
    rsi_low = rsi.rolling(lookback).min()
    rsi_high = rsi.rolling(lookback).max()
    bull = (df['close'] < price_low.shift(lookback)) & (rsi > rsi_low.shift(lookback))
    bear = (df['close'] > price_high.shift(lookback)) & (rsi < rsi_high.shift(lookback))
    out = out.where(~bull, 1)
    out = out.where(~bear, -1)
    return out


def calc_stochastic(df: pd.DataFrame, k_period: int = 14, d_period: int = 3, smooth: int = 3) -> pd.DataFrame:
    return ta.stoch(df['high'], df['low'], df['close'], k=k_period, d=d_period, smooth_k=smooth)


def calc_stochrsi(df: pd.DataFrame, rsi_period: int = 14, stoch_period: int = 14, k: int = 3, d: int = 3) -> pd.DataFrame:
    return ta.stochrsi(df['close'], length=stoch_period, rsi_length=rsi_period, k=k, d=d)


def calc_cci(df: pd.DataFrame, period: int = 20) -> pd.Series:
    return ta.cci(df['high'], df['low'], df['close'], length=period)


def calc_williams_r(df: pd.DataFrame, period: int = 14) -> pd.Series:
    return ta.willr(df['high'], df['low'], df['close'], length=period)


def calc_mfi(df: pd.DataFrame, period: int = 14) -> pd.Series:
    return ta.mfi(df['high'], df['low'], df['close'], df['volume'], length=period)


def calc_roc(df: pd.DataFrame, period: int = 10) -> pd.Series:
    return ta.roc(df['close'], length=period)


def calc_momentum(df: pd.DataFrame, period: int = 10) -> pd.Series:
    return ta.mom(df['close'], length=period)


def calc_tsi(df: pd.DataFrame, fast: int = 13, slow: int = 25) -> pd.Series:
    out = ta.tsi(df['close'], fast=fast, slow=slow)
    if isinstance(out, pd.DataFrame):
        return out.iloc[:, 0]
    return out


def calc_dpo(df: pd.DataFrame, period: int = 20) -> pd.Series:
    shift = period // 2 + 1
    sma = df['close'].rolling(period).mean()
    return df['close'] - sma.shift(shift)


def calc_cmo(df: pd.DataFrame, period: int = 14) -> pd.Series:
    return ta.cmo(df['close'], length=period)


def calc_ultimate_oscillator(df: pd.DataFrame, fast: int = 7, medium: int = 14, slow: int = 28) -> pd.Series:
    return ta.uo(df['high'], df['low'], df['close'], fast=fast, medium=medium, slow=slow)


def calc_awesome_oscillator(df: pd.DataFrame) -> pd.Series:
    return ta.ao(df['high'], df['low'])


# ── Section B signals ──

def sig_rsi_cross_below(df: pd.DataFrame, period: int = 14, threshold: float = 30.0) -> pd.Series:
    rsi = calc_rsi(df, period)
    sig = (rsi.shift(1) >= threshold) & (rsi < threshold)
    return _bool(sig, df.index)


def sig_rsi_cross_above(df: pd.DataFrame, period: int = 14, threshold: float = 70.0) -> pd.Series:
    rsi = calc_rsi(df, period)
    sig = (rsi.shift(1) <= threshold) & (rsi > threshold)
    return _bool(sig, df.index)


def sig_rsi_in_range(df: pd.DataFrame, period: int = 14, low: float = 40.0, high: float = 60.0) -> pd.Series:
    rsi = calc_rsi(df, period)
    prev = rsi.shift(1)
    in_now = (rsi >= low) & (rsi <= high)
    in_prev = (prev >= low) & (prev <= high)
    sig = in_now & ~in_prev
    return _bool(sig, df.index)


def sig_rsi_below_level(df: pd.DataFrame, period: int = 14, threshold: float = 30.0, cooldown_days: int = 5) -> pd.Series:
    rsi = calc_rsi(df, period)
    raw = (rsi < threshold).fillna(False).astype(bool)
    out = pd.Series(False, index=df.index)
    last = -10**9
    for i, (idx, val) in enumerate(raw.items()):
        if val and (i - last) > cooldown_days:
            out.iloc[i] = True
            last = i
    return _bool(out, df.index)


def sig_rsi_divergence_bullish(df: pd.DataFrame, period: int = 14, lookback: int = 5) -> pd.Series:
    div = calc_rsi_divergence(df, period, lookback)
    return _bool(div == 1, df.index)


def sig_rsi_divergence_bearish(df: pd.DataFrame, period: int = 14, lookback: int = 5) -> pd.Series:
    div = calc_rsi_divergence(df, period, lookback)
    return _bool(div == -1, df.index)


def sig_stoch_cross_above(df: pd.DataFrame, k: int = 14, d: int = 3, threshold: float = 20.0) -> pd.Series:
    stoch = calc_stochastic(df, k_period=k, d_period=d)
    if stoch is None or stoch.empty:
        return _bool(pd.Series(False, index=df.index), df.index)
    k_col = [c for c in stoch.columns if c.startswith('STOCHk')][0]
    d_col = [c for c in stoch.columns if c.startswith('STOCHd')][0]
    kk = stoch[k_col]; dd = stoch[d_col]
    cross = (kk.shift(1) < dd.shift(1)) & (kk >= dd)
    sig = cross & (kk < threshold) & (dd < threshold)
    return _bool(sig, df.index)


def sig_stoch_cross_below(df: pd.DataFrame, k: int = 14, d: int = 3, threshold: float = 80.0) -> pd.Series:
    stoch = calc_stochastic(df, k_period=k, d_period=d)
    if stoch is None or stoch.empty:
        return _bool(pd.Series(False, index=df.index), df.index)
    k_col = [c for c in stoch.columns if c.startswith('STOCHk')][0]
    d_col = [c for c in stoch.columns if c.startswith('STOCHd')][0]
    kk = stoch[k_col]; dd = stoch[d_col]
    cross = (kk.shift(1) > dd.shift(1)) & (kk <= dd)
    sig = cross & (kk > threshold) & (dd > threshold)
    return _bool(sig, df.index)


def sig_cci_cross_above(df: pd.DataFrame, period: int = 20, threshold: float = -100.0) -> pd.Series:
    cci = calc_cci(df, period)
    sig = (cci.shift(1) <= threshold) & (cci > threshold)
    return _bool(sig, df.index)


def sig_cci_cross_below(df: pd.DataFrame, period: int = 20, threshold: float = 100.0) -> pd.Series:
    cci = calc_cci(df, period)
    sig = (cci.shift(1) >= threshold) & (cci < threshold)
    return _bool(sig, df.index)


def sig_williams_r_cross_above(df: pd.DataFrame, period: int = 14, threshold: float = -80.0) -> pd.Series:
    wr = calc_williams_r(df, period)
    sig = (wr.shift(1) <= threshold) & (wr > threshold)
    return _bool(sig, df.index)


def sig_mfi_cross_below(df: pd.DataFrame, period: int = 14, threshold: float = 20.0) -> pd.Series:
    mfi = calc_mfi(df, period)
    sig = (mfi.shift(1) >= threshold) & (mfi < threshold)
    return _bool(sig, df.index)


def sig_roc_cross_zero_up(df: pd.DataFrame, period: int = 10) -> pd.Series:
    roc = calc_roc(df, period)
    sig = (roc.shift(1) <= 0) & (roc > 0)
    return _bool(sig, df.index)


def sig_momentum_cross_zero_up(df: pd.DataFrame, period: int = 10) -> pd.Series:
    mom = calc_momentum(df, period)
    sig = (mom.shift(1) <= 0) & (mom > 0)
    return _bool(sig, df.index)


def sig_stochrsi_cross_above(df: pd.DataFrame, threshold: float = 20.0) -> pd.Series:
    sr = calc_stochrsi(df)
    if sr is None or sr.empty:
        return _bool(pd.Series(False, index=df.index), df.index)
    k_col = [c for c in sr.columns if c.startswith('STOCHRSIk')][0]
    kk = sr[k_col]
    sig = (kk.shift(1) <= threshold) & (kk > threshold)
    return _bool(sig, df.index)


def sig_ao_cross_zero_up(df: pd.DataFrame) -> pd.Series:
    ao = calc_awesome_oscillator(df)
    sig = (ao.shift(1) <= 0) & (ao > 0)
    return _bool(sig, df.index)


def sig_ao_cross_zero_down(df: pd.DataFrame) -> pd.Series:
    ao = calc_awesome_oscillator(df)
    sig = (ao.shift(1) >= 0) & (ao < 0)
    return _bool(sig, df.index)


# ════════════════════════════════════════════════════════════
# SECTION C — MACD
# ════════════════════════════════════════════════════════════

def calc_macd(df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    return ta.macd(df['close'], fast=fast, slow=slow, signal=signal)


def calc_macd_histogram(df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.Series:
    macd = calc_macd(df, fast, slow, signal)
    if macd is None or macd.empty:
        return pd.Series(np.nan, index=df.index)
    h_col = [c for c in macd.columns if c.startswith('MACDh')][0]
    return macd[h_col]


def _macd_lines(df, fast, slow, signal):
    m = calc_macd(df, fast, slow, signal)
    if m is None or m.empty:
        empty = pd.Series(np.nan, index=df.index)
        return empty, empty, empty
    line = m[[c for c in m.columns if c.startswith('MACD_')][0]]
    sig_l = m[[c for c in m.columns if c.startswith('MACDs')][0]]
    hist = m[[c for c in m.columns if c.startswith('MACDh')][0]]
    return line, sig_l, hist


def sig_macd_cross_above_signal(df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.Series:
    line, sig_l, _ = _macd_lines(df, fast, slow, signal)
    sig = (line.shift(1) < sig_l.shift(1)) & (line >= sig_l)
    return _bool(sig, df.index)


def sig_macd_cross_below_signal(df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.Series:
    line, sig_l, _ = _macd_lines(df, fast, slow, signal)
    sig = (line.shift(1) > sig_l.shift(1)) & (line <= sig_l)
    return _bool(sig, df.index)


def sig_macd_histogram_cross_zero_up(df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.Series:
    _, _, hist = _macd_lines(df, fast, slow, signal)
    sig = (hist.shift(1) <= 0) & (hist > 0)
    return _bool(sig, df.index)


def sig_macd_histogram_cross_zero_down(df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.Series:
    _, _, hist = _macd_lines(df, fast, slow, signal)
    sig = (hist.shift(1) >= 0) & (hist < 0)
    return _bool(sig, df.index)


def sig_macd_line_cross_zero_up(df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.Series:
    line, _, _ = _macd_lines(df, fast, slow, signal)
    sig = (line.shift(1) <= 0) & (line > 0)
    return _bool(sig, df.index)


def sig_macd_line_cross_zero_down(df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.Series:
    line, _, _ = _macd_lines(df, fast, slow, signal)
    sig = (line.shift(1) >= 0) & (line < 0)
    return _bool(sig, df.index)


def sig_macd_divergence_bullish(df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9, lookback: int = 5) -> pd.Series:
    _, _, hist = _macd_lines(df, fast, slow, signal)
    price_low = df['close'].rolling(lookback).min()
    hist_low = hist.rolling(lookback).min()
    sig = (df['close'] < price_low.shift(lookback)) & (hist > hist_low.shift(lookback))
    return _bool(sig, df.index)


def sig_macd_histogram_expanding_bullish(df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.Series:
    _, _, hist = _macd_lines(df, fast, slow, signal)
    sig = (hist > 0) & (hist > hist.shift(1))
    return _bool(sig, df.index)


# ════════════════════════════════════════════════════════════
# SECTION D — BOLLINGER BANDS
# ════════════════════════════════════════════════════════════

def calc_bbands(df: pd.DataFrame, period: int = 20, std: float = 2.0) -> pd.DataFrame:
    return ta.bbands(df['close'], length=period, std=std)


def _bb_cols(bb):
    lower = bb[[c for c in bb.columns if c.startswith('BBL')][0]]
    middle = bb[[c for c in bb.columns if c.startswith('BBM')][0]]
    upper = bb[[c for c in bb.columns if c.startswith('BBU')][0]]
    return lower, middle, upper


def calc_bb_bandwidth(df: pd.DataFrame, period: int = 20, std: float = 2.0) -> pd.Series:
    bb = calc_bbands(df, period, std)
    lower, middle, upper = _bb_cols(bb)
    return (upper - lower) / middle * 100


def calc_bb_percent_b(df: pd.DataFrame, period: int = 20, std: float = 2.0) -> pd.Series:
    bb = calc_bbands(df, period, std)
    lower, _, upper = _bb_cols(bb)
    return (df['close'] - lower) / (upper - lower)


def sig_bb_lower_touch(df: pd.DataFrame, period: int = 20, std: float = 2.0) -> pd.Series:
    bb = calc_bbands(df, period, std)
    lower, _, _ = _bb_cols(bb)
    prev_low = df['low'].shift(1)
    prev_lower = lower.shift(1)
    sig = (prev_low > prev_lower) & (df['low'] <= lower)
    return _bool(sig, df.index)


def sig_bb_upper_touch(df: pd.DataFrame, period: int = 20, std: float = 2.0) -> pd.Series:
    bb = calc_bbands(df, period, std)
    _, _, upper = _bb_cols(bb)
    prev_high = df['high'].shift(1)
    prev_upper = upper.shift(1)
    sig = (prev_high < prev_upper) & (df['high'] >= upper)
    return _bool(sig, df.index)


def sig_bb_breakout_above(df: pd.DataFrame, period: int = 20, std: float = 2.0) -> pd.Series:
    bb = calc_bbands(df, period, std)
    _, _, upper = _bb_cols(bb)
    sig = (df['close'].shift(1) <= upper.shift(1)) & (df['close'] > upper)
    return _bool(sig, df.index)


def sig_bb_breakout_below(df: pd.DataFrame, period: int = 20, std: float = 2.0) -> pd.Series:
    bb = calc_bbands(df, period, std)
    lower, _, _ = _bb_cols(bb)
    sig = (df['close'].shift(1) >= lower.shift(1)) & (df['close'] < lower)
    return _bool(sig, df.index)


def sig_bb_squeeze(df: pd.DataFrame, period: int = 20, std: float = 2.0, squeeze_pct: float = 10.0) -> pd.Series:
    bw = calc_bb_bandwidth(df, period, std)
    rolling_min = bw.rolling(252, min_periods=20).min()
    rolling_max = bw.rolling(252, min_periods=20).max()
    rng = rolling_max - rolling_min
    threshold = rolling_min + rng * (squeeze_pct / 100.0)
    sig = bw <= threshold
    return _bool(sig, df.index)


def sig_bb_squeeze_breakout_up(df: pd.DataFrame, period: int = 20, std: float = 2.0) -> pd.Series:
    squeeze_recent = sig_bb_squeeze(df, period, std).rolling(10).max().fillna(0).astype(bool)
    breakout = sig_bb_breakout_above(df, period, std)
    sig = squeeze_recent.shift(1).fillna(False) & breakout
    return _bool(sig, df.index)


def sig_bb_w_pattern(df: pd.DataFrame, period: int = 20, std: float = 2.0, lookback: int = 10) -> pd.Series:
    bb = calc_bbands(df, period, std)
    lower, _, _ = _bb_cols(bb)
    touched = (df['low'] <= lower)
    touch_count = touched.rolling(lookback).sum()
    recent_touch = touched.shift(1).fillna(False)
    sig = (touch_count >= 2) & recent_touch & (df['close'] > df['close'].shift(1))
    return _bool(sig, df.index)


def sig_bb_mean_reversion(df: pd.DataFrame, period: int = 20, std: float = 2.0) -> pd.Series:
    bb = calc_bbands(df, period, std)
    lower, _, _ = _bb_cols(bb)
    prev_close = df['close'].shift(1)
    prev_lower = lower.shift(1)
    sig = (prev_close < prev_lower) & (df['close'] >= lower)
    return _bool(sig, df.index)


# ════════════════════════════════════════════════════════════
# SECTION E — VOLATILITY
# ════════════════════════════════════════════════════════════

def calc_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    return ta.atr(df['high'], df['low'], df['close'], length=period)


def calc_atr_pct(df: pd.DataFrame, period: int = 14) -> pd.Series:
    atr = calc_atr(df, period)
    return atr / df['close'] * 100


def calc_historical_volatility(df: pd.DataFrame, period: int = 20) -> pd.Series:
    log_returns = np.log(df['close'] / df['close'].shift(1))
    return log_returns.rolling(period).std() * np.sqrt(252) * 100


def calc_keltner_channels(df: pd.DataFrame, ema_period: int = 20, atr_period: int = 10, mult: float = 2.0) -> pd.DataFrame:
    ema = calc_ema(df, ema_period)
    atr = calc_atr(df, atr_period)
    upper = ema + mult * atr
    lower = ema - mult * atr
    return pd.DataFrame({
        f'KCL_{ema_period}_{atr_period}_{mult}': lower,
        f'KCM_{ema_period}_{atr_period}_{mult}': ema,
        f'KCU_{ema_period}_{atr_period}_{mult}': upper,
    })


def calc_donchian_channels(df: pd.DataFrame, period: int = 20) -> pd.DataFrame:
    upper = df['high'].rolling(period).max()
    lower = df['low'].rolling(period).min()
    middle = (upper + lower) / 2
    return pd.DataFrame({
        f'DCL_{period}': lower,
        f'DCM_{period}': middle,
        f'DCU_{period}': upper,
    })


def calc_supertrend(df: pd.DataFrame, atr_period: int = 10, mult: float = 3.0) -> pd.DataFrame:
    return ta.supertrend(df['high'], df['low'], df['close'], length=atr_period, multiplier=mult)


def calc_chaikin_volatility(df: pd.DataFrame, period: int = 10) -> pd.Series:
    hl = df['high'] - df['low']
    ema_hl = hl.ewm(span=period, adjust=False).mean()
    return (ema_hl - ema_hl.shift(period)) / ema_hl.shift(period) * 100


def _supertrend_dir(df, atr_period, mult):
    st = calc_supertrend(df, atr_period, mult)
    if st is None or st.empty:
        return pd.Series(np.nan, index=df.index), pd.Series(np.nan, index=df.index)
    val_col = [c for c in st.columns if c.startswith('SUPERT_')][0]
    dir_col = [c for c in st.columns if c.startswith('SUPERTd')][0]
    return st[val_col], st[dir_col]


def sig_supertrend_flip_bullish(df: pd.DataFrame, atr_period: int = 10, mult: float = 3.0) -> pd.Series:
    _, direction = _supertrend_dir(df, atr_period, mult)
    sig = (direction.shift(1) == -1) & (direction == 1)
    return _bool(sig, df.index)


def sig_supertrend_flip_bearish(df: pd.DataFrame, atr_period: int = 10, mult: float = 3.0) -> pd.Series:
    _, direction = _supertrend_dir(df, atr_period, mult)
    sig = (direction.shift(1) == 1) & (direction == -1)
    return _bool(sig, df.index)


def sig_price_above_supertrend(df: pd.DataFrame, atr_period: int = 10, mult: float = 3.0) -> pd.Series:
    _, direction = _supertrend_dir(df, atr_period, mult)
    return _bool(direction == 1, df.index)


def sig_keltner_breakout_above(df: pd.DataFrame, ema_period: int = 20, atr_period: int = 10, mult: float = 2.0) -> pd.Series:
    kc = calc_keltner_channels(df, ema_period, atr_period, mult)
    upper = kc[[c for c in kc.columns if c.startswith('KCU')][0]]
    sig = (df['close'].shift(1) <= upper.shift(1)) & (df['close'] > upper)
    return _bool(sig, df.index)


def sig_keltner_breakout_below(df: pd.DataFrame, ema_period: int = 20, atr_period: int = 10, mult: float = 2.0) -> pd.Series:
    kc = calc_keltner_channels(df, ema_period, atr_period, mult)
    lower = kc[[c for c in kc.columns if c.startswith('KCL')][0]]
    sig = (df['close'].shift(1) >= lower.shift(1)) & (df['close'] < lower)
    return _bool(sig, df.index)


def sig_donchian_breakout_above(df: pd.DataFrame, period: int = 20) -> pd.Series:
    prev_high = df['high'].shift(1).rolling(period).max()
    sig = df['close'] > prev_high
    return _bool(sig, df.index)


def sig_donchian_breakout_below(df: pd.DataFrame, period: int = 20) -> pd.Series:
    prev_low = df['low'].shift(1).rolling(period).min()
    sig = df['close'] < prev_low
    return _bool(sig, df.index)


def sig_atr_expansion(df: pd.DataFrame, period: int = 14, mult: float = 1.5) -> pd.Series:
    atr = calc_atr(df, period)
    avg = atr.rolling(20).mean()
    sig = atr > mult * avg
    return _bool(sig, df.index)


def sig_atr_contraction(df: pd.DataFrame, period: int = 14, mult: float = 0.6) -> pd.Series:
    atr = calc_atr(df, period)
    avg = atr.rolling(20).mean()
    sig = atr < mult * avg
    return _bool(sig, df.index)


def sig_low_volatility_period(df: pd.DataFrame, hv_period: int = 20, percentile: float = 20) -> pd.Series:
    hv = calc_historical_volatility(df, hv_period)
    threshold = hv.rolling(252, min_periods=20).quantile(percentile / 100.0)
    sig = hv <= threshold
    return _bool(sig, df.index)


# ════════════════════════════════════════════════════════════
# SECTION F — TREND STRENGTH
# ════════════════════════════════════════════════════════════

def calc_adx(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    return ta.adx(df['high'], df['low'], df['close'], length=period)


def calc_aroon(df: pd.DataFrame, period: int = 25) -> pd.DataFrame:
    return ta.aroon(df['high'], df['low'], length=period)


def calc_aroon_oscillator(df: pd.DataFrame, period: int = 25) -> pd.Series:
    aroon = calc_aroon(df, period)
    if aroon is None or aroon.empty:
        return pd.Series(np.nan, index=df.index)
    osc_cols = [c for c in aroon.columns if c.startswith('AROONOSC')]
    if osc_cols:
        return aroon[osc_cols[0]]
    up = aroon[[c for c in aroon.columns if c.startswith('AROONU')][0]]
    dn = aroon[[c for c in aroon.columns if c.startswith('AROOND')][0]]
    return up - dn


def calc_vortex(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    return ta.vortex(df['high'], df['low'], df['close'], length=period)


def calc_dmi(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    return ta.adx(df['high'], df['low'], df['close'], length=period)


def _adx_cols(df, period):
    a = calc_adx(df, period)
    if a is None or a.empty:
        e = pd.Series(np.nan, index=df.index)
        return e, e, e
    adx = a[[c for c in a.columns if c.startswith('ADX')][0]]
    dip = a[[c for c in a.columns if c.startswith('DMP')][0]]
    dim = a[[c for c in a.columns if c.startswith('DMN')][0]]
    return adx, dip, dim


def sig_adx_strong_trend(df: pd.DataFrame, period: int = 14, threshold: float = 25.0) -> pd.Series:
    adx, _, _ = _adx_cols(df, period)
    sig = (adx.shift(1) <= threshold) & (adx > threshold)
    return _bool(sig, df.index)


def sig_adx_weak_trend(df: pd.DataFrame, period: int = 14, threshold: float = 20.0) -> pd.Series:
    adx, _, _ = _adx_cols(df, period)
    return _bool(adx < threshold, df.index)


def sig_di_cross_bullish(df: pd.DataFrame, period: int = 14) -> pd.Series:
    _, dip, dim = _adx_cols(df, period)
    sig = (dip.shift(1) < dim.shift(1)) & (dip >= dim)
    return _bool(sig, df.index)


def sig_di_cross_bearish(df: pd.DataFrame, period: int = 14) -> pd.Series:
    _, dip, dim = _adx_cols(df, period)
    sig = (dip.shift(1) > dim.shift(1)) & (dip <= dim)
    return _bool(sig, df.index)


def sig_adx_rising_with_trend(df: pd.DataFrame, period: int = 14, threshold: float = 25.0) -> pd.Series:
    adx, dip, dim = _adx_cols(df, period)
    sig = (adx > threshold) & (dip > dim)
    return _bool(sig, df.index)


def sig_aroon_bullish_cross(df: pd.DataFrame, period: int = 25) -> pd.Series:
    aroon = calc_aroon(df, period)
    if aroon is None or aroon.empty:
        return _bool(pd.Series(False, index=df.index), df.index)
    up = aroon[[c for c in aroon.columns if c.startswith('AROONU')][0]]
    dn = aroon[[c for c in aroon.columns if c.startswith('AROOND')][0]]
    sig = (up.shift(1) < dn.shift(1)) & (up >= dn)
    return _bool(sig, df.index)


def sig_aroon_bearish_cross(df: pd.DataFrame, period: int = 25) -> pd.Series:
    aroon = calc_aroon(df, period)
    if aroon is None or aroon.empty:
        return _bool(pd.Series(False, index=df.index), df.index)
    up = aroon[[c for c in aroon.columns if c.startswith('AROONU')][0]]
    dn = aroon[[c for c in aroon.columns if c.startswith('AROOND')][0]]
    sig = (dn.shift(1) < up.shift(1)) & (dn >= up)
    return _bool(sig, df.index)


def sig_vortex_bullish_cross(df: pd.DataFrame, period: int = 14) -> pd.Series:
    v = calc_vortex(df, period)
    if v is None or v.empty:
        return _bool(pd.Series(False, index=df.index), df.index)
    vp = v[[c for c in v.columns if c.startswith('VTXP')][0]]
    vm = v[[c for c in v.columns if c.startswith('VTXM')][0]]
    sig = (vp.shift(1) < vm.shift(1)) & (vp >= vm)
    return _bool(sig, df.index)


# ════════════════════════════════════════════════════════════
# SECTION G — VOLUME
# ════════════════════════════════════════════════════════════

def calc_obv(df: pd.DataFrame) -> pd.Series:
    return ta.obv(df['close'], df['volume'])


def calc_cmf(df: pd.DataFrame, period: int = 20) -> pd.Series:
    return ta.cmf(df['high'], df['low'], df['close'], df['volume'], length=period)


def calc_vwap(df: pd.DataFrame) -> pd.Series:
    typical = (df['high'] + df['low'] + df['close']) / 3
    return (typical * df['volume']).cumsum() / df['volume'].cumsum()


def calc_volume_sma(df: pd.DataFrame, period: int = 20) -> pd.Series:
    return df['volume'].rolling(period).mean()


def calc_volume_ratio(df: pd.DataFrame, period: int = 20) -> pd.Series:
    return df['volume'] / calc_volume_sma(df, period)


def calc_ad_line(df: pd.DataFrame) -> pd.Series:
    return ta.ad(df['high'], df['low'], df['close'], df['volume'])


def calc_force_index(df: pd.DataFrame, period: int = 2) -> pd.Series:
    return ta.efi(df['close'], df['volume'], length=period)


def calc_ease_of_movement(df: pd.DataFrame, period: int = 14) -> pd.Series:
    return ta.eom(df['high'], df['low'], df['close'], df['volume'], length=period)


def sig_volume_spike(df: pd.DataFrame, period: int = 20, mult: float = 2.0) -> pd.Series:
    avg = calc_volume_sma(df, period)
    sig = df['volume'] > mult * avg
    return _bool(sig, df.index)


def sig_volume_price_confirm_up(df: pd.DataFrame, period: int = 20) -> pd.Series:
    avg = calc_volume_sma(df, period)
    price_up = df['close'] > df['close'].shift(1)
    vol_up = df['volume'] > avg
    return _bool(price_up & vol_up, df.index)


def sig_volume_price_diverge_up(df: pd.DataFrame, period: int = 20) -> pd.Series:
    avg = calc_volume_sma(df, period)
    price_dn = df['close'] < df['close'].shift(1)
    vol_up = df['volume'] > avg
    return _bool(price_dn & vol_up, df.index)


def sig_obv_cross_above_sma(df: pd.DataFrame, period: int = 20) -> pd.Series:
    obv = calc_obv(df)
    obv_sma = obv.rolling(period).mean()
    sig = (obv.shift(1) < obv_sma.shift(1)) & (obv >= obv_sma)
    return _bool(sig, df.index)


def sig_cmf_cross_zero_up(df: pd.DataFrame, period: int = 20) -> pd.Series:
    cmf = calc_cmf(df, period)
    sig = (cmf.shift(1) <= 0) & (cmf > 0)
    return _bool(sig, df.index)


def sig_cmf_positive(df: pd.DataFrame, period: int = 20) -> pd.Series:
    cmf = calc_cmf(df, period)
    return _bool(cmf > 0, df.index)


def sig_mfi_oversold(df: pd.DataFrame, period: int = 14, threshold: float = 20.0) -> pd.Series:
    mfi = calc_mfi(df, period)
    sig = (mfi.shift(1) <= threshold) & (mfi > threshold)
    return _bool(sig, df.index)


def sig_accumulation_day(df: pd.DataFrame) -> pd.Series:
    sig = (df['close'] > df['open']) & (df['volume'] > df['volume'].shift(1))
    return _bool(sig, df.index)


def sig_distribution_day(df: pd.DataFrame) -> pd.Series:
    sig = (df['close'] < df['open']) & (df['volume'] > df['volume'].shift(1))
    return _bool(sig, df.index)


def sig_high_volume_breakout(df: pd.DataFrame, price_period: int = 20, vol_period: int = 20, vol_mult: float = 1.5) -> pd.Series:
    prev_high = df['close'].shift(1).rolling(price_period).max()
    avg_vol = calc_volume_sma(df, vol_period)
    sig = (df['close'] >= prev_high) & (df['volume'] > vol_mult * avg_vol)
    return _bool(sig, df.index)


# ════════════════════════════════════════════════════════════
# SECTION H — PRICE ACTION
# ════════════════════════════════════════════════════════════

def calc_52wk_high(df: pd.DataFrame) -> pd.Series:
    return df['close'].shift(1).rolling(252).max()


def calc_52wk_low(df: pd.DataFrame) -> pd.Series:
    return df['close'].shift(1).rolling(252).min()


def calc_n_period_high(df: pd.DataFrame, period: int) -> pd.Series:
    return df['close'].shift(1).rolling(period).max()


def calc_n_period_low(df: pd.DataFrame, period: int) -> pd.Series:
    return df['close'].shift(1).rolling(period).min()


def calc_pct_from_52wk_high(df: pd.DataFrame) -> pd.Series:
    high = calc_52wk_high(df)
    return (df['close'] - high) / high * 100


def calc_daily_return(df: pd.DataFrame) -> pd.Series:
    return (df['close'] - df['close'].shift(1)) / df['close'].shift(1) * 100


def calc_gap(df: pd.DataFrame) -> pd.Series:
    return (df['open'] - df['close'].shift(1)) / df['close'].shift(1) * 100


def calc_candle_body_pct(df: pd.DataFrame) -> pd.Series:
    rng = df['high'] - df['low']
    return (df['close'] - df['open']) / rng.replace(0, np.nan) * 100


def calc_upper_shadow_pct(df: pd.DataFrame) -> pd.Series:
    rng = df['high'] - df['low']
    body_top = df[['open', 'close']].max(axis=1)
    return (df['high'] - body_top) / rng.replace(0, np.nan) * 100


def calc_lower_shadow_pct(df: pd.DataFrame) -> pd.Series:
    rng = df['high'] - df['low']
    body_bot = df[['open', 'close']].min(axis=1)
    return (body_bot - df['low']) / rng.replace(0, np.nan) * 100


def calc_inside_day(df: pd.DataFrame) -> pd.Series:
    sig = (df['high'] < df['high'].shift(1)) & (df['low'] > df['low'].shift(1))
    return sig.fillna(False).astype(bool)


def calc_outside_day(df: pd.DataFrame) -> pd.Series:
    sig = (df['high'] > df['high'].shift(1)) & (df['low'] < df['low'].shift(1))
    return sig.fillna(False).astype(bool)


def calc_parabolic_sar(df: pd.DataFrame, step: float = 0.02, max_step: float = 0.2) -> pd.DataFrame:
    return ta.psar(df['high'], df['low'], df['close'], af0=step, af=step, max_af=max_step)


def _psar_value(df, step, max_step):
    psar = calc_parabolic_sar(df, step, max_step)
    if psar is None or psar.empty:
        return pd.Series(np.nan, index=df.index)
    long_col = [c for c in psar.columns if c.startswith('PSARl')]
    short_col = [c for c in psar.columns if c.startswith('PSARs')]
    long_v = psar[long_col[0]] if long_col else pd.Series(np.nan, index=df.index)
    short_v = psar[short_col[0]] if short_col else pd.Series(np.nan, index=df.index)
    return long_v.fillna(short_v)


def sig_52wk_high_breakout(df: pd.DataFrame) -> pd.Series:
    """Fires on the transition day only — when today's close exceeds the prior
    252-day max but yesterday's close did NOT exceed its prior 252-day max
    (i.e. yesterday wasn't already a new 52-week high)."""
    high = calc_52wk_high(df)
    today_break = df['close'] > high
    yesterday_break = df['close'].shift(1) > high.shift(1)
    sig = today_break & ~yesterday_break.fillna(False)
    return _bool(sig, df.index)


def sig_52wk_low_breakdown(df: pd.DataFrame) -> pd.Series:
    """Fires on the transition day only — symmetric to 52wk high breakout."""
    low = calc_52wk_low(df)
    today_break = df['close'] < low
    yesterday_break = df['close'].shift(1) < low.shift(1)
    sig = today_break & ~yesterday_break.fillna(False)
    return _bool(sig, df.index)


def sig_pct_below_52wk_high(df: pd.DataFrame, pct: float = 20.0) -> pd.Series:
    p = calc_pct_from_52wk_high(df)
    sig = p <= -pct
    return _bool(sig, df.index)


def sig_n_period_high_breakout(df: pd.DataFrame, period: int = 20) -> pd.Series:
    high = calc_n_period_high(df, period)
    sig = df['close'] > high
    return _bool(sig, df.index)


def sig_n_period_low_breakdown(df: pd.DataFrame, period: int = 20) -> pd.Series:
    low = calc_n_period_low(df, period)
    sig = df['close'] < low
    return _bool(sig, df.index)


def sig_gap_up(df: pd.DataFrame, min_pct: float = 1.0) -> pd.Series:
    gap = calc_gap(df)
    sig = gap >= min_pct
    return _bool(sig, df.index)


def sig_gap_down(df: pd.DataFrame, min_pct: float = 1.0) -> pd.Series:
    gap = calc_gap(df)
    sig = gap <= -min_pct
    return _bool(sig, df.index)


def sig_pct_dip_from_yesterday(df: pd.DataFrame, dip_pct: float = 3.0) -> pd.Series:
    ret = calc_daily_return(df)
    sig = ret <= -dip_pct
    return _bool(sig, df.index)


def sig_pct_rally_from_yesterday(df: pd.DataFrame, rally_pct: float = 3.0) -> pd.Series:
    ret = calc_daily_return(df)
    sig = ret >= rally_pct
    return _bool(sig, df.index)


def sig_psar_flip_bullish(df: pd.DataFrame, step: float = 0.02, max_step: float = 0.2) -> pd.Series:
    psar = calc_parabolic_sar(df, step, max_step)
    if psar is None or psar.empty:
        return _bool(pd.Series(False, index=df.index), df.index)
    rev_col = [c for c in psar.columns if c.startswith('PSARr')]
    long_col = [c for c in psar.columns if c.startswith('PSARl')]
    if rev_col and long_col:
        sig = (psar[rev_col[0]] == 1) & psar[long_col[0]].notna()
        return _bool(sig, df.index)
    val = _psar_value(df, step, max_step)
    prev_below = df['close'].shift(1) < val.shift(1)
    now_above = df['close'] > val
    return _bool(prev_below & now_above, df.index)


def sig_psar_flip_bearish(df: pd.DataFrame, step: float = 0.02, max_step: float = 0.2) -> pd.Series:
    psar = calc_parabolic_sar(df, step, max_step)
    if psar is None or psar.empty:
        return _bool(pd.Series(False, index=df.index), df.index)
    rev_col = [c for c in psar.columns if c.startswith('PSARr')]
    short_col = [c for c in psar.columns if c.startswith('PSARs')]
    if rev_col and short_col:
        sig = (psar[rev_col[0]] == 1) & psar[short_col[0]].notna()
        return _bool(sig, df.index)
    val = _psar_value(df, step, max_step)
    prev_above = df['close'].shift(1) > val.shift(1)
    now_below = df['close'] < val
    return _bool(prev_above & now_below, df.index)


def sig_price_above_psar(df: pd.DataFrame) -> pd.Series:
    val = _psar_value(df, 0.02, 0.2)
    return _bool(df['close'] > val, df.index)


def sig_inside_day_breakout(df: pd.DataFrame) -> pd.Series:
    inside = calc_inside_day(df)
    sig = inside.shift(1).fillna(False) & (df['close'] > df['high'].shift(1))
    return _bool(sig, df.index)


def sig_hammer_candle(df: pd.DataFrame, min_shadow_ratio: float = 2.0) -> pd.Series:
    body = (df['close'] - df['open']).abs()
    body_bot = df[['open', 'close']].min(axis=1)
    lower_shadow = body_bot - df['low']
    safe_body = body.replace(0, np.nan)
    sig = lower_shadow >= min_shadow_ratio * safe_body
    return _bool(sig, df.index)


def sig_shooting_star_candle(df: pd.DataFrame, min_shadow_ratio: float = 2.0) -> pd.Series:
    body = (df['close'] - df['open']).abs()
    body_top = df[['open', 'close']].max(axis=1)
    upper_shadow = df['high'] - body_top
    safe_body = body.replace(0, np.nan)
    sig = upper_shadow >= min_shadow_ratio * safe_body
    return _bool(sig, df.index)


def sig_engulfing_bullish(df: pd.DataFrame) -> pd.Series:
    prev_red = df['close'].shift(1) < df['open'].shift(1)
    today_green = df['close'] > df['open']
    engulf = (df['open'] <= df['close'].shift(1)) & (df['close'] >= df['open'].shift(1))
    sig = prev_red & today_green & engulf
    return _bool(sig, df.index)


def sig_engulfing_bearish(df: pd.DataFrame) -> pd.Series:
    prev_green = df['close'].shift(1) > df['open'].shift(1)
    today_red = df['close'] < df['open']
    engulf = (df['open'] >= df['close'].shift(1)) & (df['close'] <= df['open'].shift(1))
    sig = prev_green & today_red & engulf
    return _bool(sig, df.index)

def _bool_io(series: pd.Series, index: pd.Index) -> pd.Series:
    """Coerce series to bool, aligned, NaN->False."""
    s = pd.Series(series, index=index) if not isinstance(series, pd.Series) else series.reindex(index)
    return s.fillna(False).astype(bool)


def _crosses_above(a: pd.Series, b: pd.Series) -> pd.Series:
    a_prev = a.shift(1)
    b_prev = b.shift(1)
    return (a_prev <= b_prev) & (a > b)


def _crosses_below(a: pd.Series, b: pd.Series) -> pd.Series:
    a_prev = a.shift(1)
    b_prev = b.shift(1)
    return (a_prev >= b_prev) & (a < b)


# ════════════════════════════════════════════════════════════
# SECTION I — ICHIMOKU CLOUD
# ════════════════════════════════════════════════════════════

def calc_ichimoku(df: pd.DataFrame, tenkan: int = 9, kijun: int = 26, senkou_b: int = 52) -> pd.DataFrame:
    """Ichimoku — returns ISA, ISB, ITS, IKS, ICS aligned to df.index."""
    high = df["high"]
    low = df["low"]
    close = df["close"]

    tenkan_sen = (high.rolling(tenkan).max() + low.rolling(tenkan).min()) / 2.0
    kijun_sen = (high.rolling(kijun).max() + low.rolling(kijun).min()) / 2.0
    span_a = ((tenkan_sen + kijun_sen) / 2.0).shift(kijun)
    span_b = ((high.rolling(senkou_b).max() + low.rolling(senkou_b).min()) / 2.0).shift(kijun)
    chikou = close.shift(-kijun)

    out = pd.DataFrame(
        {
            "ITS": tenkan_sen,
            "IKS": kijun_sen,
            "ISA": span_a,
            "ISB": span_b,
            "ICS": chikou,
        },
        index=df.index,
    )
    return out


def _ichi(df: pd.DataFrame) -> pd.DataFrame:
    if {"ITS", "IKS", "ISA", "ISB", "ICS"}.issubset(df.columns):
        return df
    return calc_ichimoku(df)


def sig_ichimoku_tk_cross_bullish(df: pd.DataFrame) -> pd.Series:
    ich = _ichi(df)
    return _bool_io(_crosses_above(ich["ITS"], ich["IKS"]), df.index)


def sig_ichimoku_tk_cross_bearish(df: pd.DataFrame) -> pd.Series:
    ich = _ichi(df)
    return _bool_io(_crosses_below(ich["ITS"], ich["IKS"]), df.index)


def sig_ichimoku_price_above_cloud(df: pd.DataFrame) -> pd.Series:
    ich = _ichi(df)
    cloud_top = ich[["ISA", "ISB"]].max(axis=1)
    return _bool_io(df["close"] > cloud_top, df.index)


def sig_ichimoku_price_below_cloud(df: pd.DataFrame) -> pd.Series:
    ich = _ichi(df)
    cloud_bot = ich[["ISA", "ISB"]].min(axis=1)
    return _bool_io(df["close"] < cloud_bot, df.index)


def sig_ichimoku_cloud_breakout_up(df: pd.DataFrame) -> pd.Series:
    ich = _ichi(df)
    cloud_top = ich[["ISA", "ISB"]].max(axis=1)
    return _bool_io(_crosses_above(df["close"], cloud_top), df.index)


def sig_ichimoku_cloud_breakout_down(df: pd.DataFrame) -> pd.Series:
    ich = _ichi(df)
    cloud_bot = ich[["ISA", "ISB"]].min(axis=1)
    return _bool_io(_crosses_below(df["close"], cloud_bot), df.index)


def sig_ichimoku_bullish_cloud(df: pd.DataFrame) -> pd.Series:
    ich = _ichi(df)
    return _bool_io(ich["ISA"] > ich["ISB"], df.index)


def sig_ichimoku_chikou_above_price(df: pd.DataFrame, kijun: int = 26) -> pd.Series:
    """Chikou (close shifted -kijun) vs price kijun ago — compare at evaluable t."""
    # Chikou at time t = close[t+kijun]. To avoid look-ahead, the comparison
    # is only knowable at t = current_index - kijun. Equivalently: today's close
    # compared with close kijun bars ago (>) — which is the standard interpretation.
    close = df["close"]
    cond = close > close.shift(kijun)
    return _bool_io(cond, df.index)


def sig_ichimoku_full_bullish(df: pd.DataFrame) -> pd.Series:
    a = sig_ichimoku_tk_cross_bullish(df)
    b = sig_ichimoku_price_above_cloud(df)
    c = sig_ichimoku_bullish_cloud(df)
    return _bool_io(a & b & c, df.index)


# ════════════════════════════════════════════════════════════
# SECTION J — CALENDAR
# ════════════════════════════════════════════════════════════

def _idx(df: pd.DataFrame) -> pd.DatetimeIndex:
    return pd.DatetimeIndex(df.index)


def sig_weekday(df: pd.DataFrame, weekday: int) -> pd.Series:
    idx = _idx(df)
    return _bool_io(pd.Series(idx.weekday == weekday, index=df.index), df.index)


def sig_first_trading_day_of_month(df: pd.DataFrame) -> pd.Series:
    idx = _idx(df)
    period = idx.to_period("M")
    s = pd.Series(period, index=df.index)
    cond = s != s.shift(1)
    cond.iloc[0] = True
    return _bool_io(cond, df.index)


def sig_last_trading_day_of_month(df: pd.DataFrame) -> pd.Series:
    idx = _idx(df)
    period = idx.to_period("M")
    s = pd.Series(period, index=df.index)
    cond = s != s.shift(-1)
    cond.iloc[-1] = True
    return _bool_io(cond, df.index)


def sig_day_of_month(df: pd.DataFrame, day: int) -> pd.Series:
    """Closest trading day at or after day N each month."""
    idx = _idx(df)
    out = pd.Series(False, index=df.index)
    months = pd.Series(idx.to_period("M"), index=df.index)
    for _, group in months.groupby(months):
        gidx = group.index
        target_candidates = [d for d in gidx if d.day >= day]
        if target_candidates:
            out.loc[target_candidates[0]] = True
        elif len(gidx) > 0:
            out.loc[gidx[-1]] = True
    return _bool_io(out, df.index)


def sig_first_trading_day_of_quarter(df: pd.DataFrame) -> pd.Series:
    idx = _idx(df)
    period = idx.to_period("Q")
    s = pd.Series(period, index=df.index)
    cond = s != s.shift(1)
    cond.iloc[0] = True
    return _bool_io(cond, df.index)


def sig_last_trading_day_of_quarter(df: pd.DataFrame) -> pd.Series:
    idx = _idx(df)
    period = idx.to_period("Q")
    s = pd.Series(period, index=df.index)
    cond = s != s.shift(-1)
    cond.iloc[-1] = True
    return _bool_io(cond, df.index)


def sig_month_of_year(df: pd.DataFrame, month: int) -> pd.Series:
    idx = _idx(df)
    return _bool_io(pd.Series(idx.month == month, index=df.index), df.index)


def _last_thursday(year: int, month: int) -> pd.Timestamp:
    cal = calendar.monthcalendar(year, month)
    # weekday 3 == Thursday
    thursdays = [week[3] for week in cal if week[3] != 0]
    return pd.Timestamp(year=year, month=month, day=thursdays[-1])


def sig_days_before_fno_expiry(df: pd.DataFrame, n_days: int) -> pd.Series:
    """Fires n trading days before last Thursday of month (the F&O expiry)."""
    idx = _idx(df)
    out = pd.Series(False, index=df.index)
    # For each (year, month) present, find the trading day at or before the last
    # Thursday, then back up n_days within the trading-day index.
    months = pd.Series(list(zip(idx.year, idx.month)), index=df.index)
    seen = set()
    for ym in months:
        if ym in seen:
            continue
        seen.add(ym)
        y, m = ym
        try:
            expiry = _last_thursday(y, m)
        except Exception:
            continue
        # find trading day on or before expiry
        positions = idx.get_indexer([expiry], method="pad")
        pos = int(positions[0])
        if pos < 0:
            continue
        target_pos = pos - n_days
        if 0 <= target_pos < len(idx):
            target_ts = idx[target_pos]
            # only mark if target_ts is in same month or earlier (sanity)
            out.loc[target_ts] = True
    return _bool_io(out, df.index)


# ════════════════════════════════════════════════════════════
# SECTION K — COMPOSITE / COMBINATION HELPERS
# ════════════════════════════════════════════════════════════

def _align(*signals: pd.Series) -> list[pd.Series]:
    if not signals:
        return []
    idx = signals[0].index
    return [s.reindex(idx).fillna(False).astype(bool) for s in signals]


def combine_and(*signals: pd.Series) -> pd.Series:
    if not signals:
        return pd.Series(dtype=bool)
    aligned = _align(*signals)
    out = aligned[0].copy()
    for s in aligned[1:]:
        out = out & s
    return _bool_io(out, signals[0].index)


def combine_or(*signals: pd.Series) -> pd.Series:
    if not signals:
        return pd.Series(dtype=bool)
    aligned = _align(*signals)
    out = aligned[0].copy()
    for s in aligned[1:]:
        out = out | s
    return _bool_io(out, signals[0].index)


def combine_not(signal: pd.Series) -> pd.Series:
    return _bool_io(~signal.fillna(False).astype(bool), signal.index)


def require_n_of(*signals: pd.Series, n: int) -> pd.Series:
    if not signals:
        return pd.Series(dtype=bool)
    aligned = _align(*signals)
    stacked = pd.concat([s.astype(int) for s in aligned], axis=1)
    counts = stacked.sum(axis=1)
    return _bool_io(counts >= n, signals[0].index)


def add_cooldown(signal: pd.Series, cooldown_days: int) -> pd.Series:
    """After signal fires, suppress further fires for cooldown_days bars."""
    s = signal.fillna(False).astype(bool).values
    out = np.zeros(len(s), dtype=bool)
    block_until = -1
    for i in range(len(s)):
        if i <= block_until:
            continue
        if s[i]:
            out[i] = True
            block_until = i + cooldown_days
    return _bool_io(pd.Series(out, index=signal.index), signal.index)


# ════════════════════════════════════════════════════════════
# SECTION N — SQUEEZE MOMENTUM (TTM SQUEEZE)
# ════════════════════════════════════════════════════════════

def calc_squeeze_momentum(
    df: pd.DataFrame,
    bb_period: int = 20,
    bb_mult: float = 2.0,
    kc_period: int = 20,
    kc_mult: float = 1.5,
) -> pd.DataFrame:
    """TTM Squeeze: returns squeeze_on (bool) and momentum (oscillator)."""
    close = df["close"]
    high = df["high"]
    low = df["low"]

    bb_ma = close.rolling(bb_period).mean()
    bb_std = close.rolling(bb_period).std(ddof=0)
    bb_upper = bb_ma + bb_mult * bb_std
    bb_lower = bb_ma - bb_mult * bb_std

    tr = pd.concat(
        [
            (high - low),
            (high - close.shift(1)).abs(),
            (low - close.shift(1)).abs(),
        ],
        axis=1,
    ).max(axis=1)
    kc_ma = close.rolling(kc_period).mean()
    atr_like = tr.rolling(kc_period).mean()
    kc_upper = kc_ma + kc_mult * atr_like
    kc_lower = kc_ma - kc_mult * atr_like

    squeeze_on = (bb_lower > kc_lower) & (bb_upper < kc_upper)

    # Momentum: linreg of (close - mid) where mid = avg(highest_high, lowest_low, sma_close)
    highest = high.rolling(kc_period).max()
    lowest = low.rolling(kc_period).min()
    mid = ((highest + lowest) / 2.0 + close.rolling(kc_period).mean()) / 2.0
    delta = close - mid

    def _linreg_last(arr: np.ndarray) -> float:
        n = len(arr)
        if np.isnan(arr).any():
            return np.nan
        x = np.arange(n, dtype=float)
        slope, intercept = np.polyfit(x, arr, 1)
        return float(slope * (n - 1) + intercept)

    momentum = delta.rolling(kc_period).apply(_linreg_last, raw=True)

    return pd.DataFrame(
        {"squeeze_on": squeeze_on.fillna(False).astype(bool), "momentum": momentum},
        index=df.index,
    )


def _squeeze(df: pd.DataFrame) -> pd.DataFrame:
    if {"squeeze_on", "momentum"}.issubset(df.columns):
        return df
    return calc_squeeze_momentum(df)


def sig_squeeze_fire_up(df: pd.DataFrame) -> pd.Series:
    sq = _squeeze(df)
    on_prev = sq["squeeze_on"].shift(1).fillna(False).astype(bool)
    fired = on_prev & (~sq["squeeze_on"])
    cond = fired & (sq["momentum"] > 0)
    return _bool_io(cond, df.index)


def sig_squeeze_fire_down(df: pd.DataFrame) -> pd.Series:
    sq = _squeeze(df)
    on_prev = sq["squeeze_on"].shift(1).fillna(False).astype(bool)
    fired = on_prev & (~sq["squeeze_on"])
    cond = fired & (sq["momentum"] < 0)
    return _bool_io(cond, df.index)


# ════════════════════════════════════════════════════════════
# SECTION O — PIVOT POINTS / SUPPORT-RESISTANCE
# ════════════════════════════════════════════════════════════

def calc_pivot_points(df: pd.DataFrame) -> pd.DataFrame:
    """Classic pivots from PREVIOUS day OHLC — no look-ahead."""
    prev_high = df["high"].shift(1)
    prev_low = df["low"].shift(1)
    prev_close = df["close"].shift(1)

    pp = (prev_high + prev_low + prev_close) / 3.0
    r1 = 2 * pp - prev_low
    s1 = 2 * pp - prev_high
    r2 = pp + (prev_high - prev_low)
    s2 = pp - (prev_high - prev_low)
    r3 = prev_high + 2 * (pp - prev_low)
    s3 = prev_low - 2 * (prev_high - pp)

    return pd.DataFrame(
        {"PP": pp, "R1": r1, "R2": r2, "R3": r3, "S1": s1, "S2": s2, "S3": s3},
        index=df.index,
    )


def _pivots(df: pd.DataFrame) -> pd.DataFrame:
    if {"PP", "R1", "S1"}.issubset(df.columns):
        return df
    return calc_pivot_points(df)


def sig_price_cross_above_pivot(df: pd.DataFrame) -> pd.Series:
    piv = _pivots(df)
    return _bool_io(_crosses_above(df["close"], piv["PP"]), df.index)


def sig_price_at_support(df: pd.DataFrame, tolerance_pct: float = 0.5) -> pd.Series:
    piv = _pivots(df)
    s1 = piv["S1"]
    tol = s1.abs() * (tolerance_pct / 100.0)
    cond = (df["close"] - s1).abs() <= tol
    return _bool_io(cond, df.index)


def sig_price_at_resistance(df: pd.DataFrame, tolerance_pct: float = 0.5) -> pd.Series:
    piv = _pivots(df)
    r1 = piv["R1"]
    tol = r1.abs() * (tolerance_pct / 100.0)
    cond = (df["close"] - r1).abs() <= tol
    return _bool_io(cond, df.index)

# ============================================================
# SIGNAL_REGISTRY — name -> callable
# ============================================================
SIGNAL_REGISTRY = {
    # Moving Average signals
    "price_cross_above_sma": sig_price_cross_above_sma,
    "price_cross_below_sma": sig_price_cross_below_sma,
    "price_above_sma": sig_price_above_sma,
    "price_below_sma": sig_price_below_sma,
    "golden_cross_sma": sig_sma_cross_above_sma,
    "death_cross_sma": sig_sma_cross_below_sma,
    "golden_cross_ema": sig_ema_cross_above_ema,
    "death_cross_ema": sig_ema_cross_below_ema,
    "price_above_vwma": sig_price_above_vwma,
    "hma_turn_up": sig_hma_direction_up,
    # RSI
    "rsi_cross_below": sig_rsi_cross_below,
    "rsi_cross_above": sig_rsi_cross_above,
    "rsi_in_range": sig_rsi_in_range,
    "rsi_below_level": sig_rsi_below_level,
    "rsi_divergence_bullish": sig_rsi_divergence_bullish,
    "rsi_divergence_bearish": sig_rsi_divergence_bearish,
    # Stochastic / StochRSI
    "stoch_cross_above": sig_stoch_cross_above,
    "stoch_cross_below": sig_stoch_cross_below,
    "stochrsi_cross_above": sig_stochrsi_cross_above,
    # CCI / Williams / MFI
    "cci_cross_above": sig_cci_cross_above,
    "cci_cross_below": sig_cci_cross_below,
    "williams_r_cross_above": sig_williams_r_cross_above,
    "mfi_oversold": sig_mfi_oversold,
    "mfi_cross_below": sig_mfi_cross_below,
    # ROC / Momentum / AO
    "roc_cross_zero_up": sig_roc_cross_zero_up,
    "momentum_cross_zero_up": sig_momentum_cross_zero_up,
    "ao_cross_zero_up": sig_ao_cross_zero_up,
    "ao_cross_zero_down": sig_ao_cross_zero_down,
    # MACD
    "macd_cross_above_signal": sig_macd_cross_above_signal,
    "macd_cross_below_signal": sig_macd_cross_below_signal,
    "macd_histogram_cross_zero_up": sig_macd_histogram_cross_zero_up,
    "macd_histogram_cross_zero_down": sig_macd_histogram_cross_zero_down,
    "macd_line_cross_zero_up": sig_macd_line_cross_zero_up,
    "macd_line_cross_zero_down": sig_macd_line_cross_zero_down,
    "macd_divergence_bullish": sig_macd_divergence_bullish,
    "macd_histogram_expanding_bullish": sig_macd_histogram_expanding_bullish,
    # Bollinger Bands
    "bb_lower_touch": sig_bb_lower_touch,
    "bb_upper_touch": sig_bb_upper_touch,
    "bb_breakout_above": sig_bb_breakout_above,
    "bb_breakout_below": sig_bb_breakout_below,
    "bb_squeeze": sig_bb_squeeze,
    "bb_squeeze_breakout_up": sig_bb_squeeze_breakout_up,
    "bb_w_pattern": sig_bb_w_pattern,
    "bb_mean_reversion": sig_bb_mean_reversion,
    # Volatility
    "supertrend_flip_bullish": sig_supertrend_flip_bullish,
    "supertrend_flip_bearish": sig_supertrend_flip_bearish,
    "price_above_supertrend": sig_price_above_supertrend,
    "keltner_breakout_above": sig_keltner_breakout_above,
    "keltner_breakout_below": sig_keltner_breakout_below,
    "donchian_breakout_above": sig_donchian_breakout_above,
    "donchian_breakout_below": sig_donchian_breakout_below,
    "atr_expansion": sig_atr_expansion,
    "atr_contraction": sig_atr_contraction,
    "low_volatility_period": sig_low_volatility_period,
    # Trend Strength
    "adx_strong_trend": sig_adx_strong_trend,
    "adx_weak_trend": sig_adx_weak_trend,
    "di_cross_bullish": sig_di_cross_bullish,
    "di_cross_bearish": sig_di_cross_bearish,
    "adx_rising_with_trend": sig_adx_rising_with_trend,
    "aroon_bullish_cross": sig_aroon_bullish_cross,
    "aroon_bearish_cross": sig_aroon_bearish_cross,
    "vortex_bullish_cross": sig_vortex_bullish_cross,
    # Volume
    "volume_spike": sig_volume_spike,
    "volume_price_confirm_up": sig_volume_price_confirm_up,
    "volume_price_diverge_up": sig_volume_price_diverge_up,
    "obv_cross_above_sma": sig_obv_cross_above_sma,
    "cmf_cross_zero_up": sig_cmf_cross_zero_up,
    "cmf_positive": sig_cmf_positive,
    "high_volume_breakout": sig_high_volume_breakout,
    "accumulation_day": sig_accumulation_day,
    "distribution_day": sig_distribution_day,
    # Price Action
    "52wk_high_breakout": sig_52wk_high_breakout,
    "52wk_low_breakdown": sig_52wk_low_breakdown,
    "n_period_high_breakout": sig_n_period_high_breakout,
    "n_period_low_breakdown": sig_n_period_low_breakdown,
    "pct_below_52wk_high": sig_pct_below_52wk_high,
    "gap_up": sig_gap_up,
    "gap_down": sig_gap_down,
    "pct_dip_from_yesterday": sig_pct_dip_from_yesterday,
    "pct_rally_from_yesterday": sig_pct_rally_from_yesterday,
    "psar_flip_bullish": sig_psar_flip_bullish,
    "psar_flip_bearish": sig_psar_flip_bearish,
    "price_above_psar": sig_price_above_psar,
    "inside_day_breakout": sig_inside_day_breakout,
    "hammer_candle": sig_hammer_candle,
    "shooting_star_candle": sig_shooting_star_candle,
    "engulfing_bullish": sig_engulfing_bullish,
    "engulfing_bearish": sig_engulfing_bearish,
    # Ichimoku
    "ichimoku_tk_cross_bullish": sig_ichimoku_tk_cross_bullish,
    "ichimoku_tk_cross_bearish": sig_ichimoku_tk_cross_bearish,
    "ichimoku_price_above_cloud": sig_ichimoku_price_above_cloud,
    "ichimoku_price_below_cloud": sig_ichimoku_price_below_cloud,
    "ichimoku_cloud_breakout_up": sig_ichimoku_cloud_breakout_up,
    "ichimoku_cloud_breakout_down": sig_ichimoku_cloud_breakout_down,
    "ichimoku_bullish_cloud": sig_ichimoku_bullish_cloud,
    "ichimoku_chikou_above_price": sig_ichimoku_chikou_above_price,
    "ichimoku_full_bullish": sig_ichimoku_full_bullish,
    # Calendar
    "monday": lambda df: sig_weekday(df, 0),
    "tuesday": lambda df: sig_weekday(df, 1),
    "wednesday": lambda df: sig_weekday(df, 2),
    "thursday": lambda df: sig_weekday(df, 3),
    "friday": lambda df: sig_weekday(df, 4),
    "weekday": sig_weekday,
    "first_day_of_month": sig_first_trading_day_of_month,
    "last_day_of_month": sig_last_trading_day_of_month,
    "first_day_of_quarter": sig_first_trading_day_of_quarter,
    "last_day_of_quarter": sig_last_trading_day_of_quarter,
    "day_of_month": sig_day_of_month,
    "month_of_year": sig_month_of_year,
    "days_before_fno_expiry": sig_days_before_fno_expiry,
    # Squeeze Momentum
    "squeeze_fire_up": sig_squeeze_fire_up,
    "squeeze_fire_down": sig_squeeze_fire_down,
    # Pivot Points
    "price_cross_above_pivot": sig_price_cross_above_pivot,
    "price_at_support": sig_price_at_support,
    "price_at_resistance": sig_price_at_resistance,
}
