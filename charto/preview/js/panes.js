/* Charto preview — chart LAYOUTS (TradingView's 1 / 2-column / 2-row grid).
 *
 * What this is and, more importantly, what it is NOT.
 *
 * The primary chart in main.js keeps everything: drawings, the scene layer
 * that chat annotates, indicator panes, the click-a-candle pin, the chat's
 * view of "the visible chart". This module adds SECONDARY charts beside it —
 * each an independent lightweight-charts instance with its own interval, for
 * the thing a second chart is actually for: seeing the same symbol on another
 * timeframe without giving up the one you are working on.
 *
 * Secondary charts are deliberately plain: candles, the volume study, pan
 * and zoom.
 * They are not annotated and chat does not read them. Making every pane a
 * fully-annotated peer means the scene layer needs a pane identity in its
 * addressing, chat needs to know which chart "this chart" means, and the
 * drawing layer needs per-chart hit-testing — real work in the backend
 * contract, not something to fake in the frontend. Until that exists, one
 * chart is the subject of the conversation and the rest are reference.
 *
 * Nothing in main.js's chart is touched: the primary keeps its own element
 * and its own instance, and the grid only changes the box it lives in.
 */
"use strict";

const Panes = (() => {
  const LWC = window.LightweightCharts;
  // same-origin behind a proxy, explicit port in local dev (see main.js)
  const API = ["localhost", "127.0.0.1"].includes(location.hostname)
    ? "http://127.0.0.1:5174" : "";
  // The page's instrument is the DEFAULT a new pane opens on, not a constant:
  // this file used to hard-code RELIANCE, so a split on any other company drew
  // — and labelled — Reliance beside it. A pane can now be pointed at its own
  // instrument from its legend, and everything about it (clock, currency,
  // venue, indicator series) follows that symbol rather than the page's.
  const INTERVALS = ["1m", "5m", "15m", "30m", "1h", "D", "W", "M"];
  // set by main.js — see onSettings() at the bottom of this file
  let onSettings = null;
  // the server's own vocabulary; the header's D/W/M are display labels
  const WIRE = { D: "1d", W: "1w", M: "1mo" };
  const DISP = { "1d": "D", "1w": "W", "1mo": "M" };
  const PAGE = { "1m": 3000, "5m": 2500, "15m": 2000, "30m": 2000, "1h": 2000, D: 2000, W: 700, M: 200 };
  // seconds per bar, keyed by BOTH the display label and the wire id — the
  // drawing runtime asks for it (clone offset), and a pane speaks D/W/M while
  // the server speaks 1d/1w/1mo.
  const IV_SEC = {
    "1m": 60, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600,
    D: 86400, W: 604800, M: 2592000,
    "1d": 86400, "1w": 604800, "1mo": 2592000,
  };

  /* ── the layout catalogue ────────────────────────────────────────────────
   *
   * A layout is ONE thing: a grid of area letters, row by row. Everything
   * else is derived from it —
   *
   *   grid-template-areas   join the rows with spaces between the letters
   *   pane count            how many distinct letters it uses
   *   the menu glyph        Icons.layoutSvg() walks the same grid and draws
   *                         a divider wherever two neighbouring cells differ
   *
   * so a layout and its icon cannot drift apart: there is no second place to
   * update. Adding one is a single line here and it appears in the picker,
   * correctly drawn, in the right pane-count group.
   *
   * Every letter must cover a RECTANGLE — that is CSS grid's rule for named
   * areas, not ours. `validate()` below fails loudly rather than letting the
   * browser silently drop a malformed template.
   *
   * The ids are terse on purpose (they are persisted in Store): c3 = three
   * columns, r4 = four rows, l1r3 = one left + three right, t3b1 = three top
   * + one bottom, g23 = a 2×3 grid.
   */
  const SPECS = [
    // 1
    ["s1", "Single chart", ["a"]],
    // 2
    ["c2", "Two columns", ["ab"]],
    ["r2", "Two rows", ["a", "b"]],
    // 3
    ["c3", "Three columns", ["abc"]],
    ["r3", "Three rows", ["a", "b", "c"]],
    ["l1r2", "Left · two right", ["ab", "ac"]],
    ["l2r1", "Two left · right", ["ac", "bc"]],
    ["t2b1", "Two top · bottom", ["ab", "cc"]],
    ["t1b2", "Top · two bottom", ["aa", "bc"]],
    // 4
    ["g22", "2 × 2", ["ab", "cd"]],
    ["c4", "Four columns", ["abcd"]],
    ["r4", "Four rows", ["a", "b", "c", "d"]],
    ["l1r3", "Left · three right", ["ab", "ac", "ad"]],
    ["l3r1", "Three left · right", ["ad", "bd", "cd"]],
    ["t1b3", "Top · three bottom", ["aaa", "bcd"]],
    ["t3b1", "Three top · bottom", ["abc", "ddd"]],
    ["c3l2", "Three columns · split left", ["abc", "dbc"]],
    ["c3m2", "Three columns · split middle", ["abc", "adc"]],
    ["c3r2", "Three columns · split right", ["abc", "abd"]],
    // 5
    ["c5", "Five columns", ["abcde"]],
    ["r5", "Five rows", ["a", "b", "c", "d", "e"]],
    ["l1r4", "Left · four right", ["ab", "ac", "ad", "ae"]],
    ["l4r1", "Four left · right", ["ae", "be", "ce", "de"]],
    ["t1b4", "Top · four bottom", ["aaaa", "bcde"]],
    ["t4b1", "Four top · bottom", ["abcd", "eeee"]],
    ["t2b3", "Two top · three bottom", ["aaabbb", "ccddee"]],
    ["t3b2", "Three top · two bottom", ["aabbcc", "dddeee"]],
    ["l1g22", "Left · 2 × 2", ["abc", "ade"]],
    ["g22r1", "2 × 2 · right", ["abe", "cde"]],
    // 6
    ["g23", "2 × 3", ["abc", "def"]],
    ["g32", "3 × 2", ["ab", "cd", "ef"]],
    ["c6", "Six columns", ["abcdef"]],
    ["r6", "Six rows", ["a", "b", "c", "d", "e", "f"]],
    ["l1r5", "Left · five right", ["ab", "ac", "ad", "ae", "af"]],
    ["t1b5", "Top · five bottom", ["aaaaa", "bcdef"]],
    // 7
    ["g23b1", "2 × 3 · bottom", ["abc", "def", "ggg"]],
    ["c7", "Seven columns", ["abcdefg"]],
    ["l1r6", "Left · six right", ["ab", "ac", "ad", "ae", "af", "ag"]],
    // 8
    ["g24", "2 × 4", ["abcd", "efgh"]],
    ["g42", "4 × 2", ["ab", "cd", "ef", "gh"]],
    ["c8", "Eight columns", ["abcdefgh"]],
    ["r8", "Eight rows", ["a", "b", "c", "d", "e", "f", "g", "h"]],
    // 9
    ["g33", "3 × 3", ["abc", "def", "ghi"]],
    // 16
    ["g44", "4 × 4", ["abcd", "efgh", "ijkl", "mnop"]],
  ];

  /** Area letters in the order a reader meets them, scanning row-major.
   *  That order IS the pane order: the primary always takes the first. */
  function areasOf(spec) {
    const seen = [];
    for (const row of spec) for (const ch of row) if (!seen.includes(ch)) seen.push(ch);
    return seen;
  }

  /** CSS grid only accepts an area that forms a solid rectangle. A typo here
   *  would otherwise fail silently — the browser drops the whole template and
   *  every pane stacks into cell 1. */
  function validate(id, spec) {
    const w = spec[0].length;
    if (spec.some((r) => r.length !== w)) throw new Error(`layout ${id}: ragged rows`);
    for (const ch of areasOf(spec)) {
      let top = Infinity, left = Infinity, bottom = -1, right = -1, n = 0;
      spec.forEach((row, r) => [...row].forEach((c, k) => {
        if (c !== ch) return;
        n++; top = Math.min(top, r); bottom = Math.max(bottom, r);
        left = Math.min(left, k); right = Math.max(right, k);
      }));
      if (n !== (bottom - top + 1) * (right - left + 1)) {
        throw new Error(`layout ${id}: area "${ch}" is not a rectangle`);
      }
    }
  }

  const LAYOUTS = {};
  for (const [id, label, spec] of SPECS) {
    validate(id, spec);
    LAYOUTS[id] = {
      id, label, spec,
      panes: areasOf(spec).length,
      areas: areasOf(spec),
      cols: spec[0].length,
      rows: spec.length,
      /** the value CSS wants: "a b" "a c" */
      template: spec.map((r) => `"${[...r].join(" ")}"`).join(" "),
    };
  }

  /** Layout ids are persisted, and the first three used to be spelled out. */
  const LEGACY = { single: "s1", cols: "c2", rows: "r2" };

  /* ── custom grids ────────────────────────────────────────────────────────
   *
   * The preset catalogue above is deliberately finite — the shapes a person
   * actually reaches for. The custom picker is the escape hatch: drag a
   * rectangle over a grid of cells and get exactly that uniform R×C, the way
   * openmarket and TradingView both offer. It is NOT a second layout model —
   * it builds the same {spec, template, areas} object every preset is and
   * drops it into LAYOUTS, so apply(), the splitters, the thumbnail and the
   * save/restore all treat it as any other layout with no special-casing.
   *
   * The id is `gRxC` (g2x3), memoised so the same drag twice is the same
   * object, and persisted like any preset — a reload rebuilds it from the id
   * alone. Capped at 5×5: one letter per cell keeps the string-spec model
   * (areasOf/validate/template all walk characters), and 25 charts is already
   * past the point of usefulness. */
  const CUSTOM_MAX = 5;
  const CELL = "abcdefghijklmnopqrstuvwxyz";

  function customId(rows, cols) { return `g${rows}x${cols}`; }

  /** Build (or fetch the memoised) LAYOUTS entry for a uniform rows×cols grid.
   *  Returns the id, ready to hand to apply(). */
  function ensureGrid(rows, cols) {
    const r = Math.max(1, Math.min(CUSTOM_MAX, rows | 0));
    const c = Math.max(1, Math.min(CUSTOM_MAX, cols | 0));
    const id = customId(r, c);
    if (LAYOUTS[id]) return id;
    // row-major letters, one per cell — "ab" / "cd" for 2×2
    const spec = [];
    let k = 0;
    for (let y = 0; y < r; y++) {
      let row = "";
      for (let x = 0; x < c; x++) row += CELL[k++];
      spec.push(row);
    }
    validate(id, spec);
    LAYOUTS[id] = {
      id, label: `${r} × ${c}`, spec, custom: true,
      panes: areasOf(spec).length, areas: areasOf(spec),
      cols: c, rows: r,
      template: spec.map((row) => `"${[...row].join(" ")}"`).join(" "),
    };
    return id;
  }

  let gridEl = null;
  let stage = null;       // the PRIMARY pane's element (main.js owns its chart)
  let layout = "s1";

  /* ── track sizes ─────────────────────────────────────────────────────────
   *
   * A layout used to be `repeat(N, 1fr)`: every pane the same size, with no
   * way to give the chart you are actually reading more room. Tracks are now
   * a list of fractions the user can drag, stored PER LAYOUT — c2 and r2 keep
   * their own proportions, so switching away and back does not reset the
   * split you set.
   *
   * They stay FRACTIONS rather than pixels on purpose: the grid is inside a
   * flex column that changes height with the window, and a pixel split would
   * stop meaning the same thing the moment the browser is resized.
   */
  const FR_KEY = "pane_fr";
  let frAll = Store.get(FR_KEY, {}) || {};
  const RECT_KEY = "pane_rects";
  let rectAll = Store.get(RECT_KEY, {}) || {};
  let rects = [];

  function frOf(L) {
    const saved = frAll[L.id] || {};
    const fix = (arr, n) => (Array.isArray(arr) && arr.length === n
      && arr.every((x) => Number.isFinite(x) && x > 0) ? arr.slice()
      : Array(n).fill(1));
    return { cols: fix(saved.cols, L.cols), rows: fix(saved.rows, L.rows) };
  }

  let active = 0;         // 0 = primary, 1..n = subs — the pane the toolbar drives
  const subs = [];        // active secondary charts
  let subSeq = 0;         // monotonic id for a sub's drawing runtime (never reused)
  // main.js installs this so a tool finishing on a secondary pane hands the
  // rail back to the cursor, exactly as the primary's onToolDone does.
  let onSubToolDone = null;

  /* Must stay identical to main.js's copy — a sub-pane's axis sits directly
     under the primary's and any difference reads as a rendering bug. */
  const CHART_FONT = "'Inter', ui-sans-serif, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif";

  function chartOpts() {
    const P = Theme.palette;
    return {
      layout: {
        background: { color: P.chartBg }, textColor: P.axisText,
        fontFamily: CHART_FONT, fontSize: 11,
        panes: { separatorColor: P.separator, separatorHoverColor: P.separatorHover },
        // As the primary chart — one mark on the surface, on the primary, and
        // not a row of library logos once a layout splits into four.
        attributionLogo: false,
      },
      grid: { vertLines: { color: P.grid }, horzLines: { color: P.grid } },
      rightPriceScale: { borderColor: P.border, scaleMargins: { top: 0.08, bottom: 0.24 } },
      timeScale: { borderColor: P.border, timeVisible: true, secondsVisible: false, rightOffset: 4 },
      crosshair: {
        mode: LWC.CrosshairMode.Normal,
        /* Native axis labels match the primary chart in every split pane. */
        vertLine: { color: P.crosshair, labelBackgroundColor: P.crosshairLabel },
        horzLine: { color: P.crosshair, labelBackgroundColor: P.crosshairLabel },
      },
      autoSize: true,
    };
  }

  async function fetchBars(symbol, interval, limit) {
    const qs = new URLSearchParams({
      symbol, interval: WIRE[interval] || interval, limit: String(limit),
    });
    const res = await Net.get(`${API}/bars?${qs}`,
      // the session decides how far back intraday history goes (plan depth)
      typeof Auth !== "undefined" ? { headers: Auth.headers() } : undefined);
    if (!res.ok) throw new Error(`dataserver HTTP ${res.status}`);
    const d = await res.json();
    // the axis shift belongs to the SYMBOL — crypto folds on UTC, NSE on IST
    const shift = Sym.of(symbol).tz;
    return d.bars.map((b) => ({
      time: b.t + shift, open: b.o, high: b.h, low: b.l, close: b.c, volume: b.v,
    }));
  }

  /** One secondary chart: its own element, instance, interval, symbol, data
   *  and indicators. */
  function makeSub(interval, symbol, slot) {
    const root = document.createElement("div");
    root.className = "subchart";
    // No per-pane toolbar. Every pane wears the same in-chart legend and the
    // ONE toolbar at the top drives whichever pane is selected — two panes
    // with two different interval strips made the split read as two apps
    // sharing a window rather than one chart shown twice.
    root.innerHTML =
      `<div class="sub-canvas"></div>
       <div class="readout sub-legend">
         <div class="title"></div>
         <div class="row ohlc"></div>
         <div class="ind-legend empty"></div>
       </div>`;
    const canvas = root.querySelector(".sub-canvas");
    const titleEl = root.querySelector(".sub-legend .title");
    const ohlcEl = root.querySelector(".sub-legend .ohlc");
    const legendEl = root.querySelector(".sub-legend .ind-legend");

    const chart = LWC.createChart(canvas, chartOpts());
    // Built through ChartSettings with this pane's own type. `let`, because
    // the type switcher rebuilds the series (see rebind in
    // sub.settings below). See js/chartsettings.js makeSeries / setType.
    let candle = ChartSettings.withTarget({ slot }, () => ChartSettings.makeSeries(chart));
    // No volume series here either — it is an indicator now, added below
    // through this pane's OWN manager so it wears the same eye, gear and ×
    // as every other study on the pane. See js/indicators.js seriesFor().
    const sub = { root, chart, candle, interval, destroyed: false,
                  bars: [], symbol: (symbol || Sym.name).toUpperCase() };
    /* A secondary pane owns its settings. `label` is read at paint time
     * because this pane's symbol can change under it. */
    sub.settings = {
      chart, candle, root, slot,
      // a secondary pane is built smaller than the primary on purpose, and
      // "Default" in the dialog has to mean THESE, not the primary's
      defaults: { fontSize: 11, rightOffset: 4 },
      label: () => sub.symbol,
      // The type switcher rebuilt this pane's price series — re-point the local
      // and the sub's own handle. A secondary pane has no scene/markers, so
      // there is nothing else bound to the old series.
      rebind(next) { candle = next; sub.candle = next; },
      repaint() {
        if (!sub.bars.length) return;
        candle.setData(ChartSettings.pricePoints(sub.bars));
        // the strip's colours are the study's, but the direction rule is the
        // dialog's — same coupling the primary chart documents
        if (sub.ind) sub.ind.retheme(sub.bars);
      },
    };
    ChartSettings.register(sub.settings);
    // Indicators are a property of the CHART, not of the app: the one toolbar
    // adds to whichever pane is selected, and each pane keeps what it was
    // given. Settings still live in one place per indicator, so an EMA styled
    // in one pane is the same EMA everywhere — only membership is per-pane.
    sub.ind = Indicators.createManager(chart);
    /* …and so is the legend. A secondary pane wears the same in-chart list
     * the primary does — name, live value, eye/gear/×/⋯ — reading its OWN
     * manager. That is the difference the header strip could never draw: one
     * strip, shared, describing whichever pane happened to hold the
     * selection. The collapse state is not persisted here: a secondary pane
     * is created by the layout and dies with it. */
    sub.legend = IndLegend.create({
      chart, chartEl: canvas, mgr: sub.ind, stage: root, host: legendEl,
      openSettings: (id) => { if (onSettings) onSettings(id, sub.ind); },
      onChange: () => document.dispatchEvent(
        new CustomEvent("charto:indicators-changed")),
    });

    /* What the chat draws for THIS chart. A pattern found on the hourly
     * belongs on the hourly pane, so every pane carries a scene: the primary's
     * renderer, reading this pane's bars, clock and indicator strips. main.js
     * routes each drawn item here by the chart it was computed on. */
    const paneRows = () => {
      const out = [{ key: "price", label: "price", pane: candle.getPane(), series: candle }];
      for (const [, a] of sub.ind.active || []) {
        if (a.def.kind !== "pane" || !a.series.length) continue;
        out.push({ key: a.def.name, period: a.def.period, label: a.def.label,
                   pane: a.series[0].getPane(), series: a.series[0] });
      }
      return out;
    };
    const rowRect = (p) => {
      const pe = p.pane.getHTMLElement && p.pane.getHTMLElement();
      return pe ? pe.getBoundingClientRect() : null;
    };
    sub.sceneAbort = new AbortController();
    sub.scene = Scene.create(chart, candle, {
      getBars: () => sub.bars,
      container: canvas,
      panes: paneRows,
      paneAt: (y) => (paneRows().find((p) => {
        const r = rowRect(p);
        return r && y >= r.top && y <= r.bottom;
      }) || { key: "price" }).key,
      yIn: (y, key) => {
        const r = rowRect(paneRows().find((p) => p.key === key) || paneRows()[0]);
        return r ? y - r.top : y;
      },
      getIntervalSec: () => IV_SEC[WIRE[sub.interval] || sub.interval] || 60,
      // detectors speak exchange time; this pane's axis runs on its symbol's clock
      toChartTime: (t) => t + Sym.of(sub.symbol).tz,
      fromChartTime: (t) => t - Sym.of(sub.symbol).tz,
      isCursorMode: () => true,
      onHover: () => {},
      onSelect: () => {},
      onIndicator: (a) => {
        const id = sub.ind.ensureFromId(String(a.name || "").split("@")[0]);
        if (id && !sub.ind.isActive(id)) {
          Promise.resolve(sub.ind.toggle(id, sub.bars)).catch(() => {});
        }
      },
      onChange: () => document.dispatchEvent(new CustomEvent("charto:pane-scene-changed")),
      signal: sub.sceneAbort.signal,
    });

    /* The crosshair's plates, the same module the primary uses. `panes()` is
     * the shape the drawing layer already speaks — the price pane plus one row
     * per indicator that owns a pane of its own — and it is read on every
     * pointer move, so a study added or removed here needs no re-wiring. */
    const fmt = (n) => Sym.of(sub.symbol).num(n);
    function paintLegend(b) {
      const d = Sym.of(sub.symbol);
      titleEl.innerHTML =
        `<span class="sym-btn" data-sym-btn>${Universe.logoHTML(sub.symbol, "co-logo lg")}`
        + `${sub.symbol}</span>`
        + `<span class="sep">·</span>${sub.interval}`
        + `<span class="sep">·</span><span class="ex">${d.venue}</span>`;
      if (!b) { ohlcEl.innerHTML = ""; return; }
      const cls = b.close >= b.open ? "up" : "down";
      // same per-figure classes the primary legend uses — a phone hides the
      // same four figures here, or a split would keep the row this width
      ohlcEl.innerHTML =
        `<span class="ro-o"><i>O</i> <b class="${cls}">${fmt(b.open)}</b></span>` +
        `<span class="ro-h"><i>H</i> <b class="${cls}">${fmt(b.high)}</b></span>` +
        `<span class="ro-l"><i>L</i> <b class="${cls}">${fmt(b.low)}</b></span>` +
        `<span class="ro-c"><i>C</i> <b class="${cls}">${fmt(b.close)}</b></span>` +
        `<span class="ro-v"><i>V</i> <b class="${cls}">${fmt(b.volume)}</b></span>`;
    }
    chart.subscribeCrosshairMove((p) => {
      if (!p || !p.time) return paintLegend(sub.bars[sub.bars.length - 1]);
      const d = p.seriesData && p.seriesData.get(candle);
      paintLegend(d ? { ...d, volume: (sub.bars.find((x) => x.time === p.time) || {}).volume || 0 }
                    : sub.bars[sub.bars.length - 1]);
    });

    /* ── the alert ⊕, on THIS pane ──────────────────────────────────────────
     *
     * The primary chart grows one of these (js/main.js makePlus/syncPlus): a
     * mark that rides the pointer down the price axis and, clicked, opens the
     * alert card at the level under it. Until now a secondary pane had none, so
     * hovering pane 2's axis offered nothing — the reason the affordance only
     * appeared "in the first box".
     *
     * This is the same mark and the same CSS, self-contained on the pane. It
     * does not draw the alert's price LINE — that stays a primary-chart fact
     * (see alerts.js syncChartLines, bound to __charto.candle) — but it does
     * the thing the hover is for: it lets you ADD an alert on this pane's own
     * instrument, at the price you are pointing at, on this pane's interval.
     * The line then shows up whenever this pane's symbol is the page's symbol,
     * which is exactly when alerts.js can draw it.
     */
    let plus = null, plusPrice = null;
    const PLUS_PAD = 4;
    function makePlus() {
      const b = document.createElement("div");
      b.className = "alert-plus";
      b.innerHTML = `<span class="alert-plus-mark">`
        + `<span class="alert-plus-ring">${Icons.svg("plus", "xs")}</span></span>`
        + `<span class="alert-plus-value"></span>`;
      canvas.appendChild(b);
      return b;
    }
    function hidePlus() {
      if (plus) plus.classList.remove("show", "hot");
      plusPrice = null;
    }
    function onPlus(x, y) {
      if (!plus || !plus.classList.contains("show")) return false;
      const mark = plus.querySelector(".alert-plus-mark");
      if (!mark) return false;
      const r = mark.getBoundingClientRect();
      return x >= r.left - PLUS_PAD && x <= r.right + PLUS_PAD
          && y >= r.top - PLUS_PAD && y <= r.bottom + PLUS_PAD;
    }
    function syncPlus(clientX, clientY) {
      // Only over the PRICE pane, and only on the candle side of the scale —
      // the axis is what you grab to rescale, and lighting the mark on it turns
      // an ordinary axis drag into a duplicate marker. Same split the primary
      // makes: --axis-w is measured here off this pane's own scale.
      // A tool armed on this pane owns the pointer — the ⊕ must not compete
      // with a line being placed, the same rule the primary's syncPlus applies.
      if (sub.draw && sub.draw.state.tool !== "cursor") return hidePlus();
      let axisW = 0;
      try { axisW = chart.priceScale("right").width(); } catch { /* not laid out */ }
      canvas.style.setProperty("--axis-w", `${axisW || 64}px`);
      const box = canvas.getBoundingClientRect();
      const scaleLeft = box.right - (axisW || 64);
      // The currency/venue badge sits at the top of the price scale; keep the
      // pill clear of the first ~34px so it never prints across it.
      const inside = clientX >= box.left && clientX < scaleLeft
        && clientY >= box.top + 34 && clientY <= box.bottom;
      if (!inside) return hidePlus();
      const px = candle.coordinateToPrice(clientY - box.top);
      if (px == null || !isFinite(px)) return hidePlus();
      if (!plus || !plus.isConnected) plus = makePlus();
      const d = Sym.of(sub.symbol);
      plusPrice = Number(px.toFixed(px >= 100 ? 2 : 4));
      const value = plus.querySelector(".alert-plus-value");
      if (value) { try { value.textContent = d.num(plusPrice); }
                   catch { value.textContent = String(plusPrice); } }
      plus.style.top = (clientY - box.top) + "px";
      plus.title = `Alert at ${d.num(plusPrice)} on ${sub.symbol}`;
      plus.classList.add("show");
      plus.classList.toggle("hot", onPlus(clientX, clientY));
    }
    canvas.addEventListener("mousemove", (e) => syncPlus(e.clientX, e.clientY));
    canvas.addEventListener("mouseleave", hidePlus);
    // Capture phase, ahead of the library's own canvas handlers, so a click on
    // the mark opens the card instead of panning the chart underneath.
    canvas.addEventListener("click", (e) => {
      if (!onPlus(e.clientX, e.clientY)) return;
      const at = plusPrice;
      e.preventDefault(); e.stopPropagation(); e.stopImmediatePropagation();
      hidePlus();
      if (at == null || typeof Alerts === "undefined") return;
      const last = sub.bars.length ? sub.bars[sub.bars.length - 1].close : null;
      Alerts.open({ symbol: sub.symbol, level: at, last,
                    interval: WIRE[sub.interval] || sub.interval });
    }, true);
    canvas.addEventListener("mousedown", (e) => {
      if (onPlus(e.clientX, e.clientY)) {
        e.preventDefault(); e.stopPropagation(); e.stopImmediatePropagation();
      }
    }, true);

    async function load(rawIv) {
      // The toolbar speaks the server's ids (1d/1w/1mo), this pane's ladder and
      // legend speak D/W/M. Normalising here is why a daily pane picked from
      // the header still gets a daily PAGE size and a date-only axis.
      const iv = DISP[rawIv] || rawIv;
      sub.interval = iv;
      paintLegend(null);
      try {
        const bars = await fetchBars(sub.symbol, iv, PAGE[iv] || 2000);
        if (sub.destroyed) return;
        sub.bars = bars;
        // the settings module builds the series — see the note beside
        // main.js's paint(): one place decides what a green bar is, and
        // pricePoints hands the active shape (candles/bars/line/area) its data
        candle.setData(ChartSettings.withTarget(sub.settings,
          () => ChartSettings.pricePoints(bars)));
        chart.applyOptions({
          timeScale: { timeVisible: !["D", "W", "M"].includes(iv) },
        });
        chart.timeScale().setVisibleLogicalRange(
          { from: Math.max(0, bars.length - 140), to: bars.length + 4 });
        paintLegend(bars[bars.length - 1]);
        // this pane's own indicators recompute on ITS symbol and interval
        sub.ind.recomputeAll(bars, {
          interval: WIRE[iv] || iv, limit: bars.length, symbol: sub.symbol,
        }).catch(() => {});
        // A pane opens with the volume strip, the way it always did — the
        // difference is that it is a study now and can be taken off. Added
        // after the first load, not at construction, because it needs bars
        // and this pane's context; the guard makes a symbol switch (which
        // re-enters load()) leave whatever the user has since chosen alone.
        if (!sub.volumeSeeded) {
          sub.volumeSeeded = true;
          Promise.resolve(sub.ind.toggle("volume", bars))
            // an index prints no volume and the backend says so; a pane on
            // one simply opens without the strip
            .catch(() => {});
        }
        // the volume strip (and any study) is a new pane the drawing layer
        // must attach its overlay to, the same re-attach the primary runs on
        // charto:indicators-changed
        if (sub._syncDrawPanes) requestAnimationFrame(sub._syncDrawPanes);
      } catch (e) {
        if (!sub.destroyed) titleEl.textContent = String(e.message || e);
      }
    }
    sub.load = load;

    /* ── the drawing runtime, on THIS pane ──────────────────────────────────
     *
     * A secondary pane gets the same drawing engine the primary does — arm a
     * tool, draw, select, drag, restyle — over its OWN canvas, bars and panes.
     * It is `persist:false`, so its shapes live for the life of the pane and
     * stay out of the symbol store and the undo history: a split pane is
     * reference, and a line drawn on the 1h view is about reading the 1h view,
     * not about the session the primary owns.
     *
     * The tool is armed on WHICHEVER pane holds the selection — see
     * setActiveDraw()/toolForActive() below — so the one rail drives the one
     * chart you are working in, exactly as the interval strip already does. */
    const paneId = `sub-${++subSeq}`;
    sub.paneId = paneId;
    function subPanesList() {
      const out = [{ key: "price", label: "price",
                     pane: candle.getPane(), series: candle }];
      for (const [, a] of sub.ind.active) {
        if (!a.def || a.def.kind !== "pane" || !(a.series || []).length) continue;
        out.push({ key: a.def.name, period: a.def.period, label: a.def.label,
                   pane: a.series[0].getPane(), series: a.series[0] });
      }
      return out;
    }
    sub.draw = Drawings.create(chart, candle, {
      getBars: () => sub.bars,
      getIntervalSec: () => IV_SEC[sub.interval] || 86400,
      container: canvas,
      stage: root,
      panes: subPanesList,
      persist: false,
      paneId,
      setStatus: () => {},
      // a tool that finishes hands the toolbar back to cursor, on every pane
      onToolDone: () => { if (onSubToolDone) onSubToolDone(); },
      onChange: () => {},
    });
    // panes come and go with this pane's own indicators — re-attach the
    // drawing primitives the same way the primary does on its own changes
    sub._syncDrawPanes = () => { try { sub.draw.syncPanes(); } catch {} };

    /** Point this pane at another instrument. A cold symbol hydrates server
     *  side (~6 s), so the legend says what it is doing rather than sitting
     *  on the old company's bars while the new ones are in flight. */
    sub.setSymbol = (s) => {
      const next = String(s || "").toUpperCase();
      if (!next || next === sub.symbol) return;
      sub.symbol = next;
      // what was drawn belonged to the old instrument's prices
      sub.scene.setItems([]);
      titleEl.innerHTML = `${next}<span class="sep">·</span>loading…`;
      ohlcEl.innerHTML = "";
      if (subs[active - 1] === sub) emitActive();
      return load(sub.interval);
    };

    // A secondary chart owns its own price scale and bars. Resolve the point
    // against this chart before building actions; the primary menu's captured
    // series and symbol would otherwise target the wrong instrument.
    canvas.addEventListener("contextmenu", (e) => {
      if (sub.destroyed || (sub.draw && sub.draw.state.tool !== "cursor")) return;
      const pricePane = candle.getPane().getHTMLElement();
      const pr = pricePane.getBoundingClientRect();
      if (e.clientY < pr.top || e.clientY > pr.bottom) return;
      const cr = canvas.getBoundingClientRect();
      const px = candle.coordinateToPrice(e.clientY - pr.top);
      if (!Number.isFinite(px)) return;
      e.preventDefault();
      e.stopPropagation();
      setActive(subs.indexOf(sub) + 1);
      if (window.__chartoCloseMenus) window.__chartoCloseMenus(null);
      const drawing = sub.draw.state.hoverId &&
        sub.draw.state.drawings.find((d) => d.id === sub.draw.state.hoverId);
      if (drawing) {
        Ctx.open(e.clientX, e.clientY, [
          { head: drawing.ref || "Drawing", note: sub.symbol },
          { icon: "chat", label: "Chat about drawing",
            on: () => {
              document.dispatchEvent(new CustomEvent("charto:draw-tag",
                { detail: sub.draw.tagOf(drawing.id) }));
              Chat.ask(`Analyse drawing ${drawing.ref || drawing.id} on ${sub.symbol} ${sub.interval}.`);
            } },
          { icon: "copy", label: "Duplicate", on: () => sub.draw.clone(drawing.id) },
          { icon: "lock", label: drawing.locked ? "Unlock" : "Lock",
            on: () => sub.draw.setLocked(drawing.id, !drawing.locked) },
          { sep: true },
          { icon: "trash", label: "Remove", danger: true,
            on: () => sub.draw.remove(drawing.id) },
        ]);
        return;
      }
      const time = chart.timeScale().coordinateToTime(e.clientX - cr.left);
      const bar = sub.bars.find((b) => b.time === time);
      const digits = Math.abs(px) >= 100 ? 2 : 4;
      const level = Number(px.toFixed(digits));
      const price = Sym.of(sub.symbol).price(px, {
        minimumFractionDigits: digits, maximumFractionDigits: digits,
      });
      const when = (t) => {
        const d = new Date(t * 1000);
        const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
        const date = `${d.getUTCDate()} ${months[d.getUTCMonth()]} ${d.getUTCFullYear()}`;
        return ["D", "W", "M"].includes(sub.interval) ? date :
          `${date} ${String(d.getUTCHours()).padStart(2, "0")}:` +
          String(d.getUTCMinutes()).padStart(2, "0");
      };
      const address = bar ? `${when(bar.time)} @ ${price}` : null;
      const toast = (message) => {
        if (typeof Layouts !== "undefined" && Layouts.toast) Layouts.toast(message);
      };
      const copy = (value) => navigator.clipboard.writeText(String(value))
        .then(() => toast("Copied to clipboard"))
        .catch(() => toast("Clipboard access was denied"));
      const screenshot = (dest, rect) => {
        let shot = chart.takeScreenshot(true);
        if (rect) {
          const crop = document.createElement("canvas");
          const sx = shot.width / Math.max(1, canvas.clientWidth);
          const sy = shot.height / Math.max(1, canvas.clientHeight);
          crop.width = Math.max(1, Math.round(rect.w * sx));
          crop.height = Math.max(1, Math.round(rect.h * sy));
          crop.getContext("2d").drawImage(shot, rect.x * sx, rect.y * sy,
            rect.w * sx, rect.h * sy, 0, 0, crop.width, crop.height);
          shot = crop;
        }
        if (dest === "chat") {
          const panel = document.getElementById("chatPanel");
          if (panel && panel.classList.contains("hidden"))
            document.getElementById("chatToggle").click();
          document.dispatchEvent(new CustomEvent("charto:screenshot",
            { detail: { uri: shot.toDataURL("image/png") } }));
          toast("Snapshot attached to the chat");
          return;
        }
        if (dest === "copy") {
          shot.toBlob(async (blob) => {
            if (!blob) return toast("The snapshot could not be made");
            try {
              await navigator.clipboard.write([new ClipboardItem({ "image/png": blob })]);
              toast("Snapshot copied");
            } catch { toast("Clipboard access was denied"); }
          }, "image/png");
          return;
        }
        const link = document.createElement("a");
        link.href = shot.toDataURL("image/png");
        link.download = `${sub.symbol}-${sub.interval}-chart.png`;
        link.click();
      };
      const selectRegion = () => {
        const overlay = document.createElement("div");
        overlay.className = "shot-overlay";
        overlay.innerHTML = '<div class="shot-hint">drag to capture · Esc to cancel</div>';
        canvas.appendChild(overlay);
        let x0 = 0, y0 = 0, selection = null;
        const off = () => {
          overlay.remove();
          document.removeEventListener("keydown", key, true);
        };
        const key = (event) => {
          if (event.key === "Escape") { event.stopPropagation(); off(); }
        };
        document.addEventListener("keydown", key, true);
        overlay.addEventListener("pointerdown", (event) => {
          const r = overlay.getBoundingClientRect();
          x0 = event.clientX - r.left; y0 = event.clientY - r.top;
          selection = document.createElement("div");
          selection.className = "shot-marquee";
          overlay.appendChild(selection);
          overlay.setPointerCapture(event.pointerId);
        });
        overlay.addEventListener("pointermove", (event) => {
          if (!selection) return;
          const r = overlay.getBoundingClientRect();
          const x = event.clientX - r.left, y = event.clientY - r.top;
          Object.assign(selection.style, {
            left: `${Math.min(x0, x)}px`, top: `${Math.min(y0, y)}px`,
            width: `${Math.abs(x - x0)}px`, height: `${Math.abs(y - y0)}px`,
          });
        });
        overlay.addEventListener("pointerup", (event) => {
          if (!selection) return;
          const r = overlay.getBoundingClientRect();
          const x = event.clientX - r.left, y = event.clientY - r.top;
          const rect = { x: Math.min(x0, x), y: Math.min(y0, y),
            w: Math.abs(x - x0), h: Math.abs(y - y0) };
          off();
          if (rect.w >= 24 && rect.h >= 24) screenshot("chat", rect);
        });
      };
      const drawings = sub.draw.state.drawings.length;
      const annotations = sub.scene.state.items.length;
      Ctx.open(e.clientX, e.clientY, [
        { head: sub.symbol, note: `${price}${bar ? ` · ${when(bar.time)}` : ""}` },
        { icon: "alertPlus", label: "Alert here", hint: price,
          on: () => Alerts.open({ symbol: sub.symbol, level,
            last: sub.bars.at(-1)?.close ?? null, interval: sub.interval }) },
        { icon: "position", label: "Plan a position",
          on: () => Chat.ask(`Plan a position on ${sub.symbol} with entry at ${price}.`) },
        { sep: true },
        { icon: "chat", label: "Chat", sub: [
          { label: `Is ${price} a real level on ${sub.symbol}?`, wrap: true,
            on: () => Chat.ask(`Is ${price} a real level on ${sub.symbol}?`) },
          bar && { label: `Why did ${sub.symbol} move on ${when(bar.time)}?`, wrap: true,
            on: () => Chat.ask(`Why did ${sub.symbol} move on ${when(bar.time)}?`) },
          { label: `Analyse ${sub.symbol} on the ${sub.interval} chart.`, wrap: true,
            on: () => Chat.ask(`Analyse ${sub.symbol} on the ${sub.interval} chart.`) },
        ].filter(Boolean) },
        { icon: "tag", label: "Tag point",
          on: () => document.dispatchEvent(new CustomEvent("charto:compose",
            { detail: address || String(level) })) },
        { sep: true },
        { icon: "camera", label: "Screenshot", sub: [
          { label: "Whole chart", on: () => screenshot("chat") },
          { label: "Select region", on: selectRegion },
          { label: "Download image", on: () => screenshot("download") },
          { label: "Copy image", on: () => screenshot("copy") },
        ] },
        { icon: "listPlus", label: "Add to watchlist",
          sub: () => Panels.lists().map((list) => ({
            label: list.name, tick: list.syms.includes(sub.symbol),
            disabled: list.syms.includes(sub.symbol),
            on: () => { Panels.watch(sub.symbol, list.id);
              toast(`${sub.symbol} added to ${list.name}`); },
          })) },
        bar && { icon: "pen", label: "Add note",
          on: () => sub.draw.noteAt("price", bar.time, px) },
        { sep: true },
        { icon: "copy", label: "Copy", sub: [
          { label: "Price", hint: price, on: () => copy(level) },
          address && { label: "Address", on: () => copy(address) },
        ].filter(Boolean) },
        { sep: true },
        { icon: "rotateCw", label: "Reset view",
          on: () => chart.timeScale().fitContent() },
        (drawings || annotations) && { icon: "trash", label: "Remove",
          sub: [
            drawings && { label: `Drawings (${drawings})`, danger: true,
              on: () => sub.draw.clearAll() },
            annotations && { label: `Annotations (${annotations})`, danger: true,
              on: () => sub.scene.setItems([]) },
          ].filter(Boolean) },
        { icon: "bell", label: "Alerts", on: () => Panels.show("alerts") },
      ]);
    });

    // the ticker in the legend IS the instrument switch
    titleEl.addEventListener("click", (e) => {
      const btn = e.target.closest("[data-sym-btn]");
      if (!btn) return;
      e.stopPropagation();
      setActive(subs.indexOf(sub) + 1);
      Universe.open({ anchor: btn, current: sub.symbol, onPick: (s) => sub.setSymbol(s) });
    });

    sub.retheme = () => {
      chart.applyOptions(chartOpts());
      // the candles are the settings module's to colour — the theme is only
      // its default, so re-applying it here is what puts an explicit choice
      // back over the palette this line just wrote
      ChartSettings.applyTo(sub.settings);
      sub.ind.retheme(sub.bars);
      load(sub.interval);   // candle colours are per-point, so repaint
    };
    sub.destroy = () => {
      sub.destroyed = true;
      sub.sceneAbort.abort();   // the scene's window listeners go with the pane
      // the drawing runtime holds pointer listeners on this canvas and
      // primitives on its series; drop them before the chart goes, and
      // unregister so a stale paneId cannot resolve to a dead pane
      try { sub.draw.destroy(); } catch { /* never created */ }
      // first: a settings edit must never reach a chart that is going away
      ChartSettings.unregister(sub.settings);
      // before the chart goes: the legend holds a sink on the manager and
      // floating boxes parented to `root`, and a torn-down chart cannot
      // answer the crosshair subscription still pointed at it
      try { sub.legend.destroy(); } catch { /* never created */ }
      // …and the plates, which hold a live SVG filter each: a session of
      // layout changes would otherwise leave one behind per pane ever opened
      try { chart.remove(); } catch { /* already gone */ }
      root.remove();
    };

    /* Data is loaded only AFTER the element is in the document. A chart
     * created in a detached node has zero width, so setVisibleLogicalRange
     * lands against a nil time scale and the bars end up crushed against the
     * right edge with a canyon of empty space beside them. */
    sub.start = () => load(interval);
    return sub;
  }

  function clearSubs() {
    for (const s of subs.splice(0)) s.destroy();
  }

  /** Which pane the single toolbar is aimed at. TradingView marks it with a
   *  border on the chart itself, which is the only honest place for it: the
   *  selection is a property of the pane, not of the toolbar. */
  function setActive(i) {
    const n = Math.max(0, Math.min(i, subs.length));
    const same = n === active;
    active = n;
    if (stage) stage.classList.toggle("pane-active", n === 0 && subs.length > 0);
    subs.forEach((s, k) => s.root.classList.toggle("pane-active", n === k + 1));
    if (same) return;   // a click inside the already-selected pane is not news
    emitActive();
  }
  /** Tell everyone aimed at "the selected pane" what it now holds. Selection
   *  is not the only thing that changes it — pointing the selected pane at
   *  another instrument changes it too, and the chat's subject chip has to
   *  hear about that or it keeps naming the company you just navigated away
   *  from. */
  function emitActive() {
    for (const fn of onActiveSubs) {
      try { fn(active, intervalOf(active), symbolOf(active)); } catch (e) { console.error(e); }
    }
  }
  const onActiveSubs = [];

  function intervalOf(i) {
    return i === 0 ? null : (subs[i - 1] || {}).interval || null;
  }
  function symbolOf(i) {
    return i === 0 ? Sym.name : (subs[i - 1] || {}).symbol || Sym.name;
  }

  /* A second pane defaults to a SLOWER interval — the reason to open one is
   * context, and a duplicate of what you are already looking at is not. Past
   * the first couple the ladder keeps climbing rather than filling six panes
   * with the same hour candles. */
  const SUB_LADDER = ["1h", "D", "15m", "W", "30m", "M", "5m"];

  /** Start from the catalogue's rectangular areas, then keep each pane's
   *  bounds independently. A CSS grid track moves every pane in its column;
   *  these bounds let the top divider of a 2×2 move on its own. */
  function sizeTracks(L, fr) {
    const valid = rectAll[L.id];
    if (Array.isArray(valid) && valid.length === L.panes && valid.every((r) =>
      r && [r.x, r.y, r.w, r.h].every(Number.isFinite) &&
      r.x >= 0 && r.y >= 0 && r.w > 0 && r.h > 0 &&
      r.x + r.w <= 1.001 && r.y + r.h <= 1.001)) {
      rects = valid.map((r) => ({ ...r }));
    } else {
      const fractions = (values) => {
        const sum = values.reduce((a, b) => a + b, 0);
        const cuts = [0];
        for (const v of values) cuts.push(cuts.at(-1) + v / sum);
        cuts[cuts.length - 1] = 1;
        return cuts;
      };
      const xs = fractions(fr.cols), ys = fractions(fr.rows);
      rects = L.areas.map((area) => {
        const cells = [];
        L.spec.forEach((row, y) => [...row].forEach((ch, x) => {
          if (ch === area) cells.push([x, y]);
        }));
        const left = Math.min(...cells.map((c) => c[0]));
        const top = Math.min(...cells.map((c) => c[1]));
        const right = Math.max(...cells.map((c) => c[0])) + 1;
        const bottom = Math.max(...cells.map((c) => c[1])) + 1;
        return { x: xs[left], y: ys[top], w: xs[right] - xs[left],
                 h: ys[bottom] - ys[top] };
      });
    }
    gridEl.classList.add("pane-free");
    requestAnimationFrame(() => { paintRects(); mountSplitters(); });
  }

  function paintRects() {
    const nodes = [stage, ...subs.map((s) => s.root)];
    nodes.forEach((node, i) => {
      if (!node || !rects[i]) return;
      const r = rects[i];
      node.style.left = `calc(${r.x * 100}% + ${r.x ? 0.5 : 0}px)`;
      node.style.top = `calc(${r.y * 100}% + ${r.y ? 0.5 : 0}px)`;
      node.style.width = `calc(${r.w * 100}% - ${r.x + r.w < 0.999 ? 0.5 : 0}px - ${r.x ? 0.5 : 0}px)`;
      node.style.height = `calc(${r.h * 100}% - ${r.y + r.h < 0.999 ? 0.5 : 0}px - ${r.y ? 0.5 : 0}px)`;
    });
  }

  /** A handle per INTERNAL grid line, laid over the seam that is already
   *  drawn there — the gap between panes IS the divider, so the thing you
   *  grab and the thing you see are the same edge rather than two that have
   *  to be kept in sync. */
  function mountSplitters() {
    if (!gridEl) return;
    for (const el of [...gridEl.querySelectorAll(".pane-split")]) el.remove();
    const L = LAYOUTS[layout];
    if (!L || (L.cols < 2 && L.rows < 2)) return;
    const near = (a, b) => Math.abs(a - b) < 0.00001;
    // A pane spanning two rows ties their vertical edges together. Where no
    // pane spans, each row gets its own vertical handle. The perpendicular
    // axis remains continuous, so independent drags cannot overlap panes.
    const hasWide = L.areas.some((a) => L.spec.some((row) => row.includes(a) && row.indexOf(a) !== row.lastIndexOf(a)));
    const segmented = hasWide ? "c" : L.areas.some((a) => L.spec.filter((row) => row.includes(a)).length > 1) ? "r" : "c";
    for (const axis of ["c", "r"]) {
      const edges = [];
      rects.forEach((a, i) => rects.forEach((b, j) => {
        if (i === j) return;
        const pos = axis === "c" ? a.x + a.w : a.y + a.h;
        const other = axis === "c" ? b.x : b.y;
        const lo = Math.max(axis === "c" ? a.y : a.x, axis === "c" ? b.y : b.x);
        const hi = Math.min(axis === "c" ? a.y + a.h : a.x + a.w,
                            axis === "c" ? b.y + b.h : b.x + b.w);
        if (near(pos, other) && hi - lo > 0.00001) edges.push({ i, j, pos, lo, hi });
      }));
      while (edges.length) {
        const group = [edges.shift()];
        for (let k = 0; k < edges.length;) {
          const edge = edges[k];
          if (near(edge.pos, group[0].pos) && (axis !== segmented ||
              group.some((g) => g.i === edge.i || g.j === edge.j ||
                                  g.i === edge.j || g.j === edge.i))) {
            group.push(...edges.splice(k, 1));
            k = 0;
          } else k++;
        }
        const d = document.createElement("div");
        const lo = Math.min(...group.map((g) => g.lo));
        const hi = Math.max(...group.map((g) => g.hi));
        d.className = `pane-split ${axis === "c" ? "v" : "h"}`;
        if (axis === "c") {
          d.style.left = `${group[0].pos * 100}%`;
          d.style.top = `${lo * 100}%`;
          d.style.height = `${(hi - lo) * 100}%`;
        } else {
          d.style.top = `${group[0].pos * 100}%`;
          d.style.left = `${lo * 100}%`;
          d.style.width = `${(hi - lo) * 100}%`;
        }
        d._edge = { axis, pos: group[0].pos,
                    left: [...new Set(group.map((g) => g.i))],
                    right: [...new Set(group.map((g) => g.j))] };
        gridEl.appendChild(d);
      }
    }
  }

  /** Drag one boundary. Only the two tracks it sits between change, so the
   *  rest of the layout holds still instead of every pane shifting. */
  function beginDrag(e) {
    const h = e.target.closest(".pane-split");
    if (!h || !h._edge) return;
    e.preventDefault();
    e.stopPropagation();
    const { axis, pos, left, right } = h._edge;
    const vert = axis === "c";
    const before = rects.map((r) => ({ ...r }));
    const start = vert ? e.clientX : e.clientY;
    h.classList.add("dragging");
    document.body.style.cursor = vert ? "col-resize" : "row-resize";

    const move = (ev) => {
      const delta = ((vert ? ev.clientX : ev.clientY) - start) /
        (vert ? gridEl.clientWidth : gridEl.clientHeight);
      const min = 0.06;
      const low = Math.max(...left.map((i) => min - (vert ? before[i].w : before[i].h)));
      const high = Math.min(...right.map((i) => (vert ? before[i].w : before[i].h) - min));
      const d = Math.max(low, Math.min(high, delta));
      left.forEach((i) => { rects[i] = { ...before[i] };
        if (vert) rects[i].w += d; else rects[i].h += d; });
      right.forEach((i) => { rects[i] = { ...before[i] };
        if (vert) { rects[i].x += d; rects[i].w -= d; }
        else { rects[i].y += d; rects[i].h -= d; } });
      paintRects();
      // The hover/drag stroke belongs to the seam. Without moving its handle,
      // the panes follow the pointer while a grey line is left behind.
      h.style[vert ? "left" : "top"] = `${(pos + d) * 100}%`;
    };
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      h.classList.remove("dragging");
      document.body.style.cursor = "";
      rectAll = { ...rectAll, [layout]: rects.map((r) => ({ ...r })) };
      Store.set(RECT_KEY, rectAll);
      mountSplitters();
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  }

  /** Point a pane at (symbol, interval) — both, in an order that survives.
   *
   *  Doing it as `s.load(iv); s.setSymbol(sym)` looks right and is not:
   *  setSymbol fires its OWN load at whatever `sub.interval` still says, and
   *  because neither call is awaited that second load races the first and
   *  usually wins. Asked for TCS on the daily, the pane opened on 1h — the
   *  ladder default for pane 1. The interval is therefore applied only once
   *  the symbol's own load has settled.
   */
  function retarget(sub, symbol, interval) {
    const done = sub.setSymbol(symbol);
    if (!interval) return done;
    return Promise.resolve(done).then(
      () => (sub.interval === interval ? undefined : sub.load(interval)));
  }

  function apply(next) {
    if (!gridEl) return;
    /* A custom grid id (gRxC) may not be in LAYOUTS yet — a reload restores it
     * from the persisted id alone, before anyone has opened the picker — so
     * rebuild it from the id before the lookup below. */
    const cm = /^g(\d+)x(\d+)$/.exec(String(next || ""));
    if (cm && !LAYOUTS[next]) ensureGrid(Number(cm[1]), Number(cm[2]));
    /* A persisted id from an older build (or a typo) must not leave the grid
     * with no template at all — that drops every pane into cell 1. */
    const id = LAYOUTS[next] ? next : (LEGACY[next] || "s1");
    const L = LAYOUTS[id];
    layout = id;
    gridEl.dataset.layout = id;
    /* The grid is DRIVEN BY THE SPEC, not by a stylesheet rule per layout.
     * Forty-two `.charts[data-layout="…"]` blocks would be forty-two chances
     * for the CSS and the pane count to disagree; this cannot disagree. */
    gridEl.style.gridTemplateAreas = L.template;
    sizeTracks(L, frOf(L));
    clearSubs();
    // the primary always holds the first area a reader meets
    if (stage) stage.style.gridArea = L.areas[0];
    for (let i = 1; i < L.panes; i++) {
      const s = makeSub(SUB_LADDER[(i - 1) % SUB_LADDER.length], Sym.name, i);
      s.root.style.gridArea = L.areas[i];
      subs.push(s);
      gridEl.appendChild(s.root);
      s.root.addEventListener("pointerdown", () => setActive(subs.indexOf(s) + 1));
      /* One frame for the grid to give it a size, then fetch. The panes are
       * staggered: an eight-pane layout firing seven concurrent /bars calls
       * makes every one of them slower than loading them in sequence would. */
      requestAnimationFrame(() => requestAnimationFrame(() => {
        setTimeout(() => s.start(), (i - 1) * 60);
      }));
    }
    const wasPrimary = active === 0;
    setActive(0);   // a new layout always hands the toolbar back to the primary
    if (wasPrimary) emitActive();
    for (const fn of onChangeSubs) { try { fn(id); } catch (e) { console.error(e); } }
  }

  const onChangeSubs = [];

  /** Wrap the existing stage in a grid without moving it in the DOM tree —
   *  main.js holds a reference to #stage and positions its overlays against
   *  it, so the stage element itself must survive untouched. */
  function init(stageEl) {
    stage = stageEl;
    stageEl.addEventListener("pointerdown", () => setActive(0));
    gridEl = document.createElement("div");
    gridEl.className = "charts";
    gridEl.id = "charts";
    gridEl.dataset.layout = "s1";
    stageEl.parentNode.insertBefore(gridEl, stageEl);
    gridEl.appendChild(stageEl);
    // delegated: apply() rebuilds the handles on every layout change, and a
    // listener per handle would leak one set per switch
    gridEl.addEventListener("pointerdown", beginDrag);
    // the handles are positioned in px off resolved track sizes, so they have
    // to be re-laid whenever the grid's own box changes
    if (window.ResizeObserver) new ResizeObserver(mountSplitters).observe(gridEl);
    Theme.onChange(() => { for (const s of subs) s.retheme(); });
  }

  return {
    init, apply, LAYOUTS, setActive,
    /** The largest custom grid the picker offers, per side. */
    CUSTOM_MAX,
    /** Build (memoised) and apply a uniform rows×cols custom grid — the drag
     *  picker's one entry point. Returns the layout id it applied. */
    applyGrid(rows, cols) { const id = ensureGrid(rows, cols); apply(id); return id; },
    /** The grid wrapper Panes builds around #stage. main.js parks the two
     *  chart-corner marks (the Pivot signature, the reset button) on it when a
     *  split is active, so they sit at the WHOLE grid's outer corners rather
     *  than trapped inside the primary pane's box. */
    gridEl() { return gridEl; },
    /** Where a secondary pane's legend sends a gear click. main.js owns the
     *  settings dialog (and the signal that follows an edit), so it hands
     *  down one opener rather than this file growing a second copy of it.
     *  The pane's OWN manager travels with the call — a gear on pane 2 must
     *  never edit pane 1's copy of the same indicator. */
    onSettings(fn) { onSettings = fn; },
    /** the catalogue in menu order, already grouped by pane count */
    groups() {
      const by = new Map();
      for (const [id] of SPECS) {
        const L = LAYOUTS[id];
        if (!by.has(L.panes)) by.set(L.panes, []);
        by.get(L.panes).push(L);
      }
      return [...by.entries()].sort((a, b) => a[0] - b[0]);
    },
    get layout() { return layout; },
    /** true when the toolbar should drive main.js's own chart. */
    get primaryActive() { return active === 0; },
    get activeInterval() { return intervalOf(active); },
    get activeSymbol() { return symbolOf(active); },
    /** The selected secondary pane, or null when the primary has it. Callers
     *  that must act on "the chart the user is working in" ask for this and
     *  fall back to their own primary — there is no null-object stand-in,
     *  because a silent no-op on the wrong chart is worse than a branch. */
    activeSub() { return active === 0 ? null : subs[active - 1] || null; },
    /** A pane BY INDEX — 0 is the primary (null: main.js owns it), 1..n the
     *  secondaries. Null for an index the current layout no longer has, so a
     *  caller holding a stale pane falls back to the primary instead of
     *  answering from a chart that is gone. */
    paneAt(i) { return i > 0 ? (subs[i - 1] || null) : null; },
    hasPane(i) { return i === 0 || !!subs[i - 1]; },
    /** What the secondary panes are showing, in screen order. main.js puts the
     *  primary at the head of this list — it is the only one that knows what
     *  the primary chart is on. */
    subsInfo() {
      return subs.map((s, i) => ({ pane: i + 1, symbol: s.symbol,
                                   interval: s.interval, bars: s.bars.length }));
    },
    /** The secondary panes, in screen order — for routing what the chat drew. */
    all() { return subs.slice(); },
    /** The indicator manager the one toolbar drives. */
    activeInd() { const s = this.activeSub(); return s ? s.ind : null; },
    /** The drawing runtime the rail should arm a tool on: null when the
     *  primary is selected (main.js drives its own), else the selected
     *  secondary pane's own runtime. */
    activeDraw() { const s = this.activeSub(); return s ? s.draw : null; },
    /** Run fn against every secondary pane's drawing runtime — used to put
     *  them all back to the cursor when the tool arms elsewhere. */
    eachSubDraw(fn) { for (const s of subs) if (s.draw) { try { fn(s.draw); } catch {} } },
    /** Resolve a paneId (from a charto:draw-select detail) to its runtime, so
     *  the edit toolbar can act on the pane the selection is actually on. */
    drawByPaneId(id) { return Drawings.byPaneId(id); },
    /** main.js hands down what "a tool finished" means, so a secondary pane
     *  hands the rail back to the cursor exactly as the primary does. */
    onSubToolDone(fn) { onSubToolDone = fn; },
    /** Route an interval choice to the selected pane. Returns false when the
     *  primary is selected, so main.js keeps ownership of its own chart. */
    setIntervalOnActive(iv) {
      if (active === 0) return false;
      const s = subs[active - 1];
      if (s) { s.load(iv); emitActive(); }   // the chat's subject moved with it
      return true;
    },
    onChange(fn) { onChangeSubs.push(fn); },
    onActive(fn) { onActiveSubs.push(fn); },

    /** Open an instrument in a pane — the chat's own hands on the workspace.
     *
     *  Growing means moving to the first catalogue layout with one more pane
     *  (2 → "c2", 3 → "c3", 4 → "g22"), because SPECS is already ordered by
     *  pane count and the first of each group is the one a person would have
     *  picked from the menu. At the catalogue's ceiling the newest chart
     *  replaces the focused pane instead of silently doing nothing.
     *
     *  `apply` rebuilds every secondary from scratch, so the symbols already
     *  on screen are captured BEFORE the rebuild and restored after it —
     *  without that, opening a third chart reset the second one to the page's
     *  own ticker.
     */
    openChart(symbol, interval, replace) {
      const sym = String(symbol || "").toUpperCase();
      if (!sym) return null;
      const iv = String(interval || "") || "1d";
      if (replace) {
        const s = this.activeSub();
        // The primary chart's symbol is the PAGE's symbol (?symbol=…), and
        // charto treats opening a company as a fresh session. Faking it in
        // place would leave the URL, the store scope and the chart disagreeing.
        if (!s) { location.search = `?symbol=${encodeURIComponent(sym)}`; return sym; }
        retarget(s, sym, iv);
        return sym;
      }
      const want = LAYOUTS[layout].panes + 1;
      const grown = (SPECS.find(([id]) => LAYOUTS[id].panes === want) || [])[0];
      if (!grown) {                       // at the ceiling: reuse the focused pane
        return this.openChart(sym, iv, true);
      }
      const keep = subs.map((s) => ({ symbol: s.symbol, interval: s.interval }));
      apply(grown);
      // Same sequencing rule as the new pane, and for the same reason: these
      // restores raced each other and swapped the two panes' intervals.
      keep.forEach((k, i) => {
        if (subs[i] && k.symbol) retarget(subs[i], k.symbol, k.interval);
      });
      const fresh = subs[subs.length - 1];
      if (fresh) {
        // Selection FIRST. setActive re-syncs the toolbar onto the pane it
        // selects, so running it after the interval load put the pane back on
        // its ladder default — INFY asked for 1h and opened on D.
        setActive(subs.length);           // the new chart is what you're now on
        retarget(fresh, sym, iv);
      }
      return sym;
    },
  };
})();
