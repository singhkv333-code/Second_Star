/* Charto preview — indicators.
 *
 * The math is NOT here. Every series is computed once, in `data/indicators.py`,
 * and fetched. The previous version reimplemented sma/ema/bollinger/vwap/rsi/
 * macd/atr in JavaScript alongside the Python copies, and the two had already
 * drifted: the backend could not compute Bollinger or VWAP at all, so the model
 * told users "the chart does not support Bollinger Bands" while the menu had
 * them one click away, and an RSI warmup bug existed on one side only. One
 * implementation removes the whole class.
 *
 * What stays here is presentation: which pane a line belongs in, what colour a
 * ROLE gets, how the legend reads, and — since the settings dialog landed —
 * the per-indicator STYLE the user has chosen. Structure comes from the backend
 * catalogue, styling is decided locally; the same split geometry.js and
 * tools.js already use.
 *
 * The settings model, in one place so the dialog stays dumb:
 *
 *   params      the indicator's own inputs (source, stddev, fast/slow/signal…).
 *               These change the MATH, so every one of them is a key the
 *               backend's `inputs(name)` schema declared — the dialog cannot
 *               invent a knob, and a knob added to a Python function reaches
 *               the chart with no edit here. `period` is deliberately NOT in
 *               here: it is part of the catalogue id, so re-lengthing goes
 *               through setPeriod() and keeps the id/scene/chat contract.
 *   style       colour, thickness, line style, plot type, precision, and what
 *               appears in the status line / on the price scale. Pure display.
 *   visibility  which interval buckets the indicator draws on, TradingView's
 *               Visibility tab. Also pure display.
 *   hidden      the eye on the chip. Distinct from visibility so unhiding
 *               restores exactly what the user had configured.
 */
"use strict";

const Indicators = (() => {
  const LWC = window.LightweightCharts;
  // (the module-level number formatter that used to live here is gone: the
  // legend now builds one per indicator via formatter(st), which honours the
  // settings dialog's precision as well as the symbol's locale)

  // ── catalogue, loaded from the backend ────────────────
  // Presets are the friendly names on the menu; any indicator/period the
  // backend knows can still be requested by id, so the menu is a convenience
  // rather than the limit of what exists. `base` is the label without its
  // numbers — the numbers are re-rendered from the live params, so a chip
  // reading "BOLL 20 2" still reads truthfully after the StdDev is changed.
  const PRESETS = [
    { id: "sma20", name: "sma", period: 20, base: "SMA" },
    { id: "sma50", name: "sma", period: 50, base: "SMA" },
    { id: "sma200", name: "sma", period: 200, base: "SMA" },
    { id: "ema21", name: "ema", period: 21, base: "EMA" },
    { id: "bbands", name: "bbands", period: 20, base: "BOLL" },
    { id: "keltner", name: "keltner", period: 20, base: "Keltner" },
    { id: "donchian", name: "donchian", period: 20, base: "Donchian" },
    { id: "supertrend", name: "supertrend", period: 10, base: "Supertrend" },
    { id: "psar", name: "psar", period: 0, base: "Parabolic SAR" },
    { id: "vwap", name: "vwap", period: 0, base: "VWAP session", intradayOnly: true },
    { id: "rsi", name: "rsi", period: 14, base: "RSI" },
    { id: "macd", name: "macd", period: 0, base: "MACD" },
    { id: "stoch", name: "stoch", period: 14, base: "Stoch" },
    { id: "stochrsi", name: "stochrsi", period: 14, base: "Stoch RSI" },
    { id: "adx", name: "adx", period: 14, base: "ADX" },
    { id: "atr", name: "atr", period: 14, base: "ATR" },
    { id: "cci", name: "cci", period: 20, base: "CCI" },
    { id: "williams_r", name: "williams_r", period: 14, base: "Williams %R" },
    { id: "mfi", name: "mfi", period: 14, base: "MFI" },
    { id: "obv", name: "obv", period: 0, base: "OBV" },
    { id: "cmf", name: "cmf", period: 20, base: "CMF" },
    { id: "aroon", name: "aroon", period: 14, base: "Aroon" },
  ];

  let CATALOG = [];          // presets joined with what the backend reports
  let KNOWN = {};            // the backend's own catalogue, kept for minting
  let BASE = "";
  let SYM = "";              // without it every series computes on the default symbol

  // ── labels ────────────────────────────────────────────
  const trimNum = (v) => {
    const n = Number(v);
    if (!Number.isFinite(n)) return String(v);
    return String(Number(n.toFixed(4)));   // 2.0 -> "2", 0.0200 -> "0.02"
  };

  /** "BOLL 20 2" — the base plus every NUMERIC input, in schema order. The
   *  dropdowns (Basis MA Type, Bands Style, Anchor Period…) stay out of it:
   *  TradingView's own legend is numbers, and "BOLL 20 2 sma ema" is a chip
   *  nobody can scan. The dialog is where the words live. */
  function formatLabel(def, params) {
    const nums = [];
    if (def.period) nums.push(def.period);
    for (const f of def.inputs || []) {
      if (f.key === "period" || f.type === "source"
          || f.type === "enum" || f.type === "bool") continue;
      const v = params && params[f.key] != null ? params[f.key] : f.default;
      if (v != null) nums.push(trimNum(v));
    }
    return nums.length ? `${def.base} ${nums.join(" ")}` : def.base;
  }

  function decorate(p, k) {
    const def = {
      ...p,
      kind: (k || {}).pane === "own" ? "pane" : "overlay",
      group: (k || {}).group || "trend",
      lines: (k || {}).lines || ["value"],
      bounds: (k || {}).bounds,
      formula: (k || {}).formula || "",
      inputs: (k || {}).inputs || [],
    };
    def.label = formatLabel(def, null);
    return def;
  }

  async function loadCatalogue(base, symbol) {
    BASE = base;
    SYM = symbol || "";
    let known = {};
    try {
      const r = await fetch(`${base}/indicators`);
      for (const x of (await r.json()).indicators) known[x.name] = x;
    } catch { /* offline: fall back to presets alone */ }
    KNOWN = known;
    CATALOG = PRESETS
      .filter((p) => !Object.keys(known).length || known[p.name])
      .map((p) => decorate(p, known[p.name]));
    for (const d of CATALOG) relabel(d);
    return CATALOG;
  }

  /** The catalog entry for (name, period) — minted on demand. The presets
   *  are a convenience, not the limit: "RSI 26" clones the RSI preset with
   *  the new period so any period renders under its own honest label,
   *  instead of silently collapsing onto the preset's period (which drew
   *  RSI 14 while the reply quoted RSI 26). Returns the id, or null when
   *  the backend doesn't know the indicator at all. */
  function ensure(name, period) {
    if (!name) return null;
    const p = Number(period) || 0;
    const exact = CATALOG.find((c) => c.name === name && c.period === p);
    if (exact) return exact.id;
    const sib = CATALOG.find((c) => c.name === name);
    if (!p && sib) return sib.id;          // no period asked: the preset def
    // A zero-period backend study (A/D, anchored VWAP) has no numeric suffix,
    // but is still a complete definition that can be minted from KNOWN.
    const id = p ? `${name}${p}` : name;
    if (sib) {
      const def = { ...sib, id, period: p };
      CATALOG.push(def);
      relabel(def);
      return id;
    }
    if (KNOWN[name]) {                    // renderable but not a preset (wma…)
      const def = decorate(
        { id, name, period: p, base: name.toUpperCase().replace(/_/g, " ") },
        KNOWN[name]);
      CATALOG.push(def);
      relabel(def);
      return id;
    }
    return null;
  }

  /** Re-materialise a persisted id like "rsi26" after a reload, when the
   *  dynamic def it referred to no longer exists. */
  function ensureFromId(id) {
    if (CATALOG.find((c) => c.id === id)) return id;
    const m = /^([a-z_]+?)(\d+)$/.exec(id);
    if (m) return ensure(m[1], Number(m[2]));
    return KNOWN[id] ? ensure(id, KNOWN[id].period || 0) : null;
  }

  // The chart plots on IST-shifted times — main.js's fetchBars does
  // `time: b.t + IST` — so an indicator series must carry the SAME shift or
  // its points land 5.5 hours off every candle, which silently stretches the
  // time axis instead of erroring. The backend speaks raw epoch throughout;
  // the display shift stays in the data client, applied once, here and there.
  const IST = Sym.tz;

  async function fetchSeries(def, interval, limit, params, symbol) {
    // A secondary pane can hold a different instrument, so the SHIFT is read
    // off the symbol actually being fetched — a crypto pane's series folded on
    // the page symbol's +05:30 would land 5.5 h off its own candles.
    const sym = symbol || SYM;
    const shift = symbol ? Sym.of(symbol).tz : IST;
    // The SAME number, sent on as well as applied. It is the instrument's UTC
    // offset, and the backend needs it to know where a session VWAP resets —
    // that was pinned to +05:30 there, so a crypto VWAP broke its session at
    // 18:30 UTC while every other chart broke it at midnight. Sent on every
    // request rather than only for VWAP: compute() ignores it for the
    // indicators that do not take it, and a per-indicator condition here is
    // one more place to forget.
    const q = new URLSearchParams({
      name: def.name, interval, limit: String(limit),
      tz_offset: String(shift),
      ...(sym ? { symbol: sym } : {}),
      ...(def.period ? { period: String(def.period) } : {}),
    });
    for (const f of def.inputs || []) {
      if (f.key === "period") continue;
      const v = (params || {})[f.key];
      if (v != null && v !== "") q.set(f.key, String(v));
    }
    const r = await fetch(`${BASE}/indicator?${q}`);
    if (!r.ok) throw new Error((await r.json()).error || "indicator failed");
    const lines = (await r.json()).lines;
    for (const k of Object.keys(lines)) {
      lines[k] = lines[k].map((p) => ({ time: p.time + shift, value: p.value }));
    }
    return lines;
  }

  // ── styling by role ───────────────────────────────────
  // A line's NAME tells us what it is for, so colour follows meaning rather
  // than call order: bands read as a pair, and signal lines contrast with the
  // thing they signal.
  //
  // The three DIRECTIONAL pairs take the candle colours, which is the one
  // place on this chart where borrowing them is right. They were on the
  // rotating palette — supertrend_up, aroon_up and plus_di all landed on s4,
  // a salmon RED, and their bearish counterparts on s5, a cyan. A line the
  // legend calls "Uptrend" drawn in red is not a palette preference, it is a
  // chart that reads backwards at a glance, and every other chart a user has
  // seen (TradingView's included) paints the bullish side green.
  //
  // This does not contradict theme.js's rule that ANNOTATIONS never borrow
  // the candle colours: that rule exists so a red support line is not misread
  // as a down bar. Here the direction IS the meaning.
  const ROLE = {
    upper: "bandSoft", lower: "bandSoft", middle: "bandStrong",
    macd: "s2", signal: "s1",
    k: "s2", d: "s1",
    plus_di: "up", minus_di: "down", adx: "s3",
    aroon_up: "up", aroon_down: "down",
    supertrend_up: "up", supertrend_down: "down",
    psar: "s6", vwap: "s6", anchored_vwap: "s6",
  };
  const SERIES = ["s1", "s2", "s3", "s4", "s5", "s6"];
  const roleColor = (line, i) =>
    Theme.c(ROLE[line] || SERIES[((i % SERIES.length) + SERIES.length)
                                 % SERIES.length]);

  /* A line's index WITHIN its indicator was the whole palette key, so every
   * single-line overlay was line 0 and every single-line overlay was s1:
   * SMA 20 and SMA 200 came out the same gold, on the chart and in the
   * legend, with nothing to tell them apart. The index is now offset by a
   * per-INSTANCE slot, so the second SMA lands on the next colour.
   *
   * Named lines are untouched — a Bollinger band's upper/lower are a pair on
   * purpose, and MACD's signal is meant to contrast with MACD. Only the
   * unnamed fallback rotates.
   *
   * The slot is stored in the instance's own style, so it survives a reload
   * and a recolour, and the lowest free one is reused after a removal
   * rather than drifting up forever. */
  function allocSlot() {
    const used = new Set();
    for (const s of LIVE.values()) {
      const v = s && s.style && s.style.slot;
      if (Number.isInteger(v)) used.add(v);
    }
    for (let i = 0; i < 64; i++) if (!used.has(i)) return i;
    return 0;
  }

  /** Human names for the Style tab's rows. */
  const LINE_LABEL = {
    upper: "Upper", middle: "Basis", lower: "Lower",
    macd: "MACD", signal: "Signal", histogram: "Histogram",
    k: "%K", d: "%D", adx: "ADX", plus_di: "+DI", minus_di: "-DI",
    aroon_up: "Aroon Up", aroon_down: "Aroon Down",
    supertrend_up: "Uptrend", supertrend_down: "Downtrend",
    rsi: "RSI", atr: "ATR", cci: "CCI", mfi: "MFI", cmf: "CMF", obv: "OBV",
    ad: "A/D", roc: "ROC", williams_r: "%R", psar: "SAR",
    vwap: "VWAP", anchored_vwap: "Anchored VWAP",
    sma: "Plot", ema: "Plot", wma: "Plot", hma: "Plot", dema: "Plot",
  };
  const lineLabel = (n) =>
    LINE_LABEL[n] || n.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());

  // What the Style tab offers, and what each choice means to lightweight-charts.
  // TradingView's list also has "Cross"; there is no marker shape for it in
  // this library, so it is left off rather than silently drawn as circles.
  const PLOT_TYPES = [
    { id: "line", label: "Line" },
    { id: "stepline", label: "Step line" },
    { id: "area", label: "Area" },
    { id: "columns", label: "Columns" },
    { id: "circles", label: "Circles" },
  ];
  const LINE_STYLES = [
    { id: 0, label: "Solid" },
    { id: 2, label: "Dashed" },
    { id: 1, label: "Dotted" },
  ];
  const WIDTHS = [1, 2, 3, 4];

  // Charto's intervals collapse into TradingView's Visibility rows. Ticks and
  // Seconds are absent because this chart has no such interval — an always-off
  // checkbox is worse than no checkbox.
  const BUCKETS = [
    { key: "minutes", label: "Minutes", note: "1m · 5m · 15m · 30m" },
    { key: "hours", label: "Hours", note: "1h" },
    { key: "days", label: "Days", note: "D" },
    { key: "weeks", label: "Weeks", note: "W" },
    { key: "months", label: "Months", note: "M" },
  ];
  const BUCKET_OF = {
    "1m": "minutes", "5m": "minutes", "15m": "minutes", "30m": "minutes",
    "1h": "hours", "1d": "days", "1w": "weeks", "1mo": "months",
  };
  const VALUE_OF = {
    "1m": 1, "5m": 5, "15m": 15, "30m": 30,
    "1h": 1, "1d": 1, "1w": 1, "1mo": 1,
  };
  const RANGE_DEFAULTS = {
    minutes: { min: 1, max: 59 }, hours: { min: 1, max: 24 },
    days: { min: 1, max: 366 }, weeks: { min: 1, max: 52 },
    months: { min: 1, max: 12 },
  };

  /* The dashed reference levels a bounded oscillator is READ against.
   *
   * The backend already sends `bounds` for these, and it was carried into the
   * def and then never used — so adding RSI from the menu drew a bare line
   * with nothing to say where overbought was. Worse, the pane autoscales to
   * the data, so an RSI oscillating 40-65 filled the whole pane and looked
   * far more dramatic than it was. TradingView's reference lines are real
   * plots and so they take part in its autoscale; feeding these into the
   * scale provider below reproduces that, which is most of why its oscillator
   * panes read calm.
   *
   * Only the levels that are near-universal AND are what TradingView draws.
   * ADX and Aroon are deliberately absent: TV ships them with no reference
   * line, and inventing a "25 means trending" line here would be this chart
   * asserting a threshold nobody agreed on.
   *
   * Keyed by indicator NAME, so every period of one indicator shares them. */
  const LEVELS = {
    rsi: [70, 30],
    stoch: [80, 20],
    stochrsi: [80, 20],
    mfi: [80, 20],
    williams_r: [-20, -80],
    cci: [100, -100],
  };

  // ── the settings model ────────────────────────────────
  const SET_KEY = "ind_settings";     // id   -> settings the user changed
  const DEF_KEY = "ind_defaults";     // name -> "save as default" settings
  let SAVED = Store.get(SET_KEY, {});
  let FACTORY = Store.get(DEF_KEY, {});
  const LIVE = new Map();             // id -> the settings object in use

  const clone = (v) => JSON.parse(JSON.stringify(v));

  /* A line whose natural SHAPE is not a line. Parabolic SAR is the whole
   * list: it is a sequence of discrete stops that jumps from under price to
   * over it, and joining those points draws a zig-zag through the candles
   * that looks nothing like the dotted trail every other chart shows. The
   * "circles" plot type already existed in PLOT_TYPES and seriesFor already
   * implements it — SAR was simply never pointed at it.
   *
   * Still only a DEFAULT: the Style tab can switch it back to a line, and a
   * saved setting wins over this the same as any other plot type. */
  const PLOT_DEFAULT = { psar: "circles" };

  function plotDefaults(def, slot) {
    const off = Number.isInteger(slot) ? slot : 0;
    const out = {};
    (def.lines || []).forEach((n, i) => {
      const hist = n === "histogram";
      out[n] = {
        visible: true,
        color: hist ? Theme.c("histUp") : roleColor(n, i + off),
        colorDown: hist ? Theme.c("histDown") : undefined,
        colors: hist ? ["#22ab94", "#ace5dc", "#ffb1b5", "#ff5252"] : undefined,
        custom: false,          // a theme switch repaints only untouched plots
        width: def.kind === "overlay" ? 1
          : (n === "middle" || (def.lines || []).length === 1 ? 2 : 1),
        lineStyle: 0,
        plotType: hist ? "columns" : (PLOT_DEFAULT[n] || "line"),
      };
    });
    return out;
  }

  function factorySettings(def, slot) {
    const params = {};
    for (const f of def.inputs || []) {
      if (f.key !== "period") params[f.key] = f.default;
    }
    const visibility = {};
    for (const b of BUCKETS) visibility[b.key] = true;
    return {
      params,
      symbolMode: "main",
      symbol: "",
      style: {
        plots: plotDefaults(def, slot),
        slot,          // null on pane indicators — they do not rotate
        precision: "default",
        statusLine: true,
        inputsStatusLine: true,
        priceLabel: true,
        priceLine: false,
      },
      visibility,
      visibilityRanges: clone(RANGE_DEFAULTS),
      hidden: false,
    };
  }

  /** Deep-merge a saved patch over the factory shape. Anything the saved
   *  object doesn't mention keeps the factory value, so a settings blob
   *  written before a new option existed still loads. */
  function merge(base, patch) {
    if (!patch || typeof patch !== "object") return base;
    const out = Array.isArray(base) ? base.slice() : { ...base };
    for (const [k, v] of Object.entries(patch)) {
      out[k] = v && typeof v === "object" && !Array.isArray(v)
        && out[k] && typeof out[k] === "object"
        ? merge(out[k], v) : v;
    }
    return out;
  }

  /** Repaint every plot the user has NOT recoloured from the live palette.
   *  Saved settings carry the colours of whichever theme they were written
   *  in, so this runs at load as well as on a theme toggle — otherwise a
   *  session saved in dark comes back wearing dark's line colours on white. */
  function refreshThemeColors(def, s) {
    const slot = Number.isInteger(s.style && s.style.slot) ? s.style.slot : 0;
    const paneKind = def.kind !== "overlay";
    (def.lines || []).forEach((n, i) => {
      const plot = s.style.plots[n];
      if (!plot || plot.custom) return;
      if (n === "histogram") {
        plot.color = Theme.c("histUp");
        plot.colorDown = Theme.c("histDown");
      } else {
        plot.color = roleColor(n, i + (paneKind ? 0 : slot));
      }
    });
  }

  /** The live settings for one id, built on first ask: factory, then the
   *  user's saved default for this INDICATOR, then whatever this instance
   *  itself was left at. */
  function settings(id) {
    if (LIVE.has(id)) return LIVE.get(id);
    const def = CATALOG.find((c) => c.id === id);
    if (!def) return null;
    // allocated BEFORE the merges so a saved slot still wins, and the
    // entry is parked in LIVE first so two indicators added in the same
    // tick cannot both be handed the same one
    const rotates = def.kind === "overlay";
    let s = factorySettings(def, rotates ? allocSlot() : null);
    LIVE.set(id, s);
    if (FACTORY[def.name]) s = merge(s, FACTORY[def.name]);
    if (SAVED[id]) s = merge(s, SAVED[id]);
    if (rotates && !Number.isInteger(s.style.slot)) s.style.slot = allocSlot();
    if (!rotates) s.style.slot = null;
    refreshThemeColors(def, s);
    LIVE.set(id, s);
    return s;
  }

  function persist(id) {
    const s = LIVE.get(id);
    if (!s) return;
    SAVED[id] = s;
    Store.set(SET_KEY, SAVED);
  }

  function relabel(def) {
    const s = LIVE.get(def.id) || (SAVED[def.id] || FACTORY[def.name]
      ? settings(def.id) : null);
    def.label = formatLabel(def, s ? s.params : null);
  }

  // ── colour helpers ────────────────────────────────────
  /** "#rrggbb" + alpha -> an rgba() lightweight-charts accepts. Passes any
   *  string it doesn't recognise straight through, so a theme colour that is
   *  already rgba() survives untouched. */
  function withAlpha(color, a) {
    const m = /^#?([0-9a-f]{6})$/i.exec(String(color).trim());
    if (m) {
      const n = parseInt(m[1], 16);
      return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
    }
    const r = /^rgba?\(([^)]+)\)$/i.exec(String(color).trim());
    if (r) {
      const p = r[1].split(",").map((x) => x.trim());
      return `rgba(${p[0]},${p[1]},${p[2]},${a})`;
    }
    return color;
  }

  /** The value at `time` in a sorted {time,value} array. Exact match only:
   *  the crosshair sits ON a bar, and a nearest-neighbour fallback is what
   *  made a Supertrend print the flat side's stale number as though it were
   *  live. Binary, because this runs once per line per mousemove. */
  function valueAt(arr, time) {
    let lo = 0, hi = arr.length - 1;
    while (lo <= hi) {
      const m = (lo + hi) >> 1;
      if (arr[m].time === time) return arr[m];
      if (arr[m].time < time) lo = m + 1; else hi = m - 1;
    }
    return null;
  }

  // ── manager ───────────────────────────────────────────
  function createManager(chart) {
    const active = new Map();
    const refetchSeq = new Map();
    // The legend is DOM now (js/indlegend.js), not a canvas primitive: a row
    // that carries the eye, the gear and the × has to be reachable by a
    // pointer, and nothing drawn into the chart's own canvas ever is. This is
    // the one hook it needs — "the active set or its paint moved, repaint".
    let onLegend = null;
    let ctx = { interval: "1d", limit: 3000 };
    // scene marks on a pane (rsi 70/30 lines…) sit outside the data's own
    // range; the pane's autoscale must stretch to include them or a marked
    // level is silently invisible. main.js injects the lookup.
    let scaleExtras = null;
    // The candles themselves. Every caller already hands them over (toggle,
    // recomputeAll, retheme) and they were being dropped on the floor; the
    // Supertrend fill needs them, because the thing it shades TO is the
    // candle bodies.
    let BARS = [];

    function nextPane() { return chart.panes().length; }

    const intervalOk = (st) => {
      const b = BUCKET_OF[ctx.interval];
      if (!b || st.visibility[b] === false) return !b;
      const range = (st.visibilityRanges || {})[b] || RANGE_DEFAULTS[b];
      const value = VALUE_OF[ctx.interval];
      return value == null || !range || (value >= range.min && value <= range.max);
    };
    const shown = (st, line) =>
      !st.hidden && intervalOk(st) && (st.style.plots[line] || {}).visible !== false;

    /** Value formatting follows the Precision setting, so the status line
     *  never claims more digits than the user asked to see. */
    // Precision comes from the settings dialog; the LOCALE comes from Sym.
    // Both matter and they are independent: a user can ask for 4 decimals on
    // any instrument, but "12,34,567.5" is only right for an INR-quoted one —
    // a Bitcoin legend has to read 1,234,567.5.
    function formatter(st) {
      const p = st.style.precision;
      if (p === "default") {
        return (n) => n == null ? "—"
          : Math.abs(n) >= 1000 ? Sym.num(n, { maximumFractionDigits: 2 })
          : n.toFixed(2);
      }
      return (n) => n == null ? "—"
        : Sym.num(n, { minimumFractionDigits: p, maximumFractionDigits: p });
    }

    function priceFormat(st) {
      const p = st.style.precision;
      if (p === "default") return {};
      return { priceFormat: { type: "price", precision: p, minMove: Math.pow(10, -p) } };
    }

    /** Make a line with interior holes actually BREAK at them.
     *
     *  The backend already sends whitespace points ({time} with no value) for
     *  the bars a line is not live on, which is the series API's stated way of
     *  saying "gap". lightweight-charts, though, drops whitespace out of the
     *  plotted item list entirely: the line is then drawn straight from the
     *  last real point to the next one, and there is no connectNulls option to
     *  switch that off. Supertrend is the case it ruins — the inactive band's
     *  two ends were joined by a long diagonal straight through the candles,
     *  so a chart with a dozen trend flips wore a dozen phantom trendlines.
     *
     *  The one lever the library does give is per-point colour, and — measured
     *  on the bundled build, not assumed — it paints the segment LEADING AWAY
     *  from that point, i.e. point i colours i -> i+1. So the point to paint
     *  transparent is the last real one BEFORE the hole: that hides the bridge
     *  and nothing else, which is what TradingView's plot.style_linebr draws.
     *  (Colouring the first point AFTER the hole instead leaves the bridge and
     *  erases a real segment — the mistake is invisible on a dense line and
     *  obvious on Supertrend.)
     */
    const GAP = "rgba(0,0,0,0)";
    function breakGaps(pts) {
      const out = pts.slice();
      let last = -1, hole = false;
      for (let i = 0; i < out.length; i++) {
        if (out[i].value == null) { hole = true; continue; }
        if (hole && last >= 0) out[last] = { ...out[last], color: GAP };
        hole = false; last = i;
      }
      return out;
    }

    /* ── the trend fill ──────────────────────────────────
     * TradingView's Supertrend is not only the band. It also shades the space
     * between the band and the candle bodies — green while the trend is up,
     * red while it is down, at 90% transparency — and that wash is most of
     * what makes the indicator readable in one glance: which side of price the
     * line is on stops being something you have to work out.
     *
     * lightweight-charts has no fill-between-two-series, so this is a series
     * primitive: one polygon per contiguous run, out along the band and back
     * along the body midpoints. Which lines get shaded, and against what, is a
     * table rather than an if — the next banded overlay adds a row.
     */
    const FILL_LINES = { supertrend: ["supertrend_up", "supertrend_down"] };
    const FILL_ALPHA = 0.1;

    /** The line, paired bar by bar with the candle body's midpoint, split at
     *  every hole. A run of one has no area and is dropped. */
    function fillRuns(spec) {
      const mid = new Map(BARS.map((b) => [b.time, (b.open + b.close) / 2]));
      const runs = [];
      let run = [];
      for (const p of spec.data) {
        const m = mid.get(p.time);
        if (p.value == null || m === undefined) {
          if (run.length > 1) runs.push(run);
          run = [];
          continue;
        }
        run.push({ t: p.time, v: p.value, m });
      }
      if (run.length > 1) runs.push(run);
      return runs;
    }

    function makeFillPrimitive(a) {
      const paint = (context) => {
        const ts = chart.timeScale();
        const vis = ts.getVisibleRange();
        for (const f of (a.fills || [])) {
          context.fillStyle = f.color;
          for (const run of f.runs) {
            // years of history sit off-screen on every pan; a run nobody can
            // see costs two coordinate lookups per bar if it is not skipped
            if (vis && (run[run.length - 1].t < vis.from || run[0].t > vis.to)) continue;
            context.beginPath();
            let ok = true;
            for (let i = 0; i < run.length && ok; i++) {
              const x = ts.timeToCoordinate(run[i].t);
              const y = f.series.priceToCoordinate(run[i].v);
              if (x == null || y == null) { ok = false; break; }
              if (i) context.lineTo(x, y); else context.moveTo(x, y);
            }
            for (let i = run.length - 1; i >= 0 && ok; i--) {
              const x = ts.timeToCoordinate(run[i].t);
              const y = f.series.priceToCoordinate(run[i].m);
              if (x == null || y == null) { ok = false; break; }
              context.lineTo(x, y);
            }
            if (ok) { context.closePath(); context.fill(); }
          }
        }
      };
      return {
        updateAllViews() {},
        paneViews() {
          return [{
            zOrder: () => "bottom",
            renderer: () => ({
              draw(target) {
                target.useMediaCoordinateSpace(({ context }) => paint(context));
              },
            }),
          }];
        },
      };
    }

    function dropFill(a) {
      if (a.fillPrim && a.fillHost) {
        try { a.fillHost.detachPrimitive(a.fillPrim); } catch { /* series gone */ }
      }
      a.fillPrim = null; a.fillHost = null; a.fills = null;
    }

    /** Rebuild the shading for one live indicator. Runs on add, on every
     *  restyle and after every refetch, so a recolour, a hidden line, a new
     *  period and a theme flip all reach it through the one path. */
    function syncFill(id) {
      const a = active.get(id);
      if (!a || a.pending) return;
      const want = FILL_LINES[a.def && a.def.name];
      const st = settings(id);
      if (!want || !st || st.hidden || !BARS.length || !(a.series || []).length) {
        dropFill(a);
        return;
      }
      const fills = [];
      (a.specs || []).forEach((s, i) => {
        if (!want.includes(s.line) || !a.series[i] || !shown(st, s.line)) return;
        const plot = st.style.plots[s.line] || {};
        fills.push({ color: withAlpha(plot.color, FILL_ALPHA),
                     series: a.series[i], runs: fillRuns(s) });
      });
      a.fills = fills;
      // A plot-type change hands back a NEW series object; a primitive left on
      // the old one paints nothing and says nothing about it.
      const host = a.series[0];
      if (a.fillHost !== host) {
        dropFill(a);
        a.fills = fills;
        a.fillPrim = makeFillPrimitive(a);
        host.attachPrimitive(a.fillPrim);
        a.fillHost = host;
      }
    }

    /** One fetched line-set -> the specs the series layer consumes. */
    function toSpecs(def, lines, pane, st) {
      const names = (def.lines || []).filter((n) => lines[n]);
      return names.map((n) => {
        const plot = st.style.plots[n] || {};
        const hist = plot.plotType === "columns" || n === "histogram";
        return {
          line: n,
          pane,
          hist,
          data: hist
            ? lines[n].map((p, i, all) => {
                const prev = i ? all[i - 1].value : p.value;
                const colors = plot.colors || [plot.color, plot.color,
                  plot.colorDown || plot.color, plot.colorDown || plot.color];
                const rising = prev == null || p.value >= prev;
                const ci = p.value >= 0 ? (rising ? 0 : 1) : (rising ? 2 : 3);
                return { ...p, color: colors[ci] };
              })
            : breakGaps(lines[n]),
          // `opts` marks a plot the legend can quote a single value for.
          // A histogram is a shape, not a reading, so it stays out of it —
          // the same rule the first version had, now keyed off the chosen
          // plot type rather than the line's name.
          opts: hist ? null : plot,
        };
      });
    }

    function seriesFor(spec, st, def, isFirstOfPane) {
      const plot = st.style.plots[spec.line] || {};
      const common = {
        priceLineVisible: !!st.style.priceLine,
        lastValueVisible: !!st.style.priceLabel,
        visible: shown(st, spec.line),
        ...priceFormat(st),
        ...(def.kind === "pane" && isFirstOfPane
          ? { autoscaleInfoProvider: scaleWithMarks(def) } : {}),
      };
      if (spec.hist) {
        return chart.addSeries(LWC.HistogramSeries,
          { ...common, color: plot.color }, spec.pane ?? 0);
      }
      if (plot.plotType === "area") {
        return chart.addSeries(LWC.AreaSeries, {
          ...common,
          lineColor: plot.color, lineWidth: plot.width || 1,
          lineStyle: plot.lineStyle || 0,
          topColor: withAlpha(plot.color, 0.28),
          bottomColor: withAlpha(plot.color, 0.02),
          crosshairMarkerVisible: false,
        }, spec.pane ?? 0);
      }
      return chart.addSeries(LWC.LineSeries, {
        ...common,
        color: plot.color, lineWidth: plot.width || 1,
        lineStyle: plot.lineStyle || 0,
        lineType: plot.plotType === "stepline" ? 1 : 0,
        lineVisible: plot.plotType !== "circles",
        pointMarkersVisible: plot.plotType === "circles",
        crosshairMarkerVisible: false,
      }, spec.pane ?? 0);
    }

    // widen this pane's scale to include whatever the scene has marked on it,
    // AND this indicator's own reference levels — the provider is consulted
    // on every autoscale pass. Pinning the levels into the range is what
    // stops an RSI that never leaves 40-65 from filling its pane and reading
    // as violent; it is also the only thing that guarantees a 70 line is on
    // screen rather than clipped just above the data.
    const scaleWithMarks = (def) => (orig) => {
      const r = orig();
      const extras = [
        ...(scaleExtras ? scaleExtras(def.name, def.period) : []),
        ...(LEVELS[def.name] || []),
      ];
      if (!r || !r.priceRange || !extras.length) return r;
      return { ...r, priceRange: {
        minValue: Math.min(r.priceRange.minValue, ...extras),
        maxValue: Math.max(r.priceRange.maxValue, ...extras),
      } };
    };

    /** Draw (or redraw) this indicator's reference levels as price lines on
     *  the pane's FIRST series — the same series that carries the autoscale
     *  provider, so the lines and the range they force can never disagree.
     *
     *  Torn down and rebuilt rather than patched: a plot-type change in
     *  restyle() replaces the series object outright, and price lines belong
     *  to a series, so the old ones are already gone by then. Rebuilding is
     *  the only version that is correct in both paths.
     *
     *  Follows the eye and the Visibility tab: a hidden indicator, or one
     *  switched off for this timeframe, takes its reference lines with it —
     *  a 70 line floating over an empty pane is just clutter. */
    function applyLevels(a, st) {
      const api = (a.series || [])[0];
      if (api) {
        for (const pl of a.levels || []) {
          try { api.removePriceLine(pl); } catch { /* series already replaced */ }
        }
      }
      a.levels = [];
      const lv = LEVELS[(a.def || {}).name];
      if (!api || !lv || st.hidden || !intervalOk(st)) return;
      a.levels = lv.map((price) => api.createPriceLine({
        price,
        color: Theme.c("guide"),
        lineWidth: 1,
        lineStyle: 2,             // dashed, so it never reads as a plot
        axisLabelVisible: false,  // the value is the line's whole content
        title: "",
      }));
    }

    /** One legend ROW per live indicator, read at `time` (null = the latest
     *  bar). This is the whole model the DOM legend renders: the manager owns
     *  what an indicator is CALLED, what it is WORTH and what state it is in,
     *  and the view layer owns nothing but the markup.
     *
     *  `values` is empty rather than absent when there is nothing to quote —
     *  a hidden indicator, one switched off for this timeframe, or one whose
     *  "Values in status line" is unticked still gets a row, because the row
     *  is now the only handle it has. Dropping it, the way the old canvas
     *  legend did, would leave an indicator on the chart with no way to
     *  reach its eye or its gear. */
    function legendRows(time) {
      const rows = [];
      for (const [id, a] of active) {
        if (a.pending || !a.def) continue;
        const st = settings(id);
        if (!st) continue;
        const hidden = !!st.hidden;
        const off = !hidden && !intervalOk(st);
        const fmt = formatter(st);
        // Nulls are stripped before the series is set, so a line does not
        // necessarily reach the bar under the crosshair. Supertrend draws
        // only the band on the active side; an exact-time lookup is what
        // keeps the other one's stale number out of the row.
        const at = time != null ? time
          : Math.max(-1, ...(a.specs || []).map((s) =>
              (s.data.length ? s.data[s.data.length - 1].time : -1)));
        const values = [];
        let color = null;
        for (const s of (a.specs || [])) {
          if (!s.opts || !s.data.length) continue;
          if (!color) color = s.opts.color;          // the row's own swatch
          if (!shown(st, s.line) || st.style.statusLine === false) continue;
          const p = valueAt(s.data, at);
          if (!p || p.value == null) continue;
          values.push({ color: s.opts.color, text: fmt(p.value) });
        }
        // The pane index is read LIVE, not remembered: removing an oscillator
        // closes its pane and renumbers everything below it, and a legend box
        // positioned from the index the indicator was born with then floats
        // over the wrong chart.
        let pane = a.pane || 0;
        try { pane = a.series[0].getPane().paneIndex(); } catch { /* torn down */ }
        rows.push({
          id, label: st.style.inputsStatusLine === false ? a.def.base : a.def.label,
          kind: a.def.kind, pane,
          color: color || Theme.c("legend"), values, hidden, off,
        });
      }
      return rows;
    }

    /** "Something the legend renders has moved." Add, remove, restyle, a
     *  refetch and a theme flip all land here; the crosshair does not — that
     *  is the view's own subscription, and routing it through the manager
     *  would put a full re-render on every mousemove. */
    function emitLegend() { if (onLegend) onLegend(); }

    async function add(id) {
      if (active.has(id)) return;
      const def = CATALOG.find((c) => c.id === id);
      if (!def) return;
      const st = settings(id);
      active.set(id, { pending: true, def, series: [], data: [] });
      let lines;
      try {
        lines = await fetchSeries(def, ctx.interval, ctx.limit, st.params,
          st.symbolMode === "another" && st.symbol ? st.symbol : ctx.symbol);
      } catch (e) {
        active.delete(id);
        throw e;                       // caller surfaces it; never a silent no-op
      }
      if (!active.has(id)) return;     // toggled off while in flight
      const pane = def.kind === "pane" ? nextPane() : 0;
      const specs = toSpecs(def, lines, pane, st);
      const series = specs.map((s, i) => {
        const api = seriesFor(s, st, def, i === 0);
        api.setData(s.data);
        return api;
      });
      // a fresh oscillator pane defaults to an equal share of the chart;
      // price should stay dominant, so a new pane takes ~1/3 of the price
      // pane's stretch — relative to whatever LWC's default factor is
      if (def.kind === "pane" && series.length) {
        const p = series[0].getPane();
        const base = chart.panes()[0];
        if (p.setStretchFactor && base.getStretchFactor) {
          p.setStretchFactor(base.getStretchFactor() * 0.32);
        }
      }
      const entry = { series, def, specs, data: specs.map((s) => s.data),
                      raw: lines, pane, levels: [] };
      active.set(id, entry);
      // The band FILL and the price LEVELS are two different decorations on
      // one study and neither implies the other: syncFill paints the shaded
      // area between a pair of bands (Supertrend, Bollinger), applyLevels
      // draws the fixed reference lines an oscillator is read against (RSI
      // 30/70). A study can want either, both or neither.
      syncFill(id);
      applyLevels(entry, st);
      emitLegend();
    }

    function remove(id) {
      const a = active.get(id);
      if (!a) return;
      dropFill(a);
      (a.series || []).forEach((s) => chart.removeSeries(s));
      active.delete(id);
      emitLegend();
    }

    /** Re-apply the STYLE of a live indicator without refetching. Options are
     *  patched in place; only a changed plot TYPE needs a new series object,
     *  and that one is added before the old is removed so a single-series
     *  pane is never momentarily empty (which collapses the pane). */
    function restyle(id) {
      const a = active.get(id);
      if (!a || a.pending || !a.series.length) return;
      const st = settings(id);
      const specs = toSpecs(a.def, a.raw, a.pane, st);
      specs.forEach((s, i) => {
        const old = a.series[i];
        if (!old) return;
        const plot = st.style.plots[s.line] || {};
        const wasHist = a.specs[i] && a.specs[i].hist;
        const wasType = (a.specs[i] && a.specs[i].opts && a.specs[i].opts.plotType)
          || (wasHist ? "columns" : "line");
        const typeChanged = wasType !== (plot.plotType || "line");
        if (typeChanged) {
          const next = seriesFor(s, st, a.def, i === 0);
          next.setData(s.data);
          chart.removeSeries(old);
          a.series[i] = next;
          return;
        }
        const common = {
          priceLineVisible: !!st.style.priceLine,
          lastValueVisible: !!st.style.priceLabel,
          visible: shown(st, s.line),
          ...priceFormat(st),
        };
        if (s.hist) {
          old.applyOptions({ ...common, color: plot.color });
        } else if (plot.plotType === "area") {
          old.applyOptions({
            ...common, lineColor: plot.color, lineWidth: plot.width || 1,
            lineStyle: plot.lineStyle || 0,
            topColor: withAlpha(plot.color, 0.28),
            bottomColor: withAlpha(plot.color, 0.02),
          });
        } else {
          old.applyOptions({
            ...common, color: plot.color, lineWidth: plot.width || 1,
            lineStyle: plot.lineStyle || 0,
            lineType: plot.plotType === "stepline" ? 1 : 0,
            lineVisible: plot.plotType !== "circles",
            pointMarkersVisible: plot.plotType === "circles",
          });
        }
        // Always. A histogram carries its colours per point, and a restyle
        // that followed a refetch has new numbers to draw — routing both
        // through one setData is what keeps refetch from needing a second
        // render path that forgets about plot types.
        old.setData(s.data);
      });
      a.specs = specs;
      a.data = specs.map((s) => s.data);
      syncFill(id);
      // after the series loop, because a plot-type change above replaces
      // series[0] outright and the old price lines went with it. This is also
      // the path a theme flip and the eye arrive on, so the levels repaint
      // and appear/disappear with the study.
      applyLevels(a, st);
      emitLegend();
    }

    /** Refetch one live indicator against its current params, then hand the
     *  drawing to restyle — the single render path. Cancelling a dialog can
     *  put back BOTH a different StdDev and a different plot type in one
     *  move, and a refetch that only pushed new numbers left the series as
     *  whatever shape the cancelled edit had made it. */
    async function refetch(id) {
      const a = active.get(id);
      if (!a || a.pending) return;
      const seq = (refetchSeq.get(id) || 0) + 1;
      refetchSeq.set(id, seq);
      const current = settings(id);
      const lines = await fetchSeries(a.def, ctx.interval, ctx.limit,
        current.params,
        current.symbolMode === "another" && current.symbol ? current.symbol : ctx.symbol);
      // Input edits can overlap. A slow request for the old MACD lengths must
      // never land after Reset settings and repaint the old calculation over
      // the restored 12/26/9 result.
      if (!active.has(id) || refetchSeq.get(id) !== seq) return;
      a.raw = lines;
      restyle(id);
    }

    /** Interval or history changed — refetch every live indicator. */
    async function recomputeAll(bars, next) {
      if (bars) BARS = bars;
      if (next) ctx = { ...ctx, ...next };
      for (const [id, a] of [...active]) {
        if (a.pending || !a.series.length) continue;
        // a refetch already restyles; a failed one still needs the pass, so
        // the new interval's Visibility rule reaches the series either way
        try { await refetch(id); } catch { restyle(id); }
      }
      emitLegend();
    }

    /** Light/dark switch: repaint every plot the user has NOT recoloured.
     *  A custom colour is a decision, and a theme toggle is not a reason to
     *  overrule it. */
    function retheme(bars) {
      if (bars) BARS = bars;
      for (const [id, a] of active) {
        refreshThemeColors(a.def, settings(id));
        restyle(id);
      }
      emitLegend();
    }

    /** Swap a live indicator to a new period: same indicator, new def. The
     *  instance's style and params travel with it, since the user changed a
     *  length, not the whole indicator. */
    async function setPeriod(id, period) {
      const def = CATALOG.find((c) => c.id === id);
      const p = Number(period) || 0;
      if (!def || !p || p === def.period) return id;
      const nid = ensure(def.name, p);
      if (!nid || nid === id) return id;
      const carried = clone(settings(id));
      remove(id);
      LIVE.set(nid, carried);
      const ndef = CATALOG.find((c) => c.id === nid);
      if (ndef) relabel(ndef);
      await add(nid);
      persist(nid);
      return nid;
    }

    /** The one write path the settings dialog uses. Returns a promise so the
     *  caller can surface a failed refetch instead of leaving the dialog
     *  showing a value the chart never took. */
    async function applySettings(id, patch) {
      const st = settings(id);
      if (!st) return;
      const before = JSON.stringify([st.params, st.symbolMode, st.symbol]);
      const next = merge(st, patch);
      LIVE.set(id, next);
      const def = CATALOG.find((c) => c.id === id);
      if (def) relabel(def);
      persist(id);
      if (JSON.stringify([next.params, next.symbolMode, next.symbol]) !== before) {
        await refetch(id);              // the math moved
      } else {
        restyle(id);                    // only the paint moved
      }
    }

    /** Replace the whole settings object (Cancel restoring a snapshot, or
     *  the Defaults menu resetting to factory). */
    async function replaceSettings(id, whole) {
      const def = CATALOG.find((c) => c.id === id);
      if (!def) return;
      const old = settings(id) || {};
      const before = JSON.stringify([old.params, old.symbolMode, old.symbol]);
      LIVE.set(id, clone(whole));
      relabel(def);
      persist(id);
      // Paint presentation defaults immediately. A reset includes colours,
      // widths and visibility; none of those should wait behind a MACD data
      // request before the user can see that Reset settings worked.
      restyle(id);
      if (JSON.stringify([whole.params, whole.symbolMode, whole.symbol]) !== before) await refetch(id);
    }

    /** Force every indicator pane to re-run autoscale — called when the
     *  scene changes, since marked levels feed the scale via the provider. */
    function rescalePanes() {
      for (const [, a] of active) {
        if (a.def.kind === "pane" && a.series.length) {
          try { a.series[0].priceScale().applyOptions({ autoScale: true }); }
          catch { /* series being torn down */ }
        }
      }
    }

    return {
      get CATALOG() { return CATALOG; },
      active,
      ensure, ensureFromId, setPeriod, rescalePanes,
      settings, applySettings, replaceSettings,
      /** Factory settings for this def — what "Reset settings" restores. */
      factory: (id) => {
        const def = CATALOG.find((c) => c.id === id);
        return def ? factorySettings(def) : null;
      },
      /** "Save as default" / "Reset settings" in the dialog's Defaults menu. */
      saveAsDefault(id) {
        const def = CATALOG.find((c) => c.id === id);
        if (!def) return;
        const s = clone(settings(id));
        delete s.hidden;                 // an eye is a moment, not a default
        FACTORY[def.name] = s;
        Store.set(DEF_KEY, FACTORY);
      },
      clearDefault(id) {
        const def = CATALOG.find((c) => c.id === id);
        if (!def) return;
        delete FACTORY[def.name];
        Store.set(DEF_KEY, FACTORY);
      },
      hasDefault: (id) => {
        const def = CATALOG.find((c) => c.id === id);
        return !!(def && FACTORY[def.name]);
      },
      setHidden(id, v) {
        const st = settings(id);
        if (!st) return;
        st.hidden = !!v;
        persist(id);
        restyle(id);
      },
      isHidden: (id) => !!(settings(id) || {}).hidden,
      /** True when the current interval falls in a bucket this indicator is
       *  switched off for — the chip says so rather than looking broken. */
      offInterval: (id) => {
        const st = settings(id);
        const b = BUCKET_OF[ctx.interval];
        return !!(st && b && st.visibility[b] === false);
      },
      setScaleExtras(fn) { scaleExtras = fn; },
      /** The in-chart legend's two hooks: the rows to draw, and the signal
       *  that says they moved. One sink per manager — one chart, one legend. */
      legendRows,
      setLegendSink(fn) { onLegend = fn; if (fn) fn(); },
      setContext(next) { ctx = { ...ctx, ...next }; },
      get interval() { return ctx.interval; },
      /** The instrument this manager computes on — the page symbol on the
       *  primary, the pane's own on a secondary chart. */
      get symbol() { return ctx.symbol || SYM; },
      toggle(id, bars) {
        if (bars) BARS = bars;
        return active.has(id) ? (remove(id), Promise.resolve()) : add(id);
      },
      remove, recomputeAll, retheme, restyle,
      isActive: (id) => active.has(id),
      /** Current value + value at `fromTime`, for the chat context envelope.
       *  The FIRST named line is the one that represents the indicator. */
      snapshot(fromTime) {
        const out = [];
        for (const [, a] of active) {
          const idx = (a.specs || []).findIndex((s) => s.opts);
          const arr = (a.data || [])[idx < 0 ? 0 : idx] || [];
          if (!arr.length) continue;
          let at = null;
          for (const p of arr) if (p.time >= fromTime) { at = p.value; break; }
          out.push({ label: a.def.label, now: arr[arr.length - 1].value, at });
        }
        return out;
      },
    };
  }

  return {
    createManager, loadCatalogue,
    get CATALOG() { return CATALOG; },
    // the dialog's vocabulary — one definition, read by indsettings.js
    PLOT_TYPES, LINE_STYLES, WIDTHS, BUCKETS, BUCKET_OF,
    lineLabel, formatLabel, withAlpha,
  };
})();
