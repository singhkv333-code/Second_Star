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
 * ROLE gets, and how the legend reads. Structure comes from the backend
 * catalogue, styling is decided locally — the same split geometry.js and
 * tools.js already use.
 */
"use strict";

const Indicators = (() => {
  const LWC = window.LightweightCharts;
  const fmt = (n) => n == null ? "—"
    : Math.abs(n) >= 1000 ? n.toLocaleString("en-IN", { maximumFractionDigits: 2 })
    : n.toFixed(2);

  // ── catalogue, loaded from the backend ────────────────
  // Presets are the friendly names on the menu; any indicator/period the
  // backend knows can still be requested by id, so the menu is a convenience
  // rather than the limit of what exists.
  const PRESETS = [
    { id: "sma20", name: "sma", period: 20, label: "SMA 20" },
    { id: "sma50", name: "sma", period: 50, label: "SMA 50" },
    { id: "sma200", name: "sma", period: 200, label: "SMA 200" },
    { id: "ema21", name: "ema", period: 21, label: "EMA 21" },
    { id: "bbands", name: "bbands", period: 20, label: "BOLL 20 2" },
    { id: "keltner", name: "keltner", period: 20, label: "Keltner 20" },
    { id: "donchian", name: "donchian", period: 20, label: "Donchian 20" },
    { id: "supertrend", name: "supertrend", period: 10, label: "Supertrend 10" },
    { id: "psar", name: "psar", period: 0, label: "Parabolic SAR" },
    { id: "vwap", name: "vwap", period: 0, label: "VWAP session", intradayOnly: true },
    { id: "rsi", name: "rsi", period: 14, label: "RSI 14" },
    { id: "macd", name: "macd", period: 0, label: "MACD 12 26 9" },
    { id: "stoch", name: "stoch", period: 14, label: "Stoch 14 3 3" },
    { id: "stochrsi", name: "stochrsi", period: 14, label: "Stoch RSI 14" },
    { id: "adx", name: "adx", period: 14, label: "ADX 14" },
    { id: "atr", name: "atr", period: 14, label: "ATR 14" },
    { id: "cci", name: "cci", period: 20, label: "CCI 20" },
    { id: "williams_r", name: "williams_r", period: 14, label: "Williams %R 14" },
    { id: "mfi", name: "mfi", period: 14, label: "MFI 14" },
    { id: "obv", name: "obv", period: 0, label: "OBV" },
    { id: "cmf", name: "cmf", period: 20, label: "CMF 20" },
    { id: "aroon", name: "aroon", period: 25, label: "Aroon 25" },
  ];

  let CATALOG = [];          // presets joined with what the backend reports
  let KNOWN = {};            // the backend's own catalogue, kept for minting
  let BASE = "";

  async function loadCatalogue(base) {
    BASE = base;
    let known = {};
    try {
      const r = await fetch(`${base}/indicators`);
      for (const x of (await r.json()).indicators) known[x.name] = x;
    } catch { /* offline: fall back to presets alone */ }
    KNOWN = known;
    CATALOG = PRESETS
      .filter((p) => !Object.keys(known).length || known[p.name])
      .map((p) => ({
        ...p,
        kind: (known[p.name] || {}).pane === "own" ? "pane" : "overlay",
        group: (known[p.name] || {}).group || "trend",
        lines: (known[p.name] || {}).lines || ["value"],
        bounds: (known[p.name] || {}).bounds,
        formula: (known[p.name] || {}).formula || "",
      }));
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
      // first numeric token in the label is always the period
      CATALOG.push({ ...sib, id, period: p,
                     label: sib.label.replace(/\d+/, String(p)) });
      return id;
    }
    if (KNOWN[name]) {                    // renderable but not a preset (wma…)
      const k = KNOWN[name];
      CATALOG.push({
        id, name, period: p,
        label: `${name.toUpperCase().replace(/_/g, " ")} ${p}`,
        kind: k.pane === "own" ? "pane" : "overlay",
        group: k.group || "trend", lines: k.lines || ["value"],
        bounds: k.bounds, formula: k.formula || "",
      });
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
  const IST = 19800;

  async function fetchSeries(def, interval, limit) {
    const q = new URLSearchParams({
      name: def.name, interval, limit: String(limit),
      ...(def.period ? { period: String(def.period) } : {}),
    });
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
    macd: "s2", signal: "s1", histogram: "hist",
    k: "s2", d: "s1",
    plus_di: "s4", minus_di: "s5", adx: "s3",
    aroon_up: "s4", aroon_down: "s5",
    supertrend_up: "s4", supertrend_down: "s5",
    psar: "s6", vwap: "s6", anchored_vwap: "s6",
  };
  const roleColor = (line, i) =>
    Theme.c(ROLE[line] || ["s1", "s2", "s3", "s4", "s5", "s6"][i % 6]);

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

    function nextPane() { return chart.panes().length; }

    /** One fetched line-set -> the specs the series layer consumes. */
    function toSpecs(def, lines, pane) {
      const names = def.lines.filter((n) => lines[n]);
      return names.map((n, i) => ({
        line: n,
        pane,
        hist: n === "histogram",
        data: n === "histogram"
          ? lines[n].map((p) => ({ ...p, color: p.value >= 0 ? Theme.c("histUp") : Theme.c("histDown") }))
          : lines[n],
        opts: n === "histogram" ? null
          : { color: roleColor(n, i), lineWidth: n === "middle" || def.lines.length === 1 ? 2 : 1 },
      }));
    }

    function legendLines(def, specs) {
      // Nulls are stripped before the series is set, so the last POINT of a
      // line is not necessarily a value at the latest bar. Supertrend shows
      // only the band on the active side, and printing the other one's last
      // known value reads as though both are live. Show a line only when it
      // actually reaches the newest bar any of them do.
      const endsAt = (s) => (s.data.length ? s.data[s.data.length - 1].time : -1);
      const newest = Math.max(-1, ...specs.map(endsAt));
      const named = specs.filter((s) => s.opts && endsAt(s) === newest);
      if (!named.length) return [];
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
      active.set(id, { pending: true, def, series: [], data: [], legendLines: [] });
      let lines;
      try {
        lines = await fetchSeries(def, ctx.interval, ctx.limit);
      } catch (e) {
        active.delete(id);
        throw e;                       // caller surfaces it; never a silent no-op
      }
      if (!active.has(id)) return;     // toggled off while in flight
      const pane = def.kind === "pane" ? nextPane() : 0;
      const specs = toSpecs(def, lines, pane);
      const series = specs.map((s) => {
        const api = s.hist
          ? chart.addSeries(LWC.HistogramSeries, { priceLineVisible: false, lastValueVisible: false }, s.pane ?? 0)
          : chart.addSeries(LWC.LineSeries, {
              priceLineVisible: false, lastValueVisible: false,
              crosshairMarkerVisible: false, ...(s.opts || {}),
            }, s.pane ?? 0);
        api.setData(s.data);
        return api;
      });
      const entry = { series, def, specs, legendLines: legendLines(def, specs),
                      data: specs.map((s) => s.data) };
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

    /** Interval or history changed — refetch every live indicator. */
    async function recomputeAll(_bars, next) {
      if (next) ctx = { ...ctx, ...next };
      for (const [id, a] of [...active]) {
        if (a.pending || !a.series.length) continue;
        let lines;
        try { lines = await fetchSeries(a.def, ctx.interval, ctx.limit); } catch { continue; }
        if (!active.has(id)) continue;
        const specs = toSpecs(a.def, lines, 0);
        specs.forEach((s, i) => a.series[i] && a.series[i].setData(s.data));
        a.data = specs.map((s) => s.data);
        a.specs = specs;
        a.legendLines = legendLines(a.def, specs);
        if (a.legend) a.legend.setLines(a.legendLines);
      }
      refreshOverlayLegend();
    }

    /** Light/dark switch: recolour live series and repaint colour-carrying data. */
    function retheme(bars) {
      for (const [, a] of active) {
        (a.specs || []).forEach((s, i) => {
          if (!a.series[i]) return;
          if (s.opts) a.series[i].applyOptions({ color: roleColor(s.line, i) });
          if (s.hist) {
            a.series[i].setData(s.data.map((p) => ({
              ...p, color: p.value >= 0 ? Theme.c("histUp") : Theme.c("histDown"),
            })));
          }
        });
      }
      refreshOverlayLegend();
    }

    /** Swap a live indicator to a new period: same indicator, new def. */
    async function setPeriod(id, period) {
      const def = CATALOG.find((c) => c.id === id);
      const p = Number(period) || 0;
      if (!def || !p || p === def.period) return id;
      const nid = ensure(def.name, p);
      if (!nid || nid === id) return id;
      remove(id);
      await add(nid);
      return nid;
    }

    return {
      get CATALOG() { return CATALOG; },
      active,
      ensure, ensureFromId, setPeriod,
      setContext(next) { ctx = { ...ctx, ...next }; },
      toggle(id, _bars) { return active.has(id) ? (remove(id), Promise.resolve()) : add(id); },
      remove, recomputeAll, retheme,
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

  return { createManager, loadCatalogue, get CATALOG() { return CATALOG; } };
})();
