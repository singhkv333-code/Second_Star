/* Charto preview — main wiring.
 * Data: local 1-min store via dataserver on :5174 (RELIANCE only for now).
 * All chart times are IST-shifted (+19800) so the axis reads IST regardless
 * of browser timezone; shift is removed when talking to the server.
 */
"use strict";

(function () {
  const LWC = window.LightweightCharts;
  const API = "http://127.0.0.1:5174";
  const IST = 19800;
  const SYMBOL = "RELIANCE";
  const IV_SEC = { "1m": 60, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "1d": 86400, "1w": 604800, "1mo": 2592000 };
  const PAGE = { "1m": 5000, "5m": 4000, "15m": 3000, "30m": 3000, "1h": 3000, "1d": 3000, "1w": 700, "1mo": 200 };

  const el = (id) => document.getElementById(id);
  const chartEl = el("chart");
  const stageEl = el("stage");

  Theme.init();

  const state = {
    interval: "5m",
    bars: [],          // chart-time bars {time,open,high,low,close,volume}
    hasMore: true,
    loadingOlder: false,
  };

  // ── chart ─────────────────────────────────────────────
  const CHART_FONT = 'ui-sans-serif, -apple-system, BlinkMacSystemFont, "Inter", "Segoe UI", Roboto, sans-serif';
  function chartTheme() {
    const P = Theme.palette;
    return {
      layout: {
        background: { color: P.chartBg }, textColor: P.axisText,
        panes: { separatorColor: P.separator, enableResize: true },
      },
      grid: { vertLines: { color: P.grid }, horzLines: { color: P.grid } },
      rightPriceScale: { borderColor: P.border },
      timeScale: { borderColor: P.border },
      crosshair: {
        vertLine: { color: P.crosshair, labelBackgroundColor: P.crosshairLabel },
        horzLine: { color: P.crosshair, labelBackgroundColor: P.crosshairLabel },
      },
    };
  }

  const T0 = chartTheme();
  const chart = LWC.createChart(chartEl, {
    ...T0,
    layout: { ...T0.layout, fontFamily: CHART_FONT, fontSize: 12 },
    rightPriceScale: { ...T0.rightPriceScale, scaleMargins: { top: 0.06, bottom: 0.22 } },
    timeScale: { ...T0.timeScale, timeVisible: true, secondsVisible: false, rightOffset: 5 },
    crosshair: { ...T0.crosshair, mode: LWC.CrosshairMode.Normal },
    autoSize: true,
  });

  const candle = chart.addSeries(LWC.CandlestickSeries, {
    upColor: Theme.c("up"), downColor: Theme.c("down"), borderVisible: false,
    wickUpColor: Theme.c("up"), wickDownColor: Theme.c("down"),
  });
  const volume = chart.addSeries(LWC.HistogramSeries, {
    priceFormat: { type: "volume" }, priceScaleId: "vol",
    priceLineVisible: false, lastValueVisible: false,
  });
  chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.85, bottom: 0 } });

  // ── data client ───────────────────────────────────────
  async function fetchBars(interval, toRaw, limit) {
    const qs = new URLSearchParams({ symbol: SYMBOL, interval, limit: String(limit) });
    if (toRaw) qs.set("to", String(toRaw));
    const res = await fetch(`${API}/bars?${qs}`);
    if (!res.ok) throw new Error(`dataserver HTTP ${res.status}`);
    const d = await res.json();
    return {
      bars: d.bars.map((b) => ({
        time: b.t + IST, open: b.o, high: b.h, low: b.l, close: b.c, volume: b.v,
      })),
      hasMore: d.has_more,
    };
  }

  function paint() {
    candle.setData(state.bars.map(({ time, open, high, low, close }) => ({ time, open, high, low, close })));
    volume.setData(state.bars.map(({ time, volume: v, open, close }) => ({
      time, value: v, color: close >= open ? Theme.c("volUp") : Theme.c("volDown"),
    })));
    ind.recomputeAll(state.bars, { interval: state.interval, limit: state.bars.length });
  }

  async function loadInterval(interval) {
    state.interval = interval;
    setOverlay(true, "Loading…");
    const t0 = performance.now();
    try {
      const { bars, hasMore } = await fetchBars(interval, null, PAGE[interval]);
      state.bars = bars; state.hasMore = hasMore;
      chart.applyOptions({ timeScale: { timeVisible: !["1d", "1w", "1mo"].includes(interval) } });
      paint();
      // fresh data, fresh view: hand the price scale back to auto-fit
      eachPriceScale((ps) => ps.applyOptions({ autoScale: true }));
      paintAutoPill();
      chart.timeScale().setVisibleLogicalRange({ from: bars.length - 180, to: bars.length + 6 });
      lastBar = bars[bars.length - 1];
      paintReadout(lastBar);
      el("barsLine").textContent = `${bars.length.toLocaleString()} × ${interval}`;
      status(`${interval}: ${bars.length} bars in ${Math.round(performance.now() - t0)}ms · last ₹${lastBar.close}`);
      setOverlay(false);
      // drawings live in time, not in any one interval — make sure the new
      // interval's data actually reaches back to what is on the chart
      coverScene();
    } catch (e) {
      setOverlay(true, String(e.message || e), true);
    }
  }

  /** Prepend one older page; returns how many bars arrived. Shared by the
   *  scroll handler and the scene coverage loader so the two can never
   *  disagree about paging state. */
  async function loadOlderPage() {
    if (state.loadingOlder || !state.hasMore || !state.bars.length) return 0;
    state.loadingOlder = true;
    try {
      const earliestRaw = state.bars[0].time - IST;
      const { bars: older, hasMore } = await fetchBars(state.interval, earliestRaw, PAGE[state.interval]);
      if (!older.length) { state.hasMore = false; return 0; }
      const keep = chart.timeScale().getVisibleLogicalRange();
      state.bars = older.concat(state.bars);
      state.hasMore = hasMore;
      paint();
      if (keep) chart.timeScale().setVisibleLogicalRange({
        from: keep.from + older.length, to: keep.to + older.length,
      });
      el("barsLine").textContent = `${state.bars.length.toLocaleString()} × ${state.interval}`;
      return older.length;
    } catch (e) {
      status(`older-page error: ${e.message}`);
      return 0;
    } finally {
      state.loadingOlder = false;
    }
  }

  /** The earliest raw time any chat drawing is anchored to — Infinity when
   *  nothing on the scene is time-anchored (levels and zones are not). */
  function sceneEarliest() {
    let t = Infinity;
    for (const a of scene.state.items) {
      const anchors = [].concat(a.pts || [], [a.p1, a.p2, a.a, a.b].filter(Boolean));
      for (const p of anchors) if (p && p.t) t = Math.min(t, p.t);
      if (a.t) t = Math.min(t, a.t);
    }
    return t;
  }

  /** After an interval switch the loaded window may not reach back to what
   *  the chat drew — a daily pattern from April is outside 5m's first page —
   *  and shapes projected outside the data are only approximately placed.
   *  Page in real bars until the drawings are covered (bounded, so a
   *  years-old anchor cannot trigger a fetch storm). */
  async function coverScene() {
    const need = sceneEarliest();
    if (!isFinite(need)) return;
    let guard = 0;
    while (state.hasMore && state.bars.length
           && state.bars[0].time - IST > need && guard++ < 6) {
      if (!await loadOlderPage()) break;
    }
    scene.requestUpdate();
  }

  // infinite history: prepend an older page when the left edge approaches
  chart.timeScale().subscribeVisibleLogicalRangeChange(async (r) => {
    if (!r || state.loadingOlder || !state.hasMore || !state.bars.length) return;
    if (r.from > 80) return;
    const got = await loadOlderPage();
    if (got) status(`loaded ${got} older bars (total ${state.bars.length})`);
  });

  // ── readout ───────────────────────────────────────────
  let lastBar = null;
  function paintReadout(b) {
    if (!b) { el("roOhlc").innerHTML = ""; return; }
    const cls = b.close >= b.open ? "up" : "down";
    const f = (n) => Number(n).toLocaleString("en-IN", { maximumFractionDigits: 2 });
    el("roOhlc").innerHTML =
      `<span>O <b class="${cls}">${f(b.open)}</b></span>` +
      `<span>H <b class="${cls}">${f(b.high)}</b></span>` +
      `<span>L <b class="${cls}">${f(b.low)}</b></span>` +
      `<span>C <b class="${cls}">${f(b.close)}</b></span>` +
      `<span>V <b class="${cls}">${f(b.volume)}</b></span>`;
  }
  chart.subscribeCrosshairMove((p) => {
    const b = p && p.seriesData ? p.seriesData.get(candle) : null;
    if (b) {
      const src = state.bars[state.bars.length - 1];
      paintReadout({ ...b, volume: (p.seriesData.get(volume) || {}).value ?? src.volume });
    } else paintReadout(lastBar);
  });

  function status(msg) { el("statusLine").textContent = msg; }
  function setOverlay(show, text, isErr) {
    el("overlayText").innerHTML = isErr ? `<span class="err">${text}</span>` : (text || "");
    el("overlay").classList.toggle("show", !!show);
  }

  // ── indicators UI ─────────────────────────────────────
  const ind = Indicators.createManager(chart);
  const menu = el("indMenu");

  el("indBtn").innerHTML =
    Icons.svg("indicators", "sm") + "Indicators" + Icons.svg("chevronDown", "chev");

  function renderIndMenu() {
    menu.innerHTML = '<div class="head">Overlays</div>' +
      ind.CATALOG.filter((c) => c.kind === "overlay").map(itemHTML).join("") +
      '<div class="sep"></div><div class="head">Panes</div>' +
      ind.CATALOG.filter((c) => c.kind === "pane").map(itemHTML).join("");
    function itemHTML(c) {
      const on = ind.isActive(c.id);
      return `<div class="item ${on ? "on" : ""}" data-ind="${c.id}">` +
        `<span>${c.label}</span>${on ? Icons.svg("check", "xs") : ""}</div>`;
    }
  }
  function renderChips() {
    el("indChips").innerHTML = [...ind.active.keys()].map((id) => {
      const c = ind.CATALOG.find((q) => q.id === id);
      // a period is editable in place; period-less indicators (psar, vwap,
      // obv) have nothing to edit, so their labels stay inert
      const lbl = c.period > 0
        ? `<span class="lbl" data-edit="${id}" title="Edit period">${c.label}</span>`
        : c.label;
      return `<span class="chip">${lbl}` +
        `<span class="x" data-rm="${id}" title="Remove">${Icons.svg("x", "xs")}</span></span>`;
    }).join("");
    // the chips ARE the active set, so this is the one honest save point —
    // it catches the menu, the chip's x, and anything the chat adds
    Store.set("indicators", [...ind.active.keys()]);
  }

  // ── period editor: click a chip's label, type, Enter ──
  const periodPop = el("periodPop");
  periodPop.addEventListener("click", (e) => e.stopPropagation());
  function openPeriodEditor(anchorEl, id) {
    const def = ind.CATALOG.find((c) => c.id === id);
    if (!def) return;
    periodPop.innerHTML =
      `<div class="head">${def.label.replace(/\s*\d.*$/, "")} · period</div>` +
      `<div class="period-row">` +
      `<input id="periodInput" type="number" min="2" max="500" step="1" value="${def.period}">` +
      `<button class="btn outline" id="periodApply">Apply</button></div>` +
      (def.formula ? `<div class="hint">${def.formula}</div>` : "");
    const r = anchorEl.getBoundingClientRect();
    periodPop.style.left = `${Math.min(r.left, window.innerWidth - 260)}px`;
    periodPop.style.top = `${r.bottom + 8}px`;
    closeMenus(periodPop);
    periodPop.classList.add("open");
    const input = el("periodInput");
    input.focus();
    input.select();
    const apply = async () => {
      const v = Math.max(2, Math.min(500, parseInt(input.value, 10) || def.period));
      periodPop.classList.remove("open");
      if (v === def.period) return;
      try {
        await ind.setPeriod(id, v);
        renderChips();
        document.dispatchEvent(new CustomEvent("charto:indicators-changed"));
      } catch (err) {
        status(`could not apply period ${v}: ${err.message}`);
      }
    };
    el("periodApply").onclick = apply;
    input.onkeydown = (e) => {
      if (e.key === "Enter") apply();
      if (e.key === "Escape") periodPop.classList.remove("open");
    };
  }
  el("indBtn").addEventListener("click", (e) => {
    e.stopPropagation();
    renderIndMenu();
    closeMenus(menu);
    menu.classList.toggle("open");
  });
  menu.addEventListener("click", (e) => {
    e.stopPropagation(); // keep the dropdown open for multi-select
    const it = e.target.closest("[data-ind]");
    if (!it) return;
    const id = it.dataset.ind;
    const def = ind.CATALOG.find((q) => q.id === id);
    if (def.intradayOnly && ["1d", "1w", "1mo"].includes(state.interval) && !ind.isActive(id)) {
      status("VWAP is session-anchored — switch to an intraday interval");
      return;
    }
    Promise.resolve(ind.toggle(id, state.bars))
      .then(() => { renderIndMenu(); renderChips(); })
      .catch((err) => status(`could not add ${def.label}: ${err.message}`));
    renderIndMenu(); renderChips();
  });
  el("indChips").addEventListener("click", (e) => {
    const ed = e.target.closest("[data-edit]");
    if (ed) {
      e.stopPropagation();
      openPeriodEditor(ed, ed.dataset.edit);
      return;
    }
    const x = e.target.closest("[data-rm]");
    if (!x) return;
    ind.remove(x.dataset.rm);
    renderChips();
  });

  /** Close every open dropdown except `keep`. Shared by header + composer. */
  function closeMenus(keep) {
    document.querySelectorAll(".dropdown.open").forEach((d) => {
      if (d !== keep) d.classList.remove("open");
    });
  }
  document.addEventListener("click", () => closeMenus(null));
  window.__chartoCloseMenus = closeMenus;

  // keep the chips honest when the chat adds an indicator
  document.addEventListener("charto:indicators-changed", renderChips);

  // ── interval buttons ──────────────────────────────────
  el("intervalSeg").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-iv]");
    if (!b) return;
    selectInterval(b.dataset.iv);
    loadInterval(b.dataset.iv);
  });

  function selectInterval(iv) {
    [...el("intervalSeg").children].forEach((x) =>
      x.classList.toggle("active", x.dataset.iv === iv));
    Store.set("interval", iv);
  }

  // ── left rail ─────────────────────────────────────────
  // Built from the tool catalogue, so adding a tool is a one-line data
  // change there and never a UI change here. One rail button per GROUP; the
  // button shows and re-arms the last tool you picked from that group, which
  // is how a rail stays 8 buttons wide while offering twenty tools.
  const rail = el("rail");
  const ICON_FOR = { trend: "trend", ray: "ray", extended: "extended",
    hline: "hline", vline: "vline", channel: "channel", regression: "channel",
    fib: "fib", rect: "rect", triangle: "triangle", brush: "brush",
    priceRange: "hline", dateRange: "vline", measure: "measure",
    long: "position", short: "position", text: "text" };
  const lastOfGroup = {};

  rail.insertAdjacentHTML("beforeend",
    `<button class="tool active" id="tool-cursor" data-tool="cursor" data-kind="tool">` +
    `${Icons.svg("crosshair")}<span class="tip">Cursor / select</span></button>` +
    '<div class="rail-sep"></div>');

  for (const g of Tools.GROUPS) {
    const tools = Object.entries(Tools.SPECS).filter(([, s]) => s.group === g.id);
    if (!tools.length) continue;
    lastOfGroup[g.id] = tools[0][0];
    const items = tools.map(([id, s]) =>
      `<div class="item" data-tool="${id}"><span class="lead">` +
      `${Icons.svg(ICON_FOR[id] || g.icon, "sm")}${s.label}</span></div>`).join("");
    rail.insertAdjacentHTML("beforeend",
      `<div class="tool-wrap" data-group="${g.id}">` +
        `<button class="tool has-group" id="group-${g.id}" data-group-btn="${g.id}">` +
        `${Icons.svg(g.icon)}<span class="tip">${g.label}</span></button>` +
        `<div class="dropdown side" id="menu-${g.id}">` +
          `<div class="head">${g.label}</div>${items}</div>` +
      `</div>`);
  }
  rail.insertAdjacentHTML("beforeend",
    '<div class="rail-sep"></div>' +
    `<button class="tool" id="tool-magnet" data-tool="magnet" data-kind="toggle">` +
    `${Icons.svg("magnet")}<span class="tip">Magnet — snap to OHLC</span></button>` +
    '<div class="rail-spacer"></div>' +
    `<button class="tool" id="tool-export" data-tool="export" data-kind="action">` +
    `${Icons.svg("download")}<span class="tip">Export drawings JSON</span></button>` +
    `<button class="tool" id="tool-trash" data-tool="trash" data-kind="action">` +
    `${Icons.svg("trash")}<span class="tip">Clear all drawings</span></button>`);

  function closeToolMenus(except) {
    for (const g of Tools.GROUPS) {
      if (g.id === except) continue;
      el(`menu-${g.id}`)?.classList.remove("open");
    }
  }
  // Click arms the group's last tool — the fast path, and the common one.
  // The list opens on HOVER-INTENT rather than a caret hit-target: a 12px
  // caret on a 34px button is a precision test, and picking a tool should
  // not be one.
  let hoverTimer = null;
  rail.addEventListener("mouseover", (e) => {
    const wrap = e.target.closest(".tool-wrap");
    clearTimeout(hoverTimer);
    if (!wrap) return;
    hoverTimer = setTimeout(() => {
      closeToolMenus(wrap.dataset.group);
      el(`menu-${wrap.dataset.group}`)?.classList.add("open");
    }, 320);
  });
  rail.addEventListener("mouseleave", () => {
    clearTimeout(hoverTimer);
    hoverTimer = setTimeout(() => closeToolMenus(), 260);
  });
  rail.addEventListener("click", (e) => {
    const gBtn = e.target.closest("[data-group-btn]");
    if (gBtn) {
      const gid = gBtn.dataset.groupBtn;
      clearTimeout(hoverTimer);
      closeToolMenus();
      selectTool(lastOfGroup[gid]);
      return;
    }
    const item = e.target.closest(".dropdown .item[data-tool]");
    if (item) {
      const gid = item.closest(".tool-wrap").dataset.group;
      lastOfGroup[gid] = item.dataset.tool;
      // the rail button now shows what it will arm next time
      const btn = el(`group-${gid}`);
      const icon = ICON_FOR[item.dataset.tool];
      if (icon) btn.innerHTML = Icons.svg(icon)
        + `<span class="tip">${Tools.SPECS[item.dataset.tool].label}</span>`;
      clearTimeout(hoverTimer);
      closeToolMenus();
      selectTool(item.dataset.tool);
    }
  });
  document.addEventListener("click", (e) => {
    if (!e.target.closest(".tool-wrap")) closeToolMenus();
  });

  // ── panes ─────────────────────────────────────────────
  // Every pane you can interact with, keyed by something stable. Indicator
  // ids survive a pane reshuffle; pane indices do not.
  function panesList() {
    const out = [{ key: "price", label: "price", pane: candle.getPane(), series: candle }];
    for (const [, a] of ind.active) {
      if (a.def.kind !== "pane" || !a.series.length) continue;
      // keyed by NAME, not active id: a divergence leg targets pane "rsi"
      // and must still land there when the user has re-perioded it to rsi26.
      // `period` rides along so a composite key ("rsi@26") can pick the
      // right variant when two of the same indicator are open.
      out.push({ key: a.def.name, period: a.def.period, label: a.def.label,
                 pane: a.series[0].getPane(), series: a.series[0] });
    }
    return out;
  }
  /** Which pane is this screen y in? Everything that resolves a gesture —
   *  pins, provenance, drawings — asks this instead of assuming one surface. */
  function paneAtClient(clientY) {
    for (const p of panesList()) {
      const pe = p.pane.getHTMLElement && p.pane.getHTMLElement();
      if (!pe) continue;
      const r = pe.getBoundingClientRect();
      if (clientY >= r.top && clientY <= r.bottom) return p.key;
    }
    return "price";
  }
  /** y measured inside a named pane. */
  function yInPane(clientY, key) {
    const p = panesList().find((q) => q.key === key);
    const pe = p && p.pane.getHTMLElement && p.pane.getHTMLElement();
    return pe ? clientY - pe.getBoundingClientRect().top : clientY;
  }

  const draw = Drawings.create(chart, candle, {
    getBars: () => state.bars,
    getIntervalSec: () => IV_SEC[state.interval],
    container: chartEl,
    stage: stageEl,
    panes: panesList,
    setStatus: (m) => { el("drawStatus").textContent = m; },
    onToolDone: () => selectTool("cursor"),
  });
  // panes appear and vanish with their indicators — re-attach on every change
  document.addEventListener("charto:indicators-changed", () => draw.syncPanes());

  let trashArmed = false;
  function selectTool(id) {
    draw.setTool(id);
    el("tool-cursor").classList.toggle("active", id === "cursor");
    const spec = Tools.SPECS[id];
    for (const g of Tools.GROUPS) {
      el(`group-${g.id}`)?.classList.toggle("active", !!spec && spec.group === g.id);
    }
    document.querySelectorAll(".dropdown .item[data-tool]").forEach((n) =>
      n.classList.toggle("on", n.dataset.tool === id));
    el("drawStatus").textContent = spec
      ? `${spec.label} — ${spec.anchors === "free" ? "drag to draw"
          : `click ${spec.anchors} point${spec.anchors > 1 ? "s" : ""}`}`
      : "";
    trashArmed = false;
  }
  rail.addEventListener("click", (e) => {
    const b = e.target.closest(".tool");
    if (!b) return;
    const id = b.dataset.tool, kind = b.dataset.kind;
    if (kind === "tool") { selectTool(id); return; }
    if (id === "magnet") { b.classList.toggle("toggled", draw.toggleMagnet()); return; }
    if (id === "export") {
      const blob = new Blob([draw.exportJSON()], { type: "application/json" });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = "charto_drawings_RELIANCE.json";
      a.click();
      return;
    }
    if (id === "trash") {
      if (!trashArmed) {
        trashArmed = true;
        el("drawStatus").textContent = `click trash again to clear ${draw.count()} drawings`;
        setTimeout(() => { trashArmed = false; }, 3000);
      } else {
        draw.clearAll();
        trashArmed = false;
        el("drawStatus").textContent = "all drawings cleared";
      }
    }
  });
  selectTool("cursor");

  // ── TradingView-parity panning ────────────────────────
  // Grab anywhere in the pane and drag in any direction, as far as you like —
  // the chart follows the mouse and the price scale translates with it.
  //
  // LWC only allows that once `autoScale` is off: while auto is on it ignores
  // vertical drags entirely AND re-fits the price scale on every horizontal
  // pan, so the levels jump around under you. TradingView does neither. So on
  // the first pane grab we freeze the scale at exactly where it already is —
  // visually a no-op, but it hands panning over to the user.
  //
  // Dragging the price AXIS still rescales (that is its job), and the "auto"
  // pill below hands the scale back to auto-fit.
  let panning = false;

  function isPaneMouse(e) {
    const r = chartEl.getBoundingClientRect();
    const x = e.clientX - r.left, y = e.clientY - r.top;
    let axisW = 0, axisH = 0;
    try { axisW = chart.priceScale("right").width(); } catch {}
    try { axisH = chart.timeScale().height(); } catch {}
    return x >= 0 && x < r.width - axisW && y >= 0 && y < r.height - axisH;
  }

  /** The pane under a client-Y, so we only release the scale you grabbed. */
  function paneAt(clientY) {
    for (const pane of chart.panes()) {
      const node = pane.getHTMLElement();
      if (!node) continue;
      const r = node.getBoundingClientRect();
      if (clientY >= r.top && clientY <= r.bottom) return pane;
    }
    return null;
  }

  function eachPriceScale(fn) {
    for (const pane of chart.panes()) {
      try { fn(pane.priceScale("right")); } catch { /* pane has no right scale */ }
    }
  }

  /** Pin the scale exactly where it is (no visual change, auto-fit off). */
  function releaseScale(pane) {
    if (!pane) return;
    try {
      const ps = pane.priceScale("right");
      if (!ps.options().autoScale) return;
      const r = ps.getVisibleRange();
      if (r) ps.setVisibleRange(r);
    } catch {}
  }

  // "auto" pill: TradingView's way back to an auto-fitted price scale
  const autoPill = document.createElement("button");
  autoPill.className = "auto-pill";
  autoPill.textContent = "auto";
  autoPill.title = "Re-fit the price scale to the visible bars";
  stageEl.appendChild(autoPill);
  autoPill.addEventListener("click", () => {
    eachPriceScale((ps) => ps.applyOptions({ autoScale: true }));
    paintAutoPill();
  });

  function paintAutoPill() {
    let manual = false;
    eachPriceScale((ps) => { if (!ps.options().autoScale) manual = true; });
    autoPill.classList.toggle("show", manual);
  }

  // bubble phase → runs after drawings.js has decided whether this is a
  // shape drag; a shape drag disables LWC scroll entirely, so it isn't a pan.
  chartEl.addEventListener("mousedown", (e) => {
    if (e.button !== 0) return;
    if (draw.state.tool !== "cursor" || draw.state.drag || draw.state.draft) return;
    if (!isPaneMouse(e)) return;   // axis drags keep their own behaviour
    releaseScale(paneAt(e.clientY));
    panning = true;
    stageEl.classList.add("panning");
  });
  window.addEventListener("mouseup", () => {
    if (panning) { panning = false; stageEl.classList.remove("panning"); }
    paintAutoPill();
  });

  chartEl.addEventListener("wheel", () => paintAutoPill(), { passive: true });
  chartEl.addEventListener("dblclick", () => setTimeout(paintAutoPill, 0));

  // ── chart-state envelope (Phase 1: the model can see the chart) ──
  // Pure read of state that already exists — no new math, no server round
  // trip. Chart times are IST-shifted, so UTC getters render IST wall clock.
  const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const DAILY = new Set(["1d", "1w", "1mo"]);
  // Year is always included: the store spans 2015→today, so a bare "2 Feb"
  // is ambiguous by a decade on weekly/monthly (and on any scrolled-back view).
  function fmtIST(t, withTime) {
    const d = new Date(t * 1000), p = (n) => String(n).padStart(2, "0");
    const date = `${d.getUTCDate()} ${MON[d.getUTCMonth()]} ${d.getUTCFullYear()}`;
    return withTime ? `${date} ${p(d.getUTCHours())}:${p(d.getUTCMinutes())}` : date;
  }
  const r2 = (n) => Math.round(n * 100) / 100;

  function getChartContext() {
    const bars = state.bars;
    if (!bars.length) return { status: "loading", symbol: SYMBOL, interval: state.interval };
    const withTime = !DAILY.has(state.interval);
    const T = (t) => fmtIST(t, withTime);

    const lr = chart.timeScale().getVisibleLogicalRange();
    const lo = Math.max(0, Math.floor(lr ? lr.from : 0));
    const hi = Math.min(bars.length - 1, Math.ceil(lr ? lr.to : bars.length - 1));
    const vis = bars.slice(lo, hi + 1);
    if (!vis.length) return { status: "loading", symbol: SYMBOL, interval: state.interval };

    let hp = -Infinity, ht = 0, lp = Infinity, lt = 0, vsum = 0;
    for (const b of vis) {
      if (b.high > hp) { hp = b.high; ht = b.time; }
      if (b.low < lp) { lp = b.low; lt = b.time; }
      vsum += b.volume || 0;
    }
    const first = vis[0], last = vis[vis.length - 1];

    // ~20-point close downsample: trajectory shape only, never a value source
    const step = Math.max(1, Math.floor(vis.length / 20));
    const traj = [];
    for (let i = 0; i < vis.length; i += step) traj.push(r2(vis[i].close));

    // Intraday: today's session so far (answers "how much is it up today")
    let session = null;
    if (withTime) {
      const day = Math.floor(last.time / 86400);
      const today = bars.filter((b) => Math.floor(b.time / 86400) === day);
      if (today.length > 1) {
        session = {
          date: fmtIST(last.time, false),
          open: r2(today[0].open), last: r2(last.close),
          change_pct: r2((last.close - today[0].open) / today[0].open * 100),
          high: r2(Math.max(...today.map((b) => b.high))),
          low: r2(Math.min(...today.map((b) => b.low))),
        };
      }
    }

    // A drawing's numbers only mean "rupees" on the price pane. On an
    // indicator pane they are that indicator's units, so the pane has to
    // travel with the drawing or 62 on RSI reads as ₹62.
    const paneLabel = (key) => (!key || key === "price") ? undefined
      : ((ind.CATALOG.find((c) => c.id === key) || {}).label || key);
    const drawings = draw.state.drawings.slice(0, 15).map((d) => {
      const pts = d.type === "brush" ? [d.pts[0], d.pts[d.pts.length - 1]] : d.pts;
      return {
        // `ref` is the short stable handle the chat tags and the evaluate
        // tools resolve by; `id` stays for anything holding an older one
        id: d.id, ref: d.ref, type: d.type, text: d.text || undefined,
        on: paneLabel(d.pane),
        selected: d.id === draw.state.selId || undefined,
        pts: pts.map((q) => ({ t: T(q.t), p: r2(q.v) })),
      };
    });

    return {
      symbol: SYMBOL, exchange: "NSE",
      source: "local 1-min store (Kite-sourced)",
      interval: state.interval,
      view: {
        from: T(first.time), to: T(last.time),
        bars_visible: vis.length, bars_loaded: bars.length,
        history_from: "2015-02-02", more_history: state.hasMore,
      },
      last_bar: {
        t: T(last.time), o: r2(last.open), h: r2(last.high),
        l: r2(last.low), c: r2(last.close), v: last.volume,
      },
      session,
      window: {
        open: r2(first.open), close: r2(last.close),
        change_pct: r2((last.close - first.open) / first.open * 100),
        high: { p: r2(hp), t: T(ht) }, low: { p: r2(lp), t: T(lt) },
        avg_volume: Math.round(vsum / vis.length),
        trajectory: traj,
      },
      indicators: ind.snapshot(first.time).map((x) => ({
        label: x.label, now: r2(x.now), at_window_start: x.at === null ? null : r2(x.at),
      })),
      drawings,
      drawings_omitted: Math.max(0, draw.state.drawings.length - 15) || undefined,
      pins: pins.list().map((p) => ({
        t: T(p.time), o: r2(p.open), h: r2(p.high),
        l: r2(p.low), c: r2(p.close), v: p.volume,
      })),
    };
  }

  // ── pins: a clicked bar becomes context for the next question ──
  // Chart state, so it lives here and rides in the envelope; the composer
  // only renders it. A pin is never a question on its own — it grounds the
  // next one, so a stray click costs nothing.
  const pins = (() => {
    let items = [];
    const changed = () =>
      document.dispatchEvent(new CustomEvent("charto:pins", { detail: items.slice() }));
    return {
      list: () => items,
      toggle(bar) {
        const i = items.findIndex((p) => p.time === bar.time);
        if (i >= 0) items.splice(i, 1);
        else items = [...items, bar].slice(-4);   // a handful is context; more is noise
        changed();
      },
      remove(time) { items = items.filter((p) => p.time !== time); changed(); },
      clear() { items = []; changed(); },
    };
  })();

  // ── scene layer: what the chat drew (read-only, with provenance) ──
  const scene = Scene.create(chart, candle, {
    getBars: () => state.bars,
    container: chartEl,
    panes: panesList,
    paneAt: paneAtClient,
    yIn: yInPane,
    getIntervalSec: () => IV_SEC[state.interval],
    // detectors speak raw exchange time; the chart runs IST-shifted
    toChartTime: (t) => t + IST,
    onChange: (n) => {
      const badge = el("sceneCount"), clear = el("sceneClear");
      badge.textContent = n ? `${n} drawn by chat` : "";
      badge.style.display = n ? "" : "none";
      clear.style.display = n ? "" : "none";
      Store.set("scene", scene.state.items);
      ind.rescalePanes();     // marks feed pane autoscale — recompute now
    },
    onHover: (a) => {
      el("drawStatus").textContent = a
        ? `${a.label} — ${a.source.strength} · `      // label already says touches
          + `last ${a.source.last_touch} · ${a.source.method} over `
          + `${a.source.bars_scanned} ${a.source.interval} bars`
        : "";
    },
    onIndicator: async (a) => {              // "add the 70-day average"
      // pane keys may arrive composite ("rsi@26") from period-aware marks
      const raw = String(a.name || "");
      const name = raw.split("@")[0];
      const period = a.period || Number(raw.split("@")[1]) || 0;
      const changed = () => {
        renderChips();
        document.dispatchEvent(new CustomEvent("charto:indicators-changed"));
      };
      // if this indicator is already on the chart, chat RE-PERIODS it in
      // place — a second variant of the same indicator appears only when
      // the user adds one deliberately from the menu
      const activeId = [...ind.active.keys()].find((id) => {
        const d = ind.CATALOG.find((c) => c.id === id);
        return d && d.name === name;
      });
      if (activeId) {
        const d = ind.CATALOG.find((c) => c.id === activeId);
        if (!period || d.period === period) return;
        try { await ind.setPeriod(activeId, period); changed(); }
        catch (err) { status(`could not switch ${name} to ${period}: ${err.message}`); }
        return;
      }
      // ensure() mints a def for ANY period, so the line drawn is the line
      // computed — mapping onto presets drew RSI 14 for a quoted RSI 26
      const id = ind.ensure(name, period);
      if (id && !ind.isActive(id)) {
        Promise.resolve(ind.toggle(id, state.bars)).then(changed).catch(() => {});
      }
    },
    isCursorMode: () => draw.state.tool === "cursor",
    onSelect: (a, y) => showProvenance(a, y),
  });
  el("sceneClear").innerHTML = Icons.svg("eraser", "sm");
  el("sceneClear").addEventListener("click", () => scene.clear());
  document.addEventListener("charto:indicators-changed", () => scene.syncPanes());

  // marked levels/points on an indicator pane feed its autoscale, so a
  // "70" line on the RSI is actually on screen instead of off-scale
  ind.setScaleExtras((name, period) => {
    const vals = [];
    for (const a of scene.state.items) {
      const [nm, per] = String(a.pane || "").split("@");
      if (nm !== name || (per && +per !== period)) continue;
      if (a.kind === "level") vals.push(a.price);
      else if (a.kind === "zone") vals.push(a.lo, a.hi);
      else if (a.kind === "point" && a.a) vals.push(a.a.v);
      else if (a.kind === "segment") vals.push(a.p1.v, a.p2.v);
      else if (a.kind === "poly") for (const p of a.pts || []) vals.push(p.v);
    }
    return vals.filter((v) => Number.isFinite(v));
  });

  // ── provenance card: every drawn line is interrogable ──
  const prov = el("provCard");
  let provFor = null;
  function hideProvenance() { prov.classList.remove("open"); provFor = null; }

  function showProvenance(a, y) {
    if (provFor === a.id) return hideProvenance();
    provFor = a.id;
    const s = a.source || {};
    const row = (k, v) => (v ? `<dt>${k}</dt><dd>${v}</dd>` : "");
    const num = (n) => Number(n).toLocaleString("en-IN", { minimumFractionDigits: 2 });
    // Every annotation answers the same question — "what is your record?" —
    // but each detector measures a different thing, so each states its own
    // rule. A hit rate whose definition is hidden is the number we refuse.
    let title, body;
    if (s.tool === "get_trendlines") {
      title = "Trendline";
      body = row("Record", `${s.touches} touches · ${s.strength}`)
        + row("Anchored", `${s.first_touch} → ${s.last_touch}`);
    } else if (s.tool === "get_divergences") {
      title = `${s.strength || ""} divergence`.trim();
      body = row("Record", s.record)
        + row("Instances", s.touches ? `${s.touches} in this window` : "")
        + row("Spans", `${s.first_touch} → ${s.last_touch}`);
    } else {
      const ev = s.evidence || {};
      const graded = (ev.held || 0) + (ev.broke || 0);
      title = a.kind === "zone" ? `${num(a.lo)}–${num(a.hi)}` : num(a.price);
      body = row("Record", !graded ? "never re-tested"
        : `held ${ev.held} of ${graded}`
          + (ev.hold_rate == null ? "" : ` · ${ev.hold_rate}%`))
        + row("Reaction", ev.react_pct == null ? ""
          : `${ev.react_pct}% ${a.role === "resistance" ? "down" : "up"}`
            + `, median ${ev.react_bars} bars`)
        + row("Judged", graded ? `re-tests only, ${s.horizon_bars} bars each` : "")
        + row("Touches", `${s.touches} · ${s.strength}`)
        + row("First", s.first_touch)
        + row("Last", s.last_touch);
    }
    prov.innerHTML = `
      <header>
        ${s.tool === "get_divergences" ? ""   // "bearish" already says the side
          : `<span class="role ${a.role}">${a.role === "resistance" ? "Resistance" : "Support"}</span>`}
        <span class="price">${title}</span>
        <button class="btn icon" data-act="close" title="Close">${Icons.svg("x", "xs")}</button>
      </header>
      <dl>
        ${body}
        ${row("Scanned", s.bars_scanned
          ? `${s.bars_scanned.toLocaleString()} × ${s.interval} bars` : "")}
        ${row("Method", s.method)}
      </dl>
      <footer>
        <button class="btn outline" data-act="ask">${Icons.svg("chat", "xs")}Ask about this</button>
        <button class="btn" data-act="remove">${Icons.svg("eraser", "xs")}Remove</button>
      </footer>`;
    const top = Math.max(8, Math.min(y - 20, stageEl.clientHeight - 320));
    prov.style.top = `${top}px`;
    prov.classList.add("open");
  }

  /** The card for one of the USER's own drawings. Selecting a shape opens
   *  it; only its "Ask in chat" button attaches the drawing to the message,
   *  so selecting to drag or edit never silently tags anything. */
  let provDraw = null;
  function showDrawingCard(d, y) {
    provDraw = d;
    provFor = d.id;
    const T = (t) => fmtIST(t + IST, !DAILY.has(state.interval));
    const num = (n) => Number(n).toLocaleString("en-IN", { minimumFractionDigits: 2 });
    const g = draw.geometryOf(d.id) || { pts: [] };
    const unit = d.pane && d.pane !== "price"
      ? ((ind.CATALOG.find((c) => c.id === d.pane) || {}).label || d.pane) : "";
    const rows = g.pts.slice(0, 3).map((p, i) =>
      `<dt>${["From", "To", "Third"][i] || "Point"}</dt>` +
      `<dd>${T(p.t)} @ ${num(p.v)}</dd>`).join("");
    prov.innerHTML = `
      <header>
        <span class="role draw-ref">${d.ref}</span>
        <span class="price">${d.label}</span>
        <button class="btn icon" data-act="close" title="Close">${Icons.svg("x", "xs")}</button>
      </header>
      <dl>
        ${unit ? `<dt>Pane</dt><dd>${unit} — values are that indicator's units</dd>` : ""}
        ${rows}
        ${g.pts.length > 3 ? `<dt>Points</dt><dd>${g.pts.length} anchors</dd>` : ""}
      </dl>
      <footer>
        <button class="btn outline" data-act="ask-draw">${Icons.svg("chat", "xs")}Ask in chat</button>
        <button class="btn" data-act="del-draw">${Icons.svg("eraser", "xs")}Remove</button>
      </footer>`;
    const top = Math.max(8, Math.min((y ?? 120) - 20, stageEl.clientHeight - 320));
    prov.style.top = `${top}px`;
    prov.classList.add("open");
  }
  document.addEventListener("charto:draw-select", (e) => {
    if (!e.detail) return hideProvenance();
    // finishing a drawing selects it, but popping its card mid-flow
    // interrupts someone laying out several shapes in a row
    if (e.detail.via === "create") return;
    showDrawingCard(e.detail, lastUpAt[1] - stageEl.getBoundingClientRect().top);
  });

  prov.addEventListener("click", (e) => {
    const act = e.target.closest("[data-act]")?.dataset.act;
    if (!act) return;
    if (act === "ask-draw" && provDraw) {
      // THIS is the moment the drawing joins the conversation — an explicit
      // ask, not a side effect of having clicked the shape
      document.dispatchEvent(new CustomEvent("charto:draw-tag", { detail: provDraw }));
      provDraw = null;
      return hideProvenance();
    }
    if (act === "del-draw" && provDraw) {
      draw.remove(provDraw.id);
      provDraw = null;
      return hideProvenance();
    }
    const a = scene.state.items.find((x) => x.id === provFor);
    if (act === "ask" && a) {
      const t = (a.source || {}).tool;
      const n = (v) => Number(v).toLocaleString("en-IN", { minimumFractionDigits: 2 });
      const subject = t === "get_trendlines" ? `the ${a.role} trendline`
        : t === "get_divergences" ? `the ${(a.source || {}).strength || ""} divergence`
          : a.kind === "zone" ? `the ${a.role} zone ${n(a.lo)}–${n(a.hi)}`
            : `the ${a.role} at ${n(a.price)}`;
      document.dispatchEvent(new CustomEvent("charto:compose", { detail: subject + " " }));
    }
    if (act === "remove" && a) scene.remove(a.id);
    hideProvenance();
  });

  // ── click a candle to pin it as context ──
  // Decided at mousedown, because a one-shot drawing tool reverts to cursor
  // the moment it commits — by the time the click lands, the tool would look
  // like "cursor" and we'd pin the bar the user was actually drawing on.
  let downTool = "cursor", downInPrice = true, downAt = [0, 0], lastUpAt = [0, 0];
  chartEl.addEventListener("mouseup", (e) => { lastUpAt = [e.clientX, e.clientY]; }, true);
  chartEl.addEventListener("mousedown", (e) => {
    downTool = draw.state.tool;
    downAt = [e.clientX, e.clientY];
    // Resolve the gesture against the pane it happened in, not the chart as
    // a whole: a pin is a candle, and candles only exist in the price pane.
    downInPrice = paneAtClient(e.clientY) === "price";
  }, true);

  chart.subscribeClick((param) => {
    if (!param || !param.time || !param.seriesData) return hideProvenance();
    if (downTool !== "cursor") return;                // drawing takes precedence
    if (!downInPrice) return;      // a pin is a candle; candles live in pane 0
    // Selecting, dragging or deselecting one of your own drawings already
    // spent this click — pinning the bar underneath it too is never what
    // you meant.
    if (draw.state.consumedDown) return;
    // A pan is a drag, not a click: don't pin the bar you let go over.
    if (Math.hypot(lastUpAt[0] - downAt[0], lastUpAt[1] - downAt[1]) > 4) return;
    // A click on a drawn level opens its provenance card — it must not also
    // pin the candle underneath. The scene's DOM listener can't stop this
    // subscription, so ask it whether the click was already spoken for.
    if (scene.hitAt(yInPane(downAt[1], "price"), "price",
                    downAt[0] - chartEl.getBoundingClientRect().left)) return;
    const b = param.seriesData.get(candle);
    if (!b) return;
    // A pin means THE CANDLE, not "wherever I happened to click": the click
    // must land on the bar's high-low span (small grab tolerance), so a
    // stray click in empty chart space attaches nothing to the chat.
    const yClick = yInPane(downAt[1], "price");
    const yHi = candle.priceToCoordinate(b.high);
    const yLo = candle.priceToCoordinate(b.low);
    if (yClick === null || yHi === null || yLo === null) return;
    if (yClick < yHi - 8 || yClick > yLo + 8) return;
    const v = state.bars.find((x) => x.time === param.time);
    pins.toggle({ ...b, volume: v ? v.volume : 0 });
  });
  document.addEventListener("charto:unpin", (e) => pins.remove(e.detail));

  // ── theme toggle ──────────────────────────────────────
  // ── screenshot: the chart (all panes), never the chat or the shell ──
  // LWC renders everything — candles, panes, axes, our primitives — into
  // its own canvases, so takeScreenshot() is exactly "the chart and
  // nothing else". `rect` (container px) crops it; either way the result
  // is downscaled so image tokens stay sane.
  function captureChart(rect) {
    const full = chart.takeScreenshot();
    const sx = full.width / chartEl.clientWidth;
    const sy = full.height / chartEl.clientHeight;
    let c = full;
    if (rect) {
      c = document.createElement("canvas");
      c.width = Math.max(1, Math.round(rect.w * sx));
      c.height = Math.max(1, Math.round(rect.h * sy));
      c.getContext("2d").drawImage(
        full, rect.x * sx, rect.y * sy, rect.w * sx, rect.h * sy,
        0, 0, c.width, c.height);
    }
    const MAX_W = 1280;
    if (c.width > MAX_W) {
      const d = document.createElement("canvas");
      d.width = MAX_W;
      d.height = Math.round(c.height * (MAX_W / c.width));
      d.getContext("2d").drawImage(c, 0, 0, d.width, d.height);
      c = d;
    }
    document.dispatchEvent(new CustomEvent("charto:screenshot", {
      detail: { uri: c.toDataURL("image/png") },
    }));
    status("screenshot captured — attach it in the chat");
  }

  /** Drag a marquee over the chart; the selection becomes the screenshot.
   *  Esc or a sub-24px drag cancels. The overlay swallows every pointer
   *  event, so the drawing tools cannot fire mid-selection. */
  function selectRegionCapture() {
    const ov = document.createElement("div");
    ov.className = "shot-overlay";
    ov.innerHTML = '<div class="shot-hint">drag to capture · Esc to cancel</div>';
    chartEl.appendChild(ov);
    let mq = null, x0 = 0, y0 = 0;
    const off = () => {
      ov.remove();
      document.removeEventListener("keydown", onKey, true);
    };
    const onKey = (e) => {
      if (e.key === "Escape") { e.stopPropagation(); off(); }
    };
    document.addEventListener("keydown", onKey, true);
    ov.addEventListener("mousedown", (e) => {
      const r = ov.getBoundingClientRect();
      x0 = e.clientX - r.left; y0 = e.clientY - r.top;
      mq = document.createElement("div");
      mq.className = "shot-marquee";
      ov.appendChild(mq);
      e.preventDefault();
    });
    ov.addEventListener("mousemove", (e) => {
      if (!mq) return;
      const r = ov.getBoundingClientRect();
      const x1 = e.clientX - r.left, y1 = e.clientY - r.top;
      Object.assign(mq.style, {
        left: `${Math.min(x0, x1)}px`, top: `${Math.min(y0, y1)}px`,
        width: `${Math.abs(x1 - x0)}px`, height: `${Math.abs(y1 - y0)}px`,
      });
    });
    ov.addEventListener("mouseup", (e) => {
      if (!mq) return;
      const r = ov.getBoundingClientRect();
      const x1 = e.clientX - r.left, y1 = e.clientY - r.top;
      const rect = { x: Math.min(x0, x1), y: Math.min(y0, y1),
                     w: Math.abs(x1 - x0), h: Math.abs(y1 - y0) };
      off();
      if (rect.w < 24 || rect.h < 24) { status("selection too small — cancelled"); return; }
      captureChart(rect);
    });
  }

  el("shotBtn").innerHTML = Icons.svg("camera", "sm");
  el("shotBtn").addEventListener("click", (e) => {
    e.stopPropagation();
    closeMenus(el("shotMenu"));
    el("shotMenu").classList.toggle("open");
  });
  el("shotMenu").addEventListener("click", (e) => {
    const it = e.target.closest("[data-shot]");
    if (!it) return;
    el("shotMenu").classList.remove("open");
    if (it.dataset.shot === "full") captureChart(null);
    else selectRegionCapture();
  });

  const themeBtn = el("themeToggle");
  function paintThemeBtn() {
    // show what you'd switch TO, the way macOS/Linear do it
    themeBtn.innerHTML = Theme.mode === "dark" ? Icons.svg("sun", "sm") : Icons.svg("moon", "sm");
    themeBtn.title = Theme.mode === "dark" ? "Switch to light" : "Switch to dark";
  }
  themeBtn.addEventListener("click", () => { Theme.toggle(); });
  Theme.onChange(() => {
    paintThemeBtn();
    chart.applyOptions(chartTheme());
    candle.applyOptions({
      upColor: Theme.c("up"), downColor: Theme.c("down"),
      wickUpColor: Theme.c("up"), wickDownColor: Theme.c("down"),
    });
    if (state.bars.length) {          // a toggle mid-load must not wipe series
      volume.setData(state.bars.map(({ time, volume: v, open, close }) => ({
        time, value: v, color: close >= open ? Theme.c("volUp") : Theme.c("volDown"),
      })));
      ind.retheme(state.bars);
    }
    draw.requestUpdate();
    scene.requestUpdate();
  });
  paintThemeBtn();

  el("chatToggle").innerHTML = Icons.svg("chat", "sm") + "Chat";

  // A reload refreshes the PRICE, not the session: re-fetch bars (which lands
  // the view back at the live edge) and put back everything the user built.
  // Drawings restore themselves — drawings.js reads its own store at create().
  (async function boot() {
    await Indicators.loadCatalogue(API);
    ind.setContext({ interval: Store.get("interval", "5m") });
    renderIndMenu();
    const saved = Store.get("interval", "5m");
    const iv = IV_SEC[saved] ? saved : "5m";
    selectInterval(iv);
    await loadInterval(iv);

    for (const id of Store.get("indicators", [])) {
      // dynamic defs (rsi26) don't survive a reload — re-mint from the id
      const rid = ind.ensureFromId(id);
      const def = rid && ind.CATALOG.find((c) => c.id === rid);
      // VWAP is session-anchored — silently skip it if the saved interval is
      // daily+, rather than restoring an indicator that can't mean anything
      if (!def || ind.isActive(rid)) continue;
      if (def.intradayOnly && DAILY.has(state.interval)) continue;
      // never swallow: a restore that fails silently leaves a chip on
      // screen with no series under it, which looks like a render bug
      await Promise.resolve(ind.toggle(rid, state.bars))
        .catch((err) => { console.error("[charto] indicator restore failed", rid, err); });
    }
    renderChips();
    // panes created during restore need every layer's primitives attached —
    // the same signal a live indicator change sends
    document.dispatchEvent(new CustomEvent("charto:indicators-changed"));

    const levels = Store.get("scene", []);
    if (levels.length) {
      scene.apply(levels);
      // boot-time loadInterval ran before the scene was restored, so its
      // coverage check saw an empty scene — run it again now
      coverScene();
    }
  })();

  // expose for debugging + the chat pane
  window.__charto = { chart, candle, state, draw, ind, scene, pins, getChartContext };
})();
