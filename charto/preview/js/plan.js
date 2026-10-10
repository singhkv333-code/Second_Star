/* Charto preview — the user's plan, mirrored for display.
 *
 * The SERVER decides. Every gated write is checked there and refused with a
 * 402 that carries its own sentence (data/entitlements.py), so nothing in
 * this file is a security boundary. It exists so the chart can say "your
 * plan allows 4 charts per tab" at the moment of the click, instead of
 * letting someone build a layout that the save will then refuse.
 *
 * It also runs the parallel-charts lease: each open tab renews one every 60 s
 * (POST /charts/lease). Opening more tabs than the plan allows evicts the
 * OLDEST tab, as TradingView does; that tab learns so on its next beat, says
 * so, and stops renewing until the user reclaims it.
 */
const Plan = (() => {
  "use strict";
  const API = location.port === "5173"
    ? "http://127.0.0.1:5174" : "";
  const BEAT_MS = 60_000;

  let me = null;              // the last /billing/me answer
  let beatTimer = null;
  let evicted = false;

  // One id per page LOAD, never stored: "Duplicate tab" copies
  // sessionStorage, which made two open tabs share one lease. A reload is a
  // new id too, and the old one is released on pagehide below.
  const tabId = Math.random().toString(36).slice(2) + Date.now().toString(36);

  const headers = (extra) => (typeof Auth !== "undefined"
    ? Auth.headers(extra) : Object.assign({}, extra || {}));

  async function refresh() {
    try {
      const r = await fetch(`${API}/billing/me`, { headers: headers() });
      if (r.ok) me = await r.json();
    } catch { /* offline: keep the last answer; the server still decides */ }
    document.dispatchEvent(new CustomEvent("charto:plan", { detail: me }));
    return me;
  }

  /** A feature's worth: true/false, a number, null for unlimited, or
   *  undefined when the plan has not loaded (callers then allow — the
   *  server will refuse if it must). A quota answers its limit. */
  function value(key) {
    const f = me && me.features && me.features[key];
    if (!f) return undefined;
    return f.kind === "quota" ? f.limit : f.value;
  }

  function allows(key, n) {
    // Follows the SERVER's switch (PAYWALL_ENABLED, off by default in
    // entitlements.py), so the click and the save can never disagree: with
    // the paywall off nothing is pre-blocked here and nothing is refused
    // there. `?paywall=preview` turns the client side on alone, to review the
    // prompts and lock markers without enforcing anything.
    const preview = /[?&]paywall=preview\b/.test(location.search);
    if (!preview && (!me || me.paywall_enabled !== true)) return true;
    const v = value(key);
    if (v === undefined || v === null) return true;
    if (typeof v === "boolean") return v;
    return n <= v;
  }

  function refusal(key) {
    const f = (me && me.features && me.features[key]) || {};
    const label = f.label || key;
    const lower = /^.[A-Z]/.test(label) ? label : label[0].toLowerCase() + label.slice(1);
    const name = (me && me.plan_name) || "current";
    if (me && me.plan === "anonymous") return `Sign in to use more ${lower}.`;
    if (typeof value(key) === "boolean") return `${label} are not included in the ${name} plan.`;
    return `Your ${name} plan allows ${value(key)} ${lower}.`;
  }

  async function beat(reclaim) {
    if (evicted && !reclaim) return;
    try {
      const r = await fetch(`${API}/charts/lease`, {
        method: "POST",
        headers: headers({ "Content-Type": "application/json" }),
        body: JSON.stringify({ tab_id: tabId, reclaim: !!reclaim }),
      });
      if (r.status === 402) {
        const d = await r.json().catch(() => ({}));
        if (d.code === "evicted") {
          evicted = true;
          document.dispatchEvent(new CustomEvent("charto:evicted", { detail: d }));
        }
      } else if (r.ok) {
        evicted = false;
      }
    } catch { /* a missed beat is harmless: the lease outlives two of them */ }
  }

  function startBeating() {
    clearInterval(beatTimer);
    beat();
    beatTimer = setInterval(() => beat(), BEAT_MS);
  }

  window.addEventListener("pagehide", () => {
    // Free the slot now rather than after the lease times out.
    // keepalive fetch, not sendBeacon: a beacon cannot carry the session
    // header, and the lease belongs to the signed-in account.
    try {
      fetch(`${API}/charts/lease`, {
        method: "POST", keepalive: true,
        headers: headers({ "Content-Type": "application/json" }),
        body: JSON.stringify({ tab_id: tabId, release: true }),
      }).catch(() => {});
    } catch { /* the lease lapses on its own */ }
  });

  if (typeof Auth !== "undefined" && Auth.onChange) {
    Auth.onChange(() => { refresh(); startBeating(); });
  }
  refresh();
  startBeating();

  return {
    refresh, value, allows, refusal,
    get plan() { return me && me.plan; },
    /** the last /billing/me answer, for display (js/paywall.js) */
    get me() { return me; },
    get evicted() { return evicted; },
    reclaim: () => beat(true),
  };
})();
