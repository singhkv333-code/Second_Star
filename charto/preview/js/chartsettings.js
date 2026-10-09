/* Charto preview — the chart settings dialog.
 *
 * TradingView's gear: one modal with a left-hand list of sections — Symbol,
 * Status line, Scales and lines, Canvas — over the same card, tokens and
 * colour picker the indicator dialog uses (js/dlgkit.js). Edits apply LIVE;
 * Cancel restores the snapshot taken when it opened; the settings persist per
 * browser, because how a chart is DRAWN is a preference, not a property of
 * the instrument you happen to be looking at.
 *
 * ── Two rules decide what is in here ────────────────────────────────────
 *
 * 1. EVERY CONTROL DOES SOMETHING. This is the same rule the indicator
 *    dialog states: a knob that silently does nothing is worse than a knob
 *    that isn't there. Four of TradingView's sections are therefore absent
 *    rather than stubbed —
 *      · Trading — nothing on this chart places or stages an order yet.
 *      · Alerts  — js/panels.js says it plainly: the alerts panel is a LOOK,
 *                  fixture rows with no engine behind them. Settings for
 *                  lines that are never drawn would be a second fiction.
 *      · Events  — no dividend / earnings / split marks are computed.
 *      · Timezone (TradingView keeps it under Symbol) — every bar in this app
 *        is shifted into the instrument's own clock at fetch time
 *        (js/store.js `Sym.tz`), and every drawing, level and scene anchor is
 *        stored against that shifted time. A timezone picker would either
 *        move the candles out from under the drawings or quietly re-anchor
 *        the user's own lines. It needs a real time model, not a select.
 *
 * 2. THE THEME IS THE DEFAULT, NOT THE VALUE. Every colour here starts null,
 *    meaning "whatever js/theme.js says for the mode you are in", so the
 *    light/dark toggle keeps working for anyone who never opened this dialog.
 *    The moment a colour is picked it becomes explicit and survives the
 *    toggle — the same contract indicator plots have (`custom: true`).
 *
 * Every chart has its own stored settings. The header controls follow the
 * selected pane, so an edit changes that chart alone.
 */
"use strict";

const ChartSettings = (() => {
  const LWC = window.LightweightCharts;
  const KEY = "chart_settings";

  /* Factory settings. `null` means "follow the theme" for a colour, and
   * "leave the chart's own value alone" for a size — the primary chart and a
   * secondary pane are deliberately built at different type and margin
   * sizes, and an untouched control must not flatten that difference. */
  const FACTORY = {
    candles: {
      // The SHAPE the price series makes — TradingView's chart-type switcher.
      // "candles" is the default and the one the drawing/scene/pin machinery is
      // anchored to; "bars" is the OHLC bar; "line" and "area" plot the close
      // only. It lives in the candles group because it is the one chart knob
      // the switcher and this dialog both edit, and so a session saved before
      // it existed reads the factory value through load()'s one-level merge.
      type: "candles",
      body: true, borders: false, wick: true,
      up: null, down: null,
      borderUp: null, borderDown: null,
      wickUp: null, wickDown: null,
      line: null,                // close-line / area stroke (follows "up" theme)
      prevClose: false,          // colour bars against the previous CLOSE
      precision: "default",
    },

    status: {
      symbol: true, ohlc: true, change: true, volume: true,
      indName: true, indValues: true, indButtons: true,
    },
    scales: {
      lastValue: true, priceLine: true, priceLineStyle: 2,
      border: true, textSize: null, dateFmt: 0, rightMargin: null,
      gridV: true, gridH: true, gridColor: null,
      crosshairColor: null, crosshairWidth: 1, crosshairStyle: 3,
    },
    canvas: {
      bg: null, gradient: false, bgBottom: null,
      separator: null, watermark: false, watermarkColor: null,
    },
  };

  /* The four shapes the price series can take. Order is TradingView's — the
   * two OHLC forms first, then the two close-only forms — and `ctor` names the
   * vendored v5 series constructor each maps to. The switcher in main.js reads
   * this same list, so the menu and the dialog can never offer a type the
   * swapper cannot build. */
  const CHART_TYPES = [
    { id: "candles", label: "Candles", icon: "ctCandles", ctor: "CandlestickSeries" },
    { id: "bars",    label: "Bars",    icon: "ctBars",    ctor: "BarSeries" },
    { id: "line",    label: "Line",    icon: "ctLine",    ctor: "LineSeries" },
    { id: "area",    label: "Area",    icon: "ctArea",    ctor: "AreaSeries" },
    // A candlestick series painted transparent: js/footprint.js draws the
    // bars and their order flow on it as a primitive, so the scale, crosshair,
    // drawings and indicators all keep their one price series.
    { id: "footprint", label: "Footprint", icon: "ctFootprint", ctor: "CandlestickSeries" },
  ];
  const TYPE = (id) => CHART_TYPES.find((t) => t.id === id) || CHART_TYPES[0];

  /* lightweight-charts' own line-style ids, named. Same three the indicator
   * dialog offers, plus the wide dash the crosshair is drawn with by
   * default — a crosshair is not a plot and reads differently. */
  const LINE_STYLES = [
    { id: 0, label: "Solid" },
    { id: 2, label: "Dashed" },
    { id: 1, label: "Dotted" },
    { id: 3, label: "Wide dashed" },
  ];

  /* Stored by INDEX, not by string: the default carries an apostrophe
   * (dd MMM 'yy) and threading that through an HTML attribute, a JSON blob
   * and back is three chances to lose it. */
  const DATE_FORMATS = [
    { fmt: "dd MMM 'yy", label: "17 Feb '26" },
    { fmt: "dd-MM-yyyy", label: "17-02-2026" },
    { fmt: "dd/MM/yyyy", label: "17/02/2026" },
    { fmt: "MM/dd/yyyy", label: "02/17/2026" },
    { fmt: "yyyy-MM-dd", label: "2026-02-17" },
  ];

  const SECTIONS = [
    { id: "symbol", label: "Symbol", icon: "candles" },
    { id: "status", label: "Status line", icon: "list" },
    { id: "scales", label: "Scales and lines", icon: "measure" },
    { id: "canvas", label: "Canvas", icon: "brush" },
  ];

  const clone = (v) => JSON.parse(JSON.stringify(v));
  const esc = (s) => String(s).replace(/[&<>"]/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  /** Stored settings merged onto the factory one level deep, so a release
   *  that adds a knob does not read `undefined` out of a session saved
   *  before it existed. */
  function load() {
    const saved = Store.get(KEY, {}) || {};
    const out = clone(FACTORY);
    for (const g of Object.keys(out)) Object.assign(out[g], saved[g] || {});
    return out;
  }

  let cfg = load();
  const PANE_KEY = "chart_settings_panes";
  const paneSaved = Store.get(PANE_KEY, {}) || {};
  const paneCfg = new Map([[0, cfg]]);
  let selected = 0;
  function configFor(slot) {
    if (slot === 0) return paneCfg.get(0);
    if (!paneCfg.has(slot)) {
      const next = clone(FACTORY);
      const saved = paneSaved[slot] || {};
      for (const g of Object.keys(next)) Object.assign(next[g], saved[g] || {});
      paneCfg.set(slot, next);
    }
    return paneCfg.get(slot);
  }
  function withTarget(t, fn) {
    const before = cfg;
    cfg = configFor(t.slot || 0);
    try { return fn(); } finally { cfg = before; }
  }
  const targets = new Set();     // registered charts on screen
  const subs = [];               // things that repaint when settings change

  // ── the effective values ────────────────────────────────
  // One reader per colour, theme underneath. Called at APPLY time, never
  // captured, for the same reason js/theme.js gives: a value read once goes
  // stale the moment the user toggles the mode.
  const upC = () => cfg.candles.up || Theme.c("up");
  const downC = () => cfg.candles.down || Theme.c("down");
  // The close-line's colour, for the line and area types. It has no "down" —
  // a single polyline is one colour — so it defaults to the up/accent, the
  // same green the rest of the chart treats as neutral-positive.
  const lineC = () => cfg.candles.line || upC();
  const clear = "rgba(0,0,0,0)";

  const eff = () => ({
    up: upC(), down: downC(), line: lineC(),
    borderUp: cfg.candles.borderUp || upC(),
    borderDown: cfg.candles.borderDown || downC(),
    wickUp: cfg.candles.wickUp || upC(),
    wickDown: cfg.candles.wickDown || downC(),
    grid: cfg.scales.gridColor || Theme.c("grid"),
    cross: cfg.scales.crosshairColor || Theme.c("crosshair"),
    bg: cfg.canvas.bg || Theme.c("chartBg"),
    bgBottom: cfg.canvas.bgBottom || cfg.canvas.bg || Theme.c("chartBg"),
    sep: cfg.canvas.separator || Theme.c("separator"),
    mark: cfg.canvas.watermarkColor
      || DlgKit.withAlpha(Theme.c("axisText"), 0.09),
  });

  /** What one swatch button should be showing — the explicit colour if there
   *  is one, otherwise the theme's, so an untouched control still displays
   *  the colour it controls rather than an empty chip. */
  function swatchValue(path) {
    const E = eff();
    const MAP = {
      "candles.up": E.up, "candles.down": E.down, "candles.line": E.line,
      "candles.borderUp": E.borderUp, "candles.borderDown": E.borderDown,
      "candles.wickUp": E.wickUp, "candles.wickDown": E.wickDown,
      "scales.gridColor": E.grid, "scales.crosshairColor": E.cross,
      "canvas.bg": E.bg, "canvas.bgBottom": E.bgBottom,
      "canvas.separator": E.sep, "canvas.watermarkColor": E.mark,
    };
    return MAP[path] || "#ffffff";
  }

  // ── what the charts are told ────────────────────────────
  /* Two of these knobs have no single default: the primary chart is built at
   * 12px type with a 5-bar right margin and a secondary pane at 11px and 4,
   * deliberately. So "Default" cannot mean "send nothing" — that would leave
   * the last explicit value on the chart, a control that stops working the
   * second time you use it. It means "send the value THIS chart was built
   * with", which each owner declares when it registers. */
  const DEFAULTS = { fontSize: 12, rightOffset: 5 };

  function chartOptions(t) {
    const E = eff();
    const s = cfg.scales;
    const own = (t && t.defaults) || DEFAULTS;
    const layout = {
      background: cfg.canvas.gradient
        ? { type: "gradient", topColor: E.bg, bottomColor: E.bgBottom }
        : { type: "solid", color: E.bg },
      panes: { separatorColor: E.sep, separatorHoverColor: Theme.c("separatorHover") },
      fontSize: s.textSize || own.fontSize,
    };
    const timeScale = {
      borderVisible: s.border,
      rightOffset: s.rightMargin == null ? own.rightOffset : s.rightMargin,
    };
    return {
      layout,
      grid: {
        vertLines: { visible: s.gridV, color: E.grid },
        horzLines: { visible: s.gridH, color: E.grid },
      },
      crosshair: {
        vertLine: { color: E.cross, width: s.crosshairWidth, style: s.crosshairStyle },
        horzLine: { color: E.cross, width: s.crosshairWidth, style: s.crosshairStyle },
      },
      rightPriceScale: { borderVisible: s.border },
      timeScale,
      localization: { dateFormat: (DATE_FORMATS[s.dateFmt] || DATE_FORMATS[0]).fmt },
    };
  }

  let symDec = 2;
  /* The three options every price series shares, whatever its shape: the last
   * value label, the dashed price line to it, and the precision the axis reads
   * prices at. Split out so the four option builders below cannot drift on the
   * things that are NOT about the shape. */
  function commonSeriesOptions() {
    const s = cfg.scales, p = cfg.candles.precision;
    return {
      priceLineVisible: !!s.priceLine,
      priceLineStyle: s.priceLineStyle,
      lastValueVisible: !!s.lastValue,
      // "default" is the instrument's own tick: 4 places for an INR pair,
      // none for gold quoted in whole rupees (setSymbolDecimals, from the
      // instrument master). 2 when the master has no answer.
      priceFormat: p === "default"
        ? { type: "price", precision: symDec, minMove: Math.pow(10, -symDec) }
        : { type: "price", precision: p, minMove: 1 / Math.pow(10, p) },
    };
  }

  function candleOptions() {
    const E = eff(), c = cfg.candles;
    return {
      ...commonSeriesOptions(),
      // Body OFF is TradingView's hollow candle: the fill goes, the border
      // and the wick stay. It is a transparent colour rather than a flag
      // because lightweight-charts has no hollow mode.
      upColor: c.body ? E.up : clear,
      downColor: c.body ? E.down : clear,
      borderVisible: !!c.borders,
      borderUpColor: E.borderUp, borderDownColor: E.borderDown,
      wickVisible: !!c.wick,
      wickUpColor: E.wickUp, wickDownColor: E.wickDown,
    };
  }

  /* The OHLC bar. It carries no body, so the only colours it has are the two
   * directional ones — the same up/down the candle uses, so a flip between the
   * two OHLC shapes never changes what green means. */
  function barOptions() {
    const E = eff();
    return {
      ...commonSeriesOptions(),
      upColor: E.up, downColor: E.down,
      // openVisible OFF would make it a close-only bar (a "HLC" bar); TradingView
      // shows the open tick, so we do too.
      openVisible: true, thinBars: false,
    };
  }

  function lineOptions() {
    const E = eff();
    return {
      ...commonSeriesOptions(),
      color: E.line, lineWidth: 2,
    };
  }

  /* Area is the line plus a fade to the background. The fill is the line colour
   * at two alphas so it reads as the same series dressed, not a second colour —
   * DlgKit.withAlpha is the one the rest of this module tints through. */
  function areaOptions() {
    const E = eff();
    return {
      ...commonSeriesOptions(),
      lineColor: E.line, lineWidth: 2,
      topColor: DlgKit.withAlpha(E.line, 0.30),
      bottomColor: DlgKit.withAlpha(E.line, 0.02),
    };
  }

  /** The options for whichever shape is current. The swapper and applyOne both
   *  read through here so there is one answer to "how is this series dressed". */
  function seriesOptions(type = cfg.candles.type) {
    if (type === "bars") return barOptions();
    if (type === "line") return lineOptions();
    if (type === "area") return areaOptions();
    if (type === "footprint") {
      return { ...candleOptions(), upColor: clear, downColor: clear,
               borderVisible: false, wickVisible: false };
    }
    return candleOptions();
  }

  /** The vendored v5 constructor + initial options for a shape — what an owner
   *  (main.js, panes.js) builds its price series from, and what the swapper
   *  rebuilds it from. Centralised so the four shapes are declared once. */
  function seriesSpec(type = cfg.candles.type) {
    return { ctor: LWC[TYPE(type).ctor], options: seriesOptions(type) };
  }

  /* ── per-bar colour ──────────────────────────────────────────────────────
   * "Colour bars based on previous close" cannot be a series option: it is a
   * statement about each bar's relationship to the one before it, so the
   * colour has to ride on the POINT. Both chart owners build their candle
   * data through these, and the volume indicator asks `up()` the same
   * question through the exported barUp() — one rule, so the strip and the
   * candles above it can never disagree about which way a bar went. */
  function up(bar, prev) {
    return cfg.candles.prevClose && prev
      ? bar.close >= prev.close
      : bar.close >= bar.open;
  }

  function candlePoints(bars) {
    const E = eff();
    return bars.map((b, i) => {
      const pt = { time: b.time, open: b.open, high: b.high, low: b.low, close: b.close };
      if (!cfg.candles.prevClose) return pt;
      const isUp = up(b, i > 0 ? bars[i - 1] : null);
      pt.color = cfg.candles.body ? (isUp ? E.up : E.down) : clear;
      pt.borderColor = isUp ? E.borderUp : E.borderDown;
      pt.wickColor = isUp ? E.wickUp : E.wickDown;
      return pt;
    });
  }

  /** The live edge: one bar, and the bar before it for the previous-close
   *  rule. */
  function candlePoint(bar, prev) {
    const E = eff();
    const pt = { time: bar.time, open: bar.open, high: bar.high, low: bar.low, close: bar.close };
    if (!cfg.candles.prevClose) return pt;
    const isUp = up(bar, prev);
    pt.color = cfg.candles.body ? (isUp ? E.up : E.down) : clear;
    pt.borderColor = isUp ? E.borderUp : E.borderDown;
    pt.wickColor = isUp ? E.wickUp : E.wickDown;
    return pt;
  }

  /* ── what the ACTIVE series is fed ────────────────────────────────────────
   * The two chart owners set/update their price series through these, not
   * through candlePoints directly, so the data shape always matches the chosen
   * series: OHLC for candles and bars, {time,value:close} for line and area.
   * A line series handed a candle point renders nothing — this is the one
   * join that makes a type switch a one-line change in the owner. */
  function pricePoints(bars, type = cfg.candles.type) {
    if (type === "line" || type === "area") {
      return bars.map((b) => ({ time: b.time, value: b.close }));
    }
    if (type === "footprint") {          // plain OHLC: per-point colours would paint over it
      return bars.map((b) => ({ time: b.time, open: b.open, high: b.high, low: b.low, close: b.close }));
    }
    return candlePoints(bars);           // candles + bars are both OHLC
  }

  function pricePoint(bar, prev, type = cfg.candles.type) {
    if (type === "line" || type === "area") {
      return { time: bar.time, value: bar.close };
    }
    if (type === "footprint") {
      return { time: bar.time, open: bar.open, high: bar.high, low: bar.low, close: bar.close };
    }
    return candlePoint(bar, prev);
  }


  // ── the status line ─────────────────────────────────────
  /* Legend visibility is scoped to each pane root. */
  const SL = [
    ["symbol", "sl-no-symbol"], ["ohlc", "sl-no-ohlc"],
    ["change", "sl-no-change"], ["volume", "sl-no-volume"],
    ["indName", "sl-no-indname"], ["indValues", "sl-no-indvals"],
    ["indButtons", "sl-no-indbtns"],
  ];

  function applyStatus(t) {
    const root = t.root;
    if (!root) return;
    for (const [key, cls] of SL) root.classList.toggle(cls, !cfg.status[key]);
    // The OHLC figures are coloured by direction, and TradingView colours
    // them with the CANDLE's colours — so a blue/orange chart does not keep
    // a green/red status line. Alpha is dropped: a 30%-opacity candle is a
    // deliberate choice on canvas and unreadable as 11px text.
    const E = eff();
    root.style.setProperty("--candle-up", DlgKit.hexOf(E.up));
    root.style.setProperty("--candle-down", DlgKit.hexOf(E.down));
  }

  // ── applying to the charts ──────────────────────────────
  /* A target is one chart and its ONE built-in series:
   *   { chart, candle, defaults, label(), repaint() }
   * (volume used to be the second; it is an indicator now and belongs to the
   * indicator manager, which paints it through barUp() below so the strip and
   * the candles can still never disagree about direction.)
   * `repaint` re-sets the series data through candlePoints —
   * cheap, local, and never a refetch. It is called only when a change can
   * move a per-point colour, because setData on four thousand bars for a
   * grid-colour edit would be work nobody asked for. */
  function applyOne(t, repaint) {
    return withTarget(t, () => {
    try {
      t.chart.applyOptions(chartOptions(t));
      // The ACTIVE shape's options, not the candle's — applyOptions on a line
      // series with candle keys is a no-op at best and a console warning at
      // worst, and this same call runs for every type.
      t.candle.applyOptions(seriesOptions());
      if (repaint && t.repaint) t.repaint();
    } catch (e) {
      // A pane is unregistered when it is destroyed (js/panes.js), so
      // anything landing here is a real fault and says so — dropping the
      // target silently would leave one chart out of the next edit.
      console.error("chart settings:", e);
    }
    applyWatermark(t);
    applyStatus(t);
    });
  }

  /* ── switching the shape ───────────────────────────────────────────────────
   * The series TYPE is fixed when a series is created, so changing it means
   * tearing the old series down and building a new one on that chart.
   * Drawings, the scene and the
   * pins read the price series LIVE through the owner's panesList(), so the only
   * refs that must be re-pointed are the owner's own `candle` and anything it
   * bound directly to the series (scene's marker layer). Each owner states how
   * through `rebind(newSeries)`; an owner that never swaps (none today) simply
   * would not provide one.
   *
   * The watermark is a PANE primitive, not a series one, so it survives the
   * swap untouched — applyWatermark is not called here. */
  function swapSeriesOn(t, type) {
    const spec = seriesSpec(type);
    const next = t.chart.addSeries(spec.ctor, spec.options);
    try { t.chart.removeSeries(t.candle); } catch (e) { console.error("chart type · remove:", e); }
    t.candle = next;
    if (t.rebind) t.rebind(next);
    if (t.repaint) t.repaint();          // fill the fresh series with its data
  }

  /** Change the selected chart's type and persist it. A no-op
   *  when the type is already current, so the switcher can call it freely. */
  function setType(type) {
    if (!TYPE(type) || type === cfg.candles.type) return;
    cfg.candles.type = type;
    save();
    for (const t of [...targets].filter((t) => (t.slot || 0) === selected)) {
      try { swapSeriesOn(t, type); } catch (e) { console.error("chart type:", e); }
    }
    // A colour-only sub won't have moved, but the type select in the dialog and
    // the header pill both read getType(), so let subscribers re-dress.
    for (const fn of subs) { try { fn(cfg); } catch (e) { console.error(e); } }
  }

  /* The instrument's own name behind its candles — TradingView's watermark.
   * Guarded by a capability check rather than a version assumption: the text
   * watermark is a v5 pane primitive, and if the bundled library ever lacks
   * it the control disappears instead of throwing. */
  const HAS_WATERMARK = !!(LWC && LWC.createTextWatermark);

  function applyWatermark(t) {
    if (!HAS_WATERMARK) return;
    const on = !!cfg.canvas.watermark;
    try {
      if (!on) {
        if (t._wm) { t._wm.detach(); t._wm = null; }
        return;
      }
      const lines = [{
        text: t.label ? t.label() : "",
        color: eff().mark,
        fontSize: 64,
        fontStyle: "600",
      }];
      if (t._wm) { t._wm.applyOptions({ lines }); return; }
      t._wm = LWC.createTextWatermark(t.chart.panes()[0], {
        horzAlign: "center", vertAlign: "center", lines,
      });
    } catch (e) {
      console.error("chart settings · watermark:", e);
      t._wm = null;
    }
  }

  /** Re-assert every setting on every chart. The two chart owners call this
   *  after a theme change — the theme rewrites the same options this module
   *  owns, so the user's explicit colours have to land last. */
  function apply(opts = {}) {
    const repaint = opts.repaint !== false;
    for (const t of [...targets]) applyOne(t, repaint);
    for (const fn of subs) { try { fn(cfg); } catch (e) { console.error(e); } }
  }

  function register(t) {
    targets.add(t);
    applyOne(t, true);
    return () => unregister(t);
  }
  function unregister(t) {
    if (t && t._wm) { try { t._wm.detach(); } catch {} t._wm = null; }
    targets.delete(t);
  }

  function save() {
    if (selected === 0) Store.set(KEY, cfg);
    else {
      paneSaved[selected] = cfg;
      Store.set(PANE_KEY, paneSaved);
    }
  }


  function selectPane(slot) {
    selected = Math.max(0, Number(slot) || 0);
    cfg = configFor(selected);
    for (const fn of subs) { try { fn(cfg); } catch (e) { console.error(e); } }
  }

  // ══ the dialog ═══════════════════════════════════════════
  let wrap = null, dlg = null, card = null;
  let section = "symbol";
  let snapshot = null;              // what Cancel restores

  const row = (label, control, cls = "") =>
    `<div class="dlg-row ${cls}"><label>${esc(label)}</label>${control}</div>`;

  const check = (path, label) =>
    `<label class="dlg-check"><input type="checkbox" data-k="${path}"` +
    `${get(path) ? " checked" : ""}><span>${esc(label)}</span></label>`;

  /** A checkbox that names the row, with controls of its own beside it. */
  const checkRow = (path, label, ctl = "") =>
    `<div class="dlg-row">${check(path, label)}` +
    (ctl ? `<div class="dlg-ctl">${ctl}</div>` : "") + `</div>`;

  const swatch = (path, title) =>
    `<button type="button" class="dlg-swatch" title="${esc(title)}" ` +
    `data-sw="${path}" style="--sw:${swatchValue(path)}"><span></span></button>`;

  const group = (label) => `<div class="dlg-group">${esc(label)}</div>`;

  function select(path, options, cls = "") {
    // null is a real value here — "leave the chart's own size alone" — and
    // its option carries an empty value, not the word "null"
    const v = get(path);
    const cur = v == null ? "" : String(v);
    return `<select class="dlg-select ${cls}" data-k="${path}">` +
      options.map((o) =>
        `<option value="${esc(o.value)}"${String(o.value) === cur ? " selected" : ""}>` +
        `${esc(o.label)}</option>`).join("") + `</select>`;
  }

  const styleOpts = LINE_STYLES.map((o) => ({ value: o.id, label: o.label }));

  const typeOpts = CHART_TYPES.map((t) => ({ value: t.id, label: t.label }));

  function symbolHTML() {
    const type = cfg.candles.type;
    const ohlc = type === "candles" || type === "bars";
    // The OHLC shapes (candles, bars) expose up/down bodies and wicks; the
    // close-only shapes (line, area) expose a single stroke colour. Showing the
    // irrelevant set would be the "control that does nothing" this dialog's own
    // header forbids, so the body switches with the type — same rule as the
    // gradient's second swatch below.
    const shapeRows = ohlc
      ? group(type === "bars" ? "Bars" : "Candles") +
        checkRow("candles.body", "Body",
                 swatch("candles.up", "Up") + swatch("candles.down", "Down")) +
        (type === "candles"
          ? checkRow("candles.borders", "Borders",
                     swatch("candles.borderUp", "Up") + swatch("candles.borderDown", "Down")) +
            checkRow("candles.wick", "Wick",
                     swatch("candles.wickUp", "Up") + swatch("candles.wickDown", "Down"))
          : "") +
        checkRow("candles.prevClose", "Colour bars based on previous close") +
        `<p class="dlg-note">A bar is green when it closed above the PREVIOUS
          bar's close rather than above its own open — the same rule the change
          figure in the legend already uses.</p>`
      : group(type === "area" ? "Area" : "Line") +
        row("Colour", swatch("candles.line", "Line")) +
        `<p class="dlg-note">Line and area plot the CLOSE of each bar. The
          open/high/low a candle shows are not drawn, so the per-bar colouring
          rule above does not apply here.</p>`;

    return group("Type") +
      row("Chart type", select("candles.type", typeOpts)) +
      shapeRows +
      /* No "Volume" group here any more. Volume became an ordinary indicator
       * (Indicators ▸ Volume), so its colours, its MA and its on/off live in
       * that indicator's own settings dialog — which is where TradingView
       * keeps them too, and the only place they can sit now that a chart can
       * carry the study more than once or not at all.
       *
       * The status line's V figure is NOT this and stays below: that is the
       * raw bar readout, which exists whether or not the study is on. */
      group("Data modification") +
      row("Precision", select("candles.precision",
        [{ value: "default", label: "Default" },
         ...[0, 1, 2, 3, 4, 5, 6, 7, 8].map((n) => ({ value: n, label: String(n) }))]));
  }

  function statusHTML() {
    return `<p class="dlg-note">Choose what this chart's legend shows.
      Other charts keep their own settings.</p>` +
      group("Symbol") +
      checkRow("status.symbol", "Ticker, interval and exchange") +
      checkRow("status.ohlc", "OHLC values") +
      checkRow("status.change", "Bar change values") +
      checkRow("status.volume", "Volume") +
      group("Indicators") +
      checkRow("status.indName", "Indicator names") +
      checkRow("status.indValues", "Indicator values") +
      checkRow("status.indButtons", "Indicator controls");
  }

  function scalesHTML() {
    return group("Price scale") +
      checkRow("scales.lastValue", "Symbol last value label") +
      checkRow("scales.priceLine", "Price line",
               select("scales.priceLineStyle", styleOpts)) +
      checkRow("scales.border", "Scale borders") +
      row("Scale text size", select("scales.textSize",
        [{ value: "", label: "Default" },
         ...[10, 11, 12, 13, 14, 16].map((n) => ({ value: n, label: `${n}px` }))], "tight")) +
      row("Date format", select("scales.dateFmt",
        DATE_FORMATS.map((d, i) => ({ value: i, label: d.label })))) +
      row("Right margin",
        `<div class="dlg-ctl"><input class="dlg-input" type="number"
           data-k="scales.rightMargin"
           value="${get("scales.rightMargin") == null ? "" : get("scales.rightMargin")}"
           min="0" max="60" step="1" placeholder="Default">` +
        `<span class="dlg-hint">bars</span></div>`) +
      group("Grid lines") +
      checkRow("scales.gridV", "Vertical", swatch("scales.gridColor", "Grid")) +
      checkRow("scales.gridH", "Horizontal", swatch("scales.gridColor", "Grid")) +
      group("Crosshair") +
      row("Line", `<div class="dlg-ctl">` + swatch("scales.crosshairColor", "Crosshair") +
        select("scales.crosshairWidth",
               [1, 2, 3, 4].map((w) => ({ value: w, label: `${w}px` })), "tight") +
        select("scales.crosshairStyle", styleOpts) + `</div>`);
  }

  function canvasHTML() {
    return group("Background") +
      row("Colour", `<div class="dlg-ctl">` + swatch("canvas.bg", "Background") +
        (cfg.canvas.gradient ? swatch("canvas.bgBottom", "Bottom") : "") + `</div>`) +
      checkRow("canvas.gradient", "Vertical gradient") +
      group("Panes") +
      row("Separator", swatch("canvas.separator", "Separator")) +
      `<p class="dlg-note">The line between the price pane and an oscillator's
        own pane, and the handle that resizes them.</p>` +
      (HAS_WATERMARK
        ? group("Watermark") +
          checkRow("canvas.watermark", "Symbol behind the candles",
                   swatch("canvas.watermarkColor", "Watermark"))
        : "");
  }

  function bodyHTML() {
    if (section === "status") return statusHTML();
    if (section === "scales") return scalesHTML();
    if (section === "canvas") return canvasHTML();
    return symbolHTML();
  }

  function render() {
    dlg.querySelectorAll(".dlg-nav").forEach((b) =>
      b.classList.toggle("active", b.dataset.sec === section));
    const body = dlg.querySelector(".dlg-body");
    body.innerHTML = bodyHTML();
    DlgKit.dressSelects(body);
    if (card.pinned()) card.reclamp();
  }

  // ── reading and writing one control ─────────────────────
  function get(path) {
    const [g, k] = path.split(".");
    return cfg[g][k];
  }
  function set(path, value) {
    const [g, k] = path.split(".");
    cfg[g][k] = value;
    save();
    // Only a colour or the previous-close rule can change a per-POINT
    // colour; everything else is a series or chart option.
    // Only a candle colour or the previous-close rule can move a per-POINT
    // colour. The volume strip repaints too, but through the indicator
    // manager's retheme path, not from here.
    apply({ repaint: g === "candles" });
  }

  /* Selects and numbers arrive as strings. Each control that is not a plain
   * string says here what it means — an empty size is "leave the chart's own
   * alone" (null), not zero, and Precision's "default" is a word the price
   * formatter understands rather than a number. */
  const COERCE = {
    "candles.precision": (v) => (v === "default" ? "default" : Number(v)),
    "scales.priceLineStyle": Number,
    "scales.crosshairWidth": Number,
    "scales.crosshairStyle": Number,
    "scales.dateFmt": Number,
    "scales.textSize": (v) => (v === "" ? null : Number(v)),
    "scales.rightMargin": (v) => (v === "" ? null : Math.max(0, Math.min(60, Number(v)))),
  };

  function onChange(e) {
    const t = e.target;
    const path = t.dataset.k;
    if (!path) return;
    if (t.type === "checkbox") {
      set(path, t.checked);
      // Two checkboxes change what the panel itself should offer: a gradient
      // needs a second swatch, and the previous-close rule changes nothing
      // structural but the note under it stays put.
      if (path === "canvas.gradient") render();
      return;
    }
    // The chart type is the series OBJECT, not an option — route it through the
    // swapper, not set(), then re-render because the shape's own controls
    // (candle bodies vs. a single line colour) change with it.
    if (path === "candles.type") { setType(t.value); render(); return; }
    const fn = COERCE[path];
    set(path, fn ? fn(t.value) : t.value);
  }

  function onClick(e) {
    e.stopPropagation();
    const sw = e.target.closest("[data-sw]");
    if (sw) {
      const path = sw.dataset.sw;
      DlgKit.openSwatch(sw, {
        value: swatchValue(path),
        onPick: (v) => {
          set(path, v);
          // the grid's two rows share one colour; keep both chips honest
          dlg.querySelectorAll(`[data-sw="${path}"]`).forEach((b) =>
            b.style.setProperty("--sw", v));
        },
      });
      return;
    }
    DlgKit.closeSwatch();

    const nav = e.target.closest(".dlg-nav");
    if (nav) { section = nav.dataset.sec; render(); return; }

    const act = e.target.closest("[data-act]");
    if (!act) return;
    if (act.dataset.act === "reset") {
      restoreCfg(clone(FACTORY));
      render();
    } else if (act.dataset.act === "cancel") cancel();
    else close();                      // ok / ×: the edits are already live
  }

  /* Swap this pane's config and bring its chart back in line with it. The chart
   * TYPE is not an option — it is the series object — so a restore that changes
   * it has to rebuild the series, which apply() does not do. Rebuild first (off
   * the new type), then apply the rest of the options onto the fresh series. */
  function restoreCfg(next) {
    const wantType = next.candles.type;
    const typeChanged = wantType !== cfg.candles.type;
    cfg = next;
    paneCfg.set(selected, next);
    save();
    if (typeChanged) {
      for (const t of [...targets].filter((t) => (t.slot || 0) === selected)) {
        try { swapSeriesOn(t, wantType); } catch (e) { console.error("chart type:", e); }
      }
    }
    apply();
  }

  function cancel() {
    restoreCfg(clone(snapshot));
    close();
  }

  function build() {
    wrap = document.createElement("div");
    wrap.className = "dlg-wrap";
    wrap.innerHTML = `
      <div class="dlg settings" role="dialog" aria-modal="true"
           aria-label="Chart settings">
        <header class="dlg-head">
          <div class="dlg-title">Settings</div>
          <button class="btn icon" data-act="close" title="Close"></button>
        </header>
        <div class="dlg-cols">
          <nav class="dlg-side">${SECTIONS.map((s) =>
            `<button type="button" class="dlg-nav" data-sec="${s.id}">` +
            `${Icons.svg(s.icon, "sm")}<span>${s.label}</span></button>`).join("")}
          </nav>
          <div class="dlg-body"></div>
        </div>
        <footer class="dlg-foot">
          <button class="btn outline" data-act="reset">Reset settings</button>
          <span class="spacer"></span>
          <button class="btn outline" data-act="cancel">Cancel</button>
          <button class="btn cta" data-act="ok">Ok</button>
        </footer>
      </div>`;
    document.body.appendChild(wrap);
    dlg = wrap.querySelector(".dlg");
    dlg.querySelector('[data-act="close"]').innerHTML = Icons.svg("x", "sm");
    dlg.addEventListener("click", onClick);
    dlg.addEventListener("change", onChange);
    card = DlgKit.draggable(dlg, dlg.querySelector(".dlg-head"));
    // clicking the dimmed backdrop is Cancel, not a silent commit
    wrap.addEventListener("pointerdown", (e) => { if (e.target === wrap) cancel(); });
    addEventListener("keydown", (e) => {
      if (!wrap || !wrap.classList.contains("open")) return;
      if (e.key === "Escape") { e.stopPropagation(); cancel(); }
    }, true);
  }

  function open(slot = selected) {
    selectPane(slot);
    if (!wrap) build();
    dlg.querySelector(".dlg-title").textContent =
      selected === 0 ? "Chart settings · Main chart" : `Chart settings · Chart ${selected + 1}`;
    snapshot = clone(cfg);
    section = "symbol";
    card.centre();
    render();
    wrap.classList.add("open");        // it must be laid out to have a size
    card.pinCentred();
  }

  function close() {
    DlgKit.closePopovers();
    if (wrap) wrap.classList.remove("open");
  }

  return {
    register, unregister, apply,
    selectPane(slot) { selectPane(slot); },
    withTarget,
    /** The chart's instrument quotes to `n` decimals; re-applies the candles
     *  only when that changes what "default" precision means. */
    setSymbolDecimals(n) {
      if (!Number.isFinite(n) || n === symDec) return;
      symDec = Math.max(0, Math.min(6, n));
      apply();
    },
    /** Re-assert every setting on ONE chart — for an owner that has just
     *  written the theme's palette over the user's on its own chart. */
    applyTo: (t) => applyOne(t, true),
    candlePoints, candlePoint,
    /** Build a price series of the CURRENT type on a chart. Owners call this
     *  instead of chart.addSeries(CandlestickSeries,…) so a restored "line"
     *  preference is honoured from the first paint, and so there is one place
     *  that knows how the four shapes are constructed. */
    makeSeries: (chart) => {
      const spec = seriesSpec();
      return chart.addSeries(spec.ctor, spec.options);
    },
    /** The data for the active series' shape — OHLC for candles/bars, close for
     *  line/area. Owners set/update through these, never candlePoints directly,
     *  so a type switch needs no change at the call site. */
    pricePoints, pricePoint,
    /** The chart-type catalogue + current value, for the header switcher. */
    chartTypes: () => CHART_TYPES.slice(),
    getType: () => cfg.candles.type,
    setType,
    /** "Which way did this bar go" — the one rule, so the candles and the
     *  volume indicator's bars can never disagree. Honours "colour bars based
     *  on previous close"; the volume study reads it through here rather than
     *  deciding again. */
    barUp: (bar, prev) => up(bar, prev),
    onChange(fn) { subs.push(fn); },
    open, close,
    isOpen: () => !!(wrap && wrap.classList.contains("open")),
  };
})();
