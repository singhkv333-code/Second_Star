/* Comparisons retain the primary chart style and rebase real OHLC/close data.
 * The ordinary price chart stays mounted beneath the comparison view. */
"use strict";
const ChartCompare = (() => {
  const LWC = window.LightweightCharts;
  const MODES = ["percent", "indexed", "price"];
  const COLORS = ["#009b83", "#3478c4", "#ad5b8e", "#b97e24", "#685bc7"];
  const WIRE = { D: "1d", W: "1w", M: "1mo" };
  const API = ["localhost", "127.0.0.1"].includes(location.hostname) ? "http://127.0.0.1:5174" : "";

  // Daily/weekly/monthly comparisons align trading dates. Intraday comparisons
  // align actual instants and display them in the primary security's timezone.
  function alignedTime(rawTime, symbol, interval, primarySymbol) {
    const iv = WIRE[interval] || interval;
    if (["1d", "1w", "1mo"].includes(iv)) {
      return Math.floor((rawTime + Sym.of(symbol).tz) / 86400) * 86400;
    }
    return rawTime + Sym.of(primarySymbol).tz;
  }

  const scaled = (price, base, mode) => mode === "price" ? price : mode === "indexed" ? 100 * price / base : 100 * (price / base - 1);

  function plotPoints(row, type, mode) {
    if (!["candles", "bars"].includes(type)) return row.points;
    return row.points.map(({ time }) => {
      const bar = row.ohlc.get(time);
      if (!bar || ![bar.open, bar.high, bar.low, bar.close].every(Number.isFinite)) return { time };
      return { time, ...Object.fromEntries(["open", "high", "low", "close"].map((key) => [key, scaled(bar[key], row.base, mode)])) };
    });
  }

  // The axis stays in normalized units; this native axis label also identifies
  // the actual latest plotted close. Native placement separates nearby labels.
  function axisTag(host, getRow, getMode, color) {
    let requestUpdate = null;
    const last = () => getRow()?.points.findLast((p) => Number.isFinite(p.value));
    const view = {
      coordinate: () => host.priceToCoordinate(last()?.value) ?? -10000,
      text: () => {
        const row = getRow(), point = last();
        if (!row || !point) return "";
        const raw = Sym.of(row.symbol).price(row.raw.get(point.time));
        const mode = getMode();
        return mode === "price" ? raw : `${raw} · ${mode === "percent" ? `${point.value > 0 ? "+" : ""}${point.value.toFixed(2)}%` : point.value.toFixed(2)}`;
      },
      textColor: () => "#fff", backColor: () => color,
      visible: () => !!last(), tickVisible: () => true,
    };
    host.attachPrimitive({ attached: (params) => { requestUpdate = params.requestUpdate; },
      detached: () => { requestUpdate = null; }, priceAxisViews: () => [view] });
    return () => requestUpdate?.();
  }

  function normalize(input, mode, from = -Infinity) {
    const rows = input.map((row) => ({ ...row, points: new Map(row.points
      .filter((p) => Number.isFinite(p.time) && Number.isFinite(p.value) && p.value > 0)
      .map((p) => [p.time, p.value])) }));
    if (!rows.length || rows.some((r) => !r.points.size)) throw new Error("Price history is unavailable for one of these securities.");
    const common = [...rows[0].points.keys()].filter((t) => rows.every((r) => r.points.has(t))).sort((a, b) => a - b);
    if (common.length < 2) throw new Error("These securities do not have enough shared history at this interval. Remove a security or choose another interval.");
    // A saved viewport can be newer than another security's available data.
    // Show the latest real shared window instead of asking the user to guess it.
    const requested = common.findIndex((t) => t >= from);
    const shifted = requested < 0 || requested === common.length - 1;
    const start = common[shifted ? Math.max(0, common.length - 140) : requested];
    const end = Math.min(...rows.map((r) => Math.max(...r.points.keys())));
    const times = [...new Set(rows.flatMap((r) => [...r.points.keys()]))].filter((t) => t >= start && t <= end).sort((a, b) => a - b);
    return { start, end, shifted, rows: rows.map((r) => {
      const base = r.points.get(start);
      return { symbol: r.symbol, base, raw: r.points, source: r.source,
        ohlc: new Map(input.find((row) => row.symbol === r.symbol).points.map((p) => [p.time, p])),
        points: times.map((time) => {
          const price = r.points.get(time);
          if (price == null) return { time };
          const value = scaled(price, base, mode);
          return { time, value };
        }) };
    }) };
  }

  function historyTo(end, symbol, interval, primarySymbol) {
    const iv = WIRE[interval] || interval;
    const duration = /^(\d+)(m|h)$/.exec(iv);
    // Include the full last OHLC bucket. Adding one second to an intraday
    // start would cut its close down to the first minute of that candle.
    return duration
      ? end - Sym.of(primarySymbol).tz + Number(duration[1]) * (duration[2] === "h" ? 3600 : 60)
      : end - Sym.of(symbol).tz + 1;
  }

  async function sharedHistories(primary, symbols, getHistory) {
    let rows = [primary, ...await Promise.all(symbols.map((symbol) => getHistory(symbol, primary.points.at(-1).time)))];
    const end = Math.min(...rows.map((row) => row.points.at(-1).time));
    // Load newer securities back to the oldest last observation. Equal bar
    // counts alone do not mean equal dates, especially across sparse histories.
    rows = await Promise.all(rows.map((row) => row.points.at(-1).time > end ? getHistory(row.symbol, end) : row));
    return rows;
  }

  function advanceHistory(row, bar, symbol, interval, primarySymbol) {
    if (!bar || !row || ![bar.t, bar.o, bar.h, bar.l, bar.c].every(Number.isFinite) || bar.c <= 0) return false;
    const time = alignedTime(bar.t, symbol, interval, primarySymbol);
    const last = row.points.at(-1);
    if (last && time < last.time) return false;
    const point = { time, value: bar.c, open: bar.o, high: bar.h, low: bar.l, close: bar.c };
    if (last?.time === time) row.points[row.points.length - 1] = point;
    else row.points.push(point);
    return true;
  }

  // One comparison subscription per symbol across chart widgets, released on close.
  const feeds = new Map();
  function listen(symbol, callback, onError) {
    let feed = feeds.get(symbol);
    if (!feed) {
      const stream = new EventSource(`${API}/stream?symbol=${encodeURIComponent(symbol)}`);
      feed = { stream, listeners: new Set(), errors: new Set() }; feeds.set(symbol, feed);
      stream.onmessage = (event) => {
        let payload; try { payload = JSON.parse(event.data); } catch { return; }
        if (payload?.type === "bar") for (const fn of feed.listeners) { try { fn(payload); } catch (error) { console.error("live bars:", error); } }
      };
      stream.onerror = () => { for (const fn of feed.errors) fn(); };
    }
    feed.listeners.add(callback); if (onError) feed.errors.add(onError);
    return () => { feed.listeners.delete(callback); if (onError) feed.errors.delete(onError); if (!feed.listeners.size) { feed.stream.close(); feeds.delete(symbol); } };
  }

  function create(env) {
    let symbols = [], mode = "percent", panel = null, canvas = null, plot = null, stopSizing = null;
    let revision = 0, destroyed = false, loadedKey = "", anchor = null, pending = null;
    let data = [], series = [], tags = [], poll = null, rendered = null, stopLayout = null, stopSettings = null;
    const setting = (fn) => ChartSettings.withTarget ? ChartSettings.withTarget(env.settings || { slot: 0 }, fn) : fn();
    const selectedType = () => setting(() => ChartSettings.getType());
    const chartType = () => selectedType() === "footprint" ? "candles" : selectedType();
    let subscriptions = new Map();
    function syncFeeds() {
      const wanted = panel && document.visibilityState === "visible" && panel.getBoundingClientRect().width
        ? [baseSymbol(), ...symbols] : [];
      for (const [symbol, stop] of subscriptions) if (!wanted.includes(symbol)) { stop(); subscriptions.delete(symbol); }
      for (const symbol of wanted) if (!subscriptions.has(symbol)) subscriptions.set(symbol, listen(symbol, (payload) => {
        if (destroyed || pending || loadedKey !== currentKey() || !panel?.getBoundingClientRect().width) return;
        const row = data.find((r) => r.symbol === symbol);
        if (advanceHistory(row, payload.bars?.[interval()], symbol, interval(), baseSymbol())) render(false);
      }));
    }
    const visibilityChanged = () => syncFeeds();
    document.addEventListener("visibilitychange", visibilityChanged);
    document.addEventListener("pivot:dock-layout", visibilityChanged);
    const save = () => env.save?.({ symbols: symbols.slice(), mode, anchor, symbol: baseSymbol(), interval: interval() });
    const baseSymbol = () => env.getSymbol();
    const interval = () => WIRE[env.getInterval()] || env.getInterval();
    const basePoints = () => env.getBars().map((b) => ({
      time: alignedTime(b.time - Sym.of(baseSymbol()).tz, baseSymbol(), interval(), baseSymbol()), value: b.close, open: b.open, high: b.high, low: b.low, close: b.close,
    }));
    const currentKey = () => `${baseSymbol()}|${interval()}|${symbols.join(",")}`;
    function destroyPlot() {
      stopSizing?.(); stopSizing = null;
      stopLayout?.(); stopLayout = null;
      plot?.remove(); plot = null; series = []; tags = [];
    }
    function close() {
      revision++; pending = null;
      clearInterval(poll); poll = null;
      stopSettings?.(); stopSettings = null;
      destroyPlot(); panel?.remove(); panel = null; canvas = null;
      env.root.classList.remove("has-comparison");
      for (const stop of subscriptions.values()) stop(); subscriptions.clear();
      symbols = []; data = []; loadedKey = ""; anchor = null; rendered = null; mode = "percent";
      save();
    }
    function ensurePanel() {
      if (panel) return;
      panel = document.createElement("section");
      panel.className = "comparison-view";
      panel.setAttribute("aria-label", "Security comparison");
      panel.innerHTML = `<div class="comparison-header"><div class="comparison-legend"></div><div class="comparison-controls">
        <span class="comparison-scale"><select aria-label="Comparison scale"><option value="percent">% Change</option><option value="indexed">Indexed to 100</option><option value="price">Price panes</option></select>${Icons.svg("chevronDown", "xs")}</span>
        <button type="button" data-compare="add" aria-label="Add security" title="Add a security to compare">${Icons.svg("plus", "xs")}<span>Add</span></button>
        <button type="button" data-compare="rebase" aria-label="Rebase to visible start" title="Set the comparison baseline to the visible start">${Icons.svg("rotateCw", "xs")}</button>
        <button type="button" data-compare="close" aria-label="Back to price chart" title="Back to price chart">${Icons.svg("x", "xs")}</button>
        </div></div><p class="comparison-period"></p><div class="comparison-canvas"></div><p class="comparison-status" role="status"></p>`;
      canvas = panel.querySelector(".comparison-canvas");
      panel.querySelector("select").value = mode;
      panel.querySelector("select").addEventListener("change", (e) => { mode = e.target.value; save(); render(); });
      panel.addEventListener("click", (e) => {
        e.stopPropagation();
        const remove = e.target.closest("[data-remove-symbol]");
        if (remove) {
          symbols = symbols.filter((s) => s !== remove.dataset.removeSymbol);
          if (!symbols.length) return close();
          anchor = null; save(); refresh(true); return;
        }
        const action = e.target.closest("[data-compare]")?.dataset.compare;
        if (action === "close") close();
        else if (action === "add") add(panel.querySelector('[data-compare="add"]'));
        else if (action === "rebase") {
          anchor = Number(plot?.timeScale().getVisibleRange()?.from) || null;
          render(); save();
        }
      });
      env.root.appendChild(panel);
      env.root.classList.add("has-comparison");
      paintLegend();
      stopSettings = ChartSettings.onChange(() => { if (panel && !destroyed) render(); });
      Universe.load().then(() => { if (panel && !destroyed) paintLegend(); });
      syncFeeds();
      poll = setInterval(() => { syncFeeds(); if (panel?.getBoundingClientRect().width && document.visibilityState === "visible") refresh(true); }, 30000);
    }
    function message(text) {
      if (panel) panel.querySelector(".comparison-status").textContent = text;
    }
    function add(button) {
      if (symbols.length >= 4) { ensurePanel(); message("Compare up to five securities, including this chart. Remove one to add another."); return; }
      Universe.open({ anchor: button, current: baseSymbol(), onPick: (symbol) => {
        const s = String(symbol).toUpperCase();
        if (s === baseSymbol() || symbols.includes(s)) return;
        symbols.push(s); anchor = null;
        ensurePanel(); paintLegend(); save(); refresh(true);
      } });
    }
    async function refresh(force = false) {
      if (destroyed || !symbols.length || !env.getBars().length) return;
      ensurePanel(); syncFeeds();
      const key = currentKey();
      if (pending === key) return;
      if (!force && loadedKey === key) {
        if (data.length) render(false);
        return;
      }
      if (loadedKey && loadedKey.split("|").slice(0, 2).join("|") !== key.split("|").slice(0, 2).join("|")) anchor = null;
      const ticket = ++revision;
      if (loadedKey && loadedKey !== key) { destroyPlot(); data = []; rendered = null; paintLegend(); }
      pending = key;
      message("Loading comparison…");
      try {
        const bars = env.getBars();
        const primarySymbol = baseSymbol(), iv = interval();
        const limit = Math.min(10000, Math.max(200, bars.length));
        const rows = await sharedHistories({ symbol: primarySymbol, points: basePoints() }, symbols, async (symbol, end) => {
          const query = new URLSearchParams({ symbol, interval: iv, limit: String(limit), to: String(historyTo(end, symbol, iv, primarySymbol)) });
          const response = await Net.get(`${API}/bars?${query}`, typeof Auth !== "undefined" ? { headers: Auth.headers() } : undefined);
          if (!response.ok) throw new Error(`${symbol}: price history request failed (${response.status}).`);
          const payload = await response.json();
          if (!payload.bars?.length) throw new Error(`${symbol}: price history is unavailable for this interval.`);
          return { symbol, source: payload.source, points: payload.bars.map((b) => ({ time: alignedTime(b.t, symbol, iv, primarySymbol), value: b.c, open: b.o, high: b.h, low: b.l, close: b.c })) };
        });
        if (destroyed || ticket !== revision) return;
        data = rows;
        loadedKey = key;
        render();
      } catch (e) {
        if (!destroyed && ticket === revision) message(e.message || "Comparison could not load. Try another security or interval.");
      } finally { if (ticket === revision) pending = null; }
    }
    function initialAnchor() {
      const range = env.chart.timeScale().getVisibleRange();
      if (typeof range?.from !== "number") return null;
      return alignedTime(range.from - Sym.of(baseSymbol()).tz, baseSymbol(), interval(), baseSymbol());
    }
    function render(rebuild = true) {
      if (!panel || !data.length) return;
      let normalized;
      try { normalized = normalize(data, mode, anchor ?? initialAnchor() ?? -Infinity); }
      catch (e) { destroyPlot(); rendered = null; paintLegend(); message(e.message); return; }
      anchor = normalized.start;
      rendered = normalized;
      message("");
      const previousRange = plot?.timeScale().getVisibleRange();
      if (rebuild || !plot) {
        destroyPlot();
        const P = Theme.palette;
        plot = LWC.createChart(canvas, {
          autoSize: true,
          layout: { background: { color: P.canvasBg || P.chartBg }, textColor: P.axisText, fontSize: 11, attributionLogo: false, panes: { separatorColor: P.separator, enableResize: true } },
          grid: { vertLines: { color: P.grid }, horzLines: { color: P.grid } },
          rightPriceScale: { borderColor: P.border },
          timeScale: { borderColor: P.border, timeVisible: !["1d", "1w", "1mo"].includes(interval()), rightOffset: 4 },
          crosshair: { mode: LWC.CrosshairMode.Normal },
        });
        stopSizing = ChartSize.watch(plot, canvas);
        panel.dataset.chartType = chartType();
        series = normalized.rows.map((row, i) => {
          const host = i === 0 ? (selectedType() === "footprint"
            ? plot.addSeries(LWC.CandlestickSeries, { upColor: P.up, downColor: P.down, wickUpColor: P.up, wickDownColor: P.down, borderVisible: false })
            : setting(() => ChartSettings.makeSeries(plot)))
            : plot.addSeries(LWC.LineSeries, { color: COLORS[i], lineWidth: 2 }, mode === "price" ? i : 0);
          host.applyOptions({ title: "", priceLineVisible: false, lastValueVisible: false,
            priceFormat: { type: "custom", minMove: 0.01, formatter: (value) => mode === "percent" ? `${value.toFixed(2)}%` : mode === "indexed" ? value.toFixed(2) : Sym.of(row.symbol).price(value) } });
          tags[i] = axisTag(host, () => rendered?.rows[i], () => mode, COLORS[i]);
          return host;
        });
        let layoutFrame = 0, lastMargin = -1;
        const updateLayout = () => {
          layoutFrame = 0;
          if (!plot || !panel) return;
          panel.style.setProperty("--comparison-axis-w", `${plot.priceScale("right").width()}px`);
          const height = plot.panes()[0]?.getHeight() || canvas.clientHeight;
          const header = panel.querySelector(".comparison-header").getBoundingClientRect().height;
          const top = Math.min(0.45, Math.max(0.10, (header + 18) / Math.max(1, height)));
          if (Math.abs(top - lastMargin) > 0.001) { lastMargin = top; series[0].priceScale().applyOptions({ scaleMargins: { top, bottom: 0.08 } }); }
        };
        const scheduleLayout = () => { if (!layoutFrame) layoutFrame = requestAnimationFrame(updateLayout); };
        const layoutObserver = new ResizeObserver(scheduleLayout);
        layoutObserver.observe(canvas); layoutObserver.observe(panel.querySelector(".comparison-header"));
        plot.timeScale().subscribeSizeChange(scheduleLayout);
        scheduleLayout();
        stopLayout = () => { layoutObserver.disconnect(); cancelAnimationFrame(layoutFrame); };
        if (mode !== "price") series[0].createPriceLine({ price: mode === "indexed" ? 100 : 0, color: P.axisText, lineWidth: 1, lineStyle: LWC.LineStyle.Dotted, axisLabelVisible: true, title: "" });
        plot.subscribeCrosshairMove((event) => {
          rendered.rows.forEach((row, i) => {
            const value = event.time != null ? row.raw.get(Number(event.time)) : [...row.raw.entries()].filter(([time]) => time <= rendered.end).at(-1)?.[1];
            const output = panel?.querySelectorAll(".comparison-value")[i];
            if (output) paintValue(output, row, value);
          });
        });
      }
      normalized.rows.forEach((row, i) => {
        series[i].setData(i === 0 ? plotPoints(row, chartType(), mode) : row.points);
        tags[i]?.();
      });
      paintLegend();
      {
        const dateOptions = { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" };
        const date = (time) => new Date(time * 1000).toLocaleDateString("en-GB", dateOptions);
        const period = panel.querySelector(".comparison-period");
        const startDate = new Date(normalized.start * 1000), endDate = new Date(normalized.end * 1000);
        const firstDate = startDate.toLocaleDateString("en-GB", { ...dateOptions, year: startDate.getUTCFullYear() === endDate.getUTCFullYear() ? undefined : "numeric" });
        period.textContent = date(normalized.start) === date(normalized.end) ? date(normalized.end) : `${firstDate} – ${date(normalized.end)}`;
        const exact = (time) => new Date(time * 1000).toLocaleString("en-IN", { ...dateOptions, hour: "2-digit", minute: "2-digit" });
        period.title = `${mode === "price" ? "Closing prices on separate scales" : mode === "indexed" ? "Each security starts at 100" : "Each security starts at 0%"}. Shared period: ${exact(normalized.start)} to ${exact(normalized.end)}${["1d", "1w", "1mo"].includes(interval()) ? ". Aligned by trading date." : " (chart timezone)."}`;
        if (rebuild && previousRange && Number(previousRange.from) >= normalized.start && Number(previousRange.from) < normalized.end) plot.timeScale().setVisibleRange({ from: previousRange.from, to: Math.min(Number(previousRange.to), normalized.end) });
        else if (rebuild) plot.timeScale().fitContent();
      }
      if (rebuild) save();
    }
    function paintValue(output, row, value) {
      if (value == null || !row) { output.textContent = "—"; output.removeAttribute("title"); return; }
      const change = (value / row.base - 1) * 100;
      const percent = `${change > 0 ? "+" : ""}${change.toFixed(2)}%`;
      output.textContent = mode === "price" ? Sym.of(row.symbol).price(value) : `${Sym.of(row.symbol).price(value)} · ${mode === "indexed" ? (100 * value / row.base).toFixed(2) : percent}`;
      output.title = `${Sym.of(row.symbol).price(value)} · ${percent} from the shared start`;
    }
    function paintLegend() {
      if (!panel) return;
      const legend = panel.querySelector(".comparison-legend");
      legend.replaceChildren();
      [baseSymbol(), ...symbols].forEach((symbol, i) => {
        const row = rendered?.rows.find((r) => r.symbol === symbol);
        const item = document.createElement("span"); item.className = "comparison-security";
        const logo = document.createElement("span"); logo.className = "comparison-logo";
        const src = Universe.logo(symbol);
        if (src) { const img = document.createElement("img"); img.src = src; img.alt = ""; img.addEventListener("error", () => img.remove(), { once: true }); logo.append(img); }
        else { logo.textContent = Universe.shown(symbol).slice(0, 1); logo.style.color = COLORS[i]; }
        const name = document.createElement("b"); name.textContent = Universe.label(symbol); name.title = `${Universe.shown(symbol)} · ${Universe.venue(symbol)}${row?.source && row.source !== "kite" ? ` · Source: ${row.source}` : ""}`;
        const value = document.createElement("span"); value.className = "comparison-value"; value.style.color = COLORS[i];
        const last = row && [...row.raw.entries()].filter(([time]) => time <= rendered.end).at(-1)?.[1];
        paintValue(value, row, last);
        item.append(logo, name);
        if (!i) { const meta = document.createElement("span"); meta.className = "comparison-meta"; meta.textContent = `${env.getInterval()} · ${Universe.venue(symbol)}${selectedType() === "footprint" ? " · Candles" : ""}`; if (selectedType() === "footprint") meta.title = "Comparison uses OHLC candles. Order-flow cells remain available on the original price chart."; item.append(meta); }
        item.append(value);
        if (i) { const remove = document.createElement("button"); remove.type = "button"; remove.dataset.removeSymbol = symbol; remove.setAttribute("aria-label", `Remove ${symbol} comparison`); remove.title = `Remove ${symbol}`; remove.innerHTML = Icons.svg("x", "xs"); item.append(remove); }
        legend.appendChild(item);
      });
      if (!rendered) { const period = panel.querySelector(".comparison-period"); period.textContent = ""; period.removeAttribute("title"); }
    }
    function restore(saved) {
      if (!saved || !Array.isArray(saved.symbols)) return;
      symbols = [...new Set(saved.symbols.filter((s) => typeof s === "string" && s !== baseSymbol()))].slice(0, 4);
      mode = MODES.includes(saved.mode) ? saved.mode : "percent";
      anchor = (!saved.symbol || saved.symbol === baseSymbol()) && (!saved.interval || saved.interval === interval()) && Number.isFinite(saved.anchor) ? saved.anchor : null;
      if (symbols.length) refresh(true);
    }
    return { add, refresh, restore, get active() { return !!symbols.length; },
      capture() { return plot && canvas ? [plot.takeScreenshot(true), canvas.getBoundingClientRect()] : null; },
      destroy() { destroyed = true; revision++; clearInterval(poll); stopSettings?.();
        for (const stop of subscriptions.values()) stop(); subscriptions.clear();
        document.removeEventListener("visibilitychange", visibilityChanged);
        document.removeEventListener("pivot:dock-layout", visibilityChanged);
        destroyPlot(); panel?.remove(); } };
  }
  return { create, normalize, plotPoints, axisTag, advanceHistory, subscribeBars: listen, alignedTime, historyTo, sharedHistories };
})();
