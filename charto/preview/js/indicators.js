/* Charto preview — indicator engine.
 *
 * lightweight-charts ships NO built-in indicators (deliberate: in real
 * Charto every series comes from the backend registry). For this sandbox
 * preview we compute locally from the loaded bars so the full loop is
 * testable offline. Overlays render on the price pane; oscillators get
 * their own pane via addSeries(..., paneIndex).
 *
 * Each pane gets a TradingView-style legend ("RSI 14 · 71.61") drawn by a
 * pane primitive; overlay legends stack on the price pane under the OHLC
 * readout.
 */
"use strict";

const Indicators = (() => {
  const LWC = window.LightweightCharts;
  const fmt = (n) => n == null ? "—"
    : Number(n).toLocaleString("en-IN", { maximumFractionDigits: 2 });

  // ── math ──────────────────────────────────────────────
  function sma(bars, n, src = "close") {
    const out = []; let sum = 0;
    for (let i = 0; i < bars.length; i++) {
      sum += bars[i][src];
      if (i >= n) sum -= bars[i - n][src];
      if (i >= n - 1) out.push({ time: bars[i].time, value: sum / n });
    }
    return out;
  }
  function ema(bars, n, src = "close") {
    const out = []; const k = 2 / (n + 1); let prev = null;
    for (let i = 0; i < bars.length; i++) {
      const v = bars[i][src];
      prev = prev === null ? v : v * k + prev * (1 - k);
      if (i >= n - 1) out.push({ time: bars[i].time, value: prev });
    }
    return out;
  }
  function bollinger(bars, n = 20, mult = 2) {
    const mid = [], up = [], lo = [];
    for (let i = n - 1; i < bars.length; i++) {
      let s = 0, s2 = 0;
      for (let j = i - n + 1; j <= i; j++) { s += bars[j].close; s2 += bars[j].close ** 2; }
      const m = s / n, sd = Math.sqrt(Math.max(0, s2 / n - m * m));
      mid.push({ time: bars[i].time, value: m });
      up.push({ time: bars[i].time, value: m + mult * sd });
      lo.push({ time: bars[i].time, value: m - mult * sd });
    }
    return { mid, up, lo };
  }
  function vwapSession(bars) {
    // Anchored to each IST trading day (chart times are already IST-shifted).
    const out = []; let day = null, cumPV = 0, cumV = 0;
    for (const b of bars) {
      const d = Math.floor(b.time / 86400);
      if (d !== day) { day = d; cumPV = 0; cumV = 0; }
      const tp = (b.high + b.low + b.close) / 3;
      cumPV += tp * (b.volume || 1); cumV += (b.volume || 1);
      out.push({ time: b.time, value: cumPV / cumV });
    }
    return out;
  }
  function rsi(bars, n = 14) {
    const out = []; let ag = 0, al = 0;
    for (let i = 1; i < bars.length; i++) {
      const ch = bars[i].close - bars[i - 1].close;
      const g = Math.max(ch, 0), l = Math.max(-ch, 0);
      if (i <= n) { ag += g / n; al += l / n; }
      else { ag = (ag * (n - 1) + g) / n; al = (al * (n - 1) + l) / n; }
      if (i >= n) out.push({ time: bars[i].time, value: al === 0 ? 100 : 100 - 100 / (1 + ag / al) });
    }
    return out;
  }
  function macd(bars, fast = 12, slow = 26, sig = 9) {
    const ef = [], es = []; const kf = 2 / (fast + 1), ks = 2 / (slow + 1);
    let pf = null, ps = null;
    for (const b of bars) {
      pf = pf === null ? b.close : b.close * kf + pf * (1 - kf);
      ps = ps === null ? b.close : b.close * ks + ps * (1 - ks);
      ef.push(pf); es.push(ps);
    }
    const line = [], signal = [], hist = []; const kg = 2 / (sig + 1); let pg = null;
    for (let i = slow - 1; i < bars.length; i++) {
      const m = ef[i] - es[i];
      pg = pg === null ? m : m * kg + pg * (1 - kg);
      line.push({ time: bars[i].time, value: m });
      signal.push({ time: bars[i].time, value: pg });
      hist.push({ time: bars[i].time, value: m - pg,
                  color: m - pg >= 0 ? Theme.c("histUp") : Theme.c("histDown") });
    }
    return { line, signal, hist };
  }
  function atr(bars, n = 14) {
    const out = []; let prev = null, a = null;
    for (let i = 0; i < bars.length; i++) {
      const b = bars[i];
      const tr = prev === null ? b.high - b.low
        : Math.max(b.high - b.low, Math.abs(b.high - prev.close), Math.abs(b.low - prev.close));
      a = a === null ? tr : (a * (n - 1) + tr) / n;
      if (i >= n) out.push({ time: b.time, value: a });
      prev = b;
    }
    return out;
  }

  // ── catalog ───────────────────────────────────────────
  // kind: "overlay" (price pane) | "pane" (own subpane)
  const CATALOG = [
    { id: "sma20",  label: "SMA 20",           kind: "overlay" },
    { id: "sma50",  label: "SMA 50",           kind: "overlay" },
    { id: "sma200", label: "SMA 200",          kind: "overlay" },
    { id: "ema21",  label: "EMA 21",           kind: "overlay" },
    { id: "boll",   label: "BOLL 20 2",        kind: "overlay" },
    { id: "vwap",   label: "VWAP session",     kind: "overlay", intradayOnly: true },
    { id: "rsi",    label: "RSI 14",           kind: "pane" },
    { id: "macd",   label: "MACD 12 26 9",     kind: "pane" },
    { id: "atr",    label: "ATR 14",           kind: "pane" },
  ];

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
    const active = new Map();   // id -> {series:[...], def, legend?, lastVals}
    let overlayLegend = null;   // shared price-pane legend

    function nextPane() { return chart.panes().length; }

    // Colours are read from the active theme at BUILD time; retheme() rebuilds
    // so a light/dark switch never leaves a washed-out line behind.
    const C = (k) => Theme.c(k);
    const builders = {
      sma20:  (bars) => [{ opts: { color: C("s1"), lineWidth: 1 }, data: sma(bars, 20) }],
      sma50:  (bars) => [{ opts: { color: C("s2"), lineWidth: 1 }, data: sma(bars, 50) }],
      sma200: (bars) => [{ opts: { color: C("s3"), lineWidth: 1 }, data: sma(bars, 200) }],
      ema21:  (bars) => [{ opts: { color: C("s4"), lineWidth: 1 }, data: ema(bars, 21) }],
      boll: (bars) => {
        const b = bollinger(bars);
        return [
          { opts: { color: C("bandStrong"), lineWidth: 1 }, data: b.mid },
          { opts: { color: C("bandSoft"), lineWidth: 1 }, data: b.up },
          { opts: { color: C("bandSoft"), lineWidth: 1 }, data: b.lo },
        ];
      },
      vwap: (bars) => [{ opts: { color: C("s6"), lineWidth: 2 }, data: vwapSession(bars) }],
      rsi: (bars, pane) => [
        { pane, opts: { color: C("s3"), lineWidth: 1 }, data: rsi(bars) },
      ],
      macd: (bars, pane) => {
        const m = macd(bars);
        return [
          { pane, hist: true, data: m.hist },
          { pane, opts: { color: C("s2"), lineWidth: 1 }, data: m.line },
          { pane, opts: { color: C("s1"), lineWidth: 1 }, data: m.signal },
        ];
      },
      atr: (bars, pane) => [
        { pane, opts: { color: C("s5"), lineWidth: 1 }, data: atr(bars) },
      ],
    };

    const last = (arr) => arr.length ? arr[arr.length - 1].value : null;

    function legendLines(id, specs) {
      const def = CATALOG.find((c) => c.id === id);
      if (id === "macd") {
        return [{
          text: `${def.label} · ${fmt(last(specs[1].data))}  ${fmt(last(specs[2].data))}`,
          color: specs[1].opts.color,
        }];
      }
      const spec = specs.find((s) => s.opts) || specs[0];
      return [{
        text: `${def.label} · ${fmt(last(spec.data))}`,
        color: (spec.opts && spec.opts.color) || Theme.c("legend"),
      }];
    }

    function refreshOverlayLegend() {
      if (!overlayLegend) {
        overlayLegend = makeLegendPrimitive(64); // below the HTML OHLC readout
        chart.panes()[0].attachPrimitive(overlayLegend);
      }
      const lines = [];
      for (const [id, a] of active) {
        if (a.def.kind === "overlay") lines.push(...a.legendLines);
      }
      overlayLegend.setLines(lines);
    }

    function add(id, bars) {
      if (active.has(id)) return;
      const def = CATALOG.find((c) => c.id === id);
      const pane = def.kind === "pane" ? nextPane() : 0;
      const specs = builders[id](bars, pane);
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
      const entry = { series, def, legendLines: legendLines(id, specs),
                      data: specs.map((s) => s.data) };
      if (def.kind === "pane") {
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
      if (a.legend) {
        try { a.series[0].getPane().detachPrimitive(a.legend); } catch {}
      }
      a.series.forEach((s) => chart.removeSeries(s));
      active.delete(id);
      refreshOverlayLegend();
    }

    function recomputeAll(bars) {
      for (const [id, a] of active) {
        const specs = builders[id](bars, 0 /* pane already exists */);
        specs.forEach((s, i) => a.series[i] && a.series[i].setData(s.data));
        a.data = specs.map((s) => s.data);
        a.legendLines = legendLines(id, specs);
        if (a.legend) a.legend.setLines(a.legendLines);
      }
      refreshOverlayLegend();
    }

    /** Re-apply theme colours to every live series, then repaint the data
     *  (MACD's histogram carries per-point colours, so data must rebuild). */
    function retheme(bars) {
      for (const [id, a] of active) {
        const specs = builders[id](bars, 0);
        specs.forEach((s, i) => {
          if (a.series[i] && s.opts && s.opts.color) {
            a.series[i].applyOptions({ color: s.opts.color });
          }
        });
      }
      recomputeAll(bars);
    }

    return {
      CATALOG, active,
      toggle(id, bars) { active.has(id) ? remove(id) : add(id, bars); },
      remove, recomputeAll, retheme,
      isActive: (id) => active.has(id),
      /** Current value + value at `fromTime`, for the chat context envelope.
       *  MACD reports its line (index 1; index 0 is the histogram). */
      snapshot(fromTime) {
        const out = [];
        for (const [id, a] of active) {
          const arr = a.data[id === "macd" ? 1 : 0] || [];
          if (!arr.length) continue;
          let at = null;
          for (const p of arr) if (p.time >= fromTime) { at = p.value; break; }
          out.push({ label: a.def.label, now: arr[arr.length - 1].value, at });
        }
        return out;
      },
    };
  }

  return { createManager, CATALOG };
})();
