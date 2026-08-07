/* Charto preview — the instrument universe, and the one picker over it.
 *
 * Three surfaces used to answer "which instrument?" and each was fetching
 * /symbols for itself: the header pill, the chat's logo marker, and now the
 * in-chart legend and the chat's context chip. One cache, one shape, one
 * spelling of a company's name — a logo that appears beside a name in a reply
 * and the logo on the legend are the same file, and neither can be a version
 * behind the other.
 *
 * The picker is the SAME list the header pill shows, minus the ↗ link: a logo,
 * the ticker, the company name, and whether the symbol is hydrated (a cold one
 * takes ~6 s to pull from the blob store, and saying so beforehand is the
 * difference between waiting and thinking it hung).
 */
"use strict";

const Universe = (() => {
  // same-origin behind a proxy, explicit port in local dev (see main.js)
  const API = ["localhost", "127.0.0.1"].includes(location.hostname)
    ? "http://127.0.0.1:5174" : "";

  let data = null;          // resolved payload, or null until the fetch lands
  let inflight = null;

  /** Load once; every later caller gets the same promise. Never rejects —
   *  a picker that cannot list the universe still has to open and say so. */
  function load() {
    if (data) return Promise.resolve(data);
    if (inflight) return inflight;
    inflight = fetch(`${API}/symbols`).then((r) => r.json()).then((d) => {
      data = {
        symbols: d.symbols || [],
        hydrated: new Set(d.hydrated || []),
        // the enrichment long name wins: a few Moneycontrol short names are
        // the wrong company outright (TITAN read "IAG Company")
        names: { ...(d.names || {}), ...(d.long || {}) },
        short: d.names || {},
        alias: d.alias || {},
        long: d.long || {},
        logos: d.logos || {},
      };
      return data;
    }).catch((e) => {
      console.warn("[charto] universe fetch failed", e);
      data = { symbols: [], hydrated: new Set(), names: {}, short: {},
               alias: {}, long: {}, logos: {} };
      return data;
    });
    return inflight;
  }

  /** What is known RIGHT NOW — null before the fetch lands. Callers that
   *  paint on every frame (the legend) use this and repaint on load(). */
  const peek = () => data;
  const logo = (sym) => (data && data.logos[String(sym || "").toUpperCase()]) || null;
  const label = (sym) => {
    const s = String(sym || "").toUpperCase();
    return (data && data.names[s]) || s;
  };

  /** The instrument's mark, as an <img> string — empty when we have none, so
   *  a missing logo costs no box. `onerror` removes a dead file for the same
   *  reason: an alt-text ghost beside a ticker reads as a broken chart. */
  function logoHTML(sym, cls = "co-logo") {
    const src = logo(sym);
    return src ? `<img class="${cls}" src="${src}" alt="" loading="lazy"
      onerror="this.remove()"/>` : "";
  }

  /* ── the picker ─────────────────────────────────────────────────────────
   * Anchored to whatever element was clicked and appended to <body>, so it
   * can open from a legend sitting inside an overflow-hidden chart pane
   * without being clipped. One instance at a time.
   */
  let popEl = null, offOutside = null;

  function close() {
    if (!popEl) return;
    popEl.remove(); popEl = null;
    document.removeEventListener("mousedown", offOutside, true);
    removeEventListener("resize", close);
    offOutside = null;
  }

  /** open({ anchor, current, onPick }) — onPick(symbol) fires on a choice.
   *  Opening twice on the same anchor closes it, like every other menu. */
  function open({ anchor, current, onPick, note }) {
    const again = popEl && popEl.dataset.for === (anchor.dataset.pickerId || "");
    close();
    if (again) return;
    if (window.__chartoCloseMenus) window.__chartoCloseMenus(null);

    if (!anchor.dataset.pickerId) {
      anchor.dataset.pickerId = "p" + Math.random().toString(36).slice(2, 8);
    }
    const pop = document.createElement("div");
    pop.className = "dropdown floating sym-picker open";
    pop.dataset.for = anchor.dataset.pickerId;
    pop.innerHTML =
      Icons.field(`<input class="pick-search" placeholder="Search instruments…"
              autocomplete="off" spellcheck="false" />`) +
      `<div class="pick-list"></div>` +
      (note ? `<div class="pick-note">${note}</div>` : "");
    document.body.appendChild(pop);
    popEl = pop;

    // Fixed to the anchor's own rect, flipped up when the bottom half of the
    // window has no room — a legend picker opening off-screen is a dead menu.
    const r = anchor.getBoundingClientRect();
    const W = 268;
    pop.style.width = W + "px";
    pop.style.left = Math.max(8, Math.min(r.left, innerWidth - W - 8)) + "px";
    if (r.bottom + 380 > innerHeight && r.top > 380) {
      pop.style.bottom = (innerHeight - r.top + 6) + "px";
    } else {
      pop.style.top = (r.bottom + 6) + "px";
    }

    const input = pop.querySelector(".pick-search");
    const list = pop.querySelector(".pick-list");
    const cur = String(current || "").toUpperCase();

    function render(query) {
      if (!data) {
        list.innerHTML = '<div class="item" style="color:var(--faint)">loading instruments…</div>';
        return;
      }
      const q = query.trim().toUpperCase();
      const hits = (q
        ? data.symbols.filter((s) => s.includes(q)
            || (data.names[s] || "").toUpperCase().includes(q)
            || (data.short[s] || "").toUpperCase().includes(q))
          .sort((a, b) => (a.startsWith(q) ? 0 : 1) - (b.startsWith(q) ? 0 : 1)
                          || a.localeCompare(b))
        : data.symbols);
      list.innerHTML = hits.map((s) => {
        const nm = data.names[s];
        return `<div class="item ${s === cur ? "on" : ""}" data-sym="${s}">` +
          `<span class="lead">${logoHTML(s)}` +
          (data.hydrated.has(s) ? '<span class="dot-h"></span>' : "") +
          `${s}${nm && nm !== s ? `<span class="co-name">${nm}</span>` : ""}</span>` +
          (data.hydrated.has(s) ? "" : '<span class="cold">~6s</span>') +
          "</div>";
      }).join("") || '<div class="item" style="color:var(--faint)">no match</div>';
    }

    // Focus first and never clear after the fetch: the universe can still be
    // in flight, and a box that empties itself under the cursor eats every
    // keystroke typed while waiting.
    render(""); input.focus();
    load().then(() => { if (popEl === pop) render(input.value); });

    input.addEventListener("input", () => render(input.value));
    input.addEventListener("keydown", (e) => {
      e.stopPropagation();                       // never reaches the drawing layer
      if (e.key === "Escape") close();
      if (e.key === "Enter") {
        const first = list.querySelector(".item[data-sym]");
        if (first) { const s = first.dataset.sym; close(); onPick(s); }
      }
    });
    list.addEventListener("click", (e) => {
      const it = e.target.closest(".item[data-sym]");
      if (!it) return;
      const s = it.dataset.sym;
      close();
      onPick(s);
    });
    pop.addEventListener("mousedown", (e) => e.stopPropagation());

    // capture phase, and it must not fire on the click that opened us
    offOutside = (e) => { if (!pop.contains(e.target) && !anchor.contains(e.target)) close(); };
    setTimeout(() => document.addEventListener("mousedown", offOutside, true), 0);
    addEventListener("resize", close);
  }

  return { load, peek, logo, label, logoHTML, open, close };
})();
