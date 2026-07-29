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

const Store = (() => {
  const PREFIX = "charto:";

  return {
    get(key, fallback) {
      try {
        const raw = localStorage.getItem(PREFIX + key);
        return raw === null ? fallback : JSON.parse(raw);
      } catch {
        return fallback;   // corrupt or blocked → behave like a fresh session
      }
    },
    set(key, value) {
      try { localStorage.setItem(PREFIX + key, JSON.stringify(value)); }
      catch { /* private mode / quota — persistence is a convenience, not a contract */ }
    },
    del(key) {
      try { localStorage.removeItem(PREFIX + key); } catch {}
    },
  };
})();
