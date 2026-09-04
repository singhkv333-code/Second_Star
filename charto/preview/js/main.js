/* Charto preview — main wiring.
 * Data: local 1-min store via dataserver on :5174 (RELIANCE only for now).
 * All chart times are IST-shifted (+19800) so the axis reads IST regardless
 * of browser timezone; shift is removed when talking to the server.
 */
"use strict";

(function () {
  const LWC = window.LightweightCharts;
  // Same-origin when deployed behind a proxy, explicit port in local dev.
  // A hardcoded 127.0.0.1 works on a laptop and breaks the moment the page is
  // served from anywhere else — the browser dials the VIEWER's machine, so
  // every request fails with a connection error that looks like a dead server.
  const LOCAL_DEV = ["localhost", "127.0.0.1"].includes(location.hostname);
  const API = LOCAL_DEV ? "http://127.0.0.1:5174" : "";
  // Pivot's stock page, copied into charto/web and served by `next dev`
  // there (see charto/web/README). Company links open it directly.
  /* The company page is same-origin, and the empty string is the point.
   *
   * It was an absolute http://localhost:5175 — a second port in the address
   * bar, a different origin (so no shared cookie and no shared theme), and a
   * link that is dead on every machine that is not the one it was written on.
   * The page still runs as its own app; it is REACHED through this origin,
   * proxied by serve.py in dev and by nginx on the VM, so one relative href
   * works in both with no build-time switch. */
  const COMPANY_PAGE = "";

  // Per-symbol: +05:30 for Indian instruments, 0 for crypto (whose bars the
  // dataserver folds on UTC midnight). Every `+ IST` / `- IST` below is a
  // shift between raw exchange time and chart time, so this one binding
  // switches the whole axis.
  const IST = Sym.tz;
  const SYMBOL = (new URLSearchParams(location.search).get("symbol")
                  || "RELIANCE").toUpperCase();
  // {read, write} for the four sets an edit edits — bound once the boot
  // restore has put everything back. Null until then, which is exactly what
  // stops a layout being saved from a half-restored chart.
  let workspace = null;
  const IV_SEC = { "1m": 60, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "1d": 86400, "1w": 604800, "1mo": 2592000 };
  const PAGE = { "1m": 5000, "5m": 4000, "15m": 3000, "30m": 3000, "1h": 3000, "1d": 3000, "1w": 700, "1mo": 200 };

  const el = (id) => document.getElementById(id);
  /* The app's status strip is gone from the page, but the messages it used to
     carry are written from a dozen call sites — including error paths. This
     swallows a write to a node that is no longer there rather than making
     every one of those sites test for it. */
  const setText = (id, text) => { const n = el(id); if (n) n.textContent = text; };
  const chartEl = el("chart");
  const stageEl = el("stage");

  Theme.init();

  const state = {
    interval: "5m",
    bars: [],          // chart-time bars {time,open,high,low,close,volume}
    hasMore: true,
    loadingOlder: false,
    switching: false,  // interval switch in flight — stream events wait it out
    vp: null,          // active volume-profile window, in sessions
  };

  // ── chart ─────────────────────────────────────────────
  /* The axes are canvas text, so they cannot inherit --font — the stack has
     to be repeated here. Inter leads, exactly as --font does: with the system
     faces first, Windows drew the price axis in Segoe UI while the OHLC row
     an inch above it was Inter, and now that both are the same family that
     mismatch would be the only one left on the chart. */
  const CHART_FONT = "'Inter', ui-sans-serif, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif";
  function chartTheme() {
    const P = Theme.palette;
    return {
      layout: {
        background: { color: P.chartBg }, textColor: P.axisText,
        panes: { separatorColor: P.separator, enableResize: true },
        // The chart library signs its own work bottom-left. Pivot's mark
        // stands there instead — see `.chart-mark` below. The library ships
        // this switch for exactly this; the credit to TradingView belongs in
        // the About sheet with the rest of the colophon, not on the candles.
        attributionLogo: false,
      },
      grid: { vertLines: { color: P.grid }, horzLines: { color: P.grid } },
      rightPriceScale: { borderColor: P.border },
      timeScale: { borderColor: P.border },
      crosshair: {
        /* Keep the library's native scrolling plates tied to both axes. */
        vertLine: { color: P.crosshair, labelBackgroundColor: P.crosshairLabel },
        horzLine: { color: P.crosshair, labelBackgroundColor: P.crosshairLabel },
      },
    };
  }

  const T0 = chartTheme();
  /* The price axis sizes itself to its WIDEST label, so a six-figure
   * instrument spends ~90px of a 414px phone on "80000.00" — a fifth of the
   * chart, to say a number nobody reads to the last paisa at that scale.
   * Narrow screens get a compact label: 80.0k, 1.24M. Desktop keeps the full
   * figure, because there the width costs nothing and precision is free.
   * The crosshair label and the OHLC readout are untouched — the exact price
   * is still one tap away, it just stops paying rent on the axis. */
  const compactPrice = (v) => {
    const a = Math.abs(v);
    if (a >= 1e6) return (v / 1e6).toFixed(2) + "M";
    if (a >= 1e4) return (v / 1e3).toFixed(1) + "k";
    if (a >= 100) return v.toFixed(0);
    if (a >= 1) return v.toFixed(2);
    return v.toPrecision(3);
  };
  const SMALL = "(max-width: 820px), (max-height: 520px)";
  const narrow = () => window.matchMedia(SMALL).matches;
  const priceLocale = () => (narrow()
    ? { priceFormatter: compactPrice } : {});

  /* A FLOOR under the price scale's width, so the scale — and every plate
   * drawn on it — is one size and stays there.
   *
   * Left to itself the scale is exactly as wide as its widest number plus a
   * constant, so it breathes in and out by a digit's width as you scroll from
   * 980 to 1,005: the column of prices shifts, and so does the crosshair plate
   * and the ⊕ pinned to its edge. Groww's does not move, and neither should
   * this one. 84 clears every NSE price up to six figures, and the scale still
   * grows past it on its own if a number ever needs more.
   *
   * Not on a phone. There the compact formatter above exists to WIN back this
   * width, and the ⊕ that needs the room is hidden anyway (it has no hover to
   * appear on). */
  const AXIS_MIN = 84;
  const axisMin = () => (narrow() ? 0 : AXIS_MIN);

  const chart = LWC.createChart(chartEl, {
    ...T0,
    localization: { ...(T0.localization || {}), ...priceLocale() },
    layout: { ...T0.layout, fontFamily: CHART_FONT, fontSize: 12 },
    rightPriceScale: { ...T0.rightPriceScale, minimumWidth: axisMin(),
                       scaleMargins: { top: 0.06, bottom: 0.22 } },
    timeScale: { ...T0.timeScale, timeVisible: true, secondsVisible: false, rightOffset: 5 },
    crosshair: { ...T0.crosshair, mode: LWC.CrosshairMode.Normal },
    autoSize: true,
  });

  /* Pivot's mark, bottom-left, standing where the chart library used to sign
   * its own — same corner, so the chart keeps a signature and only the name on
   * it changes.
   *
   * A DECORATION, appended once and never rebuilt: the library owns this
   * container and appends to it, so a sibling node survives every redraw.
   * `pointer-events: none` keeps it out of the way of the crosshair, which
   * reaches this corner constantly. */
  const brandMark = document.createElement("div");
  brandMark.className = "chart-mark";
  brandMark.setAttribute("aria-hidden", "true");
  chartEl.appendChild(brandMark);

  /* Two measurements the stylesheet cannot take, published to it as custom
   * properties on the chart element.
   *
   * `--time-axis-h` is what the mark stands on. The library's own logo lived
   * inside the last PANE — above the time axis — while this container runs to
   * the bottom of the axis, so a flat `bottom: 10px` dropped the mark a whole
   * axis lower than the one it replaced.
   *
   * `--axis-w` is where the alert ⊕ sits: centred on the price scale's left
   * edge, half over the chart and half over the label, which is TradingView's
   * placement. Both numbers move on their own — the scale re-sizes itself
   * around a wider price the moment the symbol changes — so neither can be a
   * constant in the stylesheet, which is what `right: 62px` used to be. */
  const metrics = { ts: 0, ps: 0 };
  function syncChartMetrics() {
    let ts = 0, ps = 0;
    try { ts = chart.timeScale().height(); } catch { /* not laid out yet */ }
    try { ps = chart.priceScale("right").width(); } catch { /* ditto */ }
    // On the STAGE, not on the chart: custom properties inherit, so #chart and
    // everything the library builds inside it still read them, and the two
    // crosshair plates — which are siblings of #chart, not children — can read
    // them too. They have to: both are sized by an axis.
    if (ts && ts !== metrics.ts) {
      metrics.ts = ts;
      stageEl.style.setProperty("--time-axis-h", ts + "px");
    }
    if (ps && ps !== metrics.ps) {
      metrics.ps = ps;
      stageEl.style.setProperty("--axis-w", ps + "px");
    }
  }
  /* THE CHART CAN COME UP TWO PIXELS TALL, and then stay that way.
   *
   * `autoSize: true` hands sizing to the library's own ResizeObserver, which
   * measures the container when the chart is created. On the phone layout
   * that measurement can land while `.charts` is still zero-height — it is
   * `flex: 1 1 0; min-height: 0` inside a `position: fixed` body, so the
   * first pass legitimately has no height to give it. The library builds
   * 2px panes from that, and because the container's size never changes
   * again afterwards, no further observation ever arrives to correct it.
   *
   * The symptom is a chart element of the right size containing canvases of
   * the wrong one: 375x286 holding 374x2. Nothing renders, priceToCoordinate
   * answers in fractions of a pixel, and no drawing can be placed because
   * there is nowhere to place it.
   *
   * So the observer also checks the result and re-applies the size when the
   * panes have plainly not taken it. Cheap — a rect read and a comparison —
   * and a no-op on every healthy layout, which is every desktop one. */
  function ensureChartSized() {
    const w = chartEl.clientWidth, h = chartEl.clientHeight;
    if (w < 40 || h < 80) return;          // genuinely small: nothing to correct
    const cv = chartEl.querySelector("canvas");
    if (!cv) return;
    if (cv.getBoundingClientRect().height >= h * 0.4) return;   // laid out fine
    // resize() alone does nothing here: with autoSize on, the library owns
    // the dimensions and discards a manual one. Turning it off, stating the
    // size, and turning it back on is what makes it re-measure — and leaves
    // it in the same mode it was in, so nothing downstream has to know this
    // happened.
    try {
      chart.applyOptions({ autoSize: false, width: w, height: h });
      chart.applyOptions({ autoSize: true });
    } catch { /* pre-init */ }
  }
  syncChartMetrics();
  new ResizeObserver(() => { syncChartMetrics(); ensureChartSized(); }).observe(chartEl);
  // …and once after the first frames, for the case the observer's own first
  // callback WAS the zero-height one and nothing resizes the container again.
  requestAnimationFrame(() => requestAnimationFrame(ensureChartSized));
  setTimeout(ensureChartSized, 400);
  setTimeout(ensureChartSized, 1500);

  const publishPlate = () =>
    stageEl.style.setProperty("--plate", Theme.c("crosshairLabel"));
  publishPlate();

  const candle = chart.addSeries(LWC.CandlestickSeries, {
    upColor: Theme.c("up"), downColor: Theme.c("down"), borderVisible: false,
    wickUpColor: Theme.c("up"), wickDownColor: Theme.c("down"),
  });
  const volume = chart.addSeries(LWC.HistogramSeries, {
    priceFormat: { type: "volume" }, priceScaleId: "vol",
    priceLineVisible: false, lastValueVisible: false,
  });
  chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.85, bottom: 0 } });

  /* The header's gear edits THIS chart too. Registering hands the settings
   * module the two series it paints and a cheap way to re-colour the bars —
   * `repaint` re-sets the data it already has, so a colour change is never a
   * refetch and never touches the indicators. The theme still supplies every
   * default; see js/chartsettings.js. */
  ChartSettings.register({
    chart, candle, volume,
    // what "Default" means for this chart's two sized knobs — the values it
    // was built with, twelve lines up
    defaults: { fontSize: 12, rightOffset: 5 },
    label: () => SYMBOL,
    repaint() {
      if (!state.bars.length) return;
      candle.setData(ChartSettings.candlePoints(state.bars));
      volume.setData(ChartSettings.volumePoints(state.bars));
    },
  });

  // ── data client ───────────────────────────────────────
  async function fetchBars(interval, toRaw, limit) {
    const qs = new URLSearchParams({ symbol: SYMBOL, interval, limit: String(limit) });
    if (toRaw) qs.set("to", String(toRaw));
    const res = await Net.get(`${API}/bars?${qs}`);
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
    // Both series are built by js/chartsettings.js, never here: the bar
    // colours are a setting (and, with "colour bars based on previous
    // close", a per-POINT one), and a second place deciding what green means
    // is a second place to get it wrong.
    candle.setData(ChartSettings.candlePoints(state.bars));
    volume.setData(ChartSettings.volumePoints(state.bars));
    // A new interval can move an indicator in or out of the timeframes its
    // Visibility tab allows, and the legend row is where that is legible —
    // without this the plot vanishes on 1h while its row still reads as live.
    // (recomputeAll repaints the legend itself; this is only the menu's tick.)
    ind.recomputeAll(state.bars, { interval: state.interval, limit: state.bars.length })
      .then(() => renderIndMenu());
  }

  async function loadInterval(interval) {
    state.interval = interval;
    setOverlay(true, "Loading…");
    const t0 = performance.now();
    state.switching = true;   // latch: stream events must not touch the old series mid-switch
    try {
      const { bars, hasMore } = await fetchBars(interval, null, PAGE[interval]);
      state.bars = bars; state.hasMore = hasMore;
      // Anything that needs to know a chart is READABLE — not merely present.
      // A restored thread repaints before this resolves, so a panel deciding
      // at render time whether it can draw its trades was answering "no bars
      // loaded" about a chart that was seconds from having them.
      document.dispatchEvent(new CustomEvent("charto:bars-loaded",
        { detail: { interval, symbol: Sym.name, bars: bars.length } }));
      chart.applyOptions({ timeScale: { timeVisible: !["1d", "1w", "1mo"].includes(interval) } });
      paintTitle();
      paint();
      // fresh data, fresh view — the same one the reset button lands on
      paintResetBtn();
      resetView(chart, bars.length);
      lastBar = bars[bars.length - 1];
      paintReadout(lastBar);
      setText("barsLine", `${bars.length.toLocaleString()} × ${interval}`);
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
      setText("barsLine", `${state.bars.length.toLocaleString()} × ${state.interval}`);
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
      // the bar BEFORE the forming one — the previous-close colouring rule
      // needs it, and on a replaced last bar that is two back
      const prev = state.bars[state.bars.length - 2] || null;
      candle.update(ChartSettings.candlePoint(bar, prev));
      volume.update(ChartSettings.volumePoint(bar, prev));
      lastBar = bar;
      paintReadout(lastBar);
      // Anything else on the page showing this instrument's PRICE. The event
      // carries the last trade and nothing else — no change, no percent, no
      // opinion — so a listener that wants a move computes it against its own
      // baseline. Today's listener is the watchlist row for this symbol,
      // which would otherwise sit up to a poll behind the candle beside it.
      document.dispatchEvent(new CustomEvent("charto:tick", {
        detail: { symbol: SYMBOL, last: b.c },
      }));
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

  /* The phone's annotation disclosure. The labels are hidden by CSS at this
   * width; this is the count that brings them back. Built once and updated,
   * rather than rebuilt per scene change, so tapping it mid-answer does not
   * lose the open state. */
  let chipsBtn = null;
  function syncChipsBtn(n) {
    const stage = el("stage");
    if (!stage) return;
    if (!chipsBtn) {
      chipsBtn = document.createElement("button");
      chipsBtn.className = "chips-btn";
      chipsBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        stage.classList.toggle("chips-on");
      });
      stage.appendChild(chipsBtn);
    }
    chipsBtn.innerHTML = `<span>${n}</span>` + Icons.svg("chevronDown", "xs");
    chipsBtn.style.display = n ? "" : "none";
    if (!n) stage.classList.remove("chips-on");
  }

  /** Same legend shape the secondary panes use, so a split shows one chart
   *  twice rather than two differently-labelled ones. */
  function paintTitle() {
    // The venue comes from Sym, not a literal: this legend sits directly under
    // a badge that already reads BYBIT on a Bitcoin chart, and the two saying
    // different exchanges about the same instrument is worse than either.
    // The ticker carries the instrument's mark and IS the instrument switch —
    // the legend is where a chart says what it is, so it is also where a
    // reader reaches to change it.
    el("roTitle").innerHTML =
      `<span class="sym-btn" data-sym-btn title="Change instrument">`
      + `${Universe.logoHTML(SYMBOL, "co-logo lg")}${SYMBOL}</span>`
      + `<span class="sep">·</span>${state.interval === "1d" ? "1D" : state.interval}`
      + `<span class="sep">·</span><span class="ex">${Sym.venue}</span>`;
  }
  // Delegated once: paintTitle rewrites its own children on every interval
  // change, and a listener per repaint leaks one per switch.
  el("roTitle").addEventListener("click", (e) => {
    const btn = e.target.closest("[data-sym-btn]");
    if (!btn) return;
    e.stopPropagation();
    Panes.setActive(0);
    Universe.open({
      anchor: btn, current: SYMBOL,
      // A symbol change is a new session — chart, chat, drawings and scene all
      // re-init against it — so it goes through the same reload the header
      // pill uses rather than swapping bars under a live conversation.
      onPick: (s) => { if (s !== SYMBOL) location.search = "?symbol=" + encodeURIComponent(s); },
    });
  });
  // the mark lands after the universe does; repaint once it is known
  Universe.load().then(() => { if (state.bars.length) paintTitle(); });

  /** Index of the bar at this chart time. Binary search because the readout
   *  runs on every crosshair move and a linear scan of 4,000 bars per
   *  mousemove is a scroll stutter nobody can name the cause of. */
  function barIndexAt(t) {
    const a = state.bars;
    let lo = 0, hi = a.length - 1;
    while (lo <= hi) {
      const m = (lo + hi) >> 1;
      if (a[m].time === t) return m;
      if (a[m].time < t) lo = m + 1; else hi = m - 1;
    }
    return -1;
  }

  function paintReadout(b) {
    if (!b) { el("roOhlc").innerHTML = ""; return; }
    const cls = b.close >= b.open ? "up" : "down";
    const f = (n) => Sym.num(n);
    // What the candle DID, which is the thing a reader is actually after and
    // the one number five OHLC figures never quite say. Measured against the
    // PREVIOUS bar's close — the same base every terminal means by a bar's
    // change — so it accounts for the gap, unlike close-minus-open.
    let chg = "";
    const i = barIndexAt(b.time);
    const prev = i > 0 ? state.bars[i - 1] : null;
    if (prev && prev.close) {
      const d = b.close - prev.close;
      const sign = d >= 0 ? "+" : "-";
      chg = `<span class="chg ${d >= 0 ? "up" : "down"}">`
        + `${sign}${f(Math.abs(d))} (${sign}${Math.abs(d / prev.close * 100)
          .toFixed(2)}%)</span>`;
    }
    // Each figure is named by its own class and its LETTER is its own element:
    // a phone drops four of the five figures and every letter, and what is
    // left — the close and the change — is the pair a small screen can read.
    // A nth-child rule could not have said that; a nested tag can.
    el("roOhlc").innerHTML =
      `<span class="ro-o"><i>O</i> <b class="${cls}">${f(b.open)}</b></span>` +
      `<span class="ro-h"><i>H</i> <b class="${cls}">${f(b.high)}</b></span>` +
      `<span class="ro-l"><i>L</i> <b class="${cls}">${f(b.low)}</b></span>` +
      `<span class="ro-c"><i>C</i> <b class="${cls}">${f(b.close)}</b></span>` +
      `<span class="ro-v"><i>V</i> <b class="${cls}">${f(b.volume)}</b></span>` + chg;
  }
  /* "Over a candle" is not "over the chart". The pick cursor is a promise that
   * there is something under the pointer to pick, so it has to be answered
   * against the bar's own high-low span rather than the pane's bounds — the
   * empty air above a downtrend is still the plot, and a hand floating there
   * says the opposite of the truth.
   *
   * THREE gestures ask this question — the cursor, the click that pins a bar,
   * and the right-click that decides which menu you get — so it is one
   * function taking a pane-local y. `slack` is the only thing they disagree
   * about, and honestly so: a HOVER promise is tight (2px, because a hand
   * over empty chart is a lie), while a GRAB is forgiving (8px, so a thin
   * wick is still catchable by a real hand on a real mouse). */
  function yOnBar(y, b, slack) {
    if (!b || y == null || b.high == null) return false;
    const top = candle.priceToCoordinate(b.high);
    const bot = candle.priceToCoordinate(b.low);
    if (top == null || bot == null) return false;
    return y >= top - slack && y <= bot + slack;
  }
  chart.subscribeCrosshairMove((p) => {
    const b = p && p.seriesData ? p.seriesData.get(candle) : null;
    chartEl.classList.toggle("on-bar", yOnBar(p && p.point ? p.point.y : null, b, 2));
    if (b) {
      const src = state.bars[state.bars.length - 1];
      paintReadout({ ...b, volume: (p.seriesData.get(volume) || {}).value ?? src.volume });
    } else paintReadout(lastBar);
  });

  function status(msg) { setText("statusLine", msg); }
  function setOverlay(show, text, isErr) {
    el("overlayText").innerHTML = isErr ? `<span class="err">${text}</span>` : (text || "");
    el("overlay").classList.toggle("is-err", !!isErr);
    el("overlay").classList.toggle("show", !!show);
  }

  // ── indicators UI ─────────────────────────────────────
  const ind = Indicators.createManager(chart);
  const menu = el("indMenu");

  /* ONE toolbar, aimed at whichever pane is selected — the same rule the
   * interval strip already follows. `ind` stays the PRIMARY chart's manager
   * (the scene layer, the chat envelope and the drawing panes are all bound
   * to it); everything the header operates goes through IND(), which is the
   * selected pane's own manager when a secondary chart holds the selection.
   * A gear opened on a secondary pane therefore edits that pane's copy. */
  const IND = () => Panes.activeInd() || ind;

  el("indBtn").innerHTML =
    Icons.svg("indicators", "sm") + "Indicators" + Icons.svg("chevronDown", "chev");

  /* Volume profile is a STUDY, not a line series: it has no per-bar value to
   * plot, so it never enters the indicator CATALOG and gets its own section.
   * The only knob offered is the WINDOW. Row height is deliberately not a
   * user setting — it is derived from the measured smear of the 1-minute
   * bars, and letting someone dial it finer is exactly the fake precision
   * the tool exists to refuse. */
  const VP_WINDOWS = [
    { n: 1, label: "Session" }, { n: 5, label: "5 sessions" },
    { n: 20, label: "20 sessions" }, { n: 60, label: "60 sessions" },
  ];

  function renderIndMenu() {
    const m = IND();
    menu.innerHTML = '<div class="head">Overlays</div>' +
      m.CATALOG.filter((c) => c.kind === "overlay").map(itemHTML).join("") +
      '<div class="sep"></div><div class="head">Panes</div>' +
      m.CATALOG.filter((c) => c.kind === "pane").map(itemHTML).join("") +
      '<div class="sep"></div><div class="head">Volume profile</div>' +
      VP_WINDOWS.map((v) => {
        const on = state.vp === v.n;
        return `<div class="item ${on ? "on" : ""}" data-vp="${v.n}">` +
          `<span>${v.label}</span>${on ? Icons.svg("check", "xs") : ""}</div>`;
      }).join("");
    function itemHTML(c) {
      const on = m.isActive(c.id);
      return `<div class="item ${on ? "on" : ""}" data-ind="${c.id}">` +
        `<span>${c.label}</span>${on ? Icons.svg("check", "xs") : ""}</div>`;
    }
  }
  /* The indicator legend is ON the chart now (js/indlegend.js) — one row per
   * study under the OHLC, carrying its own reading and its own eye, gear, ×
   * and ⋯, plus TradingView's collapse toggle. The header's chip strip is
   * gone with it: it said the same thing further from the line it was about,
   * and it could never quote a value at the bar under the pointer.
   *
   * Note this is bound to the PRIMARY chart's manager, not to IND(). Each
   * pane wears its own legend, so a legend never describes a chart other
   * than the one it is drawn on — which is the whole point of moving it. */
  const legend = IndLegend.create({
    chart, chartEl, mgr: ind, stage: stageEl, host: el("indLegend"),
    storeKey: "ind_legend_collapsed",
    openSettings: (id) => openIndSettings(id, ind),
    status,
    onChange: () => {
      renderIndMenu();
      document.dispatchEvent(new CustomEvent("charto:indicators-changed"));
    },
  });

  /** The active set moved. The legend repaints itself off the manager's own
   *  sink, so all this owns is the SAVE — and only the primary's, since a
   *  secondary pane is created by the layout and dies with it: restoring
   *  indicators onto one would be restoring them onto a different chart than
   *  the one they were added to. */
  function saveIndicators() {
    Store.set("indicators", [...ind.active.keys()]);
    // The one choke point every path to the active set passes through — the
    // menu, the legend's ×, the chat, the restore — so it is where the undo
    // stack listens. NOT the charto:indicators-changed event: the menu's own
    // handler saves without dispatching it, and an indicator you added by
    // clicking it would have been the one thing Ctrl+Z could not take back.
    Undo.touch();
  }

  /** Open the settings dialog on one indicator. Every edit inside it applies
   *  live and persists itself; all this has to do is keep the menu honest.
   *  The manager is passed in by the legend that was clicked — on a split,
   *  the gear on a secondary pane's row must edit THAT pane's copy, not
   *  whichever pane happens to hold the selection. */
  function openIndSettings(id, mgr) {
    const m = mgr || IND();
    IndSettings.open(m, id, {
      // the subtitle names the chart being edited, which on a split is not
      // necessarily the page's own symbol or interval
      subtitle: `${m.symbol} · ${m.interval}`,
      onChange: () => {
        renderIndMenu();
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
  /** Add, re-window or remove the profile. One at a time: a second window
   *  over the same prices is two histograms in one strip, unreadable and
   *  meaningless — so picking a new window REPLACES, and picking the active
   *  one clears. */
  async function setVolumeProfile(n) {
    if (!n) {
      state.vp = null;
      Store.set("vp", null);
      scene.apply([{ kind: "clear", scope: "vprofile", owner: "volume_profile" }]);
      renderIndMenu();
      status("volume profile removed");
      return;
    }
    status(`volume profile · ${n} session${n > 1 ? "s" : ""}…`);
    const r = await fetch(`${API}/volume_profile?symbol=${encodeURIComponent(SYMBOL)}`
      + `&lookback_sessions=${n}`);
    const d = await r.json();
    if (!r.ok) {
      // an instrument with no volume is a REFUSAL, not a failure: leave the
      // menu unticked and say why, the same way a volume indicator does
      state.vp = null; renderIndMenu();
      status((d.error || "volume profile unavailable")
        + (d.hint ? ` — ${d.hint}` : ""));
      return;
    }
    state.vp = n;
    Store.set("vp", n);
    // provenance, so the badge can tell who put this here
    scene.apply(d.scene.map((a) => (a.kind === "vprofile"
      ? { ...a, manual: true } : a)));
    renderIndMenu();
    const q = d.resolution;
    status(`volume profile · ${d.window.sessions} session(s), `
      + `${d.window.minute_bars.toLocaleString()} 1-min bars · `
      + `${q.rows} rows of ${q.row_height} · POC ${d.point_of_control}`
      + (q.capped ? ` · capped from ${q.requested_rows}` : ""));
  }

  menu.addEventListener("click", (e) => {
    e.stopPropagation(); // keep the dropdown open for multi-select
    const vp = e.target.closest("[data-vp]");
    if (vp) {
      const n = Number(vp.dataset.vp);
      setVolumeProfile(state.vp === n ? null : n)
        .catch((err) => status(`volume profile failed: ${err.message}`));
      return;
    }
    const it = e.target.closest("[data-ind]");
    if (!it) return;
    const id = it.dataset.ind;
    const m = IND();
    const def = m.CATALOG.find((q) => q.id === id);
    const iv = Panes.primaryActive ? state.interval : m.interval;
    if (def.intradayOnly && ["1d", "1w", "1mo", "D", "W", "M"].includes(iv) && !m.isActive(id)) {
      status("VWAP is session-anchored — switch to an intraday interval");
      return;
    }
    Promise.resolve(m.toggle(id, state.bars))
      .then(() => { renderIndMenu(); saveIndicators(); })
      // the failure path MUST re-render too. The optimistic pass below ticks
      // the menu while the fetch is still in flight; without a re-render here
      // a refused indicator (a volume study on an index, which has no volume
      // to compute from) stayed checked, reading as active over a pane that
      // never drew.
      .catch((err) => {
        renderIndMenu(); saveIndicators();
        status(`could not add ${def.label}: ${err.message}`);
      });
    renderIndMenu();                  // optimistic: the tick lands immediately
  });
  /* Two header triggers wear their own open state — the interval pill takes a
   * fill and the avatar takes a ring, and both carry aria-expanded. Every
   * other menu button in here is an icon whose menu IS the feedback. Read off
   * the menus rather than tracked, so the document-wide close below cannot
   * leave a trigger looking open over a menu that isn't. */
  const MENU_TRIGGERS = [["intervalBtn", "intervalMenu"], ["acctBtn", "acctMenu"]];
  function syncMenuTriggers() {
    for (const [b, m] of MENU_TRIGGERS) {
      const btn = el(b), menu = el(m);
      if (!btn || !menu) continue;
      const open = menu.classList.contains("open");
      btn.classList.toggle("open", open);
      btn.setAttribute("aria-expanded", open ? "true" : "false");
    }
  }
  /** Close every open dropdown except `keep`. Shared by header + composer. */
  function closeMenus(keep) {
    document.querySelectorAll(".dropdown.open").forEach((d) => {
      if (d !== keep) d.classList.remove("open");
    });
    syncMenuTriggers();
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

  // keep the menu and the saved set honest when the chat adds an indicator
  document.addEventListener("charto:indicators-changed", () => {
    saveIndicators();   // which is also where the undo stack hears about it
    if (menu.classList.contains("open")) renderIndMenu();
    // a new oscillator pane re-lays the chart's rows; the pane legends are
    // pinned to those rows, so they have to be re-measured once it has
    legend.reposition();
  });

  // ── interval ──────────────────────────────────────────
  /* Groww's control, not a segmented strip: ONE pill saying which interval
   * this chart is on, opening a list grouped the way a trader thinks about
   * time. Eight always-visible buttons cost a fifth of the header to save a
   * click, and — because this toolbar is aimed at whichever PANE is selected
   * — a strip of eight has to *state* a value anyway, which a tinted button
   * among seven others does badly and a filled word does at a glance.
   *
   * The set is exactly the eight IV_SEC/PAGE know how to page. A ninth row
   * here would offer a timeframe the loader cannot fetch, which is the same
   * class of lie as an invented number. */
  const IV_MENU = [
    ["Minutes", [["1m", "1m", "1 minute"], ["5m", "5m", "5 minutes"],
                 ["15m", "15m", "15 minutes"], ["30m", "30m", "30 minutes"]]],
    ["Hours", [["1h", "1h", "1 hour"]]],
    ["Days", [["1d", "1D", "1 day"], ["1w", "W", "1 week"], ["1mo", "M", "1 month"]]],
  ];
  const ivBtn = el("intervalBtn"), ivMenu = el("intervalMenu");
  ivMenu.innerHTML = IV_MENU.map(([sec, rows]) =>
    `<div class="iv-sec">${sec}</div>`
    + rows.map(([iv, short, name]) =>
        `<button type="button" class="item iv-item" role="menuitemradio" `
        + `data-iv="${iv}" data-short="${short}" data-name="${name}">`
        + `<span>${name}</span></button>`).join("")).join("");

  ivBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    closeMenus(ivMenu);
    ivMenu.classList.toggle("open");
    syncMenuTriggers();
  });
  ivMenu.addEventListener("click", (e) => {
    const b = e.target.closest("[data-iv]");
    if (!b) return;
    ivMenu.classList.remove("open");
    syncMenuTriggers();
    // One toolbar, aimed at whichever pane is selected. When a secondary pane
    // has the selection this must NOT touch the primary chart — the interval
    // is a property of the pane you clicked, not of the app.
    if (Panes.setIntervalOnActive(b.dataset.iv)) return markInterval(b.dataset.iv);
    selectInterval(b.dataset.iv);
    loadInterval(b.dataset.iv);
    // the chat's subject chip carries the interval too, and this is the one
    // path that changes the primary's without a pane selection
    document.dispatchEvent(new CustomEvent("charto:pane-active", {
      detail: { pane: 0, symbol: SYMBOL, interval: b.dataset.iv },
    }));
  });

  /** Paint the pill and its list without claiming either as the primary's
   *  state — a selected secondary pane drives this too. */
  function markInterval(iv) {
    const row = ivMenu.querySelector(`[data-iv="${iv}"]`);
    ivBtn.textContent = row ? row.dataset.short : iv;
    // dataset.name, not textContent: the row carries a tick now, and reading
    // the node's text would put the glyph's (empty) text in the pill's title
    ivBtn.title = row ? `Interval — ${row.dataset.name}` : "Interval";
    for (const b of ivMenu.querySelectorAll("[data-iv]")) {
      const on = b.dataset.iv === iv;
      // `on`, the class every menu in this app marks its current row with —
      // this list used to have a private `active` for the same idea, which
      // is how it ended up with a private LOOK for it too
      b.classList.toggle("on", on);
      b.setAttribute("aria-checked", on ? "true" : "false");
      // the tick is the mark; it is written here rather than at build time
      // because which row wears it is exactly what this function decides
      const tick = b.querySelector("svg");
      if (on && !tick) b.insertAdjacentHTML("beforeend", Icons.svg("check", "xs"));
      else if (!on && tick) tick.remove();
    }
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
    infoLine: "infoLine", trendAngle: "trendAngle",
    hline: "hline", hray: "hray", vline: "vline", crossline: "crossline",
    channel: "channel", regression: "channel", flatChannel: "flatChannel",
    disjointChannel: "disjointChannel",
    pitchfork: "pitchfork", schiff: "schiff", schiffModified: "schiffMod",
    insidePitchfork: "insideFork",
    fib: "fib", fibExtension: "fibExtension", fibChannel: "fibChannel",
    fibTimeZone: "fibTimeZone", fibSpeedFan: "fibSpeedFan",
    fibTimeExtension: "fibTimeExtension", fibCircles: "fibCircles",
    fibSpiral: "fibSpiral", fibArcs: "fibArcs", fibWedge: "fibWedge",
    pitchfan: "pitchfan",
    gannBox: "gannBox", gannSquare: "gannSquare",
    gannSquareFixed: "gannSquareFixed", gannFan: "gannFan",
    rect: "rect", triangle: "triangle", brush: "brush",
    priceRange: "hline", dateRange: "vline", measure: "measure",
    long: "position", short: "position", text: "text" };
  const lastOfGroup = {};

  rail.insertAdjacentHTML("beforeend",
    `<button class="tool active" id="tool-cursor" data-tool="cursor" data-kind="tool">` +
    `${Icons.svg("crosshair")}<span class="tip">Cursor / select</span></button>` +
    '<div class="rail-sep"></div>');

  /** One row of a tool flyout. A tool with a `key` advertises its shortcut
   *  in the row's trailing slot — the same slot the tick lands in, which is
   *  fine because the two never both matter: the shortcut is how you get to
   *  a tool, the tick is confirmation you are already on it. */
  const toolRow = (id, s, g) =>
    `<div class="item" data-tool="${id}"><span class="lead">` +
    `${Icons.svg(ICON_FOR[id] || g.icon, "sm")}${s.label}</span>` +
    (s.key ? `<span class="sc">Alt + ${s.key}</span>` : "") + `</div>`;

  for (const g of Tools.GROUPS) {
    const tools = Object.entries(Tools.SPECS).filter(([, s]) => s.group === g.id);
    if (!tools.length) continue;
    lastOfGroup[g.id] = tools[0][0];
    // A sectioned group heads each band and rules between them; an unsectioned
    // one is the single-heading flyout this rail has always drawn. Sections
    // are read off the group, not off the tools, so the ORDER is declared in
    // one place rather than emerging from catalogue order.
    const items = g.sections
      ? g.sections.map(([sid, slabel], i) => {
          const rows = tools.filter(([, s]) => (s.section || g.id) === sid);
          if (!rows.length) return "";
          return (i ? `<div class="sep"></div>` : "")
            + `<div class="head">${slabel}</div>`
            + rows.map(([id, s]) => toolRow(id, s, g)).join("");
        }).join("")
      : `<div class="head">${g.label}</div>`
        + tools.map(([id, s]) => toolRow(id, s, g)).join("");
    rail.insertAdjacentHTML("beforeend",
      `<div class="tool-wrap" data-group="${g.id}">` +
        `<button class="tool has-group" id="group-${g.id}" data-group-btn="${g.id}" ` +
        `aria-label="${g.label}">${Icons.svg(g.icon)}</button>` +
        `<div class="dropdown side" id="menu-${g.id}">${items}</div>` +
      `</div>`);
  }
  rail.insertAdjacentHTML("beforeend",
    '<div class="rail-sep"></div>' +
    `<button class="tool" id="tool-magnet" data-tool="magnet" data-kind="toggle">` +
    `${Icons.svg("magnet")}<span class="tip">Magnet — snap to OHLC</span></button>` +
    '<div class="rail-spacer"></div>' +
    `<button class="tool" id="tool-export" data-tool="export" data-kind="action">` +
    `${Icons.svg("download")}<span class="tip">Export drawings JSON</span></button>` +
    // "Remove objects", not "clear all drawings": the button opens a menu that
    // names each layer and its count, and a tooltip promising to clear one
    // layer over a control that offers three was the narrower of two lies.
    // Born disabled, and switched on by the first sync once the restore has
    // put the session back: a chart with nothing on it yet must not offer to
    // remove things from it, and "yet" includes the first frame.
    `<button class="tool" id="tool-trash" data-tool="trash" data-kind="action" disabled>` +
    `${Icons.svg("trash")}<span class="tip">Remove objects…</span></button>` +
    // DELETE THE SELECTED ONE, which the trash above has never done — that
    // opens a menu of whole layers. Select-then-Delete was keyboard-only, so
    // on a phone a shape could be drawn and selected and never removed.
    // Hidden until something IS selected: a button that deletes "the
    // selection" is a lie on a chart that has none, and the rail is short
    // enough that a permanent disabled slot costs more than it explains.
    //
    // An ERASER, not a second trash. This was added for touch and it landed
    // directly under the trash that had always been there, wearing the same
    // glyph — so on the one screen that never needed it, a selected shape
    // grew a duplicate bin and the pair became a guess. Different job,
    // different mark: the bin removes whole layers, the eraser removes the
    // one thing you are pointing at. The stylesheet takes the eraser back off
    // any mouse-and-keyboard screen, where Del already does this.
    `<button class="tool" id="tool-del" data-tool="del" data-kind="action" hidden>` +
    `${Icons.svg("eraser")}<span class="tip">Delete selected (Del)</span></button>`);

  /* The delete button follows BOTH selections — the user's own shapes and the
   * chat's annotations — because from the chart's side they are one idea:
   * something is selected, and this removes it. Which module owns it is an
   * implementation detail the rail should not make the user think about.
   *
   * Two events, one piece of state. `charto:draw-select` has always fired;
   * `charto:scene-select` is new for exactly this. Each carries null on
   * deselect, so the button leaves the same way it arrived. */
  const selNow = { draw: null, scene: null };
  function paintDelBtn() {
    const b = el("tool-del");
    if (!b) return;
    const on = !!(selNow.draw || selNow.scene);
    b.hidden = !on;
    b.querySelector(".tip").textContent = selNow.draw
      ? `Delete ${selNow.draw.ref || "drawing"} (Del)`
      : selNow.scene ? "Delete selected annotation (Del)" : "Delete selected (Del)";
  }
  document.addEventListener("charto:draw-select", (e) => {
    selNow.draw = e.detail || null; paintDelBtn();
  });
  document.addEventListener("charto:scene-select", (e) => {
    selNow.scene = e.detail || null; paintDelBtn();
  });
  el("tool-del").addEventListener("click", (e) => {
    e.stopPropagation();
    // The drawing wins a tie: it is the layer the user was last drawing on,
    // and only one of the two can be selected by any single click anyway.
    if (selNow.draw) draw.remove(selNow.draw.id);
    else if (selNow.scene) scene.remove(selNow.scene.id);
    selNow.draw = selNow.scene = null;
    paintDelBtn();
  });

  /** Open or close one group's flyout. The wrap gets the state too, because
   *  the button's tooltip has to know: both land in the same slot beside the
   *  rail, and the tooltip is on the higher layer, so an open menu would
   *  otherwise be wearing its own title stamped across it. */
  function setToolMenu(gid, open) {
    const menu = el(`menu-${gid}`);
    if (!menu) return;
    // The marked row is the tool this group's rail button currently arms —
    // marked on OPEN rather than at build time, because clicking an item
    // changes it, and a mark written once would go stale the first time.
    if (open) {
      for (const it of menu.querySelectorAll(".item[data-tool]")) {
        const on = it.dataset.tool === lastOfGroup[gid];
        it.classList.toggle("on", on);
        // and the tick with it — the same glyph in the same slot the
        // indicator list puts it in. `:scope > svg` so the row's own LEAD
        // icon, which lives inside .lead, is never the one removed.
        const tick = it.querySelector(":scope > svg");
        if (on && !tick) it.insertAdjacentHTML("beforeend", Icons.svg("check", "xs"));
        else if (!on && tick) tick.remove();
      }
    }
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
    // Do not leave the previous group's flyout on screen during this group's
    // hover-intent delay. Its menu and the new button's tooltip occupy the
    // same space, producing two competing labels (for example Shapes behind
    // "Measure"). The group being entered is preserved if it is already open.
    closeToolMenus(wrap.dataset.group);
    hoverTimer = setTimeout(() => {
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
      clearTimeout(hoverTimer);
      closeToolMenus();
      armTool(item.dataset.tool);
    }
  });
  document.addEventListener("click", (e) => {
    if (!e.target.closest(".tool-wrap")) closeToolMenus();
  });

  /** Arm a tool AND make its group's rail button remember it — the one path
   *  every way of picking a tool goes through, so a keyboard shortcut and a
   *  click on the same row cannot leave the rail in different states. */
  function armTool(id) {
    const spec = Tools.SPECS[id];
    if (!spec) return;
    lastOfGroup[spec.group] = id;
    // the rail button now shows what it will arm next time
    const btn = el(`group-${spec.group}`);
    const icon = ICON_FOR[id];
    if (btn && icon) {
      btn.innerHTML = Icons.svg(icon);
      btn.setAttribute("aria-label", spec.label);
    }
    selectTool(id);
  }

  /* The shortcuts the flyout advertises are dispatched by js/shortcuts.js —
   * one catalogue for the keyboard and for the sheet that lists it, so a
   * binding cannot move without the sheet moving with it. This file only
   * says what "arm a tool" MEANS; see the registrations at the foot. */

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
    setStatus: (m) => setText("drawStatus", m),
    onToolDone: () => selectTool("cursor"),
    onChange: () => syncDrawToggle(),
    // The touch bridge lives in drawings.js but has to decide for BOTH
    // layers: a finger landing on a chat-drawn level or pattern must arm it
    // just as one landing on the user's own shape does. Lazy, because
    // `scene` is created a few hundred lines below this and the bridge only
    // ever asks during a gesture.
    // try/catch, not a truthiness test: `scene` is a `const` declared below,
    // so touching it before that line runs is a TDZ ReferenceError rather
    // than undefined. Boot is synchronous through both, so this can only
    // matter if a gesture ever lands mid-init — and then panning is the
    // right answer anyway.
    sceneGrabbable: (x, y) => {
      try { return scene.grabbableAt(x, y); } catch { return false; }
    },
    // Same lazy-and-guarded reason as above: the tap that clears the
    // selection has to clear it on both layers, and the bridge lives here.
    sceneDeselect: () => { try { scene.deselect(); } catch { /* pre-init */ } },
  });
  // panes appear and vanish with their indicators — re-attach on every change
  document.addEventListener("charto:indicators-changed", () => draw.syncPanes());

  function selectTool(id) {
    draw.setTool(id);
    el("tool-cursor").classList.toggle("active", id === "cursor");
    const spec = Tools.SPECS[id];
    for (const g of Tools.GROUPS) {
      el(`group-${g.id}`)?.classList.toggle("active", !!spec && spec.group === g.id);
    }
    document.querySelectorAll(".dropdown .item[data-tool]").forEach((n) =>
      n.classList.toggle("on", n.dataset.tool === id));
    setText("drawStatus", spec
      ? `${spec.label} — ${spec.anchors === "free" ? "drag to draw"
          : `click ${spec.anchors} point${spec.anchors > 1 ? "s" : ""}`}`
      : "");
  }

  /* ── the trash: which objects, said before they go ──────────────────────
   *
   * TradingView's trash does not clear the chart — it ASKS. Its menu is one
   * row per layer with that layer's own count ("Remove 2 drawings", "Remove 2
   * indicators") and a last row for both together, so a destructive click
   * states its exact scope before it happens and you never have to guess
   * which of the things on screen the button considered yours.
   *
   * This replaces an arm-then-confirm double click, which had two problems
   * the menu does not. Its confirmation was a line of text in a status strip
   * this chart no longer has — so the first click asked a question nothing on
   * screen printed, and the second answered it blind. And the promise it
   * confirmed was only ever the DRAWING layer, while the chart plainly also
   * carried the chat's annotations and the studies. A button that says "clear
   * all" and clears a third of what you can see is worse than one that asks.
   *
   * Charto's chart holds three sets of objects rather than TradingView's two:
   * what you drew, what the chat drew, and the indicators. The fold control
   * deliberately merges the first two — someone who wants to see the candles
   * does not care whose line is covering them — but delete cannot: which of
   * those two goes is exactly the distinction worth a row here.
   *
   * The menu is BUILT FROM WHAT IS ON THE CHART. An empty layer gets no row,
   * because a row that removes nothing is a control that lies about having
   * done something; on a chart with nothing on it the button itself goes out
   * (syncTrashBtn). The combined row appears only when there are two or more
   * layers for it to combine.
   */
  const TRASH_LAYERS = [
    { key: "drawings", icon: "pen", one: "drawing", many: "drawings",
      count: () => draw.count(),
      clear: () => draw.clearAll() },
    { key: "annotations", icon: "chat", one: "annotation", many: "annotations",
      count: () => scene.count(),
      clear: () => scene.clear() },
    { key: "indicators", icon: "indicators", one: "indicator", many: "indicators",
      count: () => ind.active.size,
      clear: () => {
        // A copy of the keys, because remove() deletes from the map we'd be
        // iterating. The event is what saves the set, repaints the menu and
        // hands the whole removal to the undo stack as one step.
        for (const id of [...ind.active.keys()]) ind.remove(id);
        document.dispatchEvent(new CustomEvent("charto:indicators-changed"));
      } },
  ];
  /** "1 drawing" / "4 drawings" — the count leads, as it does in TradingView's
   *  own rows, because the number is the thing you are checking. */
  const trashPhrase = (l, n) => `${n} ${n === 1 ? l.one : l.many}`;
  /** "2 drawings, 4 annotations & 2 indicators" — commas, then an ampersand
   *  for the last pair, which is how the combined row reads at two items and
   *  still reads at three. */
  const trashList = (parts) => (parts.length < 2 ? parts.join("")
    : `${parts.slice(0, -1).join(", ")} & ${parts[parts.length - 1]}`);
  /** Every layer with something in it, and how much. */
  const trashLive = () =>
    TRASH_LAYERS.map((l) => ({ l, n: l.count() })).filter((x) => x.n > 0);

  /** Remove the named layers in one go — one gesture, one undo step. */
  function trashClear(live) {
    if (!live.length) return;
    for (const x of live) x.l.clear();
  }

  /** The button goes out when the chart is empty.
   *
   *  A trash that opens an empty menu — or worse, opens nothing at all — is a
   *  control that answers a press with silence, and the chart has no status
   *  strip left to explain itself in. Greyed, the button has already said it,
   *  and it says it before the press rather than after. */
  function syncTrashBtn() {
    const b = el("tool-trash");
    if (!b) return;
    const empty = !trashLive().length;
    b.disabled = empty;
    if (empty) closeTrashMenu();   // the last object can go while the menu is up
  }
  // Drawings and the scene both land in syncDrawToggle; the studies announce
  // themselves. Between them every path that adds or removes an object is
  // covered, including the chat's and the undo stack's.
  document.addEventListener("charto:indicators-changed", syncTrashBtn);

  /* Appended to <body>, like the legend's ⋯ menu: the rail is a 46px column
   * with its own overflow, and a menu that has to be wider than its anchor
   * cannot live inside one. */
  let trashMenu = null;
  function closeTrashMenu() {
    if (!trashMenu) return;
    trashMenu.remove();
    trashMenu = null;
    el("tool-trash").classList.remove("menu-open");
  }
  document.addEventListener("click", closeTrashMenu);

  function openTrashMenu(anchor) {
    closeTrashMenu();
    closeToolMenus();
    closeMenus(null);
    const live = trashLive();
    if (!live.length) return;      // the button is already out; belt and braces

    const pop = document.createElement("div");
    pop.className = "dropdown floating open trash-menu";
    pop.innerHTML = live.map((x, i) =>
      `<div class="item danger" data-trash="${i}"><span class="lead">` +
      `${Icons.svg(x.l.icon, "sm")}Remove ${trashPhrase(x.l, x.n)}</span></div>`).join("")
      + (live.length > 1
        ? `<div class="sep"></div><div class="item danger" data-trash="all">` +
          `<span class="lead">${Icons.svg("trash", "sm")}Remove ` +
          `${trashList(live.map((x) => trashPhrase(x.l, x.n)))}</span></div>` : "");
    document.body.appendChild(pop);
    trashMenu = pop;
    anchor.classList.add("menu-open");   // the tooltip stands down

    // The trash is the LAST button on the rail, so the menu is hung off its
    // BOTTOM edge and grows upward — anchored to the top it would open into
    // the time axis and off the foot of the window.
    const r = anchor.getBoundingClientRect();
    pop.style.left = `${r.right + 12}px`;
    pop.style.bottom = `${Math.max(8, innerHeight - r.bottom - 4)}px`;

    pop.addEventListener("click", (e) => {
      e.stopPropagation();
      const it = e.target.closest("[data-trash]");
      if (!it) return;
      closeTrashMenu();
      // The counts were read when the menu opened; nothing can have changed
      // them while it was up, since the menu takes every click.
      trashClear(it.dataset.trash === "all" ? live : [live[+it.dataset.trash]]);
    });
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
      // …or the document handler above would shut the menu in the same click
      // that opened it. The two closes openTrashMenu makes are what this
      // gives up by stopping here.
      e.stopPropagation();
      openTrashMenu(b);
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

  /* ── the view, and the one way back to it ────────────────────────────────
   * THE DEFAULT VIEW is what a chart opens on: the last VIEW_SPAN bars with a
   * little room past the newest one, and a price scale free to fit them. One
   * definition, used by the load, by this button, and by Alt+R — a "reset" that
   * lands somewhere other than where the chart started is not a reset.
   *
   * Written here rather than through timeScale().resetTimeScale(), which does
   * not do it: measured, that call restores the scroll position but leaves the
   * bar spacing where the wheel left it, because the library treats the CURRENT
   * spacing as the default the moment anything writes it. Alt+R promised "back
   * to the default zoom" and delivered only the live edge.
   */
  const VIEW_SPAN = 180, VIEW_PAD = 6;

  /** Put one chart back at its default view. `n` is that chart's bar count —
   *  a secondary pane holds its own data, so the caller supplies it. */
  function resetView(t, n) {
    if (!n) return;
    t.timeScale().setVisibleLogicalRange({ from: n - VIEW_SPAN, to: n + VIEW_PAD });
    for (const pane of t.panes()) {
      try { pane.priceScale("right").applyOptions({ autoScale: true }); } catch {}
    }
  }

  /** Is this chart's view still the default one? Three ways it can have moved,
   *  and the button is offered if any of them has: the price scale pinned by
   *  hand, the time scale panned off the live edge, or zoomed. All READ BACK
   *  off the chart rather than tracked in a flag here — the chart is the one
   *  that knows, and a flag would be wrong the first time anything else moved
   *  the view (a symbol change, a shortcut, the chat drawing to a range). */
  function viewMoved() {
    let manual = false;
    eachPriceScale((ps) => { if (!ps.options().autoScale) manual = true; });
    if (manual) return true;
    const n = state.bars.length;
    if (!n) return false;
    let r = null;
    try { r = chart.timeScale().getVisibleLogicalRange(); } catch { /* pre-layout */ }
    if (!r) return false;
    // Measured against the DEFINITION above, not against the library's own
    // rightOffset: the default view ends VIEW_PAD bars past the newest bar,
    // which is a different number (scrollPosition reads 7 where rightOffset is
    // 5), and comparing to the wrong one left the button on a chart that had
    // not moved. Both halves survive an older page arriving — those bars are
    // prepended and the range is shifted with them, so this measures the gap
    // in BARS either way.
    if (Math.abs(r.to - (n + VIEW_PAD)) > 1) return true;            // scrolled
    const span = VIEW_SPAN + VIEW_PAD;
    return Math.abs((r.to - r.from) - span) / span > 0.02;           // zoomed
  }

  /* TradingView's corner, and TradingView's affordance: one round glass button
   * in the bottom-right of the PLOT — inside both axes, so it never covers a
   * price or a date — that appears only once the view has been moved. It was an
   * "AUTO" pill floating half over the price scale, which named a property of
   * the price scale rather than the thing a hand reaches for it to do, and said
   * nothing about the zoom or the scroll it also has to undo.
   *
   * It fires the verb, not the function: `Shortcuts.run("reset-view")` is what
   * the right-click menu's "Reset view" row and Alt+R already fire, and a
   * second call site calling resetView() directly would be a second definition
   * to keep in step. The chart is passed explicitly because this button is
   * drawn on the PRIMARY's stage — the shortcut's own default is the pane the
   * toolbar is aimed at, which in a split layout is not this one. */
  const resetBtn = document.createElement("button");
  resetBtn.className = "view-reset";
  resetBtn.type = "button";
  resetBtn.innerHTML = Icons.svg("rotateCw", "sm");
  resetBtn.title = "Reset the view (⌥R)";
  resetBtn.setAttribute("aria-label", "Reset the view");
  stageEl.appendChild(resetBtn);
  resetBtn.addEventListener("click", () => {
    Shortcuts.run("reset-view", chart);
    paintResetBtn();
  });

  function paintResetBtn() {
    const on = viewMoved();
    resetBtn.classList.toggle("show", on);
  }

  /* The two axis badges: what the price scale is quoted in, and which clock
   * the time scale is on. Sym is the one place that decides both, so a rupee
   * chart says INR/UTC+5:30 and a Bybit pair says USDT/UTC without either
   * being written down twice.
   *
   * The clock ticks, because a chart of a market that is open is read against
   * the current time — "is that last bar 40 minutes old?" is a question the
   * axis alone cannot answer. It is the SAME clock as the axis, not the
   * viewer's: someone reading an NSE chart from London needs 15:29 IST, and a
   * browser-local clock in that corner would quietly be a different number
   * from every timestamp beside it.
   */
  el("curNote").textContent = Sym.code;
  const tzNote = el("tzNote");
  /* The clock is an HTML overlay, while lightweight-charts draws the
   crosshair's date label into its own canvas. HTML would always win that
   paint order and cut the date plate at the point where they overlap. Treat
   the plate as the foreground: while its footprint reaches the clock, tuck
   the clock away; restore it as soon as the crosshair moves clear.

   The plate is roughly 100px wide at the chart's 12px font. The small guard
   accounts for its horizontal padding and rounded ends. */
  const CROSSHAIR_TIME_HALF_WIDTH = 58;
  const phoneAxis = window.matchMedia("(max-width: 560px) and (orientation: portrait)");
  chart.subscribeCrosshairMove((p) => {
    const x = p && p.point && Number.isFinite(p.point.x) ? p.point.x : null;
    if (x == null) return tzNote.classList.remove("under-crosshair");
    /* At phone width lightweight-charts clamps its date plate back inside
       the canvas when the crosshair approaches either edge. That means its
       painted position is no longer centred on `p.point.x`, so a geometric
       estimate can miss the collision and the HTML clock cuts through the
       black plate. The clock is secondary while a date is being inspected:
       hide it for the lifetime of any phone crosshair, then restore it on
       the null event above. */
    if (phoneAxis.matches) {
      tzNote.classList.add("under-crosshair");
      return;
    }
    const chartBox = chartEl.getBoundingClientRect();
    const noteBox = tzNote.getBoundingClientRect();
    const noteLeft = noteBox.left - chartBox.left;
    const noteRight = noteBox.right - chartBox.left;
    const overlaps = x + CROSSHAIR_TIME_HALF_WIDTH >= noteLeft
      && x - CROSSHAIR_TIME_HALF_WIDTH <= noteRight;
    tzNote.classList.toggle("under-crosshair", overlaps);
  });
  function paintClock() {
    const t = new Date(Date.now() + Sym.tz * 1000);
    const hh = String(t.getUTCHours()).padStart(2, "0");
    const mm = String(t.getUTCMinutes()).padStart(2, "0");
    const ss = String(t.getUTCSeconds()).padStart(2, "0");
    tzNote.textContent = `${hh}:${mm}:${ss} (${Sym.tzLabel})`;
  }
  paintClock();
  setInterval(paintClock, 1000);

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
    paintResetBtn();
  });

  /* Every way the VIEW can move, in one subscription: a wheel zoom, a pan, a
   * double-click auto-fit, and the programmatic jumps too — the chat scrolling
   * to a date, a shortcut, a symbol change. The three ad-hoc listeners this
   * replaces (wheel, dblclick, and the pan's own mouseup) each covered one
   * gesture and missed every other way the same thing happens. The mouseup
   * below stays, because dragging the price SCALE pins it without moving the
   * logical range at all. */
  chart.timeScale().subscribeVisibleLogicalRangeChange(() => paintResetBtn());

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

  /** Everything in the envelope that is true of ANY chart: the instrument,
   *  what is visible, the last bar and the window's own statistics. The
   *  primary adds its drawings, indicators and pins on top of this; a
   *  secondary pane has none of those and must not borrow the primary's.
   *  Returns null when there is nothing loaded yet to describe. */
  function windowEnvelope(bars, chartObj, symName, interval, hasMore) {
    if (!bars.length) return null;
    const d = Sym.of(symName);
    // a secondary pane spells daily intervals D/W/M; both vocabularies mean
    // "no wall clock on this bar"
    const withTime = !DAILY.has(interval) && !["D", "W", "M"].includes(interval);
    const T = (t) => fmtIST(t, withTime);

    const lr = chartObj.timeScale().getVisibleLogicalRange();
    const lo = Math.max(0, Math.floor(lr ? lr.from : 0));
    const hi = Math.min(bars.length - 1, Math.ceil(lr ? lr.to : bars.length - 1));
    const vis = bars.slice(lo, hi + 1);
    if (!vis.length) return null;

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

    return { T, withTime, vis, first, last, env: {
      symbol: d.name, exchange: d.venue,
      source: `local 1-min store (${d.feed})`,
      interval,
      view: {
        from: T(first.time), to: T(last.time),
        bars_visible: vis.length, bars_loaded: bars.length,
        history_from: "2015-02-02", more_history: !!hasMore,
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
    } };
  }

  /** Every chart on screen, primary first — the choices the chat offers and
   *  what it sends when more than one is chosen. */
  function chartList() {
    return [{ pane: 0, symbol: SYMBOL, interval: state.interval,
              bars: state.bars.length, primary: true }]
      .concat(Panes.subsInfo());
  }

  /** `pane` is optional: omit it and the envelope describes whichever chart is
   *  selected; pass an index and it describes that one (the chat pins a pane
   *  so the subject it NAMES and the chart it SENDS cannot drift apart); pass
   *  an ARRAY and the envelope carries all of them — the first that resolves
   *  is the focused chart and the rest ride in `charts[]`.
   *
   *  Several charts are not a different kind of envelope: each entry is built
   *  by exactly the same code that builds a lone one, so nothing downstream
   *  needs a comparison mode to read them. An index the layout no longer has
   *  falls back to the primary. */
  function getChartContext(pane) {
    if (Array.isArray(pane)) {
      /* The FOCUSED chart is the primary whenever it is among the chosen: it
       * is the only one carrying drawings, the chat's own annotations and the
       * pinned bars, so making anything else the head would silently drop
       * them from the turn. */
      const idx = [...new Set(pane)].filter((i) => Panes.hasPane(i));
      if (!idx.length) return getChartContext();
      const head = idx.includes(0) ? 0 : idx[0];
      const built = [head, ...idx.filter((i) => i !== head)]
        .map((i) => getChartContext(i))
        .filter((c) => c && c.symbol && !c.status);
      if (!built.length) return getChartContext(head);
      // one chart chosen is one chart sent — the envelope stays exactly what
      // it has always been rather than growing a one-element list
      return built.length === 1 ? built[0] : { ...built[0], charts: built };
    }
    /* The chart the user last clicked is the chart being discussed. A
     * secondary pane carries its own instrument, interval and indicators, so
     * it can answer for itself — but it holds no drawings, no scene and no
     * pins, and the envelope says which chart this is instead of quietly
     * sending the primary's annotations attached to another pane's prices. */
    const sub = pane == null ? Panes.activeSub() : Panes.paneAt(pane);
    if (sub) {
      const w = windowEnvelope(sub.bars, sub.chart, sub.symbol, sub.interval, false);
      if (!w) return { status: "loading", symbol: sub.symbol, interval: sub.interval };
      return {
        ...w.env,
        // The backend renders `source` verbatim on the envelope's header line,
        // so the pane's nature is said where the model will actually read it —
        // a key it does not render would have been a note to nobody.
        source: `${w.env.source} · secondary pane — drawings, chat annotations `
          + `and pinned bars live on the main chart, not this one`,
        // and the tools enforce it: a reference pane has no drawing layer, so
        // "drawn" must never be said about one
        drawable: false,
        // WHICH chart is the drawable one. Without this the envelope says only
        // that this pane cannot be drawn on, and the model has to guess where
        // ink could go — it guessed "click this pane", which is the one thing
        // that never works. The main chart is the page's own symbol.
        main_chart: SYMBOL,
        indicators: sub.ind.snapshot(w.first.time).map((x) => ({
          ...x, now: r2(x.now),
          at_window_start: x.at === null ? null : r2(x.at),
          lines: Object.fromEntries(Object.entries(x.lines || {})
            .map(([k, v]) => [k, r2(v)])),
        })),
      };
    }

    const bars = state.bars;
    const w0 = windowEnvelope(bars, chart, SYMBOL, state.interval, state.hasMore);
    if (!w0) return { status: "loading", symbol: SYMBOL, interval: state.interval };
    const { T, first } = w0;

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

    /* Chat-drawn annotations, CURRENT geometry — the user can drag these,
     * so the backend must read them from here, not from what it drew.
     *
     * `CT`, not `T`: a scene annotation is stamped in RAW exchange time (the
     * detectors' clock) and the chart runs IST-shifted, so formatting one
     * with the drawing layer's own formatter printed every chat-drawn shape
     * five and a half hours early — "7 Jul 18:30" for a level placed on the
     * 8 Jul session. The model then read that stamp back into evaluate_fib
     * and scored a leg starting on the wrong day, confidently. The user's own
     * drawings are already in chart time (they were placed through xToTime),
     * which is why only this half of the envelope needed the shift. */
    const CT = (t) => T(t + IST);
    const chat_drawings = scene.state.items.slice(0, 20).map((a) => {
      const g = a.kind === "level" ? { price: r2(a.price) }
        : a.kind === "zone" ? { lo: r2(a.lo), hi: r2(a.hi) }
        : (a.kind === "segment" || a.kind === "fib")
          ? { p1: { t: CT(a.p1.t), p: r2(a.p1.v) }, p2: { t: CT(a.p2.t), p: r2(a.p2.v) } }
        : a.kind === "box"
          ? { a: { t: CT(a.a.t), p: r2(a.a.v) }, b: { t: CT(a.b.t), p: r2(a.b.v) } }
        // A catalogued ratio tool travels as its NAME and its anchors, which
        // is all it ever was — the ladder, the fan and the arcs are rebuilt
        // from those two facts on both ends. Sending resolved levels instead
        // would be sending a copy of the construction, and a copy is what
        // goes stale the moment the user drags the shape.
        : a.kind === "drawing"
          ? { tool: a.tool, pts: (a.pts || []).map((q) => ({ t: CT(q.t), p: r2(q.v) })) }
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
      ...w0.env,
      // This IS the main chart — the only one with a drawing layer. Said on
      // both branches so the backend never has to infer it from which keys
      // happen to be present.
      main_chart: SYMBOL,
      indicators: ind.snapshot(first.time).map((x) => ({
        ...x, now: r2(x.now),
        at_window_start: x.at === null ? null : r2(x.at),
        lines: Object.fromEntries(Object.entries(x.lines || {})
          .map(([k, v]) => [k, r2(v)])),
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
    // `notify`, which is the only one of these that a user can actually see.
    //
    // A shape removed with the Delete key vanishes with no other
    // acknowledgement, and a chart that silently loses something is
    // indistinguishable from one that glitched. The obvious-looking channel
    // was setText("drawStatus"), and checking the live DOM says there is NO
    // SUCH ELEMENT — nor #statusLine. The status strip both were written
    // against is gone from the markup, and `setText` no-ops on a missing id,
    // so those messages have been going nowhere for the drawing tools too
    // (see the three remaining call sites above, and layouts.js's comment
    // still describing "#statusLine, #drawStatus" as if they existed).
    // `notify` falls back to that dead strip but reaches Layouts.toast first,
    // which builds its own element and therefore always shows.
    //
    // Called lazily, so `notify` being declared further down this file is
    // fine — the arrow closes over it and nothing runs at wiring time.
    setStatus: (m) => notify(m),
    // detectors speak raw exchange time; the chart runs IST-shifted
    toChartTime: (t) => t + IST,
    // …and back. A sampled curve (a fib circle, a Gann arc) is built where
    // the axis is linear — bar index — and has to hand its points back in the
    // clock the annotation arrived in, or the shape lands half a day off.
    fromChartTime: (t) => t - IST,
    // The fold control lives at the foot of the chip legend, which scene owns
    // — but the number it carries counts the DRAWING layer too, so the state
    // is read from here and the click is handed back here.
    foldState: () => drawFoldState(),
    onFold: () => toggleDrawFold(),
    onChange: (n) => {
      // The chat DRAWING something un-folds the chart. A reply that answers
      // "where's resistance?" by placing a level the fold then swallows is an
      // answer the user never sees — so a fold set five minutes ago yields to
      // the thing that just arrived. Removals and clears do not: those leave
      // less on the chart, which is what the fold was asking for anyway.
      if (drawBooted && drawCollapsed && n > sceneCount) {
        drawCollapsed = false;
        Store.set("draw_collapsed", false);
        applyDrawCollapsed();
      }
      sceneCount = n;
      syncChipsBtn(drawCollapsed ? 0 : n);
      syncDrawToggle();
      const clear = el("sceneClear");
      // Chat owns the same overlay the menu does, and it can replace or clear
      // a profile the menu put there. Whichever window the menu last ticked is
      // then a claim about a histogram that is no longer on screen, so the
      // tick is dropped the moment the drawn profile stops being the menu's.
      if (state.vp) {
        const vp = (scene.state.items || []).find((x) => x.kind === "vprofile");
        if (!vp || !vp.manual) {
          state.vp = null; Store.set("vp", null); renderIndMenu();
        }
      }
      // The eraser is the whole indicator now: it appears exactly when there
      // is something to erase. The count that used to sit beside it is
      // already on the chart, on the .chips-btn that syncChipsBtn drives.
      clear.style.display = n ? "" : "none";
      Store.set("scene", scene.state.items);
      document.dispatchEvent(new CustomEvent("charto:scene-changed"));
      ind.rescalePanes();     // marks feed pane autoscale — recompute now
      indexChatRefs();        // new annotations → new mentions to link
      Undo.touch();           // what chat drew is undoable like anything else
    },
    onHover: (a, y) => {
      const s = (a && a.source) || {};
      setText("drawStatus", a
        ? [a.label, s.strength, s.last_touch && `last ${s.last_touch}`,
           s.method, s.bars_scanned && `${s.bars_scanned} ${s.interval || ""} bars`,
           a.adjusted && "user-adjusted"]
            .filter(Boolean).join(" · ")
        : "");
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
      const changed = () =>
        document.dispatchEvent(new CustomEvent("charto:indicators-changed"));
      // if this indicator is already on the chart, chat RE-PERIODS it in
      // place — a second variant of the same indicator appears only when
      // the user adds one deliberately from the menu
      const activeId = [...ind.active.keys()].find((id) => {
        const d = ind.CATALOG.find((c) => c.id === id);
        return d && d.name === name;
      });
      if (activeId) {
        const d = ind.CATALOG.find((c) => c.id === activeId);
        try {
          let target = activeId;
          if (period && d.period !== period) target = await ind.setPeriod(activeId, period);
          if (a.params && Object.keys(a.params).length) {
            await ind.applySettings(target, { params: a.params });
          }
          changed();
        }
        catch (err) { status(`could not switch ${name} to ${period}: ${err.message}`); }
        return;
      }
      // ensure() mints a def for ANY period, so the line drawn is the line
      // computed — mapping onto presets drew RSI 14 for a quoted RSI 26
      const id = ind.ensure(name, period);
      if (id && !ind.isActive(id)) {
        Promise.resolve(a.params && Object.keys(a.params).length
          ? ind.applySettings(id, { params: a.params }) : null)
          .then(() => ind.toggle(id, state.bars)).then(changed).catch(() => {});
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

  /* ── undo / redo ────────────────────────────────────────────────────────
   * TradingView's pair, in TradingView's place: the top bar, immediately
   * after the controls that ADD things to the chart, so the reverse of an
   * action sits beside the action.
   *
   * What it reverses is the workspace — drawings, what the chat drew, the
   * indicator set — and not the view; js/history.js states that boundary and
   * why. The buttons grey out when their stack is empty rather than clicking
   * to no effect, because a control that silently does nothing is the one
   * thing worse than not having it.
   */
  const undoBtn = el("undoBtn"), redoBtn = el("redoBtn");
  undoBtn.innerHTML = Icons.svg("undo", "sm");
  redoBtn.innerHTML = Icons.svg("redo", "sm");
  // No status line on either: undo SHOWS its result — the line goes, the
  // study leaves the pane — and a message saying "undone" beside a chart that
  // visibly changed is narration, not feedback.
  undoBtn.addEventListener("click", () => Undo.undo());
  redoBtn.addEventListener("click", () => Undo.redo());
  Undo.onChange((s) => {
    undoBtn.disabled = !s.canUndo;
    redoBtn.disabled = !s.canRedo;
  });
  /* Ctrl+Z / Ctrl+Shift+Z, and Ctrl+Y for the Windows hand that reaches for
   * it, are dispatched by js/shortcuts.js along with everything else the
   * sheet prints — including the guard that keeps the chord out of a field
   * the user is typing in, where the browser's own undo is the right one and
   * the only one that knows about their half-written sentence. */
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

  /* ── fold every drawing away ──────────────────────────
   *
   * The indicator legend has had this since it was written — one control that
   * turns a stack of rows into "⌄ 3" — and drawings, which cover far more of
   * the chart than a legend does, had only the trash. So the two things a
   * reader wants when the candles disappear under their own annotations were
   * "delete everything" and nothing.
   *
   * ONE control for BOTH layers. A user who wants to see the bars does not
   * care that the trendline is theirs and the neckline is the chat's; asking
   * them to find two toggles would be asking them to hold a distinction the
   * question does not contain. It hides — it never deletes: state, storage,
   * the chat's context envelope and the undo stack are all untouched, which
   * is what makes this safe to reach for and the trash not.
   *
   * The control itself is DRAWN BY js/scene.js, at the foot of the chip
   * legend — a control belongs under the thing it folds, and that list is
   * where the drawings say what they are. What lives here is the number, the
   * flag and the two setHidden calls; scene.js asks for all three through
   * `foldState` and hands the click back through `onFold`.
   */
  /* The GLOBAL fold, kept as machinery and retired as a saved state.
   *
   * `draw_collapsed: true` is still sitting in the storage of every browser
   * that pressed the old control before it became the layers button — and the
   * control that used to reverse it is gone. Restoring it would open the chart
   * with every annotation hidden, a badge reading 0/9, and the only button in
   * reach opening a list whose per-item switches all read "on", because
   * `state.hidden` is a different flag from the per-item one. That is the
   * stranded state the fold's own code was written to refuse ("a chart must
   * never sit in a hidden state with no control on screen to reverse it").
   *
   * So the flag is dropped at boot rather than migrated: hide-all now lives in
   * the panel, where it sets the per-item switches the panel can also unset. */
  let drawCollapsed = false;
  if (Store.get("draw_collapsed", false)) Store.set("draw_collapsed", false);
  // The restore is not an edit. Boot puts the saved scene back one apply at a
  // time, and every one of those looks exactly like the chat drawing — so the
  // "a new annotation un-folds" rule stays off until the session is standing
  // up, or a chart saved folded would open unfolded every time.
  let drawBooted = false;
  let sceneCount = 0;

  /** What the control should say right now, or null when there is nothing to
   *  fold. Read by scene.js on every chip repaint. */
  function drawFoldState() {
    // LIVE over TOTAL, and both from the PANEL — the list this button opens.
    // Counting the chart layers here instead (draw.count() + scene.count())
    // omits every pattern the detector has found and not yet drawn, so the
    // badge and the panel header disagreed by exactly the rows that are only
    // in the panel. LayersPanel caches this; see the note beside `counts`.
    const c = typeof LayersPanel !== "undefined" && LayersPanel.counts
      ? LayersPanel.counts()
      : { total: draw.count() + scene.count(),
          live: draw.liveCount() + scene.liveCount() };
    return c.total ? { n: c.total, live: c.live, open: layersOpen() } : null;
  }

  /** Repaint the control after something OTHER than a scene change — a shape
   *  placed, dragged or deleted on the drawing layer, which scene.js has no
   *  way to hear about but which its count includes. */
  function syncDrawToggle() {
    // Every path that changes the drawing or the scene layer ends here, which
    // makes it the one place the trash can hear about either of them.
    syncTrashBtn();
    // Nothing left to fold — so the fold goes too, in storage as well as in
    // memory. A chart must never sit in a hidden state with no control on
    // screen to reverse it, and a flag left set would re-hide the next
    // drawing the moment it was made.
    if (drawCollapsed && !drawFoldState()) {
      drawCollapsed = false;
      Store.set("draw_collapsed", false);
      applyDrawCollapsed();
      return;                       // applyDrawCollapsed already repainted
    }
    scene.requestUpdate();
  }

  /** Push the flag into both layers. Separate from the sync above so the
   *  restore at boot and the click take exactly the same path. */
  function applyDrawCollapsed() {
    draw.setHidden(drawCollapsed);
    scene.setHidden(drawCollapsed);   // repaints the chips, and the control
    if (drawCollapsed) {
      // The card and the chat highlight both point AT an annotation. Folding
      // the annotations away has to take them with it, or the card is left
      // describing a line that is no longer on the chart.
      hideProvenance();
      provDraw = null;
      markChatRefs(null);
    }
    // The phone's chip disclosure counts what is on screen, and while folded
    // that is nothing — otherwise the button offers to reveal an empty list.
    syncChipsBtn(drawCollapsed ? 0 : scene.count());
    scene.requestUpdate();
  }

  /* ── the layers popover ──────────────────────────────────────────────
   *
   * The button at the top-right of the price pane used to fold every
   * annotation away; it now opens the inventory that can switch them one at a
   * time (js/layers-panel.js). It hangs off the chip column rather than
   * standing in the left rail because it is a control for the chart in front
   * of you, not a place you navigate to — and because the chips right beside
   * it are where the annotations already say what they are.
   *
   * A popover, not a third column: two sidebars plus the conversation already
   * leave the price pane around 430px on a laptop (see panels.js), and a list
   * you consult for a few seconds should not cost the chart a permanent
   * quarter of its width.
   */
  let layersPop = null;
  const layersOpen = () => !!(layersPop && layersPop.isConnected);

  function closeLayers() {
    if (!layersPop) return;
    /* The lens outlives the node unless it is told not to. It carries its own
     * SVG filter and a ResizeObserver, and this sheet is destroyed and rebuilt
     * on every open — so without this a session of opening the layers list
     * leaves one live filter behind per press. js/ctxmenu.js pairs its glaze
     * with an unglaze for exactly this reason; it does not export the second
     * half, but the handle it parks on the element is all that is needed. */
    if (layersPop.__lg) {
      try { layersPop.__lg.destroy(); } catch { /* already gone */ }
      layersPop.__lg = null;
    }
    layersPop.remove();
    layersPop = null;
    document.removeEventListener("pointerdown", onLayersOutside, true);
    removeEventListener("keydown", onLayersKey, true);
    // The hover the list was driving points at an annotation nobody is
    // pointing at any more.
    scene.setHover(null);
    scene.requestUpdate();
  }

  function onLayersOutside(e) {
    if (!layersPop) return;
    if (layersPop.contains(e.target)) return;
    // A node the panel already REPLACED is not "outside" it. The layer list
    // re-renders its own innerHTML on every keystroke of the search box, so a
    // press can resolve against an element that was detached a frame earlier —
    // and `contains()` is false for anything not currently in the tree, which
    // read as a click on the chart and closed the sheet mid-search.
    if (e.target instanceof Node && !e.target.isConnected) return;
    // The button owns its own toggle; letting this handler close first would
    // make the second press re-open rather than close.
    if (e.target.closest && e.target.closest(".scene-layers")) return;
    closeLayers();
  }

  function onLayersKey(e) {
    if (e.key !== "Escape" || !layersPop) return;
    e.stopPropagation();
    closeLayers();
  }

  function toggleDrawFold() {
    if (layersOpen()) { closeLayers(); scene.requestUpdate(); return; }
    if (window.__chartoCloseMenus) window.__chartoCloseMenus(null);
    const anchor = document.querySelector(".scene-layers");
    if (!anchor || typeof LayersPanel === "undefined") return;

    const pop = document.createElement("div");
    pop.className = "dropdown floating layers-pop open";
    pop.setAttribute("role", "dialog");
    pop.setAttribute("aria-label", "Chart layers");
    document.body.appendChild(pop);
    layersPop = pop;

    /* Pinned to the anchor's own rect and flipped up when the lower half of
     * the window cannot hold it — the same rule js/universe.js uses, and for
     * the same reason: a menu that opens off-screen is a dead menu. Clamped
     * on the RIGHT edge, because this anchor lives at the right edge of the
     * plot and a left-anchored panel would hang over the price axis. */
    const r = anchor.getBoundingClientRect();
    const W = 344, H = 420;
    pop.style.width = `${W}px`;
    pop.style.left = `${Math.max(8, Math.min(r.right - W, innerWidth - W - 8))}px`;
    /* BOTH branches cap the height, from the space that branch actually has.
     * Only the downward one used to, and flipped up the sheet fell back to
     * `.dropdown`'s `max-height: calc(100vh - 72px)` — a cap measured against
     * the VIEWPORT while the panel was measured from the anchor. With the
     * button low on a tall chart and a dozen layers in the list, that let the
     * sheet grow past the top of the window and take its own header — search,
     * show-all, the count — off screen, with the list scrolled to a place the
     * user could not scroll back from. The body is `flex: 1; min-height: 0`,
     * so a definite cap here IS what creates the scrollport. */
    if (r.bottom + H > innerHeight && r.top > H) {
      pop.style.bottom = `${innerHeight - r.top + 6}px`;
      pop.style.maxHeight = `${Math.max(200, r.top - 20)}px`;
    } else {
      pop.style.top = `${r.bottom + 6}px`;
      pop.style.maxHeight = `${Math.max(200, innerHeight - r.bottom - 20)}px`;
    }

    LayersPanel.render(pop);
    /* The same lens every other sheet in this app wears. The auto-glaze
     * observer at the foot of js/ctxmenu.js watches for `.open` being ADDED to
     * an existing `.dropdown`; this sheet is created with `open` already on it
     * and appended, which is an attribute the observer never sees change — so
     * it would have been the one menu in the app that merely blurred while
     * every other one refracted. Attached here, after placement and after the
     * body is rendered, because the displacement map is generated from the
     * measured box. */
    if (typeof Ctx !== "undefined" && Ctx.glass) Ctx.glass(pop);
    document.addEventListener("pointerdown", onLayersOutside, true);
    addEventListener("keydown", onLayersKey, true);
    scene.requestUpdate();                     // repaint the button's open state
  }

  // ── provenance card: every drawn line is interrogable ──
  const prov = el("provCard");
  let provFor = null;
  let peekTimer = 0;

  /** A pattern's label, split into the segments the detector packed into it:
   *  "falling wedge · width 40.81 · unresolved" → the name, then its facts.
   *
   *  A formation is drawn as several linked pieces — outline, fill, upper and
   *  lower edge, neckline — and only some carry the label. Hovering the fill
   *  must still say "falling wedge", so the name is resolved across the whole
   *  `link` group rather than off the piece under the pointer. */
  function patternParts(a) {
    const kin = a.link
      ? scene.state.items.filter((q) => q.link === a.link && q.label)
      : (a.label ? [a] : []);
    const named = kin.find((q) => q.label) || a;
    return String(named.label || "").split("·")
      .map((x) => x.trim()).filter(Boolean);
  }

  /** What this annotation is CALLED — the name its card is titled with, and
   *  the name the composer's chip wears when you ask about it. One function,
   *  so the two can never disagree about what you clicked. */
  function annName(a) {
    const s = a.source || {};
    if (s.tool === "get_patterns") return patternParts(a)[0] || "Pattern";
    // Two tools fit the same line through the same swings — the catalogue and
    // the trend read — so a line names itself after what it IS, never after
    // which call happened to draw it.
    if (s.tool === "get_trendlines" || s.tool === "get_trend") return "Trendline";
    if (s.tool === "get_divergences") {
      return `${String(s.strength || "").replace(/_/g, " ").trim()} divergence`.trim();
    }
    // A ratio tool is called what the rail calls it. Falling through to
    // "Resistance" would name a Gann square by the colour it happened to be
    // drawn in, and the label the model wrote is a caption, not the name.
    if (a.kind === "drawing" && Tools.SPECS[a.tool]) {
      return Tools.SPECS[a.tool].label;
    }
    return a.role === "resistance" ? "Resistance"
      : a.role === "support" ? "Support" : (a.label || "Annotation");
  }

  /** The card's title LINE — the name and, where the card prints one, the
   *  number beside it. "Resistance" alone does not identify anything on a
   *  chart carrying three of them; "Resistance 1,282.50" does, which is why
   *  the card's header has always had two slots. Anything named after itself
   *  (a wedge, a divergence) needs no number and is given none. */
  function annTitle(a) {
    const num = (n) => (Number.isFinite(Number(n))
      ? Sym.num(n, { minimumFractionDigits: 2 }) : null);
    const n = a.kind === "level" ? num(a.price)
      : a.kind === "zone" && num(a.lo) && num(a.hi) ? `${num(a.lo)}–${num(a.hi)}`
        : null;
    return [annName(a), n].filter(Boolean).join(" ");
  }

  /* Kinds the backend can resolve BY ID out of the chart envelope — the
   * strong form of the tag, where the model scores the object itself instead
   * of working from a description of it. Everything else (a pattern's
   * polygon, a marker run, a box) has no evaluator behind it, so it travels
   * as prose and is not offered as a handle that would fail to resolve.
   * Mirrors data/dataserver.py's `_chat_drawing_as_user`. */
  const ANN_ADDRESSABLE = new Set(["level", "zone", "segment", "fib", "position",
                                   "drawing"]);

  /** Can this annotation be handed to the evaluate tools by id?
   *
   *  A `link` is what says the piece under the pointer is one leg of a
   *  FORMATION — a wedge is an upper edge, a lower edge and a fill; a head
   *  and shoulders is an outline, three marked peaks and a neckline. Those
   *  legs are individually addressable kinds, so without this a wedge tagged
   *  by its edge would ask for a trendline to be scored and the same wedge
   *  tagged by its fill would not — the same object producing two different
   *  questions depending on which pixel raised the card. And the trendline
   *  answer would be the wrong one anyway: nothing scores a formation, and
   *  scoring one of its edges is not a smaller version of doing so. A
   *  formation travels as prose; a level, a zone, a plan or a lone line
   *  travels as a handle. */
  const annScoreable = (a) => !a.link && ANN_ADDRESSABLE.has(a.kind);

  /** The annotation as the composer's tag: what to call it, what it is worth
   *  saying about it, and — where one exists — the handle the evaluate tools
   *  resolve it by. */
  function annTag(a) {
    const s = a.source || {};
    const title = annTitle(a);
    // The detector's own label is richer than the name it starts with; the
    // span is the other half of what identifies one formation among several
    // of the same kind on one chart.
    const facts = [];
    // Resolved across the `link` group, exactly as the card's title is: a
    // formation's measurements sit on whichever of its legs carries the
    // label, and tagging it by an unlabelled edge must not lose them.
    const label = a.label || (a.link
      ? (scene.state.items.find((q) => q.link === a.link && q.label) || {}).label
      : "") || "";
    if (label) {
      // A detector's label often LEADS with the name the title already
      // carries — "falling wedge · width 40.81 · unresolved" under the title
      // "falling wedge" — and repeating it makes the tag stutter. Only what
      // the title has not already said travels.
      const rest = label.startsWith(title)
        ? label.slice(title.length).replace(/^\s*·\s*/, "") : label;
      if (rest && rest !== title) facts.push(rest);
    }
    if (s.first_touch) {
      facts.push(s.last_touch && s.last_touch !== s.first_touch
        ? `${s.first_touch} → ${s.last_touch}` : s.first_touch);
    }
    return {
      // `origin` and `type` are the composer chip's vocabulary, not this
      // function's: chat.js draws one attachment for both layers and reads
      // those two keys to decide what it says ("Chart analysis") and which
      // glyph it wears. Its icon map is already keyed by the scene's own
      // kinds, so a level arrives as a horizontal line and a pattern as a
      // channel rather than everything falling back to a trendline.
      annotation: true, origin: "chat", type: a.kind,
      id: a.id, kind: a.kind, label: title,
      ref: annScoreable(a) ? a.id : undefined,
      on: a.pane && a.pane !== "price"
        ? ((ind.CATALOG.find((c) => c.id === a.pane) || {}).label || a.pane) : undefined,
      detail: facts.join(" · ") || undefined,
    };
  }

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
    // `peek` has to go with `open`, or it outlives the hover that set it: the
    // next card CLICKED open inherited the peek styling — narrower, and with
    // its close button display:none'd — so a card you opened deliberately had
    // no way out but Escape.
    prov.classList.remove("open", "peek");
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
    /** A row that ENDS in a number: the qualifier reads on the left and the
     *  figure closes the row against the card's right edge, so every number
     *  in the card sits on one margin instead of wherever its sentence
     *  happened to end. Either half may be missing — with only a qualifier
     *  the row is the prose it always was, and with neither, row() drops it. */
    const val = (q, v) => {
      const has = (x) => x != null && x !== "";
      if (!has(v)) return has(q) ? String(q) : "";
      return (has(q) ? `<span class="t">${q}</span>` : "") + `<b class="v">${v}</b>`;
    };
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
      // The label is "Name · Strength" now — the whole of it, for every
      // family, so the shape's identity and how much it is worth read the
      // same way on a wedge and on a double top. It used to carry a
      // measurement too ("double top · neckline 1,271.00 · confirmed"), which
      // put the formation's least surprising number where its headline
      // should be: the neckline IS the dashed line the label is attached to.
      //
      // Strength is a graded judgement from the backend, not the detector's
      // state — see `_pattern_strength`. The state is still worth a row here,
      // because it is one of the inputs and a reader asking "why moderate"
      // deserves the input rather than a re-derivation.
      const parts = patternParts(a);
      kindName = parts.shift() || "Pattern";
      const cap = (t) => t.charAt(0).toUpperCase() + t.slice(1);
      const facts = parts.map((p) => {
        const m = /^(.+?)\s+(-?[\d,]+(?:\.\d+)?%?)$/.exec(p);
        return m ? row(cap(m[1]), val(null, m[2])) : "";
      }).join("");
      title = null;
      body = facts
        + row("Strength", words(s.strength))
        + (s.status ? row("State", words(s.status)) : "")
        + row("Bias", a.role === "support" ? "bullish"
          : a.role === "resistance" ? "bearish" : "neutral")
        + row("Spans", span(s.first_touch, s.last_touch));
    } else if (s.tool === "get_trendlines" || s.tool === "get_trend") {
      kindName = "Trendline";
      title = null;
      body = row("Record", val(words(s.strength), s.touches ? `${s.touches} touches` : null))
        + row("Anchored", span(s.first_touch, s.last_touch));
    } else if (s.tool === "get_divergences") {
      title = `${words(s.strength) || ""} divergence`.trim();
      body = row("Record", words(s.record))
        + row("Instances", s.touches ? val("in this window", s.touches) : "")
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
        : val(`held ${ev.held} of ${graded}`,
              ev.hold_rate == null ? null : `${ev.hold_rate}%`))
        + row("Reaction", ev.react_pct == null ? ""
          : val(dot(a.role === "resistance" ? "down" : "up",
                    ev.react_bars == null ? null : `median ${ev.react_bars} bars`),
                `${ev.react_pct}%`))
        + row("Judged", graded && s.horizon_bars
          ? `re-tests only, ${s.horizon_bars} bars each` : "")
        + row("Touches", val(words(s.strength), s.touches))
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
        <button class="btn danger" data-act="remove">${Icons.svg("trash", "xs")}Remove</button>
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
        // Never inside the follow-up suggestions: those are questions the
        // user has not asked yet, so a price in one refers to nothing on the
        // chart and marking it as a live reference is simply false.
        //
        // Nor inside a tool's own panel. A card already knows exactly which
        // annotation each row is — the id came off the same tool call that
        // drew it — so re-deriving that by matching its text would be a
        // guess replacing a fact, and the unwrap at the top of this pass
        // would tear up markup this module did not write.
        acceptNode: (n) => (n.parentElement && !n.parentElement.closest("span.ann-ref")
          && !n.parentElement.closest(".suggest")
          && !n.parentElement.closest(".scan")
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
  //
  // Any [data-ann] in the thread, not only a wrapped mention: a tool's result
  // panel names its annotations by id rather than by prose, and a card row
  // pointing at a drawn shape is the same gesture as a sentence mentioning it.
  // One selector, so the two can never behave differently. `setHover` takes a
  // formation's LINK as readily as a single shape's id (scene.js lights the
  // whole group), which is what lets one tile highlight an outline, its fill
  // and its neckline together.
  if (threadEl) {
    const annAt = (e) => e.target.closest && e.target.closest("[data-ann]");
    threadEl.addEventListener("mouseover", (e) => {
      const r = annAt(e);
      if (r) scene.setHover(r.dataset.ann);
    });
    threadEl.addEventListener("mouseout", (e) => {
      const r = annAt(e);
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
    /* Chart time, NOT chart time plus IST. Every anchor here came out of
     * xToTime, which builds it from state.bars — and those were shifted by
     * IST once already, at the fetch. Adding it a second time printed every
     * drawing's card 5h30m late: a trendline anchored on the 15:20 bar read
     * "20:50", which is not a time this exchange has bars at. Every other
     * fmtIST call in this file passes chart time straight through; this was
     * the one outlier. */
    const T = (t) => fmtIST(t, !DAILY.has(state.interval));
    // Two decimals, like every other price in the app. Raw drawing geometry
    // carries a third ("1,268.091"), which reads as a precision the anchor
    // does not have — you dragged it there.
    const num = (n) => Sym.num(n,
      { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    const g = draw.geometryOf(d.id) || { pts: [] };
    const unit = d.pane && d.pane !== "price"
      ? ((ind.CATALOG.find((c) => c.id === d.pane) || {}).label || d.pane) : "";
    // A shape that measures TIME carries a value at each anchor only because
    // a drag has to land somewhere — the price under a date range's edge is
    // not a reading, and printing it in the same column as a price range's
    // endpoints would say it was one. Those cards state their times and their
    // bar count, and nothing they did not measure.
    const TIME_ONLY = new Set(["dateRange", "vline"]);
    const NO_DELTA = new Set(["dateRange", "vline", "text", "brush"]);
    const timeOnly = TIME_ONLY.has(g.type);

    // Anchors that MEAN something get their meaning as their label. A position
    // tool's three points are an entry, a target and a stop — the shape is
    // built from them in that order — and a card that called them "Point 2"
    // and "Point 3" made you re-derive on sight which line was the stop.
    const ANCHORS = {
      long: ["Entry", "Target", "Stop"], short: ["Entry", "Target", "Stop"],
      channel: ["From", "To", "Width"],
    };
    const pts = g.pts.slice(0, 3);
    const NAMES = ANCHORS[g.type]
      || (pts.length > 2 ? ["Point 1", "Point 2", "Point 3"] : ["From", "To"]);
    // A row is a QUALIFIER and a VALUE, not a sentence: the timestamp reads
    // on the left and the price closes the row hard against the right edge,
    // so the numbers stack into one column you can run your eye down. The
    // old "12 Jul 2026 08:15 @ 1,315.68" put the figure that matters in the
    // middle of a string, at a different x on every row.
    const rows = pts.map((p, i) =>
      `<dt>${NAMES[i] || "Point"}</dt><dd><span class="t">${T(p.t)}</span>`
      + (timeOnly ? "" : `<b class="v">${num(p.v)}</b>`) + `</dd>`).join("");

    // What a two-anchor shape is FOR is the distance between its ends. The
    // chart already draws that on the shape; the card was the one place that
    // made you subtract by eye. It is derived from the two rows printed
    // above it and from nothing else, so it cannot disagree with them.
    let extra = "";
    if (g.pts.length === 2 && !NO_DELTA.has(g.type)) {
      const dv = g.pts[1].v - g.pts[0].v;
      const base = g.pts[0].v;
      const sign = dv > 0 ? "+" : dv < 0 ? "−" : "";
      const dir = dv > 0 ? "up" : dv < 0 ? "down" : "";
      const abs = `${sign}${num(Math.abs(dv))}`;
      // Per-cent of an indicator's own scale is not a per-cent of anything
      // (RSI 30 → 60 is not "up 100%"), so it is priced only in the price pane.
      const pct = !unit && Number.isFinite(base) && base !== 0
        ? `${sign}${Math.abs((dv / base) * 100).toFixed(2)}%` : null;
      extra += `<dt>Change</dt><dd>`
        + (pct ? `<span class="t num">${abs}</span><b class="v ${dir}">${pct}</b>`
               : `<b class="v ${dir}">${abs}</b>`)
        + `</dd>`;
    }
    // The same count the tool prints on the chart, from the same arithmetic —
    // a span in bars is what a date range measured, and the other half of what
    // the measure tool did. Labelled "Bars" rather than "Span: 108 bars" so
    // the figure lands in the card's number column instead of trailing a word.
    if (g.pts.length === 2 && (timeOnly || g.type === "measure")) {
      const sec = IV_SEC[state.interval];
      const n = sec
        ? Math.max(1, Math.round(Math.abs(g.pts[1].t - g.pts[0].t) / sec)) : null;
      if (n) extra += `<dt>Bars</dt><dd><b class="v">${n}</b></dd>`;
    }
    if (g.pts.length > 3) extra += `<dt>Anchors</dt><dd><b class="v">${g.pts.length}</b></dd>`;

    prov.innerHTML = `
      <header>
        <span class="role">${d.label}</span>
        <span class="draw-ref" title="How the chat refers to this drawing">${d.ref}</span>
        <button class="btn icon" data-act="close" title="Close">${Icons.svg("x", "xs")}</button>
      </header>
      <dl>
        ${unit ? `<dt>Pane</dt><dd><span class="t">${unit}, in its own units</span></dd>` : ""}
        ${rows}
        ${extra}
      </dl>
      <footer>
        <button class="btn cta" data-act="ask-draw">${Icons.svg("chat", "xs")}Ask in chat</button>
        <button class="btn danger" data-act="del-draw">${Icons.svg("trash", "xs")}Remove</button>
      </footer>`;
    // Docked, not tracked. This used to set `top` from the pointer while the
    // stylesheet pinned `bottom` — with both edges fixed the card stretched
    // the full height of the chart instead of sitting where either wanted it.
    prov.style.top = "auto";
    dockProv();
    // this one is CLICKED open, so it is never the hover's abbreviated form
    prov.classList.remove("peek");
    prov.classList.add("open");
  }
  /* A TEXT annotation gets no card. The card exists to say what a shape
   * cannot say for itself — where a trendline's ends are, what a channel is
   * worth, how many bars a range spans. A text chip has none of that: its
   * anchors are wherever you clicked, and its entire content is already
   * printed on the chart at full size. All the card added was a second,
   * larger box quoting a timestamp and a price the label never claimed to be
   * about, over the label it was describing. Selecting still works — drag it,
   * Delete removes it. */
  const NO_CARD = new Set(["text"]);
  document.addEventListener("charto:draw-select", (e) => {
    if (!e.detail) return hideProvenance();
    // finishing a drawing selects it, but popping its card mid-flow
    // interrupts someone laying out several shapes in a row
    if (e.detail.via === "create") return;
    // hide, not return: a card open on the PREVIOUS selection would otherwise
    // stay up describing a shape that is no longer the selected one
    if (NO_CARD.has(e.detail.type)) return hideProvenance();
    showDrawingCard(e.detail, lastUpAt[1] - stageEl.getBoundingClientRect().top);
  });

  prov.addEventListener("click", (e) => {
    const act = e.target.closest("[data-act]")?.dataset.act;
    if (!act) return;   // the card has no pin state to promote to
    if (act === "ask" && provFor) {
      const a = scene.state.items.find((item) => item.id === provFor);
      if (a) {
        // Chat-created annotations are addressable by their scene id in the
        // backend, exactly as user drawings are addressable by D-refs. Send
        // the id instead of copying geometry into prose: the next turn's
        // chart envelope carries the current annotation and the model remains
        // free to decide which tool, if any, is appropriate.
        //
        // What to CALL it, and whether that id is a scoring handle at all,
        // are annTag's two jobs — see there. The tag was built inline here
        // from `a.label || a.kind`, which named a wedge "segment" whenever
        // the leg you happened to hover was one of the unlabelled ones, and
        // offered its id as a drawing_id for kinds no evaluator can convert.
        document.dispatchEvent(new CustomEvent("charto:draw-tag",
                                               { detail: annTag(a) }));
      }
      return hideProvenance();
    }
    if (act === "remove" && provFor) {
      scene.remove(provFor);
      return hideProvenance();
    }
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
    // Close and any unknown action simply dismiss the card.
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

  /* ── the ⊕ on the price axis: one click, one alert ────────────────────────
   * It rides the crosshair's own price plate, at the plate's left end, and arms
   * a crossing alert at that level on a single click. This is the fastest path
   * there is — no dialog, no typing, and the level is exactly the one being
   * pointed at rather than one transcribed into a field.
   *
   * Only in the price pane and only with the cursor tool. Signed-out users can
   * still see the affordance; Alerts.open explains that sign-in is required.
   * It hides the moment the pointer leaves.
   *
   * ON THE SCALE, not beside it — Groww's placement, and the one that ends
   * three separate complaints at once. Floating on the CHART it was a 20px disc
   * following the pointer down the last column of candles: it covered whatever
   * was drawn there, and because the click is resolved against its rectangle
   * (below) it also ATE that click — so a trendline under it could not be
   * selected, and therefore could not be deleted. Inside the scale it is out of
   * the drawing surface entirely.
   *
   * It lands at the left end of the crosshair's price plate while the pointer
   * is over the plot, and stands on the bare axis for the last twenty pixels of
   * the reach for it, because the plate deliberately does not follow the
   * pointer onto the scale. So it carries an opaque disc
   * of the chart's own background and is drawn in the app's ink rather than the
   * plate's — one appearance that reads in both places and in both themes.
   */
  let alertPlus = null, plusPrice = null;

  /* There is no crosshair HOLD any more, and its absence is the feature.
   *
   * The plate used to be kept alive by hand for the last part of a reach from
   * the pane onto the price scale, so the mark drawn in the scale stayed put
   * long enough to be clicked. The cost was that pointing at the scale AT ALL
   * — to drag it and rescale, which is what the scale is for — printed a price
   * plate and a mark over the axis you were trying to grab. The scale is the
   * scale: the pointer entering it now ends the readout instead of freezing
   * one, exactly as the library does on its own. */

  function hidePlus() {
    // `hot` goes with `show`, or it outlives the mark: the cursor rule keys off
    // it (see #chart:has(.alert-plus.show.hot) in index.html) and a stale one
    // leaves a pointer cursor standing over a scale with no mark on it.
    if (alertPlus) alertPlus.classList.remove("show", "hot");
    plusPrice = null;
  }

  /** The ⊕ is a DRAWING, not a control. `pointer-events: none`, no listener of
   *  its own, and the click is caught on the chart instead.
   *
   *  It was a real <button> first and could not be clicked, for a reason worth
   *  recording. The chart library owns several stacked canvases here and binds
   *  its own mouse handling to them; a 22px button floating over that is at the
   *  mercy of whichever element the browser decides the press landed on, and
   *  measured in Chrome it lost — mousedown went to the canvas while
   *  elementFromPoint over the same pixel returned the button's own icon. Every
   *  fix for that is a fight with hit-testing.
   *
   *  Owning the click on chartEl removes the fight: the pointer's position is
   *  compared against the mark's rectangle, which is arithmetic and cannot be
   *  intercepted. The mark is then free to be what it should have been — a
   *  picture of where the alert would go.
   */
  const PLUS_PAD = 4;          // a 16px target is small; forgive a few pixels

  /* The ⊕ lives on the PANE side of the scale, which is the whole trick.
   *
   * It used to be drawn wholly INSIDE the price scale, so the only way to
   * reach it was to put the pointer on the scale — and the scale is what you
   * grab to rescale, so the readout had to be kept alive over it by hand and
   * lit up every time you went to drag the axis. Moving the ring to the left
   * of the scale's edge dissolves that conflict rather than trading one side
   * of it away: the mark is reachable from the chart, where the pointer
   * already is, and the axis is left alone to be an axis. TradingView puts it
   * in the same place, for what is presumably the same reason.
   *
   * The plate keeps the price and stays in the scale; the ring is a separate
   * disc with its own paint and a gap between them. `.alert-plus` itself is
   * now only the row that positions the two — it carries no background, or it
   * would drag the plate's grey out over the candles with it. */
  function makePlus() {
    const b = document.createElement("div");
    b.className = "alert-plus";
    // Three nodes, not two: the joined square HALF, the circled ring inside
    // it, and the price. TradingView's mark is a ⊕ — a ring with a plus in it
    // — sitting on a square button that abuts the label, and the ring is the
    // part that says "add". A bare plus on a square reads as a UI chevron.
    b.innerHTML = `<span class="alert-plus-mark">`
      + `<span class="alert-plus-ring">${Icons.svg("plus", "xs")}</span></span>`
      + `<span class="alert-plus-value"></span>`;
    chartEl.appendChild(b);
    return b;
  }

  /** Is this pointer position on the ring?
   *
   *  Measured off the ring's own box, so the two cannot drift. The pad is kept
   *  tight on purpose: this rectangle does not merely receive the click, it
   *  SWALLOWS it, and every pixel of it is a pixel of chart where a drawing
   *  cannot be picked up. 4px forgives a shaky hand and is small enough that a
   *  trendline running under the mark is still selectable either side of it. */
  function onPlus(x, y) {
    if (!alertPlus || !alertPlus.classList.contains("show")) return false;
    const mark = alertPlus.querySelector(".alert-plus-mark");
    if (!mark) return false;
    const r = mark.getBoundingClientRect();
    return x >= r.left - PLUS_PAD && x <= r.right + PLUS_PAD
        && y >= r.top - PLUS_PAD && y <= r.bottom + PLUS_PAD;
  }

  function syncPlus(clientX, clientY) {
    /* THE PLATE DECIDES, not the pointer. The mark is printed on the crosshair's
     * price plate, so it appears exactly when that plate does, at exactly its y,
     * carrying exactly the price written on it — one answer to "what level is
     * being pointed at" instead of two that can drift apart.
     *
     * Which is also the whole of the fix for the mark that used to appear on
     * the bare price scale with nothing around it: the plate does not follow the
     * pointer over there, so neither does the mark. It survives
     * only the straight reach the plate itself survives.
     *
     * Only over the PRICE pane: an alert is a level in rupees, and the plate
     * over an RSI pane is reading a different scale. */
    const chartBox = chartEl.getBoundingClientRect();
    const price = panesList().find((p) => p.key === "price");
    const paneEl = price && price.pane.getHTMLElement && price.pane.getHTMLElement();
    const paneBox = paneEl && paneEl.getBoundingClientRect();
    // The right edge is the SCALE's left edge, not the chart's. `chartBox.right`
    // put the whole price scale inside this test, so hovering the axis — the
    // one place the pointer goes to rescale rather than to read — lit the plate
    // and the mark on top of the axis it was aiming at. Measured off the same
    // published metric the mark's own geometry uses, so the two cannot drift.
    syncChartMetrics();
    const scaleLeft = chartBox.right - metrics.ps;
    /* The top of the scale is OCCUPIED. The currency badge sits at the head of
     * the price scale and the layers chip beside it, both anchored to the top
     * right — and the pill is 22px tall centred on the pointer, so anywhere in
     * the first ~36px it lands ON them: a price plate printed across "INR" and
     * a ⊕ overlapping the chip, which is what the screenshot showed.
     *
     * Measured off the elements rather than a magic number, because both are
     * conditional — the chip only exists once something is drawn, and the
     * badge's height follows the type scale. Half the pill's height is added
     * so the guard is about where the pill would REACH, not where it is
     * centred. */
    const topGuard = [document.getElementById("curNote"),
                      document.querySelector(".scene-layers")]
      .reduce((y, el) => {
        if (!el || !el.isConnected || !el.offsetParent) return y;
        const r = el.getBoundingClientRect();
        return r.width ? Math.max(y, r.bottom) : y;
      }, chartBox.top) + 11;
    const insidePrice = paneBox
      && clientX >= chartBox.left && clientX < scaleLeft
      && clientY >= Math.max(paneBox.top, topGuard) && clientY <= paneBox.bottom;
    if (draw.state.tool !== "cursor" || !insidePrice) return hidePlus();
    const y = yInPane(clientY, "price");
    const px = y === null ? null : candle.coordinateToPrice(y);
    if (px == null || !isFinite(px)) return hidePlus();
    // isConnected, not a null check: the chart library owns this container and
    // rebuilds its contents, so the node we made can be gone while the variable
    // still holds it.
    if (!alertPlus || !alertPlus.isConnected) alertPlus = makePlus();
    plusPrice = Number(px.toFixed(px >= 100 ? 2 : 4));
    const value = alertPlus.querySelector(".alert-plus-value");
    if (value) {
      try { value.textContent = candle.priceFormatter().format(plusPrice); }
      catch { value.textContent = String(plusPrice); }
    }
    const box = chartBox;
    alertPlus.style.top = (clientY - box.top) + "px";
    alertPlus.title = `Alert at ${Sym.of(SYMBOL).price(plusPrice,
      { maximumFractionDigits: 2 })}`;
    alertPlus.classList.add("show");
    // it cannot have a :hover state of its own, so it is told when it is under
    // the pointer — otherwise the one control on the chart gives no feedback
    alertPlus.classList.toggle("hot", onPlus(clientX, clientY));
  }

  chartEl.addEventListener("mousemove", (e) => syncPlus(e.clientX, e.clientY));
  chartEl.addEventListener("mouseleave", hidePlus);
  document.addEventListener("charto:draw-select", hidePlus);

  /* Capture phase, so it runs before the library's own canvas handlers and can
   * stop the click from also pinning the candle underneath. */
  chartEl.addEventListener("click", (e) => {
    if (!onPlus(e.clientX, e.clientY)) return;
    const at = plusPrice;
    e.preventDefault();
    e.stopPropagation();
    e.stopImmediatePropagation();
    hidePlus();
    if (at == null) return;
    // The mark chooses the exact price; the compact widget confirms the
    // direction, frequency and expiry before anything is registered.
    const last = state.bars.length ? state.bars[state.bars.length - 1] : null;
    Alerts.open({ symbol: SYMBOL, level: at,
                  last: last ? last.close : null, interval: state.interval });
  }, true);
  // A press on the mark is ours too — swallowed so a click on it can never be
  // read as the start of a pan or a drawing.
  chartEl.addEventListener("mousedown", (e) => {
    if (onPlus(e.clientX, e.clientY)) {
      e.preventDefault();
      e.stopPropagation();
      e.stopImmediatePropagation();
    }
  }, true);


  /* ── right-click: three menus, routed by what is under the pointer ────────
   *
   * TradingView shows ONE menu wherever you click, so most of its rows are
   * about the chart rather than about the thing you pointed at. But pointing
   * IS the gesture: the price, the bar or the shape you aimed for is the
   * subject, and a menu that already knows which of the three it was can
   * answer a different question in each case. So there are three of them,
   * and the router at the bottom of this section picks one.
   *
   * Two rows appear in all three, in the same slot:
   *
   *   Chat ▸  three or four questions built HERE, out of what this chart
   *           already knows about the point — the bar's own numbers, the
   *           markers sitting on it, the drawing's kind. Nothing is fetched
   *           to fill the list: it has to be up before the pointer has
   *           moved, and a question offered on a chart that cannot answer it
   *           is the same failure as an invented number, so a row is only
   *           written when the thing it asks about is actually there.
   *
   *   Tag     attaches the point to the composer and stops. No question, no
   *           send — it is the row for someone who knows what they want to
   *           ask and only needs the chart to say WHERE they mean, which is
   *           precisely what the model cannot infer from the word "this".
   *
   * None of the three tags is new plumbing. A bar is a pin; a drawing is
   * `charto:draw-tag`, the same event its card fires; a bare coordinate is
   * `charto:compose` carrying the one address format the backend parses.
   */

  /** How many decimals this instrument's prices are worth — the same rule
   *  the ⊕ on the price axis already applies a few hundred lines up. Two for
   *  anything priced like a share, four below that, because a paise-priced
   *  pair rounded to 2 is an alert armed at a level that does not exist. */
  const digitsAt = (px) => (Math.abs(px) >= 100 ? 2 : 4);
  /** The price as an alert or an address should carry it: a bare number, no
   *  grouping, at that precision. */
  const levelAt = (px) => Number(px.toFixed(digitsAt(px)));
  /** …and as a row should print it. Minimum and maximum are the same, so a
   *  column of four prices lines up instead of one of them dropping its
   *  trailing zeros and losing the decimal point everything else is on. */
  const priceAt = (px) => Sym.of(SYMBOL).price(px, {
    minimumFractionDigits: digitsAt(px), maximumFractionDigits: digitsAt(px),
  });
  /** "17 Aug 2026 12:11 @ ₹1,432.50" — mark.py's `<time> @ <price>` grammar,
   *  in the display format `_parse_ist` accepts. Written the way the model
   *  already sees times in the chart envelope, so a tagged coordinate needs
   *  no translation at the other end.
   *
   *  The price carries its CURRENCY. `resolve_price` strips ₹, $ and the
   *  grouping commas before it parses (including Indian grouping, since the
   *  replace is not counted), so this stays resolvable — and a bare "1432.5"
   *  in a conversation that also covers BTCUSDT is a number with no units,
   *  which is exactly the ambiguity the symbol removes. */
  const addressAt = (t, px) =>
    `${fmtIST(t, !DAILY.has(state.interval))} @ ${priceAt(px)}`;
  const whenAt = (t) => fmtIST(t, !DAILY.has(state.interval));

  /** The bar under an x, or null when the pointer is past the last one. */
  function barAtX(clientX) {
    const t = chart.timeScale().coordinateToTime(clientX - chartEl.getBoundingClientRect().left);
    return t == null ? null : (state.bars.find((b) => b.time === t) || null);
  }
  /** …and whether the pointer is actually ON it — yOnBar at the grab
   *  tolerance, the same answer the pick cursor and the pin click give.
   *  Empty chart above a candle is not that candle, and a menu that claimed
   *  it was would put the wrong bar's numbers in its header. */
  const overBar = (bar, clientY) =>
    yOnBar(yInPane(clientY, "price"), bar, 8);

  /** What the chat has drawn ON this bar, if anything — the results marker is
   *  the only kind today. Its text is the label the question then quotes, so
   *  the row cannot claim an event the chart is not showing. */
  function markerOn(barTime) {
    for (const a of (scene.state.items || [])) {
      if (a.kind !== "markers") continue;
      for (const m of (a.marks || [])) {
        if (m.t + IST === barTime) return String(m.text || "").trim() || "that event";
      }
    }
    return null;
  }
  /** Volume against the recent median — the test behind "was anyone actually
   *  trading?", and the reason that row is not offered on an ordinary bar. */
  function heavyVolume(bar) {
    if (!bar.volume) return false;
    const i = state.bars.indexOf(bar);
    if (i < 20) return false;
    const win = state.bars.slice(Math.max(0, i - 20), i)
      .map((b) => b.volume || 0).sort((a, b) => a - b);
    const med = win[Math.floor(win.length / 2)] || 0;
    return med > 0 && bar.volume > med * 2;
  }
  /** Deals and flows are NSE/BSE reporting. Offering "who was buying?" on a
   *  crypto pair would be a question with no data behind it. */
  const isEquity = () => {
    const d = Sym.of(SYMBOL);
    return !d.isCrypto && (d.venue === "NSE" || d.venue === "BSE");
  };

  /* ── the two rows every menu carries ─────────────────────────────────── */

  /* The one place on this sheet where a row is a SENTENCE, and deliberately:
   * these are the prompts themselves, carrying the chart's own numbers and
   * dates. A row that read "News that day" hid which day, and the price it
   * was about — you would be choosing a question you could not check. Here
   * you are picking a prompt rather than a command, so it is shown as the
   * prompt, and its sheet is wide enough to hold one.
   *
   * The prices come through priceAt, so the currency is the INSTRUMENT's —
   * ₹ on RELIANCE, $ on BTCUSDT — never a hardcoded rupee. */
  /* `before` runs immediately ahead of the send. The drawing menu needs it:
   * its questions say "D1" out loud, and a ref in prose only resolves to
   * exact geometry if the shape is ATTACHED — otherwise the model is back to
   * matching a name against the envelope's list, which is the guessing the
   * ref exists to end. Test drawing already did this; the questions beside it
   * did not, so they named a shape they had not handed over. */
  const askRow = (questions, before) => ({
    icon: "chat", label: "Chat", sub: questions
      .filter(Boolean).slice(0, 4)
      .map((q) => ({ label: q, wrap: true,
                     on: () => { if (before) before(); Chat.ask(q); } })),
  });

  /* ── rows shared by more than one of the three ───────────────────────── */

  const shotRow = () => ({
    icon: "camera", label: "Screenshot", sub: [
      { label: "Whole chart", hint: "⌥ S", on: () => captureChart(null) },
      { label: "Select region", on: () => selectRegionCapture() },
    ],
  });

  /** The note lands where you right-clicked, which is the difference between
   *  this and the rail's text tool: no arming, no second click. */
  const noteRow = (t, v) => ({
    icon: "pen", label: "Add note",
    title: "A text note, placed where you clicked",
    on: () => draw.noteAt("price", t, v),
  });

  const watchRow = () => ({
    icon: "listPlus", label: "Add to watchlist",
    sub: () => Panels.lists().map((l) => ({
      label: l.name,
      // Already on it: the row reports that rather than offering to add a
      // symbol twice, which the store would silently swallow anyway.
      tick: l.syms.includes(SYMBOL),
      disabled: l.syms.includes(SYMBOL),
      on: () => { Panels.watch(SYMBOL, l.id); notify(`${SYMBOL} added to ${l.name}`); },
    })),
  });

  /** Say something the reader has to SEE.
   *
   *  Not status(): #statusLine left the markup, so status() writes to nothing
   *  (setText guards on the element) and every call is a silent no-op. That is
   *  fine for narration — "5m: 4000 bars in 300ms" is not worth a toast — and
   *  not fine for a result. A copy that failed and a copy that worked must not
   *  look identical, and a symbol filed onto a watchlist that is not on screen
   *  has no other way to say it happened. The toast is the app's live channel;
   *  js/layouts.js owns it. status() stays the fallback so this is never worse
   *  than what it replaces. */
  const notify = (msg) => {
    // `typeof`, not window.Layouts: a top-level `const` in a classic script
    // binds in the global LEXICAL scope and never becomes a property of
    // window, so the property test is always false and the fallback always
    // wins — which is silence, which is the bug this exists to fix. Every
    // other cross-module reference in this file reads the bare name for the
    // same reason; layouts.js simply loads after main.js, so the typeof
    // guard is what makes an early call safe.
    if (typeof Layouts !== "undefined" && Layouts.toast) Layouts.toast(msg);
    else status(msg);
  };

  const copyText = (text, said) => {
    navigator.clipboard.writeText(text)
      .then(() => notify(said))
      .catch(() => notify("the browser would not give up the clipboard"));
  };

  /** Everything on the chart that can be removed, as the trash's own rows —
   *  one model, so the menu and the rail's trash cannot come to different
   *  answers about what "remove" means or how much of it there is. */
  const removeRow = () => {
    const live = trashLive();
    if (!live.length) return null;
    return {
      icon: "trash", label: "Remove", sub: () => {
        const now = trashLive();
        // The count leads, as it does in the rail's own trash: the number is
        // the thing being checked before the row is pressed.
        return now.map((x) => ({
          label: trashPhrase(x.l, x.n), danger: true, on: () => trashClear([x]),
        })).concat(now.length > 1
          ? [{ sep: true },
             { label: "Everything", danger: true,
               title: trashList(now.map((x) => trashPhrase(x.l, x.n))),
               on: () => trashClear(now) }]
          : []);
      },
    };
  };

  const settingsRow = () => ({
    icon: "settings", label: "Settings", on: () => ChartSettings.open(),
  });

  /* ── 1 · empty chart: a price and a moment, and nothing else ──────────── */
  function menuForPoint(px, bar) {
    const level = levelAt(px);
    const when = bar ? whenAt(bar.time) : null;
    return [
      { head: SYMBOL, note: `${priceAt(px)}${when ? ` · ${when}` : ""}` },
      { icon: "alertPlus", label: "Alert here", hint: priceAt(px),
        on: () => Alerts.open({ symbol: SYMBOL, level,
                                last: lastBar ? lastBar.close : null,
                                interval: state.interval }) },
      // The honest answer to TradingView's two order rows. We do not place
      // orders, and a sized plan with a stop and an R:R is the thing a ticket
      // assumes you already worked out. plan_position computes it; asking is
      // how it is reached, so there is no second copy of that arithmetic here.
      { icon: "position", label: "Plan a position",
        title: `Entry at ${priceAt(px)} — sized, with a stop and an R:R`,
        on: () => Chat.ask(`Plan a position on ${SYMBOL} with entry at `
          + `${priceAt(px)}.`) },
      { sep: true },
      askRow([
        `Is ${priceAt(px)} a real level on ${SYMBOL}?`,
        when && `Why did ${SYMBOL} move on ${when}?`,
        when && isEquity() && `Was there any news on ${SYMBOL} around ${when}?`,
        `Which stocks are sitting near ${priceAt(px)} the way ${SYMBOL} is?`,
      ]),
      { icon: "tag", label: "Tag point",
        title: "Puts the coordinate in the composer — you write the question",
        on: () => {
          document.dispatchEvent(new CustomEvent("charto:compose",
            { detail: bar ? addressAt(bar.time, px) : String(level) }));
          notify("point tagged — ask what you like about it");
        } },
      { sep: true },
      shotRow(),
      watchRow(),
      bar && noteRow(bar.time, px),
      { sep: true },
      { icon: "copy", label: "Copy", sub: [
        // The hint shows the price as this instrument writes it; the
        // clipboard gets the bare figure, because a copied price is on its
        // way into a field rather than into a sentence.
        { label: "Price", hint: priceAt(px), title: `Copies ${level}`,
          on: () => copyText(String(level), "price copied") },
        bar && { label: "Address", title: addressAt(bar.time, px),
                 on: () => copyText(addressAt(bar.time, px), "address copied") },
      ].filter(Boolean) },
      { sep: true },
      { icon: "rotateCw", label: "Reset view", hint: "⌥ R",
        on: () => Shortcuts.run("reset-view") },
      removeRow(),
      { icon: "bell", label: "Alerts", hint: "⌥ A", on: () => Panels.show("alerts") },
      settingsRow(),
    ];
  }

  /* ── 2 · a candle: the menu can name its own numbers ──────────────────── */
  function menuForBar(bar, px) {
    const when = whenAt(bar.time);
    const pct = bar.open ? (bar.close - bar.open) / bar.open * 100 : 0;
    const n = (v) => priceAt(v);
    const ohlc = `O ${n(bar.open)}  H ${n(bar.high)}  L ${n(bar.low)}  C ${n(bar.close)}`;
    // The header's job is "did I grab the bar I meant" — so the close and the
    // move lead at full size and the other three sit under them, quieter. All
    // four on one line wrapped mid-number at this width, which is a receipt
    // that has to be re-read to be trusted.
    const ohl = `O ${n(bar.open)}   H ${n(bar.high)}   L ${n(bar.low)}`;
    // The price rides in the HINT slot, not in the label: four rows reading
    // "High — ₹1,310.00" are four sentences where "High" and a number in a
    // column is one glance.
    const armed = (label, v) => ({
      label, hint: priceAt(v),
      on: () => Alerts.open({ symbol: SYMBOL, level: levelAt(v),
                              last: lastBar ? lastBar.close : null,
                              interval: state.interval }),
    });
    const event = markerOn(bar.time);
    const heavy = heavyVolume(bar);
    return [
      { head: when, sub2: ohl,
        note: `${priceAt(bar.close)}   ${pct >= 0 ? "+" : "−"}${Math.abs(pct).toFixed(2)}%` },
      // The bar's own four prices, exact. Every other chart makes you read a
      // wick off the axis and type what you think it said; this is the one
      // place the number is already known.
      { icon: "alertPlus", label: "Alert at bar", sub: [
        armed("High", bar.high), armed("Low", bar.low),
        armed("Open", bar.open), armed("Close", bar.close),
      ] },
      { icon: "position", label: "Plan a position",
        title: `Entry at ${priceAt(bar.close)}, stop below ${priceAt(bar.low)}`,
        on: () => Chat.ask(`Plan a position on ${SYMBOL} with entry at `
          + `${priceAt(bar.close)} and the stop below this bar's low of `
          + `${priceAt(bar.low)}.`) },
      { sep: true },
      askRow([
        `Why did ${SYMBOL} move on ${when}?`,
        event && `What did ${event} on ${when} mean for ${SYMBOL}?`,
        `What usually follows a candle like this one — ${priceAt(bar.open)} to `
          + `${priceAt(bar.close)} on ${when}?`,
        isEquity() && heavy
          && `Volume was heavy on ${when} — were there bulk or block deals?`,
        isEquity() && !heavy
          && `Was there any news on ${SYMBOL} around ${when}?`,
      ]),
      { icon: "pin", label: "Tag candle",
        title: "The same pin a plain click on the candle leaves",
        on: () => pins.toggle({ ...bar, interval: state.interval }) },
      { sep: true },
      shotRow(),
      noteRow(bar.time, px),
      { sep: true },
      { icon: "copy", label: "Copy", sub: [
        { label: "OHLC", on: () => copyText(`${SYMBOL} ${when} · ${ohlc}`, "OHLC copied") },
        { label: "Address", title: addressAt(bar.time, bar.close),
          on: () => copyText(addressAt(bar.time, bar.close), "address copied") },
      ] },
      { sep: true },
      settingsRow(),
    ];
  }

  /* ── 3 · a drawing: the shape is the subject ──────────────────────────── */
  // Which kinds enclose a stretch of chart rather than marking a line through
  // it. A profile or a "what happened inside this" question needs two times
  // to sit between, and offering it on a horizontal ray would be a row that
  // cannot be answered.
  const SPANNING = new Set(["rect", "zone", "box", "fib", "channel", "flatChannel",
                            "long", "short", "gannBox", "fibChannel"]);
  /* Shapes that assert NO PRICE. A text note sits where you put it, a
   * vertical line and a date range name a moment, and a measure is a reading
   * of two points rather than a claim about a level. None of them can be
   * crossed, and none of them has a hit rate — so those two rows are left off
   * their menus entirely rather than offered and then refused. */
  const PRICELESS = new Set(["text", "vline", "dateRange", "measure"]);
  function menuForDrawing(d) {
    const ref = d.ref || d.id;
    const spec = draw.SPECS[d.type];
    const name = spec ? spec.label : d.type;
    const priced = !PRICELESS.has(d.type);
    const tag = () => document.dispatchEvent(
      new CustomEvent("charto:draw-tag", { detail: draw.tagOf(d.id) }));
    // The chat rows tag the shape FIRST and then ask, so the question travels
    // with the ref and the tools resolve real geometry — the model is never
    // asked to work out which line "this" was.
    const askAbout = (q) => () => { tag(); Chat.ask(q); };
    return [
      { head: name, note: ref },
      // The row with no TradingView equivalent, and therefore the first one:
      // it draws anything and never says whether it meant something. This
      // answers with a hit rate against a control (evaluate_drawing).
      // A note's words are the whole drawing, so changing them is its first
      // row — the double-click does the same thing, but a menu is where you
      // look when you do not know the gesture yet.
      draw.isText(d.id) && { icon: "pen", label: "Edit text", hint: "Double-click",
        on: () => draw.editText(d.id) },
      priced && { icon: "barChart", label: "Test drawing",
        title: "Hit rate against a control, not an opinion",
        on: askAbout(`How reliable is ${ref}? Test it against a control.`) },
      // A sloping level the engine re-prices every bar: move the line and
      // what is being watched moves with it. A typed number cannot do that.
      priced && { icon: "alertPlus", label: "Alert on it", hint: ref,
        on: () => Alerts.open({ symbol: SYMBOL, left: "close", op: "cross",
                                right: `draw:${ref}`, interval: state.interval }) },
      { sep: true },
      askRow([
        priced && `Where has ${SYMBOL} respected ${ref}?`,
        SPANNING.has(d.type) && `What is the volume profile inside ${ref}?`,
        d.type === "fib" && `Which retracement of ${ref} has price actually held?`,
        `What should I watch around ${ref}?`,
      ], tag),
      { icon: "tag", label: "Tag it", hint: ref,
        title: "Attaches the shape to the composer — you write the question",
        on: () => { tag(); notify(`${ref} tagged — ask what you like about it`); } },
      { sep: true },
      shotRow(),
      { icon: "copy", label: "Duplicate", on: () => draw.clone(d.id) },
      { icon: "layers", label: "Layer", sub: () => {
        const i = draw.state.drawings.findIndex((q) => q.id === d.id);
        const last = draw.state.drawings.length - 1;
        return [
          { label: "Bring to front", disabled: i < 0 || i === last,
            on: () => draw.moveLayer(d.id, "front") },
          { label: "Bring forward", disabled: i < 0 || i === last,
            on: () => draw.moveLayer(d.id, "forward") },
          { label: "Send backward", disabled: i <= 0,
            on: () => draw.moveLayer(d.id, "backward") },
          { label: "Send to back", disabled: i <= 0,
            on: () => draw.moveLayer(d.id, "back") },
        ];
      } },
      { icon: "lock", label: "Lock", tick: !!d.locked,
        title: "A locked shape still selects and still answers — it only stops moving",
        // Nothing on the chart changes when a shape locks, so the only other
        // evidence is the tick on a menu you have just dismissed.
        on: () => notify(`${ref} ${draw.setLocked(d.id, !d.locked) ? "locked" : "unlocked"}`) },
      { sep: true },
      { icon: "trash", label: "Remove", hint: "⌫", danger: true,
        on: () => draw.remove(d.id) },
      { sep: true },
      settingsRow(),
    ];
  }

  /** The router. Order matters: a drawing sits ON TOP of the candles, so a
   *  right-click over both belongs to the shape. */
  chartEl.addEventListener("contextmenu", (e) => {
    if (draw.state.tool !== "cursor") return;   // mid-drawing: leave it alone
    if (paneAtClient(e.clientY) !== "price") return;   // prices live in pane 0
    const y = yInPane(e.clientY, "price");
    const px = y === null ? null : candle.coordinateToPrice(y);
    if (px == null || !isFinite(px)) return;
    e.preventDefault();
    if (window.__chartoCloseMenus) window.__chartoCloseMenus(null);

    // hoverId is maintained by the same hit test the grab cursor uses, so
    // "which shape is the pointer on?" is already answered — no second walk
    // over every drawing here. Deliberately NOT selId: a shape selected five
    // minutes ago is not what a right-click over empty chart is about.
    const d = draw.state.hoverId
      && draw.state.drawings.find((q) => q.id === draw.state.hoverId);
    const bar = barAtX(e.clientX);

    const items = d ? menuForDrawing(d)
      : bar && overBar(bar, e.clientY) ? menuForBar(bar, px)
      : menuForPoint(px, bar);
    Ctx.open(e.clientX, e.clientY, items);
  });

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
    // must land on the bar's high-low span (grab tolerance), so a stray click
    // in empty chart space attaches nothing to the chat. Same test the cursor
    // and the right-click use — see yOnBar.
    if (!yOnBar(yInPane(downAt[1], "price"), b, 8)) return;
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
  // its own canvases. `rect` (container px) crops it; either way the result
  // is downscaled so image tokens stay sane.
  //
  // takeScreenshot(TRUE), and the argument is the whole feature.
  //
  // Every pane in LWC has two canvases, and the compositor draws the second
  // one only when asked:
  //
  //     fv(t,i,n,s){ … t.drawImage(this.fm.canvasElement,i,n), s) {
  //         const s=this.pm.canvasElement; t.drawImage(s,i,n) } }
  //
  // `fm` is the bars; `pm` is the OVERLAY, and `s` is this argument carried
  // down from takeScreenshot. Every shape charto draws — the user's
  // drawings, get_levels' zones, get_patterns' necklines — is a SERIES
  // primitive at zOrder "top", which is precisely what lands on `pm` (see
  // the note in drawings.js syncPanes: a series primitive gets the overlay,
  // a pane primitive shares the candles' canvas). So the default call
  // returned the candles with every mark stripped off, and it did it
  // silently — a real screenshot of the wrong thing, which is why this read
  // as vision failing to see the drawings rather than as a capture bug.
  //
  // The SECOND argument stays false: that one keeps the live crosshair, and
  // a screenshot should not carry the mouse.
  /* EVERY pane, not just this one.
   *
   * The header above says "the chart (all panes)" and meant LWC's own stacked
   * panes — price, ATR, ADX, RSI — all of which live inside this file's one
   * chart instance. A workspace pane is a different thing: `open_chart` gives
   * it its OWN LWC instance under Panes, which this function had never heard
   * of. So the moment the chat opened a second instrument, the camera
   * silently returned the left half of the screen, and the reply attached to
   * it discussed a chart that was not in the picture.
   *
   * Composited by real screen rectangles, the way layouts.js already builds
   * its thumbnail — same geometry, same background fill for the gutters
   * between panes — so the two capture paths finally agree about what "the
   * chart" is. Device scale comes from the primary, the one pane whose CSS
   * size this file knows.
   */
  function paneShots() {
    const out = [[chart.takeScreenshot(true), chartEl.getBoundingClientRect()]];
    for (let i = 1; ; i++) {
      const s = Panes.paneAt(i);
      if (!s) break;
      if (s.chart && s.root) out.push([s.chart.takeScreenshot(true),
                                       s.root.getBoundingClientRect()]);
    }
    return out;
  }

  function captureChart(rect) {
    const shots = paneShots();
    const x0 = Math.min(...shots.map(([, r]) => r.left));
    const y0 = Math.min(...shots.map(([, r]) => r.top));
    const x1 = Math.max(...shots.map(([, r]) => r.right));
    const y1 = Math.max(...shots.map(([, r]) => r.bottom));
    const sx = shots[0][0].width / Math.max(1, chartEl.clientWidth);
    const sy = shots[0][0].height / Math.max(1, chartEl.clientHeight);
    const full = document.createElement("canvas");
    full.width = Math.max(1, Math.round((x1 - x0) * sx));
    full.height = Math.max(1, Math.round((y1 - y0) * sy));
    const fx = full.getContext("2d");
    fx.fillStyle = getComputedStyle(document.body)
      .getPropertyValue("--chart-bg").trim() || "#000";
    fx.fillRect(0, 0, full.width, full.height);
    for (const [cv, r] of shots) {
      fx.drawImage(cv, Math.round((r.left - x0) * sx), Math.round((r.top - y0) * sy),
                   Math.round(r.width * sx), Math.round(r.height * sy));
    }
    let c = full;
    if (rect) {
      // The marquee lives on the primary chart's element, so its coordinates
      // are relative to THAT pane — offset them into the composite before
      // cropping, or a region selected on a two-pane screen comes back
      // shifted by the primary's own origin.
      const dx = chartEl.getBoundingClientRect().left - x0;
      const dy = chartEl.getBoundingClientRect().top - y0;
      c = document.createElement("canvas");
      c.width = Math.max(1, Math.round(rect.w * sx));
      c.height = Math.max(1, Math.round(rect.h * sy));
      c.getContext("2d").drawImage(
        full, (rect.x + dx) * sx, (rect.y + dy) * sy, rect.w * sx, rect.h * sy,
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
  // a gear on a secondary pane's legend row opens the one settings dialog,
  // pointed at that pane's own indicator manager
  Panes.onSettings((id, mgr) => openIndSettings(id, mgr));
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
    Panes.apply(it.dataset.layout);   // paint + persist ride on onChange below
  });
  // Selecting a pane re-aims the WHOLE toolbar: the segmented control shows
  // that pane's interval and the indicator menu shows what that pane is
  // carrying, so neither ever claims a value it isn't driving. The legend is
  // NOT re-aimed — every pane wears its own now, which is the point. The chat
  // is told too — `charto:pane-active` is what moves its subject to the chart
  // you just clicked (unless you have pinned one yourself).
  Panes.onActive((i, iv, sym) => {
    markInterval(iv || state.interval);
    // …but only HALF the toolbar can re-aim, and that was the bug. The
    // interval pill follows the selection; the symbol pill cannot, because
    // picking a company there navigates to ?symbol= and reloads — it is a
    // statement about the SESSION, not about the selected pane. The two sit
    // side by side and read as one control, so a chat that opened DATAPATTNS
    // in a second pane left the header saying "RELIANCE · 1h": one chart's
    // ticker beside another chart's interval, a state no pane was ever in.
    // So when a secondary holds the selection the interval pill names the
    // chart it is driving, and the pair stops disagreeing. Back on the
    // primary the attribute goes away and the pill is bare, as before.
    if (i === 0 || !sym) delete ivBtn.dataset.pane;
    else ivBtn.dataset.pane = sym;
    if (menu.classList.contains("open")) renderIndMenu();
    document.dispatchEvent(new CustomEvent("charto:pane-active", {
      detail: { pane: i, symbol: sym || SYMBOL, interval: iv || state.interval },
    }));
    // a pane's symbol or interval may have moved with the selection
    document.dispatchEvent(new CustomEvent("charto:panes-changed"));
  });
  // A layout change creates and destroys charts, so anything holding a pane
  // index — the chat's chosen set above all — has to be told the screen is
  // different now.
  //
  // The trigger is repainted and the choice persisted HERE rather than beside
  // the menu click, because the menu is not the only thing that changes the
  // layout: `open_chart` grows the grid from inside Panes when the chat opens
  // a second instrument. Hung off the click alone, a chat-grown layout left
  // the button wearing the single-chart glyph beside two charts, the menu
  // ticking a row the screen was no longer in, and nothing in the store — so
  // a reload silently threw the second chart away. onChange fires for every
  // apply(), whoever called it, which is the whole point of putting it here.
  Panes.onChange(() => {
    paintLayoutBtn();
    Store.set("layout", Panes.layout);
    document.dispatchEvent(new CustomEvent("charto:panes-changed"));
  });
  Panes.apply(Store.get("layout") || "s1");
  paintLayoutBtn();

  // ── chart settings ────────────────────────────────────
  // One button, one dialog, every chart on screen: js/chartsettings.js holds
  // the model and applies each edit to whatever is registered, so the gear
  // is not aimed at the selected pane the way the indicator toolbar is.
  el("settingsBtn").innerHTML = Icons.svg("settings", "sm");
  el("settingsBtn").addEventListener("click", (e) => {
    e.stopPropagation();
    closeMenus();
    ChartSettings.open();
  });

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

  const themeBtn = document.createElement("button");
  function paintThemeBtn() {
    // show what you'd switch TO, the way macOS/Linear do it
    themeBtn.innerHTML = (Theme.mode === "dark" ? Icons.svg("sun", "xs") : Icons.svg("moon", "xs"))
      + `<span>${Theme.mode === "dark" ? "Light mode" : "Dark mode"}</span>`;
  }
  themeBtn.addEventListener("click", () => { Theme.toggle(); });
  Theme.onChange(() => {
    paintThemeBtn();
    chart.applyOptions(chartTheme());
    publishPlate();
    if (state.bars.length) {          // a toggle mid-load must not wipe series
      // retheme repaints the LINES and re-emits the legend, whose row colours
      // are baked in at render time — without that pass a name kept the other
      // theme's palette, dark goldenrod on a near-black chart.
      ind.retheme(state.bars);
    }
    // LAST, and on every chart: the line above has just written the theme's
    // palette over the colours the user chose in Settings, and this is what
    // puts an explicit choice back on top. The candles are not re-themed by
    // hand at all — they are the settings module's, with the theme as their
    // default — and the repaint it does is what recolours the volume bars.
    ChartSettings.apply();
    draw.requestUpdate();
    scene.requestUpdate();
  });
  paintThemeBtn();

  window.matchMedia(SMALL).addEventListener("change", () => {
    chart.applyOptions({
      localization: { priceFormatter: narrow() ? compactPrice : undefined },
      rightPriceScale: { minimumWidth: axisMin() },
    });
  });

  el("chatToggle").innerHTML = Icons.svg("chat", "sm") + "Chat";

  // ── account ───────────────────────────────────────────
  /* TradingView's avatar, and everything about WHO this is behind it. The
   * chart is fully usable signed out — that is the design, written down at
   * the top of js/auth.js — so the signed-out circle is not an error state
   * and does not nag: it offers the two doors and says plainly where the
   * work is being kept meanwhile.
   *
   * Auth owns the session; this owns only its picture, and re-reads it on
   * every change rather than keeping a copy. */
  const acctBtn = el("acctBtn"), acctMenu = el("acctMenu");
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const initialOf = (u) => {
    const s = String((u && (u.name || u.email)) || "").trim();
    return s ? esc(s[0].toUpperCase()) : "?";
  };

  /* The keyboard, behind the avatar — TradingView's place for it, and the
   * right one: the shortcuts sheet is not about the chart, it is about the
   * app, which is the whole reason this menu sits outside the chart's own
   * controls. Signed in or out, because a chart you can use without an
   * account is a chart you can drive from the keyboard without one. The
   * chord rides in the row's trailing slot, the way the tool flyouts
   * advertise theirs. */
  const SHORTCUT_ROW =
    `<div class="item" data-acct="shortcuts"><span class="lead">`
    + Icons.svg("keyboard", "xs") + `Keyboard shortcuts</span>`
    + `<span class="sc">Ctrl + /</span></div>`;
  const THEME_ROW = () => `<div class="item" data-acct="theme" role="switch" `
    + `aria-checked="${Theme.mode === "dark"}"><span class="lead">`
    + Icons.svg(Theme.mode === "dark" ? "moon" : "sun", "xs")
    + `<span>Dark mode</span></span><span class="switch${Theme.mode === "dark" ? " on" : ""}" aria-hidden="true"><i></i></span></div>`;

  function paintAccount(u) {
    acctBtn.classList.toggle("in", !!u);
    acctBtn.innerHTML = u ? initialOf(u) : Icons.svg("user");
    acctBtn.title = u ? `Account — ${u.name || u.email}` : "Sign in to Pivot";
    acctBtn.setAttribute("aria-label", acctBtn.title);
    acctMenu.innerHTML = u
      ? `<div class="acct-id in"><span class="disc">${initialOf(u)}</span>`
        + `<span class="who"><span class="nm">${esc(u.name || u.email)}</span>`
        + (u.name ? `<span class="em">${esc(u.email)}</span>` : "")
        + `</span></div>`
        // The one thing an account changes in this app, said rather than left
        // to be discovered.
        + `<div class="acct-note">Layouts, drawings and conversations are `
        + `saved to this account.</div>`
        + `<div class="sep"></div>`
        // The paper book, signed in only — it belongs to an account and there
        // is nothing to link a signed-out visitor to. A plain link rather
        // than a panel: it is a full page on the app's other surface, the way
        // a company page is, and pretending otherwise would mean rebuilding
        // six charts that already exist.
        + (LOCAL_DEV                                  // see the header button
          ? `<div class="item" data-acct="paper"><span class="lead">`
            + Icons.svg("paperBook", "xs") + `Paper book</span></div>`
          : "")
        + THEME_ROW()
        + SHORTCUT_ROW
        + `<div class="sep"></div>`
        + `<div class="item" data-acct="logout"><span class="lead">`
        + Icons.svg("logOut", "xs") + `Sign out</span></div>`
      : `<div class="acct-id"><span class="disc">${Icons.svg("user")}</span>`
        + `<span class="who"><span class="nm">Not signed in</span>`
        + `<span class="em">Working in this browser</span></span></div>`
        + `<div class="item" data-acct="login"><span class="lead">Sign in</span></div>`
        + `<div class="item" data-acct="signup"><span class="lead">Create an account</span></div>`
        + `<div class="sep"></div>`
        + THEME_ROW()
        + SHORTCUT_ROW
        + `<div class="sep"></div>`
        + `<div class="acct-note">Your charts, drawings and chats stay in this `
        + `browser until you sign in.</div>`;
  }

  /* The paper book, one click from the chart.
   *
   * Deliberately visible SIGNED OUT too. The book itself needs an account —
   * a portfolio that dies with localStorage is worse than none — but hiding
   * the control means the only people who discover paper trading are the ones
   * who already knew to look in a menu. So it shows, and a signed-out press
   * opens the sign-in it actually needs rather than a page whose whole content
   * is an apology.
   *
   * A new TAB, not a navigation: leaving the chart would drop the live feed,
   * the drawing layer and the conversation, and the book is something you
   * glance at beside the chart rather than instead of it. */
  /* …and NOT on the deployed box, where the book is not ready to be found.
   *
   * Removed rather than disabled, and removed from the account menu with it:
   * a door is either there or it is not, and half of one — a glyph that
   * explains why it cannot be opened — is worse than a header that never
   * promised the room. The whole feature stays in the build and comes back
   * the moment the box can serve it; this is the one line that decides. */
  const paperBtn = el("paperBtn");
  if (paperBtn && !LOCAL_DEV) paperBtn.remove();
  else if (paperBtn) {
    paperBtn.innerHTML = Icons.svg("paperBook");
    /* COMPANY_PAGE, for the reason written where it is defined — and this is
     * the second time that reason has had to be learned. An absolute
     * http://127.0.0.1:5175/paper is a different ORIGIN, and localStorage is
     * per origin: the token the chart signed you in with does not exist over
     * there, so a signed-in user arrived at the paper book signed OUT and was
     * shown a page asking them to sign in. serve.py proxies /paper to the
     * Next app in dev exactly as nginx does on the VM. */
    const openPaper = () => {
      if (!Auth.user) return window.CHARTO_AUTH_OPEN("login");
      window.open(COMPANY_PAGE + "/paper", "_blank", "noopener");
    };
    paperBtn.addEventListener("click", openPaper);
    // The title carries the state, so a signed-out press is not a surprise.
    const paintPaper = (u) => {
      paperBtn.title = u
        ? "Paper book — your simulated portfolio"
        : "Paper book — sign in to open your simulated portfolio";
    };
    Auth.onChange(paintPaper);
    paintPaper(Auth.user);
  }

  acctBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    closeMenus(acctMenu);
    acctMenu.classList.toggle("open");
    syncMenuTriggers();
  });
  acctMenu.addEventListener("click", async (e) => {
    const it = e.target.closest("[data-acct]");
    if (!it) return;
    closeMenus(null);
    if (it.dataset.acct === "theme") { Theme.toggle(); paintAccount(Auth.user); return; }
    if (it.dataset.acct === "shortcuts") return Shortcuts.open();
    if (it.dataset.acct === "paper") {
      window.open(COMPANY_PAGE + "/paper", "_blank", "noopener");  // see openPaper
      return;
    }
    if (it.dataset.acct === "logout") {
      await Auth.logout();
      // Signing out changes WHOSE work this is, and the modules holding that
      // work are already running — the same reason a sign-in reloads.
      return location.reload();
    }
    window.CHARTO_AUTH_OPEN(it.dataset.acct === "signup" ? "signup" : "login");
  });
  Auth.onChange(paintAccount);
  paintAccount(Auth.user);   // onChange only fires on a CHANGE; paint the rest

  // A reload refreshes the PRICE, not the session: re-fetch bars (which lands
  // the view back at the live edge) and put back everything the user built.
  // Drawings restore themselves — drawings.js reads its own store at create().
  (async function boot() {
    await Indicators.loadCatalogue(API, SYMBOL);
    ind.setContext({ interval: Store.get("interval", "5m") });
    renderIndMenu();
    // Read the restore list BEFORE the first load: saveIndicators() runs on
    // every indicator change, and at boot that would fire against an empty
    // active map and write the session away before it had been read back.
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
      // never swallow: a restore that fails silently leaves a legend row on
      // screen with no series under it, which looks like a render bug
      await Promise.resolve(ind.toggle(rid, state.bars))
        .catch((err) => { console.error("[charto] indicator restore failed", rid, err); });
    }
    saveIndicators();
    // panes created during restore need every layer's primitives attached —
    // the same signal a live indicator change sends
    document.dispatchEvent(new CustomEvent("charto:indicators-changed"));

    /* Labels were baked into the annotation at draw time, so a shape drawn
     * before the format changed keeps the old words for as long as it stays
     * on the chart — "rounding bottom · neckline 1,292.74 · forming" sitting
     * beside a freshly drawn "Double Top · Moderate", with no way for the
     * reader to make the first one look like the second short of erasing it.
     *
     * So the stored scene is normalised on the way in: the measurement goes
     * (it is the line the label is attached to), and the name is set the way
     * the page sets it now.
     *
     * What does NOT happen is inventing a strength for them. The graded word
     * is computed from the pooled ledger, the formation's symmetry and its
     * span — none of which survives on a stored annotation — and mapping the
     * old "confirmed" onto "Strong" is exactly the equivalence the grading
     * exists to break. A legacy shape carries its name alone until it is
     * drawn again, which is honest and takes one call.
     */
    const OLD_LABEL = /^(.+?)\s+·\s+(?:neckline|width|pole)\s+[\d,.]+(?:\s+·\s+\w+)?$/i;
    const OLD_STATE = /^(.+?)\s+·\s+(?:confirmed|unconfirmed|forming|unresolved|not_assessed)$/i;
    const titleCase = (t) => t.split(/\s+/).filter(Boolean)
      .map((w) => w.charAt(0).toUpperCase() + w.slice(1)).join(" ");
    function normaliseLabels(items) {
      for (const a of items || []) {
        if (!a || typeof a.label !== "string") continue;
        const m = OLD_LABEL.exec(a.label) || OLD_STATE.exec(a.label);
        const base = m ? m[1] : a.label;
        // Only when every part is a bare NAME. One bar often carries two
        // ("bullish engulfing · tweezer bottom") and both should be set the
        // same way; a part with a digit in it is a measurement or a count
        // ("Strategy · 58 still held") and belongs to another tool entirely.
        // Anything already in the new shape title-cases to itself.
        const parts = base.split(" · ");
        if (parts.every((x) => !/\d/.test(x))) {
          a.label = parts.map(titleCase).join(" · ");
        } else if (m) {
          a.label = titleCase(base);
        }
      }
      return items;
    }

    const levels = normaliseLabels(Store.get("scene", []));
    if (levels.length) {
      scene.apply(levels);
      // boot-time loadInterval ran before the scene was restored, so its
      // coverage check saw an empty scene — run it again now
      coverScene();

      // the indicators-changed dispatch above ran BEFORE this restore,
      // so the orphan purge saw an empty scene — signal once more now
      document.dispatchEvent(new CustomEvent("charto:indicators-changed"));
    }

    // A profile is RE-FETCHED rather than restored from the saved scene: a
    // session that ran since the last visit changes what "20 sessions" means,
    // and a stale histogram under a fresh tick is the one failure a persisted
    // overlay can hide. Runs after the scene restore so it replaces, not
    // duplicates, whatever that put back.
    const vp = Store.get("vp", null);
    if (vp) {
      await setVolumeProfile(Number(vp))
        .catch((err) => console.error("[charto] volume profile restore", err));
    }

    /* The restored session is the STARTING POSITION, not a move — so the undo
     * stack opens here, with everything already put back and both buttons
     * greyed. Bound any earlier and a freshly opened tab could "undo" its own
     * restore, walking a chart the user had built up back to empty.
     *
     * The three sets below are the whole of what undo covers; `vp` rides with
     * them because the profile's MENU TICK is a claim about a scene item, and
     * putting the histogram back without the tick would leave the menu lying
     * about what is on screen. */
    /* The workspace, as one readable/writable value.
     *
     * Undo has always defined it — these four sets ARE what an edit edits —
     * and a saved layout is the same snapshot kept under a name instead of on
     * a stack. So layouts.js reuses this pair rather than growing a second
     * opinion about what a workspace is; anything undo learns to cover, a
     * layout stores for free. */
    const wsRead = () => ({
      drawings: draw.state.drawings,
      scene: scene.state.items,
      indicators: [...ind.active.keys()],
      vp: state.vp || null,
    });
    const wsWrite = (async (s) => {
        draw.setAll(s.drawings);
        scene.setItems(s.scene);          // fires onChange → may null state.vp
        // Indicators are a SET, restored by difference: dropping and re-adding
        // every study would tear down panes the survivors are drawn on.
        const want = new Set(s.indicators || []);
        for (const id of [...ind.active.keys()]) if (!want.has(id)) ind.remove(id);
        for (const id of s.indicators || []) {
          // dynamic defs (rsi26) don't survive as objects — re-mint from the id,
          // the same way the boot restore above does
          const rid = ind.ensureFromId(id);
          if (!rid || ind.isActive(rid)) continue;
          await Promise.resolve(ind.toggle(rid, state.bars))
            .catch((err) => console.warn("[charto] undo: indicator", rid, err));
        }
        saveIndicators();
        // after scene.setItems, so the tick describes the histogram that is
        // actually on the chart rather than the one that just left it
        state.vp = s.vp || null;
        Store.set("vp", state.vp);
        renderIndMenu();
        document.dispatchEvent(new CustomEvent("charto:indicators-changed"));
    });
    Undo.bind({ read: wsRead, write: wsWrite });
    workspace = { read: wsRead, write: wsWrite };
    document.dispatchEvent(new CustomEvent("charto:workspace-ready"));

    /* The fold is restored LAST — after the scene, the profile and the undo
     * bind, so `sceneCount` starts from what is actually on the chart and the
     * "a new annotation un-folds" rule opens on a true baseline rather than
     * on zero. Only now does that rule start listening. */
    sceneCount = scene.count();
    drawBooted = true;
    applyDrawCollapsed();
    syncDrawToggle();

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
    setText("srcLine", `local store · ${Sym.feed}`);
    paintTitle();
    // PIVOT, which is what the header lockup, the static <title> and the
    // company page have all said for a while. "Charto" is the repository's
    // name for this surface, not the product's, and the tab was the last
    // place it was still leaking out to a reader.
    document.title = `${SYMBOL} — Pivot`;
    const pill = el("symbolPill"), menu = el("symbolMenu");
    const input = el("symSearch"), list = el("symList");
    let all = null, hyd = new Set(), names = {}, shortNames = {}, logos = {};
    /** The instrument's own mark, on the pill. It sits BEFORE the ticker, the
     *  same order the search rows and the chat's tables use — one instrument,
     *  one mark, in one position wherever it is named. */
    Universe.load().then(() => {
      const src = Universe.logo(SYMBOL);
      if (!src || pill.querySelector(".co-logo")) return;
      const img = document.createElement("img");
      img.className = "co-logo"; img.src = src; img.alt = ""; img.loading = "lazy";
      img.onerror = () => img.remove();
      pill.insertBefore(img, pill.firstChild);
    });
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
        // SAME TAB. The href has been same-origin for a while — `/stock/X` is
        // proxied to the company app by serve.py in dev and nginx on the VM —
        // but `target="_blank"` was still treating it as somewhere else,
        // spawning a second tab of the same site for what is a route on it.
        // A subpage you can come back from with the back button is the whole
        // point of having put it on this origin.
        `<a class="open-co" href="${COMPANY_PAGE}/stock/${encodeURIComponent(s)}?theme=${document.documentElement.getAttribute("data-theme") || "dark"}"
            title="${s} — open company page"
            aria-label="${s} — open company page">${Icons.svg("externalLink", "sm")}</a>` +
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
        // one cache for the whole app — the legend, the pane pickers and the
        // chat's logo marker all read the same payload
        const d = await Universe.load();
        all = d.symbols; hyd = d.hydrated;
        // show the enrichment long name (the Moneycontrol short name is
        // wrong for a few rows); still search both
        names = d.names; shortNames = d.short; logos = d.logos;
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

  // ── the keyboard ──────────────────────────────────────
  /* js/shortcuts.js owns WHICH key — one catalogue, printed on the sheet and
   * parsed by the dispatcher, so the two cannot drift. This owns what each
   * verb MEANS, which is the only half of it this file is entitled to an
   * opinion about.
   *
   * Most of these go through the control the mouse would have used rather
   * than the function behind it. A shortcut that calls captureChart() past
   * the camera button is a second code path to keep in step with the first;
   * a shortcut that CLICKS the camera button is the same path, so the menu
   * closes, the toggled state paints and the status line says what it always
   * said. Where there is no button — invert — the work is here. Reset has one
   * now, and it goes the other way round: the button fires this verb, so the
   * key, the right-click row and the button are one path.
   */
  (function bindShortcuts() {
    /** The chart the one toolbar is aimed at: the selected secondary pane,
     *  or the primary when none is. Same rule the interval pill follows —
     *  a keyboard that acted on pane 1 while the reader was working in pane
     *  3 would be a shortcut for the wrong chart. */
    const aimed = () => {
      const s = Panes.activeSub();
      return s ? s.chart : chart;
    };

    Shortcuts.on("tool", (id) => { closeToolMenus(); armTool(id); });
    Shortcuts.on("magnet", () => el("tool-magnet").click());
    Shortcuts.on("undo", () => Undo.undo());
    Shortcuts.on("redo", () => Undo.redo());
    Shortcuts.on("fold", () => toggleDrawFold());
    Shortcuts.on("snapshot", () => { closeMenus(null); captureChart(null); });
    Shortcuts.on("chat", () => el("chatToggle").click());
    Shortcuts.on("watchlist", () => Panels.toggle("watch"));
    Shortcuts.on("alerts", () => Panels.toggle("alerts"));

    // The picker opens and takes the typing from there — the letter that
    // summoned it is deliberately not seeded into the field (see the note
    // in js/shortcuts.js).
    Shortcuts.on("symbol", () => { closeMenus(null); el("symbolPill").click(); });

    /* The quick-entry hands over an interval id and reads the answer: false
     * means "this chart cannot fetch that", and the box stays up saying so
     * rather than the chart quietly landing on something else. The row is
     * the same one the pill's menu offers, so the pane routing, the tick and
     * the chat's subject chip all move exactly as they do on a click. */
    Shortcuts.on("interval", (iv) => {
      const row = ivMenu.querySelector(`[data-iv="${iv}"]`);
      if (!row) return false;
      row.click();
      return true;
    });

    /* Alt+R — back to the default zoom, at the live edge, with the price
     * scale free again. Three separate things a chart drifts away from, and
     * a "reset" that fixed only the first would leave the reader hunting the
     * other two. resetView() up in this file is the one definition of where
     * "default" is; this used to call the library's resetTimeScale(), which
     * (measured) restores the scroll and leaves the zoom alone.
     *
     * The argument is the chart to reset, for a caller that knows — the
     * button on the primary's stage means the primary, whatever pane the
     * toolbar happens to be aimed at. Without one it falls back to the aimed
     * pane, which is what a keystroke means.
     *
     * No status line on this one or the next, for the reason undo already
     * gives a few hundred lines up: they SHOW their result. A message saying
     * "view reset" beside a chart that visibly reset is narration. */
    Shortcuts.on("reset-view", (t) => {
      const sub = Panes.activeSub();
      if (t === chart || !sub) return resetView(chart, state.bars.length);
      resetView(t || sub.chart, (sub.bars || []).length);
    });

    /* Alt+I — the price scale upside down, which is how a trader looks at a
     * short. Read back off the scale rather than tracked here: the chart is
     * the one that knows, and a flag in this file would be wrong the first
     * time anything else touched the option. */
    Shortcuts.on("invert", () => {
      const ps = aimed().priceScale("right");
      ps.applyOptions({ invertScale: !ps.options().invertScale });
    });
  })();

  window.__charto = { chart, candle, state, draw, ind, scene, pins,
                      getChartContext, charts: chartList, panes: Panes,
                      /* The trash's model, so the phone's sheet offers the same
                       * choices the rail's menu does rather than a "clear all"
                       * that means something narrower. One definition of what a
                       * layer is and what clearing it does; two ways in. */
                      objects: {
                        live: () => trashLive().map((x) => ({
                          key: x.l.key, n: x.n,
                          label: `Remove ${trashPhrase(x.l, x.n)}`,
                          icon: x.l.icon,
                        })),
                        clear(keys) {
                          const want = new Set([].concat(keys));
                          trashClear(trashLive().filter((x) => want.has(x.l.key)));
                        },
                      },
                      // set once the boot restore has finished — layouts.js
                      // waits on charto:workspace-ready rather than polling
                      get workspace() { return workspace; },
                      get symbol() { return SYMBOL; },
                      get interval() { return state.interval; },
                      loadInterval };
})();
