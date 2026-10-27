import { NIFTY_AS_OF, NIFTY_DAILY, NIFTY_WATCH } from "./nifty-snapshot";

export { NIFTY_AS_OF, NIFTY_DAILY, NIFTY_WATCH };
export const NIFTY_1D = NIFTY_DAILY.map(([t, o, h, l, c]) => [t, o, h, l, c] as const);
export const NIFTY_LAST = NIFTY_DAILY[NIFTY_DAILY.length - 1]!;
export const NIFTY_PREV = NIFTY_DAILY[NIFTY_DAILY.length - 2]!;
export const NIFTY_CHANGE = NIFTY_LAST[4] - NIFTY_PREV[4];
export const NIFTY_CHANGE_PCT = NIFTY_CHANGE / NIFTY_PREV[4] * 100;
export const NIFTY_RANGE = {
  high: Math.max(...NIFTY_DAILY.slice(-252).map((b) => b[2])),
  low: Math.min(...NIFTY_DAILY.slice(-252).map((b) => b[3])),
};
const prior = NIFTY_DAILY.slice(-21, -1);
export const NIFTY_LEVELS = [
  { role: "H", price: Math.max(...prior.map((b) => b[2])) },
  { role: "L", price: Math.min(...prior.map((b) => b[3])) },
] as const;
export const NIFTY_EMA20 = (() => {
  const k = 2 / 21;
  let ema = NIFTY_DAILY[0]![4];
  return NIFTY_DAILY.map(([t, , , , c]) => {
    ema = c * k + ema * (1 - k);
    return [t, Math.round(ema * 100) / 100] as const;
  });
})();
export const NIFTY_RSI14 = (() => {
  const closes = NIFTY_DAILY.map((b) => b[4]);
  let gains = 0, losses = 0;
  for (let i = 1; i <= 14; i++) {
    const move = closes[i]! - closes[i - 1]!;
    gains += Math.max(0, move);
    losses += Math.max(0, -move);
  }
  gains /= 14; losses /= 14;
  for (let i = 15; i < closes.length; i++) {
    const move = closes[i]! - closes[i - 1]!;
    gains = (gains * 13 + Math.max(0, move)) / 14;
    losses = (losses * 13 + Math.max(0, -move)) / 14;
  }
  return Math.round((losses ? 100 - 100 / (1 + gains / losses) : 100) * 10) / 10;
})();

/** A transparent long-only EMA(20/50) illustration: signals fill next open. */
export const NIFTY_LAB = (() => {
  let fast = NIFTY_DAILY[0]![4], slow = fast, held = false, cash = 100, units = 0, trades = 0, peak = 100, maxDd = 0;
  const equity: number[] = [];
  for (let i = 0; i < NIFTY_DAILY.length; i++) {
    const bar = NIFTY_DAILY[i]!;
    if (i > 0) {
      const previous = NIFTY_DAILY[i - 1]![4];
      const nextFast = previous * (2 / 21) + fast * (19 / 21);
      const nextSlow = previous * (2 / 51) + slow * (49 / 51);
      const enter = fast <= slow && nextFast > nextSlow;
      const exit = fast >= slow && nextFast < nextSlow;
      fast = nextFast; slow = nextSlow;
      if (enter && !held) { units = cash * (1 - 0.0016) / bar[1]; cash = 0; held = true; trades++; }
      else if (exit && held) { cash = units * bar[1] * (1 - 0.0016); units = 0; held = false; }
    }
    const value = cash + units * bar[4];
    equity.push(Math.round(value * 100) / 100);
    peak = Math.max(peak, value);
    maxDd = Math.max(maxDd, (peak - value) / peak * 100);
  }
  const ret = equity[equity.length - 1]! - 100;
  const buyHold = (NIFTY_LAST[4] / NIFTY_DAILY[0]![4] - 1) * 100;
  return { equity, ret, buy_hold: buyHold, trades, max_dd: maxDd, verdict: "Illustrative only" };
})();
