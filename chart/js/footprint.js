/* Charto preview — the Footprint chart type.
 *
 * Chosen from the chart-type menu like Candles or Bars. The price series stays
 * a real candlestick series (painted transparent, see chartsettings.js), so the
 * price scale, crosshair, drawings, indicators and the scene keep working
 * unchanged. This file draws ON that series, as a series primitive: a thin
 * candle at the left of every bar and, inside the bar, the volume that traded
 * at each price split by aggressor — sold into the bid on the left, bought
 * from the offer on the right.
 *
 * Every figure comes from the dataserver's /footprint (footprint.py): the
 * rows, delta, POC, value area, diagonal imbalances and stacked-imbalance
 * zones are computed there. This file only chooses the row size from the
 * screen (so a row is tall enough to read), fetches, and paints.
 *
 * Crypto only, by construction: Indian feeds carry no aggressor side, so for
 * an Indian symbol the chart draws plain candles and the bar says why.
 */
"use strict";

const Footprint = (() => {
  const API = location.port === "5173" ? "http://127.0.0.1:5174" : "";
  const POLL_MS = 2000;
  const PREF = "charto:footprint";

  let env = null;                 // what main.js hands over (mount)
  let series = null, prim = null, requestUpdate = () => {};
  let active = false, timer = 0, busy = false, gen = 0;
  let cache = new Map();          // raw bar ts -> footprint bar
  let meta = null;                // last answer's {row, step, source, available, reason}
  let rowWanted = 0;              // the row size the cache was fetched at (0 = server auto)
  let autoRow = 0;                // the server's data-sized row for this symbol and interval
  let oldest = null, hasMore = false, loadingOlder = false;
  let savedSpacing = null;
  let bar = null, tip = null;
  const pref = load();

  function load() {
    try { return Object.assign({ mode: "bidask", ratio: 3, rowMul: 1, summary: true }, JSON.parse(localStorage.getItem(PREF) || "{}")); }
    catch { return { mode: "bidask", ratio: 3, rowMul: 1, summary: true }; }
  }
  function save() { try { localStorage.setItem(PREF, JSON.stringify(pref)); } catch { /* private mode */ } }

  // ── colours, read from the theme so light and dark both hold ──
  function palette() {
    const cs = getComputedStyle(env.host);
    const v = (n, d) => (cs.getPropertyValue(n) || "").trim() || d;
    return {
      up: v("--candle-up", v("--up", "#089981")), down: v("--candle-down", v("--down", "#f23645")),
      fg: v("--foreground", "#e6e6e6"), muted: v("--muted-foreground", "#8f98a1"),
      faint: v("--faint", "#6b7280"), card: v("--card", "#1c1c1d"), bg: v("--background", "#0d0d0e"),
      font: v("--mono", "ui-monospace, SFMono-Regular, Menlo, monospace"),
    };
  }
  const rgba = (hex, a) => {
    const m = /^#?([0-9a-f]{6})$/i.exec(hex.replace(/\s/g, ""));
    if (!m) return hex;
    const n = parseInt(m[1], 16);
    return `rgba(${n >> 16},${(n >> 8) & 255},${n & 255},${a})`;
  };

  // ── numbers ──
  function qty(v) {
    if (!v) return "";
    const a = Math.abs(v);
    if (a >= 1e6) return (v / 1e6).toFixed(2) + " M";
    if (a >= 1e3) return (v / 1e3).toFixed(2) + " K";
    if (a >= 100) return v.toFixed(1);
    if (a >= 1) return v.toFixed(2);
    if (a >= 0.001) return v.toFixed(3);
    return v.toPrecision(2);
  }
  /** One precision for every cell on screen, from the busiest row: columns
   *  of figures line up and a dust print reads 0.000 instead of 0.000014. */
  function cellFmt(max) {
    const dec = max >= 1e4 ? -1 : max >= 100 ? 1 : max >= 10 ? 2 : 3;
    return (v) => {
      if (dec < 0) return qty(v) || "0";
      const t = (v || 0).toFixed(dec);
      return Number(t) === 0 ? (0).toFixed(dec) : t;
    };
  }
  const signed = (v) => (v > 0 ? "+" : v < 0 ? "−" : "") + qty(Math.abs(v));
  function priceText(p) {
    const step = meta && meta.row ? meta.row : 1;
    const dp = Math.max(0, Math.min(8, -Math.floor(Math.log10(step) + 1e-9)));
    return p.toLocaleString("en-IN", { minimumFractionDigits: dp, maximumFractionDigits: dp });
  }

  // ── row size from the screen ──
  function nice(x) {
    if (!(x > 0)) return 0;
    const e = Math.floor(Math.log10(x)), b = 10 ** e;
    for (const m of [1, 2, 5, 10]) if (m * b >= x * (1 - 1e-9)) return +(m * b).toPrecision(12);
    return 10 * b;
  }
  // ── data ──
  async function fetchWindow(to) {
    const sym = env.symbol(), iv = env.interval();
    const row = rowWanted || 0;
    const qs = new URLSearchParams({ symbol: sym, interval: iv, bars: "200", ratio: String(pref.ratio) });
    if (row) qs.set("row", String(row));
    if (to) qs.set("to", String(to));
    const r = await Net.get(`${API}/footprint?${qs}`);
    return r.json();
  }

  function absorb(d, replaceNewest) {
    meta = { available: !!d.available, reason: d.reason || "", row: d.row, step: d.step,
             source: d.source, interval: d.interval, symbol: d.symbol, ratio: d.ratio, first: d.first };
    if (!d.available) { cache.clear(); return; }
    if (!rowWanted) {
      // first answer is the server's own row; step it if the reader chose to
      autoRow = d.row;
      rowWanted = d.row;
      const want = rowFor(pref.rowMul || 1);
      if (want && Math.abs(want - d.row) > d.row * 1e-6) { setTimeout(() => refetchAt(want), 0); return; }
    }
    for (const b of d.bars || []) cache.set(b.t, b);
    const ts = (d.bars || []).map((b) => b.t);
    if (ts.length && (oldest == null || ts[0] < oldest)) { oldest = ts[0]; hasMore = !!d.has_more; }
    if (replaceNewest) hasMore = hasMore || !!d.has_more;
  }

  /** The poll skips a hidden tab; a first load or an explicit change does not. */
  async function refresh(force) {
    if (!active || busy || (!force && document.visibilityState !== "visible")) return;
    busy = true;
    const g = gen;
    try {
      const d = await fetchWindow(null);
      if (g !== gen || !active) return;
      absorb(d, true);
      paintBar();
      requestUpdate();
    } catch (e) {
      if (g === gen) { meta = { available: false, reason: "The data server did not answer." }; paintBar(); }
    } finally { busy = false; }
  }

  async function older() {
    if (!active || loadingOlder || !hasMore || oldest == null) return;
    loadingOlder = true;
    const g = gen;
    try {
      const d = await fetchWindow(oldest);
      if (g !== gen) return;
      const before = oldest;
      absorb(d, false);
      if (oldest === before) hasMore = false;
      requestUpdate();
    } catch { /* the next pan asks again */ } finally { loadingOlder = false; }
  }

  function reset() {
    gen++;
    cache = new Map(); oldest = null; hasMore = false; meta = null;
    rowWanted = 0; autoRow = 0;
  }

  // ── the series primitive ──
  function makePrimitive() {
    const renderer = { draw(target) { target.useMediaCoordinateSpace(({ context, mediaSize }) => draw(context, mediaSize)); } };
    const view = { zOrder: () => "normal", renderer: () => renderer };
    return {
      attached(p) { requestUpdate = p.requestUpdate || (() => {}); },
      detached() { requestUpdate = () => {}; },
      updateAllViews() {},
      paneViews: () => [view],
    };
  }

  // TradingView's layout, which traders already read without a legend: sold
  // into the bid in boxes LEFT of the candle, bought from the offer RIGHT of
  // it, one box per price row with a pale→deep fill by volume, the POC row
  // inverted, VAH/VAL dotted with a small marker, imbalances as a tick on the
  // outer edge, side totals under each column and a Delta/Total card under
  // the bar.
  function isDark(P) {
    const m = /^#?([0-9a-f]{6})$/i.exec((P.bg || "").replace(/\s/g, ""));
    if (!m) return true;
    const n = parseInt(m[1], 16);
    return (0.299 * (n >> 16) + 0.587 * ((n >> 8) & 255) + 0.114 * (n & 255)) < 128;
  }
  function box(ctx, x, y, w, h, r) {
    if (w <= 0 || h <= 0) return;
    ctx.beginPath();
    if (ctx.roundRect) ctx.roundRect(x, y, w, h, Math.min(r, h / 2, w / 2)); else ctx.rect(x, y, w, h);
    ctx.fill();
  }

  function draw(ctx, size) {
    if (!active || !series || !env) return;
    const P = palette();
    const dark = isDark(P);
    const ts = env.chart.timeScale();
    const spacing = ts.options().barSpacing;
    const data = series.data ? series.data() : [];
    if (!data.length) return;
    const W = size.width, H = size.height;
    const vis = [];
    for (const d of data) {
      const x = ts.timeToCoordinate(d.time);
      if (x == null || x < -spacing || x > W + spacing) continue;
      vis.push({ d, x, fp: cache.get(env.fromChart(d.time)) });
    }
    if (!vis.length) return;
    if (meta && meta.available && hasMore && vis[0].fp == null && oldest != null
        && env.fromChart(vis[0].d.time) < oldest) older();
    const fpOn = !!(meta && meta.available && vis.some((v) => v.fp && v.fp.rows && v.fp.rows.length));

    // ── the candle: centred, the same body every footprint column frames
    const cw = fpOn ? Math.max(4, Math.min(12, Math.round(spacing * 0.1))) : Math.max(1, spacing * 0.7);
    const single = pref.mode !== "bidask";
    for (const v of vis) {
      const { d } = v;
      const col = d.close >= d.open ? P.up : P.down;
      const yH = series.priceToCoordinate(d.high), yL = series.priceToCoordinate(d.low);
      const yO = series.priceToCoordinate(d.open), yC = series.priceToCoordinate(d.close);
      if (yH == null || yL == null) continue;
      // in a one-column mode the candle sits at the left and the cells take the rest
      const cx = fpOn && single && v.fp && v.fp.rows && v.fp.rows.length ? v.x - spacing * 0.46 + cw / 2 : v.x;
      ctx.strokeStyle = col; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(Math.round(cx) + 0.5, yH); ctx.lineTo(Math.round(cx) + 0.5, yL); ctx.stroke();
      ctx.fillStyle = col;
      ctx.fillRect(Math.round(cx - cw / 2), Math.min(yO, yC), Math.max(1, Math.round(cw)), Math.max(1, Math.abs(yC - yO)));
    }
    if (!fpOn) return;

    const row = meta.row;
    let vmax = 0, dmax = 0, tmax = 0;
    for (const v of vis) for (const r of (v.fp && v.fp.rows) || []) {
      vmax = Math.max(vmax, r[1], r[2]); dmax = Math.max(dmax, Math.abs(r[2] - r[1])); tmax = Math.max(tmax, r[1] + r[2]);
    }
    // pale → deep, in the side's own hue; dark themes start from the hue at low
    // alpha, light themes from a tint, so both read like TradingView's
    const fmtV = cellFmt(vmax), fmtD = cellFmt(dmax), fmtT = cellFmt(tmax);
    const shade = (hue, x) => rgba(hue, (dark ? 0.16 : 0.22) + (dark ? 0.72 : 0.7) * Math.min(1, Math.pow(x, 0.75)));
    const ink = (x) => (x > 0.55 ? "#ffffff" : (dark ? rgba(P.fg, 0.92) : "#131722"));
    const gap = 2;                                   // between the candle and each column
    ctx.textBaseline = "middle";

    for (const v of vis) {
      const fp = v.fp;
      if (!fp || !fp.rows || !fp.rows.length) continue;
      const half = spacing * 0.46;
      const xL = v.x - half, xR = v.x + half;
      // column geometry
      let sL, sR, bL, bR;
      if (single) { bL = xL + cw + gap + 1; bR = xR; sL = sR = null; }
      else { sL = xL; sR = v.x - cw / 2 - gap; bL = v.x + cw / 2 + gap; bR = xR; }
      const colW = bR - bL;
      const textOK = colW >= 34;
      let lowest = -Infinity;

      for (const [p, sell, buy, imb] of fp.rows) {
        const yT = series.priceToCoordinate(p + row), yB = series.priceToCoordinate(p);
        if (yT == null || yB == null || yB < -20 || yT > H + 20) continue;
        const h = yB - yT;
        if (h < 1) continue;
        lowest = Math.max(lowest, yB);
        const g = h >= 8 ? 1 : 0, bh = h - g, y = yT + g / 2;
        const poc = Math.abs(p - fp.poc) < row / 2;
        const fs = Math.max(9, Math.min(11, Math.floor(bh * 0.72)));
        const showText = textOK && bh >= 11;
        ctx.font = `500 ${fs}px ${P.font}`;
        const cell = (x0, x1, val, hue, scale, txt, align) => {
          const k = scale > 0 ? val / scale : 0;
          ctx.fillStyle = poc ? (dark ? "#f2f2f2" : "#131722") : shade(hue, k);
          box(ctx, x0, y, x1 - x0, bh, 2);
          if (showText) {
            ctx.fillStyle = poc ? (dark ? "#131722" : "#ffffff") : ink(k);
            ctx.textAlign = align;
            ctx.fillText(txt, align === "center" ? (x0 + x1) / 2 : align === "right" ? x1 - 4 : x0 + 4, y + bh / 2 + 0.5);
          }
        };
        if (pref.mode === "delta") {
          const dl = buy - sell;
          cell(bL, bR, Math.abs(dl), dl >= 0 ? P.up : P.down, dmax, (dl > 0 ? "+" : dl < 0 ? "−" : "") + fmtD(Math.abs(dl)), "center");
        } else if (pref.mode === "volume") {
          cell(bL, bR, sell + buy, P.muted, tmax, fmtT(sell + buy), "center");
        } else {
          cell(sL, sR, sell, P.down, vmax, fmtV(sell), "center");
          cell(bL, bR, buy, P.up, vmax, fmtV(buy), "center");
          // the imbalance tick, on the outer edge of the side that dominated
          if (imb === "sell") { ctx.fillStyle = P.down; ctx.fillRect(sL - 4, y + 1, 2.5, Math.max(1, bh - 2)); }
          if (imb === "buy") { ctx.fillStyle = P.up; ctx.fillRect(bR + 1.5, y + 1, 2.5, Math.max(1, bh - 2)); }
        }
      }

      // stacked imbalances: a solid rail along the run, stronger than a tick
      for (const z of fp.zones || []) {
        const yA = series.priceToCoordinate(z.hi), yB = series.priceToCoordinate(z.lo);
        if (yA == null || yB == null) continue;
        ctx.fillStyle = z.side === "buy" ? P.up : P.down;
        const x = z.side === "buy" ? (single ? bR : bR) + 1.5 : (single ? bL - 6 : sL - 4);
        ctx.fillRect(x, yA, 2.5, yB - yA);
        ctx.globalAlpha = 0.18; ctx.fillRect(x - (z.side === "buy" ? 0 : 3), yA, 5.5, yB - yA); ctx.globalAlpha = 1;
      }

      // value area: dotted rules at VAH and VAL with a marker at the left
      ctx.strokeStyle = rgba(dark ? P.fg : "#131722", 0.7); ctx.lineWidth = 1; ctx.setLineDash([1.5, 2]);
      for (const pv of [fp.vah, fp.val]) {
        const yv = series.priceToCoordinate(pv);
        if (yv == null) continue;
        const yy = Math.round(yv) + 0.5;
        ctx.beginPath(); ctx.moveTo(xL, yy); ctx.lineTo(xR, yy); ctx.stroke();
        ctx.fillStyle = rgba(dark ? P.fg : "#131722", 0.85);
        ctx.beginPath(); ctx.moveTo(xL - 5, yy - 3); ctx.lineTo(xL - 1, yy); ctx.lineTo(xL - 5, yy + 3); ctx.fill();
      }
      ctx.setLineDash([]);

      // a bar the tape only partly covered says so, above its high
      if (fp.covered > 0 && fp.covered < 0.97) {
        const yTop = series.priceToCoordinate(fp.h);
        if (yTop != null) {
          ctx.fillStyle = P.muted; ctx.font = `500 9px ${P.font}`; ctx.textAlign = "center";
          ctx.fillText(`${Math.round(fp.covered * 100)}% of tape`, v.x, yTop - 8);
        }
      }

      if (!pref.summary || lowest === -Infinity || spacing < 40) continue;
      // side totals directly under each column
      let y = lowest + 9;
      ctx.font = `500 10px ${P.font}`; ctx.textAlign = "center";
      if (single) {
        ctx.fillStyle = rgba(P.fg, 0.8); ctx.fillText(qty(fp.buy + fp.sell), (bL + bR) / 2, y);
      } else {
        ctx.fillStyle = P.down; ctx.fillText(qty(fp.sell), (sL + sR) / 2, y);
        ctx.fillStyle = P.up; ctx.fillText(qty(fp.buy), (bL + bR) / 2, y);
      }
      // the Delta / Total card under the bar
      if (spacing < 70) continue;
      const cwid = Math.min(spacing * 0.92, 132), ch = 30, cx0 = v.x - cwid / 2, cy0 = y + 8;
      if (cy0 + ch > H - 2) continue;
      ctx.fillStyle = dark ? rgba(P.card, 0.96) : "#ffffff";
      ctx.strokeStyle = rgba(P.muted, dark ? 0.28 : 0.22);
      ctx.beginPath();
      if (ctx.roundRect) ctx.roundRect(cx0 + 0.5, cy0 + 0.5, cwid - 1, ch, 6); else ctx.rect(cx0 + 0.5, cy0 + 0.5, cwid - 1, ch);
      ctx.fill(); ctx.stroke();
      ctx.font = `500 10px ${P.font}`;
      const lx = cx0 + 10, vx = cx0 + cwid * 0.42;
      ctx.textAlign = "left"; ctx.fillStyle = P.muted;
      ctx.fillText("Delta", lx, cy0 + 10); ctx.fillText("Total", lx, cy0 + 22);
      ctx.font = `600 10px ${P.font}`;
      ctx.fillStyle = fp.delta >= 0 ? P.up : P.down; ctx.fillText(signed(fp.delta), vx, cy0 + 10);
      ctx.fillStyle = rgba(P.fg, 0.92); ctx.fillText(qty(fp.buy + fp.sell), vx, cy0 + 22);
    }
  }

  // ── the control bar: draggable by its grip, collapsible to a pill ──
  function ensureBar() {
    if (bar) return bar;
    bar = document.createElement("div");
    bar.className = "fp-bar";
    bar.addEventListener("click", onBar);
    bar.addEventListener("pointerdown", onGrip);
    env.host.appendChild(bar);
    tip = document.createElement("div");
    tip.className = "fp-tip";
    tip.hidden = true;
    env.host.appendChild(tip);
    return bar;
  }

  function seg(key, opts) {
    return `<div class="fp-seg" role="group">` + opts.map(([v, label, title]) =>
      `<button type="button" data-fp="${key}" data-v="${v}" class="${String(pref[key]) === String(v) ? "on" : ""}"${title ? ` title="${title}"` : ""}>${label}</button>`).join("") + `</div>`;
  }

  /** Keep the bar where the reader put it, but always on the chart. */
  function place() {
    if (!bar) return;
    const p = pref.bar;
    if (!p) { bar.style.left = ""; bar.style.top = ""; bar.classList.remove("moved"); return; }
    const hw = env.host.clientWidth, hh = env.host.clientHeight;
    if (!hw || !hh || !bar.offsetWidth) return;      // not laid out yet; the observer re-runs this
    const x = Math.max(4, Math.min(p.x, hw - bar.offsetWidth - 4));
    const y = Math.max(4, Math.min(p.y, hh - bar.offsetHeight - 4));
    bar.classList.add("moved");
    bar.style.left = x + "px"; bar.style.top = y + "px";
  }

  function onGrip(e) {
    if (!e.target.closest(".fp-grip") || e.button !== 0) return;
    e.preventDefault(); e.stopPropagation();
    const host = env.host.getBoundingClientRect(), r = bar.getBoundingClientRect();
    const dx = e.clientX - r.left, dy = e.clientY - r.top;
    bar.classList.add("dragging");
    bar.setPointerCapture(e.pointerId);
    const move = (ev) => {
      pref.bar = { x: ev.clientX - host.left - dx, y: ev.clientY - host.top - dy };
      place();
    };
    const up = () => {
      bar.classList.remove("dragging");
      bar.removeEventListener("pointermove", move);
      bar.removeEventListener("pointerup", up);
      bar.removeEventListener("pointercancel", up);
      save();
    };
    bar.addEventListener("pointermove", move);
    bar.addEventListener("pointerup", up);
    bar.addEventListener("pointercancel", up);
  }

  // Re-render only on change: the poll repaints every two seconds, and
  // replacing the markup each time would drop hover states and a drag in progress.
  let barHTML = "";
  function setBar(html) { if (html !== barHTML) { bar.innerHTML = html; barHTML = html; } }

  const GRIP = `<span class="fp-grip" title="Drag to move">${Icons.svg("grip", "xs")}</span>`;

  function paintBar() {
    if (!active) return;
    ensureBar();
    bar.hidden = false;
    const iv = env.interval();
    bar.classList.toggle("collapsed", !!pref.barHidden);
    if (pref.barHidden) {
      setBar(GRIP + `<button type="button" class="fp-pill" data-fp="show" title="Show footprint controls">` +
        `Footprint${Icons.svg("chevronDown", "xs")}</button>`);
      return place();
    }
    let body;
    if (!meta) body = `<span class="fp-muted">Loading…</span>`;
    else if (!meta.available) {
      const intraday = /^(1m|3m|5m|15m|30m|1h)$/.test(iv);
      body = intraday
        ? `<span class="fp-muted">Needs a crypto tape</span>` +
          `<button type="button" class="fp-link" data-fp="why">Why</button>` +
          `<button type="button" class="fp-link" data-fp="sym" data-v="BTCUSDT">Open BTCUSDT</button>`
        : `<span class="fp-muted">Intraday only</span>` +
          `<button type="button" class="fp-link" data-fp="iv" data-v="5m">Switch to 5m</button>`;
    } else {
      body = seg("mode", [["bidask", "Bid × Ask", "Sold into the bid × bought from the offer"],
                          ["delta", "Delta", "Bought minus sold at each price"],
                          ["volume", "Volume", "Total traded at each price"]]) +
        `<div class="fp-seg fp-row" role="group">` +
          `<button type="button" data-fp="row" data-v="2" title="Coarser rows">−</button>` +
          `<button type="button" data-fp="row" data-v="1" class="fp-rowv${pref.rowMul === 1 ? "" : " on"}" title="Price per row · click for auto">${priceText(meta.row)}</button>` +
          `<button type="button" data-fp="row" data-v="0.5" title="Finer rows">+</button></div>` +
        seg("ratio", [[2, "2×"], [3, "3×"], [4, "4×"]].map(([v, l]) => [v, l, `Imbalance when one side is ${v}× the diagonal`])) +
        `<button type="button" class="fp-tog ${pref.summary ? "on" : ""}" data-fp="summary" title="Side totals and the Delta / Total card under each bar">Totals</button>`;
    }
    setBar(GRIP + `<span class="fp-title">Footprint</span>` + body +
      `<button type="button" class="fp-icon" data-fp="hide" title="Hide controls">${Icons.svg("chevronUp", "xs")}</button>`);
    place();
  }

  function onBar(e) {
    const b = e.target.closest("[data-fp]");
    if (!b) return;
    e.stopPropagation();
    const k = b.dataset.fp, v = b.dataset.v;
    if (k === "hide" || k === "show") { pref.barHidden = k === "hide"; save(); paintBar(); return; }
    if (k === "mode") { pref.mode = v; save(); paintBar(); requestUpdate(); return; }
    if (k === "ratio") { pref.ratio = Number(v); save(); refetchAt(rowWanted); return; }
    if (k === "row") {
      const m = Number(v);
      pref.rowMul = m === 1 ? 1 : Math.max(0.125, Math.min(16, (pref.rowMul || 1) * m));
      save(); refetchAt(rowFor(pref.rowMul)); return;
    }
    if (k === "summary") { pref.summary = !pref.summary; save(); paintBar(); requestUpdate(); return; }
    if (k === "iv") return env.setInterval(v);
    if (k === "sym") { location.search = "?symbol=" + encodeURIComponent(v); return; }
    if (k === "why") return env.notify(meta && meta.reason ? meta.reason : "");
  }

  // ── row size: the server's data-sized row, stepped by − / + ──
  function rowFor(mul) {
    if (!autoRow) return 0;
    if (mul === 1) return autoRow;
    let r = nice(autoRow * mul);
    if (meta && meta.step) r = Math.max(meta.step, Math.round(r / meta.step) * meta.step);
    return r;
  }
  function refetchAt(row) {
    gen++;
    cache = new Map(); oldest = null; hasMore = false;
    rowWanted = row || 0;
    paintBar();
    refresh(true);
  }

  // ── crosshair card ──
  function onCrosshair(param) {
    if (!active || !tip) return;
    if (!param || !param.time || !param.point || !meta || !meta.available) { tip.hidden = true; return; }
    const fp = cache.get(env.fromChart(param.time));
    if (!fp || !fp.rows || !fp.rows.length) { tip.hidden = true; return; }
    const price = series.coordinateToPrice(param.point.y);
    const row = meta.row;
    const key = Math.floor(price / row + 1e-9) * row;
    const r = fp.rows.find((x) => Math.abs(x[0] - key) < row / 2);
    const t = new Date((fp.t + (env.tzOffset || 0)) * 1000);
    const when = t.toLocaleString("en-IN", { timeZone: "UTC", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", hour12: false });
    const line = (a, b, cls = "") => `<div class="fp-tl"><span>${a}</span><b class="${cls}">${b}</b></div>`;
    tip.innerHTML =
      `<div class="fp-th">${when}<span>${env.interval()}</span></div>` +
      (r ? `<div class="fp-row">${priceText(r[0])} – ${priceText(r[0] + row)}</div>` +
           line("Sold (bid)", qty(r[1]) || "0", r[3] === "sell" ? "dn" : "") +
           line("Bought (ask)", qty(r[2]) || "0", r[3] === "buy" ? "up" : "") +
           line("Delta", signed(r[2] - r[1]), r[2] >= r[1] ? "up" : "dn") +
           (r[3] ? `<div class="fp-note ${r[3] === "buy" ? "up" : "dn"}">${r[3] === "buy" ? "Buy" : "Sell"} imbalance ≥ ${meta.ratio}× diagonal</div>` : "") +
           `<div class="fp-sep"></div>` : "") +
      line("Volume", qty(fp.buy + fp.sell)) +
      line("Delta", signed(fp.delta), fp.delta >= 0 ? "up" : "dn") +
      line("CVD", signed(fp.cvd), fp.cvd >= 0 ? "up" : "dn") +
      line("Trades", (fp.trades || 0).toLocaleString("en-IN")) +
      line("POC", priceText(fp.poc)) +
      line("Value area", `${priceText(fp.val)} – ${priceText(fp.vah)}`) +
      (fp.covered < 0.97 ? `<div class="fp-note">Tape covers ${(fp.covered * 100).toFixed(0)}% of this bar's volume</div>` : "");
    tip.hidden = false;
    const host = env.host.getBoundingClientRect();
    const x = param.point.x + 18, y = param.point.y + 18;
    const w = tip.offsetWidth, h = tip.offsetHeight;
    tip.style.left = Math.min(x, host.width - w - 80) + "px";
    tip.style.top = Math.min(y, host.height - h - 40) + "px";
  }

  // ── turning it on and off ──
  function attachTo(s) {
    if (prim && series) { try { series.detachPrimitive(prim); } catch { /* old series gone */ } }
    series = s;
    prim = makePrimitive();
    series.attachPrimitive(prim);
  }

  /** Frame both axes for reading: wide bars on the time axis, scrolled to now,
   *  and the price axis fitted to the bars on screen so the data-sized rows
   *  come out ~15px tall. Run again on the next frame, because the chart owner
   *  restores its saved visible range after a series swap or a bar load. */
  function frame() {
    const apply = () => {
      if (!active) return;
      const ts = env.chart.timeScale();
      ts.applyOptions({ barSpacing: 132, rightOffset: 2 });
      ts.scrollToRealTime();
      try { env.series().priceScale().applyOptions({ autoScale: true }); } catch { /* older build */ }
    };
    apply();
    requestAnimationFrame(() => requestAnimationFrame(apply));
    setTimeout(apply, 400);           // rAF does not run in a background tab
  }

  function enable() {
    const s = env.series();
    if (!active) {
      active = true;
      const ts = env.chart.timeScale();
      savedSpacing = ts.options().barSpacing;
      frame();
      reset();
      paintBar();
    }
    if (s !== series) attachTo(s);
    clearInterval(timer);
    timer = setInterval(() => refresh(false), POLL_MS);
    refresh(true);
  }

  function disable() {
    if (!active) return;
    active = false;
    clearInterval(timer); timer = 0;
    if (prim && series) { try { series.detachPrimitive(prim); } catch { /* gone */ } }
    prim = null; series = null;
    if (bar) bar.hidden = true;
    if (tip) tip.hidden = true;
    if (savedSpacing) env.chart.timeScale().applyOptions({ barSpacing: savedSpacing });
    savedSpacing = null;
    reset();
  }

  function sync() {
    if (!env) return;
    const want = typeof ChartSettings !== "undefined" && ChartSettings.getType() === "footprint";
    if (want) enable(); else disable();
  }

  /** main.js hands over its chart once, with getters for the live state. */
  function mount(e) {
    env = e;
    env.chart.subscribeCrosshairMove(onCrosshair);
    if (typeof ResizeObserver !== "undefined") new ResizeObserver(() => active && place()).observe(env.host);
    window.addEventListener("resize", () => active && place());
    env.chart.timeScale().subscribeVisibleLogicalRangeChange(() => active && requestUpdate());
    document.addEventListener("charto:series-swapped", sync);
    document.addEventListener("charto:bars-loaded", () => { if (active) { reset(); paintBar(); frame(); refresh(true); } });
    document.addEventListener("visibilitychange", () => { if (active && document.visibilityState === "visible") refresh(true); });
    sync();
  }

  return { mount, sync, isOn: () => active };
})();
