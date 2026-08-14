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
  const MCX = new Set(["GOLD", "GOLDM", "SILVER", "SILVERM", "CRUDEOIL",
                       "NATURALGAS", "COPPER", "ZINC", "ALUMINIUM"]);
  const CDS = new Set(["USDINR", "EURINR", "GBPINR", "JPYINR"]);
  const BSE = new Set(["SENSEX", "BANKEX"]);

  /* A descriptor for ANY symbol, not just the page's own. A secondary pane
   * can now hold a different instrument, and it has to be quoted, clocked and
   * labelled as ITSELF — a Bitcoin pane on an INR locale renders 1,00,000 and
   * folds its daily candles on a 05:30 open. One function, four callers. */
  function of(name) {
    const S = String(name || "RELIANCE").toUpperCase();
    const crypto = /(USDT|-USD)$/.test(S);
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
      tz: crypto ? 0 : 19800,            // seconds added so the axis reads local
      cur: crypto ? "$" : "₹",
      // The ISO code, for the badge on the price scale, and the name of the
      // clock the axis is on, for the badge on the time scale. Both are STATED
      // rather than offered: a chart that lets you re-clock it has to re-fold
      // every bar, and a currency picker on a scale we do not convert would be
      // a control that lies. A Bybit pair settles in USDT and a Coinbase one
      // in USD, and saying "USD" for both would be the same kind of lie.
      code: /USDT$/.test(S) ? "USDT" : crypto ? "USD" : "INR",
      // Crypto genuinely IS folded on UTC midnight (see the note above), so
      // this reads the clock the bars are actually on rather than claiming
      // IST for everything.
      tzLabel: crypto ? "UTC" : "UTC+5:30",
      locale: crypto ? "en-US" : "en-IN",
      num(n, opts) {
        return Number(n).toLocaleString(this.locale,
                                        opts || { maximumFractionDigits: 2 });
      },
      price(n, opts) { return this.cur + this.num(n, opts); },
    };
  }

  // the page's own symbol stays the default export shape, so every existing
  // `Sym.venue` / `Sym.num()` call site is untouched
  return Object.assign(of(new URLSearchParams(location.search).get("symbol")), { of });
})();

const Store = (() => {
  const PREFIX = "charto:";
  // Per-company sessions: the conversation and what chat drew belong to the
  // symbol being viewed (?symbol=X), so opening a company starts fresh and
  // returning to one restores it. RELIANCE keeps the legacy unscoped keys.
  const SYM = (new URLSearchParams(location.search).get("symbol")
               || "RELIANCE").toUpperCase();
  // "vp" rides with "scene" because it IS scene state: a volume profile is a
  // window over one instrument's own bars. Left unscoped it followed the user
  // from symbol to symbol and drew an uninvited profile on the next chart.
  // "draw_collapsed" is scoped for the same reason: the shapes it folds away
  // belong to one instrument (drawings.js keys its own store per symbol, and
  // "scene" is scoped right here), so a chart tidied on RELIANCE must not
  // open TCS with its levels already hidden and no obvious reason why.
  //
  // "chats"/"chatid" are NOT scoped, and used to be. A conversation is not a
  // property of an instrument: you ask about RELIANCE, then about TCS, and it
  // is the same conversation with the same person. Scoped, every symbol
  // change wiped the thread and the history panel could only show the chats
  // held on the chart you happened to be looking at — a record of your own
  // questions that hid most of itself. What the chat drew stays scoped
  // ("scene"), because that IS about one instrument's bars.
  const SCOPED = new Set(["chat", "scene", "vp", "draw_collapsed"]);
  const k = (key) => (SYM !== "RELIANCE" && SCOPED.has(key))
    ? `${SYM}:${key}` : key;

  /* One-time: un-scoping "chats" would ORPHAN every conversation.
   *
   * A browser that has been used holds "charto:TCS:chats" beside
   * "charto:chats". Simply removing the key from SCOPED makes the reader
   * look only at the unscoped one — the others stay in localStorage forever,
   * read by nothing, and to the user their history has been deleted.
   *
   * So merge them all, newest wins on a duplicate id, then delete the old
   * keys. Deleting them is what makes this run exactly once.
   */
  (() => {
    try {
      const olds = Object.keys(localStorage)
        .filter((key) => /^charto:[A-Z0-9.\-]+:chats$/.test(key));
      if (!olds.length) return;
      const byId = new Map();
      const add = (arr) => {
        for (const c of arr || []) {
          if (!c || !c.id) continue;
          const prev = byId.get(c.id);
          if (!prev || (c.updated || 0) > (prev.updated || 0)) byId.set(c.id, c);
        }
      };
      try { add(JSON.parse(localStorage.getItem(PREFIX + "chats") || "[]")); } catch {}
      for (const key of olds) {
        try { add(JSON.parse(localStorage.getItem(key) || "[]")); } catch {}
        localStorage.removeItem(key);
        localStorage.removeItem(key.replace(/chats$/, "chatid"));
      }
      const merged = [...byId.values()]
        .filter((c) => Array.isArray(c.turns) && c.turns.length)
        .sort((a, b) => (b.updated || 0) - (a.updated || 0))
        .slice(0, 60);
      localStorage.setItem(PREFIX + "chats", JSON.stringify(merged));
      console.info(`[charto] merged ${olds.length} per-symbol chat archive(s)`
                   + ` → ${merged.length} conversations`);
    } catch { /* a failed merge must never stop the app booting */ }
  })();

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
