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
    { id: "aroon", name: "aroon", period: 25, base: "Aroon" },
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
    if (!p) return sib ? sib.id : null;   // no period asked: the default def
    const id = `${name}${p}`;
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
    return m ? ensure(m[1], Number(m[2])) : null;
  }

  // The chart plots on IST-shifted times — main.js's fetchBars does
  // `time: b.t + IST` — so an indicator series must carry the SAME shift or
  // its points land 5.5 hours off every candle, which silently stretches the
  // time axis instead of erroring. The backend speaks raw epoch throughout;
  // the display shift stays in the data client, applied once, here and there.
  const IST = Sym.tz;

  async function fetchSeries(def, interval, limit, params) {
    const q = new URLSearchParams({
      name: def.name, interval, limit: String(limit),
      ...(SYM ? { symbol: SYM } : {}),
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
      lines[k] = lines[k].map((p) => ({ time: p.time + IST, value: p.value }));
    }
    return lines;
  }

  // ── styling by role ───────────────────────────────────
  // A line's NAME tells us what it is for, so colour follows meaning rather
  // than call order: bands read as a pair, signal lines contrast with the
  // thing they signal, and the +DI/-DI style pair stays distinguishable.
  const ROLE = {
    upper: "bandSoft", lower: "bandSoft", middle: "bandStrong",
    macd: "s2", signal: "s1",
    k: "s2", d: "s1",
    plus_di: "s4", minus_di: "s5", adx: "s3",
    aroon_up: "s4", aroon_down: "s5",
    supertrend_up: "s4", supertrend_down: "s5",
    psar: "s6", vwap: "s6", anchored_vwap: "s6",
  };
  const roleColor = (line, i) =>
    Theme.c(ROLE[line] || ["s1", "s2", "s3", "s4", "s5", "s6"][i % 6]);

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

  // ── the settings model ────────────────────────────────
  const SET_KEY = "ind_settings";     // id   -> settings the user changed
  const DEF_KEY = "ind_defaults";     // name -> "save as default" settings
  let SAVED = Store.get(SET_KEY, {});
  let FACTORY = Store.get(DEF_KEY, {});
  const LIVE = new Map();             // id -> the settings object in use

  const clone = (v) => JSON.parse(JSON.stringify(v));

  function plotDefaults(def) {
    const out = {};
    (def.lines || []).forEach((n, i) => {
      const hist = n === "histogram";
      out[n] = {
        visible: true,
        color: hist ? Theme.c("histUp") : roleColor(n, i),
        colorDown: hist ? Theme.c("histDown") : undefined,
        custom: false,          // a theme switch repaints only untouched plots
        width: n === "middle" || (def.lines || []).length === 1 ? 2 : 1,
        lineStyle: 0,
        plotType: hist ? "columns" : "line",
      };
    });
    return out;
  }

  function factorySettings(def) {
    const params = {};
    for (const f of def.inputs || []) {
      if (f.key !== "period") params[f.key] = f.default;
    }
    const visibility = {};
    for (const b of BUCKETS) visibility[b.key] = true;
    return {
      params,
      style: {
        plots: plotDefaults(def),
        precision: "default",
        statusLine: true,
        priceLabel: false,
        priceLine: false,
      },
      visibility,
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
    (def.lines || []).forEach((n, i) => {
      const plot = s.style.plots[n];
      if (!plot || plot.custom) return;
      if (n === "histogram") {
        plot.color = Theme.c("histUp");
        plot.colorDown = Theme.c("histDown");
      } else {
        plot.color = roleColor(n, i);
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
    let s = factorySettings(def);
    if (FACTORY[def.name]) s = merge(s, FACTORY[def.name]);
    if (SAVED[id]) s = merge(s, SAVED[id]);
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

  // ── legend primitive (one per pane) ───────────────────
  function makeLegendPrimitive(topOffset) {
    return {
      _lines: [], _ru: null,
      attached(p) { this._ru = p.requestUpdate; },
      setLines(lines) { this._lines = lines; if (this._ru) this._ru(); },
      updateAllViews() {},
      paneViews() {
        const self = this;
        return [{
          zOrder: () => "top",
          renderer: () => ({
            draw(target) {
              target.useMediaCoordinateSpace(({ context: ctx }) => {
                ctx.font = '11.5px ui-sans-serif, -apple-system, BlinkMacSystemFont, "Inter", "Segoe UI", Roboto, sans-serif';
                let y = topOffset;
                for (const ln of self._lines) {
                  ctx.fillStyle = ln.color || Theme.c("legend");
                  ctx.fillText(ln.text, 10, y);
                  y += 16;
                }
              });
            },
          }),
        }];
      },
    };
  }

  // ── manager ───────────────────────────────────────────
  function createManager(chart) {
    const active = new Map();
    let overlayLegend = null;
    let ctx = { interval: "1d", limit: 3000 };
    // scene marks on a pane (rsi 70/30 lines…) sit outside the data's own
    // range; the pane's autoscale must stretch to include them or a marked
    // level is silently invisible. main.js injects the lookup.
    let scaleExtras = null;

    function nextPane() { return chart.panes().length; }

    const intervalOk = (st) => {
      const b = BUCKET_OF[ctx.interval];
      return !b || st.visibility[b] !== false;
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
            ? lines[n].map((p) => ({
                ...p,
                color: p.value >= 0 ? plot.color
                  : (plot.colorDown || plot.color),
              }))
            : lines[n],
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

    // widen this pane's scale to include whatever the scene has marked on
    // it — the provider is consulted on every autoscale pass
    const scaleWithMarks = (def) => (orig) => {
      const r = orig();
      const extras = scaleExtras ? scaleExtras(def.name, def.period) : [];
      if (!r || !r.priceRange || !extras.length) return r;
      return { ...r, priceRange: {
        minValue: Math.min(r.priceRange.minValue, ...extras),
        maxValue: Math.max(r.priceRange.maxValue, ...extras),
      } };
    };

    function legendLines(def, specs, st) {
      if (!st.style.statusLine || st.hidden || !intervalOk(st)) return [];
      // Nulls are stripped before the series is set, so the last POINT of a
      // line is not necessarily a value at the latest bar. Supertrend shows
      // only the band on the active side, and printing the other one's last
      // known value reads as though both are live. Show a line only when it
      // actually reaches the newest bar any of them do.
      const endsAt = (s) => (s.data.length ? s.data[s.data.length - 1].time : -1);
      const newest = Math.max(-1, ...specs.map(endsAt));
      const named = specs.filter((s) =>
        s.opts && shown(st, s.line) && endsAt(s) === newest);
      if (!named.length) return [];
      const fmt = formatter(st);
      const text = `${def.label} · `
        + named.map((s) => fmt(s.data[s.data.length - 1].value)).join("  ");
      return [{ text, color: named[0].opts.color }];
    }

    function refreshOverlayLegend() {
      if (!overlayLegend) {
        overlayLegend = makeLegendPrimitive(64);
        chart.panes()[0].attachPrimitive(overlayLegend);
      }
      const lines = [];
      for (const [, a] of active) if (a.def.kind === "overlay") lines.push(...a.legendLines);
      overlayLegend.setLines(lines);
    }

    async function add(id) {
      if (active.has(id)) return;
      const def = CATALOG.find((c) => c.id === id);
      if (!def) return;
      const st = settings(id);
      active.set(id, { pending: true, def, series: [], data: [], legendLines: [] });
      let lines;
      try {
        lines = await fetchSeries(def, ctx.interval, ctx.limit, st.params);
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
      const entry = { series, def, specs, legendLines: legendLines(def, specs, st),
                      data: specs.map((s) => s.data), raw: lines, pane };
      if (def.kind === "pane" && series.length) {
        entry.legend = makeLegendPrimitive(16);
        series[0].getPane().attachPrimitive(entry.legend);
        entry.legend.setLines(entry.legendLines);
      }
      active.set(id, entry);
      refreshOverlayLegend();
    }

    function remove(id) {
      const a = active.get(id);
      if (!a) return;
      if (a.legend) { try { a.series[0].getPane().detachPrimitive(a.legend); } catch {} }
      (a.series || []).forEach((s) => chart.removeSeries(s));
      active.delete(id);
      refreshOverlayLegend();
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
      a.legendLines = legendLines(a.def, specs, st);
      if (a.legend) a.legend.setLines(a.legendLines);
      refreshOverlayLegend();
    }

    /** Refetch one live indicator against its current params, then hand the
     *  drawing to restyle — the single render path. Cancelling a dialog can
     *  put back BOTH a different StdDev and a different plot type in one
     *  move, and a refetch that only pushed new numbers left the series as
     *  whatever shape the cancelled edit had made it. */
    async function refetch(id) {
      const a = active.get(id);
      if (!a || a.pending) return;
      const lines = await fetchSeries(a.def, ctx.interval, ctx.limit,
                                      settings(id).params);
      if (!active.has(id)) return;
      a.raw = lines;
      restyle(id);
    }

    /** Interval or history changed — refetch every live indicator. */
    async function recomputeAll(_bars, next) {
      if (next) ctx = { ...ctx, ...next };
      for (const [id, a] of [...active]) {
        if (a.pending || !a.series.length) continue;
        // a refetch already restyles; a failed one still needs the pass, so
        // the new interval's Visibility rule reaches the series either way
        try { await refetch(id); } catch { restyle(id); }
      }
      refreshOverlayLegend();
    }

    /** Light/dark switch: repaint every plot the user has NOT recoloured.
     *  A custom colour is a decision, and a theme toggle is not a reason to
     *  overrule it. */
    function retheme(_bars) {
      for (const [id, a] of active) {
        refreshThemeColors(a.def, settings(id));
        restyle(id);
      }
      refreshOverlayLegend();
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
      const before = JSON.stringify(st.params);
      const next = merge(st, patch);
      LIVE.set(id, next);
      const def = CATALOG.find((c) => c.id === id);
      if (def) relabel(def);
      persist(id);
      if (JSON.stringify(next.params) !== before) {
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
      const before = JSON.stringify((settings(id) || {}).params);
      LIVE.set(id, clone(whole));
      relabel(def);
      persist(id);
      if (JSON.stringify(whole.params) !== before) await refetch(id);
      else restyle(id);
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
      setContext(next) { ctx = { ...ctx, ...next }; },
      get interval() { return ctx.interval; },
      toggle(id, _bars) { return active.has(id) ? (remove(id), Promise.resolve()) : add(id); },
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
