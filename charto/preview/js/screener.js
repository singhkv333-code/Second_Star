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
  const API = location.port === "5173"
    ? "http://127.0.0.1:5174" : "";
  const esc = (s) => String(s).replace(/[&<>"]/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const ic = (n, c = "xs") => Icons.svg(n, c);

  /* Short names for the engine's features. The engine's own help text (from
   * /screen/features) is the hover title, so the long explanation is one
   * pointer-rest away without costing a column its width. */
  const F = {
    close: "Last", ret_1d: "1D %", ret_1w: "1W %", ret_1m: "1M %", ret_3m: "3M %",
    ret_6m: "6M %", ret_1y: "1Y %", dist_52w_high: "From 52W high %",
    dist_52w_low: "Above 52W low %", rsi14: "RSI 14", atr_pct: "ATR %",
    sma20_rel: "vs SMA 20 %", sma50_rel: "vs SMA 50 %", sma200_rel: "vs SMA 200 %",
    sma50_cross_ago: "SMA 50 cross, sessions ago", sma200_cross_ago: "SMA 200 cross, sessions ago",
    range_20d_pct: "20D range %", vol_z20: "Volume z-score", vol_ratio20: "Volume × 20D avg",
    turnover_20d_cr: "Turnover ₹ cr", gap_pct: "Gap %", close_pos: "Close in day range %",
    hi20_break_pct: "vs 20D high %", lo20_break_pct: "vs 20D low %", streak: "Close streak",
    adx14: "ADX 14", macd_hist_pct: "MACD hist %", bb_pct_b: "Bollinger %B",
    bb_width_pct: "Bollinger width %", stoch_k: "Stochastic %K", supertrend_dir: "Supertrend",
    ret_open: "Close vs open %", turnover_20d_musd: "Turnover $ m",
    vp20_pos: "In value area %", vp20_va_width_pct: "Value area width %",
    vp20_poc_dist_pct: "From POC %", vp20_poc_shift_pct: "POC shift %",
    orb15_pos: "15-min opening range %", orb30_pos: "30-min opening range %",
  };
  /* The same measures spelled out, for the lists that have the room: the
   * filter editor's measure picker and the sort menu's "Rank by". A chip or a
   * column head keeps the short name above. */
  const LONG = {
    close: "Last price", ret_1d: "1-day change", ret_1w: "1-week change", ret_1m: "1-month change",
    ret_3m: "3-month change", ret_6m: "6-month change", ret_1y: "1-year change",
    dist_52w_high: "Distance from 52-week high", dist_52w_low: "Distance above 52-week low",
    rsi14: "RSI (14)", atr_pct: "ATR (% of price)", sma20_rel: "Distance from 20-day SMA",
    sma50_rel: "Distance from 50-day SMA", sma200_rel: "Distance from 200-day SMA",
    sma50_cross_ago: "Sessions since 50-day SMA cross", sma200_cross_ago: "Sessions since 200-day SMA cross",
    range_20d_pct: "20-day range (%)", vol_z20: "Volume z-score (20-day)", vol_ratio20: "Volume vs 20-day average",
    turnover_20d_cr: "Turnover, 20-day average (₹ cr)", turnover_20d_musd: "Turnover, 20-day average ($ m)",
    gap_pct: "Opening gap", ret_open: "Close vs open", close_pos: "Close within day's range",
    hi20_break_pct: "Distance from 20-day high", lo20_break_pct: "Distance from 20-day low",
    streak: "Consecutive closes up or down", adx14: "ADX (14)", macd_hist_pct: "MACD histogram (% of price)",
    bb_pct_b: "Bollinger %B", bb_width_pct: "Bollinger Band width", stoch_k: "Stochastic %K",
    supertrend_dir: "Supertrend direction", vp20_pos: "Position in value area",
    vp20_va_width_pct: "Value area width", vp20_poc_dist_pct: "Distance from point of control",
    vp20_poc_shift_pct: "Point of control shift", orb15_pos: "15-minute opening range",
    orb30_pos: "30-minute opening range",
  };
  const longOf = (k) => LONG[k] || F[k] || k;
  /* The measures as a trader groups them, for the filter editor's list. */
  const GROUPS = [
    ["Price and returns", ["close", "ret_1d", "ret_1w", "ret_1m", "ret_3m", "ret_6m", "ret_1y", "gap_pct", "ret_open", "close_pos", "streak"]],
    ["Highs, lows and breakouts", ["dist_52w_high", "dist_52w_low", "hi20_break_pct", "lo20_break_pct", "range_20d_pct", "orb15_pos", "orb30_pos"]],
    ["Trend", ["sma20_rel", "sma50_rel", "sma200_rel", "sma50_cross_ago", "sma200_cross_ago", "adx14", "supertrend_dir"]],
    ["Momentum", ["rsi14", "macd_hist_pct", "stoch_k", "bb_pct_b"]],
    ["Volatility", ["atr_pct", "bb_width_pct"]],
    ["Volume and liquidity", ["vol_ratio20", "vol_z20", "turnover_20d_cr", "turnover_20d_musd"]],
    ["Volume profile", ["vp20_pos", "vp20_va_width_pct", "vp20_poc_dist_pct", "vp20_poc_shift_pct"]],
  ];
  const unitOf = (k) => k === "vol_ratio20" ? "×" : k === "vol_z20" ? "σ" : k === "turnover_20d_cr" ? "₹ cr"
    : k === "turnover_20d_musd" ? "$ m" : /cross_ago$/.test(k) ? "sessions" : k === "streak" ? "closes"
    : k === "close" ? "₹" : /^(rsi14|adx14|stoch_k|supertrend_dir)$/.test(k) ? "" : "%";
  let help = {};               // feature → the engine's own description

  /* The ready-made screens, grouped the way the menu shows them. Ids are
   * stored in each widget's settings, so an existing id keeps its meaning
   * even when its label changes. Thresholds were set against the live
   * universe's distribution (Oct 2026) so each screen returns a short list
   * on an ordinary day rather than nothing or half the market. The hint is
   * the rule itself, so a reader knows what a name means before choosing it. */
  const PRESETS = [
    { group: "Movers" },
    { id: "gainers", label: "Top gainers", hint: "1D change", filters: [], sort: "ret_1d" },
    { id: "losers", label: "Top losers", hint: "1D change", filters: [["ret_1d", "lt", 0]], sort: "ret_1d", order: "asc" },
    { id: "active", label: "Most active", hint: "Turnover", filters: [], sort: "turnover_20d_cr" },
    { id: "volume", label: "Unusual volume", hint: "Vol > 2× avg", filters: [["vol_ratio20", "gt", 2]], sort: "vol_ratio20" },
    { id: "gapup", label: "Gap up", hint: "Gap > 1%", filters: [["gap_pct", "gt", 1]], sort: "gap_pct" },
    { id: "gapdown", label: "Gap down", hint: "Gap < −1%", filters: [["gap_pct", "lt", -1]], sort: "gap_pct" },
    { group: "Momentum" },
    { id: "leaders", label: "1-month leaders", hint: "1M > 10%", filters: [["ret_1m", "gt", 10]], sort: "ret_1m" },
    { id: "upstreak", label: "Winning streak", hint: "4+ higher closes", filters: [["streak", "gt", 3]], sort: "streak" },
    { id: "downstreak", label: "Losing streak", hint: "4+ lower closes", filters: [["streak", "lt", -3]], sort: "streak" },
    { id: "oversold", label: "RSI oversold", hint: "RSI < 30", filters: [["rsi14", "lt", 30]], sort: "rsi14" },
    { id: "overbought", label: "RSI overbought", hint: "RSI > 70", filters: [["rsi14", "gt", 70]], sort: "rsi14" },
    { group: "Trend" },
    { id: "trend", label: "Strong uptrend", hint: "> SMA 50, 200 · ADX > 25",
      filters: [["sma200_rel", "gt", 0], ["sma50_rel", "gt", 0], ["adx14", "gt", 25]], sort: "adx14" },
    { id: "downtrend", label: "Strong downtrend", hint: "< SMA 50, 200 · ADX > 25",
      filters: [["sma200_rel", "lt", 0], ["sma50_rel", "lt", 0], ["adx14", "gt", 25]], sort: "adx14" },
    { id: "pullback", label: "Pullback in an uptrend", hint: "> SMA 200 · RSI < 40",
      filters: [["sma200_rel", "gt", 0], ["rsi14", "lt", 40]], sort: "rsi14" },
    { id: "cross50", label: "Crossed above 50-day SMA", hint: "Last 5 sessions",
      filters: [["sma50_cross_ago", "lt", 5], ["sma50_rel", "gt", 0]], sort: "sma50_rel" },
    { id: "cross200", label: "Crossed above 200-day SMA", hint: "Last 10 sessions",
      filters: [["sma200_cross_ago", "lt", 10], ["sma200_rel", "gt", 0]], sort: "sma200_rel" },
    { group: "Highs, lows and breakouts" },
    { id: "high", label: "Near 52-week high", hint: "Within 3%", filters: [["dist_52w_high", "gt", -3]], sort: "dist_52w_high" },
    { id: "low", label: "Near 52-week low", hint: "Within 5%", filters: [["dist_52w_low", "lt", 5]], sort: "dist_52w_low" },
    { id: "breakout", label: "20-day breakout", hint: "Close > 20D high", filters: [["hi20_break_pct", "gt", 0]], sort: "vol_ratio20" },
    { id: "breakdown", label: "20-day breakdown", hint: "Close < 20D low", filters: [["lo20_break_pct", "lt", 0]], sort: "lo20_break_pct" },
    { id: "orb", label: "Opening range breakout", hint: "Above first 15 min", filters: [["orb15_pos", "gt", 100]], sort: "orb15_pos" },
    { group: "Volatility" },
    { id: "coiled", label: "Tight 20-day range", hint: "Range < 6%", filters: [["range_20d_pct", "lt", 6]], sort: "range_20d_pct" },
    { id: "squeeze", label: "Bollinger squeeze", hint: "Width < 5%", filters: [["bb_width_pct", "lt", 5]], sort: "bb_width_pct" },
    { id: "volatile", label: "High volatility", hint: "ATR > 4%", filters: [["atr_pct", "gt", 4]], sort: "atr_pct" },
  ];
  const presetOf = (id) => PRESETS.find((p) => p.id && p.id === id);

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
      ...(cfg().order ? { order: cfg().order } : {}),
    });

    /* Every stock's values, for the editor's distribution and its live
     * count. The engine's own matrix (/screen/features), fetched when the
     * editor first opens and kept ten minutes: end-of-day data. The engine
     * still decides the list — this only previews how many would pass. */
    let all = null, allAt = 0, allP = null;
    function universe() {
      if (all && Date.now() - allAt < CACHE_MS) return Promise.resolve(all);
      if (allP) return allP;
      allP = Net.get(`${API}/screen/features`).then((r) => r.json()).then((d) => {
        all = d.features || {}; allAt = Date.now();
        if (d.fields) help = d.fields;
        return all;
      }).catch(() => null).finally(() => { allP = null; });
      return allP;
    }
    const passes = (v, op, x) => v != null && (op === "gt" ? v > x : v < x);

    /** The filters, one entry per measure: a measure with both an above and
     *  a below is a "between". The engine only knows gt/lt; this is how the
     *  widget shows and edits them. */
    function grouped() {
      const by = new Map();
      for (const [f, op, v] of cfg().filters) {
        const g = by.get(f) || { f };
        g[op] = v;
        by.set(f, g);
      }
      return [...by.values()];
    }
    const nice = (v) => Math.abs(v) >= 100 ? Math.round(v) : Math.abs(v) >= 10 ? Math.round(v * 10) / 10 : Math.round(v * 100) / 100;
    function chipText(g) {
      const u = unitOf(g.f), n = (v) => `${fmt(v, Math.abs(v) >= 100 || Number.isInteger(v) ? 0 : Math.abs(v) >= 10 ? 1 : 2)}${u && u !== "₹" ? (u.length > 1 ? " " + u : u) : ""}`;
      if (g.f === "supertrend_dir") return `<b>Supertrend</b> ${g.gt != null ? "up" : "down"}`;
      if (g.gt != null && g.lt != null) return `<b>${esc(F[g.f] || g.f)}</b> ${n(g.gt)} – ${n(g.lt)}`;
      return `<b>${esc(F[g.f] || g.f)}</b> ${g.gt != null ? "&gt;" : "&lt;"} ${n(g.gt != null ? g.gt : g.lt)}`;
    }

    function title() {
      const p = presetOf(cfg().preset);
      return p ? p.label : "Custom screen";
    }

    function paintHead() {
      $(".side-pick").innerHTML = `${esc(title())}${ic("chevronDown", "")}`;
      ctx.setTitle(title());
      const chips = grouped().map((g) =>
        `<span class="scr-chip" role="listitem">` +
        `<button type="button" class="scr-edit" data-s="edit" data-f="${esc(g.f)}" title="Edit · ${esc(help[g.f] || F[g.f] || g.f)}">${chipText(g)}</button>` +
        `<button type="button" data-s="drop" data-f="${esc(g.f)}" aria-label="Remove this filter">${ic("x")}</button></span>`);
      const served = state.res && state.res.sorted_by && state.res.sorted_by.feature === cfg().sort ? state.res.sorted_by.order : null;
      const ord = cfg().order || served || "desc";
      $(".scr-chips").innerHTML = chips.join("") +
        `<button type="button" class="scr-chip add" data-s="add">${ic("plus")}Filter</button>` +
        `<button type="button" class="scr-chip sort" data-s="sort" title="Ranked by ${esc(F[cfg().sort] || cfg().sort)}, ${ord === "asc" ? "lowest" : "highest"} first">` +
        `${ic(ord === "asc" ? "arrowUp" : "arrowDown")}${esc(F[cfg().sort] || cfg().sort)}</button>`;
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
        ...PRESETS.flatMap((p, i) => p.group
          ? [...(i ? [{ sep: true }] : []), { head: p.group }]
          : [{ id: p.id, label: p.label, hint: cfg().preset === p.id ? null : p.hint, on: cfg().preset === p.id }]),
        { sep: true },
        { id: "custom", label: "New custom screen", icon: "plus" },
      ], (id) => {
        if (id === "custom") return setScreen({ preset: null, filters: [], sort: "ret_1d", order: null });
        const p = presetOf(id);
        setScreen({ preset: p.id, filters: p.filters.map((f) => [...f]), sort: p.sort, order: p.order || null });
      });
    }

    /* The filter editor: a measure, a condition (above, between, below),
     * the value, and — before anything is applied — where that value falls
     * across the universe and how many stocks it would leave. */
    function filterSheet(anchor, editing) {
      const cur = editing ? grouped().find((g) => g.f === editing) : null;
      const opts = GROUPS.map(([g, ks]) => `<optgroup label="${esc(g)}">` +
        ks.filter((k) => F[k]).map((k) => `<option value="${k}">${esc(longOf(k))}</option>`).join("") + `</optgroup>`).join("");
      const p = ctx.menu(anchor,
        `<div class="head">${cur ? "Edit filter" : "Add a filter"}</div>` +
        `<form class="scr-form" novalidate>` +
          `<select name="f" aria-label="Measure">${opts}</select>` +
          `<p class="scr-help"></p>` +
          `<div class="dk-seg scr-cond" role="radiogroup" aria-label="Condition"></div>` +
          `<div class="scr-vals">` +
            `<label class="scr-val"><input name="a" type="number" step="any" inputmode="decimal" aria-label="Value"><span class="u"></span></label>` +
            `<span class="scr-and">and</span>` +
            `<label class="scr-val"><input name="b" type="number" step="any" inputmode="decimal" aria-label="Upper value"><span class="u"></span></label>` +
          `</div>` +
          `<div class="scr-dist" aria-hidden="true"><svg class="scr-hist" viewBox="0 0 240 44" preserveAspectRatio="none"></svg>` +
            `<div class="scr-scale"><span></span><span></span><span></span></div></div>` +
          `<p class="scr-count" aria-live="polite"></p>` +
          `<div class="scr-btns">` +
            (cur ? `<button type="button" class="btn outline" data-x="remove">Remove</button>` : "") +
            `<span class="spacer"></span><button type="submit" class="btn cta">${cur ? "Apply" : "Add filter"}</button>` +
          `</div>` +
        `</form>`, null, "scr-sheet");
      if (!p) return;
      const form = p.querySelector("form"), $$ = (q) => p.querySelector(q);
      let op = cur ? (cur.gt != null && cur.lt != null ? "between" : cur.gt != null ? "gt" : "lt") : "gt";
      let vals = null;              // the chosen measure's values, sorted

      const segs = () => form.f.value === "supertrend_dir"
        ? [["gt", "Up"], ["lt", "Down"]] : [["gt", "Above"], ["between", "Between"], ["lt", "Below"]];
      function paintSeg() {
        if (form.f.value === "supertrend_dir" && op === "between") op = "gt";
        $$(".scr-cond").innerHTML = segs().map(([k, l]) =>
          `<button type="button" role="radio" aria-checked="${op === k}" class="${op === k ? "on" : ""}" data-op="${k}">${l}</button>`).join("");
        const st = form.f.value === "supertrend_dir";
        $$(".scr-vals").hidden = st;
        $$(".scr-vals").classList.toggle("two", op === "between");
        p.querySelectorAll(".scr-val .u").forEach((u) => { u.textContent = unitOf(form.f.value); });
      }
      /** [lo, hi] the condition keeps, from the inputs. */
      function range() {
        const a = form.a.value === "" ? NaN : Number(form.a.value), b = form.b.value === "" ? NaN : Number(form.b.value);
        if (form.f.value === "supertrend_dir") return op === "gt" ? [0, Infinity] : [-Infinity, 0];
        if (op === "gt") return [a, Infinity];
        if (op === "lt") return [-Infinity, a];
        return [Math.min(a, b), Math.max(a, b)];
      }
      const valid = () => { const [lo, hi] = range(); return !Number.isNaN(lo) && !Number.isNaN(hi) && lo < hi; };
      const keeps = (v, lo, hi) => v != null && v > lo && v < hi;

      function paintDist() {
        const f = form.f.value, svg = $$(".scr-hist"), sc = p.querySelectorAll(".scr-scale span"), cnt = $$(".scr-count");
        if (!all) { svg.innerHTML = ""; sc.forEach((x) => { x.textContent = ""; }); cnt.textContent = "Loading the universe…"; return; }
        vals = Object.values(all).map((r) => r[f]).filter((v) => v != null && Number.isFinite(v)).sort((x, y) => x - y);
        if (!vals.length) { svg.innerHTML = ""; cnt.textContent = "No stock carries this measure yet."; return; }
        const q = (t) => vals[Math.min(vals.length - 1, Math.max(0, Math.round(t * (vals.length - 1))))];
        // the outer 1% would stretch the axis into one tall bar; they are
        // still counted, just drawn in the end bins
        const lo0 = q(0.01), hi0 = q(0.99) > lo0 ? q(0.99) : vals[vals.length - 1] + 1, N = 30, w = (hi0 - lo0) / N;
        const bins = new Array(N).fill(0);
        for (const v of vals) bins[Math.max(0, Math.min(N - 1, Math.floor((v - lo0) / w)))]++;
        const top = Math.max(...bins), [lo, hi] = range(), ok = valid();
        svg.innerHTML = bins.map((n, i) => {
          const x0 = lo0 + i * w, mid = x0 + w / 2, h = n ? Math.max(2, (n / top) * 42) : 0;
          return `<rect x="${i * 8 + 0.5}" y="${44 - h}" width="7" height="${h}" rx="1.5" class="${ok && keeps(mid, lo, hi) ? "in" : ""}" data-v="${nice(x0)}"></rect>`;
        }).join("");
        const u = unitOf(f), lab = (v) => `${fmt(v, Math.abs(v) >= 100 || Number.isInteger(v) ? 0 : 1)}${u && u.length === 1 && u !== "₹" ? u : ""}`;
        sc[0].textContent = lab(vals[0]); sc[1].textContent = `median ${lab(q(0.5))}`; sc[2].textContent = lab(vals[vals.length - 1]);
        if (!ok) { cnt.textContent = op === "between" ? "Enter both ends of the range." : "Enter a value."; return; }
        const others = grouped().filter((g) => g.f !== f);
        let alone = 0, both = 0;
        for (const r of Object.values(all)) {
          if (!keeps(r[f], lo, hi)) continue;
          alone++;
          if (others.every((g) => (g.gt == null || passes(r[g.f], "gt", g.gt)) && (g.lt == null || passes(r[g.f], "lt", g.lt)))) both++;
        }
        const n = Object.keys(all).length;
        cnt.innerHTML = `<b>${alone}</b> of ${n} pass` + (others.length ? ` · <b>${both}</b> with your other filters` : "");
      }
      function seed(f) {
        form.a.value = form.b.value = "";
        if (!all) return;
        const v = Object.values(all).map((r) => r[f]).filter((x) => x != null).sort((x, y) => x - y);
        if (!v.length) return;
        const at = (t) => nice(v[Math.round(t * (v.length - 1))]);
        if (op === "between") { form.a.value = at(0.25); form.b.value = at(0.75); }
        else form.a.value = at(op === "gt" ? 0.75 : 0.25);
      }
      const say = () => { $$(".scr-help").textContent = (help[form.f.value] || "").replace(/\s*—\s*/g, " — "); };

      form.f.value = cur ? cur.f : "rsi14";
      if (cur) {
        if (op === "between") { form.a.value = cur.gt; form.b.value = cur.lt; }
        else form.a.value = cur.gt != null ? cur.gt : cur.lt;
      }
      paintSeg(); say(); paintDist();
      universe().then(() => { if (!p.isConnected) return; if (!cur && form.a.value === "") seed(form.f.value); say(); paintDist(); });

      form.f.addEventListener("change", () => { paintSeg(); say(); seed(form.f.value); paintDist(); });
      form.addEventListener("input", paintDist);
      p.addEventListener("click", (e) => {
        const b = e.target.closest("[data-op]");
        if (b) {
          const was = op; op = b.dataset.op;
          if (op === "between" && was !== "between" && form.b.value === "") {
            // keep the value typed as the lower end and offer the top quartile
            const v = Number(form.a.value);
            if (vals && vals.length) form.b.value = nice(Math.max(v + 1e-9, vals[Math.round(0.9 * (vals.length - 1))]));
          }
          paintSeg(); paintDist();
          return;
        }
        const bar = e.target.closest("rect[data-v]");
        if (bar && op !== "between" && form.f.value !== "supertrend_dir") { form.a.value = bar.dataset.v; paintDist(); return; }
        if (e.target.closest('[data-x="remove"]')) {
          Dock.closeMenu();
          setScreen({ preset: null, filters: cfg().filters.filter((x) => x[0] !== cur.f) });
        }
      });
      form.addEventListener("keydown", (e) => { if (e.key === "Escape") { Dock.closeMenu(); return; } e.stopPropagation(); });
      form.addEventListener("submit", (e) => {
        e.preventDefault();
        if (!valid()) { paintDist(); return; }
        const f = form.f.value, [lo, hi] = range();
        const keep = cfg().filters.filter((x) => x[0] !== f && (!cur || x[0] !== cur.f));
        const add = f === "supertrend_dir" ? [[f, op, 0]]
          : op === "between" ? [[f, "gt", lo], [f, "lt", hi]] : [[f, op, op === "gt" ? lo : hi]];
        Dock.closeMenu();
        setScreen({ preset: null, filters: [...keep, ...add] });
      });
      setTimeout(() => { if (form.f.value !== "supertrend_dir") form.a.focus(); }, 0);
    }

    function sortMenu(anchor) {
      const used = ["ret_1d", "ret_1w", "ret_1m", "ret_3m", "ret_1y", "rsi14", "adx14", "vol_ratio20", "turnover_20d_cr",
                    "dist_52w_high", "atr_pct", "range_20d_pct", ...cfg().filters.map((f) => f[0])];
      const served = state.res && state.res.sorted_by ? state.res.sorted_by.order : "desc";
      const ord = cfg().order || served;
      ctx.menu(anchor, [{ head: "Order" },
        { id: "ord:desc", label: "Highest first", icon: "arrowDown", on: ord === "desc" },
        { id: "ord:asc", label: "Lowest first", icon: "arrowUp", on: ord === "asc" },
        { sep: true }, { head: "Rank by" },
        ...[...new Set(used)].map((k) => ({ id: k, label: longOf(k), on: cfg().sort === k }))],
        (k) => {
          if (k.startsWith("ord:")) return setScreen({ order: k.slice(4), preset: null });
          setScreen({ sort: k, order: null, preset: cfg().preset && presetOf(cfg().preset).sort === k ? cfg().preset : null });
        });
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
        if (a === "edit") return filterSheet(b, b.dataset.f);
        if (a === "sort") return sortMenu(b);
        if (a === "drop") return setScreen({ preset: null, filters: cfg().filters.filter((x) => x[0] !== b.dataset.f) });
        if (a === "ask") return ctx.ask(askText());
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
      if (!d || !d.rows || !d.rows.length) return null;
      const k = cfg().sort;
      const top = d.rows.slice(0, 15).map((r, i) => {
        const f = state.feats[r.symbol] || {};
        return `${i + 1}. ${r.symbol}${r.name ? ` (${r.name})` : ""} — last ${fmt(r.close)}, 1D ${fmtPct(f.ret_1d)}` +
          (k !== "close" && k !== "ret_1d" && r[k] != null ? `, ${F[k] || k} ${fmt(r[k])}` : "");
      });
      return {
        sub: `${title()} · ${d.matched} of ${d.universe} · ${d.as_of}`,
        context: `Screen "${title()}": ${d.criteria}. ${d.ranking}. ${d.matched} of ${d.universe} stocks matched, ` +
          `end-of-day values as of ${d.as_of}. Top ${top.length}:\n${top.join("\n")}`,
        question: "Which of these look strongest on the chart, and why?",
      };
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
