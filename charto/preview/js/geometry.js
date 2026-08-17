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

  /* ── curves ───────────────────────────────────────────────────────────────
   * Circles, arcs and spirals are the one family the primitive set could not
   * express, and the temptation is to add a `circle` primitive with a pixel
   * radius. That would be the first drawing on this chart whose SHAPE lived
   * in pixel space — it would stop being anchored to the bars the moment you
   * rescaled the price axis, and it would need its own painter, its own
   * hit-tester and its own drag.
   *
   * So a curve is SAMPLED into data-space points and handed to `poly`, which
   * already renders, hits, drags and persists. A circle on this chart is
   * therefore an ellipse in pixels — semi-axes of Δtime and Δvalue — and that
   * is not an approximation of the right answer, it IS the right answer: the
   * two axes carry different quantities, so a shape that stayed circular
   * under a rescale would be claiming a relationship between rupees and
   * minutes that nothing on the chart supports. TradingView's arcs behave the
   * same way for the same reason.
   *
   * The polylines are returned OPEN with the first point repeated at the end
   * where they close. A closed poly's hit test includes its interior, and six
   * nested fib circles would make the whole disc a grab target — the stroke
   * is the drawing, so the stroke is what answers the pointer.
   */

  /** Points along an ellipse arc about `c`, semi-axes (rt, rv) in DATA units,
   *  from angle a0 to a1. Negative semi-axes mirror, which is what lets a
   *  tool pass a raw anchor delta without branching on its direction. */
  function arcPts(c, rt, rv, a0, a1, n = 48) {
    const out = [];
    for (let i = 0; i <= n; i++) {
      const th = a0 + (a1 - a0) * (i / n);
      out.push({ t: c.t + rt * Math.cos(th), v: c.v + rv * Math.sin(th) });
    }
    return out;
  }

  /** The arc a speed-resistance tool actually wants: centred on `c`, and
   *  CROSSING the vector (rt, rv) at exactly `r` of its length.
   *
   *  A naive ellipse with semi-axes (rt·r, rv·r) does not do that — it passes
   *  through (rt·r, 0) and (0, rv·r), which are the axes, not the trend line.
   *  The whole reading of a fib arc is "price met the 61.8% of this move", so
   *  the arc has to meet the move. Scaling by √2 and sweeping about the
   *  anchor's own quadrant angle puts the crossing exactly on it.
   *
   *  `half` is the half-sweep: π/2 draws the half-ellipse an arc tool shows,
   *  π draws the full ring a circle tool shows.
   *
   *  The sweep is centred on the TIME direction rather than on the crossing
   *  itself. Centred on the crossing, a half-arc reached a quarter turn back
   *  BEHIND the pivot — an arc claiming levels in the past of the swing that
   *  produced it. Centred on time it opens the way the move went, and the
   *  crossing (45° off, by the arithmetic above) is still inside it. */
  function crossArcPts(c, rt, rv, r, half = Math.PI / 2, n = 48) {
    const mid = rt < 0 ? Math.PI : 0;
    return arcPts(c, Math.abs(rt) * Math.SQRT2 * r,
                  Math.abs(rv) * Math.SQRT2 * r, mid - half, mid + half, n);
  }

  /** A golden spiral winding INWARD from `c + (rt, rv)` toward `c`.
   *
   *  Outward is the textbook direction and the wrong one for a chart: the
   *  radius multiplies by φ every quarter turn, so three turns outward is
   *  322× the anchor and the drawing is a straight line off the top of the
   *  screen. Wound inward, the anchor you dragged to IS the spiral's outer
   *  edge and the whole figure lands inside the box you drew.
   *
   *  The start angle is the anchor's QUADRANT — a multiple of 45° — which is
   *  not a rounding error but the construction: a Fibonacci spiral is built
   *  from quarter-turn squares, so its arms begin on the diagonals. The √2
   *  makes the first point land exactly on the anchor. */
  function spiralPts(c, rt, rv, turns = 3, n = 288) {
    const PHI = 1.618033988749895;
    const at = Math.abs(rt) || 1, av = Math.abs(rv) || 1;
    const th0 = Math.atan2(Math.sign(rv) || 1, Math.sign(rt) || 1);
    const total = turns * 2 * Math.PI;
    const out = [];
    for (let i = 0; i <= n; i++) {
      const th = total * (i / n);
      const g = Math.SQRT2 * Math.pow(PHI, (-2 * th) / Math.PI);
      out.push({ t: c.t + at * g * Math.cos(th0 + th),
                 v: c.v + av * g * Math.sin(th0 + th) });
    }
    return out;
  }

  /** A curve about apex `o` that meets ray o→p and ray o→q at exactly `r` of
   *  each — the rung of a fib wedge.
   *
   *  A straight chord between the two points would be the cheap answer and a
   *  wrong one: a wedge's rungs bow, because they are arcs of the same swing.
   *  Angle and radius are interpolated in a plane normalised by each leg's
   *  own extent, so the curve bows without either axis's units leaking into
   *  the other's, and it lands on both rays exactly. */
  function blendArcPts(o, p, q, r, n = 40) {
    const nt = Math.max(Math.abs(p.t - o.t), Math.abs(q.t - o.t)) || 1;
    const nv = Math.max(Math.abs(p.v - o.v), Math.abs(q.v - o.v)) || 1;
    const nrm = (z) => ({ x: (z.t - o.t) / nt, y: (z.v - o.v) / nv });
    const A = nrm(p), B = nrm(q);
    const a0 = Math.atan2(A.y, A.x);
    let a1 = Math.atan2(B.y, B.x);
    // the short way round — a wedge is the space BETWEEN its rays, and the
    // long way round draws the reflex angle nobody asked for
    while (a1 - a0 > Math.PI) a1 -= 2 * Math.PI;
    while (a0 - a1 > Math.PI) a1 += 2 * Math.PI;
    const r0 = Math.hypot(A.x, A.y), r1 = Math.hypot(B.x, B.y);
    const out = [];
    for (let i = 0; i <= n; i++) {
      const s = i / n;
      const th = a0 + (a1 - a0) * s, rad = (r0 + (r1 - r0) * s) * r;
      out.push({ t: o.t + rad * Math.cos(th) * nt,
                 v: o.v + rad * Math.sin(th) * nv });
    }
    return out;
  }

  /** Risk:reward for a position tool. Sign-aware so shorts read correctly. */
  function riskReward(entry, target, stop) {
    const reward = Math.abs(target - entry), risk = Math.abs(entry - stop);
    return risk ? reward / risk : null;
  }

  /** Which half of a plan the market is currently in: "up" for the reward
   *  side, "down" for the risk side, null when there is no price to read.
   *
   *  Sign-aware, because for a SHORT the reward half is the one BELOW the
   *  entry — a plan that went green because the price rose would be telling
   *  the reader the opposite of what happened. Null is a real answer and not
   *  a failure: with nothing to compare against, the chip paints neutral
   *  rather than picking a side it cannot back up. */
  function positionTone(entry, price, side) {
    if (price == null || entry == null) return null;
    // lower-cased, not compared as-is: the user's own tool says "short" and
    // a plan off the wire may say "Short", and getting that wrong paints a
    // losing position green — the one mistake this chip must not make
    const short = String(side).toLowerCase() === "short";
    return (short ? price <= entry : price >= entry) ? "up" : "down";
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
      case "point":
        return Math.hypot(mx - px.p[0], my - px.p[1]) < tol + 3;
      /* A LABEL is grabbable by the words, not by the dot.
       *
       * It used to share the point's test — a 10px circle on the ANCHOR —
       * while the chip is painted beside that anchor, up to a couple of
       * hundred pixels of it. So the thing on screen answered the pointer
       * nowhere along its length: a text note could not be selected, could
       * not be dragged, and could not be deleted, because Delete needs a
       * selection and there was no way to make one. The anchor stays
       * grabbable too — handleAt covers it — this adds what you can see. */
      case "label": {
        const b = chipBox(px.p, prim.text, prim.align || "right",
                          (env && env.w) || Infinity);
        return mx > b.x - tol && mx < b.x + b.w + tol
            && my > b.top - tol && my < b.bot + tol;
      }
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

  /* How wide a chip is, WITHOUT a canvas to hand.
   *
   * The hit test runs nowhere near a paint, and a label's grabbable box has
   * to be the box that was drawn — a second guess at the width (characters
   * times an average, say) is a shape you can see but not always catch. So
   * both sides call this, off one offscreen context, and cannot drift. */
  let _meas = null;
  function chipWidth(text) {
    _meas = _meas || document.createElement("canvas").getContext("2d");
    _meas.font = `11px ${FONT}`;
    return _meas.measureText(String(text == null ? "" : text)).width + 12;
  }
  /** The chip's box for a label anchored at `p`, in pane pixels — the one
   *  definition of where those 15 pixels land, read by `hit` and by `paint`.
   *  Mirrors the clamp in the label painter: a chip never leaves the pane. */
  function chipBox(p, text, align, envW) {
    const w = chipWidth(text);
    const x = (align === "left") ? p[0] - w - 4 : p[0] + 4;
    return { x: Math.max(0, Math.min(x, envW - w)), w,
             top: p[1] - 9, bot: p[1] + 6 };
  }

  function chip(ctx, text, x, y, col, bg) {
    ctx.font = `11px ${FONT}`;
    const w = chipWidth(text);
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
        // chipBox owns the placement; hit() reads the same box, so what is
        // painted and what is grabbable are the same rectangle by construction
        const b = chipBox(px.p, prim.text, prim.align || "right", env.w);
        chip(ctx, prim.text, b.x, b.bot, col);
        break;
      }
      /* The position plan, TradingView's way round: the SHAPE is always on,
       * the NUMBERS are only there when you are looking at it.
       *
       * A plan is two coloured zones and an entry line — that is the whole
       * reading at a glance, and it is what the chart is for. The three
       * labels (target, stop, the centre chip) are the second reading, and
       * left on they cover the candles the plan was drawn against: three
       * opaque plates stacked down the middle of the very bars you are
       * trying to judge it by. So they come up on `s.detail`, which the
       * owner sets when the pointer is over the shape, when it is selected,
       * and while it is being drawn — see js/drawings.js. */
      case "position": {
        const GREEN = "#089981", RED = "#f23645";
        const detail = !!s.detail;
        const w = px.x1 - px.x0, cx = (px.x0 + px.x1) / 2;
        const yFar = px.yT.length ? px.yT[px.yT.length - 1] : px.yE;
        ctx.setLineDash([]);
        /* The zones do NOT answer the pointer. Hover used to lift both fills
         * from .14 to .20, and a fifth more green over a pale chart reads as
         * a colour CHANGE rather than as emphasis — the reward box turned
         * visibly blue under the pointer. Hovering adds the labels; it does
         * not restate the plan in a second palette. */
        ctx.fillStyle = rgba(GREEN, 0.14);
        ctx.fillRect(px.x0, Math.min(px.yE, yFar), w, Math.abs(yFar - px.yE));
        ctx.fillStyle = rgba(RED, 0.14);
        ctx.fillRect(px.x0, Math.min(px.yE, px.yS), w, Math.abs(px.yS - px.yE));
        // Every target line, and the stop — the outer two are the edges of
        // the plan, so they are drawn even with the labels away. Without
        // them a zone fades into the chart background and the level it is
        // claiming has to be read off the axis.
        ctx.lineWidth = 1;
        ctx.strokeStyle = rgba(GREEN, 0.5);
        for (const y of px.yT) {
          ctx.beginPath(); ctx.moveTo(px.x0, y); ctx.lineTo(px.x1, y); ctx.stroke();
        }
        ctx.strokeStyle = rgba(RED, 0.5);
        ctx.beginPath(); ctx.moveTo(px.x0, px.yS); ctx.lineTo(px.x1, px.yS); ctx.stroke();
        // The entry, dashed. TradingView's neutral grey, not a near-white:
        // #e6e8ee is a dark-theme colour, and on a light chart it was a
        // dashed line the same value as the paper under it.
        ctx.setLineDash([4, 4]);
        ctx.strokeStyle = rgba("#787b86", 0.95);
        ctx.beginPath(); ctx.moveTo(px.x0, px.yE); ctx.lineTo(px.x1, px.yE); ctx.stroke();
        ctx.setLineDash([]);
        if (!detail) break;

        /* Tighter than they were — 8px of side padding on a 20px pill, 4px
         * radius — because three of these sit within 40px of each other and
         * the old 18/22/6 read as buttons. Text is white on both fills at
         * this size in either theme. */
        const pill = (text, y, bg, above) => {
          ctx.font = `11px ${FONT}`;
          const pw = Math.round(ctx.measureText(text).width) + 16, ph = 20;
          const x = Math.max(4, Math.min(cx - pw / 2, env.w - pw - 4));
          const py = above ? y - ph - 3 : y + 3;
          ctx.fillStyle = bg;
          ctx.beginPath(); ctx.roundRect(x, py, pw, ph, 4); ctx.fill();
          ctx.fillStyle = "#ffffff";
          ctx.fillText(text, x + 8, py + 14);
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

        /* The centre chip, on the entry line — flat, no border. The white
         * 1.2px ring it used to wear was the loudest mark on the chart, and
         * a chip that outshouts the candles is the opposite of what a plan
         * overlay is for.
         *
         * It takes the colour of the zone the market is CURRENTLY in, which
         * is TradingView's rule: green while the plan is in its reward half,
         * red once price has crossed into the risk half. That makes the chip
         * the one part of a static drawing that still says something as the
         * bars move — you can see which side of your own entry you are on
         * without reading the axis. `tone` is null when there is no price to
         * judge by (an empty chart, a plan the chat drew with no bars behind
         * it), and then the chip is slate: neutral is the honest answer, not
         * a coin-flip between two colours that both mean something. */
        const lines = (prim.center || []).filter(Boolean);
        if (lines.length) {
          ctx.font = `11px ${FONT}`;
          const lw = Math.max(...lines.map((t) => ctx.measureText(t).width)) + 22;
          const lh = lines.length * 15 + 11;
          const x = Math.max(4, Math.min(cx - lw / 2, env.w - lw - 4));
          const y = px.yE - lh / 2;
          // Solid, not the zones' 14% wash: this is a plate carrying white
          // text, and a tint that pale would leave the candles legible
          // through the letters.
          ctx.fillStyle = prim.tone === "up" ? GREEN
            : prim.tone === "down" ? RED : rgba("#131722", 0.92);
          ctx.beginPath(); ctx.roundRect(x, y, lw, lh, 5); ctx.fill();
          ctx.fillStyle = "#ffffff"; ctx.textAlign = "center";
          lines.forEach((t, i) => ctx.fillText(t, x + lw / 2, y + 16 + i * 15));
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
    linearFit, valueAt, ladder, riskReward, positionTone,
    arcPts, crossArcPts, spiralPts, blendArcPts,
  };
})();
