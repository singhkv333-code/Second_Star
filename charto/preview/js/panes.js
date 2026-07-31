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
 * Secondary charts are deliberately plain: candles, volume, pan and zoom.
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
  const API = "http://127.0.0.1:5174";
  // The page's instrument is the DEFAULT a new pane opens on, not a constant:
  // this file used to hard-code RELIANCE, so a split on any other company drew
  // — and labelled — Reliance beside it. A pane can now be pointed at its own
  // instrument from its legend, and everything about it (clock, currency,
  // venue, indicator series) follows that symbol rather than the page's.
  const INTERVALS = ["1m", "5m", "15m", "30m", "1h", "D", "W", "M"];
  // the server's own vocabulary; the header's D/W/M are display labels
  const WIRE = { D: "1d", W: "1w", M: "1mo" };
  const DISP = { "1d": "D", "1w": "W", "1mo": "M" };
  const PAGE = { "1m": 3000, "5m": 2500, "15m": 2000, "30m": 2000, "1h": 2000, D: 2000, W: 700, M: 200 };

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
  const FR_MIN = 0.12;    // a pane can be squeezed, never collapsed to nothing
  let frAll = Store.get(FR_KEY, {}) || {};

  function frOf(L) {
    const saved = frAll[L.id] || {};
    const fix = (arr, n) => (Array.isArray(arr) && arr.length === n
      && arr.every((x) => Number.isFinite(x) && x > 0) ? arr.slice()
      : Array(n).fill(1));
    return { cols: fix(saved.cols, L.cols), rows: fix(saved.rows, L.rows) };
  }

  function saveFr(id, fr) {
    frAll = { ...frAll, [id]: fr };
    Store.set(FR_KEY, frAll);
  }
  let active = 0;         // 0 = primary, 1..n = subs — the pane the toolbar drives
  const subs = [];        // active secondary charts

  const CHART_FONT = 'ui-sans-serif, -apple-system, BlinkMacSystemFont, "Inter", "Segoe UI", Roboto, sans-serif';

  function chartOpts() {
    const P = Theme.palette;
    return {
      layout: {
        background: { color: P.chartBg }, textColor: P.axisText,
        fontFamily: CHART_FONT, fontSize: 11,
        panes: { separatorColor: P.separator },
      },
      grid: { vertLines: { color: P.grid }, horzLines: { color: P.grid } },
      rightPriceScale: { borderColor: P.border, scaleMargins: { top: 0.08, bottom: 0.24 } },
      timeScale: { borderColor: P.border, timeVisible: true, secondsVisible: false, rightOffset: 4 },
      crosshair: {
        mode: LWC.CrosshairMode.Normal,
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
    const res = await fetch(`${API}/bars?${qs}`);
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
  function makeSub(interval, symbol) {
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
       </div>`;
    const canvas = root.querySelector(".sub-canvas");
    const titleEl = root.querySelector(".sub-legend .title");
    const ohlcEl = root.querySelector(".sub-legend .ohlc");

    const chart = LWC.createChart(canvas, chartOpts());
    const candle = chart.addSeries(LWC.CandlestickSeries, {
      upColor: Theme.c("up"), downColor: Theme.c("down"), borderVisible: false,
      wickUpColor: Theme.c("up"), wickDownColor: Theme.c("down"),
    });
    const volume = chart.addSeries(LWC.HistogramSeries, {
      priceFormat: { type: "volume" }, priceScaleId: "vol",
      priceLineVisible: false, lastValueVisible: false,
    });
    chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.86, bottom: 0 } });

    const sub = { root, chart, candle, volume, interval, destroyed: false,
                  bars: [], symbol: (symbol || Sym.name).toUpperCase() };
    // Indicators are a property of the CHART, not of the app: the one toolbar
    // adds to whichever pane is selected, and each pane keeps what it was
    // given. Settings still live in one place per indicator, so an EMA styled
    // in one pane is the same EMA everywhere — only membership is per-pane.
    sub.ind = Indicators.createManager(chart);

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
      ohlcEl.innerHTML =
        `<span>O <b class="${cls}">${fmt(b.open)}</b></span>` +
        `<span>H <b class="${cls}">${fmt(b.high)}</b></span>` +
        `<span>L <b class="${cls}">${fmt(b.low)}</b></span>` +
        `<span>C <b class="${cls}">${fmt(b.close)}</b></span>` +
        `<span>V <b class="${cls}">${fmt(b.volume)}</b></span>`;
    }
    chart.subscribeCrosshairMove((p) => {
      if (!p || !p.time) return paintLegend(sub.bars[sub.bars.length - 1]);
      const d = p.seriesData && p.seriesData.get(candle);
      paintLegend(d ? { ...d, volume: (sub.bars.find((x) => x.time === p.time) || {}).volume || 0 }
                    : sub.bars[sub.bars.length - 1]);
    });

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
        candle.setData(bars.map(({ time, open, high, low, close }) =>
          ({ time, open, high, low, close })));
        volume.setData(bars.map(({ time, volume: v, open, close }) => ({
          time, value: v, color: close >= open ? Theme.c("volUp") : Theme.c("volDown"),
        })));
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
      } catch (e) {
        if (!sub.destroyed) titleEl.textContent = String(e.message || e);
      }
    }
    sub.load = load;

    /** Point this pane at another instrument. A cold symbol hydrates server
     *  side (~6 s), so the legend says what it is doing rather than sitting
     *  on the old company's bars while the new ones are in flight. */
    sub.setSymbol = (s) => {
      const next = String(s || "").toUpperCase();
      if (!next || next === sub.symbol) return;
      sub.symbol = next;
      titleEl.innerHTML = `${next}<span class="sep">·</span>loading…`;
      ohlcEl.innerHTML = "";
      if (subs[active - 1] === sub) emitActive();
      return load(sub.interval);
    };

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
      candle.applyOptions({
        upColor: Theme.c("up"), downColor: Theme.c("down"),
        wickUpColor: Theme.c("up"), wickDownColor: Theme.c("down"),
      });
      sub.ind.retheme(sub.bars);
      load(sub.interval);   // volume bar colours are per-point, so repaint
    };
    sub.destroy = () => {
      sub.destroyed = true;
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

  /** Write the fractions onto the grid. minmax(0,…) so a chart's own minimum
   *  width can never push a track wider than the fraction asked for. */
  function sizeTracks(L, fr) {
    gridEl.style.gridTemplateColumns =
      fr.cols.map((f) => `minmax(0, ${f}fr)`).join(" ");
    gridEl.style.gridTemplateRows =
      fr.rows.map((f) => `minmax(0, ${f}fr)`).join(" ");
    gridEl._fr = fr;
    requestAnimationFrame(mountSplitters);
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
    const cs = getComputedStyle(gridEl);
    const px = (s) => s.split(" ").map(parseFloat);
    const cols = px(cs.gridTemplateColumns);
    const rows = px(cs.gridTemplateRows);
    const gapX = parseFloat(cs.columnGap) || 0;
    const gapY = parseFloat(cs.rowGap) || 0;
    const add = (cls, style, axis, i) => {
      const d = document.createElement("div");
      d.className = `pane-split ${cls}`;
      Object.assign(d.style, style);
      d.dataset.axis = axis;
      d.dataset.i = String(i);
      gridEl.appendChild(d);
    };
    let x = 0;
    for (let i = 0; i < cols.length - 1; i++) {
      x += cols[i];
      add("v", { left: `${x + gapX / 2}px` }, "c", i);
      x += gapX;
    }
    let y = 0;
    for (let i = 0; i < rows.length - 1; i++) {
      y += rows[i];
      add("h", { top: `${y + gapY / 2}px` }, "r", i);
      y += gapY;
    }
  }

  /** Drag one boundary. Only the two tracks it sits between change, so the
   *  rest of the layout holds still instead of every pane shifting. */
  function beginDrag(e) {
    const h = e.target.closest(".pane-split");
    if (!h || !gridEl._fr) return;
    e.preventDefault();
    const axis = h.dataset.axis;
    const i = Number(h.dataset.i);
    const vert = axis === "c";
    const cs = getComputedStyle(gridEl);
    const sizes = (vert ? cs.gridTemplateColumns : cs.gridTemplateRows)
      .split(" ").map(parseFloat);
    const fr = vert ? gridEl._fr.cols : gridEl._fr.rows;
    const a0 = sizes[i], b0 = sizes[i + 1];
    const fa = fr[i], fb = fr[i + 1];
    const span = a0 + b0, sumFr = fa + fb;
    const start = vert ? e.clientX : e.clientY;
    h.classList.add("dragging");
    document.body.style.cursor = vert ? "col-resize" : "row-resize";

    const move = (ev) => {
      const d = (vert ? ev.clientX : ev.clientY) - start;
      let a = a0 + d;
      const min = span * FR_MIN;
      a = Math.max(min, Math.min(span - min, a));
      fr[i] = sumFr * (a / span);
      fr[i + 1] = sumFr - fr[i];
      sizeTracks(LAYOUTS[layout], gridEl._fr);
    };
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      h.classList.remove("dragging");
      document.body.style.cursor = "";
      saveFr(layout, gridEl._fr);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  }

  function apply(next) {
    if (!gridEl) return;
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
      const s = makeSub(SUB_LADDER[(i - 1) % SUB_LADDER.length], Sym.name);
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
    setActive(0);   // a new layout always hands the toolbar back to the primary
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
    /** The indicator manager the one toolbar drives. */
    activeInd() { const s = this.activeSub(); return s ? s.ind : null; },
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
  };
})();
