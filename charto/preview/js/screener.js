/* Charto preview — the Screener widget.
 *
 * A front for the screen engine the chat already uses (/screen/run): the
 * same features, the same filters, the same ranking, so a list built here and
 * a list the chat quotes can never disagree. Nothing is computed in this
 * file. The engine filters and ranks; /screen/features supplies the 1-day
 * move for the rows on screen; this file only draws them and remembers what
 * you asked for.
 *
 * What it is honest about. Every figure is an end-of-day value as of the
 * date the engine reports, over the universe it reports, and the footer says
 * both — "11 of 500 · as of 22 Jul" — every time. A symbol that lacks the
 * history a filter needs is excluded by the engine, never shown as zero.
 *
 * A screener exists to put an instrument on the chart, so that is the row's
 * click, and the bars are fetched the moment the pointer rests on a row
 * (Dock.warm), which is what makes the click feel like a switch rather than
 * a page load.
 */
"use strict";

(() => {
  if (typeof Dock === "undefined") return;
  const API = ["localhost", "127.0.0.1"].includes(location.hostname)
    ? "http://127.0.0.1:5174" : "";
  const esc = (s) => String(s).replace(/[&<>"]/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const ic = (n, c = "xs") => Icons.svg(n, c);

  /* Short names for the engine's features. The engine's own help text (from
   * /screen/features) is the hover title, so the long explanation is one
   * pointer-rest away without costing a column its width. */
  const F = {
    close: "Last", ret_1d: "1D %", ret_1w: "1W %", ret_1m: "1M %", ret_3m: "3M %",
    ret_6m: "6M %", ret_1y: "1Y %", dist_52w_high: "From 52w high %",
    dist_52w_low: "Above 52w low %", rsi14: "RSI 14", atr_pct: "ATR %",
    sma20_rel: "vs SMA 20 %", sma50_rel: "vs SMA 50 %", sma200_rel: "vs SMA 200 %",
    sma50_cross_ago: "SMA 50 cross, sessions ago", sma200_cross_ago: "SMA 200 cross, sessions ago",
    range_20d_pct: "20D range %", vol_z20: "Volume σ", vol_ratio20: "Volume × 20D avg",
    turnover_20d_cr: "Turnover ₹ cr", gap_pct: "Gap %", close_pos: "Close in day range %",
    hi20_break_pct: "vs 20D high %", lo20_break_pct: "vs 20D low %", streak: "Up/down streak",
    adx14: "ADX 14", macd_hist_pct: "MACD hist %", bb_pct_b: "Bollinger %B",
    bb_width_pct: "Bollinger width %", stoch_k: "Stochastic %K", supertrend_dir: "Supertrend",
  };
  let help = {};               // feature → the engine's own description

  const PRESETS = [
    { id: "gainers", label: "Top gainers", filters: [], sort: "ret_1d" },
    { id: "losers", label: "Top losers", filters: [["ret_1d", "lt", 0]], sort: "ret_1d" },
    { id: "oversold", label: "Oversold", filters: [["rsi14", "lt", 30]], sort: "rsi14" },
    { id: "overbought", label: "Overbought", filters: [["rsi14", "gt", 70]], sort: "rsi14" },
    { id: "high", label: "Near the 52-week high", filters: [["dist_52w_high", "gt", -3]], sort: "dist_52w_high" },
    { id: "trend", label: "Strong uptrend",
      filters: [["sma200_rel", "gt", 0], ["sma50_rel", "gt", 0], ["adx14", "gt", 25]], sort: "adx14" },
    { id: "breakout", label: "20-day breakout", filters: [["hi20_break_pct", "gt", 0]], sort: "vol_ratio20" },
    { id: "volume", label: "Volume surge", filters: [["vol_ratio20", "gt", 2]], sort: "vol_ratio20" },
    { id: "coiled", label: "Coiled — tight 20-day range", filters: [["range_20d_pct", "lt", 6]], sort: "range_20d_pct" },
    { id: "cross50", label: "Just crossed above SMA 50",
      filters: [["sma50_cross_ago", "lt", 5], ["sma50_rel", "gt", 0]], sort: "sma50_rel" },
  ];
  const presetOf = (id) => PRESETS.find((p) => p.id === id);

  /* Answers are cached for the session, per spec: the data is end-of-day, so
   * a list asked for twice in an afternoon is the same list, and reopening
   * the widget should show it at once rather than spin. */
  const CACHE_MS = 10 * 60_000;
  const cacheGet = (k) => {
    try {
      const v = JSON.parse(sessionStorage.getItem("charto:scr:" + k) || "null");
      return v && Date.now() - v.at < CACHE_MS ? v.d : null;
    } catch { return null; }
  };
  const cachePut = (k, d) => {
    try { sessionStorage.setItem("charto:scr:" + k, JSON.stringify({ at: Date.now(), d })); } catch { }
  };

  const fmt = (v, d = 2) => v == null || !Number.isFinite(v) ? "—"
    : (v < 0 ? "−" : "") + Math.abs(v).toLocaleString("en-IN", { minimumFractionDigits: d, maximumFractionDigits: d });
  const fmtPct = (v) => v == null ? "—" : (v > 0 ? "+" : v < 0 ? "−" : "") + Math.abs(v).toFixed(2) + "%";
  const dir = (v) => v > 0 ? "up" : v < 0 ? "down" : "";
  // short column heads for the extra columns a narrow tile can show
  const SHORT = { ret_1w: "1W", ret_1m: "1M", ret_3m: "3M", ret_1y: "1Y", rsi14: "RSI", adx14: "ADX",
                  vol_ratio20: "Vol×", atr_pct: "ATR%", dist_52w_high: "52WH", turnover_20d_cr: "₹ cr" };
  const isPct = (k) => /(_pct|^ret_|_rel$|dist_52w)/.test(k) && k !== "bb_pct_b";

  function mount(host, ctx) {
    const cfg = () => ctx.cfg;
    if (!cfg().filters) ctx.setCfg({ preset: "gainers", filters: [], sort: "ret_1d" });
    let state = { loading: false, error: "", res: null, feats: {}, colSort: null };
    let seq = 0;

    host.innerHTML =
      `<div class="side-head scr-head">` +
        `<button type="button" class="side-pick" data-s="preset" title="Choose a screen"></button>` +
        `<div class="spacer"></div>` +
        `<button type="button" class="side-act" data-s="refresh" title="Run again" aria-label="Run again">${ic("rotateCw")}</button>` +
      `</div>` +
      `<div class="scr-chips" role="list"></div>` +
      `<div class="side-body scr-body" aria-live="polite"></div>` +
      `<div class="scr-foot"></div>`;
    const $ = (s) => host.querySelector(s);

    const spec = () => ({
      filters: cfg().filters.map(([feature, op, value]) => ({ feature, op, value })),
      sort: cfg().sort, limit: cfg().rows || 50,
    });

    function title() {
      const p = presetOf(cfg().preset);
      return p ? p.label : "Custom screen";
    }

    function paintHead() {
      $(".side-pick").innerHTML = `${esc(title())}${ic("chevronDown", "")}`;
      ctx.setTitle(title());
      const chips = cfg().filters.map(([f, op, v], i) =>
        `<span class="scr-chip" role="listitem" title="${esc(help[f] || F[f] || f)}">` +
        `<b>${esc(F[f] || f)}</b> ${op === "lt" ? "&lt;" : "&gt;"} ${esc(v)}` +
        `<button type="button" data-s="drop" data-i="${i}" aria-label="Remove this filter">${ic("x")}</button></span>`);
      $(".scr-chips").innerHTML = chips.join("") +
        `<button type="button" class="scr-chip add" data-s="add">${ic("plus")}Filter</button>` +
        `<button type="button" class="scr-chip sort" data-s="sort" title="What the list is ranked by">` +
        `${ic("sort")}${esc(F[cfg().sort] || cfg().sort)}</button>`;
    }

    async function run(force) {
      const sp = spec(), key = JSON.stringify(sp);
      const my = ++seq;
      const hit = !force && cacheGet(key);
      if (hit) { state = { ...state, ...hit, loading: false, error: "" }; return paint(); }
      state.loading = true; state.error = "";
      paint();
      // a re-run that comes back identical would look like a dead button
      const spin = $('[data-s="refresh"]');
      if (spin) spin.classList.add("spinning");
      try {
        const r = await Net.get(`${API}/screen/run?spec=` + encodeURIComponent(key));
        const d = await r.json();
        if (!r.ok || d.error) throw new Error(d.error || `HTTP ${r.status}`);
        const syms = (d.rows || []).map((x) => x.symbol);
        let feats = {};
        if (syms.length) {
          const fr = await Net.get(`${API}/screen/features?symbols=` + encodeURIComponent(syms.join(",")));
          if (fr.ok) {
            const fd = await fr.json();
            feats = fd.features || {};
            if (fd.fields) help = fd.fields;
          }
        }
        if (my !== seq) return;
        state = { ...state, loading: false, res: d, feats };
        cachePut(key, { res: d, feats });
        if (force) ctx.toast(`Screen re-run · ${(d.rows || []).length} stocks`);
      } catch (e) {
        if (my !== seq) return;
        state.loading = false;
        state.error = e.message || String(e);
      }
      if (spin) spin.classList.remove("spinning");
      paint();
    }

    function rows() {
      const list = [...((state.res || {}).rows || [])];
      const cs = state.colSort;
      if (cs) {
        const val = (r) => cs.k === "symbol" ? r.symbol
          : r[cs.k] != null ? r[cs.k] : (state.feats[r.symbol] || {})[cs.k];
        list.sort((a, b) => {
          const x = val(a), y = val(b);
          if (x == null) return 1;
          if (y == null) return -1;
          return (typeof x === "string" ? x.localeCompare(y) : x - y) * cs.d;
        });
      }
      return list;
    }

    function paint() {
      paintHead();
      const body = $(".scr-body"), foot = $(".scr-foot");
      if (state.loading && !state.res) {
        body.innerHTML = `<div class="scr-skel">${"<i></i>".repeat(8)}</div>`;
        foot.innerHTML = "";
        return;
      }
      if (state.error) {
        body.innerHTML = `<div class="side-empty">${Icons.svg("funnel")}<p>The screen could not run: ${esc(state.error)}</p>` +
          `<button type="button" class="btn cta" data-s="refresh">Try again</button></div>`;
        foot.innerHTML = "";
        return;
      }
      const d = state.res;
      if (!d) { body.innerHTML = ""; return; }
      const list = rows();
      const k = cfg().sort;
      const cur = ctx.pageSymbol();
      const th = (key, label, cls = "r") => {
        const on = state.colSort && state.colSort.k === key;
        return `<button type="button" class="${cls}${on ? " on" : ""}" data-col="${key}">${esc(label)}` +
          (on ? ic(state.colSort.d > 0 ? "chevronUp" : "chevronDown") : "") + `</button>`;
      };
      const showMetric = k !== "close" && k !== "ret_1d";
      // the extra columns the settings switch on, minus the one already
      // shown as the ranking metric, and minus whatever does not fit
      let extra = (cfg().cols || []).filter((c) => c !== k && c !== "close" && c !== "ret_1d");
      const room = (host.clientWidth || 320) - 120 - 72 - 62 - (showMetric ? 70 : 0);
      extra = extra.slice(0, Math.max(0, Math.floor(room / 62)));
      body.classList.toggle("loading", state.loading);
      body.classList.toggle("scr-flat", cfg().logos === false);
      body.style.setProperty("--scr-cols", `minmax(0,1fr) 72px 62px${showMetric ? " 70px" : ""}${extra.map(() => " 62px").join("")}`);
      body.innerHTML = list.length
        ? `<div class="scr-th">${th("symbol", "Symbol", "l")}${th("close", "Last")}${th("ret_1d", "1D")}` +
          (showMetric ? th(k, F[k] || k) : "") + extra.map((c) => th(c, SHORT[c] || F[c] || c)).join("") + `</div>` +
          list.map((r) => {
            const f = state.feats[r.symbol] || {};
            const mark = cfg().logos === false ? "" : Universe.logoHTML(r.symbol) || `<span class="co-blank">${esc(r.symbol[0])}</span>`;
            const m = r[k];
            return `<div class="scr-row${r.symbol === cur ? " on" : ""}" data-sym="${esc(r.symbol)}" role="button" tabindex="0" ` +
              `aria-label="Open ${esc(r.symbol)} on the chart">` +
              `<span class="scr-name">${mark}<span class="tx"><b>${esc(r.symbol)}</b>` +
              (cfg().names !== false ? `<span>${esc(r.name || "")}</span>` : "") + `</span></span>` +
              `<span class="r num">${fmt(r.close)}</span>` +
              `<span class="r num ${dir(f.ret_1d)}">${fmtPct(f.ret_1d)}</span>` +
              (showMetric ? `<span class="r num">${isPct(k) ? fmt(m) : fmt(m, Math.abs(m) >= 100 ? 0 : 1)}</span>` : "") +
              extra.map((c) => { const v = f[c]; return `<span class="r num ${isPct(c) ? dir(v) : ""}">${v == null ? "—" : isPct(c) ? fmtPct(v) : fmt(v, Math.abs(v) >= 100 ? 0 : 1)}</span>`; }).join("") +
              `<span class="scr-acts">` +
                `<button type="button" data-s="watch" title="Add to watchlist" aria-label="Add ${esc(r.symbol)} to watchlist">${ic("star")}</button>` +
                `<button type="button" data-s="beside" title="Open in a new pane" aria-label="Open ${esc(r.symbol)} in a new pane">${ic("split")}</button>` +
              `</span></div>`;
          }).join("")
        : `<div class="side-empty">${Icons.svg("funnel")}<p>No instrument in the ${esc(d.universe)} matches these filters on ${esc(d.as_of)}.</p>` +
          `<button type="button" class="btn cta" data-s="add">Change the filters</button></div>`;
      foot.innerHTML =
        `<span title="End-of-day values over the ${d.universe} stocks the engine covers"><b>${d.matched}</b> of ${d.universe}` +
        `${d.matched > d.shown ? ` · top ${d.shown}` : ""} · ${esc(d.as_of)}</span>` +
        `<button type="button" class="scr-ask" data-s="ask">${ic("chat")}Ask in chat</button>`;
    }

    /* ── editing the screen ─────────────────────────────────────────────── */

    function setScreen(patch) {
      ctx.setCfg(patch);
      state.colSort = null;
      run();
    }

    function presetMenu(anchor) {
      ctx.menu(anchor, [
        { head: "Screens" },
        ...PRESETS.map((p) => ({ id: p.id, label: p.label, on: cfg().preset === p.id })),
        { sep: true },
        { id: "custom", label: "Start from nothing", icon: "plus" },
      ], (id) => {
        if (id === "custom") return setScreen({ preset: null, filters: [], sort: "ret_1d" });
        const p = presetOf(id);
        setScreen({ preset: p.id, filters: p.filters.map((f) => [...f]), sort: p.sort });
      });
    }

    function filterSheet(anchor) {
      const opts = Object.entries(F).filter(([k]) => k !== "close" || true)
        .map(([k, l]) => `<option value="${k}">${esc(l)}</option>`).join("");
      const p = ctx.menu(anchor,
        `<div class="head">Add a filter</div>` +
        `<form class="scr-form">` +
          `<select name="f" aria-label="Measure">${opts}</select>` +
          `<div class="scr-op"><div class="dk-seg"><button type="button" class="on" data-op="gt">Above</button>` +
          `<button type="button" data-op="lt">Below</button></div>` +
          `<input name="v" type="number" step="any" required placeholder="Value" aria-label="Value"></div>` +
          `<p class="scr-help"></p>` +
          `<button type="submit" class="btn cta">Add filter</button>` +
        `</form>`, null, "scr-sheet");
      if (!p) return;
      const form = p.querySelector("form"), help_ = p.querySelector(".scr-help");
      let op = "gt";
      const say = () => { help_.textContent = help[form.f.value] || ""; };
      form.f.value = "rsi14"; say();
      form.f.addEventListener("change", say);
      p.addEventListener("click", (e) => {
        const b = e.target.closest("[data-op]");
        if (!b) return;
        op = b.dataset.op;
        p.querySelectorAll("[data-op]").forEach((x) => x.classList.toggle("on", x === b));
      });
      form.addEventListener("keydown", (e) => e.stopPropagation());
      form.addEventListener("submit", (e) => {
        e.preventDefault();
        const v = Number(form.v.value);
        if (!Number.isFinite(v)) return;
        Dock.closeMenu();
        setScreen({ preset: null, filters: [...cfg().filters, [form.f.value, op, v]] });
      });
      setTimeout(() => form.v.focus(), 0);
    }

    function sortMenu(anchor) {
      const used = new Set(["ret_1d", "ret_1w", "ret_1m", "rsi14", "vol_ratio20", "turnover_20d_cr",
                            "dist_52w_high", "adx14", "atr_pct", ...cfg().filters.map((f) => f[0])]);
      ctx.menu(anchor, [{ head: "Rank by" },
        ...[...used].map((k) => ({ id: k, label: F[k] || k, on: cfg().sort === k }))],
        (k) => setScreen({ sort: k, preset: cfg().preset && presetOf(cfg().preset).sort === k ? cfg().preset : null }));
    }

    host.addEventListener("click", (e) => {
      const col = e.target.closest("[data-col]");
      if (col) {
        const k = col.dataset.col, cs = state.colSort;
        state.colSort = cs && cs.k === k ? (cs.d === -1 ? { k, d: 1 } : null) : { k, d: k === "symbol" ? 1 : -1 };
        return paint();
      }
      const b = e.target.closest("[data-s]");
      const row = e.target.closest(".scr-row");
      if (b) {
        const a = b.dataset.s;
        e.stopPropagation();
        if (a === "preset") return presetMenu(b);
        if (a === "refresh") return run(true);
        if (a === "add") return filterSheet(b);
        if (a === "sort") return sortMenu(b);
        if (a === "drop") {
          const f = [...cfg().filters]; f.splice(Number(b.dataset.i), 1);
          return setScreen({ preset: null, filters: f });
        }
        if (a === "ask") return ctx.compose(askText());
        if (a === "watch" && row) {
          if (typeof Panels !== "undefined" && Panels.watch) Panels.watch(row.dataset.sym);
          return ctx.toast(`${row.dataset.sym} added to your watchlist`);
        }
        if (a === "beside" && row) return ctx.openSymbol(row.dataset.sym, "beside");
      }
      if (row) openRow(row.dataset.sym);
    });
    host.addEventListener("keydown", (e) => {
      if (e.key !== "Enter" && e.key !== " ") return;
      const row = e.target.closest(".scr-row");
      if (!row || e.target !== row) return;
      e.preventDefault();
      openRow(row.dataset.sym);
    });
    host.addEventListener("pointerover", (e) => {
      const row = e.target.closest(".scr-row");
      if (row) ctx.warm(row.dataset.sym);
    });

    /** A row chosen: its link group's symbol, a new pane, or the chart. */
    function openRow(sym) {
      if (cfg().click === "pane" && !/^[1-4]$/.test(ctx.link())) return ctx.openSymbol(sym, "beside");
      ctx.pick(sym);
    }

    function askText() {
      const d = state.res;
      if (!d || !d.rows || !d.rows.length) return "";
      const top = d.rows.slice(0, 10).map((r) => r.symbol).join(", ");
      return `My screen "${title()}" (${d.criteria}; ${d.ranking}) matched ${d.matched} of ${d.universe} ` +
        `as of ${d.as_of}. The top names are ${top}. Which of these look strongest on the chart, and why?`;
    }

    // a resize that changes how many extra columns fit redraws the table
    let fitW = 0;
    new ResizeObserver(() => {
      const w = Math.floor((host.clientWidth || 0) / 62);
      if (w !== fitW) { fitW = w; if (state.res && (cfg().cols || []).length) paint(); }
    }).observe(host);

    let ran = false;
    return {
      show() { if (!ran) { ran = true; run(); } else paint(); },
      config(c, patch) { if ("rows" in patch) run(); else paint(); },

      ask: askText,
    };
  }

  Dock.register({
    type: "screener", title: "Screener", icon: "funnel", shortcut: "screener",
    key: "Alt Shift S", desc: "Filter 500 stocks on price and volume",
    zone: "left", minW: 290, hue: "teal", group: "Market", anim: "pulse", mount,
    linkable: true,
    settings: [
      { section: "Results" },
      { key: "rows", label: "Rows", def: 50, options: [{ v: 25, label: "25" }, { v: 50, label: "50" }], hint: "The engine returns at most 50" },
      { key: "cols", label: "Extra columns", kind: "chips", def: [], hint: "A narrow tile shows the first that fit",
        options: [["ret_1w", "1W"], ["ret_1m", "1M"], ["ret_3m", "3M"], ["ret_1y", "1Y"], ["rsi14", "RSI 14"], ["adx14", "ADX 14"],
                  ["vol_ratio20", "Volume ratio"], ["atr_pct", "ATR %"], ["dist_52w_high", "From 52W high"], ["turnover_20d_cr", "Turnover ₹ cr"]]
          .map(([v, label]) => ({ v, label })) },
      { section: "Rows" },
      { key: "names", label: "Company names", kind: "toggle", def: true },
      { key: "logos", label: "Logos", kind: "toggle", def: true },
      { key: "click", label: "Clicking a row opens it", def: "chart", hint: "In a link group, the group's widgets follow instead",
        options: [{ v: "chart", label: "On the chart" }, { v: "pane", label: "In a new pane" }] },
      { kind: "note", label: "Screens run on end-of-day values, so there is nothing to refresh during the day." },
    ],
  });
})();
