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

  const chart = LWC.createChart(chartEl, {
    ...T0,
    localization: { ...(T0.localization || {}), ...priceLocale() },
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
      chart.applyOptions({ timeScale: { timeVisible: !["1d", "1w", "1mo"].includes(interval) } });
      paintTitle();
      paint();
      // fresh data, fresh view: hand the price scale back to auto-fit
      eachPriceScale((ps) => ps.applyOptions({ autoScale: true }));
      paintAutoPill();
      chart.timeScale().setVisibleLogicalRange({ from: bars.length - 180, to: bars.length + 6 });
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
      + `<span class="sep">·</span>${state.interval}`
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
  chart.subscribeCrosshairMove((p) => {
    const b = p && p.seriesData ? p.seriesData.get(candle) : null;
    if (b) {
      const src = state.bars[state.bars.length - 1];
      paintReadout({ ...b, volume: (p.seriesData.get(volume) || {}).value ?? src.volume });
    } else paintReadout(lastBar);
  });

  function status(msg) { setText("statusLine", msg); }
  function setOverlay(show, text, isErr) {
    el("overlayText").innerHTML = isErr ? `<span class="err">${text}</span>` : (text || "");
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
    ["Days", [["1d", "D", "1 day"], ["1w", "W", "1 week"], ["1mo", "M", "1 month"]]],
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
    fib: "fib", rect: "rect", triangle: "triangle", brush: "brush",
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
        `<button class="tool has-group" id="group-${g.id}" data-group-btn="${g.id}">` +
        `${Icons.svg(g.icon)}<span class="tip">${g.label}</span></button>` +
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
    `<button class="tool" id="tool-trash" data-tool="trash" data-kind="action">` +
    `${Icons.svg("trash")}<span class="tip">Clear all drawings</span></button>`);

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
      btn.innerHTML = Icons.svg(icon) + `<span class="tip">${spec.label}</span>`;
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
    setText("drawStatus", spec
      ? `${spec.label} — ${spec.anchors === "free" ? "drag to draw"
          : `click ${spec.anchors} point${spec.anchors > 1 ? "s" : ""}`}`
      : "");
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
        setText("drawStatus", `click trash again to clear ${draw.count()} drawings`);
        setTimeout(() => { trashArmed = false; }, 3000);
      } else {
        draw.clearAll();
        trashArmed = false;
        setText("drawStatus", "all drawings cleared");
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
        indicators: sub.ind.snapshot(w.first.time).map((x) => ({
          label: x.label, now: r2(x.now),
          at_window_start: x.at === null ? null : r2(x.at),
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
      ...w0.env,
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
  let drawCollapsed = !!Store.get("draw_collapsed", false);
  // The restore is not an edit. Boot puts the saved scene back one apply at a
  // time, and every one of those looks exactly like the chat drawing — so the
  // "a new annotation un-folds" rule stays off until the session is standing
  // up, or a chart saved folded would open unfolded every time.
  let drawBooted = false;
  let sceneCount = 0;

  /** What the control should say right now, or null when there is nothing to
   *  fold. Read by scene.js on every chip repaint. */
  function drawFoldState() {
    const n = draw.count() + scene.count();
    return n ? { n, collapsed: drawCollapsed } : null;
  }

  /** Repaint the control after something OTHER than a scene change — a shape
   *  placed, dragged or deleted on the drawing layer, which scene.js has no
   *  way to hear about but which its count includes. */
  function syncDrawToggle() {
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

  function toggleDrawFold() {
    drawCollapsed = !drawCollapsed;
    Store.set("draw_collapsed", drawCollapsed);
    applyDrawCollapsed();
  }

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
        if (m) return row(cap(m[1]), val(null, m[2]));
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
    Panes.apply(it.dataset.layout);
    paintLayoutBtn();
    Store.set("layout", Panes.layout);
  });
  // Selecting a pane re-aims the WHOLE toolbar: the segmented control shows
  // that pane's interval and the indicator menu shows what that pane is
  // carrying, so neither ever claims a value it isn't driving. The legend is
  // NOT re-aimed — every pane wears its own now, which is the point. The chat
  // is told too — `charto:pane-active` is what moves its subject to the chart
  // you just clicked (unless you have pinned one yourself).
  Panes.onActive((i, iv, sym) => {
    markInterval(iv || state.interval);
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
  Panes.onChange(() => document.dispatchEvent(new CustomEvent("charto:panes-changed")));
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
    chart.applyOptions({ localization: { priceFormatter: narrow() ? compactPrice : undefined } });
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

  function paintAccount(u) {
    acctBtn.classList.toggle("in", !!u);
    acctBtn.innerHTML = u ? initialOf(u) : Icons.svg("user");
    acctBtn.title = u ? `Account — ${u.name || u.email}` : "Sign in to Charto";
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
        + SHORTCUT_ROW
        + `<div class="sep"></div>`
        + `<div class="acct-note">Your charts, drawings and chats stay in this `
        + `browser until you sign in.</div>`;
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
    if (it.dataset.acct === "shortcuts") return Shortcuts.open();
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
    Undo.bind({
      read: () => ({
        drawings: draw.state.drawings,
        scene: scene.state.items,
        indicators: [...ind.active.keys()],
        vp: state.vp || null,
      }),
      write: async (s) => {
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
      },
    });

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
    document.title = `${SYMBOL} — Charto`;
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
        `<a class="open-co" target="_blank" rel="noopener" href="${COMPANY_PAGE}/stock/${encodeURIComponent(s)}?theme=${document.documentElement.getAttribute("data-theme") || "dark"}"
            title="${s} — open company page in a new tab"
            aria-label="${s} — open company page in a new tab">${Icons.svg("externalLink", "sm")}</a>` +
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
   * said. Where there is no button — reset, invert — the work is here.
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
     * other two.
     *
     * No status line on this one or the next, for the reason undo already
     * gives a few hundred lines up: they SHOW their result. A message saying
     * "view reset" beside a chart that visibly reset is narration. */
    Shortcuts.on("reset-view", () => {
      const t = aimed();
      t.timeScale().resetTimeScale();
      t.timeScale().scrollToRealTime();
      try { t.priceScale("right").applyOptions({ autoScale: true }); } catch {}
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
                      getChartContext, charts: chartList, panes: Panes };
})();
