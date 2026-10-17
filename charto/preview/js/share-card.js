/* The candlestick share card (1200 x 630) a published setup unfurls into.
 *
 * share-preview.js drew a bare line of closes, which threw away the one thing
 * a shared setup has and a cashtag card cannot: the author's own analysis.
 * This card keeps it. The right side is the author's window of candles, with
 * volume underneath and their drawings on top, solid and in their colours;
 * what the chat drew comes along dashed, as it is on the chart, so a reader
 * can still tell who drew what. The left carries the company's mark, the
 * setup's title and note, the last close and the move across the window, and
 * the byline.
 *
 * Every number is read off the bars the author was looking at. No market data
 * is fetched or invented for the card. Returns "" when it cannot draw, so the
 * caller falls back to SharePreview. */
"use strict";

const ShareCard = (() => {
  const W = 1200, H = 630, M = 56;
  const COL_W = 384;                                   // text column: 56 → 440
  const P = { x: 472, y: 40, w: 688, h: 550, r: 18 };  // candle panel
  const PLOT = { l: 22, r: 82, t: 28, b: 26 };         // plot inset inside the panel
  const MAX_URL = 640_000;                             // under shares.OG_IMAGE_MAX
  const MAX_BARS = 160;
  const IST = 19800;                                   // chart time = epoch + IST

  const C = {
    bg: "#08090a", panel: "#0e1013", border: "#23262b", grid: "rgba(255,255,255,0.045)",
    fg: "#f4f2ec", muted: "#959ba3", faint: "#666c74", chip: "#17191d",
    up: "#22ab94", down: "#f23645", draw: "#e9e5da",
    res: "#ef4444", sup: "#22c55e", neutral: "#c084fc",
  };
  const SANS = "Inter, ui-sans-serif, -apple-system, Arial, sans-serif";

  const font = (ctx, spec, tracking = 0) => {
    ctx.font = spec;
    if ("letterSpacing" in ctx) ctx.letterSpacing = `${tracking}px`;
  };
  const rgba = (hex, a) => {
    const h = String(hex || "").replace("#", "");
    if (!/^[0-9a-f]{6}$/i.test(h)) return `rgba(233,229,218,${a})`;
    const n = parseInt(h, 16);
    return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
  };

  function wrap(ctx, value, maxWidth, maxLines) {
    const words = String(value || "").replace(/\s+/g, " ").trim().split(" ").filter(Boolean);
    const out = [];
    let line = "";
    for (const word of words) {
      const next = line ? `${line} ${word}` : word;
      if (line && ctx.measureText(next).width > maxWidth) { out.push(line); line = word; }
      else line = next;
    }
    if (line) out.push(line);
    if (out.length > maxLines) {
      out.length = maxLines;
      let last = out[maxLines - 1];
      while (last && ctx.measureText(`${last}…`).width > maxWidth) last = last.slice(0, -1);
      out[maxLines - 1] = `${last.trimEnd()}…`;
    }
    return out;
  }
  const fit = (ctx, text, maxWidth) => {
    let s = String(text || "");
    if (ctx.measureText(s).width <= maxWidth) return s;
    while (s && ctx.measureText(`${s}…`).width > maxWidth) s = s.slice(0, -1);
    return `${s.trimEnd()}…`;
  };

  const loadImage = (src, cors) => new Promise((resolve) => {
    if (!src) return resolve(null);
    const img = new Image();
    // A logo from another origin is drawn only if it is served for CORS;
    // otherwise it would taint the canvas and the card could not be encoded.
    if (cors) img.crossOrigin = "anonymous";
    img.onload = () => resolve(img);
    img.onerror = () => resolve(null);
    img.src = src;
  });

  function roundRect(ctx, x, y, w, h, r) {
    ctx.beginPath();
    ctx.roundRect(x, y, w, h, r);
  }

  /** Price text at the precision the instrument trades at. */
  function fmtPrice(v, dp) {
    if (!Number.isFinite(v)) return "—";
    return v.toLocaleString("en-IN", { minimumFractionDigits: dp, maximumFractionDigits: dp });
  }
  const decimalsOf = (bars, sym) => {
    try { if (window.Universe && Universe.decimals) { const d = Universe.decimals(sym); if (Number.isFinite(d)) return d; } } catch {}
    const last = bars[bars.length - 1].close;
    return last >= 1000 ? 2 : last >= 1 ? 2 : 4;
  };
  const dateOf = (t) => {
    try {
      return new Date((t - IST) * 1000).toLocaleDateString("en-GB", { day: "numeric", month: "short", timeZone: "Asia/Kolkata" });
    } catch { return ""; }
  };

  /** Round tick values covering [lo, hi], about `n` of them. */
  function ticks(lo, hi, n = 4) {
    const span = hi - lo;
    if (!(span > 0)) return [lo];
    const raw = span / n, mag = 10 ** Math.floor(Math.log10(raw));
    const step = [1, 2, 2.5, 5, 10].map((k) => k * mag).find((s) => s >= raw) || raw;
    const out = [];
    for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(v);
    return out;
  }

  /* ── the plot: time and price to card pixels ─────────────────────────── */

  function frame(bars, drawings, scene) {
    const x0 = P.x + PLOT.l, x1 = P.x + P.w - PLOT.r;
    const y0 = P.y + PLOT.t, y1 = P.y + P.h - PLOT.b;
    const n = bars.length;
    const pitch = (x1 - x0) / n;
    const step = n > 1 ? (bars[n - 1].time - bars[0].time) / (n - 1) : 86400;
    let lo = Infinity, hi = -Infinity;
    for (const b of bars) { lo = Math.min(lo, b.low); hi = Math.max(hi, b.high); }
    // The author's marks widen the range, within reason: a fib drawn on a
    // far older swing must not flatten the candles into a stripe.
    const span = hi - lo || hi * 0.02 || 1;
    const near = (v) => Number.isFinite(v) && v > lo - span * 0.6 && v < hi + span * 0.6;
    const vals = [];
    for (const d of drawings) for (const p of d.pts || []) vals.push(p.v);
    for (const a of scene) {
      if (a.kind === "level") vals.push(a.price);
      if (a.kind === "zone") vals.push(a.lo, a.hi);
      if (a.p1) vals.push(a.p1.v, a.p2 && a.p2.v);
    }
    for (const v of vals) if (near(v)) { lo = Math.min(lo, v); hi = Math.max(hi, v); }
    const pad = (hi - lo) * 0.06 || 1;
    lo -= pad; hi += pad;
    const volH = (y1 - y0) * 0.16;
    const priceBottom = y1 - volH - 8;
    // time → x by bar index, so session gaps do not open holes in the card
    const times = bars.map((b) => b.time);
    const xOfIndex = (i) => x0 + pitch * (i + 0.5);
    const xOf = (t) => {
      if (!Number.isFinite(t)) return null;
      if (t <= times[0]) return xOfIndex((t - times[0]) / step);
      if (t >= times[n - 1]) return xOfIndex(n - 1 + (t - times[n - 1]) / step);
      let a = 0, b = n - 1;
      while (b - a > 1) { const m = (a + b) >> 1; if (times[m] <= t) a = m; else b = m; }
      return xOfIndex(a + (t - times[a]) / ((times[b] - times[a]) || 1));
    };
    const yOf = (v) => y0 + ((hi - v) / (hi - lo)) * (priceBottom - y0);
    return { x0, x1, y0, y1, priceBottom, volTop: y1 - volH, pitch, lo, hi, xOf, yOf, xOfIndex };
  }

  function panel(ctx) {
    ctx.save();
    roundRect(ctx, P.x, P.y, P.w, P.h, P.r);
    const g = ctx.createLinearGradient(0, P.y, 0, P.y + P.h);
    g.addColorStop(0, "#111317"); g.addColorStop(1, C.panel);
    ctx.fillStyle = g; ctx.fill();
    ctx.strokeStyle = C.border; ctx.lineWidth = 1.25; ctx.stroke();
    ctx.restore();
  }

  function grid(ctx, f) {
    ctx.save();
    for (const v of ticks(f.lo, f.hi, 4)) {
      const y = Math.round(f.yOf(v)) + 0.5;
      if (y < f.y0 || y > f.priceBottom) continue;
      ctx.strokeStyle = C.grid; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(f.x0, y); ctx.lineTo(f.x1, y); ctx.stroke();
    }
    ctx.restore();
  }
  /** Axis values, minus any a chip would sit on: a chip says it better. */
  function axisLabels(ctx, f, dp, taken) {
    ctx.save();
    font(ctx, `500 13px ${SANS}`);
    ctx.textAlign = "left"; ctx.fillStyle = C.faint;
    for (const v of ticks(f.lo, f.hi, 4)) {
      const y = f.yOf(v);
      if (y < f.y0 || y > f.priceBottom || taken.some((t) => Math.abs(t - y) < 20)) continue;
      ctx.fillText(fmtPrice(v, v >= 1000 ? 0 : dp), f.x1 + 12, y + 4.5);
    }
    ctx.restore();
  }

  function candles(ctx, bars, f) {
    const body = Math.max(1.5, Math.min(14, f.pitch * 0.64));
    let vmax = 0;
    for (const b of bars) vmax = Math.max(vmax, b.volume || 0);
    ctx.save();
    bars.forEach((b, i) => {
      const x = f.xOfIndex(i), up = b.close >= b.open, col = up ? C.up : C.down;
      if (vmax > 0 && b.volume > 0) {
        const vh = ((b.volume / vmax) * (f.y1 - f.volTop));
        ctx.fillStyle = rgba(col, 0.22);
        ctx.fillRect(x - body / 2, f.y1 - vh, body, vh);
      }
      ctx.strokeStyle = col; ctx.lineWidth = Math.max(1, Math.min(1.6, f.pitch * 0.18));
      ctx.beginPath(); ctx.moveTo(x, f.yOf(b.high)); ctx.lineTo(x, f.yOf(b.low)); ctx.stroke();
      const yo = f.yOf(b.open), yc = f.yOf(b.close);
      const top = Math.min(yo, yc), h = Math.max(1.5, Math.abs(yc - yo));
      ctx.fillStyle = col;
      if (body >= 4) { roundRect(ctx, x - body / 2, top, body, h, Math.min(1.5, body / 4)); ctx.fill(); }
      else ctx.fillRect(x - body / 2, top, body, h);
    });
    ctx.restore();
  }

  /** A price chip on the right axis: the author's level or the last close. */
  function chip(ctx, f, v, text, col, solid) {
    const y = f.yOf(v);
    if (!(y >= f.y0 - 2 && y <= f.priceBottom + 2)) return null;
    font(ctx, `600 12.5px ${SANS}`);
    const w = ctx.measureText(text).width + 14;
    const x = f.x1 + 6;
    ctx.save();
    roundRect(ctx, x, y - 11, w, 22, 6);
    if (!solid) { ctx.fillStyle = C.panel; ctx.fill(); }
    ctx.fillStyle = solid ? col : rgba(col, 0.2); ctx.fill();
    ctx.fillStyle = solid ? "#0a0b0c" : col;
    ctx.fillText(text, x + 7, y + 4.5);
    ctx.restore();
    return y;
  }

  function clipPlot(ctx, f) {
    ctx.beginPath();
    ctx.rect(f.x0 - 6, f.y0 - 6, f.x1 - f.x0 + 12, f.priceBottom - f.y0 + 12);
    ctx.clip();
  }

  /* ── the author's drawings, and what the chat drew ───────────────────── */

  function drawUser(ctx, f, d, dp) {
    const pts = (d.pts || []).filter((p) => Number.isFinite(p.v));
    if (!pts.length || (d.pane && d.pane !== "price")) return [];
    const col = d.color || C.draw;
    const X = (p) => f.xOf(p.t), Y = (p) => f.yOf(p.v);
    const chips = [];
    ctx.strokeStyle = col; ctx.fillStyle = col; ctx.lineWidth = 2.25;
    ctx.lineCap = "round"; ctx.lineJoin = "round"; ctx.setLineDash([]);
    const seg = (a, b) => { ctx.beginPath(); ctx.moveTo(a[0], a[1]); ctx.lineTo(b[0], b[1]); ctx.stroke(); };
    const extend = (a, b, both) => {
      const dx = b[0] - a[0], dy = b[1] - a[1];
      if (!dx) return seg(a, b);
      const k = (x) => [x, a[1] + (dy / dx) * (x - a[0])];
      seg(both ? k(f.x0 - 10) : a, k(dx > 0 ? f.x1 + 10 : f.x0 - 10));
    };
    const p = pts.map((q) => [X(q), Y(q)]).filter((q) => q[0] !== null);
    if (!p.length) return [];
    switch (d.type) {
      case "hline":
        seg([f.x0, p[0][1]], [f.x1, p[0][1]]); chips.push([pts[0].v, col]); break;
      case "hray":
        seg(p[0], [f.x1, p[0][1]]); chips.push([pts[0].v, col]); break;
      case "vline":
        seg([p[0][0], f.y0], [p[0][0], f.priceBottom]); break;
      case "crossline":
        seg([f.x0, p[0][1]], [f.x1, p[0][1]]); seg([p[0][0], f.y0], [p[0][0], f.priceBottom]); break;
      case "ray": if (p[1]) extend(p[0], p[1], false); break;
      case "extended": if (p[1]) extend(p[0], p[1], true); break;
      case "rect": if (p[1]) {
        const x = Math.min(p[0][0], p[1][0]), y = Math.min(p[0][1], p[1][1]);
        const w = Math.abs(p[1][0] - p[0][0]), h = Math.abs(p[1][1] - p[0][1]);
        ctx.fillStyle = rgba(col, 0.12); ctx.fillRect(x, y, w, h);
        ctx.lineWidth = 1.5; ctx.strokeRect(x, y, w, h);
      } break;
      case "ellipse": if (p[1]) {
        ctx.beginPath();
        ctx.ellipse((p[0][0] + p[1][0]) / 2, (p[0][1] + p[1][1]) / 2,
          Math.abs(p[1][0] - p[0][0]) / 2, Math.abs(p[1][1] - p[0][1]) / 2, 0, 0, Math.PI * 2);
        ctx.fillStyle = rgba(col, 0.1); ctx.fill(); ctx.stroke();
      } break;
      case "fib": if (p[1]) {
        const [a, b] = [pts[0].v, pts[1].v];
        const xs = Math.min(p[0][0], p[1][0]);
        font(ctx, `500 11.5px ${SANS}`);
        for (const r of [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1]) {
          const v = b + (a - b) * r, y = f.yOf(v);
          ctx.globalAlpha = r === 0.5 || r === 0.618 ? 0.95 : 0.55;
          ctx.lineWidth = 1.25; seg([xs, y], [f.x1, y]);
          ctx.fillText(String(r), xs + 4, y - 5);
        }
        ctx.globalAlpha = 1;
      } break;
      case "text": {
        const t = String(d.text || d.label || "").slice(0, 40);
        if (!t) break;
        font(ctx, `600 15px ${SANS}`);
        const w = ctx.measureText(t).width + 16;
        roundRect(ctx, p[0][0], p[0][1] - 14, w, 26, 7);
        ctx.fillStyle = C.panel; ctx.fill();
        ctx.fillStyle = rgba(col, 0.18); ctx.fill();
        ctx.fillStyle = col; ctx.fillText(t, p[0][0] + 8, p[0][1] + 4);
      } break;
      case "long": case "short": if (p[1]) {
        const x2 = p[2] ? p[2][0] : f.x1;
        const good = d.type === "long" ? C.up : C.down, bad = d.type === "long" ? C.down : C.up;
        const entry = p[0][1], tgt = p[1][1], stop = p[2] ? p[2][1] : entry + (entry - tgt) * 0.5;
        ctx.fillStyle = rgba(good, 0.16); ctx.fillRect(p[0][0], Math.min(entry, tgt), x2 - p[0][0], Math.abs(tgt - entry));
        ctx.fillStyle = rgba(bad, 0.16); ctx.fillRect(p[0][0], Math.min(entry, stop), x2 - p[0][0], Math.abs(stop - entry));
        ctx.lineWidth = 1.5; seg([p[0][0], entry], [x2, entry]);
      } break;
      default:
        // trend, channel, triangle, brush, path, curve, pitchfork and the rest
        // keep their anchors' shape: a line through the points as placed
        if (p.length === 1) { ctx.beginPath(); ctx.arc(p[0][0], p[0][1], 4, 0, Math.PI * 2); ctx.fill(); break; }
        ctx.beginPath(); p.forEach((q, i) => (i ? ctx.lineTo(q[0], q[1]) : ctx.moveTo(q[0], q[1])));
        if (d.type === "triangle" && p.length >= 3) { ctx.closePath(); ctx.fillStyle = rgba(col, 0.1); ctx.fill(); }
        ctx.stroke();
        if (d.type !== "brush" && d.type !== "path" && d.type !== "curve") {
          for (const q of p) { ctx.beginPath(); ctx.arc(q[0], q[1], 3.2, 0, Math.PI * 2); ctx.fillStyle = col; ctx.fill(); }
        }
    }
    return chips;
  }

  function drawScene(ctx, f, a) {
    if (a.pane && a.pane !== "price") return [];
    const col = a.role === "resistance" ? C.res : a.role === "support" ? C.sup : C.neutral;
    const alpha = { strong: 1, moderate: 0.75, weak: 0.5 }[a.strength] || 0.75;
    ctx.save();
    ctx.globalAlpha = alpha; ctx.strokeStyle = col; ctx.lineWidth = 1.75; ctx.setLineDash([7, 6]);
    const chips = [];
    if (a.kind === "level" && Number.isFinite(a.price)) {
      const y = f.yOf(a.price);
      ctx.beginPath(); ctx.moveTo(f.x0, y); ctx.lineTo(f.x1, y); ctx.stroke();
      chips.push([a.price, col]);
    } else if (a.kind === "zone" && Number.isFinite(a.lo) && Number.isFinite(a.hi)) {
      const y1 = f.yOf(a.hi), y2 = f.yOf(a.lo);
      ctx.setLineDash([]);
      const g = ctx.createLinearGradient(0, y1, 0, y2);
      g.addColorStop(0, rgba(col, 0.2)); g.addColorStop(0.5, rgba(col, 0.06)); g.addColorStop(1, rgba(col, 0.2));
      ctx.fillStyle = g; ctx.fillRect(f.x0, y1, f.x1 - f.x0, y2 - y1);
    } else if ((a.kind === "segment" || a.kind === "fib") && a.p1 && a.p2) {
      const x1 = f.xOf(a.p1.t), x2 = f.xOf(a.p2.t);
      if (x1 !== null && x2 !== null) {
        ctx.beginPath(); ctx.moveTo(x1, f.yOf(a.p1.v)); ctx.lineTo(x2, f.yOf(a.p2.v)); ctx.stroke();
      }
    } else if ((a.kind === "label" || a.kind === "point") && a.a) {
      const x = f.xOf(a.a.t);
      if (x !== null) {
        ctx.setLineDash([]); ctx.fillStyle = col;
        ctx.beginPath(); ctx.arc(x, f.yOf(a.a.v), 4, 0, Math.PI * 2); ctx.fill();
      }
    }
    ctx.restore();
    return chips;
  }

  /* ── the left column ─────────────────────────────────────────────────── */

  function brand(ctx) {
    // The bar mark (components/brand/PivotMark) and the wordmark.
    const x = M, y = 46, h = 24, k = h / 576;
    ctx.fillStyle = C.fg;
    for (const [bx, by, bh] of [[-12, 360, 216], [168, 180, 180], [348, 0, 180], [528, 0, 576]]) {
      roundRect(ctx, x + (bx + 12) * k, y + by * k, 132 * k, bh * k, 1.2); ctx.fill();
    }
    font(ctx, `700 25px ${SANS}`, -0.5);
    ctx.fillText("Pivot", x + 36, y + 21);
  }

  function identity(ctx, logo, symbol, name, interval) {
    const y = 128, s = 52;
    ctx.save();
    ctx.beginPath(); ctx.arc(M + s / 2, y + s / 2, s / 2, 0, Math.PI * 2);
    ctx.fillStyle = "#ffffff"; ctx.fill();
    if (logo) {
      ctx.clip();
      const k = Math.min((s - 10) / logo.width, (s - 10) / logo.height);
      const w = logo.width * k, h = logo.height * k;
      ctx.drawImage(logo, M + (s - w) / 2, y + (s - h) / 2, w, h);
    } else {
      ctx.fillStyle = "#16181c"; ctx.fill();
      ctx.fillStyle = C.fg; font(ctx, `700 22px ${SANS}`);
      ctx.textAlign = "center"; ctx.fillText(String(symbol || "?")[0], M + s / 2, y + s / 2 + 8);
    }
    ctx.restore();
    ctx.textAlign = "left";
    const tx = M + s + 16;
    ctx.fillStyle = C.fg; font(ctx, `700 22px ${SANS}`, -0.2);
    const sym = String(symbol || "").toUpperCase().slice(0, 18);
    ctx.fillText(sym, tx, y + 24);
    if (interval) {
      const sx = tx + ctx.measureText(sym).width + 10;
      font(ctx, `600 12.5px ${SANS}`, 0.4);
      const label = String(interval).slice(0, 6), w = ctx.measureText(label).width + 16;
      roundRect(ctx, sx, y + 7, w, 22, 6);
      ctx.fillStyle = C.chip; ctx.fill(); ctx.strokeStyle = C.border; ctx.lineWidth = 1; ctx.stroke();
      ctx.fillStyle = C.muted; ctx.fillText(label, sx + 8, y + 22);
    }
    ctx.fillStyle = C.muted; font(ctx, `400 16px ${SANS}`);
    ctx.fillText(fit(ctx, name || "", COL_W - (s + 16)), tx, y + 46);
  }

  function priceRow(ctx, bars, dp, y) {
    const first = bars[0].close, last = bars[bars.length - 1].close;
    const move = first ? ((last - first) / first) * 100 : 0;
    const up = move >= 0, col = up ? C.up : C.down;
    ctx.fillStyle = C.fg; font(ctx, `650 38px ${SANS}`, -1.2);
    const p = fmtPrice(last, dp);
    ctx.fillText(p, M, y);
    const px = M + ctx.measureText(p).width + 16;
    font(ctx, `650 16px ${SANS}`);
    const t = `${up ? "+" : "−"}${Math.abs(move).toFixed(2)}%`;
    const w = ctx.measureText(t).width + 40;
    roundRect(ctx, px, y - 25, w, 30, 15);
    ctx.fillStyle = rgba(col, 0.16); ctx.fill();
    ctx.fillStyle = col;
    ctx.beginPath();
    if (up) { ctx.moveTo(px + 12, y - 6); ctx.lineTo(px + 18, y - 16); ctx.lineTo(px + 24, y - 6); }
    else { ctx.moveTo(px + 12, y - 15); ctx.lineTo(px + 18, y - 5); ctx.lineTo(px + 24, y - 15); }
    ctx.fill();
    ctx.fillText(t, px + 30, y - 4.5);
    ctx.fillStyle = C.faint; font(ctx, `400 14.5px ${SANS}`);
    ctx.fillText(`since ${dateOf(bars[0].time)}`, M, y + 28);
  }

  function byline(ctx, by, chatTurns) {
    const y = 572;
    ctx.strokeStyle = C.border; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(M, 530); ctx.lineTo(M + COL_W, 530); ctx.stroke();
    const name = String(by || "").trim() || "A Pivot trader";
    ctx.beginPath(); ctx.arc(M + 15, y - 6, 15, 0, Math.PI * 2);
    ctx.fillStyle = "#089981"; ctx.fill();
    ctx.fillStyle = "#fff"; font(ctx, `600 14px ${SANS}`);
    ctx.textAlign = "center"; ctx.fillText(name[0].toUpperCase(), M + 15, y - 1); ctx.textAlign = "left";
    const right = chatTurns ? "with the chat · pivotnow.in" : "pivotnow.in";
    font(ctx, `400 15px ${SANS}`);
    const rw = ctx.measureText(right).width;
    font(ctx, `500 16px ${SANS}`, -0.1); ctx.fillStyle = C.fg;
    ctx.fillText(fit(ctx, name, COL_W - 40 - rw - 18), M + 40, y);
    ctx.fillStyle = C.faint; font(ctx, `400 15px ${SANS}`);
    ctx.textAlign = "right";
    ctx.fillText(right, M + COL_W, y);
    ctx.textAlign = "left";
  }

  function encode(canvas) {
    for (const q of [0.92, 0.86, 0.78, 0.68]) {
      const url = canvas.toDataURL("image/jpeg", q);
      if (url.length <= MAX_URL) return url;
    }
    return "";
  }

  /* opts: { bars, drawings, scene, symbol, name, logo, interval, title, note,
   *         by, chatTurns } — bars as the chart holds them (time is chart time). */
  async function render(opts = {}) {
    let bars = (opts.bars || []).filter((b) => b && [b.open, b.high, b.low, b.close].every(Number.isFinite));
    if (bars.length < 10) return "";
    if (bars.length > MAX_BARS) bars = bars.slice(-MAX_BARS);
    const drawings = (opts.drawings || []).filter((d) => d && Array.isArray(d.pts));
    const scene = (opts.scene || []).filter((a) => a && a.kind);
    if (document.fonts && document.fonts.load) {
      await Promise.all([`700 25px ${SANS}`, `650 38px ${SANS}`, `600 40px ${SANS}`, `400 18px ${SANS}`]
        .map((s) => document.fonts.load(s).catch(() => null)));
    }
    const logoUrl = opts.logo || "";
    const logo = await loadImage(logoUrl, logoUrl && !logoUrl.startsWith("/") && !logoUrl.startsWith(location.origin));

    const canvas = document.createElement("canvas");
    canvas.width = W; canvas.height = H;
    const ctx = canvas.getContext("2d", { alpha: false });
    if (!ctx) return "";
    ctx.fillStyle = C.bg; ctx.fillRect(0, 0, W, H);
    const last = bars[bars.length - 1].close;
    const up = last >= bars[0].close;
    // a low glow behind the latest candles, in the direction of the move
    const glow = ctx.createRadialGradient(P.x + P.w - 120, P.y + P.h * 0.45, 10, P.x + P.w - 120, P.y + P.h * 0.45, 420);
    glow.addColorStop(0, rgba(up ? C.up : C.down, 0.09)); glow.addColorStop(1, "rgba(0,0,0,0)");
    ctx.fillStyle = glow; ctx.fillRect(0, 0, W, H);

    const dp = decimalsOf(bars, opts.symbol);
    const f = frame(bars, drawings, scene);
    panel(ctx);
    grid(ctx, f);
    candles(ctx, bars, f);

    const chips = [];
    ctx.save(); clipPlot(ctx, f);
    for (const a of scene) chips.push(...drawScene(ctx, f, a));
    for (const d of drawings) {
      try { chips.push(...drawUser(ctx, f, d, dp)); } catch (e) { console.warn("[share-card] drawing", d.type, e); }
    }
    ctx.restore();
    // the last close, dashed across, with its chip on the axis
    ctx.save();
    const ly = f.yOf(last);
    ctx.strokeStyle = rgba(up ? C.up : C.down, 0.7); ctx.lineWidth = 1; ctx.setLineDash([2, 4]);
    ctx.beginPath(); ctx.moveTo(f.x0, ly); ctx.lineTo(f.x1, ly); ctx.stroke();
    ctx.restore();
    // the last close first: it is the one chip that must never be dropped
    const taken = [];
    const put = (v, col, solid) => {
      const y = f.yOf(v);
      if (!solid && taken.some((t) => Math.abs(t - y) < 24)) return;
      const got = chip(ctx, f, v, fmtPrice(v, dp), col, solid);
      if (got !== null) taken.push(got);
    };
    put(last, up ? C.up : C.down, true);
    for (const [v, col] of chips.slice(0, 6)) put(v, col, false);
    axisLabels(ctx, f, dp, taken);

    brand(ctx);
    ctx.fillStyle = C.faint; font(ctx, `600 12px ${SANS}`, 1.4);
    ctx.textAlign = "right";
    ctx.fillText("SHARED SETUP", M + COL_W, 64);
    ctx.textAlign = "left";
    identity(ctx, logo, opts.symbol, opts.name, opts.interval);

    ctx.fillStyle = C.fg; font(ctx, `600 40px ${SANS}`, -1.4);
    const titleLines = wrap(ctx, opts.title || "Shared setup", COL_W, 3);
    titleLines.forEach((t, i) => ctx.fillText(t, M, 246 + i * 46));
    let y = 246 + (titleLines.length - 1) * 46;
    const note = String(opts.note || "").trim();
    if (note) {
      ctx.fillStyle = C.muted; font(ctx, `400 18px ${SANS}`, -0.1);
      const room = Math.max(1, Math.min(3, Math.floor((440 - (y + 38)) / 26) + 1));
      wrap(ctx, note, COL_W, room).forEach((t, i) => ctx.fillText(t, M, y + 38 + i * 26));
    }
    priceRow(ctx, bars, dp, 478);
    byline(ctx, opts.by, opts.chatTurns || 0);
    return encode(canvas);
  }

  return { render };
})();
