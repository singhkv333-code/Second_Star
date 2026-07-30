/* Charto preview — main wiring.
 * Data: local 1-min store via dataserver on :5174 (RELIANCE only for now).
 * All chart times are IST-shifted (+19800) so the axis reads IST regardless
 * of browser timezone; shift is removed when talking to the server.
 */
"use strict";

(function () {
  const LWC = window.LightweightCharts;
  const API = "http://127.0.0.1:5174";
  // Pivot's stock page, copied into charto/web and served by `next dev`
  // there (see charto/web/README). Company links open it directly.
  const COMPANY_PAGE = "http://localhost:5175";

  // Per-symbol: +05:30 for Indian instruments, 0 for crypto (whose bars the
  // dataserver folds on UTC midnight). Every `+ IST` / `- IST` below is a
  // shift between raw exchange time and chart time, so this one binding
  // switches the whole axis.
  const IST = Sym.tz;
  const SYMBOL = (new URLSearchParams(location.search).get("symbol")
                  || "RELIANCE").toUpperCase();
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
    switching: false,  // interval switch in flight — stream events wait it out
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
    // A new interval can move an indicator in or out of the timeframes its
    // Visibility tab allows, and the chip is where that is legible — without
    // this the plot vanishes on 1h while its chip still reads as live.
    ind.recomputeAll(state.bars, { interval: state.interval, limit: state.bars.length })
      .then(() => renderChips());
  }

  async function loadInterval(interval) {
    state.interval = interval;
    setOverlay(true, "Loading…");
    const t0 = performance.now();
    state.switching = true;   // latch: stream events must not touch the old series mid-switch
    try {
      const { bars, hasMore } = await fetchBars(interval, null, PAGE[interval]);
      state.bars = bars; state.hasMore = hasMore;
      chart.applyOptions({ timeScale: { timeVisible: !["1d", "1w", "1mo"].includes(interval) } });
      paintTitle();
      paint();
      // fresh data, fresh view: hand the price scale back to auto-fit
      eachPriceScale((ps) => ps.applyOptions({ autoScale: true }));
      paintAutoPill();
      chart.timeScale().setVisibleLogicalRange({ from: bars.length - 180, to: bars.length + 6 });
      lastBar = bars[bars.length - 1];
      paintReadout(lastBar);
      el("barsLine").textContent = `${bars.length.toLocaleString()} × ${interval}`;
      status(`${interval}: ${bars.length} bars in ${Math.round(performance.now() - t0)}ms · last ${Sym.price(lastBar.close)}`);
      setOverlay(false);
      // drawings live in time, not in any one interval — make sure the new
      // interval's data actually reaches back to what is on the chart
      coverScene();
    } catch (e) {
      setOverlay(true, String(e.message || e), true);
    } finally {
      state.switching = false;
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

  // ── live stream ───────────────────────────────────────
  // One EventSource for the whole session: every event carries the forming bar
  // of every interval, so switching interval needs no new stream. The browser
  // reconnects on its own.
  let indTimer = null, es = null;
  function openStream() {
    if (es) return;
    es = new EventSource(`${API}/stream?symbol=${encodeURIComponent(SYMBOL)}`);
    es.onmessage = (msg) => {
      if (state.loadingOlder || state.switching) return;   // bars array is being rewritten
      let ev;
      try { ev = JSON.parse(msg.data); } catch { return; }
      if (!ev || ev.type !== "bar" || !ev.bars) return;
      const b = ev.bars[state.interval];
      if (!b) return;                   // 1w/1mo aren't streamed
      const bar = { time: b.t + IST, open: b.o, high: b.h, low: b.l, close: b.c, volume: b.v };
      const last = state.bars[state.bars.length - 1];
      if (last && last.time === bar.time) state.bars[state.bars.length - 1] = bar;
      else if (last && bar.time < last.time) return;   // stale/out-of-order
      else state.bars.push(bar);
      candle.update({ time: bar.time, open: bar.open, high: bar.high, low: bar.low, close: bar.close });
      volume.update({ time: bar.time, value: bar.volume,
        color: bar.close >= bar.open ? Theme.c("volUp") : Theme.c("volDown") });
      lastBar = bar;
      paintReadout(lastBar);
      // the server owns indicator math — refresh at most once a second, and a
      // fast stream of closes must not keep pushing the refresh into the future
      if (ev.closed_1m && !indTimer) {
        indTimer = setTimeout(() => {
          indTimer = null;
          ind.recomputeAll(state.bars, { interval: state.interval, limit: state.bars.length });
        }, 1000);
      }
    };
    es.onerror = () => status("live stream reconnecting…");
  }

  // ── readout ───────────────────────────────────────────
  let lastBar = null;
  /** Same legend shape the secondary panes use, so a split shows one chart
   *  twice rather than two differently-labelled ones. */
  function paintTitle() {
    // The venue comes from Sym, not a literal: this legend sits directly under
    // a badge that already reads BYBIT on a Bitcoin chart, and the two saying
    // different exchanges about the same instrument is worse than either.
    el("roTitle").innerHTML = `${SYMBOL}<span class="sep">·</span>${state.interval}`
      + `<span class="sep">·</span><span class="ex">${Sym.venue}</span>`;
  }

  function paintReadout(b) {
    if (!b) { el("roOhlc").innerHTML = ""; return; }
    const cls = b.close >= b.open ? "up" : "down";
    const f = (n) => Sym.num(n);
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
  /** The chips ARE TradingView's indicator legend: label, then the eye,
   *  the gear and the × that appear under the cursor. Everything editable
   *  about an indicator is behind the gear now — the old click-the-label
   *  period box could change one number out of the eight the dialog owns. */
  function renderChips() {
    el("indChips").innerHTML = [...ind.active.keys()].map((id) => {
      const c = ind.CATALOG.find((q) => q.id === id);
      const hidden = ind.isHidden(id);
      const off = !hidden && ind.offInterval(id);
      const cls = hidden ? " muted" : (off ? " off" : "");
      return `<span class="chip${cls}" data-ind-chip="${id}">` +
        `<span class="lbl">${c.label}</span>` +
        `<span class="acts">` +
        `<span class="act${hidden ? " pinned" : ""}" data-eye="${id}" ` +
          `title="${hidden ? "Show" : "Hide"}">${Icons.svg(hidden ? "eyeOff" : "eye")}</span>` +
        `<span class="act" data-cfg="${id}" title="Settings">${Icons.svg("settings")}</span>` +
        `<span class="act rm" data-rm="${id}" title="Remove">${Icons.svg("x")}</span>` +
        `</span></span>`;
    }).join("");
    // the chips ARE the active set, so this is the one honest save point —
    // it catches the menu, the chip's x, and anything the chat adds
    Store.set("indicators", [...ind.active.keys()]);
    if (window.__chartoSyncChips) window.__chartoSyncChips();
  }

  /** Open the settings dialog on one indicator. Every edit inside it applies
   *  live and persists itself; all this has to do is keep the chips honest. */
  function openIndSettings(id) {
    IndSettings.open(ind, id, {
      subtitle: `${SYMBOL} · ${state.interval}`,
      onChange: () => {
        renderChips();
        document.dispatchEvent(new CustomEvent("charto:indicators-changed"));
      },
    });
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
      // the failure path MUST re-render too. The optimistic pass below draws
      // the chip and the tick while the fetch is still in flight; without a
      // re-render here a refused indicator (a volume study on an index, which
      // has no volume to compute from) stayed checked in the menu and kept a
      // chip in the toolbar, reading as active over a pane that never drew.
      .catch((err) => {
        renderIndMenu(); renderChips();
        status(`could not add ${def.label}: ${err.message}`);
      });
    renderIndMenu(); renderChips();   // optimistic: chip appears immediately
  });
  // ── the chip strip scrolls sideways ───────────────────
  // Three ways in, because a strip that silently continues past its edge is
  // the same as one that is cut off: ARROWS say there is more, DRAG is what
  // a hand reaches for first, and the WHEEL is what a mouse has. The arrows
  // appear only on a side that actually has somewhere to go.
  (function chipScroller() {
    const strip = el("indChips");
    const left = el("chipsLeft"), right = el("chipsRight");
    left.innerHTML = Icons.svg("chevronLeft");
    right.innerHTML = Icons.svg("chevronRight");

    const wrap = strip.parentElement;
    function sync() {
      const over = strip.scrollWidth - strip.clientWidth;
      const x = strip.scrollLeft;
      // both slots come and go together; only their VISIBILITY tracks which
      // direction has somewhere to go, so reaching an end never reflows
      const canL = over > 1 && x > 1, canR = over > 1 && x < over - 1;
      wrap.classList.toggle("scrollable", over > 1);
      left.classList.toggle("off", !canL);
      right.classList.toggle("off", !canR);
      // the strip fades on whichever side still has chips past the edge
      strip.classList.toggle("can-left", canL);
      strip.classList.toggle("can-right", canR);
    }
    const page = () => Math.max(80, strip.clientWidth * 0.7);
    left.addEventListener("click", (e) => {
      e.stopPropagation(); strip.scrollLeft -= page();
    });
    right.addEventListener("click", (e) => {
      e.stopPropagation(); strip.scrollLeft += page();
    });

    strip.addEventListener("wheel", (e) => {
      if (strip.scrollWidth <= strip.clientWidth) return;
      const by = Math.abs(e.deltaX) > Math.abs(e.deltaY) ? e.deltaX : e.deltaY;
      if (!by) return;
      e.preventDefault();
      strip.scrollLeft += by;
    }, { passive: false });

    // Drag to pan. A press that never travels is still a click on the chip
    // under it, so the threshold decides between the two and a real drag
    // swallows the click that follows it.
    let down = null, moved = false;
    strip.addEventListener("pointerdown", (e) => {
      if (strip.scrollWidth <= strip.clientWidth) return;
      if (e.target.closest(".act")) return;      // eye / gear / × stay clickable
      down = { x: e.clientX, at: strip.scrollLeft, id: e.pointerId };
      moved = false;
    });
    addEventListener("pointermove", (e) => {
      if (!down || e.pointerId !== down.id) return;
      const dx = e.clientX - down.x;
      if (!moved && Math.abs(dx) < 4) return;
      if (!moved) { moved = true; strip.classList.add("dragging"); }
      strip.scrollLeft = down.at - dx;
    });
    addEventListener("pointerup", (e) => {
      if (!down || e.pointerId !== down.id) return;
      down = null;
      strip.classList.remove("dragging");
      if (moved) {
        // one-shot capture: kill the click this drag would otherwise fire
        strip.addEventListener("click", (ev) => {
          ev.stopPropagation(); ev.preventDefault();
        }, { capture: true, once: true });
      }
    });

    strip.addEventListener("scroll", sync, { passive: true });
    addEventListener("resize", sync);
    document.addEventListener("charto:indicators-changed", () => setTimeout(sync, 0));
    // The arrows are only honest if this ran against the CURRENT geometry.
    // A window resize is not the only thing that changes it — a chip's label
    // reflows when its webfont lands, and the strip's own share of the header
    // changes when a neighbour appears. Watch the box itself.
    if (window.ResizeObserver) new ResizeObserver(sync).observe(strip);
    sync();
    window.__chartoSyncChips = sync;
  })();

  el("indChips").addEventListener("click", (e) => {
    const eye = e.target.closest("[data-eye]");
    if (eye) {
      e.stopPropagation();
      ind.setHidden(eye.dataset.eye, !ind.isHidden(eye.dataset.eye));
      renderChips();
      return;
    }
    const cfg = e.target.closest("[data-cfg]");
    if (cfg) {
      e.stopPropagation();
      openIndSettings(cfg.dataset.cfg);
      return;
    }
    const x = e.target.closest("[data-rm]");
    if (x) {
      ind.remove(x.dataset.rm);
      renderChips();
      // the shared signal every other removal path sends — without it the
      // orphan purge and pane sync never hear about the chip's ×
      document.dispatchEvent(new CustomEvent("charto:indicators-changed"));
      return;
    }
    // anywhere else on the chip is the gear, the way double-clicking a
    // TradingView legend row opens its settings
    const chip = e.target.closest("[data-ind-chip]");
    if (chip) { e.stopPropagation(); openIndSettings(chip.dataset.indChip); }
  });

  /** Close every open dropdown except `keep`. Shared by header + composer. */
  function closeMenus(keep) {
    document.querySelectorAll(".dropdown.open").forEach((d) => {
      if (d !== keep) d.classList.remove("open");
    });
  }
  document.addEventListener("click", () => closeMenus(null));
  window.__chartoCloseMenus = closeMenus;

  // Input-modality flag the stylesheet gates .btn's focus ring on: Tab arms
  // it, any pointer press disarms it. Keyboard users still get the ring;
  // clicking a menu button no longer leaves a teal outline sitting on it.
  addEventListener("keydown", (e) => {
    if (e.key === "Tab") document.documentElement.dataset.nav = "kbd";
  }, true);
  addEventListener("pointerdown", () => {
    delete document.documentElement.dataset.nav;
  }, true);

  // keep the chips honest when the chat adds an indicator
  document.addEventListener("charto:indicators-changed", renderChips);

  // ── interval buttons ──────────────────────────────────
  el("intervalSeg").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-iv]");
    if (!b) return;
    // One toolbar, aimed at whichever pane is selected. When a secondary pane
    // has the selection this must NOT touch the primary chart — the interval
    // is a property of the pane you clicked, not of the app.
    if (Panes.setIntervalOnActive(b.dataset.iv)) return markInterval(b.dataset.iv);
    selectInterval(b.dataset.iv);
    loadInterval(b.dataset.iv);
  });

  /** Paint the segmented control without claiming it as the primary's state. */
  function markInterval(iv) {
    [...el("intervalSeg").children].forEach((x) =>
      x.classList.toggle("active", x.dataset.iv === iv));
  }

  function selectInterval(iv) {
    markInterval(iv);
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

  /** Open or close one group's flyout. The wrap gets the state too, because
   *  the button's tooltip has to know: both land in the same slot beside the
   *  rail, and the tooltip is on the higher layer, so an open menu would
   *  otherwise be wearing its own title stamped across it. */
  function setToolMenu(gid, open) {
    const menu = el(`menu-${gid}`);
    if (!menu) return;
    menu.classList.toggle("open", open);
    menu.parentElement.classList.toggle("menu-open", open);
  }
  function closeToolMenus(except) {
    for (const g of Tools.GROUPS) {
      if (g.id === except) continue;
      setToolMenu(g.id, false);
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
      setToolMenu(wrap.dataset.group, true);
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
      a.download = `charto_drawings_${SYMBOL}.json`;
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

    // Chat-drawn annotations, CURRENT geometry — the user can drag these,
    // so the backend must read them from here, not from what it drew
    const chat_drawings = scene.state.items.slice(0, 20).map((a) => {
      const g = a.kind === "level" ? { price: r2(a.price) }
        : a.kind === "zone" ? { lo: r2(a.lo), hi: r2(a.hi) }
        : (a.kind === "segment" || a.kind === "fib")
          ? { p1: { t: T(a.p1.t), p: r2(a.p1.v) }, p2: { t: T(a.p2.t), p: r2(a.p2.v) } }
        : a.kind === "box"
          ? { a: { t: T(a.a.t), p: r2(a.a.v) }, b: { t: T(a.b.t), p: r2(a.b.v) } }
        : a.kind === "position"
          ? { side: a.side, entry: r2(a.entry), stop: r2(a.stop),
              targets: (a.targets || []).map(r2), qty: a.qty || undefined,
              risk_amount: a.risk_amount || undefined }
        : null;
      return g && { id: a.id, kind: a.kind, on: paneLabel(a.pane),
                    label: a.label || undefined,
                    adjusted: a.adjusted || undefined, ...g };
    }).filter(Boolean);

    return {
      symbol: SYMBOL, exchange: Sym.venue,
      source: `local 1-min store (${Sym.feed})`,
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
      chat_drawings: chat_drawings.length ? chat_drawings : undefined,
      drawings_omitted: Math.max(0, draw.state.drawings.length - 15) || undefined,
      // A pin carries the interval it was taken on, so it is stamped and
      // formatted by ITS OWN bar size — a daily pin printed as "09:15" while
      // the chart sits on 5m would read as an intraday bar to the model.
      pins: pins.list().map((p) => {
        const iv = p.interval || state.interval;
        return {
          t: fmtIST(p.time, !DAILY.has(iv)), interval: iv,
          o: r2(p.open), h: r2(p.high),
          l: r2(p.low), c: r2(p.close), v: p.volume,
        };
      }),
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
      indexChatRefs();        // new annotations → new mentions to link
    },
    onHover: (a, y) => {
      const s = (a && a.source) || {};
      el("drawStatus").textContent = a
        ? [a.label, s.strength, s.last_touch && `last ${s.last_touch}`,
           s.method, s.bars_scanned && `${s.bars_scanned} ${s.interval || ""} bars`,
           a.adjusted && "user-adjusted"]
            .filter(Boolean).join(" · ")
        : "";
      // Hovering an annotation answers two questions at once: WHAT is this
      // (the card) and WHERE did chat say it (the mention lights up).
      if (a) { clearTimeout(peekTimer); peekProvenance(a); markChatRefs(a.id); }
      else {
        // Grace period, not an instant dismissal. The card is docked away
        // from the annotation, so reaching it means crossing bare chart —
        // and hiding on the first pixel of that journey is what made "click
        // to pin" impossible to act on. Entering the card cancels the timer.
        clearTimeout(peekTimer);
        peekTimer = setTimeout(() => { hidePeek(); markChatRefs(null); }, 650);
      }
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
    onIndicatorRemove: (a) => {              // "remove the rsi"
      const raw = String(a.name || "");
      const name = raw.split("@")[0];
      const period = a.period || Number(raw.split("@")[1]) || 0;
      // no period → every variant of the name goes; a period targets one
      const victims = [...ind.active.keys()].filter((id) => {
        const d = ind.CATALOG.find((c) => c.id === id);
        return d && d.name === name && (!period || d.period === period);
      });
      victims.forEach((id) => ind.remove(id));
      if (victims.length) {
        renderChips();
        document.dispatchEvent(new CustomEvent("charto:indicators-changed"));
      }
    },
    isCursorMode: () => draw.state.tool === "cursor",
    // drawings.js's mousedown runs first (attached earlier); if it took the
    // press — drag, handle, or a click spent deselecting — the scene must
    // not also start a drag under it
    userBusy: () => draw.state.consumedDown || draw.state.draft !== null,
    // Clicking an annotation opens nothing: the hover card already showed
    // everything, and a click-card was a second surface saying the same
    // thing. Clicking still lands on the chart for the candle-pin path.
    onSelect: () => {},
  });
  el("sceneClear").innerHTML = Icons.svg("eraser", "sm");
  el("sceneClear").addEventListener("click", () => scene.clear());
  document.addEventListener("charto:indicators-changed", () => {
    // marks on a pane whose indicator is gone are orphans: invisible, yet
    // still counted by the badge and revived if the pane ever returns —
    // they die with their pane, whichever path removed it (chat, chip x,
    // clear-all)
    const alive = new Set();
    for (const id of ind.active.keys()) {
      const d = ind.CATALOG.find((c) => c.id === id);
      if (d) alive.add(`${d.name}@${d.period}`);
    }
    const dead = [...new Set(scene.state.items
      .map((a) => a.pane)
      .filter((p) => p && p !== "price" && !alive.has(p)))];
    if (dead.length) {
      scene.apply(dead.map((p) => ({ kind: "clear", scope: "pane", pane: p })));
    }
    scene.syncPanes();
  });

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
  let peekTimer = 0;

  /** Park the card in its bottom-left dock.
   *
   *  Both openers used to set `top` from the pointer while the stylesheet
   *  pinned `bottom`; with both edges fixed the card stretched the full
   *  height of the chart. Clearing the inline edges is what keeps it a card. */
  function dockProv() {
    prov.style.top = "auto";
    prov.style.right = "auto";
  }
  function hideProvenance() {
    clearTimeout(peekTimer);
    prov.classList.remove("open");
    provFor = null;
    // Dismissing the card ends the whole hover conversation — leaving the
    // chat mention lit after its card is gone strands a highlight with
    // nothing pointing at it.
    if (typeof markChatRefs === "function") markChatRefs(null);
  }

  /** The card's body for one annotation.
   *
   *  Every field here is optional — detectors differ, and a pattern segment
   *  carries none of a level's re-test evidence. So nothing is interpolated
   *  blind: a missing number must drop its whole row rather than render as
   *  "NaN" or "undefined · confirmed". An absent measurement is stated by
   *  its absence; it is never dressed up as one.
   */
  function provHTML(a) {
    const s = a.source || {};
    const row = (k, v) => (v ? `<dt>${k}</dt><dd>${v}</dd>` : "");
    const num = (n) => (Number.isFinite(Number(n))
      ? Sym.num(n, { minimumFractionDigits: 2 }) : null);
    // join only the parts that exist, so one missing half never poisons a row
    const dot = (...p) => p.filter((x) => x != null && x !== "").join(" · ");
    // Detector enums arrive snake_cased ("not_assessed"); the card is prose,
    // so it reads them as words rather than as a database value. Some of
    // those enums are ABSENCES wearing a value's clothes — "Status: not
    // assessed" spends a whole row saying the detector had nothing to say.
    // They resolve to null, and row() then drops the row entirely, which is
    // the same statement without the furniture.
    const BLANK = new Set(["not_assessed", "unassessed", "unknown", "none",
                           "n/a", "na", "null", "undefined", ""]);
    const words = (v) => {
      if (typeof v !== "string") return v;
      return BLANK.has(v.trim().toLowerCase()) ? null : v.replace(/_/g, " ");
    };

    /** "28 Jul 2026 · 10:35 → 15:30" when both ends fall on the same day.
     *  Chart times are "DD Mon YYYY HH:MM", and a shape that lives inside one
     *  session was printing its date twice — which wrapped the row onto two
     *  lines to repeat itself. */
    const span = (from, to) => {
      if (!from || !to) return from || to || "";
      const cut = (t) => /^(.*\d{4})\s+(\d{1,2}:\d{2})$/.exec(String(t).trim());
      const f = cut(from), t2 = cut(to);
      return f && t2 && f[1] === t2[1]
        ? `${f[1]} · ${f[2]} → ${t2[2]}` : `${from} → ${to}`;
    };

    let title, body, kindName = null;
    if (s.tool === "get_patterns") {
      // A pattern's `role` is only a COLOUR decision — the detector maps
      // bullish→support, bearish→resistance so the shape inherits the right
      // hue. Printing that as the card's identity told you a double top was
      // a "RESISTANCE", which is a different kind of object entirely. The
      // formation's own name is the identity; the bias is one of its facts.
      // A formation is drawn as several linked pieces — outline, fill, upper
      // and lower edge, neckline — and only some carry the label. Hovering
      // the fill must still say "double top", so the name is resolved across
      // the whole `link` group rather than off the piece under the pointer.
      const kin = a.link
        ? scene.state.items.filter((q) => q.link === a.link && q.label)
        : (a.label ? [a] : []);
      const named = kin.find((q) => q.label) || a;
      // The label carries the detector's OWN facts — "falling wedge · width
      // 2.78 · unresolved", "double top · neckline 1,271.00 · confirmed".
      // Only the first segment was being read, as the name, and the rest was
      // dropped, which is why a pattern card had less to say than the chat
      // message that drew the pattern. Each remaining segment is either a
      // measurement ("<name> <number>") or the formation's state.
      const parts = String(named.label || "").split("·")
        .map((x) => x.trim()).filter(Boolean);
      kindName = parts.shift() || "Pattern";
      const cap = (t) => t.charAt(0).toUpperCase() + t.slice(1);
      let stated = false;
      const facts = parts.map((p) => {
        const m = /^(.+?)\s+(-?[\d,]+(?:\.\d+)?%?)$/.exec(p);
        if (m) return row(cap(m[1]), m[2]);
        stated = true;
        return row("Status", p);
      }).join("");
      title = null;
      body = facts
        + (stated ? "" : row("Status", words(s.strength)))
        + row("Bias", a.role === "support" ? "bullish"
          : a.role === "resistance" ? "bearish" : "neutral")
        + row("Spans", span(s.first_touch, s.last_touch));
    } else if (s.tool === "get_trendlines") {
      kindName = "Trendline";
      title = null;
      body = row("Record", dot(s.touches ? `${s.touches} touches` : null, words(s.strength)))
        + row("Anchored", span(s.first_touch, s.last_touch));
    } else if (s.tool === "get_divergences") {
      title = `${words(s.strength) || ""} divergence`.trim();
      body = row("Record", words(s.record))
        + row("Instances", s.touches ? `${s.touches} in this window` : "")
        + row("Spans", span(s.first_touch, s.last_touch));
    } else {
      const ev = s.evidence || {};
      const graded = (ev.held || 0) + (ev.broke || 0);
      title = a.kind === "zone"
        ? (num(a.lo) && num(a.hi) ? `${num(a.lo)}–${num(a.hi)}` : null)
        : num(a.price);
      body = row("Record", !graded
        ? (words(s.record) || (Number.isFinite(Number(a.price)) || a.kind === "zone"
            ? "never re-tested" : ""))
        : dot(`held ${ev.held} of ${graded}`,
              ev.hold_rate == null ? null : `${ev.hold_rate}%`))
        + row("Reaction", ev.react_pct == null ? ""
          : `${ev.react_pct}% ${a.role === "resistance" ? "down" : "up"}`
            + (ev.react_bars == null ? "" : `, median ${ev.react_bars} bars`))
        + row("Judged", graded && s.horizon_bars
          ? `re-tests only, ${s.horizon_bars} bars each` : "")
        + row("Touches", dot(s.touches, words(s.strength)))
        // A level with one touch has the same timestamp at both ends, and
        // printing it twice under two different labels reads as two events.
        + (s.first_touch && s.first_touch === s.last_touch
            ? row("Touched", s.first_touch)
            : row("First", s.first_touch) + row("Last", s.last_touch));
    }
    // Support/Resistance is the identity of a LEVEL. For anything else the
    // detector's own name leads, and role survives only as the colour.
    const heading = title || (kindName ? "" : a.label || "");
    const roleName = kindName || (a.role === "resistance" ? "Resistance"
      : a.role === "support" ? "Support" : (a.label || "Annotation"));
    return `
      <header>
        ${s.tool === "get_divergences" ? ""   // "bearish" already says the side
          : `<span class="role ${kindName ? "" : (a.role || "")}">${roleName}</span>`}
        ${heading && heading !== roleName ? `<span class="price">${heading}</span>` : ""}
        <button class="btn icon" data-act="close" title="Close">${Icons.svg("x", "xs")}</button>
      </header>
      <dl>
        ${body}
      </dl>
      <footer>
        <button class="btn cta" data-act="ask">${Icons.svg("chat", "xs")}Ask about this</button>
        <button class="btn danger" data-act="remove">${Icons.svg("eraser", "xs")}Remove</button>
      </footer>`;
  }


  /** The annotation card. Raised by hover, dismissed by leaving — there is
   *  no second, pinned variant of it. */
  function peekProvenance(a) {
    if (provFor === a.id && prov.classList.contains("open")) return;
    provFor = a.id;
    prov.innerHTML = provHTML(a);
    dockProv();
    prov.classList.add("open", "peek");
  }
  function hidePeek() {
    if (prov.classList.contains("peek")) hideProvenance();
  }

  // Reaching the card keeps it; leaving it lets it go.
  prov.addEventListener("mouseenter", () => clearTimeout(peekTimer));
  prov.addEventListener("mouseleave", () => {
    if (!prov.classList.contains("peek")) return;
    clearTimeout(peekTimer);
    peekTimer = setTimeout(() => { hidePeek(); markChatRefs(null); }, 220);
  });

  /* A card that cannot be dismissed is worse than no card. The peek closes
   * itself on hover-out, but hover-out only fires while the pointer is still
   * ON the chart — walk it off to the chat or the header and the old code
   * left the card parked over the candles with its close button disabled by
   * `pointer-events: none`. Every exit is wired now: leave the stage, press
   * Escape, or click anywhere that is not the card. */
  stageEl.addEventListener("mouseleave", () => { scene.setHover(null); hidePeek(); });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && prov.classList.contains("open")) {
      e.stopPropagation();
      hideProvenance();
      provDraw = null;
    }
  });
  document.addEventListener("pointerdown", (e) => {
    if (!prov.classList.contains("open")) return;
    if (prov.contains(e.target)) return;              // clicks inside are actions
    // A click on the chart closes UNLESS it landed on an annotation — that
    // case is a re-pin, and scene's own click handler will reopen the card.
    if (stageEl.contains(e.target)) {
      const p = scene.hitAt(yInPane(e.clientY, paneAtClient(e.clientY)),
                            paneAtClient(e.clientY),
                            e.clientX - stageEl.getBoundingClientRect().left);
      if (p) return;
    }
    hideProvenance(); provDraw = null;
  });

  /* ══════════════════════════════════════════════════════════════════
     Chart ⇄ chat cross-highlight.

     Chat and chart are already talking about the same objects — the chat
     writes "Falling wedge", the scene draws one — but nothing connected the
     two, so finding the sentence that explains a line meant re-reading the
     whole reply. This wires them: hover the annotation, its mention lights
     up; hover the mention, the annotation lights up.

     It decides nothing. It matches an annotation's OWN label and price
     against the text, and both were minted by the same tool call — so this
     is a pointer between two renderings of one fact, not a second opinion
     about what the fact is.
     ══════════════════════════════════════════════════════════════════ */
  const threadEl = el("thread");
  const escRe = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

  /** Every string this annotation could plausibly be called in prose.
   *
   *  The label is the richest source and the one the reply actually echoes:
   *  a zone chipped "R 1,276.63 · held 2/2" is written "1,276.63" in the
   *  text, while its lo/hi are the band edges and appear nowhere. So the
   *  number is lifted OUT of the label rather than recomputed from the
   *  geometry — same string, same rounding, guaranteed to match.
   */
  function refStrings(a) {
    const out = [];
    const label = String(a.label || "").trim();
    if (label.length >= 3) {
      // a name, if there is one: "Falling wedge — 28 Jul, 10:35" → "Falling wedge"
      const name = label.split(/\s+[—–·|(]\s*|\s+-\s+/)[0].trim();
      if (/[a-z]{3}/i.test(name)) out.push(name);
      if (/[a-z]{3}/i.test(label) && label !== name) out.push(label);
      // …and every price-shaped token inside it. A bare "2" (from "held 2/2")
      // would match half the transcript, so a ref must carry a separator.
      for (const tok of label.match(/\d[\d,]*\.\d+|\d{1,3}(?:,\d{2,3})+/g) || []) {
        if (tok.length >= 4) out.push(tok);
      }
    }
    // Fall back to the geometry when a level carries no label at all.
    const price = (v) => {
      const n = Number(v);
      if (!Number.isFinite(n)) return;
      // these strings are searched for inside the chat text, so they must be
      // formatted exactly as the reply formatted them — i.e. on the symbol's
      // locale, not always India's
      out.push(Sym.num(n, { minimumFractionDigits: 2 }));
      out.push(Sym.num(n, { minimumFractionDigits: 1 }));
    };
    if (!out.length) {
      if (a.kind === "level") price(a.price);
      if (a.kind === "zone") { price(a.lo); price(a.hi); }
    }
    return [...new Set(out)].filter((s) => s.length >= 3);
  }

  let indexing = false;
  let refObserver = null;
  /** Wrap mentions in the thread. Idempotent: it unwraps its own spans first,
   *  so a re-run after new text never nests or double-counts.
   *
   *  The observer is DISCONNECTED for the duration, not just flag-guarded.
   *  A flag doesn't work: MutationRecords are delivered in a microtask after
   *  this returns, so our own unwrap/rewrap arrives once the flag is already
   *  back down and schedules another pass — a 220ms rewrite loop that also
   *  drops the .hot class mid-hover. Disconnect, then takeRecords() to bin
   *  what we caused before listening again. */
  function indexChatRefs() {
    if (indexing || !threadEl) return;
    indexing = true;
    if (refObserver) refObserver.disconnect();
    try {
      for (const n of [...threadEl.querySelectorAll("span.ann-ref")]) {
        n.replaceWith(document.createTextNode(n.textContent));
      }
      threadEl.querySelectorAll(".prose").forEach((p) => p.normalize());

      const pairs = [];
      for (const a of scene.state.items) {
        for (const s of refStrings(a)) pairs.push([s, a.id]);
      }
      if (!pairs.length) return;
      // longest first: "Falling wedge" must win over a bare "wedge"
      pairs.sort((x, y) => y[0].length - x[0].length);
      const byText = new Map();
      for (const [s, id] of pairs) {
        if (!byText.has(s.toLowerCase())) byText.set(s.toLowerCase(), id);
      }
      const re = new RegExp(
        `(?<![\\w.])(${pairs.map(([s]) => escRe(s)).join("|")})(?![\\w])`, "gi");

      const walker = document.createTreeWalker(threadEl, NodeFilter.SHOW_TEXT, {
        acceptNode: (n) => (n.parentElement && !n.parentElement.closest("span.ann-ref")
          && n.nodeValue.trim() ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT),
      });
      const nodes = [];
      while (walker.nextNode()) nodes.push(walker.currentNode);

      for (const node of nodes) {
        const text = node.nodeValue;
        re.lastIndex = 0;
        if (!re.test(text)) continue;
        re.lastIndex = 0;
        const frag = document.createDocumentFragment();
        let last = 0, m;
        while ((m = re.exec(text)) !== null) {
          const id = byText.get(m[1].toLowerCase());
          if (!id) continue;
          if (m.index > last) frag.append(text.slice(last, m.index));
          const span = document.createElement("span");
          span.className = "ann-ref";
          span.dataset.ann = id;
          span.textContent = m[1];
          frag.append(span);
          last = m.index + m[1].length;
        }
        if (last < text.length) frag.append(text.slice(last));
        node.replaceWith(frag);
      }
    } finally {
      indexing = false;
      if (refObserver) { refObserver.takeRecords(); observeThread(); }
    }
  }

  // The thread is written by chat.js, which exports nothing — observing it
  // keeps this decoupled instead of reaching into that module's internals.
  let reindexTimer = 0;
  function observeThread() {
    refObserver.observe(threadEl, { childList: true, subtree: true, characterData: true });
  }
  if (threadEl) {
    refObserver = new MutationObserver(() => {
      clearTimeout(reindexTimer);
      reindexTimer = setTimeout(indexChatRefs, 220);   // ride out streaming
    });
    observeThread();
  }

  /** Light up every mention of one annotation.
   *
   *  This used to also scrollIntoView the first hit. That moved the text out
   *  from under the pointer mid-hover, which re-entered and re-left the
   *  element and made the patch flicker — the highlight was fighting the
   *  scroll it had asked for. Highlighting is the whole job; where the
   *  transcript sits is the reader's business. */
  function markChatRefs(id) {
    for (const n of threadEl.querySelectorAll("span.ann-ref.hot")) n.classList.remove("hot");
    if (!id) return;
    threadEl.querySelectorAll(`span.ann-ref[data-ann="${CSS.escape(id)}"]`)
      .forEach((n) => n.classList.add("hot"));
  }

  // ...and the same link in reverse.
  if (threadEl) {
    threadEl.addEventListener("mouseover", (e) => {
      const r = e.target.closest && e.target.closest("span.ann-ref");
      if (r) scene.setHover(r.dataset.ann);
    });
    threadEl.addEventListener("mouseout", (e) => {
      const r = e.target.closest && e.target.closest("span.ann-ref");
      if (r) scene.setHover(null);
    });
  }

  /** The card for one of the USER's own drawings. Selecting a shape opens
   *  it; only its "Ask in chat" button attaches the drawing to the message,
   *  so selecting to drag or edit never silently tags anything. */
  let provDraw = null;
  function showDrawingCard(d, y) {
    provDraw = d;
    provFor = d.id;
    const T = (t) => fmtIST(t + IST, !DAILY.has(state.interval));
    // Two decimals, like every other price in the app. Raw drawing geometry
    // carries a third ("1,268.091"), which reads as a precision the anchor
    // does not have — you dragged it there.
    const num = (n) => Sym.num(n,
      { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    const g = draw.geometryOf(d.id) || { pts: [] };
    const unit = d.pane && d.pane !== "price"
      ? ((ind.CATALOG.find((c) => c.id === d.pane) || {}).label || d.pane) : "";
    const rows = g.pts.slice(0, 3).map((p, i) =>
      `<dt>${["From", "To", "Third"][i] || "Point"}</dt>` +
      `<dd>${T(p.t)} @ ${num(p.v)}</dd>`).join("");
    prov.innerHTML = `
      <header>
        <span class="role">${d.label}</span>
        <span class="draw-ref" title="How the chat refers to this drawing">${d.ref}</span>
        <button class="btn icon" data-act="close" title="Close">${Icons.svg("x", "xs")}</button>
      </header>
      <dl>
        ${unit ? `<dt>Pane</dt><dd>${unit} — values are that indicator's units</dd>` : ""}
        ${rows}
        ${g.pts.length > 3 ? `<dt>Points</dt><dd>${g.pts.length} anchors</dd>` : ""}
      </dl>
      <footer>
        <button class="btn cta" data-act="ask-draw">${Icons.svg("chat", "xs")}Ask in chat</button>
        <button class="btn danger" data-act="del-draw">${Icons.svg("eraser", "xs")}Remove</button>
      </footer>`;
    // Docked, not tracked. This used to set `top` from the pointer while the
    // stylesheet pinned `bottom` — with both edges fixed the card stretched
    // the full height of the chart instead of sitting where either wanted it.
    prov.style.top = "auto";
    dockProv();
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
    if (!act) return;   // the card has no pin state to promote to
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
    // A chat-drawn annotation's card is evidence only — it is raised by
    // hover and reads as a label, so it carries no actions of its own. Close
    // is the whole interaction; the chat removes what the chat drew.
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
    // The interval travels with the bar: a pin outlives an interval switch
    // (it is a bar, not a view), so "09:35" has to keep saying which 09:35.
    pins.toggle({ ...b, volume: v ? v.volume : 0, interval: state.interval });
  });
  document.addEventListener("charto:unpin", (e) => pins.remove(e.detail));
  // Clicking a pin chip goes back to the bar it names — a chip you can't
  // locate is a label, not context. Same zoom, recentred on the bar.
  document.addEventListener("charto:reveal-pin", (e) => {
    const i = state.bars.findIndex((x) => x.time === Number(e.detail));
    // Pinned on another interval → that bar isn't in this series. Say so
    // rather than scrolling to the nearest wrong thing.
    if (i < 0) return status("that bar was pinned on another interval — switch back to see it");
    const lr = chart.timeScale().getVisibleLogicalRange();
    const span = lr ? Math.max(20, lr.to - lr.from) : 180;
    chart.timeScale().setVisibleLogicalRange({ from: i - span / 2, to: i + span / 2 });
  });

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

  // ── chart layout ──
  // Panes wraps #stage in a grid; the primary chart keeps its element, its
  // instance and every overlay bound to it, so nothing here can disturb it.
  Panes.init(stageEl);
  const layoutBtn = el("layoutBtn"), layoutMenu = el("layoutMenu");
  /* The picker is a GRID OF GLYPHS grouped by pane count, not a list of
   * names: with forty-two layouts a text menu would be four screens of
   * prose describing shapes you can read at a glance. The count leads each
   * row in the gutter, the way TradingView and Groww both put it, so
   * "I want four charts" is one downward scan rather than a search.
   *
   * Both the rows and the glyphs come from the Panes catalogue — this file
   * decides nothing about what layouts exist or what they look like. */
  layoutMenu.innerHTML =
    `<div class="head">Layout</div>`
    + Panes.groups().map(([n, list]) =>
        `<div class="lay-group">`
        + `<span class="lay-n">${n}</span>`
        + `<div class="lay-opts">`
        + list.map((L) =>
            `<button type="button" class="lay-opt" data-layout="${L.id}" `
            + `title="${L.label}" aria-label="${L.label}">`
            + Icons.layoutSvg(L.spec, "sm") + `</button>`).join("")
        + `</div></div>`).join("");

  function paintLayoutBtn() {
    // The trigger wears the layout you are in, so the header says which one
    // without opening the menu.
    const L = Panes.LAYOUTS[Panes.layout];
    layoutBtn.innerHTML = Icons.layoutSvg(L.spec, "sm");
    layoutBtn.title = `Layout — ${L.label}`;
    for (const it of layoutMenu.querySelectorAll("[data-layout]")) {
      it.classList.toggle("on", it.dataset.layout === Panes.layout);
    }
  }
  layoutBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    closeMenus(layoutMenu);
    layoutMenu.classList.toggle("open");
  });
  layoutMenu.addEventListener("click", (e) => {
    const it = e.target.closest("[data-layout]");
    if (!it) return;
    layoutMenu.classList.remove("open");
    Panes.apply(it.dataset.layout);
    paintLayoutBtn();
    Store.set("layout", Panes.layout);
  });
  // Selecting a pane re-aims the toolbar: the segmented control shows that
  // pane's interval, so the control never claims a value it isn't driving.
  Panes.onActive((_i, iv) => markInterval(iv || state.interval));
  Panes.apply(Store.get("layout") || "s1");
  paintLayoutBtn();

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
    await Indicators.loadCatalogue(API, SYMBOL);
    ind.setContext({ interval: Store.get("interval", "5m") });
    renderIndMenu();
    // Read the restore list BEFORE the first load. renderChips() is also the
    // save point for the active set, and loading an interval repaints the
    // chips — at boot that runs against an empty active map and would write
    // the session away before it had been read back.
    const wanted = Store.get("indicators", []);
    const saved = Store.get("interval", "5m");
    const iv = IV_SEC[saved] ? saved : "5m";
    selectInterval(iv);
    await loadInterval(iv);

    for (const id of wanted) {
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

      // the indicators-changed dispatch above ran BEFORE this restore,
      // so the orphan purge saw an empty scene — signal once more now
      document.dispatchEvent(new CustomEvent("charto:indicators-changed"));
    }

    openStream();   // only once history is on the chart, so ticks extend it
  })();

  // expose for debugging + the chat pane
  // ── company search — the symbol pill opens the 500-company universe ──
  // Picking one navigates to ?symbol=X: a full reload is what guarantees a
  // genuinely fresh session (chart, chat, drawings and scene all re-init
  // against that symbol's own persisted state). First open of a company
  // hydrates it server-side from the blob universe (~8 s once).
  (() => {
    el("symbolName").textContent = SYMBOL;
    el("symbolVenue").textContent = Sym.venue;
    el("srcLine").textContent = `local store · ${Sym.feed}`;
    el("roTitle").textContent = SYMBOL;
    document.title = `${SYMBOL} — Charto`;
    const pill = el("symbolPill"), menu = el("symbolMenu");
    const input = el("symSearch"), list = el("symList");
    let all = null, hyd = new Set(), names = {}, shortNames = {}, logos = {};
    const go = (s) => {
      if (!s || s === SYMBOL) { menu.classList.remove("open"); return; }
      pill.style.opacity = "0.6";
      location.search = "?symbol=" + encodeURIComponent(s);
    };
    const render = (query) => {
      const q = query.trim().toUpperCase();
      if (all === null) {   // first open: the universe is still in flight, and
        list.innerHTML =    // "no match" would read as "your query found none"
          '<div class="item" style="color:var(--faint)">loading companies…</div>';
        return;
      }
      const pool = all || [];
      // the whole universe renders — 500 rows is nothing, and a cap made
      // the list look like it "couldn't load more" past the B's
      // a name search is how people actually look ("laurus", not LAURUSLABS)
      const hits = q
        ? pool.filter((s) => s.includes(q)
                          || (names[s] || "").toUpperCase().includes(q)
                          || (shortNames[s] || "").toUpperCase().includes(q))
            .sort((a, b) => (a.startsWith(q) ? 0 : 1) - (b.startsWith(q) ? 0 : 1)
                            || a.localeCompare(b))
        : pool;
      list.innerHTML = hits.map((s) =>
        `<div class="item" data-sym="${s}"><span class="lead">` +
        (logos[s] ? `<img class="co-logo" src="${logos[s]}" alt="" loading="lazy"
             onerror="this.remove()"/>` : "") +
        (hyd.has(s) ? '<span class="dot-h"></span>' : "") +
        `${s}${names[s] && names[s] !== s
          ? `<span class="co-name">${names[s]}</span>` : ""}</span>` +
        (hyd.has(s) ? "" : '<span class="cold">~6s</span>') +
        // the row opens the chart; this opens the company page, so a search
        // can end in either surface without a second search
        `<a class="open-co" target="_blank" rel="noopener" href="${COMPANY_PAGE}/stock/${encodeURIComponent(s)}?theme=${document.documentElement.getAttribute("data-theme") || "dark"}"
            title="${s} — company page">↗</a>` +
        "</div>").join("")
        || '<div class="item" style="color:var(--faint)">no match</div>';
    };
    pill.addEventListener("click", async (e) => {
      e.stopPropagation();
      const opening = !menu.classList.contains("open");
      window.__chartoCloseMenus && window.__chartoCloseMenus();
      menu.classList.toggle("open", opening);
      if (!opening) return;
      // Focus FIRST, and never clear after the fetch. This used to sit after
      // `await`, so the first open ate every keystroke typed while the 500
      // symbols were in flight — you clicked, typed, watched the box empty
      // itself, and only the third attempt (data cached) worked.
      input.value = ""; render(""); input.focus();
      if (!all) {
        try {
          const d = await fetch(`${API}/symbols`).then((r) => r.json());
          all = d.symbols || []; hyd = new Set(d.hydrated || []);
          // show the enrichment long name (the Moneycontrol short name is
          // wrong for a few rows); still search both
          names = { ...(d.names || {}), ...(d.long || {}) };
          shortNames = d.names || {}; logos = d.logos || {};
        } catch { all = []; }
        // re-render against whatever is in the box NOW, not against ""
        if (menu.classList.contains("open")) render(input.value);
      }
    });
    input.addEventListener("input", () => render(input.value));
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") go(list.querySelector(".item[data-sym]")?.dataset.sym);
      if (e.key === "Escape") menu.classList.remove("open");
    });
    list.addEventListener("click", (e) => {
      if (e.target.closest(".open-co")) { e.stopPropagation(); return; }
      const it = e.target.closest(".item[data-sym]");
      if (it) go(it.dataset.sym);
    });
    document.addEventListener("click", (e) => {
      // closest(), not `e.target !== pill`: the pill has children (the symbol
      // text, the exchange tag), so clicking its MIDDLE made this handler
      // close the menu the pill had just opened — the click looked ignored
      // and only a hit on the pill's bare padding worked.
      if (!menu.contains(e.target) && !e.target.closest("#symbolPill")) {
        menu.classList.remove("open");
      }
    });
  })();

  window.__charto = { chart, candle, state, draw, ind, scene, pins, getChartContext };
})();
