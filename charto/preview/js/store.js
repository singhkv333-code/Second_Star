/* Charto preview — session store.
 *
 * A reload is a refresh of the PRICE, not of the session. Everything the user
 * built up — the interval they chose, the indicators they added, the levels the
 * chat drew, the conversation itself — survives; only the bars are re-fetched,
 * landing the chart back at the live edge.
 *
 * Drawings persist separately (drawings.js owns its own key, since it saves on
 * every drag). Everything else routes through here so there is one prefix and
 * one place a quota error can be swallowed.
 */
"use strict";

/* What a symbol is quoted in, and which clock its bars sit on.
 *
 * One place decides both, because the axis, the readout, the drawing labels
 * and the chat all have to agree. The dataserver already buckets crypto at
 * UTC midnight (session_for), so a +05:30 axis on those bars would draw a
 * "daily" candle that appears to open at 05:30 — the shift has to match the
 * anchor the bars were folded on. INR grouping is wrong for the same reason:
 * en-IN renders six-figure BTC as 1,00,000.
 */
const Sym = (() => {
  const S = (new URLSearchParams(location.search).get("symbol")
             || "RELIANCE").toUpperCase();
  const crypto = /(USDT|-USD)$/.test(S);
  const MCX = new Set(["GOLD", "GOLDM", "SILVER", "SILVERM", "CRUDEOIL",
                       "NATURALGAS", "COPPER", "ZINC", "ALUMINIUM"]);
  const CDS = new Set(["USDINR", "EURINR", "GBPINR", "JPYINR"]);
  const BSE = new Set(["SENSEX", "BANKEX"]);
  // The venue is not decoration: it rides into the chat context envelope, so
  // a hardcoded "NSE" told the model that BTCUSDT trades on the NSE.
  const venue = /USDT$/.test(S) ? "BYBIT" : /-USD$/.test(S) ? "COINBASE"
    : MCX.has(S) ? "MCX" : CDS.has(S) ? "NSE CDS" : BSE.has(S) ? "BSE" : "NSE";
  return {
    name: S,
    isCrypto: crypto,
    venue,
    feed: /USDT$/.test(S) ? "bybit 1-min" : /-USD$/.test(S) ? "coinbase 1-min"
      : "kite 1-min",
    tz: crypto ? 0 : 19800,              // seconds added so the axis reads local
    cur: crypto ? "$" : "₹",
    locale: crypto ? "en-US" : "en-IN",
    num(n, opts) {
      return Number(n).toLocaleString(this.locale,
                                      opts || { maximumFractionDigits: 2 });
    },
    price(n, opts) { return this.cur + this.num(n, opts); },
  };
})();

const Store = (() => {
  const PREFIX = "charto:";
  // Per-company sessions: the conversation and what chat drew belong to the
  // symbol being viewed (?symbol=X), so opening a company starts fresh and
  // returning to one restores it. RELIANCE keeps the legacy unscoped keys.
  const SYM = (new URLSearchParams(location.search).get("symbol")
               || "RELIANCE").toUpperCase();
  const SCOPED = new Set(["chat", "scene"]);
  const k = (key) => (SYM !== "RELIANCE" && SCOPED.has(key))
    ? `${SYM}:${key}` : key;

  return {
    get(key, fallback) {
      try {
        const raw = localStorage.getItem(PREFIX + k(key));
        return raw === null ? fallback : JSON.parse(raw);
      } catch {
        return fallback;   // corrupt or blocked → behave like a fresh session
      }
    },
    set(key, value) {
      try { localStorage.setItem(PREFIX + k(key), JSON.stringify(value)); }
      catch { /* private mode / quota — persistence is a convenience, not a contract */ }
    },
    del(key) {
      try { localStorage.removeItem(PREFIX + k(key)); } catch {}
    },
  };
})();
