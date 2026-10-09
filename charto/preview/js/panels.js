/* Charto preview — the widget bar and its panels: Watchlist, Alerts.
 *
 * THE WATCHLIST IS REAL. Its lists are the user's own, persisted through
 * js/store.js, and every price on it comes from the dataserver's /quotes —
 * the same daily series the chart folds, so a row and the candles beside it
 * can never quote different numbers. A symbol the store holds nothing for
 * shows an em dash, never a filler number.
 *
 * ALERTS IS REAL TOO, as of the watcher. Its rules and its log come from
 * data/alerts.py through js/alerts.js, its controls all do the thing they say,
 * and the bell on a watchlist row opens the create dialog with that instrument
 * filled in. What is NOT claimed anywhere: that a fired alert reaches you with
 * the browser shut. It does not — the log and the bell are waiting when you
 * return, and the panel says as much rather than implying a push.
 *
 * THE SHAPE. Watchlist, Alerts and Journal are widgets of the workspace
 * (js/dock.js): each can sit in a dock beside the chart, under it, or float
 * over it, alone or as a tab of a group. Watchlist and Alerts are separate
 * widgets because they are separate subjects; Alerts keeps its own two tabs
 * (Alerts / Log) INSIDE itself, because those two are one subject seen twice.
 * This file owns what is IN each panel; the dock owns where it is.
 */
"use strict";

const Panels = (() => {
  const el = (id) => document.getElementById(id);
  const bar = el("wbar");
  if (!bar) return {};

  // same-origin behind a proxy, explicit port in local dev (see main.js)
  const API = location.port === "5173"
    ? "http://127.0.0.1:5174" : "";

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
    // `data-al` as well as `data-wl`: the alerts head hangs its own two menus
    // here, and keying only off the watchlist's attribute made both of them
    // read as "no anchor", so the lit button could not be clicked shut.
    const key = anchor.dataset.wl || anchor.dataset.al || "";
    const again = popEl && popEl.classList.contains("open")
      && popEl.dataset.for === key;
    closePopup();
    if (again) return;                       // clicking the lit button closes
    if (window.__chartoCloseMenus) window.__chartoCloseMenus(null);
    const pop = document.createElement("div");
    pop.className = "dropdown floating open wl-menu";
    pop.dataset.for = key;
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
              pct: cols.pct !== false, open: !!cols.open, high: !!cols.high,
              low: !!cols.low, prev: !!cols.prev, range: !!cols.range },
      sort: SORTS[(raw || {}).sort] ? raw.sort : "manual",
      folded: Array.isArray((raw || {}).folded) ? raw.folded.map(String) : [],
    };
    // every column off would leave the hover controls nowhere to land
    if (!state.cols.last && !state.cols.chg && !state.cols.pct) state.cols.pct = true;
    return state;
  }

  function saveWL() { Store.set(WL_KEY, wl); }

  /* The numeric columns, in the order they are drawn, with their widths and
   * the order they give way when the tile is too narrow for all of them:
   * the ticker always keeps ~110px, so a narrow watchlist drops Day range,
   * then Prev, Open, High, Low, Chg — Chg% and Last go last. */
  const COLS = [
    { k: "last", label: "Last", w: 76, drop: 7 },
    { k: "open", label: "Open", w: 70, drop: 3 },
    { k: "high", label: "High", w: 70, drop: 4 },
    { k: "low", label: "Low", w: 70, drop: 5 },
    { k: "prev", label: "Prev", w: 70, drop: 2 },
    { k: "chg", label: "Chg", w: 64, drop: 6 },
    { k: "pct", label: "Chg%", w: 58, drop: 8 },
    { k: "range", label: "Day", w: 58, drop: 1 },
  ];
  const wcfg = () => (typeof Dock !== "undefined" && Dock.cfgOf ? Dock.cfgOf("watch") : {}) || {};
  /** The columns that fit in `width`, from the ones switched on. */
  function fitCols(width) {
    let on = COLS.filter((c) => wl.cols[c.k]);
    const room = () => width - 116 - on.reduce((a, c) => a + c.w, 0);
    for (const c of [...on].sort((a, b) => a.drop - b.drop)) {
      if (room() >= 0 || on.length <= 1) break;
      on = on.filter((x) => x !== c);
    }
    return on;
  }
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
    if (wcfg().groups === false) {
      const one = [...syms];
      if (wl.sort === "az") one.sort((a, b) => a.localeCompare(b));
      if (wl.sort === "pct") one.sort((a, b) => ((quotes.get(b) || {}).change_pct ?? -1e9) - ((quotes.get(a) || {}).change_pct ?? -1e9));
      return [{ name: "All", syms: one }];
    }
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
    const cfg = wcfg();
    const mark = cfg.logos === false ? "" : Universe.logoHTML(sym)
      || `<span class="co-blank">${esc(sym[0])}</span>`;
    const name = Universe.label(sym);
    const cols = shownCols;
    return `<div class="wl-row${sym === current ? " on" : ""}" ` +
      `data-sym="${esc(sym)}" role="button" tabindex="0" ` +
      `aria-label="Open ${esc(sym)} on the chart">` +
      // the ticker column is what pays for three numeric columns at this
      // width, so a truncated name still says what it is on hover
      `<span class="wl-name" title="${esc(name === sym ? sym : sym + " · " + name)}">` +
      `${mark}<span class="t">${esc(sym)}${cfg.names && name && name !== sym ? `<em>${esc(name)}</em>` : ""}</span></span>` +
      cols.map((c) => c.k === "range" ? `<span class="wl-range"><i></i></span>` : `<span class="wl-${c.k}"></span>`).join("") +
      `<span class="wl-acts">` +
        // Awake now. It opens the create dialog with this instrument already
        // filled in, which is the whole reason the button lives in the row
        // rather than only in the alerts panel. Signed out it stays visibly
        // unavailable and says why, rather than opening a dialog that could
        // only end in a refusal.
        iconBtn("al-act", "bell",
                Auth.user ? `Add an alert on ${esc(sym)}`
                          : `Sign in to add an alert on ${esc(sym)}`,
                Auth.user ? 'data-wl="alert"' : "disabled") +
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
      const change = q.change_pct == null ? NaN : Number(q.change_pct);
      row.style.setProperty("--wl-heat", Number.isFinite(change)
        ? `color-mix(in srgb, var(--${change < 0 ? "down" : "up"}) ${Math.min(28, 5 + Math.abs(change) * 5)}%, var(--surface-plain))`
        : "var(--surface-plain)");
      const put = (cls, text, dir) => {
        const n = row.querySelector("." + cls);
        if (!n) return;
        if (n.textContent !== text) n.textContent = text;
        if (dir === undefined) return;
        n.classList.toggle("up", dir === "up");
        n.classList.toggle("down", dir === "down");
      };
      const d = dirOf(q.change);
      const lastEl = row.querySelector(".wl-last");
      const was = lastEl ? lastEl.textContent : null;
      put("wl-last", fmtLast(sym, q.last));
      put("wl-chg", fmtChg(sym, q.change), d);
      put("wl-pct", fmtPct(q.change_pct), d);
      put("wl-open", fmtLast(sym, q.open));
      put("wl-high", fmtLast(sym, q.high));
      put("wl-low", fmtLast(sym, q.low));
      put("wl-prev", fmtLast(sym, q.prev_close));
      // where the price sits in today's range: a mark on a short bar
      const rg = row.querySelector(".wl-range i");
      if (rg) {
        const ok = q.high != null && q.low != null && q.last != null && q.high > q.low;
        rg.style.left = ok ? ((q.last - q.low) / (q.high - q.low) * 100).toFixed(1) + "%" : "50%";
        rg.parentNode.style.opacity = ok ? "" : ".3";
        rg.parentNode.title = ok ? `Low ${fmtLast(sym, q.low)} · High ${fmtLast(sym, q.high)}` : "No range yet";
      }
      // a price that moved tints its row for a moment, up or down
      if (lastEl && was && was !== lastEl.textContent && was !== DASH && wcfg().flash !== false) {
        const up = parseFloat(lastEl.textContent.replace(/[^\d.-]/g, "")) > parseFloat(was.replace(/[^\d.-]/g, ""));
        row.classList.remove("tick-up", "tick-down");
        void row.offsetWidth;
        row.classList.add(up ? "tick-up" : "tick-down");
      }
    }
  }

  let shownCols = [];
  function renderWatch(panel) {
    const list = activeList();
    const current = typeof Dock !== "undefined" && Dock.symbolOf ? Dock.symbolOf("watch") : currentSymbol();
    const secs = plan();
    const cfg = wcfg();
    planKey = keyOf(secs);
    shownCols = cfg.view === "heatmap" ? COLS.filter((c) => ["last", "pct"].includes(c.k))
      : fitCols(panel.clientWidth || 320);
    panel.dataset.colw = String(panel.clientWidth || 0);
    panel.classList.toggle("wl-compact", cfg.density === "compact");
    panel.classList.toggle("wl-two", !!cfg.names);
    panel.classList.toggle("wl-heatmap", cfg.view === "heatmap");
    // the numeric columns are fixed-width so a hundred rows form straight
    // edges; hiding one has to change the track list, not just the cells
    panel.style.setProperty("--wl-cols", "minmax(0, 1fr)" + shownCols.map((c) => ` ${c.w}px`).join(""));
    // A section heading is only information when there is more than one
    // section — a lone "STOCKS" over a list of stocks is furniture.
    const heads = secs.length > 1 && cfg.groups !== false;
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
            shownCols.map((c) => `<span class="r">${c.label}</span>`).join("") +
            `</div>` + body
          : empty("list",
                  "Nothing on this list yet. Add an instrument to follow it here.",
                  "Add symbol", 'data-wl="add"')) +
      `</div>`;
    paintQuotes(panel);
  }

  function repaint(force) {
    if (!on("watch")) return;
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
      const r = await Net.get(`${API}/quotes?symbols=` +
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
    }, Number(wcfg().refresh) || QUOTE_MS);
  }

  // A background tab is not watching anything; come back to fresh numbers
  // rather than to whatever was on screen when it was hidden.
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible" && on("watch")) fetchQuotes();
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
    if (on("watch")) paintQuotes(el("watchPanel"));
  });

  /* ── editing the list ──────────────────────────────────────────────────── */

  /** Add to the ACTIVE list, or to a named one.
   *
   *  `listId` is what the chart's context menu needs: it offers every list by
   *  name, so "add to Watchlist 2" must not mean "switch to Watchlist 2 and
   *  add" — the panel the reader has open is not the panel they are filing
   *  into, and moving it under them is a side effect nobody asked for. An
   *  unknown id falls back to the active list rather than dropping the
   *  symbol, which is the failure a user would never see the reason for. */
  function addSymbol(sym, listId) {
    const s = String(sym || "").trim().toUpperCase();
    const list = (listId && wl.lists.find((l) => l.id === listId)) || activeList();
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
    const only = COLS.filter((x) => c[x.k]).length === 1;
    const item = (k, label) =>
      `<div class="item ${c[k] ? "on" : ""} ${c[k] && only ? "off" : ""}" ` +
      `data-pick="col:${k}"><span class="lead">${label}</span>` +
      `${c[k] ? CHECK : ""}</div>`;
    popup(anchor,
      `<div class="head">Columns</div>` +
      COLS.map((x) => item(x.k, x.k === "range" ? "Day range" : x.k === "prev" ? "Prev close" : x.label)).join(""),
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

  /** Opening a symbol goes through the workspace: in place on a selected
   *  secondary pane, else a navigation the dock keeps instant — the bars are
   *  warmed on hover (below) and the new page paints them before it fetches.
   *  The watchlist itself survives the trip because the dock's layout does. */
  const openSymbol = (sym) => Dock.pick ? Dock.pick("watch", sym) : Dock.openSymbol(sym);

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
   *
   * Every row below is now the SERVER's, through js/alerts.js — the fixture
   * this section used to carry is gone, and so is the note explaining why the
   * buttons did nothing. The rendering is unchanged on purpose: the fixture was
   * written at the real width with real instruments precisely so that the day
   * an engine arrived, the markup would not have to.
   */
  let alertTab = "alerts";
  let alertQuery = "";
  let alertSort = "state";
  let alertSearchOpen = false;
  const RANK = { armed: 0, fired: 1, paused: 2 };

  /** A creation date, or the time it fired — the row's right-hand cell. */
  function whenOf(a) {
    const ts = a.state === "fired" && a.fired_at ? a.fired_at : a.created;
    if (!ts) return "";
    const d = new Date(ts * 1000);
    const today = new Date();
    const sameDay = d.toDateString() === today.toDateString();
    return sameDay
      ? d.toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit",
                                        hour12: false })
      : d.toLocaleDateString("en-IN", { day: "2-digit", month: "short" });
  }

  function alertRow(a) {
    // A fired alert says so where the others say when they were made; the
    // pill is the one place a state is written out rather than dotted.
    const right = a.state === "fired"
      ? '<span class="al-pill">Fired</span>'
      : `<span class="al-when">${esc(whenOf(a))}</span>`;
    // pause/resume reads off the state, so the glyph cannot contradict the
    // dot beside it
    const toggle = a.state === "paused" || a.state === "fired"
      ? iconBtn("al-act", "play", a.state === "fired" ? "Re-arm alert" : "Resume alert", 'data-al="toggle"')
      : iconBtn("al-act", "pause", "Pause alert", 'data-al="toggle"');
    // A rule the engine paused because an address stopped resolving carries
    // the reason in its note. That belongs on the row: a paused alert with no
    // stated reason reads as one the user paused.
    const why = /\[paused: /.test(a.note || "")
      ? `<div class="al-meta warn">${esc((a.note.match(/\[paused: ([^\]]+)\]/)
                                          || [, ""])[1])}</div>` : "";
    return `<div class="al-row" data-state="${esc(a.state)}" ` +
      `data-id="${a.id}" data-sym="${esc(a.symbol)}">` +
      `<span class="al-dot"></span>` +
      `<div class="al-main">` +
        `<div class="al-sym">${esc(a.symbol)}` +
          `<span class="ex">${esc(Sym.of(a.symbol).venue)}</span></div>` +
        `<div class="al-cond">${esc(a.cond)} <b>${esc(a.level)}</b></div>` +
        `<div class="al-meta">${esc(a.meta)}</div>${why}` +
      `</div>` +
      `<div class="al-side">${right}<div class="al-acts">${toggle}` +
        iconBtn("al-act", "pen", "Edit alert", 'data-al="edit"') +
        iconBtn("al-act danger", "trash", "Delete alert", 'data-al="del"') +
      `</div></div></div>`;
  }

  /* The log line is the EVIDENCE record: what fired, against what level, and
   * the value it actually saw. `late` means the engine found it on a catch-up
   * scan rather than live — a fact about the reading, so it is on the reading. */
  const mailState = (l) => {
    const labels = { pending: "Email queued", sent: "Email sent", failed: "Email failed",
      uncertain: "Email unconfirmed", expired: "Email expired", cancelled: "Email cancelled",
      disabled: "Email off", sending: "Email sending" };
    return labels[l.email_status]
      ? ` · <span title="${l.email_status === "sent" ? "Accepted by Google SMTP; inbox delivery is not guaranteed" : "Email delivery status"}">${labels[l.email_status]}</span>` : "";
  };
  const logRow = (l) =>
    `<div class="lg-row" data-sym="${esc(l.symbol)}">` +
      `<span class="lg-time">${esc(hhmm(l.ts))}</span><div>` +
      `<div class="lg-msg"><b>${esc(l.symbol)}</b> ${esc(l.verb)} ` +
        `${esc(l.level)}</div>` +
      `<div class="lg-meta">${esc(l.meta)} ` +
        `<span class="val">${esc(fmtVal(l.value))}</span>` +
        (l.late ? ` <span class="lg-late">found late</span>` : "") +
        mailState(l) +
      `</div></div></div>`;

  const hhmm = (ts) => new Date(ts * 1000).toLocaleTimeString("en-IN",
    { hour: "2-digit", minute: "2-digit", hour12: false });
  const fmtVal = (v) => (v == null ? "—"
    : Number(v).toLocaleString("en-IN", { maximumFractionDigits: 4 }));

  /** Day headings, computed rather than stored — "Today" has to still say
   *  Today tomorrow, which a server-side string could not. */
  function logGroups(rows) {
    const out = [];
    let last = null;
    for (const l of rows) {
      const d = new Date(l.ts * 1000);
      const key = d.toDateString();
      if (key !== last) {
        const days = Math.round((Date.now() - d.getTime()) / 86400000);
        out.push({ day: d.toDateString() === new Date().toDateString() ? "Today"
                   : days <= 1 ? "Yesterday"
                   : d.toLocaleDateString("en-IN", { weekday: "short",
                       day: "2-digit", month: "short" }), items: [] });
        last = key;
      }
      out[out.length - 1].items.push(l);
    }
    return out;
  }

  function visibleAlerts() {
    const q = alertQuery.trim().toLowerCase();
    const show = (Dock.cfgOf ? Dock.cfgOf("alerts") : {}).show || "all";
    let rows = Alerts.state.alerts.filter((a) => (show === "all" || a.state === show) && !q
      || (a.symbol + " " + a.cond + " " + a.level + " " + (a.note || ""))
         .toLowerCase().includes(q));
    rows = [...rows];
    if (alertSort === "symbol") rows.sort((a, b) => a.symbol.localeCompare(b.symbol));
    else if (alertSort === "created") rows.sort((a, b) => b.created - a.created);
    else rows.sort((a, b) => (RANK[a.state] - RANK[b.state])
                            || b.created - a.created);
    return rows;
  }

  /** Only the body and the tab states change when a tab is clicked — the
   *  head and the strip itself are not rebuilt, so nothing flickers. */
  function paintAlertBody() {
    const body = el("alertBody");
    if (!body) return;
    const st = Alerts.state;
    if (!Auth.user) {
      // The same boundary layouts already draw, and for the same reason: an
      // alert runs on the server so it can fire while this browser is shut,
      // which makes it a thing an ACCOUNT owns and not a tab.
      body.innerHTML = empty("bell",
        "Alerts run on the server and keep watching while you are away. " +
        "Sign in to create one.", "Sign in", 'data-al="signin"');
    } else if (!st.loaded) {
      body.innerHTML = `<div class="side-empty"><p>Loading…</p></div>`;
    } else if (st.error) {
      body.innerHTML = `<div class="side-empty"><p>${esc(st.error)}</p></div>`;
    } else if (alertTab === "alerts") {
      const rows = visibleAlerts();
      body.innerHTML = rows.length
        ? rows.map(alertRow).join("")
        : (st.alerts.length
           ? `<div class="side-empty"><p>No alert matches “${esc(alertQuery)}”.</p></div>`
           : empty("alertPlus",
                   "Alerts notify you the moment your conditions are met. " +
                   "Create one to get started.", "Create alert",
                   'data-al="new"'));
    } else {
      const groups = logGroups(st.log);
      body.innerHTML = groups.length
        ? groups.map((g) => `<div class="lg-day">${esc(g.day)}</div>` +
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
    const st = Alerts.state;
    // The feed line, when there is something to say. A watcher whose prices
    // stopped arriving must never look like a watcher that saw nothing happen.
    const streams = (st.feed && st.feed.streams) || {};
    const live = Object.keys(streams).some((v) => streams[v].connected);
    const feedNote = (Auth.user && st.alerts.some((a) => a.state === "armed")
                      && !live)
      ? `<div class="al-feednote">No price feed is connected. Alerts will be ` +
        `checked when one reconnects.</div>`
      : "";
    panel.innerHTML =
      `<div class="seg-tabs" role="tablist">` +
        `<button type="button" class="seg-tab" role="tab" data-tab="alerts">` +
          `Alerts <span class="n">${st.alerts.length}</span></button>` +
        `<button type="button" class="seg-tab" role="tab" data-tab="log">` +
          `Log <span class="n">${st.log.length}</span></button>` +
      `</div>` +
      head(iconBtn("side-act", "plus", "Create alert", 'data-al="new"') +
           `<div class="spacer"></div>`,
           iconBtn("side-act", "search", "Search alerts", 'data-al="search"') +
           iconBtn("side-act", "sort", "Sort", 'data-al="sort"') +
           iconBtn("side-act", "more", "More", 'data-al="more"')) +
      // The same box the symbol search is — Icons.field(), not a bordered
      // .dlg-input. A filter is a filter wherever it opens.
      (alertQuery || alertSearchOpen
        ? `<div class="al-searchbar">` +
          Icons.field(`<input id="alSearch" type="search" ` +
            `placeholder="Filter by symbol or condition" ` +
            `value="${esc(alertQuery)}" autocomplete="off" spellcheck="false">`) +
          `</div>` : "") +
      feedNote +
      `<div class="side-body" id="alertBody"></div>`;
    paintAlertBody();
    const s = el("alSearch");
    if (s && alertSearchOpen) s.focus();
  }

  /* ══ the widgets, handed to the workspace ══════════════════════════════
   * The three panels used to be one-at-a-time columns this file opened and
   * shut itself. They are dock widgets now (js/dock.js): the dock decides
   * where each one sits and whether it is on screen, and tells this file
   * through show/hide — which is all the panels ever needed to know, since
   * the only thing that keeps costing while a panel is away is the watchlist's
   * quote poll. The existing <aside>s are adopted as the widgets' hosts, so
   * every delegated handler below keeps working on the same element. */
  const showing = new Set();
  const on = (id) => showing.has(id);

  const widget = (id, extra) => Dock.register({
    type: id, single: true, zone: "right", host: () => el(extra.panel),
    mount: (host, ctx) => ({
      show() {
        showing.add(id);
        // rendered on show rather than up front, so a panel is never
        // showing a state older than the moment you asked for it
        extra.render(el(extra.panel), ctx.cfg);
        if (extra.onShow) extra.onShow();
      },
      hide() { showing.delete(id); if (extra.onHide) extra.onHide(); },
      // a setting changed (or the link group's symbol): the panel redraws
      config(cfg, patch) { if (extra.onConfig) extra.onConfig(cfg, patch); else if (on(id)) extra.render(el(extra.panel), cfg); },
      ask: extra.ask,
      actions: extra.actions ? extra.actions(ctx) : [],
    }),
    ...extra,
  });

  // A star, not a list: see the `star` note in js/icons.js.
  // a resize that changes which columns fit redraws the list
  if (typeof ResizeObserver !== "undefined") {
    new ResizeObserver(() => {
      const p = el("watchPanel");
      if (!on("watch") || !p.clientWidth) return;
      const count = wcfg().view === "heatmap" ? 2 : fitCols(p.clientWidth).length;
      if (count !== shownCols.length) repaint(true);
    }).observe(el("watchPanel"));
  }
  const colGet = () => COLS.filter((c) => wl.cols[c.k]).map((c) => c.k);
  const colSet = (v) => { for (const c of COLS) wl.cols[c.k] = v.includes(c.k); saveWL(); repaint(true); };

  widget("watch", {
    panel: "watchPanel", icon: "star", title: "Watchlist", shortcut: "watchlist",
    hue: "amber", group: "Market", anim: "spin", minW: 290, linkable: true,
    actions: (ctx) => [
      { icon: "plus", label: "Add symbol", run: (button) => openPicker(button) },
      { icon: "alertPlus", label: "Add alert", run: () => Alerts.open({ symbol: ctx.symbol() }) },
      { icon: "widgets", label: "Watchlist view", run: (button) => ctx.menu(button,
        [{ id: "table", label: "Table", icon: "list", on: ctx.cfg.view !== "heatmap" },
         { id: "heatmap", label: "Heatmap", icon: "widgets", on: ctx.cfg.view === "heatmap" }],
        (view) => ctx.setCfg({ view })) },
      { icon: "sort", label: "Sort watchlist", run: (button) => ctx.menu(button,
        [{ id: "manual", label: "As added", icon: "list", on: wl.sort === "manual" },
         { id: "az", label: "A → Z", icon: "sort", on: wl.sort === "az" },
         { id: "pct", label: "Change %", icon: "opRise", on: wl.sort === "pct" }],
        (sort) => { wl.sort = sort; saveWL(); repaint(true); }) },
    ],
    settings: [
      { section: "Columns" },
      { key: "view", label: "View", def: "table", options: [{ v: "table", label: "Table" }, { v: "heatmap", label: "Heatmap" }] },
      { key: "cols", label: "Shown", kind: "chips", min: 1, def: ["last", "chg", "pct"], get: colGet, set: colSet,
        hint: "A narrow tile drops the extra ones first",
        options: COLS.map((c) => ({ v: c.k, label: c.k === "range" ? "Day range" : c.k === "prev" ? "Prev close" : c.label })) },
      { key: "sort", label: "Order", def: "manual", get: () => wl.sort, set: (v) => { wl.sort = v; saveWL(); repaint(true); },
        options: [{ v: "manual", label: "As added" }, { v: "az", label: "A → Z" }, { v: "pct", label: "Change %" }] },
      { section: "Rows" },
      { key: "groups", label: "Group by kind", kind: "toggle", def: true, hint: "Indices, stocks, commodities, crypto" },
      { key: "names", label: "Company name under the ticker", kind: "toggle", def: false },
      { key: "logos", label: "Logos", kind: "toggle", def: true },
      { key: "density", label: "Row height", def: "comfortable", options: [{ v: "comfortable", label: "Comfortable" }, { v: "compact", label: "Compact" }] },
      { key: "flash", label: "Flash when a price moves", kind: "toggle", def: true },
      { section: "Prices" },
      { key: "refresh", label: "Update every", def: 5000, options: [{ v: 2000, label: "2s" }, { v: 5000, label: "5s" }, { v: 15000, label: "15s" }] },
    ],
    key: "Alt W", desc: "Your lists, priced live",
    render: renderWatch,
    onShow: () => polling(true),
    onHide: () => { polling(false); closePopup(); },
    onConfig: (cfg, patch) => { if ("refresh" in patch && on("watch")) polling(true); repaint(true); },
    ask: () => {
      const l = activeList();
      return l.syms.length
        ? { sub: `${l.name} · ${l.syms.length} instrument${l.syms.length === 1 ? "" : "s"}`,
            context: `My "${l.name}" watchlist: ${l.syms.join(", ")}.`,
            question: "Compare these: which are strongest on the chart right now, and why?" } : null;
    },
  });
  const perm = () => ("Notification" in window ? Notification.permission : "unsupported");
  widget("alerts", {
    panel: "alertsPanel", icon: "bell", title: "Alerts", shortcut: "alerts",
    hue: "coral", group: "Market", anim: "ring",
    settings: [
      { section: "List" },
      { key: "sort", label: "Order", def: "state", get: (cfg) => cfg.sort || alertSort,
        set: (v, cfg) => { cfg.sort = v; alertSort = v; paintAlertBody(); },
        options: [{ v: "state", label: "State" }, { v: "symbol", label: "Symbol" }, { v: "created", label: "Newest" }] },
      { key: "show", label: "Show", def: "all",
        options: [{ v: "all", label: "All" }, { v: "armed", label: "Armed" }, { v: "fired", label: "Fired" }, { v: "paused", label: "Paused" }] },
      { key: "seenOnOpen", label: "Mark the log read when I open it", kind: "toggle", def: false },
      { section: "When one fires" },
      { key: "notify", label: "Desktop notification", kind: "toggle", def: true, hint: "Only when this tab is not showing the alert" },
      { kind: "action", label: "This browser has not been asked yet", button: "Allow", when: () => perm() === "default",
        run: () => Alerts.allowNotifications() },
      { kind: "note", label: "Notifications are blocked for this site in the browser's settings.", when: () => perm() === "denied" },
      { key: "sound", label: "Play a sound", kind: "toggle", def: false },
      { kind: "action", label: "Hear it", button: "Play", when: (cfg) => !!cfg.sound, run: () => Alerts.chime() },
    ],
    onConfig: () => { if (on("alerts")) paintAlertBody(); },
    key: "Alt A", desc: "Rules and what fired",
    render: renderAlerts,
    // Opening the panel asks the server for the current truth — a tab that
    // was in the background through a fire has a stale list.
    onShow: () => {
      const c = Dock.cfgOf ? Dock.cfgOf("alerts") : {};
      if (c.sort) alertSort = c.sort;
      if (Auth.user) Alerts.load().then(() => { if (c.seenOnOpen && Alerts.state.unseen) Alerts.markSeen(); });
    },
    onHide: closePopup,
  });
  /* Patterns used to be a rail widget too. It moved to the chart's own
   * top-right corner (js/layers-panel.js): the rail is for places you GO,
   * and the layers list is about this chart, this second. */
  widget("journal", {
    panel: "journalPanel", icon: "fileText", title: "Journal", hue: "sand", group: "Tools",
    desc: "Your trades and their outcomes",
    render: (host, cfg) => Journal.renderSidebar(host, cfg),
    settings: [
      { section: "Trade log" },
      { key: "tab", label: "Open with", def: "summary",
        options: [{ v: "summary", label: "Trade log" }, { v: "new", label: "New trade" }] },
      { key: "sort", label: "First", def: "recent",
        options: [{ v: "recent", label: "Recent" }, { v: "pnl", label: "P&L" }, { v: "symbol", label: "Symbol" }] },
      { kind: "note", label: "Filters and review details stay in the full journal." },
    ],
  });
  bar.innerHTML = "";

  /** The bell's dot is the UNSEEN COUNT, not "there is a log" — the fixture
   *  version marked it whenever today had a row, which meant it lit again on
   *  every reload of something already read. */
  function syncBell() {
    // the dock shows it on the Alerts tab, its hub card and the Widgets button
    if (typeof Dock !== "undefined" && Dock.badge) Dock.badge("alerts", Alerts.state.unseen > 0);
  }

  /* One subscription, three jobs: keep the bell honest, keep an open panel
   * current, and keep a watchlist row's bell in step with being signed in.
   * Only the panel actually on screen re-renders. */
  Alerts.onChange(() => {
    syncBell();
    if (on("alerts")) renderAlerts(el("alertsPanel"));
  });
  Auth.onChange(() => {
    syncBell();
    if (on("alerts")) renderAlerts(el("alertsPanel"));
    if (on("watch")) repaint(true);        // the bells' enabled state
  });
  setTimeout(syncBell, 0);                  // the rail is built on Dock.start

  const show = (id) => {
    if (id) return Dock.open(id);
    for (const w of ["watch", "alerts", "journal"]) {
      for (const i of Dock.instances(w)) Dock.close(i);
    }
  };
  const toggle = (id) => Dock.toggle(id);

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
      if (what === "alert") {
        // the bell sits inside the row, and the row navigates — arming an
        // alert must not also switch the chart
        e.stopPropagation();
        return Alerts.open({ symbol: btn.closest(".wl-row").dataset.sym });
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

  // a row the pointer rests on is a row about to be clicked: fetch its bars
  for (const p of ["watchPanel", "alertsPanel"]) {
    el(p).addEventListener("pointerover", (e) => {
      const row = e.target.closest("[data-sym]");
      if (row) Dock.warm(row.dataset.sym);
    });
  }

  // the rows are the panel's one keyboard target: Enter/Space opens, which is
  // what `role="button"` on them already promises
  el("watchPanel").addEventListener("keydown", (e) => {
    if (e.key !== "Enter" && e.key !== " ") return;
    const row = e.target.closest(".wl-row[data-sym]");
    if (!row) return;
    e.preventDefault();
    openSymbol(row.dataset.sym);
  });

  /* The alerts panel, wired. Same delegated shape as the watchlist's, for the
   * same reason: the body is rebuilt on every render and a listener per row
   * would be a listener per repaint. */
  el("alertsPanel").addEventListener("click", (e) => {
    const t = e.target.closest(".seg-tab");
    if (t) {
      alertTab = t.dataset.tab;
      paintAlertBody();
      // Opening the log is what READS it, so that is where the dot is spent —
      // not on opening the panel, which may well be the rules you wanted.
      if (alertTab === "log") Alerts.markSeen();
      return;
    }
    const btn = e.target.closest("[data-al]");
    if (btn) {
      const what = btn.dataset.al;
      const row = btn.closest(".al-row");
      const id = row ? Number(row.dataset.id) : 0;
      if (what === "new") return Alerts.open({ symbol: currentSymbol() });
      if (what === "signin") {
        e.stopPropagation();
        const b = el("authBtn") || el("signInBtn");
        return b ? b.click() : Alerts.toast("Use the account button to sign in");
      }
      if (what === "search") {
        e.stopPropagation();
        alertSearchOpen = !alertSearchOpen;
        if (!alertSearchOpen) alertQuery = "";
        return renderAlerts(el("alertsPanel"));
      }
      if (what === "sort") {
        e.stopPropagation();
        return sortMenu(btn);
      }
      if (what === "more") {
        e.stopPropagation();
        return alertsMoreMenu(btn);
      }
      // The three row controls. Each one is confirmed by the list changing
      // under it, so none of them needs a spinner — but a failure has to say
      // so rather than leaving a row that silently did not move.
      const fail = (err) => Alerts.toast(err.message || String(err));
      if (what === "toggle") {
        const a = Alerts.state.alerts.find((x) => x.id === id);
        return a && Alerts.toggle(a).catch(fail);
      }
      if (what === "edit") return Alerts.open({ edit: id });
      if (what === "del") return Alerts.remove(id).catch(fail);
    }
    // A row that is not a control is a way to the instrument it is about —
    // the same thing a watchlist row does, and the reason the log carries a
    // symbol at all.
    const r = e.target.closest(".al-row[data-sym], .lg-row[data-sym]");
    if (r && !e.target.closest(".al-acts")) Dock.openSymbol(r.dataset.sym);
  });

  el("alertsPanel").addEventListener("input", (e) => {
    if (e.target.id !== "alSearch") return;
    alertQuery = e.target.value;
    paintAlertBody();
  });

  /* The pattern drawer's refresh hook lived here while it was one of this
   * shell's columns. It is a popover now and renders itself against its own
   * host (js/layers-panel.js listens for `charto:layers-refresh`), so this
   * shell no longer has an open-panel state to keep for it. */

  /* Two small menus, hung off the head the way the watchlist's are and
   * appended to <body> for the same reason — a 302px column with its own
   * scroller would clip them. */
  function sortMenu(anchor) {
    const pick = (v, label) =>
      `<div class="item" data-pick="sort:${v}">` +
      `<span class="tick">${alertSort === v ? Icons.svg("check", "xs") : ""}</span>` +
      `<span class="lead">${label}</span></div>`;
    popup(anchor,
      `<div class="head">Sort by</div>` + pick("state", "State") +
      pick("symbol", "Symbol") + pick("created", "Newest first"),
      (v) => { alertSort = v.split(":")[1]; paintAlertBody(); });
  }

  function alertsMoreMenu(anchor) {
    const n = (s) => Alerts.state.alerts.filter((a) => a.state === s).length;
    const armed = n("armed"), paused = n("paused"), fired = n("fired");
    const item = (act, label, off) =>
      `<div class="item${off ? " off" : ""}" ` +
      `${off ? "" : `data-pick="${act}"`}><span class="lead">${label}</span></div>`;
    popup(anchor,
      item("pause-all", `Pause all (${armed})`, !armed) +
      item("arm-all", `Resume all (${paused})`, !paused) +
      `<div class="sep"></div>` +
      item("clear-fired", `Delete the ${fired} fired`, !fired) +
      item("mark-seen", "Mark the log read", !Alerts.state.unseen),
      async (act) => {
        try {
          if (act === "mark-seen") return await Alerts.markSeen();
          // Sequential, not Promise.all: these are writes to one SQLite file
          // and firing thirty at once buys nothing but lock contention.
          for (const a of [...Alerts.state.alerts]) {
            if (act === "pause-all" && a.state === "armed")
              await Alerts.patch(a.id, { state: "paused" });
            if (act === "arm-all" && a.state === "paused")
              await Alerts.patch(a.id, { state: "armed" });
            if (act === "clear-fired" && a.state === "fired")
              await Alerts.remove(a.id);
          }
        } catch (err) { Alerts.toast(err.message || String(err)); }
      });
  }

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
    show, toggle, widgets: () => ["watch", "alerts", "journal"],
    // which panel is on screen — js/alerts.js asks so it can skip the OS
    // notification for a fire you are already looking at
    openWidget: () => ["watch", "alerts", "journal"].find(on) || null,
    // the chat and the chart can put an instrument on the list without
    // knowing how one is stored
    watch: addSymbol,
    watching: () => [...activeList().syms],
    /** Every list, with what is already on it — so a menu offering them can
     *  tick the ones this symbol is on rather than offering to add it twice.
     *  A copy: nothing outside this file writes the store. */
    lists: () => wl.lists.map((l) => ({ id: l.id, name: l.name,
                                        syms: [...l.syms],
                                        active: l.id === wl.active })),
  };
})();
