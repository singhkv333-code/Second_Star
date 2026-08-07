/* Charto preview — the shape algebra.
 *
 * Every drawing on this chart, whether a person placed it or a detector
 * produced it, is a composition of a few primitives. This module owns the
 * primitives, the plane maths, and the painting. Nothing else does geometry.
 *
 * Two rules make the maths correct rather than merely plausible:
 *
 *  1. ANCHORS LIVE IN DATA SPACE — (time, value). A slope is dValue/dTime,
 *     so a channel stays parallel and a fit stays fitted at every zoom
 *     level. Pixels are a rendering detail, never a source of truth.
 *  2. HIT-TESTING LIVES IN PIXEL SPACE — a grab radius is a human-finger
 *     quantity, so it can only be expressed in pixels. Anchors are
 *     projected, then tested.
 *
 * Primitives: point · hline · vline · segment · band · box · polyline · label
 */
"use strict";

const Geo = (() => {

  // ── constructors (data space) ───────────────────────────
  const point = (a, o = {}) => ({ kind: "point", a, ...o });
  const hline = (v, o = {}) => ({ kind: "hline", v, ...o });
  const vline = (t, o = {}) => ({ kind: "vline", t, ...o });
  /** extend: "none" (segment) | "right" (ray) | "both" (extended line) */
  const segment = (a, b, o = {}) => ({ kind: "segment", a, b, extend: "none", ...o });
  const band = (v1, v2, o = {}) => ({ kind: "band", v1, v2, ...o });
  /** The time-axis mirror of `band`: a full-height strip between two times.
   *  A date range is about a span of time, so it needs a shape with width
   *  and no opinion about price. */
  const vband = (t1, t2, o = {}) => ({ kind: "vband", t1, t2, ...o });
  const box = (a, b, o = {}) => ({ kind: "box", a, b, ...o });
  const poly = (pts, o = {}) => ({ kind: "poly", pts, closed: false, ...o });
  const label = (a, text, o = {}) => ({ kind: "label", a, text, ...o });
  // TradingView-style position plan: reward/risk zones, pill labels on the
  // target and stop edges, a bordered centre chip on the entry line. ONE
  // primitive so the user's drawn tool and the chat scene render the
  // identical design from a single painter. targets: [{v, text}] sorted
  // nearest-first; stop: {v, text}; o: {t1, center: [line, line]}.
  const position = (entry, stop, targets, o = {}) =>
    ({ kind: "position", entry, stop, targets, ...o });

  // ── plane maths ─────────────────────────────────────────

  /** Distance from a point to a finite segment. */
  function distToSegment(px, py, x1, y1, x2, y2) {
    const dx = x2 - x1, dy = y2 - y1;
    const l2 = dx * dx + dy * dy;
    const u = l2 ? Math.max(0, Math.min(1, ((px - x1) * dx + (py - y1) * dy) / l2)) : 0;
    return Math.hypot(px - (x1 + u * dx), py - (y1 + u * dy));
  }

  /** Distance to an infinite line through two points (for rays/extensions). */
  function distToLine(px, py, x1, y1, x2, y2) {
    const dx = x2 - x1, dy = y2 - y1;
    const len = Math.hypot(dx, dy);
    if (!len) return Math.hypot(px - x1, py - y1);
    return Math.abs(dy * (px - x1) - dx * (py - y1)) / len;
  }

  /** Is the point inside the polygon? Ray casting, handles concave shapes. */
  function pointInPoly(px, py, pts) {
    let inside = false;
    for (let i = 0, j = pts.length - 1; i < pts.length; j = i++) {
      const [xi, yi] = pts[i], [xj, yj] = pts[j];
      if ((yi > py) !== (yj > py)
          && px < ((xj - xi) * (py - yi)) / (yj - yi || 1e-9) + xi) inside = !inside;
    }
    return inside;
  }

  /** Clip a parametric line to a rect (Liang–Barsky). Returns [[x1,y1],[x2,y2]]
   *  or null. Handles vertical and horizontal lines without special cases. */
  function clipToRect(x1, y1, x2, y2, w, h, tMin = -1e9, tMax = 1e9) {
    const dx = x2 - x1, dy = y2 - y1;
    let t0 = tMin, t1 = tMax;
    const edges = [[-dx, x1], [dx, w - x1], [-dy, y1], [dy, h - y1]];
    for (const [p, q] of edges) {
      if (p === 0) { if (q < 0) return null; continue; }
      const r = q / p;
      if (p < 0) { if (r > t1) return null; if (r > t0) t0 = r; }
      else { if (r < t0) return null; if (r < t1) t1 = r; }
    }
    return [[x1 + t0 * dx, y1 + t0 * dy], [x1 + t1 * dx, y1 + t1 * dy]];
  }

  /** Least squares fit of value against BAR INDEX, not wall-clock.
   *  Sessions have gaps — weekends, holidays — so fitting against epoch
   *  seconds would bend the line across every closed market. Returns the
   *  line in index space plus the residual sigma for deviation bands. */
  function linearFit(values) {
    const n = values.length;
    if (n < 2) return null;
    let sx = 0, sy = 0, sxx = 0, sxy = 0;
    for (let i = 0; i < n; i++) { sx += i; sy += values[i]; sxx += i * i; sxy += i * values[i]; }
    const denom = n * sxx - sx * sx;
    if (!denom) return null;
    const slope = (n * sxy - sx * sy) / denom;
    const intercept = (sy - slope * sx) / n;
    let ss = 0;
    for (let i = 0; i < n; i++) { const r = values[i] - (intercept + slope * i); ss += r * r; }
    return { slope, intercept, sigma: Math.sqrt(ss / Math.max(1, n - 2)) };
  }

  /** Value of the line a→b at time t, in DATA space (zoom-invariant). */
  function valueAt(a, b, t) {
    const dt = b.t - a.t;
    return dt === 0 ? b.v : a.v + ((b.v - a.v) / dt) * (t - a.t);
  }

  /** Ratio levels along a value span — fib and friends. Returns [{ratio,v}]. */
  function ladder(v0, v1, ratios) {
    return ratios.map((r) => ({ ratio: r, v: v1 + (v0 - v1) * r }));
  }

  /** Risk:reward for a position tool. Sign-aware so shorts read correctly. */
  function riskReward(entry, target, stop) {
    const reward = Math.abs(target - entry), risk = Math.abs(entry - stop);
    return risk ? reward / risk : null;
  }

  // ── projection: data space → pixel space ────────────────
  /** env: { tToX(t), vToY(v), w, h } — supplied per pane by the caller. */
  function project(prim, env) {
    const X = env.tToX, Y = env.vToY;
    const P = (a) => { const x = X(a.t), y = Y(a.v); return (x === null || y === null) ? null : [x, y]; };
    switch (prim.kind) {
      case "point": case "label": { const p = P(prim.a); return p && { p }; }
      case "hline": { const y = Y(prim.v); return y === null ? null : { y }; }
      case "vline": { const x = X(prim.t); return x === null ? null : { x }; }
      case "band": {
        const y1 = Y(prim.v1), y2 = Y(prim.v2);
        return (y1 === null || y2 === null) ? null
          : { top: Math.min(y1, y2), bot: Math.max(y1, y2) };
      }
      case "vband": {
        const x1 = X(prim.t1), x2 = X(prim.t2);
        return (x1 === null || x2 === null) ? null
          : { left: Math.min(x1, x2), right: Math.max(x1, x2) };
      }
      case "segment": {
        const a = P(prim.a), b = P(prim.b);
        if (!a || !b) return null;
        if (prim.extend === "none") return { a, b, draw: [a, b] };
        // a ray starts at `a` and passes through `b`; an extended line runs
        // both ways. Clipping is parametric so verticals need no special case.
        const clip = clipToRect(a[0], a[1], b[0], b[1], env.w, env.h,
                                prim.extend === "right" ? 0 : -1e9, 1e9);
        return { a, b, draw: clip || [a, b] };
      }
      case "box": {
        const a = P(prim.a), b = P(prim.b);
        if (!a || !b) return null;
        // normalised, so which corner you dragged from is irrelevant
        return { x: Math.min(a[0], b[0]), y: Math.min(a[1], b[1]),
                 w: Math.abs(b[0] - a[0]), h: Math.abs(b[1] - a[1]), a, b };
      }
      case "poly": {
        const pts = prim.pts.map(P);
        return pts.some((p) => !p) ? null : { pts };
      }
      case "position": {
        const x0 = X(prim.entry.t), x1 = X(prim.t1);
        const yE = Y(prim.entry.v), yS = Y(prim.stop.v);
        if ([x0, x1, yE, yS].some((q) => q === null)) return null;
        const yT = prim.targets.map((tp) => Y(tp.v));
        if (yT.some((q) => q === null)) return null;
        return { x0: Math.min(x0, x1), x1: Math.max(x0, x1), yE, yS, yT };
      }
      default: return null;
    }
  }

  // ── hit-testing (pixel space) ───────────────────────────
  function hit(prim, px, mx, my, tol, env) {
    if (!px) return false;
    switch (prim.kind) {
      case "point": case "label":
        return Math.hypot(mx - px.p[0], my - px.p[1]) < tol + 3;
      case "hline": return Math.abs(my - px.y) < tol;
      case "vline": return Math.abs(mx - px.x) < tol;
      case "band": return my > px.top - tol && my < px.bot + tol;
      case "vband": return mx > px.left - tol && mx < px.right + tol;
      case "segment": {
        const [p, q] = px.draw;
        return prim.extend === "none"
          ? distToSegment(mx, my, p[0], p[1], q[0], q[1]) < tol
          : distToSegment(mx, my, p[0], p[1], q[0], q[1]) < tol;
      }
      case "box": {
        const nearEdge =
          (Math.abs(mx - px.x) < tol || Math.abs(mx - (px.x + px.w)) < tol)
            && my > px.y - tol && my < px.y + px.h + tol
          || (Math.abs(my - px.y) < tol || Math.abs(my - (px.y + px.h)) < tol)
            && mx > px.x - tol && mx < px.x + px.w + tol;
        const inside = prim.fill && mx > px.x && mx < px.x + px.w
          && my > px.y && my < px.y + px.h;
        return nearEdge || !!inside;
      }
      case "position": {
        if (mx < px.x0 - tol || mx > px.x1 + tol) return false;
        const yFar = px.yT.length ? px.yT[px.yT.length - 1] : px.yE;
        const inReward = my > Math.min(px.yE, yFar) - tol
          && my < Math.max(px.yE, yFar) + tol;
        const inRisk = my > Math.min(px.yE, px.yS) - tol
          && my < Math.max(px.yE, px.yS) + tol;
        return inReward || inRisk;
      }
      case "poly": {
        if (prim.closed && pointInPoly(mx, my, px.pts)) return true;
        for (let i = 1; i < px.pts.length; i++) {
          const [x1, y1] = px.pts[i - 1], [x2, y2] = px.pts[i];
          if (distToSegment(mx, my, x1, y1, x2, y2) < tol) return true;
        }
        if (prim.closed && px.pts.length > 2) {
          const [x1, y1] = px.pts[px.pts.length - 1], [x2, y2] = px.pts[0];
          if (distToSegment(mx, my, x1, y1, x2, y2) < tol) return true;
        }
        return false;
      }
      default: return false;
    }
  }

  // ── painting ────────────────────────────────────────────
  const FONT = 'ui-sans-serif, -apple-system, BlinkMacSystemFont, "Inter", "Segoe UI", Roboto, sans-serif';

  function rgba(hex, a) {
    if (!hex || hex[0] !== "#") return hex;
    const n = parseInt(hex.slice(1), 16);
    return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
  }

  function chip(ctx, text, x, y, col, bg) {
    ctx.font = `11px ${FONT}`;
    const w = ctx.measureText(text).width + 12;
    ctx.fillStyle = bg || Theme.c("chipBg");
    ctx.fillRect(x, y - 15, w, 15);
    ctx.fillStyle = col;
    ctx.fillText(text, x + 6, y - 4);
    return w;
  }

  function paint(ctx, prim, px, s, env) {
    if (!px) return;
    const col = s.color;
    ctx.save();
    ctx.strokeStyle = col;
    ctx.fillStyle = col;
    ctx.lineWidth = s.width || 1.5;
    ctx.setLineDash(s.dash || []);
    switch (prim.kind) {
      case "hline":
        ctx.beginPath(); ctx.moveTo(0, px.y); ctx.lineTo(env.w, px.y); ctx.stroke(); break;
      case "vline":
        ctx.beginPath(); ctx.moveTo(px.x, 0); ctx.lineTo(px.x, env.h); ctx.stroke(); break;
      case "segment": {
        const [p, q] = px.draw;
        ctx.beginPath(); ctx.moveTo(p[0], p[1]); ctx.lineTo(q[0], q[1]); ctx.stroke();
        if (prim.arrow) arrowHead(ctx, p, q, col);
        break;
      }
      case "band":
        ctx.globalAlpha = s.fillAlpha ?? 0.12;
        ctx.fillRect(0, px.top, env.w, Math.max(2, px.bot - px.top));
        ctx.globalAlpha = 1;
        ctx.setLineDash(s.dash || []);
        ctx.beginPath();
        ctx.moveTo(0, px.top); ctx.lineTo(env.w, px.top);
        ctx.moveTo(0, px.bot); ctx.lineTo(env.w, px.bot);
        ctx.stroke();
        break;
      case "vband":
        // the primitive's own alpha wins, like its own colour does — a strip
        // the chat drew to shade a session needs more fill and less edge than
        // one the user dragged out to measure a date range
        ctx.globalAlpha = prim.fillAlpha ?? s.fillAlpha ?? 0.10;
        ctx.fillRect(px.left, 0, Math.max(2, px.right - px.left), env.h);
        ctx.globalAlpha = 1;
        // A REGION is its area, not its boundary. The user's own date-range
        // tool keeps its edges — they are the two handles it is dragged by —
        // but a dozen shaded sessions with hard edges reads as a picket
        // fence, and the fence is louder than the price it is drawn over.
        if (prim.stroke === false) break;
        ctx.beginPath();
        ctx.moveTo(px.left, 0); ctx.lineTo(px.left, env.h);
        ctx.moveTo(px.right, 0); ctx.lineTo(px.right, env.h);
        ctx.stroke();
        break;
      case "box":
        if (prim.fill) {
          ctx.fillStyle = rgba(prim.fill === true ? col : prim.fill, s.fillAlpha ?? 0.12);
          ctx.fillRect(px.x, px.y, px.w, px.h);
          ctx.fillStyle = col;
        }
        ctx.strokeRect(px.x, px.y, px.w, px.h);
        break;
      case "poly": {
        ctx.beginPath();
        px.pts.forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)));
        if (prim.closed) ctx.closePath();
        if (prim.fill) {
          ctx.globalAlpha = s.fillAlpha ?? 0.1; ctx.fill(); ctx.globalAlpha = 1;
        }
        // a fill-only polygon (stroke:false) tints the pattern's interior
        // while its edges are drawn once, by their own primitives
        if (prim.stroke !== false) ctx.stroke();
        break;
      }
      case "point":
        ctx.beginPath(); ctx.arc(px.p[0], px.p[1], s.width === 2 ? 4 : 3, 0, Math.PI * 2);
        ctx.fill();
        break;
      case "label": {
        const align = prim.align || "right";
        ctx.font = `11px ${FONT}`;
        const w = ctx.measureText(prim.text).width + 12;
        const x = align === "left" ? px.p[0] - w - 4 : px.p[0] + 4;
        chip(ctx, prim.text, Math.max(0, Math.min(x, env.w - w)), px.p[1] + 6, col);
        break;
      }
      case "position": {
        const GREEN = "#089981", RED = "#f23645";
        const w = px.x1 - px.x0, cx = (px.x0 + px.x1) / 2;
        const yFar = px.yT.length ? px.yT[px.yT.length - 1] : px.yE;
        ctx.setLineDash([]);
        ctx.fillStyle = rgba(GREEN, 0.16);
        ctx.fillRect(px.x0, Math.min(px.yE, yFar), w, Math.abs(yFar - px.yE));
        ctx.fillStyle = rgba(RED, 0.16);
        ctx.fillRect(px.x0, Math.min(px.yE, px.yS), w, Math.abs(px.yS - px.yE));
        // intermediate target boundaries inside the reward zone
        ctx.strokeStyle = rgba(GREEN, 0.55); ctx.lineWidth = 1;
        for (const y of px.yT.slice(0, -1)) {
          ctx.beginPath(); ctx.moveTo(px.x0, y); ctx.lineTo(px.x1, y); ctx.stroke();
        }
        ctx.setLineDash([4, 4]);
        ctx.strokeStyle = rgba("#e6e8ee", 0.85);
        ctx.beginPath(); ctx.moveTo(px.x0, px.yE); ctx.lineTo(px.x1, px.yE); ctx.stroke();
        ctx.setLineDash([]);
        const pill = (text, y, bg, above) => {
          ctx.font = `11px ${FONT}`;
          const pw = ctx.measureText(text).width + 18, ph = 22;
          const x = Math.max(4, Math.min(cx - pw / 2, env.w - pw - 4));
          const py = above ? y - ph - 4 : y + 4;
          ctx.fillStyle = bg;
          ctx.beginPath(); ctx.roundRect(x, py, pw, ph, 6); ctx.fill();
          ctx.fillStyle = "#fff";
          ctx.fillText(text, x + 9, py + 15);
        };
        // The arithmetic appears when you POINT at the plan, not before.
        // Entry, stop, target, percentage, distance, quantity, risk, R:R and
        // rupee P&L is nine values in four pills sitting over the candles; as
        // a permanent fixture it is a wall of text on a chart whose job is to
        // show the shape of the trade. The shape stays — zones, boundaries,
        // entry line — and the numbers are one hover or one selection away,
        // in the reply, and in the plan card.
        if (!s.detail) break;
        px.yT.forEach((y, i) => {
          const tp = prim.targets[i];
          if (tp.text) pill(tp.text, y, GREEN, y <= px.yE);
        });
        if (prim.stop.text) pill(prim.stop.text, px.yS, RED, px.yS < px.yE);
        const lines = prim.center || [];
        if (lines.length) {
          ctx.font = `11px ${FONT}`;
          const lw = Math.max(...lines.map((t) => ctx.measureText(t).width)) + 24;
          const lh = lines.length * 15 + 12;
          const x = Math.max(4, Math.min(cx - lw / 2, env.w - lw - 4));
          const y = px.yE - lh / 2;
          ctx.fillStyle = rgba("#0d3a32", 0.95);
          ctx.beginPath(); ctx.roundRect(x, y, lw, lh, 8); ctx.fill();
          ctx.strokeStyle = "#fff"; ctx.lineWidth = 1.2;
          ctx.beginPath(); ctx.roundRect(x, y, lw, lh, 8); ctx.stroke();
          ctx.fillStyle = "#fff"; ctx.textAlign = "center";
          lines.forEach((t, i) => ctx.fillText(t, x + lw / 2, y + 17 + i * 15));
          ctx.textAlign = "left";
        }
        break;
      }
    }
    ctx.restore();
  }

  function arrowHead(ctx, from, to, col) {
    const ang = Math.atan2(to[1] - from[1], to[0] - from[0]);
    const L = 9;
    ctx.save(); ctx.fillStyle = col; ctx.setLineDash([]);
    ctx.beginPath();
    ctx.moveTo(to[0], to[1]);
    ctx.lineTo(to[0] - L * Math.cos(ang - Math.PI / 7), to[1] - L * Math.sin(ang - Math.PI / 7));
    ctx.lineTo(to[0] - L * Math.cos(ang + Math.PI / 7), to[1] - L * Math.sin(ang + Math.PI / 7));
    ctx.closePath(); ctx.fill(); ctx.restore();
  }

  return {
    point, hline, vline, segment, band, vband, box, poly, label, position,
    project, hit, paint, chip, rgba, FONT,
    distToSegment, distToLine, pointInPoly, clipToRect,
    linearFit, valueAt, ladder, riskReward,
  };
})();
