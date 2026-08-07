/* Charto preview — the widget bar and its panels: Watchlist, Alerts.
 *
 * THE WATCHLIST IS REAL. Its lists are the user's own, persisted through
 * js/store.js, and every price on it comes from the dataserver's /quotes —
 * the same daily series the chart folds, so a row and the candles beside it
 * can never quote different numbers. A symbol the store holds nothing for
 * shows an em dash, never a filler number.
 *
 * ALERTS IS STILL A LOOK. Every alert and log line below is fixture data in
 * this file's MOCK block, and its create/pause/edit/delete controls are drawn
 * at full fidelity and deliberately do nothing — there is no alert engine to
 * wire them to, and a half-wired button that half-works is a worse answer
 * than one that plainly doesn't. The bell on a watchlist row belongs to that
 * unbuilt widget and is inert for the same reason.
 *
 * THE SHAPE, and why it is this one. TradingView keeps a permanent strip of
 * widget icons on the outer edge and opens ONE widget panel beside it, and
 * that is what this is. Watchlist and Alerts are separate widgets — separate
 * panels, separate heads, separate contents — because they are separate
 * subjects: one is what you are watching, the other is what you asked to be
 * told about. Alerts keeps its own two tabs (Alerts / Log) INSIDE its panel,
 * because those two are one subject seen twice: the standing rules, and the
 * moments they fired.
 *
 * A widget is one entry in WIDGETS below: an icon, a label, and a render()
 * that fills its own panel. The bar, the exclusivity rule, the phone's
 * one-panel rule and the mobile sheet all read that list, so a third widget
 * is one entry here and an <aside> in index.html.
 */
"use strict";

const Panels = (() => {
  const el = (id) => document.getElementById(id);
  const bar = el("wbar");
  if (!bar) return {};

  // same-origin behind a proxy, explicit port in local dev (see main.js)
  const API = ["localhost", "127.0.0.1"].includes(location.hostname)
    ? "http://127.0.0.1:5174" : "";

  /* ══ fixture data — ALERTS ONLY ════════════════════════════════════════
   * Real NSE/MCX instruments at plausible levels, so the rows are read at
   * the width they will really be read at — a panel mocked with "AAA 100.00"
   * looks roomy and then ships broken.
   */
  const MOCK = {
    /* `state` is one of: armed (live, watching) · paused (kept, not
     * watching) · fired (it happened; the rule stopped itself). */
    alerts: [
      { sym: "RELIANCE", ex: "NSE", state: "armed",
        cond: "Crossing up", level: "1,420.00",
        meta: "Once per bar close · 5m", when: "12 Jul" },
      { sym: "NIFTY 50", ex: "NSE", state: "armed",
        cond: "Crossing down", level: "24,800.00",
        meta: "Only once · 15m", when: "28 Jul" },
      { sym: "BANKNIFTY", ex: "NSE", state: "armed",
        cond: "Volume above", level: "2× average",
        meta: "Once per bar · 1h", when: "31 Jul" },
      { sym: "HDFCBANK", ex: "NSE", state: "fired",
        cond: "Crossing up", level: "1,655.00",
        meta: "Only once · 5m", when: "09:41" },
      { sym: "TCS", ex: "NSE", state: "paused",
        cond: "RSI(14) crossing down", level: "30",
        meta: "Once per bar close · 1h", when: "04 Aug" },
      { sym: "INFY", ex: "NSE", state: "paused",
        cond: "Moving down", level: "2% in 1D",
        meta: "Once per day · 1D", when: "22 Jul" },
    ],

    log: [
      { day: "Today", items: [
        { time: "09:41", sym: "HDFCBANK", verb: "crossed above", level: "1,655.00",
          meta: "5m · price", val: "1,656.20" },
        { time: "09:18", sym: "NIFTY 50", verb: "crossed below", level: "24,800.00",
          meta: "15m · index", val: "24,794.55" },
      ] },
      { day: "Yesterday", items: [
        { time: "15:22", sym: "TCS", verb: "RSI(14) crossed below", level: "30",
          meta: "1h · RSI", val: "28.4" },
        { time: "11:05", sym: "RELIANCE", verb: "crossed above", level: "1,420.00",
          meta: "5m · price", val: "1,421.75" },
      ] },
      { day: "Fri, 31 Jul", items: [
        { time: "14:47", sym: "BANKNIFTY", verb: "volume rose above", level: "2× average",
          meta: "1h · volume", val: "3.1×" },
      ] },
    ],
  };

  /* ══ shared bits ═══════════════════════════════════════════════════════ */

  const iconBtn = (cls, icon, label, extra = "") =>
    `<button type="button" class="${cls}" title="${label}" aria-label="${label}" ` +
    `${extra}>${Icons.svg(icon, "xs")}</button>`;

  /** The head is TradingView's 38px toolbar: what this list is on the left,
   *  what you can do to it on the right. No close × — the lit icon in the bar
   *  is what closes a panel. */
  const head = (leadHTML, actsHTML) =>
    `<div class="side-head">${leadHTML}${actsHTML}</div>`;

  /** …and the actions on it are 28px ghosts, not the app's 30px .btn. */
  const act = (icon, label) => iconBtn("side-act", icon, label);

  /** An empty widget is a drawing, one full-ink sentence at reading size,
   *  and the single button that ends the state. `extra` is what makes that
   *  button a real one — the delegated handler finds it by its attribute. */
  const empty = (icon, line, cta, extra = "") =>
    `<div class="side-empty">${Icons.svg(icon)}<p>${line}</p>` +
    (cta ? `<button type="button" class="btn cta" ${extra}>${cta}</button>` : "")
    + `</div>`;

  /** List names are the one thing on these panels the USER typed. */
  const esc = (s) => String(s).replace(/[&<>"]/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  /* A menu hung off a head button. The panel is a 302px column with its own
   * scroller, so these are appended to <body> and positioned to the anchor —
   * the same thing Universe's picker does, and for the same reason: a menu
   * clipped by the list it edits is a dead menu. One at a time. */
  let popEl = null, popOff = null;

  function closePopup() {
    if (!popEl) return;
    popEl.remove(); popEl = null;
    document.removeEventListener("mousedown", popOff, true);
    removeEventListener("resize", closePopup);
    popOff = null;
  }

  function popup(anchor, html, onPick) {
    // `.open` can have been stripped by main.js's closeMenus (another menu was
    // opened elsewhere) while this node is still parked in the DOM. That is a
    // CLOSED menu, so re-clicking its button must reopen it, not toggle a
    // hidden thing shut and make the first click look ignored.
    const again = popEl && popEl.classList.contains("open")
      && popEl.dataset.for === anchor.dataset.wl;
    closePopup();
    if (again) return;                       // clicking the lit button closes
    if (window.__chartoCloseMenus) window.__chartoCloseMenus(null);
    const pop = document.createElement("div");
    pop.className = "dropdown floating open wl-menu";
    pop.dataset.for = anchor.dataset.wl || "";
    pop.innerHTML = html;
    document.body.appendChild(pop);
    popEl = pop;

    // right-aligned to the button, flipped up when the bottom has no room
    const r = anchor.getBoundingClientRect();
    const w = pop.offsetWidth, h = pop.offsetHeight;
    pop.style.left = Math.max(8, Math.min(r.right - w, innerWidth - w - 8)) + "px";
    if (r.bottom + h + 8 > innerHeight && r.top - h - 6 > 8) {
      pop.style.top = (r.top - h - 6) + "px";
    } else {
      pop.style.top = Math.min(r.bottom + 6, Math.max(8, innerHeight - h - 8)) + "px";
    }

    pop.addEventListener("mousedown", (e) => e.stopPropagation());
    pop.addEventListener("click", (e) => {
      const it = e.target.closest("[data-pick]");
      if (!it || it.classList.contains("off")) return;
      closePopup();
      onPick(it.dataset.pick);
    });
    // capture phase, and never on the click that opened this
    popOff = (e) => {
      if (!pop.contains(e.target) && !anchor.contains(e.target)) closePopup();
    };
    setTimeout(() => document.addEventListener("mousedown", popOff, true), 0);
    addEventListener("resize", closePopup);
  }

  /* ══ widget · watchlist ════════════════════════════════════════════════
   * A real list of instruments — kept, edited and priced. Three facts, and
   * each one lives in exactly one place:
   *
   *  · WHAT IS ON IT is this widget's own state, persisted through Store
   *    (js/store.js), unscoped — a watchlist follows the user, not the
   *    symbol the tab happens to be on.
   *  · WHAT IT COSTS comes from the dataserver's /quotes, which folds the
   *    same daily series the chart draws (forming minute included). Nothing
   *    here computes a price, a change or a percent from anything else.
   *  · WHICH ONE THE CHART IS ON is read off the header pill at render time
   *    and never stored — a copy of that fact is a copy that can be a symbol
   *    behind.
   *
   * The prices are polled while the panel is open, and the page's own symbol
   * additionally rides the live stream (js/main.js dispatches `charto:tick`),
   * so the row you are charting moves with the candle rather than up to five
   * seconds after it.
   */
  const WL_KEY = "watchlists";           // Store key → localStorage charto:…
  const WL_SCOPES = "wlscopes";          // symbol → asset class, told by /quotes
  const WL_MAX = 12;
  const QUOTE_MS = 5000;
  // A first-ever visit opens on something rather than on an empty state: the
  // index everyone checks, its bank, and four of the most-held large caps.
  const WL_SEED = ["NIFTY 50", "NIFTY BANK", "RELIANCE", "TCS", "HDFCBANK", "INFY"];

  /* The store's own asset classes (scope_for) → the section a row sits in.
   * India VIX is pooled separately server-side because its statistics are
   * nothing like an index's; on a watchlist it is still an index row. */
  const SECTION_OF_SCOPE = {
    index_in: "Indices", volatility_in: "Indices", equity_in: "Stocks",
    commodity_in: "Commodities", fx_in: "Currencies", crypto: "Crypto",
  };
  const SECTIONS = ["Indices", "Stocks", "Commodities", "Currencies", "Crypto"];
  // Only until the first /quotes answers for a symbol — after that the server
  // is the source. Naming, not judgement: these are the exact spellings
  // backfill_macro.py stores.
  const INDEX_RE = /^(NIFTY\b|SENSEX$|BANKEX$|INDIA VIX$)/;

  const SORTS = { manual: "Order added", az: "Symbol A→Z", pct: "Change %" };

  let wl = loadWL();
  let scopes = Store.get(WL_SCOPES, {}) || {};
  const quotes = new Map();     // symbol → the last /quotes row for it
  let planKey = "";             // what the DOM currently shows, for diffing
  let quoteTimer = null, quoting = false;

  /** Whatever was persisted, re-shaped so the rest of this file can trust it:
   *  a corrupt or half-written blob behaves like a first visit, not a crash. */
  function loadWL() {
    const raw = Store.get(WL_KEY, null);
    const lists = [];
    for (const l of (raw && Array.isArray(raw.lists) ? raw.lists : [])) {
      if (!l || typeof l !== "object") continue;
      const syms = (Array.isArray(l.syms) ? l.syms : [])
        .filter((s) => typeof s === "string")
        .map((s) => s.trim().toUpperCase()).filter(Boolean);
      lists.push({ id: String(l.id || lists.length + 1),
                   name: String(l.name || "My list").slice(0, 32),
                   syms: [...new Set(syms)] });
      if (lists.length >= WL_MAX) break;
    }
    if (!lists.length) lists.push({ id: "1", name: "My list", syms: [...WL_SEED] });
    const cols = (raw && raw.cols) || {};
    const state = {
      lists,
      active: lists.some((l) => l.id === (raw || {}).active)
        ? raw.active : lists[0].id,
      cols: { last: cols.last !== false, chg: cols.chg !== false,
              pct: cols.pct !== false },
      sort: SORTS[(raw || {}).sort] ? raw.sort : "manual",
      folded: Array.isArray((raw || {}).folded) ? raw.folded.map(String) : [],
    };
    // every column off would leave the hover controls nowhere to land
    if (!state.cols.last && !state.cols.chg && !state.cols.pct) state.cols.pct = true;
    return state;
  }

  function saveWL() { Store.set(WL_KEY, wl); }
  const activeList = () => wl.lists.find((l) => l.id === wl.active) || wl.lists[0];
  const currentSymbol = () =>
    (el("symbolName").textContent || "").trim().toUpperCase();

  function sectionOf(sym) {
    const told = SECTION_OF_SCOPE[scopes[sym]];
    if (told) return told;
    const d = Sym.of(sym);
    if (d.isCrypto) return "Crypto";
    if (d.venue === "MCX") return "Commodities";
    if (d.venue === "NSE CDS") return "Currencies";
    return INDEX_RE.test(sym) ? "Indices" : "Stocks";
  }

  /** What the body is showing, as one string — so a poll can tell "the same
   *  rows, new numbers" (repaint the cells) from "a different list" (rebuild)
   *  without rebuilding to find out. */
  const keyOf = (secs) =>
    secs.map((s) => s.name + ":" + s.syms.join(",")).join("|");

  /** The list, grouped and ordered exactly as it will be drawn. */
  function plan() {
    const syms = activeList().syms;
    const by = new Map();
    for (const s of syms) {
      const name = sectionOf(s);
      if (!by.has(name)) by.set(name, []);
      by.get(name).push(s);
    }
    const order = (a, b) => {
      if (wl.sort === "az") return a.localeCompare(b);
      if (wl.sort === "pct") {
        // an instrument we hold no price for cannot be ranked by one, so it
        // sinks to the bottom of its section rather than sorting as zero
        const pa = (quotes.get(a) || {}).change_pct;
        const pb = (quotes.get(b) || {}).change_pct;
        if (pa == null && pb == null) return a.localeCompare(b);
        if (pa == null) return 1;
        if (pb == null) return -1;
        return pb - pa;
      }
      return 0;                                    // manual: as they were added
    };
    return [...by.keys()]
      .sort((a, b) => SECTIONS.indexOf(a) - SECTIONS.indexOf(b))
      .map((name) => ({ name, syms: [...by.get(name)].sort(order) }));
  }

  /* ── the numbers ─────────────────────────────────────────────────────────
   * Formatting only. The minus is U+2212, the typographic one TradingView
   * uses too — a hyphen next to tabular figures sits too high and too short.
   * An em dash means "we hold no price for this", which is a fact about the
   * store and is never dressed up as a zero.
   */
  const DASH = "—", MINUS = "−";
  /* Decimals come from the INSTRUMENT, not from the number being printed —
   * two paise on a ₹1,267 stock, four on a sub-rupee one. Reading it off each
   * value would give a row a 2-decimal price and a 4-decimal change on the
   * quiet day its move rounds below one. */
  const dp = (sym) => {
    const last = (quotes.get(sym) || {}).last;
    return last != null && Math.abs(last) < 1 ? 4 : 2;
  };
  const num = (sym, v, d) => Sym.of(sym).num(v,
    { minimumFractionDigits: d, maximumFractionDigits: d });
  const fmtLast = (sym, v) => (v == null ? DASH : num(sym, v, dp(sym)));
  const fmtChg = (sym, v) =>
    (v == null ? "" : (v < 0 ? MINUS : "+") + num(sym, Math.abs(v), dp(sym)));
  const fmtPct = (v) =>
    (v == null ? "" : (v < 0 ? MINUS : "+") + Math.abs(v).toFixed(2) + "%");
  const dirOf = (v) => (v == null || v === 0 ? "" : v > 0 ? "up" : "down");

  function watchRow(sym, current) {
    // An instrument we hold no mark for still owns the 16px — otherwise the
    // tickers below it start at a different x and the column stops being a
    // column. It carries its own initial rather than sitting blank, which is
    // what TradingView does for the same reason: a row of empty grey squares
    // reads as images that failed to load.
    const mark = Universe.logoHTML(sym)
      || `<span class="co-blank">${esc(sym[0])}</span>`;
    const name = Universe.label(sym);
    const c = wl.cols;
    return `<div class="wl-row${sym === current ? " on" : ""}" ` +
      `data-sym="${esc(sym)}" role="button" tabindex="0" ` +
      `aria-label="Open ${esc(sym)} on the chart">` +
      // the ticker column is what pays for three numeric columns at this
      // width, so a truncated name still says what it is on hover
      `<span class="wl-name" title="${esc(name === sym ? sym : sym + " · " + name)}">` +
      `${mark}<span class="t">${esc(sym)}</span></span>` +
      (c.last ? `<span class="wl-last"></span>` : "") +
      (c.chg ? `<span class="wl-chg"></span>` : "") +
      (c.pct ? `<span class="wl-pct"></span>` : "") +
      `<span class="wl-acts">` +
        // Drawn, disabled, and honestly labelled: the Alerts widget beside
        // this one is still fixture data, so there is nothing behind this
        // button. It stays in the row because that is where it belongs the
        // day there is — a control that half-works would be the worse answer.
        iconBtn("al-act", "bell",
                `Add an alert on ${esc(sym)} — alerts are not wired yet`,
                "disabled") +
        // Plain, not `danger`: a × is a dismissal, not a warning, and red on
        // this page means the price went down. Dropping a symbol from a list
        // is one click to put back. `.al-act.danger` stays for the alert
        // trash, which is the control that has earned it.
        iconBtn("al-act", "x", `Remove ${esc(sym)}`, 'data-wl="drop"') +
      `</span></div>`;
  }

  /** The three numeric cells, in place. Called on every poll and every tick,
   *  so it must never rebuild the list: a repaint under the pointer would
   *  drop the hover controls the pointer is aiming at. */
  function paintQuotes(panel) {
    for (const row of panel.querySelectorAll(".wl-row[data-sym]")) {
      const sym = row.dataset.sym, q = quotes.get(sym) || {};
      const put = (cls, text, dir) => {
        const n = row.querySelector("." + cls);
        if (!n) return;
        if (n.textContent !== text) n.textContent = text;
        if (dir === undefined) return;
        n.classList.toggle("up", dir === "up");
        n.classList.toggle("down", dir === "down");
      };
      const d = dirOf(q.change);
      put("wl-last", fmtLast(sym, q.last));
      put("wl-chg", fmtChg(sym, q.change), d);
      put("wl-pct", fmtPct(q.change_pct), d);
    }
  }

  function renderWatch(panel) {
    const list = activeList();
    const current = currentSymbol();
    const secs = plan();
    const c = wl.cols;
    planKey = keyOf(secs);
    // the numeric columns are fixed-width so a hundred rows form straight
    // edges; hiding one has to change the track list, not just the cells
    panel.style.setProperty("--wl-cols", "minmax(0, 1fr)"
      + (c.last ? " 76px" : "") + (c.chg ? " 64px" : "") + (c.pct ? " 58px" : ""));
    // A section heading is only information when there is more than one
    // section — a lone "STOCKS" over a list of stocks is furniture.
    const heads = secs.length > 1;
    const body = secs.map((s) => {
      const shut = heads && wl.folded.includes(s.name);
      return (heads
        ? `<button type="button" class="wl-sec" data-wl="fold" data-sec="${esc(s.name)}">` +
          `${Icons.svg(shut ? "chevronRight" : "chevronDown")}` +
          `<span>${esc(s.name)}</span></button>`
        : "") +
        (shut ? "" : s.syms.map((sym) => watchRow(sym, current)).join(""));
    }).join("");

    panel.innerHTML =
      head(`<button type="button" class="side-pick" data-wl="lists" ` +
           `title="Switch watchlist">${esc(list.name)}` +
           `${Icons.svg("chevronDown")}</button>`,
           iconBtn("side-act", "plus", "Add symbol", 'data-wl="add"') +
           iconBtn("side-act", "columns", "Choose columns", 'data-wl="cols"') +
           iconBtn("side-act", "more", "More", 'data-wl="more"')) +
      `<div class="side-body">` +
        (list.syms.length
          ? `<div class="wl-head"><span>Symbol</span>` +
            (c.last ? `<span class="r">Last</span>` : "") +
            (c.chg ? `<span class="r">Chg</span>` : "") +
            (c.pct ? `<span class="r">Chg%</span>` : "") +
            `</div>` + body
          : empty("list",
                  "Nothing on this list yet. Add an instrument to follow it here.",
                  "Add symbol", 'data-wl="add"')) +
      `</div>`;
    paintQuotes(panel);
  }

  function repaint(force) {
    if (openId !== "watch") return;
    const panel = el("watchPanel");
    if (force || keyOf(plan()) !== planKey) renderWatch(panel);
    else paintQuotes(panel);
  }

  /* ── prices ──────────────────────────────────────────────────────────────
   * One call for the whole visible list. Only the ACTIVE list is polled: the
   * others are not on screen, and a widget that fetches what nobody is
   * looking at is how a 5-second timer becomes a load problem.
   */
  async function fetchQuotes() {
    const syms = activeList().syms;
    if (!syms.length || quoting) return;
    quoting = true;
    try {
      const r = await fetch(`${API}/quotes?symbols=` +
                            encodeURIComponent(syms.join(",")));
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const d = await r.json();
      let learned = false;
      for (const q of d.quotes || []) {
        quotes.set(q.symbol, q);
        if (q.scope && scopes[q.symbol] !== q.scope) {
          scopes[q.symbol] = q.scope;
          learned = true;
        }
      }
      // the asset class is remembered so the NEXT open groups the rows right
      // away, instead of re-sectioning them a moment after they appear
      if (learned) Store.set(WL_SCOPES, scopes);
      repaint(false);
    } catch (e) {
      // A dead dataserver must not blank the list: the rows stay, the numbers
      // stay as of the last good answer. Saying nothing is better than saying
      // zero, and this is the one place that could invent a price.
      console.warn("[charto] quotes fetch failed", e);
    } finally {
      quoting = false;
    }
  }

  function polling(on) {
    clearInterval(quoteTimer);
    quoteTimer = null;
    if (!on) return;
    fetchQuotes();
    quoteTimer = setInterval(() => {
      if (document.visibilityState === "visible") fetchQuotes();
    }, QUOTE_MS);
  }

  // A background tab is not watching anything; come back to fresh numbers
  // rather than to whatever was on screen when it was hidden.
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible" && openId === "watch") fetchQuotes();
  });

  /* The page's own symbol streams (js/main.js). Its row is the one the user
   * is looking at hardest, so it moves with the candle instead of waiting for
   * the next poll. The stream carries a PRICE and nothing else — the previous
   * close it is measured against comes from /quotes, so a row with no quote
   * yet is left alone rather than shown a change computed from nothing. */
  document.addEventListener("charto:tick", (e) => {
    const { symbol, last } = e.detail || {};
    const q = quotes.get(symbol);
    if (!q || last == null || q.last == null) return;
    q.last = last;
    if (q.prev_close) {
      q.change = last - q.prev_close;
      q.change_pct = (last - q.prev_close) / q.prev_close * 100;
    }
    if (openId === "watch") paintQuotes(el("watchPanel"));
  });

  /* ── editing the list ──────────────────────────────────────────────────── */

  function addSymbol(sym) {
    const s = String(sym || "").trim().toUpperCase();
    const list = activeList();
    if (!s || list.syms.includes(s)) return;
    list.syms.push(s);
    // it lands in a section, and a section can be shut — adding an instrument
    // and watching nothing appear is the same as the button not working
    wl.folded = wl.folded.filter((f) => f !== sectionOf(s));
    saveWL();
    repaint(true);
    fetchQuotes();
  }

  function dropSymbol(sym) {
    const list = activeList();
    const i = list.syms.indexOf(sym);
    if (i < 0) return;
    list.syms.splice(i, 1);
    saveWL();
    repaint(true);
  }

  function openPicker(anchor) {
    Universe.open({
      anchor,
      onPick: addSymbol,
      note: `Adds to ${esc(activeList().name)}`,
    });
  }

  /** Renaming happens IN the head, where the name is — a modal over the chart
   *  to type six characters is the thing js/drawings.js already deleted once.
   *  Blur commits, Escape abandons; an empty name is an abandon, not a list
   *  called "". */
  function editName(seed, done) {
    const panel = el("watchPanel");
    const pick = panel.querySelector(".side-pick");
    if (!pick) return;
    const inp = document.createElement("input");
    inp.className = "side-rename";
    inp.value = seed;
    inp.maxLength = 32;
    inp.spellcheck = false;
    pick.replaceWith(inp);
    inp.focus();
    inp.select();
    let settled = false;
    const finish = (ok) => {
      if (settled) return;
      settled = true;
      const v = inp.value.trim().slice(0, 32);
      done(ok && v ? v : null);
    };
    inp.addEventListener("keydown", (e) => {
      e.stopPropagation();                    // never reaches the drawing layer
      if (e.key === "Enter") finish(true);
      if (e.key === "Escape") finish(false);
    });
    inp.addEventListener("blur", () => finish(true));
  }

  function newList() {
    if (wl.lists.length >= WL_MAX) return;
    editName(`List ${wl.lists.length + 1}`, (name) => {
      if (name) {
        const id = String(Date.now());
        wl.lists.push({ id, name, syms: [] });
        wl.active = id;
        saveWL();
      }
      repaint(true);
      if (name) fetchQuotes();
    });
  }

  const CHECK = Icons.svg("check", "xs");

  function listsMenu(anchor) {
    const rows = wl.lists.map((l) =>
      `<div class="item ${l.id === wl.active ? "on" : ""}" data-pick="list:${esc(l.id)}">` +
      `<span class="lead">${l.id === wl.active ? CHECK : '<span class="tick"></span>'}` +
      `${esc(l.name)}</span><span class="n">${l.syms.length}</span></div>`).join("");
    popup(anchor,
      `<div class="head">Watchlists</div>${rows}<div class="sep"></div>` +
      `<div class="item ${wl.lists.length >= WL_MAX ? "off" : ""}" data-pick="new">` +
      `<span class="lead">${Icons.svg("plus", "xs")}New list</span></div>`,
      (pick) => {
        if (pick === "new") return newList();
        const id = pick.slice(5);
        if (id === wl.active) return;
        wl.active = id;
        saveWL();
        repaint(true);
        fetchQuotes();
      });
  }

  function colsMenu(anchor) {
    const c = wl.cols;
    const only = ["last", "chg", "pct"].filter((k) => c[k]).length === 1;
    const item = (k, label) =>
      `<div class="item ${c[k] ? "on" : ""} ${c[k] && only ? "off" : ""}" ` +
      `data-pick="col:${k}"><span class="lead">${label}</span>` +
      `${c[k] ? CHECK : ""}</div>`;
    popup(anchor,
      `<div class="head">Columns</div>` +
      item("last", "Last") + item("chg", "Chg") + item("pct", "Chg%"),
      (pick) => {
        const k = pick.slice(4);
        wl.cols[k] = !wl.cols[k];
        saveWL();
        repaint(true);
      });
  }

  function moreMenu(anchor) {
    const alone = wl.lists.length < 2;
    const list = activeList();
    const sorts = Object.entries(SORTS).map(([k, label]) =>
      `<div class="item ${wl.sort === k ? "on" : ""}" data-pick="sort:${k}">` +
      `<span class="lead">${label}</span>${wl.sort === k ? CHECK : ""}</div>`).join("");
    popup(anchor,
      `<div class="head">${esc(list.name)}</div>` +
      `<div class="item" data-pick="rename"><span class="lead">` +
        `${Icons.svg("pen", "xs")}Rename list</span></div>` +
      `<div class="item ${list.syms.length ? "" : "off"}" data-pick="clear">` +
        `<span class="lead">${Icons.svg("eraser", "xs")}Clear list</span></div>` +
      `<div class="item danger ${alone ? "off" : ""}" data-pick="delete">` +
        `<span class="lead">${Icons.svg("trash", "xs")}Delete list</span></div>` +
      `<div class="head">Sort</div>${sorts}`,
      (pick) => {
        if (pick.startsWith("sort:")) {
          wl.sort = pick.slice(5);
          saveWL();
          return repaint(true);
        }
        if (pick === "rename") {
          return editName(list.name, (name) => {
            if (name) { list.name = name; saveWL(); }
            repaint(true);
          });
        }
        if (pick === "clear") {
          list.syms = [];
        } else if (pick === "delete") {
          wl.lists = wl.lists.filter((l) => l.id !== list.id);
          wl.active = wl.lists[0].id;
        }
        saveWL();
        repaint(true);
        fetchQuotes();
      });
  }

  /** Opening a symbol is a full reload of the page onto it — the same thing
   *  the header pill and the legend do, and for the same reason: chart, chat,
   *  drawings and scene all re-init against their own persisted state for
   *  that instrument rather than being swapped under a live conversation. */
  function openSymbol(sym) {
    if (!sym || sym === currentSymbol()) return;
    location.search = "?symbol=" + encodeURIComponent(sym);
  }

  /* Another tab of the same app edits the same lists. `storage` fires only in
   * the OTHER tabs, so this is the cheap way to keep them from disagreeing. */
  addEventListener("storage", (e) => {
    if (e.key !== "charto:" + WL_KEY) return;
    wl = loadWL();
    repaint(true);
  });

  /* ══ widget · alerts ═══════════════════════════════════════════════════
   * Two tabs of one subject: the standing RULES, and the MOMENTS they fired.
   * Rules are ordered armed → fired → paused, the order the eye wants: what
   * is live, what just happened, what is merely kept.
   */
  let alertTab = "alerts";
  const RANK = { armed: 0, fired: 1, paused: 2 };

  function alertRow(a) {
    // A fired alert says so where the others say when they were made; the
    // pill is the one place a state is written out rather than dotted.
    const right = a.state === "fired"
      ? '<span class="al-pill">Fired</span>'
      : `<span class="al-when">${a.when}</span>`;
    // pause/resume reads off the state, so the glyph cannot contradict the
    // dot beside it
    const toggle = a.state === "paused"
      ? iconBtn("al-act", "play", "Resume alert")
      : iconBtn("al-act", "pause", "Pause alert");
    return `<div class="al-row" data-state="${a.state}">` +
      `<span class="al-dot"></span>` +
      `<div class="al-main">` +
        `<div class="al-sym">${a.sym}<span class="ex">${a.ex}</span></div>` +
        `<div class="al-cond">${a.cond} <b>${a.level}</b></div>` +
        `<div class="al-meta">${a.meta}</div>` +
      `</div>` +
      `<div class="al-side">${right}<div class="al-acts">${toggle}` +
        iconBtn("al-act", "pen", "Edit alert") +
        iconBtn("al-act danger", "trash", "Delete alert") +
      `</div></div></div>`;
  }

  const logRow = (l) =>
    `<div class="lg-row"><span class="lg-time">${l.time}</span><div>` +
      `<div class="lg-msg"><b>${l.sym}</b> ${l.verb} ${l.level}</div>` +
      `<div class="lg-meta">${l.meta} <span class="val">${l.val}</span></div>` +
    `</div></div>`;

  /** Only the body and the tab states change when a tab is clicked — the
   *  head and the strip itself are not rebuilt, so nothing flickers. */
  function paintAlertBody() {
    const body = el("alertBody");
    if (!body) return;
    if (alertTab === "alerts") {
      body.innerHTML = MOCK.alerts.length
        ? [...MOCK.alerts].sort((a, b) => RANK[a.state] - RANK[b.state])
            .map(alertRow).join("")
        : empty("alertPlus",
                "Alerts notify you the moment your conditions are met. " +
                "Create one to get started.", "Create alert");
    } else {
      body.innerHTML = MOCK.log.length
        ? MOCK.log.map((g) => `<div class="lg-day">${g.day}</div>` +
            g.items.map(logRow).join("")).join("")
        : empty("clock",
                "Nothing has fired yet. Alerts that trigger are listed here " +
                "with what they saw.");
    }
    for (const t of el("alertsPanel").querySelectorAll(".seg-tab")) {
      const on = t.dataset.tab === alertTab;
      t.classList.toggle("active", on);
      t.setAttribute("aria-selected", String(on));
    }
  }

  /* The panel leads with the SWITCH, not with a title: the lit bell in the
   * bar already says which widget this is, and the first question inside it
   * is which of the two lists you want. Under it the same 38px toolbar the
   * watchlist head is — create on the left, the list's own controls right. */
  function renderAlerts(panel) {
    const nLog = MOCK.log.reduce((n, g) => n + g.items.length, 0);
    panel.innerHTML =
      `<div class="seg-tabs" role="tablist">` +
        `<button type="button" class="seg-tab" role="tab" data-tab="alerts">` +
          `Alerts <span class="n">${MOCK.alerts.length}</span></button>` +
        `<button type="button" class="seg-tab" role="tab" data-tab="log">` +
          `Log <span class="n">${nLog}</span></button>` +
      `</div>` +
      head(act("plus", "Create alert") + `<div class="spacer"></div>`,
           act("search", "Search alerts") + act("sort", "Sort") +
           act("more", "More")) +
      `<div class="side-body" id="alertBody"></div>`;
    paintAlertBody();
  }

  /* ══ the widget list ═══════════════════════════════════════════════════ */

  const WIDGETS = [
    // A star, not a list: see the `star` note in js/icons.js.
    { id: "watch",  panel: "watchPanel",  icon: "star", label: "Watchlist",
      render: renderWatch },
    { id: "alerts", panel: "alertsPanel", icon: "bell", label: "Alerts",
      render: renderAlerts },
  ];
  const byId = (id) => WIDGETS.find((w) => w.id === id);

  /* ══ the bar, and the header's named buttons ═══════════════════════════
   * Two renderings of the ONE list: the vertical strip on the right edge,
   * and — at laptop width, where there is room to spell the panel's name —
   * a pair of labelled buttons in the header. The stylesheet shows exactly
   * one of them (see .wtabs), and everything below writes state to BOTH, so
   * neither can be the stale one. `tabs` is optional: a build without the
   * header container still gets the bar.
   */
  const tabs = el("wtabs");

  bar.innerHTML = WIDGETS.map((w) =>
    `<button type="button" class="tool" id="wb-${w.id}" data-widget="${w.id}" ` +
    `aria-expanded="false" aria-controls="${w.panel}">${Icons.svg(w.icon)}` +
    `<span class="tip">${w.label}</span></button>`).join("");

  if (tabs) {
    tabs.innerHTML = WIDGETS.map((w) =>
      `<button type="button" class="btn wtab" id="wt-${w.id}" data-widget="${w.id}" ` +
      `aria-expanded="false" aria-controls="${w.panel}">${Icons.svg(w.icon)}` +
      `<span>${w.label}</span></button>`).join("");
  }

  /** Every control that opens this widget — the bar's icon and, at laptop
   *  width, the header's named button. */
  const ctrls = (id) => [el(`wb-${id}`), el(`wt-${id}`)].filter(Boolean);

  // the fixture log has something in it from today, so the bell starts marked
  if (MOCK.log.length && MOCK.log[0].day === "Today") {
    for (const b of ctrls("alerts")) b.classList.add("has-new");
  }

  let openId = null;

  // the breakpoint the stylesheet stacks at — below it the shell is a column
  const stackMq = window.matchMedia("(max-width: 820px) and (orientation: portrait)");
  const stacked = () => stackMq.matches;
  const chatOpen = () => !el("chatPanel").classList.contains("hidden");

  /* ══ two sidebars, and what the chart gives up for them ════════════════
   * A widget panel and the conversation are TWO columns taken off the
   * chart. On a 1512px laptop that leaves the price pane around 430px —
   * narrow enough that the five OHLC figures no longer fit beside the
   * ticker, wrap to a second line, and run at the price axis they are
   * already bounded off (see .readout's `right: 76px`). Two rows of small
   * grey numbers over the candles, to say what the axis and the crosshair
   * are both saying anyway.
   *
   * So the figures stand down while both columns are open. They are the
   * right thing to drop and the only one: the ticker line says WHICH chart
   * this is and costs a line either way, the indicator legend is the only
   * place a study can be reached, and the exact price is still on the axis
   * under the crosshair and pinned there for the last close. Nothing is
   * lost that the chart itself was not already showing.
   *
   * The class goes on <body>, not on .stage: panes are built and rebuilt
   * per layout, and this is a fact about the SHELL's columns. */
  const syncDense = () =>
    document.body.classList.toggle("two-columns", !!openId && chatOpen());

  /** show(id) — open that widget, or null to close whatever is open. One at
   *  a time: two 304px columns plus the conversation leaves no chart. */
  function show(id) {
    openId = id;
    for (const w of WIDGETS) {
      const on = w.id === id;
      el(w.panel).classList.toggle("hidden", !on);
      for (const b of ctrls(w.id)) {
        b.classList.toggle("active", on);
        b.setAttribute("aria-expanded", String(on));
      }
      // rendered on open rather than up front, so a panel is never showing a
      // state older than the moment you asked for it
      if (on) w.render(el(w.panel));
    }
    // A closed panel is not priced: the quote poll is the one thing here that
    // keeps costing after you look away, so it stops with the panel.
    polling(id === "watch");
    if (id !== "watch") closePopup();
    // looking at the alerts spends the "something fired" dot
    if (id === "alerts") for (const b of ctrls("alerts")) b.classList.remove("has-new");
    // Stacked, there is room for the chart and ONE panel. The conversation
    // goes away through its own toggle rather than by being hidden here, so
    // the chat button's state stays true.
    if (id && stacked() && chatOpen()) el("chatToggle").click();
    // last, so it reads the state the line above may just have changed
    syncDense();
    // the charts are autoSize — they re-measure themselves off the layout
  }

  const toggle = (id) => show(openId === id ? null : id);

  // one handler shape, both renderings — the bar and the header's buttons
  // carry the same data-widget, so neither needs its own branch
  const onPick = (e) => {
    const b = e.target.closest("[data-widget]");
    if (b) toggle(b.dataset.widget);
  };
  bar.addEventListener("click", onPick);
  if (tabs) tabs.addEventListener("click", onPick);

  /* ══ inside the panels ═════════════════════════════════════════════════
   * One delegated handler per panel, because a panel's body is rebuilt on
   * every render and a listener per row would be a listener per repaint.
   * Clicks are NOT stopped here: a click in a panel is a click away from
   * whatever header menu was open, and the document handler that closes those
   * is the one place that decision lives.
   */
  el("watchPanel").addEventListener("click", (e) => {
    const btn = e.target.closest("[data-wl]");
    if (btn) {
      const what = btn.dataset.wl;
      // The four that OPEN something stop the event, and only they. main.js
      // closes every `.dropdown.open` on any document click, so a menu that
      // let its own opening click reach the document would be shut by it a
      // moment after being built. Both openers below close the other menus
      // themselves first, so nothing is left hanging by stopping here.
      if (what === "lists") { e.stopPropagation(); return listsMenu(btn); }
      if (what === "cols") { e.stopPropagation(); return colsMenu(btn); }
      if (what === "more") { e.stopPropagation(); return moreMenu(btn); }
      if (what === "add") { e.stopPropagation(); return openPicker(btn); }
      if (what === "fold") {
        const sec = btn.dataset.sec;
        wl.folded = wl.folded.includes(sec)
          ? wl.folded.filter((f) => f !== sec) : [...wl.folded, sec];
        saveWL();
        return repaint(true);
      }
      if (what === "drop") {
        // the × sits inside the row, and the row navigates — removing an
        // instrument must not also open it
        e.stopPropagation();
        return dropSymbol(btn.closest(".wl-row").dataset.sym);
      }
    }
    const row = e.target.closest(".wl-row[data-sym]");
    if (row && !e.target.closest(".wl-acts")) openSymbol(row.dataset.sym);
  });

  // the rows are the panel's one keyboard target: Enter/Space opens, which is
  // what `role="button"` on them already promises
  el("watchPanel").addEventListener("keydown", (e) => {
    if (e.key !== "Enter" && e.key !== " ") return;
    const row = e.target.closest(".wl-row[data-sym]");
    if (!row) return;
    e.preventDefault();
    openSymbol(row.dataset.sym);
  });

  el("alertsPanel").addEventListener("click", (e) => {
    const t = e.target.closest(".seg-tab");
    if (t) { alertTab = t.dataset.tab; paintAlertBody(); }
    // everything else in this panel is unhandled ON PURPOSE — see the head
  });

  /* ══ the phone's one-panel rule ════════════════════════════════════════ */

  // chat.js registered its handler first, so by the time this runs the chat
  // has already flipped — if it is now showing, the panel is what gives way.
  el("chatToggle").addEventListener("click", () => {
    if (stacked() && chatOpen()) show(null);
    // the conversation is half of "two columns", so its toggle owns this
    // just as much as a panel's does — and show(null) above already ran
    // syncDense on the stacked path, which is why this is safe to repeat
    syncDense();
  });
  // Rotating a phone, or dragging a window across the breakpoint, can arrive
  // at the stacked layout with both already open — a state the column has no
  // room for and which nothing else would resolve. The conversation is the
  // product surface, so this is the one that gives way.
  stackMq.addEventListener("change", () => {
    if (stacked() && chatOpen() && openId) show(null);
    syncDense();
  });

  // No Escape-to-close: these are columns of the shell, like the chat panel,
  // not overlays over the chart — and Escape already means "cancel the
  // drawing I am halfway through" (js/drawings.js).

  /* The watchlist marks the instrument the chart is on, so it has to repaint
   * when that changes — from the header pill, the chat, or a pane selection.
   * One observer on the element carrying the fact, rather than four call
   * sites that must remember. The marks arrive with the universe fetch. */
  if (window.MutationObserver) {
    new MutationObserver(() => repaint(true))
      .observe(el("symbolName"), { childList: true, characterData: true, subtree: true });
  }
  if (typeof Universe !== "undefined") {
    Universe.load().then(() => repaint(true));
  }

  return {
    show, toggle, widgets: () => WIDGETS.map((w) => w.id),
    // the chat and the chart can put an instrument on the list without
    // knowing how one is stored
    watch: addSymbol,
    watching: () => [...activeList().syms],
  };
})();
