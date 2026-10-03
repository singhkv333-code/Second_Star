/* Charto preview — the Chart widget: as many more charts as you want.
 *
 * The page's own chart is the one you draw on and talk to the chat about.
 * These are the others: a reference index beside it, a sector peer, a
 * crypto pair on another clock. Each has its own symbol, interval and chart
 * type, reads the same /bars the main chart reads, and keeps its last bar
 * current by re-reading the tail every 15 seconds while it is on screen.
 * The candle colours are the chart's own settings, so green means the same
 * thing in every tile.
 *
 * "Open on the main chart" makes it the page's instrument; a click on the
 * plot does nothing else — a reference chart is for looking.
 */
"use strict";

(() => {
  if (typeof Dock === "undefined" || typeof LightweightCharts === "undefined") return;
  const { API, esc, ic, num, pct, dir } = WKit;
  const IVS = [["5m", "5m"], ["15m", "15m"], ["1h", "1H"], ["1d", "1D"], ["1w", "1W"]];
  const TYPES = [["candles", "Candles"], ["hollow", "Hollow candles"], ["bars", "Bars"], ["line", "Line"],
                 ["area", "Area"], ["baseline", "Baseline"]];
  const OHLC = new Set(["candles", "hollow", "bars"]);
  const POLL_MS = 15_000;

  const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
  const tzOf = (sym) => (/(USDT|-USD)$/.test(sym) ? 0 : 19800);

  function defaultSymbol(page) {
    // a second chart of the same thing is a mirror, so start on something else
    const lists = typeof Panels !== "undefined" && Panels.watching ? Panels.watching() : [];
    return lists.find((s) => s !== page && !/^NIFTY|SENSEX/.test(s)) || (page === "TCS" ? "INFY" : "TCS");
  }

  function mount(host, ctx) {
    // a new chart starts on something other than the page's symbol, unless
    // it has been told to follow the chart or to join a group
    if (!ctx.cfg.pin && !ctx.cfg.link) ctx.setCfg({ pin: defaultSymbol(ctx.pageSymbol()) });
    let chart = null, series = null, vol = null, timer = 0, bars = [], busy = false;
    host.innerHTML =
      `<div class="mc-head">` +
        `<button type="button" class="side-pick mc-sym" data-mc="sym" title="Change the instrument"></button>` +
        `<span class="mc-px"><b></b><em></em></span>` +
        `<span class="sh-gap"></span>` +
        `<div class="dk-seg mc-iv">${IVS.map(([v, l]) => `<button type="button" data-iv="${v}">${l}</button>`).join("")}</div>` +
        `<button type="button" class="sh-btn i" data-mc="type" title="Chart type">${ic("lineChart")}</button>` +
        `<button type="button" class="sh-btn i" data-mc="main" title="Open on the main chart">${ic("externalLink")}</button>` +
      `</div>` +
      `<div class="mc-plot"></div><div class="mc-msg" hidden></div>`;
    const $ = (s) => host.querySelector(s);
    const plot = $(".mc-plot"), msg = $(".mc-msg");

    const sym = () => ctx.symbol().toUpperCase();
    const iv = () => ctx.linkedIv() || ctx.cfg.iv || "1d";
    const kind = () => ctx.cfg.kind || "candles";

    function theme() {
      return {
        layout: { background: { type: "solid", color: "transparent" }, textColor: css("--foreground") || "#111",
                  fontFamily: getComputedStyle(document.body).fontFamily, fontSize: 11, attributionLogo: false },
        grid: { vertLines: { visible: ctx.cfg.grid !== false, color: css("--border-soft") || "#eee" },
                horzLines: { visible: ctx.cfg.grid !== false, color: css("--border-soft") || "#eee" } },
        rightPriceScale: { borderVisible: false, mode: { log: 1, pct: 2 }[ctx.cfg.scale] || 0 },
        timeScale: { borderVisible: false, timeVisible: !["1d", "1w"].includes(iv()) },
        crosshair: { mode: ctx.cfg.magnet ? 1 : 0 },
      };
    }
    function build() {
      if (chart) chart.remove();
      chart = LightweightCharts.createChart(plot, { autoSize: true, ...theme() });
      const up = css("--up") || "#089981", down = css("--down") || "#f23645";
      const common = { priceLineVisible: ctx.cfg.priceLine !== false, lastValueVisible: true };
      if (kind() === "candles") {
        series = chart.addSeries(LightweightCharts.CandlestickSeries,
          { ...common, upColor: up, downColor: down, wickUpColor: up, wickDownColor: down, borderVisible: false });
      } else if (kind() === "hollow") {
        series = chart.addSeries(LightweightCharts.CandlestickSeries,
          { ...common, upColor: "rgba(0,0,0,0)", downColor: down, borderUpColor: up, borderDownColor: down,
            wickUpColor: up, wickDownColor: down, borderVisible: true });
      } else if (kind() === "bars") {
        series = chart.addSeries(LightweightCharts.BarSeries, { ...common, upColor: up, downColor: down, thinBars: false });
      } else if (kind() === "baseline") {
        series = chart.addSeries(LightweightCharts.BaselineSeries, { ...common, lineWidth: 2,
          topLineColor: up, bottomLineColor: down,
          topFillColor1: "rgba(8,153,129,.18)", topFillColor2: "rgba(8,153,129,0)",
          bottomFillColor1: "rgba(242,54,69,0)", bottomFillColor2: "rgba(242,54,69,.18)" });
      } else if (kind() === "line") {
        series = chart.addSeries(LightweightCharts.LineSeries, { ...common, color: css("--foreground"), lineWidth: 2 });
      } else {
        series = chart.addSeries(LightweightCharts.AreaSeries, { ...common, lineColor: css("--foreground"), lineWidth: 2,
          topColor: "rgba(120,120,120,.18)", bottomColor: "rgba(120,120,120,0)" });
      }
      vol = ctx.cfg.volume === false ? null
        : chart.addSeries(LightweightCharts.HistogramSeries, { priceFormat: { type: "volume" }, priceScaleId: "v", lastValueVisible: false, priceLineVisible: false });
      if (vol) chart.priceScale("v").applyOptions({ scaleMargins: { top: .82, bottom: 0 } });
    }
    const point = (b) => OHLC.has(kind())
      ? { time: b.t + tzOf(sym()), open: b.o, high: b.h, low: b.l, close: b.c }
      : { time: b.t + tzOf(sym()), value: b.c };
    const vpoint = (b) => ({ time: b.t + tzOf(sym()), value: b.v || 0,
      color: b.c >= b.o ? "rgba(8,153,129,.35)" : "rgba(242,54,69,.35)" });

    async function load(full) {
      if (busy) return;
      busy = true;
      const want = sym(), wantIv = iv();
      try {
        const qs = new URLSearchParams({ symbol: want, interval: wantIv, limit: full ? String(ctx.cfg.depth || 800) : "3" });
        const r = await Net.get(`${API}/bars?${qs}`, typeof Auth !== "undefined" ? { headers: Auth.headers() } : undefined);
        const d = await r.json();
        if (want !== sym() || wantIv !== iv()) return;
        if (!r.ok || !d.bars) throw new Error(d.error || `HTTP ${r.status}`);
        if (full) {
          bars = d.bars;
          if (!bars.length) throw new Error(`No ${wantIv} bars are stored for ${want}.`);
          if (kind() === "baseline") series.applyOptions({ baseValue: { type: "price", price: bars[0].c } });
          series.setData(bars.map(point));
          if (vol) vol.setData(bars.map(vpoint));
          chart.timeScale().fitContent();
          msg.hidden = true;
        } else {
          for (const b of d.bars) { series.update(point(b)); if (vol) vol.update(vpoint(b)); }
          const last = d.bars[d.bars.length - 1];
          if (last) bars[bars.length - 1] = last;
        }
        paintPx();
      } catch (e) {
        if (full) { msg.hidden = false; msg.textContent = e.message || String(e); }
      } finally { busy = false; }
    }

    async function paintPx() {
      try {
        const r = await Net.get(`${API}/quotes?symbols=${encodeURIComponent(sym())}`);
        const q = ((await r.json()).quotes || [])[0] || {};
        $(".mc-px b").textContent = q.last == null ? "—" : num(q.last, Math.abs(q.last) < 1 ? 4 : 2);
        const em = $(".mc-px em");
        em.textContent = q.change_pct == null ? "" : pct(q.change_pct);
        em.className = dir(q.change_pct);
      } catch { /* the price line simply stays as it was */ }
    }

    function paintHead() {
      $(".mc-sym").innerHTML = `${esc(sym())}${ic("chevronDown", "")}`;
      for (const b of host.querySelectorAll("[data-iv]")) b.classList.toggle("on", b.dataset.iv === iv());
      ctx.setTitle(sym());
    }

    function reload() { paintHead(); build(); load(true); }

    host.addEventListener("click", (e) => {
      const b = e.target.closest("[data-iv]");
      if (b) {
        // in a group the interval is the group's, so every chart in it moves
        if (ctx.linkedIv() !== null || /^[1-4]$/.test(ctx.link())) ctx.setLinkedIv(b.dataset.iv);
        ctx.setCfg({ iv: b.dataset.iv });
        return;
      }
      const a = e.target.closest("[data-mc]");
      if (!a) return;
      e.stopPropagation();
      if (a.dataset.mc === "sym") {
        // in a group, choosing a symbol here moves the whole group
        return Universe.open({ anchor: a, current: sym(),
          onPick: (s) => /^[1-4]$/.test(ctx.link()) ? ctx.pick(s) : ctx.setCfg({ pin: s, link: "pin" }) });
      }
      if (a.dataset.mc === "type") {
        return ctx.menu(a, [{ head: "Chart type" }, ...TYPES.map(([id, label]) => ({ id, label, on: kind() === id }))],
          (k) => ctx.setCfg({ kind: k }));
      }
      if (a.dataset.mc === "main") return ctx.openSymbol(sym());
    });
    if (typeof Theme !== "undefined" && Theme.onChange) Theme.onChange(() => chart && chart.applyOptions(theme()));

    const poll = () => {
      clearInterval(timer);
      timer = setInterval(() => { if (document.visibilityState === "visible") load(false); }, Number(ctx.cfg.refresh) || POLL_MS);
    };
    return {
      show() { if (!chart) reload(); poll(); },
      hide() { clearInterval(timer); },
      config(cfg, patch) {
        const keys = Object.keys(patch).filter((k) => k !== "title");
        if (!keys.length) return;
        if (keys.every((k) => k === "refresh")) return poll();
        // grid, scale and crosshair are options on the live chart; the rest rebuild it
        if (chart && keys.every((k) => ["grid", "scale", "magnet"].includes(k))) return chart.applyOptions(theme());
        reload();
      },
      ask: () => `Compare ${sym()} with ${ctx.pageSymbol()} on the ${iv()} chart: how have they moved relative to each other, and what does that say?`,
    };
  }

  Dock.register({
    type: "chart", title: "Chart", icon: "candles", hue: "blue", group: "Market",
    desc: "Another chart — any symbol, any interval", zone: "right", minW: 280, mount, linkable: true,
    defaults: { iv: "1d", kind: "candles" },
    settings: [
      { section: "Chart" },
      { key: "iv", label: "Interval", def: "1d", options: IVS.map(([v, l]) => ({ v, label: l })), hint: "In a link group, the group's interval" },
      { key: "kind", label: "Style", def: "candles", kind: "select", options: TYPES.map(([v, label]) => ({ v, label })) },
      { key: "scale", label: "Price scale", def: "normal", options: [{ v: "normal", label: "Normal" }, { v: "log", label: "Log" }, { v: "pct", label: "Percent" }] },
      { key: "depth", label: "History", def: 800, options: [{ v: 300, label: "300 bars" }, { v: 800, label: "800" }, { v: 2000, label: "2,000" }] },
      { section: "Display" },
      { key: "volume", label: "Volume", kind: "toggle", def: true },
      { key: "grid", label: "Grid lines", kind: "toggle", def: true },
      { key: "priceLine", label: "Last price line", kind: "toggle", def: true },
      { key: "magnet", label: "Snap the crosshair to prices", kind: "toggle", def: false },
      { section: "Data" },
      { key: "refresh", label: "Update every", def: 15000, options: [{ v: 5000, label: "5s" }, { v: 15000, label: "15s" }, { v: 60000, label: "1m" }] },
    ],
  });
})();
