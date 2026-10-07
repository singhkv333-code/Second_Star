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
        // [exchange, kind, price decimals] for everything that is not an
        // NSE equity — the instrument master's answer, not a guess
        meta: d.meta || {},
      };
      return data;
    }).catch((e) => {
      console.warn("[charto] universe fetch failed", e);
      data = { symbols: [], hydrated: new Set(), names: {}, short: {},
               alias: {}, long: {}, logos: {}, meta: {} };
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

  /* ── the instrument row, and the one place it is built ──────────────────
   * Both surfaces that list instruments — the header pill's menu and this
   * floating picker — draw the same row, because they are the same list and
   * a company that looks one way in one of them and another way in the other
   * is two companies as far as the eye is concerned.
   *
   * The shape is the standard one for this job: a LIST ROW with a leading
   * avatar, a two-line title/subtitle stack, and trailing metadata. Left says
   * WHICH instrument, right says WHERE IT IS — and the right side is the part
   * that has to earn its place, so it carries only the last price and the
   * day's move.
   */

  // Mirrors dataserver.session_for(): the venue is a property of the symbol's
  // shape, not a guess. If that function ever learns a new venue, this is the
  // other half of the pair.
  const MCX = new Set(["GOLD", "GOLDM", "SILVER", "SILVERM", "CRUDEOIL",
    "NATURALGAS", "COPPER", "ZINC", "ALUMINIUM", "LEAD", "NICKEL",
    "COTTON", "MENTHAOIL"]);
  function venue(sym) {
    const s = String(sym || "").toUpperCase();
    const m = data && data.meta[s];
    if (m) return m[0];
    if (s.endsWith("USDT")) return "BYBIT";
    if (s.endsWith("-USD")) return "COINBASE";
    return MCX.has(s) ? "MCX" : "NSE";
  }

  function category(sym) {
    const s = String(sym || "").toUpperCase();
    const m = data && data.meta[s];
    const kind = String(m ? m[1] : "equity").toLowerCase();
    const ex = venue(s);
    if (kind.includes("crypto") || ex === "BYBIT" || ex === "COINBASE" || /(?:USDT|-USD)$/.test(s)) return "Crypto";
    if (kind.includes("option") || ex === "NFO") return "Options";
    if (kind.includes("index") || kind.includes("volatility")) return "Indices";
    if (ex === "MCX" || kind.includes("commodity")) return "Commodities";
    return "Stocks";
  }
  const categories = ["All", "Stocks", "Crypto", "Indices", "Commodities", "Options"];
  function tabsHTML() {
    return `<div class="symbol-categories" role="tablist" aria-label="Instrument category">${categories.map((c) =>
      `<button type="button" role="tab" aria-selected="${c === "All"}" data-category="${c}">${c}</button>`).join("")}</div>`;
  }

  /** Price decimals the instrument is quoted in (its tick size): 4 for an
   *  INR pair, 0 for gold quoted in whole rupees. null = not in the master. */
  function decimals(sym) {
    const m = data && data.meta[String(sym || "").toUpperCase()];
    return m && Number.isFinite(m[2]) ? m[2] : null;
  }
  /** What the picker prints: the ticker without its exchange prefix, because
   *  the exchange already sits beside it as its own tag. */
  const shown = (sym) => String(sym).includes(":") ? String(sym).split(":").slice(1).join(":") : String(sym);

  const CCY = { INR: "\u20b9", USD: "$" };
  function money(v, ccy, sym) {
    if (v === null || v === undefined || !isFinite(v)) return "";
    // Two places is what a price is quoted in, here and on the exchange.
    // Sub-rupee instruments are the only ones that need more, and they need
    // it badly — a 0.0231 coin at two places is 0.02 for every one of them.
    // A master instrument says its own: an INR pair quotes to 4.
    const known = decimals(sym);
    const dp = known !== null ? known : Math.abs(v) >= 1 ? 2 : 4;
    return (CCY[ccy] || "") + v.toLocaleString(undefined,
      { minimumFractionDigits: dp, maximumFractionDigits: dp });
  }

  /** The trailing cell, from a /quotes row. Two states that are NOT the same:
   *  a price we do not have YET (blank, the fetch is in flight) and a price
   *  that does not exist (an em dash). Printing 0.00 for either would be the
   *  one thing this app does not do. */
  function quoteHTML(q) {
    if (!q || q.last === null || q.last === undefined) {
      return '<span class="ir-last ir-none">\u2014</span>';
    }
    const pct = q.change_pct;
    const dir = pct === null || pct === undefined ? "" : pct > 0 ? " up" : pct < 0 ? " dn" : "";
    return `<span class="ir-last">${money(q.last, q.currency, q.symbol)}</span>` +
      (pct === null || pct === undefined ? ""
        // A real minus sign, not a hyphen: at this size a hyphen sits high
        // and short beside tabular figures and reads as a dash between two
        // things rather than as a negative number.
        : `<span class="ir-chg${dir}">${pct > 0 ? "+" : pct < 0 ? "\u2212" : ""}` +
          `${Math.abs(pct).toFixed(2)}%</span>`);
  }

  /* ── logo cache ───────────────────────────────────────────────────
   * The picker is rebuilt with innerHTML on every open, every filter
   * keystroke and every quote refresh, so each rebuild constructs brand-new
   * <img> elements. The browser's HTTP cache means the bytes are not
   * re-fetched, but a fresh element still decodes before it paints, which is
   * what makes the marks flicker in as you type.
   *
   * Holding one detached Image per URL keeps it decoded in memory for the
   * life of the page, so later rows paint the mark immediately. `decoding`
   * and `fetchpriority` keep that work off the layout path.
   *
   * Keyed by URL, not by symbol: two symbols on the same asset (BTC-USD and
   * BTCUSDT) share one file and must share one cache entry. */
  const _logoCache = new Map();
  function warmLogo(src) {
    if (!src || _logoCache.has(src)) return;
    const im = new Image();
    im.decoding = "async";
    im.fetchPriority = "low";
    im.src = src;                       // held by the Map, so never collected
    _logoCache.set(src, im);
  }

  /** One row. `link` adds the company-page affordance (the header menu has
   *  it; the in-chart picker does not). `cold` marks a symbol whose bars are
   *  still in the blob store — it has no price to show and saying "~6s" is
   *  more use than an em dash. */
  function rowHTML(sym, { current, link, cold, companyBase, theme } = {}) {
    const nm = (data && data.names[sym]) || "";
    const src = logo(sym);
    const on = sym === String(current || "").toUpperCase();
    return `<div class="item inst-row${on ? " on" : ""}" data-sym="${sym}">` +
      `<span class="ir-lead">` +
        (src ? (warmLogo(src), `<img class="ir-logo" src="${src}" alt=""
                 loading="lazy" decoding="async"
                 onerror="this.classList.add('ir-dead')"/>`)
             : `<span class="ir-logo ir-blank">${sym.slice(0, 1)}</span>`) +
        `<span class="ir-copy">` +
          `<span class="ir-tick">${shown(sym)}<span class="ir-ex">${venue(sym)}</span></span>` +
          (nm && nm !== sym ? `<span class="ir-name">${nm}</span>` : "") +
        `</span>` +
      `</span>` +
      (cold
        ? `<span class="ir-quote"><span class="ir-cold">~6s to load</span></span>`
        : `<span class="ir-quote" data-q="${sym}"></span>`) +
      (link ? `<a class="open-co" href="${companyBase}/stock/${encodeURIComponent(sym)}?theme=${theme}"
            title="${sym} \u2014 open company page"
            aria-label="${sym} \u2014 open company page">${Icons.svg("externalLink", "sm")}</a>` : "") +
      "</div>";
  }

  /** One quiet route from an instrument label to its company page. The
   *  surrounding `.company-affordance` owns disclosure: hidden at rest,
   *  visible on hover/focus, and always present on touch screens. Keeping the
   *  href here gives the main chart and every symbol-aware widget one exact
   *  destination and one accessible name. */
  function companyLinkHTML(sym, companyBase = "") {
    const s = String(sym || "").toUpperCase();
    if (!s) return "";
    return `<a class="company-page-icon" href="${companyBase}/stock/${encodeURIComponent(s)}"` +
      ` title="Open the ${shown(s)} company page" aria-label="Open the ${shown(s)} company page">` +
      `${Icons.svg("externalLink", "xs")}</a>`;
  }

  /* ── prices, for the rows you can actually see ──────────────────────────
   * There are 557 instruments in the list and /quotes caps a call at 120, so
   * pricing the whole universe on open would be five round trips for a list
   * the user scrolls two screens of. An IntersectionObserver asks only for
   * what is on screen, the answers are cached for the life of the page, and
   * a re-render after a keystroke repaints from that cache with no fetch —
   * so typing never costs a request for a row already priced.
   */
  const quotes = new Map();

  function quoteWatch(listEl) {
    if (!listEl || !("IntersectionObserver" in window)) return;
    if (listEl.__qio) listEl.__qio.disconnect();
    let pending = new Set(), timer = null;

    const paint = (sym) => {
      const q = quotes.get(sym);
      if (!q) return;
      listEl.querySelectorAll(`.ir-quote[data-q="${CSS.escape(sym)}"]`)
        .forEach((cell) => { cell.innerHTML = quoteHTML(q); });
    };

    const flush = () => {
      timer = null;
      const want = [...pending].filter((s) => !quotes.has(s)).slice(0, 100);
      pending = new Set([...pending].slice(100));
      if (!want.length) return;
      // Mark them taken BEFORE the fetch so a second scroll over the same
      // rows does not queue them again while the first call is in flight.
      want.forEach((s) => quotes.set(s, null));
      fetch(`${API}/quotes?symbols=${encodeURIComponent(want.join(","))}`)
        .then((r) => r.json())
        .then((d) => {
          (d.quotes || []).forEach((q) => { quotes.set(q.symbol, q); paint(q.symbol); });
        })
        .catch(() => {
          // A failed batch must not poison the cache: leaving `null` there
          // would make those rows permanently blank with no way back.
          want.forEach((s) => { if (quotes.get(s) === null) quotes.delete(s); });
        });
      if (pending.size) timer = setTimeout(flush, 60);
    };

    const io = new IntersectionObserver((entries) => {
      for (const e of entries) {
        if (!e.isIntersecting) continue;
        const sym = e.target.dataset.q;
        io.unobserve(e.target);
        if (quotes.has(sym)) { paint(sym); continue; }
        pending.add(sym);
      }
      if (pending.size && !timer) timer = setTimeout(flush, 90);
    }, { root: listEl, rootMargin: "160px 0px" });

    listEl.querySelectorAll(".ir-quote[data-q]").forEach((cell) => {
      const sym = cell.dataset.q;
      if (quotes.get(sym)) { cell.innerHTML = quoteHTML(quotes.get(sym)); return; }
      io.observe(cell);
    });
    listEl.__qio = io;
  }

  /* ── the picker ─────────────────────────────────────────────────────────
   * Appended to <body>, so it can open from a legend sitting inside an
   * overflow-hidden chart pane
   * without being clipped. One instance at a time.
   */
  let popEl = null, popAnchor = null, offOutside = null;

  function close() {
    if (!popEl) return;
    if (popAnchor) popAnchor.setAttribute("aria-expanded", "false");
    popEl.remove(); popEl = null;
    popAnchor = null;
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
      `<div class="symbol-dialog-head"><strong>Symbol search</strong><button type="button" class="symbol-close" aria-label="Close symbol search"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M18 6 6 18M6 6l12 12"/></svg></button></div>` +
      Icons.field(`<input class="pick-search" placeholder="Search symbol or company"
              autocomplete="off" spellcheck="false" />`) + tabsHTML() +
      `<div class="symbol-results-label">INSTRUMENTS</div>` +
      `<div class="pick-list"></div>` +
      (note ? `<div class="pick-note">${note}</div>` : "");
    document.body.appendChild(pop);
    popEl = pop;
    popAnchor = anchor;
    anchor.setAttribute("aria-expanded", "true");

    // Center the picker regardless of which chart control opened it.
    const W = Math.min(760, innerWidth - 24);
    pop.style.width = W + "px";
    pop.style.left = (innerWidth - W) / 2 + "px";
    pop.style.top = "min(12vh, 100px)";

    const input = pop.querySelector(".pick-search");
    const list = pop.querySelector(".pick-list");
    const cur = String(current || "").toUpperCase();

    let selectedCategory = "All";
    function render(query) {
      if (!data) {
        list.innerHTML = '<div class="item" style="color:var(--faint)">loading instruments…</div>';
        return;
      }
      const q = query.trim().toUpperCase();
      const hits = (q
        ? data.symbols.filter((s) => (selectedCategory === "All" || category(s) === selectedCategory) && (s.includes(q)
            || (data.names[s] || "").toUpperCase().includes(q)
            || (data.short[s] || "").toUpperCase().includes(q)))
          .sort((a, b) => (a.startsWith(q) ? 0 : 1) - (b.startsWith(q) ? 0 : 1)
                          || a.localeCompare(b))
        : data.symbols.filter((s) => selectedCategory === "All" || category(s) === selectedCategory));
      list.innerHTML = hits.map((s) => rowHTML(s, {
        current: cur, cold: !data.hydrated.has(s),
      })).join("") || '<div class="item" style="color:var(--faint)">no match</div>';
      quoteWatch(list);
    }

    // Focus first and never clear after the fetch: the universe can still be
    // in flight, and a box that empties itself under the cursor eats every
    // keystroke typed while waiting.
    render(""); input.focus();
    load().then(() => { if (popEl === pop) render(input.value); });

    input.addEventListener("input", () => render(input.value));
    pop.querySelector(".symbol-close").addEventListener("click", close);
    pop.querySelector(".symbol-categories").addEventListener("click", (e) => {
      const tab = e.target.closest("[data-category]");
      if (!tab) return;
      selectedCategory = tab.dataset.category;
      pop.querySelectorAll("[data-category]").forEach((b) => b.setAttribute("aria-selected", String(b === tab)));
      render(input.value);
    });
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
    pop.addEventListener("click", (e) => e.stopPropagation());

    // capture phase, and it must not fire on the click that opened us
    offOutside = (e) => { if (!pop.contains(e.target) && !anchor.contains(e.target)) close(); };
    setTimeout(() => document.addEventListener("mousedown", offOutside, true), 0);
    addEventListener("resize", close);
  }

  return { load, peek, logo, label, logoHTML, open, close,
           rowHTML, companyLinkHTML, quoteWatch, venue, category, tabsHTML, decimals, shown };
})();
