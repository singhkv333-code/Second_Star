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
  const TYPES = [["candles", "Candles"], ["line", "Line"], ["area", "Area"]];
  const POLL_MS = 15_000;

  const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
  const tzOf = (sym) => (/(USDT|-USD)$/.test(sym) ? 0 : 19800);

  function defaultSymbol(page) {
    // a second chart of the same thing is a mirror, so start on something else
    const lists = typeof Panels !== "undefined" && Panels.watching ? Panels.watching() : [];
    return lists.find((s) => s !== page && !/^NIFTY|SENSEX/.test(s)) || (page === "TCS" ? "INFY" : "TCS");
  }

  function mount(host, ctx) {
    if (!ctx.cfg.pin) ctx.setCfg({ pin: defaultSymbol(ctx.pageSymbol()) });
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

    const sym = () => (ctx.cfg.pin || ctx.pageSymbol()).toUpperCase();
    const iv = () => ctx.cfg.iv || "1d";
    const kind = () => ctx.cfg.kind || "candles";

    function theme() {
      return {
        layout: { background: { type: "solid", color: "transparent" }, textColor: css("--foreground") || "#111",
                  fontFamily: getComputedStyle(document.body).fontFamily, fontSize: 11, attributionLogo: false },
        grid: { vertLines: { color: css("--border-soft") || "#eee" }, horzLines: { color: css("--border-soft") || "#eee" } },
        rightPriceScale: { borderVisible: false }, timeScale: { borderVisible: false, timeVisible: !["1d", "1w"].includes(iv()) },
        crosshair: { mode: 0 },
      };
    }
    function build() {
      if (chart) chart.remove();
      chart = LightweightCharts.createChart(plot, { autoSize: true, ...theme() });
      const up = css("--up") || "#089981", down = css("--down") || "#f23645";
      if (kind() === "candles") {
        series = chart.addSeries(LightweightCharts.CandlestickSeries,
          { upColor: up, downColor: down, wickUpColor: up, wickDownColor: down, borderVisible: false });
      } else if (kind() === "line") {
        series = chart.addSeries(LightweightCharts.LineSeries, { color: css("--foreground"), lineWidth: 2 });
      } else {
        series = chart.addSeries(LightweightCharts.AreaSeries, { lineColor: css("--foreground"), lineWidth: 2,
          topColor: "rgba(120,120,120,.18)", bottomColor: "rgba(120,120,120,0)" });
      }
      vol = chart.addSeries(LightweightCharts.HistogramSeries, { priceFormat: { type: "volume" }, priceScaleId: "v" });
      chart.priceScale("v").applyOptions({ scaleMargins: { top: .82, bottom: 0 } });
    }
    const point = (b) => kind() === "candles"
      ? { time: b.t + tzOf(sym()), open: b.o, high: b.h, low: b.l, close: b.c }
      : { time: b.t + tzOf(sym()), value: b.c };
    const vpoint = (b) => ({ time: b.t + tzOf(sym()), value: b.v || 0,
      color: b.c >= b.o ? "rgba(8,153,129,.35)" : "rgba(242,54,69,.35)" });

    async function load(full) {
      if (busy) return;
      busy = true;
      const want = sym(), wantIv = iv();
      try {
        const qs = new URLSearchParams({ symbol: want, interval: wantIv, limit: full ? "800" : "3" });
        const r = await Net.get(`${API}/bars?${qs}`, typeof Auth !== "undefined" ? { headers: Auth.headers() } : undefined);
        const d = await r.json();
        if (want !== sym() || wantIv !== iv()) return;
        if (!r.ok || !d.bars) throw new Error(d.error || `HTTP ${r.status}`);
        if (full) {
          bars = d.bars;
          if (!bars.length) throw new Error(`No ${wantIv} bars are stored for ${want}.`);
          series.setData(bars.map(point));
          vol.setData(bars.map(vpoint));
          chart.timeScale().fitContent();
          msg.hidden = true;
        } else {
          for (const b of d.bars) { series.update(point(b)); vol.update(vpoint(b)); }
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
      if (b) { ctx.setCfg({ iv: b.dataset.iv }); return; }
      const a = e.target.closest("[data-mc]");
      if (!a) return;
      e.stopPropagation();
      if (a.dataset.mc === "sym") {
        return Universe.open({ anchor: a, current: sym(), onPick: (s) => ctx.setCfg({ pin: s }) });
      }
      if (a.dataset.mc === "type") {
        return ctx.menu(a, [{ head: "Chart type" }, ...TYPES.map(([id, label]) => ({ id, label, on: kind() === id }))],
          (k) => ctx.setCfg({ kind: k }));
      }
      if (a.dataset.mc === "main") return ctx.openSymbol(sym());
    });
    if (typeof Theme !== "undefined" && Theme.onChange) Theme.onChange(() => chart && chart.applyOptions(theme()));

    return {
      show() {
        if (!chart) reload();
        clearInterval(timer);
        timer = setInterval(() => { if (document.visibilityState === "visible") load(false); }, POLL_MS);
      },
      hide() { clearInterval(timer); },
      config(cfg, patch) { if ("pin" in patch || "iv" in patch || "kind" in patch) reload(); },
      ask: () => `Compare ${sym()} with ${ctx.pageSymbol()} on the ${iv()} chart: how have they moved relative to each other, and what does that say?`,
    };
  }

  Dock.register({
    type: "chart", title: "Chart", icon: "candles", hue: "blue", group: "Market",
    desc: "Another chart — any symbol, any interval", zone: "right", minW: 280, mount,
    defaults: { iv: "1d", kind: "candles" },
  });
})();
