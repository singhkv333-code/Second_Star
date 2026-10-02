/* Charto preview — the small kit every workspace widget shares: where the
 * data server is, escaping, number formats, loading a library on first use,
 * and the empty / failed states, so twelve widgets say "unavailable" one way. */
"use strict";

const WKit = (() => {
  const API = ["localhost", "127.0.0.1"].includes(location.hostname) ? "http://127.0.0.1:5174" : "";
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const ic = (n, c = "xs") => Icons.svg(n, c);

  /** A script or stylesheet, loaded once, on first use — never with the page. */
  const once = new Map();
  function load(src) {
    if (once.has(src)) return once.get(src);
    const p = new Promise((res, rej) => {
      const css = /\.css(\?|$)/.test(src);
      const n = document.createElement(css ? "link" : "script");
      if (css) { n.rel = "stylesheet"; n.href = src; } else { n.src = src; n.async = false; }
      n.onload = () => res(); n.onerror = () => rej(new Error(`could not load ${src}`));
      document.head.appendChild(n);
    });
    once.set(src, p);
    return p;
  }
  const loadAll = (list) => list.reduce((p, s) => p.then(() => load(s)), Promise.resolve());

  async function json(path, opts) {
    const r = await Net.get(API + path, opts);
    let d = null;
    try { d = await r.json(); } catch { /* not json */ }
    if (!r.ok) throw new Error((d && (d.error || d.detail)) || `HTTP ${r.status}`);
    return d;
  }

  const MINUS = "−";
  const num = (v, d = 2) => v == null || !Number.isFinite(+v) ? "—"
    : (v < 0 ? MINUS : "") + Math.abs(+v).toLocaleString("en-IN", { minimumFractionDigits: d, maximumFractionDigits: d });
  const pct = (v, d = 2) => v == null || !Number.isFinite(+v) ? "—"
    : (v > 0 ? "+" : v < 0 ? MINUS : "") + Math.abs(+v).toFixed(d) + "%";
  /** Rupees in the Indian way: ₹ lakh crore / crore, never a bare 13-digit number. */
  function inr(v) {
    if (v == null || !Number.isFinite(+v)) return "—";
    const a = Math.abs(v), sg = v < 0 ? MINUS : "";
    if (a >= 1e12) return `${sg}₹${(a / 1e12).toFixed(2)} L Cr`;
    if (a >= 1e7) return `${sg}₹${(a / 1e7).toLocaleString("en-IN", { maximumFractionDigits: 0 })} Cr`;
    return `${sg}₹${a.toLocaleString("en-IN", { maximumFractionDigits: 2 })}`;
  }
  const dir = (v) => (v > 0 ? "up" : v < 0 ? "down" : "");
  const ago = (ts) => {
    if (!ts) return "";
    const s = Math.max(0, Date.now() / 1000 - ts);
    if (s < 60) return "just now";
    if (s < 3600) return `${Math.floor(s / 60)}m ago`;
    if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
    return new Date(ts * 1000).toLocaleDateString("en-IN", { day: "2-digit", month: "short" });
  };

  /** The one empty state: a mark, a sentence, and the button that ends it. */
  const empty = (icon, line, cta, attrs = "") =>
    `<div class="side-empty">${Icons.svg(icon)}<p>${line}</p>` +
    (cta ? `<button type="button" class="dk-cta" ${attrs}>${cta}</button>` : "") + `</div>`;
  const skel = (n = 8, cls = "") => `<div class="w-skel ${cls}">${"<i></i>".repeat(n)}</div>`;

  /** A key-value store in IndexedDB for what is too big for localStorage — a
   *  workbook, an uploaded PDF. It lives in this browser only, and every call
   *  degrades to "nothing stored" rather than throwing when storage is off. */
  let dbp = null;
  function db() {
    if (dbp) return dbp;
    dbp = new Promise((res, rej) => {
      try {
        const r = indexedDB.open("charto-widgets", 1);
        r.onupgradeneeded = () => r.result.createObjectStore("kv");
        r.onsuccess = () => res(r.result);
        r.onerror = () => rej(r.error);
      } catch (e) { rej(e); }
    });
    return dbp;
  }
  const tx = (mode, fn) => db().then((d) => new Promise((res, rej) => {
    const t = d.transaction("kv", mode);
    const q = fn(t.objectStore("kv"));
    t.oncomplete = () => res(q && q.result);
    t.onerror = () => rej(t.error);
  }));
  const idb = {
    get: (k) => tx("readonly", (s) => s.get(k)).catch(() => undefined),
    set: (k, v) => tx("readwrite", (s) => s.put(v, k)).catch((e) => { console.warn("[wkit] idb", e); }),
    del: (k) => tx("readwrite", (s) => s.delete(k)).catch(() => {}),
    keys: (prefix) => tx("readonly", (s) => s.getAllKeys()).then((ks) => (ks || []).filter((k) => String(k).startsWith(prefix))).catch(() => []),
  };

  return { API, esc, ic, load, loadAll, json, num, pct, inr, dir, ago, empty, skel, idb };
})();
